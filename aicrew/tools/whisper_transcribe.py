"""Audio transcription tool. Mock + real-API skeleton (OpenAI Whisper).

Mock mode synthesises a plausible SRT block with 3-5 stub subtitles spaced
1-3 seconds apart, so downstream consumers (subtitle_styler) get a realistic
input shape even though no actual audio was decoded.

Real path: ``openai:whisper-1`` (multipart upload to /v1/audio/transcriptions
with response_format=srt) — skeleton only, never called in sandbox.
"""

from __future__ import annotations

import logging
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
) -> TranscribeResult:
    """Transcribe an audio file and return SRT text + duration.

    Mock mode: returns a deterministic SRT with 4 segments over 12 seconds.
    Real path: OpenAI Whisper-1 — skeleton only.
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY", "")
    use_mock = not api_key  # any deployment without OpenAI creds = mock
    if use_mock:
        srt = _mock_srt(language)
        return TranscribeResult(srt_text=srt, duration_s=12.0)
    try:
        srt = _call_openai_whisper(audio_url, api_key, language)
    except Exception as exc:
        log.exception("whisper call failed, falling back to mock SRT: %s", exc)
        srt = _mock_srt(language)
    # Estimate total duration from the last "end" timestamp in the SRT
    # (best-effort; a real implementation would read it from the audio).
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
# Real OpenAI Whisper skeleton — NEVER called in sandbox / mock mode.
# ---------------------------------------------------------------------------


def _call_openai_whisper(audio_path: str, api_key: str, language: str) -> str:
    """Skeleton for OpenAI Whisper-1 call.

    Wire format (multipart/form-data):
        POST https://api.openai.com/v1/audio/transcriptions
        Authorization: Bearer <OPENAI_API_KEY>
        Content-Type: multipart/form-data; boundary=...

        Form fields:
          model            = "whisper-1"
          response_format  = "srt"
          language         = ISO 639-1 code (optional, e.g. "ru")
          file             = <binary audio bytes>

        -> 200 OK with body = SRT text (Content-Type: text/plain).

    Cost: $0.006 / minute of input audio at the time of writing.
    """
    raise NotImplementedError(
        "Real OpenAI Whisper call is a skeleton; sandbox has no internet."
    )
