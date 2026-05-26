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
import os
import time
from dataclasses import dataclass
from typing import Any

from . import db
from .agents.executor import AgentExecutor
from .agents.registry import LANG_SCOPED_ROLES, default_enabled_teams
from .channels.registry import CHANNEL_KINDS
from .crypto import decrypt
from .llm.adapter import get_adapter
from .logging_setup import set_pipeline_run
from .publishers import get_publisher
from .scheduler import next_slot_utc, _utc_iso, publish_due_posts
from .settings import Settings
from .tools.image_gen import generate_image
from .tools.tts_gen import generate_tts
from .tools.video_assembler import assemble_video
from .tools.video_gen import generate_video_clip
from .tools.whisper_transcribe import transcribe_audio

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


def _enabled_teams_of(project: dict[str, Any]) -> set[str]:
    """Parse ``project.enabled_teams`` JSON column into a set.

    Falls back to "all teams enabled" when the column is missing or empty,
    so legacy projects upgraded in place keep working until the API
    backfills them via ``_ensure_enabled_teams``. The legacy fallback uses
    the project's ``language_modes`` so a Russian-only project gets only
    text_ru/video_ru, not the full default.
    """
    raw = project.get("enabled_teams")
    if not raw:
        try:
            langs = json.loads(project.get("language_modes") or '["ru"]')
        except (json.JSONDecodeError, TypeError):
            langs = ["ru"]
        return set(default_enabled_teams(langs))
    if isinstance(raw, list):
        return set(raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return set(parsed)
        return set()
    except (json.JSONDecodeError, TypeError):
        return set()


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
            # Each retry feeds the already-confirmed titles back into
            # forbidden_topics so the generator does not re-propose them;
            # we keep going until either the target is reached or we run
            # out of retries. Under-shooting the target is an acceptable
            # outcome (per product decision).
            gen_out = self.executor.run(
                agent=gen, pipeline_run_id=prid,
                inputs={"project": project,
                        "forbidden_topics": forbidden + [c["title"] for c in confirmed]},
            ).output
            cands = gen_out.get("topics", [])
            # Per-candidate validation: we no longer hand the whole batch
            # to topic_validator with a single generic search query. Each
            # candidate gets its own validator call with
            # candidate_topics=[ONE_candidate]. The validator's
            # search_query_template ("{{ candidate_topics[0].title }}
            # {{ candidate_topics[0].event_date }}") then renders into a
            # specific Tavily query per candidate, giving the agent a
            # WEB SEARCH RESULTS block focused on THAT exact title+date.
            # The 24h search_cache deduplicates repeated queries (same
            # candidate hit twice, identical phrasing across retries).
            #
            # This is the single biggest anti-hallucination win in the
            # topic phase: previously the validator searched
            # "{{ today_md }} historical events fact check" once for the
            # whole batch and then approved candidates "from memory" if
            # the date was right. Now every candidate must actually
            # appear on the web, on the right day, before is_valid=true.
            for cand in cands:
                val_out = self.executor.run(
                    agent=val_runtime, pipeline_run_id=prid,
                    inputs={"candidate_topics": [cand]},
                ).output
                for v in val_out.get("validated", []):
                    if v.get("is_valid") and v["title"] not in [c["title"] for c in confirmed]:
                        confirmed.append(v)
            log.info("topic phase: attempt %d/%d -> confirmed=%d / target=%d",
                     attempt + 1, max_retries, len(confirmed), target)
            if len(confirmed) >= target:
                break

        if len(confirmed) < target:
            log.info("topic phase: stopped with %d confirmed of %d target after %d attempts "
                     "(under-shooting is acceptable)",
                     len(confirmed), target, max_retries)

        rank_out = self.executor.run(
            agent=ranker, pipeline_run_id=prid,
            inputs={"validated_topics": confirmed},
        ).output

        # Compute score_total programmatically from scores * criteria_weights.
        # We don't trust the LLM to do the arithmetic — many models leave
        # score_total at 0 even when scores are populated, which then breaks
        # ordering downstream. Read weights from the ranker's params; fall
        # back to even weights across whatever criteria the LLM produced.
        ranker_params = json.loads(ranker["params"]) if isinstance(ranker["params"], str) \
            else dict(ranker["params"] or {})
        weights = ranker_params.get("criteria_weights") or {}
        for r in rank_out.get("ranked", []) or []:
            scores = r.get("scores") or {}
            if not isinstance(scores, dict):
                scores = {}
            if weights:
                total = sum(float(scores.get(k, 0) or 0) * float(w)
                            for k, w in weights.items())
            elif scores:
                vals = [float(v or 0) for v in scores.values()]
                total = sum(vals) / max(1, len(vals))
            else:
                total = 0.0
            # If the LLM did supply a non-zero score_total AND ours is also
            # non-zero, prefer the larger to be conservative (avoid masking
            # genuine ranking signal). If LLM gave 0, our computed value wins.
            llm_total = float(r.get("score_total") or 0)
            r["score_total"] = round(max(total, llm_total), 2)

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
                    "status, score_total, scores, fingerprint, event_date, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        db.new_id("tp_"), project_id, prid, title,
                        v.get("summary_extended", ""),
                        db.jdump(v.get("sources", [])),
                        "ranked",
                        float(rank.get("score_total", 0)),
                        db.jdump(rank.get("scores", {})),
                        fp,
                        # event_date is the exact date of the historical
                        # event (e.g. "19 мая 1536"). It comes from the
                        # topic_validator and is the anchor that all
                        # downstream agents (researcher, writer, headline,
                        # qa) re-mention so the article keeps the date in
                        # the body and never silently drifts to "today".
                        (v.get("event_date") or "").strip(),
                        db.now_iso(),
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

        # Filter languages by enabled text teams. If the user disabled
        # text_ru in Settings, articles in Russian are not produced even
        # though the project still lists "ru" in language_modes — the
        # rationale is that language_modes is a static project capability
        # while enabled_teams is a runtime toggle the user can flip on/off
        # without re-creating agents.
        enabled = _enabled_teams_of(project)
        languages = [l for l in languages if f"text_{l}" in enabled]
        prid = pipeline_run_id or self._start_pipeline_run(project_id, "articles")
        if not languages:
            log.info("article phase: all text teams disabled, nothing to do")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                    ("completed", db.now_iso(), prid),
                )
            return []

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
        # Safety net: ensure every article in this run shares a chosen_image_id
        # with at least one sibling article of the same topic. Protects against:
        #   * old DBs where _generate_topic_image was per-article and only the
        #     first language got an image;
        #   * topics where image generation had a transient error in some
        #     branches but succeeded in another.
        # Only NULL chosen_image_id rows are touched; existing assignments are
        # preserved.
        self._propagate_topic_images(article_ids)
        return article_ids

    def _propagate_topic_images(self, article_ids: list[str]) -> None:
        """For each topic touched in this article phase, if any of its articles
        already has chosen_image_id, copy that id to every sibling article of
        the same topic that still has chosen_image_id NULL.

        Runs in a single SQLite transaction so it is cheap even with many
        articles and is safe to re-run.
        """
        if not article_ids:
            return
        with db.connect(self.settings.db_path) as conn:
            # Distinct topic_ids touched in this run.
            placeholders = ",".join("?" for _ in article_ids)
            topic_ids = [
                r["topic_id"] for r in conn.execute(
                    f"SELECT DISTINCT topic_id FROM articles WHERE id IN ({placeholders})",
                    article_ids,
                ).fetchall() if r["topic_id"]
            ]
            for tid in topic_ids:
                row = conn.execute(
                    "SELECT chosen_image_id FROM articles "
                    "WHERE topic_id=? AND chosen_image_id IS NOT NULL "
                    "AND chosen_image_id <> '' LIMIT 1",
                    (tid,),
                ).fetchone()
                if not row:
                    log.warning("topic %s: no image attached to any article "
                                "after article phase (image gen likely failed "
                                "for all languages)", tid)
                    continue
                cur = conn.execute(
                    "UPDATE articles SET chosen_image_id=? "
                    "WHERE topic_id=? AND (chosen_image_id IS NULL OR chosen_image_id='')",
                    (row["chosen_image_id"], tid),
                )
                if cur.rowcount:
                    log.info("topic %s: propagated chosen_image_id to %d "
                             "previously-unlinked article(s)",
                             tid, cur.rowcount)

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
            # event_date — exact date of the historical event, e.g.
            # "19 мая 1536". Persisted on the topic by the topic phase
            # from the topic_validator output. ALL downstream agents
            # (researcher, research_validator, article_writer,
            # headline_writer, qa_editorial, image_prompt_writer) read
            # this so the article keeps the date in the body and never
            # silently drifts to "today". Empty string for projects
            # that don't use date anchoring.
            "event_date": (topic.get("event_date") or "").strip(),
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
        # Fact-audit BEFORE headline_writer/qa_editorial. fact_audit walks
        # article.body_md, extracts every concrete claim (names, numbers,
        # quotes, dates) and verifies it against research_validated. If a
        # claim has no support in the brief, it is removed/rephrased into
        # `fixed_body_md`. We use the cleaned body as input to the rest of
        # the chain (headlines, qa_editorial), so any fabricated names or
        # quotes never reach published posts.
        # Falls back gracefully if no fact_audit agent is present (e.g.
        # an old DB pre-migration) — we just skip the step and leave
        # article_out untouched.
        fa_agent = agents.get(("fact_audit", lang))
        fa_out: dict[str, Any] = {}
        if fa_agent:
            fa_out = self.executor.run(
                agent=fa_agent, pipeline_run_id=prid, topic_id=topic["id"],
                inputs={"article": article_out, "research_validated": brief,
                        "topic": topic_inputs, "language": lang},
            ).output
            fixed_body = fa_out.get("fixed_body_md")
            if fixed_body:
                article_out["body_md"] = fixed_body
            unsupported = fa_out.get("unsupported_claims") or []
            log.info("topic %s [%s]: fact_audit score=%s must_fix=%s "
                     "unsupported_claims=%d",
                     topic["id"], lang,
                     fa_out.get("score"), fa_out.get("must_fix"),
                     len(unsupported))
        headline_writer = agents.get(("headline_writer", lang))
        headlines_out = self.executor.run(
            agent=headline_writer, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"article": article_out, "topic": topic_inputs, "language": lang},
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
        # We pass research_validated so the new checklist item #0 (the
        # factcheck pass) can verify that no name/number/quote in the
        # body sneaks past fact_audit. Even though fact_audit already
        # ran, qa_editorial provides a literary-second-pair-of-eyes
        # check that occasionally catches edge cases.
        qa_ed = agents.get(("qa_editorial", lang))
        qa_out = self.executor.run(
            agent=qa_ed, pipeline_run_id=prid, article_id=article_id,
            inputs={"article": article_out, "headlines": headlines_out.get("headlines", []),
                    "topic": topic_inputs, "language": lang,
                    "research_validated": brief},
        ).output
        # Combined QA notes: persist fact_audit findings alongside the
        # editorial issues so the UI / audit trail can see both.
        combined_notes: dict[str, Any] = {
            "issues": qa_out.get("issues", []),
        }
        if fa_out:
            combined_notes["fact_audit"] = {
                "score": fa_out.get("score"),
                "must_fix": fa_out.get("must_fix"),
                "unsupported_claims": fa_out.get("unsupported_claims", []),
            }
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE articles SET chosen_headline=?, body_full=?, "
                "qa_score=?, qa_notes=?, status='qa_passed', written_at=? WHERE id=?",
                (
                    qa_out.get("chosen_headline"),
                    qa_out.get("revised_body_md") or article_out.get("body_md", ""),
                    int(qa_out.get("score", 0)),
                    db.jdump(combined_notes),
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
        # Build a topic_inputs dict identical to what _write_article_text
        # passes to per-language agents, so image agents get event_date.
        topic_inputs = {
            "title": topic["title"],
            "summary_extended": topic.get("summary", ""),
            "sources": json.loads(topic.get("sources") or "[]"),
            "event_date": (topic.get("event_date") or "").strip(),
        }
        # image_prompt_writer is GLOBAL_ROLES (language-neutral) — one
        # instance per project, language='bi'. Falls back to the per-language
        # variant for older DBs that still have language='ru'/'en' rows.
        ipw = (agents.get(("image_prompt_writer", "bi"))
               or agents.get(("image_prompt_writer", first_lang)))
        if not ipw:
            log.warning("topic %s: no image_prompt_writer agent found; "
                        "skipping image", topic["id"])
            return
        prompts_out = self.executor.run(
            agent=ipw, pipeline_run_id=prid, topic_id=topic["id"],
            inputs={"article": first_article_out, "topic": topic_inputs,
                    "language": first_lang},
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
        # QA visual: pick the best of the generated variants. qa_visual is
        # GLOBAL_ROLES (language-neutral) — one instance per project,
        # language='bi'. Falls back to the per-language variant for older
        # DBs.
        qa_vi = (agents.get(("qa_visual", "bi"))
                 or agents.get(("qa_visual", first_lang)))
        chosen_idx = 0
        if qa_vi and len(media_assets) > 1:
            visual_out = self.executor.run(
                agent=qa_vi, pipeline_run_id=prid, article_id=first_article_id,
                inputs={"article": first_article_out, "image_options": media_assets,
                        "topic": topic_inputs, "language": first_lang},
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
        log.info(
            "topic %s: image attached to %d article(s) (lang=%s); chosen_image_id=%s",
            topic["id"], len(written),
            ",".join(lang for _, lang, _ in written),
            chosen_image_id,
        )

    # ---------------- publication phase ----------------------------------

    def run_publication_phase(self, project_id: str, *,
                              dry_run: bool = False,
                              publish_immediately: bool = False
                              ) -> dict[str, int]:
        """Plan upcoming publications and enqueue them with ``scheduled_for``.

        This is the slot-aware replacement for the old "publish now"
        flow. For each active text channel of the project we:

          1. Look up ``channel_slots`` (enabled rows). Each slot is an
             "HH:MM" in ``project.timezone`` — convert to the next
             future UTC moment using zoneinfo. If the channel has no
             slots, fall back to ``posts_per_day`` evenly distributed
             over the day so old projects don't break.

          2. Pick articles by ``selection_strategy`` (``by_rank`` or
             ``random_among_written``), excluding any (article, channel)
             pair already in ``posts`` (UNIQUE constraint guards us
             too, but checking up front avoids wasting LLM calls on
             the rewriter).

          3. For each (article, slot) pair, run the channel rewriter
             (so the per-channel body is ready to publish) and INSERT
             a ``posts`` row with ``status='scheduled'`` and
             ``scheduled_for``. The background scheduler thread will
             pick the row up at the appointed moment.

        ``publish_immediately=True`` skips the slot calculation, sets
        ``scheduled_for=now``, and synchronously runs one tick of
        ``publish_due_posts`` so the user sees results in one shot.
        Useful for the "Полный цикл (опубликовать сейчас)" smoke-test
        button. Default is False — proper scheduled mode.

        ``dry_run=True`` plans the posts (creates ``status='scheduled'``
        rows) but does not run a synchronous publish tick. Same as the
        default mode, kept for backward compat.
        """
        with db.connect(self.settings.db_path) as conn:
            project_row = conn.execute(
                "SELECT timezone FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            tz_name = (project_row["timezone"] if project_row else "UTC") or "UTC"
            project_full = _project(conn, project_id)
            channels = db.rows_to_list(conn.execute(
                "SELECT * FROM channels WHERE project_id=? AND is_enabled=1",
                (project_id,),
            ).fetchall())
            articles = db.rows_to_list(conn.execute(
                "SELECT * FROM articles WHERE project_id=? AND status='qa_passed' "
                "ORDER BY written_at DESC", (project_id,)
            ).fetchall())
            agents = _agents_by_role(conn, project_id)
        # Filter channels: only those whose language has the matching text
        # team enabled. If text_en is off, English channels are silently
        # skipped — the user can still see them in the UI, but new
        # articles won't be queued for them on this run.
        enabled = _enabled_teams_of(project_full)
        before_filter = len(channels)
        channels = [c for c in channels
                    if f"text_{c['language']}" in enabled]
        if len(channels) != before_filter:
            log.info("publication phase: filtered %d channels by "
                     "enabled_teams (kept %d of %d)",
                     before_filter - len(channels), len(channels),
                     before_filter)
        prid = self._start_pipeline_run(project_id, "publication")
        posts_created = 0
        for ch in channels:
            spec = CHANNEL_KINDS[ch["kind"]]
            lang_articles = [a for a in articles if a["language"] == ch["language"]]
            if not lang_articles:
                continue
            slot_times = self._upcoming_slots_utc(
                ch["id"], tz_name,
                fallback_count=max(1, int(ch["posts_per_day"])),
                publish_immediately=publish_immediately,
            )
            if not slot_times:
                continue
            # selection strategy: order articles, then take as many as
            # we have slots, skipping ones already posted to this channel.
            if ch["selection_strategy"] == "random_among_written":
                pool = sorted(lang_articles, key=lambda a: a["id"])
            else:
                with db.connect(self.settings.db_path) as conn:
                    scored = []
                    for a in lang_articles:
                        t = conn.execute(
                            "SELECT score_total FROM topics WHERE id=?",
                            (a["topic_id"],),
                        ).fetchone()
                        scored.append((float(t["score_total"]) if t else 0.0, a))
                pool = [a for _, a in sorted(scored, key=lambda x: x[0],
                                              reverse=True)]
            slot_iter = iter(slot_times)
            for a in pool:
                try:
                    slot_dt = next(slot_iter)
                except StopIteration:
                    break
                with db.connect(self.settings.db_path) as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM posts WHERE article_id=? AND channel_id=?",
                        (a["id"], ch["id"]),
                    ).fetchone()
                if exists:
                    # Article already queued/published for this channel;
                    # we still consumed a slot iteration step? No — we
                    # didn't, we just skip this article and try the
                    # next one for the same slot. Push the slot back.
                    slot_iter = iter([slot_dt, *slot_iter])  # type: ignore[arg-type]
                    continue
                if spec.is_video:
                    body = ""
                    headline = a["chosen_headline"]
                    image_id = None
                else:
                    body, headline, image_id = self._rewrite_for_channel(
                        prid, agents, ch, spec, a)
                post_id = db.new_id("po_")
                with db.connect(self.settings.db_path) as conn:
                    conn.execute(
                        "INSERT INTO posts (id, article_id, channel_id, body, "
                        "headline, image_asset_id, status, scheduled_for, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (post_id, a["id"], ch["id"], body, headline,
                         image_id, "scheduled", _utc_iso(slot_dt),
                         db.now_iso()),
                    )
                posts_created += 1
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                ("completed", db.now_iso(), prid),
            )
        posts_published = 0
        if publish_immediately and not dry_run:
            # One synchronous tick so the user sees published rows in
            # the same request. The scheduler thread would do it on
            # its next tick anyway; this just makes the demo snappy.
            posts_published = publish_due_posts(self.settings)
        return {"posts_created": posts_created,
                "posts_published": posts_published,
                "pipeline_run_id": prid}

    def _upcoming_slots_utc(self, channel_id: str, tz_name: str,
                            *, fallback_count: int,
                            publish_immediately: bool) -> list:
        """List of next-occurrence UTC datetimes for this channel's slots.

        Reads enabled rows from ``channel_slots`` and converts each
        ``time_local`` to the next future UTC moment in the project's
        timezone. Falls back to ``fallback_count`` evenly-spaced
        moments today if the channel has no slots configured (so old
        projects keep working).

        When ``publish_immediately=True`` we ignore slots entirely
        and return ``fallback_count`` copies of "right now". This is
        the smoke-test path; the scheduler will fire them at the
        next tick or the synchronous tick at the end of
        ``run_publication_phase`` will do it in this request.
        """
        from datetime import datetime, timezone, timedelta
        if publish_immediately:
            now = datetime.now(timezone.utc)
            return [now for _ in range(max(1, fallback_count))]
        with db.connect(self.settings.db_path) as conn:
            slots = db.rows_to_list(conn.execute(
                "SELECT time_local FROM channel_slots "
                "WHERE channel_id=? AND enabled=1 ORDER BY time_local",
                (channel_id,),
            ).fetchall())
        if slots:
            return [next_slot_utc(s["time_local"], tz_name) for s in slots]
        # No explicit slots — synthesize fallback_count moments
        # spread across the next 24 h starting an hour from now. Old
        # behaviour was "publish all immediately"; this is the
        # gentlest replacement.
        now = datetime.now(timezone.utc)
        step = timedelta(hours=max(1, 24 // max(1, fallback_count)))
        return [now + step * (i + 1) for i in range(fallback_count)]

    def _rewrite_for_channel(self, prid: str, agents: dict, ch: dict,
                             spec, article: dict) -> tuple[str, str, str | None]:
        """Run the per-channel rewriter and return (body, headline, image_id).

        Extracted from the old run_publication_phase so enqueueing can
        happen ahead of the slot. The rewriter call is the expensive
        part (one LLM round-trip per channel) — we make it once here
        and store the rendered body in ``posts.body`` so the scheduler
        only does the network call to the channel API at fire time.
        """
        rewriter_inputs = {
            "article": {
                "body_md": article["body_full"],
                "title_working": article["chosen_headline"] or "",
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
                if row:
                    rewriter_agent = db.row_to_dict(row)
        if rewriter_agent is None:
            rewriter_agent = (agents.get(("channel_rewriter", "bi"))
                              or agents.get(("channel_rewriter", ch["language"])))
        if rewriter_agent is None:
            body = article["body_full"][: spec.max_chars]
        else:
            rew_out = self.executor.run(
                agent=rewriter_agent, pipeline_run_id=prid,
                article_id=article["id"], inputs=rewriter_inputs,
            ).output
            body = rew_out.get("post_body", "")
        return body, article["chosen_headline"], article["chosen_image_id"]


    # ---------------- video phase ---------------------------------------

    # 9:16 is hard-coded by product decision; the UI does not expose it.
    VIDEO_ASPECT = "9:16"

    def run_video_phase(self, article_id: str) -> dict[str, Any]:
        """Generate a short-form video for one article (mock-friendly).

        Pipeline (10 steps):
            [1] load article + topic + project + agents
            [2] video_scenarist[lang]                -> scenes JSON
            [3] video_keyframe_artist[bi]            -> keyframes JSON
            [4] for each scene: image_gen            -> per-scene PNG
            [5] for each scene: video_gen            -> per-scene MP4 (5s)
            [6] voice_director[lang]                 -> normalised voiceover
            [7] for each scene: tts_gen              -> per-scene MP3
            [8] whisper_transcribe (on combined audio in mock: first MP3)
            [9] subtitle_styler[lang]                -> ASS file
            [10] video_assembler                     -> final MP4 (chosen=1)

        Each step inserts ``media_assets`` rows with a ``meta`` JSON
        describing its purpose. Only the final MP4 has ``chosen=1`` for
        ``kind='video'`` — that's the article-level video record the API
        looks up.
        """
        # ---- [1] load article + project + agents ----
        with db.connect(self.settings.db_path) as conn:
            article_row = conn.execute(
                "SELECT * FROM articles WHERE id=?", (article_id,)
            ).fetchone()
            if article_row is None:
                raise KeyError(article_id)
            article = db.row_to_dict(article_row) or {}
            project = _project(conn, article["project_id"])
            topic_row = conn.execute(
                "SELECT * FROM topics WHERE id=?", (article["topic_id"],)
            ).fetchone()
            topic = db.row_to_dict(topic_row) if topic_row else {}
            agents = _agents_by_role(conn, article["project_id"])
        lang = (article.get("language") or "ru").lower()
        # If the user disabled the corresponding video_<lang> team in
        # Settings, refuse to run. The article page in the UI hides the
        # "generate video" button in that case, so this is a defensive
        # check for direct API callers.
        enabled_teams = _enabled_teams_of(project)
        team_id = f"video_{lang}"
        if team_id not in enabled_teams:
            raise RuntimeError(
                f"video team for language={lang} is disabled in project "
                f"settings (enabled_teams={sorted(enabled_teams)})"
            )
        prid = self._start_pipeline_run(article["project_id"], "video")

        article_inputs = {
            "title_working": article.get("chosen_headline") or "",
            "body_md": article.get("body_full", ""),
            "tldr": (article.get("chosen_headline") or "")[:200],
        }

        # ---- [2] video_scenarist ----
        scenarist = (agents.get(("video_scenarist", lang))
                     or agents.get(("video_scenarist", "bi")))
        if not scenarist:
            raise RuntimeError(
                f"video_scenarist agent missing for language={lang}; "
                f"run soft-migration / re-seed the project."
            )
        # target_duration_s may have been stored as a string ("15"/"30"/"60")
        # because the UI uses a string-valued select; cast back to int here.
        sc_params = json.loads(scenarist["params"]) if isinstance(scenarist["params"], str) \
            else dict(scenarist["params"] or {})
        try:
            target_dur_int = int(str(sc_params.get("target_duration_s", 30)).strip())
        except (TypeError, ValueError):
            target_dur_int = 30
        sc_params["target_duration_s"] = target_dur_int
        scenarist_runtime = dict(scenarist)
        scenarist_runtime["params"] = json.dumps(sc_params)
        scenes_out = self.executor.run(
            agent=scenarist_runtime, pipeline_run_id=prid, article_id=article_id,
            inputs={"article": article_inputs, "topic": topic, "language": lang},
        ).output
        scenes = scenes_out.get("scenes", []) or []
        if not scenes:
            raise RuntimeError("video_scenarist returned no scenes")

        # ---- [3] video_keyframe_artist ----
        kf_agent = (agents.get(("video_keyframe_artist", "bi"))
                    or agents.get(("video_keyframe_artist", lang)))
        if not kf_agent:
            raise RuntimeError("video_keyframe_artist agent missing")
        kf_out = self.executor.run(
            agent=kf_agent, pipeline_run_id=prid, article_id=article_id,
            inputs={"scenes": scenes, "article": article_inputs, "language": lang},
        ).output
        keyframes = {int(k.get("idx", i + 1)): k
                     for i, k in enumerate(kf_out.get("keyframes", []) or [])}

        # Resolve the image model from the project's image_prompt_writer
        # (so the user's UI choice for project images is reused for keyframes).
        ipw = (agents.get(("image_prompt_writer", "bi"))
               or agents.get(("image_prompt_writer", lang)))
        ipw_params = (json.loads(ipw["params"])
                      if ipw and isinstance(ipw["params"], str)
                      else (ipw or {}).get("params") or {})
        image_model = ipw_params.get("image_model", "mock:placeholder") or "mock:placeholder"

        kf_params = json.loads(kf_agent["params"]) if isinstance(kf_agent["params"], str) \
            else dict(kf_agent["params"] or {})
        video_model = kf_params.get("video_model", "302ai:wan2.2-i2v")

        # ---- [4] image_gen per scene + [5] video_gen per scene ----
        scene_clips: list[dict[str, Any]] = []
        for i, scene in enumerate(scenes):
            idx = int(scene.get("idx", i + 1))
            duration = float(scene.get("duration_s") or 5.0)
            kf = keyframes.get(idx) or {}
            image_prompt = kf.get("image_prompt") or (
                f"cinematic still: {scene.get('b_roll_idea') or 'scene'}, aspect 9:16"
            )
            i2v_prompt = kf.get("i2v_prompt") or "slow zoom in"
            # [4] image
            img = generate_image(image_prompt, settings=self.settings,
                                  idx=idx, model=image_model)
            img_id = db.new_id("ma_")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "INSERT INTO media_assets (id, article_id, project_id, kind, "
                    "language, prompt, model, storage_url, mime, width, height, "
                    "chosen, meta, created_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        img_id, article_id, article["project_id"], "image",
                        lang, image_prompt, img.model, img.storage_url, img.mime,
                        img.width, img.height, 0,
                        db.jdump({"scene_idx": idx, "purpose": "video_keyframe",
                                   "topic_id": article.get("topic_id")}),
                        db.now_iso(),
                    ),
                )
            # [5] video clip
            clip = generate_video_clip(
                img.storage_url, i2v_prompt, settings=self.settings,
                idx=idx, model=video_model, duration_s=duration,
            )
            clip_id = db.new_id("ma_")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "INSERT INTO media_assets (id, article_id, project_id, kind, "
                    "language, prompt, model, storage_url, mime, width, height, "
                    "duration_s, chosen, meta, created_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        clip_id, article_id, article["project_id"], "video",
                        lang, i2v_prompt, clip.model, clip.storage_url, clip.mime,
                        clip.width, clip.height, clip.duration_s, 0,
                        db.jdump({"scene_idx": idx, "purpose": "video_scene_clip"}),
                        db.now_iso(),
                    ),
                )
            scene_clips.append({
                "idx": idx,
                "clip_url": clip.storage_url,
                "clip_path": os.path.join(self.settings.media_dir,
                                            os.path.basename(clip.storage_url)),
                "duration_s": duration,
                "voiceover": scene.get("voiceover", ""),
            })

        # ---- [6] voice_director ----
        vd_agent = (agents.get(("voice_director", lang))
                    or agents.get(("voice_director", "bi")))
        if not vd_agent:
            raise RuntimeError(f"voice_director agent missing for language={lang}")
        vd_out = self.executor.run(
            agent=vd_agent, pipeline_run_id=prid, article_id=article_id,
            inputs={"scenes": scenes, "language": lang},
        ).output
        normalized = {int(s.get("idx", i + 1)): s
                      for i, s in enumerate(vd_out.get("scenes_normalized", []) or [])}
        vd_params = json.loads(vd_agent["params"]) if isinstance(vd_agent["params"], str) \
            else dict(vd_agent["params"] or {})
        tts_model = vd_params.get("tts_model", "openai:gpt-4o-mini-tts")
        voice_id = vd_params.get("voice_id", "onyx")
        speed = float(vd_params.get("speed", 1.0) or 1.0)

        # ---- [7] tts_gen per scene ----
        for sc in scene_clips:
            norm = normalized.get(sc["idx"]) or {}
            text = (norm.get("voiceover_normalized")
                    or sc.get("voiceover")
                    or (f"Сцена {sc['idx']}." if lang == "ru" else f"Scene {sc['idx']}."))
            tts = generate_tts(text, settings=self.settings, idx=sc["idx"],
                                model=tts_model, voice_id=voice_id,
                                speed=speed, language=lang)
            tts_id = db.new_id("ma_")
            with db.connect(self.settings.db_path) as conn:
                conn.execute(
                    "INSERT INTO media_assets (id, article_id, project_id, kind, "
                    "language, prompt, model, storage_url, mime, "
                    "duration_s, chosen, meta, created_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        tts_id, article_id, article["project_id"], "audio",
                        lang, text, tts.model, tts.storage_url, tts.mime,
                        tts.duration_s, 0,
                        db.jdump({"scene_idx": sc["idx"], "purpose": "voiceover",
                                   "voice_id": tts.voice_id}),
                        db.now_iso(),
                    ),
                )
            sc["audio_url"] = tts.storage_url
            sc["audio_path"] = os.path.join(self.settings.media_dir,
                                              os.path.basename(tts.storage_url))

        # ---- [8] combine audio (concat all scene MP3s) + transcribe ----
        # Whisper sees the WHOLE voiceover as one stream so the resulting
        # SRT timestamps cover the full final video. Without concat we'd
        # only get subtitles for the first scene, and burn-in over the
        # concatenated MP4 would silently drop after that.
        from .tools.video_assembler import concat_audio_files
        scene_audio_paths = [sc.get("audio_path", "") for sc in scene_clips]
        combined_audio_path = concat_audio_files(
            scene_audio_paths,
            settings=self.settings,
            out_basename=f"{article_id}_{lang}_combined.mp3",
        ) or (scene_clips[0]["audio_path"] if scene_clips else "")
        # transcribe_audio expects a /media/<file> URL (it resolves to a
        # local file under settings.media_dir), so build that from the
        # absolute path concat_audio_files returned.
        combined_audio_url = (
            "/media/" + os.path.basename(combined_audio_path)
            if combined_audio_path else ""
        )
        srt = transcribe_audio(combined_audio_url, settings=self.settings, language=lang)

        # ---- [9] subtitle_styler -> ASS file ----
        ss_agent = (agents.get(("subtitle_styler", lang))
                    or agents.get(("subtitle_styler", "bi")))
        if not ss_agent:
            raise RuntimeError(f"subtitle_styler agent missing for language={lang}")
        ss_out = self.executor.run(
            agent=ss_agent, pipeline_run_id=prid, article_id=article_id,
            inputs={"srt_text": srt.srt_text, "language": lang},
        ).output
        ass_text = ss_out.get("ass_text") or ""
        ass_filename = f"{article_id}_{lang}.ass"
        ass_path = os.path.join(self.settings.media_dir, ass_filename)
        os.makedirs(self.settings.media_dir, exist_ok=True)
        with open(ass_path, "w", encoding="utf-8") as fh:
            fh.write(ass_text)
        ass_id = db.new_id("ma_")
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO media_assets (id, article_id, project_id, kind, "
                "language, prompt, model, storage_url, mime, "
                "chosen, meta, created_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ass_id, article_id, article["project_id"], "subtitle",
                    lang, "", "openai:whisper-1+subtitle_styler",
                    f"/media/{ass_filename}", "text/x-ass",
                    0,
                    db.jdump({"format": "ass", "purpose": "final_subtitles",
                               "srt_duration_s": srt.duration_s}),
                    db.now_iso(),
                ),
            )

        # ---- [10] video_assembler -> final MP4 ----
        scenes_for_assembler = [
            {
                "clip_path": sc["clip_path"],
                "audio_path": sc.get("audio_path", ""),
                "ass_path": ass_path,
                "duration_s": sc["duration_s"],
            }
            for sc in scene_clips
        ]
        final = assemble_video(
            scenes_for_assembler, settings=self.settings,
            article_id=article_id, language=lang,
        )
        final_id = db.new_id("ma_")
        with db.connect(self.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO media_assets (id, article_id, project_id, kind, "
                "language, prompt, model, storage_url, mime, width, height, "
                "duration_s, chosen, meta, created_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    final_id, article_id, article["project_id"], "video",
                    lang, "", video_model, final.storage_url, final.mime,
                    final.width, final.height, final.duration_s, 1,
                    db.jdump({"purpose": "final",
                               "scenes_count": len(scene_clips),
                               "aspect": self.VIDEO_ASPECT,
                               "topic_id": article.get("topic_id")}),
                    db.now_iso(),
                ),
            )
            conn.execute(
                "UPDATE pipeline_runs SET status='completed', finished_at=? WHERE id=?",
                (db.now_iso(), prid),
            )
        return {
            "final_video_url": final.storage_url,
            "scenes_count": len(scene_clips),
            "duration_s": final.duration_s,
            "media_asset_id": final_id,
        }


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
