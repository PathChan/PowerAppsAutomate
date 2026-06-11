"""formula_bar.py — 公式栏/属性选择器 DOM 原子操作。

基于 DOM 直接操作 PowerApps Studio 的公式栏区域：
  点击属性选择器 combobox → 打开下拉
  获取所有属性选项
  按文本选择某个属性
  在公式编辑器中写入文本

不依赖经验库。
"""
from __future__ import annotations

import json
import logging

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS：打开属性选择器下拉框 + 获取所有选项（滚动）
# ═══════════════════════════════════════════════════════════════
_GET_OPTIONS_JS = r"""
(async () => {
    try {
        var container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false, error: 'no formulaBarContainer'};

        // 收集所有 role=option
        function collect() {
            var els = document.querySelectorAll('[role="option"],.ms-Dropdown-item,[class*="dropdown"] li');
            var out = [];
            els.forEach(function(o) {
                var r = o.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                var txt = (o.textContent || '').trim().slice(0, 120);
                for (var j = 0; j < out.length; j++) { if (out[j].text === txt) return; }
                out.push({text: txt, selected: o.getAttribute('aria-selected') || '', rect: {x: r.x, y: r.y, w: r.width, h: r.height}});
            });
            return out;
        }

        // 找触发器 + 打开下拉
        function openDropdown() {
            var candidates = container.querySelectorAll('button,[role="combobox"],[role="listbox"],select');
            var trig = null;
            for (var i = 0; i < candidates.length; i++) {
                var r = candidates[i].getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { trig = candidates[i]; break; }
            }
            if (!trig) return false;
            var o = {bubbles: true, cancelable: true, view: window};
            trig.focus();
            trig.dispatchEvent(new PointerEvent('pointerover', o));
            trig.dispatchEvent(new PointerEvent('pointerdown', o));
            trig.dispatchEvent(new MouseEvent('mousedown', o));
            trig.dispatchEvent(new PointerEvent('pointerup', o));
            trig.dispatchEvent(new MouseEvent('mouseup', o));
            trig.dispatchEvent(new MouseEvent('click', o));
            return true;
        }

        // 看是否已打开
        var opts = collect();
        if (opts.length === 0) {
            openDropdown();
            await new Promise(function(rr) { setTimeout(rr, 800); });
            opts = collect();
            if (opts.length === 0) return {found: false, error: 'dropdown empty after open'};
        }

        // 尝试滚动
        var scrollEl = document.querySelector(
            '[role="listbox"],.ms-Dropdown-items,[class*="dropdown"] [class*="menu"],' +
            '.ms-Callout-main,[class*="Callout"] [class*="main"],.ms-ContextualMenu-list,' +
            '[class*="menuContainer"],[class*="MenuContainer"]'
        );
        if (scrollEl) {
            var prev = 0;
            for (var s = 0; s < 20; s++) {
                scrollEl.scrollTop += 300;
                await new Promise(function(rr) { setTimeout(rr, 200); });
                var fresh = collect();
                if (fresh.length > prev) { opts = fresh; prev = fresh.length; } else break;
            }
            scrollEl.scrollTop = 0;
        }

        return {found: true, optionsCount: opts.length, options: opts};
    } catch (e) { return {found: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：选择第 N 个选项 + 收集面板数据
# ═══════════════════════════════════════════════════════════════
_SELECT_OPTION_JS = r"""
(async () => {
    try {
        var idx = INDEX_PLACEHOLDER;
        var container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false, error: 'no container'};

        function getVisibleOptions() {
            var els = document.querySelectorAll('[role="option"],.ms-Dropdown-item,[class*="dropdown"] li,[class*="menu"] [role="menuitem"],.ms-ContextualMenu-item');
            var vis = [];
            els.forEach(function(o) { var r = o.getBoundingClientRect(); if (r.width > 0 && r.height > 0) vis.push(o); });
            return vis;
        }

        var vis = getVisibleOptions();
        if (vis.length === 0) {
            var candidates = container.querySelectorAll('button,[role="combobox"],[role="listbox"],select');
            var trig = null;
            for (var i = 0; i < candidates.length; i++) { var r = candidates[i].getBoundingClientRect(); if (r.width > 0 && r.height > 0) { trig = candidates[i]; break; } }
            if (!trig) return {found: false, error: 'no trigger'};
            var o = {bubbles: true, cancelable: true, view: window};
            trig.focus();
            trig.dispatchEvent(new PointerEvent('pointerover', o));
            trig.dispatchEvent(new PointerEvent('pointerdown', o));
            trig.dispatchEvent(new MouseEvent('mousedown', o));
            trig.dispatchEvent(new PointerEvent('pointerup', o));
            trig.dispatchEvent(new MouseEvent('mouseup', o));
            trig.dispatchEvent(new MouseEvent('click', o));
            await new Promise(function(rr) { setTimeout(rr, 800); });
            vis = getVisibleOptions();
            if (vis.length === 0) return {found: false, error: 'dropdown empty'};
        }

        if (idx >= vis.length) return {found: false, error: 'index ' + idx + ' out of ' + vis.length};

        var target = vis[idx];
        var targetText = (target.textContent || '').trim().slice(0, 120);
        var o = {bubbles: true, cancelable: true, view: window};
        target.focus();
        target.dispatchEvent(new PointerEvent('pointerover', o));
        target.dispatchEvent(new PointerEvent('pointerdown', o));
        target.dispatchEvent(new MouseEvent('mousedown', o));
        target.dispatchEvent(new PointerEvent('pointerup', o));
        target.dispatchEvent(new MouseEvent('mouseup', o));
        target.dispatchEvent(new MouseEvent('click', o));
        await new Promise(function(rr) { setTimeout(rr, 800); });

        // 收集面板
        var formulaInput = container.querySelector('input,textarea,[role="textbox"],[contenteditable="true"]');
        var formulaValue = formulaInput ? (formulaInput.value || formulaInput.textContent || '') : '';

        var inputs = document.querySelectorAll('.property-pane input,.property-editor input,[class*="property"] input:not([type="hidden"]):not([type="password"]),.property-pane textarea,.property-editor textarea');
        var inputList = [];
        inputs.forEach(function(inp) { var r3 = inp.getBoundingClientRect(); if (r3.width <= 0 || r3.height <= 0) return; inputList.push({placeholder: inp.placeholder || '', ariaLabel: inp.getAttribute('aria-label') || '', value: (inp.value || '').slice(0, 200)}); });

        var labels = document.querySelectorAll('.property-pane label,.property-editor label,[class*="property"] label,[class*="editor"] label');
        var labelList = [];
        labels.forEach(function(lbl) { var t2 = (lbl.textContent || '').trim(); if (!t2) return; labelList.push(t2.slice(0, 100)); });

        return {found: true, index: idx, optionText: targetText, panelData: {formulaValue: formulaValue, propertyInputs: inputList, propertyLabels: labelList}};
    } catch (e) { return {found: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：在公式编辑器中写入文本
# ═══════════════════════════════════════════════════════════════
_TYPE_FORMULA_JS = r"""
(function() {
    try {
        var text = TARGET_TEXT_PLACEHOLDER;
        var clear = CLEAR_EXISTING_PLACEHOLDER;
        var container = document.querySelector('#formulaBarContainer');
        if (!container) return {success: false, error: 'no container'};

        var editor = container.querySelector('textarea,[contenteditable="true"],input');
        if (!editor) return {success: false, error: 'no editor found'};

        editor.focus();
        if (clear) {
            if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
                editor.value = '';
            } else {
                editor.textContent = '';
            }
        }

        // 写入文本
        if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
            editor.value = text;
            editor.dispatchEvent(new Event('input', {bubbles: true}));
            editor.dispatchEvent(new Event('change', {bubbles: true}));
        } else {
            editor.textContent = text;
            editor.dispatchEvent(new Event('input', {bubbles: true}));
        }

        return {success: true, value: text.slice(0, 200)};
    } catch (e) { return {success: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""


# ── 公开 API ──────────────────────────────────────────────────


async def get_property_options(session: BrowserSession) -> dict:
    """打开属性选择器下拉框并返回全量选项（含滚动）。

    Args:
        session: BrowserSession

    Returns:
        {found, optionsCount, options: [{text, selected, rect}]}
    """
    raw = await execute_in_studio(session, _GET_OPTIONS_JS)
    if raw.get("exceptionDetails"):
        return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"found": False}


async def select_property_option(session: BrowserSession, index: int) -> dict:
    """选择第 index 个属性选项并收集面板数据。

    Args:
        session: BrowserSession
        index: 选项索引（0-based）

    Returns:
        {found, index, optionText, panelData: {formulaValue, propertyInputs, propertyLabels}}
    """
    js = _SELECT_OPTION_JS.replace("INDEX_PLACEHOLDER", str(index))
    raw = await execute_in_studio(session, js)
    if raw.get("exceptionDetails"):
        return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"found": False}


async def type_into_formula(session: BrowserSession, text: str, clear_existing: bool = True) -> dict:
    """在公式编辑器中写入文本。

    Args:
        session: BrowserSession
        text: 要写入的文本 / Power Fx 公式
        clear_existing: 是否先清空已有内容

    Returns:
        {success, value, error?}
    """
    js = _TYPE_FORMULA_JS \
        .replace("TARGET_TEXT_PLACEHOLDER", json.dumps(text)) \
        .replace("CLEAR_EXISTING_PLACEHOLDER", "true" if clear_existing else "false")
    raw = await execute_in_studio(session, js)
    if raw.get("exceptionDetails"):
        return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"success": False}