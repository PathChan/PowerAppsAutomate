"""
Test 1: LangGraph 有向图记忆系统测试
======================================
测试 ControlGraph 的核心功能：
  - 节点（屏幕/控件）的增删改
  - 边（属性引用）的自动检测
  - N 度邻居提取 → 伪代码序列化 → 喂给 LLM
  - 有向图可视化输出

运行方式：准备好后按回车执行
    uv run python .\Test\test_langgraph_memory.py
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

from PowerfulApps.Agents.memory.graph_memory import (
    ControlGraph,
    parse_powerfx_references,
    parse_set_writes,
    parse_variable_reads,
)
from PowerfulApps.Agents.memory.graph_agent import (
    create_graph_agent,
    query_neighborhood,
    update_control,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_graph")

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


def build_mock_project() -> ControlGraph:
    """创建一个模拟的 PowerApps 项目有向图"""
    g = ControlGraph()

    # ── 屏幕 ──────────────────────────────────────────────
    g.add_screen("Screen1")
    g.add_screen("Screen2")

    # ── Screen1 上的控件 ───────────────────────────────────
    g.add_control("Screen1", "Label1", {
        "Text": "\"你好世界\"",
        "X": "100",
        "Y": "200",
        "Size": "24",
        "Color": "RGBA(0, 0, 0, 1)",
    })
    g.add_control("Screen1", "TextInput1", {
        "Text": "\"请输入\"",
        "X": "100",
        "Y": "300",
        "Width": "300",
    })
    g.add_control("Screen1", "Button1", {
        "Text": "\"提交\"",
        "X": "100",
        "Y": "400",
        "OnSelect": "Set(VarText, TextInput1.Text); Navigate(Screen2)",
        "Width": "150",
    })
    g.add_control("Screen1", "Dropdown1", {
        "Items": "[\"选项A\", \"选项B\", \"选项C\"]",
        "X": "100",
        "Y": "500",
        "Width": "200",
    })
    g.add_control("Screen1", "Label2", {
        "Text": "Dropdown1.Selected.Value",
        "X": "100",
        "Y": "600",
        "Size": "18",
    })

    # ── Screen2 上的控件 ──────────────────────────────────
    g.add_control("Screen2", "Label3", {
        "Text": "VarText",
        "X": "100",
        "Y": "200",
        "Size": "32",
        "Color": "RGBA(255, 0, 0, 1)",
    })
    g.add_control("Screen2", "Button2", {
        "Text": "\"返回\"",
        "X": "100",
        "Y": "400",
        "OnSelect": "Navigate(Screen1)",
    })

    # ── 手动添加引用边 ─────────────────────────────────────
    g.add_reference("Button1", "TextInput1",
                    source_property="OnSelect", target_property="Text",
                    formula="Set(VarText, TextInput1.Text); Navigate(Screen2)")
    g.add_reference("Button1", "Screen2",
                    source_property="OnSelect", target_property="",
                    formula="Set(VarText, TextInput1.Text); Navigate(Screen2)")
    g.add_reference("Label2", "Dropdown1",
                    source_property="Text", target_property="Selected",
                    formula="Dropdown1.Selected.Value")

    # ── 自动检测引用（从属性公式中解析） ────────────────────
    g.auto_detect_references("Button1")
    g.auto_detect_references("Label2")

    return g


def try_visualize_graph(graph: ControlGraph, output_path: str | None = None) -> str:
    """用 matplotlib 输出有向图 PNG（委托至 memory.visualizer）"""
    from PowerfulApps.Agents.memory.visualizer import visualize_graph
    return visualize_graph(graph, output_path)


async def test_langgraph_agent() -> None:
    """测试 langgraph agent 的 update 和 query 流程"""
    print_header("测试 LangGraph Agent 工作流")

    # 设置环境变量，让 graph_agent 能找到图
    import tempfile
    tmp = tempfile.mktemp(suffix=".bin")
    os.environ["POWERAPPS_GRAPH_PATH"] = tmp

    try:
        # 先建图并保存
        g = build_mock_project()
        g.save(Path(tmp))

        # 测试 query
        print(cc("\n  >> query_neighborhood(Button1, degree=2)", "y"))
        result = await query_neighborhood("Button1", 2)
        print(cc(f"  {result}", "g"))

        print(cc("\n  >> update_control(Button1, {'Text': '新文本'})", "y"))
        upd = await update_control("Button1", {"Text": "新文本"})
        print(cc(f"  {upd}", "g"))

        # 验证更新是否生效
        reloaded = ControlGraph.load(Path(tmp))
        props = reloaded.get_properties("Button1")
        assert props.get("Text") == "新文本", f"属性更新失败: {props}"
        print(cc("  ✓ 属性更新验证通过", "g"))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


async def main() -> None:
    print(cc("+-----------------------------------------------------------+", "c"))
    print(cc("|  Test 1: LangGraph 有向图记忆系统                          |", "c", "b"))
    print(cc("|  按回车开始测试...                                         |", "d"))
    print(cc("+-----------------------------------------------------------+", "c"))

    input("  > 按回车执行测试...")

    # ═══════════════ 1. 创建有向图 ═══════════════
    print_header("1. 构建模拟 PowerApps 项目有向图")
    graph = build_mock_project()
    stats = graph.stats()
    print(cc(f"  ✓ 屏幕: {stats['screens']} 个 → {stats['screens_list']}", "g"))
    print(cc(f"  ✓ 控件: {stats['controls']} 个", "g"))
    print(cc(f"  ✓ 引用边: {stats['edges']} 条", "g"))

    # ═══════════════ 2. 邻域提取测试 ═══════════════
    print_header("2. 邻域提取 → LLM 伪代码 (Button1 的 2 度拓扑)")
    pseudocode = graph.serialize_neighborhood_pseudocode("Button1", degree=2)
    print(cc(f"\n{pseudocode}\n", "g"))

    print_header("2b. Label2 的邻域拓扑")
    pseudocode2 = graph.serialize_neighborhood_pseudocode("Label2", degree=1)
    print(cc(f"\n{pseudocode2}\n", "g"))

    # ═══════════════ 3. JSON 序列化 ═══════════════
    print_header("3. 完整图 JSON 序列化（前 2000 字符）")
    j = graph.to_json()
    print(cc(f"\n{j[:2000]}...\n", "y"))

    # ═══════════════ 4. 压缩保存 & 重载 ═══════════════
    print_header("4. 压缩保存 & 重载验证")
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        save_path = f.name
    try:
        graph.save(Path(save_path))
        size = os.path.getsize(save_path)
        print(cc(f"  ✓ 压缩保存: {save_path} ({size} bytes)", "g"))

        reloaded = ControlGraph.load(Path(save_path))
        rs = reloaded.stats()
        assert rs["screens"] == stats["screens"]
        assert rs["controls"] == stats["controls"]
        assert rs["edges"] == stats["edges"]
        print(cc(f"  ✓ 重载验证通过: {rs['screens']}S {rs['controls']}C {rs['edges']}E", "g"))
    finally:
        if os.path.exists(save_path):
            os.unlink(save_path)

    # ═══════════════ 5. 有向图可视化 ═══════════════
    print_header("5. 有向图可视化输出")
    viz_path = try_visualize_graph(graph)
    print(cc(f"  ✓ 可视化文件: {viz_path}", "g"))

    # ═══════════════ 6. 引用解析测试 ═══════════════
    print_header("6. Power Fx 引用解析测试")
    formulas = [
        ("Set(VarText, TextInput1.Text); Navigate(Screen2)",
         [("TextInput1", "Text"), ("Screen2", "")]),
        ("Dropdown1.Selected.Value",
         [("Dropdown1", "Selected")]),
        "Collect(DataSource1, {Value: Slider1.Value})",
    ]
    for formula in formulas:
        if isinstance(formula, tuple):
            f_text, expected = formula
        else:
            f_text = formula
            expected = None
        refs = parse_powerfx_references(f_text)
        print(cc(f"  Formula: {f_text}", "d"))
        print(cc(f"    → 引用: {refs}", "g"))

    # ═══════════════ 7. LangGraph Agent 测试 ═══════════════
    await test_langgraph_agent()

    # ═══════════════ 8. 变量因果链测试 ═══════════════
    print_header("8. 变量因果链检测")
    # 先检测 Set() 写入解析
    f1 = "Set(VarText, TextInput1.Text); Navigate(Screen2)"
    writes = parse_set_writes(f1)
    print(cc(f"  Set() 写入解析: {f1}", "d"))
    print(cc(f"    → 写入变量: {writes}", "g"))

    # 检测变量读取解析
    f2 = "VarText"
    reads = parse_variable_reads(f2)
    print(cc(f"  变量读取解析: {f2}", "d"))
    print(cc(f"    → 读取变量: {reads}", "g"))

    # 运行变量因果链检测
    before_edges = graph.stats()["edges"]
    graph.auto_detect_all_variable_chains()
    after_edges = graph.stats()["edges"]
    print(cc(f"  变量链检测前边数: {before_edges}, 检测后: {after_edges}", "y"))

    # 检查变量链边
    var_edges = [
        (u, v, d) for u, v, d in graph.graph.edges(data=True)
        if d.get("relation") == "variable_chain"
    ]
    print(cc(f"  变量因果链边: {len(var_edges)} 条", "g"))
    for u, v, d in var_edges:
        var = d.get("variable", "?")
        sp = d.get("source_property", "?")
        tp = d.get("target_property", "?")
        print(cc(f"    {u}.{sp} --[{var}]--> {v}.{tp}", "c"))

    # 再次可视化（含变量链边）
    viz_path2 = try_visualize_graph(graph)
    print(cc(f"  ✓ 含变量链可视化: {viz_path2}", "g"))

    # ═══════════════ 完成 ═════════════════════
    print()
    print(cc(f"{'='*60}", "c"))
    print(cc("  全部测试通过 ✓", "g", "b"))
    print(cc(f"{'='*60}", "c"))
    print()
    print(cc("  可视化文件位置 ↑ 双击打开即可查看有向图", "d"))
    print()


if __name__ == "__main__":
    asyncio.run(main())