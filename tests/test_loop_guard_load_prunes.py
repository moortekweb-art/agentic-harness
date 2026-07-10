"""Tests for LoopGuard._load() pruning expired events on load.

The _load() method must prune expired events after loading from disk so that
status() and is_tripped() report the current window, not the stale on-disk
snapshot. Without this, a guard that has been sitting idle past its window
could incorrectly report stale event counts.
"""

from __future__ import annotations

import json


from agentic_harness.core.loop_guard import LoopGuard


def test_load_prunes_expired_events(tmp_path) -> None:
    """_load() must prune events older than the window when loading from disk."""
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"events": [10.0, 20.0, 30.0]}), encoding="utf-8")

    # Clock is far in the future: all events are > 10s old
    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    # All events should be pruned
    assert len(guard._events) == 0
    status = guard.status()
    assert status["events_total"] == 0
    assert status["events_in_window"] == 0
    assert status["remaining"] == 5
    assert status["tripped"] is False


def test_load_keeps_fresh_events(tmp_path) -> None:
    """_load() must keep events within the window."""
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"events": [990.0, 995.0, 999.0]}), encoding="utf-8")

    # Clock is at 1000: all events are within 10s window
    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    assert len(guard._events) == 3
    status = guard.status()
    assert status["events_total"] == 3
    assert status["events_in_window"] == 3
    assert status["remaining"] == 2
    assert status["tripped"] is False


def test_load_keeps_boundary_events(tmp_path) -> None:
    """_load() must keep events exactly at the window boundary."""
    path = tmp_path / "guard.json"
    # Event at exactly window_seconds ago should be kept (not > window)
    path.write_text(json.dumps({"events": [990.0]}), encoding="utf-8")

    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    # 1000.0 - 990.0 = 10.0, which is == window_seconds, not > window_seconds
    assert len(guard._events) == 1
    status = guard.status()
    assert status["events_in_window"] == 1


def test_load_prunes_mixed_fresh_and_stale(tmp_path) -> None:
    """_load() must prune only stale events, keeping fresh ones."""
    path = tmp_path / "guard.json"
    # Mix: some expired, some fresh
    path.write_text(json.dumps({"events": [10.0, 500.0, 995.0]}), encoding="utf-8")

    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    # 10.0 is expired (990s ago), 500.0 is expired (500s ago), 995.0 is fresh (5s ago)
    assert len(guard._events) == 1
    assert list(guard._events) == [995.0]
    status = guard.status()
    assert status["events_total"] == 1
    assert status["events_in_window"] == 1


def test_load_nonexistent_file_noop(tmp_path) -> None:
    """_load() must be a no-op when the state file doesn't exist."""
    path = tmp_path / "nonexistent" / "guard.json"
    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 100.0)
    guard._load()

    assert len(guard._events) == 0


def test_load_corrupt_file_noop(tmp_path) -> None:
    """_load() must be a no-op when the state file contains corrupt JSON."""
    path = tmp_path / "guard.json"
    path.write_text("not valid json{{{", encoding="utf-8")

    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 100.0)
    guard._load()

    assert len(guard._events) == 0


def test_record_continue_after_load_prunes_and_records(tmp_path) -> None:
    """record_continue() after _load() must prune expired events, then record."""
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"events": [10.0, 20.0, 30.0]}), encoding="utf-8")

    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    # Now record a new event — should succeed since expired events were pruned
    guard.record_continue()
    assert len(guard._events) == 1
    status = guard.status()
    assert status["events_in_window"] == 1
    assert status["remaining"] == 4


def test_load_preserves_filtered_events(tmp_path) -> None:
    """_load() must still filter non-finite and boolean events after pruning."""
    path = tmp_path / "guard.json"
    # Mix: 995.0 is fresh (5s ago), inf/True/nan should be filtered
    path.write_text(
        json.dumps({"events": [10.0, float("inf"), True, 995.0, float("nan")]}),
        encoding="utf-8",
    )

    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=path, clock=lambda: 1000.0)
    guard._load()

    # inf, True, nan should be filtered; 10.0 should be pruned (expired)
    # 995.0 is fresh (5s ago, within 10s window)
    assert len(guard._events) == 1
    assert list(guard._events) == [995.0]


def test_loop_guard_save_failure_does_not_crash(tmp_path) -> None:
    """Verify that _save() failure (e.g., disk full) does not crash the guard."""
    from agentic_harness.core.loop_guard import LoopGuard

    state_path = tmp_path / "guard.json"
    guard = LoopGuard(max_continues=5, window_seconds=10.0, state_path=state_path)

    # Make the parent directory read-only to simulate write failure
    state_path.parent.chmod(0o555)

    try:
        # This should not raise, even though save fails
        guard.record_continue()
        # The in-memory state should still be valid
        assert len(guard._events) == 1
    finally:
        # Restore permissions for cleanup
        state_path.parent.chmod(0o755)
