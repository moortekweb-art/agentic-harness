# Agentic Harness v0.13.5

Version 0.13.5 fixes a Home-screen state conflict that could make a ready
installation feel stuck on an older completed task.

## Verified boundary hardening

- Treats `npm`, `pnpm`, `yarn`, and `bun` test commands as opaque unless the
  complete repository-controlled verifier boundary is declared with
  `review.assets` or top-level `review_assets`.
- Freezes direct verifier-directory membership and standard Maven, Gradle, and
  .NET test-source membership so candidates cannot add acceptance inputs after
  the manifest is captured.

## Ready means a new task can start

- Keeps Home open when installation readiness says a new task can start.
- Stops an older pinned review result from repeatedly pulling the user back to
  Tasks after navigation, refreshes, or live status updates.
- Removes the blocking recovery card for an older result when the installation
  is ready, while retaining that result in Tasks and History.
- Continues to open the current task and block a new start when readiness
  truthfully reports that review or repair is required.

## Compatibility

This patch preserves foreground task ownership, readable results, fragment-only
task deep links, route receipts, and the managed-task API. It only changes
whether an older foreground result is treated as a blocker when the global
readiness contract says `can_start: true`.
