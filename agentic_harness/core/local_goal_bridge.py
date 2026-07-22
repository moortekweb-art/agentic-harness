"""Bridge to an optional external goal-orchestration executable.

This module intentionally keeps the public CLI small while delegating execution
to the existing local-goal runtime when it is installed on the machine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
from threading import Lock
import time


DOC_ROOT_ENV = "AGENTIC_HARNESS_DOC_ROOT"
LOCAL_GOAL_ENV = "AGENTIC_HARNESS_LOCAL_GOAL"
EXTERNAL_CANDIDATE_CONTRACT = "agentic_harness.external_candidate.v1"
EXTERNAL_AUDIT_CONTRACT = "agentic_harness.external_audit.v1"
TURNSTONE_MODE3A_CONTRACT = "agentic_harness_turnstone_mode3.v1"
LOCAL_BUILD_ROUTE_ID = "local-build"
TURNSTONE_GLM_LOCAL_BUILD_ROUTE_ID = "turnstone-glm-local-build"
CLOUD_GLM_BUILD_ROUTE_ID = "cloud-glm-build"
GLM_READONLY_AUDIT_ROUTE_ID = "glm-readonly-audit"
MODE3A_PLANNER = "glm-5.2"
MODE3A_WORKER = "opencode-glm-build"
MODE3A_BACKEND_ID = "mode3a_cloud_long_horizon_goal"
MODE4_WORKER = "glm52-direct"
MODE4_BACKEND_ID = "glm52_fully_local_long_horizon_executor"
MODE4B_WORKER = "glm52-direct-implementation-canary"
MODE4B_BACKEND_ID = "glm52_direct_implementation_canary"
EXECUTION_PROFILES = frozenset({"automatic", "qwen-primary", "ornith-text"})


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
        title="Quick task",
        best_for="a small, clear job that can be completed in one focused pass",
        caution="Choose this when you already know the result you want.",
    ),
    HumanMode(
        key="guided",
        number=2,
        title="Plan first",
        best_for="important or unfamiliar work where the assistant should plan before changing anything",
        caution="Recommended when you are unsure which approach is best.",
    ),
    HumanMode(
        key="cloud",
        number=3,
        title="Keep working",
        best_for="a larger job that may need several attempts or take a while",
        caution="Use this for work you want the assistant to continue until it has evidence.",
    ),
    HumanMode(
        key="experimental",
        number=4,
        title="Safe experiment",
        best_for="a tiny, low-risk trial where an experimental worker can be evaluated",
        caution="Keep this away from important production work and broad changes.",
    ),
)


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    metadata: dict[str, object] = field(default_factory=dict)


def _started_run_dir(result: CommandResult) -> str:
    """Return the run directory printed by a successful local-goal start."""
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "run_dir" and value.strip():
            return value.strip()
    return ""


@dataclass
class LocalGoalBridge:
    doc_root: str | Path | None = None
    local_goal: str | Path | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    # Model-profile activation can include a guarded same-GPU service window.
    # Match the deployed bridge timeout so that a healthy model swap is not
    # reported as a failed task start after two minutes.
    timeout_seconds: int = 360
    status_cache_seconds: float = 1.5
    _status_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _status_cache: CommandResult | None = field(default=None, init=False, repr=False)
    _status_cache_at: float = field(default=0.0, init=False, repr=False)
    _last_run_cache: CommandResult | None = field(default=None, init=False, repr=False)
    _last_run_cache_at: float = field(default=0.0, init=False, repr=False)
    _monitor_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _monitor_cache: CommandResult | None = field(default=None, init=False, repr=False)
    _monitor_cache_at: float = field(default=0.0, init=False, repr=False)
    # A model profile is selected before the goal starts and (for Ornith)
    # attached afterwards.  The profile manager serializes each individual
    # command, but it cannot make that multi-command sequence atomic.  Keep the
    # entire managed start transaction together so concurrent GUI requests
    # cannot swap the model between another request's verification and start.
    _start_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        resolved_doc_root = resolve_doc_root(self.doc_root)
        self.doc_root = resolved_doc_root
        if self.local_goal is None:
            configured = os.environ.get(LOCAL_GOAL_ENV, "").strip()
            self.local_goal = (
                Path(configured).expanduser()
                if configured
                else resolved_doc_root / "scripts/local-goal"
            )
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

    def enqueue_mode3a(
        self,
        options: Mode3AGoalOptions,
        *,
        capabilities: CommandResult | None = None,
        adapter_matrix: CommandResult | None = None,
        harness_modes: CommandResult | None = None,
    ) -> CommandResult:
        capabilities = capabilities or self.capabilities()
        harness_modes = harness_modes or self.harness_modes()
        legacy_contract = _supports_candidate_contract(capabilities, EXTERNAL_CANDIDATE_CONTRACT)
        managed_registry = _managed_mode_registry_present(harness_modes)
        if managed_registry and not _supports_managed_mode(harness_modes, MODE3A_BACKEND_ID):
            return _blocked_command_result(
                harness_modes,
                "The managed mode registry does not advertise Mode 3A as currently "
                "dispatchable.",
            )
        if _is_turnstone_executable(self):
            if not legacy_contract:
                return _blocked_command_result(
                    capabilities,
                    "The Turnstone wrapper does not advertise the required external "
                    f"candidate contract: {EXTERNAL_CANDIDATE_CONTRACT}",
                )
            if not _cloud_lane_available(capabilities):
                return _blocked_command_result(
                    capabilities,
                    "The Turnstone Mode 3A cloud coordinator lane is not currently available.",
                )
            adapter_status = self.mode3a_adapter_status()
            if not _turnstone_mode3a_ready(adapter_status):
                return _blocked_command_result(
                    adapter_status,
                    "Turnstone Mode 3A is active, blocked, or its managed adapter contract "
                    "could not be verified.",
                )
            return self.enqueue_cloud_goal(
                build_mode3a_goal(options),
                worker="local-builder",
                planner="glm-coordinator",
                executor="turnstone",
                contract=EXTERNAL_CANDIDATE_CONTRACT,
                route_id=TURNSTONE_GLM_LOCAL_BUILD_ROUTE_ID,
            )
        if not managed_registry and not legacy_contract:
            return CommandResult(
                args=harness_modes.args,
                returncode=2,
                stdout=harness_modes.stdout,
                stderr=(
                    "The backend does not advertise canonical Mode 3A or the legacy "
                    f"candidate contract: {EXTERNAL_CANDIDATE_CONTRACT}"
                ),
            )
        if not _cloud_lane_available(capabilities):
            return _blocked_command_result(
                capabilities,
                "The canonical Mode 3A cloud lane is not currently available.",
            )
        adapter_matrix = adapter_matrix or self.adapter_matrix()
        if not _worker_dispatchable(adapter_matrix, MODE3A_WORKER, mutation="implementation"):
            return _blocked_command_result(
                adapter_matrix,
                f"The canonical Mode 3A worker is not dispatchable: {MODE3A_WORKER}",
            )
        goal = build_mode3a_goal(options)
        return self.enqueue_cloud_goal(
            goal,
            worker=MODE3A_WORKER,
            planner=MODE3A_PLANNER,
            executor="opencode",
            contract=EXTERNAL_CANDIDATE_CONTRACT if legacy_contract else "",
            route_id=CLOUD_GLM_BUILD_ROUTE_ID,
        )

    def enqueue_mode4_audit(
        self,
        options: Mode3AGoalOptions,
        *,
        capabilities: CommandResult | None = None,
        adapter_matrix: CommandResult | None = None,
        harness_modes: CommandResult | None = None,
    ) -> CommandResult:
        """Dispatch the direct GLM audit worker through its audit-only contract."""
        capabilities = capabilities or self.capabilities()
        harness_modes = harness_modes or self.harness_modes()
        if not _supports_external_contract(
            capabilities,
            "external_audit_contracts",
            EXTERNAL_AUDIT_CONTRACT,
        ):
            return _blocked_command_result(
                capabilities,
                "The managed backend does not advertise the required audit-only contract: "
                f"{EXTERNAL_AUDIT_CONTRACT}",
            )
        if not _supports_managed_mode(harness_modes, MODE4_BACKEND_ID):
            return _blocked_command_result(
                harness_modes,
                "The managed mode registry does not advertise the direct GLM audit route "
                "as dispatchable.",
            )
        if not _cloud_lane_available(capabilities):
            return _blocked_command_result(
                capabilities,
                "The direct GLM audit lane is not currently available.",
            )
        adapter_matrix = adapter_matrix or self.adapter_matrix()
        if not _worker_dispatchable(adapter_matrix, MODE4_WORKER, mutation="audit"):
            return _blocked_command_result(
                adapter_matrix,
                f"The audit-only worker is not dispatchable: {MODE4_WORKER}",
            )
        return self.enqueue_cloud_goal(
            build_mode4_audit_goal(options),
            worker=MODE4_WORKER,
            planner="none",
            # The legacy queue schema accepts the local executor family here;
            # the explicitly pinned executor-worker is the process that
            # actually performs this cloud audit.
            executor="opencode",
            contract=EXTERNAL_AUDIT_CONTRACT,
            route_id=GLM_READONLY_AUDIT_ROUTE_ID,
        )

    def start_human_goal(
        self,
        *,
        mode_key: str,
        objective: str,
        safe_areas: tuple[str, ...] = (),
        checks: tuple[str, ...] = (),
        execution_profile: str = "automatic",
        route_id: str = "",
        supervision: str = "none",
    ) -> CommandResult:
        with self._start_lock:
            return self._start_human_goal_locked(
                mode_key=mode_key,
                objective=objective,
                safe_areas=safe_areas,
                checks=checks,
                execution_profile=execution_profile,
                route_id=route_id,
                supervision=supervision,
            )

    def _start_human_goal_locked(
        self,
        *,
        mode_key: str,
        objective: str,
        safe_areas: tuple[str, ...] = (),
        checks: tuple[str, ...] = (),
        execution_profile: str = "automatic",
        route_id: str = "",
        supervision: str = "none",
    ) -> CommandResult:
        route = managed_route_key(mode_key)
        expected_route_id = canonical_route_id(route, turnstone=_is_turnstone_executable(self))
        if route_id and route_id != expected_route_id:
            raise ValueError(
                f"route identity mismatch: {route_id!r} cannot dispatch managed route {route!r}; "
                f"expected {expected_route_id!r}"
            )
        route_id = expected_route_id
        if supervision not in {"none", "glm-5.2"}:
            raise ValueError("supervision must be none or glm-5.2")
        if supervision != "none" and route != "mode1":
            raise ValueError("GLM advisory supervision is available only for local Mode 1 goals")
        if execution_profile not in EXECUTION_PROFILES:
            raise ValueError(f"unsupported execution profile {execution_profile}")
        if route != "mode1" and execution_profile not in {
            "automatic",
            "qwen-primary",
        }:
            raise ValueError("ornith-text is available only for local Mode 1 goals")
        if route != "mode1" and execution_profile == "qwen-primary":
            raise ValueError("local model profiles are available only for Mode 1 goals")
        if route == "mode2":
            return _blocked_command_result(
                self.harness_modes(),
                "Mode 2 supervises an already-running local goal; this backend does not "
                "advertise an independent safe Mode 2 start operation.",
            )
        if route == "mode4b":
            return _blocked_command_result(
                self.harness_modes(),
                "Mode 4B is a disabled implementation canary and cannot be started from "
                "the managed GUI.",
            )
        if route == "legacy-experimental":
            return _blocked_command_result(
                self.harness_modes(),
                "The legacy experimental route is retired. It cannot prove a distinct "
                "bounded canary dispatch contract and is disabled.",
            )

        profile_status: CommandResult | None = None
        if execution_profile != "automatic":
            profile_status = self.model_profile_status()
            if not _supports_model_profiles(profile_status):
                return _blocked_command_result(
                    profile_status,
                    "The requested model profile cannot be verified on this managed backend.",
                )

        if (
            execution_profile == "qwen-primary"
            and _active_model_profile(profile_status) != "qwen-primary"
        ):
            restore = self.run(["model-profile-restore", "--json"])
            if restore.returncode != 0:
                return _profile_recovery_failure(
                    restore,
                    restore,
                    "Qwen was requested but the active model could not be restored before start.",
                )
            profile_status = self.model_profile_status()
            if _active_model_profile(profile_status) != "qwen-primary":
                return _blocked_command_result(
                    profile_status,
                    "Qwen restoration returned successfully but the healthy primary profile "
                    "could not be verified.",
                )
        elif execution_profile == "ornith-text":
            if _model_profile_attached(profile_status):
                return _blocked_command_result(
                    profile_status or CommandResult((), 2, "", ""),
                    "The active Ornith model window is already attached to another goal.",
                )
            activation = self.run(
                ["model-profile-activate", "--profile", execution_profile, "--json"]
            )
            if activation.returncode != 0:
                restore = self.run(["model-profile-restore", "--json"])
                if restore.returncode != 0:
                    return _profile_recovery_failure(
                        activation,
                        restore,
                        "Ornith activation failed and Qwen restoration also failed.",
                    )
                return activation
            profile_status = self.model_profile_status()
            if _active_model_profile(profile_status) != "ornith-text":
                restore = self.run(["model-profile-restore", "--json"])
                failure = _blocked_command_result(
                    profile_status,
                    "Ornith activation returned successfully but a healthy Ornith profile "
                    "could not be verified.",
                )
                if restore.returncode != 0:
                    return _profile_recovery_failure(
                        failure,
                        restore,
                        "Ornith verification failed and Qwen restoration also failed.",
                    )
                return failure

        capabilities = self.capabilities()
        routing = _routing_defaults(capabilities)
        supervisor_receipt: dict[str, object] = {
            "requested": supervision,
            "running": False,
            "started_here": False,
            "contract": "local_goal_glm_supervisor.v1",
            "model": "glm-5.2",
            "route_id": route_id,
        }
        if route == "mode1":
            result = self.start_local_goal(
                objective,
                executor=routing["executor"],
                verification=checks,
                safe_areas=safe_areas,
                route_id=route_id,
            )
        elif route == "legacy-guided":
            result = self.start_guided_goal(
                objective,
                planner=routing["planner"],
                executor=routing["executor"],
            )
        elif route == "legacy-cloud":
            legacy_contract = _supports_candidate_contract(
                capabilities, EXTERNAL_CANDIDATE_CONTRACT
            )
            if not legacy_contract:
                result = _blocked_command_result(
                    capabilities,
                    "The legacy cloud route requires an explicitly advertised external "
                    f"candidate contract: {EXTERNAL_CANDIDATE_CONTRACT}",
                )
            elif _is_turnstone_executable(self):
                # The wrapper classifies every external-candidate enqueue as
                # Turnstone Mode 3A. Old client aliases must pass the same
                # active-run and adapter-contract preflight as the canonical
                # route instead of dispatching around it.
                result = self.enqueue_mode3a(
                    Mode3AGoalOptions(
                        objective=objective,
                        allowed_paths=safe_areas,
                        verification=checks,
                    ),
                    capabilities=capabilities,
                )
            else:
                result = self.enqueue_cloud_goal(
                    build_mode3a_goal(
                        Mode3AGoalOptions(
                            objective=objective,
                            allowed_paths=safe_areas,
                            verification=checks,
                        )
                    ),
                    worker=_external_setting(
                        "AGENTIC_HARNESS_EXTERNAL_LONG_WORKER",
                        routing["long_worker"],
                    ),
                    planner=_external_setting(
                        "AGENTIC_HARNESS_EXTERNAL_PLANNER",
                        routing["planner"],
                    ),
                    executor=routing["executor"],
                    contract=EXTERNAL_CANDIDATE_CONTRACT if legacy_contract else "",
                )
        elif route == "mode3a":
            adapter_matrix = self.adapter_matrix()
            harness_modes = self.harness_modes()
            result = self.enqueue_mode3a(
                Mode3AGoalOptions(
                    objective=objective,
                    allowed_paths=safe_areas,
                    verification=checks,
                ),
                capabilities=capabilities,
                adapter_matrix=adapter_matrix,
                harness_modes=harness_modes,
            )
        elif route == "mode4":
            adapter_matrix = self.adapter_matrix()
            harness_modes = self.harness_modes()
            result = self.enqueue_mode4_audit(
                Mode3AGoalOptions(
                    objective=objective,
                    allowed_paths=safe_areas,
                    verification=checks,
                ),
                capabilities=capabilities,
                adapter_matrix=adapter_matrix,
                harness_modes=harness_modes,
            )
        else:
            raise ValueError(f"unsupported managed route {route}")

        if supervision == "glm-5.2" and result.returncode == 0:
            # Start supervision only after the local run exists.  The supervisor
            # intentionally exits when no goal is active, so starting it before
            # quick-start creates a false-positive receipt during the startup gap.
            supervisor_status = self.glm_supervisor_status()
            supervisor_payload = _json_object(supervisor_status)
            supervision_failure = ""
            start_attempted = False
            if not _glm_supervisor_running(supervisor_status):
                start_attempted = True
                started = self.glm_supervisor_start()
                if not _glm_supervisor_running(started):
                    supervision_failure = (
                        "GLM advisory supervision was requested but could not be "
                        "started after the local goal became active."
                    )
                else:
                    supervisor_receipt["started_here"] = True
                    supervisor_payload = _json_object(started)
                    verified = self.glm_supervisor_status()
                    if not _glm_supervisor_running(verified):
                        supervision_failure = (
                            "GLM advisory supervision started after the local goal, but "
                            "its running state could not be verified."
                        )
                    else:
                        supervisor_payload = _json_object(verified)
            if supervision_failure:
                if start_attempted:
                    stopped = self.glm_supervisor_stop()
                    supervisor_receipt["supervisor_cleanup_returncode"] = stopped.returncode
                rollback = self._rollback_started_goal(result)
                supervisor_receipt.update(
                    {
                        "running": False,
                        "status": "failed",
                        "start_rollback": rollback,
                    }
                )
                result = CommandResult(
                    args=result.args,
                    returncode=2,
                    stdout=result.stdout,
                    stderr=supervision_failure,
                    metadata=result.metadata,
                )
            else:
                supervisor_receipt.update(
                    {
                        "running": True,
                        "status": str(supervisor_payload.get("status") or "running"),
                        "session": str(supervisor_payload.get("session") or ""),
                        "reviewer": str(
                            supervisor_payload.get("reviewer") or "glm-5.2"
                        ),
                    }
                )
        if supervision == "glm-5.2":
            result = _with_metadata(result, glm_supervision=supervisor_receipt)

        if result.returncode == 0 and execution_profile == "ornith-text":
            attachment_args = ["model-profile-attach"]
            started_run_dir = _started_run_dir(result)
            if started_run_dir:
                attachment_args.extend(["--run-dir", started_run_dir])
            attachment_args.append("--json")
            attachment = self.run(attachment_args)
            observed = self.model_profile_status()
            if _model_profile_attachment_matches(observed, started_run_dir):
                self.invalidate_status_caches()
                if attachment.returncode == 0:
                    return result
                return CommandResult(
                    result.args,
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    {
                        "profile_attachment": "confirmed_after_initial_error",
                        "execution_profile": execution_profile,
                        "run_dir": started_run_dir,
                    },
                )
            retry = self.run(attachment_args)
            observed_after_retry = self.model_profile_status()
            if _model_profile_attachment_matches(observed_after_retry, started_run_dir):
                self.invalidate_status_caches()
                return CommandResult(
                    result.args,
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    {
                        "profile_attachment": (
                            "recovered_after_retry"
                            if retry.returncode == 0
                            else "confirmed_after_retry_error"
                        ),
                        "execution_profile": execution_profile,
                        "run_dir": started_run_dir,
                    },
                )
            # The goal has already started, so restoring Qwen here could
            # invalidate it. Return a review-required success receipt with
            # explicit reconciliation evidence instead of inviting a
            # duplicate task start.
            self.invalidate_status_caches()
            return CommandResult(
                result.args,
                result.returncode,
                result.stdout,
                _join_errors(attachment.stderr, retry.stderr),
                {
                    "profile_attachment": "reconciliation_required",
                    "execution_profile": execution_profile,
                    "run_dir": started_run_dir,
                    "profile_status": _json_object(observed_after_retry),
                    "summary": (
                        "The goal started on Ornith, but its model lease could not be attached "
                        "to the started run after two attempts. Reconcile the active run before "
                        "the temporary model window can restore safely."
                    ),
                },
            )
        elif execution_profile == "ornith-text":
            # Activation happened but no goal owns the model window.
            restore = self.run(["model-profile-restore", "--json"])
            if restore.returncode != 0:
                return _profile_recovery_failure(
                    result,
                    restore,
                    "Goal start failed and Qwen restoration also failed.",
                )
        self.invalidate_status_caches()
        return result

    def _rollback_started_goal(self, result: CommandResult) -> dict[str, object]:
        """Stop only the exact run created by a failed supervised-start transaction."""

        expected = _started_run_dir(result).strip().rstrip("/")
        rollback: dict[str, object] = {
            "attempted": False,
            "expected_run_dir": expected,
            "stopped": False,
        }
        if not expected:
            rollback["reason"] = "start result did not report a run directory"
            return rollback
        self.invalidate_status_caches()
        status = self.status(json_output=True)
        payload = _json_object(status)
        active = payload.get("active_goal")
        observed = (
            str(active.get("run_dir") or "").strip().rstrip("/")
            if isinstance(active, dict)
            else ""
        )
        rollback["observed_run_dir"] = observed
        if status.returncode != 0 or observed != expected:
            rollback["reason"] = "active run identity did not match the supervised start"
            return rollback
        rollback["attempted"] = True
        stopped = self.run(["stop"])
        rollback["returncode"] = stopped.returncode
        rollback["stopped"] = stopped.returncode == 0
        self.invalidate_status_caches()
        return rollback

    def invalidate_status_caches(self) -> None:
        with self._status_lock:
            self._status_cache = None
            self._status_cache_at = 0.0
            self._last_run_cache = None
            self._last_run_cache_at = 0.0
        with self._monitor_lock:
            self._monitor_cache = None
            self._monitor_cache_at = 0.0

    def start_local_goal(
        self,
        goal: str,
        *,
        executor: str = "opencode",
        verification: tuple[str, ...] = (),
        safe_areas: tuple[str, ...] = (),
        route_id: str = LOCAL_BUILD_ROUTE_ID,
    ) -> CommandResult:
        args = [
            "quick-start",
            "--route-id",
            route_id,
            "--title",
            goal,
            "--executor",
            _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", executor),
            "--goal",
            goal,
        ]
        if safe_areas:
            for area in safe_areas:
                args.extend(["--allowed-path", area])
        else:
            # The GUI labels an empty advanced work-area field as "Entire project".
            # Carry that explicit choice into the ticket instead of silently
            # sending an unscoped request that the managed worker rejects.
            args.extend(["--project-scope", "entire-project"])
        for command in verification:
            args.extend(["--verification-command", command])
        return self.run(args)

    def start_guided_goal(
        self,
        goal: str,
        *,
        planner: str = "gpt-5.5",
        executor: str = "opencode",
    ) -> CommandResult:
        return self.run(
            [
                "premium-start",
                "--planner",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_PLANNER", planner),
                "--executor",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", executor),
                "--goal",
                goal,
            ]
        )

    def capabilities(self) -> CommandResult:
        return self.run(["capabilities", "--json"])

    def harness_modes(self) -> CommandResult:
        return self.run(["harness-modes", "--json"])

    def adapter_matrix(self) -> CommandResult:
        return self.run(["adapter-matrix", "--json"])

    def model_profile_status(self) -> CommandResult:
        return self.run(["model-profile-status", "--json"])

    def mode3a_adapter_status(self) -> CommandResult:
        """Probe an optional managed Mode 3A adapter without starting work."""
        return self.run(["turnstone-monitor", "--json"])

    def glm_supervisor_status(self) -> CommandResult:
        return self.run(["glm-supervisor", "status", "--reviewer", "glm-5.2", "--json"])

    def glm_supervisor_start(self) -> CommandResult:
        return self.run(["glm-supervisor", "start", "--reviewer", "glm-5.2", "--json"])

    def glm_supervisor_stop(self) -> CommandResult:
        return self.run(["glm-supervisor", "stop", "--reviewer", "glm-5.2", "--json"])

    def enqueue_cloud_goal(
        self,
        goal: str,
        *,
        worker: str,
        planner: str = "planner",
        executor: str = "opencode",
        contract: str = "",
        route_id: str = "",
    ) -> CommandResult:
        args = ["enqueue"]
        if route_id:
            args.extend(["--route-id", route_id])
        if contract:
            args.extend(["--harness-contract", contract])
        args.extend(
            [
                "--planner",
                planner,
                "--executor",
                _external_setting("AGENTIC_HARNESS_EXTERNAL_EXECUTOR", executor),
                "--executor-worker",
                worker,
                "--goal",
                goal,
            ]
        )
        return self.run(args)

    def status(self, *, json_output: bool = False) -> CommandResult:
        # The browser can ask for health, current work, and stream updates at
        # nearly the same time.  The external controller status command is
        # comparatively expensive, so serialize it and share one very short
        # cache window across those requests.  This prevents multiple tabs
        # from spawning overlapping controller/GPU status process trees.
        if not json_output:
            return self.run(["status"])
        with self._status_lock:
            now = time.monotonic()
            if (
                self._status_cache is not None
                and now - self._status_cache_at < self.status_cache_seconds
            ):
                return self._status_cache
            result = self.run(["status", "--json"])
            self._status_cache = result
            self._status_cache_at = time.monotonic()
            return result

    def last_run(self, *, json_output: bool = False) -> CommandResult:
        args = ["last-run"]
        if json_output:
            args.append("--json")
        now = time.monotonic()
        with self._status_lock:
            if self._last_run_cache is not None and now - self._last_run_cache_at < 5.0:
                return self._last_run_cache
            result = self.run(args)
            self._last_run_cache = result
            self._last_run_cache_at = time.monotonic()
            return result

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
        now = time.monotonic()
        with self._monitor_lock:
            if self._monitor_cache is not None and now - self._monitor_cache_at < 8.0:
                return self._monitor_cache
            result = self.run(args)
            self._monitor_cache = result
            self._monitor_cache_at = time.monotonic()
            return result

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
            "Planner, executor, and worker selection is pinned by the managed Mode 3A contract.",
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
            "- The structured completion claim covers its worker-derived requirements and the configured deterministic review passes.",
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


def build_mode4_audit_goal(options: Mode3AGoalOptions) -> str:
    """Build a source-read-only audit/proposal packet for the Mode 4 worker."""
    objective = options.objective.strip()
    if not objective:
        raise ValueError("objective must not be empty")
    allowed_paths = options.allowed_paths or (
        "Inspect only the narrowest files and evidence needed for the audit.",
    )
    verification = options.verification or (
        "Record the read-only checks and evidence used to support every finding.",
    )
    return "\n".join(
        [
            "Direct GLM read-only audit",
            "",
            "Produce an evidence-backed audit or implementation proposal. Do not edit source files.",
            "The worker may write only its managed result artifacts.",
            "",
            "Original objective:",
            objective,
            "",
            "Audit scope:",
            *[f"- {path}" for path in allowed_paths],
            "",
            "Verification:",
            *[f"- {command}" for command in verification],
            "",
            "Required result:",
            "- State the observed evidence, findings, risks, and recommended next action.",
            "- Clearly distinguish confirmed facts from inference.",
            "- Report blockers honestly; do not claim implementation or completion.",
            "",
            "Guardrails:",
            "- Do not modify source, configuration, services, credentials, routing, or provider state.",
            "- Do not broaden the requested audit scope without explaining why.",
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


def managed_route_key(value: str) -> str:
    """Resolve current managed routes while retaining safe legacy aliases.

    The old ``guided`` route is intentionally kept under a legacy-only key: it
    starts a premium planner followed by a local builder and is not the same as
    canonical Mode 2's GLM-supervision/Codex-spot-check policy.
    """
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "mode1": "mode1",
        LOCAL_BUILD_ROUTE_ID: "mode1",
        "mode-1": "mode1",
        "1": "mode1",
        "local": "mode1",
        "quick": "mode1",
        "mode2": "mode2",
        "mode-2": "mode2",
        "2": "mode2",
        "mode3a": "mode3a",
        TURNSTONE_GLM_LOCAL_BUILD_ROUTE_ID: "mode3a",
        CLOUD_GLM_BUILD_ROUTE_ID: "mode3a",
        "mode-3a": "mode3a",
        "3a": "mode3a",
        "3": "legacy-cloud",
        "cloud": "legacy-cloud",
        "persistent": "legacy-cloud",
        "thorough": "legacy-cloud",
        "mode4": "mode4",
        GLM_READONLY_AUDIT_ROUTE_ID: "mode4",
        "mode-4": "mode4",
        "4": "mode4",
        "audit": "mode4",
        "mode4b": "mode4b",
        "mode-4b": "mode4b",
        "4b": "mode4b",
        "experimental": "legacy-experimental",
        "experiment": "legacy-experimental",
        "guided": "legacy-guided",
        "plan": "legacy-guided",
        "legacy-guided": "legacy-guided",
        "legacy-cloud": "legacy-cloud",
        "legacy-experimental": "legacy-experimental",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            f"unknown managed route {value!r}; choose mode1, mode2, mode3a, mode4, or mode4b"
        ) from exc


def canonical_route_id(route: str, *, turnstone: bool = False) -> str:
    """Return the dispatch identity for a managed route, independent of UI aliases."""
    normalized = managed_route_key(route)
    if normalized == "mode1":
        return LOCAL_BUILD_ROUTE_ID
    if normalized == "mode2":
        return "local-build-codex-spotcheck-policy"
    if normalized == "mode3a":
        return (
            TURNSTONE_GLM_LOCAL_BUILD_ROUTE_ID
            if turnstone
            else CLOUD_GLM_BUILD_ROUTE_ID
        )
    if normalized == "mode4":
        return GLM_READONLY_AUDIT_ROUTE_ID
    if normalized == "mode4b":
        return "glm-direct-implementation-canary"
    return ""


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
            "Interactive: agentic-harness work",
            'Portable default: agentic-harness goal "describe one verified outcome"',
        ]
    )
    return "\n".join(lines)


def _external_setting(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _json_object(result: CommandResult) -> dict[str, object]:
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _capabilities_payload(result: CommandResult) -> dict[str, object]:
    payload = _json_object(result)
    capabilities = payload.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else payload


def _is_turnstone_executable(bridge: LocalGoalBridge) -> bool:
    local_goal = bridge.local_goal
    return local_goal is not None and Path(local_goal).name == "local-goal-turnstone"


def _turnstone_mode3a_ready(result: CommandResult) -> bool:
    payload = _json_object(result)
    classification = str(payload.get("classification") or payload.get("status") or "").lower()
    return bool(
        payload.get("contract") == TURNSTONE_MODE3A_CONTRACT
        and payload.get("active") is False
        # An accepted Turnstone run remains as the durable current pointer and
        # is rendered as ``done``. The controller explicitly permits a new run
        # over terminal state, so treat only verified ready/done states as free.
        and classification in {"ready", "done"}
    )


def _supports_model_profiles(result: CommandResult) -> bool:
    payload = _json_object(result)
    return payload.get("contract") == "node1_model_profile.v1"


def _active_model_profile(result: CommandResult | None) -> str:
    if result is None or not _supports_model_profiles(result):
        return ""
    payload = _json_object(result)
    window = payload.get("window")
    if isinstance(window, dict) and window.get("healthy") is False:
        return ""
    if payload.get("health") not in {None, 200}:
        return ""
    return str(payload.get("profile") or "")


def _model_profile_attached(result: CommandResult | None) -> bool:
    if result is None or not _supports_model_profiles(result):
        return False
    window = _json_object(result).get("window")
    return isinstance(window, dict) and window.get("attached") is True


def _model_profile_attachment_matches(
    result: CommandResult | None,
    expected_run_dir: str,
) -> bool:
    """Confirm attachment and, when reported, bind it to the started run."""
    if not _model_profile_attached(result) or result is None:
        return False
    expected = expected_run_dir.strip().rstrip("/")
    if not expected:
        return True
    window = _json_object(result).get("window")
    if not isinstance(window, dict):
        return False
    observed = ""
    for key in ("run_dir", "attached_run_dir", "run_id"):
        value = window.get(key)
        if isinstance(value, str) and value.strip():
            observed = value.strip().rstrip("/")
            break
    # Older compatible profile managers advertise only the attached boolean.
    # Keep that contract usable, but never accept a contradictory identity when
    # the backend supplies one.
    if not observed:
        return True
    return observed == expected or (
        "/" not in observed and observed == expected.rsplit("/", 1)[-1]
    )


def _join_errors(*messages: str) -> str:
    unique: list[str] = []
    for message in messages:
        normalized = message.strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return "\n".join(unique)


def _profile_recovery_failure(
    primary: CommandResult,
    recovery: CommandResult,
    summary: str,
) -> CommandResult:
    return CommandResult(
        args=recovery.args,
        returncode=recovery.returncode or primary.returncode or 2,
        stdout=_join_errors(primary.stdout, recovery.stdout),
        stderr=_join_errors(summary, primary.stderr, recovery.stderr),
        metadata={
            "profile_recovery": "failed",
            "summary": summary,
            "primary_returncode": primary.returncode,
            "recovery_returncode": recovery.returncode,
        },
    )


def _cloud_lane_available(result: CommandResult) -> bool:
    payload = _capabilities_payload(result)
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict):
        return False
    cloud = lanes.get("cloud_executor")
    return (
        isinstance(cloud, dict)
        and cloud.get("installed") is not False
        and cloud.get("available_now") is not False
    )


def _worker_dispatchable(
    result: CommandResult,
    worker: str,
    *,
    mutation: str,
) -> bool:
    payload = _json_object(result)
    matrix = payload.get("matrix")
    if not isinstance(matrix, list):
        return False
    for row in matrix:
        if not isinstance(row, dict) or row.get("worker") != worker:
            continue
        readiness = str(row.get("readiness") or "").lower()
        return (
            row.get("enabled") is True
            and row.get("binary_resolved") is not False
            and readiness not in {"blocked", "disabled", "retired"}
            and (
                row.get("mutation_default") == mutation
                or (
                    mutation == "audit"
                    and row.get("mutation_default") in {"audit_only", "proposal"}
                )
            )
        )
    return False


def _blocked_command_result(result: CommandResult, reason: str) -> CommandResult:
    return CommandResult(
        args=result.args,
        returncode=2,
        stdout=result.stdout,
        stderr=reason,
    )


def _routing_defaults(result: CommandResult) -> dict[str, str]:
    """Choose only routes advertised by the optional external backend."""
    defaults = {
        "executor": "opencode",
        "planner": "gpt-5.5",
        "long_worker": "opencode-kimi-build",
        "experimental_worker": "glm52-direct-implementation-canary",
    }
    if result.returncode != 0:
        return defaults
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return defaults
    if not isinstance(payload, dict):
        return defaults
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, dict):
        payload = capabilities
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict):
        return defaults

    local = lanes.get("local")
    if isinstance(local, dict) and isinstance(local.get("executor"), str):
        defaults["executor"] = local["executor"]

    guided = lanes.get("premium_planner_local_builder")
    if isinstance(guided, dict):
        planners = _string_values(guided.get("planners"))
        if planners:
            defaults["planner"] = "gpt-5.5" if "gpt-5.5" in planners else planners[0]

    cloud = lanes.get("cloud_executor")
    if isinstance(cloud, dict):
        workers = _string_values(cloud.get("executor_workers"))
        configured_long = cloud.get("default_executor_worker")
        if isinstance(configured_long, str) and configured_long in workers:
            defaults["long_worker"] = configured_long
        elif workers:
            defaults["long_worker"] = workers[0]
        canaries = _string_values(cloud.get("adapter_canary_workers"))
        if canaries:
            preferred = "glm52-direct-implementation-canary"
            defaults["experimental_worker"] = preferred if preferred in canaries else canaries[0]
    return defaults


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _supports_candidate_contract(result: CommandResult, contract: str) -> bool:
    return _supports_external_contract(
        result,
        "external_candidate_contracts",
        contract,
    )


def _supports_external_contract(
    result: CommandResult,
    field_name: str,
    contract: str,
) -> bool:
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
        isinstance(container.get(field_name), list)
        and contract in container[field_name]
        for container in containers
    )


def _glm_supervisor_running(result: CommandResult) -> bool:
    payload = _json_object(result)
    return (
        result.returncode == 0
        and payload.get("contract") == "local_goal_glm_supervisor.v1"
        and payload.get("running") is True
        and str(payload.get("reviewer") or "glm-5.2") == "glm-5.2"
    )


def _with_metadata(result: CommandResult, **metadata: object) -> CommandResult:
    merged = dict(result.metadata)
    merged.update(metadata)
    return CommandResult(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        metadata=merged,
    )


def _supports_managed_mode(result: CommandResult, backend_id: str) -> bool:
    payload = _json_object(result)
    if (
        payload.get("contract") != "local_goal_harness_modes.v1"
        or payload.get("status") != "available"
    ):
        return False
    modes = payload.get("modes")
    if not isinstance(modes, list):
        return False
    return any(
        isinstance(mode, dict)
        and mode.get("id") == backend_id
        and managed_mode_record_dispatchable(mode)
        for mode in modes
    )


def _managed_mode_registry_present(result: CommandResult) -> bool:
    payload = _json_object(result)
    return payload.get("contract") == "local_goal_harness_modes.v1"


def managed_mode_record_dispatchable(mode: dict[str, object]) -> bool:
    readiness = str(mode.get("readiness") or "").strip().lower().replace("-", "_")
    if not readiness or any(
        marker in readiness
        for marker in ("blocked", "disabled", "retired", "unavailable", "not_ready")
    ):
        return False
    if mode.get("blocked") is True:
        return False
    if any(mode.get(key) is False for key in ("enabled", "available", "dispatchable")):
        return False
    blockers = mode.get("blockers")
    if isinstance(blockers, str) and blockers.strip():
        return False
    if isinstance(blockers, list) and any(str(item).strip() for item in blockers):
        return False
    return True


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
