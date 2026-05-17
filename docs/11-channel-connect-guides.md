# 11. Channel Connect Guides

> Эти инструкции встроены в API (`GET /api/channels/kinds`) и автоматически показываются в UI на странице каждого канала. Здесь — единый текстовый аналог.

## Текстовые каналы

### Telegram
1. Откройте `@BotFather`, команда `/newbot`. Получите **bot_token**.
2. Создайте канал. Добавьте бота администратором с правом «Публикация сообщений».
3. Узнайте `chat_id`: для приватного — через `@userinfobot` или `https://api.telegram.org/bot<TOKEN>/getUpdates` после публикации поста.
4. Поля: `bot_token`, `chat_id`. Лимит ~4096 символов, поддержка HTML/Markdown, медиагруппы до 10 элементов.

### VK
1. Создайте сообщество (или используйте существующее).
2. На dev.vk.com создайте Standalone-приложение и сгенерируйте community access_token со скоупом `wall, photos, manage, offline`.
3. Узнайте `owner_id` (отрицательное число для группы).
4. Поля: `access_token`, `owner_id`. Метод: `wall.post`.

### MAX
Публичного API нет — сейчас работает в режиме **черновиков** (контент готовится, публикуется вручную).

### Facebook (Pages)
1. Создайте Facebook Page.
2. В Meta for Developers создайте App, получите **page_access_token** (long-lived) со скоупом `pages_manage_posts, pages_read_engagement`.
3. Узнайте `page_id`.
4. Поля: `page_id`, `page_access_token`. Личные профили публиковать нельзя.

### Instagram (Business / Creator)
1. Конвертируйте Instagram-аккаунт в Business или Creator и привяжите к Facebook Page.
2. В Meta App включите Instagram Graph API. Получите `ig_user_id`, `page_access_token`.
3. Поля: `ig_user_id`, `page_access_token`. Картинка должна быть на публичном HTTPS URL.

### Одноклассники (OK)
1. Зарегистрируйте приложение на ok.ru/devaccess.
2. Получите `application_key`, `access_token`, `group_id`.
3. Метод `mediatopic.post`. Требуется одобрение приложения.

### Threads (Meta)
1. В Meta App включите Threads Graph API.
2. Получите `threads_user_id` и `access_token` через OAuth (Meta).
3. Поля: `threads_user_id`, `access_token`. Лимит ~500 символов.

### X (Twitter)
1. Войдите в X Developer Portal, создайте проект и app, оплатите тариф **Basic+**.
2. Сгенерируйте OAuth1.0a `consumer_key`, `consumer_secret`, `access_token`, `access_secret`.
3. Поля: `consumer_key`, `consumer_secret`, `access_token`, `access_secret`. Лимит 280 символов (Premium до 25k).

## Видео каналы

### YouTube Shorts
1. Google Cloud Project → включите YouTube Data API v3.
2. OAuth2 client (Desktop), пройдите авторизацию, сохраните `refresh_token`.
3. Поля: `client_id`, `client_secret`, `refresh_token`. Видео ≤60s, формат 9:16.

### TikTok
1. TikTok for Developers — `client_key`, `client_secret`.
2. Подайте заявку на Content Posting API (есть Sandbox).
3. Авторизация под аккаунтом → `access_token` (OAuth).
4. Поля: `client_key`, `client_secret`, `access_token`. Лимит 60 секунд (10 минут для бизнес).

### Instagram Reels
Те же ключи, что для Instagram (`ig_user_id`, `page_access_token`). Загрузка `media_type=REELS`, файл на публичном HTTPS URL.

### Snapchat
Полный авто-постинг ограничен — поддерживается **режим черновиков** + опционально webhook на Make/Zapier.

### VK Video
Те же ключи, что для VK (`access_token`, `owner_id`). Метод `video.save` + загрузка по полученному upload URL. Без верификации — до 720p.

### RuTube
1. Студия RuTube партнёрским аккаунтом.
2. `api_token` в кабинете партнёра (если доступно).
3. Поля: `api_token`. Часть функциональности доступна не всем — на старте режим **черновиков**.
