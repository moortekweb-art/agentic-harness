# Agentic Harness v0.6.2

This release fixes adapter and CLI sharp edges found during a stricter critique
pass.

## Highlights

- GitHub Actions wait mode now requests `return_run_details: true`, so modern
  workflow dispatch responses can return exact run IDs/URLs instead of forcing
  recent-run polling.
- `ShellWorker` now returns structured `WorkerResult` failures for timeouts and
  missing executables instead of raising subprocess exceptions.
- `TmuxWorker` shell-quotes `{goal_id}` and `{objective}` placeholders, and the
  tmux examples now prefer passing only `{goal_id}`.
- `continue` validates that an active goal exists before recording loop-guard
  events, so accidental no-goal continues do not poison `guard.json`.
- Review helpers now fail criteria cleanly when commands or `git` cannot start.
- CLI expected errors, including no active goal, loop-guard trips, and invalid
  transitions, return structured JSON with exit code 2.
- Example coverage now parses documented harness YAML snippets with
  `load_config()` instead of only checking for marker strings.
- Test coverage increased to 63 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
