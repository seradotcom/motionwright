"""Synthetic metadata-only tests for the CI repeated-native-render receipt reducer.

These fixtures are NOT evidence of a real frame, real Broker, or user acceptance.
Only the actual existing native-render-e2e job can produce release-gate input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_provider_repeat import (  # noqa: E402
    RepeatEvidenceError, compare_runs, read_json,
)

SOURCE = "1" * 40
SEMWRIGHT = "2" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, sort_keys=True) + "\n", encoding="utf-8")


def fixture(folder: Path, index: int, pixels_digest: str = "4" * 64) -> None:
    folder.mkdir()
    first, middle, last = [f"fake unit test pixels {index}-{n}".encode()
                           for n in ("first", "middle", "last")]
    for name, data in (("frame-first.png", first), ("frame-middle.png", middle),
                       ("frame-last.png", last)):
        (folder / name).write_bytes(data)
    manifest = b'{"unit_only_not_real_media":true}\n'
    (folder / "artifact-manifest.json").write_bytes(manifest)
    film_bytes = b'{"unit_only":true,"native_film":false}\n'
    (folder / "motionwright-direct-parity-film.json").write_bytes(film_bytes)
    film_digest = sha(film_bytes)
    result = {
        "native_render_e2e": "PASS", "motionwright_sha": SOURCE,
        "semwright_sha": SEMWRIGHT,
        "frame_count": 60, "direct_mediated_native_frame_parity": "PASS",
        "linear_position_motion_sampled": True, "linear_opacity_motion_sampled": True,
        "direct_mediated_same_film_sha256": film_digest,
        "project": {
            "project_id": f"synthetic-project-{index}",
            "generation": f"synthetic-generation-{index}", "revision": 1,
        },
        "driver_sha256": "a" * 64, "node_sha256": "b" * 64,
        "artifact_manifest_sha256": sha(manifest),
        "review_frames": {
            "first_sha256": sha(first), "middle_sha256": sha(middle),
            "last_sha256": sha(last),
        },
    }
    parity = {
        "schema": "motionwright-native-direct-mediated-parity/1",
        "motionwright_sha": SOURCE, "semwright_sha": SEMWRIGHT,
        "source_film_sha256": film_digest,
        "direct_job_distinct_from_mediated": True,
        "human_visual_acceptance": "NOT_RUN",
        "arbitrary_renderer_property_parity": "NOT_PROVEN",
        "pixels": {
            "all_frames_decoded_rgba_pixel_identical": True,
            "frame_count": 60,
            "raw_rgba_pixel_format": "rgba_1920x1080",
            "checked_frames": [0, 15, 30, 59],
            "ordered_rgba_frame_digests_sha256": pixels_digest,
        },
    }
    stages = {
        "schema": "motionwright.native-render-stage-cost/1",
        "source_sha": SOURCE, "semwright_sha": SEMWRIGHT,
        "canonical_film_sha256": film_digest,
        "verified_frames_per_leg": 60,
        "verified_rgba_frames_across_both_legs": 120,
        "ordered_rgba_frame_digests_sha256": pixels_digest,
        "comparison": {"saved_native_renderer_frames": None, "cache_savings": None},
    }
    health = {
        "schema": "motionwright-ci-provider-lifecycle/1",
        "authority": "passive-ci-process-status-not-driver-evidence",
        "sample_count": 2, "daemon_exit_before_owner_cleanup": None,
        "cgroup_oom_kills_delta": 0,
    }
    for name, data in (
        ("result.json", result), ("native-direct-mediated-parity.json", parity),
        ("native-render-stage-cost.json", stages),
        ("native-provider-lifecycle.json", health),
    ):
        write(folder / name, data)


class NativeRepeatEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.one, self.two = self.base / "one", self.base / "two"
        fixture(self.one, 1)
        fixture(self.two, 2)

    def assert_rejected(self, reason: str) -> None:
        with self.assertRaisesRegex(RepeatEvidenceError, reason):
            compare_runs(self.one, self.two, SOURCE, SEMWRIGHT)

    def test_synthetic_contract_proves_no_human_or_release_claims(self) -> None:
        data = compare_runs(self.one, self.two, SOURCE, SEMWRIGHT)
        self.assertEqual(data["status"], "TWO_INDEPENDENT_NATIVE_E2E_RUNS_VERIFIED")
        self.assertEqual(data["cross_instance_rgba_claim"], "SAME_FILM_PIXEL_IDENTICAL")
        self.assertEqual(data["intermittent_driver_host_loss_fixed"], "NOT_PROVEN")
        self.assertEqual(data["automatic_retry_after_unknown_observed"], "NOT_EXERCISED")
        self.assertFalse(data["motionwright_v01_release_ready"])
        self.assertEqual(data["independent_product_acceptance"], "NOT_RUN")

    def test_source_pin_swap_is_rejected(self) -> None:
        with self.assertRaisesRegex(RepeatEvidenceError, "invalid_source_lock"):
            compare_runs(self.one, self.two, "invalid-sha", SEMWRIGHT)
        with self.assertRaisesRegex(RepeatEvidenceError, "native_run_not_source_verified"):
            compare_runs(self.one, self.two, "5" * 40, SEMWRIGHT)

    def test_provider_binary_swap_is_rejected(self) -> None:
        data = read_json(self.two / "result.json")
        data["driver_sha256"] = "d" * 64
        write(self.two / "result.json", data)
        self.assert_rejected("provider_source_changed_between_runs")

    def test_same_owner_identity_cannot_claim_repeated_independent_provider(self) -> None:
        data = read_json(self.two / "result.json")
        data["project"]["generation"] = "synthetic-generation-1"
        write(self.two / "result.json", data)
        self.assert_rejected("owner_sessions_not_independent")

    def test_swapped_manifest_bytes_fail(self) -> None:
        (self.two / "artifact-manifest.json").write_text("tampered")
        self.assert_rejected("native_manifest_bytes_mismatch")

    def test_film_bytes_tamper_does_not_pass_source_digest_claim(self) -> None:
        (self.two / "motionwright-direct-parity-film.json").write_bytes(b"tampered")
        self.assert_rejected("source_film_bytes_mismatch")

    def test_oom_kill_observation_rejects_repeat_claim(self) -> None:
        p = self.one / "native-provider-lifecycle.json"
        data = read_json(p)
        data["cgroup_oom_kills_delta"] = 1
        write(p, data)
        self.assert_rejected("native_provider_lifecycle_unconfirmed")

    def test_forged_media_result_fails_even_if_claim_pass_remains(self) -> None:
        (self.two / "frame-last.png").write_bytes(b"tamper-frame")
        self.assert_rejected("native_review_frame_bytes_mismatch")

    def test_missing_lifecycle_never_assumes_provider_survived(self) -> None:
        (self.one / "native-provider-lifecycle.json").unlink()
        self.assert_rejected("incomplete_native_evidence")

    def test_observed_daemon_exit_and_oom_never_pass(self) -> None:
        p = self.one / "native-provider-lifecycle.json"
        data = read_json(p)
        data["daemon_exit_before_owner_cleanup"] = 137
        write(p, data)
        self.assert_rejected("native_provider_lifecycle_unconfirmed")

    def test_same_film_different_pixels_is_adversarial(self) -> None:
        data = read_json(self.two / "native-direct-mediated-parity.json")
        data["pixels"]["ordered_rgba_frame_digests_sha256"] = "5" * 64
        write(self.two / "native-direct-mediated-parity.json", data)
        cost = read_json(self.two / "native-render-stage-cost.json")
        cost["ordered_rgba_frame_digests_sha256"] = "5" * 64
        write(self.two / "native-render-stage-cost.json", cost)
        self.assert_rejected("identical_film_different_decoded_rgba")

    def test_different_film_is_not_falsely_claimed_identical(self) -> None:
        alternative_film = b'{"unit_only":true,"native_film":false,"variant":2}\n'
        (self.two / "motionwright-direct-parity-film.json").write_bytes(alternative_film)
        different_digest = sha(alternative_film)
        result = read_json(self.two / "result.json")
        result["direct_mediated_same_film_sha256"] = different_digest
        write(self.two / "result.json", result)
        parity = read_json(self.two / "native-direct-mediated-parity.json")
        parity["source_film_sha256"] = different_digest
        write(self.two / "native-direct-mediated-parity.json", parity)
        stage = read_json(self.two / "native-render-stage-cost.json")
        stage["canonical_film_sha256"] = different_digest
        write(self.two / "native-render-stage-cost.json", stage)
        report = compare_runs(self.one, self.two, SOURCE, SEMWRIGHT)
        self.assertEqual(report["cross_instance_rgba_claim"],
                         "DIFFERENT_SOURCE_FILM_INSTANCES_NOT_COMPARABLE")

    def test_duplicate_json_field_rejected(self) -> None:
        (self.two / "result.json").write_text('{"schema":1,"schema":2}\n')
        self.assert_rejected("duplicate_json_field")

    def test_output_directory_is_not_reused(self) -> None:
        with self.assertRaisesRegex(RepeatEvidenceError, "repeat_source_reused"):
            compare_runs(self.one, self.one, SOURCE, SEMWRIGHT)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlink API unavailable")
    def test_symlinked_receipt_rejected(self) -> None:
        original = self.two / "result.json"
        target = self.two / "real-result.json"
        original.rename(target)
        try:
            original.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("no symlink privilege")
        self.assert_rejected("incomplete_native_evidence")


if __name__ == "__main__":
    unittest.main()
