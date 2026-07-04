#!/usr/bin/env python3
"""Compatibility entry point for the local Node1 goal command shim."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parent
    if "__file__" in globals()
    else Path("/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts")
)
_IMPL_PATH = _SCRIPT_DIR / "local_node1_goal_command_impl.py"
_SPEC = importlib.util.spec_from_file_location("local_node1_goal_command_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load local goal command implementation: {_IMPL_PATH}")
_IMPL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_IMPL)

for _name, _value in vars(_IMPL).items():
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = _value


def main() -> int:
    """Run the implementation while honoring monkeypatched wrapper globals."""
    for name in (
        "run_command",
        "STATE_PATH",
        "REPORT_PATH",
        "write_artifacts",
        "SUPERVISOR",
        "MANAGER",
        "WRAPPER",
        "DOC_ROOT",
    ):
        if name in globals():
            setattr(_IMPL, name, globals()[name])
    return _IMPL.main()


if __name__ == "__main__":
    raise SystemExit(main())
