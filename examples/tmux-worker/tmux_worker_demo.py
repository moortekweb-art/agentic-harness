"""Safe-by-default TmuxWorker example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_harness import Supervisor
from agentic_harness.adapters import TmuxWorker
from agentic_harness.core.state import Goal


COMMAND_TEMPLATE = "printf 'goal_id: {goal_id}\\n' > tmux-worker-output-{goal_id}.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Start a detached tmux session.")
    parser.add_argument("--objective", default="inspect failing tests")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    worker = TmuxWorker(COMMAND_TEMPLATE, cwd=Path(__file__).resolve().parent)
    sample_goal = Goal(objective=args.objective)
    if not args.run:
        payload = {
            "session": worker.session_name_for(sample_goal),
            "command": worker.command_for(sample_goal),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    supervisor = Supervisor(project_dir=Path(__file__).resolve().parent, worker=worker)
    goal = supervisor.start(args.objective)
    goal = supervisor.continue_goal()
    print(json.dumps(goal.to_dict(), indent=2, sort_keys=True))
    return 0 if goal.metadata.get("worker_success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
