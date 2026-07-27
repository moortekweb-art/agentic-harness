"""Transport-neutral Local Studio worker contract.

This module defines the narrow boundary that a future HTTP or local-process
transport must implement.  It intentionally does not claim acceptance: a
successful Local Studio process only moves a goal to Harness review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import time
from typing import Callable, Protocol

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


LOCAL_STUDIO_PROTOCOL_VERSION = "local-studio-worker.v1"


class LocalStudioRunStatus(StrEnum):
    """Execution states returned by Local Studio.

    ``EXITED`` deliberately means only that the worker process ended.  It is
    not a synonym for Harness ``Verified done``.
    """

    QUEUED = "queued"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_LOCAL_STUDIO_STATUSES = frozenset(
    {
        LocalStudioRunStatus.EXITED,
        LocalStudioRunStatus.FAILED,
        LocalStudioRunStatus.TIMED_OUT,
        LocalStudioRunStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class LocalStudioRunSpec:
    """Immutable input sent to a Local Studio worker lane."""

    run_id: str
    goal_id: str
    objective: str
    workspace: str
    model: str = ""
    attempt: int = 1
    acceptance_requirements: tuple[str, ...] = ()
    protocol_version: str = LOCAL_STUDIO_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("goal_id", self.goal_id),
            ("objective", self.objective),
            ("workspace", self.workspace),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.attempt < 1:
            raise ValueError("attempt must be at least 1")
        if any(
            not isinstance(requirement, str) or not requirement.strip()
            for requirement in self.acceptance_requirements
        ):
            raise ValueError("acceptance_requirements must contain non-empty strings")
        object.__setattr__(
            self,
            "acceptance_requirements",
            tuple(self.acceptance_requirements),
        )

    def to_payload(self) -> dict[str, object]:
        """Return the versioned transport payload without secrets."""
        return {
            "protocol_version": self.protocol_version,
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "objective": self.objective,
            "workspace": self.workspace,
            "model": self.model,
            "attempt": self.attempt,
            "acceptance_requirements": list(self.acceptance_requirements),
        }


@dataclass(frozen=True)
class LocalStudioRunHandle:
    """Opaque identity returned after Local Studio accepts a run."""

    run_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if not self.session_id.strip():
            raise ValueError("session_id must be non-empty")


@dataclass(frozen=True)
class LocalStudioRunState:
    """Untrusted execution state reported by Local Studio."""

    run_id: str
    status: LocalStudioRunStatus
    exit_code: int | None = None
    summary: str = ""
    worker_claim: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        object.__setattr__(self, "worker_claim", dict(self.worker_claim))


@dataclass(frozen=True)
class LocalStudioEvidenceBundle:
    """Redacted evidence collected after a Local Studio run exits."""

    run_id: str
    transcript: str = ""
    artifact_paths: tuple[str, ...] = ()
    workspace_digest_before: str = ""
    workspace_digest_after: str = ""
    redacted: bool = False
    worker_claim: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if any(not isinstance(path, str) or not path.strip() for path in self.artifact_paths):
            raise ValueError("artifact_paths must contain non-empty strings")
        object.__setattr__(self, "artifact_paths", tuple(self.artifact_paths))
        object.__setattr__(self, "worker_claim", dict(self.worker_claim))


class LocalStudioTransport(Protocol):
    """Transport contract for the future Local Studio integration."""

    def submit(self, spec: LocalStudioRunSpec) -> LocalStudioRunHandle:
        """Start one run and return its opaque session identity."""

    def poll(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        """Return current execution state; never return a Harness verdict."""

    def collect(self, handle: LocalStudioRunHandle) -> LocalStudioEvidenceBundle:
        """Collect redacted transcript, artifacts, and workspace evidence."""

    def cancel(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        """Request cancellation and return the resulting execution state."""


def validate_evidence_bundle(
    bundle: LocalStudioEvidenceBundle,
    *,
    expected_run_id: str,
) -> None:
    """Fail closed when a transport returns mismatched or unredacted evidence."""
    if bundle.run_id != expected_run_id:
        raise ValueError("Local Studio evidence run_id does not match the active run")
    if not bundle.redacted:
        raise ValueError("Local Studio evidence must be redacted before Harness ingestion")


class LocalStudioWorker:
    """Run Local Studio through a transport and hand execution evidence to review.

    This is an opt-in execution adapter.  It has no HTTP assumptions and is
    therefore easy to contract-test with a fake transport before a live Local
    Studio endpoint is selected.
    """

    def __init__(
        self,
        transport: LocalStudioTransport,
        *,
        workspace: str | Path = ".",
        model: str = "",
        timeout: float = 1800.0,
        poll_interval: float = 0.5,
        acceptance_requirements: Sequence[str] = (),
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        self.transport = transport
        self.workspace = str(Path(workspace).resolve())
        self.model = model
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.acceptance_requirements = tuple(acceptance_requirements)
        self.cancel_requested = cancel_requested or (lambda: False)

    def spec_for(self, goal: Goal) -> LocalStudioRunSpec:
        """Build a run spec from the Harness goal without trusting worker claims."""
        metadata = goal.metadata.get("local_studio")
        local_metadata = metadata if isinstance(metadata, dict) else {}
        workspace = str(local_metadata.get("workspace") or self.workspace)
        model = str(local_metadata.get("model") or self.model)
        requirements = local_metadata.get("acceptance_requirements")
        if isinstance(requirements, list):
            acceptance_requirements = tuple(str(item) for item in requirements)
        else:
            acceptance_requirements = self.acceptance_requirements
        return LocalStudioRunSpec(
            run_id=str(goal.metadata.get("worker_run_id") or goal.id),
            goal_id=goal.id,
            objective=goal.objective,
            workspace=workspace,
            model=model,
            attempt=_attempt_number(goal),
            acceptance_requirements=acceptance_requirements,
        )

    def run(self, goal: Goal) -> WorkerResult:
        """Execute a run; successful exit advances only to Harness review."""
        spec = self.spec_for(goal)
        handle = self.transport.submit(spec)
        state = self.transport.poll(handle)
        deadline = time.monotonic() + self.timeout
        while state.status not in TERMINAL_LOCAL_STUDIO_STATUSES:
            if self.cancel_requested():
                state = self.transport.cancel(handle)
                break
            if time.monotonic() >= deadline:
                state = self.transport.cancel(handle)
                if state.status not in TERMINAL_LOCAL_STUDIO_STATUSES:
                    state = LocalStudioRunState(
                        run_id=handle.run_id,
                        status=LocalStudioRunStatus.TIMED_OUT,
                        summary="Local Studio cancellation did not reach a terminal state",
                        worker_claim=state.worker_claim,
                    )
                break
            if self.poll_interval:
                time.sleep(self.poll_interval)
            state = self.transport.poll(handle)

        if state.run_id != handle.run_id:
            return WorkerResult(
                success=False,
                summary="Local Studio returned state for the wrong run",
                stderr="run identity mismatch",
                returncode=1,
            )

        evidence = self.transport.collect(handle)
        try:
            validate_evidence_bundle(evidence, expected_run_id=handle.run_id)
        except ValueError as exc:
            return WorkerResult(
                success=False,
                summary=f"Local Studio evidence rejected: {exc}",
                stderr=str(exc),
                returncode=1,
            )

        execution_succeeded = state.status is LocalStudioRunStatus.EXITED and state.exit_code == 0
        summary = state.summary.strip() or (
            "Local Studio execution exited; Harness review is still required"
            if execution_succeeded
            else f"Local Studio execution ended as {state.status.value}"
        )
        return WorkerResult(
            success=execution_succeeded,
            summary=summary,
            artifacts=list(evidence.artifact_paths),
            stdout=evidence.transcript,
            returncode=state.exit_code if state.exit_code is not None else 1,
            outcome={
                "local_studio": {
                    "protocol_version": LOCAL_STUDIO_PROTOCOL_VERSION,
                    "run_id": handle.run_id,
                    "session_id": handle.session_id,
                    "execution_status": state.status.value,
                    "worker_claim": dict(state.worker_claim),
                    "evidence_redacted": evidence.redacted,
                    "workspace_digest_before": evidence.workspace_digest_before,
                    "workspace_digest_after": evidence.workspace_digest_after,
                }
            },
        )


def _attempt_number(goal: Goal) -> int:
    value = goal.metadata.get("attempt")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 1
