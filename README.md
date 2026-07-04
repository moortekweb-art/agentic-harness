# Agentic Harness

Multi-model goal execution pipeline running on local GPU infrastructure.

## Architecture

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Planner │───▶│ Executor │───▶│ Reviewer │───▶│  Done /  │
│ (design) │    │ (build)  │    │ (verify) │    │  Iterate │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

**Planner:** Analyzes goal, writes structured spec, decomposes into tasks
**Executor:** Implements the plan using code agents (Codex, GLM, Kimi, OpenCode, pi-zai)
**Reviewer:** Validates output against done criteria, checks for regressions

## Available Models

| Role | Models |
|------|--------|
| Planner | Codex/GPT-5.5, GLM-5.2, Kimi, DeepSeek-v4-Pro, thinkmax, none |
| Reviewer | Same set (cross-review capable) |
| Executor | OpenCode, Qwen, Aider, mini-swe |
| Worker | Codex, GLM-5.2-direct, Kimi, OpenCode-build, pi-zai variants |

## Hardware

- **node1:** 2× RTX 5090 (64GB VRAM)
- **node2:** 2× RTX 4090 (48GB VRAM)
- **Total pool:** 112GB across 2 nodes
- **Production model:** Ornith 1.0 35B FP8, 196K context, served via vLLM with tensor parallel=2

## Files

```
scripts/
  local-node1-goal-manager.py      — goal lifecycle manager (CLI)
  local-node1-goal-supervisor.py   — GLM-5.2 supervisor, orchestrates planner→executor→reviewer
  local-node1-goal-command.py      — thin command shim (entry point)
  local_node1_goal_command_impl.py — full command implementation
  local-node1-goal-worker.py       — worker execution wrapper
  local-node1-goal-current-truth.py — runtime state probe
  local_node1_goal_phases.py       — typed phase state machine + GoalState model

tests/
  8 test files, 219 tests total, all passing

docs/
  reports/   — coverage analysis, security audit, dedup audit
  agentic-harness-future-ideas.md — 10 use case ideas for later review
```

## Status

Working and tested. Used in production for AI infrastructure automation.

## Security

- Path traversal protection on all worker output paths
- Chat-safe output suppression (no artifact dumps in Telegram)
- Shell metacharacter sanitization
- Typed phase state machine prevents invalid transitions
