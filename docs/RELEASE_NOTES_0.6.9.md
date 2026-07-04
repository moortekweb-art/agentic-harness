# Agentic Harness v0.6.9

This release closes the remaining repo-side release-operations gaps from the
critique.

## Changes

- Activated `.github/workflows/publish.yml` for PyPI trusted publishing of the
  `local-agentic-harness` distribution.
- Expanded CI to Linux, Windows, and macOS across Python 3.11 and 3.12.
- Made wheel-install and CLI smoke checks portable across GitHub Actions
  runners.
- Normalized recorded artifact paths to POSIX-style project-local strings on
  Windows.
- Made shell missing-executable failures include the executable name in the
  structured error output.
- Updated README and release docs with the PyPI install command:
  `pipx install local-agentic-harness`.

## Verification

- Local `pytest tests/ -q`, ruff, mypy, compile smoke, and package build pass.
- GitHub Actions CI is green on Ubuntu, Windows, and macOS for Python 3.11 and
  3.12.

## Publishing Note

The active Publish workflow is now checked in. The first PyPI upload still
requires external PyPI trusted-publisher configuration for:

- PyPI project: `local-agentic-harness`
- Repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`
