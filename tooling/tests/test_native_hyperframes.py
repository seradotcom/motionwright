"""G0 contracts only: all synthetic originals are in memory; no CLI/render ran."""
import copy
import json
from pathlib import Path
import types
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_hyperframes_evidence as e
import native_hyperframes_projection as p
import native_hyperframes_e2e as controller
from native_evidence_projection import project,SelectionError,_checked_spec

PIN="9"*64
GEN="00000000-0000-4000-8000-000000000002"
PROJECT="00000000-0000-4000-8000-000000000001"
PROFILE="00000000-0000-4000-8000-000000000003"
def encoded(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
def composition():
    values=[(0,30,40,40,240,120,"#E53935",1000,0),(0,30,100,70,120,60,"#FFFFFF",500,1),
            (30,60,320,180,240,120,"#1E88E5",1000,0),(30,60,380,210,120,60,"#FFFFFF",500,1)]
    return {"schema":"semwright.hyperframes.composition/1","binding":{"project_resource":"project:"+PROJECT,"generation":GEN,"revision":4,"deliverable_id":PROFILE},
        "width":640,"height":360,"fps":30,"frame_count":60,"background":"#101820",
        "clips":[dict(zip(("start_frame","end_frame_exclusive","x","y","width","height","fill","opacity_milli","z_index"),v),id="node-"+f"{i+1:032x}") for i,v in enumerate(values)]}
def inventory(c=None,fingerprint="1"*64):
    c=c or composition();raw=encoded(c);html=b"<html>G0 source-only fixture; no native execution</html>"
    return {"fingerprint":fingerprint,"source_sha256":e.sha(raw),"source_bytes":len(raw),"html_sha256":e.sha(html),"html_bytes":len(html),
        "compiler":"semwright-hyperframes-rect-clips-v3","hyperframes_version":e.VERSION,"hyperframes_commit":e.COMMIT,"composition":c}
def record(argv,stdout=b"",stderr=b""):
    return {"argv_sha256":e.sha(e.js_json(argv)),"exit_code":0,"signal":None,"stdout_sha256":e.sha(stdout),"stdout_bytes":len(stdout),
        "stderr_sha256":e.sha(stderr),"stderr_bytes":len(stderr),"capture_complete":True,"automatic_retry":False}
def receipt_fixture(root="/output/render-test",source=None):
    source=source or inventory();root=Path(root);files={};versions={"node":"v24.19.0","hyperframes":e.VERSION,"browser":"Chrome Fixture","ffmpeg":"ffmpeg Fixture","ffprobe":"ffprobe Fixture"}
    lock={"node_version":versions["node"],"hyperframes_version":e.VERSION,"hyperframes_commit":e.COMMIT,
          "browser_version":versions["browser"],"ffmpeg_version":versions["ffmpeg"],"ffprobe_version":versions["ffprobe"]}
    argv=["/proc/self/exe","/deps/node_modules/hyperframes/bin/hyperframes.mjs","render","/project/source-"+source["fingerprint"],"--composition",".",
        "--format","png-sequence","--fps","30","--workers","1","--no-browser-gpu","--no-best-effort","--sdr","--strict","--quiet","--output",str(root/"frames")]
    intent={"schema":"semwright.hyperframes.native-command/1","argv":argv,"source_fingerprint":source["fingerprint"],"runtime_lock_sha256":PIN,"automatic_retry":False}
    command=record(argv);frames=[]
    for i in range(60):
        raw=f"G0-NOT-A-PNG-{i}".encode();name=f"frames/frame_{i+1:06d}.png";files[str(root/name)]=raw
        frames.append({"index":i,"file":name,"bytes":len(raw),"sha256":e.sha(raw),"width":640,"height":360})
    r={"schema":"semwright.hyperframes.native-render/1","status":"completed","attempt_id":"test-original","fingerprint":source["fingerprint"],
       "source_sha256":source["source_sha256"],"html_sha256":source["html_sha256"],"runtime_lock_sha256":PIN,"hyperframes_version":e.VERSION,"hyperframes_commit":e.COMMIT,
       "frame_count":60,"fps":30,"frames":frames,"native_command":command,"versions":versions,"source_unchanged":True,"pixel_decoding_performed":False,"automatic_retry":False}
    for name,data in (("native-render-receipt.json",encoded(r)),("native-command.intent.json",encoded(intent)),("native-command.json",encoded(command)),
                      ("native-command.stdout",b""),("native-command.stderr",b"")):files[str(root/name)]=data
    for role in ("browser","ffmpeg","ffprobe"):
        raw=(versions[role]+"\n").encode();files[str(root/("preflight-"+role+".stdout"))]=raw
        files[str(root/("preflight-"+role+".stderr"))]=b"";files[str(root/("preflight-"+role+".json"))]=encoded(record([role],raw))
    return root,source,r,lock,files

def memory(files,touched=None):
    def read(path,limit=256*1024*1024):
        key=str(path)
        if touched is not None:touched.append(key)
        if key not in files:raise FileNotFoundError(key)
        data=files[key];assert len(data)<=limit
        return data,{"sha256":e.sha(data),"size":len(data)}
    return read

class OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=composition();cls.first=e.expected_rgba(cls.c,0);cls.second=e.expected_rgba(cls.c,30)
    def pixel(self,raw,x,y):return tuple(raw[(y*640+x)*4:(y*640+x)*4+4])
    def test_exact_geometry_matte_and_opaque_fill(self):
        self.assertEqual(self.pixel(self.first,0,0),(16,24,32,255));self.assertEqual(self.pixel(self.first,40,40),(229,57,53,255))
        self.assertEqual(self.pixel(self.first,39,40),(16,24,32,255));self.assertEqual(self.pixel(self.first,280,40),(16,24,32,255))
    def test_authored_opacity_and_z_are_independently_composed(self):self.assertEqual(self.pixel(self.first,100,70),(242,156,154,255))
    def test_original_scene_cut_does_not_cross_contaminate_second_card(self):
        self.assertEqual(self.pixel(self.second,40,40),(16,24,32,255));self.assertEqual(self.pixel(self.second,320,180),(30,136,229,255))
        self.assertNotEqual(self.first,self.second)
    def test_phase_edit_changes_only_authored_color(self):
        c=composition();c["clips"][0]["fill"]="#00AA66";first=e.expected_rgba(c,0)
        self.assertEqual(self.pixel(first,40,40),(0,170,102,255));self.assertEqual(e.expected_rgba(c,30),self.second)
    def test_outside_canvas_and_timing_drift_are_not_approximated(self):
        for field,value in (("x",500),("start_frame",1),("opacity_milli",499),("z_index",2)):
            c=composition();c["clips"][0][field]=value
            with self.subTest(field=field),self.assertRaises(e.HyperframesEvidenceRejected):e.composition(c)
    def test_unknown_native_property_is_rejected(self):
        c=composition();c["clips"][0]["script"]="arbitrary"
        with self.assertRaises(e.HyperframesEvidenceRejected):e.composition(c)
    def test_duplicate_native_node_identity_is_rejected(self):
        c=composition();c["clips"][1]["id"]=c["clips"][0]["id"]
        with self.assertRaises(e.HyperframesEvidenceRejected):e.composition(c)
    def test_edit_may_not_change_native_geometry_or_node_ids(self):
        before={"source":inventory()};c=composition();c["binding"]["revision"]+=1;c["clips"][0]["fill"]="#00AA66"
        after={"source":inventory(c,"2"*64)};e.verify_edit(before,after)
        after["source"]["composition"]["clips"][0]["x"]+=1
        with self.assertRaises(e.HyperframesEvidenceRejected):e.verify_edit(before,after)

class ReceiptTests(unittest.TestCase):
    def test_byte_bound_synthetic_receipt_has_no_decode_claim(self):
        root,source,r,lock,files=receipt_fixture()
        with patch.object(e,"regular",memory(files)):
            seen,_,frames=e.native_receipt(root,source,PIN,lock)
        self.assertFalse(seen["pixel_decoding_performed"]);self.assertEqual(len(frames),60)
    def test_forged_receipt_claims_never_establish_completion(self):
        edits=[("runtime pin",lambda r:r.update(runtime_lock_sha256="0"*64)),("source pin",lambda r:r.update(source_sha256="0"*64)),
            ("signal",lambda r:r["native_command"].update(signal="SIGKILL")),("retry",lambda r:r.update(automatic_retry=True)),
            ("fake decode",lambda r:r.update(pixel_decoding_performed=True)),("different browser",lambda r:r["versions"].update(browser="Other Chrome")),
            ("exit not known",lambda r:r["native_command"].update(exit_code=None)),("partial stream",lambda r:r["native_command"].update(capture_complete=False)),
            ("missing frame",lambda r:r["frames"].pop()),("wrong frame identity",lambda r:r["frames"][0].update(index=1)),
            ("frame traversal",lambda r:r["frames"][0].update(file="../motionwright.session"))]
        for label,edit in edits:
            root,source,r,lock,files=receipt_fixture();edit(r);files[str(root/"native-render-receipt.json")]=encoded(r)
            with self.subTest(label=label),patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):e.native_receipt(root,source,PIN,lock)
    def test_changed_original_stdout_is_not_trusted_from_json_claim(self):
        root,source,r,lock,files=receipt_fixture();files[str(root/"native-command.stdout")]=b"changed"
        with patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):e.native_receipt(root,source,PIN,lock)
    def test_same_version_with_another_cli_or_extra_flag_is_rejected(self):
        for change in (lambda a:a.__setitem__(1,"/deps/other-cli.mjs"),lambda a:a.__setitem__(12,"--best-effort")):
            root,source,r,lock,files=receipt_fixture();intent=json.loads(files[str(root/"native-command.intent.json")]);change(intent["argv"])
            files[str(root/"native-command.intent.json")]=encoded(intent)
            with patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):e.native_receipt(root,source,PIN,lock)
    def test_capture_namespace_cannot_replace_one_based_export_namespace(self):
        root,source,r,lock,files=receipt_fixture()
        r["frames"][0]["file"]="frames/frame_000000.png"
        files[str(root/"native-render-receipt.json")]=encoded(r)
        with patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):
            e.native_receipt(root,source,PIN,lock)
    def test_original_frame_mutation_is_rejected_before_decode(self):
        root,source,r,lock,files=receipt_fixture();files[str(root/r["frames"][0]["file"])]=b"changed"
        with patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):e.native_receipt(root,source,PIN,lock)
    def test_duplicate_original_png_is_rejected(self):
        root,source,r,lock,files=receipt_fixture();r["frames"][1].update({k:r["frames"][0][k] for k in ("file","bytes","sha256")})
        files[str(root/"native-render-receipt.json")]=encoded(r)
        with patch.object(e,"regular",memory(files)),self.assertRaises(e.HyperframesEvidenceRejected):e.native_receipt(root,source,PIN,lock)

class ProjectionTests(unittest.TestCase):
    def test_closed_json_projection_drops_raw_argv_and_unknown_ticket(self):
        value={"schema":"semwright.hyperframes.native-command/1","source_fingerprint":"1"*64,"runtime_lock_sha256":PIN,"automatic_retry":False,
               "argv":["/secret/credential"],"ticket":"PRIVATE_UNREVIEWED"}
        result=project(value,p.POLICIES["hf-native-intent"])
        self.assertNotIn("argv",result);self.assertNotIn("ticket",result)
    def test_original_json_requires_every_key_reviewed(self):
        value=composition();value["ticket"]="PRIVATE_UNREVIEWED"
        with self.assertRaises(SelectionError):project(value,p.POLICIES["hf-native-source"],exact=True)
    def test_number_types_cannot_turn_boolean_into_pixel_coordinate(self):
        value=composition();value["clips"][0]["x"]=True
        with self.assertRaises(SelectionError):project(value,p.POLICIES["hf-native-source"],exact=True)
    def test_invalid_native_path_or_noncanonical_digest_cannot_be_projected(self):
        frame={"index":0,"file":"../../ticket.session","bytes":1,"sha256":"1"*64,"width":640,"height":360}
        with self.assertRaises(SelectionError):project(frame,p.FRAME,exact=True)
    def test_selected_native_originals_require_exact_upstream_filename_grammar(self):
        frame={"index":0,"file":"frames/frame_000001.png","bytes":1,"sha256":"1"*64,"width":640,"height":360}
        self.assertEqual(project(frame,p.FRAME,exact=True),frame)
        for name in ("frames/000000.png","frames/frame-000000.png","frames/frame_00000.png","frames/frame_000000.png","frames/frame_000061.png"):
            changed=dict(frame,file=name)
            with self.subTest(name=name),self.assertRaises(SelectionError):project(changed,p.FRAME,exact=True)
    def test_full_scenario_requires_both_120_frame_rosters(self):
        self.assertEqual(p.ROLE_CONTRACT[("original","readback","native-frame")],(120,120))
        self.assertEqual(p.ROLE_CONTRACT[("edited","readback","native-frame")],(120,120))
    def test_database_is_required_and_wal_shm_absence_is_explicit(self):
        self.assertEqual(p.ROLE_CONTRACT[("single","persistence","database")],(1,1))
        self.assertEqual(p.ROLE_CONTRACT[("single","persistence","database-wal")],(0,1))
        self.assertEqual(p.ROLE_CONTRACT[("single","persistence","database-shm")],(0,1))
    def test_direct_and_mediated_attempts_never_reuse_a_mutation_identity(self):
        original=controller.mediated_attempt("original","1"*64,PIN);edited=controller.mediated_attempt("edited","2"*64,PIN)
        self.assertNotEqual(original,edited);self.assertNotEqual(original,controller.direct_attempt("original","1"*64))
        self.assertEqual(original,controller.mediated_attempt("original","1"*64,PIN))
    def test_controller_stage_vocabulary_has_no_shell_or_install_callback(self):
        self.assertEqual(controller.STAGES,("prepare-original","render-original","compare-original","prepare-edited","render-edited","compare-edited","selection"))

class SelectionSpecTests(unittest.TestCase):
    def fixture(self):
        args=types.SimpleNamespace(source_root=Path("/checkout"),records=Path("/records"),data_root=Path("/database"),
            direct_project=Path("/direct-source"),mediated_project=Path("/mediated-source"),direct_output=Path("/direct-output"),
            mediated_output=Path("/mediated-output"),dependencies=Path("/dependencies"),runtime_lock_sha256=PIN)
        context={"source_sha":"a"*40,"semwright_sha":controller.SEMWRIGHT,"run_id":"123","run_attempt":"1","scenario":"hyperframes-first-party-direct-mediated-edit"}
        bindings=[{"path":path,"sha256":"3"*64} for path in controller.SOURCE_PATHS];documents={};files={}
        for phase,fp in (("original","1"*64),("edited","2"*64)):
            c=composition()
            if phase=="edited":c["binding"]["revision"]+=1;c["clips"][0]["fill"]="#00AA66"
            source=inventory(c,fp);documents[str(args.records/(phase+"-source.json"))]={"source":source}
            documents[str(args.records/(phase+"-comparison.json"))]={"status":"SOURCE_BOUND_RGBA_PARITY_VERIFIED","source_fingerprint":fp}
            for route,attempt in (("direct",controller.direct_attempt(phase,fp)),("mediated",controller.mediated_attempt(phase,fp,PIN))):
                root=Path("/"+route+"-output")/("render-"+attempt);_,_,r,_,nativefiles=receipt_fixture(str(root),source)
                documents[str(root/"native-render-receipt.json")]=r;files.update(nativefiles)
        return args,context,bindings,documents,files
    def test_closed_spec_contains_exact_roles_and_absence_guards_without_owner_discovery(self):
        args,context,bindings,docs,files=self.fixture();touched=[]
        with patch.object(controller,"document",lambda path:docs[str(path)]),patch.object(p,"regular",memory(files,touched)):
            spec=p.build_spec(args,context,bindings)
        self.assertEqual(len(spec["absence_guards"]),2)
        self.assertEqual(sum(x["role"]=="native-frame" for x in spec["members"]),240)
        self.assertEqual(len({(x["root"],x["relative_path"]) for x in spec["members"]}),len(spec["members"]))
        self.assertTrue(all(not p.excluded(p.member_path(x["relative_path"])) for x in spec["members"]))
        self.assertTrue(all("session" not in x and not x.endswith((".stdout",".stderr")) for x in touched))
        database=next(x for x in spec["members"] if x["role"]=="database")
        self.assertTrue(database["required"]);self.assertIsNone(database["expected_sha256"])
        self.assertEqual(spec["origin"]["upload_authorization"],"NOT_GRANTED_BY_COLLECTOR")
    def test_unreviewed_native_receipt_path_is_rejected_before_its_open(self):
        args,context,bindings,docs,files=self.fixture();touched=[]
        first=next(k for k in docs if k.endswith("native-render-receipt.json"));docs[first]["frames"][0]["file"]="runtime/motionwright.session"
        with patch.object(controller,"document",lambda path:docs[str(path)]),patch.object(p,"regular",memory(files,touched)),self.assertRaises(e.HyperframesEvidenceRejected):p.build_spec(args,context,bindings)
        self.assertTrue(all("motionwright.session" not in path for path in touched))
    def test_omitting_required_source_or_frame_is_not_an_exclusion(self):
        args,context,bindings,docs,files=self.fixture()
        with patch.object(controller,"document",lambda path:docs[str(path)]),patch.object(p,"regular",memory(files)):
            spec=p.build_spec(args,context,bindings)
        spec["members"]=[x for x in spec["members"] if not(x["phase"]=="original" and x["role"]=="native-frame" and x["relative_path"].endswith("000001.png"))]
        with self.assertRaises(SelectionError):_checked_spec(spec,p.POLICIES,p.ROLE_CONTRACT)

class DecoderPinTests(unittest.TestCase):
    def test_wrong_decoder_bytes_stop_before_a_process(self):
        binary=b"G0 decoder bytes; not executable"
        with patch.object(e,"regular",return_value=(binary,{})),patch.object(e.subprocess,"run") as run:
            with self.assertRaises(e.HyperframesEvidenceRejected):e.decode_pngs([b"G0"],Path("/runtime/ffmpeg"),"0"*64,len(binary))
            run.assert_not_called()
        with patch.object(e,"regular") as read,patch.object(e.subprocess,"run") as run:
            with self.assertRaises(e.HyperframesEvidenceRejected):e.decode_pngs([b"G0"],Path("/runtime/ffmpeg"),e.sha(binary),True)
            read.assert_not_called();run.assert_not_called()
    def test_changed_decoder_after_process_is_rejected_without_retry(self):
        binary=b"G0 original decoder"
        result=types.SimpleNamespace(returncode=0,stderr=b"",stdout=b"RGBA"*4)
        with patch.object(e,"WIDTH",2),patch.object(e,"HEIGHT",1),patch.object(e,"FRAMES",2),\
             patch.object(e,"regular",side_effect=[(binary,{}),(b"changed decoder",{})]) as read,\
             patch.object(e.subprocess,"run",return_value=result) as run:
            with self.assertRaises(e.HyperframesEvidenceRejected):e.decode_pngs([b"G0"],Path("/runtime/ffmpeg"),e.sha(binary),len(binary))
            self.assertEqual(read.call_count,2);self.assertEqual(run.call_count,1)
    def test_timeout_still_checks_decoder_and_never_relaunches(self):
        binary=b"G0 original decoder"
        with patch.object(e,"regular",return_value=(binary,{})) as read,\
             patch.object(e.subprocess,"run",side_effect=e.subprocess.TimeoutExpired("ffmpeg",120)) as run:
            with self.assertRaises(e.subprocess.TimeoutExpired):e.decode_pngs([b"G0"],Path("/runtime/ffmpeg"),e.sha(binary),len(binary))
            self.assertEqual(read.call_count,2);self.assertEqual(run.call_count,1)
    def test_exact_owner_path_used_with_both_digest_checks(self):
        binary=b"G0 original decoder";pixels=b"RGBA"*4;decoder=Path("/runtime/ffmpeg")
        result=types.SimpleNamespace(returncode=0,stderr=b"",stdout=pixels)
        with patch.object(e,"WIDTH",2),patch.object(e,"HEIGHT",1),patch.object(e,"FRAMES",2),\
             patch.object(e,"regular",return_value=(binary,{})) as read,patch.object(e.subprocess,"run",return_value=result) as run:
            self.assertEqual(e.decode_pngs([b"first",b"second"],decoder,e.sha(binary),len(binary)),pixels)
            self.assertEqual(run.call_args.args[0][0],str(decoder))
            self.assertTrue(all(call.args[0]==decoder for call in read.call_args_list))
            self.assertEqual(run.call_args.kwargs["input"],b"firstsecond")
            self.assertEqual(read.call_count,2);self.assertEqual(run.call_count,1)
    def test_comparison_policy_requires_checks_without_immutable_exec_claim(self):
        rule=p.comparison_fields["decoder"].rule
        value={"sha256":"1"*64,"bytes":100,"ambient_path_lookup":False,"verified_before_each_decode":True,
               "verified_after_each_decode":True,"immutable_exec_attested":False}
        self.assertEqual(project(value,rule,exact=True),value)
        for field in ("ambient_path_lookup","verified_before_each_decode","verified_after_each_decode","immutable_exec_attested"):
            changed=dict(value);changed[field]=not changed[field]
            with self.subTest(field=field),self.assertRaises(SelectionError):project(changed,rule,exact=True)

if __name__=="__main__":unittest.main()
