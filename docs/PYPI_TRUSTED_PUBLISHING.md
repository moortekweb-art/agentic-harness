# PyPI Trusted Publishing

Agentic Harness is buildable locally and GitHub releases attach verified wheel
and source distributions. PyPI publishing should use GitHub Actions trusted
publishing, which avoids storing a PyPI token in repository secrets.

The publish workflow is currently checked in as a template at
`docs/templates/publish.yml`. Copy it to `.github/workflows/publish.yml` from a
credential that has GitHub `workflow` permission, then configure the PyPI
trusted publisher below.

## Name Availability Blocker

As of 2026-07-04, the `agentic-harness` project name on PyPI is already used by
an unrelated project. Do not configure trusted publishing for this repository
under that PyPI project unless ownership or a project transfer has been resolved.

This repository now uses `moortek-agentic-harness` as the Python distribution
name while keeping the installed CLI command as `agentic-harness`.

## Required External Setup

Configure a PyPI trusted publisher for:

- PyPI project: `moortek-agentic-harness`
- Owner/repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`

After the workflow is installed and the external PyPI setup exists, publishing a
GitHub release runs the `Publish` workflow and uploads the distributions built
from that release.

## Manual Verification

```bash
python -m build --outdir /tmp/agentic-harness-dist
python -m pip index versions moortek-agentic-harness
gh release view v0.6.6 --repo moortekweb-art/agentic-harness
```

The publish workflow should not use `PYPI_TOKEN`, `username`, or `password`.
The publish job must keep `id-token: write` at job scope.
