"""User-friendly parameter schemas for agent roles.

Each agent role has a list of editable parameters with Russian labels and
hints. The frontend renders these as proper form fields (number inputs,
checkboxes, selects, sliders) instead of forcing users to edit JSON.

This is the single source of truth for "what knobs does this agent expose".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamField:
    """Description of a single editable parameter."""
    key: str                  # JSON key in agent.params
    label: str                # human-readable label (RU)
    hint: str                 # helper text under the field
    type: str                 # "int", "float", "bool", "string", "text",
                              # "select", "multi_select", "weights"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[dict[str, str]] = field(default_factory=list)  # for select/multi


# ============================================================================
# 1. Генератор тем — параметры из ТЗ:
#    - Количество тем за раз
#    - Отправлять агенту сегодняшнюю дату (чек-бокс)
#    - Отправлять темы за X дней
# ============================================================================
TOPIC_GENERATOR_FIELDS: list[ParamField] = [
    ParamField(
        key="topics_per_run",
        label="Сколько тем генерировать за один запуск",
        hint="Чем больше — тем шире выбор, но дольше работа и выше расход токенов.",
        type="int", default=8, min=1, max=30,
    ),
    ParamField(
        key="send_today_date",
        label="Отправлять агенту сегодняшнюю дату",
        hint="Включите для проектов, где темы привязаны к календарю (как «Задним числом»). Иначе можно выключить.",
        type="bool", default=True,
    ),
    ParamField(
        key="memory_lookback_days",
        label="Помнить опубликованные темы за (дней)",
        hint="Темы, которые уже опубликованы за указанный срок, не будут предложены повторно.",
        type="int", default=60, min=0, max=365,
    ),
]


# ============================================================================
# 2. Проверяльщик тем
#    - Целевое количество подтверждённых тем
#    - Максимум попыток до-выборки
# ============================================================================
TOPIC_VALIDATOR_FIELDS: list[ParamField] = [
    ParamField(
        key="confirmed_topics_target",
        label="Сколько тем должно пройти проверку",
        hint="Если подтверждённых тем меньше — генератор повторит работу с обновлённым списком запретов.",
        type="int", default=5, min=1, max=20,
    ),
    ParamField(
        key="max_retries",
        label="Максимум попыток до-выборки",
        hint="Чтобы не зациклиться, если генератор всё равно не справляется.",
        type="int", default=3, min=1, max=10,
    ),
]


# ============================================================================
# 3. Ранжировщик тем
#    - Веса 5 критериев + сколько лучших оставлять
# ============================================================================
TOPIC_RANKER_FIELDS: list[ParamField] = [
    ParamField(
        key="criteria_weights",
        label="Веса критериев оценки",
        hint="Сумма всех весов = 1.0. Подберите, что важнее для вашего проекта.",
        type="weights",
        default={"hook": 0.25, "drama": 0.25, "novelty": 0.2,
                 "virality": 0.2, "modern_link": 0.1},
        options=[
            {"key": "hook",         "label": "Сила крючка"},
            {"key": "drama",        "label": "Драма / конфликт"},
            {"key": "novelty",      "label": "Новизна"},
            {"key": "virality",     "label": "Виральность"},
            {"key": "modern_link",  "label": "Связь с современностью"},
        ],
    ),
    ParamField(
        key="top_n_to_keep",
        label="Топ-N тем для UI",
        hint="Это мягкая отсечка — на странице тем покажем только лучшие N.",
        type="int", default=10, min=1, max=50,
    ),
]


# ============================================================================
# 4. Исследователь
# ============================================================================
RESEARCHER_FIELDS: list[ParamField] = [
    ParamField(
        key="min_facts",
        label="Минимум фактов в досье",
        hint="Сколько фактов с источниками агент должен собрать как минимум.",
        type="int", default=10, min=3, max=30,
    ),
]


# ============================================================================
# 5. Проверяльщик исследований
# ============================================================================
RESEARCH_VALIDATOR_FIELDS: list[ParamField] = [
    ParamField(
        key="strictness",
        label="Строгость проверки",
        hint="Низкая — пропустит неточности. Высокая — будет дотошно перепроверять каждый факт.",
        type="select", default="high",
        options=[
            {"value": "low",    "label": "Низкая"},
            {"value": "medium", "label": "Средняя"},
            {"value": "high",   "label": "Высокая"},
        ],
    ),
]


# ============================================================================
# 6. Автор статьи
#    - Длина в символах
#    - Тон голоса
# ============================================================================
ARTICLE_WRITER_FIELDS: list[ParamField] = [
    ParamField(
        key="target_chars",
        label="Целевая длина статьи (символов)",
        hint="Это «универсальная» статья, под каждый канал её перепишет адаптер.",
        type="int", default=8000, min=1500, max=20000, step=500,
    ),
    ParamField(
        key="tone",
        label="Тон голоса",
        hint="Кратко опишите стиль: «живой, кинематографичный, без воды» и т.п.",
        type="text", default="живой, кинематографичный, без канцелярита, с драматургией",
    ),
]


# ============================================================================
# 7. Создатель заголовков
#    - Количество заголовков
#    - Стили (множественный выбор)
# ============================================================================
HEADLINE_WRITER_FIELDS: list[ParamField] = [
    ParamField(
        key="headlines_per_article",
        label="Сколько заголовков генерировать",
        hint="QA выберет лучший. Чем больше вариантов — тем шире выбор.",
        type="int", default=5, min=1, max=15,
    ),
    ParamField(
        key="styles",
        label="Стили заголовков",
        hint="Какие приёмы использовать. Можно отметить несколько.",
        type="multi_select",
        default=["парадокс", "контраст", "цена_решения", "вопрос", "скрытая_история"],
        options=[
            {"value": "парадокс",        "label": "Парадокс"},
            {"value": "контраст",        "label": "Контраст"},
            {"value": "цена_решения",    "label": "Цена решения"},
            {"value": "вопрос",          "label": "Вопрос"},
            {"value": "скрытая_история", "label": "Скрытая история"},
            {"value": "неизвестное_имя", "label": "Знакомое имя с другой стороны"},
            {"value": "контрастные_числа", "label": "Контраст чисел"},
            {"value": "clickbait",       "label": "Кликбейт"},
            {"value": "neutral",         "label": "Нейтральный"},
            {"value": "listicle",        "label": "Список"},
            {"value": "how_to",          "label": "Как сделать"},
        ],
    ),
]


# ============================================================================
# 8. Промт-инженер картинок
#    - Количество картинок
#    - Модель генерации
#    - Стилевой пресет
# ============================================================================
IMAGE_PROMPT_WRITER_FIELDS: list[ParamField] = [
    ParamField(
        key="images_per_article",
        label="Сколько картинок генерировать на статью",
        hint="QA выберет лучшую. Если поставить 1 — генерируется только она и берётся без выбора.",
        type="int", default=2, min=1, max=8,
    ),
    ParamField(
        key="image_model",
        label="Модель генерации картинок",
        hint="Какой моделью рисовать. Wan 2.7 — фотореалистичные сцены через 302.ai.",
        type="select", default="302ai:wan2.7-image",
        options=[
            {"value": "302ai:wan2.7-image",   "label": "302.ai · Wan 2.7 Image (рекомендуется)"},
            {"value": "302ai:wan2.5-image",   "label": "302.ai · Wan 2.5 Image"},
            {"value": "302ai:flux-pro",       "label": "302.ai · Flux Pro"},
            {"value": "302ai:flux-schnell",   "label": "302.ai · Flux Schnell (быстрее, дешевле)"},
            {"value": "openai:dall-e-3",      "label": "OpenAI DALL·E 3"},
            {"value": "openai:gpt-image-1",   "label": "OpenAI GPT-Image-1"},
            {"value": "mock:placeholder",     "label": "Заглушка (без API, для тестов)"},
        ],
    ),
    ParamField(
        key="style_preset",
        label="Стилевой пресет",
        hint="Общий визуальный стиль картинок проекта.",
        type="select", default="cinematic_historical",
        options=[
            {"value": "cinematic_historical", "label": "Кинематографичный исторический"},
            {"value": "editorial",            "label": "Редакторская иллюстрация"},
            {"value": "flat",                 "label": "Плоский / минимализм"},
            {"value": "photoreal",            "label": "Фотореализм"},
            {"value": "documentary",          "label": "Документальный"},
            {"value": "noir",                 "label": "Нуар"},
        ],
    ),
]


# ============================================================================
# 9a. QA редакторский
# ============================================================================
QA_EDITORIAL_FIELDS: list[ParamField] = [
    ParamField(
        key="min_score",
        label="Минимальная оценка для прохождения QA",
        hint="0–100. Если оценка ниже — статья помечается как требующая доработки.",
        type="int", default=75, min=0, max=100,
    ),
]


# ============================================================================
# 9c. Фактаудит — пост-писательский фактчек
# ============================================================================
# Проверяет, что каждое имя/цифра/цитата из готовой статьи действительно
# присутствует в research_validated. Пороги строже, чем у qa_editorial,
# потому что один выдуманный факт убивает доверие ко всей статье — а
# qa_editorial судит только литературное качество.
FACT_AUDIT_FIELDS: list[ParamField] = [
    ParamField(
        key="min_score",
        label="Минимальная оценка для прохождения фактаудита",
        hint="0–100. Score = 100 минус penalty по unsupported_claims "
             "(low=2, med=10, high=25). Если ниже — must_fix=true.",
        type="int", default=80, min=0, max=100,
    ),
]


# ============================================================================
# 9b. QA визуальный — нет настраиваемых параметров
# ============================================================================
QA_VISUAL_FIELDS: list[ParamField] = []


# ============================================================================
# 10. Адаптер для канала
# ============================================================================
CHANNEL_REWRITER_FIELDS: list[ParamField] = [
    ParamField(
        key="style_voice",
        label="Голос канала",
        hint="Краткое описание стиля для конкретного канала. Например: «Дружелюбный, с лёгкой иронией».",
        type="text", default="",
    ),
]


# ============================================================================
# 11. Сценарист видео
# ============================================================================
VIDEO_SCENARIST_FIELDS: list[ParamField] = [
    ParamField(
        key="target_duration_s",
        label="Длительность видео (секунд)",
        hint="Оптимум для Shorts/Reels/TikTok: 30 секунд. 15 — для динамичных тизеров, 60 — для развёрнутого сюжета.",
        type="select", default="30",
        options=[
            {"value": "15", "label": "15 секунд"},
            {"value": "30", "label": "30 секунд"},
            {"value": "60", "label": "60 секунд"},
        ],
    ),
    ParamField(
        key="style_preset",
        label="Стиль видео",
        hint="Общая визуальная подача.",
        type="select", default="cinematic_narrator",
        options=[
            {"value": "cinematic_narrator",  "label": "Кинематографичный с голосом за кадром"},
            {"value": "talking_head_b-roll", "label": "Говорящая голова + перебивки"},
            {"value": "kinetic_typography",  "label": "Кинетическая типографика"},
            {"value": "documentary",         "label": "Документальный"},
        ],
    ),
]


# ============================================================================
# 12. Художник ключевых кадров видео
# ============================================================================
VIDEO_KEYFRAME_ARTIST_FIELDS: list[ParamField] = [
    ParamField(
        key="video_model",
        label="Модель генерации видео",
        hint="Из чего собирать движение из ключевого кадра.",
        type="select", default="302ai:wan2.7-i2v",
        options=[
            {"value": "302ai:wan2.7-i2v",
             "label": "302.ai · Wan 2.7 i2v 720P ($0.10/сек) — рекомендуется"},
            {"value": "302ai:wan2.2-i2v-flash",
             "label": "302.ai · Wan 2.2 i2v Flash 720P ($0.04/сек) — самый дешёвый"},
            {"value": "302ai:wan2.2-i2v-plus",
             "label": "302.ai · Wan 2.2 i2v Plus 1080P ($0.15/сек)"},
            {"value": "302ai:wan2.6-i2v",
             "label": "302.ai · Wan 2.6 i2v 720P ($0.10/сек) — multi-shot"},
            {"value": "302ai:wan2.5-i2v-preview",
             "label": "302.ai · Wan 2.5 i2v Preview 720P ($0.10/сек)"},
            {"value": "302ai:wanx2.1-i2v-turbo",
             "label": "302.ai · Wanx 2.1 i2v Turbo 720P ($0.05/сек) — legacy"},
            {"value": "302ai:wanx2.1-i2v-plus",
             "label": "302.ai · Wanx 2.1 i2v Plus 720P ($0.15/сек) — legacy"},
            {"value": "mock:placeholder",
             "label": "Заглушка (без API, для тестов)"},
        ],
    ),
    ParamField(
        key="style_preset",
        label="Стиль анимации",
        hint="Общая визуальная подача движения.",
        type="select", default="cinematic_historical",
        options=[
            {"value": "cinematic_historical", "label": "Кинематографичный исторический"},
            {"value": "documentary",          "label": "Документальный"},
            {"value": "kinetic_typography",   "label": "Кинетическая типографика"},
        ],
    ),
]


# ============================================================================
# 13. Режиссёр озвучки
# ============================================================================
VOICE_DIRECTOR_FIELDS: list[ParamField] = [
    ParamField(
        key="tts_model",
        label="Модель озвучки",
        hint="Чем синтезировать голос.",
        type="select", default="openai:gpt-4o-mini-tts",
        options=[
            {"value": "openai:gpt-4o-mini-tts",
             "label": "OpenAI gpt-4o-mini-tts ($0.015 / мин)"},
        ],
    ),
    ParamField(
        key="voice_id",
        label="Голос",
        hint="Какой голос использовать. Для русского лучше Onyx или Echo.",
        type="select", default="onyx",
        options=[{"value": v, "label": v.capitalize()} for v in
                 ["alloy", "ash", "ballad", "coral", "echo", "fable",
                  "nova", "onyx", "sage", "shimmer", "verse"]],
    ),
    ParamField(
        key="speed",
        label="Скорость речи",
        hint="1.0 — нормальная.",
        type="float", default=1.0, min=0.5, max=2.0, step=0.05,
    ),
]


# ============================================================================
# 14. Оформитель субтитров
# ============================================================================
SUBTITLE_STYLER_FIELDS: list[ParamField] = [
    ParamField(
        key="font",
        label="Шрифт",
        hint="Имя системного шрифта.",
        type="text", default="Inter",
    ),
    ParamField(
        key="font_size",
        label="Размер шрифта",
        type="int", default=54, min=20, max=120, hint="Высота в пикселях.",
    ),
    ParamField(
        key="color",
        label="Цвет (HEX)",
        hint="Например, #FFFFFF — белый, #FFD400 — жёлтый.",
        type="text", default="#FFFFFF",
    ),
    ParamField(
        key="position",
        label="Положение на экране",
        hint="Куда выводить субтитры в кадре 9:16.",
        type="select", default="bottom_center",
        options=[
            {"value": "top_center",    "label": "Сверху по центру"},
            {"value": "middle",        "label": "По центру"},
            {"value": "bottom_center", "label": "Снизу по центру"},
        ],
    ),
    ParamField(
        key="highlight_keywords",
        label="Подсвечивать ключевые слова",
        hint="Включить выделение ключевых слов цветом.",
        type="bool", default=True,
    ),
]


# ============================================================================
# 15. Сборщик видео — без настроек
# ============================================================================
VIDEO_ASSEMBLER_FIELDS: list[ParamField] = []


# ============================================================================
# Registry
# ============================================================================
PARAM_SCHEMAS: dict[str, list[ParamField]] = {
    "topic_generator":     TOPIC_GENERATOR_FIELDS,
    "topic_validator":     TOPIC_VALIDATOR_FIELDS,
    "topic_ranker":        TOPIC_RANKER_FIELDS,
    "researcher":          RESEARCHER_FIELDS,
    "research_validator":  RESEARCH_VALIDATOR_FIELDS,
    "article_writer":      ARTICLE_WRITER_FIELDS,
    "headline_writer":     HEADLINE_WRITER_FIELDS,
    "image_prompt_writer": IMAGE_PROMPT_WRITER_FIELDS,
    "qa_editorial":        QA_EDITORIAL_FIELDS,
    "fact_audit":          FACT_AUDIT_FIELDS,
    "qa_visual":           QA_VISUAL_FIELDS,
    "channel_rewriter":    CHANNEL_REWRITER_FIELDS,
    "video_scenarist":     VIDEO_SCENARIST_FIELDS,
    "video_keyframe_artist": VIDEO_KEYFRAME_ARTIST_FIELDS,
    "voice_director":      VOICE_DIRECTOR_FIELDS,
    "subtitle_styler":     SUBTITLE_STYLER_FIELDS,
    "video_assembler":     VIDEO_ASSEMBLER_FIELDS,
}


def schema_for(role: str) -> list[ParamField]:
    """Returns ordered list of editable params for a given agent role."""
    return PARAM_SCHEMAS.get(role, [])


def schema_payload(role: str) -> list[dict[str, Any]]:
    """JSON-serializable representation of the schema (for the API)."""
    out: list[dict[str, Any]] = []
    for f in schema_for(role):
        out.append({
            "key": f.key,
            "label": f.label,
            "hint": f.hint,
            "type": f.type,
            "default": f.default,
            "min": f.min,
            "max": f.max,
            "step": f.step,
            "options": f.options,
        })
    return out
