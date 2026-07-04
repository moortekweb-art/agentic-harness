# Agentic Harness Launch Posts

Draft only. Do not post without Michael reviewing links and wording.

## Hacker News

Title:

```text
Show HN: Agentic Harness - local-first goal execution for AI agents
```

Body:

```text
I built Agentic Harness, a small MIT-licensed Python package for running long-lived AI agent goals from a local project directory.

The basic loop is:

1. Start a goal.
2. Execute it through a worker adapter.
3. Save state and artifacts under .agentic-harness/.
4. Run deterministic review criteria.
5. Stop with a done/failed state instead of relying on an open-ended auto-continue loop.

The motivation was practical: I wanted a minimal control plane for local coding agents and scripts that did not depend on a hosted service, did not hardcode one machine's paths, and made review gates explicit.

Current status:

- 63 current-package tests
- CI green with tests, ruff, mypy, package build, and wheel install
- MIT licensed
- Python 3.11+
- installable with pipx from the GitHub repo
- examples for shell workers, coding-agent CLIs, tmux workers, GitHub Actions, and local OpenAI-compatible LLM endpoints

Repo: [link to repo]
Case study: [link to case study]

Feedback on the adapter API and deterministic review contract would be useful.
```

## Reddit

### r/LocalLLaMA

Title:

```text
Agentic Harness: local-first goal loops for coding agents and local LLM workers
```

Body:

```text
I put together Agentic Harness, a small MIT-licensed Python package for running agent goals from a local project directory.

It is meant for people who already have local tools, local GPUs, tmux sessions, shell scripts, or OpenAI-compatible local LLM endpoints, and want a simple loop around them:

- start a goal
- execute through an adapter
- write artifacts under .agentic-harness/
- run deterministic review criteria
- stop with a done/failed state

It does not require a hosted agent service. The core package is intentionally small and the provider-specific pieces belong in adapters.

Verified status right now: 63 current-package tests, CI green with ruff/mypy/build smoke, MIT licensed, Python 3.11+, pipx-installable from GitHub.

Repo: [link to repo]
Case study: [link to case study]

I would be interested in feedback from people running local coding agents or OpenAI-compatible local model endpoints. The harness should be useful around model waves such as Ornith 35B without depending on any one model staying dominant.
```

### r/MachineLearning

Title:

```text
[P] Agentic Harness: deterministic review gates for local agent execution
```

Body:

```text
I built Agentic Harness, a small Python project for local-first agent goal execution.

The focus is not on a new model or prompting technique. It is a control loop around agent execution:

- project-local state
- worker adapters
- artifact capture
- loop guards
- deterministic review criteria

The reason for building it was that long-running agent workflows need an auditable state machine and explicit pass/fail review gates, especially when the worker might be a local LLM endpoint, a tmux process, or a shell command.

Current verified state: 63 current-package tests, CI green with ruff/mypy/build smoke, MIT license, Python 3.11+, pipx-installable from GitHub.

Repo: [link to repo]
Case study: [link to case study]

I am looking for technical feedback on the review contract and whether the adapter interface is too small, too broad, or about right for reproducible local workflows.
```

## X/Twitter

```text
1/ I released Agentic Harness, a small MIT-licensed Python package for local-first AI agent goal execution.

It gives agent runs a project-local state machine, artifacts, loop guards, and deterministic review gates.

[link to repo]
```

```text
2/ The core loop is intentionally simple:

Plan -> Execute -> Review -> Done

Workers can be shell commands, coding-agent CLIs, tmux sessions, GitHub Actions, or local OpenAI-compatible LLM endpoints.
```

```text
3/ The main design constraint: no hosted control plane required.

Runtime state lives in .agentic-harness/ inside the project, so a run can be inspected and handed off without depending on one private machine setup.
```

```text
4/ Current verified status:

- 63 current-package tests
- CI green with ruff/mypy/build smoke
- MIT licensed
- Python 3.11+
- pipx-installable from GitHub

Case study: [link to case study]
```

```text
5/ I built it because long-running agent workflows need explicit review gates and loop guards.

Feedback on the adapter API and deterministic review contract is welcome.

[link to repo]
```
