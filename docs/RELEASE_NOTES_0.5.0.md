# Agentic Harness v0.5.0

This release hardens GitHub Actions workflow tracking for production use.

## Highlights

- GitHub Actions dispatch requests now send `X-GitHub-Api-Version:
  2026-03-10`, which supports dispatch responses containing workflow run ids
  and URLs.
- When GitHub returns a `run_url`, `GitHubActionsAdapter` waits on that exact
  run instead of inferring from the workflow run list.
- Older GitHub API responses without a returned run URL still fall back to the
  v0.4.0 workflow_dispatch + created-after polling path.
- Config now exposes `github_api_version` for GitHub Enterprise or future API
  compatibility.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```

