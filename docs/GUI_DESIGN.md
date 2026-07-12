# GUI Design

## Product promise

The GUI turns one plain-language goal into a visible, evidence-backed work
session. A user should be able to answer these questions without reading a
script, prompt, or internal agent transcript:

- What workspace and execution method will be used?
- What is the current subgoal?
- What has actually happened?
- Which checks passed or failed?
- What changed, and where is the evidence?
- Is the result `Verified done`, `Blocked with reason`, or `Failed with
  evidence`?

The browser app is a human layer over the same durable engine used by the CLI.
It simplifies language; it does not weaken review or invent progress.

## Setup

The first-run dialog asks for four decisions:

1. **Execution method** — an installed coding agent, a local
   OpenAI-compatible model, or a cloud OpenAI-compatible model.
2. **Provider details** — endpoint and arbitrary model ID when a model is used.
3. **Credential source** — no key, an environment-variable name, or a key held
   only for this GUI process.
4. **Independent check** — a command that can prove the result outside the
   worker's own claim.

Remote model setup explicitly states that selected file excerpts and tool
results may leave the computer. Saving is disabled until the user confirms
that boundary. The interface never offers to save a plaintext API key.

The setup dialog also exposes bounded cycle, elapsed-time, token, provider-call,
and tool-call limits. Reaching a limit produces `Blocked with reason` or
`Failed with evidence`, according to the recorded terminal condition.

## Main journey

### Ready

- Show the selected workspace and execution method.
- Accept one complete goal in ordinary language.
- Let the user narrow safe areas or checks.
- Disable Start until setup, objective, and independent verification are valid.

### Working

- Put the active goal above the new-goal form, including on narrow screens.
- Show the current subgoal, checkpoint, and cycle.
- Render the persisted plan and requirements.
- Stream sanitized, ordered activity events.
- Use determinate progress only when plan or requirement counts support it;
  otherwise show an indeterminate working state.
- Offer only actions that the backend says are currently valid.

### Checking and review

- Separate worker-reported checks from independent review.
- Show pass/fail state and a short, redacted message.
- Never turn worker text such as “done” into acceptance.
- If repair is possible, continue automatically within the configured budgets.
- If a genuine human decision is needed, explain it and expose Continue or Stop.

### Verified done

- Show `Verified done` only after independent verification passes.
- Show the accepted result summary.
- List changed files and independent checks.
- Link to bounded previews of changed text and recorded artifacts.
- Preserve the run in history across refreshes and service restarts.

### Blocked with reason

- State the concrete missing credential, repeated blocker, exhausted budget, or
  safety boundary.
- Keep all evidence already produced.
- Permit a fresh goal after the terminal state is durable.

### Failed with evidence

- State the failed execution, failed independent check, or cancellation.
- Keep the check result, changed-file summary, attempt history, and report path.
- A stopped task must not become `Verified done` because a late worker cycle
  returned.
- Permit a fresh goal after the terminal state is durable.

## Language boundary

The default surface uses goal, plan, current step, activity, check, changed
file, evidence, result, `Verified done`, `Blocked with reason`, and `Failed with
evidence`. It hides model prompts, raw JSON, shell output, provider payloads,
queue internals, and worker identities.

Technical details remain available only where they help diagnose a problem.
They are redacted and never include credentials, raw provider traffic, file
contents from tool calls, or hidden reasoning. The product shows observable
actions and evidence rather than private chain-of-thought.

## Visual direction

The selected direction is a calm local workbench:

- warm neutral canvas and high-contrast text;
- restrained teal for primary actions and active progress;
- amber for attention and review;
- red only for destructive or failed states;
- compact information hierarchy without a terminal-shaped main surface;
- no decorative gradients or activity animation that implies work not recorded
  by the backend.

The earlier concept assets remain useful visual references:

- [workbench start](assets/gui-concepts/workbench-start.png)
- [review desk](assets/gui-concepts/review-desk.png)
- [active run room](assets/gui-concepts/run-room-active.png)

They are concepts, not authoritative screenshots. Current behavior and copy are
defined by the packaged static assets and browser tests.

## Accessibility and responsive behavior

- Use semantic headings, forms, labels, buttons, details, and dialogs.
- Keep all controls keyboard reachable with visible focus.
- Accompany color with status text.
- Clear password fields immediately after submission.
- Wrap long objectives and paths.
- Keep the layout free of horizontal overflow at mobile widths.
- Respect reduced-motion preferences.
- Move active status ahead of the start form on a narrow screen so current work
  is never hidden below input controls.

## Decision record

- One distribution and shared engine, with CLI and browser interfaces.
- One visible goal per workspace.
- Provider-neutral setup based on capability, not model brand.
- Real durable events instead of cosmetic progress.
- Evidence previews are bounded by workspace and artifact ownership.
- Session credentials are memory-only; environment references are durable.
- Local browser delivery through the packaged application.
- The optional external orchestration backend remains an adapter, not a public
  prerequisite.
