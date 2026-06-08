"""PowerApps MVP Agent。

运行：
    uv run python .\PowerfulApps\Agents\powerapps_mvp_agent.py

项目命令：
    /project ls
    /project new
    /project use 项目名
    /project current
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

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PowerfulApps.Agents.LLM import DeepSeekClient
from PowerfulApps.Agents.doc_extract import extract_project_memory_blocks, remove_project_memory_blocks
from PowerfulApps.Agents.env import load_env_file
from PowerfulApps.Agents.memory import ProjectMemoryStore, ProjectRecord
from PowerfulApps.Agents.project_commands import ProjectCommand, parse_project_command
from PowerfulApps.Agents.prompts import SYSTEM_PROMPT
from PowerfulApps.Agents.runtime import create_browser_session, prepare_studio
from PowerfulApps.Agents.short_memory import ShortTermMemory
from PowerfulApps.Agents.tools import PowerAppsToolRegistry

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
        llm: DeepSeekClient,
        max_steps: int = 12,
    ) -> None:
        self.project = project
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.llm = llm
        self.max_steps = max_steps
        self.short_memory = ShortTermMemory(max_messages=int(os.getenv("POWERAPPS_SHORT_MEMORY_MAX_MESSAGES", "24")))

    async def run(self, user_request: str) -> str:
        self.short_memory.add_user(user_request)

        for step in range(1, self.max_steps + 1):
            log.info("Agent step %d/%d", step, self.max_steps)
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
                return remove_project_memory_blocks(final_text)

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                log.info("Tool call: %s(%s)", call.function.name, args)
                content = await self.tool_registry.run_tool(call.function.name, args)
                self.short_memory.add_tool(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
                        "content": content,
                    }
                )

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
        llm=DeepSeekClient(),
        max_steps=args.max_steps,
    )

    if args.request:
        print(await agent.run(args.request))
    else:
        print("PowerApps MVP Agent 已启动。输入 exit 退出。输入 /project 可管理项目；切换项目会重启浏览器会话。")
        while True:
            request = await asyncio.to_thread(input, "需求> ")
            request = request.strip()
            if request.lower() in {"exit", "quit", "q"}:
                break
            if not request:
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
                        llm=DeepSeekClient(),
                        max_steps=args.max_steps,
                    )
                continue
            print(await agent.run(request))

    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
