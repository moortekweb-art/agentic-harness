"""Tests for Goal serialization safety: deep copy and input validation."""

from __future__ import annotations

import pytest

from agentic_harness.core.state import Goal, GoalStatus, SCHEMA_VERSION


def _base_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "id": "test-id",
        "objective": "test objective",
        "status": "pending",
        "created_at": "2026-07-05T00:00:00Z",
        "updated_at": "2026-07-05T00:00:00Z",
    }
    payload.update(overrides)
    return payload


class TestGoalToDictDeepCopy:
    """to_dict() must return independent copies of mutable fields."""

    def test_metadata_nested_dict_is_deep_copied(self) -> None:
        goal = Goal(objective="test")
        goal.metadata["nested"] = {"key": "original"}
        d = goal.to_dict()
        d["metadata"]["nested"]["key"] = "modified"
        assert goal.metadata["nested"] == {"key": "original"}

    def test_metadata_list_is_deep_copied(self) -> None:
        goal = Goal(objective="test")
        goal.metadata["items"] = [1, 2, 3]
        d = goal.to_dict()
        d["metadata"]["items"].append(4)
        assert goal.metadata["items"] == [1, 2, 3]

    def test_metadata_nested_list_is_deep_copied(self) -> None:
        goal = Goal(objective="test")
        goal.metadata["matrix"] = {"rows": [1, 2, 3]}
        d = goal.to_dict()
        d["metadata"]["matrix"]["rows"].append(4)
        assert goal.metadata["matrix"]["rows"] == [1, 2, 3]

    def test_history_entries_are_deep_copied(self) -> None:
        goal = Goal(objective="test")
        goal.transition(GoalStatus.PLANNING, reason="started")
        goal.transition(GoalStatus.IN_PROGRESS, reason="continued")
        d = goal.to_dict()
        d["history"][1]["reason"] = "modified"
        assert goal.history[1]["reason"] == "continued"

    def test_artifacts_list_is_independent(self) -> None:
        goal = Goal(objective="test")
        goal.artifacts.append("a.txt")
        d = goal.to_dict()
        d["artifacts"].append("b.txt")
        assert goal.artifacts == ["a.txt"]


class TestGoalFromDictInputValidation:
    """from_dict() must validate and sanitize optional fields."""

    @pytest.mark.parametrize(
        "goal_id",
        ["../outside", "nested/goal", "", ".", "..", "goal\\outside"],
    )
    def test_goal_id_rejects_unsafe_path_values(self, goal_id: str) -> None:
        with pytest.raises(ValueError, match="goal payload 'id'.*safe identifier"):
            Goal.from_dict(_base_payload(id=goal_id))

    def test_artifacts_none_becomes_empty_list(self) -> None:
        goal = Goal.from_dict(_base_payload(artifacts=None))
        assert goal.artifacts == []

    def test_artifacts_with_none_values_filters_none(self) -> None:
        goal = Goal.from_dict(_base_payload(artifacts=[None, "valid.txt", None, "also_valid.txt"]))
        assert goal.artifacts == ["valid.txt", "also_valid.txt"]

    def test_artifacts_wrong_type_raises(self) -> None:
        with pytest.raises(ValueError, match="artifacts.*must be a list"):
            Goal.from_dict(_base_payload(artifacts="not-a-list"))

    def test_metadata_none_becomes_empty_dict(self) -> None:
        goal = Goal.from_dict(_base_payload(metadata=None))
        assert goal.metadata == {}

    def test_metadata_wrong_type_raises(self) -> None:
        with pytest.raises(ValueError, match="metadata.*must be a mapping"):
            Goal.from_dict(_base_payload(metadata=[1, 2, 3]))

    def test_history_none_becomes_empty_list(self) -> None:
        goal = Goal.from_dict(_base_payload(history=None))
        assert goal.history == []

    def test_history_wrong_type_raises(self) -> None:
        with pytest.raises(ValueError, match="history.*must be a list"):
            Goal.from_dict(_base_payload(history="not-a-list"))

    def test_roundtrip_preserves_nested_metadata(self) -> None:
        goal = Goal(objective="test")
        goal.metadata["nested"] = {"a": {"b": [1, 2, 3]}}
        goal.metadata["items"] = [10, 20]
        d = goal.to_dict()
        goal2 = Goal.from_dict(d)
        assert goal2.metadata == {"nested": {"a": {"b": [1, 2, 3]}}, "items": [10, 20]}

    def test_roundtrip_isolation(self) -> None:
        """Mutating the reconstituted goal must not affect the original."""
        goal = Goal(objective="test")
        goal.metadata["shared"] = {"key": "original"}
        d = goal.to_dict()
        goal2 = Goal.from_dict(d)
        goal2.metadata["shared"]["key"] = "mutated"
        assert goal.metadata["shared"]["key"] == "original"


class TestGoalFromDictReviewAndErrorValidation:
    """from_dict() must validate review and error field types."""

    def test_review_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="review.*must be a mapping or null"):
            Goal.from_dict(_base_payload(review="not a dict"))

    def test_review_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="review.*must be a mapping or null"):
            Goal.from_dict(_base_payload(review=123))

    def test_review_none_accepted(self) -> None:
        goal = Goal.from_dict(_base_payload(review=None))
        assert goal.review is None

    def test_review_dict_accepted(self) -> None:
        goal = Goal.from_dict(_base_payload(review={"passed": True}))
        assert goal.review == {"passed": True}

    def test_review_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="review.*must be a mapping or null"):
            Goal.from_dict(_base_payload(review=[1, 2, 3]))

    def test_error_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="error.*must be a string or null"):
            Goal.from_dict(_base_payload(error=123))

    def test_error_none_accepted(self) -> None:
        goal = Goal.from_dict(_base_payload(error=None))
        assert goal.error is None

    def test_error_string_accepted(self) -> None:
        goal = Goal.from_dict(_base_payload(error="something broke"))
        assert goal.error == "something broke"

    def test_error_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="error.*must be a string or null"):
            Goal.from_dict(_base_payload(error=["not", "a", "string"]))

    def test_objective_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="objective.*non-empty"):
            Goal.from_dict(_base_payload(objective=""))

    def test_objective_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="objective.*non-empty"):
            Goal.from_dict(_base_payload(objective="   \t\n  "))

    def test_objective_stripped_before_validation(self) -> None:
        goal = Goal.from_dict(_base_payload(objective="  hello world  "))
        assert goal.objective == "hello world"


class TestGoalValidate:
    """Goal.validate() must surface all structural problems without raising."""

    def test_valid_goal_produces_no_errors(self) -> None:
        goal = Goal(objective="do something", status=GoalStatus.PLANNING)
        assert goal.validate() == []

    def test_empty_objective_is_invalid(self) -> None:
        goal = Goal(objective="", status=GoalStatus.PLANNING)
        errors = goal.validate()
        assert any("objective" in e for e in errors)

    def test_whitespace_objective_is_invalid(self) -> None:
        goal = Goal(objective="   ", status=GoalStatus.PLANNING)
        errors = goal.validate()
        assert any("objective" in e for e in errors)

    def test_empty_id_is_invalid(self) -> None:
        goal = Goal(objective="do something", id="")
        errors = goal.validate()
        assert any("id" in e for e in errors)

    def test_wrong_schema_version_is_invalid(self) -> None:
        goal = Goal(objective="do something", schema_version="wrong.version")
        errors = goal.validate()
        assert any("schema_version" in e for e in errors)

    def test_invalid_created_at_is_invalid(self) -> None:
        goal = Goal(objective="do something", created_at="not-a-date")
        errors = goal.validate()
        assert any("created_at" in e for e in errors)

    def test_invalid_updated_at_is_invalid(self) -> None:
        goal = Goal(objective="do something", updated_at="not-a-date")
        errors = goal.validate()
        assert any("updated_at" in e for e in errors)

    def test_artifacts_must_be_list(self) -> None:
        goal = Goal(objective="do something", artifacts="not a list")
        errors = goal.validate()
        assert any("artifacts" in e for e in errors)

    def test_metadata_must_be_dict(self) -> None:
        goal = Goal(objective="do something", metadata=[1, 2])
        errors = goal.validate()
        assert any("metadata" in e for e in errors)

    def test_review_must_be_dict_or_null(self) -> None:
        goal = Goal(objective="do something", review="not a dict")
        errors = goal.validate()
        assert any("review" in e for e in errors)

    def test_error_must_be_string_or_null(self) -> None:
        goal = Goal(objective="do something", error=42)
        errors = goal.validate()
        assert any("error" in e for e in errors)

    def test_history_must_be_list(self) -> None:
        goal = Goal(objective="do something", history="not a list")
        errors = goal.validate()
        assert any("history" in e for e in errors)

    def test_history_entry_must_be_dict(self) -> None:
        goal = Goal(objective="do something", history=["not a dict"])
        errors = goal.validate()
        assert any("history[0]" in e for e in errors)

    def test_history_entry_missing_from_key(self) -> None:
        goal = Goal(
            objective="do something",
            history=[{"to": "review", "at": "2026-07-06T12:00:00Z"}],
        )
        errors = goal.validate()
        assert any("history[0] missing" in e for e in errors)

    def test_history_entry_missing_to_key(self) -> None:
        goal = Goal(
            objective="do something",
            history=[{"from": "planning", "at": "2026-07-06T12:00:00Z"}],
        )
        errors = goal.validate()
        assert any("history[0] missing" in e for e in errors)

    def test_roundtrip_preserves_valid_state(self) -> None:
        goal = Goal(
            objective="roundtrip test",
            status=GoalStatus.IN_PROGRESS,
            artifacts=["artifact1.md"],
            metadata={"key": "value"},
        )
        goal.transition(GoalStatus.REVIEW, reason="review requested")
        errors = goal.validate()
        assert errors == []
        d = goal.to_dict()
        restored = Goal.from_dict(d)
        assert restored.validate() == []
        assert restored.objective == goal.objective
        assert restored.status == goal.status
        assert restored.history == goal.history

    def test_no_history_with_non_pending_status_is_valid_for_active_statuses(self) -> None:
        """A goal constructed directly with an active status (no history) is valid."""
        goal = Goal(objective="constructed directly", status=GoalStatus.IN_PROGRESS)
        errors = goal.validate()
        assert errors == []

    def test_no_history_with_pending_status_is_valid(self) -> None:
        goal = Goal(objective="fresh pending goal")
        errors = goal.validate()
        assert errors == []

    def test_history_ends_different_from_status_is_invalid(self) -> None:
        goal = Goal(
            objective="corrupt state",
            status=GoalStatus.IN_PROGRESS,
            history=[
                {"from": "pending", "to": "planning", "at": "2026-07-06T10:00:00Z"},
                {"from": "planning", "to": "in_progress", "at": "2026-07-06T11:00:00Z"},
                {"from": "in_progress", "to": "review", "at": "2026-07-06T12:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert any("history ends at" in e and "state is corrupt" in e for e in errors)

    def test_history_matches_status_is_valid(self) -> None:
        goal = Goal(
            objective="consistent state",
            status=GoalStatus.REVIEW,
            history=[
                {"from": "pending", "to": "planning", "at": "2026-07-06T10:00:00Z"},
                {"from": "planning", "to": "in_progress", "at": "2026-07-06T11:00:00Z"},
                {"from": "in_progress", "to": "review", "at": "2026-07-06T12:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert errors == []

    def test_illegal_transition_in_history_is_invalid(self) -> None:
        goal = Goal(
            objective="illegal transition",
            status=GoalStatus.REVIEW,
            history=[
                {"from": "pending", "to": "done", "at": "2026-07-06T10:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert any("illegal transition" in e for e in errors)

    def test_done_status_with_matching_history_is_valid(self) -> None:
        goal = Goal(
            objective="done goal",
            status=GoalStatus.DONE,
            history=[
                {"from": "pending", "to": "planning", "at": "2026-07-06T10:00:00Z"},
                {"from": "planning", "to": "in_progress", "at": "2026-07-06T11:00:00Z"},
                {"from": "in_progress", "to": "review", "at": "2026-07-06T12:00:00Z"},
                {"from": "review", "to": "done", "at": "2026-07-06T13:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert errors == []

    def test_failed_status_with_matching_history_is_valid(self) -> None:
        goal = Goal(
            objective="failed goal",
            status=GoalStatus.FAILED,
            history=[
                {"from": "pending", "to": "planning", "at": "2026-07-06T10:00:00Z"},
                {"from": "planning", "to": "in_progress", "at": "2026-07-06T11:00:00Z"},
                {"from": "in_progress", "to": "failed", "at": "2026-07-06T12:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert errors == []

    def test_corrupt_from_field_is_invalid(self) -> None:
        goal = Goal(
            objective="corrupt from",
            status=GoalStatus.REVIEW,
            history=[
                {"from": "not_a_real_status", "to": "review", "at": "2026-07-06T10:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert any("invalid status" in e for e in errors)

    def test_corrupt_to_field_is_invalid(self) -> None:
        goal = Goal(
            objective="corrupt to",
            status=GoalStatus.REVIEW,
            history=[
                {"from": "in_progress", "to": "not_a_real_status", "at": "2026-07-06T10:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert any("invalid status" in e for e in errors)

    def test_validate_returns_both_structural_and_transition_errors(self) -> None:
        goal = Goal(
            objective="",
            status=GoalStatus.IN_PROGRESS,
            history=[
                {"from": "pending", "to": "done", "at": "2026-07-06T10:00:00Z"},
            ],
        )
        errors = goal.validate()
        assert any("objective" in e for e in errors)
        assert any("illegal transition" in e or "history ends" in e for e in errors)


class TestGoalJsonSerialization:
    """Tests for Goal.to_json() and Goal.from_json() convenience methods."""

    def _make_goal(self, **overrides: object) -> Goal:
        payload = _base_payload(**overrides)
        return Goal.from_dict(payload)

    def test_to_json_produces_valid_json_string(self) -> None:
        goal = self._make_goal()
        text = goal.to_json()
        assert isinstance(text, str)
        assert len(text) > 0
        # Must be parseable as JSON
        import json

        parsed = json.loads(text)
        assert parsed["id"] == "test-id"
        assert parsed["objective"] == "test objective"
        assert parsed["status"] == "pending"

    def test_to_json_with_indent(self) -> None:
        goal = self._make_goal()
        text = goal.to_json(indent=2)
        assert "\n" in text
        assert "  " in text

    def test_from_json_roundtrip_preserves_all_fields(self) -> None:
        goal = self._make_goal(
            artifacts=["artifact1", "artifact2"],
            metadata={"key": "value"},
            review={"score": 5, "notes": "good"},
            error=None,
        )
        text = goal.to_json()
        restored = Goal.from_json(text)
        assert restored.id == goal.id
        assert restored.objective == goal.objective
        assert restored.status == goal.status
        assert restored.artifacts == goal.artifacts
        assert restored.metadata == goal.metadata
        assert restored.review == goal.review
        assert restored.error == goal.error

    def test_from_json_with_error_field(self) -> None:
        goal = self._make_goal(error="something went wrong")
        text = goal.to_json()
        restored = Goal.from_json(text)
        assert restored.error == "something went wrong"

    def test_from_json_invalid_json_raises_value_error(self) -> None:
        with pytest.raises(Exception):
            Goal.from_json("not valid json {{{")

    def test_from_json_missing_required_field_raises(self) -> None:
        import json

        goal = self._make_goal()
        text = goal.to_json()
        parsed = json.loads(text)
        del parsed["objective"]
        with pytest.raises(ValueError, match="missing required"):
            Goal.from_json(json.dumps(parsed))

    def test_from_json_wrong_schema_version_raises(self) -> None:
        import json

        goal = self._make_goal()
        text = goal.to_json()
        parsed = json.loads(text)
        parsed["schema_version"] = "wrong.version"
        with pytest.raises(ValueError, match="unsupported goal schema"):
            Goal.from_json(json.dumps(parsed))

    def test_to_json_applies_json_safe_sanitization(self) -> None:
        """to_json should apply _make_json_safe to metadata and review."""
        goal = Goal(
            objective="test with set",
            id="test-json-safe",
            status=GoalStatus.PENDING,
            metadata={"set_key": {1, 2, 3}},  # set is not JSON-serializable
        )
        text = goal.to_json()
        # Should not raise — sanitization converts set to list
        assert isinstance(text, str)


class TestGoalHashContract:
    """Mutable goals must never be used as set members or dictionary keys."""

    def test_goal_is_unhashable_before_and_after_nested_mutations(self) -> None:
        goal = Goal(objective="test", id="g1", status=GoalStatus.PLANNING)

        assert Goal.__hash__ is None
        with pytest.raises(TypeError, match="unhashable type"):
            hash(goal)

        goal.metadata["nested"] = {"key": "value", "items": [1, 2, 3]}
        goal.review = {"passed": True, "criteria": []}
        with pytest.raises(TypeError, match="unhashable type"):
            hash(goal)
