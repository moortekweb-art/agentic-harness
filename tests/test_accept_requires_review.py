"""Tests for the accept-gate enforcement.

The Supervisor.accept() method must require that review has been run before
accepting a REVIEW goal. This prevents autonomous loops from bypassing the
deterministic review gate and marking goals as done without verification.
"""

from __future__ import annotations

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.errors import InvalidTransitionError
from agentic_harness.core.review import DeterministicReviewer
from agentic_harness.core.worker import WorkerResult


class PassWorker:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return WorkerResult(success=True, summary="implemented")


def test_accept_raises_when_review_not_run(tmp_path) -> None:
    """accept() must raise InvalidTransitionError when called on a REVIEW goal
    that has not yet been reviewed."""
    worker = PassWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("must review first")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    assert goal.review is None

    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept(reason="premature")

    error = str(exc_info.value)
    assert "review must be run" in error
    assert "review" in error.lower()

    # Goal should still be in REVIEW, not DONE
    current = supervisor.status()
    assert current.status is GoalStatus.REVIEW


def test_accept_works_after_review_passes(tmp_path) -> None:
    """accept() should succeed when the goal is already DONE (idempotent).

    The normal flow is: start → continue → REVIEW, review → DONE.
    After review transitions to DONE, accept() is idempotent.
    The critical check is that accept() cannot skip review entirely,
    which is tested by test_accept_raises_when_review_not_run.
    """
    worker = PassWorker()
    reviewer = DeterministicReviewer()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker, reviewer=reviewer)

    goal = supervisor.start("proper flow")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    goal = supervisor.review()
    assert goal.status is GoalStatus.DONE
    assert goal.review is not None

    # accept() on an already-DONE goal is idempotent
    goal2 = supervisor.accept(reason="operator verified")
    assert goal2.status is GoalStatus.DONE


def test_accept_raises_for_non_review_non_done(tmp_path) -> None:
    """accept() must raise for goals that are neither REVIEW nor DONE."""
    worker = PassWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("planning goal")
    assert goal.status is GoalStatus.PLANNING

    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept(reason="wrong state")

    error = str(exc_info.value)
    assert "planning" in error
    assert "review" in error.lower()


def test_accept_does_not_bypass_review_gate(tmp_path) -> None:
    """End-to-end: accept() cannot be used to skip review entirely.

    This is the critical no-babysitting guarantee: an autonomous loop that
    calls accept() on a REVIEW goal without first calling review() must fail.
    """
    worker = PassWorker()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    goal = supervisor.start("no bypass")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    # The loop guard should trip if the loop keeps calling continue_goal
    # on terminal states, but accept() should also be blocked.
    with pytest.raises(InvalidTransitionError):
        supervisor.accept(reason="bypass attempt")

    # Goal must still be in REVIEW
    current = supervisor.status()
    assert current.status is GoalStatus.REVIEW
    assert current.review is None
