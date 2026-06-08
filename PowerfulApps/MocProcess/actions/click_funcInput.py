"""click_funcInput action：聚焦 PowerApps Studio 的 Monaco 公式编辑器并写入一段文本。

封装自 `Test/test_visual_formula_click.py` 中经验证可行的 "B 方案" + CDP insertText：

1) 通过 `Page.getFrameTree` 找到主页里的 EmbeddedStudio 跨域 iframe；
2) 通过 `Page.createIsolatedWorld({frameId})` 在该 iframe 的隔离世界里执行 JS；
3) 在 `#formulaBarContainer .view-lines` 上派发完整的事件序列让 Monaco 聚焦；
4) 主页 CDP 会话发 `Ctrl+A` 全选 + `Input.insertText` 写入文本。

改造：
- 公式栏位置（#formulaBarContainer .view-lines 的中心）会缓存到
  dom_targets.json，下次直接 CDP 坐标点击（mousedown/mouseup）即可触发
  Monaco 自身的 focus 流程，无需再次注入 JS 事件序列；
- 缓存命中后跳过 focus_formula_editor_via_dispatch 的 DOM 查询；
- 失败则自动回退到事件派发方案并把新坐标写回缓存。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from mocProcessing.agent.views import ActionResult
from mocProcessing.browser import BrowserSession
from mocProcessing.tools.powerapps_chain import (
	diagnose_focus_state,
	focus_formula_editor_via_dispatch,
)
from mocProcessing.tools.target_cache import (
	click_via_cache,
	selector_locator,
)

logger = logging.getLogger(__name__)

# 公式栏在缓存里的固定 key
_FORMULA_BAR_KEY = "formula_bar::view_lines"


class ClickFuncInputParams(BaseModel):
	"""参数：要写入 PowerApps Studio 公式栏的文本。"""

	text: str = Field(
		...,
		description=(
			'要写入公式编辑器的文本（通常是 Power Fx 公式或字符串字面量）。'
			'写入前会先 Ctrl+A 全选清空已有内容。'
		),
	)
	clear_existing: bool = Field(
		default=True,
		description='写入前是否先 Ctrl+A 全选清空。默认 True。',
	)


async def _focus_via_cache(browser_session: BrowserSession) -> bool:
	"""尝试通过坐标缓存点击公式栏并验证 Monaco 是否聚焦。"""

	async def _verify(session: BrowserSession) -> bool:
		diag = await diagnose_focus_state(session)
		return bool(diag.get("isInputArea"))

	try:
		result = await click_via_cache(
			browser_session,
			_FORMULA_BAR_KEY,
			kind="formula_bar",
			label="formula_bar",
			locator=selector_locator("#formulaBarContainer .view-lines", "formula_bar"),
			verify=_verify,
			max_retries=1,
		)
		return bool(result.get("ok"))
	except Exception as e:
		logger.debug("formula bar cache click failed: %s", e)
		return False


async def click_func_input(params: ClickFuncInputParams, browser_session: BrowserSession) -> ActionResult:
	"""聚焦 PowerApps Studio 公式编辑器并写入文本。"""
	# 1a) 优先用坐标缓存点击公式栏（成功的话 Monaco textarea 就已经聚焦）
	focused = await _focus_via_cache(browser_session)

	# 1b) 失败再退回 dispatch
	if not focused:
		focus_result = await focus_formula_editor_via_dispatch(browser_session)
		if not focus_result.get('focused'):
			diag = await diagnose_focus_state(browser_session)
			return ActionResult(
				error=(
					f'PowerApps formula editor focus FAILED. dispatch={focus_result} active={diag}'
				)
			)

	# 2) 在主页会话发 CDP 键盘事件
	cdp_session = await browser_session.get_or_create_cdp_session()

	if params.clear_existing:
		for kdef in (
			{'type': 'keyDown', 'key': 'Control', 'code': 'ControlLeft', 'windowsVirtualKeyCode': 17},
			{'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'windowsVirtualKeyCode': 65, 'modifiers': 2},
			{'type': 'keyUp', 'key': 'a', 'code': 'KeyA', 'windowsVirtualKeyCode': 65, 'modifiers': 2},
			{'type': 'keyUp', 'key': 'Control', 'code': 'ControlLeft', 'windowsVirtualKeyCode': 17},
		):
			await cdp_session.cdp_client.send.Input.dispatchKeyEvent(
				params=kdef, session_id=cdp_session.session_id
			)

	await cdp_session.cdp_client.send.Input.insertText(
		params={'text': params.text},
		session_id=cdp_session.session_id,
	)

	# 3) 确认终态
	final_diag = await diagnose_focus_state(browser_session)
	preview = params.text if len(params.text) <= 80 else params.text[:77] + '...'
	source = "cache" if focused else "dispatch"
	message = (
		f'Wrote text into PowerApps formula editor (focus_source={source}, '
		f'length={len(params.text)}, cleared={params.clear_existing}, '
		f'isInputArea={final_diag.get("isInputArea")}): {preview!r}'
	)
	return ActionResult(extracted_content=message, long_term_memory=message)


def register(tools) -> None:
	"""把 click_func_input 注册到给定的 Tools 实例上。"""
	tools.action(
		(
			'Focus the PowerApps Studio formula editor (Monaco hidden textarea) and '
			'type the provided text into it. Use this whenever you need to set or '
			'replace a Power Fx formula / property value on the currently selected '
			'control. The text parameter can be any string, e.g. "\\"hello\\"" or '
			'"RGBA(0,0,0,1)" or "If(Var1=1, \\"a\\", \\"b\\")".'
		),
		param_model=ClickFuncInputParams,
	)(click_func_input)
