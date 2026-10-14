"""Adversarial v0.1 public Native SDK shared-state artifact admission."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        commands = (["driver.motionwright.observe"] * 6
                    + ["driver.motionwright.project.rename"] * 7)
        self.calls = [
            {
                "args": {"ref": "OWNER_SECRET_NATIVE_REF"},
                "command": command,
                "envelope": {"ok": i < 8, "private": "OWNER_SECRET_ENVELOPE"},
                "exit_code": 0 if i < 8 else 4,
                "session": "OWNER_PRIVATE_SESSION_FILE",
            }
            for i, command in enumerate(commands)
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

    def test_only_ten_verified_checks_and_hash_receipts_are_public(self):
        receipt = pub.publish(self.private, self.public, SOURCE, PIN, "success")
        self.assertEqual(receipt["native_shared_state_ci"], "PASS")
        self.assertEqual(receipt["verified_checks"], sorted(pub.EXPECTED_CHECKS))
        self.assertEqual(receipt["verified_native_invocations"], 13)
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


if __name__ == "__main__":
    unittest.main()
