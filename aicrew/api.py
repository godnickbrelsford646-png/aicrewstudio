"""HTTP API + static frontend, all on Python stdlib.

This is intentionally minimal: only the endpoints the UI needs. Routes match
what a FastAPI version would expose, so the React frontend can be ported with
a different fetch base URL.
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
from .agents.registry import role_spec
from .channels.registry import CHANNEL_KINDS, list_kinds
from .crypto import decrypt, encrypt, mask
from .pipeline import PipelineRunner
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

def _project_or_404(handler: "AicrewHandler", project_id: str) -> dict[str, Any] | None:
    with db.connect(handler.settings.db_path) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        handler.send_json(404, {"error": "project not found"})
        return None
    return db.row_to_dict(row)


def _agent_or_404(handler: "AicrewHandler", agent_id: str) -> dict[str, Any] | None:
    with db.connect(handler.settings.db_path) as conn:
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        handler.send_json(404, {"error": "agent not found"})
        return None
    return db.row_to_dict(row)


# ---------------------------------------------------------------- routes ---

@route("GET", "/api/health")
def _health(h: "AicrewHandler", _params: dict[str, str]) -> None:
    h.send_json(200, {"ok": True, "version": "0.1.0"})


@route("GET", "/api/projects")
def _list_projects(h: "AicrewHandler", _params: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        rows = db.rows_to_list(conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall())
    h.send_json(200, {"projects": rows})


@route("GET", "/api/projects/{pid}")
def _get_project(h: "AicrewHandler", p: dict[str, str]) -> None:
    project = _project_or_404(h, p["pid"])
    if project is None:
        return
    with db.connect(h.settings.db_path) as conn:
        agents = db.rows_to_list(conn.execute(
            "SELECT * FROM agents WHERE project_id=? ORDER BY role, language", (p["pid"],)
        ).fetchall())
        topics = db.rows_to_list(conn.execute(
            "SELECT * FROM topics WHERE project_id=? ORDER BY score_total DESC LIMIT 50",
            (p["pid"],),
        ).fetchall())
        articles = db.rows_to_list(conn.execute(
            "SELECT * FROM articles WHERE project_id=? ORDER BY created_at DESC LIMIT 50",
            (p["pid"],),
        ).fetchall())
        channels = db.rows_to_list(conn.execute(
            "SELECT * FROM channels WHERE project_id=? ORDER BY name", (p["pid"],)
        ).fetchall())
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM pipeline_runs WHERE project_id=? ORDER BY started_at DESC LIMIT 20",
            (p["pid"],),
        ).fetchall())
    h.send_json(200, {
        "project": project, "agents": agents, "topics": topics,
        "articles": articles, "channels": channels, "pipeline_runs": runs,
    })


@route("PATCH", "/api/projects/{pid}")
def _patch_project(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _project_or_404(h, p["pid"]) is None:
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
    args.extend([db.now_iso(), p["pid"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE projects SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


@route("PATCH", "/api/channels/{cid}")
def _patch_channel(h: "AicrewHandler", p: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        row = conn.execute("SELECT * FROM channels WHERE id=?", (p["cid"],)).fetchone()
        if not row:
            h.send_json(404, {"error": "channel not found"})
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
    args.extend([db.now_iso(), p["cid"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE channels SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


@route("POST", "/api/projects/{pid}/runs/topics")
def _run_topics(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _project_or_404(h, p["pid"]) is None:
        return
    runner = PipelineRunner(h.settings)
    prid = runner.run_topic_phase(p["pid"])
    h.send_json(202, {"pipeline_run_id": prid})


@route("POST", "/api/projects/{pid}/runs/articles")
def _run_articles(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _project_or_404(h, p["pid"]) is None:
        return
    body = h.read_json() or {}
    runner = PipelineRunner(h.settings)
    ids = runner.run_article_phase(p["pid"], max_articles=body.get("max_articles"))
    h.send_json(202, {"article_ids": ids})


@route("POST", "/api/projects/{pid}/runs/publish")
def _run_publish(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _project_or_404(h, p["pid"]) is None:
        return
    body = h.read_json() or {}
    runner = PipelineRunner(h.settings)
    res = runner.run_publication_phase(p["pid"], dry_run=bool(body.get("dry_run")))
    h.send_json(202, res)


@route("POST", "/api/projects/{pid}/runs/full")
def _run_full(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _project_or_404(h, p["pid"]) is None:
        return
    runner = PipelineRunner(h.settings)
    s = runner.run_full(p["pid"])
    h.send_json(200, s.__dict__)


@route("GET", "/api/agents/{aid}")
def _get_agent(h: "AicrewHandler", p: dict[str, str]) -> None:
    agent = _agent_or_404(h, p["aid"])
    if agent is None:
        return
    with db.connect(h.settings.db_path) as conn:
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM agent_runs WHERE agent_id=? ORDER BY started_at DESC LIMIT 50",
            (p["aid"],),
        ).fetchall())
    h.send_json(200, {"agent": agent, "runs": runs})


@route("PATCH", "/api/agents/{aid}")
def _patch_agent(h: "AicrewHandler", p: dict[str, str]) -> None:
    if _agent_or_404(h, p["aid"]) is None:
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
    args.extend([db.now_iso(), p["aid"]])
    with db.connect(h.settings.db_path) as conn:
        conn.execute(f"UPDATE agents SET {','.join(sets)} WHERE id=?", args)
    h.send_json(200, {"ok": True})


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


@route("GET", "/api/channels/{cid}")
def _get_channel(h: "AicrewHandler", p: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        row = conn.execute("SELECT * FROM channels WHERE id=?", (p["cid"],)).fetchone()
        if not row:
            h.send_json(404, {"error": "channel not found"})
            return
        channel = db.row_to_dict(row)
        slots = db.rows_to_list(conn.execute(
            "SELECT * FROM channel_slots WHERE channel_id=? ORDER BY time_local",
            (p["cid"],),
        ).fetchall())
    creds_masked: dict[str, str] = {}
    if channel.get("credentials_enc"):
        try:
            raw = json.loads(decrypt(channel["credentials_enc"], h.settings.master_key))
            creds_masked = {k: mask(str(v)) for k, v in raw.items()}
        except Exception:
            pass
    channel["credentials_masked"] = creds_masked
    channel.pop("credentials_enc", None)
    h.send_json(200, {"channel": channel, "slots": slots,
                      "spec": _kind_payload(channel["kind"])})


def _kind_payload(kind: str) -> dict[str, Any]:
    spec = CHANNEL_KINDS[kind]
    return {
        "kind": spec.kind, "label": spec.label, "is_video": spec.is_video,
        "default_language": spec.default_language, "max_chars": spec.max_chars,
        "media_kinds": list(spec.media_kinds),
        "credentials_fields": [{"key": k, "hint": v} for k, v in spec.credentials_fields],
        "connect_guide_md": spec.connect_guide_md, "notes": spec.notes,
    }


@route("PATCH", "/api/channels/{cid}/credentials")
def _patch_creds(h: "AicrewHandler", p: dict[str, str]) -> None:
    body = h.read_json() or {}
    creds = body.get("credentials") or {}
    enc = encrypt(json.dumps(creds), h.settings.master_key)
    with db.connect(h.settings.db_path) as conn:
        conn.execute("UPDATE channels SET credentials_enc=?, updated_at=? WHERE id=?",
                     (enc, db.now_iso(), p["cid"]))
    h.send_json(200, {"ok": True})


@route("GET", "/api/projects/{pid}/posts")
def _list_posts(h: "AicrewHandler", p: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        rows = db.rows_to_list(conn.execute(
            "SELECT po.*, ch.name AS channel_name, ch.kind AS channel_kind, "
            "       a.chosen_headline AS article_headline, a.language AS language "
            "FROM posts po JOIN channels ch ON po.channel_id=ch.id "
            "JOIN articles a ON po.article_id=a.id "
            "WHERE ch.project_id=? "
            "ORDER BY po.created_at DESC LIMIT 200", (p["pid"],),
        ).fetchall())
    h.send_json(200, {"posts": rows})


@route("GET", "/api/projects/{pid}/articles/{aid}")
def _get_article(h: "AicrewHandler", p: dict[str, str]) -> None:
    with db.connect(h.settings.db_path) as conn:
        article = conn.execute(
            "SELECT * FROM articles WHERE id=? AND project_id=?",
            (p["aid"], p["pid"]),
        ).fetchone()
        if not article:
            h.send_json(404, {"error": "article not found"})
            return
        article = db.row_to_dict(article)
        media = db.rows_to_list(conn.execute(
            "SELECT * FROM media_assets WHERE article_id=?", (p["aid"],)
        ).fetchall())
        runs = db.rows_to_list(conn.execute(
            "SELECT * FROM agent_runs WHERE article_id=? ORDER BY started_at", (p["aid"],)
        ).fetchall())
        topic = conn.execute(
            "SELECT * FROM topics WHERE id=?", (article["topic_id"],)
        ).fetchone()
    h.send_json(200, {
        "article": article, "media": media, "agent_runs": runs,
        "topic": db.row_to_dict(topic),
    })


# --------------------------------------------------------------- handler ---

class AicrewHandler(BaseHTTPRequestHandler):
    server_version = "AiCrewStudio/0.1"
    settings: Settings

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), format % args)

    def _set_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")

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
        # static frontend
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


def serve(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    AicrewHandler.settings = settings
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), AicrewHandler)
    log.warning("serving on http://0.0.0.0:%d (db=%s, llm=%s)",
                settings.port, settings.db_path, settings.llm_provider)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


# --------------------------------------------------------------- threads ---

# Keep a per-server thread-local so handlers can access settings via class var.
_thread_local = threading.local()
