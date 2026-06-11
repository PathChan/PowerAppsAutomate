"""Tree View 遍历：爬取 PowerApps Studio 的 Tree View 结构并构建初始图。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("tree_traversal")

TREE_VIEW_SCRIPT = """
(async () => {
    // execute_in_studio 已在 Studio iframe 内部执行，直接操作 document
    const doc = document;

    // 找 Tree View 容器
    const selectors = [
        '[class*="tree-view"]', '[class*="TreeView"]',
        '[class*="treeView"]', '[class*="component-tree"]',
        '[role="tree"]', '[class*="outline"]',
    ];
    let treeEl = null;
    for (const sel of selectors) {
        treeEl = doc.querySelector(sel);
        if (treeEl) break;
    }
    if (!treeEl) {
        // 兜底：遍历所有 div 找包含控件名的树
        const allDivs = doc.querySelectorAll('div[class*="tree"]');
        for (const div of allDivs) {
            if (div.textContent.includes('Screen') || div.textContent.includes('App')) {
                treeEl = div;
                break;
            }
        }
    }
    if (!treeEl) return {error: "Tree view element not found"};

    // 提取 tree item 自己的文本（排除子级 tree item 的文本，避免拼接）
    function getItemName(el) {
        // 1) aria-label 最准确
        const aria = el.getAttribute('aria-label');
        if (aria) return aria.trim();
        // 2) 找不含子级 tree item 的文本子元素
        const kids = Array.from(el.children);
        for (const c of kids) {
            const hasSubTree = c.querySelector('[role="treeitem"], .tree-item');
            if (!hasSubTree) {
                const t = (c.textContent || '').trim();
                if (t) return t;
            }
        }
        // 3) 直接文本节点
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {
                const t = (node.textContent || '').trim();
                if (t) return t;
            }
        }
        // 4) 最后兜底：用 textContent 减去子级 tree item 的文本
        let text = (el.textContent || '').trim();
        const subItems = el.querySelectorAll('[role="treeitem"], .tree-item, li');
        for (const si of subItems) {
            if (si !== el) text = text.replace((si.textContent || '').trim(), '');
        }
        return text.trim();
    }

    // 递归提取树结构
    function extractTree(el, depth) {
        if (depth > 10) return null;
        const items = [];
        const children = el.children || [];
        for (let i = 0; i < children.length; i++) {
            const child = children[i];
            const tag = child.tagName || '';
            const role = child.getAttribute('role') || '';
            const cls = String(child.className || '');
            const isItem = role === 'treeitem' || tag === 'LI' || cls.includes('tree-item');
            if (isItem) {
                const name = getItemName(child);
                if (!name) continue;
                items.push({
                    name: name,
                    sub: extractTree(child, depth + 1),
                });
            } else {
                const sub = extractTree(child, depth + 1);
                if (sub && sub.length > 0) {
                    items.push(...sub);
                }
            }
        }
        return items.length > 0 ? items : null;
    }

    return {tree: extractTree(treeEl, 0)};
})();
"""

# ── Tree View 搜索框操作 + 读取过滤结果 ─────────────────────
# 注意：用 __KEYWORD__ / __WAIT_MS__ 占位，Python 侧用 .replace() 替换（避免 .format() 与 JS 花括号冲突）
SEARCH_TREE_VIEW_SCRIPT = """
(async () => {
    const doc = document;
    const keyword = __KEYWORD__;
    const waitMs = __WAIT_MS__;

    // 1. 找 Tree View 面板中的搜索框
    const searchSelectors = [
        'input[type="search"]',
        'input[placeholder*="search" i]',
        'input[placeholder*="Search" i]',
        'input[placeholder*="搜索" i]',
        'input[placeholder*="筛选" i]',
        '[role="searchbox"] input',
        '.ms-SearchBox input',
        '[class*="search"] input',
        '[class*="Search"] input',
        '[class*="filter"] input',
        '[class*="Filter"] input',
    ];
    let searchBox = null;
    for (const sel of searchSelectors) {
        searchBox = doc.querySelector(sel);
        if (searchBox) break;
    }
    if (!searchBox) return {error: "Search box not found in Tree View"};

    // 2. 聚焦搜索框并输入关键词
    searchBox.focus();
    searchBox.value = keyword;

    // 派发事件让 PowerApps 响应输入
    searchBox.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
    searchBox.dispatchEvent(new Event('keyup', {bubbles: true, cancelable: true}));
    searchBox.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));

    // 3. 等待 PowerApps 原生过滤生效
    await new Promise(r => setTimeout(r, waitMs));

    // 4. 找 Tree View 容器，读取当前可见的 tree item
    const treeSelectors = [
        '[class*="tree-view"]', '[class*="TreeView"]',
        '[class*="treeView"]', '[class*="component-tree"]',
        '[role="tree"]', '[class*="outline"]',
    ];
    let treeEl = null;
    for (const sel of treeSelectors) {
        treeEl = doc.querySelector(sel);
        if (treeEl) break;
    }
    if (!treeEl) {
        const allDivs = doc.querySelectorAll('div[class*="tree"]');
        for (const div of allDivs) {
            if (div.textContent.includes('Screen') || div.textContent.includes('App')) {
                treeEl = div;
                break;
            }
        }
    }

    // 读取当前可见的 tree items
    const visibleItems = [];
    if (treeEl) {
        const items = treeEl.querySelectorAll('[role="treeitem"], li, [class*="tree-item"]');
        for (const item of items) {
            const style = window.getComputedStyle(item);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const rect = item.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;

            const aria = item.getAttribute('aria-label');
            let name = '';
            if (aria) {
                name = aria.trim();
            } else {
                let t = (item.textContent || '').trim();
                const subItems = item.querySelectorAll('[role="treeitem"], .tree-item, li');
                for (const si of subItems) {
                    if (si !== item) t = t.replace((si.textContent || '').trim(), '');
                }
                name = t.trim();
            }
            if (!name) continue;

            visibleItems.push({
                name: name,
                visible: true,
            });
        }
    }

    // 5. 清空搜索框恢复原状
    searchBox.value = '';
    searchBox.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
    searchBox.dispatchEvent(new Event('keyup', {bubbles: true, cancelable: true}));
    searchBox.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));

    return {
        keyword: keyword,
        found_count: visibleItems.length,
        items: visibleItems,
    };
})();
"""

# ── 安全 JSON 序列化助手 ──────────────────────────────────────
def _js_json(val: str) -> str:
    """把 Python 字符串安全嵌入 JS 字符串（居中引号、换行等）。"""
    return json.dumps(val, ensure_ascii=False)


async def search_in_tree_via_ui(session: Any, keyword: str, wait_ms: int = 600) -> dict:
    """在 Tree View 的搜索框中输入关键词，让 PowerApps 原生过滤，然后返回可见条目。

    Args:
        session: Browser 会话
        keyword: 搜索关键词
        wait_ms: 输入后等待过滤的时间（毫秒），默认 600ms

    Returns:
        {"keyword": str, "found_count": int, "items": list[dict], "error"?: str}
    """
    try:
        from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio

        kw = json.dumps(keyword, ensure_ascii=False)
        js = SEARCH_TREE_VIEW_SCRIPT.replace('__KEYWORD__', kw).replace('__WAIT_MS__', str(wait_ms))
        result = await execute_in_studio(session, js)
        if result.get("exceptionDetails"):
            return {"error": result["exceptionDetails"].get("text", "unknown"), "items": []}

        data = (result.get("result") or {}).get("value", {})
        if data.get("error"):
            log.warning("Tree View UI 搜索失败: %s", data["error"])
            return {"error": data["error"], "items": []}

        log.info("Tree View UI 搜索 '%s' → %d 个匹配", keyword, data.get("found_count", 0))
        return data

    except Exception as e:
        log.warning("Tree View UI 搜索异常: %s", e)
        return {"error": str(e), "items": []}


async def traverse_tree_via_cdp(session: Any) -> list[dict[str, Any]]:
    """通过 CDP 在 Studio iframe 中执行 Tree View 遍历脚本。"""
    try:
        from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio
        result = await execute_in_studio(session, TREE_VIEW_SCRIPT)
        if result.get("exceptionDetails"):
            log.warning("Tree View 遍历出错: %s", result["exceptionDetails"].get("text", "unknown"))
            return []
        data = (result.get("result") or {}).get("value", {})
        if data.get("error"):
            log.warning("Tree View 遍历出错: %s", data["error"])
            return []

        tree = data.get("tree", [])
        screens = _flatten_tree(tree)
        log.info("Tree View 遍历完成: %d 个屏幕/控件", len(screens))
        return screens

    except Exception as e:
        log.warning("Tree View 遍历失败: %s", e)
        return []


def _flatten_tree(tree: list, parent: str = "") -> list[dict[str, Any]]:
    """将嵌套树结构拍平为 [{name, parent, depth}]"""
    result = []
    for item in (tree or []):
        name = item.get("name", "").strip()
        if not name:
            continue
        entry = {"name": name, "parent": parent}
        sub = item.get("sub")
        if sub:
            entry["type"] = "screen" if "screen" in name.lower() else "container"
            result.append(entry)
            result.extend(_flatten_tree(sub, name))
        else:
            entry["type"] = "control"
            result.append(entry)
    return result


async def build_graph_from_tree(session: Any, graph: Any, screen_filter: str | None = None) -> int:
    """遍历 Tree View 并将结果写入 ControlGraph。"""
    from .graph_memory import ControlGraph
    from pathlib import Path
    import os

    if graph is None:
        graph_path = os.getenv("POWERAPPS_GRAPH_PATH", "PowerfulApps/Agents/.memory/project_graph.bin")
        graph = ControlGraph.load(Path(graph_path))

    items = await traverse_tree_via_cdp(session)
    if not items:
        log.warning("Tree View 为空，跳过构建")
        return 0

    # 按屏幕过滤
    if screen_filter:
        sf = screen_filter.lower()
        items = [
            i for i in items
            if sf in i.get("name", "").lower()
            or sf in i.get("parent", "").lower()
        ]

    count = 0
    for item in items:
        name = item["name"]
        parent = item.get("parent", "")
        typ = item.get("type", "control")
        if typ == "screen":
            graph.add_screen(name)
        else:
            graph.add_control(screen=parent or "__unknown__", name=name)
        count += 1

    graph.save(Path(os.getenv("POWERAPPS_GRAPH_PATH", "PowerfulApps/Agents/.memory/project_graph.bin")))
    log.info("从 Tree View 构建了 %d 个节点", count)
    return count


async def fetch_properties_via_cdp(session: Any, control_name: str) -> dict[str, Any]:
    """通过 CDP 获取指定控件的当前属性值。"""
    props_script = """
    (() => {
        // execute_in_studio 已在 Studio iframe 内部执行，直接操作 document
        const doc = document;
        // 尝试几种方式获取属性
        const propPanel = doc.querySelector('[class*="property-panel"], [class*="properties"]');
        if (!propPanel) return {};
        const fields = propPanel.querySelectorAll('input, textarea, select');
        const props = {};
        fields.forEach(f => {
            const label = f.closest('[class*="property"]')?.querySelector('label')?.textContent || f.id || f.name;
            if (label) props[label] = f.value || f.textContent || '';
        });
        return props;
    })();
    """
    try:
        from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio
        result = await execute_in_studio(session, props_script)
        if result.get("exceptionDetails"):
            log.warning("获取属性失败: %s", result["exceptionDetails"].get("text", "unknown"))
            return {}
        return (result.get("result") or {}).get("value", {})
    except Exception as e:
        log.warning("获取属性失败: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════════
#  扫描模式：五种扫描动作
# ═══════════════════════════════════════════════════════════════

# 常用属性列表（PowerApps 高频属性）
_COMMON_PROPERTIES = [
    "Text", "OnSelect", "Visible", "Items", "Default",
    "DefaultSelectedItems", "X", "Y", "Width", "Height",
    "Fill", "BorderColor", "DisplayMode", "Tooltip",
    "OnChange", "OnVisible", "OnHidden", "Reset",
]


async def scan_tree_light(session: Any) -> list[dict]:
    """最轻量：只扫描 Tree View 组件名和层级。"""
    return await traverse_tree_via_cdp(session)


async def scan_tree_with_props(
    session: Any,
    *,
    full: bool = False,
    screen_filter: str | None = None,
) -> dict:
    """扫描 Tree View 并获取每个控件的属性。

    Args:
        session: Browser 会话
        full: True=全部属性, False=仅常用属性
        screen_filter: 可选，只扫描指定屏幕

    Returns:
        {"controls": [{"name", "screen", "properties": {...}}], "total": int}
    """
    from PowerfulApps.MocProcess.chains.search_and_select_tree_item import search_and_select_tree_item
    from PowerfulApps.MocProcess.actions.formula_bar import get_property_options

    items = await traverse_tree_via_cdp(session)
    if not items:
        return {"controls": [], "total": 0}

    # 过滤
    if screen_filter:
        sf = screen_filter.lower()
        items = [i for i in items if sf in i.get("name", "").lower() or sf in i.get("parent", "").lower()]

    controls = []
    total = len(items)
    prop_set = None if full else set(_COMMON_PROPERTIES)

    for idx, item in enumerate(items):
        name = item["name"]
        screen = item.get("parent", "__unknown__")
        typ = item.get("type", "control")

        if typ == "screen":
            controls.append({"name": name, "screen": "", "type": "screen", "properties": {}})
            continue

        # 选中控件
        sel = await search_and_select_tree_item(session, name, name, ensure_sidebar_open=(idx == 0))
        if not sel.get("success"):
            controls.append({"name": name, "screen": screen, "type": "control", "properties": {}, "error": sel.get("error", "")})
            continue

        # 获取属性选项列表
        opts = await get_property_options(session)
        options = opts.get("options", [])
        if not options:
            controls.append({"name": name, "screen": screen, "type": "control", "properties": {}})
            continue

        # 遍历属性选项，读取每个属性的公式值
        properties = {}
        from PowerfulApps.MocProcess.actions.formula_bar import select_property_option
        for opt_idx, opt in enumerate(options):
            opt_text = opt.get("text", "")
            if not full and prop_set and opt_text not in prop_set:
                continue

            sel_result = await select_property_option(session, opt_idx)
            if sel_result.get("found"):
                panel = sel_result.get("panelData", {})
                formula = panel.get("formulaValue", "")
                properties[opt_text] = formula

        controls.append({
            "name": name,
            "screen": screen,
            "type": "control",
            "properties": properties,
        })

    return {"controls": controls, "total": total}


async def scan_single_control(
    session: Any,
    control_name: str,
    *,
    full: bool = False,
) -> dict:
    """扫描单个控件的属性。

    Args:
        session: Browser 会话
        control_name: 控件名称（如 "Button1"、"TextInput1"）
        full: True=全部属性, False=仅常用属性

    Returns:
        {"name": str, "properties": {...}, "success": bool}
    """
    from PowerfulApps.MocProcess.chains.search_and_select_tree_item import search_and_select_tree_item
    from PowerfulApps.MocProcess.actions.formula_bar import get_property_options, select_property_option

    # 选中控件
    sel = await search_and_select_tree_item(session, control_name, control_name)
    if not sel.get("success"):
        return {"name": control_name, "properties": {}, "success": False, "error": sel.get("error", "选中失败")}

    # 获取属性选项列表
    opts = await get_property_options(session)
    options = opts.get("options", [])
    if not options:
        return {"name": control_name, "properties": {}, "success": True}

    prop_set = None if full else set(_COMMON_PROPERTIES)
    properties = {}

    for opt_idx, opt in enumerate(options):
        opt_text = opt.get("text", "")
        if not full and prop_set and opt_text not in prop_set:
            continue

        sel_result = await select_property_option(session, opt_idx)
        if sel_result.get("found"):
            panel = sel_result.get("panelData", {})
            formula = panel.get("formulaValue", "")
            properties[opt_text] = formula

    return {"name": control_name, "properties": properties, "success": True}


async def apply_scan_to_graph(
    session: Any,
    graph: Any,
    *,
    mode: str = "light",
    screen_filter: str | None = None,
    control_name: str | None = None,
) -> dict:
    """执行扫描并将结果写入 ControlGraph。

    Args:
        session: Browser 会话
        graph: ControlGraph 实例
        mode: "light" | "props" | "full" | "control" | "control_full"
        screen_filter: 可选屏幕过滤
        control_name: mode=control/control_full 时的目标控件名

    Returns:
        {"mode": str, "nodes_added": int, "props_updated": int}
    """
    result = {"mode": mode, "nodes_added": 0, "props_updated": 0}

    if mode in ("control", "control_full"):
        if not control_name:
            return {**result, "error": "control 模式需要指定 control_name"}
        full = (mode == "control_full")
        data = await scan_single_control(session, control_name, full=full)
        if data.get("success") and data.get("properties"):
            for prop_name, formula in data["properties"].items():
                graph.update_property(control_name, prop_name, formula)
                result["props_updated"] += 1
            graph.auto_detect_references(control_name)
            graph.auto_detect_variable_chain(control_name)
        return result

    # light / props / full
    if mode == "light":
        items = await scan_tree_light(session)
    else:
        full = (mode == "full")
        scan_result = await scan_tree_with_props(session, full=full, screen_filter=screen_filter)
        items = scan_result.get("controls", [])

    if not items:
        return result

    if screen_filter:
        sf = screen_filter.lower()
        items = [i for i in items if sf in i.get("name", "").lower() or sf in i.get("screen", "").lower()]

    for item in items:
        name = item["name"]
        screen = item.get("screen", "")
        typ = item.get("type", "control")

        if typ == "screen":
            graph.add_screen(name)
        elif not graph.graph.has_node(name):
            graph.add_control(screen=screen or "__unknown__", name=name)
            result["nodes_added"] += 1

        # 写入属性
        props = item.get("properties", {})
        for prop_name, formula in props.items():
            graph.update_property(name, prop_name, formula)
            result["props_updated"] += 1

    # 扫描后检测引用和变量链
    if result["props_updated"] > 0:
        if mode == "light":
            graph.auto_detect_all_variable_chains()
        else:
            for item in items:
                name = item["name"]
                if item.get("properties"):
                    graph.auto_detect_references(name)
                    graph.auto_detect_variable_chain(name)

    from pathlib import Path
    import os
    graph.save(Path(os.getenv("POWERAPPS_GRAPH_PATH", "PowerfulApps/Agents/.memory/project_graph.bin")))
    return result