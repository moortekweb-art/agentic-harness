# Release Checklist

Use this checklist for the v0.7.0 release.

## Local candidate

- Confirm the intended branch, commit, worktrees, and clean release tree:

  ```bash
  git branch --show-current
  git worktree list
  git status --short
  git log -1 --oneline
  ```

- Confirm package metadata, release notes, both entry points, and supported
  Python range agree on v0.7.0:

  The canonical notes file is `docs/RELEASE_NOTES_0.7.0.md`.

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
    final accepted result, and durable history;
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
- Confirm version `0.7.0` is unused on PyPI.
- Confirm the exact candidate commit has a successful `push` CI run on the
  default branch.

See [PyPI trusted publishing](PYPI_TRUSTED_PUBLISHING.md).

## Tag-driven release

Only after the protections and local gates pass, create and push the annotated
tag from the verified default-branch commit:

```bash
git tag -a v0.7.0 -m "Agentic Harness v0.7.0"
git push origin v0.7.0
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

## Post-release readback

- Confirm the GitHub Release is public and contains the wheel, sdist, and
  `SHA256SUMS` matching the workflow artifact.
- Confirm PyPI metadata, Python requirement, project links, and both console
  scripts match the tag.
- Install from PyPI in a clean environment and repeat version, CLI, GUI-help,
  packaged-asset, and harmless loopback HTTP probes.
- Record the release workflow URL and readback evidence.

If a gate fails before PyPI publication, keep or remove the draft and correct a
new commit/version deliberately. After PyPI publication, the version is
immutable; never overwrite or reuse it.
