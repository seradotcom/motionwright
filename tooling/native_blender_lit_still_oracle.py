#!/usr/bin/env python3
"""Independent full-pixel observations for the closed Blender lit-still fixture.

No Blender process, authoring command or retry is launched here. The runner supplies the
admitted decoder and the four original PNG identities from the canonical Host.
The result records these predeclared finite oracles; formal evaluation is separate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import time
import zlib
from fractions import Fraction

WIDTH, HEIGHT = 1920, 1080
RGBA_BYTES = WIDTH * HEIGHT * 4
PNG_LIMIT = 32 * 1024 * 1024
ROLES = ("original", "edited", "reopened", "light_off")
NAMES = dict(zip(ROLES, ("lit-original.png", "lit-edited.png",
                         "lit-reopened.png", "lit-light-off.png")))
FIXTURE_CONTRACT_SHA256 = "3f2dbbed43ea58b3c37087e2e758888395083e4de175eec07507d8870355a495"
INTERIORS = {
    "gold": (302, 202, 698, 498),
    "blue": (1002, 402, 1238, 638),
    "rose_disk": (1350, 300, 1430, 380),
}
OLD_STRIP = (302, 202, 322, 498)
NEW_STRIP = (702, 202, 722, 498)
GOLD_MASK = (250, 150, 775, 550)


class ObservationError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def inspect_png(data: bytes) -> dict:
    """Validate the entire static RGBA8 PNG framing and CRC before decoding."""
    if not isinstance(data, bytes) or not 57 <= len(data) <= PNG_LIMIT:
        raise ObservationError("PNG byte count outside the closed profile")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ObservationError("PNG signature differs")
    offset = 8
    chunks = []
    idat_seen = False
    idat_closed = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ObservationError("PNG chunk header truncated")
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data) or size > PNG_LIMIT:
            raise ObservationError("PNG chunk length exceeds actual bytes")
        payload = data[offset + 8:offset + 8 + size]
        crc = int.from_bytes(data[offset + 8 + size:end], "big")
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ObservationError("PNG chunk CRC differs")
        if len(kind) != 4 or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            raise ObservationError("PNG chunk type is invalid")
        if kind in (b"acTL", b"fcTL", b"fdAT"):
            raise ObservationError("Animated PNG is outside the single-frame profile")
        if not chunks:
            if kind != b"IHDR" or size != 13:
                raise ObservationError("PNG must begin with one IHDR")
            if struct.unpack(">IIBBBBB", payload) != (WIDTH, HEIGHT, 8, 6, 0, 0, 0):
                raise ObservationError("PNG dimensions or pixel format differ")
        elif kind == b"IHDR":
            raise ObservationError("PNG contains another IHDR")
        if not (kind[0] & 32) and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            raise ObservationError("Unknown critical PNG chunk")
        if kind == b"IDAT":
            if idat_closed:
                raise ObservationError("PNG IDAT sequence is not contiguous")
            idat_seen = True
        elif idat_seen:
            idat_closed = True
        chunks.append(kind.decode("ascii"))
        offset = end
        if kind == b"IEND":
            if size != 0 or not idat_seen or offset != len(data):
                raise ObservationError("PNG IEND, IDAT or trailing bytes differ")
            return {"bytes": len(data), "sha256": sha256(data), "width": WIDTH,
                    "height": HEIGHT, "bit_depth": 8, "color_type": 6,
                    "chunks": chunks, "full_crc_verified": True}
    raise ObservationError("PNG has no terminal IEND")


def _pixels(rgba: bytes, roi: tuple[int, int, int, int]):
    x0, y0, x1, y1 = roi
    for y in range(y0, y1):
        row = (y * WIDTH + x0) * 4
        for x in range(x0, x1):
            i = row + (x - x0) * 4
            yield x, y, max(rgba[i], rgba[i + 1], rgba[i + 2])


def _region(rgba: bytes, roi: tuple[int, int, int, int]) -> dict:
    total = foreground = background = sum_x = sum_y = 0
    for x, y, maximum in _pixels(rgba, roi):
        total += 1
        if maximum >= 16:
            foreground += 1
            sum_x += x
            sum_y += y
        if maximum <= 1:
            background += 1
    return {"pixels": total, "foreground": foreground, "background": background,
            "sum_x": sum_x, "sum_y": sum_y}


def _at_least(count: int, total: int, percent: int) -> bool:
    return total > 0 and count * 100 >= percent * total


def observe_rgba(images: dict[str, bytes]) -> dict:
    if not isinstance(images, dict) or set(images) != set(ROLES):
        raise ObservationError("Exactly four named decoded originals are required")
    for role, rgba in images.items():
        if not isinstance(rgba, bytes) or len(rgba) != RGBA_BYTES:
            raise ObservationError(f"{role} has incomplete full RGBA bytes")
        if rgba[3::4].count(255) != WIDTH * HEIGHT:
            raise ObservationError(f"{role} contains a nonopaque pixel")
    original, edited = images["original"], images["edited"]
    interiors = {name: _region(original, roi) for name, roi in INTERIORS.items()}
    strips = {name: {"original": _region(original, roi), "edited": _region(edited, roi)}
              for name, roi in (("old", OLD_STRIP), ("new", NEW_STRIP))}
    masks = {role: _region(images[role], GOLD_MASK) for role in ("original", "edited")}
    displacement = None
    if all(masks[role]["foreground"] for role in ("original", "edited")):
        a, b = masks["original"], masks["edited"]
        dx = Fraction(b["sum_x"], b["foreground"]) - Fraction(a["sum_x"], a["foreground"])
        dy = Fraction(b["sum_y"], b["foreground"]) - Fraction(a["sum_y"], a["foreground"])
        displacement = {"x": {"num": dx.numerator, "den": dx.denominator},
                        "y": {"num": dy.numerator, "den": dy.denominator}}
        centroid_pass = abs(dx - 25) <= 1 and abs(dy) <= 1
    else:
        centroid_pass = False
    old, new = strips["old"], strips["new"]
    off = images["light_off"]
    checks = {
        "edited_reopened_exact": edited == images["reopened"],
        "light_off_black": all(off[channel::4].count(0) == WIDTH * HEIGHT for channel in range(3)),
        "original_not_black": all(_at_least(v["foreground"], v["pixels"], 95)
                                  for v in interiors.values()),
        "gold_old_strip_removed": _at_least(old["original"]["foreground"], old["original"]["pixels"], 95)
                                  and _at_least(old["edited"]["background"], old["edited"]["pixels"], 99),
        "gold_new_strip_gained": _at_least(new["original"]["background"], new["original"]["pixels"], 99)
                                and _at_least(new["edited"]["foreground"], new["edited"]["pixels"], 95),
        "gold_displacement_centroid": centroid_pass,
    }
    return {"schema": "motionwright.blender.lit-still-pixel-observation/1",
            "fixture_contract_sha256": FIXTURE_CONTRACT_SHA256,
            "width": WIDTH, "height": HEIGHT, "rgba_bytes_per_image": RGBA_BYTES,
            "complete_pixels_per_image": WIDTH * HEIGHT, "opaque_every_pixel": True,
            "decoded_sha256": {role: sha256(images[role]) for role in ROLES},
            "checks": checks, "all_pixel_oracles_observed": all(checks.values()),
            "trimmed_interiors": interiors, "gold_strips": strips, "gold_masks": masks,
            "gold_displacement_exact": displacement,
            "native_source_readback": "SEPARATE_CANONICAL_PHASE_EVIDENCE_REQUIRED",
            "formal_acceptance": False, "human_acceptance": False}


def _write_new(path: Path, value: dict) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _regular(path: Path, maximum: int):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ObservationError("Input must be an absolute canonical nonsymlink path")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ObservationError("Input must be a bounded regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    file = os.fdopen(fd, "rb")
    actual = os.fstat(file.fileno())
    if (before.st_dev, before.st_ino, before.st_size) != (actual.st_dev, actual.st_ino, actual.st_size):
        file.close()
        raise ObservationError("Input identity changed while opening")
    return file


def _identity(file) -> tuple:
    s = os.fstat(file.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def decode_original_png(role: str, png: Path, expected: dict, ffmpeg: Path,
                        ffmpeg_sha256: str, destination: Path,
                        *, environment: dict[str, str]) -> tuple[bytes, dict]:
    """One bounded Linux read-only decoder invocation; never render or retry."""
    if sys.platform != "linux":
        raise ObservationError("The native decoder gate requires the admitted Linux runner")
    if role not in ROLES or png.name != NAMES[role] or set(expected) != {"sha256", "bytes"}:
        raise ObservationError("Decoder input is not the exact named original")
    if not _sha(expected["sha256"]) or type(expected["bytes"]) is not int or not _sha(ffmpeg_sha256):
        raise ObservationError("Decoder input identities are malformed")
    if not destination.is_absolute() or destination.resolve(strict=True) != destination:
        raise ObservationError("Decoder destination must be an existing canonical private directory")
    # The exclusive intent precedes all original input opens and any subprocess.
    intent = destination / f"{role}.decode-intent.json"
    _write_new(intent, {"schema": "motionwright.blender.lit-still-decode-intent/1",
                        "role": role, "original": expected, "decoder_sha256": ffmpeg_sha256,
                        "timeout_seconds": 30, "maximum_raw_bytes": RGBA_BYTES,
                        "native_render": False, "retry": False})
    result = {"schema": "motionwright.blender.lit-still-decode-receipt/1",
              "role": role, "intent_sha256": sha256(intent.read_bytes()),
              "decoder_sha256": ffmpeg_sha256, "native_render": False,
              "state": "INCOMPLETE", "formal_acceptance": False}
    proc = None
    started = time.monotonic()
    raw_path = destination / f"{role}.rgba"
    stderr_path = destination / f"{role}.decode-stderr"
    try:
        with _regular(png, PNG_LIMIT) as source, _regular(ffmpeg, 256 * 1024 * 1024) as tool:
            source_id, tool_id = _identity(source), _identity(tool)
            data = source.read(PNG_LIMIT + 1)
            metadata = inspect_png(data)
            if {"bytes": len(data), "sha256": sha256(data)} != expected:
                raise ObservationError("Original PNG differs from its canonical phase receipt")
            tool_data = tool.read(256 * 1024 * 1024 + 1)
            if tool_data[:4] != b"\x7fELF" or sha256(tool_data) != ffmpeg_sha256:
                raise ObservationError("Decoder executable differs from admitted ELF bytes")
            del tool_data
            source.seek(0)
            argv = [str(ffmpeg), "-nostdin", "-v", "error", "-threads", "1",
                    "-protocol_whitelist", "pipe", "-f", "png_pipe", "-i", "pipe:0",
                    "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn",
                    "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"]
            result["argv"] = argv
            result["png"] = metadata
            def limits():
                import resource
                resource.setrlimit(resource.RLIMIT_FSIZE, (RGBA_BYTES + 1, RGBA_BYTES + 1))
                resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
                resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
            with raw_path.open("xb") as raw, stderr_path.open("xb") as stderr:
                os.chmod(raw_path, 0o600)
                os.chmod(stderr_path, 0o600)
                # Execute the exact measured ELF FD and decode the exact original
                # PNG FD. No pathname is reopened by the decoder.
                proc = subprocess.Popen(argv, executable=f"/proc/self/fd/{tool.fileno()}",
                                        stdin=source, stdout=raw, stderr=stderr,
                                        env=environment, pass_fds=(tool.fileno(),),
                                        start_new_session=True, preexec_fn=limits)
                result["pid"] = proc.pid
                try:
                    result["exit_code"] = proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    result["exit_code"] = proc.wait(timeout=5)
                    result["timeout"] = True
                    raise ObservationError("Original decoder timed out; this phase cannot be replayed")
                raw.flush()
                os.fsync(raw.fileno())
            result["stderr_bytes"] = stderr_path.stat().st_size
            result["stderr_sha256"] = sha256(stderr_path.read_bytes())
            if result["exit_code"] != 0 or raw_path.stat().st_size != RGBA_BYTES:
                raise ObservationError("Decoder did not produce exactly one complete RGBA frame")
            source.seek(0)
            if (_identity(source) != source_id or sha256(source.read(PNG_LIMIT + 1)) != expected["sha256"]
                    or _identity(tool) != tool_id):
                raise ObservationError("Original PNG or admitted decoder changed during observation")
            rgba = raw_path.read_bytes()
            if rgba[3::4].count(255) != WIDTH * HEIGHT:
                raise ObservationError("Decoded original contains nonopaque pixels")
            result.update(state="COMPLETE", raw_bytes=len(rgba), raw_sha256=sha256(rgba),
                          all_pixels_decoded=True, original_fd_stable=True, decoder_fd_stable=True)
            return rgba, result
    except BaseException as error:
        result["error_type"] = type(error).__name__
        raise
    finally:
        result["seconds"] = round(time.monotonic() - started, 6)
        _write_new(destination / f"{role}.decode-receipt.json", result)
