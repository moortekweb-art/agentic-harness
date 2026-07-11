#!/usr/bin/env python3
"""Independent deterministic verifier for one materialized fixture."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.fixture_support import fixture_target, load_fixture  # noqa: E402


def main() -> int:
    workspace = Path.cwd()
    task = load_fixture(workspace)
    target = fixture_target(workspace, task.get("path"))
    expected = str(task.get("expected") or "").encode()
    actual = target.read_bytes() if target.is_file() else b""
    passed = actual == expected
    digest = hashlib.sha256(actual).hexdigest()
    print(f"fixture_verifier passed={str(passed).lower()} actual_sha256={digest}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
