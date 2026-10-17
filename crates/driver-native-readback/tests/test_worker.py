"""Fixed-adapter boundary tests; these do not execute Driver Host or a mux.

The success fixture below is deliberately synthetic. Its only purpose is to
check bounded transport, call wiring and truthful scope flags. Existing real
AAC calibration belongs to the single reader's own test lane.
"""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

CRATE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_readback_host_worker", CRATE / "src/worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
READER_PATH = Path(os.environ.get("MOTIONWRIGHT_READBACK_SOURCE",
    str(CRATE.parents[1] / "tooling/native_mlt_mix_readback.py")))
READER_SOURCE = READER_PATH.read_text(encoding="utf-8")


def synthetic_report():
    content = {"reference_sample_frames":96_000, "decoded_sample_frames":96_256,
        "decoded_access_unit_suffix_samples":256, "reference_pcm_sha256":"d" * 64,
        "decoded_pcm_sha256":"e" * 64, "logical_interval":[0,96_000],
        "all_logical_samples_compared":True, "alignment":[], "limits":{},
        "windows":[{"fixture":"synthetic_transport_only"}] * 94,
        "short_energy_windows":[{"fixture":"synthetic_transport_only"}] * 5_998}
    return {"schema":"motionwright.native-original-mix-readback/1",
        "technical_content_readback":"PASS", "reference_wav_sha256":"a" * 64,
        "final_mp4_sha256":"b" * 64, "clock":{"fixture":"synthetic_transport_only"},
        "content":content, "listening_approval":False, "rights_approval":False,
        "editable_source_reopen_verified":False, "independent_loudness_verified":False}


def packet(binding=None):
    if binding is None:
        binding = {"clock":{"frames":60,"width":64,"height":64,"fps_num":30,"fps_den":1},
            "tool_pins":{"python_sha256":"1" * 64,"ffmpeg_sha256":"2" * 64,"ffprobe_sha256":"3" * 64},
            "reference_wav_sha256":"a" * 64,"master_sha256":"b" * 64}
    binding_json = json.dumps(binding, separators=(",", ":"))
    return {"binding_json":binding_json,
        "binding_sha256":hashlib.sha256(binding_json.encode()).hexdigest(),
        "reader_source":READER_SOURCE,
        "reference_name":"mw-original-mix-test.wav", "master_name":"mw-test-multisegment-master.mp4"}


class FixedHostAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = worker._load_reader(READER_SOURCE)

    def test_exact_reader_pin_loads_and_changed_source_is_never_executed(self):
        self.assertEqual(hashlib.sha256(READER_PATH.read_bytes()).hexdigest(), worker.READER_SHA256)
        self.assertEqual(self.reader.inspect_master.__name__, "inspect_master")
        for source in (READER_SOURCE + "\n", "raise RuntimeError('private-path')", READER_SOURCE.replace("0.97", "0.10", 1)):
            with self.subTest(length=len(source)), self.assertRaises(ValueError):
                worker._load_reader(source)

    def test_binding_bytes_are_checked_without_cross_serializer_assumptions(self):
        changed = packet()
        changed["binding_json"] += " "
        with mock.patch.object(worker, "_load_reader") as loader:
            with self.assertRaises(ValueError):
                worker.run_packet(changed, ["-", "/output", "/ffmpeg", "/ffprobe"])
            loader.assert_not_called()

    def test_packet_shape_does_not_accept_code_argv_roots_or_result_overrides(self):
        for key in ("argv", "python", "output_root", "limits", "technical_content_readback", "host_job"):
            changed = packet(); changed[key] = "private injection"
            with self.subTest(key=key), mock.patch.object(worker, "_load_reader") as loader:
                with self.assertRaises(ValueError):
                    worker.run_packet(changed, ["-", "/output", "/ffmpeg", "/ffprobe"])
                loader.assert_not_called()

    def test_invalid_clock_and_geometry_precede_tool_and_media_readback(self):
        for key, value in (("frames", True), ("frames", 0), ("width", 4097), ("fps_num", 7), ("frames", 451)):
            binding = json.loads(packet()["binding_json"]); binding["clock"][key] = value
            with self.subTest(key=key,value=value), mock.patch.object(worker, "_load_reader", return_value=self.reader), \
                 mock.patch.object(self.reader, "_checked_file") as checked, \
                 mock.patch.object(self.reader, "inspect_master") as inspector:
                with self.assertRaises((ValueError, ZeroDivisionError)):
                    worker.run_packet(packet(binding), ["-", "/unused-private-output", "/ffmpeg", "/ffprobe"])
                checked.assert_not_called(); inspector.assert_not_called()

    def test_generated_basename_guard_rejects_absolute_traversal_and_control_forms(self):
        for name in ("../private.wav", "/private.wav", "C:\\private.wav", "file:///private.wav",
                     "test\x00.wav", "test\n.wav", "UPPER.wav", "a" * 241 + ".wav"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                worker._safe_name(name, ".wav")

    def test_adapter_calls_the_single_inspector_once_with_original_inputs_and_no_limits(self):
        source = packet()
        before = copy.deepcopy(source)
        report = synthetic_report()
        original_report = copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as output_root, \
             mock.patch.object(worker, "_load_reader", return_value=self.reader), \
             mock.patch.object(self.reader, "_checked_file", return_value=("pin", (1,2,3,4))) as checked, \
             mock.patch.object(self.reader, "_unchanged") as unchanged, \
             mock.patch.object(self.reader, "inspect_master", return_value=report) as inspector:
            result = worker.run_packet(source, ["-", output_root, "/sealed-ffmpeg", "/sealed-ffprobe"])
            inspector.assert_called_once_with(Path(output_root)/source["reference_name"],
                Path(output_root)/source["master_name"], reference_sha256="a" * 64,
                master_sha256="b" * 64, clock=self.reader.MediaClock(60,64,64,30,1),
                ffmpeg=Path("/sealed-ffmpeg"), ffprobe=Path("/sealed-ffprobe"))
            self.assertEqual(checked.call_count, 3)
            self.assertEqual(unchanged.call_count, 3)
            self.assertEqual(result["binding_sha256"], source["binding_sha256"])
            self.assertFalse(result["report"]["complete_reader_report_retained"])
            self.assertEqual(result["report"]["content"]["compared_short_energy_windows"], 5998)
            self.assertLess(len(json.dumps(result).encode()), worker.MAX_RESULT_BYTES)
        self.assertEqual(source, before)
        self.assertEqual(report, original_report)

    def test_wrong_executable_digest_fails_before_inspector(self):
        with tempfile.TemporaryDirectory() as output_root, \
             mock.patch.object(worker, "_load_reader", return_value=self.reader), \
             mock.patch.object(self.reader, "_checked_file", side_effect=self.reader.ReadbackError("source_or_master_digest_mismatch")), \
             mock.patch.object(self.reader, "inspect_master") as inspector:
            with self.assertRaises(self.reader.ReadbackError):
                worker.run_packet(packet(), ["-", output_root, "/sealed-ffmpeg", "/sealed-ffprobe"])
            inspector.assert_not_called()

    def test_source_master_rejection_and_tool_drift_do_not_become_pass(self):
        for failure in ("media_changed_during_readback", "source_or_master_digest_mismatch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as output_root, \
                 mock.patch.object(worker, "_load_reader", return_value=self.reader), \
                 mock.patch.object(self.reader, "_checked_file", return_value=("pin", (1,2,3,4))), \
                 mock.patch.object(self.reader, "inspect_master", side_effect=self.reader.ReadbackError(failure)) as inspector:
                with self.assertRaises(self.reader.ReadbackError):
                    worker.run_packet(packet(), ["-", output_root, "/sealed-ffmpeg", "/sealed-ffprobe"])
                self.assertEqual(inspector.call_count, 1)
        with tempfile.TemporaryDirectory() as output_root, \
             mock.patch.object(worker, "_load_reader", return_value=self.reader), \
             mock.patch.object(self.reader, "_checked_file", return_value=("pin", (1,2,3,4))), \
             mock.patch.object(self.reader, "inspect_master", return_value=synthetic_report()), \
             mock.patch.object(self.reader, "_unchanged", side_effect=self.reader.ReadbackError("media_changed_during_readback")):
            with self.assertRaises(self.reader.ReadbackError):
                worker.run_packet(packet(), ["-", output_root, "/sealed-ffmpeg", "/sealed-ffprobe"])

    def test_summary_hash_covers_every_window_without_claiming_bytes_retained(self):
        report = synthetic_report()
        expected_bytes = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        summary = worker._summary(report)
        self.assertEqual(summary["complete_reader_report_sha256"], hashlib.sha256(expected_bytes).hexdigest())
        self.assertEqual(summary["complete_reader_report_bytes"], len(expected_bytes))
        self.assertFalse(summary["complete_reader_report_retained"])
        report["content"]["short_energy_windows"][-1] = {"fixture":"changed last short window"}
        self.assertNotEqual(worker._summary(report)["complete_reader_report_sha256"], summary["complete_reader_report_sha256"])
        self.assertNotIn("windows", summary["content"])
        self.assertNotIn("short_energy_windows", summary["content"])

    def test_complete_report_budget_rejects_instead_of_truncating_success(self):
        with mock.patch.object(worker, "MAX_COMPLETE_REPORT_BYTES", 128):
            with self.assertRaises(ValueError):
                worker._summary(synthetic_report())

    def test_scope_escalation_is_rejected_even_in_a_synthetic_pass(self):
        for key in ("listening_approval", "rights_approval", "editable_source_reopen_verified", "independent_loudness_verified"):
            changed = synthetic_report(); changed[key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker._summary(changed)

    def test_public_adapter_rejection_never_leaks_paths_or_decoder_exception_text(self):
        encoded = base64.b64encode(json.dumps(packet()).encode()).decode()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(worker, "run_packet",
                side_effect=OSError("/private/owner/source.wav decoder stderr")):
            self.assertEqual(worker.main(encoded), 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value, {"schema":worker.SCHEMA, "status":"REJECTED", "diagnostic":worker.DIAGNOSTIC})
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("decoder stderr", output.getvalue())

    def test_real_stdin_program_rejects_modified_reader_before_executing_its_source(self):
        # One isolated Python process tests the real stdin-program boundary;
        # no Host, ffmpeg, ffprobe, native producer or comparator is invoked.
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "unexpected-source-execution.txt"
            changed = packet()
            changed["reader_source"] = f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\n"
            encoded = base64.b64encode(json.dumps(changed).encode()).decode()
            program = (CRATE / "src/worker.py").read_text() + f'\nraise SystemExit(main("{encoded}"))\n'
            result = subprocess.run([sys.executable,"-I","-B","-",directory,
                str(Path(directory)/"uninvoked-ffmpeg"),str(Path(directory)/"uninvoked-ffprobe")],
                input=program.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=5,check=False)
            self.assertEqual(result.returncode,2)
            self.assertFalse(marker.exists())
            self.assertEqual(result.stderr,b"")
            self.assertEqual(json.loads(result.stdout)["diagnostic"],worker.DIAGNOSTIC)


if __name__ == "__main__":
    unittest.main()
