from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentic_harness.core.local_goal_bridge import LocalGoalBridge


NOVICE_GOALS = (
    "Please audit my system and give my controller a simple report of what you found.",
    "My website contact form does not seem to work. Please find the problem and fix it.",
    "Please organize the project notes and make the getting-started guide easier to follow.",
    "Some tests are failing. Please figure out why and repair them without breaking working features.",
    "Please make the site comfortable to use on an iPhone and check that nothing runs off the screen.",
    "Tell me what changed recently and create a short report I can share with my team.",
    "Please check whether the backups are healthy and explain the result without technical jargon.",
    "This page feels slow. Please improve it without changing how it looks or works.",
    "Please add a way to download the table as a CSV file and make sure it works.",
    "The app looks frozen. Please investigate the cause, fix it, and show me how you know it is working.",
)

SUPPORTED_MODES = ("local", "guided", "cloud")


@pytest.mark.parametrize("mode", SUPPORTED_MODES)
@pytest.mark.parametrize("goal", NOVICE_GOALS)
def test_plain_language_goals_route_through_each_supported_human_mode(
    tmp_path: Path,
    mode: str,
    goal: str,
) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["capabilities", "--json"]:
            stdout = json.dumps(
                {"external_candidate_contracts": ["agentic_harness.external_candidate.v1"]}
            )
        else:
            stdout = json.dumps({"status": "starting", "summary": "Accepted"})
        return subprocess.CompletedProcess(command, 0, stdout, "")

    executable = tmp_path / "local-goal"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    bridge = LocalGoalBridge(doc_root=tmp_path, local_goal=executable, runner=runner)

    result = bridge.start_human_goal(mode_key=mode, objective=goal)

    assert result.returncode == 0
    flattened = "\n".join(" ".join(call) for call in calls)
    assert goal in flattened
    expected_action = {
        "local": "quick-start",
        "guided": "premium-start",
        "cloud": "enqueue",
    }[mode]
    assert expected_action in calls[-1]


@pytest.mark.parametrize("goal", NOVICE_GOALS)
def test_plain_language_goals_cannot_reactivate_retired_experimental_route(
    tmp_path: Path,
    goal: str,
) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"status":"available"}', "")

    executable = tmp_path / "local-goal"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    bridge = LocalGoalBridge(doc_root=tmp_path, local_goal=executable, runner=runner)

    result = bridge.start_human_goal(mode_key="experimental", objective=goal)

    assert result.returncode == 2
    assert "retired" in result.stderr.lower()
    assert all(
        not any(action in call for action in ("quick-start", "premium-start", "enqueue"))
        for call in calls
    )
