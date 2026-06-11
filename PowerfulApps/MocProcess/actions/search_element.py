"""search_element.py — Tree View 搜索 + 点击控件。

原子操作：
  search_in_tree_view(session, keyword)  — 在搜索框输入关键词，返回过滤后的可见条目
  click_tree_item(session, name)         — 按名称点击 Tree View 中的某个控件节点（选中它）
"""
from __future__ import annotations

import json
import logging

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS：在 Tree View 搜索框中输入关键词
# ═══════════════════════════════════════════════════════════════
_SEARCH_TREE_VIEW_JS = """
(function() {
    var KEYWORD = KEYWORD_PLACEHOLDER;
    var WAIT_MS = 600;

    // ── 1. 找搜索框（已验证的选择器：fui-Input__input type=search）──
    var box = document.querySelector('input.fui-Input__input[type="search"]');
    if (!box) return {error: "Tree View 搜索框未找到 (input.fui-Input__input[type=search])"};

    var boxInfo = {
        type: box.type || '',
        placeholder: box.placeholder || '',
        id: box.id || '',
        cls: (box.className || '').slice(0, 100),
    };

    // ── 2. 聚焦 → 全选 → 删除 → 写入新关键词 ──
    box.focus();
    box.select();                          // 选中已有内容
    document.execCommand('selectAll');     // 兜底全选

    // 先删除已有内容（模拟 Backspace/Delete）
    if (box.value.length > 0) {
        box.value = '';
        box.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
        box.dispatchEvent(new Event('keyup', {bubbles: true, cancelable: true}));
    }

    // 写入新关键词
    box.value = KEYWORD;
    box.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
    box.dispatchEvent(new Event('keyup', {bubbles: true, cancelable: true}));
    box.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));

    // ── 3. 等 PowerApps 原生过滤生效 ──
    setTimeout(function() {
        // ── 4. 读取当前可见的 tree items ──
        var treeSelectors = [
            '[class*="tree-view"]', '[class*="TreeView"]',
            '[role="tree"]', '[class*="outline"]',
        ];
        var treeEl = null;
        for (var si = 0; si < treeSelectors.length; si++) {
            treeEl = document.querySelector(treeSelectors[si]);
            if (treeEl) break;
        }
        if (!treeEl) {
            var allDivs = document.querySelectorAll('div[class*="tree"]');
            for (var di = 0; di < allDivs.length; di++) {
                if (allDivs[di].textContent.indexOf('Screen') > -1 || allDivs[di].textContent.indexOf('App') > -1) {
                    treeEl = allDivs[di];
                    break;
                }
            }
        }

        var visibleItems = [];
        if (treeEl) {
            var items = treeEl.querySelectorAll('[role="treeitem"], li, [class*="tree-item"]');
            for (var ii = 0; ii < items.length; ii++) {
                var style = window.getComputedStyle(items[ii]);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                var rect = items[ii].getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;

                var aria = items[ii].getAttribute('aria-label');
                var name = '';
                if (aria) {
                    name = aria.trim();
                } else {
                    name = (items[ii].textContent || '').trim().slice(0, 80);
                }
                if (!name) continue;
                visibleItems.push({name: name});
            }
        }

        // 把结果存到全局，供同步返回
        window.__searchResult = {
            keyword: KEYWORD,
            found_count: visibleItems.length,
            items: visibleItems,
            input_info: boxInfo,
        };
    }, WAIT_MS);

    return {searching: true, keyword: KEYWORD};
})();
"""

_GET_SEARCH_RESULT_JS = """
(function() {
    var r = window.__searchResult || null;
    return r;
})();
"""


async def search_in_tree_view(
    session: BrowserSession,
    keyword: str,
) -> dict:
    """在 Tree View 搜索框输入关键词，让 PowerApps 原生过滤。

    流程：
      1. 找到搜索框（input.fui-Input__input[type="search"]）
      2. 输入关键词并派发事件
      3. 等待 600ms 让原生过滤生效
      4. 读取当前可见的 tree items
      5. **不清空搜索框**，保留搜索状态让你能看到效果

    Args:
        session: Browser 会话
        keyword: 搜索关键词，如 "Button"、"Text"、"Screen"

    Returns:
        {"keyword": str, "found_count": int, "items": list[{"name": str}], "input_info": dict, "error"?: str}
    """
    try:
        # 第一步：输入关键词
        js = _SEARCH_TREE_VIEW_JS.replace("KEYWORD_PLACEHOLDER", json.dumps(keyword, ensure_ascii=False))
        result = await execute_in_studio(session, js)
        if result.get("exceptionDetails"):
            return {"error": result["exceptionDetails"].get("text", "unknown")}

        # 第二步：等待并获取结果
        import asyncio
        await asyncio.sleep(0.7)  # 等 setTimeout 执行完

        result2 = await execute_in_studio(session, _GET_SEARCH_RESULT_JS)
        data = (result2.get("result") or {}).get("value") or {}

        if data.get("error"):
            logger.warning("Tree View 搜索失败: %s", data["error"])
            return data

        found = data.get("found_count", 0)
        logger.info("Tree View 搜索 '%s' → %d 个匹配", keyword, found)
        return data

    except Exception as e:
        logger.warning("Tree View 搜索异常: %s", e)
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# JS：按名称点击 Tree View 中的控件节点
# ═══════════════════════════════════════════════════════════════
_CLICK_TREE_ITEM_JS = r"""
(function() {
    var TARGET = TARGET_NAME_PLACEHOLDER;

    // ── 1. 找 Tree View 容器 ──
    var treeEl = document.querySelector(
        '[class*="tree-view"], [class*="TreeView"], ' +
        '[role="tree"], [class*="outline"]'
    );
    if (!treeEl) {
        var allDivs = document.querySelectorAll('div[class*="tree"]');
        for (var di = 0; di < allDivs.length; di++) {
            if (allDivs[di].textContent.indexOf('Screen') > -1 || allDivs[di].textContent.indexOf('App') > -1) {
                treeEl = allDivs[di]; break;
            }
        }
    }
    if (!treeEl) return {success: false, error: 'Tree View 容器未找到'};

    // ── 2. 找匹配的 tree item ──
    var items = treeEl.querySelectorAll('[role="treeitem"], li, [class*="tree-item"]');
    var match = null;
    for (var ii = 0; ii < items.length; ii++) {
        var style = window.getComputedStyle(items[ii]);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        var rect = items[ii].getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;

        // 提取名称
        var aria = items[ii].getAttribute('aria-label');
        var name = '';
        if (aria) {
            name = aria.trim();
        } else {
            name = (items[ii].textContent || '').trim().slice(0, 80);
        }
        if (name === TARGET || name.indexOf(TARGET) >= 0) {
            match = items[ii];
            break;
        }
        // 也检查 data 属性
        var dataName = items[ii].getAttribute('data-name') || items[ii].getAttribute('data-id') || '';
        if (dataName === TARGET) {
            match = items[ii]; break;
        }
    }
    if (!match) return {success: false, error: '未找到匹配的控件: ' + TARGET};

    // ── 3. 点击（先滚动到可见）──
    match.scrollIntoView({block: 'nearest'});
    var opts = {bubbles: true, cancelable: true, view: window};
    match.dispatchEvent(new PointerEvent('pointerover', opts));
    match.dispatchEvent(new PointerEvent('pointerdown', opts));
    match.dispatchEvent(new MouseEvent('mousedown', opts));
    match.dispatchEvent(new PointerEvent('pointerup', opts));
    match.dispatchEvent(new MouseEvent('mouseup', opts));
    match.dispatchEvent(new MouseEvent('click', opts));

    var clickedInfo = {
        name: (aria || (match.textContent || '').trim()).slice(0, 80),
        tag: match.tagName || '',
    };
    return {success: true, clicked: clickedInfo};
})()
"""


async def click_tree_item(
    session: BrowserSession,
    name: str,
) -> dict:
    """在 Tree View 中按名称点击某个控件节点（选中它）。

    通常在 search_in_tree_view 之后调用，搜索结果过滤后，
    用此函数点击目标控件将其选中。

    Args:
        session: Browser 会话
        name: 控件名称，如 "Button1"、"TextInput1"、"Screen1"

    Returns:
        {"success": bool, "clicked"?: dict, "error"?: str}
    """
    try:
        js = _CLICK_TREE_ITEM_JS.replace("TARGET_NAME_PLACEHOLDER", json.dumps(name, ensure_ascii=False))
        raw = await execute_in_studio(session, js)
        if raw.get("exceptionDetails"):
            return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        result = (raw.get("result") or {}).get("value") or {"success": False}
        if result.get("success"):
            logger.info("Tree View 点击控件 '%s' 成功", name)
        else:
            logger.warning("Tree View 点击控件 '%s' 失败: %s", name, result.get("error"))
        return result
    except Exception as e:
        logger.warning("click_tree_item 异常: %s", e)
        return {"success": False, "error": str(e)}