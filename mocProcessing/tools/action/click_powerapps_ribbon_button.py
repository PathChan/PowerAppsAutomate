"""click_powerapps_ribbon_button：点击 PowerApps Studio Ribbon 按钮（封装自 PowerfulApps）。

底层实现来自 PowerfulApps.MocProcess.actions.insert_menu.click_ribbon_insert。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from PowerfulApps.MocProcess.actions.insert_menu import click_ribbon_insert


class ClickRibbonButtonParams(BaseModel):
    """参数：要点击的功能区按钮文本。"""
    button_text: str = Field(
        default="插入",
        description='功能区按钮文本。默认 "插入"，也可以是 "Insert"、'
                    '"主题"、"视图" 等。',
    )


async def click_ribbon_button(params: ClickRibbonButtonParams, browser_session: BrowserSession) -> ActionResult:
    """点击 PowerApps Studio 功能区中的指定按钮。"""
    result = await click_ribbon_insert(browser_session, params.button_text)
    if result.get("success"):
        msg = f"Clicked ribbon button '{params.button_text}'"
        return ActionResult(extracted_content=msg, long_term_memory=msg)
    return ActionResult(error=result.get("error", f"Failed to click '{params.button_text}'"))


def register(tools) -> None:
    """注册到 Tools 实例。"""
    tools.action(
        'Click a button in PowerApps Studio ribbon (top bar). '
        'Example: button_text="插入" to open insert panel.',
        param_model=ClickRibbonButtonParams,
    )(click_ribbon_button)