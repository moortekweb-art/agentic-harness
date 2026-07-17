"""Validate and score real managed Qwen/Ornith harness results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MATRIX_PATH = ROOT / "local_profile_matrix.json"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"line {number} is not a JSON object")
        rows.append(row)
    return rows


def score(rows: list[dict[str, Any]], matrix: dict[str, Any]) -> dict[str, Any]:
    tasks = matrix.get("tasks")
    profiles = matrix.get("profiles")
    thresholds = matrix.get("recommendation_thresholds")
    if not isinstance(tasks, list) or not isinstance(profiles, list) or not isinstance(thresholds, dict):
        raise ValueError("invalid local profile matrix")
    expected = {
        (str(profile), str(task["id"]))
        for task in tasks
        if isinstance(task, dict)
        for profile in task.get("profiles", [])
    }
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    required_booleans = (
        "deterministic_pass",
        "false_verified",
        "route_profile_correct",
        "guardrail_violation",
        "tool_calls_valid",
    )
    for row in rows:
        key = (str(row.get("profile") or ""), str(row.get("task_id") or ""))
        if key not in expected:
            raise ValueError(f"unexpected profile/task row: {key}")
        if key in indexed:
            raise ValueError(f"duplicate profile/task row: {key}")
        if any(not isinstance(row.get(field), bool) for field in required_booleans):
            raise ValueError(f"row {key} is missing required boolean evidence")
        indexed[key] = row
    missing = sorted(expected - indexed.keys())
    if missing:
        raise ValueError(f"missing profile/task rows: {missing}")

    summaries: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        profile_rows = [row for (name, _), row in indexed.items() if name == profile]
        passed = sum(row["deterministic_pass"] is True for row in profile_rows)
        supported = len(profile_rows)
        false_verified = sum(row["false_verified"] is True for row in profile_rows)
        route_errors = sum(row["route_profile_correct"] is not True for row in profile_rows)
        guardrail_violations = sum(row["guardrail_violation"] is True for row in profile_rows)
        invalid_tools = sum(row["tool_calls_valid"] is not True for row in profile_rows)
        pass_rate = passed / supported if supported else 0.0
        recommended = bool(
            pass_rate >= float(thresholds["minimum_deterministic_pass_rate"])
            and false_verified <= int(thresholds["maximum_false_verified"])
            and route_errors <= int(thresholds["maximum_route_profile_errors"])
            and guardrail_violations <= int(thresholds["maximum_guardrail_violations"])
        )
        summaries[str(profile)] = {
            "supported_cases": supported,
            "deterministic_passes": passed,
            "deterministic_pass_rate": round(pass_rate, 4),
            "false_verified": false_verified,
            "route_profile_errors": route_errors,
            "guardrail_violations": guardrail_violations,
            "invalid_tool_call_cases": invalid_tools,
            "mean_retries": round(
                sum(float(row.get("retries") or 0) for row in profile_rows) / supported,
                3,
            ),
            "mean_elapsed_seconds": round(
                sum(float(row.get("elapsed_seconds") or 0) for row in profile_rows) / supported,
                3,
            ),
            "recommended": recommended,
        }
    return {
        "schema": "agentic_harness.local_profile_score.v1",
        "thresholds": thresholds,
        "profiles": summaries,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Local profile evaluation",
        "",
        "| Profile | Cases | Pass rate | False verified | Route errors | Guardrail violations | Recommended |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for profile, row in summary["profiles"].items():
        lines.append(
            f"| {profile} | {row['supported_cases']} | {row['deterministic_pass_rate']:.1%} | "
            f"{row['false_verified']} | {row['route_profile_errors']} | "
            f"{row['guardrail_violations']} | {'yes' if row['recommended'] else 'no'} |"
        )
    lines.extend(["", "Generated from complete JSONL evidence; unsupported profile/task pairs are excluded.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = score(load_rows(args.results), load_json(MATRIX_PATH))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "README.md").write_text(render_markdown(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
