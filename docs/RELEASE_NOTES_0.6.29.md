# Agentic Harness v0.6.29

This recovery patch fixes the release-smoke verifier regression that prevented
the v0.6.28 release workflow from reaching PyPI publication.

## Changes

- Installed wheel and sdist smoke tests now require the packaged demo transcript
  to contain the demo project's nested `.venv` Python interpreter.
- Demo subprocesses receive a deduplicated, process-scoped import path for the
  currently running Agentic Harness package, so nested virtual environments can
  execute an enclosing artifact-venv installation without installing from the
  network or modifying the nested environment.
- Regression coverage proves that the exact nested interpreter is accepted and
  that the outer artifact-smoke virtual-environment interpreter alone is rejected,
  while preserving existing import-path entries without duplicating the harness root.

## Release recovery

v0.6.29 supersedes the partial v0.6.28 PyPI publication attempt. The immutable
v0.6.28 Git tag and GitHub Release remain unchanged; v0.6.28 did not reach PyPI.

## Verification

- Full Python tests, Ruff, strict mypy, compileall, and release checklist checks.
- Wheel and sdist build, Twine metadata, isolated installation, packaged demo,
  nested demo virtual environment, and checksum verification through release smoke.
