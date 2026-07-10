from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from agentic_harness.core.local_goal_bridge import CommandResult, LocalGoalBridge
from agentic_harness.gui import server as gui_server_module
from agentic_harness.gui.api import modes_payload, start_task, task_from_command_result
from agentic_harness.gui.server import GuiPortUnavailable, create_gui_server, make_handler


GUI_TOKEN_ENV = "AGENTIC_HARNESS_GUI_TOKEN"


def test_gui_modes_use_human_labels() -> None:
    labels = [mode["label"] for mode in modes_payload()]

    assert labels == [
        "Use this computer",
        "Let GLM guide the plan",
        "Let GLM carry a long task",
        "Try experimental GLM",
    ]


def test_task_from_command_result_maps_review_state() -> None:
    result = CommandResult(
        args=("local-goal", "status", "--json"),
        returncode=0,
        stdout='{"active_goal": {"status": "review", "objective": "ship it"}}',
        stderr="",
    )

    task = task_from_command_result(result, fallback_status="working")

    assert task["status"] == "needs_review"
    assert task["needs_human"] is True
    assert task["summary"] == "ship it"
    assert task["progress"] == 70
    assert task["metadata"]["command"] == "local-goal status --json"


def test_task_summary_hides_backend_actors_but_preserves_raw_evidence() -> None:
    backend_summary = (
        "Worker stopped and says it is done. Hermes watcher will review it "
        "automatically before any new Node1 goal starts."
    )
    result = CommandResult(
        args=("local-goal", "status", "--json"),
        returncode=0,
        stdout=json.dumps(
            {
                "classification": "needs_review",
                "capabilities": {
                    "current_state": {"recommended_action": backend_summary},
                },
            }
        ),
        stderr="",
    )

    task = task_from_command_result(result, fallback_status="working")

    assert task["summary"] == (
        "The work is ready for review. Review it or ask it to continue before "
        "starting another task."
    )
    assert "hermes" not in task["summary"].lower()
    assert "node1" not in task["summary"].lower()
    assert task["advanced_details"]["payload"]["capabilities"]["current_state"][
        "recommended_action"
    ] == backend_summary


def test_task_summary_hides_internal_generated_objective() -> None:
    result = CommandResult(
        args=("local-goal", "status", "--json"),
        returncode=0,
        stdout=json.dumps(
            {
                "classification": "needs_review",
                "active_goal": {
                    "awaiting_review": True,
                    "objective": "Mode 3A: Cloud Long-Horizon Goal",
                },
            }
        ),
        stderr="",
    )

    task = task_from_command_result(result, fallback_status="working")

    assert task["summary"] == (
        "The work is ready for review. Review it or ask it to continue before "
        "starting another task."
    )
    assert task["advanced_details"]["payload"]["active_goal"]["objective"] == (
        "Mode 3A: Cloud Long-Horizon Goal"
    )


def test_task_from_command_result_does_not_treat_accepted_false_as_done() -> None:
    result = CommandResult(
        args=("local-goal", "status", "--json"),
        returncode=0,
        stdout=json.dumps(
            {
                "classification": "needs_review",
                "active_goal": {
                    "accepted": False,
                    "awaiting_review": True,
                    "objective": "review this",
                    "run_dir": "/tmp/run",
                },
            }
        ),
        stderr="",
    )

    task = task_from_command_result(result, fallback_status="working")

    assert task["status"] == "needs_review"
    assert task["readiness_gate"]["requires_review"] is True
    assert task["readiness_gate"]["can_start"] is False
    assert task["readiness_gate"]["active_run_dir"] == "/tmp/run"
    assert task["agent_loop"]["stage"] == "Review"


def test_task_from_command_result_maps_failed_command_to_blocked() -> None:
    result = CommandResult(
        args=("local-goal", "status"),
        returncode=1,
        stdout="",
        stderr="missing backend",
    )

    task = task_from_command_result(result, fallback_status="working")

    assert task["status"] == "blocked"
    assert task["summary"] == "missing backend"
    assert task["progress"] == 0


def test_start_task_uses_bridge_human_goal() -> None:
    calls: list[list[str]] = []

    def fake_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "queued_id=abc123\n", "")

    bridge = LocalGoalBridge(
        doc_root=Path("/tmp/docs"),
        local_goal=Path("/bin/sh"),
        runner=fake_runner,
    )

    task = start_task(
        bridge,
        {
            "mode": "cloud",
            "objective": "make Jarvis voice startup more reliable",
            "safe_areas": ["services/voice"],
            "checks": ["pytest tests/test_voice.py"],
        },
    )

    assert task["status"] == "starting"
    assert calls
    assert calls[0][1] == "status"
    assert calls[-1][1] == "enqueue"


def test_start_task_blocks_when_current_work_needs_review() -> None:
    bridge = ReviewBridge()

    task = start_task(bridge, {"mode": "cloud", "objective": "new task"})

    assert task["status"] == "needs_review"
    assert task["readiness_gate"]["requires_review"] is True
    assert bridge.commands == []


def test_gui_server_get_api_routes_return_json() -> None:
    with gui_server(FakeBridge()) as base_url:
        health = get_json(base_url, "/api/health")
        modes = get_json(base_url, "/api/modes")
        tasks = get_json(base_url, "/api/tasks")
        current = get_json(base_url, "/api/tasks/current")
        details = get_json(base_url, "/api/tasks/current/details")
        readiness = get_json(base_url, "/api/readiness")

    assert health["ok"] is True
    assert health["no_babysitting"]["enabled"] is True
    assert health["readiness"]["agent_loop"]["stage"] == "Act"
    assert readiness["agent_loop"]["stage"] == "Act"
    assert modes["modes"][0]["label"] == "Use this computer"
    assert tasks["tasks"][0]["status"] == "working"
    assert current["status"] == "working"
    assert details["task"]["status"] == "working"


def test_gui_server_unknown_api_route_returns_json_404() -> None:
    with gui_server(FakeBridge()) as base_url:
        try:
            get_json(base_url, "/api/not-real")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert exc.headers["Content-Type"].startswith("application/json")
            payload = json.loads(exc.read().decode("utf-8"))
        else:  # pragma: no cover - defensive guard
            raise AssertionError("unknown API route should return 404")

    assert payload == {"ok": False, "error": "not found"}


def test_gui_token_mode_keeps_static_shell_public_and_gates_api(monkeypatch) -> None:
    monkeypatch.setenv(GUI_TOKEN_ENV, "test-token")

    with gui_server(FakeBridge()) as base_url:
        index = get_text(base_url, "/")
        app = get_text(base_url, "/static/app.js")
        styles = get_text(base_url, "/static/styles.css")
        unauthorized = get_http_error(base_url, "/api/health")
        health = get_json(base_url, "/api/health", token="test-token")
        query_health = get_json(base_url, "/api/health?token=test-token")
        unknown_unauthorized = get_http_error(base_url, "/api/not-real")
        unknown_authenticated = get_http_error(base_url, "/api/not-real", token="test-token")

    assert "<!doctype html>" in index
    assert "function connectStatusStream" in app
    assert ":root" in styles
    assert "test-token" not in index + app + styles
    assert unauthorized.code == 401
    assert unauthorized.payload == {"ok": False, "error": "unauthorized"}
    assert health["ok"] is True
    assert query_health["ok"] is True
    assert unknown_unauthorized.code == 401
    assert unknown_unauthorized.payload == {"ok": False, "error": "unauthorized"}
    assert unknown_authenticated.code == 404
    assert unknown_authenticated.payload == {"ok": False, "error": "not found"}


def test_gui_token_mode_websocket_accepts_query_token(monkeypatch) -> None:
    monkeypatch.setenv(GUI_TOKEN_ENV, "test-token")

    with gui_server(FakeBridge()) as base_url:
        host, port = base_url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port)), timeout=3) as client:
            client.sendall(
                (
                    "GET /api/tasks/stream?token=test-token HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            response = client.recv(4096)
            response += client.recv(4096)

    assert b"101 Switching Protocols" in response
    assert b'"status": "working"' in response


def test_gui_frontend_plumbs_token_without_persisting_or_exporting_it() -> None:
    app = Path("agentic_harness/gui/static/app.js").read_text(encoding="utf-8")

    assert "new URLSearchParams(window.location.search)" in app
    assert 'const TOKEN_PARAM = "token";' in app
    assert "history.replaceState" in app
    assert "sessionStorage" in app
    assert "Authorization" in app
    assert "Bearer" in app
    assert "new Headers" in app
    assert "new WebSocket" in app
    assert "encodeURIComponent(token)" in app
    assert "status === 401" in app
    assert "showTokenDialog" in app
    assert "retry" in app
    assert "authPromptPromise" in app
    assert "if (state.authPromptPromise) return state.authPromptPromise" in app
    assert "clearAuthToken()" in app
    assert "response.status === 401 && retry" in app
    assert "localStorage.setItem(TOKEN" not in app
    assert "localStorage.getItem(TOKEN" not in app
    assert "token:" not in app


def test_gui_frontend_defaults_to_guided_mode() -> None:
    app = Path("agentic_harness/gui/static/app.js").read_text(encoding="utf-8")

    assert 'mode: "guided"' in app
    assert 'snapshot.mode || "guided"' in app


def test_gui_frontend_token_prompt_concurrent_race_regression() -> None:
    subprocess.run(["node", "tests/frontend_token_race_test.js"], check=True)


def test_gui_server_output_does_not_print_or_inject_configured_token(monkeypatch, capsys) -> None:
    monkeypatch.setenv(GUI_TOKEN_ENV, "test-token")
    events: list[str] = []

    class FakeServer:
        server_address = ("127.0.0.1", 43210)

        def serve_forever(self) -> None:
            events.append("served")
            raise KeyboardInterrupt

        def server_close(self) -> None:
            events.append("closed")

    def fake_create_gui_server(*args: object, **kwargs: object) -> FakeServer:
        return FakeServer()

    monkeypatch.setattr(gui_server_module, "create_gui_server", fake_create_gui_server)

    gui_server_module.serve_gui(
        host="127.0.0.1",
        port=0,
        doc_root=Path("/tmp/docs"),
        open_browser=False,
        allow_port_fallback=False,
    )

    output = capsys.readouterr().out
    assert "Agentic Harness GUI: http://127.0.0.1:43210/" in output
    assert "test-token" not in output
    assert events == ["served", "closed"]


def test_gui_server_falls_back_when_default_port_is_busy() -> None:
    busy = _busy_port_with_free_successor()
    with busy:
        busy_port = busy.getsockname()[1]

        server = create_gui_server(
            "127.0.0.1",
            busy_port,
            make_handler(FakeBridge()),  # type: ignore[arg-type]
            allow_port_fallback=True,
        )

    try:
        assert server.server_port == busy_port + 1
    finally:
        server.server_close()


def test_gui_server_rejects_busy_explicit_port() -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        busy_port = busy.getsockname()[1]

        try:
            create_gui_server(
                "127.0.0.1",
                busy_port,
                make_handler(FakeBridge()),  # type: ignore[arg-type]
                allow_port_fallback=False,
            )
        except GuiPortUnavailable as exc:
            message = str(exc)
        else:  # pragma: no cover - defensive guard
            raise AssertionError("busy explicit GUI port should fail")

    assert f"127.0.0.1:{busy_port}" in message
    assert "omit --port" in message


def test_run_server_uses_os_selected_port_when_not_explicit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_serve_gui(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(gui_server_module, "serve_gui", fake_serve_gui)

    result = gui_server_module.run_server_from_args(
        SimpleNamespace(
            host="127.0.0.1",
            port=None,
            doc_root="/tmp/docs",
            no_open=True,
        )
    )

    assert result == 0
    assert calls[0]["port"] == 0
    assert calls[0]["allow_port_fallback"] is False


def test_run_server_expands_explicit_doc_root(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    def fake_serve_gui(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(gui_server_module, "serve_gui", fake_serve_gui)

    result = gui_server_module.run_server_from_args(
        SimpleNamespace(
            host="127.0.0.1",
            port=8765,
            doc_root="~/docs",
            no_open=True,
        )
    )

    assert result == 0
    assert calls[0]["doc_root"] == home / "docs"


def test_serve_gui_browser_open_failure_does_not_stop_server(monkeypatch, capsys) -> None:
    events: list[str] = []

    class FakeServer:
        server_address = ("127.0.0.1", 43210)

        def serve_forever(self) -> None:
            events.append("served")
            raise KeyboardInterrupt

        def server_close(self) -> None:
            events.append("closed")

    def fake_create_gui_server(*args: object, **kwargs: object) -> FakeServer:
        return FakeServer()

    def fake_open(url: str) -> bool:
        events.append(f"open:{url}")
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(gui_server_module, "create_gui_server", fake_create_gui_server)
    monkeypatch.setattr(gui_server_module.webbrowser, "open", fake_open)

    gui_server_module.serve_gui(
        host="127.0.0.1",
        port=0,
        doc_root=Path("/tmp/docs"),
        open_browser=True,
        allow_port_fallback=False,
    )

    output = capsys.readouterr().out
    assert "Agentic Harness GUI: http://127.0.0.1:43210/" in output
    assert "Could not open a browser automatically" in output
    assert "agentic-harness gui --no-open" in output
    assert events == ["open:http://127.0.0.1:43210/", "served", "closed"]


def test_gui_server_post_task_workflow_routes() -> None:
    bridge = FakeBridge()
    with gui_server(bridge) as base_url:
        created = post_json(base_url, "/api/tasks", {"mode": "cloud", "objective": "test task"})
        watched = post_json(base_url, "/api/tasks/current/watch", {})
        continued = post_json(base_url, "/api/tasks/current/continue", {"feedback": "keep going"})
        accepted = post_json(base_url, "/api/tasks/current/accept", {})
        stopped = post_json(base_url, "/api/tasks/current/stop", {})

    assert created["status"] == "starting"
    assert watched["status"] == "working"
    assert continued["status"] == "working"
    assert accepted["status"] == "done"
    assert stopped["status"] == "stopped"
    assert bridge.commands == [
        ["enqueue", "--planner", "glm-5.2", "--executor", "opencode", "--executor-worker", "opencode-glm-build", "--goal", "GOAL_CONTENT"],
        ["monitor", "--auto-accept", "--auto-continue", "--auto-dispatch", "--auto-commit-owned", "--json"],
        ["continue", "--feedback", "keep going"],
        ["accept"],
        ["stop"],
    ]


def test_gui_server_keeps_task_history_and_searches() -> None:
    bridge = FakeBridge()
    with gui_server(bridge) as base_url:
        post_json(base_url, "/api/tasks", {"mode": "cloud", "objective": "alpha deploy"})
        post_json(base_url, "/api/tasks", {"mode": "cloud", "objective": "beta docs"})
        history = get_json(base_url, "/api/tasks/history")
        filtered = get_json(base_url, "/api/tasks/history?q=beta")

    assert len(history["tasks"]) == 2
    assert history["tasks"][0]["summary"] == "beta docs"
    assert [task["summary"] for task in filtered["tasks"]] == ["beta docs"]


def test_gui_server_bulk_tasks_returns_created_tasks() -> None:
    with gui_server(FakeBridge()) as base_url:
        payload = post_json(
            base_url,
            "/api/tasks/bulk",
            {
                "tasks": [
                    {"mode": "cloud", "objective": "first", "priority": "high"},
                    {"mode": "local", "objective": "second"},
                ]
            },
        )

    assert [task["status"] for task in payload["tasks"]] == ["starting", "starting"]
    assert payload["tasks"][0]["metadata"]["priority"] == "high"


def test_gui_server_session_export_import_round_trips_history() -> None:
    with gui_server(FakeBridge()) as base_url:
        post_json(base_url, "/api/tasks", {"mode": "cloud", "objective": "export me"})
        session = get_json(base_url, "/api/session")

    with gui_server(FakeBridge()) as base_url:
        imported = post_json(base_url, "/api/session/import", session)
        history = get_json(base_url, "/api/tasks/history")

    assert imported["ok"] is True
    assert history["tasks"][0]["summary"] == "export me"


def test_gui_server_websocket_status_upgrade_sends_json_frame() -> None:
    with gui_server(FakeBridge()) as base_url:
        host, port = base_url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port)), timeout=3) as client:
            client.sendall(
                (
                    "GET /api/tasks/stream HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            response = client.recv(4096)
            response += client.recv(4096)

    assert b"101 Switching Protocols" in response
    assert b'"status": "working"' in response



class FakeBridge:
    local_goal = Path("/tmp/local-goal")

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def available(self) -> bool:
        return True

    def start_human_goal(
        self,
        *,
        mode_key: str,
        objective: str,
        safe_areas: tuple[str, ...] = (),
        checks: tuple[str, ...] = (),
    ) -> CommandResult:
        result = LocalGoalBridge(
            doc_root=Path("/tmp/docs"),
            local_goal=Path("/bin/sh"),
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "queued\n", ""),
        ).start_human_goal(
            mode_key=mode_key,
            objective=objective,
            safe_areas=safe_areas,
            checks=checks,
        )
        command = list(result.args[1:])
        if command and command[-1].startswith("Mode 3A:"):
            command[-1] = "GOAL_CONTENT"
        self.commands.append(command)
        return CommandResult(result.args, 0, "queued\n", "")

    def status(self, *, json_output: bool = False) -> CommandResult:
        return CommandResult(("local-goal", "status"), 0, '{"active_goal": {"status": "running", "objective": "test task"}}', "")

    def monitor(self, *, json_output: bool = False) -> CommandResult:
        command = ["monitor", "--auto-accept", "--auto-continue", "--auto-dispatch", "--auto-commit-owned"]
        if json_output:
            command.append("--json")
        self.commands.append(command)
        return CommandResult(tuple(command), 0, '{"active_goal": {"status": "running", "objective": "test task"}}', "")

    def run(self, args: list[str]) -> CommandResult:
        self.commands.append(args)
        if args == ["accept"]:
            return CommandResult(tuple(args), 0, '{"classification": "accepted"}', "")
        if args == ["stop"]:
            return CommandResult(tuple(args), 0, '{"status": "stopped"}', "")
        return CommandResult(tuple(args), 0, '{"active_goal": {"status": "running", "objective": "test task"}}', "")



class ReviewBridge(FakeBridge):
    def status(self, *, json_output: bool = False) -> CommandResult:
        return CommandResult(
            ("local-goal", "status", "--json"),
            0,
            json.dumps(
                {
                    "classification": "needs_review",
                    "active_goal": {
                        "accepted": False,
                        "awaiting_review": True,
                        "objective": "review current work",
                    },
                }
            ),
            "",
        )


@contextmanager
def gui_server(bridge: FakeBridge) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bridge))  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get_json(base_url: str, path: str, *, token: str | None = None) -> dict[str, object]:
    request = urllib.request.Request(base_url + path)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=3) as response:
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(response.read().decode("utf-8"))


def get_text(base_url: str, path: str) -> str:
    with urllib.request.urlopen(base_url + path, timeout=3) as response:
        return response.read().decode("utf-8")


class HttpErrorResult:
    def __init__(self, code: int, payload: dict[str, object]) -> None:
        self.code = code
        self.payload = payload


def get_http_error(base_url: str, path: str, *, token: str | None = None) -> HttpErrorResult:
    request = urllib.request.Request(base_url + path)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return HttpErrorResult(exc.code, payload)
    raise AssertionError("request should have failed")


def post_json(base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(response.read().decode("utf-8"))



def _busy_port_with_free_successor() -> socket.socket:
    for _ in range(100):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        busy_port = busy.getsockname()[1]
        if busy_port >= 65535:
            busy.close()
            continue
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", busy_port + 1))
        except OSError:
            busy.close()
            probe.close()
            continue
        probe.close()
        return busy
    raise RuntimeError("could not reserve a busy port with a free successor")
