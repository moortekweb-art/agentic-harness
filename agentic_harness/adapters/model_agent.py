"""Bounded tool-using agent for OpenAI-compatible local and cloud models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
import time
from typing import Any, Callable, Protocol
import urllib.error
import urllib.request

from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.safety import subprocess_environment
from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


MAX_FILE_BYTES = 512_000
MAX_TOOL_OUTPUT_CHARS = 20_000
MAX_PROVIDER_RESPONSE_BYTES = 2_000_000
PROTECTED_PARTS = {
    ".agentic-harness",
    ".git",
    ".hg",
    ".ssh",
    ".aws",
    ".azure",
    ".docker",
    ".gnupg",
    ".kube",
    ".terraform",
    "node_modules",
    "venv",
    ".venv",
}
PROTECTED_NAMES = {
    ".env",
    ".envrc",
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "_netrc",
    "application_default_credentials.json",
    "client_secret.json",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "service-account.json",
    "token.json",
    "tokens.json",
    "oauth_token.json",
    "auth_token.json",
}
@dataclass(frozen=True)
class ProviderResponse:
    content: dict[str, Any] | str
    usage: dict[str, int | float] = field(default_factory=dict)


class ModelProvider(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        """Return the next structured agent action."""


class OpenAICompatibleProvider:
    """Minimal, bounded chat-completions transport for user-selected models."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str = "",
        timeout: int = 120,
        retries: int = 1,
        retry_delay: float = 1.0,
    ) -> None:
        if timeout < 1:
            raise ValueError("timeout must be at least 1")
        if retries < 0:
            raise ValueError("retries must not be negative")
        if retry_delay < 0:
            raise ValueError("retry_delay must not be negative")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay

    def sensitive_values(self) -> tuple[str, ...]:
        """Return transport credentials for exact provider-output scrubbing."""
        return (self.api_key,) if self.api_key else ()

    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0,
            }
        ).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                self.endpoint,
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with _open_no_redirect(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
                if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ValueError("model provider response exceeded the size limit")
                payload = json.loads(raw.decode("utf-8"))
                content = _provider_message_content(payload)
                usage = _provider_usage(payload)
                return ProviderResponse(content=_parse_action(content), usage=usage)
            except (
                OSError,
                UnicodeDecodeError,
                ValueError,
                json.JSONDecodeError,
                urllib.error.HTTPError,
                urllib.error.URLError,
            ) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay)
        assert last_error is not None
        raise RuntimeError(_provider_error_message(last_error)) from last_error


class EmbeddedModelAgent:
    """Run a small, bounded model/tool loop inside one harness worker cycle.

    The model can inspect files, create or replace text within explicit path
    boundaries, inspect Git state, and run checks the operator supplied. It is
    intentionally not given arbitrary shell, delete, install, or network tools.
    """

    def __init__(
        self,
        *,
        project_dir: str | Path,
        provider: ModelProvider,
        model: str,
        max_steps: int = 8,
        check_timeout: int = 120,
        cancel_requested: Callable[[], bool] | None = None,
        secret_values: Iterable[str] = (),
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if check_timeout < 1:
            raise ValueError("check_timeout must be at least 1")
        self.project_dir = Path(project_dir).resolve()
        self.provider = provider
        self.model = model
        self.max_steps = max_steps
        self.check_timeout = check_timeout
        self.cancel_requested = cancel_requested or (lambda: False)
        configured_secrets = list(secret_values)
        provider_secrets = getattr(provider, "sensitive_values", None)
        if callable(provider_secrets):
            configured_secrets.extend(provider_secrets())
        self.secret_values = tuple(
            dict.fromkeys(
                value
                for value in configured_secrets
                if isinstance(value, str) and value
            )
        )

    def run(self, goal: Goal) -> WorkerResult:
        messages = self._initial_messages(goal)
        event_store = TaskEventStore(self.project_dir, goal.id)
        autonomy = goal.metadata.get("autonomy")
        cycle = int(autonomy.get("cycle") or 0) + 1 if isinstance(autonomy, dict) else 1
        events: list[dict[str, Any]] = []
        verification: list[dict[str, Any]] = []
        usage: dict[str, int | float] = {}
        progress_fingerprints: list[str] = []
        last_action: dict[str, Any] = {}
        try:
            for sequence in range(1, self.max_steps + 1):
                if self.cancel_requested():
                    return _cancelled_result(usage, events)
                response = self.provider.complete(messages)
                usage["provider_calls"] = usage.get("provider_calls", 0) + 1
                _merge_usage(usage, response.usage)
                if self.cancel_requested():
                    return _cancelled_result(usage, events)
                action = _sanitize_provider_value(
                    _parse_action(response.content),
                    self.secret_values,
                )
                last_action = action
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(action, sort_keys=True),
                    }
                )
                name = str(action.get("action") or "").strip()
                arguments = action.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                if name == "report_outcome":
                    valid_evidence_ids = {
                        str(event.get("evidence_id"))
                        for event in events
                        if isinstance(event.get("tool"), dict)
                        and event["tool"].get("status") == "passed"
                        and event.get("evidence_id")
                    }
                    outcome = _validated_outcome(
                        arguments,
                        valid_evidence_ids=valid_evidence_ids,
                    )
                    events.append(
                        self._persist_event(
                            event_store,
                            {
                                "phase": "checking",
                                "tool": "report_outcome",
                                "message": str(outcome["summary"]),
                                "passed": outcome["status"] == "complete",
                            },
                            cycle=cycle,
                            checkpoint=str(outcome.get("checkpoint") or ""),
                            kind="outcome_reported",
                        )
                    )
                    outcome["verification"] = list(verification)
                    outcome["events"] = list(events)
                    outcome["usage"] = dict(usage)
                    outcome["progress_token"] = _progress_token(progress_fingerprints)
                    return WorkerResult(
                        success=True,
                        summary=str(outcome["summary"]),
                        outcome=outcome,
                    )
                try:
                    if self.cancel_requested():
                        return _cancelled_result(usage, events)
                    observation, event = self._run_tool(
                        goal,
                        name,
                        arguments,
                        sequence=sequence,
                    )
                except (OSError, ValueError) as exc:
                    summary = redact_secrets(str(exc))
                    event = {
                        "sequence": sequence,
                        "phase": "blocked",
                        "tool": name or "unknown",
                        "message": summary,
                        "passed": False,
                    }
                    events.append(
                        self._persist_event(
                            event_store,
                            event,
                            cycle=cycle,
                            checkpoint=str(action.get("checkpoint") or "tool_blocked"),
                            kind="tool_blocked",
                        )
                    )
                    return WorkerResult(
                        success=True,
                        summary=summary,
                        outcome={
                            "status": "blocked",
                            "summary": summary,
                            "plan": action.get("plan") or [],
                            "requirements": action.get("requirements") or [],
                            "current_subgoal": str(
                                action.get("current_subgoal") or "resolve the safety blocker"
                            ),
                            "checkpoint": str(action.get("checkpoint") or "tool_blocked"),
                            "blockers": [summary],
                            "verification": list(verification),
                            "events": list(events),
                            "usage": dict(usage),
                            "progress_token": _progress_token(progress_fingerprints),
                        },
                    )
                raw_event = event
                event = self._persist_event(
                    event_store,
                    event,
                    cycle=cycle,
                    checkpoint=str(action.get("checkpoint") or ""),
                    kind="check_finished" if name == "run_check" else "tool_finished",
                )
                events.append(event)
                observation["evidence_id"] = event.get("evidence_id")
                progress_fingerprints.append(_tool_fingerprint(name, observation))
                if name == "run_check":
                    verification.append(
                        {
                            "id": str(raw_event.get("check_id") or ""),
                            "label": str(raw_event.get("label") or "Configured check"),
                            "passed": bool(raw_event.get("passed")),
                            "message": str(raw_event.get("message") or ""),
                        }
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": "TOOL_OBSERVATION=" + json.dumps(observation, sort_keys=True),
                    }
                )
                if self.cancel_requested():
                    return _cancelled_result(usage, events)
        except Exception as exc:
            summary = f"model provider request failed: {type(exc).__name__}: {exc}"
            return WorkerResult(
                success=False,
                summary=redact_secrets(summary),
                stderr=redact_secrets(str(exc)),
                returncode=1,
                outcome={"usage": dict(usage), "events": list(events)},
            )

        outcome = {
            "status": "progress",
            "summary": f"Completed {len(events)} bounded tool steps; more work remains.",
            "plan": last_action.get("plan") or [],
            "requirements": last_action.get("requirements") or [],
            "current_subgoal": str(last_action.get("current_subgoal") or "continue the task"),
            "checkpoint": str(last_action.get("checkpoint") or "tool_step_budget_reached"),
            "blockers": [],
            "verification": verification,
            "events": events,
            "usage": usage,
        }
        outcome["progress_token"] = _progress_token(progress_fingerprints)
        return WorkerResult(success=True, summary=str(outcome["summary"]), outcome=outcome)

    def _persist_event(
        self,
        store: TaskEventStore,
        event: dict[str, Any],
        *,
        cycle: int,
        checkpoint: str,
        kind: str,
    ) -> dict[str, Any]:
        phase = str(event.get("phase") or "acting")
        stage = "check" if phase == "checking" else "act"
        status = "passed" if event.get("passed") is not False else "blocked"
        try:
            return store.append(
                stage=stage,
                kind=kind,
                summary=str(event.get("message") or "Progress recorded"),
                tool_name=str(event.get("tool") or "tool"),
                tool_status=status,
                cycle=cycle,
                checkpoint=checkpoint,
            )
        except OSError:
            return dict(event)

    def _initial_messages(self, goal: Goal) -> list[dict[str, str]]:
        safety = _safety(goal)
        checks = [
            {"id": row.get("id"), "label": row.get("label")}
            for row in safety["checks"]
        ]
        system = {
            "role": "system",
            "content": "\n".join(
                [
                    "You are a bounded repository agent.",
                    "Return exactly one JSON object per response; no markdown or prose.",
                    "Allowed actions: list_files, read_file, search_files, create_file, ",
                    "replace_text, git_status, git_diff, run_check, report_outcome.",
                    "There is no arbitrary shell, delete, install, or network tool.",
                    "Read a file first, then pass its returned sha256 as expected_sha256 to replace_text.",
                    "For a tool action return action, arguments, plan, requirements, ",
                    "current_subgoal, and checkpoint.",
                    "Use report_outcome only when work is complete or genuinely blocked.",
                    "For each satisfied requirement, cite only evidence_id values returned by successful tools.",
                    "A complete outcome must include status=complete, summary, completed plan, ",
                    "satisfied requirements with evidence, current_subgoal, checkpoint, and blockers=[]",
                    f"Model identifier: {self.model}",
                    "Allowed paths: " + json.dumps(safety["allowed_paths"]),
                    "Configured checks: " + json.dumps(checks, sort_keys=True),
                ]
            ),
        }
        instruction = goal.metadata.get("continuation_instruction")
        objective = str(instruction) if isinstance(instruction, str) and instruction else goal.objective
        return [system, {"role": "user", "content": objective}]

    def _run_tool(
        self,
        goal: Goal,
        name: str,
        arguments: dict[str, Any],
        *,
        sequence: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if name == "list_files":
            path = self._path(goal, str(arguments.get("path") or "."), write=False)
            limit = _bounded_int(arguments.get("limit"), default=200, minimum=1, maximum=500)
            files = [
                item.relative_to(self.project_dir).as_posix()
                for item in sorted(path.rglob("*"))
                if item.is_file() and not item.is_symlink() and not _protected(item, self.project_dir)
            ][:limit]
            return {"files": files}, _event(sequence, name, f"Listed {len(files)} files", path=path)
        if name == "read_file":
            path = self._path(goal, _required_text(arguments, "path"), write=False)
            raw = path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError(f"file is larger than {MAX_FILE_BYTES} bytes: {_relative(path, self.project_dir)}")
            content = redact_secrets(raw.decode("utf-8"))
            lines = content.splitlines(keepends=True)
            start = _bounded_int(arguments.get("start_line"), default=1, minimum=1, maximum=max(1, len(lines)))
            end = _bounded_int(
                arguments.get("end_line"),
                default=min(len(lines), start + 399),
                minimum=start,
                maximum=max(start, len(lines)),
            )
            selected = "".join(lines[start - 1 : end])
            rel = _relative(path, self.project_dir)
            return {
                "path": rel,
                "start_line": start,
                "end_line": end,
                "content": selected,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }, _event(sequence, name, f"Read {rel}", path=path)
        if name == "search_files":
            root = self._path(goal, str(arguments.get("path") or "."), write=False)
            query = _required_text(arguments, "query")
            matches: list[dict[str, Any]] = []
            for path in sorted(root.rglob("*")):
                if len(matches) >= 100:
                    break
                if not path.is_file() or path.is_symlink() or _protected(path, self.project_dir):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if query in line:
                        matches.append(
                            {
                                "path": _relative(path, self.project_dir),
                                "line": number,
                                "text": redact_secrets(line)[:500],
                            }
                        )
                        if len(matches) >= 100:
                            break
            return {"matches": matches}, _event(sequence, name, f"Found {len(matches)} matches")
        if name == "create_file":
            path = self._path(goal, _required_text(arguments, "path"), write=True)
            if path.exists():
                raise ValueError(f"create_file refuses to overwrite existing file: {_relative(path, self.project_dir)}")
            content = _required_string(arguments, "content")
            _validate_content_size(content)
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
            rel = _relative(path, self.project_dir)
            return {"path": rel, "sha256": _sha256_text(content)}, _event(sequence, name, f"Created {rel}", path=path)
        if name == "replace_text":
            path = self._path(goal, _required_text(arguments, "path"), write=True)
            old = _required_string(arguments, "old")
            new = _required_string(arguments, "new")
            raw = path.read_bytes()
            content = raw.decode("utf-8")
            expected = _required_text(arguments, "expected_sha256")
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"file changed since inspection: {_relative(path, self.project_dir)}")
            if content.count(old) != 1:
                raise ValueError("replace_text requires old text to occur exactly once")
            updated = content.replace(old, new, 1)
            _validate_content_size(updated)
            _atomic_write(path, updated)
            rel = _relative(path, self.project_dir)
            return {"path": rel, "sha256": _sha256_text(updated)}, _event(sequence, name, f"Updated {rel}", path=path)
        if name == "git_status":
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.project_dir,
                env=subprocess_environment(_safety(goal)["secret_env_names"]),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = redact_secrets(proc.stdout)[:MAX_TOOL_OUTPUT_CHARS]
            return {"returncode": proc.returncode, "output": output}, _event(sequence, name, "Inspected workspace changes", passed=proc.returncode == 0)
        if name == "git_diff":
            allowed_paths = _safety(goal)["allowed_paths"]
            names = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-only",
                    "-z",
                    "--",
                    *allowed_paths,
                ],
                cwd=self.project_dir,
                env=subprocess_environment(_safety(goal)["secret_env_names"]),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if names.returncode != 0:
                detail = redact_secrets(os.fsdecode(names.stderr))[:MAX_TOOL_OUTPUT_CHARS]
                return {
                    "returncode": names.returncode,
                    "diff": detail,
                }, _event(sequence, name, "Git diff path discovery failed", passed=False)
            safe_paths: list[str] = []
            for raw_path in names.stdout.split(b"\0"):
                if not raw_path:
                    continue
                relative = os.fsdecode(raw_path)
                candidate = (self.project_dir / relative).resolve()
                try:
                    candidate.relative_to(self.project_dir)
                except ValueError:
                    continue
                if candidate.is_symlink() or _protected(candidate, self.project_dir):
                    continue
                if allowed_paths and not any(
                    _within(candidate, (self.project_dir / entry).resolve())
                    for entry in allowed_paths
                ):
                    continue
                safe_paths.append(relative)
                if len(safe_paths) >= 500:
                    break
            if not safe_paths:
                return {"returncode": 0, "diff": ""}, _event(
                    sequence,
                    name,
                    "No non-sensitive workspace diff was available",
                    passed=True,
                )
            proc = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--",
                    *safe_paths,
                ],
                cwd=self.project_dir,
                env=subprocess_environment(_safety(goal)["secret_env_names"]),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            output = redact_secrets(proc.stdout)[:MAX_TOOL_OUTPUT_CHARS]
            return {"returncode": proc.returncode, "diff": output}, _event(sequence, name, "Inspected the current diff", passed=proc.returncode == 0)
        if name == "run_check":
            check_id = _required_text(arguments, "check_id")
            check = next(
                (row for row in _safety(goal)["checks"] if row.get("id") == check_id),
                None,
            )
            if check is None:
                raise ValueError(f"unknown configured check: {check_id}")
            argv = check.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
                raise ValueError(f"configured check is invalid: {check_id}")
            proc = subprocess.run(
                argv,
                cwd=self.project_dir,
                env=subprocess_environment(_safety(goal)["secret_env_names"]),
                text=True,
                capture_output=True,
                timeout=self.check_timeout,
                check=False,
            )
            detail = redact_secrets(proc.stderr.strip() or proc.stdout.strip())
            message = (
                f"{check.get('label') or check_id} passed"
                if proc.returncode == 0
                else f"{check.get('label') or check_id} failed: {detail[:1000] or 'no output'}"
            )
            event = _event(sequence, name, message, passed=proc.returncode == 0)
            event["check_id"] = check_id
            event["label"] = str(check.get("label") or check_id)
            return {
                "check_id": check_id,
                "passed": proc.returncode == 0,
                "returncode": proc.returncode,
                "output": detail[:MAX_TOOL_OUTPUT_CHARS],
            }, event
        raise ValueError(f"unsupported tool: {name or 'missing action'}")

    def _path(self, goal: Goal, value: str, *, write: bool) -> Path:
        safety_metadata = goal.metadata.get("safety")
        if write and (
            not isinstance(safety_metadata, dict)
            or not isinstance(safety_metadata.get("allowed_paths"), list)
            or not isinstance(safety_metadata.get("preexisting_changes"), list)
        ):
            raise ValueError("write tools require complete goal safety metadata")
        rel = Path(value)
        if rel.is_absolute():
            raise ValueError("tool paths must be relative to the workspace")
        path = (self.project_dir / rel).resolve()
        try:
            path.relative_to(self.project_dir)
        except ValueError as exc:
            raise ValueError("tool path is outside the workspace") from exc
        if _protected(path, self.project_dir):
            raise ValueError(f"protected path is unavailable: {value}")
        allowed = _safety(goal)["allowed_paths"]
        if allowed and not any(_within(path, (self.project_dir / entry).resolve()) for entry in allowed):
            operation = "write" if write else "read"
            raise ValueError(f"{operation} is outside the allowed paths: {value}")
        rel_path = _relative(path, self.project_dir)
        preexisting = _safety(goal)["preexisting_changes"]
        explicitly_allowed = any(
            _within(path, (self.project_dir / entry).resolve()) for entry in allowed
        )
        if write and rel_path in preexisting and not explicitly_allowed:
            raise ValueError(
                f"refusing to overwrite a pre-existing change without explicit scope: {value}"
            )
        return path


def _safety(goal: Goal) -> dict[str, list[Any]]:
    raw = goal.metadata.get("safety")
    if not isinstance(raw, dict):
        raw = {}
    allowed = raw.get("allowed_paths")
    checks = raw.get("checks")
    secret_env_names = raw.get("secret_env_names")
    preexisting_changes = raw.get("preexisting_changes")
    return {
        "allowed_paths": [str(item) for item in allowed] if isinstance(allowed, list) else [],
        "checks": [item for item in checks if isinstance(item, dict)] if isinstance(checks, list) else [],
        "secret_env_names": [str(item) for item in secret_env_names]
        if isinstance(secret_env_names, list)
        else [],
        "preexisting_changes": [str(item) for item in preexisting_changes]
        if isinstance(preexisting_changes, list)
        else [],
    }


def _sanitize_provider_value(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        sanitized = redact_secrets(value)
        for secret in secret_values:
            sanitized = sanitized.replace(secret, "<redacted>")
        return sanitized
    if isinstance(value, list):
        return [_sanitize_provider_value(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {
            str(_sanitize_provider_value(key, secret_values)): _sanitize_provider_value(
                item,
                secret_values,
            )
            for key, item in value.items()
        }
    return value


def _parse_action(content: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(content, dict):
        return dict(content)
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    for marker in ("HARNESS_ACTION_JSON=", "HARNESS_RESULT_JSON="):
        if text.startswith(marker):
            text = text[len(marker) :].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


def _provider_message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("model provider returned a non-object response")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("model provider must return exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("model provider returned a malformed choice")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("model provider response is missing a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model provider response is missing JSON content")
    return content


def _provider_usage(payload: Any) -> dict[str, int | float]:
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return {}
    usage: dict[str, int | float] = {}
    for key, value in payload["usage"].items():
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool):
            usage[key] = value
    return usage


def _provider_error_message(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {401, 403}:
            return "model provider rejected the configured credential"
        if exc.code == 429:
            return "model provider rate limit was reached"
        return f"model provider returned HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "model provider is unreachable"
    return redact_secrets(str(exc))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _open_no_redirect(request: urllib.request.Request, *, timeout: int) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _validated_outcome(
    arguments: dict[str, Any],
    *,
    valid_evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    status = str(arguments.get("status") or "").strip().lower()
    if status not in {"complete", "progress", "blocked"}:
        raise ValueError("report_outcome status must be complete, progress, or blocked")
    summary = str(arguments.get("summary") or "").strip()
    if not summary:
        raise ValueError("report_outcome requires a summary")
    raw_requirements = arguments.get("requirements")
    requirements: list[Any] = raw_requirements if isinstance(raw_requirements, list) else []
    if status == "complete" and valid_evidence_ids is not None:
        for requirement in requirements:
            if not isinstance(requirement, dict):
                continue
            if str(requirement.get("status") or "").strip().lower() != "satisfied":
                continue
            evidence = requirement.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                continue
            invalid = [
                str(item)
                for item in evidence
                if not isinstance(item, str) or item not in valid_evidence_ids
            ]
            if invalid:
                raise ValueError(
                    "completion evidence must cite evidence_id values from successful tool events"
                )
    return {
        "status": status,
        "summary": summary,
        "plan": arguments.get("plan") if isinstance(arguments.get("plan"), list) else [],
        "requirements": requirements,
        "current_subgoal": str(arguments.get("current_subgoal") or "").strip(),
        "checkpoint": str(arguments.get("checkpoint") or "").strip(),
        "blockers": arguments.get("blockers") if isinstance(arguments.get("blockers"), list) else [],
    }


def _event(
    sequence: int,
    tool: str,
    message: str,
    *,
    path: Path | None = None,
    passed: bool | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "sequence": sequence,
        "phase": "acting" if tool != "run_check" else "checking",
        "tool": tool,
        "message": redact_secrets(message),
    }
    if path is not None:
        event["path"] = path.name
    if passed is not None:
        event["passed"] = passed
    return event


def _merge_usage(total: dict[str, int | float], update: dict[str, int | float]) -> None:
    for key, value in update.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def _tool_fingerprint(name: str, observation: dict[str, Any]) -> str:
    stable = {key: value for key, value in observation.items() if key != "evidence_id"}
    payload = {"action": name, "observation": stable}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _progress_token(fingerprints: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(fingerprints, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _cancelled_result(
    usage: dict[str, int | float],
    events: list[dict[str, Any]],
) -> WorkerResult:
    return WorkerResult(
        success=False,
        summary="Task stopped by user before the next model action.",
        stderr="task stopped by user",
        returncode=1,
        outcome={
            "status": "blocked",
            "summary": "Task stopped by user.",
            "blockers": ["task stopped by user"],
            "usage": dict(usage),
            "events": list(events),
        },
    )


def _protected(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    lowered = {part.lower() for part in rel.parts}
    dotenv_variant = any(
        part == ".env" or part == ".envrc" or part.startswith((".env.", ".env-"))
        for part in lowered
    )
    name = path.name.lower()
    oauth_variant = (
        name.startswith("client_secret") and name.endswith(".json")
    ) or (
        name.startswith(("oauth_token", "auth_token")) and name.endswith(".json")
    )
    return (
        bool(lowered & PROTECTED_PARTS)
        or dotenv_variant
        or oauth_variant
        or name in PROTECTED_NAMES
        or path.suffix.lower() in {".jks", ".key", ".keystore", ".pem", ".p12", ".pfx"}
    )


def _within(path: Path, allowed: Path) -> bool:
    return path == allowed or allowed in path.parents


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = default if value in (None, "") else int(value)
    return max(minimum, min(maximum, parsed))


def _validate_content_size(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"file content exceeds {MAX_FILE_BYTES} bytes")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode if path.exists() else None
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            tmp = Path(handle.name)
        if mode is not None:
            tmp.chmod(mode)
        tmp.replace(path)
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
