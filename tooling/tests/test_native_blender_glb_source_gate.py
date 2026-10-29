"""Finite source/readback controls. All geometry and process responses are synthetic.

The main-path tests execute the real Python gate and strict GLB readers, while
native process entrypoints are replaced by inert unit fixtures. No rendering,
SDK operation, persisted user project or native acceptance is represented.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_e2e as wrapper
from native_blender_glb_content import BlenderContentError, canvas_source_nodes, semantic_name
if __package__:
    from .test_native_blender_glb_content import SOURCE, blob, check, make_case, rewrite_indices
else:
    from test_native_blender_glb_content import SOURCE, blob, check, make_case, rewrite_indices


def source_pair():
    nodes=[]
    for row in SOURCE:
        nodes.append({
            "id":str(uuid.UUID(row["node_id"])),"name":row["source_name"],
            "kind":row["kind"],"parent_id":None,
            **{key:row[key] for key in ("x","y","width","height","z_index")},
            "rotation_deg":0.0,"opacity":1.0,"text":None,
            "coordinate_space":"project_pixels","relations":[],"property_locks":[],"keyframes":[],
            "style":{"fill":row["fill_hex"],"stroke":None,"stroke_width":0.0,
                     "font_family":None,"font_size":None,"font_weight":None,"line_height":None,
                     "blend_mode":"normal"},
        })
    seeded={"project_id":"11111111-1111-4111-8111-111111111111",
            "resource":"synthetic-unit-project-only", "generation":"22222222-2222-4222-8222-222222222222",
            "revision":4,"scene_id":"33333333-3333-4333-8333-333333333333",
            "mesh_count":2,"source_nodes":nodes}
    evidence={key:copy.deepcopy(value) for key,value in seeded.items() if key!="mesh_count"}
    evidence.update(native_blender_e2e="PASS",result={"objects":[
        {"node_id":row["id"]} for row in nodes]})
    return seeded,evidence


class StaticGlbSourceGateTests(unittest.TestCase):
    def test_raw_saved_canvas_fields_drive_independent_geometry(self):
        seeded,evidence=source_pair()
        source=wrapper.static_glb_source_nodes(seeded,evidence)
        self.assertEqual(source[0]["x"],seeded["source_nodes"][0]["x"])
        self.assertEqual(source[0]["fill_hex"],seeded["source_nodes"][0]["style"]["fill"])
        self.assertEqual(source[0]["node_id"],seeded["source_nodes"][0]["id"])
        gltf,binary=make_case()
        result=check(gltf,binary,source)
        self.assertTrue(result["source_Project_UUIDs_explicitly_checked"])
        self.assertTrue(result["indexed_surface_coverage_verified"])
        self.assertFalse(result["authored_UV_mapping_verified"])

    def test_seed_realize_identity_and_revision_must_all_match(self):
        for key in ("project_id","resource","generation","revision","scene_id"):
            with self.subTest(key=key):
                seeded,evidence=source_pair()
                evidence[key]=evidence[key]+1 if key=="revision" else "another-original"
                with self.assertRaisesRegex(AssertionError,"source identity changed"):
                    wrapper.static_glb_source_nodes(seeded,evidence)

    def test_unchanged_revision_does_not_excuse_changed_canvas_body(self):
        for key in ("x","width","style","id"):
            with self.subTest(key=key):
                seeded,evidence=source_pair()
                row=evidence["source_nodes"][0]
                if key=="style":row[key]["fill"]="#000000"
                elif key=="id":row[key]="abcdef01-2345-4000-8000-ffffffffffff"
                else:row[key]+=1
                with self.assertRaisesRegex(AssertionError,"CanvasNode body changed"):
                    wrapper.static_glb_source_nodes(seeded,evidence)

    def test_raw_authoring_outside_the_static_profile_is_not_flattened(self):
        mutations=[lambda n:n.__setitem__("rotation_deg",15),
                   lambda n:n.__setitem__("opacity",.5),
                   lambda n:n["style"].__setitem__("blend_mode","multiply"),
                   lambda n:n["style"].__setitem__("stroke_width",2),
                   lambda n:n.__setitem__("relations",[{}]),
                   lambda n:n.__setitem__("coordinate_space","normalized")]
        for mutate in mutations:
            seeded,_=source_pair()
            mutate(seeded["source_nodes"][0])
            with self.assertRaisesRegex(BlenderContentError,"outside_static_GLB_profile"):
                canvas_source_nodes(seeded["source_nodes"])

    def test_realized_objects_cannot_substitute_another_full_uuid(self):
        seeded,evidence=source_pair()
        evidence["result"]["objects"][0]["node_id"]="abcdef01-2345-4000-8000-ffffffffffff"
        with self.assertRaisesRegex(AssertionError,"realized nodes differ"):
            wrapper.static_glb_source_nodes(seeded,evidence)

    def test_full_uuid_names_remain_bounded_when_material_slug_is_shorter(self):
        gltf,binary=make_case()
        source=copy.deepcopy(SOURCE)
        source[0]["source_name"]="Motionwright focus export with an unusually long semantic label"
        full=source[0]["node_id"]
        gltf["nodes"][0]["name"]=semantic_name(source[0]["source_name"],full)
        gltf["materials"][0]["name"]=semantic_name(source[0]["source_name"],full,"material")
        self.assertLessEqual(len(gltf["nodes"][0]["name"]),63)
        self.assertLessEqual(len(gltf["materials"][0]["name"]),63)
        self.assertNotEqual(gltf["materials"][0]["name"],gltf["nodes"][0]["name"]+"-material")
        self.assertTrue(check(gltf,binary,source)["source_Project_UUIDs_explicitly_checked"])

    def run_main_fixture(self,root,*,bad_geometry,context_finish_error=None):
        seeded,evidence=source_pair()
        gltf,binary=make_case()
        if bad_geometry:
            binary=rewrite_indices(gltf,binary,0,[0,2,1,0,2,1])
        encoded=blob(gltf,binary)
        wanted=hashlib.sha256(encoded).hexdigest()
        glb_name="motionwright-blender-scene-"+seeded["scene_id"].replace("-","")+".glb"
        evidence["result"]["artifact"]={"path":glb_name,"sha256":wanted,"bytes":len(encoded)}
        evidence_dir=root/"evidence"
        tools=root/"inert-tools"
        tools.mkdir()
        for name in ("blender","semwright-blender-session-runner"):
            (tools/name).write_bytes(b"inert unit fixture; never executed")

        def paths(directory):
            output={name:directory/name for name in ("workspace","bin","motionwright-data")}
            for path in output.values():path.mkdir()
            (output["workspace"]/glb_name).write_bytes(encoded)
            (output["motionwright-data"]/"motionwright.sqlite3").write_bytes(b"original synthetic database bytes")
            (output["motionwright-data"]/"motionwright.sqlite3-wal").write_bytes(b"original synthetic WAL bytes")
            (output["bin"]/"semwright-blender-driver").write_bytes(b"inert unit driver")
            return output

        def inert_response(argv,*,env,timeout=60,static_capture=None):
            if argv[1]=="seed":
                response=copy.deepcopy(seeded)
            else:
                self.assertEqual(argv[1],"realize")
                Path(argv[-1]).write_text(json.dumps(evidence)+"\n")
                response=copy.deepcopy(evidence)
            if static_capture is not None:
                wrapper.static_terminal_originals(*static_capture,subprocess.CompletedProcess(
                    argv,0,json.dumps(response).encode(),b""))
            return response

        @contextmanager
        def inert_context(paths,env,destination,source,*,bounded_static=False):
            self.assertEqual(source,seeded)
            self.assertTrue(bounded_static)
            yield {"connection":root/"unused-connection.json","capabilities_checked":set()}
            if context_finish_error is not None:
                raise context_finish_error

        owner=root/"owner"
        owner.mkdir()
        with patch.object(wrapper,"EVIDENCE",evidence_dir), \
             patch.object(wrapper.tempfile,"mkdtemp",return_value=str(owner)), \
             patch.object(wrapper,"BLENDER_ROOT",tools), patch.object(wrapper,"BLENDER_BIN",tools/"blender"), \
             patch.object(wrapper,"SW_BINS",tools), \
             patch.object(wrapper,"static_original_budget"), \
             patch.object(wrapper,"exact_inputs",return_value={}), \
             patch.object(wrapper,"new_owner_paths",side_effect=paths), \
             patch.object(wrapper,"owned_environment",return_value={}), \
             patch.object(wrapper,"blender_context",side_effect=inert_context), \
             patch.object(wrapper,"run_json",side_effect=inert_response), \
             patch.object(wrapper,"semwright_version",return_value="synthetic-unit-only"), \
             patch.object(wrapper.subprocess,"run",side_effect=AssertionError("native process forbidden in unit test")), \
             patch.object(wrapper.subprocess,"check_output",side_effect=AssertionError("native process forbidden in unit test")), \
             patch.dict(wrapper.os.environ,{"MOTIONWRIGHT_BLENDER_SCOPE":"static-glb"}), \
             redirect_stdout(io.StringIO()):
            wrapper.main()
        return evidence_dir

    def test_main_rejects_corrupt_geometry_before_outer_pass_and_keeps_originals(self):
        with tempfile.TemporaryDirectory(prefix="glb-gate-red-") as directory:
            root=Path(directory)
            with self.assertRaisesRegex(BlenderContentError,"duplicate_surface_triangle"):
                self.run_main_fixture(root,bad_geometry=True)
            evidence_dir=root/"evidence"
            self.assertFalse((evidence_dir/"result.json").exists())
            original=json.loads((evidence_dir/"motionwright-blender-evidence.json").read_text())
            self.assertEqual(original["native_blender_e2e"],"PASS")
            self.assertTrue((evidence_dir/"motionwright-blender-source.original.json").is_file())
            rejected=(evidence_dir/"motionwright-contribution.glb").read_bytes()
            self.assertEqual(hashlib.sha256(rejected).hexdigest(),original["result"]["artifact"]["sha256"])
            self.assertTrue((root/"owner"/"workspace").is_dir())
            for stage in ("seed","realize"):
                custody=evidence_dir/("static-"+stage+"-originals")
                self.assertEqual((custody/"motionwright.sqlite3").read_bytes(),b"original synthetic database bytes")
                self.assertEqual((custody/"motionwright.sqlite3-wal").read_bytes(),b"original synthetic WAL bytes")
                receipt=json.loads((custody/"receipt.json").read_text())
                self.assertTrue(receipt["custody_complete"])
                self.assertFalse(receipt["sqlite_reopen_or_native_retry_executed"])
                for row in receipt["files"]:
                    raw=(custody/row["name"]).read_bytes()
                    self.assertEqual(len(raw),row["bytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(),row["sha256"])

    def test_main_uses_source_gate_and_retains_private_original_and_content(self):
        with tempfile.TemporaryDirectory(prefix="glb-gate-green-") as directory:
            evidence_dir=self.run_main_fixture(Path(directory),bad_geometry=False)
            result=json.loads((evidence_dir/"result.json").read_text())
            source=json.loads((evidence_dir/"motionwright-blender-source.original.json").read_text())
            content=json.loads((evidence_dir/"motionwright-blender-glb-content.json").read_text())
            self.assertNotIn("source_nodes",result["project"])
            self.assertEqual(len(source["source_nodes"]),2)
            self.assertTrue(content["indexed_surface_coverage_verified"])
            self.assertTrue(content["source_Project_UUIDs_explicitly_checked"])

    def test_context_cleanup_failure_prevents_outer_pass_and_keeps_admitted_glb(self):
        with tempfile.TemporaryDirectory(prefix="glb-context-incomplete-") as directory:
            root=Path(directory)
            original=RuntimeError("synthetic incomplete native context cleanup")
            with self.assertRaises(RuntimeError) as raised:
                self.run_main_fixture(root,bad_geometry=False,context_finish_error=original)
            self.assertIs(raised.exception,original)
            self.assertFalse((root/"evidence"/"result.json").exists())
            self.assertTrue((root/"evidence"/"motionwright-contribution.glb").is_file())

    def test_terminal_invalid_json_preserves_database_and_exact_streams_before_rejection(self):
        with tempfile.TemporaryDirectory(prefix="glb-terminal-invalid-") as directory:
            root=Path(directory)
            evidence=root/"evidence"
            evidence.mkdir()
            database=root/"motionwright.sqlite3"
            database.write_bytes(b"unopened SQLite original")
            response=subprocess.CompletedProcess(["inert"],0,b"not JSON\n",b"original stderr\x00")
            with patch.object(wrapper,"EVIDENCE",evidence), \
                 patch.object(wrapper.subprocess,"run",return_value=response) as run:
                with self.assertRaisesRegex(AssertionError,"non-JSON"):
                    wrapper.run_json(["inert"],env={},static_capture=("seed",database,None))
                self.assertEqual(run.call_count,1)
            custody=evidence/"static-seed-originals"
            self.assertEqual((custody/"stdout.original").read_bytes(),response.stdout)
            self.assertEqual((custody/"stderr.original").read_bytes(),response.stderr)
            self.assertEqual((custody/"motionwright.sqlite3").read_bytes(),database.read_bytes())

    def test_native_failure_stays_primary_when_custody_rejects_oversized_source(self):
        with tempfile.TemporaryDirectory(prefix="glb-custody-limit-") as directory:
            root=Path(directory)
            evidence=root/"evidence"
            evidence.mkdir()
            database=root/"motionwright.sqlite3"
            database.write_bytes(b"source larger than this unit limit")
            response=subprocess.CompletedProcess(["inert"],17,b"original stdout",b"native failure")
            with patch.object(wrapper,"EVIDENCE",evidence), \
                 patch.object(wrapper,"STATIC_DATABASE_BYTES",4), \
                 patch.object(wrapper.subprocess,"run",return_value=response):
                with self.assertRaisesRegex(AssertionError,"command failed \\(17\\)") as raised:
                    wrapper.run_json(["inert"],env={},static_capture=("seed",database,None))
            self.assertTrue(hasattr(raised.exception,"native_custody_error"))
            receipt=json.loads((evidence/"static-seed-originals"/"receipt.json").read_text())
            self.assertFalse(receipt["custody_complete"])
            self.assertEqual(database.read_bytes(),b"source larger than this unit limit")

    def test_no_terminal_does_not_reopen_capture_or_retry(self):
        with tempfile.TemporaryDirectory(prefix="glb-no-terminal-") as directory:
            root=Path(directory)
            with patch.object(wrapper,"EVIDENCE",root), \
                 patch.object(wrapper.subprocess,"run",side_effect=subprocess.TimeoutExpired(["inert"],1,output=b"partial original\n")) as run, \
                 patch.object(wrapper,"static_terminal_originals",side_effect=AssertionError("no terminal")) as capture:
                with self.assertRaises(subprocess.TimeoutExpired):
                    wrapper.run_json(["inert"],env={},static_capture=("seed",root/"absent.sqlite3",None))
                self.assertEqual(run.call_count,1)
                capture.assert_not_called()
            receipt=json.loads((root/"static-seed-unconfirmed"/"receipt.json").read_text())
            self.assertEqual(receipt["native_operation_result"],"UNKNOWN")
            self.assertFalse(receipt["native_process_terminal_observed"])
            self.assertEqual((root/"static-seed-unconfirmed"/"stdout.partial.original").read_bytes(),b"partial original\n")

    def test_symlink_sidecar_is_not_followed_and_original_target_is_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="glb-symlink-custody-") as directory:
            root=Path(directory)
            evidence=root/"evidence"
            evidence.mkdir()
            database=root/"motionwright.sqlite3"
            database.write_bytes(b"original DB")
            outside=root/"unrelated"
            outside.write_bytes(b"must not be adopted as WAL")
            Path(str(database)+"-wal").symlink_to(outside)
            response=subprocess.CompletedProcess(["inert"],0,b"{}",b"")
            with patch.object(wrapper,"EVIDENCE",evidence):
                with self.assertRaisesRegex(AssertionError,"custody is incomplete"):
                    wrapper.static_terminal_originals("seed",database,None,response)
            self.assertEqual(outside.read_bytes(),b"must not be adopted as WAL")
            self.assertFalse((evidence/"static-seed-originals"/"motionwright.sqlite3-wal").exists())

    def test_owner_retention_receipt_failure_does_not_mask_existing_native_or_oracle_error(self):
        with tempfile.TemporaryDirectory(prefix="glb-retention-finally-") as directory:
            root=Path(directory)
            owner=root/"owner"
            owner.mkdir()
            original=RuntimeError("original native or oracle failure")
            secondary=OSError("receipt disk write failed")
            with patch.object(wrapper.tempfile,"mkdtemp",return_value=str(owner)), \
                 patch.object(wrapper,"write_static_json",side_effect=secondary):
                with self.assertRaises(RuntimeError) as raised:
                    with wrapper.retained_static_owner():
                        raise original
                self.assertIs(raised.exception,original)
                self.assertIs(original.static_owner_retention_error,secondary)
                self.assertTrue(owner.is_dir())
                with self.assertRaises(OSError) as raised_without_primary:
                    with wrapper.retained_static_owner():
                        pass
                self.assertIs(raised_without_primary.exception,secondary)


if __name__=="__main__":
    unittest.main()
