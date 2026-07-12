# Agentic Harness v0.7.5

This patch release fixes browser goal submission and status recovery when the
external controller is slow or several GUI tabs are open.

## GUI reliability fixes

- Browser API requests now stop after 20 seconds and show a recoverable error
  instead of leaving Start or Refresh busy indefinitely.
- Overlapping health, task, and history refreshes are coalesced in each tab.
- A closed WebSocket immediately falls back to polling, and polling stops once
  the stream reconnects.
- External JSON status reads are serialized and briefly cached so concurrent
  tabs share one controller process tree instead of multiplying expensive
  status commands.
- Start now explains whether it is ready, waiting for setup, or actively
  submitting a verified goal.

## Recovery behavior

Task state remains durable when a browser request times out. Refreshing the GUI
shows the controller's authoritative state, so an operator can retry safely
without trusting a stale spinner.
