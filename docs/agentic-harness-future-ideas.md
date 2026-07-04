# Agentic Harness — Future Use Cases & Ideas

**Written:** 2026-07-04
**Status:** Ideas for later review — not being acted on yet
**Context:** The harness is a multi-model goal execution pipeline (6 planners, 6 reviewers, 4 executors, 9 executor workers) running on local GPU infrastructure (2 nodes, 4 GPUs, 112GB VRAM).

---

## Income-Generating

### 1. Automated Code Review Service
Feed it PRs from GitHub repos via webhooks. Planner → reviewer → feedback automatically. Multi-reviewer cross-checking (Codex + GLM + Kimi) is better than single-model review. Offer as a service: "every PR reviewed within 5 minutes."

### 2. Client Project Automation
Create a `/goal` with a spec, let the harness plan → execute → review. Works for Earl's portfolio, future client work, internal tools. Start simple: "add responsive breakpoints to Earl's portfolio."

### 3. Bug Bounty / Security Scanning Pipeline
Script the harness to scan repos on schedule. Already proven it can find vulns (the path traversal it caught today). Cron-schedule → write findings → alert on HIGH/CRITICAL.

## Infrastructure

### 4. Nightly Self-Healing
Cron `/goal` that runs current-truth probe, checks worker lanes, tests LiteLLM aliases, auto-fixes common issues (dead lanes, stale configs, broken aliases). Wake up to clean cluster or a report of what's broken.

### 5. Test Suite Generation
Point harness at any codebase, say "write comprehensive tests for this module." Planner analyzes code, executor writes tests, reviewer checks coverage/edge cases. Repeatable pipeline for any repo.

### 6. Documentation Generation
Feed source files → generates API docs, README sections, architecture diagrams. Auto-update docs on every commit.

### 7. Model Evaluation Harness
Run structured evals: same prompt to GLM-5.2, Kimi, MiniMax, Codex → reviewer scores on correctness, speed, cost. Build a leaderboard using local GPU pool.

## Experimental — Expanding Capabilities

### 8. Multi-Repo Refactoring at Scale
"migrate all Python scripts from requests to httpx" across repos. Plans changes, executes per-repo, reviews each. More reliable than sed/awk.

### 9. Autonomous Feature Development
Feature spec + repo → full loop: plan architecture, write code, write tests, review, iterate until green. The "AI developer" pitch on local hardware.

### 10. Competitive Intelligence
Point at competitor repos/docs/websites → planner extracts what changed, executor summarizes, reviewer highlights what matters. Weekly cron for competitive analysis.

---

## Recommended Starting Points

- **#1 (Code Review Service)** — fastest path to income, reviewer infra already built, just needs GitHub webhook trigger
- **#4 (Nightly Self-Healing)** — fastest path to less toil, wake up to clean cluster, pieces already exist

Both are incremental — the harness has the pieces, they just need a trigger (webhook or cron) and an output format.
