"""Safe-by-default LocalLLMAdapter example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_harness import Supervisor
from agentic_harness.adapters import LocalLLMAdapter
from agentic_harness.core.state import Goal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Call the configured endpoint.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4000/v1/chat/completions")
    parser.add_argument("--model", default="local-model")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--objective", default="Draft a concise progress update.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    worker = LocalLLMAdapter(endpoint=args.endpoint, model=args.model, api_key=args.api_key)
    if not args.run:
        sample_goal = Goal(objective=args.objective)
        print(json.dumps(worker.request_payload(sample_goal), indent=2, sort_keys=True))
        return 0

    supervisor = Supervisor(project_dir=Path(__file__).resolve().parent, worker=worker)
    goal = supervisor.start(args.objective)
    goal = supervisor.continue_goal()
    print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
    return 0 if goal.metadata.get("worker_success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

