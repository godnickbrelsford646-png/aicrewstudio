"""Text-to-speech tool. Mock + real-API skeleton (OpenAI gpt-4o-mini-tts).

Mock mode writes a tiny but well-formed MP3 placeholder under
``settings.media_dir`` and returns a ``/media/<file>.mp3`` URL. The placeholder
has a valid ID3v2 header followed by a couple of bytes that look like an MP3
frame sync — enough for browsers/OS to identify it as ``audio/mpeg``.

Real path: ``openai:gpt-4o-mini-tts`` via /v1/audio/speech (skeleton only —
never called in sandbox).
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.tts_gen")


@dataclass
class TTSResult:
    storage_url: str
    mime: str
    duration_s: float
    model: str
    voice_id: str


def _placeholder_mp3(seed: str = "") -> bytes:
    """Build a >=100 byte blob that sniffs as audio/mpeg.

    Layout:
        * ID3v2.3 header with a 100-byte payload (so total tag = 110 bytes).
        * One MPEG-1 Layer III frame sync ``\xff\xfb`` plus a junk frame body.

    Browsers will refuse to play it (the tag is empty, the frame is junk),
    but every magic-byte sniffer correctly identifies the file as MP3 — and
    that's all the skeleton UI needs to confirm the pipeline ran.
    """
    payload_size = 100
    # Synchsafe size encoding (7 bits per byte).
    s1 = (payload_size >> 21) & 0x7f
    s2 = (payload_size >> 14) & 0x7f
    s3 = (payload_size >> 7) & 0x7f
    s4 = payload_size & 0x7f
    id3_header = (
        b"ID3" + b"\x03\x00" + b"\x00"
        + bytes([s1, s2, s3, s4])
    )
    # Mix the seed into the padding so two TTS clips for different scenes
    # aren't byte-identical.
    seed_bytes = hashlib.sha1(seed.encode("utf-8")).digest() if seed else b""
    pad = (b"\x00" * payload_size)
    if seed_bytes:
        pad = seed_bytes + pad[len(seed_bytes):]
    # Fake MP3 frame sync so the trailing bytes also look like an audio frame.
    fake_frame = b"\xff\xfb" + (b"\x00" * 32)
    return id3_header + pad + fake_frame


def generate_tts(
    text: str,
    *,
    settings: Settings,
    idx: int = 0,
    model: str = "mock:placeholder",
    voice_id: str = "onyx",
    speed: float = 1.0,
    language: str = "ru",
) -> TTSResult:
    """Generates an MP3 voiceover. Mock by default; real OpenAI path is a skeleton."""
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1(
        (text + "|" + str(idx) + "|" + voice_id + "|" + str(speed) + "|" + model)
        .encode("utf-8")
    ).hexdigest()[:16]
    filename = f"tts_{h}.mp3"
    path = os.path.join(settings.media_dir, filename)
    # Estimate duration by character count (roughly 15 chars per second of
    # clear narration). Min 2 seconds so a one-word voiceover still has
    # plausible duration metadata.
    duration_s = max(2.0, len(text) / 15.0)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    use_mock = (
        model.startswith("mock:")
        or model in ("", "mock")
        or not api_key
    )
    if os.path.exists(path):
        return TTSResult(
            storage_url=f"/media/{filename}", mime="audio/mpeg",
            duration_s=duration_s, model=model, voice_id=voice_id,
        )
    if use_mock:
        data = _placeholder_mp3(seed=f"{text[:80]}|{idx}|{voice_id}")
    else:
        try:
            data = _call_openai_tts(text, voice_id, speed, api_key)
        except Exception as exc:
            log.exception("TTS provider failed, falling back to placeholder: %s", exc)
            data = _placeholder_mp3(seed=f"{text[:80]}|{idx}|{voice_id}|err")
    with open(path, "wb") as fh:
        fh.write(data)
    return TTSResult(
        storage_url=f"/media/{filename}",
        mime="audio/mpeg",
        duration_s=duration_s,
        model=model,
        voice_id=voice_id,
    )


# ---------------------------------------------------------------------------
# Real OpenAI TTS skeleton — NEVER called in sandbox / mock mode.
# ---------------------------------------------------------------------------


def _call_openai_tts(text: str, voice_id: str, speed: float, api_key: str) -> bytes:
    """Skeleton for OpenAI gpt-4o-mini-tts call.

    Wire format:
        POST https://api.openai.com/v1/audio/speech
        Authorization: Bearer <OPENAI_API_KEY>
        Content-Type: application/json
        {
            "model": "gpt-4o-mini-tts",
            "input": <text>,
            "voice": <voice_id>,    # e.g. "onyx", "alloy", "verse"
            "speed": <float>,       # 0.25..4.0
            "response_format": "mp3"
        }
        -> 200 OK with body = MP3 bytes (Content-Type: audio/mpeg).

    Cost: ~$0.015 / minute of synthesised audio at the time of writing.
    """
    raise NotImplementedError(
        "Real OpenAI TTS call is a skeleton; sandbox has no internet."
    )
