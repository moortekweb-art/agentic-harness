from __future__ import annotations

from pathlib import Path

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.assurance import AssuranceMode
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy
from agentic_harness.core.errors import GoalConflictError
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


def complete_outcome() -> dict[str, object]:
    return {
        "status": "complete",
        "summary": "Implemented and checked.",
        "checkpoint": "checked",
        "current_subgoal": "completion audit",
        "plan": [{"step": "implement", "status": "completed"}],
        "requirement_status": [
            {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
        ],
        "blockers": [],
    }


class StaticWorker:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return WorkerResult(success=True, summary="complete", outcome=complete_outcome())


def reviewer(*, covers: tuple[str, ...]) -> DeterministicReviewer:
    return DeterministicReviewer(
        [
            ReviewCriterion(
                name="check",
                check=lambda goal: (True, "passed"),
                covers=covers,
            )
        ]
    )


def policy(mode: AssuranceMode) -> AutonomyPolicy:
    return AutonomyPolicy(assurance_mode=mode)


def test_check_gated_accepts_passing_check_without_requirement_coverage(
    tmp_path: Path,
) -> None:
    worker = StaticWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer(covers=()),
    )

    goal = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.CHECK_GATED),
    ).run("Make the requested change.")

    assert goal.status is GoalStatus.DONE
    audit = goal.metadata["autonomy"]["completion_audit"]
    assert audit["assurance_mode"] == "check_gated"
    assert audit["evidence_registry"][0]["covers"] == []


def test_specification_frozen_rejects_same_uncovered_check(tmp_path: Path) -> None:
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=StaticWorker(),
        reviewer=reviewer(covers=()),
    )

    goal = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.SPECIFICATION_FROZEN),
    ).step("Make the requested change.")

    assert goal.status is not GoalStatus.DONE
    failures = goal.metadata["autonomy"]["completion_audit"]["failures"]
    assert "requirement R1 cites ineligible evidence: review:1" in failures


def test_high_assurance_pauses_before_worker_until_operator_approval(
    tmp_path: Path,
) -> None:
    worker = StaticWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer(covers=("R1",)),
    )
    runner = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.HIGH_ASSURANCE),
    )

    pending = runner.step("Make the requested change.")

    assert worker.calls == 0
    assert pending.metadata["autonomy"]["status"] == "awaiting_specification_approval"
    assert pending.metadata["autonomy"]["operator_intervention_required"] is True
    proposal = supervisor.store.read_goal_spec_proposal(pending.id)
    assert proposal.approval == "automatic"

    runner.approve_specification()
    completed = runner.run()

    assert worker.calls == 1
    assert completed.status is GoalStatus.DONE
    approved = supervisor.store.read_goal_spec(completed.id)
    assert approved.approval == "operator_approved"
    assert approved.sha256 != proposal.sha256


def test_high_assurance_operator_can_edit_conditions_before_approval(
    tmp_path: Path,
) -> None:
    worker = StaticWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer(covers=("R1", "R2")),
    )
    runner = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.HIGH_ASSURANCE),
    )
    pending = runner.step("Make the requested change.")

    runner.approve_specification(
        ["The requested behavior works.", "Regression tests cover the behavior."]
    )

    approved = supervisor.store.read_goal_spec(pending.id)
    assert [item.id for item in approved.requirements] == ["R1", "R2"]
    assert [item.text for item in approved.requirements] == [
        "The requested behavior works.",
        "Regression tests cover the behavior.",
    ]
    assert approved.derivation == "operator_authored"
    assert worker.calls == 0
    assert runner.approve_specification().id == pending.id


def test_worker_amendment_request_blocks_without_changing_frozen_spec(
    tmp_path: Path,
) -> None:
    class AmendmentWorker:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, goal: Goal) -> WorkerResult:
            self.calls += 1
            if self.calls > 1:
                return WorkerResult(
                    success=True,
                    summary="replacement complete",
                    outcome=complete_outcome(),
                )
            return WorkerResult(
                success=True,
                summary="API changed",
                outcome={
                    "status": "specification_change_required",
                    "reason": "The requested API is unavailable.",
                    "proposed_changes": [
                        {
                            "operation": "replace",
                            "requirement_id": "R1",
                            "new_text": "Use the replacement API.",
                        }
                    ],
                },
            )

    worker = AmendmentWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer(covers=("R1",)),
    )
    runner = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.SPECIFICATION_FROZEN),
    )

    goal = runner.step("Use the requested API.")
    original = supervisor.store.read_goal_spec(goal.id)

    assert goal.status is GoalStatus.FAILED
    assert goal.metadata["autonomy"]["operator_intervention_required"] is True
    assert goal.metadata["autonomy"]["goal_spec_sha256"] == original.sha256
    assert goal.metadata["autonomy"]["specification_amendment"]["proposed_changes"]


def test_high_assurance_operator_approves_versioned_mid_run_amendment(
    tmp_path: Path,
) -> None:
    class AmendmentWorker:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, goal: Goal) -> WorkerResult:
            self.calls += 1
            if self.calls > 1:
                return WorkerResult(
                    success=True,
                    summary="replacement complete",
                    outcome=complete_outcome(),
                )
            return WorkerResult(
                success=True,
                summary="API changed",
                outcome={
                    "status": "specification_change_required",
                    "reason": "The requested API is unavailable.",
                    "proposed_changes": [
                        {
                            "operation": "replace",
                            "requirement_id": "R1",
                            "new_text": "Use the supported replacement API.",
                        }
                    ],
                },
            )

    worker = AmendmentWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer(covers=("R1",)),
    )
    runner = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.HIGH_ASSURANCE),
    )
    pending_initial = runner.step("Use the requested API.")
    runner.approve_specification()
    pending_amendment = runner.step()

    assert pending_amendment.status is GoalStatus.REVIEW
    assert (
        pending_amendment.metadata["autonomy"]["status"]
        == "awaiting_specification_amendment"
    )
    original = supervisor.store.read_goal_spec(pending_initial.id)
    pending_amendment.review = {
        "passed": True,
        "criteria": [
            {
                "name": "old-check",
                "passed": True,
                "independent": True,
                "covers": ["R1"],
                "goal_spec_sha256": original.sha256,
            }
        ],
    }
    supervisor.store.write_goal(pending_amendment)

    resumed = runner.approve_specification()
    revised = supervisor.store.read_goal_spec(resumed.id)

    assert resumed.status is GoalStatus.IN_PROGRESS
    assert revised.sha256 != original.sha256
    assert revised.approval == "operator_approved"
    assert [item.text for item in revised.requirements] == [
        "Use the supported replacement API."
    ]
    assert supervisor.store.read_goal_spec_version(resumed.id) == 2
    amendment = resumed.metadata["autonomy"]["specification_amendment"]
    assert amendment["status"] == "approved"
    assert amendment["previous_goal_spec_sha256"] == original.sha256
    assert resumed.metadata["autonomy"]["invalidated_specifications"][0][
        "goal_spec_sha256"
    ] == original.sha256
    invalidated = resumed.metadata["autonomy"]["invalidated_evidence"]
    assert invalidated == [
        {
            "schema": "agentic_harness.evidence.v2",
            "id": "review:1",
            "goal_id": resumed.id,
            "run_id": str(resumed.metadata["worker_run_id"]),
            "goal_spec_sha256": original.sha256,
            "issuer": "harness.review",
            "kind": "deterministic_check",
            "result": "invalidated",
            "covers": ["R1"],
        }
    ]

    completed = runner.run()
    audit = completed.metadata["autonomy"]["completion_audit"]
    assert completed.status is GoalStatus.DONE
    assert audit["goal_spec_sha256"] == revised.sha256
    assert audit["evidence_registry"][0]["result"] == "verified"
    assert audit["evidence_registry"][0]["goal_spec_sha256"] == revised.sha256


def test_high_assurance_rejects_invalid_amendment_operation(tmp_path: Path) -> None:
    class AmendmentWorker:
        def run(self, goal: Goal) -> WorkerResult:
            return WorkerResult(
                success=True,
                summary="bad proposal",
                outcome={
                    "status": "specification_change_required",
                    "proposed_changes": [
                        {"operation": "delete_everything", "requirement_id": "R1"}
                    ],
                },
            )

    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=AmendmentWorker(),
        reviewer=reviewer(covers=("R1",)),
    )
    runner = AutonomousRunner(
        supervisor,
        policy=policy(AssuranceMode.HIGH_ASSURANCE),
    )
    runner.step("Keep the objective intact.")
    runner.approve_specification()
    runner.step()

    with pytest.raises(GoalConflictError, match="add, replace, or remove"):
        runner.approve_specification()
