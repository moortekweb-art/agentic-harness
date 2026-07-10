# Agentic Harness v0.6.19

This patch release aligns the packaged demo runner with the beginner command
surface.

## Changes

- `agentic-harness run-demo fix-tests ...` now invokes bare
  `agentic-harness status` internally.
- Generated fix-tests demo README content now uses bare
  `agentic-harness status`.
- Demo tests now exercise the same command shown to beginners instead of the
  older `status --format text` spelling.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0619-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
