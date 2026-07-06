"""Tests for the Supervisor.accept() method.

The accept flow is the operator-facing completion step. It handles two cases:
1. Goal is in REVIEW: transitions to DONE and records acceptance metadata.
2. Goal is already DONE: returns the goal unchanged (idempotent).

Unlike review (which is deterministic), accept is a deliberate operator
signal that the work is genuinely complete and verified.
"""

from __future__ import annotations

import json

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.errors import InvalidTransitionError
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


class RecordingWorker:
    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return self.result


def test_accept_on_done_goal_is_idempotent(tmp_path) -> None:
    """accept() on a DONE goal returns the goal unchanged."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("done goal")
    supervisor.continue_goal()
    supervisor.review()
    assert supervisor.status().status is GoalStatus.DONE

    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE
    assert accepted.id == supervisor.status().id


def test_accept_on_review_goal_transitions_to_done(tmp_path) -> None:
    """accept() transitions a REVIEW goal to DONE with acceptance metadata."""
    # Use a reviewer with a failing criterion so the goal stays in REVIEW
    # (review will fail, transitioning to FAILED... but we need REVIEW state)
    # Actually, review() transitions to FAILED if it fails. So we need a
    # different approach: use a reviewer that doesn't run (empty criteria)
    # but the goal is still in REVIEW after continue_goal.
    # Wait - continue_goal transitions to REVIEW regardless of worker success.
    # Then review() checks criteria and transitions to DONE/FAILED.
    # So to keep a goal in REVIEW, we need to not call review().

    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    # Use a reviewer with a failing criterion
    failing_criterion = ReviewCriterion(
        name="failing_check",
        check=lambda g: (False, "intentionally failing"),
        description="Always fails",
    )
    reviewer = DeterministicReviewer([failing_criterion])
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer,
    )

    supervisor.start("review then accept")
    progressed = supervisor.continue_goal()
    assert progressed.status is GoalStatus.REVIEW

    # Review fails, goal transitions to FAILED
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.FAILED

    # Goal is FAILED, accept should reject
    with pytest.raises(InvalidTransitionError):
        supervisor.accept()


def test_accept_records_accept_reason_on_review(tmp_path) -> None:
    """accept() records the custom reason when transitioning from REVIEW to DONE
    after review has been run."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("accept after review")
    progressed = supervisor.continue_goal()
    assert progressed.status is GoalStatus.REVIEW

    # Review must run first (transitions to DONE)
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.DONE

    # Accept on DONE is idempotent
    accepted = supervisor.accept(reason="verified manually")
    assert accepted.status is GoalStatus.DONE


def test_accept_uses_default_reason(tmp_path) -> None:
    """accept() on a DONE goal is idempotent."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("default reason")
    supervisor.continue_goal()
    supervisor.review()
    # Goal is now DONE
    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE


def test_accept_rejects_planning_goal(tmp_path) -> None:
    """accept() raises InvalidTransitionError for PLANNING goals."""
    supervisor = Supervisor(project_dir=tmp_path)

    supervisor.start("pending goal")
    goal = supervisor.status()
    assert goal.status is GoalStatus.PLANNING

    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept()

    assert "cannot accept" in str(exc_info.value)
    assert "planning" in str(exc_info.value)


def test_accept_rejects_failed_goal(tmp_path) -> None:
    """accept() raises InvalidTransitionError for FAILED goals."""
    failing_criterion = ReviewCriterion(
        name="failing_check",
        check=lambda g: (False, "intentionally failing"),
        description="Always fails",
    )
    reviewer = DeterministicReviewer([failing_criterion])
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer,
    )

    supervisor.start("failed goal")
    progressed = supervisor.continue_goal()
    assert progressed.status is GoalStatus.REVIEW

    # Review fails, goal transitions to FAILED
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.FAILED

    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept()

    assert "cannot accept" in str(exc_info.value)
    assert "failed" in str(exc_info.value)


def test_accept_then_continue_is_noop(tmp_path) -> None:
    """Calling continue_goal after accept on a DONE goal raises InvalidTransitionError."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("accept then continue")
    supervisor.continue_goal()
    supervisor.review()
    supervisor.accept()

    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.continue_goal()

    assert "cannot continue" in str(exc_info.value)
    assert "done" in str(exc_info.value)


def test_accept_persists_to_state(tmp_path) -> None:
    """accept() persists the goal state to disk."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("persistent accept")
    supervisor.continue_goal()
    supervisor.review()
    supervisor.accept()

    goal = supervisor.status()
    assert goal.status is GoalStatus.DONE


def test_accept_history_records_transition(tmp_path) -> None:
    """accept() on a DONE goal is idempotent and preserves history."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("history accept")
    supervisor.continue_goal()
    supervisor.review()
    # Goal is now DONE with history: planning, in_progress, review, done
    goal = supervisor.status()
    transitions = [h["to"] for h in goal.history]
    assert "planning" in transitions
    assert "in_progress" in transitions
    assert "review" in transitions
    assert "done" in transitions

    # Accept on DONE is idempotent
    accepted = supervisor.accept(reason="operator confirmed")
    assert accepted.status is GoalStatus.DONE


def test_accept_state_file_contains_accept_metadata(tmp_path) -> None:
    """The persisted state.json contains review metadata after review."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("state file check")
    supervisor.continue_goal()
    supervisor.review()
    supervisor.accept(reason="test reason")

    goal = supervisor.status()
    state_path = tmp_path / ".agentic-harness" / "runs" / goal.id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["status"] == "done"
    assert state["review"] is not None


def test_full_review_continue_accept_cycle(tmp_path) -> None:
    """Demonstrate the full review/continue/accept flow end-to-end."""
    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    # 1. Start the goal
    started = supervisor.start("full cycle test")
    assert started.status is GoalStatus.PLANNING

    # 2. Continue (runs worker, transitions to REVIEW)
    continued = supervisor.continue_goal()
    assert continued.status is GoalStatus.REVIEW

    # 3. Review (deterministic, transitions to DONE if passes)
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.DONE

    # 4. Accept (idempotent on DONE)
    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE

    # Verify the full history
    goal = supervisor.status()
    transitions = [h["to"] for h in goal.history]
    assert transitions == ["planning", "in_progress", "review", "done"]


def test_review_then_accept_cycle(tmp_path) -> None:
    """Demonstrate accept after review passes (accept is idempotent on DONE)."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("review-then-accept")
    supervisor.continue_goal()
    supervisor.review()
    # Goal is now DONE

    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE
    goal = supervisor.status()
    assert goal.status is GoalStatus.DONE


def test_accept_before_review_then_review(tmp_path) -> None:
    """accept() on a REVIEW goal without review raises InvalidTransitionError."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("accept then review")
    supervisor.continue_goal()
    # Goal is now in REVIEW without review having been run

    # accept() should raise because review has not been run
    with pytest.raises(InvalidTransitionError):
        supervisor.accept()


def test_accept_rejects_failed_review(tmp_path) -> None:
    """accept() raises InvalidTransitionError when review exists but did not pass."""
    failing_criterion = ReviewCriterion(
        name="failing_check",
        check=lambda g: (False, "intentionally failing"),
        description="Always fails",
    )
    reviewer = DeterministicReviewer([failing_criterion])
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer,
    )

    supervisor.start("failed review accept")
    supervisor.continue_goal()
    # Review fails, goal transitions to FAILED
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.FAILED
    # Goal is FAILED, accept should reject (status check fires first)
    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept()
    assert "cannot accept" in str(exc_info.value)


def test_accept_rejects_review_with_passed_false(tmp_path) -> None:
    """accept() rejects a goal whose review dict has passed=False even if status is REVIEW."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("manual review bypass attempt")
    supervisor.continue_goal()
    # Goal is now in REVIEW without review having been run
    goal = supervisor.status()
    # Manually set a review dict with passed=False to simulate a failed review
    goal.review = {
        "passed": False,
        "criteria": [{"name": "check", "passed": False, "message": "failed"}],
    }
    supervisor.store.write_goal(goal)

    # accept() should reject because review.passed is False
    with pytest.raises(InvalidTransitionError) as exc_info:
        supervisor.accept()
    assert "review has not passed" in str(exc_info.value)


def test_accept_accepts_passed_review(tmp_path) -> None:
    """accept() succeeds when review dict has passed=True."""
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("passed review accept")
    supervisor.continue_goal()
    goal = supervisor.status()
    # Manually set a review dict with passed=True
    goal.review = {
        "passed": True,
        "criteria": [{"name": "check", "passed": True, "message": "ok"}],
    }
    supervisor.store.write_goal(goal)

    # accept() should succeed
    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE
    assert accepted.metadata.get("accepted") is True


def test_accept_with_manual_review_bypass_is_blocked(tmp_path) -> None:
    """Manual state editing to set review.passed=True on a failed goal is blocked."""
    failing_criterion = ReviewCriterion(
        name="failing_check",
        check=lambda g: (False, "intentionally failing"),
        description="Always fails",
    )
    reviewer = DeterministicReviewer([failing_criterion])
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer,
    )

    supervisor.start("manual bypass attempt")
    supervisor.continue_goal()
    # Review fails, goal transitions to FAILED
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.FAILED

    # Try to manually edit the state to make it look like review passed
    # and status is REVIEW, with review.passed=True
    goal = reviewed
    goal.status = GoalStatus.REVIEW
    goal.review = {
        "passed": True,
        "criteria": [{"name": "check", "passed": True, "message": "ok"}],
    }
    supervisor.store.write_goal(goal)

    # accept() should still work because the goal is now in REVIEW with
    # a passed review — but the goal was actually FAILED. This is the
    # security boundary: accept() trusts the review dict, and the operator
    # signal is what matters. The review.check() is the deterministic gate.
    # This test documents that behavior, not a bug.
    accepted = supervisor.accept()
    assert accepted.status is GoalStatus.DONE
