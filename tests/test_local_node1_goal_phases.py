#!/usr/bin/env python3
"""Tests for typed local Node1 goal phase state helpers."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path


SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from local_node1_goal_phases import (  # noqa: E402
    GoalState,
    Phase,
    PhaseState,
    ReviewStatus,
    detect_phase_from_supervisor_output,
    goal_state_from_payload,
    migrate_legacy_goal_state,
    redact_secrets,
    validate_phase_transition,
    write_secure_file,
)


def test_json_phase_state_payload_is_authoritative() -> None:
    payload = {
        "phase_state": {
            "phase": "executing",
            "goal_id": "goal-123",
            "started_at": "2026-07-04T01:02:03Z",
            "transitions": [
                {
                    "from": "planning",
                    "to": "executing",
                    "timestamp": "2026-07-04T01:03:00Z",
                    "reason": "plan accepted",
                }
            ],
            "evidence": {"worker_pid": 1234},
            "confidence": 0.98,
        }
    }

    state = detect_phase_from_supervisor_output(json.dumps(payload))

    assert isinstance(state, PhaseState)
    assert state.phase is Phase.EXECUTING
    assert state.goal_id == "goal-123"
    assert state.started_at == "2026-07-04T01:02:03Z"
    assert state.transitions[0]["to"] == "executing"
    assert state.evidence == {"worker_pid": 1234}
    assert state.confidence == 0.98


def test_top_level_json_payload_aliases_status_to_phase() -> None:
    state = detect_phase_from_supervisor_output(
        json.dumps(
            {
                "status": "awaiting_review",
                "goal_id": "goal-review",
                "evidence": {"latest": "ready"},
            }
        )
    )

    assert state.phase is Phase.REVIEWING
    assert state.goal_id == "goal-review"
    assert state.evidence == {"latest": "ready"}
    assert state.confidence == 1.0


def test_json_embedded_in_text_is_detected_before_text_fallback() -> None:
    output = 'prefix says blocked\n{"phase": "done", "goal_id": "goal-done"}\nfooter'

    state = detect_phase_from_supervisor_output(output)

    assert state.phase is Phase.DONE
    assert state.goal_id == "goal-done"
    assert state.evidence == {"source": "supervisor_json"}


def test_text_fallback_detects_each_phase() -> None:
    cases = [
        ("No active goal; lane free.", Phase.IDLE),
        ("Planner produced a plan ready for execution.", Phase.PLANNING),
        ("Worker running in tmux; executing subgoal 2.", Phase.EXECUTING),
        ("Ready for review; awaiting review.", Phase.REVIEWING),
        ("Goal accepted and complete.", Phase.DONE),
        ("Blocked: needs human input.", Phase.BLOCKED),
        ("Worker failed with non-zero exit.", Phase.FAILED),
    ]

    for output, phase in cases:
        state = detect_phase_from_supervisor_output(output)
        assert state.phase is phase
        assert state.evidence["source"] == "text_fallback"
        assert state.confidence == 0.65


def test_malformed_json_falls_back_to_text_parsing() -> None:
    state = detect_phase_from_supervisor_output('{"phase": "executing"\nblocked')

    assert state.phase is Phase.BLOCKED
    assert state.evidence["source"] == "text_fallback"


def test_valid_phase_transitions() -> None:
    valid = [
        (Phase.IDLE, Phase.PLANNING),
        (Phase.PLANNING, Phase.EXECUTING),
        (Phase.EXECUTING, Phase.REVIEWING),
        (Phase.REVIEWING, Phase.DONE),
        (Phase.BLOCKED, Phase.EXECUTING),
        (Phase.FAILED, Phase.PLANNING),
        (Phase.DONE, Phase.IDLE),
    ]

    for old, new in valid:
        assert validate_phase_transition(old, new) is True


def test_invalid_phase_transitions_return_false() -> None:
    invalid = [
        (Phase.DONE, Phase.PLANNING),
        (Phase.IDLE, Phase.DONE),
        (Phase.PLANNING, Phase.REVIEWING),
        (Phase.FAILED, Phase.DONE),
    ]

    for old, new in invalid:
        assert validate_phase_transition(old, new) is False


def test_migrate_legacy_goal_state_from_boolean_flags() -> None:
    state = migrate_legacy_goal_state(
        {
            "generated_at": "2026-07-04T02:00:00Z",
            "classification": "working",
            "active_goal": {
                "tmux_running": True,
                "awaiting_review": False,
                "accepted": False,
                "run_dir": "/tmp/run",
                "prompt_path": "/tmp/run/prompt.md",
            },
            "runtime": {"complete_marker_path": "/tmp/run/complete.json"},
            "queue": {"running": 1},
        }
    )

    assert isinstance(state, GoalState)
    assert state.phase is Phase.EXECUTING
    assert state.accepted is False
    assert state.review_status is None
    assert state.artifacts == ["/tmp/run/prompt.md", "/tmp/run", "/tmp/run/complete.json"]
    assert state.last_updated == "2026-07-04T02:00:00Z"


def test_migrate_legacy_goal_state_review_and_accepted_flags() -> None:
    reviewing = migrate_legacy_goal_state(
        {"active_goal": {"awaiting_review": True}, "review": {"status": "needs_review"}}
    )
    accepted = migrate_legacy_goal_state(
        {"classification": "accepted", "active_goal": {"accepted": True}}
    )

    assert reviewing.phase is Phase.REVIEWING
    assert reviewing.review_status is ReviewStatus.PENDING
    assert accepted.phase is Phase.DONE
    assert accepted.accepted is True
    assert accepted.review_status is ReviewStatus.ACCEPTED


def test_goal_state_from_typed_payload() -> None:
    state = goal_state_from_payload(
        {
            "goal_state": {
                "phase": "blocked",
                "accepted": False,
                "review_status": "failed",
                "block_reason": "needs operator",
                "artifacts": ["/tmp/run/complete.json"],
                "last_updated": "2026-07-04T03:00:00Z",
            }
        }
    )

    assert state.phase is Phase.BLOCKED
    assert state.review_status is ReviewStatus.FAILED
    assert state.block_reason == "needs operator"
    assert state.to_dict()["phase"] == "blocked"


def test_write_secure_file_sets_requested_permissions(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "report.md"

    write_secure_file(state_path, "{}\n")
    write_secure_file(report_path, "# Report\n", 0o640)

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o640
    assert state_path.read_text(encoding="utf-8") == "{}\n"
    assert report_path.read_text(encoding="utf-8") == "# Report\n"


def test_redact_secrets_covers_required_patterns() -> None:
    text = "\n".join(
        [
            "openai=sk-proj-" + "A" * 24,
            "anthropic=sk-ant-" + "B" * 24,
            "api=sk-" + "C" * 24,
            "ghp=ghp_" + "D" * 36,
            "gho=gho_" + "E" * 36,
            "auth=Bearer abcDEF123-._~+/==",
            "url=https://user:password@example.com/path",
            "hex=" + "a" * 40,
            "b64=" + "QUJD" * 11,
        ]
    )

    redacted = redact_secrets(text)

    assert "[REDACTED:openai_key]" in redacted
    assert "[REDACTED:anthropic_key]" in redacted
    assert "[REDACTED:api_key]" in redacted
    assert redacted.count("[REDACTED:github_token]") == 2
    assert "[REDACTED:bearer_token]" in redacted
    assert "https://[REDACTED:url_credentials]@example.com/path" in redacted
    assert "[REDACTED:long_hex_token]" in redacted
    assert "[REDACTED:long_base64_token]" in redacted
    assert "password" not in redacted


def test_write_secure_file_redacts_secrets_on_disk(tmp_path) -> None:
    path = tmp_path / "state.json"

    write_secure_file(path, "stderr_tail=Bearer abcDEF123-._~+/==\n", 0o600)

    assert path.read_text(encoding="utf-8") == "stderr_tail=[REDACTED:bearer_token]\n"
