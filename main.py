from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any

# ── 经验文件路径 ──────────────────────────────────────────────────
# 所有 sub-agent 共享的经验知识库，持久化保存，跨会话复用。
# 使用 mocProcessing agent 内置的 experience.md，由 prompts.py 自动加载。
AGENT_EXPERIENCE_FILE = (
    Path(__file__).resolve().parent
    / "mocProcessing" / "agent" / "system_prompts" / "experience.md"
)

# ── 使用本地 mocProcessing 库 ─────────────────────────────────────
# 直接将项目根目录加入 sys.path，让 Python 找到 mocProcessing/ 包。
# mocProcessing 是从 browser_use 精简移植而来的版本，已删除多模型/sandbox/skills 等。
_project_root = Path(__file__).resolve().parent
_root_path = str(_project_root)
if _root_path not in sys.path:
    sys.path.insert(0, _root_path)

from mocProcessing import Agent, BrowserSession, ChatOpenAI
from RAG.prompts import EXECUTION_TASK_TEMPLATE


def load_env_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs from .env without requiring extra packages."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def setup_logging() -> None:
    """Enable verbose diagnostics for browser-use, Playwright, and this script."""
    log_level_name = os.getenv("BROWSER_USE_LOG_LEVEL", "DEBUG").upper()
    log_level = getattr(logging, log_level_name, logging.DEBUG)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for logger_name in (
        "mocProcessing",
        "mocProcessing.agent",
        "mocProcessing.browser",
        "playwright",
        "httpx",
        "openai",
    ):
        logging.getLogger(logger_name).setLevel(log_level)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_llm() -> Any:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()

    if provider == "openai":
        api_key = require_env("LLM_API_KEY")
        model = os.getenv("LLM_MODEL")
        base_url = os.getenv("LLM_BASE_URL")

        logging.info("Using OpenAI-compatible model: %s", model)
        if base_url:
            logging.info("Using OpenAI-compatible base URL: %s", base_url)

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            dont_force_structured_output=True,
            add_schema_to_system_prompt=True,
        )

    if provider == "anthropic":
        raise RuntimeError("Anthropic support removed in mocProcessing. Use LLM_PROVIDER=openai.")

    raise RuntimeError("LLM_PROVIDER must be 'openai' (anthropic removed in mocProcessing).")


def supports_parameter(callable_obj: Any, parameter_name: str) -> bool:
    try:
        return parameter_name in inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False


def build_browser_session(user_data_dir: Path) -> BrowserSession:
    """
    Build a persistent visible browser session.

    user_data_dir is the key setting for Power Apps/Microsoft login reuse:
    after you sign in manually once, cookies/session storage are retained here.
    """
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # ── 清理旧 Chrome 进程 ──────────────────────────────────────
    # 上次异常退出后 Chrome 可能还活着，占着用户数据目录的锁，
    # 导致新的 BrowserSession 启动超时（30s 后 Timeout）。
    # 杀掉同一 user_data_dir 下的所有 Chrome 进程。
    import subprocess, glob

    _kill_logged = False
    user_data_str = str(user_data_dir).replace('/', '\\')
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', 'name="chrome.exe"', 'get', 'commandline', 'processid', '/format:csv'],
            capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            if user_data_str.replace('\\', '/').lower() in line.lower().replace('\\', '/') \
               or user_data_str.lower() in line.lower():
                parts = line.split(',')
                if len(parts) >= 3 and parts[2].strip().isdigit():
                    pid = parts[2].strip()
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    if not _kill_logged:
                        logging.warning("Killed stale Chrome process (PID=%s) holding lock on user_data_dir", pid)
                        _kill_logged = True
    except Exception:
        pass  # wmic unavailable → 让 BrowserSession 自己处理超时

    kwargs: dict[str, Any] = {
        "headless": False,
        "user_data_dir": str(user_data_dir),
        "enable_default_extensions": False,
        "keep_alive": True,
    }

    chrome_path = os.getenv("CHROME_PATH")
    if chrome_path:
        # Some browser-use versions call this executable_path, others channel/path.
        if supports_parameter(BrowserSession, "executable_path"):
            kwargs["executable_path"] = chrome_path
        elif supports_parameter(BrowserSession, "browser_binary_path"):
            kwargs["browser_binary_path"] = chrome_path
        else:
            logging.warning(
                "CHROME_PATH was set, but this browser-use BrowserSession version "
                "does not expose a known executable path parameter. Ignoring it."
            )

    logging.info("Using persistent browser profile: %s", user_data_dir)
    logging.info("Running with headless=False for visual inspection.")
    return BrowserSession(**kwargs)


def make_agent(task: str, llm: Any, browser_session: BrowserSession) -> Agent:
    kwargs: dict[str, Any] = {
        "task": task,
        "llm": llm,
        "browser_session": browser_session,
    }

    if supports_parameter(Agent, "use_vision"):
        kwargs["use_vision"] = True
    if supports_parameter(Agent, "save_conversation_path"):
        kwargs["save_conversation_path"] = "mocProcessing_conversation.json"
    if supports_parameter(Agent, "max_actions_per_step"):
        kwargs["max_actions_per_step"] = int(os.getenv("AGENT_MAX_ACTIONS_PER_STEP", "3"))

    # ── 注入 PowerApps 操作铁律到系统 prompt ──────────────────────
    # 这是硬约束，直接追加到 system prompt 末尾，比 experience.md 优先级更高。
    # 这些规则会影响 Agent 在每个步骤中的决策，必须严格遵守。
    POWERAPPS_HARD_RULES = """ """

    if supports_parameter(Agent, "extend_system_message"):
        kwargs["extend_system_message"] = POWERAPPS_HARD_RULES
        logging.info("PowerApps hard rules injected into system prompt.")

    agent = Agent(**kwargs)

    # ── 强制启用坐标点击 ──────────────────────────────────────────
    # browser-use 默认只为 claude-sonnet-4 / gemini-3-pro 等模型启用坐标点击。
    # DeepSeek/其他模型需要通过 tools.set_coordinate_clicking(True) 手动开启。
    if hasattr(agent, "tools") and hasattr(agent.tools, "set_coordinate_clicking"):
        agent.tools.set_coordinate_clicking(True)
        logging.info("Coordinate clicking enabled for all models.")

    return agent


def build_execution_task(power_apps_url: str, goal: str, plan: str) -> str:
    return EXECUTION_TASK_TEMPLATE.format(
        power_apps_url=power_apps_url, goal=goal, plan=plan
    )


def read_console_goal() -> str:
    return input("\nPowerAppsAgent> ").strip()


def extract_final_result(result: Any) -> str:
    if result is None:
        return "Agent returned no result."

    if hasattr(result, "final_result") and callable(result.final_result):
        final = result.final_result()
        if final:
            return str(final)

    if hasattr(result, "extracted_content"):
        content = getattr(result, "extracted_content")
        if callable(content):
            content = content()
        if content:
            return str(content)

    return str(result)


def print_agent_diagnostics(result: Any) -> None:
    """Best-effort diagnostics across mocProcessing result/history versions."""
    if result is None:
        return

    print("\n=== Agent diagnostics ===")

    if hasattr(result, "errors") and callable(result.errors):
        errors = result.errors()
        if errors:
            print("\nErrors:")
            for index, error in enumerate(errors, start=1):
                print(f"{index}. {error}")

    if hasattr(result, "urls") and callable(result.urls):
        urls = result.urls()
        if urls:
            print("\nVisited URLs:")
            for index, url in enumerate(urls, start=1):
                print(f"{index}. {url}")

    if hasattr(result, "action_names") and callable(result.action_names):
        actions = result.action_names()
        if actions:
            print("\nActions:")
            for index, action in enumerate(actions, start=1):
                print(f"{index}. {action}")


def read_agent_experience() -> str:
    """读取持久化的经验知识库."""
    if AGENT_EXPERIENCE_FILE.exists():
        return AGENT_EXPERIENCE_FILE.read_text(encoding="utf-8").strip()
    return ""


async def run_goal(goal: str, llm: Any, browser_session: BrowserSession, power_apps_url: str, max_steps: int) -> None:
    """单一主 Agent：直接执行用户目标，无可行性规划/任务分解/经验 sub-agent。"""
    # 读取经验库作为执行参考（仍然保留，作为系统提示的一部分）
    experience_context = read_agent_experience()

    # 构建任务提示：把 goal 当作 plan 直接传入（让主 Agent 自己规划和执行）
    task_prompt = build_execution_task(
        power_apps_url=power_apps_url, goal=goal, plan=goal
    )
    if experience_context:
        task_prompt += f"\n\n参考经验（经验库当前内容）：\n{experience_context}"

    print(f"\n{'='*60}")
    print(f"  主 Agent 启动")
    print(f"{'='*60}")
    print(f"目标: {goal}")

    agent = make_agent(
        task=task_prompt,
        llm=llm,
        browser_session=browser_session,
    )
    result = await agent.run(max_steps=max_steps)
    result_text = extract_final_result(result)

    print(f"\n{'='*60}")
    print(f"  主 Agent 完成")
    print(f"{'='*60}")
    print(f"结果: {result_text[:500]}")
    print_agent_diagnostics(result)

    # agent.close() 会停止 browser_session 的事件总线。
    # 重新初始化，供下一个 goal 使用。
    from bubus import EventBus
    browser_session.event_bus = EventBus()
    browser_session._watchdogs_attached = False
    await browser_session.start()


async def main() -> int:
    load_env_file()
    setup_logging()

    power_apps_url = require_env("POWER_APPS_URL")
    max_steps = int(os.getenv("AGENT_MAX_STEPS", "100"))
    user_data_dir = Path(
        os.getenv("BROWSER_USE_USER_DATA_DIR", str(Path.cwd() / "browser_profile"))
    ).expanduser().resolve()

    logging.info("Target Power Apps URL: %s", power_apps_url)
    logging.info("Maximum agent steps per goal: %s", max_steps)

    browser_session = build_browser_session(user_data_dir)
    llm = build_llm()

    print("\nPower Apps Agent console")
    print("Type a goal and press Enter. Type 'exit' or 'quit' to leave.")

    try:
        while True:
            goal = await asyncio.to_thread(read_console_goal)
            if not goal:
                continue
            if goal.lower() in {"exit", "quit", "q"}:
                return 0

            try:
                await run_goal(goal, llm, browser_session, power_apps_url, max_steps)
            except Exception:
                logging.exception("Power Apps automation failed for this goal.")
                print(
                    "\nAutomation failed for this goal. The console is still running; "
                    "check the stack trace and the visible browser window, then enter the next goal."
                )

    except KeyboardInterrupt:
        logging.warning("Interrupted by user.")
        return 130
    finally:
        close = getattr(browser_session, "close", None)
        if callable(close):
            maybe_awaitable = close()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ is required.")

    raise SystemExit(asyncio.run(main()))
