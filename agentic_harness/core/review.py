"""Deterministic review contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agentic_harness.core.state import Goal

CriterionCheck = Callable[[Goal], tuple[bool, str]]


@dataclass(frozen=True)
class ReviewCriterion:
    name: str
    check: CriterionCheck
    description: str = ""


@dataclass
class ReviewResult:
    passed: bool
    criteria: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "criteria": list(self.criteria)}


class DeterministicReviewer:
    """Run typed pass/fail criteria without model judgment."""

    def __init__(self, criteria: list[ReviewCriterion] | None = None) -> None:
        self.criteria = criteria or [ReviewCriterion("worker_success", self._worker_success)]

    def review(self, goal: Goal) -> ReviewResult:
        results: list[dict[str, object]] = []
        for criterion in self.criteria:
            passed, message = criterion.check(goal)
            results.append(
                {
                    "name": criterion.name,
                    "description": criterion.description,
                    "passed": bool(passed),
                    "message": message,
                }
            )
        return ReviewResult(
            passed=all(bool(item["passed"]) for item in results),
            criteria=results,
        )

    def _worker_success(self, goal: Goal) -> tuple[bool, str]:
        success = bool(goal.metadata.get("worker_success"))
        return success, "worker reported success" if success else "worker did not report success"

