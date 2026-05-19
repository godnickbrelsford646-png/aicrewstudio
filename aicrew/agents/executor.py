"""AgentExecutor: render prompt, call LLM, parse JSON, persist AgentRun + LLMCall."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .. import db
from ..llm.adapter import LLMAdapter
from ..llm.pricing import estimate_cost
from ..logging_setup import set_agent_run
from ..templates import render


_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _today_human(language: str, ts: float | None = None) -> tuple[str, str, str]:
    """Returns (today_iso, today_human, today_md):
        today_iso     = '2026-05-19' (YYYY-MM-DD, UTC)
        today_human   = '19 мая 2026' (ru) or 'May 19, 2026' (en)
        today_md      = '19 мая' (ru) or 'May 19' (en) — month-day, без года
    Used to push today's date into prompts.
    """
    tm = time.gmtime(ts) if ts is not None else time.gmtime()
    iso = time.strftime("%Y-%m-%d", tm)
    month_idx = tm.tm_mon - 1
    day = tm.tm_mday
    year = tm.tm_year
    if (language or "").lower().startswith("en"):
        human = f"{_EN_MONTHS[month_idx]} {day}, {year}"
        md = f"{_EN_MONTHS[month_idx]} {day}"
    else:
        human = f"{day} {_RU_MONTHS[month_idx]} {year}"
        md = f"{day} {_RU_MONTHS[month_idx]}"
    return iso, human, md


@dataclass
class AgentRunResult:
    agent_run_id: str
    output: dict[str, Any]
    cost_usd: float
    tokens_in: int
    tokens_out: int


class AgentExecutor:
    def __init__(self, settings, llm: LLMAdapter) -> None:
        self.settings = settings
        self.llm = llm

    def run(
        self,
        *,
        agent: dict[str, Any],
        pipeline_run_id: str,
        inputs: dict[str, Any],
        topic_id: str | None = None,
        article_id: str | None = None,
        parent_id: str | None = None,
    ) -> AgentRunResult:
        agent_run_id = db.new_id("ar_")
        set_agent_run(agent_run_id)
        params = db.jload(agent["params"], {}) if isinstance(agent["params"], str) else agent["params"]
        # Resolve language for the prompt:
        #   - агент с явным языком (ru/en) использует его;
        #   - агент с language='bi' (общие на проект: topic_generator,
        #     topic_validator, topic_ranker) берёт язык из inputs, либо 'ru'
        #     по умолчанию (основной язык проекта «Задним числом»).
        agent_lang = (agent.get("language") or "").lower()
        if agent_lang in ("ru", "en"):
            language = agent_lang
        else:
            language = (inputs.get("language") or "ru").lower()
        today_iso, today_human, today_md = _today_human(language)
        ctx = {
            "today": today_iso,
            "today_human": today_human,
            "today_md": today_md,
            "language": language,
            "params": params,
            **inputs,
        }
        rendered = render(agent["prompt_template"], ctx)
        # Decide if this agent should run with live web search via OpenAI's
        # Responses API. Three conditions must hold:
        #   1) the agent's tools_enabled (set in registry per role) includes
        #      "web_search" — currently topic_generator, topic_validator,
        #      researcher, research_validator;
        #   2) the project / deployment opted in with
        #      AICREW_SEARCH_PROVIDER=openai_responses;
        #   3) the agent is routed to an openai:* model (the 302.ai gateway
        #      doesn't expose /responses and the adapter would silently fall
        #      back; we still pass the flag and let the adapter decide).
        tools_enabled = db.jload(agent.get("tools_enabled"), []) \
            if isinstance(agent.get("tools_enabled"), str) \
            else (agent.get("tools_enabled") or [])
        use_web_search = bool(
            getattr(self.settings, "use_openai_web_search", False)
            and "web_search" in (tools_enabled or [])
        )
        started = db.now_iso()
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO agent_runs (id, pipeline_run_id, agent_id, parent_id, status, "
                "inputs, output, rendered_prompt, started_at, attempt, topic_id, article_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    agent_run_id, pipeline_run_id, agent["id"], parent_id, "running",
                    db.jdump(inputs), "{}", rendered, started, 1, topic_id, article_id,
                ),
            )
        try:
            resp = self.llm.call(
                model=agent["model"],
                prompt=rendered,
                temperature=float(agent["temperature"]),
                max_tokens=int(agent["max_tokens"]),
                response_schema=None,
                agent_role=agent["role"],
                agent_params=params,
                inputs=ctx,
                language=language,
                use_web_search=use_web_search,
            )
            cost = estimate_cost(agent["model"], resp.tokens_in, resp.tokens_out)
            output = resp.parsed
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "UPDATE agent_runs SET status=?, output=?, cost_usd=?, tokens_in=?, tokens_out=?, finished_at=? WHERE id=?",
                    ("completed", db.jdump(output), cost, resp.tokens_in, resp.tokens_out, db.now_iso(), agent_run_id),
                )
                conn.execute(
                    "INSERT INTO llm_calls (id, agent_run_id, model, request, response, "
                    "tokens_in, tokens_out, cost_usd, latency_ms, created_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?)",
                    (
                        db.new_id("lc_"), agent_run_id, agent["model"],
                        json.dumps({"prompt": rendered}, ensure_ascii=False),
                        json.dumps({"parsed": output}, ensure_ascii=False),
                        resp.tokens_in, resp.tokens_out, cost, resp.latency_ms,
                        db.now_iso(),
                    ),
                )
            return AgentRunResult(agent_run_id=agent_run_id, output=output,
                                  cost_usd=cost, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)
        except Exception as exc:
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "UPDATE agent_runs SET status=?, error=?, finished_at=? WHERE id=?",
                    ("failed", repr(exc), db.now_iso(), agent_run_id),
                )
            raise
        finally:
            set_agent_run(None)
