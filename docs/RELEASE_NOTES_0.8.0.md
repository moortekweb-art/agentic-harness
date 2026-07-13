# Agentic Harness v0.8.0

This release restores the intended public first-run product while keeping the
local control surface single-user and self-hosted.

## Four provider-independent work approaches

- Quick task applies small cycle, time, token, provider-call, and tool-call
  caps to one focused change.
- Plan first is the balanced default and explicitly asks the worker to record a
  plan and requirements before editing.
- Keep working uses the full limits configured for the workspace and preserves
  resumable checkpoints across repair attempts.
- Bounded experiment applies the smallest limits and fails closed unless the
  built-in model worker and at least one explicit allowed path are selected.

The selected approach is persisted with the goal, remains visible while work
runs, and can only tighten workspace limits. It is independent of the model,
provider, execution location, and verification command.

## Bring your own provider

The setup dialog still accepts any OpenAI-compatible endpoint and model ID. It
now also provides editable, non-secret convenience templates for a custom
provider, the Z.ai general API, and a Z.ai GLM Coding Plan workflow. The Coding
Plan template begins with `glm-5.2` and an explicit entitlement warning; the
connection test remains the authority for the user's current account and model.

Keys are not included in templates or returned by the API. Existing session,
environment-variable, cloud-consent, and redaction boundaries remain in force.

## First-run and release boundary

- A fresh embedded GUI shows all four approaches without an external managed
  backend, Hermes, or Controller.
- Setup identifies the application as a local self-hosted workspace for one
  trusted user.
- Saving Setup refreshes the full visible state immediately, avoiding a stale
  “Setup needed” card beside a “Ready” header.
- Mobile uses one accessible approach selector and has no horizontal overflow
  at 390 by 844 pixels.
- Public documentation separates the shippable self-hosted package from a
  future hosted multi-user product, whose identity, isolation, quotas, secret
  handling, cleanup, and abuse-control gates are not supplied by this process.

## Compatibility

The optional `local-goal` backend retains its existing managed route names and
API request field. Embedded clients use the new `strategy` field; the backend
continues to accept the older `mode` field as a compatibility alias. Existing
goal, evidence, and task contracts remain readable.
