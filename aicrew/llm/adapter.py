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
        language: str | None = None,
        use_web_search: bool = False,
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
        language: str | None = None,
        use_web_search: bool = False,
    ) -> LLMResponse:
        base_url, api_key, real_model = self._route(model)
        # Web search is only supported via OpenAI's Responses API. The 302.ai
        # gateway is OpenAI-compatible for /chat/completions but does NOT
        # currently expose /v1/responses + the web_search tool. So if the
        # caller asked for web_search but we're routed to 302.ai, we log a
        # warning and silently fall back to plain chat/completions.
        if use_web_search and "302.ai" in base_url:
            log.warning(
                "web_search requested but model is routed to 302.ai which does "
                "not support /responses; falling back to chat/completions for "
                "model=%s. Use openai:* prefix to enable real web search.",
                model,
            )
            use_web_search = False
        if use_web_search:
            return self._call_responses_with_web_search(
                base_url=base_url,
                api_key=api_key,
                real_model=real_model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                language=language,
            )
        # Language-aware system message: if a language is given, lock the
        # model to reply ONLY in that language. Otherwise fall back to the
        # generic English instruction. This is the fix for "English-channel
        # agents replying in Russian": GPT tends to mirror the user prompt's
        # language unless the system message explicitly forbids it.
        lang_norm = (language or "").strip().lower()
        if lang_norm == "en":
            system_msg = (
                "You are a helpful assistant. You MUST reply in English only, "
                "regardless of the language of the user prompt. Always reply "
                "with a single valid JSON object, no markdown, no commentary "
                "before or after the JSON. The user prompt explains the "
                "required JSON shape."
            )
        elif lang_norm == "ru":
            system_msg = (
                "Ты помощник. Отвечай ТОЛЬКО на русском языке, независимо от "
                "языка пользовательского промта. Всегда отвечай одним валидным "
                "JSON-объектом, без markdown, без комментариев до или после "
                "JSON. В пользовательском промте описана требуемая форма JSON."
            )
        else:
            system_msg = (
                "You are a helpful assistant. Always reply with a "
                "single valid JSON object, no markdown, no commentary "
                "before or after the JSON. The user prompt explains "
                "the required JSON shape."
            )
        body = {
            "model": real_model,
            "messages": [
                {"role": "system", "content": system_msg},
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

    def _call_responses_with_web_search(
        self,
        *,
        base_url: str,
        api_key: str,
        real_model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        language: str | None,
    ) -> LLMResponse:
        """Call OpenAI Responses API (POST /v1/responses) with web_search tool.

        This is used for agents that need real, current web search — primarily
        topic_generator, topic_validator, researcher and research_validator.
        Replaces the old Tavily/Serper integration plan: we lean on OpenAI's
        first-party tool, so we only need one API key (OPENAI_API_KEY).

        Wire format (high level)::

            POST {base_url}/responses
            {
              "model": "gpt-4o",
              "input": [
                {"role":"system","content":"..."},
                {"role":"user","content":"..."}
              ],
              "tools": [{"type":"web_search_preview"}],
              "temperature": 0.7,
              "max_output_tokens": 2000,
              "text": {"format": {"type": "json_object"}}
            }

        Response: ``output`` is an array of items; we pick the one with
        ``type == "message"`` and read ``content[0].text``. Citations (if
        present) are kept in ``LLMResponse.raw["annotations"]`` for later
        display in the UI.

        Note on tool name: OpenAI uses ``web_search_preview`` for the
        currently-available tool in the Responses API. If a future model
        only accepts ``web_search``, we retry once with the alternate name.
        """
        lang_norm = (language or "").strip().lower()
        if lang_norm == "en":
            system_msg = (
                "You are a research assistant with web access. Use the "
                "web_search tool aggressively to find current, accurate "
                "information before answering. You MUST reply in English "
                "only, regardless of the user prompt language. Always "
                "reply with a single valid JSON object, no markdown, no "
                "commentary before or after the JSON. The user prompt "
                "explains the required JSON shape."
            )
        elif lang_norm == "ru":
            system_msg = (
                "Ты — исследователь с доступом к вебу. Активно используй "
                "инструмент web_search, чтобы находить свежую и точную "
                "информацию до того, как отвечаешь. Отвечай ТОЛЬКО на "
                "русском языке, независимо от языка пользовательского "
                "промта. Всегда отвечай одним валидным JSON-объектом, без "
                "markdown, без комментариев до или после JSON. В промте "
                "описана требуемая форма JSON."
            )
        else:
            system_msg = (
                "You are a research assistant with web access. Use the "
                "web_search tool aggressively. Always reply with a single "
                "valid JSON object."
            )

        url = base_url.rstrip("/") + "/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        def _build_body(tool_type: str) -> dict[str, Any]:
            return {
                "model": real_model,
                "input": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "tools": [{"type": tool_type}],
                "temperature": float(temperature),
                "max_output_tokens": int(max_tokens),
                # Force JSON output. The Responses API uses `text.format`
                # rather than `response_format` from chat/completions.
                "text": {"format": {"type": "json_object"}},
            }

        def _post(body: dict[str, Any]) -> tuple[bytes, int]:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=240) as resp:
                return resp.read(), int((time.perf_counter() - t0) * 1000)

        started = time.perf_counter()
        try:
            raw, latency = _post(_build_body("web_search_preview"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            # Some OpenAI models accept "web_search" instead of
            # "web_search_preview". Retry once on a 400 that mentions
            # the unknown tool.
            if exc.code == 400 and "web_search" in err_body:
                log.warning(
                    "Responses API rejected web_search_preview, retrying "
                    "with web_search. server said: %s",
                    err_body[:300],
                )
                try:
                    raw, latency = _post(_build_body("web_search"))
                except urllib.error.HTTPError as exc2:
                    err2 = exc2.read().decode("utf-8", errors="replace")
                    log.error(
                        "Responses API HTTP %s on retry: %s",
                        exc2.code, err2[:500],
                    )
                    raise RuntimeError(
                        f"OpenAI /responses HTTP {exc2.code}: {err2[:300]}"
                    ) from exc2
            else:
                log.error(
                    "Responses API HTTP %s: %s", exc.code, err_body[:500],
                )
                raise RuntimeError(
                    f"OpenAI /responses HTTP {exc.code}: {err_body[:300]}"
                ) from exc
        except Exception as exc:
            log.exception("Responses API request failed")
            raise RuntimeError(f"Responses API request failed: {exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Responses API returned non-JSON: {raw[:200]!r}"
            ) from exc

        text, annotations = _extract_responses_text(payload)
        parsed = _parse_json_loose(text)
        usage = payload.get("usage") or {}
        # Responses API uses input_tokens/output_tokens; chat uses
        # prompt_tokens/completion_tokens. Normalize to chat-style names.
        tokens_in = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        tokens_out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        return LLMResponse(
            text=text,
            parsed=parsed,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency,
            raw={
                "provider": "openai-responses",
                "model": real_model,
                "base_url": base_url,
                "annotations": annotations,
                "tool_used": "web_search",
            },
        )


def _extract_responses_text(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Pull the assistant's final text + URL citations out of a Responses payload.

    The Responses API output is an ordered list of items. We want the LAST
    item with ``type == "message"`` (skipping ``web_search_call`` items).
    Inside the message, ``content`` is a list of blocks; we want the first
    block of type ``output_text`` (or ``text`` for older variants) and its
    ``annotations`` array (which contains url_citation entries).
    """
    items = payload.get("output") or []
    text = ""
    annotations: list[dict[str, Any]] = []
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("output_text", "text"):
                text = block.get("text") or ""
                annotations = list(block.get("annotations") or [])
                break
        if text:
            break
    # Some SDKs surface a convenience field. Use it as a last resort.
    if not text and isinstance(payload.get("output_text"), str):
        text = payload["output_text"]
    return text, annotations



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
        language: str | None = None,
        use_web_search: bool = False,
    ) -> LLMResponse:
        started = time.perf_counter()
        params = agent_params or {}
        ins = inputs or {}
        lang = language or ins.get("language") or params.get("language") or "ru"
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
