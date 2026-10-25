"""Causal wrapper admission controls; all binaries and wheel records are synthetic.

No Manim, codec, Driver Host, Broker, render or native decoder is executed here.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_e2e as wrapper


RECEIPT = {
    "schema": 2, "manim_version": "0.21.0", "pyav_version": "15.0.0",
    "source_color_policy": "srgb-rgba8-opaque-v1",
    "color_recipe": "srgb-to-rec709-full12-v2",
}
VERSIONS = {"manim_version": "0.21.0", "pyav_version": "15.0.0"}


class RuntimeReceiptAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.packages = self.runtime / "site-packages"
        self.packages.mkdir(parents=True)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.inventory = self.root / "verification/native-manim-e2e/runtime-inventory.json"
        self.inventory.parent.mkdir(parents=True)
        self.binaries = {}
        for name in ("python", "ffmpeg", "ffprobe"):
            path = self.root / (name + "-synthetic-unit-binary")
            path.write_bytes(("synthetic unit fixture: " + name).encode())
            self.binaries[name] = path
        self.native_file = self.packages / "av/fake-core.so"
        self.native_file.parent.mkdir()
        self.native_file.write_bytes(b"synthetic unit native-file fixture, never loaded")
        for name, value in (
                ("ROOT", self.root), ("MANIM_RUNTIME", self.runtime),
                ("PYTHON", self.binaries["python"]), ("FFMPEG", self.binaries["ffmpeg"]),
                ("FFPROBE", self.binaries["ffprobe"]),
                ("GITHUB_SHA", "1" * 40), ("PIN", "2" * 40)):
            patcher = mock.patch.object(wrapper, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1",
        })
        env.start()
        self.addCleanup(env.stop)

    @contextlib.contextmanager
    def entry(self, receipt, versions=None):
        (self.runtime / "runtime.json").write_text(json.dumps(receipt), encoding="utf-8")
        observed = VERSIONS if versions is None else versions
        with mock.patch.object(Path, "exists", return_value=True), \
             mock.patch.object(wrapper.shutil, "which", return_value="unit-bwrap"), \
             mock.patch.object(wrapper.subprocess, "check_output",
                               side_effect=[wrapper.PIN, json.dumps(observed)]) as probe, \
             mock.patch.object(wrapper, "_run_single") as native, \
             mock.patch.object(sys, "argv", ["native_manim_e2e.py",
                                            "--scenario", "static-scene"]), \
             contextlib.redirect_stdout(io.StringIO()):
            yield probe, native

    def inventory_document(self):
        (self.runtime / "runtime.json").write_text(json.dumps(RECEIPT), encoding="utf-8")
        row = {"path": "av/fake-core.so", "bytes": self.native_file.stat().st_size,
               "sha256": wrapper.digest(self.native_file)}
        row["wheel_sha256"] = row["sha256"]
        return {
            "schema": "motionwright.manim.runtime-inventory/2",
            "context": {"source_sha": wrapper.GITHUB_SHA, "semwright_sha": wrapper.PIN,
                        "run_id": "12345", "run_attempt": "1"},
            **VERSIONS,
            "runtime_receipt_sha256": wrapper.digest(self.runtime / "runtime.json"),
            "binaries": {name: {"path": str(path), "bytes": path.stat().st_size,
                                "sha256": wrapper.digest(path)}
                         for name, path in self.binaries.items()},
            "pyav_wheel": {"filename": "av-15.0.0-unit-test-only.whl",
                           "bytes": 123, "sha256": "3" * 64},
            "libav_versions": {"libavcodec": [99, 0, 0]},
            "pyav_native_files": [row],
            "synthetic_unit_control": True,
        }

    def write_inventory(self, document):
        raw = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        self.inventory.write_bytes(raw)
        return raw

    def test_exact_schema2_and_observed_versions_reach_native_callback_once(self):
        with self.entry(RECEIPT) as (probe, native):
            wrapper.main()
        self.assertEqual(probe.call_count, 2)
        native.assert_called_once()
        self.assertEqual(native.call_args.args[0].scenario, "static-scene")
        self.assertEqual(probe.call_args.args[0][0], str(self.binaries["python"]))
        self.assertEqual(probe.call_args.kwargs["env"]["PYTHONPATH"], str(self.packages))

    def test_schema1_stops_before_runtime_probe_or_native_callback(self):
        with self.entry({"schema": 1, "manim_version": "0.21.0"}) as (probe, native):
            with self.assertRaisesRegex(SystemExit, "runtime receipt"):
                wrapper.main()
        self.assertEqual(probe.call_count, 1)  # Only the pre-existing SDK source check.
        native.assert_not_called()

    def test_changed_missing_extra_and_wrong_type_receipts_stop_before_probe(self):
        invalid = []
        for key in RECEIPT:
            missing = dict(RECEIPT)
            del missing[key]
            invalid.append(missing)
            invalid.append({**RECEIPT, key: "changed"})
        invalid.extend([{**RECEIPT, "extra": "not admitted"},
                        {**RECEIPT, "schema": 2.0}, {**RECEIPT, "schema": True},
                        [], None])
        for receipt in invalid:
            with self.subTest(receipt=receipt), self.entry(receipt) as (probe, native):
                with self.assertRaisesRegex(SystemExit, "runtime receipt"):
                    wrapper.main()
                self.assertEqual(probe.call_count, 1)
                native.assert_not_called()

    def test_observed_package_version_mismatch_stops_before_native_callback(self):
        for observed in ({**VERSIONS, "manim_version": "0.20.0"},
                         {**VERSIONS, "pyav_version": "16.0.0"},
                         {"manim_version": "0.21.0"}, {**VERSIONS, "extra": "unknown"}):
            with self.subTest(observed=observed), self.entry(RECEIPT, observed) as (probe, native):
                with self.assertRaisesRegex(SystemExit, "runtime versions"):
                    wrapper.main()
                self.assertEqual(probe.call_count, 2)
                native.assert_not_called()

    def test_inventory_retains_original_bytes_bound_to_actual_binaries(self):
        raw = self.write_inventory(self.inventory_document())
        observed = wrapper.retain_runtime_inventory(self.evidence)
        self.assertEqual(observed, hashlib.sha256(raw).hexdigest())
        self.assertEqual((self.evidence / "runtime-inventory.json").read_bytes(), raw)

    def test_inventory_context_or_binary_mismatch_cannot_publish(self):
        baseline = self.inventory_document()
        changed_context = copy.deepcopy(baseline)
        changed_context["context"]["source_sha"] = "4" * 40
        changed_binary = copy.deepcopy(baseline)
        changed_binary["binaries"]["python"]["sha256"] = "5" * 64
        changed_receipt = copy.deepcopy(baseline)
        changed_receipt["runtime_receipt_sha256"] = "6" * 64
        for document in (changed_context, changed_binary, changed_receipt):
            with self.subTest(document=document):
                self.write_inventory(document)
                with self.assertRaises(ValueError):
                    wrapper.retain_runtime_inventory(self.evidence)
                self.assertFalse((self.evidence / "runtime-inventory.json").exists())

    def test_inventory_native_binary_drift_or_escape_cannot_publish(self):
        baseline = self.inventory_document()
        changed_digest = copy.deepcopy(baseline)
        changed_digest["pyav_native_files"][0]["sha256"] = "7" * 64
        changed_wheel = copy.deepcopy(baseline)
        changed_wheel["pyav_native_files"][0]["wheel_sha256"] = "8" * 64
        changed_path = copy.deepcopy(baseline)
        changed_path["pyav_native_files"][0]["path"] = "../foreign.so"
        for document in (changed_digest, changed_wheel, changed_path):
            with self.subTest(document=document):
                self.write_inventory(document)
                with self.assertRaises(ValueError):
                    wrapper.retain_runtime_inventory(self.evidence)
                self.assertFalse((self.evidence / "runtime-inventory.json").exists())

    def test_existing_inventory_destination_is_never_overwritten(self):
        self.write_inventory(self.inventory_document())
        target = self.evidence / "runtime-inventory.json"
        target.write_bytes(b"retained earlier outcome")
        with self.assertRaises(FileExistsError):
            wrapper.retain_runtime_inventory(self.evidence)
        self.assertEqual(target.read_bytes(), b"retained earlier outcome")


class OriginalNativeVideoAdmissionTests(unittest.TestCase):
    """Synthetic original-result custody fixtures; no native execution is claimed."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.number = 0

    def fixture(self):
        import struct
        import zlib
        self.number += 1
        root = self.root / str(self.number)
        output, work, evidence = root / "output", root / "work", root / "evidence"
        for path in (output, work, evidence):
            path.mkdir(parents=True)
        token = "a" * 32
        job = "manim-" + token
        relative = f"videos/mw_{token}/360p30/mw_{token}.mp4"
        video = output / relative
        video.parent.mkdir(parents=True)
        video.write_bytes(b"synthetic encoded-media fixture; never decoded")
        source = work / f"mw_{token}.py"
        source.write_bytes(b"# Synthetic source fixture; never executed.\n")
        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
        rgba = b"\x00\x00\x00\xff" * 640
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 360, 8, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress((b"\x00" + rgba) * 360))
               + chunk(b"IEND", b""))
        report_path = output / (relative + ".decoded-video.json")
        frame_root = output / (relative + ".decoded-video.json.frames")
        frame_root.mkdir()
        frames = []
        for index in range(3):
            name = f"{index:06}.png"
            (frame_root / name).write_bytes(png)
            frames.append({
                "index": index, "file": name, "png_bytes": len(png),
                "png_sha256": hashlib.sha256(png).hexdigest(),
                "native_metadata": {
                    "scope": "ffmpeg-showinfo-avframe-color-clock-yuv-passthrough-v1",
                    "index": index, "pts": index * 512, "duration": 512,
                    "time_base_num": 1, "time_base_den": 15360,
                    "color": {"matrix": 1, "range": 1, "primaries": 1, "transfer": 1},
                    "yuv_sha256": "c" * 64,
                },
            })
        report = {
            "schema": "motionwright.native-video-observation/1",
            "artifact_sha256": wrapper.digest(video), "artifact_bytes": video.stat().st_size,
            "source_sha256": wrapper.digest(source),
            "decoder": {"name": "pyav", "version": "15.0.0", "libraries": {}},
            "width": 640, "height": 360, "fps_num": 30, "fps_den": 1,
            "frame_count": 3, "video_streams": 1, "audio_streams": 0,
            "codec": "h264", "pixel_format": "yuv420p",
            "stream_color": {"matrix": 1, "range": 1, "primaries": 1, "transfer": 1},
            "frame_color": {"matrix": 1, "range": 1, "primaries": None, "transfer": None},
            "alpha_min": 255, "alpha_max": 255, "all_pixels_opaque": True,
            "rgba_sequence_sha256": "d" * 64, "retained_png_bytes": 3 * len(png),
            "frames": frames,
        }
        # Deliberately noncanonical whitespace must be retained, not re-encoded.
        report_path.write_bytes((json.dumps(report, indent=1) + "\n").encode())
        summary = {key: copy.deepcopy(value) for key, value in report.items() if key != "frames"}
        summary.update(opaque_rec709_h264_without_audio=True,
                       square_progressive_all_frames=True, native_metadata_frame_count=3)
        artifact = {
            "relative_path": relative, "sha256": report["artifact_sha256"],
            "bytes": report["artifact_bytes"], "plan_sha256": "e" * 64,
            "decoded_video": {
                "relative_path": relative + ".decoded-video.json",
                "sha256": wrapper.digest(report_path), "bytes": report_path.stat().st_size,
                "summary": summary,
            },
        }
        provenance = {
            "provider": "driver:manim-community", "source": "driver",
            "provider_version": "0.1.0", "capability_version": "1.0.0",
            "provider_generation": 17, "descriptor_sha256": "f" * 64,
        }
        observed = {
            "provenance_checked": True,
            "data": {"job_ref": job, "state": "succeeded", "artifact": artifact},
            "execution": {"backend": "driver:manim-community", "provenance": provenance},
        }
        binding = {"project_id": "project-unit", "generation": "generation-unit", "revision": 7}
        receipt = {
            **binding, "command": "driver.manim-community.render.result", "stage": "completed",
            "payload": {"result": copy.deepcopy(observed), "job_ref": job},
        }
        native = {
            **binding, "native_manim_camera_dispatch": "PASS",
            "result": {
                "job_ref": job, "plan_sha256": "e" * 64,
                "profile": {"width": 640, "height": 360, "frame_rate": 30},
                "artifact": {key: artifact[key] for key in ("relative_path", "sha256", "bytes")},
                "result": {"replayed": False, "result": observed, "receipt": receipt},
            },
            "original_result_receipt": copy.deepcopy(receipt),
            "original_start_receipt": {
                "payload": {"result": {
                    "data": {"job_ref": job},
                    "execution": {"provenance": {"provider_generation": 17}},
                }},
            },
        }
        capabilities = [{
            "id": "driver.manim-community.render.result", "version": "1.0.0",
            "provenance": {
                "provider": "driver:manim-community", "source": "driver",
                "source_version": "0.1.0", "descriptor_sha256": "f" * 64,
            },
        }]
        return {"native": native, "capabilities": capabilities, "output": output, "work": work,
                "evidence": evidence, "video": video, "source": source, "report": report_path,
                "frame_root": frame_root}

    @staticmethod
    def sync_receipt(case):
        returned = case["native"]["result"]["result"]
        returned["receipt"]["payload"]["result"] = copy.deepcopy(returned["result"])
        case["native"]["original_result_receipt"] = copy.deepcopy(returned["receipt"])

    def call(self, case):
        with mock.patch.object(wrapper.subprocess, "Popen", side_effect=AssertionError("no process")), \
             mock.patch.object(wrapper.subprocess, "run", side_effect=AssertionError("no process")):
            return wrapper.require_original_native_video(
                case["native"], case["capabilities"], case["output"], case["work"], case["evidence"],
            )

    def reject(self, case, code):
        before = copy.deepcopy(case["native"])
        with self.assertRaisesRegex(ValueError, code):
            self.call(case)
        self.assertEqual(case["native"], before)
        self.assertEqual(case["native"]["result"]["result"]["result"]["data"]["state"], "succeeded")
        self.assertFalse((case["evidence"] / "native-video-admission.json").exists())

    def test_n3_complete_original_retains_api_nulls_and_byte_exact_files(self):
        case = self.fixture()
        original = case["report"].read_bytes()
        before = copy.deepcopy(case["native"])
        admitted = self.call(case)
        self.assertEqual(admitted["status"], "PASS")
        self.assertIsNone(admitted["api_frame_color"]["primaries"])
        self.assertIsNone(admitted["api_frame_color"]["transfer"])
        self.assertEqual(admitted["native_metadata_frame_count"], 3)
        retained = case["evidence"] / "native-video-originals"
        self.assertEqual((retained / "decoded-video.json").read_bytes(), original)
        self.assertEqual((retained / "source.py").read_bytes(), case["source"].read_bytes())
        for index in range(3):
            name = f"{index:06}.png"
            self.assertEqual((retained / "decoded-video.json.frames" / name).read_bytes(),
                             (case["frame_root"] / name).read_bytes())
        self.assertEqual(case["native"], before)

    def test_n3_native_success_without_decoded_report_is_rejected(self):
        case = self.fixture()
        del case["native"]["result"]["result"]["result"]["data"]["artifact"]["decoded_video"]
        self.sync_receipt(case)
        self.reject(case, "n3_complete_observation_missing")

    def test_n3_rust_refusal_or_partial_native_roster_is_rejected(self):
        for key, value in (("opaque_rec709_h264_without_audio", False),
                           ("native_metadata_frame_count", 2)):
            with self.subTest(key=key):
                case = self.fixture()
                case["native"]["result"]["result"]["result"]["data"]["artifact"][
                    "decoded_video"]["summary"][key] = value
                self.sync_receipt(case)
                self.reject(case, "n3_original_rust_admission")

    def test_n3_report_digest_or_raw_summary_mismatch_is_rejected(self):
        case = self.fixture()
        case["report"].write_bytes(case["report"].read_bytes() + b" ")
        self.reject(case, "n3_original_report_digest")
        case = self.fixture()
        case["native"]["result"]["result"]["result"]["data"]["artifact"]["decoded_video"][
            "summary"]["frame_color"]["primaries"] = 1
        self.sync_receipt(case)
        self.reject(case, "n3_original_summary_binding")

    def test_n3_changed_source_or_media_is_rejected(self):
        case = self.fixture()
        case["source"].write_bytes(b"# changed source\n")
        self.reject(case, "n3_original_report_binding")
        case = self.fixture()
        case["video"].write_bytes(b"changed encoded source")
        self.reject(case, "n3_original_media_digest")

    def test_n3_missing_middle_frame_or_changed_png_is_rejected(self):
        case = self.fixture()
        (case["frame_root"] / "000001.png").unlink()
        self.reject(case, "n3_original_frame_files")
        case = self.fixture()
        (case["frame_root"] / "000001.png").write_bytes(b"changed PNG")
        self.reject(case, "n3_original_frame_digest")

    def test_n3_changed_generation_or_descriptor_is_rejected(self):
        case = self.fixture()
        case["native"]["original_start_receipt"]["payload"]["result"]["execution"][
            "provenance"]["provider_generation"] = 18
        self.reject(case, "n3_original_provider_generation")
        case = self.fixture()
        case["capabilities"][0]["provenance"]["descriptor_sha256"] = "0" * 64
        self.reject(case, "n3_original_descriptor")

    def test_n3_foreign_job_or_replayed_result_is_rejected(self):
        case = self.fixture()
        case["native"]["result"]["result"]["result"]["data"]["job_ref"] = "manim-" + "b" * 32
        self.sync_receipt(case)
        self.reject(case, "n3_original_job")
        case = self.fixture()
        case["native"]["result"]["result"]["replayed"] = True
        self.reject(case, "n3_original_completed_receipt")


if __name__ == "__main__":
    unittest.main()
