from __future__ import annotations

from pathlib import Path

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy
from agentic_harness.core.errors import GoalConflictError, StateLockError
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult
from agentic_harness.core.workspace import workspace_change_summary


def passing_reviewer() -> DeterministicReviewer:
    return DeterministicReviewer(
        [
            ReviewCriterion(
                name="deterministic_check",
                check=lambda goal: (True, "focused check passed"),
                description="Focused check must pass",
            )
        ]
    )


def complete_outcome() -> dict[str, object]:
    return {
        "status": "complete",
        "summary": "objective implemented",
        "checkpoint": "verified",
        "current_subgoal": "final audit",
        "plan": [
            {"step": "implement", "status": "completed"},
            {"step": "verify", "status": "completed"},
        ],
        "requirements": [
            {
                "id": "requested-outcome",
                "status": "satisfied",
                "evidence": ["focused check passed"],
            }
        ],
        "blockers": [],
    }


def test_completion_status_is_case_insensitive(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["status"] = "Complete"
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=outcome)]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("accept a valid structured result")

    assert goal.status is GoalStatus.DONE
    assert worker.calls == 1


def test_coding_agent_instruction_includes_requested_scope_and_checks(
    tmp_path: Path,
) -> None:
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )
    supervisor.start(
        "make the bounded change",
        metadata={
            "safety": {
                "allowed_paths": ["src", "tests/test_feature.py"],
                "checks": [
                    {
                        "id": "check-1",
                        "label": "pytest -q tests/test_feature.py",
                        "argv": ["pytest", "-q", "tests/test_feature.py"],
                    }
                ],
                "path_enforcement": False,
                "secret_env_names": [],
                "preexisting_changes": [],
            }
        },
    )

    goal = AutonomousRunner(supervisor).run()

    assert goal.status is GoalStatus.DONE
    assert "Allowed workspace paths (operator guidance): src, tests/test_feature.py" in worker.instructions[0]
    assert "Independent check: pytest -q tests/test_feature.py" in worker.instructions[0]
    assert "Do not edit outside the allowed workspace paths" in worker.instructions[0]


def test_only_one_autonomous_driver_can_own_a_project_goal(tmp_path: Path) -> None:
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )
    supervisor.init()

    with supervisor.store.autonomy_locked():
        with pytest.raises(StateLockError, match="autonomous driver"):
            AutonomousRunner(supervisor).step("do not run twice")

    assert worker.calls == 0


def test_autonomy_lease_blocks_direct_supervisor_mutation(tmp_path: Path) -> None:
    worker = SequenceWorker([WorkerResult(success=True, summary="ran")])
    owner = Supervisor(project_dir=tmp_path, worker=worker)
    outsider = Supervisor(project_dir=tmp_path, worker=worker)
    owner.start("keep one driver in control")

    with owner.store.autonomy_locked():
        with pytest.raises(StateLockError, match="autonomous driver"):
            outsider.continue_goal()

    assert worker.calls == 0


class SequenceWorker:
    def __init__(self, results: list[WorkerResult]) -> None:
        self.results = list(results)
        self.calls = 0
        self.instructions: list[str] = []

    def run(self, goal: Goal) -> WorkerResult:
        self.instructions.append(str(goal.metadata.get("continuation_instruction") or ""))
        result = self.results[self.calls]
        self.calls += 1
        return result


class WorkspaceProgressWorker(SequenceWorker):
    def __init__(self, project_dir: Path, results: list[WorkerResult]) -> None:
        super().__init__(results)
        self.project_dir = project_dir

    def run(self, goal: Goal) -> WorkerResult:
        (self.project_dir / "progress.txt").write_text(
            str(self.calls + 1), encoding="utf-8"
        )
        return super().run(goal)


class ProgressingFailureWorker:
    def __init__(self, project_dir: Path, failures: int) -> None:
        self.project_dir = project_dir
        self.failures = failures
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        (self.project_dir / "progress.txt").write_text(str(self.calls), encoding="utf-8")
        if self.calls <= self.failures:
            return WorkerResult(
                success=False,
                summary="focused check still failing",
                stderr="same focused check failure",
                returncode=1,
            )
        return WorkerResult(
            success=True,
            summary="repaired",
            outcome=complete_outcome(),
        )


def test_autonomous_runner_continues_partial_progress_and_accepts_proven_completion(
    tmp_path: Path,
) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="foundation implemented",
                outcome={
                    "status": "progress",
                    "summary": "foundation implemented",
                    "checkpoint": "foundation",
                    "current_subgoal": "finish verification",
                    "plan": [
                        {"step": "implement", "status": "completed"},
                        {"step": "verify", "status": "in_progress"},
                    ],
                    "requirements": [
                        {
                            "id": "requested-outcome",
                            "status": "pending",
                            "evidence": ["implementation exists"],
                        }
                    ],
                },
            ),
            WorkerResult(success=True, summary="complete", outcome=complete_outcome()),
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("implement and verify the requested change")

    assert goal.status is GoalStatus.DONE
    assert goal.objective == "implement and verify the requested change"
    assert goal.metadata["accepted"] is True
    assert goal.metadata["autonomy"]["cycle"] == 2
    assert goal.metadata["autonomy"]["completion_audit"]["passed"] is True
    assert worker.calls == 2
    assert "finish verification" in worker.instructions[1]
    assert "Persisted plan:" in worker.instructions[1]
    assert '"step": "implement"' in worker.instructions[1]
    assert "Persisted requirements:" in worker.instructions[1]
    assert '"id": "requested-outcome"' in worker.instructions[1]


def test_meaningful_progress_does_not_consume_the_no_progress_circuit_breaker(
    tmp_path: Path,
) -> None:
    progress_results = [
        WorkerResult(
            success=True,
            summary=f"checkpoint {index}",
            outcome={
                "status": "progress",
                "checkpoint": f"checkpoint-{index}",
                "current_subgoal": f"subgoal {index + 1}",
                "plan": [{"step": f"part-{index}", "status": "completed"}],
                "requirements": [],
            },
        )
        for index in range(6)
    ]
    worker = WorkspaceProgressWorker(
        tmp_path,
        [*progress_results, WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("complete all seven meaningful cycles")

    assert goal.status is GoalStatus.DONE
    assert worker.calls == 7
    assert not any(entry["to"] == "failed" for entry in goal.history)


def test_repeated_progress_claim_without_evidence_trips_the_blocker_threshold(
    tmp_path: Path,
) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="still working",
                outcome={
                    "status": "progress",
                    "summary": "still working",
                    "checkpoint": "goal_started",
                    "current_subgoal": "same step",
                    "plan": [{"step": "same step", "status": "in_progress"}],
                    "requirements": [],
                },
            )
            for _ in range(3)
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("require evidence of progress")

    autonomy = goal.metadata["autonomy"]
    assert goal.status is GoalStatus.FAILED
    assert autonomy["cycle"] == 3
    assert autonomy["operator_intervention_required"] is True
    assert "without changing the workspace" in autonomy["blocker"]["reason"]
    assert worker.calls == 3


def test_runtime_progress_token_counts_bounded_tool_observation_as_progress(
    tmp_path: Path,
) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="inspected the requested file",
                outcome={
                    "status": "progress",
                    "summary": "inspected the requested file",
                    "checkpoint": "source_inspected",
                    "current_subgoal": "apply the focused change",
                    "plan": [{"step": "Inspect source", "status": "completed"}],
                    "requirements": [],
                    "progress_token": "trusted-tool-event-sha256",
                },
            )
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).step("inspect before editing")

    assert goal.status is GoalStatus.IN_PROGRESS
    assert goal.metadata["autonomy"]["blocker"]["consecutive_count"] == 0


def test_cycle_budget_blocks_resumably_instead_of_running_forever(tmp_path: Path) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="made bounded progress",
                outcome={
                    "status": "progress",
                    "summary": "made bounded progress",
                    "checkpoint": "first_cycle",
                    "current_subgoal": "continue",
                    "plan": [{"step": "Continue", "status": "in_progress"}],
                    "requirements": [],
                    "progress_token": "cycle-1",
                },
            )
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(
        supervisor,
        policy=AutonomyPolicy(max_cycles=1),
    ).run("respect the configured cycle budget")

    assert goal.status is GoalStatus.FAILED
    assert goal.metadata["autonomy"]["operator_intervention_required"] is True
    assert goal.metadata["autonomy"]["budget"]["exhausted"] == "max_cycles"
    assert "cycle budget" in str(goal.error)
    assert worker.calls == 1


def test_token_budget_exhaustion_never_counts_as_completion(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["usage"] = {"total_tokens": 11, "provider_calls": 1}
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="claims complete", outcome=outcome)]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(
        supervisor,
        policy=AutonomyPolicy(max_total_tokens=10),
    ).run("stay within the cloud token budget")

    assert goal.status is GoalStatus.FAILED
    assert goal.metadata["accepted"] is False
    assert goal.metadata["autonomy"]["budget"]["exhausted"] == "max_total_tokens"


def test_completion_at_exact_token_budget_can_still_pass_review(tmp_path: Path) -> None:
    outcome = complete_outcome()
    outcome["usage"] = {"total_tokens": 10, "provider_calls": 1}
    worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=outcome)]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(
        supervisor,
        policy=AutonomyPolicy(max_total_tokens=10),
    ).run("finish at the exact token budget")

    assert goal.status is GoalStatus.DONE
    assert goal.metadata["accepted"] is True


def test_cooperative_cancellation_prevents_late_completion_from_being_accepted(
    tmp_path: Path,
) -> None:
    cancel = {"requested": False}

    class CancellingWorker:
        def run(self, goal: Goal) -> WorkerResult:
            cancel["requested"] = True
            return WorkerResult(
                success=True,
                summary="finished after stop was requested",
                outcome=complete_outcome(),
            )

    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=CancellingWorker(),
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(
        supervisor,
        cancel_requested=lambda: cancel["requested"],
    ).step("stop safely at the next boundary")

    assert goal.status is GoalStatus.FAILED
    assert goal.metadata["cancelled"] is True
    assert goal.metadata["accepted"] is False
    assert goal.metadata["autonomy"]["status"] == "stopped"


def test_changing_checkpoint_does_not_hide_a_repeated_blocker(tmp_path: Path) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="provider unavailable",
                outcome={
                    "status": "blocked",
                    "summary": "provider unavailable",
                    "checkpoint": f"worker-label-{index}",
                    "blockers": ["provider unavailable"],
                },
            )
            for index in range(3)
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )
    runner = AutonomousRunner(supervisor)

    runner.step("require objective progress")
    runner.step()
    goal = runner.step()

    autonomy = goal.metadata["autonomy"]
    assert goal.status is GoalStatus.FAILED
    assert autonomy["blocker"]["consecutive_count"] == 3
    assert autonomy["operator_intervention_required"] is True


def test_autonomous_runner_escalates_only_after_three_identical_no_progress_blockers(
    tmp_path: Path,
) -> None:
    worker = SequenceWorker(
        [
            WorkerResult(
                success=False,
                summary="provider unavailable",
                stderr="provider unavailable",
                returncode=1,
            )
            for _ in range(3)
        ]
    )
    supervisor = Supervisor(project_dir=tmp_path, worker=worker, reviewer=passing_reviewer())

    goal = AutonomousRunner(
        supervisor,
        policy=AutonomyPolicy(repeated_blocker_limit=3),
    ).run("complete the full objective")

    autonomy = goal.metadata["autonomy"]
    assert goal.status is GoalStatus.FAILED
    assert autonomy["operator_intervention_required"] is True
    assert autonomy["blocker"]["consecutive_count"] == 3
    assert worker.calls == 3


def test_autonomous_runner_does_not_confuse_failed_attempt_count_with_no_progress(
    tmp_path: Path,
) -> None:
    worker = ProgressingFailureWorker(tmp_path, failures=4)
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(supervisor).run("repair the implementation completely")

    assert goal.status is GoalStatus.DONE
    assert goal.metadata["accepted"] is True
    assert worker.calls == 5


def test_autonomous_runner_resumes_same_goal_after_process_restart(tmp_path: Path) -> None:
    first_worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="checkpoint saved",
                outcome={
                    "status": "progress",
                    "checkpoint": "halfway",
                    "current_subgoal": "finish the second half",
                    "plan": [{"step": "finish", "status": "in_progress"}],
                    "requirements": [],
                },
            )
        ]
    )
    first = Supervisor(
        project_dir=tmp_path,
        worker=first_worker,
        reviewer=passing_reviewer(),
    )
    first_goal = AutonomousRunner(first).step("finish a resumable task")

    second_worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
    )
    second = Supervisor(
        project_dir=tmp_path,
        worker=second_worker,
        reviewer=passing_reviewer(),
    )
    resumed = AutonomousRunner(second).step()

    assert resumed.id == first_goal.id
    assert resumed.status is GoalStatus.DONE
    assert resumed.metadata["autonomy"]["cycle"] == 2
    assert "finish the second half" in second_worker.instructions[0]


def test_autonomous_runner_rejects_a_changed_persisted_objective(tmp_path: Path) -> None:
    first_worker = SequenceWorker(
        [
            WorkerResult(
                success=True,
                summary="checkpoint saved",
                outcome={
                    "status": "progress",
                    "checkpoint": "halfway",
                    "current_subgoal": "finish",
                    "plan": [{"step": "finish", "status": "in_progress"}],
                    "requirements": [],
                },
            )
        ]
    )
    first = Supervisor(project_dir=tmp_path, worker=first_worker)
    AutonomousRunner(first).step("preserve this complete objective")
    with first.store.locked():
        changed = first.store.read_current_goal()
        assert changed is not None
        changed.objective = "narrowed objective"
        first.store.write_goal(changed)

    resumed = Supervisor(
        project_dir=tmp_path,
        worker=SequenceWorker(
            [WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
        ),
        reviewer=passing_reviewer(),
    )

    with pytest.raises(GoalConflictError, match="original objective"):
        AutonomousRunner(resumed).step()


def test_autonomous_runner_migrates_and_resumes_a_legacy_failed_goal(
    tmp_path: Path,
) -> None:
    failing_worker = SequenceWorker(
        [
            WorkerResult(
                success=False,
                summary="legacy failure",
                stderr="legacy failure",
                returncode=1,
            )
        ]
    )
    legacy = Supervisor(project_dir=tmp_path, worker=failing_worker)
    legacy_goal = legacy.start("resume the original failed goal")
    legacy.continue_goal()

    repair_worker = SequenceWorker(
        [WorkerResult(success=True, summary="complete", outcome=complete_outcome())]
    )
    resumed = Supervisor(
        project_dir=tmp_path,
        worker=repair_worker,
        reviewer=passing_reviewer(),
    )

    goal = AutonomousRunner(resumed).run()

    assert goal.id == legacy_goal.id
    assert goal.status is GoalStatus.DONE
    assert goal.metadata["autonomy"]["objective"] == legacy_goal.objective


def test_failed_review_evidence_survives_automatic_repair(tmp_path: Path) -> None:
    review_calls = 0

    def check_review(goal: Goal) -> tuple[bool, str]:
        nonlocal review_calls
        review_calls += 1
        if review_calls == 1:
            return False, "focused review found a regression"
        return True, "focused review passed after repair"

    reviewer = DeterministicReviewer(
        [ReviewCriterion(name="focused_review", check=check_review)]
    )
    worker = SequenceWorker(
        [
            WorkerResult(success=True, summary="first claim", outcome=complete_outcome()),
            WorkerResult(success=True, summary="repaired", outcome=complete_outcome()),
        ]
    )
    supervisor = Supervisor(project_dir=tmp_path, worker=worker, reviewer=reviewer)

    goal = AutonomousRunner(supervisor).run("repair failed review findings")

    assert goal.status is GoalStatus.DONE
    assert review_calls == 2
    history = goal.metadata["review_history"]
    assert history[0]["passed"] is False
    assert history[0]["criteria"][0]["message"] == "focused review found a regression"


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
                        "requirements": [],
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


def test_strict_completion_requires_an_independent_review_criterion(tmp_path: Path) -> None:
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


def test_restart_preserves_original_workspace_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before\n", encoding="utf-8")
    worker = SequenceWorker(
        [
            WorkerResult(
                success=False,
                summary="failed after edit",
                stderr="failed after edit",
                returncode=1,
            )
        ]
    )
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    supervisor.start("preserve initial evidence")
    original = supervisor.status().metadata["workspace_snapshot"]
    target.write_text("after\n", encoding="utf-8")
    supervisor.continue_goal()

    restarted = supervisor.restart()

    assert restarted.metadata["workspace_snapshot"] == original
    summary = workspace_change_summary(tmp_path, original)
    assert summary is not None
    assert summary["entries"] == [{"status": "modified", "path": "target.txt"}]


def test_accept_on_reviewed_done_goal_records_acceptance_metadata(tmp_path: Path) -> None:
    worker = SequenceWorker([WorkerResult(success=True, summary="done")])
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    supervisor.start("record acceptance")
    supervisor.continue_goal()
    supervisor.review()

    accepted = supervisor.accept(reason="completion audit passed")

    assert accepted.metadata["accepted"] is True
    assert accepted.metadata["accept_reason"] == "completion audit passed"
    assert accepted.metadata["accepted_at"]
