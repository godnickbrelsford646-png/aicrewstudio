"""Image generation tool. Mock writes a real PNG with the prompt printed on it.

In production this routes to Flux/DALL-E/SDXL via Replicate/Fal/OpenAI.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass

from ..settings import Settings


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
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1((prompt + str(idx)).encode("utf-8")).hexdigest()[:16]
    filename = f"{h}.png"
    path = os.path.join(settings.media_dir, filename)
    if not os.path.exists(path):
        if settings.use_mock_images:
            data = _placeholder_png(prompt, model, idx)
        else:
            raise NotImplementedError("real image providers not wired in this build")
        with open(path, "wb") as fh:
            fh.write(data)
    storage_url = f"/media/{filename}"
    return ImageResult(
        storage_url=storage_url,
        mime="image/png",
        width=480,
        height=270,
        model=model,
        prompt=prompt,
    )
