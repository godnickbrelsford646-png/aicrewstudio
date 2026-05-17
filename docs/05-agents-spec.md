# 05. Спецификация агентов

> Каждый агент описан в одном формате: **Назначение → Входы → Параметры → Инструменты → Ожидаемый JSON‑выход → Шаблон промта (черновик)**.

Все агенты возвращают **строгий JSON** через `response_format=json_schema`. AgentExecutor парсит его в Pydantic‑модель.

---

## 1. TopicGenerator

**Назначение:** придумать N тем для постов в нише проекта.

**Входы:** `project.niche`, `project.description`, `forbidden_topics[]`, `today` (опц.).

**Параметры:**
- `topics_per_run: int` — сколько тем за один вызов.
- `send_today_date: bool`.
- `memory_lookback_days: int` — сколько дней назад смотреть в памяти опубликованных.

**Инструменты:** `web_search`.

**JSON‑выход:**
```json
{
  "topics": [
    {"title": "string", "angle": "string", "why_now": "string", "tentative_sources": ["url1","url2"]}
  ]
}
```

**Промт (шаблон):**
```
Ты — генератор тем для канала про "{{ project.niche }}".
{% if send_today_date %}Сегодня {{ today }}.{% endif %}
Твоя задача: предложить {{ topics_per_run }} новых, интересных тем.
Используй web_search для актуальности.

Запрещённые темы (уже писали):
{% for t in forbidden_topics %}- {{ t }}
{% endfor %}

Верни только JSON по схеме.
```

---

## 2. TopicValidator

**Назначение:** проверить темы на достоверность/актуальность, расширить summary.

**Входы:** `candidate_topics[]` от TopicGenerator + ранее подтверждённые в этом прогоне.

**Параметры:**
- `confirmed_topics_target: int` — сколько надо набрать.
- `max_retries: int`.

**Инструменты:** `web_search`, `fetch_url`.

**JSON‑выход:**
```json
{
  "validated": [
    {
      "title": "string",
      "summary_extended": "string",
      "sources": ["url1","url2","url3"],
      "is_valid": true,
      "reject_reason": null
    }
  ],
  "need_more": true,
  "missing_count": 3
}
```

**Логика рантайма (за пределами агента):** если `confirmed_count < confirmed_topics_target` → отправить TopicGenerator повторно с обновлённым `forbidden_topics`. Повторять до `max_retries`.

---

## 3. TopicRanker

**Назначение:** проставить рейтинг.

**Входы:** валидированные темы.

**Параметры:**
- `criteria_weights: { relevance, novelty, virality, evergreen, sources_quality }` — суммой = 1.0.
- `top_n_to_keep: int` — отсечка (мягкая, для UI).

**Инструменты:** none (или `web_search` опционально для проверки трендов).

**JSON‑выход:**
```json
{
  "ranked": [
    {
      "title": "string",
      "scores": {"relevance": 8, "novelty": 7, "virality": 6, "evergreen": 5, "sources_quality": 9},
      "score_total": 7.3,
      "rationale": "string"
    }
  ]
}
```

---

## 4. Researcher

**Назначение:** собрать факты по теме.

**Входы:** `topic.title`, `topic.summary_extended`, `topic.sources`.

**Параметры:**
- `min_facts: int = 10`.
- `language` (наследуется от ветки RU/EN).

**Инструменты:** `web_search`, `fetch_url`.

**JSON‑выход:**
```json
{
  "facts": [{"claim":"...", "source":"url"}],
  "stats": [{"value":"...", "context":"...", "source":"url"}],
  "quotes": [{"who":"...", "what":"...", "source":"url"}],
  "open_questions": ["..."]
}
```

---

## 5. ResearchValidator

**Назначение:** перепроверить досье и **переписать** с исправлениями.

**Входы:** `research_brief` от Researcher.

**Параметры:**
- `strictness: "low"|"medium"|"high"`.

**Инструменты:** `web_search`, `fetch_url`.

**JSON‑выход:** **тот же формат, что у Researcher**, но исправленный. Дополнительно — `corrections_log` (для аудита, не показывается дальше по пайплайну).

---

## 6. ArticleWriter

**Назначение:** написать одну «универсальную» статью.

**Входы:** `topic`, `validated_research`, `language`, `style_guide` (из проекта).

**Параметры:**
- `target_chars: int = 8000`.
- `tone: string`.
- `must_include_blocks: ["intro","facts","examples","conclusion","cta"]`.

**JSON‑выход:**
```json
{
  "title_working": "string",
  "body_md": "markdown text",
  "key_points": ["..."],
  "tldr": "string",
  "tags": ["..."]
}
```

---

## 7. HeadlineWriter

**Назначение:** генерировать варианты заголовков.

**Параметры:**
- `headlines_per_article: int`.
- `styles: ["clickbait","neutral","question","listicle"]`.

**JSON‑выход:**
```json
{ "headlines": [{"text":"...","style":"clickbait","char_count":58}] }
```

---

## 8. ImagePromptWriter + ImageGen (две ноды или одна с двумя шагами)

**ImagePromptWriter — Назначение:** сделать промт для иллюстрации.
**Параметры:**
- `images_per_article: int`.
- `image_model` (метаданные для генератора).
- `style_preset` (e.g. `editorial`, `flat`, `cinematic`).

**JSON‑выход:**
```json
{ "image_prompts": [{"prompt":"...", "negative":"...", "aspect":"16:9", "seed": null}] }
```

**ImageGen — нода‑инструмент** (без LLM): принимает промты, дёргает провайдера (Flux/DALL‑E/SDXL), кладёт `MediaAsset`‑ы.

---

## 9. QA (Editorial / Visual / Compose)

### 9a. QA.Editorial
- Проверяет текст: фактчек ключевых утверждений (выборочно), грамматика, тон, длина.
- Выбирает один из заголовков.
- Может вернуть `revised_body_md` с минимальной правкой.
- **JSON‑выход:**
```json
{
  "chosen_headline": "string",
  "revised_body_md": "string",
  "issues": [{"severity":"low|med|high", "note":"..."}],
  "score": 0
}
```

### 9b. QA.Visual
- Получает массив картинок + краткое описание статьи.
- Возвращает `chosen_image_id` + причину.
- Если картинок 1 — выбирает её без раздумий.

### 9c. QA.Compose
- Без LLM. Просто собирает финальный `Article` из выходов 9a/9b.
- Проставляет `status=qa_passed`, `written_at=now`.

---

## 10. ChannelRewriter (по одному на текстовый канал)

**Назначение:** адаптировать `revised_body_md` под формат канала.

**Параметры:**
- `channel_id` (для подтягивания лимитов).
- `max_chars` (TG ~4096, X ~280, VK ~16000, OK ~32000, ...).
- `style_voice` (промт от пользователя — голос канала).
- `keep_headline: bool` (по умолчанию true — заголовок и картинку берём из QA).

**JSON‑выход:**
```json
{
  "post_body": "string",
  "hashtags": ["..."],
  "first_comment": "string|null"
}
```

---

## 11. (опц.) VideoTeam — см. `07-video-team.md`

---

## Общие правила для всех агентов

1. **Output schema enforced** — `response_format=json_schema`. На клиенте — Pydantic.
2. **Tool calls** — через function calling. Каждый инструмент имеет JSON‑схему.
3. **Token budget** — `max_tokens` ограничен на агента; превышение → `agent_run.status=failed` + retry.
4. **Cost cap** — суммарный расход агентов одного `pipeline_run` сравнивается с `project.budget_usd_month / прогнозом`. Если выход за лимит — пайплайн ставится на паузу с уведомлением.
5. **Аудит‑шаги** — `corrections_log`, `issues`, `rationale` сохраняются, но **не передаются дальше** по умолчанию (UI показывает их в карточке агента).
