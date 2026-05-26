"""Agent role catalog. One source of truth for prompts, params and JSON schemas.

Each project gets one agent of each role at creation time (except
``channel_rewriter`` which is created per text channel). Settings can be
edited in the UI without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentRoleSpec:
    role: str
    display_name: str
    description: str
    default_model: str
    default_temperature: float
    default_max_tokens: int
    prompt_template: str
    default_params: dict[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    output_schema: dict[str, Any] = field(default_factory=dict)
    language_aware: bool = True


# ---- Prompt templates -----------------------------------------------------

# Topic generator: bilingual prompt that adapts via {{ language }}
TOPIC_GENERATOR_TMPL = """\
{% if language == 'ru' %}
Ты — генератор тем для канала про "{{ project.niche }}".
{% if params.send_today_date %}Сегодня: {{ today }}.{% endif %}
Предложи {{ params.topics_per_run }} новых, актуальных тем.
Используй web_search для проверки свежести.

Запрещённые темы (уже писали или уже подтверждены в этом прогоне):
{% for t in forbidden_topics %}- {{ t }}
{% endfor %}

Style guide проекта:
{{ project.style_guide | default('') }}

Верни строго JSON:
{ "topics": [ {"title": str, "angle": str, "why_now": str, "tentative_sources": [str]} ] }
{% else %}
You generate topics for a channel about "{{ project.niche }}".
{% if params.send_today_date %}Today is {{ today }}.{% endif %}
Propose {{ params.topics_per_run }} fresh, timely topics.
Use web_search to verify novelty.

Forbidden topics (already covered or already confirmed this run):
{% for t in forbidden_topics %}- {{ t }}
{% endfor %}

Project style guide:
{{ project.style_guide | default('') }}

Return strictly JSON:
{ "topics": [ {"title": str, "angle": str, "why_now": str, "tentative_sources": [str]} ] }
{% endif %}
"""

TOPIC_VALIDATOR_TMPL = """\
{% if language == 'ru' %}
Ты — фактчекер. Для каждой темы из списка проверь актуальность и достоверность.
Расширь summary, добавь надёжные источники, и пометь is_valid.
Целевое число подтверждённых: {{ params.confirmed_topics_target }}.

Кандидаты:
{% for t in candidate_topics %}- {{ t.title }}: {{ t.angle }}
{% endfor %}

Верни строго JSON:
{ "validated": [ {"title": str, "summary_extended": str, "sources": [str], "is_valid": bool, "reject_reason": str|null} ],
  "missing_count": int }
{% else %}
You are a fact checker. For each candidate topic verify timeliness and accuracy.
Expand summary, add credible sources, mark is_valid.
Target confirmed count: {{ params.confirmed_topics_target }}.

Candidates:
{% for t in candidate_topics %}- {{ t.title }}: {{ t.angle }}
{% endfor %}

Return strictly JSON:
{ "validated": [ {"title": str, "summary_extended": str, "sources": [str], "is_valid": bool, "reject_reason": str|null} ],
  "missing_count": int }
{% endif %}
"""

TOPIC_RANKER_TMPL = """\
{% if language == 'ru' %}
Ты — редактор. Оцени каждую тему по 5 критериям 0..10:
relevance, novelty, virality, evergreen, sources_quality.
Веса: {{ params.criteria_weights }}.

Темы:
{% for t in validated_topics %}- {{ t.title }}: {{ t.summary_extended }}
{% endfor %}

Верни JSON:
{ "ranked": [ {"title": str, "scores": object, "score_total": number, "rationale": str} ] }
{% else %}
You are an editor. Score each topic on 5 criteria 0..10:
relevance, novelty, virality, evergreen, sources_quality.
Weights: {{ params.criteria_weights }}.

Topics:
{% for t in validated_topics %}- {{ t.title }}: {{ t.summary_extended }}
{% endfor %}

Return JSON:
{ "ranked": [ {"title": str, "scores": object, "score_total": number, "rationale": str} ] }
{% endif %}
"""

RESEARCHER_TMPL = """\
{% if language == 'ru' %}
Ты — исследователь. Собери досье по теме: "{{ topic.title }}".
Контекст: {{ topic.summary_extended }}.
Минимум фактов: {{ params.min_facts | default(8) }}.

Верни JSON:
{ "facts":[{"claim":str,"source":str}], "stats":[{"value":str,"context":str,"source":str}],
  "quotes":[{"who":str,"what":str,"source":str}], "open_questions":[str] }
{% else %}
You are a researcher. Build a brief on topic: "{{ topic.title }}".
Context: {{ topic.summary_extended }}.
At least {{ params.min_facts | default(8) }} facts.

Return JSON:
{ "facts":[{"claim":str,"source":str}], "stats":[{"value":str,"context":str,"source":str}],
  "quotes":[{"who":str,"what":str,"source":str}], "open_questions":[str] }
{% endif %}
"""

RESEARCH_VALIDATOR_TMPL = """\
{% if language == 'ru' %}
Ты — старший фактчекер. Перепроверь досье и перепиши его, ИСПРАВИВ ошибки. Не пиши лог ошибок —
просто верни исправленную версию в том же формате.
{% else %}
You are a senior fact checker. Re-verify the brief and rewrite it with errors fixed.
Do not output an error log – just return the corrected version in the same format.
{% endif %}
Brief:
{{ research_brief }}
"""

ARTICLE_WRITER_TMPL = """\
{% if language == 'ru' %}
Ты — автор статей про "{{ project.niche }}". Напиши большую статью на тему "{{ topic.title }}"
длиной около {{ params.target_chars | default(8000) }} символов. Тон: {{ params.tone | default('экспертный, доступный') }}.
Включи блоки: вступление, факты, примеры, выводы, CTA.
{% else %}
You are a writer for "{{ project.niche }}". Write a long article on topic "{{ topic.title }}"
about {{ params.target_chars | default(8000) }} characters. Tone: {{ params.tone | default('expert and accessible') }}.
Include: intro, facts, examples, conclusion, CTA.
{% endif %}

Validated research:
{{ research_validated }}

Return JSON:
{ "title_working": str, "body_md": str, "key_points": [str], "tldr": str, "tags": [str] }
"""

HEADLINE_WRITER_TMPL = """\
{% if language == 'ru' %}
Сгенерируй {{ params.headlines_per_article }} заголовков для статьи "{{ article.title_working }}".
Стили: {{ params.styles }}.
{% else %}
Generate {{ params.headlines_per_article }} headlines for article "{{ article.title_working }}".
Styles: {{ params.styles }}.
{% endif %}
Return JSON: { "headlines": [ {"text": str, "style": str, "char_count": int} ] }
"""

IMAGE_PROMPT_WRITER_TMPL = """\
{% if language == 'ru' %}
Сделай {{ params.images_per_article }} промтов для иллюстрации статьи. Стиль: {{ params.style_preset }}, формат 16:9.
{% else %}
Create {{ params.images_per_article }} image prompts for the article. Style: {{ params.style_preset }}, aspect 16:9.
{% endif %}
Article TLDR: {{ article.tldr }}.
Return JSON: { "image_prompts": [ {"prompt": str, "negative": str, "aspect": str, "seed": null} ] }
"""

QA_EDITORIAL_TMPL = """\
{% if language == 'ru' %}
Ты — главный редактор. Проверь статью, выбери один заголовок из вариантов, дай минимальные правки.
{% else %}
You are an editor-in-chief. Review the article, pick one of the headlines, and apply minimal edits.
{% endif %}
Article: {{ article.body_md }}
Headlines: {{ headlines }}
Return JSON: { "chosen_headline": str, "revised_body_md": str, "issues":[{"severity":str,"note":str}], "score": int }
"""

# Fact audit: post-writer factcheck against the validated research brief.
# Generic registry-level template. Real prompt for "Задним числом" lives in
# seed.py (ZADNIM_PROMPTS["fact_audit"]). This stub only documents the shape
# so a project without a custom prompt still produces a valid JSON result.
FACT_AUDIT_TMPL = """\
{% if language == 'ru' %}
Ты — фактчекер. Проверь, что каждое имя/цифра/цитата из article.body_md
встречается в research_validated. Удали или перефразируй то, чего там нет.
{% else %}
You are a fact-checker. Verify that every name/number/quote in
article.body_md is present in research_validated. Drop or rephrase
anything that isn't.
{% endif %}
Article: {{ article.body_md }}
Research validated: {{ research_validated }}
Return JSON: { "unsupported_claims":[{"claim":str,"fragment":str,"severity":str}],
"fixed_body_md": str, "score": int, "must_fix": bool }
"""

QA_VISUAL_TMPL = """\
{% if language == 'ru' %}Выбери лучшую картинку из вариантов для статьи "{{ article.title_working }}".
{% else %}Pick the best image variant for article "{{ article.title_working }}".
{% endif %}
Variants: {{ image_options }}
Return JSON: { "chosen_index": int, "rationale": str }
"""

CHANNEL_REWRITER_TMPL = """\
{% if language == 'ru' %}
Перепиши статью в пост для канала "{{ channel.name }}" ({{ channel.kind }}).
Лимит символов: {{ channel.max_chars }}. Стиль/голос: {{ params.style_voice | default('') }}.
Заголовок и картинку использовать без изменений.
{% else %}
Rewrite the article into a post for channel "{{ channel.name }}" ({{ channel.kind }}).
Char limit: {{ channel.max_chars }}. Style/voice: {{ params.style_voice | default('') }}.
Keep the headline and image unchanged.
{% endif %}
Article: {{ article.body_md }}
Return JSON: { "post_body": str, "hashtags":[str], "first_comment": str|null }
"""

VIDEO_SCENARIST_TMPL = """\
{% if language == 'ru' %}
Сделай сценарий короткого видео ({{ params.target_duration_s }}s) на основе статьи.
Раздели на 5-9 сцен, каждая 4-7 секунд.
{% else %}
Build a short video script ({{ params.target_duration_s }}s) from the article.
Split into 5-9 scenes, 4-7 seconds each.
{% endif %}
Article: {{ article.body_md }}
Return JSON: { "hook": str, "scenes": [ {"idx": int, "voiceover": str, "on_screen_text": str, "b_roll_idea": str, "duration_s": number} ], "cta": str }
"""

# Video keyframe artist: scenes -> per-scene image_prompt (still) and i2v_prompt
# (motion). Bilingual prompt; English is fine since image/video models prefer it.
VIDEO_KEYFRAME_ARTIST_TMPL = """\
{% if language == 'ru' %}
Для каждой сцены сценария напиши два промта:
  - image_prompt: статичный кадр (на английском, как у image_prompt_writer);
  - i2v_prompt: как этот кадр должен двигаться (slow zoom in, camera pans left, и т.п.).
Стиль анимации: {{ params.style_preset }}.
{% else %}
For each scenario scene, write two prompts:
  - image_prompt: a still frame description (English, like image_prompt_writer);
  - i2v_prompt: how the frame should move (slow zoom in, camera pans left, etc.).
Animation style: {{ params.style_preset }}.
{% endif %}
Scenes: {{ scenes }}
Article TLDR: {{ article.tldr }}
Return JSON: { "keyframes": [ {"idx": int, "image_prompt": str, "i2v_prompt": str} ] }
"""

# Voice director: takes raw voiceover lines and normalises them for TTS —
# tempo, SSML pauses, length cap per scene.duration_s.
VOICE_DIRECTOR_TMPL = """\
{% if language == 'ru' %}
Подгони закадровый текст под длительность каждой сцены.
Расставь SSML-паузы (<break time="300ms"/>) на естественных границах.
Сократи текст, если не влезает в scene.duration_s (≈15 знаков/секунду).
Голос: {{ params.voice_id }}, скорость {{ params.speed }}.
{% else %}
Adjust the voiceover to fit each scene's duration.
Insert SSML breaks (<break time="300ms"/>) at natural boundaries.
Trim text if it does not fit scene.duration_s (≈15 chars/sec).
Voice: {{ params.voice_id }}, speed {{ params.speed }}.
{% endif %}
Scenes: {{ scenes }}
Return JSON: { "scenes_normalized": [ {"idx": int, "voiceover_normalized": str, "estimated_duration_s": number} ] }
"""

# Subtitle styler: SRT (from Whisper) -> ASS with project styling.
SUBTITLE_STYLER_TMPL = """\
{% if language == 'ru' %}
Преобразуй SRT-субтитры в ASS со стилями:
шрифт {{ params.font }}, размер {{ params.font_size }}, цвет {{ params.color }},
положение {{ params.position }}.
{% if params.highlight_keywords %}Ключевые слова — выделять цветом.{% endif %}
{% else %}
Convert SRT subtitles to ASS with styling:
font {{ params.font }}, size {{ params.font_size }}, color {{ params.color }},
position {{ params.position }}.
{% if params.highlight_keywords %}Highlight keywords with colour.{% endif %}
{% endif %}
SRT input:
{{ srt_text }}
Return JSON: { "ass_text": str }
"""

# Video assembler: non-LLM orchestrator. Prompt template is a stub that
# documents the role; the actual stitching happens in tools/video_assembler.py.
VIDEO_ASSEMBLER_TMPL = "Video assembler is a non-LLM orchestrator. No prompt is sent."

# ---- Mock generators (registered in llm.adapter via decorator) ------------

from ..llm.adapter import register


@register("topic_generator")
def _gen_topic_generator(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    n = int(params.get("topics_per_run", 5))
    niche = inputs.get("project", {}).get("niche", "AI")
    forbidden = set(inputs.get("forbidden_topics", []) or [])
    seeds_ru = [
        "Тренды", "Кейс", "Инструмент", "Сравнение", "Гайд",
        "Ошибки", "Этика", "Безопасность", "Прогноз", "История", "Интервью", "Бенчмарк",
    ]
    seeds_en = [
        "Trends", "Case study", "Tool review", "Comparison", "Guide",
        "Mistakes", "Ethics", "Safety", "Forecast", "History", "Interview", "Benchmark",
    ]
    seeds = seeds_ru if language == "ru" else seeds_en
    topics = []
    i = 0
    while len(topics) < n and i < n + len(forbidden) + 3:
        seed = seeds[i % len(seeds)]
        if language == "ru":
            title = f"{seed}: {niche} в 2026 #{i+1}"
            angle = f"Свежий взгляд на {niche.lower()} в контексте {seed.lower()}"
            why = "Тема обсуждается прямо сейчас, есть свежие источники"
        else:
            title = f"{seed}: {niche} in 2026 #{i+1}"
            angle = f"Fresh take on {niche.lower()} from a {seed.lower()} angle"
            why = "Trending right now with fresh sources"
        if title not in forbidden:
            topics.append({
                "title": title,
                "angle": angle,
                "why_now": why,
                "tentative_sources": [
                    f"https://mock.example/{i}/a",
                    f"https://mock.example/{i}/b",
                ],
            })
        i += 1
    return {"topics": topics}


@register("topic_validator")
def _gen_topic_validator(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    target = int(params.get("confirmed_topics_target", 5))
    cands = inputs.get("candidate_topics", []) or []
    validated = []
    for c in cands:
        # mark 80% valid deterministically (every 5th rejected)
        idx = sum(ord(ch) for ch in c.get("title", "")) % 5
        is_valid = idx != 0
        reason = None if is_valid else ("Источники сомнительны" if language == "ru" else "Sources look weak")
        summary = c.get("angle", "") + (
            ". Дополнительный контекст из проверенных источников."
            if language == "ru"
            else ". Additional context from verified sources."
        )
        validated.append({
            "title": c.get("title"),
            "summary_extended": summary,
            "sources": c.get("tentative_sources", [])[:3]
                        + [f"https://mock.example/verify/{abs(hash(c.get('title',''))) % 1000}"],
            "is_valid": is_valid,
            "reject_reason": reason,
        })
    confirmed = [v for v in validated if v["is_valid"]]
    missing = max(0, target - len(confirmed))
    return {"validated": validated, "missing_count": missing}


@register("topic_ranker")
def _gen_topic_ranker(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    weights = params.get("criteria_weights") or {
        "relevance": 0.3, "novelty": 0.25, "virality": 0.2,
        "evergreen": 0.1, "sources_quality": 0.15,
    }
    ranked = []
    for t in inputs.get("validated_topics", []) or []:
        seed = sum(ord(ch) for ch in t.get("title", "")) or 1
        scores = {
            "relevance": 5 + seed % 5,
            "novelty": 4 + (seed // 3) % 6,
            "virality": 3 + (seed // 5) % 7,
            "evergreen": 4 + (seed // 7) % 6,
            "sources_quality": 5 + (seed // 11) % 5,
        }
        total = round(sum(scores[k] * weights.get(k, 0) for k in scores), 2)
        ranked.append({
            "title": t.get("title"),
            "scores": scores,
            "score_total": total,
            "rationale": (
                "Хороший баланс новизны и охвата" if language == "ru"
                else "Solid balance of novelty and reach"
            ),
        })
    ranked.sort(key=lambda r: r["score_total"], reverse=True)
    return {"ranked": ranked}


@register("researcher")
def _gen_researcher(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    topic = inputs.get("topic", {})
    n = int(params.get("min_facts", 8))
    facts = []
    for i in range(n):
        if language == "ru":
            facts.append({
                "claim": f"Факт {i+1} по теме '{topic.get('title','')}'",
                "source": f"https://mock.example/fact/{i+1}",
            })
        else:
            facts.append({
                "claim": f"Fact {i+1} about '{topic.get('title','')}'",
                "source": f"https://mock.example/fact/{i+1}",
            })
    stats = [{
        "value": "42%",
        "context": ("Доля компаний, принявших решение в 2025"
                    if language == "ru" else "Share of companies that adopted in 2025"),
        "source": "https://mock.example/stat/1",
    }]
    quotes = [{
        "who": "Andrej Karpathy",
        "what": ("Software 2.0 это про данные."
                 if language == "ru" else "Software 2.0 is about data."),
        "source": "https://mock.example/quote/1",
    }]
    return {
        "facts": facts,
        "stats": stats,
        "quotes": quotes,
        "open_questions": [
            "Что дальше через 12 месяцев?" if language == "ru"
            else "What's next in 12 months?"
        ],
    }


@register("research_validator")
def _gen_research_validator(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    brief = inputs.get("research_brief") or {}
    # Pretend we corrected a minor stat. Same shape as researcher output.
    fixed = dict(brief)
    if fixed.get("stats"):
        fixed["stats"] = list(fixed["stats"])
        first = dict(fixed["stats"][0])
        first["context"] = (first.get("context", "") + " (verified)")
        fixed["stats"][0] = first
    return fixed


@register("article_writer")
def _gen_article_writer(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    topic = inputs.get("topic", {})
    title = topic.get("title", "Article")
    target = int(params.get("target_chars", 6000))
    if language == "ru":
        intro = f"## Вступление\n\nВ этой статье разберёмся с темой: {title}.\n\n"
        body = (
            "## Контекст\n\nКороткий, но насыщенный обзор предметной области.\n\n"
            "## Факты\n\n- Ключевой факт 1\n- Ключевой факт 2\n- Ключевой факт 3\n\n"
            "## Примеры\n\nПример 1, пример 2.\n\n"
            "## Выводы\n\nГлавная мысль и практические шаги.\n\n"
            "## Что делать дальше\n\nПодпишитесь на канал, чтобы не пропустить.\n"
        )
        tldr = f"Кратко: что нужно знать про '{title}' за 60 секунд."
        tags = ["AI", "продуктивность", "тренды"]
    else:
        intro = f"## Intro\n\nThis article unpacks: {title}.\n\n"
        body = (
            "## Context\n\nA short but dense overview.\n\n"
            "## Facts\n\n- Fact 1\n- Fact 2\n- Fact 3\n\n"
            "## Examples\n\nExample 1, example 2.\n\n"
            "## Takeaways\n\nMain point and what to do next.\n\n"
            "## CTA\n\nSubscribe to keep up.\n"
        )
        tldr = f"In 60 seconds: what you need to know about '{title}'."
        tags = ["AI", "productivity", "trends"]
    text = intro + body
    # Pad up to roughly target_chars to make it look long
    if len(text) < target:
        filler = "\n" + ("..." if language == "en" else "...") + "\n"
        while len(text) < target:
            text += filler + body
    return {
        "title_working": title,
        "body_md": text,
        "key_points": [
            "Ключевой тезис 1" if language == "ru" else "Key point 1",
            "Ключевой тезис 2" if language == "ru" else "Key point 2",
        ],
        "tldr": tldr,
        "tags": tags,
    }


@register("headline_writer")
def _gen_headline_writer(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    n = int(params.get("headlines_per_article", 5))
    base = inputs.get("article", {}).get("title_working", "Article")
    styles = params.get("styles") or ["clickbait", "neutral", "question", "listicle", "how_to"]
    out = []
    for i in range(n):
        style = styles[i % len(styles)]
        if language == "ru":
            text = {
                "clickbait": f"Это изменит всё: {base}",
                "neutral": f"{base}: разбор",
                "question": f"Стоит ли заниматься «{base}» в 2026?",
                "listicle": f"7 идей про {base}",
                "how_to": f"Как начать с {base} за выходные",
            }.get(style, base)
        else:
            text = {
                "clickbait": f"This changes everything: {base}",
                "neutral": f"{base}: deep dive",
                "question": f"Is {base} worth your time in 2026?",
                "listicle": f"7 ideas about {base}",
                "how_to": f"How to start with {base} this weekend",
            }.get(style, base)
        out.append({"text": text, "style": style, "char_count": len(text)})
    return {"headlines": out}


@register("image_prompt_writer")
def _gen_image_prompt_writer(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    n = int(params.get("images_per_article", 2))
    base = inputs.get("article", {}).get("title_working", "Article")
    style = params.get("style_preset", "editorial")
    prompts = []
    for i in range(n):
        prompts.append({
            "prompt": f"editorial illustration about '{base}', style={style}, variant {i+1}",
            "negative": "lowres, watermark, signature",
            "aspect": "16:9",
            "seed": None,
        })
    return {"image_prompts": prompts}


@register("qa_editorial")
def _gen_qa_editorial(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    headlines = inputs.get("headlines", []) or []
    chosen = headlines[0]["text"] if headlines else "Untitled"
    body = inputs.get("article", {}).get("body_md", "")
    return {
        "chosen_headline": chosen,
        "revised_body_md": body,  # mock: minimal edits
        "issues": [],
        "score": 92,
    }


@register("fact_audit")
def _gen_fact_audit(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    """Mock fact_audit: returns the body unchanged with a clean score.

    Real (LLM-driven) implementation walks the article body, extracts
    claims (names, numbers, direct quotes, dates) and verifies each one
    against ``research_validated`` (facts/stats/quotes/hero/...).
    The mock here returns no findings so the rest of the pipeline keeps
    working in unit tests."""
    article = inputs.get("article", {}) or {}
    body = article.get("body_md", "")
    return {
        "unsupported_claims": [],
        "score": 95,
        "must_fix": False,
        "fixed_body_md": body,
    }


@register("qa_visual")
def _gen_qa_visual(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    options = inputs.get("image_options", []) or []
    return {
        "chosen_index": 0 if options else -1,
        "rationale": ("Лучшая композиция и контраст" if language == "ru"
                      else "Best composition and contrast"),
    }


@register("channel_rewriter")
def _gen_channel_rewriter(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    article = inputs.get("article", {})
    channel = inputs.get("channel", {})
    body = article.get("body_md", "")
    limit = int(channel.get("max_chars", 1000))
    snippet = body[: min(limit, max(120, len(body)))]
    if len(snippet) < len(body):
        snippet = snippet.rsplit(" ", 1)[0] + ("…" if language == "ru" else "…")
    if language == "ru":
        post = f"{snippet}\n\n👉 Полная версия в канале."
        tags = ["#ai", "#тренды"]
    else:
        post = f"{snippet}\n\n👉 Full article in the channel."
        tags = ["#ai", "#trends"]
    return {"post_body": post, "hashtags": tags, "first_comment": None}


@register("video_scenarist")
def _gen_video_scenarist(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    target = int(params.get("target_duration_s", 30))
    n = max(5, min(9, target // 5))
    scenes = []
    base_title = inputs.get("article", {}).get("title_working", "Story")
    for i in range(n):
        if language == "ru":
            scenes.append({
                "idx": i + 1,
                "voiceover": f"Сцена {i+1}: ключевой тезис о {base_title}.",
                "on_screen_text": f"Тезис {i+1}",
                "b_roll_idea": "крупный план, яркий контраст",
                "duration_s": round(target / n, 2),
            })
        else:
            scenes.append({
                "idx": i + 1,
                "voiceover": f"Scene {i+1}: key point about {base_title}.",
                "on_screen_text": f"Point {i+1}",
                "b_roll_idea": "close-up, high contrast",
                "duration_s": round(target / n, 2),
            })
    return {
        "hook": ("Вы не поверите, что меняется в 2026." if language == "ru"
                 else "You won't believe what's changing in 2026."),
        "scenes": scenes,
        "cta": ("Подпишитесь, чтобы не пропустить." if language == "ru"
                else "Subscribe to keep up."),
    }


@register("video_keyframe_artist")
def _gen_video_keyframe_artist(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    style = params.get("style_preset", "cinematic_historical")
    scenes = inputs.get("scenes", []) or []
    base_title = inputs.get("article", {}).get("title_working", "Story")
    keyframes: list[dict[str, Any]] = []
    motions = [
        "slow zoom in", "slow zoom out", "camera pans left",
        "camera pans right", "subtle parallax", "tilt up slowly",
    ]
    for i, sc in enumerate(scenes):
        idx = int(sc.get("idx", i + 1))
        b_roll = sc.get("b_roll_idea") or "scene"
        keyframes.append({
            "idx": idx,
            "image_prompt": (
                f"cinematic still frame for '{base_title}', scene {idx}: "
                f"{b_roll}, style={style}, aspect 9:16, photoreal, "
                f"shallow depth of field"
            ),
            "i2v_prompt": motions[i % len(motions)],
        })
    return {"keyframes": keyframes}


@register("voice_director")
def _gen_voice_director(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    scenes = inputs.get("scenes", []) or []
    out: list[dict[str, Any]] = []
    # Approximate speech rate: 15 chars/sec narration speed.
    chars_per_sec = 15.0
    for i, sc in enumerate(scenes):
        idx = int(sc.get("idx", i + 1))
        duration = float(sc.get("duration_s") or 5.0)
        text = (sc.get("voiceover") or "").strip()
        max_chars = int(duration * chars_per_sec)
        if max_chars > 0 and len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        # Insert one SSML break at the midpoint if the text has a natural
        # boundary (sentence end).
        if "." in text and len(text) > 30:
            head, _, tail = text.partition(".")
            text = head + '. <break time="300ms"/>' + tail
        out.append({
            "idx": idx,
            "voiceover_normalized": text or (
                f"Сцена {idx}." if language == "ru" else f"Scene {idx}."
            ),
            "estimated_duration_s": round(min(duration, max(2.0, len(text) / chars_per_sec)), 2),
        })
    return {"scenes_normalized": out}


@register("subtitle_styler")
def _gen_subtitle_styler(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    """Mock SRT -> ASS conversion. Returns a minimal but valid ASS body."""
    srt_text = inputs.get("srt_text") or ""
    font = params.get("font", "Inter")
    font_size = int(params.get("font_size", 54) or 54)
    color = (params.get("color") or "#FFFFFF").lstrip("#")
    # ASS colour is &H<AA><BB><GG><RR> (alpha first, then BGR).
    if len(color) == 6:
        rr, gg, bb = color[0:2], color[2:4], color[4:6]
        ass_color = f"&H00{bb}{gg}{rr}"
    else:
        ass_color = "&H00FFFFFF"
    # Map our position selector to ASS Alignment numbers.
    align_map = {"top_center": 8, "middle": 5, "bottom_center": 2}
    alignment = align_map.get(params.get("position", "bottom_center"), 2)
    # Convert SRT blocks to ASS Dialogue lines (best-effort; mock-grade).
    dialogues: list[str] = []
    blocks = [b.strip() for b in srt_text.strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        ts_line = lines[1]
        text = " ".join(lines[2:]).replace("\n", " ")
        try:
            start_str, end_str = [s.strip() for s in ts_line.split("-->")]
            def _to_ass_ts(s: str) -> str:
                # SRT: HH:MM:SS,mmm -> ASS: H:MM:SS.cc (centiseconds)
                hh, mm, sec = s.split(":")
                ss, ms = sec.replace(",", ".").split(".") if "." in sec else (sec, "0")
                cs = int(round(int(ms.ljust(3, "0")[:3]) / 10))
                return f"{int(hh)}:{mm}:{ss}.{cs:02d}"
            dialogues.append(
                f"Dialogue: 0,{_to_ass_ts(start_str)},{_to_ass_ts(end_str)},"
                f"Default,,0,0,0,,{text}"
            )
        except Exception:
            continue
    if not dialogues:
        dialogues.append("Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,[mock subtitle]")
    ass_text = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},{ass_color},&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,2,1,{alignment},20,20,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        + "\n".join(dialogues) + "\n"
    )
    return {"ass_text": ass_text}


@register("video_assembler")
def _gen_video_assembler(inputs: dict[str, Any], params: dict[str, Any], language: str) -> dict[str, Any]:
    """Non-LLM orchestrator. The actual stitching happens in
    aicrew/tools/video_assembler.py; this mock just confirms the role exists
    so a manual run from the agents UI doesn't crash."""
    scenes = inputs.get("scenes", []) or []
    return {"status": "assembled", "scenes_processed": len(scenes)}


# ---- Role catalog ---------------------------------------------------------

ROLES: dict[str, AgentRoleSpec] = {
    "topic_generator": AgentRoleSpec(
        role="topic_generator",
        display_name="Topic Generator",
        description="Generates fresh topics for the channel using web search.",
        default_model="mock:smart",
        default_temperature=0.8,
        default_max_tokens=1500,
        prompt_template=TOPIC_GENERATOR_TMPL,
        default_params={
            "topics_per_run": 8,
            "send_today_date": True,
            "memory_lookback_days": 30,
            # Live web search (Tavily). Empty = search disabled for this agent.
            # If set, executor renders the template against the same context as
            # the prompt and calls web_search(). Results are prepended to the
            # prompt as a "WEB SEARCH RESULTS" block, AND exposed in the prompt
            # template as the {{ web_research }} list. See aicrew/tools/search.py.
            "search_query_template": "{{ project.niche }} {{ today_md }} {{ language }}",
            "search_depth": "basic",          # 'basic' (1 credit) or 'advanced' (2)
            "search_max_results": 5,
        },
        tools=("web_search",),
    ),
    "topic_validator": AgentRoleSpec(
        role="topic_validator",
        display_name="Topic Validator",
        description="Verifies candidate topics, expands summaries.",
        default_model="mock:smart",
        default_temperature=0.3,
        default_max_tokens=2000,
        prompt_template=TOPIC_VALIDATOR_TMPL,
        default_params={
            "confirmed_topics_target": 5,
            "max_retries": 3,
            # Validator works on already-found URLs from topic_generator,
            # so live search is OFF by default. Set search_query_template
            # to enable extra fact-checking searches per validation pass.
            "search_query_template": "",
            "search_depth": "basic",
            "search_max_results": 3,
        },
        tools=("web_search", "fetch_url"),
    ),
    "topic_ranker": AgentRoleSpec(
        role="topic_ranker",
        display_name="Topic Ranker",
        description="Scores topics by relevance/novelty/virality/evergreen/sources.",
        default_model="mock:cheap",
        default_temperature=0.2,
        default_max_tokens=1500,
        prompt_template=TOPIC_RANKER_TMPL,
        default_params={
            "criteria_weights": {
                "relevance": 0.3, "novelty": 0.25, "virality": 0.2,
                "evergreen": 0.1, "sources_quality": 0.15,
            },
            "top_n_to_keep": 10,
        },
    ),
    "researcher": AgentRoleSpec(
        role="researcher",
        display_name="Researcher",
        description="Builds a fact-rich brief for the topic.",
        default_model="mock:smart",
        default_temperature=0.4,
        default_max_tokens=2500,
        prompt_template=RESEARCHER_TMPL,
        default_params={
            "min_facts": 8,
            # Researcher needs depth, so default to 'advanced' (2 credits).
            # Per-topic search; results are cached for 24h so RU+EN of the
            # same topic share one Tavily call.
            "search_query_template": "{{ topic.title }} {{ topic.event_date | default('') }}",
            "search_depth": "advanced",
            "search_max_results": 6,
        },
        tools=("web_search", "fetch_url"),
    ),
    "research_validator": AgentRoleSpec(
        role="research_validator",
        display_name="Research Validator",
        description="Re-verifies facts and rewrites the brief in place.",
        default_model="mock:smart",
        default_temperature=0.2,
        default_max_tokens=2500,
        prompt_template=RESEARCH_VALIDATOR_TMPL,
        default_params={
            "strictness": "high",
            # Off by default — validates against the same brief content.
            "search_query_template": "",
            "search_depth": "basic",
            "search_max_results": 3,
        },
        tools=("web_search", "fetch_url"),
    ),
    "article_writer": AgentRoleSpec(
        role="article_writer",
        display_name="Article Writer",
        description="Writes the long, universal article from the validated brief.",
        default_model="mock:smart",
        default_temperature=0.7,
        default_max_tokens=4000,
        prompt_template=ARTICLE_WRITER_TMPL,
        default_params={"target_chars": 6000, "tone": "expert and accessible"},
    ),
    "headline_writer": AgentRoleSpec(
        role="headline_writer",
        display_name="Headline Writer",
        description="Generates headline variants in different styles.",
        default_model="mock:cheap",
        default_temperature=0.9,
        default_max_tokens=800,
        prompt_template=HEADLINE_WRITER_TMPL,
        default_params={"headlines_per_article": 5,
                        "styles": ["clickbait", "neutral", "question", "listicle", "how_to"]},
    ),
    "image_prompt_writer": AgentRoleSpec(
        role="image_prompt_writer",
        display_name="Image Prompt Writer",
        description="Writes prompts for the illustration generator.",
        default_model="mock:cheap",
        default_temperature=0.7,
        default_max_tokens=800,
        prompt_template=IMAGE_PROMPT_WRITER_TMPL,
        default_params={"images_per_article": 2, "style_preset": "editorial",
                        "image_model": "mock:placeholder"},
    ),
    "qa_editorial": AgentRoleSpec(
        role="qa_editorial",
        display_name="QA Editorial",
        description="Picks headline, applies minimal edits, returns score.",
        default_model="mock:smart",
        default_temperature=0.3,
        default_max_tokens=2500,
        prompt_template=QA_EDITORIAL_TMPL,
        default_params={"min_score": 80},
    ),
    "fact_audit": AgentRoleSpec(
        role="fact_audit",
        display_name="Fact Audit",
        description=("Post-writer factchecker: verifies every name, number "
                     "and quote in the article body against the validated "
                     "research brief; rewrites unsupported fragments."),
        default_model="mock:smart",
        default_temperature=0.2,
        default_max_tokens=2500,
        prompt_template=FACT_AUDIT_TMPL,
        # min_score=80 is stricter than QA's 75 — the fact-check bar must
        # be higher because a single fabricated fact poisons trust in the
        # whole article, while QA editorial only judges literary quality.
        default_params={"min_score": 80},
        tools=(),
    ),
    "qa_visual": AgentRoleSpec(
        role="qa_visual",
        display_name="QA Visual",
        description="Picks the best image among generated variants.",
        default_model="mock:cheap",
        default_temperature=0.2,
        default_max_tokens=400,
        prompt_template=QA_VISUAL_TMPL,
        default_params={},
    ),
    "channel_rewriter": AgentRoleSpec(
        role="channel_rewriter",
        display_name="Channel Rewriter",
        description="Adapts the article to a specific text channel's format and tone.",
        default_model="mock:cheap",
        default_temperature=0.5,
        default_max_tokens=1500,
        prompt_template=CHANNEL_REWRITER_TMPL,
        default_params={"style_voice": ""},
    ),
    "video_scenarist": AgentRoleSpec(
        role="video_scenarist",
        display_name="Video Scenarist",
        description="Builds a short-form scene-by-scene script for video producers.",
        default_model="mock:smart",
        default_temperature=0.7,
        default_max_tokens=2000,
        prompt_template=VIDEO_SCENARIST_TMPL,
        default_params={"target_duration_s": 30,
                        "style_preset": "cinematic_narrator"},
    ),
    "video_keyframe_artist": AgentRoleSpec(
        role="video_keyframe_artist",
        display_name="Video Keyframe Artist",
        description="Produces per-scene image_prompt + i2v_prompt pairs.",
        default_model="mock:cheap",
        default_temperature=0.6,
        default_max_tokens=1500,
        prompt_template=VIDEO_KEYFRAME_ARTIST_TMPL,
        default_params={"video_model": "302ai:wan2.2-i2v",
                        "style_preset": "cinematic_historical"},
    ),
    "voice_director": AgentRoleSpec(
        role="voice_director",
        display_name="Voice Director",
        description="Normalises voiceover text for TTS: tempo, SSML breaks, length.",
        default_model="mock:smart",
        default_temperature=0.4,
        default_max_tokens=1500,
        prompt_template=VOICE_DIRECTOR_TMPL,
        default_params={"tts_model": "openai:gpt-4o-mini-tts",
                        "voice_id": "onyx",
                        "speed": 1.0},
    ),
    "subtitle_styler": AgentRoleSpec(
        role="subtitle_styler",
        display_name="Subtitle Styler",
        description="Converts SRT (Whisper) into styled ASS subtitles.",
        default_model="mock:cheap",
        default_temperature=0.2,
        default_max_tokens=2000,
        prompt_template=SUBTITLE_STYLER_TMPL,
        default_params={"font": "Inter", "font_size": 54,
                        "color": "#FFFFFF", "position": "bottom_center",
                        "highlight_keywords": True},
    ),
    "video_assembler": AgentRoleSpec(
        role="video_assembler",
        display_name="Video Assembler",
        description="Non-LLM orchestrator that stitches clips, audio and subtitles.",
        default_model="mock:cheap",
        default_temperature=0.0,
        default_max_tokens=200,
        prompt_template=VIDEO_ASSEMBLER_TMPL,
        default_params={},
    ),
}


def list_roles() -> list[AgentRoleSpec]:
    return list(ROLES.values())


def role_spec(role: str) -> AgentRoleSpec:
    if role not in ROLES:
        raise KeyError(role)
    return ROLES[role]


# Roles that exist *per language* (one for RU, one for EN).
# image_prompt_writer and qa_visual are LANGUAGE-NEUTRAL: image prompts are
# written in English regardless of the article language, and image picking
# does not depend on language. Keeping them global cuts agent count and
# guarantees that a topic with N language versions still gets exactly one
# image, attached to all of them.
LANG_SCOPED_ROLES = {"researcher", "research_validator", "article_writer",
                     "headline_writer",
                     "qa_editorial", "fact_audit", "video_scenarist",
                     "voice_director", "subtitle_styler"}

# Roles that exist project-wide (one instance regardless of language).
GLOBAL_ROLES = {"topic_generator", "topic_validator", "topic_ranker",
                "image_prompt_writer", "qa_visual",
                "video_keyframe_artist", "video_assembler"}


def default_agents_for_project(language_modes: list[str]) -> list[dict[str, Any]]:
    """Returns the list of agents that should be created when a project is created.

    ``channel_rewriter`` is created later, per text channel.
    """
    out: list[dict[str, Any]] = []
    for role_name, spec in ROLES.items():
        if role_name == "channel_rewriter":
            continue
        if role_name in LANG_SCOPED_ROLES:
            for lang in language_modes:
                out.append({
                    "role": role_name,
                    "language": lang,
                    "display_name": f"{spec.display_name} ({lang.upper()})",
                    "spec": spec,
                })
        else:
            out.append({
                "role": role_name,
                "language": "bi",
                "display_name": spec.display_name,
                "spec": spec,
            })
    return out


# ---- Teams -----------------------------------------------------------------
# A "team" is a group of agents responsible for one content level + one
# language. The supported team ids are:
#   text_ru / text_en   — editorial team writing articles in that language
#                         (researcher, research_validator, article_writer,
#                          headline_writer, qa_editorial)
#   video_ru / video_en — video production team for that language
#                         (video_scenarist, voice_director, subtitle_styler)
# Plus two "categories" that are NOT teams in the toggle sense:
#   None                — global agents (one per project regardless of lang),
#                         e.g. topic_generator, image_prompt_writer, qa_visual,
#                         video_keyframe_artist, video_assembler
#   "rewriter"          — channel_rewriter (one per text channel)
# Mapping role -> team kind ("text", "video"), or None for globals.
TEAM_KIND_FOR_ROLE: dict[str, str | None] = {
    # text editorial team
    "researcher":          "text",
    "research_validator":  "text",
    "article_writer":      "text",
    "headline_writer":     "text",
    "qa_editorial":        "text",
    "fact_audit":          "text",
    # video production team
    "video_scenarist":     "video",
    "voice_director":      "video",
    "subtitle_styler":     "video",
    # globals (no team)
    "topic_generator":      None,
    "topic_validator":      None,
    "topic_ranker":         None,
    "image_prompt_writer":  None,
    "qa_visual":            None,
    "video_keyframe_artist": None,
    "video_assembler":      None,
    "channel_rewriter":     None,
}


def team_id_for(role: str, language: str | None) -> str | None:
    """Resolve a role+language pair to a team identifier.

    Returns 'text_ru', 'text_en', 'video_ru', 'video_en', or None for
    project-wide (global) roles and for the rewriter (which is per-channel,
    not per-team).
    """
    kind = TEAM_KIND_FOR_ROLE.get(role)
    if kind is None:
        return None
    if language in (None, "", "bi"):
        return None
    return f"{kind}_{language}"


def default_enabled_teams(language_modes: list[str]) -> list[str]:
    """Default ``enabled_teams`` for a project given its languages.

    Every (kind, lang) pair where lang is in ``language_modes`` is enabled
    by default; the user can later toggle individual teams off in the
    project's Settings tab.
    """
    teams: list[str] = []
    for lang in language_modes:
        teams.extend([f"text_{lang}", f"video_{lang}"])
    return teams
