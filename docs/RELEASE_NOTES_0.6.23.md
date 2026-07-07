# Agentic Harness v0.6.23

This patch release makes plain objective execution use the same auto-configured
backend path as direct recipes.

## Changes

- `agentic-harness run "objective"` now creates a backend config automatically
  when no project config exists and Codex, CodeWhale, OpenCode, or Aider is
  available.
- `agentic-harness run-until-done "objective"` now does the same auto-config
  before starting the bounded retry loop.
- Generated demo projects still auto-select their packaged shell worker.
- If no config and no backend are available, plain `run` now fails before
  writing failed goal state and points the user to `quickstart` or `run-demo`.
- Release smoke now verifies installed wheel and sdist artifacts can run a
  fresh-project auto-config path using a deterministic fake Codex executable.

## Verification

- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0623-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
