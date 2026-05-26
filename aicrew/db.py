"""SQLite layer. Schema mirrors docs/03-data-model.md.

Designed to be replaced by SQLAlchemy + Postgres+pgvector in production. The
public functions take primitive types so swapping the implementation is
straight-forward.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

_LOCK = threading.RLock()


SCHEMA = r"""
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    plan         TEXT NOT NULL DEFAULT 'free',
    status       TEXT NOT NULL DEFAULT 'inactive',
    valid_until  TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL REFERENCES users(id),
    slug                     TEXT NOT NULL UNIQUE DEFAULT '',
    name                     TEXT NOT NULL,
    niche                    TEXT NOT NULL DEFAULT '',
    description              TEXT NOT NULL DEFAULT '',
    is_enabled               INTEGER NOT NULL DEFAULT 0,
    enabled_at               TEXT,
    timezone                 TEXT NOT NULL DEFAULT 'UTC',
    language_modes           TEXT NOT NULL DEFAULT '["ru","en"]',
    daily_topics_target      INTEGER NOT NULL DEFAULT 5,
    daily_articles_target    INTEGER NOT NULL DEFAULT 2,
    budget_usd_month         REAL NOT NULL DEFAULT 50.0,
    style_guide              TEXT NOT NULL DEFAULT '',
    enabled_teams            TEXT NOT NULL DEFAULT '["text_ru","text_en","video_ru","video_en"]',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug              TEXT NOT NULL DEFAULT '',
    role              TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT 'openai:gpt-4o-mini',
    temperature       REAL NOT NULL DEFAULT 0.7,
    max_tokens        INTEGER NOT NULL DEFAULT 2000,
    top_p             REAL NOT NULL DEFAULT 1.0,
    prompt_template   TEXT NOT NULL,
    params            TEXT NOT NULL DEFAULT '{}',
    tools_enabled     TEXT NOT NULL DEFAULT '[]',
    language          TEXT NOT NULL DEFAULT 'bi',
    channel_id        TEXT,
    is_enabled        INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_project_role ON agents(project_id, role);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_project_slug ON agents(project_id, slug);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'full',  -- topics|articles|publication|full
    triggered_by  TEXT NOT NULL DEFAULT 'manual',
    status        TEXT NOT NULL DEFAULT 'queued',
    started_at    TEXT,
    finished_at   TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id                  TEXT PRIMARY KEY,
    pipeline_run_id     TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    agent_id            TEXT NOT NULL REFERENCES agents(id),
    parent_id           TEXT REFERENCES agent_runs(id),
    status              TEXT NOT NULL DEFAULT 'queued',
    inputs              TEXT NOT NULL DEFAULT '{}',
    output              TEXT NOT NULL DEFAULT '{}',
    rendered_prompt     TEXT NOT NULL DEFAULT '',
    cost_usd            REAL NOT NULL DEFAULT 0,
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT,
    finished_at         TEXT,
    attempt             INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    topic_id            TEXT,
    article_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_pipeline ON agent_runs(pipeline_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id, started_at);

CREATE TABLE IF NOT EXISTS llm_calls (
    id            TEXT PRIMARY KEY,
    agent_run_id  TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    model         TEXT NOT NULL,
    request       TEXT NOT NULL,
    response      TEXT NOT NULL,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pipeline_run_id TEXT,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    sources         TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'generated',
    score_total     REAL NOT NULL DEFAULT 0,
    scores          TEXT NOT NULL DEFAULT '{}',
    fingerprint     TEXT NOT NULL,
    event_date      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_project_status ON topics(project_id, status);
CREATE INDEX IF NOT EXISTS idx_topics_fingerprint ON topics(project_id, fingerprint);

CREATE TABLE IF NOT EXISTS articles (
    id                  TEXT PRIMARY KEY,
    topic_id            TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    project_id          TEXT NOT NULL,
    language            TEXT NOT NULL,
    research_brief      TEXT NOT NULL DEFAULT '',
    research_validated  TEXT NOT NULL DEFAULT '',
    body_full           TEXT NOT NULL DEFAULT '',
    headlines           TEXT NOT NULL DEFAULT '[]',
    chosen_headline     TEXT,
    chosen_image_id     TEXT,
    qa_score            INTEGER NOT NULL DEFAULT 0,
    qa_notes            TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'drafting',
    created_at          TEXT NOT NULL,
    written_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_project ON articles(project_id, language, status);

CREATE TABLE IF NOT EXISTS media_assets (
    id            TEXT PRIMARY KEY,
    article_id    TEXT REFERENCES articles(id) ON DELETE CASCADE,
    project_id    TEXT NOT NULL,
    kind          TEXT NOT NULL, -- image|video|audio|subtitle
    language      TEXT,
    prompt        TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    storage_url   TEXT NOT NULL,
    mime          TEXT NOT NULL DEFAULT '',
    width         INTEGER,
    height        INTEGER,
    duration_s    REAL,
    chosen        INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    meta          TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug                 TEXT NOT NULL DEFAULT '',
    kind                 TEXT NOT NULL,
    name                 TEXT NOT NULL,
    language             TEXT NOT NULL DEFAULT 'ru',
    is_video             INTEGER NOT NULL DEFAULT 0,
    posts_per_day        INTEGER NOT NULL DEFAULT 1,
    selection_strategy   TEXT NOT NULL DEFAULT 'by_rank',
    rewriter_prompt      TEXT NOT NULL DEFAULT '',
    rewriter_agent_id    TEXT,
    credentials_enc      TEXT NOT NULL DEFAULT '',
    is_enabled           INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_slots (
    id           TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    time_local   TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    article_id      TEXT REFERENCES articles(id) ON DELETE CASCADE,
    channel_id      TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    body            TEXT NOT NULL DEFAULT '',
    headline        TEXT,
    image_asset_id  TEXT,
    video_asset_id  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    scheduled_for   TEXT,
    published_at    TEXT,
    external_url    TEXT,
    error           TEXT,
    provider_meta   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    UNIQUE(article_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_for);

-- Search cache: short-lived (24h) cache for web_search() calls.
-- Key is sha1(query|language|depth|max_results); results is the raw
-- list[dict] from the search provider, JSON-encoded. Used by
-- aicrew/tools/search.py to dedupe Tavily calls across agents and
-- pipeline runs (events of the day move slowly; one Tavily call per
-- query per day is enough). TAVILY_DAILY_BUDGET counts rows in this
-- table to enforce a per-day cap.
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key   TEXT PRIMARY KEY,
    query       TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT '',
    results     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_cache_created ON search_cache(created_at);
"""


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    with _LOCK:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_schema(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # Soft migrations: add slug columns if missing (for existing DBs).
        for table, col in (("projects", "slug"), ("agents", "slug"), ("channels", "slug")):
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        # Soft migration: topics.event_date for date-anchored projects
        # (e.g. «Задним числом»). Older DBs created before this column
        # existed get it backfilled with empty string. The pipeline reads
        # event_date from the topic_validator output and writes it here so
        # researcher / writer / headline / qa agents can re-anchor to the
        # exact event date downstream.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(topics)").fetchall()]
        if "event_date" not in cols:
            conn.execute(
                "ALTER TABLE topics ADD COLUMN event_date TEXT NOT NULL DEFAULT ''"
            )
        # Soft migration: posts.attempts / posts.last_attempt_at for the
        # background scheduler. The scheduler retries failed publications
        # with exponential backoff up to MAX_PUBLISH_ATTEMPTS times; we
        # need a counter and a timestamp to compute the next eligible
        # retry. Older DBs created before the scheduler was added get
        # these columns backfilled here.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
        if "attempts" not in cols:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "last_attempt_at" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN last_attempt_at TEXT")
        # Soft migration: projects.enabled_teams. Each project owns a JSON
        # array of enabled team ids (text_ru / text_en / video_ru / video_en).
        # The pipeline skips disabled teams; the UI hides their cards. Older
        # DBs created before this column existed get it backfilled with the
        # full default set so no team is silently disabled by an upgrade.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
        if "enabled_teams" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN enabled_teams TEXT")
        # Backfill NULL/empty rows. Done unconditionally — cheap on small DBs
        # and safe (only touches rows where the value isn't set yet). The
        # default set is intentionally maximal: even projects with
        # language_modes=["ru"] get all four ids, which the API/UI then
        # filters by language_modes when rendering.
        conn.execute(
            "UPDATE projects SET enabled_teams = ? "
            "WHERE enabled_teams IS NULL OR enabled_teams = ''",
            (json.dumps(["text_ru", "text_en", "video_ru", "video_en"]),),
        )

        # Soft migration: media_assets.cost_usd for the cost-tracking
        # dashboard. Older rows get 0 (correct for mock/placeholder
        # assets, and an acceptable lossy-zero for real-API media we
        # can't backfill — see docs/03-data-model.md). Idempotent:
        # ALTER is gated on the column not already existing.
        cols = [r["name"] for r in conn.execute(
            "PRAGMA table_info(media_assets)").fetchall()]
        if "cost_usd" not in cols:
            conn.execute(
                "ALTER TABLE media_assets ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0"
            )

        # Sync ZADNIM-style agents with the latest prompts / params /
        # model config. Idempotent: re-running just re-applies the same
        # values. Used when a release ships new anti-hallucination rules
        # — without this hook, existing production DBs keep stale
        # snapshots of prompt_template (since prompts are stored on
        # agents.prompt_template at seed time).
        #
        # Lazy import to avoid a circular: seed.py imports db at module
        # load time (for db.connect / db.now_iso), so we cannot import
        # seed at the top of this file.
        try:
            from . import seed as _seed
        except ImportError:
            _seed = None
        if _seed is not None:
            _sync_zadnim_agents(conn, _seed)


def _sync_zadnim_agents(conn, seed_module) -> None:
    """Re-apply ZADNIM_PROMPTS / VIDEO_TEAM_PROMPTS / ZADNIM_AGENT_CONFIG /
    ZADNIM_AGENT_PARAMS onto every existing agent that owns one of those
    roles, and create missing fact_audit agents for projects that already
    have a text team.

    Idempotent. Designed to be called from ``init_schema`` on every
    application startup so that prompt fixes ship without manual SQL.

    Skips:
      * channel_rewriter — its prompt is per-channel and lives in
        REWRITER_PROMPTS, not ZADNIM_PROMPTS.
      * video_assembler — non-LLM role; its prompt template is a stub
        and there is no benefit to re-applying it.
    """
    zadnim = getattr(seed_module, "ZADNIM_PROMPTS", {}) or {}
    video_team = getattr(seed_module, "VIDEO_TEAM_PROMPTS", {}) or {}
    agent_config = getattr(seed_module, "ZADNIM_AGENT_CONFIG", {}) or {}
    agent_params = getattr(seed_module, "ZADNIM_AGENT_PARAMS", {}) or {}

    # 1) Refresh existing agents in place.
    rows = conn.execute(
        "SELECT id, project_id, role, language, prompt_template, model, "
        "temperature, max_tokens, params FROM agents"
    ).fetchall()
    for row in rows:
        role = row["role"]
        # Skip channels — they own a per-channel rewriter prompt, not a
        # role-level one. Skip video_assembler — non-LLM stub.
        if role in ("channel_rewriter", "video_assembler"):
            continue
        # Pick prompt: VIDEO_TEAM_PROMPTS wins over ZADNIM_PROMPTS for
        # roles that exist in both (matches seed.seed() priority).
        if role in video_team:
            prompt = video_team[role]
        elif role in zadnim:
            prompt = zadnim[role]
        else:
            # Role not managed by ZADNIM overrides — leave it alone.
            continue
        # Pick model triple: leave the existing values untouched if the
        # role is missing from ZADNIM_AGENT_CONFIG.
        cfg = agent_config.get(role)
        if cfg is not None:
            model, temp, max_tok = cfg
        else:
            model = row["model"]
            temp = row["temperature"]
            max_tok = row["max_tokens"]
        # Merge params: take the existing dict and overlay any keys from
        # ZADNIM_AGENT_PARAMS so a release adding a new param flag does
        # not silently revert user-customised values for keys it doesn't
        # touch.
        try:
            existing_params = json.loads(row["params"]) if row["params"] else {}
            if not isinstance(existing_params, dict):
                existing_params = {}
        except (TypeError, json.JSONDecodeError):
            existing_params = {}
        zad_params = agent_params.get(role)
        if isinstance(zad_params, dict):
            merged = dict(existing_params)
            merged.update(zad_params)
        else:
            merged = existing_params
        conn.execute(
            "UPDATE agents SET prompt_template=?, model=?, temperature=?, "
            "max_tokens=?, params=?, updated_at=? WHERE id=?",
            (prompt, model, float(temp), int(max_tok),
             json.dumps(merged, ensure_ascii=False),
             now_iso(), row["id"]),
        )

    # 2) Create missing fact_audit agents for projects that already have
    # a text team (i.e. at least one researcher or article_writer agent).
    # We do this for every distinct (project_id, language) pair found in
    # the existing text-team agents, so the new fact_audit agent matches
    # the project's language footprint.
    if "fact_audit" not in zadnim and "fact_audit" not in video_team:
        return  # Prompt template missing — nothing to seed.
    fa_prompt = zadnim.get("fact_audit") or video_team.get("fact_audit")
    fa_cfg = agent_config.get("fact_audit") or ("mock:smart", 0.2, 2500)
    fa_params = agent_params.get("fact_audit") or {"min_score": 80}
    fa_display_map = {"ru": "Fact Audit (RU)", "en": "Fact Audit (EN)"}
    fa_description = (
        "Post-writer factchecker: verifies every name, number and "
        "quote in the article body against the validated research "
        "brief; rewrites unsupported fragments."
    )
    project_lang_pairs = conn.execute(
        "SELECT DISTINCT project_id, language FROM agents "
        "WHERE role IN ('researcher', 'article_writer') "
        "AND language IN ('ru','en')"
    ).fetchall()
    for pair in project_lang_pairs:
        proj_id = pair["project_id"]
        lang = pair["language"]
        # Already exists? Keep idempotent.
        existing = conn.execute(
            "SELECT 1 FROM agents WHERE project_id=? AND role='fact_audit' "
            "AND language=?",
            (proj_id, lang),
        ).fetchone()
        if existing:
            continue
        slug = unique_slug(conn, "agents", f"fact-audit-{lang}",
                           scope_col="project_id", scope_val=proj_id)
        conn.execute(
            "INSERT INTO agents (id, project_id, slug, role, display_name, "
            "description, model, temperature, max_tokens, top_p, "
            "prompt_template, params, tools_enabled, language, is_enabled, "
            "created_at, updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("ag_"), proj_id, slug, "fact_audit",
                fa_display_map.get(lang, f"Fact Audit ({lang.upper()})"),
                fa_description,
                fa_cfg[0], float(fa_cfg[1]), int(fa_cfg[2]), 1.0,
                fa_prompt,
                json.dumps(fa_params, ensure_ascii=False),
                json.dumps([]),
                lang, 1, now_iso(), now_iso(),
            ),
        )


# --------------------------------------------------------------------- slug --

_RU2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, max_len: int = 60) -> str:
    """Russian-aware slugifier. 'Задним числом' -> 'zadnim-chislom'."""
    out: list[str] = []
    prev_dash = False
    for ch in (text or "").lower().strip():
        if ch in _RU2LAT:
            out.append(_RU2LAT[ch])
            prev_dash = False
        elif ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash and out:
                out.append("-")
                prev_dash = True
    s = "".join(out).strip("-")
    return s[:max_len].strip("-") or "x"


def unique_slug(conn, table: str, base: str, scope_col: str | None = None,
                scope_val: str | None = None, exclude_id: str | None = None) -> str:
    """Return a slug unique within the table (optionally scoped by another col)."""
    candidate = base
    n = 1
    while True:
        q = f"SELECT id FROM {table} WHERE slug=?"
        args: list[Any] = [candidate]
        if scope_col and scope_val is not None:
            q += f" AND {scope_col}=?"
            args.append(scope_val)
        if exclude_id:
            q += " AND id<>?"
            args.append(exclude_id)
        row = conn.execute(q, args).fetchone()
        if not row:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}{raw[:24]}" if prefix else raw[:24]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_list(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]  # type: ignore[misc]


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def jload(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
