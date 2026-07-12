from pathlib import Path

import pytest

from evaluation.run_real_agent_comparison import load_tasks, materialize


def test_preregistered_real_agent_manifest_has_ten_tasks() -> None:
    tasks = load_tasks(Path("evaluation/real_agent_tasks.json"))
    assert len(tasks) == 10
    assert len({task["id"] for task in tasks}) == 10


def test_materialize_exposes_initial_but_not_expected_answer(tmp_path: Path) -> None:
    task = load_tasks(Path("evaluation/real_agent_tasks.json"))[0]
    materialize(tmp_path / "workspace", task)
    target = tmp_path / "workspace" / task["path"]
    assert target.read_text(encoding="utf-8") == task["initial"]
    assert task["expected"] not in [
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "workspace").rglob("*")
        if path.is_file()
    ]


def test_load_tasks_rejects_non_preregistered_task_count(tmp_path: Path) -> None:
    manifest = tmp_path / "tasks.json"
    manifest.write_text(
        '{"schema":"agentic_harness.real_agent_tasks.v1","tasks":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly ten"):
        load_tasks(manifest)
