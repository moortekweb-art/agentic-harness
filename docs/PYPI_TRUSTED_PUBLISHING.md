# PyPI Trusted Publishing

Agentic Harness is buildable locally and GitHub releases attach verified wheel
and source distributions. PyPI publishing should use GitHub Actions trusted
publishing, which avoids storing a PyPI token in repository secrets.

The publish workflow is checked in at `.github/workflows/publish.yml`.
`docs/templates/publish.yml` is retained only as a reference copy. The trusted
publisher described below is configured and was verified by the v0.6.26 upload.

Before uploading, the workflow installs the project with test extras and runs:

```bash
python -m agentic_harness.cli release-smoke --dist-dir dist
```

That command builds the wheel and sdist, runs `twine check`, installs both
artifacts in fresh virtual environments, runs the packaged demo from each
artifact, verifies the final demo tests, and writes `SHA256SUMS` beside the
verified artifacts. The workflow then copies only `*.whl` and `*.tar.gz` into
`pypi-dist/` for PyPI; `SHA256SUMS` remains release evidence and is not passed to
the PyPI upload action.

## Name Availability Blocker

As of 2026-07-04, the `agentic-harness` project name on PyPI is already used by
an unrelated project. Do not configure trusted publishing for this repository
under that PyPI project unless ownership or a project transfer has been resolved.

This repository now uses `local-agentic-harness` as the Python distribution
name while keeping the installed CLI command as `agentic-harness`.

## Required External Setup

The configured PyPI trusted publisher uses:

- PyPI project: `local-agentic-harness`
- Owner/repository: `moortekweb-art/agentic-harness`
- Workflow: `.github/workflows/publish.yml`
- Environment: `pypi`

The observed GitHub/PyPI trusted-publishing claims use:

- `sub`: `repo:moortekweb-art/agentic-harness:environment:pypi`
- `repository`: `moortekweb-art/agentic-harness`
- `repository_owner`: `moortekweb-art`
- `workflow_ref`: `moortekweb-art/agentic-harness/.github/workflows/publish.yml@<release-ref>`
- `ref`: the pushed release tag
- `environment`: `pypi`

Pushing an annotated or lightweight `v<version>` tag starts the `Publish`
workflow. The workflow checks out the immutable triggering event SHA, verifies
that the tag and package version identify that exact commit, requires trusted
default-branch CI for it, builds once, and stages a draft GitHub release. The
same verified artifacts are then published to PyPI before the protected
`github-release` job makes the draft public. There is deliberately no manual
dispatch path.

Before publishing v0.7.0, repository owners must configure both protected
GitHub environments:

- `pypi`, restricted to release tags and protected with the desired reviewers;
- `github-release`, with the same release-tag policy and final-publication
  reviewers.

The default branch and release tags also need repository rulesets that require
CI, prevent force updates/deletion, and make release tags immutable in normal
operation. The workflow's per-tag concurrency and event-SHA validation are
defense in depth; they do not replace those repository settings.

## Current Publish Status

`local-agentic-harness` v0.6.26 is public on PyPI. Workflow run `29074514346`
completed the build, wheel/sdist smoke tests, trusted-publishing exchange,
upload, and digital attestations successfully.

The first v0.6.26 release-triggered run failed before the trusted-publishing
exchange because the upload action tried to parse `dist/SHA256SUMS` as a Python
distribution. PR #4 added the dedicated `pypi-dist/` staging directory; the
successful recovery run verified that only the wheel and sdist are uploaded.

## Manual Verification

```bash
python -m pip install -e ".[test]"
python -m agentic_harness.cli release-smoke --dist-dir /tmp/agentic-harness-dist
python -m pip index versions local-agentic-harness
gh release view v0.6.26 --repo moortekweb-art/agentic-harness
gh run view 29074514346 --repo moortekweb-art/agentic-harness
```

The publish workflow should not use `PYPI_TOKEN`, `username`, or `password`.
The publish job must keep `id-token: write` at job scope.
