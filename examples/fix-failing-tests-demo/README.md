# Fix Failing Tests Demo

This is the small demo for the core Agentic Harness pitch:

```bash
agentic-harness run "fix failing tests"
```

The project starts with a deliberately broken calculator function. The
configured `coding_agent` worker runs `mock_coding_agent.py`, which stands in
for a non-interactive coding agent CLI during local demos. The review gate runs
pytest and only marks the goal `done` after the tests pass.

## Run

From this directory:

```bash
python -m pytest tests/ -q
agentic-harness run "fix failing tests"
agentic-harness status --format text
```

The first `pytest` command is expected to fail. It proves the starting project
is broken before the harness runs the coding-agent worker and review gate.

To reset the demo:

```bash
python reset_demo.py
rm -rf .agentic-harness/runs .agentic-harness/current.json .agentic-harness/guard.json .agentic-harness/state.lock
```
