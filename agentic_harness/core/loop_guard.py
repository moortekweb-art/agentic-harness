"""Auto-continue circuit breaker."""

from __future__ import annotations

import json
import math
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
        if len(self._events) >= self.max_continues:
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
        now = self.clock()
        # Filter to finite numeric values and sort ascending so _prune
        # (which removes from the left) works correctly even if events
        # were recorded out of order on a prior run.
        self._events = deque(
            float(item)
            for item in sorted(events)
            if isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
        )
        # Prune expired events immediately so status() and is_tripped()
        # report the current window, not the on-disk snapshot.
        self._prune(now)

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"events": list(self._events)}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # Save failure is a logging issue, not a work failure.
            # The in-memory state is still valid; disk persistence failed.
            pass

    def status(self) -> dict[str, object]:
        """Return a non-mutating status snapshot of the guard."""
        self._load()
        now = self.clock()
        active = [t for t in self._events if now - t <= self.window_seconds]
        return {
            "events_total": len(self._events),
            "events_in_window": len(active),
            "max_continues": self.max_continues,
            "window_seconds": self.window_seconds,
            "remaining": max(0, self.max_continues - len(active)),
            "tripped": len(active) >= self.max_continues,
        }

    def is_tripped(self) -> bool:
        """True if the circuit breaker has tripped (window is full)."""
        return bool(self.status()["tripped"])

    def remaining_continues(self) -> int:
        """Number of auto-continue events still allowed before tripping."""
        remaining = self.status()["remaining"]
        return int(remaining) if isinstance(remaining, (int, float)) else 0
