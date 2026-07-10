"""Focused tests for the loop guard no-babysitting behavior.

The Supervisor.continue_goal method now raises InvalidTransitionError when
called on a terminal-state goal (DONE, FAILED, REVIEW). This prevents
autonomous loops from silently wasting circuit-breaker slots on terminal
goals.

This test module verifies that:
1. continue_goal raises InvalidTransitionError for terminal states.
2. continue_goal DOES record a continue event for in_progress states.
3. The circuit breaker still trips correctly for real work loops.
4. Terminal-state continues do not add events to the guard file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.worker import WorkerResult


class CountingWorker:
    """Worker that counts how many times it was called."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return WorkerResult(success=True, summary="implemented")


def _guard_event_count(guard_path: Path) -> int:
    """Return the number of events in the guard file, or 0 if it doesn't exist."""
    if not guard_path.exists():
        return 0
    try:
        data = json.loads(guard_path.read_text(encoding="utf-8"))
        return len(data.get("events", []))
    except (json.JSONDecodeError, OSError):
        return 0


def test_continue_goal_raises_for_done_goal(tmp_path) -> None:
    """Calling continue_goal on a DONE goal must raise InvalidTransitionError."""
    from agentic_harness.core.errors import InvalidTransitionError

    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("final goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    goal = supervisor.review()
    assert goal.status is GoalStatus.DONE

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    # Calling continue_goal on a DONE goal should raise.
    with pytest.raises(InvalidTransitionError):
        supervisor.continue_goal()
    events_after = _guard_event_count(guard_path)

    assert events_after == events_before, (
        f"continue_goal on DONE goal added events: before={events_before}, after={events_after}"
    )


def test_continue_goal_raises_for_failed_goal(tmp_path) -> None:
    """Calling continue_goal on a FAILED goal must raise InvalidTransitionError."""
    from agentic_harness.core.errors import InvalidTransitionError

    supervisor = Supervisor(project_dir=tmp_path)

    goal = supervisor.start("failing goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    # Calling continue_goal on a FAILED goal should raise.
    with pytest.raises(InvalidTransitionError):
        supervisor.continue_goal()
    events_after = _guard_event_count(guard_path)

    assert events_after == events_before, (
        f"continue_goal on FAILED goal added events: before={events_before}, after={events_after}"
    )


def test_continue_goal_records_guard_for_in_progress(tmp_path) -> None:
    """Calling continue_goal on a PLANNING goal MUST record a guard event."""
    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("in-progress goal")
    assert goal.status is GoalStatus.PLANNING

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    events_after = _guard_event_count(guard_path)

    assert events_after == events_before + 1, (
        f"continue_goal on PLANNING goal should add 1 event: before={events_before}, after={events_after}"
    )


def test_new_goal_does_not_inherit_previous_goal_circuit_breaker_events(tmp_path) -> None:
    """Independent goals must not fail because earlier goals used the worker."""
    guard = LoopGuard(max_continues=2, window_seconds=60, state_path=tmp_path / "guard.json")
    worker = CountingWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        loop_guard=guard,
    )

    for i in range(4):
        goal = supervisor.start(f"real work {i + 1}")
        goal = supervisor.continue_goal()
        assert goal.status is GoalStatus.REVIEW
        goal = supervisor.review()
        assert goal.status is GoalStatus.DONE


def test_guard_does_not_accumulate_events_for_review_state(tmp_path) -> None:
    """Calling continue_goal on a REVIEW goal must raise InvalidTransitionError."""
    from agentic_harness.core.errors import InvalidTransitionError

    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("review goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    with pytest.raises(InvalidTransitionError):
        supervisor.continue_goal()
    events_after = _guard_event_count(guard_path)

    assert events_after == events_before, (
        f"continue_goal on REVIEW goal added events: before={events_before}, after={events_after}"
    )


def test_multiple_terminal_continues_raise(tmp_path) -> None:
    """Multiple continue_goal calls on terminal goals must all raise InvalidTransitionError."""
    from agentic_harness.core.errors import InvalidTransitionError

    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("terminal loop")
    goal = supervisor.continue_goal()
    goal = supervisor.review()
    assert goal.status is GoalStatus.DONE

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    # Call continue_goal 10 times on the DONE goal — all should raise
    for _ in range(10):
        with pytest.raises(InvalidTransitionError):
            supervisor.continue_goal()

    events_after = _guard_event_count(guard_path)
    assert events_after == events_before, (
        f"10 continue_goal calls on DONE goal added events: before={events_before}, after={events_after}"
    )


def test_guard_events_are_scoped_to_the_current_goal(tmp_path) -> None:
    """Guard state is reset when a distinct goal starts."""
    worker = CountingWorker()
    # max_continues=4: events 1-3 allowed, 4th trips.
    guard = LoopGuard(max_continues=4, window_seconds=60, state_path=tmp_path / "guard.json")
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        loop_guard=guard,
    )

    for i in range(3):
        goal = supervisor.start(f"real work {i + 1}")
        goal = supervisor.continue_goal()
        assert goal.status is GoalStatus.REVIEW
        goal = supervisor.review()
        assert goal.status is GoalStatus.DONE

    guard_data = json.loads(guard.state_path.read_text(encoding="utf-8"))
    assert len(guard_data["events"]) == 1


def test_planning_state_continue_records_guard(tmp_path) -> None:
    """continue_goal on PLANNING goal records a guard event (transitions to IN_PROGRESS, runs worker)."""
    worker = CountingWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("planning goal")
    assert goal.status is GoalStatus.PLANNING

    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    events_before = _guard_event_count(guard_path)

    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    events_after = _guard_event_count(guard_path)

    assert events_after == events_before + 1, (
        f"continue_goal on PLANNING goal should add 1 event: before={events_before}, after={events_after}"
    )


def test_loop_guard_trip_leaves_goal_failed_not_stuck(tmp_path) -> None:
    """When the loop guard trips during continue_goal, the goal should be FAILED, not stuck in IN_PROGRESS."""
    # max_continues=1: only 0 continues allowed before tripping.
    guard = LoopGuard(max_continues=1, window_seconds=60, state_path=tmp_path / "guard.json")
    worker = CountingWorker()
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        loop_guard=guard,
    )

    # First continue: should trip the circuit breaker (1st event, 1 >= max_continues=1)
    supervisor.start("guard trip test")
    result = supervisor.continue_goal()
    assert result.status is GoalStatus.FAILED
    assert "loop guard" in (result.error or "").lower()

    # The goal should be FAILED, not stuck in IN_PROGRESS
    current = supervisor.status()
    assert current is not None
    assert current.status is GoalStatus.FAILED
