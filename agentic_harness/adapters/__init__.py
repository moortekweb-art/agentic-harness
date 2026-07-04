"""Execution adapters for agentic-harness."""

from agentic_harness.adapters.github_actions import GitHubActionsAdapter
from agentic_harness.adapters.local_llm import LocalLLMAdapter
from agentic_harness.adapters.shell import ShellWorker
from agentic_harness.adapters.tmux import TmuxWorker

__all__ = ["GitHubActionsAdapter", "LocalLLMAdapter", "ShellWorker", "TmuxWorker"]

