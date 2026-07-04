# Agentic Harness

[![CI](https://github.com/moortekweb-art/agentic-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/moortekweb-art/agentic-harness/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A small Python harness for running long-lived agent goals without turning your local scripts into a tangled control plane.

Agentic Harness gives you a project-local goal loop: start a goal, execute it through an adapter, save artifacts, run deterministic review, and stop before auto-continue loops get weird.

## Project Links

- [Examples](examples/) include shell, local LLM, and tmux worker examples.
- [Release checklist](docs/RELEASE_CHECKLIST.md) documents the v0.1.0 release checks.
- [Attraction plan](ATTRACTION_PLAN.md) captures public project positioning and follow-up ideas.
- [CI workflow](.github/workflows/ci.yml) runs tests and CLI smoke checks on push and pull requests.

## Quick Start

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
agentic-harness init
agentic-harness start "write a changelog for the last three commits"
agentic-harness continue && agentic-harness review
```

## Why This Exists

Most agent tooling lands in one of two places:

- Frameworks that are flexible but abstract enough that you still need to build the operational loop yourself.
- Internal scripts that work on one machine, with one naming scheme, one set of paths, and one operator.

Agentic Harness is the middle ground: a small state machine, adapter interface, artifact store, CLI, and deterministic review contract. It is meant for developers who already have useful local tools and want a safer way to run them as repeatable goals.

## How It Works

```text
goal text
   |
   v
pending -> planning -> in_progress -> review -> done
                         |             |
                         v             v
                       failed <----- failed
```

```text
CLI ──> Supervisor ──> Worker adapter ──> local tool / tmux / CI / LLM
          |
          ├── state.json
          ├── markdown reports
          ├── deterministic review result
          └── loop guard
```

The core package has no systemd, Cloudflare, GPU, or server-specific assumptions. Runtime state lives in `.agentic-harness/` inside your project.

## Features

- Deterministic review gates: pass/fail criteria are code, not model vibes.
- Artifact-first execution: every goal writes structured JSON state and review data.
- Loop guard: auto-continue has a project-local circuit breaker persisted at
  `.agentic-harness/guard.json`, so repeated CLI invocations share the same
  safety window.
- Adapter system: shell, tmux, GitHub Actions, and OpenAI-compatible local LLM adapters are included.
- Project-local config: no hardcoded absolute paths.
- Small public API: `Goal`, `Supervisor`, and `Worker`.

## Installation

Install as a CLI with pipx:

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
```

For development:

```bash
git clone https://github.com/moortekweb-art/agentic-harness.git
cd agentic-harness
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

## Usage Examples

See [examples/](examples/) for complete project-local examples with READMEs, safety notes, and expected output.

### Shell Worker

`.agentic-harness/config.yml`

```yaml
version: 1
worker: shell
shell_command:
  - python
  - -c
  - "import os; print('goal:', os.environ['AGENTIC_HARNESS_OBJECTIVE'])"
```

```bash
agentic-harness start "summarize open TODOs"
agentic-harness continue
agentic-harness review
agentic-harness status
```

### Local LLM Worker

```python
from agentic_harness import Supervisor
from agentic_harness.adapters import LocalLLMAdapter

worker = LocalLLMAdapter(
    endpoint="http://127.0.0.1:4000/v1/chat/completions",
    model="local-model",
)

supervisor = Supervisor(project_dir=".", worker=worker)
supervisor.start("draft release notes for v0.1.0")
supervisor.continue_goal()
supervisor.review()
```

## Adapters

Adapters implement one method: `run(goal) -> WorkerResult`.

```python
from agentic_harness.core.worker import WorkerResult

class MyWorker:
    def run(self, goal):
        path = f".agentic-harness/runs/{goal.id}/output.txt"
        # call your tool here
        return WorkerResult(success=True, summary="done", artifacts=[path])
```

Then wire it into the supervisor:

```python
from agentic_harness import Supervisor

supervisor = Supervisor(project_dir=".", worker=MyWorker())
```

## Configuration

`agentic-harness init` creates `.agentic-harness/config.yml`.

```yaml
version: 1
worker: noop
```

Shell worker configuration:

```yaml
version: 1
worker: shell
shell_command:
  - make
  - agent-goal
```

The shell adapter exposes:

- `AGENTIC_HARNESS_GOAL_ID`
- `AGENTIC_HARNESS_OBJECTIVE`

## Public API

```python
from agentic_harness import Goal, Supervisor, Worker
```

## Contributing

Issues and pull requests are welcome. Good first contributions:

- Add adapter examples for common local coding agents.
- Improve the deterministic review helpers.
- Improve examples for common local workflows.
- Write docs for running the harness in a small team.

Keep the core small. If a feature assumes a particular server, model provider, or operator workflow, it probably belongs in an adapter or example.

## License

MIT. See [LICENSE](LICENSE).
