"""Minimal action result used by PowerfulApps tools."""
from __future__ import annotations

from pydantic import BaseModel


class ActionResult(BaseModel):
    success: bool | None = None
    error: str | None = None
    extracted_content: str | None = None
    long_term_memory: str | None = None
