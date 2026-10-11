#!/usr/bin/env python3
"""Adversarial capture-contract fixtures, never positive browser evidence."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

MODULE = Path(__file__).resolve().parents[1] / "capture_evidence.py"
SPEC = importlib.util.spec_from_file_location("capture_evidence", MODULE)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)

CLAIM = "00000000-0000-4000-8000-0000000000e7"
REVISION = "f" * 40
WIDTH, HEIGHT = 1440, 940
MASK = {"selector": ".revision-chip", "x": 30, "y": 20,
        "width": 90, "height": 26, "mask_color": "#0B0B0B"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunk(name: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + name + data + struct.pack(
        ">I", zlib.crc32(name + data) & 0xFFFFFFFF)


def png(*, covered: bool = True) -> bytes:
    compressor = zlib.compressobj(level=6)
    rows = bytearray()
    for y in range(HEIGHT):
        row = bytearray(WIDTH * 3)
        for x in range(WIDTH):
            offset = x * 3
            color = ((x // 16) % 251, (y // 11) % 249, (x // 25 + y // 25) % 247)
            if covered and MASK["x"] <= x < MASK["x"] + MASK["width"] and (
                MASK["y"] <= y < MASK["y"] + MASK["height"]
            ):
                color = (11, 11, 11)
            row[offset:offset + 3] = bytes(color)
        rows.extend(b"\x00")
        rows.extend(row)
    compressed = compressor.compress(bytes(rows)) + compressor.flush()
    return (capture.PNG_HEADER + chunk(b"IHDR", struct.pack(
        ">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


class CaptureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = MODULE.parent.parent / "verification" / "capture-contract-unit"
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        cls.path = Path(cls.temp.name)
        cls.image = png(covered=True)
        cls.state = {
            "id": "00000000-0000-4000-8000-000000000001",
            "generation": "00000000-0000-4000-8000-000000000002",
            "revision": 12,
            "claims": [{
                "id": CLAIM,
                "text": "The authored Production workstation is visible.",
                "source": {"kind": "project_revision", "revision": 12},
                "context": "synthetic-first-party-capture-validation",
                "source_revision": "fixture-12",
            }],
        }
        cls.state_bytes = (json.dumps(cls.state, indent=2) + "\n").encode()
        cls.manifest = {
            "schema": capture.SCHEMA,
            "app": {"id": "motionwright-studio", "version": "0.1.0",
                    "source_revision": REVISION},
            "scenario": {
                "id": "production-original-ui-local-v1",
                "origin": capture.EXPECTED_ORIGIN,
                "route": "/",
                "actions": ["open:/", "click:Production"],
                "viewport": {"width": WIDTH, "height": HEIGHT,
                             "device_scale_factor": 1},
                "browser": "chromium",
                "fixture_kind": "synthetic-first-party-data",
                "auth_mode": "none",
                "external_requests": 0,
                "credential_fields": 0,
            },
            "claim_id": CLAIM,
            "fixture_state_sha256": sha(cls.state_bytes),
            "redactions": [MASK],
            "capture": {
                "path": "capture.png", "sha256": sha(cls.image),
                "repeated_sha256": sha(cls.image), "pixel_equal": True,
                "redacted": True, "raw_unredacted_saved": False,
                "width": WIDTH, "height": HEIGHT,
            },
            "review_status": "technical-evidence-only-human-review-required",
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def fixture(self, name: str, override: dict | None = None,
                image: bytes | None = None) -> Path:
        folder = self.path / name
        folder.mkdir(exist_ok=True)
        (folder / "capture.png").write_bytes(image if image is not None else self.image)
        (folder / "fixture-state.json").write_bytes(self.state_bytes)
        receipt = override if override is not None else self.manifest
        (folder / "capture-evidence.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return folder

    def test_bounded_synthetic_calibrator_passes_without_claiming_external_capture(self) -> None:
        evidence = capture.verify_bundle(self.fixture("good"), expected_commit=REVISION)
        self.assertEqual(evidence["technical_evidence"], "PASS")
        self.assertEqual(evidence["scope"], "first-party-synthetic-fixture-only")
        self.assertEqual(evidence["redaction"], "confirmed-by-decoded-pixels")
        self.assertEqual(evidence["human_review"], "required")

    def test_forged_source_commit_fails(self) -> None:
        with self.assertRaisesRegex(AssertionError, "source revision"):
            capture.verify_bundle(self.fixture("wrong-sha"), expected_commit="a"*40)

    def test_foreign_origin_fails_closed(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["scenario"]["origin"] = "https://some-public-app.example"
        with self.assertRaisesRegex(AssertionError, "authorized loopback"):
            capture.verify_bundle(self.fixture("foreign-url", manifest))

    def test_capture_with_credential_fields_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["scenario"]["credential_fields"] = 1
        with self.assertRaisesRegex(AssertionError, "uncontrolled"):
            capture.verify_bundle(self.fixture("credential", manifest))

    def test_redaction_removed_fails_even_with_recomputed_image_sha(self) -> None:
        unmasked = png(covered=False)
        manifest = copy.deepcopy(self.manifest)
        manifest["capture"]["sha256"] = sha(unmasked)
        manifest["capture"]["repeated_sha256"] = sha(unmasked)
        with self.assertRaisesRegex(AssertionError, "opaque mask pixels"):
            capture.verify_bundle(self.fixture("unmasked", manifest, unmasked))

    def test_wrong_claim_is_not_linked_to_fixture_revision(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["claim_id"] = "00000000-0000-4000-8000-00000000000e"
        with self.assertRaisesRegex(AssertionError, "pinned project"):
            capture.verify_bundle(self.fixture("unlinked-claim", manifest))

    def test_unknown_field_fails_closed(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["capture"]["verified_product_behavior"] = True
        with self.assertRaisesRegex(AssertionError, "schema keys"):
            capture.verify_bundle(self.fixture("forged-proof", manifest))

    def test_false_repeat_or_digest_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["capture"]["repeated_sha256"] = "b"*64
        with self.assertRaisesRegex(AssertionError, "repeatable"):
            capture.verify_bundle(self.fixture("false-repeat", manifest))

    def test_source_state_tampering_fails(self) -> None:
        folder = self.fixture("state-changed")
        (folder / "fixture-state.json").write_text("{}")
        with self.assertRaisesRegex(AssertionError, "fixture data state"):
            capture.verify_bundle(folder)

    def test_mask_bigger_than_allowed_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["redactions"][0]["width"] = 1600
        with self.assertRaisesRegex(AssertionError, "Mask bounds"):
            capture.verify_bundle(self.fixture("blanketed", manifest))

    def test_png_crc_damage_fails(self) -> None:
        damaged = bytearray(self.image)
        damaged[45] ^= 1
        manifest = copy.deepcopy(self.manifest)
        manifest["capture"]["sha256"] = sha(damaged)
        manifest["capture"]["repeated_sha256"] = sha(damaged)
        with self.assertRaisesRegex(AssertionError, "checksum"):
            capture.verify_bundle(self.fixture("bad-crc", manifest, bytes(damaged)))

    def test_missing_original_redaction_field_fails(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["redactions"] = []
        with self.assertRaisesRegex(AssertionError, "redaction"):
            capture.verify_bundle(self.fixture("no-mask", manifest))


if __name__ == "__main__":
    unittest.main()
