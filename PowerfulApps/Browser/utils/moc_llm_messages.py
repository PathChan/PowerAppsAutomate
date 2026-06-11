"""Minimal LLM message classes for copied actor helpers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SystemMessage:
    content: str


@dataclass
class UserMessage:
    content: str
