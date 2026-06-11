"""insert_menu.py — 插入菜单 DOM 原子操作。

基于 DOM 直接操作 PowerApps Studio 的插入菜单（role="tree" 树形列表），
不依赖经验库、不依赖坐标缓存。

操作：
  click_ribbon_insert()       — 点击顶部 Ribbon 的"插入"按钮
  get_insert_menu_items()     — 展开所有分类后，返回所有控件选项
  click_insert_menu_item()    — 按文本点击某个控件（展开分类后）

所有函数都是纯 async DOM 操作，返回统一的 dict 结果。
"""
from __future__ import annotations

import asyncio
import json
import logging

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS：点击 Ribbon 的"插入"按钮
# ═══════════════════════════════════════════════════════════════
_CLICK_INSERT_RIBBON_JS = r"""
(function() {
    try {
        var target = TARGET_TEXT_PLACEHOLDER;
        var all = document.querySelectorAll(
            'button,[role="button"],[role="menuitem"],[role="tab"]'
        );
        var match = null;
        var bestDist = Infinity;
        var cx = window.innerWidth / 2;
        var cy = window.innerHeight / 2;
        all.forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            var text = (el.textContent || '').trim().replace(/\s+/g, ' ');
            var label = (el.getAttribute('aria-label') || '').trim();
            if (text === target || label === target ||
                text.indexOf(target) >= 0 || label.indexOf(target) >= 0) {
                var ecx = r.x + r.width / 2;
                var ecy = r.y + r.height / 2;
                var dist = Math.sqrt((ecx - cx) * (ecx - cx) + (ecy - cy) * (ecy - cy));
                if (dist < bestDist) { bestDist = dist; match = el; }
            }
        });
        if (!match) return {success: false, error: 'not found: ' + target};
        var mr = match.getBoundingClientRect();
        match.focus();
        var opts = {bubbles: true, cancelable: true, view: window};
        match.dispatchEvent(new PointerEvent('pointerover', opts));
        match.dispatchEvent(new PointerEvent('pointerdown', opts));
        match.dispatchEvent(new MouseEvent('mousedown', opts));
        match.dispatchEvent(new PointerEvent('pointerup', opts));
        match.dispatchEvent(new MouseEvent('mouseup', opts));
        match.dispatchEvent(new MouseEvent('click', opts));
        return {success: true, text: (match.textContent || '').trim().slice(0, 100)};
    } catch (e) { return {success: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：展开插入菜单的所有分类 + 列出所有控件
# ═══════════════════════════════════════════════════════════════
_GET_MENU_ITEMS_JS = r"""
(async () => {
    try {
        function findMenu() {
            var r = document.querySelectorAll(
                '#shell-layer-host-id .ms-List[role="tree"],' +
                '#shell-layer-host-id [role="tree"]'
            );
            for (var i = 0; i < r.length; i++) {
                var rr = r[i].getBoundingClientRect();
                if (rr.width > 50 && rr.height > 50) return r[i];
            }
            var vc = document.querySelector('#shell-layer-host-id [class*="viewContainer"]');
            if (vc) { var rr = vc.getBoundingClientRect(); if (rr.width > 50 && rr.height > 50) return vc; }
            return null;
        }
        function isHeader(el) {
            return !!el.querySelector('[class*="CategoryLabel"],[class*="categoryLabel"],[class*="category"],[class*="Category"]');
        }
        function isLeaf(el) {
            return !!el.querySelector('.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]') && !isHeader(el);
        }
        function getText(el) {
            var lb = el.querySelector('.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]');
            return lb ? (lb.textContent || '').trim() : '';
        }
        function clickEl(el) {
            var o = {bubbles: true, cancelable: true, view: window};
            el.focus();
            el.dispatchEvent(new PointerEvent('pointerover', o));
            el.dispatchEvent(new PointerEvent('pointerdown', o));
            el.dispatchEvent(new MouseEvent('mousedown', o));
            el.dispatchEvent(new PointerEvent('pointerup', o));
            el.dispatchEvent(new MouseEvent('mouseup', o));
            el.dispatchEvent(new MouseEvent('click', o));
        }

        var menu = findMenu();
        if (!menu) return {found: false, error: 'insert menu not visible'};

        // 展开所有折叠分类
        var cats = menu.querySelectorAll('[role="treeitem"]');
        var headers = [];
        cats.forEach(function(el) { if (isHeader(el)) headers.push(el); });
        var expanded = 0;
        for (var ci = 0; ci < headers.length; ci++) {
            var ae = headers[ci].getAttribute('aria-expanded');
            if (ae === 'true') continue;
            clickEl(headers[ci]); expanded++;
            await new Promise(function(rr) { setTimeout(rr, 400); });
        }

        // 收集所有叶子节点
        var fresh = menu.querySelectorAll('[role="treeitem"]');
        var seen = {}, items = [];
        fresh.forEach(function(el) {
            var rr = el.getBoundingClientRect();
            if (rr.width <= 0 || rr.height <= 0) return;
            if (!isLeaf(el)) return;
            var t = getText(el);
            if (!t || t.length < 2 || seen[t]) return; seen[t] = true;
            items.push({text: t, rect: {x: rr.x, y: rr.y, w: rr.width, h: rr.height}});
        });
        return {found: true, count: items.length, items: items, categoriesExpanded: expanded};
    } catch (e) { return {found: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：在插入菜单内按文本点击控件（先展开分类）
# ═══════════════════════════════════════════════════════════════
_CLICK_MENU_ITEM_JS = r"""
(async () => {
    try {
        var targetText = TARGET_TEXT_PLACEHOLDER;
        function findMenu() {
            var r = document.querySelectorAll('#shell-layer-host-id .ms-List[role="tree"],#shell-layer-host-id [role="tree"]');
            for (var i = 0; i < r.length; i++) { var rr = r[i].getBoundingClientRect(); if (rr.width > 50 && rr.height > 50) return r[i]; }
            var vc = document.querySelector('#shell-layer-host-id [class*="viewContainer"]');
            if (vc) { var rr = vc.getBoundingClientRect(); if (rr.width > 50 && rr.height > 50) return vc; }
            return null;
        }
        function isHeader(el) { return !!el.querySelector('[class*="CategoryLabel"],[class*="categoryLabel"],[class*="category"],[class*="Category"]'); }
        function getText(el) {
            var lb = el.querySelector('.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]');
            return lb ? (lb.textContent || '').trim() : '';
        }
        function clickEl(el) {
            var o = {bubbles: true, cancelable: true, view: window};
            el.focus();
            el.dispatchEvent(new PointerEvent('pointerover', o));
            el.dispatchEvent(new PointerEvent('pointerdown', o));
            el.dispatchEvent(new MouseEvent('mousedown', o));
            el.dispatchEvent(new PointerEvent('pointerup', o));
            el.dispatchEvent(new MouseEvent('mouseup', o));
            el.dispatchEvent(new MouseEvent('click', o));
        }
        var menu = findMenu();
        if (!menu) return {success: false, error: 'menu not visible'};

        var cats = menu.querySelectorAll('[role="treeitem"]');
        var headers = [];
        cats.forEach(function(el) { if (isHeader(el)) headers.push(el); });
        for (var ci = 0; ci < headers.length; ci++) {
            var ae = headers[ci].getAttribute('aria-expanded');
            if (ae === 'false' || ae === null) { clickEl(headers[ci]); await new Promise(function(rr) { setTimeout(rr, 400); }); }
        }

        var fresh = menu.querySelectorAll('[role="treeitem"]');
        var match = null;
        for (var i = 0; i < fresh.length; i++) {
            var t = getText(fresh[i]);
            if (t === targetText || t.indexOf(targetText) >= 0) { match = fresh[i]; break; }
        }
        if (!match) return {success: false, error: 'not found: ' + targetText};
        clickEl(match);
        return {success: true, text: (match.textContent || '').trim().slice(0, 100)};
    } catch (e) { return {success: false, error: 'EX: ' + String(e.message || e)}; }
})()
"""


# ── 公开 API ──────────────────────────────────────────────────


async def click_ribbon_insert(
    session: BrowserSession,
    button_text: str = "插入",
) -> dict:
    """点击顶部 Ribbon 的"插入"按钮（或自定义文本）。

    Args:
        session: BrowserSession
        button_text: 按钮文本，默认 "插入"，也支持 "Insert"

    Returns:
        {success, text, error?}
    """
    js = _CLICK_INSERT_RIBBON_JS.replace("TARGET_TEXT_PLACEHOLDER", json.dumps(button_text))
    raw = await execute_in_studio(session, js)
    if raw.get("exceptionDetails"):
        return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"success": False}


async def get_insert_menu_items(session: BrowserSession) -> dict:
    """展开插入菜单的所有分类并返回所有可见控件选项。

    Args:
        session: BrowserSession

    Returns:
        {found, count, items: [{text, rect}], categoriesExpanded}
    """
    raw = await execute_in_studio(session, _GET_MENU_ITEMS_JS)
    if raw.get("exceptionDetails"):
        return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"found": False}


async def click_insert_menu_item(session: BrowserSession, text: str) -> dict:
    """在插入菜单内按文本点击某个控件（自动展开分类）。

    Args:
        session: BrowserSession
        text: 控件名称，如 "按钮"、"文本输入"、"标签"

    Returns:
        {success, text, error?}
    """
    js = _CLICK_MENU_ITEM_JS.replace("TARGET_TEXT_PLACEHOLDER", json.dumps(text))
    raw = await execute_in_studio(session, js)
    if raw.get("exceptionDetails"):
        return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
    return (raw.get("result") or {}).get("value") or {"success": False}