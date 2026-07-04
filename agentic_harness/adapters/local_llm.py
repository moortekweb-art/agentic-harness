"""OpenAI-compatible local LLM adapter."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from agentic_harness.core.state import Goal
from agentic_harness.core.worker import WorkerResult


@dataclass
class LocalLLMAdapter:
    endpoint: str
    model: str
    api_key: str = "local"
    timeout: int = 120

    def request_payload(self, goal: Goal) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an execution worker. Return concise progress.",
                },
                {"role": "user", "content": goal.objective},
            ],
            "stream": False,
        }

    def run(self, goal: Goal) -> WorkerResult:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(self.request_payload(goal)).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network adapter boundary: keep failure structured
            return WorkerResult(success=False, summary=str(exc), returncode=1)

        content = (
            ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
            if isinstance(payload, dict)
            else None
        )
        return WorkerResult(
            success=bool(content),
            summary=str(content or "local LLM returned no content"),
            stdout=json.dumps(payload, sort_keys=True),
        )

