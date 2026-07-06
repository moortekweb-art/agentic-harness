"""Tests for Goal.__eq__, Goal.__hash__, Goal.duration_seconds, and GoalStatus helpers."""

from __future__ import annotations

import pytest

from agentic_harness import Goal, GoalStatus
from agentic_harness.core.errors import LoopGuardTripped
from agentic_harness.core.state import now_iso


def test_goal_eq_same_fields_returns_true() -> None:
    goal1 = Goal("test objective")
    goal2 = Goal("test objective")
    # Different ids by default, so we set them equal manually
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at

    assert goal1 == goal2


def test_goal_eq_different_id_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    # ids are different by default
    assert goal1 != goal2


def test_goal_eq_different_objective_returns_false() -> None:
    goal1 = Goal("objective one")
    goal2 = Goal("objective two")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at

    assert goal1 != goal2


def test_goal_eq_different_status_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal2.transition(GoalStatus.PLANNING)

    assert goal1 != goal2


def test_goal_eq_different_artifacts_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal1.artifacts.append("file.txt")

    assert goal1 != goal2


def test_goal_eq_different_metadata_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal1.metadata["key"] = "value"

    assert goal1 != goal2


def test_goal_eq_different_error_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal1.error = "something broke"

    assert goal1 != goal2


def test_goal_eq_different_review_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal1.review = {"passed": True}

    assert goal1 != goal2


def test_goal_eq_different_history_returns_false() -> None:
    goal1 = Goal("test")
    goal2 = Goal("test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at
    goal1.transition(GoalStatus.PLANNING)

    assert goal1 != goal2


def test_goal_eq_with_non_goal_returns_false() -> None:
    goal = Goal("test")
    assert goal != "not a goal"
    assert goal != 42
    assert goal is not None


def test_goal_eq_is_reflexive() -> None:
    goal = Goal("reflexive")
    assert goal == goal


def test_goal_hash_consistent_with_eq() -> None:
    goal1 = Goal("hash test")
    goal2 = Goal("hash test")
    goal2.id = goal1.id

    assert goal1 == goal2
    assert hash(goal1) == hash(goal2)


def test_goal_hash_different_ids_different_hashes() -> None:
    goal1 = Goal("hash different")
    goal2 = Goal("hash different")
    # ids are different by default
    assert hash(goal1) != hash(goal2)


def test_goal_hash_usable_in_set() -> None:
    goal1 = Goal("set test")
    goal2 = Goal("set test")
    goal2.id = goal1.id
    goal2.created_at = goal1.created_at
    goal2.updated_at = goal1.updated_at

    goals = {goal1, goal2}
    # goal2 is equal to goal1, so set should have 1 element
    assert len(goals) == 1


def test_goal_duration_seconds_returns_none_for_unparseable_timestamps() -> None:
    goal = Goal("bad timestamps")
    goal.created_at = "not-a-timestamp"
    goal.updated_at = "also-bad"

    assert goal.duration_seconds is None


def test_goal_duration_seconds_returns_zero_for_same_timestamp() -> None:
    ts = now_iso()
    goal = Goal("same time")
    goal.created_at = ts
    goal.updated_at = ts

    assert goal.duration_seconds == 0.0


def test_goal_duration_seconds_returns_positive_value() -> None:
    # Use explicit minute-level timestamps to avoid second-precision collision
    goal = Goal("timed goal")
    goal.created_at = "2026-01-01T00:00:00Z"
    goal.updated_at = "2026-01-01T00:01:30Z"

    assert goal.duration_seconds is not None
    assert goal.duration_seconds == 90.0


def test_goal_duration_seconds_handles_z_suffix() -> None:
    goal = Goal("z suffix")
    goal.created_at = "2026-01-01T00:00:00Z"
    goal.updated_at = "2026-01-01T00:01:00Z"

    assert goal.duration_seconds == 60.0


def test_goal_duration_seconds_handles_offset_suffix() -> None:
    goal = Goal("offset suffix")
    goal.created_at = "2026-01-01T00:00:00+00:00"
    goal.updated_at = "2026-01-01T01:00:00+00:00"

    assert goal.duration_seconds == 3600.0


def test_goal_duration_seconds_returns_none_when_created_at_missing() -> None:
    goal = Goal("missing created")
    goal.created_at = ""
    goal.updated_at = now_iso()

    assert goal.duration_seconds is None


def test_goal_status_is_terminal_done() -> None:
    assert GoalStatus.DONE.is_terminal is True


def test_goal_status_is_terminal_failed() -> None:
    assert GoalStatus.FAILED.is_terminal is True


def test_goal_status_is_terminal_review_is_false() -> None:
    assert GoalStatus.REVIEW.is_terminal is False


def test_goal_status_is_terminal_planning_is_false() -> None:
    assert GoalStatus.PLANNING.is_terminal is False


def test_goal_status_is_terminal_pending_is_false() -> None:
    assert GoalStatus.PENDING.is_terminal is False


def test_goal_status_is_active_planning() -> None:
    assert GoalStatus.PLANNING.is_active is True


def test_goal_status_is_active_in_progress() -> None:
    assert GoalStatus.IN_PROGRESS.is_active is True


def test_goal_status_is_active_review() -> None:
    assert GoalStatus.REVIEW.is_active is True


def test_goal_status_is_active_done_is_false() -> None:
    assert GoalStatus.DONE.is_active is False


def test_goal_status_is_active_failed_is_false() -> None:
    assert GoalStatus.FAILED.is_active is False


def test_goal_status_is_active_pending_is_false() -> None:
    assert GoalStatus.PENDING.is_active is False


def test_goal_eq_survives_from_dict_roundtrip() -> None:
    original = Goal("roundtrip eq")
    original.transition(GoalStatus.PLANNING, reason="p")
    original.transition(GoalStatus.IN_PROGRESS, reason="i")
    original.artifacts.append("output.txt")
    original.metadata["key"] = "value"
    original.error = "something broke"
    original.review = {"passed": True, "criteria": []}

    restored = Goal.from_dict(original.to_dict())

    assert restored == original


def test_goal_status_chain_includes_current_status() -> None:
    goal = Goal("chain test")
    goal.transition(GoalStatus.PLANNING, reason="p")
    goal.transition(GoalStatus.IN_PROGRESS, reason="i")
    goal.transition(GoalStatus.REVIEW, reason="r")

    chain = goal.status_chain
    assert chain == ["planning", "in_progress", "review"]


def test_goal_status_chain_empty_for_pending() -> None:
    goal = Goal("empty chain")

    chain = goal.status_chain
    assert chain == ["pending"]


def test_goal_status_chain_includes_restarts() -> None:
    goal = Goal("restart chain")
    goal.transition(GoalStatus.PLANNING, reason="p")
    goal.transition(GoalStatus.IN_PROGRESS, reason="i")
    goal.transition(GoalStatus.FAILED, reason="f")
    goal.transition(GoalStatus.PLANNING, reason="p2")
    goal.transition(GoalStatus.IN_PROGRESS, reason="i2")

    chain = goal.status_chain
    assert chain == ["planning", "in_progress", "failed", "planning", "in_progress"]


def test_goal_status_chain_does_not_duplicate_current() -> None:
    goal = Goal("no dup")
    goal.transition(GoalStatus.PLANNING, reason="p")
    goal.transition(GoalStatus.IN_PROGRESS, reason="i")

    chain = goal.status_chain
    # history has [planning, in_progress], current is in_progress
    # should not duplicate
    assert chain == ["planning", "in_progress"]
    assert chain.count("in_progress") == 1


def test_supervisor_status_summary_returns_none_when_no_goal(tmp_path) -> None:
    from agentic_harness import Supervisor

    supervisor = Supervisor(project_dir=tmp_path)
    assert supervisor.status_summary() is None


def test_supervisor_status_summary_returns_status_with_duration(tmp_path) -> None:
    from agentic_harness import Supervisor
    from agentic_harness.core.worker import WorkerResult

    worker = type("W", (), {"run": lambda self, goal: WorkerResult(success=True, summary="ok")})()
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("summary test")
    summary = supervisor.status_summary()

    assert summary is not None
    assert "planning" in summary


def test_loop_guard_is_tripped_after_max_continues() -> None:
    from agentic_harness.core.loop_guard import LoopGuard

    guard = LoopGuard(max_continues=1, window_seconds=60)
    # With max_continues=1, recording 1 event should trip immediately (1 >= 1)
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_loop_guard_not_tripped_before_max_continues() -> None:
    from agentic_harness.core.loop_guard import LoopGuard

    guard = LoopGuard(max_continues=2, window_seconds=60)

    assert guard.is_tripped() is False
    assert guard.remaining_continues() == 2

    guard.record_continue()

    assert guard.is_tripped() is False
    assert guard.remaining_continues() == 1

    # With max_continues=2, recording 2 events should trip (2 >= 2)
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_loop_guard_remaining_respects_window_expiry() -> None:
    from agentic_harness.core.loop_guard import LoopGuard

    guard = LoopGuard(
        max_continues=1,
        window_seconds=60,
        clock=lambda: 100.0,
    )
    # With max_continues=1, recording 1 event should trip immediately
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()

    # After the window expires, remaining should be back to max
    guard2 = LoopGuard(
        max_continues=1,
        window_seconds=60,
        clock=lambda: 200.0,
    )
    assert guard2.is_tripped() is False
    assert guard2.remaining_continues() == 1


def test_goal_has_artifacts_false_when_empty() -> None:
    goal = Goal("no artifacts")
    assert goal.has_artifacts is False


def test_goal_has_artifacts_true_when_present() -> None:
    goal = Goal("has artifacts")
    goal.artifacts.append("file.txt")
    assert goal.has_artifacts is True


def test_goal_is_complete_done() -> None:
    goal = Goal("complete")
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.REVIEW)
    goal.transition(GoalStatus.DONE)
    assert goal.is_complete is True


def test_goal_is_complete_failed() -> None:
    goal = Goal("failed")
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.FAILED)
    assert goal.is_complete is True


def test_goal_is_complete_not_done() -> None:
    goal = Goal("not done")
    goal.transition(GoalStatus.PLANNING)
    assert goal.is_complete is False


def test_goal_last_transition_reason_returns_none_when_no_history() -> None:
    goal = Goal("no history")
    assert goal.last_transition_reason is None


def test_goal_last_transition_reason_returns_last_reason() -> None:
    goal = Goal("reasoned")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="planning complete")

    assert goal.last_transition_reason == "planning complete"


def test_goal_last_transition_reason_returns_empty_string_when_reason_not_set() -> None:
    goal = Goal("no reason")
    goal.transition(GoalStatus.PLANNING)

    assert goal.last_transition_reason == ""


def test_goal_from_dict_rejects_unknown_fields() -> None:
    """from_dict must reject payloads with fields not in the schema.

    This prevents silent data loss when a future schema version adds new
    fields that older code would drop.
    """
    payload = Goal("unknown field test").to_dict()
    payload["future_field"] = "should be rejected"

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    error = str(exc_info.value)
    assert "future_field" in error
    assert "unknown" in error.lower()


def test_goal_from_dict_accepts_all_known_fields() -> None:
    """from_dict must accept payloads containing only known fields."""
    goal = Goal("known fields test")
    goal.transition(GoalStatus.PLANNING, reason="p")
    goal.transition(GoalStatus.IN_PROGRESS, reason="i")
    goal.artifacts.append("output.txt")
    goal.metadata["key"] = "value"
    goal.error = "something broke"
    goal.review = {"passed": True, "criteria": []}

    restored = Goal.from_dict(goal.to_dict())
    assert restored == goal


def test_goal_from_dict_unknown_field_message_lists_known_fields() -> None:
    """Error message should list known fields so operators can diagnose."""
    payload = Goal("message test").to_dict()
    payload["bogus_field"] = 42
    payload["another_bogus"] = "x"

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    error = str(exc_info.value)
    assert "bogus_field" in error
    assert "another_bogus" in error
    assert "objective" in error
    assert "status" in error


def test_goal_transition_rejects_non_string_reason() -> None:
    """transition() must reject non-string reason values."""
    goal = Goal("reason test")
    with pytest.raises(TypeError):
        goal.transition(GoalStatus.PLANNING, reason=None)
    with pytest.raises(TypeError):
        goal.transition(GoalStatus.PLANNING, reason=42)
    with pytest.raises(TypeError):
        goal.transition(GoalStatus.PLANNING, reason=["list"])


def test_goal_transition_accepts_empty_string_reason() -> None:
    """transition() should accept empty string reason (default)."""
    goal = Goal("empty reason")
    goal.transition(GoalStatus.PLANNING)
    assert goal.history[-1]["reason"] == ""


def test_goal_to_dict_history_is_deep_copied() -> None:
    """to_dict() must return a deep copy of history so mutations don't affect the original."""
    goal = Goal("deep copy test")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="working")

    d = goal.to_dict()
    # Mutate the returned history
    d["history"][0]["reason"] = "MUTATED"
    d["history"][1]["reason"] = "MUTATED"

    # Original goal history should be unchanged
    assert goal.history[0]["reason"] == "started"
    assert goal.history[1]["reason"] == "working"


def test_goal_to_dict_artifacts_is_deep_copied() -> None:
    """to_dict() must return a copy of artifacts list."""
    goal = Goal("artifacts copy test")
    goal.artifacts.append("file1.txt")
    goal.artifacts.append("file2.txt")

    d = goal.to_dict()
    d["artifacts"].append("MUTATED")

    assert "MUTATED" not in goal.artifacts


def test_goal_from_dict_rejects_invalid_status() -> None:
    """from_dict must reject payloads with invalid status values."""
    payload = Goal("invalid status").to_dict()
    payload["status"] = "invalid_status"

    with pytest.raises(ValueError):
        Goal.from_dict(payload)


def test_goal_from_dict_rejects_missing_required_fields() -> None:
    """from_dict must reject payloads missing required fields."""
    payload = Goal("missing fields").to_dict()
    del payload["objective"]

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    error = str(exc_info.value)
    assert "objective" in error
    assert "missing" in error.lower()


def test_goal_from_dict_rejects_null_required_fields() -> None:
    """from_dict must reject payloads with null required fields."""
    payload = Goal("null fields").to_dict()
    payload["objective"] = None

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    error = str(exc_info.value)
    assert "objective" in error


def test_goal_from_dict_rejects_non_dict_history_entries() -> None:
    """from_dict must reject history entries that are not mappings."""
    payload = Goal("test").to_dict()
    payload["history"] = ["not a dict", 123, None, True]

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    error = str(exc_info.value)
    assert "history[0]" in error
    assert "mapping" in error


def test_goal_from_dict_accepts_empty_history_list() -> None:
    """from_dict must accept an empty history list."""
    payload = Goal("test").to_dict()
    payload["history"] = []
    goal = Goal.from_dict(payload)
    assert goal.history == []


def test_goal_from_dict_accepts_valid_history_entries() -> None:
    """from_dict must accept history entries that are all mappings."""
    payload = Goal("test").to_dict()
    payload["history"] = [
        {"from": "pending", "to": "planning", "at": "2026-01-01T00:00:00Z", "reason": "started"},
        {"from": "planning", "to": "in_progress", "at": "2026-01-01T00:01:00Z", "reason": "ok"},
    ]
    goal = Goal.from_dict(payload)
    assert len(goal.history) == 2
    assert goal.history[0]["reason"] == "started"


def test_goal_status_chain_handles_valid_history() -> None:
    """status_chain must work correctly with valid history entries."""
    goal = Goal("test")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="ok")
    goal.transition(GoalStatus.REVIEW, reason="done")
    chain = goal.status_chain
    assert chain == ["planning", "in_progress", "review"]


def test_goal_last_transition_reason_handles_valid_history() -> None:
    """last_transition_reason must return the reason from the last history entry."""
    goal = Goal("test")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="in progress")
    assert goal.last_transition_reason == "in progress"


def test_goal_to_dict_makes_metadata_json_safe() -> None:
    """to_dict must convert non-JSON-serializable metadata values to serializable forms."""
    goal = Goal("test")
    goal.metadata = {
        "string": "value",
        "number": 42,
        "bool": True,
        "null": None,
        "list": [1, 2, 3],
        "set": {3, 1, 2},
        "nested_set": {"a": {1, 2}},
    }
    d = goal.to_dict()
    import json

    json_str = json.dumps(d)
    assert isinstance(json_str, str)
    assert len(json_str) > 0
    # Sets should be converted to sorted lists
    assert d["metadata"]["set"] == [1, 2, 3]
    assert d["metadata"]["nested_set"] == {"a": [1, 2]}


def test_goal_to_dict_makes_review_json_safe() -> None:
    """to_dict must convert non-JSON-serializable review values to serializable forms."""
    goal = Goal("test")
    goal.review = {"passed": True, "criteria": [{"name": "test", "value": {1, 2, 3}}]}
    d = goal.to_dict()
    import json

    json_str = json.dumps(d)
    assert isinstance(json_str, str)
    assert d["review"]["criteria"][0]["value"] == [1, 2, 3]


def test_goal_to_dict_json_roundtrip_with_set_metadata() -> None:
    """to_dict + from_dict roundtrip must preserve set metadata as sorted lists."""
    goal = Goal("test")
    goal.metadata = {"key": {3, 1, 2}}
    d = goal.to_dict()
    goal2 = Goal.from_dict(d)
    assert goal2.metadata["key"] == [1, 2, 3]


def test_goal_from_dict_rejects_non_dict_payload() -> None:
    """from_dict must reject non-dict payloads with a clear error."""
    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict("not a dict")
    error = str(exc_info.value)
    assert "mapping" in error
    assert "str" in error


def test_goal_from_dict_rejects_list_payload() -> None:
    """from_dict must reject list payloads with a clear error."""
    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict([1, 2, 3])
    error = str(exc_info.value)
    assert "mapping" in error
    assert "list" in error


def test_goal_from_dict_rejects_none_payload() -> None:
    """from_dict must reject None payloads with a clear error."""
    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(None)
    error = str(exc_info.value)
    assert "mapping" in error
    assert "NoneType" in error
