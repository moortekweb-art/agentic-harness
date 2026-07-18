"""Small workspace snapshots for human-readable run reports."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
import os
from pathlib import Path
from typing import Any

from agentic_harness.core.presentation import safe_inline_text

SNAPSHOT_SCHEMA = "agentic_harness.workspace_snapshot.v1"
MAX_FILES = 5000
MAX_HASH_BYTES = 1_000_000
MAX_SUMMARY_FILES = 8
EXCLUDED_DIRS = {
    ".agentic-harness",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def capture_workspace_snapshot(project_dir: str | Path) -> dict[str, Any]:
    """Capture a bounded file snapshot for later changed-file summaries."""
    root = Path(project_dir)
    files: dict[str, dict[str, Any]] = {}
    truncated = False
    for path in _iter_workspace_files(root):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        rel = path.relative_to(root).as_posix()
        try:
            files[rel] = _file_fingerprint(path)
        except FileNotFoundError:
            # Editors and atomic writers commonly create then rename temporary
            # files while a snapshot is walking the tree. A vanished path is
            # not part of the completed snapshot and must not fail the run.
            continue
    return {
        "schema": SNAPSHOT_SCHEMA,
        "files": files,
        "truncated": truncated,
    }


def workspace_change_summary(
    project_dir: str | Path,
    snapshot: dict[str, Any] | None,
    *,
    limit: int = MAX_SUMMARY_FILES,
) -> dict[str, Any] | None:
    """Compare the current workspace to a prior snapshot."""
    if not _valid_snapshot(snapshot):
        return None
    assert snapshot is not None
    before = snapshot["files"]
    current = capture_workspace_snapshot(project_dir)
    after = current["files"]

    entries: list[dict[str, str]] = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            entries.append({"status": "added", "path": path})
        elif path not in after:
            entries.append({"status": "deleted", "path": path})
        elif before[path] != after[path]:
            entries.append({"status": "modified", "path": path})

    return {
        "total": len(entries),
        "entries": entries[:limit],
        "omitted": max(0, len(entries) - limit),
        "truncated": bool(snapshot.get("truncated")) or bool(current.get("truncated")),
    }


def format_workspace_change_lines(summary: dict[str, Any] | None) -> list[str]:
    if summary is None:
        return []
    if summary.get("evidence_unavailable") is True:
        return ["Changed-file evidence: unavailable at the terminal boundary"]
    total = int(summary.get("total", 0))
    noun = "file" if total == 1 else "files"
    lines = [f"Changed: {total} {noun}"]
    for entry in summary.get("entries", []):
        if not isinstance(entry, dict):
            continue
        status = safe_inline_text(entry.get("status", "changed"))
        path = safe_inline_text(entry.get("path", ""))
        if path:
            lines.append(f"- {status} {path}")
    omitted = int(summary.get("omitted", 0))
    if omitted:
        lines.append(f"- ... {omitted} more")
    if summary.get("truncated"):
        lines.append("- note: workspace snapshot was capped")
    return lines


def _valid_snapshot(snapshot: dict[str, Any] | None) -> bool:
    return (
        isinstance(snapshot, dict)
        and snapshot.get("schema") == SNAPSHOT_SCHEMA
        and isinstance(snapshot.get("files"), dict)
    )


def _iter_workspace_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for directory, names, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=lambda _error: None,
    ):
        parent = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if name not in EXCLUDED_DIRS and not (parent / name).is_symlink()
        )
        for name in sorted(filenames):
            path = parent / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            yield path


def _is_excluded(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in EXCLUDED_DIRS for part in rel.parts)


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    fingerprint: dict[str, Any] = {
        "size": stat.st_size,
        "mode": stat.st_mode,
    }
    if stat.st_size > MAX_HASH_BYTES:
        fingerprint["mtime_ns"] = stat.st_mtime_ns
        return fingerprint
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    fingerprint["sha256"] = digest.hexdigest()
    return fingerprint
