# Agentic Harness

![Agentic Harness social preview](docs/assets/agentic-harness-social-preview.png)

[![CI](https://github.com/moortekweb-art/agentic-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/moortekweb-art/agentic-harness/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Support](https://img.shields.io/badge/support-Buy%20Me%20a%20Coffee-ffdd00.svg)](https://buymeacoffee.com/moortekweb3)

Coding agents say "done" too early. Agentic Harness makes "done" mean checks
passed.

Agentic Harness runs coding agents and automation jobs as bounded, reviewable
goals. It captures transcripts and artifacts, prevents runaway loops, and only
marks work done when deterministic review passes.

## Fastest Demo

Run a complete supervised fix-tests workflow from any directory:

```bash
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

Or inspect the no-hidden-YAML path yourself:

```bash
agentic-harness create-demo fix-tests /tmp/agentic-harness-demo --force
cd /tmp/agentic-harness-demo
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q   # expected to fail
agentic-harness fix-tests     # auto-creates demo config
agentic-harness status
agentic-harness report
python -m pytest tests/ -q   # should pass
```

No prompt design. No dashboard. No controller.

## Quick Start

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
agentic-harness --version
agentic-harness selftest
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

## Human Mode

On a Linux/Ubuntu machine that has the optional local-goal/Mode 3A backend
installed, you do not need to write goal packets or remember planner names:

```bash
agentic-harness setup
agentic-harness do "make Jarvis voice startup more reliable"
agentic-harness check
agentic-harness watch
```

The Python package does not install that optional backend. Commands that use it
look for `scripts/local-goal` under the configured document root. Pass
`--doc-root /path/to/compatible/checkout` for a single command, set
`AGENTIC_HARNESS_DOC_ROOT` for a shell session, or launch from the compatible
checkout and let the current directory be used. For a standalone executable,
set `AGENTIC_HARNESS_LOCAL_GOAL=/path/to/local-goal`; that executable override
wins over the document-root lookup. `~` is expanded in configured paths.

`do` accepts plain English, wraps it in the safe Mode 3A GLM cloud-lane format,
queues it, and prints a work ticket. `check` shows what is happening. `watch`
asks the harness to move the current work forward once. Advanced commands such
as `mode3a-run`, `mode3a-status`, and `mode3a-monitor` remain available when
you need the underlying details.

For a local browser interface:

![Agentic Harness local GUI](docs/assets/agentic-harness-gui.png)

```bash
agentic-harness gui
```

The GUI binds to `127.0.0.1` by default and asks the OS for a free local port.
Use the exact URL printed at startup. For scripts or operators that need a
stable URL, pass an explicit port:

```bash
agentic-harness gui --port 8765
```

Use `--no-open` for headless terminals, SSH sessions, and automation:

```bash
agentic-harness gui --no-open
```

Use `agentic-harness gui --doc-root /path/to/compatible/checkout` or
`AGENTIC_HARNESS_DOC_ROOT=/path/to/compatible/checkout agentic-harness gui` when
the optional local-goal backend lives outside the directory where you launch the
GUI. Without that backend, the GUI still serves, but backend task actions report
the missing optional executable and how to configure it.

Agentic Harness is a Python application. Its GUI is rendered by packaged
HTML/CSS/JS files served by the Python backend; there is no Node, Electron,
Tauri, or native widget runtime in the v0.6.26 GUI. The packaged browser app
includes live status updates over WebSocket, progress indicators, task history
search, dark/light theme switching, keyboard shortcuts, session export/import,
and local form undo/redo.

The GUI presents the same four human modes as plain choices, keeps technical
details in an advanced drawer, and uses the local background worker under the
hood. It also exposes a readiness gate based on the local agent loop: it shows
whether the harness is ready, acting, checking, or waiting for review, and it
keeps new simple-UI starts behind review when the active local-goal run needs a
human decision.

The public interface decisions and upgrade boundary are documented in
[GUI Design](docs/GUI_DESIGN.md) and
[GUI Architecture](docs/GUI_ARCHITECTURE.md). A narrow-screen capture is also
available in [the GUI assets](docs/assets/agentic-harness-gui-mobile.png).

Keep the default loopback binding unless you have a specific reason to expose
the GUI beyond this computer. If you bind to a non-loopback host such as
`0.0.0.0`, set `AGENTIC_HARNESS_GUI_TOKEN` before launch to require a bearer
token for API actions and the WebSocket status stream, and still treat the
server as a local control surface. The static browser shell remains visible so
the app can load; API calls, task controls, session import/export, and the
status stream remain gated. In token mode, enter the configured token when the
browser asks for it, or append it once as a `token` query parameter when opening
the page. The browser removes that query parameter from visible history
immediately and keeps the token only for the current tab session. Bearer tokens
are a basic access gate; they are not a complete browser-origin or CSRF security
model.

To inspect the demo files instead of running them immediately:

```bash
agentic-harness create-demo fix-tests /tmp/agentic-harness-demo --force
cd /tmp/agentic-harness-demo
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q   # expected to fail
agentic-harness fix-tests     # auto-creates config when it can pick a backend
agentic-harness status
python -m pytest tests/ -q   # should pass
```

Or ask the installed CLI to print the shortest path for this machine:

```bash
agentic-harness quickstart
```

Advanced users can still hand-write `.agentic-harness/config.yml`; the
configuration format is documented below.

### Recipes

```bash
agentic-harness recipes
agentic-harness fix-tests
agentic-harness lint-fix
agentic-harness typecheck-fix
agentic-harness update-docs
agentic-harness changelog
agentic-harness verify-tests
agentic-harness run-recipe fix-tests --explain
agentic-harness fix-tests --until-done --max-attempts 3
```

Recipes hide the common prompt and review-command setup for beginner workflows.
Run recipes such as `fix-tests`, `lint-fix`, `typecheck-fix`, `update-docs`,
and `changelog` directly. If no project config exists, recipe commands create
one automatically when they can select a supported coding backend; demos use
the packaged shell mock. Use `init` when you want to choose or replace the
backend explicitly.
Each built-in recipe has a direct command; `run-recipe <name>` remains available
for scripts that prefer one generic entrypoint or want `--explain`.
Recipe runs write `.agentic-harness/runs/<goal-id>/report.md` automatically,
so the operator-readable handoff exists even if you do not run
`agentic-harness report` afterward.
Add `--until-done --max-attempts N` when a recipe should retry failed worker
attempts through the normal restart path before giving up.

For non-demo goals that may need more than one pass, use the bounded driver:

```bash
agentic-harness run-until-done "fix the failing tests" --max-attempts 3
```

It starts or resumes one active goal, runs worker/review cycles, restarts failed
attempts up to the limit, writes `.agentic-harness/runs/<goal-id>/report.md`,
and still stops with a clear `done` or `failed` state.

## Not a Coding Agent

Agentic Harness does not replace Codex, Aider, CodeWhale, OpenCode, or your
shell scripts. It wraps them in a deterministic goal loop with state,
transcripts, artifacts, loop limits, and review gates.

## Project Links

- [Examples](examples/) include shell, coding-agent, the fix-failing-tests demo, local LLM, tmux, GitHub Actions, and real-world recipe examples.
- [Release checklist](docs/RELEASE_CHECKLIST.md) documents the v0.6.26 release checks.
- [PyPI trusted publishing](docs/PYPI_TRUSTED_PUBLISHING.md) documents the active publish workflow and external PyPI setup required for tokenless publishing.
- [Repo artwork](docs/assets/) includes a social preview banner and square icon.
- [Support the project](https://buymeacoffee.com/moortekweb3) via Buy Me a Coffee.
- [Attraction plan](ATTRACTION_PLAN.md) captures public project positioning and follow-up ideas.
- [CI workflow](.github/workflows/ci.yml) runs tests, ruff, mypy, compile smoke checks, package builds, wheel installs, and CLI smoke checks on Linux, Windows, and macOS.

## Release Smoke

Before tagging a release, run:

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m agentic_harness.cli release-smoke
```

`release-smoke` builds the wheel and sdist, installs each into a fresh virtual
environment, runs `twine check` on the distributions, verifies direct recipe
commands, runs the packaged demo, checks the transcript/report artifacts, and
writes `SHA256SUMS` next to the verified release artifacts.

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
- State lock and active-goal guard: mutating commands acquire
  `.agentic-harness/state.lock`, and `start` refuses to overwrite an unfinished
  active goal.
- Adapter system: shell, coding-agent CLI, tmux, GitHub Actions, and OpenAI-compatible local LLM adapters are included.
- Local-model friendly: any model served through an OpenAI-compatible chat
  endpoint can be wrapped with deterministic review, including current
  30B-40B local-model experiments such as Ornith 35B.
- Project-local config: no hardcoded absolute paths.
- Small public API: `Goal`, `Supervisor`, and `Worker`.

## Installation

Install as a CLI with pipx:

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
```

After the first PyPI publish, install the released distribution with:

```bash
pipx install local-agentic-harness
```

The Python distribution name is `local-agentic-harness` so it can be reserved
on PyPI without colliding with the unrelated existing `agentic-harness` package.
The installed CLI command remains `agentic-harness`.

For development:

```bash
git clone https://github.com/moortekweb-art/agentic-harness.git
cd agentic-harness
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

On Ubuntu or other Debian-family Linux systems, install Python 3.11+ and
`pipx` from your package manager first if they are not already present. The v1
GUI ships inside the Python wheel/sdist as package data, so no frontend build
step is required.

## Usage Examples

See [examples/](examples/) for complete project-local examples with READMEs, safety notes, and expected output.
For the critique-driven demo, see
[examples/fix-failing-tests-demo](examples/fix-failing-tests-demo/).

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

For machine-readable output:

```bash
agentic-harness status --format json
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
supervisor.start("draft release notes for v0.6.15")
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

`agentic-harness init` creates `.agentic-harness/config.yml`. When Codex,
CodeWhale, OpenCode, or Aider is available on `PATH`, bare `init` selects that
backend automatically.

```bash
agentic-harness init
agentic-harness init-agent shell
agentic-harness init-agent codex
```

If no supported coding-agent backend is available, bare `init` creates a safe
placeholder config. The `init <tool>` variant and `init-agent <tool>` variants
write a pre-configured template for the named backend.

```yaml
version: 1
worker: noop
```

`noop` is a safe placeholder. It does not pass review by default because no real
worker ran. For a demo-only path, opt in explicitly:

```yaml
version: 1
worker: noop
allow_noop_success: true
```

Shell worker configuration:

```yaml
version: 1
worker:
  type: shell
  shell_command:
    - make
    - agent-goal
```

The shell adapter exposes:

- `AGENTIC_HARNESS_GOAL_ID`
- `AGENTIC_HARNESS_OBJECTIVE`

Coding-agent worker configuration:

```yaml
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codex
    - exec
    - --skip-git-repo-check
    - "{objective}"
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review:
  command:
    - python
    - -m
    - pytest
    - tests/
    - -q
```

Tmux worker configuration:

```yaml
version: 1
worker: tmux
tmux_command: "python worker.py --goal {goal_id}"
tmux_session_prefix: agentic-harness
```

Local LLM worker configuration:

```yaml
version: 1
worker: local_llm
llm_endpoint: http://127.0.0.1:4000/v1/chat/completions
llm_model: local-model
```

GitHub Actions worker configuration:

```yaml
version: 1
worker: github_actions
github_owner: moortekweb-art
github_repo: agentic-harness
github_workflow_id: ci.yml
github_token: token-from-your-secret-store
github_wait: true
github_api_version: 2026-03-10
```

Configuration is intentionally small and strict: unsupported schema versions,
unknown keys, unsupported workers, malformed values, and workers without their
required settings are rejected instead of silently ignored. Config files are
parsed with PyYAML, so flat keys and grouped sections are both supported.

## Review Helpers

The core review module includes small deterministic criteria factories:

```python
from agentic_harness.core import (
    DeterministicReviewer,
    artifact_exists,
    command_passes,
    file_changed,
    git_clean,
)

reviewer = DeterministicReviewer([
    artifact_exists(".", ".agentic-harness/runs/example/report.md"),
    command_passes(["python", "-m", "pytest", "tests/", "-q"]),
    file_changed(".", "CHANGELOG.md"),
    git_clean("."),
])
```

You can also configure common review gates in `.agentic-harness/config.yml`:

```yaml
version: 1
worker:
  type: shell
  shell_command:
    - make
    - agent-goal
review:
  command:
    - python
    - -m
    - pytest
    - tests/
    - -q
  git_clean: true
```

`GitHubActionsAdapter` dispatches workflows by default. Set `github_wait: true`
or `wait_for_completion=True` to wait for the exact workflow run returned by
GitHub's modern workflow dispatch API. Older GitHub API responses that do not
return a run URL fall back to polling workflow_dispatch runs created after the
dispatch request.

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

MIT. Copyright (c) 2026 Michael / Moortekweb. See [LICENSE](LICENSE) and
[AUTHORS.md](AUTHORS.md).

## Support

If Agentic Harness helps your local AI workflow, you can support the project
here:

https://buymeacoffee.com/moortekweb3
