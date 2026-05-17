---
inclusion: always
---

# AiCrewStudio — правила проекта

## Стек
- Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, Celery + Redis.
- DB: PostgreSQL 16 + pgvector.
- Frontend: Next.js 14 (App Router), TypeScript, shadcn/ui, TanStack Query, React Flow.
- LLM: OpenAI (GPT‑5.x) основной; адаптер для Anthropic/Gemini/Ollama.
- Tools: Tavily/Serper (web search), Flux/SDXL/DALL‑E (image), Runway/Kling (video), Whisper (STT), OpenAI TTS / ElevenLabs (TTS).

## Архитектурные принципы
- Все агенты возвращают строгий JSON через `response_format=json_schema`, парсятся в Pydantic.
- Каждое исполнение агента = `AgentRun` + минимум один `LLMCall`. Логи иммутабельные.
- Пайплайн — DAG нод, не монолит. Любую ноду можно перезапустить.
- Идемпотентность публикаций: `(article_id, channel_id)` уникальны.
- Память: pgvector для дедупа тем (cosine ≥ 0.85 — дубль).

## Кодстайл
- Python: ruff + black + mypy (strict). Type hints везде. Никаких `Any` без причины.
- TS: eslint + prettier, strict mode, без `any`.
- Названия — ясные и длинные лучше коротких и кодовых.
- Ошибки — типизированные исключения, не «голые» строки.
- Логи — структурные (loguru/structlog), c `pipeline_run_id`/`agent_run_id` в контексте.

## Безопасность
- Секреты — только из env/секретницы. Никогда в коде/миграциях/логах.
- `channels.credentials` шифруется Fernet с `MASTER_KEY` из env.
- `fetch_url` режет приватные подсети (SSRF‑защита).
- Логи запросов к каналам — без тел с токенами.

## Тестирование
- Unit + integration. На критических узлах — TDD.
- LLM в unit‑тестах — мокается. Live‑вызовы только в `tests/integration/` под `RUN_LIVE=1`.
- Публикаторы — VCR‑кассеты.

## Документация
- Любая фича — обновляет `docs/`.
- Любая модель данных — миграция Alembic + апдейт `docs/03-data-model.md`.
- Новая роль агента — секция в `docs/05-agents-spec.md` + промт‑шаблон + Pydantic‑схема.

## Что НЕ делать без явного запроса
- Не добавлять биллинг/подписки в MVP (только заложить таблицы).
- Не делать multi‑user (один владелец = один проект).
- Не добавлять browser‑automation для публикаций (хрупко, ToS‑риски).
- Не использовать сторонние no‑code оркестраторы (n8n, Make) внутри ядра — только как опциональный `WebhookPublisher`.

## Локальный запуск
- `make up` — postgres+redis+minio.
- `make migrate && make seed`.
- `make api`, `make web`, `make worker`.
