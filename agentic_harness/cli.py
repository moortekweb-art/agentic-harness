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

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.adapters.tmux import TmuxWorker
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
from agentic_harness.core.errors import ConfigError, HarnessError
from agentic_harness.core.local_goal_bridge import (
    CommandResult,
    DOC_ROOT_ENV,
    HUMAN_MODES,
    LocalGoalBridge,
    Mode3AGoalOptions,
    format_command_result,
    format_human_modes,
    format_popos_setup,
    human_mode_by_key,
    resolve_doc_root,
)
from agentic_harness.gui.server import run_server_from_args
from agentic_harness.core.recipes import Recipe, explain_recipe, list_recipes, load_recipe
from agentic_harness.core.review import (
    DeterministicReviewer,
    ReviewCriterion,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
)
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.worker import Worker
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
    "Path to optional local-goal/Mode 3A backend checkout root. Explicit value wins; "
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
    easy_do = sub.add_parser("do", help="Start useful background work from plain English")
    easy_do.add_argument("objective")
    easy_do.add_argument(
        "--mode",
        choices=[mode.key for mode in HUMAN_MODES],
        default="cloud",
        help="Human mode to use. Default: cloud.",
    )
    easy_do.add_argument("--safe-area", action="append", default=[])
    easy_do.add_argument("--check", action="append", default=[])
    easy_do.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    easy_do.add_argument(
        "--watch",
        action="store_true",
        help="Run one immediate diagnostic monitor pass after starting.",
    )
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
    gui.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    sub.add_parser("modes", help="Explain the four human work modes")
    easy_check = sub.add_parser("check", help="Show what the background worker is doing")
    easy_check.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
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
        help="Run a plain-English task through the GLM-backed Mode 3A cloud lane",
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
    mode3a_status = sub.add_parser("mode3a-status", help="Show Mode 3A/local-goal status")
    mode3a_status.add_argument("--doc-root", default=None, help=DOC_ROOT_HELP)
    mode3a_status.add_argument("--json", action="store_true")
    mode3a_monitor = sub.add_parser("mode3a-monitor", help="Run one Mode 3A/local-goal monitor pass")
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
    goal_cmd.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    status = sub.add_parser("status", help="Show current goal state")
    status.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format. Default: text.",
    )
    sub.add_parser("continue", help="Advance the active goal")
    sub.add_parser("review", help="Run deterministic review")
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
        print(format_popos_setup(LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))))
        return 0
    if args.command == "modes":
        print(format_human_modes())
        return 0
    if args.command == "work":
        return run_interactive_work_command(args)
    if args.command == "gui":
        return run_server_from_args(args)
    if args.command == "do":
        return run_easy_do_command(args)
    if args.command == "check":
        return run_easy_check_command(args)
    if args.command == "watch":
        return run_easy_watch_command(args)
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
            goal = supervisor.start(args.objective)
            print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            goal = supervisor.start(args.objective)
            goal = supervisor.continue_goal()
            if goal.status is GoalStatus.REVIEW:
                goal = supervisor.review()
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = workspace_change_summary(
                    project_dir,
                    goal.metadata.get("workspace_snapshot"),
                )
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return 0 if goal.status is GoalStatus.DONE else 1
        if args.command == "run-until-done":
            goal = run_until_done(
                supervisor,
                objective=args.objective,
                max_attempts=args.max_attempts,
            )
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = workspace_change_summary(
                    project_dir,
                    goal.metadata.get("workspace_snapshot"),
                )
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return 0 if goal.status is GoalStatus.DONE else 1
        if args.command == "goal":
            try:
                policy = AutonomyPolicy(
                    repeated_blocker_limit=args.blocker_limit,
                    require_completion_claim=True,
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            goal = AutonomousRunner(supervisor, policy=policy).run(args.objective)
            goal, report_path = write_goal_report(supervisor, project_dir, goal)
            if args.json:
                print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            else:
                if initialized_config is not None:
                    path, tool = initialized_config
                    print(format_init_text(path, tool))
                changes = workspace_change_summary(
                    project_dir,
                    goal.metadata.get("workspace_snapshot"),
                )
                print(format_report_text(goal, report_path=report_path, workspace_changes=changes))
            return 0 if goal.status is GoalStatus.DONE else 1
        if args.command == "status":
            active_goal = supervisor.status()
            if args.format == "text":
                print(format_status_text(active_goal))
            else:
                print(
                    json.dumps(
                        active_goal.to_dict() if active_goal else {"active": False},
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
                    workspace_changes=workspace_change_summary(
                        project_dir,
                        reported_goal.metadata.get("workspace_snapshot"),
                    ),
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
        if args.command == "restart":
            goal = supervisor.restart()
            print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "accept":
            goal = supervisor.accept(reason=args.reason)
            print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
            return 0
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
        print("Mode 3A task was not queued because unattended supervision is unavailable.")
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
        print("Mode 3A task queued.")
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


def run_easy_do_command(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print("I cannot find the background worker on this machine.")
        print(f"Expected: {bridge.local_goal}")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    supervision = bridge.background_supervision()
    if supervision.get("active") is not True:
        print("I did not start the task because unattended supervision is unavailable.")
        print(str(supervision.get("summary") or "Background watcher could not be verified."))
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    try:
        result = start_human_mode(
            bridge,
            mode_key=args.mode,
            objective=args.objective,
            safe_areas=tuple(args.safe_area),
            checks=tuple(args.check),
        )
    except ValueError as exc:
        print(f"Could not start work: {exc}")
        return 2
    if result.returncode != 0:
        print("The background worker refused the task.")
        print(format_command_result(result))
        return result.returncode
    print("Started background work.")
    print(_friendly_queue_summary(result.stdout))
    print("Background supervisor owns this task through completion or a true blocker.")
    print("You can close this. Status is available any time with agentic-harness check.")
    if args.watch:
        watch_result = bridge.monitor()
        print(format_command_result(watch_result))
        return watch_result.returncode
    return 0


def run_interactive_work_command(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print("I cannot find the background worker on this machine.")
        print(f"Expected: {bridge.local_goal}")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    supervision = bridge.background_supervision()
    if supervision.get("active") is not True:
        print("I did not start work because unattended supervision is unavailable.")
        print(str(supervision.get("summary") or "Background watcher could not be verified."))
        return 2
    print(format_human_modes())
    print("")
    selected = input("Choose a mode [2]: ").strip() or "2"
    objective = input("What do you want done? ").strip()
    if not objective:
        print("No task entered. Nothing started.")
        return 2
    try:
        mode = human_mode_by_key(selected)
        result = start_human_mode(
            bridge,
            mode_key=mode.key,
            objective=objective,
            safe_areas=(),
            checks=(),
        )
    except ValueError as exc:
        print(f"Could not start work: {exc}")
        return 2
    if result.returncode != 0:
        print("The background worker refused the task.")
        print(format_command_result(result))
        return result.returncode
    print("")
    print(f"Started: {mode.title}")
    print(_friendly_queue_summary(result.stdout))
    print("Background supervisor owns this task through completion or a true blocker.")
    print("You can close this. Status is available any time with agentic-harness check.")
    return 0


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
        "install or expose the optional local-goal/Mode 3A backend, then pass "
        f"--doc-root, set {DOC_ROOT_ENV}, or set AGENTIC_HARNESS_LOCAL_GOAL; "
        "run agentic-harness setup for detected paths"
    )


def run_easy_check_command(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print("I cannot find the background worker on this machine.")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    result = bridge.status(json_output=False)
    print(format_command_result(result))
    return result.returncode


def run_easy_watch_command(args: argparse.Namespace) -> int:
    bridge = LocalGoalBridge(doc_root=resolve_doc_root(args.doc_root))
    if not bridge.available():
        print("I cannot find the background worker on this machine.")
        print(f"Next: {local_goal_setup_hint()}")
        return 2
    result = bridge.monitor(json_output=False)
    print(format_command_result(result))
    return result.returncode


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
        supervisor = build_supervisor(
            project_dir,
            review_command=recipe_review_command(project_dir, recipe.review_command),
            review_command_timeout=recipe.review_command_timeout,
        )
        if until_done:
            goal = run_until_done(
                supervisor,
                objective=recipe.objective,
                max_attempts=max_attempts,
            )
        else:
            goal = supervisor.start(recipe.objective)
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
        print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
    else:
        if initialized_config is not None:
            path, tool = initialized_config
            print(format_init_text(path, tool))
        print(
            format_recipe_result_text(
                recipe,
                goal,
                report_path=report_path,
                workspace_changes=workspace_change_summary(
                    project_dir,
                    goal.metadata.get("workspace_snapshot"),
                ),
            )
        )
    return 0 if goal.status is GoalStatus.DONE else 1


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
            "\n".join(
                [
                    "version: 1",
                    "worker: shell",
                    "shell_command:",
                    "  - python",
                    "  - -c",
                    "  - \"print('worker ok')\"",
                    "review_command:",
                    "  - python",
                    "  - -c",
                    "  - \"print('review ok')\"",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        supervisor = build_supervisor(project)
        goal = supervisor.start("selftest")
        goal = supervisor.continue_goal()
        if goal.status is GoalStatus.REVIEW:
            goal = supervisor.review()
        if goal.status is not GoalStatus.DONE:
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
        if not _run_release_step(
            "Build wheel and sdist",
            [sys.executable, "-m", "build", "--outdir", str(out_dir)],
            cwd=project_dir,
        ):
            return 1
        try:
            wheel = _single_artifact(out_dir, "*.whl")
            sdist = _single_artifact(out_dir, "*.tar.gz")
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


def _single_artifact(dist_dir: Path, pattern: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ConfigError(f"expected one {pattern} artifact in {dist_dir}, found {len(matches)}")
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
    if not _run_release_step(
        f"Install {artifact.name}",
        [str(python_bin), "-m", "pip", "install", str(artifact)],
        cwd=tmp_root,
    ):
        return False
    for version_command in ("--version", "version"):
        if not _run_release_step(
            f"Smoke {stem} version {version_command}",
            [str(harness_bin), version_command],
            cwd=tmp_root,
            required_stdout=format_version_text(),
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
        ],
        cwd=tmp_root,
        required_stdout="Report: .agentic-harness/runs/",
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
        required_stdout="Status: done",
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
        ],
        cwd=tmp_root,
        required_stdout="Report: .agentic-harness/runs/",
    ):
        return False
    if len(list((driver_project / CONFIG_DIR / "runs").glob("*/report.md"))) != 1:
        print(f"{stem} smoke failed: run-until-done did not write one report artifact")
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
    if str(python_bin) not in transcripts[0].read_text(encoding="utf-8"):
        print(f"{stem} smoke failed: demo transcript did not use venv Python")
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
    auto_env = os.environ.copy()
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
        [str(python_bin), "-m", "pytest", "tests/", "-q"],
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

    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")

    print(f"Demo path: {demo_path}")
    if install and not _run_demo_step(
        "Install demo dependencies",
        [sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"],
        cwd=demo_path,
        env=env,
    ):
        return 1
    if not _run_demo_step(
        "Reset demo",
        [sys.executable, "reset_demo.py"],
        cwd=demo_path,
        env=env,
    ):
        return 1
    if not _run_demo_step(
        "Confirm starting tests fail",
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=demo_path,
        env=env,
        expect_failure=True,
    ):
        return 1
    if not _run_demo_step(
        "Run fix-tests recipe",
        [sys.executable, "-m", "agentic_harness.cli", "fix-tests"],
        cwd=demo_path,
        env=env,
        echo_output=True,
    ):
        return 1
    if not _run_demo_step(
        "Show status",
        [sys.executable, "-m", "agentic_harness.cli", "status"],
        cwd=demo_path,
        env=env,
        echo_output=True,
    ):
        return 1
    if not _run_demo_step(
        "Confirm final tests pass",
        [sys.executable, "-m", "pytest", "tests/", "-q"],
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
    changes = workspace_change_summary(project_dir, goal.metadata.get("workspace_snapshot"))
    reported_goal, report_path = supervisor.write_report(
        format_report_markdown(goal, workspace_changes=changes)
    )
    report_rel = project_relative_path(project_dir, report_path)
    changes = workspace_change_summary(project_dir, reported_goal.metadata.get("workspace_snapshot"))
    reported_goal, _ = supervisor.write_report(
        format_report_markdown(reported_goal, report_path=report_rel, workspace_changes=changes)
    )
    return reported_goal, report_rel


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


def build_supervisor(
    project_dir: Path,
    *,
    review_command: list[str] | None = None,
    review_command_timeout: int | None = None,
) -> Supervisor:
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
            retries=config.llm_retries,
            retry_delay=config.llm_retry_delay,
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
    criteria = review_criteria_from_config(
        config,
        project_dir,
        review_command=review_command,
        review_command_timeout=review_command_timeout,
    )
    return Supervisor(
        project_dir=project_dir,
        worker=worker,
        reviewer=DeterministicReviewer(criteria) if criteria else None,
        allow_noop_success=config.allow_noop_success,
    )


def review_criteria_from_config(
    config: HarnessConfig,
    project_dir: Path,
    *,
    review_command: list[str] | None = None,
    review_command_timeout: int | None = None,
) -> list[ReviewCriterion]:
    criteria: list[ReviewCriterion] = []
    command = review_command if review_command is not None else config.review_command
    timeout = (
        review_command_timeout
        if review_command_timeout is not None
        else config.review_command_timeout
    )
    if command:
        criteria.append(
            command_passes(
                command,
                cwd=project_dir,
                timeout=timeout,
            )
        )
    if config.review_artifact:
        criteria.append(artifact_exists(project_dir, config.review_artifact))
    if config.review_file_changed:
        criteria.append(file_changed(project_dir, config.review_file_changed))
    if config.review_git_clean:
        criteria.append(git_clean(project_dir))
    return criteria


def format_recipe_result_text(
    recipe: Recipe,
    goal: Goal,
    *,
    report_path: str | None = None,
    workspace_changes: dict[str, object] | None = None,
) -> str:
    verdict = "done" if goal.status is GoalStatus.DONE else "not done"
    lines = [
        f"Recipe: {recipe.name}",
        f"Result: {verdict}",
        f"Goal: {goal.id}",
        f"Status: {goal.status.value}",
    ]
    if report_path:
        lines.append(f"Report: {report_path}")
    lines.extend(format_workspace_change_lines(workspace_changes))
    if goal.review:
        passed = "passed" if goal.review.get("passed") is True else "failed"
        lines.append(f"Review: {passed}")
    if goal.error:
        lines.append(f"Error: {goal.error}")
    if goal.status is GoalStatus.FAILED:
        lines.append("Next: check the error above, then run agentic-harness report")
        if "no worker configured" in (goal.error or ""):
            lines.append("Tip: run agentic-harness init-agent codex before using coding recipes.")
    if goal.artifacts:
        lines.append("Artifacts:")
        lines.extend(f"- {artifact}" for artifact in goal.artifacts)
    return "\n".join(lines)


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
    result = "done" if goal.status is GoalStatus.DONE else goal.status.value
    lines = [
        f"Result: {result}",
        f"Goal: {goal.id}",
        f"Objective: {goal.objective}",
        f"Status: {goal.status.value}",
    ]
    if report_path:
        lines.append(f"Report: {report_path}")
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
    if goal.error:
        lines.append(f"Error: {goal.error}")
    if goal.artifacts:
        lines.append("Artifacts:")
        lines.extend(f"- {artifact}" for artifact in goal.artifacts)
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
        return "\n".join(
            [
                f"State: goal {goal.id} is done",
                "Next: agentic-harness report",
                "Run another: agentic-harness fix-tests",
            ]
        )
    return "\n".join(
        [
            f"State: goal {goal.id} failed",
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
            "1. Check the install:",
            "   agentic-harness selftest",
            "",
            "2. Give the autonomous runner a complete plain-English objective:",
            '   agentic-harness goal "fix the failing tests and verify the result"',
            "   # It continues through progress and repair without routine prompts.",
            "",
            "3. Run the packaged demo end to end:",
            "   agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
            "",
            "4. See what to do in this project:",
            "   agentic-harness quickstart",
            "",
            "5. Set up a backend and run a useful recipe:",
            "   agentic-harness fix-tests",
            "   # Creates config automatically if a supported backend is available.",
            "",
            "6. Read the result:",
            "   agentic-harness status",
            "   agentic-harness report",
            "",
            "Useful previews:",
            "   agentic-harness run-recipe fix-tests --explain",
            "   agentic-harness recipes",
            "",
            "No prompt design or YAML editing. For long work, use goal; for the Pop-OS background lane, use do and check.",
        ]
    )


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


def format_status_text(goal: Goal | None) -> str:
    if goal is None:
        return "No active goal."
    worker_success = goal.metadata.get("worker_success")
    worker = "not run"
    if worker_success is True:
        worker = "success"
    elif worker_success is False:
        worker = "failed"
    review = "not run"
    if isinstance(goal.review, dict):
        review = "passed" if goal.review.get("passed") is True else "failed"
    lines = [
        f"Goal: {goal.id}",
        f"Objective: {goal.objective}",
        f"Status: {goal.status.value}",
        f"Worker: {worker}",
        f"Review: {review}",
    ]
    duration = goal.duration_seconds
    if duration is not None:
        if duration < 60:
            lines.append(f"Duration: {duration:.0f}s")
        elif duration < 3600:
            lines.append(f"Duration: {duration / 60:.1f}m")
        else:
            lines.append(f"Duration: {duration / 3600:.1f}h")
    if goal.error:
        lines.append(f"Error: {goal.error}")
    if goal.artifacts:
        lines.append("Artifacts:")
        lines.extend(f"- {artifact}" for artifact in goal.artifacts)
    return "\n".join(lines)


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
