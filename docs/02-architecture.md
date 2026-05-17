# 02. Архитектура

## Логические слои

```
┌────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js)                                            │
│  - Дашборд проектов, карточка проекта, граф пайплайна          │
│  - Редактор агента (промт, модель, темп., параметры)           │
│  - Лента артефактов (темы/статьи/картинки/публикации)          │
└──────────────▲─────────────────────────────────────────────────┘
               │ REST + SSE (для live‑логов)
┌──────────────┴─────────────────────────────────────────────────┐
│  API (FastAPI)                                                 │
│  - Auth, Projects, Agents, Runs, Artifacts, Channels           │
│  - SSE стрим логов агентов                                     │
└──────────────▲─────────────────────────────────────────────────┘
               │
┌──────────────┴─────────────────────────────────────────────────┐
│  Orchestrator (Celery + Redis)                                 │
│  - PipelineRunner: исполняет DAG из нод‑агентов                │
│  - Scheduler: APScheduler/Celery beat — расписание публикаций  │
│  - AgentExecutor: вызов LLM, инструменты, retry, cost‑tracking │
└──────┬───────────────────────────┬─────────────────────────────┘
       │                           │
┌──────┴───────┐           ┌───────┴────────┐
│  LLM Adapter │           │ Tool Adapters  │
│  OpenAI /    │           │ Web search,    │
│  Anthropic / │           │ Image gen,     │
│  Gemini /    │           │ Video gen,     │
│  Local       │           │ TTS, Publisher │
└──────────────┘           └────────────────┘
       │
┌──────┴────────────────────────────────────────────────────────┐
│  PostgreSQL + pgvector  │  S3‑совместимое хранилище (медиа)   │
└────────────────────────────────────────────────────────────────┘
```

## Ключевые компоненты

### `pipeline_runner`
- Берёт описание пайплайна (DAG) для проекта.
- Стартует `PipelineRun` — единичный прогон цикла «генерация → публикация».
- Для каждой ноды создаёт `AgentRun` (один вызов агента) и `ArtifactBundle` (выходы ноды).
- Поддерживает **fan‑out** (RU и EN ветки в редакторах) и **loop** (повторная генерация тем, если их недостаточно).

### `agent_executor`
- На входе: `Agent` (конфиг) + `inputs` (результаты предыдущих нод).
- Рендерит финальный промт по шаблону Jinja2: подставляет переменные, артефакты, дату, память.
- Вызывает `llm_adapter.call(model, messages, tools=..., temperature=..., response_format=...)`.
- Логирует `LLMCall` (промт, ответ, tokens_in/out, latency, cost).
- Возвращает структурированный JSON (через `response_format=json_schema` или Pydantic‑парсер).

### `llm_adapter`
- Единый интерфейс: `def call(model, messages, **kwargs) -> LLMResponse`.
- Под капотом: OpenAI/Anthropic/Gemini/Ollama. Маршрутизация по `model` (например, `openai:gpt-5.3-codex`).
- Учёт стоимости — таблица цен на модели в БД, обновляемая.

### `tool_adapter`
- `web_search` (Tavily/Serper), `fetch_url`, `image_gen`, `video_gen`, `tts`, `transcribe`.
- Все инструменты — **функции для function‑calling**, чтобы агенты могли их сами выбирать.

### `publisher_adapter`
- Per‑channel: `TelegramPublisher`, `VKPublisher`, `XPublisher`, ...
- Метод: `publish(post: Post, credentials) -> PublicationResult`.
- Для каналов без официального API (Threads, Instagram personal) — фоллбек на «черновик + ручная публикация» или интеграция с Make/Zapier/Buffer.

### `scheduler`
- На уровне канала: список слотов времени (HH:MM в TZ проекта).
- Cron‑задача каждые N минут: ищет слоты «через ≤T минут» и кладёт задачу публикации в очередь.

## Стек и обоснование

- **Python + FastAPI** — стандарт для AI‑бэкенда, лучшая экосистема LLM SDK.
- **Celery + Redis** — проверенная очередь, удобна для retry и приоритетов.
- **PostgreSQL + pgvector** — реляционка + семантический поиск (дедуп тем) в одной БД.
- **Next.js + React Flow** — для визуального DAG пайплайна, как в n8n.
- **shadcn/ui** — быстро собирать админку.

## Развёртывание

- **Dev:** `docker-compose.yml` (postgres, redis, minio, api, worker, web).
- **Prod:** один VPS (4 vCPU / 8 GB) на старте; масштабируем worker‑ы горизонтально.
- **Секреты:** `.env` на dev, в проде — Doppler/Infisical/SOPS.
