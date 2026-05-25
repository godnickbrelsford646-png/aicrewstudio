"""HTTP API + static frontend, all on Python stdlib.

Resources are addressed by **slug**, not opaque ID:
  /api/projects/{project_slug}
  /api/projects/{project_slug}/agents/{agent_slug}
  /api/projects/{project_slug}/channels/{channel_slug}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import db
from .agents.registry import (
    default_agents_for_project,
    role_spec,
)
from .agents.param_schema import schema_payload
from .channels.registry import CHANNEL_KINDS, list_kinds
from .crypto import decrypt, encrypt, mask
from .pipeline import PipelineRunner
from .scheduler import (
    now_utc_iso,
    publish_one_post,
    start_scheduler,
)
from .settings import Settings, load_settings

log = logging.getLogger("aicrew.api")


Handler = Callable[["AicrewHandler", dict[str, str]], None]
_ROUTES: list[tuple[str, str, Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _ROUTES.append((method.upper(), pattern, fn))
        return fn

    return deco


def _match(pattern: str, path: str) -> dict[str, str] | None:
    p_parts = pattern.strip("/").split("/")
    a_parts = path.strip("/").split("/")
    if len(p_parts) != len(a_parts):
        return None
    out: dict[str, str] = {}
    for p, a in zip(p_parts, a_parts):
        if p.startswith("{") and p.endswith("}"):
            out[p[1:-1]] = a
        elif p != a:
            return None
    return out


# ---------------------------------------------------------------- helpers --

def _resolve_project(handler: "AicrewHandler", key: str) -> dict[str, Any] | None:
    """Look up a project by slug or fallback to id."""
    with db.connect(handler.settings.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE slug=? OR id=?", (key, key)
        ).fetchone()
    if not row:
        handler.send_json(404, {"error": "project not found"})
        return None
    return db.row_to_dict(row)


def _resolve_agent(handler: "AicrewHandler", project_id: str, key: str) -> dict[str, Any] | None:
    with db.connect(handler.settings.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE project_id=? AND (slug=? OR id=?)",
            (project_id, key, key),
        ).fetchone()
    if not row:
        handler.send_json(404, {"error": "agent not found"})
        return None
    return db.row_to_dict(row)


def _resolve_channel(handler: "AicrewHandler", project_id: str, key: str) -> dict[str, Any] | None:
    with db.connect(handler.settings.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE project_id=? AND (slug=? OR id=?)",
            (project_id, key, key),
        ).fetchone()
    if not row:
        handler.send_json(404, {"error": "channel not found"})
        return None
    return db.row_to_dict(row)


# ---------------------------------------------------------------- routes ---

@route("GET", "/api/health")
def _health(h: "AicrewHandler", _params: dict[str, str]) -> None:
    h.send_json(200, {"ok": True, "version": "0.2.0"})


@route("GET", "/api/projects")
def _list_projects(h: "AicrewHandler", _params: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        rows = db.rows_to_list(conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall())
    h.send_json(200, {"projects": rows})


@route("GET", "/api/projects/{pkey}")
def _get_project(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    pid = project["id"]
    with db.connect(h.settings.db_path) as conn:
        agents = db.rows_to_list(conn.execute(
            "SELECT * FROM agents WHERE project_id=? ORDER BY role, language", (pid,)
        ).fetchall())
        topics = db.rows_to_list(conn.execute(
            "SELECT * FROM topics WHERE project_id=? ORDER BY score_total DESC LIMIT 50",
            (pid,),
        ).fetchall())
        articles = db.rows_to_list(conn.execute(
            "SELECT * FROM articles WHERE project_id=? ORDER BY created_at DESC LIMIT 50",
            (pid,),
        ).fetchall())
        # Channels: we sort connected ones first so the user sees what's
        # already plugged in at the top of the Channels tab. We never expose
        # the encrypted blob to the browser — replace it with a boolean
        # is_connected flag.
        #
        # Critically, "is_connected" is NOT just "credentials_enc is non-empty":
        # seed.py writes an encrypted empty dict {} for every freshly created
        # channel (so the column is never NULL). We must decrypt and verify
        # that at least one real value is present.
        channels = db.rows_to_list(conn.execute(
            "SELECT * FROM channels WHERE project_id=? ORDER BY name",
            (pid,),
        ).fetchall())
        for ch in channels:
            enc = (ch.pop("credentials_enc", "") or "").strip()
            connected = False
            if enc:
                try:
                    raw = json.loads(decrypt(enc, h.settings.master_key))
                    connected = bool(raw) and any(
                        str(v).strip() for v in raw.values() if v is not None
                    )
                except Exception:
                    connected = False
            ch["is_connected"] = connected
        # Stable sort: connected first, then alphabetical by name.
        channels.sort(key=lambda c: (0 if c["is_connected"] else 1, c["name"]))
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY started_at DESC LIMIT 20",
            (pid,),
        ).fetchall())
    h.send_json(200, {
        "project": project, "agents": agents, "topics": topics,
        "articles": articles, "channels": channels, "pipeline_runs": runs,
    })


@route("PATCH", "/api/projects/{pkey}")
def _patch_project(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    body = h.read_json() or {}
    allowed = {"name", "niche", "description", "is_enabled", "timezone",
               "language_modes", "daily_topics_target", "daily_articles_target",
               "budget_usd_month", "style_guide"}
    sets = []
    args: list[Any] = []
    for k, v in body.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        if k == "language_modes" and isinstance(v, list):
            args.append(json.dumps(v))
        else:
            args.append(v)
    if not sets:
        h.send_json(400, {"error": "no editable fields"})
        return
    sets.append("updated_at=?")
    args.extend([db.now_iso(), project["id"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


@route("POST", "/api/projects/{pkey}/runs/topics")
def _run_topics(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    runner = PipelineRunner(h.settings)
    prid = runner.run_topic_phase(project["id"])
    h.send_json(202, {"pipeline_run_id": prid})


@route("POST", "/api/projects/{pkey}/runs/articles")
def _run_articles(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    body = h.read_json() or {}
    runner = PipelineRunner(h.settings)
    ids = runner.run_article_phase(project["id"], max_articles=body.get("max_articles"))
    h.send_json(202, {"article_ids": ids})


@route("POST", "/api/projects/{pkey}/runs/publish")
def _run_publish(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    body = h.read_json() or {}
    runner = PipelineRunner(h.settings)
    res = runner.run_publication_phase(project["id"], dry_run=bool(body.get("dry_run")))
    h.send_json(202, res)


@route("POST", "/api/projects/{pkey}/runs/full")
def _run_full(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    runner = PipelineRunner(h.settings)
    # Полный пайплайн через реальные API длится 1–3 минуты, поэтому
    # стартуем его в фоне и сразу отдаём 202 с pipeline_run_id.
    # Запись pipeline_run создаётся СРАЗУ (до старта потока), чтобы
    # клиент мог видеть её в списке запусков.
    prid = runner.create_pipeline_run(project["id"], kind="full")

    def _worker(pid: str, run_id: str, settings: Settings) -> None:
        try:
            # Каждый поток создаёт свой PipelineRunner, чтобы не делить
            # SQLite-соединения между потоками.
            local_runner = PipelineRunner(settings)
            local_runner.run_full(pid, pipeline_run_id=run_id)
        except Exception:
            log.exception("background run_full failed for project=%s run=%s", pid, run_id)

    threading.Thread(
        target=_worker,
        args=(project["id"], prid, h.settings),
        daemon=True,
        name=f"aicrew-runfull-{prid}",
    ).start()
    h.send_json(202, {"pipeline_run_id": prid, "status": "started"})


# ------------- agents ----------------------------------------------------

@route("GET", "/api/projects/{pkey}/agents/{akey}")
def _get_agent_by_slug(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    agent = _resolve_agent(h, project["id"], p["akey"])
    if agent is None:
        return
    with db.connect(h.settings.db_path) as conn:
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY started_at DESC LIMIT 50",
            (agent["id"],),
        ).fetchall())
    h.send_json(200, {
        "project": {"id": project["id"], "slug": project["slug"], "name": project["name"]},
        "agent": agent,
        "runs": runs,
        "param_schema": schema_payload(agent["role"]),
    })


@route("PATCH", "/api/projects/{pkey}/agents/{akey}")
def _patch_agent_by_slug(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    agent = _resolve_agent(h, project["id"], p["akey"])
    if agent is None:
        return
    body = h.read_json() or {}
    allowed = {"display_name", "description", "model", "temperature", "max_tokens",
               "top_p", "prompt_template", "params", "is_enabled"}
    sets = []
    args: list[Any] = []
    for k, v in body.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(json.dumps(v) if k == "params" and not isinstance(v, str) else v)
    if not sets:
        h.send_json(400, {"error": "no editable fields"})
        return
    sets.append("updated_at=?")
    args.extend([db.now_iso(), agent["id"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE agents SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


# ------------- channels --------------------------------------------------

@route("GET", "/api/channels/kinds")
def _list_kinds(h: "AicrewHandler", _: dict[str, str]) -> None:
    out = []
    for spec in list_kinds():
        out.append({
            "kind": spec.kind, "label": spec.label, "is_video": spec.is_video,
            "default_language": spec.default_language, "max_chars": spec.max_chars,
            "media_kinds": list(spec.media_kinds),
            "credentials_fields": [{"key": k, "hint": v} for k, v in spec.credentials_fields],
            "connect_guide_md": spec.connect_guide_md, "notes": spec.notes,
        })
    h.send_json(200, {"kinds": out})


def _kind_payload(kind: str) -> dict[str, Any]:
    spec = CHANNEL_KINDS[kind]
    return {
        "kind": spec.kind, "label": spec.label, "is_video": spec.is_video,
        "default_language": spec.default_language, "max_chars": spec.max_chars,
        "media_kinds": list(spec.media_kinds),
        "credentials_fields": [{"key": k, "hint": v} for k, v in spec.credentials_fields],
        "connect_guide_md": spec.connect_guide_md, "notes": spec.notes,
    }


@route("GET", "/api/projects/{pkey}/channels/{ckey}")
def _get_channel_by_slug(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    channel = _resolve_channel(h, project["id"], p["ckey"])
    if channel is None:
        return
    with db.connect(h.settings.db_path) as conn:
        slots = db.rows_to_list(conn.execute(
            "SELECT * FROM channel_slots WHERE channel_id=? ORDER BY time_local",
            (channel["id"],),
        ).fetchall())
        # The channel page only shows the channel-specific rewriter agent
        # (per the product decision: every channel has exactly one adapter,
        # the shared editorial/topic/media teams are visible from the
        # project's Agents tab and shouldn't be duplicated here).
        rewriter_id = channel.get("rewriter_agent_id") or ""
        rewriter_row = None
        if rewriter_id:
            rewriter_row = conn.execute(
                "SELECT id, slug, role, language, display_name, model, "
                "channel_id, is_enabled FROM agents WHERE id=?",
                (rewriter_id,),
            ).fetchone()
        if rewriter_row is None:
            # Fallback: an older project may not have rewriter_agent_id set,
            # but the rewriter still exists with channel_id=channel.id.
            rewriter_row = conn.execute(
                "SELECT id, slug, role, language, display_name, model, "
                "channel_id, is_enabled FROM agents "
                "WHERE project_id=? AND role='channel_rewriter' AND channel_id=?",
                (project["id"], channel["id"]),
            ).fetchone()
    rewriter_agent = db.row_to_dict(rewriter_row) if rewriter_row else None
    creds_masked: dict[str, str] = {}
    is_connected = False
    if channel.get("credentials_enc"):
        try:
            raw = json.loads(decrypt(channel["credentials_enc"], h.settings.master_key))
            creds_masked = {k: mask(str(v)) for k, v in raw.items()}
            # is_connected only when at least one real value is filled in;
            # encrypted empty {} (default seed state) does NOT count as
            # connected. Same logic as in _get_project.
            is_connected = bool(raw) and any(
                str(v).strip() for v in raw.values() if v is not None
            )
        except Exception:
            pass
    channel["credentials_masked"] = creds_masked
    channel["is_connected"] = is_connected
    channel.pop("credentials_enc", None)
    h.send_json(200, {
        "project": {"id": project["id"], "slug": project["slug"],
                    "name": project["name"],
                    # Surface timezone so the channel page can label slot
                    # times correctly ("local time of project, timezone=...").
                    "timezone": project.get("timezone") or "UTC"},
        "channel": channel, "slots": slots,
        "spec": _kind_payload(channel["kind"]),
        # Only the per-channel adapter is exposed here. None for video
        # channels (they have no rewriter yet).
        "rewriter_agent": rewriter_agent,
    })


@route("PATCH", "/api/projects/{pkey}/channels/{ckey}")
def _patch_channel_by_slug(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    channel = _resolve_channel(h, project["id"], p["ckey"])
    if channel is None:
        return
    body = h.read_json() or {}
    allowed = {"name", "language", "posts_per_day", "selection_strategy",
               "rewriter_prompt", "is_enabled"}
    sets = []
    args: list[Any] = []
    for k, v in body.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        h.send_json(400, {"error": "no editable fields"})
        return
    sets.append("updated_at=?")
    args.extend([db.now_iso(), channel["id"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE channels SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


@route("PATCH", "/api/projects/{pkey}/channels/{ckey}/credentials")
def _patch_creds_by_slug(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    channel = _resolve_channel(h, project["id"], p["ckey"])
    if channel is None:
        return
    body = h.read_json() or {}
    creds = body.get("credentials") or {}
    enc = encrypt(json.dumps(creds), h.settings.master_key)
    with db.connect(h.settings.db_path) as conn:
        conn.execute("UPDATE channels SET credentials_enc=?, updated_at=? WHERE id=?",
                     (enc, db.now_iso(), channel["id"]))
    h.send_json(200, {"ok": True})


@route("PUT", "/api/projects/{pkey}/channels/{ckey}/slots")
def _put_slots(h: "AicrewHandler", p: dict[str, str]) -> None:
    """Replace all schedule slots for a channel."""
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    channel = _resolve_channel(h, project["id"], p["ckey"])
    if channel is None:
        return
    body = h.read_json() or {}
    slots = body.get("slots") or []
    if not isinstance(slots, list):
        h.send_json(400, {"error": "slots must be a list of {time_local, enabled}"})
        return
    with db.connect(h.settings.db_path) as conn:
        conn.execute("DELETE FROM channel_slots WHERE channel_id=?", (channel["id"],))
        for s in slots:
            t = (s or {}).get("time_local") if isinstance(s, dict) else None
            if not t:
                continue
            conn.execute(
                "INSERT INTO channel_slots (id, channel_id, time_local, enabled) "
                "VALUES (?,?,?,?)",
                (db.new_id("cs_"), channel["id"], t,
                 1 if (s.get("enabled", True)) else 0),
            )
    h.send_json(200, {"ok": True})


# ------------- posts / articles ------------------------------------------

@route("GET", "/api/projects/{pkey}/posts")
def _list_posts(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    with db.connect(h.settings.db_path) as conn:
        rows = db.rows_to_list(conn.execute(
            "SELECT po.*, ch.name AS channel_name, ch.kind AS channel_kind, "
            "       ch.slug AS channel_slug, "
            "       a.chosen_headline AS article_headline, a.language AS language "
            "FROM posts po JOIN channels ch ON po.channel_id=ch.id "
            "JOIN articles a ON po.article_id=a.id "
            "WHERE ch.project_id=? "
            "ORDER BY po.created_at DESC LIMIT 200", (project["id"],),
        ).fetchall())
    h.send_json(200, {"posts": rows})


@route("POST", "/api/posts/{post_id}/publish_now")
def _publish_now(h: "AicrewHandler", p: dict[str, str]) -> None:
    """Publish an existing scheduled post immediately, ignoring its slot.

    Resets ``scheduled_for`` to now, zeros ``attempts`` (so the manual
    click gets a fresh retry budget — useful when a post has hit
    ``status='failed'`` after MAX_ATTEMPTS and the user wants to retry
    after fixing whatever caused the failure), then synchronously
    invokes the publisher.
    """
    post_id = p["post_id"]
    with db.connect(h.settings.db_path) as conn:
        row = conn.execute(
            "SELECT id, status FROM posts WHERE id=?", (post_id,),
        ).fetchone()
        if not row:
            h.send_json(404, {"error": "post not found"})
            return
        # Force the post into 'scheduled' state (it might be 'failed'
        # after MAX_ATTEMPTS) and reset attempts so the manual click
        # gets a fresh retry budget.
        conn.execute(
            "UPDATE posts SET status='scheduled', scheduled_for=?, "
            "attempts=0, last_attempt_at=NULL, error=NULL WHERE id=?",
            (now_utc_iso(), post_id),
        )
    ok = publish_one_post(post_id, h.settings)
    h.send_json(200, {"ok": ok})


@route("GET", "/api/projects/{pkey}/topics/{tid}")
def _get_topic(h: "AicrewHandler", p: dict[str, str]) -> None:
    """Topic detail page. Returns:
        topic         — full topic row (incl. event_date, scores, sources)
        articles      — articles already written from this topic (per language)
        agent_runs    — agent runs that touched this topic specifically
                        (researcher / research_validator / article_writer /
                        headline_writer / qa_editorial / qa_visual /
                        image_prompt_writer; their topic_id is set in
                        executor.run() calls).
        topic_phase_runs — runs from the SAME pipeline_run that produced
                        the topic (topic_generator / topic_validator /
                        topic_ranker). Their topic_id is NULL because
                        they operate on batches, but their input/output
                        contains this topic title in JSON. We surface them
                        so the user can see what the search agents
                        collected and how the ranker scored it.
    """
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    with db.connect(h.settings.db_path) as conn:
        topic = conn.execute(
            "SELECT * FROM topics WHERE id=? AND project_id=?",
            (p["tid"], project["id"]),
        ).fetchone()
        if not topic:
            h.send_json(404, {"error": "topic not found"})
            return
        topic = db.row_to_dict(topic)
        articles = db.rows_to_list(conn.execute(
            "SELECT id, language, status, chosen_headline, chosen_image_id, "
            "qa_score, created_at, written_at "
            "FROM articles WHERE topic_id=? ORDER BY language",
            (topic["id"],),
        ).fetchall())
        # agent_runs scoped to this topic (per-topic agents).
        runs = db.rows_to_list(conn.execute(
            "SELECT ar.*, a.role AS agent_role, a.language AS agent_language, "
            "a.display_name AS agent_display_name "
            "FROM agent_runs ar LEFT JOIN agents a ON a.id=ar.agent_id "
            "WHERE ar.topic_id=? ORDER BY ar.started_at",
            (topic["id"],),
        ).fetchall())
        # Topic-phase runs come from the same pipeline_run that produced the
        # topic. They have topic_id NULL but their inputs/outputs reference
        # this topic by title in JSON.
        phase_runs: list[dict[str, Any]] = []
        if topic.get("pipeline_run_id"):
            phase_runs = db.rows_to_list(conn.execute(
                "SELECT ar.*, a.role AS agent_role, a.language AS agent_language, "
                "a.display_name AS agent_display_name "
                "FROM agent_runs ar LEFT JOIN agents a ON a.id=ar.agent_id "
                "WHERE ar.pipeline_run_id=? AND ar.topic_id IS NULL "
                "AND a.role IN ('topic_generator','topic_validator','topic_ranker') "
                "ORDER BY ar.started_at",
                (topic["pipeline_run_id"],),
            ).fetchall())
    h.send_json(200, {
        "project": {"id": project["id"], "slug": project["slug"], "name": project["name"]},
        "topic": topic,
        "articles": articles,
        "agent_runs": runs,
        "topic_phase_runs": phase_runs,
    })


@route("GET", "/api/projects/{pkey}/articles/{aid}")
def _get_article(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    with db.connect(h.settings.db_path) as conn:
        article = conn.execute(
            "SELECT * FROM articles WHERE id=? AND project_id=?",
            (p["aid"], project["id"]),
        ).fetchone()
        if not article:
            h.send_json(404, {"error": "article not found"})
            return
        article = db.row_to_dict(article)
        # Media is generated ONCE per topic and stored in media_assets with
        # FK pointing to the FIRST article of the topic (usually RU). All
        # sibling articles of the same topic share the same chosen_image_id
        # but have no rows of their own in media_assets — see
        # PipelineRunner._generate_topic_image. So we look up media by
        # topic, not by article_id, otherwise the EN article page renders
        # an empty gallery even though the image is correctly attached.
        media = db.rows_to_list(conn.execute(
            "SELECT * FROM media_assets "
            "WHERE article_id IN (SELECT id FROM articles WHERE topic_id=?) "
            "ORDER BY created_at",
            (article["topic_id"],),
        ).fetchall())
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM agent_runs WHERE article_id=? ORDER BY started_at", (p["aid"],)
        ).fetchall())
        topic = conn.execute(
            "SELECT * FROM topics WHERE id=?", (article["topic_id"],)
        ).fetchone()
        # Channels of this project that match the article's language.
        # The article page uses this to render a "publish to channel"
        # panel — we only show channels with matching language because
        # a Russian-only article has nothing useful to send to an
        # English Telegram channel.
        project_channels = db.rows_to_list(conn.execute(
            "SELECT id, slug, kind, name, language, is_video, credentials_enc "
            "FROM channels WHERE project_id=? AND is_enabled=1 AND language=? "
            "ORDER BY name",
            (project["id"], article["language"]),
        ).fetchall())
        # Existing posts for this article across all channels. UI uses
        # this to show "уже опубликовано / запланировано / упало" inline
        # next to each channel button instead of the bare "Опубликовать
        # сейчас" CTA.
        art_posts = db.rows_to_list(conn.execute(
            "SELECT po.id, po.channel_id, po.status, po.scheduled_for, "
            "po.published_at, po.external_url, po.error, po.attempts, "
            "ch.name AS channel_name, ch.kind AS channel_kind, "
            "ch.slug AS channel_slug "
            "FROM posts po JOIN channels ch ON ch.id=po.channel_id "
            "WHERE po.article_id=?",
            (p["aid"],),
        ).fetchall())
    # Flag connected channels and strip the encrypted blob — same logic
    # as _get_project, kept duplicated rather than factored out so the
    # surface for accidental leaks of credentials_enc to the wire stays
    # very small and grepable.
    for ch in project_channels:
        enc = (ch.pop("credentials_enc", "") or "").strip()
        connected = False
        if enc:
            try:
                raw = json.loads(decrypt(enc, h.settings.master_key))
                connected = bool(raw) and any(
                    str(v).strip() for v in raw.values() if v is not None
                )
            except Exception:
                connected = False
        ch["is_connected"] = connected
    # Final video lookup: by product decision, the only kind='video' row
    # with chosen=1 is the assembled, final clip. Per-scene clips have
    # chosen=0 (see PipelineRunner.run_video_phase). If nothing matches,
    # the article hasn't been turned into a video yet and we return null.
    video_payload: dict[str, Any] | None = None
    with db.connect(h.settings.db_path) as conn:
        vrow = conn.execute(
            "SELECT * FROM media_assets WHERE article_id=? AND kind='video' "
            "AND chosen=1 ORDER BY created_at DESC LIMIT 1",
            (p["aid"],),
        ).fetchone()
    if vrow is not None:
        v = db.row_to_dict(vrow) or {}
        meta = db.jload(v.get("meta"), {}) or {}
        video_payload = {
            "url": v.get("storage_url"),
            "duration_s": v.get("duration_s") or 0,
            "scenes_count": int(meta.get("scenes_count") or 0),
            "created_at": v.get("created_at"),
        }
    h.send_json(200, {
        "project": {"id": project["id"], "slug": project["slug"], "name": project["name"]},
        "article": article, "media": media, "agent_runs": runs,
        "topic": db.row_to_dict(topic),
        "channels": project_channels, "posts": art_posts,
        "video": video_payload,
    })


@route("POST", "/api/projects/{pkey}/articles/{aid}/generate_video")
def _generate_video(h: "AicrewHandler", p: dict[str, str]) -> None:
    """Synchronously run the video phase for one article and return the
    final MP4 URL. In mock mode this writes ~12 placeholder media files
    in <media_dir>; in real mode it would talk to 302.ai / OpenAI / ffmpeg.
    """
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    aid = p["aid"]
    with db.connect(h.settings.db_path) as conn:
        row = conn.execute(
            "SELECT id, project_id FROM articles WHERE id=? AND project_id=?",
            (aid, project["id"]),
        ).fetchone()
    if row is None:
        h.send_json(404, {"error": "article not found"})
        return
    runner = PipelineRunner(h.settings)
    try:
        result = runner.run_video_phase(aid)
    except Exception as exc:
        log.exception("run_video_phase failed for article=%s", aid)
        h.send_json(500, {"error": repr(exc)})
        return
    h.send_json(200, {
        "final_video_url": result["final_video_url"],
        "duration_s": result["duration_s"],
        "scenes_count": result["scenes_count"],
        "media_asset_id": result["media_asset_id"],
    })


@route("POST", "/api/projects/{pkey}/articles/{aid}/publish/{ckey}")
def _publish_article_to_channel(h: "AicrewHandler", p: dict[str, str]) -> None:
    """One-click "publish this article to this channel right now".

    Used from the article detail page where the user sees a list of
    connected channels and clicks "Опубликовать в Telegram". The flow:

      1. Resolve project + channel + article, ensure the channel has
         real credentials (avoids creating a post that will instantly
         fail on the first tick).
      2. Find or create the (article, channel) post. If new, run the
         channel rewriter once so ``posts.body`` holds the
         channel-adapted text — same path the slot-aware scheduler
         takes, just compressed into one synchronous call.
      3. Force ``scheduled_for=now`` and synchronously fire
         publish_one_post.

    Idempotent: a second click won't create a duplicate post (UNIQUE
    constraint on (article_id, channel_id)) — it just resets the row
    and retries, which is exactly what the user expects when the first
    attempt failed.
    """
    project = _resolve_project(h, p["pkey"])
    if project is None:
        return
    channel = _resolve_channel(h, project["id"], p["ckey"])
    if channel is None:
        return
    # Verify the channel actually has credentials. Same is_connected
    # check as _get_project / _get_article — see those for rationale
    # (seed.py writes encrypted empty dicts for fresh channels).
    enc = (channel.get("credentials_enc") or "").strip()
    is_connected = False
    if enc:
        try:
            raw = json.loads(decrypt(enc, h.settings.master_key))
            is_connected = bool(raw) and any(
                str(v).strip() for v in raw.values() if v is not None
            )
        except Exception:
            is_connected = False
    if not is_connected:
        h.send_json(400, {"error": "channel is not connected (no credentials)"})
        return
    with db.connect(h.settings.db_path) as conn:
        article_row = conn.execute(
            "SELECT * FROM articles WHERE id=? AND project_id=?",
            (p["aid"], project["id"]),
        ).fetchone()
        if not article_row:
            h.send_json(404, {"error": "article not found"})
            return
        article = db.row_to_dict(article_row)
        existing_post = conn.execute(
            "SELECT id FROM posts WHERE article_id=? AND channel_id=?",
            (p["aid"], channel["id"]),
        ).fetchone()
    runner = PipelineRunner(h.settings)
    spec = CHANNEL_KINDS[channel["kind"]]
    if existing_post:
        post_id = existing_post["id"]
        # Reset the existing post so the user can re-publish (e.g. if
        # the previous attempt failed). We do NOT re-run the rewriter
        # here on the assumption the existing body is good; if the user
        # wants a fresh rewrite they can delete the post first.
        with db.connect(h.settings.db_path) as conn:
            conn.execute(
                "UPDATE posts SET status='scheduled', scheduled_for=?, "
                "attempts=0, last_attempt_at=NULL, error=NULL WHERE id=?",
                (now_utc_iso(), post_id),
            )
    else:
        # Fresh post: run the channel rewriter so body is adapted to
        # the channel format (Telegram tone is different from VK tone,
        # and definitely different from a tweet's char limit).
        with db.connect(h.settings.db_path) as conn:
            agents: dict[tuple[str, str], dict[str, Any]] = {}
            for r in conn.execute(
                "SELECT * FROM agents WHERE project_id=? AND is_enabled=1",
                (project["id"],),
            ).fetchall():
                d = db.row_to_dict(r) or {}
                agents[(d["role"], d.get("language") or "bi")] = d
        prid = runner.create_pipeline_run(project["id"], kind="publication")
        if spec.is_video:
            # Video channels don't have a text rewriter today — just
            # carry the headline. Real video rendering is a separate
            # feature (see docs/07-video-team.md).
            body, headline, image_id = "", article["chosen_headline"], None
        else:
            body, headline, image_id = runner._rewrite_for_channel(
                prid, agents, channel, spec, article)
        post_id = db.new_id("po_")
        with db.connect(h.settings.db_path) as conn:
            conn.execute(
                "INSERT INTO posts (id, article_id, channel_id, body, headline, "
                "image_asset_id, status, scheduled_for, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (post_id, p["aid"], channel["id"], body, headline, image_id,
                 "scheduled", now_utc_iso(), db.now_iso()),
            )
            conn.execute(
                "UPDATE pipeline_runs SET status='completed', finished_at=? WHERE id=?",
                (db.now_iso(), prid),
            )
    ok = publish_one_post(post_id, h.settings)
    h.send_json(200, {"ok": ok, "post_id": post_id})


# --------------------------------------------------------------- handler ---

class AicrewHandler(BaseHTTPRequestHandler):
    server_version = "AiCrewStudio/0.2"
    settings: Settings

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), format % args)

    def _set_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/") and not path.startswith("/media/"):
            self._serve_static(path)
            return
        if path.startswith("/media/"):
            self._serve_media(path)
            return
        for m, pattern, fn in _ROUTES:
            if m != method:
                continue
            params = _match(pattern, path)
            if params is not None:
                try:
                    fn(self, params)
                except Exception as exc:
                    log.exception("api error")
                    self.send_json(500, {"error": repr(exc)})
                return
        self.send_json(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # static --------------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        web_dir = os.path.join(os.path.dirname(__file__), "web")
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = os.path.normpath(os.path.join(web_dir, rel))
        if not target.startswith(web_dir) or not os.path.isfile(target):
            target = os.path.join(web_dir, "index.html")
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png", ".svg": "image/svg+xml",
        }.get(os.path.splitext(target)[1], "application/octet-stream")
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self._set_cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_media(self, path: str) -> None:
        rel = path[len("/media/") :]
        media_dir = os.path.abspath(self.settings.media_dir)
        target = os.path.normpath(os.path.join(media_dir, rel))
        if not target.startswith(media_dir) or not os.path.isfile(target):
            self.send_json(404, {"error": "media not found"})
            return
        with open(target, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self._set_cors()
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _ensure_default_agents_for_existing_projects(settings: Settings) -> None:
    """Add missing default agents to existing projects after a code upgrade.

    Walks every project in the DB and compares its current
    ``(role, language)`` agent set against the expected set from
    ``default_agents_for_project(language_modes)``. Inserts only the missing
    ones with their registry defaults; never modifies or deletes existing
    rows. This is what lets a user run ``git pull && systemctl restart``
    and pick up new roles (e.g. the video team) automatically.

    Called from ``serve()``. Idempotent — re-running adds nothing once the
    project is up to date.
    """
    with db.connect(settings.db_path) as conn:
        projects = db.rows_to_list(conn.execute(
            "SELECT id, name, language_modes FROM projects"
        ).fetchall())
    for proj in projects:
        try:
            languages = json.loads(proj.get("language_modes") or '["ru"]')
            if not isinstance(languages, list) or not languages:
                languages = ["ru"]
        except json.JSONDecodeError:
            languages = ["ru"]
        with db.connect(settings.db_path) as conn:
            existing_rows = conn.execute(
                "SELECT role, language FROM agents WHERE project_id=?",
                (proj["id"],),
            ).fetchall()
            existing = {(r["role"], r["language"] or "bi") for r in existing_rows}
            added = 0
            for entry in default_agents_for_project(languages):
                key = (entry["role"], entry["language"])
                if key in existing:
                    continue
                spec = entry["spec"]
                base_slug = entry["role"].replace("_", "-")
                if entry["language"] in ("ru", "en"):
                    base_slug += "-" + entry["language"]
                slug = db.unique_slug(
                    conn, "agents", base_slug,
                    scope_col="project_id", scope_val=proj["id"],
                )
                conn.execute(
                    "INSERT INTO agents (id, project_id, slug, role, "
                    "display_name, description, model, temperature, max_tokens, "
                    "top_p, prompt_template, params, tools_enabled, language, "
                    "is_enabled, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        db.new_id("ag_"), proj["id"], slug, entry["role"],
                        entry["display_name"], spec.description,
                        spec.default_model, spec.default_temperature,
                        spec.default_max_tokens, 1.0, spec.prompt_template,
                        json.dumps(spec.default_params),
                        json.dumps(list(spec.tools)),
                        entry["language"], 1, db.now_iso(), db.now_iso(),
                    ),
                )
                added += 1
        if added:
            log.info("ensuring video team for project %s: added %d new agents",
                     proj["id"], added)


def serve(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    AicrewHandler.settings = settings
    # Critical: ensure schema is up to date before the scheduler thread
    # starts touching `posts`. The scheduler reads attempts/last_attempt_at,
    # which are added by soft-migration in init_schema; without this call
    # an old DB created before the scheduler existed would crash the very
    # first tick. Cheap to call when the schema is already current.
    db.init_schema(settings.db_path)
    # Soft-migrate agents: existing projects need to pick up new roles
    # introduced by `git pull` (e.g. the video team — video_keyframe_artist,
    # voice_director, subtitle_styler, video_assembler) without forcing
    # the user to re-seed and lose their data. We only INSERT missing rows;
    # existing agents are never touched.
    _ensure_default_agents_for_existing_projects(settings)
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), AicrewHandler)
    log.warning("serving on http://0.0.0.0:%d (db=%s, llm=%s)",
                settings.port, settings.db_path, settings.llm_provider)
    # Background publication scheduler. Daemon thread, idempotent across
    # double-calls, ticks every TICK_SECONDS picking due posts and
    # publishing them through the channel adapter. Started here (not at
    # module import) so unit tests that import api.py do not spin up a
    # background thread that would race with their tearDown.
    start_scheduler(settings)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


_thread_local = threading.local()
