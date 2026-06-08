"""PowerApps Agent 工具注册。"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from PowerfulApps.MocProcess.actions import formula_bar, insert_menu
from PowerfulApps.MocProcess.actions.click_funcInput import ClickFuncInputParams, click_func_input
from PowerfulApps.MocProcess.chains.insert_and_set_formula import (
    insert_component_and_set_formula,
    scan_insert_menu,
    traverse_all_properties,
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
            "click_ribbon_insert": ToolSpec(
                "click_ribbon_insert",
                "点击 PowerApps Studio 顶部 Ribbon 的插入按钮。通常低层使用，优先用 insert_component_and_set_formula。",
                {"type": "object", "properties": {"button_text": {"type": "string", "default": "插入"}}, "required": []},
                lambda button_text="插入": insert_menu.click_ribbon_insert(self.session, button_text),
            ),
            "get_insert_menu_items": ToolSpec(
                "get_insert_menu_items",
                "扫描插入菜单中可用的控件列表。",
                {"type": "object", "properties": {}, "required": []},
                lambda: insert_menu.get_insert_menu_items(self.session),
            ),
            "click_insert_menu_item": ToolSpec(
                "click_insert_menu_item",
                "在已经打开的插入菜单中点击指定控件。通常低层使用。",
                {"type": "object", "properties": {"text": {"type": "string", "description": "控件名称，如 按钮、标签、文本输入"}}, "required": ["text"]},
                lambda text: insert_menu.click_insert_menu_item(self.session, text),
            ),
            "get_property_options": ToolSpec(
                "get_property_options",
                "打开属性选择器并返回当前选中控件的可选属性。",
                {"type": "object", "properties": {}, "required": []},
                lambda: formula_bar.get_property_options(self.session),
            ),
            "select_property_option": ToolSpec(
                "select_property_option",
                "按属性下拉框中的索引选择属性。通常优先用 set_property_formula。",
                {"type": "object", "properties": {"index": {"type": "integer", "description": "属性选项索引，从 0 开始"}}, "required": ["index"]},
                lambda index: formula_bar.select_property_option(self.session, index),
            ),
            "type_into_formula": ToolSpec(
                "type_into_formula",
                "聚焦公式栏并写入 Power Fx 公式。通常优先用 set_property_formula。",
                {"type": "object", "properties": {"text": {"type": "string", "description": "要写入的 Power Fx 公式"}, "clear_existing": {"type": "boolean", "default": True}}, "required": ["text"]},
                lambda text, clear_existing=True: click_func_input(ClickFuncInputParams(text=text, clear_existing=clear_existing), self.session),
            ),
            "insert_component_and_set_formula": ToolSpec(
                "insert_component_and_set_formula",
                "插入一个控件，并立即设置它的某个属性公式。执行后新控件通常保持选中。",
                {"type": "object", "properties": {"component": {"type": "string", "description": "控件名称，如 按钮"}, "property_name": {"type": "string", "description": "属性名，如 Text、OnSelect"}, "formula": {"type": "string", "description": "Power Fx 公式"}}, "required": ["component", "property_name", "formula"]},
                lambda component, property_name, formula: insert_component_and_set_formula(self.session, component=component, property_name=property_name, formula=formula),
            ),
            "set_property_formula": ToolSpec(
                "set_property_formula",
                "给当前选中的控件设置某个属性公式，不插入新控件。",
                {"type": "object", "properties": {"property_name": {"type": "string", "description": "属性名，如 Text、OnSelect、X、Y"}, "formula": {"type": "string", "description": "Power Fx 公式"}}, "required": ["property_name", "formula"]},
                lambda property_name, formula: set_property_formula(self.session, property_name=property_name, formula=formula),
            ),
            "scan_insert_menu": ToolSpec(
                "scan_insert_menu",
                "点击插入并扫描全部可见控件。用于不确定控件名称时。",
                {"type": "object", "properties": {}, "required": []},
                lambda: scan_insert_menu(self.session),
            ),
            "traverse_all_properties": ToolSpec(
                "traverse_all_properties",
                "遍历当前选中控件全部属性，收集公式栏/面板信息。用于不确定属性名称时。",
                {"type": "object", "properties": {}, "required": []},
                lambda: traverse_all_properties(self.session),
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
