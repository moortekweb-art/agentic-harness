"""Worker protocol used by supervisors and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agentic_harness.core.state import Goal


@dataclass
class WorkerResult:
    success: bool
    summary: str
    artifacts: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    outcome: dict[str, Any] = field(default_factory=dict)


class Worker(Protocol):
    """Execution adapter interface."""

    def run(self, goal: Goal) -> WorkerResult:
        """Execute work for a goal and return a structured result."""
