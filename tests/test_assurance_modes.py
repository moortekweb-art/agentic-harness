from __future__ import annotations

from pathlib import Path

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.assurance import AssuranceMode
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy
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
    assert worker.calls == 0


def test_worker_amendment_request_blocks_without_changing_frozen_spec(
    tmp_path: Path,
) -> None:
    class AmendmentWorker:
        def run(self, goal: Goal) -> WorkerResult:
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

    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=AmendmentWorker(),
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
