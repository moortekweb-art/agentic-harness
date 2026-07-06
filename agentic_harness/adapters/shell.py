"""Subprocess-based worker adapter."""

from __future__ import annotations

import os
import subprocess
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
        try:
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
        except subprocess.TimeoutExpired as exc:
            return WorkerResult(
                success=False,
                summary=f"shell command timed out after {self.timeout}s: {' '.join(self.command)}",
                stdout=_text_or_empty(exc.stdout),
                stderr=_text_or_empty(exc.stderr),
                returncode=124,
            )
        except OSError as exc:
            executable = self.command[0]
            message = f"{executable} could not start: {exc}"
            return WorkerResult(
                success=False,
                summary=message,
                stderr=message,
                returncode=127,
            )
        success = proc.returncode == 0
        summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not summary:
            summary = "shell command completed" if success else "shell command failed"
        artifacts: list[str] = []
        try:
            transcript = self.transcript_for(goal)
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                "\n".join(
                    [
                        f"$ {' '.join(self.command)}",
                        f"returncode: {proc.returncode}",
                        "",
                        "[stdout]",
                        proc.stdout,
                        "[stderr]",
                        proc.stderr,
                    ]
                ),
                encoding="utf-8",
            )
            artifacts.append(transcript.relative_to(self.cwd.resolve()).as_posix())
        except OSError as exc:
            summary = f"{summary} (transcript write failed: {exc})"
        return WorkerResult(
            success=success,
            summary=summary,
            artifacts=artifacts,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )

    def transcript_for(self, goal: Goal) -> Path:
        root = self.cwd.resolve()
        transcript = (root / ".agentic-harness" / "runs" / goal.id / "shell-worker.log").resolve()
        try:
            transcript.relative_to(root)
        except ValueError as exc:
            raise ValueError("transcript path is outside project directory") from exc
        return transcript


def _text_or_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
