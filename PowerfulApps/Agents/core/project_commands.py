"""/project 命令解析。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProjectCommand:
    name: str
    args: list[str]


def parse_project_command(text: str) -> ProjectCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/project"):
        return None
    parts = stripped.split()
    if len(parts) == 1:
        return ProjectCommand("help", [])
    return ProjectCommand(parts[1], parts[2:])
