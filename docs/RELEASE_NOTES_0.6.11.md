# Agentic Harness v0.6.11

This patch release makes the release artifact set reproducible and easier to
verify before any PyPI publish attempt.

## Changes

- `agentic-harness release-smoke` writes a `SHA256SUMS` manifest next to the
  verified wheel and source distribution.
- Release smoke output prints the checksum manifest path so operators can
  verify the exact artifacts that passed the installed-artifact smoke tests.
- Release docs now describe the checksum manifest as part of the release gate.
- This release is intended to be tagged from the checksum-enabled tree rather
  than moving the earlier local `v0.6.10` tag.

## Verification

- `python3 -m pytest -q` passes with 635 tests.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-checksum-dist`
  passes from a clean throwaway virtual environment, including `twine check`,
  installed-artifact version checks, wheel smoke, sdist smoke, packaged demo,
  final demo tests, and release checksum generation.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.

## Publishing Note

The repository-side publish path is ready, but PyPI upload still requires the
external trusted publisher setup documented in `docs/PYPI_TRUSTED_PUBLISHING.md`.
