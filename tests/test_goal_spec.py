from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from agentic_harness import Goal, Supervisor
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.autonomy import AutonomousRunner
from agentic_harness.core.errors import GoalConflictError, StateLockError
from agentic_harness.core.goal_spec import (
    GoalRequirement,
    GoalSpec,
    preserved_objective_spec,
)
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


FIXED_TIME = "2026-07-17T00:00:00Z"


def make_spec(*, requirement_text: str = "Add validation.") -> GoalSpec:
    return GoalSpec(
        objective="Add validation.",
        requirements=(GoalRequirement(id="R1", text=requirement_text),),
        derivation="harness_derived",
        approval="automatic",
        created_at=FIXED_TIME,
    )


def test_goal_spec_is_frozen_and_hash_addressed() -> None:
    spec = make_spec()

    assert spec.sha256 == spec.computed_sha256()
    assert GoalSpec.from_dict(spec.to_dict()) == spec
    with pytest.raises(FrozenInstanceError):
        spec.objective = "Replace objective"  # type: ignore[misc]


def test_goal_spec_hash_changes_with_canonical_content() -> None:
    first = make_spec()
    second = make_spec(requirement_text="Reject invalid input.")

    assert first.sha256 != second.sha256


def test_goal_spec_rejects_duplicate_requirement_ids() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        GoalSpec(
            objective="Do both things.",
            requirements=(
                GoalRequirement(id="R1", text="Do the first thing."),
                GoalRequirement(id="R1", text="Do the second thing."),
            ),
            derivation="harness_derived",
            approval="automatic",
            created_at=FIXED_TIME,
        )


def test_goal_spec_rejects_tampered_hash() -> None:
    payload = make_spec().to_dict()
    payload["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="does not match"):
        GoalSpec.from_dict(payload)


def test_artifact_store_writes_spec_separately_and_once(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal("Add validation.", id="goal-spec-store")
    store.write_goal(goal)
    spec = make_spec()

    path = store.write_goal_spec(goal, spec)

    assert path.name == "goal-spec.json"
    assert store.read_goal_spec(goal.id) == spec
    assert "goal_spec" not in json.loads(
        (store.goal_dir(goal) / "state.json").read_text(encoding="utf-8")
    )
    assert store.write_goal_spec(goal, spec) == path
    with pytest.raises(GoalConflictError, match="cannot be replaced"):
        store.write_goal_spec(
            goal,
            GoalSpec(
                objective=goal.objective,
                requirements=(GoalRequirement(id="R2", text="Different requirement."),),
                derivation="harness_derived",
                approval="automatic",
                created_at=FIXED_TIME,
            ),
        )


def test_artifact_store_rejects_symlinked_spec(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal("Add validation.", id="goal-spec-symlink")
    store.write_goal(goal)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    store.goal_spec_path(goal).symlink_to(outside)

    with pytest.raises(GoalConflictError, match="must not be a symlink"):
        store.write_goal_spec(goal, make_spec())
    with pytest.raises(StateLockError, match="corrupted or missing"):
        store.read_goal_spec(goal.id)


class CompleteWorker:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.saw_frozen_spec = False

    def run(self, goal: Goal) -> WorkerResult:
        spec_path = (
            self.project_dir
            / ".agentic-harness"
            / "runs"
            / goal.id
            / "goal-spec.json"
        )
        self.saw_frozen_spec = spec_path.is_file()
        return WorkerResult(
            success=True,
            summary="complete",
            outcome={
                "status": "complete",
                "summary": "complete",
                "checkpoint": "checked",
                "current_subgoal": "final audit",
                "plan": [{"step": "work", "status": "completed"}],
                "requirement_status": [
                    {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
                ],
                "blockers": [],
            },
        )


def test_autonomous_runner_freezes_spec_before_worker_execution(tmp_path: Path) -> None:
    reviewer = DeterministicReviewer(
        [
            ReviewCriterion(
                name="check",
                check=lambda goal: (True, "passed"),
                description="Independent check",
            )
        ]
    )
    worker = CompleteWorker(tmp_path)
    supervisor = Supervisor(project_dir=tmp_path, worker=worker, reviewer=reviewer)

    goal = AutonomousRunner(supervisor).run("Implement all requested validation.")
    spec = supervisor.store.read_goal_spec(goal.id)
    state = json.loads(
        (supervisor.store.goal_dir(goal) / "state.json").read_text(encoding="utf-8")
    )

    assert worker.saw_frozen_spec is True
    assert spec.objective == goal.objective
    assert spec.requirements == (GoalRequirement(id="R1", text=goal.objective),)
    assert spec.derivation == "harness_preserved_objective"
    assert state["metadata"]["autonomy"]["goal_spec_sha256"] == spec.sha256
    assert "goal_spec" not in state["metadata"]["autonomy"]


def test_preserved_objective_spec_keeps_complete_objective_as_requirement() -> None:
    objective = "Add validation, update documentation, and add regression tests."

    spec = preserved_objective_spec(objective)

    assert spec.objective == objective
    assert spec.requirements == (GoalRequirement(id="R1", text=objective),)
    assert spec.derivation == "harness_preserved_objective"
    assert spec.approval == "automatic"
