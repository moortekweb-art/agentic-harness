"""Goal supervisor coordinating state, workers, artifacts, and review."""

from __future__ import annotations

from pathlib import Path

from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.review import DeterministicReviewer
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.worker import Worker, WorkerResult


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
        self.loop_guard = loop_guard or LoopGuard(
            state_path=self.store.root / "guard.json"
        )
        self.allow_noop_success = allow_noop_success

    def init(self) -> None:
        self.store.init()

    def start(self, objective: str) -> Goal:
        self.init()
        goal = Goal(objective=objective)
        goal.transition(GoalStatus.PLANNING, reason="goal started")
        self.store.write_goal(goal)
        return goal

    def status(self) -> Goal | None:
        return self.store.read_current_goal()

    def continue_goal(self) -> Goal:
        self.loop_guard.record_continue()
        goal = self._require_goal()
        if goal.status is GoalStatus.PLANNING:
            goal.transition(GoalStatus.IN_PROGRESS, reason="planning complete")
        if goal.status is not GoalStatus.IN_PROGRESS:
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
        goal = self._require_goal()
        result = self.reviewer.review(goal)
        goal.review = result.to_dict()
        goal.transition(
            GoalStatus.DONE if result.passed else GoalStatus.FAILED,
            reason="review passed" if result.passed else "review failed",
        )
        self.store.write_goal(goal)
        return goal

    def repair(self) -> Goal | None:
        return self.store.repair_current_marker()

    def _require_goal(self) -> Goal:
        goal = self.status()
        if goal is None:
            raise RuntimeError("no active goal")
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
        return self.worker.run(goal)
