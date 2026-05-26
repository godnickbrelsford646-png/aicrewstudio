"""Text-to-speech tool. Real OpenAI gpt-4o-mini-tts + mock fallback.

Routing by model string (mirrors aicrew/tools/image_gen.py):
  - ``mock:placeholder`` (or empty / "mock") -> placeholder MP3 (no API call).
  - ``openai:<model>`` -> POST https://api.openai.com/v1/audio/speech
                         using $OPENAI_API_KEY.
  - ``302ai:<model>``  -> POST https://api.302.ai/v1/audio/speech
                         using $AI302_API_KEY (302.ai proxies the OpenAI
                         /v1/audio/speech endpoint shape unchanged).

Mock mode writes a tiny but well-formed MP3 placeholder under
``settings.media_dir`` and returns a ``/media/<file>.mp3`` URL — every
magic-byte sniffer recognises it as ``audio/mpeg``, browsers will refuse
to play it (junk frame body) but the pipeline can still chain.

Real path returns the actual MP3 bytes from the provider; for
``gpt-4o-mini-tts`` the response Content-Type is ``audio/mpeg`` and the
body is the raw MP3 file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
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
    # Choose the API key that matches the model namespace. If the user
    # has only AI302_API_KEY set we should use the 302.ai proxy of the
    # OpenAI TTS endpoint, not silently fall back to mock.
    if model.startswith("302ai:"):
        api_key = os.environ.get("AI302_API_KEY", "")
    else:
        # 'openai:...' or bare model name (treated as OpenAI).
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
            data = _call_real_tts(text, model, voice_id, speed)
        except Exception as exc:
            log.exception("TTS provider failed, falling back to placeholder")
            log.warning(
                "TTS generation failed: %s. Common causes: out of credits "
                "on OpenAI/302.ai, wrong voice id, or content blocked by "
                "the provider's safety filter. See sidecar tts_%s.error.txt "
                "for details.", exc, h,
            )
            data = _placeholder_mp3(seed=f"{text[:80]}|{idx}|{voice_id}|err")
            try:
                err_path = os.path.join(settings.media_dir, f"tts_{h}.error.txt")
                with open(err_path, "w", encoding="utf-8") as efh:
                    efh.write(
                        f"TTS generation failed\n"
                        f"model: {model}\n"
                        f"voice_id: {voice_id}\n"
                        f"speed: {speed}\n"
                        f"language: {language}\n"
                        f"text (first 500 chars): {text[:500]}\n"
                        f"error: {exc}\n"
                        f"hint: check OpenAI/302.ai credits and that "
                        f"voice_id is one of the supported voices "
                        f"(alloy, ash, ballad, coral, echo, fable, "
                        f"nova, onyx, sage, shimmer, verse).\n"
                    )
            except Exception:  # pragma: no cover - diagnostic best-effort
                log.exception("failed to write TTS error sidecar file")
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
# Real OpenAI / 302.ai TTS path.
# ---------------------------------------------------------------------------


def _call_real_tts(text: str, model: str, voice_id: str, speed: float) -> bytes:
    """Routes to OpenAI or 302.ai based on the model namespace prefix.

    Both providers expose the same ``/v1/audio/speech`` endpoint shape;
    only the base URL and the API key differ.
    """
    if model.startswith("302ai:"):
        real_model = model.split(":", 1)[1]
        api_key = os.environ.get("AI302_API_KEY", "")
        if not api_key:
            raise RuntimeError("AI302_API_KEY is empty in environment.")
        base_url = "https://api.302.ai/v1"
    else:
        # 'openai:...' or bare model -> OpenAI native.
        real_model = model.split(":", 1)[1] if ":" in model else model
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is empty in environment.")
        base_url = "https://api.openai.com/v1"
    return _call_openai_tts(text, real_model, voice_id, speed, api_key, base_url)


def _call_openai_tts(text: str, real_model: str, voice_id: str, speed: float,
                      api_key: str, base_url: str) -> bytes:
    """Synchronous call to ``/v1/audio/speech``.

    Wire format::

        POST {base_url}/audio/speech
        Authorization: Bearer <api_key>
        Content-Type: application/json
        {
            "model":           "gpt-4o-mini-tts",
            "input":           <text>,
            "voice":           <voice_id>,    # alloy / ash / ballad / coral
                                              # / echo / fable / nova / onyx
                                              # / sage / shimmer / verse
            "speed":           <float>,       # 0.25..4.0
            "response_format": "mp3"
        }
        -> 200 OK with body = MP3 bytes (Content-Type: audio/mpeg).

    Cost: ≈$0.015 / minute of synthesised audio at the time of writing.

    OpenAI's TTS endpoint does not enforce a fixed input limit per request
    but practical voiceovers are short — we trim to 4096 chars defensively
    so a runaway voice_director output cannot accidentally cause a 400.
    """
    body = {
        "model": real_model,
        "input": text[:4096],
        "voice": voice_id,
        "speed": float(speed),
        "response_format": "mp3",
    }
    url = base_url.rstrip("/") + "/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    log.info("tts: call model=%s voice=%s speed=%.2f endpoint=%s text=%r",
             real_model, voice_id, speed, url, text[:100])
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        log.error("tts HTTP %s: %s", exc.code, err_body[:500])
        raise RuntimeError(
            f"TTS provider returned HTTP {exc.code}: {err_body[:300]}"
        ) from exc
    if not data:
        raise RuntimeError("TTS provider returned empty body")
    log.info("tts ok: model=%s voice=%s bytes=%d", real_model, voice_id, len(data))
    return data
