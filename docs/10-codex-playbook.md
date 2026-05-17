# 10. Запуск через Codex CLI (playbook)

> Цель: сделать так, чтобы `codex` (терминальный агент с моделью GPT‑5.3‑codex) мог пошагово реализовать AiCrewStudio из этого репозитория, не теряя контекст.

## Подготовка

1. Установить Codex CLI и авторизоваться (см. официальную доку OpenAI Codex CLI).
2. Открыть терминал в корне репо `aicrewstudio/`.
3. Убедиться, что есть `.kiro/steering/project.md` (он подсасывается контекстом).
4. Создать `.codex/AGENTS.md` (см. ниже) — туда положить «постоянные правила игры».

## `.codex/AGENTS.md` — рекомендованное содержимое

```md
# Project rules for codex

- Stack: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Celery, Redis, PostgreSQL+pgvector, Next.js 14, shadcn/ui, React Flow.
- Style: ruff + black + mypy strict; eslint + prettier; type‑hints everywhere.
- Tests: pytest (backend), vitest (frontend). Add tests for any new public function.
- All LLM agents must use json_schema response_format and Pydantic parsing.
- Никаких секретов в коде — только через env / app settings.
- Любая новая фича — обновляет docs/ и Alembic‑миграции.
- Перед коммитом — ruff/black/mypy/eslint должны проходить.
```

## Стиль постановки задач для Codex

Лучше всего работают **маленькие, инкрементальные задания**, привязанные к вехам из `docs/08-roadmap.md`.

### Шаблон задачи

```
ROLE: senior python engineer
GOAL: <одно предложение>
CONTEXT FILES:
  - docs/02-architecture.md
  - docs/03-data-model.md
  - <конкретные исходники, если есть>
DELIVERABLE:
  - <что должно появиться/измениться>
  - тесты <такие-то>
  - миграция alembic <такая-то>
CONSTRAINTS:
  - не менять <это>
  - не добавлять зависимостей кроме <списка>
ACCEPTANCE:
  - команда `make <…>` проходит
  - тест <…> зелёный
```

## Типовая последовательность команд для Codex

> Каждый блок — отдельная сессия `codex`. Заканчиваем коммитом и переходим к следующему.

### 0. Скелет

```
codex "Сгенерируй скелет монорепо: backend/ (FastAPI), web/ (Next.js 14 app router), docker-compose.yml (postgres, redis, minio), Makefile с командами up/down/migrate/seed/api/web/worker/test. Соблюдай docs/02-architecture.md и AGENTS.md."
```

### 1. БД и миграции

```
codex "Создай SQLAlchemy‑модели и Alembic‑миграции по docs/03-data-model.md для users, projects, agents, pipeline_runs, agent_runs, llm_calls. Добавь pgvector для topics.embedding (миграция позже). Добавь pytest‑фикстуры с тестовой БД."
```

### 2. LLM Adapter и AgentExecutor

```
codex "Реализуй backend/app/llm/adapter.py с интерфейсом call(model, messages, tools, temperature, response_format) и реализацией для OpenAI. Реализуй backend/app/agents/executor.py: рендер Jinja2‑промта, вызов LLM, парсинг JSON в Pydantic, запись AgentRun + LLMCall, расчёт cost. Покрытие тестами на моках."
```

### 3. TopicGenerator MVP

```
codex "Добавь роль topic_generator: модель Pydantic для output, промт‑шаблон в backend/app/agents/prompts/topic_generator.j2, JSON‑схему. Эндпоинт POST /projects/{id}/runs/topics — стартует pipeline_run и AgentExecutor TopicGenerator. Используй docs/05-agents-spec.md."
```

### 4. Web search tool

```
codex "Реализуй backend/app/tools/web_search.py с провайдером Tavily (TAVILY_API_KEY). Зарегистрируй как function tool для агентов. Добавь интеграционный тест с записанным VCR."
```

### 5. Topic phase loop

```
codex "Реализуй PipelineRunner для topic phase: TopicGenerator → TopicValidator → (loop) → TopicRanker. Соблюдай логику из docs/04-pipeline.md (max_iterations, forbidden_topics). Добавь pgvector embedding и дедуп по cosine ≥ 0.85."
```

### 6. UI: проекты и карточка агента

```
codex "В web/ добавь страницы /projects, /projects/[id], /projects/[id]/agents/[role]. Список agent_runs последних 50, рендер inputs/output как JSON tree. Использовать shadcn/ui и TanStack Query. Бэкенд‑эндпоинты сгенерировать в openapi и потребить через openapi‑typescript."
```

### 7. Article phase

```
codex "Реализуй роли researcher, research_validator, article_writer, headline_writer, image_prompt_writer, qa_editorial, qa_visual, qa_compose согласно docs/05-agents-spec.md. Параллельные ветки RU/EN. Сохранять MediaAsset для картинок. Тесты на моках LLM и моках image gen."
```

### 8. Каналы и публикаторы

```
codex "Реализуй модели channels, channel_slots, posts. Реализуй PublisherAdapter и адаптеры Telegram, VK, X. Шифрование credentials через Fernet (MASTER_KEY env). Scheduler через Celery beat: каждые 5 минут проверяет ближайшие слоты и кладёт публикации в очередь."
```

### 9. Стабилизация

```
codex "Добавь cost‑tracking, soft/hard budget на project, dead‑letter queue для проваленных публикаций, health‑check токенов каналов раз в час, audit log пользовательских действий."
```

### 10. Видео‑команда (после M5)

См. `docs/07-video-team.md` — раскладывать на задачи по аналогичному шаблону.

## Гайдрейлы для Codex‑сессий

- **Не давайте Codex сразу всю архитектуру.** Дробите по вехам — иначе он сделает «по‑своему» в неожиданных местах.
- **Прикрепляйте конкретные файлы.** «Соблюдай docs/03-data-model.md» работает гораздо лучше, чем «соблюдай документацию».
- **Просите написать тесты до кода** на критических узлах (AgentExecutor, PublisherAdapter).
- **Каждую сессию завершайте коммитом.** Если Codex запутался — `git reset` и переформулируйте задачу мельче.
- **Просите DIFF, а не полные файлы**, если правка маленькая. Меньше шансов сломать соседний код.
- **Мокайте LLM в тестах.** Реальные вызовы — только в `tests/integration/` под флагом `RUN_LIVE=1`.

## Что хранить в `.kiro/steering/`

См. `.kiro/steering/project.md` — там зафиксированы правила, которые подсасываются и Kiro, и (если научите) Codex‑CLI как контекстный документ.
