"""Strict independent decoded RGB check for pinned Semwright-native caption frames.

This does not render video, manufacture captions or read private broker state.
It only independently decodes the *original* output PNGs through FFmpeg.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path


class NativeCaptionPixelsError(AssertionError):
    pass


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_frame(root: Path, idx: int) -> Path:
    if idx not in (0, 22, 42, 59):
        raise NativeCaptionPixelsError("unapproved caption frame index")
    manifest = root / "artifact-manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 2097152:
        raise NativeCaptionPixelsError("native artifact manifest is absent or unsafe")
    payload = json.loads(manifest.read_text())
    plan = payload.get("plan", {})
    rows = payload.get("frames", [])
    if plan.get("frame_count") != 60 or len(rows) != 60:
        raise NativeCaptionPixelsError("native caption source has the wrong frame index")
    row = rows[idx]
    relative = f"frames/{idx:06}.png"
    if row.get("index") != idx or row.get("file") != relative:
        raise NativeCaptionPixelsError("source manifest frame index/file mismatch")
    p = root / "frames" / f"{idx:06}.png"
    if p.is_symlink() or not p.is_file():
        raise NativeCaptionPixelsError("native caption frame missing or a symlink")
    if _digest(p) != row.get("sha256") or p.stat().st_size != row.get("bytes"):
        raise NativeCaptionPixelsError("native caption source frame digest/length mismatch")
    return p


def _rgb(frame: Path, w: int = 1920, h: int = 1080) -> bytes:
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(frame),
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        capture_output=True, timeout=30, check=False,
    )
    if p.returncode != 0 or len(p.stdout) != w * h * 3:
        raise NativeCaptionPixelsError("independent native PNG decode was incomplete or changed output dimensions")
    return p.stdout


def compare_caption_overlay(
    baseline_root: Path,
    captioned_root: Path,
    expected_cue_count: int,
) -> dict:
    if expected_cue_count != 2:
        raise NativeCaptionPixelsError("caption fixture must retain exactly two timed source cues")
    observed: dict[str, object] = {}
    for idx in (0, 22, 42, 59):
        plain = _rgb(_load_frame(baseline_root, idx))
        burned = _rgb(_load_frame(captioned_root, idx))
        if idx in (0, 59):
            if plain != burned:
                raise NativeCaptionPixelsError(
                    f"inactive native caption unexpectedly changed original pixels at {idx}"
                )
            observed[str(idx)] = {"inactive_all_pixels_identical": True}
            continue
        # The original scene is identical in both source revisions. Only the
        # native caption overlay may change the decoded lower-frame pixels.
        w, top, bottom = 1920, 600, 1080
        first = top * w * 3
        changed = sum(
            plain[i:i+3] != burned[i:i+3]
            for i in range(first, bottom * w * 3, 3)
        )
        if plain[:first] != burned[:first]:
            raise NativeCaptionPixelsError(
                "caption drawing modified original pixels outside the declared lower-frame overlay"
            )
        if changed < 100 or changed > 200_000:
            raise NativeCaptionPixelsError(
                f"native cue {idx} was absent, corrupted or excessively broad"
            )
        observed[str(idx)] = {
            "actual_native_lower_frame_changed_pixel_count": changed,
            "upper_original_pixels_identical": True,
            "source_frame_sha256": _digest(_load_frame(baseline_root, idx)),
            "captioned_frame_sha256": _digest(_load_frame(captioned_root, idx)),
        }
    return {
        "schema": "motionwright-native-caption-decoded-pixel-evidence/1",
        "verified_source": "original_pinned_Semwright_Motion_Canvas_PNGs",
        "sampled_frames": observed,
        "native_caption_source_cues": expected_cue_count,
        "original_geometry_retained_outside_overlay": True,
        "captions_drawn_only_when_cues_active": True,
        "full_master_caption_acceptance": "NOT_RUN",
        "audio_sync_or_human_readability": "NOT_RUN",
    }
