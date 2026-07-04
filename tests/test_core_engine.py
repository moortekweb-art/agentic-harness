from __future__ import annotations

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.errors import InvalidTransitionError, LoopGuardTripped
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


class RecordingWorker:
    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return self.result


def test_goal_state_machine_allows_expected_path() -> None:
    goal = Goal("ship something")

    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.REVIEW)
    goal.transition(GoalStatus.DONE)

    assert goal.status is GoalStatus.DONE
    assert [item["to"] for item in goal.history] == [
        "planning",
        "in_progress",
        "review",
        "done",
    ]


def test_goal_state_machine_rejects_invalid_transition() -> None:
    goal = Goal("bad jump")

    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.DONE)


def test_supervisor_writes_project_local_state_and_reviews(tmp_path) -> None:
    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    started = supervisor.start("build feature")
    progressed = supervisor.continue_goal()
    reviewed = supervisor.review()

    assert started.status is GoalStatus.PLANNING
    assert progressed.status is GoalStatus.REVIEW
    assert reviewed.status is GoalStatus.DONE
    assert worker.calls == 1
    assert (tmp_path / ".agentic-harness" / "current.json").exists()
    assert (tmp_path / ".agentic-harness" / "runs" / started.id / "state.json").exists()


def test_deterministic_reviewer_reports_typed_criteria() -> None:
    goal = Goal("review me")
    goal.metadata["worker_success"] = True
    reviewer = DeterministicReviewer(
        [
            ReviewCriterion(
                "has_objective",
                lambda g: (bool(g.objective), "objective is present"),
                "Goal must have text.",
            )
        ]
    )

    result = reviewer.review(goal)

    assert result.passed is True
    assert result.criteria == [
        {
            "name": "has_objective",
            "description": "Goal must have text.",
            "passed": True,
            "message": "objective is present",
        }
    ]


def test_loop_guard_trips_after_configured_continue_count() -> None:
    guard = LoopGuard(max_continues=1, window_seconds=60)

    guard.record_continue()
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_loop_guard_persists_events_across_instances(tmp_path) -> None:
    path = tmp_path / "guard.json"

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 100.0,
    ).record_continue()

    with pytest.raises(LoopGuardTripped):
        LoopGuard(
            max_continues=1,
            window_seconds=60,
            state_path=path,
            clock=lambda: 101.0,
        ).record_continue()


def test_loop_guard_prunes_expired_persisted_events(tmp_path) -> None:
    path = tmp_path / "guard.json"

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 100.0,
    ).record_continue()

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 161.0,
    ).record_continue()


def test_supervisor_uses_project_local_loop_guard_state(tmp_path) -> None:
    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("persist guard")
    supervisor.continue_goal()

    assert (tmp_path / ".agentic-harness" / "guard.json").exists()


def test_repair_restores_missing_current_marker(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    goal = supervisor.start("repair marker")
    (tmp_path / ".agentic-harness" / "current.json").unlink()

    repaired = supervisor.repair()

    assert repaired is not None
    assert repaired.id == goal.id
    assert supervisor.status().id == goal.id


def test_artifact_store_writes_markdown_report_under_project_state(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    goal = Goal("write report")

    report_path = store.write_report(goal, "# Report\n")

    assert report_path == tmp_path / ".agentic-harness" / "runs" / goal.id / "report.md"
    assert report_path.read_text(encoding="utf-8") == "# Report\n"
    assert goal.artifacts == [f".agentic-harness/runs/{goal.id}/report.md"]
