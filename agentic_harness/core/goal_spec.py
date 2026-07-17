"""Immutable acceptance specification for one goal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
from typing import Any

from agentic_harness.core.state import now_iso


GOAL_SPEC_CONTRACT = "agentic_harness.goal_spec.v1"
SAFE_REQUIREMENT_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
_DERIVATIONS = {
    "harness_preserved_objective",
    "harness_derived",
    "operator_authored",
}
_APPROVALS = {"automatic", "pending", "operator_approved"}


def _is_iso_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


@dataclass(frozen=True)
class GoalRequirement:
    """One immutable completion condition in a GoalSpec."""

    id: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or SAFE_REQUIREMENT_ID.fullmatch(self.id) is None:
            raise ValueError("requirement id must be a safe identifier")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("requirement text must be a non-empty string")
        if self.text != self.text.strip():
            raise ValueError("requirement text must not have surrounding whitespace")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text}

    @classmethod
    def from_dict(cls, payload: object) -> GoalRequirement:
        if not isinstance(payload, dict):
            raise ValueError("goal requirement must be an object")
        return cls(id=payload.get("id"), text=payload.get("text"))  # type: ignore[arg-type]


@dataclass(frozen=True)
class GoalSpec:
    """Frozen, hash-addressed acceptance specification owned by the harness."""

    objective: str
    requirements: tuple[GoalRequirement, ...]
    derivation: str
    approval: str
    created_at: str = field(default_factory=now_iso)
    contract: str = GOAL_SPEC_CONTRACT
    sha256: str = ""

    def __post_init__(self) -> None:
        if self.contract != GOAL_SPEC_CONTRACT:
            raise ValueError(f"goal spec contract must be {GOAL_SPEC_CONTRACT!r}")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("goal spec objective must be a non-empty string")
        if self.objective != self.objective.strip():
            raise ValueError("goal spec objective must not have surrounding whitespace")
        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise ValueError("goal spec must contain at least one requirement")
        if not all(isinstance(item, GoalRequirement) for item in self.requirements):
            raise ValueError("goal spec requirements must be GoalRequirement values")
        requirement_ids = [item.id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("goal spec requirement ids must be unique")
        if self.derivation not in _DERIVATIONS:
            raise ValueError(f"unsupported goal spec derivation: {self.derivation!r}")
        if self.approval not in _APPROVALS:
            raise ValueError(f"unsupported goal spec approval: {self.approval!r}")
        if not isinstance(self.created_at, str) or not _is_iso_timestamp(self.created_at):
            raise ValueError("goal spec created_at must be an ISO timestamp")
        expected = self.computed_sha256()
        if self.sha256 and self.sha256 != expected:
            raise ValueError("goal spec sha256 does not match its canonical content")
        if not self.sha256:
            object.__setattr__(self, "sha256", expected)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "approval": self.approval,
            "contract": self.contract,
            "created_at": self.created_at,
            "derivation": self.derivation,
            "objective": self.objective,
            "requirements": [item.to_dict() for item in self.requirements],
        }

    def computed_sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "sha256": self.sha256}

    @classmethod
    def from_dict(cls, payload: object) -> GoalSpec:
        if not isinstance(payload, dict):
            raise ValueError("goal spec must be an object")
        raw_requirements = payload.get("requirements")
        if not isinstance(raw_requirements, list):
            raise ValueError("goal spec requirements must be a list")
        return cls(
            objective=payload.get("objective"),  # type: ignore[arg-type]
            requirements=tuple(GoalRequirement.from_dict(item) for item in raw_requirements),
            derivation=payload.get("derivation"),  # type: ignore[arg-type]
            approval=payload.get("approval"),  # type: ignore[arg-type]
            created_at=payload.get("created_at"),  # type: ignore[arg-type]
            contract=payload.get("contract"),  # type: ignore[arg-type]
            sha256=payload.get("sha256"),  # type: ignore[arg-type]
        )


def preserved_objective_spec(objective: str) -> GoalSpec:
    """Freeze the complete objective as a safe baseline acceptance condition."""

    normalized = objective.strip()
    return GoalSpec(
        objective=normalized,
        requirements=(GoalRequirement(id="R1", text=normalized),),
        derivation="harness_preserved_objective",
        approval="automatic",
    )
