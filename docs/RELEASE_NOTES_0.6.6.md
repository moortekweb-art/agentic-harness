# Agentic Harness v0.6.6

This release removes the PyPI name-collision blocker from the repository
metadata.

## Highlights

- Renamed the Python distribution from `agentic-harness` to
  `moortek-agentic-harness` because `agentic-harness` is already used by an
  unrelated PyPI project.
- Kept the installed CLI command as `agentic-harness`.
- Updated the PyPI trusted-publishing template and runbook for
  `moortek-agentic-harness`.
- Confirmed `pip index versions moortek-agentic-harness` reports no matching
  distribution before first publish.
- Test coverage increased to 77 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
