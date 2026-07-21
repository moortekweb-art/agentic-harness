"""Human-facing API helpers for the local GUI."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, cast

from agentic_harness.core.local_goal_bridge import (
    CommandResult,
    EXTERNAL_CANDIDATE_CONTRACT,
    LocalGoalBridge,
    MODE3A_PLANNER,
    MODE3A_WORKER,
    MODE4_WORKER,
    MODE4B_WORKER,
    managed_mode_record_dispatchable,
    managed_route_key,
)


TaskPayload = dict[str, Any]

_TECHNICAL_SUMMARY_TERMS = (
    "hermes",
    "local goal",
    "node1",
    "opencode",
    "vllm",
    "mode 3a",
    "executor-worker",
    "local-goal",
    "run_dir",
)
_PERMANENT_COMMAND_FAILURES = frozenset({2, 126, 127})
_MAX_START_TICKET_BYTES = 128 * 1024


_MANAGED_ROUTE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "mode1",
        "technical_mode": "Mode 1 local start",
        "backend_id": "opencode_executes_glm52_supervises",
        "mode_number": "1",
        "label": "Local build",
        "summary": "Run the implementation through OpenCode on the local Node1 model lane.",
        "best_for": "Normal implementation work when the managed local lane is free.",
        "caution": (
            "The start route is local quick-start. GLM supervision is advisory and is not "
            "automatically started or proven by this GUI route."
        ),
        "recommended": True,
        "maturity": "local_start_compatibility",
        "mutation": "implementation",
        "data_location": "local_node1",
        "local_only": True,
        "network_scope": "local_execution",
        "planner": "none at start",
        "executor": "opencode",
        "worker": "local Node1 model lane",
        "verification": "deterministic review and acceptance gates",
        "labs": False,
        "experimental": False,
        "requires_scope": False,
        "hidden": False,
        "backend_route": "quick-start",
        "supports_execution_profiles": True,
        "uses_local_node1": True,
        "leaves_local_lane_free": False,
    },
    {
        "key": "mode2",
        "technical_mode": "Mode 2",
        "backend_id": "opencode_executes_glm52_supervises_codex_spotchecks",
        "mode_number": "2",
        "label": "Local build + Codex review",
        "summary": "Use GLM for routine supervision and reserve Codex for important spot-checks.",
        "best_for": "Expensive long-running local work where Codex usage should be conserved.",
        "caution": "This is a supervision policy for an existing goal, not an independent start command.",
        "recommended": False,
        "maturity": "policy_only",
        "mutation": "implementation",
        "data_location": "mixed_local_cloud",
        "local_only": False,
        "network_scope": "mixed",
        "planner": "GLM-5.2 advisory supervisor",
        "executor": "opencode",
        "worker": "local Node1 model lane with Codex spot-checks",
        "verification": "deterministic gates plus selected Codex review",
        "labs": False,
        "experimental": False,
        "requires_scope": False,
        "hidden": False,
        "backend_route": "supervise-existing-goal",
        "supports_execution_profiles": False,
        "uses_local_node1": True,
        "leaves_local_lane_free": False,
    },
    {
        "key": "mode3a",
        "technical_mode": "Mode 3A",
        "backend_id": "mode3a_cloud_long_horizon_goal",
        "mode_number": "3A",
        "label": "Cloud long run",
        "summary": "Run a bounded long-horizon goal while leaving the local Node1 lane available.",
        "best_for": "A separate, explicitly scoped cloud task when the local lane is busy.",
        "caution": "Source context is sent to the configured cloud planner and worker.",
        "recommended": False,
        "maturity": "installed_capability_bounded",
        "mutation": "implementation",
        "data_location": "cloud_provider",
        "local_only": False,
        "network_scope": "cloud",
        "planner": MODE3A_PLANNER,
        "executor": "opencode",
        "worker": MODE3A_WORKER,
        "verification": "local reconciliation, deterministic review, and acceptance gates",
        "labs": False,
        "experimental": False,
        "requires_scope": True,
        "hidden": False,
        "backend_route": f"enqueue:{MODE3A_WORKER}",
        "supports_execution_profiles": False,
        "uses_local_node1": False,
        "leaves_local_lane_free": True,
    },
    {
        "key": "mode4",
        "technical_mode": "Mode 4",
        "backend_id": "glm52_fully_local_long_horizon_executor",
        "mode_number": "4",
        "label": "Read-only audit",
        "summary": "Ask direct GLM-5.2 for an evidence-backed audit or implementation proposal.",
        "best_for": "Fast review and proposal work that must not change source files.",
        "caution": "This route cannot implement fixes; it writes managed audit artifacts only.",
        "recommended": False,
        "maturity": "audit_proposal_only",
        "mutation": "audit_only",
        "data_location": "cloud_provider",
        "local_only": False,
        "network_scope": "cloud",
        "planner": "none",
        "executor": "direct GLM-5.2 audit worker",
        "worker": MODE4_WORKER,
        "verification": "read-only evidence checks and managed audit artifacts",
        "labs": False,
        "experimental": False,
        "requires_scope": False,
        "hidden": False,
        "backend_route": f"enqueue:{MODE4_WORKER}:audit-only",
        "supports_execution_profiles": False,
        "uses_local_node1": False,
        "leaves_local_lane_free": True,
    },
    {
        "key": "mode4b",
        "technical_mode": "Mode 4B",
        "backend_id": "glm52_direct_implementation_canary",
        "mode_number": "4B",
        "label": "One-file implementation canary",
        "summary": "Evaluate direct GLM implementation inside a one-file experimental boundary.",
        "best_for": "A deliberate laboratory canary, never normal project work.",
        "caution": "The installed worker is currently disabled and must not be presented as runnable.",
        "recommended": False,
        "maturity": "canary_only",
        "mutation": "canary_implementation",
        "data_location": "cloud_provider",
        "local_only": False,
        "network_scope": "cloud",
        "planner": "none",
        "executor": "direct GLM-5.2 canary worker",
        "worker": MODE4B_WORKER,
        "verification": "one-file boundary, deterministic review, and comparison evidence",
        "labs": True,
        "experimental": True,
        "requires_scope": True,
        "hidden": True,
        "backend_route": f"enqueue:{MODE4B_WORKER}:canary",
        "supports_execution_profiles": False,
        "uses_local_node1": False,
        "leaves_local_lane_free": True,
    },
)


def _route_ui_payload(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add stable presentation metadata without changing routing identities."""
    location_labels = {
        "local_node1": "Your local AI",
        "managed_local": "Your local AI",
        "mixed_local_cloud": "Cloud planning and local execution",
        "cloud_provider": "Configured cloud provider",
    }
    for route in routes:
        capabilities = ["implementation"] if route.get("mutation") == "implementation" else []
        if route.get("mutation") == "audit_only":
            capabilities.append("read-only audit")
        if route.get("supports_execution_profiles"):
            capabilities.append("selectable model profile")
        if route.get("local_only"):
            capabilities.append("local-only")
        route.update(
            {
                "friendly_name": str(route.get("label") or "Execution route"),
                "short_purpose": str(route.get("summary") or ""),
                "location_label": location_labels.get(
                    str(route.get("data_location") or ""),
                    "Managed execution",
                ),
                "availability_state": "ready" if route.get("available") is True else "unavailable",
                "unavailable_reason": str(route.get("disabled_reason") or ""),
                "capabilities": capabilities,
                "technical_label": str(
                    route.get("technical_label") or route.get("technical_mode") or ""
                ),
                "advanced_only": bool(route.get("labs") or route.get("hidden")),
            }
        )
    return routes


_EXECUTION_EFFORTS: tuple[dict[str, Any], ...] = (
    {
        "key": "quick",
        "label": "Quick",
        "summary": "One narrow, focused implementation pass with a relevant check.",
        "policy": "Keep scope tight, avoid optional expansion, and verify the requested outcome.",
        "recommended": False,
    },
    {
        "key": "standard",
        "label": "Standard",
        "summary": "Plan briefly, implement the complete request, verify it, and repair relevant failures.",
        "policy": "Use a short plan and evidence-backed closeout without unnecessary exploration.",
        "recommended": True,
    },
    {
        "key": "thorough",
        "label": "Thorough",
        "summary": "Use checkpoints, persistent repair, and a structured completion audit.",
        "policy": "Continue through recoverable failures and preserve durable evidence across attempts.",
        "recommended": False,
    },
)


def modes_payload(bridge: LocalGoalBridge | None = None) -> list[dict[str, Any]]:
    """Return truthful managed routes, overlaid with current backend readiness.

    Without a bridge this remains a useful schema/UX description, but all
    execution is marked unavailable. A managed server should pass its bridge so
    current harness-modes, capabilities, and adapter-matrix facts determine the
    selectable routes.
    """
    routes = [
        {**spec, "available": False, "enabled": False, "disabled_reason": ""}
        for spec in _MANAGED_ROUTE_SPECS
    ]
    if bridge is None:
        for route in routes:
            route["disabled_reason"] = (
                "Current managed backend availability has not been checked."
                if route["key"] != "mode4b"
                else "Mode 4B is disabled until its canary worker is explicitly enabled and verified."
            )
        return _route_ui_payload(routes)
    if not bridge.available():
        for route in routes:
            route["disabled_reason"] = (
                "The managed local-goal backend is not installed or executable."
            )
        return _route_ui_payload(routes)

    modes_result = _bridge_command(bridge, "harness_modes", ["harness-modes", "--json"])
    capabilities_result = _bridge_command(bridge, "capabilities", ["capabilities", "--json"])
    matrix_result = _bridge_command(bridge, "adapter_matrix", ["adapter-matrix", "--json"])
    modes_document = (
        _json_from_output(modes_result.stdout) if modes_result.returncode == 0 else None
    )
    capabilities_document = (
        _json_from_output(capabilities_result.stdout)
        if capabilities_result.returncode == 0
        else None
    )
    matrix_document = (
        _json_from_output(matrix_result.stdout) if matrix_result.returncode == 0 else None
    )
    registry = _mode_registry(modes_document)
    capabilities = _capabilities_document(capabilities_document)
    workers = _worker_registry(matrix_document)

    if not registry and isinstance(capabilities.get("lanes"), dict):
        return _route_ui_payload(_legacy_modes_payload(capabilities_document))

    recommended_id = str((modes_document or {}).get("recommended_default_mode") or "")
    local_lane = _dictionary(_dictionary(capabilities.get("lanes")).get("local"))
    cloud_lane = _dictionary(_dictionary(capabilities.get("lanes")).get("cloud_executor"))
    current_contract_ok = bool(
        (modes_document or {}).get("contract") == "local_goal_harness_modes.v1"
        and (modes_document or {}).get("status") == "available"
    )
    contract_ok = bool(
        current_contract_ok
        or _document_supports_contract(capabilities_document, EXTERNAL_CANDIDATE_CONTRACT)
    )
    candidate_contract_ok = _document_supports_contract(
        capabilities_document, EXTERNAL_CANDIDATE_CONTRACT
    )
    adapter_document: dict[str, Any] | None = None
    turnstone_executable = _is_turnstone_bridge(bridge)
    if turnstone_executable:
        adapter_result = _bridge_command(
            bridge,
            "mode3a_adapter_status",
            ["turnstone-monitor", "--json"],
        )
        if adapter_result.returncode == 0:
            adapter_document = _json_from_output(adapter_result.stdout)
    adapter = _dictionary(adapter_document)
    turnstone_monitor_proven = bool(adapter.get("contract") == "agentic_harness_turnstone_mode3.v1")
    turnstone_adapter = turnstone_executable

    for route in routes:
        backend = registry.get(str(route["backend_id"]), {})
        if backend:
            route["technical_label"] = str(backend.get("label") or route["label"])
            route["best_for"] = str(backend.get("use_for") or route["best_for"])
            route["canonical_maturity"] = str(backend.get("readiness") or route["maturity"])
            if route["key"] != "mode1":
                route["maturity"] = route["canonical_maturity"]
            route["backend_gates"] = _string_list(backend.get("gates"))
        route["recommended"] = bool(
            route["key"] == "mode1"
            and (not recommended_id or recommended_id == route["backend_id"])
        )

        if not backend:
            route["disabled_reason"] = (
                "The current harness mode registry does not advertise this route."
            )
            continue
        key = route["key"]
        if key == "mode1":
            if not current_contract_ok or not managed_mode_record_dispatchable(backend):
                route["available"] = False
                route["disabled_reason"] = _managed_mode_disabled_reason(
                    backend,
                    contract_ok=current_contract_ok,
                )
            else:
                route["available"] = _lane_available(local_lane)
                route["disabled_reason"] = (
                    "" if route["available"] else _lane_disabled_reason(local_lane)
                )
        elif key == "mode2":
            route["available"] = False
            route["disabled_reason"] = (
                "Mode 2 supervises an already-running local goal; no independent safe start "
                "operation is advertised by the managed backend."
            )
        elif key == "mode3a":
            if not current_contract_ok or not managed_mode_record_dispatchable(backend):
                route["available"] = False
                route["disabled_reason"] = _managed_mode_disabled_reason(
                    backend,
                    contract_ok=current_contract_ok,
                )
            elif turnstone_adapter:
                route.update(
                    {
                        "technical_mode": "Mode 3A via Turnstone",
                        "label": "Turnstone long run",
                        "summary": (
                            "Use Turnstone's cloud GLM coordinator to plan and its local "
                            "Node1 model child to perform bounded implementation."
                        ),
                        "caution": (
                            "This adapter uses the local Node1 model for implementation; it "
                            "does not leave Node1 free for another local model workload."
                        ),
                        "data_location": "mixed_local_cloud",
                        "network_scope": "mixed",
                        "planner": "glm-coordinator (LiteLLM GLM cloud coordinator)",
                        "executor": "local-builder (LiteLLM local-node1-vllm)",
                        "worker": "local Node1 vLLM child",
                        "backend_route": "turnstone:mode3a",
                        "adapter_contract": "agentic_harness_turnstone_mode3.v1",
                        "route_facts_proven": turnstone_monitor_proven,
                        "uses_local_node1": True,
                        "leaves_local_lane_free": False,
                    }
                )
                adapter_active = adapter.get("active") is True
                adapter_state = str(
                    adapter.get("classification") or adapter.get("status") or ""
                ).lower()
                route["available"] = bool(
                    candidate_contract_ok
                    and turnstone_monitor_proven
                    and _lane_available(cloud_lane)
                    and not adapter_active
                    and adapter_state in {"ready", "done"}
                )
                if route["available"]:
                    route["disabled_reason"] = ""
                elif not candidate_contract_ok:
                    route["disabled_reason"] = (
                        "The Turnstone wrapper does not advertise the required external "
                        "candidate dispatch contract."
                    )
                elif not turnstone_monitor_proven:
                    route["disabled_reason"] = (
                        "Turnstone route identity or readiness could not be verified from "
                        "its managed adapter contract."
                    )
                elif adapter_active:
                    route["disabled_reason"] = "A Turnstone long run is already active."
                elif not _lane_available(cloud_lane):
                    route["disabled_reason"] = _lane_disabled_reason(cloud_lane)
                else:
                    route["disabled_reason"] = str(
                        adapter.get("summary")
                        or "The Turnstone adapter is not ready for another task."
                    )
            else:
                worker = workers.get(MODE3A_WORKER, {})
                route["available"] = bool(
                    contract_ok
                    and _lane_available(cloud_lane)
                    and _worker_available(worker, mutation="implementation")
                )
                route["disabled_reason"] = (
                    ""
                    if route["available"]
                    else _cloud_disabled_reason(contract_ok, cloud_lane, worker, MODE3A_WORKER)
                )
        elif key == "mode4":
            route["available"] = False
            route["disabled_reason"] = (
                "Mode 4 audit dispatch is disabled until the managed wrapper advertises a "
                "distinct audit-only routing contract."
            )
        elif key == "mode4b":
            route["available"] = False
            route["hidden"] = True
            route["disabled_reason"] = (
                "Mode 4B is a disabled implementation canary and cannot be started from "
                "the managed GUI."
            )
        route["enabled"] = route["available"]
    return _route_ui_payload(routes)


def execution_efforts_payload() -> list[dict[str, Any]]:
    return [dict(effort) for effort in _EXECUTION_EFFORTS]


def execution_profiles_payload(
    bridge: LocalGoalBridge | None = None,
) -> list[dict[str, Any]]:
    """Expose installation-specific model profiles only when the backend proves support."""
    if bridge is None or not bridge.available():
        return []
    result = _bridge_command(
        bridge,
        "model_profile_status",
        ["model-profile-status", "--json"],
    )
    payload = _json_from_output(result.stdout) if result.returncode == 0 else None
    if not isinstance(payload, dict) or payload.get("contract") != "node1_model_profile.v1":
        return []
    current = str(payload.get("profile") or "")
    runtime = {
        key: payload[key]
        for key in ("max_model_len", "max_num_seqs", "mtp_enabled", "quantization")
        if key in payload
    }
    return [
        {
            "key": "qwen-primary",
            "label": "Qwen primary",
            "summary": "Production local lane with text, tools, and vision.",
            "caution": "Recommended default. No temporary model window is needed.",
            "vision": True,
            "capabilities": ["Coding", "Tools", "Vision", "Long context"],
            "capability_evidence": "node1_model_profile.v1",
            "runtime": runtime if current == "qwen-primary" else {},
            "recommended": True,
            "requires_swap": False,
            "route_key": "mode1",
            "active": current == "qwen-primary",
        },
        {
            "key": "ornith-text",
            "label": "Ornith fast text",
            "summary": "Higher-throughput local text and tool lane on the same GPUs.",
            "caution": "Text only. Qwen is restored when the attached goal finishes or start-up fails.",
            "vision": False,
            "capabilities": ["Coding", "Tools", "Text only"],
            "capability_evidence": "node1_model_profile.v1",
            "runtime": runtime if current == "ornith-text" else {},
            "recommended": False,
            "requires_swap": True,
            "route_key": "mode1",
            "active": current == "ornith-text",
        },
    ]


def _bridge_command(
    bridge: LocalGoalBridge,
    method_name: str,
    args: list[str],
) -> CommandResult:
    method = getattr(bridge, method_name, None)
    try:
        if callable(method):
            return cast(CommandResult, method())
        return bridge.run(args)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return CommandResult(tuple(args), 127, "", str(exc))


def _is_turnstone_bridge(bridge: LocalGoalBridge) -> bool:
    """Recognize the deployed adapter without applying its facts to generic wrappers."""
    local_goal = getattr(bridge, "local_goal", None)
    return local_goal is not None and Path(str(local_goal)).name == "local-goal-turnstone"


def _dictionary(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mode_registry(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _dictionary(payload).get("modes")
    if not isinstance(rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def _capabilities_document(payload: dict[str, Any] | None) -> dict[str, Any]:
    document = _dictionary(payload)
    nested = document.get("capabilities")
    return nested if isinstance(nested, dict) else document


def _worker_registry(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = _dictionary(payload).get("matrix")
    if not isinstance(rows, list):
        return {}
    return {
        str(row["worker"]): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("worker"), str)
    }


def _document_supports_contract(payload: dict[str, Any] | None, contract: str) -> bool:
    document = _dictionary(payload)
    containers = [document]
    nested = document.get("capabilities")
    if isinstance(nested, dict):
        containers.append(nested)
    return any(
        isinstance(container.get("external_candidate_contracts"), list)
        and contract in container["external_candidate_contracts"]
        for container in containers
    )


def _lane_available(lane: dict[str, Any]) -> bool:
    return lane.get("installed") is True and lane.get("available_now") is True


def _lane_disabled_reason(lane: dict[str, Any]) -> str:
    if not lane:
        return "The current backend did not advertise this execution lane."
    if lane.get("installed") is not True:
        return "This execution lane is not installed."
    reason = str(lane.get("unavailable_reason") or lane.get("availability_reason") or "").strip()
    return reason.replace("_", " ") if reason else "This execution lane is not available right now."


def _worker_available(worker: dict[str, Any], *, mutation: str) -> bool:
    readiness = str(worker.get("readiness") or "").lower()
    return (
        bool(worker)
        and worker.get("enabled") is True
        and worker.get("binary_resolved") is not False
        and readiness not in {"blocked", "disabled", "retired"}
        and worker.get("mutation_default") == mutation
    )


def _managed_mode_disabled_reason(
    mode: dict[str, Any],
    *,
    contract_ok: bool,
) -> str:
    if not contract_ok:
        return "The managed mode registry contract is missing or unavailable."
    blockers = _string_list(mode.get("blockers"))
    if blockers:
        return f"This route is blocked by the managed registry: {', '.join(blockers)}."
    readiness = str(mode.get("readiness") or "").strip().replace("_", " ")
    if readiness:
        return f"The managed registry marks this route as {readiness}."
    return "The managed registry does not mark this route as dispatchable."


def _cloud_disabled_reason(
    contract_ok: bool,
    lane: dict[str, Any],
    worker: dict[str, Any],
    worker_name: str,
) -> str:
    if not contract_ok:
        return (
            "The backend does not advertise the current managed-mode or legacy candidate contract."
        )
    if not _lane_available(lane):
        return _lane_disabled_reason(lane)
    if not worker:
        return f"The worker registry does not advertise {worker_name}."
    if worker.get("enabled") is not True:
        return f"The worker {worker_name} is disabled."
    blockers = _string_list(worker.get("blockers"))
    if blockers:
        return f"The worker {worker_name} is blocked: {', '.join(blockers)}."
    return f"The worker {worker_name} has not passed the required dispatch contract."


def _legacy_modes_payload(
    capabilities_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Describe a generic older local-goal backend without assigning canonical mode numbers."""
    capabilities = _capabilities_document(capabilities_payload)
    lanes = _dictionary(capabilities.get("lanes"))
    local = _dictionary(lanes.get("local"))
    guided = _dictionary(lanes.get("premium_planner_local_builder"))
    cloud = _dictionary(lanes.get("cloud_executor"))
    candidate_contract = _document_supports_contract(
        capabilities_payload, EXTERNAL_CANDIDATE_CONTRACT
    )

    def record(
        *,
        key: str,
        label: str,
        summary: str,
        lane: dict[str, Any],
        backend_route: str,
        planner: str,
        executor: str,
        worker: str,
        mutation: str,
        data_location: str,
        labs: bool = False,
        experimental: bool = False,
        requires_scope: bool = False,
        available_override: bool | None = None,
        disabled_reason: str = "",
    ) -> dict[str, Any]:
        available = _legacy_lane_available(lane)
        if available_override is not None:
            available = available_override
        return {
            "key": key,
            "technical_mode": "Compatibility route",
            "backend_id": f"legacy-{key}",
            "mode_number": "",
            "label": label,
            "summary": summary,
            "best_for": summary,
            "caution": (
                "This backend does not advertise the current canonical harness-mode registry; "
                "routing follows its legacy capabilities contract."
            ),
            "available": available,
            "enabled": available,
            "recommended": key == "local",
            "maturity": str(lane.get("classification") or "legacy_compatibility"),
            "mutation": mutation,
            "data_location": data_location,
            "local_only": False,
            "network_scope": "managed",
            "planner": planner,
            "executor": executor,
            "worker": worker,
            "verification": "managed backend review and acceptance",
            "labs": labs,
            "experimental": experimental,
            "requires_scope": requires_scope,
            "hidden": experimental and not available,
            "disabled_reason": ""
            if available
            else (disabled_reason or _legacy_lane_disabled_reason(lane)),
            "backend_route": backend_route,
            "supports_execution_profiles": False,
        }

    executor = str(local.get("executor") or "opencode")
    planners = _string_list(guided.get("planners"))
    planner = "gpt-5.5" if "gpt-5.5" in planners else (planners[0] if planners else "planner")
    workers = _string_list(cloud.get("executor_workers"))
    configured_worker = str(cloud.get("default_executor_worker") or "")
    worker = configured_worker if configured_worker in workers else (workers[0] if workers else "")
    canaries = _string_list(cloud.get("adapter_canary_workers"))
    canary = canaries[0] if canaries else ""
    return [
        record(
            key="local",
            label="Local build",
            summary="Run through the local executor advertised by this installation.",
            lane=local,
            backend_route="quick-start",
            planner="none",
            executor=executor,
            worker="managed local lane",
            mutation="implementation",
            data_location="managed_local",
        ),
        record(
            key="guided",
            label="Planned local build",
            summary="Use an advertised planner before the managed local executor.",
            lane=guided,
            backend_route="premium-start",
            planner=planner,
            executor=executor,
            worker="managed local lane",
            mutation="implementation",
            data_location="mixed_or_managed",
        ),
        record(
            key="cloud",
            label="Managed cloud queue",
            summary="Queue work through the generic cloud worker advertised by this installation.",
            lane=cloud,
            backend_route="enqueue",
            planner=planner,
            executor=executor,
            worker=worker,
            mutation="implementation",
            data_location="managed_cloud",
            available_override=bool(candidate_contract and _legacy_lane_available(cloud)),
            disabled_reason=(
                "The legacy cloud route requires the explicitly advertised external "
                "candidate contract."
            ),
        ),
        record(
            key="experimental",
            label="Advertised worker canary",
            summary="Run a bounded canary only when the legacy backend advertises one.",
            lane=cloud,
            backend_route="enqueue:canary",
            planner="none",
            executor=executor,
            worker=canary,
            mutation="canary_implementation",
            data_location="managed_cloud",
            labs=True,
            experimental=True,
            requires_scope=True,
            available_override=bool(
                candidate_contract and _legacy_lane_available(cloud) and canary
            ),
            disabled_reason="No safely advertised legacy adapter canary is available.",
        ),
    ]


def _legacy_lane_available(lane: dict[str, Any]) -> bool:
    return bool(
        lane and lane.get("installed") is not False and lane.get("available_now") is not False
    )


def _legacy_lane_disabled_reason(lane: dict[str, Any]) -> str:
    if not lane:
        return "This legacy backend did not advertise the route."
    return _lane_disabled_reason(lane)


def _objective_with_effort(objective: str, effort: str) -> str:
    policies = {
        "quick": (
            "Keep the scope narrow and complete the requested outcome in one focused pass. "
            "Avoid optional expansion, but run the most relevant verification before closeout."
        ),
        "standard": (
            "Make a short plan, implement the complete request, run relevant verification, "
            "and repair failures caused by the change before closeout."
        ),
        "thorough": (
            "Use durable checkpoints, persist through recoverable failures, and finish with a "
            "structured completion audit plus recorded independent-check evidence."
        ),
    }
    return "\n".join(
        [
            "Managed task request",
            "",
            "Original objective (preserve this exactly):",
            objective,
            "",
            f"Execution effort: {effort}",
            policies[effort],
            "",
            "The effort policy changes depth and persistence, not the selected execution route.",
        ]
    )


def _accepts_keyword(function: object, keyword: str) -> bool:
    try:
        parameters = signature(function).parameters.values()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _annotate_requested_execution(
    task: TaskPayload,
    *,
    objective: str,
    route: str,
    effort: str,
    execution_profile: str,
    safe_areas: tuple[str, ...],
    checks: tuple[str, ...],
) -> TaskPayload:
    task["objective"] = objective
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        task["metadata"] = metadata
    metadata.update(
        {
            "route_key": route,
            "effort": effort,
            "execution_profile": execution_profile,
            "safe_areas": list(safe_areas),
            "checks": list(checks),
        }
    )
    metadata["execution_expectation"] = {
        "route_key": route,
        "effort": effort,
        "execution_profile": execution_profile
        if route in {"mode1", "legacy-guided"}
        else "not_applicable",
    }
    return task


def health_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    supervision = bridge.background_supervision()
    return {
        "ok": True,
        "app": "agentic-harness",
        "local_goal_available": bridge.available(),
        "local_goal_path": str(bridge.local_goal),
        "readiness": readiness_payload(bridge, supervision=supervision),
        "no_babysitting": {
            "enabled": supervision.get("active") is True,
            "policy": "The worker should move safe work forward without repeated check-ins.",
            "human_review_statuses": ["needs_review", "blocked"],
            "supervision": supervision,
        },
    }


def setup_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    """Describe the externally managed legacy backend to the shared GUI."""
    return {
        "contract": "agentic_harness.gui_setup.v1",
        "configured": bridge.available(),
        "editable": False,
        "workspace": str(bridge.doc_root),
        "worker": {
            "type": "local_goal",
            "label": "Existing local-goal runtime",
            "data_location": "managed_per_goal",
        },
        "execution_summary": (
            "Managed runtime. The active task shows whether its planner and executor are "
            "local, cloud, or mixed."
        ),
        "management": {
            "mode": "managed",
            "editable": False,
            "summary": (
                "AI routing and verification are managed by this installation. "
                "You can review the active configuration in Settings."
            ),
        },
        "verification": {
            "mode": "managed_automatic",
            "label": "Automatic evidence checks",
            "technical_command": "",
        },
    }


def readiness_payload(
    bridge: LocalGoalBridge,
    *,
    supervision: dict[str, object] | None = None,
) -> dict[str, Any]:
    if not bridge.available():
        return _readiness_gate(
            "blocked",
            "The background worker is not installed or is not executable on this machine.",
            {"local_goal_path": str(bridge.local_goal)},
        )
    supervision = supervision or bridge.background_supervision()
    if supervision.get("active") is not True:
        return _readiness_gate(
            "blocked",
            "The background assistant is paused. Ask the workspace owner to restart it, then refresh this page.",
            {"background_supervision": supervision},
        )
    task = status_task(bridge)
    gate = dict(task.get("readiness_gate", {}))
    gate["agent_loop"] = task.get(
        "agent_loop", _agent_loop_for_status(str(task.get("status", "ready")))
    )
    return gate


def start_task(
    bridge: LocalGoalBridge,
    body: dict[str, Any],
) -> TaskPayload:
    objective = str(body.get("objective", "")).strip()
    requested_route = str(body.get("route") or body.get("mode") or "mode1").strip() or "mode1"
    effort = str(body.get("effort", "standard")).strip().lower() or "standard"
    requested_profile = str(body.get("execution_profile", "")).strip()
    execution_profile = requested_profile or "automatic"
    safe_areas = tuple(_string_list(body.get("safe_areas")))
    checks = tuple(_string_list(body.get("checks")))
    if not objective:
        return _task(
            status="blocked",
            summary="Tell the assistant what you want done first.",
            needs_human=True,
            advanced_details={"error": "objective must not be empty"},
        )
    try:
        route = managed_route_key(requested_route)
    except ValueError as exc:
        return _task(
            status="blocked",
            summary=str(exc),
            needs_human=True,
            advanced_details={"error": str(exc)},
        )
    if effort not in {row["key"] for row in _EXECUTION_EFFORTS}:
        return _task(
            status="blocked",
            summary="Choose Quick, Standard, or Thorough effort.",
            needs_human=True,
            advanced_details={"error": f"unsupported execution effort: {effort}"},
        )
    if route == "mode2":
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary=(
                    "Mode 2 is not available as a standalone start route. Start Mode 1, then "
                    "use the managed supervision workflow when it is explicitly available."
                ),
                needs_human=True,
                advanced_details={"error": "mode2_not_independently_dispatchable"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    if route == "mode4b":
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary="Mode 4B is not available because its implementation-canary worker is disabled.",
                needs_human=True,
                advanced_details={"error": "mode4b_worker_disabled"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    if route == "legacy-experimental":
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary=(
                    "The former experimental route is retired because this backend cannot "
                    "prove a distinct bounded canary dispatch contract."
                ),
                needs_human=True,
                advanced_details={"error": "legacy_experimental_route_retired"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    if route == "mode4":
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary=(
                    "Mode 4 audit dispatch is not available because this backend does not "
                    "yet provide a distinct audit-only routing contract."
                ),
                needs_human=True,
                advanced_details={"error": "mode4_audit_route_not_distinct"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    if route in {"mode3a", "legacy-cloud"} and not safe_areas:
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary="Cloud long runs require at least one explicit allowed file or scope area.",
                needs_human=True,
                advanced_details={"error": "mode3a_scope_required"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    if not bridge.available():
        return _task(
            status="blocked",
            summary="The background worker is not installed or is not executable on this machine.",
            needs_human=True,
            advanced_details=health_payload(bridge),
        )
    readiness = readiness_payload(bridge)
    if readiness.get("can_queue") is not True:
        readiness_status = str(readiness.get("state") or "")
        status = readiness_status if readiness_status in {
            "needs_review",
            "needs_attention",
            "blocked",
        } else "blocked"
        return _task(
            status=status,
            summary=str(
                readiness.get("summary")
                or readiness.get("next_action")
                or "Review current work before starting another task."
            ),
            needs_human=True,
            advanced_details={"readiness": readiness},
        )
    if route == "mode1":
        available_profiles = execution_profiles_payload(bridge)
        default_profile = "qwen-primary" if available_profiles else "automatic"
        execution_profile = requested_profile or default_profile
        if execution_profile not in {
            "automatic",
            *[str(row["key"]) for row in available_profiles],
        }:
            return _annotate_requested_execution(
                _task(
                    status="blocked",
                    summary="Choose a supported local execution profile.",
                    needs_human=True,
                    advanced_details={
                        "error": f"unsupported execution profile: {execution_profile}"
                    },
                ),
                objective=objective,
                route=route,
                effort=effort,
                execution_profile=execution_profile,
                safe_areas=safe_areas,
                checks=checks,
            )
    elif execution_profile != "automatic":
        return _annotate_requested_execution(
            _task(
                status="blocked",
                summary="Local model profiles apply only to the Mode 1 local execution lane.",
                needs_human=True,
                advanced_details={"error": "execution_profile_not_applicable"},
            ),
            objective=objective,
            route=route,
            effort=effort,
            execution_profile=execution_profile,
            safe_areas=safe_areas,
            checks=checks,
        )
    try:
        start_kwargs: dict[str, Any] = {
            "mode_key": route,
            "objective": _objective_with_effort(objective, effort),
            "safe_areas": safe_areas,
            "checks": checks,
        }
        if execution_profile != "automatic" and _accepts_keyword(
            bridge.start_human_goal, "execution_profile"
        ):
            start_kwargs["execution_profile"] = execution_profile
        result = bridge.start_human_goal(**start_kwargs)
    except ValueError as exc:
        return _task(
            status="blocked",
            summary=str(exc),
            needs_human=True,
            advanced_details={"error": str(exc)},
        )
    task = task_from_command_result(result, fallback_status="starting")
    if (
        route == "mode1"
        and result.returncode == 0
        and isinstance(bridge, LocalGoalBridge)
        and not _start_receipt_matches_objective(result, objective)
    ):
        task = _task(
            status="blocked",
            summary=(
                "Your task was not started because another task acquired the local lane first. "
                "No guidance or labels were attached to that other task. Refresh and try again "
                "after it finishes."
            ),
            needs_human=True,
            advanced_details={
                "args": result.args,
                "returncode": result.returncode,
                "error": "start_identity_mismatch",
                "observed_run_dir": _started_run_dir(result),
            },
        )
    return _annotate_requested_execution(
        task,
        objective=objective,
        route=route,
        effort=effort,
        execution_profile=execution_profile,
        safe_areas=safe_areas,
        checks=checks,
    )


def _started_run_dir(result: CommandResult) -> str:
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "run_dir" and value.strip():
            return value.strip().rstrip("/")
    return ""


def _start_receipt_matches_objective(result: CommandResult, objective: str) -> bool:
    """Bind a successful Mode 1 start only to its exact harness-owned ticket."""

    run_dir = _started_run_dir(result)
    if not run_dir:
        return False
    current = Path(run_dir)
    if not current.is_absolute():
        return False
    descriptor = -1
    try:
        resolved_current = current.resolve(strict=True)
        if resolved_current != current or not resolved_current.is_dir():
            return False
        ticket = resolved_current / "ticket.json"
        ticket_stat = os.lstat(ticket)
        if stat.S_ISLNK(ticket_stat.st_mode) or not stat.S_ISREG(ticket_stat.st_mode):
            return False
        if ticket_stat.st_size > _MAX_START_TICKET_BYTES:
            return False
        descriptor = os.open(
            ticket,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        observed = os.fstat(descriptor)
        if (ticket_stat.st_dev, ticket_stat.st_ino) != (observed.st_dev, observed.st_ino):
            return False
        if not stat.S_ISREG(observed.st_mode):
            return False
        raw = os.read(descriptor, _MAX_START_TICKET_BYTES + 1)
        if len(raw) > _MAX_START_TICKET_BYTES:
            return False
        payload = json.loads(raw.decode("utf-8"))
        criteria = payload.get("done_criteria") if isinstance(payload, dict) else None
        source_goal = str(payload.get("source_goal") or "") if isinstance(payload, dict) else ""
        turnstone_marker = (
            "Original objective (preserve this exactly):\n"
            f"{objective}\n\nExecution effort:"
        )
        return (isinstance(criteria, list) and objective in criteria) or (
            turnstone_marker in source_goal
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def status_task(bridge: LocalGoalBridge) -> TaskPayload:
    if not bridge.available():
        return _task(
            status="blocked",
            summary="The background worker is not installed or is not executable on this machine.",
            needs_human=True,
            advanced_details=health_payload(bridge),
        )
    result = bridge.status(json_output=True)
    payload = _json_from_output(result.stdout)
    if _external_status_is_accepted(payload):
        last_run_result = bridge.last_run(json_output=True)
        last_run = _json_from_output(last_run_result.stdout)
        if _external_last_run_is_valid(payload, last_run):
            assert last_run is not None
            artifacts = [
                {"name": "Run evidence", "path": str(last_run.get("run_dir") or "")},
                {"name": "Goal prompt", "path": str(last_run.get("prompt_path") or "")},
            ]
            return _task(
                status="done",
                summary=str(
                    last_run.get("summary") or "The managed reviewer accepted this result."
                ),
                needs_human=False,
                changed_files=_string_list(last_run.get("owned_files_sample")),
                verification=_string_list(last_run.get("verification")),
                artifacts=[row for row in artifacts if row["path"]],
                advanced_details={
                    "args": result.args,
                    "returncode": result.returncode,
                    "payload": payload,
                    "last_run": last_run,
                },
            )
    return task_from_command_result(result, fallback_status="ready")


def watch_task(bridge: LocalGoalBridge) -> TaskPayload:
    if not bridge.available():
        return status_task(bridge)
    return task_from_command_result(bridge.monitor(json_output=True), fallback_status="checking")


def command_task(
    bridge: LocalGoalBridge, command: str, body: dict[str, Any] | None = None
) -> TaskPayload:
    if not bridge.available():
        return status_task(bridge)
    body = body or {}
    if command == "accept":
        result = bridge.run(["accept"])
    elif command == "continue":
        feedback = str(body.get("feedback", "")).strip()
        args = ["continue"]
        if feedback:
            args.extend(["--feedback", feedback])
        result = bridge.run(args)
    elif command == "nudge":
        feedback = str(body.get("feedback", "")).strip()
        if not feedback:
            return _task(
                status="blocked",
                summary="Guidance cannot be empty.",
                needs_human=True,
                advanced_details={"command": command},
            )
        result = bridge.run(["nudge", "--feedback", feedback])
    elif command == "stop":
        result = bridge.run(["stop"])
    else:
        return _task(
            status="blocked",
            summary=f"Unknown action: {command}",
            needs_human=True,
            advanced_details={"command": command},
        )
    return task_from_command_result(result, fallback_status="checking")


def details_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    status = status_task(bridge)
    return {
        "task": status,
        "raw": status.get("advanced_details", {}),
    }


def task_from_command_result(result: CommandResult, *, fallback_status: str) -> TaskPayload:
    parsed = _json_from_output(result.stdout)
    advanced_details: dict[str, Any] = {
        "args": result.args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.metadata:
        advanced_details["command_metadata"] = result.metadata
    if parsed is not None:
        advanced_details["payload"] = parsed
    if result.metadata.get("profile_recovery") == "failed":
        return _task(
            status="needs_review",
            summary=str(
                result.metadata.get("summary")
                or "The requested model change failed and the primary model could not be restored."
            ),
            needs_human=True,
            advanced_details={
                **advanced_details,
                "permanent_error": True,
                "profile_state_unknown": True,
            },
        )
    if result.metadata.get("profile_attachment") == "reconciliation_required":
        return _task(
            status="needs_review",
            summary=str(
                result.metadata.get("summary")
                or "The goal started, but its temporary model lease needs reconciliation."
            ),
            needs_human=True,
            advanced_details=advanced_details,
        )
    if result.returncode != 0:
        permanent = result.returncode in _PERMANENT_COMMAND_FAILURES
        return _task(
            status="blocked" if permanent else "checking",
            summary=_clean_summary(
                result.stderr or result.stdout or "The task could not move forward."
            ),
            needs_human=permanent,
            advanced_details={
                **advanced_details,
                "permanent_error": permanent,
                "transient_error": not permanent,
            },
        )
    status = _status_from_payload(parsed, fallback_status=fallback_status)
    summary = _summary_from_payload(parsed, result.stdout, fallback_status=status)
    return _task(
        status=status,
        summary=summary,
        needs_human=status in {"needs_review", "blocked"},
        changed_files=_changed_files_from_payload(parsed),
        verification=_verification_from_payload(parsed),
        advanced_details=advanced_details,
    )


def _task(
    *,
    status: str,
    summary: str,
    needs_human: bool,
    changed_files: list[str] | None = None,
    verification: list[str] | None = None,
    artifacts: list[dict[str, str]] | None = None,
    advanced_details: dict[str, Any] | None = None,
) -> TaskPayload:
    command = ""
    details = advanced_details or {}
    if isinstance(details.get("args"), (list, tuple)):
        command = " ".join(str(part) for part in details["args"])
    agent_loop = _agent_loop_for_status(status)
    readiness_gate = _readiness_gate(status, summary, details)
    runtime_context = _runtime_context(status, details)
    changed = changed_files or []
    checks = verification or []
    result_category = {
        "done": "verified_done",
        "blocked": "blocked",
        "stopped": "failed",
    }.get(status, "in_progress")
    final_result: dict[str, Any] = {}
    if status == "done":
        final_result = {
            "label": "Checks passed",
            "accepted": True,
            "summary": summary,
            "reason": summary,
            "worker_claim": {
                "label": "Worker claim (untrusted)",
                "trusted": False,
                "summary": "The worker reported completion; the configured managed checks passed.",
            },
            "attempts": max(1, runtime_context["current"]["cycle"]),
            "retries": 0,
            "review_attempts": [
                {
                    "number": 1,
                    "source": "managed reviewer",
                    "passed": True,
                    "summary": "Managed review accepted the result.",
                    "checks": [
                        {
                            "name": f"evidence_{number}",
                            "passed": True,
                            "message": message,
                            "independent": True,
                            "source": "independent",
                        }
                        for number, message in enumerate(checks, 1)
                    ],
                }
            ],
            "what_changed": changed,
            "verification_commands": [],
            "remaining": [],
        }
    return {
        "id": runtime_context["id"],
        "objective": runtime_context["objective"],
        "human_title": "Current work",
        "status": status,
        "status_label": _label_for_status(status),
        "progress": _progress_for_status(status),
        "summary": summary,
        "needs_human": needs_human,
        "changed_files": changed,
        "verification": checks,
        "artifacts": artifacts if artifacts is not None else _artifacts_from_details(details),
        "result_category": result_category,
        "final_result": final_result,
        "allowed_actions": _allowed_actions(status),
        "agent_loop": agent_loop,
        "readiness_gate": readiness_gate,
        "current": runtime_context["current"],
        "plan": runtime_context["plan"],
        "requirements": runtime_context["requirements"],
        "events": runtime_context["events"],
        "metadata": {
            "command": command,
            "updated_at": runtime_context["updated_at"] or datetime.now(UTC).isoformat(),
            "observed_at": _managed_observed_at(details),
            "execution": _managed_execution_context(details),
        },
        "advanced_details": details,
    }


def _managed_execution_context(details: dict[str, Any]) -> dict[str, str]:
    """Return a plain execution/data-location label for the managed backend."""
    payload = details.get("payload")
    if not isinstance(payload, dict):
        return {}
    active = payload.get("active_goal")
    runtime = payload.get("runtime")
    loop_state = runtime.get("loop_state") if isinstance(runtime, dict) else None
    planner = str(active.get("planner") or "").strip() if isinstance(active, dict) else ""
    executor = str(active.get("executor") or "").strip() if isinstance(active, dict) else ""
    model = str(loop_state.get("model") or "").strip() if isinstance(loop_state, dict) else ""
    local_model = bool(model and ("local" in model.lower() or "vllm" in model.lower()))

    if planner and local_model:
        return {
            "label": f"Hybrid: {planner} planner + local model",
            "data_location": "cloud_and_local",
            "detail": f"Planning uses {planner}; execution uses {model} through {executor or 'the managed worker'}.",
        }
    if local_model:
        return {
            "label": f"Local model: {model}",
            "data_location": "local",
            "detail": f"Execution stays on {model} through {executor or 'the managed worker'}.",
        }
    if planner or executor or model:
        route = " + ".join(part for part in (planner, model or executor) if part)
        return {
            "label": f"Managed route: {route}",
            "data_location": "managed",
            "detail": "This task uses the workspace owner's managed execution route.",
        }
    return {}


def _managed_observed_at(details: dict[str, Any]) -> str:
    payload = details.get("payload")
    if not isinstance(payload, dict):
        return ""
    value = payload.get("generated_at")
    return value.strip() if isinstance(value, str) else ""


def _external_status_is_accepted(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    active = payload.get("active_goal")
    goal_state = payload.get("goal_state")
    useful = payload.get("useful_execution")
    return (
        payload.get("contract") == "local_node1_goal_supervisor.v1"
        and _normalize_status(payload.get("classification")) == "done"
        and isinstance(active, dict)
        and active.get("accepted") is True
        and isinstance(active.get("run_dir"), str)
        and bool(active["run_dir"])
        and isinstance(goal_state, dict)
        and goal_state.get("accepted") is True
        and goal_state.get("review_status") == "accepted"
        and isinstance(useful, dict)
        and useful.get("useful") is True
        and useful.get("evidence_grounded") is True
    )


def _external_last_run_is_valid(
    status: dict[str, Any] | None,
    last_run: dict[str, Any] | None,
) -> bool:
    if not _external_status_is_accepted(status) or not isinstance(last_run, dict):
        return False
    assert status is not None
    active = status["active_goal"]
    run_dir = last_run.get("run_dir")
    owned = _string_list(last_run.get("owned_files_sample"))
    verification = _string_list(last_run.get("verification"))
    return (
        last_run.get("contract") == "local_node1_goal_last_run_summary.v1"
        and last_run.get("available") is True
        and _normalize_status(last_run.get("status")) == "done"
        and last_run.get("review_status") == "accepted"
        and run_dir == active.get("run_dir")
        and last_run.get("complete_source") in {"global", "run-local"}
        and isinstance(last_run.get("summary"), str)
        and bool(last_run["summary"].strip())
        and last_run.get("owned_file_count") == len(owned)
        and len(verification) >= 1
        and last_run.get("verification_count") == len(verification)
    )


def _allowed_actions(status: str) -> list[dict[str, Any]]:
    if status in {"starting", "working", "checking"}:
        return [
            {
                "action": "message",
                "label": "Send guidance",
                "enabled": True,
            },
            {
                "action": "stop",
                "label": "Stop safely",
                "enabled": True,
            }
        ]
    if status == "needs_review":
        return [
            {
                "action": "message",
                "label": "Send guidance and continue",
                "enabled": True,
            },
            {
                "action": "continue",
                "label": "Continue with a note",
                "enabled": True,
            },
            {
                "action": "accept",
                "label": "Accept result",
                "enabled": True,
            },
            {
                "action": "stop",
                "label": "Stop safely",
                "enabled": True,
            },
        ]
    if status == "needs_attention":
        return [
            {
                "action": "continue",
                "label": "Continue",
                "enabled": True,
            },
            {
                "action": "stop",
                "label": "Stop safely",
                "enabled": True,
            },
        ]
    return []


def _label_for_status(status: str) -> str:
    return {
        "ready": "Ready",
        "starting": "Starting",
        "working": "Working",
        "checking": "Checking work",
        "needs_review": "Needs review",
        "needs_attention": "Needs attention",
        "done": "Done",
        "blocked": "Blocked",
        "stopped": "Stopped",
    }.get(status, "Working")


def _progress_for_status(status: str) -> dict[str, Any]:
    """Expose only progress that the external runtime can honestly measure."""
    if status == "done":
        return {"determinate": True, "percent": 100, "label": "Complete"}
    if status in {"starting", "working", "checking", "needs_review", "needs_attention"}:
        return {"determinate": False, "percent": None, "label": "In progress"}
    return {"determinate": False, "percent": None, "label": ""}


def _runtime_context(status: str, details: dict[str, Any]) -> dict[str, Any]:
    payload = details.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    active_goal = payload.get("active_goal")
    active_goal = active_goal if isinstance(active_goal, dict) else {}
    goal_state = payload.get("goal_state")
    goal_state = goal_state if isinstance(goal_state, dict) else {}
    runtime = payload.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    loop_state = runtime.get("loop_state")
    loop_state = loop_state if isinstance(loop_state, dict) else {}
    latest_event = payload.get("events")
    latest_event = latest_event.get("latest") if isinstance(latest_event, dict) else {}
    latest_event = latest_event if isinstance(latest_event, dict) else {}

    iteration = _nonnegative_int(loop_state.get("iteration"))
    maximum = _nonnegative_int(loop_state.get("max_iterations"))
    phase = str(goal_state.get("phase") or payload.get("phase") or status).strip().lower()
    updated_at = next(
        (
            value.strip()
            for value in (
                loop_state.get("updated_at"),
                goal_state.get("last_updated"),
                latest_event.get("ts"),
                payload.get("generated_at"),
            )
            if isinstance(value, str) and value.strip()
        ),
        "",
    )
    objective = str(active_goal.get("objective") or "").strip()
    run_dir = str(active_goal.get("run_dir") or "").rstrip("/")
    task_id = str(active_goal.get("id") or "").strip()
    if not task_id and run_dir:
        task_id = run_dir.rsplit("/", 1)[-1]

    subgoal = active_goal.get("current_subgoal")
    if isinstance(subgoal, dict):
        subgoal = subgoal.get("title") or subgoal.get("objective") or subgoal.get("id")
    subgoal = str(subgoal or "").strip()
    current_action = subgoal or _current_action(status, iteration)
    checkpoint = _checkpoint_label(status, phase, iteration, maximum)
    events = _runtime_events(status, iteration, checkpoint, updated_at)

    return {
        "id": task_id,
        "objective": objective,
        "updated_at": updated_at,
        "current": {
            "cycle": iteration,
            "max_cycles": maximum,
            "current_subgoal": current_action,
            "checkpoint": checkpoint,
            "last_event_at": updated_at,
        },
        "plan": _workflow_plan(status),
        "requirements": (
            [{"status": "active", "text": f"Requested outcome: {objective}"}] if objective else []
        ),
        "events": events,
    }


def _nonnegative_int(value: Any) -> int:
    if type(value) is not int:
        return 0
    return max(0, value)


def _current_action(status: str, iteration: int) -> str:
    if status == "starting":
        return "Preparing the task"
    if status == "working":
        return (
            f"Working through the request (pass {iteration})"
            if iteration
            else "Working through the request"
        )
    if status == "checking":
        return "Checking the evidence"
    if status == "needs_review":
        return "Preparing the result for review"
    if status == "done":
        return "Finished and independently verified"
    if status == "blocked":
        return "Waiting for a required decision"
    if status == "stopped":
        return "Stopped safely"
    return "Waiting for a goal"


def _checkpoint_label(status: str, phase: str, iteration: int, maximum: int) -> str:
    if status == "working" and iteration:
        return f"Pass {iteration} of up to {maximum}" if maximum else f"Pass {iteration}"
    if status == "starting":
        return "Starting"
    if status == "checking":
        return "Verification"
    if status == "needs_review":
        return "Review"
    if status == "done":
        return "Complete"
    if status == "blocked":
        return "Needs attention"
    if status == "stopped":
        return "Stopped"
    if phase and phase not in {"idle", "ready"}:
        return phase.replace("_", " ").title()
    return "Ready"


def _runtime_events(
    status: str,
    iteration: int,
    checkpoint: str,
    updated_at: str,
) -> list[dict[str, Any]]:
    if status not in {"starting", "working", "checking", "needs_review", "needs_attention"}:
        return []
    if status == "working" and iteration:
        summary = f"Agent pass {iteration} is active."
    elif status == "starting":
        summary = "The task was accepted and is being prepared."
    elif status == "checking":
        summary = "The latest work is being checked."
    else:
        summary = "The latest evidence is ready for review."
    return [
        {
            "stage": "act" if status in {"starting", "working"} else "check",
            "summary": summary,
            "checkpoint": checkpoint,
            "at": updated_at,
        }
    ]


def _workflow_plan(status: str) -> list[dict[str, str]]:
    if status == "ready":
        return []
    order = ["Understand the request", "Complete the requested work", "Verify the result"]
    active_index = {
        "starting": 0,
        "working": 1,
        "checking": 2,
        "needs_review": 2,
        "needs_attention": 1,
        "done": 3,
        "blocked": 1,
        "stopped": 1,
    }.get(status, 0)
    rows: list[dict[str, str]] = []
    for index, step in enumerate(order):
        if index < active_index:
            step_status = "completed"
        elif index == active_index and active_index < len(order):
            step_status = "in_progress"
        else:
            step_status = "pending"
        rows.append({"status": step_status, "step": step})
    return rows


def _agent_loop_for_status(status: str) -> dict[str, Any]:
    stages = ["Perceive", "Plan", "Act", "Check", "Review"]
    current = {
        "ready": "Perceive",
        "starting": "Plan",
        "working": "Act",
        "checking": "Check",
        "needs_review": "Review",
        "needs_attention": "Review",
        "done": "Review",
        "blocked": "Review",
        "stopped": "Review",
    }.get(status, "Act")
    return {
        "name": "Local agent loop",
        "stage": current,
        "steps": stages,
        "description": {
            "Perceive": "Understand the request and current machine state.",
            "Plan": "Choose the safest work route and boundaries.",
            "Act": "Run the configured worker inside its task boundaries.",
            "Check": "Verify results before asking for acceptance.",
            "Review": "Ask for a human decision only when needed.",
        }[current],
    }


def _readiness_gate(status: str, summary: str, details: dict[str, Any]) -> dict[str, Any]:
    active_run_dir = ""
    payload = details.get("payload")
    if isinstance(payload, dict):
        active_run_dir = _nested_string(
            payload, ("capabilities", "current_state", "active_run_dir")
        )
        active_goal = payload.get("active_goal")
        if not active_run_dir and isinstance(active_goal, dict):
            run_dir = active_goal.get("run_dir")
            active_run_dir = run_dir if isinstance(run_dir, str) else ""
    requires_review = status == "needs_review"
    can_start = status in {"ready", "done", "stopped"}
    can_queue = status not in {"needs_review", "needs_attention", "blocked"}
    if requires_review:
        next_action = "Review or continue the current work before starting another task."
    elif status == "needs_attention":
        next_action = "Open the current task, then continue or stop it before starting another task."
    elif status == "blocked":
        next_action = "Resolve the blocker before starting another task."
    elif status in {"working", "checking", "starting"}:
        next_action = "Background supervisor owns the active work; no routine action is needed."
    else:
        next_action = "Ready to start a task."
    return {
        "state": status,
        "label": _label_for_status(status),
        "can_start": can_start,
        "can_queue": can_queue,
        "requires_review": requires_review,
        "production_ready": status in {"ready", "done"},
        "summary": summary,
        "next_action": next_action,
        "active_run_dir": active_run_dir,
        "guardrails": [
            "One visible task decision at a time.",
            "Review gates must pass before done is trusted.",
            "Raw commands and run paths stay in Advanced details.",
        ],
    }


def _artifacts_from_details(details: dict[str, Any]) -> list[dict[str, str]]:
    payload = details.get("payload")
    if not isinstance(payload, dict):
        return []
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    normalized: list[dict[str, str]] = []
    for artifact in artifacts:
        if isinstance(artifact, dict):
            name = str(artifact.get("name") or artifact.get("path") or "").strip()
            path = str(artifact.get("path") or artifact.get("url") or "").strip()
            if name or path:
                normalized.append({"name": name or path, "path": path or name})
        elif str(artifact).strip():
            value = str(artifact).strip()
            normalized.append({"name": value, "path": value})
    return normalized[:12]


def _json_from_output(output: str) -> dict[str, Any] | None:
    text = output.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _status_from_payload(payload: dict[str, Any] | None, *, fallback_status: str) -> str:
    if payload is None:
        if fallback_status in {"starting", "checking", "ready"}:
            return fallback_status
        return "working"
    if _payload_reports_completion(payload):
        return "done" if _payload_is_accepted(payload) else "needs_review"
    if (
        payload.get("active") is False
        or payload.get("active_goal") is None
        and "active_goal" in payload
    ):
        return "ready"

    recovery_status = _recovery_status(payload)
    if recovery_status:
        return recovery_status

    classification = _payload_classification(payload)
    if classification:
        return classification

    active_goal = payload.get("active_goal")
    if isinstance(active_goal, dict):
        if active_goal.get("awaiting_review") is True:
            return "needs_review"
        if active_goal.get("accepted") is True:
            return "done"
        goal_status = _normalize_status(active_goal.get("status"))
        if goal_status:
            return goal_status

    status = _normalize_status(payload.get("status"))
    if status:
        return status

    text = json.dumps(payload, sort_keys=True).lower()
    if any(
        marker in text for marker in ("needs_review", "awaiting_review", '"review"', "needs review")
    ):
        return "needs_review"
    if any(
        marker in text
        for marker in ("blocked", "failed", "error", "operator_intervention_required")
    ):
        return "blocked"
    if any(marker in text for marker in ("stopped", "cancelled", "canceled")):
        return "stopped"
    if any(marker in text for marker in ('"done"', '"complete"', '"completed"')):
        return "needs_review"
    return "working"


def _recovery_status(payload: dict[str, Any]) -> str:
    recovery = payload.get("recovery_block")
    recovery = recovery if isinstance(recovery, dict) else {}
    if _local_goal_lane_is_ready(payload):
        # The controller retains recovery details for auditability after an
        # operator acknowledges a stopped run.  Those stale details must not
        # override the current, authoritative lane-ready state or a new user
        # will see an unrelated historical run as a blocking active task.
        return ""
    if (
        payload.get("hard_blocked") is True
        or recovery.get("operator_intervention_required") is True
    ):
        return "blocked"
    runtime = payload.get("runtime")
    loop_state = runtime.get("loop_state") if isinstance(runtime, dict) else None
    loop_state = loop_state if isinstance(loop_state, dict) else {}
    loop_status = str(loop_state.get("status") or "").strip().lower()
    active_goal = payload.get("active_goal")
    accepted = active_goal.get("accepted") if isinstance(active_goal, dict) else None
    if loop_status in {"stopped_incomplete", "needs_attention"} and accepted is not True:
        return "checking"
    return ""


def _local_goal_lane_is_ready(payload: dict[str, Any]) -> bool:
    current_state = payload.get("capabilities")
    current_state = current_state.get("current_state") if isinstance(current_state, dict) else {}
    current_state = current_state if isinstance(current_state, dict) else {}
    return (
        _normalize_status(current_state.get("classification")) == "ready"
        and current_state.get("local_goal_lane_free") is True
    )


def _payload_classification(payload: dict[str, Any]) -> str:
    for value in (
        payload.get("classification"),
        _nested_string(payload, ("capabilities", "current_state", "classification")),
    ):
        status = _normalize_status(value)
        if status:
            return status
    return ""


def _normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"needs_review", "awaiting_review", "review", "review_required"}:
        return "needs_review"
    if normalized in {"needs_attention", "attention_required"}:
        return "needs_attention"
    if normalized in {"accepted", "done", "complete", "completed", "success"}:
        return "done"
    if normalized in {"blocked", "failed", "failure", "error", "operator_intervention_required"}:
        return "blocked"
    if normalized in {"stopped", "cancelled", "canceled"}:
        return "stopped"
    if normalized in {"running", "working", "in_progress", "active"}:
        return "working"
    if normalized in {"checking", "verifying", "reviewing"}:
        return "checking"
    if normalized in {"queued", "starting", "pending"}:
        return "starting"
    if normalized in {"idle", "ready", "free"}:
        return "ready"
    return ""


def _summary_from_payload(
    payload: dict[str, Any] | None,
    stdout: str,
    *,
    fallback_status: str,
) -> str:
    if payload:
        if _payload_is_accepted(payload):
            return "Previous work is accepted. Ready for the next task."
        if _payload_reports_completion(payload):
            return (
                "The external runtime reported completion without a valid harness-issued "
                "acceptance receipt. Review and verify the candidate before accepting it."
            )
        if _local_goal_lane_is_ready(payload):
            return "No local goal is running. Ready for a new task."
        recovery_status = _recovery_status(payload)
        if recovery_status == "checking":
            return (
                "The previous run stopped before completion. Background supervision is "
                "continuing recovery; no routine action is needed."
            )
        if recovery_status == "blocked":
            recovery = payload.get("recovery_block")
            if isinstance(recovery, dict):
                reason = recovery.get("recovery_block_reason")
                if isinstance(reason, str) and reason.strip():
                    return _human_summary(_clean_summary(reason), "blocked")
            return "The same blocker repeated without progress and now needs a decision."
        recommended = _nested_string(
            payload, ("capabilities", "current_state", "recommended_action")
        )
        if recommended:
            return _human_summary(_clean_summary(recommended), fallback_status)
        for key in ("summary", "message", "status", "classification"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _human_summary(_clean_summary(value), fallback_status)
        active_goal = payload.get("active_goal")
        if isinstance(active_goal, dict):
            for key in ("objective", "summary", "status"):
                value = active_goal.get(key)
                if isinstance(value, str) and value.strip():
                    return _human_summary(_clean_summary(value), fallback_status)
    if stdout.strip():
        return _human_summary(_clean_summary(stdout), fallback_status)
    return {
        "ready": "No active work is visible yet.",
        "starting": "The work has been sent to the background worker.",
        "checking": "The background worker was asked to move the work forward.",
    }.get(fallback_status, "The work is moving.")


def _payload_is_accepted(payload: dict[str, Any]) -> bool:
    return _payload_reports_completion(payload) and _acceptance_receipt_is_valid(payload)


def _payload_reports_completion(payload: dict[str, Any]) -> bool:
    if _normalize_status(payload.get("classification")) == "done":
        return True
    if _normalize_status(payload.get("status")) == "done":
        return True
    active_goal = payload.get("active_goal")
    if isinstance(active_goal, dict):
        if active_goal.get("accepted") is True:
            return True
        if _normalize_status(active_goal.get("status")) == "done":
            return True
    current_state = payload.get("capabilities")
    if isinstance(current_state, dict):
        state = current_state.get("current_state")
        return isinstance(state, dict) and _normalize_status(state.get("classification")) == "done"
    return False


def _acceptance_receipt_is_valid(payload: dict[str, Any]) -> bool:
    receipt = payload.get("acceptance")
    if not isinstance(receipt, dict):
        return False
    active_goal = payload.get("active_goal")
    active_run_id = active_goal.get("id") if isinstance(active_goal, dict) else None
    run_id = receipt.get("run_id")
    digest = receipt.get("candidate_digest")
    validation = receipt.get("validation")
    verification = receipt.get("verification")
    if (
        receipt.get("schema") != "agentic_harness.acceptance_receipt.v1"
        or receipt.get("accepted") is not True
        or receipt.get("issuer") != "harness.acceptance"
        or not isinstance(active_run_id, str)
        or not active_run_id
        or run_id != active_run_id
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or not isinstance(validation, dict)
        or validation.get("level") != "harness_verified"
        or not isinstance(verification, list)
        or not verification
    ):
        return False
    return all(
        isinstance(row, dict)
        and isinstance(row.get("command"), str)
        and bool(row["command"].strip())
        and row.get("passed") is True
        and type(row.get("returncode")) is int
        and row.get("returncode") == 0
        for row in verification
    )


def _nested_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current.strip() if isinstance(current, str) else ""


def _changed_files_from_payload(payload: dict[str, Any] | None) -> list[str]:
    return _collect_strings(payload, ("changed_files", "files", "modified_files"))


def _verification_from_payload(payload: dict[str, Any] | None) -> list[str]:
    return _collect_strings(payload, ("verification", "checks", "commands"))


def _collect_strings(payload: dict[str, Any] | None, keys: tuple[str, ...]) -> list[str]:
    if not payload:
        return []
    values: list[str] = []
    for key in keys:
        item = payload.get(key)
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
        elif isinstance(item, list):
            values.extend(str(value).strip() for value in item if str(value).strip())
    return values[:12]


def _clean_summary(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return "No detail returned yet."
    summary = " ".join(lines[:4])
    return summary if len(summary) <= 280 else f"{summary[:277]}..."


def _human_summary(summary: str, status: str) -> str:
    normalized = summary.strip().lower().replace("-", "_").replace(" ", "_")
    status_only = normalized in {
        "ready",
        "starting",
        "working",
        "checking",
        "needs_review",
        "done",
        "blocked",
        "stopped",
    }
    if not status_only and not any(term in summary.lower() for term in _TECHNICAL_SUMMARY_TERMS):
        return summary
    return {
        "ready": "The assistant is ready for a new task.",
        "starting": "The task is starting.",
        "working": "The assistant is working on the task.",
        "checking": "The work is being checked.",
        "needs_review": (
            "The work is ready for review. Review it or ask it to continue before "
            "starting another task."
        ),
        "done": "The work is complete and ready for you.",
        "blocked": "The task needs attention. Open Advanced details for technical information.",
        "stopped": "The work has stopped.",
    }.get(status, "The task status was updated.")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def tasks_payload(bridge: LocalGoalBridge) -> dict[str, Any]:
    """Return all tasks and current task state."""
    current = status_task(bridge)
    return {
        "tasks": [current],
        "current": current,
    }
