"""insert_element_and_funcInput chain：插入组件 → 选中属性 → 写入公式。

底层实现全部来自 PowerfulApps.MocProcess，不再依赖旧的经验/缓存系统。

流程：
  1. click_ribbon_button("插入")       — 打开插入菜单
  2. click_insert_menu_item("按钮")    — 插入控件（自动展开分类）
  3. get_property_options()            — 打开属性下拉框获取所有选项
  4. select_property_option("Text")    — 选中目标属性
  5. type_into_formula("点我一下")     — 写入公式

支持任意 component / property_name / formula 组合。
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from PowerfulApps.MocProcess.chains.insert_and_set_formula import (
    insert_component_and_set_formula,
)
from PowerfulApps.MocProcess.actions.insert_menu import (
    click_ribbon_insert,
    click_insert_menu_item,
)
from PowerfulApps.MocProcess.actions.formula_bar import (
    get_property_options,
    select_property_option,
    type_into_formula,
)

logger = logging.getLogger(__name__)


class InsertElementAndSetFormulaParams(BaseModel):
    """参数：插入组件并设置属性值。"""
    component: str = Field(
        default="按钮",
        description='要插入的组件名称，如 "按钮"、"文本输入"、"标签"、"组合框" 等。默认 "按钮"。',
    )
    property_name: str = Field(
        default="Text",
        description='要设置的属性名称，如 "Text"、"X"、"Y"、"Fill" 等。默认 "Text"。',
    )
    formula: str = Field(
        default='"点我一下"',
        description='要写入的 Power Fx 公式或文本。默认 "点我一下"。',
    )


async def insert_element_and_set_formula(
    params: InsertElementAndSetFormulaParams,
    browser_session: BrowserSession,
) -> ActionResult:
    """完整的插入组件 → 设置属性流程。"""
    result = await insert_component_and_set_formula(
        browser_session,
        component=params.component,
        property_name=params.property_name,
        formula=params.formula,
    )

    if result.get("success"):
        summary = (
            f"Created component '{params.component}' "
            f"and set {params.property_name} = {params.formula!r}"
        )
        return ActionResult(extracted_content=summary, long_term_memory=summary)

    # 查找失败步骤
    failed_steps = [s for s in result.get("steps", []) if not s.get("success")]
    error_msg = "; ".join(f"{s['name']}: {s.get('detail', 'unknown')}" for s in failed_steps)
    return ActionResult(error=f"Chain failed at steps: {error_msg}")


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        (
            'Full PowerApps chain: insert a component into the canvas, select a property, '
            'and set its formula. Steps: click Insert ribbon button → expand insert menu '
            'categories → select component → open property dropdown → select property → '
            'type formula value. '
            'Example: component="按钮", property_name="Text", formula="点我一下".'
        ),
        param_model=InsertElementAndSetFormulaParams,
    )(insert_element_and_set_formula)