#!/usr/bin/env python3
"""Hidden deterministic verifier for one real-agent evaluation task."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import secrets
import subprocess
import sys
from types import ModuleType


def _load_module(path: str) -> ModuleType:
    validated = _expected_path(path)
    spec = importlib.util.spec_from_file_location("candidate", validated)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_path(raw_path: str) -> Path:
    root = Path.cwd().resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"expected path escapes workspace: {raw_path}")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"expected path uses symlink: {raw_path}")
    resolved = current.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"expected path escapes workspace: {raw_path}")
    return resolved


def _exact(expected: dict[str, str]) -> bool:
    return all(
        _expected_path(path).is_file()
        and _expected_path(path).read_text(encoding="utf-8") == content
        for path, content in expected.items()
    )


def _verify_behavior(task_id: str) -> bool:
    if task_id == "compat-alias":
        dynamic = secrets.randbelow(1_000_000) + 100
        module = _load_module("config.py")
        setattr(module, "REQUEST_TIMEOUT", dynamic)
        return (
            callable(module.request_timeout)
            and callable(module.timeout)
            and module.request_timeout() == dynamic
            and module.timeout() == dynamic
            and _expected_path("example.env").read_text(encoding="utf-8")
            == "REQUEST_TIMEOUT=10\nTIMEOUT=10\nMODE=safe\n"
        )
    if task_id == "malformed-lines":
        key = secrets.token_hex(8)
        value = secrets.token_hex(8)
        function = _load_module("parser.py").parse_pairs
        return bool(function(f" {key} = {value} \n\nbad\nnext=x=y ") == {
            key: value,
            "next": "x=y",
        })
    if task_id == "boundary-window":
        function = _load_module("window.py").in_window
        return list(inspect.signature(function).parameters) == ["value", "start", "end"] and all(
            (function(-7, -7, 11), function(0, -7, 11), function(11, -7, 11))
        ) and not any(
            (function(-8, -7, 11), function(12, -7, 11))
        )
    if task_id == "preserve-unknown-json":
        marker = secrets.token_hex(8)
        function = _load_module("settings.py").set_enabled
        original = json.dumps({"name": marker, "nested": {"keep": [marker]}, "enabled": True})
        disabled_text = function(original, False)
        disabled = json.loads(disabled_text)
        enabled_text = function(json.dumps(disabled), True)
        enabled = json.loads(enabled_text)
        return bool(disabled_text == json.dumps(disabled, indent=2) + "\n" and
                    enabled_text == json.dumps(enabled, indent=2) + "\n" and disabled == {
            "name": marker,
            "nested": {"keep": [marker]},
            "enabled": False,
        } and enabled == {
            "name": marker,
            "nested": {"keep": [marker]},
            "enabled": True,
        })
    if task_id == "ordered-dedupe":
        marker = secrets.token_hex(8)
        function = _load_module("routes.py").unique_routes
        upper = f"/{marker.upper()}"
        lower = f"/{marker.lower()}"
        return bool(function([upper, lower, upper, "/fixed", lower]) ==
                    [upper, lower, "/fixed"])
    if task_id == "safe-relative-path":
        marker = secrets.token_hex(8)
        function = _load_module("paths.py").is_safe_relative
        return all((function(".env.example"), function(f"{marker}/child"))) and not any(
            (function(f"../{marker}"), function(f"{marker}/../child"), function(f"/{marker}"))
        )
    if task_id == "none-and-zero":
        default = secrets.randbelow(1_000_000) + 10
        negative = -(secrets.randbelow(1_000_000) + 1)
        explicit = secrets.randbelow(1_000_000) + 1
        function = _load_module("limits.py").effective_limit
        return bool(function(None, default) == default and function(0, default) == 0 and
                    function(explicit, default) == explicit and
                    function(negative, default) == negative)
    return False


def _verify_hard(task: dict[str, object]) -> bool:
    task_id = str(task["id"])
    expected = task["expected_files"]
    if not isinstance(expected, dict):
        return False
    files = task.get("files")
    if not isinstance(files, dict):
        return False
    for raw_path in files:
        _expected_path(str(raw_path))
    if task_id in {
        "coupled-port-docs",
        "boundary-window",
        "two-file-version",
        "status-and-doc",
    }:
        return _exact({str(path): str(content) for path, content in expected.items()})
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--probe", task_id],
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe")
    parser.add_argument("task_file", type=Path, nargs="?")
    parser.add_argument("task_id", nargs="?")
    args = parser.parse_args()
    if args.probe:
        try:
            return 0 if _verify_behavior(args.probe) else 1
        except (
            AttributeError,
            FileNotFoundError,
            ImportError,
            json.JSONDecodeError,
            SyntaxError,
            ValueError,
        ):
            return 1
    if args.task_file is None or args.task_id is None:
        parser.error("task_file and task_id are required")
    payload = json.loads(args.task_file.read_text(encoding="utf-8"))
    task = next(row for row in payload["tasks"] if row["id"] == args.task_id)
    if payload.get("schema") in {
        "agentic_harness.hard_real_agent_tasks.v1",
        "agentic_harness.hard_real_agent_tasks.v2",
        "agentic_harness.hard_real_agent_tasks.v3",
        "agentic_harness.hard_real_agent_tasks.v4",
        "agentic_harness.hard_real_agent_tasks.v5",
    }:
        try:
            return 0 if _verify_hard(task) else 1
        except (
            AttributeError,
            FileNotFoundError,
            ImportError,
            json.JSONDecodeError,
            SyntaxError,
            ValueError,
        ):
            return 1
    target = Path(task["path"])
    return 0 if target.is_file() and target.read_text(encoding="utf-8") == task["expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
