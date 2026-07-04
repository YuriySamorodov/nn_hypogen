from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    provider: str
    raw: dict | None = None


class LLMClient:
    def complete_json(self, system: str, user: str) -> LLMResponse:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    def complete_json(self, system: str, user: str) -> LLMResponse:
        payload = {
            "summary": "mock response",
            "recommendation": "Use deterministic local generators unless OPENAI_API_KEY is configured.",
        }
        return LLMResponse(text=json.dumps(payload, ensure_ascii=False), provider="mock")


class OpenAIChatClient(LLMClient):
    def __init__(self, api_key: str | None = None, model: str = "gpt-4.1-mini") -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAIChatClient")

    def complete_json(self, system: str, user: str) -> LLMResponse:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        text = raw["choices"][0]["message"]["content"]
        return LLMResponse(text=text, provider="openai", raw=raw)


def default_llm_client(mock: bool = True) -> LLMClient:
    if mock or not os.getenv("OPENAI_API_KEY"):
        return MockLLMClient()
    return OpenAIChatClient()

