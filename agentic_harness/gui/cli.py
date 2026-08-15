"""Console launcher for the Agentic Harness GUI service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from agentic_harness.core.local_goal_bridge import DOC_ROOT_ENV
from agentic_harness.gui.server import run_server_from_args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-harness-gui",
        description="Start the Agentic Harness GUI service from this installation.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Default: ask the OS for a free local port.",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help=(
            "With the embedded backend, the workspace whose .agentic-harness state and "
            "files the app should use. With --backend local-goal, this only scopes the "
            "saved GUI task history and session state; the execution work area is "
            "--doc-root. Pass the same path to both flags unless you deliberately want "
            "a separate saved-task store."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("embedded", "local-goal"),
        default="embedded",
        help="Execution backend. Default: portable embedded engine.",
    )
    parser.add_argument(
        "--doc-root",
        default=None,
        help=(
            "Optional legacy local-goal backend checkout root. This is the managed "
            "execution work area the GUI reports and the backend runs in. Explicit "
            f"value wins; otherwise {DOC_ROOT_ENV} or the current directory is used."
        ),
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.project_dir = Path(args.project_dir).expanduser()
    return run_server_from_args(args)
