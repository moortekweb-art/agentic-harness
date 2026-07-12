"""Packaged demo project generators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentic_harness.core.errors import ConfigError


@dataclass(frozen=True)
class DemoFile:
    path: str
    content: str


FIX_TESTS_DEMO_FILES = (
    DemoFile(
        "README.md",
        """# Fix Failing Tests Demo

This generated demo shows the core Agentic Harness pitch:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q   # expected to fail
agentic-harness fix-tests --until-done  # auto-creates demo config
agentic-harness status
agentic-harness report
python -m pytest tests/ -q   # should pass
```

The project starts with a deliberately broken calculator function. The
`fix-tests` command detects the generated demo and creates a shell worker config
that runs `mock_coding_agent.py`, which stands in for a non-interactive coding
agent CLI during local demos. Its first attempt claims completion without fixing
the bug. The review gate rejects that claim, the second attempt repairs the
calculator, and the goal becomes `done` only after pytest passes.

To reset the demo:

```bash
python reset_demo.py
rm -rf .agentic-harness
```
""",
    ),
    DemoFile(
        "calculator.py",
        """def add(left: int, right: int) -> int:
    return left + right + 1
""",
    ),
    DemoFile(
        "requirements-dev.txt",
        """pytest>=8
""",
    ),
    DemoFile(
        "mock_coding_agent.py",
        '''"""Tiny stand-in for a non-interactive coding-agent CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def main() -> int:
    goal_id = os.environ.get("AGENTIC_HARNESS_GOAL_ID", "unknown-goal").strip()
    objective = os.environ.get("AGENTIC_HARNESS_OBJECTIVE", "").partition("\\n")[0].strip()
    attempt_path = Path(".agentic-harness") / "runs" / goal_id / "demo-worker-attempt"
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt = int(attempt_path.read_text(encoding="utf-8") or "0") if attempt_path.exists() else 0
    attempt_path.write_text(str(attempt + 1), encoding="utf-8")
    if attempt == 0:
        print(f"claimed completion before repairing objective: {objective}")
        return 0
    path = Path("calculator.py")
    content = path.read_text(encoding="utf-8")
    if "return left + right + 1" not in content:
        print(f"nothing to fix for objective: {objective}")
        return 0
    path.write_text(
        content.replace("return left + right + 1", "return left + right"),
        encoding="utf-8",
    )
    shutil.rmtree("__pycache__", ignore_errors=True)
    shutil.rmtree("tests/__pycache__", ignore_errors=True)
    print(f"fixed calculator for objective: {objective}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    ),
    DemoFile(
        "reset_demo.py",
        '''"""Reset the demo bug after a run."""

import shutil
from pathlib import Path


Path("calculator.py").write_text(
    "def add(left: int, right: int) -> int:\\n"
    "    return left + right + 1\\n",
    encoding="utf-8",
)
shutil.rmtree("__pycache__", ignore_errors=True)
shutil.rmtree("tests/__pycache__", ignore_errors=True)
print("reset calculator.py")
''',
    ),
    DemoFile(
        "tests/test_calculator.py",
        """from calculator import add


def test_adds_two_numbers() -> None:
    assert add(2, 3) == 5
""",
    ),
)


def demo_names() -> list[str]:
    return ["fix-tests"]


def create_demo(name: str, target_dir: str | Path, *, force: bool = False) -> Path:
    normalized = name.replace("_", "-")
    if normalized != "fix-tests":
        raise ConfigError(f"unknown demo {name!r}; available demos: {', '.join(demo_names())}")

    root = Path(target_dir)
    if root.exists() and not root.is_dir():
        raise ConfigError(f"demo target exists and is not a directory: {root}")
    if root.exists() and any(root.iterdir()) and not force:
        raise ConfigError(f"demo target is not empty: {root}; rerun with --force to overwrite demo files")

    root.mkdir(parents=True, exist_ok=True)
    for demo_file in FIX_TESTS_DEMO_FILES:
        path = root / demo_file.path
        if path.exists() and path.is_dir():
            raise ConfigError(f"demo file target is a directory: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(demo_file.content, encoding="utf-8")
    return root
