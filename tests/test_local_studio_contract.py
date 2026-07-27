"""Contract tests for the transport-neutral Local Studio adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess

import pytest

from agentic_harness.adapters.local_studio import (
    LOCAL_STUDIO_PROTOCOL_VERSION,
    LocalStudioEvidenceBundle,
    HttpLocalStudioTransport,
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


class StubHttpLocalStudioTransport(HttpLocalStudioTransport):
    def __init__(self, responses: list[dict[str, object]], workspace: Path) -> None:
        super().__init__(model="test-model", request_timeout=1, tool_access="full")
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        self.workspace = workspace

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.requests.append((method, path, payload))
        return self.responses.pop(0)


def _init_git_workspace(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_http_transport_dispatches_polls_and_collects_redacted_evidence(
    tmp_path: Path,
) -> None:
    _init_git_workspace(tmp_path)
    transport = StubHttpLocalStudioTransport(
        [
            {"runtimeSessionId": "run-1", "piSessionId": None},
            {
                "sessionId": "run-1",
                "status": {"active": True, "lastError": None},
                "events": [
                    {
                        "seq": 1,
                        "timestamp": "2026-07-27T00:00:00Z",
                        "event": {"type": "agent_start"},
                    }
                ],
            },
            {
                "sessionId": "run-1",
                "status": {"active": False, "lastError": None},
                "events": [
                    {
                        "seq": 2,
                        "timestamp": "2026-07-27T00:00:01Z",
                        "event": {
                            "type": "agent_settled",
                            "message": "token=secret-value",
                        },
                    }
                ],
            },
        ],
        tmp_path,
    )

    result = LocalStudioWorker(
        transport,
        workspace=tmp_path,
        poll_interval=0,
    ).run(Goal("change one fixture", id="goal-1", metadata={"worker_run_id": "run-1"}))

    assert result.success is True
    assert result.outcome["local_studio"]["execution_status"] == "exited"
    assert result.artifacts == [
        ".agentic-harness/runs/run-1/local-studio-transcript.jsonl"
    ]
    transcript = (tmp_path / result.artifacts[0]).read_text(encoding="utf-8")
    assert "<redacted>" in transcript
    assert "secret-value" not in transcript
    assert transport.requests[0][0:2] == ("POST", "/api/agent/turn")
    assert transport.requests[0][2] == {
        "sessionId": "run-1",
        "modelId": "test-model",
        "message": "change one fixture",
        "cwd": str(tmp_path.resolve()),
        "piSessionId": None,
        "toolAccess": "full",
        "browserToolEnabled": False,
        "mode": "prompt",
    }


def test_http_transport_rejects_runtime_identity_change(tmp_path: Path) -> None:
    _init_git_workspace(tmp_path)
    transport = StubHttpLocalStudioTransport(
        [{"runtimeSessionId": "different-run"}],
        tmp_path,
    )

    result = LocalStudioWorker(transport, workspace=tmp_path, poll_interval=0).run(
        Goal("inspect fixture", id="goal-1", metadata={"worker_run_id": "run-1"})
    )

    assert result.success is False
    assert "changed the requested run identity" in result.summary


def test_http_transport_fails_fast_on_inactive_runtime_error(tmp_path: Path) -> None:
    _init_git_workspace(tmp_path)
    transport = StubHttpLocalStudioTransport(
        [
            {"runtimeSessionId": "run-1"},
            {
                "sessionId": "run-1",
                "status": {"active": False, "lastError": "provider failed"},
                "events": [],
            },
        ],
        tmp_path,
    )

    result = LocalStudioWorker(
        transport,
        workspace=tmp_path,
        poll_interval=0,
    ).run(Goal("inspect fixture", id="goal-1", metadata={"worker_run_id": "run-1"}))

    assert result.success is False
    assert result.returncode == 1
    assert "provider failed" in result.summary


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("http://10.0.0.8:8081", "must use HTTPS"),
        ("https://studio.example.test:bad", "valid port"),
        ("https://user:secret@studio.example.test", "URL credentials"),
        ("https://studio.example.test?token=secret", "query or fragment"),
    ],
)
def test_http_transport_rejects_unsafe_endpoints(endpoint: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        HttpLocalStudioTransport(endpoint=endpoint, model="test-model")


def test_http_transport_accepts_loopback_http_endpoint() -> None:
    transport = HttpLocalStudioTransport(
        endpoint="http://127.0.0.1:8081/",
        model="test-model",
    )

    assert transport.endpoint == "http://127.0.0.1:8081"


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
