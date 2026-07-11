from __future__ import annotations

import sys
import os
from pathlib import Path

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.core.state import Goal


def test_shell_transcript_is_private_and_redacted(tmp_path: Path) -> None:
    worker = ShellWorker(
        [sys.executable, "-c", "print('api_key=very-secret-value')"],
        cwd=tmp_path,
    )
    goal = Goal(objective="run safely")

    old_umask = os.umask(0)
    try:
        result = worker.run(goal)
    finally:
        os.umask(old_umask)

    transcript = tmp_path / result.artifacts[0]
    assert transcript.stat().st_mode & 0o077 == 0
    assert "very-secret-value" not in transcript.read_text(encoding="utf-8")


def test_coding_agent_transcript_is_private_and_redacted(tmp_path: Path) -> None:
    worker = CodingAgentWorker(
        [sys.executable, "-c", "print('token=very-secret-value')"],
        cwd=tmp_path,
    )
    goal = Goal(objective="run safely")

    old_umask = os.umask(0)
    try:
        result = worker.run(goal)
    finally:
        os.umask(old_umask)

    transcript = tmp_path / result.artifacts[0]
    assert transcript.stat().st_mode & 0o077 == 0
    assert "very-secret-value" not in transcript.read_text(encoding="utf-8")
