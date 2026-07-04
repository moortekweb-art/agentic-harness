"""Command line interface for agentic-harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.core.config import CONFIG_DIR, CONFIG_NAME, load_config, write_default_config
from agentic_harness.core.errors import ConfigError
from agentic_harness.core.supervisor import Supervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-harness")
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project directory containing .agentic-harness/config.yml.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Generate .agentic-harness/config.yml")
    start = sub.add_parser("start", help="Start a goal")
    start.add_argument("objective")
    sub.add_parser("status", help="Show current goal state")
    sub.add_parser("continue", help="Advance the active goal")
    sub.add_parser("review", help="Run deterministic review")
    sub.add_parser("repair", help="Repair marker-only failures")
    sub.add_parser("doctor", help="Diagnose config and local state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = Path(args.project_dir)
    if args.command == "init":
        path = write_default_config(project_dir)
        print(f"created {path}")
        return 0
    if args.command == "doctor":
        payload = doctor(project_dir)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
        supervisor = build_supervisor(project_dir)
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    if args.command == "start":
        goal = supervisor.start(args.objective)
        print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        goal = supervisor.status()
        print(json.dumps(goal.to_dict() if goal else {"active": False}, indent=2, sort_keys=True))
        return 0
    if args.command == "continue":
        goal = supervisor.continue_goal()
        print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "review":
        goal = supervisor.review()
        print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "repair":
        goal = supervisor.repair()
        print(json.dumps(goal.to_dict() if goal else {"repaired": False}, indent=2, sort_keys=True))
        return 0
    return 2


def build_supervisor(project_dir: Path) -> Supervisor:
    config = load_config(project_dir)
    worker = None
    if config.worker == "shell" and config.shell_command:
        worker = ShellWorker(config.shell_command, cwd=project_dir)
    return Supervisor(
        project_dir=project_dir,
        worker=worker,
        allow_noop_success=config.allow_noop_success,
    )


def doctor(project_dir: str | Path = ".") -> dict[str, object]:
    root = Path(project_dir)
    config_path = root / CONFIG_DIR / CONFIG_NAME
    state_dir = root / CONFIG_DIR
    config_ok = config_path.exists()
    config_message = str(config_path) if config_path.exists() else "config not initialized"
    if config_path.exists():
        try:
            load_config(root)
        except ConfigError as exc:
            config_ok = False
            config_message = str(exc)
    checks = [
        {
            "name": "project_dir",
            "ok": root.exists(),
            "message": str(root),
        },
        {
            "name": "config",
            "ok": config_ok,
            "message": config_message,
        },
        {
            "name": "state_dir",
            "ok": state_dir.exists(),
            "message": str(state_dir) if state_dir.exists() else "state dir not initialized",
        },
    ]
    return {"ok": all(bool(check["ok"]) for check in checks), "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
