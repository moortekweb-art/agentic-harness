#!/usr/bin/env python3
"""Typed phase state helpers for the local Node1 goal harness."""

from __future__ import annotations

import json
import contextlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


PROFILE = Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller")
HERMES_ROOT = PROFILE
DOC_ROOT = (
    Path(os.environ.get("DOC_ROOT", "/mnt/raid0/documentation")).expanduser().resolve()
)
MANAGER = DOC_ROOT / "scripts/local-node1-goal-manager.py"
LOCAL_GOAL_WRAPPER = DOC_ROOT / "scripts/local-goal"
SUPERVISOR = PROFILE / "scripts/local-node1-goal-supervisor.py"
COMMAND_STATE_PATH = PROFILE / "state/local-node1-goal-command-latest.json"
COMMAND_REPORT_PATH = PROFILE / "reports/local-node1-goal-command-latest.md"
CURRENT_TRUTH_STATE_PATH = PROFILE / "reports/local-node1-goal-current-truth-latest.json"
CURRENT_TRUTH_REPORT_PATH = PROFILE / "reports/local-node1-goal-current-truth-latest.md"


class Phase(StrEnum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    ACCEPTED = "accepted"


@dataclass
class PhaseState:
    phase: Phase
    goal_id: str = ""
    started_at: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class GoalState:
    phase: Phase
    accepted: bool = False
    review_status: ReviewStatus | None = None
    block_reason: str | None = None
    artifacts: list[str] = field(default_factory=list)
    last_updated: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "accepted": self.accepted,
            "review_status": self.review_status.value if self.review_status else None,
            "block_reason": self.block_reason,
            "artifacts": list(self.artifacts),
            "last_updated": self.last_updated or now_iso(),
        }


VALID_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.IDLE: {Phase.IDLE, Phase.PLANNING, Phase.EXECUTING, Phase.BLOCKED},
    Phase.PLANNING: {Phase.PLANNING, Phase.EXECUTING, Phase.BLOCKED, Phase.FAILED},
    Phase.EXECUTING: {
        Phase.EXECUTING,
        Phase.REVIEWING,
        Phase.BLOCKED,
        Phase.FAILED,
        Phase.DONE,
    },
    Phase.REVIEWING: {
        Phase.REVIEWING,
        Phase.EXECUTING,
        Phase.DONE,
        Phase.BLOCKED,
        Phase.FAILED,
    },
    Phase.BLOCKED: {Phase.BLOCKED, Phase.PLANNING, Phase.EXECUTING, Phase.FAILED},
    Phase.FAILED: {Phase.FAILED, Phase.PLANNING},
    Phase.DONE: {Phase.DONE, Phase.IDLE},
}


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def write_secure_file(path: str | Path, content: str, mode: int = 0o600) -> None:
    """Write text with deterministic local artifact permissions."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = redact_secrets(content)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(target, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(target, mode)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def redact_secrets(text: str) -> str:
    """Redact common API keys, bearer tokens, credentials, and long secrets."""
    redacted = text
    redacted = re.sub(
        r"sk-ant-[a-zA-Z0-9\-_]{20,}",
        "[REDACTED:anthropic_key]",
        redacted,
    )
    redacted = re.sub(
        r"sk-proj-[a-zA-Z0-9\-_]{20,}",
        "[REDACTED:openai_key]",
        redacted,
    )
    redacted = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED:api_key]", redacted)
    redacted = re.sub(r"ghp_[a-zA-Z0-9]{36}", "[REDACTED:github_token]", redacted)
    redacted = re.sub(r"gho_[a-zA-Z0-9]{36}", "[REDACTED:github_token]", redacted)
    redacted = re.sub(
        r"[Bb]earer\s+[a-zA-Z0-9\-._~+/]+=*",
        "[REDACTED:bearer_token]",
        redacted,
    )
    redacted = re.sub(
        r"://[^/\s]+:[^/@\s]+@",
        "://[REDACTED:url_credentials]@",
        redacted,
    )
    redacted = re.sub(
        r"\b[a-fA-F0-9]{40,}\b",
        "[REDACTED:long_hex_token]",
        redacted,
    )
    redacted = re.sub(
        r"\b[A-Za-z0-9+/]{40,}={0,2}\b",
        "[REDACTED:long_base64_token]",
        redacted,
    )
    return redacted


def parse_error_record(error: Exception, source: str, text: str) -> dict[str, str]:
    """Return a compact structured JSON parse error for state artifacts."""
    return {
        "source": source,
        "type": type(error).__name__,
        "snippet": text[:100],
    }


def validate_phase_transition(old: Phase, new: Phase) -> bool:
    """Return whether a transition is valid without an explicit harness reset."""
    return new in VALID_TRANSITIONS.get(old, set())


def detect_phase_from_supervisor_output(output: str) -> PhaseState:
    """Detect the current phase from supervisor output.

    JSON payloads are authoritative when present. Text parsing exists only as a
    compatibility fallback for legacy supervisor output.
    """
    payload = _first_json_object(output)
    if isinstance(payload, dict):
        parsed = _phase_state_from_payload(payload)
        if parsed:
            return parsed
    return _phase_state_from_text(output)


def migrate_legacy_goal_state(payload: dict[str, Any]) -> GoalState:
    """Build a typed goal state from legacy boolean/status fields."""
    active_goal = payload.get("active_goal") if isinstance(payload.get("active_goal"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}

    accepted = bool(active_goal.get("accepted") or payload.get("accepted"))
    awaiting_review = bool(active_goal.get("awaiting_review") or payload.get("awaiting_review"))
    tmux_running = bool(active_goal.get("tmux_running") or payload.get("tmux_running"))
    classification = str(payload.get("classification") or payload.get("status") or "")
    phase = _phase_from_legacy_flags(
        accepted=accepted,
        awaiting_review=awaiting_review,
        tmux_running=tmux_running,
        classification=classification,
        queue_running=int(queue.get("running") or 0) if isinstance(queue.get("running"), int) else 0,
    )

    review_status = _review_status_from_legacy(review, accepted, awaiting_review)
    artifacts = _legacy_artifacts(payload, active_goal, runtime)
    block_reason = None
    if phase is Phase.BLOCKED:
        block_reason = str(
            payload.get("recovery_block_reason")
            or payload.get("next_operator_step")
            or payload.get("recommended_action")
            or "legacy blocked state"
        )
    return GoalState(
        phase=phase,
        accepted=accepted,
        review_status=review_status,
        block_reason=block_reason,
        artifacts=artifacts,
        last_updated=str(payload.get("generated_at") or payload.get("updated_at") or now_iso()),
    )


def goal_state_from_payload(payload: dict[str, Any]) -> GoalState:
    """Return typed goal state, migrating legacy payloads when needed."""
    raw = payload.get("goal_state")
    if isinstance(raw, dict):
        phase = _coerce_phase(raw.get("phase")) or Phase.IDLE
        review_status = _coerce_review_status(raw.get("review_status"))
        artifacts = raw.get("artifacts")
        return GoalState(
            phase=phase,
            accepted=bool(raw.get("accepted")),
            review_status=review_status,
            block_reason=(
                str(raw.get("block_reason")) if raw.get("block_reason") is not None else None
            ),
            artifacts=[str(item) for item in artifacts] if isinstance(artifacts, list) else [],
            last_updated=str(raw.get("last_updated") or now_iso()),
        )
    return migrate_legacy_goal_state(payload)


def _phase_state_from_payload(payload: dict[str, Any]) -> PhaseState | None:
    data = payload.get("phase_state")
    if not isinstance(data, dict):
        data = payload

    raw_phase = data.get("phase") or data.get("status") or data.get("classification")
    phase = _coerce_phase(raw_phase)
    if phase is None:
        return None

    confidence = data.get("confidence", 1.0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 1.0

    transitions = data.get("transitions")
    evidence = data.get("evidence")
    return PhaseState(
        phase=phase,
        goal_id=str(data.get("goal_id") or data.get("run_id") or payload.get("goal_id") or ""),
        started_at=str(data.get("started_at") or payload.get("started_at") or now_iso()),
        transitions=transitions if isinstance(transitions, list) else [],
        evidence=evidence if isinstance(evidence, dict) else {"source": "supervisor_json"},
        confidence=max(0.0, min(1.0, confidence_value)),
    )


def _phase_from_legacy_flags(
    *,
    accepted: bool,
    awaiting_review: bool,
    tmux_running: bool,
    classification: str,
    queue_running: int,
) -> Phase:
    lower = classification.lower()
    if "fail" in lower:
        return Phase.FAILED
    if "blocked" in lower or "stuck" in lower:
        return Phase.BLOCKED
    if accepted or lower in {"accepted", "complete", "done"}:
        return Phase.DONE
    if awaiting_review or "review" in lower:
        return Phase.REVIEWING
    if tmux_running or queue_running > 0 or lower in {"working", "running", "active"}:
        return Phase.EXECUTING
    if "planning" in lower:
        return Phase.PLANNING
    return Phase.IDLE


def _review_status_from_legacy(
    review: dict[str, Any], accepted: bool, awaiting_review: bool
) -> ReviewStatus | None:
    raw = review.get("status")
    if raw:
        coerced = _coerce_review_status(raw)
        if coerced:
            return coerced
    if accepted:
        return ReviewStatus.ACCEPTED
    if awaiting_review:
        return ReviewStatus.PENDING
    return None


def _legacy_artifacts(
    payload: dict[str, Any], active_goal: dict[str, Any], runtime: dict[str, Any]
) -> list[str]:
    values = [
        active_goal.get("prompt_path"),
        active_goal.get("planner_packet_path"),
        active_goal.get("run_dir"),
        runtime.get("log_path"),
        runtime.get("checkpoint_path"),
        runtime.get("complete_marker_path"),
    ]
    artifacts = []
    for value in values:
        if value and str(value) not in artifacts:
            artifacts.append(str(value))
    artifact_paths = payload.get("artifact_paths")
    if isinstance(artifact_paths, dict):
        for value in artifact_paths.values():
            if value and str(value) not in artifacts:
                artifacts.append(str(value))
    return artifacts


def _coerce_review_status(value: Any) -> ReviewStatus | None:
    if isinstance(value, ReviewStatus):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "pass": ReviewStatus.PASSED,
        "passed": ReviewStatus.PASSED,
        "ok": ReviewStatus.PASSED,
        "needs_review": ReviewStatus.PENDING,
        "awaiting_review": ReviewStatus.PENDING,
        "accepted": ReviewStatus.ACCEPTED,
        "fail": ReviewStatus.FAILED,
        "failed": ReviewStatus.FAILED,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return ReviewStatus(normalized)
    except ValueError:
        return None


def _phase_state_from_text(output: str) -> PhaseState:
    text = output or ""
    lower = text.lower()
    checks: list[tuple[Phase, tuple[str, ...]]] = [
        (Phase.FAILED, ("failed", "error", "traceback", "non-zero")),
        (Phase.BLOCKED, ("blocked", "stuck", "needs human", "waiting for operator")),
        (Phase.REVIEWING, ("ready for review", "awaiting review", "reviewing")),
        (Phase.DONE, ("accepted", "complete", "done")),
        (Phase.EXECUTING, ("executing", "working", "worker running", "tmux running")),
        (Phase.PLANNING, ("planning", "planner", "plan ready")),
        (Phase.IDLE, ("idle", "no active goal", "lane free")),
    ]
    for phase, markers in checks:
        matched = [marker for marker in markers if marker in lower]
        if matched:
            return PhaseState(
                phase=phase,
                started_at=now_iso(),
                evidence={"source": "text_fallback", "matched_markers": matched},
                confidence=0.65,
            )
    return PhaseState(
        phase=Phase.IDLE,
        started_at=now_iso(),
        evidence={"source": "text_fallback", "matched_markers": []},
        confidence=0.25,
    )


def _coerce_phase(value: Any) -> Phase | None:
    if isinstance(value, Phase):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "working": Phase.EXECUTING,
        "running": Phase.EXECUTING,
        "active": Phase.EXECUTING,
        "awaiting_review": Phase.REVIEWING,
        "ready_for_review": Phase.REVIEWING,
        "complete": Phase.DONE,
        "completed": Phase.DONE,
        "accepted": Phase.DONE,
        "error": Phase.FAILED,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return Phase(normalized)
    except ValueError:
        return None


def _first_json_object(output: str) -> dict[str, Any] | None:
    text = (output or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"\{", text):
        decoder = json.JSONDecoder()
        try:
            payload, _end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None
