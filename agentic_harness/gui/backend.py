"""Portable GUI execution backend built on the public harness engine."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from threading import Event, Lock, RLock, Thread
from typing import Any

import yaml

from agentic_harness.adapters.model_agent import OpenAICompatibleProvider
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.autonomy import AUTONOMY_CONTRACT, AutonomousRunner, AutonomyPolicy
from agentic_harness.core.config import (
    CONFIG_DIR,
    CONFIG_NAME,
    HarnessConfig,
    detect_review_command,
    load_config,
)
from agentic_harness.core.errors import ConfigError, HarnessError, StateLockError
from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.factory import autonomy_policy_from_config, build_supervisor
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.providers import ProviderProfile, resolve_api_key
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.safety import format_command, goal_safety_metadata, split_command
from agentic_harness.core.secure_io import write_private_text
from agentic_harness.core.workspace import workspace_change_summary


GUI_TASK_CONTRACT = "agentic_harness.gui_task.v2"


class EmbeddedExecutionBackend:
    """Run one project-local goal in a background thread with durable state."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        api_key: str | None = None,
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.store = ArtifactStore(self.project_dir / CONFIG_DIR)
        self.api_key = api_key
        self.policy = policy
        self._thread: Thread | None = None
        self._cancel = Event()
        self._thread_lock = Lock()
        self._config_lock = RLock()
        self._resume_orphaned_goal()

    def _resume_orphaned_goal(self) -> None:
        """Restart a durable active goal when this service owns no driver yet."""

        with self._config_lock:
            self._resume_orphaned_goal_locked()

    def _resume_orphaned_goal_locked(self) -> None:
        """Resume while the provider profile and its credential are locked."""

        try:
            if self._thread is not None and self._thread.is_alive():
                return
            goal = self.store.read_current_goal()
            if goal is None or goal.status in {GoalStatus.DONE, GoalStatus.FAILED}:
                return
            config = self._config()
            if self._credential_status(config)["configured"] is not True:
                return
            review_commands = _review_commands_from_goal(goal)
            if not review_commands and config.review_command:
                review_commands = [config.review_command]
            if not review_commands:
                self._record_driver_error(
                    ConfigError(
                        "The interrupted task has no independent verification command."
                    )
                )
                return
            self._start_thread(review_commands)
        except (ConfigError, HarnessError, OSError, ValueError):
            return

    def health(self) -> dict[str, Any]:
        readiness = self.readiness()
        return {
            "ok": True,
            "app": "agentic-harness",
            "backend": "embedded",
            "workspace": str(self.project_dir),
            "configured": readiness["state"] != "setup_required",
            "readiness": readiness,
        }

    def readiness(self) -> dict[str, Any]:
        with self._config_lock:
            return self._readiness_locked()

    def _readiness_locked(self) -> dict[str, Any]:
        try:
            config = self._config()
        except ConfigError as exc:
            return {
                "state": "setup_required",
                "label": "Setup needed",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": str(exc),
                "next_action": "Choose how this workspace should run tasks.",
            }
        credential = self._credential_status(config)
        if credential["configured"] is not True:
            return {
                "state": "credential_required",
                "label": "API key needed",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "Re-enter the model API key for this app session.",
                "next_action": "Open Setup and enter the key again.",
            }
        current = self.store.read_current_goal()
        if current is not None and current.status not in {
            GoalStatus.DONE,
            GoalStatus.FAILED,
        }:
            return {
                "state": "working",
                "label": "Work in progress",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "The current task is still in progress.",
                "next_action": "Follow its progress or stop it before starting another task.",
            }
        if current is not None and current.status.is_terminal:
            current = self._ensure_terminal_report(current)
            if not self._terminal_report_ready(current):
                return {
                    "state": "working",
                    "label": "Finalizing evidence",
                    "can_start": False,
                    "can_queue": False,
                    "requires_review": False,
                    "summary": "The durable terminal report is still being finalized.",
                    "next_action": "Wait for the final evidence report before starting another task.",
                }
        return {
            "state": "ready",
            "label": "Ready",
            "can_start": True,
            "can_queue": True,
            "requires_review": False,
            "summary": f"Ready to work with {self._worker_label(config)}.",
            "next_action": "Describe one goal and start the task.",
        }

    def setup(self) -> dict[str, Any]:
        with self._config_lock:
            return self._setup_locked()

    def _setup_locked(self) -> dict[str, Any]:
        suggested = _detect_check(self.project_dir)
        try:
            config = self._config()
        except ConfigError:
            config = None
        result: dict[str, Any] = {
            "contract": "agentic_harness.gui_setup.v1",
            "workspace": str(self.project_dir),
            "configured": config is not None,
            "suggested_check": suggested,
            "execution_options": [
                {
                    "key": "coding_agent",
                    "label": "Installed coding agent",
                    "description": "Use Codex, OpenCode, Aider, or another configured CLI.",
                    "data_location": "depends_on_agent",
                },
                {
                    "key": "local_model",
                    "label": "Local model",
                    "description": "Use a tool-capable OpenAI-compatible model on this machine or LAN.",
                    "data_location": "local",
                },
                {
                    "key": "cloud_model",
                    "label": "Cloud model",
                    "description": "Use your chosen OpenAI-compatible cloud endpoint and model ID.",
                    "data_location": "remote",
                },
            ],
        }
        if config is not None:
            result["worker"] = self._public_worker()
            result["credential"] = self._credential_status(config)
            result["verification_command"] = format_command(config.review_command)
            result["limits"] = {
                "max_cycles": config.goal_max_cycles,
                "max_elapsed_seconds": config.goal_max_elapsed_seconds,
                "max_total_tokens": config.goal_max_total_tokens,
                "max_provider_calls": config.goal_max_provider_calls,
                "max_tool_calls": config.goal_max_tool_calls,
            }
            if config.worker == "model_agent":
                result["provider"] = ProviderProfile(
                    endpoint=config.llm_endpoint,
                    model=config.llm_model,
                    api_key_env=config.llm_api_key_env,
                ).to_public_dict()
        return result

    def configure(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._config_lock:
            return self._configure_locked(body)

    def _configure_locked(self, body: dict[str, Any]) -> dict[str, Any]:
        execution = str(body.get("execution") or "").strip()
        verification_text = str(
            body.get("verification_command") or _detect_check(self.project_dir)
        ).strip()
        verification = split_command(verification_text) if verification_text else []
        if not verification:
            raise ValueError("Choose an independent verification command before saving setup.")
        if execution in {"local_model", "cloud_model"}:
            profile = ProviderProfile(
                endpoint=str(body.get("endpoint") or ""),
                model=str(body.get("model") or ""),
                api_key_env=str(body.get("api_key_env") or ""),
            )
            if profile.data_location == "cloud" and body.get("confirm_remote_data") is not True:
                raise ValueError(
                    "Confirm that selected file excerpts and tool results may leave this computer."
                )
            entered_key = str(body.get("api_key") or "").strip()
            if entered_key and profile.api_key_env:
                raise ValueError("Choose either a session API key or an environment variable, not both.")
            source = "session" if entered_key else ("env" if profile.api_key_env else "none")
            payload = {
                "version": 1,
                "worker": "model_agent",
                "llm": {
                    "endpoint": profile.endpoint,
                    "model": profile.model,
                    "api_key_env": profile.api_key_env,
                    "remote_data_confirmed": profile.data_location == "cloud",
                    "max_steps": _int_setting(body.get("max_steps"), 8, 1, 50),
                    "timeout": _int_setting(body.get("timeout"), 120, 1, 3_600),
                },
                "llm_credential_source": source,
                "review_command": verification,
                "review_command_timeout": _int_setting(
                    body.get("verification_timeout"), 300, 1, 3_600
                ),
                "autonomy": _autonomy_settings(body),
            }
            self._commit_configuration(payload, entered_key or None)
            return {
                "configured": True,
                "provider": profile.to_public_dict(),
                "credential": self._credential_status(load_config(self.project_dir)),
                "verification_command": verification_text,
            }
        if execution == "coding_agent":
            agent = str(body.get("agent") or "").strip().lower()
            command = _coding_agent_command(agent)
            payload = {
                "version": 1,
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": command,
                    "coding_agent_timeout": _int_setting(
                        body.get("agent_timeout"), 1_800, 1, 86_400
                    ),
                },
                "review_command": verification,
                "review_command_timeout": _int_setting(
                    body.get("verification_timeout"), 300, 1, 3_600
                ),
                "autonomy": _autonomy_settings(body),
            }
            self._commit_configuration(payload, None)
            return self.setup()
        raise ValueError("Choose an installed coding agent, local model, or cloud model.")

    def test_connection(self, body: dict[str, Any]) -> dict[str, Any]:
        profile = ProviderProfile(
            endpoint=str(body.get("endpoint") or ""),
            model=str(body.get("model") or ""),
            api_key_env=str(body.get("api_key_env") or ""),
        )
        entered_key = str(body.get("api_key") or "").strip()
        if entered_key and profile.api_key_env:
            raise ValueError("Choose either a session API key or an environment variable, not both.")
        api_key = entered_key or resolve_api_key(profile.api_key_env)
        provider = OpenAICompatibleProvider(
            endpoint=profile.endpoint,
            model=profile.model,
            api_key=api_key,
            timeout=_int_setting(body.get("timeout"), 30, 1, 120),
            retries=0,
        )
        response = provider.complete(
            [
                {
                    "role": "system",
                    "content": "Return exactly this JSON object: {\"action\":\"report_outcome\",\"arguments\":{\"status\":\"progress\"}}",
                },
                {"role": "user", "content": "Connection and structured-action test."},
            ]
        )
        content = response.content
        structured = isinstance(content, dict) and content.get("action") == "report_outcome"
        if not structured:
            raise ValueError(
                "The model answered, but it did not follow the structured action protocol."
            )
        return {
            "reachable": True,
            "structured_actions": True,
            "model": profile.model,
            "data_location": profile.data_location,
        }

    def set_session_credential(self, api_key: str) -> dict[str, Any]:
        value = api_key.strip()
        if not value:
            raise ValueError("API key must not be empty")
        with self._config_lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("Cannot replace the session key while a task is running")
            config = self._config()
            if config.worker != "model_agent" or config.llm_credential_source != "session":
                raise ValueError("This setup does not use a session API key")
            self.api_key = value
        self._resume_orphaned_goal()
        return {"source": "session", "configured": True}

    def _commit_configuration(
        self,
        payload: dict[str, Any],
        api_key: str | None,
    ) -> None:
        with self._config_lock:
            current = self.store.read_current_goal()
            if current is not None and not current.status.is_terminal:
                raise ValueError("Cannot change execution setup while a task is active")
            self._write_config(payload)
            self.api_key = api_key

    def _write_config(self, payload: dict[str, Any]) -> None:
        config_dir = self.project_dir / CONFIG_DIR
        if config_dir.is_symlink():
            raise ValueError("The configuration directory must not be a symlink.")
        config_dir.mkdir(parents=True, exist_ok=True)
        if not config_dir.is_dir():
            raise ValueError("The configuration path must be a directory.")
        config_dir.chmod(0o700)
        path = config_dir / CONFIG_NAME
        if path.is_symlink():
            raise ValueError("The configuration file must not be a symlink.")
        if path.exists() and not path.is_file():
            raise ValueError("The configuration file path must be a regular file.")
        write_private_text(path, yaml.safe_dump(payload, sort_keys=False))

    def _credential_status(self, config: HarnessConfig) -> dict[str, Any]:
        if config.worker != "model_agent":
            return {"source": "not_required", "configured": True}
        source = config.llm_credential_source
        if source == "session":
            configured = bool(self.api_key)
        elif source == "env":
            configured = bool(os.environ.get(config.llm_api_key_env, "").strip())
        else:
            configured = True
        return {"source": source, "configured": configured}

    def start(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._config_lock:
            return self._start_locked(body)

    def _start_locked(self, body: dict[str, Any]) -> dict[str, Any]:
        objective = str(body.get("objective") or "").strip()
        if not objective:
            return self._blocked_task(
                "Tell the assistant what you want done first.",
                action="edit_goal",
            )
        readiness = self.readiness()
        if readiness["can_start"] is not True:
            action = "setup" if readiness["state"] == "setup_required" else "view_current"
            return self._blocked_task(str(readiness["summary"]), action=action)
        try:
            config = self._config()
            safe_areas = _safe_areas(body.get("safe_areas"), self.project_dir)
            requested_checks = _checks(body.get("checks"))
            review_commands = requested_checks or (
                [config.review_command] if config.review_command else []
            )
            if not review_commands:
                return self._blocked_task(
                    "Add at least one independent verification command before starting.",
                    action="edit_checks",
                )
            supervisor = build_supervisor(
                self.project_dir,
                review_commands=review_commands,
                api_key=self.api_key,
                cancel_requested=self._cancel.is_set,
            )
            goal = supervisor.start(
                objective,
                metadata=goal_safety_metadata(
                    self.project_dir,
                    allowed_paths=safe_areas,
                    review_commands=review_commands,
                    path_enforcement=config.worker == "model_agent",
                    secret_env_names=[config.llm_api_key_env],
                    interface="gui",
                ),
            )
        except (ConfigError, HarnessError, OSError, ValueError) as exc:
            return self._blocked_task(str(exc), action="setup")
        self._cancel.clear()
        with self._thread_lock:
            self._thread = Thread(
                target=self._drive,
                args=(review_commands,),
                name=f"agentic-harness-{goal.id[:8]}",
                daemon=True,
            )
            self._thread.start()
        return self._task(goal)

    def status(self) -> dict[str, Any]:
        goal = self.store.read_current_goal()
        if goal is None:
            return self._ready_task()
        return self._task(goal)

    def history(self, *, query: str = "") -> list[dict[str, Any]]:
        needle = query.strip().lower()
        tasks = [self._task(goal) for goal in self.store.list_goals()]
        if needle:
            tasks = [task for task in tasks if needle in json.dumps(task, sort_keys=True).lower()]
        return tasks

    def events(self, *, after: int = 0) -> list[dict[str, Any]]:
        goal = self.store.read_current_goal()
        if goal is None:
            return []
        return TaskEventStore(self.project_dir, goal.id).read(after=after)

    def preview_file(self, path: str, *, goal_id: str = "") -> dict[str, Any]:
        goal = self._preview_goal(goal_id)
        if goal is None:
            raise ValueError("There is no task with changed files.")
        frozen_changes = goal.metadata.get("terminal_workspace_changes")
        changes = (
            frozen_changes
            if goal.status.is_terminal and isinstance(frozen_changes, dict)
            else workspace_change_summary(
                self.project_dir,
                goal.metadata.get("workspace_snapshot")
                if isinstance(goal.metadata.get("workspace_snapshot"), dict)
                else None,
                limit=5_000,
            )
        )
        entries = changes.get("entries", []) if isinstance(changes, dict) else []
        allowed = {str(row.get("path")) for row in entries if isinstance(row, dict)}
        if path not in allowed:
            raise ValueError("Only a changed file from the current task can be previewed.")
        return self._preview(path)

    def preview_artifact(self, path: str, *, goal_id: str = "") -> dict[str, Any]:
        goal = self._preview_goal(goal_id)
        if goal is None or path not in goal.artifacts:
            raise ValueError("Only a recorded artifact from the selected task can be previewed.")
        return self._preview(path)

    def _preview_goal(self, goal_id: str) -> Goal | None:
        if not goal_id:
            return self.store.read_current_goal()
        return next(
            (goal for goal in self.store.list_goals() if goal.id == goal_id),
            None,
        )

    def _preview(self, relative: str) -> dict[str, Any]:
        requested = Path(relative)
        if requested.is_absolute():
            raise ValueError("Preview path must be relative to the workspace.")
        lexical = self.project_dir / requested
        component = self.project_dir
        for part in requested.parts:
            component /= part
            if component.is_symlink():
                raise ValueError("Preview file is unavailable.")
        path = lexical.resolve()
        try:
            path.relative_to(self.project_dir)
        except ValueError as exc:
            raise ValueError("Preview path is outside the workspace.") from exc
        if _sensitive_preview_path(path, self.project_dir):
            raise ValueError("Preview file is unavailable.")
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Preview file is unavailable.")
                if info.st_size > 1_000_000:
                    raise ValueError("Preview file is larger than 1 MB.")
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    raw = handle.read(1_000_001)
            finally:
                os.close(descriptor)
            if len(raw) > 1_000_000:
                raise ValueError("Preview file is larger than 1 MB.")
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Binary files cannot be previewed.") from exc
        except OSError as exc:
            raise ValueError("Preview file is unavailable.") from exc
        return {
            "path": requested.as_posix(),
            "content": redact_secrets(content),
            "truncated": False,
        }

    def continue_task(self, feedback: str = "") -> dict[str, Any]:
        with self._config_lock:
            return self._continue_task_locked(feedback)

    def _continue_task_locked(self, feedback: str = "") -> dict[str, Any]:
        goal = self.store.read_current_goal()
        if goal is None:
            return self._blocked_task("There is no task to continue.", action="new_task")
        if self._thread is not None and self._thread.is_alive():
            return self._task(goal)
        try:
            supervisor = build_supervisor(
                self.project_dir,
                review_commands=_review_commands_from_goal(goal),
                api_key=self.api_key,
                cancel_requested=self._cancel.is_set,
            )
            if goal.status is GoalStatus.FAILED:
                goal = supervisor.restart()
            with supervisor.store.autonomy_locked():
                with supervisor.store.locked():
                    goal = supervisor.store.read_current_goal()
                    if goal is None:
                        raise HarnessError("There is no task to continue.")
                    autonomy = goal.metadata.get("autonomy")
                    if isinstance(autonomy, dict):
                        autonomy["status"] = "continuing"
                        autonomy["operator_intervention_required"] = False
                        autonomy["blocker"] = {
                            "signature": "",
                            "consecutive_count": 0,
                            "reason": "",
                        }
                    goal.metadata["continuation_feedback"] = feedback.strip()
                    goal.metadata.pop("cancelled", None)
                    goal.error = None
                    supervisor.store.write_goal(goal)
            self._cancel.clear()
            self._start_thread(_review_commands_from_goal(goal))
            return self._task(goal)
        except (ConfigError, HarnessError, OSError, ValueError) as exc:
            return self._blocked_task(str(exc), action="setup")

    def accept(self) -> dict[str, Any]:
        goal = self.store.read_current_goal()
        if goal is None:
            return self._blocked_task("There is no task to accept.", action="new_task")
        if goal.status is GoalStatus.DONE and goal.metadata.get("accepted") is True:
            return self._task(goal)
        return self._blocked_task(
            "This task cannot be accepted until independent verification passes.",
            action="view_checks",
        )

    def stop(self) -> dict[str, Any]:
        goal = self.store.read_current_goal()
        if goal is None:
            return self._ready_task()
        self._cancel.set()
        if self._thread is None or not self._thread.is_alive():
            self._mark_cancelled()
            return self.status()
        task = self._task(goal)
        task["status"] = "stopping"
        task["status_label"] = "Stopping safely"
        task["summary"] = "The current tool step will finish, then the task will stop."
        task["allowed_actions"] = []
        return task

    def _drive(self, review_commands: list[list[str]]) -> None:
        try:
            with self._config_lock:
                supervisor = build_supervisor(
                    self.project_dir,
                    review_commands=review_commands,
                    api_key=self.api_key,
                    cancel_requested=self._cancel.is_set,
                )
                policy = self.policy or autonomy_policy_from_config(
                    load_config(self.project_dir)
                )
            runner = AutonomousRunner(
                supervisor,
                policy=policy,
                cancel_requested=self._cancel.is_set,
            )
            while not self._cancel.is_set():
                goal = runner.step()
                autonomy = goal.metadata.get("autonomy")
                intervention = (
                    isinstance(autonomy, dict)
                    and autonomy.get("operator_intervention_required") is True
                )
                if goal.status.is_terminal or intervention:
                    self._ensure_terminal_report(goal)
                    return
        except StateLockError:
            # Another live driver owns this project. Its durable state remains authoritative.
            return
        except Exception as exc:
            self._record_driver_error(exc)
        finally:
            if self._cancel.is_set():
                self._mark_cancelled()

    def _start_thread(self, review_commands: list[list[str]]) -> None:
        with self._thread_lock:
            self._thread = Thread(
                target=self._drive,
                args=(review_commands,),
                name="agentic-harness-resume",
                daemon=True,
            )
            self._thread.start()

    def _mark_cancelled(self) -> None:
        try:
            with self.store.locked():
                goal = self.store.read_current_goal()
                if goal is None or goal.status is GoalStatus.DONE:
                    return
                goal.metadata["cancelled"] = True
                goal.error = "stopped by user"
                autonomy = goal.metadata.get("autonomy")
                if isinstance(autonomy, dict):
                    autonomy["status"] = "stopped"
                    autonomy["operator_intervention_required"] = False
                if goal.status in {
                    GoalStatus.PENDING,
                    GoalStatus.PLANNING,
                    GoalStatus.IN_PROGRESS,
                    GoalStatus.REVIEW,
                }:
                    goal.transition(GoalStatus.FAILED, reason="stopped by user")
                self.store.write_goal(goal)
        except Exception:
            return

    def _record_driver_error(self, exc: Exception) -> None:
        try:
            with self.store.locked():
                goal = self.store.read_current_goal()
                if goal is None:
                    return
                goal.error = f"background task driver failed: {type(exc).__name__}: {exc}"
                autonomy = goal.metadata.get("autonomy")
                if isinstance(autonomy, dict):
                    autonomy["status"] = "blocked"
                    autonomy["operator_intervention_required"] = True
                if goal.status in {
                    GoalStatus.PENDING,
                    GoalStatus.PLANNING,
                    GoalStatus.IN_PROGRESS,
                    GoalStatus.REVIEW,
                }:
                    goal.transition(GoalStatus.FAILED, reason="background task driver failed")
                self.store.write_goal(goal)
        except Exception:
            return

    def _config(self) -> HarnessConfig:
        path = self.project_dir / CONFIG_DIR / CONFIG_NAME
        if not path.exists():
            raise ConfigError("This workspace has not been set up yet.")
        config = load_config(self.project_dir)
        if config.worker == "noop":
            raise ConfigError("Choose an execution method before starting work.")
        return config

    def _task(self, goal: Goal) -> dict[str, Any]:
        autonomy = goal.metadata.get("autonomy")
        if not isinstance(autonomy, dict):
            autonomy = {}
        status = _task_status(goal, autonomy)
        terminal = goal.status.is_terminal and status in {"done", "blocked", "stopped"}
        if terminal:
            goal = self._ensure_terminal_report(goal)
            autonomy = goal.metadata.get("autonomy")
            if not isinstance(autonomy, dict):
                autonomy = {}
            status = _task_status(goal, autonomy)
            terminal = goal.status.is_terminal and status in {
                "done",
                "blocked",
                "stopped",
            }
        outcome = goal.metadata.get("worker_outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        plan_value = autonomy.get("plan")
        plan: list[Any] = plan_value if isinstance(plan_value, list) else []
        requirements_value = autonomy.get("requirements")
        requirements: list[Any] = (
            requirements_value if isinstance(requirements_value, list) else []
        )
        events = TaskEventStore(self.project_dir, goal.id).read()
        frozen_changes = goal.metadata.get("terminal_workspace_changes")
        changes = (
            frozen_changes
            if terminal and isinstance(frozen_changes, dict)
            else workspace_change_summary(
                self.project_dir,
                goal.metadata.get("workspace_snapshot")
                if isinstance(goal.metadata.get("workspace_snapshot"), dict)
                else None,
                limit=100,
            )
        )
        changed_files = changes.get("entries", []) if isinstance(changes, dict) else []
        verification = _verification(goal, outcome)
        report_ready = not terminal or self._terminal_report_ready(goal)
        if terminal and not report_ready:
            status = "checking"
        progress = _progress(status, plan, requirements)
        blocker = autonomy.get("blocker")
        blocker_reason = (
            str(blocker.get("reason") or "") if isinstance(blocker, dict) else ""
        )
        trusted_error = (
            str(goal.error or blocker_reason) if status in {"blocked", "stopped"} else ""
        )
        summary = str(
            trusted_error
            or outcome.get("summary")
            or goal.metadata.get("worker_summary")
            or goal.error
            or goal.objective
        )
        if terminal and not report_ready:
            summary = "Finalizing the durable terminal report."
        return {
            "contract": GUI_TASK_CONTRACT,
            "id": goal.id,
            "human_title": goal.objective[:100],
            "objective": goal.objective,
            "status": status,
            "status_label": _status_label(status),
            "summary": summary,
            "needs_human": status in {"blocked", "needs_review"},
            "progress": progress,
            "current": {
                "cycle": int(autonomy.get("cycle") or 0),
                "current_subgoal": str(autonomy.get("current_subgoal") or "Understand the goal"),
                "checkpoint": str(autonomy.get("checkpoint") or "goal_started"),
                "last_event_at": str(events[-1].get("at") or "") if events else "",
            },
            "plan": plan,
            "requirements": requirements,
            "events": events[-100:],
            "changed_files": changed_files,
            "verification": verification,
            "artifacts": [{"name": Path(path).name, "path": path} for path in goal.artifacts],
            "allowed_actions": _allowed_actions(status),
            "safety": _public_safety(goal),
            "final_result": {
                "accepted": goal.metadata.get("accepted") is True and status == "done",
                "summary": summary if status == "done" else "",
                "what_changed": changed_files,
                "checks": verification,
                "remaining": _remaining(requirements),
            },
            "metadata": {
                "created_at": goal.created_at,
                "updated_at": goal.updated_at,
                "observed_at": goal.updated_at,
                "worker": self._public_worker(),
                "budget": autonomy.get("budget") if isinstance(autonomy.get("budget"), dict) else {},
            },
        }

    def _ensure_terminal_report(self, goal: Goal) -> Goal:
        if not goal.status.is_terminal:
            return goal
        try:
            with self.store.locked():
                marker = self.store.read_current_goal()
                if marker is not None and marker.id == goal.id:
                    current = marker
                    is_current = True
                else:
                    current = self.store.read_goal(goal.id)
                    is_current = False
                if not current.status.is_terminal:
                    return current
                desired_state = _terminal_report_state_sha256(
                    self.project_dir,
                    current,
                )
                existing_report = _terminal_report_path(self.project_dir, current)
                existing_content = _read_report(existing_report)
                workspace_state = str(
                    current.metadata.get("terminal_workspace_state_sha256") or ""
                )
                frozen_changes = current.metadata.get("terminal_workspace_changes")
                if not isinstance(frozen_changes, dict) or (
                    workspace_state and workspace_state != desired_state
                ):
                    parsed = _workspace_changes_from_report(existing_content)
                    if parsed is not None and not workspace_state:
                        frozen_changes = parsed
                    elif is_current:
                        frozen_changes = workspace_change_summary(
                            self.project_dir,
                            current.metadata.get("workspace_snapshot")
                            if isinstance(current.metadata.get("workspace_snapshot"), dict)
                            else None,
                            limit=500,
                        )
                    else:
                        frozen_changes = {
                            "total": 0,
                            "entries": [],
                            "omitted": 0,
                            "truncated": True,
                            "historical_evidence_unavailable": True,
                        }
                    current.metadata["terminal_workspace_changes"] = frozen_changes
                current.metadata["terminal_workspace_state_sha256"] = desired_state
                if self._terminal_report_ready(current):
                    self.store.write_goal(current, make_current=is_current)
                    return current
                if (
                    existing_content
                    and not current.metadata.get("terminal_report_state_sha256")
                    and _legacy_report_matches(current, existing_content)
                ):
                    current.metadata["terminal_report_state_sha256"] = desired_state
                    if existing_report is None:
                        return current
                    current.metadata["terminal_report_content_sha256"] = hashlib.sha256(
                        existing_report.read_bytes()
                    ).hexdigest()
                    self.store.write_goal(current, make_current=is_current)
                    return current
                content = _terminal_report_content(self.project_dir, current)
                report_path = self.store.write_report(current, content)
                current.metadata["terminal_report_state_sha256"] = desired_state
                current.metadata["terminal_report_content_sha256"] = hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest()
                self.store.write_goal(current, make_current=is_current)
                return current
        except (OSError, StateLockError, ValueError):
            return goal

    def _terminal_report_ready(self, goal: Goal) -> bool:
        if not goal.status.is_terminal:
            return False
        state_digest = str(goal.metadata.get("terminal_report_state_sha256") or "")
        content_digest = str(goal.metadata.get("terminal_report_content_sha256") or "")
        if (
            not state_digest
            or state_digest != _terminal_report_state_sha256(self.project_dir, goal)
            or not content_digest
        ):
            return False
        for value in goal.artifacts:
            if Path(value).name != "report.md":
                continue
            candidate = (self.project_dir / value).resolve()
            try:
                candidate.relative_to(self.project_dir)
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if hashlib.sha256(candidate.read_bytes()).hexdigest() == content_digest:
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _ready_task(self) -> dict[str, Any]:
        readiness = self.readiness()
        return {
            "contract": GUI_TASK_CONTRACT,
            "id": "",
            "human_title": "No active task",
            "objective": "",
            "status": "ready" if readiness["can_start"] else "blocked",
            "status_label": "Ready" if readiness["can_start"] else "Setup needed",
            "summary": str(readiness["summary"]),
            "needs_human": not bool(readiness["can_start"]),
            "progress": {"determinate": False, "percent": None},
            "current": {"cycle": 0, "current_subgoal": "", "checkpoint": "", "last_event_at": ""},
            "plan": [],
            "requirements": [],
            "events": [],
            "changed_files": [],
            "verification": [],
            "artifacts": [],
            "allowed_actions": [{"action": "new_task", "enabled": True}]
            if readiness["can_start"]
            else [{"action": "setup", "enabled": True}],
            "safety": {},
            "final_result": {"accepted": False, "summary": "", "what_changed": [], "checks": [], "remaining": []},
            "metadata": {"workspace": str(self.project_dir)},
        }

    def _blocked_task(self, summary: str, *, action: str) -> dict[str, Any]:
        task = self._ready_task()
        task.update(
            {
                "status": "blocked",
                "status_label": "Needs attention",
                "summary": summary,
                "needs_human": True,
                "allowed_actions": [{"action": action, "enabled": True}],
            }
        )
        return task

    def _public_worker(self) -> dict[str, Any]:
        try:
            config = self._config()
        except ConfigError:
            return {"type": "unconfigured"}
        worker: dict[str, Any] = {"type": config.worker}
        if config.worker == "model_agent":
            if config.llm_credential_source == "session":
                credential_source = "session"
            elif config.llm_credential_source == "env":
                credential_source = f"env:{config.llm_api_key_env}"
            else:
                credential_source = "none"
            worker.update(
                {
                    "model": config.llm_model,
                    "endpoint": config.llm_endpoint,
                    "credential_source": credential_source,
                }
            )
        return worker

    def _worker_label(self, config: HarnessConfig) -> str:
        if config.worker == "model_agent":
            return f"model {config.llm_model}"
        if config.worker == "coding_agent":
            return "the installed coding agent"
        return config.worker.replace("_", " ")


def _safe_areas(value: Any, project_dir: Path) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        path = Path(text)
        if path.is_absolute():
            raise ValueError("allowed paths must be relative to the workspace")
        resolved = (project_dir / path).resolve()
        try:
            resolved.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError(f"allowed path is outside the workspace: {text}") from exc
        result.append(path.as_posix())
    return result


def _checks(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    commands: list[list[str]] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        command = split_command(text)
        if not command:
            continue
        commands.append(command)
    return commands


def _sensitive_preview_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    parts = {part.lower() for part in relative.parts}
    name = path.name.lower()
    dotenv = name == ".env" or name == ".envrc" or name.startswith((".env.", ".env-"))
    protected_names = {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "_netrc",
        "application_default_credentials.json",
        "client_secret.json",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
        "token.json",
        "tokens.json",
        "oauth_token.json",
        "auth_token.json",
    }
    oauth_variant = (
        name.startswith("client_secret") and name.endswith(".json")
    ) or (
        name.startswith(("oauth_token", "auth_token")) and name.endswith(".json")
    )
    return (
        dotenv
        or bool(parts & {".aws", ".azure", ".docker", ".git", ".gnupg", ".kube", ".ssh"})
        or oauth_variant
        or name in protected_names
        or path.suffix.lower() in {".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
    )


def _task_status(goal: Goal, autonomy: dict[str, Any]) -> str:
    if goal.metadata.get("cancelled") is True:
        return "stopped"
    if goal.status is GoalStatus.DONE:
        return "done"
    if autonomy.get("operator_intervention_required") is True:
        return "blocked"
    if goal.status is GoalStatus.FAILED:
        if autonomy.get("contract") == AUTONOMY_CONTRACT:
            return "checking"
        return "blocked"
    if goal.status is GoalStatus.REVIEW:
        return "checking"
    if goal.status is GoalStatus.PLANNING:
        return "starting"
    return "working"


def _status_label(status: str) -> str:
    return {
        "ready": "Ready",
        "starting": "Starting",
        "working": "Working",
        "checking": "Checking work",
        "needs_review": "Needs review",
        "done": "Done",
        "blocked": "Needs attention",
        "stopped": "Stopped",
    }.get(status, "Working")


def _progress(status: str, plan: list[Any], requirements: list[Any]) -> dict[str, Any]:
    if status == "done":
        return {"determinate": True, "completed": 1, "total": 1, "percent": 100}
    rows = requirements if requirements else plan
    if not rows:
        return {"determinate": False, "percent": None}
    completed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = str(row.get("status") or "").lower()
        if state in {"satisfied", "complete", "completed", "done"}:
            completed += 1
    total = len(rows)
    return {
        "determinate": True,
        "completed": completed,
        "total": total,
        "percent": int(completed * 100 / total) if total else None,
    }


def _verification(goal: Goal, outcome: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    review = goal.review if isinstance(goal.review, dict) else {}
    criteria = review.get("criteria")
    if isinstance(criteria, list):
        for row in criteria:
            if not isinstance(row, dict):
                continue
            results.append(
                {
                    "name": str(row.get("name") or "Verification"),
                    "passed": row.get("passed") is True,
                    "message": str(row.get("message") or ""),
                    "independent": row.get("independent") is True,
                }
            )
    worker_checks = outcome.get("verification")
    if isinstance(worker_checks, list):
        for row in worker_checks:
            if isinstance(row, dict):
                results.append(
                    {
                        "name": str(row.get("label") or row.get("id") or "Worker check"),
                        "passed": row.get("passed") is True,
                        "message": str(row.get("message") or ""),
                        "independent": False,
                    }
                )
    return results


def _allowed_actions(status: str) -> list[dict[str, Any]]:
    if status == "done":
        return [{"action": "new_task", "enabled": True}]
    if status == "blocked":
        return [
            {"action": "continue", "enabled": True},
            {"action": "stop", "enabled": True},
        ]
    if status in {"starting", "working", "checking"}:
        return [{"action": "stop", "enabled": True}]
    return [{"action": "new_task", "enabled": True}]


def _public_safety(goal: Goal) -> dict[str, Any]:
    safety = goal.metadata.get("safety")
    if not isinstance(safety, dict):
        return {}
    return {
        "allowed_paths": list(safety.get("allowed_paths") or []),
        "checks": [
            {"id": row.get("id"), "label": row.get("label")}
            for row in safety.get("checks", [])
            if isinstance(row, dict)
        ],
        "path_enforcement": safety.get("path_enforcement") is True,
    }


def _remaining(requirements: list[Any]) -> list[str]:
    remaining: list[str] = []
    for row in requirements:
        if not isinstance(row, dict) or str(row.get("status") or "").lower() == "satisfied":
            continue
        remaining.append(str(row.get("text") or row.get("id") or "Unfinished requirement"))
    return remaining


def _terminal_report_state_sha256(project_dir: Path, goal: Goal) -> str:
    payload = goal.to_dict()
    payload.pop("updated_at", None)
    payload["artifacts"] = [
        path for path in goal.artifacts if Path(path).name != "report.md"
    ]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in (
            "terminal_report_state_sha256",
            "terminal_report_content_sha256",
            "terminal_workspace_state_sha256",
            "terminal_workspace_changes",
        ):
            metadata.pop(key, None)
    payload["events"] = TaskEventStore(project_dir, goal.id).read()
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _terminal_report_path(project_dir: Path, goal: Goal) -> Path | None:
    for value in goal.artifacts:
        if Path(value).name != "report.md":
            continue
        candidate = (project_dir / value).resolve()
        try:
            candidate.relative_to(project_dir)
        except ValueError:
            continue
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _read_report(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _legacy_report_matches(goal: Goal, content: str) -> bool:
    if f"Status: {goal.status.value}" not in content:
        return False
    accepted = goal.metadata.get("accepted") is True
    if "Accepted: yes" in content and not accepted:
        return False
    if "Accepted: no" in content and accepted:
        return False
    return True


def _workspace_changes_from_report(content: str) -> dict[str, Any] | None:
    total: int | None = None
    entries: list[dict[str, str]] = []
    omitted = 0
    truncated = False
    for line in content.splitlines():
        if line.startswith("Changed: "):
            value = line.removeprefix("Changed: ").split(" ", 1)[0]
            try:
                total = int(value)
            except ValueError:
                return None
            continue
        if not line.startswith("- "):
            continue
        detail = line[2:]
        status, separator, path = detail.partition(" ")
        if status in {"added", "modified", "deleted"} and separator and path:
            entries.append({"status": status, "path": path})
        elif detail.startswith("... ") and detail.endswith(" more"):
            try:
                omitted = int(detail[4:-5])
            except ValueError:
                omitted = 0
        elif detail == "note: workspace snapshot was capped":
            truncated = True
    if total is None:
        return None
    return {
        "total": total,
        "entries": entries,
        "omitted": omitted,
        "truncated": truncated,
    }


def _terminal_report_content(project_dir: Path, goal: Goal) -> str:
    autonomy = goal.metadata.get("autonomy")
    if not isinstance(autonomy, dict):
        autonomy = {}
    outcome = goal.metadata.get("worker_outcome")
    if not isinstance(outcome, dict):
        outcome = {}
    summary = str(
        outcome.get("summary")
        or goal.metadata.get("worker_summary")
        or goal.error
        or "No summary was reported."
    )
    lines = [
        "# Agentic Harness Report",
        "",
        f"- Goal: {goal.id}",
        f"- Objective: {goal.objective}",
        f"- Status: {goal.status.value}",
        f"- Accepted: {'yes' if goal.metadata.get('accepted') is True else 'no'}",
        f"- Summary: {summary}",
        f"- Current subgoal: {autonomy.get('current_subgoal') or 'not reported'}",
        f"- Checkpoint: {autonomy.get('checkpoint') or 'not reported'}",
        f"- Cycles: {int(autonomy.get('cycle') or 0)}",
        "",
        "## Plan",
        "",
    ]
    plan = autonomy.get("plan")
    if isinstance(plan, list) and plan:
        for row in plan:
            if isinstance(row, dict):
                lines.append(
                    f"- [{row.get('status') or 'pending'}] {row.get('step') or row.get('text') or 'Plan item'}"
                )
    else:
        lines.append("- No structured plan was reported.")
    lines.extend(["", "## Requirements", ""])
    requirements = autonomy.get("requirements")
    if isinstance(requirements, list) and requirements:
        for row in requirements:
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence")
            evidence_text = ", ".join(str(item) for item in evidence) if isinstance(evidence, list) else ""
            lines.append(
                f"- [{row.get('status') or 'pending'}] {row.get('text') or row.get('id') or 'Requirement'}"
                + (f" — evidence: {evidence_text}" if evidence_text else "")
            )
    else:
        lines.append("- No structured requirements were reported.")
    lines.extend(["", "## Verification", ""])
    verification = _verification(goal, outcome)
    if verification:
        for row in verification:
            scope = "independent" if row.get("independent") else "worker"
            result = "passed" if row.get("passed") else "failed"
            lines.append(
                f"- {result} ({scope}): {row.get('message') or row.get('name') or 'Verification'}"
            )
    else:
        lines.append("- No verification evidence was recorded.")
    frozen_changes = goal.metadata.get("terminal_workspace_changes")
    changes = (
        frozen_changes
        if isinstance(frozen_changes, dict)
        else workspace_change_summary(
            project_dir,
            goal.metadata.get("workspace_snapshot")
            if isinstance(goal.metadata.get("workspace_snapshot"), dict)
            else None,
            limit=500,
        )
    )
    lines.extend(["", "## Changed files", ""])
    entries = changes.get("entries") if isinstance(changes, dict) else None
    if isinstance(entries, list) and entries:
        for row in entries:
            if isinstance(row, dict):
                lines.append(f"- {row.get('status') or 'changed'}: {row.get('path') or 'unknown'}")
    else:
        lines.append("- No workspace file changes were recorded.")
    events = TaskEventStore(project_dir, goal.id).read()
    lines.extend(["", "## Activity evidence", ""])
    if events:
        for event in events:
            lines.append(
                f"- {event.get('evidence_id') or 'event'}: {event.get('summary') or 'Progress recorded'}"
            )
    else:
        lines.append("- No task events were recorded.")
    return "\n".join(lines) + "\n"


def _review_commands_from_goal(goal: Goal) -> list[list[str]]:
    safety = goal.metadata.get("safety")
    if not isinstance(safety, dict) or not isinstance(safety.get("checks"), list):
        return []
    commands: list[list[str]] = []
    for row in safety["checks"]:
        if not isinstance(row, dict):
            continue
        argv = row.get("argv")
        if isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv):
            commands.append(list(argv))
    return commands


def _detect_check(project_dir: Path) -> str:
    return format_command(detect_review_command(project_dir))


def _coding_agent_command(agent: str) -> list[str]:
    commands = {
        "codex": ["codex", "exec", "--skip-git-repo-check", "{objective}"],
        "opencode": ["opencode", "run", "{objective}"],
        "aider": ["aider", "--yes-always", "--message", "{objective}"],
        "codewhale": ["codewhale", "exec", "{objective}"],
    }
    if agent not in commands:
        raise ValueError("Choose codex, opencode, aider, or codewhale.")
    return commands[agent]


def _int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    parsed = default if value in (None, "") else int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"setting must be between {minimum} and {maximum}")
    return parsed


def _autonomy_settings(body: dict[str, Any]) -> dict[str, int]:
    return {
        "max_cycles": _int_setting(body.get("max_cycles"), 100, 1, 10_000),
        "max_elapsed_seconds": _int_setting(
            body.get("max_elapsed_seconds"), 7_200, 1, 604_800
        ),
        "max_total_tokens": _int_setting(
            body.get("max_total_tokens"), 500_000, 1, 100_000_000
        ),
        "max_provider_calls": _int_setting(
            body.get("max_provider_calls"), 200, 1, 100_000
        ),
        "max_tool_calls": _int_setting(body.get("max_tool_calls"), 1_000, 1, 1_000_000),
    }
