"""Read-only compatibility contracts for external operator clients.

The GUI remains the owner of task state and execution.  These helpers expose
only the route facts an operator configured ahead of time; they never accept a
provider endpoint or execution choice from an HTTP request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
from typing import Any
from urllib.parse import urlparse

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.providers import ProviderProfile


INTEGRATION_API_VERSION = "1"
_MAX_HEALTH_BYTES = 64 * 1024
_ROUTE_SLOTS = (
    ("PRIMARY", "primary", "Primary route"),
    ("OVERFLOW", "overflow", "Overflow route"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or default_port,
    )


def _probe(url: str, *, timeout: float = 1.5) -> tuple[str, str]:
    """Probe one fixed, validated health URL without following redirects."""

    parsed = urlparse(url)
    host = parsed.hostname
    if not host or parsed.scheme not in {"http", "https"}:
        return "unavailable", "invalid health URL"
    connection: HTTPConnection
    if parsed.scheme == "https":
        connection = HTTPSConnection(host, parsed.port, timeout=timeout)
    else:
        connection = HTTPConnection(host, parsed.port, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection.request("GET", target, headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(_MAX_HEALTH_BYTES)
        if response.status < 200 or response.status >= 300:
            return "unavailable", f"health endpoint returned HTTP {response.status}"
    except (OSError, HTTPException, TimeoutError) as exc:
        return "unavailable", type(exc).__name__
    finally:
        connection.close()

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict) and payload.get("ready") is False:
        return "degraded", "runtime reported ready=false"
    return "ready", "health endpoint passed"


def _route_definitions() -> tuple[dict[str, str], ...]:
    definitions: list[dict[str, str]] = []
    for slot, role, default_label in _ROUTE_SLOTS:
        prefix = f"AGENTIC_HARNESS_ROUTE_{slot}_"
        values = {
            "endpoint": os.environ.get(f"{prefix}ENDPOINT", "").strip(),
            "health_url": os.environ.get(f"{prefix}HEALTH_URL", "").strip(),
            "model": os.environ.get(f"{prefix}MODEL_ID", "").strip(),
        }
        if not any(values.values()):
            continue
        definitions.append(
            {
                "id": role,
                "node": os.environ.get(f"{prefix}LABEL", default_label).strip()
                or default_label,
                "runtime": os.environ.get(
                    f"{prefix}RUNTIME", "openai-compatible"
                ).strip()
                or "openai-compatible",
                "role": role,
                **values,
                "api_key_env": os.environ.get(f"{prefix}API_KEY_ENV", "").strip(),
                "capabilities": os.environ.get(
                    f"{prefix}CAPABILITIES", "chat,tools"
                ).strip()
                or "chat,tools",
                "max_context_tokens": os.environ.get(
                    f"{prefix}MAX_CONTEXT_TOKENS", "32768"
                ).strip()
                or "32768",
            }
        )
    return tuple(definitions)


def _validated_route_definition(definition: dict[str, str]) -> dict[str, Any]:
    try:
        profile = ProviderProfile(
            endpoint=definition["endpoint"],
            model=definition["model"],
            api_key_env=definition["api_key_env"],
        )
    except ConfigError as exc:
        raise ValueError("provider settings are invalid") from exc

    health = urlparse(definition["health_url"])
    if (
        health.scheme not in {"http", "https"}
        or not health.hostname
        or health.username
        or health.password
        or health.query
        or health.fragment
    ):
        raise ValueError("health URL is invalid")
    if _origin(definition["health_url"]) != _origin(profile.endpoint):
        raise ValueError("health URL does not match the provider origin")
    try:
        max_context_tokens = int(definition["max_context_tokens"])
    except ValueError as exc:
        raise ValueError("maximum context is not an integer") from exc
    if max_context_tokens <= 0:
        raise ValueError("maximum context is not positive")

    return {
        **definition,
        "endpoint": profile.endpoint,
        "model": profile.model,
        "max_context_tokens": max_context_tokens,
    }


def route_registry_payload() -> dict[str, Any]:
    """Return the current secret-free route registry from bounded live probes."""

    generated_at = _now()
    routes: list[dict[str, Any]] = []
    for raw in _route_definitions():
        try:
            definition = _validated_route_definition(raw)
        except ValueError:
            routes.append(
                {
                    "id": raw["id"],
                    "model_id": raw["model"],
                    "node": raw["node"],
                    "runtime": raw["runtime"],
                    "role": raw["role"],
                    "status": "unavailable",
                    "status_reason": "route configuration rejected",
                    "capabilities": [],
                    "max_context_tokens": 0,
                    "eligible_for": [],
                    "last_verified": generated_at,
                }
            )
            continue
        state, reason = _probe(str(definition["health_url"]))
        routes.append(
            {
                "id": definition["id"],
                "model_id": definition["model"],
                "node": definition["node"],
                "runtime": definition["runtime"],
                "role": definition["role"],
                "status": state,
                "status_reason": reason,
                "capabilities": [
                    item.strip()
                    for item in str(definition["capabilities"]).split(",")
                    if item.strip()
                ],
                "max_context_tokens": definition["max_context_tokens"],
                "eligible_for": ["chat", "worker"] if state == "ready" else [],
                "last_verified": generated_at,
            }
        )
    return {
        "api_version": INTEGRATION_API_VERSION,
        "registry_version": 1,
        "owner": "agentic-harness",
        "generated_at": generated_at,
        "selection": {
            "policy": "harness_decides",
            "operator_hint": (
                "Operator clients may request an intent; Agentic Harness records "
                "the final route."
            ),
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
        "routes_status": "ready" if statuses and statuses == {"ready"} else "degraded",
        "route_count": len(registry["routes"]),
    }
