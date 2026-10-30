"""Adversarial v0.1 public Native SDK shared-state artifact admission."""
from __future__ import annotations

import copy
from contextlib import ExitStack, closing, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
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
        outcomes = (
            [("driver.motionwright.observe", error) for error in
             (None, None, "PolicyDenied", None, None, None)]
            + [("driver.motionwright.project.rename", error) for error in
               (None, "StaleReference", "InvalidArgument", None, "StaleReference", "StaleReference", None)]
            + [("driver.motionwright.project.preview", error) for error in
               (None, None, "InvalidArgument", "StaleReference")]
        )
        self.calls = [
            {
                "args": {"ref": "OWNER_SECRET_NATIVE_REF"},
                "command": command,
                "envelope": {
                    "ok": error is None, "private": "OWNER_SECRET_ENVELOPE",
                    "error": None if error is None else {"code": error, "outcome_known": True},
                },
                "exit_code": 0 if error is None else 4,
                "session": "OWNER_PRIVATE_SESSION_FILE",
            }
            for command, error in outcomes
        ]
        self.store()
        (self.private / "semwrightd.log").write_text(
            "OWNER_PRIVATE_DAEMON_SECRET", encoding="utf-8"
        )
        (self.private.parent / "sandbox-profile.txt").write_text(
            "OWNER_PRIVATE_NETWORK_CONFIG", encoding="utf-8"
        )

    def store(self):
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / "calls.json").write_text(json.dumps(self.calls))

    def reject(self, reason):
        with self.assertRaises(pub.PublicationError) as error:
            pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(error.exception.code, reason)
        self.assertFalse(self.public.exists())

    def test_only_fifteen_verified_checks_and_hash_receipts_are_public(self):
        receipt = pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(receipt["native_shared_state_ci"], "PASS")
        self.assertEqual(receipt["verified_checks"], sorted(pub.EXPECTED_CHECKS))
        self.assertEqual(receipt["verified_native_invocations"], 17)
        self.assertEqual(len(receipt["verified_checks"]), 15)
        self.assertEqual(receipt["source_result_digest_sha256"],
                         hashlib.sha256((self.private / "result.json").read_bytes()).hexdigest())
        self.assertEqual(receipt["source_calls_digest_sha256"],
                         hashlib.sha256((self.private / "calls.json").read_bytes()).hexdigest())
        self.assertEqual([f.name for f in self.public.iterdir()], ["public-evidence.json"])
        txt = (self.public / "public-evidence.json").read_text()
        for secret in ("OWNER_PRIVATE_", "OWNER_SECRET_", "semwrightd.log",
                       "OWNER_PRIVATE_DAEMON_SECRET", "sandbox-profile.txt"):
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

    def test_unknown_or_missing_denial_never_becomes_verified_public_evidence(self):
        original = copy.deepcopy(self.calls)
        negatives = [index for index, call in enumerate(self.calls)
                     if call["envelope"]["ok"] is False]
        for index in negatives:
            for known in (False, 0, 1, "true", None):
                with self.subTest(call=index, known=known):
                    self.calls = copy.deepcopy(original)
                    error = self.calls[index]["envelope"]["error"]
                    if known is None:
                        error.pop("outcome_known")
                    else:
                        error["outcome_known"] = known
                    self.store()
                    before = (self.private / "calls.json").read_bytes()
                    self.reject("private_native_call_known_denial_unverified")
                    self.assertEqual((self.private / "calls.json").read_bytes(), before)
        self.calls = copy.deepcopy(original)
        self.calls[negatives[0]]["envelope"].pop("error")
        self.store()
        self.reject("private_native_call_known_denial_unverified")

    def test_old_thirteen_call_roster_cannot_claim_preview_checks(self):
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
                        self.reject("invalid_private_native_error" if known
                                    else "private_native_call_known_denial_unverified")
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


class PrivateCliErrorRedactionTests(unittest.TestCase):
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
        # Run the real main control flow with transport witnesses. Only the two
        # preview denial replies vary; an UNKNOWN must stop before CAS or the
        # next stale mutation, independently of unchanged Store observations.
        for target, expected_calls in (("redirected", 11), ("stale", 14)):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
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

                def invoke_cli(_env, _socket, session, command, args, calls, *, ok=True):
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
                    return {"exit_code": 4 if error is not None else 0, "envelope": outer}

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
                        "retain_owner_audit": lambda *_args: None,
                    }
                    for name, value in replacements.items():
                        stack.enter_context(patch.object(host, name, value))
                    stack.enter_context(patch.object(host.subprocess, "Popen", return_value=daemon))
                    stack.enter_context(patch.object(Path, "is_socket", return_value=True))
                    with self.assertRaisesRegex(AssertionError, "retained UNKNOWN"):
                        host.main()
                self.assertTrue(unknown_seen)
                self.assertEqual(len(native_calls), expected_calls)
                self.assertEqual(native_calls[-1], "driver.motionwright.project.preview")
                self.assertFalse((evidence / "result.json").exists())
                retained = json.loads((evidence / "calls.json").read_text())
                self.assertEqual(len(retained), expected_calls)
                self.assertIs(retained[-1]["envelope"]["error"]["outcome_known"], False)


class CapsuleSourceTransportShapeTests(unittest.TestCase):
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
