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
        # ensure language present in inputs for templates
        ctx = {
            "today": time.strftime("%Y-%m-%d", time.gmtime()),
            "language": agent.get("language") or inputs.get("language") or "ru",
            "params": params,
            **inputs,
        }
        rendered = render(agent["prompt_template"], ctx)
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
