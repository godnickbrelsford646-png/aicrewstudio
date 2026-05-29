"""Final video assembly tool. Real ffmpeg + mock fallback.

Stitches per-scene clips, mixes the per-scene voiceover audio, scales every
clip to a 1080×1920 (9:16) frame, concats the lot, burns ASS subtitles into
the result and re-encodes to a single H.264/AAC MP4. The ``ffmpeg`` binary
is invoked exactly once with one ``-filter_complex`` graph — that is the
fastest reliable shape on a single-CPU VPS.

Mock fallback: when ``ffmpeg`` is not on PATH (or the call fails) we write
the same placeholder MP4 used by ``aicrew/tools/video_gen.py`` so the
pipeline never crashes mid-flight; the user sees a ``<basename>.error.txt``
sidecar in ``settings.media_dir`` describing what went wrong.

Audio concat helper
-------------------
``concat_audio_files()`` is exposed for ``aicrew/pipeline.py`` to feed
Whisper a single combined MP3 instead of one per scene. It uses ffmpeg's
``concat`` demuxer (no re-encoding) when ffmpeg is available; otherwise it
returns the first input file as-is so the pipeline still produces a usable
SRT (the mock Whisper helper synthesises 4 stub segments anyway).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import struct
import subprocess
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


# Same minimal MP4 placeholder as aicrew/tools/video_gen.py — written when
# ffmpeg is missing OR the real call fails, so the pipeline always ends with
# a recognisable video/mp4 file under settings.media_dir.
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
          "clip_path":  "/abs/path/vclip_xxx.mp4",
          "audio_path": "/abs/path/tts_yyy.mp3",   (optional)
          "ass_path":   "/abs/path/zzz.ass",       (optional, the SAME path
                                                    on every scene — it's
                                                    a single global file
                                                    burned over the concat)
          "duration_s": 5.0
        }

    Always returns an :class:`AssembleResult` pointing to a freshly written
    file under ``settings.media_dir/{article_id}_{language}.mp4``. The file
    is either the real ffmpeg output or, on any failure, the byte-level
    placeholder. The caller never has to handle exceptions.
    """
    os.makedirs(settings.media_dir, exist_ok=True)
    filename = f"{article_id}_{language}.mp4"
    out_path = os.path.join(settings.media_dir, filename)
    total_duration = sum(float(s.get("duration_s") or 5.0) for s in scenes) or 5.0

    ffmpeg_bin = shutil.which("ffmpeg")
    use_mock = ffmpeg_bin is None or not scenes

    if use_mock:
        if ffmpeg_bin is None:
            log.warning(
                "ffmpeg not on PATH — writing placeholder %s. Install "
                "ffmpeg on the host (apt install ffmpeg) for real video "
                "assembly.", filename,
            )
        with open(out_path, "wb") as fh:
            fh.write(_placeholder_mp4(seed=f"{article_id}|{language}|{len(scenes)}"))
    else:
        try:
            _call_ffmpeg_concat(scenes, out_path, ffmpeg_bin)
        except Exception as exc:
            log.exception("ffmpeg assembly failed, writing placeholder")
            log.warning(
                "video assembly failed: %s. Common causes: per-scene clips "
                "or audio files are placeholders/corrupt (will fail to "
                "decode), missing ffmpeg codecs (libx264/aac), or read-only "
                "media_dir. See sidecar %s.error.txt for details.",
                exc, filename,
            )
            with open(out_path, "wb") as fh:
                fh.write(_placeholder_mp4(seed=f"{article_id}|{language}|err"))
            try:
                err_path = out_path + ".error.txt"
                with open(err_path, "w", encoding="utf-8") as efh:
                    efh.write(
                        f"video assembly failed\n"
                        f"article_id: {article_id}\n"
                        f"language: {language}\n"
                        f"scenes: {len(scenes)}\n"
                        f"ffmpeg: {ffmpeg_bin}\n"
                        f"error: {exc}\n"
                        f"hint: check that per-scene clip_path and "
                        f"audio_path point to real, decodable files.\n"
                    )
            except Exception:  # pragma: no cover - diagnostic best-effort
                log.exception("failed to write video assembler sidecar")

    return AssembleResult(
        storage_url=f"/media/{filename}",
        mime="video/mp4",
        duration_s=float(total_duration),
        width=1080,
        height=1920,
    )


# ---------------------------------------------------------------------------
# Audio concat helper — used by aicrew/pipeline.py before Whisper.
# ---------------------------------------------------------------------------


def concat_audio_files(audio_paths: list[str], *, settings: Settings,
                        out_basename: str) -> str:
    """Concat several MP3 files into one ``.mp3`` under ``settings.media_dir``.

    Pipeline calls this with the per-scene voiceover paths produced by
    :func:`aicrew.tools.tts_gen.generate_tts` so Whisper sees the whole
    voiceover as one stream. Without it Whisper would only transcribe the
    first scene and the resulting SRT would be useless for burn-in.

    Behaviour:
      - 0 inputs  -> returns "" (caller falls back to first scene path).
      - 1 input   -> returns that path unchanged (no re-encoding).
      - 2+ inputs and ffmpeg on PATH -> ``ffmpeg -f concat -safe 0
        -i list.txt -c copy <out>``. This is the lossless concat demuxer:
        no re-encode, ~instant.
      - 2+ inputs and ffmpeg missing -> returns the first input unchanged
        and logs a warning (best we can do without re-encoding).

    Returns the absolute local path of the (possibly newly written) file.
    """
    paths = [p for p in audio_paths if p and os.path.exists(p)]
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0]
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        log.warning(
            "concat_audio_files: ffmpeg missing, returning first input "
            "(%s) — Whisper will only see the first scene's voiceover.",
            paths[0],
        )
        return paths[0]
    os.makedirs(settings.media_dir, exist_ok=True)
    out_path = os.path.join(settings.media_dir, out_basename)
    list_path = out_path + ".concat.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as fh:
            for p in paths:
                # ffmpeg's concat demuxer resolves relative paths against
                # the LIST FILE location, not cwd. So writing relative
                # ``media/tts_x.mp3`` while the list itself lives in
                # ``media/`` produces ``media/media/tts_x.mp3`` -> ENOENT.
                # Always write absolute paths to dodge that whole class.
                # (See production bug: Impossible to open
                # 'media/media/tts_23e886.mp3', concat rc=254.)
                abs_p = os.path.abspath(p)
                # Escape single quotes per ffmpeg concat-demuxer rules.
                escaped = abs_p.replace("'", r"'\''")
                fh.write(f"file '{escaped}'\n")
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            out_path,
        ]
        log.info("concat_audio_files: %d files -> %s", len(paths), out_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            log.error("concat_audio_files: ffmpeg failed rc=%d stderr=%s",
                      result.returncode, (result.stderr or "")[:500])
            return paths[0]
        return out_path
    except Exception as exc:
        log.exception("concat_audio_files crashed: %s", exc)
        return paths[0]
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Real ffmpeg orchestration.
# ---------------------------------------------------------------------------


def _call_ffmpeg_concat(scenes: list[dict], out_path: str,
                        ffmpeg_bin: str) -> None:
    """Run ffmpeg with a single ``-filter_complex`` graph that:

      1. For every scene i:
         - normalises the clip to 1080×1920 (scale + centre-crop), trims
           to scene.duration_s, and resets timestamps;
         - prepares the audio: takes scene.audio_path if present (also
           trimmed to scene.duration_s); otherwise synthesises silence
           (``anullsrc``) of the same length so the concat shapes match;
      2. ``concat=n=N:v=1:a=1`` joins the per-scene normalised V/A pairs
         into a single A/V stream;
      3. (optional) burns ASS subtitles via the ``ass`` filter using the
         ``ass_path`` field from the scenes (any one is fine — we use the
         first non-empty value because the pipeline writes the same path
         on every scene);
      4. encodes V with libx264 (preset=medium, crf=23) and A with AAC
         128 kbps, with ``+faststart`` so the file plays from any byte
         offset over HTTP.

    Why one filter_complex graph, not two passes? Because the per-scene
    intermediate files would each need their own libx264 encode, doubling
    encode time on a single-CPU VPS. The single-shot graph touches each
    frame exactly once.

    No ``xfade`` transitions yet — straight cuts are reliable, predictable
    and a touch sharper for short-form content. xfade can be added later
    by replacing the ``concat`` filter with the documented xfade chain.
    """
    if not scenes:
        raise RuntimeError("no scenes to assemble")

    # Build -i inputs and per-scene filter graph.
    inputs: list[str] = []
    filter_parts: list[str] = []
    concat_pairs: list[str] = []

    has_audio_input = []  # whether scene i comes with a real audio file

    # Pre-scan: do we have an ASS file to burn at the end?
    ass_path = ""
    for sc in scenes:
        cand = (sc.get("ass_path") or "").strip()
        if cand and os.path.exists(cand):
            ass_path = os.path.abspath(cand)
            break

    for i, sc in enumerate(scenes):
        clip_path = sc.get("clip_path") or ""
        if not clip_path or not os.path.exists(clip_path):
            raise RuntimeError(
                f"scene {i}: clip_path is missing or not on disk: {clip_path!r}"
            )
        duration = float(sc.get("duration_s") or 5.0)

        # Always pass absolute paths to ffmpeg — relative ones get
        # resolved against an undocumented cwd which differs between
        # systemd and a manual shell. Same trap as concat_audio_files.
        inputs += ["-i", os.path.abspath(clip_path)]
        clip_input_idx = len(has_audio_input) * 2 + (
            sum(1 for h in has_audio_input if h)
        )  # not exact — recompute below

        audio_path = (sc.get("audio_path") or "").strip()
        if audio_path and os.path.exists(audio_path):
            inputs += ["-i", os.path.abspath(audio_path)]
            has_audio_input.append(True)
        else:
            has_audio_input.append(False)

    # Now compute correct stream indices.
    # Each scene contributes 1 input (clip) + optional 1 input (audio).
    # We walk scenes again to assign indices.
    cursor = 0
    audio_idx_per_scene: list[tuple[int, str]] = []  # (input_idx, type) per scene's audio
    clip_idx_per_scene: list[int] = []
    for i, sc in enumerate(scenes):
        clip_idx_per_scene.append(cursor)
        cursor += 1
        if has_audio_input[i]:
            audio_idx_per_scene.append((cursor, "real"))
            cursor += 1
        else:
            audio_idx_per_scene.append((-1, "silence"))

    for i, sc in enumerate(scenes):
        duration = float(sc.get("duration_s") or 5.0)
        ci = clip_idx_per_scene[i]
        # Video chain: scale to fit 1080x1920, centre-crop, trim, reset PTS.
        filter_parts.append(
            f"[{ci}:v]"
            f"scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"trim=duration={duration:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"fps=30"
            f"[v{i}]"
        )
        # Audio chain.
        ai_idx, ai_type = audio_idx_per_scene[i]
        if ai_type == "real":
            filter_parts.append(
                f"[{ai_idx}:a]"
                f"atrim=duration={duration:.3f},"
                f"asetpts=PTS-STARTPTS,"
                f"aresample=async=1:first_pts=0"
                f"[a{i}]"
            )
        else:
            # Silent track of matching length so concat shapes line up.
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=44100:"
                f"duration={duration:.3f}[a{i}]"
            )
        concat_pairs.append(f"[v{i}][a{i}]")

    # Concatenate all scenes.
    n = len(scenes)
    filter_parts.append(
        "".join(concat_pairs) + f"concat=n={n}:v=1:a=1[catv][cata]"
    )

    # Burn subtitles if we found an ASS file.
    if ass_path:
        # ffmpeg's ass filter wants a path; on Windows we'd need to escape
        # ":" and "\" but on Linux the absolute POSIX path is fine.
        filter_parts.append(f"[catv]ass='{_escape_ffmpeg_path(ass_path)}'[outv]")
        v_label = "[outv]"
    else:
        v_label = "[catv]"

    filter_complex = ";".join(filter_parts)

    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", v_label,
        "-map", "[cata]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    log.info("ffmpeg: assemble %d scenes -> %s (ass=%s)",
             n, out_path, "yes" if ass_path else "no")
    # ffmpeg can take 10-60s for ~30s of vertical 1080p footage on a
    # mid-tier VPS; allow plenty of headroom.
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-1500:]
        raise RuntimeError(
            f"ffmpeg returned rc={result.returncode}. stderr tail:\n"
            f"{stderr_tail}"
        )
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(
            f"ffmpeg succeeded (rc=0) but output file is missing or too "
            f"small: {out_path}"
        )
    log.info("ffmpeg: assembled ok size=%d bytes", os.path.getsize(out_path))


def _escape_ffmpeg_path(p: str) -> str:
    """Escape characters that break ffmpeg filter argument parsing.

    Inside an ``ass=`` filter value the worst offenders are ``:`` (separates
    filter options) and ``'`` (terminates the quoted value). We back-slash
    them; on POSIX absolute paths that is enough.
    """
    return p.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
