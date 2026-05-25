"""Image-to-video clip generation tool. Mock + real-API skeleton.

Mock mode writes a tiny bytes-only MP4 placeholder under ``settings.media_dir``
and returns a ``/media/<file>.mp4`` URL. The placeholder is just enough for a
browser to recognise the file as ``video/mp4`` (the file is technically not
playable, but its presence is the signal we need for skeleton UI).

Real path: ``302ai:wan2.2-i2v`` via DashScope async API. The skeleton lives in
``_call_302ai_wan22_i2v`` and is NEVER invoked when ``use_mock`` is true. The
sandbox has no internet so the real path is skeleton-only by design.

Routing rule (mirrors aicrew/tools/image_gen.py):
    * If ``model`` starts with ``mock:`` or is empty / "mock" -> mock branch.
    * Otherwise the real branch is attempted; if AI302_API_KEY is missing
      we fall back to mock so the pipeline never crashes in dev.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.video_gen")


@dataclass
class VideoClipResult:
    storage_url: str
    mime: str
    width: int
    height: int
    duration_s: float
    model: str
    prompt: str


# A minimal-but-valid MP4 byte blob. Two ISO-BMFF boxes:
#   1. ``ftyp`` — declares brand 'isom' so file sniffers see it as MP4.
#   2. ``mdat`` — empty media data box, padded so the whole blob is >100 bytes.
# A real player will refuse to play this (no moov), but every browser/OS
# correctly identifies the file as video/mp4 — that's all the skeleton UI
# needs to show that the pipeline ran.
def _placeholder_mp4(seed: str = "") -> bytes:
    ftyp = (
        b"\x00\x00\x00\x20"   # box size = 32 bytes
        b"ftyp"
        b"isom"               # major brand
        b"\x00\x00\x02\x00"   # minor version
        b"isom" b"iso2" b"avc1" b"mp41"
    )
    # Pad mdat to ~80 bytes of body so total >100 bytes.
    pad_len = 80
    mdat_size = 8 + pad_len
    mdat = struct.pack(">I", mdat_size) + b"mdat" + (b"\x00" * pad_len)
    blob = ftyp + mdat
    # Mix the seed into the padding so two clips for different scenes are
    # not byte-identical (helps with debugging / file deduplication).
    if seed:
        seed_bytes = hashlib.sha1(seed.encode("utf-8")).digest()
        head = blob[: len(blob) - len(seed_bytes)]
        return head + seed_bytes
    return blob


def generate_video_clip(
    image_url: str,
    prompt: str,
    *,
    settings: Settings,
    idx: int = 0,
    model: str = "mock:placeholder",
    duration_s: float = 5.0,
) -> VideoClipResult:
    """Generates a 5-sec video clip from a still image (image-to-video).

    Mock mode: writes a tiny valid MP4 placeholder file (just the ftyp box
    plus a padded mdat box — enough to make the file recognisable as
    video/mp4 but not playable; that's OK for skeleton).

    Real path: 302ai:wan2.2-i2v via DashScope async API (skeleton only —
    never called in sandbox).
    """
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1(
        (image_url + "|" + prompt + "|" + str(idx) + "|" + model).encode("utf-8")
    ).hexdigest()[:16]
    filename = f"vclip_{h}.mp4"
    path = os.path.join(settings.media_dir, filename)
    # Decide which provider to call.
    api_key = os.environ.get("AI302_API_KEY", "")
    use_mock = (
        model.startswith("mock:")
        or model in ("", "mock")
        or not api_key
    )
    if os.path.exists(path):
        return VideoClipResult(
            storage_url=f"/media/{filename}", mime="video/mp4",
            width=1080, height=1920, duration_s=duration_s,
            model=model, prompt=prompt,
        )
    if use_mock:
        data = _placeholder_mp4(seed=f"{prompt}|{idx}|{model}")
    else:
        try:
            data = _call_302ai_wan22_i2v(image_url, prompt, duration_s, api_key)
        except Exception as exc:
            log.exception("video provider failed, falling back to placeholder: %s", exc)
            data = _placeholder_mp4(seed=f"{prompt}|{idx}|{model}|err")
    with open(path, "wb") as fh:
        fh.write(data)
    return VideoClipResult(
        storage_url=f"/media/{filename}",
        mime="video/mp4",
        width=1080, height=1920,
        duration_s=duration_s,
        model=model,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Real-API skeleton — NEVER called in sandbox / mock mode. Kept here as a
# documented stub so a future deployment with AI302_API_KEY can wire it in.
# ---------------------------------------------------------------------------


def _call_302ai_wan22_i2v(image_url: str, prompt: str, duration_s: float,
                          api_key: str) -> bytes:
    """Skeleton for 302.ai Wan 2.2 image-to-video async call.

    Wire format (DashScope-style, mirrors the image-gen async path):
        # 1. Submit:
        POST https://api.302.ai/aliyun/api/v1/services/aigc/video-generation/video-synthesis
        Authorization: Bearer <AI302_API_KEY>
        Content-Type: application/json
        {
            "model": "wan2.2-i2v",
            "input": {
                "prompt": <prompt>,
                "img_url": <image_url>
            },
            "parameters": {
                "duration": <int seconds>,
                "size": "1080*1920"
            }
        }
        -> 200 {"output": {"task_id": "...", "task_status": "PENDING"}, ...}

        # 2. Poll until SUCCEEDED:
        GET https://api.302.ai/aliyun/api/v1/tasks/<task_id>
        -> 200 {"output": {"task_status": "SUCCEEDED",
                            "results": [{"video_url": "https://..."}]}}

        # 3. Download the URL bytes and return them.

    Cost: ~$0.12 per 5s clip at the time of writing.
    """
    raise NotImplementedError(
        "Real 302.ai wan2.2-i2v call is a skeleton; sandbox has no internet."
    )
