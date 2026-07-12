"""Goal supervisor coordinating state, workers, artifacts, and review."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.errors import (
    GoalConflictError,
    InvalidTransitionError,
    LoopGuardTripped,
    NoActiveGoalError,
    StateLockError,
)
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.review import DeterministicReviewer
from agentic_harness.core.state import Goal, GoalStatus, now_iso
from agentic_harness.core.worker import Worker, WorkerResult
from agentic_harness.core.workspace import capture_workspace_snapshot

MAX_ATTEMPT_HISTORY = 100
REVIEW_CONTEXT_CONTRACT = "agentic_harness.review_context.v1"


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

    def start(
        self,
        objective: str,
        *,
        metadata: dict[str, object] | None = None,
        _autonomy_lease: object | None = None,
    ) -> Goal:
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            self.init()
            active = self.status()
            if active is not None and active.status not in {
                GoalStatus.DONE,
                GoalStatus.FAILED,
            }:
                raise GoalConflictError(
                    f"active goal {active.id} is {active.status}; finish or fail it before starting another"
                )
            self.loop_guard.reset()
            goal = Goal(objective=objective)
            if metadata:
                goal.metadata.update(deepcopy(metadata))
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

    def write_report(
        self,
        content: str,
        *,
        name: str = "report.md",
        _autonomy_lease: object | None = None,
    ) -> tuple[Goal, Path]:
        """Write a markdown report for the active goal and record it as an artifact."""
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            goal = self._require_goal()
            report_path = self.store.write_report(goal, content, name=name)
            self.store.write_goal(goal)
            return goal, report_path

    def continue_goal(self, *, _autonomy_lease: object | None = None) -> Goal:
        with self._mutation_lease(_autonomy_lease), self.store.locked():
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
            goal.metadata["worker_run_id"] = uuid4().hex
            attempt_number = _start_worker_attempt(goal)
            self.store.write_goal(goal)
            result = self._run_worker(goal)
            _finish_worker_attempt(goal, attempt_number, result)
            goal.metadata["worker_success"] = result.success
            goal.metadata["worker_summary"] = result.summary
            goal.metadata["worker_returncode"] = result.returncode
            goal.metadata["worker_outcome"] = dict(result.outcome)
            goal.artifacts.extend(path for path in result.artifacts if path not in goal.artifacts)
            goal.error = None if result.success else result.stderr or result.summary
            goal.transition(
                GoalStatus.REVIEW if result.success else GoalStatus.FAILED,
                reason="worker completed" if result.success else "worker failed",
            )
            self.store.write_goal(goal)
            return goal

    def review(
        self,
        *,
        finalize: bool = True,
        _autonomy_lease: object | None = None,
    ) -> Goal:
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            goal = self._require_goal()
            if goal.status is not GoalStatus.REVIEW:
                raise InvalidTransitionError(
                    f"cannot review goal {goal.id} in {goal.status.value}; "
                    f"only goals in review can be reviewed"
                )
            previous_review = deepcopy(goal.review) if isinstance(goal.review, dict) else None
            result = self.reviewer.review(goal)
            review = result.to_dict()
            review["context"] = _review_context(goal)
            if previous_review is not None:
                existing_history = goal.metadata.get("review_history")
                history = list(existing_history) if isinstance(existing_history, list) else []
                history.append(previous_review)
                goal.metadata["review_history"] = history
            goal.review = review
            if result.passed:
                if finalize:
                    goal.transition(GoalStatus.DONE, reason="review passed")
            else:
                goal.transition(GoalStatus.FAILED, reason="review failed")
            self.store.write_goal(goal)
            return goal

    def continue_after_review(
        self,
        feedback: str,
        *,
        _autonomy_lease: object | None = None,
    ) -> Goal:
        """Continue the same goal after partial work or an incomplete completion audit."""
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            goal = self._require_goal()
            if goal.status is not GoalStatus.REVIEW:
                raise InvalidTransitionError(
                    f"cannot continue reviewed goal {goal.id} in {goal.status.value}"
                )
            if goal.review is not None:
                history = goal.metadata.setdefault("review_history", [])
                if isinstance(history, list):
                    history.append(dict(goal.review))
            goal.review = None
            goal.error = None
            goal.metadata["continuation_feedback"] = feedback
            goal.transition(GoalStatus.IN_PROGRESS, reason="completion remains unproven")
            self.store.write_goal(goal)
            return goal

    def accept(
        self,
        *,
        reason: str = "accepted by operator",
        _autonomy_lease: object | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Goal:
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
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            goal = self._require_goal()
            if cancel_requested is not None and cancel_requested():
                return goal
            if goal.status is GoalStatus.DONE:
                if goal.metadata.get("accepted") is not True:
                    if not isinstance(goal.review, dict) or goal.review.get("passed") is not True:
                        raise InvalidTransitionError(
                            f"cannot accept goal {goal.id}; deterministic review has not passed"
                        )
                    goal.metadata["accepted"] = True
                    goal.metadata["accepted_at"] = goal.updated_at
                    goal.metadata["accept_reason"] = reason
                    self.store.write_goal(goal)
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
            if cancel_requested is not None and cancel_requested():
                return goal
            goal.transition(
                GoalStatus.DONE,
                reason=reason,
            )
            goal.metadata["accepted"] = True
            goal.metadata["accepted_at"] = goal.updated_at
            goal.metadata["accept_reason"] = reason
            self.store.write_goal(goal)
            return goal

    def repair(self, *, _autonomy_lease: object | None = None) -> Goal | None:
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            return self.store.repair_current_marker()

    def reset_loop_guard(self, *, _autonomy_lease: object | None = None) -> bool:
        """Reset the loop guard so future continue_goal() calls can proceed.

        This is the operator-facing recovery path for goals stuck because the
        auto-continue circuit breaker tripped. It resets the guard state but
        does NOT change the goal state — the operator still needs to call
        restart() on a FAILED goal to resume it.

        Returns True if the guard was reset, False if no guard file exists.
        Raises NoActiveGoalError if there is no active goal.
        """
        with self._mutation_lease(_autonomy_lease), self.store.locked():
            self._require_goal()
            self.loop_guard.reset()
            return True

    def restart(self, *, _autonomy_lease: object | None = None) -> Goal:
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
        with self._mutation_lease(_autonomy_lease), self.store.locked():
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
            goal.metadata.pop("worker_outcome", None)
            goal.metadata.pop("worker_run_id", None)
            goal.metadata.pop("terminal_workspace_changes", None)
            if goal.review is not None:
                review_history = goal.metadata.setdefault("review_history", [])
                if isinstance(review_history, list):
                    review_history.append(dict(goal.review))
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

    @contextmanager
    def _mutation_lease(self, lease: object | None) -> Iterator[None]:
        if lease is not None:
            if not self.store.owns_autonomy_lease(lease):
                raise StateLockError("autonomous driver lease is not owned by this supervisor")
            yield
            return
        with self.store.autonomy_locked():
            yield

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


def _start_worker_attempt(goal: Goal) -> int:
    existing = goal.metadata.get("attempt_history")
    history = list(existing) if isinstance(existing, list) else []
    numbers = [
        value
        for attempt in history
        if isinstance(attempt, dict)
        and isinstance((value := attempt.get("attempt")), int)
        and not isinstance(value, bool)
        and value > 0
    ]
    attempt_number = max(numbers, default=0) + 1
    entry: dict[str, object] = {
        "attempt": attempt_number,
        "worker_run_id": str(goal.metadata.get("worker_run_id") or ""),
        "at": now_iso(),
        "success": None,
        "returncode": None,
        "summary": "Worker attempt started.",
        "artifacts": [],
    }
    goal.metadata["attempt_history"] = [*history[-(MAX_ATTEMPT_HISTORY - 1) :], entry]
    return attempt_number


def _review_context(goal: Goal) -> dict[str, object]:
    safety = goal.metadata.get("safety")
    rows = safety.get("checks") if isinstance(safety, dict) else None
    commands: list[list[str]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            argv = row.get("argv")
            if (
                isinstance(argv, list)
                and argv
                and all(isinstance(argument, str) for argument in argv)
            ):
                commands.append(list(argv))
    canonical = json.dumps(commands, ensure_ascii=False, separators=(",", ":"))
    return {
        "contract": REVIEW_CONTEXT_CONTRACT,
        "worker_run_id": str(goal.metadata.get("worker_run_id") or ""),
        "verification_commands_sha256": hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        "verification_command_count": len(commands),
    }


def _finish_worker_attempt(
    goal: Goal,
    attempt_number: int,
    result: WorkerResult,
) -> None:
    existing = goal.metadata.get("attempt_history")
    history = list(existing) if isinstance(existing, list) else []
    worker_run_id = str(goal.metadata.get("worker_run_id") or "")
    for index in range(len(history) - 1, -1, -1):
        attempt = history[index]
        if (
            isinstance(attempt, dict)
            and attempt.get("attempt") == attempt_number
            and attempt.get("worker_run_id") == worker_run_id
        ):
            history[index] = {
                **attempt,
                "success": bool(result.success),
                "returncode": result.returncode,
                "summary": redact_secrets(result.summary),
                "artifacts": [redact_secrets(path) for path in result.artifacts],
            }
            goal.metadata["attempt_history"] = history
            return
    raise RuntimeError("active worker attempt receipt is missing")
