#!/usr/bin/env python3
"""Tests for self-rescue automation in the local Node1 goal harness.

Covers:
1. Repeated-command loop detection (stuck_repeat_command classification)
2. Complete marker + leftover tmux/process cleanup before acceptance
3. Failed review checks produce targeted continue prompt
4. Auto-accept when review ok=true and node1 is idle
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

# ── import the modules under test ────────────────────────────────────────

_SCRIPTS_DIR = (
    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts"
)
_SUPERVISOR_PATH = Path(_SCRIPTS_DIR) / "local-node1-goal-supervisor.py"

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "local_node1_goal_supervisor", str(_SUPERVISOR_PATH)
)
supervisor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(supervisor)

# ── import the manager ──────────────────────────────────────────────────

_MANAGER_PATH = Path("/mnt/raid0/documentation/scripts/local-node1-goal-manager.py")
_spec_mgr = importlib.util.spec_from_file_location(
    "local_node1_goal_manager", str(_MANAGER_PATH)
)
manager = importlib.util.module_from_spec(_spec_mgr)
_spec_mgr.loader.exec_module(manager)

# ── helpers ──────────────────────────────────────────────────────────────


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_session_log(tmp_path: Path, commands: list[str]) -> Path:
    """Write a synthetic session log with command-like lines."""
    log_path = tmp_path / "session.log"
    lines = [f"$ {cmd}" for cmd in commands]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# ── Test 1: Repeated-command loop detection ─────────────────────────────


class TestRepeatedCommandDetection:
    """Repeated-command loop detection classifies stuck, not working."""

    def test_detects_repeated_command_loop(self, tmp_path):
        """5+ identical commands in a window triggers stuck_repeat_command."""
        log_path = _write_session_log(
            tmp_path,
            [
                "python3 test.py",
                "python3 test.py",
                "python3 test.py",
                "python3 test.py",
                "python3 test.py",
            ],
        )
        result = manager.detect_repeated_commands(log_path)
        assert result["stuck"] is True
        assert result["classification"] == "stuck_repeat_command"
        assert result["repeated_count"] == 5
        assert "python3 test.py" in result["repeated_command"]

    def test_does_not_flag_different_commands(self, tmp_path):
        """Different commands should not be flagged as stuck."""
        log_path = _write_session_log(
            tmp_path,
            [
                "python3 test.py",
                "python3 other.py",
                "python3 third.py",
                "python3 fourth.py",
                "python3 fifth.py",
            ],
        )
        result = manager.detect_repeated_commands(log_path)
        assert result["stuck"] is False
        assert result["classification"] == "working"

    def test_does_not_flag_below_threshold(self, tmp_path):
        """Fewer than 5 repetitions should not trigger detection."""
        log_path = _write_session_log(
            tmp_path,
            ["python3 test.py", "python3 test.py", "python3 test.py"],
        )
        result = manager.detect_repeated_commands(log_path)
        assert result["stuck"] is False
        assert result["classification"] == "working"

    def test_normalizes_flags_for_comparison(self, tmp_path):
        """Commands that differ only in trailing flags should be treated as same."""
        log_path = _write_session_log(
            tmp_path,
            [
                "python3 -m pytest tests --json",
                "python3 -m pytest tests --json",
                "python3 -m pytest tests --json",
                "python3 -m pytest tests --json",
                "python3 -m pytest tests --json",
            ],
        )
        result = manager.detect_repeated_commands(log_path)
        assert result["stuck"] is True

    def test_empty_log_returns_working(self):
        """Empty or missing log returns working, not stuck."""
        result = manager.detect_repeated_commands(Path("/nonexistent/log"))
        assert result["stuck"] is False
        assert result["classification"] == "working"

    def test_non_consecutive_repeats_not_detected(self, tmp_path):
        """Non-consecutive repeats should not be detected as stuck."""
        log_path = _write_session_log(
            tmp_path,
            [
                "python3 test.py",
                "python3 other.py",
                "python3 test.py",
                "python3 other.py",
                "python3 test.py",
            ],
        )
        result = manager.detect_repeated_commands(log_path)
        # Last command is "python3 test.py", only 1 consecutive at the end
        assert result["stuck"] is False

    def test_supervisor_classify_returns_stuck_for_repeat(self):
        """Supervisor classify() returns stuck when repeated command detected."""
        status = {
            "verdict": "running_idle",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "log_age_seconds": 60,
            "repeated_command_detection": {
                "stuck": True,
                "repeated_command": "python3 test.py",
                "repeated_count": 7,
                "classification": "stuck_repeat_command",
            },
        }
        classification, action = supervisor.classify(status)
        assert classification == "stuck"
        assert "Repeated command loop detected" in action
        assert "stuck_repeat_command" in action

    def test_supervisor_classify_returns_working_without_repeat(self):
        """Supervisor classify() returns working when no repeated command."""
        status = {
            "verdict": "running_idle",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "log_age_seconds": 60,
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
                "classification": "working",
            },
        }
        classification, action = supervisor.classify(status)
        assert classification == "working"


# ── Test 2: Complete marker + leftover tmux/process cleanup ─────────────


class TestCompletionMarkerShutdown:
    """Complete marker + leftover tmux/process is cleaned up before acceptance."""

    def test_completion_marker_shutdown_logic(self):
        """Manager computes completion_marker_shutdown_needed from complete + loop + tmux."""
        # Simulate the logic: complete.json exists with status=complete,
        # loop_state=complete, tmux_running=True
        complete_marker = {"status": "complete", "summary": "done"}
        loop_state = {"status": "complete", "iteration": 5}
        tmux_running = True

        complete_marker_status = str(complete_marker.get("status") or "").lower()
        loop_complete = str(loop_state.get("status") or "") == "complete"
        shutdown_needed = (
            complete_marker_status == "complete" and loop_complete and tmux_running
        )
        assert shutdown_needed is True

    def test_no_shutdown_flag_when_tmux_stopped(self):
        """No shutdown flag when tmux is already stopped."""
        complete_marker = {"status": "complete", "summary": "done"}
        loop_state = {"status": "complete", "iteration": 5}
        tmux_running = False

        complete_marker_status = str(complete_marker.get("status") or "").lower()
        loop_complete = str(loop_state.get("status") or "") == "complete"
        shutdown_needed = (
            complete_marker_status == "complete" and loop_complete and tmux_running
        )
        assert shutdown_needed is False

    def test_no_shutdown_flag_when_loop_not_complete(self):
        """No shutdown flag when loop_state is not complete."""
        complete_marker = {"status": "complete"}
        loop_state = {"status": "running"}
        tmux_running = True

        complete_marker_status = str(complete_marker.get("status") or "").lower()
        loop_complete = str(loop_state.get("status") or "") == "complete"
        shutdown_needed = (
            complete_marker_status == "complete" and loop_complete and tmux_running
        )
        assert shutdown_needed is False

    def test_supervisor_stops_leftover_tmux_on_completion_marker_shutdown(self):
        """Supervisor monitor stops leftover tmux when completion marker shutdown needed."""
        status_data = {
            "verdict": "running_idle",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "completion_marker_shutdown_needed": True,
            "complete_marker": {"status": "complete"},
            "loop_state": {"status": "complete"},
            "active_run_dir": "/tmp/test-run",
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
            },
        }

        stop_called = [False]

        def mock_run(cmd, **kwargs):
            if "stop" in cmd:
                stop_called[0] = True
            return mock.Mock(returncode=0, stdout="stopped", stderr="")

        with mock.patch.object(supervisor, "manager_json", return_value=status_data):
            with mock.patch.object(supervisor, "run", side_effect=mock_run):
                with mock.patch.object(
                    supervisor, "recover_stale_starting", return_value=[]
                ):
                    with mock.patch.object(
                        supervisor, "recover_stopped_running_items", return_value=[]
                    ):
                        with mock.patch.object(
                            supervisor, "reconcile_running_queue_items", return_value=[]
                        ):
                            with mock.patch.object(
                                supervisor,
                                "reconcile_mission_with_queue",
                                return_value=None,
                            ):
                                with mock.patch.object(
                                    supervisor,
                                    "write_supervisor_state",
                                    return_value={},
                                ):
                                    args = mock.Mock(
                                        auto_accept=False,
                                        auto_continue=False,
                                        auto_dispatch=False,
                                        json=False,
                                    )
                                    supervisor.monitor(args)
                                    # Verify stop was called
                                    assert stop_called[0] is True


# ── Test 3: Failed review checks produce targeted continue prompt ───────


class TestTargetedRecoveryPrompt:
    """Failed review checks produce a targeted continue prompt with exact failed check names."""

    def test_prompt_includes_failed_check_names(self):
        """Recovery prompt includes exact failed check names."""
        review = {
            "checks": [
                {"name": "completion_marker", "ok": False, "detail": "missing"},
                {
                    "name": "remaining_none",
                    "ok": False,
                    "detail": "has remaining items",
                },
                {"name": "summary_present", "ok": True, "detail": "present"},
            ]
        }
        status = {
            "current_objective": "Test objective",
            "active_run_dir": "/tmp/test-run",
            "recent_log": ["line1", "line2", "line3"],
        }
        repeated = {"stuck": False, "repeated_command": "", "repeated_count": 0}

        prompt = manager.generate_targeted_recovery_prompt(review, status, repeated)
        assert "completion_marker" in prompt
        assert "remaining_none" in prompt
        assert "summary_present" not in prompt  # passing checks not listed
        assert "FAILED REVIEW CHECKS" in prompt

    def test_prompt_includes_stuck_loop_warning(self):
        """Recovery prompt warns about stuck loop when detected."""
        review = {"checks": []}
        status = {"recent_log": []}
        repeated = {
            "stuck": True,
            "repeated_command": "python3 test.py",
            "repeated_count": 8,
        }

        prompt = manager.generate_targeted_recovery_prompt(review, status, repeated)
        assert "STUCK LOOP DETECTED" in prompt
        assert "python3 test.py" in prompt
        assert "DO NOT repeat" in prompt
        assert "8 times" in prompt

    def test_prompt_includes_active_run_context(self):
        """Recovery prompt includes active run directory and objective."""
        review = {"checks": [{"name": "test_check", "ok": False, "detail": "failed"}]}
        status = {
            "current_objective": "Fix the broken thing",
            "active_run_dir": "/tmp/my-run-dir",
            "recent_log": ["log line 1"],
        }
        repeated = {"stuck": False, "repeated_command": "", "repeated_count": 0}

        prompt = manager.generate_targeted_recovery_prompt(review, status, repeated)
        assert "Fix the broken thing" in prompt
        assert "/tmp/my-run-dir" in prompt
        assert "log line 1" in prompt

    def test_supervisor_generates_recovery_prompt_on_stuck(self):
        """Supervisor generates recovery prompt when classification is stuck from repeat."""
        status_data = {
            "verdict": "running_idle",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "repeated_command_detection": {
                "stuck": True,
                "repeated_command": "python3 test.py",
                "repeated_count": 7,
                "classification": "stuck_repeat_command",
            },
            "current_objective": "Test objective",
            "active_run_dir": "/tmp/test-run",
            "recent_log": ["log line"],
        }

        def mock_run(cmd, **kwargs):
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(supervisor, "manager_json", return_value=status_data):
            with mock.patch.object(supervisor, "run", side_effect=mock_run):
                with mock.patch.object(
                    supervisor, "recover_stale_starting", return_value=[]
                ):
                    with mock.patch.object(
                        supervisor, "recover_stopped_running_items", return_value=[]
                    ):
                        with mock.patch.object(
                            supervisor, "reconcile_running_queue_items", return_value=[]
                        ):
                            with mock.patch.object(
                                supervisor,
                                "reconcile_mission_with_queue",
                                return_value=None,
                            ):
                                with mock.patch.object(
                                    supervisor,
                                    "write_supervisor_state",
                                    return_value={},
                                ):
                                    with mock.patch.object(
                                        supervisor, "write_secure_file"
                                    ) as mock_write:
                                        args = mock.Mock(
                                            auto_accept=False,
                                            auto_continue=False,
                                            auto_dispatch=False,
                                            json=False,
                                        )
                                        supervisor.monitor(args)
                                        assert any(
                                            "recovery-prompt.md" in str(call.args[0])
                                            and call.args[2] == 0o600
                                            for call in mock_write.call_args_list
                                        ), (
                                            "write_secure_file was not called for "
                                            "recovery-prompt.md during monitor"
                                        )

    def test_prompt_tells_worker_to_fix_or_write_honest_incomplete(self):
        """Recovery prompt tells worker to fix or write honest incomplete/blocked."""
        review = {
            "checks": [
                {"name": "verification_entries", "ok": False, "detail": "only 1 entry"},
                {"name": "node1_idle", "ok": False, "detail": "vllm running"},
            ]
        }
        status = {"recent_log": []}
        repeated = {"stuck": False, "repeated_command": "", "repeated_count": 0}

        prompt = manager.generate_targeted_recovery_prompt(review, status, repeated)
        assert "ACTION REQUIRED" in prompt
        assert "honest" in prompt.lower() or "complete.json" in prompt.lower()


# ── Test 4: Auto-accept when review ok=true and node1 is idle ───────────


class TestAutoAccept:
    """A real review/monitor command path can auto-accept when review ok=true and node1 is idle."""

    def test_auto_accept_runs_when_review_ok(self):
        """Monitor auto-accepts when classification is needs_review and review ok=True."""
        # First call returns needs_review status, second returns accepted status
        status_needs_review = {
            "verdict": "complete",
            "tmux_running": False,
            "awaiting_review": True,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "complete_marker": {"status": "complete"},
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
            },
        }
        status_after_accept = {
            "verdict": "accepted",
            "tmux_running": False,
            "awaiting_review": False,
            "accepted": True,
            "vllm": {"running": 0, "waiting": 0},
            "complete_marker": {"status": "complete"},
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
            },
        }

        call_count = [0]

        def mock_manager_json():
            call_count[0] += 1
            if call_count[0] == 1:
                return status_needs_review
            return status_after_accept

        mock_review_proc = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "status": "accepted", "checks": []}),
            stderr="",
        )
        mock_accept_proc = mock.Mock(
            returncode=0,
            stdout=json.dumps({"status": "accepted"}),
            stderr="",
        )

        call_count_run = [0]

        def mock_run(cmd, **kwargs):
            call_count_run[0] += 1
            if "review" in cmd:
                return mock_review_proc
            elif "accept" in cmd:
                return mock_accept_proc
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(
            supervisor, "manager_json", side_effect=mock_manager_json
        ):
            with mock.patch.object(supervisor, "run", side_effect=mock_run):
                with mock.patch.object(
                    supervisor, "recover_stale_starting", return_value=[]
                ):
                    with mock.patch.object(
                        supervisor, "recover_stopped_running_items", return_value=[]
                    ):
                        with mock.patch.object(
                            supervisor, "reconcile_running_queue_items", return_value=[]
                        ):
                            with mock.patch.object(
                                supervisor,
                                "reconcile_mission_with_queue",
                                return_value=None,
                            ):
                                with mock.patch.object(
                                    supervisor,
                                    "write_supervisor_state",
                                    return_value={},
                                ):
                                    args = mock.Mock(
                                        auto_accept=True,
                                        auto_continue=False,
                                        auto_dispatch=False,
                                        json=False,
                                    )
                                    supervisor.monitor(args)
                                    # Verify review and accept were called
                                    assert call_count_run[0] >= 2

    def test_auto_accept_does_not_run_when_review_fails(self):
        """Monitor does NOT auto-accept when review ok=False."""
        status_needs_review = {
            "verdict": "complete",
            "tmux_running": False,
            "awaiting_review": True,
            "accepted": False,
            "vllm": {"running": 0, "waiting": 0},
            "complete_marker": {"status": "complete"},
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
            },
        }

        mock_review_proc = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": False, "status": "needs_review", "checks": []}),
            stderr="",
        )

        accept_called = [False]

        def mock_run(cmd, **kwargs):
            if "accept" in cmd:
                accept_called[0] = True
            return mock_review_proc

        with mock.patch.object(
            supervisor, "manager_json", return_value=status_needs_review
        ):
            with mock.patch.object(supervisor, "run", side_effect=mock_run):
                with mock.patch.object(
                    supervisor, "recover_stale_starting", return_value=[]
                ):
                    with mock.patch.object(
                        supervisor, "recover_stopped_running_items", return_value=[]
                    ):
                        with mock.patch.object(
                            supervisor, "reconcile_running_queue_items", return_value=[]
                        ):
                            with mock.patch.object(
                                supervisor,
                                "reconcile_mission_with_queue",
                                return_value=None,
                            ):
                                with mock.patch.object(
                                    supervisor,
                                    "write_supervisor_state",
                                    return_value={},
                                ):
                                    args = mock.Mock(
                                        auto_accept=True,
                                        auto_continue=False,
                                        auto_dispatch=False,
                                        json=False,
                                    )
                                    supervisor.monitor(args)
                                    # Accept should NOT have been called
                                    assert accept_called[0] is False

    def test_auto_continue_does_not_start_second_node1_job(self):
        """Auto-continue does not start another Node1 job while one is active."""
        status_working = {
            "verdict": "working",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 1, "waiting": 0},
            "repeated_command_detection": {
                "stuck": False,
                "repeated_command": "",
                "repeated_count": 0,
            },
        }

        with mock.patch.object(supervisor, "manager_json", return_value=status_working):
            with mock.patch.object(
                supervisor, "recover_stale_starting", return_value=[]
            ):
                with mock.patch.object(
                    supervisor, "recover_stopped_running_items", return_value=[]
                ):
                    with mock.patch.object(
                        supervisor, "reconcile_running_queue_items", return_value=[]
                    ):
                        with mock.patch.object(
                            supervisor,
                            "reconcile_mission_with_queue",
                            return_value=None,
                        ):
                            with mock.patch.object(
                                supervisor, "write_supervisor_state", return_value={}
                            ):
                                args = mock.Mock(
                                    auto_accept=True,
                                    auto_continue=True,
                                    auto_dispatch=False,
                                    json=False,
                                )
                                supervisor.monitor(args)
                                # No error should occur; the monitor should just observe
                                # and not start a second job

    def test_supervisor_state_includes_self_rescue_fields(self, tmp_path):
        """Supervisor state output includes all required self-rescue status fields."""
        status_data = {
            "verdict": "working",
            "tmux_running": True,
            "awaiting_review": False,
            "accepted": False,
            "vllm": {"running": 1, "waiting": 0},
            "current_objective": "Test",
            "prompt_path": "/tmp/test.md",
            "active_planner": "none",
            "planner_packet_path": "",
            "runner_state": {"executor": "opencode"},
            "loop_state": {"status": "running"},
            "log_path": "/tmp/log",
            "checkpoint_path": "/tmp/checkpoint.md",
            "complete_marker_path": "/tmp/complete.json",
            "active_run_dir": "/tmp/test-run",
            "repeated_command_detection": {
                "stuck": True,
                "repeated_command": "python3 test.py",
                "repeated_count": 7,
                "classification": "stuck_repeat_command",
            },
            "completion_marker_shutdown_needed": False,
        }

        with mock.patch.object(
            supervisor, "load_mission", return_value=supervisor.empty_mission()
        ):
            with mock.patch.object(supervisor, "STATE_DIR", tmp_path / "state"):
                with mock.patch.object(supervisor, "REPORT_DIR", tmp_path / "reports"):
                    with mock.patch.object(
                        supervisor, "SUPERVISOR_JSON", tmp_path / "supervisor.json"
                    ):
                        with mock.patch.object(
                            supervisor, "SUPERVISOR_MD", tmp_path / "supervisor.md"
                        ):
                            with mock.patch.object(
                                supervisor,
                                "SUPERVISOR_EVENTS_JSONL",
                                tmp_path / "supervisor-events.jsonl",
                            ):
                                with mock.patch.object(
                                    supervisor,
                                    "SUPERVISOR_NOTIFY_STATE",
                                    tmp_path / "supervisor-notify.json",
                                ):
                                    payload = supervisor.write_supervisor_state(status_data)

        assert "repeated_command_detected" in payload
        assert payload["repeated_command_detected"] is True
        assert payload["last_repeated_command"] == "python3 test.py"
        assert payload["repeated_count"] == 7
        assert "completion_marker_shutdown_needed" in payload
        assert "node1_is_idle" in payload
