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

        # Single source of truth for the target: project.daily_topics_target.
        # The validator's confirmed_topics_target is kept in sync per-run so
        # the agent prompt sees the right number, but the loop is driven by
        # the project setting.
        target = max(1, int(project.get("daily_topics_target") or 0))
        val_params = json.loads(val["params"]) if isinstance(val["params"], str) else dict(val["params"] or {})
        val_params["confirmed_topics_target"] = target
        val_runtime = dict(val)
        val_runtime["params"] = json.dumps(val_params)

        max_retries = max(int(val_params.get("max_retries", 5) or 5), 5)
        # Honor topic_generator.memory_lookback_days. Default 30 days if the
        # agent param is missing (e.g. for older DBs). Topics produced within
        # this window — at any non-rejected status — are added to
        # forbidden_topics so the generator never re-proposes them.
        gen_params = json.loads(gen["params"]) if isinstance(gen["params"], str) \
            else dict(gen["params"] or {})
        lookback_days = int(gen_params.get("memory_lookback_days", 30) or 30)
        forbidden = self._memory_forbidden_titles(project_id, lookback_days=lookback_days)
        confirmed: list[dict[str, Any]] = []

        log.info("topic phase: project=%s target=%d max_retries=%d", project_id, target, max_retries)
        for attempt in range(max_retries):
            gen_out = self.executor.run(
                agent=gen, pipeline_run_id=prid,
                inputs={"project": project, "forbidden_topics": forbidden + [c["title"] for c in confirmed]},
            ).output
            cands = gen_out.get("topics", [])
            val_out = self.executor.run(
                agent=val_runtime, pipeline_run_id=prid,
                inputs={"candidate_topics": cands},
            ).output
            for v in val_out.get("validated", []):
                if v.get("is_valid") and v["title"] not in [c["title"] for c in confirmed]:
                    confirmed.append(v)
            log.info("topic phase: attempt %d/%d -> confirmed=%d / target=%d",
                     attempt + 1, max_retries, len(confirmed), target)
            if len(confirmed) >= target:
                break

        if len(confirmed) < target:
            log.warning("topic phase: stopped with %d confirmed of %d target after %d attempts",
                        len(confirmed), target, max_retries)

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
            # Step 1: write article TEXT for each language (no images yet).
            written: list[tuple[str, str, dict[str, Any]]] = []  # (article_id, lang, article_out)
            for lang in languages:
                pair = self._write_article_text(project, agents, topic, lang, prid)
                if pair:
                    article_id, article_out = pair
                    written.append((article_id, lang, article_out))
                    article_ids.append(article_id)
            # Step 2: generate ONE image for the whole topic and link the same
            # chosen_image_id to all language versions. Saves Wan calls (was
            # 1 image × N languages × M variants per article = N*M; now M total
            # — typically 1 image per topic for both RU and EN).
            if written:
                self._generate_topic_image(project, agents, topic, written, prid)
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

    def _write_article_text(self, project: dict[str, Any],
                             agents: dict[tuple[str, str], dict[str, Any]],
                             topic: dict[str, Any], lang: str, prid: str
                             ) -> tuple[str, dict[str, Any]] | None:
        """Run research → article → headlines → qa_editorial for one language.

        Does NOT generate any images. The article is saved with status
        ``qa_passed`` and chosen_headline already chosen, but
        ``chosen_image_id`` stays NULL. Image is attached later by
        ``_generate_topic_image`` once for the whole topic (shared across
        languages).

        Returns (article_id, article_out_dict) so the caller can reuse the
        article body / tldr / title for the per-topic image prompt step.
        """
        topic_inputs = {
            "title": topic["title"],
            "summary_extended": topic.get("summary", ""),
            "sources": json.loads(topic.get("sources") or "[]"),
        }
        researcher = agents.get(("researcher", lang))
        if not researcher:
            return None
        brief = self.executor.run(
            agent=researcher, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"topic": topic_inputs, "language": lang},
        ).output
        validator = agents.get(("research_validator", lang))
        if validator:
            brief = self.executor.run(
                agent=validator, pipeline_run_id=prid, topic_id=topic["id"],
                inputs={"research_brief": brief, "language": lang, "topic": topic_inputs},
            ).output
        writer = agents.get(("article_writer", lang))
        article_out = self.executor.run(
            agent=writer, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"topic": topic_inputs, "research_validated": brief, "language": lang,
                    "project": project},
        ).output
        headline_writer = agents.get(("headline_writer", lang))
        headlines_out = self.executor.run(
            agent=headline_writer, pipeline_run_id=prid, topic_id=topic["id"],
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
        # QA editorial (text-only check + headline pick + minimal edits).
        qa_ed = agents.get(("qa_editorial", lang))
        qa_out = self.executor.run(
            agent=qa_ed, pipeline_run_id=prid, article_id=article_id,
            inputs={"article": article_out, "headlines": headlines_out.get("headlines", []),
                    "language": lang},
        ).output
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE articles SET chosen_headline=?, body_full=?, "
                "qa_score=?, qa_notes=?, status='qa_passed', written_at=? WHERE id=?",
                (
                    qa_out.get("chosen_headline"),
                    qa_out.get("revised_body_md") or article_out.get("body_md", ""),
                    int(qa_out.get("score", 0)),
                    db.jdump(qa_out.get("issues", [])),
                    db.now_iso(), article_id,
                ),
            )
        # Make the final body available to the caller (so the image prompt
        # step sees the post-QA text, not the raw draft).
        article_out["body_md"] = qa_out.get("revised_body_md") or article_out.get("body_md", "")
        article_out["title_working"] = qa_out.get("chosen_headline") or article_out.get("title_working")
        return article_id, article_out

    def _generate_topic_image(self, project: dict[str, Any],
                               agents: dict[tuple[str, str], dict[str, Any]],
                               topic: dict[str, Any],
                               written: list[tuple[str, str, dict[str, Any]]],
                               prid: str) -> None:
        """Generate ONE image per topic and attach to ALL language versions.

        Uses the FIRST written article (typically the project's primary
        language) as the source for image_prompt_writer. The resulting
        image rows in media_assets are linked to the first article_id by
        FK; the same chosen_image_id is then written into every
        articles.chosen_image_id row of this topic.
        """
        first_article_id, first_lang, first_article_out = written[0]
        ipw = agents.get(("image_prompt_writer", first_lang))
        if not ipw:
            log.warning("topic %s: no image_prompt_writer for lang=%s, skipping image",
                        topic["id"], first_lang)
            return
        prompts_out = self.executor.run(
            agent=ipw, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"article": first_article_out, "language": first_lang},
        ).output
        ipw_params = json.loads(ipw["params"]) if isinstance(ipw["params"], str) else (ipw["params"] or {})
        image_model = ipw_params.get("image_model", "mock:placeholder") or "mock:placeholder"
        media_assets: list[dict[str, Any]] = []
        for i, p in enumerate(prompts_out.get("image_prompts", [])):
            res = generate_image(p["prompt"], settings=self.settings, idx=i,
                                  model=image_model)
            asset_id = db.new_id("ma_")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "INSERT INTO media_assets (id, article_id, project_id, kind, language, prompt, "
                    "model, storage_url, mime, width, height, chosen, meta, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id, first_article_id, project["id"], "image",
                        # language='multi' to signal that this asset is shared
                        # across all language versions of the topic. The UI can
                        # treat 'multi' the same as the article's own language.
                        "multi",
                        p["prompt"],
                        res.model, res.storage_url, res.mime, res.width, res.height, 0,
                        db.jdump({"index": i, "negative": p.get("negative"),
                                   "topic_id": topic["id"]}),
                        db.now_iso(),
                    ),
                )
            media_assets.append({"id": asset_id, "url": res.storage_url, "index": i})
        if not media_assets:
            return
        # QA visual: pick the best of the generated variants. We use the
        # qa_visual agent of the first language; the image is language-neutral
        # so any qa_visual works.
        qa_vi = agents.get(("qa_visual", first_lang))
        chosen_idx = 0
        if qa_vi and len(media_assets) > 1:
            visual_out = self.executor.run(
                agent=qa_vi, pipeline_run_id=prid, article_id=first_article_id,
                inputs={"article": first_article_out, "image_options": media_assets,
                        "language": first_lang},
            ).output
            chosen_idx = max(0, min(len(media_assets) - 1, int(visual_out.get("chosen_index", 0))))
        chosen_image_id = media_assets[chosen_idx]["id"]
        # Mark chosen, and propagate the same image to ALL language versions.
        with db.connect(self.settings.db_path) as conn:
            conn.execute("UPDATE media_assets SET chosen=1 WHERE id=?", (chosen_image_id,))
            for article_id, _lang, _out in written:
                conn.execute(
                    "UPDATE articles SET chosen_image_id=? WHERE id=?",
                    (chosen_image_id, article_id),
                )

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

    # Public alias: создать запись pipeline_run и вернуть id ДО старта работы.
    # Используется API для асинхронного запуска: id отдаётся клиенту сразу,
    # а фактическая работа крутится в фоне с этим id.
    def create_pipeline_run(self, project_id: str, kind: str = "full") -> str:
        return self._start_pipeline_run(project_id, kind)

    def _memory_forbidden_titles(self, project_id: str,
                                  lookback_days: int = 30) -> list[str]:
        """Topics already covered (or in flight) within the last N days.

        Used to seed ``forbidden_topics`` in the topic_generator prompt so the
        same story doesn't reappear when the user re-runs the pipeline within
        the same window.

        Includes ALL non-rejected statuses (``ranked``, ``written``, etc.):
        - ``ranked`` — generator already produced this title earlier today;
          we don't want to spend tokens regenerating it.
        - ``written`` — article was drafted, even if not published.
        - published — articles whose posts were published.

        ``lookback_days`` defaults to 30 and can be overridden by the
        topic_generator agent's ``memory_lookback_days`` param. Note: the
        Tavily search cache is INDEPENDENT — same query may hit the same
        cached results, but the topics returned by the LLM will be different
        because forbidden_topics is in the prompt context.
        """
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - max(1, int(lookback_days)) * 86400),
        )
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                "SELECT title FROM topics "
                "WHERE project_id=? AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT 500",
                (project_id, cutoff),
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

    def run_full(self, project_id: str, *, pipeline_run_id: str | None = None) -> PipelineSummary:
        """Run the full pipeline (topics → articles → publication).

        If ``pipeline_run_id`` is provided, the existing pipeline_run row is
        reused (so the API can hand the id to the client BEFORE the work
        actually begins). Otherwise a new "topics" run is created at the
        start of the topic phase, as before.
        """
        # The 'umbrella' pipeline_run row, if provided, is updated at the end
        # to reflect the overall outcome. Each phase still creates its own
        # phase-specific pipeline_run rows so the UI can show stage timings.
        prid_topics = self.run_topic_phase(project_id)
        try:
            article_ids = self.run_article_phase(project_id)
            pub = self.run_publication_phase(project_id)
            status = "completed"
            error: str | None = None
        except Exception as exc:
            log.exception("run_full failed for project=%s", project_id)
            article_ids = []
            pub = {"posts_created": 0, "posts_published": 0, "pipeline_run_id": ""}
            status = "failed"
            error = repr(exc)
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
            if pipeline_run_id:
                conn.execute(
                    "UPDATE pipeline_runs SET status=?, finished_at=?, error=? WHERE id=?",
                    (status, db.now_iso(), error, pipeline_run_id),
                )
        return PipelineSummary(
            pipeline_run_id=pipeline_run_id or prid_topics,
            topics_generated=ts,
            topics_validated=ranked,
            articles_written=len(article_ids),
            posts_created=pub["posts_created"],
            posts_published=pub["posts_published"],
            cost_usd=float(cost),
        )
