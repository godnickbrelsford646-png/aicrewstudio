# AiCrewStudio

> Конструктор «команд ИИ-агентов» для контент-студий: генерация тем → исследование → написание статей → генерация изображений → QA → публикация в текстовые и видео-каналы. Принцип близок к n8n: пайплайн из нод-агентов, каждая нода знает контекст предыдущих.

[![tests](https://img.shields.io/badge/tests-6%2F6-green)]() [![mode](https://img.shields.io/badge/mode-mock-blue)]()

## Что готово в этой ветке (`feat/mvp-skeleton`)

- ✅ Полный пайплайн end-to-end: TopicGenerator → Validator → Ranker → Researcher → ResearchValidator → ArticleWriter → HeadlineWriter → ImagePromptWriter → ImageGen → QA(Editorial+Visual) → ChannelRewriter → Publisher.
- ✅ **RU и EN** ветки работают параллельно, на каждую тему создаётся 2 статьи.
- ✅ Все **14 каналов** (TG / VK / MAX / FB / IG / OK / Threads / X / IG Reels / Snap / TikTok / YT Shorts / VK Video / RuTube) — mock-адаптеры + скелеты реальных API.
- ✅ Встроенные **гайды подключения** по каждому каналу — выводятся в UI и в `GET /api/channels/kinds`.
- ✅ **UI** (vanilla HTML/JS, без npm): Projects / Pipeline / Agents / Topics / Articles / Channels / Posts. В карточке агента — рендеренный промт, входы/выходы, токены, стоимость.
- ✅ **Видео-команда** — каркас (агент-сценарист), готова к подключению image-to-video / TTS / монтажа.
- ✅ Шифрование credentials каналов, лог всех LLM-вызовов, бюджет проекта.
- ✅ Тесты (6/6 ✓): шаблоны, шифрование, реестр каналов, mock-публикаторы, smoke полного пайплайна.

## Как это собрано

Сборка специально без сетевых зависимостей — она работает в любом изолированном окружении и сразу демонстрирует поведение продукта на реальных данных (mock-провайдеры выдают детерминированные правдоподобные ответы).

Когда у вас на машине есть pypi/npm — поверх этого скелета без рефакторинга встают FastAPI + SQLAlchemy + Postgres + Next.js: `aicrew/db.py` уезжает в SQLAlchemy-модели (схема таблиц совпадает), `http.server` в `aicrew/api.py` — в FastAPI-роуты, mini-Jinja — в настоящий Jinja2, mock-LLM остаётся для тестов. Подробности в [`docs/12-mvp-build.md`](docs/12-mvp-build.md).

## Быстрый старт

Требуется только **Python 3.12+**. Без pip-зависимостей.

```bash
make demo                 # init + seed + полный прогон пайплайна, печатает summary
make api                  # http://localhost:8000  → UI + REST API
make test                 # юнит-тесты
make clean                # удалить SQLite и media/
```

Что вы получите после `make demo`:
- проект **AI Weekly** с RU+EN ветками;
- 8 тем → 2 статьи на тему × 2 языка = 4 статьи;
- 7 каналов (TG-RU/EN, VK, X, FB, OK, YT-Shorts) с моковыми credentials;
- 10 опубликованных постов (видео-канал в MVP пропускается);
- ~480x270 PNG-картинки в `media/`.

## Переключение на реальные API

В `.env`:
```
AICREW_LLM_PROVIDER=openai
AICREW_IMAGE_PROVIDER=flux
AICREW_SEARCH_PROVIDER=tavily
AICREW_MASTER_KEY=<32+ байт случайной строки>
OPENAI_API_KEY=...
TAVILY_API_KEY=...
FLUX_API_KEY=...
```

Каждый канал — credentials через UI (`/channels/<id>`). Все секреты шифруются с `AICREW_MASTER_KEY`, в API возвращаются маской.

## Документация

| Раздел | Файл |
|---|---|
| Видение продукта | [`docs/01-vision.md`](docs/01-vision.md) |
| Архитектура и стек | [`docs/02-architecture.md`](docs/02-architecture.md) |
| Модель данных | [`docs/03-data-model.md`](docs/03-data-model.md) |
| Пайплайн агентов | [`docs/04-pipeline.md`](docs/04-pipeline.md) |
| Спецификация агентов | [`docs/05-agents-spec.md`](docs/05-agents-spec.md) |
| Публикаторы | [`docs/06-publishers.md`](docs/06-publishers.md) |
| Видео-команда | [`docs/07-video-team.md`](docs/07-video-team.md) |
| Дорожная карта | [`docs/08-roadmap.md`](docs/08-roadmap.md) |
| Доработки и идеи | [`docs/09-improvements.md`](docs/09-improvements.md) |
| Запуск через Codex CLI | [`docs/10-codex-playbook.md`](docs/10-codex-playbook.md) |
| Гайды подключения каналов | [`docs/11-channel-connect-guides.md`](docs/11-channel-connect-guides.md) |
| MVP build notes | [`docs/12-mvp-build.md`](docs/12-mvp-build.md) |

## Структура репозитория

```
aicrewstudio/
├── aicrew/
│   ├── agents/           # реестр ролей, промт-шаблоны, AgentExecutor
│   ├── channels/         # реестр каналов + connect guides
│   ├── llm/              # LLMAdapter (OpenAI скелет + детерм. mock)
│   ├── publishers/       # mock-адаптеры всех 14 каналов + real-API скелеты
│   ├── tools/            # web_search / fetch_url / image_gen
│   ├── web/              # фронт: index.html + static/{app.js, styles.css}
│   ├── api.py            # HTTP API (на http.server, замена → FastAPI)
│   ├── cli.py            # `python -m aicrew.cli {init,seed,serve,demo}`
│   ├── crypto.py         # шифрование credentials
│   ├── db.py             # SQLite слой со схемой docs/03
│   ├── pipeline.py       # PipelineRunner: topic / article RU+EN / publication
│   ├── seed.py           # демо-проект "AI Weekly"
│   ├── settings.py       # конфиг из env
│   └── templates.py      # mini-Jinja для промтов
├── docs/                 # спецификации
├── tests/                # 6 unit/smoke тестов
├── docker-compose.yml    # для будущего prod-стека (postgres+redis+minio)
├── Makefile
├── pyproject.toml
└── .env.example
```

## Что НЕ сделано (и почему)

- **Реальные сетевые вызовы** к OpenAI / Tavily / каналам — sandbox без интернета. Скелеты на месте, переключение — одной env-переменной.
- **Видео-генерация** (image-to-video, TTS, монтаж) — каркас агента-сценариста готов, физическая сборка видео отложена до прохождения текстового MVP.
- **Multi-user / биллинг / 2FA** — single-user mode для MVP (таблицы заложены).
- **Visual DAG editor (React Flow)** — на M5 после стабильного текстового пайплайна.
- **Postgres + pgvector** — sqlite на этом этапе, схема таблиц совпадает.
