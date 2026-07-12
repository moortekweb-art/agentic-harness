# Real-agent comparison protocol

Status: preregistered before running the comparison.

Pilot disposition: the first execution from commit `102787b` is invalid for
arm comparison. Its real-agent wrapper omitted the existing structured
completion fields required by the public external-worker contract. All ten
Harness workspaces passed the hidden verifier but correctly remained
unaccepted. The pilot is retained as integration evidence and none of its arm
results are reused below. Protocol revision 2 changes only the wrapper envelope
to the already-documented contract; tasks, prompts, agent, seed, limits,
metrics, and arm order remain fixed. Both arms must be rerun in full.

## Decision

Determine whether placing the same coding agent behind Agentic Harness reduces
false acceptance without making final verified completion unreasonably slower.
This evaluates one agent/version and ten small synthetic maintenance tasks. It
is not an adoption study or a general coding benchmark.

## Fixed design

- Agent: Codex CLI 0.144.1 using its configured default model.
- Arms: one direct invocation versus the same command through Agentic Harness.
- Tasks: the ten entries in `real_agent_tasks.json`.
- Seed: `20260712`; arm order is randomized per task from that seed.
- Prompt, workspace contents, sandbox (`workspace-write`), and timeout are equal.
- The direct arm accepts an exit-zero agent completion claim.
- The Harness arm permits at most three attempts and accepts only after the
  external deterministic verifier passes.
- The wrapper's completion envelope includes plan, current subgoal,
  checkpoint, an explicit requirement linked to Harness reviewer criterion
  `review:1`, and an empty blockers list. The real agent does not author or see
  that reviewer-issued result.
- Expected results remain outside the agent workspace.
- Every attempted run is retained, including failures and timeouts.

## Metrics

Primary:

1. False-accept rate: accepted runs whose hidden verifier fails.
2. Final verifier-pass rate.

Diagnostics:

- recovery after an initially failing verification;
- attempts and elapsed seconds;
- unintended changed paths;
- tokens or provider cost only when emitted by the agent/runtime.

## Guardrails and interpretation

- Do not claim causal generality beyond these tasks and this exact agent.
- Do not omit failed, timed-out, or unavailable runs.
- Do not infer token or monetary cost when telemetry is absent.
- A tie is a valid result.
- No architecture or prompt changes are allowed after the first result is read.
