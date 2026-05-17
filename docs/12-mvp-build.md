# 12. MVP Build Notes

> Эта сборка — реальный, запускаемый MVP, написанный на стандартной библиотеке Python 3.12 без сетевых зависимостей. Архитектура полностью соответствует docs 01–10. Когда будет доступ к pypi/npm, сборка обновится на FastAPI + SQLAlchemy + Postgres + Next.js без смены формы модулей.

## Что сделано

- **Полный пайплайн** в моках: TopicGenerator → Validator → Ranker → Researcher → ResearchValidator → ArticleWriter → HeadlineWriter → ImagePromptWriter → ImageGen → QA(Editorial+Visual) → ChannelRewriter → Publisher.
- **RU и EN** ветки идут параллельно: на каждую тему создаётся 2 статьи.
- **14 каналов** (TG, VK, MAX, FB, IG, OK, Threads, X, IG Reels, Snap, TikTok, YT Shorts, VK Video, RuTube). Каждый имеет mock-адаптер; реальные API скелеты — в `aicrew/publishers/real_skeletons.py`.
- **Connect guides** — встроены в API (`GET /api/channels/kinds`) и автоматически показываются в UI на странице канала.
- **Картинки** для постов — генератор пишет реальные PNG (placeholder с подписью).
- **Видео-команда** — `video_scenarist` агент готов; `image-to-video`, TTS, монтаж — место под подключение оставлено.
- **Шифрование credentials**, бюджет, токены, аудит-лог LLM-вызовов, маска секретов в API.
- **UI** — vanilla HTML/JS SPA: Projects, Pipeline, Agents (с просмотром rendered prompt + inputs/outputs всех прогонов), Topics, Articles, Channels (с гайдом подключения и формой credentials), Posts.
- **Тесты** — `make test` (6 тестов, всё зелёное).

## Запуск

```bash
make demo                # full pipeline на mock-провайдерах, печатает summary
make api                 # http://localhost:8000  (UI + REST API)
```

## Переключение на реальные API

В `.env`:
```
AICREW_LLM_PROVIDER=openai
AICREW_IMAGE_PROVIDER=flux        # см. tools/image_gen.py
AICREW_SEARCH_PROVIDER=tavily     # см. tools/search.py
OPENAI_API_KEY=...
TAVILY_API_KEY=...
```

В каналах — заполнить `credentials` через UI (`/channels/<id>` → форма «Credentials»). Все секреты шифруются Fernet-аналогом по `AICREW_MASTER_KEY`.

## Точки роста

- Заменить SQLite → Postgres + pgvector (структура таблиц совпадает с docs/03).
- Заменить mini-jinja на настоящий Jinja2.
- Завести Celery+Redis (синхронный `PipelineRunner.run_full` уже разделён на 3 фазы — каждая может стать таской).
- Реализовать `real_skeletons.py` адаптеры (TG/VK/X — самые простые для старта).
- Подключить image-to-video и TTS к `VideoComposer` (агент-сценарист уже отдаёт сцены).
