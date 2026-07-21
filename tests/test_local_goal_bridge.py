from __future__ import annotations

import subprocess
import threading
import time

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
    monkeypatch.delenv("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", raising=False)
    monkeypatch.delenv("AGENTIC_HARNESS_EXTERNAL_PLANNER", raising=False)
    monkeypatch.delenv("AGENTIC_HARNESS_EXTERNAL_LONG_WORKER", raising=False)
    monkeypatch.delenv("AGENTIC_HARNESS_EXTERNAL_EXPERIMENTAL_WORKER", raising=False)


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
    assert "configured external orchestrator" in goal
    assert "selection is pinned by the managed Mode 3A contract" in goal
    assert "- services/voice-assistant" in goal
    assert "- python3 -m pytest tests/test_voice.py" in goal
    assert "Do not expose or modify secrets" in goal
    assert "Preserve the full original objective" in goal
    assert "same blocking condition repeats in three consecutive supervisor cycles" in goal
    assert "honest blocked report" not in goal
    assert "Do not mark the goal complete" in goal
    assert "worker-derived requirements" in goal
    assert "configured deterministic review passes" in goal
    assert "completion audit proves the original objective" not in goal


def test_local_goal_bridge_enqueue_mode3a_calls_local_goal(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        if command[-2:] == ["capabilities", "--json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    '{"external_candidate_contracts":["agentic_harness.external_candidate.v1"],'
                    '"lanes":{"cloud_executor":{"installed":true,"available_now":true}}}'
                ),
                "",
            )
        if command[-2:] == ["adapter-matrix", "--json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    '{"matrix":[{"worker":"opencode-glm-build","enabled":true,'
                    '"binary_resolved":true,"mutation_default":"implementation",'
                    '"blockers":[]}]}'
                ),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "queued_id=abc123\n", "")

    doc_root = tmp_path / "docs"
    local_goal = doc_root / "scripts/local-goal"
    bridge = LocalGoalBridge(doc_root=doc_root, local_goal=local_goal, runner=fake_runner)

    result = bridge.enqueue_mode3a(Mode3AGoalOptions(objective="fix one thing"))

    assert result.returncode == 0
    assert calls
    command = calls[-1]
    assert command[:10] == [
        str(local_goal),
        "enqueue",
        "--harness-contract",
        "agentic_harness.external_candidate.v1",
        "--planner",
        "glm-5.2",
        "--executor",
        "opencode",
        "--executor-worker",
        "opencode-glm-build",
    ]
    assert "--goal" in command
    assert "fix one thing" in command[-1]


def test_human_modes_use_routes_advertised_by_external_backend(tmp_path) -> None:
    calls: list[list[str]] = []
    capabilities = """{
      "external_candidate_contracts": ["agentic_harness.external_candidate.v1"],
      "lanes": {
        "local": {"executor": "mini-swe"},
        "premium_planner_local_builder": {"planners": ["thinkmax"]},
        "cloud_executor": {
          "default_executor_worker": "kimi",
          "executor_workers": ["kimi", "codex"],
          "adapter_canary_workers": ["codex"]
        }
      }
    }"""

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        stdout = capabilities if command[-2:] == ["capabilities", "--json"] else "{}"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )

    bridge.start_human_goal(mode_key="local", objective="one")
    bridge.start_human_goal(mode_key="guided", objective="two")
    bridge.start_human_goal(mode_key="cloud", objective="three")
    retired = bridge.start_human_goal(mode_key="experimental", objective="four")

    starts = [
        call
        for call in calls
        if len(call) > 1 and call[1] in {"quick-start", "premium-start", "enqueue"}
    ]
    assert starts[0][starts[0].index("--executor") + 1] == "mini-swe"
    assert starts[1][starts[1].index("--planner") + 1] == "thinkmax"
    assert starts[1][starts[1].index("--executor") + 1] == "mini-swe"
    assert starts[2][starts[2].index("--executor-worker") + 1] == "kimi"
    assert len(starts) == 3
    assert retired.returncode == 2
    assert "retired" in retired.stderr
    assert starts[2][starts[2].index("--harness-contract") + 1] == (
        "agentic_harness.external_candidate.v1"
    )


def test_starting_a_human_goal_invalidates_previous_status_and_last_run(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        if command[-2:] == ["capabilities", "--json"]:
            stdout = '{"lanes":{"local":{"executor":"opencode"}}}'
        elif "last-run" in command:
            stdout = '{"status":"complete"}'
        elif "status" in command:
            stdout = '{"classification":"idle"}'
        else:
            stdout = '{"status":"starting"}'
        return subprocess.CompletedProcess(command, 0, stdout, "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )
    bridge.status(json_output=True)
    bridge.last_run(json_output=True)
    bridge.start_human_goal(mode_key="local", objective="new work")
    bridge.status(json_output=True)
    bridge.last_run(json_output=True)

    assert sum("status" in call for call in calls) == 2
    assert sum("last-run" in call for call in calls) == 2


def test_mode1_forwards_every_preregistered_verification_command(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = list(args[0])
        calls.append(command)
        stdout = (
            '{"lanes":{"local":{"executor":"opencode"}}}'
            if command[-2:] == ["capabilities", "--json"]
            else "run_dir=/tmp/run\nstarted\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )
    checks = ("python3 -m pytest -q", "python3 scripts/verify_result.py")

    bridge.start_human_goal(
        mode_key="local",
        objective="verified work",
        checks=checks,
    )

    start = next(call for call in calls if "quick-start" in call)
    assert start[start.index("--title") + 1] == "verified work"
    observed = [
        start[index + 1]
        for index, value in enumerate(start)
        if value == "--verification-command"
    ]
    assert observed == list(checks)


def test_local_goal_bridge_monitor_never_requests_auto_accept(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )

    bridge.monitor(json_output=True)

    assert calls
    assert "--auto-accept" not in calls[0]


def test_local_goal_bridge_throttles_duplicate_monitor_calls(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"status":"working"}', "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )

    first = bridge.monitor(json_output=True)
    second = bridge.monitor(json_output=True)

    assert first is second
    assert len(calls) == 1


def test_local_goal_bridge_refuses_unadvertised_candidate_contract(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
    )

    result = bridge.enqueue_mode3a(Mode3AGoalOptions(objective="fix one thing"))

    assert result.returncode == 2
    assert "does not advertise" in result.stderr
    assert calls == [
        [str(tmp_path / "local-goal"), "capabilities", "--json"],
        [str(tmp_path / "local-goal"), "harness-modes", "--json"],
    ]


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


def test_json_status_requests_share_one_short_lived_controller_read(tmp_path) -> None:
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(6)

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return subprocess.CompletedProcess(args[0], 0, '{"classification":"idle"}', "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=fake_runner,
        status_cache_seconds=10,
    )
    results = []

    def read_status() -> None:
        start.wait()
        results.append(bridge.status(json_output=True))

    threads = [threading.Thread(target=read_status) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(results) == 6
    assert all(result.stdout == '{"classification":"idle"}' for result in results)
