# Release Checklist

Use this checklist for a v0.6.3 release.

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
  python -m build --outdir /tmp/agentic-harness-dist
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
git tag v0.6.3
git push origin v0.6.3
```

Create the GitHub release:

```bash
gh release create v0.6.3 --title "v0.6.3" --notes-file docs/RELEASE_NOTES_0.6.3.md
```

## Future Manual Publishing

PyPI publishing is intentionally not part of this release checklist execution. Treat packaging and publishing to PyPI as a separate future/manual step after credentials, ownership, and release process are confirmed.
