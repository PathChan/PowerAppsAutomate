"""进程内短期记忆和上下文压缩。"""
from __future__ import annotations

import json
from typing import Any


_SUMMARY_SYSTEM_PROMPT = """你是 PowerApps 自动化 Agent 的上下文压缩器。
请把对话、工具调用和工具结果压缩成中文摘要，保留：
1. 用户明确需求和偏好；
2. 已执行的 PowerApps 操作；
3. 已选中/已插入控件、属性和公式；
4. 失败原因、页面状态和后续必须注意的约束。
不要编造，不要输出无关寒暄。"""


class ShortTermMemory:
    def __init__(self, *, max_messages: int = 24, max_summaries: int = 12) -> None:
        self.max_messages = max_messages
        self.max_summaries = max_summaries
        self.summaries: list[str] = []
        self.messages: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.summaries.clear()
        self.messages.clear()

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def add_tool(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    async def compact_if_needed(self, llm: Any, *, preserve_last: int = 0) -> None:
        if len(self.messages) < self.max_messages:
            return
        compact_count = max(self.max_messages, len(self.messages) - preserve_last)
        if preserve_last:
            compact_count = min(compact_count, max(0, len(self.messages) - preserve_last))
        if compact_count <= 0:
            return

        chunk = self.messages[:compact_count]
        self.messages = self.messages[compact_count:]
        self.summaries.append(await self._summarize_messages(llm, chunk))

        if len(self.summaries) >= self.max_summaries:
            merged = await self._summarize_texts(llm, self.summaries)
            self.summaries = [merged]

    async def _summarize_messages(self, llm: Any, messages: list[dict[str, Any]]) -> str:
        text = "\n".join(self._format_message(msg) for msg in messages)
        return await self._summarize_texts(llm, [text])

    async def _summarize_texts(self, llm: Any, texts: list[str]) -> str:
        response = await llm.chat(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(texts)},
            ],
            tool_choice="none",
        )
        return (response.choices[0].message.content or "").strip()

    def _format_message(self, msg: dict[str, Any]) -> str:
        role = msg.get("role", "")
        if role == "tool":
            name = msg.get("name", "tool")
            content = str(msg.get("content", ""))[:3000]
            return f"工具 {name} 返回：{content}"
        content = msg.get("content")
        if content:
            return f"{role}：{content}"
        return f"{role}：{json.dumps(msg, ensure_ascii=False, default=str)[:3000]}"

    def build_messages(self, system_prompt: str, project_doc: str) -> list[dict[str, Any]]:
        messages = [{"role": "system", "content": system_prompt}]
        memory_parts = []
        if project_doc.strip():
            memory_parts.append("项目持久化文档：\n" + project_doc[-8000:])
        if self.summaries:
            memory_parts.append("本次启动后的压缩短期记忆：\n" + "\n\n".join(self.summaries)[-12000:])
        if memory_parts:
            messages.append({"role": "system", "content": "\n\n".join(memory_parts)})
        messages.extend(self.messages)
        return messages
