# Agentic Harness v0.6.8

This release renames the Python distribution to the preferred PyPI-facing name.

## Highlights

- Renamed the Python distribution from `moortek-agentic-harness` to
  `local-agentic-harness`.
- Kept the GitHub repository name as `agentic-harness`.
- Kept the installed CLI command as `agentic-harness`.
- Updated the PyPI trusted-publishing template, runbook, and package metadata
  tests for `local-agentic-harness`.
- Confirmed `pip index versions local-agentic-harness` reports no matching
  distribution before first publish.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
