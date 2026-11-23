"""Read-only witnesses for the exact Studio-authored Clip pose.
The caller authenticates the original frame manifests and saved source first.
This module decodes originals and never creates or dispatches a native effect.
"""
from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path

WIDTH, HEIGHT, LEFT, TOP = 500, 400, 1000, 350
FILL = (224, 169, 64)


def original_composed_rgb(frame: Path) -> bytes:
    if not frame.is_file() or frame.is_symlink():
        raise AssertionError("Composed Clip original PNG is not a plain file")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-i", str(frame), "-vf", f"crop={WIDTH}:{HEIGHT}:{LEFT}:{TOP}",
               "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AssertionError("Composed Clip original decoder is unavailable") from error
    if result.returncode or len(result.stdout) != WIDTH * HEIGHT * 3:
        raise AssertionError("Composed Clip original RGB24 dimensions are incomplete")
    return result.stdout


def verify_composed_clip_frames(originals: tuple[bytes, bytes, bytes]) -> dict:
    if len(originals) != 3 or any(len(frame) != WIDTH * HEIGHT * 3 for frame in originals):
        raise AssertionError("Composed Clip RGB24 dimensions or frame roster changed")

    def rgb(frame: int, x: int, y: int) -> tuple[int, ...]:
        offset = ((y - TOP) * WIDTH + x - LEFT) * 3
        return tuple(originals[frame][offset:offset + 3])

    def near(actual, expected):
        return all(abs(a - b) <= 3 for a, b in zip(actual, expected))

    # At frame0, (1190,465) is inside the rotated source but outside the
    # corresponding unrotated starting rectangle. Both points are inside Clip.
    for point in ((1190, 465), (1240, 500)):
        if not near(rgb(0, *point), FILL):
            raise AssertionError("Composed Clip lost its original rotated starting geometry")

    # These points are outside the source at frame0, covered after cubic
    # movement/resize at frame15, then uncovered by the exact FadeOut at30.
    # The first and terminal originals independently establish the local
    # background; a moving background invalidates this bounded witness.
    for point in ((1140, 500), (1190, 620)):
        first, middle, background = (rgb(index, *point) for index in range(3))
        if not near(first, background) or sum(abs(a - b) for a, b in zip(FILL, background)) < 45:
            raise AssertionError("Composed Clip motion/resize or original background is not established")
        expected = tuple((foreground + behind) / 2 for foreground, behind in zip(FILL, background))
        if not near(middle, expected):
            raise AssertionError("Composed Clip exact cubic geometry and linear half-opacity were lost")

    # The actual child covers these world points without its parent Clip.
    # Its color/alpha must remain absent outside the parent's x=1280 plane.
    for point in ((1320, 500), (1380, 500)):
        background = rgb(2, *point)
        if sum(abs(a - b) for a, b in zip(FILL, background)) < 45:
            raise AssertionError("Composed Clip outside witness has no source contrast")
        if not near(rgb(0, *point), background) or not near(rgb(1, *point), background):
            raise AssertionError("Composed Clip exposed original child pixels beyond its parent")

    return {
        "schema": "motionwright.native-composed-clip-original-pixels/1",
        "status": "ORIGINAL_NATIVE_COMPOSED_CLIP_POSE_PIXELS_PASS",
        "composition_case": "pose", "parent_right_px": 1280, "source_fill": "#E0A940",
        "geometry_curve": "ease_out_cubic", "opacity_curve": "linear",
        "frames": [{"frame": frame, "source_roi_sha256": hashlib.sha256(pixels).hexdigest(),
                    "motion_rgb": list(rgb(index, 1140, 500)),
                    "outside_rgb": list(rgb(index, 1320, 500))}
                   for index, (frame, pixels) in enumerate(zip((0, 15, 30), originals))],
        "source_replaced_with_overlay": False,
        "native_effect_invoked_by_validator": False, "human_visual_acceptance": False,
    }
