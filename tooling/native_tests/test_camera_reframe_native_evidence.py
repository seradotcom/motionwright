#!/usr/bin/env python3
"""Adversarial pixel-gate tests. Synthetic calibrators are NEVER native evidence."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

SCRIPT=Path(__file__).resolve().parents[1]/"camera_reframe_native_evidence.py"
SPEC=importlib.util.spec_from_file_location("camera_reframe_native_evidence",SCRIPT)
assert SPEC is not None and SPEC.loader is not None
oracle=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


class NativeCameraPixelGateTests(unittest.TestCase):
    def setUp(self):
        root=SCRIPT.parents[1]/"verification"/"native-camera-negative-tests"
        root.mkdir(parents=True,exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)

    def raster(self,name:str,*,shift:int=0, blank:bool=False,width:int=1920)->Path:
        canvas=Image.new("RGB",(width,1080),"#0B1520")
        if not blank:
            draw=ImageDraw.Draw(canvas)
            # Distinct edge-dense original graphics. They are deliberately
            # NOT Semwright output and cannot qualify as positive proof.
            for x in range(370,1450,37):
                for y in range(260,790,42):
                    draw.rectangle((x+shift,y,x+shift+16,y+19),fill="#92CBD9")
            draw.rectangle((460+shift,400,690+shift,505),outline="#FAE4B1",width=3)
        path=self.path/name
        canvas.save(path)
        return path

    def test_synthetic_calibrator_matches_only_exact_translation(self):
        first=self.raster("first.png",shift=18)
        last=self.raster("last.png")
        report=oracle.probe_pan(first,last,18,0,codec_tolerance=3)
        self.assertGreater(report["tested_edge_samples"],140)
        self.assertGreater(report["authored_motion_alignment"],0.85)
        self.assertGreater(report["authored_motion_alignment"],report["static_alignment"]+0.09)

    def test_stationary_frames_refused(self):
        same=self.raster("same.png")
        with self.assertRaisesRegex(AssertionError,"did not realize|do not distinguish"):
            oracle.probe_pan(same,same,18,0,codec_tolerance=3)

    def test_wrong_direction_refused(self):
        first=self.raster("wrong.png",shift=-18)
        end=self.raster("end.png")
        with self.assertRaisesRegex(AssertionError,"did not realize"):
            oracle.probe_pan(first,end,18,0,codec_tolerance=3)

    def test_blank_source_refused(self):
        first=self.raster("blank-a.png",blank=True)
        last=self.raster("blank-b.png",blank=True)
        with self.assertRaisesRegex(AssertionError,"enough visible detailed pixels"):
            oracle.probe_pan(first,last,18,0,codec_tolerance=3)

    def test_wrong_viewport_refused(self):
        first=self.raster("small.png",width=960)
        last=self.raster("normal.png")
        with self.assertRaisesRegex(AssertionError,"dimensions"):
            oracle.probe_pan(first,last,18,0,codec_tolerance=3)


if __name__=="__main__":
    unittest.main()
