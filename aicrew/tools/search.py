"""Search and URL fetch tools.

Providers wired in:
    - mock   : deterministic offline results, used in tests and demos.
    - tavily : real web search via https://api.tavily.com/search,
               with a SQLite-backed 24h cache and an optional
               daily budget guard (TAVILY_DAILY_BUDGET).

The web_search() function returns a list of dicts so it can be JSON-encoded
into agent inputs and into the search_cache table without extra dataclass
plumbing.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .. import db
from ..settings import Settings

log = logging.getLogger("aicrew.tools.search")

# Cache TTL: events of the day move slowly; 24 hours is a safe default.
# A second pipeline run within the same day reuses the same Tavily call.
CACHE_TTL_SEC = 24 * 60 * 60


def _slug(seed: str) -> str:
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]


def _cache_key(query: str, language: str, depth: str, max_results: int) -> str:
    raw = f"{query.strip().lower()}|{language}|{depth}|{max_results}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _today_iso() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%d")


def _load_cache(settings: Settings, key: str) -> list[dict[str, Any]] | None:
    """Returns cached results if they are within TTL, else None."""
    try:
        with db.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT results, created_at FROM search_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
    except Exception as exc:
        log.debug("search cache lookup failed (table may not exist yet): %s", exc)
        return None
    if not row:
        return None
    try:
        created = _dt.datetime.fromisoformat(row["created_at"])
    except Exception:
        return None
    age = (_dt.datetime.utcnow() - created).total_seconds()
    if age > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(row["results"])
    except Exception:
        return None


def _store_cache(settings: Settings, key: str, query: str, language: str,
                 results: list[dict[str, Any]]) -> None:
    try:
        with db.connect(settings.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache "
                "(cache_key, query, language, results, created_at) "
                "VALUES (?,?,?,?,?)",
                (key, query, language, json.dumps(results, ensure_ascii=False),
                 _dt.datetime.utcnow().isoformat()),
            )
    except Exception as exc:
        log.warning("failed to store search cache: %s", exc)


def _today_used_count(settings: Settings) -> int:
    """How many distinct Tavily queries we've made today (UTC). Used for budget guard."""
    today = _today_iso()
    try:
        with db.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM search_cache WHERE created_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0


def _budget_left(settings: Settings) -> int | None:
    """Returns remaining budget for today, or None if no budget configured."""
    raw = os.environ.get("TAVILY_DAILY_BUDGET", "").strip()
    if not raw:
        return None
    try:
        budget = int(raw)
    except ValueError:
        return None
    if budget <= 0:
        return None
    return max(0, budget - _today_used_count(settings))


def _mock_results(query: str, top_k: int) -> list[dict[str, Any]]:
    """Deterministic, plausible-looking offline results."""
    suffixes = [
        ("ai-trends", "Top 10 trends shaping the field"),
        ("research", "Recent peer-reviewed paper roundup"),
        ("explainer", "Explained for non-experts"),
        ("case-study", "Real-world deployment case study"),
        ("guide", "Practical hands-on guide"),
        ("history", "Historical background and dates"),
        ("primary-source", "Primary source archive snippet"),
    ]
    out: list[dict[str, Any]] = []
    for i, (slug, label) in enumerate(suffixes[:top_k]):
        out.append({
            "title": f"{query} — {label}",
            "url": f"https://mock.example/{slug}/{_slug(query + slug)}",
            "content": (
                f"Mock-search result for '{query}'. {label}. "
                "Synthetic content used because the sandbox has no internet."
            ),
            "score": round(1.0 - i * 0.1, 2),
            "rank": i + 1,
        })
    return out


def _tavily_call(query: str, *, api_key: str, depth: str,
                 max_results: int) -> list[dict[str, Any]]:
    """One real call to https://api.tavily.com/search.

    Wire format::

        POST https://api.tavily.com/search
        Authorization: Bearer tvly-...
        {
          "query": "...",
          "search_depth": "basic" | "advanced",
          "max_results": 5,
          "include_answer": false
        }

    Response::

        {
          "query": "...",
          "results": [
            {"title": str, "url": str, "content": str, "score": float, ...},
            ...
          ],
          "answer": str | null
        }
    """
    body = {
        "query": query,
        "search_depth": "advanced" if depth == "advanced" else "basic",
        "max_results": int(max_results),
        "include_answer": False,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        log.error("Tavily HTTP %s: %s", exc.code, err_body[:300])
        raise RuntimeError(
            f"Tavily HTTP {exc.code}: {err_body[:200]}"
        ) from exc
    except Exception as exc:
        log.exception("Tavily request failed")
        raise RuntimeError(f"Tavily request failed: {exc}") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    payload = json.loads(raw.decode("utf-8"))
    raw_results = payload.get("results") or []
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raw_results):
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "content": r.get("content") or r.get("snippet") or "",
            "score": float(r.get("score") or 0.0),
            "rank": i + 1,
        })
    log.info("tavily ok: query=%r depth=%s results=%d latency_ms=%d",
             query[:80], depth, len(out), latency_ms)
    return out


def web_search(
    query: str,
    *,
    settings: Settings,
    language: str = "",
    depth: str = "basic",
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Public entry point. Returns a list of result dicts.

    Behaviour by ``settings.search_provider``:
      - ``mock``   : deterministic mock results, no network.
      - ``tavily`` : 24h cache → daily budget guard → real Tavily call →
                     cache the result. On any error returns [] and logs;
                     we never break the pipeline because of search.
      - other      : returns [] (caller should treat as "no web context").

    Daily budget: set ``TAVILY_DAILY_BUDGET=N`` in the env to cap the
    number of distinct Tavily calls per UTC day. When the budget is hit,
    web_search() returns [] for the rest of the day, agents continue
    without web context.
    """
    query = (query or "").strip()
    if not query:
        return []
    if settings.search_provider == "mock":
        return _mock_results(query, max_results)
    if settings.search_provider != "tavily":
        return []

    key = _cache_key(query, language, depth, max_results)
    cached = _load_cache(settings, key)
    if cached is not None:
        log.info("tavily cache HIT: query=%r depth=%s -> %d results",
                 query[:80], depth, len(cached))
        return cached

    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        log.warning(
            "search_provider=tavily but TAVILY_API_KEY is empty; "
            "returning empty results. Add TAVILY_API_KEY to .env."
        )
        return []

    left = _budget_left(settings)
    if left is not None and left <= 0:
        log.warning(
            "TAVILY_DAILY_BUDGET reached for today; skipping search. "
            "Returning [] so the pipeline keeps running without web context."
        )
        return []

    try:
        results = _tavily_call(
            query, api_key=api_key, depth=depth, max_results=max_results,
        )
    except Exception as exc:
        log.warning("tavily call failed; falling back to []: %s", exc)
        return []

    _store_cache(settings, key, query, language, results)
    return results


def fetch_url(url: str, *, settings: Settings) -> dict[str, Any]:
    if settings.use_mock_search:
        return {
            "url": url,
            "status": 200,
            "title": f"Mock page: {url}",
            "text": (
                f"Synthetic body for {url}. In production this would be real "
                "fetched HTML rendered to text via readability/trafilatura."
            ),
            "fetched_at": "mock",
        }
    raise NotImplementedError("real fetch_url not wired in this build")
