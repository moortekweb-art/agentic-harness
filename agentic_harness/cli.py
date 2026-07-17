"""Command line interface for agentic-harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

from agentic_harness.core.autonomy import AutonomousRunner, AutonomyPolicy
from agentic_harness.core.config import (
    CONFIG_DIR,
    CONFIG_NAME,
    HarnessConfig,
    TOOL_CONFIGS,
    load_config,
    write_default_config,
    write_tool_config,
)
from agentic_harness.core.demos import create_demo, demo_names
from agentic_harness.core.errors import ConfigError, HarnessError, NoActiveGoalError
from agentic_harness.core.factory import (
    autonomy_policy_from_config,
    build_supervisor,
)
from agentic_harness.core.local_goal_bridge import (
    CommandResult,
    DOC_ROOT_ENV,
    LocalGoalBridge,
    Mode3AGoalOptions,
    format_command_result,
    human_mode_by_key,
    resolve_doc_root,
)
from agentic_harness.gui.server import run_server_from_args
from agentic_harness.core.recipes import Recipe, explain_recipe, list_recipes, load_recipe
from agentic_harness.core.presentation import safe_inline_text
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.reporting import build_run_receipt
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.safety import format_command, goal_safety_metadata, split_command
from agentic_harness.core.workspace import format_workspace_change_lines, workspace_change_summary


RECIPE_COMMANDS = {
    "changelog",
    "fix-tests",
    "lint-fix",
    "typecheck-fix",
    "update-docs",
    "verify-tests",
}

DIST_NAME = "local-agentic-harness"
REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_ROOT_HELP = (
    "Path to an optional external local-goal backend root. Explicit value wins; "
    f"otherwise {DOC_ROOT_ENV} is used when non-empty; otherwise the current "
    "directory is used. The Python package does not install local-goal."
)


class HarnessParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        if self.prog == "agentic-harness":
            return format_start_here_text() + "\n\nAdvanced: agentic-harness <command> --help\n"
        return super().format_help()


def build_parser() -> argparse.ArgumentParser:
    parser = HarnessParser(prog="agentic-harness")
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the installed agentic-harness version.",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project directory containing .agentic-harness/config.yml.",
    )
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init", help="Generate .agentic-harness/config.yml")
    init.add_argument("tool", nargs="?", choices=sorted(TOOL_CONFIGS))
    init.add_argument("--force", action="store_true", help="Replace an existing config.yml.")
    init_agent = sub.add_parser("init-agent", help="Generate a coding-agent backend config")
    init_agent.add_argument("tool", choices=sorted(TOOL_CONFIGS))
    init_agent.add_argument("--force", action="store_true", help="Replace an existing config.yml.")
    sub.add_parser("quickstart", help="Print the shortest setup path for this machine")
    sub.add_parser("start-here", help="Show the beginner command guide")
    sub.add_parser("guide", help="Show the beginner command guide")
    sub.add_parser("version", help="Print the installed agentic-harness version")
    easy_do = sub.add_parser("do", help="Run one goal and require independent verification")
    easy_do.add_argument("objective")
    easy_do.add_argument(
        "--blocker-limit",
        type=int,
        default=3,
        help="Stop for review after this many identical no-progress blockers.",
    )
    easy_do.add_argument("--safe-area", action="append", default=[])
    easy_do.add_argument(
        "--check",
        "--verify",
        dest="check",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Independent command to run before accepting done; repeat for multiple checks.",
    )
    easy_do.add_argument("--json", action="store_true", help="Print final goal JSON.")
    work = sub.add_parser("work", help="Interactive no-jargon mode picker")
    work.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    gui = sub.add_parser("gui", help="Open the local browser app")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind. Default: ask the OS for a free local port.",
    )
    gui.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    gui.add_argument(
        "--backend",
        choices=("embedded", "local-goal"),
        default="embedded",
        help="Use the portable embedded engine (default) or optional legacy local-goal backend.",
    )
    gui.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    sub.add_parser("modes", help="Explain the four human work modes")
    sub.add_parser("check", help="Show the current durable task result and evidence state")
    easy_watch = sub.add_parser(
        "watch",
        help="Run one diagnostic monitor pass; background supervision normally owns this",
    )
    easy_watch.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    popos = sub.add_parser("setup", help="Show simple Linux/Ubuntu setup and runtime checks")
    popos.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    popos_advanced = sub.add_parser("popos-setup", help="Show Linux/Ubuntu install and runtime checks")
    popos_advanced.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    mode3a_run = sub.add_parser(
        "mode3a-run",
        help="Run a plain-English task through an optional external orchestration lane",
    )
    mode3a_run.add_argument("objective")
    mode3a_run.add_argument("--allowed", action="append", default=[])
    mode3a_run.add_argument("--verify", action="append", default=[])
    mode3a_run.add_argument("--guardrail", action="append", default=[])
    mode3a_run.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    mode3a_run.add_argument(
        "--monitor",
        action="store_true",
        help="Run one immediate diagnostic monitor pass after queueing.",
    )
    mode3a_run.add_argument("--json", action="store_true", help="Print raw local-goal JSON output.")
    mode3a_status = sub.add_parser(
        "mode3a-status",
        help="Show optional external local-goal status (compatibility command)",
    )
    mode3a_status.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    mode3a_status.add_argument("--json", action="store_true")
    mode3a_monitor = sub.add_parser(
        "mode3a-monitor",
        help="Run one optional external-backend monitor pass (compatibility command)",
    )
    mode3a_monitor.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    mode3a_monitor.add_argument("--json", action="store_true")
    sub.add_parser("agents", help="Show supported backend tools found on PATH")
    create_demo_cmd = sub.add_parser("create-demo", help="Create a runnable example project")
    create_demo_cmd.add_argument("demo", choices=demo_names())
    create_demo_cmd.add_argument("path", nargs="?", default="agentic-harness-fix-tests-demo")
    create_demo_cmd.add_argument("--force", action="store_true", help="Overwrite known demo files.")
    run_demo_cmd = sub.add_parser("run-demo", help="Create and run a packaged demo end to end")
    run_demo_cmd.add_argument("demo", choices=demo_names())
    run_demo_cmd.add_argument("path", nargs="?", default="agentic-harness-fix-tests-demo")
    run_demo_cmd.add_argument("--force", action="store_true", help="Overwrite known demo files.")
    run_demo_cmd.add_argument(
        "--no-install",
        action="store_true",
        help="Skip installing the demo requirements file before running.",
    )
    sub.add_parser("next", help="Show the next safe command for this project")
    sub.add_parser("selftest", help="Run a temporary no-setup harness smoke test")
    release_smoke = sub.add_parser("release-smoke", help="Build and smoke-test release artifacts")
    release_smoke.add_argument(
        "--dist-dir",
        default="",
        help="Directory for built artifacts. Defaults to a temporary directory.",
    )
    sub.add_parser("recipes", help="List built-in recipes")
    easy = sub.add_parser("easy", help="Auto-configure a backend if needed, then run a recipe")
    easy.add_argument("recipe", nargs="?", default="fix-tests")
    easy.add_argument("--agent", choices=["auto", *sorted(TOOL_CONFIGS)], default="auto")
    easy.add_argument("--explain", action="store_true", help="Show what would run.")
    run_recipe = sub.add_parser("run-recipe", help="Run a built-in recipe")
    run_recipe.add_argument("recipe")
    run_recipe.add_argument("--explain", action="store_true", help="Show what would run.")
    run_recipe.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    run_recipe.add_argument(
        "--until-done",
        action="store_true",
        help="Continue until done or one no-progress blocker repeats --max-attempts times.",
    )
    run_recipe.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Consecutive identical no-progress blockers before stopping. Default: 3.",
    )
    for recipe_name in sorted(RECIPE_COMMANDS):
        recipe_cmd = sub.add_parser(recipe_name, help=f"Run the built-in {recipe_name} recipe")
        recipe_cmd.add_argument("--explain", action="store_true", help="Show what would run.")
        recipe_cmd.add_argument("--json", action="store_true", help="Print the final goal JSON.")
        recipe_cmd.add_argument(
            "--until-done",
            action="store_true",
            help="Continue until done or one no-progress blocker repeats --max-attempts times.",
        )
        recipe_cmd.add_argument(
            "--max-attempts",
            type=int,
            default=3,
            help="Consecutive identical no-progress blockers before stopping. Default: 3.",
        )
    sub.add_parser("report", help="Show a plain-language status report")
    start = sub.add_parser("start", help="Start a goal")
    start.add_argument("objective")
    run = sub.add_parser("run", help="Start, continue, and review a goal")
    run.add_argument("objective")
    run.add_argument(
        "--check",
        "--verify",
        dest="check",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Independent command to run before accepting done; repeat for multiple checks.",
    )
    run.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    drive = sub.add_parser(
        "run-until-done",
        help="Start or resume a goal and continue while meaningful progress is possible",
    )
    drive.add_argument("objective", nargs="?", help="Objective to start if no goal exists.")
    drive.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Consecutive identical no-progress blockers before stopping. Default: 3.",
    )
    drive.add_argument(
        "--check",
        "--verify",
        dest="check",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Independent command to run before accepting done; repeat for multiple checks.",
    )
    drive.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    goal_cmd = sub.add_parser(
        "goal",
        help="Start or resume an evidence-driven autonomous goal",
    )
    goal_cmd.add_argument("objective", nargs="?", help="Full objective to start, or omit to resume.")
    goal_cmd.add_argument(
        "--blocker-limit",
        type=int,
        default=3,
        help="Consecutive identical no-progress blockers before human review. Default: 3.",
    )
    goal_cmd.add_argument("--safe-area", action="append", default=[])
    goal_cmd.add_argument(
        "--check",
        "--verify",
        dest="check",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Independent command to run before accepting done; repeat for multiple checks.",
    )
    goal_cmd.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    status = sub.add_parser("status", help="Show current goal state")
    status.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format. Default: text.",
    )
    sub.add_parser("continue", help="Advance the active goal")
    approve_spec = sub.add_parser(
        "approve-spec",
        help="Approve pending high-assurance completion conditions",
    )
    approve_spec.add_argument(
        "--requirement",
        action="append",
        default=None,
        help="Replace the proposed conditions with this plain-language condition; repeatable.",
    )
    review = sub.add_parser("review", help="Run deterministic independent review")
    review.add_argument(
        "--check",
        "--verify",
        dest="check",
        action="append",
        default=[],
        metavar="COMMAND",
        help="Independent command to run; defaults to the saved goal or project check.",
    )
    sub.add_parser("repair", help="Repair marker-only failures")
    sub.add_parser("restart", help="Restart a failed goal (FAILED -> PLANNING)")
    sub.add_parser("reset-loop-guard", help="Reset the auto-continue circuit breaker")
    accept = sub.add_parser("accept", help="Accept a review-passed goal as done")
    accept.add_argument(
        "--reason",
        default="accepted by operator",
        help="Reason for accepting the goal.",
    )
    sub.add_parser("doctor", help="Diagnose config and local state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(format_version_text())
        return 0
    if args.command is None:
        build_parser().print_help()
        return 0
    project_dir = Path(args.project_dir)
    if args.command == "version":
        print(format_version_text())
        return 0
    if args.command in {"init", "init-agent"}:
        try:
            if args.command == "init-agent" or args.tool:
                path = write_tool_config(project_dir, args.tool, force=args.force)
                tool = args.tool
            else:
                tool = preferred_agent_tool()
                if tool:
                    path = write_tool_config(project_dir, tool, force=args.force)
                else:
                    path = write_default_config(project_dir)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(format_init_text(path, tool))
        return 0
    if args.command == "agents":
        print(format_agents_text())
        return 0
    if args.command == "quickstart":
        print(format_quickstart_text())
        return 0
    if args.command in {"start-here", "guide"}:
        print(format_start_here_text())
        return 0
    if args.command in {"setup", "popos-setup"}:
        print(format_portable_setup(project_dir))
        return 0
    if args.command == "modes":
        print(format_portable_goal_flow())
        return 0
    if args.command == "work":
        return run_interactive_work_command(args, project_dir)
    if args.command == "gui":
        return run_server_from_args(args)
    if args.command == "do":
        return run_easy_do_command(args, project_dir)
    if args.command == "check":
        return run_easy_check_command(args, project_dir)
    if args.command == "watch":
        return run_easy_watch_command(args, project_dir)
    if args.command == "mode3a-run":
        return run_mode3a_command(args)
    if args.command == "mode3a-status":
        return run_mode3a_status(args)
    if args.command == "mode3a-monitor":
        return run_mode3a_monitor(args)
    if args.command == "create-demo":
        try:
            demo_path = create_demo(args.demo, args.path, force=args.force)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(format_create_demo_text(args.demo, demo_path))
        return 0
    if args.command == "run-demo":
        return run_demo(args.demo, Path(args.path), force=args.force, install=not args.no_install)
    if args.command == "next":
        print(format_next_text(project_dir))
        return 0
    if args.command == "selftest":
        return run_selftest()
    if args.command == "release-smoke":
        return run_release_smoke(project_dir, Path(args.dist_dir) if args.dist_dir else None)
    if args.command == "recipes":
        for recipe in list_recipes():
            print(f"{recipe.name}: {recipe.description}")
        return 0
    if args.command == "easy":
        try:
            recipe = load_recipe(args.recipe)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        if args.explain:
            print(format_easy_explain_text(project_dir, recipe, args.agent))
            return 0
        return run_easy(project_dir, recipe, args.agent)
    if args.command == "run-recipe" or args.command in RECIPE_COMMANDS:
        recipe_name = args.recipe if args.command == "run-recipe" else args.command
        try:
            recipe = load_recipe(recipe_name)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        if args.explain:
            print(explain_recipe(recipe))
            return 0
        return run_recipe(
            project_dir,
            recipe,
            output_json=args.json,
            until_done=args.until_done,
            max_attempts=args.max_attempts,
        )
    if args.command == "doctor":
        payload = doctor(project_dir)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    initialized_config: tuple[Path, str] | None = None
    try:
        if args.command == "run" or (
            args.command in {"run-until-done", "goal"} and args.objective
        ):
            initialized_config = ensure_execution_config(project_dir)
        supervisor = build_supervisor(project_dir)
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    try:
        if args.command == "start":
            config = load_config(project_dir)
            goal = supervisor.start(
                args.objective,
                metadata=_cli_goal_safety_metadata(project_dir, config),
            )
            print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            config = load_config(project_dir)
            review_commands = resolve_review_commands(args.check, config)
            supervisor = build_supervisor(project_dir, review_commands=review_commands)
            goal = supervisor.start(
                args.objective,
                metadata=_cli_goal_safety_metadata(
                    project_dir,
                    config,
                    review_commands=review_commands,
                ),
            )
            goal = supervisor.continue_goal()
            if goal.status is GoalStatus.REVIEW:
                goal = supervisor.review()
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = goal_workspace_changes(project_dir, goal)
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return verified_goal_exit_code(goal)
        if args.command == "run-until-done":
            config = load_config(project_dir)
            current = supervisor.status()
            objective = args.objective
            if current is None and not objective:
                raise NoActiveGoalError(
                    "no active goal; provide an objective to start one"
                )
            resume_goal = (
                current
                if current is not None
                and (not objective or current.status not in {GoalStatus.DONE, GoalStatus.FAILED})
                else None
            )
            review_commands = resolve_review_commands(
                args.check,
                config,
                goal=resume_goal,
            )
            supervisor = build_supervisor(project_dir, review_commands=review_commands)
            current = supervisor.status()
            if objective and (
                current is None or current.status in {GoalStatus.DONE, GoalStatus.FAILED}
            ):
                supervisor.start(
                    objective,
                    metadata=_cli_goal_safety_metadata(
                        project_dir,
                        config,
                        review_commands=review_commands,
                    ),
                )
                objective = None
            else:
                _ensure_cli_goal_safety(
                    supervisor,
                    project_dir,
                    config,
                    review_commands=review_commands,
                )
            goal = run_until_done(
                supervisor,
                objective=objective,
                max_attempts=args.max_attempts,
            )
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = goal_workspace_changes(project_dir, goal)
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return verified_goal_exit_code(goal)
        if args.command == "goal":
            try:
                config = load_config(project_dir)
                current = supervisor.status()
                resume_goal = (
                    current
                    if current is not None
                    and (
                        not args.objective
                        or current.status not in {GoalStatus.DONE, GoalStatus.FAILED}
                    )
                    else None
                )
                review_commands = resolve_review_commands(
                    args.check,
                    config,
                    goal=resume_goal,
                )
                supervisor = build_supervisor(
                    project_dir,
                    review_commands=review_commands,
                )
                policy = autonomy_policy_from_config(
                    config,
                    repeated_blocker_limit=args.blocker_limit,
                    require_completion_claim=True,
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            current = supervisor.status()
            if args.objective and (
                current is None or current.status in {GoalStatus.DONE, GoalStatus.FAILED}
            ):
                supervisor.start(
                    args.objective,
                    metadata=goal_safety_metadata(
                        project_dir,
                        allowed_paths=list(args.safe_area),
                        review_commands=review_commands,
                        path_enforcement=config.worker == "model_agent",
                        secret_env_names=[config.llm_api_key_env],
                        interface="cli",
                    ),
                )
                goal = AutonomousRunner(supervisor, policy=policy).run()
            else:
                _ensure_cli_goal_safety(
                    supervisor,
                    project_dir,
                    config,
                    review_commands=review_commands,
                )
                goal = AutonomousRunner(supervisor, policy=policy).run(args.objective)
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = goal_workspace_changes(project_dir, goal)
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return verified_goal_exit_code(goal)
        if args.command == "status":
            active_goal = supervisor.status()
            if args.format == "text":
                print(format_status_text(active_goal))
            else:
                print(
                    json.dumps(
                        status_json_payload(active_goal),
                        indent=2,
                        sort_keys=True,
                    )
                )
            return 0
        if args.command == "report":
            active_goal = supervisor.status()
            if active_goal is None:
                print(format_report_text(None))
                return 0
            reported_goal, report_rel = write_goal_report(supervisor, project_dir, active_goal)
            print(
                format_report_text(
                    reported_goal,
                    report_path=report_rel,
                    workspace_changes=goal_workspace_changes(project_dir, reported_goal),
                )
            )
            return 0
        if args.command == "continue":
            config = load_config(project_dir)
            _ensure_cli_goal_safety(supervisor, project_dir, config)
            goal = supervisor.continue_goal()
            print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            return 0
        if args.command == "approve-spec":
            config = load_config(project_dir)
            supervisor = build_supervisor(project_dir)
            policy = autonomy_policy_from_config(config)
            goal = AutonomousRunner(supervisor, policy=policy).approve_specification(
                args.requirement
            )
            print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            return 0
        if args.command == "review":
            config = load_config(project_dir)
            current = supervisor.status()
            review_commands = resolve_review_commands(
                args.check,
                config,
                goal=current,
            )
            supervisor = build_supervisor(project_dir, review_commands=review_commands)
            _ensure_cli_goal_safety(
                supervisor,
                project_dir,
                config,
                review_commands=review_commands,
            )
            goal = supervisor.review()
            print(json.dumps(_review_command_payload(goal), indent=2, sort_keys=True))
            return verified_goal_exit_code(goal)
        if args.command == "repair":
            repaired_goal = supervisor.repair()
            print(
                json.dumps(
                    public_goal_payload(repaired_goal)
                    if repaired_goal
                    else {"repaired": False},
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "restart":
            goal = supervisor.restart()
            print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            return 0
        if args.command == "accept":
            current = supervisor.status()
            if current is None:
                raise NoActiveGoalError("no active goal")
            receipt = build_run_receipt(current)
            if receipt.category != "verified_done":
                raise HarnessError(
                    f"cannot accept goal {current.id}; "
                    f"{receipt.label}: {receipt.trusted_reason}"
                )
            goal = supervisor.accept(reason=args.reason)
            print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
            return verified_goal_exit_code(goal)
        if args.command == "reset-loop-guard":
            ok = supervisor.reset_loop_guard()
            print(json.dumps({"ok": ok, "message": "loop guard reset"}, indent=2, sort_keys=True))
            return 0
    except HarnessError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    return 0


def run_until_done(
    supervisor: Supervisor,
    *,
    objective: str | None = None,
    max_attempts: int = 3,
) -> Goal:
    if max_attempts < 1:
        raise ConfigError("--max-attempts must be at least 1")
    policy = AutonomyPolicy(
        repeated_blocker_limit=max_attempts,
        require_completion_claim=False,
    )
    return AutonomousRunner(supervisor, policy=policy).run(objective)


def verified_goal_exit_code(goal: Goal) -> int:
    return 0 if build_run_receipt(goal).category == "verified_done" else 1


def resolve_review_commands(
    explicit: list[str],
    config: HarnessConfig,
    *,
    goal: Goal | None = None,
) -> list[list[str]]:
    commands: list[list[str]] = []
    for value in explicit:
        if not value.strip():
            continue
        try:
            command = split_command(value)
        except ValueError as exc:
            raise ConfigError(f"invalid --check command: {exc}") from exc
        if not command:
            raise ConfigError("invalid --check command: command is empty")
        commands.append(command)
    if not commands and goal is not None:
        commands = persisted_review_commands(goal)
    if not commands and config.review_command:
        commands = [config.review_command]
    if not commands:
        raise ConfigError(
            "no independent verification command is configured; "
            "add --check COMMAND or save a project verification command"
        )
    return commands


def persisted_review_commands(goal: Goal) -> list[list[str]]:
    safety = goal.metadata.get("safety")
    if not isinstance(safety, dict):
        return []
    rows = safety.get("checks")
    if not isinstance(rows, list):
        return []
    commands: list[list[str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        argv = row.get("argv")
        if isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv):
            commands.append(list(argv))
    return commands


def _cli_goal_safety_metadata(
    project_dir: Path,
    config: HarnessConfig,
    *,
    review_commands: list[list[str]] | None = None,
) -> dict[str, Any]:
    commands = (
        review_commands
        if review_commands is not None
        else ([config.review_command] if config.review_command else [])
    )
    return goal_safety_metadata(
        project_dir,
        allowed_paths=[],
        review_commands=commands,
        path_enforcement=config.worker == "model_agent",
        secret_env_names=[config.llm_api_key_env],
        interface="cli",
    )


def _ensure_cli_goal_safety(
    supervisor: Supervisor,
    project_dir: Path,
    config: HarnessConfig,
    *,
    review_commands: list[list[str]] | None = None,
) -> None:
    goal = supervisor.status()
    if goal is None:
        return
    safety = goal.metadata.get("safety")
    complete_safety = (
        isinstance(safety, dict)
        and isinstance(safety.get("allowed_paths"), list)
        and isinstance(safety.get("preexisting_changes"), list)
    )
    if complete_safety and review_commands is None:
        return
    metadata = _cli_goal_safety_metadata(
        project_dir,
        config,
        review_commands=review_commands,
    )
    generated_safety = metadata["safety"]
    with supervisor.store.autonomy_locked():
        with supervisor.store.locked():
            current = supervisor.store.read_current_goal()
            if current is None or current.id != goal.id:
                raise HarnessError("active goal changed while safety metadata was prepared")
            current_safety = current.metadata.get("safety")
            if isinstance(current_safety, dict):
                merged_safety = dict(current_safety)
                for key, value in generated_safety.items():
                    if key == "checks":
                        if review_commands is not None or not isinstance(
                            merged_safety.get(key), list
                        ):
                            merged_safety[key] = value
                    elif key in {"allowed_paths", "preexisting_changes"}:
                        if not isinstance(merged_safety.get(key), list):
                            merged_safety[key] = value
                    elif key not in merged_safety:
                        merged_safety[key] = value
                current.metadata["safety"] = merged_safety
                current.metadata.setdefault("interface", metadata["interface"])
            else:
                current.metadata.update(metadata)
            supervisor.store.write_goal(current)


def run_mode3a_command(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "local-goal backend not found or not executable",
                    "path": str(bridge.local_goal),
                    "next": local_goal_setup_hint(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    supervision = bridge.background_supervision()
    if supervision.get("active") is not True:
        print("External task was not queued because unattended supervision is unavailable.")
        print(str(supervision.get("summary") or "Background watcher could not be verified."))
        return 2
    try:
        result = bridge.enqueue_mode3a(
            Mode3AGoalOptions(
                objective=args.objective,
                allowed_paths=tuple(args.allowed),
                verification=tuple(args.verify),
                guardrails=tuple(args.guardrail),
            )
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2

    if args.json:
        print(format_command_result(result))
    else:
        print("External orchestration task queued.")
        print(format_command_result(result))
        print("Background supervisor owns continuation, repair, review, and acceptance.")
        print("You can close this. Status is available with agentic-harness check.")
    if result.returncode != 0:
        return result.returncode
    if args.monitor:
        monitor_result = bridge.monitor(json_output=args.json)
        print(format_command_result(monitor_result))
        return monitor_result.returncode
    return 0


def run_easy_do_command(args: argparse.Namespace, project_dir: Path) -> int:
    try:
        initialized = ensure_execution_config(project_dir)
        config = load_config(project_dir)
        review_commands = resolve_review_commands(args.check, config)
        supervisor = build_supervisor(project_dir, review_commands=review_commands)
        supervisor.start(
            args.objective,
            metadata=goal_safety_metadata(
                project_dir,
                allowed_paths=list(args.safe_area),
                review_commands=review_commands,
                path_enforcement=config.worker == "model_agent",
                secret_env_names=[config.llm_api_key_env],
                interface="cli",
            ),
        )
        policy = autonomy_policy_from_config(
            config,
            repeated_blocker_limit=args.blocker_limit,
            require_completion_claim=True,
        )
        goal = AutonomousRunner(supervisor, policy=policy).run()
        goal, report_path = write_goal_report(supervisor, project_dir, goal)
    except (ConfigError, HarnessError, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        elif "no independent verification command" in str(exc):
            print("Could not start verified work.")
            print(f"Problem: {exc}")
            print('Next: rerun with --check "your test command".')
            print("No goal was started.")
        else:
            print(f"Could not complete verified work: {exc}")
        return 2
    if args.json:
        print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
    else:
        if initialized is not None:
            print(format_init_text(*initialized))
        print(
            format_report_text(
                goal,
                report_path=report_path,
                workspace_changes=goal_workspace_changes(project_dir, goal),
            )
        )
    return verified_goal_exit_code(goal)


def run_interactive_work_command(args: argparse.Namespace, project_dir: Path) -> int:
    print("Agentic Harness verified goal")
    print(f"Workspace: {project_dir.resolve()}")
    print("")
    objective = input("What do you want done? ").strip()
    if not objective:
        print("No task entered. Nothing started.")
        return 2
    portable_args = argparse.Namespace(
        objective=objective,
        blocker_limit=3,
        safe_area=[],
        check=[],
        json=False,
    )
    return run_easy_do_command(portable_args, project_dir)


def start_human_mode(
    bridge: LocalGoalBridge,
    *,
    mode_key: str,
    objective: str,
    safe_areas: tuple[str, ...],
    checks: tuple[str, ...],
) -> CommandResult:
    mode = human_mode_by_key(mode_key)
    return bridge.start_human_goal(
        mode_key=mode.key,
        objective=objective,
        safe_areas=safe_areas,
        checks=checks,
    )


def local_goal_setup_hint() -> str:
    return (
        "install or expose the optional external local-goal backend, then pass "
        f"--doc-root, set {DOC_ROOT_ENV}, or set AGENTIC_HARNESS_LOCAL_GOAL; "
        "run agentic-harness setup for detected paths"
    )


def run_easy_check_command(args: argparse.Namespace, project_dir: Path) -> int:
    try:
        supervisor = build_supervisor(project_dir)
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    print(format_status_text(supervisor.status()))
    return 0


def run_easy_watch_command(args: argparse.Namespace, project_dir: Path) -> int:
    print("Current durable task snapshot:")
    return run_easy_check_command(args, project_dir)


def _friendly_queue_summary(stdout: str) -> str:
    queue_id = ""
    for line in stdout.splitlines():
        if line.startswith("queued_id="):
            queue_id = line.split("=", 1)[1].strip()
            break
    if queue_id:
        return f"Work ticket: {queue_id}"
    cleaned = stdout.strip()
    return cleaned if cleaned else "Work ticket created."


def run_mode3a_status(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print(f"local-goal backend not found or not executable: {bridge.local_goal}")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    result = bridge.status(json_output=args.json)
    print(format_command_result(result))
    return result.returncode


def run_mode3a_monitor(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print(f"local-goal backend not found or not executable: {bridge.local_goal}")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    result = bridge.monitor(json_output=args.json)
    print(format_command_result(result))
    return result.returncode


def run_recipe(
    project_dir: Path,
    recipe: Recipe,
    *,
    output_json: bool = False,
    until_done: bool = False,
    max_attempts: int = 3,
) -> int:
    initialized_config: tuple[Path, str] | None = None
    try:
        initialized_config = ensure_recipe_config(project_dir)
        config = load_config(project_dir)
        review_command = recipe_review_command(project_dir, recipe.review_command)
        review_commands = [review_command] if review_command else []
        supervisor = build_supervisor(
            project_dir,
            review_commands=review_commands,
            review_command_timeout=recipe.review_command_timeout,
        )
        if until_done:
            current = supervisor.status()
            if current is None or current.status in {GoalStatus.DONE, GoalStatus.FAILED}:
                supervisor.start(
                    recipe.objective,
                    metadata=_cli_goal_safety_metadata(
                        project_dir,
                        config,
                        review_commands=review_commands,
                    ),
                )
                objective = None
            elif current.objective == recipe.objective:
                _ensure_cli_goal_safety(
                    supervisor,
                    project_dir,
                    config,
                    review_commands=review_commands,
                )
                objective = None
            else:
                objective = recipe.objective
            goal = run_until_done(
                supervisor,
                objective=objective,
                max_attempts=max_attempts,
            )
        else:
            goal = supervisor.start(
                recipe.objective,
                metadata=_cli_goal_safety_metadata(
                    project_dir,
                    config,
                    review_commands=review_commands,
                ),
            )
            goal = supervisor.continue_goal()
            if goal.status is GoalStatus.REVIEW:
                goal = supervisor.review()
    except (ConfigError, HarnessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    report_path: str | None = None
    try:
        goal, report_path = write_goal_report(supervisor, project_dir, goal)
    except (HarnessError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    if output_json:
        print(json.dumps(public_goal_payload(goal), indent=2, sort_keys=True))
    else:
        if initialized_config is not None:
            path, tool = initialized_config
            print(format_init_text(path, tool))
        print(
            format_recipe_result_text(
                recipe,
                goal,
                report_path=report_path,
                workspace_changes=goal_workspace_changes(project_dir, goal),
            )
        )
    return verified_goal_exit_code(goal)


def ensure_recipe_config(project_dir: Path) -> tuple[Path, str] | None:
    try:
        return ensure_execution_config(project_dir)
    except ConfigError as exc:
        raise ConfigError(
            "no .agentic-harness/config.yml and no coding-agent backend found; "
            "run agentic-harness init-agent codex or agentic-harness init-agent shell"
        ) from exc


def ensure_execution_config(project_dir: Path) -> tuple[Path, str] | None:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    if config_path.exists():
        return None
    selected = "shell" if is_packaged_demo_project(project_dir) else preferred_agent_tool()
    if selected is None:
        raise ConfigError(
            "no .agentic-harness/config.yml and no coding-agent backend found; "
            "run agentic-harness quickstart or agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force"
        )
    return write_tool_config(project_dir, selected), selected


def is_packaged_demo_project(project_dir: Path) -> bool:
    return all(
        (project_dir / path).exists()
        for path in (
            "mock_coding_agent.py",
            "reset_demo.py",
            "calculator.py",
            "tests/test_calculator.py",
        )
    )


def run_easy(project_dir: Path, recipe: Recipe, agent: str) -> int:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    if not config_path.exists():
        selected = resolve_easy_agent(agent)
        if selected is None:
            print(format_no_agent_text())
            return 2
        try:
            path = write_tool_config(project_dir, selected)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(format_init_text(path, selected))
    return run_recipe(project_dir, recipe)


def format_init_text(path: Path, tool: str | None) -> str:
    config_path = Path(path)
    if tool:
        lines = [f"Configured {tool} tool."]
    else:
        lines = ["Configured default project."]
    lines.append(f"Config: {config_path}")
    lines.append("Next: agentic-harness fix-tests" if tool else "Next: agentic-harness quickstart")
    return "\n".join(lines)


def package_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = payload.get("project", {}).get("version")
        if isinstance(version, str) and version:
            return version
    try:
        return metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


def format_version_text() -> str:
    return f"agentic-harness {package_version()}"


def run_selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="agentic-harness-selftest-") as tmp:
        project = Path(tmp)
        config_dir = project / CONFIG_DIR
        config_dir.mkdir()
        (config_dir / CONFIG_NAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "worker": "shell",
                    "shell_command": [sys.executable, "-c", "print('worker ok')"],
                    "review_command": [sys.executable, "-c", "print('review ok')"],
                }
            ),
            encoding="utf-8",
        )
        supervisor = build_supervisor(project)
        goal = supervisor.start("selftest")
        goal = supervisor.continue_goal()
        if goal.status is GoalStatus.REVIEW:
            goal = supervisor.review()
        if verified_goal_exit_code(goal) != 0:
            print(format_recipe_result_text(Recipe("selftest", "Self test", "selftest", []), goal))
            return 1
        print("Selftest: passed")
        print("Worker: passed")
        print("Review: passed")
        print("Next: agentic-harness quickstart")
        return 0


def run_release_smoke(project_dir: Path, dist_dir: Path | None = None) -> int:
    project_dir = project_dir.resolve()
    if not (project_dir / "pyproject.toml").exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "release-smoke must run from the project root containing pyproject.toml",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="agentic-harness-release-smoke-") as tmp:
        tmp_root = Path(tmp)
        out_dir = dist_dir or tmp_root / "dist"
        if not out_dir.is_absolute():
            out_dir = project_dir / out_dir
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        before_build = _artifact_snapshot(out_dir)
        if not _run_release_step(
            "Build wheel and sdist",
            [sys.executable, "-m", "build", "--outdir", str(out_dir)],
            cwd=project_dir,
        ):
            return 1
        try:
            wheel = _single_artifact(out_dir, "*.whl", before=before_build)
            sdist = _single_artifact(out_dir, "*.tar.gz", before=before_build)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        if not _run_release_step(
            "Check PyPI metadata",
            [sys.executable, "-m", "twine", "check", str(wheel), str(sdist)],
            cwd=project_dir,
        ):
            return 1
        for artifact in (wheel, sdist):
            if not _smoke_installed_artifact(artifact, tmp_root):
                return 1
        checksums = write_release_checksums(out_dir, [wheel, sdist])
        print("Release smoke: passed")
        print(f"Wheel: {wheel}")
        print(f"sdist: {sdist}")
        print(f"SHA256SUMS: {checksums}")
        return 0


def _artifact_snapshot(dist_dir: Path) -> dict[Path, tuple[int, int, int]]:
    snapshot: dict[Path, tuple[int, int, int]] = {}
    for pattern in ("*.whl", "*.tar.gz"):
        for path in dist_dir.glob(pattern):
            stat = path.stat()
            snapshot[path.resolve()] = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return snapshot


def _single_artifact(
    dist_dir: Path,
    pattern: str,
    *,
    before: dict[Path, tuple[int, int, int]],
) -> Path:
    matches: list[Path] = []
    for path in sorted(dist_dir.glob(pattern)):
        stat = path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if before.get(path.resolve()) != signature:
            matches.append(path)
    if len(matches) != 1:
        raise ConfigError(
            f"expected the build to create one {pattern} artifact in {dist_dir}, "
            f"found {len(matches)}"
        )
    return matches[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_release_checksums(dist_dir: Path, artifacts: list[Path]) -> Path:
    checksums_path = dist_dir / "SHA256SUMS"
    lines = [f"{sha256_file(artifact)}  {artifact.name}" for artifact in sorted(artifacts)]
    checksums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksums_path


def _smoke_installed_artifact(artifact: Path, tmp_root: Path) -> bool:
    stem = "sdist" if artifact.name.endswith(".tar.gz") else "wheel"
    smoke_root = tmp_root / stem
    venv_dir = smoke_root / "venv"
    if not _run_release_step(
        f"Create {stem} smoke venv",
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=tmp_root,
    ):
        return False
    python_bin = _venv_python(venv_dir)
    harness_bin = _venv_executable(venv_dir, "agentic-harness")
    gui_bin = _venv_executable(venv_dir, "agentic-harness-gui")
    if not _run_release_step(
        f"Install {artifact.name}",
        [str(python_bin), "-m", "pip", "install", str(artifact)],
        cwd=tmp_root,
    ):
        return False
    if not _run_release_step(
        f"Install {stem} recipe test dependency",
        [str(python_bin), "-m", "pip", "install", "pytest>=8"],
        cwd=tmp_root,
    ):
        return False
    smoke_env = os.environ.copy()
    smoke_env["PATH"] = str(python_bin.parent) + os.pathsep + smoke_env.get("PATH", "")
    for version_command in ("--version", "version"):
        if not _run_release_step(
            f"Smoke {stem} version {version_command}",
            [str(harness_bin), version_command],
            cwd=tmp_root,
            required_stdout=format_version_text(),
        ):
            return False
    if not _run_release_step(
        f"Smoke {stem} GUI entry point",
        [str(gui_bin), "--help"],
        cwd=tmp_root,
        required_stdout="usage: agentic-harness-gui",
    ):
        return False
    if not _run_release_step(
        f"Smoke {stem} packaged static assets",
        [
            str(python_bin),
            "-c",
            (
                "from importlib.resources import files; "
                "root = files('agentic_harness.gui.static'); "
                "assert all(root.joinpath(name).is_file() for name in "
                "('index.html', 'app.js', 'styles.css')); "
                "art = root.joinpath('illustrations'); "
                "assert all(art.joinpath(name).is_file() for name in "
                "('local-ai-connection.webp', 'verified-archive.webp', "
                "'setup-recovery.webp')); "
                "print('packaged static assets verified')"
            ),
        ],
        cwd=tmp_root,
        required_stdout="packaged static assets verified",
    ):
        return False
    for command in ("lint-fix", "typecheck-fix", "update-docs", "changelog", "verify-tests"):
        if not _run_release_step(
            f"Smoke {stem} command {command}",
            [str(harness_bin), command, "--explain"],
            cwd=tmp_root,
            required_stdout=f"Recipe: {command}",
        ):
            return False
    smoke_check = format_command(
        [str(python_bin), "-c", "raise SystemExit(0)"]
    )
    run_project = smoke_root / "run"
    run_config_dir = run_project / CONFIG_DIR
    run_config_dir.mkdir(parents=True)
    (run_config_dir / CONFIG_NAME).write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    if not _run_release_step(
        f"Smoke {stem} run",
        [
            str(harness_bin),
            "--project-dir",
            str(run_project),
            "run",
            "release smoke goal",
            "--check",
            smoke_check,
        ],
        cwd=tmp_root,
        required_stdout="Result: Verified done",
    ):
        return False
    if len(list((run_project / CONFIG_DIR / "runs").glob("*/report.md"))) != 1:
        print(f"{stem} smoke failed: run did not write one report artifact")
        return False
    if not _run_release_step(
        f"Smoke {stem} status default text",
        [
            str(harness_bin),
            "--project-dir",
            str(run_project),
            "status",
        ],
        cwd=tmp_root,
        required_stdout="Result: Verified done",
    ):
        return False
    driver_project = smoke_root / "run-until-done"
    config_dir = driver_project / CONFIG_DIR
    config_dir.mkdir(parents=True)
    (config_dir / CONFIG_NAME).write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    if not _run_release_step(
        f"Smoke {stem} run-until-done",
        [
            str(harness_bin),
            "--project-dir",
            str(driver_project),
            "run-until-done",
            "release smoke goal",
            "--check",
            smoke_check,
        ],
        cwd=tmp_root,
        required_stdout="Result: Verified done",
    ):
        return False
    if len(list((driver_project / CONFIG_DIR / "runs").glob("*/report.md"))) != 1:
        print(f"{stem} smoke failed: run-until-done did not write one report artifact")
        return False
    strict_project = smoke_root / "strict-goal"
    strict_config_dir = strict_project / CONFIG_DIR
    strict_config_dir.mkdir(parents=True)
    strict_worker = strict_project / "worker.py"
    strict_outcome = {
        "status": "complete",
        "summary": "installed strict goal verified",
        "current_subgoal": "final audit",
        "checkpoint": "verified",
        "plan": [{"step": "smoke installed goal", "status": "done"}],
        "requirement_status": [
            {
                "id": "R1",
                "status": "satisfied",
                "evidence": ["review:1"],
            }
        ],
        "blockers": [],
    }
    strict_worker.write_text(
        "import json\n"
        f"outcome = {strict_outcome!r}\n"
        'print("HARNESS_RESULT_JSON=" + json.dumps(outcome))\n',
        encoding="utf-8",
    )
    (strict_config_dir / CONFIG_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": [str(python_bin), str(strict_worker)],
                },
                "review": {
                    "command": [
                        str(python_bin),
                        "-c",
                        "print('independent review passed')",
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not _run_release_step(
        f"Smoke {stem} strict goal",
        [
            str(harness_bin),
            "--project-dir",
            str(strict_project),
            "goal",
            "verify the installed strict goal path",
            "--json",
        ],
        cwd=tmp_root,
        required_stdout='"accepted": true',
    ):
        return False
    recipe_project = smoke_root / "recipe-until-done"
    recipe_config_dir = recipe_project / CONFIG_DIR
    recipe_config_dir.mkdir(parents=True)
    (recipe_project / "tests").mkdir()
    (recipe_project / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    (recipe_config_dir / CONFIG_NAME).write_text(
        "version: 1\nworker: noop\nallow_noop_success: true\n",
        encoding="utf-8",
    )
    if not _run_release_step(
        f"Smoke {stem} recipe until-done",
        [
            str(harness_bin),
            "--project-dir",
            str(recipe_project),
            "fix-tests",
            "--until-done",
        ],
        cwd=tmp_root,
        required_stdout="Recipe: fix-tests",
        env=smoke_env,
    ):
        return False
    if len(list((recipe_project / CONFIG_DIR / "runs").glob("*/report.md"))) != 1:
        print(f"{stem} smoke failed: recipe --until-done did not write one report artifact")
        return False
    demo_dir = smoke_root / "demo"
    if not _run_release_step(
        f"Smoke {stem} packaged demo",
        [str(harness_bin), "run-demo", "fix-tests", str(demo_dir)],
        cwd=tmp_root,
        required_stdout="Report: .agentic-harness/runs/",
    ):
        return False
    if not (demo_dir / "requirements-dev.txt").exists():
        print(f"{stem} smoke failed: generated demo is missing requirements-dev.txt")
        return False
    transcripts = list((demo_dir / ".agentic-harness" / "runs").glob("*/shell-worker.log"))
    reports = list((demo_dir / ".agentic-harness" / "runs").glob("*/report.md"))
    if len(transcripts) != 1 or len(reports) != 1:
        print(f"{stem} smoke failed: expected one transcript and one report artifact")
        return False
    demo_python = _venv_python(demo_dir / ".venv")
    if str(demo_python) not in transcripts[0].read_text(encoding="utf-8"):
        print(f"{stem} smoke failed: demo transcript did not use nested demo venv Python")
        return False
    demo_report = reports[0].read_text(encoding="utf-8")
    if "Report: .agentic-harness/runs/" not in demo_report:
        print(f"{stem} smoke failed: report artifact did not include its path")
        return False
    if "Changed: 1 file" not in demo_report or "- modified calculator.py" not in demo_report:
        print(f"{stem} smoke failed: report artifact did not include changed-file summary")
        return False
    auto_project = smoke_root / "auto-run"
    (auto_project / "tests").mkdir(parents=True)
    (auto_project / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )
    fake_bin = smoke_root / "fake-bin"
    fake_bin.mkdir()
    if os.name == "nt":
        fake_codex = fake_bin / "codex.cmd"
        fake_codex.write_text("@echo off\necho fake codex %*\n", encoding="utf-8")
    else:
        fake_codex = fake_bin / "codex"
        fake_codex.write_text("#!/bin/sh\necho fake codex \"$@\"\n", encoding="utf-8")
        fake_codex.chmod(0o755)
    auto_env = smoke_env.copy()
    auto_env["PATH"] = str(fake_bin) + os.pathsep + auto_env.get("PATH", "")
    if not _run_release_step(
        f"Smoke {stem} run auto-config",
        [
            str(harness_bin),
            "--project-dir",
            str(auto_project),
            "run",
            "release smoke goal",
        ],
        cwd=tmp_root,
        required_stdout="Configured codex tool.",
        env=auto_env,
    ):
        return False
    auto_config = auto_project / CONFIG_DIR / CONFIG_NAME
    if "codex" not in auto_config.read_text(encoding="utf-8"):
        print(f"{stem} smoke failed: auto-config run did not write codex config")
        return False
    if len(list((auto_project / CONFIG_DIR / "runs").glob("*/report.md"))) != 1:
        print(f"{stem} smoke failed: auto-config run did not write one report artifact")
        return False
    return _run_release_step(
        f"Verify {stem} final demo tests",
        [str(demo_python), "-m", "pytest", "tests/", "-q"],
        cwd=demo_dir,
    )


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_executable(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def _run_release_step(
    label: str,
    command: list[str],
    *,
    cwd: Path,
    required_stdout: str | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    print(f"{label}: {' '.join(command)}")
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"{label} failed with exit code {proc.returncode}.")
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip())
        return False
    if required_stdout and required_stdout not in proc.stdout:
        print(f"{label} failed: expected stdout to contain {required_stdout!r}")
        if proc.stdout:
            print(proc.stdout.rstrip())
        return False
    return True


def run_demo(name: str, path: Path, *, force: bool = False, install: bool = True) -> int:
    try:
        demo_path = create_demo(name, path, force=force)
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2

    print(f"Demo path: {demo_path}")
    python = Path(sys.executable)
    env = os.environ.copy()
    harness_root = str(Path(__file__).resolve().parent.parent)
    existing_pythonpath = env.get("PYTHONPATH", "").split(os.pathsep)
    pythonpath = [harness_root]
    seen = {os.path.normcase(os.path.abspath(harness_root))}
    for entry in existing_pythonpath:
        if not entry:
            continue
        normalized = os.path.normcase(os.path.abspath(entry))
        if normalized not in seen:
            pythonpath.append(entry)
            seen.add(normalized)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    if install:
        venv_dir = demo_path / ".venv"
        if not _run_demo_step(
            "Create demo virtual environment",
            [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
            cwd=demo_path,
            env=env,
        ):
            return 1
        python = _venv_python(venv_dir)
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
    if install:
        # The harness import root may point at the parent environment's
        # site-packages for an installed artifact. Do not expose that path to
        # pip: it can make a dependency appear satisfied even though the
        # nested demo interpreter cannot import it during isolated review.
        dependency_env = env.copy()
        dependency_env.pop("PYTHONPATH", None)
        if not _run_demo_step(
            "Install demo dependencies",
            [str(python), "-m", "pip", "install", "-r", "requirements-dev.txt"],
            cwd=demo_path,
            env=dependency_env,
        ):
            return 1
    if not _run_demo_step(
        "Reset demo",
        [str(python), "reset_demo.py"],
        cwd=demo_path,
        env=env,
    ):
        return 1
    if not _run_demo_step(
        "Confirm starting tests fail",
        [str(python), "-m", "pytest", "tests/", "-q"],
        cwd=demo_path,
        env=env,
        expect_failure=True,
    ):
        return 1
    if not _run_demo_step(
        "Run fix-tests recipe",
        [str(python), "-m", "agentic_harness.cli", "fix-tests", "--until-done"],
        cwd=demo_path,
        env=env,
        echo_output=True,
    ):
        return 1
    if not _run_demo_step(
        "Show status",
        [str(python), "-m", "agentic_harness.cli", "status"],
        cwd=demo_path,
        env=env,
        echo_output=True,
    ):
        return 1
    if not _run_demo_step(
        "Confirm final tests pass",
        [str(python), "-m", "pytest", "tests/", "-q"],
        cwd=demo_path,
        env=env,
    ):
        return 1
    print(f"Demo complete: {demo_path}")
    return 0


def _run_demo_step(
    label: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expect_failure: bool = False,
    echo_output: bool = False,
) -> bool:
    print(f"{label}: {' '.join(command)}")
    proc = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if expect_failure:
        if proc.returncode == 0:
            print("Expected this command to fail, but it passed.")
            if proc.stdout:
                print(proc.stdout.rstrip())
            return False
        print("Expected failure observed.")
        return True
    if proc.returncode != 0:
        print(f"{label} failed with exit code {proc.returncode}.")
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(proc.stderr.rstrip())
        return False
    if echo_output and proc.stdout:
        print(proc.stdout.rstrip())
    return True


def write_goal_report(
    supervisor: Supervisor,
    project_dir: Path,
    goal: Goal,
) -> tuple[Goal, str]:
    changes = goal_workspace_changes(project_dir, goal)
    frozen = goal.metadata.get("terminal_workspace_changes")
    if goal.status.is_terminal and isinstance(changes, dict) and not isinstance(frozen, dict):
        with supervisor.store.autonomy_locked():
            with supervisor.store.locked():
                current = supervisor.store.read_current_goal()
                if current is None or current.id != goal.id:
                    raise HarnessError("active goal changed before terminal evidence was frozen")
                current_frozen = current.metadata.get("terminal_workspace_changes")
                if isinstance(current_frozen, dict):
                    changes = current_frozen
                else:
                    current.metadata["terminal_workspace_changes"] = changes
                supervisor.store.write_goal(current)
                goal = current
    reported_goal, report_path = supervisor.write_report(
        format_report_markdown(goal, workspace_changes=changes)
    )
    report_rel = project_relative_path(project_dir, report_path)
    changes = goal_workspace_changes(project_dir, reported_goal)
    reported_goal, _ = supervisor.write_report(
        format_report_markdown(reported_goal, report_path=report_rel, workspace_changes=changes)
    )
    return reported_goal, report_rel


def goal_workspace_changes(project_dir: Path, goal: Goal) -> dict[str, object] | None:
    frozen = goal.metadata.get("terminal_workspace_changes")
    if goal.status.is_terminal and isinstance(frozen, dict):
        return frozen
    return workspace_change_summary(project_dir, goal.metadata.get("workspace_snapshot"))


def recipe_review_command(project_dir: Path, command: list[str]) -> list[str]:
    if command and command[0] == "python" and (project_dir / "mock_coding_agent.py").exists():
        return [sys.executable, *command[1:]]
    return list(command)


def resolve_easy_agent(agent: str) -> str | None:
    if agent == "auto":
        return preferred_agent_tool()
    if agent == "shell":
        return "shell"
    return agent if shutil.which(agent) is not None else None


def format_recipe_result_text(
    recipe: Recipe,
    goal: Goal,
    *,
    report_path: str | None = None,
    workspace_changes: dict[str, object] | None = None,
) -> str:
    receipt = build_run_receipt(goal)
    lines = [
        f"Recipe: {safe_inline_text(recipe.name)}",
        f"Result: {receipt.label}",
        f"Goal: {goal.id}",
        f"Status: {receipt.label.lower()}",
    ]
    if receipt.worker_claim:
        lines.append(f"{receipt.worker_claim_label}: {receipt.worker_claim}")
    lines.extend(
        [
            f"Reason: {receipt.trusted_reason}",
            f"Attempts: {receipt.attempts}",
            f"Retries: {receipt.retries}",
        ]
    )
    if report_path:
        lines.append(f"Report: {safe_inline_text(report_path)}")
    lines.extend(format_workspace_change_lines(workspace_changes))
    if goal.review:
        passed = "passed" if goal.review.get("passed") is True else "failed"
        lines.append(f"Review: {passed}")
    if receipt.verification_commands:
        lines.append("Verification commands:")
        lines.extend(f"- {command}" for command in receipt.verification_commands)
    if receipt.review_attempts:
        lines.append("Verification attempts:")
        for attempt in receipt.review_attempts:
            result = "passed" if attempt.passed else "failed"
            lines.append(f"- Attempt {attempt.number}: {result} — {attempt.summary}")
            for check in attempt.checks:
                scope = "independent" if check.independent else "worker-reported"
                check_result = "passed" if check.passed else "failed"
                detail = check.message or check.name
                lines.append(f"  - {scope}: {check_result} — {detail}")
    if goal.error:
        lines.append(f"Error: {safe_inline_text(goal.error)}")
    if goal.status is GoalStatus.FAILED:
        lines.append("Next: check the error above, then run agentic-harness report")
        if "no worker configured" in (goal.error or ""):
            lines.append("Tip: run agentic-harness init-agent codex before using coding recipes.")
    if goal.artifacts:
        lines.append("Artifacts:")
        lines.extend(f"- {safe_inline_text(artifact)}" for artifact in goal.artifacts)
    return "\n".join(lines)


def _review_command_payload(goal: Goal) -> dict[str, object]:
    """Return the terminal-safe result of an explicit review command."""
    payload = public_goal_payload(goal)
    return {
        "id": payload["id"],
        "status": payload["status"],
        "review": payload["review"],
    }


def format_report_text(
    goal: Goal | None,
    *,
    report_path: str | None = None,
    workspace_changes: dict[str, object] | None = None,
) -> str:
    if goal is None:
        return "\n".join(
            [
                "No active run.",
                "Next: agentic-harness quickstart",
            ]
        )
    receipt = build_run_receipt(goal)
    lines = [
        f"Result: {receipt.label}",
        f"Goal: {goal.id}",
        f"Objective: {safe_inline_text(goal.objective)}",
        f"Status: {receipt.label.lower()}",
    ]
    if receipt.worker_claim:
        lines.append(f"{receipt.worker_claim_label}: {receipt.worker_claim}")
    lines.append(f"Reason: {receipt.trusted_reason}")
    lines.extend(
        [
            f"Attempts: {receipt.attempts}",
            f"Retries: {receipt.retries}",
        ]
    )
    autonomy = goal.metadata.get("autonomy")
    if isinstance(autonomy, dict):
        checkpoint = str(autonomy.get("checkpoint") or "").strip()
        if checkpoint:
            lines.append(f"Checkpoint: {safe_inline_text(checkpoint)}")
    if report_path:
        lines.append(f"Report: {safe_inline_text(report_path)}")
    lines.extend(format_workspace_change_lines(workspace_changes))
    duration = goal.duration_seconds
    if duration is not None:
        if duration < 60:
            lines.append(f"Duration: {duration:.0f}s")
        elif duration < 3600:
            lines.append(f"Duration: {duration / 60:.1f}m")
        else:
            lines.append(f"Duration: {duration / 3600:.1f}h")
    worker_success = goal.metadata.get("worker_success")
    if worker_success is not None:
        lines.append(f"Worker: {'passed' if worker_success else 'failed'}")
    if goal.review:
        lines.append(f"Review: {'passed' if goal.review.get('passed') is True else 'failed'}")
    if receipt.verification_commands:
        lines.append("Verification commands:")
        lines.extend(f"- {command}" for command in receipt.verification_commands)
    if receipt.review_attempts:
        lines.append("Verification attempts:")
        for attempt in receipt.review_attempts:
            result = "passed" if attempt.passed else "failed"
            lines.append(f"- Attempt {attempt.number}: {result} — {attempt.summary}")
            for check in attempt.checks:
                scope = "independent" if check.independent else "worker-reported"
                check_result = "passed" if check.passed else "failed"
                detail = check.message or check.name
                lines.append(f"  - {scope}: {check_result} — {detail}")
    if goal.error:
        lines.append(f"Error: {safe_inline_text(goal.error)}")
    if goal.artifacts:
        lines.append("Artifacts:")
        lines.extend(f"- {safe_inline_text(artifact)}" for artifact in goal.artifacts)
    return "\n".join(lines)


def format_report_markdown(
    goal: Goal,
    *,
    report_path: str | None = None,
    workspace_changes: dict[str, object] | None = None,
) -> str:
    return (
        "# Agentic Harness Report\n\n"
        + format_report_text(goal, report_path=report_path, workspace_changes=workspace_changes)
        + "\n"
    )


def project_relative_path(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def format_next_text(project_dir: Path) -> str:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    if not config_path.exists():
        selected = preferred_agent_tool()
        if selected:
            return "\n".join(
                [
                    "State: not set up",
                    f"Next: agentic-harness fix-tests  # auto-creates config for {selected}",
                    "Preview: agentic-harness run-recipe fix-tests --explain",
                ]
            )
        return "\n".join(
            [
                "State: not set up",
                "Next: agentic-harness quickstart",
                "Demo: agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
            ]
        )
    try:
        supervisor = build_supervisor(project_dir)
    except ConfigError as exc:
        return "\n".join(
            [
                "State: config needs attention",
                f"Problem: {exc}",
                "Next: edit .agentic-harness/config.yml or run agentic-harness init-agent codex --force",
            ]
        )
    goal = supervisor.status()
    if goal is None:
        return "\n".join(
            [
                "State: ready",
                "Next: agentic-harness fix-tests",
                "Other recipes: agentic-harness recipes",
            ]
        )
    if goal.status is GoalStatus.PLANNING:
        return "\n".join(
            [
                f"State: goal {goal.id} is planned",
                "Next: agentic-harness continue",
            ]
        )
    if goal.status is GoalStatus.IN_PROGRESS:
        return "\n".join(
            [
                f"State: goal {goal.id} is in progress",
                "Next: agentic-harness continue",
            ]
        )
    if goal.status is GoalStatus.REVIEW:
        return "\n".join(
            [
                f"State: goal {goal.id} is waiting for review",
                "Next: agentic-harness review",
            ]
        )
    if goal.status is GoalStatus.DONE:
        receipt = build_run_receipt(goal)
        state = (
            f"State: goal {goal.id} is done — {receipt.label}"
            if receipt.category == "verified_done"
            else f"State: goal {goal.id} — {receipt.label}"
        )
        return "\n".join(
            [
                state,
                f"Reason: {receipt.trusted_reason}",
                "Next: agentic-harness report",
                "Run another: agentic-harness fix-tests",
            ]
        )
    receipt = build_run_receipt(goal)
    return "\n".join(
        [
            f"State: goal {goal.id} — {receipt.label}",
            f"Reason: {receipt.trusted_reason}",
            "Next: agentic-harness report",
            "Retry: agentic-harness restart",
            "Or start new: agentic-harness fix-tests",
        ]
    )


def format_start_here_text() -> str:
    return "\n".join(
        [
            "Agentic Harness beginner guide",
            "",
            "1. Open the local browser flow:",
            "   agentic-harness gui",
            "",
            "2. Or run one terminal goal with an independent check:",
            '   agentic-harness do "fix the failing tests" --check "python -m pytest -q"',
            "",
            "3. Inspect progress and the durable evidence report:",
            "   agentic-harness check",
            "   agentic-harness report",
            "",
            "4. Check the install:",
            "   agentic-harness selftest",
            "",
            "5. Run the packaged demo end to end:",
            "   agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
            "",
            "6. See detected setup and recipe shortcuts:",
            "   agentic-harness quickstart",
            "   agentic-harness fix-tests",
            "   # Creates config automatically if a supported backend is available.",
            "",
            "Advanced:",
            '   agentic-harness goal "complete one verified outcome" --check "python -m pytest -q"',
            "   agentic-harness status",
            "   agentic-harness run-recipe fix-tests --explain",
            "   agentic-harness recipes",
            "",
            "No prompt design or YAML editing is required for the first verified run.",
        ]
    )


def format_portable_goal_flow() -> str:
    return "\n".join(
        [
            "Agentic Harness verified goal flow",
            "",
            "1. Describe one complete outcome.",
            "2. The configured agent plans and acts inside the workspace.",
            "3. Checkpoints, changed files, and checks remain visible.",
            "4. Done is accepted only after independent verification passes.",
            "",
            'CLI: agentic-harness goal "describe one verified outcome"',
            "GUI: agentic-harness gui",
            "Optional external orchestration is available with --backend local-goal.",
        ]
    )


def format_portable_setup(project_dir: Path) -> str:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    lines = [
        "Agentic Harness setup",
        "",
        f"Workspace: {project_dir.resolve()}",
        f"Config: {config_path}",
    ]
    if not config_path.exists():
        lines.extend(
            [
                "State: setup required",
                "Next: agentic-harness gui",
                "Or choose a CLI agent: agentic-harness init-agent codex",
            ]
        )
        return "\n".join(lines)
    try:
        config = load_config(project_dir)
    except ConfigError as exc:
        lines.extend(["State: config needs attention", f"Problem: {exc}"])
        return "\n".join(lines)
    lines.extend(
        [
            "State: configured",
            f"Execution: {config.worker}",
            f"Verification: {' '.join(config.review_command) or 'not configured'}",
            "Next: agentic-harness do \"describe one verified outcome\"",
        ]
    )
    if config.worker == "model_agent":
        lines.extend(
            [
                f"Model: {config.llm_model}",
                f"Endpoint: {config.llm_endpoint}",
                f"Credential source: {config.llm_credential_source}",
            ]
        )
    return "\n".join(lines)


def available_agent_tools() -> dict[str, bool]:
    return {tool: shutil.which(tool) is not None for tool in sorted(TOOL_CONFIGS)}


def preferred_agent_tool(tools: dict[str, bool] | None = None) -> str | None:
    current = tools if tools is not None else available_agent_tools()
    for tool in ("codex", "codewhale", "opencode", "aider"):
        if current.get(tool):
            return tool
    return None


def format_agents_text() -> str:
    tools = available_agent_tools()
    lines = ["Supported backends:"]
    for tool, found in tools.items():
        status = "found" if found else "not found"
        lines.append(f"- {tool}: {status}")
    selected = preferred_agent_tool(tools)
    if selected:
        lines.append(f"Recommended setup: agentic-harness init-agent {selected}")
        lines.append("Next: agentic-harness fix-tests")
    else:
        lines.append("Recommended setup: install Codex, CodeWhale, OpenCode, or Aider first.")
        lines.append("Script-only setup: agentic-harness init-agent shell")
    return "\n".join(lines)


def format_quickstart_text() -> str:
    selected = preferred_agent_tool()
    if selected is None:
        return "\n".join(
            [
                "No coding-agent backend found on PATH.",
                "Install Codex, CodeWhale, OpenCode, or Aider for coding-agent workflows.",
                "",
                "Run the packaged shell demo end to end:",
                "  agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
                "",
                "Or just generate the files:",
                "  agentic-harness create-demo fix-tests /tmp/agentic-harness-demo --force",
                "  cd /tmp/agentic-harness-demo",
                "  python -m pip install -r requirements-dev.txt",
                "  python -m pytest tests/ -q  # expected to fail",
                "  agentic-harness fix-tests  # auto-creates demo config",
                "  agentic-harness status",
                "  agentic-harness report",
                "  python -m pytest tests/ -q  # should pass",
            ]
        )
    return "\n".join(
        [
            f"Shortest path with {selected}:",
            "  agentic-harness fix-tests",
            "  agentic-harness status",
            "  agentic-harness report",
            "",
            f"`fix-tests` creates .agentic-harness/config.yml for {selected} if needed.",
            "",
            "Packaged shell demo:",
            "  agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
            "",
            "Preview first:",
            "  agentic-harness run-recipe fix-tests --explain",
        ]
    )


def format_create_demo_text(demo: str, path: Path) -> str:
    return "\n".join(
        [
            f"Created demo: {path}",
            "",
            "Next:",
            f"  cd {path}",
            "  python -m pip install -r requirements-dev.txt",
            "  python -m pytest tests/ -q  # expected to fail",
            "  agentic-harness fix-tests",
            "  agentic-harness status",
            "  agentic-harness report",
            "  python -m pytest tests/ -q  # should pass",
            "",
            f"Run all steps: agentic-harness run-demo {demo} {path} --force",
            "",
            f"Demo: {demo}",
        ]
    )


def format_easy_explain_text(project_dir: Path, recipe: Recipe, agent: str) -> str:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    lines = [
        f"Easy run: {recipe.name}",
        "This will:",
    ]
    if config_path.exists():
        lines.append(f"1. Use existing config: {config_path}")
    else:
        selected = resolve_easy_agent(agent)
        if selected is None:
            lines.append("1. Stop because no supported coding-agent backend was found.")
            lines.append("Next: install Codex, CodeWhale, OpenCode, or Aider, then run again.")
            return "\n".join(lines)
        lines.append(f"1. Create .agentic-harness/config.yml for {selected}.")
    lines.extend(
        [
            f"2. Ask the worker to run recipe: {recipe.name}",
            f"3. Run review: {' '.join(recipe.review_command)}",
            "4. Mark done only if review passes.",
        ]
    )
    return "\n".join(lines)


def format_no_agent_text() -> str:
    return "\n".join(
        [
            "No supported coding-agent backend found on PATH.",
            "Install one of: Codex, CodeWhale, OpenCode, Aider.",
            "Then run:",
            "  agentic-harness init-agent codex",
            "  agentic-harness fix-tests",
            "",
            "For script-only workflows:",
            "  agentic-harness init-agent shell",
            "  agentic-harness fix-tests",
        ]
    )


def public_goal_payload(goal: Goal) -> dict[str, Any]:
    def redact_value(value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, list):
            return [redact_value(item) for item in value]
        if isinstance(value, dict):
            return {
                redact_secrets(str(key)): redact_value(item)
                for key, item in value.items()
            }
        return value

    return {
        key: redact_value(value)
        for key, value in goal.to_dict().items()
    }


def status_json_payload(goal: Goal | None) -> dict[str, object]:
    if goal is None:
        return {"active": False}
    receipt = build_run_receipt(goal)
    payload: dict[str, object] = public_goal_payload(goal)
    payload["result_category"] = receipt.category
    payload["result_label"] = receipt.label
    if goal.status is GoalStatus.DONE and receipt.category != "verified_done":
        payload["status"] = GoalStatus.FAILED.value
    return payload


def format_status_text(goal: Goal | None) -> str:
    if goal is None:
        return "No active goal."
    return format_report_text(goal)


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
    # Check state lock
    lock_path = state_dir / "state.lock"
    lock_exists = lock_path.exists()
    lock_message = str(lock_path) if lock_exists else "no lock file"
    # Check active goal
    active_goal_status = None
    active_goal_id = None
    try:
        from agentic_harness.core.supervisor import Supervisor

        sup = Supervisor(project_dir=root)
        goal = sup.status()
        if goal is not None:
            active_goal_status = goal.status.value
            active_goal_id = goal.id
    except Exception:
        active_goal_status = None
        active_goal_id = None
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
        {
            "name": "state_lock",
            "ok": not lock_exists,
            "message": lock_message,
        },
        {
            "name": "active_goal",
            "ok": active_goal_status is None or active_goal_status in ("done", "failed"),
            "message": (
                "no active goal"
                if active_goal_status is None
                else f"{active_goal_id}: {active_goal_status}"
            ),
        },
    ]
    return {"ok": all(bool(check["ok"]) for check in checks), "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
