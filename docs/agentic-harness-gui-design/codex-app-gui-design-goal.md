# Codex App Goal: Agentic Harness Linux GUI Design

## Where This File Lives

This goal file was created on the Pop-OS/Linux machine at:

```text
/mnt/raid0/home-ai-inference/agentic-harness/codex-app-gui-design-goal.md
```

The main project/repository root is:

```text
/mnt/raid0/home-ai-inference/agentic-harness
```

If you are running this from a different device, such as the Mac mini Codex app,
make sure the `agentic-harness` repo is opened or synced there first. If the
absolute `/mnt/raid0/...` path does not exist on the Mac mini, use the local
Mac copy of the same repository and preserve the same relative output filenames.

On the original Pop-OS/Linux machine, the source plan to read is:

```text
/mnt/raid0/home-ai-inference/agentic-harness/linux-gui-agentic-harness-plan.md
```

The required result file should be written next to this goal file in the repo
root. On the original Pop-OS/Linux machine that full output path is:

```text
/mnt/raid0/home-ai-inference/agentic-harness/codex-app-gui-design-result.md
```

If working from a Mac mini clone, write:

```text
<your-local-agentic-harness-repo>/codex-app-gui-design-result.md
```

Do not write the result into a temporary downloads folder, chat transcript, or
Codex internal scratch directory. It should be a real markdown file in the
`agentic-harness` repository root.

## Goal

Design a Pop-OS/Linux GUI for Agentic Harness that turns the current script-based AI harness into a simple human-facing app.

The user should not need programming knowledge. The app should hide planner names, worker IDs, JSON, shell commands, queue mechanics, and goal-packet syntax unless the user opens an Advanced/details area.

Use strong design judgment. This is a product/design task, not backend implementation.

## Recommended Model

Use a high-capability reasoning/design setting if available, such as GPT-5.5 High.

Reason: this task needs product judgment, information architecture, UX simplification, visual direction, and careful tradeoff analysis. It is not just generating a pretty screen.

## Context

Agentic Harness currently works, but it feels too much like scripts and programming.

The backend has:

- local-goal style queues
- run directories
- worker dispatch
- Mode 3A / GLM cloud worker
- review and acceptance gates
- logs and completion markers
- some no-babysitting/autonomy goals

The GUI should become the human layer.

The human should experience:

1. Open the app.
2. Choose what kind of help they want.
3. Type what they want done in plain English.
4. Press Start.
5. See simple progress.
6. Review/accept only when needed.

## Existing Plan To Read

Read these files first:

```text
/mnt/raid0/home-ai-inference/agentic-harness/linux-gui-agentic-harness-plan.md
/mnt/raid0/home-ai-inference/agentic-harness/codex-app-design-workflow-plan.md
```

If working on the Mac mini and that absolute path does not exist, read the same
files from the local cloned repo:

```text
<your-local-agentic-harness-repo>/linux-gui-agentic-harness-plan.md
<your-local-agentic-harness-repo>/codex-app-design-workflow-plan.md
```

Use them as the source of truth.

## Four Human Modes

The GUI must present these as human choices, not technical backend modes.

### Mode 1

Human label:

```text
Use this computer
```

Meaning:

Local steady worker. Best for normal bounded work on the Pop-OS machine.

### Mode 2

Human label:

```text
Let GLM guide the plan
```

Meaning:

GLM-guided local worker. Best for harder local tasks where GLM helps structure the plan and local tools do the work.

### Mode 3

Human label:

```text
Let GLM carry a long task
```

Meaning:

Cloud GLM long task. Best for Codex `/goal`-like long-horizon work.

### Mode 4

Human label:

```text
Try experimental GLM
```

Meaning:

Experimental direct GLM canary. Best only for tiny safe experiments. It should look visibly experimental, not like the default.

## Required Screens

Design at least these screens:

1. **Home / Start Work**
   - Four mode choices.
   - Plain language descriptions.
   - Large text box: "What do you want done?"
   - Start button.
   - Optional "Add boundaries" or "Safe areas" section.

2. **Active Work**
   - Simple status: Starting, Working, Checking work.
   - Timeline or progress trail.
   - Current plain-English summary.
   - Button: "Check now" or "Move forward".
   - Advanced/details drawer hidden by default.

3. **Needs Review**
   - Summary of what changed.
   - Verification results.
   - Files changed in plain language.
   - Buttons:
     - Accept
     - Ask it to continue
     - Stop
   - Advanced/details drawer.

4. **Done / Blocked**
   - Final result summary.
   - What was verified.
   - What remains, if anything.
   - Clear next action.
   - Advanced evidence/details available but secondary.

## Design Requirements

- Target Pop-OS/Linux desktop browser first.
- Make it feel like a calm local operations app.
- Do not make it feel like SaaS marketing.
- Do not make it feel like a terminal dashboard.
- Do not expose programmer words in the main UI.
- Avoid giant hero sections.
- Keep controls clear and practical.
- Use readable spacing and typography.
- Main UI should answer:
  - What can I ask it to do?
  - Which mode should I use?
  - Is it working?
  - Does it need me?
  - What did it finish?

## Words To Hide In Main UI

Do not show these in the default user-facing UI:

- planner
- executor
- worker ID
- queue JSON
- tmux
- vLLM
- run directory
- completion marker
- Mode 3A
- shell command
- raw logs

These can appear only in an Advanced/details drawer.

## Words To Prefer

Use words like:

- Use this computer
- Let GLM guide the plan
- Let GLM carry a long task
- Try experimental GLM
- Starting
- Working
- Checking work
- Needs review
- Done
- Blocked
- What changed
- What passed
- Needs you
- Advanced details

## Visual Directions Required

Create 2-3 distinct visual directions or mockup concepts.

Prefer exactly these three named directions unless you have a strong reason to
rename them:

1. **Calm Local Operations**
   - Practical, quiet, trustworthy.
   - Should feel like a local control panel.

2. **Friendly Assistant Control**
   - Warmer and more guided.
   - Should help non-technical users feel confident.

3. **Power User Cockpit**
   - Denser, more operational.
   - Should remain human-friendly and avoid terminal/dashboard clutter.

For each direction include:

- Name of the direction.
- What it feels like.
- Why it fits or does not fit this product.
- Strengths.
- Risks.
- Which one you recommend.

If image generation is available, generate mockups or visual references for each direction.

If image generation is not available, produce detailed screen descriptions and layout specs.

## UX Decisions To Resolve

Think through and answer:

1. Should the first screen recommend a mode automatically?
2. Should mode selection happen before or after the user types the task?
3. How should the app explain Mode 4 is experimental without scaring the user?
4. What should the app show while the backend is working but quiet?
5. How should the app ask for human approval?
6. What should a blocked state look like?
7. What details belong in the Advanced drawer?
8. What should the final "Done" answer look like?

## Output File Required

When finished, create this markdown file:

```text
/mnt/raid0/home-ai-inference/agentic-harness/codex-app-gui-design-result.md
```

If working on the Mac mini, create the same file at:

```text
<your-local-agentic-harness-repo>/codex-app-gui-design-result.md
```

The result file must include:

1. Executive summary.
2. Recommended visual direction.
3. Three visual concepts with links to generated assets.
4. Description of all required screens.
5. Mode-card text.
6. Status language and event timeline language.
7. Review/acceptance flow.
8. Advanced drawer contents.
9. Empty, loading, quiet-worker, blocked, error, and success states.
10. Accessibility notes.
11. Implementation notes for a future local web GUI.
12. Open questions or risks.
13. Links or embedded references to generated mockups/images, if any.

## Done Criteria

This design goal is complete when:

- `codex-app-gui-design-result.md` exists.
- It includes three visual directions or mockup concepts.
- `docs/assets/gui-concepts/` contains three concept references if image generation is available.
- It recommends one direction.
- It explains what to keep from the other two directions.
- It describes all required screens.
- It keeps the main UI human-friendly and non-programmer.
- It explains how technical details are hidden but still available.
- It gives enough detail for another agent/developer to implement the GUI.

Do not implement the GUI yet. This goal is design and product planning only.
