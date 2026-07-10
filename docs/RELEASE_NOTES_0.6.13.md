# Agentic Harness v0.6.13

This patch release adds a bounded execution driver and tightens the direct
recipe auto-config safety checks.

## Changes

- Added `agentic-harness run-until-done [objective] --max-attempts N`, which
  starts or resumes a goal, retries failed attempts through the existing
  restart path, runs review, and exits successfully only when the goal reaches
  `done`.
- Direct recipe auto-config now treats a project as the packaged shell demo
  only when the full demo file set is present, not just because a file named
  `mock_coding_agent.py` exists.
- Kept direct recipes fail-closed when no config and no supported backend are
  available.

## Verification

- `python3 -m pytest -q` passes with 641 tests.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0613-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
