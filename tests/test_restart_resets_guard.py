"""Tests for restart() automatically resetting the loop guard."""

from pathlib import Path

import pytest

from agentic_harness.core.errors import InvalidTransitionError
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.state import GoalStatus
from agentic_harness.core.supervisor import Supervisor


class MockWorker:
    """Minimal worker that reports success."""

    def run(self, goal):
        from agentic_harness.core.worker import WorkerResult

        return WorkerResult(success=True, summary="mock worker ok")


def _make_supervisor(project_dir: Path, *, max_continues: int = 2) -> Supervisor:
    loop_guard = LoopGuard(max_continues=max_continues, state_path=project_dir / "guard.json")
    return Supervisor(
        project_dir=project_dir,
        worker=MockWorker(),
        loop_guard=loop_guard,
    )


def test_restart_resets_loop_guard(tmp_path: Path):
    """After a loop guard trip and restart, the guard should be reset so future continues can proceed."""
    sup = _make_supervisor(tmp_path, max_continues=1)
    sup.init()

    # Start a goal
    goal = sup.start("test objective")
    assert goal.status is GoalStatus.PLANNING

    # Continue — should trip the circuit breaker immediately (1 event, 1 >= max_continues=1)
    goal = sup.continue_goal()
    assert goal.status is GoalStatus.FAILED
    assert "loop guard tripped" in goal.error

    # The loop guard should be tripped
    assert sup.loop_guard.is_tripped()

    # Now restart the goal — this should reset the loop guard
    goal = sup.restart()
    assert goal.status is GoalStatus.PLANNING
    assert goal.error is None
    assert not sup.loop_guard.is_tripped()

    # After restart, we should be able to continue again without tripping
    goal = sup.continue_goal()
    assert goal.status is GoalStatus.FAILED  # Still trips because max_continues=1
    assert "loop guard tripped" in goal.error


def test_restart_without_prior_trip_still_resets_guard(tmp_path: Path):
    """Even if the loop guard wasn't tripped, restart() should still reset it (idempotent)."""
    sup = _make_supervisor(tmp_path, max_continues=5)
    sup.init()

    # Start and fail a goal (without tripping the guard)
    goal = sup.start("test objective")
    goal = sup.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    # Manually fail the goal by setting error and transitioning
    goal.error = "test failure"
    goal.transition(GoalStatus.FAILED, reason="manual fail")
    sup.store.write_goal(goal)

    # Restart should work and reset the guard
    goal = sup.restart()
    assert goal.status is GoalStatus.PLANNING
    assert goal.error is None
    assert not sup.loop_guard.is_tripped()


def test_restart_does_not_reset_guard_for_non_failed_goals(tmp_path: Path):
    """restart() should only work on FAILED goals — calling it on other statuses raises."""
    sup = _make_supervisor(tmp_path)
    sup.init()

    goal = sup.start("test objective")
    assert goal.status is GoalStatus.PLANNING

    # Can't restart a PLANNING goal
    with pytest.raises(InvalidTransitionError):
        sup.restart()

    # Continue to REVIEW
    goal = sup.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    # Can't restart a REVIEW goal
    with pytest.raises(InvalidTransitionError):
        sup.restart()


def test_restart_clears_worker_metadata(tmp_path: Path):
    """restart() should clear worker_success, worker_summary, worker_returncode metadata."""
    sup = _make_supervisor(tmp_path)
    sup.init()

    goal = sup.start("test objective")
    goal = sup.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    # Manually fail the goal
    goal.error = "test failure"
    goal.metadata["worker_success"] = False
    goal.metadata["worker_summary"] = "worker failed"
    goal.metadata["worker_returncode"] = 1
    goal.transition(GoalStatus.FAILED, reason="manual fail")
    sup.store.write_goal(goal)

    # Restart should clear all worker metadata
    goal = sup.restart()
    assert goal.status is GoalStatus.PLANNING
    assert "worker_success" not in goal.metadata
    assert "worker_summary" not in goal.metadata
    assert "worker_returncode" not in goal.metadata
    assert goal.error is None
