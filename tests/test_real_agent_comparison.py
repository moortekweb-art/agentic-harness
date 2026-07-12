from pathlib import Path

import pytest

from evaluation.run_real_agent_comparison import load_tasks, materialize
from evaluation import real_agent_worker


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


def test_real_agent_wrapper_emits_complete_external_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_HARNESS_INSTRUCTION", "make the change")
    monkeypatch.setenv("REAL_AGENT_TRANSCRIPT", str(tmp_path / "transcript.log"))
    monkeypatch.setattr(
        real_agent_worker.subprocess,
        "run",
        lambda *args, **kwargs: real_agent_worker.subprocess.CompletedProcess(
            args[0], 0, "done", ""
        ),
    )

    assert real_agent_worker.main() == 0
    payload = __import__("json").loads(capsys.readouterr().out.split("=", 1)[1])
    assert payload["current_subgoal"]
    assert payload["checkpoint"]
    assert payload["plan"]
    assert payload["requirements"][0]["evidence"] == ["review:1"]
    assert payload["blockers"] == []
