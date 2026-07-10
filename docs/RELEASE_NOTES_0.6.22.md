# Agentic Harness v0.6.22

This patch release makes the default setup command create a usable backend
config when one is available.

## Changes

- Bare `agentic-harness init` now auto-selects the preferred detected backend
  from Codex, CodeWhale, OpenCode, or Aider instead of always creating a `noop`
  placeholder.
- If no supported coding-agent backend is on `PATH`, bare `init` still creates
  the safe `noop` placeholder and points the user to `agentic-harness
  quickstart`.
- `agentic-harness next` on a fresh project now recommends direct
  `agentic-harness fix-tests` when a backend is available, including the
  backend that will be auto-configured.
- `agentic-harness next` without a backend points to `quickstart` and the
  packaged shell demo instead of telling beginners to run a second init command.
- README configuration docs now describe the auto-detecting `init` behavior.

## Verification

- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0622-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
