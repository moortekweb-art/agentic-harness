"""Small local HTTP server for the Agentic Harness GUI."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
import base64
import hmac
import hashlib
import json
import mimetypes
import os
import secrets
import stat
from threading import Lock, RLock
import time
import webbrowser

from agentic_harness.core.local_goal_bridge import LocalGoalBridge, resolve_doc_root
from agentic_harness.core.errors import HarnessError
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.strategies import (
    DEFAULT_PUBLIC_STRATEGY,
    PUBLIC_STRATEGIES,
)
from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.gui.api import (
    command_task,
    details_payload,
    execution_efforts_payload,
    execution_profiles_payload,
    health_payload,
    modes_payload,
    readiness_payload,
    setup_payload,
    start_task,
    status_task,
    tasks_payload,
    watch_task,
)


MAX_REQUEST_BYTES = 1_048_576
STREAM_MONITOR_INTERVAL_SECONDS = 8.0
GUI_SESSION_PATH_ENV = "AGENTIC_HARNESS_GUI_SESSION_PATH"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _portable_modes() -> list[dict[str, Any]]:
    return [strategy.to_public_dict() for strategy in PUBLIC_STRATEGIES]


def serve_gui(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    doc_root: str | Path | None = None,
    project_dir: str | Path = ".",
    backend: str = "embedded",
    open_browser: bool = True,
    allow_port_fallback: bool = False,
) -> None:
    if not _is_loopback_host(host) and not os.environ.get("AGENTIC_HARNESS_GUI_TOKEN", "").strip():
        raise GuiSecurityError(
            "AGENTIC_HARNESS_GUI_TOKEN is required before binding the GUI beyond loopback"
        )
    if backend == "local-goal":
        service: LocalGoalBridge | EmbeddedExecutionBackend = LocalGoalBridge(
            doc_root=resolve_doc_root(doc_root)
        )
    else:
        service = EmbeddedExecutionBackend(Path(project_dir))
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    normalized_host = host.strip().strip("[]").lower()
    if normalized_host not in {"0.0.0.0", "::", ""}:
        allowed_hosts.add(normalized_host)
    allowed_hosts.update(
        item.strip().strip("[]").lower()
        for item in os.environ.get("AGENTIC_HARNESS_GUI_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    )
    handler = make_handler(
        service,
        allowed_hosts=allowed_hosts,
        project_dir=project_dir,
    )
    server = create_gui_server(host, port, handler, allow_port_fallback=allow_port_fallback)
    actual_port = int(server.server_address[1])
    url = f"http://{host}:{actual_port}/"
    print(f"Agentic Harness GUI: {url}")
    if not _is_loopback_host(host):
        _print_non_loopback_warning()
    if open_browser:
        _open_browser_safely(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.server_close()


class GuiPortUnavailable(RuntimeError):
    """Raised when the GUI cannot bind to the requested port."""


class GuiSecurityError(RuntimeError):
    """Raised when GUI network exposure is missing a required guardrail."""


def create_gui_server(
    host: str,
    port: int,
    handler: type[BaseHTTPRequestHandler],
    *,
    allow_port_fallback: bool = False,
) -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        if not allow_port_fallback:
            raise GuiPortUnavailable(
                f"Agentic Harness GUI port {host}:{port} is already in use. "
                "Choose another --port, or omit --port to auto-select a free one."
            ) from exc
        for fallback_port in range(port + 1, min(port + 50, 65535) + 1):
            try:
                server = ThreadingHTTPServer((host, fallback_port), handler)
            except OSError:
                continue
            print(
                f"Agentic Harness GUI port {host}:{port} is already in use; "
                f"using {host}:{fallback_port} instead."
            )
            return server
        raise GuiPortUnavailable(
            f"Agentic Harness GUI port {host}:{port} is already in use, and no fallback "
            f"port was available in {port + 1}-{min(port + 50, 65535)}."
        ) from exc


def make_handler(
    service: Any,
    *,
    allowed_hosts: set[str] | None = None,
    project_dir: str | Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    embedded_service = service if isinstance(service, EmbeddedExecutionBackend) else None
    embedded = embedded_service is not None
    bridge = cast(LocalGoalBridge, service)
    session = GuiSession(state_path=None if embedded else _managed_session_path(project_dir))
    managed_demo_workspace: TemporaryDirectory[str] | None = None
    demo_service: EmbeddedExecutionBackend
    if embedded_service is not None:
        demo_service = embedded_service
    else:
        managed_demo_workspace = TemporaryDirectory(prefix="agentic-harness-managed-demo-host-")
        demo_service = EmbeddedExecutionBackend(managed_demo_workspace.name)
    auth_token = os.environ.get("AGENTIC_HARNESS_GUI_TOKEN", "").strip()
    rate_limiter = RateLimiter(limit=240, window_seconds=60)
    trusted_hosts = allowed_hosts or {"127.0.0.1", "localhost", "::1"}

    def demo_task() -> dict[str, Any] | None:
        return demo_service.demo_status()

    def active_embedded_service() -> EmbeddedExecutionBackend | None:
        if embedded_service is not None:
            return embedded_service
        return demo_service if demo_task() is not None else None

    def public_setup() -> dict[str, Any]:
        if embedded_service is not None:
            return embedded_service.setup()
        payload = setup_payload(bridge)
        demo = dict(demo_service.setup()["demo"])
        demo.update(
            {
                "managed_overlay": True,
                "summary": (
                    "Runs the real harness in a temporary practice project without changing "
                    "the connected managed workspace or its current task."
                ),
            }
        )
        payload["demo"] = demo
        return payload

    def public_health() -> dict[str, Any]:
        if embedded_service is not None:
            return embedded_service.health()
        payload = health_payload(bridge)
        payload["gui_session"] = session.persistence_status()
        if demo_task() is not None:
            payload["readiness"] = demo_service.readiness()
            payload["demo_overlay_active"] = True
        return payload

    def public_readiness() -> dict[str, Any]:
        if not embedded and demo_task() is not None:
            return demo_service.readiness()
        return (
            embedded_service.readiness()
            if embedded_service is not None
            else readiness_payload(bridge)
        )

    class GuiHandler(BaseHTTPRequestHandler):
        server_version = "AgenticHarnessGUI/0.1"
        _managed_demo_workspace = managed_demo_workspace

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            if route.startswith("/api/") and not self._trusted_host():
                return
            if route == "/api/tasks/stream":
                if not self._same_origin():
                    return
                if not self._allowed(parsed.query):
                    return
                self._websocket_status()
                return
            if route.startswith("/api/") and not self._allowed(parsed.query):
                return
            active = active_embedded_service()
            if route in {"/api/health", "/api/status"}:
                self._json(public_health())
            elif route == "/api/modes":
                if embedded:
                    self._json(
                        {
                            "modes": _portable_modes(),
                            "default": DEFAULT_PUBLIC_STRATEGY,
                            "kind": "strategy",
                        }
                    )
                else:
                    routes = modes_payload(bridge)
                    self._json(
                        {
                            # ``modes`` remains as a compatibility alias for
                            # older managed clients. New clients keep routes,
                            # effort, and model profiles as separate choices.
                            "modes": routes,
                            "routes": routes,
                            "default": "mode1",
                            "default_route": "mode1",
                            "efforts": execution_efforts_payload(),
                            "default_effort": "standard",
                            "execution_profiles": (profiles := execution_profiles_payload(bridge)),
                            "default_execution_profile": (
                                "qwen-primary" if profiles else "automatic"
                            ),
                            "kind": "managed_route",
                        }
                    )
            elif route == "/api/readiness":
                self._json(public_readiness())
            elif route == "/api/setup":
                self._json(public_setup())
            elif route == "/api/setup/local-models":
                if embedded:
                    self._json(service.detect_local_models())
                else:
                    self._json(
                        {
                            "ok": False,
                            "error": "Local model detection is available only in a self-hosted workspace.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks":
                if active is not None:
                    current = active.status()
                    self._json({"current": current, "tasks": active.history()})
                else:
                    payload = tasks_payload(bridge)
                    payload["current"] = session.record(payload["current"])
                    payload["tasks"] = session.history() or payload["tasks"]
                    self._json(payload)
            elif route == "/api/tasks/current":
                if active is not None:
                    self._json(active.status())
                else:
                    task = session.record(status_task(bridge))
                    self._json(task)
            elif route == "/api/tasks/history":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self._json(
                    {"tasks": active.history(query=query)}
                    if active is not None
                    else {"tasks": session.history(query=query)}
                )
            elif route == "/api/tasks/current/events":
                after_raw = parse_qs(parsed.query).get("after", ["0"])[0]
                try:
                    after = max(0, int(after_raw))
                except ValueError:
                    after = 0
                self._json({"events": active.events(after=after) if active is not None else []})
            elif route == "/api/tasks/current/file" and active is not None:
                path = parse_qs(parsed.query).get("path", [""])[0]
                goal_id = parse_qs(parsed.query).get("goal_id", [""])[0]
                try:
                    self._json(active.preview_file(path, goal_id=goal_id))
                except (ValueError, OSError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/current/artifact" and active is not None:
                path = parse_qs(parsed.query).get("path", [""])[0]
                goal_id = parse_qs(parsed.query).get("goal_id", [""])[0]
                try:
                    self._json(active.preview_artifact(path, goal_id=goal_id))
                except (ValueError, OSError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/current/details":
                self._json(
                    {"task": active.status(), "raw": {}}
                    if active is not None
                    else details_payload(bridge)
                )
            elif route == "/api/session":
                self._json(
                    {"version": 2, "tasks": active.history()}
                    if active is not None
                    else session.export()
                )
            elif route.startswith("/api/"):
                self._json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
            else:
                self._static(route)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path
            if route.startswith("/api/") and not self._trusted_host():
                return
            if not self._same_origin():
                return
            if route.startswith("/api/") and not self._allowed(parsed.query):
                return
            body = self._read_json()
            if body is None:
                return
            active = active_embedded_service()
            if route == "/api/demo":
                try:
                    self._json(demo_service.start_demo())
                except (ValueError, OSError, HarnessError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/demo/dismiss":
                try:
                    demo_service.clear_demo()
                    task = service.status() if embedded else session.record(status_task(bridge))
                    self._json(task)
                except (ValueError, OSError, HarnessError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks":
                if embedded:
                    self._json(service.start(body))
                else:
                    if active is not None:
                        try:
                            demo_service.clear_demo()
                        except ValueError as exc:
                            self._json(
                                {"ok": False, "error": str(exc)},
                                status=HTTPStatus.BAD_REQUEST,
                            )
                            return
                    task = session.enrich(start_task(bridge, body), body)
                    session.record(task)
                    self._json(task)
            elif route == "/api/setup" and embedded:
                if body.get("api_key") and not self._client_is_loopback():
                    self._json(
                        {
                            "ok": False,
                            "error": "Session API keys may be entered only from a loopback or local reverse-proxy connection; use an environment-variable reference instead.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    self._json(service.configure(body))
                except (ValueError, OSError, HarnessError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/setup/credential" and embedded:
                if not self._client_is_loopback():
                    self._json(
                        {
                            "ok": False,
                            "error": "Session API keys may be entered only from a loopback or local reverse-proxy connection.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    credential = service.set_session_credential(str(body.get("api_key") or ""))
                    self._json({"ok": True, "credential": credential})
                except (ValueError, OSError, HarnessError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/setup/test" and embedded:
                if body.get("api_key") and not self._client_is_loopback():
                    self._json(
                        {
                            "ok": False,
                            "error": "Session API keys may be tested only from a loopback connection; use an environment-variable reference instead.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    self._json(service.test_connection(body))
                except (ValueError, OSError, RuntimeError, HarnessError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/bulk":
                if active is not None:
                    self._json(
                        {
                            "ok": False,
                            "error": "The embedded backend runs one visible goal at a time.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                else:
                    tasks = []
                    for item in body.get("tasks", []):
                        if isinstance(item, dict):
                            task = session.enrich(start_task(bridge, item), item)
                            session.record(task)
                            tasks.append(task)
                    self._json({"tasks": tasks})
            elif route == "/api/tasks/current/watch":
                if active is not None:
                    self._json(active.status())
                else:
                    task = watch_task(bridge)
                    task = session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/accept":
                if active is not None:
                    self._json(active.accept())
                else:
                    task = command_task(bridge, "accept", body)
                    task = session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/continue":
                if active is not None:
                    self._json(active.continue_task(str(body.get("feedback") or "")))
                else:
                    task = command_task(bridge, "continue", body)
                    task = session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/stop":
                if active is not None:
                    self._json(active.stop())
                else:
                    task = command_task(bridge, "stop", body)
                    task = session.record(task)
                    self._json(task)
            elif route == "/api/session/import":
                if active is not None:
                    self._json(
                        {
                            "ok": False,
                            "error": "Durable engine history cannot be replaced by a browser import.",
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                else:
                    session.import_payload(body)
                    self._json({"ok": True, "tasks": session.history()})
            else:
                self._json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any] | None:
            content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
            if content_type != "application/json":
                self._json(
                    {"ok": False, "error": "application/json required"},
                    status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._json(
                    {"ok": False, "error": "valid Content-Length required"},
                    status=HTTPStatus.LENGTH_REQUIRED,
                )
                return None
            if length < 0:
                self._json(
                    {"ok": False, "error": "valid Content-Length required"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return None
            if length > MAX_REQUEST_BYTES:
                self._json(
                    {"ok": False, "error": "request body too large"},
                    status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
                return None
            if length == 0:
                return {}
            try:
                raw = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError:
                self._json(
                    {"ok": False, "error": "request body must be UTF-8 JSON"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return None
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                self._json(
                    {"ok": False, "error": "request body must be valid JSON"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return None
            if not isinstance(value, dict):
                self._json(
                    {"ok": False, "error": "request body must be a JSON object"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return None
            return value

        def _json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = redact_secrets(json.dumps(payload, indent=2, sort_keys=True)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                # Mobile browsers may sleep or switch networks while a start
                # command is still returning.  The task state is durable and
                # the next status refresh will reconnect to it.
                return

        def _allowed(self, query: str) -> bool:
            client = self.client_address[0] if self.client_address else "local"
            if not rate_limiter.allowed(client):
                self._json(
                    {"ok": False, "error": "rate limit exceeded"},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                )
                return False
            if not auth_token:
                return True
            supplied = self.headers.get("Authorization", "")
            bearer = (
                supplied.removeprefix("Bearer ").strip() if supplied.startswith("Bearer ") else ""
            )
            if _token_matches(bearer, auth_token):
                return True
            self._json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return False

        def _trusted_host(self) -> bool:
            raw = self.headers.get("Host", "").strip()
            if not raw:
                self._json(
                    {"ok": False, "error": "untrusted host"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False
            hostname = urlparse("//" + raw).hostname
            if hostname and hostname.strip("[]").lower() in trusted_hosts:
                return True
            self._json(
                {"ok": False, "error": "untrusted host"},
                status=HTTPStatus.FORBIDDEN,
            )
            return False

        def _client_is_loopback(self) -> bool:
            if not self.client_address:
                return False
            client = str(self.client_address[0]).strip().lower()
            return client in {"127.0.0.1", "::1", "localhost"}

        def _security_headers(self) -> None:
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)

        def _same_origin(self) -> bool:
            fetch_site = self.headers.get("Sec-Fetch-Site", "").strip().lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                self._json(
                    {"ok": False, "error": "cross-origin request rejected"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False
            origin = self.headers.get("Origin", "").strip()
            if not origin:
                return True
            parsed = urlparse(origin)
            host = self.headers.get("Host", "").strip().lower()
            if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host:
                return True
            self._json(
                {"ok": False, "error": "cross-origin request rejected"},
                status=HTTPStatus.FORBIDDEN,
            )
            return False

        def _websocket_status(self) -> None:
            if self.headers.get("Upgrade", "").lower() != "websocket":
                self._json(
                    {"ok": False, "error": "websocket upgrade required"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            key = self.headers.get("Sec-WebSocket-Key", "").strip()
            if not key:
                self._json(
                    {"ok": False, "error": "missing websocket key"}, status=HTTPStatus.BAD_REQUEST
                )
                return
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                ).digest()
            ).decode("ascii")
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            next_monitor_at = time.monotonic() + STREAM_MONITOR_INTERVAL_SECONDS
            while True:
                try:
                    active = active_embedded_service()
                    if active is not None:
                        task = active.status()
                    elif time.monotonic() >= next_monitor_at:
                        task = watch_task(bridge)
                        next_monitor_at = time.monotonic() + STREAM_MONITOR_INTERVAL_SECONDS
                    else:
                        task = status_task(bridge)
                    if active is None and not embedded:
                        task = session.record(task)
                    message = redact_secrets(json.dumps(task, sort_keys=True))
                    self.wfile.write(_websocket_text_frame(message))
                    self.wfile.flush()
                    time.sleep(2)
                except OSError:
                    return

        def _static(self, route: str) -> None:
            relative = "index.html" if route in {"", "/"} else route.removeprefix("/")
            if relative.startswith("static/"):
                relative = relative.removeprefix("static/")
            parts = relative.split("/")
            nested_asset = len(parts) == 2 and parts[0] == "illustrations"
            if (
                not all(parts)
                or any(part.startswith(".") or "\\" in part for part in parts)
                or (len(parts) > 1 and not nested_asset)
            ):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            static_root = files("agentic_harness.gui.static")
            try:
                resource = static_root
                for part in parts:
                    resource = resource.joinpath(part)
                data = resource.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime = (
                "image/webp"
                if relative.lower().endswith(".webp")
                else mimetypes.guess_type(relative)[0] or "application/octet-stream"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

    return GuiHandler


GUI_SESSION_CONTRACT = "agentic_harness.gui_session.v1"
MAX_GUI_SESSION_BYTES = 4 * 1024 * 1024
MAX_GUI_HISTORY = 100
_STARTED_STATUSES = frozenset({"starting", "working", "checking", "needs_review", "done"})
_SENSITIVE_JSON_KEYS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "githubpat",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "refreshtoken",
        "secret",
        "setcookie",
        "token",
    }
)
_GUI_METADATA_FIELDS = frozenset(
    {
        "checks",
        "effort",
        "execution_expectation",
        "execution_profile",
        "managed_route",
        "mode",
        "priority",
        "route_key",
        "safe_areas",
        "start_accepted",
        "updated_at",
    }
)
_GUI_SNAPSHOT_FIELDS = frozenset(
    {
        "agent_loop",
        "allowed_actions",
        "artifacts",
        "changed_files",
        "current",
        "events",
        "final_result",
        "human_title",
        "id",
        "metadata",
        "needs_human",
        "objective",
        "plan",
        "progress",
        "readiness_gate",
        "requirements",
        "result_category",
        "status",
        "status_label",
        "summary",
        "verification",
    }
)


class GuiSession:
    """Keep GUI-owned labels attached to the exact durable managed run."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._history: list[dict[str, Any]] = []
        self._next_id = 1
        self._active_objective = ""
        self._active_metadata: dict[str, Any] = {}
        self._active_identity = _empty_identity()
        self._state_path = Path(state_path).expanduser() if state_path else None
        self._lock = RLock()
        self._last_serialized = ""
        self._persistence_warning = ""
        self._active_durability_warning = ""
        self._load()

    def enrich(self, task: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            task = dict(task)
            objective = str(source.get("objective", "")).strip()
            if objective:
                task["objective"] = objective
                task["human_title"] = objective[:80]
                if str(task.get("status", "")) not in {"blocked", "needs_review"}:
                    task["summary"] = objective
            metadata = dict(task.get("metadata", {}))
            start_accepted = _start_was_accepted(task)
            metadata["start_accepted"] = start_accepted
            for key in ("mode", "priority", "effort", "execution_profile"):
                value = str(source.get(key, "")).strip()
                if value:
                    metadata[key] = value
            route = str(source.get("route", "")).strip()
            if route:
                metadata["route_key"] = route
            metadata["safe_areas"] = (
                [str(item) for item in source.get("safe_areas", [])]
                if isinstance(source.get("safe_areas"), list)
                else []
            )
            metadata["checks"] = (
                [str(item) for item in source.get("checks", [])]
                if isinstance(source.get("checks"), list)
                else []
            )
            if start_accepted:
                self._active_objective = objective
                self._active_metadata = _safe_metadata(metadata)
                identity = _task_identity(task)
                self._active_identity = identity
                if not _has_identity(identity) and self._state_path is not None:
                    self._active_durability_warning = (
                        "The task started without a durable run id. Its labels will remain "
                        "available in this process but cannot be safely restored after a restart."
                    )
                    metadata["persistence_warning"] = self._active_durability_warning
                else:
                    self._active_durability_warning = ""
            task["metadata"] = metadata
            return task

    def _reconcile_active_objective(self, task: dict[str, Any]) -> dict[str, Any]:
        task = dict(task)
        identity = _task_identity(task)
        if str(task.get("status", "")) == "ready" and not _has_identity(identity):
            self._clear_active()
            return task
        if not self._active_objective and not self._active_metadata:
            return task
        if _has_identity(self._active_identity):
            if not _has_identity(identity):
                return task
            if not _identities_match(self._active_identity, identity):
                self._clear_active()
                return task
        elif _has_identity(identity):
            # This binding is permitted only for an identityless start still
            # held in this process. Identityless active state is never loaded
            # from disk, so a restart cannot attach it to unrelated work.
            self._active_identity = identity
            self._active_durability_warning = ""
        if self._active_objective:
            task["objective"] = self._active_objective
            task["human_title"] = self._active_objective[:80]
            requirements = task.get("requirements")
            if isinstance(requirements, list):
                task["requirements"] = [
                    {
                        **requirement,
                        "text": f"Requested outcome: {self._active_objective}",
                    }
                    if isinstance(requirement, dict)
                    and str(requirement.get("text", "")).startswith("Requested outcome:")
                    else requirement
                    for requirement in requirements
                ]
        return self._reconcile_active_metadata(task)

    def _reconcile_active_metadata(self, task: dict[str, Any]) -> dict[str, Any]:
        if not self._active_metadata:
            return task
        metadata = dict(task.get("metadata", {}))
        for key, value in self._active_metadata.items():
            metadata.setdefault(key, value)
        task["metadata"] = metadata
        return task

    def _clear_active(self) -> None:
        self._active_objective = ""
        self._active_metadata = {}
        self._active_identity = _empty_identity()
        self._active_durability_warning = ""

    def record(self, task: dict[str, Any]) -> dict[str, Any]:
        if not task:
            return task
        with self._lock:
            task = self._reconcile_active_objective(task)
            entry = dict(task)
            identity = _task_identity(entry)
            if not entry.get("id"):
                entry["id"] = f"task-{self._next_id}"
                self._next_id += 1
            if _has_identity(identity):
                self._history = [
                    item
                    for item in self._history
                    if not _identities_match(identity, _task_identity(item))
                ]
            else:
                self._history = [
                    item
                    for item in self._history
                    if _has_identity(_task_identity(item))
                    or item.get("summary") != entry.get("summary")
                ]
            self._history.insert(0, entry)
            self._history = self._history[:MAX_GUI_HISTORY]
            self._save_locked()
            return task

    def history(self, *, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._history)
        needle = query.strip().lower()
        if needle:
            tasks = [task for task in tasks if needle in json.dumps(task, sort_keys=True).lower()]
        return tasks

    def export(self) -> dict[str, Any]:
        return {
            "version": 1,
            "tasks": [_safe_task_snapshot(task) for task in self.history()],
            "persistence": self.persistence_status(),
        }

    def import_payload(self, payload: dict[str, Any]) -> None:
        tasks = payload.get("tasks", [])
        if not isinstance(tasks, list):
            return
        with self._lock:
            imported: list[dict[str, Any]] = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                safe = _safe_task_snapshot(task)
                if _identities_match(self._active_identity, _task_identity(safe)):
                    continue
                imported.append(safe)
            self._history = imported[:MAX_GUI_HISTORY]
            self._next_id = len(self._history) + 1
            self._save_locked()

    def persistence_status(self) -> dict[str, Any]:
        if self._state_path is None:
            return {"enabled": False, "status": "disabled", "warning": ""}
        warnings = [
            warning
            for warning in (self._persistence_warning, self._active_durability_warning)
            if warning
        ]
        return {
            "enabled": True,
            "status": "degraded" if warnings else "ready",
            "warning": " ".join(warnings),
        }

    def _state_payload(self) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        for task in self._history[:MAX_GUI_HISTORY]:
            snapshot = _safe_task_snapshot(task)
            identity = _task_identity(task)
            records.append(
                {
                    "identity": identity,
                    "objective": str(snapshot.get("objective") or ""),
                    "metadata": _safe_metadata(snapshot.get("metadata")),
                    "last_public_snapshot": snapshot,
                }
            )
        if _has_identity(self._active_identity):
            active_snapshot: dict[str, Any] = {}
            remaining: list[dict[str, Any]] = []
            for record in records:
                if not active_snapshot and _identities_match(
                    self._active_identity, _identity_from_value(record.get("identity"))
                ):
                    active_snapshot = dict(record.get("last_public_snapshot") or {})
                    continue
                remaining.append(record)
            active_snapshot.update(
                {
                    "id": self._active_identity.get("run_id", ""),
                    "objective": self._active_objective,
                    "human_title": self._active_objective[:80],
                    "metadata": self._active_metadata,
                }
            )
            records = [
                {
                    "identity": self._active_identity,
                    "objective": self._active_objective,
                    "metadata": self._active_metadata,
                    "last_public_snapshot": _safe_task_snapshot(active_snapshot),
                },
                *remaining,
            ]
        return {
            "contract": GUI_SESSION_CONTRACT,
            "active_identity": (
                self._active_identity if _has_identity(self._active_identity) else None
            ),
            "records": records[:MAX_GUI_HISTORY],
        }

    def _load(self) -> None:
        if self._state_path is None:
            return
        try:
            raw = _read_session_state(self._state_path)
            if raw is None:
                return
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("contract") != GUI_SESSION_CONTRACT:
                raise ValueError("GUI session state has an unsupported contract")
            records = payload.get("records")
            if not isinstance(records, list):
                raise ValueError("GUI session records are invalid")
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ) as exc:
            self._persistence_warning = f"GUI session state was not loaded: {exc}"
            return
        self._history = []
        normalized_records: list[dict[str, Any]] = []
        for record in records[:MAX_GUI_HISTORY]:
            if not isinstance(record, dict):
                continue
            identity = _identity_from_value(record.get("identity"))
            snapshot = record.get("last_public_snapshot")
            if not isinstance(snapshot, dict):
                continue
            safe_snapshot = _safe_task_snapshot(snapshot)
            self._history.append(safe_snapshot)
            normalized_records.append(
                {
                    "identity": identity,
                    "objective": str(record.get("objective") or ""),
                    "metadata": _safe_metadata(record.get("metadata")),
                    "last_public_snapshot": safe_snapshot,
                }
            )
        active_identity = _identity_from_value(payload.get("active_identity"))
        if _has_identity(active_identity):
            for record in normalized_records:
                if _identities_match(active_identity, record["identity"]):
                    self._active_identity = active_identity
                    self._active_objective = str(record.get("objective") or "")
                    self._active_metadata = _safe_metadata(record.get("metadata"))
                    break
        self._next_id = len(self._history) + 1
        self._last_serialized = json.dumps(
            self._state_payload(), sort_keys=True, separators=(",", ":")
        )

    def _save_locked(self) -> None:
        if self._state_path is None:
            return
        payload = self._state_payload()
        serialized = _redacted_json(payload)
        while (
            len(serialized.encode("utf-8")) > MAX_GUI_SESSION_BYTES and len(payload["records"]) > 1
        ):
            payload["records"].pop()
            serialized = _redacted_json(payload)
        if len(serialized.encode("utf-8")) > MAX_GUI_SESSION_BYTES:
            self._persistence_warning = (
                "GUI session persistence is degraded: the active snapshot exceeds the "
                "session size limit."
            )
            return
        if serialized == self._last_serialized:
            return
        try:
            _write_session_state(self._state_path, serialized)
        except OSError as exc:
            self._persistence_warning = f"GUI session persistence is degraded: {exc}"
            return
        self._persistence_warning = ""
        self._last_serialized = serialized


def _managed_session_path(project_dir: str | Path | None) -> Path | None:
    configured = os.environ.get(GUI_SESSION_PATH_ENV)
    if configured is not None:
        value = configured.strip()
        if value.lower() in {"", "0", "false", "off", "none"}:
            return None
        return Path(value).expanduser()
    if project_dir is None:
        return None
    return Path(project_dir).expanduser().resolve() / ".agentic-harness" / "gui-session.v1.json"


def _read_session_state(path: Path) -> str | None:
    """Read one regular state file through a descriptor that never follows its leaf."""

    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    if _path_is_link_like(path, before):
        raise OSError("GUI session state is a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise OSError("GUI session state is not a regular file")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
            )
        except FileNotFoundError:
            return None
        observed = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            raise OSError("GUI session state changed while it was being opened")
        if not stat.S_ISREG(observed.st_mode):
            raise OSError("GUI session state is not a regular file")
        if observed.st_size > MAX_GUI_SESSION_BYTES:
            raise ValueError("GUI session state exceeds the size limit")
        if os.name == "posix" and stat.S_IMODE(observed.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(MAX_GUI_SESSION_BYTES + 1)
        if len(raw) > MAX_GUI_SESSION_BYTES:
            raise ValueError("GUI session state exceeds the size limit")
        return raw.decode("utf-8")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_session_state(path: Path, serialized: str) -> None:
    """Atomically publish state without following a caller-controlled state-file link."""

    if not _directory_fd_state_io_supported():
        _write_session_state_portable(path, serialized)
        return

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    directory_fd = _open_state_directory(parent)
    temporary_name = ""
    temporary_fd = -1
    try:
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                raise OSError("GUI session state path is a symlink")
            if not stat.S_ISREG(existing.st_mode):
                raise OSError("GUI session state path is not a regular file")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        for _ in range(16):
            temporary_name = f".{path.name}.{secrets.token_hex(8)}"
            try:
                temporary_fd = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
        else:
            raise OSError("could not allocate a unique GUI session state file")

        if hasattr(os, "fchmod"):
            os.fchmod(temporary_fd, 0o600)
        data = f"{serialized}\n".encode("utf-8")
        with os.fdopen(temporary_fd, "wb") as handle:
            temporary_fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = ""
        os.fsync(directory_fd)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _directory_fd_state_io_supported() -> bool:
    required = (os.open, os.rename, os.stat, os.unlink)
    return bool(
        os.name == "posix"
        and getattr(os, "O_DIRECTORY", 0)
        and getattr(os, "O_NOFOLLOW", 0)
        and all(function in os.supports_dir_fd for function in required)
    )


def _write_session_state_portable(path: Path, serialized: str) -> None:
    """Identity-checking fallback for platforms without descriptor-relative rename."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent_before = os.lstat(parent)
    if _path_is_link_like(parent, parent_before) or not stat.S_ISDIR(parent_before.st_mode):
        raise OSError("GUI session state parent is not a regular directory")
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if _path_is_link_like(path, existing):
            raise OSError("GUI session state path is a symlink")
        if not stat.S_ISREG(existing.st_mode):
            raise OSError("GUI session state path is not a regular file")

    temporary_path: Path | None = None
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        for _ in range(16):
            candidate = parent / f".{path.name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(candidate, flags, 0o600)
                temporary_path = candidate
                break
            except FileExistsError:
                continue
        else:
            raise OSError("could not allocate a unique GUI session state file")

        opened = os.fstat(descriptor)
        linked = os.lstat(temporary_path)
        parent_after_open = os.lstat(parent)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            linked.st_dev,
            linked.st_ino,
        ):
            raise OSError("GUI session temporary state changed while it was being opened")
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_after_open.st_dev,
            parent_after_open.st_ino,
        ) or _path_is_link_like(parent, parent_after_open):
            raise OSError("GUI session state directory changed while it was being opened")
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        data = f"{serialized}\n".encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        parent_before_publish = os.lstat(parent)
        temporary_before_publish = os.lstat(temporary_path)
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_before_publish.st_dev,
            parent_before_publish.st_ino,
        ) or _path_is_link_like(parent, parent_before_publish):
            raise OSError("GUI session state directory changed before publish")
        if (opened.st_dev, opened.st_ino) != (
            temporary_before_publish.st_dev,
            temporary_before_publish.st_ino,
        ) or _path_is_link_like(temporary_path, temporary_before_publish):
            raise OSError("GUI session temporary state changed before publish")
        try:
            destination_before_publish = os.lstat(path)
        except FileNotFoundError:
            destination_before_publish = None
        if destination_before_publish is not None and _path_is_link_like(
            path, destination_before_publish
        ):
            raise OSError("GUI session state path is a symlink")
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory_if_supported(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _path_is_link_like(path: Path, observed: os.stat_result) -> bool:
    if stat.S_ISLNK(observed.st_mode):
        return True
    file_attributes = int(getattr(observed, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if reparse_attribute and file_attributes & reparse_attribute:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _fsync_directory_if_supported(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_state_directory(path: Path) -> int:
    before: os.stat_result | None = None
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        before = os.lstat(path)
        if _path_is_link_like(path, before):
            raise OSError("GUI session state directory is a symlink")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        observed = os.fstat(descriptor)
        if before is not None and (before.st_dev, before.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            raise OSError("GUI session state directory changed while it was being opened")
        if not stat.S_ISDIR(observed.st_mode):
            raise OSError("GUI session state parent is not a directory")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _empty_identity() -> dict[str, str]:
    return {"run_dir": "", "run_id": ""}


def _identity_from_value(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return _empty_identity()
    run_dir = str(value.get("run_dir") or "").strip().rstrip("/")
    run_id = str(value.get("run_id") or "").strip()
    if run_dir and not run_id:
        run_id = run_dir.rsplit("/", 1)[-1]
    return {"run_dir": run_dir, "run_id": run_id}


def _has_identity(identity: dict[str, str]) -> bool:
    return bool(identity.get("run_dir") or identity.get("run_id"))


def _start_was_accepted(task: dict[str, Any]) -> bool:
    """Bind labels only when start dispatch, rather than a UI status name, proves ownership."""

    metadata = task.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("start_accepted"), bool):
        return bool(metadata["start_accepted"])

    status = str(task.get("status") or "").strip().lower()
    if status not in _STARTED_STATUSES:
        return False
    details = task.get("advanced_details")
    details = details if isinstance(details, dict) else {}
    returncode = details.get("returncode")
    if type(returncode) is int:
        return returncode == 0

    # Direct GuiSession callers may not carry a command receipt. A durable run
    # identity is still authoritative for active/review states. Identityless
    # starts are accepted only while they are explicitly in the starting state.
    if _has_identity(_task_identity(task)):
        return True
    return status == "starting"


def _identities_match(left: dict[str, str], right: dict[str, str]) -> bool:
    if not _has_identity(left) or not _has_identity(right):
        return False
    left_dir = left.get("run_dir", "")
    right_dir = right.get("run_dir", "")
    if left_dir and right_dir:
        return left_dir == right_dir
    left_id = left.get("run_id", "")
    right_id = right.get("run_id", "")
    return bool(left_id and right_id and left_id == right_id)


def _task_identity(task: dict[str, Any]) -> dict[str, str]:
    run_dir = ""
    run_id = ""

    def collect(source: object) -> None:
        nonlocal run_dir, run_id
        if not isinstance(source, dict):
            return
        if not run_dir:
            run_dir = str(source.get("run_dir") or "").strip().rstrip("/")
        if not run_id:
            run_id = str(source.get("run_id") or source.get("id") or "").strip()

    task_id = str(task.get("id") or "").strip()
    if task_id and not task_id.startswith("task-"):
        run_id = task_id
    details = task.get("advanced_details")
    if isinstance(details, dict):
        collect(details.get("command_metadata"))
        payload = details.get("payload")
        collect(payload)
        if isinstance(payload, dict):
            collect(payload.get("active_goal"))
        stdout = str(details.get("stdout") or "")
        for line in stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or not value.strip():
                continue
            if key.strip() == "run_dir" and not run_dir:
                run_dir = value.strip().rstrip("/")
            elif key.strip() == "run_id" and not run_id:
                run_id = value.strip()
    readiness = task.get("readiness_gate")
    if isinstance(readiness, dict) and not run_dir:
        run_dir = str(readiness.get("active_run_dir") or "").strip().rstrip("/")
    if run_dir and not run_id:
        run_id = run_dir.rsplit("/", 1)[-1]
    return {"run_dir": run_dir, "run_id": run_id}


def _safe_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _safe_json_value(item) for key, item in value.items() if key in _GUI_METADATA_FIELDS
    }


def _safe_task_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        key: (_safe_metadata(value) if key == "metadata" else _safe_json_value(value))
        for key, value in task.items()
        if key in _GUI_SNAPSHOT_FIELDS
    }
    if len(json.dumps(snapshot, sort_keys=True).encode("utf-8")) > 128 * 1024:
        for key in ("events", "final_result", "plan", "requirements"):
            snapshot.pop(key, None)
    return snapshot


def _safe_json_value(value: object, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<truncated>"
    if isinstance(value, str):
        return redact_secrets(value)[:10_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            safe_key = redact_secrets(str(key))[:200]
            safe[safe_key] = (
                "<redacted>"
                if _sensitive_json_key(str(key))
                else _safe_json_value(item, depth=depth + 1)
            )
        return safe
    return redact_secrets(str(value))[:10_000]


def _sensitive_json_key(key: str) -> bool:
    lowered = key.strip().lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if compact in _SENSITIVE_JSON_KEYS:
        return True
    pieces = {
        piece
        for piece in "".join(
            character if character.isalnum() else " " for character in lowered
        ).split()
        if piece
    }
    if pieces.intersection(
        {
            "authorization",
            "credential",
            "credentials",
            "password",
            "passwd",
            "pwd",
            "secret",
            "token",
        }
    ):
        return True
    return any(
        marker in compact
        for marker in ("accesskey", "apikey", "clientsecret", "privatekey", "refreshtoken")
    )


def _redacted_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        _safe_json_value(payload),
        sort_keys=True,
        separators=(",", ":"),
    )


class RateLimiter:
    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = Lock()

    def allowed(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            start = now - self.window_seconds
            hits = [hit for hit in self._hits.get(key, []) if hit >= start]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _websocket_text_frame(message: str) -> bytes:
    payload = message.encode("utf-8")
    length = len(payload)
    if length < 126:
        return bytes([0x81, length]) + payload
    if length < 65536:
        return bytes([0x81, 126]) + length.to_bytes(2, "big") + payload
    return bytes([0x81, 127]) + length.to_bytes(8, "big") + payload


def _token_matches(supplied: str, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _is_loopback_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _print_non_loopback_warning() -> None:
    print(
        "WARNING: non-loopback GUI binding exposes the local control API to other "
        "machines that can reach this host. Set AGENTIC_HARNESS_GUI_TOKEN before "
        "launch when binding beyond 127.0.0.1, and keep a firewall or private "
        "network as the primary access boundary."
    )


def _open_browser_safely(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print(
            "Could not open a browser automatically. Open the URL above manually, "
            "or run agentic-harness gui --no-open for headless/automation use."
        )


def run_server_from_args(args: Any) -> int:
    port = getattr(args, "port", None)
    try:
        serve_gui(
            host=str(args.host),
            port=0 if port is None else int(port),
            doc_root=resolve_doc_root(args.doc_root),
            project_dir=Path(getattr(args, "project_dir", ".")).expanduser(),
            backend=str(getattr(args, "backend", "embedded")),
            open_browser=not bool(args.no_open),
            allow_port_fallback=False,
        )
        return 0
    except (GuiPortUnavailable, GuiSecurityError) as exc:
        print(str(exc))
        return 2
