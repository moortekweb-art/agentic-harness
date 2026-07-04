#!/usr/bin/env python3
"""Read-only current-truth adapter for the local Node1 /goal harness.

Hermes worker dispatch only knows jobs it launched through the worker system.
The local Node1 /goal harness runs as a tmux-backed local lane, so controller
status surfaces need a small adapter that polls the supervisor state directly.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE = Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller")
SUPERVISOR = PROFILE / "scripts/local-node1-goal-supervisor.py"
MANAGER = Path("/mnt/raid0/documentation/scripts/local-node1-goal-manager.py")
LOCAL_GOAL = Path("/mnt/raid0/documentation/scripts/local-goal")
INTEGRATION_AUDIT_STATE = PROFILE / "state/local-node1-goal-integration-audit.json"
STATE_PATH = PROFILE / "reports/local-node1-goal-current-truth-latest.json"
REPORT_PATH = PROFILE / "reports/local-node1-goal-current-truth-latest.md"

ALIASES = [
    "agentic harness",
    "local agentic harness",
    "local harness",
    "Node1 /goal",
    "Codex-like goal",
    "local-node1-goal",
]


def mission_count(mission: dict[str, Any], count_key: str, list_key: str) -> int | None:
    """Return a mission count from either the explicit count or list schema."""
    value = mission.get(count_key)
    if isinstance(value, int):
        return value
    entries = mission.get(list_key)
    if isinstance(entries, list):
        return len(entries)
    return None


def mission_applies_to_active_goal(
    active_goal: dict[str, Any], mission: dict[str, Any]
) -> bool:
    """Return whether mission metadata describes the active local goal."""
    if not active_goal:
        return False
    if mission.get("status") == "active" or mission.get("active_subgoal"):
        return True
    active_subgoal = active_goal.get("current_subgoal")
    if active_subgoal:
        return True
    objective = active_goal.get("objective")
    mission_objective = mission.get("objective") or mission.get("umbrella_objective")
    return bool(objective and mission_objective and objective == mission_objective)


def mission_context_note(applies_to_active_goal: bool, mission: dict[str, Any]) -> str:
    if applies_to_active_goal:
        return "mission_context_applies_to_active_goal"
    if mission.get("status"):
        return "mission_context_is_previous_or_stale"
    return "mission_context_unavailable"


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def run_supervisor(*args: str) -> dict[str, Any]:
    cmd = ["python3", str(SUPERVISOR), *args]
    return run_command(cmd)


def run_manager(*args: str) -> dict[str, Any]:
    cmd = ["python3", str(MANAGER), *args]
    return run_command(cmd)


def run_local_goal(*args: str) -> dict[str, Any]:
    cmd = [str(LOCAL_GOAL), *args]
    return run_command(cmd)


def run_command(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        cwd="/mnt/raid0/documentation",
    )
    try:
        payload = json.loads(proc.stdout)
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except Exception as exc:
        payload = {
            "unreadable": True,
            "error": str(exc),
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }
    payload["_command"] = cmd
    payload["_returncode"] = proc.returncode
    return payload


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "path": str(path)}
    except Exception as exc:
        return {
            "available": False,
            "path": str(path),
            "unreadable": True,
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "path": str(path),
            "unreadable": True,
            "error": "JSON root is not an object",
        }
    payload.setdefault("available", True)
    payload.setdefault("path", str(path))
    return payload


def compact_lane_capabilities(capabilities: dict[str, Any]) -> dict[str, Any]:
    lanes = capabilities.get("lanes")
    if not isinstance(lanes, dict):
        return {}
    summary: dict[str, Any] = {}
    for name, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        summary[name] = {
            key: lane.get(key)
            for key in (
                "classification",
                "installed",
                "available_now",
                "availability_reason",
                "unavailable_reason",
                "builder",
                "command",
                "executor",
                "default_executor_worker",
                "executor_workers",
                "planners",
                "runner_path",
                "runner_present",
                "notes",
            )
            if key in lane
        }
    return summary


def compact_integration_audit() -> dict[str, Any]:
    payload = load_json_file(INTEGRATION_AUDIT_STATE)
    if payload.get("available") is not True:
        return payload

    checks = payload.get("checks")
    if not isinstance(checks, list):
        checks = []
    dry_run_intents: list[str] = []
    dry_run_commands: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, dict) or check.get("name") != "dry_run_route_checks":
            continue
        routes = check.get("routes")
        if not isinstance(routes, list):
            continue
        for route in routes:
            if not isinstance(route, dict):
                continue
            intent = route.get("expected_intent")
            if isinstance(intent, str) and intent not in dry_run_intents:
                dry_run_intents.append(intent)
            command = route.get("command")
            if isinstance(intent, str) and isinstance(command, str):
                dry_run_commands[intent] = command

    return {
        "available": True,
        "path": str(INTEGRATION_AUDIT_STATE),
        "generated_at": payload.get("generated_at"),
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "missing": payload.get("missing") or [],
        "dry_run_intents": dry_run_intents,
        "dry_run_commands": dry_run_commands,
        "artifact_paths": payload.get("artifact_paths") or {},
    }


def compact_model_promotion_decision() -> dict[str, Any]:
    payload = run_local_goal("model-promotion-decision", "--json")
    if payload.get("_returncode") != 0 or payload.get("unreadable") is True:
        return {
            "available": False,
            "status": "unavailable",
            "error": payload.get("error") or payload.get("stderr_tail"),
        }
    return {
        "available": True,
        "status": payload.get("status"),
        "mutates_live_service": payload.get("mutates_live_service"),
        "promotion_allowed": payload.get("promotion_allowed"),
        "decision_required": payload.get("decision_required"),
        "operator_can_choose_promotion": payload.get(
            "operator_can_choose_promotion"
        ),
        "compare_verdict": payload.get("compare_verdict"),
        "next_action": payload.get("next_action"),
        "promotion_allowed_meaning": payload.get("promotion_allowed_meaning"),
        "approval_preview_command": payload.get("approval_preview_command"),
        "terminal_approval_command": payload.get("terminal_approval_command"),
    }


def compact_model_status() -> dict[str, Any]:
    payload = run_local_goal("model-status", "--json")
    if payload.get("_returncode") != 0 or payload.get("unreadable") is True:
        return {
            "available": False,
            "status": "unavailable",
            "error": payload.get("error") or payload.get("stderr_tail"),
        }
    current_service = (
        payload.get("current_service")
        if isinstance(payload.get("current_service"), dict)
        else {}
    )
    durability = (
        payload.get("durability")
        if isinstance(payload.get("durability"), dict)
        else {}
    )
    promotion_gate = (
        payload.get("promotion_gate")
        if isinstance(payload.get("promotion_gate"), dict)
        else {}
    )
    return {
        "available": True,
        "ok": payload.get("ok"),
        "canary_mode": payload.get("canary_mode"),
        "current_model_is_candidate": payload.get("current_model_is_candidate"),
        "current_service_live": payload.get("current_service_live"),
        "model_path": current_service.get("model_path"),
        "durability": {
            "status": durability.get("status"),
            "reason": durability.get("reason"),
            "next_command": durability.get("next_command"),
            "durable_dropin_exists": durability.get("durable_dropin_exists"),
            "temporary_dropin_exists": durability.get("temporary_dropin_exists"),
            "durable_dropin_points_to_candidate": durability.get(
                "durable_dropin_points_to_candidate"
            ),
            "temporary_dropin_points_to_candidate": durability.get(
                "temporary_dropin_points_to_candidate"
            ),
        },
        "promotion_gate": {
            "status": promotion_gate.get("status"),
            "reason": promotion_gate.get("reason"),
        },
    }


def acceptance_disposition(status: dict[str, Any]) -> dict[str, Any]:
    """Return bounded acceptance/dirty-disposition fields from manager status."""
    acceptance = status.get("acceptance")
    if not isinstance(acceptance, dict):
        return {}
    disposition_summary = acceptance.get("disposition_summary")
    if not isinstance(disposition_summary, dict):
        disposition_summary = {}
    return {
        "status": acceptance.get("status"),
        "accepted_at": acceptance.get("accepted_at"),
        "active_run_dir": acceptance.get("active_run_dir"),
        "dirty_completion_ok": acceptance.get("dirty_completion_ok"),
        "dirty_disposition_path": acceptance.get("dirty_disposition_path"),
        "dirty_steward_report_path": acceptance.get("dirty_steward_report_path"),
        "remaining_action_required_count": acceptance.get(
            "remaining_action_required_count"
        ),
        "remaining_human_required_count": acceptance.get(
            "remaining_human_required_count"
        ),
        "disposition_summary": {
            key: disposition_summary.get(key)
            for key in (
                "completion_ok",
                "dirty_completion_ok",
                "blocking_count",
                "action_required_count",
                "human_required_count",
                "approval_required_count",
                "pending_safe_action_count",
                "operator_hold_count",
                "external_repo_hold_count",
                "unresolved_count",
                "total_items",
            )
            if key in disposition_summary
        },
    }


def augment_operator_commands(commands: dict[str, Any]) -> dict[str, str]:
    """Return current-truth commands plus high-value wrapper shortcuts."""
    merged: dict[str, str] = {
        str(name): str(command)
        for name, command in commands.items()
        if isinstance(name, str) and isinstance(command, str)
    }
    local_goal = "/mnt/raid0/documentation/scripts/local-goal"
    merged.setdefault("current_truth", f"{local_goal} current-truth")
    merged.setdefault("shortcuts", f"{local_goal} shortcuts")
    merged.setdefault("guide", f"{local_goal} guide")
    merged.setdefault("progress", f"{local_goal} progress")
    merged.setdefault("queue_summary", f"{local_goal} queue-summary")
    merged.setdefault("last_run", f"{local_goal} last-run")
    merged.setdefault(
        "last_goal_changed_files",
        f'{local_goal} ask "what files did the last local goal change?"',
    )
    merged.setdefault(
        "accepted_evidence",
        f'{local_goal} ask "show me the accepted evidence"',
    )
    merged.setdefault(
        "verification_passed",
        f'{local_goal} ask "what verification passed?"',
    )
    merged.setdefault(
        "dirty_acceptance",
        f'{local_goal} ask "does dirty work block acceptance?"',
    )
    merged.setdefault("brief", f"{local_goal} brief")
    merged.setdefault("doctor", f"{local_goal} doctor")
    merged.setdefault("doctor_json", f"{local_goal} doctor --json")
    merged.setdefault("free", f"{local_goal} free")
    merged.setdefault("can_start", f"{local_goal} can-start")
    merged.setdefault("stuck", f"{local_goal} stuck")
    merged.setdefault("next_proof", f"{local_goal} next-proof")
    merged.setdefault("completion_summary", f"{local_goal} completion-summary")
    merged.setdefault("audit_health", f"{local_goal} audit-health")
    merged.setdefault("soak_plan", f"{local_goal} soak-plan")
    merged.setdefault("glm_handoff_plan", f"{local_goal} glm-handoff-plan")
    merged.setdefault("glm_supervisor", f"{local_goal} glm-supervisor status")
    merged.setdefault("ready_review", f"{local_goal} ready-review")
    merged.setdefault("can_accept", f"{local_goal} can-accept")
    merged.setdefault(
        "quick_start_bounded",
        f'{local_goal} quick-start --goal "Describe one bounded task with expected change and verification"',
    )
    merged.setdefault(
        "hermes_start_bounded",
        "/local-goal start local goal: Describe one bounded task with expected change and verification",
    )
    merged.setdefault("model_status", f"{local_goal} model-status")
    merged.setdefault("model_eval_next", f"{local_goal} model-eval-next")
    merged.setdefault("model_promotion_decision", f"{local_goal} model-promotion-decision")
    merged.setdefault("model_promotion_plan", f"{local_goal} model-promotion-plan")
    merged.setdefault(
        "model_promotion_apply_preview", f"{local_goal} model-promotion-apply"
    )
    merged.setdefault("model_promotion_verify", f"{local_goal} model-promotion-verify")
    merged.setdefault(
        "terminal_only_model_promotion_apply_execute",
        f"{local_goal} model-promotion-apply --execute --confirm PROMOTE_ORNITH_PERMANENT",
    )
    merged.setdefault("model_promotion_waiver", f"{local_goal} model-promotion-waiver")
    merged.setdefault("model_decision_packet", f"{local_goal} qwopus-packet")
    merged.setdefault("qwopus_completion_risk", f"{local_goal} qwopus-completion-risk")
    merged.setdefault(
        "qwopus_safe_harness",
        f'{local_goal} ask "is Qwopus safe to use for the harness?"',
    )
    merged.setdefault(
        "qwopus_192k_seq4",
        f'{local_goal} ask "can Qwopus handle 192k seq4?"',
    )
    merged.setdefault("qwopus_window_check", f"{local_goal} qwopus-window-check")
    merged.setdefault("qwopus_window_next", f"{local_goal} qwopus-window-next")
    merged.setdefault(
        "qwopus_window_open_preview",
        f"{local_goal} qwopus-window-open",
    )
    merged.setdefault(
        "qwopus_window_restore_preview",
        f"{local_goal} qwopus-window-restore",
    )
    merged.setdefault("telegram_alias_progress", "/local_goal progress")
    merged.setdefault(
        "telegram_alias_can_accept",
        "/node1-goal can I accept the local goal?",
    )
    merged.setdefault(
        "telegram_alias_model_promotion_apply_preview",
        "/node1_goal model-promotion-apply",
    )
    return merged


def dirty_operator_summary(acceptance: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether dirty-worktree counts still block the accepted run."""
    disposition = acceptance.get("disposition_summary")
    if not isinstance(disposition, dict):
        disposition = {}
    dirty_ok = acceptance.get("dirty_completion_ok")
    if dirty_ok is None:
        dirty_ok = disposition.get("dirty_completion_ok")
    blocking_count = int(disposition.get("blocking_count") or 0)
    action_required = int(disposition.get("action_required_count") or 0)
    human_required = int(disposition.get("human_required_count") or 0)
    approval_required = int(disposition.get("approval_required_count") or 0)
    blocks_acceptance = bool(dirty_ok is not True or blocking_count > 0)
    if blocks_acceptance:
        note = (
            "Dirty disposition still blocks acceptance; review the steward report "
            "before trusting completion."
        )
    elif action_required or human_required or approval_required:
        note = (
            "Dirty counts are non-blocking for this accepted run because the "
            "steward disposition reports dirty_completion_ok=true and "
            "blocking_count=0."
        )
    else:
        note = "No dirty-worktree action remains for this accepted run."
    return {
        "dirty_completion_ok": dirty_ok,
        "blocking_count": blocking_count,
        "action_required_count": action_required,
        "human_required_count": human_required,
        "approval_required_count": approval_required,
        "blocks_acceptance": blocks_acceptance,
        "note": note,
    }


def node1_capacity_summary(
    supervisor_status: dict[str, Any], capabilities: dict[str, Any]
) -> dict[str, Any]:
    """Return explicit local-goal lane and vLLM capacity fields."""
    current_state = (
        capabilities.get("current_state")
        if isinstance(capabilities.get("current_state"), dict)
        else {}
    )
    runtime = (
        supervisor_status.get("runtime")
        if isinstance(supervisor_status.get("runtime"), dict)
        else {}
    )
    vllm = runtime.get("vllm") if isinstance(runtime.get("vllm"), dict) else {}
    local_goal_lane_free = supervisor_status.get("local_goal_lane_free")
    if local_goal_lane_free is None:
        local_goal_lane_free = current_state.get("local_goal_lane_free")
    node1_vllm_idle = supervisor_status.get("node1_vllm_idle")
    if node1_vllm_idle is None:
        node1_vllm_idle = current_state.get("node1_vllm_idle")
    node1_vllm_has_other_activity = supervisor_status.get(
        "node1_vllm_has_other_activity"
    )
    if node1_vllm_has_other_activity is None:
        node1_vllm_has_other_activity = current_state.get(
            "node1_vllm_has_other_activity"
        )
    running = vllm.get("running")
    waiting = vllm.get("waiting")
    if node1_vllm_idle is None and running is not None and waiting is not None:
        try:
            node1_vllm_idle = float(running or 0) <= 0 and float(waiting or 0) <= 0
            node1_vllm_has_other_activity = not node1_vllm_idle
        except (TypeError, ValueError):
            pass
    if running is None:
        running = 0 if node1_vllm_idle is True else 1 if node1_vllm_idle is False else None
    if waiting is None:
        waiting = 0
    start_may_wait = bool(local_goal_lane_free is True and node1_vllm_idle is False)
    if start_may_wait:
        guidance = (
            "Local-goal lane is free, but Node1 vLLM has separate capacity "
            "activity; a new bounded goal may wait."
        )
    elif local_goal_lane_free is True:
        guidance = "Local-goal lane and Node1 vLLM capacity are clear for a bounded goal."
    else:
        guidance = "Local-goal lane is not free; wait or inspect status before starting."
    return {
        "local_goal_lane_free": local_goal_lane_free,
        "node1_lane_idle_legacy": supervisor_status.get("node1_is_idle"),
        "node1_is_idle_scope": supervisor_status.get("node1_is_idle_scope")
        or current_state.get("node1_is_idle_scope")
        or "legacy: local-goal lane availability, not raw vLLM/GPU idleness",
        "node1_vllm_idle": node1_vllm_idle,
        "node1_vllm_has_other_activity": node1_vllm_has_other_activity,
        "vllm_running": running,
        "vllm_waiting": waiting,
        "start_may_wait": start_may_wait,
        "start_guidance": guidance,
    }


def annotate_acceptance_context(
    acceptance: dict[str, Any], active_run_dir: str | None
) -> dict[str, Any]:
    if not acceptance:
        return {}
    annotated = dict(acceptance)
    accepted_run_dir = annotated.get("active_run_dir")
    annotated["applies_to_active_run"] = bool(
        active_run_dir and accepted_run_dir and accepted_run_dir == active_run_dir
    )
    if accepted_run_dir and active_run_dir and accepted_run_dir != active_run_dir:
        annotated["context_note"] = "acceptance_belongs_to_previous_run"
    elif annotated["applies_to_active_run"]:
        annotated["context_note"] = "acceptance_applies_to_active_run"
    else:
        annotated["context_note"] = "acceptance_context_unknown"
    return annotated


def build_status() -> dict[str, Any]:
    generated_at = now()
    supervisor_status = run_supervisor("status", "--json")
    supervisor_capabilities = run_supervisor("capabilities", "--json")
    mission = run_supervisor("mission-show", "--json")
    manager_status = run_manager("status", "--json")
    active_goal = supervisor_status.get("active_goal")
    if not isinstance(active_goal, dict):
        active_goal = {}
    capabilities = supervisor_status.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = supervisor_capabilities
    if not isinstance(capabilities, dict) or capabilities.get("unreadable") is True:
        capabilities = {}
    commands = supervisor_status.get("commands")
    if not isinstance(commands, dict):
        commands = {}
    commands = augment_operator_commands(commands)
    lane_capabilities = compact_lane_capabilities(capabilities)
    node1_capacity = node1_capacity_summary(supervisor_status, capabilities)
    integration_audit_latest = compact_integration_audit()
    model_promotion_decision = compact_model_promotion_decision()
    model_status = compact_model_status()

    tmux_running = active_goal.get("tmux_running")
    if tmux_running is None:
        tmux_running = supervisor_status.get("tmux_running")

    ok = (
        supervisor_status.get("_returncode") == 0
        and supervisor_status.get("unreadable") is not True
    )
    classification = supervisor_status.get("classification")
    running = classification == "working" or bool(tmux_running)
    mission_applies = mission_applies_to_active_goal(active_goal, mission)
    acceptance = annotate_acceptance_context(
        acceptance_disposition(supervisor_status)
        or acceptance_disposition(manager_status),
        active_goal.get("run_dir"),
    )
    dirty_summary = dirty_operator_summary(acceptance)

    return {
        "contract": "local_node1_goal_current_truth.v1",
        "generated_at": generated_at,
        "ok": ok,
        "aliases": ALIASES,
        "lane": "local-node1-goal",
        "classification": classification,
        "phase": supervisor_status.get("phase"),
        "running": running,
        "node1_is_idle": supervisor_status.get("node1_is_idle"),
        "node1_is_idle_scope": node1_capacity.get("node1_is_idle_scope"),
        "local_goal_lane_free": node1_capacity.get("local_goal_lane_free"),
        "node1_vllm_idle": node1_capacity.get("node1_vllm_idle"),
        "node1_vllm_has_other_activity": node1_capacity.get(
            "node1_vllm_has_other_activity"
        ),
        "node1_vllm_running": node1_capacity.get("vllm_running"),
        "node1_vllm_waiting": node1_capacity.get("vllm_waiting"),
        "start_may_wait": node1_capacity.get("start_may_wait"),
        "start_guidance": node1_capacity.get("start_guidance"),
        "node1_capacity": node1_capacity,
        "tmux_running": tmux_running,
        "awaiting_review": active_goal.get("awaiting_review"),
        "accepted": active_goal.get("accepted"),
        "objective": active_goal.get("objective"),
        "run_dir": active_goal.get("run_dir"),
        "prompt_path": active_goal.get("prompt_path"),
        "planner": active_goal.get("planner"),
        "executor": active_goal.get("executor"),
        "current_subgoal": active_goal.get("current_subgoal"),
        "recommended_action": supervisor_status.get("recommended_action"),
        "acceptance": acceptance,
        "dirty_operator_summary": dirty_summary,
        "capabilities": capabilities,
        "commands": commands,
        "lane_capabilities": lane_capabilities,
        "integration_audit_latest": integration_audit_latest,
        "model_status": model_status,
        "model_promotion_decision": model_promotion_decision,
        "queue": supervisor_status.get("queue") or {},
        "mission_applies_to_active_goal": mission_applies,
        "mission_context_note": mission_context_note(mission_applies, mission),
        "mission_status": mission.get("status"),
        "mission_objective": mission.get("objective"),
        "mission_completed_count": mission_count(
            mission, "completed_count", "completed_subgoals"
        ),
        "mission_failed_count": mission_count(mission, "failed_count", "failed_subgoals"),
        "mission_rejected_count": mission_count(
            mission, "rejected_count", "rejected_subgoals"
        ),
        "mission_generated_count": mission.get("generated_count"),
        "mission_max_subgoals": mission.get("max_subgoals"),
        "mission_next_action": mission.get("next_action"),
        "state_source": str(SUPERVISOR),
        "supervisor_status": supervisor_status,
        "supervisor_capabilities": supervisor_capabilities,
        "manager_status": manager_status,
        "mission": mission,
    }


def write_reports(status: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    dirty = status.get("dirty_operator_summary") or {}
    capacity = (
        status.get("node1_capacity")
        if isinstance(status.get("node1_capacity"), dict)
        else {}
    )
    model_status = status.get("model_status") if isinstance(status.get("model_status"), dict) else {}
    durability = (
        model_status.get("durability")
        if isinstance(model_status.get("durability"), dict)
        else {}
    )
    promotion_gate = (
        model_status.get("promotion_gate")
        if isinstance(model_status.get("promotion_gate"), dict)
        else {}
    )
    lines = [
        "# Local Node1 Goal Current Truth",
        "",
        f"- Generated: `{status['generated_at']}`",
        f"- OK: `{status['ok']}`",
        f"- Lane: `{status['lane']}`",
        f"- Aliases: `{', '.join(status['aliases'])}`",
        f"- Classification: `{status.get('classification')}`",
        f"- Running: `{status.get('running')}`",
        f"- Node1 lane free: `{status.get('local_goal_lane_free')}`",
        f"- Node1 vLLM idle: `{status.get('node1_vllm_idle')}`, other activity `{status.get('node1_vllm_has_other_activity')}`, running `{status.get('node1_vllm_running') if status.get('node1_vllm_running') is not None else capacity.get('vllm_running')}`, waiting `{status.get('node1_vllm_waiting') if status.get('node1_vllm_waiting') is not None else capacity.get('vllm_waiting')}`",
        f"- Start may wait: `{status.get('start_may_wait')}`",
        f"- Start guidance: {status.get('start_guidance') or capacity.get('start_guidance') or 'none'}",
        f"- Node1 idle legacy: `{status.get('node1_is_idle')}` ({status.get('node1_is_idle_scope') or capacity.get('node1_is_idle_scope') or 'legacy lane availability'})",
        f"- tmux running: `{status.get('tmux_running')}`",
        f"- Awaiting review: `{status.get('awaiting_review')}`",
        f"- Accepted: `{status.get('accepted')}`",
        f"- Acceptance status: `{(status.get('acceptance') or {}).get('status')}`",
        f"- Acceptance applies to active run: `{(status.get('acceptance') or {}).get('applies_to_active_run')}`",
        f"- Acceptance context: `{(status.get('acceptance') or {}).get('context_note')}`",
        f"- Dirty completion OK: `{dirty.get('dirty_completion_ok')}`",
        f"- Dirty blocks acceptance: `{dirty.get('blocks_acceptance')}`",
        f"- Dirty action required: `{dirty.get('action_required_count')}`",
        f"- Dirty human required: `{dirty.get('human_required_count')}`",
        f"- Dirty approval required: `{dirty.get('approval_required_count')}`",
        f"- Dirty blocking count: `{dirty.get('blocking_count')}`",
        f"- Dirty operator note: {dirty.get('note') or 'none'}",
        f"- Objective: {status.get('objective') or 'none'}",
        f"- Run dir: `{status.get('run_dir') or 'none'}`",
        f"- Recommended action: {status.get('recommended_action') or 'none'}",
        f"- Mission applies to active goal: `{status.get('mission_applies_to_active_goal')}`",
        f"- Mission context note: `{status.get('mission_context_note')}`",
        f"- Mission status: `{status.get('mission_status')}`",
        f"- Mission progress: completed `{status.get('mission_completed_count')}` / max `{status.get('mission_max_subgoals')}`, failed `{status.get('mission_failed_count')}`, rejected `{status.get('mission_rejected_count')}`",
        f"- Mission next action: {status.get('mission_next_action') or 'none'}",
        f"- Integration audit: ok `{(status.get('integration_audit_latest') or {}).get('ok')}`, status `{(status.get('integration_audit_latest') or {}).get('status')}`, missing `{', '.join((status.get('integration_audit_latest') or {}).get('missing') or []) or 'none'}`",
        f"- Model status: available `{model_status.get('available')}`, canary mode `{model_status.get('canary_mode')}`, current model is candidate `{model_status.get('current_model_is_candidate')}`",
        f"- Model durability: status `{durability.get('status')}`, reason `{durability.get('reason') or 'none'}`",
        f"- Model durability next command: `{durability.get('next_command') or 'none'}`",
        f"- Model promotion gate: `{promotion_gate.get('status') or 'unknown'}`",
        f"- Model promotion decision: status `{(status.get('model_promotion_decision') or {}).get('status')}`, operator can choose promotion `{(status.get('model_promotion_decision') or {}).get('operator_can_choose_promotion')}`",
        f"- Model promotion meaning: {(status.get('model_promotion_decision') or {}).get('promotion_allowed_meaning') or 'none'}",
        f"- Model promotion terminal-only mutation command: `{(status.get('model_promotion_decision') or {}).get('terminal_approval_command') or 'none'}`",
        "",
        "## Commands",
        "",
    ]
    commands = status.get("commands")
    if isinstance(commands, dict) and commands:
        for name in sorted(commands):
            lines.append(f"- {name}: `{commands[name]}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Lane Capabilities",
            "",
        ]
    )
    lanes = status.get("lane_capabilities")
    if isinstance(lanes, dict) and lanes:
        for name in sorted(lanes):
            lane = lanes[name]
            if not isinstance(lane, dict):
                continue
            lines.append(
                "- "
                f"{name}: classification `{lane.get('classification')}`, "
                f"installed `{lane.get('installed')}`, "
                f"available_now `{lane.get('available_now')}`, "
                f"reason `{lane.get('availability_reason') or lane.get('unavailable_reason') or 'none'}`"
            )
    else:
        lines.append("- none")

    audit = status.get("integration_audit_latest")
    lines.extend(
        [
            "",
            "## Integration Audit",
            "",
            f"- Available: `{(audit or {}).get('available')}`",
            f"- Path: `{(audit or {}).get('path') or INTEGRATION_AUDIT_STATE}`",
            f"- Dry-run intents: `{', '.join((audit or {}).get('dry_run_intents') or []) or 'none'}`",
            "",
        ]
    )

    lines.extend(
        [
        "## Source",
        "",
        f"- Supervisor: `{status['state_source']}`",
        f"- JSON: `{STATE_PATH}`",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    status = build_status()
    write_reports(status)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
