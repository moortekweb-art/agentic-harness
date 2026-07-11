"""Durable, sanitized progress events for GUI and CLI consumers."""

from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any

from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.state import now_iso


EVENT_SCHEMA = "agentic_harness.task_event.v1"
_SAFE_GOAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class TaskEventStore:
    """Append one atomic event file at a time without exposing tool payloads."""

    def __init__(self, project_dir: str | Path, goal_id: str) -> None:
        if _SAFE_GOAL_ID.fullmatch(goal_id) is None:
            raise ValueError("goal id contains unsafe path characters")
        self.project_dir = Path(project_dir).resolve()
        self.goal_id = goal_id
        self.events_dir = (
            self.project_dir / ".agentic-harness" / "runs" / goal_id / "events"
        )

    def append(
        self,
        *,
        stage: str,
        kind: str,
        summary: str,
        tool_name: str = "",
        tool_status: str = "",
        cycle: int = 0,
        checkpoint: str = "",
        progress: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        self.events_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.events_dir.chmod(0o700)
        seq = self._next_sequence()
        event: dict[str, Any] = {
            "schema": EVENT_SCHEMA,
            "goal_id": self.goal_id,
            "seq": seq,
            "at": now_iso(),
            "stage": _safe_label(stage, "act"),
            "kind": _safe_label(kind, "progress"),
            "summary": redact_secrets(str(summary))[:2_000],
            "cycle": max(0, int(cycle)),
            "checkpoint": redact_secrets(str(checkpoint))[:500],
            "evidence_id": f"event:{seq}",
        }
        if tool_name:
            event["tool"] = {
                "name": _safe_label(tool_name, "tool"),
                "status": _safe_label(tool_status, "finished"),
            }
        if progress:
            event["progress"] = {
                str(key): max(0, int(value))
                for key, value in progress.items()
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
            }
        self._write_json(self.events_dir / f"{seq:06d}.json", event)
        return event

    def read(self, *, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if not self.events_dir.exists():
            return []
        events: list[dict[str, Any]] = []
        for path in sorted(self.events_dir.glob("*.json")):
            if len(events) >= max(1, min(limit, 2_000)):
                break
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != EVENT_SCHEMA
                or payload.get("goal_id") != self.goal_id
                or not isinstance(payload.get("seq"), int)
                or int(payload["seq"]) <= after
            ):
                continue
            events.append(payload)
        return events

    def _next_sequence(self) -> int:
        latest = 0
        for path in self.events_dir.glob("*.json"):
            try:
                latest = max(latest, int(path.stem))
            except ValueError:
                continue
        return latest + 1

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        tmp: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.events_dir,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                tmp = Path(handle.name)
            tmp.chmod(0o600)
            tmp.replace(path)
            path.chmod(0o600)
        except Exception:
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


def _safe_label(value: str, fallback: str) -> str:
    normalized = str(value).strip().lower().replace(" ", "_")
    return normalized if _SAFE_VALUE.fullmatch(normalized) else fallback
