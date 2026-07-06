"""Versioned goal state and transition rules."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from agentic_harness.core.errors import InvalidTransitionError

SCHEMA_VERSION = "agentic_harness.goal.v1"


class GoalStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True for DONE and FAILED — states that cannot continue."""
        return self in {GoalStatus.DONE, GoalStatus.FAILED}

    @property
    def is_active(self) -> bool:
        """True for states that can still take action (not terminal, not pending)."""
        return self in {GoalStatus.PLANNING, GoalStatus.IN_PROGRESS, GoalStatus.REVIEW}


VALID_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.PENDING: {GoalStatus.PLANNING, GoalStatus.FAILED},
    GoalStatus.PLANNING: {GoalStatus.IN_PROGRESS, GoalStatus.FAILED},
    GoalStatus.IN_PROGRESS: {GoalStatus.REVIEW, GoalStatus.FAILED},
    GoalStatus.REVIEW: {GoalStatus.IN_PROGRESS, GoalStatus.DONE, GoalStatus.FAILED},
    GoalStatus.DONE: set(),
    GoalStatus.FAILED: {GoalStatus.PLANNING},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _deep_copy_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return a deep copy of a dict, recursing into nested dicts and lists."""
    if value is None:
        return {}
    result: dict[str, Any] = {}
    for key, val in value.items():
        if isinstance(val, dict):
            result[key] = _deep_copy_dict(val)
        elif isinstance(val, list):
            result[key] = [
                _deep_copy_dict(item) if isinstance(item, dict) else item for item in val
            ]
        else:
            result[key] = val
    return result


def _make_json_safe(value: Any) -> Any:
    """Convert a value to be JSON-serializable.

    Handles sets (converts to sorted list), frozensets, and other non-serializable
    types by converting them to strings. Preserves None, bool, int, float, str,
    list, and dict values as-is.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_make_json_safe(item) for item in value)
    return str(value)


def _make_hashable(value: Any) -> Any:
    """Convert a value to a hashable form for use in __hash__.

    Recursively converts dicts to sorted tuples of (key, hashable_value) pairs
    and lists to tuples. Sets/frozensets become sorted tuples. Non-hashable
    leaf values are converted to their string representation.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return tuple((k, _make_hashable(v)) for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_make_hashable(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(_make_hashable(item) for item in value))
    return hash(str(value))


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not isinstance(value, str):
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


@dataclass
class Goal:
    """A single goal moving through the harness state machine."""

    objective: str
    id: str = field(default_factory=lambda: uuid4().hex)
    status: GoalStatus = GoalStatus.PENDING
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] | None = None
    error: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Goal):
            return NotImplemented
        return (
            self.id == other.id
            and self.objective == other.objective
            and self.status == other.status
            and self.schema_version == other.schema_version
            and self.created_at == other.created_at
            and self.updated_at == other.updated_at
            and self.artifacts == other.artifacts
            and self.metadata == other.metadata
            and self.review == other.review
            and self.error == other.error
            and self.history == other.history
        )

    def __hash__(self) -> int:
        """Hash based on the same fields as __eq__ to maintain the hash/eq contract.

        Goal is mutable (not frozen), so hashing on all __eq__ fields means
        mutating a Goal after it is placed in a set/dict will break lookup.
        This is intentional: Goal objects are managed by ArtifactStore which
        handles persistence and locking; they should not be used as set/dict
        keys across mutations.
        """
        return hash(
            (
                self.id,
                self.objective,
                self.status,
                self.schema_version,
                self.created_at,
                self.updated_at,
                tuple(sorted(self.artifacts)),
                _make_hashable(self.metadata) if self.metadata else (),
                self.review,
                self.error,
                tuple(
                    _make_hashable(entry) if isinstance(entry, dict) else entry
                    for entry in self.history
                ),
            )
        )

    @property
    def duration_seconds(self) -> float | None:
        """Elapsed seconds between created_at and updated_at, or None if timestamps are unparseable."""
        start = _parse_iso(self.created_at)
        end = _parse_iso(self.updated_at)
        if start is None or end is None:
            return None
        delta = (end - start).total_seconds()
        return max(0.0, delta)

    @property
    def status_chain(self) -> list[str]:
        """Return the ordered list of status values from history, plus current status."""
        chain = [entry["to"] for entry in self.history]
        if self.status not in chain:
            chain.append(self.status.value)
        return chain

    @property
    def has_artifacts(self) -> bool:
        """True if the goal has any recorded artifacts."""
        return bool(self.artifacts)

    @property
    def is_complete(self) -> bool:
        """True if the goal is in a terminal state (DONE or FAILED)."""
        return self.status.is_terminal

    @property
    def last_transition_reason(self) -> str | None:
        """Return the reason for the last transition, or None if no transitions have occurred."""
        if not self.history:
            return None
        reason = self.history[-1].get("reason", "")
        return str(reason) if reason is not None else ""

    def validate(self) -> list[str]:
        """Return a list of validation error messages. Empty list means valid."""
        errors: list[str] = []
        if not isinstance(self.objective, str) or not self.objective.strip():
            errors.append("objective must be a non-empty string")
        if not isinstance(self.id, str) or not self.id.strip():
            errors.append("id must be a non-empty string")
        if self.schema_version != SCHEMA_VERSION:
            errors.append(f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}")
        if self.created_at and not _parse_iso(self.created_at):
            errors.append("created_at must be a valid ISO timestamp")
        if self.updated_at and not _parse_iso(self.updated_at):
            errors.append("updated_at must be a valid ISO timestamp")
        if not isinstance(self.artifacts, list):
            errors.append("artifacts must be a list")
        if not isinstance(self.metadata, dict):
            errors.append("metadata must be a dict")
        if self.review is not None and not isinstance(self.review, dict):
            errors.append("review must be a dict or null")
        if self.error is not None and not isinstance(self.error, str):
            errors.append("error must be a string or null")
        if not isinstance(self.history, list):
            errors.append("history must be a list")
        for i, entry in enumerate(self.history):
            if not isinstance(entry, dict):
                errors.append(f"history[{i}] must be a dict")
                continue
            for key in ("from", "to", "at"):
                if key not in entry:
                    errors.append(f"history[{i}] missing required key {key!r}")
        errors.extend(self._validate_transitions())
        return errors

    def _validate_transitions(self) -> list[str]:
        """Validate that the current status is reachable from the history chain.

        This catches corrupt state where the recorded history says the goal
        ended in one status but self.status reports a different one. A goal
        whose history ends in DONE but self.status is IN_PROGRESS is corrupt.
        """
        errors: list[str] = []
        if not self.history:
            # No transitions recorded. This can mean either:
            # 1. A fresh goal constructed with status=PENDING (valid).
            # 2. A goal constructed with a non-PENDING status directly
            #    (also valid — constructors allow this).
            # 3. A goal whose history was truncated/corrupted (unlikely
            #    to distinguish from (2) without metadata).
            # We only flag this as corrupt if the status is a known
            # terminal state, since a terminal goal with no history is
            # almost certainly corrupt.
            if self.status.is_terminal:
                errors.append(
                    f"goal has no history but status is {self.status.value} "
                    f"(terminal); expected history or PENDING status"
                )
            return errors
        # First pass: collect well-formed entries and check for structural
        # issues (missing keys, non-string values) so we can safely index
        # into the last entry below.
        well_formed: list[tuple[int, dict[str, Any]]] = []
        for i, entry in enumerate(self.history):
            if not isinstance(entry, dict):
                continue
            from_val = entry.get("from")
            to_val = entry.get("to")
            if isinstance(from_val, str) and isinstance(to_val, str):
                well_formed.append((i, entry))
        if not well_formed:
            # No well-formed entries at all — can't validate transitions.
            return errors
        # Walk the well-formed entries and compare the last 'to' with self.status.
        last_entry = well_formed[-1][1]
        expected = last_entry["to"]
        if expected != self.status.value:
            errors.append(
                f"history ends at {expected} but status is {self.status.value}; state is corrupt"
            )
        # Verify each well-formed transition is legal.
        for i, entry in well_formed:
            from_val = entry["from"]
            to_val = entry["to"]
            try:
                from_status = GoalStatus(from_val)
                to_status = GoalStatus(to_val)
            except ValueError:
                errors.append(f"history[{i}] has invalid status: from={from_val!r}, to={to_val!r}")
                continue
            if to_status not in VALID_TRANSITIONS.get(from_status, set()):
                errors.append(f"history[{i}] illegal transition: {from_val} -> {to_val}")
        return errors

    def transition(self, status: GoalStatus, *, reason: str = "") -> None:
        if status not in VALID_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition goal {self.id} from {self.status} to {status}"
            )
        if not isinstance(reason, str):
            raise TypeError(f"reason must be a string, got {type(reason).__name__}")
        old = self.status
        self.status = status
        self.updated_at = now_iso()
        self.history.append(
            {
                "from": old.value,
                "to": status.value,
                "at": self.updated_at,
                "reason": reason,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "objective": self.objective,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "artifacts": list(self.artifacts),
            "metadata": _make_json_safe(_deep_copy_dict(self.metadata)),
            "review": _make_json_safe(_deep_copy_dict(self.review)) if self.review else None,
            "error": self.error,
            "history": [dict(entry) for entry in self.history],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Goal":
        if not isinstance(payload, dict):
            raise ValueError(f"goal payload must be a mapping, got {type(payload).__name__}")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported goal schema: {payload.get('schema_version')}")
        required = ("objective", "id", "status", "schema_version", "created_at", "updated_at")
        missing = [key for key in required if key not in payload or payload[key] is None]
        if missing:
            raise ValueError(f"missing required goal fields: {missing}")
        known = {
            "schema_version",
            "id",
            "objective",
            "status",
            "created_at",
            "updated_at",
            "artifacts",
            "metadata",
            "review",
            "error",
            "history",
        }
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError(
                f"goal payload has unknown field(s): {', '.join(unknown)}; "
                f"known fields: {', '.join(sorted(known))}"
            )
        artifacts_raw = payload.get("artifacts")
        if artifacts_raw is None:
            artifacts: list[str] = []
        elif isinstance(artifacts_raw, list):
            artifacts = [str(item) for item in artifacts_raw if item is not None]
        else:
            raise ValueError(
                f"goal payload 'artifacts' must be a list, got {type(artifacts_raw).__name__}"
            )

        metadata_raw = payload.get("metadata")
        if metadata_raw is None:
            metadata: dict[str, Any] = {}
        elif isinstance(metadata_raw, dict):
            metadata = metadata_raw
        else:
            raise ValueError(
                f"goal payload 'metadata' must be a mapping, got {type(metadata_raw).__name__}"
            )

        history_raw = payload.get("history")
        if history_raw is None:
            history: list[dict[str, Any]] = []
        elif isinstance(history_raw, list):
            history = []
            for i, item in enumerate(history_raw):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"goal payload 'history[{i}]' must be a mapping, got {type(item).__name__}"
                    )
                history.append(item)
        else:
            raise ValueError(
                f"goal payload 'history' must be a list, got {type(history_raw).__name__}"
            )

        review_raw = payload.get("review")
        if review_raw is not None and not isinstance(review_raw, dict):
            raise ValueError(
                f"goal payload 'review' must be a mapping or null, got {type(review_raw).__name__}"
            )

        error_raw = payload.get("error")
        if error_raw is not None and not isinstance(error_raw, str):
            raise ValueError(
                f"goal payload 'error' must be a string or null, got {type(error_raw).__name__}"
            )

        objective = str(payload["objective"]).strip()
        if not objective:
            raise ValueError("goal payload 'objective' must be a non-empty string")

        goal = cls(
            objective=objective,
            id=str(payload["id"]),
            status=GoalStatus(str(payload["status"])),
            schema_version=str(payload["schema_version"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            artifacts=artifacts,
            metadata=metadata,
            review=review_raw,
            error=error_raw,
            history=history,
        )
        return goal

    def to_json(self, indent: int | None = None) -> str:
        """Serialize this goal to a JSON string.

        Convenience wrapper around ``to_dict`` that applies the schema-safe
        sanitization automatically, so callers do not need to remember to pass
        the dict through ``json.dumps`` with a separate sanitization step.
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "Goal":
        """Deserialize a goal from a JSON string.

        Convenience wrapper around ``from_dict`` that parses JSON first, so
        callers do not need to ``json.loads`` separately.
        """
        payload = json.loads(text)
        return cls.from_dict(payload)
