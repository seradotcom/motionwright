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

    def test_source_binding_is_closed_to_new_operation_stage_names(self):
        self.assertEqual(runner.ORIGINAL_AUDIO_OWNER_NAME, "firstparty-film180-source.wav")
        self.assertEqual(runner.STAGE_TIMEOUT, 1500)
        self.assertEqual(runner.TRUSTED_MLT.as_posix(),
                         "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")


if __name__ == "__main__":
    unittest.main()
