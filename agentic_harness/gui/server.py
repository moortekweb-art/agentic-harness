"""Small local HTTP server for the Agentic Harness GUI."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import base64
import hmac
import hashlib
import json
import mimetypes
import os
from threading import Lock
import time
import webbrowser

from agentic_harness.core.local_goal_bridge import LocalGoalBridge, resolve_doc_root
from agentic_harness.core.errors import HarnessError
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.gui.api import (
    command_task,
    details_payload,
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
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _portable_modes() -> list[dict[str, Any]]:
    return [
        {
            "key": "goal",
            "number": 1,
            "label": "Verified goal",
            "best_for": "One clear task that should continue until its checks pass.",
            "caution": "The configured execution method may edit files in the selected workspace.",
        }
    ]


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
    if not _is_loopback_host(host) and not os.environ.get(
        "AGENTIC_HARNESS_GUI_TOKEN", ""
    ).strip():
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
    handler = make_handler(service, allowed_hosts=allowed_hosts)
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
) -> type[BaseHTTPRequestHandler]:
    session = GuiSession()
    embedded = isinstance(service, EmbeddedExecutionBackend)
    bridge = service
    auth_token = os.environ.get("AGENTIC_HARNESS_GUI_TOKEN", "").strip()
    rate_limiter = RateLimiter(limit=240, window_seconds=60)
    trusted_hosts = allowed_hosts or {"127.0.0.1", "localhost", "::1"}

    class GuiHandler(BaseHTTPRequestHandler):
        server_version = "AgenticHarnessGUI/0.1"

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
            if route in {"/api/health", "/api/status"}:
                self._json(service.health() if embedded else health_payload(bridge))
            elif route == "/api/modes":
                self._json(
                    {
                        "modes": _portable_modes()
                        if embedded
                        else modes_payload()
                    }
                )
            elif route == "/api/readiness":
                self._json(service.readiness() if embedded else readiness_payload(bridge))
            elif route == "/api/setup":
                self._json(service.setup() if embedded else setup_payload(bridge))
            elif route == "/api/tasks":
                if embedded:
                    current = service.status()
                    self._json({"current": current, "tasks": service.history()})
                else:
                    payload = tasks_payload(bridge)
                    session.record(payload["current"])
                    payload["tasks"] = session.history() or payload["tasks"]
                    self._json(payload)
            elif route == "/api/tasks/current":
                if embedded:
                    self._json(service.status())
                else:
                    task = status_task(bridge)
                    session.record(task)
                    self._json(task)
            elif route == "/api/tasks/history":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self._json(
                    {"tasks": service.history(query=query)}
                    if embedded
                    else {"tasks": session.history(query=query)}
                )
            elif route == "/api/tasks/current/events":
                after_raw = parse_qs(parsed.query).get("after", ["0"])[0]
                try:
                    after = max(0, int(after_raw))
                except ValueError:
                    after = 0
                self._json({"events": service.events(after=after) if embedded else []})
            elif route == "/api/tasks/current/file" and embedded:
                path = parse_qs(parsed.query).get("path", [""])[0]
                goal_id = parse_qs(parsed.query).get("goal_id", [""])[0]
                try:
                    self._json(service.preview_file(path, goal_id=goal_id))
                except (ValueError, OSError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/current/artifact" and embedded:
                path = parse_qs(parsed.query).get("path", [""])[0]
                goal_id = parse_qs(parsed.query).get("goal_id", [""])[0]
                try:
                    self._json(service.preview_artifact(path, goal_id=goal_id))
                except (ValueError, OSError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/current/details":
                self._json(
                    {"task": service.status(), "raw": {}}
                    if embedded
                    else details_payload(bridge)
                )
            elif route == "/api/session":
                self._json(
                    {"version": 2, "tasks": service.history()}
                    if embedded
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
            if route == "/api/tasks":
                if embedded:
                    self._json(service.start(body))
                else:
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
                    credential = service.set_session_credential(
                        str(body.get("api_key") or "")
                    )
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
                except (ValueError, OSError, RuntimeError) as exc:
                    self._json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
            elif route == "/api/tasks/bulk":
                if embedded:
                    self._json(
                        {"ok": False, "error": "The embedded backend runs one visible goal at a time."},
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
                if embedded:
                    self._json(service.status())
                else:
                    task = watch_task(bridge)
                    session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/accept":
                if embedded:
                    self._json(service.accept())
                else:
                    task = command_task(bridge, "accept", body)
                    session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/continue":
                if embedded:
                    self._json(service.continue_task(str(body.get("feedback") or "")))
                else:
                    task = command_task(bridge, "continue", body)
                    session.record(task)
                    self._json(task)
            elif route == "/api/tasks/current/stop":
                if embedded:
                    self._json(service.stop())
                else:
                    task = command_task(bridge, "stop", body)
                    session.record(task)
                    self._json(task)
            elif route == "/api/session/import":
                if embedded:
                    self._json(
                        {"ok": False, "error": "Durable engine history cannot be replaced by a browser import."},
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
            encoded = redact_secrets(
                json.dumps(payload, indent=2, sort_keys=True)
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            self.wfile.write(encoded)

        def _allowed(self, query: str) -> bool:
            client = self.client_address[0] if self.client_address else "local"
            if not rate_limiter.allowed(client):
                self._json({"ok": False, "error": "rate limit exceeded"}, status=HTTPStatus.TOO_MANY_REQUESTS)
                return False
            if not auth_token:
                return True
            supplied = self.headers.get("Authorization", "")
            bearer = supplied.removeprefix("Bearer ").strip() if supplied.startswith("Bearer ") else ""
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
                self._json({"ok": False, "error": "websocket upgrade required"}, status=HTTPStatus.BAD_REQUEST)
                return
            key = self.headers.get("Sec-WebSocket-Key", "").strip()
            if not key:
                self._json({"ok": False, "error": "missing websocket key"}, status=HTTPStatus.BAD_REQUEST)
                return
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            while True:
                try:
                    task = service.status() if embedded else status_task(bridge)
                    if not embedded:
                        session.record(task)
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
            if "/" in relative or relative.startswith("."):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            static_root = files("agentic_harness.gui.static")
            try:
                resource = static_root.joinpath(relative)
                data = resource.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)

    return GuiHandler


class GuiSession:
    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._next_id = 1

    def enrich(self, task: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        task = dict(task)
        objective = str(source.get("objective", "")).strip()
        if objective:
            task["summary"] = objective
            task["human_title"] = objective[:80]
        metadata = dict(task.get("metadata", {}))
        for key in ("mode", "priority"):
            value = str(source.get(key, "")).strip()
            if value:
                metadata[key] = value
        metadata["safe_areas"] = [str(item) for item in source.get("safe_areas", [])] if isinstance(source.get("safe_areas"), list) else []
        metadata["checks"] = [str(item) for item in source.get("checks", [])] if isinstance(source.get("checks"), list) else []
        task["metadata"] = metadata
        return task

    def record(self, task: dict[str, Any]) -> None:
        if not task:
            return
        entry = dict(task)
        if not entry.get("id"):
            entry["id"] = f"task-{self._next_id}"
            self._next_id += 1
        self._history = [item for item in self._history if item.get("summary") != entry.get("summary")]
        self._history.insert(0, entry)
        self._history = self._history[:100]

    def history(self, *, query: str = "") -> list[dict[str, Any]]:
        tasks = list(self._history)
        needle = query.strip().lower()
        if needle:
            tasks = [task for task in tasks if needle in json.dumps(task, sort_keys=True).lower()]
        return tasks

    def export(self) -> dict[str, Any]:
        return {"version": 1, "tasks": self.history()}

    def import_payload(self, payload: dict[str, Any]) -> None:
        tasks = payload.get("tasks", [])
        if not isinstance(tasks, list):
            return
        self._history = [task for task in tasks if isinstance(task, dict)][:100]
        self._next_id = len(self._history) + 1


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
