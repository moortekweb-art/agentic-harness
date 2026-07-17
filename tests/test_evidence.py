from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentic_harness.core.evidence import EvidenceRecord, EvidenceResult


SPEC_HASH = "a" * 64


def record(**overrides: object) -> EvidenceRecord:
    values: dict[str, object] = {
        "id": "review:unit-tests",
        "goal_id": "goal-123",
        "run_id": "run-456",
        "goal_spec_sha256": SPEC_HASH,
        "issuer": "harness.review",
        "kind": "deterministic_check",
        "result": EvidenceResult.VERIFIED,
        "covers": ("R1", "R3"),
    }
    values.update(overrides)
    return EvidenceRecord(**values)  # type: ignore[arg-type]


def test_evidence_v2_round_trips_and_is_frozen() -> None:
    evidence = record()

    assert EvidenceRecord.from_dict(evidence.to_dict()) == evidence
    with pytest.raises(FrozenInstanceError):
        evidence.result = EvidenceResult.FAILED  # type: ignore[misc]


def test_only_verified_exact_identity_and_declared_coverage_is_eligible() -> None:
    evidence = record()

    assert evidence.verifies(
        "R1",
        goal_id="goal-123",
        run_id="run-456",
        goal_spec_sha256=SPEC_HASH,
    )
    assert not evidence.verifies(
        "R2",
        goal_id="goal-123",
        run_id="run-456",
        goal_spec_sha256=SPEC_HASH,
    )
    assert not evidence.verifies(
        "R1",
        goal_id="goal-123",
        run_id="run-456",
        goal_spec_sha256="b" * 64,
    )


def test_invalidation_returns_new_ineligible_record() -> None:
    evidence = record()

    invalidated = evidence.invalidate()

    assert evidence.result is EvidenceResult.VERIFIED
    assert invalidated.result is EvidenceResult.INVALIDATED
    assert not invalidated.verifies(
        "R1",
        goal_id="goal-123",
        run_id="run-456",
        goal_spec_sha256=SPEC_HASH,
    )


def test_duplicate_declared_coverage_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        record(covers=("R1", "R1"))
