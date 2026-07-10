# Agentic Harness v0.6.15

This patch release brings bounded retry directly to recipe commands.

## Changes

- Direct recipe commands and `run-recipe` now accept
  `--until-done --max-attempts N`.
- Recipe bounded retry uses the recipe's own review gate, so `fix-tests
  --until-done` still means done only when the configured test recipe passes.
- Recipe `--until-done` writes the normal report artifact and keeps `--json`
  available for script consumers.
- Release smoke now verifies recipe `--until-done` from both installed wheel
  and installed sdist artifacts.

## Verification

- `python3 -m pytest -q` passes.
- `python -m ruff check` passes.
- `python -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0615-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
