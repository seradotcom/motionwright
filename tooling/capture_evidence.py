#!/usr/bin/env python3
"""Bounded verification of first-party browser capture evidence.

Only verifies a real Playwright screenshot of an owned local fixture, never
arbitrary website URLs, private sessions, scientific truth or third-party rights.
No screenshot creation or network activity is performed by this verifier.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zlib

SCHEMA = "motionwright.first-party-capture-evidence/1"
EXPECTED_ORIGIN = "http://127.0.0.1:1420"
CAPTURE_NAME = "capture.png"
DATA_STATE_NAME = "fixture-state.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
PNG_HEADER = bytes((137, 80, 78, 71, 13, 10, 26, 10))
REDACTION_COLOR = (11, 11, 11)
MAX_PNG_BYTES = 12 * 1024 * 1024


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exactly(value: object, keys: set[str], name: str) -> None:
    require(type(value) is dict and set(value) == keys, name + " schema keys mismatch")


def png_rgb(source: bytes) -> tuple[int, int, bytes]:
    """Decode one 8-bit noninterlaced RGB(A) PNG, including all PNG row filters.

    Its purpose is checking masked source pixels, not image processing or an
    OCR-based claim about visible app functionality.
    """
    require(len(source) <= MAX_PNG_BYTES, "PNG exceeds evidence byte budget")
    require(source.startswith(PNG_HEADER), "Capture is not a PNG")
    cursor = 8
    packed = bytearray()
    width = height = depth = color_type = interlace = None
    seen_end = False
    while cursor + 12 <= len(source):
        size = struct.unpack_from(">I", source, cursor)[0]
        require(size <= MAX_PNG_BYTES and cursor + 12 + size <= len(source),
                "Malformed PNG chunk length")
        chunk_type = source[cursor + 4:cursor + 8]
        payload = source[cursor + 8:cursor + 8 + size]
        crc = struct.unpack_from(">I", source, cursor + 8 + size)[0]
        require(zlib.crc32(chunk_type + payload) & 0xFFFFFFFF == crc,
                "PNG chunk checksum failed")
        cursor += size + 12
        if chunk_type == b"IHDR":
            require(width is None and size == 13, "Duplicated PNG header")
            width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload)
            require(compression == filtering == interlace == 0,
                    "PNG compress/filter/interlace mode unsupported")
        elif chunk_type == b"IDAT":
            require(width is not None, "PNG data before header")
            packed.extend(payload)
            require(len(packed) <= MAX_PNG_BYTES, "PNG compressed data budget exceeded")
        elif chunk_type == b"IEND":
            require(size == 0, "Invalid PNG end marker")
            seen_end = True
            break
        else:
            require(chunk_type[0] & 0x20 != 0, "Unrecognized critical PNG chunk")
    require(seen_end and cursor == len(source), "PNG has missing end marker or trailing payload")
    require(type(width) is int and type(height) is int
            and 320 <= width <= 3840 and 320 <= height <= 2160,
            "Capture dimensions out of allowed viewport range")
    require(depth == 8 and color_type in (2, 6), "Only 8-bit RGB/RGBA capture PNG accepted")
    channels = 4 if color_type == 6 else 3
    row_bytes = width * channels
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(packed), (row_bytes + 1) * height + 1)
    require(decoder.eof and len(raw) == (row_bytes + 1) * height,
            "PNG decoded scanlines mismatch or decompression overflow")
    require(not decoder.unused_data and not decoder.unconsumed_tail,
            "PNG contains extra compressed image content")
    output = bytearray(width * height * 3)
    previous = bytearray(row_bytes)
    offset = 0
    for y in range(height):
        mode = raw[offset]
        row = bytearray(raw[offset + 1:offset + row_bytes + 1])
        require(mode <= 4, "PNG row filter is unsupported")
        for i in range(row_bytes):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if mode == 0:
                correction = 0
            elif mode == 1:
                correction = left
            elif mode == 2:
                correction = up
            elif mode == 3:
                correction = (left + up) // 2
            else:
                candidate = left + up - upper_left
                dist = (abs(candidate - left), abs(candidate - up), abs(candidate - upper_left))
                correction = (left, up, upper_left)[dist.index(min(dist))]
            row[i] = (row[i] + correction) & 255
        for x in range(width):
            start = x * channels
            target = (y * width + x) * 3
            output[target:target + 3] = row[start:start + 3]
            if channels == 4:
                require(row[start + 3] == 255, "Screenshot contains unexpected transparent pixels")
        previous = row
        offset += row_bytes + 1
    return width, height, bytes(output)


def rgb_at(pixels: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    index = 3 * (y * width + x)
    return tuple(pixels[index:index + 3])


def verify_bundle(folder: Path, *, expected_commit: str | None = None) -> dict:
    evidence_path = folder / "capture-evidence.json"
    screenshot_path = folder / CAPTURE_NAME
    state_path = folder / DATA_STATE_NAME
    require(evidence_path.is_file() and screenshot_path.is_file() and state_path.is_file(),
            "Capture bundle is missing source evidence")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    exactly(payload, {"schema", "app", "scenario", "claim_id", "fixture_state_sha256",
                      "redactions", "capture", "review_status"}, "capture manifest")
    require(payload["schema"] == SCHEMA, "Unsupported capture evidence version")
    exactly(payload["app"], {"id", "version", "source_revision"}, "capture app")
    require(payload["app"]["id"] == "motionwright-studio",
            "Only the authorized first-party application is admitted")
    require(isinstance(payload["app"]["version"], str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", payload["app"]["version"]) is not None,
            "Missing pinned application version")
    revision = payload["app"]["source_revision"]
    require(type(revision) is str and HEX40.fullmatch(revision) is not None,
            "Missing exact Git source commit")
    if expected_commit is not None:
        require(revision == expected_commit, "Evidence belongs to a different source revision")
    scenario = payload["scenario"]
    exactly(scenario, {"id", "origin", "route", "actions", "viewport",
                       "browser", "fixture_kind", "auth_mode",
                       "external_requests", "credential_fields"}, "capture scenario")
    require(scenario["id"] == "production-original-ui-local-v1",
            "Unregistered capture scenario")
    require(scenario["origin"] == EXPECTED_ORIGIN and scenario["route"] == "/",
            "Capture target is not the authorized loopback app route")
    require(scenario["actions"] == ["open:/", "click:Production"],
            "Capture scenario navigation is not reproducible")
    exactly(scenario["viewport"], {"width", "height", "device_scale_factor"}, "capture viewport")
    viewport = scenario["viewport"]
    require(viewport == {"width": 1440, "height": 940, "device_scale_factor": 1},
            "Unexpected viewport or device pixel ratio")
    require(scenario["browser"] == "chromium" and scenario["auth_mode"] == "none"
            and scenario["fixture_kind"] == "synthetic-first-party-data"
            and scenario["external_requests"] == 0 and scenario["credential_fields"] == 0,
            "Capture may contain uncontrolled browser state or external data")
    require(payload["review_status"] == "technical-evidence-only-human-review-required",
            "Capture is not a certified third-party product demonstration")
    state_bytes = state_path.read_bytes()
    require(type(payload["fixture_state_sha256"]) is str
            and HEX64.fullmatch(payload["fixture_state_sha256"]) is not None
            and digest(state_bytes) == payload["fixture_state_sha256"],
            "Capture fixture data state digest mismatch")
    state = json.loads(state_bytes)
    exactly(state, {"id", "generation", "revision", "claims"}, "capture fixture state")
    require(type(state["revision"]) is int and state["revision"] >= 1
            and type(state["claims"]) is list and len(state["claims"]) == 1,
            "Capture fixture lacks exact authored claim")
    claim = state["claims"][0]
    exactly(claim, {"id", "text", "source", "context", "source_revision"}, "capture claim")
    require(claim["id"] == payload["claim_id"]
            and isinstance(claim["text"], str) and 10 <= len(claim["text"]) <= 200
            and claim["context"] == "synthetic-first-party-capture-validation"
            and claim["source"] == {"kind": "project_revision", "revision": state["revision"]},
            "Capture claim is not linked to the pinned project source revision")
    require(type(payload["claim_id"]) is str and
            re.fullmatch(r"[0-9a-fA-F-]{36}", payload["claim_id"]) is not None,
            "Claim ID is invalid")
    capture = payload["capture"]
    exactly(capture, {"path", "sha256", "repeated_sha256", "pixel_equal",
                      "redacted", "raw_unredacted_saved", "width", "height"}, "capture image")
    require(capture["path"] == CAPTURE_NAME and capture["pixel_equal"] is True
            and capture["redacted"] is True and capture["raw_unredacted_saved"] is False,
            "Raw or unreproduced capture is not accepted")
    screenshot = screenshot_path.read_bytes()
    require(type(capture["sha256"]) is str
            and digest(screenshot) == capture["sha256"] == capture["repeated_sha256"],
            "Capture screenshot is not exact-source repeatable")
    width, height, pixels = png_rgb(screenshot)
    require((width, height) == (capture["width"], capture["height"])
            == (viewport["width"], viewport["height"]),
            "PNG evidence dimensions do not match authored viewport")
    redactions = payload["redactions"]
    require(type(redactions) is list and len(redactions) == 1,
            "Exactly one explicit fixture redaction required")
    mask = redactions[0]
    exactly(mask, {"selector", "x", "y", "width", "height", "mask_color"}, "capture mask")
    require(mask["selector"] == ".revision-chip" and mask["mask_color"] == "#0B0B0B",
            "Only the reviewed synthetic fixture redaction is allowed")
    for key in ["x", "y", "width", "height"]:
        require(type(mask[key]) is int, "Mask rectangle must use integer pixels")
    x, y, w, h = (mask[key] for key in ["x", "y", "width", "height"])
    require(0 <= x < width and 0 <= y < height and w >= 12 and h >= 12
            and x + w <= width and y + h <= height and w * h < width * height // 10,
            "Mask bounds invalid, absent or excessively broad")
    for px, py in [(x + 3, y + 3), (x + w // 2, y + h // 2),
                   (x + w - 4, y + 3), (x + 3, y + h - 4),
                   (x + w - 4, y + h - 4)]:
        require(rgb_at(pixels, width, px, py) == REDACTION_COLOR,
                "The screenshot does not contain the declared opaque mask pixels")
    sampled = {
        rgb_at(pixels, width, xx, yy)
        for yy in range(12, height - 12, 46)
        for xx in range(12, width - 12, 46)
        if not (x <= xx < x + w and y <= yy < y + h)
    }
    require(len(sampled) >= 12, "Capture is blank or does not contain real application UI")
    return {
        "technical_evidence": "PASS",
        "source_revision": revision,
        "screenshot_sha256": capture["sha256"],
        "fixture_sha256": payload["fixture_state_sha256"],
        "claim_id": payload["claim_id"],
        "redaction": "confirmed-by-decoded-pixels",
        "scope": "first-party-synthetic-fixture-only",
        "human_review": "required",
    }


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: capture_evidence.py BUNDLE_DIR [EXACT_GIT_SHA]")
    print(json.dumps(verify_bundle(Path(sys.argv[1]), expected_commit=sys.argv[2]
                                   if len(sys.argv) == 3 else None),
                     indent=2, sort_keys=True))
