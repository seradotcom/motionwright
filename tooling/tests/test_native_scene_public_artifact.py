"""Adversarial tests for v0.1 native Blender and Manim public scene evidence."""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_scene_public_artifact as pub

SHA = "a" * 40
PIN = "b" * 40
SECRET = "OWNER_PRIVATE_SESSION_SECRET"

def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()

class NativeSceneEvidenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.private = self.base / "private"
        self.private.mkdir()
        self.out = self.base / "public"

    def fixture(self, scene):
        self.scene = scene
        config = pub.SCENES[scene]
        if scene == "blender":
            self.media = b"glTF" + struct.pack("<II", 2, 32) + b"blob" * 5
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "meshes": 2, "materials": 2}
        else:
            self.media = b"\x00\x00\x00\x18ftyp" + b"isom" + b"\x00" * 16
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "width": 640, "height": 360,
                     "relative_path": "videos/owned/360p12/owned.mp4"}
        project = {"project_id": SECRET, "generation": SECRET,
                   "revision": 9, "scene_id": SECRET}
        self.result = {f"native_{scene}_e2e": "PASS",
                       "motionwright_sha": SHA, "semwright_sha": PIN,
                       "semwright_version": "1.0.0",
                       "capabilities_checked": sorted(config["commands"]),
                       "project": project, config["asset_key"]: asset}
        self.receipt = {f"native_{scene}_e2e": "PASS", **project,
                        "receipt_count": (10 if scene == "manim"
                                          else config["required_count"]),
                        "completed_commands": sorted(config["commands"]),
                        "result": {"native_driver": "driver:blender" if scene == "blender"
                                   else "driver:manim-community",
                                   "artifact": {"sha256": asset["sha256"],
                                                "bytes": asset["bytes"]},
                                   "private_job": SECRET}}
        self.write()
        (self.private / "semwrightd.log").write_text(SECRET)

    def write(self):
        c = pub.SCENES[self.scene]
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / c["evidence"]).write_text(json.dumps(self.receipt))
        (self.private / c["media"]).write_bytes(self.media)

    def reject(self, code):
        with self.assertRaises(pub.PublicationError) as cm:
            pub.publish(self.private, self.out, SHA, PIN, self.scene, "success")
        self.assertEqual(cm.exception.code, code)
        self.assertFalse(self.out.exists())

    def test_verified_media_only_no_original_owner_receipts(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            target = self.base / ("published-" + scene)
            result = pub.publish(self.private, target, SHA, PIN, scene, "success")
            self.assertEqual({f.name for f in target.iterdir()},
                             {"public-evidence.json", pub.SCENES[scene]["media"]})
            self.assertEqual(result["included_files"][0]["sha256"], hash_bytes(self.media))
            self.assertEqual(result["included_files"][0]["bytes"], len(self.media))
            txt = (target / "public-evidence.json").read_text()
            self.assertNotIn(SECRET, txt)
            self.assertFalse(result["v01_release_authorized"])
            self.assertFalse(result["raw_daemon_or_sandbox_logs_uploaded"])
            self.assertFalse(result["native_effects_replayed_by_publisher"])
            self.assertEqual(result["installed_owner_acceptance"], "NOT_RUN")

    def test_manim_polling_count_varies_without_weakening_original_stages(self):
        # No second render: only original status polling adds journal rows.
        # All source/project/receipt/media bytes stay bound and verified.
        self.fixture("manim")
        for count in (5, 8, 10, 11, 20, 256):
            with self.subTest(count=count):
                self.receipt["receipt_count"] = count
                self.write()
                dest = self.base / ("manim-polling-" + str(count))
                report = pub.publish(self.private, dest, SHA, PIN, "manim", "success")
                self.assertEqual(report["included_files"][0]["sha256"],
                                 hash_bytes(self.media))
                self.assertEqual(set(p.name for p in dest.iterdir()),
                                 {"public-evidence.json", pub.SCENES["manim"]["media"]})
                self.assertNotIn(SECRET, (dest / "public-evidence.json").read_text())

    def test_manim_malformed_or_unproven_stages_fail_closed(self):
        self.fixture("manim")
        original = json.loads(json.dumps(self.receipt))
        cases = [
            (True, original["completed_commands"]),
            (0, original["completed_commands"]),
            (4, original["completed_commands"]),
            (257, original["completed_commands"]),
            (10, ["driver.manim-community.render.start",
                  "driver.manim-community.render.status"]),
            (10, ["driver.manim-community.render.start",
                  "driver.manim-community.render.result"]),
            (10, ["driver.manim-community.render.start",
                  "driver.manim-community.render.status",
                  "driver.manim-community.render.result",
                  "driver.manim-community.render.result"]),
            (10, ["driver.manim-community.render.start",
                  "driver.manim-community.render.status",
                  "driver.manim-community.render.result",
                  "driver.other-provider.render.start"]),
        ]
        for i, (count, commands) in enumerate(cases):
            with self.subTest(index=i):
                self.receipt = json.loads(json.dumps(original))
                self.receipt["receipt_count"] = count
                self.receipt["completed_commands"] = commands
                self.write()
                self.reject("native_scene_receipts_not_verified")

    def test_failure_never_copies_broker_material(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            for status in ("failure", "cancelled", "skipped"):
                dest = self.base / ("status-" + scene + status)
                x = pub.publish(self.private, dest, SHA, PIN, scene, status)
                self.assertEqual(x["native_scene_ci_evidence"], "NOT_VERIFIED")
                self.assertEqual(x["included_files"], [])
                self.assertEqual([f.name for f in dest.iterdir()], ["public-evidence.json"])

    def test_source_and_provenance_mismatch_rejected(self):
        self.fixture("blender")
        self.result["semwright_sha"] = "c" * 40
        self.write()
        self.reject("native_scene_receipts_not_verified")
        self.result["semwright_sha"] = PIN
        self.receipt["generation"] = "FOREIGN"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_wrong_digest_or_swapped_media_rejected(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            (self.private / pub.SCENES[scene]["media"]).write_bytes(self.media + b"changed")
            self.reject("native_scene_artifact_integrity_mismatch")

    def test_forged_receipt_digest_refused(self):
        self.fixture("manim")
        self.receipt["result"]["artifact"]["sha256"] = "d" * 64
        self.write()
        self.reject("native_scene_artifact_identity_mismatch")

    def test_missing_native_private_receipts_refused(self):
        self.fixture("blender")
        (self.private / pub.SCENES["blender"]["evidence"]).unlink()
        self.reject("missing_or_linked_public_source")

    def test_duplicate_authority_fields_refused(self):
        self.fixture("blender")
        (self.private / "result.json").write_text(
            '{"motionwright_sha":"first","motionwright_sha":"second"}'
        )
        self.reject("duplicate_evidence_json_key")

    def test_linked_original_native_media_refused(self):
        self.fixture("manim")
        target = self.private / pub.SCENES["manim"]["media"]
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda p: p == target or original(p)):
            self.reject("missing_or_linked_public_source")

    def test_forged_native_success_claim_refused(self):
        self.fixture("blender")
        self.result["native_blender_e2e"] = "FAIL"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_unsupported_mode_invalid_status_and_overlap_refused(self):
        self.fixture("blender")
        for scene, status in (("other", "success"), ("blender", "pending")):
            with self.assertRaises(pub.PublicationError):
                pub.publish(self.private, self.out, SHA, PIN, scene, status)
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.private / "out",
                        SHA, PIN, "blender", "failure")

    def test_workflows_upload_only_curated_assets(self):
        root = Path(__file__).resolve().parents[2]
        for scene in pub.SCENES:
            yml = (root / ".github/workflows" / ("native-" + scene + "-e2e.yml")).read_text()
            self.assertIn("path: verification/native-" + scene + "-public", yml)
            self.assertNotIn("path: verification/native-" + scene + "-e2e\n", yml)
            self.assertIn("test_native_scene_public_artifact.py", yml)



class ManimSourceCustodyTests(unittest.TestCase):
    """Filesystem and report-contract controls, never a native provider oracle."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.private = self.base / "private"
        self.private.mkdir()
        self.output = self.base / "public"
        import native_manim_e2e as wrapper
        self.wrapper = wrapper
        self.patchers = [
            patch.object(wrapper, "EVIDENCE", self.private),
            patch.object(wrapper, "COMMAND_NUMBER", 0),
            patch.object(wrapper, "INSPECTION_NUMBER", 0),
            patch.object(wrapper, "SOURCE_ATTEMPT", None),
            patch.object(wrapper, "SOURCE_PHASE", None),
            patch.object(wrapper, "SOURCE_BEFORE", None),
        ]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def identity(self):
        return {"C": SHA, "T": "c" * 40, "semwright_sha": PIN, "semwright_version": "1.0.0",
                "manim_version": "0.21.0",
                "sources": [{"path": name, "bytes": 1, "sha256": "a" * 64}
                            for name in pub.SOURCE_EVIDENCE_PATHS],
                "binaries": {name: {"bytes": 1, "sha256": "a" * 64} for name in pub.SOURCE_TOOLS},
                "runtime": {"files": 1, "bytes": 1, "inventory_sha256": "a" * 64}}

    @contextmanager
    def source_identity_inputs(self):
        """Physical synthetic inputs; only Git metadata and version are stubbed."""
        root = self.private / "identity-repo"
        sdk = self.private / "identity-sdk"
        runtime = self.private / "identity-runtime"
        for name in self.wrapper.SOURCE_CODE:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC SOURCE IDENTITY INPUT\n")
        sdk_bins = sdk / "bin"
        tools = {
            "MW_EXAMPLE": root / "bin" / "example",
            "MW_DRIVER": root / "bin" / "driver",
            "MW_RUNNER": root / "bin" / "runner",
            "PYTHON": root / "bin" / "python",
            "FFMPEG": root / "bin" / "ffmpeg",
            "FFPROBE": root / "bin" / "ffprobe",
        }
        binaries = list(tools.values()) + [
            sdk_bins / name for name in ("semwright", "semwrightd", "semwright-sandbox")]
        for path in binaries:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"SYNTHETIC NONEXECUTABLE BINARY IDENTITY INPUT\n")
        runtime_files = {
            "site-packages/fixture_package/__init__.py": b"# synthetic package\n",
            "site-packages/fixture_package/module.py": b"VALUE = 7\n",
        }
        for name, content in runtime_files.items():
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        commands = []
        answers = {
            (str(root), ("rev-parse", "HEAD")): SHA,
            (str(root), ("rev-parse", "HEAD^{tree}")): "c" * 40,
            (str(root), ("status", "--porcelain", "--untracked-files=no")): "",
            (str(sdk), ("rev-parse", "HEAD")): PIN,
        }

        def metadata_only(argv, **kwargs):
            self.assertEqual(argv[:2], ["git", "-C"])
            self.assertEqual(kwargs, {"capture_output": True, "check": True, "timeout": 15})
            key = (argv[2], tuple(argv[3:]))
            self.assertIn(key, answers)
            commands.append(list(argv))
            return self.wrapper.subprocess.CompletedProcess(
                argv, 0, (answers[key] + "\n").encode("ascii"), b"")

        with ExitStack() as stack:
            stack.enter_context(patch.multiple(
                self.wrapper, ROOT=root, SEMWRIGHT=sdk, SW_BINS=sdk_bins,
                MANIM_RUNTIME=runtime, GITHUB_SHA=SHA, PIN=PIN,
                SOURCE_PATHS=None, SOURCE_BEFORE=None, **tools))
            stack.enter_context(patch.object(self.wrapper.subprocess, "run", side_effect=metadata_only))
            stack.enter_context(patch.object(self.wrapper, "semwright_version", return_value="1.0.0"))
            invoke = stack.enter_context(patch.object(
                self.wrapper, "run_json", side_effect=AssertionError("native invocation forbidden")))
            launch = stack.enter_context(patch.object(
                self.wrapper.subprocess, "Popen", side_effect=AssertionError("process launch forbidden")))
            try:
                yield {"root": root, "runtime": runtime, "runtime_files": runtime_files,
                       "binaries": binaries, "commands": commands}
            finally:
                invoke.assert_not_called()
                launch.assert_not_called()

    def roots(self):
        result = {}
        for name in ("data", "bundles", "work", "output"):
            directory = self.private / "live" / name
            directory.mkdir(parents=True)
            result[name] = directory
        data = result["data"] / "original"
        data.mkdir()
        (data / "motionwright.sqlite3").write_bytes(b"UNIT OPAQUE BYTE CUSTODY; NOT AN APPLICATION DATABASE\n")
        content = b"UNIT SYNTHETIC CAS BYTES\n"
        value = hash_bytes(content)
        cas = data / "blobs" / "sha256" / value[:2]
        cas.mkdir(parents=True)
        (cas / value).write_bytes(content)
        return result

    def incomplete_attempt(self, roots, *, unknown=False):
        snapshot = self.private / "custody" / "original"
        inventory = pub.copy_source_snapshot(roots, snapshot, ())
        phases = [{"phase": phase, "native_invocations": 0, "native_outcome": "NOT_STARTED",
                   "reader_status": "NOT_RUN", "custody_status": "NOT_RUN", "files": {}}
                  for phase in pub.SOURCE_PHASES]
        phases[0].update(custody_status="RETAINED_ON_RUNNER", custody_inventory=inventory)
        if unknown:
            phases[0].update(native_invocations=1, native_outcome="UNKNOWN")
            runtime = self.private / "attempt" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "motionwright.session").write_text(SECRET)
        attempt = {"schema": pub.SOURCE_SCHEMA, "status": "NOT_VERIFIED",
                   "motionwright_sha": SHA, "semwright_sha": PIN,
                   "inputs_before": self.identity(), "inputs_after": None, "phases": phases,
                   "native_job_retried": False, "human_acceptance": False,
                   "original_runner_files_deleted": False}
        (self.private / "source-roundtrip.json").write_text(json.dumps(attempt))
        return attempt, snapshot

    def source_report_fixture(self, phase="original"):
        # Entirely authored parser controls. The marker program is inert, the MP4
        # is a header, and the measurements are synthetic. Nothing here decodes,
        # compiles Manim, issues a native command, or asserts native evidence.
        import manim_native_readback as reader
        producer, prepared, getter, origin = reader.ReaderTests.source_unit_fixture(phase)
        producer["request_id"] = "native-manim-source-v2-" + phase
        video = b"\x00\x00\x00\x18ftypisom" + b"\0" * 16
        producer["result"].update(native_driver="driver:manim-community", runtime_contract="Manim Community 0.21.0",
                                   artifact={"bytes": len(video), "sha256": hash_bytes(video)})
        source = reader.inspect_sources(producer, prepared, getter, phase, origin_bundle=origin)
        producer_bytes = json.dumps(producer, sort_keys=True).encode()
        count, fps = (29, 25) if phase == "edge25" else (24, 12)
        raw_bytes = count * 640 * 360 * 3
        process = lambda n: {"exit_code": 0, "elapsed_seconds": .01, "stdout_bytes": n,
                             "stderr_bytes": 0, "stderr_sha256": hash_bytes(b""), "whole_process_completed": True}
        left = 110 if phase in {"edit", "edited-reopen"} else 80
        report = {"schema": "motionwright.manim.static-source-readback/1", "status": "PASS", "phase": phase,
                  "classification": "REAL_DECODED_NATIVE_FIXTURE_CONTENT_READBACK_NOT_RELEASE_ACCEPTANCE",
                  "producer_record_sha256": hash_bytes(producer_bytes), "video_sha256": hash_bytes(video),
                  "video_bytes": len(video), "source": source,
                  "media": {"frame_count": count, "frame_rate": str(fps) + "/1", "time_base": "1/12288",
                            "first_pts": "0/1", "end_exclusive": "29/25" if phase == "edge25" else "2",
                            "audio_stream_count": 0, "codec": "h264", "pixel_format": "yuv420p",
                            "all_frame_timestamps_verified": True},
                  "decoder_identity": {name: {"sha256": "a" * 64, "version": name + " version UNIT",
                                              "process": process(128)} for name in ("ffmpeg", "ffprobe")},
                  "probe_process": process(1024), "decode_process": process(raw_bytes),
                  "decoded_rgb24_bytes": raw_bytes, "decoded_rgb24_sha256": "c" * 64,
                  "frames": [{"index": i, "matching_core_fractions": [1., 1., 1., 1.],
                              "observed_visible_bounds": [[460, 40, 560, 90], [left, 60, left + 200, 180], [250, 130, 330, 210]],
                              "stray_saturated_pixels": [0, 0, 0], "rgb24_sha256": "c" * 64} for i in range(count)],
                  "images": [{"name": f"manim-{phase}-frame-{i:04d}.png", "bytes": 128, "sha256": "d" * 64}
                             for i in (0, count - 1)],
                  "thresholds": {"rgb_channel_error": reader.MAX_CHANNEL_ERROR, "boundary_margin": reader.BOUNDARY_MARGIN,
                                 "min_shape_core_fraction": reader.MIN_CORE_FRACTION,
                                 "min_background_fraction": reader.MIN_BACKGROUND_FRACTION,
                                 "bbox_edge_error": reader.BBOX_EDGE_ERROR},
                  "losses": ["static shapes only", "opaque black matte", "no audio or captions",
                             "binary64 geometry projection",
                             "static identical-frame permutations are not observable animation evidence"],
                  "human_acceptance": False, "external_rights_certified": False, "native_job_retried": False}
        return producer_bytes, origin, prepared, getter, video, report

    def projection(self, phase, fixture):
        producer, origin, prepared, getter, video, report = fixture
        return pub.source_readback_projection(phase, producer, origin, prepared, getter, video,
                                              json.dumps(report).encode(), self.identity())

    def test_all_five_typed_clocks_and_parser_controls_remain_distinct_from_native_evidence(self):
        for phase in pub.SOURCE_PHASES:
            with self.subTest(phase=phase):
                fixture = self.source_report_fixture(phase)
                output = self.projection(phase, fixture)
                self.assertEqual(output["frames_checked"], 29 if phase == "edge25" else 24)
                self.assertEqual(output["frame_rate"], 25 if phase == "edge25" else 12)
                self.assertFalse(output["video_decoded_by_publisher"])
                self.assertFalse(output["source_projection_executed_by_publisher"])
                self.assertFalse(output["human_acceptance"])
                self.assertEqual(output["full_readback_sha256"], hash_bytes(json.dumps(fixture[-1]).encode()))

    def test_original_carrier_physical_binding_is_mandatory_even_with_same_prepared_bundle(self):
        fixture = list(self.source_report_fixture())
        fixture[1] += b"CHANGED"
        with self.assertRaises(pub.PublicationError):
            self.projection("original", fixture)
        fixture = self.source_report_fixture()
        fixture[-1]["source"]["origin_carrier_bytes_independently_read"] = False
        with self.assertRaises(pub.PublicationError):
            self.projection("original", fixture)

    def test_tail_frame_clock_thresholds_decoder_and_types_cannot_be_promoted(self):
        changes = [
            lambda r: r["frames"].pop(),
            lambda r: r["frames"][-1].update(index=0),
            lambda r: r["frames"][-1].update(observed_visible_bounds=[[], [], []]),
            lambda r: r["frames"][-1].update(stray_saturated_pixels=[False, 0, 0]),
            lambda r: r["frames"][-1].update(matching_core_fractions=[1, 1, .1, 1]),
            lambda r: r["media"].update(audio_stream_count=False),
            lambda r: r["media"].update(frame_count=True),
            lambda r: r["thresholds"].update(rgb_channel_error=True),
            lambda r: r["decode_process"].update(whole_process_completed=False),
            lambda r: r["decoder_identity"]["ffmpeg"].update(sha256="b" * 64),
            lambda r: r["images"].pop(),
            lambda r: r.update(human_acceptance=True),
        ]
        for change in changes:
            with self.subTest(change=change):
                fixture = self.source_report_fixture()
                change(fixture[-1])
                with self.assertRaises(pub.PublicationError):
                    self.projection("original", fixture)

    def test_json_duplicate_nonfinite_overflow_and_bool_counters_are_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(pub.PublicationError):
                pub.source_json(raw)
        for value in (True, False, 1.0, "1", -1):
            with self.subTest(value=value), self.assertRaises(pub.PublicationError):
                pub.source_uint(value, 5, "unit_counter")
        self.assertEqual(pub.source_uint(1, 5, "unit_counter"), 1)

    def test_complete_snapshot_preserves_exact_bytes_and_excludes_unapproved_empty_roots(self):
        roots = self.roots()
        before = pub.snapshot_live_inventory(roots)
        copied = pub.copy_source_snapshot(roots, self.private / "snapshot", ())
        self.assertEqual(before, copied)
        self.assertEqual(before, pub.snapshot_live_inventory(roots))
        (self.private / "snapshot" / "owner").mkdir()
        with self.assertRaises(pub.PublicationError):
            pub.source_tree_inventory(self.private / "snapshot")


    def test_empty_native_image_directories_survive_complete_custody(self):
        roots = self.roots()
        module_name = "mw_" + "a" * 32
        image_directory = roots["output"] / "images" / module_name
        image_directory.mkdir(parents=True)
        before = pub.snapshot_live_inventory(roots)
        destination = self.private / "snapshot-images"
        copied = pub.copy_source_snapshot(roots, destination, ())
        self.assertEqual(before, copied)
        self.assertEqual(copied, pub.source_tree_inventory(destination))
        self.assertEqual(before, pub.snapshot_live_inventory(roots))
        self.assertIn("output/images", copied["directories"])
        self.assertIn("output/images/" + module_name, copied["directories"])
        self.assertEqual(list(image_directory.iterdir()), [])
        self.assertEqual(list((destination / "output/images" / module_name).iterdir()), [])

    def test_image_directory_custody_keeps_unknown_paths_and_files_rejected(self):
        module_name = "mw_" + "a" * 32
        for name in (
            "output/images/foreign", "output/images/" + module_name + "/child",
            "output/images/" + module_name + ".py", "output/owner",
            "output/images/" + module_name + "/../owner",
        ):
            with self.subTest(path=name), self.assertRaises(pub.PublicationError):
                pub.source_case_directory(name)
        roots = self.roots()
        image_directory = roots["output"] / "images" / module_name
        image_directory.mkdir(parents=True)
        unexpected = image_directory / "MotionwrightScene.png"
        original = b"UNAPPROVED IMAGE FILE CONTROL"
        unexpected.write_bytes(original)
        with self.assertRaises(pub.PublicationError):
            pub.snapshot_live_inventory(roots)
        with self.assertRaises(pub.PublicationError):
            pub.copy_source_snapshot(roots, self.private / "refused-image-snapshot", ())
        self.assertEqual(unexpected.read_bytes(), original)

    def test_unapproved_directory_failure_reports_only_closed_shape(self):
        token = "mw_" + "e" * 32
        examples = [
            ("output/images/" + token + "_execution",
             "unapproved_manim_case_directory:output:images:execution-token:3"),
            ("work/" + token + "_execution/__pycache__",
             "unapproved_manim_case_directory:work:other:__pycache__:3"),
            ("output/Tex",
             "unapproved_manim_case_directory:output:Tex:Tex:2"),
            ("data/original/private-project-alpha",
             "unapproved_manim_case_directory:data:original:other:3"),
            ("output/sensitive-session-credential",
             "unapproved_manim_case_directory:output:other:other:2"),
        ]
        for candidate, expected in examples:
            with self.subTest(candidate=candidate), self.assertRaises(pub.PublicationError) as error:
                pub.source_case_directory(candidate)
            self.assertEqual(str(error.exception), expected)
            self.assertNotIn(token, str(error.exception))
            self.assertNotIn("private-project", str(error.exception))
            self.assertNotIn("sensitive-session", str(error.exception))
        pub.source_case_directory("output/images/" + token)

    def test_exported_bundle_cas_custody_matches_canonical_blob_layout(self):
        roots = self.roots()
        content = b"CANONICAL EXPORTED BUNDLE ORIGINAL CAS CONTROL"
        digest = hash_bytes(content)
        target = roots["bundles"] / "original" / "blobs" / "sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
        manifest = roots["bundles"] / "original" / "manifest.json"
        manifest.write_bytes(b"{}")
        before = pub.snapshot_live_inventory(roots)
        self.assertIn("bundles/original/blobs", before["directories"])
        self.assertIn("bundles/original/blobs/sha256/" + digest[:2], before["directories"])
        originals = [row for row in before["files"] if row["path"] ==
                     "bundles/original/blobs/sha256/" + digest[:2] + "/" + digest]
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0]["kind"], "cas")
        self.assertEqual(originals[0]["sha256"], digest)
        snapshot = self.private / "bundle-closed-cas-custody"
        self.assertEqual(before, pub.copy_source_snapshot(roots, snapshot, ()))
        self.assertEqual(before, pub.source_tree_inventory(snapshot))
        self.assertEqual(before, pub.snapshot_live_inventory(roots))
        self.assertEqual(target.read_bytes(), content)

    def test_bundle_cas_untrusted_bytes_and_unrelated_members_still_reject(self):
        roots = self.roots()
        content = b"KNOWN ORIGINAL BUNDLE CAS"
        digest = hash_bytes(content)
        parent = roots["bundles"] / "original" / "blobs" / "sha256" / digest[:2]
        parent.mkdir(parents=True)
        member = parent / digest
        member.write_bytes(content)
        self.assertEqual(pub.snapshot_live_inventory(roots)["total_bytes"] > 0, True)
        member.write_bytes(b"DIFFERENT ORIGINAL BYTES")
        with self.assertRaisesRegex(pub.PublicationError, "source_live_cas_mismatch"):
            pub.snapshot_live_inventory(roots)
        with self.assertRaisesRegex(pub.PublicationError, "source_snapshot_cas_mismatch"):
            pub.copy_source_snapshot(roots, self.private / "bad-bundle-cas-custody", ())
        self.assertEqual(member.read_bytes(), b"DIFFERENT ORIGINAL BYTES")
        member.write_bytes(content)
        outsider = roots["bundles"] / "original" / "blobs" / "foreign"
        outsider.mkdir()
        with self.assertRaises(pub.PublicationError):
            pub.snapshot_live_inventory(roots)
        outsider.rmdir()
        malicious = parent / "identity.txt"
        malicious.write_bytes(b"OUTSIDE CLOSED BUNDLE FILE PROFILE")
        with self.assertRaisesRegex(pub.PublicationError, "unapproved_manim_case_file"):
            pub.snapshot_live_inventory(roots)
        self.assertEqual(malicious.read_bytes(), b"OUTSIDE CLOSED BUNDLE FILE PROFILE")
        for path in (
            "bundles/original/blobs/sha256/abc",
            "bundles/original/blobs/sha256/" + digest[:2] + "/other",
            "bundles/foreign/blobs",
            "bundles/original/secret",
        ):
            with self.subTest(path=path), self.assertRaises(pub.PublicationError):
                pub.source_case_directory(path)

    def test_missing_or_changed_cas_identity_is_not_repaired_by_custody(self):
        roots = self.roots()
        cas = next((roots["data"] / "original" / "blobs" / "sha256").glob("*/*"))
        cas.write_bytes(b"TAMPERED")
        with self.assertRaises(pub.PublicationError):
            pub.copy_source_snapshot(roots, self.private / "snapshot", ())
        self.assertEqual(cas.read_bytes(), b"TAMPERED")

    def test_actual_emitted_session_bytes_in_approved_file_block_publication(self):
        for label in ("PRIVATE KEY", "OPENSSH PRIVATE KEY"):
            marker = bytes.fromhex("2d2d2d2d2d424547494e20") + label.encode("ascii") + b"-" * 5
            with self.subTest(marker=label), self.assertRaisesRegex(pub.PublicationError, "private_key_material_in_case"):
                pub.source_excludes_emitted_tokens(marker, ())
        pub.source_excludes_emitted_tokens(b"synthetic noncredential media fixture", ())
        roots = self.roots()
        attempt, snapshot = self.incomplete_attempt(roots, unknown=True)
        database = snapshot / "data" / "original" / "motionwright.sqlite3"
        database.write_bytes(SECRET.encode())
        attempt["phases"][0]["custody_inventory"] = pub.source_tree_inventory(snapshot)
        (self.private / "source-roundtrip.json").write_text(json.dumps(attempt))
        with self.assertRaises(pub.PublicationError):
            pub.publish_source_roundtrip(self.private, self.output, SHA, PIN, "failure")
        self.assertEqual(database.read_bytes(), SECRET.encode())
        self.assertEqual(json.loads((self.output / "public-evidence.json").read_text())["native_scene_ci_evidence"],
                         "NOT_VERIFIED")

    def test_unknown_failure_archives_originals_without_requiring_a_pass_or_publishing_owner_files(self):
        import io
        import zipfile
        roots = self.roots()
        _, snapshot = self.incomplete_attempt(roots, unknown=True)
        (self.private / "semwrightd.log").write_text(SECRET)
        original = snapshot / "data" / "original" / "motionwright.sqlite3"
        data = original.read_bytes()
        with patch.object(self.wrapper, "run_json", side_effect=AssertionError("No new invocation")) as invoke:
            report = pub.publish_source_roundtrip(self.private, self.output, SHA, PIN, "failure")
        invoke.assert_not_called()
        self.assertEqual(report["native_scene_ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["phases"][0]["native_outcome"], "UNKNOWN")
        self.assertTrue(report["synthetic_database_files_included"])
        self.assertFalse(report["owner_connection_or_session_material_uploaded"])
        archive = self.output / report["included_files"][0]["name"]
        self.assertEqual(hash_bytes(archive.read_bytes()), report["included_files"][0]["sha256"])
        with zipfile.ZipFile(io.BytesIO(archive.read_bytes())) as zipped:
            self.assertEqual(zipped.read("custody/original/data/original/motionwright.sqlite3"), data)
            self.assertIsNone(zipped.testzip())
            self.assertFalse(any("session" in name or "semwrightd" in name or "config" in name
                                 for name in zipped.namelist()))
        self.assertEqual(original.read_bytes(), data)

    def test_early_failure_keeps_not_verified_and_drafts_are_never_overwritten(self):
        report = pub.publish_source_roundtrip(self.private, self.output, SHA, PIN, "failure")
        self.assertEqual(report["native_scene_ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(report["included_files"], [])
        before = (self.output / "public-evidence.json").read_bytes()
        with self.assertRaises(pub.PublicationError):
            pub.publish_source_roundtrip(self.private, self.output, SHA, PIN, "failure")
        self.assertEqual((self.output / "public-evidence.json").read_bytes(), before)
        draft = self.private / "native.mp4"
        draft.write_bytes(b"EXISTING")
        with self.assertRaises(FileExistsError):
            self.wrapper.phase_record_file(self.private, "native.mp4", b"REPLACEMENT")
        self.assertEqual(draft.read_bytes(), b"EXISTING")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pathname AF_UNIX endpoint")
    def test_actual_142_byte_socket_rejected_and_short_owner_endpoint_preserves_attempt(self):
        short_temporary = tempfile.TemporaryDirectory(prefix="mw-af-", dir="/tmp")
        self.addCleanup(short_temporary.cleanup)
        short_base = Path(short_temporary.name)
        padding = 142 - len(os.fsencode(str(short_base))) - len(b"/broker.sock") - 1
        self.assertGreater(padding, 0)
        legacy_parent = short_base / ("l" * padding)
        legacy_parent.mkdir(mode=0o700)
        legacy_socket = legacy_parent / "broker.sock"
        self.assertEqual(len(os.fsencode(legacy_socket)), 142)
        with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as listener:
            with self.assertRaises(OSError):
                listener.bind(str(legacy_socket))
        self.assertFalse(legacy_socket.exists())

        class LiveListener:
            def poll(self):
                return None

        with self.wrapper.preserved_attempt_directory() as retained_raw:
            retained = Path(retained_raw)
            runtime = retained / "runtime"
            runtime.mkdir(mode=0o700)
            session = runtime / "motionwright.session"
            session.write_bytes(b"ORIGINAL SYNTHETIC SESSION")
            database = retained / "motionwright-data" / "original" / "motionwright.sqlite3"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"ORIGINAL OPAQUE DATABASE CONTROL")
            original = {path: path.read_bytes() for path in (session, database)}
            with patch.object(self.wrapper, "SOURCE_DAEMON_ERRORS", []), \
                    patch.object(self.wrapper.subprocess, "Popen",
                                 side_effect=AssertionError("No Broker or provider")) as launch, \
                    patch.object(self.wrapper, "run_json",
                                 side_effect=AssertionError("No native command")) as invoke:
                with self.wrapper.source_owner_endpoint() as endpoint:
                    endpoint_root = endpoint["root"]
                    endpoint_path = self.wrapper.source_owner_endpoint_path(endpoint)
                    self.assertLessEqual(len(os.fsencode(endpoint_path)), 107)
                    self.assertEqual(endpoint_root.resolve(), endpoint_root)
                    self.assertEqual(stat.S_IMODE(endpoint_root.lstat().st_mode), 0o700)
                    self.assertEqual(endpoint_root.lstat().st_uid, os.getuid())
                    self.assertNotIn(retained, endpoint_root.parents)
                    with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as listener:
                        listener.bind(str(endpoint_path))
                        endpoint_path.chmod(0o600)
                        listener.listen(1)
                        self.wrapper.wait_for_socket(
                            LiveListener(), endpoint_path, self.private / "absent-daemon.log",
                            owner_endpoint=endpoint)
                        self.assertIsNotNone(endpoint["socket_identity"])
                launch.assert_not_called()
                invoke.assert_not_called()
                self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS, [])
            self.assertFalse(endpoint_root.exists())
            self.assertEqual({path: path.read_bytes() for path in original}, original)
        self.assertTrue(retained.is_dir())
        self.assertEqual({path: path.read_bytes() for path in original}, original)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pathname AF_UNIX endpoint")
    def test_endpoint_byte_bound_and_linked_parent_reject_before_first_process(self):
        short_temporary = tempfile.TemporaryDirectory(prefix="mw-ap-", dir="/tmp")
        self.addCleanup(short_temporary.cleanup)
        short_base = Path(short_temporary.name)
        long_parent = short_base / ("l" * 110)
        unicode_parent = short_base / ("é" * 45)
        real_parent = short_base / "canonical-parent"
        linked_parent = short_base / "linked-parent"
        for parent in (long_parent, unicode_parent, real_parent):
            parent.mkdir(mode=0o700)
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        allocate = tempfile.mkdtemp
        for parent, reason in (
                (long_parent, "owner_endpoint_path_too_long"),
                (unicode_parent, "owner_endpoint_path_too_long"),
                (linked_parent, "owner_endpoint_parent_not_canonical")):
            created = []
            def capture_directory(*args, **kwargs):
                path = Path(allocate(*args, **kwargs))
                created.append(path)
                return str(path)
            with self.subTest(parent=parent.name), \
                    patch.multiple(self.wrapper, SOURCE_OWNER_ENDPOINT_PARENT=parent,
                                   SOURCE_PATHS=None, SOURCE_ATTEMPT=None, SOURCE_MODE=False,
                                   SOURCE_DAEMON_ERRORS=[]), \
                    patch.object(self.wrapper.tempfile, "mkdtemp", side_effect=capture_directory), \
                    patch.object(self.wrapper, "exact_inputs",
                                 side_effect=AssertionError("input probe reached")) as inputs, \
                    patch.object(self.wrapper.subprocess, "Popen",
                                 side_effect=AssertionError("process reached")) as launch, \
                    patch.object(self.wrapper, "run_json",
                                 side_effect=AssertionError("seed reached")) as invoke:
                with self.assertRaisesRegex(AssertionError, reason):
                    self.wrapper.main(["--source-roundtrip"])
                inputs.assert_not_called()
                launch.assert_not_called()
                invoke.assert_not_called()
                self.assertIsNone(self.wrapper.SOURCE_ATTEMPT)
            if parent == linked_parent:
                self.assertEqual(created, [])
            else:
                self.assertEqual(len(created), 1)
                endpoint_path = created[0] / "broker.sock"
                self.assertGreater(len(os.fsencode(endpoint_path)), 107)
                if parent == unicode_parent:
                    self.assertLessEqual(len(str(endpoint_path)), 107)
                self.assertFalse(created[0].exists())
                self.assertEqual(list(parent.iterdir()), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pathname AF_UNIX endpoint")
    def test_endpoint_cleanup_preserves_original_unknown_and_foreign_replacement(self):
        row = {"label": "realize-original", "state": "UNKNOWN", "exit_code": None,
               "cleanup_exit_code": None, "native_mutation_retried": False}
        original = self.wrapper.CaseCommandError(row)
        before = dict(row)
        root = path = None
        with patch.object(self.wrapper, "SOURCE_DAEMON_ERRORS", []), \
                patch.object(self.wrapper.subprocess, "Popen",
                             side_effect=AssertionError("No replacement process")) as launch, \
                patch.object(self.wrapper, "run_json",
                             side_effect=AssertionError("No replacement command")) as invoke:
            try:
                with self.assertRaises(self.wrapper.CaseCommandError) as caught:
                    with self.wrapper.source_owner_endpoint() as endpoint:
                        root = endpoint["root"]
                        path = self.wrapper.source_owner_endpoint_path(endpoint)
                        with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as listener:
                            listener.bind(str(path))
                            path.chmod(0o600)
                            self.wrapper.source_owner_endpoint_observe_socket(endpoint)
                        path.unlink()
                        path.write_bytes(b"FOREIGN REPLACEMENT CONTROL")
                        raise original
                self.assertIs(caught.exception, original)
                self.assertEqual(row, before)
                self.assertEqual(path.read_bytes(), b"FOREIGN REPLACEMENT CONTROL")
                self.assertTrue(root.is_dir())
                self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS,
                                 ["owner_endpoint_cleanup:AssertionError"])
                launch.assert_not_called()
                invoke.assert_not_called()
            finally:
                # Only this test created the replacement; production left it intact.
                if path is not None and path.is_file():
                    path.unlink()
                if root is not None and root.exists():
                    root.rmdir()

        # The existing run_case finally records a failed wait/kill/log close in
        # SOURCE_DAEMON_ERRORS. An observed socket is still in use until shutdown
        # is verified, even if its inode, owner and mode still match.
        root = path = None
        with self.subTest(daemon_cleanup="unverified"), \
                patch.object(self.wrapper, "SOURCE_DAEMON_ERRORS", []), \
                patch.object(self.wrapper.subprocess, "Popen",
                             side_effect=AssertionError("No replacement process")) as launch, \
                patch.object(self.wrapper, "run_json",
                             side_effect=AssertionError("No replacement command")) as invoke:
            try:
                with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as listener:
                    with self.assertRaises(self.wrapper.CaseCommandError) as caught:
                        with self.wrapper.source_owner_endpoint() as endpoint:
                            root = endpoint["root"]
                            path = self.wrapper.source_owner_endpoint_path(endpoint)
                            listener.bind(str(path))
                            path.chmod(0o600)
                            listener.listen(1)
                            self.wrapper.source_owner_endpoint_observe_socket(endpoint)
                            observed = endpoint["socket_identity"]
                            self.wrapper.SOURCE_DAEMON_ERRORS.append("TimeoutExpired")
                            raise original
                    self.assertIs(caught.exception, original)
                    self.assertEqual(row, before)
                    self.assertTrue(root.is_dir())
                    self.assertEqual(
                        self.wrapper.source_owner_endpoint_identity(path, directory=False),
                        observed)
                    self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS, [
                        "TimeoutExpired",
                        "owner_endpoint_retained_daemon_cleanup_unverified",
                    ])
                    launch.assert_not_called()
                    invoke.assert_not_called()
            finally:
                # This test, not the production finalizer, owns the retained fixture.
                if path is not None and path.exists():
                    path.unlink()
                if root is not None and root.exists():
                    root.rmdir()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pathname AF_UNIX endpoint")
    def test_endpoint_cleanup_keeps_unobserved_socket_and_unrelated_child(self):
        for observed in (False, True):
            root = path = other = None
            with self.subTest(observed=observed), \
                    patch.object(self.wrapper, "SOURCE_DAEMON_ERRORS", []):
                try:
                    expected = OSError if observed else AssertionError
                    with self.assertRaises(expected):
                        with self.wrapper.source_owner_endpoint() as endpoint:
                            root = endpoint["root"]
                            path = self.wrapper.source_owner_endpoint_path(endpoint)
                            with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as listener:
                                listener.bind(str(path))
                                path.chmod(0o600)
                                if observed:
                                    self.wrapper.source_owner_endpoint_observe_socket(endpoint)
                                    other = root / "unrelated.bin"
                                    other.write_bytes(b"UNRELATED CHILD CONTROL")
                    self.assertTrue(root.is_dir())
                    if observed:
                        self.assertFalse(path.exists())
                        self.assertEqual(other.read_bytes(), b"UNRELATED CHILD CONTROL")
                        self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS,
                                         ["owner_endpoint_cleanup:OSError"])
                    else:
                        self.assertTrue(stat.S_ISSOCK(path.lstat().st_mode))
                        self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS,
                                         ["owner_endpoint_cleanup:AssertionError"])
                finally:
                    # Both entries are synthetic objects created by this test.
                    for candidate in (path, other):
                        if candidate is not None and candidate.exists():
                            candidate.unlink()
                    if root is not None and root.exists():
                        root.rmdir()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pathname AF_UNIX endpoint")
    def test_endpoint_directory_mode_and_identity_are_rechecked_before_use(self):
        with patch.object(self.wrapper, "SOURCE_DAEMON_ERRORS", []):
            with self.wrapper.source_owner_endpoint() as endpoint:
                root = endpoint["root"]
                root.chmod(0o755)
                try:
                    with self.assertRaisesRegex(AssertionError, "owner_endpoint_identity_not_private"):
                        self.wrapper.source_owner_endpoint_path(endpoint)
                finally:
                    root.chmod(0o700)
                moved = root.with_name(root.name + "-held")
                root.rename(moved)
                root.mkdir(mode=0o700)
                try:
                    with self.assertRaisesRegex(AssertionError, "owner_endpoint_directory_changed"):
                        self.wrapper.source_owner_endpoint_path(endpoint)
                    self.assertTrue(root.is_dir())
                    self.assertTrue(moved.is_dir())
                finally:
                    root.rmdir()
                    moved.rename(root)
                self.assertEqual(self.wrapper.source_owner_endpoint_path(endpoint), root / "broker.sock")
            self.assertFalse(root.exists())
            self.assertEqual(self.wrapper.SOURCE_DAEMON_ERRORS, [])

    def test_runtime_inventory_accepts_empty_regular_files_and_retains_exact_identity(self):
        with self.source_identity_inputs() as inputs:
            empty_name = "site-packages/fixture_package/__init__.py"
            (inputs["runtime"] / empty_name).write_bytes(b"")
            inputs["runtime_files"][empty_name] = b""
            expected_rows = [
                {"path": name, "bytes": len(content), "sha256": hash_bytes(content)}
                for name, content in sorted(inputs["runtime_files"].items())
            ]
            expected_runtime = {
                "files": 2,
                "bytes": sum(row["bytes"] for row in expected_rows),
                "inventory_sha256": hash_bytes(json.dumps(
                    expected_rows, sort_keys=True, separators=(",", ":")).encode()),
            }
            output = io.StringIO()
            with patch.object(self.wrapper.sys, "stderr", output):
                before = self.wrapper.source_identity()
                after = self.wrapper.source_identity()
            self.assertEqual(before, after)
            self.assertEqual(before["runtime"], expected_runtime)
            self.assertEqual(pub.source_identity_shape(before, SHA, PIN), before)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(len(inputs["commands"]), 8)
            altered = json.loads(json.dumps(before))
            altered["runtime"]["bytes"] = 0
            with self.assertRaisesRegex(pub.PublicationError, "source_runtime_bytes"):
                pub.source_identity_shape(altered, SHA, PIN)

    def test_source_identity_keeps_empty_code_binary_and_evidence_rejected(self):
        empty = self.private / "empty-evidence"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(pub.PublicationError, "source_case_file_not_bounded"):
            pub.source_file_record(self.private, empty.name)
        with self.source_identity_inputs() as inputs:
            self.wrapper.source_identity()
            targets = (inputs["root"] / self.wrapper.SOURCE_CODE[0], inputs["binaries"][0])
            for path in targets:
                original = path.read_bytes()
                with self.subTest(path=path.name):
                    path.write_bytes(b"")
                    try:
                        with self.assertRaisesRegex(pub.PublicationError, "source_case_file_not_bounded"):
                            self.wrapper.source_identity()
                    finally:
                        path.write_bytes(original)
            self.wrapper.source_identity()

    def test_runtime_oversize_is_rejected_before_open_with_relative_diagnostic(self):
        with self.source_identity_inputs() as inputs:
            name = "site-packages/oversized.bin"
            path = inputs["runtime"] / name
            with path.open("wb") as stream:
                stream.truncate(128 * 1024 * 1024 + 1)
            original_open = Path.open

            def no_large_read(target, *args, **kwargs):
                if target == path and args and args[0] == "rb":
                    self.fail("oversized runtime input was opened for reading")
                return original_open(target, *args, **kwargs)

            output = io.StringIO()
            with patch.object(Path, "open", no_large_read), \
                    patch.object(self.wrapper.sys, "stderr", output), \
                    self.assertRaisesRegex(pub.PublicationError, "source_case_file_not_bounded"):
                self.wrapper.source_identity()
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("manim-runtime-input-rejected="))
            self.assertEqual(json.loads(lines[0].split("=", 1)[1]),
                             {"path": name, "bytes": 128 * 1024 * 1024 + 1, "class": "regular"})
            self.assertNotIn(str(self.base), output.getvalue())
            closed_sink = io.StringIO()
            closed_sink.close()
            with patch.object(self.wrapper.sys, "stderr", closed_sink), \
                    self.assertRaisesRegex(pub.PublicationError, "source_case_file_not_bounded"):
                self.wrapper.source_identity()
            self.assertIsNone(self.wrapper.SOURCE_ATTEMPT)

    def test_empty_runtime_growth_still_fails_the_existing_physical_read_guard(self):
        with self.source_identity_inputs() as inputs:
            name = "site-packages/fixture_package/__init__.py"
            path = inputs["runtime"] / name
            path.write_bytes(b"")
            original_open = Path.open
            requests = []

            class GrowingRead:
                def __init__(self, stream):
                    self.stream = stream
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                def fileno(self):
                    return self.stream.fileno()
                def read(self, count):
                    requests.append(count)
                    with original_open(path, "ab") as writer:
                        writer.write(b"x")
                    return self.stream.read(count)

            def changed_open(target, *args, **kwargs):
                result = original_open(target, *args, **kwargs)
                return GrowingRead(result) if target == path and args and args[0] == "rb" else result

            output = io.StringIO()
            with patch.object(Path, "open", changed_open), \
                    patch.object(self.wrapper.sys, "stderr", output), \
                    self.assertRaisesRegex(pub.PublicationError, "source_case_file_changed"):
                self.wrapper.source_identity()
            self.assertEqual(requests, [1])
            self.assertEqual(path.read_bytes(), b"x")
            line = output.getvalue().strip()
            self.assertTrue(line.startswith("manim-runtime-input-rejected="))
            self.assertEqual(json.loads(line.split("=", 1)[1]),
                             {"path": name, "bytes": 0, "class": "regular"})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux real runtime symlink")
    def test_runtime_inventory_rejects_a_real_symlink_without_disclosing_its_target(self):
        with self.source_identity_inputs() as inputs:
            target = self.private / SECRET
            target.write_bytes(SECRET.encode())
            name = "site-packages/linked-input"
            link = inputs["runtime"] / name
            link.symlink_to(target)
            output = io.StringIO()
            with patch.object(self.wrapper.sys, "stderr", output), \
                    self.assertRaisesRegex(AssertionError, "linked_manim_runtime_input"):
                self.wrapper.source_identity()
            descriptor = json.loads(output.getvalue().strip().split("=", 1)[1])
            self.assertEqual(descriptor, {"path": name, "bytes": link.lstat().st_size,
                                          "class": "symlink"})
            self.assertNotIn(SECRET, output.getvalue())
            self.assertNotIn(str(self.base), output.getvalue())

    def test_runtime_rejection_diagnostic_has_closed_bounded_fields_and_omits_unsafe_names(self):
        path = self.private / "diagnostic-input"
        path.write_bytes(b"x")
        metadata = path.lstat()
        names = (
            "site-packages/" + "/".join(["a" * 250] * 4),
            "site-packages/line\nbreak",
            "site-packages/../outside",
            "site-packages/back\\slash",
            "site-packages/drive:part",
            "/absolute/input",
            "site-packages/" + "a" * 1025,
            "site-packages/nonascii-\u00e9",
        )
        for index, name in enumerate(names):
            with self.subTest(index=index):
                output = io.StringIO()
                with patch.object(self.wrapper.sys, "stderr", output):
                    self.wrapper.runtime_input_rejection(name, metadata)
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertLessEqual(len(lines[0]), 2304)
                self.assertTrue(lines[0].isascii())
                descriptor = json.loads(lines[0].split("=", 1)[1])
                self.assertEqual(set(descriptor), {"path", "bytes", "class"})
                self.assertEqual(descriptor, {"path": name if index == 0 else None,
                                              "bytes": 1, "class": "regular"})
                self.assertNotIn(str(self.base), output.getvalue())
                self.assertNotIn(SECRET, output.getvalue())

    def test_read_budget_catches_a_file_that_grows_during_the_actual_read(self):
        path = self.private / "growing"
        path.write_bytes(b"12345678")
        original_open = Path.open
        requests = []
        class GrowingRead:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, count):
                requests.append(count)
                with original_open(path, "ab") as writer:
                    writer.write(b"x" * 4096)
                return self.stream.read(count)
        def changed_open(target, *args, **kwargs):
            result = original_open(target, *args, **kwargs)
            return GrowingRead(result) if target == path and args and args[0] == "rb" else result
        with patch.object(Path, "open", changed_open), self.assertRaises(pub.PublicationError):
            pub.source_bytes(self.private, "growing")
        self.assertEqual(requests, [9])
        self.assertEqual(path.stat().st_size, 4104)


    def test_portable_reopen_preserves_every_project_field_except_two_actual_import_assignments(self):
        before = {"id": "SYNTHETIC PROJECT", "generation": "00000000-0000-4000-8000-000000000001",
                  "revision": 7, "updated_at": "2026-10-17T00:00:00Z",
                  "draft": {"title": "KEEP", "unselected_profile": {"width": 1080}},
                  "assets": [{"sha256": "f" * 64}]}
        after = json.loads(json.dumps(before))
        after.update(generation="00000000-0000-4000-8000-000000000002",
                     updated_at="2026-10-16T23:59:59Z")
        observed = pub.source_reopened_project(before, after)
        self.assertEqual(observed["before"]["updated_at"], before["updated_at"])
        self.assertEqual(observed["after"]["updated_at"], after["updated_at"])
        self.assertFalse(observed["wall_clock_monotonicity_claimed"])
        for change in (
                lambda p: p.update(generation=before["generation"]),
                lambda p: p.update(revision=8),
                lambda p: p.update(updated_at=True),
                lambda p: p["draft"].update(title="LOST"),
                lambda p: p["draft"]["unselected_profile"].update(width=1920),
                lambda p: p["assets"].clear(),
                lambda p: p.update(extra="NEW")):
            changed = json.loads(json.dumps(after))
            change(changed)
            with self.subTest(changed=changed), self.assertRaises(pub.PublicationError):
                pub.source_reopened_project(before, changed)

    def test_database_inspection_uses_a_separate_copy_and_keeps_original_sqlite_bytes(self):
        import sqlite3
        database = self.private / "original.sqlite3"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE projects(document_json TEXT)")
            connection.execute("INSERT INTO projects VALUES (?)", ('{"unit":"SAVED PROJECT"}',))
            connection.execute("CREATE TABLE production_receipts(receipt_id TEXT,project_id TEXT,generation TEXT,"
                               "revision INTEGER,request_id TEXT,request_sha256 TEXT,command TEXT,stage TEXT,payload_json TEXT)")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        (self.private / "attempt" / "config").mkdir(parents=True)
        before = database.read_bytes()
        original_connect = sqlite3.connect
        opened = []
        handles = []
        def copied_only(target, *args, **kwargs):
            opened.append(str(target))
            self.assertNotIn("original.sqlite3", str(target))
            handle = original_connect(target, *args, **kwargs)
            handles.append(handle)
            return handle
        with patch.object(sqlite3, "connect", side_effect=copied_only):
            snapshot = self.wrapper.inspect_saved_database(database)
        self.assertEqual(snapshot["project"], {"unit": "SAVED PROJECT"})
        self.assertEqual(snapshot["receipts"], [])
        self.assertEqual(len(opened), 1)
        self.assertIn("inspect-0001", opened[0])
        self.assertEqual(len(handles), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            handles[0].execute("SELECT 1")
        self.assertEqual(database.read_bytes(), before)

    def test_custody_error_does_not_rewrite_completed_native_outcome_or_issue_another_job(self):
        roots = self.roots()
        (self.private / "attempt" / "runtime").mkdir(parents=True)
        attempt, _ = self.incomplete_attempt(roots)
        attempt["phases"][0].update(native_invocations=1, native_outcome="COMPLETED",
                                     reader_status="NOT_VERIFIED", custody_status="NOT_RUN")
        self.wrapper.SOURCE_ATTEMPT = attempt
        self.wrapper.SOURCE_PHASE = attempt["phases"][0]
        self.wrapper.SOURCE_BEFORE = self.identity()
        with patch.object(self.wrapper, "source_identity", return_value=self.identity()), \
             patch.object(self.wrapper, "snapshot_source_phase", side_effect=OSError("unit custody failure")), \
             patch.object(self.wrapper, "run_json", side_effect=AssertionError("No new job")) as invoke:
            errors = self.wrapper.finish_source_attempt({}, self.private / "absent-session", AssertionError("unit readback failed"))
        invoke.assert_not_called()
        self.assertTrue(errors)
        self.assertEqual(attempt["phases"][0]["native_outcome"], "COMPLETED")
        self.assertEqual(attempt["phases"][0]["reader_status"], "NOT_VERIFIED")
        self.assertEqual(attempt["status"], "NOT_VERIFIED")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pipe/process-group lifecycle")
    def test_real_command_known_failure_retains_its_exit_and_both_original_streams(self):
        import os
        with self.assertRaises(self.wrapper.CaseCommandError):
            self.wrapper.run_json([sys.executable, "-c",
                "import sys;print('ORIGINAL OUT');sys.stderr.write('ORIGINAL ERR\\n');sys.exit(7)"],
                env=os.environ.copy(), timeout=10, label="realize-original", mutation=True)
        directory = self.private / "commands" / "0001"
        row = json.loads((directory / "command.json").read_text())
        self.assertEqual(row["state"], "EXITED")
        self.assertEqual(row["exit_code"], 7)
        self.assertEqual((directory / "stdout.bin").read_bytes(), b"ORIGINAL OUT\n")
        self.assertEqual((directory / "stderr.bin").read_bytes(), b"ORIGINAL ERR\n")
        self.assertTrue(all(value["complete"] for value in row["streams"].values()))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pipe/process-group lifecycle")
    def test_post_popen_receipt_failure_is_unknown_and_cleanup_does_not_make_it_pass(self):
        import os
        original_write = self.wrapper.write_private_json
        def fail_after_start(path, value):
            if type(value) is dict and value.get("state") == "STARTED":
                raise OSError("unit receipt failure after Popen")
            return original_write(path, value)
        with patch.object(self.wrapper, "write_private_json", side_effect=fail_after_start):
            with self.assertRaises(self.wrapper.CaseCommandError) as error:
                self.wrapper.command_capture([sys.executable, "-c", "import time;time.sleep(30)"],
                    os.environ.copy(), 10, "realize-original", mutation=True)
        row = error.exception.row
        self.assertEqual(row["state"], "UNKNOWN")
        self.assertIsNone(row["exit_code"])
        self.assertIs(type(row["cleanup_exit_code"]), int)
        self.assertFalse(row["native_mutation_retried"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pipe/process-group lifecycle")
    def test_actual_fast_output_and_deadline_fail_closed_and_retain_original_partial_streams(self):
        import os
        for ordinal, (program, timeout, reason) in enumerate((
            ("import sys;sys.stdout.write('x'*(4*1024*1024+2))", 10, "output_budget"),
            ("import time;time.sleep(30)", .1, "deadline")), 1):
            with self.subTest(reason=reason), self.assertRaises(self.wrapper.CaseCommandError) as error:
                self.wrapper.command_capture([sys.executable, "-c", program],
                    os.environ.copy(), timeout, "realize-original", mutation=True)
            row = error.exception.row
            self.assertEqual(row["state"], "UNKNOWN")
            self.assertEqual(row["reason"], reason)
            self.assertIsNone(row["exit_code"])
            self.assertFalse(row["native_mutation_retried"])
            directory = self.private / "commands" / f"{ordinal:04d}"
            self.assertTrue((directory / "command.json").is_file())
            for record in row["streams"].values():
                self.assertFalse(record["complete"])
                self.assertLessEqual(record["bytes_retained"], 4 * 1024 * 1024 + 1)


    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pipe/process-group lifecycle")
    def test_real_positive_command_keeps_complete_original_json_and_zero_exit(self):
        import os
        value = self.wrapper.run_json([sys.executable, "-c", "print('{\"positive\": true}')"],
                                      env=os.environ.copy(), timeout=10, label="readback-original")
        self.assertEqual(value, {"positive": True})
        directory = self.private / "commands" / "0001"
        row = json.loads((directory / "command.json").read_text())
        self.assertEqual(row["state"], "EXITED")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["finalization_errors"], [])
        self.assertEqual(set(row["streams"]), {"stdout", "stderr"})
        self.assertTrue(all(record["complete"] for record in row["streams"].values()))


if __name__ == "__main__":
    unittest.main()
