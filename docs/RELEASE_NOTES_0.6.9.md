# Agentic Harness v0.6.9

This release closes the remaining repo-side release-operations gaps from the
critique and adds the first built-in recipe layer for beginner workflows.

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
- Added built-in recipes for common workflows: `fix-tests`, `lint-fix`,
  `typecheck-fix`, `update-docs`, `changelog`, and `verify-tests`.
- Added `agentic-harness recipes`, `agentic-harness run-recipe`, and
  `agentic-harness init-agent` so users can run common supervised tasks without
  hand-writing YAML or prompt templates.
- Added first-class commands for every built-in recipe: `fix-tests`,
  `lint-fix`, `typecheck-fix`, `update-docs`, `changelog`, and
  `verify-tests`.
- Added `agentic-harness create-demo fix-tests` so wheel-installed users can
  generate the runnable fix-failing-tests demo without a source checkout.
- Added `agentic-harness run-demo fix-tests` so users can create the packaged
  demo, install its declared dependency, run the failing test, execute the
  harness fix, write the report, and verify the final passing test in one
  command.
- Generated demos include `requirements-dev.txt`, and the README/CI smoke path
  uses `run-demo` to install it before running pytest, so the demo does not
  depend on an undeclared local pytest install.
- Updated the fix-failing-tests demo and README to lead with the no-hidden-YAML
  easy path: `agentic-harness init shell` then `agentic-harness fix-tests`.
- Shell workers now write `.agentic-harness/runs/<goal-id>/shell-worker.log`
  so script-backed easy runs leave durable execution evidence.
- `agentic-harness report` now writes
  `.agentic-harness/runs/<goal-id>/report.md` and prints the report path in
  the plain-language output.
- Recipe commands now write the same markdown report artifact automatically,
  so `fix-tests`, `run-recipe`, `easy`, and `run-demo` leave an
  operator-readable handoff without a second command.
- Demo `init shell` config and demo recipe review gates now use the Python
  interpreter running the CLI, avoiding drift between dependency installation
  and pytest execution.
- Added `agentic-harness release-smoke`, which builds the wheel and sdist,
  installs each into a fresh virtual environment, verifies direct recipe
  commands, runs the packaged demo, and checks transcript/report artifacts.
- Expanded CI and release checklist wheel smokes to verify bundled recipe YAML
  and the packaged demo generator after installing the built wheel.

## Verification

- Local `pytest tests/ -q`, ruff, mypy, compile smoke, and package build pass.
- Installed-wheel smoke verifies `agentic-harness recipes`, recipe package data,
  and `load_recipe("fix-tests")`.
- The fix-failing-tests demo was generated and smoke-tested through
  `agentic-harness run-demo` from a temporary installed-wheel environment:
  tests failed before the run, `init shell` created config, `fix-tests`
  returned `done`, `status` and `report` worked, shell transcript and markdown
  report artifacts were present, and tests passed after the run.
- GitHub Actions CI is green on Ubuntu, Windows, and macOS for Python 3.11 and
  3.12.

## Publishing Note

The active Publish workflow is now checked in. The first PyPI upload still
requires external PyPI trusted-publisher configuration for:

- PyPI project: `local-agentic-harness`
- Repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`
