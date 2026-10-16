"""Independent decoded-pixel admission for MLT H.264/AAC with native subtitles.

CI ONLY. The existing Semwright Native SDK first renders verified native
caption subjects to source PNG, then the MLT Driver Host encodes FFV1 + MP4.
This observer only reads the ACTUAL two owner-produced video artifacts through
FFmpeg. It never adds captions, changes timelines, writes an MP4, or obtains a
provider grant. An H.264 stream is inherently lossy, so we compare only the
native caption-specific high-contrast mask with a bounded error budget.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

WIDTH, HEIGHT = 1280, 720
MAX_FRAME_BYTES = WIDTH * HEIGHT * 3
SUPPORTED = frozenset((0, 14, 15, 31, 32))
# Native text is placed by the pinned Film authoring safe-area grammar.
ROI = (80, 440, 1200, 700)


class CaptionedMasterPixelsNotVerified(AssertionError):
    pass


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _frame(path: Path, index: int, ffmpeg: Path) -> bytes:
    if index not in SUPPORTED:
        raise CaptionedMasterPixelsNotVerified("Unapproved decoded frame index")
    if path.is_symlink() or not path.is_file() or not ffmpeg.is_file():
        raise CaptionedMasterPixelsNotVerified("Original bound native video/decoder missing")
    result = subprocess.run(
        [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-an", "-sn", "-dn",
         "-vf", rf"select=eq(n\,{index})", "-vsync", "0", "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False,
    )
    if result.returncode != 0 or len(result.stdout) != MAX_FRAME_BYTES:
        raise CaptionedMasterPixelsNotVerified("Original native video frame decoder did not return one exact RGB frame")
    return result.stdout


def _caption_mask(
    previous: bytes,
    active: bytes,
    master: bytes,
    *,
    width: int = WIDTH,
    height: int = HEIGHT,
    roi: tuple[int, int, int, int] = ROI,
    minimum: int = 350,
) -> dict:
    if not (len(previous) == len(active) == len(master) == width * height * 3):
        raise CaptionedMasterPixelsNotVerified("Native decoded RGB frame size differs")
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise CaptionedMasterPixelsNotVerified("Caption viewing region is out of bounds")
    count = 0
    error_active = error_previous = contrast = 0
    for y in range(top, bottom):
        for x in range(left, right):
            i = (y * width + x) * 3
            delta = max(abs(previous[i + j] - active[i + j]) for j in range(3))
            if delta < 48:
                continue
            count += 1
            contrast += delta
            error_active += max(abs(master[i + j] - active[i + j]) for j in range(3))
            error_previous += max(abs(master[i + j] - previous[i + j]) for j in range(3))
    if count < minimum or count > min(100_000, (right - left) * (bottom - top) // 3):
        raise CaptionedMasterPixelsNotVerified("Real FFV1 subtitle is absent or changes too much of the native scene")
    if contrast < count * 52 or error_previous <= count * 20:
        raise CaptionedMasterPixelsNotVerified("Native source mask cannot independently distinguish active caption")
    if error_active * 3 >= error_previous * 2 or error_active > count * 48:
        raise CaptionedMasterPixelsNotVerified("Final MLT H.264 did not retain the actual native caption mask")
    return {
        "native_caption_mask_pixels": count,
        "masked_native_original_contrast_mean": round(contrast / count, 3),
        "masked_lossy_master_error_mean": round(error_active / count, 3),
        "masked_inactive_reference_error_mean": round(error_previous / count, 3),
    }


def _inactive_comparison(reference: bytes, master: bytes) -> float:
    if len(reference) != MAX_FRAME_BYTES or len(master) != MAX_FRAME_BYTES:
        raise CaptionedMasterPixelsNotVerified("Inactive source frame has invalid RGB geometry")
    # A lossy H.264 encoder must retain the *original* native image rather than
    # substituting its own blank frame or a second WebView compositor.
    diff = sum(abs(reference[i] - master[i]) for i in range(0, len(reference), 4))
    mean = diff / ((len(reference) + 3) // 4)
    if mean > 25:
        raise CaptionedMasterPixelsNotVerified("Inactive H.264 native frame materially diverges from owner FFV1")
    return round(mean, 3)


def prove_native_captioned_master(
    ffv1: Path,
    master_mp4: Path,
    ffmpeg: Path,
    *,
    source_sha: str,
    semwright_sha: str,
    selected_cues: int,
) -> dict:
    if (
        selected_cues != 2
        or any(len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
               for sha in (source_sha, semwright_sha))
    ):
        raise CaptionedMasterPixelsNotVerified("Captioned master source identity/cue grammar is invalid")
    original = {i: _frame(ffv1, i, ffmpeg) for i in SUPPORTED}
    mastered = {i: _frame(master_mp4, i, ffmpeg) for i in (0, 15, 32)}
    inactive = _inactive_comparison(original[0], mastered[0])
    first = _caption_mask(original[14], original[15], mastered[15])
    last = _caption_mask(original[31], original[32], mastered[32])
    return {
        "schema": "motionwright-v05-native-captioned-master-pixels/1",
        "motionwright_source_sha": source_sha,
        "semwright_source_sha": semwright_sha,
        "captioned_master_decoded_pixels": "PASS",
        "original_native_ffv1_frame_indices": [0, 14, 15, 31, 32],
        "decoded_h264_frame_indices": [0, 15, 32],
        "native_cue_count": selected_cues,
        "inactive_native_frame_mean_abs_byte_delta": inactive,
        "active_caption_frames": {"15": first, "32": last},
        "original_ffv1_sha256": _digest(ffv1),
        "actual_master_mp4_sha256": _digest(master_mp4),
        "original_owner_media_or_transcript_published": False,
        "human_readability": "NOT_RUN",
        "voice_speech_authenticity": "NOT_VERIFIED",
        "commercial_release_authorized": False,
    }
