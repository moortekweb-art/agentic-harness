# Agentic Harness v0.13.4

Version 0.13.4 turns the guided GUI recovery work into one consistent public
release. It keeps the user's task and readable result in the foreground,
reports the route that actually ran, and stabilizes supervised local starts.

## Foreground task ownership

- Keeps the task started by the user pinned while background maintenance and
  qualification work continue separately.
- Restores the pinned task after a refresh and loads its full readable result
  directly in the Tasks view.
- Prevents internal qualification canaries from replacing a completed user
  task or its review actions.
- Supports task deep links through a fragment-only `#task=<id>` reference,
  avoiding authentication or task identifiers in request query parameters.

## Observed route receipts

- Reports the builder, model, reviewer, fallback state, and route identity from
  validated run evidence instead of presenting only the requested route.
- Preserves reviewer evidence across managed continuations and reports a real
  reviewer fallback when an earlier reviewer failed.
- Gives accepted results precedence over older intermediate review state.

## Supervised local starts

- Starts GLM advisory supervision only after the local goal exists, eliminating
  the startup gap where the supervisor could exit before seeing the task.
- Rolls back a newly started goal if requested supervision cannot be started
  and verified.
- Preserves explicit route metadata through start, continuation, review, and
  foreground result presentation.

## Compatibility

This patch release preserves the existing GoalSpec, evidence-v2, assurance,
and managed local-goal contracts. The route receipt remains additive, and the
fragment deep link only affects optional browser navigation.
