"""click_powerapps_component：在 PowerApps Studio 组件插入面板中选中指定组件。

例如点击"按钮"以在画布上插入一个新按钮控件。

改造：优先从坐标缓存点击，缓存失效时自动回退到 DOM 查询并更新缓存。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from mocProcessing.tools.powerapps_chain import execute_in_studio
from mocProcessing.tools.target_cache import (
    click_via_cache,
    listitem_locator,
)


class ClickComponentParams(BaseModel):
    """参数：要选中的组件名称。"""

    component_name: str = Field(
        default="按钮",
        description=(
            '组件列表中要选中的组件名。默认 "按钮"，'
            '也可以是 "文本输入框"、"标签"、"图标" 等。'
        ),
    )


async def click_component(params: ClickComponentParams, browser_session: BrowserSession) -> ActionResult:
    """在 PowerApps Studio 组件插入面板中找到目标组件并点击。

    优先读坐标缓存 → CDP 坐标点击；失败则回退到 DOM 查询 + JS 事件派发。
    """
    label = params.component_name
    cache_key = f"component::{label}"

    # ── 1) 缓存路径 ──────────────────────────────────────────
    try:
        result = await click_via_cache(
            browser_session,
            cache_key,
            kind="component_item",
            label=label,
            locator=listitem_locator(label),
        )
        if result.get("ok"):
            msg = f"Selected component '{label}' via cache (source={result.get('source')})"
            return ActionResult(extracted_content=msg, long_term_memory=msg)
    except Exception:
        pass  # 缓存出错不阻断

    # ── 2) DOM 回退 ──────────────────────────────────────────
    js = rf"""
    (() => {{
        const target = {json.dumps(label)};

        // 策略 1: 标准 listitem / option 角色
        const items = document.querySelectorAll(
            '[role="listitem"], [role="option"], [role="treeitem"], '
            '.component-item, [class*="component"], [class*="insert-item"]'
        );
        for (const item of items) {{
            const text = (item.textContent || '').trim();
            const lbl = (item.getAttribute('aria-label') || '').trim();
            if (text === target || lbl === target ||
                text.startsWith(target) || lbl.startsWith(target)) {{
                const r = item.getBoundingClientRect();
                const opts = {{bubbles: true, cancelable: true, view: window}};
                item.dispatchEvent(new PointerEvent('pointerover', opts));
                item.dispatchEvent(new PointerEvent('pointerdown', opts));
                item.dispatchEvent(new MouseEvent('mousedown', opts));
                item.dispatchEvent(new PointerEvent('pointerup', opts));
                item.dispatchEvent(new MouseEvent('mouseup', opts));
                item.dispatchEvent(new MouseEvent('click', opts));
                return {{
                    success: true,
                    clickedText: text || lbl,
                    rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                }};
            }}
        }}

        // 策略 2: 列表/网格中的任意元素
        const lists = document.querySelectorAll('[class*="insert"] li, [class*="panel"] li, '
            '[class*="gallery"] li, [class*="grid"] [class*="item"]');
        for (const el of lists) {{
            const text = (el.textContent || '').trim();
            if (text === target || text.startsWith(target)) {{
                el.click();
                return {{success: true, clickedText: text}};
            }}
        }}

        return {{success: false, error: 'Component "' + target + '" not found in insert panel'}};
    }})()
    """
    result = await execute_in_studio(browser_session, js)
    if result.get("exceptionDetails"):
        return ActionResult(
            error=f"JS execution failed: {result['exceptionDetails'].get('text', 'unknown')}"
        )

    value = (result.get("result") or {}).get("value") or {}

    if not value.get("success"):
        return ActionResult(
            error=f"Component '{label}' not found: {value.get('error', 'unknown')}"
        )

    # DOM 成功 → 写入缓存
    rect = value.get("rect")
    if rect and rect.get("w", 0) > 0:
        from mocProcessing.tools.target_cache import _get_iframe_offset_and_viewport, save_cached
        from datetime import datetime, timezone
        try:
            geo = await _get_iframe_offset_and_viewport(browser_session)
            ox = float(geo.get("iframe_x", 0) or 0)
            oy = float(geo.get("iframe_y", 0) or 0)
            cx = ox + float(rect["x"]) + float(rect["w"]) / 2.0
            cy = oy + float(rect["y"]) + float(rect["h"]) / 2.0
            save_cached(cache_key, {
                "name": cache_key,
                "kind": "component_item",
                "label": label,
                "selector": "listitem:" + label,
                "matched_text": value.get("clickedText", ""),
                "x": round(cx, 1),
                "y": round(cy, 1),
                "viewport_width": geo.get("vw"),
                "viewport_height": geo.get("vh"),
                "device_pixel_ratio": geo.get("dpr"),
                "iframe_offset": {"x": ox, "y": oy},
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
        except Exception:
            pass

    message = f"Selected component '{label}' from insert panel"
    return ActionResult(extracted_content=message, long_term_memory=message)


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        (
            'Select a component from the PowerApps Studio insert/components panel. '
            'Use this after clicking the Insert ribbon button to choose which control '
            'to add to the canvas (e.g. "按钮", "文本输入框", "标签"). '
            'Default component_name="按钮".'
        ),
        param_model=ClickComponentParams,
    )(click_component)