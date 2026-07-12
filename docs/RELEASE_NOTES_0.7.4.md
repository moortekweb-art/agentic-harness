# Agentic Harness v0.7.4

This patch release corrects public installation and evidence documentation and
polishes the browser's first-run verification workflow after independent
review.

## Public trust fixes

- PyPI-safe report examples use `{goal-id}` so package rendering preserves the
  complete `.agentic-harness/runs/{goal-id}/report.md` path.
- Installation copy no longer hard-codes a supposedly current PyPI version.
- The external beta guide explicitly tests current GitHub source while also
  showing the version-agnostic latest-release command.
- The README now embeds the desktop product result, exposes the beta and
  sanitized-feedback paths, and discloses the current harder preregistered
  result: both arms passed 9/10 verifiers, direct execution falsely accepted
  the miss, and Harness refused it without repairing it at higher cost.

## Browser interaction fixes

- Setup now labels its command as the workspace default; each goal labels its
  own verification override and explains the prefill relationship.
- Undo and Redo remain available through documented keyboard shortcuts but no
  longer compete visually with Start goal and Refresh.
- Ordinary disabled controls use a not-allowed cursor; only active operations
  use a progress cursor.
- The product eyebrow now leads with independent verification.
- Acknowledged stopped-run audit residue no longer masks an authoritative idle,
  free execution lane as blocked for a new visitor.
