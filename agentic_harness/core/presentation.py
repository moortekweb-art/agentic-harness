"""Safe one-line text for terminal and Markdown presentation."""

from __future__ import annotations

import unicodedata

from agentic_harness.core.redaction import redact_secrets


def safe_inline_text(value: object) -> str:
    """Redact secrets and escape characters that can alter line-oriented output."""
    rendered: list[str] = []
    for character in redact_secrets(str(value)):
        if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            rendered.append(character.encode("unicode_escape").decode("ascii"))
        else:
            rendered.append(character)
    return "".join(rendered)
