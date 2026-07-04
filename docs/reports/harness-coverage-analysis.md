# Harness Coverage Analysis

Generated: 2026-07-04

## Scope

Task 2 requested coverage for `test_local_node1_goal_command.py` and `test_local_node1_goal_command_doc_spam.py`. In this working tree, `test_local_node1_goal_command.py` is absent; the available command-shim tests are:

- `.hermes-control/profiles/controller/tests/test_local_node1_goal_command_doc_spam.py`
- `.hermes-control/profiles/controller/tests/test_doc_spam_integration.py`

Coverage was run with the user-provided Python environment:

```bash
/mnt/raid0/home-ai-inference/clawd/.venv/bin/python -m coverage run --source=.hermes-control/profiles/controller/scripts -m pytest .hermes-control/profiles/controller/tests/test_local_node1_goal_command_doc_spam.py .hermes-control/profiles/controller/tests/test_doc_spam_integration.py
/mnt/raid0/home-ai-inference/clawd/.venv/bin/python -m coverage report --include=.hermes-control/profiles/controller/scripts/local-node1-goal-command.py --show-missing
```

## Overall Coverage

`local-node1-goal-command.py`: 20% line coverage

- Statements: 2,207
- Missed: 1,774
- Covered: 433

The broad `--cov=.hermes-control/profiles/controller/scripts` invocation also works, but it includes every controller script and reports a misleading 1% total for the whole scripts directory. The command shim itself is the meaningful Task 2 target.

## Top 10 Uncovered / Low-Coverage Functions

| Function | Lines | Statement count | Missed | Coverage |
|---|---:|---:|---:|---:|
| `human_supervisor_summary` | 5943-7016 | 544 | 543 | 0% |
| `summarize_supervisor_payload` | 7019-7776 | 434 | 431 | 1% |
| `brief_summary` | 5337-5543 | 102 | 101 | 1% |
| `doctor_decision_state` | 5095-5180 | 46 | 45 | 2% |
| `trust_boundary_summary` | 5546-5663 | 44 | 43 | 2% |
| `model_durability_summary` | 5183-5235 | 42 | 41 | 2% |
| `doctor_structured_state` | 5666-5807 | 38 | 37 | 3% |
| `doctor_envelope_status` | 5810-5852 | 25 | 24 | 4% |
| `run_doctor_command` | 5855-5934 | 20 | 19 | 5% |
| `_node1_has_other_vllm_activity` | 5049-5072 | 19 | 18 | 5% |

## Critical Paths Lacking Tests

- Supervisor payload summarization is effectively untested despite being the largest human-facing formatter.
- Doctor / trust-boundary / brief modes have very low coverage, including structured status envelopes and remediation text.
- Current-truth enrichment via `attach_current_truth_model_decision` has only incidental coverage and lacks failure-mode tests for malformed supervisor output.
- Dry-run summaries and parse/routing branches for many operator phrases are not covered by the remaining tests in this tree.
- Error handling around non-zero supervisor exits, empty output, malformed JSON, and stderr suppression needs negative canaries.

## Recommendations

1. Restore or recreate the missing `test_local_node1_goal_command.py` regression suite if it was accidentally removed from the tree.
2. Add table-driven tests for `summarize_supervisor_payload` covering planning, executing, reviewing, blocked, failed, done, accepted, and malformed payloads.
3. Add focused doctor-mode tests for `doctor_structured_state`, `doctor_envelope_status`, `brief_summary`, and `trust_boundary_summary`.
4. Add failure-path tests for supervisor non-zero exits, empty stdout, invalid JSON, and artifact-heavy stderr.
5. Keep doc-spam tests as a separate canary suite because they protect the chat boundary against accidental document dumps.
