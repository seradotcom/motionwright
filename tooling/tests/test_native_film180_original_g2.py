"""Source-only original Film180 G2 runner safeguards; no native CI effects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_film180_original_g2 as runner


class Film180G2SourceTests(unittest.TestCase):
    def test_simulated_windows_native_effect_denial_also_runs_on_linux_ci(self):
        # Do not skip this negative when executed in selected Linux CI:
        # test the Windows platform denial with a harmless patched platform.
        with mock.patch.object(runner.sys, "platform", "win32"):
            with self.assertRaisesRegex(runner.Film180G2Rejected, "approved_Linux_GHA"):
                runner.checked_plan()

    def test_absent_owner_G2_authorization_fails_closed_on_every_host(self):
        with mock.patch.dict(
            os.environ, {"MOTIONWRIGHT_APPROVED_FILM180_G2": "0"},
        ):
            with self.assertRaisesRegex(runner.Film180G2Rejected, "approved_Linux_GHA"):
                runner.checked_plan()

    def test_closed_native_tool_and_provider_protocol_without_alternate_runtime(self):
        with mock.patch.object(runner, "pin_version", return_value="1.0.0"):
            with tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "driver"
                source.write_bytes(b"firstparty-controlled-test-binary")
                manifest = runner.make_manifest(
                    "motion-canvas", source,
                    [{"root": "motion-node-tool", "name": "motion-node",
                      "sha256": runner.digest(source), "mounts": ["project"]}],
                    [{"root": "project", "read_only": False}],
                    app_process="node", cooperative_cancellation=True,
                    artifacts=True, progress=True,
                )
        self.assertEqual(manifest["protocol"], 7)
        self.assertEqual(manifest["id"], "motion-canvas")
        self.assertFalse(manifest["network"])
        self.assertEqual(manifest["publisher"], "semwright")
        self.assertEqual(manifest["sha256"],
                         hashlib.sha256(b"firstparty-controlled-test-binary").hexdigest())
        self.assertTrue(manifest["interfaces"]["host_tools"])
        self.assertTrue(manifest["interfaces"]["cooperative_cancellation"])

    def test_private_json_is_create_new_and_does_not_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "original.json"
            runner.write_private_json(path, {"first": "original"})
            with self.assertRaisesRegex(runner.Film180G2Rejected, "path_not_fresh"):
                runner.write_private_json(path, {"first": "replaced"})
            self.assertEqual(json.loads(path.read_text()), {"first": "original"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600 if os.name != "nt" else
                             os.stat(path).st_mode & 0o777)

    def test_private_json_rejects_non_parent_or_relative_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(runner.Film180G2Rejected, "path_not_fresh"):
                runner.write_private_json(Path("relative.json"), {"value": 1})
            with self.assertRaisesRegex(runner.Film180G2Rejected, "path_not_fresh"):
                runner.write_private_json(Path(temp) / "absent" / "receipt.json", {"v": 2})

    def test_original_source_subprocess_failure_is_not_retried(self):
        with mock.patch.object(runner, "run_sanitized_json", side_effect=ValueError("failed")) as call:
            with self.assertRaises(ValueError):
                runner.source_command(["render", "/s/test.sqlite3"], {})
            self.assertEqual(call.call_count, 1)

    def test_original_sanitized_json_passthrough_never_changes_original_arguments(self):
        with mock.patch.object(runner, "run_sanitized_json", return_value={"frame_count": 180}) as call:
            result = runner.source_command(["render", "/owner/original.sqlite3"], {"CI": "true"})
            self.assertEqual(result["frame_count"], 180)
            self.assertEqual(call.call_count, 1)
            args = call.call_args.args[0]
            self.assertEqual(args[1:], ["render", "/owner/original.sqlite3"])

    @staticmethod
    def native_manifest(tmp: Path, *, n: int = 180) -> tuple[Path, dict]:
        root = tmp / "output"
        root.mkdir()
        original = root / "original-film180-manifest"
        frames = original / "frames"
        frames.mkdir(parents=True)
        rows = []
        for index in range(n):
            data = b"\x89PNG\r\n\x1a\n" + index.to_bytes(4, "little") + b"original-shape"
            file = frames / f"{index:06}.png"
            file.write_bytes(data)
            rows.append({
                "file": f"frames/{index:06}.png",
                "index": index, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        manifest = {
            "frames": rows,
            "plan": {"frame_count": n, "first_frame": 0,
                     "end_frame_exclusive": n},
        }
        raw = (json.dumps(manifest, sort_keys=True) + "\n").encode()
        (original / "artifact-manifest.json").write_bytes(raw)
        evidence = {"segments": [{
            "artifact": {
                "directory": "original-film180-manifest",
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            },
        }]}
        return root, evidence

    def test_all_original_180_frame_manifest_hashes_are_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            result = runner.original_png_index(root, manifest)
            self.assertEqual(result["frame_count"], 180)
            self.assertTrue(result["every_original_frame_SHA_matched"])

    def test_a_missing_frame_never_counts_as_180_original_native_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            (root / "original-film180-manifest" / "frames" / "000179.png").unlink()
            with self.assertRaisesRegex(runner.Film180G2Rejected, "digest_or_sequence"):
                runner.original_png_index(root, manifest)

    def test_source_bound_frame_tamper_rejected_without_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            file = root / "original-film180-manifest" / "frames" / "000090.png"
            file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"edited-original")
            with self.assertRaisesRegex(runner.Film180G2Rejected, "digest_or_sequence"):
                runner.original_png_index(root, manifest)

    def test_original_manifest_wrong_frame_count_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp), n=179)
            with self.assertRaisesRegex(runner.Film180G2Rejected, "frame_index_not_contiguous"):
                runner.original_png_index(root, manifest)

    def test_native_artifact_directory_escape_fails_before_media_open(self):
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            manifest["segments"][0]["artifact"]["directory"] = "../foreign"
            with self.assertRaisesRegex(runner.Film180G2Rejected, "directory_not_closed"):
                runner.original_png_index(root, manifest)

    def test_original_native_MLT_master_basename_matches_saved_project_revision(self):
        project = "0f123456-78ab-4cde-80ff-123456789abc"
        for stage in ("original", "edited"):
            request = f"film180-{stage}-av-master-v1"
            token = hashlib.sha256(request.encode()).hexdigest()[:16]
            expected = f"mw-{project.replace('-', '')}-r42-{token}-master.mp4"
            self.assertEqual(runner.original_native_master_name(project, 42, stage), expected)
        self.assertNotEqual(
            runner.original_native_master_name(project, 42, "original"),
            runner.original_native_master_name(project, 43, "original"),
        )
        self.assertNotEqual(
            runner.original_native_master_name(project, 42, "original"),
            runner.original_native_master_name(project, 42, "edited"),
        )

    def test_unknown_original_operation_or_project_cannot_create_MLT_filename(self):
        with self.assertRaisesRegex(runner.Film180G2Rejected, "render_stage"):
            runner.original_native_master_name(
                "0f123456-78ab-4cde-80ff-123456789abc", 42, "third-or-retry"
            )
        with self.assertRaisesRegex(runner.Film180G2Rejected, "identity_invalid"):
            runner.original_native_master_name("foreign", 42, "original")
        with self.assertRaisesRegex(runner.Film180G2Rejected, "identity_invalid"):
            runner.original_native_master_name(
                "0f123456-78ab-4cde-80ff-123456789abc", 0, "original"
            )

    def test_source_PNG_signature_cannot_be_replaced_with_trusted_manifest_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root, evidence = self.native_manifest(Path(temp))
            file = root / "original-film180-manifest" / "frames" / "000027.png"
            bytes_ = b"NOTPNG!?" + bytes(range(20))
            file.write_bytes(bytes_)
            manifest = root / "original-film180-manifest" / "artifact-manifest.json"
            obj = json.loads(manifest.read_text())
            obj["frames"][27]["sha256"] = hashlib.sha256(bytes_).hexdigest()
            obj["frames"][27]["bytes"] = len(bytes_)
            raw = (json.dumps(obj, sort_keys=True) + "\n").encode()
            manifest.write_bytes(raw)
            evidence["segments"][0]["artifact"]["manifest_sha256"] = (
                hashlib.sha256(raw).hexdigest()
            )
            with self.assertRaisesRegex(runner.Film180G2Rejected, "PNG_signature"):
                runner.original_png_index(root, evidence)

    @staticmethod
    def decoded_all_180_manifest(*, frames: int = 180,
                                 timebase: str = "1/30") -> bytes:
        # Bounded synthetic format fixture only; actual pixels must be
        # streamed through the original installed FFmpeg6.1 on the CI master.
        parts = timebase.split("/")
        num, den = map(int, parts)
        step = den // (30 * num) if num and den % (30 * num) == 0 else 1
        lines = [
            "#format: frame checksums",
            "#version: 2",
            "#hash: SHA256",
            "#software: libavformat",
            "#tb 0: " + timebase,
            "#media_type 0: video",
            "#codec_id 0: rawvideo",
            "#dimensions 0: 1920x1080",
            "#sar 0: 1/1",
            "#stream#, dts, pts, duration, size, hash",
        ]
        for frame in range(frames):
            mark = hashlib.sha256(f"source-only-{frame}".encode()).hexdigest()
            lines.append(
                f"0, {frame * step}, {frame * step}, {step}, 6220800, {mark}"
            )
        return ("\n".join(lines) + "\n").encode("ascii")

    def test_complete_original_180_decoded_RGB_sha_manifest_is_source_bound(self):
        result = runner.parse_original_180_framemd5(self.decoded_all_180_manifest())
        self.assertEqual(result["source_frame_count"], 180)
        self.assertEqual(len(result["all_original_frame_sha256"]), 180)
        self.assertEqual(result["decoded_format"], "RGB24")
        self.assertEqual(result["bytes_per_decoded_frame"], 6220800)
        self.assertTrue(result["every_original_decoded_RGB_frame_observed"])
        self.assertFalse(result["original_PNG_pixel_equality_claim"])
        self.assertFalse(result["H264_perceptual_fidelity_claim"])

    def test_video_with_missing_179th_original_RGB_frame_fails_closed(self):
        with self.assertRaisesRegex(runner.Film180G2Rejected, "frames_not_actually_decoded"):
            runner.parse_original_180_framemd5(
                self.decoded_all_180_manifest(frames=179)
            )

    def test_wrong_original_RGB_color_clock_rejected_without_claiming_fidelity(self):
        with self.assertRaisesRegex(runner.Film180G2Rejected, "not_exact_30fps"):
            runner.parse_original_180_framemd5(
                self.decoded_all_180_manifest(timebase="1001/30000")
            )
        alternate = self.decoded_all_180_manifest().replace(
            b"#dimensions 0: 1920x1080", b"#dimensions 0: 960x540"
        )
        with self.assertRaisesRegex(runner.Film180G2Rejected, "format_or_hash"):
            runner.parse_original_180_framemd5(alternate)

    def test_bad_original_frame_PTS_and_invalid_bytes_rejected(self):
        valid = self.decoded_all_180_manifest()
        incorrect_pts = valid.replace(
            b"0, 60, 60, 1, 6220800, ",
            b"0, 60, 59, 1, 6220800, ", 1,
        )
        with self.assertRaisesRegex(runner.Film180G2Rejected, "index_pts_size"):
            runner.parse_original_180_framemd5(incorrect_pts)
        truncated_pixels = valid.replace(
            b"0, 70, 70, 1, 6220800, ",
            b"0, 70, 70, 1, 150000, ", 1,
        )
        with self.assertRaisesRegex(runner.Film180G2Rejected, "index_pts_size"):
            runner.parse_original_180_framemd5(truncated_pixels)

    @staticmethod
    def synthetic_RGB_loss_report(
        *, frame_count: int = 180, wrong_index: bool = False,
        duplicate_metric: bool = False, infinite: bool = False,
    ) -> bytes:
        lines = []
        for frame in range(1, frame_count + 1):
            n = 1 if (wrong_index and frame == 90) else frame
            mse = "0.000" if infinite and frame == 12 else "1.421"
            psnr = "inf" if infinite and frame == 12 else "46.60"
            extra = " psnr_avg:46.60" if duplicate_metric and frame == 90 else ""
            lines.append(
                f"n:{n} mse_avg:{mse} mse_r:1.2 mse_g:1.4 mse_b:1.8"
                f" psnr_avg:{psnr}{extra}"
            )
        return ("\n".join(lines) + "\n").encode("ascii")

    def test_full_180_native_png_against_H264_RGB_loss_stats_are_observational(self):
        report = runner.parse_rgb_psnr_observations(self.synthetic_RGB_loss_report())
        self.assertEqual(report["observed_frame_count"], 180)
        self.assertEqual(report["observed_RGB_mse_mean"], 1.421)
        self.assertEqual(report["observed_RGB_psnr_min_finite"], 46.60)
        self.assertFalse(report["native_PNG_alpha_channel_compared"])
        self.assertFalse(report["H264_lossy_quality_accepted_by_this_observer"])

    def test_infinite_native_RGB_psnr_does_not_generate_invalid_float_json(self):
        report = runner.parse_rgb_psnr_observations(
            self.synthetic_RGB_loss_report(infinite=True),
        )
        self.assertEqual(report["observed_identical_RGB_frame_count"], 1)
        self.assertEqual(report["observed_frame_count"], 180)
        self.assertNotIn("Infinity", json.dumps(report, allow_nan=False))

    def test_psnr_missing_forged_repeated_or_corrupt_frame_does_not_pass(self):
        for label, raw in (
            ("missing", self.synthetic_RGB_loss_report(frame_count=179)),
            ("duplicated", self.synthetic_RGB_loss_report(wrong_index=True)),
            ("metric", self.synthetic_RGB_loss_report(duplicate_metric=True)),
            ("nonnumeric", self.synthetic_RGB_loss_report().replace(
                b"mse_avg:1.421", b"mse_avg:unverified", 1,
            )),
            ("zero", self.synthetic_RGB_loss_report().replace(
                b"n:1 mse_avg:1.421", b"n:1 mse_avg:-1.0", 1,
            )),
        ):
            with self.subTest(label=label):
                with self.assertRaises(runner.Film180G2Rejected):
                    runner.parse_rgb_psnr_observations(raw)

    @staticmethod
    def native_png_IHDR(*, width: int = 1920, height: int = 1080,
                        color_type: int = 6) -> bytes:
        return (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\x0dIHDR"
            + width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + b"\x08"
            + bytes([color_type])
        )

    def test_native_RGBA_source_alpha_is_explicitly_excluded_from_H264_RGB(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "frames"
            directory.mkdir()
            header = self.native_png_IHDR(color_type=6)
            for index in range(180):
                (directory / f"{index:06}.png").write_bytes(header)
            report = runner.original_png_color_types(directory)
            self.assertEqual(report["native_source_PNG_color_type_counts"], {"6": 180})
            self.assertEqual(report["native_RGBA_or_greyscale_alpha_PNG_frames"], 180)
            self.assertFalse(report["native_alpha_preserved_in_H264"])

    def test_native_IHDR_missing_or_wrong_geometry_fails_before_FFmpeg(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "frames"
            directory.mkdir()
            original = self.native_png_IHDR(color_type=2)
            for index in range(180):
                (directory / f"{index:06}.png").write_bytes(original)
            (directory / "000090.png").write_bytes(self.native_png_IHDR(width=500))
            with self.assertRaisesRegex(runner.Film180G2Rejected, "IHDR"):
                runner.original_png_color_types(directory)

    def test_film_original_frames_directory_symlink_is_rejected_before_hashes(self):
        # Real Windows symlink creation can require elevated permissions. Mock
        # only the filesystem's symlink observation so this negative runs on
        # both host platforms without making foreign filesystem references.
        actual = Path.is_symlink
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            with mock.patch.object(
                Path, "is_symlink",
                lambda path: path.name == "frames" or actual(path),
            ):
                with self.assertRaisesRegex(
                    runner.Film180G2Rejected, "frames_symlink_not_admitted"
                ):
                    runner.original_png_index(root, manifest)

    def test_film_owner_output_root_symlink_is_not_trusted(self):
        actual = Path.is_symlink
        with tempfile.TemporaryDirectory() as temp:
            root, manifest = self.native_manifest(Path(temp))
            with mock.patch.object(
                Path, "is_symlink",
                lambda path: path == root or actual(path),
            ):
                with self.assertRaisesRegex(
                    runner.Film180G2Rejected, "frames_symlink_not_admitted"
                ):
                    runner.original_png_index(root, manifest)

    def test_source_signed_manifest_cannot_hide_duplicate_frames_field(self):
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.native_manifest(Path(temp))
            manifest_path = (
                root / "original-film180-manifest" / "artifact-manifest.json"
            )
            raw = manifest_path.read_text(encoding="utf-8")
            self.assertIn('"frames":', raw)
            forged = raw.replace('"frames":', '"frames": [], "frames":', 1)
            manifest_path.write_bytes(forged.encode("utf-8"))
            # Even a provider reporting the digest of the ambiguous JSON
            # cannot make the duplicate-key manifest authoritative.
            original["segments"][0]["artifact"]["manifest_sha256"] = (
                hashlib.sha256(forged.encode("utf-8")).hexdigest()
            )
            with self.assertRaisesRegex(
                runner.Film180G2Rejected, "duplicate_JSON_field"
            ):
                runner.original_png_index(root, original)

    def test_source_manifest_rejects_nonfinite_nested_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root, original = self.native_manifest(Path(temp))
            manifest_path = (
                root / "original-film180-manifest" / "artifact-manifest.json"
            )
            raw = manifest_path.read_text(encoding="utf-8")
            forged = raw.replace('"first_frame": 0', '"first_frame": NaN', 1)
            manifest_path.write_bytes(forged.encode("utf-8"))
            original["segments"][0]["artifact"]["manifest_sha256"] = (
                hashlib.sha256(forged.encode("utf-8")).hexdigest()
            )
            with self.assertRaisesRegex(
                runner.Film180G2Rejected, "manifest_nonfinite_json"
            ):
                runner.original_png_index(root, original)

    def test_source_binding_is_closed_to_new_operation_stage_names(self):
        self.assertEqual(runner.ORIGINAL_AUDIO_OWNER_NAME, "firstparty-film180-source.wav")
        self.assertEqual(runner.STAGE_TIMEOUT, 1500)
        self.assertEqual(runner.TRUSTED_MLT.as_posix(),
                         "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")



# These controls exercise the shared transport and evidence boundaries with
# synthetic process responses. They do not execute a renderer or install MLT.
from contextlib import contextmanager, ExitStack
import copy
import io
import struct
import subprocess
import types
import wave

import native_av_failure_guard as command_guard
import native_film180_media_readback as media
import native_mlt_g2_custody as custody
import native_mlt_g2_observation as observed
from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content, PcmContentError

CURRENT = {"source_sha": "a" * 40, "semwright_sha": observed.SEMWRIGHT_PIN,
           "run_id": "123", "run_attempt": "1"}


def film_source_wave(frames=288000, *, channels=2, rate=48000):
    period = b"".join(struct.pack("<hh", (i % 120 - 60) * 250, (i % 80 - 40) * 260)
                      for i in range(240))
    pcm = (period * ((frames + 239) // 240))[:frames * 4]
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm if channels == 2 else pcm[::2])
    return output.getvalue(), pcm


def film_dispatch():
    descriptor = {"artifact_id": 1, "run_id": 2, "run_attempt": 1,
                  "source_sha": "b" * 40, "zip_sha256": "c" * 64}
    return {
        "schema": custody.FILM_DISPATCH_SCHEMA,
        "motionwright_sha": CURRENT["source_sha"], "semwright_sha": CURRENT["semwright_sha"],
        "official_runtime": {
            "artifact": descriptor, "receipt_sha256": "d" * 64,
            "known_original_module_sha256": "e" * 64, "known_melt_sha256": "f" * 64},
        "compiler": {
            "artifact": descriptor, "source_mode": custody.SOURCE_MODE,
            "members": {"compiled_module": "only-libmltavformat.so",
                        "compiler_receipt_path": "only-compiler-receipt.json"}},
        "recipient": {"fixture": "key verification is covered by the common RSA contracts"},
    }


def film_env(root):
    return {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": CURRENT["source_sha"], "GITHUB_RUN_ID": CURRENT["run_id"],
            "GITHUB_WORKSPACE": str(root), "MW_MLT_SCOPE": custody.FILM_SCOPE,
            "MOTIONWRIGHT_APPROVED_FILM180_G2": "1", "MOTIONWRIGHT_APPROVED_MLT_G2": "1"}


class PrivateCommandCaptureTests(unittest.TestCase):
    def test_original_binary_streams_are_captured_before_json_parsing(self):
        events = []
        result = types.SimpleNamespace(stdout=b'{"result":"source-only"}\n',
                                       stderr=b"\x00/owner/private\r\n\x1a\xff", returncode=0)
        actual_loads = command_guard.json.loads
        def parse(raw):
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["stdout"], raw)
            return actual_loads(raw)
        with mock.patch.object(command_guard.subprocess, "run", return_value=result) as call, \
             mock.patch.object(command_guard.json, "loads", side_effect=parse):
            value = command_guard.run_sanitized_json(["test-double"], env={}, capture=events.append)
        self.assertEqual(value, {"result": "source-only"})
        call.assert_called_once()
        self.assertEqual(events[0]["stderr"], result.stderr)
        self.assertFalse(events[0]["automatic_retry"])

    def test_nonzero_native_failure_remains_primary_when_private_capture_also_fails(self):
        response = b'{"error":{"code":"Timeout","message":"/owner/private","outcome_known":false}}'
        result = types.SimpleNamespace(stdout=response, stderr=b"PRIVATE-CANARY", returncode=2)
        seen = []
        def capture(value):
            seen.append(value)
            raise OSError("SECONDARY-PRIVATE-CANARY")
        with mock.patch.object(command_guard.subprocess, "run", return_value=result) as call:
            with self.assertRaises(command_guard.NativeCommandFailure) as caught:
                command_guard.run_sanitized_json(["private/path"], env={}, capture=capture)
        call.assert_called_once()
        self.assertEqual(seen[0]["stdout"], response)
        self.assertTrue(caught.exception.private_capture_failed)
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")
        self.assertEqual(caught.exception.diagnostic["reported_error_code"], "Timeout")
        self.assertNotIn("PRIVATE-CANARY", str(caught.exception))
        self.assertNotIn("/owner/private", json.dumps(caught.exception.diagnostic))

    def test_timeout_retains_original_partial_streams_once_without_replay(self):
        failure = subprocess.TimeoutExpired(["one"], 4, output=b"partial\x1a", stderr=b"raw\x00")
        seen = []
        with mock.patch.object(command_guard.subprocess, "run", side_effect=failure) as call:
            with self.assertRaises(command_guard.NativeCommandFailure) as caught:
                command_guard.run_sanitized_json(["one"], env={}, timeout=4, capture=seen.append)
        call.assert_called_once()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["stdout"], b"partial\x1a")
        self.assertEqual(seen[0]["stderr"], b"raw\x00")
        self.assertIsNone(seen[0]["exit_code"])
        self.assertTrue(seen[0]["deadline_exceeded"])
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_process_creation_failure_is_retained_without_claiming_a_terminal(self):
        seen = []
        with mock.patch.object(command_guard.subprocess, "run",
                               side_effect=OSError("test process not created")) as call:
            with self.assertRaises(command_guard.NativeCommandFailure) as caught:
                command_guard.run_sanitized_json(["one"], env={}, capture=seen.append)
        call.assert_called_once()
        self.assertEqual(seen[0]["error_class"], "OSError")
        self.assertIsNone(seen[0]["exit_code"])
        self.assertEqual(caught.exception.diagnostic["source_outcome"], "UNKNOWN")

    def test_invalid_json_still_retains_original_zero_exit_response(self):
        seen = []
        result = types.SimpleNamespace(stdout=b"not JSON\r\n\x1a", stderr=b"", returncode=0)
        with mock.patch.object(command_guard.subprocess, "run", return_value=result) as call:
            with self.assertRaises(command_guard.NativeCommandFailure):
                command_guard.run_sanitized_json(["one"], env={}, capture=seen.append)
        call.assert_called_once()
        self.assertEqual(seen[0]["stdout"], result.stdout)
        self.assertEqual(seen[0]["exit_code"], 0)

    def test_capture_failure_after_valid_native_json_blocks_further_verification(self):
        result = types.SimpleNamespace(stdout=b'{"native_result_reported":"SUCCEEDED"}',
                                       stderr=b"", returncode=0)
        with mock.patch.object(command_guard.subprocess, "run", return_value=result) as call:
            with self.assertRaises(command_guard.NativeCommandFailure) as caught:
                command_guard.run_sanitized_json(
                    ["one"], env={}, capture=mock.Mock(side_effect=OSError("private disk")))
        call.assert_called_once()
        self.assertTrue(caught.exception.private_capture_failed)

    def test_pcm_capture_uses_the_single_original_decode_without_sample_adjustment(self):
        _, pcm = film_source_wave()
        seen = []
        result = types.SimpleNamespace(stdout=pcm, stderr=b"decoder-original", returncode=0)
        with mock.patch.object(media.subprocess, "run", return_value=result) as call:
            returned = media.decode_original_audio(Path("owned.mp4"), "ffmpeg", capture=seen.append)
        call.assert_called_once()
        self.assertIs(returned, pcm)
        self.assertIs(seen[0]["stdout"], pcm)
        self.assertEqual(seen[0]["stderr"], b"decoder-original")
        self.assertFalse(seen[0]["automatic_retry"])

    def test_failed_pcm_decode_preserves_original_error_even_if_capture_fails(self):
        failure = subprocess.TimeoutExpired(["decode"], 120, output=b"\x01\x00", stderr=b"original")
        seen = []
        def capture(value):
            seen.append(value)
            raise OSError("private capture failure")
        with mock.patch.object(media.subprocess, "run", side_effect=failure) as call:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                media.decode_original_audio(Path("owned.mp4"), "ffmpeg", capture=capture)
        self.assertIs(caught.exception, failure)
        self.assertTrue(failure.private_capture_failed)
        self.assertEqual(seen[0]["stdout"], b"\x01\x00")
        call.assert_called_once()

    def test_short_pcm_is_captured_before_the_existing_budget_rejects_it(self):
        seen = []
        result = types.SimpleNamespace(stdout=b"\x00" * 211200, stderr=b"", returncode=0)
        with mock.patch.object(media.subprocess, "run", return_value=result) as call:
            with self.assertRaisesRegex(media.OriginalMediaMismatch, "sample_budget_changed"):
                media.decode_original_audio(Path("owned.mp4"), "ffmpeg", capture=seen.append)
        call.assert_called_once()
        self.assertEqual(seen[0]["stdout"], result.stdout)


class FilmRuntimeEnvelopeTests(unittest.TestCase):
    def test_manual_film_scope_still_refuses_PR_and_attempt_two(self):
        env = film_env(Path("source"))
        self.assertEqual(custody.context(env), CURRENT)
        for field, value in (("GITHUB_EVENT_NAME", "pull_request"),
                             ("GITHUB_RUN_ATTEMPT", "2"),
                             ("MW_MLT_SCOPE", "normal")):
            with self.subTest(field=field), self.assertRaises(custody.CustodyRejected):
                custody.context({**env, field: value})

    def test_film_dispatch_accepts_exact_pins_and_only_historical_binary_members(self):
        value = film_dispatch()
        with mock.patch.object(custody, "public_key"):
            self.assertEqual(custody.validate_dispatch(
                custody.json_bytes(value), CURRENT, scenario=custody.FILM_SCOPE), value)
        self.assertEqual(set(value["compiler"]["members"]),
                         {"compiled_module", "compiler_receipt_path"})

    def test_dispatch_schemas_cannot_be_borrowed_between_film_and_repaired_G2(self):
        value = film_dispatch()
        with mock.patch.object(custody, "public_key"):
            with self.assertRaisesRegex(custody.CustodyRejected, "source_binding"):
                custody.validate_dispatch(custody.json_bytes(value), CURRENT)
            for schema in (custody.DISPATCH_SCHEMA, custody.SOURCE_DISPATCH_SCHEMA):
                with self.subTest(schema=schema), self.assertRaises(custody.CustodyRejected):
                    custody.validate_dispatch(
                        custody.json_bytes({**value, "schema": schema}), CURRENT,
                        scenario=custody.FILM_SCOPE)

    def test_film_dispatch_refuses_invented_compiler_C_members_and_fallback_modes(self):
        for kind in ("source-member", "source-mode"):
            value = film_dispatch()
            if kind == "source-member":
                value["compiler"]["members"]["original_c_source"] = "invented-original.c"
            else:
                value["compiler"]["source_mode"] = "fallback-on-absent"
            with mock.patch.object(custody, "public_key"), \
                 self.subTest(kind=kind), self.assertRaises(custody.CustodyRejected):
                custody.validate_dispatch(custody.json_bytes(value), CURRENT,
                                          scenario=custody.FILM_SCOPE)

    def test_film_intake_does_not_fetch_the_historical_short_WAV(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            env = film_env(root)
            env["MW_MLT_G2_ADMISSION"] = json.dumps(film_dispatch())
            control, installed, session = (root / name for name in ("control", "installed", "session"))
            with mock.patch.object(custody.sys, "platform", "linux"), \
                 mock.patch.object(custody.os, "geteuid", return_value=1001, create=True), \
                 mock.patch.object(custody, "names", return_value=(control, installed, session)), \
                 mock.patch.object(custody, "source_guard", return_value={"source": "unchanged"}), \
                 mock.patch.object(custody, "public_key"), \
                 mock.patch.object(custody, "download_artifact", return_value=b"official ZIP") as download, \
                 mock.patch.object(custody, "zip_member", return_value=b'{"fixture":"official"}'), \
                 mock.patch.object(observed, "verify_official_runtime_bindings"), \
                 mock.patch.object(observed, "reference_source") as historical, \
                 mock.patch.object(custody, "intake_compiler") as compiler:
                custody.prepare(env)
            download.assert_called_once()
            self.assertEqual(download.call_args.args[1], "official")
            historical.assert_not_called()
            compiler.assert_called_once()
            self.assertFalse((control / "intake/original-artifact.zip").exists())
            intent = json.loads((control / "records/film-source-intent.json").read_bytes())
            self.assertEqual(intent["pcm_frames"], 288000)
            self.assertFalse(intent["historical_reference_WAV_reused"])
            self.assertFalse(intent["native_effect_started"])

    def test_privileged_receipt_handoff_validates_the_selected_film_schema(self):
        class BoundaryReached(Exception):
            pass
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "records").mkdir()
            (root / "records/dispatch.json").write_bytes(custody.json_bytes(film_dispatch()))
            real_stat = Path.stat
            def owned_stat(path, *args, **kwargs):
                value = real_stat(path, *args, **kwargs)
                if path == root:
                    fields = list(value)
                    fields[4:6] = [1001, 1001]
                    return os.stat_result(fields)
                return value
            def validate(raw, current, *, scenario):
                self.assertEqual(scenario, custody.FILM_SCOPE)
                self.assertEqual(json.loads(raw)["schema"], custody.FILM_DISPATCH_SCHEMA)
                raise BoundaryReached()
            with mock.patch.object(custody.sys, "platform", "linux"), \
                 mock.patch.object(custody.os, "geteuid", return_value=0, create=True), \
                 mock.patch.object(custody, "names", return_value=(root, root / "installed", root / "session")), \
                 mock.patch.object(Path, "stat", owned_stat), \
                 mock.patch.object(custody, "validate_dispatch", side_effect=validate), \
                 self.assertRaises(BoundaryReached):
                custody.handoff(film_env(root), 1001, 1001)

    def test_generated_film_inputs_omit_historical_reference_and_use_shared_runtime_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for directory in ("records", "handoff"):
                (root / directory).mkdir()
            (root / "records/dispatch.json").write_bytes(custody.json_bytes(film_dispatch()))
            installation = b'{"fixture":"original installation receipt"}'
            (root / "handoff/installation.json").write_bytes(installation)
            handoff = {"context": CURRENT, "recipient_uid": 1001,
                       "installation_receipt_admitted": True, "custody_errors": [],
                       "receipt_sha256": custody.sha(installation)}
            (root / "handoff/handoff.json").write_bytes(custody.json_bytes(handoff))
            with mock.patch.object(custody.sys, "platform", "linux"), \
                 mock.patch.object(custody.os, "geteuid", return_value=1001, create=True), \
                 mock.patch.object(custody.os, "getuid", return_value=1001, create=True), \
                 mock.patch.object(custody, "names", return_value=(root, root / "installed", root / "session")), \
                 mock.patch.object(custody, "public_key"):
                custody.make_inputs(film_env(root))
            result = json.loads((root / "records/g2-inputs.json").read_bytes())
            self.assertEqual(result["schema"], custody.FILM_INPUT_SCHEMA)
            self.assertEqual(set(result), custody.RUNTIME_INPUT_KEYS | {"schema", "private_evidence_dir"})
            self.assertNotIn("original_artifact_zip", result)

    def test_common_runtime_preflight_rejects_extra_or_fixture_specific_inputs(self):
        for extra in ("original_artifact_zip", "verified", "private_evidence_dir"):
            data = {key: "closed" for key in custody.RUNTIME_INPUT_KEYS}
            data[extra] = "not allowed"
            with self.subTest(extra=extra), \
                 mock.patch.object(observed.admission, "checked_bytes") as read, \
                 self.assertRaisesRegex(observed.G2Unverified, "runtime_inputs_schema"):
                custody.verify_runtime_inputs(data, source_sha="a" * 40, semwright_sha=observed.SEMWRIGHT_PIN)
            read.assert_not_called()

    def test_film_prepare_rejects_reference_archive_before_runtime_or_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = {key: "placeholder" for key in custody.RUNTIME_INPUT_KEYS}
            data.update(schema=custody.FILM_INPUT_SCHEMA, private_evidence_dir=str(root / "private"),
                        original_artifact_zip=str(root / "historical.zip"))
            path = root / "inputs.json"
            path.write_bytes(custody.json_bytes(data))
            with mock.patch.dict(os.environ, film_env(root)), \
                 mock.patch.object(observed.admission, "disposable_linux_runner", return_value=root), \
                 mock.patch.object(custody, "verify_runtime_inputs") as runtime, \
                 self.assertRaisesRegex(runner.Film180G2Rejected, "inputs_schema"):
                runner.FilmAttempt.prepare(path)
            runtime.assert_not_called()
            self.assertFalse((root / "private").exists())

    def test_film_preflight_uses_common_validator_once_and_never_creates_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = {key: "placeholder" for key in custody.RUNTIME_INPUT_KEYS}
            data.update(schema=custody.FILM_INPUT_SCHEMA, private_evidence_dir=str(root / "private"))
            path = root / "inputs.json"
            path.write_bytes(custody.json_bytes(data))
            with mock.patch.dict(os.environ, film_env(root)), \
                 mock.patch.object(observed.admission, "disposable_linux_runner", return_value=root), \
                 mock.patch.object(custody, "verify_runtime_inputs", return_value=(
                     {"compiler": "test double only"}, {"alias": "test double only"})) as runtime:
                attempt = runner.FilmAttempt.prepare(path)
            runtime.assert_called_once()
            self.assertEqual(set(runtime.call_args.args[0]), custody.RUNTIME_INPUT_KEYS)
            self.assertIsNone(attempt.wave_sha)
            self.assertFalse((attempt.evidence_dir / "firstparty-source.wav").exists())

    def test_film_public_projection_rejects_a_repaired_master_receipt(self):
        final = {"schema": "motionwright.native.mlt-g2-custody/1",
                 "terminal_scope": observed.TERMINAL_SCOPE, "native_operation_retried": False}
        with self.assertRaisesRegex(custody.CustodyRejected, "private_final_contract"):
            custody.projection(CURRENT, final, 0, "success", b"cipher", {"sha256": "a" * 64},
                               scenario=custody.FILM_SCOPE)


class FilmAttemptCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "private"
        self.evidence.mkdir()
        self.attempt = runner.FilmAttempt(
            {"run_id": "123", "run_attempt": "1", "known_melt_sha256": "a" * 64}, self.evidence)

    def tearDown(self):
        self.temp.cleanup()

    def final(self):
        self.attempt.finish()
        return json.loads((self.evidence / "final-custody.json").read_bytes())

    def test_own_six_second_wave_is_preserved_as_exact_binary_source(self):
        packed, _ = film_source_wave()
        path = self.root / "own.wav"
        path.write_bytes(packed)
        self.attempt.retain_wave(path)
        self.assertEqual((self.evidence / "firstparty-source.wav").read_bytes(), packed)
        self.assertEqual(self.attempt.wave_sha, hashlib.sha256(packed).hexdigest())
        receipt = json.loads((self.evidence / "firstparty-source-custody.json").read_bytes())
        self.assertEqual(receipt["pcm_frames"], 288000)
        self.assertFalse(receipt["historical_reference_WAV_reused"])

    def test_historical_short_WAV_and_wrong_source_profiles_are_rejected(self):
        for index, (frames, rate, channels) in enumerate(((52800, 48000, 2),
                                                        (288000, 44100, 2),
                                                        (288000, 48000, 1))):
            raw, _ = film_source_wave(frames, rate=rate, channels=channels)
            path = self.root / ("wrong-" + str(index) + ".wav")
            path.write_bytes(raw)
            with self.subTest(frames=frames, rate=rate, channels=channels), \
                 self.assertRaisesRegex(runner.Film180G2Rejected, "own_six_second"):
                self.attempt.retain_wave(path)
        self.assertIsNone(self.attempt.wave_sha)
        self.assertFalse((self.evidence / "firstparty-source.wav").exists())

    def test_private_stream_custody_keeps_all_byte_values_CRLF_and_Ctrl_Z(self):
        raw = bytes(range(256)) + b"\r\n\x1a\x00"
        self.attempt.capture("original.render")({
            "stdout": raw, "stderr": raw[::-1], "exit_code": 0, "automatic_retry": False})
        self.assertEqual((self.evidence / "original.render.stdout").read_bytes(), raw)
        self.assertEqual((self.evidence / "original.render.stderr").read_bytes(), raw[::-1])
        receipt = json.loads((self.evidence / "original.render.terminal.json").read_bytes())
        self.assertEqual(receipt["stdout_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertFalse(receipt["native_terminal_inferred_from_exit"])

    def test_command_label_cannot_be_replayed_after_its_first_response(self):
        with mock.patch.object(runner, "source_command", return_value={"source": "fixture"}) as call:
            self.attempt.command("source.seed", ["seed"], {})
            with self.assertRaisesRegex(runner.Film180G2Rejected, "replay_refused"):
                self.attempt.command("source.seed", ["seed"], {})
        call.assert_called_once()

    def test_original_timeout_is_recorded_unknown_and_never_replayed(self):
        error = command_guard.NativeCommandFailure(command_guard.classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True))
        with mock.patch.object(runner, "source_command", side_effect=error) as call:
            with self.assertRaises(command_guard.NativeCommandFailure):
                self.attempt.command("original.render", ["render"], {}, native=True)
            with self.assertRaisesRegex(runner.Film180G2Rejected, "replay_refused"):
                self.attempt.command("original.render", ["render"], {}, native=True)
        call.assert_called_once()
        self.attempt.retain_error(error)
        final = self.final()
        self.assertEqual(final["native_terminal"], "ERROR_OR_UNKNOWN")
        self.assertFalse(final["source_content_and_live_module_verified"])
        self.assertFalse(final["native_operation_retried"])

    def test_original_success_does_not_claim_the_missing_edited_execution(self):
        self.attempt.native_commands = {
            "original.render": "SUCCESS_REPORTED", "original.master": "SUCCESS_REPORTED"}
        final = self.final()
        self.assertEqual(final["native_terminal"], "RESULT_REPORTED_UNVERIFIED")
        self.assertFalse(final["source_content_and_live_module_verified"])

    def test_reported_success_retains_its_terminal_when_custody_is_partial(self):
        self.attempt.native_commands = {
            label: "SUCCESS_REPORTED" for label in
            ("original.render", "original.master", "edited.render", "edited.master")}
        self.attempt.completed = True
        self.attempt.completed_stages = {"original": {}, "edited": {}}
        self.attempt.secondary_failures.append("changing-WAL-observed")
        final = self.final()
        self.assertEqual(final["native_terminal"], "SUCCESS_REPORTED")
        self.assertFalse(final["source_content_and_live_module_verified"])

    def test_complete_scoped_observation_never_grants_media_or_human_acceptance(self):
        self.attempt.native_commands = {
            label: "SUCCESS_REPORTED" for label in
            ("original.render", "original.master", "edited.render", "edited.master")}
        self.attempt.completed = True
        self.attempt.completed_stages = {"original": {}, "edited": {}}
        final = self.final()
        self.assertTrue(final["source_content_and_live_module_verified"])
        public = custody.projection(
            CURRENT, final, 0, "success", b"ciphertext", {"sha256": "a" * 64},
            producer_started=True, source_unchanged=True, scenario=custody.FILM_SCOPE)
        self.assertTrue(public["source_content_and_live_module_verified"])
        self.assertEqual(set(public), custody.PUBLIC_KEYS)
        self.assertFalse(public["physical_AV_master_acceptance"])
        self.assertFalse(public["human_acceptance"])
        self.assertFalse(public["packet_PTS_and_AAC_priming_verified"])
        self.assertFalse(public["release_authorized"])
        self.assertNotIn(str(self.evidence), json.dumps(public))
        partial = custody.projection(
            CURRENT, final, 0, "success", b"ciphertext", {"sha256": "a" * 64},
            producer_started=True, source_unchanged=True, selected_originals_complete=False,
            scenario=custody.FILM_SCOPE)
        self.assertFalse(partial["source_content_and_live_module_verified"])
        self.assertEqual(partial["native_terminal"], "SUCCESS_REPORTED")

    def test_source_WAV_change_stops_before_any_native_stage(self):
        audio = self.root / "source.wav"
        audio.write_bytes(b"changed source")
        self.attempt.wave_sha = "0" * 64
        with mock.patch.object(self.attempt, "command") as command:
            with self.assertRaisesRegex(runner.Film180G2Rejected, "WAV_changed_between"):
                runner.stage_and_decode(
                    {}, self.root / "connection", self.root / "db", self.root / "project.json",
                    audio, "original", {}, self.evidence, attempt=self.attempt, daemon_pid=9)
        command.assert_not_called()

    def test_six_second_source_uses_the_unchanged_full_256_frame_oracle_at_offset_zero(self):
        packed, pcm = film_source_wave()
        result = inspect_source_pcm16_stereo_content(
            packed, pcm, original_wav_sha256=hashlib.sha256(packed).hexdigest(),
            decoded_pcm_sha256=hashlib.sha256(pcm).hexdigest())
        self.assertEqual(result["full_source_window_count"], 1125)
        self.assertEqual(result["source_start_offset_frames_assumed"], 0)
        self.assertFalse(result["source_to_decoded_PTS_and_AAC_priming_verified"])
        damaged = bytearray(pcm)
        for frame in range(288000 - 256, 288000):
            damaged[frame * 4 + 2:frame * 4 + 4] = b"\0\0"
        with self.assertRaisesRegex(PcmContentError, "missing_or_scaled:channel=1"):
            inspect_source_pcm16_stereo_content(
                packed, bytes(damaged), original_wav_sha256=hashlib.sha256(packed).hexdigest(),
                decoded_pcm_sha256=hashlib.sha256(damaged).hexdigest())


    def test_failed_subprocess_buffers_survive_without_replacing_the_primary_error(self):
        stdout = b"original partial output\r\n\x00\x1a\xff"
        stderr = b"original auxiliary diagnostic /owner/private\r\n"
        for timeout in (False, True):
            for capture_failure in (False, True):
                with self.subTest(timeout=timeout, capture_failure=capture_failure):
                    evidence = self.root / f"failure-{int(timeout)}-{int(capture_failure)}"
                    evidence.mkdir()
                    attempt = runner.FilmAttempt({}, evidence)
                    attempt.native_commands = {"original.master": "SUCCESS_REPORTED"}
                    failure = (
                        subprocess.TimeoutExpired(["fixture-decode"], 120,
                                                  output=stdout, stderr=stderr)
                        if timeout else subprocess.CalledProcessError(
                            2, ["fixture-decode"], output=stdout, stderr=stderr)
                    )
                    if capture_failure:
                        with mock.patch.object(runner, "new_bytes",
                                               side_effect=OSError("fixture capture failure")):
                            attempt.retain_error(failure)
                    else:
                        attempt.retain_error(failure)
                    self.assertIs(attempt.error, failure)
                    error = json.loads((evidence / "original-attempt-error.json").read_bytes())
                    self.assertEqual(error["error_class"], type(failure).__name__)
                    self.assertEqual(error["error"], str(failure))
                    if capture_failure:
                        self.assertIn("original-subprocess-custody", attempt.secondary_failures)
                    else:
                        self.assertEqual((evidence / "original.failed-subprocess.stdout").read_bytes(), stdout)
                        self.assertEqual((evidence / "original.failed-subprocess.stderr").read_bytes(), stderr)
                        terminal = json.loads(
                            (evidence / "original.failed-subprocess.terminal.json").read_bytes())
                        self.assertEqual(terminal["exit_code"], None if timeout else 2)
                        self.assertEqual(terminal["deadline_exceeded"], timeout)
                        self.assertFalse(terminal["automatic_retry"])
                        self.assertFalse(terminal["native_terminal_inferred_from_exit"])
                    attempt.finish()
                    final = json.loads((evidence / "final-custody.json").read_bytes())
                    self.assertEqual(final["native_terminal"], "RESULT_REPORTED_UNVERIFIED")
                    self.assertFalse(final["source_content_and_live_module_verified"])


class FilmStageOrderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "private"
        self.evidence.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.audio = self.root / "source.wav"
        self.audio.write_bytes(b"source-only WAV fixture")
        self.project = self.root / "project.json"
        self.project.write_text(json.dumps({
            "id": "00000000-0000-4000-8000-000000000001", "revision": 7}))
        self.attempt = runner.FilmAttempt({"known_melt_sha256": "a" * 64}, self.evidence)
        self.attempt.wave_sha = runner.digest(self.audio)
        self.witness = mock.Mock()
        self.witness.candidates = {(9, 10)}
        self.witness.receipts = {}
        self.witness.fatal = None
        self.witness.observation_failures = []
        self.witness.diagnostic.return_value = {"bounded_journal_complete": True}

    def tearDown(self):
        self.temp.cleanup()

    def run_stage(self, callback):
        def command(label, args, env, **kwargs):
            if kwargs.get("native"):
                self.attempt.native_commands[label] = "ERROR_OR_UNKNOWN"
            if label == "original.render":
                (self.evidence / "original-native-Film180-evidence.json").write_text("{}")
                return {"native_film180_host": "SUCCEEDED_REPORTED", "frame_count": 180}
            if label == "original.stage-audio":
                return {"staged": True}
            self.assertEqual(label, "original.master")
            self.witness.start.assert_called_once()
            return callback()
        with mock.patch.object(runner, "original_png_index", return_value={"source-only": True}), \
             mock.patch.object(observed, "LiveObserver", return_value=self.witness), \
             mock.patch.object(self.attempt, "command", side_effect=command) as calls:
            try:
                return runner.stage_and_decode(
                    {"output": self.output}, self.root / "connection", self.root / "db",
                    self.project, self.audio, "original", {}, self.evidence,
                    attempt=self.attempt, daemon_pid=9)
            finally:
                self.calls = list(calls.call_args_list)

    def test_known_native_master_terminal_precedes_failed_observer_custody(self):
        failure = OSError("observer stop failed")
        self.witness.stop.side_effect = failure
        with self.assertRaises(OSError) as caught:
            self.run_stage(lambda: {"frame_count": 180, "native_result_reported": "SUCCEEDED"})
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.attempt.native_commands["original.master"], "SUCCESS_REPORTED")
        self.assertEqual(sum(call.args[0] == "original.master" for call in self.calls), 1)

    def test_native_failure_is_not_replaced_by_a_secondary_observer_failure(self):
        primary = command_guard.NativeCommandFailure(command_guard.classify_native_command_failure(
            stdout=b"", stderr=b"", exit_code=None, deadline_exceeded=True))
        self.witness.stop.side_effect = OSError("secondary private failure")
        def callback():
            raise primary
        with self.assertRaises(command_guard.NativeCommandFailure) as caught:
            self.run_stage(callback)
        self.assertIs(caught.exception, primary)
        self.assertEqual(self.attempt.native_commands["original.master"], "ERROR_OR_UNKNOWN")
        self.assertEqual(sum(call.args[0] == "original.master" for call in self.calls), 1)

    def test_observer_start_failure_prevents_the_native_master_call(self):
        self.witness.start.side_effect = OSError("observer absent")
        callback = mock.Mock()
        with self.assertRaises(OSError):
            self.run_stage(callback)
        callback.assert_not_called()
        self.assertFalse(any(call.args[0] == "original.master" for call in self.calls))

    def test_unknown_original_stage_prevents_edit_and_retains_owner_database(self):
        import native_mlt_sequence_e2e as canonical
        original = runner.Film180G2Rejected("fixture native status unknown")
        self.attempt.inputs.update(run_id="123", run_attempt="1")
        commands = []
        def source(label, args, env, **kwargs):
            commands.append(label)
            self.assertEqual(label, "source.seed")
            Path(args[1]).write_bytes(b"original database fixture")
            Path(str(args[1]) + "-wal").write_bytes(b"original WAL fixture")
            packed, _ = film_source_wave()
            Path(args[2]).write_bytes(packed)
            Path(args[3]).write_text("{}")
            return {"frame_count": 180, "saved_reopen": True,
                    "native_render_performed": False, "project_id": "project", "revision": 7}
        @contextmanager
        def broker(*args):
            yield self.root / "connection", {}, 9
        def fail_stage(*args, **kwargs):
            self.attempt.native_commands["original.render"] = "ERROR_OR_UNKNOWN"
            raise original
        with mock.patch.object(runner.FilmAttempt, "prepare", return_value=self.attempt), \
             mock.patch.object(runner, "checked_plan", return_value={"source-only": True}), \
             mock.patch.object(observed.admission, "disposable_linux_runner", return_value=self.root), \
             mock.patch.object(self.attempt, "command", side_effect=source), \
             mock.patch.object(runner, "fresh_source_readback"), \
             mock.patch.object(runner, "bound_broker", broker), \
             mock.patch.object(runner, "stage_and_decode", side_effect=fail_stage) as stage, \
             self.assertRaises(runner.Film180G2Rejected) as caught:
            runner.execute(self.root / "inputs.json")
        self.assertIs(caught.exception, original)
        self.assertEqual(commands, ["source.seed"])
        stage.assert_called_once()
        marker = json.loads((self.evidence / "owner-workspace.json").read_bytes())
        owner = Path(marker["path"])
        self.assertTrue(owner.is_dir())
        database = owner / "motionwright-data/original-film180.sqlite3"
        self.assertEqual(database.read_bytes(), b"original database fixture")
        self.assertEqual(Path(str(database) + "-wal").read_bytes(), b"original WAL fixture")
        terminal = json.loads((self.evidence / "final-custody.json").read_bytes())
        self.assertEqual(terminal["native_terminal"], "ERROR_OR_UNKNOWN")
        self.assertFalse(terminal["native_operation_retried"])
        self.assertFalse(terminal["source_content_and_live_module_verified"])

    def completed_master_callback(self, *, proof_change=None, ack_change=None):
        relative = runner.original_native_master_name(
            "00000000-0000-4000-8000-000000000001", 7, "original")
        raw = b"source-only MP4 custody fixture"
        self.original_master = self.output / relative
        self.original_master.write_bytes(raw)
        self.original_digest = hashlib.sha256(raw).hexdigest()
        self.witness.require_one.return_value = {
            "publication_binding": "SAME_ORIGINAL_OPEN_DESCRIPTOR_BYTES",
            "published_output_sha256": self.original_digest,
            "published_output_bytes": len(raw), **(proof_change or {}),
        }
        def callback():
            artifact = {"root":"output","path":relative,"sha256":self.original_digest,
                        "bytes":len(raw),**(ack_change or {})}
            (self.evidence / "original-H264-AAC-native-master.json").write_text(
                json.dumps({"master":{"artifact":artifact}}),encoding="utf-8")
            return {"frame_count":180,"native_result_reported":"SUCCEEDED"}
        return callback

    def test_original_live_descriptor_must_match_the_native_ack_before_decode(self):
        for proof_change,ack_change in (
            ({"published_output_sha256":"0"*64},{}),
            ({"published_output_bytes":1},{}),
            ({"published_output_bytes":"30"},{}),
            ({},{"sha256":"0"*64}),
            ({},{"bytes":True}),
        ):
            with self.subTest(proof=proof_change,ack=ack_change):
                fixture = FilmStageOrderingTests("runTest")
                fixture.setUp()
                try:
                    callback = fixture.completed_master_callback(
                        proof_change=proof_change,ack_change=ack_change)
                    with mock.patch("native_film180_media_readback.verified_source_readback",
                                    return_value={"test_double":True}) as decode, \
                         mock.patch.object(runner,"actual_all180_original_RGB_digests",
                                           return_value={"source_frame_count":180}), \
                         mock.patch.object(runner,"native_png_vs_h264_observations",
                                           return_value={"test_double":True}):
                        with self.assertRaisesRegex(runner.Film180G2Rejected,
                                                    "differs_from_native_result_ack"):
                            fixture.run_stage(callback)
                    decode.assert_not_called()
                    fixture.witness.require_one.assert_called_once_with(fixture.original_master)
                    fixture.witness.close.assert_called_once()
                    self.assertFalse((fixture.evidence / "original-original-live-MLT-loaded-module.json").exists())
                    self.assertEqual(fixture.attempt.native_commands["original.master"],"SUCCESS_REPORTED")
                    self.assertEqual(sum(call.args[0]=="original.master" for call in fixture.calls),1)
                finally:
                    fixture.tearDown()

    def test_exact_live_descriptor_and_native_ack_allow_one_original_decode(self):
        callback = self.completed_master_callback()
        with mock.patch("native_film180_media_readback.verified_source_readback",
                        return_value={"test_double":True}) as decode, \
             mock.patch.object(runner,"actual_all180_original_RGB_digests",
                               return_value={"source_frame_count":180}), \
             mock.patch.object(runner,"native_png_vs_h264_observations",
                               return_value={"test_double":True}):
            result = self.run_stage(callback)
        decode.assert_called_once()
        self.assertEqual(decode.call_args.args[9],self.original_digest)
        self.witness.require_one.assert_called_once_with(self.original_master)
        self.witness.close.assert_called_once()
        proof = json.loads((self.evidence / "original-original-live-MLT-loaded-module.json").read_bytes())
        self.assertEqual(proof["original_live_pid_observer_receipt"]["published_output_sha256"],
                         self.original_digest)
        self.assertEqual(result["master_sha"],self.original_digest)
        self.assertEqual(sum(call.args[0]=="original.master" for call in self.calls),1)
        self.assertEqual(self.attempt.native_commands["original.master"],"SUCCESS_REPORTED")

class FilmLiveObserverLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.evidence = self.root / "private"
        self.evidence.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.attempt = runner.FilmAttempt({"known_melt_sha256": "a"*64}, self.evidence)
        self.witness = mock.Mock()
        self.witness.diagnostic.return_value = {
            "schema": "motionwright.native.mlt-live-observer-diagnostic/1",
            "bounded_journal_complete": True,
            "native_operation_retried": False,
        }
        self.patch = mock.patch.object(observed, "LiveObserver", return_value=self.witness)
        self.constructor = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)

    def scope(self):
        return runner.retained_live_observer(self.attempt, "original", 9, self.output)

    def error_document(self):
        return json.loads((self.evidence / "original-live-observer/original-observation-error.json").read_bytes())

    def test_stop_retention_diagnostic_and_binding_precede_descriptor_close(self):
        events = []
        self.witness.start.side_effect = lambda: events.append("start")
        self.witness.stop.side_effect = lambda: events.append("stop")
        self.witness.retain_intermediate.side_effect = lambda path: events.append("retain")
        diagnostic = self.witness.diagnostic.return_value
        self.witness.diagnostic.side_effect = lambda: events.append("diagnostic") or diagnostic
        self.witness.require_one.side_effect = lambda path: events.append("bind") or {"test_double": True}
        self.witness.close.side_effect = lambda: events.append("close")
        with self.scope() as (watcher, finish, custody_record):
            events.append("native-terminal")
            self.attempt.native_commands["original.master"] = "SUCCESS_REPORTED"
            finish()
            watcher.require_one(self.output / "original.mp4")
            original = (self.evidence / "original-live-observer/observer-diagnostic.json").read_bytes()
            self.assertEqual(custody_record["diagnostic_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(events, ["start","native-terminal","stop","retain","diagnostic","bind","close"])
        self.witness.retain_intermediate.assert_called_once_with(self.evidence / "original-live-observer")

    def test_unknown_native_error_stays_primary_when_stop_and_close_also_fail(self):
        original = RuntimeError("native status UNKNOWN")
        self.witness.stop.side_effect = OSError("stop unavailable")
        self.witness.close.side_effect = OSError("close unavailable")
        with self.assertRaises(RuntimeError) as caught:
            with self.scope():
                self.attempt.native_commands["original.master"] = "ERROR_OR_UNKNOWN"
                raise original
        self.assertIs(caught.exception, original)
        self.assertIn("native status UNKNOWN", json.dumps(self.error_document()))
        self.assertEqual(self.attempt.native_commands["original.master"], "ERROR_OR_UNKNOWN")
        self.witness.start.assert_called_once()
        self.witness.stop.assert_called_once()
        self.witness.retain_intermediate.assert_called_once()
        self.witness.diagnostic.assert_called_once()
        self.witness.close.assert_called_once()
        self.witness.require_one.assert_not_called()

    def test_start_failure_retains_available_diagnostic_and_prevents_native_body(self):
        original = OSError("start unavailable")
        self.witness.start.side_effect = original
        body = mock.Mock()
        with self.assertRaises(OSError) as caught:
            with self.scope():
                body()
        self.assertIs(caught.exception, original)
        body.assert_not_called()
        self.witness.stop.assert_called_once()
        self.witness.retain_intermediate.assert_called_once()
        self.witness.diagnostic.assert_called_once()
        self.witness.close.assert_called_once()
        self.assertIn("start unavailable", json.dumps(self.error_document()))

    def test_intermediate_failure_still_custodies_diagnostic_and_blocks_binding(self):
        original = OSError("original descriptor snapshot unavailable")
        self.witness.retain_intermediate.side_effect = original
        with self.assertRaises(OSError) as caught:
            with self.scope() as (watcher, finish, _record):
                finish()
                watcher.require_one(self.output / "original.mp4")
        self.assertIs(caught.exception, original)
        self.assertTrue((self.evidence / "original-live-observer/observer-diagnostic.json").is_file())
        self.witness.require_one.assert_not_called()
        self.witness.close.assert_called_once()

    def test_diagnostic_write_failure_does_not_replace_native_unknown(self):
        original = RuntimeError("original native UNKNOWN")
        original_write = observed.new_bytes
        def write(path, raw):
            if path.name == "observer-diagnostic.json":
                raise OSError("diagnostic sink unavailable")
            return original_write(path, raw)
        with mock.patch.object(observed, "new_bytes", side_effect=write):
            with self.assertRaises(RuntimeError) as caught:
                with self.scope():
                    raise original
        self.assertIs(caught.exception, original)
        self.assertIn("original native UNKNOWN", json.dumps(self.error_document()))
        self.assertIn("original.observer-diagnostic", self.attempt.secondary_failures)
        self.witness.close.assert_called_once()

    def test_incomplete_bounded_journal_is_retained_but_cannot_bind_a_master(self):
        self.witness.diagnostic.return_value["bounded_journal_complete"] = False
        with self.assertRaisesRegex(runner.Film180G2Rejected, "diagnostic_incomplete"):
            with self.scope() as (watcher, finish, _record):
                finish()
                watcher.require_one(self.output / "original.mp4")
        retained = json.loads((self.evidence / "original-live-observer/observer-diagnostic.json").read_bytes())
        self.assertFalse(retained["bounded_journal_complete"])
        self.witness.require_one.assert_not_called()
        self.witness.close.assert_called_once()

    def test_close_failure_prevents_success_without_changing_known_native_terminal(self):
        original = OSError("descriptor close failed")
        self.witness.close.side_effect = original
        with self.assertRaises(OSError) as caught:
            with self.scope() as (_watcher, finish, _record):
                self.attempt.native_commands["original.master"] = "SUCCESS_REPORTED"
                finish()
        self.assertIs(caught.exception, original)
        self.assertEqual(self.attempt.native_commands["original.master"], "SUCCESS_REPORTED")
        self.assertIn("original.observer-close", self.attempt.secondary_failures)
        self.assertIn("descriptor close failed", json.dumps(self.error_document()))

    def test_failed_live_binding_keeps_exact_bounded_error_and_closes_original_descriptor(self):
        original = observed.G2Unverified("fixture module inode changed")
        self.witness.require_one.side_effect = original
        with self.assertRaises(observed.G2Unverified) as caught:
            with self.scope() as (watcher, finish, _record):
                finish()
                watcher.require_one(self.output / "original.mp4")
        self.assertIs(caught.exception, original)
        self.assertIn("fixture module inode changed", json.dumps(self.error_document()))
        self.witness.stop.assert_called_once()
        self.witness.close.assert_called_once()

    def test_repeated_finish_does_not_repeat_retention_or_observation(self):
        with self.scope() as (_watcher, finish, _record):
            finish()
            finish()
        self.witness.start.assert_called_once()
        self.witness.stop.assert_called_once()
        self.witness.retain_intermediate.assert_called_once()
        self.witness.diagnostic.assert_called_once()
        self.witness.close.assert_called_once()

    def test_oversized_diagnostic_fails_with_error_custody_and_no_fallback(self):
        self.witness.diagnostic.return_value["oversized_fixture"] = "x" * (3*1024*1024)
        with self.assertRaisesRegex(runner.Film180G2Rejected, "diagnostic_budget"):
            with self.scope() as (_watcher, finish, _record):
                finish()
        self.assertFalse((self.evidence / "original-live-observer/observer-diagnostic.json").exists())
        self.assertIn("diagnostic_budget", json.dumps(self.error_document()))
        self.witness.diagnostic.assert_called_once()
        self.witness.close.assert_called_once()

    def test_existing_stage_custody_refuses_replay_before_constructing_another_observer(self):
        with self.scope() as (_watcher, finish, _record):
            finish()
        with self.assertRaises(FileExistsError):
            with self.scope():
                self.fail("existing stage must not issue another observation")
        self.constructor.assert_called_once()
        self.witness.start.assert_called_once()
        self.witness.close.assert_called_once()

    def test_constructor_error_is_retained_without_start_or_native_command(self):
        original = OSError("original process identity unavailable")
        self.constructor.side_effect = original
        body = mock.Mock()
        with self.assertRaises(OSError) as caught:
            with self.scope():
                body()
        self.assertIs(caught.exception, original)
        body.assert_not_called()
        self.witness.start.assert_not_called()
        self.witness.close.assert_not_called()
        self.assertIn("original process identity unavailable", json.dumps(self.error_document()))


if __name__ == "__main__":
    unittest.main()
