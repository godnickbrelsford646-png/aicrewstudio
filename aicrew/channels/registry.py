"""Channel kinds catalog with per-kind connect guides shown in the UI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelKindSpec:
    kind: str
    label: str
    is_video: bool
    default_language: str
    max_chars: int
    media_kinds: tuple[str, ...]   # what media this channel can accept
    credentials_fields: tuple[tuple[str, str], ...]  # (key, hint)
    connect_guide_md: str
    notes: str = ""


# --- Connect guides --------------------------------------------------------

TG_GUIDE = """\
### Telegram

1. Откройте @BotFather в Telegram, команда `/newbot`. Получите **bot_token**.
2. Создайте канал. Добавьте бота администратором канала с правом «Публикация сообщений».
3. Узнайте `chat_id` канала: для приватного — через `@userinfobot` или `https://api.telegram.org/bot<TOKEN>/getUpdates` после публикации поста в канал.
4. Заполните поля: `bot_token`, `chat_id`.
5. Сохранить — мы зашифруем токен в БД.

Лимиты: ~4096 символов, поддержка HTML/Markdown, медиагруппы до 10 элементов.
"""

VK_GUIDE = """\
### VK (ВКонтакте)

1. Создайте сообщество (или используйте существующее).
2. Создайте Standalone-приложение на dev.vk.com и сгенерируйте **community access_token**
   со скоупом `wall, photos, manage, offline`.
3. Узнайте `owner_id` сообщества (отрицательное число для группы, например `-12345678`).
4. Заполните: `access_token`, `owner_id`.

Метод публикации: `wall.post`. Картинки заливаем через `photos.getWallUploadServer`.
"""

MAX_GUIDE = """\
### MAX

На момент сборки публичного API для авто-постинга в каналы MAX нет.
Поддерживается **режим черновиков**: AiCrewStudio готовит пост и сохраняет его как
готовый к ручной публикации. Когда официальный API появится, переключим адаптер.

Заполнить нужно только название и язык.
"""

FB_GUIDE = """\
### Facebook (Pages)

1. Создайте Facebook Page.
2. В Meta for Developers создайте App, получите **page_access_token** (long-lived) со скоупом
   `pages_manage_posts, pages_read_engagement`.
3. Узнайте `page_id`.
4. Заполните: `page_id`, `page_access_token`.

Личные профили публиковать нельзя — только страницы.
"""

IG_GUIDE = """\
### Instagram (Business / Creator)

1. Конвертируйте Instagram-аккаунт в Business или Creator и привяжите к Facebook Page.
2. В Meta App включите Instagram Graph API. Получите `ig_user_id`, `page_access_token`.
3. Заполните: `ig_user_id`, `page_access_token`.

Картинка должна быть на публичном HTTPS URL. Сначала создаётся `media`-контейнер,
затем `media_publish`.
"""

OK_GUIDE = """\
### Одноклассники (OK)

1. Зарегистрируйте приложение на ok.ru/devaccess.
2. Получите `application_key`, `access_token`, `group_id`.
3. Метод `mediatopic.post`. Требуется одобрение приложения.

Заполните: `application_key`, `access_token`, `group_id`.
"""

THREADS_GUIDE = """\
### Threads (Meta)

1. В Meta App включите Threads Graph API.
2. Получите `threads_user_id` и `access_token` через OAuth (Meta).
3. Заполните: `threads_user_id`, `access_token`.

Лимит ~500 символов. Поддерживается текст + 1 медиа.
"""

X_GUIDE = """\
### X (Twitter)

1. Войдите в X Developer Portal, создайте проект и app, оплатите тариф **Basic+** (для записи).
2. Сгенерируйте OAuth1.0a `consumer_key`, `consumer_secret`, `access_token`, `access_secret`.
3. Заполните: `consumer_key`, `consumer_secret`, `access_token`, `access_secret`.

Лимит 280 символов (Premium до 25k). Поддерживаются треды — ChannelRewriter может вернуть массив постов.
"""

YT_SHORTS_GUIDE = """\
### YouTube Shorts

1. Создайте проект в Google Cloud, включите YouTube Data API v3.
2. Создайте OAuth2 client (Desktop), пройдите авторизацию, сохраните `refresh_token`.
3. Заполните: `client_id`, `client_secret`, `refresh_token`.

Видео ≤60s, формат 9:16. Заголовок/описание/тэги мы передаём при загрузке.
"""

TIKTOK_GUIDE = """\
### TikTok

1. Зарегистрируйте приложение в TikTok for Developers, получите `client_key` и `client_secret`.
2. Подайте заявку на Content Posting API (есть Sandbox).
3. Авторизуйтесь под аккаунтом и получите `access_token` (через OAuth).
4. Заполните: `client_key`, `client_secret`, `access_token`.

Лимит 60 секунд (10 минут для бизнес). Загрузка через init+upload+publish.
"""

IG_REELS_GUIDE = """\
### Instagram Reels

Те же ключи, что для Instagram (`ig_user_id`, `page_access_token`).
Видео загружается как `media_type=REELS`. Файл должен быть на публичном HTTPS URL.
"""

SNAP_GUIDE = """\
### Snapchat

Полный авто-постинг ограничен. Поддерживается **режим черновиков**: контент готовится
и кладётся в очередь к ручной публикации (через приложение или Snap Spotlight Studio).
Можно дополнительно подключить webhook на Make/Zapier.
"""

VK_VIDEO_GUIDE = """\
### VK Video

Используйте те же ключи, что для VK (`access_token`, `owner_id`). Метод `video.save`
+ загрузка по полученному upload URL.

Без верификации — до 720p, для большего разрешения нужно подтверждение группы.
"""

RUTUBE_GUIDE = """\
### RuTube

1. Войдите в Студию RuTube партнёрским аккаунтом.
2. Получите `api_token` в кабинете партнёра (если доступно для вашего аккаунта).
3. Заполните: `api_token`.

Часть функциональности доступна не всем аккаунтам — на старте поддерживается **режим черновиков**.
"""


# --- Catalog ---------------------------------------------------------------

CHANNEL_KINDS: dict[str, ChannelKindSpec] = {
    "telegram": ChannelKindSpec(
        kind="telegram", label="Telegram", is_video=False, default_language="ru",
        max_chars=4096, media_kinds=("image",),
        credentials_fields=(("bot_token", "Bot token from @BotFather"),
                            ("chat_id", "Channel id or @username (bot must be admin)")),
        connect_guide_md=TG_GUIDE,
    ),
    "vk": ChannelKindSpec(
        kind="vk", label="VK", is_video=False, default_language="ru",
        max_chars=16000, media_kinds=("image",),
        credentials_fields=(("access_token", "Community access token"),
                            ("owner_id", "Group owner id (negative integer)")),
        connect_guide_md=VK_GUIDE,
    ),
    "max": ChannelKindSpec(
        kind="max", label="MAX", is_video=False, default_language="ru",
        max_chars=4000, media_kinds=("image",),
        credentials_fields=(),
        connect_guide_md=MAX_GUIDE, notes="Drafts mode until public API is available.",
    ),
    "facebook": ChannelKindSpec(
        kind="facebook", label="Facebook", is_video=False, default_language="en",
        max_chars=63206, media_kinds=("image",),
        credentials_fields=(("page_id", "Facebook Page id"),
                            ("page_access_token", "Long-lived page access token")),
        connect_guide_md=FB_GUIDE,
    ),
    "instagram": ChannelKindSpec(
        kind="instagram", label="Instagram", is_video=False, default_language="en",
        max_chars=2200, media_kinds=("image",),
        credentials_fields=(("ig_user_id", "IG Business user id"),
                            ("page_access_token", "Page access token")),
        connect_guide_md=IG_GUIDE,
    ),
    "ok": ChannelKindSpec(
        kind="ok", label="Одноклассники", is_video=False, default_language="ru",
        max_chars=32000, media_kinds=("image",),
        credentials_fields=(("application_key", "Application key"),
                            ("access_token", "User/group access token"),
                            ("group_id", "Group id")),
        connect_guide_md=OK_GUIDE,
    ),
    "threads": ChannelKindSpec(
        kind="threads", label="Threads", is_video=False, default_language="en",
        max_chars=500, media_kinds=("image",),
        credentials_fields=(("threads_user_id", "Threads user id"),
                            ("access_token", "Threads access token")),
        connect_guide_md=THREADS_GUIDE,
    ),
    "x": ChannelKindSpec(
        kind="x", label="X (Twitter)", is_video=False, default_language="en",
        max_chars=280, media_kinds=("image",),
        credentials_fields=(("consumer_key", "API key"),
                            ("consumer_secret", "API secret"),
                            ("access_token", "Access token"),
                            ("access_secret", "Access token secret")),
        connect_guide_md=X_GUIDE,
    ),
    "youtube_shorts": ChannelKindSpec(
        kind="youtube_shorts", label="YouTube Shorts", is_video=True, default_language="en",
        max_chars=5000, media_kinds=("video",),
        credentials_fields=(("client_id", "OAuth client id"),
                            ("client_secret", "OAuth client secret"),
                            ("refresh_token", "OAuth refresh token")),
        connect_guide_md=YT_SHORTS_GUIDE,
    ),
    "tiktok": ChannelKindSpec(
        kind="tiktok", label="TikTok", is_video=True, default_language="en",
        max_chars=2200, media_kinds=("video",),
        credentials_fields=(("client_key", "TikTok client key"),
                            ("client_secret", "TikTok client secret"),
                            ("access_token", "User access token")),
        connect_guide_md=TIKTOK_GUIDE,
    ),
    "instagram_reels": ChannelKindSpec(
        kind="instagram_reels", label="Instagram Reels", is_video=True, default_language="en",
        max_chars=2200, media_kinds=("video",),
        credentials_fields=(("ig_user_id", "IG Business user id"),
                            ("page_access_token", "Page access token")),
        connect_guide_md=IG_REELS_GUIDE,
    ),
    "snapchat": ChannelKindSpec(
        kind="snapchat", label="Snapchat", is_video=True, default_language="en",
        max_chars=2200, media_kinds=("video",),
        credentials_fields=(),
        connect_guide_md=SNAP_GUIDE, notes="Drafts mode by default.",
    ),
    "vk_video": ChannelKindSpec(
        kind="vk_video", label="VK Video", is_video=True, default_language="ru",
        max_chars=4000, media_kinds=("video",),
        credentials_fields=(("access_token", "Community access token"),
                            ("owner_id", "Group owner id")),
        connect_guide_md=VK_VIDEO_GUIDE,
    ),
    "rutube": ChannelKindSpec(
        kind="rutube", label="RuTube", is_video=True, default_language="ru",
        max_chars=4000, media_kinds=("video",),
        credentials_fields=(("api_token", "Partner API token"),),
        connect_guide_md=RUTUBE_GUIDE, notes="Drafts mode for non-partner accounts.",
    ),
}


def list_kinds() -> list[ChannelKindSpec]:
    return list(CHANNEL_KINDS.values())


def kind_spec(kind: str) -> ChannelKindSpec:
    if kind not in CHANNEL_KINDS:
        raise KeyError(kind)
    return CHANNEL_KINDS[kind]


def connect_guide(kind: str) -> str:
    return kind_spec(kind).connect_guide_md
