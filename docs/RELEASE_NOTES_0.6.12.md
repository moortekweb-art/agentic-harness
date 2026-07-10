# Agentic Harness v0.6.12

This patch release removes another first-run step from recipe workflows.

## Changes

- Direct recipe commands such as `agentic-harness fix-tests` now create
  `.agentic-harness/config.yml` automatically when no config exists and a
  supported coding backend is available.
- Packaged demos auto-select the bundled shell mock worker, so users can run
  `agentic-harness fix-tests` immediately after `create-demo` without a
  separate `init shell` step.
- `run-demo` now exercises that direct recipe path instead of initializing the
  demo explicitly before running `fix-tests`.
- Quickstart and README examples now lead with the direct recipe path while
  keeping `init` documented for explicit backend selection.
- The no-backend case fails closed without creating a placeholder config.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0612-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
