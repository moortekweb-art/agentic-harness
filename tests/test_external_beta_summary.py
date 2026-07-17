from __future__ import annotations

import json
from pathlib import Path

from evaluation.summarize_external_beta import summarize


def _receipt(index: int, *, status: str = "verified") -> dict[str, object]:
    return {
        "schema": "agentic_harness.external_beta_receipt.v1",
        "attempt_id": f"fixture-attempt-{index}",
        "submitted_at": "2026-07-17T00:00:00Z",
        "release": {
            "version": "0.12.0",
            "commit": "a" * 40,
            "wheel_sha256": "b" * 64,
        },
        "participant": {
            "anonymous_id_hash": f"fixture-user-{index % 2}",
            "maintainer": False,
        },
        "repository": {"ecosystem": ("python", "node", "rust")[index % 3]},
        "runtime": {
            "os": "fixture-os",
            "python": "3.14",
            "agent": "fixture-agent",
            "model": "fixture-model",
        },
        "outcome": {"status": status, "maintainer_intervention": False},
        "safety": {
            "credential_leak": False,
            "unsafe_unexpected_writes": False,
            "false_verified_completion": False,
            "unresolved_critical_or_high_defect": False,
        },
        "metrics": {"minutes_to_terminal_result": 1.0, "retries": 0},
    }


def _write_receipts(root: Path, statuses: list[str]) -> None:
    root.mkdir()
    for index, status in enumerate(statuses):
        (root / f"receipt-{index}.json").write_text(
            json.dumps(_receipt(index, status=status)),
            encoding="utf-8",
        )


def test_external_beta_gate_accepts_only_complete_threshold(tmp_path) -> None:
    receipts = tmp_path / "receipts"
    _write_receipts(receipts, ["verified", "verified", "verified", "verified", "failed"])

    result = summarize(receipts)

    assert result["gate_passed"] is True
    assert result["attempts"] == 5
    assert result["verified_without_maintainer"] == 4


def test_external_beta_gate_counts_abandoned_attempts_and_fails_threshold(tmp_path) -> None:
    receipts = tmp_path / "receipts"
    _write_receipts(receipts, ["verified", "verified", "verified", "abandoned", "failed"])

    result = summarize(receipts)

    assert result["attempts"] == 5
    assert result["gate_passed"] is False
    assert result["gates"]["verified_without_maintainer_threshold"] is False
