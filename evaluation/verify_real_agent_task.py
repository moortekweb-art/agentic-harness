#!/usr/bin/env python3
"""Hidden deterministic verifier for one real-agent evaluation task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file", type=Path)
    parser.add_argument("task_id")
    args = parser.parse_args()
    payload = json.loads(args.task_file.read_text(encoding="utf-8"))
    task = next(row for row in payload["tasks"] if row["id"] == args.task_id)
    target = Path(task["path"])
    return 0 if target.is_file() and target.read_text(encoding="utf-8") == task["expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
