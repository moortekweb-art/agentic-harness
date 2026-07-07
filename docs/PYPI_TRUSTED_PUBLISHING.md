# PyPI Trusted Publishing

Agentic Harness is buildable locally and GitHub releases attach verified wheel
and source distributions. PyPI publishing should use GitHub Actions trusted
publishing, which avoids storing a PyPI token in repository secrets.

The publish workflow is checked in at `.github/workflows/publish.yml`.
`docs/templates/publish.yml` is retained only as a reference copy. Configure the
PyPI trusted publisher below before expecting release publishes to succeed.

Before uploading, the workflow installs the project with test extras and runs:

```bash
python -m agentic_harness.cli release-smoke --dist-dir dist
```

That command builds the wheel and sdist, runs `twine check`, installs both
artifacts in fresh virtual environments, runs the packaged demo from each
artifact, verifies the final demo tests, and writes `SHA256SUMS` beside the
verified artifacts. The PyPI publish action uploads the same `dist/` artifacts
only after that gate passes.

## Name Availability Blocker

As of 2026-07-04, the `agentic-harness` project name on PyPI is already used by
an unrelated project. Do not configure trusted publishing for this repository
under that PyPI project unless ownership or a project transfer has been resolved.

This repository now uses `local-agentic-harness` as the Python distribution
name while keeping the installed CLI command as `agentic-harness`.

## Required External Setup

Configure a PyPI trusted publisher for:

- PyPI project: `local-agentic-harness`
- Owner/repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`

The observed GitHub/PyPI trusted-publishing claims from the first `v0.6.9`
release attempt were:

- `sub`: `repo:moortekweb-art/agentic-harness:environment:pypi`
- `repository`: `moortekweb-art/agentic-harness`
- `repository_owner`: `moortekweb-art`
- `workflow_ref`: `moortekweb-art/agentic-harness/.github/workflows/publish.yml@refs/tags/v0.6.9`
- `ref`: `refs/tags/v0.6.9`
- `environment`: `pypi`

After the external PyPI setup exists, publishing a GitHub release runs the
`Publish` workflow and uploads the release-smoke-verified distributions built
from that release.

## Current Publish Status

The active workflow ran on `v0.6.9` and reached the PyPI trusted-publishing
exchange. PyPI rejected it with `invalid-publisher`, meaning the GitHub workflow
is active but PyPI does not yet have a matching trusted publisher configured for
this project.

After configuring the PyPI trusted publisher, publish a new release tag such as
`v0.6.13` so the upload includes the release-smoke-gated publish workflow and
checksum-manifest generation.

## Manual Verification

```bash
python -m pip install -e ".[test]"
python -m agentic_harness.cli release-smoke --dist-dir /tmp/agentic-harness-dist
python -m pip index versions local-agentic-harness
gh release view v0.6.9 --repo moortekweb-art/agentic-harness
gh release view v0.6.10 --repo moortekweb-art/agentic-harness
gh release view v0.6.11 --repo moortekweb-art/agentic-harness
gh release view v0.6.12 --repo moortekweb-art/agentic-harness
gh release view v0.6.13 --repo moortekweb-art/agentic-harness
gh run view 28703761225 --repo moortekweb-art/agentic-harness --log-failed
```

The publish workflow should not use `PYPI_TOKEN`, `username`, or `password`.
The publish job must keep `id-token: write` at job scope.
