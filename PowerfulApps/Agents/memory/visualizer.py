"""
有向图记忆可视化。

提供 ControlGraph 的图形化导出（PNG），用于调试和查看项目记忆结构：
- 屏幕/控件节点分色
- 引用边带属性标签
- 中文字体支持
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from .graph_memory import ControlGraph


def _setup_chinese_font():
    """配置 matplotlib 支持中文显示。"""
    import matplotlib
    import matplotlib.font_manager as fm
    cn_font_names = [
        "Microsoft YaHei", "SimHei", "DengXian", "FangSong",
        "KaiTi", "Microsoft JhengHei",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in cn_font_names:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    for f in fm.fontManager.ttflist:
        try:
            if any(ord(c) > 0x4E00 for c in f.name):
                matplotlib.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
                matplotlib.rcParams["axes.unicode_minus"] = False
                return f.name
        except Exception:
            continue
    return None


def truncate_name(name: str, max_len: int = 24) -> str:
    """截断过长的组件名，保留可读性。"""
    if len(name) <= max_len:
        return name
    parts = []
    current = ""
    for ch in name:
        if ch.isupper() and current:
            parts.append(current)
            current = ch
        else:
            current += ch
    if current:
        parts.append(current)
    if len(parts) >= 4:
        shortened = "".join(parts[:2]) + ".." + "".join(parts[-2:])
        if len(shortened) < len(name):
            return shortened
    return name[:max_len - 3] + "..."


def visualize_graph(graph: ControlGraph, output_path: str | None = None) -> str:
    """将 ControlGraph 导出为 PNG 图片，返回图片路径。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    if graph.graph.number_of_nodes() == 0:
        return "[空图]"

    _setup_chinese_font()

    G = graph.graph
    pos = nx.spring_layout(G, seed=42, k=2.5, iterations=50)

    n_nodes = G.number_of_nodes()
    figsize = (max(14, n_nodes * 1.2), max(9, n_nodes * 0.8))
    plt.figure(figsize=(figsize))

    screen_nodes = [n for n, a in G.nodes(data=True) if a.get("type") == "screen"]
    control_nodes = [n for n, a in G.nodes(data=True) if a.get("type") == "control"]

    if screen_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=screen_nodes,
            node_color="#4A90D9", node_size=3500, node_shape="s",
            edgecolors="#2B6CB0", linewidths=2,
        )
    if control_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=control_nodes,
            node_color="#7BC47F", node_size=2200, node_shape="o",
            edgecolors="#4A9E4A", linewidths=1.5,
        )

    # 绘制边（分两种类型）
    edge_labels = {}
    formula_edges = []
    var_chain_edges = []
    if G.number_of_edges() > 0:
        for u, v, d in G.edges(data=True):
            if d.get("relation") == "variable_chain":
                var_chain_edges.append((u, v))
            else:
                formula_edges.append((u, v))

        # 公式引用边：灰色
        if formula_edges:
            nx.draw_networkx_edges(
                G, pos, edgelist=formula_edges,
                edge_color="#888888", arrows=True, arrowsize=18,
                width=1.5, alpha=0.6,
            )
        # 变量因果链边：橙色虚线，加粗
        if var_chain_edges:
            nx.draw_networkx_edges(
                G, pos, edgelist=var_chain_edges,
                edge_color="#E87722", arrows=True, arrowsize=22,
                width=2.2, alpha=0.85, style="dashed",
            )

        for u, v, d in G.edges(data=True):
            sp = d.get("source_property", "")
            tp = d.get("target_property", "")
            if d.get("relation") == "variable_chain":
                var = d.get("variable", "")
                label = f"{sp} | {var} | {tp}" if var else f"{sp} | {tp}"
            elif sp and tp:
                label = f"{sp} | {tp}"
            elif sp or tp:
                label = sp or tp
            else:
                continue
            edge_labels[(u, v)] = label

        if edge_labels:
            nx.draw_networkx_edge_labels(
                G, pos, edge_labels=edge_labels,
                font_size=6, font_color="#555555",
                alpha=0.85, label_pos=0.5,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7),
            )

    labels = {}
    for n in G.nodes():
        node_type = G.nodes[n].get("type", "")
        props = G.nodes[n].get("properties", {})
        display_name = truncate_name(n, max_len=22)

        key_props = {}
        for k in ("Text", "Value", "Label", "Default", "Items", "Name"):
            if k in props:
                v = str(props[k])
                if len(v) > 18:
                    v = v[:15] + "..."
                key_props[k] = v
                if len(key_props) >= 2:
                    break

        if node_type == "screen":
            labels[n] = f"[屏幕] {display_name}"
        elif key_props:
            props_str = " | ".join(f"{k}={v}" for k, v in key_props.items())
            labels[n] = f"{display_name}\n{props_str}"
        else:
            labels[n] = display_name

    nx.draw_networkx_labels(G, pos, labels, font_size=8)

    legend_elements = [
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="#4A90D9",
                   markersize=14, label=f"屏幕 ({len(screen_nodes)})"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#7BC47F",
                   markersize=11, label=f"控件 ({len(control_nodes)})"),
    ]
    if formula_edges:
        legend_elements.append(
            plt.Line2D([0], [0], color="#888888", linewidth=1.8,
                       label=f"公式引用 ({len(formula_edges)})")
        )
    if var_chain_edges:
        legend_elements.append(
            plt.Line2D([0], [0], color="#E87722", linewidth=2.5, linestyle="dashed",
                       label=f"变量因果链 ({len(var_chain_edges)})")
        )
    plt.legend(handles=legend_elements, loc="upper left",
               fontsize=9, framealpha=0.85, edgecolor="#CCCCCC")

    has_cn = any(ord(c) > 0x4E00 for c in str(labels.values()))
    title = (
        f"PowerApps 项目有向图 | {n_nodes} 节点 | {G.number_of_edges()} 引用"
        if not has_cn else
        f"PowerApps 项目有向图 | {n_nodes} 节点 | {G.number_of_edges()} 条引用"
    )
    plt.title(title, fontsize=12, pad=16)

    out = output_path or str(
        Path(tempfile.gettempdir()) / f"powerapps_graph_{datetime.now().strftime('%H%M%S')}.png"
    )
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(out, dpi=180, bbox_inches="tight")
    plt.close()
    return out