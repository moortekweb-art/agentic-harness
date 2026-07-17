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

_ACTION_WORDS = {
    "add", "allow", "build", "change", "check", "complete", "configure",
    "create", "delete", "deploy", "document", "enable", "ensure", "fix",
    "implement", "improve", "include", "keep", "make", "migrate", "move",
    "prevent", "publish", "refactor", "remove", "rename", "replace", "require",
    "restore", "run", "split", "support", "test", "update", "upgrade", "use",
    "validate", "verify",
}
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(?P<text>.+?)\s*$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\s*;\s*")
_SERIES_BOUNDARY = re.compile(r"\s*,\s*(?:and\s+|then\s+|also\s+)?", re.IGNORECASE)


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


def derive_goal_requirements(objective: str) -> tuple[GoalRequirement, ...]:
    """Derive ordered conditions without guessing when objective prose is ambiguous."""

    normalized = objective.strip()
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    list_items = [
        match.group("text").strip()
        for line in lines
        if (match := _LIST_ITEM.fullmatch(line)) is not None
    ]
    parts: list[str]
    if len(list_items) >= 2:
        parts = [
            match.group("text").strip() if (match := _LIST_ITEM.fullmatch(line)) else line
            for line in lines
        ]
    else:
        parts = []
        for sentence in _SENTENCE_BOUNDARY.split(" ".join(normalized.split())):
            cleaned = sentence.strip()
            if cleaned:
                parts.extend(_split_action_series(cleaned))
    if len(parts) < 2:
        return (GoalRequirement(id="R1", text=normalized),)
    return tuple(
        GoalRequirement(id=f"R{index}", text=_completion_condition(part))
        for index, part in enumerate(parts, 1)
    )


def derived_objective_spec(objective: str, *, approval: str = "automatic") -> GoalSpec:
    """Freeze a harness-owned specification before worker execution."""

    normalized = objective.strip()
    requirements = derive_goal_requirements(normalized)
    return GoalSpec(
        objective=normalized,
        requirements=requirements,
        derivation=(
            "harness_derived"
            if len(requirements) > 1
            else "harness_preserved_objective"
        ),
        approval=approval,
    )


def _split_action_series(text: str) -> list[str]:
    core = text[:-1] if text[-1:] in {".", "!", "?"} else text
    comma_parts = [part.strip() for part in _SERIES_BOUNDARY.split(core)]
    if len(comma_parts) >= 2 and all(_starts_with_action(part) for part in comma_parts):
        return comma_parts
    return [text]


def _starts_with_action(text: str) -> bool:
    first = re.match(r"[A-Za-z]+", text)
    return first is not None and first.group(0).lower() in _ACTION_WORDS


def _completion_condition(text: str) -> str:
    cleaned = text.strip().rstrip(".!?:").strip()
    if not cleaned:
        raise ValueError("derived completion condition must not be empty")
    return cleaned[0].upper() + cleaned[1:] + "."
