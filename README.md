# Agentic Harness

<p align="center">
  <img src="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-social-preview.png" width="880" alt="Agentic Harness: plan, execute, independently review, then mark work done">
</p>

<p align="center">
  <a href="https://github.com/moortekweb-art/agentic-harness/actions"><img src="https://github.com/moortekweb-art/agentic-harness/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://pypi.org/project/local-agentic-harness/"><img src="https://img.shields.io/pypi/v/local-agentic-harness.svg" alt="PyPI version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11--3.14-blue.svg" alt="Python 3.11 through 3.14"></a>
  <a href="https://github.com/moortekweb-art/agentic-harness/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT license"></a>
</p>

> Let your coding agent work. Make it prove the result.

A coding agent saying “done” is not proof that the task is done. Agentic Harness
adds a completion gate to Codex, OpenCode, Aider, CodeWhale, or a compatible
local/cloud model. It keeps the objective, records the work, runs a check you
control, and only then reports **Verified done**.

- **Keep your agent.** The harness wraps the tools and models you already use.
- **Choose the proof.** Tests, lint, builds, or another deterministic command
  decide whether the result is accepted.
- **Keep the evidence.** Every run leaves a project-local, redacted report you
  can inspect or commit with the work.
- **Stay local by default.** The GUI binds to loopback, and local model paths do
  not require a cloud service.

## Try it in two minutes

Install the released CLI and browser interface:

```bash
pipx install local-agentic-harness
cd /path/to/your/project
agentic-harness gui
```

The browser opens on **Home**. Describe the result in a normal sentence, choose
how much effort it deserves, and use **Settings** once to connect an installed
coding app, local AI, or cloud AI. Commands, endpoints, and model details stay
under advanced disclosures until you need them.

No account or AI setup yet? Click **Try safe demo** in the app, or run:

```bash
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

The demo begins with a failing test and a worker that claims success too early.
The independent gate rejects the claim, a second attempt repairs the project,
and the final report shows why the result was accepted. It is a controlled
mechanics demo, not evidence about model quality. See the complete
[terminal demo script](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/demo-script.md).

## See the workflow

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui.png">
        <img src="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui.png" width="420" alt="Agentic Harness Home screen">
      </a>
      <br><sub><strong>Describe the outcome.</strong> Choose an effort level and see what will run before files change.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-verified.png">
        <img src="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-verified.png" width="420" alt="Agentic Harness verified task evidence">
      </a>
      <br><sub><strong>Inspect the proof.</strong> Verified done includes changed files, the worker report, and independent evidence.</sub>
    </td>
  </tr>
</table>

<details>
<summary>See the mobile first-run experience</summary>

<p align="center">
  <a href="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-mobile.png">
    <img src="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-mobile.png" width="220" alt="Agentic Harness mobile first-run screen">
  </a>
</p>

</details>

Click any preview for the full-size screenshot.

## Prefer the terminal?

```bash
cd /path/to/your/project
agentic-harness do "fix the failing tests" --check "python -m pytest tests/ -q"
agentic-harness check
agentic-harness report
```

`--check` is the independent completion gate. A worker saying “done” cannot
replace it. The durable report is written to
`.agentic-harness/runs/{goal-id}/report.md`.

## Is it for you?

Agentic Harness is for developers who want autonomous help without delegating
the definition of “finished” to the same agent doing the work. It is especially
useful for bounded maintenance, test repair, lint/type fixes, documentation
updates, and longer tasks that need resumable evidence.

The documented security boundary is one trusted user and one workspace. It is
not a multi-tenant agent platform, an anonymous public service, or proof that an
underlying model has become more capable. External coding-agent CLIs retain
their own permissions and runtime policies.

## What you install

`local-agentic-harness` is one Python package with a shared engine and two
interfaces:

- `agentic-harness` is the CLI.
- `agentic-harness-gui` is the local browser interface.

This is the same install, not two products. Both use `.agentic-harness/` inside
the selected workspace. No separate
orchestration service is required for the default embedded engine.

The task screen offers four provider-independent approaches:

| Approach | Intended use | Runtime boundary |
| --- | --- | --- |
| Quick | One small, clear change | Small retry and spending caps |
| Standard | Important or unfamiliar work | Balanced caps; recommended default |
| Thorough | Larger resumable work | Full project-configured limits |
| Experiment | A tiny reversible trial | Built-in model worker plus an explicit file limit |

The approach is not a model choice. Any compatible provider can be used with
Quick, Standard, or Thorough. Experiment additionally
requires the built-in worker because it can enforce the selected path boundary.

Managed installations keep that effort choice separate from their execution
routes. Before a task starts, the browser combines the product's supported
route catalog with live facts from the connected backend: where the work runs,
the planner and executor, model profile, data boundary, verification policy,
maturity, and current availability. Routes that are currently unavailable stay
disabled with a reason; internal canaries that are not a user-facing product
route can remain hidden. The interface never silently changes a local selection
to a cloud route.

<details>
<summary>Example managed installation</summary>

This example includes installation-specific local and cloud routes. Friendly
names lead; technical mode identifiers stay in Advanced details. Choices appear
only when the connected backend proves that they exist and reports their current
availability, and unavailable routes remain visible with a reason.

<p align="center">
  <a href="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-managed.png">
    <img src="https://raw.githubusercontent.com/moortekweb-art/agentic-harness/main/docs/assets/agentic-harness-gui-managed.png" width="720" alt="Managed Agentic Harness installation with execution routes and local model profiles">
  </a>
</p>

</details>

## Current evidence and open beta

Version 0.12.0 is a released, technically certified self-hosted completion-
assurance tool. Its frozen specification and evidence boundaries passed a
preregistered ten-case adversarial matrix with zero false verified completions.
External usability and real-agent performance validation remain in progress.

- Read the [v0.12.0 release and evidence packet](https://github.com/moortekweb-art/agentic-harness/releases/tag/v0.12.0).
- Review the [assurance protocol](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/V012_ASSURANCE_PROTOCOL.md).
- Try it on a disposable branch using the [external beta guide](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EXTERNAL_BETA.md).
- Count a success, failure, blocked setup, or abandoned attempt through the
  [beta issue form](https://github.com/moortekweb-art/agentic-harness/issues/new?template=external-beta.yml).

The project does not claim that the harness improves model intelligence or that
the still-open external beta has already proved broad usability.

## Advanced Workflows

### Verified Best-of-N

The verified tournament workflow runs two to ten implementations concurrently
in isolated Git worktrees and applies a winner only after independent verification:

```bash
agentic-harness best-of-n -n 3 \
  "repair the parser without changing its public API" \
  --check "python -m pytest tests/test_parser.py -q"
```

All candidates start from the same commit, receive the same immutable GoalSpec,
and run the same configured checks. Explicit verifier files, existing tracked
test suites, and the relevant test-runner configuration are hashed before work;
a candidate that changes those assets is disqualified even if its altered check
returns zero. Among passing candidates, the harness deterministically prefers
the smallest patch, applies it to the original workspace, and runs the checks
again there. If no candidate passes—or if the applied result fails—the command
returns blocked with no accepted winner. It never selects a "least bad" failing
implementation.

The command requires a clean Git-root workspace so it cannot overwrite
pre-existing changes. Private candidate patches and the versioned tournament
receipt are stored under `.agentic-harness/tournaments/`. The initial selection
policy is deliberately deterministic rather than model-judged; a future judge
may rank only candidates that have already passed the frozen checks.

This first surface is CLI-only. Git worktrees isolate candidate file changes,
but they are not a security sandbox for a malicious external coding-agent
process; that process retains the authority of its configured adapter and OS
account. Use the embedded model agent or an external sandbox when stronger tool
containment is required.

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
`.agentic-harness/runs/{goal-id}/report.md`.

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
                     verified done
```

The original objective remains attached to the goal across cycles and recovery.
The worker maintains a plan, status against harness-frozen requirement IDs,
the current subgoal, and a checkpoint. Tool use produces durable redacted events. A completion claim
is accepted only when it is structurally valid, cites recognized current-run
harness records, and at least one configured independent criterion passes.
Worker-authored prose alone is not evidence.

Starting in v0.12, the harness owns the acceptance specification before worker
execution. Explicit objective clauses are conservatively frozen as stable
requirement IDs; ambiguous prose remains one complete objective requirement.
Specification-frozen completion requires eligible, current-run, harness-issued
evidence for every frozen ID. High-assurance runs add operator approval and
versioned mid-run amendments whose prior evidence is invalidated. See the
[GoalSpec](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GOAL_SPEC.md)
and [evidence v2](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EVIDENCE_V2.md)
contracts.

Limits on cycles, elapsed time, model tokens, provider calls, and tool calls are
resource budgets, not success conditions. Exhausting a budget produces a
blocked or failed result; it never converts unfinished work into done.

One workspace has one active goal. Use separate project roots when truly
independent goals must run concurrently.

## Controlled Evaluation

A reproducible comparison has 24 task-behavior cases across six maintenance payloads.
Each runs the same scripted coding-agent process directly and through Agentic
Harness in pristine workspaces. The matrix includes correct first attempts,
premature claims that can be repaired, persistent false claims, and process
failures that can be retried.

| Arm | Verified accepts | False accepts | Acceptance precision | Recovered tasks | Mean attempts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct baseline | 6 | 12 | 33.3% | 0 | 1.0 |
| Agentic Harness | 18 | 0 | 100% | 12 | 2.0 |

This is a controlled gate evaluation, not a real-model benchmark or adoption
claim. The table comes from the immutable v0.7.2 release snapshot. Validate it
against the v0.7.2 tag, not current main, because the default branch may contain
later source changes. Its value is narrower: the direct baseline produced
12 false accepts; Agentic Harness produced 0 false accepts. It caught all 12
premature claims and recovered every repairable task at the explicit cost of more
attempts. See the
[method](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/README.md),
[snapshot receipt](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/results/representative/README.md),
[summary](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/results/representative/summary.md), and
[raw JSONL](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/results/representative/raw.jsonl).

A first preregistered [real Codex comparison](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/results/real-agent-20260712/README.md)
also publishes all records and redacted transcripts. Both arms passed all ten
easy tasks, so it found no correctness advantage; Harness cost more time and
tokens. That negative result is a starting point for harder evaluation, not a
marketing claim.

On a harder preregistered set, both arms passed 9/10 verifiers. Direct
execution falsely accepted the miss; Harness refused it but did not repair it
and cost more time and tokens. See the current
[revision-five result](https://github.com/moortekweb-art/agentic-harness/blob/main/evaluation/results/hard-real-agent-v5-20260712/README.md).

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

Settings can find fixed loopback endpoints for Ollama, LM Studio, vLLM, and
llama.cpp. When a server reports more than one model, the user chooses the exact
model before connecting. Endpoint, model ID, and environment-variable fields
remain available under **Manual connection**. Discovery only proves that a
server reports models; a separate structured-action test must pass before the
project becomes ready.

If this is your first local-AI setup, choose **AI running on my computer** in
Settings and follow the built-in LM Studio guide: [install LM Studio](https://lmstudio.ai/download),
download and load a chat model with tool-use support, switch on the local server
from LM Studio's Developer page, then return to Agentic Harness and choose
**Find local AI**. LM Studio documents the same [app setup](https://lmstudio.ai/docs/app/basics)
and [local-server switch](https://lmstudio.ai/docs/developer/core/server).
Ollama, vLLM, llama.cpp, custom ports, and private-network servers remain under
the advanced/manual path.

Settings also includes editable convenience templates for a custom provider, the Z.ai
general API, and a Z.ai GLM Coding Plan account. Templates only pre-fill the
endpoint, model ID, and environment-variable name; they do not bundle a key or
turn a provider into a work approach. The GLM Coding Plan template starts with
`glm-5.2`, but the value remains editable because the user's account and current
provider entitlement determine which model IDs and clients are allowed. Z.ai
documents separate [general API](https://docs.z.ai/api-reference/introduction)
and [Coding Plan](https://docs.z.ai/api-reference/llm/chat-completion) base URLs.

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
agentic-harness do "complete and verify one bounded goal" --check "python -m pytest -q"
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

Settings are scoped to that one project and stored in its
`.agentic-harness/config.yml`. A managed installation exposes the same Settings
view as read-only instead of hiding it. If an existing configuration is invalid
or uses an unsafe symlink, the GUI reports the problem and refuses to overwrite
the original file.

Keep loopback as the default. A non-loopback bind is refused unless
`AGENTIC_HARNESS_GUI_TOKEN` is set. Authenticated clients send that value in the
`Authorization: Bearer ...` header; query-string tokens are not supported. If a
reverse proxy uses another hostname, add only that expected hostname to
`AGENTIC_HARNESS_GUI_ALLOWED_HOSTS` and preserve the original `Host` header.

See [GUI deployment](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/GUI_DEPLOYMENT.md) for the portable systemd and private
network pattern.

The public release is a self-hosted application for one trusted user and one
selected workspace. Publishing the package does not make the maintainer's
running GUI a safe shared website. A hosted multi-user service needs identity,
per-user isolated workspaces and secrets, quotas, abuse controls, cleanup, and
an independently operated execution plane. See the
[public-release boundary](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/PUBLIC_RELEASE.md).

## Recovery and Evidence

Project configuration lives at `.agentic-harness/config.yml`. Goal state,
redacted events, transcripts, reports, and verification evidence live below the
same `.agentic-harness/` directory.

After a failed or blocked goal, inspect `agentic-harness report` before deciding
what to do next. Use `agentic-harness restart` to retry that same failed goal
while preserving its evidence. Start a fresh goal only when the objective is
intentionally separate.

GUI stop is cooperative: the current bounded tool step finishes, then the task
ends as `Failed with evidence` with a stopped-by-user reason. A late worker
result cannot be accepted as done after cancellation. Session-only API keys are
deliberately absent after a GUI process restart and must be entered again.

## Optional External Orchestration

[Turnstone](https://github.com/turnstonelabs/turnstone) is a separate,
self-hosted orchestration framework. It is not bundled, imported, or installed
by `local-agentic-harness`, and the default embedded GUI does not need it.

Operators who already use an external orchestrator can opt into the generic
`local-goal` compatibility boundary. A direct Turnstone REST/SDK adapter is not
part of this release. See
[Turnstone integration](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/TURNSTONE_INTEGRATION.md) for the exact boundary,
capability preflight, and lifecycle expectations.

The optional long-running route uses a versioned, fail-closed candidate
contract. External completion text is never enough for `Verified done`; a
matching Harness acceptance receipt and independent passing command are
required.

## Other Adapters

The shared engine also supports shell, tmux, GitHub Actions, and custom Python
workers. `LocalLLMAdapter` remains importable for compatibility but is
deprecated; new local and remote model profiles should use `model_agent`. See
[examples](https://github.com/moortekweb-art/agentic-harness/tree/main/examples) for project-local configurations and safety notes.

The small public API remains available:

```python
from agentic_harness import Goal, Supervisor, Worker
```

## Installation

Install the latest published release from PyPI:

```bash
pipx install local-agentic-harness
```

The distribution name avoids a collision with the unrelated
`agentic-harness` package on PyPI. The installed CLI command remains `agentic-harness`.
The same installation also provides `agentic-harness-gui`.

The default branch can contain unreleased CLI and receipt changes. Install the
current GitHub source with:

```bash
pipx install --force git+https://github.com/moortekweb-art/agentic-harness.git
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
- [Public-release boundary](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/PUBLIC_RELEASE.md)
- [Autonomous goal contract](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/CODEX_GOAL_PARITY.md)
- [Evidence contract](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EVIDENCE_CONTRACT.md)
- [Turnstone integration boundary](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/TURNSTONE_INTEGRATION.md)
- [Release checklist](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/RELEASE_CHECKLIST.md)
- [PyPI trusted publishing](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/PYPI_TRUSTED_PUBLISHING.md)
- [Security policy](https://github.com/moortekweb-art/agentic-harness/blob/main/SECURITY.md)
- [Contributor guide](https://github.com/moortekweb-art/agentic-harness/blob/main/CONTRIBUTING.md)
- [External beta guide](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EXTERNAL_BETA.md)
- [External beta feedback template](https://github.com/moortekweb-art/agentic-harness/blob/main/docs/EXTERNAL_BETA_FEEDBACK.md)
- [Examples](https://github.com/moortekweb-art/agentic-harness/tree/main/examples)

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](https://github.com/moortekweb-art/agentic-harness/blob/main/CONTRIBUTING.md) for setup, test, portability, documentation, and pull-request expectations. Security reports belong in the private channel described by [SECURITY.md](https://github.com/moortekweb-art/agentic-harness/blob/main/SECURITY.md).

## License

MIT. Copyright (c) 2026 Michael / Moortekweb. See
[LICENSE](https://github.com/moortekweb-art/agentic-harness/blob/main/LICENSE) and
[AUTHORS.md](https://github.com/moortekweb-art/agentic-harness/blob/main/AUTHORS.md).

## Support

If Agentic Harness helps your local AI workflow, you can support the project at
[Buy Me a Coffee](https://buymeacoffee.com/moortekweb3).
