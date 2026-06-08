"""PowerApps Studio 操作链与共享工具函数。

包含：
- 被 click_funcInput.py 引用的 diagnose_focus_state / focus_formula_editor_via_dispatch
- 通用的 Studio iframe 内 JS 执行工具（execute_in_studio / click_in_studio）
- PowerAppsChainAction model + execute_powerapps_chain（供 service.py 注册）
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession

logger = logging.getLogger(__name__)

# ── Studio 上下文缓存 ──────────────────────────────────────────
# frameId 和 executionContextId 在 iframe 生命周期内稳定，缓存避免重复 CDP 调用。
_ctx_cache: dict[str, str] = {}       # "_frame_id" -> frameId
_ctx_world_cache: dict[str, str] = {}  # "_exec_ctx_id" -> executionContextId


def reset_studio_cache() -> None:
    """手动清除缓存的 studio 上下文（iframe 重建后调用）。"""
    _ctx_cache.clear()
    _ctx_world_cache.clear()


async def _ensure_studio_context(browser_session: BrowserSession) -> dict:
    """获取缓存的 Studio iframe 上下文，必要时首次查找/创建。

    返回 {"frameId": str, "executionContextId": str}。
    """
    cache_key = id(browser_session)

    frame_id = _ctx_cache.get(f"{cache_key}_frame_id")
    ctx_id = _ctx_world_cache.get(f"{cache_key}_exec_ctx_id")

    if frame_id and ctx_id:
        return {"frameId": frame_id, "executionContextId": ctx_id}

    # 首次：查 frameTree
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
        return {"error": "EmbeddedStudio iframe not found"}

    _ctx_cache[f"{cache_key}_frame_id"] = frame_id

    # 创建隔离世界（不传 worldName，避免 CDP 客户端序列化 None 报错）
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
    browser_session: BrowserSession,
    js_code: str,
    return_by_value: bool = True,
    await_promise: bool = True,
) -> dict:
    """在 PowerApps Studio iframe（EmbeddedStudio）的隔离世界里执行 JS。

    缓存 frameId 和 executionContextId，避免每次重复创建。
    返回 Runtime.evaluate 的完整 result dict。
    """
    ctx = await _ensure_studio_context(browser_session)
    if ctx.get("error"):
        return ctx

    cdp_session = await browser_session.get_or_create_cdp_session()
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


# ── 向后兼容别名 ──────────────────────────────────────────────


async def find_studio_frame_id(browser_session: BrowserSession) -> str | None:
    """向后兼容：返回缓存的 EmbeddedStudio frameId。"""
    ctx = await _ensure_studio_context(browser_session)
    return ctx.get("frameId")


# ── 点击工具 ──────────────────────────────────────────────────


async def click_in_studio(
    browser_session: BrowserSession,
    css_selector: str,
    retries: int = 3,
    delay: float = 0.5,
) -> dict:
    """在 Studio iframe 内通过 CSS 选择器找到元素并点击。

    返回 {success: bool, error: str | None, details: ...}。
    """
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
    # 先通过 execute_in_studio 尝试
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


# ── 原有函数（被 click_funcInput.py 引用）───────────────────────


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
        // Monaco 把焦点转给隐藏 textarea.inputarea
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


# ── PowerAppsChainAction（被 service.py 引用）─────────────────────


class PowerAppsChainAction(BaseModel):
    """PowerApps 操作链参数。

    支持两种模式：
    - insert_component_set_formula: 插入组件 → 选择属性 → 设置公式
    - set_current_selection_formula: 直接设置当前选中控件的属性公式
    """

    chain: str = Field(
        description="操作链类型：insert_component_set_formula | set_current_selection_formula",
    )
    component: str | None = Field(
        default=None,
        description="要插入的组件名（insert_component_set_formula 模式），如 '按钮'、'文本输入框'",
    )
    property_name: str | None = Field(
        default=None,
        description="要设置的属性名，如 'Text'、'X'、'Y'、'Fill'",
    )
    formula: str = Field(
        description="要写入的公式/文本",
    )


async def execute_powerapps_chain(
    params: PowerAppsChainAction,
    browser_session: BrowserSession,
) -> ActionResult:
    """执行 PowerApps 操作链。

    根据 params.chain 分发到不同流程。
    """
    chain = params.chain
    if chain == "set_current_selection_formula":
        return await _chain_set_selection_formula(params, browser_session)
    elif chain == "insert_component_set_formula":
        return await _chain_insert_component(params, browser_session)
    else:
        return ActionResult(error=f"Unknown chain: {chain!r}")


async def _chain_set_selection_formula(
    params: PowerAppsChainAction,
    browser_session: BrowserSession,
) -> ActionResult:
    """选中当前控件的指定属性并写入公式。"""
    from mocProcessing.tools.action.click_funcInput import (
        ClickFuncInputParams,
        click_func_input,
    )

    property_name = params.property_name or "Text"

    # 1) 点击属性名标签（通常是属性面板中的标签元素）
    prop_selector = f'[data-ux-name="{property_name}"] input, [data-automationid="{property_name}"] input, .property-editor-label-{property_name.lower()} input, input[aria-label*="{property_name}"]'
    click_result = await click_in_studio(browser_session, prop_selector)
    if not click_result.get("success"):
        # fallback: 点击属性面板里的 input 元素
        fallback_js = rf"""
        (() => {{
            const inputs = document.querySelectorAll('.property-pane input, .property-editor input');
            for (const inp of inputs) {{
                const label = inp.placeholder || inp.getAttribute('aria-label') || '';
                if (label.includes({json.dumps(property_name)})) {{
                    inp.focus();
                    inp.click();
                    return {{success: true, text: inp.value || ''}};
                }}
            }}
            return {{success: false, error: 'Property not found: {json.dumps(property_name)}'}};
        }})()
        """
        click_result = await execute_in_studio(browser_session, fallback_js)
        click_result = (click_result.get("result") or {}).get("value") or click_result

    if not click_result.get("success"):
        return ActionResult(
            error=f"Failed to click property '{property_name}': {click_result.get('error')}"
        )

    await asyncio.sleep(0.3)

    # 2) 写入公式
    try:
        func_params = ClickFuncInputParams(text=params.formula, clear_existing=True)
        return await click_func_input(func_params, browser_session)
    except Exception as e:
        return ActionResult(
            error=f"Chain completed click but formula write failed: {e}"
        )


async def _chain_insert_component(
    params: PowerAppsChainAction,
    browser_session: BrowserSession,
) -> ActionResult:
    """插入组件 → 选择属性 → 写入公式。"""
    from mocProcessing.tools.action.click_funcInput import (
        ClickFuncInputParams,
        click_func_input,
    )

    component = params.component or "按钮"
    property_name = params.property_name or "Text"
    formula = params.formula

    # 1) 点击 ribbon 中的插入按钮
    insert_btn_js = rf"""
    (() => {{
        const buttons = document.querySelectorAll('button, [role="button"], [role="tab"]');
        for (const btn of buttons) {{
            const text = (btn.textContent || '').trim();
            const label = (btn.getAttribute('aria-label') || '').trim();
            if (text.includes('插入') || label.includes('插入') || text.includes('Insert') || label.includes('Insert')) {{
                btn.click();
                return {{success: true, text: text || label}};
            }}
        }}
        return {{success: false, error: 'Insert button not found'}};
    }})()
    """
    result = await execute_in_studio(browser_session, insert_btn_js)
    value = (result.get("result") or {}).get("value") or {}
    if not value.get("success"):
        return ActionResult(error=f"Failed to click Insert button: {value.get('error')}")

    await asyncio.sleep(0.8)

    # 2) 从组件列表中找到目标组件并点击
    component_js = rf"""
    (() => {{
        const items = document.querySelectorAll('[role="listitem"], [role="option"], .component-item, .insert-panel-item, [data-ux-name*="component"]');
        for (const item of items) {{
            const text = (item.textContent || '').trim();
            const label = (item.getAttribute('aria-label') || '').trim();
            if (text.includes({json.dumps(component)}) || label.includes({json.dumps(component)})) {{
                item.click();
                return {{success: true, text: text || label}};
            }}
        }}
        // fallback: 搜索所有可见元素
        const all = document.querySelectorAll('.insert-panel * li, .insert-panel * div, [class*="component"], [class*="insert"]');
        for (const el of all) {{
            const text = (el.textContent || '').trim();
            if (text === {json.dumps(component)} || text.startsWith({json.dumps(component)})) {{
                el.click();
                return {{success: true, text: text}};
            }}
        }}
        return {{success: false, error: 'Component {json.dumps(component)} not found'}};
    }})()
    """
    result = await execute_in_studio(browser_session, component_js)
    value = (result.get("result") or {}).get("value") or {}
    if not value.get("success"):
        return ActionResult(error=f"Failed to select component '{component}': {value.get('error')}")

    await asyncio.sleep(0.8)

    # 3) 点击属性
    prop_selector = f'input[aria-label*="{property_name}"], [data-automationid*="{property_name}"] input, .property-editor input[placeholder*="{property_name}"]'
    click_result = await click_in_studio(browser_session, prop_selector)
    if not click_result.get("success"):
        fallback = rf"""
        (() => {{
            const inputs = document.querySelectorAll('.property-pane input, .property-editor input');
            for (const inp of inputs) {{
                const label = inp.placeholder || inp.getAttribute('aria-label') || '';
                if (label.includes({json.dumps(property_name)})) {{
                    inp.focus(); inp.click();
                    return {{success: true}};
                }}
            }}
            return {{success: false}};
        }})()
        """
        result = await execute_in_studio(browser_session, fallback)
        click_result = (result.get("result") or {}).get("value") or {}

    if not click_result.get("success"):
        return ActionResult(error=f"Failed to click property '{property_name}'")

    await asyncio.sleep(0.3)

    # 4) 写入公式
    try:
        func_params = ClickFuncInputParams(text=formula, clear_existing=True)
        return await click_func_input(func_params, browser_session)
    except Exception as e:
        return ActionResult(error=f"Chain completed but formula write failed: {e}")