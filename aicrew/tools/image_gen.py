"""Image generation tool. Real 302.ai support + mock placeholder fallback.

Routing by model string:
  - ``mock:placeholder``  -> generate a coloured PNG locally (no API call).
  - ``302ai:wan2.7-image`` (or any ``302ai:<model>``)
        -> POST https://api.302.ai/v1/images/generations with that model.
  - ``openai:dall-e-3`` etc. -> OpenAI native /v1/images/generations.

The result is always saved as a local PNG under ``settings.media_dir`` and
served by the API on ``/media/<file>``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.image")


@dataclass
class ImageResult:
    storage_url: str
    mime: str
    width: int
    height: int
    model: str
    prompt: str


# A minimal 5x7 bitmap font for a few characters – enough to render seed labels
# on placeholder images without external font files.
_FONT: dict[str, list[str]] = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    " ": ["00000"] * 7,
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ":": ["00000", "00100", "00000", "00000", "00000", "00100", "00000"],
}


def _draw_text(pixels: list[list[tuple[int, int, int]]], text: str, x: int, y: int,
               color: tuple[int, int, int], scale: int = 3) -> None:
    cx = x
    for ch in text.upper():
        glyph = _FONT.get(ch, _FONT[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            px, py = cx + rx * scale + dx, y + ry * scale + dy
                            if 0 <= px < len(pixels[0]) and 0 <= py < len(pixels):
                                pixels[py][px] = color
        cx += (len(glyph[0]) + 1) * scale


def _png_bytes(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    height = len(pixels)
    width = len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw.extend([r, g, b])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=6)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _seed_color(seed: str) -> tuple[int, int, int]:
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    return (60 + h[0] % 160, 60 + h[1] % 160, 60 + h[2] % 160)


def _placeholder_png(prompt: str, model: str, idx: int, w: int = 480, h: int = 270) -> bytes:
    bg = _seed_color(prompt + str(idx))
    fg = (255, 255, 255)
    pixels = [[bg for _ in range(w)] for _ in range(h)]
    label = f"AI {idx + 1}"
    _draw_text(pixels, label, 20, 20, fg, scale=4)
    return _png_bytes(pixels)


def generate_image(prompt: str, *, settings: Settings, idx: int = 0,
                   model: str = "mock:placeholder") -> ImageResult:
    """Generate an image. Saves the result locally and returns a /media/<file> URL.

    Provider routing happens by ``model`` prefix or by ``settings.image_provider``:

    - If model starts with ``mock:`` OR ``settings.image_provider == "mock"``:
      generate a placeholder PNG without network calls.
    - If model starts with ``302ai:`` OR ``settings.image_provider == "302ai"``:
      call 302.ai gateway.
    - Otherwise: call OpenAI native images API.
    """
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1((prompt + str(idx) + model).encode("utf-8")).hexdigest()[:16]
    filename = f"{h}.png"
    path = os.path.join(settings.media_dir, filename)
    if os.path.exists(path):
        return ImageResult(storage_url=f"/media/{filename}", mime="image/png",
                           width=0, height=0, model=model, prompt=prompt)

    # Decide which provider to call.
    use_mock = (
        settings.use_mock_images
        or model.startswith("mock:")
        or model in ("", "mock")
    )
    if use_mock:
        data = _placeholder_png(prompt, model, idx)
        width, height = 480, 270
    else:
        try:
            data, width, height = _call_real_provider(prompt, model, settings)
        except Exception as exc:
            log.exception("image provider failed, falling back to placeholder")
            data = _placeholder_png(prompt, model, idx)
            width, height = 480, 270

    with open(path, "wb") as fh:
        fh.write(data)
    return ImageResult(
        storage_url=f"/media/{filename}",
        mime="image/png",
        width=width, height=height,
        model=model,
        prompt=prompt,
    )


def _call_real_provider(prompt: str, model: str, settings: Settings
                        ) -> tuple[bytes, int, int]:
    """Calls 302.ai or OpenAI images API depending on the model string."""
    # Route
    if ":" in model:
        prefix, real = model.split(":", 1)
    else:
        prefix, real = settings.image_provider, model
    prefix = prefix.lower()

    if prefix == "302ai":
        api_key = os.environ.get("AI302_API_KEY", "")
        base_url = "https://api.302.ai/v1"
        if not api_key:
            raise RuntimeError("AI302_API_KEY is empty in environment.")
    else:  # openai
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = "https://api.openai.com/v1"
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is empty in environment.")

    # Build OpenAI-compatible /images/generations request.
    body = {
        "model": real,
        "prompt": prompt,
        "n": 1,
        "size": "1280x720",
        "response_format": "b64_json",
    }
    url = base_url.rstrip("/") + "/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        log.error("image HTTP %s: %s", exc.code, err_body[:500])
        raise RuntimeError(
            f"image provider returned HTTP {exc.code}: {err_body[:300]}"
        ) from exc

    item = (payload.get("data") or [{}])[0]
    # 302.ai may return either b64_json or an URL; handle both.
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as r:
            data = r.read()
    else:
        raise RuntimeError(f"image response missing data: {payload}")
    # Best-effort PNG dimensions extraction.
    w, h = _try_png_size(data)
    return data, w or 1280, h or 720


def _try_png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    try:
        w = struct.unpack(">I", data[16:20])[0]
        h = struct.unpack(">I", data[20:24])[0]
        return w, h
    except struct.error:
        return None, None
