"""Cargo and Rust inline-test detection for verified tournament verifier boundaries."""

from __future__ import annotations

from pathlib import Path
import re

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.verifiers.common import (
    _ALWAYS_PROTECTED_TEST_DIRECTORIES,
    is_link_or_reparse,
)

# A Rust attribute may span lines, so the whole source is scanned instead of
# individual lines.  Over-matching an unrelated attribute is safe; missing an
# inline test definition is not.
_RUST_INLINE_TEST_ATTRIBUTE = re.compile(r"#!?\s*\[[^\]]*\b(?:test|bench)\b[^\]]*\]")
_RUST_DOC_COMMENT_FENCE = re.compile(r"\s*(?:///|//!).*```")


def _refuse_editable_rust_inline_tests(
    root: Path,
    candidates: set[Path],
    tracked_paths: set[str],
) -> None:
    for relative in sorted(tracked_paths):
        if not relative.endswith(".rs"):
            continue
        parts = Path(relative).parts
        if parts and parts[0] in _ALWAYS_PROTECTED_TEST_DIRECTORIES:
            continue
        candidate = root / relative
        if candidate in candidates or is_link_or_reparse(candidate):
            continue
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(
                f"unable to inspect Rust verifier inputs: {relative}"
            ) from exc
        if not _rust_inline_tests(text):
            continue
        raise ConfigError(
            "verified best-of-N cannot treat inline tests in editable Rust sources as "
            f"an independent acceptance boundary: {relative}; move the acceptance "
            "tests into a frozen integration-test directory (tests/) or configure "
            "review_assets with every repository-controlled verifier input"
        )


def _rust_inline_tests(text: str) -> bool:
    if _RUST_INLINE_TEST_ATTRIBUTE.search(text):
        return True
    return any(
        _RUST_DOC_COMMENT_FENCE.match(line) is not None for line in text.splitlines()
    )
