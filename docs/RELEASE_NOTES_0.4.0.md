# Agentic Harness v0.4.0

This release closes the next maturity layer after v0.3.0.

## Highlights

- Config loading now uses PyYAML and supports both flat keys and grouped
  sections for worker, review, GitHub Actions, and local LLM settings.
- GitHub Actions wait mode now polls `workflow_dispatch` runs created after the
  dispatch request, which avoids reporting older completed runs on the same
  branch.
- CI now runs tests, ruff, mypy, compile smoke, package build, wheel install,
  and CLI smoke checks.
- Added real-world recipes for shell, tmux, local LLM, GitHub Actions, and
  config-driven review-command workflows.
- Package metadata now declares the PyYAML runtime dependency and the test
  extra includes build, ruff, mypy, pytest, and PyYAML typing support.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```

