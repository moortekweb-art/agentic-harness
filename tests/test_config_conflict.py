"""Tests for config conflict detection between top-level keys and nested dicts.

The config loader flattens nested `review`, `github`, and `llm` dicts into
top-level keys (e.g. `review.command` → `review_command`). If both a
top-level key and a nested dict key map to the same target, the nested
version would silently override the top-level one. This module verifies
that such conflicts now raise ConfigError with a clear message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_harness.core.config import ConfigError, load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / ".agentic-harness"
    config_dir.mkdir()
    config = config_dir / "config.yml"
    config.write_text(content, encoding="utf-8")
    return config


def test_review_conflict_raises_config_error(tmp_path) -> None:
    """Both top-level review_command and review.command must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
review_command:
  - echo
  - top-level
review:
  command:
    - echo
    - from-dict
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "review_command" in str(exc_info.value)


def test_review_only_dict_works(tmp_path) -> None:
    """Only review dict should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
review:
  command:
    - echo
    - from-dict
""",
    )
    config = load_config(tmp_path)
    assert config.review_command == ["echo", "from-dict"]


def test_review_only_top_level_works(tmp_path) -> None:
    """Only top-level review_command should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
review_command:
  - echo
  - top-level
""",
    )
    config = load_config(tmp_path)
    assert config.review_command == ["echo", "top-level"]


def test_github_conflict_raises_config_error(tmp_path) -> None:
    """Both top-level github_owner and github.owner must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: top-level-owner
github:
  owner: dict-owner
  repo: myrepo
  workflow_id: ci.yml
  token: test
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "github_owner" in str(exc_info.value)


def test_github_only_dict_works(tmp_path) -> None:
    """Only github dict should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github:
  owner: dict-owner
  repo: myrepo
  workflow_id: ci.yml
  token: test
""",
    )
    config = load_config(tmp_path)
    assert config.github_owner == "dict-owner"
    assert config.github_repo == "myrepo"


def test_github_only_top_level_works(tmp_path) -> None:
    """Only top-level github_owner should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: top-level-owner
github_repo: myrepo
github_workflow_id: ci.yml
github_token: test
""",
    )
    config = load_config(tmp_path)
    assert config.github_owner == "top-level-owner"
    assert config.github_repo == "myrepo"


def test_github_token_environment_reference_loads_from_nested_config(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github:
  owner: owner
  repo: repo
  workflow_id: ci.yml
  token_env: AGENTIC_HARNESS_GITHUB_TOKEN
""",
    )

    config = load_config(tmp_path)

    assert config.github_token is None
    assert config.github_token_env == "AGENTIC_HARNESS_GITHUB_TOKEN"


def test_github_token_and_environment_reference_conflict(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: owner
github_repo: repo
github_workflow_id: ci.yml
github_token: plaintext
github_token_env: AGENTIC_HARNESS_GITHUB_TOKEN
""",
    )

    with pytest.raises(ConfigError, match="either github_token_env or github_token"):
        load_config(tmp_path)


def test_github_token_environment_reference_must_be_valid_name(tmp_path) -> None:
    _write_config(
        tmp_path,
        """
version: 1
worker: github_actions
github_owner: owner
github_repo: repo
github_workflow_id: ci.yml
github_token_env: NOT-A-NAME
""",
    )

    with pytest.raises(ConfigError, match="valid environment variable name"):
        load_config(tmp_path)


def test_llm_conflict_raises_config_error(tmp_path) -> None:
    """Both top-level llm_endpoint and llm.endpoint must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker: local_llm
llm_endpoint: http://top-level:8008
llm:
  endpoint: http://dict:8008
  model: gemma4
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "llm_endpoint" in str(exc_info.value)


def test_llm_only_dict_works(tmp_path) -> None:
    """Only llm dict should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: local_llm
llm:
  endpoint: http://dict:8008
  model: gemma4
""",
    )
    config = load_config(tmp_path)
    assert config.llm_endpoint == "http://dict:8008"
    assert config.llm_model == "gemma4"


def test_llm_only_top_level_works(tmp_path) -> None:
    """Only top-level llm_endpoint should load without conflict."""
    _write_config(
        tmp_path,
        """
version: 1
worker: local_llm
llm_endpoint: http://top-level:8008
llm_model: gemma4
""",
    )
    config = load_config(tmp_path)
    assert config.llm_endpoint == "http://top-level:8008"
    assert config.llm_model == "gemma4"


def test_review_conflict_with_timeout(tmp_path) -> None:
    """Conflict between review_command_timeout and review.command_timeout raises."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
review_command_timeout: 120
review:
  command_timeout: 300
  command:
    - echo
    - test
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "review_command_timeout" in str(exc_info.value)


def test_artifact_conflict_raises(tmp_path) -> None:
    """Conflict between review_artifact and review.artifact raises."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
review_artifact: top-level.md
review:
  artifact: dict.md
  command:
    - echo
    - test
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "review_artifact" in str(exc_info.value)


def test_worker_dict_missing_type_raises(tmp_path) -> None:
    """Worker dict without type key must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  shell_command:
    - echo
    - hello
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "missing required key: type" in str(exc_info.value)


def test_worker_dict_conflict_with_shell_command(tmp_path) -> None:
    """Both top-level shell_command and worker.shell_command must raise ConfigError."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: shell
  shell_command:
    - echo
    - from-dict
shell_command:
  - echo
  - from-top-level
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "shell_command" in str(exc_info.value)


def test_worker_dict_conflict_with_coding_agent_command(tmp_path) -> None:
    """Both top-level coding_agent_command and worker.coding_agent_command must raise."""
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
coding_agent_command:
  - codex
  - exec
  - test
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "coding_agent_command" in str(exc_info.value)


def test_worker_dict_conflict_with_tmux_command(tmp_path) -> None:
    """Both top-level tmux_command and worker.tmux_command must raise."""
    _write_config(
        tmp_path,
        """
version: 1
worker:
  type: tmux
  tmux_command: echo test
tmux_command: echo top-level
""",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)
    assert "conflicting config" in str(exc_info.value)
    assert "tmux_command" in str(exc_info.value)


def test_worker_dict_no_conflict_when_only_dict(tmp_path) -> None:
    """Worker dict without conflicting top-level keys should load normally."""
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


def test_worker_dict_no_conflict_when_only_top_level(tmp_path) -> None:
    """Top-level keys without worker dict should load normally."""
    _write_config(
        tmp_path,
        """
version: 1
worker: shell
shell_command:
  - echo
  - hello
""",
    )
    config = load_config(tmp_path)
    assert config.worker == "shell"
    assert config.shell_command == ["echo", "hello"]
