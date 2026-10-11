#!/usr/bin/env python3
"""Negative-only pixel oracle tests; synthetic frames never become real proof."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw

MODULE=Path(__file__).resolve().parents[1] / "measured_native_evidence.py"
SPEC=importlib.util.spec_from_file_location("measured_native_evidence", MODULE)
assert SPEC is not None and SPEC.loader is not None
oracle=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


class MeasuredNativePixelOracleNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="measured-negative-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def sample(self, name:str, *, bar_width:int, label:bool=True) -> Path:
        frame=Image.new("RGB", (1920,1080), "#0C1723")
        drawing=ImageDraw.Draw(frame)
        if bar_width:
            drawing.rectangle((340,480,340+bar_width-1,551),fill="#A5C8DF")
        if label:
            drawing.rectangle((360,270,630,295),fill="#F2F4F3")
        file=self.root / name
        frame.save(file)
        return file

    def test_missing_bar_rejected(self):
        with self.assertRaisesRegex(AssertionError,"not visible"):
            oracle.inspect_measured_frame(self.sample("absent.png",bar_width=0),x=340,y=480,width=300)

    def test_overwidth_bar_rejected(self):
        with self.assertRaisesRegex(AssertionError,"exceeds"):
            oracle.inspect_measured_frame(self.sample("oversize.png",bar_width=700),x=340,y=480,width=300)

    def test_missing_text_rejected(self):
        with self.assertRaisesRegex(AssertionError,"label"):
            oracle.inspect_measured_frame(self.sample("unlabeled.png",bar_width=300,label=False),x=340,y=480,width=300)

    def test_tampered_dimension_rejected(self):
        file=self.sample("correct.png",bar_width=300)
        with Image.open(file) as img:
            img.crop((0,0,900,540)).save(file)
        with self.assertRaisesRegex(AssertionError,"dimensions"):
            oracle.inspect_measured_frame(file,x=340,y=480,width=300)

    def test_expected_technical_samples_do_not_assert_source_truth(self):
        file=self.sample("expected.png",bar_width=300)
        report=oracle.inspect_measured_frame(file,x=340,y=480,width=300)
        self.assertGreater(report["bar_inside_delta"],36)
        self.assertLess(report["bar_outside_delta"],36)
        self.assertGreater(report["label_nonblank_samples"],25)


if __name__=="__main__":
    unittest.main()
