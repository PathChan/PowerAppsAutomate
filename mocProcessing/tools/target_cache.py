"""PowerApps 常用 UI 目标的坐标缓存。

目的
----
PowerApps Studio 的 DOM 很重，并且大部分按钮 / 输入框的 selector 几乎不变。
每次操作都重新 querySelector + 派发 PointerEvent 既慢又脆。
本模块把"已知目标的中心坐标"持久化到 JSON：

    .cache/powerapps/dom_targets.json

字段结构::

    {
      "ribbon::插入": {
        "name": "ribbon::插入",
        "kind": "ribbon_button",
        "label": "插入",
        "selector": "...",   // 命中时实际用到的 CSS selector（可选）
        "x": 612.0,          // 主页 viewport 坐标系下的中心点
        "y": 88.0,
        "viewport_width": 1510,
        "viewport_height": 910,
        "device_pixel_ratio": 1.0,
        "updated_at": "2026-06-08T04:37:13Z"
      }
    }

设计要点
--------
- 坐标统一存"主页 viewport 坐标系"。在 Studio iframe 内拿到的 rect
  会自动加上 iframe 在主页里的偏移，方便直接 `Input.dispatchMouseEvent`。
- 视口尺寸 / DPR 也存进去，运行时一致才会信任缓存；否则触发重定位。
- 没有索引文件 / 没有 schema 校验：保持文件足够简单，方便人手编辑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from mocProcessing.browser import BrowserSession
from mocProcessing.tools.powerapps_chain import (
    _ensure_studio_context,
    execute_in_studio,
)

logger = logging.getLogger(__name__)

# ── 缓存路径 ──────────────────────────────────────────────────
# 跟 Test/.cache/powerapps/visual_targets.json 同目录约定。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CACHE_DIR = _PROJECT_ROOT / ".cache" / "powerapps"
_CACHE_FILE = _CACHE_DIR / "dom_targets.json"

# 视口容差：宽/高相差超过该比例视为视口变化，缓存作废。
_VIEWPORT_TOLERANCE = 0.02

# 在 Studio iframe 内执行的 JS 模板，统一返回 {found, rect, ...}
# rect 是 iframe 内坐标，需要叠加 iframe 偏移转主页坐标。
_PROBE_BY_SELECTOR_JS = r"""
((selector) => {
    const el = document.querySelector(selector);
    if (!el) return {found: false};
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return {found: false, reason: 'zero-size'};
    return {
        found: true,
        rect: {x: r.x, y: r.y, w: r.width, h: r.height},
        tag: el.tagName,
        text: (el.textContent || '').trim().slice(0, 80),
    };
})
"""


# ── 文件 IO ──────────────────────────────────────────────────


def _read_cache() -> dict[str, dict[str, Any]]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("dom_targets.json corrupted, starting fresh: %s", e)
        return {}


def _write_cache(data: dict[str, dict[str, Any]]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_cached(key: str) -> dict[str, Any] | None:
    """读取一条缓存（不做有效性检查）。"""
    return _read_cache().get(key)


def save_cached(key: str, entry: dict[str, Any]) -> None:
    """写入或覆盖一条缓存。"""
    data = _read_cache()
    data[key] = entry
    _write_cache(data)


def invalidate(key: str) -> None:
    """删除一条缓存（点击失败后调用）。"""
    data = _read_cache()
    if key in data:
        del data[key]
        _write_cache(data)
        logger.info("Invalidated cached target: %s", key)


def list_all() -> dict[str, dict[str, Any]]:
    """返回当前缓存的全部内容（调试 / 批量预热用）。"""
    return _read_cache()


# ── 坐标定位 ──────────────────────────────────────────────────


async def _get_iframe_offset_and_viewport(
    browser_session: BrowserSession,
) -> dict[str, float]:
    """在主页拿 EmbeddedStudio iframe 的 boundingClientRect + viewport 尺寸。

    返回 {iframe_x, iframe_y, vw, vh, dpr}。找不到 iframe 时偏移视为 (0,0)。
    """
    ctx = await _ensure_studio_context(browser_session)
    frame_id = ctx.get("frameId")
    cdp_session = await browser_session.get_or_create_cdp_session()

    js = r"""
    (() => {
        const iframes = document.querySelectorAll('iframe');
        let target = null;
        for (const f of iframes) {
            // 主页里 EmbeddedStudio iframe 的 name 就叫 EmbeddedStudio
            if (f.name === 'EmbeddedStudio' && f.offsetParent !== null) { target = f; break; }
        }
        const dpr = window.devicePixelRatio || 1;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        if (!target) return {iframe_x: 0, iframe_y: 0, vw, vh, dpr, found: false};
        const r = target.getBoundingClientRect();
        return {iframe_x: r.x, iframe_y: r.y, vw, vh, dpr, found: true};
    })()
    """
    result = await cdp_session.cdp_client.send.Runtime.evaluate(
        params={"expression": js, "returnByValue": True},
        session_id=cdp_session.session_id,
    )
    value = (result.get("result") or {}).get("value") or {}
    # 标注一下当前用的 frame，便于调试
    value["_frame_id"] = frame_id
    return value


async def _probe_in_studio(
    browser_session: BrowserSession,
    locate_js: str,
) -> dict[str, Any]:
    """在 Studio iframe 隔离世界里执行 locate_js，期望返回 {found, rect, ...}。

    locate_js 必须是表达式，求值后返回上述结构。
    """
    result = await execute_in_studio(browser_session, locate_js)
    if result.get("exceptionDetails"):
        return {"found": False, "error": result["exceptionDetails"].get("text")}
    return (result.get("result") or {}).get("value") or {"found": False}


def _viewport_matches(entry: dict[str, Any], vw: float, vh: float, dpr: float) -> bool:
    if not entry:
        return False
    evw = entry.get("viewport_width")
    evh = entry.get("viewport_height")
    edpr = entry.get("device_pixel_ratio")
    if not evw or not evh:
        return False
    if abs(evw - vw) / max(evw, 1) > _VIEWPORT_TOLERANCE:
        return False
    if abs(evh - vh) / max(evh, 1) > _VIEWPORT_TOLERANCE:
        return False
    if edpr and abs(edpr - dpr) > 0.01:
        return False
    return True


# 一个 locator 函数：负责在当前 Studio iframe 状态下找到目标元素，
# 返回 {found: True, rect: {x,y,w,h}, label: str, selector: str} 或 {found: False, ...}。
LocatorFn = Callable[[BrowserSession], Awaitable[dict[str, Any]]]


async def _locate_and_record(
    browser_session: BrowserSession,
    key: str,
    kind: str,
    label: str,
    locator: LocatorFn,
) -> dict[str, Any] | None:
    """跑 locator 找出元素，写缓存并返回写入的 entry。

    出错或元素找不到时返回 None。
    """
    info = await locator(browser_session)
    if not info or not info.get("found"):
        logger.info("locator miss key=%s label=%s info=%s", key, label, info)
        return None

    rect = info.get("rect") or {}
    if not rect:
        return None

    geo = await _get_iframe_offset_and_viewport(browser_session)
    ox = float(geo.get("iframe_x", 0) or 0)
    oy = float(geo.get("iframe_y", 0) or 0)
    cx = ox + float(rect.get("x", 0)) + float(rect.get("w", 0)) / 2.0
    cy = oy + float(rect.get("y", 0)) + float(rect.get("h", 0)) / 2.0

    entry = {
        "name": key,
        "kind": kind,
        "label": label,
        "selector": info.get("selector") or "",
        "matched_text": info.get("text") or "",
        "x": round(cx, 1),
        "y": round(cy, 1),
        "viewport_width": geo.get("vw"),
        "viewport_height": geo.get("vh"),
        "device_pixel_ratio": geo.get("dpr"),
        "iframe_offset": {"x": ox, "y": oy},
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_cached(key, entry)
    logger.info("Cached PowerApps target %s @ (%.1f, %.1f)", key, cx, cy)
    return entry


# ── 主入口 ──────────────────────────────────────────────────


async def click_via_cache(
    browser_session: BrowserSession,
    key: str,
    *,
    kind: str,
    label: str,
    locator: LocatorFn,
    verify: Callable[[BrowserSession], Awaitable[bool]] | None = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    """读缓存 → 坐标点击；点不中就跑 locator 重定位、写盘、再点。

    返回 {ok: bool, source: 'cache'|'relocated', x, y, ...}。

    verify 可选：点击后调用，返回 True 表示成功。
    没有 verify 时默认信任坐标点击成功。
    """
    cdp_session = await browser_session.get_or_create_cdp_session()
    geo = await _get_iframe_offset_and_viewport(browser_session)
    vw, vh, dpr = geo.get("vw", 0), geo.get("vh", 0), geo.get("dpr", 1)

    entry = get_cached(key)
    used_source = "cache"

    # 视口不匹配 → 直接当缓存失效
    if entry and not _viewport_matches(entry, vw, vh, dpr):
        logger.info("Viewport changed for %s, refreshing target", key)
        entry = None

    async def _click_at(x: float, y: float) -> None:
        await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=cdp_session.session_id,
        )
        await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
            params={"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            session_id=cdp_session.session_id,
        )

    attempts = 0
    while attempts <= max_retries:
        attempts += 1

        if not entry:
            entry = await _locate_and_record(browser_session, key, kind, label, locator)
            used_source = "relocated"
            if not entry:
                return {"ok": False, "error": f"locator failed for {key}"}

        x = entry["x"]
        y = entry["y"]
        await _click_at(x, y)

        if verify is None:
            return {"ok": True, "source": used_source, "x": x, "y": y, "key": key}

        # 短暂等一下 UI 反应
        await asyncio.sleep(0.25)
        ok = False
        try:
            ok = bool(await verify(browser_session))
        except Exception as e:
            logger.debug("verify raised for %s: %s", key, e)

        if ok:
            return {"ok": True, "source": used_source, "x": x, "y": y, "key": key}

        # 失败：作废缓存重试
        logger.info("Cached click failed for %s, invalidating and retrying", key)
        invalidate(key)
        entry = None

    return {"ok": False, "error": f"click_via_cache exhausted retries for {key}"}


# ── 内置 locator 工厂：基于 selector / 文本匹配 ─────────────────


def selector_locator(selector: str, label: str = "") -> LocatorFn:
    """根据 CSS selector 在 Studio iframe 内定位首个可见元素。"""

    async def _fn(session: BrowserSession) -> dict[str, Any]:
        js = rf"""
        (() => {{
            const sel = {json.dumps(selector)};
            const el = document.querySelector(sel);
            if (!el) return {{found: false}};
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return {{found: false, reason: 'zero-size'}};
            return {{
                found: true,
                rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                selector: sel,
                text: (el.textContent || '').trim().slice(0, 80),
            }};
        }})()
        """
        return await _probe_in_studio(session, js)

    return _fn


def text_button_locator(label: str) -> LocatorFn:
    """通过可见文本 / aria-label / title 在 ribbon 类按钮里找首个匹配项。

    跟 click_powerapps_ribbon_button 的策略保持一致，但只读 rect 不点击。
    """

    async def _fn(session: BrowserSession) -> dict[str, Any]:
        js = rf"""
        (() => {{
            const target = {json.dumps(label)};
            const buttons = document.querySelectorAll(
                'button, [role="button"], [role="tab"], [is="button"]'
            );
            const cmp = (s) => s === target || s.includes(target);
            for (const btn of buttons) {{
                const text = (btn.textContent || '').trim();
                const lab = (btn.getAttribute('aria-label') || '').trim();
                const ttl = (btn.getAttribute('title') || '').trim();
                if (cmp(text) || cmp(lab) || cmp(ttl)) {{
                    const r = btn.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    return {{
                        found: true,
                        rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                        selector: 'text:' + (lab || text || ttl),
                        text: text || lab || ttl,
                    }};
                }}
            }}
            return {{found: false}};
        }})()
        """
        return await _probe_in_studio(session, js)

    return _fn


def listitem_locator(label: str) -> LocatorFn:
    """在插入面板 / 组件列表里通过文本找 listitem / option。"""

    async def _fn(session: BrowserSession) -> dict[str, Any]:
        js = rf"""
        (() => {{
            const target = {json.dumps(label)};
            const items = document.querySelectorAll(
                '[role="listitem"], [role="option"], [role="treeitem"], '
                '.component-item, [class*="component"], [class*="insert-item"], '
                '[class*="insert"] li, [class*="panel"] li'
            );
            for (const it of items) {{
                const text = (it.textContent || '').trim();
                const lab = (it.getAttribute('aria-label') || '').trim();
                if (text === target || lab === target ||
                    text.startsWith(target) || lab.startsWith(target)) {{
                    const r = it.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    return {{
                        found: true,
                        rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                        selector: 'listitem:' + target,
                        text: text || lab,
                    }};
                }}
            }}
            return {{found: false}};
        }})()
        """
        return await _probe_in_studio(session, js)

    return _fn


def property_input_locator(prop: str) -> LocatorFn:
    """在属性面板里找匹配 placeholder / aria-label 的 input。"""

    async def _fn(session: BrowserSession) -> dict[str, Any]:
        js = rf"""
        (() => {{
            const target = {json.dumps(prop)};
            const sels = [
                'input[aria-label*="' + target + '"]',
                '[data-automationid*="' + target + '"] input',
                '.property-editor input[placeholder*="' + target + '"]',
                'input[aria-labelledby*="' + target + '"]',
            ];
            for (const s of sels) {{
                const el = document.querySelector(s);
                if (!el) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                return {{
                    found: true,
                    rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                    selector: s,
                    text: el.value || el.placeholder || '',
                }};
            }}
            // fallback：遍历属性面板里所有 input
            const inputs = document.querySelectorAll(
                '.property-pane input, .property-editor input, '
                '[class*="property"] input, [class*="editor"] input'
            );
            for (const inp of inputs) {{
                const lab = inp.placeholder || inp.getAttribute('aria-label') || '';
                const parentText = (inp.parentElement ? inp.parentElement.textContent || '' : '');
                if (lab.includes(target) || parentText.includes(target)) {{
                    const r = inp.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    return {{
                        found: true,
                        rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                        selector: 'fallback-input:' + target,
                        text: inp.value || lab,
                    }};
                }}
            }}
            return {{found: false}};
        }})()
        """
        return await _probe_in_studio(session, js)

    return _fn


__all__ = [
    "click_via_cache",
    "get_cached",
    "save_cached",
    "invalidate",
    "list_all",
    "selector_locator",
    "text_button_locator",
    "listitem_locator",
    "property_input_locator",
]
