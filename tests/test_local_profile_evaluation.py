from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.score_local_profile_results import load_json, score  # noqa: E402


MATRIX = Path("evaluation/local_profile_matrix.json")


def complete_rows(*, fail_qwen: int = 0) -> list[dict[str, object]]:
    matrix = load_json(MATRIX)
    rows: list[dict[str, object]] = []
    qwen_seen = 0
    for task in matrix["tasks"]:
        for profile in task["profiles"]:
            failing = profile == "qwen-primary" and qwen_seen < fail_qwen
            if profile == "qwen-primary":
                qwen_seen += 1
            rows.append(
                {
                    "profile": profile,
                    "task_id": task["id"],
                    "run_id": f"{profile}-{task['id']}",
                    "deterministic_pass": not failing,
                    "false_verified": False,
                    "route_profile_correct": True,
                    "guardrail_violation": False,
                    "tool_calls_valid": True,
                    "retries": 0,
                    "elapsed_seconds": 1,
                }
            )
    return rows


def test_complete_profile_matrix_scores_supported_vision_boundary() -> None:
    summary = score(complete_rows(), load_json(MATRIX))
    assert summary["profiles"]["qwen-primary"]["supported_cases"] == 12
    assert summary["profiles"]["ornith-text"]["supported_cases"] == 11
    assert summary["profiles"]["qwen-primary"]["recommended"] is True
    assert summary["profiles"]["ornith-text"]["recommended"] is True


def test_profile_recommendation_fails_closed_on_quality_and_missing_rows() -> None:
    matrix = load_json(MATRIX)
    summary = score(complete_rows(fail_qwen=2), matrix)
    assert summary["profiles"]["qwen-primary"]["recommended"] is False
    with pytest.raises(ValueError, match="missing profile/task rows"):
        score(complete_rows()[:-1], matrix)


def test_matrix_and_protocol_are_versioned_and_documented() -> None:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    protocol = Path("evaluation/LOCAL_PROFILE_PROTOCOL.md").read_text(encoding="utf-8")
    assert matrix["schema"] == "agentic_harness.local_profile_matrix.v1"
    assert "zero false verified" in protocol.lower()
    assert "ornith is text/tool-only" in protocol.lower()
