"""Shared construction of the public harness engine for CLI and GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentic_harness.adapters.coding_agent import CodingAgentWorker
from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.model_agent import EmbeddedModelAgent, OpenAICompatibleProvider
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.adapters.tmux import TmuxWorker
from agentic_harness.core.config import HarnessConfig, load_config
from agentic_harness.core.autonomy import AutonomyPolicy
from agentic_harness.core.assurance import AssuranceMode
from agentic_harness.core.errors import ConfigError
from agentic_harness.core.providers import resolve_api_key
from agentic_harness.core.review import (
    DeterministicReviewer,
    ReviewCriterion,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
)
from agentic_harness.core.supervisor import Supervisor
from agentic_harness.core.worker import Worker


def build_supervisor(
    project_dir: Path,
    *,
    review_command: list[str] | None = None,
    review_commands: list[list[str]] | None = None,
    review_command_timeout: int | None = None,
    api_key: str | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Supervisor:
    """Build the same configured engine for every product interface."""

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
    elif config.worker == "model_agent":
        if config.llm_credential_source == "session":
            if api_key is None:
                raise ConfigError("Re-enter the session API key before starting this model.")
            resolved_key = api_key
        elif config.llm_credential_source == "env":
            resolved_key = resolve_api_key(config.llm_api_key_env)
        else:
            resolved_key = ""
        worker = EmbeddedModelAgent(
            project_dir=project_dir,
            provider=OpenAICompatibleProvider(
                endpoint=config.llm_endpoint,
                model=config.llm_model,
                api_key=resolved_key,
                timeout=config.llm_timeout,
                retries=config.llm_retries,
                retry_delay=config.llm_retry_delay,
            ),
            model=config.llm_model,
            max_steps=config.llm_max_steps,
            cancel_requested=cancel_requested,
            secret_values=[resolved_key],
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
        review_commands=review_commands,
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
    review_commands: list[list[str]] | None = None,
    review_command_timeout: int | None = None,
) -> list[ReviewCriterion]:
    criteria: list[ReviewCriterion] = []
    timeout = (
        review_command_timeout
        if review_command_timeout is not None
        else config.review_command_timeout
    )
    if review_commands is not None:
        commands = review_commands
    else:
        command = review_command if review_command is not None else config.review_command
        commands = [command] if command else []
    for command in commands:
        if command:
            secret_env_names = (
                [config.llm_api_key_env] if config.llm_api_key_env else []
            )
            criteria.append(
                command_passes(
                    command,
                    cwd=project_dir,
                    timeout=timeout,
                    secret_env_names=secret_env_names,
                    covers=tuple(config.review_covers),
                )
            )
    if config.review_artifact:
        criteria.append(
            artifact_exists(
                project_dir,
                config.review_artifact,
                covers=tuple(config.review_covers),
            )
        )
    if config.review_file_changed:
        criteria.append(
            file_changed(
                project_dir,
                config.review_file_changed,
                covers=tuple(config.review_covers),
            )
        )
    if config.review_git_clean:
        criteria.append(
            git_clean(project_dir, covers=tuple(config.review_covers))
        )
    return criteria


def autonomy_policy_from_config(
    config: HarnessConfig,
    *,
    repeated_blocker_limit: int = 3,
    require_completion_claim: bool = True,
) -> AutonomyPolicy:
    return AutonomyPolicy(
        repeated_blocker_limit=repeated_blocker_limit,
        require_completion_claim=require_completion_claim,
        assurance_mode=AssuranceMode(config.assurance_mode),
        max_cycles=config.goal_max_cycles,
        max_elapsed_seconds=config.goal_max_elapsed_seconds,
        max_total_tokens=config.goal_max_total_tokens,
        max_provider_calls=config.goal_max_provider_calls,
        max_tool_calls=config.goal_max_tool_calls,
    )
