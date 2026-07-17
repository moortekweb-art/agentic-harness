"""Operator-owned initial approval and versioned GoalSpec amendment flow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentic_harness.core.autonomy_support import evidence_registry
from agentic_harness.core.errors import GoalConflictError
from agentic_harness.core.evidence import EvidenceRecord
from agentic_harness.core.goal_spec import GoalRequirement, GoalSpec
from agentic_harness.core.specification_amendment import amended_requirements
from agentic_harness.core.state import Goal, GoalStatus, now_iso
from agentic_harness.core.supervisor import Supervisor


class SpecificationController:
    """Apply only explicit operator approval to immutable specification artifacts."""

    def __init__(self, supervisor: Supervisor) -> None:
        self.supervisor = supervisor

    def approve(
        self,
        goal: Goal,
        autonomy: dict[str, Any],
        requirements: list[str] | None,
        lease: object,
        *,
        save: Callable[[Goal, object], None],
    ) -> Goal:
        if autonomy.get("status") == "awaiting_specification_amendment":
            return self._approve_amendment(
                goal,
                autonomy,
                requirements,
                lease,
                save=save,
            )
        proposal = self.supervisor.store.read_goal_spec_proposal(goal.id)
        requirement_text = (
            [str(item).strip() for item in requirements]
            if requirements is not None
            else [item.text for item in proposal.requirements]
        )
        if not requirement_text or any(not item for item in requirement_text):
            raise GoalConflictError("approved specification requires completion conditions")
        approved_path = self.supervisor.store.approved_goal_spec_path(goal)
        if approved_path.exists():
            existing = self.supervisor.store.read_goal_spec(goal.id)
            if (
                requirements is not None
                and requirement_text != [item.text for item in existing.requirements]
            ):
                raise GoalConflictError(
                    "operator-approved specification cannot be edited after approval"
                )
            return goal
        if autonomy.get("status") != "awaiting_specification_approval":
            raise GoalConflictError("specification is not awaiting operator approval")
        approved = GoalSpec(
            objective=proposal.objective,
            requirements=tuple(
                GoalRequirement(id=f"R{index}", text=text)
                for index, text in enumerate(requirement_text, 1)
            ),
            derivation=("operator_authored" if requirements is not None else proposal.derivation),
            approval="operator_approved",
            created_at=now_iso(),
        )
        with self.supervisor.store.locked():
            self.supervisor.store.write_approved_goal_spec(goal, approved)
        autonomy["goal_spec_sha256"] = approved.sha256
        autonomy["goal_spec_requirement_ids"] = [item.id for item in approved.requirements]
        autonomy["status"] = "running"
        autonomy["operator_intervention_required"] = False
        autonomy["checkpoint"] = "specification_approved"
        save(goal, lease)
        return goal

    def _approve_amendment(
        self,
        goal: Goal,
        autonomy: dict[str, Any],
        requirements: list[str] | None,
        lease: object,
        *,
        save: Callable[[Goal, object], None],
    ) -> Goal:
        amendment = autonomy.get("specification_amendment")
        if not isinstance(amendment, dict):
            raise GoalConflictError("pending specification amendment is malformed")
        current = self.supervisor.store.read_goal_spec(goal.id)
        revised = GoalSpec(
            objective=current.objective,
            requirements=amended_requirements(
                current,
                amendment.get("proposed_changes"),
                replacement_texts=requirements,
            ),
            derivation="operator_authored",
            approval="operator_approved",
            created_at=now_iso(),
        )
        review = goal.review if isinstance(goal.review, dict) else {}
        records = {
            f"{record.run_id}:{record.id}": record
            for record in evidence_registry(self.supervisor, goal, review).values()
        }
        audit = autonomy.get("completion_audit")
        audit_rows = audit.get("evidence_registry") if isinstance(audit, dict) else None
        if isinstance(audit_rows, list):
            for row in audit_rows:
                if not isinstance(row, dict):
                    continue
                try:
                    record = EvidenceRecord.from_dict(row)
                except ValueError:
                    continue
                records[f"{record.run_id}:{record.id}"] = record
        invalidated = [
            record.invalidate().to_dict()
            for record in records.values()
            if record.goal_spec_sha256 == current.sha256
        ]
        with self.supervisor.store.locked():
            _, version = self.supervisor.store.write_goal_spec_revision(
                goal,
                revised,
                previous_sha256=current.sha256,
            )
        previous_invalidated = autonomy.get("invalidated_evidence")
        history = list(previous_invalidated) if isinstance(previous_invalidated, list) else []
        autonomy["invalidated_evidence"] = [*history, *invalidated]
        invalidated_specs = autonomy.get("invalidated_specifications")
        spec_history = list(invalidated_specs) if isinstance(invalidated_specs, list) else []
        autonomy["invalidated_specifications"] = [
            *spec_history,
            {
                "goal_spec_sha256": current.sha256,
                "invalidated_at": now_iso(),
                "reason": "operator approved a replacement specification revision",
            },
        ]
        amendment.update(
            {
                "status": "approved",
                "approved_at": now_iso(),
                "previous_goal_spec_sha256": current.sha256,
                "goal_spec_sha256": revised.sha256,
                "version": version,
            }
        )
        autonomy["goal_spec_sha256"] = revised.sha256
        autonomy["goal_spec_requirement_ids"] = [item.id for item in revised.requirements]
        autonomy["requirement_status"] = []
        autonomy["status"] = "running"
        autonomy["operator_intervention_required"] = False
        autonomy["checkpoint"] = f"specification_revision_{version}_approved"
        save(goal, lease)
        if goal.status is GoalStatus.REVIEW:
            return self.supervisor.continue_after_review(
                "Operator approved a revised GoalSpec; prior evidence was invalidated.",
                _autonomy_lease=lease,
            )
        return goal
