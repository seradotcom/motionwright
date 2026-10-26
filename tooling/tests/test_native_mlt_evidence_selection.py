"""G0 contracts over synthetic in-memory bytes; no native or physical PASS."""
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import native_evidence_projection as core
import native_mlt_evidence_selection as mlt
from native_selection_test_fixtures import World,CURRENT,PCM33,WAV33,rewrite_selected,selected_value_change,sample


class MltSelectionTests(unittest.TestCase):
    def world(self):return World("repaired-g2")

    def test_source_contract_original_is_retained_without_neighbor_enumeration(self):
        world = self.world()
        original = world.files[("contracts", "contracts.json")].rstrip() + b"\r\n"
        world.files[("contracts", "contracts.json")] = original
        secret = b"UNLISTED-SOURCE-GATE-NEIGHBOR"
        neighbors = [("contracts", "contracts-00-stderr.log"),
                     ("contracts", "unlisted.json"), ("contracts", "nested/contracts.json")]
        for key in neighbors: world.files[key] = secret
        with world.patched():
            raw, manifest = world.capture(); world.verify(raw)
        row = next(r for r in manifest["members"] if r["role"] == "mlt-source-contracts")
        self.assertEqual((row["root"], row["relative_path"], row["kind"]),
                         ("contracts", "contracts.json", "json-original"))
        self.assertTrue(row["original_bytes"])
        self.assertEqual(row["retained_sha256"], core.sha(original))
        self.assertIn(original, raw)
        self.assertNotIn(secret, raw)
        self.assertTrue(set(neighbors).isdisjoint(world.reads))
        self.assertTrue(set(neighbors).isdisjoint(world.stats))

    def test_absent_source_contract_root_or_file_blocks_selected_completeness(self):
        for missing_root in (False, True):
            with self.subTest(missing_root=missing_root):
                world = self.world()
                del world.files[("contracts", "contracts.json")]
                if missing_root: del world.roots["contracts"]
                with world.patched():
                    raw, manifest = world.capture()
                    self.assertFalse(manifest["required_layers_complete"])
                    with self.assertRaises(core.SelectionError): world.verify(raw)
                row = next(r for r in manifest["members"] if r["role"] == "mlt-source-contracts")
                self.assertEqual(row["state"], "REQUIRED_MISSING")
                self.assertFalse(row.get("original_bytes", False))

    def test_failed_or_unknown_source_contract_is_retained_but_not_promoted(self):
        for status, code in (("FAIL", 1), ("UNKNOWN", 124), ("PASS_WITH_OMISSIONS", 0)):
            with self.subTest(status=status):
                world = self.world()
                world.edit("contracts", "contracts.json", lambda v:v.update(status=status, exit_code=code))
                original = world.files[("contracts", "contracts.json")]
                with world.patched():
                    raw, manifest = world.capture()
                    self.assertTrue(manifest["required_layers_complete"])
                    with self.assertRaisesRegex(core.SelectionError, "native-source-contract-not-passed"):
                        world.verify(raw)
                self.assertIn(original, raw)
                self.assertFalse(manifest["physical_acceptance"])
                self.assertFalse(manifest["human_acceptance"])
                self.assertFalse(manifest["release_authorized"])

    def test_source_contract_changed_after_selection_is_not_complete(self):
        world = self.world(); key = ("contracts", "contracts.json"); calls = 0
        def changed(candidate):
            nonlocal calls
            if candidate == key:
                calls += 1
                if calls == 2: world.files[key] += b" "
        world.on_read = changed
        with world.patched():
            raw, manifest = world.capture()
            self.assertFalse(manifest["required_layers_complete"])
            with self.assertRaises(core.SelectionError): world.verify(raw)
        row = next(r for r in manifest["members"] if r["role"] == "mlt-source-contracts")
        self.assertNotEqual(row["state"], "INCLUDED")

    def test_source_contract_closed_original_rejects_unregistered_fields(self):
        world = self.world(); secret = "UNREGISTERED-RAW-DETAIL"
        world.edit("contracts", "contracts.json", lambda v:v.update(raw_traceback=secret))
        with world.patched(): raw, manifest = world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertNotIn(secret.encode(), raw)
        row = next(r for r in manifest["members"] if r["role"] == "mlt-source-contracts")
        self.assertEqual(row["state"], "CAPTURE_FAILED")

    def test_source_contract_cannot_borrow_another_source_run_or_runner(self):
        changes = (lambda v:v.update(executed_source_sha="e" * 40),
                   lambda v:v.update(tree_sha="e" * 40),
                   lambda v:v.update(runner_sha256="e" * 64),
                   lambda v:v["github"].update(GITHUB_RUN_ID="99999"),
                   lambda v:v["github"].update(GITHUB_RUN_ATTEMPT="2"))
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                world = self.world(); world.edit("contracts", "contracts.json", change)
                with world.patched():
                    raw, manifest = world.capture()
                    self.assertTrue(manifest["required_layers_complete"])
                    with self.assertRaisesRegex(core.SelectionError, "native-source-contract-identity"):
                        world.verify(raw)

    def test_source_contract_pass_text_cannot_mask_omissions_or_incomplete_steps(self):
        changes = (lambda v:v.update(require_complete=False),
                   lambda v:v.update(source_postcheck_error="source changed"),
                   lambda v:v["steps"][0]["counts"].update(skipped=1),
                   lambda v:v["steps"][0].update(status="UNKNOWN"),
                   lambda v:v["steps"].pop())
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                world = self.world(); world.edit("contracts", "contracts.json", change)
                with world.patched():
                    raw, manifest = world.capture()
                    self.assertTrue(manifest["required_layers_complete"])
                    with self.assertRaises(core.SelectionError): world.verify(raw)

    def test_source_contract_boolean_counter_is_not_an_integer(self):
        world = self.world()
        world.edit("contracts", "contracts.json", lambda v:v["steps"][0]["counts"].update(tests_run=True))
        with world.patched(): raw, manifest = world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(any(r["role"] == "mlt-source-contracts" and r["state"] == "CAPTURE_FAILED"
                            for r in manifest["members"]))

    def test_synthetic_graph_requires_three_layers_without_native_acceptance(self):
        world=self.world()
        with world.patched():
            raw,manifest=world.capture();result=world.verify(raw)
        self.assertTrue(manifest["required_layers_complete"])
        self.assertEqual({r["layer"] for r in manifest["members"]},{"readback","persistence","attempt-custody"})
        self.assertTrue(result["source_content_oracle_recomputed"])
        self.assertEqual(result["full_decoded_minus_source_samples"],0)
        self.assertEqual(manifest["native_effects_invoked_by_collector"],0)
        for key in ("physical_acceptance","human_acceptance","release_authorized","native_reopen_performed","packet_PTS_and_AAC_priming_verified","privileged_Broker_grant_verified"):
            self.assertIs(result[key],False)

    def test_every_required_original_group_blocks_completion_when_missing(self):
        cases=[("owner","output/source-segment-0.mkv"),("owner","output/source-segment-1.mkv"),
            ("owner","output/actual-master.mp4"),("owner","output/timeline-video.mkv"),
            ("owner","motionwright-data/motionwright.sqlite3"),
            ("owner","motionwright-data/blobs/sha256/"+core.sha(WAV33)[:2]+"/"+core.sha(WAV33)),
            ("observation","original-master-decoded.s16le"),("observation","original-master-packets.json"),
            ("observation","original-melt-consumer-111-2001.mp4"),("control","intake/original_c_source"),
            ("control","intake/repaired_c_source"),("control","intake/compiled_module")]
        for key in cases:
            with self.subTest(path=key):
                world=self.world();del world.files[key]
                with world.patched():
                    raw,manifest=world.capture()
                    self.assertFalse(manifest["required_layers_complete"])
                    with self.assertRaises(core.SelectionError):world.verify(raw)

    def test_excluded_and_unlisted_secrets_are_never_opened_hashed_or_retained(self):
        world=self.world();secret=b"PRIVATE-SENTINEL-NOT-A-REAL-CREDENTIAL"
        paths=[("owner","home/.ssh/private-key"),("owner","runtime/environ"),("owner","config/driver.json"),
            ("control","logs/native.stderr"),("observation","native.stdout"),("observation","observer-diagnostic.json"),
            ("observation","arbitrary-unlisted-project.json")]
        for key in paths:world.files[key]=secret
        world.edit("observation","final-custody.json",lambda v:v.update(raw_error=secret.decode()))
        with world.patched():raw,manifest=world.capture();world.verify(raw)
        self.assertNotIn(secret,raw)
        self.assertTrue(set(paths).isdisjoint(world.reads));self.assertTrue(set(paths).isdisjoint(world.stats))
        self.assertTrue(all(not row["opened"] and not row["hashed"] and not row["enumerated"] for row in manifest["exclusions"]))

    def test_projection_has_new_identity_and_never_borrows_raw_json_identity(self):
        world=self.world()
        with world.patched():raw,manifest=world.capture()
        rows=[r for r in manifest["members"] if r["kind"]=="json-projection"]
        self.assertTrue(rows)
        self.assertTrue(all(r["retained_sha256"]!=r["expected_sha256"] and r["original_bytes"] is False for r in rows))
        self.assertFalse(manifest["full_original_json_claimed"])

    def test_traversal_in_native_receipt_is_rejected_before_selected_media_open(self):
        world=self.world()
        world.edit("observation","producer/native-multisegment-av-master-evidence.json",lambda v:v["master"]["artifact"].update(path="../home/private-key"))
        with world.patched(),self.assertRaises(core.SelectionError):world.capture()
        self.assertFalse(any("private-key" in path for _,path in world.reads))

    def test_same_inode_alias_cannot_satisfy_two_native_segment_roles(self):
        world=self.world();a=("owner","output/source-segment-0.mkv");b=("owner","output/source-segment-1.mkv")
        world.aliases[b]=a
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(any(r["role"]=="source-segment" and r["state"]!="INCLUDED" for r in manifest["members"]))

    def test_absent_wal_and_shm_have_two_sided_guards_without_reads(self):
        world=self.world()
        with world.patched():raw,manifest=world.capture();world.verify(raw)
        self.assertEqual(len(manifest["absence_guards"]),3)
        self.assertEqual({(row["role"],row["relative_path"]) for row in manifest["absence_guards"]},
                         {("ffv1-stage-plan",mlt.FFV1_PLAN_PATH),
                          ("database-wal","motionwright-data/motionwright.sqlite3-wal"),
                          ("database-shm","motionwright-data/motionwright.sqlite3-shm")})
        for row in manifest["absence_guards"]:
            self.assertTrue(row["absent_before"] and row["absent_after"])
            self.assertFalse(row["opened"] or row["hashed"])
            self.assertNotIn((row["root"],row["relative_path"]),world.reads)

    def test_present_database_journals_are_retained_as_exact_original_bytes(self):
        world=self.world()
        for suffix in ("-wal","-shm"):world.put("owner","motionwright-data/motionwright.sqlite3"+suffix,("original"+suffix).encode())
        with world.patched():raw,manifest=world.capture();world.verify(raw)
        self.assertEqual(len(manifest["absence_guards"]),1)
        guard=manifest["absence_guards"][0]
        self.assertEqual((guard["root"],guard["relative_path"],guard["role"]),
                         ("observation",mlt.FFV1_PLAN_PATH,"ffv1-stage-plan"))
        self.assertTrue(guard["absent_before"] and guard["absent_after"])
        self.assertFalse(guard["opened"] or guard["hashed"])
        rows=[r for r in manifest["members"] if r["role"] in ("database-wal","database-shm")]
        self.assertEqual(len(rows),2);self.assertTrue(all(r["original_bytes"] for r in rows))
        self.assertFalse(manifest["atomic_sqlite_snapshot_claimed"])

    def test_wal_created_after_absence_selection_blocks_without_late_read(self):
        world=self.world();key=("owner","motionwright-data/motionwright.sqlite3-wal")
        def event(candidate):
            if candidate==key and world.stats.count(key)==3:world.files[key]=b"late WAL"
        world.on_stat=event
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["selected_namespace_stable"])
        self.assertFalse(manifest["required_layers_complete"])
        self.assertNotIn(key,world.reads)

    def test_database_mutation_after_read_invalidates_layer(self):
        world=self.world();key=("owner","motionwright-data/motionwright.sqlite3")
        def event(candidate):
            if candidate==key and world.stats.count(key)==2:world.files[key]+=b"changed"
        world.on_stat=event
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(any(r["role"]=="database" and r["state"]=="CHANGED" for r in manifest["members"]))

    def test_missing_sqlite_absence_guard_is_not_a_complete_persistence_contract(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            changed=rewrite_selected(raw,lambda manifest,entries:manifest["absence_guards"].pop())
            with self.assertRaisesRegex(core.SelectionError,"sqlite-companion"):world.verify(changed)

    def test_physical_source_mismatch_blocks_before_evidence_read_despite_clean_git_blob(self):
        world=self.world();world.files[("source","tooling/native_mlt_evidence_selection.py")]=b"hidden physical change"
        with world.patched(),self.assertRaisesRegex(core.SelectionError,"physical-source"):world.collector()
        self.assertTrue(all(root=="source" for root,path in world.reads))

    def test_source_mutation_during_capture_cannot_reuse_preflight_identity(self):
        world=self.world();target=("owner","output/actual-master.mp4")
        def event(key):
            if key==target and world.reads.count(target)==2:world.files[("source","tooling/native_mlt_evidence_selection.py")]+=b"changed"
        world.on_read=event
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["source_bytes_bound"]);self.assertFalse(manifest["required_layers_complete"])

    def test_capture_once_latch_survives_success_and_incomplete_result(self):
        for missing in (False,True):
            world=self.world()
            if missing:del world.files[("owner","output/actual-master.mp4")]
            with world.patched():
                collector=world.collector();collector.capture();reads=len(world.reads)
                with self.assertRaisesRegex(core.SelectionError,"repeat-refused"):collector.capture()
                self.assertEqual(len(world.reads),reads)

    def test_unknown_original_terminal_cannot_borrow_successful_result_json(self):
        world=self.world();world.edit("observation","native-terminal.json",lambda v:v.update(native_terminal="ERROR_OR_UNKNOWN"))
        with world.patched():
            raw,manifest=world.capture();self.assertTrue(manifest["required_layers_complete"])
            with self.assertRaisesRegex(core.SelectionError,"terminal-not-verified"):world.verify(raw)

    def test_nonzero_producer_exit_and_wrong_exact_source_are_rejected(self):
        for role,path,change in (("control","records/producer-exit.json",lambda v:v.update(exit_code=7)),
            ("observation","producer/result.json",lambda v:v.update(motionwright_sha="b"*40))):
            world=self.world();world.edit(role,path,change)
            with world.patched():
                raw,_=world.capture()
                with self.assertRaises(core.SelectionError):world.verify(raw)

    def test_consumer_pid_start_ticks_and_same_open_descriptor_bind_published_bytes(self):
        for change in (lambda v:v.update(process_identity=[111,999]),lambda v:v.update(sha256="0"*64)):
            world=self.world();world.edit("observation","original-melt-consumer-111-2001.json",change)
            with world.patched():
                raw,_=world.capture()
                with self.assertRaises(core.SelectionError):world.verify(raw)

    def test_live_module_must_match_repaired_C_ELF_and_admitted_distro_runtime(self):
        world=self.world();world.edit("observation","original-live-module.json",lambda v:v["live_module"].update(original_repaired_avformat_module_sha256="0"*64))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"module-closure"):world.verify(raw)

    def test_owner_root_identity_cannot_be_rebound_to_other_private_tree(self):
        world=self.world();world.edit("observation","original-live-module.json",lambda v:v["owner_contract"].update(owner_identity=[9,9,16832,1001,1001]))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"owner-root"):world.verify(raw)

    def test_muted_original_right_tail_fails_recomputed_PCM_even_with_reported_PASS(self):
        world=self.world();pcm=bytearray(PCM33)
        for offset in range(len(pcm)-4096,len(pcm),4):pcm[offset+2:offset+4]=b"\0\0"
        digest=world.put("observation","original-master-decoded.s16le",bytes(pcm))
        def content(value):
            value["decoded_pcm_sha256"]=digest;value["oracles"]["full_source_content"]["witness"]["decoded_pcm_sha256"]=digest
        world.edit("observation","source-content.json",content)
        world.edit("observation","producer/result.json",lambda v:v.update(g2_whole_decoded_pcm_sha256=digest))
        with world.patched():
            raw,manifest=world.capture();self.assertTrue(manifest["required_layers_complete"])
            with self.assertRaises(ValueError):world.verify(raw)

    def test_native_decoded_WAV_bytes_are_checked_beyond_matching_receipt_hashes(self):
        for original in (b"not a WAV, even though all receipts agree",WAV33[:-4]):
            with self.subTest(bytes=len(original)):
                world=self.world();digest=world.put("owner","output/decoded-master.wav",original)
                def master(v):
                    for artifact in (v["decoded_audio"],v["master"]["decoded_audio"]):
                        artifact.update(sha256=digest,bytes=len(original))
                world.edit("observation","producer/native-multisegment-av-master-evidence.json",master)
                for path in ("native-terminal.json","final-custody.json"):
                    world.edit("observation",path,lambda v:v["native_result"].update(master_decoded_audio_sha256=digest))
                world.edit("observation","producer/result.json",lambda v:v.update(decoded_audio_sha256=digest))
                with world.patched():
                    raw,manifest=world.capture();self.assertTrue(manifest["required_layers_complete"])
                    with self.assertRaisesRegex(core.SelectionError,"decoded-wav"):world.verify(raw)

    def test_full_pcm_shape_reports_extra_samples_without_trimming(self):
        original=PCM33+b"\0"*(2048*4)
        self.assertEqual(mlt.pcm_shape(WAV33,original,52800),2048)
        self.assertEqual(len(original),(52800+2048)*4)

    def test_selected_context_cannot_borrow_other_attempt_source_or_scenario(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            for key,value in (("source_sha","b"*40),("run_id","999"),("run_attempt","2"),("scenario","film180-g2")):
                with self.subTest(axis=key):
                    changed=rewrite_selected(raw,lambda manifest,entries:manifest["context"].update({key:value}))
                    with self.assertRaises(core.SelectionError):world.verify(changed)

    def test_manifest_cannot_promote_human_physical_privilege_or_atomic_snapshot(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            for key in ("physical_acceptance","human_acceptance","release_authorized","atomic_sqlite_snapshot_claimed","raw_environment_included","automatic_retry"):
                changed=rewrite_selected(raw,lambda manifest,entries:manifest.update({key:True}))
                with self.assertRaises(core.SelectionError):world.verify(changed)

    def test_unlisted_tar_member_and_untyped_projected_fields_are_rejected(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            changed=rewrite_selected(raw,lambda manifest,entries:entries.update({"extra-private-file.bin":b"secret"}))
            with self.assertRaises(core.SelectionError):world.verify(changed)
            changed=rewrite_selected(raw,lambda manifest,entries:selected_value_change(manifest,entries,"final-custody","original",lambda v:v.update(raw_error="secret")))
            with self.assertRaises(core.SelectionError):world.verify(changed)

    def test_missing_owner_root_is_partial_and_not_recreated_or_recursively_read(self):
        world=self.world();del world.roots["owner"]
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertTrue(any(r["state"]!="INCLUDED" and r["role"]=="database" for r in manifest["members"]))
        self.assertFalse(any(root=="owner" for root,path in world.reads))

    def test_oversize_original_is_incomplete_without_trimming_or_new_budget(self):
        world=self.world();key=("owner","output/source-segment-0.mkv")
        class OversizeMetadata(bytes):
            def __len__(self): return core.MAX_FILE+1
        original=world.files[key];world.files[key]=OversizeMetadata(original)
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertEqual(bytes(world.files[key]),original)
        self.assertEqual(core.MAX_FILE,256*1024*1024);self.assertEqual(core.MAX_TOTAL,512*1024*1024)



class SealedSelectionTests(unittest.TestCase):
    """Ephemeral in-memory test keys; never read a recipient's private key."""
    @classmethod
    def setUpClass(cls):
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.test_key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        der=cls.test_key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient={"spki_der_base64":base64.b64encode(der).decode(),"sha256":core.sha(der)}

    def bundle(self,world):
        import ci_private_evidence as sealed
        raw,manifest=world.capture()
        envelope=sealed.encrypt_snapshot(raw,self.recipient,CURRENT)
        film=world.scenario=="film180-g2"
        public=dict(schema="motionwright.native.film180-g2-public/2" if film else "motionwright.native.mlt-g2-public/2",
            context=copy.deepcopy(CURRENT),scope="film180-original-and-edited-content-and-live-module-only" if film else "source-content-and-live-module-only",
            native_terminal="SUCCESS_REPORTED",producer_exit_code=0,job_outcome_at_seal="success",
            source_content_and_live_module_verified=True,sealed_originals_sha256=core.sha(envelope),
            sealed_originals_bytes=len(envelope),recipient_spki_sha256=self.recipient["sha256"],
            custody_scope="explicit-source-bound-selected-layers/2",source_unchanged=True,
            selected_layers_complete=True,selected_intake_verified=True,selected_manifest_sha256=core.sha(core.canonical(manifest)),
            full_original_json_claimed=False,atomic_sqlite_snapshot_claimed=False,native_operation_retried=False,
            packet_PTS_and_AAC_priming_verified=False,physical_AV_master_acceptance=False,human_acceptance=False,
            release_authorized=False,upload_authorization="NOT_GRANTED_BY_COLLECTOR")
        return envelope,public

    def test_existing_crypto_roundtrip_preserves_four_context_axes_for_both_scenarios(self):
        for scenario in ("repaired-g2","film180-g2"):
            with self.subTest(scenario=scenario):
                world=World(scenario)
                with world.patched():
                    envelope,public=self.bundle(world)
                    result=mlt.verify_sealed_selection(envelope,self.test_key,public)
                self.assertTrue(result["selected_layers_complete"])
                self.assertIs(result["physical_acceptance"],False)
                self.assertEqual({k:result["context"][k] for k in CURRENT},CURRENT)
                self.assertEqual(set(public["context"]),set(CURRENT))

    def test_ciphertext_public_recipient_manifest_and_context_cannot_be_swapped(self):
        world=World("repaired-g2")
        with world.patched():
            envelope,public=self.bundle(world)
            cases=[("sealed_originals_sha256","b"*64),("sealed_originals_bytes",len(envelope)+1),
                   ("recipient_spki_sha256","c"*64),("selected_manifest_sha256","d"*64)]
            for key,value in cases:
                changed=copy.deepcopy(public);changed[key]=value
                with self.subTest(field=key),self.assertRaises(core.SelectionError):
                    mlt.verify_sealed_selection(envelope,self.test_key,changed)
            for key,value in (("source_sha","b"*40),("semwright_sha","c"*40),("run_id","999"),("run_attempt","2")):
                changed=copy.deepcopy(public);changed["context"][key]=value
                with self.subTest(axis=key),self.assertRaises(core.SelectionError):
                    mlt.verify_sealed_selection(envelope,self.test_key,changed)

    def test_public_scope_and_terminal_must_be_typed_and_cannot_promote_UNKNOWN(self):
        world=World("repaired-g2")
        with world.patched():
            envelope,public=self.bundle(world)
            for key,value in (("producer_exit_code",False),("producer_exit_code",1),
                    ("native_terminal","ERROR_OR_UNKNOWN"),("selected_intake_verified",False),
                    ("physical_AV_master_acceptance",True),("release_authorized",True),
                    ("upload_authorization","GRANTED"),("unexpected_environment",{"CANARY":"not a credential"})):
                changed=copy.deepcopy(public);changed[key]=value
                with self.subTest(field=key),self.assertRaises(core.SelectionError):
                    mlt.verify_sealed_selection(envelope,self.test_key,changed)





class Ffv1DiagnosticWorld(World):
    """Synthetic originals with external admission; never a Host/native witness."""
    def __init__(self, plan):
        super().__init__("repaired-g2")
        plan=copy.deepcopy(plan)
        timeline=self.document("observation","producer/native-multisegment-timeline-evidence.json")
        plan.update({key:timeline[key] for key in mlt.IDENTITY})
        plan["input_sha256"]=timeline["source"]["input_sha256"]
        plan["owner_output_namespace"]="mw-mlt-"+plan["input_sha256"][:20]
        for index,slot in enumerate(plan["slots"]):
            slot["args"]["output_path"]=plan["owner_output_namespace"]+f"-s{index:02}-ffv1.mkv"
        self.plan=plan
        self.plan_original=core.canonical(plan).rstrip()+b"\r\n"
        self.put("observation",mlt.FFV1_PLAN_PATH,self.plan_original)
        inputs=self.document("observation","input-custody.json")
        inputs["original_source"]=sample(mlt.FFV1_REFERENCE_SOURCE)
        inputs["original_source"]["original_wav_sha256"]=self.pins["REFERENCE_WAV"]
        self.put("observation","input-custody.json",inputs)
        seed=dict(project_id=plan["project_resource"][8:],resource=plan["project_resource"],
            generation=plan["generation"],revision=plan["revision"],master_profile_id=plan["deliverable_id"],
            width=1280,height=720,fps_num=30,fps_den=1,scene_count=33,deliverable_count=3)
        stdout=core.canonical(seed).rstrip()+b"\r\n"
        self.put("observation",mlt.FFV1_SEED_STDOUT_PATH,stdout)
        self.put("observation",mlt.FFV1_SEED_STDERR_PATH,b"")
        def stream(path,raw):
            return dict(relative_path=path,sha256=core.sha(raw),bytes=len(raw),
                returned_buffer_sha256=core.sha(raw),returned_buffer_bytes=len(raw),
                prefix_truncated=False,retained=True,stream_eof_attested=False)
        terminal=dict(schema=mlt.FFV1_SEED_CAPTURE_SCHEMA,
            context={**CURRENT,"scenario":"repaired-g2"},
            argv=[str(Path(self.roots["source"])/"target"/"debug"/"examples"/"native-mlt-sequence-e2e"),
                  "seed",str(Path(self.roots["owner"])/"motionwright-data"/"motionwright.sqlite3")],
            timeout_seconds=30,stdout=stream(mlt.FFV1_SEED_STDOUT_PATH,stdout),
            stderr=stream(mlt.FFV1_SEED_STDERR_PATH,b""),exit_code=0,deadline_exceeded=False,
            error_class=None,error=None,automatic_retry=False,
            capture_scope="existing-run-sanitized-json-callback-before-parse",
            before_assemble=True,native_outcome_inferred=False)
        self.put("observation",mlt.FFV1_SEED_TERMINAL_PATH,terminal)
        with self.patched():
            self.admission=mlt.read_ffv1_admission(roots=self.roots,current=CURRENT,
                                                  source_bindings=self.bindings)

    def collector(self):
        return mlt.Selection(roots=self.roots,current=CURRENT,scenario=self.scenario,
            source_bindings=self.bindings,role_contract=mlt.mlt_contract(seed_diagnostics=True),
            json_policies=mlt.policies(self.scenario),diagnostic_admission=self.admission)

    def verify(self, raw):
        return mlt.verify_decrypted_selection(raw,current=CURRENT,scenario=self.scenario,
                                              expected_diagnostic_admission=self.admission)


class Ffv1OriginalSelectionTests(unittest.TestCase):
    """Synthetic custody-only fixtures; no Host/native/media acceptance."""
    def plan(self):
        identity="00000000-0000-0000-0000-000000000001"
        namespace="mw-mlt-"+"a"*20
        return dict(schema="motionwright.native.ffv1-stage-plan/1",
            project_resource="project:"+identity,generation=identity,revision=102,
            deliverable_id=identity,input_sha256="a"*64,owner_output_namespace=namespace,
            total_frames=33,slots=[dict(slot=i,frame_count=n,args=dict(root="output",
                manifest_path="render-"+str(i)*32+"/artifact-manifest.json",
                expected_manifest_sha256=("b" if i==0 else "c")*64,
                output_path=f"{namespace}-s{i:02}-ffv1.mkv",max_bytes=512*1024*1024))
                for i,n in enumerate((32,1))],
            scope="canonical-preflight-destinations-before-original-FFV1-call",
            original_host_stdin=False,FFV1_call_started=False,
            native_operation_retried=False,physical_acceptance=False)

    def world_with_plan(self):
        world=Ffv1DiagnosticWorld(self.plan())
        return world,world.plan,world.plan_original

    def typed_original_documents(self, plan):
        """Literal Rust-producer wire shapes, synthetic bytes and no Host authority."""
        args=plan["slots"][0]["args"];output=args["output_path"]
        simple_error=dict(code="BackendFailed",message="synthetic original failure",outcome_known=False)
        sdk_error={**simple_error,"candidates":["synthetic-candidate"],
            "progress":dict(completed_steps=0,failed_step="synthetic-encode",
                            state="partial_success_or_unknown"),
            "native_diagnostic":dict(phase="HostRead",reason="UnexpectedEof",
                exit_code=None,signal=None,stderr_tail_sha256="d"*64,
                stderr_tail_bytes=0,stderr_tail_truncated=False)}
        host=dict(schema="semwright.ffv1-host-original/1",host_stage="host",
            state="wait-returned-error",output_path=output,runtime_job_id="synthetic-job",
            runtime_job_id_exposed=True,host_exit_code=None,
            stdin=dict(root="output",path=output+".ffv1-host.stdin.original.bin",
                       sha256="e"*64,bytes=100),
            stdin_scope="same-serialized-Vec-supplied-to-existing-AV-job-start",
            stdin_delivery_confirmed=None,stdout=None,stderr=None,original_error=sdk_error,
            stream_eof_attested=False,native_execution_retried=False,physical_acceptance=False,
            capture_scope="same-existing-Host-return-before-parse-and-public-redaction")
        def stream(stage,name,body):
            return dict(root="output",path=f"{output}.ffv1-{stage}.{name}.original.bin",
                sha256=core.sha(body),bytes=len(body),returned_buffer_bytes=len(body),
                returned_buffer_sha256=core.sha(body),diagnostic_prefix_truncated=False,
                retained=True,upstream_stream_eof_attested=False)
        process=dict(schema="semwright.ffv1-process-original/1",stage="encode",
            output_path=output,source_manifest_sha256=args["expected_manifest_sha256"],
            source_capture_retained_limit=16384,source_capture_total_limit=262144,
            diagnostic_prefix_limit=16384,
            spec=dict(encoding="unix-os-bytes-hex",executable=b"/synthetic/ffmpeg".hex(),
                argv=[b"-version".hex()],cwd=b"/synthetic/output".hex(),timeout_millis=1000,
                cpu_seconds=30,address_space_bytes=536870912,
                environment_included=False,native_execution_retried=False),
            terminal=dict(process_result_received=True,exit_code=17,term_signal=None,
                cancelled=False,timed_out=False,output_exceeded=False,
                stdout=stream("encode","stdout",b""),
                stderr=stream("encode","stderr",b"\xffsynthetic-ffv1-error"),
                original_error=None),
            native_execution_retried=False,stream_eof_attested=False,
            capture_scope="existing-ProcessResult-before-checked-or-parse",physical_acceptance=False)
        custody=dict(schema="semwright.ffv1-process-custody/1",output_path=output,
            source_manifest_sha256=args["expected_manifest_sha256"],
            observed_stages=["zscale-help","encode"],secondary_failures=["stream-retention"],
            original_operation_succeeded=False,original_error=simple_error,
            stream_eof_attested=False,native_execution_retried=False,physical_acceptance=False)
        catalog=copy.deepcopy(host)
        catalog.update(host_stage="catalog",state="completed",runtime_job_id=None,
            runtime_job_id_exposed=False,host_exit_code=2,original_error=None,
            stdin=dict(root="output",path=output+".ffv1-catalog.stdin.original.bin",
                       sha256=core.sha(b""),bytes=0),
            stdin_scope="same-empty-Vec-supplied-to-existing-discover-call",
            stdin_delivery_confirmed=True)
        def host_stream(name,body):
            return dict(root="output",path=f"{output}.ffv1-catalog.{name}.original.bin",
                sha256=core.sha(body),bytes=len(body),delivered_buffer_bytes=len(body),
                delivered_buffer_sha256=core.sha(body),diagnostic_prefix_truncated=False,
                retained=True,stream_eof_attested=False)
        catalog["stdout"]=host_stream("stdout",b"\xffsynthetic-not-json")
        catalog["stderr"]=host_stream("stderr",b"")
        return [
            ("ffv1-host-terminal","output/"+output+".ffv1-host.terminal.original.json",host),
            ("ffv1-catalog-terminal","output/"+output+".ffv1-catalog.terminal.original.json",catalog),
            ("ffv1-process-terminal","output/"+output+".ffv1-encode.terminal.original.json",process),
            ("ffv1-process-custody","output/"+output+".ffv1-process-custody.original.json",custody),
        ]

    def test_failed_first_ffv1_keeps_originals_without_final_timeline(self):
        world,plan,plan_raw=self.world_with_plan()
        del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        output=plan["slots"][0]["args"]["output_path"]
        binary=b"\xff\0original-ffmpeg-stderr\r\n"
        member="output/"+output+".ffv1-encode.stderr.original.bin"
        world.put("owner",member,binary)
        with world.patched(): raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        row=next(r for r in manifest["members"] if r["relative_path"]==member)
        self.assertEqual(row["state"],"INCLUDED");self.assertTrue(row["original_bytes"])
        self.assertEqual(row["retained_sha256"],core.sha(binary))
        self.assertIn(binary,raw);self.assertIn(plan_raw,raw)

    def test_closed_roster_ignores_neighbor_files_and_opaque_runtime_state(self):
        world,plan,_=self.world_with_plan();output=plan["slots"][0]["args"]["output_path"]
        neighbors=[("owner","output/"+output+".unlisted-secret"),
                   ("owner","runtime/diagnostic.secret"),("owner","scratch/original.stderr")]
        for key in neighbors:world.files[key]=b"UNLISTED-PRIVATE-CANARY"
        with world.patched():raw,_=world.capture()
        self.assertTrue(set(neighbors).isdisjoint(world.reads));self.assertTrue(set(neighbors).isdisjoint(world.stats))
        self.assertNotIn(b"UNLISTED-PRIVATE-CANARY",raw)
        self.assertEqual(len(mlt.ffv1_diagnostic_paths(plan)),54)

    def test_cross_namespace_plan_cannot_select_another_original(self):
        world,plan,_=self.world_with_plan()
        plan["slots"][0]["args"]["output_path"]="mw-mlt-"+"d"*20+"-s00-ffv1.mkv"
        world.put("observation",mlt.FFV1_PLAN_PATH,core.canonical(plan))
        escaped="output/"+plan["slots"][0]["args"]["output_path"]+".ffv1-host.stdout.original.bin"
        world.put("owner",escaped,b"FOREIGN-ORIGINAL")
        with world.patched():raw,manifest=world.capture()
        self.assertFalse(manifest["required_layers_complete"])
        self.assertNotIn(("owner",escaped),world.reads);self.assertNotIn(b"FOREIGN-ORIGINAL",raw)
        with self.assertRaises(core.SelectionError):mlt.ffv1_diagnostic_paths(plan)

    def test_closed_slot_order_and_source_ranges_are_required(self):
        for mutate in (lambda p:p["slots"].reverse(),
                       lambda p:p["slots"][0].update(frame_count=31),
                       lambda p:p["slots"][0]["args"].update(output_path="../secret.mkv")):
            plan=self.plan();mutate(plan)
            with self.assertRaises((ValueError,TypeError,core.SelectionError)):mlt.ffv1_diagnostic_paths(plan)

    def test_actual_original_stdin_binds_to_plan_without_claiming_dispatch(self):
        plan=self.plan();value=dict(command="driver.mlt-video.frames.encode",descriptor_sha256="d"*64,args=plan["slots"][0]["args"])
        raw=core.canonical(value).rstrip()+b"\r\n"
        result=mlt.verify_ffv1_original_stdin(plan,0,raw)
        self.assertEqual(result["sha256"],core.sha(raw));self.assertEqual(result["bytes"],len(raw))
        self.assertIs(result["actual_dispatch_verified"],False);self.assertIs(result["native_outcome_known"],False)
        value["args"]=copy.deepcopy(value["args"]);value["args"]["expected_manifest_sha256"]="e"*64
        with self.assertRaises(core.SelectionError):mlt.verify_ffv1_original_stdin(plan,0,core.canonical(value))

    def test_empty_host_stderr_is_an_original_zero_byte_file(self):
        world,plan,_=self.world_with_plan();output=plan["slots"][0]["args"]["output_path"]
        member="output/"+output+".ffv1-host.stderr.original.bin";world.put("owner",member,b"")
        with world.patched():_raw,manifest=world.capture()
        row=next(r for r in manifest["members"] if r["relative_path"]==member)
        self.assertEqual(row["state"],"INCLUDED");self.assertEqual(row["bytes"],0)
        self.assertEqual(row["retained_sha256"],core.sha(b""));self.assertTrue(row["original_bytes"])

    def test_missing_or_oversize_stream_is_unobserved_without_trimming(self):
        world,plan,_=self.world_with_plan();output=plan["slots"][0]["args"]["output_path"]
        member="output/"+output+".ffv1-encode.stderr.original.bin"
        original=b"x"*16385;world.put("owner",member,original)
        with world.patched():_raw,manifest=world.capture()
        row=next(r for r in manifest["members"] if r["relative_path"]==member)
        self.assertNotEqual(row["state"],"INCLUDED");self.assertEqual(world.files[("owner",member)],original)
        self.assertFalse(row.get("original_bytes",False))

    def test_optional_ffv1_plan_absence_preserves_existing_complete_selection(self):
        world=World("repaired-g2");key=("observation",mlt.FFV1_PLAN_PATH)
        with world.patched():
            raw,manifest=world.capture()
            result=world.verify(raw)
        self.assertTrue(manifest["required_layers_complete"])
        self.assertNotIn(key,world.reads)
        self.assertFalse(any(row["role"]=="ffv1-stage-plan" for row in manifest["members"]))
        guards=[row for row in manifest["absence_guards"] if row["role"]=="ffv1-stage-plan"]
        self.assertEqual(len(guards),1)
        guard=guards[0]
        self.assertEqual((guard["root"],guard["relative_path"]),key)
        self.assertIs(guard["absent_before"],True);self.assertIs(guard["absent_after"],True)
        self.assertIs(guard["opened"],False);self.assertIs(guard["hashed"],False)
        self.assertIs(manifest["physical_acceptance"],False)
        self.assertIs(result["physical_acceptance"],False)

    def test_ffv1_absence_guards_reject_late_plan_or_stream_without_read(self):
        for kind in ("plan","stream"):
            with self.subTest(kind=kind):
                if kind=="plan":
                    world=World("repaired-g2");key=("observation",mlt.FFV1_PLAN_PATH)
                    late=core.canonical(self.plan())
                else:
                    world,plan,_=self.world_with_plan()
                    key=("owner","output/"+plan["slots"][0]["args"]["output_path"]+".ffv1-encode.stderr.original.bin")
                    late=b"\xfflate-original-never-selected"
                def event(candidate):
                    if candidate==key and world.stats.count(key)==3:
                        world.files[key]=late
                world.on_stat=event
                with world.patched():_raw,manifest=world.capture()
                self.assertFalse(manifest["selected_namespace_stable"])
                self.assertFalse(manifest["required_layers_complete"])
                self.assertNotIn(key,world.reads)
                guard=next(row for row in manifest["absence_guards"]
                           if (row["root"],row["relative_path"])==key)
                self.assertIs(guard["absent_before"],True)
                self.assertIs(guard["absent_after"],False)
                self.assertFalse(guard["opened"] or guard["hashed"])

    def test_ffv1_closed_json_originals_preserve_full_typed_terminal_bytes(self):
        world,plan,_=self.world_with_plan();expected={}
        for role,path,value in self.typed_original_documents(plan):
            original=core.canonical(value).rstrip()+b"\r\n"
            expected[path]=(role,original)
            world.put("owner",path,original)
        with world.patched():raw,manifest=world.capture();world.verify(raw)
        self.assertTrue(manifest["required_layers_complete"])
        for path,(role,original) in expected.items():
            with self.subTest(role=role):
                row=next(row for row in manifest["members"] if row["relative_path"]==path)
                self.assertEqual(row["state"],"INCLUDED")
                self.assertEqual(row["kind"],"diagnostic-json-original")
                self.assertEqual(row["policy"],mlt.FFV1_SIDECAR_POLICY_BY_ROLE[role])
                self.assertTrue(row["original_bytes"])
                self.assertEqual(row["retained_sha256"],core.sha(original))
                self.assertEqual(row["retained_bytes"],len(original))
                self.assertIn(original,raw)
                self.assertEqual(world.files[("owner",path)],original)
        self.assertFalse(manifest["physical_acceptance"])
        self.assertFalse(manifest["automatic_retry"])

    def test_ffv1_closed_json_rejects_extra_fields_and_capture_claims(self):
        changes=(
            ("unregistered-host-field",0,lambda v:v.update(unregistered_secret="NOT-ADMITTED")),
            ("unknown-diagnostic-phase",0,lambda v:v["original_error"]["native_diagnostic"].update(phase="Execute")),
            ("source-error-control-text",0,lambda v:v["original_error"].update(message="line one\nline two")),
            ("invented-process-eof",2,lambda v:v.update(stream_eof_attested=True)),
            ("invented-native-retry",2,lambda v:v.update(native_execution_retried=True)),
            ("invented-acceptance",3,lambda v:v.update(physical_acceptance=True)),
        )
        for name,index,change in changes:
            with self.subTest(case=name):
                world,plan,_=self.world_with_plan()
                role,path,value=self.typed_original_documents(plan)[index];change(value)
                original=core.canonical(value)
                world.put("owner",path,original)
                with world.patched():raw,manifest=world.capture()
                row=next(row for row in manifest["members"] if row["relative_path"]==path)
                self.assertEqual(row["kind"],"diagnostic-json-original")
                self.assertEqual(row["policy"],mlt.FFV1_SIDECAR_POLICY_BY_ROLE[role])
                self.assertNotEqual(row["state"],"INCLUDED")
                self.assertFalse(row.get("original_bytes",False))
                self.assertFalse(manifest["required_layers_complete"])
                self.assertNotIn(original,raw)
                self.assertEqual(world.files[("owner",path)],original)


class Ffv1ExternalAdmissionTests(unittest.TestCase):
    def world(self):
        return Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())

    def test_external_admission_fixes_exact_roster_limits_source_and_original_plan(self):
        world=self.world();admission=world.admission
        self.assertEqual(admission["stage_plan"]["original"]["sha256"],core.sha(world.plan_original))
        self.assertEqual(admission["stage_plan"]["original"]["bytes"],len(world.plan_original))
        self.assertEqual(admission["source_bindings"],sorted(world.bindings,key=lambda row:row["path"]))
        entries=admission["diagnostic_contract"]["entries"]
        self.assertEqual(len(entries),54)
        self.assertEqual(len({(row["root"],row["relative_path"]) for row in entries}),54)
        self.assertEqual(sum(row["maximum"] for row in entries)+65536,3997696)
        self.assertEqual(sum(row["type"]=="typed-terminal" for row in entries),18)
        self.assertEqual(admission["selected_optional_members"],55)
        self.assertEqual(admission["fixture_identity"]["project_resource"],world.plan["project_resource"])

    def test_external_admission_rejects_foreign_context_fixture_plan_and_policy(self):
        world=self.world()
        cases=(
            ("run",lambda a:a["context"].update(run_id="999")),
            ("source",lambda a:a["context"].update(source_sha="f"*40)),
            ("attempt",lambda a:a["context"].update(run_attempt="2")),
            ("fixture",lambda a:a["fixture_identity"].update(revision=a["fixture_identity"]["revision"]+1)),
            ("source-binding",lambda a:a["source_bindings"][0].update(sha256="f"*64)),
            ("path",lambda a:a["diagnostic_contract"]["entries"][0].update(relative_path="output/foreign.bin")),
            ("policy",lambda a:a["diagnostic_contract"]["entries"][3].update(policy="ffv1_process_terminal")),
            ("budget",lambda a:a["diagnostic_contract"]["entries"][0].update(maximum=1)),
            ("roster",lambda a:a["diagnostic_contract"]["entries"].pop()),
            ("plan-pin-layout",lambda a:a["stage_plan"]["original"].update(relative_path="producer/foreign.json")),
        )
        for label,change in cases:
            with self.subTest(case=label):
                candidate=copy.deepcopy(world.admission);change(candidate)
                with self.assertRaises(core.SelectionError):
                    mlt.checked_ffv1_admission(candidate,current=CURRENT,source_bindings=world.bindings)

    def test_capture_freezes_admission_and_uses_explicit_diagnostic_kinds(self):
        world=self.world();entry=world.admission["diagnostic_contract"]["entries"][1]
        path=entry["relative_path"]
        body=b'{"looks":"like-json"}\xff\0'
        world.put(entry["root"],path,body)
        with world.patched():
            collector=world.collector()
            world.admission["diagnostic_contract"]["entries"][1]["relative_path"]="output/late.bin"
            raw,manifest=collector.capture()
        self.assertEqual(manifest["schema"],core.MANIFEST_SCHEMA_V3)
        row=next(row for row in manifest["members"] if row["relative_path"]==path)
        self.assertEqual(row["kind"],"diagnostic-original")
        self.assertEqual(row["diagnostic_type"],"stdout")
        self.assertIs(row["required"],True)
        self.assertIn(body,raw)
        self.assertNotIn(("owner","output/late.bin"),world.reads)

    def test_intake_keeps_opaque_diagnostic_bytes_and_requires_the_external_contract(self):
        world=self.world();entry=world.admission["diagnostic_contract"]["entries"][1]
        body=b'{"not":"an-admitted-JSON-policy"}\xff'
        world.put("owner",entry["relative_path"],body)
        with world.patched():
            raw,_=world.capture()
            with self.assertRaises(core.SelectionError):
                mlt.verify_decrypted_selection(raw,current=CURRENT,scenario="repaired-g2")
            intake=mlt.Intake(raw,CURRENT,"repaired-g2",mlt.mlt_contract(seed_diagnostics=True),mlt.policies("repaired-g2"),
                             expected_diagnostic_admission=world.admission)
            self.assertEqual(intake.original("ffv1-host-stdout","original","attempt-custody"),body)
            self.assertTrue(world.verify(raw)["selected_layers_complete"])

    def test_legacy_capture_cannot_silently_include_admitted_diagnostics(self):
        world=self.world()
        with world.patched():
            legacy=mlt.Selection(roots=world.roots,current=CURRENT,scenario="repaired-g2",
                source_bindings=world.bindings,role_contract=mlt.mlt_contract(),
                json_policies=mlt.policies("repaired-g2"))
            with self.assertRaisesRegex(core.SelectionError,"diagnostic-admission-required"):
                legacy.capture()
        diagnostic_paths={row["relative_path"] for row in world.admission["diagnostic_contract"]["entries"]}
        self.assertFalse(any(root=="owner" and path in diagnostic_paths for root,path in world.reads))

    def test_manifest_cannot_supply_or_replace_its_own_diagnostic_authority(self):
        world=self.world()
        with world.patched():
            raw,_=world.capture()
            foreign=copy.deepcopy(world.admission)
            foreign["stage_plan"]["original"]["sha256"]="f"*64
            with self.assertRaisesRegex(core.SelectionError,"original-plan"):
                mlt.verify_decrypted_selection(raw,current=CURRENT,scenario="repaired-g2",
                                                expected_diagnostic_admission=foreign)
            def alter(manifest,_):
                manifest["diagnostic_contract"]["entries"][0]["maximum"]=1
                manifest["diagnostic_contract_sha256"]=core.sha(core.canonical(manifest["diagnostic_contract"]))
            altered=rewrite_selected(raw,alter)
            with self.assertRaises(core.SelectionError):
                world.verify(altered)

    def test_input_original_change_after_external_admission_is_rejected(self):
        world=self.world()
        world.edit("observation","input-custody.json",lambda value:value.update(official_runtime_receipt_sha256="f"*64))
        with world.patched():
            raw,_=world.capture()
            with self.assertRaisesRegex(core.SelectionError,"original-input"):
                world.verify(raw)

    def test_seed_original_failure_or_truncation_cannot_admit_a_plan(self):
        world=self.world()
        cases=(
            ("nonzero",lambda v:v.update(exit_code=7)),
            ("timeout",lambda v:v.update(exit_code=None,deadline_exceeded=True)),
            ("capture-error",lambda v:v.update(error_class="OSError",error="original seed failed")),
            ("prefix",lambda v:v["stdout"].update(prefix_truncated=True)),
            ("bytes",lambda v:v["stdout"].update(sha256="f"*64)),
            ("missing-stderr-custody",lambda v:v["stderr"].update(retained=False)),
            ("foreign-binary",lambda v:v["argv"].__setitem__(0,"/foreign/native-mlt-sequence-e2e")),
            ("foreign-owner-database",lambda v:v["argv"].__setitem__(2,"/foreign/motionwright.sqlite3")),
        )
        original=world.files[("observation",mlt.FFV1_SEED_TERMINAL_PATH)]
        for label,change in cases:
            with self.subTest(case=label):
                value=core.strict(original);change(value)
                world.put("observation",mlt.FFV1_SEED_TERMINAL_PATH,value)
                with world.patched(),self.assertRaises(core.SelectionError):
                    mlt.read_ffv1_admission(roots=world.roots,current=CURRENT,source_bindings=world.bindings)
        world.put("observation",mlt.FFV1_SEED_TERMINAL_PATH,original)

    def test_v3_offline_full_intake_rejects_missing_or_swapped_external_admission(self):
        world=self.world()
        # Use the existing crypto fixture, which never reads an operator key.
        SealedSelectionTests.setUpClass()
        key,recipient=SealedSelectionTests.test_key,SealedSelectionTests.recipient
        import native_mlt_g2_custody as custody
        import ci_private_evidence as sealed
        with world.patched():
            raw,manifest=world.capture()
            encrypted=sealed.encrypt_snapshot(raw,recipient,CURRENT)
            final={"schema":"motionwright.native.mlt-g2-custody/1",
                "terminal_scope":"canonical_assemble_command","native_terminal":"SUCCESS_REPORTED",
                "native_operation_retried":False,"source_content_and_live_module_verified":True,
                "secondary_failures":[],"packet_PTS_and_AAC_priming_verified":False,
                "physical_AV_master_acceptance":False,"human_acceptance":False}
            public=custody.selected_projection(CURRENT,final,0,"success",encrypted,recipient,manifest,
                producer_started=True,source_unchanged=True,intake_verified=True,
                diagnostic_admission=world.admission)
            self.assertEqual(public["schema"],"motionwright.native.mlt-g2-public/3")
            self.assertIs(public["diagnostic_payloads_redacted"],False)
            with self.assertRaises(core.SelectionError):
                mlt.verify_sealed_selection(encrypted,key,public)
            wrong=copy.deepcopy(world.admission);wrong["stage_plan"]["original"]["sha256"]="f"*64
            with self.assertRaises(core.SelectionError):
                mlt.verify_sealed_selection(encrypted,key,public,expected_diagnostic_admission=wrong)
            result=mlt.verify_sealed_selection(encrypted,key,public,expected_diagnostic_admission=world.admission)
            self.assertEqual(result["schema"],"motionwright.native.mlt-selected-intake/3")
            self.assertFalse(result["physical_acceptance"])


class Ffv1PlanAbsenceTests(unittest.TestCase):
    """Synthetic filesystem adapter; the existing Core performs one capture."""
    def world(self):
        return Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())

    def absent_collector(self, world):
        return mlt.Selection(roots=world.roots,current=CURRENT,scenario="repaired-g2",
            source_bindings=world.bindings,role_contract=mlt.mlt_contract(),
            json_policies=mlt.policies("repaired-g2"),ffv1_stage_plan_absent=True)

    def test_reader_converts_path_roots_before_the_existing_core_handles(self):
        world=self.world()
        roots={name:Path(path) for name,path in world.roots.items()}
        observed=[]
        with world.patched():
            actual=core.RootHandles
            def typed_handles(paths):
                self.assertTrue(all(type(value) is str for value in paths.values()))
                observed.append(dict(paths))
                return actual(paths)
            with mock.patch.object(core,"RootHandles",side_effect=typed_handles):
                result=mlt.read_ffv1_admission(roots=roots,current=CURRENT,
                                                source_bindings=world.bindings)
        self.assertEqual(result,world.admission)
        self.assertIn({name:str(path) for name,path in roots.items()},observed)

    def test_only_fixed_plan_enoent_yields_the_absent_plan_marker(self):
        cases=("plan-absent","seed-absent","plan-access","plan-disappeared-after-stat",
               "plan-invalid","fixture-identity")
        for case in cases:
            with self.subTest(case=case):
                world=self.world()
                if case=="plan-absent":
                    del world.files[("observation",mlt.FFV1_PLAN_PATH)]
                elif case=="seed-absent":
                    del world.files[("observation",mlt.FFV1_SEED_STDOUT_PATH)]
                elif case=="plan-access":
                    def denied(key):
                        if key==("observation",mlt.FFV1_PLAN_PATH):
                            raise PermissionError("synthetic plan access failure")
                    world.on_read=denied
                elif case=="plan-disappeared-after-stat":
                    def disappear(key):
                        if key==("observation",mlt.FFV1_PLAN_PATH):
                            world.files.pop(key)
                    world.on_read=disappear
                elif case=="plan-invalid":
                    world.put("observation",mlt.FFV1_PLAN_PATH,b"{not-json")
                else:
                    plan=copy.deepcopy(world.plan)
                    plan["revision"]+=1
                    world.put("observation",mlt.FFV1_PLAN_PATH,plan)
                with world.patched(),self.assertRaises(Exception) as caught:
                    mlt.read_ffv1_admission(roots=world.roots,current=CURRENT,
                                           source_bindings=world.bindings)
                if case=="plan-absent":
                    self.assertIsInstance(caught.exception,mlt.Ffv1StagePlanAbsent)
                    self.assertIsInstance(caught.exception.__cause__,FileNotFoundError)
                else:
                    self.assertNotIsInstance(caught.exception,mlt.Ffv1StagePlanAbsent)
                    if case=="plan-access":
                        self.assertIsInstance(caught.exception,PermissionError)
                    elif case=="plan-disappeared-after-stat":
                        self.assertIsInstance(caught.exception,FileNotFoundError)
                        self.assertIn(("observation",mlt.FFV1_PLAN_PATH),world.stats)

    def test_absent_plan_keeps_core2_roles_losses_and_one_bilateral_guard(self):
        world=self.world()
        del world.files[("observation",mlt.FFV1_PLAN_PATH)]
        del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        world.reads.clear();world.stats.clear()
        with world.patched(),mock.patch.object(core,"capture_selected_originals",
                wraps=core.capture_selected_originals) as capture:
            raw,manifest=self.absent_collector(world).capture()
            self.assertIs(mlt.checked_ffv1_plan_absence(manifest,current=CURRENT,
                source_bindings=world.bindings),manifest)
        self.assertEqual(capture.call_count,1)
        self.assertEqual(manifest["schema"],core.MANIFEST_SCHEMA)
        self.assertEqual(manifest["lost_scope"],list(core.LOST_SCOPE))
        self.assertFalse(manifest["required_layers_complete"])
        self.assertFalse(manifest["raw_diagnostics_included"])
        self.assertNotIn("diagnostic_contract",manifest)
        actual={(row["phase"],row["layer"],row["role"]):(row["minimum"],row["maximum"])
                for row in manifest["role_contract"]}
        self.assertEqual(actual,mlt.mlt_contract())
        guards=[row for row in manifest["absence_guards"]
                if row["relative_path"]==mlt.FFV1_PLAN_PATH]
        self.assertEqual(len(guards),1)
        self.assertTrue(guards[0]["absent_before"] and guards[0]["absent_after"])
        self.assertNotIn(("observation",mlt.FFV1_PLAN_PATH),world.reads)
        seed=world.files[("observation",mlt.FFV1_SEED_STDOUT_PATH)]
        self.assertNotIn(seed,raw)

    def test_plan_appearance_on_either_capture_side_invalidates_absence_without_reading_it(self):
        for appearance in ("before","after"):
            with self.subTest(appearance=appearance):
                world=self.world()
                original=world.files.pop(("observation",mlt.FFV1_PLAN_PATH))
                world.reads.clear();world.stats.clear()
                seen=[]
                def appear(key):
                    if key==("observation",mlt.FFV1_PLAN_PATH):
                        seen.append(key)
                        if len(seen)==(1 if appearance=="before" else 2):
                            world.put("observation",mlt.FFV1_PLAN_PATH,original)
                world.on_stat=appear
                with world.patched(),mock.patch.object(core,"capture_selected_originals",
                        wraps=core.capture_selected_originals) as capture:
                    _raw,manifest=self.absent_collector(world).capture()
                    with self.assertRaises(core.SelectionError):
                        mlt.checked_ffv1_plan_absence(manifest,current=CURRENT,
                                                      source_bindings=world.bindings)
                self.assertEqual(capture.call_count,1)
                self.assertEqual(len(seen),2)
                guard=next(row for row in manifest["absence_guards"]
                           if row["relative_path"]==mlt.FFV1_PLAN_PATH)
                self.assertEqual(guard["absent_before"],appearance=="after")
                self.assertFalse(guard["absent_after"])
                self.assertNotIn(("observation",mlt.FFV1_PLAN_PATH),world.reads)


class Ffv1PartialIntakeTests(unittest.TestCase):
    """A6's actual partial /3 API, with all native roles still required."""
    def world(self):
        return Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())

    def incomplete(self):
        world=self.world()
        del world.files[("observation","producer/native-multisegment-timeline-evidence.json")]
        entry=world.admission["diagnostic_contract"]["entries"][2]
        world.put(entry["root"],entry["relative_path"],b"original FFV1 failure\x00\xff")
        return world

    def test_partial_intake_preserves_incomplete_native_layers_and_original_diagnostics(self):
        world=self.incomplete()
        with world.patched():
            raw,manifest=world.capture()
            report=mlt.verify_decrypted_selection(raw,current=CURRENT,scenario="repaired-g2",
                expected_diagnostic_admission=world.admission,require_complete=False)
            with self.assertRaises(core.SelectionError):
                mlt.verify_decrypted_selection(raw,current=CURRENT,scenario="repaired-g2",
                    expected_diagnostic_admission=world.admission)
        self.assertEqual(report["schema"],"motionwright.native.mlt-diagnostic-intake/3")
        self.assertTrue(report["diagnostic_intake_verified"])
        self.assertFalse(report["selected_layers_complete"])
        self.assertGreater(report["required_missing_or_failed_members"],0)
        self.assertEqual(manifest["required_layers_complete"],
                         all(layer["complete"] for layer in manifest["layers"]))
        self.assertIn(b"original FFV1 failure\x00\xff",raw)
        for name in ("selected_intake_verified","source_content_and_live_module_verified",
                     "original_native_media_bytes_verified","source_bound_reported_native_success",
                     "native_operation_retried","physical_acceptance","human_acceptance","release_authorized"):
            self.assertFalse(report[name])

    def test_partial_intake_still_rejects_role_and_payload_or_type_or_namespace_tampering(self):
        world=self.incomplete()
        with world.patched():
            raw,_=world.capture()
            def missing_row(manifest,entries):
                manifest["members"]=[row for row in manifest["members"] if row["role"]!="native-master"]
            def optional_row(manifest,entries):
                next(row for row in manifest["members"] if row["role"]=="native-master")["required"]=False
            def extra_payload(manifest,entries):
                entries["unregistered-original.bin"]=b"not admitted"
            def changed_payload(manifest,entries):
                row=next(row for row in manifest["members"] if row["kind"]=="diagnostic-original"
                         and row["state"]=="INCLUDED")
                entries[row["member"]]+=b"changed"
            cases=(
                ("missing-required-role",missing_row),("required-made-optional",optional_row),
                ("extra-payload",extra_payload),("changed-original",changed_payload),
                ("kind-type",lambda m,e:m["members"][0].update(kind={})),
                ("phase-type",lambda m,e:m["members"][0].update(phase=[])),
                ("namespace",lambda m,e:m.update(selected_namespace_stable=False)),
                ("source",lambda m,e:m.update(source_bytes_bound=False)),
            )
            for label,change in cases:
                with self.subTest(case=label),self.assertRaises(core.SelectionError):
                    mlt.verify_decrypted_selection(rewrite_selected(raw,change),current=CURRENT,
                        scenario="repaired-g2",expected_diagnostic_admission=world.admission,
                        require_complete=False)

    def test_partial_mode_rejects_forged_global_completeness_in_both_directions(self):
        for complete in (True,False):
            with self.subTest(original_complete=complete):
                world=self.world() if complete else self.incomplete()
                with world.patched():
                    raw,manifest=world.capture()
                    self.assertIs(manifest["required_layers_complete"],complete)
                    forged=rewrite_selected(raw,lambda m,e:m.update(required_layers_complete=not complete))
                    with self.assertRaisesRegex(core.SelectionError,"selected-global-completeness-mismatch"):
                        mlt.verify_decrypted_selection(forged,current=CURRENT,scenario="repaired-g2",
                            expected_diagnostic_admission=world.admission,require_complete=False)

    def test_authenticated_partial_intake_is_explicit_and_keeps_unknown_terminal(self):
        import native_mlt_g2_custody as custody
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import base64
        key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        der=key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient=dict(spki_der_base64=base64.b64encode(der).decode(),sha256=core.sha(der))
        world=self.incomplete()
        with world.patched():
            raw,manifest=world.capture()
            encrypted=custody.encrypt_snapshot(raw,recipient,CURRENT)
            public=custody.selected_projection(CURRENT,None,1,"failure",encrypted,recipient,manifest,
                producer_started=True,source_unchanged=True,diagnostic_admission=world.admission,
                diagnostic_intake_verified=True)
            report=mlt.verify_sealed_selection(encrypted,key,public,
                expected_diagnostic_admission=world.admission,require_complete=False)
            self.assertEqual(report["native_terminal"],"ERROR_OR_UNKNOWN")
            self.assertFalse(report["selected_layers_complete"])
            self.assertFalse(report["source_content_and_live_module_verified"])
            with self.assertRaises(core.SelectionError):
                mlt.verify_sealed_selection(encrypted,key,public,
                    expected_diagnostic_admission=world.admission)
            with self.assertRaises(core.SelectionError):
                mlt.verify_sealed_selection(encrypted,key,public,require_complete=False)
            changed={**public,"selected_layers_complete":True}
            with self.assertRaises(core.SelectionError):
                mlt.verify_sealed_selection(encrypted,key,changed,
                    expected_diagnostic_admission=world.admission,require_complete=False)

    def test_external_metadata_contains_no_copied_seed_argv_or_full_plan(self):
        world=self.world();admission=world.admission
        self.assertEqual(set(admission["fixture_identity"]),set(mlt.IDENTITY))
        self.assertEqual(set(admission["stage_plan"]["identity"]),
                         set(mlt.IDENTITY)|{"input_sha256","owner_output_namespace"})
        self.assertEqual(admission["stage_plan"]["original"]["sha256"],core.sha(world.plan_original))
        def keys(value):
            if type(value) is dict:
                return set(value)|set().union(*(keys(item) for item in value.values()))
            if type(value) is list:
                return set().union(*(keys(item) for item in value))
            return set()
        self.assertTrue({"argv","seed_input","slots","args","value"}.isdisjoint(keys(admission)))
        self.assertEqual(len(admission["diagnostic_contract"]["entries"]),54)



class Ffv1SeedCustodyTests(unittest.TestCase):
    """Explicit seed subgroup in the same Core /3 capture; no native process."""
    def world(self):
        return Ffv1DiagnosticWorld(Ffv1OriginalSelectionTests().plan())

    def test_combined_capture_preserves_ffv1_and_seed_subgroups_and_original_bytes(self):
        world=self.world();admitted=world.admission
        ffv1=admitted["diagnostic_contract"];seed=admitted["seed_diagnostic_contract"]
        combined=admitted["capture_diagnostic_contract"]
        self.assertEqual(len(ffv1["entries"]),54)
        self.assertEqual(len(seed["entries"]),3)
        self.assertEqual(combined["entries"],ffv1["entries"]+seed["entries"])
        self.assertEqual(len(combined["entries"]),57)
        self.assertEqual((admitted["selected_optional_members"],admitted["capture_selected_members"]),(55,58))
        self.assertEqual((admitted["selected_maximum"],admitted["seed_diagnostic_maximum"],
                          admitted["capture_selected_maximum"]),(3997696,393216,4390912))
        self.assertEqual(sum(row["maximum"] for row in combined["entries"])+65536,4390912)
        self.assertEqual([row["type"] for row in seed["entries"]],["stdout","stderr","typed-terminal"])
        with world.patched():
            raw,manifest=world.capture()
            intake=mlt.Intake(raw,CURRENT,"repaired-g2",mlt.mlt_contract(seed_diagnostics=True),
                mlt.policies("repaired-g2"),expected_diagnostic_admission=admitted)
        self.assertEqual(manifest["diagnostic_contract"],combined)
        rows=[row for row in manifest["members"] if row["role"].startswith("ffv1-seed-")]
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row["state"]=="INCLUDED" and row["original_bytes"] for row in rows))
        for entry in seed["entries"]:
            row,value,payload=intake.one(entry["role"],"original","attempt-custody")
            self.assertEqual(payload,world.files[(entry["root"],entry["relative_path"])])
            self.assertEqual(row["diagnostic_type"],entry["type"])
            self.assertFalse(row.get("stream_eof_attested",False))
        stderr=intake.one("ffv1-seed-stderr","original","attempt-custody")
        self.assertEqual((stderr[0]["bytes"],stderr[2]),(0,b""))
        terminal=intake.value("ffv1-seed-terminal","original","attempt-custody")
        self.assertEqual(len(terminal["argv"]),3)
        self.assertFalse(terminal["native_outcome_inferred"])
        self.assertFalse(terminal["stdout"]["stream_eof_attested"])

    def test_subgroup_cardinality_budget_and_context_substitution_are_rejected(self):
        world=self.world()
        changes=(
            ("ffv1-roster-53",lambda a:a["diagnostic_contract"]["entries"].pop()),
            ("seed-roster-2",lambda a:a["seed_diagnostic_contract"]["entries"].pop()),
            ("combined-roster-56",lambda a:a["capture_diagnostic_contract"]["entries"].pop()),
            ("ffv1-carriers-54",lambda a:a.update(selected_optional_members=54)),
            ("seed-carriers-4",lambda a:a.update(seed_diagnostic_members=4)),
            ("combined-diagnostics-54",lambda a:a.update(capture_diagnostic_members=54)),
            ("combined-carriers-55",lambda a:a.update(capture_selected_members=55)),
            ("ffv1-budget",lambda a:a.update(selected_maximum=4390912)),
            ("seed-budget",lambda a:a.update(seed_diagnostic_maximum=65536)),
            ("combined-budget",lambda a:a.update(capture_selected_maximum=3997696)),
            ("ffv1-run",lambda a:a["diagnostic_contract"]["context"].update(run_id="999")),
            ("seed-source",lambda a:a["seed_diagnostic_contract"]["context"].update(source_sha="f"*40)),
            ("combined-sdk",lambda a:a["capture_diagnostic_contract"]["context"].update(semwright_sha="f"*40)),
            ("seed-as-ffv1",lambda a:a.update(diagnostic_contract=a["seed_diagnostic_contract"])),
            ("combined-as-seed",lambda a:a.update(seed_diagnostic_contract=a["capture_diagnostic_contract"])),
        )
        for name,change in changes:
            with self.subTest(case=name):
                candidate=copy.deepcopy(world.admission);change(candidate)
                with self.assertRaises(core.SelectionError):
                    mlt.checked_ffv1_admission(candidate,current=CURRENT,source_bindings=world.bindings)

    def test_changed_original_seed_carriers_remain_bytes_but_cannot_verify_intake(self):
        for path,suffix in ((mlt.FFV1_SEED_STDOUT_PATH,b"\n"),
                            (mlt.FFV1_SEED_STDERR_PATH,b"\x00\xffstderr"),
                            (mlt.FFV1_SEED_TERMINAL_PATH,b"\r\n")):
            with self.subTest(path=path):
                world=self.world()
                changed=world.files[("observation",path)]+suffix
                world.put("observation",path,changed)
                with world.patched():
                    raw,manifest=world.capture()
                    with self.assertRaisesRegex(core.SelectionError,"ffv1-intake-original-seed"):
                        mlt.verify_decrypted_selection(raw,current=CURRENT,scenario="repaired-g2",
                            expected_diagnostic_admission=world.admission,require_complete=False)
                row=next(row for row in manifest["members"] if row["relative_path"]==path)
                self.assertEqual(row["state"],"INCLUDED")
                self.assertEqual((row["retained_sha256"],row["retained_bytes"]),(core.sha(changed),len(changed)))
                self.assertEqual(world.files[("observation",path)],changed)

    def test_input_custody_is_original_only_in_admitted_v3_and_keeps_historical_source(self):
        world=self.world()
        original=world.files[("observation","input-custody.json")]
        with world.patched():
            raw,_=world.capture()
            intake=mlt.Intake(raw,CURRENT,"repaired-g2",mlt.mlt_contract(seed_diagnostics=True),
                mlt.policies("repaired-g2"),expected_diagnostic_admission=world.admission)
        row,value,payload=intake.one("input-custody","original","attempt-custody")
        self.assertEqual((row["kind"],row["policy"]),("json-original","ffv1_input_custody"))
        self.assertEqual(payload,original)
        self.assertEqual(value["original_source"]["workflow_head_sha"],
                         "30f02fd0b744cda36035a4473c98e066f89d3d00")
        self.assertEqual(value["semwright_sha"],mlt.SEM)
        for scenario in ("repaired-g2","film180-g2"):
            with self.subTest(legacy_scenario=scenario):
                legacy=World(scenario)
                with legacy.patched():
                    _raw,manifest=legacy.capture()
                self.assertEqual(manifest["schema"],core.MANIFEST_SCHEMA)
                if scenario=="repaired-g2":
                    expected=(("input-custody","input-custody.json","original","mlt_input"),)
                else:
                    # Film keeps its own single-phase custody roles; repaired
                    # FFV1 input-custody is not part of that contract.
                    expected=(("runtime-custody","runtime-input-custody.json","single","film_runtime"),
                              ("source-custody","firstparty-source-custody.json","single","film_source_custody"))
                    self.assertFalse(any(item["role"]=="input-custody"
                                         for item in manifest["members"]))
                for role,path,phase,policy in expected:
                    rows=[item for item in manifest["members"] if item["role"]==role]
                    self.assertEqual(len(rows),1)
                    self.assertEqual(tuple(rows[0][key] for key in
                        ("root","relative_path","phase","layer","kind","policy","state")),
                        ("observation",path,phase,"attempt-custody","json-projection",policy,"INCLUDED"))
                self.assertFalse(any(item["role"].startswith("ffv1-seed-") for item in manifest["members"]))
                self.assertNotIn("diagnostic_contract",manifest)

    def test_stderr_original_identity_is_checked_before_admission(self):
        world=self.world()
        world.put("observation",mlt.FFV1_SEED_STDERR_PATH,b"unrecorded stderr\xff")
        with world.patched():
            with self.assertRaisesRegex(core.SelectionError,"ffv1-seed-original-stderr-custody"):
                mlt.read_ffv1_admission(roots=world.roots,current=CURRENT,source_bindings=world.bindings)



class AudioSourceSelectionTests(unittest.TestCase):
    def pretty(self, target, *, ignored=False):
        names = ((mlt.AUDIO_PHYSICAL_CASE,) if ignored else
                 (*mlt.AUDIO_SOURCE_ROSTERS[target], mlt.AUDIO_PHYSICAL_CASE) if target == "live" else
                 mlt.AUDIO_SOURCE_ROSTERS[target])
        footer = str(len(names)) + (" test" if len(names) == 1 else " tests") + ", 0 benchmarks\n"
        return ("".join(name + ": test\n" for name in sorted(names)) + "\n" + footer).encode()

    def test_audio_eight_targets_preserve_declared136_and_three_real_consumers48(self):
        self.assertEqual({key:len(value) for key,value in mlt.AUDIO_SOURCE_ROSTERS.items()}, {
            "semwright_mlt_video":82, "semwright-mlt-video-driver":28,
            "semwright-mlt-runtime-runner":3, "domain_contract":4, "live":19,
            "conformance":17, "process":22, "runtime_runner":9})
        self.assertEqual(sum(map(len,mlt.AUDIO_SOURCE_ROSTERS.values())),184)
        self.assertNotIn(mlt.AUDIO_PHYSICAL_CASE, mlt.AUDIO_SOURCE_ROSTERS["live"])
        self.assertIn("runtime::audio_derivative_tests::audio_capture_child_fixture",
                      mlt.AUDIO_SOURCE_ROSTERS["semwright_mlt_video"])
        self.assertEqual(len(mlt.audio_source_normal_specs()),4)
        self.assertEqual((mlt.AUDIO_SOURCE_SHA, mlt.AUDIO_SOURCE_TREE, mlt.AUDIO_SOURCE_PARENT),
                         ("7a31aab1419b803c93e6527bb4abb2bd59222bf2", "4b573cfd586a111d54d24b30b5e0892e4554717b",
                          "b4a71f0c10d3e1d49e71212de7cb5de02b72a9c6"))
        self.assertEqual(mlt.AUDIO_SOURCE_PINS["crates/driver-host/src/lib.rs"],
                         "da69223ac6f7e1b2a4421349ac77fb9b4c85526889c0722631b030cd3ef65821")

    def test_audio_pretty_lists_admit_all_targets_and_separate_physical_ignore(self):
        for target,names in mlt.AUDIO_SOURCE_ROSTERS.items():
            with self.subTest(target=target):
                value=mlt.audio_source_list(self.pretty(target),0,target)
                self.assertEqual(value["selected"],names)
                self.assertEqual(value["not_selected_credit"],"NONE")
        ignored=mlt.audio_source_list(self.pretty("live",ignored=True),0,"live",ignored_only=True)
        self.assertEqual(ignored["all"],(mlt.AUDIO_PHYSICAL_CASE,))
        self.assertEqual(ignored["selected"],())

    def test_audio_list_rejects_missing_duplicate_extra_footer_noise_and_bad_exit(self):
        raw=self.pretty("domain_contract"); first=raw.splitlines()[0]+b"\n"
        for changed in (raw.replace(first,b"",1), first+raw,
                        b"foreign::test: test\n"+raw, raw.replace(b"4 tests, 0 benchmarks",b""),
                        raw.replace(b"4 tests, 0 benchmarks",b"4 test, 0 benchmarks"),raw+b"late text\n"):
            with self.subTest(changed=changed), self.assertRaises(core.SelectionError):
                mlt.audio_source_list(changed,0,"domain_contract")
        for code in (False,True,1,None):
            with self.subTest(code=code),self.assertRaises(core.SelectionError):
                mlt.audio_source_list(raw,code,"domain_contract")

    def test_audio_ignored_boundary_rejects_another_case_or_unselected_target(self):
        raw=self.pretty("live",ignored=True)
        for changed in (raw.replace(mlt.AUDIO_PHYSICAL_CASE.encode(),b"foreign_test"),
                        raw.replace(b"1 test, 0 benchmarks",b"1 tests, 0 benchmarks")):
            with self.assertRaises(core.SelectionError): mlt.audio_source_list(changed,0,"live",ignored_only=True)
        with self.assertRaises(core.SelectionError):
            mlt.audio_source_list(self.pretty("domain_contract"),0,"domain_contract",ignored_only=True)

    def test_audio_compile_selection_is_closed_and_has_no_live_execution(self):
        plans=mlt.audio_source_producers("/owned/cargo")
        self.assertEqual([row[0] for row in plans],["strict-clippy","normal-tools-build","affected-test-build"])
        self.assertIn("--bins",plans[1][1])
        self.assertIn("--no-run",plans[2][1])
        self.assertNotIn("--all-targets",plans[0][1])
        self.assertNotIn("--all-features",plans[2][1])
        self.assertEqual(plans[2][1].count("--test"),5)
        self.assertEqual(plans[2][1].count("--bin"),2)
        self.assertEqual(len(mlt.audio_source_specs()),8)

    def test_audio_completion_requires_every_original_target_name_pair_once(self):
        rosters={name:mlt.audio_source_list(self.pretty(name),0,name) for name in mlt.AUDIO_SOURCE_ROSTERS}
        results=[{"target":target,"name":name,"status":"PASS"}
                 for target,names in mlt.AUDIO_SOURCE_ROSTERS.items() for name in names]
        self.assertEqual(mlt.audio_source_complete(results,rosters)["unique_selected_tests"],184)
        variants=(results[:-1],results+[results[0]],
                  [{**results[0],"status":"UNKNOWN"},*results[1:]],
                  [{**results[0],"name":mlt.AUDIO_PHYSICAL_CASE},*results[1:]])
        for changed in variants:
            with self.subTest(length=len(changed)),self.assertRaises(core.SelectionError):
                mlt.audio_source_complete(changed,rosters)

    def test_audio_context_rejects_other_sha_attempt_os_job_and_physical_scope(self):
        env={"RUNNER_OS":"Linux","RUNNER_ARCH":"X64","CI":"true","GITHUB_ACTIONS":"true",
             "GITHUB_EVENT_NAME":"workflow_dispatch","GITHUB_RUN_ATTEMPT":"1",
             "GITHUB_JOB":"audio-source","MW_MLT_SCOPE":"audio-source-g1",
             "GITHUB_SHA":"a"*40,"GITHUB_WORKFLOW_SHA":"a"*40,"MW_EXPECTED_GATE_SHA":"a"*40,
             "MW_EXPECTED_SDK_SHA":mlt.AUDIO_SOURCE_SHA,"GITHUB_REPOSITORY":"seradotcom/motionwright",
             "GITHUB_WORKFLOW_REF":"seradotcom/motionwright/.github/workflows/native-mlt-sequence-e2e.yml@refs/heads/source",
             "GITHUB_RUN_ID":"123"}
        self.assertEqual(mlt.audio_source_context(env,"linux")["product_source_sha"],mlt.AUDIO_SOURCE_SHA)
        for key,value in (("MW_EXPECTED_SDK_SHA","b"*40),
                          ("MW_EXPECTED_SDK_SHA","b4a71f0c10d3e1d49e71212de7cb5de02b72a9c6"),
                          ("GITHUB_RUN_ATTEMPT","2"),
                          ("GITHUB_SHA","c"*40),("RUNNER_OS","Windows"),
                          ("GITHUB_JOB","native-mlt-sequence"),("MW_MLT_SCOPE","normal")):
            with self.subTest(key=key),self.assertRaises(core.SelectionError):
                mlt.audio_source_context({**env,key:value},"linux")

if __name__=="__main__":unittest.main()
