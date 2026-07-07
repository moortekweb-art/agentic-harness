# Agentic Harness v0.6.25

This patch release hardens local run artifacts so obvious secrets are redacted
before they are written to disk.

## Changes

- Added a shared `redact_secrets()` helper for common secret-shaped values:
  OpenAI/Anthropic-style keys, GitHub tokens, bearer tokens, password/API-key
  assignments, and URL userinfo credentials.
- Shell-worker transcripts now redact secret-shaped command/output text before
  writing `.agentic-harness/runs/<goal-id>/shell-worker.log`.
- Coding-agent transcripts now redact secret-shaped command/objective/output
  text before writing `.agentic-harness/runs/<goal-id>/coding-agent.log`.
- Markdown reports and JSON state artifacts written through `ArtifactStore` now
  pass through the same redaction boundary.
- Added regression tests for report, shell transcript, and coding-agent
  transcript redaction.

## Verification

- `python3 -m pytest tests/test_adapters.py tests/test_core_engine.py -q`
  passes.
- `python3 -m ruff check agentic_harness/core/redaction.py agentic_harness/core/artifacts.py agentic_harness/adapters/coding_agent.py agentic_harness/adapters/shell.py tests/test_adapters.py tests/test_core_engine.py`
  passes.
- `python3 -m mypy agentic_harness` passes.
- `python3 -m pytest -q` passes.
- `python3 -m ruff check` passes.
- `python3 -m compileall agentic_harness` passes.
- `agentic-harness --version` and `agentic-harness version` report
  `agentic-harness 0.6.25`.
- `agentic-harness release-smoke --dist-dir /tmp/agentic-harness-0625-dist`
  passes from a clean throwaway virtual environment.
- `sha256sum -c SHA256SUMS` passes from the generated dist directory.
