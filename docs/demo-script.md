# Agentic Harness Terminal Demo Script

Target recording length: 90-120 seconds.

This script uses real commands and summarizes the shape of expected output
instead of scripted fake output. Run it from a clean terminal with the
repository available locally.

## Setup

Time: 10-15 seconds

Command:

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
```

Expected output summary:

- `pipx` creates an isolated environment.
- The installed app is named `agentic-harness`.
- If recording from this checkout before installing from GitHub, use the
  development fallback:

```bash
python -m pip install -e .
```

## 1. Run The Complete Demo

Time: 20-30 seconds

Command:

```bash
agentic-harness run-demo fix-tests /tmp/agentic-harness-demo --force
```

Expected output summary:

- Creates the packaged fix-tests demo under `/tmp/agentic-harness-demo`.
- Installs the demo test dependency.
- Shows the initial pytest failure.
- Runs `agentic-harness fix-tests`.
- Prints `Status: done` and `Review: passed`.
- Verifies the final pytest run passes.

## 2. Inspect The No-Hidden-YAML Path

Time: 30-40 seconds

Command:

```bash
rm -rf /tmp/agentic-harness-demo
agentic-harness create-demo fix-tests /tmp/agentic-harness-demo
cd /tmp/agentic-harness-demo
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q   # expected to fail
agentic-harness fix-tests     # auto-creates demo config
agentic-harness status
agentic-harness report
python -m pytest tests/ -q   # should pass
```

Expected output summary:

- The first pytest run fails because `calculator.py` has a deliberate bug.
- `fix-tests` creates `.agentic-harness/config.yml` for the generated demo.
- The mock coding-agent worker edits `calculator.py`.
- `status` prints `Status: done`.
- `report` prints the run summary, review result, changed file, and report path.
- The final pytest run passes.

## 3. Show The Artifact Trail

Time: 10-15 seconds

Command:

```bash
find .agentic-harness -maxdepth 4 -type f | sort
```

Expected output summary:

- Shows `.agentic-harness/config.yml`.
- Shows `.agentic-harness/runs/<goal-id>/shell-worker.log`.
- Shows `.agentic-harness/runs/<goal-id>/report.md`.
- Shows saved goal state.

## 4. Show The Shortest Path For This Machine

Time: 10 seconds

Command:

```bash
agentic-harness quickstart
```

Expected output summary:

- If Codex, CodeWhale, OpenCode, or Aider is installed, prints the direct
  `fix-tests` -> `status` -> `report` path for that backend.
- If no coding-agent backend is installed, points back to the packaged shell
  demo path.

## Coding Agent Demo Variant

Use this variant when recording with a real coding-agent backend instead of the
packaged shell demo.

Command:

```bash
agentic-harness init-agent codex --force
agentic-harness run-recipe fix-tests --explain
agentic-harness fix-tests
agentic-harness status
agentic-harness report
```

Expected output summary:

- `init-agent codex --force` writes a Codex-backed config for the current
  project.
- `run-recipe fix-tests --explain` previews the objective and pytest review
  gate without running the worker.
- `fix-tests` reaches `done` only if the coding agent exits successfully and
  the pytest review command passes.
- The transcript is written under `.agentic-harness/runs/<goal-id>/`.
- If tests fail, the goal status is `failed` with the review failure recorded
  in state.

## Recording Tips

- Use `asciinema rec agentic-harness-demo.cast` for a clean terminal recording.
- Set terminal font size to 16-18 px before recording.
- Use a dark, high-contrast theme so output remains readable.
- Keep the terminal width around 100-120 columns.
- Pause briefly after `run-demo`, `status`, and `report` so viewers can see the
  completion gate and artifact path.
- Do not paste sample output. Let the real command output appear on screen.
