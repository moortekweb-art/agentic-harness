from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from agentic_harness.gui import integration


def _set_primary_route(
    monkeypatch: pytest.MonkeyPatch,
    *,
    endpoint: str = "http://127.0.0.1:8008/v1/chat/completions",
    health_url: str = "http://127.0.0.1:8008/health",
    max_context_tokens: str = "32768",
) -> None:
    values = {
        "ENDPOINT": endpoint,
        "HEALTH_URL": health_url,
        "MODEL_ID": "model-primary",
        "LABEL": "Local primary",
        "RUNTIME": "vllm",
        "API_KEY_ENV": "PRIVATE_MODEL_KEY",
        "CAPABILITIES": "chat,tools,vision",
        "MAX_CONTEXT_TOKENS": max_context_tokens,
    }
    for suffix, value in values.items():
        monkeypatch.setenv(f"AGENTIC_HARNESS_ROUTE_PRIMARY_{suffix}", value)


def test_route_registry_is_live_bounded_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_primary_route(monkeypatch)
    monkeypatch.setattr(
        integration,
        "_probe",
        lambda _url: ("ready", "health endpoint passed"),
    )

    payload = integration.route_registry_payload()

    assert payload["api_version"] == "1"
    assert payload["owner"] == "agentic-harness"
    assert payload["selection"]["policy"] == "harness_decides"
    assert payload["routes"] == [
        {
            "id": "primary",
            "model_id": "model-primary",
            "node": "Local primary",
            "runtime": "vllm",
            "role": "primary",
            "status": "ready",
            "status_reason": "health endpoint passed",
            "capabilities": ["chat", "tools", "vision"],
            "max_context_tokens": 32768,
            "eligible_for": ["chat", "worker"],
            "last_verified": payload["generated_at"],
        }
    ]
    serialized = json.dumps(payload)
    assert "127.0.0.1:8008" not in serialized
    assert "PRIVATE_MODEL_KEY" not in serialized
    assert "endpoint" not in payload["routes"][0]
    assert "api_key_env" not in payload["routes"][0]


def test_invalid_route_configuration_is_reported_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_endpoint = "https://secret-route.example/v1/chat/completions"
    _set_primary_route(
        monkeypatch,
        endpoint=secret_endpoint,
        health_url="https://different.example/health",
        max_context_tokens="not-a-number",
    )

    payload = integration.route_registry_payload()
    route = payload["routes"][0]

    assert route["status"] == "unavailable"
    assert route["status_reason"] == "route configuration rejected"
    assert route["max_context_tokens"] == 0
    assert route["eligible_for"] == []
    assert secret_endpoint not in json.dumps(payload)


def test_health_probe_does_not_follow_redirects() -> None:
    redirected_hits = 0

    class RedirectTarget(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal redirected_hits
            redirected_hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ready":true}')

        def log_message(self, _format: str, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTarget)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()

    class RedirectSource(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{target.server_port}/ready",
            )
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectSource)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        state, reason = integration._probe(
            f"http://127.0.0.1:{source.server_port}/health"
        )
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=2)
        target.shutdown()
        target.server_close()
        target_thread.join(timeout=2)

    assert state == "unavailable"
    assert reason == "health endpoint returned HTTP 302"
    assert redirected_hits == 0


def test_integration_health_is_degraded_without_configured_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for slot in ("PRIMARY", "OVERFLOW"):
        for suffix in ("ENDPOINT", "HEALTH_URL", "MODEL_ID"):
            monkeypatch.delenv(
                f"AGENTIC_HARNESS_ROUTE_{slot}_{suffix}",
                raising=False,
            )

    payload = integration.integration_health_payload()

    assert payload["ok"] is True
    assert payload["route_count"] == 0
    assert payload["routes_status"] == "degraded"
    assert payload["task_ownership"] == {
        "state": "agentic-harness",
        "events": "agentic-harness",
        "artifacts": "agentic-harness",
    }
