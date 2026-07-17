"""Focused adversarial tests for the structured completion-claim boundary."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


def passing_reviewer() -> DeterministicReviewer:
    return DeterministicReviewer(
        [
            ReviewCriterion(
                name="deterministic_check",
                check=lambda goal: (True, "focused check passed"),
                covers=("R1",),
            )
        ]
    )


def complete_outcome() -> dict[str, object]:
    return {
        "status": "complete",
        "summary": "objective implemented",
        "checkpoint": "verified",
        "current_subgoal": "final audit",
        "plan": [{"step": "verify", "status": "completed"}],
        "requirement_status": [
            {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
        ],
        "blockers": [],
    }


class SequenceWorker:
    def __init__(self, results: list[WorkerResult]) -> None:
        self.results = results
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return deepcopy(result)


def test_strict_autonomy_refuses_unproven_completion(tmp_path: Path) -> None:
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="done") for _ in range(3)]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("do not claim success without evidence")

    assert goal.status is GoalStatus.FAILED
    assert goal.metadata["accepted"] is not True
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is False
    assert "structured completion claim" in " ".join(audit["failures"])


def test_strict_autonomy_refuses_a_malformed_completion_schema(tmp_path: Path) -> None:
    malformed = complete_outcome()
    malformed.pop("blockers")
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="done", outcome=malformed) for _ in range(3)]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("require a complete result schema")

    assert goal.status is GoalStatus.FAILED
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is False
    assert "blockers list is missing" in audit["failures"]


def test_strict_completion_cannot_be_downgraded_when_resumed(tmp_path: Path) -> None:
    first = Supervisor(
        project_dir=tmp_path,
        worker=SequenceWorker(
            [
                WorkerResult(
                    success=True,
                    summary="partial",
                    outcome={
                        "status": "progress",
                        "checkpoint": "partial",
                        "current_subgoal": "finish",
                        "plan": [{"step": "finish", "status": "in_progress"}],
                        "requirement_status": [],
                    },
                )
            ]
        ),
        reviewer=passing_reviewer(),
    )
    strict_goal = AutonomousRunner(first).step("preserve strict completion")

    resumed = AutonomousRunner(
        Supervisor(
            project_dir=tmp_path,
            worker=SequenceWorker([WorkerResult(success=True, summary="unstructured")]),
            reviewer=passing_reviewer(),
        ),
        policy=AutonomyPolicy(require_completion_claim=False),
    ).step()

    assert strict_goal.metadata["autonomy"]["strict_completion"] is True
    assert resumed.status is not GoalStatus.DONE
    assert resumed.metadata["accepted"] is False
    audit = resumed.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is False
    assert "structured completion claim is missing" in audit["failures"]


def test_strict_completion_requires_an_independent_review_criterion(
    tmp_path: Path,
) -> None:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=SequenceWorker(
            [WorkerResult(success=True, summary="claimed", outcome=complete_outcome())]
        ),
    )

    goal = AutonomousRunner(supervisor).step("verify independently")

    assert goal.status is not GoalStatus.DONE
    assert goal.metadata["accepted"] is False
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is False
    assert "deterministic review has no independent criterion" in audit["failures"]
