"""Seed project: 'Задним числом' with RU+EN agents and ALL 14 channels."""

from __future__ import annotations

import json
from typing import Any

from . import db
from .agents.registry import default_agents_for_project, role_spec
from .channels.registry import CHANNEL_KINDS
from .crypto import encrypt
from .settings import Settings


DEMO_USER_EMAIL = "owner@aicrew.local"
DEMO_PROJECT_NAME = "Задним числом"

# ---- Per-channel unique rewriter prompts ----------------------------------

REWRITER_PROMPTS: dict[str, str] = {
    "telegram": (
        "Перепиши статью в пост для Telegram-канала. Требования:\n"
        "- Максимум 4096 символов.\n"
        "- Первый абзац — цепляющий крючок, который заставляет читать дальше.\n"
        "- Короткие абзацы (2–4 предложения), между ними пустая строка.\n"
        "- Используй Markdown-форматирование: **жирный** для ключевых фраз.\n"
        "- Финал: сильная смысловая точка, не «подписывайтесь».\n"
        "- Хэштеги: 3–5 штук в самом конце, через пробел.\n"
        "- Заголовок и картинку использовать без изменений.\n"
        "Верни JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "vk": (
        "Перепиши статью в пост для ВКонтакте. Требования:\n"
        "- До 16 000 символов, но оптимально 2000–4000.\n"
        "- Живой, эмоциональный стиль. Можно emoji, но без перебора.\n"
        "- Абзацы средние. Можно использовать нумерованные списки.\n"
        "- В конце: 5–8 хэштегов через пробел (формат #история #факты).\n"
        "- Заголовок и картинку использовать без изменений.\n"
        "Верни JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "max": (
        "Перепиши статью в пост для мессенджера MAX. Требования:\n"
        "- До 4000 символов.\n"
        "- Короткие предложения, разговорный тон.\n"
        "- Markdown не поддерживается — только плоский текст.\n"
        "- Заголовок и картинку использовать без изменений.\n"
        "Верни JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "facebook": (
        "Rewrite the article as a Facebook Page post. Requirements:\n"
        "- Optimal length 800–2200 characters (for maximum reach).\n"
        "- Conversational, engaging tone.\n"
        "- Start with a hook that stops scrolling.\n"
        "- 3–5 hashtags at the end.\n"
        "- Keep the headline and image unchanged.\n"
        "Return JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "instagram": (
        "Rewrite the article as an Instagram caption. Requirements:\n"
        "- Maximum 2200 characters.\n"
        "- First line is the hook (no line break after it in preview).\n"
        "- Use line breaks generously. Short paragraphs.\n"
        "- 20–30 hashtags at the very end, separated by a dotted line.\n"
        "- Storytelling tone, slightly personal.\n"
        "- Keep the headline and image unchanged.\n"
        "Return JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "ok": (
        "Перепиши статью в пост для Одноклассников. Требования:\n"
        "- До 4000 символов (оптимально).\n"
        "- Простой, понятный язык для широкой аудитории 35+.\n"
        "- Без сложных терминов, без молодёжного сленга.\n"
        "- Разбей на абзацы. Можно emoji, но аккуратно (1–3).\n"
        "- 3–5 хэштегов в конце.\n"
        "- Заголовок и картинку использовать без изменений.\n"
        "Верни JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}"
    ),
    "threads": (
        "Rewrite the article as a Threads post. Requirements:\n"
        "- MAXIMUM 500 characters. This is HARD limit.\n"
        "- One powerful thought from the article, phrased as a standalone micro-post.\n"
        "- Provocative or thought-provoking angle.\n"
        "- No hashtags (Threads doesn't use them well).\n"
        "- Keep headline and image unchanged.\n"
        "Return JSON: {\"post_body\": str, \"hashtags\": [], \"first_comment\": str|null}"
    ),
    "x": (
        "Rewrite the article as a tweet thread for X (Twitter). Requirements:\n"
        "- Each tweet max 280 characters.\n"
        "- First tweet is a hook that makes people click 'show thread'.\n"
        "- Total: 3–7 tweets in the thread.\n"
        "- Last tweet: call to discussion or strong closing thought.\n"
        "- Use 1–2 relevant hashtags in the first tweet only.\n"
        "- Keep headline and image unchanged.\n"
        "Return JSON: {\"post_body\": str, \"hashtags\": [str], \"first_comment\": str|null}\n"
        "(post_body should contain all tweets separated by \\n---\\n)"
    ),
}

# ---- Channel definitions: ALL 14 channels ---------------------------------

DEMO_CHANNELS: list[dict[str, Any]] = [
    # --- ТЕКСТОВЫЕ: RU ---
    {"kind": "telegram", "name": "Задним числом · Telegram", "language": "ru",
     "posts_per_day": 2, "slots": ["09:00", "19:00"],
     "creds": {"bot_token": "подставить", "chat_id": "подставить"}},
    {"kind": "vk", "name": "Задним числом · ВКонтакте", "language": "ru",
     "posts_per_day": 1, "slots": ["12:00"],
     "creds": {"access_token": "подставить", "owner_id": "подставить"}},
    {"kind": "max", "name": "Задним числом · MAX", "language": "ru",
     "posts_per_day": 1, "slots": ["14:00"], "creds": {}},
    {"kind": "ok", "name": "Задним числом · Одноклассники", "language": "ru",
     "posts_per_day": 1, "slots": ["18:00"],
     "creds": {"application_key": "подставить", "access_token": "подставить", "group_id": "подставить"}},
    # --- ТЕКСТОВЫЕ: EN ---
    {"kind": "facebook", "name": "Backdated · Facebook", "language": "en",
     "posts_per_day": 1, "slots": ["15:00"],
     "creds": {"page_id": "подставить", "page_access_token": "подставить"}},
    {"kind": "instagram", "name": "Backdated · Instagram", "language": "en",
     "posts_per_day": 1, "slots": ["17:00"],
     "creds": {"ig_user_id": "подставить", "page_access_token": "подставить"}},
    {"kind": "threads", "name": "Backdated · Threads", "language": "en",
     "posts_per_day": 2, "slots": ["10:00", "20:00"],
     "creds": {"threads_user_id": "подставить", "access_token": "подставить"}},
    {"kind": "x", "name": "Backdated · X", "language": "en",
     "posts_per_day": 2, "slots": ["08:00", "21:00"],
     "creds": {"consumer_key": "подставить", "consumer_secret": "подставить",
               "access_token": "подставить", "access_secret": "подставить"}},
    # --- ВИДЕО: RU ---
    {"kind": "vk_video", "name": "Задним числом · VK Видео", "language": "ru",
     "posts_per_day": 1, "slots": ["13:00"],
     "creds": {"access_token": "подставить", "owner_id": "подставить"}},
    {"kind": "rutube", "name": "Задним числом · RuTube", "language": "ru",
     "posts_per_day": 1, "slots": ["14:00"],
     "creds": {"api_token": "подставить"}},
    # --- ВИДЕО: EN ---
    {"kind": "youtube_shorts", "name": "Backdated · YouTube Shorts", "language": "en",
     "posts_per_day": 1, "slots": ["16:00"],
     "creds": {"client_id": "подставить", "client_secret": "подставить", "refresh_token": "подставить"}},
    {"kind": "tiktok", "name": "Backdated · TikTok", "language": "en",
     "posts_per_day": 1, "slots": ["18:00"],
     "creds": {"client_key": "подставить", "client_secret": "подставить", "access_token": "подставить"}},
    {"kind": "instagram_reels", "name": "Backdated · IG Reels", "language": "en",
     "posts_per_day": 1, "slots": ["19:00"],
     "creds": {"ig_user_id": "подставить", "page_access_token": "подставить"}},
    {"kind": "snapchat", "name": "Backdated · Snapchat", "language": "en",
     "posts_per_day": 1, "slots": ["20:00"], "creds": {}},
]



# ---- Custom prompt overrides for "Задним числом" project -------------------
# These replace the generic prompts from registry.py for this specific project.

ZADNIM_PROMPTS: dict[str, str] = {
    "topic_generator": (
        "Ты — генератор тем для проекта «Задним числом». "
        "Сегодняшняя дата: {{ today }}.\n\n"
        "Твоя задача: найти {{ params.topics_per_run }} исторических событий, "
        "которые произошли ИМЕННО в этот день (число и месяц) в разные годы.\n\n"
        "Критерии выбора:\n"
        "- Событие должно содержать ИСТОРИЮ: герой, конфликт, драма, поворот, тайна или абсурд.\n"
        "- Не просто факт из Википедии, а сюжет, который цепляет.\n"
        "- Разнообразие: история, культура, наука, спорт, политика, кино, музыка, криминал, технологии.\n"
        "- Массовый интерес: обычный читатель должен понять, почему это интересно.\n"
        "- Проверяемость: должны быть надёжные источники.\n\n"
        "НЕ подходят:\n"
        "- Сухие даты без истории ('родился X', 'подписан договор Y').\n"
        "- Слишком локальные события без человеческого напряжения.\n"
        "- Темы, по которым невозможно написать живой текст.\n\n"
        "Запрещённые темы (уже обработаны ранее):\n"
        "{% for t in forbidden_topics %}- {{ t }}\n{% endfor %}\n\n"
        "Используй web_search для поиска событий на сегодняшнюю дату.\n\n"
        "Верни строго JSON:\n"
        '{ "topics": [ {"title": "краткое название события", '
        '"angle": "почему это интересно, в чём конфликт/герой/драма", '
        '"why_now": "связь с современностью или причина привлекательности", '
        '"tentative_sources": ["url1","url2"]} ] }'
    ),
    "topic_validator": (
        "Ты — фактчекер проекта «Задним числом».\n\n"
        "Для каждой темы из списка:\n"
        "1. Проверь, что событие ДЕЙСТВИТЕЛЬНО произошло в указанную дату.\n"
        "2. Убедись, что у события есть герой/конфликт/драма — что из него можно сделать сильную историю.\n"
        "3. Расширь описание: добавь 2–3 детали, которые делают историю живой.\n"
        "4. Добавь 2–3 надёжных источника.\n"
        "5. Пометь is_valid=true, только если дата верна И история сильная.\n\n"
        "Отклоняй темы, где:\n"
        "- Дата неверная.\n"
        "- Нет внутреннего конфликта/драмы/героя.\n"
        "- Тема слишком банальна ('родился X' без угла).\n\n"
        "Целевое число подтверждённых: {{ params.confirmed_topics_target }}.\n\n"
        "Кандидаты:\n"
        "{% for t in candidate_topics %}- {{ t.title }}: {{ t.angle }}\n{% endfor %}\n\n"
        "Верни JSON:\n"
        '{ "validated": [ {"title": str, "summary_extended": str, "sources": [str], '
        '"is_valid": bool, "reject_reason": str|null} ], "missing_count": int }'
    ),
    "topic_ranker": (
        "Ты — главный редактор проекта «Задним числом».\n\n"
        "Оцени каждую тему по 5 критериям (0–10):\n"
        "1. **hook** — сила первого впечатления: есть ли парадокс, контраст, загадка?\n"
        "2. **drama** — глубина конфликта: герой vs обстоятельства, ставки, цена решения.\n"
        "3. **novelty** — насколько тема неожиданна для массового читателя.\n"
        "4. **virality** — захочет ли человек пересказать это другу.\n"
        "5. **modern_link** — есть ли связь с сегодняшним днём, отзвук в современности.\n\n"
        "Веса: hook=0.25, drama=0.25, novelty=0.2, virality=0.2, modern_link=0.1\n\n"
        "Темы:\n"
        "{% for t in validated_topics %}- {{ t.title }}: {{ t.summary_extended }}\n{% endfor %}\n\n"
        "Верни JSON:\n"
        '{ "ranked": [ {"title": str, "scores": {"hook":int,"drama":int,"novelty":int,'
        '"virality":int,"modern_link":int}, "score_total": number, "rationale": str} ] }'
    ),
    "researcher": (
        "Ты — исследователь проекта «Задним числом».\n\n"
        "Тема: \"{{ topic.title }}\"\n"
        "Контекст: {{ topic.summary_extended }}\n\n"
        "Твоя задача: собрать ПОЛНОЕ исследовательское досье для автора статьи.\n\n"
        "Что нужно найти:\n"
        "- Кто герой/герои истории? Их биография кратко, мотивы.\n"
        "- В чём конфликт? Что стояло на кону?\n"
        "- Хронология: что было до, что случилось, что после.\n"
        "- Неожиданные детали: мелочи, которые удивят читателя.\n"
        "- Цитаты участников (если есть).\n"
        "- Последствия: как событие повлияло на будущее.\n"
        "- Связь с современностью.\n"
        "- Визуальный потенциал: что можно показать на картинке.\n\n"
        "Минимум фактов: {{ params.min_facts }}.\n"
        "Используй web_search и fetch_url.\n\n"
        "Верни JSON:\n"
        '{ "facts":[{"claim":str,"source":str}], '
        '"stats":[{"value":str,"context":str,"source":str}], '
        '"quotes":[{"who":str,"what":str,"source":str}], '
        '"hero": str, "conflict": str, "unexpected_detail": str, '
        '"modern_relevance": str, "visual_idea": str, '
        '"open_questions":[str] }'
    ),
    "research_validator": (
        "Ты — старший фактчекер проекта «Задним числом».\n\n"
        "Перепроверь исследовательское досье. Для каждого факта:\n"
        "- Верна ли дата? Верны ли имена, цифры, места?\n"
        "- Надёжен ли источник?\n"
        "- Нет ли подмены фактов легендами?\n\n"
        "Если нашёл ошибку — ИСПРАВЬ прямо в досье. Не пиши лог ошибок.\n"
        "Верни исправленную версию в ТОМ ЖЕ формате, что получил на входе.\n"
        "Если всё верно — верни как есть.\n\n"
        "Досье:\n{{ research_brief }}"
    ),
    "article_writer": (
        "Ты — автор проекта «Задним числом». Твоя задача: написать ЖИВУЮ историю, не энциклопедическую справку.\n\n"
        "Тема: \"{{ topic.title }}\"\n\n"
        "ПРАВИЛА:\n"
        "1. НЕ начинай с даты. Начни с героя, конфликта, парадокса или драматического момента.\n"
        "2. Дата появляется естественно по ходу текста.\n"
        "3. Текст должен ощущаться как рассказ хорошего рассказчика, а не Википедия.\n"
        "4. Короткие абзацы (2–4 предложения). Между абзацами — пустая строка.\n"
        "5. Никакого канцелярита, академической сухости, штампов.\n"
        "6. Обязательно: крючок → контекст → конфликт → развитие → неожиданная деталь → последствия → связь с сегодня → финал.\n"
        "7. Финал: сильная смысловая точка, не 'таким образом'.\n"
        "8. Длина: {{ params.target_chars }} символов.\n\n"
        "Стиль: {{ project.style_guide }}\n\n"
        "Исследовательское досье:\n{{ research_validated }}\n\n"
        "Верни JSON:\n"
        '{ "title_working": str, "body_md": str (markdown), "key_points": [str], '
        '"tldr": str, "tags": [str] }'
    ),
    "headline_writer": (
        "Ты — создатель заголовков для проекта «Задним числом».\n\n"
        "Статья: \"{{ article.title_working }}\"\n"
        "TL;DR: {{ article.tldr }}\n\n"
        "Создай {{ params.headlines_per_article }} вариантов заголовков. Стили:\n"
        "- Парадокс: 'Его считали неудачником, но...' \n"
        "- Контраст: 'Мир запомнил её улыбку. Она сама запомнила голод.'\n"
        "- Цена решения: 'Один приказ. Десятилетия последствий.'\n"
        "- Вопрос: 'Почему человек, которого называли спасителем...?'\n"
        "- Скрытая история: 'Официально всё началось в этот день. Но настоящая причина...'\n\n"
        "ВАЖНО: заголовок НЕ должен начинаться с даты! Дата — не крючок.\n"
        "Заголовок должен цеплять, но НЕ обманывать.\n\n"
        "Верни JSON:\n"
        '{ "headlines": [ {"text": str, "style": str, "char_count": int} ] }'
    ),
    "image_prompt_writer": (
        "Ты — промт-инженер для иллюстраций проекта «Задним числом».\n\n"
        "Статья: \"{{ article.title_working }}\"\n"
        "TL;DR: {{ article.tldr }}\n\n"
        "Создай {{ params.images_per_article }} промтов для генерации иллюстрации.\n"
        "Стиль: кинематографичный, драматичный, исторический.\n"
        "Формат: 16:9.\n"
        "Описывай СЦЕНУ, а не текст — генератор картинок не умеет рисовать буквы.\n"
        "Промт должен содержать: объект, обстановку, освещение, настроение, эпоху.\n\n"
        "Верни JSON:\n"
        '{ "image_prompts": [ {"prompt": str, "negative": str, "aspect": "16:9", "seed": null} ] }'
    ),
    "qa_editorial": (
        "Ты — главный редактор проекта «Задним числом».\n\n"
        "Проверь статью по критериям:\n"
        "1. Начинается ли текст с крючка (НЕ с даты)?\n"
        "2. Есть ли герой, конфликт, ставки?\n"
        "3. Ощущается ли как живой рассказ, а не справка?\n"
        "4. Нет ли канцелярита, 'воды', штампов?\n"
        "5. Есть ли неожиданная деталь?\n"
        "6. Есть ли связь с современностью?\n"
        "7. Сильный ли финал?\n\n"
        "Выбери лучший заголовок из вариантов.\n"
        "Если текст 'сухой' — добавь жизни минимальными правками.\n\n"
        "Статья:\n{{ article.body_md }}\n\n"
        "Варианты заголовков:\n{{ headlines }}\n\n"
        "Верни JSON:\n"
        '{ "chosen_headline": str, "revised_body_md": str, '
        '"issues":[{"severity":"low|med|high","note":str}], "score": int }'
    ),
    "qa_visual": (
        "Выбери лучшую картинку для статьи «{{ article.title_working }}» проекта «Задним числом».\n"
        "Критерии: драматичность, соответствие эпохе, эмоциональная сила, кинематографичность.\n\n"
        "Варианты:\n{{ image_options }}\n\n"
        "Верни JSON: { \"chosen_index\": int, \"rationale\": str }"
    ),
    "video_scenarist": (
        "Ты — сценарист коротких видео для проекта «Задним числом».\n\n"
        "Из статьи сделай сценарий видео длительностью {{ params.target_duration_s }} секунд.\n"
        "Формат: 9:16 (вертикальное).\n"
        "Стиль: кинематографичный, драматичный, с голосом за кадром.\n\n"
        "Правила:\n"
        "- Первая секунда (hook) — самая сильная фраза, которая остановит скролл.\n"
        "- 5–8 сцен по 4–6 секунд каждая.\n"
        "- Каждая сцена: озвучка + визуал (что показывать).\n"
        "- Последняя сцена: сильная точка, не 'подписывайтесь'.\n\n"
        "Статья:\n{{ article.body_md }}\n\n"
        "Верни JSON:\n"
        '{ "hook": str, "scenes": [ {"idx": int, "voiceover": str, '
        '"on_screen_text": str, "b_roll_idea": str, "duration_s": number} ], "cta": str }'
    ),
}



# ---- Agent model/temperature overrides for "Задним числом" -----------------
# Key: role -> (model, temperature, max_tokens)
# Using GPT-4o for creative/complex tasks, GPT-4o-mini for simpler ones.

ZADNIM_AGENT_CONFIG: dict[str, tuple[str, float, int]] = {
    "topic_generator":      ("openai:gpt-4o", 0.9, 2000),
    "topic_validator":      ("openai:gpt-4o", 0.3, 2000),
    "topic_ranker":         ("openai:gpt-4o-mini", 0.2, 1500),
    "researcher":           ("openai:gpt-4o", 0.5, 3000),
    "research_validator":   ("openai:gpt-4o", 0.2, 3000),
    "article_writer":       ("openai:gpt-4o", 0.8, 5000),
    "headline_writer":      ("openai:gpt-4o", 0.9, 1000),
    "image_prompt_writer":  ("openai:gpt-4o-mini", 0.7, 800),
    "qa_editorial":         ("openai:gpt-4o", 0.3, 3000),
    "qa_visual":            ("openai:gpt-4o-mini", 0.2, 400),
    "channel_rewriter":     ("openai:gpt-4o-mini", 0.6, 2000),
    "video_scenarist":      ("openai:gpt-4o", 0.7, 2500),
}

# Agent params overrides
ZADNIM_AGENT_PARAMS: dict[str, dict[str, Any]] = {
    "topic_generator": {
        "topics_per_run": 8,
        "send_today_date": True,
        "memory_lookback_days": 60,
    },
    "topic_validator": {
        "confirmed_topics_target": 5,
        "max_retries": 3,
    },
    "topic_ranker": {
        "criteria_weights": {
            "hook": 0.25, "drama": 0.25, "novelty": 0.2,
            "virality": 0.2, "modern_link": 0.1,
        },
        "top_n_to_keep": 10,
    },
    "researcher": {"min_facts": 10},
    "research_validator": {"strictness": "high"},
    "article_writer": {
        "target_chars": 8000,
        "tone": "живой, кинематографичный, без канцелярита, с драматургией",
    },
    "headline_writer": {
        "headlines_per_article": 5,
        "styles": ["парадокс", "контраст", "цена_решения", "вопрос", "скрытая_история"],
    },
    "image_prompt_writer": {
        "images_per_article": 2,
        "style_preset": "cinematic_historical",
        "image_model": "302ai:flux-pro",
    },
    "qa_editorial": {"min_score": 75},
    "qa_visual": {},
    "video_scenarist": {
        "target_duration_s": 45,
        "aspect": "9:16",
        "style_preset": "cinematic_narrator",
    },
}


# ---- Main seed function ---------------------------------------------------

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
        project_slug = db.unique_slug(conn, "projects", db.slugify(DEMO_PROJECT_NAME))
        conn.execute(
            "INSERT INTO projects (id, user_id, slug, name, niche, description, is_enabled, "
            "timezone, language_modes, daily_topics_target, daily_articles_target, "
            "budget_usd_month, style_guide, created_at, updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, user_id, project_slug, DEMO_PROJECT_NAME,
                "история / события дня / культура / судьбы людей",
                "Каждый день берём сегодняшнюю дату и находим события, которые произошли "
                "в этот день в разные годы. Превращаем их в живые, цепляющие истории "
                "с героем, конфликтом и неожиданными деталями.",
                1, "Europe/Moscow", json.dumps(["ru", "en"]),
                8, 3, 80.0,
                "Тон — живой, умный, кинематографичный. Без академической сухости, "
                "без канцелярита, без воды. Текст должен ощущаться как рассказ хорошего "
                "рассказчика. Короткие абзацы. Начинать с крючка, не с даты. "
                "Финал — сильная смысловая точка.",
                db.now_iso(), db.now_iso(),
            ),
        )

        # --- Create agents with project-specific prompts ---
        for entry in default_agents_for_project(["ru", "en"]):
            spec = entry["spec"]
            role = entry["role"]
            lang = entry["language"]

            # Use project-specific prompt if role is in ZADNIM_PROMPTS
            if role in ZADNIM_PROMPTS and lang in ("ru", "bi"):
                prompt = ZADNIM_PROMPTS[role]
            else:
                prompt = spec.prompt_template

            model, temp, max_tok = ZADNIM_AGENT_CONFIG.get(
                role, (spec.default_model, spec.default_temperature, spec.default_max_tokens)
            )
            params = ZADNIM_AGENT_PARAMS.get(role, spec.default_params)

            # Slug: role-language; for global agents (bi) -- just role.
            base_slug = role.replace("_", "-")
            if lang in ("ru", "en"):
                base_slug += "-" + lang
            slug = db.unique_slug(conn, "agents", base_slug,
                                  scope_col="project_id", scope_val=project_id)

            conn.execute(
                "INSERT INTO agents (id, project_id, slug, role, display_name, description, "
                "model, temperature, max_tokens, top_p, prompt_template, params, tools_enabled, "
                "language, is_enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    db.new_id("ag_"), project_id, slug, role, entry["display_name"],
                    spec.description, model, temp, max_tok, 1.0, prompt,
                    json.dumps(params), json.dumps(list(spec.tools)),
                    lang, 1, db.now_iso(), db.now_iso(),
                ),
            )

        # --- Create channels with per-channel rewriter prompts ---
        for ch in DEMO_CHANNELS:
            spec = CHANNEL_KINDS[ch["kind"]]
            channel_id = db.new_id("c_")
            ch_slug = db.unique_slug(conn, "channels", ch["kind"] + "-" + ch["language"],
                                     scope_col="project_id", scope_val=project_id)
            creds_enc = encrypt(json.dumps(ch.get("creds") or {}), settings.master_key)
            rewriter_id = None

            if not spec.is_video:
                rspec = role_spec("channel_rewriter")
                rewriter_id = db.new_id("ag_")
                custom_prompt = REWRITER_PROMPTS.get(ch["kind"], rspec.prompt_template)
                model, temp, max_tok = ZADNIM_AGENT_CONFIG.get(
                    "channel_rewriter",
                    (rspec.default_model, rspec.default_temperature, rspec.default_max_tokens),
                )
                rewriter_slug = db.unique_slug(
                    conn, "agents", "rewriter-" + ch["kind"],
                    scope_col="project_id", scope_val=project_id,
                )
                conn.execute(
                    "INSERT INTO agents (id, project_id, slug, role, display_name, description, "
                    "model, temperature, max_tokens, top_p, prompt_template, params, "
                    "tools_enabled, language, channel_id, is_enabled, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rewriter_id, project_id, rewriter_slug, "channel_rewriter",
                        f"Адаптер — {ch['name']}",
                        f"Переписывает статью под формат {spec.label} ({spec.max_chars} симв.)",
                        model, temp, max_tok, 1.0, custom_prompt,
                        json.dumps({"style_voice": "Живой, кинематографичный, без штампов."}),
                        json.dumps([]), ch["language"], channel_id, 1,
                        db.now_iso(), db.now_iso(),
                    ),
                )

            conn.execute(
                "INSERT INTO channels (id, project_id, slug, kind, name, language, is_video, "
                "posts_per_day, selection_strategy, rewriter_prompt, rewriter_agent_id, "
                "credentials_enc, is_enabled, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    channel_id, project_id, ch_slug, ch["kind"], ch["name"], ch["language"],
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
