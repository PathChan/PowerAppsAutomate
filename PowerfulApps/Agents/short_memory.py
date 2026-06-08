"""进程内短期记忆和上下文压缩。"""
from __future__ import annotations

from typing import Any


class ShortTermMemory:
    def __init__(self, *, max_messages: int = 24) -> None:
        self.max_messages = max_messages
        self.summary = ""
        self.messages: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.summary = ""
        self.messages.clear()

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self.compact_if_needed()

    def add_assistant(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.compact_if_needed()

    def add_tool(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.compact_if_needed()

    def compact_if_needed(self) -> None:
        if len(self.messages) <= self.max_messages:
            return
        removable_count = len(self.messages) - self.max_messages
        old = self.messages[:removable_count]
        self.messages = self.messages[removable_count:]
        facts: list[str] = []
        for msg in old:
            role = msg.get("role", "")
            if role == "user":
                facts.append(f"用户需求：{msg.get('content', '')}")
            elif role == "tool":
                name = msg.get("name", "tool")
                content = str(msg.get("content", ""))[:500]
                facts.append(f"工具 {name} 返回：{content}")
            elif role == "assistant" and msg.get("content"):
                facts.append(f"AI 回复：{msg.get('content')}")
        if facts:
            self.summary = (self.summary + "\n" + "\n".join(facts)).strip()[-6000:]

    def build_messages(self, system_prompt: str, project_doc: str) -> list[dict[str, Any]]:
        messages = [{"role": "system", "content": system_prompt}]
        memory_parts = []
        if project_doc.strip():
            memory_parts.append("项目持久化文档：\n" + project_doc[-8000:])
        if self.summary.strip():
            memory_parts.append("本次启动后的短期记忆摘要：\n" + self.summary[-6000:])
        if memory_parts:
            messages.append({"role": "system", "content": "\n\n".join(memory_parts)})
        messages.extend(self.messages)
        return messages
