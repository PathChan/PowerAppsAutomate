"""Empty stub for TokenCost service - cost tracking removed in mocProcessing."""

from typing import Any

from mocProcessing.tokens.views import TokenUsageEntry, UsageSummary


class TokenCost:
	"""No-op stub. All methods are safe to call but track nothing."""

	def __init__(self, *args: Any, **kwargs: Any) -> None:
		self.usage_history: list[TokenUsageEntry] = []

	def register_llm(self, llm: Any) -> Any:
		"""Pass-through: no wrapping done."""
		return llm

	async def get_usage_summary(self) -> UsageSummary:
		return UsageSummary()

	def add_usage(self, *args: Any, **kwargs: Any) -> TokenUsageEntry:
		return TokenUsageEntry()

	async def calculate_cost(self, *args: Any, **kwargs: Any) -> None:
		return None

	def log_usage_summary(self, *args: Any, **kwargs: Any) -> None:
		pass
