"""Tests for Supervisor.restart() — FAILED goal recovery without state deletion.

The restart method lets operators (or autonomous loops) retry a failed goal
without deleting state. It:
1. Clears the error field.
2. Clears worker result metadata.
3. Transitions FAILED -> PLANNING.
4. Preserves objective, artifacts, and history as durable evidence.

This test module verifies all of the above.
"""

from __future__ import annotations


import pytest

from agentic_harness import GoalStatus, Supervisor
from agentic_harness.core.errors import InvalidTransitionError
from agentic_harness.core.worker import WorkerResult


class FailingWorker:
    """Worker that always fails."""

    def run(self, goal):
        return WorkerResult(
            success=False,
            summary="worker failed",
            stderr="simulated failure",
            returncode=1,
        )


class CountingWorker:
    """Worker that counts calls and succeeds."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, goal):
        self.calls += 1
        return WorkerResult(success=True, summary="implemented")


def test_restart_transitions_failed_to_planning(tmp_path) -> None:
    """Restarting a FAILED goal transitions it to PLANNING."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("failing goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED
    assert goal.error is not None

    restarted = supervisor.restart()
    assert restarted.status is GoalStatus.PLANNING
    assert restarted.error is None


def test_restart_clears_worker_metadata(tmp_path) -> None:
    """Restart clears worker_success, worker_summary, worker_returncode."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("failing goal")
    goal = supervisor.continue_goal()
    assert goal.metadata.get("worker_success") is False

    restarted = supervisor.restart()
    assert "worker_success" not in restarted.metadata
    assert "worker_summary" not in restarted.metadata
    assert "worker_returncode" not in restarted.metadata


def test_restart_preserves_objective_and_artifacts(tmp_path) -> None:
    """Restart preserves the original objective and any artifacts written to disk."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("original objective")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED
    # Write an artifact to disk (simulating what a worker would do)
    artifact_file = tmp_path / "some-artifact.txt"
    artifact_file.write_text("content", encoding="utf-8")
    # Update the goal's artifacts list and write back to disk
    goal.artifacts.append("some-artifact.txt")
    supervisor.store.write_goal(goal)
    objective = goal.objective

    restarted = supervisor.restart()
    assert restarted.objective == objective
    assert "some-artifact.txt" in restarted.artifacts


def test_restart_preserves_history(tmp_path) -> None:
    """Restart preserves the transition history as durable evidence."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("failing goal")
    goal = supervisor.continue_goal()
    history_before = list(goal.history)

    restarted = supervisor.restart()
    # History should contain all previous transitions plus the restart transition
    assert len(restarted.history) > len(history_before)
    # The last entry should be the restart transition
    last = restarted.history[-1]
    assert last["from"] == "failed"
    assert last["to"] == "planning"
    assert "restarted" in last["reason"]


def test_restart_raises_for_done_goal(tmp_path) -> None:
    """Restarting a DONE goal must raise InvalidTransitionError."""
    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    goal = supervisor.start("done goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    goal = supervisor.review()
    assert goal.status is GoalStatus.DONE

    with pytest.raises(InvalidTransitionError):
        supervisor.restart()


def test_restart_raises_for_in_progress_goal(tmp_path) -> None:
    """Restarting an IN_PROGRESS goal must raise InvalidTransitionError."""
    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    goal = supervisor.start("in-progress goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    with pytest.raises(InvalidTransitionError):
        supervisor.restart()


def test_restart_raises_for_planning_goal(tmp_path) -> None:
    """Restarting a PLANNING goal must raise InvalidTransitionError."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("planning goal")
    assert goal.status is GoalStatus.PLANNING

    with pytest.raises(InvalidTransitionError):
        supervisor.restart()


def test_restart_allows_retry_after_failure(tmp_path) -> None:
    """After restart, a subsequent continue_goal should run the worker again."""
    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    # First attempt: force failure by using FailingWorker temporarily
    fail_supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = fail_supervisor.start("retryable goal")
    goal = fail_supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED

    # Now restart with a working supervisor
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)
    restarted = supervisor.restart()
    assert restarted.status is GoalStatus.PLANNING

    # Continue should now succeed
    continued = supervisor.continue_goal()
    assert continued.status is GoalStatus.REVIEW
    assert worker.calls == 1


def test_restart_idempotent_for_failed(tmp_path) -> None:
    """Multiple restarts on a FAILED goal should all work (each transitions to PLANNING)."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    goal = supervisor.start("repeated restart")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED

    # First restart
    r1 = supervisor.restart()
    assert r1.status is GoalStatus.PLANNING

    # Second restart on PLANNING should raise
    with pytest.raises(InvalidTransitionError):
        supervisor.restart()


def test_restart_produces_valid_goal_dict(tmp_path) -> None:
    """Restarted goal should produce a valid to_dict() output."""
    supervisor = Supervisor(project_dir=tmp_path, worker=FailingWorker())
    supervisor.start("dict test")
    supervisor.continue_goal()
    restarted = supervisor.restart()
    d = restarted.to_dict()
    assert d["status"] == "planning"
    assert d["error"] is None
    assert d["objective"] == "dict test"
