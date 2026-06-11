"""Recording watchdog disabled for PowerfulApps."""
from __future__ import annotations

from typing import ClassVar

from bubus import BaseEvent

from PowerfulApps.Browser.core.events import AgentFocusChangedEvent, BrowserConnectedEvent, BrowserStopEvent
from PowerfulApps.Browser.core.watchdog_base import BaseWatchdog


class RecordingWatchdog(BaseWatchdog):
    """No-op watchdog: video recording support was intentionally removed."""

    LISTENS_TO: ClassVar[list[type[BaseEvent]]] = [BrowserConnectedEvent, BrowserStopEvent, AgentFocusChangedEvent]
    EMITS: ClassVar[list[type[BaseEvent]]] = []

    async def on_BrowserConnectedEvent(self, event: BrowserConnectedEvent) -> None:
        return None

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        return None

    async def on_AgentFocusChangedEvent(self, event: AgentFocusChangedEvent) -> None:
        return None
