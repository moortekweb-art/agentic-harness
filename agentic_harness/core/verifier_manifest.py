"""Frozen verifier-asset manifests for verified tournament execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.verifiers.cargo import (
    _refuse_editable_rust_inline_tests,
    _RUST_DOC_COMMENT_FENCE,
    _RUST_INLINE_TEST_ATTRIBUTE,
    _rust_inline_tests,
)
from agentic_harness.core.verifiers.common import (
    _add_globs,
    _add_patterns,
    _ALWAYS_PROTECTED_TEST_DIRECTORIES,
    _configured_test_root,
    _file_sha256,
    _git_relevant_paths,
    _is_excluded_workspace_path,
    _lexists,
    _matching_paths,
    _protect_configured_test_roots,
    _regular_files,
    _repository_argument_path,
    _tracked_membership,
    is_link_or_reparse,
    require_lexical_regular_path,
)
from agentic_harness.core.verifiers.gradle import (
    _balanced_brace_body,
    _balanced_parenthesis_end,
    _gradle_alias_closures,
    _GRADLE_ALIAS_CLOSURE_SUFFIX,
    _GRADLE_DELEGATED_BUILD_LOGIC,
    _GRADLE_DIRECT_TEST_CONFIG,
    _GRADLE_GAP,
    _GRADLE_GAP_TOKEN,
    _gradle_lexical_mask,
    _GRADLE_POTENTIAL_CLOSURE,
    _GRADLE_SOURCE_ROOT_CALL,
    _GRADLE_SOURCE_ROOT_NAME,
    _GRADLE_TEST_ALIAS,
    _GRADLE_TEST_BLOCK,
    _gradle_test_roots,
    _gradle_unrecognized_alias_closure_openings,
    _literal_gradle_test_paths,
    _QUOTED_PATH,
    _refuse_delegated_gradle_build_logic,
    _supported_gradle_alias_receiver,
)
from agentic_harness.core.verifiers.maven import _maven_test_roots
from agentic_harness.core.verifiers.python import (
    _effective_pytest_configuration,
    _protect_pytest_configuration,
    _PYTEST_CONFIG_SOURCES,
    _pytest_configuration_error,
    _PYTEST_GLOB_CHARACTERS,
    _pytest_plugin_closure_is_unprovable,
    _pytest_testpath_targets,
    _pytest_testpaths,
    harden_python_module_commands,
)

__all__ = [
    "_ALWAYS_PROTECTED_TEST_DIRECTORIES",
    "_GRADLE_ALIAS_CLOSURE_SUFFIX",
    "_GRADLE_DELEGATED_BUILD_LOGIC",
    "_GRADLE_DIRECT_TEST_CONFIG",
    "_GRADLE_GAP",
    "_GRADLE_GAP_TOKEN",
    "_GRADLE_POTENTIAL_CLOSURE",
    "_GRADLE_SOURCE_ROOT_CALL",
    "_GRADLE_SOURCE_ROOT_NAME",
    "_GRADLE_TEST_ALIAS",
    "_GRADLE_TEST_BLOCK",
    "_PYTEST_CONFIG_SOURCES",
    "_PYTEST_GLOB_CHARACTERS",
    "_QUOTED_PATH",
    "_RUST_DOC_COMMENT_FENCE",
    "_RUST_INLINE_TEST_ATTRIBUTE",
    "_add_globs",
    "_add_patterns",
    "_balanced_brace_body",
    "_balanced_parenthesis_end",
    "_configured_test_root",
    "_effective_pytest_configuration",
    "_file_sha256",
    "_git_relevant_paths",
    "_gradle_alias_closures",
    "_gradle_lexical_mask",
    "_gradle_test_roots",
    "_gradle_unrecognized_alias_closure_openings",
    "_is_excluded_workspace_path",
    "_lexists",
    "_literal_gradle_test_paths",
    "_matching_paths",
    "_maven_test_roots",
    "_protect_configured_test_roots",
    "_protect_pytest_configuration",
    "_pytest_configuration_error",
    "_pytest_plugin_closure_is_unprovable",
    "_pytest_testpath_targets",
    "_pytest_testpaths",
    "_refuse_delegated_gradle_build_logic",
    "_refuse_editable_rust_inline_tests",
    "_regular_files",
    "_repository_argument_path",
    "_rust_inline_tests",
    "_supported_gradle_alias_receiver",
    "_tracked_membership",
    "freeze_verifier_assets",
    "harden_python_module_commands",
    "is_link_or_reparse",
    "require_lexical_regular_path",
    "verifier_asset_drift",
]


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
            _protect_pytest_configuration(
                root,
                candidates,
                protected_patterns,
                tracked_paths,
                allow_dynamic=bool(explicit_assets),
            )
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
            if not explicit_assets:
                _refuse_editable_rust_inline_tests(root, candidates, tracked_paths)
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
            if not explicit_assets:
                _refuse_delegated_gradle_build_logic(root)
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

    for directory_name in _ALWAYS_PROTECTED_TEST_DIRECTORIES:
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
