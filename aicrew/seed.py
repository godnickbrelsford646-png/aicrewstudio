"""Seed a demo project: 'AI Weekly' with RU+EN agents and a few channels."""

from __future__ import annotations

import json
from typing import Any

from . import db
from .agents.registry import default_agents_for_project, role_spec
from .channels.registry import CHANNEL_KINDS
from .crypto import encrypt
from .settings import Settings


DEMO_USER_EMAIL = "owner@aicrew.local"
DEMO_PROJECT_NAME = "AI Weekly"

DEMO_CHANNELS: list[dict[str, Any]] = [
    {"kind": "telegram", "name": "TG: AI Weekly RU", "language": "ru", "posts_per_day": 2,
     "slots": ["09:00", "18:30"], "creds": {"bot_token": "demo:tg_bot_token", "chat_id": "@demo"}},
    {"kind": "telegram", "name": "TG: AI Weekly EN", "language": "en", "posts_per_day": 2,
     "slots": ["10:00", "19:00"], "creds": {"bot_token": "demo:tg_bot_token", "chat_id": "@demo_en"}},
    {"kind": "vk", "name": "VK: AI Weekly", "language": "ru", "posts_per_day": 1,
     "slots": ["12:00"], "creds": {"access_token": "demo:vk_token", "owner_id": "-12345678"}},
    {"kind": "x", "name": "X: AI Weekly", "language": "en", "posts_per_day": 3,
     "slots": ["08:00", "13:00", "20:00"],
     "creds": {"consumer_key": "demo", "consumer_secret": "demo",
               "access_token": "demo", "access_secret": "demo"}},
    {"kind": "facebook", "name": "FB: AI Weekly", "language": "en", "posts_per_day": 1,
     "slots": ["15:00"], "creds": {"page_id": "demo", "page_access_token": "demo"}},
    {"kind": "ok", "name": "OK: AI Weekly", "language": "ru", "posts_per_day": 1,
     "slots": ["19:00"],
     "creds": {"application_key": "demo", "access_token": "demo", "group_id": "demo"}},
    # Video channel: kept for demo but pipeline currently skips publishing video posts.
    {"kind": "youtube_shorts", "name": "YT Shorts: AI Weekly", "language": "en",
     "posts_per_day": 1, "slots": ["16:00"],
     "creds": {"client_id": "demo", "client_secret": "demo", "refresh_token": "demo"}},
]


def seed(settings: Settings) -> dict[str, str]:
    db.init_schema(settings.db_path)
    with db.connect(settings.db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email=?", (DEMO_USER_EMAIL,)
        ).fetchone()
        if existing:
            user_id = existing["id"]
        else:
            user_id = db.new_id("u_")
            conn.execute(
                "INSERT INTO users (id, email, created_at) VALUES (?,?,?)",
                (user_id, DEMO_USER_EMAIL, db.now_iso()),
            )
        existing_proj = conn.execute(
            "SELECT id FROM projects WHERE user_id=? AND name=?",
            (user_id, DEMO_PROJECT_NAME),
        ).fetchone()
        if existing_proj:
            return {"user_id": user_id, "project_id": existing_proj["id"], "status": "exists"}
        project_id = db.new_id("p_")
        conn.execute(
            "INSERT INTO projects (id, user_id, name, niche, description, is_enabled, "
            "timezone, language_modes, daily_topics_target, daily_articles_target, "
            "budget_usd_month, style_guide, created_at, updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, user_id, DEMO_PROJECT_NAME, "AI / ML / производительность",
                "Еженедельный дайджест про AI с разбором инструментов и трендов.",
                1, "Europe/Berlin", json.dumps(["ru", "en"]),
                8, 2, 50.0,
                "Тон — экспертный, дружелюбный. Короткие абзацы. Без воды.",
                db.now_iso(), db.now_iso(),
            ),
        )
        # Project agents
        for entry in default_agents_for_project(["ru", "en"]):
            spec = entry["spec"]
            conn.execute(
                "INSERT INTO agents (id, project_id, role, display_name, description, "
                "model, temperature, max_tokens, top_p, prompt_template, params, tools_enabled, "
                "language, is_enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    db.new_id("ag_"), project_id, entry["role"], entry["display_name"],
                    spec.description, spec.default_model, spec.default_temperature,
                    spec.default_max_tokens, 1.0, spec.prompt_template,
                    json.dumps(spec.default_params),
                    json.dumps(list(spec.tools)),
                    entry["language"], 1, db.now_iso(), db.now_iso(),
                ),
            )
        # Channels + per-channel rewriter agent
        for ch in DEMO_CHANNELS:
            spec = CHANNEL_KINDS[ch["kind"]]
            channel_id = db.new_id("c_")
            creds_enc = encrypt(json.dumps(ch.get("creds") or {}), settings.master_key)
            rewriter_id = None
            if not spec.is_video:
                # create a per-channel rewriter agent
                rspec = role_spec("channel_rewriter")
                rewriter_id = db.new_id("ag_")
                conn.execute(
                    "INSERT INTO agents (id, project_id, role, display_name, description, "
                    "model, temperature, max_tokens, top_p, prompt_template, params, "
                    "tools_enabled, language, channel_id, is_enabled, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rewriter_id, project_id, "channel_rewriter",
                        f"Rewriter — {ch['name']}",
                        f"Adapts the article to {spec.label} format and tone.",
                        rspec.default_model, rspec.default_temperature,
                        rspec.default_max_tokens, 1.0, rspec.prompt_template,
                        json.dumps({**rspec.default_params,
                                    "style_voice": "Голос канала, читабельно, без воды."}),
                        json.dumps([]), ch["language"], channel_id, 1,
                        db.now_iso(), db.now_iso(),
                    ),
                )
            conn.execute(
                "INSERT INTO channels (id, project_id, kind, name, language, is_video, "
                "posts_per_day, selection_strategy, rewriter_prompt, rewriter_agent_id, "
                "credentials_enc, is_enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    channel_id, project_id, ch["kind"], ch["name"], ch["language"],
                    1 if spec.is_video else 0, ch["posts_per_day"], "by_rank",
                    "", rewriter_id, creds_enc, 1, db.now_iso(), db.now_iso(),
                ),
            )
            for slot in ch["slots"]:
                conn.execute(
                    "INSERT INTO channel_slots (id, channel_id, time_local, enabled) "
                    "VALUES (?,?,?,1)",
                    (db.new_id("cs_"), channel_id, slot),
                )
        return {"user_id": user_id, "project_id": project_id, "status": "created"}
