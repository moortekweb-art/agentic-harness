# Examples

These examples are intentionally small and safe to inspect.

- [shell-worker](shell-worker/) runs a project-local Python worker through the shell adapter.
- [coding-agent](coding-agent/) wraps Codex, Aider, OpenCode, or a similar CLI and
  captures a transcript before deterministic review.
- [local-llm](local-llm/) shows the OpenAI-compatible local LLM adapter without calling an endpoint by default.
- [tmux-worker](tmux-worker/) shows the tmux adapter without starting tmux by default.
- [real-world-recipes.md](real-world-recipes.md) gives copyable config patterns for shell,
  tmux, local LLM, GitHub Actions, and review-command workflows.

Each example includes run commands, expected output, and safety notes.
