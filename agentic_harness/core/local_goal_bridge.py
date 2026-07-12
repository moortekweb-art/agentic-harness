"""Bridge to an optional external goal-orchestration executable.

This module intentionally keeps the public CLI small while delegating execution
to the existing local-goal runtime when it is installed on the machine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess


DOC_ROOT_ENV = "AGENTIC_HARNESS_DOC_ROOT"
LOCAL_GOAL_ENV = "AGENTIC_HARNESS_LOCAL_GOAL"
EXTERNAL_CANDIDATE_CONTRACT = "agentic_harness.external_candidate.v1"


def resolve_doc_root(doc_root: str | Path | None = None) -> Path:
    if doc_root is not None:
        return Path(doc_root).expanduser()
    configured = os.environ.get(DOC_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.cwd()


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
        title="Use this computer",
        best_for="small, bounded work that should stay on this Linux machine",
        caution="best when the work is clear and only one task should move",
    ),
    HumanMode(
        key="guided",
        number=2,
        title="Plan, then execute",
        best_for="important work where an external planner should shape the approach",
        caution="keeps review in the loop before the work is called done",
    ),
    HumanMode(
        key="cloud",
        number=3,
        title="Use a long-running orchestrator",
        best_for="longer work owned by a configured external orchestration service",
        caution="keeps results reviewable before they are accepted",
    ),
    HumanMode(
        key="experimental",
        number=4,
        title="Try an experimental executor",
        best_for="tiny sandbox checks with a separately configured experimental path",
        caution="not the default for broad source edits or important production work",
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
    doc_root: str | Path | None = None
    local_goal: str | Path | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        resolved_doc_root = resolve_doc_root(self.doc_root)
        self.doc_root = resolved_doc_root
        if self.local_goal is None:
            configured = os.environ.get(LOCAL_GOAL_ENV, "").strip()
            self.local_goal = Path(configured).expanduser() if configured else resolved_doc_root / "scripts/local-goal"
        else:
            self.local_goal = Path(self.local_goal).expanduser()

    def available(self) -> bool:
        assert self.local_goal is not None
        local_goal = Path(self.local_goal)
        return local_goal.exists() and os.access(local_goal, os.X_OK)

    def run(self, args: Sequence[str]) -> CommandResult:
        assert self.local_goal is not None
        command = [str(self.local_goal), *args]
        try:
            completed = self.runner(
                command,
                cwd=str(self.doc_root),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=tuple(command),
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=f"background worker command timed out after {self.timeout_seconds}s",
            )
        except OSError as exc:
            return CommandResult(
                args=tuple(command),
                returncode=127,
                stdout="",
                stderr=str(exc),
            )
        return CommandResult(
            args=tuple(command),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def enqueue_mode3a(self, options: Mode3AGoalOptions) -> CommandResult:
        capabilities = self.run(["capabilities", "--json"])
        if not _supports_candidate_contract(capabilities, EXTERNAL_CANDIDATE_CONTRACT):
            return CommandResult(
                args=capabilities.args,
                returncode=2,
                stdout=capabilities.stdout,
                stderr=(
                    "The external backend does not advertise the required candidate "
                    f"contract: {EXTERNAL_CANDIDATE_CONTRACT}"
                ),
            )
        goal = build_mode3a_goal(options)
        return self.enqueue_cloud_goal(
            goal,
            worker=_external_setting("AGENTIC_HARNESS_EXTERNAL_LONG_WORKER", "long-horizon"),
            planner=_external_setting("AGENTIC_HARNESS_EXTERNAL_PLANNER", "planner"),
            contract=EXTERNAL_CANDIDATE_CONTRACT,
        )

    def start_human_goal(
        self,
        *,
        mode_key: str,
        objective: str,
        safe_areas: tuple[str, ...] = (),
        checks: tuple[str, ...] = (),
    ) -> CommandResult:
        mode = human_mode_by_key(mode_key)
        if mode.key == "local":
            return self.start_local_goal(objective)
        if mode.key == "guided":
            return self.start_guided_goal(objective)
        if mode.key == "cloud":
            return self.enqueue_mode3a(
                Mode3AGoalOptions(
                    objective=objective,
                    allowed_paths=safe_areas,
                    verification=checks,
                )
            )
        if mode.key == "experimental":
            goal = build_experimental_goal(objective, safe_areas=safe_areas, checks=checks)
            return self.enqueue_cloud_goal(
                goal,
                worker=_external_setting(
                    "AGENTIC_HARNESS_EXTERNAL_EXPERIMENTAL_WORKER",
                    "experimental",
                ),
                planner="none",
            )
        raise ValueError(f"unsupported mode {mode.key}")

    def start_local_goal(self, goal: str) -> CommandResult:
        return self.run(
            [
                "quick-start",
                "--executor",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", "executor"),
                "--goal",
                goal,
            ]
        )

    def start_guided_goal(self, goal: str) -> CommandResult:
        return self.run(
            [
                "premium-start",
                "--planner",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_PLANNER", "planner"),
                "--executor",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", "executor"),
                "--goal",
                goal,
            ]
        )

    def enqueue_cloud_goal(
        self,
        goal: str,
        *,
        worker: str,
        planner: str = "planner",
        contract: str = "",
    ) -> CommandResult:
        args = ["enqueue"]
        if contract:
            args.extend(["--harness-contract", contract])
        args.extend(
            [
                "--planner",
                planner,
                "--executor",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", "executor"),
                "--executor-worker",
                worker,
                "--goal",
                goal,
            ]
        )
        return self.run(args)

    def status(self, *, json_output: bool = False) -> CommandResult:
        return self.run(["status", "--json"] if json_output else ["status"])

    def mode3a_status(self, *, json_output: bool = False) -> CommandResult:
        return self.run(["mode3a-status", "--json"] if json_output else ["mode3a-status"])

    def monitor(self, *, json_output: bool = False) -> CommandResult:
        args = [
            "monitor",
            "--auto-continue",
            "--auto-dispatch",
            "--auto-commit-owned",
        ]
        if json_output:
            args.append("--json")
        return self.run(args)

    def background_supervision(self) -> dict[str, object]:
        result = self.run(["capabilities", "--json"])
        payload: dict[str, object] = {}
        if result.returncode == 0:
            try:
                parsed = json.loads(result.stdout)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                payload = parsed
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, dict):
            capabilities = payload
        supervision = capabilities.get("supervision")
        watcher = supervision.get("watcher") if isinstance(supervision, dict) else None
        watcher = watcher if isinstance(watcher, dict) else {}
        active = (
            result.returncode == 0
            and watcher.get("timer_active") is True
            and str(watcher.get("state") or "").lower() == "active"
        )
        return {
            "active": active,
            "timer_active": watcher.get("timer_active") is True,
            "state": str(watcher.get("state") or "unknown"),
            "summary": str(
                watcher.get("summary")
                or (
                    "Background supervisor is active."
                    if active
                    else "Background supervisor could not be verified."
                )
            ),
            "returncode": result.returncode,
        }


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
            "External long-horizon goal",
            "",
            "Use the configured external orchestrator as a durable, evidence-driven goal worker.",
            "",
            "Planner, executor, and worker names come from the external backend configuration.",
            "Boundary: bounded external goal, reviewable artifacts, deterministic review and acceptance gates.",
            "",
            "Autonomy contract:",
            "- Preserve the full original objective across every continuation and recovery.",
            "- Derive and persist a concrete plan, current subgoal, checkpoints, and requirement list.",
            "- Inspect current files and external state before relying on an earlier claim.",
            "- Treat failed checks and review findings as repair input while meaningful progress is possible.",
            "- Ask for human input only when the same blocking condition repeats in three consecutive supervisor cycles without progress.",
            "- Do not mark the goal complete because time, attempts, context, or a budget was consumed.",
            "",
            "Goal:",
            objective,
            "",
            "Allowed files or areas:",
            *[f"- {path}" for path in allowed_paths],
            "",
            "Done when:",
            "- The requested task is fully implemented, not narrowed to an easier substitute.",
            "- A requirement-by-requirement completion audit proves the original objective.",
            "- Changed files are listed.",
            "- Verification commands and results are recorded.",
            "- The local supervisor independently reviews and accepts the result.",
            "- A blocked outcome remains blocked and is never reported as successful completion.",
            "",
            "Verification:",
            *[f"- {command}" for command in verification],
            "",
            "Guardrails:",
            *[f"- {guardrail}" for guardrail in guardrails],
        ]
    )


def build_experimental_goal(
    objective: str,
    *,
    safe_areas: tuple[str, ...],
    checks: tuple[str, ...],
) -> str:
    objective = objective.strip()
    if not objective:
        raise ValueError("objective must not be empty")

    areas = safe_areas or ("one narrow file or sandbox evidence artifact chosen from the task",)
    verification = checks or ("run one narrow verification command and record the result",)
    return "\n".join(
        [
            "Experimental external executor canary",
            "",
            "Use this only as a tiny bounded canary, not a broad production edit.",
            "",
            "Goal:",
            objective,
            "",
            "Safe areas:",
            *[f"- {area}" for area in areas],
            "",
            "Done when:",
            "- A tiny useful change or honest blocked report is produced.",
            "- Changed files and verification are recorded.",
            "",
            "Verification:",
            *[f"- {command}" for command in verification],
            "",
            "Guardrails:",
            "- Do not touch secrets, services, routing, provider accounts, or broad source areas.",
            "- If the task is bigger than a canary, stop and report that Mode 2 or Mode 3 is more appropriate.",
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
            "These modes apply only to the optional external backend; the embedded GUI uses one verified goal flow.",
            'Interactive: agentic-harness work',
            'Portable default: agentic-harness goal "describe one verified outcome"',
        ]
    )
    return "\n".join(lines)


def _external_setting(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _supports_candidate_contract(result: CommandResult, contract: str) -> bool:
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    containers = [payload]
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, dict):
        containers.append(capabilities)
    return any(
        isinstance(container.get("external_candidate_contracts"), list)
        and contract in container["external_candidate_contracts"]
        for container in containers
    )


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
    supervision = bridge.background_supervision()
    lines = [
        "Agentic Harness Linux/Ubuntu setup",
        "",
        "Install the CLI:",
        "  pipx install local-agentic-harness",
        "",
        "Or install from a source checkout for development:",
        '  python3 -m pip install -e ".[test]"',
        "",
        "Run a local smoke test:",
        "  agentic-harness selftest",
        "",
        "Run a portable verified goal:",
        '  agentic-harness do "fix one small verified issue"',
        "",
        "Useful commands:",
        "  agentic-harness check",
        "  agentic-harness doctor",
        "",
        "Optional local-goal backend override:",
        "  export AGENTIC_HARNESS_LOCAL_GOAL=/path/to/local-goal",
        "",
        "Optional local-goal checkout root:",
        "  agentic-harness gui --doc-root /path/to/compatible/checkout",
        "  export AGENTIC_HARNESS_DOC_ROOT=/path/to/compatible/checkout",
        "",
        "If neither is set, Agentic Harness checks the current directory. The",
        "Python package does not install the optional external local-goal backend.",
        "",
        f"Detected local-goal: {bridge.local_goal}",
        f"Detected local-goal usable: {bridge.available()}",
        f"Background supervisor active: {supervision.get('active') is True}",
        f"Background supervisor: {supervision.get('summary')}",
    ]
    return "\n".join(lines)
