"""Image generation tool. Real 302.ai support + mock placeholder fallback.

Routing by model string:
  - ``mock:placeholder``  -> generate a coloured PNG locally (no API call).
  - ``302ai:wan2.7-image`` / ``302ai:wan2.6-image`` (Wan 2.6+ family) -> ASYNC path:
        POST https://api.302.ai/aliyun/api/v1/services/aigc/image-generation/generation
        Body uses input.messages[].content[].text (Tongyi Wanxiang format),
        parameters.enable_interleave=true for pure text-to-image,
        returns task_id; poll GET /aliyun/api/v1/tasks/{task_id} until SUCCEEDED.
  - ``302ai:wanx*`` / older Wan 2.1 -> ASYNC legacy path:
        POST https://api.302.ai/aliyun/api/v1/services/aigc/text2image/image-synthesis
        Body uses input.prompt; same poll flow.
  - ``302ai:flux-*`` etc. -> SYNC path:
        POST https://api.302.ai/v1/images/generations (OpenAI-compatible).
  - ``openai:dall-e-3`` / ``openai:gpt-image-1`` -> OpenAI native /v1/images/generations.

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
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.image")

# Async poll settings for 302.ai Wan path.
POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 180

# 302.ai gateway base.
AI302_BASE = "https://api.302.ai"

# Submit endpoints. We pick by model name:
#   wan2.6+ / wan2.7 -> /aliyun/api/v1/services/aigc/image-generation/generation
#   older wanx-*    -> /aliyun/api/v1/services/aigc/text2image/image-synthesis
WAN_NEW_SUBMIT_PATH = "/aliyun/api/v1/services/aigc/image-generation/generation"
WAN_LEGACY_SUBMIT_PATH = "/aliyun/api/v1/services/aigc/text2image/image-synthesis"

# Poll endpoint(s). The first one is the documented form on 302.ai.
WAN_POLL_PATHS = (
    "/aliyun/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}",  # fallback, some 302.ai variants
)


@dataclass
class ImageResult:
    storage_url: str
    mime: str
    width: int
    height: int
    model: str
    prompt: str
    cost_usd: float = 0.0


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
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
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


def _placeholder_png(prompt: str, model: str, idx: int, w: int = 480, h: int = 270,
                     *, error: bool = False) -> bytes:
    if error:
        # Distinct reddish background so the user can SEE that this placeholder
        # is the result of a real-API failure (vs the normal mock path).
        bg = (180, 40, 40)
        label = f"ERR {idx + 1}"
    else:
        bg = _seed_color(prompt + str(idx))
        label = f"AI {idx + 1}"
    fg = (255, 255, 255)
    pixels = [[bg for _ in range(w)] for _ in range(h)]
    _draw_text(pixels, label, 20, 20, fg, scale=4)
    return _png_bytes(pixels)


def generate_image(prompt: str, *, settings: Settings, idx: int = 0,
                   model: str = "mock:placeholder") -> ImageResult:
    """Generate an image. Saves the result locally and returns a /media/<file> URL."""
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1((prompt + str(idx) + model).encode("utf-8")).hexdigest()[:16]
    filename = f"{h}.png"
    path = os.path.join(settings.media_dir, filename)
    # Decide which provider to call.
    use_mock = (
        settings.use_mock_images
        or model.startswith("mock:")
        or model in ("", "mock")
    )
    # Cost is known up-front for image generation (one image per call,
    # static per-model rate). We attribute the same cost on cache hits
    # so a re-run that resolves to a cached file does NOT add a phantom
    # second charge — the dashboard sums media_assets rows, and only
    # the original INSERT writes cost_usd into media_assets.
    if use_mock:
        cost_usd = 0.0
    else:
        from ..llm.pricing import estimate_image_cost
        cost_usd = estimate_image_cost(model, count=1)
    if os.path.exists(path):
        return ImageResult(storage_url=f"/media/{filename}", mime="image/png",
                           width=0, height=0, model=model, prompt=prompt,
                           cost_usd=cost_usd)

    if use_mock:
        data = _placeholder_png(prompt, model, idx)
        width, height = 480, 270
    else:
        try:
            data, width, height = _call_real_provider(prompt, model, settings)
        except Exception as exc:
            log.exception("image provider failed, falling back to placeholder")
            log.warning(
                "image generation failed: %s. "
                "Common causes: out of credits on 302.ai/OpenAI dashboard, "
                "wrong model name, or a network/region block. See sidecar "
                "%s.error.txt for details.", exc, h,
            )
            data = _placeholder_png(prompt, model, idx, error=True)
            width, height = 480, 270
            # Real call never reached the provider successfully — we
            # ate a placeholder, not a billed image. Drop the cost so
            # the dashboard does not over-report on transient failures.
            cost_usd = 0.0
            try:
                err_path = os.path.join(settings.media_dir, f"{h}.error.txt")
                with open(err_path, "w", encoding="utf-8") as efh:
                    efh.write(
                        f"image generation failed\n"
                        f"model: {model}\n"
                        f"prompt: {prompt[:500]}\n"
                        f"error: {exc}\n"
                        f"hint: check 302.ai dashboard for credits and "
                        f"model availability (e.g. wan2.7-image).\n"
                    )
            except Exception:  # pragma: no cover - diagnostic best-effort
                log.exception("failed to write image error sidecar file")

    with open(path, "wb") as fh:
        fh.write(data)
    return ImageResult(
        storage_url=f"/media/{filename}",
        mime="image/png",
        width=width, height=height,
        model=model,
        prompt=prompt,
        cost_usd=cost_usd,
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
    real_lower = real.lower()

    if prefix == "302ai":
        api_key = os.environ.get("AI302_API_KEY", "")
        if not api_key:
            raise RuntimeError("AI302_API_KEY is empty in environment.")
        # Wan 2.6 / 2.7 (modern Tongyi Wanxiang) -> async messages API.
        if real_lower.startswith("wan2.") or real_lower.startswith("wan-2.") \
                or real_lower in ("wan2.6-image", "wan2.7-image"):
            return _call_302ai_async_wan_messages(prompt, real, api_key)
        # Older wanx-* (Wan 2.1) -> async legacy text2image API.
        if real_lower.startswith("wanx") or "wanx" in real_lower:
            return _call_302ai_async_wan_legacy(prompt, real, api_key)
        # Sync OpenAI-compat models on 302.ai (flux, dall-e proxy, gpt-image-1 proxy).
        return _call_openai_compat_sync(
            prompt, real, api_key, base_url="https://api.302.ai/v1",
        )
    # OpenAI native
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is empty in environment.")
    return _call_openai_compat_sync(
        prompt, real, api_key, base_url="https://api.openai.com/v1",
    )


def _call_openai_compat_sync(prompt: str, real_model: str, api_key: str,
                              *, base_url: str) -> tuple[bytes, int, int]:
    """Synchronous OpenAI-compatible /v1/images/generations call."""
    body = {
        "model": real_model,
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
    log.info("image: sync call model=%s endpoint=%s prompt=%r",
             real_model, url, prompt[:100])
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
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
        log.info("image ok: model=%s mode=b64_json bytes=%d", real_model, len(data))
    elif item.get("url"):
        log.info("image ok: model=%s mode=url url=%s", real_model, item["url"])
        with urllib.request.urlopen(item["url"], timeout=120) as r:
            data = r.read()
    else:
        raise RuntimeError(f"image response missing data: {payload}")
    w, h = _try_png_size(data)
    return data, w or 1280, h or 720


# ----------------------------------------------------------------------------
# Wan 2.6 / 2.7 async path (Tongyi Wanxiang, "image-generation/generation")
# ----------------------------------------------------------------------------

# Allowed sizes for wan2.x-image (per 302.ai docs). 16:9 -> 1280*720.
_WAN_ALLOWED_SIZES_16_9 = "1280*720"


def _call_302ai_async_wan_messages(prompt: str, real_model: str, api_key: str
                                    ) -> tuple[bytes, int, int]:
    """Async path for Wan 2.6/2.7 on 302.ai using the Tongyi Wanxiang
    "image-generation/generation" endpoint.

    Wire format (302.ai docs)::

        # 1. Submit:
        POST https://api.302.ai/aliyun/api/v1/services/aigc/image-generation/generation
        Authorization: Bearer <AI302_API_KEY>
        Content-Type: application/json
        {
          "model": "wan2.7-image",
          "input": {"messages": [
            {"role": "user", "content": [{"text": "<prompt>"}]}
          ]},
          "parameters": {
            "n": 1,
            "size": "1280*720",
            "enable_interleave": true,
            "max_images": 1,
            "watermark": false
          }
        }
        -> 200 {"output": {"task_id": "...", "task_status": "PENDING"},
                "request_id": "..."}

        # 2. Poll until SUCCEEDED:
        GET https://api.302.ai/aliyun/api/v1/tasks/<task_id>
        Authorization: Bearer <AI302_API_KEY>
        -> 200 {"output": {"task_status": "SUCCEEDED",
                            "results": [{"url": "https://..."}], ...}, ...}

        # 3. Download the URL.

    Note on enable_interleave: for pure text-to-image (no reference image)
    we need ``enable_interleave: true`` (the docs call this "text-image
    interleaving output"; image array length 0 is allowed; n is fixed to 1
    in this mode; max_images is 1..5). The default mode in the docs is
    ``enable_interleave: false`` which is image EDITING and REQUIRES at
    least one input image — we don't have one, so we explicitly set true.
    """
    submit_url = AI302_BASE + WAN_NEW_SUBMIT_PATH
    body = {
        "model": real_model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt[:2000]}],  # 2000-char hard cap
                }
            ]
        },
        "parameters": {
            "n": 1,
            "size": _WAN_ALLOWED_SIZES_16_9,
            "enable_interleave": True,
            "max_images": 1,
            "watermark": False,
        },
    }
    task_id = _wan_submit(submit_url, body, api_key, real_model, prompt)
    return _wan_poll_and_download(task_id, api_key, real_model)


def _call_302ai_async_wan_legacy(prompt: str, real_model: str, api_key: str
                                  ) -> tuple[bytes, int, int]:
    """Legacy DashScope text2image flow (Wan 2.1 / wanx-*).

    Body uses ``input.prompt`` (not messages) and goes to the older
    ``text2image/image-synthesis`` endpoint. Polling endpoint is the same.
    """
    submit_url = AI302_BASE + WAN_LEGACY_SUBMIT_PATH
    body = {
        "model": real_model,
        "input": {"prompt": prompt[:2000]},
        "parameters": {"size": "1280*720", "n": 1},
    }
    task_id = _wan_submit(submit_url, body, api_key, real_model, prompt,
                          extra_headers={"X-DashScope-Async": "enable"})
    return _wan_poll_and_download(task_id, api_key, real_model)


def _wan_submit(url: str, body: dict, api_key: str, real_model: str,
                prompt: str, *, extra_headers: dict | None = None) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    log.info("image(wan): submit url=%s model=%s prompt=%r",
             url, real_model, prompt[:100])
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        log.error("image(wan): submit HTTP %s body=%s", exc.code, err[:500])
        raise RuntimeError(
            f"Wan submit HTTP {exc.code} on {url}: {err[:300]}"
        ) from exc
    out = payload.get("output") or {}
    task_id = (
        out.get("task_id")
        or payload.get("task_id")
        or (payload.get("data") or {}).get("task_id")
    )
    if not task_id:
        raise RuntimeError(
            f"Wan submit returned 200 but no task_id. "
            f"payload={str(payload)[:300]}"
        )
    log.info("image(wan): submit ok task_id=%s status=%s",
             task_id, out.get("task_status") or "?")
    return task_id


def _wan_poll_and_download(task_id: str, api_key: str, real_model: str
                            ) -> tuple[bytes, int, int]:
    headers = {"Authorization": f"Bearer {api_key}"}
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    last_status = "UNKNOWN"
    poll_payload: dict | None = None
    poll_errors: list[str] = []
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        polled = False
        for path_tmpl in WAN_POLL_PATHS:
            poll_url = AI302_BASE + path_tmpl.format(task_id=task_id)
            req = urllib.request.Request(poll_url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    poll_payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                err = exc.read().decode("utf-8", errors="replace")
                poll_errors.append(f"{path_tmpl} -> HTTP {exc.code}: {err[:150]}")
                continue
            except Exception as exc:
                poll_errors.append(f"{path_tmpl} -> {exc}")
                continue
            polled = True
            break
        if not polled:
            continue
        out = (poll_payload or {}).get("output") or {}
        last_status = (out.get("task_status") or out.get("status") or "UNKNOWN").upper()
        log.info("image(wan): poll task_id=%s status=%s", task_id, last_status)
        if last_status == "SUCCEEDED":
            break
        if last_status in ("FAILED", "UNKNOWN_ERROR"):
            code = out.get("code") or ""
            msg = out.get("message") or out.get("error") or ""
            raise RuntimeError(
                f"Wan task FAILED on 302.ai: code={code} message={msg} "
                f"task_id={task_id}"
            )
    if last_status != "SUCCEEDED":
        raise RuntimeError(
            f"Wan task did not finish in {POLL_TIMEOUT_SEC}s "
            f"(last_status={last_status}, task_id={task_id}). "
            f"poll_errors={'; '.join(poll_errors[-3:]) if poll_errors else 'none'}"
        )

    out = (poll_payload or {}).get("output") or {}
    image_url = _extract_wan_url(out, poll_payload or {})
    if not image_url:
        raise RuntimeError(
            f"Wan task SUCCEEDED but no image URL in result: "
            f"task_id={task_id} payload={str(poll_payload)[:300]}"
        )
    log.info("image(wan): downloading result url=%s task_id=%s",
             image_url, task_id)
    with urllib.request.urlopen(image_url, timeout=120) as r:
        data = r.read()
    log.info("image(wan): ok bytes=%d task_id=%s model=%s",
             len(data), task_id, real_model)
    w, h = _try_png_size(data)
    return data, w or 1280, h or 720


def _extract_wan_url(out: dict, payload: dict) -> str | None:
    """Look for the image URL in several known result-shape variants."""
    # Variant 1: output.results = [{"url": ...}]  (Wan 2.1 / wanx)
    results = out.get("results")
    if isinstance(results, list) and results:
        first = results[0] or {}
        if isinstance(first, dict):
            url = first.get("url") or first.get("image_url") or first.get("output_url")
            if url:
                return url
    # Variant 2: output.choices = [{"message": {"content": [{"image": "url"}]}}]
    #            (Wan 2.6/2.7 messages format)
    choices = out.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        msg = (first or {}).get("message") or {}
        content = msg.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    url = block.get("image") or block.get("image_url") or block.get("url")
                    if url:
                        return url
    # Variant 3: output.url / output.image_url at the top level.
    url = out.get("url") or out.get("image_url")
    if url:
        return url
    # Variant 4: payload.data[0].url (some 302.ai variants normalise output).
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0] or {}
        if isinstance(first, dict):
            url = first.get("url") or first.get("image_url")
            if url:
                return url
    return None


def _try_png_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    try:
        w = struct.unpack(">I", data[16:20])[0]
        h = struct.unpack(">I", data[20:24])[0]
        return w, h
    except struct.error:
        return None, None
