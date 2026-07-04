# Agentic Harness v0.6.0

This release addresses the critique that the harness needed a turnkey path for
real coding agents, not only shell and API-level adapters.

## Highlights

- Added `CodingAgentWorker`, a generic adapter for Codex, Aider, OpenCode, and
  similar non-interactive coding-agent CLIs.
- Added `worker.type: coding_agent` config support with command templating for
  `{objective}` and `{goal_id}`.
- Coding-agent runs now save stdout/stderr transcripts as project-local
  artifacts under `.agentic-harness/runs/<goal-id>/`.
- Added a coding-agent example and a demo-script variant for:
  `agentic-harness run "fix failing tests"`.
- Updated real-world recipes to show Codex, Aider, and OpenCode command shapes
  paired with deterministic pytest review.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```

