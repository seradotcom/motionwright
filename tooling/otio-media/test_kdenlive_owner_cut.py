"""Closed source-to-owner conversion and one-shot failure boundaries; no native effects."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import kdenlive_owner_cut as owner


def originals():
    ids = ["scene-a", "scene-b", "scene-c"]
    source = {"id": "project", "generation": "generation", "revision": 7,
              "scenes": [{"id": ident, "duration": {"num": "1", "den": "1"}}
                         for ident in ids]}
    baseline = {
        "schema": "motionwright.otio-edit-preview/1",
        "source_project_sha256": "a" * 64, "project_id": "project",
        "generation": "generation", "expected_project_revision": 7,
        "original_scene_order": ids, "proposed_scene_order": ids,
        "native_change_proposals": [], "media_refs_verified": 3,
        "missing_references_retained": 0, "loss_report": ["Preserve source-only loss"],
        "property_fidelity": [{"property": str(i)} for i in range(12)],
        "edit_imported": False, "source_media_relinked": False,
        "owner_revision_commit_required": True, "human_review_required": True,
        "rendering_performed": False, "semantic_native_roundtrip": "NOT_SUPPORTED",
        "nle_receiver_acceptance": "NOT_VERIFIED", "publication_approved": False,
    }
    returned = copy.deepcopy(baseline)
    for field in ("rendering_performed", "missing_references_retained"):
        returned.pop(field)
    returned.update(
        schema="motionwright.kdenlive-bound-cut-preview/1",
        profile="kdenlive-bound-video-cut-v1",
        status="BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
        original_missing_scene_ids=[], raw_files_rewritten=False,
        native_final_frames_verified=False, native_project_reopened="NOT_VERIFIED",
        received_clip_count=3, edited_clip_count=3,
        native_change_proposals=[{"type": "set_scene_duration", "scene_id": ids[2],
                                  "duration": {"num": "1", "den": "2"}}],
        loss_report=baseline["loss_report"] + ["Retain native-identity loss"],
    )
    return baseline, returned, source


class KdenliveOwnerCutContracts(unittest.TestCase):
    def test_existing_flags_and_every_loss_survive_in_separate_derived_preview(self):
        baseline, returned, source = originals()
        before = copy.deepcopy((baseline, returned, source))
        derived = owner.derive_owner_preview(baseline, returned, source, "a" * 64)
        self.assertEqual((baseline, returned, source), before)
        self.assertEqual(derived["schema"], baseline["schema"])
        self.assertEqual(derived["loss_report"], returned["loss_report"])
        self.assertEqual(derived["property_fidelity"], baseline["property_fidelity"])
        self.assertTrue(derived["human_review_required"])
        self.assertFalse(derived["edit_imported"])
        self.assertFalse(derived["rendering_performed"])
        derived["loss_report"].append("Only the new value changes")
        self.assertEqual((baseline, returned, source), before)

    def test_stale_or_mixed_project_generation_revision_or_digest_rejects(self):
        for field, wrong in (("project_id", "foreign"), ("generation", "foreign"),
                             ("expected_project_revision", 8),
                             ("expected_project_revision", True),
                             ("source_project_sha256", "b" * 64)):
            with self.subTest(field=field, wrong=wrong):
                baseline, returned, source = originals()
                returned[field] = wrong
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_imported_or_human_or_receiver_authority_cannot_be_promoted(self):
        for field, wrong in (("edit_imported", True), ("publication_approved", True),
                             ("human_review_required", False),
                             ("owner_revision_commit_required", False),
                             ("native_project_reopened", "VERIFIED"),
                             ("native_final_frames_verified", True),
                             ("nle_receiver_acceptance", "VERIFIED"),
                             ("raw_files_rewritten", True),
                             ("status", "REJECTED_LOSS")):
            with self.subTest(field=field):
                baseline, returned, source = originals()
                returned[field] = wrong
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_only_exact_returned_third_scene_half_second_end_trim_is_admitted(self):
        for changes in ([], [{"type": "move_scene", "scene_id": "scene-c", "to_index": 0}],
                        [{"type": "set_scene_duration", "scene_id": "scene-a",
                          "duration": {"num": "1", "den": "2"}}],
                        [{"type": "set_scene_duration", "scene_id": "scene-c",
                          "duration": {"num": "2", "den": "1"}}]):
            with self.subTest(changes=changes):
                baseline, returned, source = originals()
                returned["native_change_proposals"] = changes
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_dropped_property_or_loss_cannot_be_hidden_in_legacy_conversion(self):
        for field, value in (("loss_report", ["replacement"]),
                             ("property_fidelity", [{"property": "new"}]),
                             ("original_missing_scene_ids", ["scene-c"]),
                             ("media_refs_verified", 2)):
            with self.subTest(field=field):
                baseline, returned, source = originals()
                returned[field] = value
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_non_original_duration_or_baseline_edit_is_rejected(self):
        for mutate in (lambda b, r, s: s["scenes"][2].update(duration={"num": "2", "den": "1"}),
                       lambda b, r, s: b.update(native_change_proposals=r["native_change_proposals"]),
                       lambda b, r, s: b.update(missing_references_retained=1)):
            with self.subTest(mutate=mutate):
                baseline, returned, source = originals()
                mutate(baseline, returned, source)
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def one_shot(self, outcomes):
        baseline, returned, source = originals()
        evidence = Path("/isolated/evidence")
        receipt = {"schema": "motionwright.native.otio-owner-cut-commit/1",
                   "single_revision_committed": True, "human_acceptance": False}
        def original_bytes(path):
            if path.name == "source-project.json":
                return json.dumps(source).encode()
            if path.name == "owner-cut-commit-receipt.json":
                return json.dumps(receipt).encode()
            return json.dumps(returned).encode()
        # This helper tests one-shot control/error flow only. The real capture
        # implementation is exercised by the separate immutable-file controls.
        def captured_bytes(path, maximum):
            raw = original_bytes(path)
            return raw, {"sha256": "a" * 64, "size": len(raw)}
        with patch.object(owner, "import_preview", return_value=baseline), \
             patch.object(owner, "capture_original", side_effect=captured_bytes), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "is_symlink", return_value=False), \
             patch.object(Path, "resolve", lambda path, **kwargs: path), \
             patch.object(owner.fixture, "sha", return_value="a" * 64), \
             patch.object(owner.fixture, "_new_original"), \
             patch.object(owner.fixture, "run_firstparty_native",
                          side_effect=outcomes) as run:
            try:
                owner.commit_once(Path("/isolated/capsule"), evidence,
                                  evidence / "source.sqlite3",
                                  evidence / "reopened-edited-cut-preview.json")
            except BaseException as error:
                return run, error
        self.fail("The intentionally incomplete operation was promoted to success")

    def test_unknown_commit_terminal_never_retries_or_launches_readback(self):
        run, error = self.one_shot([TimeoutError("Original commit state unknown")])
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1], "commit")

    def test_readback_failure_never_replays_known_commit(self):
        receipt = {"schema": "motionwright.native.otio-owner-cut-commit/1",
                   "single_revision_committed": True, "human_acceptance": False}
        run, error = self.one_shot([json.dumps(receipt).encode(), b'{"project_reopened_equal":false}'])
        self.assertIsInstance(error, owner.fixture.KdenliveCompleteFixtureRefused)
        self.assertEqual([call.args[0][1] for call in run.call_args_list],
                         ["commit", "readback"])



class KdenliveCanonicalSourceCycleContracts(unittest.TestCase):
    """Real original files; only the existing native caller is replaced."""

    def prepare(self, root):
        import hashlib
        capsule, evidence = root / "capsule", root / "evidence"
        capsule.mkdir()
        evidence.mkdir()
        database = evidence / "source.sqlite3"
        database.write_bytes(b"synthetic database placeholder; no SQLite claim")
        binary = root / "owner-example"
        binary.write_bytes(b"synthetic executable identity; never executed")
        native = evidence / "edited.kdenlive"
        native.write_bytes(b"<mlt><!-- original opaque native source --></mlt>")
        source = {"id": "a4817cd0-b422-4b59-a5e5-a41028a8b555",
                  "generation": "80a257c6-2e9c-4628-8451-4816c682a3c9", "revision": 7}
        source_bytes = (json.dumps(source, sort_keys=True) + "\n").encode()
        (capsule / "source-project.json").write_bytes(source_bytes)
        committed = {**source, "revision": 8}
        cut = {"schema": "motionwright.native.otio-owner-cut-commit/1",
               "single_revision_committed": True, "committed_project": committed,
               "human_acceptance": False, "release_authorized": False,
               "source_project_sha256": hashlib.sha256(source_bytes).hexdigest()}
        cut_bytes = (json.dumps(cut, sort_keys=True) + "\n").encode()
        (evidence / "owner-cut-commit-receipt.json").write_bytes(cut_bytes)
        completion = {
            "sqlite_owner_cut_committed": True, "new_process_project_reopened_equal": True,
            "single_revision_increment": 1,
            "owner_cut_commit_receipt_sha256": hashlib.sha256(cut_bytes).hexdigest(),
            "human_acceptance": False, "release_authorized": False,
        }
        self.paths = capsule, evidence, database, native, binary
        self.cut, self.completion, self.native_sha = (
            cut, completion, hashlib.sha256(native.read_bytes()).hexdigest())
        self.calls, self.outputs = [], {}

    def invoke(self, *, fail_mode=None, original=None, mutate=None,
               disk_mismatch=None, after_last=None, new_original=None):
        import hashlib
        capsule, evidence, database, native, binary = self.paths
        attached_project = {**self.cut["committed_project"], "revision": 9}
        portable_generation = "24f61f6e-cb47-48b9-a66b-31df4b83dcd7"
        def run(argv, *, timeout, evidence_prefix):
            mode = argv[1]
            self.calls.append((mode, argv, timeout, evidence_prefix))
            if mode == fail_mode:
                raise original
            if mode == "source-attach":
                self.assertEqual(argv[2:], [str(database), str(capsule / "source-project.json"),
                    str(evidence / "owner-cut-commit-receipt.json"), str(native), self.native_sha,
                    str(evidence / "native-source-attach-receipt.json")])
                value = {
                    "schema": "motionwright.native.kdenlive-source-import/1",
                    "request_id": "original:kdenlive-capsule:" + self.native_sha,
                    "source_project_sha256": self.cut["source_project_sha256"],
                    "owner_cut_receipt_sha256": self.completion["owner_cut_commit_receipt_sha256"],
                    "native_source_sha256": self.native_sha, "committed_project": attached_project,
                    "committed_project_sha256": "a" * 64,
                    "single_source_revision_committed": True, "source_fidelity_unavailable": True,
                    "source_blob_reopened_equal": True, "native_editor_dispatched_here": False,
                    "per_scene_native_semantic_mapping_verified": False,
                    "automatic_retry": False, "human_acceptance": False, "release_authorized": False,
                }
                output_path = evidence / "native-source-attach-receipt.json"
            elif mode == "source-readback":
                self.assertEqual(argv[2:], [str(database), str(capsule / "source-project.json"),
                    str(evidence / "owner-cut-commit-receipt.json"),
                    str(evidence / "native-source-attach-receipt.json")])
                value = {
                    "schema": "motionwright.native.kdenlive-source-readback/1",
                    "attachment_receipt_sha256": owner.fixture.sha(
                        evidence / "native-source-attach-receipt.json"),
                    "committed_project_sha256": "a" * 64, "native_source_sha256": self.native_sha,
                    "project_id": attached_project["id"], "generation": attached_project["generation"],
                    "revision": 9, "project_reopened_equal": True, "source_blob_reopened_equal": True,
                    "source_fidelity_unavailable": True, "exact_two_history_events": True,
                    "canonical_project_mutation_performed": False, "native_editor_dispatched_here": False,
                    "automatic_retry": False, "human_acceptance": False, "release_authorized": False,
                }
                output_path = None
            elif mode == "source-bundle":
                self.assertEqual(argv[2:], [str(database), str(capsule / "source-project.json"),
                    str(evidence / "owner-cut-commit-receipt.json"),
                    str(evidence / "native-source-attach-receipt.json"),
                    str(evidence / "native-source-portable"),
                    str(evidence / "native-source-restored-owner/motionwright.sqlite3"),
                    str(evidence / "native-source-bundle-receipt.json")])
                bundle = evidence / "native-source-portable"
                bundle.mkdir()
                manifest = b'{"synthetic_original_bundle_manifest_only":true}\n'
                (bundle / "manifest.json").write_bytes(manifest)
                (evidence / "native-source-restored-owner/motionwright.sqlite3").write_bytes(b"synthetic restored DB")
                value = {
                    "schema": "motionwright.native.kdenlive-source-portability/1",
                    "attachment_receipt_sha256": owner.fixture.sha(
                        evidence / "native-source-attach-receipt.json"),
                    "native_source_sha256": self.native_sha, "project_id": attached_project["id"],
                    "source_generation": attached_project["generation"],
                    "imported_generation": portable_generation, "revision": 9,
                    "imported_project_sha256": "e" * 64, "production_receipt_count": 0,
                    "fresh_generation_observed": True, "source_assets_and_capsules_preserved": True,
                    "source_blob_reopened_equal": True, "source_fidelity_unavailable": True,
                    "local_production_receipts_inherited": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False,
                    "bundle_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                    "inspection_fingerprint": "f" * 64,
                }
                output_path = evidence / "native-source-bundle-receipt.json"
            elif mode == "source-portable-readback":
                self.assertEqual(argv[2:], [str(evidence / "native-source-restored-owner/motionwright.sqlite3"),
                                           str(evidence / "native-source-bundle-receipt.json")])
                value = {
                    "schema": "motionwright.native.kdenlive-source-portable-readback/1",
                    "bundle_receipt_sha256": owner.fixture.sha(evidence / "native-source-bundle-receipt.json"),
                    "native_source_sha256": self.native_sha, "project_id": attached_project["id"],
                    "generation": portable_generation, "revision": 9, "imported_project_sha256": "e" * 64,
                    "production_receipt_count": 0, "project_reopened_equal": True,
                    "source_blob_reopened_equal": True, "fresh_generation_observed": True,
                    "source_fidelity_unavailable": True, "local_production_receipts_inherited": False,
                    "canonical_project_mutation_performed": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False,
                }
                output_path = None
            else:
                self.fail("Unreserved native mode: " + mode)
            if mutate is not None:
                mutate(mode, value)
            raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
            if output_path is not None:
                output_path.write_bytes(raw)
                if disk_mismatch == mode:
                    output_path.write_bytes(b" " + raw)
                    # Whitespace still parses to the same value; a later original
                    # read is expected to bind these actual bytes, not normalized JSON.
                    value = {**value, "native_source_sha256": "0" * 64}
            self.outputs[mode] = value
            if mode == "source-portable-readback" and after_last is not None:
                after_last()
            return (json.dumps(value) + "\n").encode()
        patches = [
            patch.object(owner, "OWNER_EXAMPLE", binary),
            patch.object(owner.fixture, "run_firstparty_native", side_effect=run),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            if new_original is not None:
                stack.enter_context(patch.object(owner.fixture, "_new_original",
                                                side_effect=new_original))
            return owner.source_cycle_once(
                capsule, evidence, database, native, self.native_sha, self.completion)

    def test_four_existing_native_calls_bind_originals_and_separate_fresh_generation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            self.prepare(Path(temporary).resolve())
            originals = {path: path.read_bytes() for path in
                         (self.paths[0] / "source-project.json", self.paths[3],
                          self.paths[1] / "owner-cut-commit-receipt.json")}
            result = self.invoke()
            self.assertEqual([item[0] for item in self.calls], [
                "source-attach", "source-readback", "source-bundle", "source-portable-readback"])
            self.assertEqual([item[3].name for item in self.calls], [
                "native-source-attach-cli", "native-source-readback-cli",
                "native-source-bundle-cli", "native-source-portable-readback-cli"])
            self.assertTrue(all(item[2] == 90 for item in self.calls))
            self.assertTrue(result["source_fidelity_unavailable"])
            self.assertTrue(result["portable_readback_in_new_process"])
            self.assertNotEqual(result["source_generation"], result["portable_generation"])
            self.assertFalse(result["execution_authority_issued"])
            self.assertFalse(result["per_scene_native_semantic_mapping_verified"])
            self.assertFalse(result["alab_sources_adopted_here"])
            self.assertEqual({path: path.read_bytes() for path in originals}, originals)
            for key, name in (
                ("native_source_import_receipt_sha256", "native-source-attach-receipt.json"),
                ("native_source_readback_sha256", "native-source-reopen.json"),
                ("native_source_bundle_receipt_sha256", "native-source-bundle-receipt.json"),
                ("native_source_portable_readback_sha256", "native-source-portable-reopen.json"),
            ):
                self.assertEqual(result[key], owner.fixture.sha(self.paths[1] / name))

    def test_unknown_at_each_stage_stops_without_repeating_any_known_operation(self):
        import tempfile
        modes = ["source-attach", "source-readback", "source-bundle", "source-portable-readback"]
        for index, mode in enumerate(modes):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                original = TimeoutError("original " + mode + " unknown")
                with self.assertRaises(TimeoutError) as caught:
                    self.invoke(fail_mode=mode, original=original)
                self.assertIs(caught.exception, original)
                self.assertEqual([item[0] for item in self.calls], modes[:index + 1])

    def test_source_or_portable_claims_cannot_replace_original_closed_results(self):
        import tempfile
        cases = (
            ("source-attach", "source_fidelity_unavailable", False),
            ("source-attach", "per_scene_native_semantic_mapping_verified", True),
            ("source-readback", "canonical_project_mutation_performed", True),
            ("source-readback", "native_source_sha256", "0" * 64),
            ("source-bundle", "fresh_generation_observed", False),
            ("source-bundle", "production_receipt_count", 1),
            ("source-bundle", "execution_authority_issued", True),
            ("source-bundle", "inspection_fingerprint", "not-a-fingerprint"),
            ("source-portable-readback", "source_blob_reopened_equal", False),
            ("source-portable-readback", "canonical_project_mutation_performed", True),
        )
        modes = ["source-attach", "source-readback", "source-bundle", "source-portable-readback"]
        for mode, key, value in cases:
            with self.subTest(mode=mode, key=key), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                def mutate(actual_mode, result):
                    if actual_mode == mode:
                        result[key] = value
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    self.invoke(mutate=mutate)
                self.assertEqual([item[0] for item in self.calls], modes[:modes.index(mode) + 1])

    def test_invalid_input_is_rejected_before_the_first_native_call(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        for defect in ("owner-not-reopened", "bool-revision-count", "changed-native",
                       "changed-cut", "invalid-original-project"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                if defect == "owner-not-reopened":
                    self.completion["new_process_project_reopened_equal"] = False
                elif defect == "bool-revision-count":
                    self.completion["single_revision_increment"] = True
                elif defect == "changed-native":
                    self.paths[3].write_bytes(b"replaced native original")
                elif defect == "changed-cut":
                    (self.paths[1] / "owner-cut-commit-receipt.json").write_bytes(b"changed")
                else:
                    self.cut["committed_project"]["revision"] = True
                    raw = json.dumps(self.cut).encode()
                    (self.paths[1] / "owner-cut-commit-receipt.json").write_bytes(raw)
                    self.completion["owner_cut_commit_receipt_sha256"] = owner.fixture.sha(
                        self.paths[1] / "owner-cut-commit-receipt.json")
                with self.assertRaises((owner.fixture.KdenliveCompleteFixtureRefused,
                                        KdenliveReadbackRejected)):
                    self.invoke()
                self.assertEqual(self.calls, [])

    def test_custody_failure_stops_before_next_effect_with_same_original_error(self):
        import tempfile
        write = owner.fixture._new_original
        for failed_name, expected_calls in (
            ("native-source-cycle-input.original.json", []),
            ("native-source-reopen.json", ["source-attach", "source-readback"]),
        ):
            with self.subTest(name=failed_name), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                original = OSError("original " + failed_name + " storage failure")
                def retain(path, raw):
                    if path.name == failed_name:
                        raise original
                    return write(path, raw)
                with self.assertRaises(OSError) as caught:
                    self.invoke(new_original=retain)
                self.assertIs(caught.exception, original)
                self.assertEqual([item[0] for item in self.calls], expected_calls)

    def test_stdout_and_retained_native_receipt_must_be_the_same_returned_value(self):
        import tempfile
        modes = ["source-attach", "source-readback", "source-bundle", "source-portable-readback"]
        for mode in ("source-attach", "source-bundle"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    self.invoke(disk_mismatch=mode)
                self.assertEqual([item[0] for item in self.calls], modes[:modes.index(mode) + 1])

    def test_late_replacement_of_original_receipt_cannot_rebase_returned_fingerprint(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        for name in ("native-source-attach-receipt.json", "native-source-reopen.json",
                     "native-source-bundle-receipt.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                def replace():
                    path = self.paths[1] / name
                    original = path.read_bytes()
                    path.write_bytes(b" " + original)
                with self.assertRaises(KdenliveReadbackRejected):
                    self.invoke(after_last=replace)
                self.assertEqual(len(self.calls), 4)




class KdenliveAlabCanonicalSourceContracts(unittest.TestCase):
    """Real original-shaped files and bridge; native example calls are mocked.

    These controls exercise orchestration, never SQLite, codec or NLE evidence.
    Separate Rust tests use the real Service/importer; G2 must run the example.
    """

    def prepare(self, root):
        import hashlib
        import test_bridge as bridge_controls
        helper = bridge_controls.RetainedAlabObservationTests()
        data = helper.fixture()
        raw, pins, media_pins = helper.encode(data)
        self.root = root
        self.destination = root / "portable-cut-A"
        self.evidence = root / "evidence"
        self.evidence.mkdir()
        self.workspace = self.evidence / "alab-originals"
        self.database = self.evidence / "source.sqlite3"
        inputs = root / "admitted-originals"
        inputs.mkdir()
        self.originals = {}
        roles = ("otio", "readback", "container0", "container1", "media0", "media1")
        names = ("source.otio", "readback.json", "container0.json", "container1.json",
                 "mk020_0010.mov", "mk020_0020.mov")
        for role, name, body in zip(roles, names, (*raw, *data["raw_media"])):
            path = inputs / name
            path.write_bytes(body)
            self.originals[role] = path
        self.notices, self.notice_pins = {}, []
        for role, name, _, _ in owner._ALAB_SOURCE_NOTICES:
            raw_notice = ("unit original notice " + role).encode()
            path = inputs / name
            path.write_bytes(raw_notice)
            self.notices[role] = path
            self.notice_pins.append((role, name, len(raw_notice), hashlib.sha256(raw_notice).hexdigest()))
        self.notice_pins = tuple(self.notice_pins)
        self.pins, self.media_pins = pins, media_pins
        self.binary = root / "native-owner-example"
        self.binary.write_bytes(b"unit executable identity; never run")
        self.owner_review = {"reviewer": "explicit unit source caller", "synthetic_source_allowed": False}
        self.rights = [{"owner": "unit source owner", "license": "private unit contract declaration",
                        "authorized": True, "redistributable": True} for _ in range(2)]
        uid = bridge_controls.ID
        scenes, assets, clips = [], [], []
        for index, row in enumerate(media_pins):
            scenes.append({"id": uid(10 + index), "name": ("mk020_0010", "mk020_0020")[index],
                "start": ({"num": "0", "den": "1"}, {"num": "13", "den": "6"})[index],
                "duration": ({"num": "13", "den": "6"}, {"num": "91", "den": "12"})[index]})
            assets.append({"id": uid(50 + index), "name": names[4 + index],
                "media_type": "video/quicktime", "content_sha256": row[3],
                "source_revision": "unit source-shaped canonical asset"})
            clips.append({"OTIO_SCHEMA": "Clip.1", "enabled": True, "effects": [], "markers": [],
                "media_reference": {"OTIO_SCHEMA": "MissingReference.1", "available_range": None,
                                    "metadata": {}, "name": None},
                "source_range": bridge_controls.trange(0, row[5], 24), "name": scenes[-1]["name"],
                "metadata": {"motionwright": {"scene_id": scenes[-1]["id"]}}})
        assets.append({"id": uid(60), "name": "Original ALab selected-source OTIO",
            "media_type": "application/vnd.opentimelineio+json", "content_sha256": pins[0][2],
            "source_revision": "unit original opaque capsule source"})
        self.project = {"id": uid(1), "generation": uid(2), "revision": 7,
                        "title": "Unit canonical source", "scenes": scenes, "assets": assets}
        self.canonical_otio = {"OTIO_SCHEMA": "Timeline.1", "name": "Unit canonical source export",
            "metadata": {"motionwright": {"project_id": uid(1), "generation": uid(2), "revision": 7,
                "export_profile": "conservative-cut-v1", "loss_report": ["Unit opaque source retained."]}},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [{"OTIO_SCHEMA": "Track.1",
                       "kind": "Video", "children": clips}]}}
        self.source_assets = {"media": [{"asset_id": assets[i]["id"], "scene_id": scenes[i]["id"],
            "sha256": row[3], "bytes": row[2], "media_type": "video/quicktime", "name": names[4 + i]}
            for i, row in enumerate(media_pins)], "source_asset_id": uid(60), "capsule_id": uid(61),
            "source_sha256": pins[0][2], "source_bytes": pins[0][1],
            "all_source_blobs_reopened_equal": True, "source_fidelity_unavailable": True,
            "original_unselected_references_retained_in_opaque_source": True,
            "per_scene_native_editability_verified": False}
        self.calls, self.outputs = [], {}

    def invoke(self, *, fail_mode=None, original=None, mutate=None, after=None,
               custody_failure=None, disk_mismatch=None):
        import bridge as media_bridge
        import hashlib
        from contextlib import ExitStack
        from unittest.mock import patch
        write_original = owner.fixture._new_original
        adopted_path = self.workspace / "alab-adoption-receipt.json"
        portable_path = self.workspace / "alab-bundle-receipt.json"
        descriptor_originals = [{"role": role, "bytes": size, "sha256": digest}
                               for role, size, digest in self.pins]
        source = self.project
        imported = {**source, "generation": "34a58bc0-fb96-4c92-8bb3-6463e1ec9830"}

        def run(argv, *, timeout, evidence_prefix):
            mode = argv[1]
            self.calls.append((mode, argv, timeout, evidence_prefix))
            if mode == fail_mode:
                raise original
            if mode == "alab-adopt":
                self.assertEqual(argv[2:], [str(self.database),
                    str(self.workspace / "ALab_mk020_final_edit.h264.otio"),
                    str(self.workspace / "mk020_0010.mov"), str(self.workspace / "mk020_0020.mov"),
                    str(self.workspace / "NATIVE_READBACK.original.json"),
                    str(self.workspace / "mk020_0010-container.original.json"),
                    str(self.workspace / "mk020_0020-container.original.json"),
                    str(self.workspace / "alab-retained-origin-derived.json"), str(adopted_path)])
                self.database.write_bytes(b"unit database placeholder; not a SQLite observation")
                stages = []
                for index in range(7):
                    path = adopted_path.with_suffix(f".stage-{index:02}.result.original.json")
                    raw = json.dumps({"request_id": f"unit-source-{index}", "replayed": False,
                                      "project": source, "previous_revision": index}).encode()
                    path.write_bytes(raw)
                    stages.append({"index": index, "file": path.name, "request_id": f"unit-source-{index}",
                                   "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                value = {"schema": "motionwright.native.alab-selected-source-adoption/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE, "project": source,
                    "canonical_otio": self.canonical_otio, "project_value_sha256": "a" * 64,
                    "originals": descriptor_originals, "source_assets": self.source_assets,
                    "stage_receipts": stages, "original_history_events": 7,
                    "production_receipt_count": 0,
                    "derived_descriptor_sha256": owner.fixture.sha(self.workspace / "alab-retained-origin-derived.json"),
                    "canonical_source_adopted": True, "sqlite_source_reopened_equal": True,
                    "whole_original_otio_retained": True, "source_fidelity_unavailable": True,
                    "historical_readback_rewritten": False, "new_decode": False, "new_render": False,
                    "nle_opened": False, "native_editor_dispatched_here": False,
                    "execution_authority_issued": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False}
                output_path = adopted_path
            elif mode == "alab-source-readback":
                self.assertEqual(argv[2:], [str(self.database), str(adopted_path)])
                value = {"schema": "motionwright.native.alab-selected-source-readback/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "adoption_receipt_sha256": owner.fixture.sha(adopted_path),
                    "project_id": source["id"], "generation": source["generation"], "revision": source["revision"],
                    "project_value_sha256": "a" * 64, "source_assets": self.source_assets,
                    "project_reopened_equal": True, "source_fidelity_unavailable": True,
                    "canonical_project_mutation_performed": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "human_acceptance": False, "release_authorized": False}
                output_path = None
            elif mode == "alab-source-bundle":
                self.assertEqual(argv[2:], [str(self.database), str(adopted_path),
                    str(self.workspace / "alab-source-portable"),
                    str(self.workspace / "alab-restored-owner/motionwright.sqlite3"), str(portable_path)])
                self.assertTrue((self.workspace / "alab-restored-owner.intent.json").is_file())
                self.assertTrue((self.workspace / "alab-restored-owner").is_dir())
                bundle = self.workspace / "alab-source-portable"
                bundle.mkdir()
                raw = b'{"unit_original_bundle_manifest":true}\n'
                (bundle / "manifest.json").write_bytes(raw)
                (self.workspace / "alab-restored-owner/motionwright.sqlite3").write_bytes(b"unit restored DB placeholder")
                value = {"schema": "motionwright.native.alab-source-portability/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "adoption_receipt_sha256": owner.fixture.sha(adopted_path),
                    "source_project": source, "imported_project": imported,
                    "source_generation": source["generation"], "imported_generation": imported["generation"],
                    "source_assets": self.source_assets, "originals": descriptor_originals,
                    "production_receipt_count": 0, "imported_project_value_sha256": "b" * 64,
                    "manifest_sha256": hashlib.sha256(raw).hexdigest(), "inspection_fingerprint": "c" * 64,
                    "fresh_generation_observed": True, "separate_owner_cas": True,
                    "project_reopened_equal": True, "all_source_blobs_reopened_equal": True,
                    "whole_original_otio_retained": True, "source_fidelity_unavailable": True,
                    "local_production_receipts_inherited": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False}
                output_path = portable_path
            elif mode == "alab-source-portable-readback":
                self.assertEqual(argv[2:], [str(self.workspace / "alab-restored-owner/motionwright.sqlite3"),
                                           str(portable_path)])
                value = {"schema": "motionwright.native.alab-source-portable-readback/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "portability_receipt_sha256": owner.fixture.sha(portable_path),
                    "project_id": source["id"], "generation": imported["generation"], "revision": imported["revision"],
                    "project_value_sha256": "b" * 64, "source_assets": self.source_assets,
                    "project_reopened_equal": True, "all_source_blobs_reopened_equal": True,
                    "source_fidelity_unavailable": True, "fresh_generation_observed": True,
                    "canonical_project_mutation_performed": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "local_production_receipts_inherited": False,
                    "human_acceptance": False, "release_authorized": False}
                output_path = None
            else:
                self.fail("Unexpected native operation: " + mode)
            if mutate is not None:
                mutate(mode, value)
            if output_path is not None:
                output_path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            self.outputs[mode] = copy.deepcopy(value)
            if disk_mismatch == mode:
                disk = {**value, "nle_opened": "changed retained value"}
                output_path.write_bytes((json.dumps(disk) + "\n").encode())
            output = (json.dumps(value) + "\n").encode()
            # Simulate the SAME native helper's real original retention before
            # returning these unit bytes to the caller; no process is executed.
            owner.fixture._new_original(evidence_prefix.with_name(evidence_prefix.name + ".intent.json"),
                (json.dumps({"state": "INTENT", "command": argv, "timeout": timeout,
                             "invocation_retried": False}, sort_keys=True) + "\n").encode())
            owner.fixture._retain_call(evidence_prefix, returncode=0, stdout=output, stderr=b"")
            if after is not None:
                after(mode)
            return output

        def retain(path, raw):
            if custody_failure is not None and path.name == custody_failure[0]:
                raise custody_failure[1]
            return write_original(path, raw)

        with ExitStack() as stack:
            for entry in (
                patch.object(owner, "OWNER_EXAMPLE", self.binary),
                patch.object(owner, "_ALAB_SOURCE_NOTICES", self.notice_pins),
                patch.object(owner.fixture, "require_ephemeral_approved_source", return_value=self.root),
                patch.object(owner.fixture, "run_firstparty_native", side_effect=run),
                patch.object(owner.fixture, "_new_original", side_effect=retain),
                patch.object(media_bridge, "_ALAB_SOURCE_NOTICES", self.notice_pins),
                patch.object(media_bridge, "_ALAB_RETAINED_ORIGINALS", self.pins),
                patch.object(media_bridge, "_ALAB_RETAINED_MEDIA", self.media_pins),
                patch.object(media_bridge, "file_probe", side_effect=AssertionError("no new decoder")),
                patch.object(media_bridge.subprocess, "Popen", side_effect=AssertionError("no native process in unit test")),
            ):
                stack.enter_context(entry)
            return owner.prepare_alab_original(self.destination, evidence_root=self.workspace,
                source_database=self.database, originals=self.originals, notices=self.notices,
                owner_review=self.owner_review, media_rights=self.rights)

    def test_four_existing_calls_export_two_canonical_MOVs_without_decoder_or_receiver_claim(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            self.prepare(Path(temporary).resolve())
            originals = {role: path.read_bytes() for role, path in self.originals.items()}
            result, retained = self.invoke()
            self.assertEqual([call[0] for call in self.calls], ["alab-adopt", "alab-source-readback",
                "alab-source-bundle", "alab-source-portable-readback"])
            self.assertTrue(all(call[2] == 90 for call in self.calls))
            self.assertEqual(result["source_scene_frames"], [52, 182])
            self.assertEqual(result["expected_later_edited_scene_frames"], [52, 170])
            self.assertEqual(result["source_adoption_history_events"], 7)
            self.assertTrue(result["whole_original_otio_retained"])
            self.assertTrue(result["new_process_portable_readback"])
            self.assertNotEqual(result["source_generation"], result["portable_generation"])
            for key in ("new_decode", "new_render", "nle_opened", "edit_imported", "human_acceptance",
                        "release_authorized", "execution_authority_issued"):
                self.assertIs(result[key], False)
            self.assertEqual(retained.descriptor()["historical_source_origin"], "NOT_OBSERVED")
            manifest = json.loads((self.destination / "bridge-manifest.json").read_bytes())
            self.assertEqual(manifest["output_profile"], "alab-two-retained-mov-cut-v1")
            self.assertEqual(len(manifest["bound_media"]), 2)
            self.assertEqual(len(manifest["property_fidelity"]), 12)
            self.assertEqual(manifest["missing_scene_ids"], [])
            self.assertTrue(all(row["path"].endswith(".mov") for row in manifest["bound_media"]))
            self.assertEqual({role: path.read_bytes() for role, path in self.originals.items()}, originals)
            self.assertEqual(len(list(self.workspace.glob("alab-adoption-receipt.stage-*.result.original.json"))), 7)
            for role, name, _, _ in self.notice_pins:
                self.assertEqual((self.destination / name).read_bytes(), self.notices[role].read_bytes())
                self.assertEqual((self.workspace / name).read_bytes(), self.notices[role].read_bytes())
            scope = json.loads((self.destination / "ALab-HANDOFF-SCOPE.json").read_bytes())
            self.assertTrue(scope["retain_all_original_notices_when_redistributing"])
            self.assertTrue(scope["portable_zip_includes_all_original_notices"])
            import zipfile
            with zipfile.ZipFile(self.destination / "portable-cut.zip") as zipped:
                for role, name, _, _ in self.notice_pins:
                    self.assertEqual(zipped.read(name), self.notices[role].read_bytes())
            self.assertFalse(scope["music_or_voice_clearance_claimed"])
            self.assertFalse(scope["endorsement_claimed"])

    def test_UNKNOWN_at_each_stage_stops_once_and_preserves_the_identical_original(self):
        import tempfile
        modes = ["alab-adopt", "alab-source-readback", "alab-source-bundle", "alab-source-portable-readback"]
        for index, mode in enumerate(modes):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                error = TimeoutError("original " + mode + " UNKNOWN")
                with self.assertRaises(TimeoutError) as caught:
                    self.invoke(fail_mode=mode, original=error)
                self.assertIs(caught.exception, error)
                self.assertEqual([call[0] for call in self.calls], modes[:index + 1])
                self.assertFalse(self.destination.exists())

    def test_invalid_originals_or_rights_stop_before_source_workspace_and_native_effect(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        for defect in ("otio", "readback", "container0", "container1", "media0", "media1",
                       "rights", "synthetic-review", "extra-role", "license", "readme", "selection_notice"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                if defect in self.originals:
                    self.originals[defect].write_bytes(b"replaced original bytes")
                elif defect in self.notices:
                    self.notices[defect].write_bytes(b"replaced original notice")
                elif defect == "rights":
                    self.rights[1]["redistributable"] = False
                elif defect == "synthetic-review":
                    self.owner_review["synthetic_source_allowed"] = True
                else:
                    self.originals["unadmitted"] = self.originals["otio"]
                with self.assertRaises((owner.fixture.KdenliveCompleteFixtureRefused, KdenliveReadbackRejected)):
                    self.invoke()
                self.assertEqual(self.calls, [])
                self.assertFalse(self.workspace.exists())
                self.assertFalse(self.database.exists())

    def test_original_scope_generation_and_CAS_claims_must_agree_before_next_call(self):
        import tempfile
        cases = (
            ("alab-adopt", "new_decode", True),
            ("alab-adopt", "nle_opened", True),
            ("alab-adopt", "original_history_events", True),
            ("alab-adopt", "source_fidelity_unavailable", False),
            ("alab-source-readback", "canonical_project_mutation_performed", True),
            ("alab-source-readback", "adoption_receipt_sha256", "0" * 64),
            ("alab-source-bundle", "source_generation", "another generation"),
            ("alab-source-bundle", "separate_owner_cas", False),
            ("alab-source-bundle", "production_receipt_count", True),
            ("alab-source-portable-readback", "local_production_receipts_inherited", True),
            ("alab-source-portable-readback", "all_source_blobs_reopened_equal", False),
        )
        modes = ["alab-adopt", "alab-source-readback", "alab-source-bundle", "alab-source-portable-readback"]
        for mode, key, value in cases:
            with self.subTest(mode=mode, key=key), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                def mutate(actual, body):
                    if actual == mode:
                        body[key] = value
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    self.invoke(mutate=mutate)
                self.assertEqual([call[0] for call in self.calls], modes[:modes.index(mode) + 1])
                self.assertFalse(self.destination.exists())

    def test_original_receipt_custody_failure_blocks_the_next_operation(self):
        import tempfile
        for name, completed in (("alab-source-input.original.json", 0),
                                ("alab-source-reopen.json", 2),
                                ("alab-restored-owner.intent.json", 2),
                                ("alab-portable-reopen.json", 4)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                error = OSError("original custody error " + name)
                with self.assertRaises(OSError) as caught:
                    self.invoke(custody_failure=(name, error))
                self.assertIs(caught.exception, error)
                self.assertEqual(len(self.calls), completed)
                self.assertFalse(self.destination.exists())

    def test_stdout_cannot_disagree_with_the_original_native_disk_receipt(self):
        import tempfile
        for mode, completed in (("alab-adopt", 1), ("alab-source-bundle", 3)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    self.invoke(disk_mismatch=mode)
                self.assertEqual(len(self.calls), completed)
                self.assertFalse(self.destination.exists())

    def test_late_source_binary_or_retained_stage_change_cannot_rebase_original_identity(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        from bridge import BridgeRefused
        for target in ("input", "copy", "binary", "adoption", "stage", "portable-manifest", "license", "readme", "selection_notice"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                self.prepare(Path(temporary).resolve())
                def after(mode):
                    if mode != "alab-source-portable-readback":
                        return
                    path = {"input": self.originals["otio"],
                        "copy": self.workspace / "mk020_0010.mov", "binary": self.binary,
                        "adoption": self.workspace / "alab-adoption-receipt.json",
                        "stage": self.workspace / "alab-adoption-receipt.stage-04.result.original.json",
                        "portable-manifest": self.workspace / "alab-source-portable/manifest.json",
                        "license": self.notices["license"], "readme": self.notices["readme"],
                        "selection_notice": self.notices["selection_notice"]}[target]
                    path.write_bytes(b" " + path.read_bytes())
                # Five originals are checked with their exact admitted byte bound.
                # Their one-byte growth is rejected by the shared bridge reader
                # before SHA comparison; the other three retained JSON originals
                # remain below their broader ceiling and fail the SHA guard.
                if target in ("input", "copy", "license", "readme", "selection_notice"):
                    expected_type, reason = BridgeRefused, "File exceeds byte budget:"
                elif target == "binary":
                    expected_type = owner.fixture.KdenliveCompleteFixtureRefused
                    reason = "alab_native_example_changed_between_calls"
                else:
                    expected_type = KdenliveReadbackRejected
                    reason = "readback_original_content_changed_from_expected_SHA"
                with self.assertRaisesRegex(expected_type, reason) as caught:
                    self.invoke(after=after)
                self.assertIs(type(caught.exception), expected_type)
                self.assertEqual(len(self.calls), 4)
                self.assertFalse(self.destination.exists())

    def test_caller_receipt_cannot_be_reused_as_a_retained_observation_or_source_grant(self):
        import tempfile
        import bridge as media_bridge
        with tempfile.TemporaryDirectory() as temporary:
            self.prepare(Path(temporary).resolve())
            result, retained = self.invoke()
            with self.assertRaises(media_bridge.BridgeRefused):
                media_bridge.import_preview(self.destination, "timeline.otio",
                    self.destination / "source-project.json", retained_alab=result)
            self.assertFalse(result["execution_authority_issued"])
            self.assertFalse(result["human_acceptance"])
            self.assertFalse(result["release_authorized"])
            self.assertEqual(retained.descriptor()["status"], "RETAINED_MEDIA_ORIGIN_DERIVED_NOT_ADOPTED")
            with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                self.invoke()
            self.assertEqual(len(self.calls), 4)



class KdenliveAlabReturnedSourceContracts(unittest.TestCase):
    """Caller contracts over real bounded files; the four native calls are mocked.

    These controls do not credit SQLite, CAS, Kdenlive or a receiver. The Rust
    example's Service tests and the later native G2 retain those obligations.
    """

    def prepare_return(self, root):
        import bridge as media_bridge
        self.base = KdenliveAlabCanonicalSourceContracts()
        self.base.prepare(root)
        self.base.workspace = self.base.evidence / "alab-source"
        self.base.database = self.base.evidence / "canonical-source.sqlite3"
        self.source_input, self.retained = self.base.invoke()
        self.evidence, self.capsule, self.database = self.base.evidence, self.base.destination, self.base.database
        self.native = self.evidence / "edited.kdenlive"
        self.native.write_bytes(b"unit opaque edited Kdenlive source; no receiver observation")
        self.raw_path = self.evidence / "reopened-edited-cut-preview.json"
        with self.original_scope():
            self.baseline = media_bridge.import_preview(self.capsule, "timeline.otio",
                self.capsule / "source-project.json", retained_alab=self.retained)
        self.returned = copy.deepcopy(self.baseline)
        self.returned.update({"schema": "motionwright.kdenlive-bound-cut-preview/1",
            "profile": "kdenlive-bound-video-cut-v1", "status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
            "original_missing_scene_ids": [], "raw_files_rewritten": False,
            "native_final_frames_verified": False, "native_project_reopened": "NOT_VERIFIED",
            "received_clip_count": 2, "edited_clip_count": 2,
            "native_change_proposals": [{"type": "set_scene_duration",
                "scene_id": self.base.project["scenes"][1]["id"], "duration": {"num": "85", "den": "12"}}]})
        self.returned.pop("rendering_performed", None)
        self.returned.pop("missing_references_retained", None)
        self.raw_path.write_bytes((json.dumps(self.returned, sort_keys=True) + "\n").encode())
        self.calls = []

    def original_scope(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        import bridge as media_bridge
        stack = ExitStack()
        for entry in (
            patch.object(owner, "OWNER_EXAMPLE", self.base.binary),
            patch.object(owner, "_ALAB_SOURCE_NOTICES", self.base.notice_pins),
            patch.object(media_bridge, "_ALAB_SOURCE_NOTICES", self.base.notice_pins),
            patch.object(media_bridge, "_ALAB_RETAINED_ORIGINALS", self.base.pins),
            patch.object(media_bridge, "_ALAB_RETAINED_MEDIA", self.base.media_pins),
            patch.object(media_bridge, "file_probe", side_effect=AssertionError("no new decoder")),
            patch.object(media_bridge.subprocess, "Popen", side_effect=AssertionError("no native process in caller unit test")),
        ):
            stack.enter_context(entry)
        return stack

    def invoke_return(self, *, fail_mode=None, original=None, mutate=None, after=None, custody_failure=None):
        import hashlib
        import bridge as media_bridge
        from unittest.mock import patch
        root, source = self.evidence, self.base.project
        native_sha = owner.fixture.sha(self.native)
        raw_sha = owner.fixture.sha(self.raw_path)
        receipt = root / "alab-returned-source-receipt.json"
        bundle_receipt = root / "alab-edited-bundle-receipt.json"
        adoption = root / "alab-source/alab-adoption-receipt.json"
        final = copy.deepcopy(source)
        final["revision"] += 2
        final["scenes"][1]["duration"] = {"num": "85", "den": "12"}
        final["assets"].append({"id": "71a75b55-82d1-466f-b745-51bf2920a0c0", "name": "edited.kdenlive",
            "media_type": "application/x-kdenlive", "content_sha256": native_sha,
            "source_revision": "unit source-shaped canonical original"})
        imported = {**copy.deepcopy(final), "generation": "8da7a803-f803-4cf0-9603-6bd590ba6df3"}
        descriptor = {"sha256": native_sha, "size_bytes": len(self.native.read_bytes())}
        assets = {"original_source_assets": self.base.source_assets, "native_source": descriptor,
                  "all_four_source_blobs_reopened_equal": True, "source_fidelity_unavailable": True,
                  "per_scene_native_editability_verified": False}
        cut_request = "original:alab-kdenlive-cut:" + raw_sha
        source_request = "original:alab-kdenlive-capsule:" + native_sha
        original_writer = owner.fixture._new_original

        def run(argv, *, timeout, evidence_prefix):
            mode = argv[1]
            self.calls.append((mode, argv, timeout, evidence_prefix))
            if mode == fail_mode:
                raise original
            self.assertEqual(timeout, 90)
            self.assertEqual(argv[0], str(self.base.binary))
            output_path = None
            if mode == "alab-returned-cut":
                self.assertEqual(argv[2:], [str(self.database), str(adoption), str(self.capsule / "source-project.json"),
                    str(root / "alab-owner-cut-preview.json"), str(self.raw_path), str(self.native), native_sha, str(receipt)])
                stages = []
                for number, request in ((7, cut_request), (8, source_request)):
                    path = receipt.with_suffix(f".stage-{number:02}.result.original.json")
                    value = {"request_id": request, "replayed": False, "project": final,
                             "previous_revision": source["revision"] + number - 7}
                    body = (json.dumps(value, sort_keys=True) + "\n").encode()
                    path.write_bytes(body)
                    stages.append({"index": number, "request_id": request, "file": path.name,
                                   "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
                value = {"schema": "motionwright.native.alab-returned-cut-and-source/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE, "committed_project": final,
                    "adoption_receipt_sha256": self.source_input["adoption_receipt_sha256"],
                    "source_project_sha256": owner.fixture.sha(self.capsule / "source-project.json"),
                    "owner_preview_sha256": owner.fixture.sha(root / "alab-owner-cut-preview.json"),
                    "original_Kdenlive_preview_sha256": raw_sha, "cut_request_id": cut_request,
                    "source_import_request_id": source_request, "native_source": descriptor,
                    "source_assets": assets, "committed_project_value_sha256": "d" * 64,
                    "stage_receipts": stages, "exact_additional_history_events": 2,
                    "owner_cut_single_revision_committed": True, "native_source_additional_revision_committed": True,
                    "whole_original_otio_retained": True, "all_four_source_blobs_reopened_equal": True,
                    "source_fidelity_unavailable": True, "sqlite_source_reopened_equal": True,
                    "native_editor_dispatched_here": False, "per_scene_native_editability_verified": False,
                    "execution_authority_issued": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False}
                output_path = receipt
            elif mode == "alab-returned-readback":
                self.assertEqual(argv[2:], [str(self.database), str(adoption), str(receipt), owner.fixture.sha(receipt)])
                value = {"schema": "motionwright.native.alab-returned-source-readback/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "returned_source_receipt_sha256": owner.fixture.sha(receipt),
                    "project_id": final["id"], "generation": final["generation"], "revision": final["revision"],
                    "project_value_sha256": "d" * 64, "source_assets": assets, "native_source": descriptor,
                    "project_reopened_equal": True, "exact_nine_history_events": True,
                    "all_four_source_blobs_reopened_equal": True, "source_fidelity_unavailable": True,
                    "canonical_project_mutation_performed": False, "native_editor_dispatched_here": False,
                    "execution_authority_issued": False, "human_acceptance": False, "release_authorized": False}
            elif mode == "alab-edited-bundle":
                bundle, restored = root / "alab-edited-source-portable", root / "alab-edited-restored-owner/motionwright.sqlite3"
                self.assertEqual(argv[2:], [str(self.database), str(adoption), str(receipt), owner.fixture.sha(receipt),
                    str(bundle), str(restored), str(bundle_receipt)])
                self.assertTrue((root / "alab-edited-restored-owner.intent.json").is_file())
                self.assertTrue(restored.parent.is_dir())
                bundle.mkdir()
                (bundle / "manifest.json").write_bytes(b'{"unit_edited_original_bundle":true}\n')
                restored.write_bytes(b"unit restored DB placeholder; not a SQLite observation")
                value = {"schema": "motionwright.native.alab-edited-source-portability/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE,
                    "returned_source_receipt_sha256": owner.fixture.sha(receipt), "source_project": final,
                    "imported_project": imported, "source_generation": final["generation"],
                    "imported_generation": imported["generation"], "source_assets": assets, "native_source": descriptor,
                    "production_receipt_count": 0, "imported_project_value_sha256": "e" * 64,
                    "inspection_fingerprint": "f" * 64, "manifest_sha256": owner.fixture.sha(bundle / "manifest.json"),
                    "fresh_generation_observed": True, "separate_owner_cas": True, "project_reopened_equal": True,
                    "all_four_source_blobs_reopened_equal": True, "source_fidelity_unavailable": True,
                    "whole_original_otio_retained": True, "local_production_receipts_inherited": False,
                    "execution_authority_issued": False, "native_editor_dispatched_here": False,
                    "per_scene_native_editability_verified": False, "automatic_retry": False,
                    "human_acceptance": False, "release_authorized": False}
                output_path = bundle_receipt
            elif mode == "alab-edited-portable-readback":
                self.assertEqual(argv[2:], [str(root / "alab-edited-restored-owner/motionwright.sqlite3"),
                    str(bundle_receipt), owner.fixture.sha(bundle_receipt)])
                value = {"schema": "motionwright.native.alab-edited-portable-readback/1",
                    "profile": media_bridge.ALAB_RETAINED_PROFILE, "bundle_receipt_sha256": owner.fixture.sha(bundle_receipt),
                    "project_id": final["id"], "generation": imported["generation"], "revision": imported["revision"],
                    "project_value_sha256": "e" * 64, "source_assets": assets, "native_source": descriptor,
                    "project_reopened_equal": True, "all_four_source_blobs_reopened_equal": True,
                    "source_fidelity_unavailable": True, "fresh_generation_observed": True,
                    "whole_original_otio_retained": True, "canonical_project_mutation_performed": False,
                    "local_production_receipts_inherited": False, "execution_authority_issued": False,
                    "native_editor_dispatched_here": False, "human_acceptance": False, "release_authorized": False}
            else:
                self.fail("Unexpected ALab native operation " + mode)
            if mutate is not None:
                mutate(mode, value)
            if output_path is not None:
                output_path.write_bytes((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            output = (json.dumps(value) + "\n").encode()
            # Simulate the SAME native helper's real original retention before
            # returning these unit bytes to the caller; no process is executed.
            owner.fixture._new_original(evidence_prefix.with_name(evidence_prefix.name + ".intent.json"),
                (json.dumps({"state": "INTENT", "command": argv, "timeout": timeout,
                             "invocation_retried": False}, sort_keys=True) + "\n").encode())
            owner.fixture._retain_call(evidence_prefix, returncode=0, stdout=output, stderr=b"")
            if after is not None:
                after(mode)
            return output

        def retain(path, body):
            if custody_failure is not None and path.name == custody_failure[0]:
                raise custody_failure[1]
            return original_writer(path, body)

        with self.original_scope(), patch.object(owner.fixture, "run_firstparty_native", side_effect=run), \
             patch.object(owner.fixture, "_new_original", side_effect=retain):
            return owner.finish_alab_return_once(self.capsule, root, self.database, self.native,
                native_sha, self.raw_path, self.source_input, self.retained)

    def test_returned_source_four_calls_use_external_original_SHA_and_keep_two_separate_commits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            self.prepare_return(Path(temporary).resolve())
            result = self.invoke_return()
            self.assertEqual([row[0] for row in self.calls], ["alab-returned-cut", "alab-returned-readback",
                "alab-edited-bundle", "alab-edited-portable-readback"])
            self.assertEqual((result["additional_history_events"], result["total_original_history_events"]), (2, 9))
            self.assertEqual((result["original_second_frames"], result["edited_second_frames"]), (182, 170))
            self.assertNotEqual(result["source_generation"], result["portable_generation"])
            self.assertEqual([row["index"] for row in result["stage_originals"]], [7, 8])
            self.assertEqual(len(result["source_call_originals"]), 16)
            self.assertEqual(len(self.source_input["source_call_originals"]), 16)
            empty = [value for name, value in result["source_call_originals"].items() if name.endswith(".stderr.bin")]
            self.assertEqual([row["bytes"] for row in empty], [0, 0, 0, 0])
            for key in ("execution_authority_issued", "local_production_receipts_inherited",
                        "per_scene_native_editability_verified", "native_editor_dispatched_here",
                        "automatic_retry", "human_acceptance", "release_authorized"):
                self.assertIs(result[key], False)
            self.assertTrue((self.evidence / "alab-returned-source-input.original.json").is_file())
            self.assertTrue((self.evidence / "alab-edited-portable-reopen.json").is_file())

    def test_returned_preview_rejects_unselected_trim_or_promoted_flags_before_native_call(self):
        import tempfile
        for defect in ("first-scene", "duration", "extra", "native-pass", "clip-count", "lost-property"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                self.prepare_return(Path(temporary).resolve())
                value = self.returned
                if defect == "first-scene": value["native_change_proposals"][0]["scene_id"] = self.base.project["scenes"][0]["id"]
                elif defect == "duration": value["native_change_proposals"][0]["duration"] = {"num": "1", "den": "2"}
                elif defect == "extra": value["native_change_proposals"].append(copy.deepcopy(value["native_change_proposals"][0]))
                elif defect == "native-pass": value["native_final_frames_verified"] = True
                elif defect == "clip-count": value["edited_clip_count"] = 3
                else: value["property_fidelity"].pop()
                self.raw_path.write_bytes(json.dumps(value).encode())
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused): self.invoke_return()
                self.assertEqual(self.calls, [])
                self.assertFalse((self.evidence / "alab-owner-cut-preview.json").exists())

    def test_each_returned_native_unknown_preserves_original_identity_and_stops_without_retry(self):
        import tempfile
        modes = ("alab-returned-cut", "alab-returned-readback", "alab-edited-bundle", "alab-edited-portable-readback")
        for index, mode in enumerate(modes):
            for failure in (TimeoutError, KeyboardInterrupt):
                with self.subTest(mode=mode, failure=failure.__name__), tempfile.TemporaryDirectory() as temporary:
                    self.prepare_return(Path(temporary).resolve())
                    original = failure("unit original unknown terminal")
                    with self.assertRaises(failure) as caught:
                        self.invoke_return(fail_mode=mode, original=original)
                    self.assertIs(caught.exception, original)
                    self.assertEqual([row[0] for row in self.calls], list(modes[:index + 1]))
                    self.assertTrue((self.evidence / "alab-returned-source-input.original.json").is_file())
                    if index:
                        self.assertTrue((self.evidence / "alab-returned-source-receipt.json").is_file())
                    self.assertFalse((self.evidence / "alab-edited-portable-reopen.json").exists())

    def test_returned_receipt_qualification_refuses_wrong_originals_requests_and_generations(self):
        import tempfile
        cases = (("alab-returned-cut", "cut_request_id", "foreign", 1),
                 ("alab-returned-cut", "original_Kdenlive_preview_sha256", "0" * 64, 1),
                 ("alab-returned-readback", "exact_nine_history_events", False, 2),
                 ("alab-edited-bundle", "execution_authority_issued", True, 3),
                 ("alab-edited-portable-readback", "generation", "foreign", 4))
        for selected, field, value, calls in cases:
            with self.subTest(mode=selected, field=field), tempfile.TemporaryDirectory() as temporary:
                self.prepare_return(Path(temporary).resolve())
                def change(mode, result):
                    if mode == selected: result[field] = value
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused): self.invoke_return(mutate=change)
                self.assertEqual(len(self.calls), calls)
                self.assertTrue((self.evidence / "alab-returned-source-receipt.json").is_file())

    def test_returned_original_changed_between_processes_stops_before_the_next_call(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        for target in ("receipt", "preview", "stage", "stdout-and-terminal"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                self.prepare_return(Path(temporary).resolve())
                def after(mode):
                    if mode != "alab-returned-readback": return
                    if target == "stdout-and-terminal":
                        stdout = self.evidence / "alab-returned-cut-cli.stdout.bin"
                        original = stdout.read_bytes()
                        # Same length and still valid JSON, but not the original
                        # process bytes. A matching edited terminal is not enough.
                        changed = b" " + original[:-1]
                        stdout.write_bytes(changed)
                        terminal = self.evidence / "alab-returned-cut-cli.terminal.json"
                        value = json.loads(terminal.read_bytes())
                        value["captured_streams"]["stdout"]["sha256"] = owner.fixture.sha(stdout)
                        terminal.write_bytes((json.dumps(value, sort_keys=True) + "\n").encode())
                        return
                    path = {"receipt": self.evidence / "alab-returned-source-receipt.json",
                            "preview": self.raw_path,
                            "stage": self.evidence / "alab-returned-source-receipt.stage-07.result.original.json"}[target]
                    path.write_bytes(b" " + path.read_bytes())
                error_type = (owner.fixture.KdenliveCompleteFixtureRefused if target == "stdout-and-terminal"
                              else KdenliveReadbackRejected)
                reason = ("alab_owner_call_original_changed_after_its_first_observation" if target == "stdout-and-terminal"
                          else "readback_original_content_changed_from_expected_SHA")
                with self.assertRaisesRegex(error_type, reason): self.invoke_return(after=after)
                self.assertEqual(len(self.calls), 2)
                self.assertFalse((self.evidence / "alab-edited-bundle-receipt.json").exists())

    def test_returned_success_with_failed_original_custody_never_becomes_verified(self):
        import tempfile
        for name, count in (("alab-returned-source-reopen.json", 2), ("alab-edited-portable-reopen.json", 4)):
            with self.subTest(member=name), tempfile.TemporaryDirectory() as temporary:
                self.prepare_return(Path(temporary).resolve())
                original = OSError("unit original writer collision")
                with self.assertRaises(OSError) as caught: self.invoke_return(custody_failure=(name, original))
                self.assertIs(caught.exception, original)
                self.assertEqual(len(self.calls), count)
                self.assertTrue((self.evidence / "alab-returned-source-receipt.json").is_file())
                self.assertFalse((self.evidence / name).exists())


class ImmutableOwnerOriginalReadTests(unittest.TestCase):
    def test_original_native_error_precedes_any_post_call_original_capture(self):
        import tempfile
        import hashlib
        for original in (TimeoutError("original UNKNOWN"),KeyboardInterrupt("original interrupt"),SystemExit(19)):
            with self.subTest(kind=type(original).__name__),tempfile.TemporaryDirectory()as folder:
                root=Path(folder).resolve()
                capsule,evidence=root/"capsule",root/"evidence"
                capsule.mkdir();evidence.mkdir()
                baseline,returned,source=originals()
                source_raw=json.dumps(source).encode()
                digest=hashlib.sha256(source_raw).hexdigest()
                baseline["source_project_sha256"]=digest
                returned["source_project_sha256"]=digest
                source_path=capsule/"source-project.json"
                source_path.write_bytes(source_raw)
                returned_path=evidence/"reopened-edited-cut-preview.json"
                returned_path.write_bytes(json.dumps(returned).encode())
                database=evidence/"source.sqlite3";database.write_bytes(b"unit placeholder, not SQLite evidence")
                binary=root/"owned-example";binary.write_bytes(b"unit binary identity, never executed")
                real=owner.capture_original
                captures=[]
                def capture(path,limit):
                    captures.append(path)
                    if path.name=="owner-cut-commit-receipt.json":raise AssertionError("post-UNKNOWN capture")
                    return real(path,limit)
                with patch.object(owner,"OWNER_EXAMPLE",binary), \
                     patch.object(owner,"import_preview",return_value=baseline), \
                     patch.object(owner,"capture_original",side_effect=capture), \
                     patch.object(owner.fixture,"run_firstparty_native",side_effect=original)as native:
                    with self.assertRaises(type(original))as caught:
                        owner.commit_once(capsule,evidence,database,returned_path)
                self.assertIs(caught.exception,original)
                self.assertEqual(native.call_count,1)
                self.assertEqual(captures,[source_path,returned_path])

    def test_ALab_receipt_first_capture_stays_pinned_before_the_next_native_call(self):
        import tempfile
        from kdenlive_readback import KdenliveReadbackRejected
        helper=KdenliveAlabCanonicalSourceContracts()
        self.addCleanup(helper.doCleanups)
        with tempfile.TemporaryDirectory()as folder:
            helper.prepare(Path(folder).resolve())
            real=owner.capture_original
            changed=[]
            def capture(path,limit):
                result=real(path,limit)
                if path.name=="alab-adoption-receipt.json"and not changed:
                    raw=result[0]
                    path.write_bytes(b" "+raw[:-1])
                    changed.append(path)
                return result
            with patch.object(owner,"capture_original",side_effect=capture):
                with self.assertRaisesRegex(KdenliveReadbackRejected,"readback_original_content_changed_from_expected_SHA"):
                    helper.invoke()
            self.assertEqual(len(changed),1)
            self.assertEqual([row[0]for row in helper.calls],["alab-adopt"])


if __name__ == "__main__":
    unittest.main()
