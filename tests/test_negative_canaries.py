#!/usr/bin/env python3
"""Negative canaries for graceful local Node1 goal harness failures."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts")
COMMAND_PATH = SCRIPTS / "local-node1-goal-command.py"
SUPERVISOR_PATH = SCRIPTS / "local-node1-goal-supervisor.py"
WORKER_PATH = SCRIPTS / "local-node1-goal-worker.py"
CURRENT_TRUTH_PATH = SCRIPTS / "local-node1-goal-current-truth.py"

cmd_ns: dict = {"__name__": "local_node1_goal_command_negative_canaries_test"}
exec(compile(COMMAND_PATH.read_text(), str(COMMAND_PATH), "exec"), cmd_ns)

spec = importlib.util.spec_from_file_location(
    "local_node1_goal_supervisor_negative_canaries_test", SUPERVISOR_PATH
)
supervisor = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(supervisor)

worker_spec = importlib.util.spec_from_file_location(
    "local_node1_goal_worker_negative_canaries_test", WORKER_PATH
)
worker = importlib.util.module_from_spec(worker_spec)
assert worker_spec.loader is not None
worker_spec.loader.exec_module(worker)

current_truth_spec = importlib.util.spec_from_file_location(
    "local_node1_goal_current_truth_negative_canaries_test", CURRENT_TRUTH_PATH
)
current_truth = importlib.util.module_from_spec(current_truth_spec)
assert current_truth_spec.loader is not None
current_truth_spec.loader.exec_module(current_truth)


def run_command_main(
    argv: list[str],
    tmp_path: Path,
    monkeypatch,
    fake_run_command,
) -> int:
    old_run_command = cmd_ns["run_command"]
    old_state_path = cmd_ns["STATE_PATH"]
    old_report_path = cmd_ns["REPORT_PATH"]
    try:
        cmd_ns["run_command"] = fake_run_command
        cmd_ns["STATE_PATH"] = tmp_path / "state.json"
        cmd_ns["REPORT_PATH"] = tmp_path / "report.md"
        monkeypatch.setattr(sys, "argv", ["local-node1-goal-command.py", *argv])
        return cmd_ns["main"]()
    finally:
        cmd_ns["run_command"] = old_run_command
        cmd_ns["STATE_PATH"] = old_state_path
        cmd_ns["REPORT_PATH"] = old_report_path


def test_corrupted_queue_state_invalid_json_falls_back_without_rewriting(
    tmp_path, monkeypatch
) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(supervisor, "QUEUE_JSON", queue_path)

    queue = supervisor.load_queue()

    assert queue["contract"] == "local_node1_goal_queue.v1"
    assert queue["items"] == []
    assert queue["_parse_errors"][0]["type"] == "JSONDecodeError"
    assert queue["_parse_errors"][0]["snippet"] == "{not-json"
    assert queue_path.read_text(encoding="utf-8") == "{not-json"


def test_corrupted_queue_state_wrong_item_types_are_ignored(
    tmp_path, monkeypatch
) -> None:
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps({"contract": "local_node1_goal_queue.v1", "items": "bad"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "QUEUE_JSON", queue_path)

    assert supervisor.queue_items() == []


def test_missing_run_directory_reports_missing_artifacts_without_crashing(tmp_path) -> None:
    missing_run = tmp_path / "does-not-exist"

    artifacts = supervisor._active_run_artifacts(missing_run)

    assert artifacts["run_dir"] == str(missing_run)
    assert artifacts["present"] == []
    assert "complete.json" in artifacts["missing"]


def test_concurrent_start_rejection_is_returned_cleanly(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "already running: active goal")

    rc = run_command_main(
        ["start local goal: second start should be rejected"],
        tmp_path,
        monkeypatch,
        fake_run_command,
    )

    captured = capsys.readouterr()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert "already running" in captured.err
    assert state["returncode"] == 1
    assert "already running" in state["stderr_tail"]


def test_partial_truncated_supervisor_output_does_not_crash(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, '{"contract": "local_node1_goal', "")

    rc = run_command_main(
        ["start local goal: tolerate partial output", "--json"],
        tmp_path,
        monkeypatch,
        fake_run_command,
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["supervisor_payload"] is None
    assert payload["stdout"] == '{"contract": "local_node1_goal'


def test_empty_goal_text_is_rejected_before_subprocess(
    tmp_path, monkeypatch, capsys
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    rc = run_command_main(["start local goal:"], tmp_path, monkeypatch, fake_run_command)

    assert rc == 2
    assert not calls
    assert "start requires a non-empty goal" in capsys.readouterr().err
    assert not (tmp_path / "state.json").exists()


def test_goal_text_with_shell_metacharacters_is_passed_as_one_argument(
    tmp_path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    goal = "touch safe; echo injected && $(whoami)"

    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    rc = run_command_main(
        [f"start local goal: {goal}"],
        tmp_path,
        monkeypatch,
        fake_run_command,
    )

    assert rc == 0
    assert calls
    assert "--goal" in calls[0]
    assert calls[0][calls[0].index("--goal") + 1] == goal


def test_supervisor_nonzero_exit_is_reported_without_exception(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 7, "partial stdout", "fatal supervisor error")

    rc = run_command_main(
        ["start local goal: handle supervisor failure"],
        tmp_path,
        monkeypatch,
        fake_run_command,
    )

    captured = capsys.readouterr()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert rc == 7
    assert "partial stdout" in captured.out
    assert "fatal supervisor error" in captured.err
    assert state["returncode"] == 7


def test_supervisor_empty_output_does_not_crash(tmp_path, monkeypatch, capsys) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "", "")

    rc = run_command_main(
        ["start local goal: tolerate empty output"],
        tmp_path,
        monkeypatch,
        fake_run_command,
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert "intent=start" in captured.out
    assert (tmp_path / "state.json").exists()


def test_state_artifact_write_failure_returns_clear_error(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    old_write_artifacts = cmd_ns["write_artifacts"]
    try:
        cmd_ns["write_artifacts"] = lambda *args, **kwargs: (_ for _ in ()).throw(
            TimeoutError("state file locked")
        )
        rc = run_command_main(
            ["start local goal: state lock timeout", "--json"],
            tmp_path,
            monkeypatch,
            fake_run_command,
        )
    finally:
        cmd_ns["write_artifacts"] = old_write_artifacts

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "state file locked" in payload["artifact_write_error"]
    assert payload["summary"].startswith("ERROR: command artifact write failed")


def test_artifact_traversal_paths_are_suppressed_and_not_counted() -> None:
    output = (
        "# Local Goal\n"
        "prompt=/mnt/raid0/documentation/reports/local-node1-goal-harness/../secret/prompt.md\n"
        "complete=/mnt/raid0/documentation/reports/local-node1-goal-harness/complete.json\n"
    )

    safe = cmd_ns["chat_safe_command_output"](
        output, intent="transfer", cmd=[str(cmd_ns["WRAPPER"]), "transfer"]
    )

    assert "Artifact output suppressed for chat." in safe
    assert "/mnt/raid0/" not in safe
    assert "secret/prompt.md" not in safe
    assert "Artifact files kept on disk: 1" in safe


def test_worker_rejects_report_output_outside_controller_artifact_roots() -> None:
    try:
        worker.resolve_worker_output_path("/tmp/escape.md", "report")
    except ValueError as exc:
        assert "controller artifact roots" in str(exc)
    else:
        raise AssertionError("expected report path traversal rejection")


def test_worker_accepts_controller_report_and_worker_run_outputs() -> None:
    report = worker.resolve_worker_output_path(
        "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/reports/local-node1.md",
        "report",
    )
    status = worker.resolve_worker_output_path(
        "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/worker-runs/local-node1/result.json",
        "status",
    )

    assert str(report).endswith("/reports/local-node1.md")
    assert str(status).endswith("/worker-runs/local-node1/result.json")


def test_current_truth_load_json_file_records_parse_error(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"bad"', encoding="utf-8")

    payload = current_truth.load_json_file(path)

    assert payload["available"] is False
    assert payload["unreadable"] is True
    assert payload["_parse_errors"][0]["type"] == "JSONDecodeError"
    assert payload["_parse_errors"][0]["snippet"] == '{"bad"'


def test_worker_manager_status_records_parse_error(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, '{"bad"', "stderr")

    monkeypatch.setattr(worker, "run", fake_run)

    payload = worker.manager_status()

    assert payload["error"] == "manager status unreadable"
    assert payload["_parse_errors"][0]["type"] == "JSONDecodeError"
    assert payload["_parse_errors"][0]["snippet"] == '{"bad"'
