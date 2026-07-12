"""Typed, redacted receipts derived from durable goal state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agentic_harness.core.presentation import safe_inline_text
from agentic_harness.core.safety import format_command
from agentic_harness.core.state import Goal, GoalStatus

ReceiptCategory = Literal["verified_done", "blocked", "failed", "in_progress"]
ReviewSource = Literal["prior", "current"]

_LABELS: dict[ReceiptCategory, str] = {
    "verified_done": "Verified done",
    "blocked": "Blocked with reason",
    "failed": "Failed with evidence",
    "in_progress": "In progress",
}


@dataclass(frozen=True)
class ReviewCheckReceipt:
    name: str
    passed: bool
    message: str
    independent: bool


@dataclass(frozen=True)
class ReviewAttemptReceipt:
    number: int
    source: ReviewSource
    passed: bool
    summary: str
    checks: tuple[ReviewCheckReceipt, ...]


@dataclass(frozen=True)
class RunReceipt:
    category: ReceiptCategory
    label: str
    worker_claim_label: str
    worker_claim_trusted: bool
    worker_claim: str
    verification_commands: tuple[str, ...]
    review_attempts: tuple[ReviewAttemptReceipt, ...]
    attempts: int
    retries: int
    trusted_reason: str


def build_run_receipt(goal: Goal) -> RunReceipt:
    """Build a safe presentation model without trusting worker-authored prose."""
    category = _category(goal)
    review_attempts = _review_attempts(goal)
    attempts = _attempt_count(goal, len(review_attempts))
    return RunReceipt(
        category=category,
        label=_LABELS[category],
        worker_claim_label="Worker claim (untrusted)",
        worker_claim_trusted=False,
        worker_claim=_worker_claim(goal),
        verification_commands=_verification_commands(goal),
        review_attempts=review_attempts,
        attempts=attempts,
        retries=max(0, attempts - 1),
        trusted_reason=_trusted_reason(goal, category, review_attempts),
    )


def _category(goal: Goal) -> ReceiptCategory:
    if goal.status is GoalStatus.DONE:
        return "verified_done" if _passed_independent_review(goal.review) else "failed"
    if _is_blocked(goal):
        return "blocked"
    if goal.status is GoalStatus.FAILED:
        return "failed"
    return "in_progress"


def _passed_independent_review(review: object) -> bool:
    if not isinstance(review, dict) or review.get("passed") is not True:
        return False
    criteria = review.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return False
    has_independent = False
    for criterion in criteria:
        if not isinstance(criterion, dict) or criterion.get("passed") is not True:
            return False
        has_independent = has_independent or criterion.get("independent") is True
    return has_independent


def _is_blocked(goal: Goal) -> bool:
    autonomy = goal.metadata.get("autonomy")
    if not isinstance(autonomy, dict):
        return False
    if str(autonomy.get("status") or "").strip().lower() == "blocked":
        return True
    if autonomy.get("operator_intervention_required") is True:
        return True
    budget = autonomy.get("budget")
    return isinstance(budget, dict) and bool(str(budget.get("exhausted") or "").strip())


def _worker_claim(goal: Goal) -> str:
    outcome = goal.metadata.get("worker_outcome")
    if isinstance(outcome, dict):
        summary = outcome.get("summary")
        if isinstance(summary, str) and summary.strip():
            return safe_inline_text(summary.strip())
    summary = goal.metadata.get("worker_summary")
    return safe_inline_text(summary.strip()) if isinstance(summary, str) else ""


def _verification_commands(goal: Goal) -> tuple[str, ...]:
    safety = goal.metadata.get("safety")
    if not isinstance(safety, dict):
        return ()
    checks = safety.get("checks")
    if not isinstance(checks, list):
        return ()
    commands: list[str] = []
    for check in checks:
        command = _verification_command(check)
        if command:
            commands.append(command)
    return tuple(commands)


def _verification_command(check: object) -> str:
    if not isinstance(check, dict):
        return ""
    argv = check.get("argv")
    if (
        isinstance(argv, list)
        and argv
        and all(isinstance(argument, str) for argument in argv)
    ):
        return safe_inline_text(format_command(argv))
    label = check.get("label")
    if isinstance(label, str) and label.strip():
        return safe_inline_text(label.strip())
    return ""


def _review_attempts(goal: Goal) -> tuple[ReviewAttemptReceipt, ...]:
    reviews: list[tuple[ReviewSource, dict[Any, Any]]] = []
    history = goal.metadata.get("review_history")
    if isinstance(history, list):
        reviews.extend(("prior", review) for review in history if isinstance(review, dict))
    if isinstance(goal.review, dict):
        reviews.append(("current", goal.review))
    return tuple(
        _review_attempt(number, source, review)
        for number, (source, review) in enumerate(reviews, 1)
    )


def _review_attempt(
    number: int,
    source: ReviewSource,
    review: dict[Any, Any],
) -> ReviewAttemptReceipt:
    checks = _review_checks(review)
    messages = [check.message for check in checks if check.message]
    passed = review.get("passed") is True
    summary = "; ".join(messages) or ("Review passed." if passed else "Review failed.")
    return ReviewAttemptReceipt(
        number=number,
        source=source,
        passed=passed,
        summary=safe_inline_text(summary),
        checks=checks,
    )


def _review_checks(review: dict[Any, Any]) -> tuple[ReviewCheckReceipt, ...]:
    criteria = review.get("criteria")
    if not isinstance(criteria, list):
        return ()
    checks: list[ReviewCheckReceipt] = []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        checks.append(
            ReviewCheckReceipt(
                name=safe_inline_text(criterion.get("name") or "Verification"),
                passed=criterion.get("passed") is True,
                message=safe_inline_text(criterion.get("message") or ""),
                independent=criterion.get("independent") is True,
            )
        )
    return tuple(checks)


def _attempt_count(goal: Goal, review_count: int) -> int:
    history = goal.metadata.get("attempt_history")
    if isinstance(history, list):
        durable_rows = [attempt for attempt in history if isinstance(attempt, dict)]
        numbers = [
            value
            for attempt in durable_rows
            if isinstance((value := attempt.get("attempt")), int)
            and not isinstance(value, bool)
            and value > 0
        ]
        durable_count = max(numbers, default=len(durable_rows))
        if durable_count:
            return durable_count
    autonomy = goal.metadata.get("autonomy")
    cycle = autonomy.get("cycle") if isinstance(autonomy, dict) else 0
    cycle_count = cycle if isinstance(cycle, int) and not isinstance(cycle, bool) else 0
    worker_seen = int(
        "worker_success" in goal.metadata or "worker_outcome" in goal.metadata
    )
    return max(0, cycle_count, review_count, worker_seen)


def _trusted_reason(
    goal: Goal,
    category: ReceiptCategory,
    reviews: tuple[ReviewAttemptReceipt, ...],
) -> str:
    if category == "verified_done":
        return "Independent verification passed."
    if goal.status is GoalStatus.DONE:
        return "Done state lacks passed independent verification."
    if category == "blocked":
        reason = _blocker_reason(goal)
        return reason or "The harness stopped for operator review."
    if category == "failed":
        if reviews and reviews[-1].passed is False:
            return "Independent verification failed."
        if goal.metadata.get("worker_success") is False:
            return "Worker execution failed."
        if goal.error:
            return safe_inline_text(goal.error)
        return "The harness recorded a failed result."
    return "Completion has not been verified."


def _blocker_reason(goal: Goal) -> str:
    autonomy = goal.metadata.get("autonomy")
    if not isinstance(autonomy, dict):
        return ""
    blocker = autonomy.get("blocker")
    if isinstance(blocker, dict):
        reason = blocker.get("reason")
        if isinstance(reason, str) and reason.strip():
            return safe_inline_text(reason.strip())
    budget = autonomy.get("budget")
    if isinstance(budget, dict):
        exhausted = budget.get("exhausted")
        if isinstance(exhausted, str) and exhausted.strip():
            return f"Resource budget exhausted: {safe_inline_text(exhausted.strip())}."
    return ""
