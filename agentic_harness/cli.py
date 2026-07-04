"""Command line interface for agentic-harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.adapters.tmux import TmuxWorker
from agentic_harness.core.config import (
    CONFIG_DIR,
    CONFIG_NAME,
    HarnessConfig,
    load_config,
    write_default_config,
)
from agentic_harness.core.errors import ConfigError
from agentic_harness.core.review import (
    DeterministicReviewer,
    ReviewCriterion,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
)
from agentic_harness.core.state import GoalStatus
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.worker import Worker


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
    run = sub.add_parser("run", help="Start, continue, and review a goal")
    run.add_argument("objective")
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
    if args.command == "run":
        goal = supervisor.start(args.objective)
        goal = supervisor.continue_goal()
        if goal.status is GoalStatus.REVIEW:
            goal = supervisor.review()
        print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
        return 0 if goal.status is GoalStatus.DONE else 1
    if args.command == "status":
        active_goal = supervisor.status()
        print(
            json.dumps(
                active_goal.to_dict() if active_goal else {"active": False},
                indent=2,
                sort_keys=True,
            )
        )
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
        repaired_goal = supervisor.repair()
        print(
            json.dumps(
                repaired_goal.to_dict() if repaired_goal else {"repaired": False},
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    return 2


def build_supervisor(project_dir: Path) -> Supervisor:
    config = load_config(project_dir)
    worker: Worker | None = None
    if config.worker == "shell" and config.shell_command:
        worker = ShellWorker(config.shell_command, cwd=project_dir)
    elif config.worker == "coding_agent":
        worker = CodingAgentWorker(
            config.coding_agent_command,
            cwd=project_dir,
            timeout=config.coding_agent_timeout,
            transcript_path=config.coding_agent_transcript,
        )
    elif config.worker == "tmux":
        worker = TmuxWorker(
            config.tmux_command,
            session_prefix=config.tmux_session_prefix,
            cwd=project_dir,
        )
    elif config.worker == "local_llm":
        worker = LocalLLMAdapter(
            endpoint=config.llm_endpoint,
            model=config.llm_model,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout,
        )
    elif config.worker == "github_actions":
        worker = GitHubActionsAdapter(
            owner=config.github_owner,
            repo=config.github_repo,
            workflow_id=config.github_workflow_id,
            token=config.github_token,
            ref=config.github_ref,
            wait_for_completion=config.github_wait,
            poll_interval=config.github_poll_interval,
            timeout=config.github_timeout,
            api_version=config.github_api_version,
        )
    criteria = review_criteria_from_config(config, project_dir)
    return Supervisor(
        project_dir=project_dir,
        worker=worker,
        reviewer=DeterministicReviewer(criteria) if criteria else None,
        allow_noop_success=config.allow_noop_success,
    )


def review_criteria_from_config(config: HarnessConfig, project_dir: Path) -> list[ReviewCriterion]:
    criteria: list[ReviewCriterion] = []
    if config.review_command:
        criteria.append(
            command_passes(
                config.review_command,
                cwd=project_dir,
                timeout=config.review_command_timeout,
            )
        )
    if config.review_artifact:
        criteria.append(artifact_exists(project_dir, config.review_artifact))
    if config.review_file_changed:
        criteria.append(file_changed(project_dir, config.review_file_changed))
    if config.review_git_clean:
        criteria.append(git_clean(project_dir))
    return criteria


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
