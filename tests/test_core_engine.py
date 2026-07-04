from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agentic_harness import Goal, GoalStatus, Supervisor
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.errors import (
    GoalConflictError,
    InvalidTransitionError,
    LoopGuardTripped,
    NoActiveGoalError,
    StateLockError,
)
from agentic_harness.core.loop_guard import LoopGuard
from agentic_harness.core.review import (
    DeterministicReviewer,
    ReviewCriterion,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
)
from agentic_harness.core.worker import WorkerResult


class RecordingWorker:
    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def run(self, goal: Goal) -> WorkerResult:
        self.calls += 1
        return self.result


class InspectingWorker:
    def __init__(self, project_dir) -> None:
        self.project_dir = project_dir
        self.seen_status = ""

    def run(self, goal: Goal) -> WorkerResult:
        state_path = (
            self.project_dir / ".agentic-harness" / "runs" / goal.id / "state.json"
        )
        self.seen_status = json.loads(state_path.read_text(encoding="utf-8"))["status"]
        return WorkerResult(success=True, summary="implemented")


def test_goal_state_machine_allows_expected_path() -> None:
    goal = Goal("ship something")

    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.REVIEW)
    goal.transition(GoalStatus.DONE)

    assert goal.status is GoalStatus.DONE
    assert [item["to"] for item in goal.history] == [
        "planning",
        "in_progress",
        "review",
        "done",
    ]


def test_goal_state_machine_rejects_invalid_transition() -> None:
    goal = Goal("bad jump")

    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.DONE)


def test_supervisor_writes_project_local_state_and_reviews(tmp_path) -> None:
    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    started = supervisor.start("build feature")
    progressed = supervisor.continue_goal()
    reviewed = supervisor.review()

    assert started.status is GoalStatus.PLANNING
    assert progressed.status is GoalStatus.REVIEW
    assert reviewed.status is GoalStatus.DONE
    assert worker.calls == 1
    assert (tmp_path / ".agentic-harness" / "current.json").exists()
    assert (tmp_path / ".agentic-harness" / "runs" / started.id / "state.json").exists()


def test_supervisor_refuses_to_overwrite_active_goal(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    first = supervisor.start("first goal")

    with pytest.raises(GoalConflictError) as exc:
        supervisor.start("second goal")

    assert first.id in str(exc.value)
    assert supervisor.status().id == first.id


def test_supervisor_persists_in_progress_before_worker_runs(tmp_path) -> None:
    worker = InspectingWorker(tmp_path)
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("long work")
    supervisor.continue_goal()

    assert worker.seen_status == "in_progress"


def test_supervisor_surfaces_state_lock_contention(monkeypatch, tmp_path) -> None:
    from agentic_harness.core import artifacts

    def fake_flock(*args) -> None:
        raise BlockingIOError

    monkeypatch.setattr(artifacts.fcntl, "flock", fake_flock)
    supervisor = Supervisor(project_dir=tmp_path)

    with pytest.raises(StateLockError):
        supervisor.start("locked goal")


def test_artifact_store_does_not_import_fcntl_at_module_import_time() -> None:
    source = Path("agentic_harness/core/artifacts.py").read_text(encoding="utf-8")

    assert "\nimport fcntl\n" not in source


def test_supervisor_default_noop_fails_instead_of_passing_review(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)

    supervisor.start("do real work")
    progressed = supervisor.continue_goal()

    assert progressed.status is GoalStatus.FAILED
    assert progressed.metadata["worker_success"] is False
    assert "no worker configured" in (progressed.error or "")


def test_supervisor_explicit_noop_success_can_pass_review(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path, allow_noop_success=True)

    supervisor.start("demo only")
    progressed = supervisor.continue_goal()
    reviewed = supervisor.review()

    assert progressed.status is GoalStatus.REVIEW
    assert reviewed.status is GoalStatus.DONE


def test_deterministic_reviewer_reports_typed_criteria() -> None:
    goal = Goal("review me")
    goal.metadata["worker_success"] = True
    reviewer = DeterministicReviewer(
        [
            ReviewCriterion(
                "has_objective",
                lambda g: (bool(g.objective), "objective is present"),
                "Goal must have text.",
            )
        ]
    )

    result = reviewer.review(goal)

    assert result.passed is True
    assert result.criteria == [
        {
            "name": "has_objective",
            "description": "Goal must have text.",
            "passed": True,
            "message": "objective is present",
        }
    ]


def test_artifact_exists_criterion_requires_recorded_existing_artifact(tmp_path) -> None:
    goal = Goal("check artifact")
    report = tmp_path / "report.md"
    report.write_text("ok\n", encoding="utf-8")
    goal.artifacts.append("report.md")

    result = DeterministicReviewer([artifact_exists(tmp_path, "report.md")]).review(goal)

    assert result.passed is True


def test_artifact_exists_criterion_rejects_path_escape(tmp_path) -> None:
    goal = Goal("bad artifact")
    goal.artifacts.append("../outside.txt")

    result = DeterministicReviewer([artifact_exists(tmp_path, "../outside.txt")]).review(goal)

    assert result.passed is False
    assert "outside project" in result.criteria[0]["message"]


def test_command_passes_criterion_runs_bounded_command(tmp_path) -> None:
    goal = Goal("command check")

    result = DeterministicReviewer(
        [command_passes(["python", "-c", "print('ok')"], cwd=tmp_path)]
    ).review(goal)

    assert result.passed is True


def test_command_passes_criterion_reports_missing_executable(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("missing-review-tool")

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("command check")

    result = DeterministicReviewer(
        [command_passes(["missing-review-tool"], cwd=tmp_path)]
    ).review(goal)

    assert result.passed is False
    assert "missing-review-tool" in result.criteria[0]["message"]


def test_file_changed_and_git_clean_criteria_use_git_status(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    path = tmp_path / "tracked.txt"
    path.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    path.write_text("two\n", encoding="utf-8")
    goal = Goal("git criteria")

    dirty = DeterministicReviewer([file_changed(tmp_path, "tracked.txt")]).review(goal)
    clean = DeterministicReviewer([git_clean(tmp_path)]).review(goal)

    assert dirty.passed is True
    assert clean.passed is False


def test_git_criteria_report_missing_git(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("git criteria")

    changed = DeterministicReviewer([file_changed(tmp_path, "tracked.txt")]).review(goal)
    clean = DeterministicReviewer([git_clean(tmp_path)]).review(goal)

    assert changed.passed is False
    assert clean.passed is False
    assert "git" in changed.criteria[0]["message"]
    assert "git" in clean.criteria[0]["message"]


def test_loop_guard_trips_after_configured_continue_count() -> None:
    guard = LoopGuard(max_continues=1, window_seconds=60)

    guard.record_continue()
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_loop_guard_persists_events_across_instances(tmp_path) -> None:
    path = tmp_path / "guard.json"

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 100.0,
    ).record_continue()

    with pytest.raises(LoopGuardTripped):
        LoopGuard(
            max_continues=1,
            window_seconds=60,
            state_path=path,
            clock=lambda: 101.0,
        ).record_continue()


def test_loop_guard_prunes_expired_persisted_events(tmp_path) -> None:
    path = tmp_path / "guard.json"

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 100.0,
    ).record_continue()

    LoopGuard(
        max_continues=1,
        window_seconds=60,
        state_path=path,
        clock=lambda: 161.0,
    ).record_continue()


def test_supervisor_uses_project_local_loop_guard_state(tmp_path) -> None:
    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    supervisor = Supervisor(project_dir=tmp_path, worker=worker)

    supervisor.start("persist guard")
    supervisor.continue_goal()

    assert (tmp_path / ".agentic-harness" / "guard.json").exists()


def test_supervisor_does_not_record_loop_guard_without_active_goal(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    supervisor.init()

    with pytest.raises(NoActiveGoalError):
        supervisor.continue_goal()

    assert not (tmp_path / ".agentic-harness" / "guard.json").exists()


def test_repair_restores_missing_current_marker(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    goal = supervisor.start("repair marker")
    (tmp_path / ".agentic-harness" / "current.json").unlink()

    repaired = supervisor.repair()

    assert repaired is not None
    assert repaired.id == goal.id
    assert supervisor.status().id == goal.id


def test_artifact_store_writes_markdown_report_under_project_state(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    goal = Goal("write report")

    report_path = store.write_report(goal, "# Report\n")

    assert report_path == tmp_path / ".agentic-harness" / "runs" / goal.id / "report.md"
    assert report_path.read_text(encoding="utf-8") == "# Report\n"
    assert goal.artifacts == [f".agentic-harness/runs/{goal.id}/report.md"]


def test_artifact_store_rejects_report_name_path_escape(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    goal = Goal("write report")

    with pytest.raises(ValueError, match="outside goal artifact directory"):
        store.write_report(goal, "# Escape\n", name="../escape.md")

    assert not (tmp_path / ".agentic-harness" / "runs" / "escape.md").exists()
