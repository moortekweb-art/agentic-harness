# Local LLM Adapter Example

This example demonstrates `LocalLLMAdapter`, which calls an OpenAI-compatible local chat completions endpoint.

## How to Run

Dry-run mode is the default and does not call any endpoint:

```bash
python run_local_llm.py
```

To call a local endpoint explicitly:

```bash
python run_local_llm.py --run --endpoint http://127.0.0.1:4000/v1/chat/completions --model local-model
```

## Expected Output

Dry-run mode prints the request payload that would be sent. With `--run`, the harness creates `.agentic-harness/` state in this example directory and prints the resulting goal JSON.

## Safety and Assumptions

The script does not call a live endpoint unless `--run` is provided. It assumes the endpoint implements the OpenAI chat completions shape and that any required API key is supplied with `--api-key`.

