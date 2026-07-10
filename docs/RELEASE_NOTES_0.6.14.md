# Agentic Harness v0.6.14

This patch release makes the bounded execution driver produce a durable handoff
artifact by default.

## Changes

- `agentic-harness run-until-done` now writes
  `.agentic-harness/runs/<goal-id>/report.md` after the drive loop finishes.
- Default `run-until-done` output is now the same plain operator summary used by
  `agentic-harness report`; scripts can pass `--json` for the final goal JSON.
- Release smoke now verifies `run-until-done` from both installed wheel and
  installed sdist artifacts, including report artifact creation.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0614-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
