"""Image-to-video clip generation tool. Real 302.ai Wan 2.2-i2v + mock fallback.

Routing by model string (mirrors aicrew/tools/image_gen.py):
  - ``mock:placeholder`` (or empty / "mock") -> placeholder MP4 (no API call).
  - ``302ai:wan2.2-i2v`` (or any ``302ai:wan*-i2v``) -> ASYNC DashScope path:
        POST /aliyun/api/v1/services/aigc/video-generation/video-synthesis
        Body uses input.{prompt, img_url}, parameters.{duration, size}.
        Returns task_id; poll /aliyun/api/v1/tasks/{task_id} until SUCCEEDED.
        Result MP4 is downloaded from output.results[0].video_url (or one of
        several alternative shape variants).

The output is always saved as a local MP4 under ``settings.media_dir`` and
served by the API on ``/media/<file>.mp4``.

Public base URL
---------------
302.ai needs a publicly reachable URL for the source keyframe — it will fetch
the image from our server before running i2v. We compose this URL by joining
``$AICREW_PUBLIC_BASE_URL`` with the relative ``/media/<file>.png`` path that
``image_gen.py`` returns. If ``AICREW_PUBLIC_BASE_URL`` is empty the call will
log a warning and pass the relative path as-is (302.ai will then fail with a
clearer "image not reachable" error than if we silently corrupted things).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.video_gen")

# Async poll settings. Wan 2.2-i2v takes noticeably longer than image gen —
# typically 30-90s per 5s clip, occasionally up to 3 minutes under load.
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 600  # 10 minutes is enough for the 99th percentile

AI302_BASE = "https://api.302.ai"
WAN_I2V_SUBMIT_PATH = "/aliyun/api/v1/services/aigc/video-generation/video-synthesis"

# Same poll endpoints as image_gen.py — DashScope tasks share one routing
# table on 302.ai's side.
WAN_POLL_PATHS = (
    "/aliyun/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}",  # fallback for some 302.ai variants
)


@dataclass
class VideoClipResult:
    storage_url: str
    mime: str
    width: int
    height: int
    duration_s: float
    model: str
    prompt: str
    cost_usd: float = 0.0


# Minimal-but-valid MP4 byte blob used in mock mode AND as the fallback when
# the real 302.ai call fails. Browser/OS magic-byte sniffers will recognise
# the ftyp/mdat boxes; the file is technically not playable (no moov atom)
# but the mime type is correct, which is all the skeleton needs.
def _placeholder_mp4(seed: str = "") -> bytes:
    ftyp = (
        b"\x00\x00\x00\x20"   # box size = 32 bytes
        b"ftyp"
        b"isom"               # major brand
        b"\x00\x00\x02\x00"   # minor version
        b"isom" b"iso2" b"avc1" b"mp41"
    )
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


def _abs_image_url(image_url: str) -> str:
    """Convert ``/media/x.png`` to an absolute URL using
    ``$AICREW_PUBLIC_BASE_URL``, so 302.ai can fetch the keyframe.

    Pass-through for already-absolute URLs (http/https).
    """
    if image_url.startswith(("http://", "https://")):
        return image_url
    base = (os.environ.get("AICREW_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        log.warning(
            "AICREW_PUBLIC_BASE_URL is empty — Wan i2v needs a publicly "
            "reachable image URL. Set AICREW_PUBLIC_BASE_URL=https://<domain> "
            "in .env or video generation will fail with 'image not reachable'."
        )
        return image_url
    return base + "/" + image_url.lstrip("/")


def generate_video_clip(
    image_url: str,
    prompt: str,
    *,
    settings: Settings,
    idx: int = 0,
    model: str = "mock:placeholder",
    duration_s: float = 5.0,
) -> VideoClipResult:
    """Generates a short video clip from a still image (image-to-video).

    Mock mode: writes a tiny valid MP4 placeholder file (just the ftyp box
    plus a padded mdat box — enough for the file to be recognisable as
    video/mp4 but not playable).

    Real path: 302.ai Wan 2.2-i2v via the DashScope async API. On any
    failure we log + write an ``.error.txt`` sidecar and fall back to the
    placeholder, so the video pipeline never crashes mid-flight.
    """
    os.makedirs(settings.media_dir, exist_ok=True)
    h = hashlib.sha1(
        (image_url + "|" + prompt + "|" + str(idx) + "|" + model).encode("utf-8")
    ).hexdigest()[:16]
    filename = f"vclip_{h}.mp4"
    path = os.path.join(settings.media_dir, filename)
    api_key = os.environ.get("AI302_API_KEY", "")
    use_mock = (
        model.startswith("mock:")
        or model in ("", "mock")
        or not api_key
    )
    # Cost is known up-front (Wan 2.2-i2v bills linearly by duration).
    # Computed once; reused on cache hits and zeroed on real-call
    # failures so the dashboard never over-reports on transient errors.
    if use_mock:
        cost_usd = 0.0
    else:
        from ..llm.pricing import estimate_video_cost
        cost_usd = estimate_video_cost(model, duration_s=duration_s)
    # Cache hit: same prompt+image+idx+model already produced a clip.
    if os.path.exists(path):
        return VideoClipResult(
            storage_url=f"/media/{filename}", mime="video/mp4",
            width=1080, height=1920, duration_s=duration_s,
            model=model, prompt=prompt, cost_usd=cost_usd,
        )
    if use_mock:
        data = _placeholder_mp4(seed=f"{prompt}|{idx}|{model}")
    else:
        try:
            full_image_url = _abs_image_url(image_url)
            data = _call_302ai_wan_i2v(
                full_image_url, prompt, model, duration_s, api_key,
            )
        except Exception as exc:
            log.exception("video provider failed, falling back to placeholder")
            log.warning(
                "video generation failed: %s. Common causes: out of credits "
                "on 302.ai, image URL not reachable from 302.ai (check "
                "AICREW_PUBLIC_BASE_URL), or wrong model name. See sidecar "
                "vclip_%s.error.txt for details.", exc, h,
            )
            data = _placeholder_mp4(seed=f"{prompt}|{idx}|{model}|err")
            # Real call never reached the provider successfully —
            # placeholder bytes cost nothing.
            cost_usd = 0.0
            try:
                err_path = os.path.join(settings.media_dir, f"vclip_{h}.error.txt")
                with open(err_path, "w", encoding="utf-8") as efh:
                    efh.write(
                        f"video generation failed\n"
                        f"model: {model}\n"
                        f"image_url: {image_url}\n"
                        f"prompt: {prompt[:500]}\n"
                        f"duration_s: {duration_s}\n"
                        f"error: {exc}\n"
                        f"hint: check 302.ai dashboard for credits and "
                        f"verify AICREW_PUBLIC_BASE_URL in .env points to "
                        f"a host where /media/ is publicly served.\n"
                    )
            except Exception:  # pragma: no cover - diagnostic best-effort
                log.exception("failed to write video error sidecar file")
    with open(path, "wb") as fh:
        fh.write(data)
    return VideoClipResult(
        storage_url=f"/media/{filename}",
        mime="video/mp4",
        width=1080, height=1920,
        duration_s=duration_s,
        model=model,
        prompt=prompt,
        cost_usd=cost_usd,
    )


# ---------------------------------------------------------------------------
# Real 302.ai Wan i2v call.
# ---------------------------------------------------------------------------


def _call_302ai_wan_i2v(image_url: str, prompt: str, model: str,
                        duration_s: float, api_key: str) -> bytes:
    """Real 302.ai Wan 2.2-i2v image-to-video async call.

    Wire format::

        # 1. Submit:
        POST https://api.302.ai/aliyun/api/v1/services/aigc/video-generation/video-synthesis
        Authorization: Bearer <AI302_API_KEY>
        Content-Type: application/json
        {
            "model": "wan2.2-i2v",
            "input": {
                "prompt":  <motion description>,
                "img_url": <absolute https URL of source still>
            },
            "parameters": {
                "duration": <seconds, integer>,
                "size":     "1080*1920"   # 9:16 vertical
            }
        }
        -> 200 {"output": {"task_id": "...", "task_status": "PENDING"}, ...}

        # 2. Poll until SUCCEEDED:
        GET https://api.302.ai/aliyun/api/v1/tasks/<task_id>
        -> 200 {"output": {"task_status": "SUCCEEDED",
                            "results": [{"video_url": "https://..."}]}}

        # 3. Download the video URL bytes.

    Cost: ≈$0.12 per 5s clip at the time of writing.
    """
    # The model string we get can be either the full namespaced form
    # ``302ai:wan2.2-i2v`` (when called from the pipeline) or the bare
    # ``wan2.2-i2v`` (when called directly). 302.ai expects the bare form
    # in the body.
    real_model = model.split(":", 1)[1] if ":" in model else model
    submit_url = AI302_BASE + WAN_I2V_SUBMIT_PATH
    body = {
        "model": real_model,
        "input": {
            # Wan 2.2-i2v hard-caps the prompt at ~2000 chars; trim
            # defensively so we get a real error code from 302.ai
            # rather than a 400 about prompt length.
            "prompt": prompt[:2000],
            "img_url": image_url,
        },
        "parameters": {
            # Wan i2v accepts integer seconds; round to be safe.
            "duration": int(round(duration_s)) or 5,
            # 9:16 vertical, locked by product decision.
            "size": "1080*1920",
        },
    }
    task_id = _wan_submit(submit_url, body, api_key, real_model, prompt)
    return _wan_poll_and_download(task_id, api_key, real_model)


def _wan_submit(url: str, body: dict, api_key: str, real_model: str,
                prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    log.info("video(wan): submit url=%s model=%s prompt=%r",
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
        log.error("video(wan): submit HTTP %s body=%s", exc.code, err[:500])
        raise RuntimeError(
            f"Wan i2v submit HTTP {exc.code} on {url}: {err[:300]}"
        ) from exc
    out = payload.get("output") or {}
    task_id = (
        out.get("task_id")
        or payload.get("task_id")
        or (payload.get("data") or {}).get("task_id")
    )
    if not task_id:
        raise RuntimeError(
            f"Wan i2v submit returned 200 but no task_id. "
            f"payload={str(payload)[:300]}"
        )
    log.info("video(wan): submit ok task_id=%s status=%s",
             task_id, out.get("task_status") or "?")
    return task_id


def _wan_poll_and_download(task_id: str, api_key: str, real_model: str) -> bytes:
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
        log.info("video(wan): poll task_id=%s status=%s", task_id, last_status)
        if last_status == "SUCCEEDED":
            break
        if last_status in ("FAILED", "UNKNOWN_ERROR"):
            code = out.get("code") or ""
            msg = out.get("message") or out.get("error") or ""
            raise RuntimeError(
                f"Wan i2v task FAILED on 302.ai: code={code} message={msg} "
                f"task_id={task_id}"
            )
    if last_status != "SUCCEEDED":
        raise RuntimeError(
            f"Wan i2v task did not finish in {POLL_TIMEOUT_SEC}s "
            f"(last_status={last_status}, task_id={task_id}). "
            f"poll_errors={'; '.join(poll_errors[-3:]) if poll_errors else 'none'}"
        )

    out = (poll_payload or {}).get("output") or {}
    video_url = _extract_wan_video_url(out, poll_payload or {})
    if not video_url:
        raise RuntimeError(
            f"Wan i2v task SUCCEEDED but no video URL in result: "
            f"task_id={task_id} payload={str(poll_payload)[:300]}"
        )
    log.info("video(wan): downloading url=%s task_id=%s", video_url, task_id)
    # Video downloads can be 5-30 MB; allow plenty of time on slow links.
    with urllib.request.urlopen(video_url, timeout=600) as r:
        data = r.read()
    log.info("video(wan): ok bytes=%d task_id=%s model=%s",
             len(data), task_id, real_model)
    return data


def _extract_wan_video_url(out: dict, payload: dict) -> str | None:
    """Look for the video URL in known result-shape variants.

    302.ai is not 100% consistent with the field name across model
    versions; we try several known shapes before giving up.
    """
    # Variant 1: output.results = [{"video_url": ...}]   (most common)
    results = out.get("results")
    if isinstance(results, list) and results:
        first = results[0] or {}
        if isinstance(first, dict):
            url = (first.get("video_url") or first.get("url")
                   or first.get("output_url") or first.get("video"))
            if url:
                return url
    # Variant 2: output.video_url at top level.
    url = out.get("video_url") or out.get("url") or out.get("video")
    if url:
        return url
    # Variant 3: output.choices[0].message.content[*].video / .video_url
    #            (DashScope multi-modal style — rare for i2v, but seen).
    choices = out.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        msg = (first or {}).get("message") or {}
        content = msg.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    url = (block.get("video") or block.get("video_url")
                           or block.get("url"))
                    if url:
                        return url
    # Variant 4: payload.data[0].url at the top level (some 302.ai variants
    # normalise output away from the DashScope envelope).
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0] or {}
        if isinstance(first, dict):
            url = (first.get("video_url") or first.get("url")
                   or first.get("output_url"))
            if url:
                return url
    return None
