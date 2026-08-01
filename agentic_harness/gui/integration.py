"""Small, read-only integration contracts for external operator clients.

The Agentic Harness remains the owner of execution, task state, and evidence.
This module only reports route eligibility by probing operator-configured health
endpoints; it does not start, stop, or reconfigure any model server.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.providers import ProviderProfile

INTEGRATION_API_VERSION = "1"
_ROUTE_SLOTS = (
    ("PRIMARY", "primary", "Primary route"),
    ("OVERFLOW", "overflow", "Overflow route"),
)


class _RejectRedirects(HTTPRedirectHandler):
    """Keep a validated health probe on the exact configured endpoint."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        raise URLError("health endpoint redirects are not allowed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _probe(url: str, *, timeout: float = 1.5) -> tuple[str, str]:
    """Return a public health state and bounded reason for a fixed endpoint."""

    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with build_opener(_RejectRedirects()).open(request, timeout=timeout) as response:
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


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower(), port


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
                    f"{prefix}RUNTIME",
                    "openai-compatible",
                ).strip()
                or "openai-compatible",
                "role": role,
                **values,
                "api_key_env": os.environ.get(f"{prefix}API_KEY_ENV", "").strip(),
                "capabilities": os.environ.get(
                    f"{prefix}CAPABILITIES",
                    "chat,tools",
                ).strip()
                or "chat,tools",
                "max_context_tokens": os.environ.get(
                    f"{prefix}MAX_CONTEXT_TOKENS",
                    "32768",
                ).strip()
                or "32768",
            }
        )
    return tuple(definitions)


def _validated_route_definition(route_id: str) -> dict[str, str]:
    definition = next(
        (row for row in _route_definitions() if row["id"] == route_id),
        None,
    )
    if definition is None:
        raise ValueError(f"unknown route id: {route_id!r}")
    try:
        profile = ProviderProfile(
            endpoint=definition["endpoint"],
            model=definition["model"],
            api_key_env=definition["api_key_env"],
        )
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if _origin(definition["health_url"]) != _origin(profile.endpoint):
        raise ValueError(
            f"route {route_id!r} health and execution endpoints must use the same origin"
        )
    try:
        max_context_tokens = int(definition["max_context_tokens"])
    except ValueError as exc:
        raise ValueError(f"route {route_id!r} max context must be an integer") from exc
    if max_context_tokens <= 0:
        raise ValueError(f"route {route_id!r} max context must be positive")
    return {**definition, "endpoint": profile.endpoint, "model": profile.model}


def route_registry_payload() -> dict[str, Any]:
    """Build the current, secret-free route registry from live probes."""

    generated_at = _now()
    routes = []
    for raw_definition in _route_definitions():
        try:
            definition = _validated_route_definition(raw_definition["id"])
            state, reason = _probe(definition["health_url"])
        except (ConfigError, ValueError) as exc:
            definition = raw_definition
            state, reason = "unavailable", f"route configuration rejected: {exc}"
        try:
            max_context_tokens = int(definition["max_context_tokens"])
        except ValueError:
            max_context_tokens = 0
        if max_context_tokens <= 0:
            max_context_tokens = 0
        routes.append(
            {
                "id": definition["id"],
                "model_id": definition["model"],
                "node": definition["node"],
                "runtime": definition["runtime"],
                "role": definition["role"],
                "status": state,
                "status_reason": reason,
                "capabilities": definition["capabilities"].split(","),
                "max_context_tokens": max_context_tokens,
                "eligible_for": ["chat", "worker"],
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


def select_route(
    registry: dict[str, Any],
    requested: str = "",
) -> tuple[dict[str, Any], str]:
    """Select a ready route without exposing transport credentials to clients."""

    routes = [row for row in registry.get("routes", []) if isinstance(row, dict)]
    normalized = requested.strip().lower()
    if normalized:
        selected = next(
            (
                row
                for row in routes
                if str(row.get("id", "")).lower() == normalized
                or str(row.get("model_id", "")).lower() == normalized
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"unknown route: {requested}")
        if selected.get("status") != "ready":
            raise RuntimeError(f"requested route is not ready: {selected.get('id', requested)}")
        return dict(selected), "operator requested this route"

    primary = next(
        (row for row in routes if row.get("role") == "primary" and row.get("status") == "ready"),
        None,
    )
    if primary is not None:
        return dict(primary), "primary route is ready"
    fallback = next((row for row in routes if row.get("status") == "ready"), None)
    if fallback is None:
        raise RuntimeError("no ready model route is available")
    return dict(fallback), "primary unavailable; selected the first ready overflow route"


def route_execution_config(route: dict[str, Any]) -> dict[str, str]:
    """Return private provider settings for a selected route.

    Fails closed: an unrecognized route id must not silently execute against
    any configured provider endpoint, even if client-supplied labels match.
    """

    route_id = str(route.get("id") or "")
    definition = _validated_route_definition(route_id)
    advertised_model = str(route.get("model_id") or "")
    if advertised_model and advertised_model != definition["model"]:
        raise ValueError(f"route {route_id!r} model identity changed after health verification")
    return {
        "endpoint": definition["endpoint"],
        "model": definition["model"],
        "api_key_env": definition["api_key_env"],
    }


def read_only_analysis_config(route: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded model-agent config for an isolated analysis workspace."""

    provider = route_execution_config(route)
    review = (
        "from pathlib import Path; "
        "unexpected = [str(p) for p in Path('.').rglob('*') "
        "if p.is_file() and '.agentic-harness' not in p.parts]; "
        "assert not unexpected, unexpected"
    )
    return {
        "version": 1,
        "worker": "model_agent",
        "llm": {
            "endpoint": provider["endpoint"],
            "model": provider["model"],
            "api_key_env": provider["api_key_env"],
            "remote_data_confirmed": False,
            "max_steps": 4,
            "max_output_tokens": 512,
            "disable_thinking": str(route.get("runtime") or "").lower()
            in {"vllm", "sglang"},
            "timeout": 120,
        },
        "llm_credential_source": "env",
        "llm_retries": 1,
        "llm_retry_delay": 1.0,
        "review_command": [sys.executable, "-c", review],
        # This check verifies isolation only. It does not judge whether the
        # model's prose is a correct or complete answer.
        "review_covers": ["R1"],
        "review_command_timeout": 30,
        "autonomy": {
            "max_cycles": 2,
            "max_elapsed_seconds": 180,
            "max_total_tokens": 4_000,
            "max_provider_calls": 4,
            "max_tool_calls": 8,
        },
    }
