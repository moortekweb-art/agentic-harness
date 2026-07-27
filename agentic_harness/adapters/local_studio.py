"""Local Studio worker contract and HTTP transport.

The transport intentionally does not claim acceptance: a successful Local
Studio process only moves a goal to Harness review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import ipaddress
import json
from pathlib import Path
import time
from typing import Any, Callable, Protocol
import urllib.error
import urllib.parse
import urllib.request

from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.secure_io import write_private_text
from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult
from agentic_harness.core.workspace_transaction import workspace_fingerprint


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
    """Transport contract for a Local Studio execution lane."""

    def submit(self, spec: LocalStudioRunSpec) -> LocalStudioRunHandle:
        """Start one run and return its opaque session identity."""

    def poll(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        """Return current execution state; never return a Harness verdict."""

    def collect(self, handle: LocalStudioRunHandle) -> LocalStudioEvidenceBundle:
        """Collect redacted transcript, artifacts, and workspace evidence."""

    def cancel(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        """Request cancellation and return the resulting execution state."""


@dataclass
class _HttpRunRecord:
    """Transport-owned state for one Local Studio execution."""

    workspace: Path
    workspace_digest_before: str
    event_cursor: int = 0
    events: list[dict[str, object]] = field(default_factory=list)
    started: bool = False
    settled: bool = False
    failed: bool = False
    cancelled: bool = False


class HttpLocalStudioTransport:
    """HTTP transport for Local Studio's loopback agent-runtime API.

    Local Studio exposes execution state, not a completion verdict.  This
    transport deliberately maps only the runtime session lifecycle and leaves
    acceptance to Agentic Harness's independent reviewer.
    """

    _MAX_RESPONSE_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        endpoint: str = "http://127.0.0.1:8081",
        model: str = "",
        api_key: str = "",
        request_timeout: float = 30.0,
        tool_access: str = "full",
    ) -> None:
        endpoint = _validate_endpoint(endpoint)
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if tool_access not in {"read_only", "full"}:
            raise ValueError("tool_access must be read_only or full")
        self.endpoint = endpoint.rstrip("/")
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.request_timeout = request_timeout
        self.tool_access = tool_access
        self._runs: dict[str, _HttpRunRecord] = {}

    def sensitive_values(self) -> tuple[str, ...]:
        """Return the optional transport credential for artifact redaction."""
        return (self.api_key,) if self.api_key else ()

    def submit(self, spec: LocalStudioRunSpec) -> LocalStudioRunHandle:
        workspace = Path(spec.workspace).expanduser().resolve()
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ValueError("Local Studio workspace must be an existing directory")
        model = spec.model.strip() or self.model
        if not model:
            raise ValueError("Local Studio model is required")
        before = workspace_fingerprint(workspace)
        payload = {
            "sessionId": spec.run_id,
            "modelId": model,
            "message": spec.objective,
            "cwd": str(workspace),
            "piSessionId": None,
            "toolAccess": self.tool_access,
            "browserToolEnabled": False,
            "mode": "prompt",
        }
        response = self._request_json("POST", "/api/agent/turn", payload)
        runtime_session_id = _required_string(response, "runtimeSessionId")
        if runtime_session_id != spec.run_id:
            raise RuntimeError(
                "Local Studio changed the requested run identity; refusing to bind "
                "the Harness goal to another session"
            )
        self._runs[spec.run_id] = _HttpRunRecord(
            workspace=workspace,
            workspace_digest_before=before,
        )
        return LocalStudioRunHandle(run_id=spec.run_id, session_id=runtime_session_id)

    def poll(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        record = self._record(handle)
        if record.cancelled:
            return _cancelled_state(handle)
        query = urllib.parse.urlencode(
            {"sessionId": handle.session_id, "after": record.event_cursor}
        )
        response = self._request_json("GET", f"/api/agent/runtime/status?{query}")
        returned_session_id = _required_string(response, "sessionId")
        if returned_session_id != handle.session_id:
            raise RuntimeError("Local Studio returned status for the wrong runtime session")
        status = response.get("status")
        if not isinstance(status, Mapping):
            raise RuntimeError("Local Studio runtime session disappeared")
        self._record_events(record, response.get("events"))
        active = status.get("active") is True
        last_error = status.get("lastError")
        if active or record.events:
            record.started = True
        if isinstance(last_error, str) and last_error.strip():
            record.failed = True
        if record.cancelled:
            return _cancelled_state(handle)
        if record.failed and not active:
            summary = (
                redact_secrets(last_error.strip())
                if isinstance(last_error, str) and last_error.strip()
                else "Local Studio agent session failed"
            )
            return LocalStudioRunState(
                run_id=handle.run_id,
                status=LocalStudioRunStatus.FAILED,
                exit_code=1,
                summary=summary,
            )
        if record.settled:
            failed = record.failed
            return LocalStudioRunState(
                run_id=handle.run_id,
                status=(
                    LocalStudioRunStatus.FAILED
                    if failed
                    else LocalStudioRunStatus.EXITED
                ),
                exit_code=1 if failed else 0,
                summary=(
                    redact_secrets(str(last_error).strip())
                    if failed and isinstance(last_error, str) and last_error.strip()
                    else "Local Studio agent session settled"
                ),
            )
        return LocalStudioRunState(
            run_id=handle.run_id,
            status=(
                LocalStudioRunStatus.RUNNING
                if record.started and active
                else LocalStudioRunStatus.QUEUED
            ),
            summary="Local Studio agent session is running"
            if record.started and active
            else "Local Studio agent session is queued",
        )

    def collect(self, handle: LocalStudioRunHandle) -> LocalStudioEvidenceBundle:
        record = self._record(handle)
        after = workspace_fingerprint(record.workspace)
        transcript = "\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True)
            for event in record.events
        )
        transcript = redact_secrets(transcript)
        evidence_path = (
            record.workspace
            / ".agentic-harness"
            / "runs"
            / _safe_run_component(handle.run_id)
            / "local-studio-transcript.jsonl"
        )
        write_private_text(evidence_path, transcript + ("\n" if transcript else ""))
        artifact = evidence_path.relative_to(record.workspace).as_posix()
        return LocalStudioEvidenceBundle(
            run_id=handle.run_id,
            transcript=transcript,
            artifact_paths=(artifact,),
            workspace_digest_before=record.workspace_digest_before,
            workspace_digest_after=after,
            redacted=True,
        )

    def cancel(self, handle: LocalStudioRunHandle) -> LocalStudioRunState:
        record = self._record(handle)
        self._request_json(
            "POST",
            "/api/agent/abort",
            {"sessionId": handle.session_id},
        )
        record.cancelled = True
        return _cancelled_state(handle)

    def _record(self, handle: LocalStudioRunHandle) -> _HttpRunRecord:
        try:
            return self._runs[handle.run_id]
        except KeyError as exc:
            raise RuntimeError("Local Studio run handle is unknown") from exc

    def _record_events(self, record: _HttpRunRecord, raw_events: object) -> None:
        if not isinstance(raw_events, list):
            return
        for raw in raw_events:
            if not isinstance(raw, Mapping):
                continue
            seq = raw.get("seq")
            if not isinstance(seq, int) or seq <= record.event_cursor:
                continue
            event = raw.get("event")
            if not isinstance(event, Mapping):
                continue
            entry = {
                "seq": seq,
                "timestamp": str(raw.get("timestamp") or ""),
                "event": dict(event),
            }
            record.events.append(entry)
            record.event_cursor = seq
            event_type = str(event.get("type") or "")
            if event_type in {"agent_end", "agent_settled"}:
                record.settled = True
            if event_type == "error" or any(
                event.get(key) is not None
                for key in ("error", "errorMessage", "aborted", "cancelled", "canceled", "failed")
            ):
                record.failed = True

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with _open_no_redirect(request, timeout=self.request_timeout) as response:
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4_096).decode("utf-8", errors="replace")
            raise RuntimeError(
                redact_secrets(f"Local Studio HTTP {exc.code}: {detail[:500]}")
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(redact_secrets(f"Local Studio request failed: {exc}")) from exc
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise RuntimeError("Local Studio response exceeded the size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Studio returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("Local Studio returned a non-object JSON response")
        return decoded


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Local Studio response is missing {key}")
    return value.strip()


def _validate_endpoint(endpoint: str) -> str:
    """Validate and normalize the configured Local Studio base URL."""
    normalized = endpoint.rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local Studio endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Local Studio endpoint must not include URL credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Local Studio endpoint must not include a query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Local Studio endpoint must use a valid port") from exc
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("Local Studio endpoint must use a valid port")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Local Studio endpoint must include a hostname")
    if parsed.scheme == "http" and not _is_loopback_hostname(hostname):
        raise ValueError("non-loopback Local Studio endpoints must use HTTPS")
    return normalized


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _cancelled_state(handle: LocalStudioRunHandle) -> LocalStudioRunState:
    return LocalStudioRunState(
        run_id=handle.run_id,
        status=LocalStudioRunStatus.CANCELLED,
        summary="Local Studio run cancelled by Harness",
    )


def _safe_run_component(run_id: str) -> str:
    if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id:
        raise ValueError("Local Studio run_id must be one safe path component")
    return run_id


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        new: str,
    ) -> urllib.request.Request | None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


def _open_no_redirect(request: urllib.request.Request, *, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


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
        try:
            handle = self.transport.submit(spec)
            state = self.transport.poll(handle)
        except Exception as exc:
            return _transport_failure(exc)
        deadline = time.monotonic() + self.timeout
        while state.status not in TERMINAL_LOCAL_STUDIO_STATUSES:
            if self.cancel_requested():
                try:
                    state = self.transport.cancel(handle)
                except Exception as exc:
                    return _transport_failure(exc)
                break
            if time.monotonic() >= deadline:
                try:
                    state = self.transport.cancel(handle)
                except Exception as exc:
                    return _transport_failure(exc)
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
            try:
                state = self.transport.poll(handle)
            except Exception as exc:
                return _transport_failure(exc)

        if state.run_id != handle.run_id:
            return WorkerResult(
                success=False,
                summary="Local Studio returned state for the wrong run",
                stderr="run identity mismatch",
                returncode=1,
            )

        try:
            evidence = self.transport.collect(handle)
        except Exception as exc:
            return _transport_failure(exc)
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


def _transport_failure(error: Exception) -> WorkerResult:
    message = redact_secrets(f"Local Studio transport failed: {error}")
    return WorkerResult(
        success=False,
        summary=message,
        stderr=message,
        returncode=1,
    )
