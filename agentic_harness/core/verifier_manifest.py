"""Frozen verifier-asset manifests for verified tournament execution."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat

from agentic_harness.core.config import CONFIG_DIR
from agentic_harness.core.errors import ConfigError


def freeze_verifier_assets(
    root: Path,
    review_commands: list[list[str]],
    *,
    review_assets: list[str] | None,
    tracked_paths: set[str],
) -> list[dict[str, str]]:
    """Hash tracked verifier inputs or refuse an unbounded custom verifier."""

    candidates: set[Path] = set()
    explicit_assets = review_assets or []
    for asset in explicit_assets:
        candidate = _repository_argument_path(root, asset, executable=False)
        if candidate is None:
            raise ConfigError(f"review_assets entry does not exist: {asset}")
        require_lexical_regular_path(root, candidate, label=asset)
        if candidate.is_file():
            candidates.add(candidate)
        elif candidate.is_dir():
            candidates.update(_regular_files(root, candidate))

    for command in review_commands:
        lowered = [Path(argument).name.lower() for argument in command]
        boundary_established = bool(explicit_assets)
        for index, argument in enumerate(command):
            if not argument or argument.startswith("-"):
                continue
            path_text = argument.split("::", 1)[0]
            candidate = _repository_argument_path(root, path_text, executable=index == 0)
            if candidate is None:
                continue
            boundary_established = True
            require_lexical_regular_path(root, candidate, label=path_text)
            if candidate.is_file():
                candidates.add(candidate)
            elif candidate.is_dir():
                candidates.update(_regular_files(root, candidate))

        if any("pytest" in argument or argument == "unittest" for argument in lowered):
            boundary_established = True
            _add_patterns(root, candidates, ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"))
        if any(argument in {"npm", "pnpm", "yarn", "bun"} for argument in lowered):
            boundary_established = True
            _add_patterns(
                root,
                candidates,
                (
                    "package.json",
                    "package-lock.json",
                    "pnpm-lock.yaml",
                    "yarn.lock",
                    "bun.lock",
                    "bun.lockb",
                ),
            )
        if "cargo" in lowered:
            boundary_established = True
            _add_patterns(root, candidates, ("Cargo.toml", "Cargo.lock"))

        command_names = set(lowered)
        if "go" in command_names:
            boundary_established = True
            _add_patterns(root, candidates, ("go.mod", "go.sum", "go.work", "go.work.sum"))
            _add_globs(root, candidates, ("**/*_test.go",))
        if command_names & {"mvn", "mvnw", "mvnw.cmd", "mvnw.bat"}:
            boundary_established = True
            _add_patterns(root, candidates, ("mvnw", "mvnw.cmd", "mvnw.bat"))
            _add_globs(root, candidates, ("**/pom.xml", ".mvn/**/*"))
        if command_names & {"gradle", "gradlew", "gradlew.cmd", "gradlew.bat"}:
            boundary_established = True
            _add_patterns(root, candidates, ("gradlew", "gradlew.cmd", "gradlew.bat"))
            _add_globs(
                root,
                candidates,
                (
                    "**/build.gradle",
                    "**/build.gradle.kts",
                    "**/settings.gradle",
                    "**/settings.gradle.kts",
                    "**/gradle.properties",
                    "gradle/wrapper/**/*",
                ),
            )
        if "dotnet" in command_names:
            boundary_established = True
            _add_patterns(
                root,
                candidates,
                (
                    "global.json",
                    "NuGet.config",
                    "Directory.Build.props",
                    "Directory.Build.targets",
                    "Directory.Packages.props",
                ),
            )
            _add_globs(
                root,
                candidates,
                (
                    "**/*.sln",
                    "**/*.slnx",
                    "**/*.csproj",
                    "**/*.fsproj",
                    "**/*.vbproj",
                    "**/*.props",
                    "**/*.targets",
                ),
            )
        if "rspec" in command_names:
            boundary_established = True
            _add_patterns(root, candidates, ("Gemfile", "Gemfile.lock", ".rspec", "Rakefile"))
            _add_globs(root, candidates, ("spec/**/*.rb",))
        if not boundary_established:
            raise ConfigError(
                "verified best-of-N cannot infer this verifier boundary; "
                "configure review_assets with every repository-controlled verifier input"
            )

    for directory_name in ("tests", "test", "spec", "specs"):
        directory = root / directory_name
        if is_link_or_reparse(directory):
            raise ConfigError(
                f"verifier asset must not be a symlink or reparse point: {directory}"
            )
        if directory.is_dir():
            candidates.update(_regular_files(root, directory))

    tracked = {root / relative for relative in tracked_paths}
    candidates.intersection_update(tracked)
    if not candidates:
        raise ConfigError(
            "verified best-of-N found no tracked verifier assets; "
            "configure review_assets with tracked verifier inputs"
        )
    return [
        {
            "path": candidate.relative_to(root).as_posix(),
            "sha256": _file_sha256(candidate),
        }
        for candidate in sorted(candidates)
    ]


def verifier_asset_drift(
    worktree: Path,
    verifier_assets: list[dict[str, str]],
) -> list[str]:
    drift: list[str] = []
    for asset in verifier_assets:
        relative = asset["path"]
        candidate = worktree / relative
        try:
            require_lexical_regular_path(worktree, candidate, label=relative)
        except ConfigError:
            drift.append(relative)
            continue
        if not candidate.is_file() or _file_sha256(candidate) != asset["sha256"]:
            drift.append(relative)
    return drift


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
