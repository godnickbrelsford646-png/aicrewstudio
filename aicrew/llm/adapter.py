"""LLM adapter: real OpenAI skeleton + a deterministic mock provider.

The mock provider is what powers the offline demo: each agent role has a
hand-tuned response generator that returns realistic structured data based on
the rendered prompt and inputs. This way the entire pipeline can run with no
network access and produce believable outputs.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..settings import Settings


@dataclass
class LLMResponse:
    text: str
    parsed: dict[str, Any]
    tokens_in: int
    tokens_out: int
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)


class LLMAdapter(Protocol):
    name: str

    def call(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        response_schema: dict[str, Any] | None,
        agent_role: str | None = None,
        agent_params: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


class OpenAILLMAdapter:
    """Skeleton. Implements the same interface, real HTTP call goes here."""

    name = "openai"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def call(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        response_schema: dict[str, Any] | None,
        agent_role: str | None = None,
        agent_params: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> LLMResponse:
        # Real implementation would POST to OpenAI Responses API with
        # response_format=json_schema and parse the structured output.
        # We intentionally do not do live network calls in the sandbox.
        raise NotImplementedError(
            "OpenAI adapter is a skeleton in this build. "
            "Set AICREW_LLM_PROVIDER=mock or wire OpenAI SDK."
        )


# ---------- Mock provider --------------------------------------------------


def _seed_int(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _pick(seq: list[str], salt: str) -> str:
    return seq[_seed_int(salt) % len(seq)]


# Deterministic generator registry: agent_role -> function(inputs, params, language)
MockGenerator = Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]]
_GENERATORS: dict[str, MockGenerator] = {}


def register(role: str) -> Callable[[MockGenerator], MockGenerator]:
    def deco(fn: MockGenerator) -> MockGenerator:
        _GENERATORS[role] = fn
        return fn

    return deco


class MockLLMAdapter:
    name = "mock"

    def call(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        response_schema: dict[str, Any] | None,
        agent_role: str | None = None,
        agent_params: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        params = agent_params or {}
        ins = inputs or {}
        lang = ins.get("language") or params.get("language") or "ru"
        gen = _GENERATORS.get(agent_role or "")
        if gen is None:
            data = {"echo": prompt[:256], "note": f"no mock generator for role={agent_role}"}
        else:
            data = gen(ins, params, lang)
        text = json.dumps(data, ensure_ascii=False)
        # cheap-but-realistic token counting
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(text) // 4)
        latency = int((time.perf_counter() - started) * 1000) + 30
        return LLMResponse(
            text=text,
            parsed=data,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency,
            raw={"provider": "mock", "model": model},
        )


def get_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "mock":
        return MockLLMAdapter()
    if settings.llm_provider == "openai":
        import os

        return OpenAILLMAdapter(api_key=os.environ.get("OPENAI_API_KEY", ""))
    raise ValueError(f"unknown LLM provider: {settings.llm_provider}")


# Public access for agents module to register their mock generators.
__all__ = ["LLMAdapter", "LLMResponse", "MockLLMAdapter", "OpenAILLMAdapter", "get_adapter", "register"]
