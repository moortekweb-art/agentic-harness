"""OpenAI-compatible local LLM adapter."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import warnings
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
    retries: int = 2
    retry_delay: float = 1.0

    def __post_init__(self) -> None:
        warnings.warn(
            "LocalLLMAdapter is deprecated; use the model_agent worker for "
            "structured, tool-capable OpenAI-compatible models.",
            DeprecationWarning,
            stacklevel=2,
        )
        if self.timeout < 1:
            raise ValueError(f"timeout must be >= 1, got {self.timeout}")
        if self.retries < 0:
            raise ValueError(f"retries must be >= 0, got {self.retries}")
        if self.retry_delay < 0:
            raise ValueError(f"retry_delay must be >= 0, got {self.retry_delay}")

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
        last_exc: Exception | None = None
        for attempt in range(1 + self.retries):
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
                content = self._extract_message_content(payload)
                return WorkerResult(
                    success=bool(content),
                    summary=str(content or "local LLM returned no content"),
                    stdout=json.dumps(payload, sort_keys=True),
                    returncode=0 if content else 1,
                )
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                OSError,
                json.JSONDecodeError,
            ) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay)
        return WorkerResult(
            success=False,
            summary=f"local LLM request failed after {1 + self.retries} attempt(s): {last_exc}",
            stderr=str(last_exc),
            returncode=1,
        )
