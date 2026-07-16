from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from agentic_harness.gui import backend as gui_backend_module
from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.adapters.model_agent import ProviderResponse
from agentic_harness.core.config import load_config
from agentic_harness.core.factory import build_supervisor
from agentic_harness.core.providers import ProviderProfile
from agentic_harness.core.safety import (
    command_uses_windows_shell,
    format_command,
    goal_safety_metadata,
    resolve_command_executable,
    split_command,
)


def test_windows_command_split_preserves_backslashes_and_removes_outer_quotes() -> None:
    command = (
        '"C:\\Program Files\\Python\\python.exe" -c '
        '"from pathlib import Path; print(Path(\'result.txt\'))"'
    )

    assert split_command(command, windows=True) == [
        "C:\\Program Files\\Python\\python.exe",
        "-c",
        "from pathlib import Path; print(Path('result.txt'))",
    ]


def test_windows_command_format_and_split_round_trip() -> None:
    command = [
        "C:\\Program Files\\Python\\python.exe",
        "-c",
        'print("verified")',
    ]

    assert split_command(format_command(command, windows=True), windows=True) == command


def test_windows_command_resolution_uses_the_exact_discovered_shim(
    monkeypatch,
) -> None:
    resolved = r"C:\Users\Michael\AppData\Roaming\npm\codex.cmd"
    monkeypatch.setattr(
        "agentic_harness.core.safety.shutil.which",
        lambda executable: resolved if executable == "codex" else None,
    )

    command = resolve_command_executable(
        ["codex", "exec", "{objective}"],
        windows=True,
    )

    assert command == [resolved, "exec", "{objective}"]
    assert command_uses_windows_shell(command, windows=True) is True
    assert command_uses_windows_shell([r"C:\Tools\codex.exe"], windows=True) is False


def test_setup_detects_project_specific_check_without_assuming_pytest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()

    assert setup["configured"] is False
    assert setup["suggested_check"] == "npm test"
    assert setup["verification"] == {
        "mode": "automatic",
        "label": "Project tests (npm)",
        "technical_command": "npm test",
    }
    assert setup["management"] == {
        "mode": "workspace",
        "editable": True,
        "summary": "Settings for this project.",
    }
    assert setup["workspace"] == str(tmp_path)
    assert {option["key"] for option in setup["execution_options"]} == {
        "coding_agent",
        "local_model",
        "cloud_model",
    }


def test_unknown_project_does_not_claim_automatic_verification(tmp_path: Path) -> None:
    setup = EmbeddedExecutionBackend(tmp_path).setup()

    assert setup["suggested_check"] == ""
    assert setup["verification"] == {
        "mode": "setup_needed",
        "label": "No automatic project check found",
        "technical_command": "",
    }


def test_setup_reports_available_agents_without_exposing_executable_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    locations = {
        "codex": r"C:\Tools\Codex\codex.exe",
        "codewhale": "/opt/private/bin/codewhale",
    }
    looked_up: list[str] = []

    def find_agent(name: str) -> str | None:
        looked_up.append(name)
        return locations.get(name)

    monkeypatch.setattr("shutil.which", find_agent)

    setup = EmbeddedExecutionBackend(tmp_path).setup()
    options = {option["key"]: option for option in setup["execution_options"]}
    coding_agent = options["coding_agent"]

    assert all(isinstance(option["available"], bool) for option in options.values())
    assert all(isinstance(option["recommended"], bool) for option in options.values())
    assert coding_agent["available"] is True
    assert coding_agent["recommended"] is True
    assert coding_agent["recommended_agent"] == "codex"
    assert coding_agent["agents"] == [
        {"key": "codex", "label": "Codex", "available": True, "recommended": True},
        {
            "key": "codewhale",
            "label": "CodeWhale",
            "available": True,
            "recommended": False,
        },
        {
            "key": "opencode",
            "label": "OpenCode",
            "available": False,
            "recommended": False,
        },
        {"key": "aider", "label": "Aider", "available": False, "recommended": False},
    ]
    assert set(looked_up) == {"codex", "codewhale", "opencode", "aider"}
    serialized = json.dumps(setup)
    assert r"C:\Tools\Codex" not in serialized
    assert "/opt/private/bin" not in serialized


def test_setup_has_no_recommended_agent_when_none_are_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    setup = EmbeddedExecutionBackend(tmp_path).setup()
    coding_agent = next(
        option for option in setup["execution_options"] if option["key"] == "coding_agent"
    )

    assert coding_agent["available"] is False
    assert coding_agent["recommended"] is False
    assert coding_agent["recommended_agent"] == ""
    assert not any(agent["recommended"] for agent in coding_agent["agents"])


def test_coding_agent_setup_rejects_an_unavailable_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="codex is not available on PATH"):
        backend.configure(
            {
                "execution": "coding_agent",
                "agent": "codex",
                "verification_command": f"{sys.executable} -c \"print('verified')\"",
            }
        )

    assert not (tmp_path / ".agentic-harness" / "config.yml").exists()


def test_configured_coding_agent_keeps_its_public_label_after_reload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/private/tools/{name}" if name == "codewhale" else None,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "codewhale",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    assert EmbeddedExecutionBackend(tmp_path).setup()["worker"] == {
        "type": "coding_agent",
        "agent": "codewhale",
        "label": "CodeWhale",
    }


def test_configure_persists_the_resolved_coding_agent_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resolved = tmp_path / "private" / "codex"
    monkeypatch.setattr(
        "shutil.which",
        lambda name: str(resolved) if name == "codex" else None,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "codex",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    assert load_config(tmp_path).coding_agent_command[0] == str(resolved)


def test_configure_preserves_a_valid_existing_absolute_agent_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "private" / "codex"
    executable.parent.mkdir()
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    original_command = [str(executable), "custom", "{objective}"]
    (config_dir / "config.yml").write_text(
        json.dumps(
            {
                "version": 1,
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": original_command,
                },
                "review_command": [sys.executable, "-c", "print('verified')"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("shutil.which", lambda _name: None)
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()
    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "current",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    coding_agent = next(
        option for option in setup["execution_options"] if option["key"] == "coding_agent"
    )
    current = next(
        agent for agent in coding_agent["agents"] if agent["key"] == "current"
    )
    assert current["available"] is True
    assert setup["worker"] == {
        "type": "coding_agent",
        "agent": "current",
        "label": "Current configured agent",
    }
    assert load_config(tmp_path).coding_agent_command == original_command


def test_selecting_a_named_agent_explicitly_replaces_custom_argv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "private" / "codex"
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    original_command = [str(executable), "custom", "{objective}"]
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": original_command,
                },
                "review_command": [sys.executable, "-c", "print('verified')"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agentic_harness.gui.backend.resolve_executable",
        lambda name: str(executable) if name in {"codex", str(executable)} else None,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "codex",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )

    assert load_config(tmp_path).coding_agent_command == [
        str(executable),
        "exec",
        "--skip-git-repo-check",
        "{objective}",
    ]


def test_unknown_existing_coding_agent_config_keeps_a_generic_public_label(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker:",
                "  type: coding_agent",
                "  coding_agent_command:",
                "    - custom-agent",
                "    - run",
                "    - '{objective}'",
                "review_command:",
                f"  - {sys.executable}",
                "  - -c",
                "  - print('verified')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert EmbeddedExecutionBackend(tmp_path).setup()["worker"] == {
        "type": "coding_agent",
        "agent": "current",
        "label": "Current configured agent",
    }


def test_custom_coding_agent_has_a_safe_current_option_and_round_trips(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    original_command = ["private-custom-agent", "run", "--profile", "secret-profile", "{objective}"]
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": original_command,
                    "coding_agent_timeout": 90_001,
                    "coding_agent_transcript": "private/transcripts/{goal_id}.log",
                },
                "review_command": [sys.executable, "-c", "print('verified')"],
                "review_command_timeout": 4_001,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agentic_harness.gui.backend.resolve_executable",
        lambda executable: "/private/bin/custom-agent"
        if executable == "private-custom-agent"
        else None,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()
    coding_agent = next(
        option for option in setup["execution_options"] if option["key"] == "coding_agent"
    )
    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "current",
            "verification_command": f"{sys.executable} -c \"print('still verified')\"",
        }
    )

    assert setup["worker"] == {
        "type": "coding_agent",
        "agent": "current",
        "label": "Current configured agent",
    }
    assert coding_agent["recommended_agent"] == "current"
    assert {
        "key": "current",
        "label": "Current configured agent",
        "available": True,
        "recommended": True,
    } in coding_agent["agents"]
    assert "private-custom-agent" not in json.dumps(setup)
    assert "secret-profile" not in json.dumps(setup)
    assert "/private/bin" not in json.dumps(setup)
    config = load_config(tmp_path)
    assert config.coding_agent_command == original_command
    assert config.coding_agent_timeout == 90_001
    assert config.coding_agent_transcript == "private/transcripts/{goal_id}.log"
    assert config.review_command_timeout == 4_001


def test_model_setup_preserves_hidden_advanced_values_when_fields_are_absent(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "worker": "model_agent",
                "llm": {
                    "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                    "model": "local-model",
                    "max_steps": 75,
                    "timeout": 4_444,
                },
                "llm_retries": 101,
                "llm_retry_delay": 3_601.0,
                "review_command": [sys.executable, "-c", "print('verified')"],
                "review_command_timeout": 4_002,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "local-model",
            "verification_command": f"{sys.executable} -c \"print('still verified')\"",
        }
    )

    config = load_config(tmp_path)
    assert config.llm_max_steps == 75
    assert config.llm_timeout == 4_444
    assert config.llm_retries == 101
    assert config.llm_retry_delay == 3_601.0
    assert config.review_command_timeout == 4_002


@pytest.mark.parametrize(
    ("worker", "settings"),
    [
        ("shell", {"shell_command": [sys.executable, "-c", "print('work')"]}),
        ("tmux", {"tmux_command": "python worker.py"}),
        (
            "github_actions",
            {
                "github_owner": "owner",
                "github_repo": "repo",
                "github_workflow_id": "verify.yml",
            },
        ),
        (
            "local_llm",
            {
                "llm_endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "llm_model": "legacy-local-model",
            },
        ),
    ],
)
def test_setup_is_read_only_for_worker_types_the_gui_cannot_faithfully_edit(
    tmp_path: Path,
    worker: str,
    settings: dict[str, object],
) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "worker": worker,
                **settings,
                "review_command": [sys.executable, "-c", "print('verified')"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    setup = EmbeddedExecutionBackend(tmp_path).setup()

    assert setup["configured"] is True
    assert setup["editable"] is False
    assert setup["worker"]["type"] == worker


def test_read_only_worker_setup_rejects_a_destructive_gui_save(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    config_path = config_dir / "config.yml"
    original = yaml.safe_dump(
        {
            "version": 1,
            "worker": "shell",
            "shell_command": [sys.executable, "-c", "print('work')"],
            "review_command": [sys.executable, "-c", "print('verified')"],
        },
        sort_keys=False,
    )
    config_path.write_text(original, encoding="utf-8")
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="read-only"):
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "replacement-model",
                "verification_command": f"{sys.executable} -c \"print('verified')\"",
            }
        )

    assert config_path.read_text(encoding="utf-8") == original


def test_readiness_refuses_config_without_independent_verification_command(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker:",
                "  type: coding_agent",
                "  coding_agent_command:",
                "    - custom-agent",
                "    - run",
                "    - '{objective}'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    readiness = EmbeddedExecutionBackend(tmp_path).readiness()

    assert readiness["state"] == "verification_required"
    assert readiness["label"] == "Verification needed"
    assert readiness["can_start"] is False
    assert readiness["can_queue"] is False
    assert "independent verification command" in readiness["summary"]


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
    if os.name != "nt":
        assert (
            stat.S_IMODE((tmp_path / ".agentic-harness" / "config.yml").stat().st_mode)
            == 0o600
        )


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


def test_invalid_existing_config_is_reported_and_never_overwritten(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    config_path = config_dir / "config.yml"
    original = "version: [invalid\napi_key: sk-example-secret\n"
    config_path.write_text(original, encoding="utf-8")
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()

    assert setup["configured"] is False
    assert setup["editable"] is False
    assert setup["configuration_error"]["code"] == "invalid_existing_configuration"
    assert "sk-example-secret" not in json.dumps(setup)
    with pytest.raises(ValueError, match="was not changed"):
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "local-model",
                "verification_command": f"{sys.executable} -c \"print('verified')\"",
            }
        )
    assert config_path.read_text(encoding="utf-8") == original


def test_existing_configuration_directory_file_is_reported_read_only(tmp_path: Path) -> None:
    config_path = tmp_path / ".agentic-harness"
    config_path.write_text("not a directory\n", encoding="utf-8")
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()

    assert setup["configured"] is False
    assert setup["editable"] is False
    assert setup["configuration_error"] == {
        "code": "unsafe_configuration_path",
        "summary": "The existing configuration path is not a directory.",
    }
    with pytest.raises(ValueError, match="was not changed"):
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "local-model",
                "verification_command": "python -m pytest -q",
            }
        )
    assert config_path.read_text(encoding="utf-8") == "not a directory\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO paths require POSIX")
def test_existing_configuration_fifo_is_rejected_without_reading(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    config_path = config_dir / "config.yml"
    os.mkfifo(config_path)
    backend = EmbeddedExecutionBackend(tmp_path)
    observed: dict[str, object] = {}

    thread = threading.Thread(
        target=lambda: observed.update(setup=backend.setup()),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=1)

    assert thread.is_alive() is False
    setup = observed["setup"]
    assert isinstance(setup, dict)
    assert setup["editable"] is False
    assert setup["configuration_error"] == {
        "code": "unsafe_configuration_path",
        "summary": "The existing configuration file path is not a regular file.",
    }


def test_first_gui_save_preserves_existing_noop_review_gates(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: noop",
                "allow_noop_success: true",
                "review_command: [python, -m, pytest, -q]",
                "review_artifact: reports/result.json",
                "review_file_changed: src/result.py",
                "review_git_clean: true",
                "goal_max_cycles: 17",
                "",
            ]
        ),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()
    assert setup["configured"] is False
    assert setup["editable"] is True
    assert setup["verification_command"] == "python -m pytest -q"
    assert setup["limits"]["max_cycles"] == 17

    backend.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "local-model",
        }
    )

    config = load_config(tmp_path)
    assert config.allow_noop_success is True
    assert config.review_artifact == "reports/result.json"
    assert config.review_file_changed == "src/result.py"
    assert config.review_git_clean is True
    assert config.review_command == ["python", "-m", "pytest", "-q"]
    assert config.goal_max_cycles == 17


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_cycles", {}, "whole number"),
        ("max_elapsed_seconds", True, "whole number"),
        ("retries", 1.5, "whole number"),
        ("retry_delay", float("inf"), "finite number"),
    ],
)
def test_gui_setup_rejects_malformed_numeric_settings(
    tmp_path: Path,
    field,
    value,
    message,
) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match=message):
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "local-model",
                "verification_command": "python -m pytest -q",
                field: value,
            }
        )

    assert not (tmp_path / ".agentic-harness" / "config.yml").exists()


def test_unsupported_existing_config_version_is_read_only(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    config_path = config_dir / "config.yml"
    original = "version: 99\nworker: noop\n"
    config_path.write_text(original, encoding="utf-8")
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()

    assert setup["editable"] is False
    assert "unsupported config version" in setup["configuration_error"]["summary"]
    with pytest.raises(ValueError, match="was not changed"):
        backend.configure(
            {
                "execution": "coding_agent",
                "agent": "codex",
                "verification_command": "python -m pytest -q",
            }
        )
    assert config_path.read_text(encoding="utf-8") == original


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


def test_editing_model_limits_preserves_the_existing_session_credential(
    tmp_path: Path,
) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)
    body = {
        "execution": "cloud_model",
        "endpoint": "https://api.example.test/v1/chat/completions",
        "model": "my-cloud-model",
        "confirm_remote_data": True,
        "verification_command": f"{sys.executable} -c \"print('verified')\"",
    }
    backend.configure(
        {
            **body,
            "api_key": "temporary-secret",
            "max_total_tokens": 12_345,
            "max_provider_calls": 17,
            "max_tool_calls": 89,
        }
    )

    backend.configure({**body, "max_cycles": 12})

    config = load_config(tmp_path)
    assert config.llm_credential_source == "session"
    assert backend.api_key == "temporary-secret"
    assert config.goal_max_total_tokens == 12_345
    assert config.goal_max_provider_calls == 17
    assert config.goal_max_tool_calls == 89
    assert backend.setup()["credential"] == {
        "source": "session",
        "configured": True,
    }
    assert backend.readiness()["state"] == "connection_test_required"


def test_editing_after_restart_keeps_missing_session_key_explicit(
    tmp_path: Path,
) -> None:
    first = EmbeddedExecutionBackend(tmp_path)
    body = {
        "execution": "cloud_model",
        "endpoint": "https://api.example.test/v1/chat/completions",
        "model": "my-cloud-model",
        "confirm_remote_data": True,
        "verification_command": f"{sys.executable} -c \"print('verified')\"",
    }
    first.configure({**body, "api_key": "temporary-secret"})
    restarted = EmbeddedExecutionBackend(tmp_path)

    restarted.configure({**body, "max_cycles": 12})

    assert load_config(tmp_path).llm_credential_source == "session"
    assert restarted.api_key is None
    assert restarted.readiness()["state"] == "credential_required"


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
    monkeypatch.setattr(
        restarted,
        "_start_thread",
        lambda commands, policy: started.append(commands),
    )
    with pytest.raises(ValueError, match="Test this AI connection"):
        restarted.set_session_credential("replacement-secret")
    profile = ProviderProfile(
        endpoint=config.llm_endpoint,
        model=config.llm_model,
        api_key_env=config.llm_api_key_env,
    )
    restarted._execution_validation = {
        "verified": True,
        "kind": "model_agent",
        "credential_source": "session",
        "fingerprint": gui_backend_module._model_connection_fingerprint(
            profile,
            credential_source="session",
            credential_value="replacement-secret",
        ),
    }

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
    monkeypatch.setattr(backend, "_drive", lambda review_commands, policy: None)
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
                content={
                    "action": "report_outcome",
                    "arguments": {
                        "status": "progress",
                        "summary": "Connection test passed.",
                    },
                },
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
        "verified": True,
        "model": "user-model",
        "data_location": "cloud",
        "summary": "The AI connection and structured actions are working.",
    }
    assert captured["api_key"] == "connection-test-secret"
    assert "connection-test-secret" not in json.dumps(result)

    backend.configure(
        {
            "execution": "cloud_model",
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "user-model",
            "confirm_remote_data": True,
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    assert backend.setup()["execution_validation"]["verified"] is False

    backend.configure(
        {
            "execution": "cloud_model",
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "user-model",
            "api_key": "connection-test-secret",
            "confirm_remote_data": True,
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    assert backend.setup()["execution_validation"]["verified"] is True
    assert backend.readiness()["state"] == "ready"

    backend.configure(
        {
            "execution": "cloud_model",
            "endpoint": "https://api.example.test/v1/chat/completions",
            "model": "different-model",
            "api_key": "connection-test-secret",
            "confirm_remote_data": True,
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    assert backend.setup()["execution_validation"]["verified"] is False
    assert backend.readiness()["state"] == "connection_test_required"

    backend.test_connection(
        {
            "endpoint": "https://different-provider.example/v1/chat/completions",
            "model": "different-provider-model",
        }
    )
    assert captured["api_key"] == ""


@pytest.mark.parametrize(
    "content",
    [
        {"action": "report_outcome", "arguments": "not-an-object"},
        {"action": "report_outcome", "arguments": {"status": "progress"}},
        {
            "action": "report_outcome",
            "arguments": {"status": "unexpected", "summary": "Wrong status."},
        },
    ],
)
def test_model_connection_rejects_malformed_report_outcome(
    tmp_path: Path,
    monkeypatch,
    content,
) -> None:
    class FakeProvider:
        def __init__(self, **kwargs) -> None:
            pass

        def complete(self, messages):
            return ProviderResponse(content=content, usage={"total_tokens": 1})

    monkeypatch.setattr(
        "agentic_harness.gui.backend.OpenAICompatibleProvider",
        FakeProvider,
    )
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="structured action protocol"):
        backend.test_connection(
            {
                "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
                "model": "local-model",
            }
        )

    assert backend._execution_validation == {}


def test_replacing_session_key_invalidates_model_connection_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeProvider:
        def __init__(self, **kwargs) -> None:
            pass

        def complete(self, messages):
            return ProviderResponse(
                content={
                    "action": "report_outcome",
                    "arguments": {"status": "progress", "summary": "Connection passed."},
                },
                usage={"total_tokens": 1},
            )

    monkeypatch.setattr(
        "agentic_harness.gui.backend.OpenAICompatibleProvider",
        FakeProvider,
    )
    backend = EmbeddedExecutionBackend(tmp_path)
    body = {
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "local-model",
        "api_key": "tested-secret",
    }
    backend.test_connection(body)
    backend.configure(
        {
            **body,
            "execution": "local_model",
            "verification_command": "python -m pytest -q",
        }
    )
    assert backend.readiness()["state"] == "ready"

    backend.set_session_credential("replacement-secret")

    assert backend.setup()["execution_validation"]["verified"] is False
    assert backend.readiness()["state"] == "connection_test_required"


def test_changing_env_credential_name_or_value_invalidates_model_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeProvider:
        def __init__(self, **kwargs) -> None:
            pass

        def complete(self, messages):
            return ProviderResponse(
                content={
                    "action": "report_outcome",
                    "arguments": {"status": "progress", "summary": "Connection passed."},
                },
                usage={"total_tokens": 1},
            )

    monkeypatch.setattr(
        "agentic_harness.gui.backend.OpenAICompatibleProvider",
        FakeProvider,
    )
    monkeypatch.setenv("MODEL_KEY_A", "tested-secret")
    backend = EmbeddedExecutionBackend(tmp_path)
    connection = {
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
        "model": "local-model",
        "api_key_env": "MODEL_KEY_A",
    }
    backend.test_connection(connection)
    backend.configure(
        {
            **connection,
            "execution": "local_model",
            "verification_command": "python -m pytest -q",
        }
    )
    assert backend.readiness()["state"] == "ready"

    monkeypatch.setenv("MODEL_KEY_A", "replacement-secret")
    assert backend.readiness()["state"] == "connection_test_required"

    backend.test_connection(connection)
    assert backend.readiness()["state"] == "ready"
    monkeypatch.setenv("MODEL_KEY_B", "replacement-secret")
    backend.configure(
        {
            **connection,
            "api_key_env": "MODEL_KEY_B",
            "execution": "local_model",
            "verification_command": "python -m pytest -q",
        }
    )
    assert backend.setup()["execution_validation"]["verified"] is False


def test_codex_connection_test_runs_the_configured_model_in_read_only_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "agentic_harness.gui.backend.resolve_executable",
        lambda name: sys.executable if name == "codex" else None,
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            "AGENTIC_HARNESS_AGENT_READY\n",
            "",
        )

    monkeypatch.setattr("agentic_harness.gui.backend.subprocess.run", fake_run)
    backend = EmbeddedExecutionBackend(tmp_path)

    result = backend.test_connection(
        {"execution": "coding_agent", "agent": "codex"}
    )
    backend.configure(
        {
            "execution": "coding_agent",
            "agent": "codex",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    setup = backend.setup()

    command = captured["command"]
    assert isinstance(command, list)
    assert command[command.index("-s") + 1] == "read-only"
    assert "{objective}" not in command
    assert result["verified"] is True
    assert result["scope"] == "live_model"
    assert setup["execution_validation"]["verified"] is True
    assert "verified in this app session" in setup["execution_validation"]["summary"]


def test_codex_connection_test_surfaces_config_error_without_claiming_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agentic_harness.gui.backend.resolve_executable",
        lambda name: sys.executable if name == "codex" else None,
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "invalid value 'default' for service_tier",
        )

    monkeypatch.setattr("agentic_harness.gui.backend.subprocess.run", fake_run)
    backend = EmbeddedExecutionBackend(tmp_path)

    with pytest.raises(ValueError, match="service_tier"):
        backend.test_connection({"execution": "coding_agent", "agent": "codex"})

    assert backend._execution_validation == {}


def test_setup_exposes_editable_provider_templates_without_secrets(tmp_path: Path) -> None:
    setup = EmbeddedExecutionBackend(tmp_path).setup()
    templates = {row["key"]: row for row in setup["provider_templates"]}

    assert setup["deployment"] == {
        "scope": "local_self_hosted",
        "multi_user": False,
        "summary": "One trusted user and one workspace on this computer.",
    }
    assert templates["custom"]["endpoint"] == ""
    assert templates["ollama_local"]["endpoint"] == (
        "http://127.0.0.1:11434/v1/chat/completions"
    )
    assert templates["lm_studio_local"]["data_location"] == "local"
    assert templates["vllm_local"]["data_location"] == "local"
    assert templates["zai_api"]["endpoint"].endswith("/paas/v4/chat/completions")
    assert templates["zai_api"]["data_location"] == "cloud"
    assert templates["zai_coding_plan"]["model"] == "glm-5.2"
    assert all("api_key" not in row for row in setup["provider_templates"])
