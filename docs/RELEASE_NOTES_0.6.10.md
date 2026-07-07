# Agentic Harness v0.6.10

This release turns the new recipe/easy-mode work into a cleaner first-run
product path and hardens the package publish path so releases upload only
smoke-tested artifacts.

## Changes

- Reworked the README opening so new users see the executable path first:
  `run-demo`, then `init shell`, `fix-tests`, `status`, and `report`.
- Aligned `agentic-harness quickstart` with the direct beginner flow:
  `init <backend>`, `fix-tests`, `status`, and `report`.
- Updated `start-here`, top-level help, `agents`, `next`, `report`, `selftest`,
  and no-backend guidance so beginner-facing output points to the direct recipe
  flow instead of the older `easy` / `init-agent` wording.
- Cleaned up the README recipe section so direct recipe commands come before
  the generic `run-recipe` entrypoint.
- Added `agentic-harness --version` and `agentic-harness version` so operators
  can confirm the installed package version from source checkouts, wheels, and
  sdists.
- Added a PyPI metadata gate to `agentic-harness release-smoke`: after building
  the wheel and sdist, it now runs `twine check` before installing and smoking
  each artifact.
- Added `twine>=5.1` to the test extra so release-smoke has the dependency it
  needs in local and CI release environments.
- Updated CI to run `python -m twine check dist/*` after package build.
- Updated the active PyPI publish workflow and template so publishing runs
  `agentic-harness release-smoke --dist-dir dist` and uploads the same verified
  artifacts only after release-smoke passes.
- `agentic-harness release-smoke` now writes a `SHA256SUMS` manifest beside the
  verified wheel and sdist artifacts.
- Updated PyPI/release docs to describe the release-smoke-gated publish path.

## Verification

- `python3 -m pytest -q` passes with 631 tests.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-publish-gate-dist`
  passes from a clean throwaway virtual environment, including `twine check`,
  installed-artifact version checks, wheel smoke, sdist smoke, packaged demo,
  final demo tests, and release checksum generation.

## Publishing Note

The repository-side publish path is ready for the `local-agentic-harness`
distribution. The remaining PyPI publication dependency is external trusted
publisher setup for:

- PyPI project: `local-agentic-harness`
- Repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`
