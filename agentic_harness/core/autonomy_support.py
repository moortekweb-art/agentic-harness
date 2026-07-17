"""Pure support functions for autonomous goal driving and completion audits."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from agentic_harness.core.evidence import EvidenceRecord, EvidenceResult
from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.state import Goal
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.workspace import capture_workspace_snapshot


_COMPLETE_OUTCOME_STATUSES = {"complete", "completed", "done"}


def autonomy_metadata(goal: Goal) -> dict[str, Any]:
    value = goal.metadata.get("autonomy")
    if not isinstance(value, dict):
        raise RuntimeError("goal autonomy state is missing")
    return value


def worker_failure(goal: Goal) -> str:
    return str(goal.error or goal.metadata.get("worker_summary") or "worker failed")


def complete_outcome(outcome: dict[str, Any]) -> bool:
    return str(outcome.get("status") or "").strip().lower() in _COMPLETE_OUTCOME_STATUSES


def permanent_worker_failure(goal: Goal) -> bool:
    """Identify launch/configuration failures that another agent pass cannot repair."""

    returncode = goal.metadata.get("worker_returncode")
    if isinstance(returncode, int) and returncode in {2, 126, 127}:
        return True
    text = " ".join(
        str(value or "").lower()
        for value in (
            goal.error,
            goal.metadata.get("worker_summary"),
        )
    )
    permanent_markers = (
        "could not start",
        "command not found",
        "executable missing",
        "invalid configuration",
        "invalid value",
        "unsupported service_tier",
        "unsupported service tier",
        "requires a newer version",
        "requires newer codex",
        "not logged in",
        "authentication failed",
        "unauthorized",
        "unknown model",
        "model is not supported",
    )
    return any(marker in text for marker in permanent_markers)


def review_failure(goal: Goal) -> str:
    review = goal.review if isinstance(goal.review, dict) else {}
    criteria = review.get("criteria")
    messages: list[str] = []
    if isinstance(criteria, list):
        for row in criteria:
            if isinstance(row, dict) and row.get("passed") is not True:
                messages.append(str(row.get("message") or row.get("name") or "review failed"))
    return "; ".join(messages) or "deterministic review failed"


def outcome_blocker(outcome: dict[str, Any]) -> str:
    blockers = outcome.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "; ".join(str(item) for item in blockers)
    return str(outcome.get("summary") or "worker reported a blocker")


def progress_feedback(autonomy: dict[str, Any]) -> str:
    return "Continue from checkpoint " + str(autonomy.get("checkpoint") or "current progress")


def review_evidence_ref(index: int) -> str:
    return f"review:{index}"


def expected_review_evidence_refs(
    supervisor: Supervisor,
    *,
    require_coverage: bool,
) -> list[str]:
    return [
        review_evidence_ref(index)
        for index, criterion in enumerate(supervisor.reviewer.criteria, 1)
        if criterion.independent and (criterion.covers or not require_coverage)
    ]


def durable_event_evidence_records(
    supervisor: Supervisor,
    goal: Goal,
) -> dict[str, EvidenceRecord]:
    run_id = str(goal.metadata.get("worker_run_id") or "")
    if not run_id:
        return {}
    try:
        events = TaskEventStore(supervisor.project_dir, goal.id).read(limit=None)
    except (OSError, ValueError):
        return {}
    records: dict[str, EvidenceRecord] = {}
    for event in events:
        if event.get("run_id") != run_id:
            continue
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            continue
        tool = event.get("tool")
        tool_status = (
            str(tool.get("status") or "")
            if isinstance(tool, dict)
            else ""
        )
        kind = str(event.get("kind") or "progress").strip()[:64] or "progress"
        try:
            # Event files live inside the worker-writable workspace. Reconstruct
            # their evidence semantics from the trusted event contract instead
            # of accepting serialized issuer, result, or coverage claims.
            record = EvidenceRecord(
                id=f"event:{seq}",
                goal_id=goal.id,
                run_id=run_id,
                goal_spec_sha256=str(
                    goal.metadata.get("autonomy", {}).get("goal_spec_sha256") or ""
                ),
                issuer="harness.task_event",
                kind=kind,
                result=(
                    EvidenceResult.OBSERVED
                    if tool_status in {"passed", "completed"}
                    else EvidenceResult.FAILED
                ),
                covers=(),
            )
        except ValueError:
            continue
        records[record.id] = record
    return records


def evidence_registry(
    supervisor: Supervisor,
    goal: Goal,
    review: dict[str, Any],
) -> dict[str, EvidenceRecord]:
    run_id = str(goal.metadata.get("worker_run_id") or "")
    registry = durable_event_evidence_records(supervisor, goal)
    criteria = review.get("criteria")
    if not isinstance(criteria, list):
        return registry
    for index, criterion in enumerate(criteria, 1):
        if not isinstance(criterion, dict) or criterion.get("independent") is not True:
            continue
        covers = criterion.get("covers")
        if not isinstance(covers, list) or not all(isinstance(item, str) for item in covers):
            covers = []
        evidence_id = review_evidence_ref(index)
        try:
            registry[evidence_id] = EvidenceRecord(
                id=evidence_id,
                goal_id=goal.id,
                run_id=run_id,
                goal_spec_sha256=str(criterion.get("goal_spec_sha256") or ""),
                issuer="harness.review",
                kind="deterministic_check",
                result=(
                    EvidenceResult.VERIFIED
                    if criterion.get("passed") is True
                    else EvidenceResult.FAILED
                ),
                covers=tuple(covers),
            )
        except ValueError:
            continue
    return registry


def blocker_signature(supervisor: Supervisor, reason: str) -> str:
    payload = {
        "reason": " ".join(reason.lower().split()),
        "workspace": capture_workspace_snapshot(supervisor.project_dir),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def progress_signature(supervisor: Supervisor, *, progress_token: str = "") -> str:
    payload = {
        "workspace": capture_workspace_snapshot(supervisor.project_dir),
        "progress_token": progress_token,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def elapsed_seconds(started_at: str) -> float:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - started).total_seconds())
