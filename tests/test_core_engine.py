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
        state_path = self.project_dir / ".agentic-harness" / "runs" / goal.id / "state.json"
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


def test_artifact_store_goal_dir_cannot_escape_runs_directory(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")

    with pytest.raises(ValueError, match="outside runs directory"):
        store.goal_dir("../../outside")

    safe = store.goal_dir("legacy-safe-id")
    assert safe == (store.runs_dir / "legacy-safe-id").resolve()


@pytest.mark.parametrize(
    "goal_id",
    ["/tmp/absolute", "goal\\windows-escape", "C:\\outside", "x" * 129],
)
def test_artifact_store_rejects_unsafe_goal_ids_on_every_platform(
    tmp_path: Path,
    goal_id: str,
) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")

    with pytest.raises(ValueError, match="outside runs directory"):
        store.goal_dir(goal_id)


def test_artifact_store_goal_dir_rejects_existing_symlink_escape(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.runs_dir / "safe-looking-id").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        store.goal_dir("safe-looking-id")


def test_artifact_store_goal_dir_rejects_symlink_alias_within_runs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    target = store.runs_dir / "goal-b"
    target.mkdir()
    (store.runs_dir / "goal-a").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        store.goal_dir("goal-a")

    assert not (target / "state.json").exists()


def test_goal_listing_and_repair_ignore_symlinked_run_directories(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    outside = tmp_path / "outside-run"
    outside.mkdir()
    (outside / "state.json").write_text(
        json.dumps(Goal("outside", id="safe-looking-id").to_dict()),
        encoding="utf-8",
    )
    (store.runs_dir / "safe-looking-id").symlink_to(outside, target_is_directory=True)

    assert store.list_goals() == []
    assert store.repair_current_marker() is None
    assert not store.current_path.exists()


@pytest.mark.parametrize("symlink_level", ["root", "runs"])
def test_artifact_store_rejects_symlinked_state_directories(
    tmp_path: Path,
    symlink_level: str,
) -> None:
    outside = tmp_path / "outside-state"
    outside.mkdir()
    root = tmp_path / ".agentic-harness"
    if symlink_level == "root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        (root / "runs").symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(root)

    with pytest.raises(ValueError, match="symlink"):
        store.init()


def test_artifact_store_rejects_symlinked_state_file_and_lock(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    outside_state = tmp_path / "outside-state.json"
    outside_state.write_text(
        json.dumps(Goal("outside", id="safe-id").to_dict()),
        encoding="utf-8",
    )
    run_dir = store.goal_dir("safe-id")
    run_dir.mkdir()
    (run_dir / "state.json").symlink_to(outside_state)

    with pytest.raises(StateLockError, match="corrupted or missing goal state"):
        store.read_goal("safe-id")

    outside_lock = tmp_path / "outside.lock"
    outside_lock.touch()
    store.lock_path.symlink_to(outside_lock)
    with pytest.raises(StateLockError, match="symlink"):
        with store.locked():
            pass


def test_tampered_current_marker_cannot_select_goal_outside_runs(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    escaped_name = f"outside-{tmp_path.name}"
    store.current_path.write_text(
        json.dumps({"goal_id": f"../../../{escaped_name}"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(StateLockError, match="corrupted or missing goal state"):
        store.read_current_goal()

    assert not (tmp_path.parent / escaped_name).exists()


def test_goal_state_id_must_match_its_containing_run_directory(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    state_path = store.runs_dir / "expected-id" / "state.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(Goal("tampered", id="different-id").to_dict()),
        encoding="utf-8",
    )

    with pytest.raises(StateLockError, match="corrupted or missing goal state"):
        store.read_goal("expected-id")


def test_goal_state_machine_rejects_invalid_transition() -> None:
    goal = Goal("bad jump")

    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.DONE)


def test_goal_validation_rejects_unsafe_direct_constructor_id() -> None:
    goal = Goal("unsafe direct object", id="../../../outside")

    assert "id must be a safe identifier" in goal.validate()


def test_goal_state_machine_rejects_done_to_anything() -> None:
    goal = Goal("finished")
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.REVIEW)
    goal.transition(GoalStatus.DONE)

    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.IN_PROGRESS)
    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.FAILED)
    with pytest.raises(InvalidTransitionError):
        goal.transition(GoalStatus.DONE)


def test_goal_state_machine_failed_can_restart_once() -> None:
    goal = Goal("failed goal")
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.FAILED)

    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)

    assert goal.status is GoalStatus.IN_PROGRESS


def test_goal_state_machine_failed_cannot_restart_twice() -> None:
    goal = Goal("double-fail")
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.FAILED)
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)
    goal.transition(GoalStatus.FAILED)
    goal.transition(GoalStatus.PLANNING)
    goal.transition(GoalStatus.IN_PROGRESS)

    # DONE has no valid transitions, so we test that instead
    goal2 = Goal("done-terminal")
    goal2.transition(GoalStatus.PLANNING)
    goal2.transition(GoalStatus.IN_PROGRESS)
    goal2.transition(GoalStatus.REVIEW)
    goal2.transition(GoalStatus.DONE)

    with pytest.raises(InvalidTransitionError):
        goal2.transition(GoalStatus.IN_PROGRESS)
    with pytest.raises(InvalidTransitionError):
        goal2.transition(GoalStatus.FAILED)


def test_goal_state_machine_records_transition_reason() -> None:
    goal = Goal("reasoned")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="planning complete")

    assert goal.history[0]["reason"] == "started"
    assert goal.history[1]["reason"] == "planning complete"


def test_goal_state_machine_records_timestamps() -> None:
    goal = Goal("timed")
    goal.transition(GoalStatus.PLANNING)
    first_updated = goal.updated_at
    # Add a small delay to ensure second timestamp differs
    import time

    time.sleep(1.1)
    goal.transition(GoalStatus.IN_PROGRESS)
    second_updated = goal.updated_at

    assert first_updated != second_updated
    assert goal.created_at <= first_updated <= second_updated


def test_goal_from_dict_roundtrip_preserves_all_fields() -> None:
    original = Goal("roundtrip test")
    original.transition(GoalStatus.PLANNING, reason="p")
    original.transition(GoalStatus.IN_PROGRESS, reason="i")
    original.artifacts.append("output.txt")
    original.metadata["key"] = "value"
    original.error = "something broke"
    original.review = {"passed": True, "criteria": []}

    restored = Goal.from_dict(original.to_dict())

    assert restored.objective == original.objective
    assert restored.id == original.id
    assert restored.status is GoalStatus.IN_PROGRESS
    assert restored.artifacts == ["output.txt"]
    assert restored.metadata == {"key": "value"}
    assert restored.error == "something broke"
    assert restored.review == {"passed": True, "criteria": []}
    assert len(restored.history) == 2


def test_goal_from_dict_rejects_wrong_schema_version() -> None:
    payload = {
        "schema_version": "agentic_harness.goal.v99",
        "id": "abc123",
        "objective": "test",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    with pytest.raises(ValueError) as exc_info:
        Goal.from_dict(payload)

    assert "unsupported goal schema" in str(exc_info.value)


def test_goal_from_dict_handles_missing_optional_fields() -> None:
    payload = {
        "schema_version": "agentic_harness.goal.v1",
        "id": "minimal",
        "objective": "minimal goal",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    goal = Goal.from_dict(payload)

    assert goal.artifacts == []
    assert goal.metadata == {}
    assert goal.review is None
    assert goal.error is None
    assert goal.history == []


def test_goal_to_dict_does_not_mutate_original() -> None:
    goal = Goal("immutable")
    goal.artifacts.append("a.txt")
    goal.metadata["k"] = "v"

    d = goal.to_dict()
    d["artifacts"].append("b.txt")
    d["metadata"]["k2"] = "v2"

    assert "b.txt" not in goal.artifacts
    assert "k2" not in goal.metadata


def test_goal_transition_raises_with_clear_error_message() -> None:
    goal = Goal("clear error")

    with pytest.raises(InvalidTransitionError) as exc_info:
        goal.transition(GoalStatus.DONE)

    error = str(exc_info.value)
    assert "cannot transition" in error
    assert "pending" in error
    assert "done" in error


def test_goal_history_includes_from_to_and_at() -> None:
    goal = Goal("history check")
    goal.transition(GoalStatus.PLANNING, reason="start")

    entry = goal.history[0]
    assert entry["from"] == "pending"
    assert entry["to"] == "planning"
    assert "at" in entry
    assert entry["reason"] == "start"


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

    def fake_lock(*args) -> None:
        raise BlockingIOError

    if artifacts.fcntl is not None:
        monkeypatch.setattr(artifacts.fcntl, "flock", fake_lock)
    elif artifacts.msvcrt is not None:
        monkeypatch.setattr(artifacts.msvcrt, "locking", fake_lock)
    else:
        pytest.skip("no state lock backend available on this platform")
    supervisor = Supervisor(project_dir=tmp_path)

    with pytest.raises(StateLockError):
        supervisor.start("locked goal")


def test_artifact_store_does_not_import_fcntl_at_module_import_time() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    source = (repo_root / "agentic_harness/core/artifacts.py").read_text(encoding="utf-8")

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
            "independent": True,
            "covers": [],
            "goal_spec_sha256": "",
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

    result = DeterministicReviewer([command_passes(["missing-review-tool"], cwd=tmp_path)]).review(
        goal
    )

    assert result.passed is False
    assert result.criteria[0]["message"] == "independent command could not start"


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


def test_command_passes_criterion_reports_timeout(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 1))
        raise exc

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("timeout check")

    result = DeterministicReviewer(
        [command_passes(["sleep", "999"], cwd=tmp_path, timeout=1)]
    ).review(goal)

    assert result.passed is False
    assert "timed out" in result.criteria[0]["message"]


def test_command_passes_criterion_reports_return_code_and_output(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        result = subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="exit failed\n",
        )
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("returncode check")

    result = DeterministicReviewer([command_passes(["false"], cwd=tmp_path)]).review(goal)

    assert result.passed is False
    assert "1" in result.criteria[0]["message"]
    assert "exit failed" not in result.criteria[0]["message"]


def test_command_passes_criterion_handles_nonzero_return_code(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=2, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("nonzero check")

    result = DeterministicReviewer([command_passes(["false"], cwd=tmp_path)]).review(goal)

    assert result.passed is False
    assert "2" in result.criteria[0]["message"]


def test_file_changed_criterion_handles_git_not_available(monkeypatch, tmp_path) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", fake_run)
    goal = Goal("no git")

    result = DeterministicReviewer([file_changed(tmp_path, "missing.txt")]).review(goal)

    assert result.passed is False
    assert "git" in result.criteria[0]["message"]


@pytest.mark.parametrize("criterion_name", ["file_changed", "git_clean"])
def test_git_review_criteria_never_echo_failed_process_output(
    monkeypatch,
    tmp_path,
    criterion_name: str,
) -> None:
    secret = f"opaque-{criterion_name}-review-secret-Z7Q4M9"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=9,
            stdout=secret,
            stderr=secret,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    criterion = (
        file_changed(tmp_path, "tracked.txt")
        if criterion_name == "file_changed"
        else git_clean(tmp_path)
    )

    result = DeterministicReviewer([criterion]).review(Goal("do not echo git output"))

    serialized = json.dumps(result.to_dict())
    assert result.passed is False
    assert "exit code 9" in serialized
    assert secret not in serialized


def test_file_changed_criterion_reports_clean_when_no_changes(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    path = tmp_path / "clean.txt"
    path.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "clean.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    goal = Goal("clean file")
    result = DeterministicReviewer([file_changed(tmp_path, "clean.txt")]).review(goal)

    assert result.passed is False  # committed, not dirty
    assert "clean" in result.criteria[0]["message"]


def test_deterministic_reviewer_all_criteria_must_pass() -> None:
    def always_pass(goal):
        return True, "pass"

    def always_fail(goal):
        return False, "fail"

    reviewer = DeterministicReviewer(
        [
            ReviewCriterion("good", always_pass),
            ReviewCriterion("bad", always_fail),
        ]
    )
    goal = Goal("mixed")

    result = reviewer.review(goal)

    assert result.passed is False
    assert len(result.criteria) == 2
    assert result.criteria[0]["passed"] is True
    assert result.criteria[1]["passed"] is False


def test_deterministic_reviewer_empty_criteria_raises() -> None:
    """Empty criteria list must raise ValueError, not silently pass."""
    reviewer = DeterministicReviewer([])
    goal = Goal("empty criteria")
    goal.metadata["worker_success"] = True

    with pytest.raises(ValueError, match="empty criteria"):
        reviewer.review(goal)


def test_review_criterion_has_name_and_description() -> None:
    criterion = ReviewCriterion(
        name="test_criterion",
        check=lambda goal: (True, "ok"),
        description="A test criterion",
    )

    assert criterion.name == "test_criterion"
    assert criterion.description == "A test criterion"


def test_loop_guard_trips_after_configured_continue_count() -> None:
    guard = LoopGuard(max_continues=1, window_seconds=60)

    # With max_continues=1, recording 1 event should trip immediately
    with pytest.raises(LoopGuardTripped):
        guard.record_continue()


def test_loop_guard_persists_events_across_instances(tmp_path) -> None:
    path = tmp_path / "guard.json"

    # First record should trip (max_continues=1, 1 >= 1)
    with pytest.raises(LoopGuardTripped):
        LoopGuard(
            max_continues=1,
            window_seconds=60,
            state_path=path,
            clock=lambda: 100.0,
        ).record_continue()

    # Second record should also trip (event persisted, still 1 >= 1)
    with pytest.raises(LoopGuardTripped):
        LoopGuard(
            max_continues=1,
            window_seconds=60,
            state_path=path,
            clock=lambda: 101.0,
        ).record_continue()


def test_loop_guard_prunes_expired_persisted_events(tmp_path) -> None:
    path = tmp_path / "guard.json"

    # First record should trip (max_continues=1, 1 >= 1)
    with pytest.raises(LoopGuardTripped):
        LoopGuard(
            max_continues=1,
            window_seconds=60,
            state_path=path,
            clock=lambda: 100.0,
        ).record_continue()

    # After window expires, second record should also trip (pruned event, but still 1 >= 1)
    with pytest.raises(LoopGuardTripped):
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


def test_artifact_store_redacts_secret_like_report_content(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    goal = Goal("write report")

    report_path = store.write_report(
        goal,
        "token=secret-value-12345\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz\n",
    )

    text = report_path.read_text(encoding="utf-8")
    assert "secret-value-12345" not in text
    assert "abcdefghijklmnopqrstuvwxyz" not in text
    assert "token=<redacted>" in text
    assert "Bearer <redacted>" in text


def test_artifact_store_rejects_report_name_path_escape(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    goal = Goal("write report")

    with pytest.raises(ValueError, match="outside goal artifact directory"):
        store.write_report(goal, "# Escape\n", name="../escape.md")

    assert not (tmp_path / ".agentic-harness" / "runs" / "escape.md").exists()


def test_artifact_store_relative_root_records_project_local_report(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    store = ArtifactStore(".agentic-harness")
    goal = Goal("write relative report")

    report_path = store.write_report(goal, "# Report\n")

    assert report_path == tmp_path / ".agentic-harness" / "runs" / goal.id / "report.md"
    assert goal.artifacts == [f".agentic-harness/runs/{goal.id}/report.md"]


def test_repair_returns_none_when_no_runs_dir(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    # No goal started, no runs dir created

    repaired = supervisor.repair()

    assert repaired is None


def test_repair_returns_none_when_no_current_and_no_runs(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    runs_dir = config_dir / "runs"
    runs_dir.mkdir()
    # current.json deleted, runs dir is empty
    (config_dir / "current.json").unlink(missing_ok=True)

    supervisor = Supervisor(project_dir=tmp_path)
    repaired = supervisor.repair()

    assert repaired is None


def test_artifact_store_read_current_goal_returns_none_without_marker(tmp_path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")

    assert store.read_current_goal() is None


def test_artifact_store_read_current_goal_returns_none_for_corrupt_marker(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "current.json").write_text("not json", encoding="utf-8")
    store = ArtifactStore(config_dir)

    assert store.read_current_goal() is None


def test_artifact_store_read_current_goal_returns_none_for_missing_goal_id(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "current.json").write_text('{"other_key": "value"}', encoding="utf-8")
    store = ArtifactStore(config_dir)

    assert store.read_current_goal() is None


def test_artifact_store_read_current_goal_returns_none_for_non_string_goal_id(tmp_path) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "current.json").write_text('{"goal_id": 12345}', encoding="utf-8")
    store = ArtifactStore(config_dir)

    assert store.read_current_goal() is None


def test_supervisor_start_with_no_worker_fails_cleanly(tmp_path) -> None:
    supervisor = Supervisor(project_dir=tmp_path)
    supervisor.start("no worker")

    # Goal is in PLANNING; continue transitions to IN_PROGRESS then tries to run worker
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED
    assert "no worker configured" in (goal.error or "")


def test_supervisor_continue_after_done_raises(tmp_path) -> None:
    worker = RecordingWorker(WorkerResult(success=True, summary="ok"))
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=DeterministicReviewer([command_passes(["true"])]),
    )
    goal = supervisor.start("done goal")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW
    goal = supervisor.review()
    assert goal.status is GoalStatus.DONE

    # continue after done should raise InvalidTransitionError
    with pytest.raises(InvalidTransitionError):
        supervisor.continue_goal()


def test_goal_status_enum_values_are_lowercase() -> None:
    assert GoalStatus.PENDING.value == "pending"
    assert GoalStatus.PLANNING.value == "planning"
    assert GoalStatus.IN_PROGRESS.value == "in_progress"
    assert GoalStatus.REVIEW.value == "review"
    assert GoalStatus.DONE.value == "done"
    assert GoalStatus.FAILED.value == "failed"


def test_goal_to_dict_includes_schema_version() -> None:
    from agentic_harness.core.state import SCHEMA_VERSION

    goal = Goal("schema check")
    d = goal.to_dict()

    assert d["schema_version"] == SCHEMA_VERSION


def test_config_rejects_bool_for_int_timeout_field() -> None:
    import tempfile
    from pathlib import Path

    from agentic_harness.core.config import load_config
    from agentic_harness.core.errors import ConfigError

    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / ".agentic-harness"
        config_dir.mkdir()
        (config_dir / "config.yml").write_text(
            "version: 1\nworker: noop\nreview_command_timeout: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(tmp)
        assert "integer" in str(exc_info.value)


def test_config_rejects_bool_for_float_field() -> None:
    import tempfile
    from pathlib import Path

    from agentic_harness.core.config import load_config
    from agentic_harness.core.errors import ConfigError

    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / ".agentic-harness"
        config_dir.mkdir()
        (config_dir / "config.yml").write_text(
            "version: 1\nworker: noop\ngithub_poll_interval: false\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(tmp)
        assert "number" in str(exc_info.value)


def test_artifact_store_read_goal_raises_on_corrupted_state(tmp_path) -> None:
    from agentic_harness.core.artifacts import ArtifactStore
    from agentic_harness.core.errors import StateLockError

    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    run_dir = store.runs_dir / "deadbeef"
    run_dir.mkdir()
    (run_dir / "state.json").write_text("not valid json{{{", encoding="utf-8")

    with pytest.raises(StateLockError):
        store.read_goal("deadbeef")


def test_artifact_store_retries_a_transient_state_read_error(tmp_path, monkeypatch) -> None:
    from agentic_harness.core.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal(objective="survive a concurrent atomic replacement")
    state_path = store.write_goal(goal)
    original_read_text = Path.read_text
    attempts = 0

    def flaky_read_text(path, *args, **kwargs):
        nonlocal attempts
        if path == state_path and attempts == 0:
            attempts += 1
            raise PermissionError("state file is being atomically replaced")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    assert store.read_goal(goal.id).id == goal.id
    assert attempts == 1


def test_artifact_store_retries_a_transient_state_replace_error(
    tmp_path, monkeypatch
) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    target = store.current_path
    target.write_text('{"goal_id": "before"}\n', encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path, destination):
        nonlocal attempts
        if destination == target and attempts == 0:
            attempts += 1
            raise PermissionError("state file is briefly held by a reader")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    store._write_json(target, {"goal_id": "after"})

    assert store._read_json(target) == {"goal_id": "after"}
    assert attempts == 1


def test_artifact_store_repair_skips_corrupted_state_files(tmp_path) -> None:
    from agentic_harness.core.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    run_dir = store.runs_dir / "corrupt123"
    run_dir.mkdir()
    (run_dir / "state.json").write_text("corrupted{{{", encoding="utf-8")

    result = store.repair_current_marker()
    assert result is None


def test_coding_agent_escapes_braces_in_objective() -> None:
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "{objective}"])
    goal = Goal(objective="fix {broken} thing with braces")

    command = worker.command_for(goal)

    # Python str.format does not re-process substituted values,
    # so braces in the objective pass through unchanged.
    assert command == ["echo", "fix {broken} thing with braces"]


def test_coding_agent_preserves_explicit_double_braces() -> None:
    from agentic_harness.adapters.coding_agent import CodingAgentWorker
    from agentic_harness.core.state import Goal

    worker = CodingAgentWorker(["echo", "{{literal}} {objective}"])
    goal = Goal(objective="test")

    command = worker.command_for(goal)

    # {{literal}} is a format-string escaped brace, so it stays as {literal}
    # {objective} is replaced with the objective value
    assert command == ["echo", "{literal} test"]


def test_loop_guard_filters_non_finite_events(tmp_path) -> None:
    import json

    from agentic_harness.core.loop_guard import LoopGuard

    state_path = tmp_path / "guard.json"
    state_path.write_text(
        json.dumps({"events": [95.0, float("inf"), float("nan"), 98.0, 99.0]}),
        encoding="utf-8",
    )
    # Use a fixed clock so events are within the window
    guard = LoopGuard(state_path=state_path, clock=lambda: 100.0, window_seconds=60.0)
    guard._load()

    # inf and nan are filtered; 95.0, 98.0, 99.0 are within 60s window (5, 2, 1 seconds ago)
    assert list(guard._events) == [95.0, 98.0, 99.0]


def test_loop_guard_filters_boolean_events_from_json(tmp_path) -> None:
    import json

    from agentic_harness.core.loop_guard import LoopGuard

    state_path = tmp_path / "guard.json"
    state_path.write_text(
        json.dumps({"events": [95.0, True, False, 98.0]}),
        encoding="utf-8",
    )
    # Use a fixed clock so events are within the window
    guard = LoopGuard(state_path=state_path, clock=lambda: 100.0, window_seconds=60.0)
    guard._load()

    # True and False are filtered; 95.0, 98.0 are within 60s window
    assert list(guard._events) == [95.0, 98.0]


def test_config_bool_for_int_in_nested_worker_dict() -> None:
    import tempfile
    from pathlib import Path

    from agentic_harness.core.config import load_config
    from agentic_harness.core.errors import ConfigError

    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / ".agentic-harness"
        config_dir.mkdir()
        (config_dir / "config.yml").write_text(
            "version: 1\nworker:\n  type: coding_agent\n  coding_agent_timeout: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(tmp)
        assert "integer" in str(exc_info.value)


def test_goal_status_is_criterion_passes_when_status_matches() -> None:
    from agentic_harness.core.review import goal_status_is

    goal = Goal("status check")
    goal.transition(GoalStatus.PLANNING)

    result = DeterministicReviewer([goal_status_is("planning")]).review(goal)

    assert result.passed is True
    assert result.criteria[0]["message"] == "goal status is planning"


def test_goal_status_is_criterion_fails_when_status_mismatches() -> None:
    from agentic_harness.core.review import goal_status_is

    goal = Goal("status check")
    goal.transition(GoalStatus.PLANNING)

    result = DeterministicReviewer([goal_status_is("done")]).review(goal)

    assert result.passed is False
    assert "planning" in result.criteria[0]["message"]
    assert "done" in result.criteria[0]["message"]


def test_goal_status_is_criterion_has_name_and_description() -> None:
    from agentic_harness.core.review import goal_status_is

    criterion = goal_status_is("done")

    assert criterion.name == "goal_status_is"
    assert "done" in criterion.description


def test_goal_status_is_used_in_supervisor_review_flow(tmp_path) -> None:
    """Demonstrate the review/continue/accept flow with goal_status_is.

    The supervisor flow is: start → PLANNING, continue → IN_PROGRESS → REVIEW,
    review → DONE (if passes). accept() on DONE is idempotent.
    """
    from agentic_harness.core.review import goal_status_is
    from agentic_harness.core.worker import WorkerResult

    worker = RecordingWorker(WorkerResult(success=True, summary="implemented"))
    reviewer = DeterministicReviewer(
        [
            goal_status_is("review"),
            command_passes(["true"]),
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=worker,
        reviewer=reviewer,
    )

    # 1. Start
    started = supervisor.start("demonstrate flow")
    assert started.status is GoalStatus.PLANNING

    # 2. Continue (worker runs, transitions to REVIEW)
    continued = supervisor.continue_goal()
    assert continued.status is GoalStatus.REVIEW

    # 3. Review (deterministic, transitions to DONE)
    reviewed = supervisor.review()
    assert reviewed.status is GoalStatus.DONE

    # 4. Accept on DONE is idempotent
    accepted = supervisor.accept(reason="operator verified")
    assert accepted.status is GoalStatus.DONE

    # Verify full history
    goal = supervisor.status()
    transitions = [h["to"] for h in goal.history]
    assert transitions == ["planning", "in_progress", "review", "done"]


def test_supervisor_worker_exception_is_caught_and_reported(tmp_path) -> None:
    """If a worker raises an unexpected exception, supervisor catches it and returns a structured failure."""

    class ExplodingWorker:
        def run(self, goal: Goal) -> WorkerResult:
            raise RuntimeError("boom")

    supervisor = Supervisor(project_dir=tmp_path, worker=ExplodingWorker())

    goal = supervisor.start("exploding goal")
    goal = supervisor.continue_goal()

    assert goal.status is GoalStatus.FAILED
    assert "RuntimeError" in goal.metadata.get("worker_summary", "")
    assert "boom" in goal.metadata.get("worker_summary", "")


def test_supervisor_worker_unexpected_exception_is_caught(tmp_path) -> None:
    """Any unexpected exception from a worker should be caught and reported as a structured failure."""

    class CrashWorker:
        def run(self, goal: Goal) -> WorkerResult:
            raise RuntimeError("worker crashed unexpectedly")

    supervisor = Supervisor(project_dir=tmp_path, worker=CrashWorker())

    goal = supervisor.start("crash goal")
    goal = supervisor.continue_goal()

    assert goal.status is GoalStatus.FAILED
    assert "RuntimeError" in goal.metadata.get("worker_summary", "")
    assert "worker crashed unexpectedly" in goal.metadata.get("worker_summary", "")


def test_artifact_store_write_text_cleans_up_temp_on_replace_failure(tmp_path) -> None:
    """If NamedTemporaryFile.replace() fails, the temp file must be cleaned up."""
    import os
    from agentic_harness.core.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / "state")
    store.init()

    # Create a scenario where replace() will fail: target is a directory
    target = tmp_path / "state" / "runs" / "goal-123" / "report.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old content", encoding="utf-8")

    # Make the target a directory so replace will fail
    target.unlink()
    target.mkdir()

    # Try to write a file that would need to replace the directory
    inner_file = target / "inner.md"
    inner_file.parent.mkdir(parents=True, exist_ok=True)

    # The write_text should succeed for inner_file (not replacing target)
    # But if we try to replace target (a directory), it should fail
    with pytest.raises(OSError):
        store._write_text(target, "new content")

    # Verify no temp files were leaked in the parent directory
    parent_dir = target.parent
    temp_files = [f for f in os.listdir(parent_dir) if f.startswith(".tmp")]
    assert len(temp_files) == 0, f"leaked temp files: {temp_files}"


def test_supervisor_reset_loop_guard_resets_circuit_breaker(tmp_path) -> None:
    """reset_loop_guard must clear the guard state so future continue_goal() calls can proceed."""
    from agentic_harness.core.loop_guard import LoopGuard
    from agentic_harness.core.worker import WorkerResult

    guard = LoopGuard(max_continues=2, state_path=tmp_path / "guard.json")

    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=RecordingWorker(WorkerResult(success=True, summary="ok")),
        loop_guard=guard,
    )

    goal = supervisor.start("loop guard test")
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW

    # Transition back to IN_PROGRESS to continue
    goal.transition(GoalStatus.IN_PROGRESS, reason="reset for next iteration")
    supervisor.store.write_goal(goal)

    # Second continue should trip the circuit breaker (max_continues=2, so 2nd event trips)
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.FAILED
    assert "loop guard tripped" in goal.error

    # Reset the loop guard
    result = supervisor.reset_loop_guard()
    assert result is True

    # Restart the goal (FAILED -> PLANNING)
    goal = supervisor.restart()
    assert goal.status is GoalStatus.PLANNING

    # Now continue should work again (1 event after reset)
    goal = supervisor.continue_goal()
    assert goal.status is GoalStatus.REVIEW


def test_supervisor_reset_loop_guard_requires_active_goal(tmp_path) -> None:
    """reset_loop_guard must raise NoActiveGoalError when no goal is active."""
    supervisor = Supervisor(project_dir=tmp_path)

    with pytest.raises(NoActiveGoalError):
        supervisor.reset_loop_guard()
