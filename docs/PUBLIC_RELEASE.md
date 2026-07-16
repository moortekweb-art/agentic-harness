# Public Release Boundary

## Decision

Agentic Harness can be released publicly now as a self-hosted package for one
trusted user and one selected workspace. The maintainer's live GUI is an
operator instance, not the public product, and must not become an anonymous
shared control surface.

This split keeps the useful part public without pretending that a local process
has multi-tenant isolation:

```text
Public source and package
        |
        v
User installs locally -> chooses workspace -> supplies agent or provider
        |
        v
Four bounded work strategies -> independent verification -> durable evidence

Future hosted web app
        |
        v
Identity and quotas -> isolated disposable runner -> same goal/evidence contract
```

## Shippable self-hosted product

The public first run must provide all of the following without Hermes,
Controller, or any maintainer-only service:

- a fresh `pipx install local-agentic-harness` launch;
- the embedded backend and packaged browser assets;
- predictable Home, Tasks, History, and Settings views;
- a plain-language goal with no special prompt grammar;
- visible Quick, Standard, Thorough, and advanced Experiment choices;
- an installed coding agent, a local model, or a bring-your-own
  OpenAI-compatible cloud endpoint;
- fixed-loopback discovery for Ollama, LM Studio, vLLM, and llama.cpp with an
  explicit model choice and structured-action connection test;
- editable provider templates with no bundled credentials;
- explicit remote-data consent for a cloud model;
- automatic deterministic project-check detection with a technical override;
- visible read-only Settings for managed installations; and
- a durable `Verified done`, `Blocked with reason`, or `Failed with evidence`
  result.

Provider, execution method, work strategy, and verification are independent.
The Z.ai and GLM Coding Plan entries are setup conveniences, not proprietary
modes. The Coding Plan template starts with `glm-5.2` for the intended user
workflow but remains editable and carries an entitlement warning. A successful
connection test, not the template name, determines whether the selected account
and model work.

## Explicit non-goals for the local service

The self-hosted GUI does not promise:

- anonymous public access;
- multiple untrusted users in one process or workspace;
- safe execution against the maintainer's repositories or credentials;
- account management, billing, shared provider keys, or team permissions; or
- isolation supplied only by a bearer token, TLS proxy, container label, or
  model prompt.

## Hosted demo and multi-user acceptance gates

A hosted product must be a separate deployment with all gates below satisfied
before public traffic is accepted:

1. Authenticate every user and authorize every workspace and artifact read.
2. Create an isolated, disposable workspace and unprivileged runner for each
   run; never mount maintainer or other-user files.
3. Inject provider credentials per user or per run without writing them to goal
   state, logs, URLs, browser storage, or artifacts.
4. Deny outbound network access by default and explicitly allow only required
   provider and source endpoints.
5. Enforce per-user concurrency, elapsed-time, token, provider-call, tool-call,
   storage, and request-size quotas before work is queued.
6. Separate the browser/API control plane from the execution workers with a
   durable queue and idempotent lifecycle transitions.
7. Record redacted security and billing audit events, detect abuse, and support
   emergency revocation.
8. Tear down compute, mounts, and secrets automatically after completion,
   timeout, cancellation, and crash recovery.
9. Prove tenant separation, restart recovery, cancellation, budget exhaustion,
   and secret non-disclosure with automated adversarial tests.
10. Publish clear data-retention, provider-data-transfer, support, and incident
    response policies.

Until those gates pass, public distribution means users run the harness on
their own trusted system. A maintainer can separately operate a private demo for
invited testers, but that is not a general public launch.

## Release gates

Before tagging the self-hosted release:

- run the complete Python, frontend, lint, type, compile, and release-smoke
  suites;
- install both wheel and source distribution into clean environments;
- complete a fresh-workspace browser journey on desktop and narrow mobile
  layouts;
- prove every primary view and tab-keyboard path is reachable;
- prove all four strategies are submitted and persisted independently of the
  provider selection;
- prove Experiment fails closed without an enforced explicit access limit;
- prove malformed and symlinked existing configuration is never overwritten;
- prove model discovery alone cannot produce Ready and a changed endpoint or
  model invalidates the session validation;
- prove no entered credential appears in configuration, API output, history,
  events, transcripts, reports, or built artifacts; and
- publish screenshots and release notes that describe the self-hosted boundary
  without implying a hosted multi-user service.
