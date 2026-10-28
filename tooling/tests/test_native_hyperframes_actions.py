"""G0 caller/custody contracts. Synthetic originals; no native execution."""
import copy
from pathlib import Path
import subprocess
import tempfile
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


class SourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        existing.source_lock_fixture(self.root)
        self.environment = {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ATTEMPT": "1",
                            "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123",
                            "RUNNER_OS": "Linux"}

    def test_actions_context_uses_verified_selected_sdk_without_a_historical_pin(self):
        for revision in (existing.SDK_FIXTURE_REVISION, "c" * 40):
            with self.subTest(revision=revision):
                existing.source_lock_fixture(self.root, revision)
                with patch.object(actions, "ROOT", self.root), \
                     patch.object(actions.sys, "platform", "linux"), \
                     patch.dict(actions.os.environ, self.environment):
                    self.assertEqual(actions.context(), {
                        "source_sha": "a" * 40, "semwright_sha": revision,
                        "run_id": "123", "run_attempt": "1", "scenario": actions.SCENARIO})

    def test_changed_source_evidence_stops_actions_before_native_effects(self):
        path = self.root / "evidence/semwright/LICENSE-MIT"
        raw = path.read_bytes()
        path.write_bytes(b"X" + raw[1:])
        with patch.object(actions, "ROOT", self.root), \
             patch.object(actions.sys, "platform", "linux"), \
             patch.dict(actions.os.environ, self.environment), \
             patch.object(actions.custody, "new_json") as write, \
             patch.object(actions.subprocess, "Popen") as process, \
             self.assertRaisesRegex(existing.SourceAdmissionError,
                                    "source_admission_evidence_hash_mismatch"):
            actions.run(SimpleNamespace())
        write.assert_not_called()
        process.assert_not_called()


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
            if value.stage.startswith("compare-"):
                phase = value.stage[8:]
                return projection.PhaseOriginals(phase, "1" * 64, b"G0 phase result", ())

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

    def test_original_phase_result_survives_later_edit_failure(self):
        args, binaries, events, stage, command = self.sequence("prepare-edited")
        with patch.object(stages, "run", stage), \
             patch.object(actions, "direct_config", return_value=(Path("/config.json"), "a" * 64)), \
             patch.object(stages.Attempt, "command", command), \
             self.assertRaises(HyperframesEvidenceRejected):
            actions.execute_stages(args, binaries)
        self.assertEqual(set(args.phase_results), {"original"})
        self.assertEqual(args.phase_results["original"].comparison_raw, b"G0 phase result")
        self.assertNotIn("edited.direct-host", events)

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
        del files["/records/original-comparison.json"]
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             self.assertRaises(FileNotFoundError):
            projection.build_spec(args, current, bindings)

    def test_incomplete_selection_keeps_all_240_required_frame_slots(self):
        args, current, bindings, _, _ = self.fixture()
        args.phase_results = {}  # Failure before either oracle completed.
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
        args.phase_results = {}  # No earlier phase authority exists in this case.
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
        args, current, _, docs, files = PartialSelectionTests().fixture()
        args.private_config = Path("/config")
        files["/config/manifest.json"] = b"G0 shared owner manifest"
        inputs = {"schema": "motionwright.hyperframes.actions-native-inputs/1",
            "context": current, "recipient_sha256": "2" * 64, "automatic_retry": False,
            "binaries": {key: {"sha256": str(i + 1) * 64, "bytes": 100}
                         for i, key in enumerate(("mw", "direct", "driver", "node",
                                                  "sandbox_helper", "sem_cli", "daemon"))}}
        docs["/records/actions-native-inputs.json"] = inputs
        for phase in ("original", "edited"):
            source = docs[f"/records/{phase}-source.json"]["source"]
            attempt = stages.direct_attempt(phase, source["fingerprint"])
            root = Path("/direct-output") / ("render-" + attempt)
            receipt = docs[(root / "native-render-receipt.json").as_posix()]
            terminal = {"job_ref": "direct-" + phase, "state": "succeeded",
                        "fingerprint": source["fingerprint"], "runtime_lock_sha256": existing.PIN,
                        "directory": "render-" + attempt, "receipt": copy.deepcopy(receipt),
                        "automatic_retry": False}
            request_path = f"/records/{phase}-direct-request.json"
            request = {"schema": "semwright.hyperframes.render-request/1",
                       "source": source, "fingerprint": source["fingerprint"],
                       "runtime_lock_sha256": existing.PIN, "attempt_id": attempt}
            docs[request_path] = request
            files[request_path] = existing.encoded(request)
            reference = {
                "schema": actions.DIRECT_SCHEMA, "context": current, "phase": phase,
                "source": source, "result": copy.deepcopy(terminal),
                "request_sha256": sha(files[request_path]),
                "runtime_lock_sha256": existing.PIN,
                "manifest_sha256": sha(files["/config/manifest.json"]),
                "driver_sha256": inputs["binaries"]["driver"]["sha256"],
                "node_sha256": inputs["binaries"]["node"]["sha256"],
                "sandbox_helper_sha256": inputs["binaries"]["sandbox_helper"]["sha256"],
                "session_sha256": "6" * 64,
                "separate_empty_project": True, "prepared_export_equal_before": True,
                "prepared_export_equal_after": True, "direct_provider_without_broker": True,
                "original_job_terminal_observed": True, "host_shutdown_observed": True,
                "automatic_retry": False, "formal_acceptance": False}
            docs[f"/direct-host/{phase}/evidence/direct-host-reference.json"] = reference
            docs[f"/direct-host/{phase}/evidence/terminal.json"] = terminal
            for name in actions.FILES:
                rel = "source-" + source["fingerprint"] + "/" + name
                files[f"/direct-host/{phase}/project/" + rel] = files["/direct-source/" + rel]
        return args, current, docs, files

    def capture(self, args, current, docs, files):
        from test_native_evidence_projection import MemoryHandles
        files.update({path: existing.encoded(value) for path, value in docs.items()})
        bindings = [{"path": path, "sha256": "3" * 64} for path in stages.SOURCE_PATHS]
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             patch.object(actions.custody, "regular", memory(files)):
            spec, policies, roles = actions.extended_spec(args, current, bindings, incomplete=True)
        selected = {}
        for row in spec["members"]:
            path = (Path(spec["roots"][row["root"]]) / row["relative_path"]).as_posix()
            if path in files:
                selected[(row["root"], row["relative_path"])] = files[path]
        with patch.object(actions.selection, "RootHandles", return_value=MemoryHandles(selected)), \
             patch.object(actions.selection, "_verify_sources"):
            archive, manifest = actions.selection.capture_selected_originals(
                spec, policies=policies, role_contract=roles)
        return archive, manifest

    def verify(self, args, current, docs, files, *, change_retained=None, after_capture=None):
        archive, manifest = self.capture(args, current, docs, files)
        if change_retained:
            archive, manifest = change_retained(archive, manifest)
        if after_capture:
            after_capture(files)
        # Completeness of unrelated attempt diagnostics is covered by the shared
        # collector suite. These fixtures exercise actual TAR bytes and the
        # independent HyperFrames cross-bindings, not a mocked PNG validator.
        with patch.object(actions.custody, "regular", memory(files)), \
             patch.object(actions.selection, "validate_selected_manifest") as generic:
            actions.verify_direct_binding(args, current, manifest, archive=archive)
            generic.assert_called_once()

    def full_fixture(self):
        # Fill only the previously absent fixed diagnostic roles with explicit
        # synthetic schema fixtures. No missing-role/completeness validator is
        # mocked in the full-container regression below. This is not SQLite,
        # native runtime, signature, or physical acceptance evidence.
        import re
        args, current, docs, files = self.fixture()
        policies, roles = actions.direct_contract()
        def sample(rule, phase):
            source = docs["/records/" + (phase if phase in ("original", "edited") else "original") + "-source.json"]["source"]
            if rule is projection.SOURCE:
                return copy.deepcopy(source)
            if rule is projection.CONTEXT:
                return copy.deepcopy(current)
            if rule is projection.BINDING:
                return copy.deepcopy(source["composition"]["binding"])
            if rule is projection.PHASE:
                return phase
            if isinstance(rule, actions.selection.Literal):
                return copy.deepcopy(rule.value)
            if isinstance(rule, actions.selection.Nullable):
                return None
            if isinstance(rule, actions.selection.Object):
                return {k: sample(v.rule, phase) for k, v in rule.fields.items() if v.required}
            if isinstance(rule, actions.selection.Array):
                return [sample(rule.item, phase) for _ in range(rule.minimum)]
            if isinstance(rule, actions.selection.Number):
                return int(rule.minimum or 0) if rule.integer else float(rule.minimum or 0)
            if isinstance(rule, actions.selection.String):
                if rule.values:
                    return rule.values[0]
                values = ["G0 synthetic diagnostic", "a", "1", "1"*40, "1"*64,
                    existing.GEN, "project:"+existing.PROJECT, "node-"+"1"*32,
                    "#E53935", "frames/frame_000001.png", "render-g0", "v1.2.3",
                    "1.2.3.4", "libfixture.so", "hyperframes:original",
                    "https://storage.googleapis.com/chrome-for-testing-public/1.2.3.4/linux64/chrome-linux64.zip"]
                return next(v for v in values if len(v) <= rule.maximum
                            and (rule.pattern is None or re.fullmatch(rule.pattern, v)))
            if rule is bool:
                return False
            raise TypeError("unregistered synthetic fixture rule")
        files.update({path: existing.encoded(value) for path, value in docs.items()})
        bindings = [{"path": path, "sha256": "3"*64} for path in stages.SOURCE_PATHS]
        with patch.object(projection, "regular", memory(files)), \
             patch.object(actions.custody, "regular", memory(files)):
            spec, _, _ = actions.extended_spec(args, current, bindings, incomplete=True)
        for row in spec["members"]:
            path = (Path(spec["roots"][row["root"]]) / row["relative_path"]).as_posix()
            if not row["required"] or path in files:
                continue
            if row["kind"] == "original":
                files[path] = b"G0 SYNTHETIC DATABASE PLACEHOLDER; NOT SQLITE ACCEPTANCE"
            else:
                value = sample(policies[row["policy"]], row["phase"])
                actions.selection.project(value, policies[row["policy"]],
                    exact=row["kind"] == "json-original")
                docs[path] = value
                files[path] = existing.encoded(value)
        return args, current, docs, files

    def test_full_synthetic_container_preserves_names_without_mocked_completeness(self):
        args, current, docs, files = self.full_fixture()
        archive, manifest = self.capture(args, current, docs, files)
        self.assertTrue(manifest["required_layers_complete"])
        self.assertEqual(sum(r["role"] == "native-frame" and r["state"] == "INCLUDED"
                             for r in manifest["members"]), 240)
        long_jobs = [r for r in manifest["members"]
                     if r["root"] == "mediated-source" and r["role"] in ("host-job-intent", "host-job-binding")]
        self.assertEqual(len(long_jobs), 4)
        self.assertTrue(all(len(r["member"].rsplit("/", 1)[1]) in (106, 107) for r in long_jobs))
        with patch.object(actions.custody, "regular", memory(files)):
            actions.verify_direct_binding(args, current, manifest, archive=archive)

    def test_full_container_rejects_coordinated_replacement_against_original_phase_result(self):
        args, current, docs, files = self.full_fixture()
        comparison = docs["/records/original-comparison.json"]
        output = "/direct-output/render-" + stages.direct_attempt("original", comparison["source_fingerprint"]) + "/"
        replacement = existing.png_pair()[1]
        files[output + "frames/frame_000001.png"] = replacement
        receipt = docs[output + "native-render-receipt.json"]
        receipt["frames"][0].update(sha256=sha(replacement), bytes=len(replacement))
        comparison["frames"][0]["direct_png_sha256"] = sha(replacement)
        comparison["direct_receipt_sha256"] = sha(existing.encoded(receipt))
        docs["/direct-host/original/evidence/terminal.json"]["receipt"] = copy.deepcopy(receipt)
        docs["/direct-host/original/evidence/direct-host-reference.json"]["result"]["receipt"] = copy.deepcopy(receipt)
        archive, manifest = self.capture(args, current, docs, files)
        self.assertFalse(manifest["required_layers_complete"])
        with patch.object(actions.custody, "regular", memory(files)), \
             self.assertRaises(SelectionError):
            actions.verify_direct_binding(args, current, manifest, archive=archive)

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


    def test_each_phase_and_route_rejects_valid_png_substitution(self):
        for phase in ("original", "edited"):
            for route in ("direct", "mediated"):
                args, current, docs, files = self.fixture()
                fp = docs[f"/records/{phase}-source.json"]["source"]["fingerprint"]
                attempt = (stages.direct_attempt(phase, fp) if route == "direct"
                           else stages.mediated_attempt(phase, fp, existing.PIN))
                files[f"/{route}-output/render-{attempt}/frames/frame_000001.png"] = existing.png_pair()[1]
                with self.subTest(phase=phase, route=route), \
                     self.assertRaises(HyperframesEvidenceRejected):
                    self.verify(args, current, docs, files)

    def test_coordinated_original_substitution_before_reselection_is_rejected(self):
        # This is the causal cross-binding seam, using actual collector/TAR.
        # Unrelated diagnostic completeness is explicitly mocked by verify().
        for phase in ("original", "edited"):
            for route in ("direct", "mediated"):
                args, current, docs, files = self.fixture()
                comparison = docs[f"/records/{phase}-comparison.json"]
                fp = comparison["source_fingerprint"]
                attempt = (stages.direct_attempt(phase, fp) if route == "direct"
                           else stages.mediated_attempt(phase, fp, existing.PIN))
                output = f"/{route}-output/render-{attempt}/"
                replacement = existing.png_pair()[1]
                files[output + "frames/frame_000001.png"] = replacement
                receipt = docs[output + "native-render-receipt.json"]
                receipt["frames"][0].update(sha256=sha(replacement), bytes=len(replacement))
                comparison["frames"][0][route + "_png_sha256"] = sha(replacement)
                comparison[route + "_receipt_sha256"] = sha(existing.encoded(receipt))
                if route == "direct":
                    docs[f"/direct-host/{phase}/evidence/terminal.json"]["receipt"] = copy.deepcopy(receipt)
                    docs[f"/direct-host/{phase}/evidence/direct-host-reference.json"]["result"]["receipt"] = copy.deepcopy(receipt)
                else:
                    render = docs[f"/records/{phase}-render.json"]
                    render["hyperframes"]["result"]["receipt"] = copy.deepcopy(receipt)
                    comparison["render_document_sha256"] = sha(existing.encoded(render))
                    reopen = docs[f"/records/{phase}-reopen.json"]
                    reopen["render_document_sha256"] = comparison["render_document_sha256"]
                    comparison["reopen_document_sha256"] = sha(existing.encoded(reopen))
                with self.subTest(phase=phase, route=route), \
                     self.assertRaises((HyperframesEvidenceRejected, SelectionError)):
                    self.verify(args, current, docs, files)

    def test_both_html_copies_changed_together_do_not_create_an_original(self):
        args, current, docs, files = self.fixture()
        for path in files:
            if path.endswith("/index.html") and (path.startswith("/direct-source/")
                                                 or path.startswith("/direct-host/")):
                files[path] += b"changed together"
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(args, current, docs, files)

    def test_changed_embedded_terminal_receipt_is_rejected(self):
        args, current, docs, files = self.fixture()
        for suffix in ("terminal.json", "direct-host-reference.json"):
            value = docs["/direct-host/original/evidence/" + suffix]
            terminal = value["result"] if suffix.startswith("direct-host") else value
            terminal["receipt"]["frames"][0]["sha256"] = "0" * 64
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(args, current, docs, files)

    def test_manifest_and_payload_cannot_promote_replacement_hash_after_capture(self):
        from test_native_evidence_projection import rewrite_tar
        replacement = existing.png_pair()[1]
        def forge(archive, manifest):
            value = copy.deepcopy(manifest)
            row = next(row for row in value["members"] if row["phase"] == "original"
                       and row["root"] == "direct-output"
                       and row["relative_path"].endswith("/frames/frame_000001.png"))
            row.update(expected_sha256=sha(replacement), retained_sha256=sha(replacement),
                       bytes=len(replacement), retained_bytes=len(replacement))
            changed = rewrite_tar(archive,
                mutate_manifest=lambda target: target.update(value),
                mutate_payload=lambda name, raw: replacement if name == row["member"] else raw)
            return changed, value
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(*self.fixture(), change_retained=forge)

    def test_post_capture_live_changes_do_not_replace_retained_originals(self):
        def change(files):
            for path in list(files):
                if path.endswith(".png") or path.endswith("native-render-receipt.json"):
                    files[path] = b"G0 live files changed after the first capture"
        self.verify(*self.fixture(), after_capture=change)

    def test_inventory_semantics_cannot_be_changed(self):
        args, current, docs, files = self.fixture()
        path = next(path for path in files if path.startswith("/mediated-source/")
                    and path.endswith("/source-inventory.json"))
        value = existing.json.loads(files[path]); value["html_sha256"] = "0" * 64
        files[path] = existing.encoded(value)
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(args, current, docs, files)


    def test_actual_tar_payload_corruption_is_rejected_before_cross_binding(self):
        from test_native_evidence_projection import rewrite_tar
        def corrupt(archive, manifest):
            row = next(row for row in manifest["members"] if row["role"] == "native-frame"
                       and row["state"] == "INCLUDED")
            return rewrite_tar(archive, mutate_payload=lambda name, raw:
                existing.png_pair()[1] if name == row["member"] else raw), manifest
        with self.assertRaises(SelectionError):
            self.verify(*self.fixture(), change_retained=corrupt)

    def test_owner_manifest_changed_after_capture_cannot_match_retained_reference(self):
        def change(files):
            files["/config/manifest.json"] += b"changed after capture"
        with self.assertRaises(HyperframesEvidenceRejected):
            self.verify(*self.fixture(), after_capture=change)


class CaptureOrderingTests(unittest.TestCase):
    def test_failure_capture_is_sealed_once_and_cannot_claim_complete(self):
        args = SimpleNamespace(records=Path("/records"))
        current = {"source_sha": "a" * 40, "semwright_sha": existing.SDK_FIXTURE_REVISION,
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
        current = {"source_sha": "a" * 40, "semwright_sha": existing.SDK_FIXTURE_REVISION,
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


    def test_changed_receipt_still_captures_and_seals_once_before_failure(self):
        from test_native_evidence_projection import MemoryHandles
        args, current, docs, files = DirectBindingTests().fixture()
        receipt_path = next(path for path in docs if path.startswith("/direct-output/")
                            and "direct-original-" in path and path.endswith("native-render-receipt.json"))
        docs[receipt_path]["attempt_id"] = "changed-after-comparison"
        files.update({path: existing.encoded(value) for path, value in docs.items()})
        bindings = [{"path": path, "sha256": "3" * 64} for path in stages.SOURCE_PATHS]
        original_capture = actions.selection.capture_selected_originals
        def capture(spec, **kwargs):
            selected = {}
            for row in spec["members"]:
                path = (Path(spec["roots"][row["root"]]) / row["relative_path"]).as_posix()
                if path in files:
                    selected[(row["root"], row["relative_path"])] = files[path]
            with patch.object(actions.selection, "RootHandles", return_value=MemoryHandles(selected)), \
                 patch.object(actions.selection, "_verify_sources"):
                return original_capture(spec, **kwargs)
        with patch.object(stages, "document", documents(docs)), \
             patch.object(projection, "regular", memory(files)), \
             patch.object(actions.custody, "regular", memory(files)), \
             patch.object(actions.selection, "capture_selected_originals", side_effect=capture) as collect, \
             patch.object(actions, "extended_spec", wraps=actions.extended_spec) as build, \
             patch.object(actions.custody, "encrypt_snapshot", return_value=b"G0 sealed") as seal, \
             patch.object(actions.custody, "new_json"), \
             patch.object(actions.custody, "new_bytes") as write, \
             self.assertRaises(SelectionError):
            actions.capture_and_seal(args, current, bindings, {}, Path("/sealed"), completed=True)
        build.assert_called_once()
        self.assertTrue(build.call_args.kwargs["incomplete"])
        collect.assert_called_once()
        seal.assert_called_once()
        write.assert_called_once_with(Path("/sealed"), b"G0 sealed")


if __name__ == "__main__":
    unittest.main()
