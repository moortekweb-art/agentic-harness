"""Command line interface for agentic-harness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    TOOL_CONFIGS,
    load_config,
    write_default_config,
    write_tool_config,
)
from agentic_harness.core.demos import create_demo, demo_names
from agentic_harness.core.errors import ConfigError, HarnessError
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


RECIPE_COMMANDS = {
    "changelog",
    "fix-tests",
    "lint-fix",
    "typecheck-fix",
    "update-docs",
    "verify-tests",
}


class HarnessParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        if self.prog == "agentic-harness":
            return format_start_here_text() + "\n\nAdvanced: agentic-harness <command> --help\n"
        return super().format_help()


def build_parser() -> argparse.ArgumentParser:
    parser = HarnessParser(prog="agentic-harness")
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project directory containing .agentic-harness/config.yml.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Generate .agentic-harness/config.yml")
    init.add_argument("tool", nargs="?", choices=sorted(TOOL_CONFIGS))
    init.add_argument("--force", action="store_true", help="Replace an existing config.yml.")
    init_agent = sub.add_parser("init-agent", help="Generate a coding-agent backend config")
    init_agent.add_argument("tool", choices=sorted(TOOL_CONFIGS))
    init_agent.add_argument("--force", action="store_true", help="Replace an existing config.yml.")
    sub.add_parser("quickstart", help="Print the shortest setup path for this machine")
    sub.add_parser("start-here", help="Show the beginner command guide")
    sub.add_parser("guide", help="Show the beginner command guide")
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
    for recipe_name in sorted(RECIPE_COMMANDS):
        recipe_cmd = sub.add_parser(recipe_name, help=f"Run the built-in {recipe_name} recipe")
        recipe_cmd.add_argument("--explain", action="store_true", help="Show what would run.")
        recipe_cmd.add_argument("--json", action="store_true", help="Print the final goal JSON.")
    sub.add_parser("report", help="Show a plain-language status report")
    start = sub.add_parser("start", help="Start a goal")
    start.add_argument("objective")
    run = sub.add_parser("run", help="Start, continue, and review a goal")
    run.add_argument("objective")
    status = sub.add_parser("status", help="Show current goal state")
    status.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format.",
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
    project_dir = Path(args.project_dir)
    if args.command in {"init", "init-agent"}:
        try:
            if args.command == "init-agent" or args.tool:
                path = write_tool_config(project_dir, args.tool, force=args.force)
            else:
                path = write_default_config(project_dir)
        except ConfigError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(f"created {path}")
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
        return run_recipe(project_dir, recipe, output_json=args.json)
    if args.command == "doctor":
        payload = doctor(project_dir)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
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
            print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
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


def run_recipe(project_dir: Path, recipe: Recipe, *, output_json: bool = False) -> int:
    try:
        supervisor = build_supervisor(
            project_dir,
            review_command=recipe_review_command(project_dir, recipe.review_command),
            review_command_timeout=recipe.review_command_timeout,
        )
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
        print(format_recipe_result_text(recipe, goal, report_path=report_path))
    return 0 if goal.status is GoalStatus.DONE else 1


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
        print(f"created {path}")
    return run_recipe(project_dir, recipe)


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
        print("Next: agentic-harness easy fix-tests")
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
        for artifact in (wheel, sdist):
            if not _smoke_installed_artifact(artifact, tmp_root):
                return 1
        print("Release smoke: passed")
        print(f"Wheel: {wheel}")
        print(f"sdist: {sdist}")
        return 0


def _single_artifact(dist_dir: Path, pattern: str) -> Path:
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ConfigError(f"expected one {pattern} artifact in {dist_dir}, found {len(matches)}")
    return matches[0]


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
    for command in ("lint-fix", "typecheck-fix", "update-docs", "changelog", "verify-tests"):
        if not _run_release_step(
            f"Smoke {stem} command {command}",
            [str(harness_bin), command, "--explain"],
            cwd=tmp_root,
            required_stdout=f"Recipe: {command}",
        ):
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
    if "Report: .agentic-harness/runs/" not in reports[0].read_text(encoding="utf-8"):
        print(f"{stem} smoke failed: report artifact did not include its path")
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
) -> bool:
    print(f"{label}: {' '.join(command)}")
    proc = subprocess.run(
        command,
        cwd=cwd,
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
    init_command = [
        sys.executable,
        "-m",
        "agentic_harness.cli",
        "init",
        "shell",
    ]
    if force:
        init_command.append("--force")
    if not _run_demo_step("Initialize shell worker", init_command, cwd=demo_path, env=env):
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
        [sys.executable, "-m", "agentic_harness.cli", "status", "--format", "text"],
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
    reported_goal, report_path = supervisor.write_report(format_report_markdown(goal))
    report_rel = project_relative_path(project_dir, report_path)
    reported_goal, _ = supervisor.write_report(
        format_report_markdown(reported_goal, report_path=report_rel)
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


def format_report_text(goal: Goal | None, *, report_path: str | None = None) -> str:
    if goal is None:
        return "\n".join(
            [
                "No active run.",
                "Next: agentic-harness recipes",
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


def format_report_markdown(goal: Goal, *, report_path: str | None = None) -> str:
    return "# Agentic Harness Report\n\n" + format_report_text(goal, report_path=report_path) + "\n"


def project_relative_path(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def format_next_text(project_dir: Path) -> str:
    config_path = project_dir / CONFIG_DIR / CONFIG_NAME
    if not config_path.exists():
        return "\n".join(
            [
                "State: not set up",
                "Next: agentic-harness easy fix-tests",
                "Preview: agentic-harness easy fix-tests --explain",
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
                "Next: agentic-harness easy fix-tests",
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
                "Run another: agentic-harness easy fix-tests",
            ]
        )
    return "\n".join(
        [
            f"State: goal {goal.id} failed",
            "Next: agentic-harness report",
            "Retry: agentic-harness restart",
            "Or start new: agentic-harness easy fix-tests",
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
            "2. Run the packaged demo end to end:",
            "   agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force",
            "",
            "3. See what to do in this project:",
            "   agentic-harness next",
            "",
            "4. Run the easiest useful recipe:",
            "   agentic-harness easy fix-tests",
            "",
            "5. Read the result:",
            "   agentic-harness report",
            "",
            "Useful previews:",
            "   agentic-harness easy fix-tests --explain",
            "   agentic-harness recipes",
            "",
            "Advanced commands exist, but beginners usually only need selftest, run-demo, next, easy, and report.",
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
            ]
        )
    return "\n".join(
        [
            "Shortest path:",
            "  agentic-harness easy fix-tests",
            "  agentic-harness report",
            "",
            "Manual setup:",
            f"  agentic-harness init-agent {selected}",
            "  agentic-harness run-recipe fix-tests",
            "  agentic-harness report",
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
            "  agentic-harness init shell",
            "  agentic-harness fix-tests",
            "  agentic-harness status --format text",
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
            "  agentic-harness easy fix-tests",
            "",
            "For script-only workflows:",
            "  agentic-harness init-agent shell",
            "  agentic-harness run-recipe fix-tests",
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
