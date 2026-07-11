# Turnstone Integration Boundary

## Status

[Turnstone](https://github.com/turnstonelabs/turnstone) is a separate,
self-hosted, local-first orchestrator for tool-using AI agents. Agentic Harness
does not vendor Turnstone, import its Python package, manage its services, or
install its database and cluster components.

Turnstone is optional and not bundled with Agentic Harness.

The portable Agentic Harness product is complete without Turnstone:

- the embedded engine is the default CLI and GUI backend;
- an installed coding-agent CLI or user-selected OpenAI-compatible model can
  execute a goal;
- project state, progress events, evidence, resource budgets, and independent
  verification remain inside `.agentic-harness/`; and
- one `local-agentic-harness` install provides both interfaces.

The public package includes a generic `LocalGoalBridge` compatibility boundary
for operators who already have an external orchestrator. An operator-maintained
wrapper may translate that contract to Turnstone. The wrapper and Turnstone
runtime are not included.

| Capability | Delivery state |
|---|---|
| Embedded Agentic Harness GUI/CLI goal engine | Installed capability in this package |
| Generic `local-goal` compatibility bridge | Installed capability in this package; explicit opt-in |
| Turnstone-compatible wrapper | Deployment-owned component; not shipped here |
| Direct Turnstone REST or SDK client | Not done |
| Turnstone service installation, upgrade, and operations | Not done by this package |

## Why the Boundary Is Narrow

Turnstone covers a broader orchestration surface: tool-rich conversations,
parallel workstreams, multiple provider families, MCP servers, cluster routing,
and optional governance controls. Agentic Harness focuses on a smaller contract:
one project goal, durable evidence, bounded continuation, and deterministic
completion review.

Keeping those products separate has practical benefits:

- a pipx install does not start services or add database dependencies;
- the default GUI remains usable on a single developer machine;
- Turnstone can evolve or be replaced without changing the public goal-state
  and evidence model; and
- the compatibility contract can keep execution results separate from a
  deployment-owned acceptance decision.

## Ideas Applied in the Embedded Engine

The embedded implementation adopts several orchestration ideas that also make
Turnstone useful, without claiming that Turnstone itself is bundled:

- durable goal identity and lifecycle state;
- an explicit perceive, plan, act, evaluate, and continue loop;
- live structured events instead of fabricated percentage progress;
- capability/setup preflight before a task can start;
- bounded provider and tool calls;
- cooperative cancellation with durable evidence;
- bring-your-own local or cloud model profiles; and
- separation between execution evidence and independent acceptance.

The executor may propose a completion report. It cannot self-accept the goal:
configured deterministic verification must pass, requirements must carry
evidence, and a blocked or budget-exhausted result remains incomplete.

## Public Integration Contract

The optional route is:

```text
Agentic Harness GUI
        |
        | --backend local-goal
        v
LocalGoalBridge
        |
        | AGENTIC_HARNESS_LOCAL_GOAL
        v
operator-maintained compatibility wrapper
        |
        v
Turnstone or another external orchestrator
```

Select it explicitly:

```bash
export AGENTIC_HARNESS_LOCAL_GOAL=/absolute/path/to/compatible-wrapper
agentic-harness-gui \
  --backend local-goal \
  --project-dir /path/to/project \
  --no-open
```

If `AGENTIC_HARNESS_LOCAL_GOAL` is unset, the compatibility backend looks for
`scripts/local-goal` beneath the explicit `--doc-root`, then beneath
`AGENTIC_HARNESS_DOC_ROOT`, then beneath the current directory. This lookup
exists only for the optional backend; it is not used by the embedded default.

The executable must support the command shapes the bridge invokes:

- `capabilities --json` for readiness and active background-supervision truth;
- `status --json` for current durable state;
- `quick-start`, `premium-start`, or `enqueue` for an operator-selected external
  execution lane;
- `continue [--feedback ...]`, `accept`, and `stop` for explicit lifecycle
  decisions; and
- `monitor` with `--auto-accept`, `--auto-continue`, `--auto-dispatch`,
  `--auto-commit-owned`, and `--json` for the legacy diagnostic/supervisor
  contract.

`capabilities --json` must expose a `supervision.watcher` object, either at the
top level or below `capabilities`, with at least:

```json
{
  "supervision": {
    "watcher": {
      "timer_active": true,
      "state": "active",
      "summary": "Background supervisor is active."
    }
  }
}
```

Agentic Harness treats supervision as active only when `timer_active` is true
and `state` is `active`. An executable merely existing on disk is not readiness
proof.

The external backend's status JSON should preserve stable task identity and
distinguish at least queued, running, checking/review, accepted/done, blocked,
failed, and stopped states. It should return changed files and verification
evidence when available. Unknown or sparse fields are normalized conservatively;
the GUI must not invent progress or acceptance.

The bridge necessarily trusts the state returned by the compatibility wrapper.
It cannot prove that Turnstone ran an independent judge or deterministic check
behind that wrapper. Verify this deployment-owned trust boundary before treating
an external `accepted` state as completion evidence.

## Security and Ownership

Enabling the compatibility backend delegates execution authority to the wrapper
and external orchestrator. The embedded agent's path containment, hash-before-
replace rule, and restricted tool set do not automatically constrain that
external process.

The integration owner is responsible for:

- authenticating and authorizing the Turnstone endpoint;
- deciding which models, tools, MCP servers, and workspaces it may access;
- keeping credentials outside command output and project artifacts;
- making start and lifecycle operations idempotent;
- mapping cancellation without accepting a late completion; and
- preserving an independent review boundary before final acceptance.

Do not place a Turnstone token or provider API key in
`.agentic-harness/config.yml`, a wrapper command line, GUI URL, event, or report.
Use the external service's secret-entry and runtime environment mechanisms.

## Provider Coverage

Turnstone supports a broader provider surface than the embedded Agentic Harness
model transport. According to its upstream project documentation, Turnstone can
route OpenAI-compatible endpoints, the Anthropic Messages API, and Google
Gemini, and can expose shell, file, search, web, planning, and MCP tools.

Agentic Harness's embedded engine intentionally supports only
OpenAI-compatible chat completions and a restricted repository tool set. Native
Anthropic, Gemini, Turnstone workstreams, MCP, parallel sub-agents, cluster
routing, RBAC, and SSO are not silently provided by selecting the embedded
backend.

## Private Deployment Note

> This note describes one maintainer environment, not public package behavior
> or a release guarantee.

The maintainer's external deployment manifest records a local-only Turnstone
sidecar pinned to v1.7.2. The same manifest records v1.7.3 as
`validated-promote-recommended`, meaning it is a validated promotion candidate,
not proof that production has already been upgraded.

That manifest, its wrapper, container images, ports, routing choices, service
state, and promotion approval are outside this repository. Public users should
select and verify their own Turnstone release from the
[upstream repository](https://github.com/turnstonelabs/turnstone) rather than
copying a private deployment pin.

## Verification Checklist for an Optional Adapter

Before calling a Turnstone-backed deployment usable:

1. Confirm the wrapper is executable and its location is explicit.
2. Verify `capabilities --json` reports the actual active supervisor, not a
   static configured flag.
3. Start one harmless sandbox task with a stable external task ID.
4. Observe queued, running, evidence/review, and terminal state transitions.
5. Confirm repeat reads and commands do not duplicate the task.
6. Stop a second task and prove a late executor result is not accepted.
7. Confirm changed files and verification evidence are normalized without raw
   secrets or private provider payloads.
8. Restart the wrapper or GUI and prove durable status can be reconstructed.
9. Run an independent verification criterion before accepting completion.

Passing that checklist verifies the deployment-specific adapter. It does not
turn Turnstone into a dependency of the public Agentic Harness distribution.
