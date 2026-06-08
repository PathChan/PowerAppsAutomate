"""click_powerapps_insert_item：在插入菜单内点击指定控件（封装自 PowerfulApps）。

底层实现来自 PowerfulApps.MocProcess.actions.insert_menu.click_insert_menu_item。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from PowerfulApps.MocProcess.actions.insert_menu import click_insert_menu_item


class ClickInsertItemParams(BaseModel):
    """参数：要点击的控件名称。"""
    item_name: str = Field(
        default="按钮",
        description='插入菜单中的控件名称，如 "按钮"、"文本输入"、"标签"、"组合框" 等。',
    )


async def click_insert_item(params: ClickInsertItemParams, browser_session: BrowserSession) -> ActionResult:
    """在 PowerApps 插入菜单中点击指定控件。"""
    result = await click_insert_menu_item(browser_session, params.item_name)
    if result.get("success"):
        msg = f"Clicked insert menu item '{params.item_name}'"
        return ActionResult(extracted_content=msg, long_term_memory=msg)
    return ActionResult(error=result.get("error", f"Failed to click '{params.item_name}'"))


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        'Click a component template in the PowerApps insert menu tree. '
        'Call after clicking "插入" ribbon button. '
        'Example: item_name="按钮" to insert a Button control.',
        param_model=ClickInsertItemParams,
    )(click_insert_item)