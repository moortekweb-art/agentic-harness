"""Coding-agent CLI worker adapter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class CodingAgentWorker:
    """Run a Codex/Aider/OpenCode-style CLI command and save a transcript."""

    def __init__(
        self,
        command_template: list[str],
        *,
        cwd: str | Path = ".",
        timeout: int = 1800,
        transcript_path: str = ".agentic-harness/runs/{goal_id}/coding-agent.log",
    ) -> None:
        if not command_template:
            raise ValueError("command_template must not be empty")
        self.command_template = command_template
        self.cwd = Path(cwd)
        self.timeout = timeout
        self.transcript_path = transcript_path

    def command_for(self, goal: Goal) -> list[str]:
        return [
            part.format(goal_id=goal.id, objective=goal.objective)
            for part in self.command_template
        ]

    def transcript_for(self, goal: Goal) -> Path:
        rel = Path(
            self.transcript_path.format(goal_id=goal.id, objective=goal.objective)
        )
        root = self.cwd.resolve()
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("transcript path is outside project directory") from exc
        return path

    def run(self, goal: Goal) -> WorkerResult:
        command = self.command_for(goal)
        env = os.environ.copy()
        env["AGENTIC_HARNESS_GOAL_ID"] = goal.id
        env["AGENTIC_HARNESS_OBJECTIVE"] = goal.objective
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.cwd),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return WorkerResult(
                success=False,
                summary=f"coding agent timed out after {self.timeout}s",
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                returncode=124,
            )
        except OSError as exc:
            executable = command[0] if command else "coding agent"
            return WorkerResult(
                success=False,
                summary=f"{executable} could not start: {exc}",
                stderr=str(exc),
                returncode=127,
            )
        try:
            transcript = self.transcript_for(goal)
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                "$ " + " ".join(command) + "\n\n"
                "[stdout]\n"
                f"{proc.stdout}\n"
                "[stderr]\n"
                f"{proc.stderr}",
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            return WorkerResult(
                success=False,
                summary=f"could not write transcript: {exc}",
                stdout=proc.stdout,
                stderr=str(exc),
                returncode=1,
            )
        success = proc.returncode == 0
        summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not summary:
            summary = "coding agent completed" if success else "coding agent failed"
        artifact = str(transcript.relative_to(self.cwd.resolve()))
        return WorkerResult(
            success=success,
            summary=summary,
            artifacts=[artifact],
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
