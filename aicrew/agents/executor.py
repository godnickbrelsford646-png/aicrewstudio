"""AgentExecutor: render prompt, call LLM, parse JSON, persist AgentRun + LLMCall."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .. import db
from ..llm.adapter import LLMAdapter
from ..llm.pricing import estimate_cost
from ..logging_setup import set_agent_run
from ..templates import render
from ..tools.search import web_search

log = logging.getLogger("aicrew.agent_executor")


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
        # ---- Pre-search via Tavily (or other future search providers) ----
        # If the agent has params.search_query_template set AND the deployment
        # is configured with AICREW_SEARCH_PROVIDER in {tavily, mock}, we run
        # one web search BEFORE the LLM call and prepend the results to the
        # rendered prompt. This way:
        #   - any LLM (openai, 302.ai, mock) works the same way;
        #   - results are stored in agent_runs.inputs as web_research, so the
        #     UI can show what was found and what citations the article uses;
        #   - the search_cache table dedupes calls (default 24h TTL, see
        #     aicrew/tools/search.py).
        # If the template is empty, no search happens.
        web_research: list[dict[str, Any]] = []
        web_query = ""
        sp = getattr(self.settings, "search_provider", "mock")
        query_tmpl = (params or {}).get("search_query_template") or ""
        if query_tmpl and sp in ("tavily", "mock"):
            try:
                web_query = render(query_tmpl, ctx).strip()
            except Exception as exc:
                log.warning("search_query_template render failed for role=%s: %s",
                            agent.get("role"), exc)
                web_query = ""
            if web_query:
                depth = (params or {}).get("search_depth", "basic")
                max_results = int((params or {}).get("search_max_results", 5) or 5)
                web_research = web_search(
                    web_query,
                    settings=self.settings,
                    language=language,
                    depth=depth,
                    max_results=max_results,
                )
        if web_research:
            ctx["web_research"] = web_research
            ctx["web_query"] = web_query
            # Prepend a clearly delimited block so the LLM uses these as the
            # primary, dated source. The block format is intentionally simple
            # (numbered, with URL and snippet) so any model can quote from it.
            block_lines = [
                f"WEB SEARCH RESULTS for query: {web_query}",
                f"(Use these as the primary, dated source. {len(web_research)} results.)",
                "",
            ]
            for i, r in enumerate(web_research, start=1):
                title = (r.get("title") or "").strip()
                url = (r.get("url") or "").strip()
                content = (r.get("content") or "").strip()
                block_lines.append(f"[{i}] {title}")
                if url:
                    block_lines.append(f"URL: {url}")
                if content:
                    block_lines.append(content)
                block_lines.append("")
            block_lines.append("--- end of web search results ---")
            block_lines.append("")
            rendered = "\n".join(block_lines) + rendered
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
