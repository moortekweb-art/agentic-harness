"""Shared goal safety metadata for CLI and GUI starts."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any


SAFE_SUBPROCESS_ENV = {
    "CI",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
}


def split_command(text: str, *, windows: bool | None = None) -> list[str]:
    """Split a command line using the target platform's quoting rules."""

    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return shlex.split(text)
    result: list[str] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index] in " \t":
            index += 1
        if index >= len(text):
            break
        argument: list[str] = []
        quoted = False
        while index < len(text) and (quoted or text[index] not in " \t"):
            if text[index] == "\\":
                start = index
                while index < len(text) and text[index] == "\\":
                    index += 1
                backslashes = index - start
                if index < len(text) and text[index] == '"':
                    argument.extend("\\" * (backslashes // 2))
                    if backslashes % 2:
                        argument.append('"')
                    else:
                        quoted = not quoted
                    index += 1
                else:
                    argument.extend("\\" * backslashes)
                continue
            if text[index] == '"':
                if quoted and index + 1 < len(text) and text[index + 1] == '"':
                    argument.append('"')
                    index += 2
                else:
                    quoted = not quoted
                    index += 1
                continue
            argument.append(text[index])
            index += 1
        result.append("".join(argument))
    return result


def format_command(command: list[str], *, windows: bool | None = None) -> str:
    """Format argv so the matching parser can reconstruct it exactly."""

    is_windows = os.name == "nt" if windows is None else windows
    return subprocess.list2cmdline(command) if is_windows else shlex.join(command)


def subprocess_environment(secret_env_names: Iterable[object] = ()) -> dict[str, str]:
    """Build the minimal environment used by workspace subprocesses."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in SAFE_SUBPROCESS_ENV
    }
    env.setdefault("PATH", os.defpath)
    for name in secret_env_names:
        env.pop(str(name), None)
    return env


def goal_safety_metadata(
    project_dir: Path,
    *,
    allowed_paths: list[str],
    review_commands: list[list[str]],
    path_enforcement: bool,
    secret_env_names: list[str],
    interface: str,
) -> dict[str, Any]:
    normalized_paths: list[str] = []
    root = project_dir.resolve()
    for value in allowed_paths:
        candidate = Path(value)
        if candidate.is_absolute():
            raise ValueError("allowed paths must be relative to the workspace")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"allowed path is outside the workspace: {value}") from exc
        normalized_paths.append(candidate.as_posix())
    checks = [
        {
            "id": f"check-{index}",
            "label": " ".join(command),
            "argv": list(command),
        }
        for index, command in enumerate(review_commands, 1)
    ]
    return {
        "interface": interface,
        "safety": {
            "allowed_paths": normalized_paths,
            "checks": checks,
            "path_enforcement": path_enforcement,
            "secret_env_names": [name for name in secret_env_names if name],
            "preexisting_changes": git_changes(project_dir),
        },
    }


def git_changes(project_dir: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=project_dir,
            env=subprocess_environment(),
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    result: list[str] = []
    records = proc.stdout.split(b"\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            continue
        status = record[:2]
        path = os.fsdecode(record[3:])
        if path:
            result.append(path)
        if b"R" in status or b"C" in status:
            if index < len(records) and records[index]:
                original = os.fsdecode(records[index])
                if original:
                    result.append(original)
            index += 1
    return result
