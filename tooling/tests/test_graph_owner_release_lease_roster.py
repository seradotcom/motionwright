"""Independent source-only acceptance: release must bind every original client lease.

No synthetic Broker reply is acceptance evidence. These adversarial cases
exercise the production function and guard against mixed original cohorts.
"""
from __future__ import annotations

from copy import deepcopy
import unittest

from . import test_graph_owner_native_quiescence as foundation


class OriginalLeaseRosterTests(unittest.TestCase):
    def setUp(self):
        foundation.SourceOnlyReleaseTests.setUp(self)
        first = self.obj.clients[0]
        second_session = self.client_dir / "client-second.session"
        second_session.write_bytes(b"synthetic-second-original-session")
        second = dict(first)
        second["session"] = foundation.identity(second_session)
        second["actual_mount_observation"] = deepcopy(self.mount)
        self.obj.clients.append(second)
        self.ledger.snapshot = lambda: [
            {"status": "READY", "client_id": "first"},
            {"status": "READY", "client_id": "second"},
        ]

    def assert_no_receipt(self):
        foundation.SourceOnlyReleaseTests.assert_no_receipt(self)

    def test_identical_original_provider_lease_roster_remains_eligible(self):
        response = self.release(self.obj, request_id="same-lease")
        self.assertEqual(response["event"], "PROVIDER_RELEASED")
        self.assertEqual(len(self.calls), 2)

    def test_prior_client_different_provider_generation_is_not_quiescence(self):
        self.obj.clients[0]["actual_mount_observation"] = {
            **deepcopy(self.mount), "provider_generation": 6,
        }
        with self.assertRaisesRegex(ValueError, "provider-release-original-lease-roster-changed"):
            self.release(self.obj, request_id="different-generation")
        self.assert_no_receipt()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.source_checks, 0)

    def test_prior_client_different_provider_connection_is_not_quiescence(self):
        self.obj.clients[0]["actual_mount_observation"] = {
            **deepcopy(self.mount), "connection": "prior-provider-lease",
        }
        with self.assertRaisesRegex(ValueError, "provider-release-original-lease-roster-changed"):
            self.release(self.obj, request_id="different-connection")
        self.assert_no_receipt()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.source_checks, 0)

    def test_prior_session_changed_during_mount_denies_before_bound_release(self):
        original_invoke = self.obj.invoke

        def invoke(label, socket, session, command, args):
            result = original_invoke(label, socket, session, command, args)
            if command == "providers.mount.observe":
                (self.client_dir / "client-original.session").write_bytes(
                    b"foreign-prior-after-mount")
            return result

        self.obj.invoke = invoke
        with self.assertRaisesRegex(ValueError, "provider-release-original-session-reissued"):
            self.release(self.obj, request_id="foreign-after-observe")
        self.assert_no_receipt()
        self.assertEqual([row["command"] for row in self.calls],
                         ["providers.mount.observe"])

    def test_prior_original_session_reissue_cannot_be_ignored(self):
        prior_session = self.client_dir / "client-original.session"
        prior_session.write_bytes(b"foreign-prior-broker-session")
        with self.assertRaisesRegex(ValueError, "provider-release-original-session-reissued"):
            self.release(self.obj, request_id="foreign-prior-session")
        self.assert_no_receipt()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.source_checks, 0)


if __name__ == "__main__":
    unittest.main()
