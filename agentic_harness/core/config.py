"""Project-local configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = ".agentic-harness"
CONFIG_NAME = "config.yml"


@dataclass
class HarnessConfig:
    project_dir: Path
    worker: str = "noop"
    shell_command: list[str] = field(default_factory=list)

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
    shell_command: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key == "shell_command":
            shell_command.append(_unquote(stripped[2:].strip()))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "worker" and value:
            config.worker = _unquote(value)
        elif key == "shell_command":
            shell_command = []
    config.shell_command = shell_command
    return config


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value

