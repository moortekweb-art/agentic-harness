"""Best-effort redaction for local harness artifacts."""

from __future__ import annotations

import json
import re
from typing import Any


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
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----"
    r".*?"
    r"-----END (?P=label)-----",
    re.DOTALL,
)


SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-ant-[A-Za-z0-9._-]{8,}\b"), "sk-ant-<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b"), "sk-<redacted>"),
    (re.compile(r"\bghp_[A-Za-z0-9_]{8,}\b"), "ghp_<redacted>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"), "github_pat_<redacted>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE), "Bearer <redacted>"),
    (
        re.compile(
            r"(?i)(?P<prefix>\b"
            r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
            r"refresh[_-]?token|private[_-]?key|token|secret|password|passwd|pwd)\b"
            r"(?:\\?['\"])?\s*[:=]\s*(?:\\?['\"])?)(?P<value>[^'\"\\\s,}]{6,})"
        ),
        r"\g<prefix><redacted>",
    ),
    (
        re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:@/\s]+):(?P<pw>[^@/\s]+)@"),
        r"\g<scheme><redacted>@",
    ),
)


def redact_secrets(text: str) -> str:
    """Redact common secret-shaped tokens before writing local artifacts."""
    redacted = _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", text)
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sensitive_json_key(key: str) -> bool:
    """Return true when a structured field name denotes secret material."""

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
        for marker in (
            "accesskey",
            "apikey",
            "clientsecret",
            "privatekey",
            "refreshtoken",
        )
    ) or compact.endswith("cookie") or compact.startswith("setcookie")


def redact_json_value(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact sensitive fields before JSON serialization."""

    if depth > 32:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if sensitive_json_key(str(key))
                else redact_json_value(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return [redact_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def redact_preview_text(text: str) -> str:
    """Redact JSON in native form and fall back to bounded text redaction."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        structured = json.loads(normalized)
    except (json.JSONDecodeError, RecursionError):
        return redact_secrets(normalized)
    return json.dumps(
        redact_json_value(structured),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
