from __future__ import annotations

import json
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.adapters.model_agent import ProviderResponse
from agentic_harness.core.factory import build_supervisor
from agentic_harness.core.safety import goal_safety_metadata


def test_setup_detects_project_specific_check_without_assuming_pytest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()

    assert setup["configured"] is False
    assert setup["suggested_check"] == "npm test"
    assert setup["workspace"] == str(tmp_path)
    assert {option["key"] for option in setup["execution_options"]} == {
        "coding_agent",
        "local_model",
        "cloud_model",
    }


def test_model_setup_keeps_entered_api_key_in_memory_only(tmp_path: Path) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    result = backend.configure(
        {
            "execution": "cloud_model",
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "my-cloud-model",
            "api_key": "top-secret-api-key",
            "confirm_remote_data": True,
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    config_text = (tmp_path / ".agentic-harness" / "config.yml").read_text(
        encoding="utf-8"
    )
    public_setup = backend.setup()
    serialized = json.dumps({"result": result, "setup": public_setup})
    assert result["configured"] is True
    assert result["credential"]["source"] == "session"
    assert result["credential"]["configured"] is True
    assert public_setup["worker"]["credential_source"] == "session"
    assert "top-secret-api-key" not in config_text
    assert "top-secret-api-key" not in serialized
    assert "llm_credential_source: session" in config_text
    assert stat.S_IMODE((tmp_path / ".agentic-harness" / "config.yml").stat().st_mode) == 0o600


def test_model_setup_refuses_preexisting_config_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yml"
    outside.write_text("do not overwrite\n", encoding="utf-8")
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").symlink_to(outside)
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "local-model",
                "verification_command": f"{sys.executable} -c \"print('verified')\"",
            }
        )

    assert outside.read_text(encoding="utf-8") == "do not overwrite\n"


def test_session_only_cloud_key_must_be_reentered_after_restart(tmp_path: Path) -> None:
    first = EmbeddedExecutionBackend(tmp_path)
    first.configure(
        {
            "execution": "cloud_model",
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "my-cloud-model",
            "api_key": "temporary-secret",
            "confirm_remote_data": True,
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    restarted = EmbeddedExecutionBackend(tmp_path)

    assert restarted.readiness()["state"] == "credential_required"
    assert restarted.readiness()["can_start"] is False


def test_reentering_session_key_resumes_orphaned_goal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = EmbeddedExecutionBackend(tmp_path)
    first.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "local-model",
            "api_key": "temporary-secret",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    config = first._config()
    check = config.review_command
    supervisor = build_supervisor(tmp_path, api_key="temporary-secret")
    supervisor.start(
        "resume after the key is re-entered",
        metadata=goal_safety_metadata(
            tmp_path,
            allowed_paths=[],
            review_commands=[check],
            path_enforcement=True,
            secret_env_names=[],
            interface="gui",
        ),
    )
    restarted = EmbeddedExecutionBackend(tmp_path)
    started: list[list[list[str]]] = []
    monkeypatch.setattr(restarted, "_start_thread", lambda commands: started.append(commands))

    result = restarted.set_session_credential("replacement-secret")

    assert result == {"source": "session", "configured": True}
    assert started == [[check]]


def test_start_waits_for_atomic_provider_and_session_key_reconfiguration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "old-model",
            "api_key": "old-session-secret",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    original_write = backend._write_config
    config_written = threading.Event()
    release_write = threading.Event()

    def paused_write(payload):
        original_write(payload)
        config_written.set()
        assert release_write.wait(timeout=3)

    monkeypatch.setattr(backend, "_write_config", paused_write)
    monkeypatch.setattr(backend, "_drive", lambda review_commands: None)
    configure_done = threading.Event()
    start_done = threading.Event()

    def configure_keyless() -> None:
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:9/v1/chat/completions",
                "model": "new-keyless-model",
                "verification_command": f"{sys.executable} -c \"print('verified')\"",
            }
        )
        configure_done.set()

    def start_goal() -> None:
        backend.start({"objective": "start only after profile commit"})
        start_done.set()

    configure_thread = threading.Thread(target=configure_keyless)
    configure_thread.start()
    assert config_written.wait(timeout=3)
    start_thread = threading.Thread(target=start_goal)
    start_thread.start()
    time.sleep(0.05)
    assert not start_done.is_set()

    release_write.set()
    configure_thread.join(timeout=3)
    start_thread.join(timeout=3)

    assert configure_done.is_set()
    assert start_done.is_set()
    assert backend.api_key is None


def test_cloud_model_setup_requires_explicit_remote_data_confirmation(tmp_path: Path) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="leave this computer"):
        backend.configure(
            {
                "execution": "cloud_model",
                "endpoint": "https://api.example.test/v1/chat/completions",
                "model": "my-cloud-model",
                "api_key_env": "MODEL_API_KEY",
                "verification_command": "python -m pytest -q",
            }
        )


def test_local_model_setup_supports_keyless_loopback_and_arbitrary_model_id(
    tmp_path: Path,
) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    result = backend.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "Qwen/Qwen3-9B-Coder",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    assert result["provider"]["data_location"] == "local"
    assert result["provider"]["model"] == "Qwen/Qwen3-9B-Coder"
    assert result["credential"] == {"source": "none", "configured": True}


def test_setup_connection_test_proves_structured_model_response_without_echoing_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def complete(self, messages):
            captured["messages"] = messages
            return ProviderResponse(
                content={"action": "report_outcome", "arguments": {"status": "progress"}},
                usage={"total_tokens": 3},
            )

    monkeypatch.setattr(
        "agentic_harness.gui.backend.OpenAICompatibleProvider",
        FakeProvider,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    result = backend.test_connection(
        {
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "user-model",
            "api_key": "connection-test-secret",
        }
    )

    assert result == {
        "reachable": True,
        "structured_actions": True,
        "model": "user-model",
        "data_location": "cloud",
    }
    assert captured["api_key"] == "connection-test-secret"
    assert "connection-test-secret" not in json.dumps(result)
