"""Tests for the deterministic review system.

Covers:
- DeterministicReviewer: criterion execution, pass/fail aggregation
- ReviewResult: serialization, to_dict
- Built-in criteria: artifact_exists, command_passes, file_changed,
  git_clean, goal_status_is, worker_success
"""

from __future__ import annotations

import pytest

import subprocess
import tempfile
from pathlib import Path

from agentic_harness import Goal, GoalStatus
from agentic_harness.core.review import (
    DeterministicReviewer,
    ReviewCriterion,
    ReviewResult,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
    goal_status_is,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_goal(
    status: GoalStatus = GoalStatus.REVIEW,
    *,
    artifacts: list[str] | None = None,
    metadata: dict | None = None,
) -> Goal:
    """Build a minimal goal suitable for review tests."""
    return Goal(
        objective="test objective",
        id="test-goal-review",
        status=status,
        artifacts=artifacts or [],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# ReviewResult
# ---------------------------------------------------------------------------


class TestReviewResult:
    def test_to_dict_includes_passed_and_criteria(self):
        result = ReviewResult(
            passed=True,
            criteria=[
                {"name": "a", "passed": True, "message": "ok"},
                {"name": "b", "passed": False, "message": "fail"},
            ],
        )
        d = result.to_dict()
        assert d["passed"] is True
        assert len(d["criteria"]) == 2
        assert d["criteria"][0]["name"] == "a"
        assert d["criteria"][1]["passed"] is False

    def test_to_dict_empty_criteria(self):
        result = ReviewResult(passed=False)
        d = result.to_dict()
        assert d["passed"] is False
        assert d["criteria"] == []

    def test_criteria_list_is_independent_copy(self):
        """to_dict() returns a new list, not the same list object."""
        criteria = [{"name": "x", "passed": True}]
        result = ReviewResult(passed=True, criteria=criteria)
        d1 = result.to_dict()
        # The returned list should be a different object than the internal one
        assert d1["criteria"] is not criteria
        # But dict items inside are shared (shallow copy)
        assert d1["criteria"][0] is criteria[0]


# ---------------------------------------------------------------------------
# DeterministicReviewer
# ---------------------------------------------------------------------------


class TestDeterministicReviewer:
    def test_default_criterion_checks_worker_success(self):
        goal = _make_goal(metadata={"worker_success": True})
        reviewer = DeterministicReviewer()
        result = reviewer.review(goal)
        assert result.passed is True
        assert len(result.criteria) == 1
        assert result.criteria[0]["name"] == "worker_success"
        assert "success" in result.criteria[0]["message"].lower()

    def test_default_criterion_fails_when_no_worker_success(self):
        goal = _make_goal(metadata={})
        reviewer = DeterministicReviewer()
        result = reviewer.review(goal)
        assert result.passed is False
        assert result.criteria[0]["name"] == "worker_success"

    def test_default_criterion_fails_on_false_worker_success(self):
        goal = _make_goal(metadata={"worker_success": False})
        reviewer = DeterministicReviewer()
        result = reviewer.review(goal)
        assert result.passed is False

    def test_custom_criteria_run_in_order(self):
        order: list[str] = []

        def check_a(goal: Goal):
            order.append("a")
            return True, "a ok"

        def check_b(goal: Goal):
            order.append("b")
            return False, "b fail"

        reviewer = DeterministicReviewer(
            criteria=[
                ReviewCriterion("a", check_a, "first"),
                ReviewCriterion("b", check_b, "second"),
            ]
        )
        result = reviewer.review(_make_goal())
        assert order == ["a", "b"]
        assert result.passed is False  # all() requires all True

    def test_review_passes_when_all_criteria_pass(self):
        reviewer = DeterministicReviewer(
            criteria=[
                ReviewCriterion("c1", lambda g: (True, "ok1")),
                ReviewCriterion("c2", lambda g: (True, "ok2")),
            ]
        )
        result = reviewer.review(_make_goal())
        assert result.passed is True
        assert all(c["passed"] for c in result.criteria)

    def test_review_fails_when_any_criterion_fails(self):
        reviewer = DeterministicReviewer(
            criteria=[
                ReviewCriterion("c1", lambda g: (True, "ok")),
                ReviewCriterion("c2", lambda g: (False, "fail")),
            ]
        )
        result = reviewer.review(_make_goal())
        assert result.passed is False

    def test_review_with_empty_criteria_raises_value_error(self):
        """Empty criteria list must raise ValueError — an empty review is meaningless."""
        reviewer = DeterministicReviewer(criteria=[])
        with pytest.raises(ValueError, match="empty criteria"):
            reviewer.review(_make_goal())

    def test_criterion_description_included_in_output(self):
        reviewer = DeterministicReviewer(
            criteria=[
                ReviewCriterion("desc_test", lambda g: (True, "ok"), description="A description"),
            ]
        )
        result = reviewer.review(_make_goal())
        assert result.criteria[0]["description"] == "A description"

    def test_passed_field_is_boolean(self):
        def check(goal: Goal):
            return "truthy", "message"  # non-bool return

        reviewer = DeterministicReviewer(criteria=[ReviewCriterion("truthy", check)])
        result = reviewer.review(_make_goal())
        # bool("truthy") is True, so passed should be True
        assert result.passed is True
        assert isinstance(result.criteria[0]["passed"], bool)


# ---------------------------------------------------------------------------
# Built-in criterion: artifact_exists
# ---------------------------------------------------------------------------


class TestArtifactExists:
    def test_passes_when_artifact_exists_and_recorded(self, tmp_path):
        artifact = tmp_path / "output.txt"
        artifact.write_text("hello")
        reviewer = DeterministicReviewer(criteria=[artifact_exists(tmp_path, "output.txt")])
        goal = _make_goal(artifacts=["output.txt"])
        result = reviewer.review(goal)
        assert result.passed is True
        assert "exists" in result.criteria[0]["message"]

    def test_fails_when_artifact_not_recorded(self, tmp_path):
        artifact = tmp_path / "output.txt"
        artifact.write_text("hello")
        reviewer = DeterministicReviewer(criteria=[artifact_exists(tmp_path, "output.txt")])
        goal = _make_goal(artifacts=[])  # not recorded
        result = reviewer.review(goal)
        assert result.passed is False
        assert "not recorded" in result.criteria[0]["message"]

    def test_fails_when_artifact_does_not_exist(self, tmp_path):
        reviewer = DeterministicReviewer(criteria=[artifact_exists(tmp_path, "missing.txt")])
        goal = _make_goal(artifacts=["missing.txt"])
        result = reviewer.review(goal)
        assert result.passed is False
        assert "does not exist" in result.criteria[0]["message"]

    def test_fails_when_artifact_is_outside_project(self, tmp_path):
        outside = Path("/tmp/outside_artifact.txt")
        outside.write_text("data")
        reviewer = DeterministicReviewer(criteria=[artifact_exists(tmp_path, str(outside))])
        goal = _make_goal(artifacts=[str(outside)])
        result = reviewer.review(goal)
        assert result.passed is False
        assert "outside project" in result.criteria[0]["message"]
        outside.unlink()


# ---------------------------------------------------------------------------
# Built-in criterion: command_passes
# ---------------------------------------------------------------------------


class TestCommandPasses:
    def test_passes_for_successful_command(self):
        criterion = command_passes(["true"])
        result = criterion.check(_make_goal())
        assert result[0] is True
        assert "passed" in result[1].lower()

    def test_fails_for_failing_command(self):
        criterion = command_passes(["false"])
        result = criterion.check(_make_goal())
        assert result[0] is False
        assert "failed" in result[1].lower()

    def test_fails_on_timeout(self):
        criterion = command_passes(["sleep", "10"], timeout=1)
        result = criterion.check(_make_goal())
        assert result[0] is False
        assert "timed out" in result[1].lower()

    def test_includes_stderr_on_failure(self):
        criterion = command_passes(["sh", "-c", "echo error_msg >&2; exit 1"])
        result = criterion.check(_make_goal())
        assert result[0] is False
        assert "error_msg" in result[1]

    def test_cwd_is_respected(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "run.sh"
            script.write_text("#!/bin/sh\necho done\n")
            script.chmod(0o755)
            criterion = command_passes(["./run.sh"], cwd=td)
            result = criterion.check(_make_goal())
            assert result[0] is True


# ---------------------------------------------------------------------------
# Built-in criterion: file_changed
# ---------------------------------------------------------------------------


class TestFileChanged:
    def test_passes_when_file_is_modified(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("original")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, capture_output=True)
        target.write_text("modified")
        criterion = file_changed(tmp_path, "file.txt")
        result = criterion.check(_make_goal())
        assert result[0] is True
        assert "changed" in result[1].lower()

    def test_fails_when_file_is_clean(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("content")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, capture_output=True)
        criterion = file_changed(tmp_path, "file.txt")
        result = criterion.check(_make_goal())
        assert result[0] is False
        assert "clean" in result[1].lower()


# ---------------------------------------------------------------------------
# Built-in criterion: git_clean
# ---------------------------------------------------------------------------


class TestGitClean:
    def test_passes_when_git_worktree_is_clean(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
        criterion = git_clean(tmp_path)
        result = criterion.check(_make_goal())
        assert result[0] is True
        assert "clean" in result[1].lower()

    def test_fails_when_git_worktree_has_changes(self, tmp_path):
        target = tmp_path / "dirty.txt"
        target.write_text("dirty")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
        criterion = git_clean(tmp_path)
        result = criterion.check(_make_goal())
        assert result[0] is False
        assert "changes" in result[1].lower()


# ---------------------------------------------------------------------------
# Built-in criterion: goal_status_is
# ---------------------------------------------------------------------------


class TestGoalStatusIs:
    def test_passes_when_status_matches(self):
        goal = _make_goal(status=GoalStatus.REVIEW)
        criterion = goal_status_is("review")
        result = criterion.check(goal)
        assert result[0] is True
        assert "review" in result[1].lower()

    def test_fails_when_status_does_not_match(self):
        goal = _make_goal(status=GoalStatus.DONE)
        criterion = goal_status_is("review")
        result = criterion.check(goal)
        assert result[0] is False
        assert "expected review" in result[1].lower()

    def test_case_sensitive(self):
        goal = _make_goal(status=GoalStatus.DONE)
        criterion = goal_status_is("done")
        result = criterion.check(goal)
        assert result[0] is True

    def test_description_includes_expected_status(self):
        criterion = goal_status_is("done")
        assert "done" in criterion.description


# ---------------------------------------------------------------------------
# ReviewResult serialization round-trip
# ---------------------------------------------------------------------------


class TestReviewResultRoundTrip:
    def test_roundtrip_preserves_criteria(self):
        original = ReviewResult(
            passed=False,
            criteria=[
                {"name": "a", "description": "desc_a", "passed": True, "message": "ok"},
                {"name": "b", "description": "desc_b", "passed": False, "message": "fail"},
            ],
        )
        d = original.to_dict()
        restored = ReviewResult(
            passed=d["passed"],
            criteria=d["criteria"],
        )
        assert restored.passed is False
        assert len(restored.criteria) == 2
        assert restored.criteria[0]["name"] == "a"
        assert restored.criteria[1]["passed"] is False
