"""Durable, progress-aware goal driving for unattended work."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable
from typing import Any

from agentic_harness.core.assurance import AssuranceMode
from agentic_harness.core.errors import GoalConflictError, NoActiveGoalError
from agentic_harness.core.autonomy_support import (
    autonomy_metadata as _autonomy,
    blocker_signature as _blocker_signature,
    complete_outcome as _complete_outcome,
    elapsed_seconds as _elapsed_seconds,
    evidence_registry as _evidence_registry,
    expected_review_evidence_refs as _expected_review_evidence_refs,
    outcome_blocker as _outcome_blocker,
    permanent_worker_failure as _permanent_worker_failure,
    progress_feedback as _progress_feedback,
    progress_signature as _progress_signature,
    review_failure as _review_failure,
    worker_failure as _worker_failure,
)
from agentic_harness.core.goal_spec import (
    GoalSpec,
    derived_objective_spec,
)
from agentic_harness.core.state import Goal, GoalStatus, now_iso
from agentic_harness.core.specification_controller import SpecificationController
from agentic_harness.core.supervisor import Supervisor


AUTONOMY_CONTRACT = "agentic_harness.autonomy.v1"
COMPLETION_AUDIT_CONTRACT = "agentic_harness.completion_audit.v1"


@dataclass(frozen=True)
class AutonomyPolicy:
    """Safety policy for progress-driven continuation."""

    repeated_blocker_limit: int = 3
    require_completion_claim: bool = True
    assurance_mode: AssuranceMode = AssuranceMode.SPECIFICATION_FROZEN
    max_cycles: int = 100
    max_elapsed_seconds: int = 7_200
    max_total_tokens: int = 500_000
    max_provider_calls: int = 200
    max_tool_calls: int = 1_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "assurance_mode", AssuranceMode(self.assurance_mode))
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

    def approve_specification(
        self,
        requirements: list[str] | None = None,
        *,
        expected_goal_id: str = "",
        expected_goal_spec_sha256: str = "",
        expected_spec_version: int | None = None,
    ) -> Goal:
        """Approve or edit the pending high-assurance completion conditions."""

        with self.supervisor.store.autonomy_locked() as lease:
            goal = self.supervisor.status()
            if goal is None:
                raise NoActiveGoalError("no active goal has a specification to approve")
            if self.policy.assurance_mode is not AssuranceMode.HIGH_ASSURANCE:
                raise GoalConflictError(
                    "specification approval is available only in high_assurance mode"
                )
            autonomy = self._initialize(goal)
            current_spec = self.supervisor.store.read_goal_spec(goal.id)
            current_version = self.supervisor.store.read_goal_spec_version(goal.id)
            if expected_goal_id and goal.id != expected_goal_id:
                raise GoalConflictError("the reviewed task is no longer current")
            if (
                expected_goal_spec_sha256
                and current_spec.sha256 != expected_goal_spec_sha256
            ):
                raise GoalConflictError("the reviewed completion conditions have changed")
            if (
                expected_spec_version is not None
                and current_version != expected_spec_version
            ):
                raise GoalConflictError("the reviewed specification revision has changed")
            return SpecificationController(self.supervisor).approve(
                goal,
                autonomy,
                requirements,
                lease,
                save=self._save,
            )

    def _step_unlocked(self, objective: str | None, lease: object) -> Goal:
        goal = self._load_or_start(objective, lease)
        autonomy = self._initialize(goal)
        autonomy.setdefault(
            "progress_signature",
            _progress_signature(self.supervisor),
        )
        self._save(goal, lease)
        if autonomy.get("status") in {
            "awaiting_specification_approval",
            "awaiting_specification_amendment",
        }:
            return goal
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
            reason = _worker_failure(goal)
            return self._record_blocker(
                goal,
                reason,
                lease,
                immediate=_permanent_worker_failure(goal),
            )

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
        if outcome_status == "specification_change_required":
            proposed = outcome.get("proposed_changes")
            autonomy["specification_amendment"] = {
                "reason": str(outcome.get("reason") or "Specification change requested"),
                "proposed_changes": proposed if isinstance(proposed, list) else [],
                "requested_at": now_iso(),
                "status": "pending",
            }
            if self.policy.assurance_mode is AssuranceMode.HIGH_ASSURANCE:
                autonomy["status"] = "awaiting_specification_amendment"
                autonomy["checkpoint"] = "specification_amendment_approval_required"
                autonomy["operator_intervention_required"] = True
                self._save(goal, lease)
                return goal
            self._save(goal, lease)
            return self._record_blocker(
                goal,
                "worker requested a specification amendment; frozen specification unchanged",
                lease,
                immediate=True,
            )

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
        goal_spec = self._ensure_goal_spec(goal)
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
            persisted_spec_hash = existing.get("goal_spec_sha256")
            if persisted_spec_hash not in {None, goal_spec.sha256}:
                raise GoalConflictError("persisted goal specification identity changed")
            existing["goal_spec_sha256"] = goal_spec.sha256
            existing["goal_spec_requirement_ids"] = [
                item.id for item in goal_spec.requirements
            ]
            existing["strict_completion"] = bool(persisted_strict) or bool(
                self.policy.require_completion_claim
            )
            persisted_assurance = existing.get("assurance_mode")
            if persisted_assurance not in {None, self.policy.assurance_mode.value}:
                raise GoalConflictError("persisted assurance mode cannot change during a goal")
            existing["assurance_mode"] = self.policy.assurance_mode.value
            if (
                self.policy.assurance_mode is AssuranceMode.HIGH_ASSURANCE
                and not self.supervisor.store.approved_goal_spec_path(goal).exists()
            ):
                existing["status"] = "awaiting_specification_approval"
                existing["checkpoint"] = "specification_approval_required"
                existing["operator_intervention_required"] = True
            existing.setdefault("budget", self._new_budget())
            return existing
        autonomy: dict[str, Any] = {
            "contract": AUTONOMY_CONTRACT,
            "objective": goal.objective,
            "objective_sha256": hashlib.sha256(goal.objective.encode("utf-8")).hexdigest(),
            "goal_spec_sha256": goal_spec.sha256,
            "goal_spec_requirement_ids": [item.id for item in goal_spec.requirements],
            "status": (
                "awaiting_specification_approval"
                if self.policy.assurance_mode is AssuranceMode.HIGH_ASSURANCE
                and not self.supervisor.store.approved_goal_spec_path(goal).exists()
                else "running"
            ),
            "cycle": 0,
            "heartbeat": now_iso(),
            "plan": [],
            "requirement_status": [],
            "current_subgoal": "execute the frozen requirements",
            "checkpoint": (
                "specification_approval_required"
                if self.policy.assurance_mode is AssuranceMode.HIGH_ASSURANCE
                and not self.supervisor.store.approved_goal_spec_path(goal).exists()
                else "goal_started"
            ),
            "blocker": {"signature": "", "consecutive_count": 0, "reason": ""},
            "operator_intervention_required": (
                self.policy.assurance_mode is AssuranceMode.HIGH_ASSURANCE
                and not self.supervisor.store.approved_goal_spec_path(goal).exists()
            ),
            "strict_completion": self.policy.require_completion_claim,
            "assurance_mode": self.policy.assurance_mode.value,
            "budget": self._new_budget(),
        }
        goal.metadata["autonomy"] = autonomy
        goal.metadata.setdefault("accepted", False)
        return autonomy

    def _ensure_goal_spec(self, goal: Goal) -> GoalSpec:
        store = self.supervisor.store
        with store.locked():
            current = store.read_current_goal()
            if current is None or current.id != goal.id:
                raise GoalConflictError(
                    "active goal changed while freezing its acceptance specification"
                )
            path = store.goal_spec_path(goal)
            if path.exists() or path.is_symlink():
                spec = store.read_goal_spec(goal.id)
            else:
                spec = derived_objective_spec(goal.objective)
                store.write_goal_spec(goal, spec)
            if spec.objective != goal.objective:
                raise GoalConflictError(
                    "frozen goal specification no longer matches the original objective"
                )
            return spec

    def _prepare_instruction(self, goal: Goal, autonomy: dict[str, Any]) -> None:
        goal_spec = self.supervisor.store.read_goal_spec(goal.id)
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
            f"Frozen GoalSpec SHA-256: {goal_spec.sha256}",
            "Frozen requirements (ids and text are immutable): "
            + json.dumps(
                [item.to_dict() for item in goal_spec.requirements],
                sort_keys=True,
                default=str,
            ),
            "Persisted requirement status: "
            + json.dumps(
                autonomy.get("requirement_status") or [], sort_keys=True, default=str
            ),
            f"Prior feedback: {feedback or 'none'}",
            "",
            "Continue autonomously while meaningful progress is possible.",
            "Treat failed checks and review findings as repair input, not completion.",
            "Do not ask for routine decisions or claim completion from effort, time, or token use.",
            "Completion requires every frozen requirement to be satisfied with concrete evidence.",
            "Return one HARNESS_RESULT_JSON object with status, plan, current_subgoal, ",
            "checkpoint, requirement_status, blockers, summary, and verification evidence.",
            "Do not return a requirements list or change requirement ids or text. ",
            "Use status complete for finished work. requirement_status must contain exactly one ",
            "entry for every frozen requirement id, with status satisfied and an evidence list.",
            "Requirement evidence must contain only the exact harness-issued identifiers listed ",
            "below, never your own tool-call IDs or prose.",
        ]
        strategy = goal.metadata.get("execution_strategy")
        if isinstance(strategy, dict):
            strategy_instruction = str(strategy.get("instruction") or "").strip()
            if strategy_instruction:
                lines.extend(["", strategy_instruction])
        review_refs = _expected_review_evidence_refs(
            self.supervisor,
            require_coverage=(
                self.policy.assurance_mode is not AssuranceMode.CHECK_GATED
            ),
        )
        if review_refs:
            lines.append(
                "If the corresponding independent checks pass, cite these exact IDs: "
                + ", ".join(review_refs)
            )
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
            ("requirement_status", "requirement_status"),
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
        complete = _complete_outcome(outcome)
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
        goal_spec = self.supervisor.store.read_goal_spec(goal.id)
        autonomy = _autonomy(goal)
        if autonomy.get("goal_spec_sha256") != goal_spec.sha256:
            failures.append("frozen goal specification identity changed")
        requirement_ids = [item.id for item in goal_spec.requirements]
        requirement_status = outcome.get("requirement_status")
        evidence_registry = _evidence_registry(
            self.supervisor,
            goal,
            review,
        )

        strict_completion = autonomy.get("strict_completion") is True
        assurance_mode = AssuranceMode(
            str(
                autonomy.get("assurance_mode")
                or AssuranceMode.SPECIFICATION_FROZEN.value
            )
        )
        if strict_completion:
            has_independent_criterion = isinstance(criteria, list) and any(
                isinstance(item, dict) and item.get("independent") is True
                for item in criteria
            )
            if not has_independent_criterion:
                failures.append("deterministic review has no independent criterion")
            if not _complete_outcome(outcome):
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
            if "requirements" in outcome:
                failures.append(
                    "worker returned a mutable requirements list; use requirement_status"
                )
            if not isinstance(requirement_status, list) or not requirement_status:
                failures.append("structured completion claim has no requirement status")
            else:
                rows_by_id: dict[str, dict[str, Any]] = {}
                seen_ids: set[str] = set()
                for index, requirement in enumerate(requirement_status):
                    if not isinstance(requirement, dict):
                        failures.append(f"requirement status {index + 1} is malformed")
                        continue
                    requirement_id = str(requirement.get("id") or "").strip()
                    if not requirement_id:
                        failures.append(f"requirement status {index + 1} has no id")
                        continue
                    if requirement_id in seen_ids:
                        failures.append(f"requirement {requirement_id} is duplicated")
                        continue
                    seen_ids.add(requirement_id)
                    if requirement_id not in requirement_ids:
                        failures.append(f"unknown frozen requirement id: {requirement_id}")
                        continue
                    if "text" in requirement:
                        failures.append(
                            f"requirement {requirement_id} attempts to replace frozen text"
                        )
                    rows_by_id[requirement_id] = requirement
                for missing_id in sorted(set(requirement_ids) - seen_ids):
                    failures.append(f"frozen requirement {missing_id} is missing")
                for requirement_id in requirement_ids:
                    requirement = rows_by_id.get(requirement_id)
                    if requirement is None:
                        continue
                    reported_status = str(
                        requirement.get("status") or ""
                    ).strip().lower()
                    if reported_status != "satisfied":
                        failures.append(
                            f"requirement {requirement_id} is not satisfied"
                        )
                    evidence = requirement.get("evidence")
                    if (
                        not isinstance(evidence, list)
                        or not evidence
                        or not any(str(item).strip() for item in evidence)
                    ):
                        failures.append(
                            f"requirement {requirement_id} has no evidence"
                        )
                    else:
                        normalized = [
                            str(item).strip()
                            for item in evidence
                            if isinstance(item, str) and str(item).strip()
                        ]
                        if len(normalized) != len(set(normalized)):
                            failures.append(
                                f"requirement {requirement_id} "
                                "contains duplicate evidence references"
                            )
                        invalid = sorted(
                            {
                                str(item).strip()
                                for item in evidence
                                if not isinstance(item, str)
                                or str(item).strip() not in evidence_registry
                            }
                        )
                        if invalid:
                            failures.append(
                                f"requirement {requirement_id} "
                                "cites unverified evidence: " + ", ".join(invalid)
                            )
                        elif assurance_mode is not AssuranceMode.CHECK_GATED:
                            ineligible = [
                                evidence_id
                                for evidence_id in normalized
                                if not evidence_registry[evidence_id].verifies(
                                    requirement_id,
                                    goal_id=goal.id,
                                    run_id=str(goal.metadata.get("worker_run_id") or ""),
                                    goal_spec_sha256=goal_spec.sha256,
                                )
                            ]
                            if ineligible:
                                failures.append(
                                    f"requirement {requirement_id} cites ineligible evidence: "
                                    + ", ".join(ineligible)
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
            "goal_spec_sha256": goal_spec.sha256,
            "assurance_mode": assurance_mode.value,
            "requirement_status": outcome.get("requirement_status") or [],
            "evidence_registry": [
                record.to_dict() for record in evidence_registry.values()
            ],
        }

    def _record_blocker(
        self,
        goal: Goal,
        reason: str,
        lease: object,
        *,
        immediate: bool = False,
    ) -> Goal:
        autonomy = _autonomy(goal)
        signature = _blocker_signature(self.supervisor, reason)
        previous = autonomy.get("blocker")
        previous_signature = previous.get("signature") if isinstance(previous, dict) else ""
        previous_count = (
            int(previous.get("consecutive_count") or 0) if isinstance(previous, dict) else 0
        )
        count = previous_count + 1 if signature == previous_signature else 1
        if immediate:
            count = max(count, self.policy.repeated_blocker_limit)
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
