from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from agentic_harness.core.local_goal_bridge import (
    CommandResult,
    LocalGoalBridge,
    Mode3AGoalOptions,
)
from agentic_harness.gui import api as gui_api
from agentic_harness.gui import server as gui_server
from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.gui.server import GuiSession, _managed_session_path, make_handler


MANAGED_ROUTE_FIELDS = {
    "key",
    "technical_mode",
    "mode_number",
    "label",
    "summary",
    "best_for",
    "caution",
    "available",
    "enabled",
    "recommended",
    "maturity",
    "mutation",
    "data_location",
    "local_only",
    "network_scope",
    "planner",
    "executor",
    "worker",
    "verification",
    "labs",
    "experimental",
    "requires_scope",
    "hidden",
    "disabled_reason",
    "backend_route",
}


def _capabilities() -> str:
    return json.dumps(
        {
            "external_candidate_contracts": ["agentic_harness.external_candidate.v1"],
            "lanes": {
                "local": {"executor": "opencode"},
                "premium_planner_local_builder": {"planners": ["glm-5.2"]},
                "cloud_executor": {
                    "default_executor_worker": "opencode-glm-build",
                    "executor_workers": ["opencode-glm-build"],
                    "adapter_canary_workers": [],
                },
            },
        }
    )


def _profile_commands(calls: list[list[str]]) -> list[list[str]]:
    return [call[1:] for call in calls if len(call) > 1 and call[1].startswith("model-profile-")]


def _profile_status(
    profile: str,
    *,
    attached: bool = False,
    run_id: str = "",
) -> str:
    window: dict[str, object] = {"healthy": True, "attached": attached}
    if run_id:
        window["run_id"] = run_id
    return json.dumps(
        {
            "contract": "node1_model_profile.v1",
            "profile": profile,
            "health": 200,
            "window": window,
        }
    )


def test_qwen_primary_is_the_managed_default_and_never_opens_a_swap_window(tmp_path) -> None:
    calls: list[list[str]] = []

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(args[0])
        calls.append(command)
        stdout = _capabilities() if command[-2:] == ["capabilities", "--json"] else "started\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(mode_key="mode1", objective="Keep the primary lane active")

    assert result.returncode == 0
    assert any("quick-start" in command for command in calls)
    assert _profile_commands(calls) == []


def test_ornith_text_activates_before_start_and_attaches_to_the_started_run(tmp_path) -> None:
    calls: list[list[str]] = []
    active_profile = "qwen-primary"
    attached_run = ""

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active_profile, attached_run
        command = list(args[0])
        calls.append(command)
        if command[-2:] == ["capabilities", "--json"]:
            stdout = _capabilities()
        elif "model-profile-status" in command:
            stdout = _profile_status(
                active_profile,
                attached=bool(attached_run),
                run_id=attached_run,
            )
        elif "model-profile-activate" in command:
            active_profile = "ornith-text"
            stdout = _profile_status(active_profile)
        elif "quick-start" in command:
            stdout = "run_dir=/tmp/runs/ornith-success\nstarted local-node1-goal\n"
        elif "model-profile-attach" in command:
            attached_run = command[command.index("--run-dir") + 1]
            stdout = _profile_status(
                active_profile,
                attached=True,
                run_id=attached_run,
            )
        else:
            stdout = "{}"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(
        mode_key="mode1",
        objective="Run a fast text task",
        execution_profile="ornith-text",
    )

    assert result.returncode == 0
    assert _profile_commands(calls) == [
        ["model-profile-status", "--json"],
        ["model-profile-activate", "--profile", "ornith-text", "--json"],
        ["model-profile-status", "--json"],
        ["model-profile-attach", "--run-dir", "/tmp/runs/ornith-success", "--json"],
        ["model-profile-status", "--json"],
    ]
    activation_index = next(
        index for index, command in enumerate(calls) if "model-profile-activate" in command
    )
    start_index = next(index for index, command in enumerate(calls) if "quick-start" in command)
    attachment_index = next(
        index for index, command in enumerate(calls) if "model-profile-attach" in command
    )
    assert activation_index < start_index < attachment_index


def test_managed_starts_serialize_profile_selection_through_goal_start(tmp_path) -> None:
    calls: list[list[str]] = []
    active_profile = "qwen-primary"
    attached_run = ""
    started_profiles: list[str] = []
    qwen_status_started = threading.Event()
    ornith_call_attempted = threading.Event()

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active_profile, attached_run
        command = list(args[0])
        calls.append(command)
        if "model-profile-status" in command:
            if threading.current_thread().name == "qwen-start" and not qwen_status_started.is_set():
                qwen_status_started.set()
                assert ornith_call_attempted.wait(timeout=2)
            return subprocess.CompletedProcess(
                command,
                0,
                _profile_status(
                    active_profile,
                    attached=bool(attached_run),
                    run_id=attached_run,
                ),
                "",
            )
        if "model-profile-activate" in command:
            active_profile = "ornith-text"
            attached_run = ""
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if command[-2:] == ["capabilities", "--json"]:
            return subprocess.CompletedProcess(command, 0, _capabilities(), "")
        if "quick-start" in command:
            started_profiles.append(active_profile)
            run_dir = f"/tmp/runs/{active_profile}"
            return subprocess.CompletedProcess(command, 0, f"run_dir={run_dir}\nstarted\n", "")
        if "model-profile-attach" in command:
            attached_run = command[command.index("--run-dir") + 1]
            return subprocess.CompletedProcess(
                command,
                0,
                _profile_status(active_profile, attached=True, run_id=attached_run),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )
    results: list[CommandResult] = []

    def start_qwen() -> None:
        results.append(
            bridge.start_human_goal(
                mode_key="mode1",
                objective="Qwen task",
                execution_profile="qwen-primary",
            )
        )

    def start_ornith() -> None:
        ornith_call_attempted.set()
        results.append(
            bridge.start_human_goal(
                mode_key="mode1",
                objective="Ornith task",
                execution_profile="ornith-text",
            )
        )

    qwen_thread = threading.Thread(target=start_qwen, name="qwen-start")
    ornith_thread = threading.Thread(target=start_ornith, name="ornith-start")
    qwen_thread.start()
    assert qwen_status_started.wait(timeout=2)
    ornith_thread.start()
    qwen_thread.join(timeout=5)
    ornith_thread.join(timeout=5)

    assert not qwen_thread.is_alive()
    assert not ornith_thread.is_alive()
    assert [result.returncode for result in results] == [0, 0]
    assert started_profiles == ["qwen-primary", "ornith-text"]


def test_ornith_attachment_retries_when_status_names_a_different_run(tmp_path) -> None:
    active_profile = "qwen-primary"
    attached_run = ""
    attach_attempts = 0
    expected_run = "/tmp/runs/ornith-identity"

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active_profile, attached_run, attach_attempts
        command = list(args[0])
        if "model-profile-status" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                _profile_status(
                    active_profile,
                    attached=bool(attached_run),
                    run_id=attached_run,
                ),
                "",
            )
        if "model-profile-activate" in command:
            active_profile = "ornith-text"
            attached_run = ""
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if command[-2:] == ["capabilities", "--json"]:
            return subprocess.CompletedProcess(command, 0, _capabilities(), "")
        if "quick-start" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                f"run_dir={expected_run}\nstarted\n",
                "",
            )
        if "model-profile-attach" in command:
            attach_attempts += 1
            attached_run = "/tmp/runs/different" if attach_attempts == 1 else expected_run
            return subprocess.CompletedProcess(
                command,
                0,
                _profile_status(active_profile, attached=True, run_id=attached_run),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(
        mode_key="mode1",
        objective="Bind the lease to the started run",
        execution_profile="ornith-text",
    )

    assert result.returncode == 0
    assert attach_attempts == 2
    assert result.metadata["profile_attachment"] == "recovered_after_retry"
    assert result.metadata["run_dir"] == expected_run


def test_ornith_text_restores_qwen_when_goal_start_fails(tmp_path) -> None:
    calls: list[list[str]] = []
    active_profile = "qwen-primary"

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active_profile
        command = list(args[0])
        calls.append(command)
        if command[-2:] == ["capabilities", "--json"]:
            return subprocess.CompletedProcess(command, 0, _capabilities(), "")
        if "model-profile-status" in command:
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if "model-profile-activate" in command:
            active_profile = "ornith-text"
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if "model-profile-restore" in command:
            active_profile = "qwen-primary"
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if "quick-start" in command:
            return subprocess.CompletedProcess(command, 17, "", "worker start failed")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(
        mode_key="mode1",
        objective="Fail after swapping models",
        execution_profile="ornith-text",
    )

    assert result.returncode == 17
    assert _profile_commands(calls) == [
        ["model-profile-status", "--json"],
        ["model-profile-activate", "--profile", "ornith-text", "--json"],
        ["model-profile-status", "--json"],
        ["model-profile-restore", "--json"],
    ]


def test_failed_profile_restore_requires_human_review_even_for_transient_exit_code() -> None:
    task = gui_api.task_from_command_result(
        CommandResult(
            ("local-goal", "model-profile-restore", "--json"),
            19,
            "",
            "restore failed",
            {
                "profile_recovery": "failed",
                "summary": "Ornith start failed and Qwen restoration also failed.",
            },
        ),
        fallback_status="starting",
    )

    assert task["status"] == "needs_review"
    assert task["needs_human"] is True
    assert task["readiness_gate"]["can_queue"] is False
    assert task["advanced_details"]["profile_state_unknown"] is True
    assert "restoration also failed" in task["summary"]


def test_ornith_attachment_failure_does_not_stop_a_possibly_running_goal(tmp_path) -> None:
    calls: list[list[str]] = []
    active_profile = "qwen-primary"

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal active_profile
        command = list(args[0])
        calls.append(command)
        if command[-2:] == ["capabilities", "--json"]:
            return subprocess.CompletedProcess(command, 0, _capabilities(), "")
        if "model-profile-status" in command:
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if "model-profile-activate" in command:
            active_profile = "ornith-text"
            return subprocess.CompletedProcess(command, 0, _profile_status(active_profile), "")
        if "quick-start" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "run_dir=/tmp/runs/unattached-ornith\nstarted local-node1-goal\n",
                "",
            )
        if "model-profile-attach" in command:
            return subprocess.CompletedProcess(command, 19, "", "attachment failed")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(
        mode_key="mode1",
        objective="Fail while attaching the model lease",
        execution_profile="ornith-text",
    )

    assert result.returncode == 0
    assert result.metadata["profile_attachment"] == "reconciliation_required"
    assert _profile_commands(calls) == [
        ["model-profile-status", "--json"],
        ["model-profile-activate", "--profile", "ornith-text", "--json"],
        ["model-profile-status", "--json"],
        [
            "model-profile-attach",
            "--run-dir",
            "/tmp/runs/unattached-ornith",
            "--json",
        ],
        ["model-profile-status", "--json"],
        [
            "model-profile-attach",
            "--run-dir",
            "/tmp/runs/unattached-ornith",
            "--json",
        ],
        ["model-profile-status", "--json"],
    ]
    assert not any("model-profile-restore" in command for command in calls)


def test_ornith_activation_failure_stops_before_goal_dispatch(tmp_path) -> None:
    calls: list[list[str]] = []

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        command = list(args[0])
        calls.append(command)
        if "model-profile-status" in command:
            return subprocess.CompletedProcess(command, 0, _profile_status("qwen-primary"), "")
        if "model-profile-activate" in command:
            return subprocess.CompletedProcess(command, 23, "", "activation failed")
        if "model-profile-restore" in command:
            return subprocess.CompletedProcess(command, 0, _profile_status("qwen-primary"), "")
        raise AssertionError(f"unexpected command after failed activation: {command}")

    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "local-goal",
        runner=runner,
    )

    result = bridge.start_human_goal(
        mode_key="mode1",
        objective="Do not dispatch without the requested model",
        execution_profile="ornith-text",
    )

    assert result.returncode == 23
    assert _profile_commands(calls) == [
        ["model-profile-status", "--json"],
        ["model-profile-activate", "--profile", "ornith-text", "--json"],
        ["model-profile-restore", "--json"],
    ]


def test_managed_mode_contract_exposes_backend_facts_not_effort_aliases() -> None:
    routes = gui_api.modes_payload()

    assert [route["key"] for route in routes] == [
        "mode1",
        "mode2",
        "mode3a",
        "mode4",
        "mode4b",
    ]
    assert all(MANAGED_ROUTE_FIELDS <= route.keys() for route in routes)
    by_key = {route["key"]: route for route in routes}
    assert by_key["mode1"]["technical_mode"] == "Mode 1 local start"
    assert by_key["mode1"]["recommended"] is True
    assert by_key["mode1"]["executor"] == "opencode"
    assert by_key["mode1"]["local_only"] is True
    assert by_key["mode2"]["enabled"] is False
    assert by_key["mode2"]["disabled_reason"]
    assert by_key["mode3a"]["technical_mode"] == "Mode 3A"
    assert by_key["mode3a"]["planner"] == "glm-5.2"
    assert by_key["mode3a"]["worker"] == "opencode-glm-build"
    assert by_key["mode3a"]["requires_scope"] is True
    assert by_key["mode4"]["mutation"] == "audit_only"
    assert by_key["mode4b"]["labs"] is True
    assert by_key["mode4b"]["enabled"] is False
    assert by_key["mode4b"]["disabled_reason"]


def test_retired_or_blocked_mode3a_registry_fails_closed_before_enqueue(tmp_path) -> None:
    registrations = (
        {"readiness": "retired", "blockers": []},
        {
            "readiness": "installed_capability_bounded",
            "blockers": ["operator_hold"],
        },
    )
    for index, registration in enumerate(registrations):
        root = tmp_path / str(index)
        root.mkdir()
        executable = root / "local-goal"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        calls: list[list[str]] = []

        def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            command = list(args[0])
            calls.append(command)
            if command[-2:] == ["harness-modes", "--json"]:
                stdout = json.dumps(
                    {
                        "contract": "local_goal_harness_modes.v1",
                        "status": "available",
                        "modes": [
                            {
                                "id": "mode3a_cloud_long_horizon_goal",
                                **registration,
                            }
                        ],
                    }
                )
            elif command[-2:] == ["capabilities", "--json"]:
                stdout = json.dumps(
                    {
                        "external_candidate_contracts": ["agentic_harness.external_candidate.v1"],
                        "lanes": {
                            "cloud_executor": {
                                "installed": True,
                                "available_now": True,
                            }
                        },
                    }
                )
            elif command[-2:] == ["adapter-matrix", "--json"]:
                stdout = json.dumps(
                    {
                        "matrix": [
                            {
                                "worker": "opencode-glm-build",
                                "enabled": True,
                                "binary_resolved": True,
                                "readiness": "use_now",
                                "mutation_default": "implementation",
                                "blockers": [],
                            }
                        ]
                    }
                )
            else:
                stdout = "queued_id=must-not-dispatch\n"
            return subprocess.CompletedProcess(command, 0, stdout, "")

        bridge = LocalGoalBridge(
            doc_root=root,
            local_goal=executable,
            runner=runner,
        )

        route = next(row for row in gui_api.modes_payload(bridge) if row["key"] == "mode3a")
        result = bridge.enqueue_mode3a(Mode3AGoalOptions("Do not dispatch retired work"))

        assert route["available"] is False
        assert route["disabled_reason"]
        assert result.returncode == 2
        assert "dispatchable" in result.stderr
        assert not any("enqueue" in command for command in calls)


def test_turnstone_terminal_done_is_ready_but_unknown_monitor_state_fails_closed(
    tmp_path,
) -> None:
    for index, (classification, expected_available) in enumerate(
        (("done", True), ("unknown", False))
    ):
        root = tmp_path / f"turnstone-{index}"
        root.mkdir()
        executable = root / "local-goal-turnstone"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        calls: list[list[str]] = []

        def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            command = list(args[0])
            calls.append(command)
            if command[-2:] == ["harness-modes", "--json"]:
                stdout = json.dumps(
                    {
                        "contract": "local_goal_harness_modes.v1",
                        "status": "available",
                        "modes": [
                            {
                                "id": "mode3a_cloud_long_horizon_goal",
                                "readiness": "installed_capability_bounded",
                                "blockers": [],
                            }
                        ],
                    }
                )
            elif command[-2:] == ["capabilities", "--json"]:
                stdout = json.dumps(
                    {
                        "external_candidate_contracts": ["agentic_harness.external_candidate.v1"],
                        "lanes": {
                            "cloud_executor": {
                                "installed": True,
                                "available_now": True,
                            }
                        },
                    }
                )
            elif command[-2:] == ["adapter-matrix", "--json"]:
                stdout = json.dumps({"matrix": []})
            elif command[-2:] == ["turnstone-monitor", "--json"]:
                stdout = json.dumps(
                    {
                        "contract": "agentic_harness_turnstone_mode3.v1",
                        "active": False,
                        "classification": classification,
                        "status": classification,
                    }
                )
            else:
                stdout = "queued_id=turnstone-terminal\n"
            return subprocess.CompletedProcess(command, 0, stdout, "")

        bridge = LocalGoalBridge(
            doc_root=root,
            local_goal=executable,
            runner=runner,
        )

        route = next(row for row in gui_api.modes_payload(bridge) if row["key"] == "mode3a")
        result = bridge.enqueue_mode3a(Mode3AGoalOptions("Start after terminal state"))

        assert route["available"] is expected_available
        assert (result.returncode == 0) is expected_available
        assert any("enqueue" in command for command in calls) is expected_available


@contextmanager
def _api_server(service: object) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get_json(base_url: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(base_url + path, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def test_managed_modes_endpoint_separates_routes_effort_and_model_profile(tmp_path) -> None:
    bridge = LocalGoalBridge(
        doc_root=tmp_path,
        local_goal=tmp_path / "missing-local-goal",
    )

    with _api_server(bridge) as base_url:
        payload = _get_json(base_url, "/api/modes")

    assert payload["kind"] == "managed_route"
    assert payload["default_route"] == "mode1"
    assert payload["default_effort"] == "standard"
    assert payload["default_execution_profile"] == "automatic"
    assert [route["key"] for route in payload["routes"]] == [
        "mode1",
        "mode2",
        "mode3a",
        "mode4",
        "mode4b",
    ]
    assert [effort["key"] for effort in payload["efforts"]] == [
        "quick",
        "standard",
        "thorough",
    ]
    assert payload["execution_profiles"] == []
    assert isinstance(payload["modes"], list)


def test_embedded_modes_endpoint_keeps_strategies_as_effort_policies(tmp_path) -> None:
    backend = EmbeddedExecutionBackend(tmp_path)

    with _api_server(backend) as base_url:
        payload = _get_json(base_url, "/api/modes")

    assert payload["kind"] == "strategy"
    assert payload["default"] == "plan"
    assert [strategy["key"] for strategy in payload["modes"]] == [
        "quick",
        "plan",
        "persistent",
        "experiment",
    ]
    assert all(strategy["budget_profile"] for strategy in payload["modes"])
    assert all("technical_mode" not in strategy for strategy in payload["modes"])
    assert all("backend_route" not in strategy for strategy in payload["modes"])


class ReadyCaptureBridge:
    local_goal = Path("/tmp/local-goal")
    doc_root = Path("/tmp/docs")

    def __init__(self) -> None:
        self.starts: list[dict[str, object]] = []

    def available(self) -> bool:
        return True

    def background_supervision(self) -> dict[str, object]:
        return {"active": True, "state": "active", "timer_active": True}

    def status(self, *, json_output: bool = False) -> CommandResult:
        return CommandResult(
            ("local-goal", "status", "--json"),
            0,
            json.dumps(
                {
                    "classification": "idle",
                    "active_goal": None,
                    "capabilities": {
                        "current_state": {
                            "classification": "idle",
                            "local_goal_lane_free": True,
                        }
                    },
                }
            ),
            "",
        )

    def model_profile_status(self) -> CommandResult:
        return CommandResult(
            ("local-goal", "model-profile-status", "--json"),
            0,
            _profile_status("qwen-primary"),
            "",
        )

    def start_human_goal(self, **kwargs: object) -> CommandResult:
        self.starts.append(dict(kwargs))
        return CommandResult(
            ("local-goal", "quick-start"),
            0,
            "run_dir=/tmp/runs/managed-route\nstarted local-node1-goal\n",
            "",
        )


def test_managed_modes_endpoint_exposes_only_proven_local_profiles() -> None:
    with _api_server(ReadyCaptureBridge()) as base_url:
        payload = _get_json(base_url, "/api/modes")

    assert payload["default_execution_profile"] == "qwen-primary"
    assert [profile["key"] for profile in payload["execution_profiles"]] == [
        "qwen-primary",
        "ornith-text",
    ]
    assert all(profile["route_key"] == "mode1" for profile in payload["execution_profiles"])
    assert payload["execution_profiles"][0]["vision"] is True
    assert payload["execution_profiles"][1]["vision"] is False


def test_standard_effort_defaults_to_mode1_instead_of_silently_selecting_mode2() -> None:
    bridge = ReadyCaptureBridge()

    task = gui_api.start_task(
        bridge,  # type: ignore[arg-type]
        {
            "objective": "Use the normal managed route with a balanced effort budget",
            "effort": "standard",
        },
    )

    assert task["status"] == "starting"
    assert len(bridge.starts) == 1
    assert bridge.starts[0]["mode_key"] == "mode1"
    assert bridge.starts[0].get("mode_key") != "mode2"


def test_managed_api_defaults_execution_profile_to_qwen_primary() -> None:
    bridge = ReadyCaptureBridge()

    task = gui_api.start_task(
        bridge,  # type: ignore[arg-type]
        {"route": "mode1", "effort": "quick", "objective": "Use the primary model"},
    )

    assert task["status"] == "starting"
    assert bridge.starts[0]["execution_profile"] == "qwen-primary"


def test_mode3a_requires_explicit_managed_scope_before_dispatch() -> None:
    bridge = ReadyCaptureBridge()

    task = gui_api.start_task(
        bridge,  # type: ignore[arg-type]
        {
            "route": "mode3a",
            "effort": "thorough",
            "objective": "Run bounded cloud work",
        },
    )

    assert task["status"] == "blocked"
    assert task["needs_human"] is True
    assert "scope" in task["summary"].lower() or "allowed" in task["summary"].lower()
    assert bridge.starts == []


def test_disabled_mode4b_cannot_be_started_even_with_explicit_scope() -> None:
    bridge = ReadyCaptureBridge()

    task = gui_api.start_task(
        bridge,  # type: ignore[arg-type]
        {
            "route": "mode4b",
            "effort": "quick",
            "objective": "Attempt a one-file direct implementation canary",
            "safe_areas": ["reports/canary.md"],
        },
    )

    assert task["status"] == "blocked"
    assert task["needs_human"] is True
    assert "disabled" in task["summary"].lower() or "not available" in task["summary"].lower()
    assert bridge.starts == []


def test_legacy_experimental_alias_cannot_bypass_disabled_canary_route() -> None:
    bridge = ReadyCaptureBridge()

    task = gui_api.start_task(
        bridge,  # type: ignore[arg-type]
        {
            "mode": "experimental",
            "objective": "Attempt the retired compatibility route",
            "safe_areas": ["reports/canary.md"],
        },
    )

    assert task["status"] == "blocked"
    assert task["advanced_details"]["error"] == "legacy_experimental_route_retired"
    assert bridge.starts == []


def test_managed_selected_route_effort_and_profile_survive_status_refresh() -> None:
    session = GuiSession()
    source = {
        "objective": "Continue this managed task",
        "route": "mode3a",
        "effort": "thorough",
        "execution_profile": "ornith-text",
        "safe_areas": ["services/voice"],
    }
    started = session.enrich(
        {
            "id": "managed-run-1",
            "status": "starting",
            "summary": "starting",
            "metadata": {},
        },
        source,
    )
    session.record(started)

    refreshed = session.record(
        {
            "id": "managed-run-1",
            "status": "working",
            "objective": "Continue this managed task",
            "summary": "working",
            "metadata": {"updated_at": "2026-07-16T12:00:00Z"},
        }
    )

    assert refreshed["metadata"]["route_key"] == "mode3a"
    assert refreshed["metadata"]["effort"] == "thorough"
    assert refreshed["metadata"]["execution_profile"] == "ornith-text"
    assert refreshed["metadata"]["safe_areas"] == ["services/voice"]
    assert session.history()[0]["metadata"]["route_key"] == "mode3a"


def test_managed_execution_metadata_survives_gui_restart_without_leaking_to_another_run(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "gui-session.json"
    source = {
        "objective": "Keep this exact managed route after a service restart",
        "route": "mode1",
        "effort": "thorough",
        "execution_profile": "ornith-text",
        "safe_areas": ["services/voice"],
        "checks": ["pytest -q"],
    }
    first = GuiSession(state_path)
    started = first.enrich(
        {
            "id": "",
            "status": "starting",
            "summary": "starting",
            "metadata": {},
            "advanced_details": {"stdout": "run_dir=/tmp/runs/durable-run-1\nstarted\n"},
        },
        source,
    )
    first.record(started)

    restarted = GuiSession(state_path)
    refreshed = restarted.record(
        {
            "id": "durable-run-1",
            "status": "working",
            "summary": "working",
            "metadata": {},
            "advanced_details": {
                "payload": {
                    "active_goal": {
                        "id": "durable-run-1",
                        "run_dir": "/tmp/runs/durable-run-1",
                    }
                }
            },
        }
    )

    assert refreshed["objective"] == source["objective"]
    assert refreshed["metadata"]["route_key"] == "mode1"
    assert refreshed["metadata"]["effort"] == "thorough"
    assert refreshed["metadata"]["execution_profile"] == "ornith-text"
    if os.name == "posix":
        assert state_path.stat().st_mode & 0o777 == 0o600

    unrelated = restarted.record(
        {
            "id": "durable-run-1",
            "status": "working",
            "objective": "A different task",
            "summary": "working",
            "metadata": {},
            "advanced_details": {
                "payload": {
                    "active_goal": {
                        "id": "durable-run-1",
                        "run_dir": "/tmp/other-workspace/durable-run-1",
                    }
                }
            },
        }
    )

    assert "route_key" not in unrelated["metadata"]
    assert unrelated["objective"] == "A different task"


def test_started_goal_needing_profile_reconciliation_keeps_its_labels_after_restart(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "gui-session.json"
    source = {
        "objective": "Run this goal on the selected temporary model",
        "route": "mode1",
        "effort": "thorough",
        "execution_profile": "ornith-text",
    }
    session = GuiSession(state_path)
    started = session.enrich(
        {
            "id": "",
            "status": "needs_review",
            "summary": "The goal started, but its model lease needs reconciliation.",
            "metadata": {},
            "advanced_details": {
                "returncode": 0,
                "command_metadata": {
                    "profile_attachment": "reconciliation_required",
                    "run_dir": "/tmp/runs/ornith-reconcile",
                },
            },
        },
        source,
    )

    assert started["metadata"]["start_accepted"] is True
    session.record(started)
    restarted = GuiSession(state_path)
    refreshed = restarted.record(
        {
            "id": "ornith-reconcile",
            "status": "working",
            "summary": "working",
            "metadata": {},
            "advanced_details": {
                "payload": {
                    "active_goal": {
                        "id": "ornith-reconcile",
                        "run_dir": "/tmp/runs/ornith-reconcile",
                    }
                }
            },
        }
    )

    assert refreshed["objective"] == source["objective"]
    assert refreshed["metadata"]["route_key"] == "mode1"
    assert refreshed["metadata"]["effort"] == "thorough"
    assert refreshed["metadata"]["execution_profile"] == "ornith-text"


def test_transient_failed_start_cannot_claim_an_observed_run() -> None:
    session = GuiSession()
    failed = session.enrich(
        {
            "id": "already-running",
            "status": "checking",
            "summary": "The start request failed transiently.",
            "metadata": {},
            "advanced_details": {"returncode": 17, "transient_error": True},
        },
        {
            "objective": "A new request that was not accepted",
            "route": "mode1",
            "effort": "standard",
        },
    )
    assert failed["metadata"]["start_accepted"] is False
    session.record(failed)

    observed = session.record(
        {
            "id": "already-running",
            "status": "working",
            "objective": "The pre-existing task",
            "summary": "working",
            "metadata": {},
        }
    )

    assert observed["objective"] == "The pre-existing task"
    assert "route_key" not in observed["metadata"]


def test_managed_session_uses_project_state_root_not_wrapper_doc_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    wrapper_docs = tmp_path / "wrapper-docs"
    monkeypatch.delenv("AGENTIC_HARNESS_GUI_SESSION_PATH", raising=False)

    state_path = _managed_session_path(project)

    assert state_path == project.resolve() / ".agentic-harness" / "gui-session.v1.json"
    assert wrapper_docs not in state_path.parents


def test_identityless_start_is_not_rebound_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)
    started = session.enrich(
        {"status": "starting", "summary": "starting", "metadata": {}},
        {
            "objective": "Identityless task",
            "route": "mode1",
            "effort": "standard",
        },
    )
    assert "durable run id" in started["metadata"]["persistence_warning"]
    session.record(started)
    assert session.persistence_status()["status"] == "degraded"
    assert "durable run id" in session.persistence_status()["warning"]

    restarted = GuiSession(state_path)
    unrelated = restarted.record(
        {
            "id": "different-run",
            "status": "working",
            "objective": "External task",
            "summary": "working",
            "metadata": {},
        }
    )

    assert unrelated["objective"] == "External task"
    assert "route_key" not in unrelated["metadata"]


def test_persisted_managed_session_omits_raw_details_and_redacts_public_secrets(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)
    secret = "sk-secretvalue123456789"
    task = session.enrich(
        {
            "id": "secret-run",
            "status": "working",
            "summary": f"Working with {secret}",
            "metadata": {},
            "advanced_details": {
                "stdout": f"Authorization: Bearer {secret}",
                "stderr": f"api_key={secret}",
            },
        },
        {
            "objective": f"Handle a credential-shaped value {secret}",
            "route": "mode1",
            "effort": "standard",
        },
    )
    session.record(task)

    persisted = state_path.read_text(encoding="utf-8")

    assert secret not in persisted
    assert "advanced_details" not in persisted
    assert "stdout" not in persisted
    assert "stderr" not in persisted
    assert "<redacted>" in persisted


def test_persisted_managed_session_redacts_values_under_sensitive_nested_keys(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)
    opaque_secret = "opaque-credential-with-no-provider-prefix"
    session.record(
        {
            "id": "nested-secret-run",
            "status": "done",
            "summary": "Complete",
            "metadata": {},
            "events": [
                {
                    "context": {
                        "token": opaque_secret,
                        "client_secret_value": opaque_secret,
                        "monkey": "safe value",
                    }
                }
            ],
        }
    )

    persisted = state_path.read_text(encoding="utf-8")

    assert opaque_secret not in persisted
    assert persisted.count("<redacted>") >= 2
    assert '"monkey":"safe value"' in persisted


def test_existing_session_permissions_are_hardened_before_load(tmp_path: Path) -> None:
    state_path = tmp_path / "gui-session.json"
    state_path.write_text(
        json.dumps(
            {
                "contract": "agentic_harness.gui_session.v1",
                "active_identity": None,
                "records": [],
            }
        ),
        encoding="utf-8",
    )
    os.chmod(state_path, 0o644)

    session = GuiSession(state_path)

    if os.name == "posix":
        assert state_path.stat().st_mode & 0o777 == 0o600
    assert session.persistence_status()["status"] == "ready"


def test_portable_state_io_fallback_round_trips_without_dir_fd_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gui_server, "_directory_fd_state_io_supported", lambda: False)
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)
    session.record(
        {
            "id": "portable-run",
            "status": "done",
            "summary": "portable state",
            "metadata": {},
        }
    )

    restarted = GuiSession(state_path)

    assert restarted.persistence_status()["status"] == "ready"
    assert restarted.history()[0]["id"] == "portable-run"
    assert restarted.history()[0]["summary"] == "portable state"


def test_portable_state_io_fallback_still_rejects_state_symlinks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if os.name != "posix":
        return
    monkeypatch.setattr(gui_server, "_directory_fd_state_io_supported", lambda: False)
    target = tmp_path / "sentinel.json"
    target.write_text("sentinel", encoding="utf-8")
    state_path = tmp_path / "gui-session.json"
    state_path.symlink_to(target)

    session = GuiSession(state_path)
    session.record(
        {
            "id": "portable-run",
            "status": "working",
            "summary": "working",
            "metadata": {},
        }
    )

    assert target.read_text(encoding="utf-8") == "sentinel"
    assert session.persistence_status()["status"] == "degraded"


def test_oversized_single_snapshot_fails_closed_instead_of_writing_unloadable_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(gui_server, "MAX_GUI_SESSION_BYTES", 512)
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)

    session.record(
        {
            "id": "large-run",
            "status": "working",
            "summary": "x" * 2_000,
            "metadata": {},
        }
    )

    assert not state_path.exists()
    assert session.persistence_status()["status"] == "degraded"
    assert "size limit" in session.persistence_status()["warning"]


def test_same_summary_distinct_runs_remain_in_durable_history(tmp_path: Path) -> None:
    state_path = tmp_path / "gui-session.json"
    session = GuiSession(state_path)
    for run_id in ("run-a", "run-b"):
        session.record(
            {
                "id": run_id,
                "status": "done",
                "objective": "Same objective",
                "summary": "Same summary",
                "metadata": {},
            }
        )

    restarted = GuiSession(state_path)

    assert [task["id"] for task in restarted.history()] == ["run-b", "run-a"]


def test_session_symlink_and_write_failures_are_nonfatal(tmp_path: Path) -> None:
    target = tmp_path / "sentinel.json"
    target.write_text("sentinel", encoding="utf-8")
    linked_state = tmp_path / "linked-session.json"
    linked_state.symlink_to(target)
    linked = GuiSession(linked_state)
    returned = linked.record(
        {"id": "run-a", "status": "working", "summary": "working", "metadata": {}}
    )

    assert returned["status"] == "working"
    assert target.read_text(encoding="utf-8") == "sentinel"
    assert linked.persistence_status()["status"] == "degraded"

    impossible_parent = tmp_path / "not-a-directory"
    impossible_parent.write_text("file", encoding="utf-8")
    unwritable = GuiSession(impossible_parent / "session.json")
    returned = unwritable.record(
        {"id": "run-b", "status": "starting", "summary": "starting", "metadata": {}}
    )

    assert returned["status"] == "starting"
    assert unwritable.persistence_status()["status"] == "degraded"


def test_nonregular_session_state_is_rejected_without_blocking_startup(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        return
    state_path = tmp_path / "gui-session.pipe"
    os.mkfifo(state_path)

    session = GuiSession(state_path)

    assert session.history() == []
    assert session.persistence_status()["status"] == "degraded"
    assert "not a regular file" in session.persistence_status()["warning"]
