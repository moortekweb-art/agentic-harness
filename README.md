# Agentic Harness

A local-first goal execution harness with a small core state machine, pluggable execution adapters, deterministic review gates, and a project-local CLI.

This branch is the clean rebuild. The old Node1/Hermes extraction is preserved under `legacy/` as reference material, but the package code is intentionally server-agnostic.

## Architecture

### Core Engine

`agentic_harness/core/`

- Versioned goal state: `pending -> planning -> in_progress -> review -> done/failed`
- Project-local artifact store: `.agentic-harness/runs/<goal-id>/state.json`
- Deterministic review criteria with typed pass/fail results
- Auto-continue loop guard
- Typed harness errors

### Adapters

`agentic_harness/adapters/`

- `ShellWorker` for subprocess execution
- `TmuxWorker` for detached interactive sessions
- `GitHubActionsAdapter` for workflow dispatch
- `LocalLLMAdapter` for OpenAI-compatible local endpoints

Adapters are plugins from the core engine's perspective. The supervisor only depends on the `Worker` protocol.

### CLI

```bash
agentic-harness init
agentic-harness start "ship a feature"
agentic-harness status
agentic-harness continue
agentic-harness review
agentic-harness repair
agentic-harness doctor
```

Config lives in `.agentic-harness/config.yml` and is intentionally gitignored.

## Install Locally

```bash
pipx install .
```

For development:

```bash
python -m pytest tests/ -q
python -c "from agentic_harness import Goal, Supervisor, Worker"
python -m agentic_harness.cli init
python -m agentic_harness.cli doctor
```

## Public API

```python
from agentic_harness import Goal, Supervisor, Worker
```

## Legacy Reference

The original extracted Node1 harness scripts and tests remain under:

- `legacy/scripts/`
- `legacy/tests/`

They are not imported by the clean package.
