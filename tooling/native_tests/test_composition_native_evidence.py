"""Fail-closed negative image tests: never manufacture positive native evidence."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from composition_native_evidence import (
    _image_metric,
    _source,
    _sample,
    inspect_composition_frames,
)

class CompositionNativeEvidenceTests(unittest.TestCase):
    def test_rejects_missing_real_frame_count(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(AssertionError, "exactly 540"):
                inspect_composition_frames([], Path(name), {}, source_sha="a"*40, provider_sha="b"*40)

    def test_rejects_foreign_or_misrepresented_fixture(self):
        with tempfile.TemporaryDirectory() as name:
            source={
                "fixture":"third-party-capture/1",
                "creative_approval":"required",
                "original_ui_not_real_capture":False,
                "data_truth":"synthetic_fixture_not_real_world_metric",
                "frame_count":540,"width":1920,"height":1080,
            }
            with self.assertRaisesRegex(AssertionError, "Unknown source family"):
                _source(Path(name),source)
            source["fixture"]="composition-stack/1"
            with self.assertRaisesRegex(AssertionError, "mislabeled"):
                _source(Path(name),source)

    def test_blank_measurement_fails_even_with_correct_source_label(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/"blank.png"
            Image.new("RGB",(1920,1080),(20,20,20)).save(path)
            nodes=[
                {"id":"a","kind":"text","text":"Synthetic rate · 37.5%",
                 "x":330,"y":220,"width":1240,"height":140},
                {"id":"b","kind":"rectangle","text":None,
                 "x":340,"y":470,"width":300,"height":72},
            ]
            with self.assertRaisesRegex(AssertionError, "numeric bar"):
                _image_metric(path,nodes)

    def test_wrong_bar_source_or_scene_count_never_passes(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/"test.png"
            Image.new("RGB",(1920,1080),(20,20,20)).save(path)
            nodes=[
                {"kind":"text","text":"Proven customer growth","x":330,"y":220,
                 "width":1240,"height":140},
                {"kind":"rectangle","text":None,"x":340,"y":470,"width":300,"height":72},
            ]
            with self.assertRaisesRegex(AssertionError, "diverges from imported"):
                _image_metric(path,nodes)
            with self.assertRaisesRegex(AssertionError, "three source/decoded"):
                _sample([path],[])

if __name__=="__main__":
    unittest.main()
