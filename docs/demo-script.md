# Agentic Harness Terminal Demo Script

Target recording length: 90-120 seconds.

This script uses real commands and summarizes the shape of expected output instead of scripted fake output. Run it from a clean terminal with the repository available locally.

## Setup

Time: 10-15 seconds

Command:

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
```

Expected output summary:

- `pipx` creates an isolated environment.
- The installed app is named `agentic-harness`.
- If recording from this checkout before installing from GitHub, use the development fallback:

```bash
python -m pip install -e .
```

## 1. Create A Fresh Project

Time: 10 seconds

Command:

```bash
mkdir -p /tmp/agentic-harness-demo
cd /tmp/agentic-harness-demo
agentic-harness init
cat > .agentic-harness/config.yml <<'YAML'
version: 1
worker: shell
shell_command:
  - python
  - -c
  - "import os; print('demo goal:', os.environ['AGENTIC_HARNESS_OBJECTIVE'])"
YAML
```

Expected output summary:

- Prints the path to the created config file.
- Creates `.agentic-harness/config.yml`.
- Replaces the safe default `noop` placeholder with a tiny shell worker so the
  demo runs real work.

## 2. Run Doctor

Time: 10-15 seconds

Command:

```bash
agentic-harness doctor
```

Expected output summary:

- Prints JSON with `"ok": true`.
- Includes checks for `project_dir`, `config`, and `state_dir`.
- Each check should have `"ok": true`.

## 3. Start A Goal

Time: 15 seconds

Command:

```bash
agentic-harness start "write a status note"
```

Expected output summary:

- Prints a JSON goal object.
- Status is `"planning"`.
- The objective is `"write a status note"`.
- A goal id is generated for the run.

## 4. Continue The Goal

Time: 15 seconds

Command:

```bash
agentic-harness continue
```

Expected output summary:

- Prints the same goal as JSON.
- Status moves to `"review"`.
- Metadata includes a worker success marker because the shell worker completed.

## 5. Run Deterministic Review

Time: 20 seconds

Command:

```bash
agentic-harness review
```

Expected output summary:

- Prints JSON with status `"done"`.
- The `review` object includes `"passed": true`.
- The default deterministic criterion is `worker_success`.
- The criterion message should say the worker reported success.

## 6. Point At The Artifact Directory

Time: 10-15 seconds

Command:

```bash
find .agentic-harness -maxdepth 4 -type f | sort
```

Expected output summary:

- Shows `.agentic-harness/config.yml`.
- Shows a run directory under `.agentic-harness/runs/<goal-id>/`.
- Shows saved state for the goal.

## Recording Tips

- Use `asciinema rec agentic-harness-demo.cast` for a clean terminal recording.
- Set terminal font size to 16-18 px before recording.
- Use a dark, high-contrast theme so JSON output remains readable.
- Keep the terminal width around 100-120 columns.
- Pause briefly after `doctor`, `continue`, and `review` so viewers can see the state transitions.
- Do not paste sample output. Let the real command output appear on screen.

## Coding Agent Demo Variant

Use this variant when recording the critique-driven demo: a real coding agent
does the work, and Agentic Harness supplies the audit trail and deterministic
completion gate.

Command:

```bash
cat > .agentic-harness/config.yml <<'YAML'
version: 1
worker:
  type: coding_agent
  coding_agent_command:
    - codex
    - exec
    - --full-auto
    - "{objective}"
  coding_agent_transcript: .agentic-harness/runs/{goal_id}/coding-agent.log
review:
  command:
    - python
    - -m
    - pytest
    - tests/
    - -q
YAML
agentic-harness run "fix failing tests"
```

Expected output summary:

- The goal reaches `done` only if the coding agent exits successfully and the
  pytest review command passes.
- The transcript is written under `.agentic-harness/runs/<goal-id>/`.
- If tests fail, the goal status is `failed` with the review failure recorded
  in state.
