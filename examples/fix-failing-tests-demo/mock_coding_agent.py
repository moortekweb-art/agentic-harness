"""Tiny stand-in for a non-interactive coding-agent CLI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def main() -> int:
    objective = os.environ.get("AGENTIC_HARNESS_OBJECTIVE", "").partition("\n")[0].strip()
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
