# Agentic Harness v0.6.3

This release adds a small operator-facing status view.

## Highlights

- Added `agentic-harness status --format text` for a compact human-readable
  goal summary while keeping JSON as the default automation format.
- Text status reports the goal ID, objective, state, worker result, review
  result, error, and artifacts when present.
- Test coverage increased to 65 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
