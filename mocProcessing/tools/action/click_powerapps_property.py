"""click_powerapps_property：在 PowerApps Studio 属性面板中点击指定属性字段。

例如点击"Text"属性以聚焦公式栏，然后可用 click_funcInput 写入新值。

改造：优先从坐标缓存点击，缓存失效时自动回退到 DOM 查询并更新缓存。
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from mocProcessing.tools.powerapps_chain import click_in_studio, execute_in_studio
from mocProcessing.tools.target_cache import (
    click_via_cache,
    property_input_locator,
)


class ClickPropertyParams(BaseModel):
    """参数：要点击的属性名称。"""

    property_name: str = Field(
        default="Text",
        description=(
            '属性面板中的属性名。默认 "Text"，'
            '也可以是 "X"、"Y"、"Width"、"Height"、"Fill" 等。'
        ),
    )


async def click_property(params: ClickPropertyParams, browser_session: BrowserSession) -> ActionResult:
    """在 PowerApps Studio 属性面板中找到指定名称的属性字段并点击。

    优先读坐标缓存 → CDP 坐标点击；失败则回退到 DOM 查询 + JS 事件派发。
    """
    prop = params.property_name
    cache_key = f"property::{prop}"

    # ── 1) 缓存路径 ──────────────────────────────────────────
    try:
        result = await click_via_cache(
            browser_session,
            cache_key,
            kind="property_input",
            label=prop,
            locator=property_input_locator(prop),
        )
        if result.get("ok"):
            msg = f"Clicked property '{prop}' via cache (source={result.get('source')})"
            return ActionResult(extracted_content=msg, long_term_memory=msg)
    except Exception:
        pass

    # ── 2) DOM 回退 ──────────────────────────────────────────

    # 策略 1: 通过 CSS 选择器直接点击
    selectors = [
        f'input[aria-label*="{prop}"]',
        f'[data-automationid*="{prop}"] input',
        f'.property-editor input[placeholder*="{prop}"]',
        f'input[aria-labelledby*="{prop}"]',
    ]
    for sel in selectors:
        result = await click_in_studio(browser_session, sel, retries=1)
        if result.get("success"):
            message = f"Clicked property '{prop}' in property pane"
            return ActionResult(extracted_content=message, long_term_memory=message)

    # 策略 2: JS 遍历查找
    js = rf"""
    (() => {{
        const target = {json.dumps(prop)};

        // 查找所有可能包含属性编辑器的 input
        const inputs = document.querySelectorAll(
            '.property-pane input, .property-editor input, '
            '[class*="property"] input, [class*="editor"] input'
        );
        for (const inp of inputs) {{
            const label = inp.placeholder || inp.getAttribute('aria-label') || '';
            const parentText = (inp.parentElement ? inp.parentElement.textContent || '' : '');
            if (label.includes(target) || parentText.includes(target)) {{
                const r = inp.getBoundingClientRect();
                const opts = {{bubbles: true, cancelable: true, view: window}};
                inp.dispatchEvent(new PointerEvent('pointerover', opts));
                inp.dispatchEvent(new PointerEvent('pointerdown', opts));
                inp.dispatchEvent(new MouseEvent('mousedown', opts));
                inp.dispatchEvent(new PointerEvent('pointerup', opts));
                inp.dispatchEvent(new MouseEvent('mouseup', opts));
                inp.dispatchEvent(new MouseEvent('click', opts));
                inp.focus();
                return {{
                    success: true,
                    inputValue: inp.value || '',
                    rect: {{x: r.x, y: r.y, w: r.width, h: r.height}},
                }};
            }}
        }}

        // 策略 3: 查找属性行（label + input 组合）
        const rows = document.querySelectorAll('[class*="property-row"], [class*="field-row"], '
            '[class*="property-group"]');
        for (const row of rows) {{
            const text = (row.textContent || '').trim();
            if (text.includes(target)) {{
                const input = row.querySelector('input, textarea, [contenteditable]');
                if (input) {{
                    input.focus();
                    input.click();
                    return {{success: true, inputValue: input.value || ''}};
                }}
            }}
        }}

        return {{success: false, error: 'Property "' + target + '" input not found'}};
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
            error=f"Property '{prop}' not found in property pane: {value.get('error', 'unknown')}"
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
                "kind": "property_input",
                "label": prop,
                "selector": "property:" + prop,
                "matched_text": value.get("inputValue", ""),
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

    message = f"Clicked property '{prop}' (current value: {value.get('inputValue', '')})"
    return ActionResult(extracted_content=message, long_term_memory=message)


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        (
            'Click a property field in the PowerApps Studio property pane by name. '
            'Use this to focus a property (e.g. "Text", "X", "Y", "Fill") so that '
            'the formula bar activates. After this you can use click_funcInput to '
            'write a new value. Default property_name="Text".'
        ),
        param_model=ClickPropertyParams,
    )(click_property)