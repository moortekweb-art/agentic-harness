# Release Checklist

Use this checklist for a v0.1.0 release.

## Before Tagging

- Confirm the working tree is clean except for intended release changes:

  ```bash
  git status --short
  ```

- Run local tests:

  ```bash
  python -m pytest tests/ -q
  ```

- Run CLI smoke checks:

  ```bash
  python -m agentic_harness.cli --help
  python -m agentic_harness.cli --project-dir /tmp/agentic-harness-smoke init
  python -m agentic_harness.cli --project-dir /tmp/agentic-harness-smoke doctor
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
git tag v0.1.0
git push origin v0.1.0
```

Create the GitHub release:

```bash
gh release create v0.1.0 --title "v0.1.0" --notes-file docs/RELEASE_CHECKLIST.md
```

## Future Manual Publishing

PyPI publishing is intentionally not part of this release checklist execution. Treat packaging and publishing to PyPI as a separate future/manual step after credentials, ownership, and release process are confirmed.

