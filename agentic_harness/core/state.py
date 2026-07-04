"""Versioned goal state and transition rules."""

from __future__ import annotations

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


VALID_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.PENDING: {GoalStatus.PLANNING, GoalStatus.FAILED},
    GoalStatus.PLANNING: {GoalStatus.IN_PROGRESS, GoalStatus.FAILED},
    GoalStatus.IN_PROGRESS: {GoalStatus.REVIEW, GoalStatus.FAILED},
    GoalStatus.REVIEW: {GoalStatus.IN_PROGRESS, GoalStatus.DONE, GoalStatus.FAILED},
    GoalStatus.DONE: set(),
    GoalStatus.FAILED: {GoalStatus.PLANNING},
}


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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

    def transition(self, status: GoalStatus, *, reason: str = "") -> None:
        if status not in VALID_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot transition goal {self.id} from {self.status} to {status}"
            )
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
            "metadata": dict(self.metadata),
            "review": self.review,
            "error": self.error,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Goal":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported goal schema: {payload.get('schema_version')}")
        goal = cls(
            objective=str(payload["objective"]),
            id=str(payload["id"]),
            status=GoalStatus(str(payload["status"])),
            schema_version=str(payload["schema_version"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            artifacts=list(payload.get("artifacts") or []),
            metadata=dict(payload.get("metadata") or {}),
            review=payload.get("review"),
            error=payload.get("error"),
            history=list(payload.get("history") or []),
        )
        return goal

