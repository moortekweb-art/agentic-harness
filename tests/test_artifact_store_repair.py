"""Tests for ArtifactStore.repair_current_marker reliability.

repair_current_marker must use the goal's updated_at timestamp to determine
which run was most recently worked on, not st_mtime. Filesystem modification
time can be wrong after copies, restores, or VCS operations.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.state import Goal, GoalStatus, SCHEMA_VERSION


def _write_goal_state(
    run_dir: Path, objective: str, updated_at: str, goal_id: str | None = None
) -> Path:
    """Write a state.json with controlled updated_at timestamp."""
    goal = Goal(objective=objective, status=GoalStatus.IN_PROGRESS)
    goal.id = goal_id or goal.id
    goal.updated_at = updated_at
    goal.created_at = "2026-07-05T00:00:00Z"
    state_path = run_dir / "state.json"
    state_path.write_text(json.dumps(goal.to_dict()))
    return state_path


class TestRepairCurrentMarkerUsesUpdatedAt:
    """repair_current_marker must pick the goal with the newest updated_at, not newest mtime."""

    def test_picks_newest_updated_at_over_newest_mtime(self, tmp_path: Path) -> None:
        """When an older goal has a newer mtime (e.g. after copy), repair should still pick the
        goal with the newer updated_at timestamp."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        # Goal A: newer updated_at but older mtime
        a_dir = tmp_path / "runs" / "goal-a"
        a_dir.mkdir(parents=True)
        _write_goal_state(a_dir, "newer timestamp", "2026-07-06T10:00:00Z", goal_id="goal-a")

        # Goal B: older updated_at but newer mtime (simulating a copy/restore)
        b_dir = tmp_path / "runs" / "goal-b"
        b_dir.mkdir(parents=True)
        _write_goal_state(b_dir, "older timestamp", "2026-07-05T10:00:00Z", goal_id="goal-b")

        # Make goal-b's file have a newer mtime than goal-a
        time.sleep(0.1)
        os.utime(b_dir / "state.json", None)

        # current.json does not exist
        assert not (tmp_path / "current.json").exists()

        result = store.repair_current_marker()
        assert result is not None
        assert result.id == "goal-a", (
            f"Expected goal-a (newer updated_at), got {result.id} ({result.objective})"
        )

    def test_picks_newest_updated_at_among_many(self, tmp_path: Path) -> None:
        """With multiple goals, repair should pick the one with the newest updated_at."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        timestamps = [
            ("2026-07-01T00:00:00Z", "oldest"),
            ("2026-07-03T00:00:00Z", "middle"),
            ("2026-07-06T12:00:00Z", "newest"),
            ("2026-07-05T00:00:00Z", "second-newest"),
        ]
        for ts, obj in timestamps:
            goal_dir = tmp_path / "runs" / f"goal-{obj}"
            goal_dir.mkdir(parents=True)
            _write_goal_state(goal_dir, obj, ts, goal_id=f"goal-{obj}")

        # Make all files have the same mtime (current time) to remove mtime as a factor
        now = time.time()
        for f in (tmp_path / "runs").glob("*/state.json"):
            os.utime(f, (now, now))

        result = store.repair_current_marker()
        assert result is not None
        assert result.id == "goal-newest", f"Expected goal-newest, got {result.id}"

    def test_returns_none_when_no_runs_exist(self, tmp_path: Path) -> None:
        """With no runs, repair should return None."""
        store = ArtifactStore(root=tmp_path)
        store.init()
        result = store.repair_current_marker()
        assert result is None

    def test_returns_none_when_all_states_corrupt(self, tmp_path: Path) -> None:
        """If all state files are corrupt JSON, repair should return None."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        bad_dir = tmp_path / "runs" / "bad-goal"
        bad_dir.mkdir(parents=True)
        (bad_dir / "state.json").write_text("not valid json {{{")

        result = store.repair_current_marker()
        assert result is None

    def test_returns_none_when_states_missing_required_fields(self, tmp_path: Path) -> None:
        """If state files lack updated_at or id, they should be skipped."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        # State without updated_at
        bad_dir = tmp_path / "runs" / "bad-goal"
        bad_dir.mkdir(parents=True)
        bad_goal = Goal(objective="bad", status=GoalStatus.PENDING)
        bad_goal.id = "bad-goal"
        del bad_goal  # remove updated_at by writing minimal dict
        (bad_dir / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "id": "bad-goal",
                    "objective": "bad",
                    "status": "pending",
                    "created_at": "2026-07-05T00:00:00Z",
                }
            )
        )

        # Valid state
        good_dir = tmp_path / "runs" / "good-goal"
        good_dir.mkdir(parents=True)
        _write_goal_state(good_dir, "good", "2026-07-06T10:00:00Z", goal_id="good-goal")

        result = store.repair_current_marker()
        assert result is not None
        assert result.id == "good-goal"

    def test_preserves_existing_current_marker(self, tmp_path: Path) -> None:
        """If current.json already exists, repair should read it directly."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        goal = Goal(objective="existing", status=GoalStatus.IN_PROGRESS)
        goal.id = "existing-goal"
        goal.updated_at = "2026-07-06T10:00:00Z"
        goal.created_at = "2026-07-05T00:00:00Z"
        run_dir = tmp_path / "runs" / "existing-goal"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps(goal.to_dict()))

        # Pre-write current.json
        current = tmp_path / "current.json"
        current.write_text(json.dumps({"goal_id": "existing-goal"}))

        result = store.repair_current_marker()
        assert result is not None
        assert result.id == "existing-goal"

    def test_writes_current_marker_on_success(self, tmp_path: Path) -> None:
        """After repair, current.json should be written with the selected goal_id."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        goal_dir = tmp_path / "runs" / "repair-target"
        goal_dir.mkdir(parents=True)
        _write_goal_state(goal_dir, "repair me", "2026-07-06T10:00:00Z", goal_id="repair-target")

        result = store.repair_current_marker()
        assert result is not None

        current = tmp_path / "current.json"
        assert current.exists()
        payload = json.loads(current.read_text())
        assert payload == {"goal_id": "repair-target"}

    def test_handles_duplicate_updated_at_picks_first(self, tmp_path: Path) -> None:
        """When two goals share the same updated_at, sort is stable so the first
        encountered (alphabetical by path) wins. This is deterministic behavior."""
        store = ArtifactStore(root=tmp_path)
        store.init()

        ts = "2026-07-06T12:00:00Z"
        for name in ["alpha-goal", "beta-goal"]:
            goal_dir = tmp_path / "runs" / name
            goal_dir.mkdir(parents=True)
            _write_goal_state(goal_dir, name, ts, goal_id=name)

        result = store.repair_current_marker()
        assert result is not None
        # Both have same timestamp, so the first sorted one wins
        assert result.id in ("alpha-goal", "beta-goal")
