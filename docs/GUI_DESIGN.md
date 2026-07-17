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

For the portable embedded backend, the browser app is a human layer over the
same durable engine used by the CLI. Managed installations can connect an
external orchestration adapter while preserving the same visible evidence and
fail-closed product contract. The interface simplifies language; it does not
weaken review or invent progress.

## Predictable product structure

The application has four permanent top-level views:

1. **Home** — one plain-language task field, concise run mode, and collapsed
   Checks and Access options.
2. **Tasks** — current progress, plan, evidence, verification, and recovery.
3. **History** — searchable durable task records and export.
4. **Settings** — project-scoped AI connection, project checks, and advanced
   limits.

Settings asks for three decisions:

1. **Execution method** — an installed coding agent, a local
   OpenAI-compatible model, or a cloud OpenAI-compatible model.
2. **Provider and credential** — local-AI detection or an editable provider,
   with technical endpoint and model fields under **Manual connection**. A key
   can come from an environment variable or remain only in this GUI process.
3. **Independent check** — automatically detected project tests when possible,
   with the raw command under **Technical check**.

The Home view separately asks how much effort the assistant should use: Quick,
Standard, Thorough, or the advanced Experiment strategy. These are execution
strategies, not provider or orchestration modes. The selected effort remains
visible while the task runs. Experiment explains and enforces its
built-in-worker and explicit-access requirements before Start is enabled.

Managed installations add a compact **What to expect** card. The default view
shows the selected effort and resolved execution summary; **Choose where it runs**
reveals the supported user-facing route catalog and any model profiles proven
by that installation. Backend mode identifiers remain small technical badges
beside plain labels. Routes that are currently unavailable stay disabled with a
reason; non-product internal canaries can remain hidden. The interface does not
silently fall back across local, private-network, or cloud boundaries.

The interface does not ask users to classify a task as Create, Fix, Check, or
Explain because those choices do not alter execution. One ordinary sentence is
the primary input.

Remote model setup explicitly states that selected file excerpts and tool
results may leave the computer. Saving is disabled until the user confirms
that boundary. The interface never offers to save a plaintext API key.

Local-AI discovery probes only the fixed loopback ports for Ollama, LM Studio,
vLLM, and llama.cpp. It lists every bounded model ID returned by those servers,
then requires a successful structured-action test. It never scans the LAN.

Managed installations keep Settings visible and show Project, AI connection,
and Checks as read-only values. An invalid existing project configuration is
also read-only: the GUI explains the error and refuses to replace the file.

Settings also exposes bounded cycle, elapsed-time, token, provider-call,
and tool-call limits. Reaching a limit produces `Blocked with reason` or
`Failed with evidence`, according to the recorded terminal condition.

## Main journey

### Ready

- Show the selected workspace and execution method.
- Accept one complete task in ordinary language.
- Show **Completion check · Automatic** and **Work area · Entire project** by default.
- For managed installations, show the resolved route, model profile, data
  location, planner, executor, verification, and maturity before submission.
- Let advanced users override the technical check or limit access.
- Disable Start until setup, objective, and independent verification are valid.

### Working

- Switch to the Tasks view as soon as a task is submitted.
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
- Explain that the v1 label means the structured claim and configured checks
  passed; the requirement list is worker-derived and is not yet a frozen,
  independently approved interpretation of every objective clause.
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

The default surface uses task, plan, current step, activity, check, changed
file, evidence, result, `Verified done`, `Blocked with reason`, and `Failed with
evidence`. It hides model prompts, raw JSON, shell output, provider payloads,
queue internals, and worker identities.

Technical details remain available only where they help diagnose a problem.
They are redacted and never include credentials, raw provider traffic, file
contents from tool calls, or hidden reasoning. The product shows observable
actions and evidence rather than private chain-of-thought.

## Visual direction

The selected direction is a refined local workbench:

- warm neutral canvas and high-contrast text;
- restrained teal for primary actions and active progress;
- amber for attention and review;
- red only for destructive or failed states;
- compact information hierarchy without a terminal-shaped main surface;
- build-time img2img illustrations for setup, recovery, empty, and verified
  states, with no runtime image-generation or network dependency;
- one normalized 24px SVG control-icon family; generated raster art never
  substitutes for an interactive control; and
- no decorative animation that implies work not recorded by the backend.

The earlier concept assets remain useful visual references:

- [workbench start](assets/gui-concepts/workbench-start.png)
- [review desk](assets/gui-concepts/review-desk.png)
- [active run room](assets/gui-concepts/run-room-active.png)

They are concepts, not authoritative screenshots. Current behavior and copy are
defined by the packaged static assets and browser tests.

## Accessibility and responsive behavior

- Use semantic headings, forms, labels, buttons, details, and dialogs.
- Keep all controls keyboard reachable with visible focus.
- Implement Left/Right/Home/End keyboard movement across the four tabs.
- Accompany color with status text.
- Clear password fields immediately after submission.
- Wrap long objectives and paths.
- Keep the layout free of horizontal overflow at mobile widths.
- Respect reduced-motion preferences.
- Keep each top-level view independent on narrow screens so active work is never
  buried beneath the task form.

## Decision record

- One distribution and shared engine, with CLI and browser interfaces.
- One visible goal per workspace.
- Four predictable top-level views; Settings is always discoverable.
- Provider-neutral setup based on capability, not model brand.
- Provider, execution method, work approach, and verification are independent.
- Real durable events instead of cosmetic progress.
- Evidence previews are bounded by workspace and artifact ownership.
- Session credentials are memory-only; environment references are durable.
- Local browser delivery through the packaged application.
- The optional external orchestration backend remains an adapter, not a public
  prerequisite.
