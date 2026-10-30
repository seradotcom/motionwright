"""Finite synthetic causal controls; these tests do not execute a native renderer."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_incremental_reconstruction as verifier


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Fixture:
    """An explicit synthetic timeline with static head/tail and changed middle."""

    def __init__(self, root, lengths=(2, 3, 2)):
        self.root = Path(root)
        self.lengths = lengths
        self.start = lengths[0]
        self.stop = lengths[0] + lengths[1]
        self.end = sum(lengths)
        self.source = {"motionwright_sha": "a" * 40, "sdk_revision": "b" * 40,
                       "locks_sha256": sha("synthetic-locks")}
        self.project = {"id": "synthetic-n83-fixture", "generation": "00000000-0000-0000-0000-000000000003"}
        self.before = {"revision": 7, "film_sha256": sha("synthetic-film-before"),
                       "dependencies_sha256": sha("synthetic-shared-dependencies")}
        self.after = {"revision": 8, "film_sha256": sha("synthetic-film-after"),
                      "dependencies_sha256": self.before["dependencies_sha256"]}
        before_capture = self.capture("before-original", "before", self.before, self.end)
        dirty_capture = self.capture("dirty-prefix", "after_dirty", self.after, self.stop)
        full_capture = self.capture("full-reference", "full_after", self.after, self.end)
        self.before["segments"] = self.segments(False)
        self.after["segments"] = self.segments(True)
        self.manifest = {
            "schema": verifier.SCHEMA, "evidence_kind": "synthetic",
            "source": self.source, "project": self.project,
            "before": self.before, "after": self.after,
            "invalidated_ranges": [{"start_frame": self.start, "end_frame_exclusive": self.stop,
                                    "causes": ["synthetic-middle-fill-change"]}],
            "captures": [before_capture, dirty_capture, full_capture],
            "full_after_capture_id": "full-reference",
            "costs": {"setup": {"before": {"rendered_frames": self.end}},
                      "operation": {"rendered_frames": self.stop,
                                    "encode": {"measured": False}, "transfer": {"measured": False}},
                      "validation": {"rendered_frames": self.end}},
        }

    def record(self, path):
        data = path.read_bytes()
        return {"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data)}

    def capture(self, identity, role, state, end):
        directory = self.root / identity
        directory.mkdir()
        receipt = directory / "receipt.json"
        receipt.write_text(json.dumps({"synthetic": True, "id": identity, "role": role,
                                       "revision": state["revision"]}), encoding="utf-8")
        frames = []
        for index in range(end):
            color = (20 + index, 80, 120, 255)
            if role != "before" and self.start <= index < self.stop:
                color = (200 + index, 40, 70, 255)
            path = directory / f"source-{index:04d}.png"
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("fixture", identity)
            Image.new("RGBA", (3, 2), color).save(path, pnginfo=metadata,
                                                   compress_level=1 if role == "full_after" else 6)
            frames.append({"source_index": index, **self.record(path)})
        return {"id": identity, "role": role, "source": dict(self.source),
                "project": dict(self.project), "revision": state["revision"],
                "film_sha256": state["film_sha256"],
                "dependencies_sha256": state["dependencies_sha256"],
                "job_id": f"synthetic-job-{identity}", "first_frame": 0,
                "end_frame_exclusive": end, "receipt": self.record(receipt), "frames": frames}

    def segments(self, after):
        result = []
        start = 0
        for number, length in enumerate(self.lengths):
            suffix = "changed" if after and number == 1 else "original"
            result.append({"id": f"scene-{number}", "start_frame": start,
                           "end_frame_exclusive": start + length,
                           "plan_sha256": sha(f"plan-{number}-{suffix}"),
                           "film_sha256": sha(f"scene-film-{number}-{suffix}"),
                           "dependencies_sha256": sha(f"scene-dependencies-{number}"),
                           "capture_id": "dirty-prefix" if after and number == 1 else "before-original"})
            start += length
        return result

    def full_dirty_capture(self):
        capture = self.capture("dirty-entire", "after_dirty", self.after, self.end)
        self.manifest["captures"][1] = capture
        self.manifest["costs"]["operation"]["rendered_frames"] = self.end
        return capture


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="n83-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)
        self.data = self.fixture.manifest

    def check(self):
        return verifier.verify(self.data, self.root)

    def rejected(self, code):
        with self.assertRaises(verifier.VerificationError) as error:
            self.check()
        self.assertEqual(error.exception.code, code)

    def test_actual_rgba_reconstruction_with_original_lineage(self):
        result = self.check()
        self.assertEqual(result["result"], "PASS_PREVIEW_RECONSTRUCTION")
        self.assertEqual(result["status"], "PREVIEW_ONLY")
        self.assertEqual(result["reused_frames"], 4)
        self.assertEqual(result["after_dirty_frames_used"], 3)
        self.assertEqual(result["dirty_frames_changed_from_before"], 3)
        self.assertEqual([frame["frame_index"] for frame in result["frames"]], list(range(7)))
        self.assertEqual([frame["source_revision"] for frame in result["frames"]], [7, 7, 8, 8, 8, 7, 7])

    def test_synthetic_test_never_grants_native_store_or_master_acceptance(self):
        result = self.check()
        self.assertEqual(result["evidence_kind"], "synthetic")
        for field in ("master_eligible", "store_authority_granted", "native_execution_verified", "acceptance_granted"):
            self.assertIs(result[field], False)

    def test_all_physical_prefix_work_accounted_separately(self):
        cost = self.check()["costs"]
        self.assertEqual(cost["setup"]["before"]["rendered_frames"], 7)
        self.assertEqual(cost["operation"]["rendered_frames"], 5)
        self.assertEqual(cost["operation"]["rendered_frames_avoided_vs_full_after"], 2)
        self.assertEqual(cost["validation"]["rendered_frames"], 7)
        self.assertEqual(cost["validation"]["decoded_frames"], 19)
        self.assertEqual(cost["total_original_rendered_frames"], 19)
        self.assertFalse(cost["operation"]["prefix_skip_claimed"])
        self.assertFalse(cost["end_to_end_speedup_claimed"])
        self.assertEqual(cost["operation"]["encode"]["avoided"], 0)
        self.assertEqual(cost["operation"]["transfer"]["avoided"], 0)

    def test_dimensions_and_duration_not_hardcoded_to_historical_fixture(self):
        with tempfile.TemporaryDirectory(prefix="n83-synthetic-other-") as directory:
            fixture = Fixture(directory, lengths=(3, 5, 4))
            result = verifier.verify(fixture.manifest, directory)
            self.assertEqual(len(result["frames"]), 12)
            self.assertEqual(result["reused_frames"], 7)
            self.assertEqual(result["costs"]["operation"]["rendered_frames"], 8)

    def test_png_container_difference_with_identical_rgba_is_allowed(self):
        selected = self.data["captures"][1]["frames"][2]
        reference = self.data["captures"][2]["frames"][2]
        self.assertNotEqual(selected["sha256"], reference["sha256"])
        self.assertEqual(self.check()["comparison"], "DECODED_RGBA_AND_COLOR_METADATA_MATCH_ALL_RECONSTRUCTED_FRAMES")

    def test_reconstructed_files_copy_selected_originals_and_preserve_them(self):
        result = verifier.materialize_preview(self.check(), self.root / "preview")
        for frame in result["frames"]:
            self.assertEqual(Path(frame["reconstructed_path"]).read_bytes(), Path(frame["path"]).read_bytes())
            self.assertTrue(Path(frame["path"]).exists())
            self.assertNotEqual(frame["capture_id"], "full-reference")

    def test_existing_output_is_not_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "user-file"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "OUTPUT_EXISTS"):
            verifier.materialize_preview(self.check(), output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_missing_original_is_not_replaced_from_full_after(self):
        (self.root / self.data["captures"][1]["frames"][2]["path"]).unlink()
        self.rejected("MISSING_ORIGINAL")

    def test_same_size_corrupt_original_is_rejected(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        self.rejected("CORRUPT_ORIGINAL")

    def test_corrupt_png_with_updated_byte_digest_still_fails_decode(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        path.write_bytes(b"\x89PNG\r\n\x1a\ninvalid")
        record.update(self.fixture.record(path))
        self.rejected("PNG_DECODE_FAILED")

    def test_receipt_corruption_is_rejected(self):
        record = self.data["captures"][0]["receipt"]
        (self.root / record["path"]).write_text("changed", encoding="utf-8")
        self.rejected("SIZE_MISMATCH")

    def test_non_png_file_with_matching_hash_is_rejected(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        path.write_bytes(b"synthetic non-png")
        record.update(self.fixture.record(path))
        self.rejected("NOT_PNG")

    def test_capture_stale_revision_rejected(self):
        self.data["captures"][1]["revision"] = 7
        self.rejected("STALE_CAPTURE")

    def test_capture_revision_cannot_be_boolean(self):
        self.data["captures"][0]["revision"] = True
        self.rejected("INVALID_INTEGER")

    def test_nonconsecutive_change_rejected(self):
        self.data["after"]["revision"] = 9
        self.rejected("NONCONSECUTIVE_REVISION")

    def test_different_sdk_rejected(self):
        self.data["captures"][1]["source"]["sdk_revision"] = "c" * 40
        self.rejected("SOURCE_MISMATCH")

    def test_different_lock_rejected(self):
        self.data["captures"][1]["source"]["locks_sha256"] = sha("other-locks")
        self.rejected("SOURCE_MISMATCH")

    def test_different_candidate_rejected(self):
        self.data["captures"][1]["source"]["motionwright_sha"] = "c" * 40
        self.rejected("SOURCE_MISMATCH")

    def test_different_project_rejected(self):
        self.data["captures"][1]["project"]["id"] = "other-project"
        self.rejected("PROJECT_MISMATCH")

    def test_different_generation_rejected(self):
        self.data["captures"][1]["project"]["generation"] = "00000000-0000-0000-0000-000000000004"
        self.rejected("PROJECT_MISMATCH")

    def test_capture_wrong_global_film_rejected(self):
        self.data["captures"][1]["film_sha256"] = self.data["before"]["film_sha256"]
        self.rejected("CAPTURE_STATE_MISMATCH")

    def test_subgraph_film_does_not_have_to_equal_global_film(self):
        self.assertNotEqual(self.data["after"]["film_sha256"], self.data["after"]["segments"][1]["film_sha256"])
        self.assertEqual(self.check()["result"], "PASS_PREVIEW_RECONSTRUCTION")

    def test_overlap_in_segments_rejected(self):
        self.data["after"]["segments"][1]["start_frame"] -= 1
        self.rejected("SEGMENT_ORDER_OR_OVERLAP")

    def test_reordered_segments_rejected(self):
        self.data["after"]["segments"].reverse()
        self.rejected("SEGMENT_ORDER_OR_OVERLAP")

    def test_duplicate_segment_rejected(self):
        self.data["after"]["segments"][1]["id"] = "scene-0"
        self.rejected("DUPLICATE_SEGMENT")

    def test_overlap_in_invalidations_rejected(self):
        self.data["invalidated_ranges"].append(copy.deepcopy(self.data["invalidated_ranges"][0]))
        self.rejected("INVALIDATION_ORDER_OR_OVERLAP")

    def test_changed_dependency_beyond_visible_edit_cannot_reuse(self):
        self.data["after"]["segments"][2]["dependencies_sha256"] = sha("tail-dependency-changed")
        self.rejected("UNDECLARED_DIRTY_SEGMENT")

    def test_explicit_tail_invalidation_cannot_reuse_old_tail(self):
        self.data["invalidated_ranges"][0]["end_frame_exclusive"] = self.fixture.end
        self.data["invalidated_ranges"][0]["causes"].append("synthetic-audio-tail")
        self.rejected("INVALIDATED_FRAME_REUSED")

    def test_global_context_change_requires_conservative_invalidation(self):
        self.data["after"]["dependencies_sha256"] = sha("new-global-font-or-output-context")
        self.rejected("GLOBAL_DEPENDENCIES_REQUIRE_FULL_INVALIDATION")

    def test_changed_segment_cannot_reuse_before_capture(self):
        self.data["after"]["segments"][1]["capture_id"] = "before-original"
        self.rejected("STALE_SEGMENT_REUSE")

    def test_full_after_reference_cannot_fabricate_dirty_source(self):
        self.data["after"]["segments"][1]["capture_id"] = "full-reference"
        self.rejected("REFERENCE_USED_AS_PRODUCER")

    def test_duplicate_job_disallows_relabeling_reference_as_dirty(self):
        self.data["captures"][1]["job_id"] = self.data["captures"][2]["job_id"]
        self.rejected("DUPLICATE_PRODUCER_JOB")

    def test_duplicate_capture_id_rejected(self):
        self.data["captures"][1]["id"] = self.data["captures"][0]["id"]
        self.rejected("DUPLICATE_CAPTURE")

    def test_duplicate_frame_index_rejected(self):
        self.data["captures"][1]["frames"][2]["source_index"] = 1
        self.rejected("FRAME_ORDER_OR_DUPLICATE")

    def test_reordered_frame_records_rejected(self):
        self.data["captures"][1]["frames"].reverse()
        self.rejected("FRAME_ORDER_OR_DUPLICATE")

    def test_repeated_original_path_rejected(self):
        self.data["captures"][1]["frames"][1] = {
            "source_index": 1, **{key: value for key, value in self.data["captures"][1]["frames"][0].items()
                                 if key != "source_index"}}
        self.rejected("DUPLICATE_ORIGINAL_PATH")

    def test_actual_prefix_must_start_at_zero(self):
        self.data["captures"][1]["first_frame"] = self.fixture.start
        self.rejected("NONZERO_NATIVE_PREFIX")

    def test_prefix_must_include_every_native_frame(self):
        self.data["captures"][1]["frames"].pop()
        self.rejected("INCOMPLETE_NATIVE_PREFIX")

    def test_claimed_render_cost_cannot_omit_discarded_prefix(self):
        self.data["costs"]["operation"]["rendered_frames"] = self.fixture.lengths[1]
        self.rejected("RENDER_COST_MISMATCH")

    def test_unmeasured_encode_savings_rejected(self):
        self.data["costs"]["operation"]["encode"]["avoided_frames"] = 100
        self.rejected("UNSUPPORTED_COST_CLAIM")

    def test_unverified_measured_transfer_claim_rejected(self):
        self.data["costs"]["operation"]["transfer"]["measured"] = True
        self.rejected("UNSUPPORTED_COST_CLAIM")

    def test_noop_state_rejected_even_if_revision_changes(self):
        self.data["after"]["film_sha256"] = self.data["before"]["film_sha256"]
        self.rejected("NO_OP")

    def test_no_visual_change_rejected_even_if_digests_claim_change(self):
        for capture in self.data["captures"][1:]:
            for index, record in enumerate(capture["frames"]):
                path = self.root / record["path"]
                Image.new("RGBA", (3, 2), (20 + index, 80, 120, 255)).save(path)
                record.update(self.fixture.record(path))
        self.rejected("NO_VISUAL_CHANGE")

    def test_rgba_mismatch_rejected_when_files_and_hashes_are_valid(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        Image.new("RGBA", (3, 2), (9, 9, 9, 255)).save(path)
        record.update(self.fixture.record(path))
        self.rejected("RGBA_MISMATCH")

    def test_all_regenerated_is_explicitly_not_incremental_pass(self):
        capture = self.fixture.full_dirty_capture()
        for segment in self.data["after"]["segments"]:
            segment["capture_id"] = capture["id"]
        result = self.check()
        self.assertEqual(result["result"], "NO_INCREMENTAL_REUSE")
        self.assertEqual(result["reused_frames"], 0)
        self.assertEqual(result["costs"]["operation"]["rendered_frames_avoided_vs_full_after"], 0)
        with self.assertRaisesRegex(verifier.VerificationError, "NOT_INCREMENTAL"):
            verifier.materialize_preview(result, self.root / "must-not-be-produced")

    def test_full_prefix_production_has_zero_render_saving_even_with_frame_reuse(self):
        capture = self.fixture.full_dirty_capture()
        self.data["after"]["segments"][1]["capture_id"] = capture["id"]
        result = self.check()
        self.assertGreater(result["reused_frames"], 0)
        self.assertEqual(result["costs"]["operation"]["rendered_frames_avoided_vs_full_after"], 0)

    def test_frame_budget_is_enforced(self):
        with self.assertRaisesRegex(verifier.VerificationError, "INVALID_INTEGER"):
            verifier.verify(self.data, self.root, max_frames=4)

    def test_duplicate_json_key_is_not_silently_overwritten(self):
        path = self.root / "duplicate.json"
        path.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
        with self.assertRaisesRegex(verifier.VerificationError, "DUPLICATE_JSON_KEY"):
            verifier.read_manifest(path)

    def test_missing_original_cli_fails_without_preview_fallback(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        (self.root / self.data["captures"][1]["frames"][2]["path"]).unlink()
        output = self.root / "forbidden-fallback"
        process = subprocess.run([sys.executable, str(Path(verifier.__file__)), str(path),
                                  "--artifact-root", str(self.root), "--output-dir", str(output)],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["error_code"], "MISSING_ORIGINAL")
        self.assertFalse(output.exists())

    def test_cli_writes_preview_and_report_with_exact_source_bindings(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        report_path = self.root / "report.json"
        process = subprocess.run([sys.executable, str(Path(verifier.__file__)), str(path),
                                  "--artifact-root", str(self.root), "--output-dir", str(self.root / "preview"),
                                  "--report", str(report_path)], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["source"], self.data["source"])
        self.assertEqual(report["input_manifest_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(report["result"], "PASS_PREVIEW_RECONSTRUCTION")
        self.assertEqual(len(list((self.root / "preview").glob("*.png"))), self.fixture.end)

    def test_generation_is_canonical_uuid_not_sequence_integer(self):
        self.data["project"]["generation"] = 3
        self.rejected("INVALID_GENERATION")

    def test_generation_noncanonical_uuid_is_rejected(self):
        self.data["project"]["generation"] = "00000000000000000000000000000003"
        self.rejected("INVALID_GENERATION")

    def test_absolute_original_descriptor_is_rejected(self):
        record = self.data["captures"][0]["frames"][0]
        record["path"] = str(self.root / record["path"])
        self.rejected("NONCANONICAL_DESCRIPTOR_PATH")

    def test_traversal_original_descriptor_is_rejected(self):
        record = self.data["captures"][0]["frames"][0]
        record["path"] = "before-original/../" + record["path"]
        self.rejected("NONCANONICAL_DESCRIPTOR_PATH")

    def test_windows_drive_descriptor_is_rejected(self):
        self.data["captures"][0]["frames"][0]["path"] = "E:/original.png"
        self.rejected("NONCANONICAL_DESCRIPTOR_PATH")

    def test_windows_backslash_descriptor_is_rejected(self):
        self.data["captures"][0]["frames"][0]["path"] = "before-original\\source-0000.png"
        self.rejected("NONCANONICAL_DESCRIPTOR_PATH")

    def test_aggregate_original_and_decoded_byte_budget(self):
        with mock.patch.object(verifier, "MAX_TOTAL_BYTES", 500):
            self.rejected("AGGREGATE_BYTE_BUDGET")

    def test_single_original_file_budget(self):
        with mock.patch.object(verifier, "MAX_FILE_BYTES", 8):
            self.rejected("INVALID_INTEGER")

    def test_16bit_png_is_not_lossily_converted_and_accepted(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        Image.new("I;16", (3, 2), 1000).save(path)
        record.update(self.fixture.record(path))
        self.rejected("PNG_PROFILE_UNSUPPORTED")

    def test_paletted_png_is_outside_native_profile(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        Image.new("P", (3, 2), 1).save(path)
        record.update(self.fixture.record(path))
        self.rejected("PNG_PROFILE_UNSUPPORTED")

    def test_animated_png_is_outside_native_profile(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        Image.new("RGBA", (3, 2), (1, 2, 3, 255)).save(path, save_all=True,
            append_images=[Image.new("RGBA", (3, 2), (4, 5, 6, 255))], duration=10)
        record.update(self.fixture.record(path))
        self.rejected("PNG_PROFILE_UNSUPPORTED")

    def test_equal_rgba_with_different_gamma_does_not_pass(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", struct.pack(">I", 45455))
        Image.new("RGBA", (3, 2), (202, 40, 70, 255)).save(path, pnginfo=metadata)
        record.update(self.fixture.record(path))
        self.rejected("COLOR_METADATA_MISMATCH")

    def test_equal_rgba_with_different_srgb_intent_does_not_pass(self):
        record = self.data["captures"][1]["frames"][2]
        path = self.root / record["path"]
        metadata = PngImagePlugin.PngInfo()
        metadata.add(b"sRGB", bytes([1]))
        Image.new("RGBA", (3, 2), (202, 40, 70, 255)).save(path, pnginfo=metadata)
        record.update(self.fixture.record(path))
        self.rejected("COLOR_METADATA_MISMATCH")

    def test_artifact_root_symlink_is_rejected_before_resolving(self):
        alias = self.root / "root-link"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"host does not authorize symlink creation: {error}")
        with self.assertRaisesRegex(verifier.VerificationError, "LINKED_ORIGINAL"):
            verifier.verify(self.data, alias)

    def test_artifact_root_linked_ancestor_is_rejected(self):
        container = self.root / "container"
        actual = container / "actual"
        actual.mkdir(parents=True)
        alias = self.root / "ancestor-link"
        try:
            alias.symlink_to(container, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"host does not authorize symlink creation: {error}")
        with self.assertRaisesRegex(verifier.VerificationError, "LINKED_ORIGINAL"):
            verifier.Files(alias / "actual")

    def test_materialization_rechecks_link_even_when_bytes_match(self):
        report = self.check()
        first = report["frames"][0]
        path = Path(first["path"])
        preserved = path.with_name("preserved-original.png")
        path.rename(preserved)
        try:
            path.symlink_to(preserved)
        except OSError as error:
            preserved.rename(path)
            self.skipTest(f"host does not authorize symlink creation: {error}")
        with self.assertRaisesRegex(verifier.VerificationError, "LINKED_ORIGINAL"):
            verifier.materialize_preview(report, self.root / "preview-recheck")

    def test_existing_report_preflight_prevents_preview_side_effect(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.data), encoding="utf-8")
        report = self.root / "report.json"
        report.write_text("keep-original", encoding="utf-8")
        output = self.root / "must-not-copy"
        process = subprocess.run([sys.executable, str(Path(verifier.__file__)), str(manifest),
                                  "--artifact-root", str(self.root), "--output-dir", str(output),
                                  "--report", str(report)], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(json.loads(process.stdout)["error_code"], "REPORT_EXISTS")
        self.assertEqual(report.read_text(encoding="utf-8"), "keep-original")
        self.assertFalse(output.exists())

    def test_validation_counters_measure_read_files_and_decoded_rgba(self):
        result = self.check()
        costs = result["costs"]["validation"]
        png_paths = [self.root / frame["path"] for capture in self.data["captures"]
                     for frame in capture["frames"]]
        receipt_paths = [self.root / capture["receipt"]["path"] for capture in self.data["captures"]]
        png_bytes = sum(path.stat().st_size for path in png_paths)
        receipt_bytes = sum(path.stat().st_size for path in receipt_paths)
        rgba_bytes = 0
        for path in png_paths:
            with Image.open(path) as image:
                rgba_bytes += len(image.convert("RGBA").tobytes())
        self.assertEqual(costs["capture_png_files_read"], len(png_paths))
        self.assertEqual(costs["capture_png_bytes_read"], png_bytes)
        self.assertEqual(costs["producer_receipt_files_read"], len(receipt_paths))
        self.assertEqual(costs["producer_receipt_bytes_read"], receipt_bytes)
        self.assertEqual(costs["original_bytes_read"], png_bytes + receipt_bytes)
        self.assertEqual(costs["original_bytes_sha256_hashed"], png_bytes + receipt_bytes)
        self.assertEqual(costs["decoded_frames"], len(png_paths))
        self.assertEqual(costs["decoded_rgba_bytes"], rgba_bytes)
        self.assertEqual(costs["decoded_rgba_bytes_sha256_hashed"], rgba_bytes)
        self.assertEqual(costs["reference_frame_dimension_and_rgba_digest_comparisons"], self.fixture.end)
        self.assertEqual(costs["reference_color_metadata_digest_comparisons"], self.fixture.end)
        self.assertEqual(costs["dirty_before_dimension_and_rgba_digest_comparisons"], self.fixture.lengths[1])
        self.assertFalse(costs["file_read_bytes_are_disk_io_measurement"])
        for name in ("file_read_and_hash_wall_seconds", "png_profile_wall_seconds", "png_decode_wall_seconds",
                     "rgba_hash_wall_seconds", "comparison_and_selection_wall_seconds", "verifier_wall_seconds"):
            self.assertGreaterEqual(costs[name], 0)

    def test_selection_does_not_claim_materialization_or_native_transfer(self):
        result = self.check()
        selection = result["costs"]["reconstruction"]["selection"]
        materialization = result["costs"]["reconstruction"]["materialization"]
        selected = result["frames"]
        self.assertEqual(selection["frames"], len(selected))
        self.assertEqual(selection["png_bytes"], sum(Path(frame["path"]).stat().st_size for frame in selected))
        self.assertEqual(selection["reused_before_png_bytes"], sum(
            Path(frame["path"]).stat().st_size for frame in selected if frame["producer_role"] == "before"))
        self.assertEqual(selection["after_dirty_png_bytes"], sum(
            Path(frame["path"]).stat().st_size for frame in selected if frame["producer_role"] == "after_dirty"))
        self.assertEqual(materialization["status"], "NOT_RUN")
        self.assertFalse(materialization["executed"])
        self.assertEqual(materialization["bytes_written"], 0)
        self.assertEqual(materialization["output_bytes_rehashed"], 0)
        self.assertIsNone(materialization["wall_seconds"])
        self.assertNotIn("preview_output", result)
        for dimension in ("encode", "transfer"):
            measurement = result["costs"]["operation"][dimension]
            self.assertFalse(measurement["measured"])
            self.assertEqual(measurement["performed_work"], "UNKNOWN")
            self.assertEqual(measurement["avoided"], 0)

    def test_each_frame_lineage_binds_original_job_receipt_and_segment(self):
        result = self.check()
        captures = {capture["id"]: capture for capture in self.data["captures"]}
        full = captures[self.data["full_after_capture_id"]]
        for frame in result["frames"]:
            capture = captures[frame["capture_id"]]
            segment = next(segment for segment in self.data["after"]["segments"]
                           if segment["start_frame"] <= frame["frame_index"] < segment["end_frame_exclusive"])
            lineage = frame["lineage"]
            self.assertEqual(lineage["producer_job_id"], capture["job_id"])
            self.assertEqual(lineage["producer_receipt"], capture["receipt"])
            self.assertEqual(lineage["producer_film_sha256"], capture["film_sha256"])
            self.assertEqual(lineage["producer_dependencies_sha256"], capture["dependencies_sha256"])
            self.assertEqual(lineage["segment"], {key: value for key, value in segment.items() if key != "capture_id"})
            self.assertEqual(frame["source_revision"], capture["revision"])
            reference = frame["comparison_reference"]
            full_frame = full["frames"][frame["source_index"]]
            self.assertEqual(reference["producer_job_id"], full["job_id"])
            self.assertEqual(reference["producer_receipt_sha256"], full["receipt"]["sha256"])
            self.assertEqual(reference["relative_path"], full_frame["path"])
            self.assertEqual(reference["sha256"], full_frame["sha256"])
            self.assertEqual(reference["rgba_sha256"], frame["rgba_sha256"])
            self.assertFalse(reference["used_as_reconstruction_source"])
            self.assertNotEqual(reference["producer_job_id"], lineage["producer_job_id"])

    def test_materialized_hashes_and_copy_counters_are_read_back_from_files(self):
        result = verifier.materialize_preview(self.check(), self.root / "measured-preview")
        materialization = result["costs"]["reconstruction"]["materialization"]
        observed_bytes = 0
        for frame in result["frames"]:
            output = frame["materialized"]
            self.assertEqual(output["path"], f"frame-{frame['frame_index']:08d}.png")
            path = Path(result["preview_output"]) / output["path"]
            self.assertEqual(path, Path(frame["reconstructed_path"]))
            data = path.read_bytes()
            self.assertEqual(output["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(output["bytes"], len(data))
            self.assertEqual(data, Path(frame["path"]).read_bytes())
            observed_bytes += len(data)
        self.assertTrue(materialization["executed"])
        self.assertEqual(materialization["status"], "COMPLETE_VERIFIED_COPY_OF_SELECTED_ORIGINAL_PNGS")
        for name in ("source_files_reread", "files_written", "output_files_rehashed"):
            self.assertEqual(materialization[name], len(result["frames"]))
        for name in ("source_bytes_reread", "bytes_written", "output_bytes_rehashed"):
            self.assertEqual(materialization[name], observed_bytes)
        self.assertGreaterEqual(materialization["wall_seconds"], 0)
        self.assertEqual(result["costs"]["operation"]["transfer"]["performed_work"], "UNKNOWN")

    def test_output_corrupted_after_copy_cannot_get_verified_materialized_digest(self):
        report = self.check()
        original_read = verifier.Files.read

        def corrupt_before_readback(files, record, owner, kind="original"):
            if kind == "materialized_output":
                target = files.root / record["path"]
                data = bytearray(target.read_bytes())
                data[-1] ^= 1
                target.write_bytes(data)
            return original_read(files, record, owner, kind=kind)

        with mock.patch.object(verifier.Files, "read", new=corrupt_before_readback):
            with self.assertRaisesRegex(verifier.VerificationError, "CORRUPT_ORIGINAL"):
                verifier.materialize_preview(report, self.root / "corrupted-preview")
        materialization = report["costs"]["reconstruction"]["materialization"]
        self.assertEqual(materialization["status"], "FAILED")
        self.assertTrue(materialization["executed"])
        self.assertEqual(materialization["files_written"], 1)
        self.assertEqual(materialization["bytes_written"], report["frames"][0]["bytes"])
        self.assertEqual(materialization["output_files_rehashed"], 1)
        self.assertEqual(materialization["output_bytes_rehashed"], report["frames"][0]["bytes"])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["result"], "FAIL_PREVIEW_MATERIALIZATION")
        self.assertNotIn("materialized", report["frames"][0])
        self.assertNotIn("preview_output", report)
        for capture in self.data["captures"]:
            for record in capture["frames"]:
                self.assertEqual(hashlib.sha256((self.root / record["path"]).read_bytes()).hexdigest(), record["sha256"])

    def test_materialization_does_not_reset_aggregate_byte_budget(self):
        report = self.check()
        validation = report["costs"]["validation"]
        copy_bytes = sum(Path(frame["path"]).stat().st_size for frame in report["frames"])
        limit = validation["original_bytes_read"] + validation["decoded_rgba_bytes"] + 2 * copy_bytes - 1
        output = self.root / "over-budget-preview"
        with mock.patch.object(verifier, "MAX_TOTAL_BYTES", limit):
            with self.assertRaisesRegex(verifier.VerificationError, "AGGREGATE_BYTE_BUDGET"):
                verifier.materialize_preview(report, output)
        self.assertFalse(output.exists())

    def test_cli_input_binding_covers_report_lineage_and_materialized_outputs(self):
        path = self.root / "lineage-input.json"
        path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        report_path = self.root / "lineage-report.json"
        process = subprocess.run([sys.executable, str(Path(verifier.__file__)), str(path),
                                  "--artifact-root", str(self.root), "--output-dir", str(self.root / "lineage-preview"),
                                  "--report", str(report_path)], capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result["input_manifest_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(result["costs"]["reconstruction"]["materialization"]["executed"])
        for frame in result["frames"]:
            self.assertIn("producer_receipt", frame["lineage"])
            self.assertEqual(frame["sha256"], frame["materialized"]["sha256"])
        for field in ("native_execution_verified", "store_authority_granted", "master_eligible", "acceptance_granted"):
            self.assertIs(result[field], False)

    def test_cli_failed_copy_preserves_consumed_work_without_pass(self):
        path = self.root / "failed-copy-input.json"
        path.write_text(json.dumps(self.data), encoding="utf-8")
        report_path = self.root / "failed-copy-report.json"
        original_read = verifier.Files.read

        def corrupt_output(files, record, owner, kind="original"):
            if kind == "materialized_output":
                target = files.root / record["path"]
                data = bytearray(target.read_bytes())
                data[-1] ^= 1
                target.write_bytes(data)
            return original_read(files, record, owner, kind=kind)

        output = io.StringIO()
        with mock.patch.object(verifier.Files, "read", new=corrupt_output), contextlib.redirect_stdout(output):
            code = verifier.main([str(path), "--artifact-root", str(self.root),
                                  "--output-dir", str(self.root / "failed-copy-output"),
                                  "--report", str(report_path)])
        self.assertEqual(code, 1)
        result = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["result"], "REJECTED")
        self.assertEqual(result["error_code"], "CORRUPT_ORIGINAL")
        self.assertEqual(result["input_manifest_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        costs = result["costs"]["reconstruction"]["materialization"]
        self.assertEqual(costs["status"], "FAILED")
        self.assertEqual(costs["files_written"], 1)
        self.assertGreater(costs["bytes_written"], 0)
        self.assertEqual(costs["source_bytes_reread"], costs["bytes_written"])
        self.assertFalse(result["store_authority_granted"])


if __name__ == "__main__":
    unittest.main()
