# Agentic Harness v0.6.16

This patch release makes recipe and run reports answer the beginner question:
what changed?

## Changes

- Goals now capture a bounded workspace snapshot when they start.
- `run-until-done`, direct recipe commands, `run-recipe`, `report`, and report
  artifacts now summarize changed files from that baseline.
- Packaged demo output now shows the actual changed file, for example
  `Changed: 1 file` and `- modified calculator.py`.
- Workspace snapshots exclude harness state, VCS internals, caches,
  environments, build output, and dependency folders.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0616-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
