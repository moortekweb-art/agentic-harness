#!/usr/bin/env python3
"""Hidden deterministic verifier for one real-agent evaluation task."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_module(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exact(expected: dict[str, str]) -> bool:
    return all(
        Path(path).is_file() and Path(path).read_text(encoding="utf-8") == content
        for path, content in expected.items()
    )


def _verify_hard(task: dict[str, object]) -> bool:
    task_id = task["id"]
    expected = task["expected_files"]
    if not isinstance(expected, dict):
        return False
    if task_id in {"coupled-port-docs", "two-file-version", "status-and-doc"}:
        return _exact({str(path): str(content) for path, content in expected.items()})
    if task_id == "compat-alias":
        module = _load_module("config.py")
        return (
            module.request_timeout() == 10
            and module.timeout() == 10
            and Path("example.env").read_text(encoding="utf-8")
            == "REQUEST_TIMEOUT=10\nTIMEOUT=10\nMODE=safe\n"
        )
    if task_id == "malformed-lines":
        return bool(_load_module("parser.py").parse_pairs(" a = 1\n\nbad\nb=x=y ") == {
            "a": "1",
            "b": "x=y",
        })
    if task_id == "boundary-window":
        function = _load_module("window.py").in_window
        return all((function(2, 2, 4), function(3, 2, 4), function(4, 2, 4))) and not any(
            (function(1, 2, 4), function(5, 2, 4))
        )
    if task_id == "preserve-unknown-json":
        function = _load_module("settings.py").set_enabled
        original = '{"name":"demo","nested":{"keep":[1,2]},"enabled":true}'
        disabled = json.loads(function(original, False))
        enabled = json.loads(function(json.dumps(disabled), True))
        return bool(disabled == {
            "name": "demo",
            "nested": {"keep": [1, 2]},
            "enabled": False,
        } and enabled == {
            "name": "demo",
            "nested": {"keep": [1, 2]},
            "enabled": True,
        })
    if task_id == "ordered-dedupe":
        function = _load_module("routes.py").unique_routes
        return bool(function(["/b", "/a", "/b", "/c", "/a"]) == ["/b", "/a", "/c"])
    if task_id == "safe-relative-path":
        function = _load_module("paths.py").is_safe_relative
        return all((function(".env.example"), function("a/b"))) and not any(
            (function("../x"), function("a/../b"), function("/abs"))
        )
    if task_id == "none-and-zero":
        function = _load_module("limits.py").effective_limit
        return bool(function(None, 7) == 7 and function(0, 7) == 0 and function(3, 7) == 3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file", type=Path)
    parser.add_argument("task_id")
    args = parser.parse_args()
    payload = json.loads(args.task_file.read_text(encoding="utf-8"))
    task = next(row for row in payload["tasks"] if row["id"] == args.task_id)
    if payload.get("schema") == "agentic_harness.hard_real_agent_tasks.v1":
        try:
            return 0 if _verify_hard(task) else 1
        except (AttributeError, FileNotFoundError, ImportError, json.JSONDecodeError, SyntaxError):
            return 1
    target = Path(task["path"])
    return 0 if target.is_file() and target.read_text(encoding="utf-8") == task["expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
