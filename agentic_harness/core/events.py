"""Durable, sanitized progress events for GUI and CLI consumers."""

from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any

from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.evidence import EvidenceRecord, EvidenceResult
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.state import SAFE_GOAL_ID, now_iso


EVENT_SCHEMA = "agentic_harness.task_event.v1"
_SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class TaskEventStore:
    """Append one atomic event file at a time without exposing tool payloads."""

    def __init__(
        self,
        project_dir: str | Path,
        goal_id: str,
        *,
        run_id: str = "",
        goal_spec_sha256: str = "",
    ) -> None:
        if SAFE_GOAL_ID.fullmatch(goal_id) is None:
            raise ValueError("goal id contains unsafe path characters")
        if run_id and SAFE_GOAL_ID.fullmatch(run_id) is None:
            raise ValueError("run id contains unsafe characters")
        self.project_dir = Path(project_dir).resolve()
        self.goal_id = goal_id
        self.run_id = run_id
        self.goal_spec_sha256 = goal_spec_sha256
        self._artifact_store = ArtifactStore(self.project_dir / ".agentic-harness")
        self.events_dir = self._contained_events_dir()

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
        events_dir = self._contained_events_dir()
        events_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        events_dir = self._contained_events_dir()
        events_dir.chmod(0o700)
        seq = self._next_sequence(events_dir)
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
        if self.run_id:
            event["run_id"] = self.run_id
        if self.run_id and self.goal_spec_sha256:
            event["evidence"] = EvidenceRecord(
                id=f"event:{seq}",
                goal_id=self.goal_id,
                run_id=self.run_id,
                goal_spec_sha256=self.goal_spec_sha256,
                issuer="harness.task_event",
                kind=_safe_label(kind, "progress"),
                result=(
                    EvidenceResult.OBSERVED
                    if tool_status in {"passed", "completed"}
                    else EvidenceResult.FAILED
                ),
                covers=(),
            ).to_dict()
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
        self._write_json(events_dir, events_dir / f"{seq:06d}.json", event)
        return event

    def read(
        self,
        *,
        after: int = 0,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        events_dir = self._contained_events_dir()
        if not events_dir.exists():
            return []
        maximum = None if limit is None else max(1, min(limit, 2_000))
        events: list[dict[str, Any]] = []
        for path in sorted(events_dir.glob("*.json")):
            if maximum is not None and len(events) >= maximum:
                break
            try:
                if path.is_symlink():
                    continue
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

    def _contained_events_dir(self) -> Path:
        run_dir = self._artifact_store.goal_dir(self.goal_id)
        events_dir = run_dir / "events"
        if events_dir.is_symlink():
            raise ValueError("task events directory must not be a symlink")
        resolved = events_dir.resolve()
        try:
            resolved.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("task events directory is outside goal directory") from exc
        return resolved

    def _next_sequence(self, events_dir: Path) -> int:
        latest = 0
        for path in events_dir.glob("*.json"):
            try:
                latest = max(latest, int(path.stem))
            except ValueError:
                continue
        return latest + 1

    def _write_json(
        self,
        events_dir: Path,
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        tmp: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=events_dir,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                tmp = Path(handle.name)
            tmp.chmod(0o600)
            if self._contained_events_dir() != events_dir:
                raise ValueError("task events directory changed during write")
            tmp.replace(path)
            path.chmod(0o600)
        except Exception:
            if tmp is not None and tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


def _safe_label(value: str, fallback: str) -> str:
    normalized = str(value).strip().lower().replace(" ", "_")
    return normalized if _SAFE_VALUE.fullmatch(normalized) else fallback
