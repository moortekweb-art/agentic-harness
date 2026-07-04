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
machine. `github_wait: true` polls workflow runs created after dispatch and
reports the final conclusion for the newest matching workflow_dispatch run.

```yaml
version: 1
worker: github_actions
github_owner: moortekweb-art
github_repo: agentic-harness
github_workflow_id: ci.yml
github_token: token-from-your-secret-store
github_wait: true
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
