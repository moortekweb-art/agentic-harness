# Shell Worker Example

This example demonstrates a project-local shell worker. The harness reads `.agentic-harness/config.yml`, runs `worker.py`, and passes the active goal through environment variables.

## How to Run

From this directory:

```bash
agentic-harness start "write a short status note"
agentic-harness continue
agentic-harness review
agentic-harness status
```

For local development from the repository root, use:

```bash
python -m agentic_harness.cli --project-dir examples/shell-worker start "write a short status note"
python -m agentic_harness.cli --project-dir examples/shell-worker continue
python -m agentic_harness.cli --project-dir examples/shell-worker review
```

## Expected Output

`continue` prints JSON with `"status": "review"` and worker metadata. The worker writes `examples/shell-worker/output/<goal-id>.txt`.

## Safety and Assumptions

The worker only writes inside this example directory. It does not call the network, read credentials, or run external services.

