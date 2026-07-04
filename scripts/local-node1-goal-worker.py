#!/usr/bin/env python3
"""Hermes adapter for the local Node1 Codex-like long-goal harness.

This worker starts the planner-aware tmux-backed local goal manager and returns
a terminal_worker_result.v1 artifact immediately. The actual goal work
continues inside tmux so Hermes does not need to hold a request open for days.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MANAGER = Path("/mnt/raid0/documentation/scripts/local-node1-goal-manager.py")
PROFILE = Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller")
ALLOWED_OUTPUT_ROOTS = (PROFILE / "reports", PROFILE / "worker-runs")
STATE_ROOT = Path("/mnt/raid0/documentation/reports/local-node1-goal-harness")
PROMPT_DIR = STATE_ROOT / "prompts"
DOC_ROOT = Path("/mnt/raid0/documentation")


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def run(
    cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(DOC_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def tmux_running(session: str) -> bool:
    proc = run(["tmux", "has-session", "-t", session], timeout=10)
    return proc.returncode == 0


def manager_status() -> dict:
    proc = run(["python3", str(MANAGER), "status", "--json"], timeout=90)
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {
            "error": "manager status unreadable",
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
        }


def resolve_worker_output_path(raw_path: str, label: str) -> Path:
    path = Path(raw_path).expanduser()
    resolved = path.resolve(strict=False)
    allowed = [root.resolve(strict=False) for root in ALLOWED_OUTPUT_ROOTS]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        roots = ", ".join(str(root) for root in allowed)
        raise ValueError(f"{label} path must stay under controller artifact roots: {roots}")
    return resolved


def write_artifacts(
    *,
    args: argparse.Namespace,
    status: str,
    summary: str,
    prompt_copy: Path,
    command_output: str,
    commands_run: list[str],
    risk: str,
    next_action: str,
) -> int:
    report_path = resolve_worker_output_path(args.report, "report")
    status_path = resolve_worker_output_path(args.status, "status")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    session = os.environ.get("LOCAL_NODE1_GOAL_SESSION", "local-node1-goal")
    log_path = STATE_ROOT / "session.log"
    state_path = STATE_ROOT / "state.json"
    current_status = manager_status()

    active_run = current_status.get("active_run_dir")
    prev_run = current_status.get("previous_run_dir")
    vllm_liveness = (current_status.get("vllm") or {}).get("liveness", {})
    report = "\n".join(
        [
            "# Local Node1 Codex-Like Goal Harness",
            "",
            "## Summary",
            "",
            f"- Status: `{status}`",
            f"- Summary: {summary}",
            "- Purpose: explicit Hermes-managed local worker for long Codex-like implementation/debug/verification goals.",
            f"- tmux session: `{session}`",
            f"- Prompt copy: `{prompt_copy}`",
            f"- Runner log: `{log_path}`",
            f"- Runner state: `{state_path}`",
            f"- Manager status: `{STATE_ROOT / 'manager-status.json'}`",
            f"- Active run dir: `{active_run or 'none'}`",
            f"- Previous run dir: `{prev_run or 'none'}`",
            f"- Planner: `{current_status.get('active_planner', 'unknown')}`",
            f"- Executor: `{(current_status.get('runner_state') or {}).get('executor', (current_status.get('loop_state') or {}).get('executor', 'unknown'))}`",
            f"- vLLM: healthy=`{((current_status.get('vllm') or {}).get('healthy'))}` running=`{((current_status.get('vllm') or {}).get('running'))}` waiting=`{((current_status.get('vllm') or {}).get('waiting'))}`",
            f"- vLLM liveness: ok=`{vllm_liveness.get('ok', 'unknown')}`",
            "",
            "## Commands run",
            "",
            *[f"- `{command}`" for command in commands_run],
            "",
            "## Verification",
            "",
            f"- tmux running: `{tmux_running(session)}`",
            f"- Runner output: `{command_output.strip() or 'n/a'}`",
            f"- Active run dir exists: `{bool(active_run)}`",
            f"- Previous run dir exists: `{bool(prev_run)}`",
            "",
            "## Risks",
            "",
            f"- {risk}",
            "",
            "## Next recommended action",
            "",
            f"- {next_action}",
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")

    payload = {
        "contract": "terminal_worker_result.v1",
        "worker": "local-node1-goal",
        "task_id": args.task_id,
        "status": status,
        "summary": summary,
        "report_path": str(report_path),
        "files_changed": [str(prompt_copy)],
        "commands_run": commands_run,
        "verification": {
            "tmux_running": tmux_running(session),
            "runner_log": str(log_path),
            "runner_state": str(state_path),
            "active_run_dir": active_run,
            "previous_run_dir": prev_run,
            "vllm_liveness_ok": vllm_liveness.get("ok", False),
            "manager_status": current_status,
        },
        "risks": [risk],
        "next_recommended_action": next_action,
        "generated_at": now(),
    }
    status_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if status == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--workdir", default=str(DOC_ROOT))
    args = parser.parse_args()

    prompt_source = Path(args.prompt_file)
    if not prompt_source.exists():
        return write_artifacts(
            args=args,
            status="failed",
            summary="Prompt file was missing; local Node1 goal harness was not started.",
            prompt_copy=prompt_source,
            command_output="",
            commands_run=[],
            risk="No goal was launched.",
            next_action="Retry with a valid prompt file.",
        )

    session = os.environ.get("LOCAL_NODE1_GOAL_SESSION", "local-node1-goal")
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    prompt_copy = PROMPT_DIR / f"{args.task_id}.md"
    prompt_text = prompt_source.read_text(encoding="utf-8")
    prompt_copy.write_text(prompt_text, encoding="utf-8")

    if tmux_running(session):
        return write_artifacts(
            args=args,
            status="blocked",
            summary=f"Local Node1 goal harness is already running in tmux session {session}.",
            prompt_copy=prompt_copy,
            command_output="already running",
            commands_run=["tmux has-session -t local-node1-goal"],
            risk="Only one Node1 long-goal harness should run at a time to avoid saturating the active Node1 vLLM profile.",
            next_action="Inspect or stop the existing run with `python3 /mnt/raid0/documentation/scripts/local-node1-goal-manager.py status|log|stop`.",
        )

    executor = os.environ.get("LOCAL_NODE1_GOAL_EXECUTOR", "opencode")
    planner = os.environ.get("LOCAL_NODE1_GOAL_PLANNER", "none")
    proc = run(
        [
            "python3",
            str(MANAGER),
            "transfer",
            "--title",
            args.task_id,
            "--planner",
            planner,
            "--executor",
            executor,
            "--goal-file",
            str(prompt_copy),
        ],
        timeout=1000,
    )
    output = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
    started = proc.returncode == 0 and tmux_running(session)

    return write_artifacts(
        args=args,
        status="completed" if started else "failed",
        summary=(
            f"Started local Node1 Codex-like long-goal harness in tmux session {session} through manager transfer."
            if started
            else "Failed to start local Node1 Codex-like long-goal harness."
        ),
        prompt_copy=prompt_copy,
        command_output=output,
        commands_run=[
            f"python3 {MANAGER} transfer --planner {planner} --executor {executor} --goal-file {prompt_copy}"
        ],
        risk="This launches a long-running local model coding harness with write access inside the approved local workspaces.",
        next_action=f"Monitor with `python3 {MANAGER} status` or inspect logs with `python3 {MANAGER} log`.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
