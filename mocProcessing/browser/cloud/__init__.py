"""Stub package for removed cloud browser support.

mocProcessing 不支持云端浏览器（BrowserBase 等），但 session.py 中有大量类型引用，
所以提供空实现 stub 让代码能正常 import。
"""

from mocProcessing.browser.cloud.cloud import (
	CloudBrowserAuthError,
	CloudBrowserClient,
	CloudBrowserError,
)
from mocProcessing.browser.cloud.views import (
	CloudBrowserParams,
	CreateBrowserRequest,
	ProxyCountryCode,
)

__all__ = [
	'CloudBrowserAuthError',
	'CloudBrowserClient',
	'CloudBrowserError',
	'CloudBrowserParams',
	'CreateBrowserRequest',
	'ProxyCountryCode',
]
