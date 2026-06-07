"""Empty cloud browser views stubs."""

from typing import Literal

from pydantic import BaseModel


ProxyCountryCode = Literal['us', 'gb', 'de', 'fr', 'jp', 'kr', 'br', 'au', 'ca']


class CloudBrowserParams(BaseModel):
	"""Stub - cloud support removed."""

	pass


class CreateBrowserRequest(BaseModel):
	"""Stub - cloud support removed."""

	cloud_profile_id: str | None = None
	cloud_proxy_country_code: str | None = None
	cloud_timeout: int | None = None
