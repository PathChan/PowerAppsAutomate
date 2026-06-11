"""OpenAI-compatible chat clients — 从 presets 加载配置，按 api_type 区分调用方式。"""
from __future__ import annotations

import os
from typing import Any

from openai import AsyncOpenAI

from .presets import ProviderPreset, load_default_preset, load_preset, load_presets


class OpenAICompatibleClient:
    """统一封装 OpenAI 兼容 Chat Completions 调用。

    所有供应商（DeepSeek / OpenAI / 火山方舟）都走 OpenAI 兼容格式。
    通过 `extra_body` 统一控制 thinking 开关。

    优先级：构造参数 > 环境变量(LLM_*) > JSON 预设值
    """

    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        thinking_enabled: bool | None = None,
        reason_effort: str | None = None,
    ) -> None:
        # 1. 确定预设名称（用户自定义名称）
        preset_name = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        presets = load_presets()
        preset = presets.get(preset_name) if preset_name else None

        # fallback: 用默认预设
        if preset is None:
            preset = load_default_preset()
        if preset is None:
            preset = ProviderPreset(
                name="default",
                manufacturer="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                api_key="",
                thinking=True,
                api_type="openai-compatible",
            )

        self.provider = preset.manufacturer
        self.api_type = preset.api_type

        # 2. API Key: 构造参数 > env(LLM_API_KEY) > JSON 预设
        self.api_key = api_key or os.getenv("LLM_API_KEY") or preset.api_key

        # 3. Base URL: 构造参数 > env(LLM_BASE_URL) > JSON 预设
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or preset.base_url

        # 4. Model: 构造参数 > env(LLM_MODEL) > JSON 预设
        self.model = model or os.getenv("LLM_MODEL") or preset.model

        # 5. Thinking
        if thinking_enabled is not None:
            self.thinking_enabled = thinking_enabled
        else:
            env_val = os.getenv("LLM_THINKING_ENABLED", "").strip().lower()
            if env_val in {"1", "true", "yes", "y", "on"}:
                self.thinking_enabled = True
            elif env_val in {"0", "false", "no", "off"}:
                self.thinking_enabled = False
            else:
                self.thinking_enabled = preset.thinking

        # 6. Reasoning effort
        self.reason_effort = (
            reason_effort
            or os.getenv("LLM_REASONING_EFFORT")
            or preset.reasoning_effort
        )

        # 7. Client
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

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
            if self.reason_effort:
                kwargs["reasoning_effort"] = self.reason_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

        return await self.client.chat.completions.create(**kwargs)


# 便捷子类：按厂商名自动选取该厂商的第一个预设
def _first_preset_by_manufacturer(manufacturer: str) -> str | None:
    presets = load_presets()
    for name, p in presets.items():
        if p.manufacturer == manufacturer:
            return name
    return None


class DeepSeekClient(OpenAICompatibleClient):
    def __init__(self, **kwargs: Any) -> None:
        name = _first_preset_by_manufacturer("deepseek")
        super().__init__(provider=name, **kwargs)


class VolcengineArkClient(OpenAICompatibleClient):
    def __init__(self, **kwargs: Any) -> None:
        name = _first_preset_by_manufacturer("volcengine")
        super().__init__(provider=name, **kwargs)


def create_llm_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient()