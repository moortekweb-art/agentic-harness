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
    derive_goal_requirements,
    derived_objective_spec,
    preserved_objective_spec,
)
from agentic_harness.core.review import DeterministicReviewer, ReviewCriterion
from agentic_harness.core.worker import WorkerResult


FIXED_TIME = "2026-07-17T00:00:00Z"


def test_derives_explicit_action_series_without_omitting_clauses() -> None:
    requirements = derive_goal_requirements(
        "Add input validation, update documentation, and add regression tests."
    )

    assert [item.to_dict() for item in requirements] == [
        {"id": "R1", "text": "Add input validation."},
        {"id": "R2", "text": "Update documentation."},
        {"id": "R3", "text": "Add regression tests."},
    ]


def test_derives_numbered_completion_conditions_in_source_order() -> None:
    spec = derived_objective_spec(
        "Complete the release:\n1. Run the full test suite\n2. Update the release notes"
    )

    assert spec.derivation == "harness_derived"
    assert [item.text for item in spec.requirements] == [
        "Complete the release.",
        "Run the full test suite.",
        "Update the release notes.",
    ]


def test_list_derivation_keeps_trailing_scope_in_source_order() -> None:
    requirements = derive_goal_requirements(
        "Release requirements:\n- Run tests\n- Update notes\nKeep the existing API compatible."
    )

    assert [item.text for item in requirements] == [
        "Release requirements.",
        "Run tests.",
        "Update notes.",
        "Keep the existing API compatible.",
    ]


def test_ambiguous_prose_remains_one_full_objective_requirement() -> None:
    objective = "Improve the interface for new users and the existing local workflow."

    requirements = derive_goal_requirements(objective)

    assert requirements == (GoalRequirement(id="R1", text=objective),)


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


def test_artifact_store_appends_and_reads_newest_spec_revision(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal("Add validation.", id="goal-spec-revision")
    store.write_goal(goal)
    original = make_spec()
    store.write_goal_spec(goal, original)
    revised = GoalSpec(
        objective=goal.objective,
        requirements=(GoalRequirement(id="R1", text="Reject invalid input."),),
        derivation="operator_authored",
        approval="operator_approved",
        created_at="2026-07-17T00:01:00Z",
    )

    path, version = store.write_goal_spec_revision(
        goal,
        revised,
        previous_sha256=original.sha256,
    )

    assert (path.name, version) == ("goal-spec-v2.json", 2)
    assert store.read_goal_spec(goal.id) == revised
    assert store.read_goal_spec_version(goal.id) == 2
    with pytest.raises(GoalConflictError, match="changed before"):
        store.write_goal_spec_revision(
            goal,
            revised,
            previous_sha256=original.sha256,
        )


def test_artifact_store_rejects_revision_pointer_rollback(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal("Add validation.", id="goal-spec-pointer-rollback")
    store.write_goal(goal)
    original = make_spec()
    store.write_goal_spec(goal, original)
    revised = GoalSpec(
        objective=goal.objective,
        requirements=(GoalRequirement(id="R1", text="Reject invalid input."),),
        derivation="operator_authored",
        approval="operator_approved",
        created_at="2026-07-17T00:01:00Z",
    )
    store.write_goal_spec_revision(goal, revised, previous_sha256=original.sha256)
    (store.goal_dir(goal) / "goal-spec-v3.json").write_text(
        json.dumps(revised.to_dict()),
        encoding="utf-8",
    )

    with pytest.raises(StateLockError, match="corrupted or missing"):
        store.read_goal_spec(goal.id)


def test_artifact_store_rejects_broken_revision_chain(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal("Add validation.", id="goal-spec-broken-chain")
    store.write_goal(goal)
    original = make_spec()
    store.write_goal_spec(goal, original)
    revised = GoalSpec(
        objective=goal.objective,
        requirements=(GoalRequirement(id="R1", text="Reject invalid input."),),
        derivation="operator_authored",
        approval="operator_approved",
        created_at="2026-07-17T00:01:00Z",
    )
    store.write_goal_spec_revision(goal, revised, previous_sha256=original.sha256)
    pointer = store.current_goal_spec_pointer_path(goal)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["previous_sha256"] = "0" * 64
    pointer.write_text(json.dumps(payload), encoding="utf-8")

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
                covers=("R1",),
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


def test_autonomous_runner_derives_and_verifies_every_explicit_clause(
    tmp_path: Path,
) -> None:
    objective = "Add input validation, update documentation, and add regression tests."

    class DerivedCompleteWorker:
        def run(self, goal: Goal) -> WorkerResult:
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
                        {"id": requirement_id, "status": "satisfied", "evidence": ["review:1"]}
                        for requirement_id in ("R1", "R2", "R3")
                    ],
                    "blockers": [],
                },
            )

    reviewer = DeterministicReviewer(
        [
            ReviewCriterion(
                name="complete-check",
                check=lambda goal: (True, "passed"),
                covers=("*",),
            )
        ]
    )
    supervisor = Supervisor(
        project_dir=tmp_path,
        worker=DerivedCompleteWorker(),
        reviewer=reviewer,
    )

    goal = AutonomousRunner(supervisor).run(objective)
    spec = supervisor.store.read_goal_spec(goal.id)

    assert [item.id for item in spec.requirements] == ["R1", "R2", "R3"]
    assert goal.metadata["autonomy"]["completion_audit"]["passed"] is True
    assert goal.review["criteria"][0]["covers"] == ["R1", "R2", "R3"]


def test_preserved_objective_spec_keeps_complete_objective_as_requirement() -> None:
    objective = "Add validation, update documentation, and add regression tests."

    spec = preserved_objective_spec(objective)

    assert spec.objective == objective
    assert spec.requirements == (GoalRequirement(id="R1", text=objective),)
    assert spec.derivation == "harness_preserved_objective"
    assert spec.approval == "automatic"
