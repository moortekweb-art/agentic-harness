from pathlib import Path
import json
import subprocess

import pytest

from evaluation.run_real_agent_comparison import load_tasks, materialize, verifier_command, verify
from evaluation import real_agent_worker


def test_preregistered_real_agent_manifest_has_ten_tasks() -> None:
    tasks = load_tasks(Path("evaluation/real_agent_tasks.json"))
    assert len(tasks) == 10
    assert len({task["id"] for task in tasks}) == 10


def test_harder_manifest_materializes_multiple_files_without_expected_answers(
    tmp_path: Path,
) -> None:
    tasks = load_tasks(Path("evaluation/hard_real_agent_tasks.json"))
    task = tasks[0]
    workspace = tmp_path / "workspace"
    materialize(workspace, task)
    assert sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
    ) == sorted(task["files"])
    contents = {path.read_text(encoding="utf-8") for path in workspace.rglob("*") if path.is_file()}
    assert not any(expected in contents for expected in task["expected_files"].values())


def test_harder_verifier_accepts_behaviorally_equivalent_implementation(tmp_path: Path) -> None:
    task_file = Path("evaluation/hard_real_agent_tasks.json").resolve()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "routes.py").write_text(
        "def unique_routes(routes):\n"
        "    answer = []\n"
        "    for route in routes:\n"
        "        if route not in answer:\n"
        "            answer.append(route)\n"
        "    return answer\n",
        encoding="utf-8",
    )
    assert verify(workspace, verifier_command(task_file, "ordered-dedupe"))


@pytest.mark.parametrize(
    ("task_id", "filename", "source"),
    [
        ("none-and-zero", "limits.py", "def effective_limit(value, default):\n    return default if value is None or value < 0 else value\n"),
        ("ordered-dedupe", "routes.py", "def unique_routes(routes):\n    return list(dict.fromkeys(x.lower() for x in routes))\n"),
        ("preserve-unknown-json", "settings.py", "import json\ndef set_enabled(text, enabled):\n    data=json.loads(text); data['enabled']=enabled; return json.dumps(data)\n"),
    ],
)
def test_harder_verifier_rejects_invariant_violations(
    tmp_path: Path, task_id: str, filename: str, source: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / filename).write_text(source, encoding="utf-8")
    assert not verify(
        workspace,
        verifier_command(Path("evaluation/hard_real_agent_tasks.json").resolve(), task_id),
    )


@pytest.mark.parametrize("raw_path", ["../escape", "/tmp/escape"])
def test_materialize_rejects_paths_outside_workspace(tmp_path: Path, raw_path: str) -> None:
    with pytest.raises(ValueError, match="escapes workspace"):
        materialize(tmp_path / "workspace", {"files": {raw_path: "bad"}})


@pytest.mark.parametrize("bad_id", ["..", "../escape", "/absolute", "x-direct"])
def test_load_tasks_rejects_unsafe_or_ambiguous_ids(tmp_path: Path, bad_id: str) -> None:
    payload = json.loads(Path("evaluation/hard_real_agent_tasks.json").read_text())
    payload["tasks"][0]["id"] = bad_id
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="safe unambiguous"):
        load_tasks(manifest)


def test_load_tasks_rejects_duplicate_ids(tmp_path: Path) -> None:
    payload = json.loads(Path("evaluation/hard_real_agent_tasks.json").read_text())
    payload["tasks"][1]["id"] = payload["tasks"][0]["id"]
    manifest = tmp_path / "tasks.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_tasks(manifest)


def test_verify_records_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        ),
    )
    status: dict[str, bool] = {}
    assert not verify(tmp_path, ["verifier"], status=status, timeout=1)
    assert status == {"timed_out": True}


def test_boundary_verifier_rejects_literal_special_case(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "window.py").write_text(
        "def in_window(value, start, end):\n"
        "    if (start, end) == (1, 2):\n"
        "        return True\n"
        "    return start <= value <= end\n",
        encoding="utf-8",
    )
    assert not verify(
        workspace,
        verifier_command(
            Path("evaluation/hard_real_agent_tasks.json").resolve(), "boundary-window"
        ),
    )


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
    monkeypatch.setenv("REAL_AGENT_MODEL", "fixed-model")
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


def test_real_agent_wrapper_records_timeout_and_returns_124(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transcript = tmp_path / "transcript.log"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_HARNESS_INSTRUCTION", "make the change")
    monkeypatch.setenv("REAL_AGENT_TRANSCRIPT", str(transcript))
    monkeypatch.setenv("REAL_AGENT_MODEL", "fixed-model")
    monkeypatch.setattr(
        real_agent_worker.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], 180, output="partial out", stderr="partial err")
        ),
    )

    assert real_agent_worker.main() == 124
    assert "partial out" in (tmp_path / "transcript.attempt-1.log").read_text(
        encoding="utf-8"
    )
    payload = __import__("json").loads(capsys.readouterr().out.split("=", 1)[1])
    assert payload["status"] == "failed"
    assert payload["blockers"] == ["coding agent timed out"]


def test_worker_command_pins_requested_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_HARNESS_INSTRUCTION", "make the change")
    monkeypatch.setenv("REAL_AGENT_TRANSCRIPT", str(tmp_path / "transcript.log"))
    monkeypatch.setenv("REAL_AGENT_MODEL", "fixed-model")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(real_agent_worker.subprocess, "run", fake_run)
    assert real_agent_worker.main() == 0
    assert commands[0][commands[0].index("--model") + 1] == "fixed-model"


def test_real_agent_wrapper_preserves_each_attempt_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "task-harness.log"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_HARNESS_INSTRUCTION", "make the change")
    monkeypatch.setenv("REAL_AGENT_TRANSCRIPT", str(base))
    monkeypatch.setenv("REAL_AGENT_MODEL", "fixed-model")
    monkeypatch.setattr(
        real_agent_worker.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "done", ""),
    )

    assert real_agent_worker.main() == 0
    assert real_agent_worker.main() == 0

    assert (tmp_path / "task-harness.attempt-1.log").is_file()
    assert (tmp_path / "task-harness.attempt-2.log").is_file()


def test_real_agent_wrapper_records_unavailable_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_HARNESS_INSTRUCTION", "make the change")
    monkeypatch.setenv("REAL_AGENT_TRANSCRIPT", str(tmp_path / "task.log"))
    monkeypatch.setenv("REAL_AGENT_MODEL", "fixed-model")
    monkeypatch.setattr(
        real_agent_worker.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("codex missing")),
    )

    assert real_agent_worker.main() == 127
    payload = __import__("json").loads(capsys.readouterr().out.split("=", 1)[1])
    assert payload["status"] == "failed"
    assert payload["blockers"] == ["coding agent unavailable"]
