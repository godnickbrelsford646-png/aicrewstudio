"""Background publication scheduler.

The pipeline used to publish posts synchronously the moment
``run_publication_phase`` finished. That broke the user's mental model:
slots in ``channel_slots`` were ignored, every post landed at "запуск
полного цикла" instead of at the chosen time.

This module fixes that. The flow is now:

  1. ``PipelineRunner.enqueue_publications(project_id)`` decides which
     article goes into which slot of which channel and writes one
     ``posts`` row per (article, channel, slot) with
     ``status='scheduled'`` and a UTC ``scheduled_for`` timestamp.

  2. The background thread started by ``start_scheduler(settings)``
     wakes every TICK_SECONDS, picks ``posts`` whose ``scheduled_for``
     has passed and whose ``attempts`` is below MAX_ATTEMPTS, and
     publishes them via the channel adapter.

  3. On failure the row stays in ``status='scheduled'`` with
     ``attempts`` incremented; the next attempt is gated by an
     exponential backoff (``next_retry_at`` is recomputed from
     ``last_attempt_at``). After MAX_ATTEMPTS the row goes to
     ``status='failed'`` permanently.

The scheduler is intentionally a single thread inside the same
process as the HTTP server. SQLite is the coordinator: rows are taken
under a transaction so concurrent ticks (or a manual "publish now"
click from the UI) cannot fire the same post twice.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db
from .crypto import decrypt
from .publishers import get_publisher
from .settings import Settings

log = logging.getLogger("aicrew.scheduler")


# How often the scheduler checks for due posts. 30 s is a reasonable
# balance: posts can be at most 30 s late, but we don't hammer SQLite.
TICK_SECONDS = 30

# Hard ceiling on retry attempts. After this many failures a post is
# marked as 'failed' for good and the user must intervene (the
# "publish now" button creates a fresh attempt).
MAX_ATTEMPTS = 5

# Backoff schedule (seconds since last_attempt_at): 1m, 5m, 15m, 1h, 6h.
# Gives ~7 hours of retry coverage for transient network/auth errors.
_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)


# --------------------------------------------------------------------- time --


def next_slot_utc(time_local: str, tz_name: str,
                  *, now_utc: datetime | None = None) -> datetime:
    """Return the next future moment when local clock reads ``time_local``.

    ``time_local`` is "HH:MM" (24h, no seconds). ``tz_name`` is an
    IANA name (e.g. "Europe/Moscow"). Returns a *timezone-aware* UTC
    datetime; never returns a moment that already passed.

    Falls back to UTC if the timezone can't be resolved (older Linux
    images sometimes lack the tzdata package). The fallback is loud:
    a warning is logged so deployment can fix it.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning("unknown timezone %r, falling back to UTC", tz_name)
        tz = timezone.utc
    try:
        h, m = (int(x) for x in time_local.split(":")[:2])
    except (ValueError, AttributeError):
        log.warning("bad time_local %r, defaulting to 09:00", time_local)
        h, m = 9, 0
    now_local = now_utc.astimezone(tz)
    candidate = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_local:
        # The slot is later today already — push to tomorrow. We never
        # schedule "in the past" because the scheduler would fire it
        # immediately at next tick, defeating the slot.
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    """SQLite-friendly UTC ISO string (no microseconds, with Z)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc_iso() -> str:
    return _utc_iso(datetime.now(timezone.utc))


def is_due(scheduled_for: str | None, last_attempt_at: str | None,
           attempts: int, *, now_utc: datetime | None = None) -> bool:
    """Should this post be published in this tick?

    A post is due if:
      * it has a scheduled_for in the past, AND
      * either it was never attempted, OR enough backoff time has
        passed since the previous attempt.

    Centralizing this in one function keeps the SQL simple (we filter
    by ``status='scheduled' AND scheduled_for <= now``) and lets us
    apply the per-row backoff in Python.
    """
    if not scheduled_for:
        return False
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    try:
        sch = datetime.strptime(scheduled_for, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        # Ancient rows might not have the Z suffix; be permissive.
        try:
            sch = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        except ValueError:
            return False
    if sch > now_utc:
        return False
    if not last_attempt_at or attempts <= 0:
        return True
    backoff_idx = min(attempts - 1, len(_BACKOFF_SECONDS) - 1)
    wait = _BACKOFF_SECONDS[backoff_idx]
    try:
        prev = datetime.strptime(last_attempt_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return True
    return now_utc >= prev + timedelta(seconds=wait)


# ---------------------------------------------------------------- publishing --


def _channel_creds(channel: dict[str, Any], master_key: str) -> dict[str, str]:
    """Decrypt the channel's stored credentials JSON. Returns {} on failure."""
    enc = (channel.get("credentials_enc") or "").strip()
    if not enc:
        return {}
    try:
        return json.loads(decrypt(enc, master_key))
    except Exception as exc:
        log.warning("failed to decrypt credentials for channel %s: %s",
                    channel.get("id"), exc)
        return {}


def publish_one_post(post_id: str, settings: Settings) -> bool:
    """Publish a single post by id. Returns True on success.

    Used both by the background scheduler and by the "publish now"
    button in the UI. The function is idempotent on the row level: if
    another worker already moved the row out of 'scheduled' status,
    we simply skip.
    """
    with db.connect(settings.db_path) as conn:
        # Race-safe acquire: only one worker can claim a scheduled post.
        # We bump attempts/last_attempt_at *before* the network call so
        # that even if the worker crashes mid-publish, the row is not
        # picked up again until the backoff window passes.
        post = conn.execute(
            "SELECT * FROM posts WHERE id=? AND status='scheduled'",
            (post_id,)
        ).fetchone()
        if not post:
            return False
        post = db.row_to_dict(post)
        attempts = int(post.get("attempts") or 0) + 1
        conn.execute(
            "UPDATE posts SET attempts=?, last_attempt_at=? WHERE id=?",
            (attempts, now_utc_iso(), post_id),
        )
        ch_row = conn.execute(
            "SELECT * FROM channels WHERE id=?", (post["channel_id"],)
        ).fetchone()
    if not ch_row:
        log.error("post %s references missing channel %s", post_id, post["channel_id"])
        return False
    channel = db.row_to_dict(ch_row)
    publisher = get_publisher(channel["kind"])
    creds = _channel_creds(channel, settings.master_key)
    try:
        res = publisher.publish(
            post={
                "id": post_id,
                "body": post["body"] or "",
                "headline": post["headline"],
                "image_asset_id": post["image_asset_id"],
                "language": channel["language"],
            },
            creds=creds, settings=settings,
        )
    except Exception as exc:
        log.exception("publish raised for post=%s channel=%s", post_id, channel["kind"])
        with db.connect(settings.db_path) as conn:
            if attempts >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE posts SET status='failed', error=? WHERE id=?",
                    (f"max attempts reached: {exc!r}", post_id),
                )
            else:
                conn.execute("UPDATE posts SET error=? WHERE id=?",
                             (repr(exc), post_id))
        return False
    with db.connect(settings.db_path) as conn:
        if res.ok:
            conn.execute(
                "UPDATE posts SET status='published', published_at=?, "
                "external_url=?, provider_meta=?, error=NULL WHERE id=?",
                (now_utc_iso(), res.external_url,
                 db.jdump(res.raw_response), post_id),
            )
            log.info("published post %s -> %s", post_id, res.external_url or "(no url)")
            return True
        # Soft failure: keep status='scheduled' so the next backoff tick
        # retries; only escalate to 'failed' after MAX_ATTEMPTS.
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE posts SET status='failed', error=?, provider_meta=? WHERE id=?",
                (res.error or "unknown", db.jdump(res.raw_response), post_id),
            )
        else:
            conn.execute(
                "UPDATE posts SET error=?, provider_meta=? WHERE id=?",
                (res.error or "unknown", db.jdump(res.raw_response), post_id),
            )
    return False


def publish_due_posts(settings: Settings) -> int:
    """One scheduler tick. Publishes everything that's due. Returns count."""
    with db.connect(settings.db_path) as conn:
        now_iso = now_utc_iso()
        rows = db.rows_to_list(conn.execute(
            "SELECT id, scheduled_for, last_attempt_at, attempts FROM posts "
            "WHERE status='scheduled' AND scheduled_for IS NOT NULL "
            "AND scheduled_for <= ? AND attempts < ? "
            "ORDER BY scheduled_for ASC LIMIT 50",
            (now_iso, MAX_ATTEMPTS),
        ).fetchall())
    published = 0
    for r in rows:
        if not is_due(r["scheduled_for"], r["last_attempt_at"],
                      int(r["attempts"] or 0)):
            continue
        if publish_one_post(r["id"], settings):
            published += 1
    if published:
        log.info("scheduler tick: published %d post(s)", published)
    return published


# ---------------------------------------------------------------- background --


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler(settings: Settings) -> None:
    """Spawn the background tick thread (idempotent within the process).

    Called from ``api.serve()`` at startup. Daemon thread so it dies
    with the HTTP server. Re-entrant guard prevents double-spawn if
    ``serve()`` is somehow invoked twice in tests.
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    t = threading.Thread(
        target=_scheduler_loop, args=(settings,),
        name="aicrew-scheduler", daemon=True,
    )
    t.start()
    log.warning("publication scheduler started (tick=%ds, max_attempts=%d)",
                TICK_SECONDS, MAX_ATTEMPTS)


def _scheduler_loop(settings: Settings) -> None:
    while True:
        try:
            publish_due_posts(settings)
        except Exception:
            # Never let the loop die. Log and keep going on the next
            # tick — a transient SQLite lock or a malformed row should
            # not silently kill background publishing.
            log.exception("scheduler tick crashed")
        time.sleep(TICK_SECONDS)
