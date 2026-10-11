#!/usr/bin/env python3
"""Negative visual evidence checks; synthetic fixtures must never count as native proof."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw

MODULE = Path(__file__).resolve().parents[1] / "procedural_native_evidence.py"
SPEC = importlib.util.spec_from_file_location("procedural_native_evidence", MODULE)
assert SPEC is not None and SPEC.loader is not None
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


class ProceduralNativeEvidenceNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.nodes = [
            {"x": 100 + 130 * index, "y": 100, "width": 70, "height": 70}
            for index in range(6)
        ]

    def image(self, name: str, count: int) -> Path:
        frame = Image.new("RGB", (960, 540), "#151515")
        draw = ImageDraw.Draw(frame)
        for node in self.nodes[:count]:
            draw.rectangle(
                (node["x"], node["y"],
                 node["x"] + node["width"], node["y"] + node["height"]),
                fill="#A5C8DF",
            )
        path = self.directory / name
        frame.save(path)
        return path

    def sample(self, name: str, count: int) -> dict:
        return inspector.visible_centers(
            self.image(name, count), self.nodes, (960, 540)
        )

    def test_blank_or_missing_final_objects_never_pass(self):
        first = self.sample("blank.png", 0)
        second = self.sample("middle.png", 2)
        last = self.sample("last.png", 3)
        with self.assertRaisesRegex(AssertionError, "misses authored objects"):
            inspector.verify_temporal_samples(
                first, second, last, count=6, animated=True
            )

    def test_unchanged_frames_cannot_pass_animated_proof(self):
        frame = self.sample("unchanged.png", 6)
        with self.assertRaisesRegex(AssertionError, "visibility progression"):
            inspector.verify_temporal_samples(
                frame, frame, frame, count=6, animated=True
            )

    def test_static_must_contain_all_objects_at_frame_zero(self):
        blank = self.sample("empty.png", 0)
        full = self.sample("full.png", 6)
        with self.assertRaisesRegex(AssertionError, "starts invisible"):
            inspector.verify_temporal_samples(
                blank, full, full, count=6, animated=False
            )

    def test_valid_technical_cases_are_distinct_from_creative_review(self):
        blank = self.sample("empty.png", 0)
        full = self.sample("full.png", 6)
        inspector.verify_temporal_samples(
            blank, full, full, count=6, animated=True
        )
        inspector.verify_temporal_samples(
            full, full, full, count=6, animated=False
        )
        with self.assertRaisesRegex(AssertionError, "wrong size"):
            inspector.visible_centers(self.directory / "full.png", self.nodes, (1920, 1080))


if __name__ == "__main__":
    unittest.main()
