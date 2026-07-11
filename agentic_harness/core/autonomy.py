"""Durable, progress-aware goal driving for unattended work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from collections.abc import Callable
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
    max_cycles: int = 100
    max_elapsed_seconds: int = 7_200
    max_total_tokens: int = 500_000
    max_provider_calls: int = 200
    max_tool_calls: int = 1_000

    def __post_init__(self) -> None:
        if self.repeated_blocker_limit < 1:
            raise ValueError("repeated_blocker_limit must be at least 1")
        for name in (
            "max_cycles",
            "max_elapsed_seconds",
            "max_total_tokens",
            "max_provider_calls",
            "max_tool_calls",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must not be negative")


class AutonomousRunner:
    """Own one goal until completion is proven or one blocker truly repeats."""

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        policy: AutonomyPolicy | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.policy = policy or AutonomyPolicy()
        self.cancel_requested = cancel_requested or (lambda: False)

    def run(self, objective: str | None = None) -> Goal:
        with self.supervisor.store.autonomy_locked() as lease:
            first = True
            while True:
                goal = self._step_unlocked(objective if first else None, lease)
                first = False
                autonomy = _autonomy(goal)
                if goal.status is GoalStatus.DONE:
                    return goal
                if autonomy.get("operator_intervention_required") is True:
                    return goal

    def step(self, objective: str | None = None) -> Goal:
        with self.supervisor.store.autonomy_locked() as lease:
            return self._step_unlocked(objective, lease)

    def _step_unlocked(self, objective: str | None, lease: object) -> Goal:
        goal = self._load_or_start(objective, lease)
        autonomy = self._initialize(goal)
        autonomy.setdefault(
            "progress_signature",
            _progress_signature(self.supervisor),
        )
        self._save(goal, lease)
        if goal.status is GoalStatus.DONE:
            return goal
        if goal.status is GoalStatus.FAILED:
            if autonomy.get("operator_intervention_required") is True:
                return goal
            goal = self.supervisor.restart(_autonomy_lease=lease)
            autonomy = _autonomy(goal)
        if goal.status is GoalStatus.REVIEW:
            self._record_worker_cycle(goal, autonomy, lease)
            return self._process_worker_result(goal, autonomy, lease)

        self._prepare_instruction(goal, autonomy)
        self._save(goal, lease)
        goal = self.supervisor.continue_goal(_autonomy_lease=lease)
        autonomy = _autonomy(goal)
        self._record_worker_cycle(goal, autonomy, lease)
        return self._process_worker_result(goal, autonomy, lease)

    def _record_worker_cycle(
        self,
        goal: Goal,
        autonomy: dict[str, Any],
        lease: object,
    ) -> None:
        outcome = goal.metadata.get("worker_outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        run_id = str(goal.metadata.get("worker_run_id") or "")
        processed_run_id = str(autonomy.get("processed_worker_run_id") or "")
        if run_id and processed_run_id == run_id:
            return
        if (
            not run_id
            and int(autonomy.get("cycle") or 0) > 0
            and autonomy.get("last_worker_outcome") == outcome
        ):
            return
        autonomy["cycle"] = int(autonomy.get("cycle") or 0) + 1
        autonomy["heartbeat"] = now_iso()
        self._record_outcome(autonomy, outcome)
        if run_id:
            autonomy["processed_worker_run_id"] = run_id
        self._save(goal, lease)

    def _process_worker_result(
        self,
        goal: Goal,
        autonomy: dict[str, Any],
        lease: object,
    ) -> Goal:
        outcome = goal.metadata.get("worker_outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        if self.cancel_requested():
            return self._record_cancellation(goal, lease)

        exhausted = self._budget_exhaustion(goal, outcome)
        if exhausted is not None:
            return self._record_budget_exhaustion(goal, exhausted, lease)

        if goal.status is GoalStatus.FAILED:
            return self._record_blocker(goal, _worker_failure(goal), lease)

        outcome_status = str(outcome.get("status") or "").strip().lower()
        if outcome_status == "progress":
            progress_signature = _progress_signature(
                self.supervisor,
                progress_token=str(outcome.get("progress_token") or ""),
            )
            if progress_signature == autonomy.get("progress_signature"):
                return self._record_blocker(
                    goal,
                    "worker reported progress without changing the workspace",
                    lease,
                )
            autonomy["progress_signature"] = progress_signature
            self._clear_blocker(autonomy)
            self.supervisor.loop_guard.reset()
            feedback = _progress_feedback(autonomy)
            self._save(goal, lease)
            return self.supervisor.continue_after_review(
                feedback,
                _autonomy_lease=lease,
            )
        if outcome_status == "blocked":
            return self._record_blocker(goal, _outcome_blocker(outcome), lease)

        goal = self.supervisor.review(finalize=False, _autonomy_lease=lease)
        autonomy = _autonomy(goal)
        if self.cancel_requested():
            return self._record_cancellation(goal, lease)
        if goal.status is GoalStatus.FAILED:
            return self._record_blocker(goal, _review_failure(goal), lease)

        audit = self._completion_audit(goal, outcome)
        autonomy["completion_audit"] = audit
        if not audit["passed"]:
            self._save(goal, lease)
            return self._record_blocker(
                goal,
                "; ".join(audit["failures"]),
                lease,
            )

        self._clear_blocker(autonomy)
        autonomy["status"] = "accepted"
        autonomy["checkpoint"] = str(outcome.get("checkpoint") or "accepted")
        autonomy["operator_intervention_required"] = False
        self._save(goal, lease)
        if self.cancel_requested():
            return self._record_cancellation(goal, lease)
        accepted = self.supervisor.accept(
            reason="autonomous completion audit passed",
            _autonomy_lease=lease,
            cancel_requested=self.cancel_requested,
        )
        if accepted.status is not GoalStatus.DONE and self.cancel_requested():
            return self._record_cancellation(accepted, lease)
        return accepted

    def _load_or_start(self, objective: str | None, lease: object) -> Goal:
        goal = self.supervisor.status()
        if goal is None:
            if not objective or not objective.strip():
                raise NoActiveGoalError("no active goal; provide an objective to start one")
            return self.supervisor.start(objective.strip(), _autonomy_lease=lease)
        if objective and objective.strip() != goal.objective:
            if goal.status not in {GoalStatus.DONE, GoalStatus.FAILED}:
                raise GoalConflictError(
                    f"active goal {goal.id} is {goal.status.value}; resume it without a new objective"
                )
            return self.supervisor.start(objective.strip(), _autonomy_lease=lease)
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
            persisted_strict = existing.get("strict_completion")
            if persisted_strict is not None and not isinstance(persisted_strict, bool):
                raise GoalConflictError("persisted strict completion policy is malformed")
            existing["strict_completion"] = bool(persisted_strict) or bool(
                self.policy.require_completion_claim
            )
            existing.setdefault("budget", self._new_budget())
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
            "budget": self._new_budget(),
        }
        goal.metadata["autonomy"] = autonomy
        goal.metadata.setdefault("accepted", False)
        return autonomy

    def _prepare_instruction(self, goal: Goal, autonomy: dict[str, Any]) -> None:
        current_subgoal = str(
            autonomy.get("current_subgoal") or "derive the next concrete subgoal"
        )
        feedback = str(goal.metadata.get("continuation_feedback") or "")
        lines = [
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
        safety = goal.metadata.get("safety")
        if isinstance(safety, dict):
            allowed_paths = [
                str(path)
                for path in safety.get("allowed_paths", [])
                if str(path).strip()
            ]
            lines.extend(
                [
                    "",
                    "Workspace boundary:",
                    "Allowed workspace paths (operator guidance): "
                    + (", ".join(allowed_paths) if allowed_paths else "the whole workspace"),
                    "Do not edit outside the allowed workspace paths. Preserve pre-existing changes.",
                ]
            )
            checks = safety.get("checks")
            if isinstance(checks, list):
                for row in checks:
                    if not isinstance(row, dict):
                        continue
                    label = str(row.get("label") or "").strip()
                    if label:
                        lines.append(f"Independent check: {label}")
        goal.metadata["continuation_instruction"] = "\n".join(lines)

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
        budget = autonomy.get("budget")
        if not isinstance(budget, dict):
            budget = self._new_budget()
            autonomy["budget"] = budget
        usage = budget.get("usage")
        if not isinstance(usage, dict):
            usage = {}
            budget["usage"] = usage
        usage["cycles"] = int(autonomy.get("cycle") or 0)
        reported = outcome.get("usage")
        if isinstance(reported, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "provider_calls"):
                value = reported.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = float(usage.get(key) or 0) + value
        events = outcome.get("events")
        if isinstance(events, list):
            usage["tool_calls"] = int(usage.get("tool_calls") or 0) + len(events)
        budget["elapsed_seconds"] = _elapsed_seconds(str(budget.get("started_at") or ""))

    def _new_budget(self) -> dict[str, Any]:
        return {
            "started_at": now_iso(),
            "limits": {
                "max_cycles": self.policy.max_cycles,
                "max_elapsed_seconds": self.policy.max_elapsed_seconds,
                "max_total_tokens": self.policy.max_total_tokens,
                "max_provider_calls": self.policy.max_provider_calls,
                "max_tool_calls": self.policy.max_tool_calls,
            },
            "usage": {
                "cycles": 0,
                "provider_calls": 0,
                "tool_calls": 0,
                "total_tokens": 0,
            },
            "elapsed_seconds": 0,
            "exhausted": "",
        }

    def _budget_exhaustion(
        self,
        goal: Goal,
        outcome: dict[str, Any],
    ) -> tuple[str, str] | None:
        budget = _autonomy(goal).get("budget")
        if not isinstance(budget, dict):
            return None
        limits = budget.get("limits")
        usage = budget.get("usage")
        if not isinstance(limits, dict) or not isinstance(usage, dict):
            return None
        complete = str(outcome.get("status") or "").strip().lower() == "complete"
        checks = (
            ("max_cycles", "cycles", "cycle budget"),
            ("max_total_tokens", "total_tokens", "token budget"),
            ("max_provider_calls", "provider_calls", "provider-call budget"),
            ("max_tool_calls", "tool_calls", "tool-call budget"),
        )
        for limit_key, usage_key, label in checks:
            limit = int(limits.get(limit_key) or 0)
            consumed = float(usage.get(usage_key) or 0)
            if limit and (consumed > limit or (not complete and consumed >= limit)):
                return limit_key, f"{label} exhausted ({consumed:g}/{limit})"
        elapsed_limit = int(limits.get("max_elapsed_seconds") or 0)
        elapsed = float(budget.get("elapsed_seconds") or 0)
        if elapsed_limit and (elapsed > elapsed_limit or (not complete and elapsed >= elapsed_limit)):
            return (
                "max_elapsed_seconds",
                f"time budget exhausted ({elapsed:g}/{elapsed_limit} seconds)",
            )
        return None

    def _record_budget_exhaustion(
        self,
        goal: Goal,
        exhausted: tuple[str, str],
        lease: object,
    ) -> Goal:
        key, reason = exhausted
        autonomy = _autonomy(goal)
        budget = autonomy.get("budget")
        if isinstance(budget, dict):
            budget["exhausted"] = key
            budget["exhausted_at"] = now_iso()
        autonomy["status"] = "blocked"
        autonomy["operator_intervention_required"] = True
        autonomy["blocker"] = {
            "signature": "budget:" + key,
            "consecutive_count": 1,
            "reason": reason,
            "observed_at": now_iso(),
        }
        goal.error = reason
        if goal.status is GoalStatus.REVIEW:
            goal.transition(GoalStatus.FAILED, reason="goal resource budget exhausted")
        self._save(goal, lease)
        return goal

    def _record_cancellation(self, goal: Goal, lease: object) -> Goal:
        autonomy = _autonomy(goal)
        autonomy["status"] = "stopped"
        autonomy["operator_intervention_required"] = False
        autonomy["checkpoint"] = "stopped_at_safe_boundary"
        goal.metadata["cancelled"] = True
        goal.error = "stopped by user"
        if goal.status is GoalStatus.REVIEW:
            goal.transition(GoalStatus.FAILED, reason="stopped by user at safe boundary")
        self._save(goal, lease)
        return goal

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

        strict_completion = _autonomy(goal).get("strict_completion") is True
        if strict_completion:
            has_independent_criterion = isinstance(criteria, list) and any(
                isinstance(item, dict) and item.get("independent") is True
                for item in criteria
            )
            if not has_independent_criterion:
                failures.append("deterministic review has no independent criterion")
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

    def _record_blocker(self, goal: Goal, reason: str, lease: object) -> Goal:
        autonomy = _autonomy(goal)
        signature = _blocker_signature(self.supervisor, reason)
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
        if autonomy["operator_intervention_required"]:
            autonomy["status"] = "blocked"
            goal.error = reason
            if goal.status is GoalStatus.REVIEW:
                goal.transition(GoalStatus.FAILED, reason="same blocker repeated without progress")
            self._save(goal, lease)
            return goal
        self._save(goal, lease)
        if goal.status is GoalStatus.FAILED:
            return self.supervisor.restart(_autonomy_lease=lease)
        if goal.status is GoalStatus.REVIEW:
            return self.supervisor.continue_after_review(
                reason,
                _autonomy_lease=lease,
            )
        return goal

    def _clear_blocker(self, autonomy: dict[str, Any]) -> None:
        autonomy["blocker"] = {"signature": "", "consecutive_count": 0, "reason": ""}
        autonomy["operator_intervention_required"] = False

    def _save(self, goal: Goal, lease: object) -> None:
        if not self.supervisor.store.owns_autonomy_lease(lease):
            raise GoalConflictError("autonomous driver lease was lost")
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
) -> str:
    snapshot = capture_workspace_snapshot(supervisor.project_dir)
    payload = {
        "reason": " ".join(reason.lower().split()),
        "workspace": snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _progress_signature(
    supervisor: Supervisor,
    *,
    progress_token: str = "",
) -> str:
    payload = {
        "workspace": capture_workspace_snapshot(supervisor.project_dir),
        "progress_token": progress_token,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _elapsed_seconds(started_at: str) -> float:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - started).total_seconds())
