"""Frozen verifier-asset manifests for verified tournament execution."""

from __future__ import annotations

import hashlib
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import stat
import subprocess
import xml.etree.ElementTree as ET

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
                relative = candidate.relative_to(root).as_posix()
                protected_patterns.add(f"{relative}/**")

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
            if not explicit_assets:
                raise ConfigError(
                    "verified best-of-N treats package-manager test scripts as opaque; "
                    "configure review_assets with the script and every "
                    "repository-controlled dependency"
                )
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
            protected_patterns.update(
                ("pom.xml", "**/pom.xml", ".mvn/**", "src/test/**", "**/src/test/**")
            )
            _add_patterns(root, candidates, ("mvnw", "mvnw.cmd", "mvnw.bat"))
            _add_globs(
                root,
                candidates,
                ("**/pom.xml", ".mvn/**/*", "src/test/**/*", "**/src/test/**/*"),
            )
            _protect_configured_test_roots(
                root,
                candidates,
                protected_patterns,
                _maven_test_roots(root, allow_dynamic=bool(explicit_assets)),
            )
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
                    "gradle/**",
                    "**/gradle/**",
                    "src/test/**",
                    "**/src/test/**",
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
                    "gradle/**/*",
                    "**/gradle/**/*",
                    "src/test/**/*",
                    "**/src/test/**/*",
                ),
            )
            _protect_configured_test_roots(
                root,
                candidates,
                protected_patterns,
                _gradle_test_roots(root, allow_dynamic=bool(explicit_assets)),
            )
        if "dotnet" in command_names:
            if not explicit_assets:
                raise ConfigError(
                    "verified best-of-N cannot infer the evaluated MSBuild input closure "
                    "for dotnet test; configure review_assets with every selected test "
                    "project, source directory, imported build file, and other "
                    "repository-controlled dependency"
                )
            boundary_established = True
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


_GRADLE_TEST_BLOCK = re.compile(
    r"""(?x)
    (?:
        \btest
        |
        \b(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    (?:\s*\.\s*get\s*\(\s*\))?
    (?:\s*\.\s*(?:configure|apply))?
    \s*\{
    """
)
_GRADLE_DIRECT_TEST_CONFIG = re.compile(
    r"""(?x)
    \bsourceSets\s*
    (?:
        \.\s*test
        |
        \[\s*["']test["']\s*\]
        |
        \.\s*(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    [^\n;]*
    """
)
_GRADLE_SOURCE_ROOT_CALL = re.compile(
    r"\b(?:java|kotlin|resources)\s*"
    r"(?:\.\s*|\{\s*)"
    r"(?:srcDirs?|setSrcDirs)\b(?P<expression>[^\n;}]*)"
)
_GRADLE_SOURCE_ROOT_NAME = re.compile(r"\b(?:srcDirs?|setSrcDirs)\b")
_GRADLE_TEST_ALIAS = re.compile(
    r"""(?x)
    (?:
        \b(?:val|var|def)\s+
        |
        (?<![A-Za-z0-9_$.])
        (?:[A-Za-z_][A-Za-z0-9_$.<>,?]*\s+)+
    )
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s*:\s*[A-Za-z_][A-Za-z0-9_$.<>,?\s]*)?
    \s*=\s*
    \bsourceSets\s*
    (?:
        \.\s*test
        |
        \[\s*["']test["']\s*\]
        |
        \.\s*(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    """
)
_QUOTED_PATH = re.compile(r"""(?P<quote>['"])(?P<path>[^'"]+)(?P=quote)""")


def _maven_test_roots(root: Path, *, allow_dynamic: bool) -> set[Path]:
    configured: set[Path] = set()
    for pom in sorted(root.glob("**/pom.xml")):
        require_lexical_regular_path(root, pom, label=str(pom))
        try:
            document = ET.parse(pom)
        except (ET.ParseError, OSError) as exc:
            raise ConfigError(f"unable to inspect Maven verifier inputs: {pom}") from exc
        for element in document.iter():
            if element.tag.rsplit("}", 1)[-1] != "testSourceDirectory":
                continue
            value = (element.text or "").strip()
            if not value:
                continue
            configured.add(
                _configured_test_root(
                    root,
                    pom.parent,
                    value,
                    ecosystem="Maven",
                    allow_dynamic=allow_dynamic,
                )
            )
        for test_resources in (
            element
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == "testResources"
        ):
            for resource in test_resources:
                if resource.tag.rsplit("}", 1)[-1] != "testResource":
                    continue
                for child in resource:
                    if child.tag.rsplit("}", 1)[-1] != "directory":
                        continue
                    value = (child.text or "").strip()
                    if value:
                        configured.add(
                            _configured_test_root(
                                root,
                                pom.parent,
                                value,
                                ecosystem="Maven",
                                allow_dynamic=allow_dynamic,
                            )
                        )
        for execution in (
            element
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == "execution"
        ):
            goals = {
                (goal.text or "").strip()
                for goal in execution.iter()
                if goal.tag.rsplit("}", 1)[-1] == "goal"
            }
            selected_tags: set[str] = set()
            if "add-test-source" in goals:
                selected_tags.add("source")
            if "add-test-resource" in goals:
                selected_tags.add("directory")
            if not selected_tags:
                continue
            for child in execution.iter():
                if child.tag.rsplit("}", 1)[-1] not in selected_tags:
                    continue
                value = (child.text or "").strip()
                if value:
                    configured.add(
                        _configured_test_root(
                            root,
                            pom.parent,
                            value,
                            ecosystem="Maven",
                            allow_dynamic=allow_dynamic,
                        )
                    )
    return configured


def _gradle_test_roots(root: Path, *, allow_dynamic: bool) -> set[Path]:
    configured: set[Path] = set()
    for build_file in sorted(
        {
            *root.glob("**/build.gradle"),
            *root.glob("**/build.gradle.kts"),
        }
    ):
        require_lexical_regular_path(root, build_file, label=str(build_file))
        try:
            text = build_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"unable to inspect Gradle verifier inputs: {build_file}") from exc
        expressions: list[str] = []
        unresolved_source_root = False
        for match in _GRADLE_TEST_BLOCK.finditer(text):
            block = _balanced_brace_body(text, match.end() - 1)
            source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(block))
            expressions.extend(
                item.group("expression")
                for item in source_matches
            )
            unresolved_source_root = unresolved_source_root or (
                len(_GRADLE_SOURCE_ROOT_NAME.findall(block)) != len(source_matches)
            )
        for item in _GRADLE_DIRECT_TEST_CONFIG.finditer(text):
            direct_config = item.group(0)
            source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(direct_config))
            expressions.extend(
                source_match.group("expression") for source_match in source_matches
            )
            unresolved_source_root = unresolved_source_root or (
                len(_GRADLE_SOURCE_ROOT_NAME.findall(direct_config))
                != len(source_matches)
            )
        for alias_match in _GRADLE_TEST_ALIAS.finditer(text):
            alias = re.escape(alias_match.group("name"))
            gradle_gap = (
                r"(?:\s|//[^\n]*(?:\n|$)|/\*(?:[^*]|\*(?!/))*\*/)*"
            )
            direct_alias = re.compile(
                rf"\b{alias}\s*(?:\.\s*get\s*\(\s*\))?\s*\.\s*"
                r"(?:java|kotlin|resources)\s*\.\s*"
                r"(?:srcDirs?|setSrcDirs)\b(?P<expression>[^\n;}]*)"
            )
            alias_closure = re.compile(
                rf"\b{alias}\b{gradle_gap}"
                rf"(?:\.{gradle_gap}get{gradle_gap}"
                rf"\({gradle_gap}\){gradle_gap})?"
                rf"\.{gradle_gap}"
                r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)"
                rf"{gradle_gap}(?:\([^{{}};]*\){gradle_gap})?\{{"
            )
            for direct_match in direct_alias.finditer(text):
                expressions.append(direct_match.group("expression"))
            for closure_match in alias_closure.finditer(text):
                block = _balanced_brace_body(text, closure_match.end() - 1)
                source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(block))
                if closure_match.group("method") in {"configure", "apply"}:
                    expressions.extend(
                        source_match.group("expression")
                        for source_match in source_matches
                    )
                    unresolved_source_root = unresolved_source_root or (
                        len(_GRADLE_SOURCE_ROOT_NAME.findall(block))
                        != len(source_matches)
                    )
                elif _GRADLE_SOURCE_ROOT_NAME.search(block):
                    unresolved_source_root = True
            for line in text.splitlines():
                if not re.search(rf"\b{alias}\b", line):
                    continue
                alias_source_matches = list(direct_alias.finditer(line))
                unresolved_source_root = unresolved_source_root or (
                    len(_GRADLE_SOURCE_ROOT_NAME.findall(line))
                    != len(alias_source_matches)
                )
        if unresolved_source_root and not allow_dynamic:
            raise ConfigError(
                "verified best-of-N cannot infer a Gradle test source root syntax; "
                "configure review_assets with every selected test directory"
            )
        for expression in expressions:
            paths = _literal_gradle_test_paths(expression)
            if paths is None and not allow_dynamic:
                raise ConfigError(
                    "verified best-of-N cannot infer a dynamic Gradle test source root; "
                    "configure review_assets with every selected test directory"
                )
            for value in paths or []:
                configured.add(
                    _configured_test_root(
                        root,
                        build_file.parent,
                        value,
                        ecosystem="Gradle",
                        allow_dynamic=allow_dynamic,
                    )
                )
    return configured


def _literal_gradle_test_paths(expression: str) -> list[str] | None:
    paths = [match.group("path") for match in _QUOTED_PATH.finditer(expression)]
    scrubbed = _QUOTED_PATH.sub("", expression)
    scrubbed = re.sub(r"\b(?:files|listOf|setOf)\b", "", scrubbed)
    if "$" in scrubbed or re.search(r"[A-Za-z_]", scrubbed):
        return None
    return paths or None


def _balanced_brace_body(text: str, opening: int) -> str:
    depth = 0
    for index in range(opening, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index]
    return text[opening + 1 :]


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
