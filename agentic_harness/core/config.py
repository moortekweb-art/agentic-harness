"""Project-local configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
    lines = path.read_text(encoding="utf-8").splitlines()
    key = ""
    lists: dict[str, list[str]] = {"shell_command": [], "review_command": []}
    version = ""
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key in LIST_KEYS:
            lists[key].append(_unquote(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            raise ConfigError(f"invalid config line: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in ALLOWED_KEYS:
            raise ConfigError(f"unknown config key: {key}")
        if key == "version":
            version = _unquote(value)
            if version != SUPPORTED_VERSION:
                raise ConfigError(f"unsupported config version: {version}")
        if key == "worker" and value:
            config.worker = _unquote(value)
            if config.worker not in ALLOWED_WORKERS:
                raise ConfigError(f"unsupported worker: {config.worker}")
        elif key == "allow_noop_success":
            config.allow_noop_success = _parse_bool(value, key)
        elif key in LIST_KEYS:
            lists[key] = []
        elif key in {"github_wait", "review_git_clean"}:
            setattr(config, key, _parse_bool(value, key))
        elif key in {"llm_timeout", "github_timeout", "review_command_timeout"}:
            setattr(config, key, _parse_int(value, key))
        elif key == "github_poll_interval":
            config.github_poll_interval = _parse_float(value, key)
        elif key == "github_token":
            config.github_token = _unquote(value) or None
        elif key in ALLOWED_KEYS:
            setattr(config, key, _unquote(value))
    if not version:
        raise ConfigError("missing required config key: version")
    config.shell_command = lists["shell_command"]
    config.review_command = lists["review_command"]
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


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_bool(value: str, key: str) -> bool:
    normalized = _unquote(value).lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _parse_int(value: str, key: str) -> int:
    try:
        return int(_unquote(value))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _parse_float(value: str, key: str) -> float:
    try:
        return float(_unquote(value))
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
