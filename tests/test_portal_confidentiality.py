"""Confidentiality guarantees for the Local Network Portal.

The portal lists services on the operator's private network. That list is
topology: it must never be readable without the session token, never reachable
under an attacker-chosen Host, never cached, and never packaged into a wheel.
These tests pin each of those properties against the real server and a real
wheel build rather than against source strings.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import threading
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from agentic_harness.gui.backend import EmbeddedExecutionBackend
from agentic_harness.gui.portal_config import (
    MAX_PORTAL_CONFIG_BYTES,
    PORTAL_SERVICES_PATH_ENV,
    XDG_CONFIG_HOME_ENV,
    load_portal_services,
    portal_config_path,
    sanitize_service_url,
)
from agentic_harness.gui.server import SERVABLE_STATIC_PATHS, make_handler


REPO_ROOT = Path(__file__).parents[1]
STATIC_ROOT = REPO_ROOT / "agentic_harness" / "gui" / "static"
GUI_TOKEN_ENV = "AGENTIC_HARNESS_GUI_TOKEN"
PORTAL_ROUTE = "/api/portal/services"


@contextmanager
def portal_server(tmp_path: Path) -> Iterator[str]:
    """Serve the GUI with an embedded backend, isolated from the real host."""

    service = EmbeddedExecutionBackend(str(tmp_path / "workspace"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request_portal(
    base_url: str,
    path: str = PORTAL_ROUTE,
    *,
    token: str | None = None,
    host: str | None = None,
    method: str = "GET",
    body: bytes | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    """Return ``(status, payload, headers)`` for one portal request."""

    request = urllib.request.Request(base_url + path, data=body, method=method)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    if host is not None:
        request.add_header("Host", host)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return (
                response.status,
                json.loads(response.read().decode("utf-8")),
                dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload, dict(exc.headers)


def write_config(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 1. Configuration resolution and validation
# --------------------------------------------------------------------------


def test_config_path_prefers_env_then_xdg_then_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "explicit" / "services.json"
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(override))
    monkeypatch.setenv(XDG_CONFIG_HOME_ENV, str(tmp_path / "xdg"))
    assert portal_config_path() == override

    monkeypatch.delenv(PORTAL_SERVICES_PATH_ENV)
    assert portal_config_path() == tmp_path / "xdg" / "agentic-harness" / "services.json"

    monkeypatch.delenv(XDG_CONFIG_HOME_ENV)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert (
        portal_config_path()
        == tmp_path / "home" / ".config" / "agentic-harness" / "services.json"
    )


def test_missing_config_is_the_unconfigured_state_not_an_error(tmp_path: Path) -> None:
    payload = load_portal_services(tmp_path / "absent.json")

    assert payload == {"ok": True, "configured": False, "services": [], "warnings": []}
    assert "error" not in payload


def test_config_larger_than_the_bound_is_refused_without_reading_it_all(
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "services.json"
    oversized.write_text("x" * (MAX_PORTAL_CONFIG_BYTES + 4096), encoding="utf-8")

    payload = load_portal_services(oversized)

    assert payload["configured"] is True
    assert "larger than" in str(payload["error"])
    assert payload["services"] == []


@pytest.mark.parametrize(
    "document, marker",
    [
        ("not json at all", "not valid JSON"),
        ({"services": {"label": "x"}}, "must be"),
        ([{"label": "Bare array", "url": "https://a.example.invalid/"}], "must be"),
        ({"nope": []}, "must be"),
    ],
)
def test_malformed_config_becomes_a_readable_error(
    tmp_path: Path, document: object, marker: str
) -> None:
    target = tmp_path / "services.json"
    if isinstance(document, str):
        target.write_text(document, encoding="utf-8")
    else:
        write_config(target, document)

    payload = load_portal_services(target)

    assert payload["configured"] is True
    assert marker in str(payload["error"])
    assert payload["services"] == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://a.example.invalid/",
        "data:text/html,hi",
        "//a.example.invalid/",
        "a.example.invalid/",
    ],
)
def test_only_http_and_https_urls_are_accepted(url: str) -> None:
    value, warning = sanitize_service_url(url)

    assert value == ""
    assert warning


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@a.example.invalid/",
        "https://user@a.example.invalid/",
        "http://admin:hunter2@a.example.invalid:8080/panel",
    ],
)
def test_urls_embedding_userinfo_are_refused(url: str) -> None:
    value, warning = sanitize_service_url(url)

    assert value == ""
    assert "username or password" in warning
    # The warning is rendered in the browser: it must not repeat the secret.
    assert "password" not in warning.replace("username or password", "")
    assert "hunter2" not in warning
    assert url not in warning


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "sig",
        "signature",
        "password",
        "secret",
        "credential",
        "api_key",
        "key",
        "auth",
        "ACCESS_TOKEN",
        "apiKey",
        "api-key",
    ],
)
def test_credential_shaped_query_keys_are_refused(key: str) -> None:
    value, warning = sanitize_service_url(
        f"https://a.example.invalid/dash?{key}=s3cr3t-value"
    )

    assert value == ""
    assert "credential" in warning
    # The key name may be named; the value never may be.
    assert "s3cr3t-value" not in warning


@pytest.mark.parametrize("key", ["page", "view", "tab", "q", "id"])
def test_ordinary_query_keys_are_preserved(key: str) -> None:
    url = f"https://a.example.invalid/dash?{key}=2"

    assert sanitize_service_url(url) == (url, "")


def test_valid_config_is_reduced_to_label_url_note(tmp_path: Path) -> None:
    target = write_config(
        tmp_path / "services.json",
        {
            "services": [
                {
                    "label": "Dashboard",
                    "url": "https://a.example.invalid/",
                    "note": "A note",
                    "internal_ip": "192.0.2.3",
                    "owner": "someone",
                }
            ]
        },
    )

    payload = load_portal_services(target)

    assert payload["services"] == [
        {"label": "Dashboard", "url": "https://a.example.invalid/", "note": "A note"}
    ]
    assert payload["warnings"] == []


def test_unusable_entries_are_dropped_with_warnings_that_leak_nothing(
    tmp_path: Path,
) -> None:
    target = write_config(
        tmp_path / "services.json",
        {
            "services": [
                {"label": "Good", "url": "https://good.example.invalid/"},
                {"label": "Creds", "url": "https://u:pw@secret-host.example.invalid/"},
                {"label": "Signed", "url": "https://b.example.invalid/?sig=abc123"},
                {"label": "Scheme", "url": "javascript:alert(1)"},
                {"url": "https://c.example.invalid/"},
                "not-an-object",
            ]
        },
    )

    payload = load_portal_services(target)

    assert payload["services"] == [
        {"label": "Good", "url": "https://good.example.invalid/"}
    ]
    assert len(payload["warnings"]) == 5
    joined = " ".join(str(warning) for warning in payload["warnings"])
    # Warnings are operator-facing text: no host, no secret, ever.
    assert "secret-host.example.invalid" not in joined
    assert "abc123" not in joined
    assert "pw" not in joined.split()


def test_service_list_is_bounded(tmp_path: Path) -> None:
    target = write_config(
        tmp_path / "services.json",
        {
            "services": [
                {"label": f"S{index}", "url": f"https://a{index}.example.invalid/"}
                for index in range(400)
            ]
        },
    )

    payload = load_portal_services(target)

    assert len(payload["services"]) == 200
    assert any("only the first 200" in str(w) for w in payload["warnings"])


# --------------------------------------------------------------------------
# 2. The endpoint: authentication, Host, no-store
# --------------------------------------------------------------------------


def test_portal_endpoint_requires_the_token_when_one_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(
        tmp_path / "services.json",
        {"services": [{"label": "Private", "url": "https://private.example.invalid/"}]},
    )
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(config))
    monkeypatch.setenv(GUI_TOKEN_ENV, "s3cret-token")

    with portal_server(tmp_path) as base_url:
        status, payload, _ = request_portal(base_url)
        assert status == 401
        assert payload == {"ok": False, "error": "unauthorized"}
        # The topology must not appear anywhere in the rejection.
        assert "private.example.invalid" not in json.dumps(payload)

        status, payload, _ = request_portal(base_url, token="wrong-token")
        assert status == 401

        status, payload, headers = request_portal(base_url, token="s3cret-token")
        assert status == 200
        assert payload["services"] == [
            {"label": "Private", "url": "https://private.example.invalid/"}
        ]
        assert headers["Cache-Control"] == "no-store"
        assert headers["Content-Type"].startswith("application/json")


def test_portal_endpoint_serves_the_owner_config_without_a_token_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(
        tmp_path / "services.json",
        {"services": [{"label": "Home", "url": "https://home.example.invalid/"}]},
    )
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(config))
    monkeypatch.delenv(GUI_TOKEN_ENV, raising=False)

    with portal_server(tmp_path) as base_url:
        status, payload, headers = request_portal(base_url)

    assert status == 200
    assert payload["configured"] is True
    assert payload["services"] == [
        {"label": "Home", "url": "https://home.example.invalid/"}
    ]
    assert headers["Cache-Control"] == "no-store"


def test_portal_endpoint_reports_the_unconfigured_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(tmp_path / "absent.json"))
    monkeypatch.delenv(GUI_TOKEN_ENV, raising=False)

    with portal_server(tmp_path) as base_url:
        status, payload, _ = request_portal(base_url)

    assert status == 200
    assert payload["configured"] is False
    assert payload["services"] == []


def test_portal_endpoint_rejects_an_untrusted_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(
        tmp_path / "services.json",
        {"services": [{"label": "Private", "url": "https://private.example.invalid/"}]},
    )
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(config))
    monkeypatch.delenv(GUI_TOKEN_ENV, raising=False)

    with portal_server(tmp_path) as base_url:
        status, payload, _ = request_portal(base_url, host="attacker.example")

    assert status == 403
    assert payload == {"ok": False, "error": "untrusted host"}
    assert "private.example.invalid" not in json.dumps(payload)


@pytest.mark.parametrize(
    "path",
    ["/", "/index.html", "/portal.html", "/portal.js", "/api/health", "/nope"],
)
def test_every_get_route_validates_the_host(tmp_path: Path, path: str) -> None:
    """DNS rebinding must not have a static-asset back door."""

    with portal_server(tmp_path) as base_url:
        status, payload, _ = request_portal(base_url, path, host="attacker.example")

    assert status == 403
    assert payload == {"ok": False, "error": "untrusted host"}


@pytest.mark.parametrize("path", ["/api/demo", "/api/tasks/start", "/nope"])
def test_every_post_route_validates_the_host(tmp_path: Path, path: str) -> None:
    with portal_server(tmp_path) as base_url:
        status, payload, _ = request_portal(
            base_url,
            path,
            host="attacker.example",
            method="POST",
            body=b"{}",
        )

    assert status == 403
    assert payload == {"ok": False, "error": "untrusted host"}


def test_trusted_hosts_still_work_for_static_and_api(tmp_path: Path) -> None:
    """The Host policy must not break the ordinary loopback deployment."""

    with portal_server(tmp_path) as base_url:
        port = base_url.rsplit(":", 1)[1]
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            status, _, _ = request_portal(base_url, "/api/health", host=host)
            assert status == 200
            request = urllib.request.Request(base_url + "/portal.html")
            request.add_header("Host", host)
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200


# --------------------------------------------------------------------------
# 3. Static serving is an allowlist
# --------------------------------------------------------------------------


@pytest.fixture()
def planted_local_json() -> Iterator[Path]:
    """Plant an operator file in the package static dir, then remove it."""

    planted = STATIC_ROOT / "services.local.json"
    planted.write_text(
        json.dumps(
            {"services": [{"label": "Leak", "url": "https://leak.example.invalid/"}]}
        ),
        encoding="utf-8",
    )
    try:
        yield planted
    finally:
        planted.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "path",
    [
        "/services.local.json",
        "/static/services.local.json",
        "/services%2Elocal.json",
        "/services.local%2Ejson",
        "/%73ervices.local.json",
        "/illustrations/../services.local.json",
        "/illustrations/..%2Fservices.local.json",
        "/.%2Eservices.local.json",
        "/SERVICES.LOCAL.JSON",
    ],
)
def test_local_json_is_never_served(
    tmp_path: Path, planted_local_json: Path, path: str
) -> None:
    """Even present on disk, an operator file is invisible over HTTP."""

    assert planted_local_json.is_file()

    with portal_server(tmp_path) as base_url:
        request = urllib.request.Request(base_url + path)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8", "replace")
                raise AssertionError(f"{path} was served: {response.status} {body[:200]}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, path
            assert b"leak.example.invalid" not in exc.read()


@pytest.mark.parametrize(
    "path",
    [
        "/server.py",
        "/portal_config.py",
        "/__init__.py",
        "/api.py",
        "/.env",
        "/secrets.txt",
        "/services.json",
    ],
)
def test_only_web_assets_are_reachable(tmp_path: Path, path: str) -> None:
    with portal_server(tmp_path) as base_url:
        request = urllib.request.Request(base_url + path)
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=5)

    assert failure.value.code == 404


# Decoys that share an extension with a genuinely shipped asset. An extension
# filter would serve every one of these; only an exact allowlist refuses them.
SAME_EXTENSION_DECOYS = (
    "evil.js",
    "evil.html",
    "evil.css",
    "evil.svg",
    "evil.LICENSE",
    "evil.example.json",
    "illustrations/evil.webp",
    "illustrations/evil.js",
)


@pytest.fixture()
def planted_decoys() -> Iterator[list[Path]]:
    """Plant same-extension decoys in the package static dir, then remove them."""

    planted: list[Path] = []
    for relative in SAME_EXTENSION_DECOYS:
        target = STATIC_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PLANTED-DECOY-leak.example.invalid")
        planted.append(target)
    try:
        yield planted
    finally:
        for target in planted:
            target.unlink(missing_ok=True)


@pytest.mark.parametrize("relative", SAME_EXTENSION_DECOYS)
def test_same_extension_decoys_are_never_served(
    tmp_path: Path, planted_decoys: list[Path], relative: str
) -> None:
    """A web-looking suffix is not a licence to serve; the path must be known."""

    assert (STATIC_ROOT / relative).is_file()

    with portal_server(tmp_path) as base_url:
        request = urllib.request.Request(f"{base_url}/{relative}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8", "replace")
                raise AssertionError(f"{relative} was served: {response.status} {body[:200]}")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, relative
            assert b"PLANTED-DECOY" not in exc.read()


@pytest.mark.parametrize(
    "path",
    [
        "/static/evil.js",
        "/EVIL.JS",
        "/evil%2Ejs",
        "/illustrations/evil.webp",
        "/unknown/nested.js",
        "/illustrations/nested/deep.webp",
        "/illustrations",
        "/illustrations/",
    ],
)
def test_unknown_and_nested_asset_paths_are_refused(
    tmp_path: Path, planted_decoys: list[Path], path: str
) -> None:
    with portal_server(tmp_path) as base_url:
        request = urllib.request.Request(base_url + path)
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=5)

    assert failure.value.code == 404, path


def _tracked_static_assets() -> list[str]:
    """Every tracked static asset, relative to the static root."""

    tracked = subprocess.run(
        ["git", "ls-files", "agentic_harness/gui/static"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    prefix = "agentic_harness/gui/static/"
    return [entry.removeprefix(prefix) for entry in tracked if entry.startswith(prefix)]


@pytest.mark.parametrize("relative", _tracked_static_assets())
def test_every_shipped_web_asset_is_still_served(tmp_path: Path, relative: str) -> None:
    """The allowlist must not have quietly broken the app.

    Parametrised over the real tracked asset list, so a new asset that nobody
    added to the allowlist fails here instead of 404ing in a browser.
    """

    with portal_server(tmp_path) as base_url:
        with urllib.request.urlopen(f"{base_url}/{relative}", timeout=5) as response:
            assert response.status == 200
            assert response.read()


def test_the_index_route_is_served(tmp_path: Path) -> None:
    with portal_server(tmp_path) as base_url:
        with urllib.request.urlopen(base_url + "/", timeout=5) as response:
            assert response.status == 200
            assert response.read()


def test_the_serving_allowlist_is_exactly_the_tracked_asset_set() -> None:
    """No allowlist entry may be stale, and no tracked asset may be missing."""

    assert set(SERVABLE_STATIC_PATHS) == set(_tracked_static_assets())


# --------------------------------------------------------------------------
# 4. Packaging: a planted decoy cannot enter a wheel
# --------------------------------------------------------------------------


def _gui_package_data_patterns() -> list[str]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"]["package-data"]["agentic_harness.gui"]


def test_package_data_rules_are_exact_paths_not_globs() -> None:
    patterns = _gui_package_data_patterns()

    # No glob at all: "static/*" and even "static/*.js" match untracked files,
    # so either would sweep an operator's services.local.json — or a planted
    # evil.js — into a locally built wheel.
    assert not [
        pattern for pattern in patterns if set(pattern) & set("*?[")
    ], patterns
    for decoy in ("static/services.local.json", "static/evil.js", *(
        f"static/{name}" for name in SAME_EXTENSION_DECOYS
    )):
        assert not [
            pattern for pattern in patterns if fnmatch.fnmatch(decoy, pattern)
        ], (decoy, patterns)


def test_static_package_data_covers_exactly_every_tracked_asset() -> None:
    """Exact paths must not silently drop a real asset from the wheel."""

    patterns = set(_gui_package_data_patterns())
    tracked = {f"static/{relative}" for relative in _tracked_static_assets()}

    assert patterns == tracked


@pytest.mark.slow
def test_planted_decoys_cannot_enter_a_wheel(tmp_path: Path) -> None:
    """Build a real wheel with decoys planted; none may be packaged.

    This is the deterministic reproduction of the packaging finding: setuptools
    package-data matches untracked files, so a glob would sweep an operator's
    real service list — or any same-extension decoy — into a locally built
    wheel.
    """

    source = tmp_path / "src"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--no-hardlinks", str(REPO_ROOT), str(source)],
        capture_output=True,
        check=True,
    )
    # Carry the working-tree packaging rules into the clone.
    (source / "pyproject.toml").write_bytes((REPO_ROOT / "pyproject.toml").read_bytes())
    static_dir = source / "agentic_harness" / "gui" / "static"
    (static_dir / "services.local.json").write_text(
        json.dumps({"services": [{"label": "Decoy", "url": "https://decoy.example.invalid/"}]}),
        encoding="utf-8",
    )
    for relative in SAME_EXTENSION_DECOYS:
        target = static_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PLANTED-DECOY")

    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path / "dist"), "."],
        cwd=source,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable in this environment: {build.stderr[-400:]}")

    wheels = sorted((tmp_path / "dist").glob("*.whl"))
    assert wheels, "the build produced no wheel"
    names = set(zipfile.ZipFile(wheels[-1]).namelist())

    assert not [name for name in names if name.endswith(".local.json")], names
    assert not [name for name in names if "services.local" in name], names
    for relative in SAME_EXTENSION_DECOYS:
        assert f"agentic_harness/gui/static/{relative}" not in names, relative
    assert not [name for name in names if "evil" in name.lower()], names
    # ...and every real asset is still there, so this is not a vacuous pass.
    for relative in _tracked_static_assets():
        assert f"agentic_harness/gui/static/{relative}" in names, relative


# --------------------------------------------------------------------------
# 5. Prefix safety of the portal's own URLs
# --------------------------------------------------------------------------


def test_portal_frontend_uses_only_mount_relative_urls() -> None:
    portal_js = (STATIC_ROOT / "portal.js").read_text(encoding="utf-8")
    portal_html = (STATIC_ROOT / "portal.html").read_text(encoding="utf-8")

    # A single root-absolute URL breaks every path-prefix deployment.
    assert 'fetch("/api' not in portal_js
    assert '"/api/portal/services"' not in portal_js
    assert "api/portal/services" in portal_js
    assert not [
        match
        for match in portal_html.split('="/')[1:]
        if not match.startswith("/")  # protocol-relative "//" is not our concern here
    ], portal_html

    # The mount is derived from the document location, so "/hub" and "/hub/"
    # both resolve to "/hub/api/portal/services".
    assert "appRoot" in portal_js
    assert "window.location" in portal_js


def test_portal_service_route_is_reachable_under_a_path_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the browser computes for "/hub/" must exist on the server."""

    config = write_config(
        tmp_path / "services.json",
        {"services": [{"label": "Prefixed", "url": "https://a.example.invalid/"}]},
    )
    monkeypatch.setenv(PORTAL_SERVICES_PATH_ENV, str(config))
    monkeypatch.delenv(GUI_TOKEN_ENV, raising=False)

    with portal_server(tmp_path) as base_url:
        # A reverse proxy strips the prefix before forwarding, so the server
        # sees the bare route in both deployments.
        status, payload, _ = request_portal(base_url, PORTAL_ROUTE)

    assert status == 200
    assert payload["services"] == [
        {"label": "Prefixed", "url": "https://a.example.invalid/"}
    ]


@pytest.mark.skipif(
    not any(
        os.access(os.path.join(directory, "node"), os.X_OK)
        for directory in os.environ.get("PATH", "").split(os.pathsep)
        if directory
    ),
    reason="node is not available",
)
def test_portal_js_parses() -> None:
    result = subprocess.run(
        ["node", "--check", str(STATIC_ROOT / "portal.js")],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
