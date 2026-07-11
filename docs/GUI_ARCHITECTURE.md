# GUI Architecture

## Release shape

Agentic Harness is one Python application distributed as
`local-agentic-harness`. The same install provides two interfaces:

- `agentic-harness`, the command-line interface.
- `agentic-harness-gui`, the long-running local browser interface.

Both interfaces use the shared Python engine, the same project state model
under `.agentic-harness/`, and the same deterministic review gates. The wheel
contains the packaged static assets, so the GUI does not require Node,
Electron, a desktop toolkit, or a separately hosted application.

## Default execution path

The default GUI backend is `EmbeddedExecutionBackend`. It constructs the same
`Supervisor`, `AutonomousRunner`, `ArtifactStore`, worker, and reviewer used by
the CLI. A task therefore follows one durable lifecycle:

```text
plain-language goal
        |
        v
persist objective, scope, checks, plan, and requirements
        |
        v
bounded worker cycles -> ordered sanitized events -> visible checkpoint
        |
        v
structured completion claim
        |
        v
independent deterministic review
        |
        +-- pass -> accepted result and preserved evidence
        +-- fail -> repair cycle or explicit blocker
```

The embedded model worker supports user-selected OpenAI-compatible endpoints
and arbitrary model identifiers. The same setup surface also supports an
installed coding-agent executable. Model brand is not part of the execution
contract.

An external orchestration adapter remains available with
`--backend local-goal`. It is optional, is not the default public product path,
and is not installed by this distribution. See
[Turnstone integration](TURNSTONE_INTEGRATION.md) for the supported boundary.

## Components

### Shared factory

`agentic_harness/core/factory.py` is the composition root for both interfaces.
It loads `.agentic-harness/config.yml`, constructs the selected worker, creates
deterministic review criteria, and maps configured goal budgets into an
`AutonomyPolicy`.

### Provider profile and credentials

`agentic_harness/core/providers.py` validates the endpoint, model ID, and
optional environment-variable reference. Model-agent configuration never
accepts a plaintext key. A key is either:

- resolved from the named environment variable when work starts; or
- held only in server process memory for the current GUI session.

Session keys are never returned by the API, written to project state, placed in
URLs, or included in session exports. After a service restart, a session-key
profile reports that the credential must be re-entered. A remote endpoint also
requires explicit persisted consent that selected file excerpts and tool
results may leave the computer.

### Bounded model agent

`agentic_harness/adapters/model_agent.py` implements a small structured-action
loop. It exposes only these built-in actions:

- list and search workspace files;
- read bounded text files;
- create a text file;
- replace one exact text occurrence after a matching SHA-256 read;
- inspect Git status and diff;
- run an operator-configured check; and
- report progress, a blocker, or a completion claim.

It does not provide arbitrary shell, delete, install, service-control, Git
publish, or general network actions. Paths must remain inside the workspace and
the selected safe areas. Repository metadata, harness state, common secret
files, key material, symlink escapes, oversized files, and unowned pre-existing
changes are protected.

### Durable backend and event stream

`agentic_harness/gui/backend.py` runs one background autonomy driver per
project. Goal state and history survive browser or service restarts. Ordered
task events are written atomically to:

```text
.agentic-harness/runs/<goal-id>/events/<sequence>.json
```

Events carry stage, kind, plain summary, checkpoint, cycle, tool status, and an
evidence ID. They omit tool arguments, file contents, provider payloads,
prompts, raw check output, and credentials. The browser polls these durable
records and never invents activity.

### Local server and browser client

`agentic_harness/gui/server.py` serves the package resources and JSON API using
the Python standard library. `agentic_harness/gui/static/` presents setup,
current goal, plan, requirements, measured progress, timeline, verification,
changed files, artifacts, recovery actions, and durable history in plain
language.

## Public API

The embedded backend exposes:

- `GET /api/health` and compatibility alias `GET /api/status`.
- `GET /api/readiness`, `/api/setup`, and `/api/modes`.
- `GET /api/tasks`, `/api/tasks/current`, and `/api/tasks/history`.
- `GET /api/tasks/current/events`.
- `GET /api/tasks/current/file` and `/api/tasks/current/artifact` for bounded
  evidence previews.
- `GET /api/tasks/stream` for an authenticated WebSocket when enabled.
- `GET /api/session` for a redacted durable-history export.
- `POST /api/setup`, `/api/setup/test`, and `/api/setup/credential`.
- `POST /api/tasks`.
- `POST /api/tasks/current/continue`, `/accept`, `/stop`, and `/watch`.

The embedded product permits one active goal per project. Bulk task starts and
raw session imports are rejected. Unknown `/api/*` routes return a JSON 404.

Task records use `agentic_harness.gui_task.v2` and include stable identity,
status, plan, requirements, current subgoal, checkpoint, cycle, events, changed
files, verification, artifacts, allowed actions, safety boundaries, budget
usage, and final-result evidence.

## Progress and completion

Progress is determinate only when a persisted plan or requirement set supplies
a countable denominator. Otherwise the GUI shows an active, indeterminate
state. It reaches 100 percent only after the deterministic reviewer accepts the
goal.

Worker text is not completion evidence. A strict result needs a completed plan,
satisfied requirements with evidence, no blockers, and an independent passing
review command. Budget exhaustion, malformed output, missing credentials,
failed checks, cancellation, and repeated no-progress cycles remain visibly
blocked or stopped.

## Network boundary

- Loopback is the default bind address.
- A non-loopback bind is refused unless `AGENTIC_HARNESS_GUI_TOKEN` is set.
- Authenticated requests use only `Authorization: Bearer`; credentials never
  travel in query strings or WebSocket URLs.
- Host validation limits DNS-rebinding attacks. Reverse-proxy hostnames must be
  explicitly listed in `AGENTIC_HARNESS_GUI_ALLOWED_HOSTS`.
- State-changing requests must be same-origin JSON and are size- and
  rate-limited.
- Session-key entry is accepted only from a loopback client.
- API responses are redacted, non-cacheable JSON. Static and API responses set
  content-security, framing, MIME-sniffing, referrer, and permissions headers.

This remains a local control surface. For remote access, keep the service bound
to loopback and place an authenticated private-network proxy in front of it.

## Release verification

A GUI release must prove:

- the CLI and GUI both use the shared engine;
- a fresh installed wheel completes a real file-changing goal without the
  optional external backend;
- arbitrary local and cloud-compatible model IDs work through a scripted
  OpenAI-compatible provider;
- keys do not enter configuration, URLs, events, history, exports, transcripts,
  or API responses;
- interruption, restart, continuation, budget exhaustion, repeated blockers,
  failed review, and successful acceptance have honest durable states;
- desktop and narrow layouts expose setup, progress, evidence, and recovery
  without overflow; and
- wheel and source distributions contain both entry points and all browser
  assets.
