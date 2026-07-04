#!/usr/bin/env python3
"""End-to-end canary for chat-safe local-goal transfer output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


COMMAND_PATH = (
    Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts")
    / "local-node1-goal-command.py"
)

cmd_ns: dict = {"__name__": "local_node1_goal_command_doc_spam_integration_test"}
exec(compile(COMMAND_PATH.read_text(), str(COMMAND_PATH), "exec"), cmd_ns)

main = cmd_ns["main"]


RAW_SUPERVISOR_STDOUT = """# Local Goal

transfer_prompt=/mnt/raid0/documentation/reports/local-node1-goal-harness/prompt.md
run_dir=/mnt/raid0/documentation/reports/local-node1-goal-harness/runs/20260703T212600Z-doc-spam-integration
latest=/mnt/raid0/documentation/reports/local-node1-goal-harness/latest.md
session=/mnt/raid0/documentation/reports/local-node1-goal-harness/session.log
complete=/mnt/raid0/documentation/reports/local-node1-goal-harness/complete.json

Worker handoff is ready on disk.
"""


def assert_no_local_artifact_paths(text: str) -> None:
    assert "/mnt/raid0/" not in text
    assert "prompt.md" not in text
    assert "latest.md" not in text
    assert "session.log" not in text
    assert "complete.json" not in text


def test_transfer_command_suppresses_artifact_paths_in_text_and_json(
    tmp_path, monkeypatch, capsys
) -> None:
    calls: list[list[str]] = []

    def fake_run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, RAW_SUPERVISOR_STDOUT, "")

    old_run_command = cmd_ns["run_command"]
    old_state_path = cmd_ns["STATE_PATH"]
    old_report_path = cmd_ns["REPORT_PATH"]
    try:
        cmd_ns["run_command"] = fake_run_command
        cmd_ns["STATE_PATH"] = tmp_path / "state.json"
        cmd_ns["REPORT_PATH"] = tmp_path / "report.md"

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local-node1-goal-command.py",
                "transfer",
                "/goal:",
                "verify doc-spam integration suppression",
            ],
        )
        assert main() == 0
        text_output = capsys.readouterr().out

        state_payload = json.loads(
            (tmp_path / "state.json").read_text(encoding="utf-8")
        )
        assert "Artifact output suppressed for chat." in text_output
        assert "command_artifacts=written" in text_output
        assert_no_local_artifact_paths(text_output)
        assert RAW_SUPERVISOR_STDOUT.strip() in state_payload["stdout_tail"]
        assert calls and "start" in calls[0]
        start_index = calls[0].index("start")
        assert calls[0][start_index : start_index + 3] == [
            "start",
            "--executor",
            "opencode",
        ]

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "local-node1-goal-command.py",
                "transfer",
                "/goal:",
                "verify doc-spam integration suppression",
                "--json",
            ],
        )
        assert main() == 0
        json_output = capsys.readouterr().out

        payload = json.loads(json_output)
        visible_payload = json.dumps(payload, sort_keys=True)
        state_payload = json.loads(
            (tmp_path / "state.json").read_text(encoding="utf-8")
        )

        assert payload["artifact_paths_suppressed"] is True
        assert payload["stdout"].startswith("Artifact output suppressed for chat.")
        assert payload["state_path"] == "written"
        assert payload["report_path"] == "written"
        assert_no_local_artifact_paths(visible_payload)
        assert RAW_SUPERVISOR_STDOUT.strip() in state_payload["stdout_tail"]
    finally:
        cmd_ns["run_command"] = old_run_command
        cmd_ns["STATE_PATH"] = old_state_path
        cmd_ns["REPORT_PATH"] = old_report_path
