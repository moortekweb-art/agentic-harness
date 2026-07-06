"""Tmux-backed interactive worker adapter."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


class TmuxWorker:
    """Start a detached tmux session for a goal."""

    def __init__(
        self,
        command_template: str,
        *,
        session_prefix: str = "agentic-harness",
        cwd: str | Path = ".",
    ) -> None:
        self.command_template = command_template
        self.session_prefix = session_prefix
        self.cwd = Path(cwd)

    def command_for(self, goal: Goal) -> str:
        return self.command_template.format(
            goal_id=shlex.quote(goal.id),
            objective=shlex.quote(goal.objective),
        )

    def session_name_for(self, goal: Goal) -> str:
        return f"{self.session_prefix}-{goal.id[:12]}"

    def run(self, goal: Goal) -> WorkerResult:
        session = self.session_name_for(goal)
        command = self.command_for(goal)
        try:
            proc = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session, command],
                cwd=str(self.cwd),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            return WorkerResult(
                success=False,
                summary=f"tmux could not start: {exc}",
                stderr=str(exc),
                returncode=127,
            )
        summary = (
            f"tmux session started: {session}"
            if proc.returncode == 0
            else f"tmux session failed (exit {proc.returncode}): {session}"
        )
        return WorkerResult(
            success=proc.returncode == 0,
            summary=summary,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
