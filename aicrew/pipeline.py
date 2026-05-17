"""PipelineRunner: orchestrates topic/article/publication phases end-to-end.

The runner is intentionally synchronous in this MVP – we want a single
deterministic flow that can be exercised by ``make demo``. In production this
would be split across Celery tasks driven by Redis queues; the function shape
already mirrors that design (each phase is a self-contained method).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import db
from .agents.executor import AgentExecutor
from .agents.registry import LANG_SCOPED_ROLES
from .channels.registry import CHANNEL_KINDS
from .crypto import decrypt
from .llm.adapter import get_adapter
from .logging_setup import set_pipeline_run
from .publishers import get_publisher
from .settings import Settings
from .tools.image_gen import generate_image

log = logging.getLogger("aicrew.pipeline")


@dataclass
class PipelineSummary:
    pipeline_run_id: str
    topics_generated: int
    topics_validated: int
    articles_written: int
    posts_created: int
    posts_published: int
    cost_usd: float


def _topic_fingerprint(title: str) -> str:
    return hashlib.sha1(title.lower().strip().encode("utf-8")).hexdigest()


def _agents_by_role(conn, project_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM agents WHERE project_id=? AND is_enabled=1", (project_id,)
    ).fetchall()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        d = db.row_to_dict(r) or {}
        out[(d["role"], d.get("language") or "bi")] = d
    return out


def _project(conn, project_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise KeyError(project_id)
    return db.row_to_dict(row)  # type: ignore[return-value]


class PipelineRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.executor = AgentExecutor(settings, get_adapter(settings))

    # ---------------- topic phase ---------------------------------------

    def run_topic_phase(self, project_id: str, *, pipeline_run_id: str | None = None) -> str:
        with db.connect(self.settings.db_path) as conn:
            project = _project(conn, project_id)
            agents = _agents_by_role(conn, project_id)
        prid = pipeline_run_id or self._start_pipeline_run(project_id, "topics")
        gen = agents.get(("topic_generator", "bi"))
        val = agents.get(("topic_validator", "bi"))
        ranker = agents.get(("topic_ranker", "bi"))
        if not (gen and val and ranker):
            raise RuntimeError("topic agents missing in project")

        target = int(json.loads(val["params"]).get("confirmed_topics_target", 5))
        max_retries = int(json.loads(val["params"]).get("max_retries", 3))
        forbidden = self._memory_forbidden_titles(project_id)
        confirmed: list[dict[str, Any]] = []

        for attempt in range(max_retries):
            gen_out = self.executor.run(
                agent=gen, pipeline_run_id=prid,
                inputs={"project": project, "forbidden_topics": forbidden + [c["title"] for c in confirmed]},
            ).output
            cands = gen_out.get("topics", [])
            val_out = self.executor.run(
                agent=val, pipeline_run_id=prid,
                inputs={"candidate_topics": cands},
            ).output
            for v in val_out.get("validated", []):
                if v.get("is_valid") and v["title"] not in [c["title"] for c in confirmed]:
                    confirmed.append(v)
            if len(confirmed) >= target:
                break

        rank_out = self.executor.run(
            agent=ranker, pipeline_run_id=prid,
            inputs={"validated_topics": confirmed},
        ).output

        # persist topics
        scored_by_title = {r["title"]: r for r in rank_out.get("ranked", [])}
        with db.connect(self.settings.db_path) as conn:
            for v in confirmed:
                title = v["title"]
                fp = _topic_fingerprint(title)
                exists = conn.execute(
                    "SELECT 1 FROM topics WHERE project_id=? AND fingerprint=?",
                    (project_id, fp),
                ).fetchone()
                if exists:
                    continue
                rank = scored_by_title.get(title) or {}
                conn.execute(
                    "INSERT INTO topics (id, project_id, pipeline_run_id, title, summary, sources, "
                    "status, score_total, scores, fingerprint, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        db.new_id("tp_"), project_id, prid, title,
                        v.get("summary_extended", ""),
                        db.jdump(v.get("sources", [])),
                        "ranked",
                        float(rank.get("score_total", 0)),
                        db.jdump(rank.get("scores", {})),
                        fp, db.now_iso(),
                    ),
                )
            conn.execute(
                "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                ("completed", db.now_iso(), prid),
            )
        return prid

    # ---------------- article phase -------------------------------------

    def run_article_phase(self, project_id: str, *, pipeline_run_id: str | None = None,
                          max_articles: int | None = None) -> list[str]:
        with db.connect(self.settings.db_path) as conn:
            project = _project(conn, project_id)
            agents = _agents_by_role(conn, project_id)
            languages = json.loads(project["language_modes"])
            limit = max_articles or int(project["daily_articles_target"])
            top_topics = conn.execute(
                "SELECT * FROM topics WHERE project_id=? AND status='ranked' "
                "ORDER BY score_total DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            top_topics = db.rows_to_list(top_topics)

        prid = pipeline_run_id or self._start_pipeline_run(project_id, "articles")
        article_ids: list[str] = []
        for topic in top_topics:
            for lang in languages:
                article_id = self._write_article(project, agents, topic, lang, prid)
                if article_id:
                    article_ids.append(article_id)
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "UPDATE topics SET status='written' WHERE id=? AND status='ranked'",
                    (topic["id"],),
                )

        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                ("completed", db.now_iso(), prid),
            )
        return article_ids

    def _write_article(self, project: dict[str, Any], agents: dict[tuple[str, str], dict[str, Any]],
                       topic: dict[str, Any], lang: str, prid: str) -> str | None:
        topic_inputs = {
            "title": topic["title"],
            "summary_extended": topic.get("summary", ""),
            "sources": json.loads(topic.get("sources") or "[]"),
        }
        # Researcher
        researcher = agents.get(("researcher", lang))
        if not researcher:
            return None
        brief = self.executor.run(
            agent=researcher, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"topic": topic_inputs, "language": lang},
        ).output
        # ResearchValidator (rewrites in place)
        validator = agents.get(("research_validator", lang))
        if validator:
            brief = self.executor.run(
                agent=validator, pipeline_run_id=prid, topic_id=topic["id"],
                inputs={"research_brief": brief, "language": lang, "topic": topic_inputs},
            ).output
        # ArticleWriter
        writer = agents.get(("article_writer", lang))
        article_out = self.executor.run(
            agent=writer, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"topic": topic_inputs, "research_validated": brief, "language": lang,
                    "project": project},
        ).output
        # Headlines
        headline_writer = agents.get(("headline_writer", lang))
        headlines_out = self.executor.run(
            agent=headline_writer, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"article": article_out, "language": lang},
        ).output
        # Image prompts + generate images
        ipw = agents.get(("image_prompt_writer", lang))
        prompts_out = self.executor.run(
            agent=ipw, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"article": article_out, "language": lang},
        ).output
        article_id = db.new_id("ar_")
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO articles (id, topic_id, project_id, language, research_brief, "
                "research_validated, body_full, headlines, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    article_id, topic["id"], project["id"], lang,
                    db.jdump(brief), db.jdump(brief), article_out.get("body_md", ""),
                    db.jdump(headlines_out.get("headlines", [])),
                    "drafting", db.now_iso(),
                ),
            )
        media_assets = []
        for i, p in enumerate(prompts_out.get("image_prompts", [])):
            res = generate_image(p["prompt"], settings=self.settings, idx=i,
                                 model=ipw["params"] and json.loads(ipw["params"]).get("image_model", "mock:placeholder")
                                 or "mock:placeholder")
            asset_id = db.new_id("ma_")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "INSERT INTO media_assets (id, article_id, project_id, kind, language, prompt, "
                    "model, storage_url, mime, width, height, chosen, meta, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id, article_id, project["id"], "image", lang, p["prompt"],
                        res.model, res.storage_url, res.mime, res.width, res.height, 0,
                        db.jdump({"index": i, "negative": p.get("negative")}),
                        db.now_iso(),
                    ),
                )
            media_assets.append({"id": asset_id, "url": res.storage_url, "index": i})
        # QA editorial
        qa_ed = agents.get(("qa_editorial", lang))
        qa_out = self.executor.run(
            agent=qa_ed, pipeline_run_id=prid, article_id=article_id,
            inputs={"article": article_out, "headlines": headlines_out.get("headlines", []),
                    "language": lang},
        ).output
        # QA visual
        qa_vi = agents.get(("qa_visual", lang))
        chosen_idx = 0
        if media_assets:
            visual_out = self.executor.run(
                agent=qa_vi, pipeline_run_id=prid, article_id=article_id,
                inputs={"article": article_out, "image_options": media_assets, "language": lang},
            ).output
            chosen_idx = max(0, min(len(media_assets) - 1, int(visual_out.get("chosen_index", 0))))
        chosen_image_id = media_assets[chosen_idx]["id"] if media_assets else None
        with db.connect(self.settings.db_path) as conn:
            if chosen_image_id:
                conn.execute("UPDATE media_assets SET chosen=1 WHERE id=?", (chosen_image_id,))
            conn.execute(
                "UPDATE articles SET chosen_headline=?, chosen_image_id=?, body_full=?, "
                "qa_score=?, qa_notes=?, status='qa_passed', written_at=? WHERE id=?",
                (
                    qa_out.get("chosen_headline"), chosen_image_id,
                    qa_out.get("revised_body_md") or article_out.get("body_md", ""),
                    int(qa_out.get("score", 0)),
                    db.jdump(qa_out.get("issues", [])),
                    db.now_iso(), article_id,
                ),
            )
        return article_id

    # ---------------- publication phase ----------------------------------

    def run_publication_phase(self, project_id: str, *, dry_run: bool = False) -> dict[str, int]:
        """For every (article, channel) pair create a Post and publish via adapter.

        In production this is split: a scheduler enqueues posts for their slots.
        Here we synchronously publish so the demo shows results in one shot.
        """

        with db.connect(self.settings.db_path) as conn:
            channels = db.rows_to_list(conn.execute(
                "SELECT * FROM channels WHERE project_id=? AND is_enabled=1", (project_id,)
            ).fetchall())
            articles = db.rows_to_list(conn.execute(
                "SELECT * FROM articles WHERE project_id=? AND status='qa_passed' "
                "ORDER BY written_at DESC", (project_id,)
            ).fetchall())
            agents = _agents_by_role(conn, project_id)
        prid = self._start_pipeline_run(project_id, "publication")
        posts_created = 0
        posts_published = 0
        for ch in channels:
            spec = CHANNEL_KINDS[ch["kind"]]
            lang_articles = [a for a in articles if a["language"] == ch["language"]]
            if not lang_articles:
                continue
            # respect daily budget per channel: posts_per_day caps how many we publish now
            cap = max(1, int(ch["posts_per_day"]))
            # selection strategy
            if ch["selection_strategy"] == "random_among_written":
                pool = sorted(lang_articles, key=lambda a: a["id"])
            else:
                # by_rank requires topic score; use a join-like approach
                with db.connect(self.settings.db_path) as conn:
                    scored = []
                    for a in lang_articles:
                        t = conn.execute("SELECT score_total FROM topics WHERE id=?",
                                         (a["topic_id"],)).fetchone()
                        scored.append((float(t["score_total"]) if t else 0.0, a))
                pool = [a for _, a in sorted(scored, key=lambda x: x[0], reverse=True)]
            # exclude articles already posted to this channel
            taken = 0
            for a in pool:
                if taken >= cap:
                    break
                with db.connect(self.settings.db_path) as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM posts WHERE article_id=? AND channel_id=?",
                        (a["id"], ch["id"]),
                    ).fetchone()
                if exists:
                    continue
                if spec.is_video:
                    # video pipeline not yet generating final video assets; skip in MVP
                    body = ""
                    headline = a["chosen_headline"]
                    image_id = None
                else:
                    rewriter_inputs = {
                        "article": {
                            "body_md": a["body_full"],
                            "title_working": a["chosen_headline"] or "",
                        },
                        "channel": {
                            "name": ch["name"], "kind": ch["kind"],
                            "max_chars": spec.max_chars,
                        },
                        "language": ch["language"],
                    }
                    rewriter_agent = None
                    if ch.get("rewriter_agent_id"):
                        with db.connect(self.settings.db_path) as conn:
                            row = conn.execute(
                                "SELECT * FROM agents WHERE id=?",
                                (ch["rewriter_agent_id"],),
                            ).fetchone()
                            rewriter_agent = db.row_to_dict(row)
                    if rewriter_agent is None:
                        # fall back to project-level prototype if any
                        rewriter_agent = agents.get(("channel_rewriter", "bi")) \
                                         or agents.get(("channel_rewriter", ch["language"]))
                    if rewriter_agent is None:
                        # last resort: use raw body trimmed
                        body = a["body_full"][: spec.max_chars]
                    else:
                        rew_out = self.executor.run(
                            agent=rewriter_agent, pipeline_run_id=prid, article_id=a["id"],
                            inputs=rewriter_inputs,
                        ).output
                        body = rew_out.get("post_body", "")
                    headline = a["chosen_headline"]
                    image_id = a["chosen_image_id"]
                post_id = db.new_id("po_")
                with db.connect(self.settings.db_path) as conn:
                    conn.execute(
                        "INSERT INTO posts (id, article_id, channel_id, body, headline, "
                        "image_asset_id, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (post_id, a["id"], ch["id"], body, headline, image_id, "scheduled",
                         db.now_iso()),
                    )
                posts_created += 1
                taken += 1
                if dry_run:
                    continue
                # publish via adapter (mock by default)
                creds = self._channel_creds(ch)
                publisher = get_publisher(ch["kind"])
                try:
                    res = publisher.publish(
                        post={"id": post_id, "body": body, "headline": headline,
                              "image_asset_id": image_id, "language": ch["language"]},
                        creds=creds, settings=self.settings,
                    )
                    with db.connect(self.settings.db_path) as conn:
                        if res.ok:
                            conn.execute(
                                "UPDATE posts SET status='published', published_at=?, "
                                "external_url=?, provider_meta=? WHERE id=?",
                                (db.now_iso(), res.external_url,
                                 db.jdump(res.raw_response), post_id),
                            )
                            posts_published += 1
                        else:
                            conn.execute(
                                "UPDATE posts SET status='failed', error=?, provider_meta=? WHERE id=?",
                                (res.error or "unknown", db.jdump(res.raw_response), post_id),
                            )
                except Exception as exc:
                    with db.connect(self.settings.db_path) as conn:
                        conn.execute(
                            "UPDATE posts SET status='failed', error=? WHERE id=?",
                            (repr(exc), post_id),
                        )
            # mark articles as published once at least one channel succeeded
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                ("completed", db.now_iso(), prid),
            )
        return {"posts_created": posts_created, "posts_published": posts_published,
                "pipeline_run_id": prid}

    # ---------------- helpers --------------------------------------------

    def _start_pipeline_run(self, project_id: str, kind: str) -> str:
        prid = db.new_id("pr_")
        set_pipeline_run(prid)
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO pipeline_runs (id, project_id, kind, status, started_at) "
                "VALUES (?,?,?,?,?)",
                (prid, project_id, kind, "running", db.now_iso()),
            )
        return prid

    def _memory_forbidden_titles(self, project_id: str) -> list[str]:
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                "SELECT t.title FROM topics t "
                "JOIN articles a ON a.topic_id = t.id "
                "JOIN posts p ON p.article_id = a.id AND p.status='published' "
                "WHERE t.project_id=? GROUP BY t.id "
                "ORDER BY MAX(p.published_at) DESC LIMIT 200",
                (project_id,),
            ).fetchall()
        return [r["title"] for r in rows]

    def _channel_creds(self, channel: dict[str, Any]) -> dict[str, str]:
        enc = channel.get("credentials_enc") or ""
        if not enc:
            return {}
        try:
            return json.loads(decrypt(enc, self.settings.master_key))
        except Exception as exc:
            log.warning("failed to decrypt credentials for channel %s: %s", channel.get("id"), exc)
            return {}

    def run_full(self, project_id: str) -> PipelineSummary:
        prid_topics = self.run_topic_phase(project_id)
        article_ids = self.run_article_phase(project_id)
        pub = self.run_publication_phase(project_id)
        with db.connect(self.settings.db_path) as conn:
            ts = conn.execute("SELECT COUNT(*) c FROM topics WHERE project_id=?",
                              (project_id,)).fetchone()["c"]
            ranked = conn.execute(
                "SELECT COUNT(*) c FROM topics WHERE project_id=? AND status IN ('ranked','written')",
                (project_id,),
            ).fetchone()["c"]
            cost = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) c FROM agent_runs ar "
                "JOIN pipeline_runs pr ON pr.id=ar.pipeline_run_id WHERE pr.project_id=?",
                (project_id,),
            ).fetchone()["c"]
        return PipelineSummary(
            pipeline_run_id=prid_topics,
            topics_generated=ts,
            topics_validated=ranked,
            articles_written=len(article_ids),
            posts_created=pub["posts_created"],
            posts_published=pub["posts_published"],
            cost_usd=float(cost),
        )
