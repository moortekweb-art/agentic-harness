# Harness Deduplication Audit

Generated: 2026-07-04

## Scope

Audited files:

- `.hermes-control/profiles/controller/scripts/local-node1-goal-command.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-supervisor.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-worker.py`
- `.hermes-control/profiles/controller/scripts/local-node1-goal-current-truth.py`

Method: AST call/name/import/constant scan plus bounded duplicate-name similarity checks. No code changes were made for this task.

## Dead Code Candidates

| Function | File | Lines | Notes |
|---|---|---:|---|
| `_read_last_supervisor_event` | `local-node1-goal-supervisor.py:437` | 13 | No in-scope call reference found. `_read_supervisor_events()` appears to be the actively used timeline reader. Safe removal should be confirmed with external grep before deleting. |

Estimated line reduction from direct dead-code removal: 13 lines.

## Duplicate / Near-Duplicate Code

| Functions / Constants | Similarity | Recommendation |
|---|---:|---|
| `now()` in all four files | 100% | Move to a shared helper module, or use the Task 3 phase module if it becomes the common harness utility module. Estimated reduction: ~21 duplicated lines. |
| `MANAGER`, `DOC_ROOT`, `SUPERVISOR`, `STATE_PATH`, `REPORT_PATH`, `PROFILE` constants | exact-name duplicates | Extract common path constants to a small `local_node1_goal_paths.py`. Keep script-specific output paths in callers. Estimated reduction: ~15-25 lines and fewer drift points. |
| `run()` in supervisor and worker | low textual similarity, same responsibility | Consider a shared subprocess helper only if timeout/env behavior is normalized. Do not force this extraction until semantics are aligned. |
| `run_command()` in command shim and current-truth | low textual similarity, same responsibility | Candidate for shared helper after command-output redaction and timeout policies are centralized. |

Estimated line reduction if constants + timestamp helper are extracted cleanly: 35-50 lines.

## Misplaced / Overlapping Responsibilities

| Function / Area | Current Location | Recommended Location |
|---|---|---|
| Artifact path extraction and chat-safe artifact suppression | `local-node1-goal-command.py:4739` through chat-safe helpers | `local_node1_goal_output.py` or `local_node1_goal_artifacts.py` in Task 8. This is presentation/safety logic, not command routing. |
| Large human summary builders (`human_supervisor_summary`, `summarize_supervisor_payload`, doctor/brief/trust summaries) | `local-node1-goal-command.py` | `local_node1_goal_output.py`. This is the largest extraction target and should drive command-shim line reduction. |
| Natural-language intent parsing and routing | `local-node1-goal-command.py` | `local_node1_goal_parsing.py`. Keep the entry point responsible for argv/stdin, execution, and artifact writing only. |
| Command artifact writing | `local-node1-goal-command.py` | `local_node1_goal_artifacts.py`, alongside redaction/permission hardening from Task 5. |
| Notification formatting/sending | `local-node1-goal-supervisor.py` | `local_node1_goal_notifications.py` if Task 8 includes supervisor-adjacent extraction; otherwise leave until command shim split is stable. |

## Unused Imports

AST reported `from __future__ import annotations` as unused in each file. This is expected and should not be removed because Task 6 explicitly requires it and Python consumes it as a future feature, not a runtime name.

No other unused imports were detected by the bounded AST scan.

## Extraction Priority For Task 8

1. Extract command parsing/routing from `local-node1-goal-command.py` into `local_node1_goal_parsing.py`.
2. Extract chat-safe output, human summaries, and artifact suppression into `local_node1_goal_output.py`.
3. Extract command artifact writing and output path/redaction policy into `local_node1_goal_artifacts.py`.
4. Defer shared subprocess helpers until behavior differences between command/current-truth/supervisor/worker are intentionally reconciled.
5. Defer notification extraction unless command-shim extraction finishes with enough margin.

## Estimated Reduction

- Direct dead code: ~13 lines.
- Timestamp/path constants: ~35-50 lines.
- Command parsing extraction: likely 2,000-3,000 lines moved out of the shim.
- Output summary extraction: likely 2,000+ lines moved out of the shim.
- Artifact helper extraction: ~150-250 lines moved out of the shim.

If Task 8 moves rather than copies these responsibilities, `local-node1-goal-command.py` should be reducible below 3,000 lines without behavior changes.
