"""OpenAI-compatible local LLM adapter."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

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

    def _extract_message_content(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0]
        if not isinstance(first, dict):
            return None
        message = first.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        return content if isinstance(content, str) and content else None

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

        content = self._extract_message_content(payload)
        return WorkerResult(
            success=bool(content),
            summary=str(content or "local LLM returned no content"),
            stdout=json.dumps(payload, sort_keys=True),
            returncode=0 if content else 1,
        )
