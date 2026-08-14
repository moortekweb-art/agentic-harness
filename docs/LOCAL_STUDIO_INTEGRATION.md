# Local Studio and model runtimes

Agentic Harness is the completion-assurance layer. It can run with its
embedded engine, or it can sit in front of an operator-owned execution
runtime. Local Studio is an optional example of that second arrangement; it
is not bundled with this package and is not required for the default install.

## What each layer owns

| Layer | Owns | Does not claim |
| --- | --- | --- |
| Agentic Harness | Objective, scope, effort, task identity, allowed actions, independent checks, evidence, and the final `Verified done`, `Blocked with reason`, or `Failed with evidence` state | That a model is correct merely because it says a task is complete |
| Local Studio or another managed runtime | The operator's model process, queue, model profile, execution host, and runtime-specific lifecycle | That its worker claim is independent verification |
| vLLM, Ollama, LM Studio, llama.cpp, or a cloud endpoint | Model inference behind the provider contract | That the endpoint supplies the Harness's completion gate |

The public package deliberately keeps these boundaries separate. A provider
choice is not an effort choice, and a managed route is not silently converted
into a local or cloud route. The Harness accepts a result only after the
configured independent review succeeds.

## Recommended arrangements

### Portable self-hosted install

Use the default embedded backend when one trusted user is working in one
workspace:

```bash
pipx install local-agentic-harness
cd /path/to/project
agentic-harness gui
```

In Settings, connect an installed coding app or an OpenAI-compatible model
endpoint. A local vLLM server is one valid provider when it is reachable from
the machine running the GUI and exposes the expected API. The Harness does
not require a particular model brand or a particular inference host.

### Managed or remote execution

Use the optional managed compatibility backend only when an operator already
has a compatible external controller/runtime contract:

```bash
agentic-harness-gui \
  --backend local-goal \
  --doc-root /path/to/project \
  --project-dir /path/to/project \
  --no-open
```

The external runtime may be Local Studio, a vLLM-backed controller, or another
operator-owned service. The exact adapter and service remain deployment
specific; this repository does not ship a private controller, a private
hostname, or a default remote connection. See
[`TURNSTONE_INTEGRATION.md`](TURNSTONE_INTEGRATION.md) for the public
compatibility boundary.

### One work area, one saved-task store

In managed mode the two path flags mean different things:

- `--doc-root` is the execution work area. The backend runs there, and the GUI
  reports it as the workspace.
- `--project-dir` only scopes the saved GUI task history and session state.

Pass the same path to both unless a separate saved-task store is intended. When
they differ, two managed services can share one work area and still disagree
about the current task, because each reads a different store while labelling
itself with the same work area.

Managed `/api/setup` and `/api/health` therefore publish an additive
`workspace_identity` object (`agentic_harness.workspace_identity.v1`) carrying
`work_area`, `state_scope`, a `split` flag, and an opaque `fingerprint`.
Every managed task projection carries the same value at
`metadata.workspace_scope`. Compare the fingerprint across any two pages that
claim one work area: equal means one shared store, different means two. The GUI
shows a short note when `split` is true and stays quiet otherwise.

### Read-only operator compatibility

Operator clients that need a compact integration view can use the versioned
read-only routes under `/v1`: `health`, `routes`, `tasks`, `tasks/current`, and
task-specific reads for the task, events, and artifacts. These routes use the
same Host validation, bearer-token gate, redaction, and `no-store` response
policy as `/api`. There is deliberately no `/v1` write route; task start and
actions remain on the normal `/api` contract.

The route registry reads only the fixed `PRIMARY` and `OVERFLOW` deployment
slots under `AGENTIC_HARNESS_ROUTE_<SLOT>_*`. It publishes model and capability
labels, never provider endpoints or credential environment names. Health probes
are bounded, do not follow redirects, and must use the same HTTP(S) origin as
the operator-configured provider endpoint. A client may display these facts,
but Agentic Harness still owns route selection and records the final decision.
HTTP endpoints in the RFC 6598 CGNAT range remain rejected by default; a
deployment using a trusted private overlay such as Tailscale must opt in with
the exact value `AGENTIC_HARNESS_TRUST_CGNAT_OVERLAY=1`.

In managed mode, the GUI should expose the runtime's route and availability as
read-only facts. The Harness must retain the requested objective, route, work
area, verification policy, and authoritative task identity. A status page,
queue completion, or worker sentence is not enough to produce `Verified done`.

## Network and secret boundary

Keep the GUI loopback-bound unless a private, authenticated reverse proxy is
required. If a private network or Tailscale is used, proxy the loopback
service rather than binding the control surface directly to an untrusted
interface. Configure the GUI token and allowed host explicitly, and never put
credentials in URLs, project configuration, screenshots, reports, or task
events.

Remote model use also requires explicit consent because selected prompts,
file excerpts, and tool observations may leave the machine for the endpoint
chosen in Settings. A private-network endpoint and a same-machine endpoint are
different data-boundary claims and should be presented as such.

## Public product boundary

This package is a self-hosted tool for one trusted user and one selected
workspace. Combining it with Local Studio can make a powerful private
deployment, but it does not turn the process into a hosted multi-user service.
A hosted product needs identity, per-user isolated workspaces and secrets,
quotas, abuse controls, audit records, and teardown outside this package.
