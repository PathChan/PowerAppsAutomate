"""PowerApps Agent 工具注册。

工具优先级（LLM 按此顺序使用）：
  1. insert_component_and_set_formula / set_property_formula  — 日常修改
  2. search_and_select_tree_item                             — 在树视图中搜索并选中控件
  3. get_tree_structure / search_in_tree / click_tree_item   — 查看/定位控件
  4. click_sidebar_tab                                       — 左侧栏导航（树视图/插入/数据...）
  5. traverse_all_properties / get_property_options           — 查看属性
  6. get_dom_snapshot                                         — 兜底（最后手段）
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from PowerfulApps.MocProcess.actions import formula_bar, insert_menu
from PowerfulApps.MocProcess.actions.click_funcInput import ClickFuncInputParams, click_func_input
from PowerfulApps.MocProcess.actions.click_sidebar_tab import click_sidebar_tab
from PowerfulApps.MocProcess.actions.search_element import (
    click_tree_item,
    search_in_tree_view,
)
from PowerfulApps.MocProcess.chains.insert_and_set_formula import (
    insert_component_and_set_formula,
    scan_insert_menu,
    traverse_all_properties,
)
from PowerfulApps.MocProcess.chains.search_and_select_tree_item import (
    search_and_select_tree_item,
)
from PowerfulApps.MocProcess.chains.set_property_formula import set_property_formula


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Awaitable[Any]] | Callable[..., Any]


class PowerAppsToolRegistry:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.tools = self._build_tools()

    def _build_tools(self) -> dict[str, ToolSpec]:
        return {
            # ── 日常操作 ──────────────────────────────────────
            "insert_component_and_set_formula": ToolSpec(
                "insert_component_and_set_formula",
                "插入一个控件，并立即设置它的某个属性公式。执行后新控件通常保持选中。例如：插入 按钮，设置 Text='提交'。",
                {"type": "object", "properties": {"component": {"type": "string", "description": "控件名称，如 按钮、标签、文本输入"}, "property_name": {"type": "string", "description": "属性名，如 Text、OnSelect"}, "formula": {"type": "string", "description": "Power Fx 公式"}}, "required": ["component", "property_name", "formula"]},
                lambda component, property_name, formula: insert_component_and_set_formula(self.session, component=component, property_name=property_name, formula=formula),
            ),
            "set_property_formula": ToolSpec(
                "set_property_formula",
                "给当前选中的控件设置某个属性公式（不插入新控件）。必须先确定控件已被选中。",
                {"type": "object", "properties": {"property_name": {"type": "string", "description": "属性名，如 Text、OnSelect、X、Y、Fill、Size"}, "formula": {"type": "string", "description": "Power Fx 公式"}}, "required": ["property_name", "formula"]},
                lambda property_name, formula: set_property_formula(self.session, property_name=property_name, formula=formula),
            ),
            # ── 搜索并选中控件 ──────────────────────────────
            "search_and_select_tree_item": ToolSpec(
                "search_and_select_tree_item",
                "在左侧 Tree View 中搜索控件并点击选中。自动打开树视图 tab。适合：不知道控件在树中的精确位置时，按名称搜索并跳转。",
                {"type": "object", "properties": {"keyword": {"type": "string", "description": "搜索关键词，如 'Button'、'TextInput1'、'Screen1'"}, "target_name": {"type": "string", "description": "（可选）要点击的控件名称，默认同 keyword"}}, "required": ["keyword"]},
                lambda keyword, target_name=None: search_and_select_tree_item(self.session, keyword=keyword, target_name=target_name),
            ),
            # ── Tree View 查看 ────────────────────────────────
            "get_tree_structure": ToolSpec(
                "get_tree_structure",
                "【首选查看工具】扫描 PowerApps Studio 的 Tree View（树视图），返回当前所有屏幕和控件的结构化列表。用于确认已插入/存在的控件、查找控件名称。比 get_dom_snapshot 更轻量、更准确。",
                {"type": "object", "properties": {"filter": {"type": "string", "description": "可选，按名称过滤（不区分大小写），只返回包含此文本的条目"}}, "required": []},
                lambda filter="": self._get_tree_structure(filter),
            ),
            "search_in_tree": ToolSpec(
                "search_in_tree",
                "在 Tree View 中按名称搜索控件，返回所有匹配的控件及其父容器。用于定位不确定全名的控件。",
                {"type": "object", "properties": {"keyword": {"type": "string", "description": "搜索关键词（不区分大小写），如 '按钮'、'TextInput'、'Screen'"}}, "required": ["keyword"]},
                lambda keyword: self._search_in_tree(keyword),
            ),
            "click_tree_item": ToolSpec(
                "click_tree_item",
                "在 Tree View 中按名称点击某个控件节点将其选中。通常配合 search_in_tree 使用：先搜索找到目标，再点击选中。",
                {"type": "object", "properties": {"name": {"type": "string", "description": "控件名称，如 'Button1'、'TextInput1'、'Screen1'"}}, "required": ["name"]},
                lambda name: click_tree_item(self.session, name=name),
            ),
            # ── 左侧栏导航 ──────────────────────────────────
            "click_sidebar_tab": ToolSpec(
                "click_sidebar_tab",
                "点击 PowerApps Studio 左侧栏的 tab 按钮。用于切换到树视图、插入面板、数据面板等。例如：click_sidebar_tab('树视图')、click_sidebar_tab('插入')、click_sidebar_tab('数据')。",
                {"type": "object", "properties": {"tab_label": {"type": "string", "description": "tab 标签文本，如 '树视图'、'插入'、'数据'、'Tree view'、'Insert'"}}, "required": ["tab_label"]},
                lambda tab_label: click_sidebar_tab(self.session, tab_label=tab_label),
            ),
            # ── 属性查看 ──────────────────────────────────────
            "get_property_options": ToolSpec(
                "get_property_options",
                "打开属性选择器并返回当前选中控件的可选属性列表。",
                {"type": "object", "properties": {}, "required": []},
                lambda: formula_bar.get_property_options(self.session),
            ),
            "traverse_all_properties": ToolSpec(
                "traverse_all_properties",
                "遍历当前选中控件全部属性，收集每个属性的公式值。用于查看某控件的全部配置，或不确定属性名时。",
                {"type": "object", "properties": {}, "required": []},
                lambda: traverse_all_properties(self.session),
            ),
            # ── 插入菜单扫描 ──────────────────────────────────
            "scan_insert_menu": ToolSpec(
                "scan_insert_menu",
                "点击「插入」并扫描全部可见控件模板。用于探索当前可用控件或不确定控件名称时。",
                {"type": "object", "properties": {}, "required": []},
                lambda: scan_insert_menu(self.session),
            ),
            "click_ribbon_insert": ToolSpec(
                "click_ribbon_insert",
                "点击 PowerApps Studio 顶部 Ribbon 的插入按钮。通常由 scan_insert_menu 自动完成，无需单独调用。",
                {"type": "object", "properties": {"button_text": {"type": "string", "default": "插入"}}, "required": []},
                lambda button_text="插入": insert_menu.click_ribbon_insert(self.session, button_text),
            ),
            "click_insert_menu_item": ToolSpec(
                "click_insert_menu_item",
                "在已打开的插入菜单中点击指定控件。通常优先用 insert_component_and_set_formula。",
                {"type": "object", "properties": {"text": {"type": "string", "description": "控件名称，如 按钮、标签、文本输入"}}, "required": ["text"]},
                lambda text: insert_menu.click_insert_menu_item(self.session, text),
            ),
            "select_property_option": ToolSpec(
                "select_property_option",
                "按属性下拉框中的索引选择属性。通常由 set_property_formula 自动完成，无需单独调用。",
                {"type": "object", "properties": {"index": {"type": "integer", "description": "属性选项索引，从 0 开始"}}, "required": ["index"]},
                lambda index: formula_bar.select_property_option(self.session, index),
            ),
            "type_into_formula": ToolSpec(
                "type_into_formula",
                "聚焦公式栏并写入 Power Fx 公式。通常优先用 set_property_formula 或 insert_component_and_set_formula。",
                {"type": "object", "properties": {"text": {"type": "string", "description": "要写入的 Power Fx 公式"}, "clear_existing": {"type": "boolean", "default": True}}, "required": ["text"]},
                lambda text, clear_existing=True: click_func_input(ClickFuncInputParams(text=text, clear_existing=clear_existing), self.session),
            ),
            # ── 兜底 ──────────────────────────────────────────
            "get_dom_snapshot": ToolSpec(
                "get_dom_snapshot",
                "【最后手段】获取当前 Studio 页面的 DOM 快照文本。仅当前面所有工具（get_tree_structure / search_in_tree / get_property_options）都失败、页面状态完全不确定时才使用。每次调用成本高、返回信息噪点多。同一任务中请勿连续调用。",
                {"type": "object", "properties": {"max_chars": {"type": "integer", "default": 8000}}, "required": []},
                lambda max_chars=8000: self.get_dom_fallback(max_chars=max_chars),
            ),
        }

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters}}
            for tool in self.tools.values()
        ]

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools[name]
        result = tool.func(**arguments)
        if inspect.isawaitable(result):
            result = await result
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        elif hasattr(result, "dict"):
            result = result.dict()
        return json.dumps(result, ensure_ascii=False, default=str)

    # ── 新增工具实现 ────────────────────────────────────────

    async def _get_tree_structure(self, filter_text: str = "") -> str:
        """扫描 Tree View，返回结构化列表。"""
        try:
            from PowerfulApps.Agents.memory.tree_traversal import traverse_tree_via_cdp
            items = await traverse_tree_via_cdp(self.session)
            if not items:
                return json.dumps({"found": False, "error": "Tree View 为空或不可访问"}, ensure_ascii=False)

            if filter_text:
                ft = filter_text.strip().lower()
                items = [i for i in items if ft in i.get("name", "").lower() or ft in i.get("parent", "").lower()]

            # 构建清晰的结构化输出
            tree_text = f"Tree View 共有 {len(items)} 个条目：\n"
            for item in items:
                name = item.get("name", "")
                parent = item.get("parent", "")
                typ = item.get("type", "?")
                indent = "  " * (1 if typ == "control" else 0)
                icon = "📺" if typ == "screen" else "  ▸"
                parent_info = f" (in {parent})" if parent else ""
                tree_text += f"{indent}{icon} {name}{parent_info}\n"

            return tree_text
        except Exception as e:
            return json.dumps({"found": False, "error": str(e)}, ensure_ascii=False)

    async def _search_in_tree(self, keyword: str) -> str:
        """在 Tree View 搜索框输入关键词，让 PowerApps 原生过滤并返回结果。"""
        try:
            from PowerfulApps.MocProcess.actions.search_element import search_in_tree_view

            result = await search_in_tree_view(self.session, keyword.strip())
            if result.get("error"):
                return json.dumps({"found": False, "error": result["error"]}, ensure_ascii=False)

            items = result.get("items", [])
            if not items:
                return json.dumps({
                    "found": False,
                    "keyword": keyword,
                }, ensure_ascii=False)

            response = f"找到 {len(items)} 个匹配 \"{keyword}\" 的控件：\n"
            for item in items:
                name = item.get("name", "")
                response += f"  - {name}\n"
            return response

        except Exception as e:
            return json.dumps({"found": False, "error": str(e)}, ensure_ascii=False)

    async def get_dom_fallback(self, *, max_chars: int = 8000) -> str:
        """Capture a serialized DOM snapshot of the current page for LLM observation."""
        try:
            from PowerfulApps.DOM.service import DomService

            async with DomService(
                browser_session=self.session,
                cross_origin_iframes=False,
                paint_order_filtering=True,
            ) as dom:
                serialized, _, _ = await dom.get_serialized_dom_tree()
                snapshot = serialized.llm_representation()
                return snapshot[:max_chars]
        except Exception as e:
            return f"[DOM 兜底失败] {e}"