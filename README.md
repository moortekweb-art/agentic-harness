# Agentic Harness

![Agentic Harness social preview](https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-social-preview.png)

[![CI](https://github.com/moortekweb-art/agentic-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/moortekweb-art/agentic-harness/actions)
[![Python](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/moortekweb-art/agentic-harness/blob/main/LICENSE)

A local-first execution harness that lets an agent work toward a complete goal
without treating its own claim of success as proof.

Agentic Harness provides one project-local engine for two interfaces: a CLI and
a browser GUI. It can supervise an installed coding-agent CLI or run a bounded
tool-using agent against a user-selected OpenAI-compatible local or cloud model.
In both cases, durable progress, resource limits, recorded evidence, and an
independent verification command determine whether the result is done.

## Product Boundary

`local-agentic-harness` is one Python distribution with a shared Python engine,
project state model, packaged static browser assets, and two executable
interfaces:

- `agentic-harness` is the CLI.
- `agentic-harness-gui` is the browser service.

This is the same install, not two products. Both interfaces use
`.agentic-harness/` inside the selected workspace. The portable embedded engine
is the default for both new CLI goals and the GUI; a private controller or
machine-specific sidecar is not required.

## Quick Start

Install the released distribution and open the GUI in a project:

```bash
pipx install local-agentic-harness
cd /path/to/your/project
agentic-harness selftest
agentic-harness gui
```

The GUI asks you to choose one execution method:

- an installed coding agent: Codex, OpenCode, Aider, or CodeWhale;
- a local OpenAI-compatible chat-completions endpoint; or
- a cloud OpenAI-compatible chat-completions endpoint.

You also choose an independent verification command before work can start. For
a model endpoint, enter the exact chat-completions URL and any model identifier
the endpoint accepts. Local endpoints may be keyless. When an endpoint requires
a key, use an environment-variable reference or a session-only key. Cloud setup
also requires explicit confirmation that selected workspace content may be sent
to that endpoint.

After setup, describe one complete outcome and start it. The GUI shows the
current subgoal, checkpoint, cycle, durable tool events, changed files, checks,
and final evidence. It does not invent progress while the worker is quiet.

The same configured workspace can run from the CLI:

```bash
agentic-harness do "fix the failing tests and verify the result"
agentic-harness check
agentic-harness report
```

If the GUI profile uses a session-only model key, that credential belongs to
the GUI process and the CLI cannot reuse it. Choose an environment-variable
reference when both interfaces need to run the same model profile.

Use a complete autonomous goal directly when a project is already configured:

```bash
agentic-harness goal "implement the requested change, preserve unrelated work, and verify it"
```

If that foreground process is interrupted, resume the same durable goal by
omitting a new objective:

```bash
agentic-harness goal
```

To see the shortest path detected for the current project:

```bash
agentic-harness quickstart
```

The packaged failure-to-fix demo remains available and auto-creates config for
its mock worker:

```bash
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

Or inspect each step:

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

### Recipes

Common workflows have direct commands:

```bash
agentic-harness recipes
agentic-harness fix-tests
agentic-harness lint-fix
agentic-harness typecheck-fix
agentic-harness update-docs
agentic-harness changelog
agentic-harness verify-tests
agentic-harness run-recipe fix-tests --explain
```

Recipes auto-create config when a supported installed coding agent is available.
Each run writes an operator-readable report at
`.agentic-harness/runs/<goal-id>/report.md`.

## How Completion Works

```text
objective
   |
   v
plan -> act -> record progress -> evaluate -> repair if needed
                                      |
                                      v
                           independent verification
                                      |
                         pass --------+-------- fail
                           |                     |
                           v                     +--> continue or block
                     accepted done
```

The original objective remains attached to the goal across cycles and recovery.
The worker maintains a plan, requirement audit, current subgoal, and checkpoint.
Tool use produces durable redacted events. A completion claim is accepted only
when every requirement has evidence and at least one configured deterministic
review criterion passes.

Limits on cycles, elapsed time, model tokens, provider calls, and tool calls are
resource budgets, not success conditions. Exhausting a budget produces a
blocked or failed result; it never converts unfinished work into done.

One workspace has one active goal. Use separate project roots when truly
independent goals must run concurrently.

## Execution Methods

### Installed coding agents

The GUI can configure Codex, OpenCode, Aider, or CodeWhale. From the CLI, create
or replace a starter config explicitly:

```bash
agentic-harness init-agent codex
agentic-harness init-agent opencode
agentic-harness init-agent aider
agentic-harness init-agent codewhale
```

The harness owns lifecycle, evidence, and independent review. The selected
coding-agent process still owns its own credentials, tool permissions, and
runtime policy. Safe-area labels are enforced by the embedded model agent; for
an external coding-agent CLI they are operator guidance unless that CLI enforces
the same boundary.

### Local and cloud models

The embedded model agent accepts an exact OpenAI-compatible chat-completions
endpoint and an arbitrary model ID. This covers local servers such as vLLM,
llama.cpp, Ollama-compatible gateways, and LM Studio when they expose that API,
as well as compatible cloud gateways.

Native Anthropic Messages and Google Gemini transports are not built into the
embedded engine. Use an OpenAI-compatible gateway, an installed coding agent,
or an optional external orchestrator if those native APIs are required.

The GUI is the recommended way to create a model profile. This equivalent cloud
profile uses an environment-variable reference and contains no API key:

```yaml
version: 1
worker: model_agent
llm:
  endpoint: https://provider.example/v1/chat/completions
  model: organization/model-name-or-any-provider-id
  api_key_env: MODEL_PROVIDER_API_KEY
  credential_source: env
  remote_data_confirmed: true
  max_steps: 8
  timeout: 120
review:
  command:
    - python
    - -m
    - pytest
    - -q
  command_timeout: 300
autonomy:
  max_cycles: 100
  max_elapsed_seconds: 7200
  max_total_tokens: 500000
  max_provider_calls: 200
  max_tool_calls: 1000
```

Set the key outside the project before running the CLI or GUI:

```bash
export MODEL_PROVIDER_API_KEY="use-your-secret-entry-path"
agentic-harness do "complete and verify one bounded goal"
```

Do not put a literal API key in `.agentic-harness/config.yml`. Model-agent
config rejects plaintext keys. A session key entered in the loopback GUI stays
only in that server process, is not returned by the API, and must be re-entered
after restart. Environment-variable references survive restarts without writing
the secret to project state.

Cloud profiles require HTTPS and `remote_data_confirmed: true`. That consent
means selected file excerpts, tool observations, and prompts may leave the
machine for the endpoint you chose. It is not inferred from the provider name.

## Embedded Safety Boundary

The built-in model agent intentionally exposes a narrow tool set:

- list, read, and search workspace files;
- create text files and replace previously read text inside allowed paths;
- inspect Git status and diff;
- run only the verification commands supplied for the goal; and
- report a structured outcome with requirement evidence.

It does not expose arbitrary shell, delete, package-install, service-control, or
network tools. Writes are contained to the workspace, protect repository and
credential paths, reject symlink escapes, require a current file hash before
replacement, and protect pre-existing dirty files unless they were explicitly
placed in scope. Configured checks run in a minimal environment without provider
keys or other unrelated process secrets. Provider redirects, URL credentials, URL query credentials, and
oversized responses are rejected.

Transcripts and task events are redacted, written atomically, and stored with
owner-only permissions. Redaction is defense in depth, not permission to place
secrets in prompts or source files.

External coding-agent, shell, tmux, GitHub Actions, and optional orchestration
adapters can have broader authority. Their tool policy is not silently upgraded
to the embedded agent's enforcement; review their configuration before use.

## GUI Operation and Network Safety

The GUI binds to loopback and asks the OS for a free port by default. Use the
exact URL printed at startup:

```bash
agentic-harness-gui --project-dir /path/to/project --no-open
```

Choose a stable loopback port when a service or private reverse proxy needs one:

```bash
agentic-harness-gui --project-dir /path/to/project --port 8765 --no-open
```

Keep loopback as the default. A non-loopback bind is refused unless
`AGENTIC_HARNESS_GUI_TOKEN` is set. Authenticated clients send that value in the
`Authorization: Bearer ...` header; query-string tokens are not supported. If a
reverse proxy uses another hostname, add only that expected hostname to
`AGENTIC_HARNESS_GUI_ALLOWED_HOSTS` and preserve the original `Host` header.

See [GUI deployment](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GUI_DEPLOYMENT.md) for the portable systemd and private
network pattern.

## Recovery and Evidence

Project configuration lives at `.agentic-harness/config.yml`. Goal state,
redacted events, transcripts, reports, and verification evidence live below the
same `.agentic-harness/` directory.

After a failed or blocked goal, inspect `agentic-harness report` before deciding
what to do next. Use `agentic-harness restart` to retry that same failed goal
while preserving its evidence. Start a fresh goal only when the objective is
intentionally separate.

GUI stop is cooperative: the current bounded tool step finishes, then the task
is recorded as stopped. A late worker result cannot be accepted as done after
cancellation. Session-only API keys are deliberately absent after a GUI process
restart and must be entered again.

## Optional Turnstone Integration

[Turnstone](https://github.com/turnstonelabs/turnstone) is a separate,
self-hosted orchestration framework. It is not bundled, imported, or installed
by `local-agentic-harness`, and the default embedded GUI does not need it.

Operators who already run Turnstone may place an operator-maintained
Turnstone-compatible wrapper behind the explicit `local-goal` backend:

```bash
export AGENTIC_HARNESS_LOCAL_GOAL=/absolute/path/to/compatible-wrapper
agentic-harness-gui --backend local-goal --project-dir /path/to/project --no-open
```

That path uses a narrow command contract and is opt-in. A direct Turnstone
REST/SDK adapter is not part of this release. See
[Turnstone integration](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/TURNSTONE_INTEGRATION.md) for the exact boundary,
capability preflight, lifecycle expectations, and private-deployment note.

## Other Adapters

The shared engine also supports shell, tmux, GitHub Actions, the legacy
single-response local LLM adapter, and custom Python workers. See
[examples](https://github.com/moortekweb-art/agentic-harness/tree/main/examples) for project-local configurations and safety notes.

The small public API remains available:

```python
from agentic_harness import Goal, Supervisor, Worker
```

## Installation

Install the released distribution from PyPI:

```bash
pipx install local-agentic-harness
```

The distribution name avoids a collision with the unrelated
`agentic-harness` package on PyPI. The installed CLI command remains `agentic-harness`.
The same installation also provides `agentic-harness-gui`.

Install the current GitHub source with:

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

The GUI frontend ships as packaged static assets in the wheel and sdist. No
Node, Electron, Tauri, or frontend build step is required to run it.

## Release Verification

Before tagging a release:

```bash
python -m pip install -e ".[test]"
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
python -m agentic_harness.cli release-smoke
```

`release-smoke` builds and checks a wheel and sdist, installs each into a fresh
virtual environment, verifies both entry points and packaged assets, runs a
goal/report smoke test, and writes `SHA256SUMS` beside the artifacts.

## Documentation

- [GUI architecture](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GUI_ARCHITECTURE.md)
- [GUI design](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GUI_DESIGN.md)
- [GUI deployment](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GUI_DEPLOYMENT.md)
- [Autonomous goal contract](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/CODEX_GOAL_PARITY.md)
- [Turnstone integration boundary](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/TURNSTONE_INTEGRATION.md)
- [Release checklist](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/RELEASE_CHECKLIST.md)
- [PyPI trusted publishing](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/PYPI_TRUSTED_PUBLISHING.md)
- [Examples](https://github.com/moortekweb-art/agentic-harness/tree/main/examples)

## Contributing

Issues and pull requests are welcome. Keep the public core portable and
provider-neutral. Machine-specific services, model names, credentials, and
operator workflows belong in adapters or private deployment configuration, not
in default product behavior.

## License

MIT. Copyright (c) 2026 Michael / Moortekweb. See
[LICENSE](https://github.com/moortekweb-art/agentic-harness/blob/main/LICENSE) and
[AUTHORS.md](https://github.com/moortekweb-art/agentic-harness/blob/main/AUTHORS.md).

## Support

If Agentic Harness helps your local AI workflow, you can support the project at
[Buy Me a Coffee](https://buymeacoffee.com/moortekweb3).
