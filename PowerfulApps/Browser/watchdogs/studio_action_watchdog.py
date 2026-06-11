"""
Studio Action Watchdog — 监测用户在 PowerApps Studio 浏览器中的操作。

工作原理：
  1. 通过 CDP 在 Studio iframe 中注入 MutationObserver
  2. 监听 Tree View、属性面板、公式栏的 DOM 变化
  3. 周期性轮询获取最新状态快照
  4. 将检测到的变化应用到 ControlGraph
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("studio_watchdog")

# ═══════════════════════════════════════════════════════════════
#  JavaScript 注入脚本
# ═══════════════════════════════════════════════════════════════

_OBSERVER_SCRIPT = """
// == Studio Action Observer ==
(function() {
    // 避免重复注入
    if (window.__studioObserverInjected) return {injected: false, reason: 'already injected'};
    window.__studioObserverInjected = true;
    window.__studioChanges = [];

    const STORE = '__studioChanges';

    function push(change) {
        window[STORE].push({...change, ts: Date.now()});
        // 最多保留 200 条
        if (window[STORE].length > 200) window[STORE].splice(0, 50);
    }

    // --- 监测 Tree View 变化 ---
    function watchTreeView(root) {
        const treeSelectors = [
            '[class*="tree-view"]', '[class*="TreeView"]',
            '[role="tree"]', '[class*="outline"]',
            '[class*="component-tree"]',
        ];
        let treeEl = null;
        for (const sel of treeSelectors) {
            treeEl = root.querySelector(sel);
            if (treeEl) break;
        }
        if (!treeEl) {
            // 兜底：找包含 Screen 文字的 div 树
            for (const div of root.querySelectorAll('div')) {
                const t = div.textContent || '';
                if ((t.includes('Screen') || t.includes('App(')) && div.children.length > 1) {
                    treeEl = div; break;
                }
            }
        }
        if (!treeEl) return null;

        const observer = new MutationObserver((mutations) => {
            for (const m of mutations) {
                if (m.type === 'childList' && m.addedNodes.length > 0) {
                    for (const node of m.addedNodes) {
                        const text = (node.textContent || '').trim();
                        if (text && text.length < 80) {
                            push({type: 'tree_add', text, tag: node.tagName});
                        }
                    }
                }
                if (m.type === 'childList' && m.removedNodes.length > 0) {
                    for (const node of m.removedNodes) {
                        const text = (node.textContent || '').trim();
                        if (text && text.length < 80) {
                            push({type: 'tree_remove', text});
                        }
                    }
                }
                if (m.type === 'characterData' || (m.type === 'childList' && m.target?.textContent)) {
                    const target = m.target;
                    const text = (target.textContent || '').trim();
                    if (text && text.length < 80 && !text.includes('object') && !text.includes('undefined')) {
                        push({type: 'tree_change', text, tag: target.tagName});
                    }
                }
            }
        });
        observer.observe(treeEl, {
            childList: true, subtree: true,
            characterData: true, attributes: false,
        });
        return {treeElTag: treeEl.tagName, treeElClass: (treeEl.className || '').slice(0, 100)};
    }

    // --- 监测属性面板变化 ---
    function watchPropertyPanel(root) {
        const panelSelectors = [
            '[class*="property-panel"]', '[class*="properties"]',
            '[class*="PropertyPane"]', '[class*="inspector"]',
        ];
        let panelEl = null;
        for (const sel of panelSelectors) {
            panelEl = root.querySelector(sel);
            if (panelEl) break;
        }
        if (!panelEl) return null;

        let lastValues = {};
        const observer = new MutationObserver(() => {
            const inputs = panelEl.querySelectorAll('input, textarea, select');
            const current = {};
            inputs.forEach(inp => {
                const label = inp.closest('[class*="property"]')?.querySelector('label')?.textContent?.trim() || inp.id || inp.name || '';
                if (label) current[label] = inp.value || inp.textContent || '';
            });
            // 对比变化
            for (const [key, val] of Object.entries(current)) {
                if (lastValues[key] !== undefined && lastValues[key] !== val) {
                    push({type: 'prop_change', property: key, oldValue: lastValues[key], newValue: val});
                }
            }
            lastValues = current;
        });
        observer.observe(panelEl, {
            childList: true, subtree: true,
            characterData: true, attributes: true, attributeFilter: ['value'],
        });
        return true;
    }

    // --- 监测公式栏 ---
    function watchFormulaBar(root) {
        const formulaSelectors = [
            '#formulaBarContainer', '[class*="formula-bar"]',
            '[class*="formulaBar"]',
        ];
        let formulaEl = null;
        for (const sel of formulaSelectors) {
            formulaEl = root.querySelector(sel);
            if (formulaEl) break;
        }
        if (!formulaEl) return null;

        // 优先监听 textarea/input 的 value 变化
        const textarea = formulaEl.querySelector('textarea, input');
        if (textarea) {
            let lastValue = textarea.value || '';
            const observer = new MutationObserver(() => {
                const current = textarea.value || '';
                if (current !== lastValue && current.length > 0) {
                    push({type: 'formula_change', oldValue: lastValue.slice(0, 200), newValue: current.slice(0, 200)});
                    lastValue = current;
                }
            });
            observer.observe(textarea, {attributes: true, attributeFilter: ['value'], childList: false, characterData: false});
            return {mode: 'textarea'};
        }

        // 兜底：监听 textContent
        let lastText = formulaEl.textContent || '';
        const observer = new MutationObserver(() => {
            const current = formulaEl.textContent || '';
            if (current !== lastText && current.length > 0) {
                push({type: 'formula_change', oldValue: lastText.slice(0, 200), newValue: current.slice(0, 200)});
                lastText = current;
            }
        });
        observer.observe(formulaEl, {childList: true, subtree: true, characterData: true});
        return {mode: 'textcontent'};
    }

    // --- 启动 ---
    const root = document.body || document.documentElement;
    const results = {
        injected: true,
        tree: watchTreeView(root),
        panel: watchPropertyPanel(root),
        formula: watchFormulaBar(root),
    };
    return results;
})();
"""

_SNAPSHOT_SCRIPT = """
// == Studio State Snapshot ==
(function() {
    const d = document;
    const studio = d.querySelector('iframe[src*="authoring"]');
    const doc = studio ? (studio.contentDocument || studio.contentWindow?.document) : d;

    // 提取 Tree View 结构
    const treeSelectors = [
        '[class*="tree-view"]', '[class*="TreeView"]', '[role="tree"]',
        '[class*="outline"]', '[class*="component-tree"]',
    ];
    let treeEl = null;
    for (const sel of treeSelectors) { treeEl = doc.querySelector(sel); if (treeEl) break; }
    if (!treeEl) {
        for (const div of doc.querySelectorAll('div')) {
            if ((div.textContent||'').includes('Screen') && div.children.length > 1) { treeEl = div; break; }
        }
    }

    const treeItems = [];
    function walk(el, depth) {
        if (depth > 8) return;
        for (const c of el.children || []) {
            const t = (c.textContent||'').trim();
            if (!t) continue;
            const role = c.getAttribute('role')||'';
            const isItem = role==='treeitem' || c.tagName==='LI' || (c.className||'').includes('tree-item');
            if (isItem && t.length < 80) {
                treeItems.push({name: t, depth});
            }
            walk(c, depth + 1);
        }
    }
    if (treeEl) walk(treeEl, 0);

    // 提取选中控件信息 --- 使用 Tree View 中的真实控件名，而非 UI 文本
    const selectedEl = doc.querySelector('[class*="selected"],[aria-selected="true"]');
    let selectedName = '';
    if (selectedEl) {
        // 方法1: 从 aria-label 提取（通常包含真实控件名）
        const ariaLabel = selectedEl.getAttribute('aria-label') || '';
        if (ariaLabel) {
            selectedName = ariaLabel.trim();
        }
        // 方法2: 从 data-* 属性提取
        if (!selectedName) {
            const dataId = selectedEl.getAttribute('data-automationid')
                || selectedEl.getAttribute('data-id')
                || selectedEl.getAttribute('data-control-name')
                || '';
            if (dataId) selectedName = dataId.trim();
        }
        // 方法3: 从 Tree View 对应的条目里找字母数字控件名（跳过纯中文/UI文本）
        if (!selectedName) {
            const raw = (selectedEl.textContent||'').trim();
            // 如果包含英文控件名（如 Screen3, Button1），提取它
            const match = raw.match(/([A-Z][a-zA-Z0-9_]+)/);
            if (match) selectedName = match[1];
        }
        // 方法4: 从同级 tree items 中匹配位置
        if (!selectedName) {
            const treeItems = Array.from(doc.querySelectorAll('[role="treeitem"], li, [class*="tree-item"]'));
            const idx = treeItems.indexOf(selectedEl);
            if (idx >= 0) {
                const nameMatch = (treeItems[idx].textContent||'').match(/([A-Z][a-zA-Z0-9_]+)/);
                if (nameMatch) selectedName = nameMatch[1];
            }
        }
        // 兜底
        if (!selectedName) selectedName = (selectedEl.textContent||'').trim();
    }

    // 提取属性面板
    const panelEl = doc.querySelector('[class*="property-panel"],[class*="properties"],[class*="PropertyPane"]');
    const properties = {};
    if (panelEl) {
        panelEl.querySelectorAll('input, textarea, select').forEach(inp => {
            const label = inp.closest('[class*="property"]')?.querySelector('label')?.textContent?.trim() || inp.id || inp.name || '';
            if (label) properties[label] = inp.value || inp.textContent || '';
        });
    }

    // 提取公式栏 --- 清理掉"不存在公式错误"等干扰文本
    const formulaEl = doc.querySelector('#formulaBarContainer, [class*="formula-bar"]');
    let formulaText = '';
    if (formulaEl) {
        // 优先取 textarea/input 的 value（纯净公式）
        const textarea = formulaEl.querySelector('textarea, input');
        if (textarea && textarea.value) {
            formulaText = textarea.value.trim().slice(0, 1000);
        } else {
            // 兜底取 textContent 并清理
            const raw = (formulaEl.textContent||'').trim();
            // 去掉 "=不存在公式错误" 等常见干扰前缀
            formulaText = raw
                .replace(/^=.*?错误/, '')
                .replace(/^=\s*=/, '=')
                .trim()
                .slice(0, 1000);
        }
    }

    return {
        tree: treeItems,
        selectedControl: selectedName,
        properties: properties,
        formula: formulaText,
    };
})();
"""


@dataclass
class StudioChange:
    """用户操作变更记录"""
    type: str           # tree_add / tree_remove / tree_change / prop_change / formula_change
    ts: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StudioSnapshot:
    """Studio 当前状态快照"""
    tree: list[dict] = field(default_factory=list)
    selected_control: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    formula: str = ""


class StudioActionWatchdog:
    """监测 PowerApps Studio 浏览器中的用户操作。

    Usage:
        watchdog = StudioActionWatchdog(session)
        await watchdog.start()
        # ... later ...
        changes = await watchdog.collect_changes()
        await watchdog.apply_to_graph(graph)
    """

    def __init__(self, session: Any) -> None:
        self.session = session
        self._running = False
        self._task: asyncio.Task | None = None
        self._injected = False

    async def start(self) -> bool:
        """启动观察器：在 Studio iframe 中注入 MutationObserver。"""
        try:
            from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

            result = await execute_in_studio(self.session, _OBSERVER_SCRIPT)
            if result.get("exceptionDetails"):
                log.warning("注入失败: %s", result["exceptionDetails"])
                return False
            value = (result.get("result") or {}).get("value") or {}
            log.info("Observer 注入结果: %s", json.dumps(value, ensure_ascii=False))
            self._injected = value.get("injected", False)

            if not self._injected and value.get("reason"):
                log.warning("注入被拒绝: %s", value["reason"])

            return self._injected

        except Exception as e:
            log.warning("启动观察器失败: %s", e)
            return False

    async def collect_changes(self) -> list[dict]:
        """收集自上次检查以来的所有变更。"""
        if not self._injected:
            return []

        try:
            from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

            # 获取累积的变更并清空队列
            fetch_script = """
            (function() {
                const changes = window.__studioChanges || [];
                window.__studioChanges = [];
                return changes;
            })();
            """
            result = await execute_in_studio(self.session, fetch_script)
            if result.get("exceptionDetails"):
                return []
            return (result.get("result") or {}).get("value") or []

        except Exception as e:
            log.warning("收集变更失败: %s", e)
            return []

    async def snapshot(self) -> StudioSnapshot:
        """获取 Studio 当前完整状态快照。"""
        try:
            from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

            result = await execute_in_studio(self.session, _SNAPSHOT_SCRIPT)
            if result.get("exceptionDetails"):
                return StudioSnapshot()
            data = (result.get("result") or {}).get("value") or {}

            return StudioSnapshot(
                tree=data.get("tree", []),
                selected_control=data.get("selectedControl", ""),
                properties=data.get("properties", {}),
                formula=data.get("formula", ""),
            )

        except Exception as e:
            log.warning("快照失败: %s", e)
            return StudioSnapshot()

    async def apply_changes_to_graph(self, graph: Any, changes: list[dict]) -> list[str]:
        """将检测到的变更应用到 ControlGraph。返回变更描述列表。"""
        applied: list[str] = []

        for change in changes:
            try:
                ct = change.get("type", "")
                if ct == "tree_add":
                    name = change.get("text", "")
                    if name and not graph.graph.has_node(name):
                        graph.add_control("__unknown__", name)
                        applied.append(f"新增控件: {name} (来自 Tree View)")

                elif ct == "tree_remove":
                    name = change.get("text", "")
                    if name and graph.graph.has_node(name):
                        graph.remove_control(name)
                        applied.append(f"删除控件: {name}")

                elif ct == "prop_change":
                    prop = change.get("property", "")
                    value = change.get("newValue", "")
                    # 属性变更需要知道是哪个控件，从 snapshot 获取选中控件
                    # 在这里我们只做标记，apply 时会结合 snapshot 一起处理

                elif ct == "formula_change":
                    formula = change.get("newValue", "")
                    applied.append(f"公式变更 (长度 {len(formula)})")

            except Exception as e:
                log.warning("应用变更失败: %s: %s", change, e)

        return applied

    async def apply_snapshot_to_graph(self, graph: Any, snapshot: StudioSnapshot) -> list[str]:
        """将当前状态快照应用到 ControlGraph。"""
        applied: list[str] = []

        # 处理 Tree View 结构
        tree_names = set()
        for item in snapshot.tree:
            name = item.get("name", "").strip()
            if not name:
                continue
            tree_names.add(name)
            if not graph.graph.has_node(name):
                depth = item.get("depth", 0)
                if depth == 0 and "screen" in name.lower():
                    graph.add_screen(name)
                    applied.append(f"发现屏幕: {name}")
                else:
                    graph.add_control("__unknown__", name)
                    applied.append(f"发现控件: {name}")

        # 处理选中控件的属性
        selected = snapshot.selected_control
        if selected and snapshot.properties:
            if not graph.graph.has_node(selected):
                graph.add_control("__unknown__", selected)
                applied.append(f"自动添加选中控件: {selected}")

            for k, v in snapshot.properties.items():
                old = graph.get_properties(selected).get(k)
                graph.update_property(selected, k, v)
                if old is None:
                    applied.append(f"{selected}.{k} = {v}")
                elif str(old) != v:
                    applied.append(f"{selected}.{k}: {old} -> {v}")

            # 检测公式中的引用
            graph.auto_detect_references(selected)

        return applied

    async def watch_loop(
        self,
        graph: Any,
        *,
        interval: float = 2.0,
        on_change: Any = None,
    ) -> None:
        """启动持续监听循环。

        Args:
            graph: ControlGraph 实例
            interval: 轮询间隔（秒）
            on_change: 可选的回调，每次检测到变更时调用
        """
        if not self._injected:
            if not await self.start():
                log.error("无法启动观察器，监听循环退出")
                return

        log.info("监听循环已启动 (间隔 %.1fs)", interval)
        self._running = True

        while self._running:
            try:
                changes = await self.collect_changes()
                snapshot = await self.snapshot()

                all_applied: list[str] = []

                if changes:
                    applied = await self.apply_changes_to_graph(graph, changes)
                    all_applied.extend(applied)

                if snapshot.tree or snapshot.properties:
                    applied = await self.apply_snapshot_to_graph(graph, snapshot)
                    all_applied.extend(applied)

                if all_applied and on_change:
                    await on_change(all_applied)

                if all_applied:
                    from pathlib import Path
                    import os
                    gp = os.getenv("POWERAPPS_GRAPH_PATH",
                                    str(Path.cwd() / "PowerfulApps" / "Agents" / ".memory" / "project_graph.bin"))
                    graph.save(Path(gp))
                    log.info("已应用 %d 条变更并保存图", len(all_applied))

            except Exception as e:
                log.warning("监听循环异常: %s", e)

            await asyncio.sleep(interval)

    def stop(self) -> None:
        """停止监听循环。"""
        self._running = False
        if self._task:
            self._task.cancel()