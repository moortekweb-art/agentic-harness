# Agentic Harness v0.13.3

Version 0.13.3 makes managed model routing explicit and makes the GUI
understandable without a separate expert explaining the harness lifecycle.

## Managed model routing

- Adds canonical route identities for local build, Turnstone GLM plus local
  build, cloud GLM build, and direct GLM read-only audit.
- Rejects stale or mismatched route identities before external dispatch.
- Adds optional GLM-5.2 advisory supervision for local builds, including live
  start/status verification and rollback when task start fails.
- Enables the distinct GLM read-only audit contract while keeping direct GLM
  implementation blocked.
- Preserves MiniMax as the generic cloud automation default and Kimi as its
  bounded fallback; explicit GLM routes remain separately pinned.

## Guided task lifecycle

- Adds an in-app guide that translates starting, working, checking, review, and
  attention states into plain language and a single recommended next step.
- Makes quiet background execution explicit so a temporarily still screen does
  not look like a stopped worker.
- Presents `needs_review` as **Your result is ready** and explains that the
  assistant stopped safely rather than crashed.
- Shows the worker's result summary and evidence counts before asking the user
  to make a decision.
- Replaces harness-oriented decisions with **Review result**, **Ask for
  changes**, **Approve and finish**, and **Stop without approving**.
- Describes the interactive worker as “the assistant” instead of assuming that
  a new user knows which coding application is underneath the managed route.

## Honest completion expectations

- Warns before starting audits, assessments, ratings, reports, and other
  judgment tasks that the user will review the result.
- Describes other tasks as using automatic checks when possible, while retaining
  fail-closed human review when no reliable verifier can be established.
- Keeps the guide deterministic: it can explain a worker result, but it cannot
  approve that result or weaken independent verification.

## Compatibility

This release does not change GoalSpec, evidence-v2, assurance-mode, amendment,
or managed local-goal contracts. Existing API consumers receive one additive
`guide` object and clearer labels on the existing action identifiers.
