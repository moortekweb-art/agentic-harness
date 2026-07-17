"""Tests for config loading, type parsing, worker validation, and defaults.

Covers the main `load_config()` path: version checks, unknown keys, worker
validation, type coercion (str/int/float/bool/list), required-field enforcement
per worker type, and default values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_harness.core import config as config_module
from agentic_harness.core.config import (
    ConfigError,
    HarnessConfig,
    detect_review_command,
    load_config,
    write_default_config,
    write_tool_config,
)


def _write_config(tmp_path: Path, content: str) -> None:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(content, encoding="utf-8")


# --- Version and structure checks ---


def test_missing_version_raises(tmp_path) -> None:
    """A config without version must raise ConfigError."""
    _write_config(tmp_path, "worker: noop\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "version" in str(exc_info.value)


def test_empty_config_raises(tmp_path) -> None:
    """A completely empty config (None after YAML parse) must raise."""
    _write_config(tmp_path, "")
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "version" in str(exc_info.value)


def test_non_mapping_config_raises(tmp_path) -> None:
    """A YAML list at the root must raise ConfigError."""
    _write_config(tmp_path, "- item1\n- item2\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "mapping" in str(exc_info.value)


def test_unknown_key_raises(tmp_path) -> None:
    """An unknown top-level key must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
bogus_key: some_value
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "unknown" in str(exc_info.value)
    assert "bogus_key" in str(exc_info.value)


def test_unsupported_version_raises(tmp_path) -> None:
    """A config with version 99 must raise ConfigError."""
    _write_config(tmp_path, "version: 99\nworker: noop\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "unsupported" in str(exc_info.value)


def test_missing_config_returns_defaults(tmp_path) -> None:
    """No config file should return a HarnessConfig with defaults."""
    config = load_config(tmp_path)
    assert config.worker == "noop"
    assert config.shell_command == []
    assert config.coding_agent_timeout == 1800
    assert config.allow_noop_success is False
    assert config.github_ref == "main"
    assert config.config_path == tmp_path / ".agentic-harness" / "config.yml"


# --- Type parsing ---


def test_int_field_accepts_integer(tmp_path) -> None:
    """coding_agent_timeout as an integer should load correctly."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
coding_agent_timeout: 3600
""",
    )
    config = load_config(tmp_path)
    assert config.coding_agent_timeout == 3600


def test_int_field_rejects_boolean(tmp_path) -> None:
    """coding_agent_timeout as a boolean must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
coding_agent_timeout: true
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "integer" in str(exc_info.value)
    assert "boolean" in str(exc_info.value)


def test_int_field_accepts_string_number(tmp_path) -> None:
    """coding_agent_timeout as a numeric string should be coerced to int."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
coding_agent_timeout: "120"
""",
    )
    config = load_config(tmp_path)
    assert config.coding_agent_timeout == 120


def test_int_field_rejects_non_numeric_string(tmp_path) -> None:
    """coding_agent_timeout as a non-numeric string must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
coding_agent_timeout: "not-a-number"
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "integer" in str(exc_info.value)


def test_float_field_accepts_float(tmp_path) -> None:
    """llm_retry_delay as a float should load correctly."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
llm_retry_delay: 2.5
""",
    )
    config = load_config(tmp_path)
    assert config.llm_retry_delay == 2.5


def test_float_field_accepts_integer(tmp_path) -> None:
    """llm_retry_delay as an integer should be coerced to float."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
llm_retry_delay: 3
""",
    )
    config = load_config(tmp_path)
    assert config.llm_retry_delay == 3.0


def test_bool_field_accepts_true_string(tmp_path) -> None:
    """github_wait as 'true' string should load as True."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
github_wait: true
""",
    )
    config = load_config(tmp_path)
    assert config.github_wait is True


def test_bool_field_accepts_yes_string(tmp_path) -> None:
    """github_wait as 'yes' string should load as True."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
github_wait: yes
""",
    )
    config = load_config(tmp_path)
    assert config.github_wait is True


def test_bool_field_accepts_one_string(tmp_path) -> None:
    """github_wait as '1' string should load as True."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
github_wait: "1"
""",
    )
    config = load_config(tmp_path)
    assert config.github_wait is True


def test_bool_field_accepts_false_string(tmp_path) -> None:
    """github_wait as 'false' string should load as False."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
github_wait: false
""",
    )
    config = load_config(tmp_path)
    assert config.github_wait is False


def test_bool_field_rejects_invalid_string(tmp_path) -> None:
    """github_wait as 'maybe' must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
github_wait: maybe
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "true or false" in str(exc_info.value)


def test_list_field_accepts_list(tmp_path) -> None:
    """shell_command as a list of strings should load correctly."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - python
  - -c
  - "print('hello')"
""",
    )
    config = load_config(tmp_path)
    assert config.shell_command == ["python", "-c", "print('hello')"]


def test_list_field_rejects_non_list(tmp_path) -> None:
    """shell_command as a string must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command: "not a list"
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "list" in str(exc_info.value)


def test_list_field_rejects_empty_list(tmp_path) -> None:
    """An empty shell_command list must raise ConfigError (via worker validation)."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command: []
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    # Empty list is caught by the shell worker required-field check,
    # which says "shell worker requires shell_command".
    assert "shell_command" in str(exc_info.value)


def test_list_field_accepts_integer_items(tmp_path) -> None:
    """List items that are integers should be coerced to strings."""
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
review_command:
  - python
  - -m
  - pytest
""",
    )
    config = load_config(tmp_path)
    assert config.review_command == ["python", "-m", "pytest"]


def test_nested_review_coverage_is_loaded_before_checks_run(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
review:
  command: [python, -m, pytest]
  covers: [R1, R3]
""",
    )

    config = load_config(tmp_path)

    assert config.review_covers == ["R1", "R3"]


def test_review_coverage_may_be_explicitly_empty(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
review:
  command: [python, -c, "raise SystemExit(0)"]
  covers: []
""",
    )

    assert load_config(tmp_path).review_covers == []


def test_review_command_omission_does_not_grant_wildcard_coverage(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
review_command: [python, -c, "raise SystemExit(0)"]
""",
    )

    assert load_config(tmp_path).review_covers == []


def test_duplicate_review_coverage_is_rejected(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: noop
review_covers: [R1, R1]
""",
    )

    with pytest.raises(ConfigError, match="duplicate"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "mode",
    ["check_gated", "specification_frozen", "high_assurance"],
)
def test_named_assurance_modes_are_accepted(tmp_path, mode: str) -> None:
    _write_config(
        tmp_path,
        f"version: 1\nworker: noop\nassurance_mode: {mode}\n",
    )

    assert load_config(tmp_path).assurance_mode == mode


def test_unknown_assurance_mode_is_rejected(tmp_path) -> None:
    _write_config(
        tmp_path,
        "version: 1\nworker: noop\nassurance_mode: thorough\n",
    )

    with pytest.raises(ConfigError, match="assurance_mode"):
        load_config(tmp_path)


# --- Worker validation ---


def test_unsupported_worker_raises(tmp_path) -> None:
    """An unsupported worker type must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: magic_box
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "unsupported worker" in str(exc_info.value)


def test_shell_worker_requires_shell_command(tmp_path) -> None:
    """Shell worker without shell_command must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "shell_command" in str(exc_info.value)


def test_coding_agent_worker_requires_command(tmp_path) -> None:
    """Coding agent worker without coding_agent_command must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker: coding_agent
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "coding_agent_command" in str(exc_info.value)


def test_tmux_worker_requires_tmux_command(tmp_path) -> None:
    """Tmux worker without tmux_command must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: tmux
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "tmux_command" in str(exc_info.value)


def test_local_llm_worker_requires_endpoint(tmp_path) -> None:
    """Local LLM worker without llm_endpoint must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: local_llm
llm_model: gemma4
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "llm_endpoint" in str(exc_info.value)


def test_local_llm_worker_requires_model(tmp_path) -> None:
    """Local LLM worker without llm_model must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: local_llm
llm_endpoint: http://localhost:8008
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "llm_model" in str(exc_info.value)


def test_github_actions_worker_requires_owner(tmp_path) -> None:
    """GitHub Actions worker without github_owner must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_repo: myrepo
github_workflow_id: ci.yml
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "github_owner" in str(exc_info.value)


def test_github_actions_worker_requires_repo(tmp_path) -> None:
    """GitHub Actions worker without github_repo must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: myorg
github_workflow_id: ci.yml
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "github_repo" in str(exc_info.value)


def test_github_actions_worker_requires_workflow_id(tmp_path) -> None:
    """GitHub Actions worker without github_workflow_id must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: myorg
github_repo: myrepo
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "github_workflow_id" in str(exc_info.value)


# --- Worker dict (nested worker) ---


def test_nested_worker_shell_works(tmp_path) -> None:
    """Worker dict with type=shell and shell_command should load."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: shell
  shell_command:
    - echo
    - hello
""",
    )
    config = load_config(tmp_path)
    assert config.worker == "shell"
    assert config.shell_command == ["echo", "hello"]


def test_nested_worker_coding_agent_works(tmp_path) -> None:
    """Worker dict with type=coding_agent and coding_agent_command should load."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - opencode
    - run
    - test
  coding_agent_timeout: 600
""",
    )
    config = load_config(tmp_path)
    assert config.worker == "coding_agent"
    assert config.coding_agent_command == ["opencode", "run", "test"]
    assert config.coding_agent_timeout == 600


def test_nested_worker_unknown_type_raises(tmp_path) -> None:
    """Worker dict with unsupported type must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: magic_box
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "unsupported worker" in str(exc_info.value)


def test_nested_worker_unknown_key_raises(tmp_path) -> None:
    """Worker dict with unknown key must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: noop
  magic_key: value
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "unknown" in str(exc_info.value)
    assert "magic_key" in str(exc_info.value)


# --- write_default_config and write_tool_config ---


def test_write_default_config_creates_file(tmp_path) -> None:
    """write_default_config should create .agentic-harness/config.yml."""
    path = write_default_config(tmp_path)
    assert path.exists()
    assert path.name == "config.yml"
    content = path.read_text(encoding="utf-8")
    assert "version: 1" in content
    assert "worker: noop" in content


def test_write_default_config_no_overwrite(tmp_path) -> None:
    """write_default_config should not overwrite existing config."""
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    existing = config_dir / "config.yml"
    existing.write_text("version: 1\nworker: shell\n", encoding="utf-8")
    write_default_config(tmp_path)
    content = existing.read_text(encoding="utf-8")
    assert "worker: shell" in content


def test_write_tool_config_shell(tmp_path) -> None:
    """write_tool_config with tool=shell should create valid config."""
    path = write_tool_config(tmp_path, tool="shell")
    assert path.exists()
    config = load_config(tmp_path)
    assert config.worker == "shell"
    assert config.shell_command == [
        "python",
        "-c",
        "print('shell worker placeholder: edit .agentic-harness/config.yml to run your own command')",
    ]


def test_write_tool_config_opencode(tmp_path) -> None:
    """write_tool_config with tool=opencode should create valid config."""
    path = write_tool_config(tmp_path, tool="opencode")
    assert path.exists()
    config = load_config(tmp_path)
    assert config.worker == "coding_agent"
    assert Path(config.coding_agent_command[0]).stem.lower() == "opencode"
    assert config.coding_agent_command[1:] == ["run", "{objective}"]


def test_write_tool_config_materializes_the_discovered_agent_executable(
    tmp_path,
    monkeypatch,
) -> None:
    resolved = r"C:\Users\Michael\AppData\Roaming\npm\codex.cmd"
    monkeypatch.setattr(
        "agentic_harness.core.safety.shutil.which",
        lambda executable: resolved if executable == "codex" else None,
    )

    write_tool_config(tmp_path, tool="codex")

    assert load_config(tmp_path).coding_agent_command[0] == resolved


def test_write_tool_config_detects_node_review_command(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"}}',
        encoding="utf-8",
    )

    write_tool_config(tmp_path, tool="codex")

    assert load_config(tmp_path).review_command == ["npm", "test"]


def test_write_tool_config_detects_rust_review_command(tmp_path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")

    write_tool_config(tmp_path, tool="aider")

    assert load_config(tmp_path).review_command == ["cargo", "test"]


def test_detect_review_command_ignores_placeholder_npm_and_bare_pyproject(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"echo \\"Error: no test specified\\" && exit 1"}}',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    assert detect_review_command(tmp_path) == []


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("mvnw", ["./mvnw", "test"]),
        ("gradlew", ["./gradlew", "test"]),
    ],
)
def test_detect_review_command_prefers_project_build_wrappers(
    tmp_path,
    monkeypatch,
    marker,
    expected,
) -> None:
    monkeypatch.setattr(config_module, "_IS_WINDOWS", False)
    if marker == "mvnw":
        (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    else:
        (tmp_path / "build.gradle").write_text("plugins {}", encoding="utf-8")
    wrapper = tmp_path / marker
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)

    assert detect_review_command(tmp_path) == expected


@pytest.mark.parametrize(
    ("project_marker", "wrapper", "fallback"),
    [
        ("pom.xml", "mvnw", ["mvn", "test"]),
        ("build.gradle", "gradlew", ["gradle", "test"]),
    ],
)
def test_detect_review_command_ignores_non_executable_unix_wrapper(
    tmp_path,
    project_marker,
    wrapper,
    fallback,
) -> None:
    (tmp_path / project_marker).write_text("project", encoding="utf-8")
    (tmp_path / wrapper).write_text("#!/bin/sh\n", encoding="utf-8")

    assert detect_review_command(tmp_path) == fallback


@pytest.mark.parametrize(
    ("project_marker", "unix_wrapper", "windows_wrapper", "expected"),
    [
        ("pom.xml", "mvnw", "mvnw.cmd", ["mvnw.cmd", "test"]),
        ("build.gradle", "gradlew", "gradlew.bat", ["gradlew.bat", "test"]),
    ],
)
def test_detect_review_command_prefers_windows_wrapper_on_windows(
    tmp_path,
    monkeypatch,
    project_marker,
    unix_wrapper,
    windows_wrapper,
    expected,
) -> None:
    monkeypatch.setattr(config_module, "_IS_WINDOWS", True)
    (tmp_path / project_marker).write_text("project", encoding="utf-8")
    (tmp_path / unix_wrapper).write_text("unix wrapper", encoding="utf-8")
    (tmp_path / windows_wrapper).write_text("windows wrapper", encoding="utf-8")

    assert detect_review_command(tmp_path) == expected


def test_write_tool_config_leaves_review_unconfigured_when_project_has_no_known_check(
    tmp_path,
) -> None:
    write_tool_config(tmp_path, tool="opencode")

    assert load_config(tmp_path).review_command == []


def test_write_tool_config_invalid_tool_raises(tmp_path) -> None:
    """write_tool_config with unknown tool must raise ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        write_tool_config(tmp_path, tool="nonexistent")
    assert "unsupported init tool" in str(exc_info.value)


def test_write_tool_config_no_overwrite_without_force(tmp_path) -> None:
    """write_tool_config should not overwrite existing config without --force."""
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    existing = config_dir / "config.yml"
    existing.write_text("version: 1\nworker: noop\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        write_tool_config(tmp_path, tool="shell")
    assert "already exists" in str(exc_info.value)


def test_write_tool_config_overwrite_with_force(tmp_path) -> None:
    """write_tool_config with force=True should overwrite existing config."""
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    existing = config_dir / "config.yml"
    existing.write_text("version: 1\nworker: noop\n", encoding="utf-8")
    path = write_tool_config(tmp_path, tool="shell", force=True)
    assert path.exists()
    config = load_config(tmp_path)
    assert config.worker == "shell"


# --- Defaults ---


def test_default_values_on_harness_config() -> None:
    """HarnessConfig defaults should match the documented defaults."""
    config = HarnessConfig(project_dir=Path("/tmp"))
    assert config.worker == "noop"
    assert config.shell_command == []
    assert config.coding_agent_command == []
    assert config.coding_agent_timeout == 1800
    assert config.allow_noop_success is False
    assert config.tmux_command == ""
    assert config.tmux_session_prefix == "agentic-harness"
    assert config.llm_endpoint == ""
    assert config.llm_model == ""
    assert config.llm_api_key == "local"
    assert config.llm_timeout == 120
    assert config.llm_retries == 2
    assert config.llm_retry_delay == 1.0
    assert config.github_owner == ""
    assert config.github_repo == ""
    assert config.github_workflow_id == ""
    assert config.github_ref == "main"
    assert config.github_wait is False
    assert config.github_poll_interval == 5.0
    assert config.github_timeout == 300
    assert config.github_api_version == "2026-03-10"
    assert config.review_command == []
    assert config.review_command_timeout == 60
    assert config.review_artifact == ""
    assert config.review_file_changed == ""
    assert config.review_git_clean is False
