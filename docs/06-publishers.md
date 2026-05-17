# 06. Публикаторы каналов

> Как устроен слой публикаций, какие каналы поддерживаются, какие есть API, и где придётся идти через обходные пути.

## Общая модель

Все каналы — это сущность `channels` (см. `03-data-model.md`). У текстового канала есть `rewriter_agent_id` (свой агент‑переписчик). У видео‑канала — нет агента, только язык и расписание.

```
Article(qa_passed, lang=ru) ─┬─► ChannelRewriter(TG)   ─► Post(TG)   ─► TelegramPublisher
                             ├─► ChannelRewriter(VK)   ─► Post(VK)   ─► VKPublisher
                             └─► ...
Video(lang=ru, ready)        ─► (без агента)            ─► Post(TT)   ─► TikTokPublisher
```

## Параметры канала (UI)

- `name` — отображаемое имя (например, «TG: AI weekly RU»).
- `kind` — один из enum (см. ниже).
- `language` — `ru` или `en`.
- `is_video` — флаг.
- `posts_per_day` — целое. При его изменении генерится строка слотов (по умолчанию равномерно по дню). Пользователь правит каждое время вручную.
- `slots[]` — `[{time_local: "09:00"}, {time_local: "14:30"}, ...]`.
- `selection_strategy` — `by_rank` | `random_among_written`.
- `rewriter_prompt` (только для текстовых) — голос/стиль канала.
- `credentials` — токены (см. ниже по каждому каналу).

## Текстовые каналы

| Канал | Официальный API | Что хранить в credentials | Особенности |
|---|---|---|---|
| **Telegram** | Bot API | `bot_token`, `chat_id` (или username канала, бот должен быть админом) | Лимит ~4096 символов, поддержка Markdown/HTML, медиа‑группы. Самый простой канал. |
| **VK** | VK API | `access_token` (community token со скоупом `wall,photos,manage`), `owner_id` (ID группы со знаком «−») | `wall.post`, картинки через `photos.getWallUploadServer`. Лимит ~16k символов. |
| **MAX** | (мессенджер от VK) | На 2026 — открытого публичного API для авто‑постинга в каналы нет. | **Фоллбек:** интеграция через webhook/бот, либо ручная публикация из «черновиков». Заложить интерфейс, реализовать когда API появится. |
| **Facebook** | Graph API (Pages) | `page_id`, `page_access_token` (long‑lived, скоуп `pages_manage_posts`) | Личные профили публиковать нельзя — только страницы. |
| **Instagram** | Graph API (Business/Creator) | `ig_user_id`, `page_access_token` | Только бизнес/креатор аккаунт, привязанный к FB Page. Личный аккаунт — нельзя. Сначала создаётся `media`‑контейнер, потом `media_publish`. Картинка должна быть на публичном URL. |
| **OK (Одноклассники)** | OK API | `application_key`, `access_token`, `group_id` | Метод `mediatopic.post`. Требуется одобрение приложения. |
| **Threads** | Threads Graph API (есть с 2024) | `threads_user_id`, `access_token` | Лимит ~500 символов. Поддерживает текст + 1 медиа. Auth через FB/Meta. |
| **X (Twitter)** | X API v2 | `oauth2 client_id/secret` или `bearer/access_token`+`access_secret`, `user_id` | Платный тариф для публикаций (Basic+). Лимит 280 символов (Premium — больше). Поддерживает треды — наш ChannelRewriter может вернуть массив `tweets[]`. |

### Лимиты, которые знает ChannelRewriter

| Канал | `max_chars` | Доп. |
|---|---|---|
| Telegram | 4096 | Поддерживает HTML, эмодзи |
| VK | 16000 | Хэштеги в конец |
| MAX | 4000 | (предварительно) |
| Facebook | 63206 | Лучше держать ≤2200 для охватов |
| Instagram | 2200 (caption) | Хэштеги до 30 |
| OK | 32000 | — |
| Threads | 500 | Можно треды до 25 постов |
| X | 280 (Free/Basic) / 25000 (Premium) | Треды |

Все значения хранятся в БД как пресеты — пользователь может править.

## Видео каналы

| Канал | Официальный API | Что нужно | Особенности |
|---|---|---|---|
| **YouTube Shorts** | YouTube Data API v3 | OAuth2 token (скоуп `youtube.upload`) | Видео ≤60s, формат 9:16, тайтл/описание/тэги. |
| **TikTok** | TikTok Content Posting API | `client_key`, `client_secret`, `access_token` (требует одобрения приложения; есть Sandbox) | Лимит 60s (или 10m для бизнес). Загрузка через init+upload+publish. |
| **Instagram Reels** | Graph API | Те же ключи, что для IG | `media_type=REELS`, ссылка на mp4 на публичном URL. |
| **Snapchat** | Snap Kit / Creative Kit | OAuth + Creative Kit | Полноценный авто‑постинг ограничен; чаще — share intent в моб. приложение. **Фоллбек:** черновик/ручная публикация. |
| **VK Video** | VK API | `access_token`, метод `video.save` + upload | До 720p без верификации, дальше — с верификацией. |
| **RuTube** | RuTube API (есть, но ограниченный) | `api_token` от партнёрского кабинета | Авто‑заливка возможна не для всех аккаунтов. **Фоллбек:** черновики. |

### Стратегия фоллбеков
Для каналов без полноценного API (MAX, Snapchat, иногда RuTube/Instagram personal):
1. **Черновик‑режим:** генерим готовый пост (текст+медиа+хэштеги) и отдаём в UI «к публикации вручную» с deep‑link/инструкцией.
2. **Buffer/Make/Zapier:** один общий webhook → они сами разносят. Заложить generic `WebhookPublisher`.
3. **Browser‑automation (Playwright)** — последний резерв. Хрупко, ToS‑чувствительно. Не делать в MVP.

## Безопасность токенов

- Все `credentials` шифруются на уровне приложения (Fernet с ключом из `MASTER_KEY` env).
- В UI токены отображаются маской (`tg_bot_***1234`).
- Refresh‑токены автообновляются фоновой задачей за час до истечения.
- Логирование запросов к каналам — без тел запросов с токенами; только статусы и URL.

## Контракт `PublisherAdapter`

```python
class PublisherAdapter(Protocol):
    kind: ChannelKind
    def validate_credentials(self, creds: dict) -> None: ...
    def publish(self, post: Post, creds: dict) -> PublicationResult: ...
    def supports_media(self) -> set[Literal["image","video","carousel"]]: ...
```

`PublicationResult`:
```python
@dataclass
class PublicationResult:
    ok: bool
    external_url: str | None
    external_id: str | None
    raw_response: dict
    error: str | None = None
```

Каждый канал — отдельный класс в `app/publishers/<kind>.py`. Регистрируются в `PublisherRegistry` по `kind`.
