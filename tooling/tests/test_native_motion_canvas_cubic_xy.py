"""G0 custody and join controls only; fixture bytes do not prove Native pixels."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_motion_canvas_cubic_xy as xy

PROJECT = "11111111-1111-4111-8111-111111111111"
GENERATION = "22222222-2222-4222-8222-222222222222"
SCENE = "33333333-3333-4333-8333-333333333333"
NODE = "44444444-4444-4444-8444-444444444444"


def geometry():
    rows = []
    for frame in xy.FRAMES:
        p = 1 - (1 - frame / 30) ** 3
        left, top = 1410 + 100*p, 620 + 130*p
        center = [left+70, top+45]
        rows.append({"frame":frame, "solid_rgb_pixels":12600,
            "bounding_edges":[left,top,left+140,top+90],
            "observed_center":center, "expected_center":center[:], "axis_error_px":[0.0,0.0]})
    return {"schema":"motionwright.native-out-cubic-pixels/2",
        "native_original_pixels_verified":True, "interpolation":"ease_out_cubic",
        "native_motion_easing":"out_cubic", "position_sampled_frames":list(xy.FRAMES),
        "position_roi_pixels":[1370,580,320,300], "position_authored_start":[1410,620],
        "position_authored_end":[1510,750], "position_solid_rgb":[119,198,250],
        "position_rgb_tolerance":2, "position_axis_tolerance_px":1.5,
        "position_pixel_center_convention":"column+0.5,row+0.5",
        "position_x_verified":True, "position_y_verified":True,
        "position_geometry":rows, "opacity_sampled_frames":[0,15,30],
        "opacity_full_mean_rgb_distance":40.0, "opacity_half_to_final_mean_rgb_distance":5.0,
        "pixel_difference_interpreted_as_visual_approval":False}


def source_tile():
    return {"id":NODE, "name":"Native exact cubic-out position tile", "kind":"rectangle",
        "parent_id":None, "coordinate_space":"project_pixels",
        "x":1510.0, "y":750.0, "width":140.0, "height":90.0, "rotation_deg":0.0,
        "opacity":1.0, "style":{"fill":"#77C6FA","stroke":None,"stroke_width":0.0},
        "keyframes":[{"at":{"num":str(second),"den":"1"}, "property":prop, "value":float(value),
                      "interpolation":ease} for second, prop, value, ease in
                     [(0,"x",1410,"linear"), (0,"y",620,"linear"),
                      (1,"x",1510,"ease_out_cubic"), (1,"y",750,"ease_out_cubic")]]}


class OriginalJoinControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mc-xy-custody-g0-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.owner, self.evidence = root/"owner", root/"evidence"
        self.owner.mkdir()
        self.evidence.mkdir()
        self.context = {"source_sha":"a"*40, "semwright_sha":"b"*40,
                        "run_id":"123456", "run_attempt":"1"}
        self.source = {"motionwright_sha":"a"*40, "sdk_revision":"b"*40,
                       "locks":{}, "locks_sha256":"c"*64}
        self.jobs = {"mediated":"mc1:g0-only:render_job:render-"+"1"*32,
                     "direct":"mc1:g0-only:render_job:render-"+"2"*32}
        self.terminal = {"pid":2345, "exit_code":-15, "group_quiescent":True}
        self.closure = {"host_identity":{"pid":2345,"start_ticks":789},
            "final_inventory_verified":True, "broker_group_closed":True,
            "observed_descendants_closed":True, "automatic_retry":False}
        self.identity = {"project_id":PROJECT,"generation":GENERATION,"revision":7,
            "scene_id":SCENE,"frame_count":60,"width":1920,"height":1080,"frame_rate":"30/1"}
        self.write("identity", self.identity)
        self.write("project", {"id":PROJECT,"generation":GENERATION,"revision":7,
                              "scenes":[{"id":SCENE,"nodes":[source_tile()]}]})
        film = {"g0_only_binding":True}
        self.write("film",film)
        self.write("exported_film",film)
        digests = {key:value*64 for key,value in
                   (("intent_digest","d"),("realization_digest","e"),("projection_digest","f"))}
        self.write("inspection",{"project":{"authoring":{"intent":film,**digests}}})
        self.write("binding",{"binding_derivation":"VERIFIED_BY_SELECTED_SDK",
            "native_execution":False, "film_sha256":self.file_sha("film"),
            "inspection_sha256":self.file_sha("inspection"), **digests})
        self.artifacts = {}
        manifests = {}
        for leg in ("mediated","direct"):
            folder = self.owner/"output"/leg
            (folder/"frames").mkdir(parents=True)
            frames = []
            for index in range(60):
                # Deliberately not decodable: this metadata-only reader must not decode.
                raw = b"\x89PNG\r\n\x1a\nG0-CUSTODY-ONLY-" + bytes([index])
                path = folder/"frames"/f"{index:06d}.png"
                path.write_bytes(raw)
                frames.append({"index":index,"file":f"frames/{index:06d}.png",
                               "bytes":len(raw),"sha256":xy.sha(raw)})
            manifest = {"plan":{"first_frame":0,"end_frame_exclusive":60,"frame_count":60},
                        "frames":frames}
            raw = self.json_bytes(manifest)
            (folder/"artifact-manifest.json").write_bytes(raw)
            self.artifacts[leg] = folder
            manifests[leg] = xy.sha(raw)
        self.write("result", {"motionwright_sha":"a"*40,"semwright_sha":"b"*40,
            "native_render_e2e":"PASS","project":self.identity,"frame_count":60,
            "direct_mediated_native_frame_parity":"PASS",
            "direct_mediated_same_film_sha256":self.file_sha("exported_film"),
            "artifact_manifest_sha256":manifests["mediated"],"artifact_directory":"mediated",
            "native_cubic_out_region":geometry()})
        self.write("parity",{"motionwright_sha":"a"*40,"semwright_sha":"b"*40,
            "project_generation":GENERATION,"project_revision":7,
            "direct_job_distinct_from_mediated":True,
            "source_film_sha256":self.file_sha("exported_film"),
            "native_manifest_sha256":manifests,
            "pixels":{"frame_count":60,"all_frames_decoded_rgba_pixel_identical":True,
                "raw_rgba_pixel_format":"rgba_1920x1080",
                "ordered_rgba_frame_digests_sha256":"0"*64}})
        self.witness = xy.capture(self.owner,self.evidence, context=self.context,source=self.source,
                                  jobs=self.jobs,host_pid=2345,artifacts=self.artifacts)

    @staticmethod
    def json_bytes(value):
        return (json.dumps(value,sort_keys=True)+"\n").encode()

    def write(self, role, value):
        path = self.evidence/xy.FILES[role]
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(self.json_bytes(value))

    def file_sha(self, role):
        return xy.sha((self.evidence/xy.FILES[role]).read_bytes())

    def mutate_document(self, role, change):
        path = self.evidence/xy.FILES[role]
        value = json.loads(path.read_bytes())
        change(value)
        self.write(role,value)
        self.witness["files"][role] = xy.capture_reference(self.evidence,"evidence",path)

    def inspect(self, **changes):
        options = {"context":self.context,"source":self.source,
            "jobs":list(self.jobs.values()),"hosts":[2345],"completed":["original"],
            "closure":self.closure,"terminal":self.terminal}
        options.update(changes)
        return xy.inspect(self.owner,self.evidence,self.witness,**options)

    def test_complete_join_reads_all_120_originals_without_process_or_decode(self):
        with mock.patch("subprocess.Popen",side_effect=AssertionError("no process")):
            report = self.inspect()
        self.assertEqual(report["verified_original_pngs"],120)
        self.assertEqual(report["status"],"VERIFIED_AFTER_HOST_CLOSURE")
        self.assertFalse(report["additional_media_decode_performed"])
        self.assertFalse(report["native_execution_performed"])
        self.assertFalse(report["formal_acceptance"])
        self.assertFalse(report["source_edit_or_reopen_proven_by_this_scope"])
        self.assertEqual(set(report["sampled_frame_sha256"]["mediated"]),{"0","2","8","30"})

    def test_last_unsampled_frame_change_is_rejected(self):
        target = self.artifacts["direct"]/"frames/000059.png"
        raw = target.read_bytes()
        target.write_bytes(raw[:-1]+b"x")
        with self.assertRaisesRegex(xy.CubicXYRefused,"original_changed_after_capture"):
            self.inspect()

    def test_source_and_original_job_identity_cannot_be_rebound(self):
        for changed in ({"source":{**self.source,"sdk_revision":"c"*40}},
                        {"context":{**self.context,"run_id":"999"}},
                        {"jobs":list(reversed(list(self.jobs.values())))},
                        {"jobs":[self.jobs["direct"]]*2},
                        {"completed":["original","reopened"]},
                        {"hosts":[2345,3456]}):
            with self.subTest(changed=changed), self.assertRaises(xy.CubicXYRefused):
                self.inspect(**changed)

    def test_incomplete_inventory_or_process_closure_never_passes(self):
        for key in ("final_inventory_verified","broker_group_closed","observed_descendants_closed"):
            with self.subTest(key=key), self.assertRaisesRegex(xy.CubicXYRefused,"host_closure"):
                self.inspect(closure={**self.closure,key:False})
        for terminal in ({**self.terminal,"exit_code":-9}, {**self.terminal,"exit_code":True},
                         {**self.terminal,"group_quiescent":False},
                         {**self.terminal,"pid":3456}):
            with self.subTest(terminal=terminal), self.assertRaisesRegex(xy.CubicXYRefused,"host_terminal"):
                self.inspect(terminal=terminal)

    def test_binding_receipt_must_hash_the_same_original_film(self):
        self.mutate_document("binding",lambda v:v.update(film_sha256="0"*64))
        with self.assertRaisesRegex(xy.CubicXYRefused,"sdk_binding_not_original"):
            self.inspect()

    def test_manifests_and_frame_paths_cannot_select_another_file(self):
        ref = self.witness["artifacts"]["direct"]["frames"][8]
        for path in ("../outside", self.witness["artifacts"]["direct"]["frames"][9]["path"]):
            old = ref["path"]
            ref["path"] = path
            with self.subTest(path=path), self.assertRaisesRegex(xy.CubicXYRefused,"manifest_frame_binding"):
                self.inspect()
            ref["path"] = old

    def test_geometry_flag_alone_cannot_hide_frozen_y(self):
        def alter(value):
            row = value["native_cubic_out_region"]["position_geometry"][2]
            row["observed_center"][1] = 665.0
        self.mutate_document("result",alter)
        with self.assertRaisesRegex(xy.CubicXYRefused,"geometry_independent_axis_bounds"):
            self.inspect()

    def test_historical_constant_y_source_cannot_gain_xy_credit(self):
        self.mutate_document("project", lambda v:
            v["scenes"][0]["nodes"][0]["keyframes"][1].update(value=750.0))
        with self.assertRaisesRegex(xy.CubicXYRefused,"source_cubic_keyframe_changed"):
            self.inspect()

    def test_rational_time_requires_selected_sdk_canonical_string_wire(self):
        self.mutate_document("project", lambda v:
            v["scenes"][0]["nodes"][0]["keyframes"][0].update(at={"num":0,"den":1}))
        with self.assertRaisesRegex(xy.CubicXYRefused,"source_cubic_keyframe_changed"):
            self.inspect()

    def test_project_generation_or_revision_mismatch_is_rejected(self):
        self.mutate_document("result",lambda v:v["project"].update(revision=8))
        with self.assertRaisesRegex(xy.CubicXYRefused,"project_identity_changed"):
            self.inspect()

    def test_formal_or_human_credit_is_always_rejected(self):
        for key in ("formal_acceptance","human_visual_acceptance","automatic_retry"):
            self.witness[key] = True
            with self.subTest(key=key), self.assertRaisesRegex(xy.CubicXYRefused,"acceptance_not_available"):
                self.inspect()
            self.witness[key] = False


if __name__ == "__main__":
    unittest.main()
