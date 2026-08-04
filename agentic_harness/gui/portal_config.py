"""Owner-controlled configuration for the Local Network Portal.

The portal renders links to services on the operator's own private network, so
its configuration is deliberately kept *outside* the installed package. Package
data is world-readable to anything that can reach the static server and can be
captured by a local wheel build; an owner-controlled config path is neither.

Resolution order:

1. ``$AGENTIC_HARNESS_PORTAL_SERVICES`` — explicit override (any path);
2. ``$XDG_CONFIG_HOME/agentic-harness/services.json``;
3. ``~/.config/agentic-harness/services.json``.

The file is read under a size bound, validated against a strict shape, and
reduced to the only three fields the renderer needs. Entries whose URL could
carry a credential are dropped with a human-readable warning rather than being
handed to the browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from agentic_harness.core.redaction import sensitive_json_key


PORTAL_SERVICES_PATH_ENV = "AGENTIC_HARNESS_PORTAL_SERVICES"
XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"
PORTAL_CONFIG_RELATIVE_PATH = Path("agentic-harness") / "services.json"
DEFAULT_PORTAL_CONFIG_DISPLAY_PATH = "~/.config/agentic-harness/services.json"
MAX_PORTAL_CONFIG_BYTES = 64 * 1024
MAX_PORTAL_SERVICES = 200
MAX_PORTAL_FIELD_CHARS = 500
ALLOWED_PORTAL_SCHEMES = frozenset({"http", "https"})
# Credential-shaped query names the key-aware policy does not already claim.
# ``sensitive_json_key`` covers token/secret/password/credential families; a
# signed dashboard link hides its capability in a bare signature parameter.
_EXTRA_SENSITIVE_QUERY_KEYS = frozenset({"sig", "signature", "key", "auth"})


class PortalConfigError(Exception):
    """The portal configuration file exists but cannot be used."""


def portal_config_path() -> Path:
    """Return the resolved owner-controlled portal configuration path."""

    override = os.environ.get(PORTAL_SERVICES_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    xdg_config_home = os.environ.get(XDG_CONFIG_HOME_ENV, "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / PORTAL_CONFIG_RELATIVE_PATH
    return Path.home() / ".config" / PORTAL_CONFIG_RELATIVE_PATH


def _sensitive_query_key(name: str) -> bool:
    compact = "".join(character for character in name.strip().lower() if character.isalnum())
    return compact in _EXTRA_SENSITIVE_QUERY_KEYS or sensitive_json_key(name)


def sanitize_service_url(raw: Any) -> tuple[str, str]:
    """Return ``(url, warning)`` for one configured service URL.

    A non-empty warning means the URL is unusable and the caller must drop it.
    The warning never repeats the offending URL, so it stays safe to render.
    """

    if not isinstance(raw, str):
        return "", "url must be a string"
    value = raw.strip()
    if not value:
        return "", "url is empty"
    if len(value) > MAX_PORTAL_FIELD_CHARS:
        return "", "url is too long"
    if any(character in value for character in ("\n", "\r", "\t")):
        return "", "url contains control characters"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "", "url is not a valid URL"
    if parsed.scheme.lower() not in ALLOWED_PORTAL_SCHEMES:
        return "", "url must use http:// or https://"
    try:
        username = parsed.username
        password = parsed.password
        hostname = parsed.hostname
    except ValueError:
        return "", "url is not a valid URL"
    if username or password:
        return "", "url must not embed a username or password"
    if not hostname:
        return "", "url must name a host"
    try:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return "", "url query string is not valid"
    for name, _ in query_pairs:
        if _sensitive_query_key(name):
            return "", f"url query parameter {name!r} looks like a credential"
    return value, ""


def _entry_text(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _sanitize_entry(raw: Any, position: int) -> tuple[dict[str, str] | None, str]:
    if not isinstance(raw, dict):
        return None, f"entry {position} is not an object"
    label = _entry_text(raw.get("label"))
    if not label:
        return None, f"entry {position} is missing a non-empty \"label\""
    if len(label) > MAX_PORTAL_FIELD_CHARS:
        return None, f"entry {position} has a label longer than {MAX_PORTAL_FIELD_CHARS} characters"
    url, warning = sanitize_service_url(raw.get("url"))
    if warning:
        return None, f"{label}: {warning}"
    entry: dict[str, str] = {"label": label, "url": url}
    note = _entry_text(raw.get("note"))
    if note:
        entry["note"] = note[:MAX_PORTAL_FIELD_CHARS]
    return entry, ""


def _read_bounded_text(path: Path) -> str:
    """Read at most ``MAX_PORTAL_CONFIG_BYTES`` + 1 bytes and decode as UTF-8."""

    with path.open("rb") as handle:
        raw = handle.read(MAX_PORTAL_CONFIG_BYTES + 1)
    if len(raw) > MAX_PORTAL_CONFIG_BYTES:
        raise PortalConfigError(
            f"portal configuration is larger than {MAX_PORTAL_CONFIG_BYTES} bytes"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PortalConfigError("portal configuration is not valid UTF-8 text") from exc


def load_portal_services(path: Path | None = None) -> dict[str, Any]:
    """Return the portal payload: configured status, services, and warnings.

    Never raises for operator-input problems. A missing file is the normal
    unconfigured state; a malformed file becomes an ``error`` string the portal
    can display without exposing file contents.
    """

    target = portal_config_path() if path is None else Path(path).expanduser()
    payload: dict[str, Any] = {
        "ok": True,
        "configured": False,
        "services": [],
        "warnings": [],
    }
    try:
        text = _read_bounded_text(target)
    except FileNotFoundError:
        return payload
    except IsADirectoryError:
        payload["configured"] = True
        payload["error"] = "portal configuration path is a directory"
        return payload
    except PortalConfigError as exc:
        # The file is there, it just cannot be used: that is a configured but
        # broken portal, not an unconfigured one, and the portal says so.
        payload["configured"] = True
        payload["error"] = str(exc)
        return payload
    except OSError:
        # Permissions or an unreadable device: name the class of problem
        # without echoing the operator's path back over the network.
        payload["configured"] = True
        payload["error"] = "portal configuration could not be read"
        return payload

    payload["configured"] = True
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        payload["error"] = "portal configuration is not valid JSON"
        return payload

    if not isinstance(document, dict) or not isinstance(document.get("services"), list):
        payload["error"] = 'portal configuration must be {"services": [ ... ]}'
        return payload

    raw_entries = document["services"]
    warnings: list[str] = []
    if len(raw_entries) > MAX_PORTAL_SERVICES:
        warnings.append(
            f"only the first {MAX_PORTAL_SERVICES} of {len(raw_entries)} services are shown"
        )
        raw_entries = raw_entries[:MAX_PORTAL_SERVICES]

    services: list[dict[str, str]] = []
    for position, raw in enumerate(raw_entries, start=1):
        entry, warning = _sanitize_entry(raw, position)
        if entry is None:
            warnings.append(f"skipped {warning}")
            continue
        services.append(entry)

    payload["services"] = services
    payload["warnings"] = warnings
    return payload
