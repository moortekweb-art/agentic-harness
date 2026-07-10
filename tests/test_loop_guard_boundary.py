"""Tests for LoopGuard boundary condition: should trip at exactly max_continues."""

import pytest

from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.errors import LoopGuardTripped


def test_trips_at_exactly_max_continues():
    """When we record max_continues events, the guard should trip on the max_continues-th event."""
    clock = [100.0]
    guard = LoopGuard(max_continues=3, window_seconds=300.0, clock=lambda: clock[0])
    for i in range(3):
        if i < 2:
            guard.record_continue()
        else:
            with pytest.raises(LoopGuardTripped):
                guard.record_continue()


def test_does_not_trip_before_max_continues():
    """Recording fewer than max_continues events should not trip."""
    clock = [100.0]
    guard = LoopGuard(max_continues=3, window_seconds=300.0, clock=lambda: clock[0])
    for i in range(2):
        guard.record_continue()


def test_trips_immediately_on_first_event_if_max_is_one():
    """If max_continues=1, recording one event should trip."""
    clock = [100.0]
    guard = LoopGuard(max_continues=1, window_seconds=300.0, clock=lambda: clock[0])
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_trips_after_pruning_stale_events():
    """After pruning expired events, recording enough fresh events should trip."""
    clock = [100.0]
    guard = LoopGuard(max_continues=2, window_seconds=50.0, clock=lambda: clock[0])
    # Record 1 event (should not trip)
    guard.record_continue()
    # Now advance time past the window so the event expires
    clock[0] = 200.0
    # Record 2 fresh events - should trip on the second
    guard.record_continue()
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_load_prunes_out_of_order_events():
    """Events loaded from JSON that are out of order must be sorted before pruning."""
    import json

    from agentic_harness.core.loop_guard import LoopGuard

    state_path = state_path_fixture()
    # Write events in reverse order
    state_path.write_text(
        json.dumps({"events": [99.0, 95.0, 97.0, 101.0]}),
        encoding="utf-8",
    )
    clock = [105.0]
    guard = LoopGuard(
        max_continues=1,
        window_seconds=60.0,
        state_path=state_path,
        clock=lambda: clock[0],
    )
    guard._load()

    # All events are within the window (4, 10, 8, 4 seconds ago)
    # max_continues=1, so 4 events should trip
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_load_prunes_out_of_order_events_with_expiry():
    """Out-of-order events: older ones should be pruned correctly."""
    import json

    from agentic_harness.core.loop_guard import LoopGuard

    state_path = state_path_fixture()
    # Write events in reverse order: old event at 49.0, recent at 100.0
    state_path.write_text(
        json.dumps({"events": [100.0, 49.0]}),
        encoding="utf-8",
    )
    clock = [110.0]
    # max_continues=3 so that 1 persisted (after prune) + 1 new = 2 < 3
    guard = LoopGuard(
        max_continues=3,
        window_seconds=60.0,
        state_path=state_path,
        clock=lambda: clock[0],
    )
    guard._load()

    # After sort + prune at 110: 49.0 is 61s ago (expired), 100.0 is 10s ago (kept).
    # So 1 event in window. max_continues=3, so recording 1 event (total 2) does NOT trip.
    guard.record_continue()  # 2 events in window (100.0, 110.0)
    # Now 2 events in window, still < 3, so recording another trips at 3
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def state_path_fixture():
    """Create a temp state path for loop guard tests."""
    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp(prefix="lg-test-")
    return Path(tmp) / "guard.json"
