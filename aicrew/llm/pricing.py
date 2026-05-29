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

# Image-to-video: USD per second of generated clip (302.ai bills i2v
# linearly by output duration). The "wan2.2-i2v" name 302.ai never
# actually exposed — production calls returned HTTP 503 "No available
# models currently". Real i2v models all carry a suffix:
#   wan2.2-i2v-plus / wan2.2-i2v-flash / wan2.5-i2v-preview /
#   wan2.6-i2v / wan2.7-i2v / wanx2.1-i2v-{turbo,plus} / happyhorse-1.0-i2v
# We pick the most common resolution per model (matches what we send
# in parameters.resolution); when the user picks a different
# resolution this stays a sane upper-bound estimate for the dashboard.
VIDEO_PRICES: dict[str, float] = {
    # Wan 2.7 i2v — newest. Our default. Sweet spot for quality/price.
    "302ai:wan2.7-i2v":          0.10,   # 720P
    # Wan 2.2 family — older but cheap.
    "302ai:wan2.2-i2v-plus":     0.15,   # 1080P
    "302ai:wan2.2-i2v-flash":    0.04,   # 720P
    # Wan 2.5 / 2.6 — also valid choices.
    "302ai:wan2.5-i2v-preview":  0.10,   # 720P
    "302ai:wan2.6-i2v":          0.10,   # 720P
    # Older Wanx 2.1 — keep for completeness.
    "302ai:wanx2.1-i2v-turbo":   0.05,
    "302ai:wanx2.1-i2v-plus":    0.15,
    # Specialty.
    "302ai:happyhorse-1.0-i2v":  0.156,
    # Mock.
    "mock:placeholder":          0.0,
    # Backwards-compat alias for any DB row still carrying the obsolete
    # bare name. Same price as wan2.7-i2v which we migrate to.
    "302ai:wan2.2-i2v":          0.10,
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

    302.ai bills i2v linearly per output second (a 10s clip costs 2x a
    5s one). At wan2.7-i2v 720P that is $0.10/s; a 5s clip is $0.50,
    a 10s clip is $1.00.
    """
    rate_per_sec = VIDEO_PRICES.get(model, 0.0)
    return round(rate_per_sec * max(0.0, float(duration_s)), 6)


def estimate_tts_cost(model: str, chars: int) -> float:
    """USD cost for synthesising ``chars`` characters of speech."""
    p = TTS_PRICES.get(model, 0.0)
    return round(p * (max(0, int(chars)) / 1_000_000.0), 6)


def estimate_whisper_cost(model: str, duration_s: float) -> float:
    """USD cost for transcribing ``duration_s`` seconds of audio."""
    p = WHISPER_PRICES.get(model, 0.0)
    return round(p * (max(0.0, float(duration_s)) / 60.0), 6)
