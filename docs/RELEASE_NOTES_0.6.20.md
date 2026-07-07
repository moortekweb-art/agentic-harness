# Agentic Harness v0.6.20

This patch release removes stale setup friction from the first-run demo path.

## Changes

- The checked-in fix-tests demo README no longer tells beginners to run
  `agentic-harness init shell` before `agentic-harness fix-tests`.
- The packaged demo generator now documents the direct auto-config path:
  install test requirements, observe the failing test, run `fix-tests`, inspect
  status/report, then verify tests pass.
- `agentic-harness quickstart` now prints the full generated-demo path when no
  coding-agent backend is on `PATH`, including dependency install and before /
  after pytest checks.
- Demo tests now prove `agentic-harness fix-tests` creates the demo shell config
  itself and completes the failure-fix-review cycle without a manual init step.

## Verification

- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0620-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
