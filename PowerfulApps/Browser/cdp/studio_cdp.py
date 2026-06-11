"""PowerApps Studio iframe 的 CDP 执行工具。"""

from __future__ import annotations

import asyncio
import json

# ── Studio 上下文缓存 ──────────────────────────────────────────
# frameId 和 executionContextId 在 iframe 生命周期内稳定，缓存避免重复 CDP 调用。
_ctx_cache: dict[str, str] = {}       # "_frame_id" -> frameId
_ctx_world_cache: dict[str, str] = {}  # "_exec_ctx_id" -> executionContextId


def reset_studio_cache() -> None:
    """手动清除缓存的 studio 上下文（iframe 重建后调用）。"""
    _ctx_cache.clear()
    _ctx_world_cache.clear()


async def _ensure_studio_context(browser_session: "BrowserSession") -> dict:
    """获取缓存的 Studio iframe 上下文，必要时首次查找/创建。

    返回 {"frameId": str, "executionContextId": str}。
    """
    from PowerfulApps.Browser.core import BrowserSession
    cache_key = id(browser_session)

    frame_id = _ctx_cache.get(f"{cache_key}_frame_id")
    ctx_id = _ctx_world_cache.get(f"{cache_key}_exec_ctx_id")

    if frame_id and ctx_id:
        return {"frameId": frame_id, "executionContextId": ctx_id}

    cdp_session = await browser_session.get_or_create_cdp_session()
    tree = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)
    frame_tree = (tree or {}).get("frameTree", {})

    def _search(tree: dict) -> str | None:
        frame = tree.get("frame", {})
        if frame.get("name") == "EmbeddedStudio":
            return frame.get("id")
        for child in tree.get("childFrames", []):
            found = _search(child)
            if found:
                return found
        return None

    frame_id = _search(frame_tree)
    if not frame_id:
        return {"error": "未找到 EmbeddedStudio iframe"}

    _ctx_cache[f"{cache_key}_frame_id"] = frame_id

    world = await cdp_session.cdp_client.send.Page.createIsolatedWorld(
        params={
            "frameId": frame_id,
            "grantUniveralAccess": True,
        },
        session_id=cdp_session.session_id,
    )
    ctx_id = world.get("executionContextId")
    _ctx_world_cache[f"{cache_key}_exec_ctx_id"] = ctx_id

    return {"frameId": frame_id, "executionContextId": ctx_id}


async def execute_in_studio(
    browser_session: "BrowserSession",
    js_code: str,
    return_by_value: bool = True,
    await_promise: bool = True,
) -> dict:
    """在 PowerApps Studio iframe（EmbeddedStudio）的隔离世界里执行 JS。

    如果 CDP 重新连接导致旧 context 失效（"Cannot find context with specified id"），
    会自动清缓存、重建 context 并重试一次。
    """
    from PowerfulApps.Browser.core import BrowserSession

    for attempt in range(2):  # 最多重试 1 次
        ctx = await _ensure_studio_context(browser_session)
        if ctx.get("error"):
            return ctx

        cdp_session = await browser_session.get_or_create_cdp_session()
        try:
            result = await cdp_session.cdp_client.send.Runtime.evaluate(
                params={
                    "expression": js_code,
                    "contextId": ctx["executionContextId"],
                    "returnByValue": return_by_value,
                    "awaitPromise": await_promise,
                },
                session_id=cdp_session.session_id,
            )
            return result
        except RuntimeError as e:
            err_str = str(e)
            if "Cannot find context with specified id" in err_str and attempt == 0:
                # 缓存过期，清掉重建
                reset_studio_cache()
                continue
            raise

    return {"error": "execute_in_studio 失败（重试后）"}


async def find_studio_frame_id(browser_session: BrowserSession) -> str | None:
    """返回缓存的 EmbeddedStudio frameId。"""
    ctx = await _ensure_studio_context(browser_session)
    return ctx.get("frameId")


async def click_in_studio(
    browser_session: BrowserSession,
    css_selector: str,
    retries: int = 3,
    delay: float = 0.5,
) -> dict:
    """在 Studio iframe 内通过 CSS 选择器找到元素并点击。"""
    js = rf"""
    (() => {{
        const el = document.querySelector({json.dumps(css_selector)});
        if (!el) return {{success: false, error: 'Selector not found: {json.dumps(css_selector)}'}};
        const rect = el.getBoundingClientRect();
        el.dispatchEvent(new PointerEvent('pointerover', {{bubbles: true, cancelable: true, view: window}}));
        el.dispatchEvent(new PointerEvent('pointerdown', {{bubbles: true, cancelable: true, view: window}}));
        el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, cancelable: true, view: window}}));
        el.dispatchEvent(new PointerEvent('pointerup', {{bubbles: true, cancelable: true, view: window}}));
        el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, cancelable: true, view: window}}));
        el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, view: window}}));
        return {{
            success: true,
            tag: el.tagName,
            id: el.id,
            text: (el.textContent || '').trim().slice(0, 200),
            rect: {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}},
        }};
    }})()
    """
    for attempt in range(1, retries + 1):
        result = await execute_in_studio(browser_session, js)
        if result.get("exceptionDetails"):
            err = result["exceptionDetails"].get("text", "unknown JS error")
            if attempt < retries:
                await asyncio.sleep(delay)
                continue
            return {"success": False, "error": err}
        value = (result.get("result") or {}).get("value") or {}
        if value.get("success"):
            return value
        if attempt < retries:
            await asyncio.sleep(delay)
    return {"success": False, "error": "Max retries reached without success"}


async def diagnose_focus_state(browser_session: BrowserSession) -> dict:
    """诊断当前 activeElement 状态。"""
    js = """(() => {
        const el = document.activeElement;
        if (!el) return {activeTag: null, isInputArea: false};
        return {
            activeTag: el.tagName,
            activeClass: (el.className || '').slice(0, 300),
            activeId: el.id || '',
            isInputArea: !!(el.classList && el.classList.contains('inputarea')),
            _ctx: 'isolatedWorld',
        };
    })()"""
    result = await execute_in_studio(browser_session, js)
    if result.get("exceptionDetails"):
        return {"error": result["exceptionDetails"].get("text", "unknown")}
    return (result.get("result") or {}).get("value") or {}


async def focus_formula_editor_via_dispatch(browser_session: BrowserSession) -> dict:
    """在 #formulaBarContainer .view-lines 上派发完整事件序列以聚焦 Monaco textarea。"""
    js = """(() => {
        const viewLines = document.querySelector('#formulaBarContainer .view-lines');
        if (!viewLines) return {focused: false, reason: '#formulaBarContainer .view-lines not found'};
        const dispatchAll = (el) => {
            const opts = {bubbles: true, cancelable: true, view: window};
            el.dispatchEvent(new PointerEvent('pointerover', opts));
            el.dispatchEvent(new PointerEvent('pointerenter', {...opts, bubbles: false}));
            el.dispatchEvent(new PointerEvent('pointermove', opts));
            el.dispatchEvent(new MouseEvent('mousemove', opts));
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
        };
        dispatchAll(viewLines);
        const active = document.activeElement;
        return {
            focused: !!(active && active.classList && active.classList.contains('inputarea')),
            activeTag: active ? active.tagName : null,
            activeClass: active ? (active.className || '').slice(0, 300) : null,
        };
    })()"""
    result = await execute_in_studio(browser_session, js)
    if result.get("exceptionDetails"):
        return {"focused": False, "error": result["exceptionDetails"].get("text", "unknown")}
    return (result.get("result") or {}).get("value") or {}


async def inspect_formula_bar_dom(browser_session: BrowserSession) -> dict:
    """检查公式栏 DOM 状态。"""
    js = """(() => {
        const container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false};
        const btn = container.querySelector('button');
        const viewLines = container.querySelector('.view-lines');
        const textarea = document.querySelector('textarea.inputarea');
        return {
            found: true,
            hasButton: !!btn,
            hasViewLines: !!viewLines,
            hasTextarea: !!textarea,
            innerText: (container.textContent || '').trim().slice(0, 300),
        };
    })()"""
    result = await execute_in_studio(browser_session, js)
    if result.get("exceptionDetails"):
        return {"error": result["exceptionDetails"].get("text", "unknown")}
    return (result.get("result") or {}).get("value") or {}


async def list_frames_via_page(browser_session: BrowserSession) -> dict:
    """获取 Page.getFrameTree 结果。"""
    cdp_session = await browser_session.get_or_create_cdp_session()
    tree = await cdp_session.cdp_client.send.Page.getFrameTree(session_id=cdp_session.session_id)
    return tree or {}


async def list_all_targets(browser_session: BrowserSession) -> list[dict]:
    """列出所有 CDP targets。"""
    cdp_session = await browser_session.get_or_create_cdp_session()
    targets = await cdp_session.cdp_client.send.Target.getTargets(session_id=cdp_session.session_id)
    return (targets or {}).get("targetInfos", [])
