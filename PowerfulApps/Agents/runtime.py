"""浏览器运行时适配层。

这一层集中隔离 Agent 对 mocProcessing 的直接依赖。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from mocProcessing import BrowserSession
from mocProcessing.tools.powerapps_chain import _ensure_studio_context, reset_studio_cache

log = logging.getLogger("powerapps_runtime")


async def create_browser_session(url: str, user_data_dir: Path) -> BrowserSession:
    session = BrowserSession(
        headless=False,
        user_data_dir=str(user_data_dir),
        enable_default_extensions=False,
        keep_alive=True,
    )
    await session.start()
    await session.new_page(url)
    return session


async def ensure_cdp_stable(session: Any) -> bool:
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


async def switch_to_powerapps_tab(session: Any) -> None:
    try:
        tabs = await session.get_tabs()
        for tab in tabs:
            url = (tab.url or "").lower()
            if "make.powerapps" in url or "authoring" in url or "powerapps" in url:
                current = await session.get_or_create_cdp_session()
                if current.target_id != tab.target_id:
                    from mocProcessing.browser.events import SwitchTabEvent

                    event = session.event_bus.dispatch(SwitchTabEvent(target_id=tab.target_id))
                    await event
                    await event.event_result(raise_if_any=False, raise_if_none=False)
                    await asyncio.sleep(1.0)
                return
    except Exception as e:
        log.warning("Tab detection failed: %s", e)


async def prepare_studio(session: Any) -> bool:
    if not await ensure_cdp_stable(session):
        return False
    reset_studio_cache()
    await switch_to_powerapps_tab(session)

    for attempt in range(1, 8):
        if attempt > 1:
            await asyncio.sleep(2.0)
            reset_studio_cache()
        try:
            ctx = await _ensure_studio_context(session)
            if ctx and not ctx.get("error"):
                log.info("EmbeddedStudio frameId=%s", ctx.get("frameId", "")[:16])
                return True
        except Exception as e:
            log.warning("Studio connect attempt %d: %s", attempt, e)
    return False
