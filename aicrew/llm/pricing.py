"""Static price table per model (USD per 1k tokens). Update as needed."""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # model -> (input_per_1k, output_per_1k)
    "openai:gpt-4o": (0.005, 0.015),
    "openai:gpt-4o-mini": (0.00015, 0.0006),
    "openai:gpt-5": (0.01, 0.03),
    "openai:gpt-5.3": (0.01, 0.03),
    "mock:cheap": (0.0, 0.0),
    "mock:smart": (0.0, 0.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return round((tokens_in / 1000.0) * p_in + (tokens_out / 1000.0) * p_out, 6)



# ---------------------------------------------------------------------------
# Media generation prices.
#
# These tables mirror the public 302.ai / OpenAI rate cards (May 2026
# snapshot). Numbers are rounded to the nearest cent / sub-cent where
# the providers themselves publish a rounded figure. They are used by
# aicrew/tools/{image_gen,tts_gen,video_gen,whisper_transcribe}.py to
# label every media asset with its cost so the project dashboard can
# attribute spend to image / video / TTS / Whisper alongside LLM calls.
#
# Updating: change one number here and the dashboard immediately
# reflects the new rate for all NEW media calls. Existing media_assets
# rows keep their historical cost_usd (we never rewrite history) — the
# downside is purely accounting accuracy on past data, never correctness.
# ---------------------------------------------------------------------------

# Image generation: USD per image (Wan 2.x family @ 1280x720; 9:16 frames
# share the same per-image rate on 302.ai's billing).
IMAGE_PRICES: dict[str, float] = {
    "302ai:wan2.7-image":   0.05,
    "302ai:wan2.6-image":   0.05,
    "302ai:wan2.5-image":   0.04,
    "302ai:flux-pro":       0.04,
    "302ai:flux-schnell":   0.003,
    "openai:dall-e-3":      0.04,
    "openai:gpt-image-1":   0.04,
    "mock:placeholder":     0.0,
}

# Image-to-video: USD per 5 seconds of generated clip.
VIDEO_PRICES: dict[str, float] = {
    "302ai:wan2.2-i2v":     0.12,
    "mock:placeholder":     0.0,
}

# TTS: USD per 1M characters of input text. gpt-4o-mini-tts is the cheap
# default (~$0.6/1M); legacy tts-1 / tts-1-hd are listed for completeness
# and only used if the user explicitly picks them in voice_director params.
TTS_PRICES: dict[str, float] = {
    "openai:gpt-4o-mini-tts": 0.6,
    "openai:tts-1":           15.0,
    "openai:tts-1-hd":        30.0,
    "mock:placeholder":       0.0,
}

# Speech-to-text (Whisper): USD per minute of input audio.
WHISPER_PRICES: dict[str, float] = {
    "openai:whisper-1":     0.006,
    "302ai:whisper-1":      0.006,
    "mock:placeholder":     0.0,
}


def estimate_image_cost(model: str, count: int = 1) -> float:
    """USD cost for ``count`` images at the listed per-image rate.

    Unknown models price at $0 (lossy-zero) so an experimental model name
    in agent params never makes the dashboard balloon with phantom spend.
    """
    return round(IMAGE_PRICES.get(model, 0.0) * max(0, int(count)), 6)


def estimate_video_cost(model: str, duration_s: float) -> float:
    """USD cost for a clip of ``duration_s`` seconds.

    Rates are quoted per 5s on 302.ai's invoice but bill linearly within
    a clip — a 10s i2v call costs 2x a 5s call.
    """
    p = VIDEO_PRICES.get(model, 0.0)
    return round(p * (max(0.0, float(duration_s)) / 5.0), 6)


def estimate_tts_cost(model: str, chars: int) -> float:
    """USD cost for synthesising ``chars`` characters of speech."""
    p = TTS_PRICES.get(model, 0.0)
    return round(p * (max(0, int(chars)) / 1_000_000.0), 6)


def estimate_whisper_cost(model: str, duration_s: float) -> float:
    """USD cost for transcribing ``duration_s`` seconds of audio."""
    p = WHISPER_PRICES.get(model, 0.0)
    return round(p * (max(0.0, float(duration_s)) / 60.0), 6)
