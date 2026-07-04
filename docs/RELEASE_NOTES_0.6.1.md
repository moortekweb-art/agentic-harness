# Agentic Harness v0.6.1

This release continues the critique cleanup by tightening project-local state
handling for real agent runs.

## Highlights

- Added a non-blocking `.agentic-harness/state.lock` around mutating supervisor
  operations so concurrent CLI invocations do not write state at the same time.
- `agentic-harness start` now refuses to overwrite an unfinished active goal.
- `continue` now persists `in_progress` before the worker runs, so live status
  reflects active execution instead of stale planning state.
- CLI-level harness errors now return structured JSON with exit code 2 instead
  of an unhandled traceback.
- Test coverage increased to 51 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
