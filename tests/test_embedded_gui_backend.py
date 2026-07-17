from __future__ import annotations

import json
import hashlib
import sys
import time
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentic_harness.gui import backend as gui_backend_module
from agentic_harness.cli import write_goal_report
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.autonomy import AUTONOMY_CONTRACT, AutonomousRunner
from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.core.factory import build_supervisor
from agentic_harness.core.goal_spec import GoalRequirement, GoalSpec
from agentic_harness.core.providers import ProviderProfile
from agentic_harness.core.safety import split_command
from agentic_harness.core.state import Goal, GoalStatus


def _configure_scripted_agent(
    project: Path,
    *,
    assurance_mode: str = "specification_frozen",
    declare_review_coverage: bool = True,
) -> None:
    worker = project / "scripted_agent.py"
    worker.write_text(
        """
import json
from pathlib import Path

Path("result.txt").write_text("finished", encoding="utf-8")
outcome = {
    "status": "complete",
    "summary": "Created and verified result.txt.",
    "checkpoint": "verified",
    "current_subgoal": "final verification complete",
    "plan": [
        {"step": "Create result", "status": "completed"},
        {"step": "Verify result", "status": "completed"},
    ],
    "requirement_status": [
        {
            "id": "R1",
            "status": "satisfied",
            "evidence": ["review:1"],
        }
    ],
    "blockers": [],
}
print("HARNESS_RESULT_JSON=" + json.dumps(outcome))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config_dir = project / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                f"assurance_mode: {assurance_mode}",
                "worker:",
                "  type: coding_agent",
                "  coding_agent_command:",
                f"    - {sys.executable}",
                f"    - {worker}",
                "review_command:",
                f"  - {sys.executable}",
                "  - -c",
                "  - \"from pathlib import Path; assert Path('result.txt').read_text() == 'finished'\"",
                *(
                    ["review_covers:", "  - '*'"]
                    if declare_review_coverage
                    else []
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _wait_for_terminal(backend: EmbeddedExecutionBackend) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = backend.status()
        if task["status"] in {"done", "blocked", "failed"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task did not finish: {backend.status()}")


def test_embedded_backend_runs_public_engine_from_start_to_verified_finish(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)

    started = backend.start(
        {
            "objective": "Create result.txt and prove its exact contents",
            "safe_areas": ["result.txt"],
            "checks": [],
        }
    )
    finished = _wait_for_terminal(backend)

    assert started["id"]
    assert started["metadata"]["strategy"] == {
        "key": "plan",
        "label": "Plan first",
        "budget_profile": "balanced",
    }
    assert finished["id"] == started["id"]
    assert finished["contract"] == "agentic_harness.gui_task.v2"
    assert finished["status"] == "done"
    assert finished["progress"]["percent"] == 100
    assert finished["current"]["checkpoint"] == "verified"
    assert finished["current"]["current_subgoal"] == "final verification complete"
    assert finished["plan"][0]["status"] == "completed"
    assert finished["requirements"][0]["status"] == "satisfied"
    assert any(row["passed"] is True for row in finished["verification"])


def test_embedded_backend_does_not_infer_wildcard_review_coverage(tmp_path) -> None:
    _configure_scripted_agent(tmp_path, declare_review_coverage=False)
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.start(
        {
            "objective": "Create result.txt and prove its exact contents",
            "safe_areas": ["result.txt"],
            "checks": [],
        }
    )
    finished = _wait_for_terminal(backend)

    assert finished["status"] == "blocked"
    assert finished["final_result"]["accepted"] is False
    assert "requirement R1 cites ineligible evidence: review:1" in str(
        finished["summary"]
    )


def test_embedded_high_assurance_requires_visible_approval_before_worker(
    tmp_path,
) -> None:
    _configure_scripted_agent(tmp_path, assurance_mode="high_assurance")
    backend = EmbeddedExecutionBackend(tmp_path)

    backend.start(
        {
            "objective": "Create result.txt only after approval",
            "safe_areas": ["result.txt"],
            "checks": [],
        }
    )
    deadline = time.monotonic() + 5
    pending: dict[str, object] = {}
    while time.monotonic() < deadline:
        pending = backend.status()
        if pending["status"] == "needs_review":
            break
        time.sleep(0.02)

    assert pending["status"] == "needs_review"
    assert pending["needs_human"] is True
    assert pending["allowed_actions"][0]["action"] == "approve_spec"
    assert not (tmp_path / "result.txt").exists()

    backend.approve_specification()
    finished = _wait_for_terminal(backend)

    assert finished["status"] == "done"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "finished"
    assert any(row["path"] == "result.txt" for row in finished["changed_files"])
    assert finished["final_result"]["accepted"] is True
    assert finished["allowed_actions"] == [{"action": "new_task", "enabled": True}]
    assert finished["metadata"]["budget"]["limits"]["max_cycles"] == 20
    assert "stdout" not in json.dumps(finished).lower()


def test_embedded_high_assurance_exposes_plain_amendment_preview(tmp_path) -> None:
    _configure_scripted_agent(tmp_path, assurance_mode="high_assurance")
    backend = EmbeddedExecutionBackend(tmp_path)
    goal = Goal("Use the requested API.", id="amendment-preview")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="planned")
    goal.transition(GoalStatus.REVIEW, reason="worker requested amendment")
    spec = GoalSpec(
        objective=goal.objective,
        requirements=(GoalRequirement(id="R1", text="Use the requested API."),),
        derivation="harness_preserved_objective",
        approval="operator_approved",
    )
    goal.metadata["autonomy"] = {
        "contract": AUTONOMY_CONTRACT,
        "status": "awaiting_specification_amendment",
        "goal_spec_sha256": spec.sha256,
        "requirement_status": [],
        "operator_intervention_required": True,
        "specification_amendment": {
            "reason": "The requested API is unavailable.",
            "proposed_changes": [
                {
                    "operation": "replace",
                    "requirement_id": "R1",
                    "new_text": "Use the supported replacement API.",
                }
            ],
        },
    }
    backend.store.init()
    backend.store.write_goal_spec(goal, spec)
    backend.store.write_approved_goal_spec(goal, spec)
    backend.store.write_goal(goal)

    task = backend.status()

    assert task["status"] == "needs_review"
    assert task["allowed_actions"][0]["action"] == "approve_spec"
    assert task["metadata"]["specification_review"] == {
        "goal_id": "amendment-preview",
        "goal_spec_sha256": spec.sha256,
        "kind": "amendment",
        "reason": "The requested API is unavailable.",
        "version": 1,
        "conditions": [
            {"id": "R1", "text": "Use the supported replacement API."}
        ],
    }


def test_embedded_high_assurance_rejects_stale_review_binding(tmp_path) -> None:
    _configure_scripted_agent(tmp_path, assurance_mode="high_assurance")
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start(
        {
            "objective": "First reviewed task",
            "safe_areas": ["result.txt"],
            "checks": [],
        }
    )
    deadline = time.monotonic() + 5
    reviewed: dict[str, object] = {}
    while time.monotonic() < deadline:
        reviewed = backend.status()
        if reviewed["status"] == "needs_review":
            break
        time.sleep(0.02)
    binding = reviewed["metadata"]["specification_review"]  # type: ignore[index]

    replacement = Goal("Replacement task", id="replacement-task")
    replacement_spec = GoalSpec(
        objective=replacement.objective,
        requirements=(GoalRequirement(id="R1", text="Replacement task"),),
        derivation="harness_preserved_objective",
        approval="pending",
    )
    backend.store.write_goal_spec(replacement, replacement_spec)
    backend.store.write_goal(replacement)

    result = backend.approve_specification(
        ["First reviewed task"],
        expected_goal_id=str(binding["goal_id"]),
        expected_goal_spec_sha256=str(binding["goal_spec_sha256"]),
        expected_spec_version=int(binding["version"]),
    )

    assert result["status"] == "blocked"
    assert "no longer current" in str(result["summary"])
    assert backend.store.read_current_goal().id == "replacement-task"


def test_embedded_backend_persists_provider_independent_quick_strategy(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)

    started = backend.start(
        {
            "objective": "Create the result in one focused pass",
            "strategy": "quick",
        }
    )
    finished = _wait_for_terminal(backend)

    assert started["metadata"]["strategy"]["key"] == "quick"
    assert finished["metadata"]["strategy"]["key"] == "quick"
    assert finished["metadata"]["budget"]["limits"]["max_cycles"] == 3


def test_bounded_experiment_rejects_unenforced_installed_agent_scope(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)

    task = backend.start(
        {
            "objective": "Try one small change",
            "strategy": "experiment",
            "safe_areas": ["result.txt"],
        }
    )

    assert task["status"] == "blocked"
    assert task["allowed_actions"] == [{"action": "edit_strategy", "enabled": True}]
    assert "built-in model worker" in task["summary"]


def test_bounded_experiment_requires_explicit_scope_for_model_worker(tmp_path) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.configure(
        {
            "execution": "local_model",
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "local-model",
            "verification_command": f"{sys.executable} -c \"print('verified')\"",
        }
    )
    profile = ProviderProfile(
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        model="local-model",
    )
    backend._execution_validation = {
        "verified": True,
        "kind": "model_agent",
        "fingerprint": gui_backend_module._model_connection_fingerprint(
            profile,
            credential_source="none",
            credential_value="",
        ),
        "credential_source": "none",
    }

    task = backend.start(
        {
            "objective": "Try one small change",
            "strategy": "experiment",
        }
    )

    assert task["status"] == "blocked"
    assert "requires at least one allowed file or folder" in task["summary"]


def test_terminal_state_waits_for_the_driver_to_quiesce(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Finish only after the driver releases its locks"})
    _wait_for_terminal(backend)
    original_driver = backend._thread
    assert original_driver is not None
    original_driver.join(timeout=5)
    assert original_driver.is_alive() is False
    release = threading.Event()
    active_driver = threading.Thread(target=release.wait, daemon=True)
    active_driver.start()
    backend._thread = active_driver

    try:
        visible = backend.status()
        readiness = backend.readiness()
        current_history = next(row for row in backend.history() if row["id"] == visible["id"])
    finally:
        release.set()
        active_driver.join(timeout=5)

    assert visible["status"] == "checking"
    assert visible["allowed_actions"] == []
    assert visible["final_result"]["accepted"] is False
    assert readiness["state"] == "working"
    assert readiness["can_start"] is False
    assert current_history["status"] == "checking"
    assert current_history["allowed_actions"] == []
    assert current_history["final_result"]["accepted"] is False


def test_embedded_backend_history_survives_server_restart(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    first = EmbeddedExecutionBackend(tmp_path)
    started = first.start({"objective": "Create a durable result"})
    _wait_for_terminal(first)

    restarted = EmbeddedExecutionBackend(tmp_path)
    history = restarted.history()

    assert [task["id"] for task in history] == [started["id"]]
    assert history[0]["status"] == "done"
    assert restarted.status()["id"] == started["id"]


def test_terminal_history_keeps_each_goals_original_changed_files(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    supervisor = build_supervisor(tmp_path)

    first = supervisor.start("add the first file")
    (tmp_path / "a.txt").write_bytes(b"a\n")
    supervisor.continue_goal()
    supervisor.review()
    first = supervisor.accept(reason="first verified")
    first, _ = write_goal_report(supervisor, tmp_path, first)

    second = supervisor.start("add the second file")
    (tmp_path / "b.txt").write_bytes(b"b\n")
    supervisor.continue_goal()
    supervisor.review()
    second = supervisor.accept(reason="second verified")
    second, _ = write_goal_report(supervisor, tmp_path, second)

    history = EmbeddedExecutionBackend(tmp_path).history()
    by_id = {task["id"]: task for task in history}
    first_paths = {row["path"] for row in by_id[first.id]["changed_files"]}
    second_paths = {row["path"] for row in by_id[second.id]["changed_files"]}

    assert by_id[first.id]["status"] == "done"
    assert by_id[first.id]["final_result"]["accepted"] is True
    assert "a.txt" in first_paths
    assert "b.txt" not in first_paths
    assert "b.txt" in second_paths
    first_report = next(path for path in first.artifacts if Path(path).name == "report.md")
    assert (
        "Result: Verified done"
        in EmbeddedExecutionBackend(tmp_path).preview_artifact(
            first_report,
            goal_id=first.id,
        )["content"]
    )
    assert (
        EmbeddedExecutionBackend(tmp_path).preview_file(
            "a.txt",
            goal_id=first.id,
        )["content"]
        == "a\n"
    )
    with pytest.raises(ValueError, match="changed file"):
        EmbeddedExecutionBackend(tmp_path).preview_file("b.txt", goal_id=first.id)


def test_embedded_backend_resumes_orphaned_active_goal_after_service_restart(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    check = [
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('result.txt').read_text() == 'finished'",
    ]
    supervisor = build_supervisor(tmp_path, review_commands=[check])
    orphaned = supervisor.start(
        "Resume this goal after the service restarts",
        metadata={
            "interface": "gui",
            "safety": {
                "allowed_paths": ["result.txt"],
                "checks": [{"id": "check-1", "label": "Result check", "argv": check}],
                "path_enforcement": False,
                "secret_env_names": [],
                "preexisting_changes": [],
            },
        },
    )

    restarted = EmbeddedExecutionBackend(tmp_path)
    finished = _wait_for_terminal(restarted)

    assert restarted._thread is not None
    assert finished["id"] == orphaned.id
    assert finished["status"] == "done"


def test_embedded_backend_resumes_orphaned_review_without_rerunning_worker(
    tmp_path,
) -> None:
    _configure_scripted_agent(tmp_path)
    check = [
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('result.txt').read_text() == 'finished'",
    ]
    supervisor = build_supervisor(tmp_path, review_commands=[check])
    supervisor.start(
        "Finish verification after the service restarts",
        metadata={
            "interface": "gui",
            "safety": {
                "allowed_paths": ["result.txt"],
                "checks": [{"id": "check-1", "label": "Result check", "argv": check}],
                "path_enforcement": False,
                "secret_env_names": [],
                "preexisting_changes": [],
            },
        },
    )
    orphaned = supervisor.continue_goal()
    assert orphaned.status is GoalStatus.REVIEW

    restarted = EmbeddedExecutionBackend(tmp_path)
    finished = _wait_for_terminal(restarted)

    assert finished["id"] == orphaned.id
    assert finished["status"] == "done"
    assert finished["final_result"]["accepted"] is True


def test_embedded_backend_previews_only_changed_files_and_recorded_artifacts(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Create an inspectable result"})
    finished = _wait_for_terminal(backend)

    file_preview = backend.preview_file("result.txt")
    artifact_preview = backend.preview_artifact(finished["artifacts"][0]["path"])

    assert file_preview["path"] == "result.txt"
    assert file_preview["content"] == "finished"
    assert artifact_preview["path"] == finished["artifacts"][0]["path"]
    assert "HARNESS_RESULT_JSON" in artifact_preview["content"]
    with pytest.raises(ValueError, match="changed file"):
        backend.preview_file("scripted_agent.py")
    with pytest.raises(ValueError, match="recorded artifact"):
        backend.preview_artifact("/etc/passwd")

    secret = tmp_path / ".env"
    secret.write_text("OPAQUE_VALUE=must-not-preview\n", encoding="utf-8")
    (tmp_path / "result.txt").unlink()
    (tmp_path / "result.txt").symlink_to(secret)
    with pytest.raises(ValueError, match="unavailable"):
        backend.preview_file("result.txt")


def test_embedded_backend_reports_setup_needed_without_project_config(tmp_path) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    readiness = backend.readiness()
    task = backend.start({"objective": "Do useful work"})

    assert readiness["state"] == "setup_required"
    assert readiness["can_start"] is False
    assert task["status"] == "blocked"
    assert task["allowed_actions"][0]["action"] == "setup"


def test_safe_demo_runs_without_setup_credentials_or_selected_workspace_access(tmp_path) -> None:
    selected_project_file = tmp_path / "keep-me.txt"
    selected_project_file.write_text("untouched\n", encoding="utf-8")
    backend = EmbeddedExecutionBackend(tmp_path)

    setup = backend.setup()
    started = backend.start_demo()
    finished = _wait_for_terminal(backend)

    assert setup["configured"] is False
    assert setup["demo"] == {
        "available": True,
        "kind": "scripted_practice",
        "model_used": False,
        "workspace": "isolated_temporary",
        "summary": (
            "Runs the real harness in a temporary practice project with a scripted "
            "worker. No API key, model server, or selected-workspace access is used."
        ),
        "state": "ready",
    }
    assert started["metadata"]["demo"]["model_used"] is False
    assert started["progress"] == {
        "determinate": False,
        "percent": None,
        "label": "Waiting for independent verification",
    }
    assert finished["status"] == "done"
    assert finished["result_category"] == "verified_done"
    assert finished["final_result"]["accepted"] is True
    assert finished["final_result"]["attempts"] == 2
    assert finished["final_result"]["retries"] == 1
    assert [row["passed"] for row in finished["final_result"]["review_attempts"]] == [
        False,
        True,
    ]
    assert finished["final_result"]["worker_claim"]["label"] == ("Scripted worker report (not AI)")
    assert finished["metadata"]["execution"]["label"] == ("Safe demo · scripted worker")
    assert finished["changed_files"] == [{"path": "calculator.py", "status": "modified"}]
    verification_commands = finished["final_result"]["verification_commands"]
    assert len(verification_commands) == 1
    assert split_command(verification_commands[0]) == [
        "python",
        "-c",
        "from calculator import add; assert add(2, 3) == 5",
    ]
    assert selected_project_file.read_text(encoding="utf-8") == "untouched\n"
    assert not (tmp_path / "calculator.py").exists()
    serialized = json.dumps(finished)
    assert "agentic-harness-safe-demo-" not in serialized
    assert backend.readiness()["label"] == "Demo complete"


def test_local_model_detection_uses_fixed_loopback_probe(tmp_path, monkeypatch) -> None:
    class ModelsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/v1/models"
            payload = json.dumps(
                {
                    "data": [
                        {"id": "local/demo-model"},
                        {"id": "local/second-model"},
                        {"id": "local/demo-model"},
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        gui_backend_module,
        "_LOCAL_MODEL_PROBES",
        (
            {
                "template_key": "ollama_local",
                "label": "Ollama",
                "port": server.server_port,
                "endpoint": (f"http://127.0.0.1:{server.server_port}/v1/chat/completions"),
            },
        ),
    )
    try:
        payload = EmbeddedExecutionBackend(tmp_path).detect_local_models()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload["status"] == "found"
    assert payload["detected"] == [
        {
            "template_key": "ollama_local",
            "label": "Ollama",
            "endpoint": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            "model": "local/demo-model",
            "models": ["local/demo-model", "local/second-model"],
        }
    ]


def test_local_model_detection_ignores_server_without_model_ids(
    tmp_path,
    monkeypatch,
) -> None:
    class EmptyModelsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = b'{"data":[{}, {"id":"  "}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), EmptyModelsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        gui_backend_module,
        "_LOCAL_MODEL_PROBES",
        (
            {
                "template_key": "ollama_local",
                "label": "Ollama",
                "port": server.server_port,
                "endpoint": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            },
        ),
    )
    try:
        payload = EmbeddedExecutionBackend(tmp_path).detect_local_models()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload["status"] == "not_found"
    assert payload["detected"] == []


def test_embedded_backend_rejects_blank_goal_before_starting_thread(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)

    task = backend.start({"objective": "   "})

    assert task["status"] == "blocked"
    assert "what you want done" in task["summary"].lower()
    assert backend.status()["status"] == "ready"


@contextmanager
def scripted_model_server(responses: list[dict[str, object]]):
    queue = list(responses)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            action = queue.pop(0)
            payload = json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(action)}}],
                    "usage": {"total_tokens": 5},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_embedded_backend_local_model_edits_checks_and_finishes_end_to_end(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "value.txt").write_text("before", encoding="utf-8")
    actions: list[dict[str, object]] = [
        {
            "action": "report_outcome",
            "arguments": {
                "status": "progress",
                "summary": "Connection test passed.",
            },
        },
        {
            "action": "read_file",
            "arguments": {"path": "src/value.txt"},
            "plan": [{"step": "Update value", "status": "in_progress"}],
            "requirement_status": [],
            "current_subgoal": "inspect the value",
            "checkpoint": "goal_started",
        },
        {
            "action": "replace_text",
            "arguments": {
                "path": "src/value.txt",
                "old": "before",
                "new": "after",
                "expected_sha256": hashlib.sha256(b"before").hexdigest(),
            },
            "plan": [{"step": "Update value", "status": "in_progress"}],
            "requirement_status": [
                {"id": "R1", "status": "pending", "evidence": []}
            ],
            "current_subgoal": "update the value",
            "checkpoint": "source_read",
        },
        {
            "action": "run_check",
            "arguments": {"check_id": "check-1"},
            "plan": [{"step": "Update value", "status": "completed"}],
            "requirement_status": [
                {"id": "R1", "status": "pending", "evidence": []}
            ],
            "current_subgoal": "verify the value",
            "checkpoint": "source_updated",
        },
        {
            "action": "report_outcome",
            "arguments": {
                "status": "complete",
                "summary": "Updated and verified the value.",
                "plan": [{"step": "Update value", "status": "completed"}],
                "requirement_status": [
                        {"id": "R1", "status": "satisfied", "evidence": ["review:1"]}
                ],
                "current_subgoal": "final verification complete",
                "checkpoint": "verified",
                "blockers": [],
            },
        },
    ]
    with scripted_model_server(actions) as endpoint:
        backend = EmbeddedExecutionBackend(tmp_path)
        tested = backend.test_connection(
            {"endpoint": endpoint, "model": "arbitrary-local-model-id"}
        )
        assert tested["structured_actions"] is True
        backend.configure(
            {
                "execution": "local_model",
                "endpoint": endpoint,
                "model": "arbitrary-local-model-id",
                "verification_command": (
                    f'{sys.executable} -c "from pathlib import Path; '
                    "assert Path('src/value.txt').read_text() == 'after'\""
                ),
            }
        )
        started = backend.start(
            {
                "objective": "Change the value from before to after and verify it",
                "safe_areas": ["src"],
            }
        )
        finished = _wait_for_terminal(backend)

    assert started["id"] == finished["id"]
    assert finished["status"] == "done"
    assert finished["metadata"]["worker"]["model"] == "arbitrary-local-model-id"
    assert [event["tool"]["name"] for event in finished["events"]] == [
        "read_file",
        "replace_text",
        "run_check",
        "report_outcome",
    ]
    assert (source / "value.txt").read_text(encoding="utf-8") == "after"
    report = next(row for row in finished["artifacts"] if row["name"] == "report.md")
    preview = backend.preview_artifact(report["path"])
    assert "Accepted: yes" in preview["content"]
    assert "Updated and verified the value" in preview["content"]


def test_terminal_payload_waits_for_its_durable_report(tmp_path, monkeypatch) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Create a result with terminal evidence"})
    finished = _wait_for_terminal(backend)
    report_path = next(row["path"] for row in finished["artifacts"] if row["name"] == "report.md")
    (tmp_path / report_path).unlink()
    with backend.store.locked():
        goal = backend.store.read_current_goal()
        assert goal is not None
        goal.artifacts.remove(report_path)
        backend.store.write_goal(goal)
    monkeypatch.setattr(backend, "_ensure_terminal_report", lambda goal: goal)

    visible = backend.status()

    assert visible["status"] == "checking"
    assert visible["final_result"]["accepted"] is False
    assert all(row["name"] != "report.md" for row in visible["artifacts"])


def test_terminal_report_hash_uses_the_persisted_file_bytes(tmp_path, monkeypatch) -> None:
    _configure_scripted_agent(tmp_path)
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Create a report with platform newlines"})
    finished = _wait_for_terminal(backend)
    report_path = next(row["path"] for row in finished["artifacts"] if row["name"] == "report.md")
    (tmp_path / report_path).unlink()
    with backend.store.locked():
        goal = backend.store.read_current_goal()
        assert goal is not None
        goal.artifacts.remove(report_path)
        goal.metadata.pop("terminal_report_state_sha256", None)
        goal.metadata.pop("terminal_report_content_sha256", None)
        backend.store.write_goal(goal)
    original_write_report = ArtifactStore.write_report

    def write_crlf_report(self, goal, content, name="report.md"):
        path = original_write_report(self, goal, content, name)
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        return path

    monkeypatch.setattr(ArtifactStore, "write_report", write_crlf_report)
    repaired = backend._ensure_terminal_report(goal)

    assert backend._terminal_report_ready(repaired) is True


def test_retryable_failed_goal_is_not_exposed_as_terminal(tmp_path, monkeypatch) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)
    goal = Goal(objective="Retry a failed independent check")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="planned")
    goal.transition(GoalStatus.REVIEW, reason="worker completed")
    goal.transition(GoalStatus.FAILED, reason="review failed")
    goal.metadata["autonomy"] = {
        "contract": "agentic_harness.autonomy.v1",
        "status": "checking",
        "operator_intervention_required": False,
    }
    goal.metadata["worker_outcome"] = {"summary": "untrusted worker success"}

    def reject_terminal_report(_goal):
        raise AssertionError("retryable state must not create terminal evidence")

    monkeypatch.setattr(backend, "_ensure_terminal_report", reject_terminal_report)

    visible = backend._task(goal)

    assert visible["status"] == "checking"
    assert visible["final_result"]["accepted"] is False


def test_retryable_failed_goal_resumes_without_terminal_evidence_after_restart(
    tmp_path, monkeypatch
) -> None:
    _configure_scripted_agent(tmp_path)
    store = ArtifactStore(tmp_path / ".agentic-harness")
    store.init()
    goal = Goal(objective="Resume after an interrupted blocker decision")
    goal.transition(GoalStatus.PLANNING, reason="started")
    goal.transition(GoalStatus.IN_PROGRESS, reason="planned")
    goal.transition(GoalStatus.REVIEW, reason="worker completed")
    goal.transition(GoalStatus.FAILED, reason="review failed")
    goal.metadata["autonomy"] = {
        "contract": "agentic_harness.autonomy.v1",
        "status": "checking",
        "operator_intervention_required": False,
    }
    goal.metadata["worker_outcome"] = {"summary": "untrusted worker success"}
    store.write_goal(goal)
    resumed_with: list[list[list[str]]] = []

    def record_resume(self, review_commands, policy):
        resumed_with.append(review_commands)

    monkeypatch.setattr(EmbeddedExecutionBackend, "_start_thread", record_resume)

    backend = EmbeddedExecutionBackend(tmp_path)
    readiness = backend.readiness()
    visible = backend.status()
    persisted = store.read_current_goal()

    assert resumed_with
    assert readiness["state"] == "working"
    assert readiness["can_start"] is False
    assert visible["status"] == "checking"
    assert not list(store.runs_dir.rglob("report.md"))
    assert persisted is not None
    assert all(Path(path).name != "report.md" for path in persisted.artifacts)


def test_terminal_report_is_refreshed_after_blocked_goal_recovers(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "assert Path('result.txt').read_text() == 'finished'",
            "assert Path('result.txt').read_text() == 'finished' and Path('accept.flag').exists()",
        ),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Recover after verification becomes available"})
    blocked = _wait_for_terminal(backend)
    assert blocked["status"] == "blocked"
    assert blocked["summary"] != "Created and verified result.txt."
    assert blocked["summary"] == "independent command failed with exit code 1"
    report_path = next(row["path"] for row in blocked["artifacts"] if row["name"] == "report.md")
    blocked_report = backend.preview_artifact(report_path)["content"]
    assert "Accepted: no" in blocked_report

    (tmp_path / "accept.flag").write_text("ready\n", encoding="utf-8")
    backend.continue_task("Verification dependency is now available.")
    finished = _wait_for_terminal(backend)
    final_report = backend.preview_artifact(report_path)["content"]

    assert finished["status"] == "done"
    assert "Result: Verified done" in final_report
    assert "Accepted: yes" in final_report
    assert final_report != blocked_report


def test_embedded_backend_stop_is_cooperative_and_preserves_evidence(tmp_path) -> None:
    worker = tmp_path / "slow_agent.py"
    worker.write_text(
        """
import json
import time
from pathlib import Path

Path("partial.txt").write_text("kept", encoding="utf-8")
time.sleep(0.2)
print("HARNESS_RESULT_JSON=" + json.dumps({
    "status": "complete",
    "summary": "late completion",
    "checkpoint": "late",
    "current_subgoal": "late",
    "plan": [{"step": "work", "status": "completed"}],
    "requirement_status": [{"id": "R1", "status": "satisfied", "evidence": ["late"]}],
    "blockers": [],
}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        "\n".join(
            [
                "version: 1",
                "worker:",
                "  type: coding_agent",
                "  coding_agent_command:",
                f"    - {sys.executable}",
                f"    - {worker}",
                "review_command:",
                f"  - {sys.executable}",
                "  - -c",
                "  - \"print('verified')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Do work that can be stopped safely"})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not (tmp_path / "partial.txt").exists():
        time.sleep(0.01)
    assert (tmp_path / "partial.txt").exists()

    stopping = backend.stop()
    stopped = _wait_for_terminal(backend)

    assert stopping["status"] in {"stopping", "failed"}
    assert stopped["status"] == "failed"
    assert stopped["final_result"]["accepted"] is False
    assert (tmp_path / "partial.txt").read_text(encoding="utf-8") == "kept"
    assert stopped["artifacts"]


def test_embedded_backend_stop_during_review_prevents_late_acceptance(tmp_path) -> None:
    _configure_scripted_agent(tmp_path)
    config_path = tmp_path / ".agentic-harness" / "config.yml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "from pathlib import Path; assert Path('result.txt').read_text() == 'finished'",
            "import time; from pathlib import Path; Path('review.started').write_text('yes'); time.sleep(0.5); print('verified')",
        ),
        encoding="utf-8",
    )
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Do not accept after stop during review"})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        goal = backend.store.read_current_goal()
        if (
            goal is not None
            and goal.status is GoalStatus.REVIEW
            and (tmp_path / "review.started").exists()
        ):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("goal never reached review")

    backend.stop()
    stopped = _wait_for_terminal(backend)

    assert stopped["status"] == "failed"
    assert stopped["final_result"]["accepted"] is False
    assert backend.store.read_current_goal().metadata.get("accepted") is not True


def test_embedded_backend_stop_during_completion_audit_prevents_acceptance(
    tmp_path,
    monkeypatch,
) -> None:
    _configure_scripted_agent(tmp_path)
    audit_started = threading.Event()
    original = AutonomousRunner._completion_audit

    def slow_audit(self, goal, outcome):
        audit_started.set()
        time.sleep(0.3)
        return original(self, goal, outcome)

    monkeypatch.setattr(AutonomousRunner, "_completion_audit", slow_audit)
    backend = EmbeddedExecutionBackend(tmp_path)
    backend.start({"objective": "Stop before a delayed completion audit accepts"})
    assert audit_started.wait(timeout=3)

    backend.stop()
    stopped = _wait_for_terminal(backend)

    assert stopped["status"] == "failed"
    assert stopped["final_result"]["accepted"] is False
