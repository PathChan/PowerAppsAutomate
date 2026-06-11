"""浏览器运行时适配层。

这一层集中隔离 Agent 对 mocProcessing 的直接依赖。
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.Browser.cdp.studio_cdp import _ensure_studio_context, reset_studio_cache

log = logging.getLogger("powerapps_runtime")


def _ensure_playwright_chromium() -> None:
    """确保 Playwright 自带的 Chromium 已安装。"""
    import glob
    playwright_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not playwright_path:
        playwright_path = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    pattern = os.path.join(playwright_path, "chromium-*", "chrome-win*", "chrome.exe")
    if glob.glob(pattern):
        log.info("Playwright Chromium 已安装")
        return
    log.warning("Playwright Chromium 未安装，正在自动安装...")
    result = subprocess.run(
        ["uvx", "playwright", "install", "chromium"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        log.warning("playwright install chromium 输出: %s", result.stderr[:500])
    if glob.glob(pattern):
        log.info("Playwright Chromium 安装成功")
    else:
        log.warning("Playwright Chromium 安装可能未生效，将继续尝试使用系统浏览器")


async def create_browser_session(url: str, user_data_dir: Path) -> BrowserSession:
    _ensure_playwright_chromium()
    session = BrowserSession(
        headless=False,
        user_data_dir=str(user_data_dir),
        enable_default_extensions=False,
        keep_alive=True,
        channel="chromium",
    )
    await session.start()
    # 复用第一个 tab 导航到目标 URL，而不是再开一个新 tab
    pages = await session.get_pages()
    if pages:
        page = pages[0]
        await page.goto(url)
    else:
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
            log.info("CDP 已稳定（第 %d/10 次尝试）", attempt)
            return True
        except Exception as e:
            log.info("等待 CDP 就绪（%d/10）：%s", attempt, e)
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
                    from PowerfulApps.Browser.core.events import SwitchTabEvent

                    event = session.event_bus.dispatch(SwitchTabEvent(target_id=tab.target_id))
                    await event
                    await event.event_result(raise_if_any=False, raise_if_none=False)
                    await asyncio.sleep(1.0)
                return
    except Exception as e:
        log.warning("标签页检测失败：%s", e)


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
                log.info("EmbeddedStudio 框架ID=%s", ctx.get("frameId", "")[:16])
                return True
        except Exception as e:
            log.warning("Studio 连接尝试 %d 失败：%s", attempt, e)
    return False
