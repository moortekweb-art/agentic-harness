# Real-World Recipes

These recipes are starting points for project-local use. Keep credentials in
your secret store or CI environment, not in committed config files.

## One-Shot Shell Goal With Review

Use this when a project already has a script that can perform bounded work and
exit with a meaningful status.

`.agentic-harness/config.yml`

```yaml
version: 1
worker: shell
shell_command:
  - python
  - scripts/agent_task.py
review:
  command:
    - python
    - -m
    - pytest
    - tests/
    - -q
```

```bash
agentic-harness run "update docs for the latest CLI behavior"
```

## Detached Tmux Worker

Use tmux when the worker is interactive or long-running and you want a named
session to inspect manually.

```yaml
version: 1
worker: tmux
tmux_command: "python scripts/agent_task.py --goal {goal_id} --objective '{objective}'"
tmux_session_prefix: agentic-harness
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
```

```bash
agentic-harness start "run the migration checklist"
agentic-harness continue
tmux attach -t agentic-harness-<goal-prefix>
agentic-harness review
```

## Coding Agent Worker

Use this when Codex, Aider, OpenCode, or another coding-agent CLI should make
the code changes while Agentic Harness owns state, transcript capture, loop
limits, and deterministic review.

```yaml
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codex
    - exec
    - --full-auto
    - "{objective}"
  coding_agent_timeout: 1800
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review:
  command:
    - python
    - -m
    - pytest
    - tests/
    - -q
```

```bash
agentic-harness run "fix failing tests"
```

Swap the command list for another tool when needed:

```yaml
coding_agent_command: [aider, --message, "{objective}"]
```

```yaml
coding_agent_command: [opencode, run, "{objective}"]
```

## Local LLM Endpoint

Use this when an OpenAI-compatible local endpoint can produce a bounded result.

```yaml
version: 1
worker: local_llm
llm_endpoint: http://127.0.0.1:4000/v1/chat/completions
llm_model: local-model
review_command:
  - python
  - -m
  - compileall
  - agentic_harness
```

## GitHub Actions Worker

Use GitHub Actions when execution should happen in CI instead of on the local
machine. `github_wait: true` waits on the exact workflow run URL returned by
GitHub's modern workflow dispatch API. Older API responses without a run URL
fall back to polling workflow_dispatch runs created after dispatch.

```yaml
version: 1
worker: github_actions
github_owner: moortekweb-art
github_repo: agentic-harness
github_workflow_id: ci.yml
github_token: token-from-your-secret-store
github_wait: true
github_api_version: 2026-03-10
github_timeout: 600
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
```

The target workflow should accept `goal_id` and `objective` inputs if it needs
to inspect the dispatched goal.

```yaml
on:
  workflow_dispatch:
    inputs:
      goal_id:
        required: true
        type: string
      objective:
        required: true
        type: string
```
