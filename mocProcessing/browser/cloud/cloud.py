"""Empty cloud browser stubs - cloud support removed in mocProcessing."""

from typing import Any


class CloudBrowserError(Exception):
	"""Cloud browser error stub."""

	pass


class CloudBrowserAuthError(CloudBrowserError):
	"""Cloud browser auth error stub."""

	pass


class CloudBrowserClient:
	"""Stub client - cloud browser not supported in mocProcessing."""

	def __init__(self, *args: Any, **kwargs: Any) -> None:
		self.current_session_id: str | None = None

	async def create_browser(self, *args: Any, **kwargs: Any) -> Any:
		raise CloudBrowserError('Cloud browser support removed in mocProcessing')

	async def stop_browser(self, *args: Any, **kwargs: Any) -> None:
		pass

	async def close(self) -> None:
		pass
