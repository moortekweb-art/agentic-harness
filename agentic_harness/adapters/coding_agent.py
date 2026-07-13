"""Coding-agent CLI worker adapter."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.safety import (
    command_uses_windows_shell,
    resolve_command_executable,
)
from agentic_harness.core.secure_io import write_private_text
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
        instruction = self.instruction_for(goal)
        result: list[str] = []
        for part in self.command_template:
            # Use safe substitution: replace known placeholders first, then
            # fall back to str.format for any remaining template variables.
            # This prevents objectives containing bare braces from breaking
            # the format string while still supporting arbitrary template
            # variables (goal_id, objective, and any user-defined ones).
            try:
                substituted = part.format(goal_id=goal.id, objective=instruction)
            except (KeyError, ValueError):
                # If format() fails (e.g. unmatched braces in template),
                # fall back to safe replacement of known placeholders only.
                substituted = part.replace("{goal_id}", goal.id).replace(
                    "{objective}", instruction
                )
            result.append(substituted)
        return result

    def instruction_for(self, goal: Goal) -> str:
        autonomy = goal.metadata.get("autonomy")
        instruction = goal.metadata.get("continuation_instruction")
        if (
            isinstance(autonomy, dict)
            and autonomy.get("strict_completion") is True
            and isinstance(instruction, str)
            and instruction.strip()
        ):
            return instruction
        return goal.objective

    def transcript_for(self, goal: Goal) -> Path:
        rel = Path(self.transcript_path.format(goal_id=goal.id, objective=goal.objective))
        root = self.cwd.resolve()
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("transcript path is outside project directory") from exc
        return path

    def run(self, goal: Goal) -> WorkerResult:
        command = resolve_command_executable(self.command_for(goal))
        self._append_event(
            goal,
            kind="tool_started",
            summary="Started the installed coding-agent process",
            status="started",
        )
        env = os.environ.copy()
        env["AGENTIC_HARNESS_GOAL_ID"] = goal.id
        env["AGENTIC_HARNESS_OBJECTIVE"] = goal.objective
        env["AGENTIC_HARNESS_INSTRUCTION"] = self.instruction_for(goal)
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.cwd),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
                env=env,
                shell=command_uses_windows_shell(command),
            )
        except subprocess.TimeoutExpired as exc:
            self._append_event(
                goal,
                kind="tool_finished",
                summary=f"Coding-agent process timed out after {self.timeout}s",
                status="timed_out",
            )
            return WorkerResult(
                success=False,
                summary=f"coding agent timed out after {self.timeout}s",
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                returncode=124,
            )
        except OSError as exc:
            executable = command[0] if command else "coding agent"
            self._append_event(
                goal,
                kind="tool_finished",
                summary=f"Coding-agent process could not start: {executable}",
                status="failed",
            )
            return WorkerResult(
                success=False,
                summary=f"{executable} could not start: {exc}",
                stderr=str(exc),
                returncode=127,
            )
        self._append_event(
            goal,
            kind="tool_finished",
            summary=(
                "Installed coding-agent process completed"
                if proc.returncode == 0
                else f"Installed coding-agent process failed with exit code {proc.returncode}"
            ),
            status="completed" if proc.returncode == 0 else "failed",
        )
        outcome = _parse_harness_outcome(proc.stdout)
        try:
            transcript = self.transcript_for(goal)
            write_private_text(
                transcript,
                redact_secrets(
                    "$ "
                    + " ".join(command)
                    + f"\n\n[stdout]\n{proc.stdout}\n[stderr]\n{proc.stderr}"
                ),
            )
        except (OSError, ValueError) as exc:
            # Transcript write failure is a logging issue, not a work failure.
            # Surface the error in the summary but preserve the actual result.
            summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            if not summary:
                summary = (
                    "coding agent completed" if proc.returncode == 0 else "coding agent failed"
                )
            summary = f"{summary} (transcript write failed: {exc})"
            return WorkerResult(
                success=proc.returncode == 0,
                summary=summary,
                stdout=proc.stdout,
                stderr=str(exc),
                returncode=proc.returncode,
                outcome=outcome,
            )
        success = proc.returncode == 0
        outcome_summary = outcome.get("summary")
        summary = str(outcome_summary).strip() if isinstance(outcome_summary, str) else ""
        if not summary:
            summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not summary:
            summary = "coding agent completed" if success else "coding agent failed"
        artifact = transcript.relative_to(self.cwd.resolve()).as_posix()
        return WorkerResult(
            success=success,
            summary=summary,
            artifacts=[artifact],
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
            outcome=outcome,
        )

    def _append_event(
        self,
        goal: Goal,
        *,
        kind: str,
        summary: str,
        status: str,
    ) -> None:
        autonomy = goal.metadata.get("autonomy")
        cycle = int(autonomy.get("cycle") or 0) + 1 if isinstance(autonomy, dict) else 0
        checkpoint = (
            str(autonomy.get("checkpoint") or "") if isinstance(autonomy, dict) else ""
        )
        try:
            TaskEventStore(
                self.cwd,
                goal.id,
                run_id=str(goal.metadata.get("worker_run_id") or ""),
            ).append(
                stage="act",
                kind=kind,
                summary=summary,
                tool_name="coding_agent",
                tool_status=status,
                cycle=cycle,
                checkpoint=checkpoint,
            )
        except (OSError, ValueError):
            return


def _parse_harness_outcome(stdout: str) -> dict[str, object]:
    """Return the last valid structured outcome emitted by a coding agent.

    Coding-agent CLIs do not all preserve the exact marker spelling requested
    in the prompt.  In practice they emit either the documented
    ``HARNESS_RESULT_JSON=<object>`` marker or a JSON wrapper such as
    ``{"HARNESS_RESULT_JSON": <object>}``, and either form may be pretty
    printed across multiple lines.  Parse all supported candidates and keep
    the last valid one instead of letting a later malformed marker erase an
    earlier valid completion claim.
    """

    marker = "HARNESS_RESULT_JSON="
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, object]]] = []

    start = 0
    while True:
        index = stdout.find(marker, start)
        if index < 0:
            break
        try:
            value, _ = decoder.raw_decode(stdout[index + len(marker) :].lstrip())
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(value, dict):
                candidates.append((index, value))
        start = index + len(marker)

    wrapper_key = '"HARNESS_RESULT_JSON"'
    start = 0
    while True:
        key_index = stdout.find(wrapper_key, start)
        if key_index < 0:
            break
        object_index = stdout.rfind("{", 0, key_index + 1)
        if object_index >= 0:
            try:
                value, _ = decoder.raw_decode(stdout[object_index:])
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, dict):
                    outcome = value.get("HARNESS_RESULT_JSON")
                    if isinstance(outcome, dict):
                        candidates.append((object_index, outcome))
        start = key_index + len(wrapper_key)

    if not candidates:
        return {}
    return max(candidates, key=lambda item: item[0])[1]
