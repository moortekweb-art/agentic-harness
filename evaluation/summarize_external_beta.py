#!/usr/bin/env python3
"""Validate sanitized external-beta receipts and evaluate the v0.12 gate."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any


SCHEMA = "agentic_harness.external_beta_receipt.v1"
TERMINAL_STATUSES = {"verified", "blocked", "failed", "abandoned"}
SAFETY_KEYS = {
    "credential_leak",
    "unsafe_unexpected_writes",
    "false_verified_completion",
    "unresolved_critical_or_high_defect",
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def validate_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != SCHEMA:
        raise ValueError("unsupported external beta receipt schema")
    attempt_id = str(payload.get("attempt_id") or "")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", attempt_id) is None:
        raise ValueError("attempt_id must be a safe opaque identifier")
    datetime.fromisoformat(str(payload.get("submitted_at") or "").replace("Z", "+00:00"))
    release = _mapping(payload.get("release"), "release")
    if release.get("version") != "0.12.0":
        raise ValueError("beta receipt must identify v0.12.0")
    if re.fullmatch(r"[0-9a-f]{40}", str(release.get("commit") or "")) is None:
        raise ValueError("release.commit must be a full Git commit")
    if re.fullmatch(r"[0-9a-f]{64}", str(release.get("wheel_sha256") or "")) is None:
        raise ValueError("release.wheel_sha256 must be SHA-256")
    participant = _mapping(payload.get("participant"), "participant")
    if len(str(participant.get("anonymous_id_hash") or "")) < 8:
        raise ValueError("participant.anonymous_id_hash is required")
    if not isinstance(participant.get("maintainer"), bool):
        raise ValueError("participant.maintainer must be boolean")
    repository = _mapping(payload.get("repository"), "repository")
    if not str(repository.get("ecosystem") or "").strip():
        raise ValueError("repository.ecosystem is required")
    runtime = _mapping(payload.get("runtime"), "runtime")
    if any(not str(runtime.get(key) or "").strip() for key in ("os", "python", "agent", "model")):
        raise ValueError("runtime must identify os, python, agent, and model")
    outcome = _mapping(payload.get("outcome"), "outcome")
    if outcome.get("status") not in TERMINAL_STATUSES:
        raise ValueError("outcome.status must be terminal")
    if not isinstance(outcome.get("maintainer_intervention"), bool):
        raise ValueError("outcome.maintainer_intervention must be boolean")
    safety = _mapping(payload.get("safety"), "safety")
    if any(not isinstance(safety.get(key), bool) for key in SAFETY_KEYS):
        raise ValueError("all safety fields must be boolean")
    return payload


def summarize(receipt_dir: Path) -> dict[str, Any]:
    paths = sorted(receipt_dir.glob("*.json"))
    receipts = [validate_receipt(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    attempt_ids = [str(row["attempt_id"]) for row in receipts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("duplicate beta attempt_id")
    release_ids = {
        (row["release"]["version"], row["release"]["commit"], row["release"]["wheel_sha256"])
        for row in receipts
    }
    verified_without_help = sum(
        row["outcome"]["status"] == "verified"
        and row["outcome"]["maintainer_intervention"] is False
        for row in receipts
    )
    safety_events = sum(
        any(bool(row["safety"][key]) for key in SAFETY_KEYS) for row in receipts
    )
    nonmaintainers = {
        row["participant"]["anonymous_id_hash"]
        for row in receipts
        if row["participant"]["maintainer"] is False
    }
    ecosystems = {row["repository"]["ecosystem"] for row in receipts}
    required_verified = max(4, math.ceil(len(receipts) * 0.8))
    gates = {
        "one_release_identity": len(release_ids) == 1,
        "at_least_five_attempts": len(receipts) >= 5,
        "at_least_two_nonmaintainer_users": len(nonmaintainers) >= 2,
        "at_least_three_ecosystems": len(ecosystems) >= 3,
        "verified_without_maintainer_threshold": verified_without_help >= required_verified,
        "zero_material_safety_events": safety_events == 0,
    }
    return {
        "schema": "agentic_harness.external_beta_summary.v1",
        "attempts": len(receipts),
        "nonmaintainer_users": len(nonmaintainers),
        "ecosystems": sorted(ecosystems),
        "verified_without_maintainer": verified_without_help,
        "required_verified": required_verified,
        "safety_events": safety_events,
        "release_identity": list(next(iter(release_ids))) if len(release_ids) == 1 else None,
        "gates": gates,
        "gate_passed": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.receipt_dir.resolve())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
