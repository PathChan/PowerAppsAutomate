"""mocProcessing — 从 browser_use 移植的精简版浏览器自动化框架。

只保留 PowerApps 自动化所需的核心功能：
- DOM 树解析与可交互元素检测
- 浏览器会话管理（CDP）
- 点击/输入等用户动作执行（含 PowerApps Monaco 公式栏 focus 修复）
- Agent 任务编排
- OpenAI LLM（其他多模型已移除）
"""

import os
from typing import TYPE_CHECKING

from mocProcessing.logging_config import setup_logging

# Only set up logging if not in MCP mode or if explicitly requested
if os.environ.get('BROWSER_USE_SETUP_LOGGING', 'true').lower() != 'false':
	from mocProcessing.config import CONFIG

	# Get log file paths from config/environment
	debug_log_file = getattr(CONFIG, 'BROWSER_USE_DEBUG_LOG_FILE', None)
	info_log_file = getattr(CONFIG, 'BROWSER_USE_INFO_LOG_FILE', None)

	# Set up logging with file handlers if specified
	logger = setup_logging(debug_log_file=debug_log_file, info_log_file=info_log_file)
else:
	import logging

	logger = logging.getLogger('mocProcessing')

# Monkeypatch BaseSubprocessTransport.__del__ to handle closed event loops gracefully
from asyncio import base_subprocess

_original_del = base_subprocess.BaseSubprocessTransport.__del__


def _patched_del(self):
	"""Patched __del__ that handles closed event loops without throwing noisy red-herring errors like RuntimeError: Event loop is closed"""
	try:
		# Check if the event loop is closed before calling the original
		if hasattr(self, '_loop') and self._loop and self._loop.is_closed():
			# Event loop is closed, skip cleanup that requires the loop
			return
		_original_del(self)
	except RuntimeError as e:
		if 'Event loop is closed' in str(e):
			# Silently ignore this specific error
			pass
		else:
			raise


base_subprocess.BaseSubprocessTransport.__del__ = _patched_del


# Type stubs for lazy imports - fixes linter warnings
if TYPE_CHECKING:
	from mocProcessing.agent.prompts import SystemPrompt
	from mocProcessing.agent.service import Agent
	from mocProcessing.agent.views import ActionModel, ActionResult, AgentHistoryList
	from mocProcessing.browser import BrowserProfile, BrowserSession
	from mocProcessing.browser import BrowserSession as Browser
	from mocProcessing.dom.service import DomService
	from mocProcessing.llm.openai.chat import ChatOpenAI
	from mocProcessing.tools.service import Controller, Tools


# Lazy imports mapping - only import when actually accessed
_LAZY_IMPORTS = {
	'Agent': ('mocProcessing.agent.service', 'Agent'),
	'SystemPrompt': ('mocProcessing.agent.prompts', 'SystemPrompt'),
	'ActionModel': ('mocProcessing.agent.views', 'ActionModel'),
	'ActionResult': ('mocProcessing.agent.views', 'ActionResult'),
	'AgentHistoryList': ('mocProcessing.agent.views', 'AgentHistoryList'),
	'BrowserSession': ('mocProcessing.browser', 'BrowserSession'),
	'Browser': ('mocProcessing.browser', 'BrowserSession'),  # Alias
	'BrowserProfile': ('mocProcessing.browser', 'BrowserProfile'),
	'Tools': ('mocProcessing.tools.service', 'Tools'),
	'Controller': ('mocProcessing.tools.service', 'Controller'),
	'DomService': ('mocProcessing.dom.service', 'DomService'),
	'ChatOpenAI': ('mocProcessing.llm.openai.chat', 'ChatOpenAI'),
}


def __getattr__(name: str):
	"""Lazy import mechanism - only import modules when they're actually accessed."""
	if name in _LAZY_IMPORTS:
		module_path, attr_name = _LAZY_IMPORTS[name]
		try:
			from importlib import import_module

			module = import_module(module_path)
			attr = getattr(module, attr_name) if attr_name else module
			globals()[name] = attr
			return attr
		except ImportError as e:
			raise ImportError(f'Failed to import {name} from {module_path}: {e}') from e

	raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
	'Agent',
	'BrowserSession',
	'Browser',
	'BrowserProfile',
	'Controller',
	'DomService',
	'SystemPrompt',
	'ActionResult',
	'ActionModel',
	'AgentHistoryList',
	'ChatOpenAI',
	'Tools',
]
