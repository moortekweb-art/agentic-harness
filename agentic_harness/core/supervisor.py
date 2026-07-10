"""Goal supervisor coordinating state, workers, artifacts, and review."""

from __future__ import annotations

from pathlib import Path

from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.errors import (
    GoalConflictError,
    InvalidTransitionError,
    LoopGuardTripped,
    NoActiveGoalError,
)
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.review import DeterministicReviewer
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.worker import Worker, WorkerResult
from agentic_harness.core.workspace import capture_workspace_snapshot


class Supervisor:
    """Small project-local supervisor for one active goal at a time."""

    def __init__(
        self,
        *,
        project_dir: str | Path = ".",
        worker: Worker | None = None,
        reviewer: DeterministicReviewer | None = None,
        loop_guard: LoopGuard | None = None,
        allow_noop_success: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.store = ArtifactStore(self.project_dir / ".agentic-harness")
        self.worker = worker
        self.reviewer = reviewer or DeterministicReviewer()
        self.loop_guard = loop_guard or LoopGuard(state_path=self.store.root / "guard.json")
        self.allow_noop_success = allow_noop_success

    def init(self) -> None:
        self.store.init()

    def start(self, objective: str) -> Goal:
        with self.store.locked():
            self.init()
            active = self.status()
            if active is not None and active.status not in {
                GoalStatus.DONE,
                GoalStatus.FAILED,
            }:
                raise GoalConflictError(
                    f"active goal {active.id} is {active.status}; finish or fail it before starting another"
                )
            goal = Goal(objective=objective)
            goal.metadata["workspace_snapshot"] = capture_workspace_snapshot(self.project_dir)
            goal.transition(GoalStatus.PLANNING, reason="goal started")
            self.store.write_goal(goal)
            return goal

    def status(self) -> Goal | None:
        return self.store.read_current_goal()

    def status_summary(self) -> str | None:
        """Return a one-line status summary for logging/monitoring, or None if no active goal."""
        goal = self.status()
        if goal is None:
            return None
        duration = goal.duration_seconds
        dur_str = ""
        if duration is not None:
            if duration < 60:
                dur_str = f" ({duration:.0f}s)"
            elif duration < 3600:
                dur_str = f" ({duration / 60:.1f}m)"
            else:
                dur_str = f" ({duration / 3600:.1f}h)"
        return f"{goal.status.value}{dur_str}"

    def write_report(self, content: str, *, name: str = "report.md") -> tuple[Goal, Path]:
        """Write a markdown report for the active goal and record it as an artifact."""
        with self.store.locked():
            goal = self._require_goal()
            report_path = self.store.write_report(goal, content, name=name)
            self.store.write_goal(goal)
            return goal, report_path

    def continue_goal(self) -> Goal:
        with self.store.locked():
            goal = self._require_goal()
            if goal.status is GoalStatus.PLANNING:
                goal.transition(GoalStatus.IN_PROGRESS, reason="planning complete")
                self.store.write_goal(goal)
            if goal.status is not GoalStatus.IN_PROGRESS:
                raise InvalidTransitionError(
                    f"cannot continue goal {goal.id} in {goal.status.value}; "
                    f"only goals in in_progress can be continued. "
                    f"Terminal states (done/failed) require a new goal."
                )
            if "workspace_snapshot" not in goal.metadata:
                goal.metadata["workspace_snapshot"] = capture_workspace_snapshot(self.project_dir)
            try:
                self.loop_guard.record_continue()
            except LoopGuardTripped:
                # Circuit breaker tripped: mark the goal as failed so the
                # operator (or restart) can deal with it instead of leaving
                # the goal stuck in IN_PROGRESS with no worker result.
                goal.error = "loop guard tripped: auto-continue circuit breaker exceeded"
                goal.transition(
                    GoalStatus.FAILED,
                    reason="loop guard tripped",
                )
                self.store.write_goal(goal)
                return goal
            result = self._run_worker(goal)
            goal.metadata["worker_success"] = result.success
            goal.metadata["worker_summary"] = result.summary
            goal.metadata["worker_returncode"] = result.returncode
            goal.artifacts.extend(path for path in result.artifacts if path not in goal.artifacts)
            goal.error = None if result.success else result.stderr or result.summary
            goal.transition(
                GoalStatus.REVIEW if result.success else GoalStatus.FAILED,
                reason="worker completed" if result.success else "worker failed",
            )
            self.store.write_goal(goal)
            return goal

    def review(self) -> Goal:
        with self.store.locked():
            goal = self._require_goal()
            if goal.status is not GoalStatus.REVIEW:
                raise InvalidTransitionError(
                    f"cannot review goal {goal.id} in {goal.status.value}; "
                    f"only goals in review can be reviewed"
                )
            result = self.reviewer.review(goal)
            goal.review = result.to_dict()
            goal.transition(
                GoalStatus.DONE if result.passed else GoalStatus.FAILED,
                reason="review passed" if result.passed else "review failed",
            )
            self.store.write_goal(goal)
            return goal

    def accept(self, *, reason: str = "accepted by operator") -> Goal:
        """Explicitly accept a goal as done.

        This is the operator-facing completion step. It handles two cases:
        1. Goal is in REVIEW with a completed review: transitions to DONE and
           records acceptance metadata.
        2. Goal is already DONE: returns the goal unchanged (idempotent).

        Unlike review (which is deterministic), accept is a deliberate operator
        signal that the work is genuinely complete and verified. The goal must
        have been reviewed first — calling accept() on a REVIEW goal that has
        not yet been reviewed raises InvalidTransitionError to prevent
        bypassing the deterministic review gate.
        """
        with self.store.locked():
            goal = self._require_goal()
            if goal.status is GoalStatus.DONE:
                return goal
            if goal.status is not GoalStatus.REVIEW:
                raise InvalidTransitionError(
                    f"cannot accept goal {goal.id} in {goal.status.value}; "
                    f"only reviewed REVIEW or DONE goals can be accepted"
                )
            if goal.review is None:
                raise InvalidTransitionError(
                    f"cannot accept goal {goal.id} in {goal.status.value}; "
                    f"review must be run before accept. Run agentic-harness review first."
                )
            if not goal.review.get("passed"):
                raise InvalidTransitionError(
                    f"cannot accept goal {goal.id} in {goal.status.value}; "
                    f"review has not passed. Run agentic-harness review first."
                )
            goal.transition(
                GoalStatus.DONE,
                reason=reason,
            )
            goal.metadata["accepted"] = True
            goal.metadata["accepted_at"] = goal.updated_at
            goal.metadata["accept_reason"] = reason
            self.store.write_goal(goal)
            return goal

    def repair(self) -> Goal | None:
        with self.store.locked():
            return self.store.repair_current_marker()

    def reset_loop_guard(self) -> bool:
        """Reset the loop guard so future continue_goal() calls can proceed.

        This is the operator-facing recovery path for goals stuck because the
        auto-continue circuit breaker tripped. It resets the guard state but
        does NOT change the goal state — the operator still needs to call
        restart() on a FAILED goal to resume it.

        Returns True if the guard was reset, False if no guard file exists.
        Raises NoActiveGoalError if there is no active goal.
        """
        with self.store.locked():
            self._require_goal()
            self.loop_guard.reset()
            return True

    def restart(self) -> Goal:
        """Restart a failed goal: reset error/metadata, transition to PLANNING.

        This is the operator-facing recovery path for failed goals. It:
        1. Clears the error field so the worker can run fresh.
        2. Clears worker result metadata so the next continue does not
           re-evaluate stale success/failure signals.
        3. Resets the loop guard so the auto-continue circuit breaker can
           accept new continue events without tripping immediately.
        4. Transitions from FAILED to PLANNING (the only valid transition
           out of FAILED per the state machine).
        5. Does NOT reset the objective, artifacts, or history — those are
           durable evidence of what was attempted.

        Raises InvalidTransitionError if the goal is not in FAILED status.
        """
        with self.store.locked():
            goal = self._require_goal()
            if goal.status is not GoalStatus.FAILED:
                raise InvalidTransitionError(
                    f"cannot restart goal {goal.id} in {goal.status.value}; "
                    f"only goals in failed can be restarted. "
                    f"Terminal states (done/failed) require a new goal or restart."
                )
            goal.error = None
            goal.metadata.pop("worker_success", None)
            goal.metadata.pop("worker_summary", None)
            goal.metadata.pop("worker_returncode", None)
            goal.metadata.pop("workspace_snapshot", None)
            goal.review = None
            self.loop_guard.reset()
            goal.transition(
                GoalStatus.PLANNING,
                reason="operator restarted failed goal",
            )
            self.store.write_goal(goal)
            return goal

    def _require_goal(self) -> Goal:
        goal = self.status()
        if goal is None:
            raise NoActiveGoalError("no active goal")
        return goal

    def _run_worker(self, goal: Goal) -> WorkerResult:
        if self.worker is None:
            if self.allow_noop_success:
                return WorkerResult(success=True, summary="noop success explicitly allowed")
            return WorkerResult(
                success=False,
                summary="no worker configured",
                stderr="no worker configured; set allow_noop_success: true only for demos",
                returncode=2,
            )
        try:
            return self.worker.run(goal)
        except Exception as exc:
            return WorkerResult(
                success=False,
                summary=f"worker raised unexpected exception: {type(exc).__name__}: {exc}",
                stderr=str(exc),
                returncode=1,
            )
