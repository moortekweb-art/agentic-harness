# PyPI Trusted Publishing

Agentic Harness is buildable locally and GitHub releases attach verified wheel
and source distributions. PyPI publishing should use GitHub Actions trusted
publishing, which avoids storing a PyPI token in repository secrets.

The publish workflow is checked in at `.github/workflows/publish.yml`.
`docs/templates/publish.yml` is retained only as a reference copy. The trusted
publisher configuration is described below. Determine current publication
status from PyPI, the matching GitHub release, and the workflow run for the
candidate tag; do not infer it from a historical receipt.

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

## Distribution name

The `agentic-harness` project name on PyPI belongs to an unrelated project. Do
not configure trusted publishing for this repository under that name.

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
- `ref`: normally the pushed release tag; the bounded recovery path uses the
  protected default branch
- `environment`: `pypi`

Pushing an annotated or lightweight `v<version>` tag starts the `Publish`
workflow. The workflow checks out the immutable triggering event SHA, verifies
that the tag and package version identify that exact commit, requires trusted
default-branch CI for it, builds once, and stages a draft GitHub release. The
same verified artifacts are then published to PyPI before the protected
`github-release` job makes the draft public.

The manual dispatch is an immutable-tag recovery path, not an alternate way to
choose release contents. It is used only after a tag-triggered run fails before
PyPI publication because the workflow itself needs repair. The repaired
workflow must first merge through the protected default branch. A recovery run
requires both `release_tag` and an independently audited full 40-character
`release_sha`, checks out that SHA, proves the tag resolves to the same `HEAD`,
and applies the same version, ancestry, exact-CI, build, artifact, and
environment gates. The expected SHA must not be derived from the recovery
checkout, and the tag must not be moved or recreated.

## Protected environments and steady state

Repository owners must keep both publishing environments protected:

- `pypi`, with a required reviewer and a deployment policy restricted to
  release tags;
- `github-release`, with a required reviewer and the same release-tag policy.

The required steady state has:

- required reviewer `moortekweb-art`; and
- exactly one deployment policy on each environment: tag pattern `v*`.

No default-branch deployment policy remains. This tag-only policy is
intentional; a normal release must originate from a release tag.

GitHub evaluates an environment deployment policy against the workflow-run
ref, not the ref checked out by a later step. A recovery dispatch therefore
uses a temporary exact-default-branch deployment policy on both environments.
Required reviewers remain enabled. Remove those temporary branch policies as
soon as the recovery run finishes; the normal steady state is the `v*` tag
policy only.

The default branch and release tags also need repository rulesets that require
CI, prevent force updates/deletion, and make release tags immutable in normal
operation. The workflow's per-tag concurrency and event-SHA validation are
defense in depth; they do not replace those repository settings.

## Historical publication receipt: v0.7.0

The v0.7.0 publication is preserved as a dated receipt; use PyPI and GitHub
readback to determine the latest version at the time of a future release.
[`local-agentic-harness` v0.7.0](https://pypi.org/project/local-agentic-harness/0.7.0/)
was published on PyPI with a matching
[GitHub release](https://github.com/moortekweb-art/agentic-harness/releases/tag/v0.7.0).
Recovery workflow run
[`29159578285`](https://github.com/moortekweb-art/agentic-harness/actions/runs/29159578285)
completed validation, wheel/sdist build and smoke tests, draft-release staging,
the trusted-publishing exchange, PyPI upload, digital attestations, and final
GitHub release publication.

The recovery run used the protected `main` workflow definition after
[PR #10](https://github.com/moortekweb-art/agentic-harness/pull/10) repaired the
initial tag-triggered workflow defect. It checked out the audited v0.7.0 tag
commit, proved tag/package/SHA equality and default-branch ancestry, and
required successful default-branch CI for that exact release commit before
building. After the run completed, both environments were restored to their
required reviewer plus only the `v*` tag deployment policy.

The first v0.6.26 release-triggered run failed before the trusted-publishing
exchange because the upload action tried to parse `dist/SHA256SUMS` as a Python
distribution. PR #4 added the dedicated `pypi-dist/` staging directory; the
successful recovery run verified that only the wheel and sdist are uploaded.

## Manual Verification

```bash
VERSION="$(python -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
TAG="v${VERSION}"
python -m pip install -e ".[test]"
python -m agentic_harness.cli release-smoke --dist-dir /tmp/agentic-harness-dist
python -m pip index versions local-agentic-harness
gh release view "$TAG" --repo moortekweb-art/agentic-harness
gh run list --workflow publish.yml --branch "$TAG" --limit 5 \
  --repo moortekweb-art/agentic-harness
```

The publish workflow should not use `PYPI_TOKEN`, `username`, or `password`.
The publish job must keep `id-token: write` at job scope.
