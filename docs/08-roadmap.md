# 08. Дорожная карта

## M0. Скелет (1–2 дня)

- Репозиторий, структура папок, `docker-compose` (postgres + redis + minio).
- FastAPI‑скелет (`/health`, `/version`).
- Next.js‑скелет (главная, страница `/projects`).
- Alembic init, базовые миграции (`users`, `projects`).
- Seed: один пользователь, один проект.

**Acceptance:** `make up && make migrate && make seed` поднимает всё локально, открывается `localhost:3000` со списком проектов.

---

## M1. Агентский движок (3–5 дней)

- Модели `agents`, `pipeline_runs`, `agent_runs`, `llm_calls`.
- `LLMAdapter` для OpenAI (GPT‑5.x).
- `AgentExecutor` с Jinja2‑шаблонами и `response_format=json_schema`.
- Один тестовый агент (TopicGenerator), запускаемый из API: `POST /projects/{id}/runs/topics`.
- UI: список проектов, страница проекта, карточка агента (просмотр последних `agent_runs` + ответ JSON).

**Acceptance:** из UI кнопка «Сгенерировать темы» создаёт `pipeline_run`, в карточке TopicGenerator видны вход/промт/ответ.

---

## M2. Topic phase end‑to‑end (3–4 дня)

- Агенты: TopicGenerator, TopicValidator, TopicRanker.
- Инструмент `web_search` (Tavily/Serper).
- Цикл «генерация → проверка → дозапрос» с `max_iterations`.
- Сохранение `topics` со score и эмбеддингом (pgvector).
- UI: банк тем, фильтры, ручное «отклонить/одобрить».
- Память: исключение из генерации тем, у которых есть опубликованный пост за N дней.

**Acceptance:** проект за один прогон собирает N подтверждённых тем с рейтингом.

---

## M3. Article phase RU/EN (5–7 дней)

- Researcher + ResearchValidator (с `web_search`/`fetch_url`).
- ArticleWriter, HeadlineWriter, ImagePromptWriter.
- Инструмент ImageGen (старт — OpenAI Images или Flux через Fal/Replicate).
- QA.Editorial / QA.Visual / QA.Compose.
- UI: просмотр статьи, всех её агентских шагов, выбранного заголовка/картинки.
- Параллельные ветки RU и EN (две линии редакторов и QA).

**Acceptance:** из утверждённой темы создаётся статья со статусом `qa_passed`, видны все промежуточные артефакты.

---

## M4. Публикация в текстовые каналы (5–7 дней)

- Модели `channels`, `channel_slots`, `posts`.
- ChannelRewriter‑агент (один общий код, разные настройки на канал).
- Адаптеры: Telegram, VK, X (минимум).
- Scheduler (Celery beat / APScheduler) — слоты публикации.
- UI: каналы, добавление, тест‑публикация, лента опубликованных.

**Acceptance:** проект автоматически по расписанию публикует посты в TG и VK.

---

## M5. Расширение каналов и устойчивость (3–5 дней)

- Адаптеры: Facebook Page, Instagram (через Graph API), Threads, OK.
- Generic `WebhookPublisher` (для Buffer/Make/Zapier).
- MAX/Snapchat — режим «черновик к ручной публикации».
- Rate limit/retry/backoff на каждом адаптере.
- Шифрование `credentials` (Fernet + MASTER_KEY).

**Acceptance:** добавляется любой из заявленных текстовых каналов и публикует пост.

---

## M6. Видео‑команда (10–14 дней)

- Скаффолд видео‑пайплайна (см. `07-video-team.md`).
- VideoScenarist + ScenePromptWriter.
- SceneImageGen + SceneVideoGen (Runway или Kling).
- Voicer (OpenAI TTS / ElevenLabs).
- Subtitler (Whisper).
- VideoComposer (ffmpeg).
- Видео‑публикаторы: YouTube Shorts, TikTok, IG Reels, VK Video.
- UI: storyboard сцен, регенерация одной сцены, превью.

**Acceptance:** из статьи получаются 2 финальных видео (RU/EN), публикуются хотя бы в YouTube Shorts.

---

## M7. Бюджет, наблюдаемость, надёжность (3–5 дней)

- `cost_usd` на каждый `llm_call` и `agent_run`.
- Лимит `budget_usd_month` на проект; хук на превышение → пауза.
- Метрики (Prometheus): кол‑во запусков, ошибки, средние tokens.
- Логи в JSON, трассировка через `request_id`/`pipeline_run_id`.

**Acceptance:** дашборд расходов по проекту/агенту, алерт на 80% бюджета.

---

## M8. Подписки и мульти‑юзер (отложено)

- Регистрация, OAuth (Google), email‑magic‑link.
- Тарифы (Free/Pro/Team), Stripe/YooKassa.
- Лимиты на проекты/прогоны/токены по тарифу.
- Роли в проекте (owner/editor/viewer).

**Acceptance:** новый юзер регистрируется, выбирает план, оплачивает, создаёт проект.

---

## Критерии «Definition of Done» для каждой вехи

- Покрытие тестами критического пути (агенты — мокированные LLM, публикаторы — vcr‑кассеты).
- Миграции в `alembic/`, обновляется `seed`.
- Документация в `docs/` обновлена.
- Заведена `Makefile`‑команда `make m{N}-demo` — воспроизводимый сценарий вехи.
