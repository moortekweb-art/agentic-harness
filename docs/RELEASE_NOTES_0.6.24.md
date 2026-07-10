# Agentic Harness v0.6.24

This patch release makes the generated Codex backend usable from fresh project
directories that are not Git repositories.

## Changes

- Generated Codex backend configs now run `codex exec --skip-git-repo-check
  "{objective}"`.
- `agentic-harness init codex`, bare `agentic-harness init` when Codex is the
  detected backend, and fresh-project auto-configured `run` paths all inherit
  that safer default.
- README and example Codex config snippets now match the generated config.
- CLI tests now assert the generated Codex command includes the non-git project
  compatibility flag.

## Verification

- `python3 -m pytest tests/test_cli.py tests/test_config_load.py tests/test_examples.py -q`
  passes.
- `python3 -m ruff check agentic_harness/core/config.py agentic_harness/cli.py tests/test_cli.py`
  passes.
- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0624-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
- Generated `init codex` config was smoke-checked and includes
  `--skip-git-repo-check`.
