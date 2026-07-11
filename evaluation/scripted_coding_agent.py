#!/usr/bin/env python3
"""Deterministic coding-agent process used by both evaluation arms."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.fixture_support import fixture_target, load_fixture  # noqa: E402


EVIDENCE_REF = "review:1"
ATTEMPT_FILE = Path(".agentic-harness/evaluation-agent-attempt.json")


def next_attempt(workspace: Path) -> int:
    path = workspace / ATTEMPT_FILE
    previous = 0
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        previous = int(payload.get("attempt") or 0) if isinstance(payload, dict) else 0
    attempt = previous + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attempt": attempt}) + "\n", encoding="utf-8")
    return attempt


def main() -> int:
    workspace = Path.cwd()
    task = load_fixture(workspace)
    behavior = str(task.get("behavior") or "")
    attempt = next_attempt(workspace)
    if behavior == "exit_failure_then_repair" and attempt == 1:
        print("scripted first-attempt process failure", file=sys.stderr)
        return 1

    target = fixture_target(workspace, task.get("path"))
    target.parent.mkdir(parents=True, exist_ok=True)
    should_complete = behavior == "correct_first_try" or (
        behavior in {"false_then_repair", "exit_failure_then_repair"} and attempt >= 2
    )
    if not should_complete and behavior != "persistent_false_complete":
        if behavior != "false_then_repair":
            raise ValueError(f"unsupported fixture behavior: {behavior}")
    content = task.get("expected") if should_complete else task.get("incorrect")
    target.write_text(str(content or ""), encoding="utf-8", newline="")

    task_id = str(task.get("id") or "fixture")
    outcome = {
        "status": "complete",
        "summary": f"Scripted agent claims {task_id} is complete.",
        "current_subgoal": "independent verification",
        "checkpoint": f"structured completion claim emitted on attempt {attempt}",
        "plan": [{"step": "apply maintenance change", "status": "completed"}],
        "requirements": [
            {
                "id": task_id,
                "status": "satisfied",
                "evidence": [EVIDENCE_REF],
            }
        ],
        "blockers": [],
    }
    print("HARNESS_RESULT_JSON=" + json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
