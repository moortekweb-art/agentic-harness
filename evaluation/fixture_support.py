"""Shared fixture loading and path containment for evaluation subprocesses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_NAME = ".evaluation-task.json"


def load_fixture(workspace: Path) -> dict[str, Any]:
    payload = json.loads((workspace / FIXTURE_NAME).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture payload must be an object")
    return payload


def fixture_target(workspace: Path, relative_path: object) -> Path:
    root = workspace.resolve()
    target = (root / str(relative_path)).resolve()
    target.relative_to(root)
    return target
