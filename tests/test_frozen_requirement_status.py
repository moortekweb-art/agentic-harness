from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.autonomy import AutonomousRunner
from agentic_harness.core.goal_spec import GoalRequirement, GoalSpec
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


OBJECTIVE = "Add input validation, update documentation, and add regression tests."


def frozen_spec() -> GoalSpec:
    return GoalSpec(
        objective=OBJECTIVE,
        requirements=(
            GoalRequirement(id="R1", text="Invalid input is rejected with a useful error."),
            GoalRequirement(id="R2", text="Documentation explains accepted input."),
            GoalRequirement(id="R3", text="Regression tests cover valid and invalid input."),
        ),
        derivation="harness_derived",
        approval="automatic",
        created_at="2026-07-17T00:00:00Z",
    )


def complete_outcome() -> dict[str, Any]:
    return {
        "status": "complete",
        "summary": "Implemented and checked.",
        "checkpoint": "final_check",
        "current_subgoal": "completion audit",
        "plan": [{"step": "implement", "status": "completed"}],
        "requirement_status": [
            {"id": "R1", "status": "satisfied", "evidence": ["review:1"]},
            {"id": "R2", "status": "satisfied", "evidence": ["review:1"]},
            {"id": "R3", "status": "satisfied", "evidence": ["review:1"]},
        ],
        "blockers": [],
    }


class OutcomeWorker:
    def __init__(self, outcome: dict[str, Any]) -> None:
        self.outcome = outcome

    def run(self, goal: Goal) -> WorkerResult:
        return WorkerResult(success=True, summary="reported", outcome=deepcopy(self.outcome))


def passing_reviewer() -> DeterministicReviewer:
    return DeterministicReviewer(
        [
            ReviewCriterion(
                name="check",
                check=lambda goal: (True, "passed"),
                description="Independent check",
            )
        ]
    )


def run_one_audit(tmp_path: Path, outcome: dict[str, Any]) -> Goal:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=OutcomeWorker(outcome),
        reviewer=passing_reviewer(),
    )
    goal = supervisor.start(OBJECTIVE)
    supervisor.store.write_goal_spec(goal, frozen_spec())
    return AutonomousRunner(supervisor).step()


def audit_failures(goal: Goal) -> list[str]:
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is False
    return audit["failures"]


def test_completion_rejects_omitted_frozen_requirement(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["requirement_status"] = outcome["requirement_status"][:2]

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert "frozen requirement R3 is missing" in failures


def test_completion_rejects_changed_requirement_text(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["requirement_status"][0]["text"] = "A narrower replacement."

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert "requirement R1 attempts to replace frozen text" in failures


def test_completion_rejects_unknown_requirement_id(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["requirement_status"][2]["id"] = "R4"

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert "unknown frozen requirement id: R4" in failures
    assert "frozen requirement R3 is missing" in failures


def test_completion_rejects_duplicate_requirement_id(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["requirement_status"][2]["id"] = "R2"

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert "requirement R2 is duplicated" in failures
    assert "frozen requirement R3 is missing" in failures


def test_completion_rejects_replacement_requirement_list(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["requirements"] = [{"id": "R1", "text": "Replacement"}]

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert "worker returned a mutable requirements list; use requirement_status" in failures


def test_completion_accepts_exact_frozen_requirement_status(tmp_path: Path) -> None:
    goal = run_one_audit(tmp_path, complete_outcome())

    assert goal.status is GoalStatus.DONE
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["passed"] is True
    assert audit["goal_spec_sha256"] == frozen_spec().sha256


@pytest.mark.parametrize("field", ["requirement_status", "blockers"])
def test_required_completion_collections_remain_required(tmp_path: Path, field: str) -> None:
    outcome = complete_outcome()
    outcome.pop(field)

    failures = audit_failures(run_one_audit(tmp_path, outcome))

    assert failures
