"""Synthetic custody regressions; no FFmpeg, AAC encoding, renderer or native proof."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_aac_clock_custody as custody
if __package__:
    from .test_native_aac_clock_inspection import fixture
else:
    from test_native_aac_clock_inspection import fixture


def timeline():
    identity = dict(project_resource="project:00000000-0000-0000-0000-000000000001",
                    generation="00000000-0000-0000-0000-000000000002",
                    revision=1, deliverable_id="00000000-0000-0000-0000-000000000003")
    segments = []
    for index, start, count in ((0, 0, 32), (1, 32, 1)):
        segments.append(dict(segment_id="segment-" + str(index),
            scene_ids=["00000000-0000-0000-0000-000000000004"],
            output_start_frame=start, frame_count=count, source_manifest_sha256="11" * 32,
            artifact_path="segment-" + str(index) + ".mkv", artifact_sha256="22" * 32,
            artifact_bytes=10))
    source = dict(**identity, input_sha256="33" * 32, fps_num=30, fps_den=1,
                  total_frames=33, verified_video_segments=segments)
    return dict(**identity, recipe_sha256="44" * 32, frame_count=33,
        native_frame_count_observed=33, transport_pcm_audio=False,
        native_render_profile="lossless-video-only", artifact_path="timeline.mkv",
        artifact_sha256="55" * 32, artifact_bytes=20, source=source)


class ClockCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.sample = fixture()
        self.paths = {}
        for key in ("original_wav", "master", "packets", "decoded_pcm"):
            self.paths[key] = self.root / key
            self.paths[key].write_bytes(self.sample[key])
        self.ffprobe = self.root / "synthetic-ffprobe-NOT-EXECUTABLE"
        self.ffprobe.write_bytes(b"SYNTHETIC_TOOL_IDENTITY_ONLY")
        self.source = self.root / "timeline.json"
        self.source.write_bytes(custody.packed(timeline()))
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, argv, *, env, timeout, capture):
        self.calls.append(list(argv))
        mode = "manual" if "+skip_manual" in argv else "default"
        stdout = self.sample[mode + "_frames"]
        capture(dict(argv=argv, timeout_seconds=timeout, stdout=stdout, stderr=b"",
                     exit_code=0, deadline_exceeded=False, error_class=None,
                     error=None, automatic_retry=False))
        label = "original-aac-" + mode + "-frames"
        self.assertEqual((self.evidence / (label + ".stdout")).read_bytes(), stdout)
        self.assertTrue((self.evidence / (label + ".terminal.json")).is_file())
        return json.loads(stdout)

    def capture(self, runner=None):
        return custody.retain_clock_diagnostic(
            evidence_dir=self.evidence, phase="original", scenario="repaired-g2",
            master=self.paths["master"], master_sha256=custody.core.sha(self.sample["master"]),
            ffprobe=self.ffprobe, env={}, source_document=self.source,
            original_wav=self.paths["original_wav"], packets=self.paths["packets"],
            decoded_pcm=self.paths["decoded_pcm"], runner=runner or self.runner)

    def verify(self, value):
        return custody.verify_selected_carrier(
            value, phase="original", scenario="repaired-g2", source_document=timeline(),
            source_identity=custody.desc(self.source.read_bytes()),
            original_wav=self.sample["original_wav"], master=self.sample["master"],
            decoded_pcm=self.sample["decoded_pcm"],
            packet_identity=custody.desc(self.sample["packets"]))

    def test_two_distinct_frame_modes_preserve_same_existing_originals(self):
        before = {key: path.read_bytes() for key, path in self.paths.items()}
        value = self.capture()
        self.assertEqual(value["state"], "CAPTURED")
        self.assertEqual(value["report_status"], "CONSISTENT_WITHIN_SCOPE")
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("+skip_manual", self.calls[0])
        self.assertIn("+skip_manual", self.calls[1])
        self.assertTrue(all("-show_frames" in call and "-show_packets" not in call for call in self.calls))
        self.assertEqual({key: path.read_bytes() for key, path in self.paths.items()}, before)
        self.assertFalse(self.verify(value)["physical_AV_master_acceptance"])
        self.assertFalse(value["packet_probe_reinvoked"])
        self.assertFalse(value["pcm_decoder_reinvoked"])

    def test_invalid_frame_json_and_terminal_are_retained_before_parse_failure(self):
        self.sample["default_frames"] = b'{"frames":BROKEN'
        value = self.capture()
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertEqual(value["report_status"], "NOT_RUN")
        self.assertEqual(custody.decoded(value["captures"][0]["original_stdout"], custody.MAX_PROBE),
                         b'{"frames":BROKEN')
        self.assertEqual(value["captures"][0]["terminal"]["exit_code"], 0)
        self.assertTrue(value["captures"][0]["runner_failed"])
        self.assertEqual(len(self.calls), 2)

    def test_timeout_original_is_not_retried_or_minted_as_terminal_success(self):
        def timeout(argv, **kwargs):
            if "+skip_manual" in argv:
                return self.runner(argv, **kwargs)
            self.calls.append(list(argv))
            kwargs["capture"](dict(argv=argv, timeout_seconds=60, stdout=b"PARTIAL",
                stderr=b"timeout", exit_code=None, deadline_exceeded=True,
                error_class="TimeoutExpired", error="private", automatic_retry=False))
            raise TimeoutError("synthetic timeout")
        value = self.capture(timeout)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertIsNone(value["captures"][0]["terminal"]["exit_code"])
        self.assertTrue(value["captures"][0]["terminal"]["deadline_exceeded"])
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(FileExistsError):
            self.capture(timeout)
        self.assertEqual(len(self.calls), 2)

    def test_nonzero_exit_with_valid_json_is_not_qualified(self):
        def nonzero(argv, **kwargs):
            self.calls.append(list(argv))
            mode = "manual" if "+skip_manual" in argv else "default"
            kwargs["capture"](dict(argv=argv, timeout_seconds=60,
                stdout=self.sample[mode + "_frames"], stderr=b"error", exit_code=1,
                deadline_exceeded=False, error_class=None, error=None, automatic_retry=False))
            raise ValueError("synthetic nonzero")
        value = self.capture(nonzero)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertIsNone(value["report"])
        with self.assertRaises(ValueError):
            self.verify(value)

    def test_missing_pcm_does_not_start_a_replacement_decoder(self):
        self.paths["decoded_pcm"].unlink()
        value = self.capture()
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertIsNone(value["decoded_pcm"])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(value["pcm_decoder_reinvoked"])
        self.assertFalse(self.paths["decoded_pcm"].exists())

    def test_missing_packets_does_not_start_a_replacement_packet_probe(self):
        self.paths["packets"].unlink()
        value = self.capture()
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertIsNone(value["packets"])
        self.assertTrue(all("-show_packets" not in argv for argv in self.calls))
        self.assertFalse(self.paths["packets"].exists())

    def test_changed_master_is_retained_as_incomplete(self):
        def changed(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            self.paths["master"].write_bytes(b"CHANGED_SYNTHETIC_MASTER")
            return result
        value = self.capture(changed)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertFalse(value["master_unchanged_after_observation"])

    def test_changed_probe_identity_is_not_qualified(self):
        def changed(argv, **kwargs):
            result = self.runner(argv, **kwargs)
            self.ffprobe.write_bytes(b"CHANGED_SYNTHETIC_TOOL")
            return result
        value = self.capture(changed)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertFalse(value["ffprobe_unchanged_after_observation"])

    def test_unobserved_terminal_is_not_inferred_from_runner_return(self):
        def no_callback(argv, **kwargs):
            self.calls.append(list(argv))
            return {}
        value = self.capture(no_callback)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertTrue(all(row["terminal"] is None for row in value["captures"]))

    def test_oversized_stdout_is_not_truncated_into_an_original(self):
        def oversized(argv, **kwargs):
            self.calls.append(list(argv))
            kwargs["capture"](dict(argv=argv, timeout_seconds=60,
                stdout=b"x" * (custody.MAX_PROBE + 1), stderr=b"", exit_code=0,
                deadline_exceeded=False, error_class=None, error=None, automatic_retry=False))
            return {}
        value = self.capture(oversized)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertEqual(value["captures"][0]["terminal"]["stdout"]["bytes"], custody.MAX_PROBE + 1)
        self.assertIsNone(value["captures"][0]["original_stdout"])
        self.assertFalse((self.evidence / "original-aac-default-frames.stdout").exists())

    def test_wrong_packet_bytes_cannot_borrow_selected_packet_identity(self):
        value = self.capture()
        packet = custody.decoded(value["packets"], custody.MAX_PROBE) + b" "
        value["packets"] = custody.encoded(packet, custody.MAX_PROBE)
        with self.assertRaisesRegex(ValueError, "clock-packet-original-not-the-selected-probe"):
            self.verify(value)

    def test_swapped_mode_records_are_rejected(self):
        value = self.capture()
        value["captures"].reverse()
        with self.assertRaisesRegex(ValueError, "clock-mode-order-or-duplicate"):
            self.verify(value)

    def test_manual_document_with_default_argv_is_rejected(self):
        value = self.capture()
        value["captures"][1]["terminal"]["argv"] = list(value["captures"][0]["terminal"]["argv"])
        with self.assertRaisesRegex(ValueError, "clock-decode-mode-argv-changed"):
            self.verify(value)

    def test_forged_report_digest_and_bytes_do_not_replace_recomputation(self):
        value = self.capture()
        report = custody.core.strict(custody.decoded(value["report"], custody.MAX_REPORT))
        report["source_sample_frames"] += 1
        value["report"] = custody.encoded(custody.packed(report), custody.MAX_REPORT)
        with self.assertRaisesRegex(ValueError, "clock-inspection-does-not-match-originals"):
            self.verify(value)

    def test_claimed_media_acceptance_is_rejected_by_selected_policy(self):
        value = self.capture()
        value["physical_AV_master_acceptance"] = True
        with self.assertRaises(ValueError):
            self.verify(value)

    def test_timeline_anchor_is_not_relabelled_authorial_project(self):
        value = self.capture()
        self.assertEqual(value["anchor"]["kind"], "NATIVE_VERIFIED_OUTPUT_TIMELINE")
        self.assertFalse(value["anchor"]["authorial_project_read_by_capture"])
        value["anchor"]["kind"] = "SAVED_AUTHORIAL_PROJECT"
        value["anchor"]["authorial_project_read_by_capture"] = True
        with self.assertRaisesRegex(ValueError, "clock-selected-source-anchor-changed"):
            self.verify(value)

    def test_source_document_identity_is_bound_without_reserializing_it(self):
        value = self.capture()
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "clock-selected-source-anchor-changed"):
            self.verify(value)

    def test_film_anchor_comes_from_the_actual_saved_project_contract(self):
        from native_film180_evidence_selection import PROJECT_TEMPLATE
        project = copy.deepcopy(PROJECT_TEMPLATE)
        anchor = custody.derive_anchor(project, custody.desc(custody.packed(project)),
                                       scenario="film180-g2", phase="original")
        self.assertEqual(anchor["kind"], "SAVED_AUTHORIAL_PROJECT")
        self.assertTrue(anchor["authorial_project_read_by_capture"])
        self.assertEqual(anchor["presentation_start"], {"num": 0, "den": 1})
        project["scenes"][0]["start"]["num"] = "1"
        with self.assertRaises(ValueError):
            custody.derive_anchor(project, custody.desc(custody.packed(project)),
                                  scenario="film180-g2", phase="original")

    def test_selected_role_cannot_be_omitted_in_either_existing_scenario(self):
        from native_selection_test_fixtures import World
        for scenario in ("repaired-g2", "film180-g2"):
            with self.subTest(scenario=scenario):
                world = World(scenario)
                del world.files[("observation", "original-aac-clock-capture.json")]
                with world.patched():
                    raw, manifest = world.capture()
                    self.assertFalse(manifest["required_layers_complete"])
                    with self.assertRaises(ValueError):
                        world.verify(raw)

    def test_selected_originals_recompute_carrier_without_new_capture(self):
        from unittest import mock
        import native_aac_clock_inspection as inspector
        from native_selection_test_fixtures import World, _synthetic_clock_report_bytes
        for scenario in ("repaired-g2", "film180-g2"):
            with self.subTest(scenario=scenario):
                phases = ("original", "edited") if scenario == "film180-g2" else ("original",)
                _synthetic_clock_report_bytes.cache_clear()
                try:
                    world = World(scenario)
                    duplicate = World(scenario)
                    before_verify = _synthetic_clock_report_bytes.cache_info()
                    self.assertEqual(before_verify.maxsize, 8)
                    self.assertEqual(before_verify.misses, len(phases))
                    self.assertEqual(before_verify.hits, len(phases))
                    self.assertEqual(before_verify.currsize, len(phases))
                    for phase in phases:
                        key = ("observation", phase + "-aac-clock-capture.json")
                        self.assertEqual(world.files[key], duplicate.files[key])
                    with mock.patch.object(inspector, "inspect_originals",
                                           wraps=inspector.inspect_originals) as recomputed:
                        with world.patched():
                            raw, manifest = world.capture()
                            value = world.verify(raw)
                    self.assertEqual(recomputed.call_count, len(phases))
                    self.assertEqual(_synthetic_clock_report_bytes.cache_info(), before_verify)
                    rows = [row for row in manifest["members"] if row["role"] == "audio-clock-carrier"]
                    self.assertEqual(len(rows), len(phases))
                    self.assertTrue(all(row["kind"] == "json-original" and row["original_bytes"]
                                        for row in rows))
                    self.assertFalse(value["physical_acceptance"])
                    self.assertFalse(value["packet_PTS_and_AAC_priming_verified"])
                finally:
                    _synthetic_clock_report_bytes.cache_clear()

    def test_non_integer_terminal_cannot_be_reconstructed_as_success(self):
        def aliased(argv, **kwargs):
            self.calls.append(list(argv))
            mode = "manual" if "+skip_manual" in argv else "default"
            code = 0.0 if mode == "manual" else False
            kwargs["capture"](dict(argv=argv, timeout_seconds=60,
                stdout=self.sample[mode + "_frames"], stderr=b"", exit_code=code,
                deadline_exceeded=False, error_class=None, error=None, automatic_retry=False))
            return {}
        value = self.capture(aliased)
        self.assertEqual(value["state"], "INCOMPLETE")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(row["terminal"] is None and row["runner_failed"] is True
                            for row in value["captures"]))
        self.assertIsNone(value["report"])

    def test_report_boolean_and_float_aliases_fail_exact_recomputation(self):
        original = self.capture()
        for field, replacement in (("native_execution_attested", 0),
                                   ("source_sample_frames", float(len(self.sample["decoded_pcm"]) // 4))):
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                report = custody.core.strict(custody.decoded(value["report"], custody.MAX_REPORT))
                prior = report[field]
                self.assertEqual(prior, replacement)
                self.assertIsNot(type(prior), type(replacement))
                report[field] = replacement
                value["report"] = custody.encoded(custody.packed(report), custody.MAX_REPORT)
                with self.assertRaisesRegex(ValueError, "clock-inspection-does-not-match-originals"):
                    self.verify(value)


if __name__ == "__main__":
    unittest.main()
