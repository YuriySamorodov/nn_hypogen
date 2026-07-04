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
    reasoning: str | None = None


class LLMClient:
    def complete_json(self, system: str, user: str) -> LLMResponse:
        raise NotImplementedError

    def chat_text(self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 1600) -> LLMResponse:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    def complete_json(self, system: str, user: str) -> LLMResponse:
        payload = {
            "summary": "mock response",
            "recommendation": "Use deterministic local generators unless OPENAI_API_KEY is configured.",
        }
        return LLMResponse(text=json.dumps(payload, ensure_ascii=False), provider="mock")

    def chat_text(self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 1600) -> LLMResponse:
        return LLMResponse(text="[mock-llm] LLM отключён; используется экстрактивный ответ.", provider="mock")


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


class OpenAICompatibleClient(LLMClient):
    """Generic OpenAI-compatible chat client (DeepSeek, Gonka, vLLM, etc.)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai-compatible",
        extra_body: dict | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for OpenAICompatibleClient")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider = provider
        self.extra_body = extra_body or {}

    def _post(self, payload: dict, timeout: int) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _extract(raw: dict) -> tuple[str, str | None]:
        message = raw["choices"][0]["message"]
        return message.get("content") or "", message.get("reasoning_content")

    def chat_text(self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 1600) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **self.extra_body,
        }
        raw = self._post(payload, timeout=180)
        text, reasoning = self._extract(raw)
        return LLMResponse(text=text, provider=self.provider, raw=raw, reasoning=reasoning)

    def complete_json(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
            **self.extra_body,
        }
        try:
            raw = self._post(payload, timeout=90)
        except Exception:
            payload.pop("response_format", None)
            raw = self._post(payload, timeout=90)
        text, reasoning = self._extract(raw)
        return LLMResponse(text=text, provider=self.provider, raw=raw, reasoning=reasoning)


class ZhipuGLMClient(OpenAICompatibleClient):
    """Zhipu AI / z.ai GLM client with native long-thinking (thinking + reasoning_effort)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        thinking: bool = True,
        reasoning_effort: str | None = "high",
        provider: str = "glm",
    ) -> None:
        extra: dict = {}
        if thinking:
            extra["thinking"] = {"type": "enabled"}
            if reasoning_effort:
                extra["reasoning_effort"] = reasoning_effort
        super().__init__(api_key, base_url, model, provider=provider, extra_body=extra)

    def web_search(self, query: str, *, count: int = 6, engine: str | None = None, timeout: int = 40) -> list[dict]:
        """Zhipu standalone Web Search API. Returns [{title, content, link, publish_date, media}].

        Uses the dedicated /web_search endpoint (not chat tools) which reliably
        returns structured results; engine `search_prime` gives the best quality.
        """
        engine = engine or os.getenv("GLM_WEB_SEARCH_ENGINE", "search_prime")
        payload = {"search_engine": engine, "search_query": query, "count": count}
        request = urllib.request.Request(
            f"{self.base_url}/web_search",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        results = raw.get("search_result") or []
        return [r for r in results if isinstance(r, dict) and (r.get("link") or r.get("title"))]


def default_llm_client(mock: bool = True) -> LLMClient:
    if mock or not os.getenv("OPENAI_API_KEY"):
        return MockLLMClient()
    return OpenAIChatClient()


def research_llm_client() -> LLMClient:
    """LLM used by Deep Research. Independent of HF_MOCK_LLM (that flag governs the
    hypothesis pipeline). Prefers DeepSeek, then a Gonka OpenAI-compatible proxy."""
    if (os.getenv("HF_RESEARCH_LLM", "") or "").strip().lower() == "mock":
        return MockLLMClient()

    forced_model = os.getenv("HF_RESEARCH_MODEL") or None

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        base = (os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").split()[0].rstrip("/")
        model = forced_model or os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")
        return OpenAICompatibleClient(deepseek_key, base, model, provider="deepseek")

    gonka_key = os.getenv("GONKA_API_KEY")
    gonka_base = (os.getenv("GONKA_API_BASE") or "").split()[0].rstrip("/") if os.getenv("GONKA_API_BASE") else ""
    if gonka_key and gonka_base:
        model = forced_model or os.getenv("GONKA_MODEL", "MiniMaxAI/MiniMax-M2.7")
        return OpenAICompatibleClient(gonka_key, gonka_base, model, provider="gonka")

    if os.getenv("OPENAI_API_KEY"):
        try:
            return OpenAIChatClient()
        except Exception:
            pass
    return MockLLMClient()


def deepseek_research_client(fast: bool = True) -> LLMClient | None:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    base = (os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") or "").split()[0].rstrip("/")
    model_fast = os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")
    model_struct = os.getenv("DEEPSEEK_MODEL_STRUCT", "deepseek-v4-pro")
    return OpenAICompatibleClient(key, base, model_fast if fast else model_struct, provider="deepseek")


def glm_research_client(thinking: bool = True, reasoning_effort: str | None = None) -> LLMClient | None:
    key = os.getenv("GLM_API_KEY") or os.getenv("ZAI_API_KEY")
    if not key:
        return None
    key = key.split()[0]
    base = (os.getenv("GLM_BASE_URL", "https://api.z.ai/api/paas/v4") or "").split()[0].rstrip("/")
    model = os.getenv("GLM_MODEL", "glm-5.2")
    effort = reasoning_effort or os.getenv("GLM_REASONING_EFFORT", "high")
    return ZhipuGLMClient(key, base, model, thinking=thinking, reasoning_effort=effort, provider="glm")

