# Agentic Harness v0.6.21

This patch release aligns the terminal recording script and examples index with
the shipped first-run product path.

## Changes

- `docs/demo-script.md` now starts with `agentic-harness run-demo fix-tests`
  and the generated-demo `fix-tests` auto-config workflow instead of teaching
  users to hand-write `.agentic-harness/config.yml`.
- The coding-agent recording variant now uses `init-agent codex --force`,
  `run-recipe fix-tests --explain`, `fix-tests`, `status`, and `report`.
- `examples/README.md` now describes the fix-tests demo as direct
  `agentic-harness fix-tests` auto-config plus pytest review.
- Example tests now prevent the terminal demo script from reintroducing
  copy-pasted config YAML as the beginner path.

## Verification

- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0621-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
