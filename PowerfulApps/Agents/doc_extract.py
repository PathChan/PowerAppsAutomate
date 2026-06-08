"""从 AI 回复中提取项目文档记忆片段。"""
from __future__ import annotations

import re

_PROJECT_MEMORY_RE = re.compile(r"```project-memory\s*(.*?)```", re.DOTALL)


def extract_project_memory_blocks(text: str) -> list[str]:
    return [match.strip() for match in _PROJECT_MEMORY_RE.findall(text or "") if match.strip()]


def remove_project_memory_blocks(text: str) -> str:
    return _PROJECT_MEMORY_RE.sub("", text or "").strip()
