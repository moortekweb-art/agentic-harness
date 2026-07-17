"""Immutable evidence records for frozen goal specifications."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import Any


EVIDENCE_SCHEMA = "agentic_harness.evidence.v2"
_SAFE_EVIDENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SAFE_REQUIREMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceResult(StrEnum):
    """Meaning assigned by the harness when evidence is issued."""

    OBSERVED = "observed"
    PRODUCED = "produced"
    VERIFIED = "verified"
    FAILED = "failed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class EvidenceRecord:
    """One immutable statement about a specific goal run and GoalSpec."""

    id: str
    goal_id: str
    run_id: str
    goal_spec_sha256: str
    issuer: str
    kind: str
    result: EvidenceResult
    covers: tuple[str, ...] = ()
    schema: str = EVIDENCE_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "covers", tuple(self.covers))
        if self.schema != EVIDENCE_SCHEMA:
            raise ValueError(f"unsupported evidence schema: {self.schema}")
        if _SAFE_EVIDENCE_ID.fullmatch(self.id) is None:
            raise ValueError("evidence id is invalid")
        if not self.goal_id.strip():
            raise ValueError("evidence goal_id must not be empty")
        if not self.run_id.strip():
            raise ValueError("evidence run_id must not be empty")
        if _SHA256.fullmatch(self.goal_spec_sha256) is None:
            raise ValueError("evidence GoalSpec hash must be a lowercase SHA-256 digest")
        if not self.issuer.strip() or not self.kind.strip():
            raise ValueError("evidence issuer and kind must not be empty")
        if len(self.covers) != len(set(self.covers)):
            raise ValueError("evidence coverage contains duplicate requirement ids")
        if any(
            not isinstance(item, str) or _SAFE_REQUIREMENT_ID.fullmatch(item) is None
            for item in self.covers
        ):
            raise ValueError("evidence coverage contains an invalid requirement id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "goal_spec_sha256": self.goal_spec_sha256,
            "issuer": self.issuer,
            "kind": self.kind,
            "result": self.result.value,
            "covers": list(self.covers),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EvidenceRecord:
        covers = payload.get("covers")
        if not isinstance(covers, list) or not all(isinstance(item, str) for item in covers):
            raise ValueError("evidence covers must be a list of requirement ids")
        return cls(
            schema=str(payload.get("schema") or ""),
            id=str(payload.get("id") or ""),
            goal_id=str(payload.get("goal_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            goal_spec_sha256=str(payload.get("goal_spec_sha256") or ""),
            issuer=str(payload.get("issuer") or ""),
            kind=str(payload.get("kind") or ""),
            result=EvidenceResult(str(payload.get("result") or "")),
            covers=tuple(covers),
        )

    def verifies(
        self,
        requirement_id: str,
        *,
        goal_id: str,
        run_id: str,
        goal_spec_sha256: str,
    ) -> bool:
        """Return whether this record may close one frozen requirement."""

        return (
            self.result is EvidenceResult.VERIFIED
            and self.goal_id == goal_id
            and self.run_id == run_id
            and self.goal_spec_sha256 == goal_spec_sha256
            and requirement_id in self.covers
        )

    def invalidate(self) -> EvidenceRecord:
        """Return a new immutable record that can no longer satisfy requirements."""

        return replace(self, result=EvidenceResult.INVALIDATED)
