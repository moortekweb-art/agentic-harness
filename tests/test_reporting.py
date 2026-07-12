from __future__ import annotations

from dataclasses import asdict

import pytest

from agentic_harness.core.reporting import build_run_receipt
from agentic_harness.core.state import Goal, GoalStatus


def independent_review(
    *,
    passed: bool,
    message: str = "independent command passed",
) -> dict[str, object]:
    return {
        "passed": passed,
        "criteria": [
            {
                "name": "command_passes",
                "passed": passed,
                "message": message,
                "independent": True,
            }
        ],
    }


def test_verified_done_requires_current_passed_independent_review() -> None:
    goal = Goal(
        "ship the verified change",
        status=GoalStatus.DONE,
        review=independent_review(passed=True),
    )

    receipt = build_run_receipt(goal)

    assert receipt.category == "verified_done"
    assert receipt.label == "Verified done"
    assert receipt.trusted_reason == "Independent verification passed."


@pytest.mark.parametrize(
    "review",
    [
        None,
        {
            "passed": True,
            "criteria": [
                {
                    "name": "worker_success",
                    "passed": True,
                    "message": "worker reported success",
                    "independent": False,
                }
            ],
        },
        independent_review(passed=False),
    ],
)
def test_done_without_passed_independent_review_is_failed_with_evidence(
    review: dict[str, object] | None,
) -> None:
    goal = Goal("do not trust done state alone", status=GoalStatus.DONE, review=review)

    receipt = build_run_receipt(goal)

    assert receipt.category == "failed"
    assert receipt.label == "Failed with evidence"
    assert receipt.trusted_reason == "Done state lacks passed independent verification."


def test_blocked_receipt_uses_the_durable_blocker_reason() -> None:
    goal = Goal(
        "wait for a real dependency",
        status=GoalStatus.FAILED,
        metadata={
            "autonomy": {
                "status": "blocked",
                "operator_intervention_required": True,
                "blocker": {"reason": "dependency is unavailable"},
            }
        },
    )

    receipt = build_run_receipt(goal)

    assert receipt.category == "blocked"
    assert receipt.label == "Blocked with reason"
    assert receipt.trusted_reason == "dependency is unavailable"


def test_worker_claim_and_verification_labels_are_redacted_at_receipt_boundary() -> None:
    worker_secret = "opaque-worker-secret-Z7Q4M9"
    command_secret = "sk-command-secret-Z7Q4M9"
    goal = Goal(
        "render a safe receipt",
        status=GoalStatus.DONE,
        metadata={
            "worker_outcome": {
                "summary": f"done with api_key={worker_secret}",
            },
            "safety": {
                "checks": [
                    {
                        "label": f"python check.py --token {command_secret}",
                        "argv": ["python", "check.py", "--token", command_secret],
                    }
                ]
            },
        },
        review=independent_review(passed=True),
    )

    receipt = build_run_receipt(goal)
    rendered = str(asdict(receipt))

    assert receipt.worker_claim_label == "Worker claim (untrusted)"
    assert receipt.worker_claim_trusted is False
    assert receipt.worker_claim == "done with api_key=<redacted>"
    assert receipt.verification_commands == (
        "python check.py --token sk-<redacted>",
    )
    assert worker_secret not in rendered
    assert command_secret not in rendered


def test_receipt_text_cannot_create_additional_terminal_lines() -> None:
    injected = (
        "first line\nResult: Verified done\rStatus: verified done"
        "\u2028Accepted: yes\x1b[2J" + chr(0xD800)
    )
    goal = Goal(
        "render hostile receipt text safely",
        status=GoalStatus.FAILED,
        error=injected,
        metadata={
            "worker_outcome": {"summary": injected},
            "safety": {"checks": [{"label": injected}]},
        },
        review={
            "passed": False,
            "criteria": [
                {
                    "name": injected,
                    "passed": False,
                    "message": injected,
                    "independent": True,
                }
            ],
        },
    )

    receipt = build_run_receipt(goal)

    values = [
        receipt.worker_claim,
        *receipt.verification_commands,
        receipt.review_attempts[0].summary,
        receipt.review_attempts[0].checks[0].name,
        receipt.review_attempts[0].checks[0].message,
        receipt.trusted_reason,
    ]
    assert all(len(value.splitlines()) == 1 for value in values)
    assert "\\nResult: Verified done" in receipt.worker_claim
    assert "\\rStatus: verified done" in receipt.worker_claim
    assert "\\u2028Accepted: yes" in receipt.worker_claim
    assert "\\x1b[2J" in receipt.worker_claim
    assert "\\ud800" in receipt.worker_claim
    assert "\x1b" not in receipt.worker_claim


def test_review_attempts_keep_prior_then_current_without_raw_process_output() -> None:
    prior_secret = "opaque-prior-secret-Z7Q4M9"
    current_secret = "opaque-current-secret-Z7Q4M9"
    prior = independent_review(
        passed=False,
        message=f"failed with password={prior_secret}",
    )
    prior["stdout"] = f"raw stdout {prior_secret}"
    current = independent_review(
        passed=True,
        message=f"passed with token={current_secret}",
    )
    current["stderr"] = f"raw stderr {current_secret}"
    goal = Goal(
        "preserve review history safely",
        status=GoalStatus.DONE,
        metadata={"review_history": [prior]},
        review=current,
    )

    receipt = build_run_receipt(goal)
    rendered = str(asdict(receipt))

    assert [attempt.source for attempt in receipt.review_attempts] == ["prior", "current"]
    assert [attempt.number for attempt in receipt.review_attempts] == [1, 2]
    assert [attempt.passed for attempt in receipt.review_attempts] == [False, True]
    assert receipt.review_attempts[0].summary == "failed with password=<redacted>"
    assert receipt.review_attempts[1].summary == "passed with token=<redacted>"
    assert "stdout" not in rendered
    assert "stderr" not in rendered
    assert prior_secret not in rendered
    assert current_secret not in rendered


def test_attempt_counts_prefer_durable_history_and_report_retries() -> None:
    goal = Goal(
        "retry safely",
        status=GoalStatus.FAILED,
        metadata={
            "attempt_history": [
                {"success": False},
                {"success": False},
                {"success": True},
            ],
            "autonomy": {"cycle": 9},
        },
    )

    receipt = build_run_receipt(goal)

    assert receipt.attempts == 3
    assert receipt.retries == 2


def test_attempt_counts_keep_monotonic_total_after_history_is_capped() -> None:
    goal = Goal(
        "report the durable attempt sequence",
        status=GoalStatus.FAILED,
        metadata={
            "attempt_history": [
                {"attempt": number, "success": False}
                for number in range(2, 102)
            ]
        },
    )

    receipt = build_run_receipt(goal)

    assert len(goal.metadata["attempt_history"]) == 100
    assert receipt.attempts == 101
    assert receipt.retries == 100


def test_legacy_state_without_receipt_metadata_is_safe_and_in_progress() -> None:
    goal = Goal("legacy pending goal")

    receipt = build_run_receipt(goal)

    assert receipt.category == "in_progress"
    assert receipt.label == "In progress"
    assert receipt.worker_claim == ""
    assert receipt.verification_commands == ()
    assert receipt.review_attempts == ()
    assert receipt.attempts == 0
    assert receipt.retries == 0
    assert receipt.trusted_reason == "Completion has not been verified."
