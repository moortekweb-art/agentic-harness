# Attraction Plan

## Target Audience

- Indie AI developers who have useful scripts but need a safer goal loop.
- Homelab operators running local LLMs, coding agents, and automation jobs.
- Small teams building internal agent pipelines without adopting a large framework.
- DevOps-minded engineers who care about state, artifacts, retries, and review gates.
- Open-source maintainers experimenting with agent-assisted maintenance.

## Channels Ranked By ROI

### 1. Hacker News

Best angle: "small, boring control loop for local agents" rather than "new agent framework."

Title options:

- Show HN: A small Python harness for long-running local agent goals
- Show HN: Agentic Harness - goal state, artifacts, and review gates for local agents
- I built a boring control loop for local coding agents

Timing:

- Tuesday, Wednesday, or Thursday.
- Post between 7:00 and 9:00 AM Pacific.
- Be present for the first two hours to answer technical questions.

Post structure:

- One paragraph problem statement.
- One paragraph on why it is not another orchestration framework.
- Link to README and one concrete shell-worker example.
- Ask for feedback on adapter design and review contracts.

### 2. Reddit

Ranked subreddits:

- `r/LocalLLaMA`: best fit if the post focuses on local LLM workflows and OpenAI-compatible endpoints.
- `r/devops`: good fit if framed as state, artifacts, and safe automation instead of "AI agents."
- `r/MachineLearning`: lower ROI unless paired with a thoughtful write-up about deterministic review and failure modes.

Angles:

- `r/LocalLLaMA`: "How are you managing long-running local agent jobs?"
- `r/devops`: "A small state machine for agent jobs, with artifacts and review gates."
- `r/MachineLearning`: "What should deterministic review look like for agent execution loops?"

### 3. Twitter/X Thread

Hook:

> Most local agent setups become one giant script. I split mine into a small goal state machine, adapters, artifacts, and deterministic review.

Thread:

1. "Agentic Harness is a small Python control loop for local agents: start goal -> execute -> review -> done."
2. "It is intentionally not a big framework. The core only knows goals, state, artifacts, review, and loop limits."
3. "Adapters do the work: shell, tmux, GitHub Actions, or an OpenAI-compatible local LLM endpoint."
4. "The useful part is operational: JSON state, markdown reports, deterministic pass/fail criteria, and a circuit breaker."
5. "Looking for feedback from people running local coding agents or homelab AI workflows."

### 4. Dev.to Or Medium

Article outline:

- Title: "Building a Boring Harness for Local AI Agents"
- The failure mode: internal scripts grow into unreviewable automation.
- The design: state machine, adapters, artifacts, review gates.
- Example: shell worker in 10 minutes.
- Example: local LLM adapter.
- What is intentionally missing.
- Next steps and open questions.

### 5. Discord / Slack Communities

Share only where project links are welcome and after participating in the community:

- LocalLLaMA Discords or local AI communities.
- MLOps Community Slack.
- Open-source AI builder groups.
- Homelab/self-hosted Discords with automation channels.

Good ask:

> I extracted the control-loop part of my local agent setup into a small Python package. I would like feedback on the adapter API and deterministic review model.

## Positioning

### LangChain

LangChain is a broad app framework. Agentic Harness is a small execution control loop for people who already have tools and want state, artifacts, and review.

### CrewAI

CrewAI focuses on multi-agent role orchestration. Agentic Harness focuses on one goal lifecycle and pluggable workers.

### AutoGen

AutoGen focuses on agent conversation patterns. Agentic Harness focuses on operational boundaries: goals, adapters, artifacts, loop guard, and review.

### SWE-agent

SWE-agent is stronger for benchmark-style software engineering tasks. Agentic Harness is a general local harness for arbitrary goal execution.

## What To Build Next

1. Real GitHub Actions CI with lint, tests, packaging, and README smoke checks.
2. Adapter examples for common local coding agents: Codex CLI, OpenCode, Aider, and shell scripts.
3. Keep deterministic review helpers small and demand-driven; the current
   artifact-exists, command-passes, file-changed, and git-clean helpers cover
   the first credibility gap.
4. A small web/status UI for active goals and artifacts.
5. `agentic-harness run` that combines start, continue, review, and final status for simple workflows.

## 30-Day Plan

### Week 1

- Add CI and a release checklist.
- Add three working examples under `examples/`.
- Write a short "why this exists" blog post draft.
- Open issues for adapter requests.

### Week 2

- Share with 5-10 trusted builders privately.
- Fix README gaps based on first-user feedback.
- Add one local LLM end-to-end example.
- Use first-user feedback to decide which review helper, if any, deserves the
  next small addition.

### Week 3

- Post on Hacker News.
- Share a focused `r/LocalLLaMA` post.
- Publish the Dev.to or Medium article.
- Track recurring objections and convert them into docs or issues.

### Week 4

- Cut a tagged `v0.2.0` release.
- Record a two-minute terminal demo.
- Share the Twitter/X thread.
- Decide whether PyPI publishing is warranted based on stars, issues, and actual users.
