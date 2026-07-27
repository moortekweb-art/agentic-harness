"""Contract tests for the transport-neutral Local Studio adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agentic_harness.adapters.local_studio import (
    LOCAL_STUDIO_PROTOCOL_VERSION,
    LocalStudioEvidenceBundle,
    LocalStudioRunHandle,
    LocalStudioRunSpec,
    LocalStudioRunState,
    LocalStudioRunStatus,
    LocalStudioWorker,
)
from agentic_harness.core.state import Goal


@dataclass
class FakeLocalStudioTransport:
    states: list[LocalStudioRunState]
    evidence: LocalStudioEvidenceBundle
    submitted: list[LocalStudioRunSpec] = field(default_factory=list)
    cancelled: list[LocalStudioRunHandle] = field(default_factory=list)

    def submit(self, spec: LocalStudioRunSpec) -> LocalStudioRunHandle:
        self.submitted.append(spec)
        return LocalStudioRunHandle(run_id=spec.run_id, session_id="studio-session-1")

    def poll(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        del handle
        if self.states:
            return self.states.pop(0)
        return LocalStudioRunState(
            run_id="run-1",
            status=LocalStudioRunStatus.EXITED,
            exit_code=0,
        )

    def collect(self, handle: LocalStudioRunHandle) -> LocalStudioEvidenceBundle:
        assert handle.run_id == self.evidence.run_id
        return self.evidence

    def cancel(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        self.cancelled.append(handle)
        return LocalStudioRunState(
            run_id=handle.run_id,
            status=LocalStudioRunStatus.CANCELLED,
            summary="cancelled by Harness",
        )


def _evidence(*, run_id: str = "run-1", redacted: bool = True) -> LocalStudioEvidenceBundle:
    return LocalStudioEvidenceBundle(
        run_id=run_id,
        transcript="redacted transcript",
        artifact_paths=(".agentic-harness/runs/run-1/transcript.jsonl",),
        workspace_digest_before="before-digest",
        workspace_digest_after="after-digest",
        redacted=redacted,
        worker_claim={"status": "complete"},
    )


def test_run_spec_is_versioned_and_preserves_attempt_and_requirements(tmp_path: Path) -> None:
    spec = LocalStudioRunSpec(
        run_id="run-1",
        goal_id="goal-1",
        objective="change one fixture",
        workspace=str(tmp_path),
        model="kimi-k3",
        attempt=2,
        acceptance_requirements=("fixture_changed", "checks_pass"),
    )

    assert spec.to_payload() == {
        "protocol_version": LOCAL_STUDIO_PROTOCOL_VERSION,
        "run_id": "run-1",
        "goal_id": "goal-1",
        "objective": "change one fixture",
        "workspace": str(tmp_path),
        "model": "kimi-k3",
        "attempt": 2,
        "acceptance_requirements": ["fixture_changed", "checks_pass"],
    }


def test_worker_submits_and_returns_execution_evidence_without_verified_claim(
    tmp_path: Path,
) -> None:
    transport = FakeLocalStudioTransport(
        states=[
            LocalStudioRunState(run_id="run-1", status=LocalStudioRunStatus.RUNNING),
            LocalStudioRunState(
                run_id="run-1",
                status=LocalStudioRunStatus.EXITED,
                exit_code=0,
                worker_claim={"status": "complete"},
            ),
        ],
        evidence=_evidence(),
    )
    goal = Goal("change one fixture", id="goal-1", metadata={"worker_run_id": "run-1"})
    worker = LocalStudioWorker(transport, workspace=tmp_path, poll_interval=0)

    result = worker.run(goal)

    assert result.success is True
    assert result.artifacts == [".agentic-harness/runs/run-1/transcript.jsonl"]
    assert transport.submitted[0].protocol_version == LOCAL_STUDIO_PROTOCOL_VERSION
    assert result.outcome["local_studio"]["worker_claim"] == {"status": "complete"}
    assert "verified" not in result.outcome
    assert "status" not in result.outcome


def test_unredacted_evidence_fails_closed_and_is_not_returned(tmp_path: Path) -> None:
    transport = FakeLocalStudioTransport(
        states=[
            LocalStudioRunState(run_id="run-1", status=LocalStudioRunStatus.EXITED, exit_code=0),
        ],
        evidence=_evidence(redacted=False),
    )

    result = LocalStudioWorker(transport, workspace=tmp_path, poll_interval=0).run(
        Goal("inspect fixture", id="goal-1", metadata={"worker_run_id": "run-1"})
    )

    assert result.success is False
    assert "must be redacted" in result.summary
    assert result.stdout == ""
    assert result.artifacts == []


def test_cancel_request_is_forwarded_to_local_studio(tmp_path: Path) -> None:
    transport = FakeLocalStudioTransport(
        states=[LocalStudioRunState(run_id="run-1", status=LocalStudioRunStatus.RUNNING)],
        evidence=_evidence(),
    )

    result = LocalStudioWorker(
        transport,
        workspace=tmp_path,
        poll_interval=0,
        cancel_requested=lambda: True,
    ).run(Goal("long task", id="goal-1", metadata={"worker_run_id": "run-1"}))

    assert result.success is False
    assert result.outcome["local_studio"]["execution_status"] == "cancelled"
    assert [handle.session_id for handle in transport.cancelled] == ["studio-session-1"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0}, "timeout must be positive"),
        ({"poll_interval": -1}, "poll_interval must not be negative"),
    ],
)
def test_worker_rejects_invalid_timing_configuration(
    tmp_path: Path, kwargs: dict[str, float], message: str
) -> None:
    transport = FakeLocalStudioTransport(states=[], evidence=_evidence())
    with pytest.raises(ValueError, match=message):
        LocalStudioWorker(transport, workspace=tmp_path, **kwargs)
