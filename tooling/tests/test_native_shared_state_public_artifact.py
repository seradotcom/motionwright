"""Adversarial v0.1 public Native SDK shared-state artifact admission."""
from __future__ import annotations

import copy
from contextlib import ExitStack, closing, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_shared_state_public_artifact as pub  # noqa: E402
import native_host_shared_state as host  # noqa: E402

SOURCE = "a" * 40
PIN = "b" * 40


class NativeSharedStatePublicArtifactTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="mw-v01-shared-state-public-")
        self.addCleanup(t.cleanup)
        self.private = Path(t.name) / "private"
        self.private.mkdir()
        self.public = Path(t.name) / "public"
        self.result = {
            "motionwright_sha": SOURCE,
            "semwright_sha": PIN,
            "provider_sha256": "c" * 64,
            "project": {
                "id": "OWNER_PRIVATE_PROJECT",
                "resource": "OWNER_PRIVATE_RESOURCE",
                "generation": "OWNER_PRIVATE_GENERATION",
                "final_revision": "5",
                "final_title": "SDK final shared state",
            },
            "checks": {key: True for key in pub.EXPECTED_CHECKS},
        }
        outcomes = {
            "positive": [("observe", None), ("project.rename", None), ("observe", None),
                         ("project.rename", "InvalidArgument"), ("observe", "PolicyDenied"),
                         ("observe", None), ("observe", None), ("project.preview", None),
                         ("project.preview", None), ("project.preview", "InvalidArgument"),
                         ("observe", None), ("project.rename", None)],
            "stale-ui": [("observe", None), ("project.rename", "StaleReference")],
            "stale-preview": [("observe", None), ("project.preview", None),
                              ("project.preview", "StaleReference")],
            "stale-cas": [("observe", None), ("observe", None), ("project.rename", None),
                          ("project.rename", "StaleReference")],
            "cas-race": [("observe", None), ("observe", None), ("project.preview", None),
                         ("project.preview", None), ("project.rename", None),
                         ("project.rename", "StaleReference")],
        }
        self.calls = []
        self.result["cases"] = {}
        for case, operations in outcomes.items():
            owner = Path(t.name) / "owners" / case
            database = owner / "motionwright-data" / "motionwright.sqlite3"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"PRIVATE_DATABASE_ORIGINAL")
            metadata = database.stat()
            self.result["cases"][case] = {
                "owner": str(owner), "provider_sha256": "c" * 64,
                "database": {"path": str(database), "device": metadata.st_dev, "inode": metadata.st_ino},
                "native_calls": len(operations),
                "terminal": None if case == "positive" else {
                    "case": case, "evaluation": "EXPECTED_TERMINAL_UNKNOWN", "outcome_known": False,
                    "scope_terminal": True, "effects_absent_asserted": False},
            }
            for index, (command, error) in enumerate(operations):
                args = {"ref": "OWNER_SECRET_NATIVE_REF_" + str(index)}
                terminal_count = 2 if case == "cas-race" else 1
                if case != "positive" and index >= len(operations) - terminal_count:
                    title = {
                        "stale-ui": "must not commit from stale ref",
                        "stale-preview": "stale preview must be rejected",
                        "stale-cas": "old default-session ref must stay stale",
                        "cas-race": "Human contender" if index == len(operations) - 2 else "Agent contender",
                    }[case]
                    if case == "stale-preview":
                        args["edit"] = {"operation": "project.rename", "title": title}
                    else:
                        args["title"] = title
                self.calls.append({
                    "args": args, "command": "driver.motionwright." + command,
                    "envelope": {"ok": error is None, "private": "OWNER_SECRET_ENVELOPE",
                                 "error": None if error is None else {
                                     "code": error, "outcome_known": case == "positive",
                                     "message": "OWNER_SECRET_ERROR_ORIGINAL",
                                     "progress": {"observed": 1}, "candidates": ["OWNER_SECRET_CANDIDATE"]}},
                    "exit_code": 0 if error is None else 4,
                    "session": "OWNER_PRIVATE_SESSION_" + str(index % 2 if case == "cas-race" else 0),
                    "case": case, "owner": str(owner), "receipt": f"{len(self.calls) + 1:032x}",
                })
                if error is None:
                    self.calls[-1]["envelope"]["data"] = {
                        "binding_ref": self.binding(case, self.calls[-1]["session"]),
                        "result": {"ok": True, "data": {"private": "OWNER_SECRET_NATIVE_DATA"}},
                    }
        self.store()
        (self.private / "semwrightd.log").write_text(
            "OWNER_PRIVATE_DAEMON_SECRET", encoding="utf-8"
        )
        stderr = self.private / "startup-stderr" / "shared-state"
        stderr.mkdir(parents=True)
        (stderr / "stderr.original").write_bytes(b"OWNER_PRIVATE_STARTUP_STDERR")
        (stderr / "capture.json").write_text('{"connection_result":{"returned":"ok"}}')
        (self.private.parent / "sandbox-profile.txt").write_text(
            "OWNER_PRIVATE_NETWORK_CONFIG", encoding="utf-8"
        )

    @staticmethod
    def binding(case, session):
        return "provider-mount:" + hashlib.sha256((case + ":" + session).encode()).hexdigest()[:32]

    def store(self):
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / "calls.json").write_text(json.dumps(self.calls))
        for case in pub.CASE_COMMANDS:
            directory = self.private / case
            directory.mkdir(exist_ok=True)
            transport, sessions = [], set()
            for entry in self.calls:
                if entry["case"] != case:
                    continue
                binding = self.binding(case, entry["session"])
                if entry["session"] not in sessions:
                    sessions.add(entry["session"])
                    transport.append({
                        "command": "providers.mount.observe",
                        "args": {"provider": "driver:motionwright", "root": "motionwright-data"},
                        "session": entry["session"],
                        "receipt": f"{1000 + list(pub.CASE_COMMANDS).index(case) * 10 + len(sessions):032x}",
                        "automatic_retry": False, "exit_code": 0,
                        "envelope": {"ok": True, "data": {
                            "binding_ref": binding, "provider": "driver:motionwright",
                            "connection": hashlib.sha256(case.encode()).hexdigest()[:32],
                            "provider_generation": 162, "admission_ttl_ms": 59999,
                            "mount": {"root": "motionwright-data",
                                      "path": str(Path(entry["owner"]) / "motionwright-data"),
                                      "read": True, "write": True, "execute": False}}},
                    })
                transport.append({"command": "providers.execute_bound",
                                  "args": {"binding_ref": binding,
                                           "request": {"command": entry["command"], "args": entry["args"], "dry_run": False}},
                                  "session": entry["session"], "receipt": entry["receipt"],
                                  "automatic_retry": False, "exit_code": entry["exit_code"],
                                  "envelope": entry["envelope"]})
            self.write_transport(case, transport)

    def write_transport(self, case, transport):
        directory = self.private / case
        (directory / "transport-calls.original.json").write_text(json.dumps(transport))
        for entry in transport:
            (directory / (entry["receipt"] + ".stdout.original")).write_bytes(
                json.dumps(entry["envelope"]).encode() + b"\n")
            (directory / (entry["receipt"] + ".stderr.original")).write_bytes(b"")

    def reject(self, reason):
        with self.assertRaises(pub.PublicationError) as error:
            pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(error.exception.code, reason)
        self.assertFalse(self.public.exists())

    def test_only_fifteen_verified_checks_and_hash_receipts_are_public(self):
        receipt = pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(receipt["native_shared_state_ci"], "PASS")
        self.assertEqual(receipt["verified_checks"], sorted(pub.EXPECTED_CHECKS))
        self.assertEqual(receipt["verified_native_invocations"], 27)
        self.assertEqual(receipt["verified_known_core_denials"], 3)
        self.assertEqual(receipt["verified_terminal_driver_cases"], 4)
        self.assertIs(receipt["terminal_driver_outcomes_known"], False)
        self.assertIs(receipt["effects_absent_asserted"], False)
        self.assertEqual(len(receipt["verified_checks"]), 15)
        self.assertEqual(receipt["source_result_digest_sha256"],
                         hashlib.sha256((self.private / "result.json").read_bytes()).hexdigest())
        self.assertEqual(receipt["source_calls_digest_sha256"],
                         hashlib.sha256((self.private / "calls.json").read_bytes()).hexdigest())
        self.assertEqual([f.name for f in self.public.iterdir()], ["public-evidence.json"])
        txt = (self.public / "public-evidence.json").read_text()
        for secret in ("OWNER_PRIVATE_", "OWNER_SECRET_", "semwrightd.log",
                       "OWNER_PRIVATE_DAEMON_SECRET", "sandbox-profile.txt", "startup-stderr",
                       "OWNER_PRIVATE_STARTUP_STDERR", "capture.json"):
            self.assertNotIn(secret, txt)
        self.assertFalse(receipt["raw_native_cli_calls_uploaded"])
        self.assertFalse(receipt["raw_daemon_or_provider_logs_uploaded"])
        self.assertFalse(receipt["owner_session_or_project_refs_uploaded"])
        self.assertFalse(receipt["native_effect_replayed_by_publisher"])
        self.assertFalse(receipt["v01_release_authorized"])
        self.assertEqual(receipt["installed_owner_acceptance"], "NOT_RUN")

    def test_fail_cancel_skip_always_publish_only_not_verified(self):
        for status in ("failure", "cancelled", "skipped"):
            with self.subTest(status=status):
                target = self.public.parent / ("out-" + status)
                result = pub.publish(Path("/no/private/native/proof"), target,
                                     SOURCE, PIN, status)
                self.assertEqual(result["native_shared_state_ci"], "NOT_VERIFIED")
                self.assertEqual(result["verified_checks"], [])
                self.assertIsNone(result["source_calls_digest_sha256"])
                self.assertIsNone(result["verified_native_invocations"])
                self.assertEqual(result["verified_known_core_denials"], 0)
                self.assertEqual(result["verified_terminal_driver_cases"], 0)
                self.assertIsNone(result["terminal_driver_outcomes_known"])
                self.assertIs(result["effects_absent_asserted"], False)
                self.assertEqual([x.name for x in target.iterdir()],
                                 ["public-evidence.json"])

    def test_source_and_pinned_sdk_swap_fail_closed(self):
        self.result["motionwright_sha"] = "f" * 40
        self.store()
        self.reject("native_shared_state_source_not_verified")
        self.result["motionwright_sha"] = SOURCE
        self.result["semwright_sha"] = "f" * 40
        self.store()
        self.reject("native_shared_state_source_not_verified")

    def test_missing_false_or_extra_checks_cannot_claim_success(self):
        del self.result["checks"]["ui_seed_visible_through_host"]
        self.store()
        self.reject("native_shared_state_checks_not_verified")
        self.result["checks"]["ui_seed_visible_through_host"] = False
        self.store()
        self.reject("native_shared_state_checks_not_verified")
        self.result["checks"]["ui_seed_visible_through_host"] = True
        self.result["checks"]["fake_extra_approval"] = True
        self.store()
        self.reject("native_shared_state_checks_not_verified")

    def test_forged_original_native_project_not_admitted(self):
        self.result["project"]["final_title"] = "forged approval"
        self.store()
        self.reject("native_shared_state_final_project_unverified")

    def test_native_job_ids_are_never_promoted_as_source_fields(self):
        self.result["native_job_ref"] = "OWNER_SECRET"
        self.store()
        self.reject("native_shared_state_source_not_verified")

    def test_missing_raw_calls_refuses_success_not_reconstructs_them(self):
        (self.private / "calls.json").unlink()
        self.reject("missing_or_linked_public_source")

    def test_contradictory_returncode_rejected(self):
        self.calls[0]["exit_code"] = 4
        self.store()
        self.reject("contradictory_private_native_call_status")

    def test_each_case_requires_literal_expected_certainty_and_preserves_original_error(self):
        original = copy.deepcopy(self.calls)
        negatives = [index for index, call in enumerate(self.calls)
                     if call["envelope"]["ok"] is False]
        for index in negatives:
            expected = original[index]["case"] == "positive"
            reason = ("private_native_call_known_denial_unverified" if expected
                      else "private_native_terminal_unknown_unverified")
            for known in (not expected, 0, 1, "true", None):
                with self.subTest(call=index, known=known):
                    self.calls = copy.deepcopy(original)
                    error = self.calls[index]["envelope"]["error"]
                    if known is None:
                        error.pop("outcome_known")
                    else:
                        error["outcome_known"] = known
                    self.store()
                    before = (self.private / "calls.json").read_bytes()
                    self.reject(reason)
                    self.assertEqual((self.private / "calls.json").read_bytes(), before)
            self.calls = copy.deepcopy(original)
            self.calls[index]["envelope"].pop("error")
            self.store()
            self.reject(reason)

    def test_old_call_roster_cannot_claim_terminal_owner_and_preview_checks(self):
        self.calls = [call for call in self.calls
                      if call["command"] != "driver.motionwright.project.preview"]
        self.store()
        self.reject("incomplete_private_call_receipts")

    def test_extra_native_command_or_partial_call_chain_rejected(self):
        self.calls[0]["command"] = "driver.other.unapproved"
        self.store()
        self.reject("private_native_call_sequence_unverified")
        self.calls = self.calls[:-1]
        self.store()
        self.reject("incomplete_private_call_receipts")

    def test_duplicate_keys_inside_original_native_calls_refused(self):
        raw = (self.private / "calls.json").read_text()
        raw = raw.replace('"ok": true,', '"ok": true, "ok": false,', 1)
        (self.private / "calls.json").write_text(raw)
        self.reject("duplicate_private_call_key")

    def test_duplicate_keys_inside_original_result_refused(self):
        data = (self.private / "result.json").read_text()
        data = data.replace('"motionwright_sha": ', '"motionwright_sha": "forged", "motionwright_sha": ', 1)
        (self.private / "result.json").write_text(data)
        self.reject("duplicate_evidence_json_key")

    def test_invalid_output_destination_and_invalid_status_refused(self):
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.public, SOURCE, PIN, "pending")
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.private / "public", SOURCE, PIN, "failure")
        pub.publish(self.private, self.public, SOURCE, PIN, "success")
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.public, SOURCE, PIN, "success")

    def test_linked_private_result_refused(self):
        target = self.private / "result.json"
        old = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda p: p == target or old(p)):
            self.reject("missing_or_linked_public_source")

    def test_workflow_upload_is_curated_not_raw_native_folder(self):
        yml = (Path(__file__).resolve().parents[2]
               / ".github/workflows/native-sdk.yml").read_text(encoding="utf-8")
        self.assertIn("path: verification/native-sdk/public-shared-state", yml)
        self.assertNotIn("path: verification/native-sdk/github-actions/shared-state\n", yml)
        self.assertIn("native_shared_state_public_artifact.py", yml)
        self.assertIn("test_native_shared_state_public_artifact.py", yml)
        # GitHub refuses action refs that abbreviate immutable commit SHAs.
        # This has previously prevented real-host startup before any code ran.
        import re
        pins = re.findall(r"uses:\s*[^\s@]+@([0-9a-f]+)", yml)
        self.assertGreaterEqual(len(pins), 5)
        self.assertTrue(all(len(pin) == 40 for pin in pins),
                        "Every SHA-pinned GitHub Action must use the full 40 characters")


    def test_wrong_or_swapped_error_codes_cannot_match_closed_command_roster(self):
        original = copy.deepcopy(self.calls)
        negatives = [i for i, call in enumerate(original)
                     if call["envelope"]["ok"] is False]
        for index in negatives:
            with self.subTest(unapproved_code_at=index):
                self.calls = copy.deepcopy(original)
                self.calls[index]["envelope"]["error"]["code"] = "BackendFailed"
                self.store()
                before = (self.private / "calls.json").read_bytes()
                self.reject("private_native_error_sequence_unverified")
                self.assertEqual((self.private / "calls.json").read_bytes(), before)
        observe = next(i for i in negatives
                       if original[i]["command"] == "driver.motionwright.observe")
        rename = next(i for i in negatives
                      if original[i]["command"] == "driver.motionwright.project.rename"
                      and original[i]["envelope"]["error"]["code"] == "StaleReference")
        for field in ("code", "command"):
            with self.subTest(swapped=field):
                self.calls = copy.deepcopy(original)
                left, right = self.calls[observe], self.calls[rename]
                if field == "code":
                    left, right = left["envelope"]["error"], right["envelope"]["error"]
                left[field], right[field] = right[field], left[field]
                self.store()
                before = (self.private / "calls.json").read_bytes()
                self.reject("private_native_error_sequence_unverified")
                self.assertEqual((self.private / "calls.json").read_bytes(), before)
        self.calls = copy.deepcopy(original)
        rename_invalid = next(i for i in negatives
                              if original[i]["command"] == "driver.motionwright.project.rename"
                              and original[i]["envelope"]["error"]["code"] == "InvalidArgument")
        self.calls[rename_invalid]["envelope"]["error"]["code"] = "StaleReference"
        self.store()
        self.reject("private_native_error_sequence_unverified")

    def test_missing_or_untyped_error_codes_preserve_unknown_denial_priority(self):
        original = copy.deepcopy(self.calls)
        negatives = [i for i, call in enumerate(original)
                     if call["envelope"]["ok"] is False]
        for index in negatives:
            for missing, value in ((True, None), (False, None), (False, 1),
                                   (False, True), (False, []), (False, {})):
                for known in (True, False):
                    with self.subTest(call=index, missing=missing, code=value, known=known):
                        self.calls = copy.deepcopy(original)
                        error = self.calls[index]["envelope"]["error"]
                        if missing:
                            error.pop("code")
                        else:
                            error["code"] = value
                        error["outcome_known"] = known
                        self.store()
                        before = (self.private / "calls.json").read_bytes()
                        expected = original[index]["case"] == "positive"
                        self.reject("invalid_private_native_error" if known is expected
                                    else "private_native_call_known_denial_unverified" if expected
                                    else "private_native_terminal_unknown_unverified")
                        self.assertEqual((self.private / "calls.json").read_bytes(), before)

    def test_nonfinite_private_call_json_is_refused_before_publication(self):
        original = copy.deepcopy(self.calls)
        for token in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
            for location in ("args", "envelope", "nested_error"):
                with self.subTest(token=token, location=location):
                    self.calls = copy.deepcopy(original)
                    if location == "nested_error":
                        target = self.calls[-1]["envelope"]["error"]
                    else:
                        target = self.calls[0][location]
                    target["private_metric"] = {"nested": [0, "NONFINITE_SENTINEL"]}
                    raw = json.dumps(self.calls)
                    self.assertEqual(raw.count('"NONFINITE_SENTINEL"'), 1)
                    raw = raw.replace('"NONFINITE_SENTINEL"', token)
                    (self.private / "calls.json").write_bytes(raw.encode("utf-8"))
                    before = (self.private / "calls.json").read_bytes()
                    self.reject("invalid_private_call_receipts")
                    self.assertEqual((self.private / "calls.json").read_bytes(), before)
        finite = copy.deepcopy(original)
        finite[0]["args"]["private_metric"] = {"nested": [0.25, -1.5, 1e308]}
        self.assertEqual(pub.parse_private_call_list(json.dumps(finite).encode("utf-8")), finite)

    def test_closed_cases_have_distinct_owners_databases_and_exact_terminal_metadata(self):
        original_result, original_calls = copy.deepcopy(self.result), copy.deepcopy(self.calls)
        mutations = (
            (lambda: self.result["cases"]["stale-ui"].update(owner=self.result["cases"]["positive"]["owner"]),
             "private_native_case_identity_unverified"),
            (lambda: self.result["cases"]["stale-ui"]["database"].update(
                device=self.result["cases"]["positive"]["database"]["device"],
                inode=self.result["cases"]["positive"]["database"]["inode"]),
             "private_native_case_identity_unverified"),
            (lambda: self.result["cases"]["stale-ui"].update(provider_sha256="d" * 64),
             "private_native_case_identity_unverified"),
            (lambda: self.result["cases"]["stale-ui"].update(native_calls=3),
             "private_native_case_identity_unverified"),
            (lambda: self.calls[12].update(owner=self.result["cases"]["positive"]["owner"]),
             "private_native_case_identity_unverified"),
            (lambda: self.result["cases"]["stale-ui"]["terminal"].update(outcome_known=True),
             "private_native_terminal_evaluation_unverified"),
            (lambda: self.result["cases"]["stale-ui"]["terminal"].update(effects_absent_asserted=True),
             "private_native_terminal_evaluation_unverified"),
            (lambda: self.result["cases"]["stale-ui"]["terminal"].update(scope_terminal=1),
             "private_native_terminal_evaluation_unverified"),
            (lambda: self.result["cases"].pop("stale-ui"), "private_native_case_roster_unverified"),
        )
        for index, (mutate, reason) in enumerate(mutations):
            with self.subTest(mutation=index):
                self.result, self.calls = copy.deepcopy(original_result), copy.deepcopy(original_calls)
                mutate()
                self.store()
                self.reject(reason)

    def test_terminal_requests_and_receipts_cannot_be_swapped_or_reused(self):
        original = copy.deepcopy(self.calls)
        mutations = (
            (lambda: self.calls[13]["args"].update(title="unreviewed terminal effect"),
             "private_native_terminal_request_unverified"),
            (lambda: self.calls[13]["args"].update(ref=""), "private_native_terminal_request_unverified"),
            (lambda: self.calls[16]["args"]["edit"].update(operation="scene.add"),
             "private_native_terminal_request_unverified"),
            (lambda: self.calls[-1].update(session=self.calls[-2]["session"]),
             "private_native_race_sessions_unverified"),
            (lambda: self.calls[-1].update(receipt=self.calls[-2]["receipt"]),
             "duplicate_private_native_receipt"),
            (lambda: self.calls[13].update(case="stale-cas"), "private_native_case_sequence_unverified"),
        )
        for index, (mutate, reason) in enumerate(mutations):
            with self.subTest(mutation=index):
                self.calls = copy.deepcopy(original)
                mutate()
                self.store()
                self.reject(reason)

    def test_raw_originals_and_transport_must_match_every_full_envelope(self):
        case = "stale-ui"
        receipt = self.calls[13]["receipt"]
        directory = self.private / case
        for mutation in ("missing_stdout", "missing_stderr", "changed_error", "changed_args", "changed_session", "retry"):
            with self.subTest(mutation=mutation):
                self.store()
                transport = json.loads((directory / "transport-calls.original.json").read_bytes())
                reason = "private_native_original_mismatch"
                if mutation.startswith("missing_"):
                    stream = mutation.removeprefix("missing_")
                    (directory / (receipt + "." + stream + ".original")).unlink()
                    reason = "missing_or_linked_public_source"
                elif mutation == "changed_error":
                    original = copy.deepcopy(transport[-1]["envelope"])
                    original["error"]["progress"]["observed"] = 2
                    (directory / (receipt + ".stdout.original")).write_text(json.dumps(original))
                else:
                    if mutation == "changed_args":
                        transport[-1]["args"]["request"]["args"]["title"] = "changed original request"
                    elif mutation == "changed_session":
                        transport[-1]["session"] = "another-owner-session"
                        reason = "private_native_binding_unverified"
                    else:
                        transport[-1]["automatic_retry"] = True
                        reason = "private_native_transport_unverified"
                    self.write_transport(case, transport)
                before = (self.private / "calls.json").read_bytes()
                self.reject(reason)
                self.assertEqual((self.private / "calls.json").read_bytes(), before)

    def test_no_followup_transport_or_native_call_after_terminal_batch(self):
        case = "stale-ui"
        directory = self.private / case
        for command in ("providers.mount.observe", "providers.execute_bound"):
            with self.subTest(command=command):
                self.store()
                transport = json.loads((directory / "transport-calls.original.json").read_bytes())
                extra = copy.deepcopy(transport[0] if command == "providers.mount.observe" else transport[1])
                extra["receipt"] = "f" * 32
                if command == "providers.mount.observe":
                    extra["session"] = "new-session-after-terminal"
                    extra["envelope"]["data"]["binding_ref"] = "provider-mount:" + "e" * 32
                transport.append(extra)
                self.write_transport(case, transport)
                self.reject("private_native_terminal_transport_tail_unverified" if command == "providers.mount.observe"
                            else "private_native_transport_roster_unverified")
        self.calls[12], self.calls[13] = self.calls[13], self.calls[12]
        self.store()
        self.reject("private_native_terminal_position_unverified")

    def test_cas_terminal_batch_accepts_either_completion_order_without_new_dispatch(self):
        self.calls[-1], self.calls[-2] = self.calls[-2], self.calls[-1]
        self.store()
        receipt = pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(receipt["verified_native_invocations"], 27)
        self.assertEqual(len(receipt["verified_checks"]), 15)

    def test_linked_case_and_raw_stream_are_refused_before_publication(self):
        targets = (self.private / "stale-ui",
                   self.private / "stale-ui" / (self.calls[13]["receipt"] + ".stdout.original"))
        old = Path.is_symlink
        for target in targets:
            with self.subTest(target=target.name):
                with patch.object(Path, "is_symlink", autospec=True,
                                  side_effect=lambda p: p == target or old(p)):
                    self.reject("missing_or_linked_private_case_root" if target.is_dir()
                                else "missing_or_linked_public_source")

    def test_mount_originals_bind_owner_provider_connection_and_session(self):
        case = "cas-race"
        directory = self.private / case
        for mutation in ("owner_path", "provider", "read", "write", "execute", "shared_connection",
                         "shared_binding", "changed_generation", "unobserved_binding", "other_session_binding",
                         "reply_binding", "execute_before_mount"):
            with self.subTest(mutation=mutation):
                self.store()
                transport = json.loads((directory / "transport-calls.original.json").read_bytes())
                mounts = [entry for entry in transport if entry["command"] == "providers.mount.observe"]
                positive = json.loads((self.private / "positive" / "transport-calls.original.json").read_bytes())[0]
                observed = mounts[0]["envelope"]["data"]
                reason = "private_native_mount_unverified"
                if mutation == "owner_path":
                    observed["mount"]["path"] = positive["envelope"]["data"]["mount"]["path"]
                elif mutation == "provider":
                    observed["provider"] = "driver:unapproved"
                elif mutation in {"read", "write", "execute"}:
                    observed["mount"][mutation] = not observed["mount"][mutation]
                elif mutation == "shared_connection":
                    for mount in mounts:
                        mount["envelope"]["data"]["connection"] = positive["envelope"]["data"]["connection"]
                    reason = "private_native_owner_binding_crossed"
                elif mutation == "shared_binding":
                    observed["binding_ref"] = positive["envelope"]["data"]["binding_ref"]
                    reason = "private_native_owner_binding_crossed"
                elif mutation == "changed_generation":
                    mounts[1]["envelope"]["data"]["provider_generation"] += 1
                    reason = "private_native_owner_binding_crossed"
                elif mutation in {"unobserved_binding", "other_session_binding"}:
                    transport[-1]["args"]["binding_ref"] = (
                        "provider-mount:" + "f" * 32 if mutation == "unobserved_binding"
                        else mounts[0]["envelope"]["data"]["binding_ref"])
                    reason = "private_native_binding_unverified"
                elif mutation == "reply_binding":
                    transport[1]["envelope"]["data"]["binding_ref"] = "provider-mount:" + "f" * 32
                    reason = "private_native_binding_unverified"
                else:
                    transport[0], transport[1] = transport[1], transport[0]
                    reason = "private_native_binding_unverified"
                self.write_transport(case, transport)
                self.reject(reason)

    def test_coherent_owner_claim_cannot_replace_the_original_mount_path(self):
        case = "stale-ui"
        owner = Path(self.result["cases"][case]["owner"]).parent / "forged-owner"
        self.result["cases"][case]["owner"] = str(owner)
        self.result["cases"][case]["database"]["path"] = str(owner / "motionwright-data" / "motionwright.sqlite3")
        for call in self.calls:
            if call["case"] == case:
                call["owner"] = str(owner)
        # Keep all retained mount/transport/stream originals untouched.
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / "calls.json").write_text(json.dumps(self.calls))
        self.reject("private_native_mount_unverified")


class PrivateCliErrorRedactionTests(unittest.TestCase):
    def test_original_stream_collision_is_not_overwritten_or_accepted_as_terminal_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            receipt = "a" * 32
            retained = root / (receipt + ".stdout.original")
            retained.write_bytes(b"previous original must survive")
            error = {"ok": False, "error": {"code": "StaleReference", "outcome_known": False}}
            transport = []

            def dispatch(command, args, **_kwargs):
                return host.invoke_cli({}, root / "socket", root / "session", "providers.execute_bound",
                                       {"request": {"command": command, "args": args}}, transport,
                                       ok=None, evidence_root=root)["envelope"]

            scope = host.SharedStateCalls("stale-ui", dispatch)
            with patch.object(host.uuid, "uuid4", return_value=Mock(hex=receipt)), \
                    patch.object(host.subprocess, "run", return_value=Mock(
                        returncode=4, stdout=json.dumps(error).encode(), stderr=b"original stderr")) as run:
                with self.assertRaises(FileExistsError):
                    scope.terminal([("driver.motionwright.project.rename",
                                     {"ref": "observed", "title": "must not commit from stale ref"}, None)])
                with self.assertRaisesRegex(AssertionError, "terminal"):
                    scope.invoke("driver.motionwright.observe", {})
                self.assertEqual(run.call_count, 1)
            self.assertEqual(retained.read_bytes(), b"previous original must survive")
            self.assertIsNone(scope.terminal_evaluation)

    SECRET = b"PRIVATE_OWNER_TICKET_ABC123"

    def test_real_typed_success_is_unmodified(self):
        payload = {"ok": True, "data": {"owner_token": "PRIVATE_OWNER_REF"}}
        result = host.checked_private_cli_payload(
            json.dumps(payload).encode(), self.SECRET, 0, True,
        )
        self.assertEqual(result, payload)

    def test_expected_native_failure_retains_typed_envelope(self):
        payload = {"ok": False, "error": {"code": "StaleReference",
                                          "private_ref": "PRIVATE_OWNER_REF"}}
        result = host.checked_private_cli_payload(
            json.dumps(payload).encode(), self.SECRET, 4, False,
        )
        self.assertEqual(result, payload)
        self.assertEqual(host.checked_private_cli_payload(
            json.dumps(payload).encode(), b"", 4, None
        ), payload)

    def test_malformed_native_json_never_leaks_cli_streams(self):
        try:
            host.checked_private_cli_payload(self.SECRET, self.SECRET, 4, None)
        except AssertionError as error:
            message = str(error)
            self.assertLess(len(message), 160)
            self.assertNotIn("PRIVATE_OWNER_TICKET", message)
        else:
            self.fail("Untrusted malformed native CLI response was accepted")

    def test_unexpected_true_outcome_not_printed(self):
        blob = json.dumps({"ok": False, "error": str(self.SECRET)}).encode()
        with self.assertRaises(AssertionError) as error:
            host.checked_private_cli_payload(blob, self.SECRET, 4, True)
        self.assertNotIn("PRIVATE_OWNER_TICKET", str(error.exception))

    def test_inconsistent_exit_status_fails_closed_even_for_cas_loser(self):
        blob = json.dumps({"ok": False, "error": str(self.SECRET)}).encode()
        with self.assertRaises(AssertionError) as error:
            host.checked_private_cli_payload(blob, self.SECRET, 0, None)
        self.assertNotIn("PRIVATE_OWNER_TICKET", str(error.exception))

    def test_nonobject_and_oversize_envelopes_are_refused(self):
        for blob in (b"null", b"[]", b'{"data":true}', b'{"ok":1}'):
            with self.subTest(blob=blob), self.assertRaises(AssertionError):
                host.checked_private_cli_payload(blob, self.SECRET, 0, None)
        with self.assertRaises(AssertionError) as error:
            host.checked_private_cli_payload(
                b'{"ok":true}', self.SECRET * 30000, 0, True
            )
        self.assertNotIn("PRIVATE_OWNER_TICKET", str(error.exception))

    def test_known_denial_requires_literal_true_and_preserves_original_error(self):
        for code in ("StaleReference", "InvalidArgument", "PolicyDenied"):
            for known in (False, 0, 1, "true", None):
                with self.subTest(code=code, known=known):
                    value = {"ok": False, "error": {"code": code}}
                    if known is not None:
                        value["error"]["outcome_known"] = known
                    original = copy.deepcopy(value)
                    with self.assertRaisesRegex(AssertionError, "retained UNKNOWN"):
                        host.require_known_denial(value, code, "wrong typed denial")
                    self.assertEqual(value, original)
            value = {"ok": False, "error": {"code": code, "outcome_known": True}}
            original = copy.deepcopy(value)
            host.require_known_denial(value, code, "wrong typed denial")
            self.assertEqual(value, original)


class PreviewStoreAndControlFlowTests(unittest.TestCase):
    """Python harness regressions only; no Broker/Host or product acceptance."""

    def test_store_comparison_ignores_wal_checkpoint_but_detects_rows_and_file_replacement(self):
        with tempfile.TemporaryDirectory() as raw:
            database = Path(raw) / "store.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE receipts (id INTEGER PRIMARY KEY, payload TEXT)")
                connection.execute("INSERT INTO receipts VALUES (1, 'original')")
                connection.commit()
                before = host.stored_content(database)
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.assertEqual(host.stored_content(database), before)
                connection.execute("INSERT INTO receipts VALUES (2, 'changed')")
                connection.commit()
                self.assertNotEqual(host.stored_content(database), before)
                connection.execute("DELETE FROM receipts WHERE id=2")
                connection.commit()
                self.assertEqual(host.stored_content(database), before)
                replacement = Path(raw) / "replacement.sqlite3"
                with closing(sqlite3.connect(replacement)) as destination:
                    connection.backup(destination)
            replacement.replace(database)
            after = host.stored_content(database)
            self.assertEqual(after[2], before[2])
            self.assertNotEqual(after[:2], before[:2])

    def test_unknown_preview_aborts_main_before_any_following_native_operation(self):
        # Run the real positive worker through main. An unexpected UNKNOWN in
        # the resource-override preview stops this owner and prevents starting
        # the next independently owned case, even with unchanged Store rows.
        for target, expected_calls, private in (("redirected", 10, False), ("redirected", 10, True)):
            with self.subTest(target=target, private=private), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                retained_owner = root / "retained-owner" if private else None
                if retained_owner is not None:
                    retained_owner.mkdir(mode=0o700)
                evidence = root / "evidence"
                provider = root / "provider"
                provider.write_bytes(b"transport witness only")
                state = {}
                native_calls = []
                unknown_seen = False
                bootstrap_count = 0

                def actor(_env, _database, *args):
                    if args[0] == "init":
                        project_id = str(len(state) + 1)
                        state[project_id] = {"id": project_id, "resource": "project:" + project_id,
                                             "generation": "same-generation", "revision": "0",
                                             "title": args[1]}
                    else:
                        project_id = args[1]
                        if args[0] == "rename":
                            state[project_id]["revision"] = str(int(state[project_id]["revision"]) + 1)
                            state[project_id]["title"] = args[3]
                    return copy.deepcopy(state[project_id])

                def invoke_cli(_env, _socket, session, command, args, calls, *, ok=True,
                               evidence_root=None):
                    nonlocal unknown_seen, bootstrap_count
                    if unknown_seen:
                        self.fail("A following Native operation was dispatched after UNKNOWN")
                    if command == "project.create":
                        bootstrap_count += 1
                        return {"envelope": {"ok": True, "data": {"project": f"prj_{bootstrap_count:032x}"}}}
                    binding = "binding-" + session.name
                    if command == "providers.mount.observe":
                        return {"envelope": {"ok": True, "data": {"binding_ref": binding,
                                "mount": {"root": "motionwright-data", "write": True}}}}
                    self.assertEqual(command, "providers.execute_bound")
                    request = args["request"]
                    native_command, payload = request["command"], request["args"]
                    native_calls.append(native_command)
                    data, error, phase = {}, None, None
                    if native_command == "driver.motionwright.observe":
                        if "ref" in payload:
                            error = "PolicyDenied"
                        else:
                            data = {"ref": "observed-ref", "page": {
                                "version": {"generation": state["1"]["generation"],
                                            "revision": state["1"]["revision"]},
                                "items": [{"title": state["1"]["title"]}]}}
                    elif native_command == "driver.motionwright.project.preview":
                        if "resource" in payload:
                            error, phase = "InvalidArgument", "redirected"
                        elif payload["edit"].get("title", "").startswith("stale preview"):
                            error, phase = "StaleReference", "stale"
                        else:
                            added = payload["edit"]["operation"] == "scene.add"
                            data = {"schema": "motionwright.change-preview/1",
                                    "status": "READ_ONLY_DOMAIN_VALIDATION", "resource": "project:1",
                                    "generation": state["1"]["generation"], "revision": state["1"]["revision"],
                                    "committed": False, "exact_future_ids_proven": False,
                                    "candidate_title": state["1"]["title"] if added else payload["edit"]["title"],
                                    "candidate_scene_count": int(added)}
                    else:
                        self.assertEqual(native_command, "driver.motionwright.project.rename")
                        if "actor" in payload:
                            error = "InvalidArgument"
                        elif payload["title"] in ("SDK renamed project", "Human contender", "SDK final shared state"):
                            state["1"]["title"] = payload["title"]
                            state["1"]["revision"] = str(int(state["1"]["revision"]) + 1)
                            data = {"revision": state["1"]["revision"], "project": copy.deepcopy(state["1"])}
                        else:
                            error = "StaleReference"
                    if error is not None:
                        unknown_seen = phase == target
                        outer = {"ok": False, "error": {"code": error, "outcome_known": not unknown_seen}}
                    else:
                        outer = {"ok": True, "data": {"binding_ref": binding, "result": {
                            "ok": True, "data": data, "execution": {"provenance": {
                                "provider": "driver:motionwright", "source": "driver",
                                "descriptor_sha256": "e" * 64}}}}}
                    return {"exit_code": 4 if error is not None else 0, "envelope": outer,
                            "receipt": f"{len(native_calls):032x}"}

                daemon = Mock()
                daemon.poll.return_value = None
                with ExitStack() as stack:
                    replacements = {
                        "require_exact_inputs": lambda: None,
                        "EVIDENCE": evidence, "PROVIDER_SOURCE": provider,
                        "actor": actor, "invoke_cli": invoke_cli,
                        "stored_content": lambda _database: (1, 2, "unchanged-store"),
                        "run_json": lambda _argv, *, env: {"state": env["XDG_STATE_HOME"]},
                        "running_daemon": lambda *_args, **_kwargs: nullcontext(),
                        "retain_owner_audit": lambda *_args, **_kwargs: None,
                    }
                    for name, value in replacements.items():
                        stack.enter_context(patch.object(host, name, value))
                    popen = stack.enter_context(patch.object(host.subprocess, "Popen", return_value=daemon))
                    stack.enter_context(patch.object(Path, "is_socket", return_value=True))
                    with self.assertRaisesRegex(AssertionError, "retained UNKNOWN"):
                        host.main(retained_owner=retained_owner)
                self.assertTrue(unknown_seen)
                argv = popen.call_args.args[0]
                if retained_owner is None:
                    self.assertNotIn("--driver-startup-stderr-dir", argv)
                else:
                    self.assertEqual(argv[-4:], ["--driver-startup-stderr-dir",
                                                str(retained_owner / "startup-stderr" / "shared-state"),
                                                "--driver-startup-stderr-provider", "driver:motionwright"])
                self.assertEqual(len(native_calls), expected_calls)
                self.assertEqual(native_calls[-1], "driver.motionwright.project.preview")
                self.assertFalse((evidence / "result.json").exists())
                retained = json.loads((evidence / "calls.json").read_text())
                self.assertEqual(len(retained), expected_calls)
                self.assertIs(retained[-1]["envelope"]["error"]["outcome_known"], False)


class SharedStateCallsTests(unittest.TestCase):
    """Real owner call fences with inert transport originals; no Native execution."""

    @staticmethod
    def stale_original():
        return {"ok": False, "error": {"code": "StaleReference", "outcome_known": False,
                "message": "INERT_PRIVATE_ORIGINAL", "details": {"receipt": ["original", 7]}}}

    @staticmethod
    def requests(case):
        if case == "stale-preview":
            return [("driver.motionwright.project.preview", {"ref": "original-ref", "edit": {
                "operation": "project.rename", "title": "stale preview must be rejected"}}, None)]
        titles = {"stale-ui": ["must not commit from stale ref"],
                  "stale-cas": ["old default-session ref must stay stale"],
                  "cas-race": ["Human contender", "Agent contender"]}[case]
        return [("driver.motionwright.project.rename", {"ref": "original-ref", "title": title},
                 Path("/inert-owner") / (str(index) + ".session") if case == "cas-race" else None)
                for index, title in enumerate(titles)]

    def assert_closed(self, calls, dispatch, expected_calls):
        with self.assertRaises(AssertionError):
            calls.invoke("driver.motionwright.observe", {"resource": "forbidden-followup"})
        with self.assertRaises(AssertionError):
            calls.terminal(self.requests("stale-ui"))
        self.assertEqual(dispatch.call_count, expected_calls)
        self.assertIs(calls.blocked, True)

    def orchestrated_case(self, case, owner, evidence, provider, calls, *, custody_owner,
                          capture_startup, seen, fail_case=None):
        self.assertEqual(owner, custody_owner / "cases" / case)
        self.assertEqual(provider, custody_owner / "binary" / "motionwright-native-provider")
        self.assertTrue(capture_startup)
        self.assertFalse(owner.exists())
        owner.mkdir(mode=0o700)
        evidence.mkdir(mode=0o700)
        database = owner / "motionwright-data" / "motionwright.sqlite3"
        database.parent.mkdir(mode=0o700)
        database.write_bytes(b"inert physical identity fixture")
        info = database.stat()
        seen.append((case, owner, info.st_dev, info.st_ino))
        original = ({"ok": True, "data": {"revision": "1"}} if case == "positive"
                    else self.stale_original())
        calls.append({"case": case, "owner": str(owner), "envelope": original})
        if case == fail_case:
            raise OSError("inert case original retention failed")
        terminal = None
        if case != "positive":
            outcomes = [original]
            if case == "cas-race":
                outcomes.append({"ok": True, "data": {"revision": "1"}})
            terminal = host.evaluate_terminal_case(case, outcomes)
        result = {"owner": str(owner), "database": {
            "path": str(database), "device": info.st_dev, "inode": info.st_ino},
            "provider_sha256": host.digest(provider), "native_calls": 1, "terminal": terminal}
        project = {"id": "inert-project", "final_revision": "1"} if case == "positive" else None
        return result, project

    def test_main_starts_each_case_with_independent_owner_and_database_identity(self):
        with tempfile.TemporaryDirectory(prefix="mw-hst-") as raw:
            root = Path(raw).resolve()
            owner, evidence, provider = root / "o", root / "e", root / "provider"
            owner.mkdir(mode=0o700)
            provider.write_bytes(b"inert provider fixture")
            seen = []
            def worker(*args, **kwargs):
                return self.orchestrated_case(*args, **kwargs, seen=seen)
            with patch.object(host, "ROOT", root), patch.object(host, "EVIDENCE", evidence), \
                    patch.object(host, "PROVIDER_SOURCE", provider), \
                    patch.object(host, "require_exact_inputs"), \
                    patch.object(host, "run_shared_case", side_effect=worker) as dispatched, \
                    patch("builtins.print"):
                host.main(retained_owner=owner)
            expected = ["positive", "stale-ui", "stale-preview", "stale-cas", "cas-race"]
            self.assertEqual([row[0] for row in seen], expected)
            self.assertEqual(dispatched.call_count, 5)
            self.assertEqual(len({row[1] for row in seen}), 5)
            self.assertEqual(len({row[2:] for row in seen}), 5)
            retained = json.loads((evidence / "calls.json").read_text())
            self.assertEqual([row["case"] for row in retained], expected)
            for row in retained[1:]:
                self.assertEqual(row["envelope"], self.stale_original())
                self.assertIs(row["envelope"]["error"]["outcome_known"], False)
            result = json.loads((evidence / "result.json").read_text())
            self.assertEqual(set(result["cases"]), set(expected))
            for case in expected[1:]:
                self.assertIs(result["cases"][case]["terminal"]["effects_absent_asserted"], False)

    def test_full_five_case_controlflow_retains_real_cli_streams_and_reaches_publisher(self):
        # This exercises the real Python main, workers, CLI custody and publisher.
        # Only Native process transport and the Service actor are inert; SQLite
        # persistence, physical file identities and stdout/stderr writes are real.
        import threading
        with tempfile.TemporaryDirectory(prefix="mw-h27-") as raw:
            root = Path(raw).resolve()
            owner, evidence, provider = root / "o", root / "e", root / "provider"
            owner.mkdir(mode=0o700)
            provider.write_bytes(b"inert provider binary for Python controlflow only")
            (root / "Cargo.toml").write_bytes((host.ROOT / "Cargo.toml").read_bytes())
            refs, native, sealed = {}, {}, set()
            critical = threading.Lock()
            contenders = threading.Barrier(2, timeout=2)
            created = 0
            private_stderr = b"INERT_PRIVATE_CLI_STDERR\x00original\n"

            def actor(_env, database, *args):
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("CREATE TABLE IF NOT EXISTS projects "
                                       "(id TEXT PRIMARY KEY, title TEXT, revision INTEGER)")
                    if args[0] == "init":
                        ordinal = connection.execute("SELECT count(*) FROM projects").fetchone()[0] + 1
                        project_id = f"{ordinal:08x}-0000-4000-8000-{ordinal:012x}"
                        connection.execute("INSERT INTO projects VALUES (?, ?, 0)", (project_id, args[1]))
                    else:
                        project_id = args[1]
                        if args[0] == "rename":
                            connection.execute("UPDATE projects SET title=?, revision=revision+1 WHERE id=?",
                                               (args[3], project_id))
                        else:
                            self.assertEqual(args[0], "show")
                    connection.commit()
                    row = connection.execute("SELECT title, revision FROM projects WHERE id=?",
                                             (project_id,)).fetchone()
                return {"id": project_id, "resource": "project:" + project_id,
                        "generation": "10000000-0000-4000-8000-000000000001",
                        "revision": str(row[1]), "title": row[0]}

            def cli(argv, **kwargs):
                nonlocal created
                self.assertEqual(argv[0], str(host.SW_BINS / "semwright"))
                command = argv[argv.index("execute") + 1]
                args = json.loads(argv[argv.index("--args-json") + 1])
                session = Path(argv[argv.index("--session-file") + 1])
                case_owner = session.parent.parent
                case = case_owner.name
                database = case_owner / "motionwright-data" / "motionwright.sqlite3"
                self.assertNotIn(case, sealed, "CLI dispatched after this owner's terminal batch")
                if (case == "cas-race" and command == "providers.execute_bound"
                        and args["request"]["command"] == "driver.motionwright.project.rename"):
                    contenders.wait()
                with critical:
                    self.assertNotIn(case, sealed, "CLI continued after terminal original")
                    binding = f"inert-binding-{case}-{session.name}"
                    if command == "project.create":
                        created += 1
                        outer = {"ok": True, "data": {"project": f"prj_{created:032x}"}}
                    elif command == "providers.mount.observe":
                        outer = {"ok": True, "data": {
                            "provider": "driver:motionwright", "binding_ref": binding,
                            "connection": "inert-connection-" + case, "provider_generation": 1,
                            "mount": {"root": "motionwright-data", "path": str(database.parent),
                                      "read": True, "write": True, "execute": False}}}
                    else:
                        self.assertEqual(command, "providers.execute_bound")
                        self.assertEqual(args["binding_ref"], binding)
                        self.assertIs(args["request"]["dry_run"], False)
                        request = args["request"]
                        name, payload = request["command"], request["args"]
                        native.setdefault(case, []).append((name, copy.deepcopy(payload), session.name))
                        data, code, known = {}, None, True
                        if name == "driver.motionwright.observe":
                            if "ref" in payload:
                                code = "PolicyDenied"
                            else:
                                current = actor({}, database, "show", payload["resource"].split(":", 1)[1])
                                reference = f"inert-ref-{case}-{len(refs)}"
                                refs[reference] = (case, session.name, current["id"], current["revision"])
                                data = {"ref": reference, "page": {"version": {
                                    "generation": current["generation"], "revision": current["revision"]},
                                    "items": [{"title": current["title"]}]}}
                        else:
                            ref_case, ref_session, project_id, revision = refs[payload["ref"]]
                            self.assertEqual((ref_case, ref_session), (case, session.name))
                            current = actor({}, database, "show", project_id)
                            if "actor" in payload or "resource" in payload:
                                code = "InvalidArgument"
                            elif revision != current["revision"]:
                                code, known = "StaleReference", False
                            elif name == "driver.motionwright.project.preview":
                                edit = payload["edit"]
                                added = edit["operation"] == "scene.add"
                                data = {"schema": "motionwright.change-preview/1",
                                        "status": "READ_ONLY_DOMAIN_VALIDATION", "resource": current["resource"],
                                        "generation": current["generation"], "revision": current["revision"],
                                        "committed": False, "exact_future_ids_proven": False,
                                        "candidate_title": current["title"] if added else edit["title"],
                                        "candidate_scene_count": int(added)}
                            else:
                                self.assertEqual(name, "driver.motionwright.project.rename")
                                changed = actor({}, database, "rename", project_id, "inert-sdk-write", payload["title"])
                                data = {"revision": changed["revision"], "project": changed}
                        if code is None:
                            outer = {"ok": True, "data": {"binding_ref": binding, "result": {
                                "ok": True, "data": data, "execution": {"provenance": {
                                    "provider": "driver:motionwright", "source": "driver",
                                    "descriptor_sha256": "e" * 64}}}}}
                        else:
                            outer = {"ok": False, "error": {"code": code, "outcome_known": known,
                                     "message": "INERT_PRIVATE_ERROR_ORIGINAL", "details": {"receipt": "unchanged"}}}
                            if not known:
                                sealed.add(case)
                    stdout = (json.dumps(outer, sort_keys=True) + "\n").encode()
                    return subprocess.CompletedProcess(argv, 0 if outer["ok"] else 4, stdout, private_stderr)

            daemon = Mock()
            daemon.poll.return_value = None
            daemon.wait.return_value = 0
            with ExitStack() as stack:
                for name, value in {
                    "ROOT": root, "EVIDENCE": evidence, "PROVIDER_SOURCE": provider,
                    "GITHUB_SHA": SOURCE, "PIN": PIN,
                    "require_exact_inputs": lambda: None, "actor": actor,
                    "run_json": lambda _argv, *, env: {"state": env["XDG_STATE_HOME"]},
                    "running_daemon": lambda *_args, **_kwargs: nullcontext(),
                }.items():
                    stack.enter_context(patch.object(host, name, value))
                launched = stack.enter_context(patch.object(host.subprocess, "Popen", return_value=daemon))
                transported = stack.enter_context(patch.object(host.subprocess, "run", side_effect=cli))
                stack.enter_context(patch.object(Path, "is_socket", return_value=True))
                stack.enter_context(patch("builtins.print"))
                host.main(retained_owner=owner)
            counts = {"positive": 12, "stale-ui": 2, "stale-preview": 3, "stale-cas": 4, "cas-race": 6}
            self.assertEqual({case: len(rows) for case, rows in native.items()}, counts)
            self.assertEqual(sealed, set(counts) - {"positive"})
            self.assertEqual(launched.call_count, 5)
            self.assertEqual(transported.call_count, 46)
            result = json.loads((evidence / "result.json").read_text())
            self.assertEqual(len({(row["database"]["device"], row["database"]["inode"])
                                  for row in result["cases"].values()}), 5)
            race = native["cas-race"]
            self.assertEqual({row[2]: row[1]["ref"] for row in race[2:4]},
                             {row[2]: row[1]["ref"] for row in race[4:]})
            calls = json.loads((evidence / "calls.json").read_text())
            self.assertEqual(len(calls), 27)
            for case in counts:
                original_dir = evidence / case
                entries = json.loads((original_dir / "transport-calls.original.json").read_text())
                for entry in entries:
                    stdout = original_dir / (entry["receipt"] + ".stdout.original")
                    stderr = original_dir / (entry["receipt"] + ".stderr.original")
                    self.assertEqual(stdout.read_bytes(), (json.dumps(entry["envelope"], sort_keys=True) + "\n").encode())
                    self.assertEqual(stderr.read_bytes(), private_stderr)
                    self.assertEqual(stdout.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(stderr.stat().st_mode & 0o777, 0o600)
                if case != "positive":
                    tail = 2 if case == "cas-race" else 1
                    case_calls = [entry for entry in calls if entry["case"] == case]
                    self.assertEqual({entry["receipt"] for entry in entries[-tail:]},
                                     {entry["receipt"] for entry in case_calls[-tail:]})
                    failed = [entry for entry in case_calls if entry["envelope"]["ok"] is False]
                    self.assertEqual(len(failed), 1)
                    self.assertIs(failed[0]["envelope"]["error"]["outcome_known"], False)
                    self.assertEqual(failed[0]["envelope"]["error"]["details"], {"receipt": "unchanged"})
            publication = pub.publish(evidence, root / "public", SOURCE, PIN, "success")
            self.assertEqual(publication["verified_native_invocations"], 27)
            self.assertEqual(publication["verified_terminal_driver_cases"], 4)
            self.assertIs(publication["terminal_driver_outcomes_known"], False)
            self.assertIs(publication["effects_absent_asserted"], False)
            public_bytes = (root / "public" / "public-evidence.json").read_bytes()
            self.assertNotIn(b"INERT_PRIVATE", public_bytes)
            self.assertNotIn(b"inert-ref-", public_bytes)

    def test_main_custody_failure_prevents_following_case_and_preserves_failed_ledger(self):
        with tempfile.TemporaryDirectory(prefix="mw-hst-") as raw:
            root = Path(raw).resolve()
            owner, evidence, provider = root / "o", root / "e", root / "provider"
            owner.mkdir(mode=0o700)
            provider.write_bytes(b"inert provider fixture")
            seen = []
            def worker(*args, **kwargs):
                return self.orchestrated_case(*args, **kwargs, seen=seen, fail_case="stale-ui")
            with patch.object(host, "ROOT", root), patch.object(host, "EVIDENCE", evidence), \
                    patch.object(host, "PROVIDER_SOURCE", provider), \
                    patch.object(host, "require_exact_inputs"), \
                    patch.object(host, "run_shared_case", side_effect=worker) as dispatched:
                with self.assertRaisesRegex(OSError, "original retention failed"):
                    host.main(retained_owner=owner)
            self.assertEqual([row[0] for row in seen], ["positive", "stale-ui"])
            self.assertEqual(dispatched.call_count, 2)
            self.assertFalse((owner / "cases" / "stale-preview").exists())
            self.assertFalse((evidence / "result.json").exists())
            retained = json.loads((evidence / "calls.json").read_text())
            self.assertEqual([row["case"] for row in retained], ["positive", "stale-ui"])
            self.assertEqual(retained[-1]["envelope"], self.stale_original())

    def test_success_and_known_denial_preserve_originals_without_closing_owner(self):
        success = {"ok": True, "data": {"ref": "original-ref", "revision": "3"}}
        denied = {"ok": False, "error": {"code": "PolicyDenied", "outcome_known": True,
                                         "details": {"original": "untouched"}}}
        before = copy.deepcopy([success, denied])
        dispatch = Mock(side_effect=[success, denied, success])
        calls = host.SharedStateCalls("positive", dispatch)
        self.assertIs(calls.invoke("driver.motionwright.observe", {}), success)
        actual = calls.invoke("driver.motionwright.observe", {"ref": "foreign-ref"}, ok=False)
        self.assertIs(actual, denied)
        host.require_known_denial(actual, "PolicyDenied", "inert known denial")
        self.assertIs(calls.invoke("driver.motionwright.observe", {}), success)
        self.assertFalse(calls.blocked)
        self.assertEqual([success, denied], before)

    def test_unknown_or_malformed_invoke_stops_without_rewriting_the_original(self):
        malformed = [None, [], {}, {"ok": 1}, {"ok": None}, {"ok": False, "error": None}]
        malformed += [{"ok": False, "error": {"code": "StaleReference", "outcome_known": known}}
                      for known in (False, 0, 1, "true", None)]
        for original in malformed:
            with self.subTest(original=original):
                before = copy.deepcopy(original)
                dispatch = Mock(return_value=original)
                calls = host.SharedStateCalls("positive", dispatch)
                with self.assertRaises(AssertionError):
                    calls.invoke("driver.motionwright.project.preview", {"ref": "original-ref"}, ok=False)
                self.assertEqual(original, before)
                self.assertIsNone(calls.terminal_evaluation)
                self.assert_closed(calls, dispatch, 1)

    def test_transport_and_custody_failures_close_both_regular_and_terminal_paths(self):
        for terminal in (False, True):
            for failure in (OSError("inert custody write failed"),
                            subprocess.TimeoutExpired(["inert-command"], 1),
                            AssertionError("inert original readback failed")):
                with self.subTest(terminal=terminal, failure=type(failure).__name__):
                    dispatch = Mock(side_effect=failure)
                    calls = host.SharedStateCalls("stale-ui", dispatch)
                    with self.assertRaises(type(failure)) as seen:
                        if terminal:
                            calls.terminal(self.requests("stale-ui"))
                        else:
                            calls.invoke("driver.motionwright.observe", {})
                    self.assertIs(seen.exception, failure)
                    self.assertIsNone(calls.terminal_evaluation)
                    self.assert_closed(calls, dispatch, 1)

    def test_declared_terminal_negatives_seal_before_dispatch_and_retain_complete_error(self):
        for case in ("stale-ui", "stale-preview", "stale-cas"):
            with self.subTest(case=case):
                original = self.stale_original()
                before = copy.deepcopy(original)
                calls = None
                def transport(command, args, *, ok, session_path):
                    self.assertIs(calls.blocked, True)
                    self.assertIsNone(ok)
                    with self.assertRaises(AssertionError):
                        calls.invoke("forbidden-reentrant-operation", {})
                    return original
                dispatch = Mock(side_effect=transport)
                calls = host.SharedStateCalls(case, dispatch)
                observed = calls.terminal(self.requests(case))
                self.assertEqual(len(observed), 1)
                self.assertIs(observed[0], original)
                self.assertEqual(original, before)
                self.assertEqual(calls.terminal_evaluation, {
                    "case": case, "evaluation": "EXPECTED_TERMINAL_UNKNOWN", "outcome_known": False,
                    "scope_terminal": True, "effects_absent_asserted": False})
                self.assert_closed(calls, dispatch, 1)

    def test_terminal_unexpected_error_known_true_and_success_never_pass(self):
        invalid = [
            {"ok": False, "error": {"code": "Internal", "outcome_known": False}},
            {"ok": False, "error": {"code": "StaleReference", "outcome_known": True}},
            {"ok": True, "data": {"revision": "4"}},
            {"ok": False, "error": {"code": "StaleReference", "outcome_known": 0}},
            {"ok": False, "error": {"code": "StaleReference"}},
            {"ok": False, "error": "malformed"},
        ]
        for original in invalid:
            with self.subTest(original=original):
                before = copy.deepcopy(original)
                dispatch = Mock(return_value=original)
                calls = host.SharedStateCalls("stale-ui", dispatch)
                with self.assertRaises(AssertionError):
                    calls.terminal(self.requests("stale-ui"))
                self.assertEqual(original, before)
                self.assertIsNone(calls.terminal_evaluation)
                self.assert_closed(calls, dispatch, 1)

    def test_terminal_selection_rejects_foreign_requests_before_any_dispatch(self):
        variants = [[], self.requests("stale-ui") * 2, self.requests("stale-preview"),
                    [("driver.motionwright.project.rename", {"ref": "original-ref", "title": "other"}, None)],
                    [("driver.motionwright.project.rename", {"ref": "original-ref",
                      "title": "must not commit from stale ref", "resource": "foreign"}, None)]]
        for requests in variants:
            with self.subTest(requests=requests):
                dispatch = Mock()
                calls = host.SharedStateCalls("stale-ui", dispatch)
                with self.assertRaises(AssertionError):
                    calls.terminal(requests)
                self.assert_closed(calls, dispatch, 0)
        for sessions in ((None, None), (Path("/same.session"), Path("/same.session"))):
            requests = [(command, args, session) for (command, args, _), session
                        in zip(self.requests("cas-race"), sessions, strict=True)]
            dispatch = Mock()
            calls = host.SharedStateCalls("cas-race", dispatch)
            with self.assertRaises(AssertionError):
                calls.terminal(requests)
            self.assert_closed(calls, dispatch, 0)
        dispatch = Mock()
        with self.assertRaises(AssertionError):
            host.SharedStateCalls("undeclared", dispatch)
        with self.assertRaises(AssertionError):
            host.SharedStateCalls("positive", dispatch).terminal(self.requests("stale-ui"))
        dispatch.assert_not_called()

    def test_cas_batch_schedules_both_original_calls_and_never_a_third(self):
        import threading
        for loser in (0, 1):
            with self.subTest(loser=loser):
                requests = self.requests("cas-race")
                originals = [{"ok": True, "data": {"revision": "4"}} for _ in range(2)]
                originals[loser] = self.stale_original()
                before = copy.deepcopy(originals)
                rendezvous = threading.Barrier(2, timeout=2)
                calls = None
                def transport(command, args, *, ok, session_path):
                    self.assertIs(calls.blocked, True)
                    self.assertIsNone(ok)
                    index = next(i for i, request in enumerate(requests) if request[2] == session_path)
                    rendezvous.wait()
                    return originals[index]
                dispatch = Mock(side_effect=transport)
                calls = host.SharedStateCalls("cas-race", dispatch)
                observed = calls.terminal(requests)
                self.assertIs(observed[0], originals[0])
                self.assertIs(observed[1], originals[1])
                self.assertEqual(originals, before)
                self.assertEqual(calls.terminal_evaluation["evaluation"], "EXPECTED_TERMINAL_UNKNOWN")
                self.assert_closed(calls, dispatch, 2)

    def test_cas_batch_rejects_two_successes_two_unknowns_and_known_loser(self):
        success = {"ok": True, "data": {"revision": "4"}}
        unknown = self.stale_original()
        known = {"ok": False, "error": {"code": "StaleReference", "outcome_known": True}}
        for originals in ([success, success], [unknown, unknown], [success, known]):
            with self.subTest(originals=originals):
                before = copy.deepcopy(originals)
                requests = self.requests("cas-race")
                def transport(command, args, *, ok, session_path):
                    return originals[next(i for i, request in enumerate(requests) if request[2] == session_path)]
                dispatch = Mock(side_effect=transport)
                calls = host.SharedStateCalls("cas-race", dispatch)
                with self.assertRaises(AssertionError):
                    calls.terminal(requests)
                self.assertEqual(originals, before)
                self.assertIsNone(calls.terminal_evaluation)
                self.assert_closed(calls, dispatch, 2)


@unittest.skipUnless(sys.platform == "linux", "private SDK startup observation is Linux-only")
class DriverStartupStderrSelectionTests(unittest.TestCase):
    """Consumer selection and transport witnesses only; no Broker/Native execution."""

    def test_private_selection_has_closed_paths_and_never_reuses_an_attempt(self):
        with tempfile.TemporaryDirectory() as raw:
            owner = Path(raw).resolve()
            missing = owner / "not-created"
            self.assertEqual(host.driver_startup_stderr_args(missing, "bootstrap", selected=False), [])
            self.assertFalse(missing.exists())
            for label in ("bootstrap", "../escape", "unknown"):
                with self.assertRaisesRegex(AssertionError, "PRIVATE_STARTUP_STDERR_SELECTION"):
                    host.driver_startup_stderr_args(owner, label, selected=True)
            self.assertFalse((owner / "startup-stderr").exists())
            for label in ("shared-state", "authorized", "reopened", "withdrawn", "restored",
                          "stale-ui", "stale-preview", "stale-cas", "cas-race", "source-stale"):
                argv = host.driver_startup_stderr_args(owner, label, selected=True)
                directory = owner / "startup-stderr" / label
                self.assertEqual(argv, ["--driver-startup-stderr-dir", str(directory),
                                        "--driver-startup-stderr-provider", "driver:motionwright"])
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual(directory.parent.stat().st_mode & 0o777, 0o700)
                original = directory / "stderr.original"
                original.write_bytes(b"unmodified original failure")
                with self.assertRaises(FileExistsError):
                    host.driver_startup_stderr_args(owner, label, selected=True)
                self.assertEqual(original.read_bytes(), b"unmodified original failure")

    def test_private_selection_rejects_linked_or_nonprivate_parent_before_launch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            owner, outside = root / "owner", root / "outside"
            owner.mkdir(mode=0o700)
            outside.mkdir(mode=0o700)
            (owner / "startup-stderr").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(AssertionError, "PRIVATE_STARTUP_STDERR_PARENT"):
                host.driver_startup_stderr_args(owner, "shared-state", selected=True)
            self.assertEqual(list(outside.iterdir()), [])
            (owner / "startup-stderr").unlink()
            owner.chmod(0o755)
            with self.assertRaisesRegex(AssertionError, "PRIVATE_STARTUP_STDERR_OWNER"):
                host.driver_startup_stderr_args(owner, "shared-state", selected=True)
            self.assertFalse((owner / "startup-stderr").exists())

    def test_capsule_bootstrap_has_no_observer_and_selected_driver_uses_real_daemon_argv(self):
        for label in ("bootstrap", "authorized", "reopened", "withdrawn", "restored"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                owner, evidence = root / "owner", root / "evidence"
                owner.mkdir(mode=0o700)
                evidence.mkdir(mode=0o700)
                (owner / "runtime").mkdir(mode=0o700)
                config = owner / "owner.toml"
                config.write_text("drivers = []\n")
                custody = Mock()
                custody.new_json.side_effect = host.write_json
                admitted = {"custody": custody, "evidence": evidence, "owner": owner,
                            "source": {}, "processes": [], "shutdown": [], "unknown": False}
                process = Mock(pid=4242)
                process.poll.return_value = 0
                with ExitStack() as stack:
                    popen = stack.enter_context(patch.object(host.subprocess, "Popen", return_value=process))
                    stack.enter_context(patch.object(Path, "is_socket", return_value=True))
                    stack.enter_context(patch.object(host, "capsule_pid_identity", return_value={"pid": 4242}))
                    stack.enter_context(patch.object(host, "capsule_track_descendants"))
                    stack.enter_context(patch.object(host, "capsule_stop_group", return_value=True))
                    stack.enter_context(patch.object(host, "capsule_stop_descendants", return_value=True))
                    with host.capsule_daemon(admitted, label, config, {}):
                        pass
                self.assertEqual(popen.call_count, 1)
                argv = popen.call_args.args[0]
                self.assertEqual(argv[:5], [str(host.SW_BINS / "semwrightd"), "--config", str(config),
                                            "--socket", str(owner / "runtime" / f"{label}.sock")])
                if label == "bootstrap":
                    self.assertEqual(len(argv), 5)
                    self.assertFalse((owner / "startup-stderr").exists())
                else:
                    self.assertEqual(argv[5:], ["--driver-startup-stderr-dir",
                                                str(owner / "startup-stderr" / label),
                                                "--driver-startup-stderr-provider", "driver:motionwright"])

    def test_workflow_builds_feature_only_for_existing_private_selection(self):
        yml = (Path(__file__).resolve().parents[2] / ".github/workflows/native-sdk.yml").read_text()
        def step(name):
            return yml.split("      - name: " + name + "\n", 1)[1].split("\n      - ", 1)[0]
        build = step("Build exact Semwright daemon, CLI and sandbox")
        selected = step("Real UI-service to Native Host shared-state acceptance")
        expression = r"^\s+MW_SHARED_STATE_CUSTODY: (.+)$"
        self.assertEqual(re.search(expression, build, re.MULTILINE).group(1),
                         re.search(expression, selected, re.MULTILINE).group(1))
        self.assertIn("if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.g1_only) }}",
                      yml.split("  real-host-shared-state:\n", 1)[1])
        script = textwrap.dedent(build.split("        run: |\n", 1)[1])
        for selection in ("", "0", "1"):
            with self.subTest(selection=selection):
                result = subprocess.run(
                    ["bash", "-c", 'cargo() { printf "%s\\n" "$*"; }\n' + script],
                    env={**os.environ, "MW_SHARED_STATE_CUSTODY": selection},
                    capture_output=True, text=True, check=True,
                )
                calls = result.stdout.splitlines()
                self.assertEqual(len(calls), 2)
                expected = "build --locked -p semwright-daemon -p semwright-cli --bins"
                if selection == "1":
                    expected += " --features semwright-daemon/private-driver-startup-stderr"
                self.assertEqual(calls[0], expected)
                self.assertEqual(calls[1], "build --locked -p semwright-plugin-host --bin semwright-sandbox")


class CapsuleSourceTransportShapeTests(unittest.TestCase):
    def test_source_terminal_retains_original_unknown_and_fences_every_later_launch(self):
        # Transport/retention control only: the provider is never executed here.
        with tempfile.TemporaryDirectory() as temporary:
            owner = Path(temporary).resolve()
            evidence = owner / "evidence"
            evidence.mkdir()
            error = {"code": "StaleReference", "outcome_known": False,
                     "message": "unmodified private error", "progress": {"done": 1},
                     "candidates": ["retained"], "native_diagnostic": {"detail": "original"}}
            envelope = {"ok": False, "error": error, "private": "original envelope"}
            stdout = json.dumps(envelope).encode()
            admitted = {"owner": owner, "evidence": evidence, "terminal_source": True,
                        "transport_calls": [], "bindings": ["genuine-bound-ref"],
                        "unknown": False, "failed": False}
            initial = {"resource": "project:one", "capsule_id": "source-one", "revision": "0"}
            edited = {**initial, "revision": "1"}
            result = Mock(returncode=4, stdout=stdout, stderr=b"private original stderr")
            with patch.object(host.subprocess, "run", return_value=result) as run, \
                    patch.object(host.subprocess, "Popen") as launch, \
                    patch.object(host, "capsule_track_descendants"):
                host.capsule_source_terminal_read(admitted, {}, owner / "owner.sock", initial, edited,
                                                  {"ref": "original-source-ref"})
                self.assertIs(admitted["unknown"], True)
                self.assertIs(admitted["terminal_started"], True)
                self.assertIs(admitted["terminal_evaluation"]["outcome_known"], False)
                with self.assertRaisesRegex(AssertionError, "NO_FOLLOWUP"):
                    host.capsule_cli(admitted, {}, owner / "owner.sock", owner / "other.session",
                                     "forbidden-followup", {})
                with self.assertRaisesRegex(AssertionError, "NO_PROCESS"):
                    host.capsule_process(admitted, "forbidden-followup", ["not-executed"], {})
                with self.assertRaisesRegex(AssertionError, "NO_DAEMON"):
                    with host.capsule_daemon(admitted, "restored", owner / "config", {}):
                        self.fail("a terminal owner opened another daemon")
                self.assertEqual(run.call_count, 1)
                launch.assert_not_called()
            retained = admitted["transport_calls"][0]
            self.assertEqual(retained["envelope"], envelope)
            self.assertEqual((evidence / f"{retained['receipt']}.stdout.original").read_bytes(), stdout)
            self.assertEqual((evidence / f"{retained['receipt']}.stderr.original").read_bytes(),
                             b"private original stderr")
            self.assertEqual(admitted["terminal_evaluation"]["receipt"], retained["receipt"])
            self.assertEqual(retained["args"]["request"], {
                "command": "driver.motionwright.observe", "dry_run": False,
                "args": {"resource": "project:one", "scope": "native-capsule-source-inspection:source-one",
                         "limit": 1, "ref": "original-source-ref"},
            })

    def test_source_terminal_unexpected_outcomes_and_custody_failure_cannot_pass_or_reopen(self):
        variants = [
            {"ok": True, "data": {}},
            {"ok": False, "error": {"code": "StaleReference", "outcome_known": True}},
            {"ok": False, "error": {"code": "Internal", "outcome_known": False}},
            {"ok": False, "error": None},
            {"ok": False, "error": {"code": "StaleReference", "outcome_known": 0}},
        ]
        for envelope in variants:
            with self.subTest(envelope=envelope), tempfile.TemporaryDirectory() as temporary:
                owner = Path(temporary).resolve()
                admitted = {"owner": owner, "evidence": owner, "terminal_source": True,
                            "transport_calls": [], "bindings": ["bound"], "unknown": False, "failed": False}
                initial = {"resource": "project:one", "capsule_id": "source-one", "revision": "0"}
                result = Mock(returncode=0 if envelope["ok"] else 4,
                              stdout=json.dumps(envelope).encode(), stderr=b"")
                with patch.object(host.subprocess, "run", return_value=result) as run, \
                        patch.object(host, "capsule_track_descendants"):
                    with self.assertRaisesRegex(AssertionError, "TERMINAL_OUTCOME_UNEXPECTED"):
                        host.capsule_source_terminal_read(admitted, {}, owner / "socket", initial,
                                                          {**initial, "revision": "1"}, {"ref": "original"})
                    with self.assertRaisesRegex(AssertionError, "TERMINAL_PREPARATION"):
                        host.capsule_source_terminal_read(admitted, {}, owner / "socket", initial,
                                                          {**initial, "revision": "1"}, {"ref": "original"})
                self.assertEqual(run.call_count, 1)
                self.assertNotIn("terminal_evaluation", admitted)
                self.assertEqual(admitted["transport_calls"][0]["envelope"], envelope)
                self.assertIs(admitted["unknown"], True)
        with tempfile.TemporaryDirectory() as temporary:
            owner = Path(temporary).resolve()
            admitted = {"owner": owner, "evidence": owner, "terminal_source": True,
                        "transport_calls": [], "bindings": ["bound"], "unknown": False, "failed": False}
            initial = {"resource": "project:one", "capsule_id": "source-one", "revision": "0"}
            with patch.object(host, "invoke_cli", side_effect=OSError("retention failed")) as dispatch:
                with self.assertRaises(OSError):
                    host.capsule_source_terminal_read(admitted, {}, owner / "socket", initial,
                                                      {**initial, "revision": "1"}, {"ref": "original"})
                with self.assertRaisesRegex(AssertionError, "NO_FOLLOWUP"):
                    host.capsule_cli(admitted, {}, owner / "socket", owner / "session", "forbidden", {})
            self.assertEqual(dispatch.call_count, 1)
            self.assertNotIn("terminal_evaluation", admitted)

    def test_unknown_or_failed_capsule_owner_cannot_start_process_or_daemon(self):
        for reason in ("unknown", "failed", "terminal_started"):
            with self.subTest(reason=reason), patch.object(host.subprocess, "Popen") as launch:
                admitted = {reason: True}
                with self.assertRaisesRegex(AssertionError, "NO_PROCESS"):
                    host.capsule_process(admitted, "later", ["not-executed"], {})
                with self.assertRaisesRegex(AssertionError, "NO_DAEMON"):
                    with host.capsule_daemon(admitted, "later", Path("not-opened"), {}):
                        self.fail("closed owner opened a daemon")
                launch.assert_not_called()

    def test_capsule_parent_retains_unknown_from_failed_terminal_owner(self):
        # Exercise the real capsule_main orchestration and final persistence.
        # Only the physical case worker and encryption boundary are inert.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            records = {}
            custody = Mock()
            custody.validate_context.return_value = {
                "source_sha": SOURCE, "semwright_sha": PIN, "run_id": "12345", "run_attempt": "1"}
            custody.strict.return_value = {"sha256": "c" * 64}
            custody.regular.return_value = (b"source witness", {})
            custody.gather_snapshot.return_value = (b"inert snapshot", True)
            custody.encrypt_snapshot.return_value = b"inert ciphertext"
            custody.new_json.side_effect = lambda path, value: records.setdefault(str(path), copy.deepcopy(value))
            cohorts = []

            def physical_case(admitted):
                cohorts.append(admitted)
                admitted["completed_stages"] = list(host.CAPSULE_STAGES)
                admitted["provider"] = root / "one-provider"
                admitted["shutdown"] = [True]
                if admitted.get("terminal_source"):
                    admitted["completed_stages"] = list(host.CAPSULE_STAGES[:2])
                    admitted["terminal_started"] = True
                    admitted["unknown"] = True
                    raise AssertionError("unexpected original terminal error retained")

            versions = {"cryptography": "50.0.2", "cffi": "2.1.1", "pycparser": "2.23"}
            with patch.dict(sys.modules, {"ci_private_evidence": custody}), \
                    patch.dict(os.environ, {"RUNNER_TEMP": str(root), "GITHUB_EVENT_NAME": "workflow_dispatch",
                                            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "12345"}), \
                    patch("importlib.metadata.version", side_effect=versions.__getitem__), \
                    patch.object(host, "ROOT", root), patch.object(host, "GITHUB_SHA", SOURCE), \
                    patch.object(host, "PIN", PIN), patch.object(host, "require_exact_inputs"), \
                    patch.object(host, "capsule_source_identity", return_value={"source": SOURCE}), \
                    patch.object(host, "capsule_run", side_effect=physical_case), \
                    patch.object(host, "digest", return_value="d" * 64), \
                    patch.object(host.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(AssertionError, "NOT_VERIFIED_OR_PARTIAL_PRIVATE_CUSTODY"):
                    host.capsule_main()
                launch.assert_not_called()
            self.assertEqual(len(cohorts), 2)
            self.assertIs(cohorts[0]["unknown"], False)
            self.assertIs(cohorts[1]["unknown"], True)
            self.assertIs(cohorts[1]["failed"], True)
            parent_path = str(root / "verification/native-sdk/github-actions/capsule-source" / SOURCE
                              / "evidence/runner.terminal.json")
            self.assertEqual(records[parent_path]["status"], "UNKNOWN")
            self.assertEqual(records[parent_path]["failure"]["error"], "unexpected original terminal error retained")
            self.assertIs(records[parent_path]["effects_absent_asserted"], False)
            self.assertIs(records[parent_path]["terminal_source_case"]["outcome_known"], False)

    def test_malformed_or_unknown_error_retains_original_and_stops_before_followup(self):
        for error in (None, [], "malformed", {"code": "Internal", "outcome_known": False}):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary:
                evidence = Path(temporary)
                envelope = {"ok": False, "error": error, "private": "OWNER_PRIVATE_SOURCE"}
                stdout = json.dumps(envelope).encode()
                stderr = b"OWNER_PRIVATE_DIAGNOSTIC"
                admitted = {"evidence": evidence, "transport_calls": [], "unknown": False}
                result = Mock(returncode=4, stdout=stdout, stderr=stderr)
                with patch.object(host.subprocess, "run", return_value=result) as dispatched, \
                        patch.object(host, "capsule_track_descendants"):
                    with self.assertRaisesRegex(AssertionError, "E11_NATIVE_OUTCOME_UNKNOWN_RETAINED"):
                        host.capsule_cli(admitted, {}, evidence / "owner.sock",
                                         evidence / "owner.session", "providers.execute_bound", {}, ok=False)
                        host.capsule_cli(admitted, {}, evidence / "owner.sock",
                                         evidence / "owner.session", "forbidden-followup", {})
                self.assertEqual(dispatched.call_count, 1)
                self.assertIs(admitted["unknown"], True)
                self.assertEqual(len(admitted["transport_calls"]), 1)
                retained = admitted["transport_calls"][0]
                self.assertEqual(retained["envelope"], envelope)
                self.assertEqual(retained["exit_code"], 4)
                self.assertIs(retained["automatic_retry"], False)
                self.assertEqual((evidence / f"{retained['receipt']}.stdout.original").read_bytes(), stdout)
                self.assertEqual((evidence / f"{retained['receipt']}.stderr.original").read_bytes(), stderr)

    def test_original_known_denial_is_returned_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            envelope = {"ok": False, "error": {"code": "PolicyDenied", "outcome_known": True}}
            stdout = json.dumps(envelope).encode()
            admitted = {"evidence": evidence, "transport_calls": [], "unknown": False}
            result = Mock(returncode=4, stdout=stdout, stderr=b"")
            with patch.object(host.subprocess, "run", return_value=result) as dispatched, \
                    patch.object(host, "capsule_track_descendants"):
                actual = host.capsule_cli(admitted, {}, evidence / "owner.sock",
                                          evidence / "owner.session", "providers.execute_bound", {}, ok=False)
            self.assertEqual(dispatched.call_count, 1)
            self.assertEqual(actual, envelope)
            self.assertIs(admitted["unknown"], False)
            self.assertEqual(admitted["transport_calls"][0]["envelope"], envelope)


if __name__ == "__main__":
    unittest.main()
