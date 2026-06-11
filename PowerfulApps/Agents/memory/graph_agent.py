"""LangGraph 工作流：管理 ControlGraph 记忆的增删改查。"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

log = logging.getLogger("graph_agent")


class GraphState(TypedDict):
    """LangGraph 的状态"""
    action: str                           # 当前动作：init | update | query | detect | traverse | watch
    control_name: str                     # 操作的控件名
    properties: dict[str, Any]            # 要更新的属性
    formula: str                          # 公式文本
    source_control: str                   # 引用源
    target_control: str                   # 引用目标
    source_property: str                  # 源属性
    target_property: str                  # 目标属性
    query_result: str                     # 查询结果（JSON 或伪代码）
    degree: int                           # 邻域搜索深度
    error: str                            # 错误信息


def _make_initial_state() -> GraphState:
    return {
        "action": "",
        "control_name": "",
        "properties": {},
        "formula": "",
        "source_control": "",
        "target_control": "",
        "source_property": "",
        "target_property": "",
        "query_result": "",
        "degree": 2,
        "error": "",
    }


def create_graph_agent() -> StateGraph:
    """创建 LangGraph 工作流"""

    def route_action(state: GraphState) -> Literal["update_properties", "query_neighborhood", "auto_detect", "add_reference", "remove_control", "idle"]:
        return state.get("action", "idle")

    def node_update_properties(state: GraphState) -> dict:
        from .graph_memory import ControlGraph
        cg = ControlGraph.load(_graph_path())
        name = state.get("control_name", "")
        props = state.get("properties", {})
        if name and props:
            cg.update_properties(name, props)
            cg.save(_graph_path())
            return {"query_result": f"已更新 {name} 的属性: {json.dumps(props, ensure_ascii=False)}"}
        return {"query_result": "缺少 control_name 或 properties"}

    def node_query_neighborhood(state: GraphState) -> dict:
        from .graph_memory import ControlGraph
        cg = ControlGraph.load(_graph_path())
        name = state.get("control_name", "")
        degree = state.get("degree", 2)
        if not name:
            return {"query_result": "缺少 control_name"}
        result = cg.serialize_neighborhood_pseudocode(name, degree)
        return {"query_result": result}

    def node_auto_detect(state: GraphState) -> dict:
        from .graph_memory import ControlGraph
        cg = ControlGraph.load(_graph_path())
        name = state.get("control_name", "")
        if name:
            cg.auto_detect_references(name)
            refs = cg.get_references(name)
            deps = cg.get_dependents(name)
            cg.save(_graph_path())
            return {"query_result": f"已更新 {name} 的引用关系。发出 {len(refs)} 条引用，被 {len(deps)} 个控件引用"}
        cg.auto_detect_all_references()
        cg.save(_graph_path())
        return {"query_result": f"已全图扫描引用关系。当前 {cg.graph.number_of_edges()} 条边"}

    def node_add_reference(state: GraphState) -> dict:
        from .graph_memory import ControlGraph
        cg = ControlGraph.load(_graph_path())
        cg.add_reference(
            state.get("source_control", ""),
            state.get("target_control", ""),
            source_property=state.get("source_property", ""),
            target_property=state.get("target_property", ""),
            formula=state.get("formula", ""),
        )
        cg.save(_graph_path())
        return {"query_result": f"已添加引用: {state['source_control']} -> {state['target_control']}"}

    def node_remove_control(state: GraphState) -> dict:
        from .graph_memory import ControlGraph
        cg = ControlGraph.load(_graph_path())
        cg.remove_control(state.get("control_name", ""))
        cg.save(_graph_path())
        return {"query_result": f"已删除控件: {state['control_name']}"}

    def node_idle(state: GraphState) -> dict:
        return {"query_result": "空闲状态，无操作"}

    builder = StateGraph(GraphState)
    builder.add_node("update_properties", node_update_properties)
    builder.add_node("query_neighborhood", node_query_neighborhood)
    builder.add_node("auto_detect", node_auto_detect)
    builder.add_node("add_reference", node_add_reference)
    builder.add_node("remove_control", node_remove_control)
    builder.add_node("idle", node_idle)
    builder.set_conditional_edge_source("idle", route_action)
    builder.add_edge("update_properties", END)
    builder.add_edge("query_neighborhood", END)
    builder.add_edge("auto_detect", END)
    builder.add_edge("add_reference", END)
    builder.add_edge("remove_control", END)
    builder.set_entry_point("idle")
    return builder.compile()


def _graph_path() -> str:
    import os
    return os.getenv("POWERAPPS_GRAPH_PATH", "PowerfulApps/Agents/.memory/project_graph.bin")


# 便捷函数
async def update_control(name: str, properties: dict) -> str:
    agent = create_graph_agent()
    result = await agent.ainvoke({"action": "update_properties", "control_name": name, "properties": properties})
    return result.get("query_result", "")

async def query_neighborhood(name: str, degree: int = 2) -> str:
    agent = create_graph_agent()
    result = await agent.ainvoke({"action": "query_neighborhood", "control_name": name, "degree": degree})
    return result.get("query_result", "")

async def auto_detect(control: str = "") -> str:
    agent = create_graph_agent()
    result = await agent.ainvoke({"action": "auto_detect", "control_name": control})
    return result.get("query_result", "")