"""Synthetic negative tests for the spatial gate; not native-render or design evidence."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("creative_frame_evidence", Path(__file__).resolve().parents[1] / "creative_frame_evidence.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class NativeLayoutGateTests(unittest.TestCase):
    def fixture(self, root: Path):
        project = {"project_id":"synthetic-project", "generation":"synthetic-generation", "revision":7}
        nodes = []
        image = Image.new("RGB", (640,360), "#0F1216")
        draw = ImageDraw.Draw(image)
        for index in range(6):
            x, y = 40 + (index%3)*190, 40 + (index//3)*160
            color = "#F2F4F3" if index%2 == 0 else "#A5C8DF"
            nodes.append({"id":f"synthetic-{index}","name":f"Synthetic node {index}","x":x,"y":y,"width":130,"height":70,"style":{"fill":color}})
            draw.rectangle((x+12,y+15,x+100,y+44), fill=color)
        expected = {"schema":"motionwright.component-layout-regions/1",**project,"width":640,"height":360,"nodes":nodes}
        (root/"expected-component-layout.json").write_text(json.dumps(expected),encoding="utf-8")
        return image, project, nodes

    def test_known_spatial_fixture_passes(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);image,project,_=self.fixture(root)
            result=module.inspect_layout_regions(image,root,project)
            self.assertEqual(result["spatial_regression"],"PASS")
            self.assertEqual(result["creative_approval"],"required")
            self.assertTrue(all(item["foreground_pixels"]>0 for item in result["nodes"]))

    def test_relocated_foreground_cannot_be_certified_by_a_valid_file_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);image,project,_=self.fixture(root)
            moved=Image.new("RGB",image.size,"#0F1216")
            moved.paste(image,(75,85))
            with self.assertRaisesRegex(AssertionError,"authored component regions"):
                module.inspect_layout_regions(moved,root,project)
            report=json.loads((root/"native-layout-inspection.json").read_text())
            self.assertEqual(report["spatial_regression"],"FAIL")
            self.assertEqual(report["creative_approval"],"required")

    def test_missing_wordmark_and_extraneous_pixels_fail(self):
        for mode in ["missing","extra"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                root=Path(raw);image,project,nodes=self.fixture(root)
                draw=ImageDraw.Draw(image)
                if mode=="missing":
                    node=nodes[5];draw.rectangle((node["x"]-1,node["y"]-1,node["x"]+131,node["y"]+71),fill="#0F1216")
                else:
                    draw.rectangle((0,0,639,25),fill="#A5C8DF")
                with self.assertRaises(AssertionError):module.inspect_layout_regions(image,root,project)

    def test_stale_source_and_wrong_dimensions_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);image,project,_=self.fixture(root)
            with self.assertRaisesRegex(AssertionError,"revision mismatch"):
                module.inspect_layout_regions(image,root,{**project,"revision":8})
            with self.assertRaisesRegex(AssertionError,"size mismatch"):
                module.inspect_layout_regions(image.resize((320,180)),root,project)

if __name__ == "__main__":unittest.main()
