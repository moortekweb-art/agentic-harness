# Fix Failing Tests Demo

This is the small demo for the core Agentic Harness pitch:

```bash
agentic-harness fix-tests
```

The project starts with a deliberately broken calculator function. The
`fix-tests` command detects this demo and creates a shell worker config that
runs `mock_coding_agent.py`, which stands in for a non-interactive coding agent
CLI during local demos. The review gate runs pytest and only marks the goal
`done` after the tests pass.

## Run

From this directory:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q   # expected to fail
agentic-harness fix-tests     # auto-creates demo config
agentic-harness status
agentic-harness report
python -m pytest tests/ -q   # should pass
```

The first `pytest` command proves the starting project is broken before the
harness runs the shell worker and review gate.

To reset the demo:

```bash
python reset_demo.py
rm -rf .agentic-harness
```
