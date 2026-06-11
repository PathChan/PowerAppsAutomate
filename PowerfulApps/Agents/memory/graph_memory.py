"""
有向图项目记忆系统。

使用 networkx.DiGraph 存储 PowerApps 项目结构：
- 每个控件/屏幕是一个节点（Node）
- 控件的属性（Text, OnSelect, Items 等）是节点的属性
- 控件之间的引用（如 Button1.Text → TextInput1.Text）是边（Edge）
- 边带有标签，标注是哪个属性发生了引用

序列化格式：
    graph.json 存储完整有向图
    给 LLM 喂数据时，提取目标节点及其 N 度邻居的拓扑子图
"""
from __future__ import annotations

import json
import logging
import re
import zlib
from pathlib import Path
from typing import Any

import networkx as nx

log = logging.getLogger("graph_memory")

# Power Fx 引用匹配模式：匹配 "ControlName.PropertyName" 形式的引用
_FX_REF_PATTERN = re.compile(
    r'(?<![a-zA-Z_.])'          # 前面不能是字母、点、下划线
    r'([A-Z][a-zA-Z0-9_]+)'     # 控件名：首字母大写的标识符
    r'\.'                        # 点
    r'([a-zA-Z][a-zA-Z0-9_]*)'  # 属性名
    r'(?![a-zA-Z_.])'           # 后面不能是字母、点、下划线
)

# Set 函数调用匹配：Set(varName, value) 或 Set(varName; value)
_SET_PATTERN = re.compile(
    r'\bSet\s*\(\s*'            # Set(
    r'([a-zA-Z_][a-zA-Z0-9_]*)' # 变量名
    r'\s*[,;]\s*'               # , 或 ;
    r'([^)]*)'                  # 值（到下一个 ) 为止，简单匹配）
    r'\)',
    re.IGNORECASE,
)

# 变量读取匹配：匹配公式中作为变量使用的标识符（排除函数调用和已知关键字）
_VAR_READ_PATTERN = re.compile(
    r'(?<![a-zA-Z_.(])'         # 前面不能是字母、点、下划线、左括号
    r'([a-z_][a-zA-Z0-9_]*)'    # 小写开头或下划线开头的标识符（变量风格）
    r'(?![a-zA-Z0-9_(])'        # 后面不能是字母数字、下划线、左括号
)


def parse_powerfx_references(formula: str) -> list[tuple[str, str]]:
    """解析 Power Fx 公式，提取所有 Control.Property 引用。

    Returns:
        list of (control_name, property_name)
    """
    if not formula or not isinstance(formula, str):
        return []
    # 排除常见关键字/函数名
    skip_names = {
        "Self", "Parent", "ThisItem", "ThisRecord",
        "First", "Last", "CountRows", "CountIf", "Sum", "Average",
        "Distinct", "Filter", "Search", "LookUp", "Sort", "SortByColumns",
        "GroupBy", "AddColumns", "DropColumns", "RenameColumns",
        "ShowColumns", "Collect", "ClearCollect", "Remove",
        "Patch", "Update", "UpdateIf", "Navigate", "Back",
        "Notify", "Set", "Reset", "Select", "App", "Screen",
        "true", "false", "blank", "If", "Switch", "ForAll",
        "With", "Sequence", "Rand", "Char", "Text", "Value",
        "Date", "Time", "Now", "Today", "IsBlank", "IsEmpty",
        "Concat", "Split", "Match", "StartsWith", "EndsWith",
        "Upper", "Lower", "Trim", "Len", "Left", "Right",
        "Mid", "Replace", "Substitute", "JSON", "Color",
        "ColorFade", "RGBA", "Hex2RGB", "Round", "RoundUp",
        "RoundDown", "Int", "Abs", "Mod", "Max", "Min",
        "Exp", "Ln", "Sqrt", "Power", "Pi", "Sin", "Cos",
        "Tan", "Asin", "Acos", "Atan",
        "Errors", "DataSourceInfo", "Defaults", "User",
        "Screen1", "Screen2", "Screen3",
    }
    results: list[tuple[str, str]] = []
    for match in _FX_REF_PATTERN.finditer(formula):
        ctrl = match.group(1)
        prop = match.group(2)
        if ctrl not in skip_names:
            results.append((ctrl, prop))
    return results


def parse_set_writes(formula: str) -> list[tuple[str, str]]:
    """解析 Set() 调用，提取 (变量名, 写入的值)。

    Returns:
        list of (variable_name, value)
    """
    if not formula or not isinstance(formula, str):
        return []
    results: list[tuple[str, str]] = []
    for match in _SET_PATTERN.finditer(formula):
        var_name = match.group(1)
        value = match.group(2).strip()
        results.append((var_name, value))
    return results


def parse_variable_reads(formula: str) -> list[str]:
    """解析公式中作为变量读取的标识符。

    只匹配小写/下划线开头的标识符（变量命名风格），
    排除函数名和关键字。

    Returns:
        list of variable_name
    """
    if not formula or not isinstance(formula, str):
        return []
    skip_vars = {
        "true", "false", "blank", "self", "parent",
        "thisitem", "thisrecord", "app", "screen",
    }
    results: list[str] = []
    for match in _VAR_READ_PATTERN.finditer(formula):
        var_name = match.group(1)
        if var_name not in skip_vars and len(var_name) > 1:
            results.append(var_name)
    return results


class ControlGraph:
    """有向图：节点=控件/屏幕，边=属性引用"""

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self._screens: set[str] = set()

    # ── 节点操作 ──────────────────────────────────────────────

    def add_screen(self, name: str) -> None:
        """添加一个屏幕节点。"""
        if not self.graph.has_node(name):
            self.graph.add_node(name, type="screen", properties={})
        self._screens.add(name)

    def add_control(self, screen: str, name: str, properties: dict[str, Any] | None = None) -> None:
        """添加一个控件节点，挂到所属屏幕下。

        screen 可以是屏幕名，未知时用 "__unknown__"。
        """
        if not self.graph.has_node(screen):
            self.add_screen(screen)
        if not self.graph.has_node(name):
            self.graph.add_node(
                name,
                type="control",
                screen=screen,
                properties=properties or {},
            )
        elif properties:
            existing = self.graph.nodes[name].get("properties", {})
            existing.update(properties)
            self.graph.nodes[name]["properties"] = existing

    def update_properties(self, control_name: str, properties: dict[str, Any]) -> None:
        """更新控件的属性。如果控件不存在则忽略。"""
        if not self.graph.has_node(control_name):
            log.warning("控件 %s 不在图中，忽略更新", control_name)
            return
        existing = self.graph.nodes[control_name].get("properties", {})
        existing.update(properties)
        self.graph.nodes[control_name]["properties"] = existing

    def update_property(self, control_name: str, prop_name: str, value: Any) -> None:
        """更新单个属性。"""
        self.update_properties(control_name, {prop_name: value})

    def remove_control(self, name: str) -> None:
        """删除控件及其所有边。"""
        if self.graph.has_node(name):
            self.graph.remove_node(name)

    def get_properties(self, control_name: str) -> dict[str, Any]:
        """获取控件属性。"""
        if not self.graph.has_node(control_name):
            return {}
        return self.graph.nodes[control_name].get("properties", {})

    def get_node_type(self, control_name: str) -> str | None:
        """获取节点类型：'screen' | 'control' | None"""
        if not self.graph.has_node(control_name):
            return None
        return self.graph.nodes[control_name].get("type")

    # ── 边操作 ──────────────────────────────────────────────

    def add_reference(
        self,
        source_control: str,
        target_control: str,
        *,
        source_property: str = "",
        target_property: str = "",
        formula: str = "",
    ) -> None:
        """添加一条引用边：source_control → target_control。

        Args:
            source_control: 引用方控件名 (如 Button1)
            target_control: 被引用方控件名 (如 TextInput1)
            source_property: 引用方的哪个属性发了引用 (如 OnSelect)
            target_property: 被引用了对方的哪个属性 (如 Text)
            formula: 完整的公式文本，用于溯源
        """
        self.graph.add_edge(
            source_control,
            target_control,
            source_property=source_property,
            target_property=target_property,
            formula=formula,
        )

    def remove_reference(self, source_control: str, target_control: str) -> None:
        """删除一条引用边。"""
        if self.graph.has_edge(source_control, target_control):
            self.graph.remove_edge(source_control, target_control)

    def get_references(self, control_name: str) -> list[dict[str, Any]]:
        """获取控件发出的所有引用（出边）。"""
        if not self.graph.has_node(control_name):
            return []
        edges = []
        for _, target, data in self.graph.out_edges(control_name, data=True):
            edges.append({
                "source": control_name,
                "target": target,
                **data,
            })
        return edges

    def get_dependents(self, control_name: str) -> list[dict[str, Any]]:
        """获取所有引用了此控件的其他控件（入边）。"""
        if not self.graph.has_node(control_name):
            return []
        edges = []
        for source, _, data in self.graph.in_edges(control_name, data=True):
            edges.append({
                "source": source,
                "target": control_name,
                **data,
            })
        return edges

    # ── 自动检测引用 ──────────────────────────────────────────

    def auto_detect_references(self, control_name: str) -> None:
        """扫描控件的所有属性公式，自动添加/更新引用边。"""
        if not self.graph.has_node(control_name):
            return
        props = self.graph.nodes[control_name].get("properties", {})
        # 先移除旧出边
        old_edges = list(self.graph.out_edges(control_name))
        for src, tgt in old_edges:
            self.graph.remove_edge(src, tgt)

        for prop_name, formula in props.items():
            if not isinstance(formula, str):
                continue
            refs = parse_powerfx_references(formula)
            for ref_ctrl, ref_prop in refs:
                if self.graph.has_node(ref_ctrl) or ref_ctrl in self._screens:
                    self.add_reference(
                        source_control=control_name,
                        target_control=ref_ctrl,
                        source_property=prop_name,
                        target_property=ref_prop,
                        formula=formula,
                    )

    def auto_detect_all_references(self) -> None:
        """全图重新扫描引用关系。"""
        for node in list(self.graph.nodes()):
            if self.graph.nodes[node].get("type") == "control":
                self.auto_detect_references(node)

    # ── 变量因果链 ──────────────────────────────────────────

    def auto_detect_variable_chain(self, control_name: str) -> None:
        """检测控件的 Set() 写入和变量读取，建立变量因果链边。

        因果链：控件A.OnSelect 写入了变量 v → 控件B.Visible 读取了变量 v
        建立边 A → B，边属性记录 relation_type="variable_chain" 和变量名。
        """
        if not self.graph.has_node(control_name):
            return
        props = self.graph.nodes[control_name].get("properties", {})
        node_type = self.graph.nodes[control_name].get("type", "")

        # 移除该控件的旧变量链边（保留 formula_ref 边）
        old_edges = list(self.graph.out_edges(control_name, data=True))
        for src, tgt, d in old_edges:
            if d.get("relation") == "variable_chain":
                self.graph.remove_edge(src, tgt)

        for prop_name, formula in props.items():
            if not isinstance(formula, str):
                continue

            # 检测 Set() 写入
            writes = parse_set_writes(formula)
            for var_name, value in writes:
                # 找所有读取了这个变量的控件
                for other in list(self.graph.nodes()):
                    if other == control_name:
                        continue
                    other_props = self.graph.nodes[other].get("properties", {})
                    for op_name, op_formula in other_props.items():
                        if not isinstance(op_formula, str):
                            continue
                        reads = parse_variable_reads(op_formula)
                        if var_name in reads:
                            self.graph.add_edge(
                                control_name, other,
                                relation="variable_chain",
                                variable=var_name,
                                source_property=prop_name,
                                target_property=op_name,
                                source_action="write",
                                target_action="read",
                                source_formula=formula,
                                target_formula=op_formula,
                            )

            # 检测变量读取（找谁写入了这个变量）
            reads = parse_variable_reads(formula)
            for var_name in reads:
                for other in list(self.graph.nodes()):
                    if other == control_name:
                        continue
                    other_props = self.graph.nodes[other].get("properties", {})
                    for op_name, op_formula in other_props.items():
                        if not isinstance(op_formula, str):
                            continue
                        writes2 = parse_set_writes(op_formula)
                        for w_var, w_val in writes2:
                            if w_var == var_name:
                                self.graph.add_edge(
                                    other, control_name,
                                    relation="variable_chain",
                                    variable=var_name,
                                    source_property=op_name,
                                    target_property=prop_name,
                                    source_action="write",
                                    target_action="read",
                                    source_formula=op_formula,
                                    target_formula=formula,
                                )

    def auto_detect_all_variable_chains(self) -> None:
        """全图扫描变量因果链。"""
        for node in list(self.graph.nodes()):
            self.auto_detect_variable_chain(node)

    # ── 查询：邻域提取 ──────────────────────────────────────────

    def get_neighborhood(
        self,
        control_name: str,
        degree: int = 2,
    ) -> nx.DiGraph:
        """提取目标控件的 N 度邻居子图。

        包含：
        - 目标节点本身
        - 所有入边邻居（谁引用了我）
        - 所有出边邻居（我引用了谁）
        - 递归扩展到 degree 层
        """
        if not self.graph.has_node(control_name):
            return nx.DiGraph()

        # BFS 收集邻居
        neighbors: set[str] = {control_name}
        current_ring: set[str] = {control_name}

        for _ in range(degree):
            next_ring: set[str] = set()
            for node in current_ring:
                for _, target in self.graph.out_edges(node):
                    if target not in neighbors:
                        next_ring.add(target)
                for source, _ in self.graph.in_edges(node):
                    if source not in neighbors:
                        next_ring.add(source)
            neighbors.update(next_ring)
            current_ring = next_ring
            if not current_ring:
                break

        return self.graph.subgraph(neighbors).copy()

    def serialize_neighborhood(
        self,
        control_name: str,
        degree: int = 2,
    ) -> dict[str, Any]:
        """提取邻域并序列化为 JSON 结构，供 LLM 使用。"""
        subgraph = self.get_neighborhood(control_name, degree)
        if not subgraph.nodes:
            return {"error": f"控件 {control_name} 不在图中", "nodes": [], "edges": []}

        nodes_data = []
        for node, attrs in subgraph.nodes(data=True):
            entry: dict[str, Any] = {"name": node}
            entry.update(attrs)
            nodes_data.append(entry)

        edges_data = []
        for src, tgt, attrs in subgraph.edges(data=True):
            edges_data.append({
                "source": src,
                "target": tgt,
                **attrs,
            })

        return {
            "center": control_name,
            "degree": degree,
            "nodes": nodes_data,
            "edges": edges_data,
        }

    def serialize_neighborhood_pseudocode(
        self,
        control_name: str,
        degree: int = 2,
    ) -> str:
        """提取邻域并格式化为伪代码，更适合 LLM 理解。"""
        subgraph = self.get_neighborhood(control_name, degree)
        if not subgraph.nodes:
            return f"// 控件 {control_name} 不在项目记忆图中"

        lines = [f"// ===== {control_name} 的 {degree} 度关系拓扑 =====", ""]

        # 按类型分组：屏幕 / 控件
        screens = []
        controls = []
        for node, attrs in subgraph.nodes(data=True):
            if attrs.get("type") == "screen":
                screens.append(node)
            else:
                controls.append(node)

        if screens:
            lines.append("// 📺 屏幕")
            for s in screens:
                lines.append(f"Screen {s} {{ }}")
            lines.append("")

        if controls:
            lines.append("// 🧩 控件")
            for c in controls:
                props = subgraph.nodes[c].get("properties", {})
                if props:
                    props_str = ", ".join(f"{k}: {v!r}" for k, v in props.items())
                    lines.append(f"Control {c} {{ {props_str} }}")
                else:
                    lines.append(f"Control {c} {{ }}")
            lines.append("")

        if subgraph.edges:
            lines.append("// 🔗 引用关系（有向边）")
            for src, tgt, attrs in subgraph.edges(data=True):
                relation = attrs.get("relation", "")
                sp = attrs.get("source_property", "?")
                tp = attrs.get("target_property", "?")
                formula = attrs.get("formula", "")
                if relation == "variable_chain":
                    var = attrs.get("variable", "?")
                    lines.append(f"{src}.{sp} | {var} | {tgt}.{tp}")
                elif formula:
                    lines.append(f"{src}.{sp} | {tgt}.{tp}  // {formula}")
                else:
                    lines.append(f"{src}.{sp} | {tgt}.{tp}")
            lines.append("")

        return "\n".join(lines)

    # ── 序列化 ──────────────────────────────────────────────

    def to_json(self) -> str:
        """将图序列化为 JSON。"""
        data = {
            "screens": sorted(self._screens),
            "nodes": [],
            "edges": [],
        }
        for node, attrs in self.graph.nodes(data=True):
            data["nodes"].append({
                "id": node,
                "type": attrs.get("type", "unknown"),
                "screen": attrs.get("screen", ""),
                "properties": attrs.get("properties", {}),
            })
        for src, tgt, attrs in self.graph.edges(data=True):
            data["edges"].append({
                "source": src,
                "target": tgt,
                "relation": attrs.get("relation", ""),
                "variable": attrs.get("variable", ""),
                "source_property": attrs.get("source_property", ""),
                "target_property": attrs.get("target_property", ""),
                "formula": attrs.get("formula", ""),
            })
        return json.dumps(data, ensure_ascii=False, indent=2)

    def to_json_compact(self) -> str:
        """紧凑 JSON（压缩后 base85 编码），适合存文件。"""
        raw = self.to_json().encode("utf-8")
        compressed = zlib.compress(raw, level=6)
        import base64
        return base64.b85encode(compressed).decode("ascii")

    @classmethod
    def from_json(cls, text: str) -> "ControlGraph":
        """从 JSON 反序列化图。"""
        data = json.loads(text)
        cg = cls()
        for s in data.get("screens", []):
            cg.add_screen(s)
        for n in data.get("nodes", []):
            node_type = n.get("type", "control")
            if node_type == "screen":
                cg.add_screen(n["id"])
            else:
                cg.add_control(
                    screen=n.get("screen", "__unknown__"),
                    name=n["id"],
                    properties=n.get("properties", {}),
                )
        for e in data.get("edges", []):
            cg.add_reference(
                source_control=e["source"],
                target_control=e["target"],
                source_property=e.get("source_property", ""),
                target_property=e.get("target_property", ""),
                formula=e.get("formula", ""),
            )
            # 恢复 variable_chain 边上的额外属性
            if e.get("relation") or e.get("variable"):
                cg.graph[e["source"]][e["target"]].update({
                    "relation": e.get("relation", ""),
                    "variable": e.get("variable", ""),
                    "source_action": e.get("source_action", ""),
                    "target_action": e.get("target_action", ""),
                    "source_formula": e.get("source_formula", ""),
                    "target_formula": e.get("target_formula", ""),
                })
        return cg

    @classmethod
    def from_json_compact(cls, text: str) -> "ControlGraph":
        """从压缩编码反序列化。"""
        import base64
        compressed = base64.b85decode(text.encode("ascii"))
        raw = zlib.decompress(compressed)
        return cls.from_json(raw.decode("utf-8"))

    # ── 文件读写 ──────────────────────────────────────────────

    def save(self, path: Path | str) -> None:
        """保存到文件（压缩格式）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json_compact(), encoding="utf-8")
        log.info("图已保存到 %s (%d 节点, %d 边)",
                 path, self.graph.number_of_nodes(), self.graph.number_of_edges())

    @classmethod
    def load(cls, path: Path | str) -> "ControlGraph":
        """从文件加载。"""
        path = Path(path)
        if not path.exists():
            log.info("图文件 %s 不存在，返回空图", path)
            return cls()
        text = path.read_text(encoding="utf-8").strip()
        # 自动检测格式：JSON 对象或 base85 压缩
        if text.startswith("{"):
            return cls.from_json(text)
        return cls.from_json_compact(text)

    # ── 统计 ──────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "screens": len(self._screens),
            "controls": self.graph.number_of_nodes() - len(self._screens),
            "edges": self.graph.number_of_edges(),
            "screens_list": sorted(self._screens),
        }

    def list_controls_on_screen(self, screen: str) -> list[str]:
        """列出某屏幕上的所有控件名。"""
        return sorted([
            n for n, a in self.graph.nodes(data=True)
            if a.get("screen") == screen and a.get("type") == "control"
        ])

    def get_all_controls(self) -> list[str]:
        """获取所有控件名。"""
        return sorted([
            n for n, a in self.graph.nodes(data=True)
            if a.get("type") == "control"
        ])