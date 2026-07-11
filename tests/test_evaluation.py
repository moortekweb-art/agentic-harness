from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation"
MANIFEST = EVALUATION / "tasks.json"
RUNNER = EVALUATION / "run_gate_benchmark.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mini_manifest(path: Path) -> Path:
    tasks = [
        {
            "id": "config-update-correct-first-try",
            "payload": "config-update",
            "maintenance_kind": "config_update",
            "objective": "Update the retry limit from two to three.",
            "path": "service.ini",
            "initial": "retries=2\n",
            "expected": "retries=3\n",
            "incorrect": "retries=4\n",
            "case": "true_completion",
            "behavior": "correct_first_try",
        },
        {
            "id": "config-update-false-then-repair",
            "payload": "config-update",
            "maintenance_kind": "config_update",
            "objective": "Update the retry limit from two to three.",
            "path": "service.ini",
            "initial": "retries=2\n",
            "expected": "retries=3\n",
            "incorrect": "retries=4\n",
            "case": "premature_false_claim",
            "behavior": "false_then_repair",
        },
        {
            "id": "config-update-persistent-false",
            "payload": "config-update",
            "maintenance_kind": "config_update",
            "objective": "Update the retry limit from two to three.",
            "path": "service.ini",
            "initial": "retries=2\n",
            "expected": "retries=3\n",
            "incorrect": "retries=4\n",
            "case": "premature_false_claim",
            "behavior": "persistent_false_complete",
        },
        {
            "id": "config-update-exit-failure-then-repair",
            "payload": "config-update",
            "maintenance_kind": "config_update",
            "objective": "Update the retry limit from two to three.",
            "path": "service.ini",
            "initial": "retries=2\n",
            "expected": "retries=3\n",
            "incorrect": "retries=4\n",
            "case": "recoverable_process_failure",
            "behavior": "exit_failure_then_repair",
        },
    ]
    path.write_text(
        json.dumps(
            {
                "schema": "agentic_harness.gate_evaluation_tasks.v1",
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_cli(
    manifest: Path, output_dir: Path, *, seed: int = 17
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--tasks",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--seed",
            str(seed),
            "--repetitions",
            "1",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_manifest_contains_24_balanced_maintenance_tasks() -> None:
    assert MANIFEST.exists()
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tasks = payload["tasks"]

    assert payload["schema"] == "agentic_harness.gate_evaluation_tasks.v1"
    assert len(tasks) == 24
    assert len({task["id"] for task in tasks}) == 24
    assert len({task["payload"] for task in tasks}) == 6
    assert {task["behavior"] for task in tasks} == {
        "correct_first_try",
        "false_then_repair",
        "persistent_false_complete",
        "exit_failure_then_repair",
    }
    assert all(
        sum(task["behavior"] == behavior for task in tasks) == 6
        for behavior in {
            "correct_first_try",
            "false_then_repair",
            "persistent_false_complete",
            "exit_failure_then_repair",
        }
    )
    assert sum(task["case"] == "true_completion" for task in tasks) == 6
    assert sum(task["case"] == "premature_false_claim" for task in tasks) == 12
    assert sum(task["case"] == "recoverable_process_failure" for task in tasks) == 6
    assert all(task["maintenance_kind"] for task in tasks)
    assert all(task["initial"] != task["expected"] for task in tasks)
    assert all(task["incorrect"] not in {task["initial"], task["expected"]} for task in tasks)


def test_cli_compares_pristine_arms_and_catches_false_claims(tmp_path: Path) -> None:
    assert RUNNER.exists()
    manifest = _mini_manifest(tmp_path / "tasks.json")
    output_dir = tmp_path / "results"

    completed = _run_cli(manifest, output_dir)

    assert completed.returncode == 0, completed.stderr
    rows = _read_jsonl(output_dir / "raw.jsonl")
    assert len(rows) == 8
    by_behavior_arm = {(row["behavior"], row["arm"]): row for row in rows}

    baseline_true = by_behavior_arm[("correct_first_try", "baseline")]
    harness_true = by_behavior_arm[("correct_first_try", "harness")]
    assert baseline_true["accepted"] is True
    assert baseline_true["verifier_pass"] is True
    assert harness_true["accepted"] is True
    assert harness_true["verifier_pass"] is True
    assert harness_true["attempts"] == 1

    baseline_repair = by_behavior_arm[("false_then_repair", "baseline")]
    harness_repair = by_behavior_arm[("false_then_repair", "harness")]
    assert baseline_repair["accepted"] is True
    assert baseline_repair["verifier_pass"] is False
    assert baseline_repair["false_success"] is True
    assert baseline_repair["caught_false_claim"] is False
    assert harness_repair["accepted"] is True
    assert harness_repair["verifier_pass"] is True
    assert harness_repair["false_success"] is False
    assert harness_repair["caught_false_claim"] is True
    assert harness_repair["attempts"] == 2

    baseline_persistent = by_behavior_arm[("persistent_false_complete", "baseline")]
    harness_persistent = by_behavior_arm[("persistent_false_complete", "harness")]
    assert baseline_persistent["accepted"] is True
    assert baseline_persistent["verifier_pass"] is False
    assert baseline_persistent["false_success"] is True
    assert harness_persistent["accepted"] is False
    assert harness_persistent["verifier_pass"] is False
    assert harness_persistent["caught_false_claim"] is True
    assert harness_persistent["attempts"] == 3

    baseline_exit = by_behavior_arm[("exit_failure_then_repair", "baseline")]
    harness_exit = by_behavior_arm[("exit_failure_then_repair", "harness")]
    assert baseline_exit["accepted"] is False
    assert baseline_exit["verifier_pass"] is False
    assert baseline_exit["claim_complete"] is False
    assert harness_exit["accepted"] is True
    assert harness_exit["verifier_pass"] is True
    assert harness_exit["attempts"] == 2

    for task_id in {row["task_id"] for row in rows}:
        task_rows = [row for row in rows if row["task_id"] == task_id]
        assert len({row["initial_tree_sha256"] for row in task_rows}) == 1
        assert {row["arm_position"] for row in task_rows} == {1, 2}
        assert len({row["agent_command_sha256"] for row in task_rows}) == 1

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation_type"] == "controlled_completion_gate_efficacy"
    assert summary["task_count"] == 4
    assert summary["arms"]["baseline"]["false_success_rate"] == 1.0
    assert summary["arms"]["harness"]["false_success_rate"] == 0.0
    assert summary["arms"]["harness"]["caught_false_claims"] == 2
    assert summary["arms"]["harness"]["verified_accepts"] == 3
    assert summary["arms"]["harness"]["recovered_tasks"] == 2
    assert summary["token_metrics_available"] is False
    assert all("tokens" not in row for row in rows)


def test_seeded_order_and_report_artifacts_are_reproducible(tmp_path: Path) -> None:
    manifest = _mini_manifest(tmp_path / "tasks.json")
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_run = _run_cli(manifest, first, seed=8128)
    second_run = _run_cli(manifest, second, seed=8128)

    assert first_run.returncode == second_run.returncode == 0
    first_rows = _read_jsonl(first / "raw.jsonl")
    second_rows = _read_jsonl(second / "raw.jsonl")
    first_order = [(row["task_id"], row["arm"], row["arm_position"]) for row in first_rows]
    second_order = [(row["task_id"], row["arm"], row["arm_position"]) for row in second_rows]
    assert first_order == second_order

    environment = json.loads((first / "environment.json").read_text(encoding="utf-8"))
    serialized_environment = json.dumps(environment, sort_keys=True)
    source_checksums = environment["source_checksums"]
    assert (
        source_checksums["evaluation/tasks.json"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert "evaluation/scripted_coding_agent.py" in source_checksums
    assert "agentic_harness/adapters/coding_agent.py" in source_checksums
    assert environment["seed"] == 8128
    assert environment["repetitions"] == 1
    assert environment["agent_command"] == [
        "python",
        "evaluation/scripted_coding_agent.py",
    ]
    assert environment["verifier_command"] == [
        "python",
        "evaluation/verify_fixture.py",
    ]
    assert str(ROOT) not in serialized_environment

    markdown = (first / "summary.md").read_text(encoding="utf-8")
    assert "controlled completion-gate efficacy evaluation" in markdown.lower()
    assert "not real-model performance" in markdown.lower()
    assert (first / "summary.json").is_file()
    assert (first / "raw.jsonl").is_file()


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [("id", "../escape"), ("path", "."), ("path", "../escape.txt")],
)
def test_manifest_rejects_unsafe_workspace_identifiers(
    tmp_path: Path,
    field: str,
    unsafe_value: str,
) -> None:
    from evaluation.run_gate_benchmark import load_tasks

    manifest = _mini_manifest(tmp_path / "tasks.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["tasks"][0][field] = unsafe_value
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe"):
        load_tasks(manifest)
