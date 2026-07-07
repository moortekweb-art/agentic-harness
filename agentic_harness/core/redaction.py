"""Best-effort redaction for local harness artifacts."""

from __future__ import annotations

import re


SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-ant-[A-Za-z0-9._-]{8,}\b"), "sk-ant-<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b"), "sk-<redacted>"),
    (re.compile(r"\bghp_[A-Za-z0-9_]{8,}\b"), "ghp_<redacted>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"), "github_pat_<redacted>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE), "Bearer <redacted>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)"
            r"(\s*[:=]\s*['\"]?)[^'\"\s]{6,}"
        ),
        r"\1\2<redacted>",
    ),
    (
        re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:@/\s]+):(?P<pw>[^@/\s]+)@"),
        r"\g<scheme><redacted>@",
    ),
)


def redact_secrets(text: str) -> str:
    """Redact common secret-shaped tokens before writing local artifacts."""
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
