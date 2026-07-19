"""Frozen verifier-asset manifests for verified tournament execution."""

from __future__ import annotations

import hashlib
from fnmatch import fnmatchcase
import json
from pathlib import Path
import stat
import subprocess

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
    protected_paths: set[str] = set()
    protected_patterns: set[str] = set()
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
            relative = candidate.relative_to(root).as_posix()
            protected_patterns.add(f"{relative}/**")

    for command in review_commands:
        lowered = [Path(argument).name.lower() for argument in command]
        boundary_established = False
        repository_arguments: list[Path] = []
        for index, argument in enumerate(command):
            if not argument or argument.startswith("-"):
                continue
            path_text = argument.split("::", 1)[0]
            candidate = _repository_argument_path(root, path_text, executable=index == 0)
            if candidate is None:
                continue
            repository_arguments.append(candidate)
            require_lexical_regular_path(root, candidate, label=path_text)
            if candidate.is_file():
                candidates.add(candidate)
            elif candidate.is_dir():
                candidates.update(_regular_files(root, candidate))

        python_verifier = any(
            "pytest" in argument or argument == "unittest" for argument in lowered
        )
        if python_verifier:
            boundary_established = True
            python_paths = (
                "pyproject.toml",
                "pytest.ini",
                "tox.ini",
                "setup.cfg",
                "conftest.py",
                "pytest.py",
                "pytest",
                "unittest.py",
                "unittest",
                "sitecustomize.py",
                "usercustomize.py",
            )
            protected_paths.update(python_paths)
            protected_patterns.add("**/conftest.py")
            _add_patterns(root, candidates, python_paths)
        if any(argument in {"npm", "pnpm", "yarn", "bun"} for argument in lowered):
            boundary_established = True
            javascript_paths = (
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "bun.lock",
                "bun.lockb",
            )
            protected_paths.update(javascript_paths)
            _add_patterns(
                root,
                candidates,
                javascript_paths,
            )
        if "cargo" in lowered:
            boundary_established = True
            protected_paths.update(("Cargo.toml", "Cargo.lock"))
            protected_patterns.update(("**/Cargo.toml", "**/Cargo.lock"))
            _add_patterns(root, candidates, ("Cargo.toml", "Cargo.lock"))

        command_names = set(lowered)
        if "go" in command_names:
            boundary_established = True
            protected_paths.update(("go.mod", "go.sum", "go.work", "go.work.sum"))
            protected_patterns.update(
                ("**/go.mod", "**/go.sum", "**/go.work", "**/go.work.sum", "*_test.go", "**/*_test.go")
            )
            _add_patterns(root, candidates, ("go.mod", "go.sum", "go.work", "go.work.sum"))
            _add_globs(root, candidates, ("**/*_test.go",))
        if command_names & {"mvn", "mvnw", "mvnw.cmd", "mvnw.bat"}:
            boundary_established = True
            protected_paths.update(("mvnw", "mvnw.cmd", "mvnw.bat"))
            protected_patterns.update(("pom.xml", "**/pom.xml", ".mvn/**"))
            _add_patterns(root, candidates, ("mvnw", "mvnw.cmd", "mvnw.bat"))
            _add_globs(root, candidates, ("**/pom.xml", ".mvn/**/*"))
        if command_names & {"gradle", "gradlew", "gradlew.cmd", "gradlew.bat"}:
            boundary_established = True
            protected_paths.update(("gradlew", "gradlew.cmd", "gradlew.bat"))
            protected_patterns.update(
                (
                    "build.gradle",
                    "build.gradle.kts",
                    "settings.gradle",
                    "settings.gradle.kts",
                    "gradle.properties",
                    "**/build.gradle",
                    "**/build.gradle.kts",
                    "**/settings.gradle",
                    "**/settings.gradle.kts",
                    "**/gradle.properties",
                    "gradle/wrapper/**",
                )
            )
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
            dotnet_paths = (
                "global.json",
                "NuGet.config",
                "Directory.Build.props",
                "Directory.Build.targets",
                "Directory.Packages.props",
            )
            protected_paths.update(dotnet_paths)
            protected_patterns.update(
                (
                    "*.sln",
                    "*.slnx",
                    "*.csproj",
                    "*.fsproj",
                    "*.vbproj",
                    "*.props",
                    "*.targets",
                    "**/*.sln",
                    "**/*.slnx",
                    "**/*.csproj",
                    "**/*.fsproj",
                    "**/*.vbproj",
                    "**/*.props",
                    "**/*.targets",
                )
            )
            _add_patterns(
                root,
                candidates,
                dotnet_paths,
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
            protected_paths.update(("Gemfile", "Gemfile.lock", ".rspec", "Rakefile"))
            protected_patterns.update(("spec/**", "**/.rspec"))
            _add_patterns(root, candidates, ("Gemfile", "Gemfile.lock", ".rspec", "Rakefile"))
            _add_globs(root, candidates, ("spec/**/*.rb",))
        if repository_arguments and not boundary_established and not explicit_assets:
            raise ConfigError(
                "verified best-of-N cannot infer dependencies for a repository-local "
                "custom verifier; configure review_assets with the verifier and every "
                "repository-controlled dependency"
            )
        if explicit_assets:
            boundary_established = True
        if not boundary_established:
            raise ConfigError(
                "verified best-of-N cannot infer this verifier boundary; "
                "configure review_assets with every repository-controlled verifier input"
            )

    for directory_name in ("tests", "test", "spec", "specs"):
        protected_patterns.add(f"{directory_name}/**")
        directory = root / directory_name
        if is_link_or_reparse(directory):
            raise ConfigError(
                f"verifier asset must not be a symlink or reparse point: {directory}"
            )
        if directory.is_dir():
            candidates.update(_regular_files(root, directory))

    tracked = {root / relative for relative in tracked_paths}
    untracked_candidates = candidates - tracked
    if untracked_candidates:
        names = ", ".join(
            candidate.relative_to(root).as_posix()
            for candidate in sorted(untracked_candidates)
        )
        raise ConfigError(f"verifier assets must be tracked by Git: {names}")
    if not candidates:
        raise ConfigError(
            "verified best-of-N found no tracked verifier assets; "
            "configure review_assets with tracked verifier inputs"
        )
    manifest = [
        {
            "kind": "file",
            "path": candidate.relative_to(root).as_posix(),
            "sha256": _file_sha256(candidate),
        }
        for candidate in sorted(candidates)
    ]
    for relative in sorted(protected_paths):
        candidate = root / relative
        if candidate in candidates:
            continue
        if _lexists(candidate):
            if candidate.is_dir():
                protected_patterns.add(f"{relative}/**")
                continue
            raise ConfigError(f"verifier-sensitive path is not a tracked file: {relative}")
        manifest.append({"kind": "absent", "path": relative, "sha256": ""})
    for pattern in sorted(protected_patterns):
        members = _matching_paths(root, pattern, tracked_paths)
        manifest.append(
            {
                "kind": "membership",
                "path": f"@pattern:{pattern}",
                "pattern": pattern,
                "paths": json.dumps(members, separators=(",", ":")),
                "sha256": hashlib.sha256("\0".join(members).encode()).hexdigest(),
            }
        )
    return manifest


def verifier_asset_drift(
    worktree: Path,
    verifier_assets: list[dict[str, str]],
) -> list[str]:
    drift: list[str] = []
    relevant_paths = _git_relevant_paths(worktree)
    for asset in verifier_assets:
        kind = asset.get("kind", "file")
        if kind == "membership":
            pattern = asset["pattern"]
            expected = json.loads(asset.get("paths", "[]"))
            current = _matching_paths(worktree, pattern, relevant_paths)
            if current != expected:
                drift.extend(sorted(set(expected) ^ set(current)) or [f"pattern:{pattern}"])
            continue
        relative = asset["path"]
        candidate = worktree / relative
        if kind == "absent":
            if _lexists(candidate):
                drift.append(relative)
            continue
        try:
            require_lexical_regular_path(worktree, candidate, label=relative)
        except ConfigError:
            drift.append(relative)
            continue
        if not candidate.is_file() or _file_sha256(candidate) != asset["sha256"]:
            drift.append(relative)
    return sorted(set(drift))


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
