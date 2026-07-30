"""Bridge to the local Node1/Hermes goal harness.

This module intentionally keeps the public CLI small while delegating execution
to the existing local-goal runtime when it is installed on the machine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess


DEFAULT_DOC_ROOT = Path("/mnt/raid0/documentation")
DEFAULT_LOCAL_GOAL = DEFAULT_DOC_ROOT / "scripts/local-goal"


@dataclass(frozen=True)
class Mode3AGoalOptions:
    objective: str
    allowed_paths: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    guardrails: tuple[str, ...] = ()


@dataclass(frozen=True)
class HumanMode:
    key: str
    number: int
    title: str
    best_for: str
    caution: str


HUMAN_MODES: tuple[HumanMode, ...] = (
    HumanMode(
        key="local",
        number=1,
        title="Local steady worker",
        best_for="normal bounded work on this Pop-OS machine",
        caution="uses the local lane, so only run one long job at a time",
    ),
    HumanMode(
        key="guided",
        number=2,
        title="GLM-guided local worker",
        best_for="harder goals where GLM should shape the plan and local tools do the edits",
        caution="still needs local review/acceptance before work is called done",
    ),
    HumanMode(
        key="cloud",
        number=3,
        title="Cloud GLM worker",
        best_for="Codex /goal-like long work when you want GLM to carry the task",
        caution="bounded cloud lane; not unrestricted background automation",
    ),
    HumanMode(
        key="experimental",
        number=4,
        title="Experimental direct GLM canary",
        best_for="tiny sandbox/canary tasks to test direct GLM implementation",
        caution="not the default for broad source edits",
    ),
)


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class LocalGoalBridge:
    doc_root: Path = DEFAULT_DOC_ROOT
    local_goal: Path | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def __post_init__(self) -> None:
        if self.local_goal is None:
            configured = os.environ.get("AGENTIC_HARNESS_LOCAL_GOAL")
            self.local_goal = Path(configured) if configured else self.doc_root / "scripts/local-goal"

    def available(self) -> bool:
        assert self.local_goal is not None
        return self.local_goal.exists() and os.access(self.local_goal, os.X_OK)

    def run(self, args: Sequence[str]) -> CommandResult:
        assert self.local_goal is not None
        command = [str(self.local_goal), *args]
        completed = self.runner(
            command,
            cwd=str(self.doc_root),
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(
            args=tuple(command),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def enqueue_mode3a(self, options: Mode3AGoalOptions) -> CommandResult:
        goal = build_mode3a_goal(options)
        return self.enqueue_cloud_goal(goal, worker="opencode-glm-build", planner="glm-5.2")

    def start_local_goal(self, goal: str) -> CommandResult:
        return self.run(["quick-start", "--executor", "opencode", "--goal", goal])

    def start_guided_goal(self, goal: str) -> CommandResult:
        return self.run(["premium-start", "--planner", "glm-5.2", "--executor", "opencode", "--goal", goal])

    def enqueue_cloud_goal(
        self,
        goal: str,
        *,
        worker: str,
        planner: str = "glm-5.2",
    ) -> CommandResult:
        return self.run(
            [
                "enqueue",
                "--planner",
                planner,
                "--executor",
                "opencode",
                "--executor-worker",
                worker,
                "--goal",
                goal,
            ]
        )

    def status(self, *, json_output: bool = False) -> CommandResult:
        return self.run(["status", "--json"] if json_output else ["status"])

    def mode3a_status(self, *, json_output: bool = False) -> CommandResult:
        return self.run(["mode3a-status", "--json"] if json_output else ["mode3a-status"])

    def monitor(self, *, json_output: bool = False) -> CommandResult:
        args = [
            "monitor",
            "--auto-accept",
            "--auto-continue",
            "--auto-dispatch",
            "--auto-commit-owned",
        ]
        if json_output:
            args.append("--json")
        return self.run(args)


def build_mode3a_goal(options: Mode3AGoalOptions) -> str:
    objective = options.objective.strip()
    if not objective:
        raise ValueError("objective must not be empty")

    allowed_paths = options.allowed_paths or (
        "Derive the narrowest safe local files from the objective before editing.",
    )
    verification = options.verification or (
        "Run the narrowest relevant tests, syntax checks, or live checks for the changed files.",
        "Record every verification command and result in the run evidence.",
    )
    guardrails = options.guardrails or (
        "Do not expose or modify secrets, credentials, tokens, private keys, or provider dashboards.",
        "Do not run destructive cleanup, broad formatting, service restarts, DNS, billing, or routing changes.",
        "Do not overwrite unrelated dirty work; if ownership is unclear, stop that part and report it.",
        "Do not claim report-only work as installed capability.",
    )

    return "\n".join(
        [
            "Mode 3A: Cloud Long-Horizon Goal",
            "",
            "Use the GLM-backed cloud executor lane as a Codex /goal-style worker.",
            "",
            "Planner: glm-5.2",
            "Executor: opencode",
            "Executor worker: opencode-glm-build",
            "Boundary: bounded cloud goal, reviewable artifacts, deterministic review/acceptance gates.",
            "",
            "Goal:",
            objective,
            "",
            "Allowed files or areas:",
            *[f"- {path}" for path in allowed_paths],
            "",
            "Done when:",
            "- The requested task is implemented or an honest blocked report explains exactly why it cannot be.",
            "- Changed files are listed.",
            "- Verification commands and results are recorded.",
            "- The local supervisor can review and accept the result.",
            "",
            "Verification:",
            *[f"- {command}" for command in verification],
            "",
            "Guardrails:",
            *[f"- {guardrail}" for guardrail in guardrails],
        ]
    )


def human_mode_by_key(value: str) -> HumanMode:
    normalized = value.strip().lower()
    for mode in HUMAN_MODES:
        if normalized in {mode.key, str(mode.number)}:
            return mode
    valid = ", ".join(f"{mode.number}:{mode.key}" for mode in HUMAN_MODES)
    raise ValueError(f"unknown mode {value!r}; choose one of {valid}")


def format_human_modes() -> str:
    lines = ["Agentic Harness modes", ""]
    for mode in HUMAN_MODES:
        lines.append(f"{mode.number}. {mode.title}")
        lines.append(f"   Best for: {mode.best_for}")
        lines.append(f"   Note: {mode.caution}")
    lines.extend(
        [
            "",
            "Beginner default: 2 for important local work, 3 when you want GLM/cloud to carry it.",
            'Interactive: agentic-harness work',
            'One line: agentic-harness do --mode cloud "make Jarvis voice startup more reliable"',
        ]
    )
    return "\n".join(lines)


def format_command_result(result: CommandResult) -> str:
    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(result.stderr.rstrip())
    if not parts:
        parts.append(f"command exited {result.returncode}")
    return "\n".join(parts)


def format_popos_setup(bridge: LocalGoalBridge) -> str:
    lines = [
        "Agentic Harness Pop-OS setup",
        "",
        "Install or update the CLI from this checkout:",
        "  cd /mnt/raid0/home-ai-inference/agentic-harness",
        '  python3 -m pip install -e ".[test]"',
        "",
        "Run a local smoke test:",
        "  agentic-harness selftest",
        "",
        "Run a GLM long-horizon task:",
        '  agentic-harness mode3a-run "fix one small verified issue"',
        "",
        "Useful commands:",
        "  agentic-harness mode3a-status",
        "  agentic-harness mode3a-monitor",
        "  agentic-harness doctor",
        "",
        f"Detected local-goal: {bridge.local_goal}",
        f"Detected local-goal usable: {bridge.available()}",
    ]
    return "\n".join(lines)
