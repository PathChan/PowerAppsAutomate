"""PowerfulApps Browser 模块。

子包：
  core/    — 会话管理、事件系统、配置、视图、watchdog 基类
  cdp/     — CDP 直连工具（Studio iframe 操作、超时、坐标缓存）
  utils/   — 工具函数（配置、日志、可观测性、截图标注）
  demo/    — Demo 面板注入
  actor/   — CDP 高级抽象层（Playwright 风格）
  cloud/   — 云端浏览器 stub
  watchdogs/ — 浏览器监控组件
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core.profile import BrowserProfile, ProxySettings
    from .core.session import BrowserSession

_LAZY_IMPORTS = {
    'ProxySettings': ('.core.profile', 'ProxySettings'),
    'BrowserProfile': ('.core.profile', 'BrowserProfile'),
    'BrowserSession': ('.core.session', 'BrowserSession'),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            from importlib import import_module
            full_module_path = f'PowerfulApps.Browser{module_path}'
            module = import_module(full_module_path)
            attr = getattr(module, attr_name)
            globals()[name] = attr
            return attr
        except ImportError as e:
            raise ImportError(f'Failed to import {name} from {full_module_path}: {e}') from e
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    'BrowserSession',
    'BrowserProfile',
    'ProxySettings',
]
