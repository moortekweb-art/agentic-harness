# Agentic Harness v0.6.18

This patch release makes `agentic-harness status` match the beginner and demo
workflow: human-readable by default.

## Changes

- `agentic-harness status` now prints the plain text operator status by
  default.
- `agentic-harness status --format json` remains available for scripts and
  compatibility.
- Generated demo instructions and README examples now use bare
  `agentic-harness status`.
- Release smoke now verifies that installed wheel and sdist artifacts return
  text output from bare `status`.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0618-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
