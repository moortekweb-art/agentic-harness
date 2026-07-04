# Harness Tech Debt Fixes

## Scope

Implemented the four Node1 local goal harness fixes from `/tmp/harness-tech-debt-spec.md` in required order: 4, 1, 2, 3.

## Fix 4: Shared Helpers and Constants

- Added shared path constants and `now_iso()` to `.hermes-control/profiles/controller/scripts/local_node1_goal_phases.py`.
- Updated command, supervisor, current-truth, and worker scripts to import shared paths and `now_iso`.
- Removed duplicated `now()` definitions from harness scripts.
- Removed dead `_read_last_supervisor_event` helper from the supervisor.
- Audit: duplicate path constant grep now reports only the shared helper definitions.

Validation after fix 4: `python3 -m pytest .hermes-control/profiles/controller/tests/ -q` -> `219 passed in 10.50s`.

## Fix 1: Secure Artifact Writes

- Added `write_secure_file(path, content, mode=0o600)` to the shared helper module.
- Replaced direct artifact writes in harness scripts with `write_secure_file`.
- Used `0o600` for JSON/state/prompt artifacts and `0o640` for markdown reports.
- Added permission coverage in `test_local_node1_goal_phases.py`.
- Updated the self-rescue test to mock the secure writer instead of `Path.write_text`.

Validation after fix 1: `python3 -m pytest .hermes-control/profiles/controller/tests/ -q` -> `220 passed in 10.35s`.

## Fix 2: Secret Redaction

- Added `redact_secrets(text)` to the shared helper module.
- `write_secure_file` redacts before writing to disk.
- Redaction covers OpenAI-style keys, Anthropic keys, GitHub tokens, bearer tokens, URL credentials, and long hex/base64-like secrets.
- Added tests for direct redaction coverage and on-disk redaction through `write_secure_file`.

Validation after fix 2: `python3 -m pytest .hermes-control/profiles/controller/tests/ -q` -> `222 passed in 10.47s`.

## Fix 3: Typed JSON Parse Errors

- Added `parse_error_record(error, source, text)` to the shared helper module.
- Updated supervisor `load_queue()`, current-truth `load_json_file()`, and worker `manager_status()` to catch `json.JSONDecodeError`, `KeyError`, and `TypeError` around JSON parsing.
- Fallback payloads now include `_parse_errors` with source, error type, and first 100 characters of the input.
- Added negative canary tests for all three parse fallback paths.

Validation after fix 3: `/mnt/raid0/home-ai-inference/clawd/.venv/bin/python -m pytest --cov=.hermes-control/profiles/controller/scripts .hermes-control/profiles/controller/tests/ -q` -> `224 passed in 16.39s` after rerunning outside the sandbox because the sandbox blocked local 127.0.0.1 socket creation in command-center tests.

## Completion Audit

- Syntax check passed for modified harness scripts and tests via `py_compile`.
- Duplicate helper/constant grep: only shared helper definitions remain.
- Direct artifact write grep: only `write_secure_file`, stdout/stderr writes, and the existing lock file descriptor write remain.
- Target JSON parse sites no longer use broad `except Exception` catches.

## Final Validation

Final full-suite validation: `/mnt/raid0/home-ai-inference/clawd/.venv/bin/python -m pytest --cov=.hermes-control/profiles/controller/scripts .hermes-control/profiles/controller/tests/ -q` -> `224 passed in 17.18s`.
