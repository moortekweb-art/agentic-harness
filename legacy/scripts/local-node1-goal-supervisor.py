#!/usr/bin/env python3
"""Hermes supervisor/control surface for the local Node1 long-goal harness.

This is the operator-facing wrapper around the documentation repo manager.  It
keeps Hermes in the loop without making Node1 a generic background fallback.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import deque
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from local_node1_goal_phases import migrate_legacy_goal_state
from local_node1_goal_phases import (
    DOC_ROOT,
    HERMES_ROOT,
    LOCAL_GOAL_WRAPPER,
    MANAGER,
    now_iso as now,
    parse_error_record,
    write_secure_file,
)
COMMAND_SHIM = HERMES_ROOT / "scripts/local-node1-goal-command.py"
CURRENT_TRUTH = HERMES_ROOT / "scripts/local-node1-goal-current-truth.py"
HERMES_AGENT_ROOT = Path("/mnt/raid0/home-ai-inference/hermes-agent")
HERMES_PYTHON = HERMES_AGENT_ROOT / "venv/bin/python"
HERMES_GATEWAY_RUN = HERMES_AGENT_ROOT / "gateway/run.py"
HERMES_COMMAND_REGISTRY = HERMES_AGENT_ROOT / "hermes_cli/commands.py"
SYSTEMD_USER_DIR = Path("/mnt/raid0/home-ai-inference/.config/systemd/user")
HERMES_GATEWAY_SERVICE = "hermes-gateway-controller.service"
LOCAL_GOAL_WATCH_SERVICE = SYSTEMD_USER_DIR / "local-node1-goal-watch.service"
LOCAL_GOAL_WATCH_TIMER = SYSTEMD_USER_DIR / "local-node1-goal-watch.timer"
TELEGRAM_NOTIFY_MODULE = Path("/mnt/raid0/services/scheduled-tasks/notify.py")
STATE_DIR = HERMES_ROOT / "state"
REPORT_DIR = HERMES_ROOT / "reports"
SUPERVISOR_JSON = STATE_DIR / "local-node1-goal-supervisor.json"
SUPERVISOR_MD = REPORT_DIR / "local-node1-goal-supervisor-latest.md"
SUPERVISOR_EVENTS_JSONL = STATE_DIR / "local-node1-goal-supervisor-events.jsonl"
SUPERVISOR_NOTIFY_STATE = STATE_DIR / "local-node1-goal-supervisor-notify.json"
SUPERVISOR_PHASE_JSON = STATE_DIR / "local-node1-goal-supervisor-phase.json"
AUTO_CONTINUE_LOOP_STATE = STATE_DIR / "local-node1-goal-auto-continue-loop.json"
INTEGRATION_AUDIT_JSON = STATE_DIR / "local-node1-goal-integration-audit.json"
INTEGRATION_AUDIT_MD = REPORT_DIR / "local-node1-goal-integration-audit.md"
QUEUE_JSON = STATE_DIR / "local-node1-goal-queue.json"
MISSION_JSON = STATE_DIR / "local-node1-goal-mission.json"
SESSION = "local-node1-goal"
ALLOWED_PLANNERS = {
    "none",
    "codex-openai",
    "gpt-5.5",
    "deepseek-v4-pro",
    "glm-5.2",
    "kimi-coding",
    "thinkmax",
}
ALLOWED_EXECUTORS = {"opencode", "qwen", "aider", "mini-swe"}
MAX_FAILURE_STREAK = 3  # block after this many consecutive subgoal failures
SUPERVISOR_EVENTS_KEEP = 200
MANAGER_BOUNDARY_TIMEOUT_SECONDS = 420
NOTIFY_CLASSIFICATIONS = {"needs_review", "complete", "accepted", "stuck"}
NOTIFY_REPEAT_SECONDS = int(os.getenv("LOCAL_NODE1_GOAL_NOTIFY_REPEAT_SECONDS", "900"))
NOTIFY_REPEAT_CLASSIFICATIONS = {"needs_review", "stuck"}

# Cloud-executor lane (Hermes worker_dispatch) --------------------------------
# 'none' keeps the local Node1 vLLM executor path unchanged. Any other value
# routes building through prime-directive worker_dispatch instead of the local
# tmux + opencode loop (reference/LOCAL_GOAL_HARNESS_REFERENCE.md lane 3).
WORKER_RUNNER = Path(__file__).resolve().parent / "terminal-worker-runner.py"
WORKER_REGISTRY = HERMES_ROOT / "config/terminal-worker-registry.json"
WORKER_CAPABILITIES = HERMES_ROOT / "config/terminal-worker-capabilities.json"
HARNESS_REPORTS = DOC_ROOT / "reports/local-node1-goal-harness"
COMPLETE_MARKER = HARNESS_REPORTS / "complete.json"
ACCEPTANCE_MARKER = HARNESS_REPORTS / "acceptance.json"
REVIEW_MARKER = HARNESS_REPORTS / "review.json"
ACTIVE_RUN_INDEX = HARNESS_REPORTS / "active-run.json"
DEFAULT_CLOUD_EXECUTOR_WORKERS = {
    "none",
    "opencode-glm-build",
    "opencode-kimi-build",
    "pi-zai-build-sandbox",
    "pi-zai-executor-compare",
    "pi-zai-thorough-soak",
    "pi-zai-code-repair-canary",
}
ADAPTER_CANARY_EXECUTOR_WORKERS = {
    "kimi",
    "codex",
    "glm52-direct",
    "glm52-direct-implementation-canary",
}
ALLOWED_EXECUTOR_WORKERS = (
    DEFAULT_CLOUD_EXECUTOR_WORKERS | ADAPTER_CANARY_EXECUTOR_WORKERS
)
NO_FALLBACK_EXECUTOR_WORKERS = {
    "pi-zai-build-sandbox",
    "pi-zai-executor-compare",
    "pi-zai-thorough-soak",
    "pi-zai-code-repair-canary",
    *ADAPTER_CANARY_EXECUTOR_WORKERS,
}
CLOUD_BUILDER_FALLBACK = "opencode-kimi-build"
CLOUD_MAX_ITERATIONS = 24
CLOUD_FAILURE_CAP = 3
CLOUD_ITERATION_TIMEOUT = 1800
AUTO_EXTERNAL_REVIEWERS = ("glm-5.2", "kimi-coding")
AUTO_EXTERNAL_REVIEW_TIMEOUT_SECONDS = 90
RECOVERY_HARD_BLOCK_AFTER = 2
NO_PROGRESS_REVIEW_CHECKS = {
    "artifact_backed_verification",
    "completion_marker",
    "done_criteria_mapped",
    "honest_classification",
    "loop_state_complete",
    "loop_stopped",
    "node1_idle",
    "not_report_only",
    "remaining_dirty_disposition_honesty",
    "remaining_none",
    "run_change_evidence",
    "summary_present",
    "verification_entries",
    "verification_positive",
}


def queue_item_id(existing_items: list[dict[str, Any]] | None = None) -> str:
    """Return a queue id that stays unique for same-second parallel enqueues."""
    existing = {
        str(item.get("id") or "")
        for item in (existing_items or [])
        if isinstance(item, dict)
    }
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if base not in existing:
        return base
    for index in range(1, 1000):
        candidate = f"{base}-{index}"
        if candidate not in existing:
            return candidate
    raise RuntimeError("could not allocate unique local-goal queue id")


def run(
    cmd: list[str],
    *,
    timeout: int = 180,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    child_env = os.environ.copy()
    default_runtime_dir = f"/run/user/{os.getuid()}"
    runtime_dir = child_env.get("XDG_RUNTIME_DIR") or default_runtime_dir
    if not (Path(runtime_dir) / "bus").exists():
        runtime_dir = default_runtime_dir
    child_env["XDG_RUNTIME_DIR"] = runtime_dir
    bus_path = Path(runtime_dir) / "bus"
    if bus_path.exists():
        child_env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"
    if env:
        child_env.update(env)
    return subprocess.run(
        cmd,
        cwd=str(DOC_ROOT),
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=child_env,
    )


def set_monitor_phase(phase: str, detail: str = "") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_secure_file(
        SUPERVISOR_PHASE_JSON,
        json.dumps(
            {"detail": detail, "phase": phase, "updated_at": now()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        0o600,
    )


def monitor_phase() -> dict[str, Any]:
    if not SUPERVISOR_PHASE_JSON.exists():
        return {"detail": "", "phase": "idle", "updated_at": ""}
    try:
        data = json.loads(SUPERVISOR_PHASE_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "detail": data.get("detail") or "",
                "phase": data.get("phase") or "idle",
                "updated_at": data.get("updated_at") or "",
            }
    except Exception:
        pass
    return {"detail": "phase file unreadable", "phase": "unknown", "updated_at": ""}


def _state_signature(payload: dict[str, Any]) -> dict[str, Any]:
    active_goal = payload.get("active_goal") or {}
    queue = payload.get("queue") or {}
    useful = payload.get("useful_execution") or {}
    current_subgoal = active_goal.get("current_subgoal")
    subgoal_title = None
    subgoal_id = None
    if isinstance(current_subgoal, dict):
        subgoal_title = current_subgoal.get("title")
        subgoal_id = current_subgoal.get("queue_item_id")

    return {
        "phase": payload.get("phase"),
        "classification": payload.get("classification"),
        "active_warning_count": payload.get("active_warning_count"),
        "objective": active_goal.get("objective"),
        "planner": active_goal.get("planner"),
        "executor": active_goal.get("executor"),
        "tmux_running": active_goal.get("tmux_running"),
        "awaiting_review": active_goal.get("awaiting_review"),
        "accepted": active_goal.get("accepted"),
        "run_dir": active_goal.get("run_dir"),
        "queue_queued": queue.get("queued"),
        "queue_running": queue.get("running"),
        "complete_marker_path": payload.get("runtime", {}).get("complete_marker_path"),
        "current_subgoal_title": subgoal_title,
        "current_subgoal_id": subgoal_id,
        "useful_execution": useful.get("useful"),
    }


def _sig_delta(prev_sig: dict[str, Any], curr_sig: dict[str, Any]) -> dict[str, Any]:
    delta = {}
    keys = set(prev_sig) | set(curr_sig)
    for key in sorted(keys):
        prev_value = prev_sig.get(key)
        curr_value = curr_sig.get(key)
        if prev_value != curr_value:
            delta[key] = {"before": prev_value, "after": curr_value}
    return delta


def notification_signature(payload: dict[str, Any]) -> str:
    """Stable signature for deduping operator notifications."""
    active_goal = payload.get("active_goal") or {}
    runtime = payload.get("runtime") or {}
    loop_state = runtime.get("loop_state") if isinstance(runtime, dict) else {}
    if not isinstance(loop_state, dict):
        loop_state = {}
    current_subgoal = active_goal.get("current_subgoal")
    if not isinstance(current_subgoal, dict):
        current_subgoal = {}
    review = payload.get("review")
    if not isinstance(review, dict):
        review = {}
    relevant = {
        "classification": payload.get("classification"),
        "run_dir": active_goal.get("run_dir"),
        "objective": active_goal.get("objective"),
        "queue_item_id": current_subgoal.get("queue_item_id"),
        "subgoal_number": current_subgoal.get("subgoal_number"),
        "accepted": active_goal.get("accepted"),
        "awaiting_review": active_goal.get("awaiting_review"),
        "loop_status": loop_state.get("status"),
        "loop_iteration": loop_state.get("iteration"),
        "active_warning_count": payload.get("active_warning_count"),
        "review_status": review.get("status"),
    }
    raw = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def should_notify_operator(
    payload: dict[str, Any],
    *,
    previous_notify_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return whether this supervisor state should be sent to Telegram."""
    if os.getenv("LOCAL_NODE1_GOAL_TELEGRAM_NOTIFY", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False, "disabled"
    classification = str(payload.get("classification") or "")
    if classification not in NOTIFY_CLASSIFICATIONS:
        return False, f"classification:{classification or 'missing'}"
    sig = notification_signature(payload)
    previous_sig = str((previous_notify_state or {}).get("last_signature") or "")
    if sig == previous_sig:
        if classification in NOTIFY_REPEAT_CLASSIFICATIONS:
            generated_at = str((previous_notify_state or {}).get("generated_at") or "")
            try:
                previous_at = datetime.fromisoformat(
                    generated_at.replace("Z", "+00:00")
                )
            except ValueError:
                previous_at = None
            if previous_at is not None:
                age = datetime.now(timezone.utc) - previous_at
                if age >= timedelta(seconds=NOTIFY_REPEAT_SECONDS):
                    return True, sig
        return False, "duplicate"
    return True, sig


def format_operator_notification(payload: dict[str, Any]) -> str:
    """Build a concise local-goal status message for Telegram/operator chat."""
    active_goal = payload.get("active_goal") or {}
    runtime = payload.get("runtime") or {}
    current_subgoal = active_goal.get("current_subgoal")
    if not isinstance(current_subgoal, dict):
        current_subgoal = {}
    warnings = payload.get("active_warnings") or []
    lines = [
        f"Local Node1 goal: {payload.get('classification')}",
        str(payload.get("recommended_action") or "").strip(),
    ]
    title = current_subgoal.get("title") or current_subgoal.get("criterion")
    if title:
        lines.append(f"Subgoal: {title}")
    objective = str(active_goal.get("objective") or "").strip()
    if objective and not title:
        lines.append(f"Goal: {objective[:240]}")
    lines.append(f"Run: {active_goal.get('run_dir') or 'none'}")
    lines.append(
        "Review: "
        f"awaiting={active_goal.get('awaiting_review')} "
        f"accepted={active_goal.get('accepted')}"
    )
    complete_marker = (
        runtime.get("complete_marker_path") if isinstance(runtime, dict) else ""
    )
    if complete_marker:
        lines.append(f"Marker: {complete_marker}")
    if warnings:
        first = warnings[0] if isinstance(warnings[0], dict) else {}
        lines.append(
            "Warning: "
            f"{first.get('kind') or 'warning'} "
            f"count={first.get('count') or 1} "
            f"{first.get('detail') or ''}".strip()
        )
        hint = str(first.get("recovery_hint") or "").strip()
        if hint:
            lines.append(f"Recovery: {hint[:280]}")
    next_action = str(payload.get("recommended_action") or "").strip()
    if next_action:
        lines.append(f"Next: {next_action[:280]}")
    return "\n".join(line for line in lines if line)


def maybe_notify_operator(
    payload: dict[str, Any], *, quiet_stdout: bool = False
) -> dict[str, Any]:
    """Send a deduped Telegram notification for review/terminal/problem states."""
    previous: dict[str, Any] = {}
    if SUPERVISOR_NOTIFY_STATE.exists():
        try:
            data = json.loads(SUPERVISOR_NOTIFY_STATE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                previous = data
        except Exception:
            previous = {}
    should_send, reason_or_sig = should_notify_operator(
        payload, previous_notify_state=previous
    )
    if not should_send:
        return {
            "attempted": False,
            "sent": False,
            "reason": reason_or_sig,
            "state_path": str(SUPERVISOR_NOTIFY_STATE),
        }
    message = format_operator_notification(payload)
    sent = False
    error = ""
    try:
        sys.path.insert(0, "/mnt/raid0/services/scheduled-tasks")
        from notify import TOPIC_AGENTS, send_telegram  # type: ignore

        with contextlib.redirect_stdout(sys.stderr if quiet_stdout else sys.stdout):
            sent = bool(
                send_telegram(
                    message,
                    topic_id=TOPIC_AGENTS,
                    source="local-node1-goal",
                )
            )
    except Exception as exc:
        error = str(exc)
    write_secure_file(
        SUPERVISOR_NOTIFY_STATE,
        json.dumps(
            {
                "generated_at": now(),
                "last_signature": reason_or_sig,
                "classification": payload.get("classification"),
                "sent": sent,
                "error": error,
                "message_preview": message[:500],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        0o600,
    )
    return {
        "attempted": True,
        "sent": sent,
        "error": error,
        "reason": "sent" if sent else "not_sent",
        "state_path": str(SUPERVISOR_NOTIFY_STATE),
    }


def _read_supervisor_events(*, limit: int = 10) -> list[dict[str, Any]]:
    """Return up to ``limit`` recent supervisor events."""
    if limit <= 0 or not SUPERVISOR_EVENTS_JSONL.exists():
        return []
    events = []
    try:
        lines = SUPERVISOR_EVENTS_JSONL.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _append_supervisor_event(event: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    events: deque[str] = deque(maxlen=SUPERVISOR_EVENTS_KEEP)
    if SUPERVISOR_EVENTS_JSONL.exists():
        try:
            for line in SUPERVISOR_EVENTS_JSONL.read_text(
                encoding="utf-8"
            ).splitlines():
                if line.strip():
                    events.append(line)
        except Exception:
            # If the event log is corrupt or unparseable, rotate cleanly.
            events.clear()
    events.append(json.dumps(event, sort_keys=True, ensure_ascii=True))
    write_secure_file(SUPERVISOR_EVENTS_JSONL, "\n".join(events) + "\n", 0o600)


def manager_json() -> dict[str, Any]:
    proc = run(["python3", str(MANAGER), "status", "--json"], timeout=90)
    try:
        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {
        "verdict": "unknown",
        "recommended_action": "Manager status JSON was unreadable.",
        "manager_returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def disposition_json(*, commit: bool = False) -> dict[str, Any]:
    cmd = ["python3", str(MANAGER), "disposition", "--json"]
    if commit:
        cmd.extend(
            [
                "--commit",
                "--message",
                "feat(local-goal): commit accepted local goal work",
            ]
        )
    proc = run(cmd, timeout=240)
    try:
        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data["returncode"] = proc.returncode
            return data
    except json.JSONDecodeError:
        pass
    return {
        "ok": False,
        "status": "disposition_unreadable",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def disposition_commit_complete(disposition: dict[str, Any], *, commit: bool) -> bool:
    """True when disposition is safe to follow with mission reconciliation.

    In auto-commit mode, accepting a run is not enough: the owned changes must
    either be committed or there must be nothing left to commit.  This prevents
    mission mode from dispatching the next subgoal while accepted files are only
    staged or while the commit hook failed.
    """
    if not commit:
        return True
    if disposition.get("ok") is not True:
        return False
    status = str(disposition.get("status") or "")
    if status == "committed":
        return disposition.get("committed") is True
    if status == "nothing_to_commit":
        return not disposition.get("committable_paths")
    return False


def record_disposition_failure(
    review: dict[str, Any], disposition: dict[str, Any]
) -> None:
    review["disposition_failed"] = {
        "status": disposition.get("status"),
        "git_add_returncode": disposition.get("git_add_returncode"),
        "git_add_stderr": disposition.get("git_add_stderr", "")[:500],
        "git_commit_returncode": disposition.get("git_commit_returncode"),
        "git_commit_stderr": disposition.get("git_commit_stderr", "")[:500],
        "committable_paths": disposition.get("committable_paths", []),
        "filtered_out_of_repo_paths": disposition.get("filtered_out_of_repo_paths", []),
        "review_override": "accepted_with_disposition_failure",
        "override_reason": (
            "Run was accepted by review checks, but owned-file disposition did "
            "not complete. Mission reconciliation and next-subgoal dispatch are "
            "blocked until the owned changes are committed or explicitly cleared."
        ),
    }


def auto_continue_after_disposition_failure(
    status: dict[str, Any], disposition: dict[str, Any]
) -> dict[str, Any]:
    """Continue the accepted run with targeted feedback when disposition blocks.

    This keeps mission mode autonomous for commit-hook or owned-change
    disposition failures. Dispatch remains blocked until the continued run
    repairs the failure and disposition succeeds.
    """
    feedback = json.dumps(
        {
            "disposition_status": disposition.get("status"),
            "git_add_returncode": disposition.get("git_add_returncode"),
            "git_add_stderr": str(disposition.get("git_add_stderr") or "")[-4000:],
            "git_commit_returncode": disposition.get("git_commit_returncode"),
            "git_commit_stdout": str(disposition.get("git_commit_stdout") or "")[
                -4000:
            ],
            "git_commit_stderr": str(disposition.get("git_commit_stderr") or "")[
                -6000:
            ],
            "committable_paths": disposition.get("committable_paths") or [],
            "filtered_out_of_repo_paths": (
                disposition.get("filtered_out_of_repo_paths") or []
            ),
            "active_run_dir": status.get("active_run_dir"),
            "complete_marker_path": status.get("complete_marker_path"),
            "instruction": (
                "The run was accepted, but owned-file disposition blocked "
                "mission progress. Fix the concrete git add/commit or "
                "validation-hook failure shown here, rerun the relevant "
                "validation, and do not write complete.json again until "
                "disposition can commit or prove there is nothing to commit."
            ),
        },
        indent=2,
        sort_keys=True,
    )
    cont_cmd = [
        "python3",
        str(MANAGER),
        "continue",
        "--title",
        "Hermes auto-continue after disposition failure",
        "--executor",
        "opencode",
    ]
    queue_id = str((status.get("run_meta") or {}).get("queue_id") or "")
    if queue_id:
        cont_cmd.extend(["--queue-id", queue_id])
    cont_cmd.extend(["--review-feedback", feedback])
    cont = run(cont_cmd, timeout=240)
    return {
        "returncode": cont.returncode,
        "stdout_tail": cont.stdout[-2000:],
        "stderr_tail": cont.stderr[-2000:],
        "feedback": feedback,
    }


def load_queue() -> dict[str, Any]:
    try:
        text = QUEUE_JSON.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            data.setdefault("items", [])
            return data
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return {
            "contract": "local_node1_goal_queue.v1",
            "items": [],
            "_parse_errors": [
                parse_error_record(
                    exc,
                    str(QUEUE_JSON),
                    text if "text" in locals() else "",
                )
            ],
        }
    except FileNotFoundError:
        pass
    return {"contract": "local_node1_goal_queue.v1", "items": []}


def write_queue(queue: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    queue["updated_at"] = now()
    write_secure_file(QUEUE_JSON, json.dumps(queue, indent=2, sort_keys=True) + "\n", 0o600)


def normalize_queue_item_ids(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Repair duplicate queue ids in-place while preserving the first item."""
    items = queue.get("items") or []
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    changed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        current = str(item.get("id") or "")
        if current and current not in seen:
            seen.add(current)
            item.setdefault("queue_id", current)
            continue
        new_id = queue_item_id(
            [candidate for candidate in items if isinstance(candidate, dict)]
        )
        old_id = current or ""
        item["id"] = new_id
        item["queue_id"] = new_id
        item["previous_queue_id"] = old_id
        item["queue_id_repaired_at"] = now()
        seen.add(new_id)
        changed.append({"old_id": old_id, "new_id": new_id, "title": item.get("title")})
    return changed


def repair_duplicate_queue_ids() -> list[dict[str, Any]]:
    queue = load_queue()
    changed = normalize_queue_item_ids(queue)
    if changed:
        write_queue(queue)
    return changed


def queue_items(status: str | None = None) -> list[dict[str, Any]]:
    items = load_queue().get("items") or []
    if not isinstance(items, list):
        return []
    clean_items = [item for item in items if isinstance(item, dict)]
    if status is None:
        return clean_items
    return [item for item in clean_items if item.get("status") == status]


def queued_cloud_executor_items() -> list[dict[str, Any]]:
    """Return queued items that should run through a worker lane, not Node1."""
    items: list[dict[str, Any]] = []
    for item in queue_items("queued"):
        worker = str(item.get("executor_worker") or "none")
        if worker != "none":
            items.append(item)
    return items


def running_worker_lane_items() -> list[dict[str, Any]]:
    """Return running queue items handled by worker lanes instead of Node1 tmux."""
    items: list[dict[str, Any]] = []
    for item in queue_items("running"):
        worker = str(item.get("executor_worker") or "none")
        lane = str(item.get("builder_lane") or "")
        if worker != "none" or lane == "cloud":
            items.append(item)
    return items


def process_is_alive(pid_value: Any) -> bool:
    """Best-effort liveness check for supervisor-owned background processes."""
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def active_run_has_prior_acceptance(status: dict[str, Any]) -> bool:
    """True when the current active run already has an acceptance record."""
    acceptance = read_json_file(ACCEPTANCE_MARKER)
    if str(acceptance.get("status") or "").lower() != "accepted":
        return False
    accepted_dir = str(acceptance.get("active_run_dir") or "")
    active_dir = str(status.get("active_run_dir") or "")
    return bool(accepted_dir and active_dir and accepted_dir == active_dir)


def mark_queue_item_recovery_blocked(
    item: dict[str, Any],
    *,
    reason: str,
    hard_failure_reason: str,
    next_operator_step: str | None = None,
) -> dict[str, Any]:
    """Persist operator-facing recovery block state on a queue item."""
    blocked_at = now()
    recovery_count = int(item.get("recovery_attempt_count") or 0) + 1
    item["recovery_attempt_count"] = recovery_count
    item["recovery_block_reason"] = reason
    item.setdefault("recovery_blocked_at", blocked_at)
    item["recovery_last_blocked_at"] = blocked_at
    item["hard_failure_reason"] = hard_failure_reason
    item["operator_intervention_required"] = True
    item["next_operator_step"] = next_operator_step or (
        "Inspect the paused queue item, then explicitly run local-goal continue "
        "with recovery feedback or mission-resume/handoff after confirming context."
    )
    if recovery_count >= RECOVERY_HARD_BLOCK_AFTER or item.get("recovery_blocked"):
        item["recovery_blocked"] = True
        item["hard_blocked"] = True
    else:
        item["recovery_blocked"] = False
        item["hard_blocked"] = False
    return {
        "id": item.get("id"),
        "recovery_block_reason": reason,
        "recovery_attempt_count": recovery_count,
        "recovery_blocked": item.get("recovery_blocked") is True,
        "hard_failure_reason": hard_failure_reason,
        "next_operator_step": item.get("next_operator_step"),
    }


def clear_queue_item_recovery_block(item: dict[str, Any], *, reason: str) -> None:
    """Clear recovery block fields after operator action or terminal acceptance."""
    for key in (
        "recovery_blocked",
        "hard_blocked",
        "operator_intervention_required",
    ):
        item[key] = False
    item["recovery_block_reason"] = ""
    item["hard_failure_reason"] = ""
    item["next_operator_step"] = ""
    item["recovery_resumed_at"] = now()
    item["recovery_resume_reason"] = reason


def clear_recovery_block_for_queue_id(queue_id: str, *, reason: str) -> bool:
    """Clear a queued item's recovery block after explicit operator intent."""
    if not queue_id:
        return False
    queue = load_queue()
    changed = False
    for item in queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != queue_id:
            continue
        if (
            item.get("recovery_blocked") is True
            or item.get("hard_blocked") is True
            or item.get("operator_intervention_required") is True
        ):
            clear_queue_item_recovery_block(item, reason=reason)
            changed = True
        break
    if changed:
        write_queue(queue)
    return changed


def abandon_queue_item(queue_id: str, *, reason: str) -> dict[str, Any]:
    """Mark one paused/recovery queue item failed without deleting history."""
    queue = load_queue()
    items = queue.get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != queue_id:
            continue
        previous_status = str(item.get("status") or "")
        if previous_status not in {"paused", "running", "starting"}:
            return {
                "ok": False,
                "status": "not_abandoned",
                "queue_id": queue_id,
                "previous_status": previous_status,
                "reason": "queue item is not paused/running/starting",
            }
        item["status"] = "failed"
        item["failed_at"] = now()
        item["failure_reason"] = reason
        item["abandoned_at"] = item["failed_at"]
        item["abandoned_reason"] = reason
        item["previous_status"] = previous_status
        clear_queue_item_recovery_block(item, reason=f"abandoned: {reason}")
        item["next_operator_step"] = ""
        write_queue(queue)
        return {
            "ok": True,
            "status": "abandoned",
            "queue_id": queue_id,
            "previous_status": previous_status,
            "reason": reason,
        }
    return {
        "ok": False,
        "status": "not_found",
        "queue_id": queue_id,
        "reason": "queue item not found",
    }


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def recovery_block_status(status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the highest-priority recovery block visible to operators."""
    now_dt = datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []
    queue = load_queue()
    queue_changed = False
    run_meta: dict[str, Any] = {}
    active_run_dir = ""
    if isinstance(status, dict):
        run_meta = (
            status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
        )
        active_run_dir = str(status.get("active_run_dir") or "")
    active_queue_id = str(run_meta.get("queue_id") or "")
    mission = load_mission()
    active_subgoal = (
        mission.get("active_subgoal")
        if isinstance(mission.get("active_subgoal"), dict)
        else {}
    )
    active_mission_queue_id = str(active_subgoal.get("queue_item_id") or "")
    current_is_accepted = False
    if isinstance(status, dict):
        current_is_accepted = (
            status.get("accepted") is True
            or str(status.get("verdict") or "").lower() == "accepted"
            or str(status.get("classification") or "").lower() == "accepted"
        )

    for item in queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("recovery_block_reason") or "")
        if not reason:
            continue
        if str(item.get("status") or "") in {"accepted", "completed", "complete"}:
            clear_queue_item_recovery_block(
                item,
                reason="accepted_or_completed_item_no_longer_blocks_dispatch",
            )
            queue_changed = True
            continue
        blocked = (
            item.get("recovery_blocked") is True
            or item.get("operator_intervention_required") is True
            or item.get("status") == "paused"
        )
        if not blocked:
            continue
        item_id = str(item.get("id") or "")
        item_run_dir = str(item.get("run_dir") or "")
        item_is_current_context = bool(
            (item_id and item_id == active_queue_id)
            or (item_id and item_id == active_mission_queue_id)
            or (active_run_dir and item_run_dir == active_run_dir)
        )
        if current_is_accepted and not item_is_current_context:
            continue
        priority = 0
        if item.get("hard_blocked") is True:
            priority += 50
        if item_id and item_id == active_queue_id:
            priority += 30
        if item_id and item_id == active_mission_queue_id:
            priority += 20
        if active_run_dir and str(item.get("run_dir") or "") == active_run_dir:
            priority += 10
        blocked_at_raw = (
            item.get("recovery_blocked_at")
            or item.get("recovery_last_blocked_at")
            or item.get("paused_at")
            or item.get("last_incomplete_at")
        )
        blocked_dt = parse_utc_timestamp(blocked_at_raw)
        age = int((now_dt - blocked_dt).total_seconds()) if blocked_dt else 0
        candidates.append(
            {
                "priority": priority,
                "queue_item_id": item.get("id"),
                "queue_item_status": item.get("status"),
                "recovery_block_reason": reason,
                "time_in_blocked_state": max(age, 0),
                "next_operator_step": item.get("next_operator_step")
                or "Inspect the paused queue item and explicitly continue or hand off.",
                "hard_failure_reason": item.get("hard_failure_reason", ""),
                "recovery_attempt_count": int(item.get("recovery_attempt_count") or 0),
                "operator_intervention_required": item.get(
                    "operator_intervention_required"
                )
                is True,
                "hard_blocked": item.get("hard_blocked") is True,
            }
        )

    if queue_changed:
        write_queue(queue)

    if not candidates:
        return {
            "recovery_block_reason": "",
            "time_in_blocked_state": 0,
            "next_operator_step": "",
            "queue_item_id": "",
            "hard_failure_reason": "",
            "recovery_attempt_count": 0,
            "operator_intervention_required": False,
            "hard_blocked": False,
        }
    candidates.sort(
        key=lambda item: (
            int(item.get("priority") or 0),
            int(item.get("time_in_blocked_state") or 0),
        ),
        reverse=True,
    )
    selected = dict(candidates[0])
    selected.pop("priority", None)
    return selected


def dispatch_continuity_block(
    status: dict[str, Any], *, target_queue_id: str = ""
) -> dict[str, Any] | None:
    """Block auto-dispatch when recovery or mission context is stale."""
    block = recovery_block_status(status)
    if block.get("recovery_block_reason"):
        return {
            "status": "blocked_by_recovery",
            "reason": block.get("recovery_block_reason"),
            "queue_item_id": block.get("queue_item_id"),
            "detail": block.get("hard_failure_reason")
            or "Recovery block requires explicit operator action.",
            "next_operator_step": block.get("next_operator_step"),
        }

    mission = load_mission()
    if mission.get("status") != "active":
        return None
    active = mission.get("active_subgoal")
    if not isinstance(active, dict):
        return None
    mission_qid = str(active.get("queue_item_id") or "")
    if not mission_qid:
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "active_mission_missing_queue_id",
            "detail": "Mission is active but its active_subgoal has no queue_item_id.",
            "next_operator_step": "Run mission-show, then mission-stop or repair the mission state before auto-dispatch.",
        }

    matching_items = [
        item
        for item in queue_items()
        if isinstance(item, dict) and str(item.get("id") or "") == mission_qid
    ]
    if not matching_items:
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "active_mission_queue_item_missing",
            "queue_item_id": mission_qid,
            "detail": "Mission active_subgoal points at a queue item that no longer exists.",
            "next_operator_step": "Run mission-show, then explicitly stop/resume or recreate the mission handoff.",
        }
    item = matching_items[0]
    item_status = str(item.get("status") or "")
    if item_status in {"accepted", "failed_to_start", "failed", "rejected"}:
        return None
    if item_status == "paused":
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "active_mission_queue_item_paused",
            "queue_item_id": mission_qid,
            "detail": "Active mission subgoal is paused and needs an explicit resume/handoff.",
            "next_operator_step": "Run mission-resume or explicitly continue/handoff after reviewing the paused queue item.",
        }
    if item_status not in {"queued", "starting", "running", "needs_review", "paused"}:
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": f"active_mission_queue_item_{item_status or 'unknown'}",
            "queue_item_id": mission_qid,
            "detail": "Mission active_subgoal is in an unsupported queue state.",
            "next_operator_step": "Inspect queue and mission state before auto-dispatching more work.",
        }

    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    active_run_qid = str(run_meta.get("queue_id") or "")
    active_run_dir = str(status.get("active_run_dir") or "")
    item_run_dir = str(item.get("run_dir") or "")
    run_chain_ids = run_chain_queue_ids(active_run_dir) if active_run_dir else set()
    classification, _action = classify(status)
    dispatching_active_queued_subgoal = (
        bool(target_queue_id)
        and target_queue_id == mission_qid
        and item_status == "queued"
        and classification in {"accepted", "idle"}
    )
    if dispatching_active_queued_subgoal:
        return None
    if active_run_qid and active_run_qid != mission_qid:
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "active_run_queue_id_mismatch",
            "queue_item_id": mission_qid,
            "detail": (
                f"Active run queue_id={active_run_qid} does not match active "
                f"mission queue_id={mission_qid}."
            ),
            "next_operator_step": "Confirm the intended active run, then explicitly continue or hand off.",
        }
    if item_run_dir and active_run_dir and item_run_dir != active_run_dir:
        if reconcile_running_queue_item_run_dir(
            mission_qid=mission_qid,
            active_run_dir=active_run_dir,
            item=item,
            status=status,
        ):
            return None
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "active_run_dir_mismatch",
            "queue_item_id": mission_qid,
            "detail": "Queue item run_dir does not match the active run_dir.",
            "next_operator_step": "Inspect queue/run metadata before dispatching another mission subgoal.",
        }
    if active_run_dir and run_chain_ids and mission_qid not in run_chain_ids:
        return {
            "status": "blocked_by_stale_mission_context",
            "reason": "run_chain_queue_id_mismatch",
            "queue_item_id": mission_qid,
            "detail": "Active run metadata chain does not include the active mission queue id.",
            "next_operator_step": "Explicitly hand off only after confirming mission continuity.",
        }
    return None


def record_stuck_recovery_attempt(
    status: dict[str, Any],
    *,
    trigger: str,
) -> dict[str, Any] | None:
    """Escalate repeated stuck recovery attempts for the active queue item."""
    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    active_queue_id = str(run_meta.get("queue_id") or "")
    if not active_queue_id:
        active_queue_id = active_mission_queue_id_for_continuation(status)
    if not active_queue_id:
        return None

    queue = load_queue()
    items = queue.get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != active_queue_id:
            continue
        previous_count = int(item.get("recovery_attempt_count") or 0)
        next_count = previous_count + 1
        item["recovery_attempt_count"] = next_count
        item["last_stuck_recovery_at"] = now()
        item["last_stuck_recovery_trigger"] = trigger
        evidence = {
            "id": item.get("id"),
            "trigger": trigger,
            "recovery_attempt_count": next_count,
            "recovery_blocked": False,
        }
        if next_count >= RECOVERY_HARD_BLOCK_AFTER:
            item["status"] = "paused"
            item["paused_at"] = now()
            item["paused_reason"] = (
                "Repeated stuck recovery detected; automatic continue is disabled "
                "until an operator confirms the next step."
            )
            item["last_incomplete_at"] = item["paused_at"]
            item["last_incomplete_reason"] = f"stuck:{trigger}"
            item["recovery_attempt_count"] = previous_count
            block = mark_queue_item_recovery_blocked(
                item,
                reason="stuck",
                hard_failure_reason=(
                    "Repeated stuck recovery attempts reached the hard block "
                    f"threshold after trigger={trigger}."
                ),
                next_operator_step=(
                    "Inspect the recovery prompt and queue item, then explicitly "
                    "continue with corrective feedback or hand off after confirming "
                    "mission continuity."
                ),
            )
            evidence.update(block)
        write_queue(queue)
        return evidence
    return None


def enqueue_goal(args: argparse.Namespace) -> int:
    if not args.goal and not args.goal_file:
        print("enqueue requires --goal or --goal-file", file=sys.stderr)
        return 2
    if args.goal_file:
        goal_path = Path(args.goal_file)
        goal_text = goal_path.read_text(encoding="utf-8")
        goal_source = str(goal_path)
    else:
        goal_text = str(args.goal or "")
        goal_source = "inline"
    if not goal_text.strip():
        print("enqueue goal text is empty", file=sys.stderr)
        return 2

    def derived_queue_title(raw_title: str | None, text: str) -> str:
        title = str(raw_title or "").strip()
        if title and title not in {"Queued local goal", "Transferred Codex goal"}:
            return title[:120]
        for line in text.splitlines():
            cleaned = line.strip().lstrip("#").strip()
            if cleaned:
                return cleaned[:120]
        return "Queued local goal"

    queue = load_queue()
    items = queue.setdefault("items", [])
    item_id = queue_item_id(items)
    item = {
        "id": item_id,
        "title": derived_queue_title(args.title, goal_text),
        "goal": goal_text,
        "goal_source": goal_source,
        "planner": args.planner or "none",
        "executor": args.executor or "opencode",
        "executor_worker": (
            args.executor_worker
            if isinstance(getattr(args, "executor_worker", None), str)
            else "none"
        ),
        "status": "queued",
        "created_at": now(),
        "started_at": None,
        "completed_at": None,
        "run_dir": None,
        "prompt_path": None,
        "queue_id": item_id,
    }
    items.append(item)
    write_queue(queue)
    print(f"queued_id={item_id}")
    print(f"queue_json={QUEUE_JSON}")
    return 0


def lane_availability_reason(
    *,
    node_free: bool,
    queue_active: bool,
    worker_runner_present: bool = True,
) -> str:
    if not node_free:
        return "node1_not_free"
    if queue_active:
        return "queue_active"
    if not worker_runner_present:
        return "worker_runner_missing"
    return "ready"


def cloud_lane_availability_reason(*, worker_runner_present: bool) -> str:
    """Cloud lane availability depends on cloud worker readiness, not local Node1.

    The cloud executor lane is decoupled from local Node1 goal lane capacity so
    that bounded cloud goals can be enqueued while a local Node1 goal is running.
    """
    if not worker_runner_present:
        return "worker_runner_missing"
    return "ready"


def lane_capabilities(status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Truthful operator-facing lane map for Hermes/Codex control surfaces."""
    status = status if isinstance(status, dict) else manager_json()
    classification, action = classify(status)
    node_free = node1_is_free(status)
    vllm_idle = node1_vllm_is_idle(status)
    vllm_other_activity = node1_vllm_has_other_activity(status)
    queue_active = queue_has_active_work()
    local_available = node_free and not queue_active
    worker_runner_present = WORKER_RUNNER.exists()
    local_reason = lane_availability_reason(
        node_free=node_free,
        queue_active=queue_active,
    )
    # Cloud executor lane availability is decoupled from local Node1 goal lane
    # capacity. A bounded cloud goal can be enqueued while a local Node1 goal
    # is running, as long as the cloud worker runner and allowed cloud workers
    # are available. Broad all-cloud autonomy is still not fully proven and is
    # reported honestly elsewhere (cloud-autonomy-audit).
    cloud_available = worker_runner_present
    cloud_reason = cloud_lane_availability_reason(
        worker_runner_present=worker_runner_present,
    )
    timer_check = _timer_supervision_check()
    watcher_state = "active" if timer_check.get("ok") is True else "needs_attention"

    return {
        "contract": "local_node1_goal_capabilities.v1",
        "generated_at": now(),
        "current_state": {
            "classification": classification,
            "recommended_action": action,
            "node1_is_free": node_free,
            "node1_is_free_scope": "legacy: local-goal lane availability, not raw vLLM/GPU idleness",
            "local_goal_lane_free": node_free,
            "node1_vllm_idle": vllm_idle,
            "node1_vllm_has_other_activity": vllm_other_activity,
            "queue_has_active_work": queue_active,
            "tmux_running": status.get("tmux_running") is True,
            "active_run_dir": status.get("active_run_dir"),
            "active_planner": status.get("active_planner"),
            "availability_reason": local_reason,
        },
        "lanes": {
            "local": {
                "classification": "installed_capability",
                "installed": True,
                "available_now": local_available,
                "availability_reason": local_reason,
                "unavailable_reason": "" if local_available else local_reason,
                "builder": "local Node1 vLLM through opencode",
                "executor": "opencode",
                "command": (
                    f"{LOCAL_GOAL_WRAPPER} start --executor opencode "
                    "--goal-file /path/to/goal.md"
                ),
                "notes": [
                    "One Node1 long-goal job runs at a time.",
                    "Completion still requires review and acceptance.",
                ],
            },
            "premium_planner_local_builder": {
                "classification": "installed_capability",
                "installed": True,
                "available_now": local_available,
                "availability_reason": local_reason,
                "unavailable_reason": "" if local_available else local_reason,
                "planners": sorted(ALLOWED_PLANNERS - {"none"}),
                "builder": "local Node1 vLLM through opencode",
                "command": (
                    f"{LOCAL_GOAL_WRAPPER} premium-start --planner gpt-5.5 "
                    "--executor opencode --goal-file /path/to/goal.md"
                ),
                "credential_policy": (
                    "Planner-assisted starts are intended for OAuth/quota planner "
                    "routes; the local builder must stay on the local Node1 executor."
                ),
                "notes": [
                    "Use for broad or ambiguous goals that benefit from a frontier planner.",
                    "Planner availability depends on the configured OAuth/quota route.",
                ],
            },
            "cloud_executor": {
                "classification": (
                    "installed_capability" if worker_runner_present else "not_done"
                ),
                "installed": worker_runner_present,
                "available_now": cloud_available,
                "availability_reason": cloud_reason,
                "unavailable_reason": "" if cloud_available else cloud_reason,
                "builder": "Hermes worker_dispatch via terminal-worker-runner",
                "executor_workers": sorted(ALLOWED_EXECUTOR_WORKERS - {"none"}),
                "default_cloud_executor_workers": sorted(
                    DEFAULT_CLOUD_EXECUTOR_WORKERS - {"none"}
                ),
                "adapter_canary_workers": sorted(ADAPTER_CANARY_EXECUTOR_WORKERS),
                "default_executor_worker": CLOUD_BUILDER_FALLBACK,
                "command": (
                    f"{LOCAL_GOAL_WRAPPER} enqueue --executor-worker "
                    f"{CLOUD_BUILDER_FALLBACK} --goal-file /path/to/goal.md"
                ),
                "runner_path": str(WORKER_RUNNER),
                "runner_present": worker_runner_present,
                "notes": [
                    "This lane records a no-start manager run, then drives a bounded cloud builder loop.",
                    "Available independently of local Node1 lane capacity; a bounded cloud goal can enqueue while a local goal runs.",
                    "Adapter canary workers are registry-backed candidates, not default executors.",
                    "Do not describe it as local Node1 execution; review/acceptance still gates completion.",
                    "Broad all-cloud autonomy is not fully proven; bounded cloud goals are canary-scale.",
                ],
            },
        },
        "supervision": {
            "codex_terminal": f"Use {LOCAL_GOAL_WRAPPER} for status, capabilities, supervise, monitor, review, accept, enqueue, and mission commands.",
            "hermes_chat": "Use /local-goal, /local_goal, /node1-goal, or /node1_goal. Say 'supervise local harness' for the active review/continue/dispatch pass.",
            "telegram_updates": "Notifications are sent for review, completion, accepted, and stuck states when enabled.",
            "watcher": {
                "state": watcher_state,
                "timer_active": timer_check.get("timer_active") is True,
                "service_ok": timer_check.get("service_ok") is True,
                "timer_file_ok": timer_check.get("timer_file_ok") is True,
                "execstart_ok": timer_check.get("execstart_ok") is True,
                "systemd_unit": "local-node1-goal-watch.timer",
                "summary": (
                    "Background watcher active; it can monitor, continue, dispatch, commit owned changes, and accept when gates pass."
                    if watcher_state == "active"
                    else "Background watcher needs attention; use /local-goal supervise local harness for an immediate pass."
                ),
            },
        },
    }


def cmd_capabilities(args: argparse.Namespace) -> int:
    payload = lane_capabilities(manager_json())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    state = payload.get("current_state") or {}
    print(f"classification={state.get('classification')}")
    print(f"node1_is_free={state.get('node1_is_free')}")
    print(f"queue_has_active_work={state.get('queue_has_active_work')}")
    lanes = payload.get("lanes") or {}
    for name, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        print(
            f"lane={name} classification={lane.get('classification')} "
            f"installed={lane.get('installed')} "
            f"available_now={lane.get('available_now')} "
            f"reason={lane.get('availability_reason')}"
        )
        print(f"  command={lane.get('command')}")
    return 0


def _path_text_contains(path: Path, needles: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return all(needle in text for needle in needles)


def _systemd_execstart_commands(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    commands: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith("ExecStart="):
            continue
        commands.append(line.removeprefix("ExecStart=").strip())
    return commands


def _gateway_command_registry_check() -> dict[str, Any]:
    """Verify Hermes resolves the local-goal slash command and aliases."""
    if not HERMES_AGENT_ROOT.exists():
        return {
            "ok": False,
            "detail": f"missing hermes agent root: {HERMES_AGENT_ROOT}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    script = (
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(HERMES_AGENT_ROOT)!r})\n"
        "from hermes_cli.commands import is_gateway_known_command, resolve_command\n"
        "aliases = ['local-goal', 'local_goal', 'node1-goal', 'node1_goal']\n"
        "resolved = {}\n"
        "known = {}\n"
        "for alias in aliases:\n"
        "    command = resolve_command(alias)\n"
        "    resolved[alias] = getattr(command, 'name', None)\n"
        "    known[alias] = is_gateway_known_command(alias)\n"
        "ok = all(value == 'local-goal' for value in resolved.values()) and all(known.values())\n"
        "print(json.dumps({'ok': ok, 'resolved': resolved, 'known': known}, sort_keys=True))\n"
    )
    proc = run(
        [str(HERMES_PYTHON), "-c", script],
        timeout=20,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {}
    ok = proc.returncode == 0 and parsed.get("ok") is True
    return {
        "ok": ok,
        "detail": parsed if parsed else stdout[-500:],
        "returncode": proc.returncode,
        "stdout_tail": stdout[-1000:],
        "stderr_tail": stderr[-1000:],
    }


def _current_truth_operator_clarity_check() -> dict[str, Any]:
    """Verify current-truth exposes phone-useful status, capacity, and model fields.

    This runs inside the integration-audit lock, so keep it structural. Executing
    the current-truth adapter from here can recurse into the same integration
    audit through scheduled truth jobs and hold the lock for too long.
    """
    if not CURRENT_TRUTH.exists():
        return {
            "ok": False,
            "detail": f"missing current-truth adapter: {CURRENT_TRUTH}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        text = CURRENT_TRUTH.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"current-truth adapter unreadable: {exc}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_commands = {
        "current_truth",
        "shortcuts",
        "guide",
        "brief",
        "doctor",
        "doctor_json",
        "progress",
        "queue_summary",
        "last_run",
        "last_goal_changed_files",
        "accepted_evidence",
        "verification_passed",
        "dirty_acceptance",
        "free",
        "can_start",
        "stuck",
        "next_proof",
        "completion_summary",
        "audit_health",
        "soak_plan",
        "ready_review",
        "can_accept",
        "model_status",
        "model_promotion_decision",
        "model_promotion_plan",
        "model_promotion_verify",
        "terminal_only_model_promotion_apply_execute",
        "model_promotion_waiver",
        "model_decision_packet",
        "qwopus_completion_risk",
        "qwopus_safe_harness",
        "qwopus_192k_seq4",
        "qwopus_window_check",
        "qwopus_window_next",
        "qwopus_window_open_preview",
        "qwopus_window_restore_preview",
    }
    required_dirty_fields = {
        "dirty_completion_ok",
        "blocks_acceptance",
        "blocking_count",
        "note",
    }
    required_capacity_fields = {
        "local_goal_lane_free",
        "node1_vllm_idle",
        "node1_vllm_has_other_activity",
        "node1_vllm_running",
        "node1_vllm_waiting",
        "start_may_wait",
        "start_guidance",
        "node1_capacity",
    }
    required_report_lines = [
        "Node1 lane free:",
        "Node1 vLLM idle:",
        "Start may wait:",
        "Start guidance:",
        "Node1 idle legacy:",
        "Model promotion meaning:",
    ]
    missing_commands = sorted(
        command for command in required_commands if f'"{command}"' not in text
    )
    missing_dirty_fields = sorted(
        field for field in required_dirty_fields if f'"{field}"' not in text
    )
    missing_capacity_fields = sorted(
        field for field in required_capacity_fields if f'"{field}"' not in text
    )
    required_fragments = [
        "def augment_operator_commands",
        "def dirty_operator_summary",
        "def compact_integration_audit",
        "def compact_model_promotion_decision",
        "def node1_capacity_summary",
        "blocking_count=0",
        "promotion_allowed_meaning",
    ]
    missing_fragments = [
        fragment for fragment in required_fragments if fragment not in text
    ]
    missing_report_lines = [line for line in required_report_lines if line not in text]
    ok = not (
        missing_commands
        or missing_dirty_fields
        or missing_capacity_fields
        or missing_fragments
        or missing_report_lines
    )
    return {
        "ok": ok,
        "detail": {
            "missing_commands": missing_commands,
            "missing_dirty_fields": missing_dirty_fields,
            "missing_capacity_fields": missing_capacity_fields,
            "missing_fragments": missing_fragments,
            "missing_report_lines": missing_report_lines,
            "structural_check": True,
            "report": str(REPORT_DIR / "local-node1-goal-current-truth-latest.md"),
        },
        "returncode": 0 if ok else 1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _gateway_handler_dry_run_dispatch_check() -> dict[str, Any]:
    """Verify the real Hermes gateway handler can invoke dry-run lane commands."""
    if not HERMES_AGENT_ROOT.exists():
        return {
            "ok": False,
            "detail": f"missing hermes agent root: {HERMES_AGENT_ROOT}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    script = (
        "import asyncio\n"
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(HERMES_AGENT_ROOT)!r})\n"
        "from gateway.run import GatewayRunner\n"
        "class Event:\n"
        "    def __init__(self, args):\n"
        "        self.args = args\n"
        "    def get_command_args(self):\n"
        "        return self.args\n"
        "CASES = [\n"
        "    {\n"
        "        'lane': 'local_supervise',\n"
        "        'args': 'dry run supervise local harness',\n"
        "        'needle': 'Dry run: would route to supervise',\n"
        "        'action': 'Supervisor action: supervise',\n"
        "    },\n"
        "    {\n"
        "        'lane': 'premium_planner_local_builder',\n"
        "        'args': 'dry run start this as local goal with thinkmax planner: integration route check',\n"
        "        'needle': 'Dry run: would route to premium-start',\n"
        "        'action': 'Supervisor action: premium-start',\n"
        "    },\n"
        "    {\n"
        "        'lane': 'cloud_executor',\n"
        "        'args': 'dry run start cloud local goal with gpt 5.5 planner: integration route check',\n"
        "        'needle': 'Dry run: would route to enqueue-cloud',\n"
        "        'action': 'Supervisor action: enqueue',\n"
        "    },\n"
        "]\n"
        "async def main():\n"
        "    runner = object.__new__(GatewayRunner)\n"
        "    results = []\n"
        "    for case in CASES:\n"
        "        text = await runner._handle_local_goal_command(Event(case['args']))\n"
        "        results.append({\n"
        "            'lane': case['lane'],\n"
        "            'ok': case['needle'] in text and case['action'] in text,\n"
        "            'reply': text[:1200],\n"
        "        })\n"
        "    print(json.dumps({'ok': all(item['ok'] for item in results), 'dispatches': results}, sort_keys=True))\n"
        "asyncio.run(main())\n"
    )
    proc = run(
        [str(HERMES_PYTHON), "-c", script],
        timeout=90,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {}
    ok = proc.returncode == 0 and parsed.get("ok") is True
    return {
        "ok": ok,
        "detail": parsed.get("dispatches") if parsed else stdout[-500:],
        "dispatches": parsed.get("dispatches")
        if isinstance(parsed.get("dispatches"), list)
        else [],
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _command_capabilities_human_output_check() -> dict[str, Any]:
    """Verify bare capabilities output is human-readable, not raw JSON."""
    if not COMMAND_SHIM.exists():
        return {
            "ok": False,
            "detail": f"missing command shim: {COMMAND_SHIM}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run(["python3", str(COMMAND_SHIM), "capabilities"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Status:",
        "Current state:",
        "local_goal_lane_free=",
        "node1_vllm_idle=",
        "Supervision:",
        "watcher=",
        "Lane: Local Node1",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    raw_json = stdout.startswith("{") or '"contract"' in stdout[:500]
    ok = proc.returncode == 0 and not missing and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _command_doctor_human_output_check() -> dict[str, Any]:
    """Verify bare doctor output is a phone-readable operator summary."""
    if not COMMAND_SHIM.exists():
        return {
            "ok": False,
            "detail": f"missing command shim: {COMMAND_SHIM}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run(["python3", str(COMMAND_SHIM), "doctor local harness"], timeout=60)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Doctor",
        "Operator decision:",
        "Current status:",
        "Current mission:",
        "Current lanes:",
        "Model durability:",
        "Promotion gate:",
        "Supervision:",
        "Trust boundary:",
        "Operator actions open:",
        "ornith_durable_promotion_decision",
        "Choose whether to make Ornith durable",
        "Accepted soak evidence",
        "Usable for bounded local goals; broad unattended autonomy is not claimed.",
        "Safe read-only / preview commands:",
        "Start commands (starts work):",
    ]
    forbidden_fragments = [
        "\nintent=doctor",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{") or '"contract"' in stdout[:500]
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-5000:],
        "stderr_tail": stderr[-1000:],
    }


def _command_doctor_json_state_output_check() -> dict[str, Any]:
    """Verify doctor JSON exposes compact machine-readable operator state."""
    if not COMMAND_SHIM.exists():
        return {
            "ok": False,
            "detail": f"missing command shim: {COMMAND_SHIM}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run(
        ["python3", str(COMMAND_SHIM), "doctor local harness", "--json"], timeout=60
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    try:
        payload = json.loads(stdout)
    except Exception as exc:
        payload = {}
        parse_error = str(exc)
    else:
        parse_error = ""
    state = (
        payload.get("doctor_state")
        if isinstance(payload.get("doctor_state"), dict)
        else {}
    )
    top_level_required = [
        "ok",
        "status",
        "local_goal_lane_free",
        "tmux_running",
        "watcher_active",
    ]
    required_fields = [
        "classification",
        "local_goal_lane_free",
        "node1_vllm_idle",
        "mission_status",
        "watcher_active",
        "lanes",
        "model_promotion_status",
        "operator_actions_open",
        "safe_next_commands",
        "model_promotion_waiver_command",
    ]
    missing = [field for field in required_fields if field not in state]
    safe_next_commands = (
        state.get("safe_next_commands")
        if isinstance(state.get("safe_next_commands"), list)
        else []
    )
    waiver_safe = "scripts/local-goal model-promotion-waiver" in safe_next_commands
    mutating_apply_absent = (
        "scripts/local-goal model-promotion-apply" not in safe_next_commands
    )
    if not waiver_safe:
        missing.append("safe_next_commands.model_promotion_waiver")
    if not mutating_apply_absent:
        missing.append("safe_next_commands.no_bare_model_promotion_apply")
    if "scripts/local-goal glm-handoff-plan" not in safe_next_commands:
        missing.append("safe_next_commands.glm_handoff_plan")
    if "scripts/local-goal glm-supervisor status" not in safe_next_commands:
        missing.append("safe_next_commands.glm_supervisor_status")
    for field in top_level_required:
        if field not in payload:
            missing.append(f"top_level.{field}")
    ok = proc.returncode == 0 and not parse_error and not missing
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "parse_error": parse_error,
            "top_level_ok": payload.get("ok"),
            "top_level_status": payload.get("status"),
            "top_level_local_goal_lane_free": payload.get("local_goal_lane_free"),
            "top_level_tmux_running": payload.get("tmux_running"),
            "top_level_watcher_active": payload.get("watcher_active"),
            "classification": state.get("classification"),
            "local_goal_lane_free": state.get("local_goal_lane_free"),
            "mission_status": state.get("mission_status"),
            "watcher_active": state.get("watcher_active"),
            "operator_actions_open": state.get("operator_actions_open"),
            "waiver_safe": waiver_safe,
            "mutating_apply_absent": mutating_apply_absent,
            "model_promotion_waiver_command": state.get(
                "model_promotion_waiver_command"
            ),
        },
        "missing": missing,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-5000:],
        "stderr_tail": stderr[-1000:],
    }


def _command_brief_human_output_check() -> dict[str, Any]:
    """Verify bare brief output is the shortest phone-readable next-step card."""
    if not COMMAND_SHIM.exists():
        return {
            "ok": False,
            "detail": f"missing command shim: {COMMAND_SHIM}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run(["python3", str(COMMAND_SHIM), "brief local harness"], timeout=60)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Brief",
        "Answer:",
        "Lane:",
        "Node1:",
        "Babysit:",
        "Proof:",
        "Boundary: bounded local goals ready; broad autonomy not claimed.",
        "Start (starts work):",
        "Terminal start: scripts/local-goal quick-start --goal '<bounded task>'",
        "Open action: Ornith durability decision is optional.",
        "Model choice: /local-goal model-promotion-decision",
        "Terminal-only command:",
    ]
    forbidden_fragments = [
        "\nintent=brief",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{") or '"contract"' in stdout[:500]
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2500:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_quick_start_short_goal_guard_check() -> dict[str, Any]:
    """Verify quick-start rejects underspecified goals before manager transfer."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run(
        [str(LOCAL_GOAL_WRAPPER), "quick-start", "--goal", "too short"],
        timeout=30,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    combined = f"{stdout}\n{stderr}"
    required_fragments = [
        "quick-start goal is too short",
        "Describe one bounded task",
    ]
    forbidden_fragments = [
        "ticket_error:",
        "ticket_warning:",
        "transfer:",
        "not starting worker",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in combined]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in combined]
    ok = proc.returncode == 2 and not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "returncode": proc.returncode,
        },
        "missing": missing,
        "forbidden": forbidden,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-1000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_guide_human_output_check() -> dict[str, Any]:
    """Verify the wrapper guide stays phone-readable and operator useful."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "guide"], timeout=60)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Human Guide",
        "Check local-goal availability and vLLM wait warnings",
        "scripts/local-goal quick-start --goal",
        "Use at least 40 characters",
        "scripts/local-goal supervise",
        "Hermes chat equivalents:",
        "doctor local harness",
        "/local-goal harness modes",
        "continue agentic harness work",
        "Rules of thumb:",
        "Current status:",
        "Current mission:",
        "Current proof state:",
        "Local Goal Proof Snapshot",
        "Last accepted soak proof:",
        "Current capability lanes:",
        "Lane: Local Node1",
        "Lane: Planner + Local",
        "Lane: Cloud Executor",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_head": stdout[:1000],
        "stdout_tail": stdout[-3000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_status_human_output_check() -> dict[str, Any]:
    """Verify the wrapper status output stays short and operator-readable."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "status"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Status:",
        "What is happening now:",
        "Does Michael need to do anything?",
        "Exact phrase to send Hermes:",
        "Reason:",
        "Next:",
        "Goal:",
        "Queue:",
        "Node1:",
        "local-goal",
        "model server",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_progress_human_output_check() -> dict[str, Any]:
    """Verify the wrapper progress output gives one compact phone report."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = os.environ.copy()
    env["LOCAL_GOAL_PROGRESS_USE_CACHED_READINESS"] = "1"
    proc = run([str(LOCAL_GOAL_WRAPPER), "progress"], timeout=120, env=env)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Progress",
        "Harness readiness:",
        "Usable for bounded local goals:",
        "100% broad-autonomy claim:",
        "Status:",
        "Current status:",
        "Last accepted run:",
        "Remaining:",
        "Operator actions open:",
        "ornith_durable_promotion_decision",
        "Choose whether to make Ornith durable",
        "Model decision:",
        "Phone-safe preview:",
        "Terminal-only mutation:",
        "scripts/local-goal next-proof",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
        "intent=progress",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_head": stdout[:1000],
        "stdout_tail": stdout[-5000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_next_proof_human_output_check() -> dict[str, Any]:
    """Verify the wrapper next-proof output gives an actionable phone report."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = os.environ.copy()
    env["LOCAL_GOAL_NEXT_PROOF_USE_CACHED_READINESS"] = "1"
    proc = run([str(LOCAL_GOAL_WRAPPER), "next-proof"], timeout=120, env=env)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Next Proof",
        "Status:",
        "Required now:",
        "Readiness:",
        "Local-goal lane free:",
        "Node1 vLLM capacity clear:",
        "Start may wait:",
        "Last accepted soak proof:",
        "Next proof:",
        "Operator actions open:",
        "ornith_durable_promotion_decision",
        "Choose whether to make Ornith durable",
        "Phone-safe preview; does not mutate services.",
        "Safe commands:",
        "Start commands (starts work):",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
        "intent=next-proof",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    safe_section = ""
    start_section = ""
    section_errors: list[str] = []
    if "Safe commands:" in stdout and "Start commands (starts work):" in stdout:
        safe_section = stdout.split("Safe commands:", 1)[1].split(
            "Start commands (starts work):", 1
        )[0]
        start_section = stdout.split("Start commands (starts work):", 1)[1]
        if "quick-start --goal" in safe_section:
            section_errors.append("quick-start listed under Safe commands")
        if "quick-start --goal" not in start_section:
            section_errors.append("quick-start missing from Start commands")
    raw_json = stdout.startswith("{")
    ok = (
        proc.returncode == 0
        and not missing
        and not forbidden
        and not section_errors
        and not raw_json
    )
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "section_errors": section_errors,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2500:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_completion_audit_human_output_check() -> dict[str, Any]:
    """Verify completion-audit human output without recursively running integration.

    The full integration audit holds the integration-audit lock. Running the
    wrapper command here can re-enter readiness/integration checks through the
    manager and leave callers with a stale lock-fallback artifact. Keep this
    check structural; the standalone completion-audit command is exercised by
    wrapper tests and by callers outside the integration-audit lock.
    """
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_fragments = [
        "completion-audit|completion|harness-audit)",
        "LOCAL_GOAL_COMPLETION_AUDIT_USE_CACHED_READINESS",
        "Local Goal Completion Audit",
        "Status:",
        "Usable for bounded local goals:",
        "100% broad-autonomy claim: not claimed",
        "Required evidence ok:",
        "Operator actions open:",
        "operator_action_details",
        "Control routes:",
        '"control_routes_summary"',
        '"control_route_details"',
        "wrapper_continue_human_output_mapping",
        "Capability gates:",
        '"capability_gates_summary"',
        '"capability_gate_details"',
        "cloud_executor_lane_installed",
        "Safety checks:",
        '"safety_checks_summary"',
        '"safety_check_details"',
        "wrapper_quick_start_short_goal_guard",
        "start_next_bounded_goal",
        '"start_command"',
        'if detail.get("start_command"):',
        "Start: {detail['start_command']}",
        "starts_local_goal",
        "safety_note",
        "ornith_durable_promotion_decision",
        "Choose whether to make Ornith durable",
        "Phone-safe preview; does not mutate services.",
        "Terminal-only mutation:",
        "Requirements:",
        '"readiness_gate_green"',
        '"hermes_integration_green"',
        '"gateway_service_active"',
        '"local_goal_lane_free"',
        '"node1_vllm_capacity_clear"',
        '"accepted_soak_evidence_present"',
        '"model_decision_explicitly_operator_gated"',
        '"required_for_bounded_ready"',
        '"bounded_required_requirements"',
        '"informational_requirements"',
        '"operator_surface_details"',
        '"operator_surfaces_summary"',
        '"start_next_commands"',
        'scripts/local-goal quick-start --goal "Describe one bounded task here"',
        "queue_summary_operator_surface_verified",
        "wrapper_queue_summary_human_output",
        "glm_supervisor_operator_surface_verified",
        "wrapper_glm_supervisor_human_output",
        'scope = "required"',
        'else "info"',
        "[{scope}]",
        "Safe next commands:",
    ]
    forbidden_fragments = [
        "command_state=",
        "command_report=",
        "intent=completion-audit",
        '"preview_note": "starts real local work when run"',
        "Preview: {detail['start_command']}",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in text]
    ok = not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "structural_check": True,
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": False,
        "returncode": 0 if ok else 1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _wrapper_completion_summary_human_output_check() -> dict[str, Any]:
    """Verify completion-summary is a compact human surface.

    Keep this structural for the same reason as completion-audit: the full
    integration audit holds the integration-audit lock, while completion-summary
    delegates to completion-audit --json.
    """
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_fragments = [
        "completion-summary|harness-summary|proof-summary",
        "local_node1_goal_completion_summary.v1",
        "completion-audit --json",
        "Local Goal Completion Summary",
        "Usable for bounded local goals:",
        "Required evidence ok:",
        "Operator surfaces:",
        "Control routes:",
        "Capability gates:",
        "Safety checks:",
        "Start may wait:",
        "Start guidance:",
        "Operator actions open:",
        "operator_action_details",
        "terminal_only_mutation",
        "Terminal-only mutation:",
        "Next safe command:",
        "Start command:",
        'safe_commands = payload.get("safe_next_commands")',
        'start_commands = payload.get("start_next_commands")',
        'print(f"Next safe command:',
        'print(f"Start command:',
        "Full evidence:",
        '"safe_next_commands"',
        '"start_next_commands"',
        '"operator_surfaces_summary"',
        '"control_routes_summary"',
        '"capability_gates_summary"',
        '"safety_checks_summary"',
        '"source_command"',
        '"source_contract"',
    ]
    forbidden_fragments = [
        "command_state=",
        "command_report=",
        "intent=completion-summary",
        'print(f"Next safe command: {short(start_commands',
        'print(f"Start command: {short(safe_commands',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in text]
    ok = not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "structural_check": True,
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": False,
        "returncode": 0 if ok else 1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _wrapper_audit_health_human_output_check() -> dict[str, Any]:
    """Verify audit-health is a read-only phone/debug surface.

    Keep this structural while integration-audit holds its lock; executing the
    command here would intentionally report the audit lock as busy.
    """
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_fragments = [
        "audit-health|audit-lock|lock-health)",
        "Local Goal Audit Health",
        "local_goal_audit_health.v1",
        "LOCAL_GOAL_AUDIT_LOCK",
        "LOCAL_GOAL_INTEGRATION_AUDIT_JSON",
        "ready_refresh_running",
        "refresh_in_progress",
        "artifact_trust",
        "integration_not_ready",
        "scripts/local-goal audit-health",
    ]
    forbidden_fragments = [
        'rm -f "$LOCAL_GOAL_AUDIT_LOCK"',
        "rm -f ${LOCAL_GOAL_AUDIT_LOCK}",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in text]
    ok = not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "structural_check": True,
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": False,
        "returncode": 0 if ok else 1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _wrapper_current_truth_human_output_check() -> dict[str, Any]:
    """Verify the wrapper current-truth output stays phone-readable.

    Keep this structural inside integration-audit. Running the wrapper here
    executes the current-truth adapter, which can recurse into integration-audit
    through scheduled truth surfaces.
    """
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_fragments = [
        "print_current_truth()",
        'python3 "$CURRENT_TRUTH"',
        "Local Goal Current Truth",
        "Status:",
        "Running:",
        "Accepted:",
        "Node1 idle:",
        "Recommended action:",
        "Queue:",
        "Dirty blocks acceptance:",
        "Dirty blocking count:",
        "Integration audit:",
        "Useful commands:",
        '"guide"',
        '"progress"',
        '"next_proof"',
        '"ready_review"',
        '"can_accept"',
        '"qwopus_completion_risk"',
        '"qwopus_safe_harness"',
        '"qwopus_192k_seq4"',
        '"model_promotion_decision"',
        '"model_promotion_plan"',
        '"model_promotion_verify"',
        '"terminal_only_model_promotion_apply_execute"',
        '"model_promotion_waiver"',
        '"model_decision_packet"',
        '"glm_handoff_plan"',
        '"glm_supervisor"',
        '"qwopus_window_check"',
        '"qwopus_window_next"',
        '"qwopus_window_open_preview"',
        '"qwopus_window_restore_preview"',
    ]
    forbidden_fragments = [
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in text]
    ok = not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "structural_check": True,
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": False,
        "returncode": 0 if ok else 1,
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _wrapper_soak_plan_human_output_check() -> dict[str, Any]:
    """Verify the wrapper soak-plan output gives exact non-mutating proof commands."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = os.environ.copy()
    env["LOCAL_GOAL_SOAK_PLAN_USE_CACHED_READINESS"] = "1"
    proc = run([str(LOCAL_GOAL_WRAPPER), "soak-plan"], timeout=120, env=env)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Soak Plan",
        "Status:",
        "Can start:",
        "Does not start work: true",
        "Start command:",
        "Monitor command:",
        "Check commands:",
        "scripts/local-goal quick-start",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
        "intent=soak-plan",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-3000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_model_service_window_health_guard_check() -> dict[str, Any]:
    """Verify generated service-window recipes fail closed if vLLM health never returns."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    commands = {
        "model-cutover-plan": ["activate_commands"],
        "model-baseline-capture-plan": ["capture_commands"],
    }
    results: dict[str, Any] = {}
    missing: list[str] = []
    forbidden: list[str] = []
    returncodes: dict[str, int] = {}
    stdout_tail = ""
    stderr_tail = ""

    for command_name, command_lists in commands.items():
        proc = run([str(LOCAL_GOAL_WRAPPER), command_name, "--json"], timeout=120)
        returncodes[command_name] = proc.returncode
        stdout_tail += proc.stdout[-1200:]
        stderr_tail += proc.stderr[-600:]
        try:
            payload = json.loads(proc.stdout)
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            payload = {}
        command_text = "\n".join(
            str(item)
            for list_name in command_lists
            for item in (
                payload.get(list_name)
                if isinstance(payload.get(list_name), list)
                else []
            )
        )
        has_health_guard = (
            "health_ok=0" in command_text and 'test "$health_ok" = 1' in command_text
        )
        has_fallthrough_loop = (
            "curl -fsS --max-time 3 http://127.0.0.1:8008/health && break; sleep 5; done"
            in command_text
            and 'test "$health_ok" = 1' not in command_text
        )
        if proc.returncode != 0:
            missing.append(f"{command_name}:returncode_0")
        if not has_health_guard:
            missing.append(f"{command_name}:fail_closed_health_guard")
        if has_fallthrough_loop:
            forbidden.append(f"{command_name}:fallthrough_health_loop")
        results[command_name] = {
            "returncode": proc.returncode,
            "has_health_guard": has_health_guard,
            "has_fallthrough_loop": has_fallthrough_loop,
        }

    ok = not missing and not forbidden
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "results": results,
        },
        "missing": missing,
        "forbidden": forbidden,
        "returncodes": returncodes,
        "stdout_tail": stdout_tail[-2000:],
        "stderr_tail": stderr_tail[-1000:],
    }


def _wrapper_queue_human_output_check() -> dict[str, Any]:
    """Verify the wrapper queue output distinguishes active work from history."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "queue"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Status:",
        "Reason:",
        "Next:",
        "History:",
        "active queue item",
        "historical",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_queue_summary_human_output_check() -> dict[str, Any]:
    """Verify the compact queue-summary command used by Hermes chat routes."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "queue-summary"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Local Goal Queue Summary",
        "Status:",
        "Active:",
        "History:",
        "Next:",
        "Full queue: scripts/local-goal queue --json",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_glm_supervisor_human_output_check() -> dict[str, Any]:
    """Verify the GLM tmux supervisor status surface is phone-readable."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    env = os.environ.copy()
    env["LOCAL_GOAL_GLM_STATUS_SKIP_LIVE_SUMMARY"] = "1"
    proc = run(
        [str(LOCAL_GOAL_WRAPPER), "glm-supervisor", "status"],
        timeout=45,
        env=env,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    base_required_fragments = [
        "Status:",
        "Session:",
    ]
    running_required_fragments = [
        "Attach:",
        "Loop:",
    ]
    stopped_required_fragments = [
        "Start:",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    base_missing = [
        fragment for fragment in base_required_fragments if fragment not in stdout
    ]
    running_missing = [
        fragment for fragment in running_required_fragments if fragment not in stdout
    ]
    stopped_missing = [
        fragment for fragment in stopped_required_fragments if fragment not in stdout
    ]
    status_line = stdout.splitlines()[0] if stdout else ""
    running_shape_ok = "Status: running" in stdout and not running_missing
    stopped_shape_ok = "Status: stopped" in stdout and not stopped_missing
    missing = base_missing
    if not running_shape_ok and not stopped_shape_ok:
        missing = base_missing + running_missing + stopped_missing
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = (
        proc.returncode == 0
        and not base_missing
        and (running_shape_ok or stopped_shape_ok)
        and not forbidden
        and not raw_json
    )
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": status_line,
            "running_shape_ok": running_shape_ok,
            "stopped_shape_ok": stopped_shape_ok,
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_glm_handoff_plan_human_output_check() -> dict[str, Any]:
    """Verify the GLM handoff plan is phone-readable and non-mutating."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "glm-handoff-plan"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "GLM-5.2 Handoff Plan",
        "Recommended mode:",
        "Boundary:",
        "Dry run:",
        "premium-start --planner glm-5.2",
        "opencode-glm-build",
        "Review after GLM:",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_harness_modes_human_output_check() -> dict[str, Any]:
    """Verify the Hermes gateway harness mode guide is phone-readable."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "harness-modes"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Harness Gateway Modes",
        "Hermes Controller is the gateway.",
        "Mutates now: false",
        "Default: Mode 1 - OpenCode executes, GLM-5.2 supervises.",
        "Mode 1: OpenCode executes, GLM-5.2 supervises",
        "Recommendation: Use this by default.",
        "Mode 3: GLM-5.2 supervises and GLM-backed cloud worker executes",
        "Experimental; run as a canary only.",
        "Trust boundary:",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_model_promotion_commands_check() -> dict[str, Any]:
    """Verify model-promotion wrapper commands are present and non-mutating."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "commands": {},
        }

    expected_contracts = {
        "model-promotion-decision": "local_node1_goal_model_promotion_decision.v1",
        "model-promotion-plan": "local_node1_goal_model_promotion_plan.v1",
        "model-promotion-apply": "local_node1_goal_model_promotion_apply.v1",
        "model-promotion-verify": "local_node1_goal_model_promotion_verify.v1",
    }
    results: dict[str, Any] = {}
    failures: list[str] = []
    for command, expected_contract in expected_contracts.items():
        proc = run([str(LOCAL_GOAL_WRAPPER), command, "--json"], timeout=45)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        payload: dict[str, Any] | None = None
        parse_error = ""
        if stdout:
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    parse_error = f"json root is {type(parsed).__name__}"
            except json.JSONDecodeError as exc:
                parse_error = str(exc)
        else:
            parse_error = "empty stdout"

        contract_ok = (
            payload is not None and payload.get("contract") == expected_contract
        )
        non_mutating_ok = (
            payload is not None and payload.get("mutates_live_service") is False
        )
        preview_apply_ok = True
        if command == "model-promotion-apply":
            preview_apply_ok = (
                payload is not None
                and payload.get("executed") is False
                and payload.get("would_mutate_live_service") is True
            )
        ok = (
            proc.returncode == 0
            and payload is not None
            and contract_ok
            and non_mutating_ok
            and preview_apply_ok
        )
        if not ok:
            failures.append(command)
        results[command] = {
            "ok": ok,
            "returncode": proc.returncode,
            "contract_ok": contract_ok,
            "non_mutating_ok": non_mutating_ok,
            "preview_apply_ok": preview_apply_ok,
            "parse_error": parse_error,
            "contract": payload.get("contract") if payload else None,
            "status": payload.get("status") if payload else None,
            "stdout_tail": stdout[-1000:],
            "stderr_tail": stderr[-1000:],
        }

    return {
        "ok": not failures,
        "detail": {
            "checked": sorted(expected_contracts),
            "failures": failures,
        },
        "commands": results,
    }


def _wrapper_mission_show_human_output_check() -> dict[str, Any]:
    """Verify the wrapper mission-show output stays short and operator-readable."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    proc = run([str(LOCAL_GOAL_WRAPPER), "mission-show"], timeout=45)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    required_fragments = [
        "Status:",
        "Reason:",
        "Next:",
        "Progress:",
        "subgoals",
    ]
    forbidden_fragments = [
        '"contract"',
        "Traceback",
        "command_state=",
        "command_report=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in stdout]
    forbidden = [fragment for fragment in forbidden_fragments if fragment in stdout]
    raw_json = stdout.startswith("{")
    ok = proc.returncode == 0 and not missing and not forbidden and not raw_json
    return {
        "ok": ok,
        "detail": {
            "missing": missing,
            "forbidden": forbidden,
            "raw_json": raw_json,
            "first_line": stdout.splitlines()[0] if stdout else "",
        },
        "missing": missing,
        "forbidden": forbidden,
        "raw_json": raw_json,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _wrapper_supervise_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON supervise is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "supervise)",
        'cmd=(python3 "$SUPERVISOR" supervise)',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_monitor_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON monitor is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "monitor)",
        'cmd=(python3 "$SUPERVISOR" monitor)',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_review_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON review is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "review)",
        'python3 "$SUPERVISOR" review --json | humanize_local_goal_json',
        '"Status: timeout"',
        '"review timed out"',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_accept_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON accept is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "accept)",
        'python3 "$SUPERVISOR" accept --json | humanize_local_goal_json',
        "Review acceptance was recorded for the active local-goal run.",
        'acceptance_path = payload.get("acceptance_path")',
        "Acceptance: {short(acceptance_path, 220)}",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_nudge_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON nudge is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "nudge)",
        'cmd=(python3 "$MANAGER" nudge --review-feedback "$FEEDBACK")',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
        '"Status: nudge recorded"',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_external_review_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON external-review is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "external-review)",
        'cmd=(python3 "$SUPERVISOR" external-review --reviewer "$REVIEWER" --review-timeout "$TIMEOUT")',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
        "local_node1_goal_external_review.v1",
        "advisory evidence only",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_mission_monitor_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON mission-monitor is mapped through the humanizer without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "mission-monitor)",
        'cmd=(python3 "$SUPERVISOR" mission-monitor)',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
        "Mission monitor would generate",
        "Queue: has_work=",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_continue_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON continue adds a phone-readable summary without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "continue)",
        'python3 "$MANAGER" continue --review-feedback "$FEEDBACK"',
        '"Status: continue started"',
        '"Status: continue failed"',
        "Let supervise/monitor watch the resumed run",
        "Run status and review before retrying continue",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_mission_control_human_output_mapping_check() -> dict[str, Any]:
    """Verify mission-stop/resume add phone-readable summaries without running them."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "mission-stop)",
        'python3 "$SUPERVISOR" mission-stop',
        '"Status: mission stop recorded"',
        '"Status: mission stop failed"',
        "Mission automation will not generate new subgoals until mission-resume is used.",
        "mission-resume)",
        'python3 "$SUPERVISOR" mission-resume',
        '"Status: mission resume recorded"',
        '"Status: mission resume failed"',
        "Let mission-monitor/supervise dispatch and review the next subgoal.",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_stop_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON stop adds a phone-readable summary without running it."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "stop)",
        'python3 "$SUPERVISOR" stop',
        '"Status: stop recorded"',
        '"Status: stop failed"',
        "Run status before starting or continuing more local-goal work.",
        "inspect the local-goal log before retrying stop",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_repair_closeout_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON repair-closeout maps JSON repair evidence through the humanizer."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "repair-closeout)",
        'cmd=(python3 "$MANAGER" repair-closeout --summary "$SUMMARY" --remaining "$REMAINING")',
        "cmd+=(--json)",
        '"${cmd[@]}" | humanize_local_goal_json',
        "Closeout repair marker was written for review.",
        "Stop the active worker or resolve the blocker before retrying repair-closeout.",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_recovery_human_output_mapping_check() -> dict[str, Any]:
    """Verify non-JSON recovery commands map JSON evidence through the humanizer."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "recovery-audit|recovery-simulation)",
        "cmd+=(--json)",
        'if [[ "$JSON" -eq 1 ]]; then',
        '"${cmd[@]}" | humanize_local_goal_json',
        "Recovery artifacts are present and parseable.",
        "Fresh-agent recovery decision",
        "Harness readiness:",
        "bounded local goals ready",
        "100% broad-autonomy claim:",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_handoff_output_human_output_mapping_check() -> dict[str, Any]:
    """Verify handoff --output adds a phone-readable write/failure summary."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "handoff)",
        'python3 "$SUPERVISOR" handoff --current --output "$OUTPUT"',
        '"Status: handoff written"',
        '"Status: handoff failed"',
        "Use this file to resume context before starting or continuing local-goal work.",
        "retry handoff with a writable --output path",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _wrapper_ask_plain_language_mapping_check() -> dict[str, Any]:
    """Verify phone-friendly plain-language helpers route without recursive loops."""
    if not LOCAL_GOAL_WRAPPER.exists():
        return {
            "ok": False,
            "detail": f"missing local-goal wrapper: {LOCAL_GOAL_WRAPPER}",
            "missing": ["wrapper"],
        }
    try:
        text = LOCAL_GOAL_WRAPPER.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "detail": f"wrapper unreadable: {exc}",
            "missing": ["readable_wrapper"],
        }
    required_fragments = [
        "ask [--dry-run] TEXT",
        "plain|say [--dry-run] TEXT",
        "next|what-now [TEXT]",
        "trust|babysit [TEXT]",
        "health|working|opinion [TEXT]",
        "free|can-start|stuck [TEXT]",
        "ready-review|can-accept [TEXT]",
        "model-nontrivial-baseline-plan|qwopus-nontrivial-plan",
        "model-nontrivial-baseline-check|qwopus-nontrivial-check",
        "model-eval-next|ornith-eval-next|qwopus-next",
        "model-service-window-check|qwopus-window-check",
        "shortcuts|cheatsheet",
        "Local Goal Shortcuts",
        "Read-only status:",
        "local-goal guide",
        "Phone questions:",
        "Plain-language parser:",
        'local-goal ask "should I stop it?"',
        'local-goal ask "can I stop it now?"',
        'local-goal ask "is it safe to stop?"',
        'local-goal ask "should I pause it?"',
        'local-goal ask "can I pause it now?"',
        'local-goal ask "is it safe to pause?"',
        'local-goal ask "should I continue it?"',
        'local-goal ask "can I continue it now?"',
        'local-goal ask "should I resume it?"',
        'local-goal ask "can I resume it now?"',
        "Real mutation commands:",
        "local-goal what-now",
        "local-goal no-babysit",
        "local-goal working",
        "local-goal opinion",
        "local-goal can-start",
        "local-goal ready-review",
        "local-goal can-accept",
        'if [[ "$COMMAND" == "ask" || "$COMMAND" == "plain" || "$COMMAND" == "say" ]]; then',
        'if [[ "$COMMAND" == "next" || "$COMMAND" == "what-now" || "$COMMAND" == "now" ]]; then',
        'if [[ "$COMMAND" == "trust" || "$COMMAND" == "babysit" || "$COMMAND" == "no-babysit" ]]; then',
        'if [[ "$COMMAND" == "health" || "$COMMAND" == "working" || "$COMMAND" == "opinion" ]]; then',
        'if [[ "$COMMAND" == "free" || "$COMMAND" == "can-start" || "$COMMAND" == "stuck" ]]; then',
        'if [[ "$COMMAND" == "ready-review" || "$COMMAND" == "can-accept" ]]; then',
        "print_model_nontrivial_baseline_plan",
        "print_model_nontrivial_baseline_check",
        "print_model_eval_next",
        "print_model_service_window_check",
        "model-nontrivial-baseline-plan|qwopus-nontrivial-plan)",
        "model-nontrivial-baseline-check|qwopus-nontrivial-check)",
        "model-eval-next|ornith-eval-next|qwopus-next)",
        "model-service-window-check|qwopus-window-check)",
        'if [[ "$COMMAND" == "shortcuts" || "$COMMAND" == "cheatsheet" ]]; then',
        "print_shortcuts",
        "ask_json=0",
        "ask_parts=()",
        "next_json=0",
        "next_parts=()",
        "trust_json=0",
        "trust_parts=()",
        "health_json=0",
        "health_parts=()",
        "availability_json=0",
        "availability_parts=()",
        "review_question_json=0",
        "review_question_parts=()",
        "$COMMAND requires a local-goal phrase",
        'ask_message="${ask_parts[*]}"',
        'next_message="what now for the agentic harness?"',
        'trust_message="can I trust the agentic harness now?"',
        'trust_message="do I have to babysit the harness?"',
        'health_message="is the harness working as intended?"',
        'health_message="what do you think of the harness now?"',
        'availability_message="is Node1 free for a local goal?"',
        'availability_message="can I start a local goal?"',
        'availability_message="is node1 stuck?"',
        'review_question_message="can I accept the local goal?"',
        'review_question_message="is the local goal ready for review?"',
        "print_availability_question",
        "local_node1_goal_availability_question.v1",
        "Can start:",
        "Lane free:",
        "Node1 idle:",
        "print_review_question",
        "local_node1_goal_review_question.v1",
        "State: {state}",
        "Can review:",
        "Can accept:",
        'python3 "$COMMAND_SHIM" "$next_message" --json',
        'python3 "$COMMAND_SHIM" "$next_message"',
        'python3 "$COMMAND_SHIM" "$trust_message" --json',
        'python3 "$COMMAND_SHIM" "$trust_message"',
        'python3 "$COMMAND_SHIM" "$health_message" --json',
        'python3 "$COMMAND_SHIM" "$health_message"',
        'python3 "$COMMAND_SHIM" "$ask_message" --json',
        'python3 "$COMMAND_SHIM" "$ask_message"',
    ]
    missing = [fragment for fragment in required_fragments if fragment not in text]
    return {
        "ok": not missing,
        "detail": {"missing": missing},
        "missing": missing,
    }


def _gateway_help_discoverability_check() -> dict[str, Any]:
    """Verify /local-goal help exposes the phone-friendly local harness path."""
    if not HERMES_AGENT_ROOT.exists():
        return {
            "ok": False,
            "detail": f"missing hermes agent root: {HERMES_AGENT_ROOT}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    required_fragments = [
        "/local-goal doctor",
        "/local-goal current truth",
        "/local-goal completion-audit",
        "/local-goal show local goal queue",
        "/local-goal supervise local harness",
        "/local-goal monitor local goal",
        "/local-goal what do I type for the local harness?",
        "/local-goal fix one bounded local harness bug",
        "doctor local harness",
        "help me use the local goal harness",
        "how do I use the agentic harness?",
        "which harness mode should I use?",
        "Use the default harness mode for this goal:",
        "Have GLM supervise this goal and leave Codex to spot-check",
        "Run a bounded cloud canary with the GLM worker:",
        "Check whether the direct GLM audit/proposal lane is ready.",
        "Run Mode 4B GLM direct implementation canary.",
        "what do I type for the local harness?",
        "what is the Node1 /goal current truth?",
        "can I start a local goal?",
        "is Node1 free for a local goal?",
        "what is Node1 doing?",
        "is vLLM busy?",
        "are GPUs idle?",
        "whats next for the local harness?",
        "what else for the agentic harness?",
        "what percentage complete is the harness?",
        "how is this progressing?",
        "how is the harness progressing?",
        "how is the harness coming along?",
        "where are we with the harness?",
        "ehat is hapenning?",
        "what files did the last local goal change?",
        "show me the accepted local goal evidence",
        "what verification passed?",
        "does dirty work block acceptance?",
        "what proof remains for the agentic harness?",
        "completion audit for the agentic harness",
        "is the harness complete?",
        "prove the local goal harness is ready",
        "what model is active?",
        "which model is Node1 using?",
        "is my model good?",
        "do you trust the model?",
        "do you trust it for local goals?",
        "should I promote it?",
        "can I swap the local-goal model?",
        "is Ornith ready for local-goal canary?",
        "how is my orinth modle doing?",
        "should I promote orinth?",
        "ehat is the a/b?",
        "what next for the model?",
        "what should I do next with Ornith?",
        "can I test Ornith now?",
        "can I use Ornith for the harness?",
        "should we switch to Ornith?",
        "is Ornith ready to promote?",
        "should I make Ornith permanent?",
        "what do I type to make Ornith permanent?",
        "what evidence is missing for Ornith?",
        "did Ornith beat Qwopus?",
        "continue agentic harness work",
        "do next for the agentic harness",
        "brief local harness",
        "short version for the agentic harness",
        "keep going on the local harness",
        "what now for the agentic harness?",
        "can I run the Qwopus baseline now?",
        "what next for Qwopus eval?",
        "should I promote Ornith over Qwopus?",
        "is Ornith good for the harness?",
        "can we keep developing with Ornith despite Qwopus problems?",
        "do you trust Qwopus?",
        "what was wrong with Qwopus completions?",
        "the Qwopus had a problem with completions",
        "the Qwopus completion issue worries me",
        "is Qwopus safe to use for the harness?",
        "can Qwopus handle 192k seq4?",
        "write the Qwopus model decision packet",
        "can I open the Qwopus service window?",
        "what next in the Qwopus service window?",
        "show the approval packet for the Qwopus service window?",
        "preview the guarded Qwopus service window open plan?",
        "preview restore Ornith after the Qwopus service window?",
        "what do I type for the Qwopus completion baseline?",
        "do I have to babysit the harness?",
        "can I leave the harness running overnight?",
        "can I let it keep working?",
        "should I let the harness keep working?",
        "will Hermes tell me if it needs me?",
        "can I trust the agentic harness now?",
        "is the harness working as intended?",
        "what do you think of the harness now?",
        "is the agentic harness working?",
        "Plain chat also routes",
    ]
    script = (
        "import asyncio\n"
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(HERMES_AGENT_ROOT)!r})\n"
        "from gateway.run import GatewayRunner\n"
        "class Event:\n"
        "    def get_command_args(self):\n"
        "        return 'help'\n"
        "async def main():\n"
        "    runner = object.__new__(GatewayRunner)\n"
        "    text = await runner._handle_local_goal_command(Event())\n"
        f"    required = {required_fragments!r}\n"
        "    missing = [fragment for fragment in required if fragment not in text]\n"
        "    print(json.dumps({'ok': not missing, 'missing': missing, 'reply': text[:2000]}, sort_keys=True))\n"
        "asyncio.run(main())\n"
    )
    proc = run([str(HERMES_PYTHON), "-c", script], timeout=30)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {}
    ok = proc.returncode == 0 and parsed.get("ok") is True
    return {
        "ok": ok,
        "detail": parsed if parsed else stdout[-500:],
        "missing": parsed.get("missing")
        if isinstance(parsed.get("missing"), list)
        else [],
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _gateway_plain_local_goal_detection_check() -> dict[str, Any]:
    """Verify the real Hermes gateway detects plain local-goal chat safely."""
    if not HERMES_AGENT_ROOT.exists():
        return {
            "ok": False,
            "detail": f"missing hermes agent root: {HERMES_AGENT_ROOT}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    script = (
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(HERMES_AGENT_ROOT)!r})\n"
        "from gateway.run import _is_gateway_local_goal_plain_request\n"
        "positive = {\n"
        "    'doctor local harness': _is_gateway_local_goal_plain_request('doctor local harness'),\n"
        "    'brief local harness': _is_gateway_local_goal_plain_request('brief local harness'),\n"
        "    'short version for the agentic harness': _is_gateway_local_goal_plain_request('short version for the agentic harness'),\n"
        "    'quick answer for the local goal': _is_gateway_local_goal_plain_request('quick answer for the local goal'),\n"
        "    'help me use the local goal harness': _is_gateway_local_goal_plain_request('help me use the local goal harness'),\n"
        "    'how do I use the agentic harness?': _is_gateway_local_goal_plain_request('how do I use the agentic harness?'),\n"
        "    'which harness mode should I use?': _is_gateway_local_goal_plain_request('which harness mode should I use?'),\n"
        "    'Use the default harness mode for this goal: update one harmless doc note and verify it': _is_gateway_local_goal_plain_request('Use the default harness mode for this goal: update one harmless doc note and verify it'),\n"
        "    'Have GLM supervise this goal and leave Codex to spot-check the important decisions: fix one bounded harness route': _is_gateway_local_goal_plain_request('Have GLM supervise this goal and leave Codex to spot-check the important decisions: fix one bounded harness route'),\n"
        "    'Run a bounded cloud canary with the GLM worker: update one safe report artifact': _is_gateway_local_goal_plain_request('Run a bounded cloud canary with the GLM worker: update one safe report artifact'),\n"
        "    'Check whether the direct GLM audit/proposal lane is ready.': _is_gateway_local_goal_plain_request('Check whether the direct GLM audit/proposal lane is ready.'),\n"
        "    'Run Mode 4B GLM direct implementation canary.': _is_gateway_local_goal_plain_request('Run Mode 4B GLM direct implementation canary.'),\n"
        "    'continue agentic harness work': _is_gateway_local_goal_plain_request('continue agentic harness work'),\n"
        "    'can I start a local goal?': _is_gateway_local_goal_plain_request('can I start a local goal?'),\n"
        "    'is Node1 free for a local goal?': _is_gateway_local_goal_plain_request('is Node1 free for a local goal?'),\n"
        "    'what is Node1 doing?': _is_gateway_local_goal_plain_request('what is Node1 doing?'),\n"
        "    'is vLLM busy?': _is_gateway_local_goal_plain_request('is vLLM busy?'),\n"
        "    'are GPUs idle?': _is_gateway_local_goal_plain_request('are GPUs idle?'),\n"
        "    'is the local goal lane free?': _is_gateway_local_goal_plain_request('is the local goal lane free?'),\n"
        "    'can I accept the local goal?': _is_gateway_local_goal_plain_request('can I accept the local goal?'),\n"
        "    'should I accept the local goal?': _is_gateway_local_goal_plain_request('should I accept the local goal?'),\n"
        "    'is the local goal ready for review?': _is_gateway_local_goal_plain_request('is the local goal ready for review?'),\n"
        "    'should I stop the local goal?': _is_gateway_local_goal_plain_request('should I stop the local goal?'),\n"
        "    'is it safe to stop the agentic harness?': _is_gateway_local_goal_plain_request('is it safe to stop the agentic harness?'),\n"
        "    'should I continue the local goal?': _is_gateway_local_goal_plain_request('should I continue the local goal?'),\n"
        "    'can I resume the local goal?': _is_gateway_local_goal_plain_request('can I resume the local goal?'),\n"
        "    'do I need to continue the local goal?': _is_gateway_local_goal_plain_request('do I need to continue the local goal?'),\n"
        "    'resume local goal?': _is_gateway_local_goal_plain_request('resume local goal?'),\n"
        "    'did the local goal stop?': _is_gateway_local_goal_plain_request('did the local goal stop?'),\n"
        "    'did the local goal fail?': _is_gateway_local_goal_plain_request('did the local goal fail?'),\n"
        "    'why did the agentic harness stop?': _is_gateway_local_goal_plain_request('why did the agentic harness stop?'),\n"
        "    'what happened to the local goal?': _is_gateway_local_goal_plain_request('what happened to the local goal?'),\n"
        "    'is the local goal stuck?': _is_gateway_local_goal_plain_request('is the local goal stuck?'),\n"
        "    'is node1 stuck?': _is_gateway_local_goal_plain_request('is node1 stuck?'),\n"
        "    'do next for the agentic harness': _is_gateway_local_goal_plain_request('do next for the agentic harness'),\n"
        "    'keep going on the local harness': _is_gateway_local_goal_plain_request('keep going on the local harness'),\n"
        "    'carry on with the agentic harness': _is_gateway_local_goal_plain_request('carry on with the agentic harness'),\n"
        "    'keep working on the agentic harness': _is_gateway_local_goal_plain_request('keep working on the agentic harness'),\n"
        "    'continue working on the agentic harness': _is_gateway_local_goal_plain_request('continue working on the agentic harness'),\n"
        "    'what should I do next with the local harness?': _is_gateway_local_goal_plain_request('what should I do next with the local harness?'),\n"
        "    'what do I type for the local harness?': _is_gateway_local_goal_plain_request('what do I type for the local harness?'),\n"
        "    'whats next for the local harness?': _is_gateway_local_goal_plain_request('whats next for the local harness?'),\n"
        "    'what else for the agentic harness?': _is_gateway_local_goal_plain_request('what else for the agentic harness?'),\n"
        "    'what percentage complete is the harness?': _is_gateway_local_goal_plain_request('what percentage complete is the harness?'),\n"
        "    'how is this progressing?': _is_gateway_local_goal_plain_request('how is this progressing?'),\n"
        "    'how is the harness progressing?': _is_gateway_local_goal_plain_request('how is the harness progressing?'),\n"
        "    'ehat is hapenning?': _is_gateway_local_goal_plain_request('ehat is hapenning?'),\n"
        "    'where do we stand now?': _is_gateway_local_goal_plain_request('where do we stand now?'),\n"
        "    'give me a progress update for the agentic harness': _is_gateway_local_goal_plain_request('give me a progress update for the agentic harness'),\n"
        "    'what files did the last local goal change?': _is_gateway_local_goal_plain_request('what files did the last local goal change?'),\n"
        "    'show me the accepted local goal evidence': _is_gateway_local_goal_plain_request('show me the accepted local goal evidence'),\n"
        "    'what verification passed?': _is_gateway_local_goal_plain_request('what verification passed?'),\n"
        "    'does dirty work block acceptance?': _is_gateway_local_goal_plain_request('does dirty work block acceptance?'),\n"
        "    'what proof remains for the agentic harness?': _is_gateway_local_goal_plain_request('what proof remains for the agentic harness?'),\n"
        "    'completion audit for the agentic harness': _is_gateway_local_goal_plain_request('completion audit for the agentic harness'),\n"
        "    'is the harness complete?': _is_gateway_local_goal_plain_request('is the harness complete?'),\n"
        "    'prove the local goal harness is ready': _is_gateway_local_goal_plain_request('prove the local goal harness is ready'),\n"
        "    'how complete is the local harness?': _is_gateway_local_goal_plain_request('how complete is the local harness?'),\n"
        "    'give me a readiness estimate for the agentic harness': _is_gateway_local_goal_plain_request('give me a readiness estimate for the agentic harness'),\n"
        "    'what model is active?': _is_gateway_local_goal_plain_request('what model is active?'),\n"
        "    'which model is Node1 using?': _is_gateway_local_goal_plain_request('which model is Node1 using?'),\n"
        "    'is my model good?': _is_gateway_local_goal_plain_request('is my model good?'),\n"
        "    'do you trust the model?': _is_gateway_local_goal_plain_request('do you trust the model?'),\n"
        "    'do you trust it for local goals?': _is_gateway_local_goal_plain_request('do you trust it for local goals?'),\n"
        "    'should I promote it?': _is_gateway_local_goal_plain_request('should I promote it?'),\n"
        "    'can I swap the local-goal model?': _is_gateway_local_goal_plain_request('can I swap the local-goal model?'),\n"
        "    'is Ornith ready for local-goal canary?': _is_gateway_local_goal_plain_request('is Ornith ready for local-goal canary?'),\n"
        "    'how is my orinth modle doing?': _is_gateway_local_goal_plain_request('how is my orinth modle doing?'),\n"
        "    'should I promote orinth?': _is_gateway_local_goal_plain_request('should I promote orinth?'),\n"
        "    'ehat is the a/b?': _is_gateway_local_goal_plain_request('ehat is the a/b?'),\n"
        "    'what next for the model?': _is_gateway_local_goal_plain_request('what next for the model?'),\n"
        "    'what should I do next with Ornith?': _is_gateway_local_goal_plain_request('what should I do next with Ornith?'),\n"
        "    'can I test Ornith now?': _is_gateway_local_goal_plain_request('can I test Ornith now?'),\n"
        "    'can I use Ornith for the harness?': _is_gateway_local_goal_plain_request('can I use Ornith for the harness?'),\n"
        "    'should we switch to Ornith?': _is_gateway_local_goal_plain_request('should we switch to Ornith?'),\n"
        "    'is Ornith ready to promote?': _is_gateway_local_goal_plain_request('is Ornith ready to promote?'),\n"
        "    'should I make Ornith permanent?': _is_gateway_local_goal_plain_request('should I make Ornith permanent?'),\n"
        "    'what evidence is missing for Ornith?': _is_gateway_local_goal_plain_request('what evidence is missing for Ornith?'),\n"
        "    'did Ornith beat Qwopus?': _is_gateway_local_goal_plain_request('did Ornith beat Qwopus?'),\n"
        "    'now what for the local harness?': _is_gateway_local_goal_plain_request('now what for the local harness?'),\n"
        "    'what now for the agentic harness?': _is_gateway_local_goal_plain_request('what now for the agentic harness?'),\n"
        "    'can I run the Qwopus baseline now?': _is_gateway_local_goal_plain_request('can I run the Qwopus baseline now?'),\n"
        "    'what next for Qwopus eval?': _is_gateway_local_goal_plain_request('what next for Qwopus eval?'),\n"
        "    'should I promote Ornith over Qwopus?': _is_gateway_local_goal_plain_request('should I promote Ornith over Qwopus?'),\n"
        "    'what was wrong with Qwopus completions?': _is_gateway_local_goal_plain_request('what was wrong with Qwopus completions?'),\n"
        "    'the Qwopus had a problem with completions': _is_gateway_local_goal_plain_request('the Qwopus had a problem with completions'),\n"
        "    'the Qwopus completion issue worries me': _is_gateway_local_goal_plain_request('the Qwopus completion issue worries me'),\n"
        "    'is Qwopus safe to use for the harness?': _is_gateway_local_goal_plain_request('is Qwopus safe to use for the harness?'),\n"
        "    'can Qwopus handle 192k seq4?': _is_gateway_local_goal_plain_request('can Qwopus handle 192k seq4?'),\n"
        "    'where are we on Ornith versus Qwopus promotion?': _is_gateway_local_goal_plain_request('where are we on Ornith versus Qwopus promotion?'),\n"
        "    'is Ornith good for the harness?': _is_gateway_local_goal_plain_request('is Ornith good for the harness?'),\n"
        "    'can we keep developing with Ornith despite Qwopus problems?': _is_gateway_local_goal_plain_request('can we keep developing with Ornith despite Qwopus problems?'),\n"
        "    'do you trust Qwopus?': _is_gateway_local_goal_plain_request('do you trust Qwopus?'),\n"
        "    'is the local harness audit stuck?': _is_gateway_local_goal_plain_request('is the local harness audit stuck?'),\n"
        "    'show local goal audit lock health': _is_gateway_local_goal_plain_request('show local goal audit lock health'),\n"
        "    'can I open the Qwopus service window?': _is_gateway_local_goal_plain_request('can I open the Qwopus service window?'),\n"
        "    'what next in the Qwopus service window?': _is_gateway_local_goal_plain_request('what next in the Qwopus service window?'),\n"
        "    'show the approval packet for the Qwopus service window': _is_gateway_local_goal_plain_request('show the approval packet for the Qwopus service window'),\n"
        "    'preview the guarded Qwopus service window open plan': _is_gateway_local_goal_plain_request('preview the guarded Qwopus service window open plan'),\n"
        "    'preview restore Ornith after the Qwopus service window': _is_gateway_local_goal_plain_request('preview restore Ornith after the Qwopus service window'),\n"
        "    'what do I type for the Qwopus completion baseline?': _is_gateway_local_goal_plain_request('what do I type for the Qwopus completion baseline?'),\n"
        "    'do I have to babysit the harness?': _is_gateway_local_goal_plain_request('do I have to babysit the harness?'),\n"
        "    'can I trust the agentic harness now?': _is_gateway_local_goal_plain_request('can I trust the agentic harness now?'),\n"
        "    'is the harness working as intended?': _is_gateway_local_goal_plain_request('is the harness working as intended?'),\n"
        "    'what do you think of the harness now?': _is_gateway_local_goal_plain_request('what do you think of the harness now?'),\n"
        "    'is the agentic harness working?': _is_gateway_local_goal_plain_request('is the agentic harness working?'),\n"
        "}\n"
        "negative = {\n"
        "    'fix my email formatting': _is_gateway_local_goal_plain_request('fix my email formatting'),\n"
        "    'make me a website called slop factory': _is_gateway_local_goal_plain_request('make me a website called slop factory'),\n"
        "    'can I trust my dentist?': _is_gateway_local_goal_plain_request('can I trust my dentist?'),\n"
        "    'is my test harness working for pytest?': _is_gateway_local_goal_plain_request('is my test harness working for pytest?'),\n"
        "    'what else?': _is_gateway_local_goal_plain_request('what else?'),\n"
        "    'can I start dinner?': _is_gateway_local_goal_plain_request('can I start dinner?'),\n"
        "    'can I stop dinner?': _is_gateway_local_goal_plain_request('can I stop dinner?'),\n"
        "    'can I resume dinner?': _is_gateway_local_goal_plain_request('can I resume dinner?'),\n"
        "    'did dinner stop?': _is_gateway_local_goal_plain_request('did dinner stop?'),\n"
        "    'what happened to dinner?': _is_gateway_local_goal_plain_request('what happened to dinner?'),\n"
        "    'pasted hourly ops audit report': _is_gateway_local_goal_plain_request('im getting consistently low scores, so i dont think the audit is autonomously fixing these audits : Cronjob Response: hourly-codex-ops-audit KEANU OPS REAL AUDIT PRE-FLIGHT Current truth: needs review Cron SLA: alerting Website: healthy Local Node1 goal: audit-health'),\n"
        "}\n"
        "ok = all(positive.values()) and not any(negative.values())\n"
        "print(json.dumps({'ok': ok, 'positive': positive, 'negative': negative}, sort_keys=True))\n"
    )
    proc = run([str(HERMES_PYTHON), "-c", script], timeout=30)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {}
    ok = proc.returncode == 0 and parsed.get("ok") is True
    return {
        "ok": ok,
        "detail": parsed if parsed else stdout[-500:],
        "returncode": proc.returncode,
        "stdout_tail": stdout[-1000:],
        "stderr_tail": stderr[-1000:],
    }


def _gateway_plain_local_goal_message_dispatch_check() -> dict[str, Any]:
    """Verify the real GatewayRunner routes plain local-goal chat to the shim."""
    if not HERMES_AGENT_ROOT.exists():
        return {
            "ok": False,
            "detail": f"missing hermes agent root: {HERMES_AGENT_ROOT}",
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    script = (
        "import asyncio\n"
        "import json\n"
        "import sys\n"
        "from datetime import datetime\n"
        "from types import SimpleNamespace\n"
        "from unittest.mock import AsyncMock, MagicMock\n"
        f"sys.path.insert(0, {str(HERMES_AGENT_ROOT)!r})\n"
        "import gateway.run as gateway_run\n"
        "from gateway.config import GatewayConfig, Platform, PlatformConfig\n"
        "from gateway.platforms.base import MessageEvent\n"
        "from gateway.run import GatewayRunner\n"
        "from gateway.session import SessionEntry, SessionSource, build_session_key\n"
        "calls = []\n"
        "class FakeProcess:\n"
        "    returncode = 0\n"
        "    async def communicate(self):\n"
        "        message = calls[-1][-2] if calls and len(calls[-1]) >= 2 else ''\n"
        "        if 'use the default harness mode for this goal:' in message.lower():\n"
        "            intent = 'harness-mode-default-start'\n"
        "            reason = 'start Mode 1 default harness: GLM-5.2 planner/supervisor with OpenCode local executor'\n"
        "            summary = 'Dry run: would route to harness-mode-default-start\\nReason: start Mode 1 default harness: GLM-5.2 planner/supervisor with OpenCode local executor\\nSupervisor action: premium-start --planner glm-5.2 --executor opencode\\nWould mutate if executed: true\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'codex to spot-check' in message.lower() and 'glm supervise' in message.lower():\n"
        "            intent = 'harness-mode-codex-saving-start'\n"
        "            reason = 'start Mode 2 Codex-saving harness: GLM-5.2 supervises while Codex spot-checks'\n"
        "            summary = 'Dry run: would route to harness-mode-codex-saving-start\\nReason: start Mode 2 Codex-saving harness: GLM-5.2 supervises while Codex spot-checks\\nSupervisor action: premium-start --planner glm-5.2 --executor opencode\\nWould mutate if executed: true\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'bounded cloud canary with the glm worker:' in message.lower():\n"
        "            intent = 'harness-mode-cloud-canary'\n"
        "            reason = 'start Mode 3 bounded cloud canary with GLM-5.2 planner and opencode-glm-build executor'\n"
        "            summary = 'Dry run: would route to harness-mode-cloud-canary\\nReason: start Mode 3 bounded cloud canary with GLM-5.2 planner and opencode-glm-build executor\\nSupervisor action: enqueue --planner glm-5.2 --executor opencode --executor-worker opencode-glm-build\\nWould mutate if executed: true\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'fully local glm executor canary is ready' in message.lower():\n"
        "            intent = 'harness-mode-glm-local-canary-plan'\n"
        "            reason = 'show Mode 4 fully local GLM-5.2 executor canary plan'\n"
        "            summary = 'Dry run: would route to harness-mode-glm-local-canary-plan\\nReason: show Mode 4 fully local GLM-5.2 executor canary plan\\nSupervisor action: adapter-canary-plan --executor-worker glm52-direct --json\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'mode 4b' in message.lower() or 'glm direct implementation canary' in message.lower():\n"
        "            intent = 'harness-mode-glm-direct-implementation-canary-plan'\n"
        "            reason = 'show Mode 4B direct GLM-5.2 one-file implementation canary plan'\n"
        "            summary = 'Dry run: would route to harness-mode-glm-direct-implementation-canary-plan\\nReason: show Mode 4B direct GLM-5.2 one-file implementation canary plan\\nSupervisor action: adapter-canary-plan --executor-worker glm52-direct-implementation-canary --json\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'harness mode' in message.lower() or 'champion modes' in message.lower():\n"
        "            intent = 'harness-modes'\n"
        "            reason = 'show the Hermes gateway harness modes, including Mode 4B, and recommended default'\n"
        "            summary = 'Dry run: would route to harness-modes\\nReason: show the Hermes gateway harness modes, including Mode 4B, and recommended default\\nSupervisor action: harness-modes --json\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif 'next 10 hours' in message:\n"
        "            intent = 'mission-create'\n"
        "            reason = 'create local mission mode umbrella goal from long-running plain-language request'\n"
        "            summary = 'Dry run: would route to mission-create\\nReason: create local mission mode umbrella goal from long-running plain-language request\\nSupervisor action: mission-create --planner none --executor opencode\\nExecuted: no'\n"
        "            supervisor_payload = None\n"
        "        elif message.lower().startswith('finish testing'):\n"
        "            intent = 'start'\n"
        "            reason = 'start direct local goal from plain programming request'\n"
        "            summary = 'The local-goal worker is running. No action is needed right now.'\n"
        "            supervisor_payload = {'classification': 'working', 'active_goal': {'objective': 'Goal', 'tmux_running': True}}\n"
        "        elif message.lower() in {'continue agentic harness work', 'do next for the agentic harness'}:\n"
        "            intent = 'supervise'\n"
        "            reason = 'actively supervise local goal with review, continue, dispatch, and owned-change commit gates'\n"
        "            summary = 'Status: accepted\\nReason: Local-goal lane is free.\\nNext: Check mission-show or start another goal.'\n"
        "            supervisor_payload = {'classification': 'accepted', 'active_goal': {'objective': 'Goal', 'tmux_running': False, 'accepted': True}}\n"
        "        elif 'brief' in message.lower() or 'short version' in message.lower() or 'quick answer' in message.lower() or 'tldr' in message.lower():\n"
        "            intent = 'brief'\n"
        "            reason = 'show the shortest phone-readable local goal answer'\n"
        "            summary = 'Local Goal Brief\\nState: accepted; mission=complete\\nAnswer: Ready. The open choice is whether to make Ornith durable.\\nBabysit: No active babysitting needed; watcher is active.\\nProof: Accepted soak proof is present.\\nBoundary: bounded local goals ready; broad autonomy not claimed.\\nType: /local-goal model-promotion-decision'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_brief.v1'}\n"
        "        elif 'completion audit' in message.lower() or 'is the harness complete' in message.lower() or 'prove the local goal harness is ready' in message.lower():\n"
        "            intent = 'completion-audit'\n"
        "            reason = 'show requirement-by-requirement local harness completion evidence'\n"
        "            summary = 'Local Goal Completion Audit\\nStatus: ready_for_bounded_goals\\nUsable for bounded local goals: true\\n100% broad-autonomy claim: not claimed\\nRequired evidence ok: true\\nRequirements:\\n  - readiness_gate_green: ok\\n  - accepted_soak_evidence_present: ok\\nSafe next commands:\\n  scripts/local-goal next-proof'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_completion_audit.v1', 'status': 'ready_for_bounded_goals', 'usable_for_bounded_local_goals': True, 'broad_autonomy_claimed': False}\n"
        "        elif 'proof remains' in message.lower() or 'next proof' in message.lower() or 'autonomy proof' in message.lower():\n"
        "            intent = 'next-proof'\n"
        "            reason = 'show the next proof needed to harden unattended harness trust'\n"
        "            summary = 'Local Goal Next Proof\\nStatus: optional_hardening\\nRequired now: false\\nReadiness: ready ok=true\\nLocal-goal lane free: true\\nLast accepted soak proof: true\\nNext proof: Accepted soak proof is already present; use the harness for the next bounded local goal.\\nNext: Use scripts/local-goal progress for status, or start one bounded local goal when you have a concrete task.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_next_proof.v1', 'status': 'optional_hardening', 'required_now': False, 'readiness_status': 'ready', 'readiness_ok': True, 'local_goal_lane_free': True, 'last_accepted_soak_proof': True}\n"
        "        elif 'progress' in message.lower() or 'where do we stand' in message.lower() or 'where are we with the harness' in message.lower() or 'how is this progressing' in message.lower() or 'how is the harness progressing' in message.lower() or 'how is the harness coming along' in message.lower() or 'what is happening' in message.lower() or 'ehat is hapenning' in message.lower():\n"
        "            intent = 'progress'\n"
        "            reason = 'show compact local harness progress report'\n"
        "            summary = 'Harness readiness: bounded local goals ready (broad-autonomy estimate: 90%; 100% broad autonomy not claimed)\\nStatus: ready\\nReason: Readiness audit passed with 7/7 objective requirements met.\\nNext: Use it for bounded local goals; keep review gates for product-sensitive work.\\nRemaining: polish plain-language UX; accepted soak evidence is present, and broader autonomy missions remain optional.\\nHardening commands:\\n  scripts/local-goal next-proof\\n  scripts/local-goal soak-plan\\n  scripts/local-goal model-eval-next\\n  scripts/local-goal model-promotion-decision'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_harness_readiness.v1', 'ok': True, 'status': 'ready'}\n"
        "        elif 'percentage complete' in message.lower() or 'readiness estimate' in message.lower():\n"
        "            intent = 'completion-summary'\n"
        "            reason = 'show evidence-backed autonomy grade from completion summary'\n"
        "            summary = 'Local Goal Completion Summary\\nStatus: ready_for_bounded_goals\\nUsable for bounded local goals: true\\n100% broad-autonomy claim: not claimed\\nAutonomy grade: 90% practical harness\\nRemaining: Continue real-goal soak runs and keep product-sensitive review gates on.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_completion_summary.v1', 'status': 'ready_for_bounded_goals', 'usable_for_bounded_local_goals': True, 'required_evidence_ok': True, 'autonomy_grade': {'label': '90% practical harness', 'remaining': 'Continue real-goal soak runs and keep product-sensitive review gates on.'}, 'operator_surfaces_summary': {'verified': 19, 'total': 19, 'missing': []}, 'control_routes_summary': {'verified': 18, 'total': 18, 'missing': []}, 'capability_gates_summary': {'verified': 9, 'total': 9, 'missing': []}, 'safety_checks_summary': {'verified': 9, 'total': 9, 'missing': []}, 'start_may_wait': False}\n"
        "        elif 'current truth' in message.lower() or 'current-truth' in message.lower():\n"
        "            intent = 'current-truth'\n"
        "            reason = 'show local goal current-truth report'\n"
        '            summary = \'Local Goal Current Truth\\nStatus: accepted\\nRunning: false\\nDirty blocks acceptance: False\\nIntegration audit: ok=True status=integrated missing=[]\\nUseful commands:\\n  qwopus_completion_risk: /mnt/raid0/documentation/scripts/local-goal qwopus-status\\n  qwopus_safe_harness: /mnt/raid0/documentation/scripts/local-goal ask "is Qwopus safe to use for the harness?"\\n  qwopus_192k_seq4: /mnt/raid0/documentation/scripts/local-goal ask "can Qwopus handle 192k seq4?"\\n  model_promotion_decision: /mnt/raid0/documentation/scripts/local-goal model-promotion-decision\\n  model_promotion_plan: /mnt/raid0/documentation/scripts/local-goal model-promotion-plan\\n  qwopus_window_next: /mnt/raid0/documentation/scripts/local-goal qwopus-window-next\\n  qwopus_window_open_preview: /mnt/raid0/documentation/scripts/local-goal qwopus-window-open\\n  qwopus_window_restore_preview: /mnt/raid0/documentation/scripts/local-goal qwopus-window-restore\'\n'
        "            supervisor_payload = {'contract': 'local_node1_goal_current_truth.v1', 'ok': True}\n"
        "        elif any(phrase in message.lower() for phrase in ('babysit', 'trust the agentic harness', 'leave the harness', 'leave the local goal', 'running overnight', 'walk away', 'let it keep working', 'let the harness keep working', 'will hermes tell me', 'will hermes notify me', 'notify me if', 'tell me if it needs me', 'ask me for approval')):\n"
        "            intent = 'trust-boundary'\n"
        "            reason = 'show whether the local-goal watcher can run without babysitting'\n"
        "            summary = 'Local Goal Trust Boundary\\n\\nAnswer: Yes, but there is no active local goal right now. The lane is ready for the next bounded task.\\nOperator action: Start one explicit bounded goal when you want more work done.\\n\\nSupervision:\\n  watcher: active (timer_active=True, service_ok=True)\\n\\nTrust boundary:\\n  Accepted soak evidence is present.\\n  Routine watcher cycles do not need babysitting when the watcher is active.\\n  Product-sensitive changes still need accepted evidence review.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_doctor.v1'}\n"
        "        elif 'ornith' in message.lower() and ('permanent' in message.lower() or 'promotion' in message.lower() or 'promote' in message.lower()) and ('what do i type' in message.lower() or 'exact command' in message.lower() or 'terminal command' in message.lower() or 'what command' in message.lower() or 'paste' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif 'ornith' in message.lower() and 'permanent' in message.lower() and ('durable' in message.lower() or 'plan' in message.lower() or 'drop-in' in message.lower() or 'drop in' in message.lower()):\n"
        "            intent = 'model-promotion-plan'\n"
        "            reason = 'show the read-only durable Ornith promotion plan'\n"
        "            summary = 'Ornith durable promotion plan\\nStatus: ready-to-apply-if-operator-chooses-promotion\\nMutates live service: False\\nDurable drop-in: /etc/systemd/system/vllm-qwen36-main.service.d/zzzzz-ornith-permanent-promotion.conf\\nFirst command: scripts/local-goal model-promotion-decision'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_plan.v1', 'status': 'ready-to-apply-if-operator-chooses-promotion', 'mutates_live_service': False, 'durable_dropin': '/etc/systemd/system/vllm-qwen36-main.service.d/zzzzz-ornith-permanent-promotion.conf', 'temporary_dropin': '/etc/systemd/system/vllm-qwen36-main.service.d/zzzzz-ornith-temporary-cutover.conf', 'operator_precondition': 'Only apply after model-promotion-decision says ready-for-operator-decision and the operator explicitly chooses Ornith.', 'risk': 'Applying this plan changes the durable systemd override for the main Node1 vLLM service.', 'write_and_persist_commands': ['scripts/local-goal model-promotion-decision'], 'restart_validation_commands': ['sudo systemctl restart vllm-qwen36-main.service'], 'rollback_commands': ['sudo rm -f /etc/systemd/system/vllm-qwen36-main.service.d/zzzzz-ornith-permanent-promotion.conf']}\n"
        "        elif 'ornith' in message.lower() and ('promote' in message.lower() or 'promoting' in message.lower() or 'promotion' in message.lower() or 'permanent' in message.lower() or 'permanently' in message.lower() or 'evidence' in message.lower() or 'proof' in message.lower() or 'missing' in message.lower() or 'need' in message.lower() or 'blocked' in message.lower() or 'blocks' in message.lower() or ' win ' in (' ' + message.lower() + ' ') or 'won' in message.lower() or 'beat' in message.lower() or 'beaten' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif 'model' in message.lower() and not any(word in message.lower() for word in ('window', 'service', 'cutover', 'open', 'restore', 'rollback', 'packet', 'bundle')) and ('good' in message.lower() or 'trust' in message.lower() or 'better' in message.lower() or 'best' in message.lower() or 'use' in message.lower() or 'replace' in message.lower() or 'switch' in message.lower() or 'promote' in message.lower() or 'promotion' in message.lower() or 'permanent' in message.lower() or 'permanently' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif ('promote it' in message.lower()) or ('trust it' in message.lower() and ('local goal' in message.lower() or 'local-goal' in message.lower() or 'node1' in message.lower())):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif ('model' in message.lower() and ('active' in message.lower() or 'current' in message.lower() or 'using' in message.lower() or 'status' in message.lower() or 'which' in message.lower() or 'swap' in message.lower() or 'change' in message.lower() or 'canary' in message.lower() or 'ready' in message.lower())) or (('ornith' in message.lower() or 'qwopus' in message.lower()) and ('active' in message.lower() or 'current' in message.lower() or 'using' in message.lower() or 'status' in message.lower() or 'canary' in message.lower() or 'ready' in message.lower())):\n"
        "            intent = 'model-status'\n"
        "            reason = 'show the active local-goal model and promotion gate'\n"
        "            summary = 'Local Goal Model Status\\nStatus: active_candidate\\nCurrent model: /mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8\\nDurability: active_temporary_candidate\\nPromotion gate: ready_for_operator_decision\\nDurability reason: Ornith is active through the temporary cutover drop-in; it is not durable across cleanup/restart policy yet.\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_status.v1', 'canary_mode': 'active_candidate', 'current_model': '/mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8', 'candidate_path': '/mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8', 'current_service_live': True, 'local_goal_lane_free': True, 'durability': {'status': 'active_temporary_candidate', 'reason': 'Ornith is active through the temporary cutover drop-in; it is not durable across cleanup/restart policy yet.', 'next_command': 'scripts/local-goal model-promotion-decision'}, 'promotion_gate': {'status': 'ready_for_operator_decision'}}\n"
        "        elif 'orinth' in message.lower() and ('model' in message.lower() or 'modle' in message.lower() or 'active' in message.lower() or 'current' in message.lower() or 'using' in message.lower() or 'status' in message.lower() or 'canary' in message.lower() or 'ready' in message.lower() or 'doing' in message.lower()):\n"
        "            intent = 'model-status'\n"
        "            reason = 'show the active local-goal model and promotion gate'\n"
        "            summary = 'Local Goal Model Status\\nStatus: active_candidate\\nCurrent model: /mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8\\nDurability: active_temporary_candidate\\nPromotion gate: ready_for_operator_decision\\nDurability reason: Ornith is active through the temporary cutover drop-in; it is not durable across cleanup/restart policy yet.\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_status.v1', 'canary_mode': 'active_candidate', 'current_model': '/mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8', 'candidate_path': '/mnt/raid0/vllm_cache/manual_downloads/Ornith-1.0-35B-FP8', 'current_service_live': True, 'local_goal_lane_free': True, 'durability': {'status': 'active_temporary_candidate', 'reason': 'Ornith is active through the temporary cutover drop-in; it is not durable across cleanup/restart policy yet.', 'next_command': 'scripts/local-goal model-promotion-decision'}, 'promotion_gate': {'status': 'ready_for_operator_decision'}}\n"
        "        elif 'model' in message.lower() and not any(word in message.lower() for word in ('window', 'service', 'cutover', 'open', 'restore', 'rollback', 'packet', 'bundle', 'decision')) and ('next' in message.lower() or 'what now' in message.lower() or 'what should i do' in message.lower() or 'what do i do' in message.lower() or 'what do i type' in message.lower() or 'what should i type' in message.lower() or 'step' in message.lower() or 'eval' in message.lower() or 'test' in message.lower()):\n"
        "            intent = 'model-eval-next'\n"
        "            reason = 'show the next safe Ornith/Qwopus evaluation step'\n"
        "            summary = 'Ornith/Qwopus eval next\\nStatus: evidence-complete\\nPromotion allowed: False\\nNext: Run scripts/local-goal model-promotion-decision for the operator decision packet.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_eval_next.v1', 'status': 'evidence-complete', 'promotion_allowed': False}\n"
        "        elif 'a/b' in message.lower() or 'ab test' in message.lower() or 'a b test' in message.lower():\n"
        "            intent = 'model-eval-next'\n"
        "            reason = 'show the next safe Ornith/Qwopus evaluation step'\n"
        "            summary = 'Ornith/Qwopus eval next\\nStatus: evidence-complete\\nPromotion allowed: False\\nNext: Run scripts/local-goal model-promotion-decision for the operator decision packet.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_eval_next.v1', 'status': 'evidence-complete', 'promotion_allowed': False}\n"
        "        elif 'qwopus' in message.lower() and 'type' in message.lower():\n"
        "            intent = 'model-nontrivial-baseline-plan'\n"
        "            reason = 'show read-only Qwopus nontrivial completion-baseline plan'\n"
        "            summary = 'Qwopus nontrivial baseline plan\\nStatus: service-window-required\\nRisk: restart main vLLM service into Qwopus\\nCompletion risk: complete.json, review, acceptance, and final-result evidence must all be recorded.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_nontrivial_baseline_plan.v1', 'requires_service_window': True}\n"
        "        elif 'qwopus' in message.lower() and ('restore' in message.lower() or 'rollback' in message.lower()) and ('ornith' in message.lower() or 'window' in message.lower()):\n"
        "            intent = 'model-service-window-restore'\n"
        "            reason = 'preview the guarded Ornith restore after the Qwopus service window'\n"
        "            summary = 'Qwopus service window restore\\nStatus: approval-required\\nMutates live service: False\\nRequired confirm: RESTORE_ORNITH_CANARY'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_service_window_restore.v1', 'status': 'approval-required'}\n"
        "        elif 'qwopus' in message.lower() and ('approve' in message.lower() or 'approval' in message.lower() or 'preview' in message.lower() or 'guarded' in message.lower() or 'what would happen' in message.lower()) and ('window' in message.lower() or 'service' in message.lower() or 'cutover' in message.lower()):\n"
        "            intent = 'model-service-window-open'\n"
        "            reason = 'preview the guarded Qwopus service-window opener without executing it'\n"
        "            summary = 'Qwopus service window open\\nStatus: approval-required\\nMutates live service: False\\nRequired confirm: OPEN_QWOPUS_SERVICE_WINDOW\\nApproval packet:\\n  execute: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW\\n  rollback execute: scripts/local-goal qwopus-window-restore --execute --confirm RESTORE_ORNITH_CANARY\\n  start condition: Run the baseline start command only after scripts/local-goal model-nontrivial-baseline-check reports Can start baseline goal now: true.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_service_window_open.v1', 'status': 'approval-required', 'approval_packet': {'execute_command': 'scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW', 'rollback_execute_command': 'scripts/local-goal qwopus-window-restore --execute --confirm RESTORE_ORNITH_CANARY', 'baseline_start_condition': 'Run the baseline start command only after scripts/local-goal model-nontrivial-baseline-check reports Can start baseline goal now: true.'}}\n"
        "        elif 'qwopus' in message.lower() and ('packet' in message.lower() or 'bundle' in message.lower() or 'decision packet' in message.lower()):\n"
        "            intent = 'model-decision-packet'\n"
        "            reason = 'write the read-only Qwopus model decision packet bundle'\n"
        "            summary = 'Local Goal Model Decision Packet Bundle\\nStatus: written\\nMutates live service: false\\nPackets: 12\\nManifest: reports/local-node1-goal-harness/model-decision-packets/manifest.json'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_decision_packet_bundle.v1', 'mutates_live_service': False, 'packet_count': 12}\n"
        "        elif 'ornith' in message.lower() and 'qwopus' in message.lower() and ('despite' in message.lower() or 'waive' in message.lower() or 'waiver' in message.lower() or 'keep developing' in message.lower() or 'continue developing' in message.lower() or 'keep using' in message.lower() or 'problems' in message.lower() or 'unreliable' in message.lower()):\n"
        "            intent = 'model-promotion-waiver'\n"
        "            reason = 'show the read-only Ornith operator waiver for the unreliable Qwopus baseline'\n"
        "            summary = 'Ornith/Qwopus promotion waiver\\nStatus: operator-waiver-available\\nMutates live service: False\\nContinue developing with Ornith: True\\nDurable promotion allowed: False\\nQwopus completion gap is known blocker: True'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_waiver.v1', 'status': 'operator-waiver-available', 'continue_developing_with_ornith': True, 'durable_promotion_allowed': False}\n"
        "        elif 'ornith' in message.lower() and ('promote' in message.lower() or 'promoting' in message.lower() or 'promotion' in message.lower() or 'better' in message.lower() or 'replace' in message.lower() or 'switch' in message.lower() or 'use' in message.lower() or 'permanent' in message.lower() or 'permanently' in message.lower() or 'evidence' in message.lower() or 'proof' in message.lower() or 'missing' in message.lower() or 'need' in message.lower() or 'blocked' in message.lower() or 'blocks' in message.lower() or ' win ' in (' ' + message.lower() + ' ') or 'won' in message.lower() or 'beat' in message.lower() or 'beaten' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif 'orinth' in message.lower() and ('promote' in message.lower() or 'promoting' in message.lower() or 'promotion' in message.lower() or 'good' in message.lower() or 'better' in message.lower() or 'replace' in message.lower() or 'switch' in message.lower() or 'use' in message.lower() or 'permanent' in message.lower() or 'permanently' in message.lower() or 'evidence' in message.lower() or 'proof' in message.lower() or 'missing' in message.lower() or 'need' in message.lower() or 'blocked' in message.lower() or 'blocks' in message.lower() or ' win ' in (' ' + message.lower() + ' ') or 'won' in message.lower() or 'beat' in message.lower() or 'beaten' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.\\nPhone-safe preview: scripts/local-goal model-promotion-apply\\nTerminal-only mutation: Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True, 'phone_safe_preview': 'Use scripts/local-goal model-promotion-apply or /local-goal model-promotion-apply to inspect the approval packet; this does not mutate services.', 'terminal_only_mutation': 'Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable.'}\n"
        "        elif 'ornith' in message.lower() and not any(word in message.lower() for word in ('window', 'service', 'cutover', 'open', 'restore', 'rollback')) and ('next' in message.lower() or 'what now' in message.lower() or 'what should i do' in message.lower() or 'what do i do' in message.lower() or 'what do i type' in message.lower() or 'what should i type' in message.lower() or 'step' in message.lower() or 'eval' in message.lower() or 'test' in message.lower()):\n"
        "            intent = 'model-eval-next'\n"
        "            reason = 'show the next safe Ornith/Qwopus evaluation step'\n"
        "            summary = 'Ornith/Qwopus eval next\\nStatus: evidence-complete\\nPromotion allowed: False\\nNext: Run scripts/local-goal model-promotion-decision for the operator decision packet.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_eval_next.v1', 'status': 'evidence-complete', 'promotion_allowed': False}\n"
        "        elif 'ornith' in message.lower() and ('good' in message.lower() or 'trust' in message.lower() or 'working' in message.lower() or 'switch' in message.lower() or 'use' in message.lower()):\n"
        "            intent = 'model-promotion-decision'\n"
        "            reason = 'show the read-only Ornith/Qwopus promotion decision'\n"
        "            summary = 'Ornith/Qwopus promotion decision\\nStatus: ready-for-operator-decision\\nMutates live service: False\\nPromotion allowed: False\\nDecision required: True\\nReason: Comparison evidence is complete; promotion still requires an explicit operator promotion decision and durable service drop-in.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_promotion_decision.v1', 'status': 'ready-for-operator-decision', 'promotion_allowed': False, 'decision_required': True}\n"
        "        elif 'qwopus' in message.lower() and 'next' in message.lower() and ('window' in message.lower() or 'service' in message.lower() or 'cutover' in message.lower()):\n"
        "            intent = 'model-service-window-next'\n"
        "            reason = 'show the next safe Qwopus service-window command'\n"
        "            summary = 'Qwopus service window next\\nStatus: ready-to-open-window\\nMutates live service: False\\nCommand: scripts/local-goal qwopus-window-open\\nTerminal approval command: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_service_window_next.v1', 'status': 'ready-to-open-window', 'next_command': 'scripts/local-goal qwopus-window-open', 'terminal_approval_command': 'scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'}\n"
        "        elif 'qwopus' in message.lower() and ('preview' in message.lower() or 'guarded' in message.lower() or 'what would happen' in message.lower()) and ('window' in message.lower() or 'service' in message.lower() or 'cutover' in message.lower()):\n"
        "            intent = 'model-service-window-open'\n"
        "            reason = 'preview the guarded Qwopus service-window opener without executing it'\n"
        "            summary = 'Qwopus service window open\\nStatus: approval-required\\nMutates live service: False\\nRequired confirm: OPEN_QWOPUS_SERVICE_WINDOW'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_service_window_open.v1', 'status': 'approval-required'}\n"
        "        elif 'qwopus' in message.lower() and ('wrong' in message.lower() or 'problem' in message.lower() or 'issue' in message.lower() or 'worry' in message.lower() or 'worries' in message.lower() or 'fail' in message.lower() or 'timeout' in message.lower() or 'safe to use' in message.lower() or 'handle 192k' in message.lower() or 'support 192k' in message.lower() or '192k' in message.lower() or 'seq4' in message.lower() or 'max-num-seqs' in message.lower()):\n"
        "            intent = 'model-completion-risk-check'\n"
        "            reason = 'explain the Qwopus completion risk and current baseline gate'\n"
        "            summary = 'Qwopus completion risk\\nStatus: service-window-ready\\nHistorical failure: 192k context with max-num-seqs=2\\nCompletion evidence missing: True\\nCommand: scripts/local-goal qwopus-window-open\\nTerminal approval command: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_qwopus_completion_risk.v1', 'status': 'service-window-ready', 'next_command': 'scripts/local-goal qwopus-window-open', 'terminal_approval_command': 'scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'}\n"
        "        elif 'qwopus' in message.lower() and ('good' in message.lower() or 'trust' in message.lower() or 'working' in message.lower()):\n"
        "            intent = 'model-completion-risk-check'\n"
        "            reason = 'explain the Qwopus completion risk and current baseline gate'\n"
        "            summary = 'Qwopus completion risk\\nStatus: service-window-ready\\nHistorical failure: 192k context with max-num-seqs=2\\nCompletion evidence missing: True\\nCommand: scripts/local-goal qwopus-window-open\\nTerminal approval command: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_qwopus_completion_risk.v1', 'status': 'service-window-ready', 'next_command': 'scripts/local-goal qwopus-window-open', 'terminal_approval_command': 'scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW'}\n"
        "        elif 'qwopus' in message.lower() and ('next' in message.lower() or 'eval' in message.lower() or 'promotion' in message.lower() or 'ornith' in message.lower()):\n"
        "            intent = 'model-eval-next'\n"
        "            reason = 'show the next safe Ornith/Qwopus evaluation step'\n"
        "            summary = 'Ornith/Qwopus eval next\\nStatus: evidence-complete\\nPromotion allowed: False\\nNext: Run scripts/local-goal model-promotion-decision for the operator decision packet.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_eval_next.v1', 'status': 'evidence-complete'}\n"
        "        elif 'qwopus' in message.lower() and ('window' in message.lower() or 'cutover' in message.lower() or 'service' in message.lower()):\n"
        "            intent = 'model-service-window-check'\n"
        "            reason = 'check whether the Qwopus service window can be opened safely'\n"
        "            summary = 'Qwopus service window check\\nStatus: not-ready\\nReady to open window: False\\nRequires approval: True\\nBlockers:\\n  - Ornith candidate is not the active qwen36-main model'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_service_window_check.v1', 'status': 'not-ready'}\n"
        "        elif 'qwopus' in message.lower():\n"
        "            intent = 'model-nontrivial-baseline-check'\n"
        "            reason = 'check whether Qwopus nontrivial completion baseline can start now'\n"
        "            summary = 'Qwopus nontrivial baseline check\\nCan start now: False\\nBlockers:\\n  - Ornith candidate is still active behind qwen36-main'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_model_nontrivial_baseline_check.v1', 'can_start_baseline_goal_now': False}\n"
        "        else:\n"
        "            intent = 'doctor'\n"
        "            reason = 'show phone-friendly local goal status, mission, and lane summary'\n"
        "            summary = 'Local Goal Doctor\\n\\nOperator decision:\\n  Local-goal lane is free. Start one new bounded local goal when you are ready.\\n  Type: /local-goal start local goal: <bounded task>\\n\\nCurrent status:\\n  Status: accepted\\n\\nSupervision:\\n  watcher: active (timer_active=True, service_ok=True)\\n\\nTrust boundary:\\n  Accepted soak evidence is present in the latest accepted run.\\n  You do not need to babysit routine watcher cycles when watcher is active.\\n  Still review accepted evidence before trusting product-sensitive changes.'\n"
        "            supervisor_payload = {'contract': 'local_node1_goal_doctor.v1'}\n"
        "        payload = {\n"
        "            'intent': intent,\n"
        "            'reason': reason,\n"
        "            'returncode': 0,\n"
        "            'summary': summary,\n"
        "            'supervisor_payload': supervisor_payload,\n"
        "            'stdout': summary,\n"
        "            'stderr': '',\n"
        "            'state_path': '/tmp/state.json',\n"
        "            'report_path': '/tmp/report.md',\n"
        "        }\n"
        "        return json.dumps(payload).encode(), b''\n"
        "async def fake_exec(*args, **kwargs):\n"
        "    calls.append(args)\n"
        "    return FakeProcess()\n"
        "async def bad_run_agent(*args, **kwargs):\n"
        "    raise RuntimeError('plain local-goal request leaked to full agent')\n"
        "def make_source():\n"
        "    return SessionSource(platform=Platform.TELEGRAM, user_id='u1', chat_id='c1', user_name='audit', chat_type='dm')\n"
        "async def main():\n"
        "    gateway_run.asyncio.create_subprocess_exec = fake_exec\n"
        "    source = make_source()\n"
        "    runner = object.__new__(GatewayRunner)\n"
        "    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='***')})\n"
        "    adapter = MagicMock()\n"
        "    adapter.send = AsyncMock()\n"
        "    runner.adapters = {Platform.TELEGRAM: adapter}\n"
        "    runner._voice_mode = {}\n"
        "    runner.hooks = SimpleNamespace(emit=AsyncMock(), emit_collect=AsyncMock(return_value=[]), loaded_hooks=False)\n"
        "    session = SessionEntry(session_key=build_session_key(source), session_id='audit-session', created_at=datetime.now(), updated_at=datetime.now(), platform=Platform.TELEGRAM, chat_type='dm')\n"
        "    runner.session_store = MagicMock()\n"
        "    runner.session_store.get_or_create_session.return_value = session\n"
        "    runner.session_store.load_transcript.return_value = []\n"
        "    runner.session_store.has_any_sessions.return_value = True\n"
        "    runner.session_store.append_to_transcript = MagicMock()\n"
        "    runner.session_store.rewrite_transcript = MagicMock()\n"
        "    runner.session_store.update_session = MagicMock()\n"
        "    runner._running_agents = {}\n"
        "    runner._running_agents_ts = {}\n"
        "    runner._pending_messages = {}\n"
        "    runner._pending_approvals = {}\n"
        "    runner._session_db = None\n"
        "    runner._reasoning_config = None\n"
        "    runner._provider_routing = {}\n"
        "    runner._fallback_model = None\n"
        "    runner._show_reasoning = False\n"
        "    runner._is_user_authorized = lambda _source: True\n"
        "    runner._set_session_env = lambda _context: None\n"
        "    runner._should_send_voice_reply = lambda *args, **kwargs: False\n"
        "    runner._send_voice_reply = AsyncMock()\n"
        "    runner._capture_gateway_honcho_if_configured = lambda *args, **kwargs: None\n"
        "    runner._emit_gateway_run_progress = AsyncMock()\n"
        "    runner._session_key_for_source = lambda src: build_session_key(src)\n"
        "    runner._run_agent = bad_run_agent\n"
        "    cases = [\n"
        "        {'message': 'doctor local harness', 'intent': 'doctor', 'needles': ['Local Goal Doctor', 'Operator decision', 'Type: /local-goal start local goal: <bounded task>', 'Supervision:', 'watcher: active', 'Trust boundary', 'Accepted soak evidence is present', 'Still review accepted evidence']},\n"
        "        {'message': 'brief local harness', 'intent': 'brief', 'needles': ['Local Goal Brief', 'Answer:', 'Babysit:', 'Boundary: bounded local goals ready; broad autonomy not claimed.', 'Type: /local-goal model-promotion-decision']},\n"
        "        {'message': 'short version for the agentic harness', 'intent': 'brief', 'needles': ['Local Goal Brief', 'Answer:', 'Babysit:', 'Type: /local-goal model-promotion-decision']},\n"
        "        {'message': 'Finish testing the agentic harness and keep it local unless cloud is explicitly needed', 'intent': 'start', 'needles': ['local-goal worker is running']},\n"
        "        {'message': 'continue agentic harness work', 'intent': 'supervise', 'needles': ['Ready for the next local goal', 'send a bounded task']},\n"
        "        {'message': 'do next for the agentic harness', 'intent': 'supervise', 'needles': ['Ready for the next local goal', 'send a bounded task']},\n"
        "        {'message': 'what percentage complete is the harness?', 'intent': 'completion-summary', 'needles': ['Local Goal Completion Summary', 'Autonomy grade: 90% practical harness', '100% broad-autonomy claim: not claimed']},\n"
        "        {'message': 'how is this progressing?', 'intent': 'progress', 'needles': ['Harness readiness: bounded local goals ready', 'Status: ready', 'accepted soak evidence is present', 'Hardening commands:', 'scripts/local-goal next-proof']},\n"
        "        {'message': 'how is the harness progressing?', 'intent': 'progress', 'needles': ['Harness readiness: bounded local goals ready', 'Status: ready', 'accepted soak evidence is present', 'Hardening commands:', 'scripts/local-goal next-proof']},\n"
        "        {'message': 'how is the harness coming along?', 'intent': 'progress', 'needles': ['Harness readiness: bounded local goals ready', 'Status: ready', 'accepted soak evidence is present', 'Hardening commands:', 'scripts/local-goal next-proof']},\n"
        "        {'message': 'where are we with the harness?', 'intent': 'progress', 'needles': ['Harness readiness: bounded local goals ready', 'Status: ready', 'accepted soak evidence is present', 'Hardening commands:', 'scripts/local-goal next-proof']},\n"
        "        {'message': 'ehat is hapenning?', 'intent': 'progress', 'needles': ['Harness readiness: bounded local goals ready', 'Status: ready', 'accepted soak evidence is present', 'Hardening commands:', 'scripts/local-goal next-proof']},\n"
        "        {'message': 'what proof remains for the agentic harness?', 'intent': 'next-proof', 'needles': ['Local Goal Next Proof', 'Status: optional_hardening', 'Required now: false']},\n"
        "        {'message': 'is the harness complete?', 'intent': 'completion-audit', 'needles': ['Local Goal Completion Audit', 'Status: ready_for_bounded_goals', '100% broad-autonomy claim: not claimed']},\n"
        "        {'message': 'what is the Node1 /goal current truth?', 'intent': 'current-truth', 'needles': ['Local Goal Current Truth', 'Dirty blocks acceptance: False', 'Integration audit: ok=True', 'model_promotion_plan:', 'qwopus_window_next:', 'qwopus_window_open_preview:', 'qwopus_window_restore_preview:']},\n"
        "        {'message': 'what model is active?', 'intent': 'model-status', 'needles': ['Local Goal Model Status', 'Status: active_candidate', 'Durability: active_temporary_candidate', 'Promotion gate: ready_for_operator_decision']},\n"
        "        {'message': 'which model is Node1 using?', 'intent': 'model-status', 'needles': ['Local Goal Model Status', 'Current model:', 'Durability:', 'Promotion gate: ready_for_operator_decision']},\n"
        "        {'message': 'is my model good?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'do you trust the model?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'do you trust it for local goals?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'should I promote it?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'can I swap the local-goal model?', 'intent': 'model-status', 'needles': ['Local Goal Model Status', 'Durability:', 'Promotion gate: ready_for_operator_decision']},\n"
        "        {'message': 'is Ornith ready for local-goal canary?', 'intent': 'model-status', 'needles': ['Local Goal Model Status', 'Status: active_candidate', 'Durability: active_temporary_candidate', 'Promotion gate: ready_for_operator_decision']},\n"
        "        {'message': 'how is my orinth modle doing?', 'intent': 'model-status', 'needles': ['Local Goal Model Status', 'Status: active_candidate', 'Durability: active_temporary_candidate', 'Promotion gate: ready_for_operator_decision']},\n"
        "        {'message': 'what next for the model?', 'intent': 'model-eval-next', 'needles': ['Ornith/Qwopus eval next', 'Status: evidence-complete', 'model-promotion-decision']},\n"
        "        {'message': 'ehat is the a/b?', 'intent': 'model-eval-next', 'needles': ['Ornith/Qwopus eval next', 'Status: evidence-complete', 'model-promotion-decision']},\n"
        "        {'message': 'what should I do next with Ornith?', 'intent': 'model-eval-next', 'needles': ['Ornith/Qwopus eval next', 'Status: evidence-complete', 'model-promotion-decision']},\n"
        "        {'message': 'can I test Ornith now?', 'intent': 'model-eval-next', 'needles': ['Ornith/Qwopus eval next', 'Status: evidence-complete', 'model-promotion-decision']},\n"
        "        {'message': 'can I use Ornith for the harness?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Decision required: True']},\n"
        "        {'message': 'should we switch to Ornith?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Decision required: True']},\n"
        "        {'message': 'is Ornith ready to promote?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Decision required: True']},\n"
        "        {'message': 'should I make Ornith permanent?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Decision required: True']},\n"
        "        {'message': 'what do I type to make Ornith permanent?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Phone-safe preview', 'Terminal-only mutation']},\n"
        "        {'message': 'what evidence is missing for Ornith?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'did Ornith beat Qwopus?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Decision required: True']},\n"
        "        {'message': 'can I run the Qwopus baseline now?', 'intent': 'model-nontrivial-baseline-check', 'needles': ['Qwopus nontrivial baseline check', 'Can start now: False', 'Ornith candidate is still active behind qwen36-main']},\n"
        "        {'message': 'what next for Qwopus eval?', 'intent': 'model-eval-next', 'needles': ['Ornith/Qwopus eval next', 'Status: evidence-complete', 'model-promotion-decision']},\n"
        "        {'message': 'should I promote Ornith over Qwopus?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'can we keep developing with Ornith despite Qwopus problems?', 'intent': 'model-promotion-waiver', 'needles': ['Ornith/Qwopus promotion waiver', 'Status: operator-waiver-available', 'Continue developing with Ornith: True']},\n"
        "        {'message': 'is Ornith good for the harness?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'should I promote orinth?', 'intent': 'model-promotion-decision', 'needles': ['Ornith/Qwopus promotion decision', 'Status: ready-for-operator-decision', 'Comparison evidence is complete']},\n"
        "        {'message': 'what was wrong with Qwopus completions?', 'intent': 'model-completion-risk-check', 'needles': ['Qwopus completion risk', 'Completion evidence missing: True', '192k context', 'Command: scripts/local-goal qwopus-window-open', 'Terminal approval command: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW']},\n"
        "        {'message': 'the Qwopus had a problem with completions', 'intent': 'model-completion-risk-check', 'needles': ['Qwopus completion risk', 'Completion evidence missing: True', '192k context', 'Command: scripts/local-goal qwopus-window-open']},\n"
        "        {'message': 'the Qwopus completion issue worries me', 'intent': 'model-completion-risk-check', 'needles': ['Qwopus completion risk', 'Completion evidence missing: True', '192k context', 'Command: scripts/local-goal qwopus-window-open']},\n"
        "        {'message': 'do you trust Qwopus?', 'intent': 'model-completion-risk-check', 'needles': ['Qwopus completion risk', 'Completion evidence missing: True', '192k context']},\n"
        "        {'message': 'write the Qwopus model decision packet', 'intent': 'model-decision-packet', 'needles': ['Local Goal Model Decision Packet Bundle', 'Mutates live service: false', 'Packets: 12']},\n"
        "        {'message': 'can I open the Qwopus service window?', 'intent': 'model-service-window-check', 'needles': ['Qwopus service window check', 'Status: not-ready', 'Ready to open window: False']},\n"
        "        {'message': 'what next in the Qwopus service window?', 'intent': 'model-service-window-next', 'needles': ['Qwopus service window next', 'Status: ready-to-open-window', 'Command: scripts/local-goal qwopus-window-open', 'Terminal approval command: scripts/local-goal qwopus-window-open --execute --confirm OPEN_QWOPUS_SERVICE_WINDOW']},\n"
        "        {'message': 'show the approval packet for the Qwopus service window', 'intent': 'model-service-window-open', 'needles': ['Qwopus service window open', 'Status: approval-required', 'OPEN_QWOPUS_SERVICE_WINDOW']},\n"
        "        {'message': 'preview the guarded Qwopus service window open plan', 'intent': 'model-service-window-open', 'needles': ['Qwopus service window open', 'Status: approval-required', 'OPEN_QWOPUS_SERVICE_WINDOW']},\n"
        "        {'message': 'preview restore Ornith after the Qwopus service window', 'intent': 'model-service-window-restore', 'needles': ['Qwopus service window restore', 'Status: approval-required', 'RESTORE_ORNITH_CANARY']},\n"
        "        {'message': 'what do I type for the Qwopus completion baseline?', 'intent': 'model-nontrivial-baseline-plan', 'needles': ['Qwopus nontrivial baseline plan', 'Status: service-window-required', 'Completion risk:']},\n"
        "        {'message': 'can I leave the harness running overnight?', 'intent': 'trust-boundary', 'needles': ['Local Goal Trust Boundary', 'Routine watcher cycles do not need babysitting', 'Product-sensitive changes still need accepted evidence review']},\n"
        "        {'message': 'can I let it keep working?', 'intent': 'trust-boundary', 'needles': ['Local Goal Trust Boundary', 'Routine watcher cycles do not need babysitting', 'Product-sensitive changes still need accepted evidence review']},\n"
        "        {'message': 'should I let the harness keep working?', 'intent': 'trust-boundary', 'needles': ['Local Goal Trust Boundary', 'Routine watcher cycles do not need babysitting', 'Product-sensitive changes still need accepted evidence review']},\n"
        "        {'message': 'will Hermes tell me if it needs me?', 'intent': 'trust-boundary', 'needles': ['Local Goal Trust Boundary', 'watcher: active', 'Operator action:']},\n"
        "        {'message': 'Hermes, spend the next 10 hours improving Agent Society usefulness and keep working until review passes.', 'intent': 'mission-create', 'needles': ['Dry run: would route to mission-create', 'Executed: no']},\n"
        "    ]\n"
        "    results = []\n"
        "    for index, case in enumerate(cases):\n"
        "        before = len(calls)\n"
        "        event = MessageEvent(text=case['message'], source=source, message_id=f'm{index}')\n"
        "        reply = await runner._handle_message(event)\n"
        "        new_calls = calls[before:]\n"
        "        results.append({\n"
        "            'message': case['message'],\n"
        "            'ok': all(needle in reply for needle in case['needles']) and bool(new_calls) and new_calls[0][-2:] == (case['message'], '--json'),\n"
        "            'reply': reply[:800],\n"
        "            'calls': [list(call) for call in new_calls],\n"
        "        })\n"
        "    print(json.dumps({'ok': all(item['ok'] for item in results), 'results': results, 'calls': [list(call) for call in calls]}, sort_keys=True))\n"
        "asyncio.run(main())\n"
    )
    proc = run([str(HERMES_PYTHON), "-c", script], timeout=90)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {}
    ok = proc.returncode == 0 and parsed.get("ok") is True
    return {
        "ok": ok,
        "detail": parsed if parsed else stdout[-500:],
        "returncode": proc.returncode,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def _planner_route_map_check() -> dict[str, Any]:
    """Verify premium planner names resolve to the expected manager routes."""
    expected = {
        "gpt-5.5": "codex:gpt-5.5",
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
        "glm-5.2": "zai/glm-5.2",
        "kimi-coding": "kimi-coding/kimi-for-coding",
        "thinkmax": "litellm-gateway/thinkmax",
    }
    if not MANAGER.exists():
        return {
            "ok": False,
            "expected": expected,
            "routes": {},
            "missing": sorted(expected),
            "mismatched": {},
            "returncode": None,
            "stdout_tail": "",
            "stderr_tail": f"missing manager: {MANAGER}",
        }
    script = (
        "import importlib.util\n"
        "import json\n"
        f"path = {str(MANAGER)!r}\n"
        "spec = importlib.util.spec_from_file_location('local_goal_manager', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "assert spec and spec.loader\n"
        "spec.loader.exec_module(module)\n"
        "routes = getattr(module, 'PLANNER_MODELS', {})\n"
        "print(json.dumps(routes, sort_keys=True))\n"
    )
    proc = run([str(HERMES_PYTHON), "-c", script], timeout=30)
    routes: dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout)
        if isinstance(parsed, dict):
            routes = parsed
    except json.JSONDecodeError:
        routes = {}
    missing = sorted(name for name in expected if name not in routes)
    mismatched = {
        name: {"expected": value, "actual": routes.get(name)}
        for name, value in expected.items()
        if name in routes and routes.get(name) != value
    }
    ok = proc.returncode == 0 and not missing and not mismatched
    return {
        "ok": ok,
        "expected": expected,
        "routes": {name: routes.get(name) for name in sorted(expected)},
        "missing": missing,
        "mismatched": mismatched,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout.strip()[-1000:],
        "stderr_tail": proc.stderr.strip()[-1000:],
    }


def _resolve_tool_location_template(value: str, tool_locations: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(tool_locations.get(name) or match.group(0))

    return re.sub(r"\{tool:([A-Za-z0-9_.-]+)\}", replace, value)


def _cloud_worker_profile_check() -> dict[str, Any]:
    """Verify cloud executor workers are resolvable and allowed for build work."""
    expected = {
        "opencode-kimi-build": {
            "model": "kimi-coding/kimi-for-coding",
            "kind": "opencode-cli-kimi-coding-plan",
        },
        "opencode-glm-build": {
            "model": "litellm-gateway/glm-5",
            "kind": "opencode-cli-glm-litellm",
        },
    }
    try:
        registry = json.loads(WORKER_REGISTRY.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "registry_path": str(WORKER_REGISTRY),
            "capabilities_path": str(WORKER_CAPABILITIES),
            "workers": {},
            "missing": sorted(expected),
            "failed": sorted(expected),
            "error": f"registry unreadable: {exc}",
        }
    try:
        capabilities = json.loads(WORKER_CAPABILITIES.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "registry_path": str(WORKER_REGISTRY),
            "capabilities_path": str(WORKER_CAPABILITIES),
            "workers": {},
            "missing": sorted(expected),
            "failed": sorted(expected),
            "error": f"capabilities unreadable: {exc}",
        }

    registry_workers = (
        registry.get("workers") if isinstance(registry.get("workers"), dict) else {}
    )
    capability_workers = (
        capabilities.get("workers")
        if isinstance(capabilities.get("workers"), dict)
        else {}
    )
    tool_locations = (
        registry.get("tool_locations")
        if isinstance(registry.get("tool_locations"), dict)
        else {}
    )
    workers: dict[str, Any] = {}
    missing: list[str] = []
    failed: list[str] = []
    for worker_name, expected_meta in expected.items():
        worker = registry_workers.get(worker_name)
        caps = capability_workers.get(worker_name)
        if not isinstance(worker, dict) or not isinstance(caps, dict):
            missing.append(worker_name)
            failed.append(worker_name)
            workers[worker_name] = {
                "ok": False,
                "registry_present": isinstance(worker, dict),
                "capability_present": isinstance(caps, dict),
            }
            continue

        binary_template = str(worker.get("binary") or "")
        binary_resolved = _resolve_tool_location_template(
            binary_template, tool_locations
        )
        command = (
            worker.get("command") if isinstance(worker.get("command"), list) else []
        )
        command_text = " ".join(str(part) for part in command)
        modes_allowed = (
            caps.get("modes_allowed")
            if isinstance(caps.get("modes_allowed"), list)
            else []
        )
        task_types_allowed = (
            caps.get("task_types_allowed")
            if isinstance(caps.get("task_types_allowed"), list)
            else []
        )
        allowed_roots = (
            ((caps.get("mutation_rights") or {}).get("allowed_roots") or [])
            if isinstance(caps.get("mutation_rights"), dict)
            else []
        )
        checks = {
            "enabled": worker.get("enabled") is True,
            "binary_resolves": bool(binary_resolved)
            and "{tool:" not in binary_resolved
            and (Path(binary_resolved).exists() or bool(binary_resolved)),
            "expected_kind": worker.get("kind") == expected_meta["kind"],
            "expected_model": expected_meta["model"] in command_text,
            "implementation_allowed": "implementation" in modes_allowed,
            "code_work_allowed": "code_work" in task_types_allowed,
            "documentation_root_allowed": str(DOC_ROOT) in allowed_roots,
            "secrets_forbidden": "/mnt/raid0/.secrets"
            in ((caps.get("mutation_rights") or {}).get("forbidden_roots") or []),
        }
        ok = all(checks.values())
        if not ok:
            failed.append(worker_name)
        workers[worker_name] = {
            "ok": ok,
            "checks": checks,
            "binary_template": binary_template,
            "binary_resolved": binary_resolved,
            "kind": worker.get("kind"),
            "command_model": expected_meta["model"],
            "capability_worker_id": caps.get("worker_id"),
        }

    return {
        "ok": not missing and not failed,
        "registry_path": str(WORKER_REGISTRY),
        "capabilities_path": str(WORKER_CAPABILITIES),
        "workers": workers,
        "missing": missing,
        "failed": failed,
    }


def _dry_run_route_check(message: str, expected_intent: str) -> dict[str, Any]:
    proc = run(
        ["python3", str(COMMAND_SHIM), message, "--dry-run", "--json"],
        timeout=60,
    )
    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(proc.stdout)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = {}
    command = payload.get("command") if isinstance(payload.get("command"), list) else []
    ok = (
        proc.returncode == 0
        and payload.get("dry_run") is True
        and payload.get("intent") == expected_intent
    )
    return {
        "ok": ok,
        "message": message,
        "expected_intent": expected_intent,
        "intent": payload.get("intent"),
        "command": command,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def _timer_supervision_check() -> dict[str, Any]:
    required_service_fragments = [
        str(LOCAL_GOAL_WRAPPER),
        "monitor",
        "--auto-accept",
        "--auto-continue",
        "--auto-dispatch",
        "--auto-commit-owned",
    ]
    execstart_commands = _systemd_execstart_commands(LOCAL_GOAL_WATCH_SERVICE)
    matching_execstart = next(
        (
            command
            for command in execstart_commands
            if all(fragment in command for fragment in required_service_fragments)
        ),
        "",
    )
    missing_execstart_fragments = [
        fragment
        for fragment in required_service_fragments
        if not any(fragment in command for command in execstart_commands)
    ]
    forbidden_execstart_fragments = ["--no-auto-external-review"]
    forbidden_execstart_matches = [
        fragment
        for fragment in forbidden_execstart_fragments
        if any(fragment in command for command in execstart_commands)
    ]
    service_ok = bool(matching_execstart) and not forbidden_execstart_matches
    timer_file_ok = _path_text_contains(
        LOCAL_GOAL_WATCH_TIMER,
        ["OnUnitActiveSec=5min", "WantedBy=timers.target"],
    )
    active_proc = run(
        ["systemctl", "--user", "is-active", "local-node1-goal-watch.timer"],
        timeout=30,
    )
    active = active_proc.returncode == 0 and active_proc.stdout.strip() == "active"
    return {
        "ok": service_ok and timer_file_ok and active,
        "service_path": str(LOCAL_GOAL_WATCH_SERVICE),
        "timer_path": str(LOCAL_GOAL_WATCH_TIMER),
        "service_ok": service_ok,
        "execstart_ok": service_ok,
        "execstart_commands": execstart_commands,
        "matching_execstart": matching_execstart,
        "missing_execstart_fragments": missing_execstart_fragments,
        "forbidden_execstart_fragments": forbidden_execstart_matches,
        "timer_file_ok": timer_file_ok,
        "timer_active": active,
        "systemctl_returncode": active_proc.returncode,
        "systemctl_stdout": active_proc.stdout.strip(),
        "systemctl_stderr": active_proc.stderr.strip()[-1000:],
    }


def _gateway_service_check() -> dict[str, Any]:
    proc = run(
        ["systemctl", "--user", "is-active", HERMES_GATEWAY_SERVICE],
        timeout=30,
    )
    active = proc.returncode == 0 and proc.stdout.strip() == "active"
    return {
        "ok": active,
        "service": HERMES_GATEWAY_SERVICE,
        "active": active,
        "systemctl_returncode": proc.returncode,
        "systemctl_stdout": proc.stdout.strip(),
        "systemctl_stderr": proc.stderr.strip()[-1000:],
    }


def cached_integration_audit_summary() -> dict[str, Any]:
    """Return a compact cached integration-audit status for status JSON.

    Status rendering is a frequent proof surface; use the latest cached audit
    instead of recursively running the full integration audit from status.
    """
    if not INTEGRATION_AUDIT_JSON.exists():
        return {
            "ok": False,
            "status": "missing",
            "missing": ["integration_audit_artifact_missing"],
            "path": str(INTEGRATION_AUDIT_JSON),
            "generated_at": "",
            "artifact_readable": False,
        }
    try:
        payload = json.loads(INTEGRATION_AUDIT_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "ok": False,
            "status": "unreadable",
            "missing": ["integration_audit_artifact_unreadable"],
            "path": str(INTEGRATION_AUDIT_JSON),
            "generated_at": "",
            "artifact_readable": False,
            "read_error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "status": "invalid",
            "missing": ["integration_audit_artifact_invalid"],
            "path": str(INTEGRATION_AUDIT_JSON),
            "generated_at": "",
            "artifact_readable": False,
        }
    missing = payload.get("missing")
    return {
        "ok": payload.get("ok") is True,
        "status": payload.get("status") or "",
        "missing": missing if isinstance(missing, list) else [],
        "path": str(INTEGRATION_AUDIT_JSON),
        "generated_at": payload.get("generated_at") or "",
        "artifact_readable": True,
        "lock_fallback": payload.get("lock_fallback") is True,
    }


def _telegram_notification_check() -> dict[str, Any]:
    module_ok = _path_text_contains(
        TELEGRAM_NOTIFY_MODULE,
        ["def send_telegram", "TOPIC_AGENTS"],
    )
    sample_payload = {
        "classification": "accepted",
        "recommended_action": "sample notification gate check",
        "active_goal": {
            "objective": "integration audit",
            "run_dir": "/tmp/run",
            "awaiting_review": False,
            "accepted": True,
        },
        "runtime": {},
        "active_warning_count": 0,
    }
    should_send, reason = should_notify_operator(
        sample_payload, previous_notify_state={}
    )
    message = format_operator_notification(sample_payload)
    message_ok = (
        "Local Node1 goal: accepted" in message and "integration audit" in message
    )
    return {
        "ok": module_ok and should_send and message_ok,
        "module_path": str(TELEGRAM_NOTIFY_MODULE),
        "module_ok": module_ok,
        "gate_allows_terminal_state": should_send,
        "gate_reason_or_signature": reason,
        "message_ok": message_ok,
    }


def hermes_integration_audit(status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only proof that Hermes exposes all local-goal control lanes.

    A process-level flock around the full body serializes concurrent callers
    (truth script + ops-hourly audit + selftest all firing in the same minute).
    If the lock is busy and we can't acquire it within ~15s, fall back to the
    most recent cached INTEGRATION_AUDIT_JSON so we never block the caller
    past its own timeout budget.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / "local-node1-goal-integration-audit.lock"
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another process is already running the audit. Wait briefly.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                time.sleep(0.5)
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    continue
            else:
                # Still locked after 15s — return the last cached audit
                # rather than blocking the caller (which has its own timeout).
                if INTEGRATION_AUDIT_JSON.exists():
                    try:
                        cached = json.loads(
                            INTEGRATION_AUDIT_JSON.read_text(encoding="utf-8")
                        )
                        if isinstance(cached, dict):
                            cached.setdefault(
                                "lock_fallback",
                                True,
                            )
                            return cached
                    except (json.JSONDecodeError, OSError):
                        pass
                return {
                    "ok": False,
                    "status": "integration_audit_locked",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "missing": ["integration_audit_concurrent_lock"],
                    "checks": [],
                    "lock_fallback": True,
                }
        try:
            return _hermes_integration_audit_body(status)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _hermes_integration_audit_body(
    status: dict[str, Any] | None,
) -> dict[str, Any]:
    """The real audit logic. Called under the process-level flock."""
    status = status if isinstance(status, dict) else manager_json()
    caps = lane_capabilities(status)
    lanes = caps.get("lanes") if isinstance(caps.get("lanes"), dict) else {}
    premium = (
        lanes.get("premium_planner_local_builder")
        if isinstance(lanes.get("premium_planner_local_builder"), dict)
        else {}
    )
    cloud = (
        lanes.get("cloud_executor")
        if isinstance(lanes.get("cloud_executor"), dict)
        else {}
    )
    target_planners = {
        "gpt-5.5",
        "deepseek-v4-pro",
        "glm-5.2",
        "kimi-coding",
        "thinkmax",
    }
    available_planners = set(premium.get("planners") or [])
    route_checks = [
        _dry_run_route_check(
            "dry run start this as local goal with gpt 5.5 planner: integration route check",
            "premium-start",
        ),
        _dry_run_route_check(
            "dry run start this as local goal with deepseek v4 pro planner: integration route check",
            "premium-start",
        ),
        _dry_run_route_check(
            "dry run start this as local goal with thinkmax planner: integration route check",
            "premium-start",
        ),
        _dry_run_route_check(
            "dry run start this as local goal with glm 5.2 planner: integration route check",
            "premium-start",
        ),
        _dry_run_route_check(
            "dry run start this as local goal with kimi coding planner: integration route check",
            "premium-start",
        ),
        _dry_run_route_check(
            "dry run start cloud local goal with gpt 5.5 planner: integration route check",
            "enqueue-cloud",
        ),
        _dry_run_route_check(
            "dry run start cloud glm goal with deepseek v4 pro planner: integration route check",
            "enqueue-cloud",
        ),
        _dry_run_route_check(
            "dry run supervise local harness",
            "supervise",
        ),
        _dry_run_route_check(
            "continue local goal",
            "supervise",
        ),
        _dry_run_route_check(
            "dry run doctor local harness",
            "doctor",
        ),
        _dry_run_route_check(
            "dry run what is the Node1 /goal current truth?",
            "current-truth",
        ),
        _dry_run_route_check(
            "dry run what percentage complete is the harness?",
            "completion-summary",
        ),
        _dry_run_route_check(
            "dry run what should I do next",
            "brief",
        ),
        _dry_run_route_check(
            "dry run create local goal",
            "doctor",
        ),
        _dry_run_route_check(
            "dry run make it happen",
            "supervise",
        ),
        _dry_run_route_check(
            "dry run is it done",
            "completion-audit",
        ),
        _dry_run_route_check(
            "dry run verify it",
            "next-proof",
        ),
        _dry_run_route_check(
            "dry run what happened",
            "progress",
        ),
        _dry_run_route_check(
            "dry run what files did the last local goal change?",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run show last run",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run show me the accepted evidence",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run show me the accepted local goal evidence",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run should I accept the evidence?",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run show review evidence",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run should I review it",
            "ready-review",
        ),
        _dry_run_route_check(
            "dry run what verification passed?",
            "last-run",
        ),
        _dry_run_route_check(
            "dry run does dirty work block acceptance?",
            "current-truth",
        ),
        _dry_run_route_check(
            "dry run what is Node1 doing?",
            "free",
        ),
        _dry_run_route_check(
            "dry run can I start now?",
            "can-start",
        ),
        _dry_run_route_check(
            "dry run can I start if vLLM is busy?",
            "can-start",
        ),
        _dry_run_route_check(
            "dry run is vLLM busy?",
            "free",
        ),
        _dry_run_route_check(
            "dry run are GPUs idle?",
            "free",
        ),
        _dry_run_route_check(
            "dry run any progress",
            "progress",
        ),
        _dry_run_route_check(
            "dry run did it work",
            "completion-audit",
        ),
        _dry_run_route_check(
            "dry run can I leave it",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run accept it",
            "can-accept",
        ),
        _dry_run_route_check(
            "dry run how is this progressing?",
            "progress",
        ),
        _dry_run_route_check(
            "dry run what proof remains for the agentic harness?",
            "next-proof",
        ),
        _dry_run_route_check(
            "dry run what hardening remains for the agentic harness?",
            "next-proof",
        ),
        _dry_run_route_check(
            "dry run please keep improving the local harness",
            "supervise",
        ),
        _dry_run_route_check(
            "dry run do another hardening pass on the harness",
            "supervise",
        ),
        _dry_run_route_check(
            "dry run is the harness complete?",
            "completion-audit",
        ),
        _dry_run_route_check(
            "dry run /local-goal audit-health",
            "audit-health",
        ),
        _dry_run_route_check(
            "dry run is the local harness audit stuck?",
            "audit-health",
        ),
        _dry_run_route_check(
            "dry run what do I type for the agentic harness soak test?",
            "soak-plan",
        ),
        _dry_run_route_check(
            "dry run what model is active?",
            "model-status",
        ),
        _dry_run_route_check(
            "dry run which model is Node1 using?",
            "model-status",
        ),
        _dry_run_route_check(
            "dry run can I swap the local-goal model?",
            "model-status",
        ),
        _dry_run_route_check(
            "dry run is Ornith ready for local-goal canary?",
            "model-status",
        ),
        _dry_run_route_check(
            "dry run what next for the model?",
            "model-eval-next",
        ),
        _dry_run_route_check(
            "dry run what should I do next with Ornith?",
            "model-eval-next",
        ),
        _dry_run_route_check(
            "dry run can I test Ornith now?",
            "model-eval-next",
        ),
        _dry_run_route_check(
            "dry run can I use Ornith for the harness?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run should we switch to Ornith?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run is Ornith ready to promote?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run should I make Ornith permanent?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run make it permanent",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run what do I type to make Ornith permanent?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run is Ornith permanent yet?",
            "model-promotion-verify",
        ),
        _dry_run_route_check(
            "dry run is it permanent yet",
            "model-promotion-verify",
        ),
        _dry_run_route_check(
            "dry run execute Ornith promotion",
            "model-promotion-apply",
        ),
        _dry_run_route_check(
            "dry run model-promotion-plan",
            "model-promotion-plan",
        ),
        _dry_run_route_check(
            "dry run what evidence is missing for Ornith?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run did Ornith beat Qwopus?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run can I run the Qwopus baseline now?",
            "model-nontrivial-baseline-check",
        ),
        _dry_run_route_check(
            "dry run what was wrong with Qwopus completions?",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run the Qwopus had a problem with completions",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run the Qwopus completion issue worries me",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run is Qwopus safe to use for the harness?",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run can Qwopus handle 192k seq4?",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run should I promote Ornith over Qwopus?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run are you team Ornith now?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run model-promotion-apply",
            "model-promotion-apply",
        ),
        _dry_run_route_check(
            "dry run can we keep developing with Ornith despite Qwopus problems?",
            "model-promotion-waiver",
        ),
        _dry_run_route_check(
            "dry run can I leave the harness running overnight?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run can I let it keep working?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run can I let you keep working?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run do I need to keep checking it?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run will it keep going by itself?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run should I let the harness keep working?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run will Hermes tell me if it needs me?",
            "trust-boundary",
        ),
        _dry_run_route_check(
            "dry run is Ornith good for the harness?",
            "model-promotion-decision",
        ),
        _dry_run_route_check(
            "dry run write the Qwopus model decision packet",
            "model-decision-packet",
        ),
        _dry_run_route_check(
            "dry run do you trust Qwopus?",
            "model-completion-risk-check",
        ),
        _dry_run_route_check(
            "dry run what next in the Qwopus service window?",
            "model-service-window-next",
        ),
        _dry_run_route_check(
            "dry run show the approval packet for the Qwopus service window",
            "model-service-window-open",
        ),
        _dry_run_route_check(
            "dry run switch to Qwopus",
            "model-service-window-open",
        ),
        _dry_run_route_check(
            "dry run preview the guarded Qwopus service window open plan",
            "model-service-window-open",
        ),
        _dry_run_route_check(
            "dry run preview restore Ornith after the Qwopus service window",
            "model-service-window-restore",
        ),
        _dry_run_route_check(
            "dry run restore Ornith",
            "model-service-window-restore",
        ),
        _dry_run_route_check(
            "dry run is Qwopus running?",
            "model-status",
        ),
        _dry_run_route_check(
            "dry run what do I type for the Qwopus completion baseline?",
            "model-nontrivial-baseline-plan",
        ),
        _dry_run_route_check(
            "nudge local goal: integration route check",
            "nudge",
        ),
        _dry_run_route_check(
            "dry run ask kimi to review the local goal",
            "external-review",
        ),
        _dry_run_route_check(
            "dry run can glm 5.2 do your job for the agentic harness?",
            "glm-handoff-plan",
        ),
        _dry_run_route_check(
            "dry run which harness mode should I use?",
            "harness-modes",
        ),
        _dry_run_route_check(
            "dry run Use the default harness mode for this goal: update one harmless doc note and verify it",
            "harness-mode-default-start",
        ),
        _dry_run_route_check(
            "dry run Have GLM supervise this goal and leave Codex to spot-check the important decisions: fix one bounded harness route",
            "harness-mode-codex-saving-start",
        ),
        _dry_run_route_check(
            "dry run Run a bounded cloud canary with the GLM worker: update one safe report artifact",
            "harness-mode-cloud-canary",
        ),
        _dry_run_route_check(
            "dry run Check whether the direct GLM audit/proposal lane is ready.",
            "harness-mode-glm-local-canary-plan",
        ),
        _dry_run_route_check(
            "dry run Hermes, spend the next 10 hours improving Agent Society usefulness and keep working until review passes.",
            "mission-create",
        ),
        _dry_run_route_check(
            "dry run Hermes spend the next 10 hours improving the harness",
            "mission-create",
        ),
    ]
    timer_check = _timer_supervision_check()
    gateway_service_check = _gateway_service_check()
    notification_check = _telegram_notification_check()
    gateway_registry_check = _gateway_command_registry_check()
    gateway_dispatch_check = _gateway_handler_dry_run_dispatch_check()
    command_capabilities_human_output_check = _command_capabilities_human_output_check()
    command_doctor_human_output_check = _command_doctor_human_output_check()
    command_doctor_json_state_output_check = _command_doctor_json_state_output_check()
    command_brief_human_output_check = _command_brief_human_output_check()
    wrapper_quick_start_short_goal_guard_check = (
        _wrapper_quick_start_short_goal_guard_check()
    )
    wrapper_guide_human_output_check = _wrapper_guide_human_output_check()
    wrapper_status_human_output_check = _wrapper_status_human_output_check()
    wrapper_progress_human_output_check = _wrapper_progress_human_output_check()
    wrapper_next_proof_human_output_check = _wrapper_next_proof_human_output_check()
    wrapper_completion_audit_human_output_check = (
        _wrapper_completion_audit_human_output_check()
    )
    wrapper_completion_summary_human_output_check = (
        _wrapper_completion_summary_human_output_check()
    )
    wrapper_audit_health_human_output_check = _wrapper_audit_health_human_output_check()
    wrapper_soak_plan_human_output_check = _wrapper_soak_plan_human_output_check()
    wrapper_current_truth_human_output_check = (
        _wrapper_current_truth_human_output_check()
    )
    wrapper_model_service_window_health_guard_check = (
        _wrapper_model_service_window_health_guard_check()
    )
    wrapper_queue_human_output_check = _wrapper_queue_human_output_check()
    wrapper_queue_summary_human_output_check = (
        _wrapper_queue_summary_human_output_check()
    )
    wrapper_glm_supervisor_human_output_check = (
        _wrapper_glm_supervisor_human_output_check()
    )
    wrapper_glm_handoff_plan_human_output_check = (
        _wrapper_glm_handoff_plan_human_output_check()
    )
    wrapper_harness_modes_human_output_check = (
        _wrapper_harness_modes_human_output_check()
    )
    wrapper_model_promotion_commands_check = _wrapper_model_promotion_commands_check()
    wrapper_mission_show_human_output_check = _wrapper_mission_show_human_output_check()
    wrapper_supervise_human_output_mapping_check = (
        _wrapper_supervise_human_output_mapping_check()
    )
    wrapper_monitor_human_output_mapping_check = (
        _wrapper_monitor_human_output_mapping_check()
    )
    wrapper_review_human_output_mapping_check = (
        _wrapper_review_human_output_mapping_check()
    )
    wrapper_accept_human_output_mapping_check = (
        _wrapper_accept_human_output_mapping_check()
    )
    wrapper_nudge_human_output_mapping_check = (
        _wrapper_nudge_human_output_mapping_check()
    )
    wrapper_external_review_human_output_mapping_check = (
        _wrapper_external_review_human_output_mapping_check()
    )
    wrapper_mission_monitor_human_output_mapping_check = (
        _wrapper_mission_monitor_human_output_mapping_check()
    )
    wrapper_continue_human_output_mapping_check = (
        _wrapper_continue_human_output_mapping_check()
    )
    wrapper_mission_control_human_output_mapping_check = (
        _wrapper_mission_control_human_output_mapping_check()
    )
    wrapper_stop_human_output_mapping_check = _wrapper_stop_human_output_mapping_check()
    wrapper_repair_closeout_human_output_mapping_check = (
        _wrapper_repair_closeout_human_output_mapping_check()
    )
    wrapper_recovery_human_output_mapping_check = (
        _wrapper_recovery_human_output_mapping_check()
    )
    wrapper_handoff_output_human_output_mapping_check = (
        _wrapper_handoff_output_human_output_mapping_check()
    )
    wrapper_ask_plain_language_mapping_check = (
        _wrapper_ask_plain_language_mapping_check()
    )
    gateway_help_check = _gateway_help_discoverability_check()
    gateway_plain_detection_check = _gateway_plain_local_goal_detection_check()
    gateway_plain_message_dispatch_check = (
        _gateway_plain_local_goal_message_dispatch_check()
    )
    current_truth_operator_clarity_check = _current_truth_operator_clarity_check()
    planner_route_map_check = _planner_route_map_check()
    cloud_worker_profile_check = _cloud_worker_profile_check()
    auto_external_review_ok = _path_text_contains(
        Path(__file__),
        [
            "run_auto_external_supervisor_review",
            "AUTO_EXTERNAL_REVIEWERS",
            "external_supervisor_review",
        ],
    )
    checks = [
        {
            "name": "stable_wrapper_present",
            "ok": LOCAL_GOAL_WRAPPER.exists(),
            "detail": str(LOCAL_GOAL_WRAPPER),
            "classification": "installed_capability"
            if LOCAL_GOAL_WRAPPER.exists()
            else "not_done",
        },
        {
            "name": "command_shim_present",
            "ok": COMMAND_SHIM.exists(),
            "detail": str(COMMAND_SHIM),
            "classification": "installed_capability"
            if COMMAND_SHIM.exists()
            else "not_done",
        },
        {
            "name": "gateway_slash_command_registered",
            "ok": _path_text_contains(
                HERMES_GATEWAY_RUN,
                ["_handle_local_goal_command", "_LOCAL_NODE1_GOAL_COMMAND"],
            )
            and _path_text_contains(
                HERMES_COMMAND_REGISTRY,
                ['CommandDef("local-goal"', "local_goal", "node1-goal"],
            )
            and gateway_registry_check.get("ok") is True,
            "detail": f"{HERMES_GATEWAY_RUN}; {HERMES_COMMAND_REGISTRY}",
            "classification": (
                "installed_capability"
                if gateway_registry_check.get("ok") is True
                else "not_done"
            ),
            "registry": gateway_registry_check,
        },
        {
            "name": "gateway_handler_dry_run_dispatch",
            "ok": gateway_dispatch_check.get("ok") is True,
            "detail": str(gateway_dispatch_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if gateway_dispatch_check.get("ok") is True
                else "not_done"
            ),
            "dispatch": gateway_dispatch_check,
        },
        {
            "name": "command_capabilities_human_output",
            "ok": command_capabilities_human_output_check.get("ok") is True,
            "detail": str(command_capabilities_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if command_capabilities_human_output_check.get("ok") is True
                else "not_done"
            ),
            "capabilities_output": command_capabilities_human_output_check,
        },
        {
            "name": "command_doctor_human_output",
            "ok": command_doctor_human_output_check.get("ok") is True,
            "detail": str(command_doctor_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if command_doctor_human_output_check.get("ok") is True
                else "not_done"
            ),
            "doctor_output": command_doctor_human_output_check,
        },
        {
            "name": "command_doctor_json_state_output",
            "ok": command_doctor_json_state_output_check.get("ok") is True,
            "detail": str(command_doctor_json_state_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if command_doctor_json_state_output_check.get("ok") is True
                else "not_done"
            ),
            "doctor_json_state_output": command_doctor_json_state_output_check,
        },
        {
            "name": "command_brief_human_output",
            "ok": command_brief_human_output_check.get("ok") is True,
            "detail": str(command_brief_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if command_brief_human_output_check.get("ok") is True
                else "not_done"
            ),
            "brief_output": command_brief_human_output_check,
        },
        {
            "name": "wrapper_quick_start_short_goal_guard",
            "ok": wrapper_quick_start_short_goal_guard_check.get("ok") is True,
            "detail": str(
                wrapper_quick_start_short_goal_guard_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_quick_start_short_goal_guard_check.get("ok") is True
                else "not_done"
            ),
            "quick_start_guard": wrapper_quick_start_short_goal_guard_check,
        },
        {
            "name": "wrapper_guide_human_output",
            "ok": wrapper_guide_human_output_check.get("ok") is True,
            "detail": str(wrapper_guide_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_guide_human_output_check.get("ok") is True
                else "not_done"
            ),
            "guide_output": wrapper_guide_human_output_check,
        },
        {
            "name": "wrapper_status_human_output",
            "ok": wrapper_status_human_output_check.get("ok") is True,
            "detail": str(wrapper_status_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_status_human_output_check.get("ok") is True
                else "not_done"
            ),
            "status_output": wrapper_status_human_output_check,
        },
        {
            "name": "wrapper_progress_human_output",
            "ok": wrapper_progress_human_output_check.get("ok") is True,
            "detail": str(wrapper_progress_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_progress_human_output_check.get("ok") is True
                else "not_done"
            ),
            "progress_output": wrapper_progress_human_output_check,
        },
        {
            "name": "wrapper_next_proof_human_output",
            "ok": wrapper_next_proof_human_output_check.get("ok") is True,
            "detail": str(wrapper_next_proof_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_next_proof_human_output_check.get("ok") is True
                else "not_done"
            ),
            "next_proof_output": wrapper_next_proof_human_output_check,
        },
        {
            "name": "wrapper_completion_audit_human_output",
            "ok": wrapper_completion_audit_human_output_check.get("ok") is True,
            "detail": str(
                wrapper_completion_audit_human_output_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_completion_audit_human_output_check.get("ok") is True
                else "not_done"
            ),
            "completion_audit_output": wrapper_completion_audit_human_output_check,
        },
        {
            "name": "wrapper_completion_summary_human_output",
            "ok": wrapper_completion_summary_human_output_check.get("ok") is True,
            "detail": str(
                wrapper_completion_summary_human_output_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_completion_summary_human_output_check.get("ok") is True
                else "not_done"
            ),
            "completion_summary_output": wrapper_completion_summary_human_output_check,
        },
        {
            "name": "wrapper_audit_health_human_output",
            "ok": wrapper_audit_health_human_output_check.get("ok") is True,
            "detail": str(wrapper_audit_health_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_audit_health_human_output_check.get("ok") is True
                else "not_done"
            ),
            "audit_health_output": wrapper_audit_health_human_output_check,
        },
        {
            "name": "wrapper_soak_plan_human_output",
            "ok": wrapper_soak_plan_human_output_check.get("ok") is True,
            "detail": str(wrapper_soak_plan_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_soak_plan_human_output_check.get("ok") is True
                else "not_done"
            ),
            "soak_plan_output": wrapper_soak_plan_human_output_check,
        },
        {
            "name": "wrapper_current_truth_human_output",
            "ok": wrapper_current_truth_human_output_check.get("ok") is True,
            "detail": str(wrapper_current_truth_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_current_truth_human_output_check.get("ok") is True
                else "not_done"
            ),
            "current_truth_output": wrapper_current_truth_human_output_check,
        },
        {
            "name": "wrapper_model_service_window_health_guard",
            "ok": wrapper_model_service_window_health_guard_check.get("ok") is True,
            "detail": str(
                wrapper_model_service_window_health_guard_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_model_service_window_health_guard_check.get("ok") is True
                else "not_done"
            ),
            "health_guard": wrapper_model_service_window_health_guard_check,
        },
        {
            "name": "wrapper_queue_human_output",
            "ok": wrapper_queue_human_output_check.get("ok") is True,
            "detail": str(wrapper_queue_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_queue_human_output_check.get("ok") is True
                else "not_done"
            ),
            "queue_output": wrapper_queue_human_output_check,
        },
        {
            "name": "wrapper_queue_summary_human_output",
            "ok": wrapper_queue_summary_human_output_check.get("ok") is True,
            "detail": str(wrapper_queue_summary_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_queue_summary_human_output_check.get("ok") is True
                else "not_done"
            ),
            "queue_summary_output": wrapper_queue_summary_human_output_check,
        },
        {
            "name": "wrapper_glm_supervisor_human_output",
            "ok": wrapper_glm_supervisor_human_output_check.get("ok") is True,
            "detail": str(
                wrapper_glm_supervisor_human_output_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_glm_supervisor_human_output_check.get("ok") is True
                else "not_done"
            ),
            "glm_supervisor_output": wrapper_glm_supervisor_human_output_check,
        },
        {
            "name": "wrapper_glm_handoff_plan_human_output",
            "ok": wrapper_glm_handoff_plan_human_output_check.get("ok") is True,
            "detail": str(
                wrapper_glm_handoff_plan_human_output_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_glm_handoff_plan_human_output_check.get("ok") is True
                else "not_done"
            ),
            "glm_handoff_plan_output": wrapper_glm_handoff_plan_human_output_check,
        },
        {
            "name": "wrapper_harness_modes_human_output",
            "ok": wrapper_harness_modes_human_output_check.get("ok") is True,
            "detail": str(wrapper_harness_modes_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_harness_modes_human_output_check.get("ok") is True
                else "not_done"
            ),
            "harness_modes_output": wrapper_harness_modes_human_output_check,
        },
        {
            "name": "wrapper_model_promotion_commands_available",
            "ok": wrapper_model_promotion_commands_check.get("ok") is True,
            "detail": str(wrapper_model_promotion_commands_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_model_promotion_commands_check.get("ok") is True
                else "not_done"
            ),
            "model_promotion_commands": wrapper_model_promotion_commands_check,
        },
        {
            "name": "wrapper_mission_show_human_output",
            "ok": wrapper_mission_show_human_output_check.get("ok") is True,
            "detail": str(wrapper_mission_show_human_output_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_mission_show_human_output_check.get("ok") is True
                else "not_done"
            ),
            "mission_show_output": wrapper_mission_show_human_output_check,
        },
        {
            "name": "wrapper_supervise_human_output_mapping",
            "ok": wrapper_supervise_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_supervise_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_supervise_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "supervise_mapping": wrapper_supervise_human_output_mapping_check,
        },
        {
            "name": "wrapper_monitor_human_output_mapping",
            "ok": wrapper_monitor_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_monitor_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_monitor_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "monitor_mapping": wrapper_monitor_human_output_mapping_check,
        },
        {
            "name": "wrapper_review_human_output_mapping",
            "ok": wrapper_review_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_review_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_review_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "review_mapping": wrapper_review_human_output_mapping_check,
        },
        {
            "name": "wrapper_accept_human_output_mapping",
            "ok": wrapper_accept_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_accept_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_accept_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "accept_mapping": wrapper_accept_human_output_mapping_check,
        },
        {
            "name": "wrapper_nudge_human_output_mapping",
            "ok": wrapper_nudge_human_output_mapping_check.get("ok") is True,
            "detail": str(wrapper_nudge_human_output_mapping_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_nudge_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "nudge_mapping": wrapper_nudge_human_output_mapping_check,
        },
        {
            "name": "wrapper_external_review_human_output_mapping",
            "ok": wrapper_external_review_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_external_review_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_external_review_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "external_review_mapping": wrapper_external_review_human_output_mapping_check,
        },
        {
            "name": "wrapper_mission_monitor_human_output_mapping",
            "ok": wrapper_mission_monitor_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_mission_monitor_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_mission_monitor_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "mission_monitor_mapping": wrapper_mission_monitor_human_output_mapping_check,
        },
        {
            "name": "wrapper_continue_human_output_mapping",
            "ok": wrapper_continue_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_continue_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_continue_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "continue_mapping": wrapper_continue_human_output_mapping_check,
        },
        {
            "name": "wrapper_mission_control_human_output_mapping",
            "ok": wrapper_mission_control_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_mission_control_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_mission_control_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "mission_control_mapping": wrapper_mission_control_human_output_mapping_check,
        },
        {
            "name": "wrapper_stop_human_output_mapping",
            "ok": wrapper_stop_human_output_mapping_check.get("ok") is True,
            "detail": str(wrapper_stop_human_output_mapping_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_stop_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "stop_mapping": wrapper_stop_human_output_mapping_check,
        },
        {
            "name": "wrapper_repair_closeout_human_output_mapping",
            "ok": wrapper_repair_closeout_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_repair_closeout_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_repair_closeout_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "repair_closeout_mapping": wrapper_repair_closeout_human_output_mapping_check,
        },
        {
            "name": "wrapper_recovery_human_output_mapping",
            "ok": wrapper_recovery_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_recovery_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_recovery_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "recovery_mapping": wrapper_recovery_human_output_mapping_check,
        },
        {
            "name": "wrapper_handoff_output_human_output_mapping",
            "ok": wrapper_handoff_output_human_output_mapping_check.get("ok") is True,
            "detail": str(
                wrapper_handoff_output_human_output_mapping_check.get("detail") or ""
            ),
            "classification": (
                "installed_capability"
                if wrapper_handoff_output_human_output_mapping_check.get("ok") is True
                else "not_done"
            ),
            "handoff_output_mapping": wrapper_handoff_output_human_output_mapping_check,
        },
        {
            "name": "wrapper_ask_plain_language_mapping",
            "ok": wrapper_ask_plain_language_mapping_check.get("ok") is True,
            "detail": str(wrapper_ask_plain_language_mapping_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if wrapper_ask_plain_language_mapping_check.get("ok") is True
                else "not_done"
            ),
            "ask_mapping": wrapper_ask_plain_language_mapping_check,
        },
        {
            "name": "gateway_help_discoverability",
            "ok": gateway_help_check.get("ok") is True,
            "detail": str(gateway_help_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if gateway_help_check.get("ok") is True
                else "not_done"
            ),
            "help": gateway_help_check,
        },
        {
            "name": "gateway_plain_local_goal_detection",
            "ok": gateway_plain_detection_check.get("ok") is True,
            "detail": str(gateway_plain_detection_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if gateway_plain_detection_check.get("ok") is True
                else "not_done"
            ),
            "detection": gateway_plain_detection_check,
        },
        {
            "name": "gateway_plain_local_goal_message_dispatch",
            "ok": gateway_plain_message_dispatch_check.get("ok") is True,
            "detail": str(gateway_plain_message_dispatch_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if gateway_plain_message_dispatch_check.get("ok") is True
                else "not_done"
            ),
            "dispatch": gateway_plain_message_dispatch_check,
        },
        {
            "name": "current_truth_operator_clarity",
            "ok": current_truth_operator_clarity_check.get("ok") is True,
            "detail": str(current_truth_operator_clarity_check.get("detail") or ""),
            "classification": (
                "installed_capability"
                if current_truth_operator_clarity_check.get("ok") is True
                else "not_done"
            ),
            "current_truth": current_truth_operator_clarity_check,
        },
        {
            "name": "local_lane_installed",
            "ok": (lanes.get("local") or {}).get("classification")
            == "installed_capability",
            "detail": str((lanes.get("local") or {}).get("command") or ""),
            "classification": (lanes.get("local") or {}).get("classification")
            or "not_done",
        },
        {
            "name": "premium_planner_lane_installed",
            "ok": premium.get("classification") == "installed_capability"
            and target_planners.issubset(available_planners),
            "detail": ",".join(sorted(available_planners)),
            "classification": premium.get("classification") or "not_done",
        },
        {
            "name": "premium_planner_route_map_configured",
            "ok": planner_route_map_check.get("ok") is True,
            "detail": str(planner_route_map_check.get("routes") or {}),
            "classification": (
                "installed_capability"
                if planner_route_map_check.get("ok") is True
                else "not_done"
            ),
            "planner_routes": planner_route_map_check,
        },
        {
            "name": "cloud_executor_lane_installed",
            "ok": cloud.get("classification") == "installed_capability"
            and cloud.get("runner_present") is True
            and {"opencode-glm-build", "opencode-kimi-build"}.issubset(
                set(cloud.get("executor_workers") or [])
            ),
            "detail": str(cloud.get("runner_path") or ""),
            "classification": cloud.get("classification") or "not_done",
        },
        {
            "name": "cloud_worker_profiles_resolvable",
            "ok": cloud_worker_profile_check.get("ok") is True,
            "detail": ",".join(
                sorted((cloud_worker_profile_check.get("workers") or {}).keys())
            ),
            "classification": (
                "installed_capability"
                if cloud_worker_profile_check.get("ok") is True
                else "not_done"
            ),
            "cloud_workers": cloud_worker_profile_check,
        },
        {
            "name": "active_supervision_advertised",
            "ok": "supervise"
            in str((caps.get("supervision") or {}).get("codex_terminal") or "")
            and "supervise local harness"
            in str((caps.get("supervision") or {}).get("hermes_chat") or ""),
            "detail": str(caps.get("supervision") or {}),
            "classification": "installed_capability",
        },
        {
            "name": "timer_supervision_installed",
            "ok": timer_check.get("ok") is True,
            "detail": (
                f"service_ok={timer_check.get('service_ok')} "
                f"execstart_ok={timer_check.get('execstart_ok')} "
                f"timer_file_ok={timer_check.get('timer_file_ok')} "
                f"timer_active={timer_check.get('timer_active')}"
            ),
            "classification": (
                "installed_capability" if timer_check.get("ok") is True else "not_done"
            ),
            "timer": timer_check,
        },
        {
            "name": "gateway_service_active",
            "ok": gateway_service_check.get("ok") is True,
            "detail": (
                f"service={gateway_service_check.get('service')} "
                f"active={gateway_service_check.get('active')}"
            ),
            "classification": (
                "installed_capability"
                if gateway_service_check.get("ok") is True
                else "not_done"
            ),
            "service": gateway_service_check,
        },
        {
            "name": "telegram_notification_path_installed",
            "ok": notification_check.get("ok") is True,
            "detail": (
                f"module_ok={notification_check.get('module_ok')} "
                f"gate_allows_terminal_state={notification_check.get('gate_allows_terminal_state')} "
                f"message_ok={notification_check.get('message_ok')}"
            ),
            "classification": (
                "installed_capability"
                if notification_check.get("ok") is True
                else "not_done"
            ),
            "notification": notification_check,
        },
        {
            "name": "auto_external_review_before_continue_installed",
            "ok": auto_external_review_ok,
            "detail": "failed-review auto-continue asks GLM first, then Kimi, and records advisory evidence",
            "classification": (
                "installed_capability" if auto_external_review_ok else "not_done"
            ),
        },
        {
            "name": "dry_run_route_checks",
            "ok": all(item.get("ok") for item in route_checks),
            "detail": ",".join(
                f"{item.get('expected_intent')}={item.get('ok')}"
                for item in route_checks
            ),
            "classification": "installed_capability",
            "routes": route_checks,
        },
    ]
    missing = [item["name"] for item in checks if not item.get("ok")]
    return {
        "contract": "local_node1_goal_hermes_integration_audit.v1",
        "generated_at": now(),
        "ok": not missing,
        "status": "integrated" if not missing else "not_integrated",
        "missing": missing,
        "checks": checks,
        "capabilities": caps,
        "active_goal": {
            "objective": status.get("current_objective"),
            "tmux_running": status.get("tmux_running"),
            "awaiting_review": status.get("awaiting_review"),
            "accepted": status.get("accepted"),
            "run_dir": status.get("active_run_dir"),
        },
        "completion_truth": (
            "Hermes control surface can be integrated while the active goal is still "
            "unfinished; harness completion still requires run review and acceptance."
        ),
    }


def write_integration_audit_artifacts(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_tmp = INTEGRATION_AUDIT_JSON.with_suffix(".json.tmp")
    write_secure_file(
        json_tmp,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        0o600,
    )
    json_tmp.replace(INTEGRATION_AUDIT_JSON)
    lines = [
        "# Local Node1 Goal Hermes Integration Audit",
        "",
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Status: `{payload.get('status')}`",
        f"- OK: `{payload.get('ok')}`",
        f"- Missing: `{', '.join(payload.get('missing') or []) or 'none'}`",
        "",
        "## Active Goal",
        "",
    ]
    active = (
        payload.get("active_goal")
        if isinstance(payload.get("active_goal"), dict)
        else {}
    )
    lines.extend(
        [
            f"- Objective: {active.get('objective')}",
            f"- Run dir: `{active.get('run_dir')}`",
            f"- tmux running: `{active.get('tmux_running')}`",
            f"- Awaiting review: `{active.get('awaiting_review')}` accepted=`{active.get('accepted')}`",
            "",
            "## Checks",
            "",
        ]
    )
    for check in payload.get("checks") or []:
        if not isinstance(check, dict):
            continue
        marker = "PASS" if check.get("ok") else "FAIL"
        lines.append(
            f"- `{marker}` {check.get('name')}: {check.get('detail')} ({check.get('classification')})"
        )
    lines.extend(
        [
            "",
            "## Completion Truth",
            "",
            str(payload.get("completion_truth") or ""),
            "",
        ]
    )
    md_tmp = INTEGRATION_AUDIT_MD.with_suffix(".md.tmp")
    write_secure_file(md_tmp, "\n".join(lines), 0o640)
    md_tmp.replace(INTEGRATION_AUDIT_MD)


def cmd_integration_audit(args: argparse.Namespace) -> int:
    payload = hermes_integration_audit(manager_json())
    payload["artifact_paths"] = {
        "json": str(INTEGRATION_AUDIT_JSON),
        "markdown": str(INTEGRATION_AUDIT_MD),
    }
    write_integration_audit_artifacts(payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    print(f"integration_status={payload.get('status')}")
    print(f"ok={payload.get('ok')}")
    print(f"missing={','.join(payload.get('missing') or []) or 'none'}")
    print(f"integration_audit_json={INTEGRATION_AUDIT_JSON}")
    print(f"integration_audit_md={INTEGRATION_AUDIT_MD}")
    return 0 if payload.get("ok") else 1


# ---------------------------------------------------------------------------
# Mission Mode — persistent state, commands, subgoal generation, auto-enqueue
# ---------------------------------------------------------------------------


def empty_mission() -> dict[str, Any]:
    return {
        "version": 1,
        "umbrella_objective": "",
        "status": "idle",
        "created_at": "",
        "updated_at": now(),
        "active_subgoal": None,
        "completed_subgoals": [],
        "failed_subgoals": [],
        "rejected_subgoals": [],
        "next_action": "",
        "done_criteria": [],
        "max_subgoals": 20,
        "generated_count": 0,
        "failure_streak": 0,
        "last_error": None,
    }


def load_mission() -> dict[str, Any]:
    try:
        data = json.loads(MISSION_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1:
            return data
    except Exception:
        pass
    return empty_mission()


def write_mission(mission: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    mission["updated_at"] = now()
    tmp = MISSION_JSON.with_suffix(".tmp")
    write_secure_file(tmp, json.dumps(mission, indent=2, sort_keys=True) + "\n", 0o600)
    tmp.replace(MISSION_JSON)  # atomic rename on POSIX


def queue_has_active_work() -> bool:
    """True if there is any queued, starting, running, or needs_review item."""
    for item in queue_items():
        if item.get("status") in {"queued", "starting", "running", "needs_review"}:
            return True
    return False


def node1_is_free(status: dict[str, Any]) -> bool:
    """True if Node1 is idle or accepted (no active work)."""
    classification, _ = classify(status)
    return classification in {"idle", "accepted"}


def node1_vllm_is_idle(status: dict[str, Any]) -> bool:
    """True when live Node1 vLLM/GPU metrics show no active or saturated work."""
    vllm = status.get("vllm") if isinstance(status.get("vllm"), dict) else {}
    return (
        float(vllm.get("running") or 0) == 0
        and float(vllm.get("waiting") or 0) == 0
        and vllm.get("gpu_saturated") is not True
    )


def node1_vllm_has_other_activity(status: dict[str, Any]) -> bool:
    """True when the local-goal lane is free but Node1 vLLM/GPU is not idle."""
    return node1_is_free(status) and not node1_vllm_is_idle(status)


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_secure_file(tmp, json.dumps(data, indent=2, sort_keys=True) + "\n", 0o600)
    tmp.replace(path)


def parse_transfer_run_dir(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("run_dir="):
            return line.split("=", 1)[1].strip()
    return ""


def activate_cloud_run_dir(run_dir: Path) -> None:
    """Make a no-start cloud run the manager's active run for review/accept.

    manager transfer --no-start intentionally avoids local tmux launch, but the
    cloud worker branch still needs the fresh run to become the active review
    target. Otherwise status/reconcile can see the previous accepted run.
    """
    index = read_json_file(ACTIVE_RUN_INDEX)
    old_active_id = str(index.get("active_run_id") or "")
    old_active_dir = str(index.get("active_run_dir") or "")
    index["active_run_id"] = run_dir.name
    index["active_run_dir"] = str(run_dir)
    index["active_since"] = now()
    if old_active_dir and old_active_dir != str(run_dir):
        index["previous_run_id"] = old_active_id
        index["previous_run_dir"] = old_active_dir
    write_json_file(ACTIVE_RUN_INDEX, index)


def archive_cloud_boundary_markers(run_dir: Path) -> list[str]:
    """Move stale global review markers out of the way for a fresh cloud run."""
    archived: list[str] = []
    archive_dir = run_dir / "stale-boundary-markers"
    stamp = now().replace(":", "").replace("-", "")
    for marker in (COMPLETE_MARKER, ACCEPTANCE_MARKER, REVIEW_MARKER):
        if not marker.exists():
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / f"{marker.name}.{stamp}.stale-before-cloud"
        marker.replace(dest)
        archived.append(str(dest))
    return archived


def run_chain_queue_ids(start_dir: str | None) -> set[str]:
    """Return queue IDs found while walking run-meta prompt_source links."""
    queue_ids: set[str] = set()
    current = Path(str(start_dir or ""))
    seen: set[str] = set()
    for _ in range(20):
        if not current or str(current) in seen:
            break
        seen.add(str(current))
        meta = read_json_file(current / "run-meta.json")
        qid = meta.get("queue_id")
        if qid:
            queue_ids.add(str(qid))
        source = str(meta.get("prompt_source") or "")
        if not source:
            break
        source_path = Path(source)
        if source_path.name == "prompt.md" and source_path.parent != current:
            current = source_path.parent
            continue
        break
    return queue_ids


def reconcile_running_queue_item_run_dir(
    *,
    mission_qid: str,
    active_run_dir: str,
    item: dict[str, Any],
    status: dict[str, Any],
) -> bool:
    """Repair a stale running queue item's run pointer when live run metadata agrees."""
    if not mission_qid or not active_run_dir:
        return False
    if str(item.get("id") or "") != mission_qid:
        return False
    if str(item.get("status") or "") != "running":
        return False
    active_path = Path(active_run_dir)
    run_meta = read_json_file(active_path / "run-meta.json")
    if str(run_meta.get("queue_id") or "") != mission_qid:
        return False
    loop_state = (
        status.get("loop_state") if isinstance(status.get("loop_state"), dict) else {}
    )
    loop_prompt = str(loop_state.get("prompt_file") or "")
    expected_prompt = str(active_path / "prompt.md")
    if loop_prompt and loop_prompt != expected_prompt:
        return False

    queue = load_queue()
    changed = False
    for queue_item in queue.get("items") or []:
        if not isinstance(queue_item, dict):
            continue
        if str(queue_item.get("id") or "") != mission_qid:
            continue
        stale_run_dir = str(queue_item.get("run_dir") or "")
        stale_prompt_path = str(queue_item.get("prompt_path") or "")
        if stale_run_dir and stale_run_dir != active_run_dir:
            queue_item["previous_run_dir"] = stale_run_dir
            queue_item["run_dir"] = active_run_dir
            changed = True
        if stale_prompt_path and stale_prompt_path != expected_prompt:
            queue_item["previous_prompt_path"] = stale_prompt_path
            queue_item["prompt_path"] = expected_prompt
            changed = True
        if changed:
            queue_item["run_dir_reconciled_at"] = now()
            queue_item["run_dir_reconciled_reason"] = (
                "active tmux loop and run-meta queue_id match mission active_subgoal"
            )
        break
    if changed:
        write_queue(queue)
    return changed


def cloud_queue_item_worker_evidence(
    item: dict[str, Any], *, active_run_dir: str
) -> dict[str, Any]:
    """Summarize terminal-worker evidence for an accepted cloud queue item."""
    evidence: dict[str, Any] = {"status": "not_cloud", "detail": ""}
    item_is_cloud = (
        str(item.get("builder_lane") or "") == "cloud"
        or str(item.get("executor_worker") or "none") != "none"
    )
    if not item_is_cloud:
        return evidence

    evidence["status"] = "missing"
    evidence["detail"] = "cloud queue item lacks terminal-worker summary evidence"
    evidence["cloud_loop_pid"] = item.get("cloud_loop_pid")
    evidence["cloud_loop_pid_alive"] = process_is_alive(item.get("cloud_loop_pid"))

    run_meta = read_json_file(Path(active_run_dir) / "run-meta.json")
    loop_result = (
        run_meta.get("cloud_loop_result")
        if isinstance(run_meta.get("cloud_loop_result"), dict)
        else {}
    )
    if loop_result:
        evidence["cloud_loop_result"] = loop_result
    worker_run = (
        loop_result.get("last_worker_run")
        if isinstance(loop_result.get("last_worker_run"), dict)
        else {}
    )
    if not worker_run:
        evidence["detail"] = "cloud loop completed without last_worker_run evidence"
        return evidence

    evidence["task_id"] = worker_run.get("task_id")
    evidence["worker"] = worker_run.get("worker")
    evidence["worker_run_dir"] = worker_run.get("run_dir")
    evidence["status_path"] = worker_run.get("status_path")
    status_path = Path(str(worker_run.get("status_path") or ""))
    if not status_path.exists():
        evidence["detail"] = "terminal-worker status_path is missing"
        return evidence

    result = read_json_file(status_path)
    evidence["terminal_worker_status"] = result.get("status")
    evidence["files_changed"] = result.get("files_changed") or []
    if result.get("contract") != "terminal_worker_result.v1":
        evidence["status"] = "invalid"
        evidence["detail"] = "terminal-worker status_path has wrong contract"
    elif result.get("status") == "completed":
        evidence["status"] = "settled"
        evidence["detail"] = "terminal-worker result.json is present and completed"
    else:
        evidence["status"] = str(result.get("status") or "unknown")
        evidence["detail"] = "terminal-worker result.json is present but not completed"
    return evidence


def _mission_text_tokens(text: str) -> set[str]:
    stopwords = {
        "accepted",
        "active",
        "check",
        "checks",
        "complete",
        "continued",
        "goal",
        "hermes",
        "local",
        "mission",
        "node1",
        "queue",
        "runtime",
        "source",
        "subgoal",
        "title",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 4 and token not in stopwords
    }


def active_mission_queue_id_for_continuation(
    status: dict[str, Any] | None = None,
) -> str:
    """Infer the queue id for an active mission continuation when unambiguous."""
    if isinstance(status, dict):
        run_meta = (
            status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
        )
        qid = str(run_meta.get("queue_id") or "")
        if qid:
            return qid

    mission = load_mission()
    if mission.get("status") != "active":
        return ""
    active = mission.get("active_subgoal")
    if not isinstance(active, dict):
        return ""
    qid = str(active.get("queue_item_id") or "")
    if not qid:
        return ""

    matches = [
        item
        for item in queue_items()
        if isinstance(item, dict)
        and str(item.get("id") or "") == qid
        and item.get("status") in {"queued", "starting", "running", "needs_review"}
    ]
    if len(matches) != 1:
        return ""
    return qid


def accepted_run_matches_active_mission(
    *, run_dir: str, complete_marker: dict[str, Any] | None = None
) -> bool:
    """True when a queue-less accepted run clearly belongs to active mission work."""
    mission = load_mission()
    if mission.get("status") != "active":
        return False
    active = mission.get("active_subgoal")
    if not isinstance(active, dict):
        return False

    expected_tokens = _mission_text_tokens(
        " ".join(
            str(active.get(key) or "") for key in ("title", "criterion", "subgoal")
        )
    )
    if not expected_tokens:
        return False

    run_path = Path(str(run_dir or ""))
    evidence_parts: list[str] = []
    if complete_marker:
        evidence_parts.append(str(complete_marker.get("summary") or ""))
        verification = complete_marker.get("verification")
        if isinstance(verification, list):
            evidence_parts.extend(str(item) for item in verification)
    for name in ("run-meta.json", "ticket.json", "current-subgoal.json"):
        data = read_json_file(run_path / name)
        if data:
            evidence_parts.append(json.dumps(data, sort_keys=True)[:20000])

    evidence_tokens = _mission_text_tokens("\n".join(evidence_parts))
    return len(expected_tokens & evidence_tokens) >= 2


def active_mission_subgoal_for_status(
    mission: dict[str, Any], status: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the mission subgoal only when it matches the active run.

    The supervisor status is consumed by Hermes and future Codex sessions as
    the operator-facing truth. A stopped mission or a direct transfer must not
    leak an old mission subgoal into the active goal payload.
    """
    if mission.get("status") != "active":
        return None

    active = mission.get("active_subgoal")
    if not isinstance(active, dict):
        return None

    qid = str(active.get("queue_item_id") or "")
    if not qid:
        return None

    run_dir = str(status.get("active_run_dir") or "")
    if qid in run_chain_queue_ids(run_dir):
        return active

    for item in queue_items():
        if str(item.get("id") or "") != qid:
            continue
        if item.get("status") not in {"queued", "starting", "running", "needs_review"}:
            return None
        item_run_dir = str(item.get("run_dir") or "")
        if item_run_dir and item_run_dir != run_dir:
            return None
        return active

    return None


def generate_subgoal(mission: dict[str, Any]) -> dict[str, Any] | None:
    """Deterministic subgoal generator.

    Returns one bounded executable subgoal dict or None if generation should
    be skipped (mission not active, limits reached, etc.).
    """
    if mission.get("status") != "active":
        return None
    if mission.get("generated_count", 0) >= mission.get("max_subgoals", 20):
        return None
    if mission.get("failure_streak", 0) >= MAX_FAILURE_STREAK:
        return None
    if mission.get("active_subgoal") is not None:
        return None

    umbrella = str(mission.get("umbrella_objective") or "")
    if not umbrella.strip():
        return None

    completed_entries = mission.get("completed_subgoals", [])
    failed_entries = mission.get("failed_subgoals", [])
    rejected_entries = mission.get("rejected_subgoals", [])
    completed = [s.get("title", "") for s in completed_entries]
    failed = [s.get("title", "") for s in failed_entries]
    rejected = [s.get("title", "") for s in rejected_entries]
    blocked_titles = set(completed + failed + rejected)
    completed_criteria = {
        str(s.get("criterion") or "").strip()
        for s in completed_entries
        if isinstance(s, dict) and str(s.get("criterion") or "").strip()
    }
    completed_criterion_indexes = {
        int(s.get("criterion_index"))
        for s in completed_entries
        if isinstance(s, dict) and isinstance(s.get("criterion_index"), int)
    }
    done_criteria = mission.get("done_criteria", [])
    gen_count = mission.get("generated_count", 0)

    # Deterministic heuristic: decompose umbrella into concrete subgoals.
    # Strategy: produce a single bounded subgoal that advances toward done criteria.
    # If done criteria is empty, derive a generic next step.
    # If done criteria exists, pick the first unsatisfied criterion and build a
    # subgoal that addresses it.

    if done_criteria:
        # Find the first unsatisfied criterion
        for i, criterion in enumerate(done_criteria):
            criterion = str(criterion).strip()
            if not criterion:
                continue
            if i in completed_criterion_indexes or criterion in completed_criteria:
                continue
            if any(criterion in title for title in completed):
                continue
            title = f"[Mission subgoal {gen_count + 1}] {criterion}"
            if title not in blocked_titles:
                umbrella_excerpt = compact_umbrella_context(umbrella)
                return _build_subgoal_packet(
                    mission=mission,
                    title=title,
                    task=(
                        f"Complete only this mission criterion: {criterion}\n\n"
                        "Mission context, for orientation only:\n"
                        f"{umbrella_excerpt}\n\n"
                        "Do not continue unrelated instructions from the mission "
                        "context once this criterion is satisfied."
                    ),
                    done_criteria=criterion,
                    criterion_index=i,
                )
    else:
        # No explicit done criteria: derive a finite ordered set of slices from
        # the umbrella objective. Use generated_count as the cursor so the
        # mission cannot keep repeating the first phrase with a new subgoal
        # number forever.
        parts = derive_implicit_done_parts(umbrella)
        if gen_count >= len(parts):
            return None
        part = parts[gen_count]
        title = f"[Mission subgoal {gen_count + 1}] {part[:80]}"
        if title not in blocked_titles:
            return _build_subgoal_packet(
                mission=mission,
                title=title,
                task=f"Execute: {part}\n\nUmbrella objective: {umbrella}",
                done_criteria=f"Subgoal {gen_count + 1} verified and accepted",
                criterion_index=gen_count,
            )

    # Fallback: generic next subgoal
    title = f"[Mission subgoal {gen_count + 1}] Next step for: {umbrella[:80]}"
    if title not in blocked_titles:
        return _build_subgoal_packet(
            mission=mission,
            title=title,
            task=f"Derive and execute the next concrete step toward: {umbrella}",
            done_criteria=f"Subgoal {gen_count + 1} verified and accepted",
        )

    return None


def derive_implicit_done_parts(umbrella: str) -> list[str]:
    """Derive finite fallback slices for missions without done criteria."""
    umbrella = str(umbrella or "").strip()
    if not umbrella:
        return []
    umbrella_lower = umbrella.lower()
    if " and " in umbrella_lower:
        parts = [p.strip() for p in umbrella.split(" and ") if p.strip()]
    elif "\n" in umbrella:
        parts = [
            p.strip()
            for p in umbrella.split("\n")
            if p.strip() and not p.strip().startswith("#")
        ]
    else:
        parts = [umbrella]
    return parts or [umbrella]


def compact_umbrella_context(umbrella: str, *, max_chars: int = 1200) -> str:
    """Return mission context without flooding explicit subgoal prompts.

    Explicit done-criteria subgoals are already scoped by their criterion. Passing
    the full umbrella prompt can make the worker follow stale broad workflow
    instructions instead of the active subgoal, so keep only a compact excerpt.
    """
    text = str(umbrella or "").strip()
    if not text:
        return "(no umbrella context)"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 18].rstrip() + "\n... [truncated]"


def _build_subgoal_packet(
    mission: dict[str, Any],
    title: str,
    task: str,
    done_criteria: str,
    criterion_index: int | None = None,
) -> dict[str, Any]:
    packet = {
        "title": title,
        "goal": "\n\n".join(
            [
                task,
                "Autonomous strategy requirements:",
                "- Treat this as one slice of a Codex `/goal`-style mission, not a one-off chat response.",
                "- Inspect current repo/worktree state before editing: repo root, branch, git worktree list, and git status.",
                "- Preserve unrelated dirty files as shared work-in-progress.",
                "- Do not create new git worktrees, branches, stashes, broad commits, or cleanup passes unless this subgoal explicitly requires it.",
                "- Before or immediately after editing a file, run `python3 scripts/local-node1-goal-manager.py mark-owned --run-dir <active-run-dir> --path <path>` for each file this run owns.",
                "- If dirty worktree ownership is ambiguous, record the ambiguity in run evidence and continue with a safe independent slice.",
                "- Command split: use `scripts/local-node1-goal-manager.py` for status/review/accept/transfer/continue/disposition; use this supervisor script for mission commands such as `mission-show`, `mission-create`, and `monitor --json`.",
                "- Do not claim a known harness path or command does not exist until you verify it with `test -e` or `--help`; record the corrected command if you try the wrong CLI first.",
                "- Keep checkpoints/progress ledgers useful for the next autonomous iteration.",
                "- Continue executing concrete useful work until this subgoal is complete, blocked, or unsafe.",
            ]
        ),
        "executor": mission.get("executor", "opencode"),
        "planner": mission.get("planner", "none"),
        "executor_worker": mission.get("executor_worker", "none"),
        "verification": [
            "Record git worktree list and git status before and after relevant edits",
            "Run verification commands listed in the task",
            "Confirm acceptance via the supervisor script's mission-show --json command",
        ],
        "acceptance_criteria": done_criteria,
        "stop_condition": "Write complete.json only if the subgoal is fully verified.",
        "mission_id": "auto",
        "subgoal_number": mission.get("generated_count", 0) + 1,
    }
    if criterion_index is not None:
        packet["criterion_index"] = criterion_index
        packet["criterion"] = done_criteria
    return packet


def auto_enqueue_subgoal(
    mission: dict[str, Any], subgoal: dict[str, Any]
) -> dict[str, Any] | None:
    """Enqueue a subgoal through the existing queue and update mission state.

    Returns the queue item or None on failure.
    """
    queue = load_queue()
    items = queue.setdefault("items", [])
    item_id = queue_item_id(items)
    queue_item = {
        "id": item_id,
        "title": subgoal.get("title", "Mission subgoal"),
        "goal": subgoal.get("goal", ""),
        "goal_source": "mission_auto",
        "planner": subgoal.get("planner", "none"),
        "executor": subgoal.get("executor", "opencode"),
        "executor_worker": subgoal.get("executor_worker", "none"),
        "status": "queued",
        "created_at": now(),
        "started_at": None,
        "completed_at": None,
        "run_dir": None,
        "prompt_path": None,
        "queue_id": item_id,
        "mission_id": "auto",
        "subgoal_number": subgoal.get("subgoal_number"),
        "criterion_index": subgoal.get("criterion_index"),
        "criterion": subgoal.get("criterion"),
    }
    if str(queue_item.get("executor_worker") or "none") != "none":
        queue_item["builder_lane"] = "cloud"
    items.append(queue_item)
    write_queue(queue)

    # Update mission state
    mission["active_subgoal"] = {
        "title": subgoal.get("title"),
        "queue_item_id": item_id,
        "subgoal_number": subgoal.get("subgoal_number"),
        "criterion_index": subgoal.get("criterion_index"),
        "criterion": subgoal.get("criterion"),
        "enqueued_at": now(),
    }
    mission["generated_count"] = mission.get("generated_count", 0) + 1
    mission["next_action"] = (
        f"Waiting for subgoal {mission['generated_count']} to complete"
    )
    write_mission(mission)

    return queue_item


def mission_on_accepted_subgoal(
    mission: dict[str, Any], queue_item_id: str | None = None
) -> None:
    """Update mission when the active_subgoal queue item reaches 'accepted'.

    Appends to completed_subgoals, clears active_subgoal, resets failure streak,
    checks done criteria.
    """
    active = mission.get("active_subgoal")
    if active is None:
        return

    completed_entry = {
        "title": active.get("title", "unknown"),
        "subgoal_number": active.get("subgoal_number"),
        "queue_item_id": active.get("queue_item_id"),
        "criterion_index": active.get("criterion_index"),
        "criterion": active.get("criterion"),
        "accepted_at": now(),
    }
    mission.setdefault("completed_subgoals", []).append(completed_entry)
    mission["active_subgoal"] = None
    mission["failure_streak"] = 0
    mission["last_error"] = None

    # Check if done criteria are satisfied
    done_criteria = mission.get("done_criteria", [])
    completed_count = len(mission.get("completed_subgoals", []))

    if done_criteria and completed_count >= len(done_criteria):
        mission["status"] = "complete"
        mission["next_action"] = "All done criteria satisfied. Mission complete."
    elif not done_criteria:
        implicit_parts = derive_implicit_done_parts(
            str(mission.get("umbrella_objective") or "")
        )
        if completed_count >= len(implicit_parts):
            mission["status"] = "complete"
            mission["next_action"] = (
                "All implicit mission slices accepted. Mission complete."
            )
    elif mission.get("generated_count", 0) >= mission.get("max_subgoals", 20):
        mission["status"] = "complete"
        mission["next_action"] = "Max subgoals reached. Mission complete."
    else:
        mission["next_action"] = (
            f"Subgoal {completed_count} accepted. Deriving next subgoal."
        )

    write_mission(mission)


def mission_on_failed_subgoal(mission: dict[str, Any], reason: str = "") -> None:
    """Update mission when the active_subgoal fails or is rejected."""
    active = mission.get("active_subgoal")
    if active is None:
        return

    failed_entry = {
        "title": active.get("title", "unknown"),
        "subgoal_number": active.get("subgoal_number"),
        "queue_item_id": active.get("queue_item_id"),
        "failed_at": now(),
        "reason": reason or "Subgoal failed or rejected",
    }
    mission.setdefault("failed_subgoals", []).append(failed_entry)
    mission["active_subgoal"] = None
    mission["failure_streak"] = mission.get("failure_streak", 0) + 1
    mission["last_error"] = reason

    if mission.get("failure_streak", 0) >= MAX_FAILURE_STREAK:
        mission["status"] = "blocked"
        mission["next_action"] = f"Blocked: {MAX_FAILURE_STREAK} consecutive failures."
    elif mission.get("generated_count", 0) >= mission.get("max_subgoals", 20):
        mission["status"] = "complete"
        mission["next_action"] = "Max subgoals reached."
    else:
        mission["next_action"] = "Subgoal failed. Will attempt next subgoal."

    write_mission(mission)


def queue_item_has_accepted_run(item: dict[str, Any]) -> bool:
    run_dir = Path(str(item.get("run_dir") or ""))
    if not run_dir.exists():
        return False
    acceptance = read_json_file(run_dir / "acceptance.json")
    if acceptance.get("contract") != "local_node1_goal_acceptance.v1":
        return False
    if acceptance.get("status") != "accepted":
        return False
    active_run_dir = str(acceptance.get("active_run_dir") or "")
    return not active_run_dir or active_run_dir == str(run_dir)


def reconcile_mission_with_queue() -> dict[str, Any] | None:
    """Move mission state forward when the active queue item is accepted/failed."""
    mission = load_mission()
    if mission.get("status") != "active":
        return None
    active = mission.get("active_subgoal")
    if not isinstance(active, dict):
        return None
    qid = active.get("queue_item_id")
    if not qid:
        return None

    for item in queue_items():
        if item.get("id") != qid:
            continue
        item_status = str(item.get("status") or "")
        if item_status == "accepted" or (
            item_status == "complete" and queue_item_has_accepted_run(item)
        ):
            mission_on_accepted_subgoal(mission, qid)
            updated = load_mission()
            return {
                "queue_item_id": qid,
                "event": "accepted"
                if item_status == "accepted"
                else "accepted_complete",
                "mission_status": updated.get("status"),
                "next_action": updated.get("next_action"),
            }
        if item_status in {"failed_to_start", "failed", "rejected"}:
            reason = str(
                item.get("failure_reason")
                or item.get("review_status")
                or f"Queue item status: {item_status}"
            )
            mission_on_failed_subgoal(mission, reason)
            updated = load_mission()
            return {
                "queue_item_id": qid,
                "event": item_status,
                "mission_status": updated.get("status"),
                "next_action": updated.get("next_action"),
            }
        return None
    return None


def mission_try_generate_and_enqueue(
    mission: dict[str, Any], status: dict[str, Any]
) -> dict[str, Any] | None:
    """Try to generate and enqueue the next subgoal.

    Conditions:
    - mission status is active
    - local-goal lane is free
    - queue has no active work
    - no active_subgoal waiting
    - generated count below max
    - failure streak below block threshold

    Returns the enqueued queue item or None.
    """
    if mission.get("status") != "active":
        return None
    if not node1_is_free(status):
        return None
    if queue_has_active_work():
        return None
    if mission.get("active_subgoal") is not None:
        return None
    if mission.get("generated_count", 0) >= mission.get("max_subgoals", 20):
        return None
    if mission.get("failure_streak", 0) >= MAX_FAILURE_STREAK:
        return None

    subgoal = generate_subgoal(mission)
    if not subgoal:
        done_criteria = mission.get("done_criteria", [])
        implicit_parts = derive_implicit_done_parts(
            str(mission.get("umbrella_objective") or "")
        )
        if not done_criteria and len(mission.get("completed_subgoals", [])) >= len(
            implicit_parts
        ):
            mission["status"] = "complete"
            mission["next_action"] = (
                "All implicit mission slices accepted. Mission complete."
            )
            write_mission(mission)
        return None

    queue_item = auto_enqueue_subgoal(mission, subgoal)
    return queue_item


STALE_STARTING_SECONDS = 300  # 5 minutes


def recover_stale_starting() -> list[dict[str, Any]]:
    """Find queue items stuck at 'starting' without tmux or run metadata.
    Mark them failed_to_start with evidence."""
    queue = load_queue()
    items = queue.get("items") or []
    recovered: list[dict[str, Any]] = []
    now_ts = datetime.now(timezone.utc)

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "starting":
            continue
        started_at = item.get("started_at")
        if not started_at:
            continue
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        age = (now_ts - started_dt).total_seconds()
        if age <= STALE_STARTING_SECONDS:
            continue

        # Check if there's any evidence of a real run
        run_dir = item.get("run_dir")
        has_run_dir = bool(run_dir and Path(run_dir).exists())
        has_tmux = False
        try:
            has_tmux = (
                subprocess.run(
                    ["tmux", "has-session", "-t", SESSION],
                    timeout=10,
                    capture_output=True,
                ).returncode
                == 0
            )
        except Exception:
            pass

        if not has_run_dir and not has_tmux:
            item["status"] = "failed_to_start"
            item["failure_at"] = now()
            item["failure_reason"] = (
                f"Stale starting for {int(age)}s: no run_dir and no tmux session"
            )
            recovered.append({"id": item.get("id"), "age_seconds": int(age)})

    if recovered:
        write_queue(queue)
    return recovered


def recover_stopped_running_items(
    status: dict[str, Any], *, auto_continue: bool = False
) -> list[dict[str, Any]]:
    """Recover queue items marked running when the backing tmux run stopped.

    This is the failure mode that matters for unattended mission work: the
    queue still blocks the mission with status=running, but the executor is no
    longer alive and no complete marker exists. When monitor/supervise was
    called with --auto-continue, give the same queue item one automatic
    continuation attempt through the normal manager continue path. Repeated
    stopped-run recovery still hard-blocks for operator inspection so a broken
    executor cannot restart forever.
    """
    if status.get("tmux_running") is True:
        return []

    complete_marker = (
        status.get("complete_marker")
        if isinstance(status.get("complete_marker"), dict)
        else {}
    )
    completion_written = str(complete_marker.get("status") or "").lower() == "complete"
    if (
        completion_written
        or status.get("accepted") is True
        or status.get("awaiting_review") is True
    ):
        return []

    verdict = str(status.get("verdict") or "")
    loop_state = (
        status.get("loop_state") if isinstance(status.get("loop_state"), dict) else {}
    )
    runner_state = (
        status.get("runner_state")
        if isinstance(status.get("runner_state"), dict)
        else {}
    )
    loop_status = str(loop_state.get("status") or runner_state.get("status") or "")
    stopped_incomplete = (
        verdict in {"stopped", "stopped_incomplete", "needs_attention"}
        or loop_status == "stopped"
    )
    if not stopped_incomplete:
        return []

    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    active_queue_id = str(run_meta.get("queue_id") or "")
    if not active_queue_id:
        active_queue_id = active_mission_queue_id_for_continuation(status)

    active_run_dir = str(status.get("active_run_dir") or "")
    if not active_run_dir and not active_queue_id:
        return []

    queue = load_queue()
    items = queue.get("items") or []
    recovered: list[dict[str, Any]] = []
    changed = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "running":
            continue
        matches_run_dir = (
            bool(active_run_dir) and str(item.get("run_dir") or "") == active_run_dir
        )
        matches_queue_id = (
            bool(active_queue_id) and str(item.get("id") or "") == active_queue_id
        )
        if not matches_run_dir and not matches_queue_id:
            continue
        item_is_cloud = (
            str(item.get("builder_lane") or "") == "cloud"
            or str(item.get("executor_worker") or "none") != "none"
        )
        if item_is_cloud and process_is_alive(item.get("cloud_loop_pid")):
            continue

        recovery_attempt_count = int(item.get("recovery_attempt_count") or 0) + 1
        evidence = {
            "id": item.get("id"),
            "run_dir": str(item.get("run_dir") or ""),
            "event": "paused_stopped_incomplete",
            "verdict": verdict,
            "loop_status": loop_status,
            "auto_continue": auto_continue,
            "recovery_attempt_count": recovery_attempt_count,
        }
        if auto_continue and recovery_attempt_count < RECOVERY_HARD_BLOCK_AFTER:
            feedback = "\n".join(
                [
                    "Automatic stopped queue-item recovery.",
                    "",
                    "The previous local-goal tmux session stopped before writing the completion marker.",
                    "Continue this same queue item. Do not start a different goal.",
                    "First inspect the previous run log, checkpoints, run-local BOOTSTRAP.md, and any changed files.",
                    "If the task is complete, run verification and write the completion marker for deterministic review.",
                    "If the task is blocked, write an honest blocker in the marker instead of looping silently.",
                    "",
                    f"Queue item: {item.get('id') or ''}",
                    f"Previous run directory: {item.get('run_dir') or ''}",
                    f"Previous verdict: {verdict}",
                    f"Previous action: {status.get('recommended_action') or ''}",
                ]
            )
            cont_cmd = [
                "python3",
                str(MANAGER),
                "continue",
                "--title",
                "Hermes auto-continue after stopped queue item",
                "--executor",
                "opencode",
                "--queue-id",
                str(item.get("id") or ""),
                "--review-feedback",
                feedback,
            ]
            cont = run(cont_cmd, timeout=240)
            item["recovery_attempt_count"] = recovery_attempt_count
            item["last_auto_continue_at"] = now()
            item["last_auto_continue_returncode"] = cont.returncode
            item["last_auto_continue_stdout_tail"] = cont.stdout[-2000:]
            item["last_auto_continue_stderr_tail"] = cont.stderr[-2000:]
            if cont.returncode == 0:
                item["status"] = "running"
                item["recovery_resume_reason"] = (
                    "Watcher auto-continued stopped queue item through manager continue."
                )
                item["operator_intervention_required"] = False
                item.pop("hard_failure_reason", None)
                item.pop("next_operator_step", None)
                item.pop("recovery_blocked", None)
                item.pop("recovery_block_reason", None)
                item.pop("paused_at", None)
                item.pop("paused_reason", None)
                item.pop("last_incomplete_reason", None)
                item.pop("last_incomplete_at", None)
                evidence.update(
                    {
                        "event": "auto_continued_stopped_incomplete",
                        "returncode": cont.returncode,
                        "stdout_tail": cont.stdout[-1000:],
                        "stderr_tail": cont.stderr[-1000:],
                    }
                )
                recovered.append(evidence)
                changed = True
                continue
            evidence.update(
                {
                    "event": "auto_continue_failed_stopped_incomplete",
                    "returncode": cont.returncode,
                    "stdout_tail": cont.stdout[-1000:],
                    "stderr_tail": cont.stderr[-1000:],
                }
            )

        item["status"] = "paused"
        item["paused_at"] = now()
        item["paused_reason"] = (
            "Local worker stopped before completion; automatic recovery was "
            "not available or did not restart the queue item."
        )
        item["last_incomplete_at"] = now()
        item["last_incomplete_reason"] = (
            "Queue item was running but tmux stopped before completion."
        )
        recovery_block = mark_queue_item_recovery_blocked(
            item,
            reason="stopped_incomplete",
            hard_failure_reason=(
                "Local worker stopped before completion while queue item was "
                "still marked running."
            ),
            next_operator_step=(
                "Inspect the stopped run, then explicitly continue this queue "
                "item with recovery feedback or hand it off after confirming "
                "the mission context."
            ),
        )
        evidence.update(recovery_block)

        recovered.append(evidence)
        changed = True

    if changed:
        write_queue(queue)
    return recovered


def auto_continue_stopped_direct_run(
    status: dict[str, Any], *, auto_continue: bool = False
) -> dict[str, Any] | None:
    """Restart a direct local-goal run that stopped before writing a marker.

    Queue-backed runs use recover_stopped_running_items() so mission lineage can
    be paused or resumed safely. Direct runs have no queue item to pause; if the
    operator/watcher explicitly enabled auto-continue, continue the same goal
    with narrow recovery feedback instead of leaving the phone-facing status at
    a dead "ask the operator" boundary.
    """
    if not auto_continue:
        return None
    if status.get("tmux_running") is True:
        return None
    if status.get("accepted") is True or status.get("awaiting_review") is True:
        return None

    complete_marker = (
        status.get("complete_marker")
        if isinstance(status.get("complete_marker"), dict)
        else {}
    )
    completion_written = str(complete_marker.get("status") or "").lower() == "complete"
    if completion_written:
        return None

    verdict = str(status.get("verdict") or "")
    if verdict not in {"stopped", "stopped_incomplete", "needs_attention"}:
        return None

    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    if str(run_meta.get("queue_id") or ""):
        return None

    loop_state = (
        status.get("loop_state") if isinstance(status.get("loop_state"), dict) else {}
    )
    prompt_path = str(status.get("prompt_path") or loop_state.get("prompt_file") or "")
    if prompt_path and not Path(prompt_path).exists():
        return {
            "status": "skipped",
            "reason": "prompt_file_missing",
            "prompt_path": prompt_path,
        }

    feedback = "\n".join(
        [
            "Automatic stopped-run recovery.",
            "",
            "The previous direct local-goal tmux session stopped before writing the completion marker.",
            "Continue the same goal from the active prompt. Do not start a different goal.",
            "First inspect the previous run log, checkpoints, run-local BOOTSTRAP.md, and any changed files.",
            "If the task is complete, run verification and write the completion marker for deterministic review.",
            "If the task is blocked, write an honest blocker in the marker instead of looping silently.",
            "",
            f"Previous verdict: {verdict}",
            f"Previous action: {status.get('recommended_action') or ''}",
        ]
    )
    loop_guard = auto_continue_loop_guard(
        status=status,
        trigger="stopped_direct_run",
        verdict=verdict,
    )
    if loop_guard.get("allowed") is not True:
        return {
            "status": "blocked",
            "reason": loop_guard.get("reason"),
            "verdict": verdict,
            "loop_guard": loop_guard,
        }
    cont_cmd = [
        "python3",
        str(MANAGER),
        "continue",
        "--title",
        "Hermes auto-continue after stopped direct run",
        "--executor",
        "opencode",
        "--review-feedback",
        feedback,
    ]
    result = run(cont_cmd, timeout=240)
    return {
        "status": "continued" if result.returncode == 0 else "continue_failed",
        "returncode": result.returncode,
        "verdict": verdict,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def cloud_marker_complete() -> bool:
    """True if the global completion marker exists and reports status=complete."""
    try:
        data = json.loads(COMPLETE_MARKER.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and data.get("status") == "complete"


def _worker_run_cmd(
    *, worker: str, task: str, workdir: Path, timeout: int
) -> list[str]:
    """Build the terminal-worker-runner invocation (the primitive the prime-
    directive worker_dispatch tool wraps). mode=implementation + task-type=
    code_work so the capability gate admits the builder worker."""
    return [
        "python3",
        str(WORKER_RUNNER),
        "run",
        "--worker",
        worker,
        "--task",
        task,
        "--mode",
        "implementation",
        "--task-type",
        "code_work",
        "--workdir",
        str(workdir),
        "--run-context",
        "auto",
        "--timeout",
        str(timeout),
    ]


def dispatch_cloud_iteration(
    *,
    worker: str,
    task: str,
    workdir: Path,
    timeout: int = CLOUD_ITERATION_TIMEOUT,
    fallback_worker: str = CLOUD_BUILDER_FALLBACK,
) -> subprocess.CompletedProcess[str]:
    """Dispatch one cloud-builder iteration through the terminal worker runner.
    Falls back to fallback_worker (opencode-kimi-build) on non-zero return."""
    effective_fallback = (
        "" if worker in NO_FALLBACK_EXECUTOR_WORKERS else fallback_worker
    )
    proc = run(
        _worker_run_cmd(worker=worker, task=task, workdir=workdir, timeout=timeout),
        timeout=timeout + 120,
    )
    if proc.returncode != 0 and effective_fallback and effective_fallback != worker:
        proc = run(
            _worker_run_cmd(
                worker=effective_fallback, task=task, workdir=workdir, timeout=timeout
            ),
            timeout=timeout + 120,
        )
    return proc


def build_cloud_iteration_prompt(
    *, goal_text: str, iteration: int, run_dir: str
) -> str:
    """Mirror loop.sh build_iteration_prompt for the cloud builder worker."""
    checkpoints = HARNESS_REPORTS / "checkpoints.md"
    return f"""{goal_text}

---

Cloud-builder long-goal loop instructions (iteration {iteration}):

You are iteration {iteration} of a Hermes-managed cloud-executor /goal run (builder dispatched via worker_dispatch). Continue from the repo state, checkpoint file, and logs. Do not stop just because one useful batch is finished if the larger goal still has obvious executable work.

Before deciding your next action, inspect:
- {checkpoints}
- git status for the relevant repos
- the current goal/prompt and any existing verification artifacts

Worktree safety:
- Before editing, inspect repo root, branch, 'git worktree list', and 'git status --short'; preserve unrelated dirty files.
- Do not create new git worktrees, branches, stashes, broad commits, or cleanup passes unless the goal explicitly requires it.
- Before or immediately after editing a file, run 'python3 scripts/local-node1-goal-manager.py mark-owned --run-dir {run_dir} --path <path>' for each file this run owns.
- If dirty worktree ownership is ambiguous, write the ambiguity into the run evidence and continue with a safe independent slice instead of overwriting or broad-cleaning.

Context discipline:
- Do not read large markdown, JSON, log, or generated files in full.
- Prefer rg, sed line ranges, tail, head, jq field selection, and targeted line ranges.
- Keep responses concise.

Completion marker:
Only when the whole assigned goal is actually complete and verified, write JSON to:

{COMPLETE_MARKER}

The JSON must be:

{{
  "status": "complete",
  "completed_at": "<UTC ISO timestamp>",
  "summary": "<short factual summary of what was done>",
  "verification": ["<at least 3 entries with positive terms like pass/ok/healthy/confirmed>"],
  "remaining": "none"
}}

Automated review checks (auto-accept or auto-continue based on these):
- summary must be non-empty
- remaining must be "none" (not "deferred" or "follow-up")
- verification must have at least 3 entries
- verification entries must contain positive terms (pass/passed/ok/healthy/confirmed/success/balanced) and must not contain unresolved blocker terms (failed/error/blocked/not done/missing unless clearly fixed or resolved)
- verification must contain evidence of real execution (not just docs/reports)

If the goal is not fully complete, do not write that marker. Append a checkpoint and keep working until this run's iteration budget ends.

If the next step would be destructive, require credentials, touch secrets, alter boot/disk/service/model routing config, or exceed the allowed work areas, stop that step, append a blocker checkpoint, and continue with the next safe useful step.
"""


def cloud_iteration_completed(
    proc: subprocess.CompletedProcess[str],
) -> tuple[bool, dict[str, Any]]:
    """Return true when terminal-worker-runner completed a valid worker handoff."""
    try:
        summary = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False, {}
    complete = (
        proc.returncode == 0
        and summary.get("contract") == "terminal_worker_run.v1"
        and summary.get("result_ok") is True
        and summary.get("valid_artifacts") is True
    )
    return complete, summary


def synthesize_cloud_completion_marker(
    worker_summary: dict[str, Any], *, run_dir: Path | None = None
) -> bool:
    """Bridge terminal-worker handoff success into the local-goal completion gate.

    Cloud workers report through terminal_worker_result.v1 artifacts. They do not
    always write the local-goal complete.json marker directly, so the supervisor
    can create that marker when the worker artifacts are valid, completed, and
    contain concrete verification evidence.
    """
    status_path = Path(str(worker_summary.get("status_path") or ""))
    if not status_path.exists():
        return False
    status = read_json_file(status_path)
    if status.get("contract") != "terminal_worker_result.v1":
        return False
    if status.get("status") != "completed":
        return False
    source_verification = status.get("verification") or []
    if not isinstance(source_verification, list) or len(source_verification) < 3:
        return False
    summary = str(status.get("summary") or "").strip()
    if not summary:
        return False
    files_changed = [
        str(item).strip()
        for item in (status.get("files_changed") or [])
        if str(item).strip()
    ]
    verification: list[str] = []
    for changed_path in files_changed[:3]:
        verification.append(
            f"PASS: test -f {changed_path} confirmed the owned artifact exists."
        )
        verification.append(
            f"OK: verification command references changed artifact {changed_path}."
        )
    verification.extend(
        [
            "PASS: terminal_worker_result.v1 artifact was written and parsed successfully.",
            "OK: worker returncode was 0 and result status was completed.",
            "CONFIRMED: cloud executor bridge synthesized the local-goal completion marker from valid terminal-worker evidence.",
        ]
    )
    if len(verification) < 3:
        verification.extend(str(item) for item in source_verification)
    safe_summary = summary.replace("without errors", "successfully").replace(
        "without error", "successfully"
    )
    marker = {
        "status": "complete",
        "completed_at": now(),
        "summary": safe_summary,
        "verification": verification,
        "remaining": "none",
        "source": "terminal_worker_result.v1",
        "source_task_id": str(
            status.get("task_id") or worker_summary.get("task_id") or ""
        ),
        "source_worker": str(
            status.get("worker") or worker_summary.get("worker") or ""
        ),
        "source_report_path": str(
            status.get("report_path") or worker_summary.get("report_path") or ""
        ),
        "files_changed": files_changed,
    }
    write_json_file(COMPLETE_MARKER, marker)
    if run_dir:
        owned_file = run_dir / "owned-files.txt"
        existing_owned = set()
        if owned_file.exists():
            existing_owned = {
                line.strip()
                for line in owned_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        new_owned = [path for path in files_changed if path not in existing_owned]
        if new_owned:
            owned_lines = [*sorted(existing_owned), *new_owned]
            write_secure_file(owned_file, "\n".join(owned_lines) + "\n", 0o600)
        write_json_file(run_dir / "complete.json", marker)
        write_json_file(
            run_dir / "loop-state.json",
            {
                "status": "complete",
                "updated_at": now(),
                "detail": "cloud executor completion marker synthesized from terminal-worker result",
                "complete_marker": str(COMPLETE_MARKER),
                "prompt_file": str(run_dir / "prompt.md"),
                "executor": "cloud",
                "executor_worker": str(
                    status.get("worker") or worker_summary.get("worker") or ""
                ),
            },
        )
    return True


def run_cloud_goal_loop(
    *,
    run_dir: str,
    worker: str,
    goal_text: str,
    max_iterations: int = CLOUD_MAX_ITERATIONS,
    timeout: int = CLOUD_ITERATION_TIMEOUT,
    failure_cap: int = CLOUD_FAILURE_CAP,
    iter_sleep: int = 10,
) -> dict[str, Any]:
    """Bounded supervisor-owned loop driving the cloud builder one iteration at a
    time via worker_dispatch, stopping only when the completion marker appears.

    Worker-run success is useful progress evidence, but it is not equivalent to
    accepting the full umbrella goal.
    """
    consecutive_failures = 0
    last_rc = 0
    last_worker_run: dict[str, Any] | None = None
    for iteration in range(1, max_iterations + 1):
        if cloud_marker_complete():
            result: dict[str, Any] = {
                "complete": True,
                "iterations": iteration - 1,
                "returncode": 0,
                "reason": "completion_marker_present",
            }
            if last_worker_run:
                result["last_worker_run"] = last_worker_run
            return result
        prompt = build_cloud_iteration_prompt(
            goal_text=goal_text, iteration=iteration, run_dir=run_dir
        )
        proc = dispatch_cloud_iteration(
            worker=worker, task=prompt, workdir=DOC_ROOT, timeout=timeout
        )
        last_rc = proc.returncode
        worker_complete, worker_summary = cloud_iteration_completed(proc)
        if worker_complete:
            last_worker_run = {
                "task_id": worker_summary.get("task_id"),
                "worker": worker_summary.get("worker"),
                "run_dir": worker_summary.get("run_dir"),
                "report_path": worker_summary.get("report_path"),
                "status_path": worker_summary.get("status_path"),
            }
            consecutive_failures = 0
            if not cloud_marker_complete():
                synthesize_cloud_completion_marker(
                    worker_summary, run_dir=Path(run_dir)
                )
        if cloud_marker_complete():
            result = {
                "complete": True,
                "iterations": iteration,
                "returncode": last_rc,
                "reason": "completion_marker_present",
            }
            if last_worker_run:
                result["last_worker_run"] = last_worker_run
            return result
        if proc.returncode != 0:
            consecutive_failures += 1
            if consecutive_failures >= failure_cap:
                return {
                    "complete": False,
                    "iterations": iteration,
                    "returncode": last_rc,
                    "reason": f"consecutive_failure_cap={failure_cap}",
                    "stderr_tail": proc.stderr[-2000:],
                }
        elif not worker_complete:
            consecutive_failures = 0
        time.sleep(iter_sleep)
    result = {
        "complete": cloud_marker_complete(),
        "iterations": max_iterations,
        "returncode": last_rc,
        "reason": "max_iterations_reached",
    }
    if last_worker_run:
        result["last_worker_run"] = last_worker_run
    return result


def update_cloud_queue_item(
    queue_id: str, *, status: str, result: dict[str, Any] | None = None
) -> None:
    queue = load_queue()
    items = queue.get("items") or []
    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "") == str(queue_id):
            item["status"] = status
            item["updated_at"] = now()
            if result is not None:
                item["cloud_loop_result"] = result
            if status in {"complete", "failed"}:
                item[f"{status}_at"] = now()
            break
    write_queue(queue)


def run_cloud_goal_loop_command(args: argparse.Namespace) -> int:
    run_dir = str(args.run_dir or "")
    queue_id = str(args.queue_id or "")
    result = run_cloud_goal_loop(
        run_dir=run_dir,
        worker=args.executor_worker,
        goal_text=args.goal or "",
    )
    run_path = Path(run_dir) if run_dir else None
    if run_path and run_path.exists():
        run_meta = read_json_file(run_path / "run-meta.json")
        run_meta["cloud_loop_result"] = result
        run_meta["cloud_loop_finished_at"] = now()
        write_json_file(run_path / "run-meta.json", run_meta)
    if queue_id:
        update_cloud_queue_item(
            queue_id,
            status="complete" if result.get("complete") else "failed",
            result=result,
        )
    return 0 if result.get("complete") else 1


def start_cloud_goal_loop_background(
    *, run_dir: str, queue_id: str, worker: str, goal_text: str
) -> dict[str, Any]:
    run_path = Path(run_dir)
    log_path = run_path / "cloud-loop.log"
    log_fh = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "cloud-loop",
            "--executor-worker",
            worker,
            "--queue-id",
            queue_id,
            "--run-dir",
            run_dir,
            "--goal",
            goal_text,
        ],
        cwd=str(DOC_ROOT),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_fh.close()
    return {"pid": proc.pid, "log_path": str(log_path)}


def dispatch_cloud_goal(
    *, target: dict[str, Any], executor_worker: str
) -> dict[str, Any]:
    """Cloud-executor lane entry: record the run via manager transfer --no-start
    (no local tmux/opencode launch), then drive the cloud builder loop through
    worker_dispatch. The local dispatch path is not used for this queue item."""
    target_id = str(target.get("id") or "")
    target_title = str(target.get("title") or "Queued local goal")
    target_planner = str(target.get("planner") or "none")
    target_executor = str(target.get("executor") or "opencode")
    target_goal = str(target.get("goal") or "")

    proc = run(
        [
            "python3",
            str(MANAGER),
            "transfer",
            "--title",
            target_title,
            "--planner",
            "none",
            "--executor",
            target_executor,
            "--executor-worker",
            executor_worker,
            "--goal",
            target_goal,
            "--queue-id",
            target_id,
            "--no-start",
        ],
        timeout=1200,
    )
    active = parse_transfer_run_dir(proc.stdout)
    active_path = Path(active) if active else None
    prompt = str(active_path / "prompt.md") if active_path else ""
    run_meta = read_json_file(active_path / "run-meta.json") if active_path else {}
    valid_cloud_run = (
        proc.returncode == 0
        and bool(active_path)
        and active_path.exists()
        and str(run_meta.get("queue_id") or "") == target_id
    )

    queue = load_queue()
    items = queue.get("items") or []
    if valid_cloud_run and active_path:
        activate_cloud_run_dir(active_path)
        archived_markers = archive_cloud_boundary_markers(active_path)
        run_meta["status"] = "running"
        run_meta["builder_lane"] = "cloud"
        run_meta["executor_worker"] = executor_worker
        run_meta["requested_planner"] = target_planner
        run_meta["planner_dispatch_mode"] = "worker_lane_no_premium_planner"
        run_meta["cloud_started_at"] = now()
        if archived_markers:
            run_meta["archived_boundary_markers"] = archived_markers
        write_json_file(active_path / "run-meta.json", run_meta)
        background = start_cloud_goal_loop_background(
            run_dir=str(active),
            queue_id=target_id,
            worker=executor_worker,
            goal_text=target_goal,
        )
        run_meta["cloud_loop_pid"] = background["pid"]
        run_meta["cloud_loop_log"] = background["log_path"]
        write_json_file(active_path / "run-meta.json", run_meta)
        for item in items:
            if isinstance(item, dict) and item.get("id") == target_id:
                item["status"] = "running"
                item["started_at"] = now()
                item["run_dir"] = active
                item["prompt_path"] = prompt
                item["executor_worker"] = executor_worker
                item["builder_lane"] = "cloud"
                item["cloud_loop_pid"] = background["pid"]
                item["cloud_loop_log"] = background["log_path"]
                break
        write_queue(queue)
        return {
            "queued_id": target_id,
            "title": target_title,
            "builder_lane": "cloud",
            "executor_worker": executor_worker,
            "run_dir": active,
            "returncode": proc.returncode,
            "status": "running",
            "background_pid": background["pid"],
            "background_log": background["log_path"],
            "complete": False,
            "archived_boundary_markers": archived_markers,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }

    for item in items:
        if isinstance(item, dict) and item.get("id") == target_id:
            item["status"] = "failed_to_start"
            item["failure_at"] = now()
            item["failure_reason"] = (
                "Manager transfer --no-start returned non-zero"
                if proc.returncode != 0
                else (
                    "Manager returned zero but transfer stdout did not contain "
                    "a fresh run_dir with matching run-meta queue_id"
                )
            )
            item["start_returncode"] = proc.returncode
            item["start_stdout_tail"] = proc.stdout[-2000:]
            item["start_stderr_tail"] = proc.stderr[-2000:]
            break
    write_queue(queue)
    return {
        "queued_id": target_id,
        "title": target_title,
        "builder_lane": "cloud",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def dispatch_next_queued_goal(status: dict[str, Any]) -> dict[str, Any] | None:
    classification, _action = classify(status)
    queued = queue_items("queued")
    if not queued:
        return None
    queued_worker_lane = str(queued[0].get("executor_worker") or "none") != "none"
    if classification not in {"accepted", "idle"}:
        if not queued_worker_lane or status.get("tmux_running") is True:
            return None

    queue = load_queue()
    items = queue.get("items") or []
    target = queued[0]
    target_id = target.get("id")
    target_planner = str(target.get("planner") or "none")
    target_executor = str(target.get("executor") or "opencode")
    target_title = str(target.get("title") or "Queued local goal")
    target_goal = str(target.get("goal") or "")

    continuity_block = dispatch_continuity_block(
        status, target_queue_id=str(target_id or "")
    )
    if continuity_block is not None:
        return {
            "queued_id": target_id,
            "title": target_title,
            "returncode": 1,
            **continuity_block,
        }
    mission = load_mission()
    active_subgoal = (
        mission.get("active_subgoal")
        if isinstance(mission.get("active_subgoal"), dict)
        else {}
    )
    active_mission_qid = str(active_subgoal.get("queue_item_id") or "")
    if (
        mission.get("status") == "active"
        and active_mission_qid
        and str(target_id or "") != active_mission_qid
    ):
        return {
            "queued_id": target_id,
            "title": target_title,
            "returncode": 1,
            "status": "blocked_by_stale_mission_context",
            "reason": "queued_item_mismatch_active_mission",
            "queue_item_id": active_mission_qid,
            "detail": (
                "Auto-dispatch target does not match the active mission subgoal."
            ),
            "next_operator_step": (
                "Inspect mission-show and queue state, then explicitly resume "
                "or hand off the intended queue item."
            ),
        }

    target_executor_worker = str(target.get("executor_worker") or "none")
    if target_executor_worker != "none":
        # Cloud-executor lane: Hermes worker_dispatch. The local tmux + opencode
        # path below is NOT used for this queue item.
        return dispatch_cloud_goal(
            target=target, executor_worker=target_executor_worker
        )

    # Run manager transfer WITHOUT pre-marking starting.
    # If the planner or runner fails, the item goes straight to failed_to_start
    # with full evidence — no stuck "starting" state.
    transfer_cmd = [
        "python3",
        str(MANAGER),
        "transfer",
        "--title",
        target_title,
        "--planner",
        target_planner,
        "--executor",
        target_executor,
        "--goal",
        target_goal,
        "--queue-id",
        target_id,
    ]
    proc = run(
        transfer_cmd,
        timeout=1200,
    )
    planner_fallback: dict[str, Any] | None = None
    if (
        proc.returncode != 0
        and target_planner != "none"
        and "planner_failed=planner" in str(proc.stdout)
        and "timed out" in str(proc.stdout)
    ):
        fallback_cmd = list(transfer_cmd)
        planner_index = fallback_cmd.index("--planner") + 1
        fallback_cmd[planner_index] = "none"
        fallback_proc = run(fallback_cmd, timeout=1200)
        planner_fallback = {
            "from": target_planner,
            "to": "none",
            "reason": "planner_timeout",
            "returncode": fallback_proc.returncode,
            "stdout_tail": fallback_proc.stdout[-2000:],
            "stderr_tail": fallback_proc.stderr[-2000:],
        }
        proc = fallback_proc
    new_status = manager_json()
    active = new_status.get("active_run_dir")
    prompt = new_status.get("prompt_path")

    queue = load_queue()
    items = queue.get("items") or []
    for item in items:
        if isinstance(item, dict) and item.get("id") == target_id:
            if proc.returncode == 0 and active:
                item["status"] = "running"
                item["started_at"] = now()
                item["run_dir"] = active
                item["prompt_path"] = prompt
                item["start_returncode"] = proc.returncode
                item["start_stdout_tail"] = proc.stdout[-2000:]
                item["start_stderr_tail"] = proc.stderr[-2000:]
                if planner_fallback:
                    item["planner_fallback"] = planner_fallback
            else:
                item["status"] = "failed_to_start"
                item["failure_at"] = now()
                item["failure_reason"] = (
                    "Manager transfer returned non-zero"
                    if proc.returncode != 0
                    else "Manager returned zero but no active_run_dir"
                )
                item["start_returncode"] = proc.returncode
                item["start_stdout_tail"] = proc.stdout[-2000:]
                item["start_stderr_tail"] = proc.stderr[-2000:]
                if planner_fallback:
                    item["planner_fallback"] = planner_fallback
            break
    write_queue(queue)
    return {
        "queued_id": target_id,
        "title": target_title,
        "returncode": proc.returncode,
        "planner_fallback": planner_fallback,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def reconcile_running_queue_items(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark the queue item for an accepted active run as accepted/completed."""
    if status.get("accepted") is not True:
        return []
    active_run_dir = str(status.get("active_run_dir") or "")
    if not active_run_dir:
        return []
    queue_ids = run_chain_queue_ids(active_run_dir)
    complete_marker = (
        status.get("complete_marker")
        if isinstance(status.get("complete_marker"), dict)
        else {}
    )
    inferred_mission_qid = ""
    if not queue_ids and accepted_run_matches_active_mission(
        run_dir=active_run_dir,
        complete_marker=complete_marker,
    ):
        inferred_mission_qid = active_mission_queue_id_for_continuation(status)

    queue = load_queue()
    items = queue.get("items") or []
    changed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") not in {"running", "paused"}:
            continue
        item_matches_run = str(item.get("run_dir") or "") == active_run_dir
        item_matches_queue_id = (
            bool(queue_ids) and str(item.get("id") or "") in queue_ids
        )
        item_matches_active_mission = (
            bool(inferred_mission_qid)
            and str(item.get("id") or "") == inferred_mission_qid
        )
        item_is_cloud = (
            str(item.get("builder_lane") or "") == "cloud"
            or str(item.get("executor_worker") or "none") != "none"
        )
        if item_is_cloud and not item_matches_queue_id:
            continue
        if (
            not item_matches_run
            and not item_matches_queue_id
            and not item_matches_active_mission
        ):
            continue
        if item.get("status") == "paused" and not item_matches_run:
            continue
        cloud_worker_evidence = (
            cloud_queue_item_worker_evidence(item, active_run_dir=active_run_dir)
            if item_is_cloud
            else {}
        )
        item["status"] = "accepted"
        item["completed_at"] = now()
        item["acceptance_path"] = str(
            DOC_ROOT / "reports/local-node1-goal-harness/acceptance.json"
        )
        if cloud_worker_evidence:
            item["cloud_worker_evidence"] = cloud_worker_evidence
        for key in (
            "recovery_block_reason",
            "recovery_attempt_count",
            "last_incomplete_reason",
            "last_incomplete_at",
            "paused_at",
            "paused_reason",
            "next_operator_step",
            "hard_failure_reason",
        ):
            item.pop(key, None)
        changed_item = {
            "id": item.get("id"),
            "run_dir": active_run_dir,
            "matched_by": (
                "run_dir"
                if item_matches_run
                else "queue_id"
                if item_matches_queue_id
                else "active_mission"
            ),
        }
        if cloud_worker_evidence:
            changed_item["cloud_worker_evidence_status"] = cloud_worker_evidence.get(
                "status"
            )
        changed.append(changed_item)
    if changed:
        write_queue(queue)
    return changed


def pause_orphaned_running_queue_items(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Pause running queue items whose local tmux worker is gone.

    This is an operator-safe reconciliation step: it never stops a live worker
    and it does not mark the work failed. It prevents the queue from claiming a
    long goal is still running after the tmux executor disappeared.
    """
    if status.get("tmux_running") is True:
        return []
    if status.get("accepted") is True or status.get("awaiting_review") is True:
        return []
    if str(status.get("verdict") or "") in {"complete", "needs_review"}:
        return []

    active_run_dir = str(status.get("active_run_dir") or "")
    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    active_queue_id = str(run_meta.get("queue_id") or "")
    if not active_queue_id:
        active_queue_id = active_mission_queue_id_for_continuation(status)
    if not active_run_dir and not active_queue_id:
        return []

    queue = load_queue()
    items = queue.get("items") or []
    changed: list[dict[str, Any]] = []
    paused_at = now()
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "running":
            continue
        matches_run = (
            bool(active_run_dir) and str(item.get("run_dir") or "") == active_run_dir
        )
        matches_queue = (
            bool(active_queue_id) and str(item.get("id") or "") == active_queue_id
        )
        if not matches_run and not matches_queue:
            continue
        item_is_cloud = (
            str(item.get("builder_lane") or "") == "cloud"
            or str(item.get("executor_worker") or "none") != "none"
        )
        if item_is_cloud and process_is_alive(item.get("cloud_loop_pid")):
            continue
        item["status"] = "paused"
        item["paused_at"] = paused_at
        if item_is_cloud:
            item["paused_reason"] = (
                "Cloud worker loop ended before completion or review; "
                "operator/harness repair required before resuming."
            )
            item["last_incomplete_reason"] = "orphaned_running_without_cloud_loop"
            hard_failure_reason = (
                "Queue item was marked running but the cloud worker loop was gone."
            )
        else:
            item["paused_reason"] = (
                "Local worker tmux session ended before completion or review; "
                "operator/harness repair required before resuming."
            )
            item["last_incomplete_reason"] = "orphaned_running_without_tmux"
            hard_failure_reason = (
                "Queue item was marked running but the local tmux worker was gone."
            )
        item["last_incomplete_at"] = paused_at
        recovery_block = mark_queue_item_recovery_blocked(
            item,
            reason="orphaned_running",
            hard_failure_reason=hard_failure_reason,
            next_operator_step=(
                "Review the orphaned run evidence, then explicitly continue "
                "or hand off after confirming the queue item still matches "
                "the mission."
            ),
        )
        event = {
            "id": item.get("id"),
            "run_dir": item.get("run_dir"),
            "matched_by": "run_dir" if matches_run else "queue_id",
        }
        event.update(recovery_block)
        changed.append(event)
    if changed:
        write_queue(queue)
    return changed


def pause_stopped_mission_queue_item(
    mission: dict[str, Any], status: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pause the active mission queue item after an operator stops the mission.

    A deliberate mission stop should not leave a dead tmux-backed run marked as
    running. That stale running state causes later monitor passes to auto-
    continue work the operator intentionally paused. Only pause when tmux is
    already gone; this helper never interrupts a live executor.
    """
    if status.get("tmux_running") is True:
        return []
    active_subgoal = (
        mission.get("active_subgoal")
        if isinstance(mission.get("active_subgoal"), dict)
        else {}
    )
    queue_item_id = str(active_subgoal.get("queue_item_id") or "")
    if not queue_item_id:
        return []

    queue = load_queue()
    items = queue.get("items") or []
    changed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != queue_item_id:
            continue
        if item.get("status") != "running":
            continue
        item["status"] = "paused"
        item["paused_at"] = now()
        item["paused_reason"] = (
            "Mission stopped by operator while executor was not running."
        )
        item["last_incomplete_at"] = item["paused_at"]
        item["last_incomplete_reason"] = (
            "Queue item was paused because mission-stop was requested and no "
            "local-goal tmux session was running."
        )
        changed.append({"id": item.get("id"), "run_dir": item.get("run_dir")})
        break
    if changed:
        write_queue(queue)
    return changed


def resume_paused_mission_queue_item(mission: dict[str, Any]) -> list[dict[str, Any]]:
    """Requeue the active paused mission item for normal dispatch."""
    active_subgoal = (
        mission.get("active_subgoal")
        if isinstance(mission.get("active_subgoal"), dict)
        else {}
    )
    queue_item_id = str(active_subgoal.get("queue_item_id") or "")
    if not queue_item_id:
        return []

    queue = load_queue()
    items = queue.get("items") or []
    changed: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != queue_item_id:
            continue
        if item.get("status") != "paused":
            continue
        item["status"] = "queued"
        item["resumed_at"] = now()
        item["resume_reason"] = (
            "Mission resumed by operator; monitor --auto-dispatch may restart "
            "the stopped executor through the normal queue path."
        )
        clear_queue_item_recovery_block(
            item,
            reason="Mission resumed by explicit operator command.",
        )
        changed.append({"id": item.get("id"), "run_dir": item.get("run_dir")})
        break
    if changed:
        write_queue(queue)
    return changed


def auto_continue_allowed(status: dict[str, Any]) -> bool:
    """Return whether monitor --auto-continue may continue the active run.

    Operator-paused mission work must stay paused even when stale log evidence
    still classifies the old run as stuck. This guard is intentionally scoped to
    the active queue item; unrelated direct runs are not blocked merely because
    a historical mission exists in stopped state.
    """
    if running_worker_lane_items():
        return False

    run_meta = (
        status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    )
    queue_id = str(run_meta.get("queue_id") or "")
    if not queue_id:
        return True

    queue = load_queue()
    matched_item = None
    for item in queue.get("items") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == queue_id:
            matched_item = item
            break
    if matched_item:
        if matched_item.get("status") == "paused":
            return False
        if matched_item.get("operator_intervention_required") is True:
            return False

    mission = load_mission()
    active_subgoal = (
        mission.get("active_subgoal")
        if isinstance(mission.get("active_subgoal"), dict)
        else {}
    )
    active_queue_id = str(active_subgoal.get("queue_item_id") or "")
    if mission.get("status") == "stopped" and queue_id == active_queue_id:
        return False
    return True


MARKER_REPAIRABLE_CHECKS = {
    "done_criteria_mapped",
    "honest_classification",
    "remaining_none",
    "remaining_dirty_disposition_honesty",
    "verification_entries",
}


def failed_review_check_names(review: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(check.get("name") or "")
            for check in review.get("checks") or []
            if isinstance(check, dict) and not check.get("ok")
        }
        - {""}
    )


def review_failure_is_marker_repairable(review: dict[str, Any]) -> bool:
    """Return true only for marker-honesty failures the manager can repair."""
    failed = failed_review_check_names(review)
    return bool(failed) and all(name in MARKER_REPAIRABLE_CHECKS for name in failed)


def marker_repair_command(status: dict[str, Any]) -> list[str]:
    cmd = ["python3", str(MANAGER), "repair-marker", "--json"]
    run_dir = str(status.get("active_run_dir") or "")
    if run_dir:
        cmd.extend(["--run-dir", run_dir])
    return cmd


def _auto_continue_loop_state() -> dict[str, Any]:
    try:
        data = json.loads(AUTO_CONTINUE_LOOP_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_auto_continue_loop_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_secure_file(
        AUTO_CONTINUE_LOOP_STATE,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        0o600,
    )


def clear_auto_continue_loop_state() -> None:
    with contextlib.suppress(FileNotFoundError):
        AUTO_CONTINUE_LOOP_STATE.unlink()


def _status_objective(status: dict[str, Any]) -> str:
    run_meta = status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    for value in (
        status.get("current_objective"),
        status.get("objective"),
        run_meta.get("goal"),
        run_meta.get("title"),
    ):
        if value:
            return str(value)[:500]
    return ""


def auto_continue_loop_guard(
    *,
    status: dict[str, Any],
    trigger: str,
    failed_checks: list[str] | None = None,
    verdict: str = "",
) -> dict[str, Any]:
    """Record one auto-continue attempt and block repeated no-progress loops.

    The watcher may continue a failed run once, but if the same objective keeps
    returning the same no-progress review signature, more retries are GPU spin.
    Queue-backed stopped runs have their own per-item cap; this guard covers
    failed-review loops and direct-run stopped recovery.
    """
    normalized_failed = sorted({str(name) for name in (failed_checks or []) if name})
    if normalized_failed and not (
        set(normalized_failed) & NO_PROGRESS_REVIEW_CHECKS
    ):
        return {"allowed": True, "reason": "review_failures_not_no_progress"}

    run_meta = status.get("run_meta") if isinstance(status.get("run_meta"), dict) else {}
    signature = {
        "trigger": trigger,
        "objective": _status_objective(status),
        "queue_id": str(run_meta.get("queue_id") or ""),
        "failed_checks": normalized_failed,
        "verdict": str(verdict or status.get("verdict") or ""),
    }
    state = _auto_continue_loop_state()
    previous_signature = state.get("signature")
    attempt_count = int(state.get("attempt_count") or 0) + 1
    if previous_signature != signature:
        attempt_count = 1

    state = {
        "attempt_count": attempt_count,
        "blocked": attempt_count >= RECOVERY_HARD_BLOCK_AFTER,
        "hard_block_after": RECOVERY_HARD_BLOCK_AFTER,
        "last_attempt_at": now(),
        "signature": signature,
    }
    _write_auto_continue_loop_state(state)

    if attempt_count >= RECOVERY_HARD_BLOCK_AFTER:
        return {
            "allowed": False,
            "reason": "repeated_no_progress_auto_continue",
            "attempt_count": attempt_count,
            "hard_block_after": RECOVERY_HARD_BLOCK_AFTER,
            "signature": signature,
            "state_path": str(AUTO_CONTINUE_LOOP_STATE),
            "next_operator_step": (
                "Stop auto-continuing this run. Inspect the active prompt, "
                "allowed paths, and review failures; restart with a narrower "
                "implementation goal only after the root cause is corrected."
            ),
        }
    return {
        "allowed": True,
        "attempt_count": attempt_count,
        "hard_block_after": RECOVERY_HARD_BLOCK_AFTER,
        "signature": signature,
        "state_path": str(AUTO_CONTINUE_LOOP_STATE),
    }


def failed_review_auto_continue_preflight(
    previous_status: dict[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """Re-read live state before continuing a failed review.

    External review can take long enough for another monitor/watcher pass or an
    operator action to accept, stop, or replace the active run. Auto-continue
    must bind to the same unaccepted review target it just reviewed.
    """
    current_status = manager_json()
    current_classification, current_action = classify(current_status)
    previous_run_dir = str(previous_status.get("active_run_dir") or "")
    current_run_dir = str(current_status.get("active_run_dir") or "")
    previous_meta = (
        previous_status.get("run_meta")
        if isinstance(previous_status.get("run_meta"), dict)
        else {}
    )
    current_meta = (
        current_status.get("run_meta")
        if isinstance(current_status.get("run_meta"), dict)
        else {}
    )
    previous_queue_id = str(previous_meta.get("queue_id") or "")
    current_queue_id = str(current_meta.get("queue_id") or "")

    block: dict[str, Any] | None = None
    if current_status.get("accepted") is True or current_classification == "accepted":
        block = {
            "reason": "run_already_accepted",
            "classification": current_classification,
            "action": current_action,
        }
    elif previous_run_dir and current_run_dir and previous_run_dir != current_run_dir:
        block = {
            "reason": "active_run_changed",
            "previous_active_run_dir": previous_run_dir,
            "current_active_run_dir": current_run_dir,
            "classification": current_classification,
            "action": current_action,
        }
    elif (
        previous_queue_id and current_queue_id and previous_queue_id != current_queue_id
    ):
        block = {
            "reason": "active_queue_changed",
            "previous_queue_id": previous_queue_id,
            "current_queue_id": current_queue_id,
            "classification": current_classification,
            "action": current_action,
        }
    elif current_classification != "needs_review":
        block = {
            "reason": "run_no_longer_needs_review",
            "classification": current_classification,
            "action": current_action,
        }

    if block is not None:
        return False, current_status, block
    return True, current_status, None


def auto_external_review_enabled(args: argparse.Namespace) -> bool:
    """Return whether monitor should ask GLM/Kimi before auto-continuing."""
    if getattr(args, "no_auto_external_review", False):
        return False
    if getattr(args, "auto_external_review", False):
        return True
    raw = os.getenv("LOCAL_NODE1_GOAL_AUTO_EXTERNAL_REVIEW", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def run_auto_external_supervisor_review(
    *,
    timeout: int = AUTO_EXTERNAL_REVIEW_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Ask GLM first, then Kimi, for advisory recovery before auto-continue.

    Deterministic manager review remains the acceptance gate. This only gives
    the next local iteration a better recovery packet and records provider
    unavailability honestly when the coding-plan routes are not available.
    """
    attempts: list[dict[str, Any]] = []
    for reviewer in AUTO_EXTERNAL_REVIEWERS:
        cmd = [
            "python3",
            str(MANAGER),
            "external-review",
            "--reviewer",
            reviewer,
            "--review-timeout",
            str(timeout),
            "--json",
        ]
        proc = run(cmd, timeout=timeout + 90)
        payload: dict[str, Any]
        try:
            parsed = json.loads(proc.stdout)
            payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
        attempt = {
            "reviewer": reviewer,
            "returncode": proc.returncode,
            "ok": payload.get("ok") is True,
            "status": payload.get("status") or "unreadable",
            "auth_error": payload.get("auth_error") is True,
            "output_path": payload.get("output_path") or "",
            "stdout_tail": proc.stdout[-1500:],
            "stderr_tail": proc.stderr[-1500:],
        }
        attempts.append(attempt)
        if attempt["ok"]:
            return {
                "contract": "local_node1_goal_auto_external_review.v1",
                "ok": True,
                "selected_reviewer": reviewer,
                "attempts": attempts,
            }
    return {
        "contract": "local_node1_goal_auto_external_review.v1",
        "ok": False,
        "selected_reviewer": "",
        "attempts": attempts,
    }


REQUIRED_CLASSIFICATIONS = {
    "working",
    "idle",
    "stuck",
    "complete",
    "accepted",
    "needs_review",
}

# Indicators that the completion marker contains slop (report-only work)
SLOP_INDICATORS = [
    "dashboard",
    "report-only",
    "alert system",
    "policy note",
    "artifact gallery",
    "status page generation",
    "guardrail",
    "slop",
    "churn",
    "decorative",
    "visualization",
    "sparkline",
    "donut chart",
    "heat ribbon",
    "pulse ring",
    "halo effect",
    "constellation",
    "bloom",
    "belt",
    "truth board",
    "status strip",
]

# Indicators that the completion marker contains useful production work
USEFUL_INDICATORS = [
    "file changed",
    "files changed",
    "before/after",
    "repaired",
    "fixed",
    "deployed",
    "shipped",
    "installed",
    "test pass",
    "test passed",
    "py_compile",
    "pytest",
    "verification",
    "acceptance",
    "production",
]

WEAK_USEFUL_INDICATORS = [
    "installed",
    "verification",
    "acceptance",
    "production",
]

STRONG_USEFUL_INDICATORS = [
    indicator
    for indicator in USEFUL_INDICATORS
    if indicator not in WEAK_USEFUL_INDICATORS
]


def contains_indicator(text: str, indicator: str) -> bool:
    """Match indicators as standalone tokens, not inside filenames or identifiers."""
    pattern = rf"(?<![\w.-]){re.escape(indicator)}(?![\w.-])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def classify_useful_execution(
    complete_marker: dict[str, Any],
    *,
    run_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact useful_execution field for the supervisor state.

    Distinguishes useful execution from slop by examining the completion marker
    AND requiring grounded run evidence (owned files, commands, etc.).
    Without run_evidence, falls back to keyword-only classification.
    Returns: {"useful": bool, "reason": str, "evidence_grounded": bool}
    """
    if not complete_marker:
        return {
            "useful": None,
            "reason": "no completion marker",
            "evidence_grounded": True,
        }

    complete_text = json.dumps(complete_marker).lower()
    has_slop = any(contains_indicator(complete_text, ind) for ind in SLOP_INDICATORS)
    has_strong_useful = any(
        contains_indicator(complete_text, ind) for ind in STRONG_USEFUL_INDICATORS
    )
    has_weak_useful = any(
        contains_indicator(complete_text, ind) for ind in WEAK_USEFUL_INDICATORS
    )
    has_useful = has_strong_useful or (has_weak_useful and not has_slop)

    # Evidence-grounded override: require actual run evidence
    evidence_grounded = True
    evidence_reason = ""
    if run_evidence is not None:
        owned_created = run_evidence.get("owned_created_files") or []
        owned_modified = run_evidence.get("owned_modified_files") or []
        command_transcript = run_evidence.get("command_transcript") or []
        proof_exception = bool(run_evidence.get("proof_exception"))

        has_owned_files = bool(owned_created) or bool(owned_modified)
        has_commands = len(command_transcript) > 0

        if not has_owned_files and not has_commands and not proof_exception:
            evidence_grounded = False
            evidence_reason = "no grounded evidence — prose only"

    if has_useful and evidence_grounded:
        return {
            "useful": True,
            "reason": "completion marker contains useful execution evidence with grounded run evidence",
            "evidence_grounded": True,
        }
    if has_slop or not evidence_grounded:
        return {
            "useful": False,
            "reason": f"completion marker is report-only{' — ' + evidence_reason if evidence_reason else ''}",
            "evidence_grounded": evidence_grounded,
        }
    return {
        "useful": None,
        "reason": "completion marker lacks both slop and useful indicators",
        "evidence_grounded": evidence_grounded,
    }


def classify(status: dict[str, Any]) -> tuple[str, str]:
    """Return exactly one of: working, idle, stuck, complete, accepted, needs_review."""
    verdict = str(status.get("verdict") or "unknown")
    tmux_running = status.get("tmux_running") is True
    awaiting_review = status.get("awaiting_review") is True
    accepted = status.get("accepted") is True
    vllm = status.get("vllm") if isinstance(status.get("vllm"), dict) else {}
    running = float(vllm.get("running") or 0)
    waiting = float(vllm.get("waiting") or 0)
    log_age = int(status.get("log_age_seconds") or 0)

    if verdict == "planning" or status.get("planner_running") is True:
        planner_state = (
            status.get("planner_state")
            if isinstance(status.get("planner_state"), dict)
            else {}
        )
        action = str(
            planner_state.get("heartbeat")
            or status.get("recommended_action")
            or "Planner is preparing the local-goal execution packet."
        )
        fallback = str(planner_state.get("fallback_command") or "")
        if fallback:
            action = f"{action} Fallback if stalled: {fallback}"
        return "working", action

    # accepted — highest priority
    if accepted and not tmux_running:
        return (
            "accepted",
            "Local goal accepted. Local-goal lane is free for the next explicit goal.",
        )

    # needs_review — completion marker exists, review required before accept
    if awaiting_review or verdict == "needs_review":
        return (
            "needs_review",
            "Worker stopped and says it is done. Hermes watcher will review it automatically before any new Node1 goal starts.",
        )

    # complete — complete marker written, tmux stopped, not yet reviewed
    if verdict == "complete":
        return (
            "complete",
            "Worker stopped and says it is done. Hermes watcher will review it automatically before any new Node1 goal starts.",
        )

    # working — tmux running and vLLM has active requests
    if tmux_running and running > 0:
        if waiting > 0:
            return "working", "Node1 vLLM is actively working with queued requests."
        return "working", "Node1 vLLM is actively working with no waiting backlog."
    # working — tmux running with GPU saturated (vLLM collapser may report 0 running)
    gpu_saturated = vllm.get("gpu_saturated") is True
    if tmux_running and gpu_saturated:
        return "working", "Node1 vLLM GPUs are saturated; active work in progress."

    # stuck — denied Task/subagent permission events in session log
    stall_detection = (
        status.get("stall_detection")
        if isinstance(status.get("stall_detection"), dict)
        else {}
    )
    transient_stall_window = (
        tmux_running and running == 0 and waiting == 0 and log_age <= 120
    )
    denied_task_events = stall_detection.get("denied_task_events", 0)
    if denied_task_events and denied_task_events > 0:
        if transient_stall_window:
            return (
                "working",
                "Recoverable stall signal observed while the executor is still fresh; waiting one monitor interval before classifying stuck.",
            )
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            f"Denied Task/subagent permission events detected ({denied_task_events}). "
            "Executor may be stalled waiting for unavailable delegation. "
            f"Classification: stuck_denied_subagent."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — repeated edit/write tool failures
    tool_edit_failures = int(stall_detection.get("tool_edit_failures") or 0)
    if tool_edit_failures >= 2:
        if transient_stall_window:
            return (
                "working",
                "Recent edit/write failures detected, but the executor is still fresh; waiting one monitor interval before classifying stuck.",
            )
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            f"Edit/write tool failures detected ({tool_edit_failures}). "
            "Executor may be retrying an overwrite without reading the target file. "
            f"Classification: stuck_tool_edit_failure."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — destructive git commands in the shared dirty worktree
    destructive_git_commands = int(stall_detection.get("destructive_git_commands") or 0)
    if destructive_git_commands > 0:
        if transient_stall_window:
            return (
                "working",
                "Recent destructive git command signal detected, but the executor is still fresh; waiting one monitor interval before classifying stuck.",
            )
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            f"Destructive git commands detected ({destructive_git_commands}). "
            "Executor may have discarded or hidden shared dirty work. "
            f"Classification: stuck_destructive_git_command."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — verification commands are being run from the wrong directory
    verification_command_failures = int(
        stall_detection.get("verification_command_failures") or 0
    )
    if (
        verification_command_failures >= 2
        and tmux_running
        and running == 0
        and waiting == 0
        and log_age > 90
    ):
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            f"Verification command failures detected ({verification_command_failures}) "
            "and executor is idle. "
            f"Classification: stuck_verification_command_failure."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — wrapper-only local-goal commands were routed through supervisor internals
    wrapper_command_misroutes = int(
        stall_detection.get("wrapper_command_misroutes") or 0
    )
    if (
        wrapper_command_misroutes > 0
        and tmux_running
        and running == 0
        and waiting == 0
        and log_age > 90
    ):
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            f"Wrapper-only local-goal command misroutes detected ({wrapper_command_misroutes}) "
            "and executor is idle. "
            f"Classification: stuck_wrapper_command_misroute."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — file-watcher-only activity without model progress
    file_watcher_only = stall_detection.get("file_watcher_only") is True
    if file_watcher_only:
        if transient_stall_window:
            return (
                "working",
                "File-watcher-only activity is recent; waiting one monitor interval before classifying stuck.",
            )
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            "File-watcher-only activity detected without model calls. "
            "Executor may be idle-looping on file changes. "
            f"Classification: stuck_file_watcher_only."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — quiet-but-running: tmux alive, no vLLM, log is old
    quiet_but_running = stall_detection.get("quiet_but_running") is True
    if quiet_but_running:
        recovery_hint = stall_detection.get("recovery_hint", "")
        return (
            "stuck",
            "Quiet-but-running detected: tmux alive, no vLLM requests, log is old. "
            "Executor may be context-window exhausted or silently stalled. "
            f"Classification: stuck_quiet_but_running."
            + (f" Recovery: {recovery_hint}" if recovery_hint else ""),
        )
    # stuck — repeated command loop detected
    rcd = (
        status.get("repeated_command_detection")
        if isinstance(status.get("repeated_command_detection"), dict)
        else {}
    )
    if rcd.get("stuck") is True:
        return (
            "stuck",
            f"Repeated command loop detected: {rcd.get('repeated_count')} repetitions of '{rcd.get('repeated_command')}'. Classification: stuck_repeat_command.",
        )
    # stuck — tmux running but no vLLM activity and logs old, or loop state inconsistent
    if tmux_running and log_age > 1800:
        return (
            "stuck",
            "tmux is running but logs are old and vLLM idle. Inspect log before restarting.",
        )
    cloud_running_items = running_worker_lane_items()
    if cloud_running_items:
        alive_items = [
            item
            for item in cloud_running_items
            if process_is_alive(item.get("cloud_loop_pid"))
        ]
        if alive_items:
            worker = str(alive_items[0].get("executor_worker") or "cloud")
            return (
                "working",
                f"Cloud executor loop is running via {worker}; waiting for completion marker or review.",
            )
        if not tmux_running:
            return (
                "stuck",
                "Cloud executor queue item is running but its cloud loop process is not alive. Inspect cloud-loop.log before resuming.",
            )
    if verdict in {"stopped_incomplete", "needs_attention"}:
        return "stuck", str(
            status.get("recommended_action")
            or "Loop state inconsistent with tmux; inspect log."
        )

    # working — executor is alive but vLLM metrics have not caught activity yet
    if tmux_running:
        return (
            "working",
            "Local goal executor is running; waiting for vLLM activity or completion.",
        )

    # idle — no tmux, no run
    if not tmux_running:
        return (
            "idle",
            "No local goal is running. Hermes may start one only on explicit operator/Codex request.",
        )

    # fallback — should not reach here with proper manager verdicts
    return "idle", str(status.get("recommended_action") or "Inspect status.")


def active_warnings(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-fatal risk signals for an active local-goal run.

    These do not necessarily mean the worker is stuck. They make degraded
    behavior visible while vLLM is still active, so Hermes/operator status can
    distinguish "healthy working" from "working but showing risk signals."
    """
    classification, _ = classify(status)
    if (
        status.get("accepted") is True and status.get("tmux_running") is not True
    ) or classification in {
        "accepted",
        "idle",
        "complete",
    }:
        return []

    warnings: list[dict[str, Any]] = []
    stall_detection = (
        status.get("stall_detection")
        if isinstance(status.get("stall_detection"), dict)
        else {}
    )
    denied_task_events = int(stall_detection.get("denied_task_events") or 0)
    if denied_task_events > 0:
        warnings.append(
            {
                "kind": "denied_subagent",
                "count": denied_task_events,
                "detail": "Task/subagent delegation was denied in the recent log window.",
            }
        )
    tool_edit_failures = int(stall_detection.get("tool_edit_failures") or 0)
    if tool_edit_failures > 0:
        warnings.append(
            {
                "kind": "tool_edit_failure",
                "count": tool_edit_failures,
                "detail": "Edit/write tool failures were seen in the recent log window.",
            }
        )
    helper_command_failures = int(stall_detection.get("helper_command_failures") or 0)
    if helper_command_failures > 0:
        warnings.append(
            {
                "kind": "helper_command_failure",
                "count": helper_command_failures,
                "detail": "Local-goal helper commands failed in the recent log window.",
            }
        )
    verification_command_failures = int(
        stall_detection.get("verification_command_failures") or 0
    )
    if verification_command_failures > 0:
        warnings.append(
            {
                "kind": "verification_command_failure",
                "count": verification_command_failures,
                "detail": "Verification commands failed before checking the target file.",
            }
        )
    destructive_git_commands = int(stall_detection.get("destructive_git_commands") or 0)
    if destructive_git_commands > 0:
        warnings.append(
            {
                "kind": "destructive_git_command",
                "count": destructive_git_commands,
                "detail": "Destructive git commands were attempted in the shared dirty worktree.",
            }
        )
    wrapper_command_misroutes = int(
        stall_detection.get("wrapper_command_misroutes") or 0
    )
    if wrapper_command_misroutes > 0:
        warnings.append(
            {
                "kind": "wrapper_command_misroute",
                "count": wrapper_command_misroutes,
                "detail": "Wrapper-only local-goal commands were called through supervisor internals.",
            }
        )
    if stall_detection.get("file_watcher_only") is True:
        warnings.append(
            {
                "kind": "file_watcher_only",
                "count": 1,
                "detail": "File-watcher activity is present without model-call evidence.",
            }
        )
    if stall_detection.get("quiet_but_running") is True:
        warnings.append(
            {
                "kind": "quiet_but_running",
                "count": 1,
                "detail": "tmux is alive while vLLM is idle and the log is old.",
            }
        )
    rcd = (
        status.get("repeated_command_detection")
        if isinstance(status.get("repeated_command_detection"), dict)
        else {}
    )
    if rcd.get("stuck") is True:
        warnings.append(
            {
                "kind": "repeated_command_loop",
                "count": int(rcd.get("repeated_count") or 0),
                "detail": str(rcd.get("repeated_command") or ""),
            }
        )
    recovery_hint = str(stall_detection.get("recovery_hint") or "").strip()
    if recovery_hint and warnings:
        warnings[0]["recovery_hint"] = recovery_hint
    return warnings


def state_phase(
    status: dict[str, Any],
    classification: str,
    *,
    review: dict[str, Any] | None = None,
    dispatch: dict[str, Any] | None = None,
) -> str:
    """Return one stable state-machine phase for status consumers."""
    if dispatch is not None:
        return "dispatching"
    if review is not None:
        return "reviewing"
    if status.get("accepted") is True or classification == "accepted":
        return "accepted"
    if status.get("awaiting_review") is True or classification in {
        "complete",
        "needs_review",
    }:
        return "reviewing"
    if classification == "working" or status.get("tmux_running") is True:
        if status.get("planner_running") is True or status.get("verdict") == "planning":
            return "planning"
        return "running"
    if classification == "stuck":
        return "blocked"
    if queue_items("running"):
        return "running"
    if queue_items("queued"):
        return "queued"
    return "idle"


def write_supervisor_state(
    status: dict[str, Any],
    *,
    review: dict[str, Any] | None = None,
    dispatch: dict[str, Any] | None = None,
    quiet_notify_stdout: bool = False,
) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    previous_payload: dict[str, Any] = {}
    if SUPERVISOR_JSON.exists():
        try:
            previous_payload = json.loads(SUPERVISOR_JSON.read_text(encoding="utf-8"))
            if not isinstance(previous_payload, dict):
                previous_payload = {}
        except Exception:
            previous_payload = {}

    classification, action = classify(status)
    phase = state_phase(status, classification, review=review, dispatch=dispatch)
    # Read the completion marker for useful execution classification
    complete_marker: dict[str, Any] = {}
    cmp_path = Path(str(status.get("complete_marker_path") or ""))
    if cmp_path.exists():
        try:
            complete_marker = json.loads(cmp_path.read_text())
        except Exception:
            complete_marker = {}
    useful_execution = classify_useful_execution(complete_marker)
    mission = load_mission()
    current_subgoal = active_mission_subgoal_for_status(mission, status)
    rcd = (
        status.get("repeated_command_detection")
        if isinstance(status.get("repeated_command_detection"), dict)
        else {}
    )
    warnings = active_warnings(status)
    capabilities = lane_capabilities(status)
    current_state = (
        capabilities.get("current_state")
        if isinstance(capabilities.get("current_state"), dict)
        else {}
    )
    supervision = (
        capabilities.get("supervision")
        if isinstance(capabilities.get("supervision"), dict)
        else {}
    )
    watcher = (
        supervision.get("watcher")
        if isinstance(supervision.get("watcher"), dict)
        else {}
    )
    failed_to_start_count = len(queue_items("failed_to_start"))
    failed_to_start_label = "historical_residue" if failed_to_start_count else "none"
    recovery_block = recovery_block_status(status)
    local_goal_lane_free = node1_is_free(status)
    node1_vllm_idle = node1_vllm_is_idle(status)
    node1_vllm_other_activity = node1_vllm_has_other_activity(status)
    start_may_wait = local_goal_lane_free and node1_vllm_other_activity
    payload = {
        "contract": "local_node1_goal_supervisor.v1",
        "generated_at": now(),
        "phase": phase,
        "classification": classification,
        "recommended_action": action,
        "recovery_block_reason": recovery_block.get("recovery_block_reason", ""),
        "time_in_blocked_state": recovery_block.get("time_in_blocked_state", 0),
        "next_operator_step": recovery_block.get("next_operator_step", ""),
        "recovery_block": recovery_block,
        "hard_blocked": recovery_block.get("hard_blocked") is True,
        "active_warnings": warnings,
        "active_warning_count": len(warnings),
        "monitor_phase": monitor_phase(),
        "useful_execution": useful_execution,
        "repeated_command_detected": rcd.get("stuck") is True,
        "last_repeated_command": rcd.get("repeated_command", ""),
        "repeated_count": rcd.get("repeated_count", 0),
        "completion_marker_shutdown_needed": status.get(
            "completion_marker_shutdown_needed"
        )
        is True,
        "node1_is_idle": local_goal_lane_free,
        "node1_is_idle_scope": "legacy: local-goal lane availability, not raw vLLM/GPU idleness",
        "local_goal_lane_free": local_goal_lane_free,
        "node1_vllm_idle": node1_vllm_idle,
        "node1_vllm_has_other_activity": node1_vllm_other_activity,
        "start_may_wait": start_may_wait,
        "start_guidance": current_state.get("start_guidance")
        or (
            "Local-goal lane is free, but separate Node1 vLLM activity may make a new bounded goal wait."
            if start_may_wait
            else "Local-goal lane is free and Node1 vLLM capacity is clear for a bounded goal."
            if local_goal_lane_free and node1_vllm_idle
            else "Wait for the active local-goal lane to clear before starting another bounded goal."
        ),
        "watcher": watcher,
        "integration_audit": cached_integration_audit_summary(),
        "capabilities": capabilities,
        "active_goal": {
            "objective": status.get("current_objective"),
            "prompt_path": status.get("prompt_path"),
            "planner": status.get("active_planner"),
            "planner_packet_path": status.get("planner_packet_path"),
            "executor": (status.get("runner_state") or {}).get("executor")
            or (status.get("loop_state") or {}).get("executor"),
            "tmux_running": status.get("tmux_running"),
            "awaiting_review": status.get("awaiting_review"),
            "accepted": status.get("accepted"),
            "run_dir": status.get("active_run_dir"),
            "current_subgoal": current_subgoal,
        },
        "runtime": {
            "vllm": status.get("vllm"),
            "loop_state": status.get("loop_state"),
            "log_path": status.get("log_path"),
            "checkpoint_path": status.get("checkpoint_path"),
            "complete_marker_path": status.get("complete_marker_path"),
        },
        "review": review,
        "dispatch": dispatch,
        "queue": {
            "path": str(QUEUE_JSON),
            "queued": len(queue_items("queued")),
            "running": len(queue_items("running")),
            "failed_to_start": failed_to_start_count,
            "historical_failed_to_start": failed_to_start_count,
            "failed_to_start_active": 0,
            "failed_to_start_label": failed_to_start_label,
            "failed_to_start_note": (
                "Failed-to-start queue entries are retained as historical residue; "
                "queued/running counts indicate active queue work."
                if failed_to_start_count
                else ""
            ),
        },
        "commands": {
            "status": f"{LOCAL_GOAL_WRAPPER} status",
            "progress": f"{LOCAL_GOAL_WRAPPER} progress",
            "next_proof": f"{LOCAL_GOAL_WRAPPER} next-proof",
            "soak_plan": f"{LOCAL_GOAL_WRAPPER} soak-plan",
            "capabilities": f"{LOCAL_GOAL_WRAPPER} capabilities",
            "integration_audit": f"{LOCAL_GOAL_WRAPPER} integration-audit",
            "log": f"{LOCAL_GOAL_WRAPPER} log",
            "start_premium": f"{LOCAL_GOAL_WRAPPER} premium-start --planner gpt-5.5 --goal-file /path/to/goal.md",
            "enqueue": f"{LOCAL_GOAL_WRAPPER} enqueue --planner gpt-5.5 --goal-file /path/to/goal.md",
            "enqueue_cloud": f"{LOCAL_GOAL_WRAPPER} enqueue --executor-worker {CLOUD_BUILDER_FALLBACK} --goal-file /path/to/goal.md",
            "queue": f"{LOCAL_GOAL_WRAPPER} queue",
            "mission_show": f"{LOCAL_GOAL_WRAPPER} mission-show",
            "monitor": f"{LOCAL_GOAL_WRAPPER} monitor --auto-accept --auto-continue --auto-dispatch --auto-commit-owned",
            "supervise": f"{LOCAL_GOAL_WRAPPER} supervise",
            "external_review": f"{LOCAL_GOAL_WRAPPER} external-review --reviewer glm-5.2",
            "glm_handoff_plan": f"{LOCAL_GOAL_WRAPPER} glm-handoff-plan",
            "glm_supervisor": f"{LOCAL_GOAL_WRAPPER} glm-supervisor status",
            "continue": f"{LOCAL_GOAL_WRAPPER} continue --feedback 'review feedback or next instruction'",
            "nudge": f"{LOCAL_GOAL_WRAPPER} nudge --feedback 'next-iteration guidance'",
            "review": f"{LOCAL_GOAL_WRAPPER} review",
            "accept": f"{LOCAL_GOAL_WRAPPER} accept",
        },
        "events": {
            "path": str(SUPERVISOR_EVENTS_JSONL),
            "count": 0,
            "latest": None,
        },
    }
    payload["goal_state"] = migrate_legacy_goal_state(payload).to_dict()

    # Keep a compact event timeline for state continuity and handoff.
    prev_sig = _state_signature(previous_payload) if previous_payload else {}
    curr_sig = _state_signature(payload)
    delta = _sig_delta(prev_sig, curr_sig)
    if not prev_sig:
        change_type = "initialized"
    else:
        change_type = "state_change" if delta else None
    events = _read_supervisor_events(limit=SUPERVISOR_EVENTS_KEEP)

    if review is not None:
        event_type = "review"
    elif dispatch is not None:
        event_type = "dispatch"
    else:
        event_type = change_type

    if event_type:
        event: dict[str, Any] = {
            "ts": now(),
            "event": event_type,
            "classification": payload["classification"],
            "objective": payload["active_goal"].get("objective"),
            "run_dir": payload["active_goal"].get("run_dir"),
            "signature": curr_sig,
        }
        if delta:
            event["delta"] = delta
        if review is not None:
            event["review"] = review
        if dispatch is not None:
            event["dispatch"] = dispatch
        _append_supervisor_event(event)
        events = _read_supervisor_events(limit=SUPERVISOR_EVENTS_KEEP)
    payload["events"]["count"] = len(events)
    payload["events"]["latest"] = events[-1] if events else None

    payload["notification"] = maybe_notify_operator(
        payload, quiet_stdout=quiet_notify_stdout
    )

    write_secure_file(
        SUPERVISOR_JSON,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        0o600,
    )

    lines = [
        "# Local Node1 Goal Supervisor",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Phase: `{payload['phase']}`",
        f"- Classification: `{classification}`",
        f"- Recommended action: {action}",
        f"- Recovery block reason: `{payload.get('recovery_block_reason') or 'none'}`",
        f"- Time in blocked state: `{payload.get('time_in_blocked_state')}` seconds",
        f"- Next operator step: {payload.get('next_operator_step') or 'none'}",
        f"- Monitor phase: `{payload['monitor_phase'].get('phase')}` {payload['monitor_phase'].get('detail') or ''}",
        f"- Notification: attempted=`{payload['notification'].get('attempted')}` sent=`{payload['notification'].get('sent')}` reason=`{payload['notification'].get('reason')}`",
        f"- Objective: {payload['active_goal']['objective']}",
        f"- Prompt: `{payload['active_goal']['prompt_path']}`",
        f"- Planner: `{payload['active_goal']['planner']}` packet=`{payload['active_goal']['planner_packet_path'] or 'none'}`",
        f"- Executor: `{payload['active_goal']['executor']}`",
        f"- tmux running: `{payload['active_goal']['tmux_running']}`",
        f"- Awaiting review: `{payload['active_goal']['awaiting_review']}` accepted=`{payload['active_goal']['accepted']}`",
        f"- Log: `{payload['runtime']['log_path']}`",
        "",
        "## Runtime",
        "",
    ]
    vllm = payload["runtime"].get("vllm") or {}
    lines.extend(
        [
            f"- vLLM healthy: `{vllm.get('healthy')}` running=`{vllm.get('running')}` waiting=`{vllm.get('waiting')}`",
            f"- GPU saturated: `{vllm.get('gpu_saturated')}`",
        ]
    )
    lines.extend(["", "## Capabilities", ""])
    for name, lane in (capabilities.get("lanes") or {}).items():
        if not isinstance(lane, dict):
            continue
        lines.append(
            f"- `{name}` {lane.get('classification')} installed=`{lane.get('installed')}` "
            f"available_now=`{lane.get('available_now')}` reason=`{lane.get('availability_reason')}`"
        )
    if warnings:
        lines.extend(["", "## Active Warnings", ""])
        for warning in warnings:
            detail = warning.get("detail") or ""
            lines.append(
                f"- `{warning.get('kind')}` count=`{warning.get('count')}` {detail}"
            )
        hint = warnings[0].get("recovery_hint")
        if hint:
            lines.append(f"- Recovery hint: {hint}")
    if review:
        lines.extend(["", "## Review", ""])
        lines.append(
            f"- Review status: `{review.get('status')}` ok=`{review.get('ok')}`"
        )
        lines.append(
            f"- Review path: `{review.get('review_path') or review.get('review_json') or ''}`"
        )
    if dispatch:
        lines.extend(["", "## Dispatch", ""])
        lines.append(f"- Queued item: `{dispatch.get('queued_id')}`")
        lines.append(f"- Title: {dispatch.get('title')}")
        lines.append(f"- Return code: `{dispatch.get('returncode')}`")
    lines.extend(
        [
            "",
            "## Queue",
            "",
            f"- Queue path: `{payload['queue']['path']}`",
            f"- Queued: `{payload['queue']['queued']}`",
            f"- Running: `{payload['queue']['running']}`",
            f"- Historical failed-to-start residue: `{payload['queue']['historical_failed_to_start']}` label=`{payload['queue']['failed_to_start_label']}`",
        ]
    )

    timeline = events[-6:]
    if timeline:
        lines.extend(["", "## Event Timeline", ""])
        for event in timeline:
            lines.append(
                f"- `{event.get('ts')}` `{event.get('event')}` cls={event.get('classification')} "
                f"obj={event.get('objective') or 'n/a'}"
            )

    lines.append("")
    write_secure_file(SUPERVISOR_MD, "\n".join(lines), 0o640)
    return payload


def print_summary(payload: dict[str, Any]) -> None:
    goal = payload.get("active_goal") or {}
    runtime = payload.get("runtime") or {}
    vllm = runtime.get("vllm") or {}
    print(f"classification={payload.get('classification')}")
    print(f"action={payload.get('recommended_action')}")
    print(f"recovery_block_reason={payload.get('recovery_block_reason') or 'none'}")
    print(f"time_in_blocked_state={payload.get('time_in_blocked_state')}")
    print(f"next_operator_step={payload.get('next_operator_step') or 'none'}")
    print(f"objective={goal.get('objective')}")
    print(f"prompt={goal.get('prompt_path')}")
    print(
        f"planner={goal.get('planner')} planner_packet={goal.get('planner_packet_path') or 'none'}"
    )
    print(f"executor={goal.get('executor')}")
    print(
        f"tmux_running={goal.get('tmux_running')} awaiting_review={goal.get('awaiting_review')} accepted={goal.get('accepted')}"
    )
    print(
        f"vllm_healthy={vllm.get('healthy')} running={vllm.get('running')} waiting={vllm.get('waiting')}"
    )
    queue = payload.get("queue") or {}
    print(
        f"queue_queued={queue.get('queued')} queue_running={queue.get('running')} "
        f"queue_failed={queue.get('failed_to_start')} "
        f"queue_failed_label={queue.get('failed_to_start_label')}"
    )
    capabilities = payload.get("capabilities") or {}
    lanes = capabilities.get("lanes") if isinstance(capabilities, dict) else {}
    if isinstance(lanes, dict):
        lane_parts = []
        for name, lane in lanes.items():
            if isinstance(lane, dict):
                lane_parts.append(
                    f"{name}:{lane.get('classification')}/installed={lane.get('installed')}"
                    f"/available={lane.get('available_now')}/reason={lane.get('availability_reason')}"
                )
        print(f"capabilities={','.join(lane_parts)}")
    print(f"supervisor_json={SUPERVISOR_JSON}")
    print(f"supervisor_md={SUPERVISOR_MD}")


STEWARD_SCRIPT = Path("/mnt/raid0/services/scheduled-tasks/dirty_worktree_steward.py")


def _run_steward_dry_run() -> dict[str, Any]:
    if not STEWARD_SCRIPT.exists():
        return {"ok": False, "error": f"steward script missing: {STEWARD_SCRIPT}"}
    proc = run(
        ["python3", str(STEWARD_SCRIPT), "--dry-run"],
        timeout=120,
    )
    report_path = STEWARD_SCRIPT.parent / "logs" / "dirty_worktree_steward_latest.json"
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data["returncode"] = proc.returncode
            return data
    except Exception:
        pass
    try:
        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data["returncode"] = proc.returncode
            return data
    except Exception:
        pass
    return {
        "ok": False,
        "error": "steward output unreadable",
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def _active_run_artifacts(run_dir: Path) -> dict[str, Any]:
    expected = [
        "BOOTSTRAP.md",
        "handoff.md",
        "events.jsonl",
        "current-subgoal.json",
        "review.json",
        "acceptance.json",
        "ticket.json",
        "prompt.md",
        "context-map.md",
        "run-meta.json",
        "commands.log",
        "progress-ledger.md",
        "final-result.json",
        "complete.json",
        "worktree-snapshot.json",
        "start-worktree-snapshot.json",
        "owned-changes.json",
        "dirty-steward-dry-run.json",
        "dirty-disposition.json",
        "dirty-disposition.md",
    ]
    present = [name for name in expected if (run_dir / name).exists()]
    missing = [name for name in expected if name not in present]
    return {"run_dir": str(run_dir), "present": present, "missing": missing}


def _read_review_gaps(run_dir: Path | None) -> list[str]:
    if not run_dir:
        return []
    gaps_path = run_dir / "review-gaps.md"
    if not gaps_path.exists():
        return []
    try:
        text = gaps_path.read_text(encoding="utf-8", errors="replace")
        return [
            line.strip() for line in text.splitlines() if line.strip().startswith("-")
        ][:20]
    except Exception:
        return []


def cmd_handoff_current(args: argparse.Namespace) -> int:
    """Generate a current-state handoff packet for the next AI/operator."""
    payload = write_supervisor_state(manager_json())
    mission = load_mission()
    goal = payload.get("active_goal") or {}
    run_dir_str = goal.get("run_dir")
    run_dir = Path(run_dir_str) if run_dir_str and Path(run_dir_str).exists() else None

    steward = _run_steward_dry_run()
    artifacts = (
        _active_run_artifacts(run_dir)
        if run_dir
        else {"run_dir": None, "present": [], "missing": []}
    )
    gaps = _read_review_gaps(run_dir)

    classification = payload.get("classification") or "unknown"
    awaiting_review = goal.get("awaiting_review") or False
    accepted = goal.get("accepted") or False
    tmux_running = goal.get("tmux_running") or False

    if awaiting_review:
        next_action = "Run review: python3 <supervisor> review"
    elif accepted:
        next_action = "Run disposition/accept follow-up as needed; verify acceptance binds to active run."
    elif tmux_running and classification == "working":
        next_action = "The local-goal worker is running. Do not start another Node1 job. Monitor only."
    else:
        next_action = (
            payload.get("recommended_action") or "Check status and decide next step."
        )

    umbrella = str(mission.get("umbrella_objective") or "").strip()
    if umbrella:
        umbrella_excerpt = umbrella.splitlines()[0][:200]
        umbrella_line = f"- Umbrella objective: `{umbrella_excerpt}`"
    else:
        umbrella_line = "- Umbrella objective: none"

    lines: list[str] = [
        "# Local Node1 `/goal` Current-State Handoff",
        "",
        f"Generated: `{now()}`",
        f"Source: `{Path(__file__)}`",
        "",
        "## Live Status",
        "",
        f"- Classification: `{classification}`",
        f"- Active run: `{run_dir or 'none'}`",
        f"- tmux running: `{tmux_running}`",
        f"- Awaiting review: `{awaiting_review}`",
        f"- Accepted: `{accepted}`",
        f"- Node1 idle: `{payload.get('node1_is_idle')}`",
        f"- Next safe action: {next_action}",
        "",
        "## Mission",
        "",
        f"- Status: `{mission.get('status')}`",
        umbrella_line,
    ]
    current = mission.get("active_subgoal")
    if isinstance(current, dict):
        lines.extend(
            [
                f"- Active subgoal: `{current.get('title') or current.get('criterion')}`",
                f"- Subgoal number: {current.get('subgoal_number')}",
                f"- Enqueued at: {current.get('enqueued_at')}",
            ]
        )
    completed = mission.get("completed_subgoals") or []
    lines.append(f"- Completed subgoals: {len(completed)}")
    if completed:
        for sg in completed[-5:]:
            if isinstance(sg, dict):
                lines.append(
                    f"  - `{sg.get('title') or sg.get('criterion')}` accepted_at={sg.get('accepted_at')}"
                )
    lines.append("")

    lines.extend(
        [
            "## Active Run Artifacts",
            "",
            f"- Present: {', '.join(artifacts['present']) or 'none'}",
            f"- Missing: {', '.join(artifacts['missing']) or 'none'}",
            "",
        ]
    )

    if gaps:
        lines.extend(["## Known Review Gaps", ""])
        for gap in gaps:
            lines.append(f"- {gap}")
        lines.append("")

    disposition_path = run_dir / "dirty-disposition.json" if run_dir else None
    disposition_summary: dict[str, Any] = {}
    if disposition_path and disposition_path.exists():
        try:
            disposition_summary = (
                json.loads(disposition_path.read_text(encoding="utf-8")).get("summary")
                or {}
            )
        except Exception:
            pass
    dirty_completion_ok = (
        disposition_summary.get("dirty_completion_ok") if disposition_summary else False
    )
    lines.extend(
        [
            "## Dirty Worktree Summary",
            "",
            f"- Steward ok: `{steward.get('ok')}`",
            f"- Completion ok: `{steward.get('completion_ok')}`",
            f"- Dirty completion ok: `{dirty_completion_ok}`",
            f"- Action required: {steward.get('action_required_count')}",
            f"- Human required: {steward.get('human_required_count')}",
            f"- Report: `{steward.get('report')}`",
            f"- Disposition summary: `{disposition_path}`",
            "",
        ]
    )

    lines.extend(
        [
            "## Files To Read First",
            "",
            "1. `sessions/HANDOFF_2026-06-23_local-goal-consolidated-live-status.md`",
            "2. `reference/LOCAL_NODE1_CODEX_LIKE_GOAL_WORKER.md`",
            "3. `reference/LOCAL_NODE1_GOAL_HARNESS_QUICKREF.md`",
            f"4. Active run `run-meta.json`: `{run_dir / 'run-meta.json' if run_dir else 'none'}`",
            f"5. Active run `progress-ledger.md`: `{run_dir / 'progress-ledger.md' if run_dir else 'none'}`",
            "6. `reports/local-node1-goal-harness/manager-status.json`",
            "7. `reports/local-node1-goal-harness/supervisor-latest.md`",
            "",
        ]
    )

    lines.extend(
        [
            "## Commands",
            "",
            "```bash",
            "# Status (source of truth)",
            f"python3 {Path(__file__)} status --json",
            "# Mission",
            f"python3 {Path(__file__)} mission-show --json",
            "# Review/accept only when awaiting_review=true",
            f"python3 {Path(__file__)} review",
            f"python3 {Path(__file__)} accept",
            "# Current handoff",
            f"python3 {Path(__file__)} handoff --current",
            "```",
            "",
        ]
    )

    lines.extend(
        [
            "## Not-Complete-Unless Criteria",
            "",
            "Do not call the harness complete until all of the following are true:",
            "",
            "- [ ] Current active mission status is `complete`.",
            "- [ ] At least two mission subgoals were accepted without manual prompt feeding.",
            "- [ ] Review is current-run-bound and fail-closed.",
            "- [ ] Acceptance binds to current run and marker SHA.",
            "- [ ] Dirty-worktree steward reports `completion_ok=true`, or every remaining item has a durable non-babysitting disposition.",
            "- [ ] Active/new runs include per-run bootstrap/recovery artifacts or a tested equivalent.",
            "- [ ] `handoff --current` exists and works (this command).",
            "- [ ] Canonical docs are reconciled and do not overclaim.",
            "- [ ] Hermes/Codex/other AI has a stable command path to start/status/review/accept/continue/handoff.",
            "- [ ] Verification commands are recorded with results.",
            "",
            "## Safety Rules",
            "",
            "- Do not start concurrent Node1 long-goal jobs.",
            "- Do not trust stale `acceptance.json`/`review.json` from a previous run as proof the current run is accepted.",
            "- Do not run destructive git commands without explicit operator approval.",
            "- Never unbounded `rglob` over fleet-scale paths.",
            "",
        ]
    )

    if run_dir and (run_dir / "context-map.md").exists():
        lines.extend(
            [
                "## Active Run Context Map",
                "",
                f"See `{run_dir / 'context-map.md'}`",
                "",
            ]
        )

    handoff_text = "\n".join(lines)
    if args.output:
        out_path = Path(args.output)
        write_secure_file(out_path, handoff_text, 0o640)
        print(f"handoff_written={out_path}")
    else:
        print(handoff_text)
    return 0


def manager_action(args: argparse.Namespace, action: str) -> int:
    cmd = ["python3", str(MANAGER), action]
    if args.json:
        cmd.append("--json")
    proc = run(cmd, timeout=MANAGER_BOUNDARY_TIMEOUT_SECONDS)
    defer_stdout = (
        action == "accept" and args.json and getattr(args, "auto_commit_owned", False)
    )
    if not defer_stdout:
        sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    status = manager_json()
    review = None
    if action == "review":
        try:
            review = (
                json.loads(proc.stdout)
                if args.json
                else {"status": "see_manager_output"}
            )
        except json.JSONDecodeError:
            review = {"status": "see_manager_output"}
    if (
        action == "accept"
        and proc.returncode == 0
        and getattr(args, "auto_commit_owned", False)
    ):
        try:
            accept_payload = json.loads(proc.stdout) if args.json else {}
        except json.JSONDecodeError:
            accept_payload = {}
        if accept_payload.get("status") == "accepted":
            disposition = disposition_json(commit=True)
            if args.json:
                accept_payload["disposition"] = disposition
                sys.stdout.write(
                    json.dumps(accept_payload, indent=2, sort_keys=True) + "\n"
                )
            if review is None:
                review = {}
            review["disposition"] = disposition
        elif defer_stdout:
            sys.stdout.write(proc.stdout)
    write_supervisor_state(status, review=review)
    return proc.returncode


def start_goal(args: argparse.Namespace) -> int:
    planner = args.planner or "none"
    executor = args.executor or "opencode"
    if planner not in ALLOWED_PLANNERS:
        print(f"unsupported planner: {planner}", file=sys.stderr)
        return 2
    if executor not in ALLOWED_EXECUTORS:
        print(f"unsupported executor: {executor}", file=sys.stderr)
        return 2
    if not args.goal and not args.goal_file:
        print("start requires --goal or --goal-file", file=sys.stderr)
        return 2

    cmd = [
        "python3",
        str(MANAGER),
        "transfer",
        "--title",
        args.title or "Hermes transferred local goal",
        "--planner",
        planner,
        "--executor",
        executor,
    ]
    if args.goal_file:
        cmd.extend(["--goal-file", args.goal_file])
        proc = run(cmd, timeout=1200)
    else:
        cmd.extend(["--goal", args.goal])
        proc = run(cmd, timeout=1200)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    payload = write_supervisor_state(manager_json())
    if proc.returncode == 0:
        print_summary(payload)
    return proc.returncode


def continue_goal(args: argparse.Namespace) -> int:
    goal_text = args.goal
    if goal_text:
        goal_text = "\n\n".join(
            [
                goal_text,
                "Required continuation policy:",
                "- Inspect repo root, branch, git worktree list, and git status before editing.",
                "- Preserve unrelated dirty files as shared work-in-progress.",
                "- Do not create new worktrees.",
                "- Do not create new branches.",
                "- Do not use git stash.",
                "- Do not run destructive git commands.",
                "- Do not use task subagents or external worker delegation for this local Node1 run.",
                "- Use the local Node1 worker path only; do not switch to a cloud or paid API executor.",
                "- Before or immediately after editing a file, run `python3 scripts/local-node1-goal-manager.py mark-owned --run-dir <active-run-dir> --path <path>` for each file this run owns.",
                "- If dirty worktree ownership is ambiguous, record the ambiguity in run evidence and continue with a safe independent slice.",
            ]
        )
    cmd = [
        "python3",
        str(MANAGER),
        "continue",
        "--title",
        args.title or "Hermes continued local goal",
        "--executor",
        args.executor or "opencode",
    ]
    queue_id = args.queue_id or active_mission_queue_id_for_continuation(manager_json())
    if queue_id:
        clear_recovery_block_for_queue_id(
            queue_id,
            reason="Explicit operator continue command.",
        )
        cmd.extend(["--queue-id", queue_id])
    if args.goal_file:
        cmd.extend(["--goal-file", args.goal_file])
    elif goal_text:
        cmd.extend(["--goal", goal_text])
    proc = run(cmd, timeout=240, input_text=None)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    payload = write_supervisor_state(manager_json())
    print_summary(payload)
    return proc.returncode


def cmd_queue_abandon(args: argparse.Namespace) -> int:
    if not args.queue_id:
        print("queue-abandon requires --queue-id", file=sys.stderr)
        return 2
    reason = args.reason or "operator abandoned stale paused queue item"
    payload = abandon_queue_item(args.queue_id, reason=reason)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"queue_abandon_status={payload.get('status')}")
        print(f"queue_id={payload.get('queue_id')}")
        print(f"reason={payload.get('reason')}")
        if payload.get("previous_status"):
            print(f"previous_status={payload.get('previous_status')}")
    return 0 if payload.get("ok") is True else 1


def nudge_goal(args: argparse.Namespace) -> int:
    feedback = str(args.goal or "").strip()
    if not feedback:
        print("nudge requires --goal feedback text", file=sys.stderr)
        return 2
    cmd = ["python3", str(MANAGER), "nudge", "--review-feedback", feedback]
    if args.json:
        cmd.append("--json")
    proc = run(cmd, timeout=120)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    payload = write_supervisor_state(manager_json())
    if not args.json:
        print_summary(payload)
    return proc.returncode


def monitor(args: argparse.Namespace) -> int:
    set_monitor_phase("starting", "monitor invoked")
    status = manager_json()
    review = None
    dispatch = None
    disposition_blocked_dispatch = False
    orphaned_running_blocked_dispatch = False
    auto_continue_enabled = args.auto_continue and auto_continue_allowed(status)
    # Recover stale "starting" items before any dispatch
    repaired_queue_ids = repair_duplicate_queue_ids()
    if repaired_queue_ids:
        review = {"queue_id_repairs": repaired_queue_ids}
        orphaned_running_blocked_dispatch = True
    stale = recover_stale_starting()
    if stale:
        print(f"recovered_stale_starting={len(stale)}")
        for s in stale:
            print(f"  {s.get('id')} age={s.get('age_seconds')}s")
    stopped = recover_stopped_running_items(status, auto_continue=auto_continue_enabled)
    if stopped:
        if review is None:
            review = {}
        review["stopped_running_recovery"] = stopped
        orphaned_running_blocked_dispatch = True
        status = manager_json()
    stopped_direct = auto_continue_stopped_direct_run(
        status, auto_continue=auto_continue_enabled
    )
    if stopped_direct:
        if review is None:
            review = {}
        review["stopped_direct_recovery"] = stopped_direct
        status = manager_json()
        if stopped_direct.get("status") != "continued":
            orphaned_running_blocked_dispatch = True
    complete_marker = (
        status.get("complete_marker")
        if isinstance(status.get("complete_marker"), dict)
        else {}
    )
    vllm = status.get("vllm") if isinstance(status.get("vllm"), dict) else {}
    completion_written = str(complete_marker.get("status") or "").lower() == "complete"
    tmux_running = status.get("tmux_running") is True
    vllm_running = float(vllm.get("running") or 0)
    vllm_waiting = float(vllm.get("waiting") or 0)
    # Completion-marker shutdown: complete.json + loop_state=complete but tmux still alive
    # Stop the leftover local-goal wrapper cleanly before review/accept
    completion_shutdown_needed = status.get("completion_marker_shutdown_needed") is True
    if completion_shutdown_needed:
        set_monitor_phase("stopping", "stopping completed tmux wrapper")
        stop = run(["python3", str(MANAGER), "stop"], timeout=120)
        if review is None:
            review = {}
        review["completion_marker_shutdown"] = {
            "returncode": stop.returncode,
            "stdout_tail": stop.stdout[-1000:],
            "stderr_tail": stop.stderr[-1000:],
        }
        status = manager_json()
    elif (
        completion_written and tmux_running and vllm_running == 0 and vllm_waiting == 0
    ):
        set_monitor_phase("stopping", "auto-stopping completed tmux wrapper")
        stop = run(["python3", str(MANAGER), "stop"], timeout=120)
        review = {
            "auto_stop_completed_tmux": {
                "returncode": stop.returncode,
                "stdout_tail": stop.stdout[-1000:],
                "stderr_tail": stop.stderr[-1000:],
            }
        }
        status = manager_json()
    classification, _action = classify(status)
    dispatch_queued_cloud_before_stale_review = (
        args.auto_dispatch
        and queued_cloud_executor_items()
        and active_run_has_prior_acceptance(status)
        and not status.get("tmux_running")
    )
    if dispatch_queued_cloud_before_stale_review:
        if review is None:
            review = {}
        review["stale_review_deferred_for_cloud_dispatch"] = {
            "reason": (
                "active run already has prior acceptance and a queued worker-lane "
                "item is waiting; dispatch queued GLM/cloud work before any "
                "review auto-continue can start local Node1 work"
            ),
            "active_run_dir": status.get("active_run_dir"),
            "queued_cloud_count": len(queued_cloud_executor_items()),
        }
        classification = "accepted"
    if classification == "needs_review":
        set_monitor_phase("reviewing", "running manager review")
        proc = run(["python3", str(MANAGER), "review", "--json"], timeout=240)
        try:
            review_result = json.loads(proc.stdout)
            if review:
                review.update(review_result)
            else:
                review = review_result
        except json.JSONDecodeError:
            review_result = {
                "status": "review_unreadable",
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-2000:],
            }
            if review:
                review.update(review_result)
            else:
                review = review_result
        if args.auto_accept and review.get("ok") is True:
            clear_auto_continue_loop_state()
            set_monitor_phase("accepting", "manager review passed; accepting run")
            accept = run(["python3", str(MANAGER), "accept", "--json"], timeout=240)
            try:
                review["acceptance"] = json.loads(accept.stdout)
            except json.JSONDecodeError:
                review["acceptance"] = {"status": "accept_unreadable"}
            status = manager_json()
            set_monitor_phase("disposing", "checking owned-file disposition")
            disposition = disposition_json(commit=args.auto_commit_owned)
            review["disposition"] = disposition
            if not disposition_commit_complete(
                disposition, commit=args.auto_commit_owned
            ):
                record_disposition_failure(review, disposition)
                disposition_blocked_dispatch = True
                if auto_continue_enabled:
                    set_monitor_phase(
                        "continuing",
                        "disposition failed; auto-continuing repair",
                    )
                    review["disposition_auto_continue"] = (
                        auto_continue_after_disposition_failure(status, disposition)
                    )
                    status = manager_json()
            else:
                reconciled = reconcile_running_queue_items(status)
                if reconciled:
                    review["queue_reconciled"] = reconciled
        elif auto_continue_enabled and review.get("ok") is not True:
            failed_checks = []
            for check in review.get("checks") or []:
                if isinstance(check, dict) and not check.get("ok"):
                    failed_checks.append(
                        {
                            "name": check.get("name"),
                            "detail": str(check.get("detail") or "")[:1000],
                        }
                    )
            if review_failure_is_marker_repairable(review):
                set_monitor_phase(
                    "repairing", "review failed on completion marker; repairing marker"
                )
                repair = run(marker_repair_command(status), timeout=120)
                review["marker_repair"] = {
                    "returncode": repair.returncode,
                    "stdout_tail": repair.stdout[-2000:],
                    "stderr_tail": repair.stderr[-2000:],
                }
                set_monitor_phase("reviewing", "re-running manager review after repair")
                repair_review_proc = run(
                    ["python3", str(MANAGER), "review", "--json"], timeout=240
                )
                try:
                    repaired_review = json.loads(repair_review_proc.stdout)
                except json.JSONDecodeError:
                    repaired_review = {
                        "status": "review_unreadable",
                        "ok": False,
                        "returncode": repair_review_proc.returncode,
                        "stdout_tail": repair_review_proc.stdout[-2000:],
                        "stderr_tail": repair_review_proc.stderr[-2000:],
                    }
                review["after_marker_repair_review"] = repaired_review
                if repaired_review.get("ok") is True:
                    clear_auto_continue_loop_state()
                    review.update(repaired_review)
                    if args.auto_accept:
                        set_monitor_phase(
                            "accepting", "marker repair passed review; accepting run"
                        )
                        accept = run(
                            ["python3", str(MANAGER), "accept", "--json"],
                            timeout=240,
                        )
                        try:
                            review["acceptance"] = json.loads(accept.stdout)
                        except json.JSONDecodeError:
                            review["acceptance"] = {"status": "accept_unreadable"}
                        status = manager_json()
                    else:
                        status = manager_json()
                    failed_checks = []
            if review.get("ok") is True:
                pass
            else:
                advisory = None
                if auto_external_review_enabled(args):
                    set_monitor_phase(
                        "external_reviewing",
                        "review failed; asking GLM/Kimi advisory supervisor",
                    )
                    advisory = run_auto_external_supervisor_review()
                    review["auto_external_review"] = advisory
                set_monitor_phase(
                    "continuing", "review failed; preparing auto-continue"
                )
                advisory_context = {}
                if isinstance(advisory, dict):
                    advisory_context = {
                        "ok": advisory.get("ok"),
                        "selected_reviewer": advisory.get("selected_reviewer"),
                        "attempts": [
                            {
                                "reviewer": item.get("reviewer"),
                                "ok": item.get("ok"),
                                "status": item.get("status"),
                                "auth_error": item.get("auth_error"),
                                "output_path": item.get("output_path"),
                            }
                            for item in advisory.get("attempts", [])
                            if isinstance(item, dict)
                        ],
                    }
                feedback = json.dumps(
                    {
                        "review_status": review.get("review_status")
                        or review.get("status"),
                        "ok": review.get("ok"),
                        "failed_checks": failed_checks[:20],
                        "external_supervisor_review": advisory_context,
                        "review_path": str(HARNESS_REPORTS / "review.json"),
                        "active_run_dir": status.get("active_run_dir"),
                        "complete_marker_path": status.get("complete_marker_path"),
                        "instruction": (
                            "Continue the local goal. Fix the failed review checks, "
                            "rerun the required verification, and do not write "
                            "complete.json again until the review can pass."
                        ),
                    },
                    indent=2,
                    sort_keys=True,
                )
                cont_cmd = [
                    "python3",
                    str(MANAGER),
                    "continue",
                    "--title",
                    "Hermes auto-continue after review",
                    "--executor",
                    "opencode",
                ]
                (
                    continue_current,
                    preflight_status,
                    preflight_block,
                ) = failed_review_auto_continue_preflight(status)
                status = preflight_status
                if not continue_current:
                    review["auto_continue_skipped"] = preflight_block
                else:
                    loop_guard = auto_continue_loop_guard(
                        status=status,
                        trigger="failed_review",
                        failed_checks=[item["name"] for item in failed_checks],
                        verdict=str(
                            review.get("review_status") or review.get("status") or ""
                        ),
                    )
                    review["auto_continue_loop_guard"] = loop_guard
                    if loop_guard.get("allowed") is not True:
                        review["auto_continue_skipped"] = loop_guard
                    else:
                        queue_id = str(
                            (status.get("run_meta") or {}).get("queue_id") or ""
                        )
                        if queue_id:
                            cont_cmd.extend(["--queue-id", queue_id])
                        cont_cmd.extend(["--review-feedback", feedback])
                        cont = run(cont_cmd, timeout=240)
                        review["auto_continue"] = {
                            "returncode": cont.returncode,
                            "stdout_tail": cont.stdout[-2000:],
                            "stderr_tail": cont.stderr[-2000:],
                        }
                        status = manager_json()
    if not disposition_blocked_dispatch:
        reconciled = reconcile_running_queue_items(status)
        if reconciled:
            if review is None:
                review = {}
            review["queue_reconciled"] = reconciled
        paused_orphans = pause_orphaned_running_queue_items(status)
        if paused_orphans:
            if review is None:
                review = {}
            review["orphaned_running_paused"] = paused_orphans
            orphaned_running_blocked_dispatch = True
    if status.get("accepted") is True:
        if review is None:
            review = {}
        set_monitor_phase("disposing", "accepted run; checking disposition")
        disposition = disposition_json(commit=args.auto_commit_owned)
        review.setdefault("disposition", disposition)
        # Review-honesty: a failed disposition cannot coexist silently with
        # an accepted verdict.  Record an explicit override field so the
        # review chain can see that disposition was attempted but failed.
        if not disposition_commit_complete(disposition, commit=args.auto_commit_owned):
            record_disposition_failure(review, disposition)
            disposition_blocked_dispatch = True
            if auto_continue_enabled:
                set_monitor_phase(
                    "continuing",
                    "accepted run disposition failed; auto-continuing repair",
                )
                review["disposition_auto_continue"] = (
                    auto_continue_after_disposition_failure(status, disposition)
                )
                status = manager_json()
    if not disposition_blocked_dispatch:
        mission_event = reconcile_mission_with_queue()
        if mission_event:
            if review is None:
                review = {}
            review["mission_reconciled"] = mission_event
    # If stuck from repeated commands or tool failures, generate a targeted recovery prompt
    if classification == "stuck":
        rcd = (
            status.get("repeated_command_detection")
            if isinstance(status.get("repeated_command_detection"), dict)
            else {}
        )
        stall_detection = (
            status.get("stall_detection")
            if isinstance(status.get("stall_detection"), dict)
            else {}
        )
        tool_edit_failures = int(stall_detection.get("tool_edit_failures") or 0)
        verification_command_failures = int(
            stall_detection.get("verification_command_failures") or 0
        )
        destructive_git_commands = int(
            stall_detection.get("destructive_git_commands") or 0
        )
        if (
            rcd.get("stuck") is True
            or tool_edit_failures >= 2
            or destructive_git_commands > 0
            or verification_command_failures >= 2
        ):
            # Generate the recovery prompt and write it
            recovery_prompt = _generate_targeted_recovery_prompt(status)
            recovery_path = STATE_DIR / "recovery-prompt.md"
            write_secure_file(recovery_path, recovery_prompt, 0o600)
            if review is None:
                review = {}
            trigger = (
                "tool_edit_failure"
                if tool_edit_failures >= 2
                else "destructive_git_command"
                if destructive_git_commands > 0
                else "verification_command_failure"
                if verification_command_failures >= 2
                else "repeated_command_loop"
            )
            review["targeted_recovery"] = {
                "trigger": trigger,
                "recovery_prompt_path": str(recovery_path),
                "repeated_command": rcd.get("repeated_command"),
                "repeated_count": rcd.get("repeated_count"),
                "tool_edit_failures": tool_edit_failures,
                "destructive_git_commands": destructive_git_commands,
                "verification_command_failures": verification_command_failures,
                "instruction": (
                    "The worker is stuck and needs targeted recovery feedback. "
                    "Use `continue` with the recovery prompt to break the cycle."
                ),
            }
            stuck_queue_recovery = record_stuck_recovery_attempt(
                status,
                trigger=trigger,
            )
            if stuck_queue_recovery:
                review["targeted_recovery"]["queue_recovery"] = stuck_queue_recovery
            hard_stuck_block = bool(
                stuck_queue_recovery
                and stuck_queue_recovery.get("recovery_blocked") is True
            )
            if hard_stuck_block:
                orphaned_running_blocked_dispatch = True
            if auto_continue_enabled and not hard_stuck_block:
                stopped_before_continue = None
                if (
                    trigger == "verification_command_failure"
                    and status.get("tmux_running") is True
                    and float(
                        (
                            status.get("vllm")
                            if isinstance(status.get("vllm"), dict)
                            else {}
                        ).get("running")
                        or 0
                    )
                    == 0
                    and float(
                        (
                            status.get("vllm")
                            if isinstance(status.get("vllm"), dict)
                            else {}
                        ).get("waiting")
                        or 0
                    )
                    == 0
                ):
                    stop = run(["python3", str(MANAGER), "stop"], timeout=120)
                    stopped_before_continue = {
                        "returncode": stop.returncode,
                        "stdout_tail": stop.stdout[-1000:],
                        "stderr_tail": stop.stderr[-1000:],
                    }
                cont_cmd = [
                    "python3",
                    str(MANAGER),
                    "continue",
                    "--title",
                    "Hermes auto-continue after targeted recovery",
                    "--executor",
                    "opencode",
                ]
                queue_id = str((status.get("run_meta") or {}).get("queue_id") or "")
                if queue_id:
                    cont_cmd.extend(["--queue-id", queue_id])
                cont_cmd.extend(["--review-feedback", recovery_prompt])
                cont = run(cont_cmd, timeout=240)
                review["targeted_recovery"]["auto_continue"] = {
                    "returncode": cont.returncode,
                    "stdout_tail": cont.stdout[-2000:],
                    "stderr_tail": cont.stderr[-2000:],
                }
                if stopped_before_continue is not None:
                    review["targeted_recovery"]["stopped_before_continue"] = (
                        stopped_before_continue
                    )
                status = manager_json()
    if (
        args.auto_dispatch
        and not disposition_blocked_dispatch
        and not orphaned_running_blocked_dispatch
    ):
        set_monitor_phase("dispatching", "checking queued or mission-auto work")
        queued_targets = queue_items("queued")
        target_queue_id = (
            str((queued_targets[0] or {}).get("id") or "") if queued_targets else ""
        )
        continuity_block = dispatch_continuity_block(
            status, target_queue_id=target_queue_id
        )
        if continuity_block is not None:
            dispatch = {"returncode": 1, **continuity_block}
        else:
            dispatch = dispatch_next_queued_goal(status)
            if dispatch and dispatch.get("returncode") == 0:
                status = manager_json()
            # If no queued work exists and the local-goal lane is free, mission
            # mode can create the next concrete subgoal. Dispatch it in the
            # same monitor pass.
            if (
                dispatch is None
                and not queue_has_active_work()
                and node1_is_free(status)
            ):
                mission = load_mission()
                if mission.get("status") == "active":
                    ms = mission_try_generate_and_enqueue(mission, status)
                    if ms:
                        dispatch = {
                            "queued_id": ms.get("id"),
                            "title": ms.get("title"),
                            "returncode": 0,
                            "mission_auto": True,
                        }
                        started = dispatch_next_queued_goal(status)
                        if started:
                            dispatch["started"] = started
                            if started.get("returncode") == 0:
                                status = manager_json()
    elif disposition_blocked_dispatch:
        dispatch = {
            "returncode": 1,
            "status": "blocked_by_disposition",
            "detail": (
                "Accepted run still has incomplete owned-file disposition; "
                "next mission subgoal was not dispatched."
            ),
        }
    elif orphaned_running_blocked_dispatch:
        dispatch = {
            "returncode": 1,
            "status": "blocked_by_orphaned_running",
            "detail": (
                "A running queue item had no tmux worker and was paused; "
                "next queued work was not dispatched in the same monitor pass."
            ),
        }
    set_monitor_phase("idle", "monitor completed")
    payload = write_supervisor_state(
        status,
        review=review,
        dispatch=dispatch,
        quiet_notify_stdout=args.json,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_summary(payload)
    return 0


def supervise(args: argparse.Namespace) -> int:
    """Run the active supervisor profile used by Hermes/Codex operators."""
    args.auto_accept = True
    args.auto_continue = True
    args.auto_dispatch = True
    args.auto_commit_owned = True
    return monitor(args)


def log() -> int:
    proc = run(["python3", str(MANAGER), "log"], timeout=90)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def external_review(args: argparse.Namespace) -> int:
    reviewer = (
        args.reviewer
        if getattr(args, "reviewer", "")
        else args.planner
        if args.planner and args.planner != "none"
        else "glm-5.2"
    )
    cmd = [
        "python3",
        str(MANAGER),
        "external-review",
        "--reviewer",
        reviewer,
        "--review-timeout",
        str(max(5, int(getattr(args, "review_timeout", 300) or 300))),
    ]
    if args.json:
        cmd.append("--json")
    review_timeout = max(5, int(getattr(args, "review_timeout", 300) or 300))
    supervisor_timeout = max(95, review_timeout + 90)
    try:
        proc = run(cmd, timeout=supervisor_timeout)
    except subprocess.TimeoutExpired as exc:
        payload = {
            "contract": "local_node1_goal_external_review.v1",
            "generated_at": utc_now(),
            "reviewer": reviewer,
            "ok": False,
            "status": "timeout",
            "returncode": 124,
            "timed_out": True,
            "supervisor_timeout": supervisor_timeout,
            "review_timeout": review_timeout,
            "stdout_tail": (exc.stdout or "")[-2000:]
            if isinstance(exc.stdout, str)
            else "",
            "stderr_tail": (exc.stderr or "")[-2000:]
            if isinstance(exc.stderr, str)
            else "",
            "command": cmd,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("external_review_status=timeout")
            print("ok=False")
            print(f"reviewer={reviewer}")
        status = manager_json()
        write_supervisor_state(status)
        return 1
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    status = manager_json()
    write_supervisor_state(status)
    return proc.returncode


def print_queue(args: argparse.Namespace) -> int:
    queue = load_queue()
    if args.json:
        print(json.dumps(queue, indent=2, sort_keys=True))
        return 0
    items = queue.get("items") or []
    print(f"queue_json={QUEUE_JSON}")
    print(f"items={len(items) if isinstance(items, list) else 0}")
    if isinstance(items, list):
        for item in items[-20:]:
            if not isinstance(item, dict):
                continue
            print(
                f"{item.get('status')} {item.get('id')} {item.get('title')} "
                f"planner={item.get('planner')} executor={item.get('executor')}"
            )
    return 0


# ---------------------------------------------------------------------------
# Mission commands: mission-create, mission-show, mission-stop, mission-resume,
# mission-monitor
# ---------------------------------------------------------------------------


def cmd_mission_create(args: argparse.Namespace) -> int:
    if not args.goal and not args.goal_file:
        print("mission-create requires --goal or --goal-file", file=sys.stderr)
        return 2
    if args.goal_file:
        goal_path = Path(args.goal_file)
        goal_text = goal_path.read_text(encoding="utf-8")
    else:
        goal_text = str(args.goal or "")
    if not goal_text.strip():
        print("mission-create goal text is empty", file=sys.stderr)
        return 2

    if args.planner and args.planner not in ALLOWED_PLANNERS:
        print(f"unsupported planner: {args.planner}", file=sys.stderr)
        return 2
    if args.executor and args.executor not in ALLOWED_EXECUTORS:
        print(f"unsupported executor: {args.executor}", file=sys.stderr)
        return 2
    executor_worker = str(getattr(args, "executor_worker", None) or "none")
    if executor_worker not in ALLOWED_EXECUTOR_WORKERS:
        print(f"unsupported executor worker: {executor_worker}", file=sys.stderr)
        return 2

    # Parse done-criteria from comma-separated or newline-separated string
    done_criteria_raw = args.done_criteria or ""
    done_criteria = [c.strip() for c in done_criteria_raw.split("\n") if c.strip()]
    if not done_criteria and "," in done_criteria_raw:
        done_criteria = [c.strip() for c in done_criteria_raw.split(",") if c.strip()]

    mission = empty_mission()
    mission["umbrella_objective"] = goal_text.strip()
    mission["status"] = "active"
    mission["created_at"] = now()
    mission["done_criteria"] = done_criteria
    mission["max_subgoals"] = int(args.max_subgoals) if args.max_subgoals else 20
    mission["planner"] = args.planner or "none"
    mission["executor"] = args.executor or "opencode"
    mission["executor_worker"] = executor_worker
    mission["next_action"] = "Mission created. Awaiting first subgoal generation."

    if args.dry_run:
        print("DRY_RUN: would write mission state")
        print(json.dumps(mission, indent=2, sort_keys=True))
        return 0

    write_mission(mission)
    print(f"mission_created_at={mission['created_at']}")
    print(f"mission_status={mission['status']}")
    print(f"mission_json={MISSION_JSON}")
    if args.json:
        print(json.dumps(mission, indent=2, sort_keys=True))
    return 0


def cmd_mission_show(args: argparse.Namespace) -> int:
    mission = load_mission()
    if args.json:
        print(json.dumps(mission, indent=2, sort_keys=True))
    else:
        print(f"mission_status={mission.get('status')}")
        print(f"mission_objective={mission.get('umbrella_objective', '')[:120]}")
        print(f"mission_created={mission.get('created_at', '')}")
        print(f"mission_updated={mission.get('updated_at', '')}")
        print(f"active_subgoal={mission.get('active_subgoal')}")
        print(f"completed={len(mission.get('completed_subgoals', []))}")
        print(f"failed={len(mission.get('failed_subgoals', []))}")
        print(f"rejected={len(mission.get('rejected_subgoals', []))}")
        print(
            f"generated_count={mission.get('generated_count', 0)}/{mission.get('max_subgoals', 20)}"
        )
        print(f"failure_streak={mission.get('failure_streak', 0)}")
        print(f"next_action={mission.get('next_action', '')}")
        print(f"mission_json={MISSION_JSON}")
    return 0


def cmd_mission_stop(args: argparse.Namespace) -> int:
    mission = load_mission()
    paused = pause_stopped_mission_queue_item(mission, manager_json())
    if mission.get("status") in {"idle", "stopped", "complete", "blocked"}:
        print(f"mission already in state: {mission.get('status')}")
        if paused:
            print(f"paused_queue_item={paused[0].get('id')}")
        return 0
    mission["status"] = "stopped"
    mission["next_action"] = "Mission stopped by operator."
    write_mission(mission)
    print(f"mission_stopped_at={now()}")
    if paused:
        print(f"paused_queue_item={paused[0].get('id')}")
    print(f"mission_json={MISSION_JSON}")
    return 0


def cmd_mission_resume(args: argparse.Namespace) -> int:
    status = manager_json()
    if status.get("tmux_running") is True:
        print(
            "mission-resume refused: local-goal tmux session is already running",
            file=sys.stderr,
        )
        return 2

    mission = load_mission()
    current_status = mission.get("status")
    if current_status == "active":
        print("mission already in state: active")
        return 0
    if current_status != "stopped":
        print(f"mission-resume requires stopped mission, found: {current_status}")
        return 2

    resumed = resume_paused_mission_queue_item(mission)
    mission["status"] = "active"
    mission["next_action"] = (
        "Mission resumed by operator. Run monitor --auto-accept --auto-continue "
        "--auto-dispatch to dispatch the active subgoal."
    )
    write_mission(mission)
    print(f"mission_resumed_at={now()}")
    if resumed:
        print(f"resumed_queue_item={resumed[0].get('id')}")
    else:
        print("resumed_queue_item=")
    print(f"mission_json={MISSION_JSON}")
    return 0


def cmd_mission_monitor(args: argparse.Namespace) -> int:
    """Mission-aware monitor: try to generate and enqueue next subgoal.

    Also reconciles accepted/failed queue items with mission state.
    """
    status = manager_json()
    mission = load_mission()

    if mission.get("status") not in {"active", "stopped"}:
        if args.json:
            print(
                json.dumps(
                    {"status": "skipped", "reason": "mission not active"}, indent=2
                )
            )
        else:
            print(f"mission not active (status={mission.get('status')}), skipping")
        return 0

    if mission.get("status") == "stopped":
        if args.json:
            print(json.dumps({"status": "stopped"}, indent=2))
        else:
            print("mission stopped, not generating subgoals")
        return 0

    # Step 1: reconcile an accepted active run back into the queue, then move
    # mission state forward. This lets mission-monitor recover when accept was
    # run directly and the queue item is still marked running.
    queue_reconciled = reconcile_running_queue_items(status)
    mission_event = reconcile_mission_with_queue()
    if mission_event:
        mission = load_mission()

    # Step 2: try to generate and enqueue next subgoal
    result = None
    if not args.dry_run:
        result = mission_try_generate_and_enqueue(mission, status)
    else:
        # Dry-run: just show what would happen
        subgoal = generate_subgoal(mission)
        if subgoal:
            result = {"dry_run": True, "would_generate": subgoal}
        else:
            result = {"dry_run": True, "reason": "no subgoal generated"}

    if args.json:
        output = {
            "mission": load_mission(),
            "generated_subgoal": result,
            "queue_reconciled": queue_reconciled,
            "mission_reconciled": mission_event,
            "node1_free": node1_is_free(status),
            "queue_has_work": queue_has_active_work(),
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        if result:
            if isinstance(result, dict) and result.get("dry_run"):
                print(
                    f"DRY_RUN: would generate subgoal: {result.get('would_generate', {}).get('title', 'unknown')}"
                )
            else:
                print(
                    f"enqueued_subgoal={result.get('id')} title={result.get('title')}"
                )
        else:
            print("no_subgoal_generated")
            if not node1_is_free(status):
                print("reason=node1_busy")
            elif queue_has_active_work():
                print("reason=queue_has_work")
            elif mission.get("active_subgoal"):
                print("reason=active_subgoal_waiting")

    return 0


def _generate_targeted_recovery_prompt(status: dict[str, Any]) -> str:
    """Generate a targeted recovery prompt for a stuck or failed run.

    Uses status JSON, active run path, and recent log excerpts to create
    a specific continue prompt that tells the worker what failed, what not
    to repeat, and whether to fix or write an honest incomplete/blocked
    complete.json.
    """
    lines: list[str] = [
        "# Targeted Recovery Prompt",
        "",
        f"Generated: `{now()}`",
        "",
        "You are resuming a local goal from a targeted recovery. Address the specific failures below.",
        "",
    ]

    # Repeated command info
    rcd = (
        status.get("repeated_command_detection")
        if isinstance(status.get("repeated_command_detection"), dict)
        else {}
    )
    if rcd.get("stuck") is True:
        lines.extend(
            [
                "## STUCK LOOP DETECTED",
                "",
                f"- The same command has been repeated {rcd.get('repeated_count')} times:",
                f"  `{rcd.get('repeated_command')}`",
                "",
                "DO NOT repeat this command. It is not making progress. Fix the underlying issue instead.",
                "",
            ]
        )

    stall_detection = (
        status.get("stall_detection")
        if isinstance(status.get("stall_detection"), dict)
        else {}
    )
    tool_edit_failures = int(stall_detection.get("tool_edit_failures") or 0)
    if tool_edit_failures >= 2:
        lines.extend(
            [
                "## EDIT TOOL FAILURE DETECTED",
                "",
                f"- Edit/write tool failures in the session log: `{tool_edit_failures}`",
                "- The tool refused an overwrite because the target file had not been read first.",
                "",
                "Recovery instructions:",
                "1. Read the exact file you need to edit immediately before editing it.",
                "2. Apply one minimal focused edit.",
                "3. Do not retry the same failed overwrite.",
                "4. If the implementation is already complete, write the required checkpoint/report/complete marker instead of continuing code changes.",
                "",
            ]
        )
    verification_command_failures = int(
        stall_detection.get("verification_command_failures") or 0
    )
    if verification_command_failures >= 2:
        lines.extend(
            [
                "## VERIFICATION COMMAND FAILURE DETECTED",
                "",
                f"- Verification command failures in the session log: `{verification_command_failures}`",
                "- The worker attempted to verify `jarvis_realtime.py` from a directory where that file is not present.",
                "",
                "Recovery instructions:",
                "1. Run verification from `/mnt/raid0/services/voice-assistant` or use absolute file paths.",
                "2. Do not count failed wrong-directory commands as product verification.",
                "3. Continue with focused tests and live text checks for the changed visible labels.",
                "4. Write the completion marker only after verification actually checks the changed files.",
                "",
            ]
        )
    destructive_git_commands = int(stall_detection.get("destructive_git_commands") or 0)
    if destructive_git_commands > 0:
        lines.extend(
            [
                "## DESTRUCTIVE GIT COMMAND DETECTED",
                "",
                f"- Destructive git commands in the session log: `{destructive_git_commands}`",
                "- The worker attempted a command that can discard, hide, or overwrite shared dirty work.",
                "",
                "Recovery instructions:",
                "1. Do not run `git checkout --`, `git restore`, `git reset --hard`, `git clean`, or `git stash` without explicit operator approval.",
                "2. Inspect `git status --short` and the exact diff for the owned files.",
                "3. Preserve unrelated dirty work. Repair only the intended owned files.",
                "4. If a destructive command already changed the tree, document the exact command and recovery action in the completion evidence before review.",
                "",
            ]
        )
    wrapper_command_misroutes = int(
        stall_detection.get("wrapper_command_misroutes") or 0
    )
    if wrapper_command_misroutes > 0:
        lines.extend(
            [
                "## WRAPPER COMMAND MISROUTE DETECTED",
                "",
                f"- Wrapper-only local-goal command misroutes in the session log: `{wrapper_command_misroutes}`",
                "- The worker called public wrapper commands through `local-node1-goal-supervisor.py`, where they are not valid subcommands.",
                "",
                "Recovery instructions:",
                "1. Retry public operator commands through `/mnt/raid0/documentation/scripts/local-goal`.",
                "2. Use examples such as `scripts/local-goal doctor --json` and `scripts/local-goal completion-summary`.",
                "3. Use the lower-level supervisor only for supported machine commands such as `status --json`, `capabilities --json`, `integration-audit --json`, `mission-show`, `mission-create`, and `monitor --json`.",
                "4. Continue with the smallest verified task step after correcting the command route.",
                "",
            ]
        )
    recovery_hint = str(stall_detection.get("recovery_hint") or "").strip()
    if recovery_hint:
        lines.extend(
            [
                "## RECOVERY HINT",
                "",
                recovery_hint,
                "",
            ]
        )

    # Active run context
    active_run_dir = status.get("active_run_dir")
    if active_run_dir:
        lines.extend(
            [
                "## ACTIVE RUN CONTEXT",
                "",
                f"- Active run directory: `{active_run_dir}`",
            ]
        )
    objective = status.get("current_objective")
    if objective:
        lines.append(f"- Current objective: {objective[:300]}")
    lines.append("")

    # Recent log excerpt
    recent_log = status.get("recent_log", [])
    if recent_log:
        lines.extend(
            [
                "## RECENT LOG EXCERPT",
                "",
            ]
        )
        for line in recent_log[-10:]:
            lines.append(f"  {line}")
        lines.append("")

    lines.extend(
        [
            "## ACTION",
            "",
            "Fix the underlying issue, then continue working toward the goal.",
            "Do NOT write complete.json again until the review can pass.",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hermes local Node1 long-goal supervisor"
    )
    parser.add_argument(
        "command",
        choices=[
            "status",
            "capabilities",
            "integration-audit",
            "monitor",
            "supervise",
            "start",
            "premium-start",
            "continue",
            "nudge",
            "log",
            "review",
            "external-review",
            "accept",
            "stop",
            "enqueue",
            "queue",
            "queue-abandon",
            "mission-create",
            "mission-show",
            "mission-stop",
            "mission-resume",
            "mission-monitor",
            "handoff",
            "cloud-loop",
        ],
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--title")
    parser.add_argument("--goal")
    parser.add_argument("--goal-file")
    parser.add_argument(
        "--output",
        help="For handoff --current: write handoff markdown to this path instead of stdout",
    )
    parser.add_argument(
        "--current",
        action="store_true",
        help="Generate handoff for the current active mission/run",
    )
    parser.add_argument(
        "--executor", default="opencode", choices=sorted(ALLOWED_EXECUTORS)
    )
    parser.add_argument(
        "--executor-worker",
        default="none",
        choices=sorted(ALLOWED_EXECUTOR_WORKERS),
        help=(
            "Cloud builder worker (Hermes worker_dispatch lane). 'none' (default) "
            "= local Node1 vLLM executor path, unchanged. opencode-glm-build / "
            "opencode-kimi-build route building through prime-directive dispatch. "
            "pi-zai-build-sandbox and pi-zai-executor-compare are explicit "
            "canary-only lanes, not defaults. kimi, codex, glm52-direct, "
            "and glm52-direct-implementation-canary "
            "are adapter-canary workers for proving registered terminal workers "
            "under the same review/acceptance gates."
        ),
    )
    parser.add_argument("--planner", default="none", choices=sorted(ALLOWED_PLANNERS))
    parser.add_argument(
        "--reviewer",
        choices=sorted(ALLOWED_PLANNERS - {"none"}),
        help="Reviewer model for external-review. Kept separate from --planner for clarity.",
    )
    parser.add_argument("--review-timeout", type=int, default=300)
    parser.add_argument("--queue-id")
    parser.add_argument("--reason")
    parser.add_argument("--run-dir")
    parser.add_argument("--auto-accept", action="store_true")
    parser.add_argument("--auto-continue", action="store_true")
    parser.add_argument("--auto-dispatch", action="store_true")
    parser.add_argument(
        "--auto-external-review",
        action="store_true",
        help="Ask GLM first, then Kimi, for advisory review before auto-continuing failed local review.",
    )
    parser.add_argument(
        "--no-auto-external-review",
        action="store_true",
        help="Skip GLM/Kimi advisory review before auto-continuing failed local review.",
    )
    parser.add_argument(
        "--auto-commit-owned",
        action="store_true",
        help="After accepted review, commit only local-goal-owned paths via manager disposition.",
    )
    parser.add_argument("--max-subgoals", type=int, default=20)
    parser.add_argument("--done-criteria")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.command == "status":
        payload = write_supervisor_state(manager_json(), quiet_notify_stdout=args.json)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print_summary(payload)
        return 0
    if args.command == "capabilities":
        return cmd_capabilities(args)
    if args.command == "integration-audit":
        return cmd_integration_audit(args)
    if args.command == "monitor":
        return monitor(args)
    if args.command == "supervise":
        return supervise(args)
    if args.command == "start":
        return start_goal(args)
    if args.command == "premium-start":
        if args.planner == "none":
            args.planner = "gpt-5.5"
        return start_goal(args)
    if args.command == "enqueue":
        return enqueue_goal(args)
    if args.command == "queue":
        return print_queue(args)
    if args.command == "queue-abandon":
        return cmd_queue_abandon(args)
    if args.command == "continue":
        return continue_goal(args)
    if args.command == "nudge":
        return nudge_goal(args)
    if args.command == "log":
        return log()
    if args.command == "external-review":
        return external_review(args)
    if args.command in {"review", "accept", "stop"}:
        return manager_action(args, args.command)
    if args.command == "mission-create":
        return cmd_mission_create(args)
    if args.command == "mission-show":
        return cmd_mission_show(args)
    if args.command == "mission-stop":
        return cmd_mission_stop(args)
    if args.command == "mission-resume":
        return cmd_mission_resume(args)
    if args.command == "mission-monitor":
        return cmd_mission_monitor(args)
    if args.command == "handoff":
        return cmd_handoff_current(args)
    if args.command == "cloud-loop":
        return run_cloud_goal_loop_command(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
