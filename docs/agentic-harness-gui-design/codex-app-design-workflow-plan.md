# Codex App Design Workflow Plan

## Purpose

Use the Codex app as the visual/product design workspace for the Agentic Harness Linux GUI. The Codex app should produce the design package, not implement the GUI.

The goal is to make a script-based AI harness feel like a simple human app: choose a work mode, type a normal request, press Start, and see clear progress.

## Workspace

Use the Mac mini clone:

```text
/Users/MikeMacMini/agentic-harness
```

If working from the original Linux machine, the matching repo is:

```text
/mnt/raid0/home-ai-inference/agentic-harness
```

Primary input files:

```text
codex-app-gui-design-goal.md
linux-gui-agentic-harness-plan.md
```

Required output file:

```text
codex-app-gui-design-result.md
```

Required concept image directory:

```text
docs/assets/gui-concepts/
```

## Recommended Codex App Setup

- Model: GPT-5.5 High or the strongest available reasoning/design model.
- Mode: design/product planning, not implementation.
- Image generation: enabled if available.
- Repository: open `/Users/MikeMacMini/agentic-harness`.
- Goal source: paste or reference `codex-app-gui-design-goal.md`.

Do not ask Codex app to build the GUI in this phase. Ask it to create the design result and image/concept assets only.

## Codex App Prompt

Use this as the Codex app `/goal` prompt:

```text
Read codex-app-gui-design-goal.md and linux-gui-agentic-harness-plan.md.

Create the design package for the Agentic Harness Linux GUI.

This is a design/product task only. Do not implement GUI code.

Produce:
1. codex-app-gui-design-result.md in the repository root.
2. Three visual concept references under docs/assets/gui-concepts/.

The GUI must let a non-programmer choose one of four human modes, type what they want in plain English, start work, and see simple progress. Hide planner names, executor details, queue JSON, shell commands, logs, run directories, and Mode 3A wording from the main UI. Put those only in Advanced details.

Required screens:
- Start Work
- Active Work
- Needs Review
- Done / Blocked

Required modes:
- Use this computer
- Let GLM guide the plan
- Let GLM carry a long task
- Try experimental GLM

Compare three visual directions, recommend one, and explain the tradeoffs.

The result markdown must be implementation-ready for a future local web GUI.
```

## Required Visual Directions

Codex app should create three distinct directions:

1. **Calm Local Operations**
   - Practical, quiet, trustworthy.
   - Best default candidate.
   - Should feel like a local control panel.

2. **Friendly Assistant Control**
   - Warmer and more guided.
   - Better for non-technical confidence.
   - Risk: may feel too soft or vague.

3. **Power User Cockpit**
   - Denser, more operational.
   - Better for advanced users.
   - Risk: may feel like a dashboard or terminal wrapper.

For each direction, create or describe:

- Start Work screen.
- Active Work screen.
- Needs Review screen.
- Done / Blocked screen.
- Strengths.
- Risks.
- Whether to recommend it.

## Main UI Rules

Default UI must hide these terms:

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

Default UI should use these terms:

- Use this computer
- Let GLM guide the plan
- Let GLM carry a long task
- Try experimental GLM
- What do you want done?
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

## Required Result File Structure

`codex-app-gui-design-result.md` must include:

1. Executive summary.
2. Recommended visual direction.
3. Three visual concepts with links to generated assets.
4. Start Work screen spec.
5. Active Work screen spec.
6. Needs Review screen spec.
7. Done / Blocked screen spec.
8. Four mode card titles, descriptions, helper text, and warning text.
9. Status language and event timeline language.
10. Review/acceptance flow.
11. Advanced details drawer contents.
12. Empty, loading, quiet-worker, blocked, error, and success states.
13. Accessibility notes.
14. Implementation notes for local web GUI.
15. Open questions and risks.

## Acceptance Checklist

The Codex app design pass is complete only when:

- `codex-app-gui-design-result.md` exists.
- `docs/assets/gui-concepts/` contains three concept references.
- The result recommends one direction.
- The result explains what to keep from the other two directions.
- All four required screens are covered.
- All four modes have final user-facing copy.
- Advanced details are clearly separated from default UI.
- The blocked and needs-review states are designed.
- The output is specific enough for implementation without new UX decisions.

## Handoff Back To Linux/Coding Agent

After Codex app finishes, sync or show:

```text
/Users/MikeMacMini/agentic-harness/codex-app-gui-design-result.md
/Users/MikeMacMini/agentic-harness/docs/assets/gui-concepts/
```

The Linux/coding agent will then implement:

- backend contracts,
- local GUI server,
- frontend screens,
- API endpoints,
- tests,
- Playwright screenshot verification,
- GitHub publication.
