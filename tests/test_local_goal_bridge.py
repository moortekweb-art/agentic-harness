from __future__ import annotations

import subprocess
from pathlib import Path

from agentic_harness.cli import _friendly_queue_summary
from agentic_harness.core.local_goal_bridge import (
    LocalGoalBridge,
    Mode3AGoalOptions,
    build_mode3a_goal,
)


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


def test_local_goal_bridge_enqueue_mode3a_calls_local_goal() -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "queued_id=abc123\n", "")

    bridge = LocalGoalBridge(
        doc_root=Path("/tmp/docs"),
        local_goal=Path("/tmp/docs/scripts/local-goal"),
        runner=fake_runner,
    )

    result = bridge.enqueue_mode3a(Mode3AGoalOptions(objective="fix one thing"))

    assert result.returncode == 0
    assert calls
    command = calls[0]
    assert command[:8] == [
        "/tmp/docs/scripts/local-goal",
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
