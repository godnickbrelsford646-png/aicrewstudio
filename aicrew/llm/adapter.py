"""LLM adapter: real OpenAI provider + 302.ai gateway + mock provider.

OpenAI-compatible providers (302.ai gateway) share the same wire format
(``POST /v1/chat/completions`` with ``Authorization: Bearer <key>``), so they
are implemented by the same adapter class with a different ``base_url``.

The mock provider stays available for offline demos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..settings import Settings

log = logging.getLogger("aicrew.llm")


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
    """Real OpenAI-compatible adapter. Works with OpenAI and 302.ai gateway.

    Wire format: ``POST {base_url}/chat/completions`` with body
    ``{"model": ..., "messages": [...], "temperature": ..., ...}``,
    ``Authorization: Bearer <api_key>``.

    Model strings:
      - ``openai:gpt-4o``       -> OpenAI native, model="gpt-4o"
      - ``openai:gpt-4o-mini``  -> OpenAI native, model="gpt-4o-mini"
      - ``302ai:gpt-4o``        -> 302.ai gateway, model="gpt-4o"
      - ``302ai:gemini-2.5-pro``-> 302.ai gateway, any model 302.ai supports
    """

    name = "openai"

    # Default base URLs per provider prefix.
    BASE_URLS = {
        "openai": "https://api.openai.com/v1",
        "302ai": "https://api.302.ai/v1",
    }

    def __init__(self, openai_key: str = "", ai302_key: str = "") -> None:
        self.openai_key = openai_key or os.environ.get("OPENAI_API_KEY", "")
        self.ai302_key = ai302_key or os.environ.get("AI302_API_KEY", "")

    def _route(self, model: str) -> tuple[str, str, str]:
        """Returns (base_url, api_key, real_model_name)."""
        if ":" in model:
            prefix, real = model.split(":", 1)
        else:
            prefix, real = "openai", model
        prefix = prefix.lower()
        if prefix == "302ai":
            if not self.ai302_key:
                raise RuntimeError(
                    "302.ai requested but AI302_API_KEY is empty. "
                    "Set AI302_API_KEY in /opt/aicrewstudio/.env."
                )
            return self.BASE_URLS["302ai"], self.ai302_key, real
        # default to OpenAI
        if not self.openai_key:
            raise RuntimeError(
                "OpenAI requested but OPENAI_API_KEY is empty. "
                "Set OPENAI_API_KEY in /opt/aicrewstudio/.env."
            )
        return self.BASE_URLS["openai"], self.openai_key, real

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
        base_url, api_key, real_model = self._route(model)
        # System message instructs the model to ALWAYS reply with a single
        # JSON object — this matches our agent prompts that already say
        # "Return JSON".
        body = {
            "model": real_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. Always reply with a "
                        "single valid JSON object, no markdown, no commentary "
                        "before or after the JSON. The user prompt explains "
                        "the required JSON shape."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "response_format": {"type": "json_object"},
        }
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            log.error("LLM HTTP %s: %s", exc.code, err_body[:500])
            raise RuntimeError(
                f"LLM provider returned HTTP {exc.code}: {err_body[:300]}"
            ) from exc
        except Exception as exc:
            log.exception("LLM request failed")
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        latency = int((time.perf_counter() - started) * 1000)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM returned non-JSON: {raw[:200]!r}") from exc
        # Extract message
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"LLM response missing choices: {payload}") from exc
        # Parse the JSON the model produced.
        parsed = _parse_json_loose(text)
        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text,
            parsed=parsed,
            tokens_in=int(usage.get("prompt_tokens", 0) or 0),
            tokens_out=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency,
            raw={"provider": "openai-compat", "model": real_model,
                 "base_url": base_url},
        )


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Try hard to extract a JSON object from the model's reply."""
    text = (text or "").strip()
    if not text:
        return {}
    # 1. Strip ```json fences.
    if text.startswith("```"):
        lines = text.split("\n")
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 2. Direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 3. Find first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        chunk = text[start : end + 1]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass
    # Give up but don't crash the pipeline.
    return {"_raw_text": text}


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
    # "openai" provider supports both openai: and 302ai: prefixed model names.
    if settings.llm_provider in ("openai", "302ai"):
        return OpenAILLMAdapter(
            openai_key=os.environ.get("OPENAI_API_KEY", ""),
            ai302_key=os.environ.get("AI302_API_KEY", ""),
        )
    raise ValueError(f"unknown LLM provider: {settings.llm_provider}")


# Public access for agents module to register their mock generators.
__all__ = ["LLMAdapter", "LLMResponse", "MockLLMAdapter", "OpenAILLMAdapter", "get_adapter", "register"]
