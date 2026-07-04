"""Auto-continue circuit breaker."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from agentic_harness.core.errors import LoopGuardTripped


@dataclass
class LoopGuard:
    max_continues: int = 5
    window_seconds: float = 300.0
    _events: deque[float] = field(default_factory=deque)

    def record_continue(self) -> None:
        now = monotonic()
        self._events.append(now)
        while self._events and now - self._events[0] > self.window_seconds:
            self._events.popleft()
        if len(self._events) > self.max_continues:
            raise LoopGuardTripped(
                f"auto-continue circuit breaker tripped after {len(self._events)} events"
            )

    def reset(self) -> None:
        self._events.clear()

