# Release Checklist

Use this checklist for a v0.6.9 release.

## Before Tagging

- Confirm the working tree is clean except for intended release changes:

  ```bash
  git status --short
  ```

- Run local tests:

  ```bash
  python -m pip install -e ".[test]"
  python -m pytest tests/ -q
  python -m ruff check
  python -m mypy agentic_harness
  python -m compileall agentic_harness
  python -m agentic_harness.cli release-smoke
  ```

- Run CLI smoke checks:

  ```bash
  python -m agentic_harness.cli --help
  python -m agentic_harness.cli --project-dir /tmp/agentic-harness-smoke init
  python -m agentic_harness.cli --project-dir /tmp/agentic-harness-smoke doctor
  printf 'version: 1\nworker: noop\nallow_noop_success: true\n' > /tmp/agentic-harness-smoke/.agentic-harness/config.yml
  python -m agentic_harness.cli --project-dir /tmp/agentic-harness-smoke run "smoke goal"
  ```

- Smoke-check safe examples:

  ```bash
  python examples/local-llm/run_local_llm.py
  python examples/tmux-worker/tmux_worker_demo.py
  ```

- Check README links to examples, docs, license, and CI status.
- Confirm GitHub Actions CI is green on `main`.

## Tag and GitHub Release

Create and push the tag:

```bash
git tag v0.6.9
git push origin v0.6.9
```

Create the GitHub release:

```bash
gh release create v0.6.9 --title "v0.6.9" --notes-file docs/RELEASE_NOTES_0.6.9.md
```

## PyPI Publishing

PyPI publishing has an active trusted-publishing workflow at
`.github/workflows/publish.yml` for the `local-agentic-harness` distribution.
Complete the external trusted publisher setup documented in
`docs/PYPI_TRUSTED_PUBLISHING.md` before relying on release-triggered uploads.
