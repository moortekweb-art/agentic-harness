"""Auto-continue circuit breaker."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Callable

from agentic_harness.core.errors import LoopGuardTripped


@dataclass
class LoopGuard:
    max_continues: int = 5
    window_seconds: float = 300.0
    state_path: Path | None = None
    clock: Callable[[], float] = time
    _events: deque[float] = field(default_factory=deque)

    def record_continue(self) -> None:
        now = self.clock()
        self._load()
        self._events.append(now)
        self._prune(now)
        self._save()
        if len(self._events) > self.max_continues:
            raise LoopGuardTripped(
                f"auto-continue circuit breaker tripped after {len(self._events)} events"
            )

    def reset(self) -> None:
        self._events.clear()
        self._save()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] > self.window_seconds:
            self._events.popleft()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        events = payload.get("events")
        if not isinstance(events, list):
            return
        self._events = deque(
            float(item) for item in events if isinstance(item, (int, float))
        )

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"events": list(self._events)}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
