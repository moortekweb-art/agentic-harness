from __future__ import annotations

import json
import subprocess
import sys

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.tmux import TmuxWorker
from agentic_harness import Goal, Supervisor, Worker
from agentic_harness.cli import build_supervisor, main
from agentic_harness.core.config import load_config
from agentic_harness.core.errors import ConfigError


def test_init_creates_valid_project_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "init"])

    assert rc == 0
    assert (tmp_path / ".agentic-harness" / "config.yml").exists()
    assert load_config(tmp_path).worker == "noop"
    assert "created" in capsys.readouterr().out


def test_start_reports_active_goal_conflict_as_json(tmp_path, capsys) -> None:
    main(["--project-dir", str(tmp_path), "init"])
    assert main(["--project-dir", str(tmp_path), "start", "first"]) == 0
    capsys.readouterr()

    rc = main(["--project-dir", str(tmp_path), "start", "second"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["ok"] is False
    assert "active goal" in payload["error"]


def test_continue_without_active_goal_returns_json_and_does_not_poison_guard(
    tmp_path, capsys
) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    for _ in range(3):
        rc = main(["--project-dir", str(tmp_path), "continue"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 2
        assert payload["ok"] is False
        assert "no active goal" in payload["error"]

    assert not (tmp_path / ".agentic-harness" / "guard.json").exists()
    assert main(["--project-dir", str(tmp_path), "run", "real goal"]) == 0


def test_continue_reports_loop_guard_trip_as_json(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    guard_path = tmp_path / ".agentic-harness" / "guard.json"
    guard_path.write_text(
        json.dumps(
            {
                "events": [
                    9999999999,
                    9999999999,
                    9999999999,
                    9999999999,
                    9999999999,
                    9999999999,
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["--project-dir", str(tmp_path), "start", "guarded"]) == 0
    capsys.readouterr()

    rc = main(["--project-dir", str(tmp_path), "continue"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["ok"] is False
    assert "circuit breaker tripped" in payload["error"]


def test_review_reports_invalid_transition_as_json(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["--project-dir", str(tmp_path), "run", "done goal"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done"

    rc = main(["--project-dir", str(tmp_path), "review"])

    error_payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert error_payload["ok"] is False
    assert "cannot transition" in error_payload["error"]


def test_run_reports_missing_shell_executable_as_failed_goal(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "version: 1",
                "worker: shell",
                "shell_command:",
                "  - definitely-missing-agentic-harness-tool",
                "",
            ]
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    rc = main(["--project-dir", str(tmp_path), "run", "missing executable"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "failed"
    assert "definitely-missing-agentic-harness-tool" in payload["error"]


def test_config_parses_explicit_noop_success(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.worker == "noop"
    assert config.allow_noop_success is True


def test_config_rejects_unknown_keys(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: noop\nsurprise: true\n",
        encoding="utf-8",
    )

    try:
        load_config(tmp_path)
    except ConfigError as exc:
        assert "unknown config key" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_config_rejects_unsupported_version(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("version: 2\nworker: noop\n", encoding="utf-8")

    try:
        load_config(tmp_path)
    except ConfigError as exc:
        assert "unsupported config version" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_config_parses_cli_wired_adapters_and_review_command(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: tmux",
                "tmux_command: echo {goal_id}",
                "tmux_session_prefix: ah",
                "review_command:",
                "  - python",
                "  - -c",
                "  - \"print('review')\"",
                "review_command_timeout: 12",
                "review_git_clean: true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.worker == "tmux"
    assert config.tmux_command == "echo {goal_id}"
    assert config.tmux_session_prefix == "ah"
    assert config.review_command == ["python", "-c", "print('review')"]
    assert config.review_command_timeout == 12
    assert config.review_git_clean is True


def test_config_parses_nested_yaml_sections_and_inline_lists(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker:",
                "  type: shell",
                "  shell_command: [python, -c, \"print('value: with colon')\"]",
                "review:",
                "  command: [python, -c, \"print('review: ok')\"]",
                "  command_timeout: 7",
                "  git_clean: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.worker == "shell"
    assert config.shell_command == ["python", "-c", "print('value: with colon')"]
    assert config.review_command == ["python", "-c", "print('review: ok')"]
    assert config.review_command_timeout == 7
    assert config.review_git_clean is False


def test_build_supervisor_wires_tmux_worker_from_config(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "version: 1\nworker: tmux\ntmux_command: echo {objective}\n",
        encoding="utf-8",
    )

    supervisor = build_supervisor(tmp_path)

    assert isinstance(supervisor.worker, TmuxWorker)


def test_build_supervisor_wires_local_llm_worker_from_config(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: local_llm",
                "llm_endpoint: http://127.0.0.1:4000/v1/chat/completions",
                "llm_model: local-model",
                "",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = build_supervisor(tmp_path)

    assert isinstance(supervisor.worker, LocalLLMAdapter)


def test_build_supervisor_wires_github_actions_worker_from_config(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: github_actions",
                "github_owner: owner",
                "github_repo: repo",
                "github_workflow_id: workflow.yml",
                "github_token: token",
                "github_wait: true",
                "github_api_version: 2026-03-10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = build_supervisor(tmp_path)

    assert isinstance(supervisor.worker, GitHubActionsAdapter)
    assert supervisor.worker.wait_for_completion is True
    assert supervisor.worker.api_version == "2026-03-10"


def test_build_supervisor_wires_coding_agent_worker_from_config(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker:",
                "  type: coding_agent",
                "  coding_agent_command:",
                "    - codex",
                "    - exec",
                "    - --full-auto",
                "    - \"{objective}\"",
                "  coding_agent_timeout: 120",
                "  coding_agent_transcript: .agentic-harness/runs/{goal_id}/agent.log",
                "",
            ]
        ),
        encoding="utf-8",
    )

    supervisor = build_supervisor(tmp_path)

    assert isinstance(supervisor.worker, CodingAgentWorker)
    assert supervisor.worker.timeout == 120


def test_build_supervisor_wires_review_command_from_config(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker: noop",
                "allow_noop_success: true",
                "review_command:",
                "  - python",
                "  - -c",
                "  - \"print('ok')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    supervisor = build_supervisor(tmp_path)
    supervisor.start("review configured command")
    supervisor.continue_goal()

    reviewed = supervisor.review()

    assert reviewed.status == "done"
    assert [item["name"] for item in reviewed.review["criteria"]] == ["command_passes"]


def test_doctor_runs_without_crashing_on_empty_config(tmp_path, capsys) -> None:
    rc = main(["--project-dir", str(tmp_path), "doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is False
    assert payload["checks"][1]["name"] == "config"


def test_cli_start_status_continue_review_round_trip(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["--project-dir", str(tmp_path), "start", "ship cli"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "planning"

    assert main(["--project-dir", str(tmp_path), "continue"]) == 0
    continued = json.loads(capsys.readouterr().out)
    assert continued["status"] == "review"

    assert main(["--project-dir", str(tmp_path), "review"]) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["status"] == "done"

    assert main(["--project-dir", str(tmp_path), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["id"] == started["id"]


def test_cli_run_executes_start_continue_review_round_trip(tmp_path, capsys) -> None:
    assert main(["--project-dir", str(tmp_path), "init"]) == 0
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["--project-dir", str(tmp_path), "run", "ship in one command"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "done"
    assert payload["review"]["passed"] is True


def test_module_cli_init_works_in_temp_dir(tmp_path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentic_harness.cli",
            "--project-dir",
            str(tmp_path),
            "init",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0
    assert (tmp_path / ".agentic-harness" / "config.yml").exists()


def test_public_api_imports_cleanly() -> None:
    assert Goal is not None
    assert Supervisor is not None
    assert Worker is not None
