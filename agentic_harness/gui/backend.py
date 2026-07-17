"""Portable GUI execution backend built on the public harness engine."""

from __future__ import annotations

from copy import deepcopy
from http.client import HTTPConnection
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock, RLock, Thread
from typing import Any

import yaml

from agentic_harness.adapters.model_agent import (
    OpenAICompatibleProvider,
    validate_report_outcome,
)
from agentic_harness.core.artifacts import ArtifactStore
from agentic_harness.core.autonomy import AUTONOMY_CONTRACT, AutonomousRunner, AutonomyPolicy
from agentic_harness.core.config import (
    CONFIG_DIR,
    CONFIG_NAME,
    HarnessConfig,
    detect_review_command,
    load_config,
)
from agentic_harness.core.demos import create_demo
from agentic_harness.core.errors import ConfigError, HarnessError, StateLockError
from agentic_harness.core.events import TaskEventStore
from agentic_harness.core.factory import autonomy_policy_from_config, build_supervisor
from agentic_harness.core.state import Goal, GoalStatus
from agentic_harness.core.specification_amendment import amended_requirements
from agentic_harness.core.providers import (
    PROVIDER_TEMPLATES,
    ProviderProfile,
    resolve_api_key,
)
from agentic_harness.core.presentation import safe_inline_text
from agentic_harness.core.redaction import redact_secrets
from agentic_harness.core.reporting import RunReceipt, build_run_receipt
from agentic_harness.core.safety import (
    command_uses_windows_shell,
    format_command,
    goal_safety_metadata,
    resolve_command_executable,
    resolve_executable,
    split_command,
)
from agentic_harness.core.secure_io import write_private_text
from agentic_harness.core.strategies import (
    DEFAULT_PUBLIC_STRATEGY,
    ExecutionStrategy,
    policy_for_strategy,
    strategy_by_key,
    strategy_from_metadata,
)
from agentic_harness.core.workspace import workspace_change_summary


GUI_TASK_CONTRACT = "agentic_harness.gui_task.v2"
TERMINAL_REPORT_CONTRACT = "agentic_harness.terminal_report.v2"

_CODING_AGENT_COMMANDS = {
    "codex": ["codex", "exec", "--skip-git-repo-check", "{objective}"],
    "codewhale": ["codewhale", "exec", "{objective}"],
    "opencode": ["opencode", "run", "{objective}"],
    "aider": ["aider", "--yes-always", "--message", "{objective}"],
}
_CODING_AGENT_LABELS = {
    "codex": "Codex",
    "codewhale": "CodeWhale",
    "opencode": "OpenCode",
    "aider": "Aider",
}

_LOCAL_MODEL_PROBES = (
    {
        "template_key": "ollama_local",
        "label": "Ollama",
        "port": 11434,
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    },
    {
        "template_key": "lm_studio_local",
        "label": "LM Studio",
        "port": 1234,
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
    },
    {
        "template_key": "vllm_local",
        "label": "vLLM",
        "port": 8000,
        "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
    },
    {
        "template_key": "llamacpp_local",
        "label": "llama.cpp",
        "port": 8080,
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
    },
)

_DEMO_OBJECTIVE = "Fix the deliberately broken calculator and prove that adding 2 and 3 returns 5."
_DEMO_CHECK = "from calculator import add; assert add(2, 3) == 5"
_DEMO_CHECK_LABEL = format_command(["python", "-c", _DEMO_CHECK])
_ACTIVE_DEMO_STATUSES = frozenset({"starting", "working", "checking", "stopping", "needs_review"})
_GUI_DEMO_WORKER = '''"""Scripted practice worker for the isolated GUI demo."""

from __future__ import annotations

import json
import os
from pathlib import Path


goal_id = os.environ.get("AGENTIC_HARNESS_GOAL_ID", "practice-goal").strip()
attempt_path = Path(".agentic-harness") / "runs" / goal_id / "demo-worker-attempt"
attempt_path.parent.mkdir(parents=True, exist_ok=True)
attempt = int(attempt_path.read_text(encoding="utf-8") or "0") if attempt_path.exists() else 0
attempt_path.write_text(str(attempt + 1), encoding="utf-8")
calculator = Path("calculator.py")
if attempt == 0:
    summary = "claimed completion before repairing the deliberately broken calculator"
else:
    content = calculator.read_text(encoding="utf-8")
    calculator.write_text(
        content.replace("return left + right + 1", "return left + right"),
        encoding="utf-8",
    )
    summary = "repaired the calculator after the first claim was rejected"
outcome = {
    "status": "complete",
    "plan": [
        {"status": "complete", "step": "Inspect the deliberately broken calculator"},
        {"status": "complete", "step": "Repair the addition result"},
        {"status": "complete", "step": "Submit the result for independent verification"},
    ],
    "current_subgoal": "Prove the repaired calculator returns the expected result",
    "checkpoint": "repair_submitted_for_independent_review",
    "requirement_status": [
        {
            "id": "R1",
            "status": "satisfied",
            "evidence": ["review:1"],
        }
    ],
    "blockers": [],
    "summary": summary,
}
print(summary)
print("HARNESS_RESULT_JSON=" + json.dumps(outcome, separators=(",", ":")))
'''


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
        self._execution_validation: dict[str, Any] = {}
        self._demo_workspace: TemporaryDirectory[str] | None = None
        self._demo_backend: EmbeddedExecutionBackend | None = None
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
            if goal is None or _durably_terminal(goal):
                return
            config = self._config()
            if self._credential_status(config)["configured"] is not True:
                return
            review_commands = _review_commands_from_goal(goal)
            if not review_commands and config.review_command:
                review_commands = [config.review_command]
            if not review_commands:
                self._record_driver_error(
                    ConfigError("The interrupted task has no independent verification command.")
                )
                return
            self._start_thread(
                review_commands,
                self._policy_for_goal(config, goal),
            )
        except (ConfigError, HarnessError, OSError, ValueError):
            return

    def health(self) -> dict[str, Any]:
        readiness = self.readiness()
        return {
            "ok": True,
            "app": "agentic-harness",
            "backend": "embedded",
            "workspace": str(self.project_dir),
            "configured": readiness["state"] not in {"setup_required", "configuration_error"},
            "readiness": readiness,
        }

    def readiness(self) -> dict[str, Any]:
        with self._config_lock:
            return self._readiness_locked()

    def _readiness_locked(self) -> dict[str, Any]:
        demo_task = self._demo_status()
        if demo_task is not None and demo_task["status"] in _ACTIVE_DEMO_STATUSES:
            return {
                "state": "working",
                "label": "Safe demo running",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "The isolated practice task is still running.",
                "next_action": "Follow the demo progress and its independent verification.",
            }
        config, configuration_error = self._gui_config_state()
        if configuration_error is not None:
            return {
                "state": "configuration_error",
                "label": "Settings need repair",
                "can_start": False,
                "can_queue": False,
                "requires_review": True,
                "summary": str(configuration_error["summary"]),
                "next_action": (
                    "Repair or move .agentic-harness/config.yml, then refresh this page."
                ),
            }
        if config is None:
            if demo_task is not None and demo_task.get("result_category") == "verified_done":
                return {
                    "state": "setup_required",
                    "label": "Demo complete",
                    "can_start": False,
                    "can_queue": False,
                    "requires_review": False,
                    "summary": "The safe demo passed. Connect your own coding agent or model for real work.",
                    "next_action": "Open Settings and choose how real project tasks should run.",
                }
            return {
                "state": "setup_required",
                "label": "Setup needed",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "This project has not been set up yet.",
                "next_action": "Open Settings and choose how this project should run tasks.",
            }
        if not config.review_command:
            return {
                "state": "verification_required",
                "label": "Verification needed",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "Add an independent verification command before starting work.",
                "next_action": "Open Settings and choose the project check that proves the result.",
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
                "next_action": "Open Settings and enter the key again.",
            }
        current = self.store.read_current_goal()
        if self._driver_active() or (current is not None and not _durably_terminal(current)):
            return {
                "state": "working",
                "label": "Work in progress",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "The current task is still in progress.",
                "next_action": "Follow its progress or stop it before starting another task.",
            }
        if current is not None and _durably_terminal(current):
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
        if config.worker == "model_agent" and self._model_validation(config)["verified"] is not True:
            return {
                "state": "connection_test_required",
                "label": "AI test needed",
                "can_start": False,
                "can_queue": False,
                "requires_review": False,
                "summary": "The AI connection has not passed its structured-action test in this app session.",
                "next_action": "Open Settings and choose Save and test settings.",
            }
        summary = f"Ready to work with {self._worker_label(config)}."
        if config.worker == "coding_agent":
            validation = self._coding_agent_validation(config.coding_agent_command)
            summary = str(validation["summary"])
        return {
            "state": "ready",
            "label": "Ready",
            "can_start": True,
            "can_queue": True,
            "requires_review": False,
            "summary": summary,
            "next_action": "Describe one goal and start the task.",
        }

    def setup(self) -> dict[str, Any]:
        with self._config_lock:
            return self._setup_locked()

    def _setup_locked(self) -> dict[str, Any]:
        suggested_argv = detect_review_command(self.project_dir)
        suggested = format_command(suggested_argv)
        demo_task = self._demo_status()
        config, configuration_error = self._gui_config_state()
        bootstrap_config: HarnessConfig | None = None
        config_path = self.project_dir / CONFIG_DIR / CONFIG_NAME
        if configuration_error is None and config is None and config_path.is_file():
            candidate = load_config(self.project_dir)
            if candidate.worker == "noop":
                bootstrap_config = candidate
        agents, recommended_agent = _coding_agent_options(config)
        editable = configuration_error is None and (
            config is None or _gui_setup_editable(config)
        )
        result: dict[str, Any] = {
            "contract": "agentic_harness.gui_setup.v1",
            "workspace": str(self.project_dir),
            "configured": config is not None,
            "editable": editable,
            "suggested_check": suggested,
            "verification": {
                "mode": "automatic" if suggested_argv else "setup_needed",
                "label": _review_command_label(suggested_argv),
                "technical_command": suggested,
            },
            "deployment": {
                "scope": "local_self_hosted",
                "multi_user": False,
                "summary": "One trusted user and one workspace on this computer.",
            },
            "management": {
                "mode": "workspace",
                "editable": editable,
                "summary": "Settings for this project.",
            },
            "provider_templates": [template.to_public_dict() for template in PROVIDER_TEMPLATES],
            "demo": {
                "available": True,
                "kind": "scripted_practice",
                "model_used": False,
                "workspace": "isolated_temporary",
                "summary": (
                    "Runs the real harness in a temporary practice project with a scripted "
                    "worker. No API key, model server, or selected-workspace access is used."
                ),
                "state": (
                    str(demo_task.get("status") or "ready") if demo_task is not None else "ready"
                ),
            },
            "local_model_detection": {
                "status": "not_checked",
                "detected": [],
                "summary": (
                    "Automatic detection for Ollama, LM Studio, vLLM, and llama.cpp "
                    "is available on demand."
                ),
            },
            "execution_options": [
                {
                    "key": "coding_agent",
                    "label": "Installed coding agent",
                    "description": "Use Codex, OpenCode, Aider, or another configured CLI.",
                    "data_location": "depends_on_agent",
                    "available": bool(recommended_agent),
                    "recommended": bool(recommended_agent),
                    "recommended_agent": recommended_agent,
                    "agents": agents,
                },
                {
                    "key": "local_model",
                    "label": "Local model",
                    "description": "Use a tool-capable OpenAI-compatible model on this machine or LAN.",
                    "data_location": "local",
                    "available": True,
                    "recommended": False,
                },
                {
                    "key": "cloud_model",
                    "label": "Cloud model",
                    "description": "Use your chosen OpenAI-compatible cloud endpoint and model ID.",
                    "data_location": "remote",
                    "available": True,
                    "recommended": False,
                },
            ],
        }
        if configuration_error is not None:
            result["configuration_error"] = configuration_error
        if config is not None:
            result["worker"] = self._public_worker()
            result["credential"] = self._credential_status(config)
            result["verification_command"] = format_command(config.review_command)
            result["verification"] = {
                "mode": "configured",
                "label": _review_command_label(config.review_command),
                "technical_command": format_command(config.review_command),
            }
            result["assurance_mode"] = config.assurance_mode
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
                result["execution_validation"] = self._model_validation(config)
            elif config.worker == "coding_agent":
                result["execution_validation"] = self._coding_agent_validation(
                    config.coding_agent_command
                )
        elif bootstrap_config is not None:
            result["verification_command"] = format_command(
                bootstrap_config.review_command
            )
            result["verification"] = {
                "mode": "configured" if bootstrap_config.review_command else "setup_needed",
                "label": _review_command_label(bootstrap_config.review_command),
                "technical_command": format_command(bootstrap_config.review_command),
            }
            result["limits"] = {
                "max_cycles": bootstrap_config.goal_max_cycles,
                "max_elapsed_seconds": bootstrap_config.goal_max_elapsed_seconds,
                "max_total_tokens": bootstrap_config.goal_max_total_tokens,
                "max_provider_calls": bootstrap_config.goal_max_provider_calls,
                "max_tool_calls": bootstrap_config.goal_max_tool_calls,
            }
        return result

    def detect_local_models(self) -> dict[str, Any]:
        """Probe only fixed loopback endpoints for supported local model servers."""

        detected = [
            result
            for probe in _LOCAL_MODEL_PROBES
            if (result := _probe_local_model_server(probe)) is not None
        ]
        if detected:
            labels = ", ".join(
                f"{row['label']} ({row['model']})" if row["model"] else str(row["label"])
                for row in detected
            )
            summary = f"Detected on this computer: {labels}."
        else:
            summary = (
                "No running Ollama, LM Studio, vLLM, or llama.cpp server was detected. "
                "You can start one "
                "and check again, or enter another local OpenAI-compatible endpoint."
            )
        return {
            "status": "found" if detected else "not_found",
            "detected": detected,
            "summary": summary,
        }

    def start_demo(self) -> dict[str, Any]:
        """Start a credential-free practice run in an isolated temporary workspace."""

        with self._config_lock:
            current_demo = self._demo_status()
            if current_demo is not None and current_demo["status"] in _ACTIVE_DEMO_STATUSES:
                return current_demo
            current = self.store.read_current_goal()
            if self._driver_active() or (current is not None and not _durably_terminal(current)):
                return self._blocked_task(
                    "Stop or finish the current workspace task before starting the safe demo.",
                    action="view_current",
                )
            self._dispose_demo()
            workspace = TemporaryDirectory(prefix="agentic-harness-safe-demo-")
            demo_path = Path(workspace.name).resolve()
            demo_path.chmod(0o700)
            create_demo("fix-tests", demo_path)
            (demo_path / "mock_coding_agent.py").write_text(
                _GUI_DEMO_WORKER,
                encoding="utf-8",
            )
            demo = EmbeddedExecutionBackend(demo_path)
            demo._write_config(
                {
                    "version": 1,
                    "worker": {
                        "type": "coding_agent",
                        "coding_agent_command": [
                            sys.executable,
                            str(demo_path / "mock_coding_agent.py"),
                        ],
                        "coding_agent_timeout": 120,
                        "coding_agent_transcript": (
                            ".agentic-harness/runs/{goal_id}/demo-worker.log"
                        ),
                    },
                    "review_command": [sys.executable, "-c", _DEMO_CHECK],
                    "review_command_timeout": 30,
                    "autonomy": {
                        "max_cycles": 4,
                        "max_elapsed_seconds": 120,
                        "max_total_tokens": 1_000,
                        "max_provider_calls": 1,
                        "max_tool_calls": 20,
                    },
                }
            )
            self._demo_workspace = workspace
            self._demo_backend = demo
            task = demo.start(
                {
                    "objective": _DEMO_OBJECTIVE,
                    "strategy": "quick",
                    "safe_areas": ["calculator.py"],
                }
            )
            return _tag_demo_task(task)

    def _demo_status(self) -> dict[str, Any] | None:
        if self._demo_backend is None:
            return None
        return _tag_demo_task(self._demo_backend.status())

    def demo_status(self) -> dict[str, Any] | None:
        """Return the isolated practice task without exposing its temporary path."""

        with self._config_lock:
            return self._demo_status()

    def clear_demo(self) -> None:
        """Dismiss a terminal practice task and return to the real workspace."""

        with self._config_lock:
            current = self._demo_status()
            if current is not None and current.get("status") in _ACTIVE_DEMO_STATUSES:
                raise ValueError("Wait for the safe demo to finish before returning to real work.")
            self._dispose_demo()

    def _dispose_demo(self) -> None:
        workspace = self._demo_workspace
        self._demo_backend = None
        self._demo_workspace = None
        if workspace is not None:
            workspace.cleanup()

    def configure(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._config_lock:
            return self._configure_locked(body)

    def _configure_locked(self, body: dict[str, Any]) -> dict[str, Any]:
        existing, configuration_error = self._gui_config_state()
        if configuration_error is not None:
            raise ValueError(
                f"{configuration_error['summary']} The existing "
                ".agentic-harness/config.yml was not changed."
            )
        config_path = self.project_dir / CONFIG_DIR / CONFIG_NAME
        if existing is None and config_path.is_file():
            candidate = load_config(self.project_dir)
            if candidate.worker == "noop":
                existing = candidate
        if existing is not None and not _gui_setup_editable(existing):
            raise ValueError(f"The existing {existing.worker} setup is read-only in the GUI.")
        execution = str(body.get("execution") or "").strip()
        verification_text = str(
            body.get("verification_command")
            or (format_command(existing.review_command) if existing else "")
            or _detect_check(self.project_dir)
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
                raise ValueError(
                    "Choose either a session API key or an environment variable, not both."
                )
            existing_model = (
                existing if existing is not None and existing.worker == "model_agent" else None
            )
            preserve_session = (
                not entered_key
                and not profile.api_key_env
                and existing_model is not None
                and existing_model.llm_credential_source == "session"
                and existing_model.llm_endpoint == profile.endpoint
                and existing_model.llm_model == profile.model
            )
            source = (
                "session"
                if entered_key or preserve_session
                else ("env" if profile.api_key_env else "none")
            )
            payload = {
                "version": 1,
                "worker": "model_agent",
                "assurance_mode": _assurance_setting(body, existing),
                "llm": {
                    "endpoint": profile.endpoint,
                    "model": profile.model,
                    "api_key_env": profile.api_key_env,
                    "remote_data_confirmed": profile.data_location == "cloud",
                    "max_steps": _int_setting(
                        body.get("max_steps"),
                        existing_model.llm_max_steps if existing_model else 8,
                        1,
                        50,
                    ),
                    "timeout": _int_setting(
                        body.get("timeout"),
                        existing_model.llm_timeout if existing_model else 120,
                        1,
                        3_600,
                    ),
                },
                "llm_retries": _int_setting(
                    body.get("retries"),
                    existing_model.llm_retries if existing_model else 2,
                    0,
                    100,
                ),
                "llm_retry_delay": _float_setting(
                    body.get("retry_delay"),
                    existing_model.llm_retry_delay if existing_model else 1.0,
                    0.0,
                    3_600.0,
                ),
                "llm_credential_source": source,
                "review_command": verification,
                "review_command_timeout": _int_setting(
                    body.get("verification_timeout"),
                    existing.review_command_timeout if existing else 300,
                    1,
                    3_600,
                ),
                **_preserved_configuration_settings(existing),
                "autonomy": _autonomy_settings(body, existing),
            }
            session_key = entered_key or (self.api_key if preserve_session else None)
            self._commit_configuration(payload, session_key)
            return {
                "configured": True,
                "provider": profile.to_public_dict(),
                "credential": self._credential_status(load_config(self.project_dir)),
                "verification_command": verification_text,
            }
        if execution == "coding_agent":
            agent = str(body.get("agent") or "").strip().lower()
            command = _existing_coding_agent_command(self.project_dir, agent)
            resolved = resolve_executable(agent) if agent != "current" else None
            if command is None and resolved is None:
                raise ValueError(f"{agent} is not available on PATH.")
            if command is None:
                command = _coding_agent_command(agent, executable=resolved)
            existing_coding_agent = (
                existing if existing is not None and existing.worker == "coding_agent" else None
            )
            payload = {
                "version": 1,
                "assurance_mode": _assurance_setting(body, existing),
                "worker": {
                    "type": "coding_agent",
                    "coding_agent_command": command,
                    "coding_agent_timeout": _int_setting(
                        body.get("agent_timeout"),
                        existing_coding_agent.coding_agent_timeout
                        if existing_coding_agent
                        else 1_800,
                        1,
                        86_400,
                    ),
                    "coding_agent_transcript": _text_setting(
                        body.get("agent_transcript"),
                        existing_coding_agent.coding_agent_transcript
                        if existing_coding_agent
                        else ".agentic-harness/runs/{goal_id}/coding-agent.log",
                    ),
                },
                "review_command": verification,
                "review_command_timeout": _int_setting(
                    body.get("verification_timeout"),
                    existing.review_command_timeout if existing else 300,
                    1,
                    3_600,
                ),
                **_preserved_configuration_settings(existing),
                "autonomy": _autonomy_settings(body, existing),
            }
            fingerprint = _command_fingerprint(command)
            if self._execution_validation.get("fingerprint") != fingerprint:
                self._execution_validation = {}
            self._commit_configuration(payload, None)
            return self.setup()
        raise ValueError("Choose an installed coding agent, local model, or cloud model.")

    def test_connection(self, body: dict[str, Any]) -> dict[str, Any]:
        if str(body.get("execution") or "").strip() == "coding_agent":
            return self._test_coding_agent(body)
        profile = ProviderProfile(
            endpoint=str(body.get("endpoint") or ""),
            model=str(body.get("model") or ""),
            api_key_env=str(body.get("api_key_env") or ""),
        )
        entered_key = str(body.get("api_key") or "").strip()
        if entered_key and profile.api_key_env:
            raise ValueError(
                "Choose either a session API key or an environment variable, not both."
            )
        saved_session_key = ""
        if not entered_key and not profile.api_key_env and self.api_key:
            existing, configuration_error = self._gui_config_state()
            if (
                configuration_error is None
                and existing is not None
                and existing.worker == "model_agent"
                and existing.llm_credential_source == "session"
                and existing.llm_endpoint == profile.endpoint
                and existing.llm_model == profile.model
            ):
                saved_session_key = self.api_key
        api_key = (
            resolve_api_key(profile.api_key_env)
            if profile.api_key_env
            else entered_key or saved_session_key
        )
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
                    "content": (
                        'Return exactly this JSON object: {"action":"report_outcome",'
                        '"arguments":{"status":"progress","summary":"Connection test passed."}}'
                    ),
                },
                {"role": "user", "content": "Connection and structured-action test."},
            ]
        )
        content = response.content
        if not isinstance(content, dict) or content.get("action") != "report_outcome":
            raise ValueError(
                "The model answered, but it did not follow the structured action protocol."
            )
        arguments = content.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError(
                "The model answered, but it did not follow the structured action protocol."
            )
        try:
            validate_report_outcome(arguments)
        except ValueError as exc:
            raise ValueError(
                "The model answered, but it did not follow the structured action protocol."
            ) from exc
        credential_source = (
            "env"
            if profile.api_key_env
            else "session"
            if entered_key or saved_session_key
            else "none"
        )
        self._execution_validation = {
            "fingerprint": _model_connection_fingerprint(
                profile,
                credential_source=credential_source,
                credential_value=api_key,
            ),
            "verified": True,
            "kind": "model_agent",
            "credential_source": credential_source,
        }
        return {
            "reachable": True,
            "structured_actions": True,
            "verified": True,
            "model": profile.model,
            "data_location": profile.data_location,
            "summary": "The AI connection and structured actions are working.",
        }

    def _test_coding_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        agent = str(body.get("agent") or "").strip().lower()
        command = _existing_coding_agent_command(self.project_dir, agent)
        resolved = resolve_executable(agent) if agent != "current" else None
        if command is None and resolved is None:
            raise ValueError(f"{agent or 'The selected coding agent'} is not available on PATH.")
        if command is None:
            command = _coding_agent_command(agent, executable=resolved)

        label = _CODING_AGENT_LABELS.get(agent, "The configured coding agent")
        if agent != "codex":
            executable = resolve_command_executable([command[0], "--version"])
            try:
                proc = subprocess.run(
                    executable,
                    cwd=str(self.project_dir),
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                    shell=command_uses_windows_shell(executable),
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError(f"{label} executable test timed out after 30 seconds.") from exc
            if proc.returncode != 0:
                raise ValueError(_coding_agent_probe_error(label, proc))
            return {
                "reachable": True,
                "verified": False,
                "scope": "executable_only",
                "agent": agent,
                "summary": (
                    f"{label} is installed. This agent does not yet support a safe live "
                    "model probe, so connection compatibility will be checked on the first run."
                ),
            }

        probe_command = resolve_command_executable(
            _coding_agent_probe_command(command, agent=agent)
        )
        env = os.environ.copy()
        env["AGENTIC_HARNESS_OBJECTIVE"] = "Connection test only"
        env["AGENTIC_HARNESS_INSTRUCTION"] = "Connection test only"
        try:
            proc = subprocess.run(
                probe_command,
                cwd=str(self.project_dir),
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
                env=env,
                shell=command_uses_windows_shell(probe_command),
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"{label} connection test timed out after 120 seconds.") from exc
        if proc.returncode != 0:
            raise ValueError(_coding_agent_probe_error(label, proc))
        if "AGENTIC_HARNESS_AGENT_READY" not in proc.stdout:
            raise ValueError(
                f"{label} started, but its model did not return the expected connection-test reply."
            )
        fingerprint = _command_fingerprint(command)
        self._execution_validation = {
            "fingerprint": fingerprint,
            "verified": True,
            "agent": agent,
        }
        return {
            "reachable": True,
            "verified": True,
            "scope": "live_model",
            "agent": agent,
            "summary": f"{label} connection and configured model are working.",
        }

    def _coding_agent_validation(self, command: list[str]) -> dict[str, Any]:
        agent = _coding_agent_selection(command)
        label = _CODING_AGENT_LABELS.get(agent, "Coding agent")
        verified = self._execution_validation.get(
            "verified"
        ) is True and self._execution_validation.get("fingerprint") == _command_fingerprint(command)
        return {
            "verified": verified,
            "scope": "live_model" if verified else "executable_only",
            "summary": (
                f"{label} connection and configured model were verified in this app session."
                if verified
                else (
                    f"{label} is installed, but its connection and configured model have not "
                    "been tested in this app session."
                )
            ),
        }

    def _model_validation(self, config: HarnessConfig) -> dict[str, Any]:
        profile = ProviderProfile(
            endpoint=config.llm_endpoint,
            model=config.llm_model,
            api_key_env=config.llm_api_key_env,
        )
        credential_value = (
            self.api_key or ""
            if config.llm_credential_source == "session"
            else os.environ.get(config.llm_api_key_env, "").strip()
            if config.llm_credential_source == "env"
            else ""
        )
        verified = (
            self._execution_validation.get("verified") is True
            and self._execution_validation.get("kind") == "model_agent"
            and self._execution_validation.get("fingerprint")
            == _model_connection_fingerprint(
                profile,
                credential_source=config.llm_credential_source,
                credential_value=credential_value,
            )
            and self._execution_validation.get("credential_source")
            == config.llm_credential_source
        )
        return {
            "verified": verified,
            "scope": "structured_actions" if verified else "not_tested",
            "summary": (
                "The AI connection and structured actions were verified in this app session."
                if verified
                else "The AI connection has not been tested in this app session."
            ),
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
            profile = ProviderProfile(
                endpoint=config.llm_endpoint,
                model=config.llm_model,
                api_key_env=config.llm_api_key_env,
            )
            expected_fingerprint = _model_connection_fingerprint(
                profile,
                credential_source="session",
                credential_value=value,
            )
            validation_matches = (
                self._execution_validation.get("verified") is True
                and self._execution_validation.get("kind") == "model_agent"
                and self._execution_validation.get("fingerprint") == expected_fingerprint
            )
            current = self.store.read_current_goal()
            if current is not None and not _durably_terminal(current) and not validation_matches:
                raise ValueError(
                    "Test this AI connection with the session key before resuming the task."
                )
            if not validation_matches:
                self._execution_validation = {}
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
            if current is not None and not _durably_terminal(current):
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
        self._dispose_demo()
        try:
            config = self._config()
            strategy = strategy_by_key(
                str(body.get("strategy") or body.get("mode") or DEFAULT_PUBLIC_STRATEGY)
            )
            safe_areas = _safe_areas(body.get("safe_areas"), self.project_dir)
            strategy_error = self._strategy_boundary_error(
                strategy,
                config=config,
                safe_areas=safe_areas,
            )
            if strategy_error:
                return self._blocked_task(strategy_error, action="edit_strategy")
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
            metadata = goal_safety_metadata(
                self.project_dir,
                allowed_paths=safe_areas,
                review_commands=review_commands,
                path_enforcement=config.worker == "model_agent",
                secret_env_names=[config.llm_api_key_env],
                interface="gui",
            )
            metadata["execution_strategy"] = strategy.to_metadata()
            goal = supervisor.start(
                objective,
                metadata=metadata,
            )
            policy = self._policy_for_strategy(config, strategy)
        except (ConfigError, HarnessError, OSError, ValueError) as exc:
            return self._blocked_task(str(exc), action="setup")
        self._cancel.clear()
        with self._thread_lock:
            self._thread = Thread(
                target=self._drive,
                args=(review_commands, policy),
                name=f"agentic-harness-{goal.id[:8]}",
                daemon=True,
            )
            self._thread.start()
        return self._current_task(goal)

    def status(self) -> dict[str, Any]:
        demo_task = self._demo_status()
        if demo_task is not None:
            return demo_task
        goal = self.store.read_current_goal()
        if goal is None:
            return self._ready_task()
        return self._current_task(goal)

    def _current_task(self, goal: Goal) -> dict[str, Any]:
        task = self._task(goal)
        if self._driver_active() and task["status"] in {
            "done",
            "blocked",
            "failed",
            "stopped",
        }:
            task["status"] = "checking"
            task["status_label"] = _status_label("checking")
            task["summary"] = "Finalizing the task driver and durable evidence."
            task["needs_human"] = False
            plan = task.get("plan")
            requirements = task.get("requirements")
            task["progress"] = _progress(
                "checking",
                plan if isinstance(plan, list) else [],
                requirements if isinstance(requirements, list) else [],
            )
            task["allowed_actions"] = []
            final_result = task.get("final_result")
            if isinstance(final_result, dict):
                final_result["accepted"] = False
                final_result["label"] = "In progress"
                final_result["summary"] = ""
                final_result["reason"] = ""
            task["result_category"] = "in_progress"
        return task

    def _driver_active(self) -> bool:
        driver = self._thread
        return driver is not None and driver.is_alive()

    def history(self, *, query: str = "") -> list[dict[str, Any]]:
        needle = query.strip().lower()
        current = self.store.read_current_goal()
        current_id = current.id if current is not None else ""
        tasks = [
            self._current_task(goal) if goal.id == current_id else self._task(goal)
            for goal in self.store.list_goals()
        ]
        if self._demo_backend is not None:
            tasks = [
                _tag_demo_task(task) for task in self._demo_backend.history(query=query)
            ] + tasks
        if needle:
            tasks = [task for task in tasks if needle in json.dumps(task, sort_keys=True).lower()]
        return tasks

    def events(self, *, after: int = 0) -> list[dict[str, Any]]:
        if self._demo_backend is not None:
            return self._demo_backend.events(after=after)
        goal = self.store.read_current_goal()
        if goal is None:
            return []
        return TaskEventStore(self.project_dir, goal.id).read(after=after)

    def preview_file(self, path: str, *, goal_id: str = "") -> dict[str, Any]:
        if self._demo_backend is not None and self._demo_has_goal(goal_id):
            return self._demo_backend.preview_file(path, goal_id=goal_id)
        goal = self._preview_goal(goal_id)
        if goal is None:
            raise ValueError("There is no task with changed files.")
        frozen_changes = goal.metadata.get("terminal_workspace_changes")
        changes = (
            frozen_changes
            if _durably_terminal(goal) and isinstance(frozen_changes, dict)
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
        if self._demo_backend is not None and self._demo_has_goal(goal_id):
            return self._demo_backend.preview_artifact(path, goal_id=goal_id)
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

    def _demo_has_goal(self, goal_id: str) -> bool:
        if self._demo_backend is None:
            return False
        if not goal_id:
            return True
        return any(task.get("id") == goal_id for task in self._demo_backend.history())

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
        if self._demo_backend is not None:
            return _tag_demo_task(self._demo_backend.continue_task(feedback))
        with self._config_lock:
            return self._continue_task_locked(feedback)

    def approve_specification(self, requirements: list[str] | None = None) -> dict[str, Any]:
        """Approve pending high-assurance conditions and resume execution."""

        with self._config_lock:
            goal = self.store.read_current_goal()
            if goal is None:
                return self._blocked_task(
                    "There is no specification to approve.",
                    action="new_task",
                )
            if self._driver_active():
                return self._current_task(goal)
            try:
                config = self._config()
                review_commands = _review_commands_from_goal(goal)
                supervisor = build_supervisor(
                    self.project_dir,
                    review_commands=review_commands,
                    api_key=self.api_key,
                    cancel_requested=self._cancel.is_set,
                )
                policy = self._policy_for_goal(config, goal)
                goal = AutonomousRunner(
                    supervisor,
                    policy=policy,
                    cancel_requested=self._cancel.is_set,
                ).approve_specification(requirements)
                self._cancel.clear()
                self._start_thread(review_commands, policy)
                return self._current_task(goal)
            except (ConfigError, HarnessError, OSError, ValueError) as exc:
                return self._blocked_task(str(exc), action="view_current")

    def _continue_task_locked(self, feedback: str = "") -> dict[str, Any]:
        goal = self.store.read_current_goal()
        if goal is None:
            return self._blocked_task("There is no task to continue.", action="new_task")
        if self._thread is not None and self._thread.is_alive():
            return self._current_task(goal)
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
            config = self._config()
            self._start_thread(
                _review_commands_from_goal(goal),
                self._policy_for_goal(config, goal),
            )
            return self._current_task(goal)
        except (ConfigError, HarnessError, OSError, ValueError) as exc:
            return self._blocked_task(str(exc), action="setup")

    def accept(self) -> dict[str, Any]:
        if self._demo_backend is not None:
            return _tag_demo_task(self._demo_backend.accept())
        goal = self.store.read_current_goal()
        if goal is None:
            return self._blocked_task("There is no task to accept.", action="new_task")
        if build_run_receipt(goal).category == "verified_done":
            return self._current_task(goal)
        return self._blocked_task(
            "This task cannot be accepted until independent verification passes.",
            action="view_checks",
        )

    def stop(self) -> dict[str, Any]:
        if self._demo_backend is not None:
            return _tag_demo_task(self._demo_backend.stop())
        goal = self.store.read_current_goal()
        if goal is None:
            return self._ready_task()
        self._cancel.set()
        if self._thread is None or not self._thread.is_alive():
            self._mark_cancelled()
            return self.status()
        task = self._current_task(goal)
        task["status"] = "stopping"
        task["status_label"] = "Stopping safely"
        task["summary"] = "The current tool step will finish, then the task will stop."
        task["allowed_actions"] = []
        return task

    def _drive(
        self,
        review_commands: list[list[str]],
        policy: AutonomyPolicy | None = None,
    ) -> None:
        try:
            with self._config_lock:
                supervisor = build_supervisor(
                    self.project_dir,
                    review_commands=review_commands,
                    api_key=self.api_key,
                    cancel_requested=self._cancel.is_set,
                )
                policy = (
                    policy
                    or self.policy
                    or autonomy_policy_from_config(load_config(self.project_dir))
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
                if _durably_terminal(goal) or intervention:
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

    def _start_thread(
        self,
        review_commands: list[list[str]],
        policy: AutonomyPolicy | None = None,
    ) -> None:
        with self._thread_lock:
            self._thread = Thread(
                target=self._drive,
                args=(review_commands, policy),
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

    def _gui_config_state(
        self,
    ) -> tuple[HarnessConfig | None, dict[str, str] | None]:
        """Separate a new/noop project from an invalid existing configuration."""

        config_dir = self.project_dir / CONFIG_DIR
        path = config_dir / CONFIG_NAME
        if config_dir.is_symlink() or path.is_symlink():
            return None, {
                "code": "unsafe_configuration_path",
                "summary": "The existing configuration path is a symlink.",
            }
        if config_dir.exists() and not config_dir.is_dir():
            return None, {
                "code": "unsafe_configuration_path",
                "summary": "The existing configuration path is not a directory.",
            }
        if path.exists() and not path.is_file():
            return None, {
                "code": "unsafe_configuration_path",
                "summary": "The existing configuration file path is not a regular file.",
            }
        if not path.exists():
            return None, None
        try:
            config = load_config(self.project_dir)
        except (ConfigError, OSError, UnicodeError) as exc:
            raw_message = str(exc).strip()
            if isinstance(exc, ConfigError) and raw_message.startswith("invalid YAML config:"):
                message = "The existing configuration is not valid YAML."
            elif isinstance(exc, (OSError, UnicodeError)):
                message = "The existing configuration could not be read."
            else:
                message = safe_inline_text(raw_message)[:500]
            message = message or "The existing configuration could not be read."
            return None, {
                "code": "invalid_existing_configuration",
                "summary": message,
            }
        if config.worker == "noop":
            return None, None
        return config, None

    def _task(self, goal: Goal) -> dict[str, Any]:
        autonomy = goal.metadata.get("autonomy")
        if not isinstance(autonomy, dict):
            autonomy = {}
        status = _task_status(goal, autonomy)
        terminal = _durably_terminal(goal) and status in {"done", "blocked", "stopped"}
        if terminal:
            goal = self._ensure_terminal_report(goal)
            autonomy = goal.metadata.get("autonomy")
            if not isinstance(autonomy, dict):
                autonomy = {}
            status = _task_status(goal, autonomy)
            terminal = _durably_terminal(goal) and status in {
                "done",
                "blocked",
                "stopped",
            }
        outcome = goal.metadata.get("worker_outcome")
        if not isinstance(outcome, dict):
            outcome = {}
        plan_value = autonomy.get("plan")
        plan: list[Any] = plan_value if isinstance(plan_value, list) else []
        requirements = self._requirement_rows(goal, autonomy)
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
        changed_files_evidence = _changed_files_evidence(changes)
        verification = _verification(goal, outcome)
        report_ready = not terminal or self._terminal_report_ready(goal)
        if terminal and not report_ready:
            status = "checking"
        receipt = build_run_receipt(goal)
        terminal_receipt_ready = terminal and report_ready
        result_category = receipt.category if terminal_receipt_ready else "in_progress"
        public_status = status
        status_label = _status_label(status)
        if terminal_receipt_ready:
            public_status = {
                "verified_done": "done",
                "blocked": "blocked",
                "failed": "failed",
            }.get(receipt.category, status)
            status_label = receipt.label
        progress = _progress(public_status, plan, requirements)
        blocker = autonomy.get("blocker")
        blocker_reason = str(blocker.get("reason") or "") if isinstance(blocker, dict) else ""
        trusted_error = (
            str(goal.error or blocker_reason) if status in {"blocked", "stopped"} else ""
        )
        summary = redact_secrets(
            str(
                trusted_error
                or outcome.get("summary")
                or goal.metadata.get("worker_summary")
                or goal.error
                or goal.objective
            )
        )
        if terminal_receipt_ready:
            summary = receipt.trusted_reason
        elif terminal and not report_ready:
            summary = "Finalizing the durable terminal report."
        return {
            "contract": GUI_TASK_CONTRACT,
            "id": goal.id,
            "human_title": goal.objective[:100],
            "objective": goal.objective,
            "status": public_status,
            "status_label": status_label,
            "result_category": result_category,
            "summary": summary,
            "needs_human": public_status in {"blocked", "needs_review"},
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
            "changed_files_evidence": changed_files_evidence,
            "verification": verification,
            "artifacts": [{"name": Path(path).name, "path": path} for path in goal.artifacts],
            "allowed_actions": _allowed_actions(public_status),
            "safety": _public_safety(goal),
            "final_result": _final_result_payload(
                goal,
                receipt,
                terminal_ready=terminal_receipt_ready,
                status=public_status,
                changed_files=changed_files,
                changed_files_evidence=changed_files_evidence,
                verification=verification,
                requirements=requirements,
            ),
            "metadata": {
                "created_at": goal.created_at,
                "updated_at": goal.updated_at,
                "observed_at": goal.updated_at,
                "worker": self._public_worker(),
                "strategy": _public_strategy(goal),
                "budget": autonomy.get("budget")
                if isinstance(autonomy.get("budget"), dict)
                else {},
                "specification_review": self._specification_review(goal, autonomy),
            },
        }

    def _requirement_rows(
        self, goal: Goal, autonomy: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Hydrate mutable status from the separate frozen GoalSpec."""

        status_value = autonomy.get("requirement_status")
        status_rows = status_value if isinstance(status_value, list) else []
        by_id = {
            str(row.get("id") or ""): row
            for row in status_rows
            if isinstance(row, dict) and str(row.get("id") or "")
        }
        try:
            spec = self.store.read_goal_spec(goal.id)
        except StateLockError:
            legacy = autonomy.get("requirements")
            return legacy if isinstance(legacy, list) else []
        return [
            {
                "id": requirement.id,
                "text": requirement.text,
                "status": str(by_id.get(requirement.id, {}).get("status") or "pending"),
                "evidence": (
                    list(by_id.get(requirement.id, {}).get("evidence") or [])
                    if isinstance(by_id.get(requirement.id, {}).get("evidence"), list)
                    else []
                ),
            }
            for requirement in spec.requirements
        ]

    def _specification_review(
        self,
        goal: Goal,
        autonomy: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(autonomy.get("status") or "")
        if status not in {
            "awaiting_specification_approval",
            "awaiting_specification_amendment",
        }:
            return {}
        try:
            spec = self.store.read_goal_spec(goal.id)
            conditions = spec.requirements
            reason = "Review the completion conditions before any work starts."
            kind = "initial"
            if status == "awaiting_specification_amendment":
                amendment = autonomy.get("specification_amendment")
                if not isinstance(amendment, dict):
                    return {}
                conditions = amended_requirements(
                    spec,
                    amendment.get("proposed_changes"),
                )
                reason = str(
                    amendment.get("reason")
                    or "The worker requested a change to the frozen completion conditions."
                )
                kind = "amendment"
            return {
                "kind": kind,
                "reason": redact_secrets(reason),
                "version": self.store.read_goal_spec_version(goal.id),
                "conditions": [item.to_dict() for item in conditions],
            }
        except (HarnessError, OSError, ValueError):
            return {}

    def _policy_for_strategy(
        self,
        config: HarnessConfig,
        strategy: ExecutionStrategy,
    ) -> AutonomyPolicy:
        base = self.policy or autonomy_policy_from_config(config)
        return policy_for_strategy(base, strategy)

    def _policy_for_goal(self, config: HarnessConfig, goal: Goal) -> AutonomyPolicy:
        return self._policy_for_strategy(
            config,
            strategy_from_metadata(goal.metadata.get("execution_strategy")),
        )

    @staticmethod
    def _strategy_boundary_error(
        strategy: ExecutionStrategy,
        *,
        config: HarnessConfig,
        safe_areas: list[str],
    ) -> str:
        if not strategy.requires_enforced_scope:
            return ""
        if config.worker != "model_agent":
            return (
                "Bounded experiment requires the built-in model worker because installed "
                "coding-agent CLIs do not enforce the selected file boundary."
            )
        if not safe_areas:
            return "Bounded experiment requires at least one allowed file or folder."
        return ""

    def _ensure_terminal_report(self, goal: Goal) -> Goal:
        if not _durably_terminal(goal):
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
                if not _durably_terminal(current):
                    return current
                existing_report = _terminal_report_path(self.project_dir, current)
                existing_content = _read_report(existing_report)
                workspace_state = str(current.metadata.get("terminal_workspace_state_sha256") or "")
                frozen_changes = current.metadata.get("terminal_workspace_changes")
                if not isinstance(frozen_changes, dict):
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
                            "evidence_unavailable": True,
                        }
                    current.metadata["terminal_workspace_changes"] = frozen_changes
                if not workspace_state:
                    current.metadata["terminal_workspace_state_sha256"] = (
                        _terminal_workspace_boundary_sha256(self.project_dir, current)
                    )
                desired_state = _terminal_report_state_sha256(
                    self.project_dir,
                    current,
                )
                if self._terminal_report_ready(current):
                    self.store.write_goal(current, make_current=is_current)
                    return current
                if (
                    existing_content
                    and not current.metadata.get("terminal_report_state_sha256")
                    and _legacy_report_matches(current, existing_content)
                ):
                    current.metadata["terminal_report_contract"] = TERMINAL_REPORT_CONTRACT
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
                current.metadata["terminal_report_contract"] = TERMINAL_REPORT_CONTRACT
                current.metadata["terminal_report_state_sha256"] = desired_state
                current.metadata["terminal_report_content_sha256"] = hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest()
                self.store.write_goal(current, make_current=is_current)
                return current
        except (OSError, StateLockError, ValueError):
            return goal

    def _terminal_report_ready(self, goal: Goal) -> bool:
        if not _durably_terminal(goal):
            return False
        state_digest = str(goal.metadata.get("terminal_report_state_sha256") or "")
        content_digest = str(goal.metadata.get("terminal_report_content_sha256") or "")
        if (
            goal.metadata.get("terminal_report_contract") != TERMINAL_REPORT_CONTRACT
            or not state_digest
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
            "final_result": {
                "accepted": False,
                "summary": "",
                "what_changed": [],
                "checks": [],
                "remaining": [],
            },
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
        if config.worker == "coding_agent":
            agent = _coding_agent_selection(config.coding_agent_command)
            if agent != "current":
                worker.update({"agent": agent, "label": _CODING_AGENT_LABELS[agent]})
            else:
                worker.update(
                    {
                        "agent": "current",
                        "label": "Current configured agent",
                    }
                )
        elif config.worker == "model_agent":
            if config.llm_credential_source == "session":
                credential_source = "session"
            elif config.llm_credential_source == "env":
                credential_source = f"env:{config.llm_api_key_env}"
            else:
                credential_source = "none"
            profile = ProviderProfile(
                endpoint=config.llm_endpoint,
                model=config.llm_model,
                api_key_env=config.llm_api_key_env,
            )
            worker.update(
                {
                    "model": config.llm_model,
                    "endpoint": config.llm_endpoint,
                    "credential_source": credential_source,
                    "data_location": profile.data_location,
                    "network_scope": profile.network_scope,
                }
            )
        return worker

    def _worker_label(self, config: HarnessConfig) -> str:
        if config.worker == "model_agent":
            return f"model {config.llm_model}"
        if config.worker == "coding_agent":
            agent = _coding_agent_selection(config.coding_agent_command)
            return _CODING_AGENT_LABELS.get(agent, "the installed coding agent")
        return config.worker.replace("_", " ")


def _public_strategy(goal: Goal) -> dict[str, object]:
    strategy = strategy_from_metadata(goal.metadata.get("execution_strategy"))
    return {
        "key": strategy.key,
        "label": strategy.label,
        "budget_profile": strategy.budget_profile,
    }


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
    oauth_variant = (name.startswith("client_secret") and name.endswith(".json")) or (
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
    if autonomy.get("status") in {
        "awaiting_specification_approval",
        "awaiting_specification_amendment",
    }:
        return "needs_review"
    if autonomy.get("operator_intervention_required") is True:
        return "blocked"
    if goal.status is GoalStatus.FAILED:
        if _retryable_autonomy_failure(goal):
            return "checking"
        return "blocked"
    if goal.status is GoalStatus.REVIEW:
        return "checking"
    if goal.status is GoalStatus.PLANNING:
        return "starting"
    return "working"


def _retryable_autonomy_failure(goal: Goal) -> bool:
    autonomy = goal.metadata.get("autonomy")
    return (
        goal.status is GoalStatus.FAILED
        and goal.metadata.get("cancelled") is not True
        and isinstance(autonomy, dict)
        and autonomy.get("contract") == AUTONOMY_CONTRACT
        and autonomy.get("operator_intervention_required") is not True
    )


def _durably_terminal(goal: Goal) -> bool:
    return goal.status.is_terminal and not _retryable_autonomy_failure(goal)


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
                    "name": redact_secrets(str(row.get("name") or "Verification")),
                    "passed": row.get("passed") is True,
                    "message": redact_secrets(str(row.get("message") or "")),
                    "independent": row.get("independent") is True,
                    "source": (
                        "independent" if row.get("independent") is True else "worker-reported"
                    ),
                }
            )
    worker_checks = outcome.get("verification")
    if isinstance(worker_checks, list):
        for row in worker_checks:
            if isinstance(row, dict):
                results.append(
                    {
                        "name": redact_secrets(
                            str(row.get("label") or row.get("id") or "Worker check")
                        ),
                        "passed": row.get("passed") is True,
                        "message": redact_secrets(str(row.get("message") or "")),
                        "independent": False,
                        "source": "worker-reported",
                    }
                )
    return results


def _final_result_payload(
    goal: Goal,
    receipt: RunReceipt,
    *,
    terminal_ready: bool,
    status: str,
    changed_files: list[Any],
    changed_files_evidence: dict[str, Any],
    verification: list[dict[str, Any]],
    requirements: list[Any],
) -> dict[str, Any]:
    reason = receipt.trusted_reason if terminal_ready else ""
    return {
        "label": receipt.label if terminal_ready else "In progress",
        "accepted": (terminal_ready and receipt.category == "verified_done" and status == "done"),
        "summary": reason,
        "reason": reason,
        "worker_claim": {
            "label": receipt.worker_claim_label,
            "trusted": receipt.worker_claim_trusted,
            "summary": receipt.worker_claim,
        },
        "attempts": receipt.attempts,
        "retries": receipt.retries,
        "verification_commands": list(receipt.verification_commands),
        "review_attempts": _review_attempt_payloads(receipt),
        "what_changed": changed_files,
        "what_changed_evidence": changed_files_evidence,
        "checks": verification,
        "remaining": _remaining(requirements),
    }


def _changed_files_evidence(changes: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(changes, dict) and changes.get("evidence_unavailable") is True:
        return {
            "available": False,
            "reason": "Changed-file evidence was unavailable at the terminal boundary.",
        }
    return {"available": isinstance(changes, dict), "reason": ""}


def _review_attempt_payloads(receipt: RunReceipt) -> list[dict[str, Any]]:
    return [
        {
            "number": attempt.number,
            "source": attempt.source,
            "passed": attempt.passed,
            "summary": attempt.summary,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "message": check.message,
                    "independent": check.independent,
                    "source": "independent" if check.independent else "worker-reported",
                }
                for check in attempt.checks
            ],
        }
        for attempt in receipt.review_attempts
    ]


def _allowed_actions(status: str) -> list[dict[str, Any]]:
    if status == "done":
        return [{"action": "new_task", "enabled": True}]
    if status == "blocked":
        return [
            {"action": "continue", "enabled": True},
            {"action": "stop", "enabled": True},
        ]
    if status == "needs_review":
        return [
            {"action": "approve_spec", "enabled": True},
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
    return _terminal_state_sha256(project_dir, goal, include_workspace_changes=True)


def _terminal_workspace_boundary_sha256(project_dir: Path, goal: Goal) -> str:
    return _terminal_state_sha256(project_dir, goal, include_workspace_changes=False)


def _terminal_state_sha256(
    project_dir: Path,
    goal: Goal,
    *,
    include_workspace_changes: bool,
) -> str:
    payload = goal.to_dict()
    payload.pop("updated_at", None)
    payload["artifacts"] = [path for path in goal.artifacts if Path(path).name != "report.md"]
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in (
            "terminal_report_state_sha256",
            "terminal_report_content_sha256",
            "terminal_report_contract",
            "terminal_workspace_state_sha256",
        ):
            metadata.pop(key, None)
        if not include_workspace_changes:
            metadata.pop("terminal_workspace_changes", None)
    payload["terminal_report_contract"] = TERMINAL_REPORT_CONTRACT
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
    receipt = build_run_receipt(goal)
    accepted = _receipt_accepted(receipt)
    required_fields = (
        f"- Contract: {TERMINAL_REPORT_CONTRACT}",
        f"- Result: {receipt.label}",
        f"- Trusted reason: {receipt.trusted_reason}",
        f"- Accepted: {'yes' if accepted else 'no'}",
        f"- Attempts: {receipt.attempts}",
        f"- Retries: {receipt.retries}",
    )
    lines = content.splitlines()
    if not all(_report_field_is_exact(lines, field) for field in required_fields):
        return False
    if lines.count("## Worker claim (untrusted)") != 1:
        return False
    if receipt.worker_claim and f"- {receipt.worker_claim}" not in content:
        return False
    if any(line not in content for line in _report_review_attempt_lines(receipt)):
        return False
    if any(line not in content for line in _report_verification_command_lines(receipt)):
        return False
    if lines.count("## Artifacts") != 1:
        return False
    if any(line not in content for line in _report_artifact_lines(goal)):
        return False
    raw_fields = [line.lstrip("- ") for line in lines]
    if any(line.startswith("Summary:") for line in raw_fields):
        return False
    if receipt.category != "verified_done" and any("Status: done" in line for line in lines):
        return False
    return True


def _report_field_is_exact(lines: list[str], expected: str) -> bool:
    prefix = expected.partition(":")[0] + ":"
    return [line for line in lines if line.startswith(prefix)] == [expected]


def _receipt_accepted(receipt: RunReceipt) -> bool:
    return receipt.category == "verified_done"


def _report_review_attempt_lines(receipt: RunReceipt) -> list[str]:
    if not receipt.review_attempts:
        return ["- No review attempts were recorded."]
    lines: list[str] = []
    for attempt in receipt.review_attempts:
        lines.extend(
            [
                f"### Attempt {attempt.number} ({attempt.source})",
                "",
                f"- Outcome: {'passed' if attempt.passed else 'failed'}",
            ]
        )
        if not attempt.checks:
            lines.append("- No review checks were recorded.")
            continue
        for check in attempt.checks:
            result = "passed" if check.passed else "failed"
            source = "independent" if check.independent else "worker-reported"
            detail = check.message or check.name
            lines.append(f"- {result} ({source}): {detail}")
    return lines


def _report_verification_command_lines(receipt: RunReceipt) -> list[str]:
    if not receipt.verification_commands:
        return ["- No verification commands were recorded."]
    return [
        f"- {number}. {command}" for number, command in enumerate(receipt.verification_commands, 1)
    ]


def _report_artifact_lines(goal: Goal) -> list[str]:
    report_path = (Path(CONFIG_DIR) / "runs" / goal.id / "report.md").as_posix()
    paths: list[str] = []
    seen: set[str] = set()
    for path in [*goal.artifacts, report_path]:
        rendered = safe_inline_text(path)
        if rendered in seen:
            continue
        seen.add(rendered)
        paths.append(rendered)
    return [f"- {number}. {path}" for number, path in enumerate(paths, 1)]


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
    receipt = build_run_receipt(goal)
    accepted = _receipt_accepted(receipt)
    lines = [
        "# Agentic Harness Report",
        "",
        f"- Contract: {TERMINAL_REPORT_CONTRACT}",
        f"- Goal: {safe_inline_text(goal.id)}",
        f"- Objective: {safe_inline_text(goal.objective)}",
        f"- Result: {receipt.label}",
        f"- Trusted reason: {receipt.trusted_reason}",
        f"- Accepted: {'yes' if accepted else 'no'}",
        f"- Attempts: {receipt.attempts}",
        f"- Retries: {receipt.retries}",
        f"- Current subgoal: {safe_inline_text(autonomy.get('current_subgoal') or 'not reported')}",
        f"- Checkpoint: {safe_inline_text(autonomy.get('checkpoint') or 'not reported')}",
        f"- Cycles: {int(autonomy.get('cycle') or 0)}",
        "",
        "## Worker claim (untrusted)",
        "",
        f"- {receipt.worker_claim or 'No worker completion claim was recorded.'}",
        "",
        "## Review attempts (ordered)",
        "",
    ]
    lines.extend(_report_review_attempt_lines(receipt))
    lines.extend(["", "## Verification commands", ""])
    lines.extend(_report_verification_command_lines(receipt))
    lines.extend(["", "## Artifacts", ""])
    lines.extend(_report_artifact_lines(goal))
    lines.extend(["", "## Plan", ""])
    plan = autonomy.get("plan")
    if isinstance(plan, list) and plan:
        for row in plan:
            if isinstance(row, dict):
                lines.append(
                    f"- [{safe_inline_text(row.get('status') or 'pending')}] "
                    f"{safe_inline_text(row.get('step') or row.get('text') or 'Plan item')}"
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
            evidence_text = (
                ", ".join(safe_inline_text(item) for item in evidence)
                if isinstance(evidence, list)
                else ""
            )
            lines.append(
                f"- [{safe_inline_text(row.get('status') or 'pending')}] "
                f"{safe_inline_text(row.get('text') or row.get('id') or 'Requirement')}"
                + (f" — evidence: {evidence_text}" if evidence_text else "")
            )
    else:
        lines.append("- No structured requirements were reported.")
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
    if isinstance(changes, dict) and changes.get("evidence_unavailable") is True:
        lines.append("- Changed-file evidence was unavailable at the terminal boundary.")
    elif isinstance(entries, list) and entries:
        for row in entries:
            if isinstance(row, dict):
                lines.append(
                    f"- {safe_inline_text(row.get('status') or 'changed')}: "
                    f"{safe_inline_text(row.get('path') or 'unknown')}"
                )
    else:
        lines.append("- No workspace file changes were recorded.")
    events = TaskEventStore(project_dir, goal.id).read()
    lines.extend(["", "## Activity evidence", ""])
    if events:
        for event in events:
            lines.append(
                f"- {safe_inline_text(event.get('evidence_id') or 'event')}: "
                f"{safe_inline_text(event.get('summary') or 'Progress recorded')}"
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


def _review_command_label(command: list[str]) -> str:
    """Describe a deterministic project check without requiring shell knowledge."""

    if not command:
        return "No automatic project check found"
    signatures = {
        ("npm", "test"): "Project tests (npm)",
        ("pnpm", "test"): "Project tests (pnpm)",
        ("yarn", "test"): "Project tests (Yarn)",
        ("bun", "test"): "Project tests (Bun)",
        ("cargo", "test"): "Project tests (Rust)",
        ("go", "test", "./..."): "Project tests (Go)",
        ("mvn", "test"): "Project tests (Maven)",
        ("./mvnw", "test"): "Project tests (Maven)",
        ("mvnw.cmd", "test"): "Project tests (Maven)",
        ("mvnw.bat", "test"): "Project tests (Maven)",
        ("gradle", "test"): "Project tests (Gradle)",
        ("./gradlew", "test"): "Project tests (Gradle)",
        ("gradlew.bat", "test"): "Project tests (Gradle)",
        ("gradlew.cmd", "test"): "Project tests (Gradle)",
        ("dotnet", "test"): "Project tests (.NET)",
        ("bundle", "exec", "rspec"): "Project tests (RSpec)",
        ("python", "-m", "pytest", "-q"): "Project tests (pytest)",
    }
    return signatures.get(tuple(command), "Configured project check")


def _probe_local_model_server(probe: dict[str, Any]) -> dict[str, Any] | None:
    """Return a small public result from one fixed loopback model-server probe."""

    try:
        connection = HTTPConnection("127.0.0.1", int(probe["port"]), timeout=0.35)
        connection.request("GET", "/v1/models", headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(65_537)
    except (OSError, TimeoutError, ValueError):
        return None
    finally:
        try:
            connection.close()
        except (NameError, OSError):
            pass
    if response.status != 200 or len(raw) > 65_536:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    rows = payload.get("data") if isinstance(payload, dict) else None
    models: list[str] = []
    if isinstance(rows, list):
        for row in rows[:25]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                model_id = row["id"].strip()[:300]
                if model_id and model_id not in models:
                    models.append(model_id)
    if not models:
        return None
    return {
        "template_key": str(probe["template_key"]),
        "label": str(probe["label"]),
        "endpoint": str(probe["endpoint"]),
        "model": models[0] if models else "",
        "models": models,
    }


def _tag_demo_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Make the scripted, isolated nature of the practice run unambiguous."""

    task = deepcopy(payload)
    task["human_title"] = "Safe demo: verified repair"
    metadata = task.setdefault("metadata", {})
    metadata["demo"] = {
        "enabled": True,
        "kind": "scripted_practice",
        "model_used": False,
        "workspace": "temporary",
        "data_location": "isolated_local",
        "label": "Safe demo · scripted worker",
    }
    metadata["execution"] = {
        "label": "Safe demo · scripted worker",
        "data_location": "local",
        "detail": (
            "No model or API key is used. The temporary practice workspace is isolated "
            "from the selected project."
        ),
    }
    metadata["strategy"] = {
        "key": "demo",
        "label": "Safe demo",
        "budget_profile": "practice",
    }
    worker = metadata.get("worker")
    if not isinstance(worker, dict):
        worker = {}
    worker["label"] = "Scripted practice worker (not AI)"
    worker["model_used"] = False
    metadata["worker"] = worker
    if task.get("status") in {
        "starting",
        "working",
        "checking",
        "stopping",
        "needs_review",
    }:
        task["progress"] = {
            "determinate": False,
            "percent": None,
            "label": "Waiting for independent verification",
        }
    safety = task.get("safety")
    if isinstance(safety, dict) and isinstance(safety.get("checks"), list):
        for check in safety["checks"]:
            if isinstance(check, dict):
                check["label"] = _DEMO_CHECK_LABEL
    final = task.get("final_result")
    if isinstance(final, dict):
        if isinstance(final.get("verification_commands"), list):
            final["verification_commands"] = [_DEMO_CHECK_LABEL]
        claim = final.get("worker_claim")
        if isinstance(claim, dict):
            claim["label"] = "Scripted worker report (not AI)"
            reported = str(claim.get("summary") or "").strip()
            if reported and not reported.startswith("Scripted practice worker reported:"):
                claim["summary"] = f"Scripted practice worker reported: {reported}"
    return task


def _coding_agent_command(agent: str, *, executable: str | None = None) -> list[str]:
    if agent not in _CODING_AGENT_COMMANDS:
        raise ValueError("Choose codex, opencode, aider, or codewhale.")
    command = list(_CODING_AGENT_COMMANDS[agent])
    if executable:
        command[0] = executable
    return command


def _command_fingerprint(command: list[str]) -> str:
    encoded = json.dumps(command, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_connection_fingerprint(
    profile: ProviderProfile,
    *,
    credential_source: str,
    credential_value: str,
) -> str:
    credential_digest = hashlib.sha256(credential_value.encode("utf-8")).hexdigest()
    encoded = json.dumps(
        {
            "endpoint": profile.endpoint,
            "model": profile.model,
            "credential_source": credential_source,
            "credential_name": profile.api_key_env if credential_source == "env" else "",
            "credential_digest": credential_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coding_agent_probe_command(command: list[str], *, agent: str) -> list[str]:
    prompt = (
        "Reply with exactly AGENTIC_HARNESS_AGENT_READY. "
        "This is a connection test. Do not inspect or modify files."
    )
    result = [part.replace("{objective}", prompt) for part in command]
    if all("{objective}" not in part for part in command):
        result.append(prompt)
    if agent != "codex":
        return result

    sandbox_found = False
    for index, part in enumerate(result):
        if part in {"-s", "--sandbox"} and index + 1 < len(result):
            result[index + 1] = "read-only"
            sandbox_found = True
        elif part.startswith("--sandbox="):
            result[index] = "--sandbox=read-only"
            sandbox_found = True
    if not sandbox_found:
        prompt_index = result.index(prompt) if prompt in result else len(result)
        result[prompt_index:prompt_index] = ["-s", "read-only"]
    return result


def _coding_agent_probe_error(
    label: str,
    proc: subprocess.CompletedProcess[str],
) -> str:
    raw = (proc.stderr or proc.stdout or "No diagnostic output was returned.").strip()
    details = redact_secrets(raw)
    if len(details) > 1_500:
        details = details[-1_500:]
    return f"{label} connection test failed with exit code {proc.returncode}: {details}"


def _existing_coding_agent_command(
    project_dir: Path,
    agent: str,
) -> list[str] | None:
    try:
        config = load_config(project_dir)
    except ConfigError:
        return None
    command = config.coding_agent_command
    matches = _coding_agent_selection(command) == agent
    if config.worker != "coding_agent" or not matches:
        return None
    if resolve_executable(command[0]) is None:
        return None
    return list(command)


def _coding_agent_options(
    config: HarnessConfig | None = None,
) -> tuple[list[dict[str, Any]], str]:
    available = {agent: resolve_executable(agent) is not None for agent in _CODING_AGENT_COMMANDS}
    configured = ""
    current_available = False
    if config is not None and config.worker == "coding_agent":
        configured = _coding_agent_selection(config.coding_agent_command)
        if (
            configured != "current"
            and resolve_executable(config.coding_agent_command[0]) is not None
        ):
            available[configured] = True
        elif configured == "current":
            current_available = (
                bool(config.coding_agent_command)
                and resolve_executable(config.coding_agent_command[0]) is not None
            )
    recommended = (
        configured
        if available.get(configured)
        else next(
            (agent for agent, found in available.items() if found),
            "",
        )
    )
    if configured == "current" and current_available:
        recommended = "current"
    options = [
        {
            "key": agent,
            "label": _CODING_AGENT_LABELS[agent],
            "available": available[agent],
            "recommended": agent == recommended,
        }
        for agent in _CODING_AGENT_COMMANDS
    ]
    if configured == "current":
        options.insert(
            0,
            {
                "key": "current",
                "label": "Current configured agent",
                "available": current_available,
                "recommended": current_available,
            },
        )
    return (
        options,
        recommended,
    )


def _coding_agent_identity(command: list[str]) -> str:
    if not command:
        return ""
    executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if executable.endswith(suffix):
            executable = executable[: -len(suffix)]
            break
    return executable if executable in _CODING_AGENT_LABELS else ""


def _coding_agent_selection(command: list[str]) -> str:
    identity = _coding_agent_identity(command)
    if not identity or command[1:] != _CODING_AGENT_COMMANDS[identity][1:]:
        return "current"
    return identity


def _int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("setting must be a whole number")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError("setting must be a whole number")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("setting must be a whole number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"setting must be between {minimum} and {maximum}")
    return parsed


def _float_setting(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("setting must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("setting must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError("setting must be a finite number")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"setting must be between {minimum:g} and {maximum:g}")
    return parsed


def _text_setting(value: Any, default: str) -> str:
    parsed = default if value in (None, "") else str(value).strip()
    if not parsed:
        raise ValueError("setting must not be empty")
    return parsed


def _gui_setup_editable(config: HarnessConfig) -> bool:
    return config.worker in {"noop", "coding_agent", "model_agent"}


def _autonomy_settings(
    body: dict[str, Any],
    existing: HarnessConfig | None = None,
) -> dict[str, int]:
    return {
        "max_cycles": _int_setting(
            body.get("max_cycles"),
            existing.goal_max_cycles if existing else 100,
            1,
            10_000,
        ),
        "max_elapsed_seconds": _int_setting(
            body.get("max_elapsed_seconds"),
            existing.goal_max_elapsed_seconds if existing else 7_200,
            1,
            604_800,
        ),
        "max_total_tokens": _int_setting(
            body.get("max_total_tokens"),
            existing.goal_max_total_tokens if existing else 500_000,
            1,
            100_000_000,
        ),
        "max_provider_calls": _int_setting(
            body.get("max_provider_calls"),
            existing.goal_max_provider_calls if existing else 200,
            1,
            100_000,
        ),
        "max_tool_calls": _int_setting(
            body.get("max_tool_calls"),
            existing.goal_max_tool_calls if existing else 1_000,
            1,
            1_000_000,
        ),
    }


def _preserved_configuration_settings(
    existing: HarnessConfig | None,
) -> dict[str, Any]:
    if existing is None:
        return {}
    preserved: dict[str, Any] = {}
    if existing.allow_noop_success:
        preserved["allow_noop_success"] = True
    if existing.review_artifact:
        preserved["review_artifact"] = existing.review_artifact
    if existing.review_file_changed:
        preserved["review_file_changed"] = existing.review_file_changed
    if existing.review_git_clean:
        preserved["review_git_clean"] = True
    return preserved


def _assurance_setting(
    body: dict[str, Any],
    existing: HarnessConfig | None,
) -> str:
    value = str(
        body.get("assurance_mode")
        or (existing.assurance_mode if existing else "specification_frozen")
    ).strip()
    if value not in {"check_gated", "specification_frozen", "high_assurance"}:
        raise ValueError("Choose check-gated, specification-frozen, or high assurance.")
    return value
