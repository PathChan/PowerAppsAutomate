"""experience/engine.py：DOM 探索引擎 + 点击重放引擎。

核心能力
--------
1. explore_area(css_scope) → 扫描区域内所有可交互元素，提取特征
2. learn_from_area(css_scope, area_hint) → 探索并存入经验库
3. replay(key_or_query) → 根据经验找到元素并点击
4. verify_click(key) → 点击后验证是否成功
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from mocProcessing.browser import BrowserSession
from mocProcessing.tools.experience.db import (
    FEATURE_WEIGHTS,
    ExperienceDB,
    ElementExperience,
    _best_selector,
)
from mocProcessing.tools.powerapps_chain import execute_in_studio

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# JS：在 Studio iframe 内扫描指定 CSS scope 下所有可交互元素
# ═══════════════════════════════════════════════════════════════
EXPLORE_AREA_JS = r"""
((cssScope) => {
    const container = cssScope ? document.querySelector(cssScope) : document.body;
    if (!container) return {found: false, error: 'scope not found: ' + cssScope};

    // 找出所有可交互元素
    const interactiveSelector = [
        'button', 'select', 'textarea',
        'input:not([type="hidden"]):not([type="password"])',
        '[role="button"]', '[role="combobox"]', '[role="listbox"]',
        '[role="option"]', '[role="tab"]', '[role="menuitem"]',
        '[role="link"]', '[role="checkbox"]', '[role="radio"]',
        '[role="switch"]', '[role="textbox"]',
        '[contenteditable="true"]',
        'a[href]',
        '[onclick]', '[data-action]',
    ].join(',');

    const elements = container.querySelectorAll(interactiveSelector);
    const results = [];

    elements.forEach((el, idx) => {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;  // 隐藏的跳过

        // 构建 DOM chain（从容器往下到元素）
        const chain = [];
        let cur = el;
        while (cur && cur !== container && cur !== document.body) {
            let desc = cur.tagName.toLowerCase();
            if (cur.id) desc += '#' + cur.id;
            else {
                const parent = cur.parentElement;
                if (parent) {
                    const siblings = Array.from(parent.children).filter(
                        s => s.tagName === cur.tagName
                    );
                    if (siblings.length > 1) {
                        const nth = siblings.indexOf(cur) + 1;
                        desc += ':nth-of-type(' + nth + ')';
                    }
                }
            }
            chain.unshift(desc);
            cur = cur.parentElement;
        }

        const classes = Array.from(el.classList).filter(c => c.length > 0);

        results.push({
            index: idx,
            tag: el.tagName,
            id: el.id || '',
            classes: classes,
            text: (el.textContent || '').trim().slice(0, 200),
            ariaLabel: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || '',
            ariaHasPopup: el.getAttribute('aria-haspopup') || '',
            ariaExpanded: el.getAttribute('aria-expanded') || '',
            ariaSelected: el.getAttribute('aria-selected') || '',
            dataAutomationId: el.getAttribute('data-automationid') || '',
            dataControlName: el.getAttribute('data-control-name') || '',
            dataUxName: el.getAttribute('data-ux-name') || '',
            placeholder: el.getAttribute('placeholder') || '',
            title: el.getAttribute('title') || '',
            value: el.value || '',
            type: el.type || '',
            disabled: el.disabled || false,
            domChain: chain,
            rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height},
        });
    });

    return {
        found: true,
        scope: cssScope || 'body',
        count: results.length,
        elements: results,
        containerInfo: {
            tag: container.tagName,
            id: container.id || '',
            classes: (container.className || '').slice(0, 200),
        },
    };
})
"""

# ═══════════════════════════════════════════════════════════════
# JS：点击元素（dispatchEvent 完整事件链）
# ═══════════════════════════════════════════════════════════════
CLICK_ELEMENT_JS = r"""
((selector) => {
    try {
        const el = document.querySelector(selector);
        if (!el) return {success: false, error: 'selector not found: ' + selector};

        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return {success: false, error: 'element hidden'};

        const opts = {bubbles: true, cancelable: true, view: window};
        el.dispatchEvent(new PointerEvent('pointerover', opts));
        el.dispatchEvent(new PointerEvent('pointerdown', opts));
        el.dispatchEvent(new MouseEvent('mousedown', opts));
        el.click();
        el.dispatchEvent(new PointerEvent('pointerup', opts));
        el.dispatchEvent(new MouseEvent('mouseup', opts));

        return {
            success: true,
            tag: el.tagName,
            text: (el.textContent || '').trim().slice(0, 100),
            rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height},
        };
    } catch (e) {
        return {success: false, error: 'JS exception: ' + (e.message || e)};
    }
})
"""

# ═══════════════════════════════════════════════════════════════
# JS：查找元素（返回所有匹配特征）
# ═══════════════════════════════════════════════════════════════
LOCATE_BY_FEATURES_JS = r"""
((features) => {
    // features: 只包含跨环境稳定特征
    // {tag, id, text, ariaLabel, dataAutomationId, dataControlName, role}
    try {
        const f = features;
        let candidates = [];

        // 1) data-automationid 精确匹配（最稳定）
        if (f.dataAutomationId) {
            const el = document.querySelector('[data-automationid="' + f.dataAutomationId + '"]');
            if (el) candidates.push(el);
        }
        if (candidates.length === 0 && f.dataControlName) {
            const el = document.querySelector('[data-control-name="' + f.dataControlName + '"]');
            if (el) candidates.push(el);
        }

        // 2) id 精确匹配
        if (candidates.length === 0 && f.id) {
            const el = document.getElementById(f.id);
            if (el) candidates.push(el);
        }

        // 3) aria-label 精确匹配
        if (candidates.length === 0 && f.ariaLabel) {
            const els = document.querySelectorAll('[aria-label="' + f.ariaLabel + '"]');
            els.forEach(el => candidates.push(el));
        }

        // 4) 文本 + tag 匹配
        if (candidates.length === 0 && f.text) {
            const tag = f.tag || '*';
            const all = document.querySelectorAll(tag);
            for (const el of all) {
                const t = (el.textContent || '').trim();
                if (t === f.text) { candidates.push(el); break; }
            }
            if (candidates.length === 0) {
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    if (t.startsWith(f.text) || f.text.startsWith(t)) { candidates.push(el); break; }
                }
            }
        }

        // 5) role + tag
        if (candidates.length === 0 && f.role) {
            const tag = f.tag || '*';
            const sel = tag + '[role="' + f.role + '"]';
            const els = document.querySelectorAll(sel);
            els.forEach(el => candidates.push(el));
        }

        // 过滤可见的
        for (const el of candidates) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {
                let bestSel = '';
                const aid = el.getAttribute('data-automationid');
                if (aid) bestSel = '[data-automationid="' + aid + '"]';
                else if (el.id) bestSel = '#' + el.id;
                else if (f.text) bestSel = tag + ':contains("' + f.text + '")';
                return {
                    found: true,
                    tag: el.tagName,
                    id: el.id || '',
                    text: (el.textContent || '').trim().slice(0, 100),
                    ariaLabel: el.getAttribute('aria-label') || '',
                    dataAutomationId: el.getAttribute('data-automationid') || '',
                    selector: bestSel,
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                };
            }
        }

        return {found: false, error: 'no matching visible element'};
    } catch (e) {
        return {found: false, error: 'JS exception: ' + (e.message || e)};
    }
})
"""

# ═══════════════════════════════════════════════════════════════
# JS：打开下拉框并通过滚动获取所有选项
# ═══════════════════════════════════════════════════════════════
PROBE_GET_ALL_OPTIONS_JS = r"""
(async () => {
    try {
        var container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false, error: 'no container'};

        // 收集所有 role=option 元素
        function collectOptions() {
            var els = document.querySelectorAll(
                '[role="option"],.ms-Dropdown-item,[class*="dropdown"] li,' +
                '[class*="menu"] [role="menuitem"],.ms-ContextualMenu-item'
            );
            var out = [];
            els.forEach(function(o) {
                var r = o.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                var txt = (o.textContent || '').trim().slice(0, 120);
                // 去重
                for (var i = 0; i < out.length; i++) {
                    if (out[i].text === txt) return;
                }
                out.push({
                    text: txt,
                    tag: o.tagName,
                    role: o.getAttribute('role') || '',
                    selected: o.getAttribute('aria-selected') || '',
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                });
            });
            return out;
        }

        // 找下拉菜单的滚动容器
        function findScrollContainer() {
            var selectors = [
                '[role="listbox"]',
                '.ms-Dropdown-items',
                '[class*="dropdown"] [class*="menu"]',
                '.ms-Callout-main',
                '[class*="Callout"] [class*="main"]',
                '.ms-ContextualMenu-list',
                '.ms-ContextualMenu [class*="list"]',
                '[class*="menuContainer"]',
                '[class*="MenuContainer"]',
                '[class*="scroll"]',
            ];
            for (var i = 0; i < selectors.length; i++) {
                var el = document.querySelector(selectors[i]);
                if (el) {
                    var r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return el;
                }
            }
            // fallback: 找第一个 option 的滚动父容器
            var firstOpt = document.querySelector('[role="option"],.ms-Dropdown-item,.ms-ContextualMenu-item');
            if (firstOpt) {
                var p = firstOpt.parentElement;
                while (p) {
                    var pr = p.getBoundingClientRect();
                    if (pr.width > 0 && pr.height > 0 && p.scrollHeight > p.clientHeight + 10) {
                        return p;
                    }
                    p = p.parentElement;
                }
            }
            return null;
        }

        // 打开下拉
        function openDropdown() {
            var candidates = container.querySelectorAll('button,[role="combobox"],[role="listbox"],select');
            var trig = null;
            for (var i = 0; i < candidates.length; i++) {
                var r = candidates[i].getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { trig = candidates[i]; break; }
            }
            if (!trig) return null;
            var opts = {bubbles: true, cancelable: true, view: window};
            trig.dispatchEvent(new PointerEvent('pointerover', opts));
            trig.dispatchEvent(new PointerEvent('pointerdown', opts));
            trig.dispatchEvent(new MouseEvent('mousedown', opts));
            trig.click();
            trig.dispatchEvent(new PointerEvent('pointerup', opts));
            trig.dispatchEvent(new MouseEvent('mouseup', opts));
            return trig;
        }

        // 1) 先看是否已打开
        var allOpts = collectOptions();
        if (allOpts.length === 0) {
            var t = openDropdown();
            if (!t) return {found: false, error: 'no trigger'};
            await new Promise(function(rr) { setTimeout(rr, 1000); });
            allOpts = collectOptions();
            if (allOpts.length === 0) return {found: false, error: 'dropdown empty'};
        }

        // 2) 滚动收集所有选项
        var scrollContainer = findScrollContainer();
        if (scrollContainer) {
            var prevCount = 0;
            for (var scrollAttempt = 0; scrollAttempt < 20; scrollAttempt++) {
                scrollContainer.scrollTop += 300;
                await new Promise(function(rr) { setTimeout(rr, 300); });
                var fresh = collectOptions();
                if (fresh.length > prevCount) {
                    allOpts = fresh;
                    prevCount = fresh.length;
                } else {
                    break; // 滚不动了
                }
            }
            // 滚回顶部
            scrollContainer.scrollTop = 0;
        }

        return {found: true, optionsCount: allOpts.length, options: allOpts};
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：选择第 N 个选项 → 等待面板刷新 → 收集属性编辑器内容
# ═══════════════════════════════════════════════════════════════
PROBE_SELECT_OPTION_JS = r"""
(async () => {
    try {
        var container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false, error: 'no container'};

        // 找选项
        function findOptionByIndex(idx) {
            var els = document.querySelectorAll(
                '[role="option"],.ms-Dropdown-item,[class*="dropdown"] li,' +
                '[class*="menu"] [role="menuitem"],.ms-ContextualMenu-item'
            );
            var vis = [];
            els.forEach(function(o) {
                var r = o.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) vis.push(o);
            });
            if (idx < vis.length) return vis[idx];
            return null;
        }

        // 如果下拉没开，打开它
        var target = findOptionByIndex(0);
        if (!target) {
            var candidates = container.querySelectorAll('button,[role="combobox"],[role="listbox"],select');
            var trig = null;
            for (var i = 0; i < candidates.length; i++) {
                var r = candidates[i].getBoundingClientRect();
                if (r.width > 0 && r.height > 0) { trig = candidates[i]; break; }
            }
            if (!trig) return {found: false, error: 'no trigger'};
            var opts = {bubbles: true, cancelable: true, view: window};
            trig.dispatchEvent(new PointerEvent('pointerover', opts));
            trig.dispatchEvent(new PointerEvent('pointerdown', opts));
            trig.dispatchEvent(new MouseEvent('mousedown', opts));
            trig.click();
            trig.dispatchEvent(new PointerEvent('pointerup', opts));
            trig.dispatchEvent(new MouseEvent('mouseup', opts));
            await new Promise(function(rr) { setTimeout(rr, 1000); });
            target = findOptionByIndex(INDEX_PLACEHOLDER);
            if (!target) return {found: false, error: 'option ' + INDEX_PLACEHOLDER + ' not found after open'};
        }

        var idx = INDEX_PLACEHOLDER;
        target = findOptionByIndex(idx);
        if (!target) return {found: false, error: 'option ' + idx + ' out of range'};

        var targetText = (target.textContent || '').trim().slice(0, 120);
        var targetRect = target.getBoundingClientRect();

        var opts = {bubbles: true, cancelable: true, view: window};
        target.dispatchEvent(new PointerEvent('pointerover', opts));
        target.dispatchEvent(new PointerEvent('pointerdown', opts));
        target.dispatchEvent(new MouseEvent('mousedown', opts));
        target.click();
        target.dispatchEvent(new PointerEvent('pointerup', opts));
        target.dispatchEvent(new MouseEvent('mouseup', opts));
        await new Promise(function(rr) { setTimeout(rr, 1000); });

        // ── 收集 ─────────────────────────────────────
        var panelData = {};
        var formulaInput = container.querySelector('input,textarea,[role="textbox"],[contenteditable="true"]');
        if (formulaInput) panelData.formulaValue = formulaInput.value || formulaInput.textContent || '';

        var inputs = document.querySelectorAll(
            '.property-pane input,.property-editor input,' +
            '[class*="property"] input:not([type="hidden"]):not([type="password"]),' +
            '.property-pane textarea,.property-editor textarea'
        );
        var inputList = [];
        inputs.forEach(function(inp) {
            var r3 = inp.getBoundingClientRect();
            if (r3.width <= 0 || r3.height <= 0) return;
            inputList.push({
                placeholder: inp.placeholder || '',
                ariaLabel: inp.getAttribute('aria-label') || '',
                value: (inp.value || '').slice(0, 200),
                dataAutomationId: inp.getAttribute('data-automationid') || '',
                rect: {x: r3.x, y: r3.y, w: r3.width, h: r3.height},
            });
        });
        panelData.propertyInputs = inputList;

        var labels = document.querySelectorAll(
            '.property-pane label,.property-editor label,' +
            '[class*="property"] label,[class*="editor"] label,' +
            '.property-pane [class*="label"],.property-editor [class*="label"]'
        );
        var labelList = [];
        labels.forEach(function(lbl) {
            var text = (lbl.textContent || '').trim();
            if (!text) return;
            labelList.push(text.slice(0, 100));
        });
        panelData.propertyLabels = labelList;

        var buttons = document.querySelectorAll(
            '.property-pane button,.property-editor button,' +
            '[class*="property"] button,[class*="editor"] button'
        );
        var btnList = [];
        buttons.forEach(function(btn) {
            var r4 = btn.getBoundingClientRect();
            if (r4.width <= 0 || r4.height <= 0) return;
            btnList.push({
                text: (btn.textContent || '').trim().slice(0, 80),
                ariaLabel: btn.getAttribute('aria-label') || '',
                role: btn.getAttribute('role') || '',
                rect: {x: r4.x, y: r4.y, w: r4.width, h: r4.height},
            });
        });
        panelData.propertyButtons = btnList;

        return {
            found: true,
            index: idx,
            optionText: targetText,
            optionRect: {x: targetRect.x, y: targetRect.y, w: targetRect.width, h: targetRect.height},
            panelData: panelData,
        };
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：获取属性面板侧边栏 tab 列表
# ═══════════════════════════════════════════════════════════════
PROBE_GET_SIDEBAR_TABS_JS = r"""
(function() {
    try {
        var tabs = document.querySelectorAll(
            '[role="tab"],.property-pane [role="tab"],' +
            '[class*="pivot"] button,[class*="Pivot"] button,' +
            '.ms-Pivot-link,[class*="pivot"] [class*="link"],' +
            '.property-pane [class*="tab"],.property-editor [class*="tab"]'
        );
        var result = [];
        tabs.forEach(function(t) {
            var r = t.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            result.push({
                text: (t.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
                tag: t.tagName,
                role: t.getAttribute('role') || '',
                ariaSelected: t.getAttribute('aria-selected') || '',
                dataAutomationId: t.getAttribute('data-automationid') || '',
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
            });
        });
        return {found: result.length > 0, count: result.length, tabs: result};
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：点击侧边栏 tab（含 focus）+ 收集该 tab 下的所有输入框/标签
# ═══════════════════════════════════════════════════════════════
PROBE_CLICK_TAB_JS = r"""
(function() {
    try {
        var tabs = document.querySelectorAll(
            '[role="tab"],.property-pane [role="tab"],' +
            '[class*="pivot"] button,[class*="Pivot"] button,' +
            '.ms-Pivot-link,[class*="pivot"] [class*="link"],' +
            '.property-pane [class*="tab"],.property-editor [class*="tab"]'
        );
        var idx = INDEX_PLACEHOLDER;
        var vis = [];
        tabs.forEach(function(t) {
            var r = t.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) vis.push(t);
        });
        if (idx >= vis.length) return {found: false, error: 'tab ' + idx + ' out of range'};

        var target = vis[idx];
        var tabText = (target.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
        var tabRect = target.getBoundingClientRect();

        // Fabric UI tab 需要 focus + 完整事件链
        target.focus();
        var opts = {bubbles: true, cancelable: true, view: window};
        target.dispatchEvent(new PointerEvent('pointerover', opts));
        target.dispatchEvent(new PointerEvent('pointerdown', opts));
        target.dispatchEvent(new MouseEvent('mousedown', opts));
        target.dispatchEvent(new PointerEvent('pointerup', opts));
        target.dispatchEvent(new MouseEvent('mouseup', opts));
        target.dispatchEvent(new MouseEvent('click', opts));

        // 收集该 tab 下的所有 input/label
        var inputs = document.querySelectorAll(
            '.property-pane input,.property-editor input,' +
            '[class*="property"] input:not([type="hidden"]):not([type="password"]),' +
            '.property-pane textarea,.property-editor textarea'
        );
        var inputList = [];
        inputs.forEach(function(inp) {
            var r3 = inp.getBoundingClientRect();
            if (r3.width <= 0 || r3.height <= 0) return;
            inputList.push({
                placeholder: inp.placeholder || '',
                ariaLabel: inp.getAttribute('aria-label') || '',
                value: (inp.value || '').slice(0, 200),
                dataAutomationId: inp.getAttribute('data-automationid') || '',
                rect: {x: r3.x, y: r3.y, w: r3.width, h: r3.height},
            });
        });

        var labels = document.querySelectorAll(
            '.property-pane label,.property-editor label,' +
            '[class*="property"] label,[class*="editor"] label'
        );
        var labelList = [];
        labels.forEach(function(lbl) {
            var t2 = (lbl.textContent || '').trim();
            if (!t2) return;
            labelList.push(t2.slice(0, 100));
        });

        return {
            found: true,
            index: idx,
            tabText: tabText,
            tabRect: {x: tabRect.x, y: tabRect.y, w: tabRect.width, h: tabRect.height},
            panelData: {propertyInputs: inputList, propertyLabels: labelList}
        };
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：找出所有当前可见的可交互元素（用于动态遍历）
# ═══════════════════════════════════════════════════════════════
PROBE_FIND_ALL_CLICKABLE_JS = r"""
(function() {
    try {
        var sel = 'button,[role="button"],[role="combobox"],[role="listbox"],' +
            '[role="option"],[role="tab"],[role="menuitem"],[role="menuitemcheckbox"],' +
            '[role="radio"],[role="link"],[role="switch"],[role="checkbox"],' +
            'select,input:not([type="hidden"]):not([type="password"]),' +
            'textarea,a[href],[contenteditable="true"]';
        var all = document.querySelectorAll(sel);
        var out = [];
        all.forEach(function(el, idx) {
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            var text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 100);
            var ariaLabel = el.getAttribute('aria-label') || '';
            out.push({
                index: idx,
                tag: el.tagName,
                id: el.id || '',
                text: text,
                ariaLabel: ariaLabel,
                role: el.getAttribute('role') || '',
                dataAutomationId: el.getAttribute('data-automationid') || '',
                dataControlName: el.getAttribute('data-control-name') || '',
                ariaHasPopup: el.getAttribute('aria-haspopup') || '',
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
            });
        });
        return {found: out.length > 0, count: out.length, elements: out};
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：通过 data-automationid 或文本模糊找到元素并点击
# ═══════════════════════════════════════════════════════════════
PROBE_CLICK_BY_TEXT_JS = r"""
(function() {
    try {
        var targetText = TARGET_TEXT_PLACEHOLDER;
        var dataAid = DATA_AID_PLACEHOLDER;
        var all = document.querySelectorAll(
            'button,[role="button"],[role="combobox"],[role="listbox"],' +
            '[role="tab"],[role="menuitem"],[role="menuitemcheckbox"],' +
            '[role="radio"],[role="link"],select,' +
            'input:not([type="hidden"]):not([type="password"]),textarea'
        );
        var match = null;
        var bestDist = Infinity;
        var cx = window.innerWidth / 2;
        var cy = window.innerHeight / 2;
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            if (dataAid && el.getAttribute('data-automationid') === dataAid) { match = el; break; }
            var text = (el.textContent || '').trim();
            var label = (el.getAttribute('aria-label') || '').trim();
            if (targetText && (text === targetText || label === targetText || text.indexOf(targetText) >= 0 || label.indexOf(targetText) >= 0)) {
                // 优先选靠近 viewport 中心的（避免点到错误区域的重名元素）
                var ecx = r.x + r.width / 2;
                var ecy = r.y + r.height / 2;
                var dist = Math.sqrt((ecx - cx) * (ecx - cx) + (ecy - cy) * (ecy - cy));
                if (dist < bestDist) {
                    bestDist = dist;
                    match = el;
                }
            }
        }
        if (!match) return {success: false, error: 'not found: ' + targetText};
        var mr = match.getBoundingClientRect();
        match.focus();
        var opts = {bubbles: true, cancelable: true, view: window};
        match.dispatchEvent(new PointerEvent('pointerover', opts));
        match.dispatchEvent(new PointerEvent('pointerdown', opts));
        match.dispatchEvent(new MouseEvent('mousedown', opts));
        match.dispatchEvent(new PointerEvent('pointerup', opts));
        match.dispatchEvent(new MouseEvent('mouseup', opts));
        match.dispatchEvent(new MouseEvent('click', opts));
        return {
            success: true,
            tag: match.tagName,
            text: (match.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 100),
            ariaLabel: match.getAttribute('aria-label') || '',
            dataAutomationId: match.getAttribute('data-automationid') || '',
            rect: {x: mr.x, y: mr.y, w: mr.width, h: mr.height},
        };
    } catch (e) {
        return {success: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：在插入下拉菜单内查找所有可点击的控件模板
# ═══════════════════════════════════════════════════════════════
PROBE_GET_INSERT_MENU_ITEMS_JS = r"""
(async () => {
    try {
        function findInsertMenu() {
            var primary = document.querySelectorAll(
                '#shell-layer-host-id .ms-List[role="tree"],' +
                '#shell-layer-host-id [role="tree"]'
            );
            for (var i = 0; i < primary.length; i++) {
                var r = primary[i].getBoundingClientRect();
                if (r.width > 50 && r.height > 50) return primary[i];
            }
            var vc = document.querySelector('#shell-layer-host-id [class*="viewContainer"]');
            if (vc) {
                var r = vc.getBoundingClientRect();
                if (r.width > 50 && r.height > 50) return vc;
            }
            return null;
        }

        function isCategoryHeader(el) {
            return !!el.querySelector(
                '[class*="CategoryLabel"],[class*="categoryLabel"],' +
                '[class*="category"],[class*="Category"]'
            );
        }

        function isLeafItem(el) {
            return !!el.querySelector(
                '.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]'
            ) && !isCategoryHeader(el);
        }

        function getItemText(el) {
            var labelEl = el.querySelector('.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]');
            return labelEl ? (labelEl.textContent || '').trim() : '';
        }

        function clickElement(el) {
            var opts = {bubbles: true, cancelable: true, view: window};
            el.focus();
            el.dispatchEvent(new PointerEvent('pointerover', opts));
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
        }

        var menu = findInsertMenu();
        if (!menu) return {found: false, error: 'insert menu not visible'};

        // 1) 找出所有分类（可展开的 treeitem）
        var allTreeItems = menu.querySelectorAll('[role="treeitem"]');
        var categoryItems = [];
        allTreeItems.forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            if (isCategoryHeader(el)) categoryItems.push(el);
        });

        // 2) 展开所有折叠的分类
        var expandedCount = 0;
        for (var ci = 0; ci < categoryItems.length; ci++) {
            var cat = categoryItems[ci];
            var ariaExpanded = cat.getAttribute('aria-expanded');
            // 如果已展开或没有 expand 属性则跳过
            if (ariaExpanded === 'true') continue;
            if (ariaExpanded === 'false' || ariaExpanded === null) {
                clickElement(cat);
                expandedCount++;
                await new Promise(function(rr) { setTimeout(rr, 500); });
            }
        }

        // 3) 重新收集所有可见的叶子 treeitem
        var freshItems = menu.querySelectorAll('[role="treeitem"]');
        var seen = {};
        var out = [];
        freshItems.forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            if (!isLeafItem(el)) return;
            var text = getItemText(el);
            if (!text || text.length < 2) return;
            if (seen[text]) return; seen[text] = true;
            out.push({
                text: text,
                tag: el.tagName,
                role: el.getAttribute('role') || '',
                ariaLabel: (el.getAttribute('aria-label') || '').trim(),
                dataAutomationId: el.getAttribute('data-automationid') || '',
                rect: {x: r.x, y: r.y, w: r.width, h: r.height},
            });
        });

        return {
            found: out.length > 0,
            count: out.length,
            items: out,
            categoriesExpanded: expandedCount,
        };
    } catch (e) {
        return {found: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# JS：在插入下拉菜单内按文本点击控件（先展开分类再点）
# ═══════════════════════════════════════════════════════════════
CLICK_INSERT_MENU_ITEM_JS = r"""
(async () => {
    try {
        var targetText = TARGET_TEXT_PLACEHOLDER;

        function findInsertMenu() {
            var primary = document.querySelectorAll(
                '#shell-layer-host-id .ms-List[role="tree"],' +
                '#shell-layer-host-id [role="tree"]'
            );
            for (var i = 0; i < primary.length; i++) {
                var r = primary[i].getBoundingClientRect();
                if (r.width > 50 && r.height > 50) return primary[i];
            }
            var vc = document.querySelector('#shell-layer-host-id [class*="viewContainer"]');
            if (vc) {
                var r = vc.getBoundingClientRect();
                if (r.width > 50 && r.height > 50) return vc;
            }
            return null;
        }

        function isCategoryHeader(el) {
            return !!el.querySelector(
                '[class*="CategoryLabel"],[class*="categoryLabel"],' +
                '[class*="category"],[class*="Category"]'
            );
        }

        function getItemText(el) {
            var labelEl = el.querySelector('.ms-Label,label,[class*="itemLabel"],[class*="ItemLabel"]');
            return labelEl ? (labelEl.textContent || '').trim() : '';
        }

        function clickElement(el) {
            var opts = {bubbles: true, cancelable: true, view: window};
            el.focus();
            el.dispatchEvent(new PointerEvent('pointerover', opts));
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
        }

        var menu = findInsertMenu();
        if (!menu) return {success: false, error: 'insert menu not visible'};

        // 1) 先展开所有折叠分类，确保目标可见
        var allItems = menu.querySelectorAll('[role="treeitem"]');
        var catItems = [];
        allItems.forEach(function(el) {
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            if (isCategoryHeader(el)) catItems.push(el);
        });
        for (var ci = 0; ci < catItems.length; ci++) {
            var cat = catItems[ci];
            var ae = cat.getAttribute('aria-expanded');
            if (ae === 'false' || ae === null) {
                clickElement(cat);
                await new Promise(function(rr) { setTimeout(rr, 400); });
            }
        }

        // 2) 找到目标 treeitem
        var freshItems = menu.querySelectorAll('[role="treeitem"]');
        var match = null;
        for (var i = 0; i < freshItems.length; i++) {
            var el = freshItems[i];
            var r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            var text = getItemText(el);
            if (text === targetText || text.indexOf(targetText) >= 0) {
                match = el; break;
            }
        }
        if (!match) return {success: false, error: 'item not found in insert menu: ' + targetText};

        // 3) 点击匹配到的 treeitem
        clickElement(match);
        return {
            success: true,
            text: getItemText(match) || match.textContent.trim().slice(0, 100),
            rect: (function(){ var mr = match.getBoundingClientRect(); return {x: mr.x, y: mr.y, w: mr.width, h: mr.height}; })(),
        };
    } catch (e) {
        return {success: false, error: 'EX: ' + String(e.message || e)};
    }
})()
"""


class ExperienceEngine:
    """经验引擎：探索 DOM + 学习 + 重放点击。"""

    def __init__(
        self,
        browser_session: BrowserSession,
        db: ExperienceDB | None = None,
    ) -> None:
        self.session = browser_session
        self.db = db or ExperienceDB()

    # ── 探索 ──────────────────────────────────────────────

    async def explore_area(self, css_scope: str = "") -> dict[str, Any]:
        """在 Studio iframe 内扫描指定 CSS scope 下的所有可见可交互元素。

        Args:
            css_scope: CSS 选择器限定范围，如 "#formulaBarContainer"、".property-pane"。
                       空字符串 = document.body。

        Returns:
            {found, count, elements: [{tag, id, classes, text, ...}], ...}
        """
        js = f"({EXPLORE_AREA_JS})({json.dumps(css_scope)})" if css_scope else f"({EXPLORE_AREA_JS})(null)"
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            return {
                "found": False,
                "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"])),
            }
        return (raw.get("result") or {}).get("value") or {"found": False}

    async def learn_from_area(
        self,
        css_scope: str,
        area_hint: str = "",
        prefix: str = "",
    ) -> list[ElementExperience]:
        """探索一个区域并存入经验库。

        Args:
            css_scope: CSS 选择器限定范围。
            area_hint: 区域标记，如 "formulaBar", "ribbon", "propertyPanel"。
            prefix: key 前缀，如 "formulaBar."。

        Returns:
            新创建 / 更新的经验列表。
        """
        data = await self.explore_area(css_scope)
        if not data.get("found"):
            logger.warning("explore_area failed for %s: %s", css_scope, data.get("error"))
            return []

        elements = data.get("elements", [])
        learned: list[ElementExperience] = []

        for el in elements:
            key = self._make_key(el, prefix)
            existing = self.db.get(key)

            if existing:
                # 存在则合并特征（特征可能已变化）
                existing.features = self._build_features(el)
                existing.area_hint = area_hint or existing.area_hint
                self.db.save(existing)
                learned.append(existing)
            else:
                exp = ElementExperience(
                    key=key,
                    features=self._build_features(el),
                    area_hint=area_hint,
                    created_at=time.time(),
                    last_used_at=time.time(),
                )
                self.db.save(exp)
                learned.append(exp)

        logger.info(
            "learn_from_area scope=%s area=%s found=%d new=%d",
            css_scope, area_hint, len(elements), len(learned),
        )
        return learned

    def _make_key(self, el: dict, prefix: str = "") -> str:
        """为元素生成人类可读的唯一 key。

        优先级：data-automationid > data-control-name > id > tag + text > index
        不依赖坐标/dom 路径，保证跨环境稳定性。
        """
        p = prefix
        aid = el.get("dataAutomationId", "") or el.get("dataControlName", "") or ""
        if aid:
            return f"{p}aid_{aid}"
        id_val = el.get("id", "") or ""
        if id_val:
            tag = el.get("tag", "").lower()
            return f"{p}{tag}#{id_val}"
        tag = el.get("tag", "").lower()
        text = (el.get("text", "") or "").strip()[:20]
        role = el.get("role", "")
        if text:
            return f"{p}{tag}_{text}"
        if role:
            return f"{p}{tag}_{role}"
        return f"{p}{tag}_{el.get('index', 0)}"

    def _build_features(self, el: dict) -> dict[str, Any]:
        """从原始元素数据构建特征向量。

        只保留跨环境稳定的特征：
        data-automationid, data-control-name, aria-label, id, tag, text, role,
        placeholder, title, classes（仅作参考）。
        排除 dom_chain / rect / selector — 这些随环境变化。
        """
        return {
            "tag": el.get("tag", ""),
            "id": el.get("id", ""),
            "text": el.get("text", ""),
            "aria_label": el.get("ariaLabel", ""),
            "role": el.get("role", ""),
            "data_automationid": el.get("dataAutomationId", ""),
            "data_control_name": el.get("dataControlName", ""),
            "placeholder": el.get("placeholder", ""),
            "title": el.get("title", ""),
        }

    # ── 重放 ──────────────────────────────────────────────

    async def replay(
        self,
        key_or_query: str,
        *,
        feature_name: str = "text",
        feature_value: str = "",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """根据经验重放一次点击。

        匹配策略：
        1. 如果 key_or_query 是已存储的 key，直接用
        2. 否则按 feature_name + feature_value 搜索经验库
        3. 在 iframe 内用多特征 JS 定位元素
        4. 执行点击
        5. 记录结果到经验库

        Returns:
            {success, source, key, error, ...}
        """
        # ── 找经验 ──────────────────────────────────────
        exp = self.db.get(key_or_query)

        if not exp and feature_value:
            matches = self.db.find_by_feature(feature_name, feature_value)
            if matches:
                exp = matches[0]
                logger.info("Found experience by feature %s=%s: %s", feature_name, feature_value, exp.key)

        if not exp:
            # 用多特征搜索
            results = self.db.search(
                text=key_or_query,
                automation_id=key_or_query,
                aria_label=key_or_query,
            )
            if results:
                exp = results[0][0]
                logger.info("Fuzzy matched experience: %s (score=%.2f)", exp.key, results[0][1])

        if not exp:
            return {"success": False, "error": f"No experience found for: {key_or_query}"}

        # ── 在 iframe 内定位元素 ──────────────────────
        features_json = json.dumps(exp.features, ensure_ascii=False)
        js = f"({LOCATE_BY_FEATURES_JS})({features_json})"
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            exp.record_failure(str(raw["exceptionDetails"]))
            self.db.save(exp)
            return {
                "success": False,
                "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"])),
                "key": exp.key,
            }

        locate_result = (raw.get("result") or {}).get("value") or {}
        if not locate_result.get("found"):
            exp.record_failure(locate_result.get("error", "element not found on page"))
            self.db.save(exp)
            return {
                "success": False,
                "error": locate_result.get("error", "element not found"),
                "key": exp.key,
            }

        # ── 点击 ────────────────────────────────────────
        # 优先用 locate_result 返回的 selector（多特征 JS 找到的实际元素）
        selector = locate_result.get("selector", "")
        if not selector:
            selector = _best_selector(exp.features)

        if selector:
            # JS 点击
            js_click = f"({CLICK_ELEMENT_JS})({json.dumps(selector)})"
            raw = await execute_in_studio(self.session, js_click)
            if raw.get("exceptionDetails"):
                exp.record_failure(str(raw["exceptionDetails"]))
                self.db.save(exp)
                return {
                    "success": False,
                    "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"])),
                    "key": exp.key,
                }

            click_result = (raw.get("result") or {}).get("value") or {}
            if click_result.get("success"):
                exp.record_success()
                self.db.save(exp)
                return {
                    "success": True,
                    "source": "selector",
                    "key": exp.key,
                    "confidence": exp.confidence,
                    "tag": click_result.get("tag"),
                    "rect": click_result.get("rect"),
                }

        # JS selector 失败 → 坐标点击
        rect = locate_result.get("rect", {})
        if rect.get("x") is not None and rect.get("w") is not None:
            cdp_session = await self.session.get_or_create_cdp_session()
            x = rect["x"] + rect["w"] / 2
            y = rect["y"] + rect["h"] / 2
            await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                params={"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                session_id=cdp_session.session_id,
            )
            await cdp_session.cdp_client.send.Input.dispatchMouseEvent(
                params={"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
                session_id=cdp_session.session_id,
            )
            exp.record_success()
            self.db.save(exp)
            return {
                "success": True,
                "source": "coordinate",
                "key": exp.key,
                "confidence": exp.confidence,
                "x": x, "y": y,
            }

    # ── 便利方法 ──────────────────────────────────────────

    async def learn_formula_bar(self) -> list[ElementExperience]:
        """专门探索公式栏区域。"""
        return await self.learn_from_area(
            "#formulaBarContainer",
            area_hint="formulaBar",
            prefix="formulaBar.",
        )

    async def learn_property_panel(self) -> list[ElementExperience]:
        """专门探索属性面板区域。"""
        return await self.learn_from_area(
            ".property-pane, .property-editor, [class*='property']",
            area_hint="propertyPanel",
            prefix="property.",
        )

    async def learn_ribbon(self) -> list[ElementExperience]:
        """专门探索功能区（顶部按钮栏）。"""
        return await self.learn_from_area(
            "[class*='ribbon'], [class*='Ribbon'], [class*='toolbar'], [class*='Toolbar']",
            area_hint="ribbon",
            prefix="ribbon.",
        )

    async def learn_all(self) -> dict[str, int]:
        """探索所有已知区域，返回各区域学到的元素数。"""
        counts = {}
        for name, method in [
            ("formulaBar", self.learn_formula_bar),
            ("propertyPanel", self.learn_property_panel),
            ("ribbon", self.learn_ribbon),
        ]:
            elist = await method()
            counts[name] = len(elist)
            await asyncio.sleep(0.3)
        # 也学其他可见元素
        others = await self.learn_from_area("", area_hint="global")
        counts["global"] = len(others)
        return counts

    def stats(self) -> dict[str, Any]:
        """返回经验数据库统计信息。"""
        return self.db.get_stats()

    # ══════════════════════════════════════════════════════
    # 属性下拉框遍历
    # ══════════════════════════════════════════════════════

    async def get_all_dropdown_options(self) -> dict[str, Any]:
        """打开属性选择器下拉框并通过滚动获取所有选项（不限可视区域）。

        Returns:
            {found, optionsCount, options: [{text, tag, ...}]}
        """
        raw = await execute_in_studio(self.session, PROBE_GET_ALL_OPTIONS_JS)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    async def select_option_and_collect(self, index: int) -> dict[str, Any]:
        """选择第 index 个选项，等待面板刷新，收集属性编辑器内容。

        Args:
            index: 选项索引（0-based）

        Returns:
            {found, index, optionText, panelData: {formulaValue, propertyInputs, ...}}
        """
        js = PROBE_SELECT_OPTION_JS.replace("INDEX_PLACEHOLDER", str(index))
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    # ── 侧边栏 tab 遍历 ──────────────────────────────────

    async def get_sidebar_tabs(self) -> dict[str, Any]:
        """获取属性面板侧边栏的所有 tab。

        Returns:
            {found, count, tabs: [{text, tag, role, ...}]}
        """
        raw = await execute_in_studio(self.session, PROBE_GET_SIDEBAR_TABS_JS)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    async def click_sidebar_tab(self, index: int) -> dict[str, Any]:
        """点击第 index 个侧边栏 tab 并收集该 tab 下的输入框/标签。

        Args:
            index: tab 索引（0-based）

        Returns:
            {found, index, tabText, panelData: {propertyInputs, ...}}
        """
        js = PROBE_CLICK_TAB_JS.replace("INDEX_PLACEHOLDER", str(index))
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    # ── 动态点击遍历 ────────────────────────────────────

    async def find_all_clickable(self) -> dict[str, Any]:
        """找出当前页面所有可见可交互元素。

        Returns:
            {found, count, elements: [{tag, text, role, dataAutomationId, ...}]}
        """
        raw = await execute_in_studio(self.session, PROBE_FIND_ALL_CLICKABLE_JS)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    async def click_by_text(
        self,
        text: str = "",
        data_automation_id: str = "",
    ) -> dict[str, Any]:
        """按文本或 data-automationid 点击按钮。

        Returns:
            {success, tag, text, ariaLabel, rect, ...}
        """
        js = PROBE_CLICK_BY_TEXT_JS \
            .replace("TARGET_TEXT_PLACEHOLDER", json.dumps(text)) \
            .replace("DATA_AID_PLACEHOLDER", json.dumps(data_automation_id))
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"success": False}

    # ── Ribbon 遍历（点击所有可见 Ribbon 按钮） ──────────

    async def explore_ribbon_buttons(self) -> list[dict[str, Any]]:
        """遍历并点击所有可见的顶部 Ribbon 按钮（插入、数据、变量等）。

        跳过"返回"/"Back"类按钮。
        每个按钮点击后等待 1s 让面板打开，然后学习该区域的经验。

        Returns:
            每个按钮的点击结果列表：[{label, success, clicked_text, ...}]
        """
        log = logging.getLogger("experience_engine.ribbon")
        results = []

        # 先扫描所有可见的 ribbon 按钮
        js = r"""
        (function() {
            var all = document.querySelectorAll(
                '[role="menuitem"],button[class*="ribbon"],' +
                '[class*="ribbon"] button,[class*="Ribbon"] button,' +
                '[class*="toolbar"] button,[class*="Toolbar"] button,' +
                '.ms-CommandBarItem-link,[class*="CommandBar"] button'
            );
            var out = [];
            all.forEach(function(el) {
                var r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                var text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60);
                if (!text) return;
                out.push({
                    text: text,
                    ariaLabel: (el.getAttribute('aria-label') || '').trim(),
                    dataAutomationId: el.getAttribute('data-automationid') || '',
                    dataControlName: el.getAttribute('data-control-name') || '',
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                });
            });
            return out;
        })()
        """
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            log.warning("scan ribbon failed: %s", raw["exceptionDetails"].get("text", ""))
            return results
        buttons = (raw.get("result") or {}).get("value") or []

        log.info("Ribbon buttons found: %d", len(buttons))
        for btn in buttons:
            label = btn.get("text") or btn.get("ariaLabel") or btn.get("dataAutomationId") or ""

            # 跳过返回按钮
            label_lower = label.strip().lower()
            if label_lower in ("返回", "back", "←", "«"):
                log.info("  ⏭ Skip: %s (back button)", label[:30])
                results.append({"label": label, "skipped": True})
                continue

            log.info("  %s", label[:50])

            # 点击
            aid = btn.get("dataAutomationId", "")
            click_result = await self.click_by_text(text=label, data_automation_id=aid)
            if click_result.get("success"):
                log.info("  ✅ Clicked: %s", label[:40])
                await asyncio.sleep(1.0)
                # 只学 ribbon 区域，不扫全局（避免污染经验库）
                await self.learn_from_area(
                    "[class*='ribbon'], [class*='Ribbon'], [class*='toolbar'], [class*='Toolbar'], "
                    "[class*='panel'], [class*='Panel'], [class*='pane'], [class*='Pane']",
                    area_hint=f"ribbon_{label.replace(' ', '_')[:20]}",
                    prefix=f"ribbon.{label.replace(' ', '_')[:10]}.",
                )
                results.append({
                    "label": label,
                    "success": True,
                    "clicked_text": click_result.get("text", ""),
                    "dataAutomationId": click_result.get("dataAutomationId", ""),
                })
            else:
                log.info("  ✗ Failed: %s — %s", label[:40], click_result.get("error", ""))
                results.append({
                    "label": label,
                    "success": False,
                    "error": click_result.get("error"),
                })

            await asyncio.sleep(0.3)

        return results

    # ── 插入面板遍历 ─────────────────────────────────────

    async def click_insert_menu_item(self, text: str) -> dict[str, Any]:
        """在插入下拉菜单内按文本点击控件。

        Returns:
            {success, text, rect}
        """
        js = CLICK_INSERT_MENU_ITEM_JS.replace("TARGET_TEXT_PLACEHOLDER", json.dumps(text))
        raw = await execute_in_studio(self.session, js)
        if raw.get("exceptionDetails"):
            return {"success": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"success": False}

    async def get_insert_menu_items(self) -> dict[str, Any]:
        """扫描插入下拉菜单的所有控件模板。

        Returns:
            {found, count, items: [{text, tag, role, ...}]}
        """
        raw = await execute_in_studio(self.session, PROBE_GET_INSERT_MENU_ITEMS_JS)
        if raw.get("exceptionDetails"):
            return {"found": False, "error": raw["exceptionDetails"].get("text", str(raw["exceptionDetails"]))}
        return (raw.get("result") or {}).get("value") or {"found": False}

    async def traverse_current_selection(self, control_name: str) -> dict:
        """遍历当前选中的控件的所有属性（下拉选项 + 侧边栏 tab）。

        1. 打开属性选择器下拉框
        2. 获取全量选项
        3. 逐个选择每个属性并收集面板数据
        4. 对每个属性遍历侧边栏 tab

        Returns:
            {options, optionsCount, properties: {name: {formulaValue, ...}}}
        """
        log = logging.getLogger(f"experience_engine.traverse[{control_name[:20]}]")

        # 先点 formulaBar combobox 打开下拉（经验点击）
        formula_exp = self.db.list_by_area("formulaBar")
        target_exp = None
        for e in formula_exp:
            role = e.features.get("role", "")
            tag = e.features.get("tag", "").upper()
            if role in ("combobox", "listbox", "button") or tag in ("BUTTON", "SELECT"):
                target_exp = e
                break
        if not target_exp and formula_exp:
            target_exp = formula_exp[0]
        if target_exp:
            await self.replay(target_exp.key)
            await asyncio.sleep(0.5)

        opts_data = await self.get_all_dropdown_options()
        if not opts_data.get("found"):
            return {"error": opts_data.get("error", "no dropdown options")}

        all_options = opts_data.get("options", [])
        total = opts_data.get("optionsCount", 0)
        log.info("Options: %d", total)
        for i, o in enumerate(all_options):
            log.info("  [%d] %s%s", i, o.get("text", "")[:50],
                     " [SEL]" if o.get("selected") == "true" else "")

        # 遍历每个属性（不遍历侧边栏 tab）
        properties = {}
        for idx in range(total):
            opt_text = all_options[idx].get("text", f"<{idx}>")[:50]
            sel_res = await self.select_option_and_collect(idx)
            if not sel_res.get("found"):
                log.warning("  [%d] ✗ %s", idx, sel_res.get("error", ""))
                continue

            panel = sel_res.get("panelData", {})
            entry = {
                "formulaValue": panel.get("formulaValue", ""),
                "panelInputs": panel.get("propertyInputs", []),
                "panelLabels": panel.get("propertyLabels", []),
            }

            properties[opt_text] = entry
            log.info("  [%d] ✓ %s", idx, opt_text[:40])
            await asyncio.sleep(0.3)

        return {
            "options": all_options,
            "optionsCount": total,
            "properties": properties,
        }

    async def explore_insert_panel(self, wait_after_insert: int = 5) -> list[dict]:
        """点击插入面板中的每个控件模板，插入后遍历其所有属性。

        步骤：
          1. 点击 Ribbon 上的"插入"按钮
          2. 等待 1s
          3. 扫描插入面板的所有控件模板
          4. 逐个点击控件 → 等待 wait_after_insert 秒 → 遍历属性

        Args:
            wait_after_insert: 插入后等待秒数（PowerApps 加载时间）

        Returns:
            每个插入控件的遍历结果列表
        """
        log = logging.getLogger("experience_engine.insert_panel")
        results = []

        # 1) 点击"插入"
        log.info("Clicking '插入' button...")
        insert_result = await self.click_by_text(text="插入")
        if not insert_result.get("success"):
            log.warning("Cannot find/click '插入': %s", insert_result.get("error", ""))
            # 尝试英文
            insert_result = await self.click_by_text(text="Insert")
            if not insert_result.get("success"):
                log.warning("Cannot find/click 'Insert' either.")
                return results

        log.info("✅ Clicked '插入', waiting for insert panel...")
        await asyncio.sleep(1.5)

        # 2) 扫描插入菜单
        menu_data = await self.get_insert_menu_items()
        if not menu_data.get("found"):
            log.warning("No insert menu items found")
            return results

        items = menu_data.get("items", [])
        log.info("Insert panel items: %d", len(items))
        for i, item in enumerate(items):
            log.info("  [%d] %s", i, item.get("text", "")[:50])

        # 3) 逐个点击并遍历
        control_idx = 0
        for item in items:
            text = item.get("text", "")
            if not text:
                continue

            log.info("=" * 60)
            log.info("Inserting control [%d/%d]: %s", control_idx + 1, len(items), text[:50])
            log.info("=" * 60)

            # 点击控件模板 — 在插入下拉菜单内点击（不会点到属性面板的重复标签）
            click_res = await self.click_insert_menu_item(text=text)
            if not click_res.get("success"):
                log.warning("  ✗ Cannot click in insert panel: %s", click_res.get("error", ""))
                continue

            log.info("  ✅ Clicked, waiting %ds for PowerApps to load...", wait_after_insert)
            await asyncio.sleep(wait_after_insert)

            # 遍历新控件的所有属性
            traverse_result = await self.traverse_current_selection(control_name=text)
            result_entry = {
                "control_name": text.strip(),
                "insert_success": True,
                "traverse": traverse_result,
            }
            results.append(result_entry)

            log.info("  ✅ %s: %d properties traversed",
                     text[:40],
                     len(traverse_result.get("properties", {})))

            control_idx += 1
            # 点下一个前重新点"插入"让面板再打开
            if control_idx < len(items):
                log.info("Re-clicking '插入' for next control...")
                rc = await self.click_by_text(text="插入")
                if not rc.get("success"):
                    await self.click_by_text(text="Insert")
                await asyncio.sleep(1.0)

        return results

    # ── 全自动降维打击：遍历所有能点的 ──────────────────

    async def click_all_explorable(self) -> dict[str, Any]:
        """按优先顺序自动点击所有可见可交互元素并学习。

        顺序：
          1. Ribbon 顶部按钮（跳过返回）
          2. 插入面板遍历（逐个插入控件并遍历属性）
          3. 公式栏组合框（属性选择器）→ 获取选项 → 遍历每个属性

        Returns:
            {ribbon, insertResults, properties, sidebarTabs}
        """
        log = logging.getLogger("experience_engine.click_all")

        results = {"ribbon": [], "insertResults": [], "properties": {}}

        # 1) Ribbon 按钮
        log.info("=" * 60)
        log.info("Phase 1: Ribbon buttons (skip 返回)")
        log.info("=" * 60)
        ribbon_results = await self.explore_ribbon_buttons()
        results["ribbon"] = ribbon_results

        # 2) 插入面板
        log.info("=" * 60)
        log.info("Phase 2: Insert panel — insert each control & traverse all properties")
        log.info("=" * 60)
        insert_results = await self.explore_insert_panel(wait_after_insert=4)
        results["insertResults"] = insert_results
        log.info("Insert panel: %d controls inserted", len(insert_results))

        # 3) 当前控件的全量属性遍历
        log.info("=" * 60)
        log.info("Phase 3: Current selection property traversal")
        log.info("=" * 60)
        current_traverse = await self.traverse_current_selection(control_name="current")
        if current_traverse.get("properties"):
            results["properties"] = current_traverse["properties"]
            log.info("Properties: %d", len(current_traverse["properties"]))
        else:
            log.info("No properties traversed: %s", current_traverse.get("error", ""))

        return results

    # ── 深度遍历 ─────────────────────────────────────────

    async def deep_traverse(self) -> dict[str, Any]:
        """（旧版）属性下拉遍历。保留兼容。"""
        return await self.click_all_explorable()

    async def traverse_property_dropdown(self) -> dict[str, Any]:
        """（旧版）简单遍历属性下拉框。保留向后兼容。"""

        # 1) 打开下拉框获取选项
        options_data = await self.get_all_dropdown_options()
        if not options_data.get("found"):
            return {"success": False, "error": options_data.get("error", "get_options failed")}

        trigger = options_data.get("trigger", {})
        options = options_data.get("options", [])
        total = options_data.get("optionsCount", 0)
        log.info("Dropdown opened: trigger=%s | %d options", trigger.get("tag", ""), total)

        for i, opt in enumerate(options):
            sel = " [SELECTED]" if opt.get("selected") == "true" else ""
            log.info("  [%d] %s%s", i, opt.get("text", "")[:60], sel)

        # 2) 逐个选择并收集
        properties = {}
        errors = []

        for idx in range(total):
            opt_text = options[idx].get("text", f"<index {idx}>")[:50]
            log.info("  --- Option [%d/%d]: %s ---", idx + 1, total, opt_text)

            result = await self.select_option_and_collect(idx)
            if result.get("found"):
                panel = result.get("panelData", {})
                formula_val = panel.get("formulaValue", "")
                inputs_count = len(panel.get("propertyInputs", []))
                labels_count = len(panel.get("propertyLabels", []))
                log.info("    ✓ formula=%s inputs=%d labels=%d",
                         (formula_val or "(empty)")[:40], inputs_count, labels_count)
                properties[opt_text] = {
                    "optionText": result.get("optionText", ""),
                    "optionRect": result.get("optionRect"),
                    "panelData": panel,
                }
            else:
                err = result.get("error", "unknown")
                log.warning("    ✗ FAILED: %s", err)
                errors.append({"index": idx, "text": opt_text, "error": err})

            await asyncio.sleep(0.3)

        return {
            "success": True,
            "trigger": trigger,
            "optionsCount": total,
            "options": options,
            "properties": properties,
            "properties_count": len(properties),
            "errors": errors,
        }


__all__ = ["ExperienceEngine"]