"""DeepSeek OpenAI-compatible chat client for PowerApps agents."""
from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI


class DeepSeekClient:
    """只封装 DeepSeek 调用参数，避免 Agent 直接依赖具体 SDK 细节。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking_enabled: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com"
        self.model = model or os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-v4-pro"
        self.thinking_enabled = self._env_bool("DEEPSEEK_THINKING_ENABLED", True) if thinking_enabled is None else thinking_enabled
        self.reasoning_effort = reasoning_effort or os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name, "").strip().lower()
        if not value:
            return default
        return value in {"1", "true", "yes", "y", "on"}

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if self.thinking_enabled:
            kwargs["reasoning_effort"] = self.reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return await self.client.chat.completions.create(**kwargs)
