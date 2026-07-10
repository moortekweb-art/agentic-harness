"""Durable, progress-aware goal driving for unattended work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from agentic_harness.core.errors import GoalConflictError, NoActiveGoalError
from agentic_harness.core.state import Goal, GoalStatus, now_iso
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.workspace import capture_workspace_snapshot


AUTONOMY_CONTRACT = "agentic_harness.autonomy.v1"
COMPLETION_AUDIT_CONTRACT = "agentic_harness.completion_audit.v1"


@dataclass(frozen=True)
class AutonomyPolicy:
    """Safety policy for progress-driven continuation."""

    repeated_blocker_limit: int = 3
    require_completion_claim: bool = True

    def __post_init__(self) -> None:
        if self.repeated_blocker_limit < 1:
            raise ValueError("repeated_blocker_limit must be at least 1")


class AutonomousRunner:
    """Own one goal until completion is proven or one blocker truly repeats."""

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.policy = policy or AutonomyPolicy()

    def run(self, objective: str | None = None) -> Goal:
        with self.supervisor.store.autonomy_locked():
            first = True
            while True:
                goal = self._step_unlocked(objective if first else None)
                first = False
                autonomy = _autonomy(goal)
                if goal.status is GoalStatus.DONE:
                    return goal
                if autonomy.get("operator_intervention_required") is True:
                    return goal

    def step(self, objective: str | None = None) -> Goal:
        with self.supervisor.store.autonomy_locked():
            return self._step_unlocked(objective)

    def _step_unlocked(self, objective: str | None = None) -> Goal:
        goal = self._load_or_start(objective)
        autonomy = self._initialize(goal)
        autonomy.setdefault(
            "progress_signature",
            _progress_signature(self.supervisor, autonomy),
        )
        self._save(goal)
        if goal.status is GoalStatus.DONE:
            return goal
        if goal.status is GoalStatus.FAILED:
            if autonomy.get("operator_intervention_required") is True:
                return goal
            goal = self.supervisor.restart()
            autonomy = _autonomy(goal)

        self._prepare_instruction(goal, autonomy)
        self._save(goal)
        goal = self.supervisor.continue_goal()
        autonomy = _autonomy(goal)
        autonomy["cycle"] = int(autonomy.get("cycle") or 0) + 1
        autonomy["heartbeat"] = now_iso()
        outcome = goal.metadata.get("worker_outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        self._record_outcome(autonomy, outcome)
        self._save(goal)

        if goal.status is GoalStatus.FAILED:
            return self._record_blocker(goal, _worker_failure(goal))

        outcome_status = str(outcome.get("status") or "").strip().lower()
        if outcome_status == "progress":
            progress_signature = _progress_signature(self.supervisor, autonomy)
            if progress_signature == autonomy.get("progress_signature"):
                return self._record_blocker(
                    goal,
                    "worker reported progress without changing the workspace or checkpoint",
                )
            autonomy["progress_signature"] = progress_signature
            self._clear_blocker(autonomy)
            self.supervisor.loop_guard.reset()
            feedback = _progress_feedback(autonomy)
            self._save(goal)
            return self.supervisor.continue_after_review(feedback)
        if outcome_status == "blocked":
            return self._record_blocker(goal, _outcome_blocker(outcome))

        goal = self.supervisor.review(finalize=False)
        autonomy = _autonomy(goal)
        if goal.status is GoalStatus.FAILED:
            return self._record_blocker(goal, _review_failure(goal))

        audit = self._completion_audit(goal, outcome)
        autonomy["completion_audit"] = audit
        if not audit["passed"]:
            self._save(goal)
            return self._record_blocker(goal, "; ".join(audit["failures"]))

        self._clear_blocker(autonomy)
        autonomy["status"] = "accepted"
        autonomy["checkpoint"] = str(outcome.get("checkpoint") or "accepted")
        autonomy["operator_intervention_required"] = False
        self._save(goal)
        return self.supervisor.accept(reason="autonomous completion audit passed")

    def _load_or_start(self, objective: str | None) -> Goal:
        goal = self.supervisor.status()
        if goal is None:
            if not objective or not objective.strip():
                raise NoActiveGoalError("no active goal; provide an objective to start one")
            return self.supervisor.start(objective.strip())
        if objective and objective.strip() != goal.objective:
            if goal.status not in {GoalStatus.DONE, GoalStatus.FAILED}:
                raise GoalConflictError(
                    f"active goal {goal.id} is {goal.status.value}; resume it without a new objective"
                )
            return self.supervisor.start(objective.strip())
        return goal

    def _initialize(self, goal: Goal) -> dict[str, Any]:
        existing = goal.metadata.get("autonomy")
        if isinstance(existing, dict) and existing.get("contract") == AUTONOMY_CONTRACT:
            original_objective = str(existing.get("objective") or "")
            original_hash = hashlib.sha256(original_objective.encode("utf-8")).hexdigest()
            if (
                not original_objective
                or goal.objective != original_objective
                or existing.get("objective_sha256") != original_hash
            ):
                raise GoalConflictError(
                    "persisted goal no longer matches the original objective identity"
                )
            return existing
        autonomy: dict[str, Any] = {
            "contract": AUTONOMY_CONTRACT,
            "objective": goal.objective,
            "objective_sha256": hashlib.sha256(goal.objective.encode("utf-8")).hexdigest(),
            "status": "running",
            "cycle": 0,
            "heartbeat": now_iso(),
            "plan": [],
            "requirements": [],
            "current_subgoal": "derive the plan and requirements",
            "checkpoint": "goal_started",
            "blocker": {"signature": "", "consecutive_count": 0, "reason": ""},
            "operator_intervention_required": False,
            "strict_completion": self.policy.require_completion_claim,
        }
        goal.metadata["autonomy"] = autonomy
        goal.metadata.setdefault("accepted", False)
        return autonomy

    def _prepare_instruction(self, goal: Goal, autonomy: dict[str, Any]) -> None:
        current_subgoal = str(
            autonomy.get("current_subgoal") or "derive the next concrete subgoal"
        )
        feedback = str(goal.metadata.get("continuation_feedback") or "")
        goal.metadata["continuation_instruction"] = "\n".join(
            [
                "Preserve and pursue this complete objective without shrinking it:",
                goal.objective,
                "",
                f"Current subgoal: {current_subgoal}",
                f"Checkpoint: {autonomy.get('checkpoint') or 'none'}",
                "Persisted plan: "
                + json.dumps(autonomy.get("plan") or [], sort_keys=True, default=str),
                "Persisted requirements: "
                + json.dumps(
                    autonomy.get("requirements") or [], sort_keys=True, default=str
                ),
                f"Prior feedback: {feedback or 'none'}",
                "",
                "Continue autonomously while meaningful progress is possible.",
                "Treat failed checks and review findings as repair input, not completion.",
                "Do not ask for routine decisions or claim completion from effort, time, or token use.",
                "Completion requires every derived requirement to be satisfied with concrete evidence.",
                "Return one HARNESS_RESULT_JSON object with status, plan, current_subgoal, ",
                "checkpoint, requirements, blockers, summary, and verification evidence.",
            ]
        )

    def _record_outcome(self, autonomy: dict[str, Any], outcome: dict[str, Any]) -> None:
        for source, target in (
            ("plan", "plan"),
            ("requirements", "requirements"),
            ("current_subgoal", "current_subgoal"),
            ("checkpoint", "checkpoint"),
        ):
            value = outcome.get(source)
            if value not in (None, "", []):
                autonomy[target] = value
        autonomy["last_worker_outcome"] = outcome
        autonomy["status"] = "checking"

    def _completion_audit(
        self, goal: Goal, outcome: dict[str, Any]
    ) -> dict[str, Any]:
        failures: list[str] = []
        review = goal.review if isinstance(goal.review, dict) else {}
        if review.get("passed") is not True:
            failures.append("deterministic review did not pass")
        criteria = review.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            failures.append("deterministic review produced no criteria evidence")

        if self.policy.require_completion_claim:
            if str(outcome.get("status") or "").strip().lower() != "complete":
                failures.append("structured completion claim is missing")
            if not str(outcome.get("summary") or "").strip():
                failures.append("structured completion claim has no summary")
            if not str(outcome.get("current_subgoal") or "").strip():
                failures.append("structured completion claim has no current subgoal")
            if not str(outcome.get("checkpoint") or "").strip():
                failures.append("structured completion claim has no checkpoint")
            plan = outcome.get("plan")
            if not isinstance(plan, list) or not plan:
                failures.append("structured completion claim has no plan")
            else:
                for index, item in enumerate(plan):
                    if not isinstance(item, dict):
                        failures.append(f"plan item {index + 1} is malformed")
                        continue
                    item_status = str(item.get("status") or "").strip().lower()
                    if item_status not in {"complete", "completed", "done"}:
                        failures.append(f"plan item {index + 1} is not completed")
            requirements = outcome.get("requirements")
            if not isinstance(requirements, list) or not requirements:
                failures.append("structured completion claim has no requirements")
            else:
                for index, requirement in enumerate(requirements):
                    if not isinstance(requirement, dict):
                        failures.append(f"requirement {index + 1} is malformed")
                        continue
                    requirement_id = str(requirement.get("id") or "").strip()
                    if not requirement_id:
                        failures.append(f"requirement {index + 1} has no id")
                    requirement_status = str(
                        requirement.get("status") or ""
                    ).strip().lower()
                    if requirement_status != "satisfied":
                        failures.append(
                            f"requirement {requirement.get('id') or index + 1} is not satisfied"
                        )
                    evidence = requirement.get("evidence")
                    if (
                        not isinstance(evidence, list)
                        or not evidence
                        or not any(str(item).strip() for item in evidence)
                    ):
                        failures.append(
                            f"requirement {requirement.get('id') or index + 1} has no evidence"
                        )
            blockers = outcome.get("blockers")
            if not isinstance(blockers, list):
                failures.append("blockers list is missing")
            elif blockers:
                failures.append("structured completion claim still contains blockers")

        return {
            "contract": COMPLETION_AUDIT_CONTRACT,
            "audited_at": now_iso(),
            "passed": not failures,
            "failures": failures,
            "review": review,
            "requirements": outcome.get("requirements") or [],
        }

    def _record_blocker(self, goal: Goal, reason: str) -> Goal:
        autonomy = _autonomy(goal)
        signature = _blocker_signature(self.supervisor, reason, autonomy)
        previous = autonomy.get("blocker")
        previous_signature = previous.get("signature") if isinstance(previous, dict) else ""
        previous_count = (
            int(previous.get("consecutive_count") or 0) if isinstance(previous, dict) else 0
        )
        count = previous_count + 1 if signature == previous_signature else 1
        autonomy["blocker"] = {
            "signature": signature,
            "consecutive_count": count,
            "reason": reason,
            "observed_at": now_iso(),
        }
        autonomy["status"] = "continuing"
        autonomy["operator_intervention_required"] = (
            count >= self.policy.repeated_blocker_limit
        )
        goal.metadata["continuation_feedback"] = reason
        self._save(goal)

        if autonomy["operator_intervention_required"]:
            autonomy["status"] = "blocked"
            goal.error = reason
            if goal.status is GoalStatus.REVIEW:
                goal.transition(GoalStatus.FAILED, reason="same blocker repeated without progress")
            self._save(goal)
            return goal
        if goal.status is GoalStatus.FAILED:
            return self.supervisor.restart()
        if goal.status is GoalStatus.REVIEW:
            return self.supervisor.continue_after_review(reason)
        return goal

    def _clear_blocker(self, autonomy: dict[str, Any]) -> None:
        autonomy["blocker"] = {"signature": "", "consecutive_count": 0, "reason": ""}
        autonomy["operator_intervention_required"] = False

    def _save(self, goal: Goal) -> None:
        with self.supervisor.store.locked():
            current = self.supervisor.store.read_current_goal()
            if current is None or current.id != goal.id:
                raise GoalConflictError("active goal changed while autonomous work was running")
            self.supervisor.store.write_goal(goal)


def _autonomy(goal: Goal) -> dict[str, Any]:
    value = goal.metadata.get("autonomy")
    if not isinstance(value, dict):
        raise RuntimeError("goal autonomy state is missing")
    return value


def _worker_failure(goal: Goal) -> str:
    return str(goal.error or goal.metadata.get("worker_summary") or "worker failed")


def _review_failure(goal: Goal) -> str:
    review = goal.review if isinstance(goal.review, dict) else {}
    criteria = review.get("criteria")
    messages: list[str] = []
    if isinstance(criteria, list):
        for row in criteria:
            if isinstance(row, dict) and row.get("passed") is not True:
                messages.append(str(row.get("message") or row.get("name") or "review failed"))
    return "; ".join(messages) or "deterministic review failed"


def _outcome_blocker(outcome: dict[str, Any]) -> str:
    blockers = outcome.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "; ".join(str(item) for item in blockers)
    return str(outcome.get("summary") or "worker reported a blocker")


def _progress_feedback(autonomy: dict[str, Any]) -> str:
    return "Continue from checkpoint " + str(autonomy.get("checkpoint") or "current progress")


def _blocker_signature(
    supervisor: Supervisor,
    reason: str,
    autonomy: dict[str, Any],
) -> str:
    snapshot = capture_workspace_snapshot(supervisor.project_dir)
    payload = {
        "reason": " ".join(reason.lower().split()),
        "workspace": snapshot,
        "checkpoint": autonomy.get("checkpoint"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _progress_signature(
    supervisor: Supervisor,
    autonomy: dict[str, Any],
) -> str:
    payload = {
        "workspace": capture_workspace_snapshot(supervisor.project_dir),
        "checkpoint": autonomy.get("checkpoint"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
