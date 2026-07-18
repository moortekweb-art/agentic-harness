"""Project-local configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import json
import os
import re
import sys
from typing import Any

import yaml

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.safety import resolve_command_executable

CONFIG_DIR = ".agentic-harness"
CONFIG_NAME = "config.yml"
SUPPORTED_VERSION = "1"
ALLOWED_KEYS = {
    "version",
    "worker",
    "shell_command",
    "coding_agent_command",
    "coding_agent_timeout",
    "coding_agent_transcript",
    "allow_noop_success",
    "assurance_mode",
    "tmux_command",
    "tmux_session_prefix",
    "llm_endpoint",
    "llm_model",
    "llm_api_key",
    "llm_api_key_env",
    "llm_credential_source",
    "llm_remote_data_confirmed",
    "llm_max_steps",
    "llm_timeout",
    "llm_retries",
    "llm_retry_delay",
    "github_owner",
    "github_repo",
    "github_workflow_id",
    "github_token",
    "github_token_env",
    "github_ref",
    "github_wait",
    "github_poll_interval",
    "github_timeout",
    "github_api_version",
    "review_command",
    "review_command_timeout",
    "review_covers",
    "review_artifact",
    "review_file_changed",
    "review_git_clean",
    "goal_max_cycles",
    "goal_max_elapsed_seconds",
    "goal_max_total_tokens",
    "goal_max_provider_calls",
    "goal_max_tool_calls",
}
ALLOWED_WORKER_DICT_KEYS = {
    "type",
    "shell_command",
    "coding_agent_command",
    "coding_agent_timeout",
    "coding_agent_transcript",
    "tmux_command",
    "tmux_session_prefix",
    "llm_endpoint",
    "llm_model",
    "llm_api_key",
    "llm_api_key_env",
    "llm_credential_source",
    "llm_remote_data_confirmed",
    "llm_max_steps",
    "llm_timeout",
    "llm_retries",
    "llm_retry_delay",
    "github_owner",
    "github_repo",
    "github_workflow_id",
    "github_token",
    "github_token_env",
    "github_ref",
    "github_wait",
    "github_poll_interval",
    "github_timeout",
    "github_api_version",
    "allow_noop_success",
}
ALLOWED_GITHUB_DICT_KEYS = {
    "owner",
    "repo",
    "workflow_id",
    "token",
    "token_env",
    "ref",
    "wait",
    "poll_interval",
    "timeout",
    "api_version",
}
ALLOWED_LLM_DICT_KEYS = {
    "endpoint",
    "model",
    "api_key",
    "api_key_env",
    "credential_source",
    "remote_data_confirmed",
    "max_steps",
    "timeout",
}
ALLOWED_REVIEW_DICT_KEYS = {
    "command",
    "command_timeout",
    "covers",
    "artifact",
    "file_changed",
    "git_clean",
}
ALLOWED_AUTONOMY_DICT_KEYS = {
    "max_cycles",
    "max_elapsed_seconds",
    "max_total_tokens",
    "max_provider_calls",
    "max_tool_calls",
}
ALLOWED_WORKERS = {
    "noop",
    "shell",
    "coding_agent",
    "tmux",
    "local_llm",
    "model_agent",
    "github_actions",
}
LIST_KEYS = {
    "shell_command",
    "coding_agent_command",
    "review_command",
    "review_covers",
}
_IS_WINDOWS = os.name == "nt"


@dataclass
class HarnessConfig:
    project_dir: Path
    worker: str = "noop"
    shell_command: list[str] = field(default_factory=list)
    coding_agent_command: list[str] = field(default_factory=list)
    coding_agent_timeout: int = 1800
    coding_agent_transcript: str = ".agentic-harness/runs/{goal_id}/coding-agent.log"
    allow_noop_success: bool = False
    assurance_mode: str = "specification_frozen"
    tmux_command: str = ""
    tmux_session_prefix: str = "agentic-harness"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = "local"
    llm_api_key_env: str = ""
    llm_credential_source: str = "none"
    llm_remote_data_confirmed: bool = False
    llm_max_steps: int = 8
    llm_timeout: int = 120
    llm_retries: int = 2
    llm_retry_delay: float = 1.0
    github_owner: str = ""
    github_repo: str = ""
    github_workflow_id: str = ""
    github_token: str | None = None
    github_token_env: str = ""
    github_ref: str = "main"
    github_wait: bool = False
    github_poll_interval: float = 5.0
    github_timeout: int = 300
    github_api_version: str = "2026-03-10"
    review_command: list[str] = field(default_factory=list)
    review_command_timeout: int = 60
    review_covers: list[str] = field(default_factory=list)
    review_artifact: str = ""
    review_file_changed: str = ""
    review_git_clean: bool = False
    goal_max_cycles: int = 100
    goal_max_elapsed_seconds: int = 7_200
    goal_max_total_tokens: int = 500_000
    goal_max_provider_calls: int = 200
    goal_max_tool_calls: int = 1_000

    @property
    def config_path(self) -> Path:
        return self.project_dir / CONFIG_DIR / CONFIG_NAME


DEFAULT_CONFIG = """# agentic-harness project config
version: 1
worker: noop
assurance_mode: specification_frozen
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
# review_covers:
#   - "*"  # resolve to every already-frozen requirement before the check runs
"""

TOOL_CONFIGS: dict[str, str] = {
    "shell": """# agentic-harness shell starter config
version: 1
worker: shell
shell_command:
  - python
  - -c
  - "print('shell worker placeholder: edit .agentic-harness/config.yml to run your own command')"
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
    "codex": """# agentic-harness Codex starter config
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codex
    - exec
    - --skip-git-repo-check
    - "{objective}"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
    "grok": """# agentic-harness Grok Build starter config
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - grok
    - -p
    - "{objective}"
    - --cwd
    - .
    - --output-format
    - plain
    - --max-turns
    - "50"
    - --permission-mode
    - bypassPermissions
    - --sandbox
    - workspace
    - --no-auto-update
    - --deny
    - "Bash(git push*)"
    - --deny
    - "Bash(sudo*)"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
    "opencode": """# agentic-harness OpenCode starter config
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - opencode
    - run
    - "{objective}"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
    "aider": """# agentic-harness Aider starter config
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - aider
    - --yes-always
    - --message
    - "{objective}"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
    "codewhale": """# agentic-harness CodeWhale starter config
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codewhale
    - exec
    - --allowed-tools
    - read_file,exec_shell
    - --max-turns
    - "10"
    - "{objective}"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/codewhale.log
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
review_covers:
  - "*"
review_command_timeout: 120
""",
}

def write_default_config(project_dir: str | Path = ".") -> Path:
    root = Path(project_dir)
    config_dir = root / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / CONFIG_NAME
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


def write_tool_config(
    project_dir: str | Path = ".", tool: str = "shell", *, force: bool = False
) -> Path:
    try:
        content = _tool_config_content(Path(project_dir), tool)
    except KeyError as exc:
        supported = ", ".join(sorted(TOOL_CONFIGS))
        raise ConfigError(f"unsupported init tool: {tool}; choose one of: {supported}") from exc
    root = Path(project_dir)
    config_dir = root / CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / CONFIG_NAME
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists; pass --force to replace it")
    path.write_text(content, encoding="utf-8")
    return path


def _tool_config_content(project_dir: Path, tool: str) -> str:
    if tool == "shell" and (project_dir / "mock_coding_agent.py").exists():
        return demo_shell_config()
    content = TOOL_CONFIGS[tool]
    if tool == "shell":
        return content
    payload = yaml.safe_load(content)
    if not isinstance(payload, dict):
        raise ConfigError(f"invalid built-in config template for {tool}")
    worker = payload.get("worker")
    if isinstance(worker, dict):
        command = worker.get("coding_agent_command")
        if isinstance(command, list) and all(isinstance(item, str) for item in command):
            worker["coding_agent_command"] = resolve_command_executable(
                command,
                always=True,
            )
    detected = detect_review_command(project_dir)
    if detected:
        payload["review_command"] = detected
        payload["review_covers"] = ["*"]
        payload["review_command_timeout"] = 300
    else:
        payload.pop("review_command", None)
        payload.pop("review_covers", None)
        payload.pop("review_command_timeout", None)
    return f"# agentic-harness {tool} starter config\n" + yaml.safe_dump(
        payload,
        sort_keys=False,
    )


def detect_review_command(project_dir: str | Path) -> list[str]:
    root = Path(project_dir)
    package_json = root / "package.json"
    if package_json.exists():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        scripts = payload.get("scripts") if isinstance(payload, dict) else None
        test_script = scripts.get("test") if isinstance(scripts, dict) else None
        if (
            isinstance(test_script, str)
            and test_script.strip()
            and "no test specified" not in test_script.lower()
        ):
            if (root / "pnpm-lock.yaml").exists():
                return ["pnpm", "test"]
            if (root / "yarn.lock").exists():
                return ["yarn", "test"]
            if (root / "bun.lock").exists() or (root / "bun.lockb").exists():
                return ["bun", "test"]
            return ["npm", "test"]
    if (root / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (root / "go.mod").exists():
        return ["go", "test", "./..."]
    if (root / "pom.xml").exists():
        wrapper = _build_wrapper(root, unix_name="mvnw", windows_names=("mvnw.cmd", "mvnw.bat"))
        return [wrapper, "test"] if wrapper else ["mvn", "test"]
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        wrapper = _build_wrapper(
            root,
            unix_name="gradlew",
            windows_names=("gradlew.bat", "gradlew.cmd"),
        )
        return [wrapper, "test"] if wrapper else ["gradle", "test"]
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        return ["dotnet", "test"]
    if (root / "Gemfile").exists() and (root / "spec").is_dir():
        return ["bundle", "exec", "rspec"]
    if (root / "tests").is_dir() or (root / "pytest.ini").is_file():
        return ["python", "-m", "pytest", "-q"]
    return []


def _build_wrapper(
    root: Path,
    *,
    unix_name: str,
    windows_names: tuple[str, ...],
) -> str:
    if _IS_WINDOWS:
        return next((name for name in windows_names if (root / name).is_file()), "")
    path = root / unix_name
    return f"./{unix_name}" if path.is_file() and os.access(path, os.X_OK) else ""


def demo_shell_config(*, python_executable: str | None = None) -> str:
    executable = python_executable or sys.executable
    payload = {
        "version": 1,
        "worker": "shell",
        "shell_command": [executable, "mock_coding_agent.py"],
        "review_command": [executable, "-m", "pytest", "tests/", "-q"],
        "review_covers": ["*"],
        "review_command_timeout": 120,
    }
    return "# agentic-harness shell demo config\n" + yaml.safe_dump(
        payload,
        sort_keys=False,
    )


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
    provided_keys = set(values)
    version = str(values.pop("version", ""))
    if not version:
        raise ConfigError("missing required config key: version")
    if version != SUPPORTED_VERSION:
        raise ConfigError(f"unsupported config version: {version}")
    unknown = sorted(set(values) - ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
    for key, value in values.items():
        if key == "worker":
            config.worker = _parse_str(value, key)
            if config.worker not in ALLOWED_WORKERS:
                raise ConfigError(f"unsupported worker: {config.worker}")
        elif key == "allow_noop_success":
            config.allow_noop_success = _parse_bool(value, key)
        elif key in LIST_KEYS:
            setattr(config, key, _parse_list(value, key))
        elif key in {"github_wait", "review_git_clean", "llm_remote_data_confirmed"}:
            setattr(config, key, _parse_bool(value, key))
        elif key in {
            "coding_agent_timeout",
            "llm_timeout",
            "llm_retries",
            "llm_max_steps",
            "github_timeout",
            "review_command_timeout",
            "goal_max_cycles",
            "goal_max_elapsed_seconds",
            "goal_max_total_tokens",
            "goal_max_provider_calls",
            "goal_max_tool_calls",
        }:
            setattr(config, key, _parse_int(value, key))
        elif key in {"llm_retry_delay", "github_poll_interval"}:
            setattr(config, key, _parse_float(value, key))
        elif key == "github_token":
            config.github_token = _parse_str(value, key) or None
        else:
            setattr(config, key, _parse_str(value, key))
    if config.worker == "shell" and not config.shell_command:
        raise ConfigError("shell worker requires shell_command")
    if config.worker == "coding_agent" and not config.coding_agent_command:
        raise ConfigError("coding_agent worker requires coding_agent_command")
    if config.worker == "tmux" and not config.tmux_command:
        raise ConfigError("tmux worker requires tmux_command")
    if config.worker == "local_llm" and not config.llm_endpoint:
        raise ConfigError("local_llm worker requires llm_endpoint")
    if config.worker == "local_llm" and not config.llm_model:
        raise ConfigError("local_llm worker requires llm_model")
    if config.worker == "model_agent" and not config.llm_endpoint:
        raise ConfigError("model_agent worker requires llm_endpoint")
    if config.worker == "model_agent" and not config.llm_model:
        raise ConfigError("model_agent worker requires llm_model")
    if config.worker == "model_agent" and "llm_api_key" in provided_keys:
        raise ConfigError("model_agent credentials must use llm.api_key_env, not plaintext api_key")
    if config.worker == "model_agent":
        from agentic_harness.core.providers import ProviderProfile

        profile = ProviderProfile(
            endpoint=config.llm_endpoint,
            model=config.llm_model,
            api_key_env=config.llm_api_key_env,
        )
        if profile.data_location == "cloud" and not config.llm_remote_data_confirmed:
            raise ConfigError(
                "cloud model config requires llm.remote_data_confirmed: true"
            )
        if config.llm_api_key_env and config.llm_credential_source == "none":
            config.llm_credential_source = "env"
        if config.llm_credential_source not in {"none", "env", "session"}:
            raise ConfigError("llm_credential_source must be none, env, or session")
        if config.llm_credential_source == "env" and not config.llm_api_key_env:
            raise ConfigError("env credential source requires llm_api_key_env")
    if config.worker == "github_actions" and not config.github_owner:
        raise ConfigError("github_actions worker requires github_owner")
    if config.worker == "github_actions" and not config.github_repo:
        raise ConfigError("github_actions worker requires github_repo")
    if config.worker == "github_actions" and not config.github_workflow_id:
        raise ConfigError("github_actions worker requires github_workflow_id")
    if config.github_token and config.github_token_env:
        raise ConfigError(
            "github_actions credentials must use either github_token_env or "
            "github_token, not both"
        )
    if config.github_token_env and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", config.github_token_env
    ):
        raise ConfigError("github_token_env must be a valid environment variable name")
    if config.assurance_mode not in {
        "check_gated",
        "specification_frozen",
        "high_assurance",
    }:
        raise ConfigError(
            "assurance_mode must be check_gated, specification_frozen, or high_assurance"
        )
    if len(config.review_covers) != len(set(config.review_covers)):
        raise ConfigError("review_covers contains duplicate requirement ids")
    if any(not requirement_id.strip() for requirement_id in config.review_covers):
        raise ConfigError("review_covers contains an empty requirement id")
    for key in (
        "goal_max_cycles",
        "goal_max_elapsed_seconds",
        "goal_max_total_tokens",
        "goal_max_provider_calls",
        "goal_max_tool_calls",
    ):
        if int(getattr(config, key)) < 0:
            raise ConfigError(f"{key} must not be negative")
    return config


def _flatten_config(payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(payload)
    worker = values.get("worker")
    if isinstance(worker, dict):
        _check_unknown_keys(worker, ALLOWED_WORKER_DICT_KEYS, "worker")
        if "type" not in worker:
            raise ConfigError("worker dict is missing required key: type")
        values["worker"] = worker["type"]
        for key, value in worker.items():
            if key != "type":
                if key in values and values[key] is not None:
                    raise ConfigError(
                        f"conflicting config: both top-level '{key}' and "
                        f"worker.{key} are set; pick one"
                    )
                values[key] = value
    review = values.pop("review", None)
    if isinstance(review, dict):
        _check_unknown_keys(review, ALLOWED_REVIEW_DICT_KEYS, "review")
        aliases = {
            "command": "review_command",
            "command_timeout": "review_command_timeout",
            "covers": "review_covers",
            "artifact": "review_artifact",
            "file_changed": "review_file_changed",
            "git_clean": "review_git_clean",
        }
        for key, value in review.items():
            target = aliases.get(key, f"review_{key}")
            if target in values:
                raise ConfigError(
                    f"conflicting config: both top-level '{target}' and "
                    f"review.{key} are set; pick one"
                )
            values[target] = value
    github = values.pop("github", None)
    if isinstance(github, dict):
        _check_unknown_keys(github, ALLOWED_GITHUB_DICT_KEYS, "github")
        for key, value in github.items():
            target = f"github_{key}"
            if target in values:
                raise ConfigError(
                    f"conflicting config: both top-level '{target}' and "
                    f"github.{key} are set; pick one"
                )
            values[target] = value
    llm = values.pop("llm", None)
    if isinstance(llm, dict):
        _check_unknown_keys(llm, ALLOWED_LLM_DICT_KEYS, "llm")
        for key, value in llm.items():
            target = f"llm_{key}"
            if target in values:
                raise ConfigError(
                    f"conflicting config: both top-level '{target}' and llm.{key} are set; pick one"
                )
            values[target] = value
    autonomy = values.pop("autonomy", None)
    if isinstance(autonomy, dict):
        _check_unknown_keys(autonomy, ALLOWED_AUTONOMY_DICT_KEYS, "autonomy")
        for key, value in autonomy.items():
            target = f"goal_{key}"
            if target in values:
                raise ConfigError(
                    f"conflicting config: both top-level '{target}' and autonomy.{key} are set; pick one"
                )
            values[target] = value
    return values


def _check_unknown_keys(payload: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigError(f"{context} has unknown key(s): {', '.join(unknown)}")


def _parse_str(value: object, key: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a string, got {type(value).__name__}")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
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
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer, got boolean")
    if isinstance(value, int):
        return value
    try:
        return int(_parse_str(value, key))
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc


def _parse_float(value: object, key: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be a number, got boolean")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(_parse_str(value, key))
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
