"""Focused G0 SDK5c2 wire/custody contracts over synthetic bytes.

The literal process shape was compared with SDK5c2 runtime.rs; no Rust
serializer, renderer, Host, codec, namespace or live /proc witness runs here.
Physical Linux validation of the owned-work witness is a separate pending gate.
"""
import copy
import json
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_evidence_projection as core
import native_mlt_evidence_selection as mlt
import native_mlt_g2_observation as observation
import native_mlt_loaded_module as loaded
import test_native_mlt_evidence_selection as historical


def world():
    return historical.Ffv1DiagnosticWorld(historical.Ffv1OriginalSelectionTests().plan())


def process_original(plan, *, stage="encode", stdout=b"", stderr=b"\xfforiginal-error", complete=False):
    """Literal SDK5c2 serde_json shape, with synthetic buffers and terminal."""
    args = plan["slots"][0]["args"]
    output = args["output_path"]
    limit = {"zscale-help": 262144, "complete-probe": 8 * 1024 * 1024}.get(stage, 16384)
    def stream(name, body):
        return dict(root="output", path=output + ".ffv1-" + stage + "." + name + ".original.bin",
            sha256=core.sha(body), bytes=len(body), returned_buffer_bytes=len(body),
            returned_buffer_sha256=core.sha(body), diagnostic_prefix_truncated=False,
            retention_limit=limit, retained=True, upstream_stream_eof_attested=False)
    def pipe(body):
        return dict(observed_bytes=len(body), retained_bytes=len(body), retained_sha256=core.sha(body),
            eof_observed=complete, read_error=None, reader_panicked=False, drain_stopped=False,
            truncated=False, complete=complete)
    same_spawn = dict(spawn_attempted=True, pid=456, terminal_observed=True, exit_code=17,
        term_signal=None, cancelled=False, timed_out=False, output_exceeded=False,
        drain_stop_reason=None, stdout=pipe(stdout), stderr=pipe(stderr),
        supervisor_error=None, capture_complete=complete, native_execution_retried=False)
    return dict(schema="semwright.ffv1-process-original/1", stage=stage, role="ffv1",
        member_prefix="ffv1", output_path=output,
        source_manifest_sha256=args["expected_manifest_sha256"],
        source_sha256=args["expected_manifest_sha256"], source_kind="motion-frame-manifest-bytes",
        source_capture_retained_limit=limit, source_capture_total_limit=max(limit, 262144),
        diagnostic_prefix_limit=limit,
        spec=dict(encoding="unix-os-bytes-hex", executable=b"/synthetic/ffmpeg".hex(),
            argv=[b"-version".hex()], cwd=b"/synthetic/output".hex(), timeout_millis=1000,
            cpu_seconds=30, address_space_bytes=536870912, environment_included=False,
            native_execution_retried=False),
        terminal=dict(process_result_received=True, exit_code=17, term_signal=None, cancelled=False,
            timed_out=False, output_exceeded=False, stdout=stream("stdout", stdout),
            stderr=stream("stderr", stderr), original_error=None),
        same_spawn_observation=same_spawn, file_size_limit_bytes=512 * 1024 * 1024,
        native_execution_retried=False, stream_eof_attested=False,
        capture_scope="same-spawn-retained-buffers-before-checked-or-parse", physical_acceptance=False)


def row_for(manifest, path):
    return next(row for row in manifest["members"] if row["relative_path"] == path)


class Sdk5c2OriginalContractsTests(unittest.TestCase):
    def test_literal_process_and_custody_bytes_survive_selector_without_native_claims(self):
        sample_world = world()
        value = process_original(sample_world.plan)
        process_path = "output/" + value["output_path"] + ".ffv1-encode.terminal.original.json"
        source_bytes = json.dumps(value, sort_keys=False, separators=(",", ":")).encode() + b"\r\n"
        sample_world.put("owner", process_path, source_bytes)
        for name, body in (("stdout", b""), ("stderr", b"\xfforiginal-error")):
            sample_world.put("owner", "output/" + value["terminal"][name]["path"], body)
        _, custody_path, custody = historical.Ffv1OriginalSelectionTests().typed_original_documents(
            sample_world.plan)[3]
        custody["retention_errors"] = [dict(phase="stream-retention", code="BackendFailed",
            message="synthetic retention failed", outcome_known=False)]
        custody_bytes = json.dumps(custody, separators=(",", ":")).encode() + b"\r\n"
        sample_world.put("owner", custody_path, custody_bytes)
        with sample_world.patched():
            raw, manifest = sample_world.capture()
            sample_world.verify(raw)
        for path, original in ((process_path, source_bytes), (custody_path, custody_bytes)):
            row = row_for(manifest, path)
            self.assertEqual(row["state"], "INCLUDED")
            self.assertEqual(row["kind"], "diagnostic-json-original")
            self.assertEqual((row["retained_sha256"], row["retained_bytes"]), (core.sha(original), len(original)))
            self.assertIn(original, raw)
        self.assertIn(b"\xfforiginal-error", raw)
        self.assertFalse(manifest["physical_acceptance"])
        self.assertFalse(manifest["automatic_retry"])
        self.assertFalse(value["same_spawn_observation"]["capture_complete"])
        self.assertFalse(value["stream_eof_attested"])

    def test_closed_sdk_fields_reject_extras_and_missing_new_fields(self):
        plan = historical.Ffv1OriginalSelectionTests().plan()
        base = process_original(plan)
        paths = ((), ("spec",), ("terminal",), ("terminal", "stdout"),
                 ("same_spawn_observation",), ("same_spawn_observation", "stderr"))
        for path in paths:
            value = copy.deepcopy(base)
            target = value
            for key in path:
                target = target[key]
            target["unregistered"] = "UNSELECTED"
            with self.subTest(extra_at=path), self.assertRaises(core.SelectionError):
                mlt.checked_ffv1_process_original(value, plan)
        for key in ("role", "member_prefix", "source_sha256", "source_kind",
                    "same_spawn_observation", "file_size_limit_bytes"):
            value = copy.deepcopy(base)
            del value[key]
            with self.subTest(missing=key), self.assertRaises(core.SelectionError):
                mlt.checked_ffv1_process_original(value, plan)
        custody = historical.Ffv1OriginalSelectionTests().typed_original_documents(plan)[3][2]
        for key in ("retention_errors", "observation_boundary"):
            value = copy.deepcopy(custody)
            del value[key]
            with self.subTest(missing_custody=key), self.assertRaises(core.SelectionError):
                mlt.checked_ffv1_process_original(value, plan)

    def test_source_output_stream_and_same_spawn_links_reject_substitution(self):
        plan = historical.Ffv1OriginalSelectionTests().plan()
        changes = (
            lambda v: v.update(output_path=v["output_path"].replace("s00", "s01")),
            lambda v: v.update(source_sha256="f" * 64),
            lambda v: v.update(source_manifest_sha256="f" * 64),
            lambda v: v["terminal"]["stdout"].update(path=v["terminal"]["stderr"]["path"]),
            lambda v: v["terminal"]["stderr"].update(returned_buffer_bytes=1),
            lambda v: v["terminal"]["stderr"].update(returned_buffer_sha256="f" * 64),
            lambda v: v["same_spawn_observation"]["stderr"].update(retained_sha256="f" * 64),
            lambda v: v["same_spawn_observation"].update(exit_code=0),
        )
        for index, change in enumerate(changes):
            value = process_original(plan); change(value)
            with self.subTest(case=index), self.assertRaises(core.SelectionError):
                mlt.checked_ffv1_process_original(value, plan)

    def test_incomplete_pipe_is_preserved_but_forged_eof_or_completeness_is_rejected(self):
        plan = historical.Ffv1OriginalSelectionTests().plan()
        value = process_original(plan)
        pipe = value["same_spawn_observation"]["stderr"]
        pipe.update(observed_bytes=pipe["retained_bytes"] + 1, truncated=True,
                    read_error=dict(kind="Interrupted", raw_os_error=4, message="interrupted"))
        mlt.checked_ffv1_process_original(value, plan)
        self.assertFalse(value["same_spawn_observation"]["capture_complete"])
        for path, key, changed in (
                ((), "stream_eof_attested", True),
                ((), "native_execution_retried", True),
                ((), "physical_acceptance", True),
                (("terminal", "stderr"), "diagnostic_prefix_truncated", True),
                (("same_spawn_observation", "stderr"), "complete", True),
                (("same_spawn_observation", "stderr"), "truncated", False),
                (("same_spawn_observation",), "capture_complete", True)):
            bad = copy.deepcopy(value); target = bad
            for name in path: target = target[name]
            target[key] = changed
            with self.subTest(path=path, key=key), self.assertRaises(core.SelectionError):
                mlt.checked_ffv1_process_original(bad, plan)
        complete = process_original(plan, complete=True)
        mlt.checked_ffv1_process_original(complete, plan)
        self.assertFalse(complete["stream_eof_attested"])
        self.assertFalse(complete["physical_acceptance"])

    def test_per_stage_limits_match_source_without_changing_global_caps(self):
        sample_world = world()
        contract = sample_world.admission["diagnostic_contract"]
        maxima = [entry["maximum"] for entry in contract["entries"]]
        self.assertEqual(len(maxima), 54)
        self.assertEqual(sum(maxima), 38404096)
        self.assertEqual(sample_world.admission["capture_selected_maximum"], 38862848)
        self.assertEqual(maxima.count(8 * 1024 * 1024), 4)
        self.assertEqual((core.MAX_FILE, core.MAX_TOTAL, core.MAX_FILES),
                         (256 * 1024 * 1024, 512 * 1024 * 1024, 4096))
        self.assertEqual((core.DIAGNOSTIC_MAXIMUM["stdout"], core.DIAGNOSTIC_MAXIMUM["stderr"]),
                         (262144, 262144))
        for stage, expected in (("zscale-help", 262144), ("complete-probe", 8 * 1024 * 1024),
                                ("encode", 16384), ("media-probe", 16384),
                                ("decoder-version", 16384), ("decode", 16384)):
            with self.subTest(stage=stage):
                value = process_original(sample_world.plan, stage=stage)
                mlt.checked_ffv1_process_original(value, sample_world.plan)
                self.assertEqual(value["diagnostic_prefix_limit"], expected)
                value["diagnostic_prefix_limit"] = expected - 1
                with self.assertRaises(core.SelectionError):
                    mlt.checked_ffv1_process_original(value, sample_world.plan)

    def test_extended_limit_requires_exact_new_closed_context_and_four_members(self):
        sample_world = world()
        original = sample_world.admission["capture_diagnostic_contract"]
        core._checked_diagnostic_contract(original, original["context"])
        selected = next(i for i, e in enumerate(original["entries"]) if e["maximum"] == 8 * 1024 * 1024)
        alterations = (
            lambda c: c["context"].update(scenario="film180-g2"),
            lambda c: c["context"].update(semwright_sha="f" * 40),
            lambda c: c["entries"][selected].update(root="observation"),
            lambda c: c["entries"][selected].update(role="other-stdout"),
            lambda c: c["entries"][selected].update(phase="edit"),
            lambda c: c["entries"][selected].update(type="stderr"),
            lambda c: c["entries"][selected].update(maximum=8 * 1024 * 1024 + 1),
            lambda c: c["entries"][selected].update(relative_path=c["entries"][selected]["relative_path"].replace(
                "complete-probe", "encode")),
            lambda c: c["entries"].pop(),
        )
        for index, change in enumerate(alterations):
            bad = copy.deepcopy(original); change(bad)
            with self.subTest(case=index), self.assertRaises(core.SelectionError):
                core._checked_diagnostic_contract(bad, bad["context"])
        old = copy.deepcopy(original)
        for entry in old["entries"]:
            if entry["role"].startswith("ffv1-process-") and entry["type"] in ("stdout", "stderr"):
                entry["maximum"] = 16384
        with self.assertRaises(core.SelectionError):
            mlt.ffv1_extended_stream_limit(original["entries"][selected], old)
        bad = copy.deepcopy(sample_world.admission)
        bad["source_bindings"][0]["sha256"] = "e" * 64
        with self.assertRaises(core.SelectionError):
            mlt.checked_ffv1_admission(bad, current=historical.CURRENT,
                                      source_bindings=sample_world.bindings)

    def test_complete_probe_eight_mib_is_exact_original_and_one_extra_byte_fails(self):
        for size, included in ((8 * 1024 * 1024, True), (8 * 1024 * 1024 + 1, False)):
            sample_world = world()
            output = sample_world.plan["slots"][0]["args"]["output_path"]
            path = "output/" + output + ".ffv1-complete-probe.stdout.original.bin"
            original = b"\xff" + bytes(size - 1)
            sample_world.put("owner", path, original)
            with sample_world.patched():
                raw, manifest = sample_world.capture()
                if included: sample_world.verify(raw)
            row = row_for(manifest, path)
            with self.subTest(size=size):
                self.assertEqual(row["state"], "INCLUDED" if included else "CAPTURE_FAILED")
                if included:
                    self.assertEqual((row["retained_sha256"], row["retained_bytes"]), (core.sha(original), size))
                    self.assertIn(original, raw)
                else:
                    self.assertNotIn(original, raw)
                    self.assertFalse(row.get("original_bytes", False))
                self.assertFalse(manifest["physical_acceptance"])

    def test_zscale_limit_and_unrelated_stream_budgets_remain_closed(self):
        for stage, size, included in (("zscale-help", 262144, True), ("zscale-help", 262145, False),
                                      ("encode", 16385, False), ("media-probe", 262144, False)):
            sample_world = world()
            output = sample_world.plan["slots"][0]["args"]["output_path"]
            path = "output/" + output + ".ffv1-" + stage + ".stderr.original.bin"
            original = b"\xfe" * size
            sample_world.put("owner", path, original)
            with sample_world.patched():
                raw, manifest = sample_world.capture()
            with self.subTest(stage=stage, size=size):
                self.assertEqual(row_for(manifest, path)["state"], "INCLUDED" if included else "CAPTURE_FAILED")
                self.assertEqual(original in raw, included)

    def test_owned_output_cmdline_is_exact_and_keeps_legacy_path(self):
        owned = "mw-mlt-0123456789abcdef0123-master.mp4"
        cwd = "/workspace/output/" + owned + ".raw-original-work"
        self.assertEqual(observation.host_owned_output(cwd), owned)
        for consumer in ("avformat:partial.mp4", "avformat:" + cwd + "/partial.mp4"):
            raw = ("\0".join((observation.HOST_MELT, "source.mlt", "-consumer", consumer)) + "\0").encode()
            self.assertEqual(observation.host_tool_consumer(raw, cwd), cwd + "/partial.mp4")
            result = loaded.verified_melt_cmdline(raw, cwd + "/partial.mp4",
                relative_cwd=cwd, owned_output_path=owned)
            self.assertEqual(result["selected_original_output_path"], cwd + "/partial.mp4")
            self.assertNotIn("actual_loaded_module_verified", result)
        legacy = "/tmp/semwright-video-" + "a" * 32
        raw = ("\0".join((observation.HOST_MELT, "source.mlt", "-consumer", "avformat:partial.mp4")) + "\0").encode()
        self.assertEqual(observation.host_tool_consumer(raw, legacy), legacy + "/partial.mp4")
        with self.assertRaises(loaded.LoadedModuleUnverified):
            loaded.verified_melt_cmdline(raw, cwd + "/partial.mp4",
                relative_cwd=cwd, owned_output_path="another.mp4")

    def test_owned_path_parser_never_accepts_foreign_root_traversal_or_missing_fd(self):
        for output in ("../x.mp4", "/x.mp4", "nested/x.mp4", "x.mp4/other", ".hidden.mp4",
                       "x.MP4", "x.mp4:stream", "x\\y.mp4", "x.mp4\0", "é.mp4", "a" * 161 + ".mp4"):
            with self.subTest(output=repr(output)), self.assertRaises(loaded.LoadedModuleUnverified):
                loaded.owned_output_work_cwd(output)
        for cwd in ("/tmp/a.mp4.raw-original-work", "/workspace/other/a.mp4.raw-original-work",
                    "/workspace/output/a.mp4.raw-original-work/other",
                    "/workspace/output/../a.mp4.raw-original-work"):
            with self.subTest(cwd=cwd), self.assertRaises(observation.G2Unverified):
                observation.host_owned_output(cwd)
        for fd in (None, -1, True, "3"):
            with self.subTest(fd=fd), self.assertRaisesRegex(loaded.LoadedModuleUnverified,
                    "owned_melt_work_descriptor_required"):
                loaded._owned_work_identity(fd, None, Path("unused"))
        # No descriptor/namespace/proc identity is synthesized into a physical PASS.

    def test_minimal_failure_reason_is_finite_private_and_bounded(self):
        marker = "PRIVATE-ENV-OR-PROC-SENTINEL"
        events = [dict(stage=marker, error=dict(exception_type=marker, message=marker)),
                  dict(stage="host_sealed_consumer", error=dict(
                      exception_type="G2Unverified", message="g2_host_owned_work_mapping_changed"))]
        events += [dict(stage="host_sealed_consumer", error=dict(exception_type="G2Unverified", message=code))
                   for code in observation.LIVE_WITNESS_CODES]
        reason = observation.minimal_witness_failure(RuntimeError(marker),
            types.SimpleNamespace(events=events), master_sha256="a" * 64, master_bytes=1234)
        raw = core.canonical(reason)
        core.project(reason, mlt.LIVE_WITNESS_REASON, exact=True)
        self.assertNotIn(marker.encode(), raw)
        self.assertEqual(reason["failure"]["code"], "unclassified-verification-error")
        self.assertLessEqual(len(reason["first_observer_failures"]), 16)
        self.assertLessEqual(len(raw), 8192)
        for name in ("raw_exception_text_included", "raw_proc_included", "raw_environment_included",
                     "diagnostic_history_complete_claimed", "native_terminal_inferred",
                     "module_attestation_inferred", "native_operation_retried", "physical_acceptance"):
            self.assertFalse(reason[name])
        for bad in (dict(reason, raw_proc_included=True), dict(reason, extra=marker)):
            with self.assertRaises(core.SelectionError):
                core.project(bad, mlt.LIVE_WITNESS_REASON, exact=True)

    def test_minimal_reason_is_retained_byte_exact_and_bound_to_original_master(self):
        for wrong_binding in (False, True):
            sample_world = world()
            terminal = sample_world.document("observation", "native-terminal.json")["native_result"]
            reason = observation.minimal_witness_failure(
                observation.G2Unverified("g2_original_live_module_not_uniquely_witnessed"), None,
                master_sha256="e" * 64 if wrong_binding else terminal["master_artifact_sha256"],
                master_bytes=terminal["master_artifact_bytes"])
            raw_reason = core.canonical(reason).rstrip() + b"\r\n"
            sample_world.put("observation", observation.LIVE_WITNESS_REASON_PATH, raw_reason)
            with sample_world.patched():
                raw, manifest = sample_world.capture()
                if wrong_binding:
                    with self.assertRaisesRegex(core.SelectionError, "native-witness-reason-master-binding"):
                        sample_world.verify(raw)
                else:
                    sample_world.verify(raw)
            row = row_for(manifest, observation.LIVE_WITNESS_REASON_PATH)
            self.assertEqual(row["state"], "INCLUDED")
            self.assertEqual(row["kind"], "json-original")
            self.assertEqual(row["retained_sha256"], core.sha(raw_reason))
            self.assertIn(raw_reason, raw)
            self.assertFalse(manifest["physical_acceptance"])

    def test_additional_aac_readback_preserves_original_carrier_and_denies_acceptance(self):
        sample_world = world()
        before = sample_world.files[("observation", "original-aac-clock-capture.json")]
        with sample_world.patched():
            raw, manifest = sample_world.capture()
            result = mlt.verify_mlt_intake(raw, current=historical.CURRENT,
                                           expected_diagnostic_admission=sample_world.admission)
        report = result["aac_presentation_readback"]
        self.assertEqual(report["status"], "CONSISTENT_WITHIN_SCOPE")
        self.assertEqual(report["original_carrier"], dict(sha256=core.sha(before), bytes=len(before)))
        self.assertFalse(report["original_carrier_replaced"])
        self.assertEqual(sample_world.files[("observation", "original-aac-clock-capture.json")], before)
        for flag in ("physical_acceptance", "human_acceptance", "release_authorized",
                     "packet_PTS_and_AAC_priming_verified", "privileged_Broker_grant_verified"):
            self.assertFalse(result[flag])
        self.assertFalse(manifest["physical_acceptance"])
        self.assertEqual(mlt.SOURCE_PATHS.count("tooling/native_mp4_aac_presentation.py"), 1)
        self.assertEqual(mlt.FILM_SOURCE_PATHS.count("tooling/native_mp4_aac_presentation.py"), 1)


if __name__ == "__main__":
    unittest.main()
