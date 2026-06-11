"""
Test 2: Tree View 扫描测试
===========================
扫描 PowerApps Studio 的 Tree View 结构，输出到控制台，
同时用 langgraph 输出有向图。

运行方式：
  1. 确保已登录 https://make.powerapps.com 并打开 App
  2. 准备好后按回车执行
    uv run python .\Test\test_tree_scan.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PowerfulApps.Agents.memory.graph_memory import ControlGraph
from PowerfulApps.Agents.config.env import load_env_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_tree")

# ── 颜色工具 ──────────────────────────────────────────────────
_C = {"rst": "[0m", "b": "[1m", "d": "[2m", "r": "[31m",
      "g": "[32m", "y": "[33m", "c": "[36m", "m": "[35m"}
_E = chr(27)
def cc(t: str, *ns: str) -> str:
    return _E + "".join(_C.get(n, "") for n in ns) + t + _E + _C["rst"]


def print_header(title: str) -> None:
    print()
    print(cc(f"{'='*60}", "c"))
    print(cc(f"  {title}", "c", "b"))
    print(cc(f"{'='*60}", "c"))


def try_viz_scan_result(graph: ControlGraph, output_path: str | None = None) -> str:
    """将扫描结果可视化为 PNG"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    if graph.graph.number_of_nodes() == 0:
        return "[空图，无可视化]"

    pos = nx.spring_layout(graph.graph, seed=42, k=3.0, iterations=50)
    plt.figure(figsize=(18, 12))

    screen_nodes = [n for n, a in graph.graph.nodes(data=True) if a.get("type") == "screen"]
    control_nodes = [n for n, a in graph.graph.nodes(data=True) if a.get("type") == "control"]

    if screen_nodes:
        nx.draw_networkx_nodes(graph.graph, pos, nodelist=screen_nodes,
                               node_color="#4A90D9", node_size=3500, node_shape="s")
    if control_nodes:
        nx.draw_networkx_nodes(graph.graph, pos, nodelist=control_nodes,
                               node_color="#7BC47F", node_size=1800, node_shape="o")

    if graph.graph.number_of_edges() > 0:
        nx.draw_networkx_edges(graph.graph, pos, edge_color="#888888",
                               arrows=True, arrowsize=18, arrowstyle="->",
                               width=1.2, connectionstyle="arc3,rad=0.1")

    labels = {n: n for n in graph.graph.nodes()}
    nx.draw_networkx_labels(graph.graph, pos, labels, font_size=7)

    out = output_path or str(Path(tempfile.gettempdir()) / "powerapps_tree_scan.png")
    plt.title(f"Tree View 扫描结果 | {graph.graph.number_of_nodes()} 节点 | "
              f"{len(screen_nodes)} 屏幕 {len(control_nodes)} 控件")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    return out


# ═══════════════════════════════════════════════════════════════
#  Tree View 深度扫描脚本（增强版：读出控件名 + 关键属性）
# ═══════════════════════════════════════════════════════════════
_DEEP_SCAN_SCRIPT = """
(async () => {
    // execute_in_studio 已在 Studio iframe 内部，直接操作 document
    const doc = document;

    // === 1. 扫描 Tree View 结构 ===
    function findTree(el) {
        if (!el) return null;
        const selectors = [
            '[class*="tree-view"]', '[class*="TreeView"]', '[role="tree"]',
            '[class*="outline"]', '[class*="component-tree"]',
        ];
        for (const sel of selectors) {
            const found = el.querySelector(sel);
            if (found) return found;
        }
        for (const div of el.querySelectorAll('div')) {
            const t = div.textContent || '';
            if ((t.includes('Screen') || t.includes('App(')) && div.children.length > 1) return div;
        }
        return null;
    }

    const treeEl = findTree(doc);
    if (!treeEl) return {error: 'Tree view element not found'};

    const treeItems = [];
    function walk(el, depth) {
        if (depth > 10) return;
        for (const c of el.children || []) {
            const t = (c.textContent || '').trim();
            if (!t || t.length > 100) continue;
            const role = c.getAttribute('role') || '';
            const cls = c.className || '';
            const isItem = role === 'treeitem' || c.tagName === 'LI' || cls.includes('tree-item');
            if (isItem && t.length < 80) {
                treeItems.push({name: t, depth, tag: c.tagName});
            }
            walk(c, depth + 1);
        }
    }
    walk(treeEl, 0);

    // === 2. 探测关键属性 ===
    // 逐个点击 tree item → 读取属性面板
    const propsByControl = {};
    for (let i = 0; i < Math.min(treeItems.length, 30); i++) {
        const itemName = treeItems[i].name;
        try {
            // 尝试点击该 tree item（选中它，属性面板就会更新）
            const clickable = Array.from(treeEl.querySelectorAll('[role="treeitem"], li, [class*="tree-item"]'))
                .find(el => (el.textContent || '').trim() === itemName);
            if (clickable) {
                clickable.click();
                await new Promise(r => setTimeout(r, 300));
            }
        } catch(e) { /* ignore */ }

        // 读取属性面板
        const panel = doc.querySelector('[class*="property-panel"],[class*="properties"],[class*="PropertyPane"]');
        if (panel) {
            const props = {};
            panel.querySelectorAll('input, textarea, select').forEach(inp => {
                const label = inp.closest('[class*="property"]')?.querySelector('label')?.textContent?.trim() || inp.id || inp.name || '';
                if (label) props[label] = (inp.value || inp.textContent || '').slice(0, 200);
            });
            if (Object.keys(props).length > 0) propsByControl[itemName] = props;
        }
    }

    return {
        tree: treeItems,
        depth: treeItems.length > 0 ? Math.max(...treeItems.map(i => i.depth)) : 0,
        controlsCount: treeItems.length,
        properties: propsByControl,
    };
})();
"""


async def setup_browser_session() -> tuple:
    """创建浏览器会话并连接 PowerApps Studio"""
    from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio

    load_env_file(_PROJECT_ROOT / ".env")
    url = os.getenv("POWER_APPS_URL", "https://make.powerapps.com")
    ud = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile")))
    ud.mkdir(parents=True, exist_ok=True)

    print(cc("  [1/3] 启动浏览器...", "y"))
    session = await create_browser_session(url, ud)

    print(cc("  [2/3] 连接 Studio...", "y"))
    ok = await prepare_studio(session)
    if not ok:
        print(cc("  [WARN] Studio 未连接，可能未登录或未打开 App", "r"))
    else:
        print(cc("  [OK] Studio 已连接", "g"))

    return session, ok


async def deep_scan_via_cdp(session) -> dict:
    """通过 CDP 执行深度扫描"""
    from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio
    result = await execute_in_studio(session, _DEEP_SCAN_SCRIPT)
    if result.get("exceptionDetails"):
        print(cc(f"  [ERR] CDP 执行异常: {result['exceptionDetails']}", "r"))
        return {}
    value = (result.get("result") or {}).get("value") or {}
    return value


async def quick_snapshot(session) -> dict:
    """快速快照：仅取 Tree View 结构，不点击"""
    from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio
    script = """
    (() => {
        // execute_in_studio 已在 Studio iframe 内部执行，直接操作 document
        const tree = document.querySelector('[role="tree"], [class*="tree-view"], [class*="TreeView"], [class*="outline"]');
        if (!tree) return {error: 'no tree'};
        const items = [];
        function walk(el, d) {
            if (d > 10) return;
            for (const c of el.children || []) {
                const t = (c.textContent||'').trim();
                if (t && t.length < 80) items.push({name: t, depth: d});
                walk(c, d + 1);
            }
        }
        walk(tree, 0);
        return {items, count: items.length, maxDepth: items.length ? Math.max(...items.map(i=>i.depth)) : 0};
    })();
    """
    result = await execute_in_studio(session, script)
    return ((result.get("result") or {}).get("value") or {})


async def main() -> None:
    print(cc("+-----------------------------------------------------------+", "c"))
    print(cc("|  Test 2: Tree View 扫描                                   |", "c", "b"))
    print(cc("|  启动浏览器 → 你登录 → 按回车扫描 → 构建有向图 → 可视化    |", "d"))
    print(cc("+-----------------------------------------------------------+", "c"))

    # ── 先建立浏览器连接 ────────────────────────────────────
    print(cc("\n  [1/3] 正在启动浏览器...", "y"))
    session, studio_ok = await setup_browser_session()
    if studio_ok:
        print(cc("  [OK] 浏览器已启动，请在打开的页面中登录 PowerApps Studio", "g"))
    else:
        print(cc("  [WARN] 浏览器已启动但 Studio 未连接", "y"))

    print(cc("\n  > 登录完成后，按回车开始扫描 Tree View...\n", "c", "b"))
    input()

    # ── 用户按回车后，重试连接 Studio ───────────────────────
    if not studio_ok:
        print(cc("  [3/3] 重试连接 Studio...", "y"))
        from PowerfulApps.Agents.core.runtime import prepare_studio
        from PowerfulApps.Browser.cdp.studio_cdp import reset_studio_cache
        reset_studio_cache()
        studio_ok = await prepare_studio(session)
        if studio_ok:
            print(cc("  [OK] Studio 已连接！", "g"))
        else:
            print(cc("  [WARN] Studio 仍未连接，尝试直接扫描...", "y"))

    # ── 扫描 Tree View ───────────────────────────────────────
    print_header("1. 快速扫描 Tree View")
    props: dict = {}
    snapshot = await quick_snapshot(session)
    if snapshot.get("error"):
        print(cc(f"  [X] {snapshot['error']}", "r"))
        print(cc("  [!] 请确认：", "y"))
        print(cc("    1. 已在浏览器中登录 make.powerapps.com", "y"))
        print(cc("    2. 已打开一个 App 进行编辑", "y"))
        print(cc("    3. Tree View 面板没有被收起或遮挡", "y"))
    else:
        items = snapshot.get("items", [])
        count = snapshot.get("count", 0)
        max_depth = snapshot.get("maxDepth", 0)
        print(cc(f"  ✓ 共扫描到 {count} 个控件/屏幕，最大深度 {max_depth}", "g"))
        print()
        print(cc(f"  {'='*50}", "d"))
        print(cc(f"  {'Tree View 结构':^48}", "d", "b"))
        print(cc(f"  {'='*50}", "d"))

        # 按层级缩进打印
        for item in items:
            name = item.get("name", "")
            depth = item.get("depth", 0)
            indent = "  " + "  " * depth
            prefix = "📺" if "screen" in name.lower() else "  ▸"
            if depth == 0:
                print(cc(f"  {prefix} {name}", "c", "b"))
            else:
                print(cc(f"  {indent}{prefix} {name}", "d"))

        print(cc(f"  {'='*50}", "d"))

        # ── 深度扫描：获取属性 ──────────────────────────────
        print_header("2. 深度扫描：选中控件 → 提取关键属性")
        print(cc("  [*] 正在逐个扫描控件属性 (最多 20 个)...", "y"))
        deep = await deep_scan_via_cdp(session)
        props = deep.get("properties", {})
        if props:
            print(cc(f"  ✓ 共获取 {len(props)} 个控件的属性", "g"))
            for ctrl, p in list(props.items())[:15]:
                p_str = "; ".join(f"{k}={v!r}" for k, v in list(p.items())[:4])
                print(cc(f"     {ctrl}: {p_str}", "d"))
        else:
            print(cc("  [!] 未获取到属性（可能 Studio 属性面板需要手动打开）", "y"))

    # ── 构建有向图 ───────────────────────────────────────────
    print_header("3. 构建有向图")
    graph = ControlGraph()
    items = snapshot.get("items", [])
    # 确定屏幕：depth=0 的控件作为屏幕
    for item in items:
        name = item.get("name", "")
        depth = item.get("depth", 0)
        if depth == 0:
            graph.add_screen(name)
        elif depth == 1:
            # 第一级是屏幕的直接子控件
            parent_name = ""
            for p in reversed(items[:items.index(item)]):
                if p.get("depth", 0) == 0:
                    parent_name = p.get("name", "")
                    break
            graph.add_control(parent_name or "__unknown__", name, props.get(name, {}))
        else:
            # 更深层级
            graph.add_control("__unknown__", name, props.get(name, {}))

    # 自动检测引用
    for ctrl in props:
        if graph.graph.has_node(ctrl):
            graph.update_properties(ctrl, props[ctrl])
            graph.auto_detect_references(ctrl)

    stats = graph.stats()
    print(cc(f"  ✓ 有向图: {stats['screens']} 屏幕, {stats['controls']} 控件, {stats['edges']} 引用边", "g"))
    print(cc(f"  ✓ 图 JSON: {graph.to_json()[:1000]}", "g"))

    # ── 可视化输出 ───────────────────────────────────────────
    print_header("4. 有向图可视化输出")
    viz_path = try_viz_scan_result(graph)
    print(cc(f"  ✓ 可视化文件: {viz_path}", "g"))

    # ── 保存快照 ─────────────────────────────────────────────
    out_dir = _PROJECT_ROOT / "Test" / "probe_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    scan_out = out_dir / "tree_scan_result.json"
    result_data = {
        "snapshot": snapshot,
        "deep_scan": {k: v for k, v in props.items()},
        "graph_stats": stats,
        "graph_json": json.loads(graph.to_json()),
    }
    scan_out.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(cc(f"  ✓ 扫描结果已保存: {scan_out}", "g"))

    # ── 完成 ─────────────────────────────────────────────────
    print()
    print(cc(f"{'='*60}", "c"))
    print(cc("  Tree View 扫描完成 ✓", "g", "b"))
    print(cc(f"  {'='*60}", "c"))
    print()
    print(cc(f"  可视化文件: {viz_path}", "d"))
    print(cc(f"  数据快照: {scan_out}", "d"))

    # 关闭浏览器
    try:
        await session.stop()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())