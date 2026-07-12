# Release Checklist

Use this version-generic checklist for every release. The candidate version is
always read from `pyproject.toml`; never copy a previous tag or reuse a version
that has already reached PyPI.

Versioned notes and workflow links are historical receipts, not a source for
the next version. For example, the historical v0.7.2 notes at
`docs/RELEASE_NOTES_0.7.2.md` and the historical v0.7.0 records remain useful
evidence, but neither identifies the current candidate. Resolve every candidate
from package metadata and live registry readback.

## Local candidate

- Confirm the intended branch, commit, worktrees, and clean release tree:

  ```bash
  git branch --show-current
  git worktree list
  git status --short
  git log -1 --oneline
  ```

- Resolve the candidate version, tag, and release-notes path from package
  metadata:

  ```bash
  VERSION="$(python -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
  TAG="v${VERSION}"
  NOTES="docs/RELEASE_NOTES_${VERSION}.md"
  test -f "$NOTES"
  test "$(sed -n '1p' "$NOTES")" = "# Agentic Harness ${TAG}"
  printf 'candidate=%s tag=%s notes=%s\n' "$VERSION" "$TAG" "$NOTES"
  ```

- Confirm package metadata, candidate release notes, both entry points, and
  supported Python range agree:

  ```bash
  agentic-harness --version
  agentic-harness-gui --help
  python - <<'PY'
  import tomllib
  from pathlib import Path
  print(tomllib.loads(Path("pyproject.toml").read_text())["project"])
  PY
  ```

- Run the complete local verification stack:

  ```bash
  python -m pip install -e ".[test]"
  python -m pytest tests/ -q
  python -m ruff check
  python -m mypy agentic_harness
  python -m compileall agentic_harness
  node --check agentic_harness/gui/static/app.js
  node tests/frontend_token_race_test.js
  python -m agentic_harness.cli release-smoke --dist-dir dist
  ```

  `release-smoke` builds the wheel and sdist, runs Twine metadata checks,
  installs each artifact in a fresh environment, verifies both CLI entry
  points and packaged browser assets, runs strict autonomous and demo flows,
  and writes `dist/SHA256SUMS`.

- Run the real browser journey from the candidate:

  - setup with a scripted local OpenAI-compatible provider;
  - reject a blank goal;
  - start a file-changing goal and observe ordered activity;
  - inspect plan, current subgoal, checkpoint, checks, changed file, artifact,
    exact trusted result category, and durable history;
  - refresh during or after work;
  - stop a slow goal and confirm late completion is rejected;
  - verify desktop and narrow layouts have no overflow or page errors; and
  - verify the key field clears and no credential appears in URLs, storage,
    responses, events, exports, or evidence.

## GitHub protection before tagging

Repository-owner configuration is a release gate, not an optional follow-up:

- Protect the default branch or add an equivalent ruleset requiring the full
  CI workflow and review before merge.
- Configure the `pypi` environment with required reviewers and a deployment
  policy restricted to release tags.
- Configure the `github-release` environment with required reviewers and the
  same release-tag policy.
- Confirm the PyPI trusted-publisher identity names this repository,
  `.github/workflows/publish.yml`, and environment `pypi`.
- Inspect the published versions and confirm the candidate `VERSION` is unused
  on PyPI. An existing version must never be overwritten or reused:

  ```bash
  python -m pip index versions local-agentic-harness
  ```

- Confirm the exact candidate commit has a successful `push` CI run on the
  default branch.

See [PyPI trusted publishing](PYPI_TRUSTED_PUBLISHING.md).

## Tag-driven release

Only after the protections and local gates pass, create and push the annotated
tag from the verified default-branch commit:

```bash
VERSION="$(python -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
TAG="v${VERSION}"
git tag -a "$TAG" -m "Agentic Harness ${TAG}"
git push origin "$TAG"
```

The tag workflow then:

1. checks out the immutable triggering tag event SHA without persisting checkout credentials;
2. verifies tag/package version equality, default-branch ancestry, and a
   successful CI `push` run for the exact commit;
3. builds and release-smokes the distributions once;
4. keeps wheel/sdist-only PyPI inputs separate from checksums and release notes;
5. creates or updates a draft GitHub release and attaches the verified wheel,
   sdist, and `SHA256SUMS`;
6. publishes through the protected `pypi` environment using OIDC; and
7. makes the draft public only through the protected `github-release`
   environment after PyPI succeeds.

Do not manually publish the GitHub Release first. The draft-first order prevents
a failed validation or PyPI upload from leaving a broken public latest release.
The final release job is separate, so it can be retried without attempting to
republish an immutable PyPI version.

### Immutable-tag workflow recovery

Use the manual `Publish` dispatch only when an immutable release tag already
exists and its tag-triggered run failed before PyPI publication because of a
workflow defect. Merge the workflow repair through the protected default
branch, wait for CI on that exact default-branch commit, then dispatch the
workflow from the default branch with the existing tag as `release_tag` and
its independently audited full 40-character commit as `release_sha`.

The recovery run uses the protected default-branch workflow definition but
checks out the supplied commit SHA and builds that immutable tree. It requires
the selected tag to resolve to the same `HEAD`, requires tag/package version
equality, verifies default-branch ancestry, and requires successful
default-branch CI for the exact tagged commit. Never derive the audited SHA
from the recovery checkout, or move, delete, or recreate the release tag.

A manually dispatched run has a default-branch deployment ref even though it
builds the tag. If the protected `pypi` and `github-release` environments allow
only `v*` tag refs, temporarily add an exact default-branch deployment policy
for the approved recovery run. Keep required reviewers enabled, approve each
environment separately, and remove the temporary branch policies immediately
after the run reaches a terminal state.

## Post-release readback

- Resolve the released version and tag from the immutable checkout:

  ```bash
  VERSION="$(python -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
  TAG="v${VERSION}"
  ```

- Confirm the GitHub Release is public and contains the wheel, sdist, and
  `SHA256SUMS` matching the workflow artifact.
- Confirm PyPI metadata, Python requirement, project links, and both console
  scripts match the tag.
- Install from PyPI in a clean environment and repeat version, CLI, GUI-help,
  packaged-asset, and harmless loopback HTTP probes.
- Record the release workflow URL and readback evidence.

  ```bash
  gh release view "$TAG" --repo moortekweb-art/agentic-harness
  python -m pip index versions local-agentic-harness
  ```

If a product or artifact gate fails before PyPI publication, keep or remove the
draft and correct a new commit/version deliberately. If only the workflow
failed after an immutable tag was created, use the bounded recovery path above.
After PyPI publication, the version is immutable; never overwrite or reuse it.
