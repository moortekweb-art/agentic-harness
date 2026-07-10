from __future__ import annotations

import subprocess

import pytest

from agentic_harness.cli import _friendly_queue_summary
from agentic_harness.core.local_goal_bridge import (
    LocalGoalBridge,
    Mode3AGoalOptions,
    build_mode3a_goal,
    format_popos_setup,
)


@pytest.fixture(autouse=True)
def clean_local_goal_env(monkeypatch) -> None:
    monkeypatch.delenv("AGENTIC_HARNESS_DOC_ROOT", raising=False)
    monkeypatch.delenv("AGENTIC_HARNESS_LOCAL_GOAL", raising=False)


def test_local_goal_bridge_defaults_to_current_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    bridge = LocalGoalBridge()

    assert bridge.doc_root == tmp_path
    assert bridge.local_goal == tmp_path / "scripts/local-goal"


def test_local_goal_bridge_uses_doc_root_environment_override(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "configured-docs"
    monkeypatch.setenv("AGENTIC_HARNESS_DOC_ROOT", str(configured))
    monkeypatch.chdir(tmp_path)

    bridge = LocalGoalBridge()

    assert bridge.doc_root == configured
    assert bridge.local_goal == configured / "scripts/local-goal"


def test_local_goal_bridge_ignores_empty_doc_root_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTIC_HARNESS_DOC_ROOT", "")
    monkeypatch.chdir(tmp_path)

    bridge = LocalGoalBridge()

    assert bridge.doc_root == tmp_path


def test_local_goal_bridge_explicit_doc_root_wins_over_environment(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "explicit-docs"
    monkeypatch.setenv("AGENTIC_HARNESS_DOC_ROOT", str(tmp_path / "env-docs"))

    bridge = LocalGoalBridge(doc_root=explicit)

    assert bridge.doc_root == explicit
    assert bridge.local_goal == explicit / "scripts/local-goal"


def test_local_goal_bridge_expands_user_in_configured_paths(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENTIC_HARNESS_DOC_ROOT", "~/docs")

    bridge = LocalGoalBridge()

    assert bridge.doc_root == home / "docs"
    assert bridge.local_goal == home / "docs/scripts/local-goal"


def test_local_goal_bridge_local_goal_executable_override_wins(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENTIC_HARNESS_DOC_ROOT", str(tmp_path / "docs"))
    monkeypatch.setenv("AGENTIC_HARNESS_LOCAL_GOAL", "~/bin/local-goal")

    bridge = LocalGoalBridge()

    assert bridge.doc_root == tmp_path / "docs"
    assert bridge.local_goal == home / "bin/local-goal"


def test_build_mode3a_goal_hides_worker_details_behind_plain_objective() -> None:
    goal = build_mode3a_goal(
        Mode3AGoalOptions(
            objective="make Jarvis voice startup more reliable",
            allowed_paths=("services/voice-assistant",),
            verification=("python3 -m pytest tests/test_voice.py",),
        )
    )

    assert "make Jarvis voice startup more reliable" in goal
    assert "Planner: glm-5.2" in goal
    assert "Executor worker: opencode-glm-build" in goal
    assert "- services/voice-assistant" in goal
    assert "- python3 -m pytest tests/test_voice.py" in goal
    assert "Do not expose or modify secrets" in goal
    assert "Preserve the full original objective" in goal
    assert "same blocking condition repeats in three consecutive supervisor cycles" in goal
    assert "honest blocked report" not in goal
    assert "Do not mark the goal complete" in goal
    assert "requirement-by-requirement completion audit" in goal


def test_local_goal_bridge_enqueue_mode3a_calls_local_goal(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "queued_id=abc123\n", "")

    doc_root = tmp_path / "docs"
    local_goal = doc_root / "scripts/local-goal"
    bridge = LocalGoalBridge(doc_root=doc_root, local_goal=local_goal, runner=fake_runner)

    result = bridge.enqueue_mode3a(Mode3AGoalOptions(objective="fix one thing"))

    assert result.returncode == 0
    assert calls
    command = calls[0]
    assert command[:8] == [
        str(local_goal),
        "enqueue",
        "--planner",
        "glm-5.2",
        "--executor",
        "opencode",
        "--executor-worker",
        "opencode-glm-build",
    ]
    assert "--goal" in command
    assert "fix one thing" in command[-1]


def test_friendly_queue_summary_prefers_ticket_id() -> None:
    assert _friendly_queue_summary("queued_id=abc123\nqueue_json=/tmp/q.json\n") == (
        "Work ticket: abc123"
    )


def test_friendly_queue_summary_handles_empty_output() -> None:
    assert _friendly_queue_summary("") == "Work ticket created."


def test_background_supervision_is_derived_from_backend_capabilities(tmp_path) -> None:
    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0],
            0,
            (
                '{"supervision":{"watcher":{"timer_active":true,'
                '"state":"active","summary":"watcher owns the run"}}}'
            ),
            "",
        )

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )

    supervision = bridge.background_supervision()

    assert supervision["active"] is True
    assert supervision["summary"] == "watcher owns the run"


def test_setup_reports_verified_background_supervision(tmp_path) -> None:
    local_goal = tmp_path / "local-goal"
    local_goal.write_text("#!/bin/sh\n", encoding="utf-8")
    local_goal.chmod(0o755)

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0],
            0,
            (
                '{"supervision":{"watcher":{"timer_active":true,'
                '"state":"active","summary":"watcher owns the run"}}}'
            ),
            "",
        )

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=local_goal,
        runner=fake_runner,
    )

    output = format_popos_setup(bridge)

    assert "Background supervisor active: True" in output
    assert "watcher owns the run" in output
    assert "agentic-harness mode3a-monitor" not in output


def test_bridge_timeout_is_a_recoverable_command_result(tmp_path) -> None:
    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args[0], timeout=kwargs["timeout"])

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
        timeout_seconds=7,
    )

    result = bridge.status(json_output=True)

    assert result.returncode == 124
    assert "timed out after 7s" in result.stderr
