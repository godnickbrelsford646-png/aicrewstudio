# AiCrewStudio

> Конструктор «команд ИИ‑агентов» для контент‑студий: генерация тем → исследование → написание статей → генерация изображений → QA → публикация в текстовые и видео‑каналы. Принцип близок к n8n: пайплайн из нод‑агентов, каждая нода знает контекст предыдущих.

## TL;DR

- **Юзер** создаёт «Проект» (тематика блога/канала, расписание, каналы публикации).
- В каждом проекте — единая **команда агентов** с фиксированной структурой ролей, но индивидуальными настройками (модель, температура, промт).
- Пайплайн: **Темы → Проверка → Ранжирование → Редактор (RU/EN) → Иллюстратор → QA → Публикаторы (текст + видео)**.
- Все артефакты (темы, исследовательские досье, статьи, картинки, посты) сохраняются и доступны для аудита.
- Подписки/биллинг — заложены архитектурно, но **выключены в MVP** (single‑user режим).

## Документация

| Раздел | Файл |
|---|---|
| Видение продукта | [`docs/01-vision.md`](docs/01-vision.md) |
| Архитектура и стек | [`docs/02-architecture.md`](docs/02-architecture.md) |
| Модель данных | [`docs/03-data-model.md`](docs/03-data-model.md) |
| Пайплайн агентов | [`docs/04-pipeline.md`](docs/04-pipeline.md) |
| Спецификация агентов | [`docs/05-agents-spec.md`](docs/05-agents-spec.md) |
| Публикаторы | [`docs/06-publishers.md`](docs/06-publishers.md) |
| Видео‑команда | [`docs/07-video-team.md`](docs/07-video-team.md) |
| Дорожная карта | [`docs/08-roadmap.md`](docs/08-roadmap.md) |
| Доработки и идеи | [`docs/09-improvements.md`](docs/09-improvements.md) |
| Запуск через Codex CLI | [`docs/10-codex-playbook.md`](docs/10-codex-playbook.md) |

## Стек (предложенный)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, Celery + Redis (очереди/расписание), APScheduler как fallback.
- **DB:** PostgreSQL 16 + pgvector (для памяти и дедупа тем).
- **LLM/инструменты:** OpenAI API (GPT‑5/5.3), плюс адаптер для Anthropic, Gemini, локальных моделей (vLLM/Ollama). Веб‑поиск через Tavily/Serper/Brave.
- **Изображения:** OpenAI Images / Flux / SDXL (через Replicate/Fal). 
- **Видео:** Runway / Kling / Pika (image‑to‑video), TTS — ElevenLabs/OpenAI, субтитры — Whisper.
- **Frontend:** Next.js 14 (App Router) + TanStack Query + shadcn/ui + React Flow (визуальный пайплайн).
- **Auth:** Clerk или собственный JWT + OAuth (заложено, в MVP можно single‑user).
- **Деплой:** Docker Compose для dev, Fly.io/Render/VPS на проде.

## Быстрый старт (после реализации MVP)

```bash
cp .env.example .env
docker compose up -d db redis
make migrate
make seed
make api    # FastAPI на :8000
make web    # Next.js на :3000
make worker # Celery worker
```

## Лицензия

Private (на этапе MVP). Решим позже.
