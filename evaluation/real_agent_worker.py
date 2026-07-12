#!/usr/bin/env python3
"""Run one real coding-agent attempt and emit the Harness worker contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def main() -> int:
    objective = os.environ.get("AGENTIC_HARNESS_INSTRUCTION") or os.environ.get(
        "AGENTIC_HARNESS_OBJECTIVE", ""
    )
    transcript = Path(os.environ.get("REAL_AGENT_TRANSCRIPT", "/tmp/real-agent.log"))
    prompt = (
        objective
        + "\nWork only inside the current directory. Inspect the relevant file, make the "
        "smallest correct edit, and verify it if practical. Do not create reports or notes."
    )
    completed = subprocess.run(
        [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "workspace-write", "--color", "never", prompt,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    result = {
        "status": "complete" if completed.returncode == 0 else "failed",
        "summary": "Real coding-agent process exited.",
        "evidence": [],
    }
    print("HARNESS_RESULT_JSON=" + json.dumps(result, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
