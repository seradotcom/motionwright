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
    def test_never_automatically_authorizes_native_effects_on_windows(self):
        if sys.platform == "linux":
            self.skipTest("Linux GHA requires a separate permission-negative run")
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

    def test_source_binding_is_closed_to_new_operation_stage_names(self):
        self.assertEqual(runner.ORIGINAL_AUDIO_OWNER_NAME, "firstparty-film180-source.wav")
        self.assertEqual(runner.STAGE_TIMEOUT, 1500)
        self.assertEqual(runner.TRUSTED_MLT.as_posix(),
                         "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so")


if __name__ == "__main__":
    unittest.main()
