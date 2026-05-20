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
