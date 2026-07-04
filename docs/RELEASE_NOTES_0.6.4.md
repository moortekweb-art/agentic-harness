# Agentic Harness v0.6.4

This release closes two adoption gaps from the critique: PyPI publishing
readiness and a runnable fix-failing-tests demo.

## Highlights

- Added `docs/templates/publish.yml`, a PyPI trusted publishing workflow
  template using `pypa/gh-action-pypi-publish@release/v1`.
- Added `docs/PYPI_TRUSTED_PUBLISHING.md` with the required external PyPI
  trusted-publisher setup and the GitHub `workflow` permission needed to
  install the active workflow.
- Added `examples/fix-failing-tests-demo/`, a self-contained
  `agentic-harness run "fix failing tests"` demo with a coding-agent worker and
  pytest review gate.
- Added repository artwork under `docs/assets/` for GitHub/readme flair.
- Added explicit Michael / Moortekweb attribution in the MIT license metadata
  and `AUTHORS.md`.
- Test coverage increased to 69 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
