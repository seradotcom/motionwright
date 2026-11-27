"""Read-only pixel witnesses for the synthetic AAC31 installed playback case.

The caller captures the installed WebView's video content rectangle, with
letterboxing excluded, after invoking its normal Play control. This module
examines that viewport; it does not decode an MP4 or manufacture a UI action.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns, sleep


@dataclass(frozen=True)
class PlaybackFrame:
    width: int
    height: int
    row_stride: int
    channels: int
    pixels: bytes


def marker(frame: PlaybackFrame) -> str | None:
    """Identify the two authored scene markers in a captured video viewport."""
    w, h = frame.width, frame.height
    stride, channels = frame.row_stride, frame.channels
    if (not 160 <= w <= 4096 or not 90 <= h <= 2160
            or abs(w * 9 - h * 16) > 16
            or channels not in (3, 4)
            or not w * channels <= stride <= w * channels + 64
            or not stride * (h - 1) + w * channels <= len(frame.pixels) <= stride * h):
        raise ValueError("invalid AAC31 playback viewport")

    def samples(x_160: int) -> list[tuple[int, int, int]]:
        center_x, center_y = w * x_160 // 160, h * 40 // 90
        return [
            tuple(frame.pixels[offset:offset + 3])
            for y in (center_y - 2, center_y, center_y + 2)
            for x in (center_x - 2, center_x, center_x + 2)
            for offset in (y * stride + x * channels,)
        ]

    left, right = samples(40), samples(70)
    for name, active, inactive, primary in (
        ("motion-canvas", left, right, 0), ("manim", right, left, 1),
    ):
        if (all(rgb[primary] >= 96 and all(
                channel <= 48 for index, channel in enumerate(rgb) if index != primary
                ) for rgb in active)
                and all(max(rgb) <= 32 for rgb in inactive)):
            return name
    return None


def observe_playback(capture, *, play_intent_ns: int, deadline_ns: int
                     ) -> tuple[tuple[PlaybackFrame, int], tuple[PlaybackFrame, int]]:
    """Require a red-to-green transition in one bounded playback observation."""
    if not 0 < play_intent_ns < deadline_ns or deadline_ns - play_intent_ns > 5_000_000_000:
        raise ValueError("invalid AAC31 playback observation window")
    first = None
    while monotonic_ns() < deadline_ns:
        frame = capture()
        observed = marker(frame)
        now = monotonic_ns()
        if now >= deadline_ns:
            break
        if observed == "motion-canvas" and first is None:
            first = (frame, now)
        elif observed == "manim" and first is not None and now > first[1]:
            return first, (frame, now)
        sleep(0.02)
    raise ValueError("AAC31 playback did not show the two authored scenes in order")
