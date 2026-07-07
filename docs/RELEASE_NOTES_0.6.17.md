# Agentic Harness v0.6.17

This patch release makes `agentic-harness run "objective"` a first-class
operator workflow instead of a JSON-only command.

## Changes

- `agentic-harness run "objective"` now writes the normal
  `.agentic-harness/runs/<goal-id>/report.md` artifact.
- Default `run` output now prints the plain operator summary with result,
  objective, review status, report path, and changed-file summary when
  available.
- `agentic-harness run "objective" --json` preserves the previous final goal
  JSON output for scripts.
- Release smoke now verifies the plain `run` command from both installed wheel
  and installed sdist artifacts.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0617-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
