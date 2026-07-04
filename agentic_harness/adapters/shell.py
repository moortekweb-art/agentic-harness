"""Subprocess-based worker adapter."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


class ShellWorker:
    """Run a configured command and pass the goal objective in the environment."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | Path = ".",
        timeout: int = 600,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self.command = command
        self.cwd = Path(cwd)
        self.timeout = timeout

    def run(self, goal: Goal) -> WorkerResult:
        env = os.environ.copy()
        env["AGENTIC_HARNESS_GOAL_ID"] = goal.id
        env["AGENTIC_HARNESS_OBJECTIVE"] = goal.objective
        proc = subprocess.run(
            self.command,
            cwd=str(self.cwd),
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
            env=env,
            input=None,
        )
        success = proc.returncode == 0
        summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not summary:
            summary = "shell command completed" if success else "shell command failed"
        return WorkerResult(
            success=success,
            summary=summary,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
