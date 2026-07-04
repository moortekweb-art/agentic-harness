"""Small shell-worker target used by the example config."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    goal_id = os.environ["AGENTIC_HARNESS_GOAL_ID"]
    objective = os.environ["AGENTIC_HARNESS_OBJECTIVE"]
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{goal_id}.txt"
    output_path.write_text(f"Goal: {objective}\nStatus: captured by shell worker\n", encoding="utf-8")
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

