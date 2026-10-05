#!/usr/bin/env python3
"""Generate the Motionwright desktop icon with only Python's standard library."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 512
SCALE = 4
OUT = Path(__file__).resolve().parents[1] / "apps" / "desktop" / "src-tauri" / "icons" / "icon.png"

BG = (9, 11, 14, 255)
INK = (242, 244, 243, 255)
AMBER = (245, 184, 74, 255)
TRANSPARENT = (0, 0, 0, 0)


def inside_round_rect(x: float, y: float, inset: float, radius: float) -> bool:
    left, top = inset, inset
    right, bottom = SIZE - inset, SIZE - inset
    if left + radius <= x <= right - radius and top <= y <= bottom:
        return True
    if left <= x <= right and top + radius <= y <= bottom - radius:
        return True
    cx = left + radius if x < left + radius else right - radius
    cy = top + radius if y < top + radius else bottom - radius
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    denom = vx * vx + vy * vy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy)


def sample(x: float, y: float) -> tuple[int, int, int, int]:
    if not inside_round_rect(x, y, 28, 92):
        return TRANSPARENT

    color = BG
    strokes = (
        (142, 366, 142, 146),
        (142, 146, 256, 292),
        (256, 292, 370, 146),
        (370, 146, 370, 366),
    )
    if any(distance_to_segment(x, y, *stroke) <= 22 for stroke in strokes):
        color = INK

    # The amber splice line is Motionwright's signature move.
    if 252 <= x <= 260 and 82 <= y <= 430:
        color = AMBER
    if 238 <= x <= 274 and (78 <= y <= 86 or 426 <= y <= 434):
        color = AMBER
    return color


def render() -> bytes:
    rows: list[bytes] = []
    for y in range(SIZE):
        row = bytearray([0])
        for x in range(SIZE):
            accum = [0, 0, 0, 0]
            for sy in range(SCALE):
                for sx in range(SCALE):
                    rgba = sample(x + (sx + 0.5) / SCALE, y + (sy + 0.5) / SCALE)
                    for i, value in enumerate(rgba):
                        accum[i] += value
            count = SCALE * SCALE
            row.extend(round(value / count) for value in accum)
        rows.append(bytes(row))
    return b"".join(rows)


def chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = render()
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=9))
        + chunk(b"IEND", b"")
    )
    OUT.write_bytes(png)
    print(f"wrote {OUT} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
