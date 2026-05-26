"""Audio transcription tool. Real OpenAI Whisper-1 + mock fallback.

Routing by model string (auto-detected from env if not specified, mirrors
the routing in aicrew/tools/tts_gen.py):
  - ``mock:placeholder`` (or empty / "mock") -> deterministic stub SRT.
  - ``openai:whisper-1``  -> POST https://api.openai.com/v1/audio/transcriptions
                             using $OPENAI_API_KEY.
  - ``302ai:whisper-1``   -> POST https://api.302.ai/v1/audio/transcriptions
                             using $AI302_API_KEY.

If ``model`` is not passed by the caller we auto-pick: prefer OpenAI when
$OPENAI_API_KEY is set, fall back to 302.ai when only $AI302_API_KEY is
present, otherwise mock. This keeps the pipeline working without changes
when only one key is configured.

The audio is uploaded as ``multipart/form-data`` with the OpenAI fields
``model`` / ``response_format=srt`` / ``language`` / ``file``. The response
body is the raw SRT text we return verbatim — ``subtitle_styler`` then
converts it to ASS for burn-in by ffmpeg.
"""

from __future__ import annotations

import logging
import os
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.whisper_transcribe")


@dataclass
class TranscribeResult:
    srt_text: str
    duration_s: float


def _format_ts(t: float) -> str:
    """Format ``t`` seconds as SRT timestamp ``HH:MM:SS,mmm``."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:  # avoid rounding to 1.000s
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _mock_srt(language: str, n_blocks: int = 4, duration_s: float = 12.0) -> str:
    """Build a minimal but valid SRT with 3-5 stub blocks."""
    n = max(3, min(5, n_blocks))
    step = duration_s / n
    if (language or "").lower().startswith("ru"):
        line = "Сегмент {idx} автотранскрипции."
    else:
        line = "Auto-transcribed segment {idx}."
    parts: list[str] = []
    for i in range(n):
        start = i * step
        end = (i + 1) * step
        parts.append(
            f"{i + 1}\n"
            f"{_format_ts(start)} --> {_format_ts(end)}\n"
            f"{line.format(idx=i + 1)}\n"
        )
    return "\n".join(parts).strip() + "\n"


def transcribe_audio(
    audio_url: str,
    *,
    settings: Settings,
    language: str = "ru",
    model: str | None = None,
) -> TranscribeResult:
    """Transcribe a local audio file and return SRT text + duration.

    ``audio_url`` is the URL we returned from :func:`generate_tts` (e.g.
    ``/media/tts_<hash>.mp3``). We resolve it to a local file under
    ``settings.media_dir`` and upload that file to Whisper as multipart.

    Mock-mode (no API key, or model='mock:*') returns a deterministic
    4-segment SRT spanning 12 seconds, so the rest of the pipeline can
    chain even without internet.
    """
    # Auto-pick provider when the caller did not specify one.
    if model is None:
        if os.environ.get("OPENAI_API_KEY"):
            model = "openai:whisper-1"
        elif os.environ.get("AI302_API_KEY"):
            model = "302ai:whisper-1"
        else:
            model = "mock:placeholder"
    # Resolve provider-specific endpoint and key.
    if model.startswith("302ai:"):
        api_key = os.environ.get("AI302_API_KEY", "")
        base_url = "https://api.302.ai/v1"
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = "https://api.openai.com/v1"
    real_model = model.split(":", 1)[1] if ":" in model else model
    use_mock = (
        model.startswith("mock:")
        or model in ("", "mock")
        or not api_key
    )
    if use_mock:
        srt = _mock_srt(language)
        return TranscribeResult(srt_text=srt, duration_s=12.0)

    # Resolve the audio file on disk. Whisper needs the bytes themselves;
    # we never give it a URL.
    audio_basename = os.path.basename(audio_url or "")
    audio_path = os.path.join(settings.media_dir, audio_basename)
    if not audio_basename or not os.path.exists(audio_path):
        log.warning(
            "whisper: audio file %s not found on disk, falling back to mock "
            "SRT (the upstream TTS step probably failed or was skipped)",
            audio_path,
        )
        srt = _mock_srt(language)
        return TranscribeResult(srt_text=srt, duration_s=12.0)

    try:
        srt = _call_openai_whisper(
            audio_path, real_model, base_url, api_key, language,
        )
    except Exception as exc:
        log.exception("whisper call failed, falling back to mock SRT")
        log.warning(
            "whisper failed: %s. Common causes: out of credits on "
            "OpenAI/302.ai, audio file too large (>25MB Whisper limit), "
            "wrong language code, or unsupported audio format. See sidecar "
            "%s.error.txt for details.", exc, audio_basename,
        )
        try:
            err_path = os.path.join(
                settings.media_dir, f"{audio_basename}.error.txt"
            )
            with open(err_path, "w", encoding="utf-8") as efh:
                efh.write(
                    f"whisper transcription failed\n"
                    f"model: {model}\n"
                    f"audio_url: {audio_url}\n"
                    f"audio_path: {audio_path}\n"
                    f"language: {language}\n"
                    f"error: {exc}\n"
                    f"hint: Whisper requires <25MB files; supported formats "
                    f"are mp3, mp4, mpeg, mpga, m4a, wav, webm.\n"
                )
        except Exception:  # pragma: no cover - diagnostic best-effort
            log.exception("failed to write whisper error sidecar file")
        srt = _mock_srt(language)

    duration_s = _estimate_duration_from_srt(srt) or 12.0
    return TranscribeResult(srt_text=srt, duration_s=duration_s)


def _estimate_duration_from_srt(srt: str) -> float:
    """Pull the last ``--> HH:MM:SS,mmm`` timestamp from an SRT body."""
    last = 0.0
    for line in srt.splitlines():
        if "-->" not in line:
            continue
        try:
            _, end = line.split("-->")
            end = end.strip().replace(",", ".")
            h, m, s = end.split(":")
            last = max(last, int(h) * 3600 + int(m) * 60 + float(s))
        except Exception:
            continue
    return last


# ---------------------------------------------------------------------------
# Real OpenAI / 302.ai Whisper call.
# ---------------------------------------------------------------------------

# Map our file extensions to MIME types so Whisper's content-type sniffing
# is happy. Whisper only really needs *something* in audio/* or video/*,
# but we keep this honest so logs read cleanly.
_AUDIO_MIME_BY_EXT = {
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".m4a":  "audio/mp4",
    ".mpga": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".wav":  "audio/wav",
    ".webm": "audio/webm",
    ".ogg":  "audio/ogg",
    ".flac": "audio/flac",
}


def _build_multipart(fields: dict[str, str], file_name: str, file_bytes: bytes,
                      file_mime: str) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body.

    Returns ``(body, boundary)``. We avoid the ``email`` module because it
    re-encodes binary payloads and corrupts MP3 frames; building the bytes
    by hand is the simplest correct path.
    """
    boundary = "----aicrew" + secrets.token_hex(16)
    sep = ("--" + boundary + "\r\n").encode("ascii")
    end = ("--" + boundary + "--\r\n").encode("ascii")
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(sep)
        chunks.append(
            (f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode("ascii")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(sep)
    chunks.append(
        (
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{file_name}"\r\n'
            f"Content-Type: {file_mime}\r\n\r\n"
        ).encode("ascii")
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(end)
    return b"".join(chunks), boundary


def _call_openai_whisper(audio_path: str, real_model: str, base_url: str,
                         api_key: str, language: str) -> str:
    """Real call to ``/v1/audio/transcriptions``.

    Wire format::

        POST {base_url}/audio/transcriptions
        Authorization: Bearer <api_key>
        Content-Type: multipart/form-data; boundary=<...>

        Form fields:
          model            = "whisper-1"
          response_format  = "srt"
          language         = ISO 639-1 code (optional, e.g. "ru")
          file             = <binary audio bytes>

        -> 200 OK, body = SRT text (Content-Type: text/plain).

    Cost: ≈$0.006 per minute of input audio at the time of writing.
    """
    with open(audio_path, "rb") as fh:
        audio_bytes = fh.read()
    file_name = os.path.basename(audio_path) or "audio.mp3"
    ext = os.path.splitext(file_name)[1].lower()
    file_mime = _AUDIO_MIME_BY_EXT.get(ext, "audio/mpeg")
    fields: dict[str, str] = {
        "model": real_model,
        "response_format": "srt",
    }
    # Whisper accepts ISO 639-1 language hints (`ru`, `en`); a longer string
    # like 'ru-RU' would be rejected. We normalise to the first two chars.
    if language:
        fields["language"] = language.strip()[:2].lower()
    body, boundary = _build_multipart(fields, file_name, audio_bytes, file_mime)
    url = base_url.rstrip("/") + "/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    log.info("whisper: call model=%s endpoint=%s file=%s bytes=%d lang=%s",
             real_model, url, file_name, len(audio_bytes),
             fields.get("language") or "(auto)")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        # Whisper transcription can take 5-30s for short clips; allow a
        # generous timeout for longer audio.
        with urllib.request.urlopen(req, timeout=300) as resp:
            srt = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        log.error("whisper HTTP %s: %s", exc.code, err_body[:500])
        raise RuntimeError(
            f"Whisper provider returned HTTP {exc.code}: {err_body[:300]}"
        ) from exc
    if not srt.strip():
        raise RuntimeError(
            f"Whisper returned an empty response (model={real_model}, "
            f"file={file_name}). The audio may be silent or corrupted."
        )
    log.info("whisper ok: model=%s srt_chars=%d", real_model, len(srt))
    return srt
