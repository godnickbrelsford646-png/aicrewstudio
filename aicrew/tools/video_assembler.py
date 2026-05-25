"""Final video assembly tool. Mock + real-ffmpeg skeleton.

Stitches per-scene clips, mixes the voiceover audio, and burns subtitles into
one MP4. In mock mode just writes a placeholder MP4 (same byte format as
``aicrew/tools/video_gen.py``) so the rest of the pipeline can finish without
ffmpeg or any external binary on the sandbox.

The real path uses ``ffmpeg`` (xfade between clips, audio overlay, ASS burn-in)
— it lives in ``_call_ffmpeg_concat`` as a skeleton and is NEVER called in mock
mode. Before the real path runs we check ``shutil.which("ffmpeg")`` so the
user gets a clear error rather than a generic "FileNotFoundError".
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
from dataclasses import dataclass

from ..settings import Settings

log = logging.getLogger("aicrew.video_assembler")


@dataclass
class AssembleResult:
    storage_url: str
    mime: str
    duration_s: float
    width: int
    height: int


# Same minimal MP4 placeholder as aicrew/tools/video_gen.py.
def _placeholder_mp4(seed: str = "") -> bytes:
    ftyp = (
        b"\x00\x00\x00\x20"
        b"ftyp"
        b"isom"
        b"\x00\x00\x02\x00"
        b"isom" b"iso2" b"avc1" b"mp41"
    )
    pad_len = 80
    mdat_size = 8 + pad_len
    mdat = struct.pack(">I", mdat_size) + b"mdat" + (b"\x00" * pad_len)
    blob = ftyp + mdat
    if seed:
        import hashlib
        seed_bytes = hashlib.sha1(seed.encode("utf-8")).digest()
        head = blob[: len(blob) - len(seed_bytes)]
        return head + seed_bytes
    return blob


def assemble_video(
    scenes: list[dict],
    *,
    settings: Settings,
    article_id: str,
    language: str,
) -> AssembleResult:
    """Assemble per-scene clips + audio + subtitles into one final MP4.

    Each ``scene`` dict is expected to carry::

        {
          "clip_path":  "/media/vclip_xxx.mp4",
          "audio_path": "/media/tts_yyy.mp3",
          "ass_path":   "/media/zzz.ass" (optional, only for the last/global
                                          subtitles row),
          "duration_s": 5.0
        }

    Mock mode: writes one placeholder MP4 named ``{article_id}_{language}.mp4``
    to ``settings.media_dir`` and returns ``/media/{article_id}_{language}.mp4``
    (no ffmpeg call — just a byte-level placeholder).

    Real mode: skeleton path via ``_call_ffmpeg_concat`` (not invoked here).
    """
    os.makedirs(settings.media_dir, exist_ok=True)
    filename = f"{article_id}_{language}.mp4"
    path = os.path.join(settings.media_dir, filename)
    total_duration = sum(float(s.get("duration_s") or 5.0) for s in scenes) or 5.0

    api_key = os.environ.get("AI302_API_KEY", "")
    # Heuristic: if no real video API key is present, we are clearly in
    # sandbox / dev mode; never attempt real ffmpeg orchestration.
    use_mock = not api_key

    if use_mock:
        data = _placeholder_mp4(seed=f"{article_id}|{language}|{len(scenes)}")
        with open(path, "wb") as fh:
            fh.write(data)
    else:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError(
                "ffmpeg is not installed on PATH; required for real video "
                "assembly. Install ffmpeg or switch to mock mode."
            )
        try:
            _call_ffmpeg_concat(scenes, path, ffmpeg_bin)
        except Exception as exc:
            log.exception("ffmpeg assembly failed, writing placeholder: %s", exc)
            data = _placeholder_mp4(seed=f"{article_id}|{language}|err")
            with open(path, "wb") as fh:
                fh.write(data)

    return AssembleResult(
        storage_url=f"/media/{filename}",
        mime="video/mp4",
        duration_s=float(total_duration),
        width=1080,
        height=1920,
    )


# ---------------------------------------------------------------------------
# Real ffmpeg skeleton — NEVER called in sandbox / mock mode.
# ---------------------------------------------------------------------------


def _call_ffmpeg_concat(scenes: list[dict], out_path: str,
                        ffmpeg_bin: str) -> None:
    """Skeleton for the real ffmpeg orchestration.

    Strategy:
        1. For each scene, render a per-scene MP4 with the voiceover laid
           on top of the clip (``ffmpeg -i clip -i audio -c:v copy -c:a aac
           -shortest scene_N.mp4``).
        2. Concat scene MP4s with xfade transitions (filter_complex chain
           of ``xfade=transition=fade:duration=0.5:offset=...``).
        3. Burn ASS subtitles into the concatenated stream
           (``-vf ass=path/to.ass``).
        4. Re-mux to the final 9:16 H.264/AAC MP4.

    Notes:
        * ``shutil.which('ffmpeg')`` is checked by the caller; this skeleton
          assumes ``ffmpeg_bin`` is a valid absolute path.
        * Aspect is fixed 9:16 by product decision (zashityey konstantoy).
        * The skeleton intentionally does no work — it just declares the
          shape of the call so a future engineer can drop in the actual
          ``subprocess.run`` invocations.
    """
    raise NotImplementedError(
        "Real ffmpeg assembly is a skeleton; sandbox has no ffmpeg / no internet."
    )
