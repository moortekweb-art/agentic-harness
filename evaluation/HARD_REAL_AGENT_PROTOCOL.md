# Harder real-agent comparison protocol — revision 2

Status: revision 2 preregistered after revision 1 was invalidated and before
any revision-2 task result was observed. Revision 1 remains published as an
invalid pilot; none of its primary outcome claims are reused.

This follow-up uses Codex CLI 0.144.1 with explicit model `gpt-5.6-sol` on ten
synthetic multi-file or edge-preservation maintenance tasks. Code tasks use
behavioral verification so equivalent correct implementations pass; exact-file checks are
reserved for tasks whose requirements specify exact config, version, or documentation state. It reuses the
revision-3 evidence pipeline from the first study: randomized arm order, three
maximum Harness attempts, hidden-from-prompt deterministic verification,
incremental raw rows, per-attempt transcripts, raw-derived aggregates, and
optional telemetry.

The primary metrics remain false accepts and final verifier-pass rate.
Attempts, recovery, elapsed time, transcript-reported tokens, and unintended
paths remain diagnostics. Both arms receive the same task objective and
starting workspace; Harness necessarily adds lifecycle/evidence instructions,
so this is an end-to-end system comparison rather than equal full prompts or
equal budgets.

Task manifest: `hard_real_agent_tasks.json` schema v2. Seed: `2026071202`. No task, prompt,
model, timeout, verifier, or metric may change after the first result is read.
A tie, failed run, timeout, or infrastructure failure must be published.

False-accept counts are a treatment-integrity sanity check: the Harness arm is
defined to require the outcome verifier, while the direct arm trusts exit-zero.
They test whether that policy boundary was enforced, not whether Harness makes
the underlying model more capable. This remote-model artifact is auditable but
not exactly reproducible because provider and model internals are not immutable.
