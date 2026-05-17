# 03. Модель данных

> Уровень: концептуальный. Точные DDL — в Alembic‑миграциях после генерации скелета через Codex.

## ER‑карта (упрощённо)

```
User ──< Project ──< Agent ──< AgentRun ──< LLMCall
                │           
                ├──< Topic ──< Article ──< Post ──< Publication
                ├──< Channel ──< ChannelSlot
                ├──< PipelineRun
                └──< Subscription (заложено, не используется в MVP)
```

## Таблицы

### `users`
- `id`, `email`, `password_hash` (или ext_id для Clerk), `created_at`.
- В MVP: один зашитый пользователь (seed).

### `subscriptions` *(заложено, неактивно)*
- `id`, `user_id`, `plan` (`free`/`pro`/`team`), `valid_until`, `status`.

### `projects`
- `id`, `user_id`, `name`, `niche`, `description`.
- `is_enabled` (bool), `enabled_at` (datetime), `timezone`.
- `language_modes`: `['ru','en']` — какие ветки редакторов активны.
- `daily_topics_target`, `daily_articles_target` — цели по объёму.
- `budget_usd_month` — лимит расходов LLM/инструментов.
- `created_at`, `updated_at`.

### `agents`
Каждый проект получает фиксированный набор ролей при создании (см. `04-pipeline.md`).

- `id`, `project_id`, `role` (enum: `topic_generator`, `topic_validator`, `topic_ranker`, `researcher`, `research_validator`, `editor_ru`, `editor_en`, `headline_writer`, `image_prompt_writer`, `qa_ru`, `qa_en`, `channel_rewriter`, `video_scriptwriter`, ...).
- `display_name`, `description`.
- `model` (`openai:gpt-5.3`, ...), `temperature`, `max_tokens`, `top_p`.
- `prompt_template` (Jinja2, с переменными).
- `params` (jsonb) — специфические настройки роли:
  - для `topic_generator`: `topics_per_run`, `send_today_date`, `memory_lookback_days`.
  - для `topic_validator`: `confirmed_topics_target`, `max_retries`.
  - для `topic_ranker`: `scoring_rubric`, `top_n_to_keep`.
  - для `headline_writer`: `headlines_per_article`.
  - для `image_prompt_writer`: `images_per_article`, `image_model` (`flux-pro`, `dall-e-3`...).
  - для `channel_rewriter`: `channel_id`.
- `tools_enabled` (jsonb, e.g. `["web_search","fetch_url"]`).
- `is_enabled`, `created_at`, `updated_at`.

### `pipeline_runs`
- `id`, `project_id`, `triggered_by` (`schedule`/`manual`), `status` (`queued`/`running`/`completed`/`failed`), `started_at`, `finished_at`, `error`.

### `agent_runs`
- `id`, `pipeline_run_id`, `agent_id`, `parent_agent_run_id` (для ветвлений).
- `status`, `started_at`, `finished_at`.
- `inputs` (jsonb) — что подали на вход.
- `output` (jsonb) — структурированный ответ.
- `cost_usd`, `tokens_in`, `tokens_out`.
- `attempt`, `error`.

### `llm_calls`
- `id`, `agent_run_id`, `model`, `messages` (jsonb), `tools` (jsonb), `response` (jsonb), `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`.

### `topics`
- `id`, `project_id`, `pipeline_run_id`.
- `title`, `summary`, `language` (`ru`/`en`/`bi`).
- `source_links` (jsonb).
- `status` (`generated`/`validated`/`ranked`/`assigned`/`written`/`published`/`rejected`).
- `score_total`, `scores` (jsonb — по критериям).
- `embedding` (vector(1536)) — для дедупа по семантике.
- `created_at`.

### `articles`
- `id`, `topic_id`, `language` (`ru`/`en`).
- `research_brief` (text), `research_validated` (text).
- `body_full` (text) — большая статья «универсал».
- `headlines` (jsonb — список вариантов от headline_writer).
- `chosen_headline` (text) — после QA.
- `status` (`drafting`/`written`/`qa_passed`/`failed`).
- `qa_notes` (text), `qa_score` (int).
- `created_at`, `written_at`.

### `media_assets`
- `id`, `article_id` (nullable), `kind` (`image`/`video`/`audio`/`subtitle`).
- `prompt`, `model`, `provider_meta` (jsonb).
- `storage_url`, `mime`, `width`, `height`, `duration_s`.
- `chosen` (bool) — выбран ли QA.
- `created_at`.

### `channels`
- `id`, `project_id`, `kind` (enum: `telegram`,`vk`,`max`,`facebook`,`instagram`,`ok`,`threads`,`x`, `instagram_reels`,`snapchat`,`tiktok`,`youtube_shorts`,`vk_video`,`rutube`).
- `name`, `language` (`ru`/`en`).
- `is_video` (bool).
- `posts_per_day` (int).
- `slots` — связь с `channel_slots`.
- `selection_strategy` (`by_rank`/`random_among_written`).
- `credentials` (jsonb, **шифруется**) — токены/cookies/refresh tokens.
- `rewriter_agent_id` (FK на `agents`, nullable; для видео — null).

### `channel_slots`
- `id`, `channel_id`, `time_local` (TIME), `enabled`.

### `posts`
- `id`, `article_id`, `channel_id`.
- `body` (text) — переписанный под канал текст.
- `headline` (text), `image_asset_id`, `video_asset_id`.
- `status` (`pending`/`scheduled`/`published`/`failed`).
- `scheduled_for` (datetime), `published_at` (datetime).
- `external_url`, `provider_response` (jsonb), `error`.

### `publications` *(сводная таблица, опционально)*
- Денормализованный лог: `article_id`, `channel_id`, `published_at`. Для быстрых фильтров «опубликовано/нет».

### `topic_memory`
- Для генератора тем: список уже **опубликованных** тем за N дней.
- Реализуется как view над `topics` где `id IN (SELECT topic_id FROM articles WHERE id IN (SELECT article_id FROM posts WHERE status='published'))`.

### Индексы и инварианты
- Уник: `(project_id, role)` для большинства ролей (по одному агенту на роль), кроме `channel_rewriter` (по одному на канал).
- Индексы: `agent_runs(pipeline_run_id)`, `posts(scheduled_for) where status='scheduled'`, `topics USING ivfflat(embedding)`.
- Soft delete для `topics` и `articles` — `deleted_at`.
