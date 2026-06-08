"""click_by_cached_target：根据已知 target key 从缓存读坐标并 CDP 点击。

适用场景
--------
Test 阶段或 locate_and_cache_targets.py 预填充缓存之后，Agent / 调用方
可以用一个 key 直接命中按钮，跳过 DOM 查找：

    click_by_cached_target("ribbon::插入")
    click_by_cached_target("component::按钮")
    click_by_cached_target("property::Text")
    click_by_cached_target("formula_bar::view_lines")

如果 key 不在缓存里、视口变化或点击失败，会返回 error。
此 action 故意"不"做 DOM 回退 —— 那是各个 click_powerapps_* action 的职责。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from mocProcessing.tools.target_cache import (
    _get_iframe_offset_and_viewport,
    get_cached,
    invalidate,
)

logger = logging.getLogger(__name__)


class ClickByCachedTargetParams(BaseModel):
    """参数：缓存里的 target key。"""

    target_key: str = Field(
        ...,
        description=(
            '缓存条目的 key，例如 "ribbon::插入"、"component::按钮"、'
            '"property::Text"、"formula_bar::view_lines"。'
            '完整列表见 .cache/powerapps/dom_targets.json。'
        ),
    )
    invalidate_on_viewport_mismatch: bool = Field(
        default=True,
        description='当当前视口尺寸与缓存不一致时，是否作废缓存并报错。',
    )


def _viewport_close(entry: dict, vw: float, vh: float, tol: float = 0.02) -> bool:
    evw = entry.get("viewport_width") or 0
    evh = entry.get("viewport_height") or 0
    if not evw or not evh:
        return False
    return (
        abs(evw - vw) / max(evw, 1) <= tol
        and abs(evh - vh) / max(evh, 1) <= tol
    )


async def click_by_cached_target(
    params: ClickByCachedTargetParams,
    browser_session: BrowserSession,
) -> ActionResult:
    """按 key 从缓存取坐标 → CDP Input.dispatchMouseEvent 点击。"""
    key = params.target_key
    entry = get_cached(key)
    if not entry:
        return ActionResult(
            error=(
                f'Cached target {key!r} not found. '
                f'Run the corresponding click_powerapps_* action once (or the '
                f'locate_and_cache_targets.py script) to populate the cache.'
            )
        )

    geo = await _get_iframe_offset_and_viewport(browser_session)
    vw = geo.get("vw") or 0
    vh = geo.get("vh") or 0

    if not _viewport_close(entry, vw, vh):
        if params.invalidate_on_viewport_mismatch:
            invalidate(key)
        return ActionResult(
            error=(
                f'Viewport mismatch for {key!r}: cache='
                f'{entry.get("viewport_width")}x{entry.get("viewport_height")} '
                f'vs current={vw}x{vh}. Cache invalidated; re-run the '
                f'matching click_powerapps_* action to re-locate.'
            )
        )

    x = float(entry["x"])
    y = float(entry["y"])
    cdp_session = await browser_session.get_or_create_cdp_session()
    await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
        params={"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        session_id=cdp_session.session_id,
    )
    await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
        params={"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        session_id=cdp_session.session_id,
    )

    msg = (
        f'Clicked cached target {key!r} at ({x:.1f}, {y:.1f}) '
        f'[kind={entry.get("kind")}, label={entry.get("label")}]'
    )
    return ActionResult(extracted_content=msg, long_term_memory=msg)


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        (
            'Click a PowerApps Studio UI element by its cached target key '
            '(e.g. "ribbon::插入", "component::按钮", "property::Text", '
            '"formula_bar::view_lines"). Faster than DOM lookup but requires the '
            'cache to be populated and the viewport to match. Returns error if '
            'the cache is missing or the viewport changed.'
        ),
        param_model=ClickByCachedTargetParams,
    )(click_by_cached_target)
