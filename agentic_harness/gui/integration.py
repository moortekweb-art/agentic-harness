"""Small, read-only integration contracts for external operator clients.

The Agentic Harness remains the owner of execution, task state, and evidence.
This module only reports route eligibility by probing fixed health endpoints; it
does not start, stop, or reconfigure any model server.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


INTEGRATION_API_VERSION = "1"
DEFAULT_NODE1_READY_URL = "http://127.0.0.1:8008/ready"
DEFAULT_NODE2_HEALTH_URL = "http://100.64.47.42:8009/health"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _probe(url: str, *, timeout: float = 1.5) -> tuple[str, str]:
    """Return a public health state and bounded reason for a fixed endpoint."""

    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(65_536)
            if response.status < 200 or response.status >= 300:
                return "unavailable", f"health endpoint returned HTTP {response.status}"
    except (OSError, URLError, TimeoutError) as exc:
        return "unavailable", type(exc).__name__

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict) and payload.get("ready") is False:
        return "degraded", "runtime reported ready=false"
    return "ready", "health endpoint passed"


def route_registry_payload() -> dict[str, Any]:
    """Build the current, secret-free route registry from live probes."""

    generated_at = _now()
    node1_state, node1_reason = _probe(
        os.environ.get("AGENTIC_HARNESS_NODE1_READY_URL", DEFAULT_NODE1_READY_URL)
    )
    node2_state, node2_reason = _probe(
        os.environ.get("AGENTIC_HARNESS_NODE2_HEALTH_URL", DEFAULT_NODE2_HEALTH_URL)
    )
    routes = [
        {
            "id": "local-node1-vllm",
            "model_id": os.environ.get("AGENTIC_HARNESS_NODE1_MODEL_ID", "local-qwen36-main"),
            "node": "node1",
            "runtime": "vllm",
            "role": "primary",
            "status": node1_state,
            "status_reason": node1_reason,
            "capabilities": ["chat", "tools", "vision"],
            "max_context_tokens": 230400,
            "eligible_for": ["chat", "worker"],
            "last_verified": generated_at,
        },
        {
            "id": "local-node2-overflow",
            "model_id": os.environ.get(
                "AGENTIC_HARNESS_NODE2_MODEL_ID", "local-node2-overflow"
            ),
            "node": "node2",
            "runtime": "vllm",
            "role": "overflow",
            "status": node2_state,
            "status_reason": node2_reason,
            "capabilities": ["chat", "tools"],
            "max_context_tokens": 32768,
            "eligible_for": ["chat", "worker"],
            "last_verified": generated_at,
        },
    ]
    return {
        "api_version": INTEGRATION_API_VERSION,
        "registry_version": 1,
        "owner": "agentic-harness",
        "generated_at": generated_at,
        "selection": {
            "policy": "harness_decides",
            "operator_hint": "Local Studio may request an intent; Harness records the final route.",
        },
        "routes": routes,
    }


def integration_health_payload() -> dict[str, Any]:
    registry = route_registry_payload()
    statuses = {str(route.get("status")) for route in registry["routes"]}
    return {
        "ok": True,
        "service": "agentic-harness",
        "integration_api_version": INTEGRATION_API_VERSION,
        "executor": "agentic-harness",
        "operator_clients": ["local-studio"],
        "task_ownership": {
            "state": "agentic-harness",
            "events": "agentic-harness",
            "artifacts": "agentic-harness",
        },
        "routes_status": "ready" if statuses == {"ready"} else "degraded",
        "route_count": len(registry["routes"]),
    }
