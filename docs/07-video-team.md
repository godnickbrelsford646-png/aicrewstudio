# 07. Видео‑команда

> Команда, которая делает короткое видео из готовой статьи. Реализуем во 2‑й итерации, но архитектурно учитываем сейчас.

## Принципы

- **Один пайплайн на оба языка.** Картинки/композиция/монтаж общие. Различаются только **озвучка** и **субтитры**.
- **Параметр `videos_per_article`** — сколько разных вариантов видео делать (можно A/B‑тестировать).
- **Источник правды** — финальная статья (`articles.body_full` + `chosen_headline`).

## Состав команды

| Роль | Назначение | Инструменты | Выход |
|---|---|---|---|
| `VideoScenarist` | Из статьи делает сценарий, делит на сцены (5–9 сцен на 30–60s) | LLM | `Scenario { hook, scenes: [{idx, voiceover, on_screen_text, b_roll_idea, duration_s}], cta }` |
| `ScenePromptWriter` | Для каждой сцены пишет промт картинки и описание движения камеры | LLM | `[{scene_idx, image_prompt, motion_prompt, aspect: "9:16"}]` |
| `SceneImageGen` | Инструмент: генерит картинку для сцены | Flux/SDXL/DALL‑E | `MediaAsset(kind=image)` |
| `SceneVideoGen` | Инструмент: image‑to‑video (5s клип на сцену) | Runway / Kling / Pika / Luma | `MediaAsset(kind=video, duration_s≈5)` |
| `Voicer` | TTS озвучка сценария на нужном языке | OpenAI TTS / ElevenLabs / Yandex SpeechKit | `MediaAsset(kind=audio)` (один файл на язык) |
| `Subtitler` | Делает субтитры (из voiceover‑таймингов или Whisper) | Whisper / форс‑алайн | `MediaAsset(kind=subtitle, format=srt/vtt)` |
| `VideoComposer` | Инструмент (без LLM): склеивает сцены, накладывает озвучку и субтитры | ffmpeg / remotion | `MediaAsset(kind=video, final=true, language=ru/en)` |
| `VideoQA` | Просматривает (по миниатюрам/субтитрам) и подтверждает финал | LLM | `{ok: bool, issues:[...]}` |

## Поток

```
Article ─► VideoScenarist ─► ScenePromptWriter ─┬─► SceneImageGen (по сцене)
                                                └─► SceneVideoGen (img→video)
                                       ▼
                              [сцены готовы, общие для RU/EN]
                                       │
                ┌──────────────────────┴──────────────────────┐
                ▼                                             ▼
          Voicer(ru)                                    Voicer(en)
                │                                             │
          Subtitler(ru)                                 Subtitler(en)
                │                                             │
          VideoComposer ─► Final RU video           VideoComposer ─► Final EN video
                │                                             │
          VideoQA(ru)                                  VideoQA(en)
                │                                             │
          MediaAsset(ready, ru)                       MediaAsset(ready, en)
                │                                             │
                ▼                                             ▼
        Видео‑публикаторы RU                          Видео‑публикаторы EN
```

## Параметры (на уровне проекта/команды)

- `videos_per_article: int` — сколько финальных видео делать (умножает Voicer/Composer; сцены общие).
- `target_duration_s: int` — 15/30/45/60 (Shorts/Reels/TikTok сладкие точки).
- `aspect: "9:16"` (Shorts/Reels/TikTok); опционально `"1:1"`/`"16:9"` для YouTube/VK Video.
- `style_preset` — `cinematic`, `talking_head_b-roll`, `kinetic_typography`, `whiteboard`.
- `voice_id_ru`, `voice_id_en` — id голосов в провайдере TTS.
- `subtitles_style` — `karaoke`, `bottom_box`, `none`.
- `music_track_id` (опционально) — фоновая музыка из библиотеки.

## Что ещё стоит добавить (мои предложения)

1. **HookAuditor** — отдельный микро‑агент, который проверяет, что первая секунда «цепляет» (классика для Shorts/Reels). Может вернуть переписанный hook.
2. **B‑roll fetcher** — альтернатива genAI: вместо генерации видео, тянуть стоковые ролики с Pexels/Pixabay, если их хватает по теме (дешевле).
3. **Caption density check** — проверка, что субтитры успевают показаться (≤20 слов/сек).
4. **Loudness normalization** — `-14 LUFS` через ffmpeg loudnorm, иначе TikTok/Reels глушат.
5. **Watermark/branding** — лого канала в углу (опц.).
6. **Storyboard preview в UI** — пользователь видит миниатюры сцен и может перегенерить отдельную сцену, не пересобирая всё видео.
7. **Cost cap для видео** — самый дорогой пайплайн; полезно ставить лимит на одно видео (например, $1.50).

## Ограничения

- API image‑to‑video (Runway/Kling/Pika) — платные и медленные. Для MVP лучше: 1 видео в день на проект.
- Длительность сцены ограничена провайдером (5–10s). Для 60s видео нужно 6–12 склеек.
- Voice cloning — отдельная этическая зона; в MVP — только готовые голоса провайдера.
