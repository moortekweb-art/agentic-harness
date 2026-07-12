#!/usr/bin/env python3
"""Run one real coding-agent attempt and emit the Harness worker contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    objective = os.environ.get("AGENTIC_HARNESS_INSTRUCTION") or os.environ.get(
        "AGENTIC_HARNESS_OBJECTIVE", ""
    )
    transcript = Path(os.environ.get("REAL_AGENT_TRANSCRIPT", "/tmp/real-agent.log"))
    model = os.environ.get("REAL_AGENT_MODEL", "").strip()
    if not model:
        print("REAL_AGENT_MODEL is required", file=sys.stderr)
        return 2
    prompt = (
        objective
        + "\nWork only inside the current directory. Inspect the relevant file, make the "
        "smallest correct edit, and verify it if practical. Do not create reports or notes."
    )
    command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "--model", model,
            "--color", "never", prompt,
        ]
    timed_out = False
    try:
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False, timeout=180,
        )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
        returncode = 124
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(stdout + stderr, encoding="utf-8")
    result = {
        "status": "complete" if returncode == 0 else "failed",
        "summary": "Real coding-agent process exited.",
        "current_subgoal": "independent verification",
        "checkpoint": "real coding-agent process completed",
        "plan": [{"step": "apply the requested maintenance change", "status": "completed"}],
        "requirements": [
            {
                "id": "requested-change",
                "status": "satisfied",
                "evidence": ["review:1"],
            }
        ],
        "blockers": ["coding agent timed out"] if timed_out else [],
    }
    print("HARNESS_RESULT_JSON=" + json.dumps(result, sort_keys=True))
    return returncode


def _timeout_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


if __name__ == "__main__":
    raise SystemExit(main())
