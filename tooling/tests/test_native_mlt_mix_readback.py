"""Negative regressions for full native master PCM and timestamp readback.

The generated signals exercise the reader only. They are not native render,
speech, licensed-media, listening or editable-project acceptance evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import native_mlt_mix_readback as reader
from native_mlt_mix_readback import (
    ContentLimits, MediaClock, ReadbackError, bounded_decoder, compare_pcm,
    read_reference_wav, validate_clock,
)

SAMPLES = 52_800
READER = Path(reader.__file__).resolve()
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def fixture() -> tuple[bytes, dict[str, bytes]]:
    buses = {"voice": bytearray(), "music": bytearray(), "sfx": bytearray()}
    mixed = bytearray()
    for index in range(SAMPLES):
        values = []
        for channel in range(2):
            phase = 2 * math.pi * index / 48_000
            music = round(4200 * math.sin(phase * (317 + 142 * channel)
                                         + 0.000000025 * index * index))
            voice = round(6800 * math.sin(phase * (863 + 190 * channel))) \
                if 9601 <= index < 10801 or 24000 <= index < 33600 else 0
            sfx = round(7200 * math.sin(phase * (1361 + 302 * channel)
                                      + 0.00000009 * index * index)) \
                if 50_400 <= index < SAMPLES else 0
            for name, value in (("voice", voice), ("music", music), ("sfx", sfx)):
                buses[name].extend(struct.pack("<h", value))
            values.append(music + voice + sfx)
        mixed.extend(struct.pack("<hh", *values))
    return bytes(mixed), {name: bytes(data) for name, data in buses.items()}


def without_bus(reference: bytes, bus: bytes) -> bytes:
    original = struct.unpack("<" + "h" * (len(reference) // 2), reference)
    removed = struct.unpack("<" + "h" * (len(bus) // 2), bus)
    return struct.pack("<" + "h" * len(original),
                       *(a - b for a, b in zip(original, removed, strict=True)))


def valid_probe() -> dict:
    return {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1280,
             "height": 720, "r_frame_rate": "30/1", "time_base": "1/15360",
             "start_pts": 0, "duration_ts": 16_896, "nb_read_frames": "33"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "profile": "LC", "sample_rate": "48000",
             "channels": 2, "time_base": "1/48000", "start_pts": 0,
             "duration_ts": SAMPLES},
        ],
        "frames": [
            {"media_type": "video", "stream_index": 0, "pts": index * 512,
             "best_effort_timestamp": index * 512}
            for index in range(33)
        ] + [
            {"media_type": "audio", "stream_index": 1, "pts": index * 1024,
             "nb_samples": 1024}
            for index in range(52)
        ],
        "packets": [
            {"stream_index": 0, "pts": index * 512, "dts": index * 512, "duration": 512}
            for index in range(33)
        ] + [{"stream_index": 1, "pts": -1024, "dts": -1024, "duration": 1024,
              "side_data_list": [{"side_data_type": "Skip Samples", "skip_samples": 1024,
                                  "discard_padding": 0}]}] + [
            {"stream_index": 1, "pts": index * 1024, "dts": index * 1024,
             "duration": min(1024, SAMPLES - index * 1024)}
            for index in range(52)
        ],
    }


class FullContentReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference, cls.buses = fixture()

    def test_all_samples_and_tail_are_compared_without_shifting(self):
        result = compare_pcm(self.reference, self.reference)
        self.assertTrue(result["all_logical_samples_compared"])
        self.assertEqual(result["logical_interval"], [0, SAMPLES])
        self.assertEqual(result["decoded_access_unit_suffix_samples"], 0)
        self.assertTrue(all(item["best_lag_samples"] == 0 for item in result["alignment"]))
        for channel in range(2):
            windows = [item for item in result["windows"] if item["channel"] == channel]
            self.assertEqual(windows[0]["start_sample"], 0)
            self.assertEqual(windows[-1]["end_exclusive"], SAMPLES)
            self.assertTrue(all(a["end_exclusive"] >= b["start_sample"]
                                for a, b in zip(windows, windows[1:])))

    def test_codec_sized_suffix_is_disclosed_without_claiming_extra_content(self):
        result = compare_pcm(self.reference, self.reference + b"\0" * (448 * 4),
                             declared_suffix_samples=448)
        self.assertEqual(result["decoded_sample_frames"], 53_248)
        self.assertEqual(result["decoded_access_unit_suffix_samples"], 448)
        self.assertEqual(result["logical_interval"], [0, SAMPLES])

    def test_central_tone_gate_misses_a_tail_that_the_full_reader_rejects(self):
        spec = importlib.util.spec_from_file_location(
            "native_sequence_tail_regression", ROOT / "tooling/native_mlt_sequence_e2e.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tones = bytearray()
        for sample in range(SAMPLES):
            tones.extend(struct.pack(
                "<hh", round(5000 * math.sin(2 * math.pi * 400 * sample / 48_000)),
                round(5000 * math.sin(2 * math.pi * 600 * sample / 48_000)),
            ))
        corrupted = bytes(tones[:48_000 * 4]) + bytes(4800 * 4)
        self.assertEqual(module.inspect_decoded_ci_stereo(corrupted)
                         ["decoded_master_pcm_frames"], SAMPLES)
        with self.assertRaises(ReadbackError):
            compare_pcm(bytes(tones), corrupted)

    def test_shifts_from_one_sample_to_one_video_frame_fail(self):
        for shift in (1, -1, 512, -512, 1024, 1600):
            with self.subTest(shift=shift):
                size = abs(shift) * 4
                shifted = (bytes(size) + self.reference[:-size]) if shift > 0 else \
                    (self.reference[size:] + bytes(size))
                with self.assertRaises(ReadbackError):
                    compare_pcm(self.reference, shifted)

    def test_each_missing_bus_fails(self):
        for name, bus in self.buses.items():
            with self.subTest(bus=name), self.assertRaises(ReadbackError):
                compare_pcm(self.reference, without_bus(self.reference, bus))

    def test_cut_and_last_100_milliseconds_cannot_disappear(self):
        for start in (48_000, 51_200):
            with self.subTest(start=start), self.assertRaises(ReadbackError):
                compare_pcm(self.reference, self.reference[:start * 4]
                            + bytes((SAMPLES - start) * 4))

    def test_channel_swap_and_duplication_fail(self):
        swapped, duplicated = bytearray(), bytearray()
        for left, right in struct.iter_unpack("<hh", self.reference):
            swapped.extend(struct.pack("<hh", right, left))
            duplicated.extend(struct.pack("<hh", left, left))
        for data in (swapped, duplicated):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, bytes(data))

    def test_wrong_gain_fails(self):
        wrong = b"".join(struct.pack("<h", max(-32768, min(32767, value[0] * 2)))
                         for value in struct.iter_unpack("<h", self.reference))
        with self.assertRaises(ReadbackError):
            compare_pcm(self.reference, wrong)

    def test_missing_or_partial_logical_sample_and_excess_padding_fail(self):
        for data in (self.reference[:-4], self.reference[:-1],
                     self.reference + bytes(1025 * 4)):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, data)

    def test_silence_cannot_prove_alignment(self):
        with self.assertRaisesRegex(ReadbackError, "timing_signature"):
            compare_pcm(bytes(SAMPLES * 4), bytes(SAMPLES * 4))

    def test_last_100_samples_cannot_disappear_inside_a_large_window(self):
        corrupted = self.reference[:-100 * 4] + bytes(100 * 4)
        with self.assertRaisesRegex(ReadbackError, "short_window_audio_gain"):
            compare_pcm(self.reference, corrupted)

    def test_suffix_requires_the_validated_packet_declaration(self):
        for size in (1, 448, 1024):
            with self.subTest(size=size), self.assertRaisesRegex(ReadbackError, "undeclared"):
                compare_pcm(self.reference, self.reference + bytes(size * 4))

    def test_periodic_reference_cannot_claim_a_unique_alignment(self):
        tones = periodic_pcm()
        for shifted in (tones, bytes(96 * 4) + tones[:-96 * 4]):
            with self.subTest(delayed=shifted != tones), \
                    self.assertRaises(ReadbackError):
                compare_pcm(tones, shifted)

    def test_alignment_report_exposes_its_finite_search_scope(self):
        result = compare_pcm(self.reference, self.reference)
        for channel in result["alignment"]:
            self.assertEqual(channel["search_interval_samples"], [-1600, 1600])
            self.assertFalse(channel["absolute_delay_absence_proven"])
            self.assertGreaterEqual(channel["reference_zero_lag_margin"],
                                    ContentLimits().minimum_lag_margin)

    def test_fractional_project_sample_clock_fails(self):
        with self.assertRaisesRegex(ReadbackError, "fractional_audio_sample_clock"):
            _ = MediaClock(1, 1280, 720, 59, 1).samples

    def test_invalid_limits_cannot_disable_the_reader(self):
        for limits in (ContentLimits(minimum_correlation=float("nan")),
                       ContentLimits(block_samples=0),
                       ContentLimits(maximum_relative_error=100)):
            with self.assertRaises(ReadbackError):
                compare_pcm(self.reference, self.reference, limits)


class TimestampAndDecoderTests(unittest.TestCase):
    def test_exact_frame_clock_exposes_aac_suffix(self):
        result = validate_clock(valid_probe(), MediaClock(33, 1280, 720))
        self.assertEqual(result["decoded_audio_samples_from_frames"], 53_248)
        self.assertEqual(result["audio_duration_samples"], SAMPLES)
        self.assertTrue(result["exact_contiguous_frame_timestamps"])

    def test_nonzero_or_discontinuous_pts_fail_with_unchanged_pcm(self):
        for kind in ("audio_start", "video_start", "audio_gap", "video_gap"):
            data = valid_probe()
            if kind == "audio_start":
                data["streams"][1]["start_pts"] = 1
            elif kind == "video_start":
                data["streams"][0]["start_pts"] = 512
            elif kind == "audio_gap":
                data["frames"][34]["pts"] += 1
            else:
                data["frames"][15]["best_effort_timestamp"] += 512
            with self.subTest(kind=kind), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_no_channel_or_rate_normalization_hides_a_wrong_stream(self):
        for field, value in (("sample_rate", "44100"), ("channels", 1),
                             ("codec_name", "mp3"), ("duration_ts", SAMPLES - 1)):
            data = valid_probe()
            data["streams"][1][field] = value
            with self.subTest(field=field), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_metadata_frame_total_cannot_hide_a_missing_decoded_frame(self):
        data = valid_probe()
        del data["frames"][32]
        with self.assertRaises(ReadbackError):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_stream_durations_must_match_the_exact_logical_clock(self):
        for stream, delta in ((0, 15360), (0, -512), (1, 1), (1, 432), (1, 1024)):
            data = valid_probe()
            data["streams"][stream]["duration_ts"] += delta
            with self.subTest(stream=stream, delta=delta), \
                    self.assertRaisesRegex(ReadbackError, "duration_clock_mismatch"):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_packets_are_required_in_addition_to_decoded_frames(self):
        data = valid_probe()
        del data["packets"]
        with self.assertRaisesRegex(ReadbackError, "missing_media_packets"):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_priming_is_explicit_and_consistent_with_the_first_packet_pts(self):
        for skip in (None, 1023, 1025):
            data = valid_probe()
            packet = data["packets"][33]
            if skip is None:
                del packet["side_data_list"]
            else:
                packet["side_data_list"][0]["skip_samples"] = skip
            with self.subTest(skip=skip), self.assertRaisesRegex(ReadbackError, "priming"):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_audio_packet_end_cannot_hide_extra_presented_samples(self):
        data = valid_probe()
        data["packets"][-1]["duration"] += 1
        with self.assertRaisesRegex(ReadbackError, "presentation_end_mismatch"):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_explicit_final_discard_padding_is_coherent_with_exact_presentation(self):
        for duration in (576, 1024):
            for decoded_last in (576, 1024):
                data = valid_probe()
                packet = data["packets"][-1]
                packet["duration"] = duration
                packet["side_data_list"] = [{"side_data_type": "Skip Samples", "skip_samples": 0,
                                             "discard_padding": 448}]
                data["frames"][-1]["nb_samples"] = decoded_last
                with self.subTest(duration=duration, decoded_last=decoded_last):
                    result = validate_clock(data, MediaClock(33, 1280, 720))
                    self.assertEqual(result["discard_padding_samples"], 448)
                    self.assertEqual(result["audio_packet_presentation_end_samples"], SAMPLES)
                    self.assertEqual(result["declared_decoder_suffix_budget_samples"], 448)

    def test_arbitrary_short_audio_frames_cannot_hide_behind_a_suffix_budget(self):
        for shortened in (10, 51):
            data = valid_probe()
            frames = data["frames"][33:]
            frames[shortened]["nb_samples"] -= 100
            for frame in frames[shortened + 1:]:
                frame["pts"] -= 100
            with self.subTest(shortened=shortened), \
                    self.assertRaisesRegex(ReadbackError, "frame_and_packet_samples_disagree"):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_discard_metadata_must_match_the_nominal_access_unit_end(self):
        data = valid_probe()
        data["packets"][-1]["side_data_list"] = [{"side_data_type": "Skip Samples",
                                                  "skip_samples": 0, "discard_padding": 447}]
        with self.assertRaises(ReadbackError):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_skip_discard_and_packet_timestamps_cannot_move_interior_content(self):
        for corruption in ("skip", "discard", "gap", "dts", "short", "duplicate_skip"):
            data = valid_probe()
            packet = data["packets"][35]
            if corruption in ("skip", "discard"):
                packet["side_data_list"] = [{"side_data_type": "Skip Samples",
                                             "skip_samples": int(corruption == "skip"),
                                             "discard_padding": int(corruption == "discard")}]
            elif corruption == "gap":
                packet["pts"] += 1
                packet["dts"] += 1
            elif corruption == "dts":
                packet["dts"] += 1
            elif corruption == "short":
                packet["duration"] -= 1
            else:
                first = data["packets"][33]
                first["side_data_list"] *= 2
            with self.subTest(corruption=corruption), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_video_packets_and_frames_cannot_disagree(self):
        for corruption in ("missing_packet", "packet_pts", "packet_duration", "frame_stream"):
            data = valid_probe()
            if corruption == "missing_packet":
                del data["packets"][0]
            elif corruption == "packet_pts":
                data["packets"][0]["pts"] += 1
            elif corruption == "packet_duration":
                data["packets"][0]["duration"] += 1
            else:
                data["frames"][0]["stream_index"] = 1
            with self.subTest(corruption=corruption), self.assertRaises(ReadbackError):
                validate_clock(data, MediaClock(33, 1280, 720))

    def test_aac_profile_cannot_change_access_unit_assumptions(self):
        data = valid_probe()
        data["streams"][1]["profile"] = "HE-AAC"
        with self.assertRaisesRegex(ReadbackError, "audio_profile_mismatch"):
            validate_clock(data, MediaClock(33, 1280, 720))

    def test_decoder_stderr_is_rejected_even_with_exit_zero(self):
        with self.assertRaisesRegex(ReadbackError, "media_errors"):
            bounded_decoder([sys.executable, "-c",
                             "import sys;sys.stderr.write('corrupt media')"], 1024)

    def test_decoder_output_is_bounded_while_reading(self):
        with self.assertRaisesRegex(ReadbackError, "output_limit"):
            bounded_decoder([sys.executable, "-c", "print('x'*100000)"], 1024)

    def test_decoder_timeout_kills_and_reaps(self):
        with self.assertRaisesRegex(ReadbackError, "timeout"):
            bounded_decoder([sys.executable, "-c", "import time;time.sleep(30)"],
                            1024, timeout=0.1)

    def test_reference_wav_is_read_physically_without_resampling(self):
        with tempfile.TemporaryDirectory(prefix="native-mix-readback-") as directory:
            path = Path(directory) / "source.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((2, 2, 48_000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(2048 * 4))
            self.assertEqual(len(read_reference_wav(path, 2048)), 2048 * 4)
            path.write_bytes(path.read_bytes()[:-4])
            with self.assertRaisesRegex(ReadbackError, "physically_truncated"):
                read_reference_wav(path, 2048)


def pid_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists
                return False
            raise OSError("could not observe owned test descendant")
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            if stat.read_text().split(")", 1)[1].strip().split()[0] == "Z":
                return False  # A terminated orphan may await the OS reaper.
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class BoundedDecoderProcessTests(unittest.TestCase):
    def test_small_and_exact_limit_output(self):
        self.assertEqual(reader.bounded_decoder(
            [sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(b'x'*1024)"],
            1024), b"x" * 1024)

    def test_partial_pipe_write_overflow_is_detected_without_waiting_for_64k(self):
        start = time.monotonic()
        with self.assertRaisesRegex(reader.ReadbackError, "output_limit"):
            reader.bounded_decoder([
                sys.executable, "-B", "-c",
                "import os,time;os.write(1,b'x'*1025);time.sleep(30)"
            ], 1024, timeout=3)
        self.assertLess(time.monotonic() - start, 2)

    def test_both_pipes_are_drained_and_bounded(self):
        with self.assertRaisesRegex(reader.ReadbackError, "output_limit"):
            reader.bounded_decoder([
                sys.executable, "-B", "-c",
                "import os;os.write(1,b'x'*65536);os.write(2,b'e'*65537)"
            ], 131072, timeout=3)


    def test_nonzero_exit_fails(self):
        with self.assertRaisesRegex(reader.ReadbackError, "decoder_failed"):
            reader.bounded_decoder([sys.executable, "-B", "-c", "raise SystemExit(7)"], 1024)

    def test_invalid_deadline_rejected_before_any_spawn(self):
        for timeout in (0, -1, math.nan, math.inf, -math.inf, True):
            with self.subTest(timeout=timeout), mock.patch.object(reader.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(reader.ReadbackError, "timeout_budget"):
                    reader.bounded_decoder([sys.executable], 1024, timeout=timeout)
                spawn.assert_not_called()

    def _descendant_under_external_deadline(self, close_pipes: bool) -> dict:
        with tempfile.TemporaryDirectory(prefix="reader-process-test-") as directory:
            root = Path(directory)
            pid_file = root / "descendant.pid"
            leaf = (
                "import os,sys,time\n"
                "from pathlib import Path\n"
                "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                + ("os.close(1);os.close(2)\n" if close_pipes else "")
                + "time.sleep(30)\n"
            )
            direct = (
                "import subprocess,sys,time\n"
                "from pathlib import Path\n"
                "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]])\n"
                "while not Path(sys.argv[2]).exists(): time.sleep(0.005)\n"
            )
            harness = (
                "import importlib.util,json,sys,time\n"
                "spec=importlib.util.spec_from_file_location('reader_case',sys.argv[1])\n"
                "r=importlib.util.module_from_spec(spec);sys.modules[spec.name]=r;spec.loader.exec_module(r)\n"
                "start=time.monotonic()\n"
                "try:\n"
                " r.bounded_decoder([sys.executable,'-B','-c',sys.argv[2],sys.argv[3],sys.argv[4]],1024,timeout=1)\n"
                " result={'status':'success'}\n"
                "except r.ReadbackError as e: result={'status':'rejected','diagnostic':str(e)}\n"
                "result['elapsed_s']=time.monotonic()-start;print(json.dumps(result))\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", harness, str(READER), direct, leaf, str(pid_file)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **({"start_new_session": True} if os.name != "nt" else {}),
            )
            try:
                try:
                    stdout, stderr = process.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    self.fail("bounded_decoder exceeded the independent six-second deadline")
                self.assertEqual(process.returncode, 0, stderr.decode())
                result = json.loads(stdout)
                self.assertTrue(pid_file.exists(), "descendant never started; negative was not exercised")
                pid = int(pid_file.read_text())
                limit = time.monotonic() + 1
                while pid_running(pid) and time.monotonic() < limit:
                    time.sleep(0.01)
                self.assertFalse(pid_running(pid), "descendant survived reader cleanup")
                self.assertLess(result["elapsed_s"], 3)
                return result
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
                if pid_file.exists():
                    pid = int(pid_file.read_text())
                    if pid_running(pid):
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

    def test_exited_parent_with_descendant_holding_pipes_obeys_external_deadline(self):
        result = self._descendant_under_external_deadline(close_pipes=False)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["diagnostic"], "decoder_timeout")

    def test_successful_parent_cannot_leave_a_descendant_without_open_pipes(self):
        result = self._descendant_under_external_deadline(close_pipes=True)
        self.assertEqual(result["status"], "success")

    def test_closed_pipes_do_not_end_deadline_while_leader_is_running(self):
        with self.assertRaisesRegex(reader.ReadbackError, "decoder_timeout"):
            reader.bounded_decoder([
                sys.executable, "-B", "-c", "import os,time;os.close(1);os.close(2);time.sleep(30)"
            ], 1024, timeout=0.2)

    @unittest.skipUnless(os.name == "nt", "requires real Windows Job Objects")
    def test_job_assignment_failure_never_releases_decoder_launch_gate(self):
        with tempfile.TemporaryDirectory(prefix="reader-job-gate-") as directory:
            marker = Path(directory) / "decoder-started"
            with mock.patch.object(reader._WindowsDecoderJob, "assign",
                                   side_effect=reader.ReadbackError("decoder_job_assignment_failed")):
                with self.assertRaisesRegex(reader.ReadbackError, "job_assignment_failed"):
                    reader.bounded_decoder([
                        sys.executable, "-B", "-c",
                        "import sys;from pathlib import Path;Path(sys.argv[1]).write_text('started')",
                        str(marker),
                    ], 1024)
            self.assertFalse(marker.exists())


def periodic_pcm() -> bytes:
    return b"".join(struct.pack(
        "<hh", round(5000 * math.sin(2 * math.pi * 1000 * sample / 48000)),
        round(5000 * math.sin(2 * math.pi * 1500 * sample / 48000)),
    ) for sample in range(SAMPLES))


@unittest.skipUnless(FFMPEG and FFPROBE,
                     "requires ffmpeg and ffprobe for real AAC calibration")
class RealAacCalibrationTests(unittest.TestCase):
    """Synthetic FFmpeg codec fixtures; not Motionwright native-production proof."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="native-reader-real-aac-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.reference, _ = fixture()
        periodic = periodic_pcm()
        extension = b"".join(struct.pack(
            "<hh", round(12000 * math.sin(2 * math.pi * 1800 * sample / 48000)),
            round(12000 * math.sin(2 * math.pi * 2400 * sample / 48000)),
        ) for sample in range(448))
        signals = {
            "reference": cls.reference,
            "audible-extension": cls.reference + extension,
            "muted-last-100": cls.reference[:-100 * 4] + bytes(100 * 4),
            "periodic": periodic,
            "periodic-delayed-96": bytes(96 * 4) + periodic[:-96 * 4],
        }
        for name, pcm in signals.items():
            with wave.open(str(cls.directory / f"{name}.wav"), "wb") as output:
                output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                output.writeframes(pcm)
        video = cls.directory / "33-frame-black-video.mp4"
        cls._encode(["-f", "lavfi", "-i", "color=c=black:s=64x64:r=30", "-frames:v", "33",
                     "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)])
        for name in signals:
            cls._encode(["-i", str(video), "-i", str(cls.directory / f"{name}.wav"),
                         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                         "-b:a", "256k", str(cls.directory / f"{name}.mp4")])

    @classmethod
    def _encode(cls, arguments):
        result = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                                 *arguments], capture_output=True, timeout=45, check=False)
        if result.returncode or result.stderr.strip():
            raise AssertionError("real AAC calibration fixture could not be encoded")

    def _inspect(self, master_name, reference_name="reference"):
        reference = self.directory / f"{reference_name}.wav"
        master = self.directory / f"{master_name}.mp4"
        return reader.inspect_master(
            reference, master, reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            master_sha256=hashlib.sha256(master.read_bytes()).hexdigest(),
            clock=MediaClock(33, 64, 64), ffmpeg=Path(FFMPEG), ffprobe=Path(FFPROBE),
        )

    def test_real_aac_positive_retains_the_measured_comparison_limits(self):
        result = self._inspect("reference")
        self.assertEqual(result["technical_content_readback"], "PASS")
        self.assertEqual(result["clock"]["audio_duration_samples"], SAMPLES)
        self.assertEqual(result["clock"]["audio_packet_presentation_end_samples"], SAMPLES)
        self.assertTrue(result["clock"]["packet_priming_and_presentation_checked"])
        self.assertEqual(result["content"]["limits"]["minimum_correlation"], 0.97)
        self.assertEqual(result["content"]["limits"]["maximum_relative_error"], 0.25)
        self.assertFalse(result["listening_approval"])
        for channel in result["content"]["alignment"]:
            self.assertEqual(channel["best_lag_samples"], 0)
            self.assertFalse(channel["absolute_delay_absence_proven"])

    def test_real_audible_suffix_is_not_reclassified_as_codec_padding(self):
        with self.assertRaisesRegex(ReadbackError, "audio_duration_clock_mismatch"):
            self._inspect("audible-extension")

    def test_real_aac_cannot_hide_a_loss_of_the_last_100_samples(self):
        with self.assertRaises(ReadbackError):
            self._inspect("muted-last-100")

    def test_real_periodic_audio_cannot_prove_zero_delay_or_hide_96_sample_shift(self):
        for master in ("periodic", "periodic-delayed-96"):
            with self.subTest(master=master), self.assertRaises(ReadbackError):
                self._inspect(master, "periodic")


if __name__ == "__main__":
    unittest.main()
