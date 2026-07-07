# Coding Agent Worker

This example shows how to wrap a Codex, Aider, OpenCode, or similar CLI with
Agentic Harness. The harness does not become the coding agent; it starts the
agent, captures a transcript, and runs deterministic review gates afterwards.

## Config

`.agentic-harness/config.yml`

```yaml
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codex
    - exec
    - --skip-git-repo-check
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

For Aider or OpenCode, replace `coding_agent_command` with that tool's
non-interactive command.

```yaml
coding_agent_command:
  - aider
  - --message
  - "{objective}"
```

```yaml
coding_agent_command:
  - opencode
  - run
  - "{objective}"
```

## Run

```bash
agentic-harness run "fix failing tests"
```

Expected shape:

- The coding-agent command runs in the project directory.
- `.agentic-harness/runs/<goal-id>/coding-agent.log` records the command,
  stdout, and stderr.
- The goal only reaches `done` if the configured review command passes.
