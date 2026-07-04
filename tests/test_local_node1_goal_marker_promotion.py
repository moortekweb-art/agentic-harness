#!/usr/bin/env python3
"""Tests for the run-local completion marker promotion fix.

Verifies that when a worker writes complete.json inside the active run
directory, the manager promotes it to the global COMPLETE_MARKER path
and the loop stops cleanly without starting another iteration.

These tests use temp files and do NOT start tmux, OpenCode, Qwen, or vLLM.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

_SCRIPTS_DIR = "/mnt/raid0/documentation/scripts"
_MANAGER_PATH = Path(_SCRIPTS_DIR) / "local-node1-goal-manager.py"

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "local_node1_goal_manager", str(_MANAGER_PATH)
)
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _make_complete_marker(status: str = "complete", **extra) -> dict:
    marker = {
        "status": status,
        "completed_at": _utc_now(),
        "summary": "Run-local completion marker promotion verified.",
        "verification": [
            "pytest marker promotion tests passed",
            "manager promoted run-local marker successfully",
            "global completion marker reflected completed status",
        ],
        "remaining": "none",
    }
    marker.update(extra)
    return marker


class TestPromoteRunLocalMarker:
    """promote_run_local_marker copies run-local complete.json to global marker."""

    def _setup_dirs(self, tmp_path):
        """Set up a temporary state dir with active-run.json pointing to a run dir."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        runs_dir = state_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260623T025522Z-test-run"
        run_dir.mkdir()
        active_run_index = {
            "active_run_id": run_dir.name,
            "active_run_dir": str(run_dir),
            "active_since": _utc_now(),
        }
        (state_dir / "active-run.json").write_text(json.dumps(active_run_index))
        return state_dir, run_dir

    def test_promotes_run_local_marker_to_global(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker()
        (run_dir / "complete.json").write_text(json.dumps(complete_data))

        global_marker = state_dir / "complete.json"
        assert not global_marker.exists()

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is True
        assert global_marker.exists()
        global_data = json.loads(global_marker.read_text())
        assert global_data["status"] == "complete"

    def test_no_promotion_when_no_active_run(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is False

    def test_no_promotion_when_run_local_marker_missing(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is False
        assert not global_marker.exists()

    def test_no_promotion_when_run_local_marker_not_complete(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        incomplete_data = _make_complete_marker(status="partial")
        (run_dir / "complete.json").write_text(json.dumps(incomplete_data))
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is False
        assert not global_marker.exists()

    def test_no_promotion_when_run_local_marker_is_synthetic_test_marker(
        self, tmp_path
    ):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        synthetic_data = _make_complete_marker(
            summary="test: run-local marker promotion verification",
            verification=["test1", "test2", "test3"],
        )
        (run_dir / "complete.json").write_text(json.dumps(synthetic_data))
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is False
        assert not global_marker.exists()

    def test_no_promotion_when_global_marker_already_complete(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker()
        (run_dir / "complete.json").write_text(json.dumps(complete_data))
        global_marker = state_dir / "complete.json"
        global_marker.write_text(json.dumps(_make_complete_marker()))

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is False

    def test_promotes_when_global_marker_missing(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker(summary="run-local completion")
        (run_dir / "complete.json").write_text(json.dumps(complete_data))
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is True
        global_data = json.loads(global_marker.read_text())
        assert global_data["status"] == "complete"
        assert global_data["summary"] == "run-local completion"

    def test_promotes_when_global_marker_not_complete(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker(summary="run-local completion")
        (run_dir / "complete.json").write_text(json.dumps(complete_data))
        global_marker = state_dir / "complete.json"
        # Global marker exists but is not "complete"
        global_marker.write_text(json.dumps({"status": "partial"}))

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    result = manager.promote_run_local_marker()

        assert result is True
        global_data = json.loads(global_marker.read_text())
        assert global_data["status"] == "complete"

    def test_preserves_all_complete_marker_fields(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker(
            summary="detailed summary",
            verification=["v1", "v2", "v3"],
            remaining="none",
        )
        (run_dir / "complete.json").write_text(json.dumps(complete_data))
        global_marker = state_dir / "complete.json"

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    manager.promote_run_local_marker()

        global_data = json.loads(global_marker.read_text())
        assert global_data["summary"] == "detailed summary"
        assert global_data["verification"] == ["v1", "v2", "v3"]
        assert global_data["remaining"] == "none"


class TestBuildStatusPromotesRunLocalMarker:
    """build_status calls promote_run_local_marker before reading global marker."""

    def _setup_dirs(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        runs_dir = state_dir / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "20260623T025522Z-test-run"
        run_dir.mkdir()
        active_run_index = {
            "active_run_id": run_dir.name,
            "active_run_dir": str(run_dir),
            "active_since": _utc_now(),
        }
        (state_dir / "active-run.json").write_text(json.dumps(active_run_index))

        # Write minimal state files needed by build_status
        (state_dir / "state.json").write_text(
            json.dumps(
                {
                    "prompt_file": str(tmp_path / "prompt.md"),
                }
            )
        )
        (state_dir / "loop-state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "iteration": 1,
                    "prompt_file": str(tmp_path / "prompt.md"),
                    "complete_marker": str(state_dir / "complete.json"),
                    "max_iterations": 24,
                    "iteration_wall_time": "6h",
                    "max_wall_time": "72h",
                    "executor": "opencode",
                    "qwen_model": "local-node1-vllm",
                    "opencode_model": "litellm-gateway/local-node1-vllm",
                }
            )
        )
        (tmp_path / "prompt.md").write_text("Test prompt\n")
        (state_dir / "session.log").write_text("")
        (state_dir / "checkpoints.md").write_text("# Checkpoints\n")

        return state_dir, run_dir

    def test_build_status_promotes_and_sees_complete(self, tmp_path):
        state_dir, run_dir = self._setup_dirs(tmp_path)
        complete_data = _make_complete_marker()
        (run_dir / "complete.json").write_text(json.dumps(complete_data))
        global_marker = state_dir / "complete.json"
        assert not global_marker.exists()

        with mock.patch.object(manager, "STATE_DIR", state_dir):
            with mock.patch.object(manager, "COMPLETE_MARKER", global_marker):
                with mock.patch.object(
                    manager, "ACTIVE_RUN_INDEX", state_dir / "active-run.json"
                ):
                    with mock.patch.object(manager, "tmux_running", return_value=False):
                        with mock.patch.object(
                            manager,
                            "vllm_status",
                            return_value={
                                "vllm_healthy": False,
                                "vllm_running": 0,
                                "vllm_waiting": 0,
                                "node1_gpu": None,
                            },
                        ):
                            with mock.patch.object(
                                manager,
                                "vllm_liveness_check",
                                return_value={"ok": True},
                            ):
                                status = manager.build_status()

        # Global marker should have been promoted
        assert global_marker.exists()
        # Status should reflect completion
        assert status["complete_marker"]["status"] == "complete"
        # Verdict should be "complete" (not "running")
        assert status["verdict"] == "complete"
