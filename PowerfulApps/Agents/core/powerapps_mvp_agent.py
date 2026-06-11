"""PowerApps MVP Agent。

运行：
    uv run python .\PowerfulApps\Agents\core\powerapps_mvp_agent.py

项目命令：
    /project ls             列出所有项目
    /project new            新建项目
    /project use <项目名>    使用指定项目
    /project current        查看当前项目
    /model                  管理模型
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PowerfulApps.Agents.LLM import OpenAICompatibleClient, create_llm_client, provider_names
from PowerfulApps.Agents.core.doc_extract import extract_project_memory_blocks, remove_project_memory_blocks
from PowerfulApps.Agents.config.env import load_env_file
from PowerfulApps.Agents.memory.memory import ProjectMemoryStore, ProjectRecord
from PowerfulApps.Agents.core.project_commands import ProjectCommand, parse_project_command
from PowerfulApps.Agents.core.prompts import SYSTEM_PROMPT
from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio
from PowerfulApps.Agents.memory.short_memory import ShortTermMemory
from PowerfulApps.Agents.core.tools import PowerAppsToolRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("powerapps_agent")


class PowerAppsMvpAgent:
    def __init__(
        self,
        *,
        project: ProjectRecord,
        memory_store: ProjectMemoryStore,
        tool_registry: PowerAppsToolRegistry,
        llm: OpenAICompatibleClient,
        max_steps: int = 12,
    ) -> None:
        self.project = project
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.llm = llm
        self.max_steps = max_steps
        self.short_memory = ShortTermMemory(
            max_messages=int(os.getenv("POWERAPPS_SHORT_MEMORY_MAX_MESSAGES", "24")),
            max_summaries=int(os.getenv("POWERAPPS_SHORT_MEMORY_MAX_SUMMARIES", "12")),
        )

    async def run(self, user_request: str) -> str:
        self.short_memory.add_user(user_request)
        await self.short_memory.compact_if_needed(self.llm, preserve_last=1)

        for step in range(1, self.max_steps + 1):
            log.info("Agent 步骤 %d/%d", step, self.max_steps)
            project_doc = self.memory_store.read_doc(self.project)
            messages = self.short_memory.build_messages(SYSTEM_PROMPT, project_doc)
            response = await self.llm.chat(
                messages=messages,
                tools=self.tool_registry.openai_tools(),
                tool_choice="auto",
            )
            msg = response.choices[0].message
            assistant_message = msg.model_dump(exclude_none=True)
            self.short_memory.add_assistant(assistant_message)

            if not msg.tool_calls:
                final_text = msg.content or "完成。"
                self._persist_project_memory(final_text)
                await self.short_memory.compact_if_needed(self.llm)
                return remove_project_memory_blocks(final_text)

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                log.info("工具调用：%s(%s)", call.function.name, args)
                content = await self.tool_registry.run_tool(call.function.name, args)
                self.short_memory.add_tool(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
                        "content": content,
                    }
                )

            await self.short_memory.compact_if_needed(self.llm, preserve_last=len(msg.tool_calls) + 1)

        return "达到最大步骤数，已停止。你可以继续输入需求让我接着执行。"

    def _persist_project_memory(self, final_text: str) -> None:
        blocks = extract_project_memory_blocks(final_text)
        for block in blocks:
            self.memory_store.append_doc(self.project, block)


def _memory_root() -> Path:
    value = os.getenv("POWERAPPS_PROJECT_MEMORY_DIR", "PowerfulApps/Agents/.memory")
    path = Path(value)
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path


def _print_project_help() -> None:
    print("/project ls                 列出所有项目")
    print("/project new                新建项目记忆，需要输入自定义名称和 URL")
    print("/project use 项目名          使用指定项目记忆")
    print("/project current            查看当前项目")


def _model_config_path() -> Path:
    value = os.getenv("POWERAPPS_MODEL_CONFIG_FILE", "")
    if value:
        path = Path(value)
        return path if path.is_absolute() else (_PROJECT_ROOT / path).resolve()
    return _memory_root() / "model_config.json"


def _model_env_names(provider: str) -> tuple[str, str, str, str]:
    name = provider.strip().lower()
    if name in {"volcengine", "ark", "doubao"}:
        return "ARK_API_KEY", "ARK_BASE_URL", "ARK_MODEL", "ARK_THINKING_ENABLED"
    prefix = "OPENAI" if name == "openai" else "DEEPSEEK"
    return f"{prefix}_API_KEY", f"{prefix}_BASE_URL", f"{prefix}_MODEL", f"{prefix}_THINKING_ENABLED"


def _mask_key(value: str) -> str:
    if not value:
        return "未设置"
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _provider_from_env() -> str:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    return "volcengine" if provider in {"ark", "doubao"} else provider


def _load_model_config() -> None:
    path = _model_config_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("模型配置读取失败：%s", e)
        return

    provider = str(data.get("provider") or "").strip().lower()
    if provider:
        os.environ["LLM_PROVIDER"] = "volcengine" if provider in {"ark", "doubao"} else provider

    configs = data.get("providers", {})
    if not isinstance(configs, dict):
        return
    for name, config in configs.items():
        if not isinstance(config, dict):
            continue
        key_env, base_url_env, model_env, thinking_env = _model_env_names(str(name))
        mapping = {
            key_env: config.get("api_key"),
            base_url_env: config.get("base_url"),
            model_env: config.get("model"),
            thinking_env: config.get("thinking"),
        }
        for env_name, value in mapping.items():
            if value is not None:
                os.environ[env_name] = str(value)


def _save_model_config() -> None:
    provider = _provider_from_env()
    providers: dict[str, dict[str, str]] = {}
    for name in provider_names():
        normalized = "volcengine" if name in {"ark", "doubao"} else name
        if normalized in providers:
            continue
        key_env, base_url_env, model_env, thinking_env = _model_env_names(normalized)
        values = {
            "api_key": os.getenv(key_env, ""),
            "base_url": os.getenv(base_url_env, ""),
            "model": os.getenv(model_env, ""),
            "thinking": os.getenv(thinking_env, ""),
        }
        providers[normalized] = {k: v for k, v in values.items() if v}

    path = _model_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"provider": provider, "providers": providers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_model_help() -> None:
    print("/model current              查看当前模型配置")
    print("/model providers            查看可用供应商")
    print("/model provider 名称         切换供应商，如 openai、deepseek、volcengine")
    print("/model set key 值            设置当前供应商 API Key")
    print("/model set model 值          设置当前供应商模型")
    print("/model set base_url 值       设置当前供应商 Base URL")
    print("/model set thinking true|false 设置当前供应商 thinking")
    print(f"配置文件：{_model_config_path()}")


def _print_current_model() -> None:
    client = create_llm_client()
    print(f"当前供应商：{client.provider}")
    print(f"当前模型：{client.model}")
    print(f"Base URL：{client.base_url}")
    print(f"API Key：{_mask_key(client.api_key)}")
    print(f"Thinking：{client.thinking_enabled}")
    print(f"配置文件：{_model_config_path()}")


def _handle_model_command(raw: str) -> bool:
    parts = raw.strip().split()
    if len(parts) == 1 or parts[1] in {"help", "h"}:
        _print_model_help()
        return False

    action = parts[1].lower()
    if action == "current":
        _print_current_model()
        return False

    if action == "providers":
        print("可用供应商：" + ", ".join(provider_names()))
        return False

    if action == "provider":
        if len(parts) < 3:
            print("用法：/model provider openai|deepseek|volcengine")
            return False
        provider = parts[2].lower()
        if provider not in provider_names():
            print("未知供应商。可用供应商：" + ", ".join(provider_names()))
            return False
        os.environ["LLM_PROVIDER"] = "volcengine" if provider in {"ark", "doubao"} else provider
        _save_model_config()
        print(f"已切换供应商：{os.environ['LLM_PROVIDER']}")
        _print_current_model()
        return True

    if action == "set":
        if len(parts) < 4:
            print("用法：/model set key|model|base_url|thinking 值")
            return False
        field = parts[2].lower()
        value = " ".join(parts[3:]).strip()
        provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
        key_env, base_url_env, model_env, thinking_env = _model_env_names(provider)
        env_by_field = {
            "key": key_env,
            "api_key": key_env,
            "base_url": base_url_env,
            "url": base_url_env,
            "model": model_env,
            "thinking": thinking_env,
        }
        env_name = env_by_field.get(field)
        if not env_name:
            print("可设置字段：key、model、base_url、thinking")
            return False
        os.environ[env_name] = value
        _save_model_config()
        print(f"已设置 {env_name}，并已保存。")
        _print_current_model()
        return True

    print(f"未知命令：/model {action}")
    _print_model_help()
    return False


def _handle_project_command(
    command: ProjectCommand,
    store: ProjectMemoryStore,
    current: ProjectRecord | None,
) -> ProjectRecord | None:
    if command.name in {"help", "h"}:
        _print_project_help()
        return current

    if command.name == "ls":
        projects = store.list_projects()
        if not projects:
            print("还没有项目。使用 /project new 创建。")
            return current
        for item in projects:
            marker = "*" if current and current.name == item.name else " "
            print(f"{marker} {item.name} | {item.title} | {item.url}")
        return current

    if command.name == "new":
        name = input("项目自定义名称> ").strip()
        url = input("项目 URL> ").strip()
        title = input("项目标题，可空> ").strip() or name
        if not name or not url:
            print("项目名称和 URL 不能为空。")
            return current
        project = store.upsert(name=name, url=url, title=title)
        print(f"已创建并使用项目：{project.name}")
        return project

    if command.name == "use":
        if not command.args:
            print("用法：/project use 项目名")
            return current
        name = " ".join(command.args)
        project = store.get(name)
        if not project:
            print(f"未找到项目：{name}")
            return current
        print(f"已切换到项目：{project.name}")
        return project

    if command.name == "current":
        if current:
            print(f"当前项目：{current.name} | {current.title} | {current.url}")
        else:
            print("当前没有选择项目。")
        return current

    print(f"未知命令：/project {command.name}")
    _print_project_help()
    return current


def _select_project(store: ProjectMemoryStore) -> ProjectRecord:
    projects = store.list_projects()
    if projects:
        print("请选择要修改的项目。可用命令：/project ls、/project use 项目名、/project new")
    else:
        print("还没有项目记忆，请使用 /project new 创建。")

    current: ProjectRecord | None = None
    while current is None:
        raw = input("project> ").strip()
        command = parse_project_command(raw)
        if not command:
            print("请先使用 /project 命令选择项目。")
            continue
        current = _handle_project_command(command, store, current)
    return current


async def _start_session_for_project(project: ProjectRecord) -> Any:
    user_data_dir = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", "./browser_profile"))
    if not user_data_dir.is_absolute():
        user_data_dir = (_PROJECT_ROOT / user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    session = await create_browser_session(project.url, user_data_dir)
    log.info("登录并打开 PowerApps Studio 后按 Enter。")
    await asyncio.to_thread(input, "Ready? Press Enter to start agent...")
    if not await prepare_studio(session):
        await session.stop()
        raise RuntimeError("Cannot connect to Studio iframe.")
    return session


async def main() -> None:
    load_env_file(_PROJECT_ROOT / ".env")
    _load_model_config()

    parser = argparse.ArgumentParser()
    parser.add_argument("--request", default="", help="直接执行的一条用户需求；不传则进入交互循环")
    parser.add_argument("--project", default="", help="直接使用指定项目名")
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("POWERAPPS_AGENT_MAX_STEPS", "12")))
    args = parser.parse_args()

    store = ProjectMemoryStore(_memory_root())
    project = store.get(args.project) if args.project else None
    if not project:
        project = _select_project(store)

    session = await _start_session_for_project(project)
    agent = PowerAppsMvpAgent(
        project=project,
        memory_store=store,
        tool_registry=PowerAppsToolRegistry(session),
        llm=create_llm_client(),
        max_steps=args.max_steps,
    )

    if args.request:
        print(await agent.run(args.request))
    else:
        print("PowerApps MVP Agent 已启动。输入 exit 退出。输入 /project 管理项目，输入 /model 管理模型。")
        while True:
            request = await asyncio.to_thread(input, "需求> ")
            request = request.strip()
            if request.lower() in {"exit", "quit", "q"}:
                break
            if not request:
                continue
            if request.startswith("/model"):
                if _handle_model_command(request):
                    agent.llm = create_llm_client()
                continue
            command = parse_project_command(request)
            if command:
                new_project = _handle_project_command(command, store, agent.project)
                if new_project and new_project.name != agent.project.name:
                    await session.stop()
                    session = await _start_session_for_project(new_project)
                    agent = PowerAppsMvpAgent(
                        project=new_project,
                        memory_store=store,
                        tool_registry=PowerAppsToolRegistry(session),
                        llm=create_llm_client(),
                        max_steps=args.max_steps,
                    )
                continue
            print(await agent.run(request))

    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("用户中断。")
