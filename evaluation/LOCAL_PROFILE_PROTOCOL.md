# Local Qwen and Ornith profile protocol

This protocol evaluates real harness runs, not isolated chat responses. Use the
task set in `local_profile_matrix.json`, prepare a fresh fixture for each task,
start it through the managed Mode 1 route with the named execution profile, and
run its deterministic verifier after the harness reaches a terminal state.

Record one JSON object per supported profile/task pair with:

- `profile`, `task_id`, and `run_id`;
- `deterministic_pass`, `false_verified`, `route_profile_correct`,
  `guardrail_violation`, and `tool_calls_valid`;
- `retries`, `elapsed_seconds`, `input_tokens`, `output_tokens`, and
  `context_tokens` when the backend reports them;
- `terminal_status`, `failure_reason`, and hashes of the fixture and verifier;
- vision evidence for `vision-layout-review`.

Ornith is text/tool-only and is not assigned the vision case. Unsupported
capability is not converted into an ordinary quality failure. Do not recommend
a profile unless it passes at least 90% of its supported deterministic cases,
has zero false verified results, zero route/profile errors, and zero guardrail
violations. Keep raw JSONL, sanitized transcripts, fixture hashes, and the
generated summary together as one dated result artifact.

Score a completed run with:

```bash
python evaluation/score_local_profile_results.py \
  --results /path/to/raw.jsonl \
  --output-dir /path/to/scored-result
```

The scorer fails closed on missing or duplicate profile/task rows.
