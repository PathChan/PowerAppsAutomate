"""Empty stubs for token usage views."""

from pydantic import BaseModel


class TokenUsageEntry(BaseModel):
	"""Stub - token tracking removed."""

	model: str = ''
	prompt_tokens: int = 0
	completion_tokens: int = 0
	total_tokens: int = 0


class UsageSummary(BaseModel):
	"""Stub - token tracking removed."""

	total_prompt_tokens: int = 0
	total_completion_tokens: int = 0
	total_tokens: int = 0
	total_cost: float = 0.0
	entries: list[TokenUsageEntry] = []
