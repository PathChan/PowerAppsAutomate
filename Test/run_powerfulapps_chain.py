"""直接 DOM Chain 测试入口：插入控件 → 选择属性 → 写入公式。

不使用 ExperienceDB，不跑经验学习。

运行：
    uv run python .\Test\run_powerfulapps_chain.py

可选参数：
    uv run python .\Test\run_powerfulapps_chain.py --component 按钮 --property Text --formula '"你好世界"'
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mocProcessing import BrowserSession
from mocProcessing.tools.powerapps_chain import _ensure_studio_context, reset_studio_cache
from PowerfulApps.MocProcess.chains.insert_and_set_formula import insert_component_and_set_formula


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("powerfulapps_chain")


async def _ensure_cdp_stable(session: BrowserSession) -> bool:
    for attempt in range(1, 11):
        try:
            cdp_s = await session.get_or_create_cdp_session()
            await cdp_s.cdp_client.send.Runtime.evaluate(
                params={"expression": "document.readyState", "returnByValue": True},
                session_id=cdp_s.session_id,
            )
            log.info("CDP stable (attempt %d/10)", attempt)
            return True
        except Exception as e:
            log.info("Waiting for CDP (%d/10): %s", attempt, e)
            await asyncio.sleep(1.5)
    return False


async def _switch_to_powerapps_tab(session: BrowserSession) -> None:
    try:
        tabs = await session.get_tabs()
        log.info("Available tabs: %d", len(tabs))
        for t in tabs:
            url = (t.url or "").lower()
            if "make.powerapps" in url or "authoring" in url or "powerapps" in url:
                cdp_s = await session.get_or_create_cdp_session()
                if cdp_s.target_id != t.target_id:
                    from mocProcessing.browser.events import SwitchTabEvent
                    ev = session.event_bus.dispatch(SwitchTabEvent(target_id=t.target_id))
                    await ev
                    await ev.event_result(raise_if_any=False, raise_if_none=False)
                    await asyncio.sleep(1.0)
                    log.info("Switched to PowerApps tab ...%s", t.target_id[-6:])
                return
    except Exception as e:
        log.warning("Tab detection failed: %s", e)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", default="按钮", help="要插入的控件名称，如 按钮、文本输入、组合框")
    parser.add_argument("--property", dest="property_name", default="Text", help="要设置的属性名，如 Text、X、Y、Fill")
    parser.add_argument("--formula", default='"你好世界"', help='要写入的 Power Fx 公式，例如 "你好世界"')
    args = parser.parse_args()

    power_apps_url = os.getenv("POWER_APPS_URL", "").strip()
    if not power_apps_url:
        log.error("POWER_APPS_URL not set in .env")
        return

    user_data_dir = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", ".chrome-profile-test"))
    if not user_data_dir.is_absolute():
        user_data_dir = (_PROJECT_ROOT / user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    log.info("Launching browser profile=%s", user_data_dir)
    session = BrowserSession(
        headless=False,
        user_data_dir=str(user_data_dir),
        enable_default_extensions=False,
        keep_alive=True,
    )
    await session.start()
    await session.new_page(power_apps_url)

    log.info("=" * 60)
    log.info("1. Sign in if needed")
    log.info("2. Open any app to Studio editor")
    log.info("3. Press Enter to run direct DOM chain")
    log.info("   component=%s property=%s formula=%s", args.component, args.property_name, args.formula)
    log.info("=" * 60)
    await asyncio.to_thread(input, "Ready? Press Enter to run chain...")

    log.info("Waiting for CDP stability...")
    if not await _ensure_cdp_stable(session):
        log.error("CDP not stable, aborting.")
        await session.stop()
        return
    reset_studio_cache()
    await _switch_to_powerapps_tab(session)

    ctx = None
    for attempt in range(1, 8):
        if attempt > 1:
            await asyncio.sleep(2.0)
            reset_studio_cache()
        try:
            ctx = await _ensure_studio_context(session)
            if ctx and not ctx.get("error"):
                break
        except Exception as e:
            log.warning("Studio connect attempt %d: %s", attempt, e)

    if not ctx or ctx.get("error"):
        log.error("Cannot connect to Studio iframe: %s", ctx)
        await session.stop()
        return
    log.info("EmbeddedStudio frameId=%s", ctx.get("frameId")[:16])

    log.info("Running direct DOM chain...")
    result = await insert_component_and_set_formula(
        session,
        component=args.component,
        property_name=args.property_name,
        formula=args.formula,
    )

    if result.get("success"):
        log.info("✅ Chain success")
        log.info("Report: %s", result.get("reportPath"))
    else:
        log.error("❌ Chain failed: %s", result)

    log.info("Browser stays open 30s for inspection.")
    await asyncio.sleep(30)
    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
