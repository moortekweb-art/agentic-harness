#!/usr/bin/env python3
"""Focused canaries for suppressing local-goal artifact paths in chat output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


COMMAND_PATH = (
    Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts")
    / "local-node1-goal-command.py"
)

cmd_ns: dict = {"__name__": "local_node1_goal_command_doc_spam_test"}
exec(compile(COMMAND_PATH.read_text(), str(COMMAND_PATH), "exec"), cmd_ns)

WRAPPER = str(cmd_ns["WRAPPER"])
looks_like_chat_unsafe_artifact_output = cmd_ns[
    "looks_like_chat_unsafe_artifact_output"
]
summarize_chat_unsafe_artifact_output = cmd_ns[
    "summarize_chat_unsafe_artifact_output"
]
chat_safe_command_output = cmd_ns["chat_safe_command_output"]
main = cmd_ns["main"]


RAW_ARTIFACT_STDOUT = """# Local Goal

transfer_prompt=/mnt/raid0/documentation/reports/local-node1-goal-harness/prompt.md
run_dir=/mnt/raid0/documentation/reports/local-node1-goal-harness/runs/20260703T212600Z-example
latest=/mnt/raid0/documentation/reports/local-node1-goal-harness/latest.md
session=/mnt/raid0/documentation/reports/local-node1-goal-harness/session.log
complete=/mnt/raid0/documentation/reports/local-node1-goal-harness/complete.json
"""


def assert_no_attachable_artifact_paths(text: str) -> None:
    assert "/mnt/raid0/" not in text
    assert "prompt.md" not in text
    assert "latest.md" not in text
    assert "session.log" not in text
    assert "complete.json" not in text


def test_artifact_path_summary_omits_attachable_file_paths() -> None:
    assert looks_like_chat_unsafe_artifact_output(RAW_ARTIFACT_STDOUT)
    summary = summarize_chat_unsafe_artifact_output(
        RAW_ARTIFACT_STDOUT,
        intent="premium-start",
        cmd=[WRAPPER, "premium-start", "--planner", "gpt-5.5"],
    )

    assert "Artifact output suppressed for chat." in summary
    assert "Artifact files kept on disk: 4" in summary
    assert "worker prompt" in summary
    assert "latest summary" in summary
    assert "session transcript" in summary
    assert "completion marker" in summary
    assert_no_attachable_artifact_paths(summary)


def test_chat_safe_command_output_suppresses_transfer_artifact_paths() -> None:
    safe = chat_safe_command_output(
        RAW_ARTIFACT_STDOUT,
        intent="transfer",
        cmd=[WRAPPER, "transfer"],
    )

    assert "Artifact output suppressed for chat." in safe
    assert_no_attachable_artifact_paths(safe)


def test_routine_start_json_suppresses_document_attachment_paths(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, RAW_ARTIFACT_STDOUT, "")

    old_run_command = cmd_ns["run_command"]
    old_state_path = cmd_ns["STATE_PATH"]
    old_report_path = cmd_ns["REPORT_PATH"]
    cmd_ns["run_command"] = fake_run_command
    cmd_ns["STATE_PATH"] = tmp_path / "state.json"
    cmd_ns["REPORT_PATH"] = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "local-node1-goal-command.py",
            "start local goal: canary document attachment suppression",
            "--json",
        ],
    )
    try:
        assert main() == 0
    finally:
        cmd_ns["run_command"] = old_run_command
        cmd_ns["STATE_PATH"] = old_state_path
        cmd_ns["REPORT_PATH"] = old_report_path

    payload = json.loads(capsys.readouterr().out)
    visible = json.dumps(payload, sort_keys=True)
    state_payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    assert payload["artifact_paths_suppressed"] is True
    assert payload["stdout"].startswith("Artifact output suppressed for chat.")
    assert payload["state_path"] == "written"
    assert payload["report_path"] == "written"
    assert_no_attachable_artifact_paths(visible)
    assert RAW_ARTIFACT_STDOUT.strip() in state_payload["stdout_tail"]


def test_routine_start_text_suppresses_document_attachment_paths(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, RAW_ARTIFACT_STDOUT, "")

    old_run_command = cmd_ns["run_command"]
    old_state_path = cmd_ns["STATE_PATH"]
    old_report_path = cmd_ns["REPORT_PATH"]
    cmd_ns["run_command"] = fake_run_command
    cmd_ns["STATE_PATH"] = tmp_path / "state.json"
    cmd_ns["REPORT_PATH"] = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "local-node1-goal-command.py",
            "start local goal: canary document attachment suppression",
        ],
    )
    try:
        assert main() == 0
    finally:
        cmd_ns["run_command"] = old_run_command
        cmd_ns["STATE_PATH"] = old_state_path
        cmd_ns["REPORT_PATH"] = old_report_path

    visible = capsys.readouterr().out
    state_payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    assert "Artifact output suppressed for chat." in visible
    assert "command_artifacts=written" in visible
    assert_no_attachable_artifact_paths(visible)
    assert RAW_ARTIFACT_STDOUT.strip() in state_payload["stdout_tail"]
