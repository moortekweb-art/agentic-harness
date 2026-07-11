"""Small helpers for private, atomic local artifacts."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from agentic_harness.core.redaction import redact_secrets


def write_private_text(path: str | Path, content: str) -> Path:
    """Atomically write redacted text with owner-only permissions."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            delete=False,
        ) as handle:
            handle.write(redact_secrets(content))
            tmp = Path(handle.name)
        tmp.chmod(0o600)
        tmp.replace(target)
        target.chmod(0o600)
        return target
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
