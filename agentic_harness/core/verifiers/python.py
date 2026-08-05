"""Pytest and unittest verifier-boundary hardening for verified tournaments."""

from __future__ import annotations

import configparser
from pathlib import Path
import tomllib

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.verifiers.common import (
    _is_excluded_workspace_path,
    _lexists,
    _regular_files,
    _tracked_membership,
    is_link_or_reparse,
    require_lexical_regular_path,
)

_PYTEST_CONFIG_SOURCES = (
    ("pytest.ini", "pytest"),
    ("pyproject.toml", "tool.pytest.ini_options"),
    ("tox.ini", "pytest"),
    ("setup.cfg", "tool:pytest"),
)
_PYTEST_GLOB_CHARACTERS = "*?["


def harden_python_module_commands(commands: list[list[str]]) -> list[list[str]]:
    """Prevent the repository root from shadowing pytest/unittest at interpreter start."""

    hardened: list[list[str]] = []
    for command in commands:
        updated = list(command)
        names = [Path(argument).name.lower() for argument in updated]
        executable = names[0] if names else ""
        module_index = updated.index("-m") if "-m" in updated else -1
        module = names[module_index + 1] if 0 <= module_index < len(names) - 1 else ""
        if (
            executable.startswith("python")
            and module in {"pytest", "unittest"}
            and "-P" not in updated[1:module_index]
            and "-I" not in updated[1:module_index]
        ):
            updated.insert(1, "-P")
        hardened.append(updated)
    return hardened


def _protect_pytest_configuration(
    root: Path,
    candidates: set[Path],
    protected_patterns: set[str],
    tracked_paths: set[str],
    *,
    allow_dynamic: bool,
) -> None:
    configuration = _effective_pytest_configuration(root, allow_dynamic=allow_dynamic)
    if not allow_dynamic and _pytest_plugin_closure_is_unprovable(
        root,
        configuration,
        tracked_paths,
    ):
        raise ConfigError(
            "verified best-of-N cannot prove the Pytest plugin dependency closure; "
            "configure review_assets with every loaded plugin module and other "
            "repository-controlled verifier input"
        )
    for entry in _pytest_testpaths(configuration, allow_dynamic=allow_dynamic):
        targets = _pytest_testpath_targets(root, entry, tracked_paths)
        if not targets:
            if allow_dynamic:
                continue
            raise ConfigError(
                "verified best-of-N cannot resolve a Pytest testpaths entry to bounded "
                f"tracked verifier inputs: {entry}; configure review_assets with every "
                "repository-controlled verifier input"
            )
        for target in targets:
            require_lexical_regular_path(root, target, label=entry)
            relative = target.relative_to(root).as_posix()
            if target.is_dir():
                protected_patterns.add(f"{relative}/**")
                candidates.update(_regular_files(root, target))
            elif target.is_file():
                candidates.add(target)


def _effective_pytest_configuration(
    root: Path,
    *,
    allow_dynamic: bool,
) -> dict[str, object]:
    for name, section in _PYTEST_CONFIG_SOURCES:
        path = root / name
        if not _lexists(path) or is_link_or_reparse(path) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            if allow_dynamic:
                return {}
            raise _pytest_configuration_error(name) from exc
        if name == "pyproject.toml":
            try:
                document = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                if allow_dynamic:
                    return {}
                raise _pytest_configuration_error(name) from exc
            options: object = document
            for key in section.split("."):
                options = options.get(key) if isinstance(options, dict) else None
            if isinstance(options, dict):
                return dict(options)
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text, source=name)
        except configparser.Error as exc:
            if allow_dynamic:
                return {}
            raise _pytest_configuration_error(name) from exc
        if parser.has_section(section):
            return dict(parser[section])
        # pytest stops at pytest.ini even when it declares no options.
        if name == "pytest.ini":
            return {}
    return {}


def _pytest_configuration_error(name: str) -> ConfigError:
    return ConfigError(
        f"verified best-of-N cannot inspect the Pytest verifier configuration: {name}; "
        "configure review_assets with every repository-controlled verifier input"
    )


def _pytest_testpaths(
    configuration: dict[str, object],
    *,
    allow_dynamic: bool,
) -> list[str]:
    value = configuration.get("testpaths")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return [str(entry) for entry in value]
    if allow_dynamic:
        return []
    raise ConfigError(
        "verified best-of-N cannot infer a dynamic Pytest testpaths declaration; "
        "configure review_assets with every repository-controlled verifier input"
    )


def _pytest_testpath_targets(
    root: Path,
    entry: str,
    tracked_paths: set[str],
) -> list[Path]:
    normalized = entry.replace("\\", "/").strip()
    if not normalized:
        return []
    raw = Path(normalized)
    if raw.is_absolute() or ".." in raw.parts:
        raise ConfigError(f"Pytest testpaths entry is outside the workspace: {entry}")
    pattern = raw.as_posix()
    if pattern == ".":
        return []
    if any(character in pattern for character in _PYTEST_GLOB_CHARACTERS):
        matches = sorted(root.glob(pattern))
    else:
        candidate = root / raw
        matches = [candidate] if _lexists(candidate) else []
    return [
        match
        for match in matches
        if not _is_excluded_workspace_path(match)
        and _tracked_membership(match.relative_to(root).as_posix(), tracked_paths)
    ]


def _pytest_plugin_closure_is_unprovable(
    root: Path,
    configuration: dict[str, object],
    tracked_paths: set[str],
) -> bool:
    addopts = configuration.get("addopts")
    tokens: list[str] = []
    if isinstance(addopts, str):
        tokens = addopts.split()
    elif isinstance(addopts, list):
        tokens = [str(entry) for entry in addopts]
    if any(
        token.startswith("-p") and not token.startswith("--") for token in tokens
    ):
        return True
    for relative in sorted(tracked_paths):
        if Path(relative).name != "conftest.py":
            continue
        candidate = root / relative
        if is_link_or_reparse(candidate) or not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(
                f"unable to inspect Pytest verifier inputs: {relative}"
            ) from exc
        if "pytest_plugins" in text:
            return True
    return False
