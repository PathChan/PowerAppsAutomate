"""click_sidebar_tab.py — 点击 PowerApps Studio 左侧栏 tab 按钮。

通用原子操作，可点击左侧垂直工具栏的任意 tab：
  - "树视图" / "Tree view"
  - "插入" / "Insert"
  - "数据" / "Data"
  - "...

通过 tab_label 参数指定要点的按钮文本（支持中英文）。
"""
from __future__ import annotations

import json
import logging

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS：点击左侧栏 tab
# ═══════════════════════════════════════════════════════════════
_CLICK_SIDEBAR_TAB_JS = r"""
(function() {
    var TARGET = TARGET_LABEL_PLACEHOLDER;

    // ── 1. 找左侧栏容器 ──
    // PowerApps Studio 左侧工具栏通常有 role="tablist" 或特定 class
    var sidebar = document.querySelector(
        '[role="tablist"],' +
        '[class*="left-rail"], [class*="LeftRail"],' +
        '[class*="side-bar"], [class*="Sidebar"],' +
        '[class*="toolbar"][class*="vertical"]'
    );

    // ── 2. 遍历找匹配的按钮 ──
    function findButton(container) {
        if (!container) return null;
        // 优先找 aria-label 精确匹配
        var all = container.querySelectorAll(
            'button, [role="tab"], [role="button"], [class*="tab"], a'
        );
        var best = null;
        var bestScore = -1;
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;

            var label = (el.getAttribute('aria-label') || '').trim();
            var text = (el.textContent || '').trim().replace(/\s+/g, ' ');
            var title = (el.getAttribute('title') || '').trim();

            // 评分：完全匹配 > 包含匹配
            var score = 0;
            if (label === TARGET || text === TARGET || title === TARGET) score = 3;
            else if (label.indexOf(TARGET) >= 0 || text.indexOf(TARGET) >= 0 || title.indexOf(TARGET) >= 0) score = 2;

            if (score > bestScore) { bestScore = score; best = el; }
        }
        return best;
    }

    var btn = findButton(sidebar);
    if (!btn) {
        // 兜底：在整个 document 中找
        btn = findButton(document.body);
    }
    if (!btn) return {success: false, error: '未找到左侧栏 tab: ' + TARGET};

    // ── 3. 点击 ──
    var opts = {bubbles: true, cancelable: true, view: window};
    btn.focus();
    btn.dispatchEvent(new PointerEvent('pointerover', opts));
    btn.dispatchEvent(new PointerEvent('pointerdown', opts));
    btn.dispatchEvent(new MouseEvent('mousedown', opts));
    btn.dispatchEvent(new PointerEvent('pointerup', opts));
    btn.dispatchEvent(new MouseEvent('mouseup', opts));
    btn.dispatchEvent(new MouseEvent('click', opts));

    var info = {
        label: (btn.getAttribute('aria-label') || '').trim().slice(0, 80),
        text: (btn.textContent || '').trim().slice(0, 80),
    };
    return {success: true, clicked: info};
})()
"""


async def click_sidebar_tab(
    session: BrowserSession,
    tab_label: str,
) -> dict:
    """点击 PowerApps Studio 左侧栏指定 tab。

    Args:
        session: Browser 会话
        tab_label: tab 标签文本，如 "树视图"、"插入"、"数据"、"Tree view"、"Insert"

    Returns:
        {"success": bool, "clicked"?: dict, "error"?: str}
    """
    try:
        js = _CLICK_SIDEBAR_TAB_JS.replace("TARGET_LABEL_PLACEHOLDER", json.dumps(tab_label, ensure_ascii=False))
        raw = await execute_in_studio(session, js)
        if raw.get("exceptionDetails"):
            return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        result = (raw.get("result") or {}).get("value") or {"success": False}
        if result.get("success"):
            logger.info("左侧栏 tab 点击成功: %s", result.get("clicked", {}).get("label", tab_label))
        else:
            logger.warning("左侧栏 tab 点击失败: %s", result.get("error"))
        return result
    except Exception as e:
        logger.warning("click_sidebar_tab 异常: %s", e)
        return {"success": False, "error": str(e)}