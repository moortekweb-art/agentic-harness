# Agentic Harness v0.6.5

This release hardens adapter boundaries and state artifact safety.

## Highlights

- `CodingAgentWorker` now returns structured failed `WorkerResult` objects for
  timeouts, missing executables, and transcript write failures.
- `TmuxWorker` now returns a structured failed result when `tmux` cannot start.
- `LocalLLMAdapter` now parses OpenAI-compatible responses defensively instead
  of assuming every nested field has the expected shape.
- `ArtifactStore.write_report()` now rejects caller-controlled report names that
  escape the goal artifact directory.
- State locking no longer imports `fcntl` with a bare top-level import. The lock
  path now uses `fcntl` when available and `msvcrt` when available.
- Fixed the stale README example that referenced v0.5.0.
- Documented the current PyPI name-availability blocker: `agentic-harness` is
  already used by an unrelated PyPI project, so publishing needs an owned
  distribution name or transfer before trusted publishing is enabled.
- Test coverage increased to 76 current-package tests.

## Verification

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m build
```
