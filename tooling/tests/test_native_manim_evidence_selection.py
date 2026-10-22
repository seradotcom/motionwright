"""Pure selected-evidence contracts. Every byte is an explicit synthetic fixture.

No native renderer, pixel decoder, SQLite engine, filesystem or cryptography is
executed here. Linux openat/Git behavior belongs to the generic helper's G1 tests.
"""
from __future__ import annotations
import base64
import copy
import io
import json
from pathlib import Path
import re
import sys
import tarfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import native_evidence_projection as n
import native_manim_evidence_selection as s
from tooling.tests.test_native_evidence_projection import MemoryHandles, rewrite_tar

CURRENT = {"source_sha": "1"*40, "semwright_sha": "2"*40,
           "run_id": "12345", "run_attempt": "1"}
BINDINGS = [{"path": path, "sha256": "a"*64} for path in s.SOURCE_PATHS]
DIAGNOSTIC = "unit-secret-never-selected-or-published"
TOKEN = "a"*32


def packed(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def value(rule):
    if isinstance(rule, n.Literal): return copy.deepcopy(rule.value)
    if isinstance(rule, n.Nullable): return None
    if isinstance(rule, n.Object):
        return {key: value(field.rule) for key, field in rule.fields.items() if field.required}
    if isinstance(rule, n.Array): return [value(rule.item) for _ in range(rule.minimum)]
    if isinstance(rule, n.Number):
        x = max(rule.minimum or 0, 0)
        if rule.maximum is not None: x = min(x, rule.maximum)
        return int(x) if rule.integer else float(x)
    if rule is bool: return False
    if isinstance(rule, n.String):
        if rule.values: return rule.values[0]
        for x in ("a"*64, "1"*40, "11111111-1111-4111-8111-111111111111",
                  "project:11111111-1111-4111-8111-111111111111", "1", "fixture",
                  "#112233", "manim-"+TOKEN, "mw_"+TOKEN+".mwcam.zip",
                  "videos/mw_"+TOKEN+"/360p30/mw_"+TOKEN+".mp4", "2026-10-21T12:00:00+00:00"):
            if len(x.encode()) <= rule.maximum and (not rule.pattern or re.fullmatch(rule.pattern, x)):
                return x
    raise AssertionError("Fixture rule has no explicit synthetic value")


def package(plan, token=TOKEN):
    plan_raw = packed(plan)
    program = ("# SYNTHETIC unit source; no native execution. Plan " + n.sha(plan_raw) + "\n").encode()
    contents = {"plan.json": plan_raw, "scene.py": program,
                "assets.lock.json": b'{"synthetic":true,"assets":[]}\n',
                "render-config.json": b'{"synthetic":true}\n',
                "manifest.json": b'{"synthetic":true,"acceptance":false}\n'}
    closure = value(s.CLOSURE)
    closure.update(plan_sha256=n.sha(plan_raw), source_sha256=n.sha(program),
                   assets_lock_sha256=n.sha(contents["assets.lock.json"]),
                   config_sha256=n.sha(contents["render-config.json"]),
                   manifest_sha256=n.sha(contents["manifest.json"]),
                   entries=[{"path":name,"bytes":len(data),"sha256":n.sha(data)}
                            for name,data in sorted(contents.items())])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
        for name,data in sorted(contents.items()): archive.writestr(name,data)
    raw = out.getvalue()
    carrier = {"relative_path":"mw_"+token+".mwcam.zip", "sha256":n.sha(raw), "bytes":len(raw),
               "media_type":"application/vnd.motionwright.manim-planar-camera-source+zip", "closure":closure}
    return plan_raw, program, raw, carrier


def fixture():
    plan = value(s.PLAN)
    plan["binding"].update(revision=7, project_id="10000000-1111-4111-8111-111111111111",
        generation="20000000-1111-4111-8111-111111111111", scene_id="30000000-1111-4111-8111-111111111111",
        deliverable_id="40000000-1111-4111-8111-111111111111")
    binding = plan["binding"]
    plan["reframe"].update(scene_id=binding["scene_id"], start_center_x=960.0, start_center_y=540.0,
                          end_center_x=1000.0, end_center_y=544.0)
    plan["camera"].update(center_x=1000.0, center_y=544.0, zoom=1.0, rotation_deg=0.0, safe_margin=0.02)
    plan["nodes"][0].update(x_px=700.0,y_px=400.0,width_px=360.0,height_px=200.0)
    plan["clock"].update(timeline_start={"num":"0","den":"1"}, duration={"num":"4","den":"1"},
                         frame_rate={"num":"30","den":"1"},project_end_frame_exclusive=120)
    plan_raw, program, capsule, carrier = package(plan)
    getters = value(s.GETTERS)
    getters.update(binding=copy.deepcopy(binding), plan_sha256=n.sha(plan_raw))
    for index, row in enumerate(getters["frames"]):
        row["index"] = index
        row["objects"][0]["node_id"] = plan["nodes"][0]["node_id"]
    getter_raw = packed(getters)
    seed = value(s.SEED)
    seed.update(**binding, reframe_id=plan["reframe"]["id"], source_objects=[plan["nodes"][0]["node_id"]])
    seed["prepared_program"]["prepared"].update(source=copy.deepcopy(plan),
        source_plan_sha256=n.sha(plan_raw), source_plan_bytes=len(plan_raw))
    seed["prepared_program"]["trusted_compiler"].update(plan_sha256=n.sha(plan_raw), source_sha256=n.sha(program),
        plan_document=list(plan_raw), python_source=program.decode())
    media = b"\0\0\0\x18ftypSYNTHETIC-NOT-A-VIDEO"
    native = value(s.NATIVE)
    native.update(**binding, resource="project:"+binding["project_id"],
                  request_id="camera-12345-1-"+CURRENT["source_sha"][:12])
    result = value(s.NATIVE_RESULT)
    result.update(scene_id=binding["scene_id"],project_revision=binding["revision"],plan_sha256=n.sha(plan_raw))
    result["artifact"].update(sha256=n.sha(media), bytes=len(media), camera_getters_sha256=n.sha(getter_raw),
                              camera_source=copy.deepcopy(carrier), verified_path="/never-read/"+DIAGNOSTIC)
    result["result"] = {"canonical_failure_diagnostic":{"cmdline":DIAGNOSTIC}}
    native["result"] = result
    receipts=[]
    for key,suffix,command in (("original_start_receipt",":start","driver.manim-community.camera.render.start"),
                               ("original_result_receipt",":result","driver.manim-community.render.result")):
        r=value(s.RECEIPT);r.update(project_id=binding["project_id"],generation=binding["generation"],
            revision=binding["revision"],request_id=native["request_id"]+suffix,command=command,stage="completed",
            payload={"raw_diagnostic":DIAGNOSTIC})
        native[key]=r;receipts.append(copy.deepcopy(r))
    native["recent_receipts"]=receipts
    native["canonical_failure_diagnostic"]={"session":DIAGNOSTIC}
    native_raw=packed(native)
    decoded=value(s.DECODED);decoded.update(binding=copy.deepcopy(binding), compiled_plan_sha256=n.sha(plan_raw),
        trusted_media_sha256=n.sha(media))
    for i,row in enumerate(decoded["native_frame_movement_observations"]):
        row.update(frame=(0,60,119)[i],node_id=plan["nodes"][0]["node_id"])
    decoded_raw=packed(decoded)
    summary=value(s.RESULT);summary.update(motionwright_sha=CURRENT["source_sha"],semwright_sha=CURRENT["semwright_sha"],
        project=copy.deepcopy(binding),source_project_sha256=seed["source_project_sha256"],plan_sha256=n.sha(plan_raw),
        program_sha256=n.sha(program),original_getters_sha256=n.sha(getter_raw),
        native_evidence_sha256=n.sha(native_raw),decoded_readback_sha256=n.sha(decoded_raw))
    summary["mp4"].update(sha256=n.sha(media),bytes=len(media),relative_path=result["artifact"]["relative_path"])
    terminal=value(s.TERMINAL);terminal.update(native_example_invoked=True,native_result=copy.deepcopy(native),
        original_process_observation={"pid":123,"process_returncode":0,"native_outcome":"NOT_INTERPRETED","automatic_retry":False})
    inputs=value(s.INPUTS);inputs.update(motionwright_sha=CURRENT["source_sha"],semwright_sha=CURRENT["semwright_sha"])
    data={"result.json":packed(summary),"source-seed.json":packed(seed),"compiled-camera-plan.json":plan_raw,
          "compiled-camera-program.py":program,"motionwright-manim-camera-evidence.json":native_raw,
          "original-camera-getters.json":getter_raw,"original-render-program.py":program,
          "decoded-camera-readback.json":decoded_raw,"motionwright-manim-community.mp4":media,
          "native-invocation-intent.json":packed({"context":CURRENT,"native_example_invocations":1,"automatic_retry":False}),
          "original-terminal.json":packed(terminal),"native-input-identities.json":packed(inputs)}
    files={("evidence",p):raw for p,raw in data.items()}
    files[("owner","motionwright-data/motionwright.sqlite3")]=b"SYNTHETIC-NOT-A-SQLITE-STORE"
    files[("owner","output/"+carrier["relative_path"])]=capsule
    files[("owner","runtime/motionwright.session")]=DIAGNOSTIC.encode()
    files[("owner","config/connection.json")]=DIAGNOSTIC.encode()
    files[("evidence","observer-diagnostic.json")]=DIAGNOSTIC.encode()
    return files


def edited_fixture():
    files=fixture()
    original_native=json.loads(files[("evidence","motionwright-manim-camera-evidence.json")])
    original_plan=json.loads(files[("evidence","compiled-camera-plan.json")])
    original_seed=json.loads(files[("evidence","source-seed.json")])
    original_capsule=files[("owner","output/mw_"+TOKEN+".mwcam.zip")]
    original_carrier=original_native["result"]["artifact"]["camera_source"]
    original_stamp={"resource":original_native["resource"],"generation":original_native["generation"],"revision":7}
    attached_stamp={**original_stamp,"revision":9}
    canonical_stamp={**original_stamp,"revision":10}
    edited_asset_plan=copy.deepcopy(original_plan)
    edited_asset_plan["reframe"]["end_center_x"]=edited_asset_plan["camera"]["center_x"]=1040.0
    _,_,edited_asset_capsule,edited_asset_carrier=package(edited_asset_plan)
    plan=copy.deepcopy(edited_asset_plan);plan["binding"]["revision"]=10
    token="b"*32
    plan_raw,program,capsule,carrier=package(plan,token)
    binding=plan["binding"]
    program_record=copy.deepcopy(original_seed["prepared_program"])
    program_record["prepared"].update(source=copy.deepcopy(plan),source_plan_sha256=n.sha(plan_raw),source_plan_bytes=len(plan_raw))
    program_record["trusted_compiler"].update(plan_sha256=n.sha(plan_raw),source_sha256=n.sha(program),
        plan_document=list(plan_raw),python_source=program.decode())
    original_link={"native_evidence_sha256":n.sha(files[("evidence","motionwright-manim-camera-evidence.json")]),
        "request_id":original_native["request_id"],"camera_source":copy.deepcopy(original_carrier),
        "original_start_receipt":copy.deepcopy(original_native["original_start_receipt"]),
        "original_result_receipt":copy.deepcopy(original_native["original_result_receipt"])}
    original_id="50000000-1111-4111-8111-111111111111"
    edited_id="60000000-1111-4111-8111-111111111111"
    original_rebound=copy.deepcopy(original_plan);original_rebound["binding"]["revision"]=9
    _,_,_,original_rebound_carrier=package(original_rebound)
    inspected_original=value(s.INSPECTION);inspected_original.update(source_asset_id=original_id,
        source_sha256=n.sha(original_capsule),origin_binding=copy.deepcopy(original_plan["binding"]),
        current_binding=copy.deepcopy(original_rebound["binding"]),original_closure=copy.deepcopy(original_carrier["closure"]),
        current_closure=copy.deepcopy(original_rebound_carrier["closure"]),current_plan=copy.deepcopy(original_rebound),revision_only_rebound=True)
    current_inspection=value(s.INSPECTION);current_inspection.update(source_asset_id=edited_id,
        source_sha256=n.sha(edited_asset_capsule),origin_binding=copy.deepcopy(original_plan["binding"]),
        current_binding=copy.deepcopy(binding),original_closure=copy.deepcopy(edited_asset_carrier["closure"]),
        current_closure=copy.deepcopy(carrier["closure"]),current_plan=copy.deepcopy(plan),revision_only_rebound=True)
    preview=value(s.PREVIEW);preview.update(expected=attached_stamp,original_source_asset_id=original_id,
        edited_source_asset_id=edited_id,original_source_sha256=n.sha(original_capsule),edited_source_sha256=n.sha(edited_asset_capsule),
        current_plan_sha256=original_rebound_carrier["closure"]["plan_sha256"],proposed_plan_sha256=carrier["closure"]["plan_sha256"],
        change={"synthetic_diagnostic":DIAGNOSTIC})
    edit=value(s.EDIT);edit.update(project_id=binding["project_id"],scene_id=binding["scene_id"],deliverable_id=binding["deliverable_id"],
        canonical_stamp=canonical_stamp,source_project_sha256="b"*64,original_source_asset_id=original_id,edited_source_asset_id=edited_id,
        original_source_sha256=n.sha(original_capsule),edited_source_sha256=n.sha(edited_asset_capsule),
        current_export_bundle_sha256=n.sha(capsule),current_source_inspection=current_inspection,
        original_stamp=original_stamp,attached_stamp=attached_stamp,original_native=original_link,
        original_source_inspection=inspected_original,preview=preview,prepared_program=program_record)
    edit_raw=packed(edit)
    getters=json.loads(files[("evidence","original-camera-getters.json")]);getters.update(binding=copy.deepcopy(binding),plan_sha256=n.sha(plan_raw))
    getter_raw=packed(getters)
    media=b"\0\0\0\x18ftypSYNTHETIC-EDITED-NOT-A-VIDEO"
    native=copy.deepcopy(original_native);native.update(**binding,request_id=original_native["request_id"]+"-edited1040")
    native["result"].update(project_revision=10,plan_sha256=n.sha(plan_raw),job_ref="manim-"+token)
    native["result"]["artifact"].update(relative_path="videos/mw_"+token+"/360p30/mw_"+token+".mp4",sha256=n.sha(media),bytes=len(media),
        camera_getters_sha256=n.sha(getter_raw),camera_source=copy.deepcopy(carrier))
    for key,suffix in [("original_start_receipt",":start"),("original_result_receipt",":result")]:
        native[key].update(revision=10,request_id=native["request_id"]+suffix)
    native["recent_receipts"].extend([copy.deepcopy(native["original_start_receipt"]),copy.deepcopy(native["original_result_receipt"])])
    native_raw=packed(native)
    decoded=json.loads(files[("evidence","decoded-camera-readback.json")]);decoded.update(binding=copy.deepcopy(binding),
        compiled_plan_sha256=n.sha(plan_raw),trusted_media_sha256=n.sha(media))
    decoded_raw=packed(decoded)
    result=json.loads(files[("evidence","result.json")]);result.update(project=copy.deepcopy(binding),source_project_sha256=edit["source_project_sha256"],
        plan_sha256=n.sha(plan_raw),program_sha256=n.sha(program),original_getters_sha256=n.sha(getter_raw),native_evidence_sha256=n.sha(native_raw),
        decoded_readback_sha256=n.sha(decoded_raw))
    result["mp4"].update(relative_path=native["result"]["artifact"]["relative_path"],sha256=n.sha(media),bytes=len(media))
    terminal=json.loads(files[("evidence","original-terminal.json")]);terminal["native_result"]=copy.deepcopy(native)
    inputs=json.loads(files[("evidence","native-input-identities.json")]);inputs["binaries"]["source_cycle_example"]="c"*64
    intent=value(s.EDIT_INTENT);intent.update(request_id="edit-12345-"+CURRENT["source_sha"][:12],project_id=binding["project_id"],
        canonical_stamp=original_stamp,original_native=copy.deepcopy(original_link),original_source_sha256=n.sha(original_capsule),
        edited_source_sha256=n.sha(edited_asset_capsule))
    reopen=value(s.REOPEN)
    for key in s.CYCLE_COMMON:reopen[key]=copy.deepcopy(edit[key])
    reopen.update(edit_receipt_sha256=n.sha(edit_raw),edited_native={"native_evidence_sha256":n.sha(native_raw),
        "request_id":native["request_id"],"camera_source":copy.deepcopy(carrier),
        "original_start_receipt":copy.deepcopy(native["original_start_receipt"]),"original_result_receipt":copy.deepcopy(native["original_result_receipt"])})
    reopen_raw=packed(reopen)
    negative=value(s.NEGATIVE);negative.update(original_media_sha256=original_native["result"]["artifact"]["sha256"],
        edited_plan_sha256=n.sha(plan_raw),original_native_evidence_sha256=original_link["native_evidence_sha256"])
    negative_raw=packed(negative)
    observation=value(s.OBSERVATION);observation.update(motionwright_sha=CURRENT["source_sha"],semwright_sha=CURRENT["semwright_sha"],
        original_request_id=original_native["request_id"],edited_request_id=native["request_id"],
        original_native_evidence_sha256=original_link["native_evidence_sha256"],edited_native_evidence_sha256=n.sha(native_raw),
        edit_receipt_sha256=n.sha(edit_raw),fresh_source_readback_sha256=n.sha(reopen_raw),original_media_negative_sha256=n.sha(negative_raw),
        original_plan_sha256=n.sha(files[("evidence","compiled-camera-plan.json")]),edited_plan_sha256=n.sha(plan_raw))
    data={"result.json":packed(result),"source-seed.json":edit_raw,"compiled-camera-plan.json":plan_raw,"compiled-camera-program.py":program,
        "motionwright-manim-camera-evidence.json":native_raw,"original-camera-getters.json":getter_raw,"original-render-program.py":program,
        "decoded-camera-readback.json":decoded_raw,"motionwright-manim-community.mp4":media,"original-terminal.json":packed(terminal),
        "native-input-identities.json":packed(inputs),"source-cycle-original-stamp.json":packed(original_stamp),
        "source-cycle-edit.intent.json":packed(intent),"source-cycle-edit.json":edit_raw,"source-cycle-edited-stamp.json":packed(canonical_stamp),
        "source-cycle-readback.json":reopen_raw,"original-media-edited-geometry-negative.json":negative_raw,"source-cycle-observation.json":packed(observation)}
    files.update({("evidence",p):raw for p,raw in data.items()})
    files[("owner","edited1040/output/"+carrier["relative_path"])]=capsule
    for raw in (original_capsule,edited_asset_capsule):
        sha=n.sha(raw);files[("owner","motionwright-data/blobs/sha256/"+sha[:2]+"/"+sha)]=raw
    return files


class AdapterMemoryHandles(MemoryHandles):
    def read(self, root, parts, maximum):
        if (root, "/".join(parts)) not in self.files:
            raise FileNotFoundError()
        return super().read(root, parts, maximum)


class ManimSelectedContractTests(unittest.TestCase):
    def setUp(self): self.files=fixture()
    def capture(self, files=None, *, scenario="camera120", fixture_name="original"):
        handles=AdapterMemoryHandles(self.files if files is None else files)
        with mock.patch.object(n,"RootHandles",return_value=handles), \
             mock.patch.object(n,"_verify_sources"), \
             mock.patch.object(s,"source_bindings",return_value=copy.deepcopy(BINDINGS)):
            selection=s.CameraSelection(Path("/virtual/owner"),Path("/virtual/evidence"),Path("/virtual/source"),
                CURRENT,scenario,fixture_name)
            raw,manifest=selection.capture()
        return raw,manifest,handles
    def inspect(self, raw, **changes):
        return s.inspect_camera_tar(raw,current=changes.get("current",CURRENT),scenario=changes.get("scenario","camera120"),
            fixture=changes.get("fixture","original"),bindings=changes.get("bindings",BINDINGS))
    def publication_fixture(self, *, edited=False):
        files=edited_fixture() if edited else self.files
        scenario="camera120-source-cycle" if edited else "camera120"
        fixture_name="edited1040" if edited else "original"
        raw,m,_=self.capture(files,scenario=scenario,fixture_name=fixture_name)
        manifest_raw=n.canonical(m)
        files={**files,("evidence",s.MANIFEST_NAME):manifest_raw}
        receipt={"schema":s.SCHEMA,"status":"PASS","context":CURRENT,"scenario":scenario,
            "phase":"edited" if edited else "original","required_layers_complete":True,
            "native_terminal_complete":True,"native_result_retained":True,"secondary_error_count":0,
            "original_owner_tree_removed":False,"atomic_sqlite_snapshot_claimed":False,
            "producer_authenticated_by_encryption":False,"selected_manifest_sha256":n.sha(manifest_raw),
            "selected_manifest_bytes":len(manifest_raw),"selected_tar_sha256":n.sha(raw),"selected_tar_bytes":len(raw)}
        return files,receipt,scenario,fixture_name
    def publish(self, files, receipt, scenario, fixture_name, current=None):
        handles=AdapterMemoryHandles(files)
        with mock.patch.object(n,"RootHandles",return_value=handles), \
             mock.patch.object(s,"source_bindings",return_value=copy.deepcopy(BINDINGS)):
            result=s.verify_publication_selection(Path("/virtual/evidence"),receipt,source_root=Path("/virtual/source"),
                current=current or CURRENT,scenario=scenario,fixture=fixture_name)
        return result,handles
    def test_publication_recomputes_each_projection_from_its_original_local_identity(self):
        args=self.publication_fixture();m,handles=self.publish(*args)
        self.assertTrue(m["required_layers_complete"])
        self.assertTrue(all(root=="evidence" for root,path in handles.reads))
        self.assertFalse(any("motionwright.sqlite3" in path for root,path in handles.reads))
    def test_edited_publication_rechecks_cycle_and_two_cas_descriptors_without_reopening_sqlite(self):
        m,_=self.publish(*self.publication_fixture(edited=True));self.assertTrue(m["required_layers_complete"])
    def test_changed_original_diagnostic_bytes_require_a_new_origin_identity(self):
        files,receipt,scenario,fixture_name=self.publication_fixture()
        key=("evidence","motionwright-manim-camera-evidence.json");v=json.loads(files[key]);v["canonical_failure_diagnostic"]["session"]="changed synthetic diagnostic"
        files[key]=packed(v)
        with self.assertRaisesRegex(n.SelectionError,"local-readback-changed"):
            self.publish(files,receipt,scenario,fixture_name)
    def test_publication_rejects_changed_manifest_bytes_before_receipt_reads(self):
        files,receipt,scenario,fixture_name=self.publication_fixture();files[("evidence",s.MANIFEST_NAME)]+=b" "
        with self.assertRaisesRegex(n.SelectionError,"manifest-identity"):
            self.publish(files,receipt,scenario,fixture_name)
    def test_publication_requires_both_custody_layers_and_native_terminal(self):
        files,receipt,scenario,fixture_name=self.publication_fixture()
        for field in ("required_layers_complete","native_terminal_complete"):
            with self.subTest(field=field),self.assertRaisesRegex(n.SelectionError,"custody-incomplete"):
                self.publish(files,{**receipt,field:False},scenario,fixture_name)
    def test_publication_cannot_promote_atomic_or_authenticated_producer_claims(self):
        files,receipt,scenario,fixture_name=self.publication_fixture()
        for field in ("atomic_sqlite_snapshot_claimed","producer_authenticated_by_encryption","original_owner_tree_removed"):
            with self.subTest(field=field),self.assertRaisesRegex(n.SelectionError,"custody-incomplete"):
                self.publish(files,{**receipt,field:True},scenario,fixture_name)
    def test_publication_run_identity_cannot_be_taken_from_the_receipt_itself(self):
        args=self.publication_fixture()
        with self.assertRaisesRegex(n.SelectionError,"custody-incomplete"):
            self.publish(*args,current={**CURRENT,"run_id":"99999"})

    def test_complete_synthetic_contract_keeps_four_layers_and_new_json_identity(self):
        raw,m,handles=self.capture()
        self.assertTrue(m["required_layers_complete"])
        self.assertEqual({row["layer"] for row in m["layers"]},{"readback","persistence","attempt-custody","origin"})
        self.assertEqual(self.inspect(raw),m)
        self.assertNotIn(DIAGNOSTIC.encode(),raw)
        by_role={x["role"]:x for x in m["members"]}
        self.assertNotEqual(by_role["native-receipts"]["expected_sha256"],by_role["native-receipts"]["retained_sha256"])
        self.assertFalse(by_role["source-seed"]["original_bytes"])
        self.assertFalse(m["human_acceptance"])
    def test_selected_inventory_never_reads_or_stats_excluded_roots(self):
        _,_,handles=self.capture()
        for root,path in handles.reads+handles.stats:
            self.assertFalse(n.excluded(n.member_path(path)),(root,path))
    def test_generated_python_with_newlines_is_retained_separately_and_not_in_seed_projection(self):
        raw,m,_=self.capture()
        row=next(x for x in m["members"] if x["role"]=="source-seed")
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            p=n.strict(archive.extractfile(row["member"]).read())
        self.assertNotIn("python_source",p["value"]["prepared_program"]["trusted_compiler"])
        self.assertNotIn("plan_document",p["value"]["prepared_program"]["trusted_compiler"])
        self.assertTrue(any(x["role"]=="compiled-program" and x["original_bytes"] for x in m["members"]))
    def test_actual_policy_has_no_wildcard_or_opaque_json_leaf(self):
        def walk(rule):
            if isinstance(rule,n.Object):
                self.assertTrue(all(isinstance(x,n.Field) for x in rule.fields.values()))
                for field in rule.fields.values():walk(field.rule)
            elif isinstance(rule,(n.Array,n.Nullable)):walk(rule.item)
            else:self.assertTrue(rule is bool or isinstance(rule,(n.String,n.Number,n.Literal)))
        for rule in s.POLICIES.values():walk(rule)
    def test_missing_media_blocks_intake_without_losing_attempt(self):
        self.files.pop(("evidence","motionwright-manim-community.mp4"))
        raw,m,_=self.capture()
        self.assertFalse(m["required_layers_complete"])
        self.assertEqual(next(x for x in m["members"] if x["role"]=="native-terminal")["state"],"INCLUDED")
        with self.assertRaises(n.SelectionError):self.inspect(raw)
    def test_missing_receipt_blocks_readback(self):
        self.files.pop(("evidence","motionwright-manim-camera-evidence.json"))
        raw,m,_=self.capture();self.assertFalse(m["required_layers_complete"])
        with self.assertRaises(n.SelectionError):self.inspect(raw)
    def test_missing_editable_capsule_cannot_be_counted_as_native_source(self):
        self.files.pop(("owner","output/mw_"+TOKEN+".mwcam.zip"))
        raw,m,_=self.capture();self.assertFalse(m["required_layers_complete"])
        with self.assertRaises(n.SelectionError):self.inspect(raw)
    def test_changed_capsule_digest_is_not_included(self):
        self.files[("owner","output/mw_"+TOKEN+".mwcam.zip")]+=b"changed"
        _,m,_=self.capture();self.assertFalse(m["required_layers_complete"])
    def test_required_sqlite_identity_cannot_be_removed(self):
        self.files.pop(("owner","motionwright-data/motionwright.sqlite3"))
        raw,m,_=self.capture();self.assertFalse(m["required_layers_complete"])
        with self.assertRaises(n.SelectionError):self.inspect(raw)
    def test_sqlite_auxiliaries_absent_have_positive_absence_guards(self):
        _,m,_=self.capture();self.assertEqual(len(m["absence_guards"]),2)
        self.assertTrue(all(x["absent_before"] and x["absent_after"] and not x["opened"] for x in m["absence_guards"]))
    def test_existing_sqlite_wal_is_required_and_retained_as_original(self):
        self.files[("owner","motionwright-data/motionwright.sqlite3-wal")]=b"SYNTHETIC-WAL"
        raw,m,_=self.capture();self.inspect(raw)
        row=next(x for x in m["members"] if x["role"]=="sqlite-wal")
        self.assertTrue(row["required"] and row["original_bytes"])
        self.assertEqual(len(m["absence_guards"]),1)
    def test_diagnostic_unknown_field_in_original_plan_fails_without_archiving_it(self):
        key=("evidence","compiled-camera-plan.json");p=json.loads(self.files[key]);p["diagnostic"]=DIAGNOSTIC
        self.files[key]=packed(p)
        raw,m,_=self.capture();self.assertFalse(m["required_layers_complete"]);self.assertNotIn(DIAGNOSTIC.encode(),raw)
    def test_unknown_native_outcome_cannot_pass_even_with_complete_custody(self):
        key=("evidence","original-terminal.json");v=json.loads(self.files[key]);v["original_process_observation"].update(
            native_outcome="UNKNOWN",process_returncode=None,timeout_seconds=420)
        self.files[key]=packed(v)
        raw,m,_=self.capture();self.assertTrue(m["required_layers_complete"])
        with self.assertRaisesRegex(n.SelectionError,"terminal-not-successful"):self.inspect(raw)
    def test_later_error_cannot_publish_an_otherwise_complete_native_result(self):
        key=("evidence","original-terminal.json");v=json.loads(self.files[key]);v["later_error"]={"type":"OSError","message":DIAGNOSTIC}
        self.files[key]=packed(v)
        raw,m,_=self.capture();self.assertTrue(m["required_layers_complete"]);self.assertNotIn(DIAGNOSTIC.encode(),raw)
        with self.assertRaisesRegex(n.SelectionError,"terminal-not-successful"):self.inspect(raw)
    def test_failed_process_returncode_is_not_interpreted_as_completed(self):
        key=("evidence","original-terminal.json");v=json.loads(self.files[key]);v["original_process_observation"]["process_returncode"]=2
        self.files[key]=packed(v);raw,_,_=self.capture()
        with self.assertRaisesRegex(n.SelectionError,"terminal-not-successful"):self.inspect(raw)
    def test_success_claim_cannot_substitute_a_different_callback_result(self):
        key=("evidence","original-terminal.json");v=json.loads(self.files[key]);v["native_result"]["request_id"]="other-request"
        self.files[key]=packed(v);raw,_,_=self.capture()
        with self.assertRaisesRegex(n.SelectionError,"terminal-result"):self.inspect(raw)
    def test_source_run_attempt_and_scenario_are_external_expectations(self):
        raw,_,_=self.capture()
        for current in ({**CURRENT,"source_sha":"3"*40},{**CURRENT,"run_id":"12346"},{**CURRENT,"run_attempt":"2"}):
            with self.subTest(current=current),self.assertRaises(n.SelectionError):self.inspect(raw,current=current)
        with self.assertRaises(n.SelectionError):self.inspect(raw,scenario="camera120-source-cycle")
    def test_original_cannot_be_relabelled_as_edited(self):
        raw,_,_=self.capture()
        with self.assertRaises(n.SelectionError):self.inspect(raw,scenario="camera120-source-cycle",fixture="edited1040")
    def test_source_binding_file_digest_changes_block_intake(self):
        raw,_,_=self.capture();bad=copy.deepcopy(BINDINGS);bad[0]["sha256"]="b"*64
        with self.assertRaisesRegex(n.SelectionError,"source-bindings"):self.inspect(raw,bindings=bad)
    def test_consumer_refuses_a_weaker_kind_or_policy_for_a_required_role(self):
        raw,_,_=self.capture()
        def weaker(m):
            r=next(x for x in m["members"] if x["role"]=="native-result");r["kind"]="original";r["policy"]=None
        with self.assertRaises(n.SelectionError):self.inspect(rewrite_tar(raw,mutate_manifest=weaker))
    def test_selected_capture_is_attempted_once_even_if_the_first_fails(self):
        with mock.patch.object(s,"source_bindings",return_value=copy.deepcopy(BINDINGS)), \
             mock.patch.object(n,"RootHandles",side_effect=n.SelectionError("fixture-open-failed")):
            selection=s.CameraSelection(Path("/owner"),Path("/evidence"),Path("/source"),CURRENT,"camera120","original")
            with self.assertRaisesRegex(n.SelectionError,"fixture-open-failed"):selection.capture()
            with self.assertRaisesRegex(n.SelectionError,"already-attempted"):selection.capture()
    def test_source_preflight_failure_precedes_selected_runtime_read(self):
        with mock.patch.object(s,"source_bindings",side_effect=n.SelectionError("physical-source-differs-from-git")), \
             mock.patch.object(n,"RootHandles") as handles:
            with self.assertRaises(n.SelectionError):s.CameraSelection(Path("/owner"),Path("/evidence"),Path("/source"),CURRENT,"camera120","original")
            handles.assert_not_called()
    def test_required_roles_cannot_be_downgraded_or_satisfied_by_exclusions(self):
        raw,_,_=self.capture()
        for mutate in (lambda m:m["members"][0].update(required=False),
                       lambda m:m["members"][0].update(state="EXCLUDED_BY_SCOPE")):
            with self.assertRaises(n.SelectionError):self.inspect(rewrite_tar(raw,mutate_manifest=mutate))
    def test_complete_edited_source_keeps_two_cas_assets_and_fresh_readback(self):
        raw,m,_=self.capture(edited_fixture(),scenario="camera120-source-cycle",fixture_name="edited1040")
        self.assertTrue(m["required_layers_complete"])
        self.inspect(raw,scenario="camera120-source-cycle",fixture="edited1040")
        self.assertTrue(all(x["phase"]=="edited" for x in m["members"]))
        cas=[x for x in m["members"] if x["role"] in ("original-source-cas","edited-source-cas")]
        self.assertEqual(len(cas),2);self.assertNotEqual(cas[0]["expected_sha256"],cas[1]["expected_sha256"])
        self.assertNotIn(DIAGNOSTIC.encode(),raw)
    def test_each_missing_edited_cas_asset_blocks_persistence(self):
        original=edited_fixture()
        keys=[key for key in original if key[0]=="owner" and "/blobs/sha256/" in key[1]]
        self.assertEqual(len(keys),2)
        for key in keys:
            files=dict(original);files.pop(key)
            raw,m,_=self.capture(files,scenario="camera120-source-cycle",fixture_name="edited1040")
            self.assertFalse(m["required_layers_complete"])
            with self.assertRaises(n.SelectionError):self.inspect(raw,scenario="camera120-source-cycle",fixture="edited1040")
    def test_edited_cas_bytes_cannot_change_under_the_old_content_address(self):
        files=edited_fixture();key=next(key for key in files if key[0]=="owner" and "/blobs/sha256/" in key[1])
        files[key]+=b"changed"
        _,m,_=self.capture(files,scenario="camera120-source-cycle",fixture_name="edited1040")
        self.assertFalse(m["required_layers_complete"])
    def test_missing_edit_intent_cannot_be_hidden_by_successful_native_media(self):
        files=edited_fixture();files.pop(("evidence","source-cycle-edit.intent.json"))
        _,m,_=self.capture(files,scenario="camera120-source-cycle",fixture_name="edited1040")
        self.assertFalse(m["required_layers_complete"])
    def test_edited_readback_cannot_reference_a_different_edit_receipt(self):
        files=edited_fixture();key=("evidence","source-cycle-readback.json");v=json.loads(files[key]);v["edit_receipt_sha256"]="f"*64
        files[key]=packed(v)
        raw,_,_=self.capture(files,scenario="camera120-source-cycle",fixture_name="edited1040")
        with self.assertRaisesRegex(n.SelectionError,"fresh-source-readback"):
            self.inspect(raw,scenario="camera120-source-cycle",fixture="edited1040")
    def test_missing_negative_media_comparison_is_required_even_with_two_sources(self):
        files=edited_fixture();files.pop(("evidence","original-media-edited-geometry-negative.json"))
        _,m,_=self.capture(files,scenario="camera120-source-cycle",fixture_name="edited1040")
        self.assertFalse(m["required_layers_complete"])
    def test_original_cycle_phase_cannot_satisfy_the_edited_role_contract(self):
        raw,m,_=self.capture(scenario="camera120-source-cycle")
        self.assertTrue(m["required_layers_complete"])
        with self.assertRaises(n.SelectionError):self.inspect(raw,scenario="camera120-source-cycle",fixture="edited1040")

    def test_existing_envelope_header_binds_tar_and_context_without_claiming_decryption(self):
        header={"schema":"motionwright.ci.private-evidence-seal/1","algorithm":"RSA-OAEP-SHA256+AES-256-GCM",
                "recipient_spki_sha256":"b"*64,"context":CURRENT,"nonce":base64.b64encode(b"n"*12).decode(),
                "wrapped_key":base64.b64encode(b"w"*384).decode(),"plaintext_sha256":"c"*64,"plaintext_bytes":2048}
        aad=n.canonical(header);envelope=b"MWCISEAL1\n"+len(aad).to_bytes(4,"big")+aad+b"unit-only-ciphertext-not-encrypted"
        receipt={"recipient_sha256":"b"*64,"selected_tar_sha256":"c"*64,"selected_tar_bytes":2048}
        s.verify_envelope_binding(envelope,receipt,CURRENT)
        with self.assertRaises(n.SelectionError):s.verify_envelope_binding(envelope,{**receipt,"selected_tar_sha256":"d"*64},CURRENT)

if __name__ == "__main__":unittest.main(verbosity=2)
