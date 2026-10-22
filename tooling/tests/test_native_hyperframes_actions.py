"""G0 caller/custody contracts. Synthetic memory originals; no native execution."""
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import native_hyperframes_actions as actions
import native_hyperframes_e2e as stages
import native_hyperframes_projection as projection
from native_hyperframes_evidence import HyperframesEvidenceRejected, sha
from native_evidence_projection import SelectionError, project
import test_native_hyperframes as existing


def memory(files):
    def read(path, *_):
        try:
            return files[Path(path).as_posix()], {}
        except KeyError as error:
            raise FileNotFoundError(str(path)) from error
    return read


def documents(docs):
    def read(path):
        try:
            return copy.deepcopy(docs[Path(path).as_posix()])
        except KeyError as error:
            raise FileNotFoundError(str(path)) from error
    return read


class StageSequenceTests(unittest.TestCase):
    def sequence(self, failure=None, response=None):
        events = []
        args = SimpleNamespace(records=Path("/records"))
        binaries = {"direct": {"path": "/binary/direct"}}

        def stage(value):
            events.append(value.stage)
            if value.stage == failure:
                raise HyperframesEvidenceRejected("G0_observed_stage_failure")

        def command(_attempt, label, argv, timeout):
            events.append(label)
            self.assertEqual(argv, ["/binary/direct", str(Path("/config.json")), "a" * 64])
            self.assertEqual(timeout, 1020)
            if label == failure:
                raise subprocess.TimeoutExpired(argv, timeout)
            return response or {"status": "DIRECT_HOST_REFERENCE_VERIFIED",
                                "automatic_retry": False}

        return args, binaries, events, stage, command

    def test_seven_stages_and_two_separate_direct_jobs_in_order(self):
        args, binaries, events, stage, command = self.sequence()
        with patch.object(stages, "run", stage), \
             patch.object(actions, "direct_config", return_value=(Path("/config.json"), "a" * 64)), \
             patch.object(stages.Attempt, "command", command):
            actions.execute_stages(args, binaries)
        self.assertEqual(events, [
            "prepare-original", "original.direct-host", "render-original", "compare-original",
            "prepare-edited", "edited.direct-host", "render-edited", "compare-edited", "selection"])

    def test_unknown_direct_outcome_stops_before_mediated_and_edit_without_replay(self):
        args, binaries, events, stage, command = self.sequence("original.direct-host")
        with patch.object(stages, "run", stage), \
             patch.object(actions, "direct_config", return_value=(Path("/config.json"), "a" * 64)), \
             patch.object(stages.Attempt, "command", command), \
             self.assertRaises(subprocess.TimeoutExpired):
            actions.execute_stages(args, binaries)
        self.assertEqual(events, ["prepare-original", "original.direct-host"])

    def test_direct_summary_must_be_the_closed_success_response(self):
        args, binaries, events, stage, command = self.sequence(
            response={"status": "DIRECT_HOST_REFERENCE_VERIFIED", "automatic_retry": True})
        with patch.object(stages, "run", stage), \
             patch.object(actions, "direct_config", return_value=(Path("/config.json"), "a" * 64)), \
             patch.object(stages.Attempt, "command", command), \
             self.assertRaises(HyperframesEvidenceRejected):
            actions.execute_stages(args, binaries)
        self.assertEqual(events, ["prepare-original", "original.direct-host"])

    def test_comparison_failure_prevents_the_edit_and_its_native_job(self):
        args, binaries, events, stage, command = self.sequence("compare-original")
        with patch.object(stages, "run", stage), \
             patch.object(actions, "direct_config", return_value=(Path("/config.json"), "a" * 64)), \
             patch.object(stages.Attempt, "command", command), \
             self.assertRaises(HyperframesEvidenceRejected):
            actions.execute_stages(args, binaries)
        self.assertEqual(events, ["prepare-original", "original.direct-host",
                                  "render-original", "compare-original"])

    def test_existing_attempt_intent_stops_before_any_process_call(self):
        args = SimpleNamespace(records=Path("/records"))
        with patch.object(stages, "new_json", side_effect=FileExistsError("original intent")), \
             patch.object(stages, "run_sanitized_json") as invoke, \
             self.assertRaises(FileExistsError):
            stages.Attempt(args).command("original.direct-host", ["/binary/direct"])
        invoke.assert_not_called()


class PartialSelectionTests(unittest.TestCase):
    def fixture(self):
        args, current, bindings, docs, files = existing.SelectionSpecTests().fixture()
        docs = {Path(key).as_posix(): value for key, value in docs.items()}
        files = {Path(key).as_posix(): value for key, value in files.items()}
        args.direct_host = Path("/direct-host")
        args.intake = Path("/intake")
        return args, current, bindings, docs, files

    def test_strict_success_selection_still_requires_observed_comparison(self):
        args, current, bindings, docs, files = self.fixture()
        del docs["/records/original-comparison.json"]
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             self.assertRaises(FileNotFoundError):
            projection.build_spec(args, current, bindings)

    def test_incomplete_selection_keeps_all_240_required_frame_slots(self):
        args, current, bindings, _, _ = self.fixture()
        with patch.object(stages, "document", documents({})), \
             patch.object(projection, "regular", memory({})):
            spec = projection.build_spec(args, current, bindings, incomplete=True)
        frames = [row for row in spec["members"] if row["role"] == "native-frame"]
        self.assertEqual(len(frames), 240)
        for phase in ("original", "edited"):
            direct = {row["relative_path"] for row in frames
                      if row["phase"] == phase and row["root"] == "direct-output"}
            mediated = {row["relative_path"] for row in frames
                        if row["phase"] == phase and row["root"] == "mediated-output"}
            self.assertEqual(len(direct), 60)
            self.assertEqual(len(mediated), 60)
            self.assertTrue(direct.isdisjoint(mediated))
        self.assertTrue(all(row["required"] and row["expected_sha256"] is None
                            and row["bytes"] is None for row in frames))
        self.assertTrue(all("/frames/frame_" in row["relative_path"] for row in frames))
        self.assertFalse(any(row["relative_path"].endswith("frame_000000.png") for row in frames))
        self.assertEqual(spec["origin"]["upload_authorization"], "NOT_GRANTED_BY_COLLECTOR")

    def test_malformed_source_in_failure_path_cannot_select_an_arbitrary_path(self):
        args, current, bindings, _, _ = self.fixture()
        docs = {"/records/original-source.json": {"source": {"fingerprint": "../../session"}}}
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory({})), \
             patch.object(actions.custody, "regular", memory({})):
            spec, _, _ = actions.extended_spec(args, current, bindings, incomplete=True)
        self.assertTrue(all("session" not in row["relative_path"] for row in spec["members"]))
        self.assertTrue(any("source-unprepared-original" in row["relative_path"]
                            for row in spec["members"]))

    def test_read_only_contract_admits_the_produced_extended_selection(self):
        args, current, bindings, docs, files = self.fixture()
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             patch.object(actions.custody, "regular", memory(files)):
            spec, _, _ = actions.extended_spec(args, current, bindings, incomplete=True)
        policies, roles = actions.direct_contract()
        self.assertEqual(actions.selection._checked_spec(spec, policies, roles), spec)

    def test_partial_extended_spec_retains_direct_mutation_intents_without_logs(self):
        args, current, bindings, docs, files = self.fixture()
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             patch.object(actions.custody, "regular", memory(files)):
            spec, _, roles = actions.extended_spec(args, current, bindings, incomplete=True)
        for phase in ("original", "edited"):
            for role in ("direct-host-apply-intent", "direct-host-start-intent"):
                rows = [row for row in spec["members"]
                        if row["phase"] == phase and row["role"] == role]
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0]["required"])
                self.assertEqual(roles[(phase, "attempt-custody", role)], (1, 1))
        self.assertTrue(all(not row["relative_path"].endswith((".stdout", ".stderr", ".log"))
                            for row in spec["members"]))


class DirectBindingTests(unittest.TestCase):
    def fixture(self):
        args, current, _, docs, files = existing.SelectionSpecTests().fixture()
        docs = {Path(key).as_posix(): value for key, value in docs.items()}
        files = {Path(key).as_posix(): value for key, value in files.items()}
        args.private_config = Path("/config")
        args.direct_host = Path("/direct-host")
        args.intake = Path("/intake")
        files["/config/manifest.json"] = b"G0 actual manifest original"
        inputs = {"context": current, "binaries": {
            "driver": {"sha256": "3" * 64}, "node": {"sha256": "4" * 64},
            "sandbox_helper": {"sha256": "5" * 64}}}
        docs["/records/actions-native-inputs.json"] = inputs
        for phase in ("original", "edited"):
            source = docs[f"/records/{phase}-source.json"]["source"]
            attempt = stages.direct_attempt(phase, source["fingerprint"])
            root = Path("/direct-output") / ("render-" + attempt)
            _, _, receipt, _, _ = existing.receipt_fixture(str(root), source)
            receipt["attempt_id"] = attempt
            terminal = {"job_ref": "direct-" + phase, "state": "succeeded",
                        "fingerprint": source["fingerprint"], "runtime_lock_sha256": existing.PIN,
                        "directory": "render-" + attempt, "receipt": receipt,
                        "automatic_retry": False}
            request_path = f"/records/{phase}-direct-request.json"
            files[request_path] = existing.encoded({"source": source, "G0": "not executed"})
            reference = {
                "schema": actions.DIRECT_SCHEMA, "context": current, "phase": phase,
                "source": source, "result": copy.deepcopy(terminal),
                "request_sha256": sha(files[request_path]),
                "runtime_lock_sha256": existing.PIN,
                "manifest_sha256": sha(files["/config/manifest.json"]),
                "driver_sha256": "3" * 64, "node_sha256": "4" * 64,
                "sandbox_helper_sha256": "5" * 64, "session_sha256": "6" * 64,
                "separate_empty_project": True, "prepared_export_equal_before": True,
                "prepared_export_equal_after": True, "direct_provider_without_broker": True,
                "original_job_terminal_observed": True, "host_shutdown_observed": True,
                "automatic_retry": False, "formal_acceptance": False}
            docs[f"/direct-host/{phase}/evidence/direct-host-reference.json"] = reference
            docs[f"/direct-host/{phase}/evidence/terminal.json"] = terminal
            docs[f"/records/{phase}-render.json"] = {"hyperframes": {
                "job_ref": "mediated-" + phase, "result": {"directory": "render-mediated-" + phase}}}
            for name in actions.FILES:
                raw = ("G0 actual source original " + phase + " " + name).encode()
                rel = "source-" + source["fingerprint"] + "/" + name
                files["/direct-source/" + rel] = raw
                files[f"/direct-host/{phase}/project/" + rel] = raw
        return args, current, docs, files

    def verify(self, args, current, docs, files):
        with patch.object(stages, "document", documents(docs)), \
             patch.object(actions.custody, "regular", memory(files)), \
             patch.object(actions.selection, "validate_selected_manifest") as generic:
            actions.verify_direct_binding(args, current, {"G0": "already validated separately"})
            generic.assert_called_once()

    def test_matching_separate_original_jobs_and_three_native_inputs_are_admitted(self):
        self.verify(*self.fixture())

    def test_changed_actual_host_html_is_rejected_even_when_summary_claims_equal(self):
        args, current, docs, files = self.fixture()
        path = next(path for path in files if path.startswith("/direct-host/original/project/")
                    and path.endswith("/index.html"))
        files[path] += b"changed"
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(args, current, docs, files)

    def test_binary_and_manifest_substitution_are_rejected(self):
        for key in ("manifest_sha256", "driver_sha256", "node_sha256", "sandbox_helper_sha256"):
            args, current, docs, files = self.fixture()
            docs["/direct-host/original/evidence/direct-host-reference.json"][key] = "0" * 64
            with self.subTest(key=key), self.assertRaises(HyperframesEvidenceRejected):
                self.verify(args, current, docs, files)

    def test_request_or_runtime_substitution_is_rejected(self):
        for key in ("request_sha256", "runtime_lock_sha256"):
            args, current, docs, files = self.fixture()
            docs["/direct-host/original/evidence/direct-host-reference.json"][key] = "0" * 64
            with self.subTest(key=key), self.assertRaises(HyperframesEvidenceRejected):
                self.verify(args, current, docs, files)

    def test_terminal_or_action_context_substitution_is_rejected(self):
        for kind in ("terminal", "context"):
            args, current, docs, files = self.fixture()
            if kind == "terminal":
                docs["/direct-host/original/evidence/terminal.json"]["job_ref"] = "substituted-job"
            else:
                docs["/direct-host/original/evidence/direct-host-reference.json"]["context"] = {
                    **current, "run_id": "124"}
            with self.subTest(kind=kind), self.assertRaises(HyperframesEvidenceRejected):
                self.verify(args, current, docs, files)

    def test_direct_and_mediated_job_identity_must_remain_distinct(self):
        args, current, docs, files = self.fixture()
        docs["/records/original-render.json"]["hyperframes"]["job_ref"] = "direct-original"
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(args, current, docs, files)

    def test_unknown_summary_fields_are_not_accepted_as_original_json(self):
        _, _, docs, _ = self.fixture()
        reference = docs["/direct-host/original/evidence/direct-host-reference.json"]
        reference["unreviewed_authority"] = True
        policies, _ = actions.direct_contract()
        with self.assertRaises(SelectionError):
            project(reference, policies["hf-direct-host-reference"], exact=True)


class CaptureOrderingTests(unittest.TestCase):
    def test_failure_capture_is_sealed_once_and_cannot_claim_complete(self):
        args = SimpleNamespace(records=Path("/records"))
        current = {"source_sha": "a" * 40, "semwright_sha": stages.SEMWRIGHT,
                   "run_id": "123", "run_attempt": "1", "scenario": actions.SCENARIO}
        with patch.object(actions, "extended_spec", return_value=({"G0": "spec"}, {}, {})), \
             patch.object(actions.selection, "capture_selected_originals",
                          return_value=(b"G0 archive", {"G0": "incomplete"})) as capture, \
             patch.object(actions.custody, "encrypt_snapshot", return_value=b"G0 sealed") as seal, \
             patch.object(actions.custody, "new_json"), \
             patch.object(actions.custody, "new_bytes") as write, \
             patch.object(actions, "verify_direct_binding") as verify:
            result = actions.capture_and_seal(args, current, [], {}, Path("/sealed"), completed=False)
        capture.assert_called_once()
        seal.assert_called_once()
        write.assert_called_once_with(Path("/sealed"), b"G0 sealed")
        verify.assert_not_called()
        self.assertFalse(result["selected_originals_complete"])

    def test_failed_independent_readback_does_not_discard_or_repeat_the_first_seal(self):
        args = SimpleNamespace(records=Path("/records"))
        current = {"source_sha": "a" * 40, "semwright_sha": stages.SEMWRIGHT,
                   "run_id": "123", "run_attempt": "1", "scenario": actions.SCENARIO}
        with patch.object(actions, "extended_spec", return_value=({"G0": "spec"}, {}, {})), \
             patch.object(actions.selection, "capture_selected_originals",
                          return_value=(b"G0 archive", {"G0": "candidate"})) as capture, \
             patch.object(actions.custody, "encrypt_snapshot", return_value=b"G0 sealed") as seal, \
             patch.object(actions.custody, "new_json"), \
             patch.object(actions.custody, "new_bytes") as write, \
             patch.object(actions, "verify_direct_binding",
                          side_effect=HyperframesEvidenceRejected("G0_changed_original")), \
             self.assertRaises(HyperframesEvidenceRejected):
            actions.capture_and_seal(args, current, [], {}, Path("/sealed"), completed=True)
        capture.assert_called_once()
        seal.assert_called_once()
        write.assert_called_once_with(Path("/sealed"), b"G0 sealed")


if __name__ == "__main__":
    unittest.main()
