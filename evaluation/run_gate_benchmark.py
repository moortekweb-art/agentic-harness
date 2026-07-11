#!/usr/bin/env python3
"""Run the controlled two-arm completion-gate efficacy evaluation."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import random
import re
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_harness.adapters.coding_agent import CodingAgentWorker  # noqa: E402
from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy  # noqa: E402
from agentic_harness.core.review import (  # noqa: E402
    DeterministicReviewer,
    command_passes,
)
from agentic_harness.core.state import GoalStatus  # noqa: E402
from agentic_harness.core.supervisor import Supervisor  # noqa: E402


EVALUATION_DIR = Path(__file__).resolve().parent
DEFAULT_TASKS = EVALUATION_DIR / "tasks.json"
AGENT_SCRIPT = EVALUATION_DIR / "scripted_coding_agent.py"
VERIFIER_SCRIPT = EVALUATION_DIR / "verify_fixture.py"
FIXTURE_NAME = ".evaluation-task.json"
ARMS = ("baseline", "harness")
RAW_SCHEMA = "agentic_harness.gate_evaluation_result.v1"
SUMMARY_SCHEMA = "agentic_harness.gate_evaluation_summary.v1"
EVALUATION_TYPE = "controlled_completion_gate_efficacy"
SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
BEHAVIOR_CASES = {
    "correct_first_try": "true_completion",
    "false_then_repair": "premature_false_claim",
    "persistent_false_complete": "premature_false_claim",
    "exit_failure_then_repair": "recoverable_process_failure",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def public_command(script: Path) -> list[str]:
    return ["python", script.relative_to(ROOT).as_posix()]


def load_tasks(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "agentic_harness.gate_evaluation_tasks.v1":
        raise ValueError("unsupported task manifest schema")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task manifest must contain tasks")
    _validate_tasks(tasks)
    return tasks


def _validate_tasks(tasks: list[object]) -> None:
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("every task must be an object")
        task_id = str(task.get("id") or "")
        if SAFE_TASK_ID.fullmatch(task_id) is None:
            raise ValueError(f"unsafe task id: {task_id}")
        if task_id in seen:
            raise ValueError("task ids must be unique")
        seen.add(task_id)
        behavior = str(task.get("behavior") or "")
        if behavior not in BEHAVIOR_CASES:
            raise ValueError(f"task {task_id} has an unsupported behavior")
        if task.get("case") != BEHAVIOR_CASES[behavior]:
            raise ValueError(f"task {task_id} has an unsupported case")
        if not str(task.get("payload") or "").strip():
            raise ValueError(f"task {task_id} has no payload")
        if not str(task.get("maintenance_kind") or "").strip():
            raise ValueError(f"task {task_id} has no maintenance kind")
        if not str(task.get("objective") or "").strip():
            raise ValueError(f"task {task_id} has no objective")
        if str(task.get("initial") or "") == str(task.get("expected") or ""):
            raise ValueError(f"task {task_id} does not require a change")
        if str(task.get("incorrect") or "") in {
            str(task.get("initial") or ""),
            str(task.get("expected") or ""),
        }:
            raise ValueError(f"task {task_id} has no distinct incorrect result")
        _safe_relative_path(task.get("path"))


def _safe_relative_path(value: object) -> Path:
    raw = str(value)
    path = Path(raw)
    if raw in {"", ".", ".."} or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe fixture path: {value}")
    return path


def arm_order(seed: int, repetition: int, task_id: str) -> tuple[str, str]:
    arms = list(ARMS)
    random.Random(f"{seed}:{repetition}:{task_id}").shuffle(arms)
    return arms[0], arms[1]


def materialize_task(workspace: Path, task: dict[str, Any]) -> None:
    target = workspace / _safe_relative_path(task["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(task["initial"]), encoding="utf-8")
    (workspace / FIXTURE_NAME).write_text(
        json.dumps(task, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_claim(stdout: str) -> dict[str, Any]:
    marker = "HARNESS_RESULT_JSON="
    for line in reversed(stdout.splitlines()):
        if not line.startswith(marker):
            continue
        try:
            value = json.loads(line[len(marker) :])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def run_baseline(
    workspace: Path,
    task: dict[str, Any],
    agent_command: list[str],
    verifier_command: list[str],
) -> dict[str, Any]:
    started = time.perf_counter()
    environment = os.environ.copy()
    environment.update(
        {
            "AGENTIC_HARNESS_GOAL_ID": f"baseline-{task['id']}",
            "AGENTIC_HARNESS_OBJECTIVE": str(task["objective"]),
            "AGENTIC_HARNESS_INSTRUCTION": str(task["objective"]),
        }
    )
    process = subprocess.run(
        agent_command,
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    outcome = parse_claim(process.stdout)
    claim_complete = str(outcome.get("status") or "").lower() == "complete"
    accepted = process.returncode == 0 and claim_complete
    verifier = subprocess.run(
        verifier_command,
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    result = {
        "accepted": accepted,
        "attempts": 1,
        "claim_complete": claim_complete,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "final_status": "done" if accepted else "failed",
        "returncode": process.returncode,
        "verifier_pass": verifier.returncode == 0,
    }
    tokens = _tokens_from_outcome(outcome)
    if tokens is not None:
        result["tokens"] = tokens
    return result


def run_harness(
    workspace: Path,
    task: dict[str, Any],
    agent_command: list[str],
    verifier_command: list[str],
) -> dict[str, Any]:
    started = time.perf_counter()
    worker = CodingAgentWorker(agent_command, cwd=workspace, timeout=30)
    reviewer = DeterministicReviewer([command_passes(verifier_command, cwd=workspace, timeout=30)])
    supervisor = Supervisor(project_dir=workspace, worker=worker, reviewer=reviewer)
    policy = AutonomyPolicy(repeated_blocker_limit=3, max_cycles=4)
    goal = AutonomousRunner(supervisor, policy=policy).run(str(task["objective"]))
    autonomy = goal.metadata.get("autonomy")
    autonomy = autonomy if isinstance(autonomy, dict) else {}
    outcome = goal.metadata.get("worker_outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    review = goal.review if isinstance(goal.review, dict) else {}
    result = {
        "accepted": goal.status is GoalStatus.DONE and goal.metadata.get("accepted") is True,
        "attempts": int(autonomy.get("cycle") or 0),
        "claim_complete": str(outcome.get("status") or "").lower() == "complete",
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "final_status": goal.status.value,
        "returncode": int(goal.metadata.get("worker_returncode") or 0),
        "verifier_pass": review.get("passed") is True,
    }
    tokens = _tokens_from_outcome(outcome)
    if tokens is not None:
        result["tokens"] = tokens
    return result


def _tokens_from_outcome(outcome: dict[str, Any]) -> float | None:
    usage = outcome.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("total_tokens")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def run_benchmark(
    tasks_path: Path,
    output_dir: Path,
    *,
    seed: int,
    repetitions: int,
) -> dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    tasks = load_tasks(tasks_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    agent_command = [sys.executable, str(AGENT_SCRIPT)]
    verifier_command = [sys.executable, str(VERIFIER_SCRIPT)]
    command_sha256 = sha256_bytes(json.dumps(public_command(AGENT_SCRIPT)).encode())
    started_at = _utc_now()
    rows: list[dict[str, Any]] = []
    pristine: dict[tuple[int, str], str] = {}

    with TemporaryDirectory(prefix="agentic-harness-gate-eval-") as temporary:
        temporary_root = Path(temporary)
        for repetition in range(1, repetitions + 1):
            for task in tasks:
                task_id = str(task["id"])
                for position, arm in enumerate(arm_order(seed, repetition, task_id), 1):
                    workspace = temporary_root / f"r{repetition}-{task_id}-{arm}"
                    workspace.mkdir()
                    materialize_task(workspace, task)
                    initial_checksum = tree_sha256(workspace)
                    key = (repetition, task_id)
                    expected_checksum = pristine.setdefault(key, initial_checksum)
                    if initial_checksum != expected_checksum:
                        raise RuntimeError(f"arms are not pristine copies for {task_id}")
                    if arm == "baseline":
                        measured = run_baseline(workspace, task, agent_command, verifier_command)
                    else:
                        measured = run_harness(workspace, task, agent_command, verifier_command)
                    case = str(task["case"])
                    behavior = str(task["behavior"])
                    verifier_pass = bool(measured["verifier_pass"])
                    accepted = bool(measured["accepted"])
                    row = {
                        "schema": RAW_SCHEMA,
                        "evaluation_type": EVALUATION_TYPE,
                        "seed": seed,
                        "repetition": repetition,
                        "task_id": task_id,
                        "payload": str(task["payload"]),
                        "maintenance_kind": str(task["maintenance_kind"]),
                        "case": case,
                        "behavior": behavior,
                        "arm": arm,
                        "arm_position": position,
                        "engine": (
                            "direct_exit_zero_claim"
                            if arm == "baseline"
                            else "CodingAgentWorker+AutonomousRunner+DeterministicReviewer"
                        ),
                        "initial_tree_sha256": initial_checksum,
                        "agent_command_sha256": command_sha256,
                        **measured,
                        "false_success": (
                            case == "premature_false_claim" and accepted and not verifier_pass
                        ),
                        "caught_false_claim": (
                            case == "premature_false_claim"
                            and arm == "harness"
                            and int(measured["attempts"]) > 1
                        ),
                        "recovered": (
                            behavior in {"false_then_repair", "exit_failure_then_repair"}
                            and accepted
                            and verifier_pass
                            and int(measured["attempts"]) > 1
                        ),
                    }
                    rows.append(row)

    finished_at = _utc_now()
    summary = summarize(rows, len(tasks), repetitions, seed)
    environment = environment_metadata(
        tasks_path,
        seed=seed,
        repetitions=repetitions,
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_outputs(output_dir, rows, environment, summary)
    return summary


def summarize(
    rows: list[dict[str, Any]],
    task_count: int,
    repetitions: int,
    seed: int,
) -> dict[str, Any]:
    token_metrics_available = any("tokens" in row for row in rows)
    arms = {arm: _summarize_arm([row for row in rows if row["arm"] == arm]) for arm in ARMS}
    return {
        "schema": SUMMARY_SCHEMA,
        "evaluation_type": EVALUATION_TYPE,
        "disclaimer": (
            "Controlled completion-gate efficacy evaluation using a scripted process; "
            "not real-model performance."
        ),
        "seed": seed,
        "repetitions": repetitions,
        "task_count": task_count,
        "record_count": len(rows),
        "token_metrics_available": token_metrics_available,
        "pristine_arm_mismatches": 0,
        "arms": arms,
    }


def _summarize_arm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    false_claims = [row for row in rows if row["case"] == "premature_false_claim"]
    false_successes = sum(bool(row["false_success"]) for row in false_claims)
    caught = sum(bool(row["caught_false_claim"]) for row in false_claims)
    verified_accepts = sum(bool(row["accepted"]) and bool(row["verifier_pass"]) for row in rows)
    accepted = sum(bool(row["accepted"]) for row in rows)
    summary: dict[str, Any] = {
        "runs": len(rows),
        "accepted": accepted,
        "verified_accepts": verified_accepts,
        "acceptance_precision": _rate(verified_accepts, accepted),
        "recovered_tasks": sum(bool(row["recovered"]) for row in rows),
        "verifier_passes": sum(bool(row["verifier_pass"]) for row in rows),
        "verifier_pass_rate": _rate(sum(bool(row["verifier_pass"]) for row in rows), len(rows)),
        "false_claim_cases": len(false_claims),
        "false_successes": false_successes,
        "false_success_rate": _rate(false_successes, len(false_claims)),
        "caught_false_claims": caught,
        "caught_false_claim_rate": _rate(caught, len(false_claims)),
        "mean_attempts": round(statistics.fmean(float(row["attempts"]) for row in rows), 6),
        "mean_elapsed_seconds": round(
            statistics.fmean(float(row["elapsed_seconds"]) for row in rows), 6
        ),
    }
    tokens = [float(row["tokens"]) for row in rows if "tokens" in row]
    if tokens:
        summary["token_observations"] = len(tokens)
        summary["mean_tokens"] = round(statistics.fmean(tokens), 6)
    return summary


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def environment_metadata(
    tasks_path: Path,
    *,
    seed: int,
    repetitions: int,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    source_paths = {
        "evaluation/tasks.json": tasks_path,
        "evaluation/run_gate_benchmark.py": Path(__file__),
        "evaluation/scripted_coding_agent.py": AGENT_SCRIPT,
        "evaluation/verify_fixture.py": VERIFIER_SCRIPT,
        "evaluation/fixture_support.py": EVALUATION_DIR / "fixture_support.py",
        "agentic_harness/adapters/coding_agent.py": (
            ROOT / "agentic_harness/adapters/coding_agent.py"
        ),
        "agentic_harness/core/autonomy.py": ROOT / "agentic_harness/core/autonomy.py",
        "agentic_harness/core/review.py": ROOT / "agentic_harness/core/review.py",
        "agentic_harness/core/supervisor.py": ROOT / "agentic_harness/core/supervisor.py",
    }
    commit = _git_output(["rev-parse", "HEAD"])
    status = _git_output(["status", "--porcelain=v1"])
    try:
        harness_version = metadata.version("local-agentic-harness")
    except metadata.PackageNotFoundError:
        harness_version = "source-tree"
    return {
        "schema": "agentic_harness.gate_evaluation_environment.v1",
        "evaluation_type": EVALUATION_TYPE,
        "seed": seed,
        "repetitions": repetitions,
        "started_at": started_at,
        "finished_at": finished_at,
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "harness_version": harness_version,
        "git_commit": commit,
        "git_dirty": bool(status),
        "git_status_sha256": sha256_bytes(status.encode()),
        "agent_command": public_command(AGENT_SCRIPT),
        "verifier_command": public_command(VERIFIER_SCRIPT),
        "source_checksums": {label: sha256_file(path) for label, path in source_paths.items()},
    }


def _git_output(arguments: list[str]) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return process.stdout.strip() if process.returncode == 0 else "unavailable"


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_outputs(
    output_dir: Path,
    rows: list[dict[str, Any]],
    environment: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    (output_dir / "raw.jsonl").write_text(raw, encoding="utf-8")
    _write_json(output_dir / "environment.json", environment)
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Controlled completion-gate efficacy evaluation",
        "",
        "This is a deterministic scripted gate evaluation, not real-model performance.",
        "",
        f"- Task-behavior cases: {summary['task_count']}",
        f"- Repetitions: {summary['repetitions']}",
        f"- Seed: {summary['seed']}",
        f"- Token metrics available: {str(summary['token_metrics_available']).lower()}",
        "",
        "| Arm | Verified accepts | Verifier pass | False-success rate (false-claim cases) | Caught false claims | Recovered | Mean attempts | Mean seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        metrics = summary["arms"][arm]
        lines.append(
            f"| {arm} | {metrics['verified_accepts']} | "
            f"{metrics['verifier_pass_rate']:.1%} | {metrics['false_success_rate']:.1%} | "
            f"{metrics['caught_false_claims']} | {metrics['recovered_tasks']} | "
            f"{metrics['mean_attempts']:.2f} | {metrics['mean_elapsed_seconds']:.4f} |"
        )
    lines.extend(
        [
            "",
            "The baseline trusts an exit-zero structured completion claim. The harness arm uses ",
            "`CodingAgentWorker`, `AutonomousRunner`, and an independent verifier process.",
            "The false-success rate denominator is the intentionally false-claim cases, not all runs.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the controlled completion-gate efficacy evaluation."
    )
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EVALUATION_DIR / "results" / "latest",
    )
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--repetitions", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_benchmark(
        args.tasks.resolve(),
        args.output_dir.resolve(),
        seed=args.seed,
        repetitions=args.repetitions,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
