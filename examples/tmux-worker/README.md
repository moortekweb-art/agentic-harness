# Tmux Worker Example

This example demonstrates `TmuxWorker`, which starts a detached tmux session for a goal.

## How to Run

Dry-run mode is the default and only prints the session name and command:

```bash
python tmux_worker_demo.py
```

To start a real tmux session explicitly:

```bash
python tmux_worker_demo.py --run --objective "inspect failing tests"
```

## Expected Output

Dry-run mode prints JSON containing the tmux session name and command. With `--run`, the harness writes `.agentic-harness/` state in this example directory and returns worker metadata for the tmux command.

## Safety and Assumptions

The default mode does not start tmux. The `--run` mode requires `tmux` to be installed and starts a detached session that runs a small shell command. Prefer tmux commands that pass `{goal_id}` only; workers can read the objective from `.agentic-harness/runs/<goal-id>/state.json` instead of embedding user goal text in shell commands.
