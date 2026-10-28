"""Structural GLB readback regressions; no native process or acceptance claim."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "native_blender_e2e.py"
SPEC = importlib.util.spec_from_file_location("native_blender_e2e_framing_test", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def model():
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [{"mesh": 0, "name": "Motionwright-first"}, {"mesh": 1, "name": "second"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": n}]}
                   for n in range(2)],
        "materials": [{}, {}],
        "buffers": [{"byteLength": 42}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36},
                        {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
                       "min": [0, 0, 0], "max": [1, 1, 0]},
                      {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}],
    }


def binary():
    return struct.pack("<9f3H", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2) + b"\0\0"


def chunk(kind, body):
    return struct.pack("<I4s", len(body), kind) + body


def pack(document=None, payload=None, *, extra=b"", json_bytes=None):
    document = model() if document is None else document
    raw = (json.dumps(document, separators=(",", ":")).encode("utf-8")
           if json_bytes is None else json_bytes)
    raw += b" " * (-len(raw) % 4)
    payload = binary() if payload is None else payload
    body = chunk(b"JSON", raw) + chunk(b"BIN\0", payload) + extra
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class GlbFramingTests(unittest.TestCase):
    def parse(self, data):
        with tempfile.TemporaryDirectory(prefix="blender-framing-") as directory:
            path = Path(directory) / "fixture.glb"
            path.write_bytes(data)
            return MODULE.parse_glb(path)

    def assert_rejected(self, data):
        with self.assertRaises(AssertionError):
            self.parse(data)

    def test_valid_embedded_triangles_preserve_document(self):
        original = model()
        self.assertEqual(self.parse(pack(original)), original)

    def test_original_json_only_false_positive_is_rejected(self):
        original = {"asset": {"version": "2.0"},
                    "nodes": [{"name": "Motionwright-no-geometry"}, {"name": "empty"}],
                    "meshes": [{}, {}], "materials": [{}, {}]}
        raw = json.dumps(original, separators=(",", ":")).encode()
        raw += b" " * (-len(raw) % 4)
        body = chunk(b"JSON", raw)
        self.assert_rejected(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)

    def test_empty_bin_is_rejected(self):
        self.assert_rejected(pack(payload=b""))

    def test_missing_or_external_buffer_is_rejected(self):
        for change in ("missing", "uri", "multiple"):
            with self.subTest(change=change):
                value = model()
                if change == "missing":
                    value.pop("buffers")
                elif change == "uri":
                    value["buffers"][0]["uri"] = "file:///not-an-admitted-input"
                else:
                    value["buffers"].append(copy.deepcopy(value["buffers"][0]))
                self.assert_rejected(pack(value))

    def test_buffer_length_and_boolean_are_rejected(self):
        for length in (True, False, -1, 0, 40, 45, 1000000, "42"):
            with self.subTest(length=length):
                value = model()
                value["buffers"][0]["byteLength"] = length
                self.assert_rejected(pack(value))

    def test_nonzero_bin_padding_is_rejected(self):
        self.assert_rejected(pack(payload=binary()[:-1] + b"x"))

    def test_truncated_header_payload_and_extra_bytes_are_rejected(self):
        good = pack()
        for changed in (good[:8], good[:-1], good + b"x"):
            with self.subTest(length=len(changed)):
                self.assert_rejected(changed)

    def test_duplicate_or_unknown_chunks_are_rejected(self):
        for kind in (b"BIN\0", b"TEST"):
            with self.subTest(kind=kind):
                self.assert_rejected(pack(extra=chunk(kind, b"\0" * 4)))

    def test_unaligned_chunk_is_rejected(self):
        self.assert_rejected(pack(payload=binary()[:-1]))

    def test_nonfinite_duplicate_and_nonobject_json_are_rejected(self):
        good = json.dumps(model(), separators=(",", ":")).encode()
        for raw in (good[:-1] + b',"unused":NaN}',
                    good[:-1] + b',"asset":{"version":"2.0"}}',
                    b"[]", b"null", b"true"):
            with self.subTest(raw=raw[-25:]):
                self.assert_rejected(pack(json_bytes=raw))

    def test_invalid_section_members_are_rejected(self):
        for key in ("nodes", "meshes", "materials"):
            with self.subTest(key=key):
                value = model()
                value[key] = [None, {}]
                self.assert_rejected(pack(value))

    def test_oversize_input_is_rejected(self):
        self.assert_rejected(b"x" * (8 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
