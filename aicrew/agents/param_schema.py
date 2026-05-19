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
        hint="Используйте 302ai:flux-pro для качества или 302ai:flux-schnell для скорости.",
        type="select", default="302ai:flux-pro",
        options=[
            {"value": "302ai:flux-pro",       "label": "302.ai · Flux Pro (качество)"},
            {"value": "302ai:flux-schnell",   "label": "302.ai · Flux Schnell (быстрее)"},
            {"value": "302ai:sdxl",           "label": "302.ai · SDXL"},
            {"value": "openai:dall-e-3",      "label": "OpenAI DALL·E 3"},
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
        hint="Оптимум для Shorts/Reels/TikTok: 30–45 секунд.",
        type="int", default=45, min=10, max=120,
    ),
    ParamField(
        key="aspect",
        label="Соотношение сторон",
        hint="9:16 — для Shorts/Reels/TikTok. 1:1 — для ленты. 16:9 — для YouTube/VK Видео.",
        type="select", default="9:16",
        options=[
            {"value": "9:16",  "label": "9:16 (вертикальное)"},
            {"value": "1:1",   "label": "1:1 (квадрат)"},
            {"value": "16:9",  "label": "16:9 (горизонтальное)"},
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
    "qa_visual":           QA_VISUAL_FIELDS,
    "channel_rewriter":    CHANNEL_REWRITER_FIELDS,
    "video_scenarist":     VIDEO_SCENARIST_FIELDS,
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
