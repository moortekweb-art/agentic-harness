from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agentic_harness.adapters.model_agent import (
    EmbeddedModelAgent,
    OpenAICompatibleProvider,
    ProviderResponse,
    _atomic_write,
)
from agentic_harness.core.config import HarnessConfig, load_config
from agentic_harness.core.errors import ConfigError
from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.factory import review_criteria_from_config
from agentic_harness.core.providers import ProviderProfile, resolve_api_key
from agentic_harness.core.review import command_passes
from agentic_harness.core.safety import git_changes
from agentic_harness.core.state import Goal
from agentic_harness.cli import build_supervisor


class SequenceProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> ProviderResponse:
        self.requests.append(messages)
        return ProviderResponse(content=self.responses.pop(0), usage={"total_tokens": 7})


def test_atomic_write_retries_a_transient_replace_denial(tmp_path, monkeypatch) -> None:
    target = tmp_path / "value.txt"
    target.write_text("before", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path, destination):
        nonlocal attempts
        if destination == target and attempts == 0:
            attempts += 1
            raise PermissionError("target is briefly held by another reader")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    _atomic_write(target, "after")

    assert target.read_text(encoding="utf-8") == "after"
    assert attempts == 1


def _goal(tmp_path: Path) -> Goal:
    goal = Goal(objective="Update greeting and prove it works")
    goal.metadata["safety"] = {
        "allowed_paths": ["src"],
        "checks": [
            {
                "id": "check-1",
                "label": "Greeting check",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('src/greeting.txt').read_text() == 'hello'",
                ],
            }
        ],
        "path_enforcement": True,
        "secret_env_names": [],
        "preexisting_changes": [],
    }
    goal.metadata["autonomy"] = {
        "strict_completion": True,
        "cycle": 0,
        "plan": [],
        "requirements": [],
        "current_subgoal": "inspect the greeting",
        "checkpoint": "goal_started",
    }
    goal.metadata["continuation_instruction"] = "Complete the goal and report evidence."
    return goal


def test_model_agent_executes_bounded_tools_and_returns_strict_outcome(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeting.txt").write_text("hi", encoding="utf-8")
    provider = SequenceProvider(
        [
            {
                "action": "read_file",
                "arguments": {"path": "src/greeting.txt"},
                "plan": [{"step": "Update greeting", "status": "in_progress"}],
                "requirements": [],
                "current_subgoal": "inspect the greeting",
                "checkpoint": "goal_started",
            },
            {
                "action": "replace_text",
                "arguments": {
                    "path": "src/greeting.txt",
                    "old": "hi",
                    "new": "hello",
                    "expected_sha256": hashlib.sha256(b"hi").hexdigest(),
                },
                "plan": [{"step": "Update greeting", "status": "in_progress"}],
                "requirements": [
                    {
                        "id": "R1",
                        "text": "Greeting is hello",
                        "status": "in_progress",
                        "evidence": [],
                    }
                ],
                "current_subgoal": "update the greeting",
                "checkpoint": "greeting_inspected",
            },
            {
                "action": "run_check",
                "arguments": {"check_id": "check-1"},
                "plan": [{"step": "Update greeting", "status": "completed"}],
                "requirements": [
                    {
                        "id": "R1",
                        "text": "Greeting is hello",
                        "status": "in_progress",
                        "evidence": ["src/greeting.txt updated"],
                    }
                ],
                "current_subgoal": "verify the greeting",
                "checkpoint": "greeting_updated",
            },
            {
                "action": "report_outcome",
                "arguments": {
                    "status": "complete",
                    "summary": "Updated and verified the greeting.",
                    "plan": [{"step": "Update greeting", "status": "completed"}],
                    "requirements": [
                        {
                            "id": "R1",
                            "text": "Greeting is hello",
                            "status": "satisfied",
                            "evidence": ["event:3"],
                        }
                    ],
                    "current_subgoal": "final verification complete",
                    "checkpoint": "verified",
                    "blockers": [],
                },
            },
        ]
    )
    worker = EmbeddedModelAgent(
        project_dir=tmp_path,
        provider=provider,
        model="user-chosen-model",
        max_steps=5,
    )

    goal = _goal(tmp_path)
    result = worker.run(goal)

    assert result.success is True
    assert result.outcome["status"] == "complete"
    assert result.outcome["checkpoint"] == "verified"
    assert result.outcome["verification"][0]["passed"] is True
    assert result.outcome["usage"]["total_tokens"] == 28
    assert (source / "greeting.txt").read_text(encoding="utf-8") == "hello"
    assert len(provider.requests) == 4
    events = TaskEventStore(tmp_path, goal.id).read()
    assert [event["tool"]["name"] for event in events] == [
        "read_file",
        "replace_text",
        "run_check",
        "report_outcome",
    ]
    assert all("arguments" not in event for event in events)


def test_model_agent_blocks_write_outside_allowed_paths(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    protected = tmp_path / "protected.txt"
    protected.write_text("keep", encoding="utf-8")
    provider = SequenceProvider(
        [
            {
                "action": "replace_text",
                "arguments": {"path": "protected.txt", "old": "keep", "new": "changed"},
                "current_subgoal": "change a protected file",
                "checkpoint": "attempted_out_of_scope_write",
            }
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(_goal(tmp_path))

    assert result.success is True
    assert result.outcome["status"] == "blocked"
    assert "outside the allowed paths" in result.outcome["summary"]
    assert protected.read_text(encoding="utf-8") == "keep"


def test_model_agent_never_offers_arbitrary_shell_tool(tmp_path) -> None:
    provider = SequenceProvider(
        [
            {
                "action": "run_command",
                "arguments": {"argv": ["sh", "-c", "touch escaped"]},
            }
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(_goal(tmp_path))

    assert result.outcome["status"] == "blocked"
    assert "unsupported tool" in result.outcome["summary"]
    assert not (tmp_path / "escaped").exists()


def test_model_agent_requires_latest_file_hash_before_replacement(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = source / "greeting.txt"
    target.write_text("keep", encoding="utf-8")
    provider = SequenceProvider(
        [
            {
                "action": "replace_text",
                "arguments": {"path": "src/greeting.txt", "old": "keep", "new": "changed"},
            }
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(_goal(tmp_path))

    assert result.outcome["status"] == "blocked"
    assert "expected_sha256" in result.outcome["summary"]
    assert target.read_text(encoding="utf-8") == "keep"


def test_configured_check_does_not_inherit_provider_secret_environment(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MODEL_SECRET", "must-not-reach-check")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "also-must-not-reach-check")
    goal = _goal(tmp_path)
    goal.metadata["safety"] = {
        "allowed_paths": [],
        "secret_env_names": ["MODEL_SECRET"],
        "checks": [
            {
                "id": "check-1",
                "label": "Secret isolation check",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import os; "
                        "assert 'MODEL_SECRET' not in os.environ; "
                        "assert 'UNRELATED_API_TOKEN' not in os.environ"
                    ),
                ],
            }
        ],
    }
    provider = SequenceProvider(
        [
            {"action": "run_check", "arguments": {"check_id": "check-1"}},
            {
                "action": "report_outcome",
                "arguments": {
                    "status": "complete",
                    "summary": "Secret isolation verified.",
                    "plan": [{"step": "Verify isolation", "status": "completed"}],
                    "requirements": [
                        {
                            "id": "R1",
                            "status": "satisfied",
                            "evidence": ["event:1"],
                        }
                    ],
                    "current_subgoal": "verification complete",
                    "checkpoint": "verified",
                    "blockers": [],
                },
            },
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(goal)

    assert result.outcome["status"] == "complete"
    assert result.outcome["verification"][0]["passed"] is True
    assert "must-not-reach-check" not in str(provider.requests)
    assert "also-must-not-reach-check" not in str(provider.requests)


def test_independent_review_check_does_not_inherit_process_secrets(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", "must-not-reach-review")
    monkeypatch.setenv("UNRELATED_API_TOKEN", "also-must-not-reach-review")
    criterion = command_passes(
        [
            sys.executable,
            "-c",
                (
                    "import os; "
                    "assert 'HOME' not in os.environ; "
                    "assert 'UNRELATED_API_TOKEN' not in os.environ"
                ),
            ],
            cwd=tmp_path,
            secret_env_names=["HOME"],
        )

    passed, _message = criterion.check(Goal(objective="verify secret isolation"))

    assert passed is True


def test_factory_removes_provider_key_from_independent_review(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", "must-not-reach-review")
    config = HarnessConfig(
        project_dir=tmp_path,
        llm_api_key_env="HOME",
        review_command=[
            sys.executable,
            "-c",
            "import os; assert 'HOME' not in os.environ",
        ],
    )

    criteria = review_criteria_from_config(config, tmp_path)
    passed, _message = criteria[0].check(Goal(objective="verify secret isolation"))

    assert passed is True


@pytest.mark.parametrize(
    "name",
    [
        ".env.local",
        ".env.production",
        ".envrc",
        ".netrc",
        "client_secret.json",
        "client_secret_desktop.json",
        "token.json",
        "signing.jks",
    ],
)
def test_model_agent_blocks_common_secret_file_variants(tmp_path, name: str) -> None:
    target = tmp_path / name
    target.write_text("API_KEY=must-not-leave", encoding="utf-8")
    provider = SequenceProvider(
        [{"action": "read_file", "arguments": {"path": name}}]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")
    goal = _goal(tmp_path)
    goal.metadata["safety"]["allowed_paths"] = []

    result = worker.run(goal)

    assert result.outcome["status"] == "blocked"
    assert "protected path" in result.outcome["summary"]


def test_git_diff_excludes_protected_file_contents(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    secret = "opaque-env-secret-must-not-reach-provider"
    (tmp_path / ".env").write_text("VALUE=before\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env", "visible.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text(f"VALUE={secret}\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible-after\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            {"action": "git_diff", "arguments": {}},
            {
                "action": "report_outcome",
                "arguments": {
                    "status": "blocked",
                    "summary": "Diff inspected safely.",
                    "plan": [],
                    "requirements": [],
                    "current_subgoal": "finish",
                    "checkpoint": "diff_inspected",
                    "blockers": ["fixture stops here"],
                },
            },
        ]
    )
    goal = _goal(tmp_path)
    goal.metadata["safety"]["allowed_paths"] = []
    goal.metadata["safety"]["preexisting_changes"] = [".env", "visible.txt"]

    result = EmbeddedModelAgent(
        project_dir=tmp_path,
        provider=provider,
        model="model",
        max_steps=2,
    ).run(goal)

    requests = json.dumps(provider.requests)
    assert result.outcome["status"] == "blocked"
    assert secret not in requests
    assert "visible-after" in requests
    assert "must-not-leave" not in str(provider.requests)


def test_identical_tool_observations_have_a_stable_progress_token(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeting.txt").write_text("same", encoding="utf-8")

    def run_once() -> str:
        provider = SequenceProvider(
            [{"action": "read_file", "arguments": {"path": "src/greeting.txt"}}]
        )
        worker = EmbeddedModelAgent(
            project_dir=tmp_path,
            provider=provider,
            model="model",
            max_steps=1,
        )
        return str(worker.run(_goal(tmp_path)).outcome["progress_token"])

    assert run_once() == run_once()


def test_read_then_replace_preserves_crlf_and_uses_raw_byte_hash(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = source / "greeting.txt"
    target.write_bytes(b"hello\r\nworld\r\n")
    provider = SequenceProvider(
        [
            {"action": "read_file", "arguments": {"path": "src/greeting.txt"}},
            {
                "action": "replace_text",
                "arguments": {
                    "path": "src/greeting.txt",
                    "old": "world",
                    "new": "there",
                    "expected_sha256": hashlib.sha256(b"hello\r\nworld\r\n").hexdigest(),
                },
            },
        ]
    )
    worker = EmbeddedModelAgent(
        project_dir=tmp_path,
        provider=provider,
        model="model",
        max_steps=2,
    )

    result = worker.run(_goal(tmp_path))

    assert result.success is True
    assert target.read_bytes() == b"hello\r\nthere\r\n"
    observations = [
        message["content"]
        for message in provider.requests[1]
        if message["content"].startswith("TOOL_OBSERVATION=")
    ]
    assert any("\\r\\n" in observation for observation in observations)


def test_model_agent_checks_cancellation_between_provider_and_tool_steps(tmp_path) -> None:
    cancelled = {"value": False}

    class CancellingProvider:
        calls = 0

        def complete(self, messages):
            self.calls += 1
            cancelled["value"] = True
            return ProviderResponse(
                content={"action": "create_file", "arguments": {"path": "src/late.txt", "content": "late"}}
            )

    provider = CancellingProvider()
    worker = EmbeddedModelAgent(
        project_dir=tmp_path,
        provider=provider,
        model="model",
        cancel_requested=lambda: cancelled["value"],
    )

    result = worker.run(_goal(tmp_path))

    assert result.success is False
    assert "stopped" in result.summary.lower()
    assert provider.calls == 1
    assert not (tmp_path / "src" / "late.txt").exists()


def test_model_completion_rejects_invented_requirement_evidence(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "greeting.txt").write_text("hello", encoding="utf-8")
    provider = SequenceProvider(
        [
            {"action": "read_file", "arguments": {"path": "src/greeting.txt"}},
            {
                "action": "report_outcome",
                "arguments": {
                    "status": "complete",
                    "summary": "Claims completion.",
                    "plan": [{"step": "Inspect", "status": "completed"}],
                    "requirements": [
                        {
                            "id": "R1",
                            "status": "satisfied",
                            "evidence": ["I definitely checked it"],
                        }
                    ],
                    "current_subgoal": "done",
                    "checkpoint": "done",
                    "blockers": [],
                },
            },
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(_goal(tmp_path))

    assert result.success is False
    assert "evidence" in result.summary.lower()


def test_model_agent_preserves_preexisting_changes_without_explicit_scope(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = source / "owned-by-user.txt"
    target.write_text("user work", encoding="utf-8")
    goal = _goal(tmp_path)
    goal.metadata["safety"] = {
        "allowed_paths": [],
        "preexisting_changes": ["src/owned-by-user.txt"],
        "checks": [],
    }
    provider = SequenceProvider(
        [
            {
                "action": "replace_text",
                "arguments": {
                    "path": "src/owned-by-user.txt",
                    "old": "user work",
                    "new": "agent work",
                    "expected_sha256": hashlib.sha256(b"user work").hexdigest(),
                },
            }
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(goal)

    assert result.outcome["status"] == "blocked"
    assert "pre-existing change" in result.outcome["summary"]
    assert target.read_text(encoding="utf-8") == "user work"


def test_model_agent_write_fails_closed_without_safety_metadata(tmp_path) -> None:
    target = tmp_path / "dirty.txt"
    target.write_text("user work", encoding="utf-8")
    provider = SequenceProvider(
        [
            {
                "action": "replace_text",
                "arguments": {
                    "path": "dirty.txt",
                    "old": "user work",
                    "new": "agent work",
                    "expected_sha256": hashlib.sha256(b"user work").hexdigest(),
                },
            }
        ]
    )
    worker = EmbeddedModelAgent(project_dir=tmp_path, provider=provider, model="model")

    result = worker.run(Goal(objective="do not overwrite unowned work"))

    assert result.outcome["status"] == "blocked"
    assert "safety metadata" in result.outcome["summary"]
    assert target.read_text(encoding="utf-8") == "user work"


def test_model_agent_scrubs_exact_provider_key_before_tools_and_outcomes(tmp_path) -> None:
    secret = "opaque-provider-credential-123456789"
    (tmp_path / "src").mkdir()
    provider = SequenceProvider(
        [
            {
                "action": "create_file",
                "arguments": {"path": "src/reflection.txt", "content": f"Bearer {secret}"},
            },
            {
                "action": "report_outcome",
                "arguments": {
                    "status": "blocked",
                    "summary": f"Provider reflected Bearer {secret}",
                    "plan": [],
                    "requirements": [],
                    "current_subgoal": "credential isolation",
                    "checkpoint": "blocked",
                    "blockers": [f"Bearer {secret}"],
                },
            },
        ]
    )
    worker = EmbeddedModelAgent(
        project_dir=tmp_path,
        provider=provider,
        model="model",
        secret_values=[secret],
    )

    result = worker.run(_goal(tmp_path))

    assert secret not in (tmp_path / "src" / "reflection.txt").read_text(encoding="utf-8")
    assert secret not in json.dumps(result.outcome)
    assert secret not in result.summary


def test_git_changes_preserves_exact_newline_and_quote_paths(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    names = ["space name.txt"] if os.name == "nt" else ['quote"name.txt', "line\nbreak.txt"]
    for name in names:
        (tmp_path / name).write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", *names], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    for name in names:
        (tmp_path / name).write_text("after\n", encoding="utf-8")

    assert set(git_changes(tmp_path)) == set(names)


def test_model_agent_config_uses_environment_secret_reference(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        """
version: 1
worker: model_agent
llm:
  endpoint: https://api.example.test/v1/chat/completions
  model: chosen-model
  api_key_env: EXAMPLE_MODEL_KEY
  max_steps: 12
  remote_data_confirmed: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXAMPLE_MODEL_KEY", "test-secret-value")

    config = load_config(tmp_path)

    assert config.worker == "model_agent"
    assert config.llm_api_key_env == "EXAMPLE_MODEL_KEY"
    assert config.llm_max_steps == 12
    assert resolve_api_key(config.llm_api_key_env) == "test-secret-value"
    assert "test-secret-value" not in config.config_path.read_text(encoding="utf-8")


def test_config_loads_whole_goal_resource_budgets(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        """
version: 1
worker: model_agent
llm:
  endpoint: http://127.0.0.1:8000/v1/chat/completions
  model: local-model
autonomy:
  max_cycles: 12
  max_elapsed_seconds: 900
  max_total_tokens: 42000
  max_provider_calls: 50
  max_tool_calls: 120
review_command: [python, -c, "print('ok')"]
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.goal_max_cycles == 12
    assert config.goal_max_elapsed_seconds == 900
    assert config.goal_max_total_tokens == 42000
    assert config.goal_max_provider_calls == 50
    assert config.goal_max_tool_calls == 120


def test_model_agent_config_rejects_plaintext_api_key(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        """
version: 1
worker: model_agent
llm:
  endpoint: https://api.example.test/v1/chat/completions
  model: chosen-model
  api_key: do-not-save-this
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="api_key_env"):
        load_config(tmp_path)


def test_model_agent_config_requires_persisted_remote_data_consent(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        """
version: 1
worker: model_agent
llm:
  endpoint: https://api.example.test/v1/chat/completions
  model: chosen-model
  api_key_env: MODEL_KEY
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="remote_data_confirmed"):
        load_config(tmp_path)


def test_build_supervisor_wires_embedded_model_agent(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        """
version: 1
worker: model_agent
llm:
  endpoint: https://api.example.test/v1/chat/completions
  model: chosen-model
  api_key_env: EXAMPLE_MODEL_KEY
  max_steps: 4
  remote_data_confirmed: true
review_command:
  - python
  - -c
  - "print('verified')"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXAMPLE_MODEL_KEY", "secret-value")

    supervisor = build_supervisor(tmp_path)

    assert isinstance(supervisor.worker, EmbeddedModelAgent)
    assert supervisor.worker.model == "chosen-model"
    assert supervisor.worker.max_steps == 4


def test_factory_never_applies_session_key_to_keyless_profile(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: model_agent",
                "llm:",
                "  endpoint: http://127.0.0.1:8000/v1/chat/completions",
                "  model: keyless-model",
                "review_command:",
                f"  - {sys.executable}",
                "  - -c",
                "  - \"print('verified')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = build_supervisor(tmp_path, api_key="stale-session-secret")

    assert isinstance(supervisor.worker, EmbeddedModelAgent)
    assert isinstance(supervisor.worker.provider, OpenAICompatibleProvider)
    assert supervisor.worker.provider.api_key == ""


def test_resolve_api_key_reports_missing_environment_variable(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_MODEL_KEY", raising=False)

    with pytest.raises(ConfigError, match="MISSING_MODEL_KEY"):
        resolve_api_key("MISSING_MODEL_KEY")


def test_provider_profile_rejects_insecure_public_cloud_endpoint() -> None:
    with pytest.raises(ConfigError, match="HTTPS"):
        ProviderProfile(
            endpoint="http://api.example.com/v1/chat/completions",
            model="chosen-model",
            api_key_env="MODEL_KEY",
        )


def test_provider_profile_rejects_credentials_in_endpoint_query() -> None:
    with pytest.raises(ConfigError, match="query"):
        ProviderProfile(
            endpoint="https://api.example.com/v1/chat/completions?api_key=secret",
            model="chosen-model",
        )


def test_provider_profile_allows_loopback_http_for_local_models() -> None:
    profile = ProviderProfile(
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="qwen3",
    )

    assert profile.data_location == "local"
    assert os.fspath(Path(profile.endpoint.split("://", 1)[0])) == "http"


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        import json

        return json.dumps(self.payload).encode("utf-8")


def test_openai_compatible_provider_uses_user_model_and_bearer_key(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request, *, timeout):
        import json

        captured["request"] = request
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"git_status","arguments":{}}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            }
        )

    monkeypatch.setattr("agentic_harness.adapters.model_agent._open_no_redirect", fake_urlopen)
    provider = OpenAICompatibleProvider(
        endpoint="https://api.example.test/v1/chat/completions",
        model="arbitrary-user-model",
        api_key="secret-value",
        timeout=17,
    )

    response = provider.complete([{"role": "user", "content": "work"}])

    assert response.content == {"action": "git_status", "arguments": {}}
    assert response.usage["total_tokens"] == 7
    assert captured["payload"]["model"] == "arbitrary-user-model"
    assert captured["request"].headers["Authorization"] == "Bearer secret-value"
    assert captured["timeout"] == 17


def test_openai_compatible_provider_omits_authorization_for_keyless_local_model(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        return FakeHTTPResponse(
            {"choices": [{"message": {"content": '{"action":"git_status"}'}}]}
        )

    monkeypatch.setattr("agentic_harness.adapters.model_agent._open_no_redirect", fake_urlopen)
    provider = OpenAICompatibleProvider(
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="qwen3",
    )

    provider.complete([{"role": "user", "content": "work"}])

    assert "Authorization" not in captured["request"].headers


def test_openai_compatible_provider_refuses_redirects_before_credentials_can_leak() -> None:
    calls = {"redirect": 0, "target": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.do_POST()

        def do_POST(self) -> None:
            if self.path == "/redirect":
                calls["redirect"] += 1
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            calls["target"] += 1
            payload = b'{"choices":[{"message":{"content":"{\\"action\\":\\"git_status\\"}"}}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = OpenAICompatibleProvider(
        endpoint=f"http://127.0.0.1:{server.server_port}/redirect",
        model="local-model",
        api_key="must-not-be-forwarded",
        retries=0,
    )
    try:
        with pytest.raises(RuntimeError, match="HTTP 302"):
            provider.complete([{"role": "user", "content": "work"}])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert calls == {"redirect": 1, "target": 0}
