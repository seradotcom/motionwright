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
from native_selection_test_fixtures import World,CURRENT,PCM33,WAV33,rewrite_selected,selected_value_change


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
        self.assertEqual(len(manifest["absence_guards"]),2)
        for row in manifest["absence_guards"]:
            self.assertTrue(row["absent_before"] and row["absent_after"])
            self.assertFalse(row["opened"] or row["hashed"])
            self.assertNotIn((row["root"],row["relative_path"]),world.reads)

    def test_present_database_journals_are_retained_as_exact_original_bytes(self):
        world=self.world()
        for suffix in ("-wal","-shm"):world.put("owner","motionwright-data/motionwright.sqlite3"+suffix,("original"+suffix).encode())
        with world.patched():raw,manifest=world.capture();world.verify(raw)
        self.assertEqual(manifest["absence_guards"],[])
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


if __name__=="__main__":unittest.main()
