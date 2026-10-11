"""Adversarial, lightweight conformance tests; not substitutes for real PNG CI."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import struct
import tempfile
import zlib
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "native_direct_mediated_parity.py"
# Allow the actual production script's pure sibling clock module when loaded
# under a dynamic test spec (the ordinary python tooling/... entrypoint already
# includes its source directory in sys.path).
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("native_direct_mediated_parity", MODULE)
assert spec and spec.loader
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def fixture_artifact(root: Path, size: int = 60, salt: bytes = b"same") -> Path:
    frame_root = root / "frames"
    frame_root.mkdir(parents=True)
    rows = []
    for index in range(size):
        body = parity.PNG_MAGIC + salt + index.to_bytes(2, "big")
        path = frame_root / f"{index:06}.png"
        path.write_bytes(body)
        rows.append({
            "file": f"frames/{index:06}.png",
            "index": index,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        })
    (root / "artifact-manifest.json").write_text(json.dumps({
        "plan": {
            "first_frame": 0, "end_frame_exclusive": size, "frame_count": size,
        },
        "frames": rows,
    }))
    return root


class SourceBoundComparisonTests(unittest.TestCase):
    def setUp(self):
        # Fast adversarial unit fixtures intentionally contain only PNG headers.
        # They exercise manifest/frame ordering and parity orchestration, not
        # decoded images; the separate real-FFmpeg test verifies that boundary.
        def synthetic_decoded(root, expected_frames=60):
            return [
                hashlib.sha256(
                    (root / "frames" / f"{index:06}.png").read_bytes()
                ).hexdigest()
                for index in range(expected_frames)
            ]
        mocked = patch.object(parity, "decoded_rgba_frame_hashes",
                              side_effect=synthetic_decoded)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_identical_direct_and_mediated_native_manifest_bytes_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mediated = fixture_artifact(root / "mediated")
            direct = fixture_artifact(root / "direct")
            result = parity.compare_native_pngs(mediated, direct)
            self.assertTrue(result["all_frames_decoded_rgba_pixel_identical"])
            self.assertTrue(result["png_container_byte_identical"])
            self.assertEqual(result["png_container_mismatch_count"], 0)
            self.assertEqual(result["frame_count"], 60)
            self.assertEqual(result["checked_frames"], [0, 15, 30, 59])
            self.assertEqual(len(result["checked_frame_rgba_sha256"]), 4)
            self.assertEqual(len(result["ordered_rgba_frame_digests_sha256"]), 64)
            self.assertNotIn(str(root), str(result))

    def test_metadata_only_difference_does_not_synthesize_pixel_difference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mediated = fixture_artifact(root / "mediated")
            direct = fixture_artifact(root / "direct", salt=b"other")
            # The lower-level test models a verified *decoded* same-pixel
            # result despite container-byte differences. It cannot override
            # the real decoder used in CI.
            with patch.object(
                parity, "decoded_rgba_frame_hashes",
                return_value=["a" * 64] * 60,
            ):
                result = parity.compare_native_pngs(mediated, direct)
            self.assertTrue(result["all_frames_decoded_rgba_pixel_identical"])
            self.assertFalse(result["png_container_byte_identical"])
            self.assertEqual(result["png_container_mismatch_count"], 60)

    def test_forged_second_render_output_never_passes_as_pixel_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mediated = fixture_artifact(root / "mediated")
            direct = fixture_artifact(root / "direct", salt=b"other")
            with self.assertRaisesRegex(parity.DirectParityError, "pixels differ"):
                parity.compare_native_pngs(mediated, direct)

    def test_swapped_frames_rejected_even_with_recomputed_manifest_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mediated = fixture_artifact(root / "mediated")
            direct = fixture_artifact(root / "direct")
            manifest = direct / "artifact-manifest.json"
            doc = json.loads(manifest.read_text())
            a = direct / "frames" / "000014.png"
            b = direct / "frames" / "000015.png"
            first, second = a.read_bytes(), b.read_bytes()
            a.write_bytes(second)
            b.write_bytes(first)
            doc["frames"][14]["sha256"] = parity.sha256_path(a)
            doc["frames"][15]["sha256"] = parity.sha256_path(b)
            manifest.write_text(json.dumps(doc))
            with self.assertRaisesRegex(parity.DirectParityError, "pixels differ"):
                parity.compare_native_pngs(mediated, direct)

    def test_source_digest_and_frame_plan_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mediated = fixture_artifact(root / "mediated")
            direct = fixture_artifact(root / "direct")
            target = direct / "frames" / "000015.png"
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaises(parity.DirectParityError):
                parity.compare_native_pngs(mediated, direct)
            doc = json.loads((direct / "artifact-manifest.json").read_text())
            doc["plan"]["frame_count"] = 59
            (direct / "artifact-manifest.json").write_text(json.dumps(doc))
            with self.assertRaisesRegex(parity.DirectParityError, "timeline"):
                parity.native_png_hashes(direct)

    def test_unexpected_manifest_frame_path_or_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = fixture_artifact(root / "direct")
            target = direct / "frames" / "000000.png"
            target.unlink()
            target.symlink_to(root / "outside")
            (root / "outside").write_bytes(parity.PNG_MAGIC + b"private")
            with self.assertRaisesRegex(parity.DirectParityError, "PNG byte length or path"):
                parity.native_png_hashes(direct)
            target.unlink()
            target.write_bytes(parity.PNG_MAGIC + b"same\x00\x00")
            doc = json.loads((direct / "artifact-manifest.json").read_text())
            doc["frames"][0]["file"] = "frames/../another.png"
            (direct / "artifact-manifest.json").write_text(json.dumps(doc))
            with self.assertRaisesRegex(parity.DirectParityError, "index/path"):
                parity.native_png_hashes(direct)

    def test_direct_artifact_rejects_absolute_traversal_and_symlink_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("outside").mkdir()
            bad = ["../private", "/etc", "../../data", ""]
            for relative in bad:
                with self.assertRaises(parity.DirectParityError):
                    parity.checked_direct_artifact(root / "outside", {"directory": relative})
            output = root / "output"
            output.mkdir()
            (output / "symlink").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(parity.DirectParityError):
                parity.checked_direct_artifact(output, {"directory": "symlink"})
            self.assertEqual(
                parity.checked_direct_artifact(output, {"directory": "render-2"}),
                output / "render-2",
            )

    def test_non_github_environment_never_dispatches_direct_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "film.json"
            path.write_text("{}")
            with self.assertRaisesRegex(parity.DirectParityError, "disabled"):
                parity.run_direct_native_film(
                    film_file=path, executable=Path("semwright"),
                    socket=Path("broker"), session=Path("session"),
                    env={"GITHUB_ACTIONS": "false"},
                    mediated_job_ref="mediated:1",
                )

    def test_unobservable_direct_status_does_not_reissue_render_start(self):
        commands = []
        def invoked(**kwargs):
            command = kwargs["command"]
            commands.append(command)
            if command.endswith(".plan"):
                return {"plan_ref": "plan-ref"}
            if command.endswith(".apply"):
                return {"fingerprint": "a" * 64, "applied": True, "execution_status": "completed"}
            if command.endswith(".start"):
                return {"job_ref": "direct-native"}
            if command.endswith(".status"):
                raise parity.DirectParityError("Unavailable")
            self.fail("a status failure must not call more native operations")
        with tempfile.TemporaryDirectory() as tmp:
            film = Path(tmp) / "film.json"
            film.write_text("{}")
            with patch.object(parity, "_execute_direct", side_effect=invoked):
                with self.assertRaisesRegex(parity.DirectParityError, "UNKNOWN"):
                    parity.run_direct_native_film(
                        film_file=film, executable=Path("semwright"),
                        socket=Path("broker"), session=Path("session"),
                        env={"GITHUB_ACTIONS": "true"},
                        mediated_job_ref="mediated-native",
                    )
        self.assertEqual(sum(s.endswith(".render.start") for s in commands), 1)
        self.assertFalse(any(s.endswith(".render.result") for s in commands))
        self.assertFalse(any(s.endswith(".composition.verify") for s in commands))

    def test_second_job_must_be_distinct_and_native_verified(self):
        commands = []
        def invoked(**kwargs):
            command = kwargs["command"]
            commands.append(command)
            if command.endswith(".plan"):
                return {"plan_ref": "direct-plan"}
            if command.endswith(".apply"):
                return {"applied": True, "execution_status": "completed", "fingerprint": "b" * 64}
            if command.endswith(".start"):
                return {"job_ref": "direct-native"}
            if command.endswith(".status"):
                return {"job_ref": "direct-native", "state": "succeeded", "artifact": {"frame_count": 60}}
            if command.endswith(".result"):
                return {"job_ref": "direct-native", "state": "succeeded", "artifact": {"frame_count": 60, "directory": "direct-1"}}
            if command.endswith(".verify"):
                return {"report": {"execution_status": "completed", "support_level": "native"},
                        "measurement": {"findings": [], "validation": {"checks": [{"verdict": "PASS"}]}}}
            self.fail("unsupported direct test command")
        with tempfile.TemporaryDirectory() as tmp:
            film = Path(tmp) / "film.json"
            film.write_text("{}")
            with patch.object(parity, "_execute_direct", side_effect=invoked):
                result = parity.run_direct_native_film(
                    film_file=film, executable=Path("semwright"),
                    socket=Path("broker"), session=Path("session"),
                    env={"GITHUB_ACTIONS": "true"},
                    mediated_job_ref="mediated-native",
                )
                self.assertEqual(result["native_validation_checks"], 1)
                self.assertTrue(result["independent_job_confirmed"])
                # New measured phases must observe only the six original,
                # allowed direct commands and the existing terminal wait.
                subtimings = result["measured_direct_substages_micros"]
                self.assertEqual(
                    set(subtimings),
                    {"plan", "apply", "start", "terminal_wait", "result", "verify"},
                )
                self.assertTrue(all(type(v) is int and v >= 0 for v in subtimings.values()))
                self.assertGreaterEqual(
                    result["measured_direct_total_micros"] + len(subtimings),
                    sum(subtimings.values()),
                )
                with self.assertRaisesRegex(parity.DirectParityError, "reused"):
                    parity.run_direct_native_film(
                        film_file=film, executable=Path("semwright"),
                        socket=Path("broker"), session=Path("session"),
                        env={"GITHUB_ACTIONS": "true"},
                        mediated_job_ref="direct-native",
                    )
        self.assertEqual(sum(s.endswith(".render.start") for s in commands), 2)
        self.assertNotIn("direct-native", str(result))


def real_rgb_png(*, pixels: bytes, width: int = 2, height: int = 2, note: bytes = b"") -> bytes:
    if len(pixels) != width * height * 3:
        raise ValueError("PNG test fixture pixel count mismatch")
    def chunk(name: bytes, body: bytes) -> bytes:
        data = name + body
        return struct.pack(">I", len(body)) + data + struct.pack(">I", zlib.crc32(data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(
        b"\x00" + pixels[row * width * 3:(row + 1) * width * 3]
        for row in range(height)
    )
    ancillary = chunk(b"tEXt", b"comment\x00" + note) if note else b""
    return (
        parity.PNG_MAGIC + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(scanlines))
        + ancillary + chunk(b"IEND", b"")
    )


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available for tiny real RGBA decoder test")
class RealDecodedFramehashTests(unittest.TestCase):
    def test_real_png_metadata_differences_disappear_in_decoded_rgba(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            red = bytes([255, 0, 0] * 4)
            (frames / "000000.png").write_bytes(real_rgb_png(pixels=red, note=b"one"))
            (frames / "000001.png").write_bytes(real_rgb_png(pixels=red, note=b"two"))
            a, b = parity.decoded_rgba_frame_hashes(
                root, expected_frames=2, width=2, height=2,
            )
            self.assertEqual(a, b, "container metadata must not change decoded pixels")
            self.assertEqual(len(a), 64)

    def test_real_png_changed_visible_pixel_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            red = bytes([255, 0, 0] * 4)
            other = bytes([0, 255, 0] + [255, 0, 0] * 3)
            (frames / "000000.png").write_bytes(real_rgb_png(pixels=red))
            (frames / "000001.png").write_bytes(real_rgb_png(pixels=other))
            a, b = parity.decoded_rgba_frame_hashes(
                root, expected_frames=2, width=2, height=2,
            )
            self.assertNotEqual(a, b, "even one changed visible pixel must change the digest")


if __name__ == "__main__":
    unittest.main()
