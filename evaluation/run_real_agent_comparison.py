#!/usr/bin/env python3
"""Run the preregistered direct-versus-Harness real-agent comparison."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_harness.adapters.coding_agent import CodingAgentWorker  # noqa: E402
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy  # noqa: E402
from agentic_harness.core.review import DeterministicReviewer, command_passes  # noqa: E402
from agentic_harness.core.state import GoalStatus  # noqa: E402
from agentic_harness.core.supervisor import Supervisor  # noqa: E402

TASKS = ROOT / "evaluation" / "real_agent_tasks.json"
WORKER = ROOT / "evaluation" / "real_agent_worker.py"
VERIFIER = ROOT / "evaluation" / "verify_real_agent_task.py"


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def load_tasks(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") not in {
        "agentic_harness.real_agent_tasks.v1",
        "agentic_harness.hard_real_agent_tasks.v1",
    }:
        raise ValueError("unsupported real-agent task schema")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 10:
        raise ValueError("the preregistered comparison requires exactly ten tasks")
    return tasks


def materialize(workspace: Path, task: dict[str, Any]) -> None:
    workspace.mkdir(parents=True)
    files = task.get("files")
    if isinstance(files, dict):
        for raw_path, content in files.items():
            target = workspace / str(raw_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        return
    target = workspace / task["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(task["initial"], encoding="utf-8")


def verifier_command(task_file: Path, task_id: str) -> list[str]:
    return [sys.executable, str(VERIFIER), str(task_file), task_id]


def verify(workspace: Path, command: list[str]) -> bool:
    return subprocess.run(command, cwd=workspace, check=False).returncode == 0


def changed_paths(workspace: Path, task: dict[str, Any]) -> list[str]:
    initial_paths = (
        {str(path) for path in task["files"]}
        if isinstance(task.get("files"), dict)
        else {task["path"]}
    )
    actual = {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() and ".agentic-harness" not in path.parts
    }
    return sorted(actual | initial_paths)


def run_direct(
    workspace: Path, task: dict[str, Any], transcript: Path, model: str
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "AGENTIC_HARNESS_OBJECTIVE": task["objective"],
            "AGENTIC_HARNESS_INSTRUCTION": task["objective"],
            "REAL_AGENT_TRANSCRIPT": str(transcript),
            "REAL_AGENT_MODEL": model,
        }
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, str(WORKER)], cwd=workspace, env=env, text=True,
            capture_output=True, check=False, timeout=210,
        )
        returncode = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            _timeout_text(exc.stdout) + _timeout_text(exc.stderr), encoding="utf-8"
        )
        returncode = 124
        timed_out = True
    return {
        "accepted": returncode == 0,
        "attempts": 1,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "returncode": returncode,
        "timed_out": timed_out,
    }


def run_harness(
    workspace: Path, task: dict[str, Any], transcript: Path, review: list[str], model: str
) -> dict[str, Any]:
    previous = os.environ.get("REAL_AGENT_TRANSCRIPT")
    os.environ["REAL_AGENT_TRANSCRIPT"] = str(transcript)
    previous_model = os.environ.get("REAL_AGENT_MODEL")
    os.environ["REAL_AGENT_MODEL"] = model
    try:
        worker = CodingAgentWorker([sys.executable, str(WORKER)], cwd=workspace, timeout=210)
        reviewer = DeterministicReviewer([command_passes(review, cwd=workspace, timeout=30)])
        supervisor = Supervisor(project_dir=workspace, worker=worker, reviewer=reviewer)
        started = time.perf_counter()
        goal = AutonomousRunner(
            supervisor,
            policy=AutonomyPolicy(max_cycles=3, repeated_blocker_limit=3),
        ).run(task["objective"])
    finally:
        if previous is None:
            os.environ.pop("REAL_AGENT_TRANSCRIPT", None)
        else:
            os.environ["REAL_AGENT_TRANSCRIPT"] = previous
        if previous_model is None:
            os.environ.pop("REAL_AGENT_MODEL", None)
        else:
            os.environ["REAL_AGENT_MODEL"] = previous_model
    autonomy = goal.metadata.get("autonomy")
    autonomy = autonomy if isinstance(autonomy, dict) else {}
    return {
        "accepted": goal.status is GoalStatus.DONE and goal.metadata.get("accepted") is True,
        "attempts": int(autonomy.get("cycle") or 0),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "returncode": int(goal.metadata.get("worker_returncode") or 0),
        "final_status": goal.status.value,
    }


def run(output: Path, task_file: Path, seed: int, model: str) -> dict[str, Any]:
    tasks = load_tasks(task_file)
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        arms = ["direct", "harness"]
        random.Random(f"{seed}:{task['id']}").shuffle(arms)
        for position, arm in enumerate(arms, 1):
            workspace = output / "workspaces" / task["id"] / arm
            materialize(workspace, task)
            transcript = output / "transcripts" / f"{task['id']}-{arm}.log"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            review = verifier_command(task_file, task["id"])
            measured = (
                run_direct(workspace, task, transcript, model)
                if arm == "direct"
                else run_harness(workspace, task, transcript, review, model)
            )
            passed = verify(workspace, review)
            rows.append(
                {
                    "task_id": task["id"], "arm": arm, "arm_position": position,
                    **measured, "verifier_pass": passed,
                    "false_accept": bool(measured["accepted"]) and not passed,
                    "unintended_paths": [
                        path
                        for path in changed_paths(workspace, task)
                        if path not in (
                            set(task["files"])
                            if isinstance(task.get("files"), dict)
                            else {task["path"]}
                        )
                    ],
                }
            )
            _write_partial_rows(output, rows)
    summary: dict[str, Any] = {
        "schema": "agentic_harness.real_agent_comparison.v1",
        "disclaimer": "One-agent ten-task synthetic comparison; not broad model or adoption proof.",
        "seed": seed,
        "agent": subprocess.run(["codex", "--version"], text=True, capture_output=True).stdout.strip(),
        "model": model,
        "started_from_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True
        ).stdout.strip(),
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "token_metrics_available": False,
        "arms": {},
    }
    for arm in ("direct", "harness"):
        selected = [row for row in rows if row["arm"] == arm]
        summary["arms"][arm] = {
            "runs": len(selected),
            "accepted": sum(bool(row["accepted"]) for row in selected),
            "verifier_passes": sum(bool(row["verifier_pass"]) for row in selected),
            "false_accepts": sum(bool(row["false_accept"]) for row in selected),
            "mean_attempts": round(sum(int(row["attempts"]) for row in selected) / len(selected), 3),
            "mean_elapsed_seconds": round(
                sum(float(row["elapsed_seconds"]) for row in selected) / len(selected), 3
            ),
            "runs_with_unintended_paths": sum(bool(row["unintended_paths"]) for row in selected),
        }
    (output / "raw.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    (output / "raw.partial.jsonl").unlink(missing_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _write_partial_rows(output: Path, rows: list[dict[str, Any]]) -> None:
    temporary = output / "raw.partial.jsonl.tmp"
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(output / "raw.partial.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.output_dir.resolve(), args.tasks.resolve(), args.seed, args.model),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
