from __future__ import annotations

import json
from pathlib import Path

from agentic_harness.core.local_goal_bridge import CommandResult
import agentic_harness.cli as cli


class FakeLocalGoalBridge:
    def __init__(self, *, doc_root: Path | None = None) -> None:
        self.doc_root = doc_root
        self.local_goal = Path("/tmp/fake-local-goal")
        self.status_calls = 0
        self.monitor_calls = 0
        self.start_kwargs: dict[str, object] | None = None

    def available(self) -> bool:
        return True

    def start_human_goal(self, **kwargs: object) -> CommandResult:
        self.start_kwargs = kwargs
        return CommandResult(
            ("local-goal", "start"),
            0,
            "queued_id=goal-123\nrun_dir=/tmp/queue\n",
            "",
        )

    def status(self, *, json_output: bool = False) -> CommandResult:  # noqa: ARG002
        self.status_calls += 1
        return CommandResult(
            ("local-goal", "status"),
            0,
            "mode=local\nqueued_id=goal-123\n",
            "",
        )

    def monitor(self, *, json_output: bool = False) -> CommandResult:  # noqa: ARG002
        self.monitor_calls += 1
        return CommandResult(("local-goal", "monitor"), 0, "monitor ok\n", "")


class MissingLocalGoalBridge(FakeLocalGoalBridge):
    def available(self) -> bool:
        return False


def test_external_do_command_starts_managed_goal_and_outputs_receipt(monkeypatch, tmp_path, capsys) -> None:
    captured: dict[str, FakeLocalGoalBridge] = {}

    def bridge_factory(*_args: object, **_kwargs: object) -> FakeLocalGoalBridge:
        bridge = FakeLocalGoalBridge()
        captured["bridge"] = bridge
        return bridge

    monkeypatch.setattr(cli, "LocalGoalBridge", bridge_factory)

    rc = cli.main(
        [
            "--project-dir",
            str(tmp_path),
            "external-do",
            "prepare release notes",
            "--mode",
            "local",
            "--safe-area",
            "docs",
            "--check",
            "python -m pytest",
            "--execution-profile",
            "automatic",
            "--supervision",
            "none",
            "--monitor",
            "--json",
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["mode"] == "local"
    assert payload["returncode"] == 0
    assert payload["monitor"]["returncode"] == 0

    bridge = captured["bridge"]
    assert bridge.start_kwargs == {
        "mode_key": "local",
        "objective": "prepare release notes",
        "safe_areas": ("docs",),
        "checks": ("python -m pytest",),
        "execution_profile": "automatic",
        "supervision": "none",
    }


def test_external_do_command_fails_when_backend_missing(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(cli, "LocalGoalBridge", MissingLocalGoalBridge)

    rc = cli.main(
        [
            "--project-dir",
            str(tmp_path),
            "external-do",
            "prepare release notes",
            "--json",
        ]
    )

    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "local-goal backend not found or not executable"


def test_external_status_and_watch_commands(monkeypatch, tmp_path) -> None:
    bridge = FakeLocalGoalBridge()
    monkeypatch.setattr(cli, "LocalGoalBridge", lambda *args, **kwargs: bridge)

    rc_status = cli.main(["--project-dir", str(tmp_path), "external-status"])
    assert rc_status == 0
    assert bridge.status_calls == 1

    rc_watch = cli.main(["--project-dir", str(tmp_path), "external-watch", "--json"])
    assert rc_watch == 0
    assert bridge.monitor_calls == 1
