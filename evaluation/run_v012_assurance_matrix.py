#!/usr/bin/env python3
"""Run the preregistered v0.12 adversarial assurance matrix."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "evaluation" / "v012_assurance_cases.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_matrix(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "agentic_harness.assurance_cases.v1":
        raise ValueError("unsupported assurance matrix schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("assurance matrix must contain cases")
    identities = [row.get("id") for row in cases if isinstance(row, dict)]
    if len(identities) != len(cases) or len(set(identities)) != len(identities):
        raise ValueError("assurance case IDs must be present and unique")
    for row in cases:
        if not isinstance(row.get("category"), str) or not isinstance(row.get("test"), str):
            raise ValueError("every assurance case needs a category and pytest node id")
    return payload


def package_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def run(matrix_path: Path, output_dir: Path) -> dict[str, Any]:
    matrix = load_matrix(matrix_path)
    if matrix.get("release") != package_version():
        raise ValueError("matrix release does not match package version")
    output_dir.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    source_paths = {
        matrix_path.resolve(),
        Path(__file__).resolve(),
        (ROOT / "evaluation" / "V012_ASSURANCE_PROTOCOL.md").resolve(),
    }
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for case in matrix["cases"]:
        node_id = str(case["test"])
        source_paths.add((ROOT / node_id.split("::", 1)[0]).resolve())
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", node_id],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        rows.append(
            {
                "id": case["id"],
                "category": case["category"],
                "test": node_id,
                "passed": completed.returncode == 0,
                "returncode": completed.returncode,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    raw_path = output_dir / "raw.jsonl"
    raw_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    summary: dict[str, Any] = {
        "schema": "agentic_harness.assurance_evaluation.v1",
        "release": package_version(),
        "commit": commit,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "pytest": subprocess.run(
                [sys.executable, "-m", "pytest", "--version"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip(),
        },
        "matrix": {
            "schema": matrix["schema"],
            "sha256": sha256(matrix_path),
            "cases": len(rows),
        },
        "source_checksums": {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(source_paths)
        },
        "passed": sum(bool(row["passed"]) for row in rows),
        "failed": sum(not bool(row["passed"]) for row in rows),
        "acceptance_gate_passed": all(bool(row["passed"]) for row in rows),
        "raw_sha256": sha256(raw_path),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.matrix.resolve(), args.output_dir.resolve())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["acceptance_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
