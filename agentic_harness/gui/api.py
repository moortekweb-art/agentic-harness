"""Human-facing API helpers for the local GUI."""

from __future__ import annotations

from typing import Any
import json
from datetime import UTC, datetime

from agentic_harness.core.local_goal_bridge import CommandResult, HUMAN_MODES, LocalGoalBridge


TaskPayload = dict[str, Any]

_TECHNICAL_SUMMARY_TERMS = (
    "hermes watcher",
    "node1",
    "opencode",
    "vllm",
    "mode 3a",
    "executor-worker",
    "local-goal",
    "run_dir",
)


def modes_payload() -> list[dict[str, Any]]:
    return [
        {
            "key": mode.key,
            "number": mode.number,
            "label": mode.title,
            "best_for": mode.best_for,
            "caution": mode.caution,
        }
        for mode in HUMAN_MODES
    ]


def health_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    return {
        "ok": True,
        "app": "agentic-harness",
        "local_goal_available": bridge.available(),
        "local_goal_path": str(bridge.local_goal),
        "readiness": readiness_payload(bridge),
        "no_babysitting": {
            "enabled": True,
            "policy": "The worker should move safe work forward without repeated check-ins.",
            "human_review_statuses": ["needs_review", "blocked"],
        },
    }


def readiness_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    if not bridge.available():
        return _readiness_gate(
            "blocked",
            "The background worker is not installed or is not executable on this machine.",
            {"local_goal_path": str(bridge.local_goal)},
        )
    task = task_from_command_result(bridge.status(json_output=True), fallback_status="ready")
    gate = dict(task.get("readiness_gate", {}))
    gate["agent_loop"] = task.get("agent_loop", _agent_loop_for_status(str(task.get("status", "ready"))))
    return gate


def start_task(
    bridge: LocalGoalBridge,
    body: dict[str, Any],
) -> TaskPayload:
    objective = str(body.get("objective", "")).strip()
    mode = str(body.get("mode", "cloud")).strip() or "cloud"
    safe_areas = tuple(_string_list(body.get("safe_areas")))
    checks = tuple(_string_list(body.get("checks")))
    if not objective:
        return _task(
            status="blocked",
            summary="Tell the assistant what you want done first.",
            needs_human=True,
            advanced_details={"error": "objective must not be empty"},
        )
    if not bridge.available():
        return _task(
            status="blocked",
            summary="The background worker is not installed or is not executable on this machine.",
            needs_human=True,
            advanced_details=health_payload(bridge),
        )
    readiness = readiness_payload(bridge)
    if readiness.get("requires_review") is True:
        return _task(
            status="needs_review",
            summary=str(readiness.get("next_action") or "Review current work before starting another task."),
            needs_human=True,
            advanced_details={"readiness": readiness},
        )
    try:
        result = bridge.start_human_goal(
            mode_key=mode,
            objective=objective,
            safe_areas=safe_areas,
            checks=checks,
        )
    except ValueError as exc:
        return _task(
            status="blocked",
            summary=str(exc),
            needs_human=True,
            advanced_details={"error": str(exc)},
        )
    return task_from_command_result(result, fallback_status="starting")


def status_task(bridge: LocalGoalBridge) -> TaskPayload:
    if not bridge.available():
        return _task(
            status="blocked",
            summary="The background worker is not installed or is not executable on this machine.",
            needs_human=True,
            advanced_details=health_payload(bridge),
        )
    return task_from_command_result(bridge.status(json_output=True), fallback_status="ready")


def watch_task(bridge: LocalGoalBridge) -> TaskPayload:
    if not bridge.available():
        return status_task(bridge)
    return task_from_command_result(bridge.monitor(json_output=True), fallback_status="checking")


def command_task(bridge: LocalGoalBridge, command: str, body: dict[str, Any] | None = None) -> TaskPayload:
    if not bridge.available():
        return status_task(bridge)
    body = body or {}
    if command == "accept":
        result = bridge.run(["accept"])
    elif command == "continue":
        feedback = str(body.get("feedback", "")).strip()
        args = ["continue"]
        if feedback:
            args.extend(["--feedback", feedback])
        result = bridge.run(args)
    elif command == "stop":
        result = bridge.run(["stop"])
    else:
        return _task(
            status="blocked",
            summary=f"Unknown action: {command}",
            needs_human=True,
            advanced_details={"command": command},
        )
    return task_from_command_result(result, fallback_status="checking")


def details_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    status = status_task(bridge)
    return {
        "task": status,
        "raw": status.get("advanced_details", {}),
    }


def task_from_command_result(result: CommandResult, *, fallback_status: str) -> TaskPayload:
    parsed = _json_from_output(result.stdout)
    advanced_details: dict[str, Any] = {
        "args": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if parsed is not None:
        advanced_details["payload"] = parsed
    if result.returncode != 0:
        return _task(
            status="blocked",
            summary=_clean_summary(result.stderr or result.stdout or "The task could not move forward."),
            needs_human=True,
            advanced_details=advanced_details,
        )
    status = _status_from_payload(parsed, fallback_status=fallback_status)
    summary = _summary_from_payload(parsed, result.stdout, fallback_status=status)
    return _task(
        status=status,
        summary=summary,
        needs_human=status in {"needs_review", "blocked"},
        changed_files=_changed_files_from_payload(parsed),
        verification=_verification_from_payload(parsed),
        advanced_details=advanced_details,
    )


def _task(
    *,
    status: str,
    summary: str,
    needs_human: bool,
    changed_files: list[str] | None = None,
    verification: list[str] | None = None,
    advanced_details: dict[str, Any] | None = None,
) -> TaskPayload:
    command = ""
    details = advanced_details or {}
    if isinstance(details.get("args"), (list, tuple)):
        command = " ".join(str(part) for part in details["args"])
    agent_loop = _agent_loop_for_status(status)
    readiness_gate = _readiness_gate(status, summary, details)
    return {
        "id": "",
        "human_title": "Current work",
        "status": status,
        "status_label": _label_for_status(status),
        "progress": _progress_for_status(status),
        "summary": summary,
        "needs_human": needs_human,
        "changed_files": changed_files or [],
        "verification": verification or [],
        "artifacts": _artifacts_from_details(details),
        "agent_loop": agent_loop,
        "readiness_gate": readiness_gate,
        "metadata": {
            "command": command,
            "updated_at": datetime.now(UTC).isoformat(),
        },
        "advanced_details": details,
    }


def _label_for_status(status: str) -> str:
    return {
        "ready": "Ready",
        "starting": "Starting",
        "working": "Working",
        "checking": "Checking work",
        "needs_review": "Needs review",
        "done": "Done",
        "blocked": "Blocked",
        "stopped": "Stopped",
    }.get(status, "Working")


def _progress_for_status(status: str) -> int:
    return {
        "ready": 0,
        "starting": 10,
        "working": 45,
        "checking": 60,
        "needs_review": 70,
        "blocked": 0,
        "stopped": 0,
        "done": 100,
    }.get(status, 45)


def _agent_loop_for_status(status: str) -> dict[str, Any]:
    stages = ["Perceive", "Plan", "Act", "Check", "Review"]
    current = {
        "ready": "Perceive",
        "starting": "Plan",
        "working": "Act",
        "checking": "Check",
        "needs_review": "Review",
        "done": "Review",
        "blocked": "Review",
        "stopped": "Review",
    }.get(status, "Act")
    return {
        "name": "Local agent loop",
        "stage": current,
        "steps": stages,
        "description": {
            "Perceive": "Understand the request and current machine state.",
            "Plan": "Choose the safest work route and boundaries.",
            "Act": "Run the selected local or GLM-backed worker.",
            "Check": "Verify results before asking for acceptance.",
            "Review": "Ask for a human decision only when needed.",
        }[current],
    }


def _readiness_gate(status: str, summary: str, details: dict[str, Any]) -> dict[str, Any]:
    active_run_dir = ""
    payload = details.get("payload")
    if isinstance(payload, dict):
        active_run_dir = _nested_string(payload, ("capabilities", "current_state", "active_run_dir"))
        active_goal = payload.get("active_goal")
        if not active_run_dir and isinstance(active_goal, dict):
            run_dir = active_goal.get("run_dir")
            active_run_dir = run_dir if isinstance(run_dir, str) else ""
    requires_review = status == "needs_review"
    can_start = status not in {"needs_review", "blocked"}
    if requires_review:
        next_action = "Review or continue the current work before starting another task."
    elif status == "blocked":
        next_action = "Resolve the blocker before starting another task."
    elif status in {"working", "checking", "starting"}:
        next_action = "Work is already active. You can check now or move it forward."
    else:
        next_action = "Ready to start a task."
    return {
        "state": status,
        "label": _label_for_status(status),
        "can_start": can_start,
        "requires_review": requires_review,
        "production_ready": status in {"ready", "done"},
        "summary": summary,
        "next_action": next_action,
        "active_run_dir": active_run_dir,
        "guardrails": [
            "One visible task decision at a time.",
            "Review gates must pass before done is trusted.",
            "Raw commands and run paths stay in Advanced details.",
        ],
    }


def _artifacts_from_details(details: dict[str, Any]) -> list[dict[str, str]]:
    payload = details.get("payload")
    if not isinstance(payload, dict):
        return []
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    normalized: list[dict[str, str]] = []
    for artifact in artifacts:
        if isinstance(artifact, dict):
            name = str(artifact.get("name") or artifact.get("path") or "").strip()
            path = str(artifact.get("path") or artifact.get("url") or "").strip()
            if name or path:
                normalized.append({"name": name or path, "path": path or name})
        elif str(artifact).strip():
            value = str(artifact).strip()
            normalized.append({"name": value, "path": value})
    return normalized[:12]


def _json_from_output(output: str) -> dict[str, Any] | None:
    text = output.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _status_from_payload(payload: dict[str, Any] | None, *, fallback_status: str) -> str:
    if payload is None:
        if fallback_status in {"starting", "checking", "ready"}:
            return fallback_status
        return "working"
    if payload.get("active") is False or payload.get("active_goal") is None and "active_goal" in payload:
        return "ready"

    classification = _payload_classification(payload)
    if classification:
        return classification

    active_goal = payload.get("active_goal")
    if isinstance(active_goal, dict):
        if active_goal.get("awaiting_review") is True:
            return "needs_review"
        if active_goal.get("accepted") is True:
            return "done"
        goal_status = _normalize_status(active_goal.get("status"))
        if goal_status:
            return goal_status

    status = _normalize_status(payload.get("status"))
    if status:
        return status

    text = json.dumps(payload, sort_keys=True).lower()
    if any(marker in text for marker in ("needs_review", "awaiting_review", '"review"', "needs review")):
        return "needs_review"
    if any(marker in text for marker in ("blocked", "failed", "error", "operator_intervention_required")):
        return "blocked"
    if any(marker in text for marker in ("stopped", "cancelled", "canceled")):
        return "stopped"
    if any(marker in text for marker in ('"done"', '"complete"', '"completed"')):
        return "done"
    return "working"


def _payload_classification(payload: dict[str, Any]) -> str:
    for value in (
        payload.get("classification"),
        _nested_string(payload, ("capabilities", "current_state", "classification")),
    ):
        status = _normalize_status(value)
        if status:
            return status
    return ""


def _normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"needs_review", "awaiting_review", "review", "review_required"}:
        return "needs_review"
    if normalized in {"accepted", "done", "complete", "completed", "success"}:
        return "done"
    if normalized in {"blocked", "failed", "failure", "error", "operator_intervention_required"}:
        return "blocked"
    if normalized in {"stopped", "cancelled", "canceled"}:
        return "stopped"
    if normalized in {"running", "working", "in_progress", "active"}:
        return "working"
    if normalized in {"checking", "verifying", "reviewing"}:
        return "checking"
    if normalized in {"queued", "starting", "pending"}:
        return "starting"
    if normalized in {"idle", "ready", "free"}:
        return "ready"
    return ""


def _summary_from_payload(
    payload: dict[str, Any] | None,
    stdout: str,
    *,
    fallback_status: str,
) -> str:
    if payload:
        if _payload_is_accepted(payload):
            return "Previous work is accepted. Ready for the next task."
        recommended = _nested_string(payload, ("capabilities", "current_state", "recommended_action"))
        if recommended:
            return _human_summary(_clean_summary(recommended), fallback_status)
        for key in ("summary", "message", "status", "classification"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _human_summary(_clean_summary(value), fallback_status)
        active_goal = payload.get("active_goal")
        if isinstance(active_goal, dict):
            for key in ("objective", "summary", "status"):
                value = active_goal.get(key)
                if isinstance(value, str) and value.strip():
                    return _human_summary(_clean_summary(value), fallback_status)
    if stdout.strip():
        return _human_summary(_clean_summary(stdout), fallback_status)
    return {
        "ready": "No active work is visible yet.",
        "starting": "The work has been sent to the background worker.",
        "checking": "The background worker was asked to move the work forward.",
    }.get(fallback_status, "The work is moving.")


def _payload_is_accepted(payload: dict[str, Any]) -> bool:
    if payload.get("classification") == "accepted":
        return True
    active_goal = payload.get("active_goal")
    if isinstance(active_goal, dict) and active_goal.get("accepted") is True:
        return True
    current_state = payload.get("capabilities")
    if isinstance(current_state, dict):
        state = current_state.get("current_state")
        return isinstance(state, dict) and state.get("classification") == "accepted"
    return False


def _nested_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


def _changed_files_from_payload(payload: dict[str, Any] | None) -> list[str]:
    return _collect_strings(payload, ("changed_files", "files", "modified_files"))


def _verification_from_payload(payload: dict[str, Any] | None) -> list[str]:
    return _collect_strings(payload, ("verification", "checks", "commands"))


def _collect_strings(payload: dict[str, Any] | None, keys: tuple[str, ...]) -> list[str]:
    if not payload:
        return []
    values: list[str] = []
    for key in keys:
        item = payload.get(key)
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
        elif isinstance(item, list):
            values.extend(str(value).strip() for value in item if str(value).strip())
    return values[:12]


def _clean_summary(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return "No detail returned yet."
    summary = " ".join(lines[:4])
    return summary if len(summary) <= 280 else f"{summary[:277]}..."


def _human_summary(summary: str, status: str) -> str:
    normalized = summary.strip().lower().replace("-", "_").replace(" ", "_")
    status_only = normalized in {
        "ready",
        "starting",
        "working",
        "checking",
        "needs_review",
        "done",
        "blocked",
        "stopped",
    }
    if not status_only and not any(term in summary.lower() for term in _TECHNICAL_SUMMARY_TERMS):
        return summary
    return {
        "ready": "The assistant is ready for a new task.",
        "starting": "The task is starting.",
        "working": "The assistant is working on the task.",
        "checking": "The work is being checked.",
        "needs_review": (
            "The work is ready for review. Review it or ask it to continue before "
            "starting another task."
        ),
        "done": "The work is complete and ready for you.",
        "blocked": "The task needs attention. Open Advanced details for technical information.",
        "stopped": "The work has stopped.",
    }.get(status, "The task status was updated.")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def tasks_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    """Return all tasks and current task state."""
    current = status_task(bridge)
    return {
        "tasks": [current],
        "current": current,
    }
