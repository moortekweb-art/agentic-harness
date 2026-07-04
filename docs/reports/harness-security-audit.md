# Harness Security Audit

Generated: 2026-07-04

## Scope And Method

Scoped files:

- `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-current-truth.py`

Codex Security preflight was run for `security_scan`; exhaustive-scan capability was incomplete because multi-agent mode/capacity is unknown and this Task 5 audit did not use subagents. This report is therefore a parent-agent scoped audit against the requested classes: path traversal, command injection, unsafe JSON handling, file permissions, and secret leakage.

## Findings

### HIGH: Worker report/status paths could write outside controller artifact roots

- Status: fixed in this task
- File: `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py:68`
- Affected previous sink: `write_artifacts()` used `Path(args.report)` and `Path(args.status)` directly before writing report and status artifacts.
- Risk: a malformed or compromised terminal-worker invocation could point `--report` or `--status` at an arbitrary writable path such as `/tmp/escape.md` or another workspace file.
- Fix: added `resolve_worker_output_path()` and constrained worker output paths to controller-managed artifact roots: controller `reports/` and `worker-runs/`.
- Regression proof: `.hermes-control/profiles/controller/tests/test_negative_canaries.py` now covers rejection of `/tmp/escape.md` and acceptance of expected controller artifact paths.

### MEDIUM: State/report files use default process umask permissions

- Status: documented; not fixed in this task
- Files:
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py:7862`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py:9165`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-current-truth.py:655`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py:132`
- Risk: state files include command lines, goal text, stdout/stderr tails, run directories, and status payloads. They are local artifacts, but permissions are inherited from the runtime umask instead of explicitly set.
- Recommended fix: introduce a shared atomic write helper that opens files with `0o600` for JSON/state artifacts and `0o640` or `0o600` for reports, then replace the direct `write_text()` calls.

### MEDIUM: Raw subprocess stdout/stderr tails can persist sensitive text

- Status: documented; not fixed in this task
- Files:
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py:7852`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py:63`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py:9142`
- Risk: if an invoked manager/worker emits API keys, tokens, signed URLs, or credentials, the harness preserves raw tails in local JSON/report artifacts. Current chat output suppresses known artifact paths, but it does not redact generic secrets before writing disk artifacts.
- Recommended fix: add a small redaction helper for common token patterns before writing stdout/stderr tails and report fenced output.

### LOW: JSON parsing mostly fails closed, but broad `except Exception` hides corruption causes

- Status: documented; partially covered by Task 4 canaries
- Files:
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py:633`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-current-truth.py:120`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py:55`
- Risk: invalid JSON generally returns safe fallback structures, but broad exception handling can hide permission failures, partial writes, or schema drift.
- Recommended fix: keep graceful fallbacks but log structured parse errors into diagnostics fields, and distinguish `JSONDecodeError` from filesystem errors where recovery differs.

### INFO: Command injection review found list-based subprocess execution

- Status: no finding
- Files:
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py:4704`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py:161`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py:36`
  - `.hermes-control/profiles/controller/scripts/local-node1-goal-current-truth.py:95`
- Evidence: subprocess calls use argument lists with `shell=False` default. Task 4 adds a canary proving shell metacharacters in goal text remain one `--goal` argument.
- Residual risk: commands still pass user-provided goal text to the manager as data, so downstream consumers must continue treating goal text as untrusted text.

### INFO: Artifact traversal-looking paths are now suppressed from chat summaries

- Status: fixed in this task
- File: `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py:4739`
- Change: `_extract_artifact_paths()` now skips matched artifact paths containing `..` path components.
- Regression proof: `.hermes-control/profiles/controller/tests/test_negative_canaries.py` covers traversal-looking artifact paths and verifies they are not shown or counted in chat output.

## Verification

- `python3 -m pytest .hermes-control/profiles/controller/tests/test_negative_canaries.py -q` -> `13 passed`
- `python3 -m py_compile .hermes-control/profiles/controller/scripts/local-node1-goal-worker.py .hermes-control/profiles/controller/scripts/local-node1-goal-command.py` -> passed
