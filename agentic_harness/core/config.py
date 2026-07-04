"""Project-local configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentic_harness.core.errors import ConfigError

CONFIG_DIR = ".agentic-harness"
CONFIG_NAME = "config.yml"
SUPPORTED_VERSION = "1"
ALLOWED_KEYS = {
    "version",
    "worker",
    "shell_command",
    "allow_noop_success",
    "tmux_command",
    "tmux_session_prefix",
    "llm_endpoint",
    "llm_model",
    "llm_api_key",
    "llm_timeout",
    "github_owner",
    "github_repo",
    "github_workflow_id",
    "github_token",
    "github_ref",
    "github_wait",
    "github_poll_interval",
    "github_timeout",
    "review_command",
    "review_command_timeout",
    "review_artifact",
    "review_file_changed",
    "review_git_clean",
}
ALLOWED_WORKERS = {"noop", "shell", "tmux", "local_llm", "github_actions"}
LIST_KEYS = {"shell_command", "review_command"}


@dataclass
class HarnessConfig:
    project_dir: Path
    worker: str = "noop"
    shell_command: list[str] = field(default_factory=list)
    allow_noop_success: bool = False
    tmux_command: str = ""
    tmux_session_prefix: str = "agentic-harness"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = "local"
    llm_timeout: int = 120
    github_owner: str = ""
    github_repo: str = ""
    github_workflow_id: str = ""
    github_token: str | None = None
    github_ref: str = "main"
    github_wait: bool = False
    github_poll_interval: float = 5.0
    github_timeout: int = 300
    review_command: list[str] = field(default_factory=list)
    review_command_timeout: int = 60
    review_artifact: str = ""
    review_file_changed: str = ""
    review_git_clean: bool = False

    @property
    def config_path(self) -> Path:
        return self.project_dir / CONFIG_DIR / CONFIG_NAME


DEFAULT_CONFIG = """# agentic-harness project config
version: 1
worker: noop
# For shell execution, set:
# worker: shell
# shell_command:
#   - python
#   - -c
#   - "print('implemented')"
#
# Optional review gate:
# review_command:
#   - python
#   - -m
#   - pytest
"""


def write_default_config(project_dir: str | Path = ".") -> Path:
    root = Path(project_dir)
    config_dir = root / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / CONFIG_NAME
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


def load_config(project_dir: str | Path = ".") -> HarnessConfig:
    root = Path(project_dir)
    path = root / CONFIG_DIR / CONFIG_NAME
    config = HarnessConfig(project_dir=root)
    if not path.exists():
        return config
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML config: {exc}") from exc
    if payload is None:
        raise ConfigError("missing required config key: version")
    if not isinstance(payload, dict):
        raise ConfigError("config root must be a mapping")
    values = _flatten_config(payload)
    version = str(values.pop("version", ""))
    if not version:
        raise ConfigError("missing required config key: version")
    if version != SUPPORTED_VERSION:
        raise ConfigError(f"unsupported config version: {version}")
    unknown = sorted(set(values) - ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"unknown config key: {unknown[0]}")
    for key, value in values.items():
        if key == "worker":
            config.worker = _parse_str(value, key)
            if config.worker not in ALLOWED_WORKERS:
                raise ConfigError(f"unsupported worker: {config.worker}")
        elif key == "allow_noop_success":
            config.allow_noop_success = _parse_bool(value, key)
        elif key in LIST_KEYS:
            setattr(config, key, _parse_list(value, key))
        elif key in {"github_wait", "review_git_clean"}:
            setattr(config, key, _parse_bool(value, key))
        elif key in {"llm_timeout", "github_timeout", "review_command_timeout"}:
            setattr(config, key, _parse_int(value, key))
        elif key == "github_poll_interval":
            config.github_poll_interval = _parse_float(value, key)
        elif key == "github_token":
            config.github_token = _parse_str(value, key) or None
        else:
            setattr(config, key, _parse_str(value, key))
    if config.worker == "shell" and not config.shell_command:
        raise ConfigError("shell worker requires shell_command")
    if config.worker == "tmux" and not config.tmux_command:
        raise ConfigError("tmux worker requires tmux_command")
    if config.worker == "local_llm" and not config.llm_endpoint:
        raise ConfigError("local_llm worker requires llm_endpoint")
    if config.worker == "local_llm" and not config.llm_model:
        raise ConfigError("local_llm worker requires llm_model")
    if config.worker == "github_actions" and not config.github_owner:
        raise ConfigError("github_actions worker requires github_owner")
    if config.worker == "github_actions" and not config.github_repo:
        raise ConfigError("github_actions worker requires github_repo")
    if config.worker == "github_actions" and not config.github_workflow_id:
        raise ConfigError("github_actions worker requires github_workflow_id")
    return config


def _flatten_config(payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    worker = values.get("worker")
    if isinstance(worker, dict):
        values["worker"] = worker.get("type", "")
        for key, value in worker.items():
            if key != "type":
                values[key] = value
    review = values.pop("review", None)
    if isinstance(review, dict):
        aliases = {
            "command": "review_command",
            "command_timeout": "review_command_timeout",
            "artifact": "review_artifact",
            "file_changed": "review_file_changed",
            "git_clean": "review_git_clean",
        }
        for key, value in review.items():
            values[aliases.get(key, f"review_{key}")] = value
    github = values.pop("github", None)
    if isinstance(github, dict):
        for key, value in github.items():
            values[f"github_{key}"] = value
    llm = values.pop("llm", None)
    if isinstance(llm, dict):
        for key, value in llm.items():
            values[f"llm_{key}"] = value
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_str(value: object, key: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value).lower() if isinstance(value, bool) else str(value)
    if value is None:
        return ""
    raise ConfigError(f"{key} must be a scalar")


def _parse_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{key} must be a list")
    return [_parse_str(item, key) for item in value]


def _parse_bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _parse_str(value, key).lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _parse_int(value: object, key: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(_parse_str(value, key))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _parse_float(value: object, key: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(_parse_str(value, key))
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
