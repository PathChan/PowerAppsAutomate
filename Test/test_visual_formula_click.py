from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(_PROJECT_ROOT))

from mocProcessing import BrowserSession
from mocProcessing.tools.powerapps_chain import (
	diagnose_focus_state,
	focus_formula_editor_via_dispatch,
	find_studio_frame_id,
	inspect_formula_bar_dom,
	list_all_targets,
	list_frames_via_page,
)


def _load_env_file(path: Path) -> None:
	if not path.exists():
		return
	for raw in path.read_text(encoding='utf-8').splitlines():
		line = raw.strip()
		if not line or line.startswith('#') or '=' not in line:
			continue
		key, value = line.split('=', 1)
		key = key.strip()
		value = value.strip().strip('"').strip("'")
		if key and key not in os.environ:
			os.environ[key] = value


_load_env_file(_PROJECT_ROOT / '.env')

logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s | %(levelname)-7s | %(message)s',
	datefmt='%H:%M:%S',
)
log = logging.getLogger('test_visual_formula_click')


def _dump_inspect(label: str, data: object) -> None:
	import json as _json
	try:
		pretty = _json.dumps(data, ensure_ascii=False, indent=2)
	except Exception:
		pretty = repr(data)
	log.info('%s:\n%s', label, pretty)


async def _wait_for_cdp_ready(browser_session: BrowserSession, attempts: int = 8) -> None:
	last_error: Exception | None = None
	for attempt in range(1, attempts + 1):
		try:
			cdp_session = await browser_session.get_or_create_cdp_session()
			await cdp_session.cdp_client.send.Runtime.evaluate(
				params={'expression': 'document.readyState', 'returnByValue': True},
				session_id=cdp_session.session_id,
			)
			log.info('CDP is ready.')
			return
		except Exception as e:
			last_error = e
			log.info('Waiting for CDP reconnect/stability (%d/%d): %s', attempt, attempts, e)
			await asyncio.sleep(1.5)
	if last_error:
		raise last_error


async def main() -> None:
	power_apps_url = os.getenv('POWER_APPS_URL', '').strip()
	if not power_apps_url:
		raise RuntimeError('POWER_APPS_URL is not set')

	user_data_dir = Path(os.getenv('BROWSER_USE_USER_DATA_DIR', './browser_profile')).expanduser()
	if not user_data_dir.is_absolute():
		user_data_dir = (_PROJECT_ROOT / user_data_dir).resolve()

	log.info('Opening PowerApps URL with persistent profile: %s', user_data_dir)
	session = BrowserSession(
		headless=False,
		user_data_dir=str(user_data_dir),
		enable_default_extensions=False,
		keep_alive=True,
	)
	await session.start()
	await session.new_page(power_apps_url)

	log.info('Browser opened. Please finish login / close popups / navigate to the desired app state manually.')
	await asyncio.to_thread(input, 'Ready to test visual click? Press Enter here after the page is ready...')

	log.info('Waiting for CDP to be stable after manual login/navigation...')
	await _wait_for_cdp_ready(session)
	await asyncio.sleep(2)

	# 在做任何点击之前，先快照一下当前 activeElement，便于对照。
	pre_diag = await diagnose_focus_state(session)
	log.info('Pre-click activeElement diag: %s', pre_diag)

	# 0a) 把 CDP 看到的所有 targets 打印出来（找 Studio 是不是 OOPIF）
	targets = await list_all_targets(session)
	_dump_inspect('All CDP targets', targets)

	# 0b) 把主页 frame tree 打印出来，并尝试自动找出 Studio iframe 的 frameId
	frame_tree = await list_frames_via_page(session)
	_dump_inspect('Main page frame tree', frame_tree)
	studio_frame_id = await find_studio_frame_id(session)
	log.info('Resolved Studio frameId: %s', studio_frame_id)

	# 0c) 在 Studio iframe 内跑 DOM inspect（如果找到 frameId 就会自动走 iframe 上下文）
	dom_pre = await inspect_formula_bar_dom(session)
	_dump_inspect('Pre-click DOM inspect (studio context)', dom_pre)

	# 在 .view-lines / .monaco-editor 上手动派发完整指针事件序列：聚焦公式栏 textarea
	log.info('[B] Focusing formula editor via DOM pointer/mouse event dispatch...')
	b_result = await focus_formula_editor_via_dispatch(session)
	log.info('[B] Dispatch focus result: %s', b_result)

	if not b_result.get('focused'):
		log.error('Formula editor focus FAILED. Aborting input.')
		await asyncio.to_thread(input, 'Press Enter to close browser...')
		await session.stop()
		return

	# 公式栏 textarea 已聚焦：CDP 全选 + insertText 写入测试公式
	test_formula = os.getenv('POWERAPPS_TEST_FORMULA', '"hello from automate"').strip()
	log.info('Typing test formula via CDP Input.insertText: %r', test_formula)
	cdp_session = await session.get_or_create_cdp_session()
	for kdef in (
		{'type': 'keyDown', 'key': 'Control', 'code': 'ControlLeft', 'windowsVirtualKeyCode': 17},
		{'type': 'keyDown', 'key': 'a', 'code': 'KeyA', 'windowsVirtualKeyCode': 65, 'modifiers': 2},
		{'type': 'keyUp', 'key': 'a', 'code': 'KeyA', 'windowsVirtualKeyCode': 65, 'modifiers': 2},
		{'type': 'keyUp', 'key': 'Control', 'code': 'ControlLeft', 'windowsVirtualKeyCode': 17},
	):
		await cdp_session.cdp_client.send.Input.dispatchKeyEvent(params=kdef, session_id=cdp_session.session_id)
	await cdp_session.cdp_client.send.Input.insertText(
		params={'text': test_formula},
		session_id=cdp_session.session_id,
	)
	log.info('Sent text via CDP Input.insertText')

	final_diag = await diagnose_focus_state(session)
	log.info('Final activeElement diag: %s', final_diag)

	log.info('Done. Browser will stay open for manual inspection.')
	await asyncio.to_thread(input, 'Check whether the formula editor is focused / value applied. Press Enter to close browser...')
	await session.stop()


if __name__ == '__main__':
	asyncio.run(main())
