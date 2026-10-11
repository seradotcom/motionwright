#!/usr/bin/env python3
"""Negative pixel-oracle calibration; these generated frames are NOT native proof."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

MODULE=Path(__file__).resolve().parents[1]/"product_stage_native_evidence.py"
SPEC=importlib.util.spec_from_file_location("product_stage_native_evidence",MODULE)
assert SPEC is not None and SPEC.loader is not None
oracle=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)

def rect(x,y,w,h,name):
    return {"x":x,"y":y,"width":w,"height":h,"name":"ProductStage / "+name}

class OriginalStagePixelOracleNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="stage-test-nonnative-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.nodes=[
            rect(288,151,1344,778,"frame"),
            rect(335,229,1250,665,"screen"),
            rect(335,229,1250,42,"chrome"),
            rect(380,241,19,18,"chrome_indicator"),
            rect(435,322,1050,73,"screen_heading"),
            rect(435,408,1050,60,"screen_detail"),
            rect(435,501,1050,179,"card"),
            rect(479,538,287,77,"card_highlight"),
            rect(435,741,450,66,"action_button"),
            rect(460,754,350,36,"action_text"),
            rect(966,633,18,18,"focus_marker"),
            rect(1016,630,150,26,"focus_caption"),
            rect(435,834,1025,27,"screen_caption"),
            rect(211,978,1497,24,"disclosure"),
        ]
    def sample(self,name,*,heading=True,disclosure=True,focus=True,highlight=True,
               early=False,frame=True,dim=(1920,1080)):
        img=Image.new("RGB",(1920,1080),"#0F1216")
        draw=ImageDraw.Draw(img)
        if frame:draw.rectangle((288,151,1631,928),fill="#223542")
        draw.rectangle((335,229,1584,893),fill="#132431")
        draw.rectangle((435,501,1484,679),fill="#29404B")
        if not early:
            if highlight:draw.rectangle((479,538,765,614),fill="#86C8D1")
            if focus:draw.ellipse((966,633,983,650),fill="#F7C87B")
        if early and highlight:draw.rectangle((479,538,765,614),fill="#86C8D1")
        if heading:draw.rectangle((435,344,880,350),fill="#F2F4F3")
        if disclosure:draw.rectangle((211,990,1000,997),fill="#F2F4F3")
        if dim!=(1920,1080):img=img.resize(dim)
        target=self.root/name
        img.save(target,optimize=True)
        return target
    def test_calibrated_synthetic_frame_is_not_real_native_positive_evidence(self):
        late=self.sample("calibration-late.png",early=False)
        proof=oracle.inspect_stage_frame(late,self.nodes,expect_settled=True)
        self.assertGreater(proof["heading_nonblank_samples"],20)
        self.assertGreater(proof["disclosure_nonblank_samples"],25)
        self.assertGreater(proof["focus_rgb"][0],200)
        # This is only a calibrator. The CI positive path requires actual 180
        # original frames and an imported app CAS state/provenance digest.
    def test_blank_heading_is_detected(self):
        with self.assertRaisesRegex(AssertionError,"heading"):
            oracle.inspect_stage_frame(
                self.sample("blank-heading.png",heading=False,early=False),
                self.nodes,expect_settled=True)
    def test_missing_anti_capture_disclosure_is_detected(self):
        with self.assertRaisesRegex(AssertionError,"disclosure"):
            oracle.inspect_stage_frame(
                self.sample("missing-disclosure.png",disclosure=False,early=False),
                self.nodes,expect_settled=True)
    def test_missing_focus_marker_is_detected(self):
        with self.assertRaisesRegex(AssertionError,"focus marker"):
            oracle.inspect_stage_frame(
                self.sample("missing-focus.png",focus=False,early=False),
                self.nodes,expect_settled=True)
    def test_missing_card_highlight_is_detected(self):
        with self.assertRaisesRegex(AssertionError,"focus highlight"):
            oracle.inspect_stage_frame(
                self.sample("missing-highlight.png",highlight=False,early=False),
                self.nodes,expect_settled=True)
    def test_focus_highlight_visible_too_early_is_detected(self):
        with self.assertRaisesRegex(AssertionError,"started fully illuminated"):
            oracle.inspect_stage_frame(
                self.sample("early-highlight.png",highlight=True,early=True),
                self.nodes,expect_settled=False)
    def test_wrong_native_output_dimensions_rejected(self):
        with self.assertRaisesRegex(AssertionError,"wrong native dimensions"):
            oracle.inspect_stage_frame(
                self.sample("wrong-size.png",dim=(900,540)),self.nodes,expect_settled=True)
    def test_missing_frame_rejected(self):
        with self.assertRaisesRegex(AssertionError,"frame is missing"):
            oracle.inspect_stage_frame(
                self.sample("frame-missing.png",frame=False),self.nodes,expect_settled=True)
if __name__=="__main__":
    unittest.main()
