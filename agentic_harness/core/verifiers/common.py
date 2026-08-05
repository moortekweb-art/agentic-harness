"""Shared path, glob, and hashing helpers for verifier-asset manifests."""

from __future__ import annotations

import hashlib
from fnmatch import fnmatchcase
from pathlib import Path
import stat
import subprocess

from agentic_harness.core.config import CONFIG_DIR
from agentic_harness.core.errors import ConfigError

_ALWAYS_PROTECTED_TEST_DIRECTORIES = ("tests", "test", "spec", "specs")


def require_lexical_regular_path(root: Path, candidate: Path, *, label: str) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"verifier asset path is outside the workspace: {label}") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if is_link_or_reparse(current):
            raise ConfigError(
                f"verifier asset must not use a symlink or reparse point: {label}"
            )


def _repository_argument_path(root: Path, text: str, *, executable: bool) -> Path | None:
    # Go package selectors such as ``./...`` are command syntax, not repository
    # paths.  In particular, Windows/Python 3.11 can report a synthetic ``...``
    # path as existing and then fail while traversing it.  Ecosystem-specific
    # manifest inference below supplies the actual verifier assets.
    normalized = text.replace("\\", "/")
    if normalized == "..." or normalized.endswith("/..."):
        return None
    raw = Path(text)
    if ".." in raw.parts:
        raise ConfigError(f"verifier asset path must not contain parent traversal: {text}")
    candidate = raw.absolute() if raw.is_absolute() else (root / raw).absolute()
    try:
        candidate.relative_to(root)
    except ValueError:
        if not raw.is_absolute() and ("/" in text or "\\" in text):
            raise ConfigError(f"verifier asset path is outside the workspace: {text}")
        return None
    if executable and not _lexists(candidate) and "/" not in text and "\\" not in text:
        return None
    return candidate if _lexists(candidate) else None


def _regular_files(root: Path, directory: Path) -> set[Path]:
    result: set[Path] = set()
    for candidate in directory.rglob("*"):
        if CONFIG_DIR in candidate.parts or ".git" in candidate.parts:
            continue
        if is_link_or_reparse(candidate):
            raise ConfigError(
                f"verifier asset must not be a symlink or reparse point: {candidate}"
            )
        if candidate.is_file():
            require_lexical_regular_path(root, candidate, label=str(candidate))
            result.add(candidate)
    return result


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        reparse_flag and attributes & reparse_flag
    )


def _add_patterns(root: Path, candidates: set[Path], names: tuple[str, ...]) -> None:
    for name in names:
        candidate = root / name
        if _lexists(candidate):
            require_lexical_regular_path(root, candidate, label=name)
            if candidate.is_file():
                candidates.add(candidate)


def _add_globs(root: Path, candidates: set[Path], patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        for candidate in root.glob(pattern):
            if is_link_or_reparse(candidate):
                raise ConfigError(
                    f"verifier asset must not be a symlink or reparse point: {candidate}"
                )
            if candidate.is_file():
                require_lexical_regular_path(root, candidate, label=str(candidate))
                candidates.add(candidate)


def _is_excluded_workspace_path(path: Path) -> bool:
    return CONFIG_DIR in path.parts or ".git" in path.parts


def _tracked_membership(relative: str, tracked_paths: set[str]) -> bool:
    if relative in tracked_paths:
        return True
    prefix = f"{relative}/"
    return any(tracked.startswith(prefix) for tracked in tracked_paths)


def _configured_test_root(
    root: Path,
    base: Path,
    value: str,
    *,
    ecosystem: str,
    allow_dynamic: bool,
) -> Path:
    if "$" in value:
        if allow_dynamic:
            return root / "src" / "test"
        raise ConfigError(
            f"verified best-of-N cannot infer a dynamic {ecosystem} test source root; "
            "configure review_assets with every selected test directory"
        )
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ConfigError(f"{ecosystem} test source root is outside the workspace: {value}")
    candidate = (base / raw).absolute()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigError(
            f"{ecosystem} test source root is outside the workspace: {value}"
        ) from exc
    return candidate


def _protect_configured_test_roots(
    root: Path,
    candidates: set[Path],
    protected_patterns: set[str],
    configured_roots: set[Path],
) -> None:
    for directory in configured_roots:
        relative = directory.relative_to(root).as_posix()
        protected_patterns.add(f"{relative}/**")
        if not _lexists(directory):
            continue
        if is_link_or_reparse(directory) or not directory.is_dir():
            raise ConfigError(
                f"verifier test source root must be a regular directory: {relative}"
            )
        candidates.update(_regular_files(root, directory))


def _matching_paths(root: Path, pattern: str, available_paths: set[str]) -> list[str]:
    matches: list[str] = []
    for relative in sorted(available_paths):
        if not fnmatchcase(relative, pattern):
            continue
        candidate = root / relative
        if not _lexists(candidate):
            continue
        if is_link_or_reparse(candidate):
            matches.append(f"{relative}:symlink")
        elif candidate.is_file():
            matches.append(relative)
        elif candidate.is_dir():
            matches.append(f"{relative}:directory")
        else:
            matches.append(f"{relative}:special")
    return matches


def _git_relevant_paths(root: Path) -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in proc.stdout.split(b"\0")
        if value
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
