"""Source-only regression for the originally admitted Semwright CLI session ticket.

The real Semwright Unix CLI uses connect_persistent(): an expired ticket can
fall back to a NEW session and save_ticket() replaces the file atomically on
every invocation. The mounted provider lease is insufficient evidence that
the release happened under the original CLI session. These tests execute the
production quiescence source with explicit inert command doubles; they never
prove a real Native Host, SQLite handoff, renderer, or Graph6 acceptance.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest

from . import test_graph_owner_native_quiescence as foundation


class GraphOriginalSessionTicketTests(unittest.TestCase):
    def setUp(self):
        foundation.SourceOnlyReleaseTests.setUp(self)

        def session_identity(path):
            path = Path(path)
            raw = path.read_bytes()
            return {**foundation.identity(path), "bytes": len(raw)}

        # The original owner captures this tuple immediately after the first
        # actual CLI mount; only the ticket BYTES must persist across future
        # CLI processes. A legitimate atomic save changes the file's inode.
        self.original_session = session_identity(self.session)
        self.obj.clients[0]["session"] = self.original_session
        self.release.__globals__["file_identity"] = session_identity

    def assert_no_receipt(self):
        self.assertFalse((self.root / "owner-provider-release.original.json").exists())
        self.assertIs(self.obj.native_quiescence, False)

    def substitute_after_command(self, target, *, preserve_original_ticket):
        original_invoke = self.obj.invoke
        self.replacements = 0

        def invoke(label, socket, session, command, args):
            result = original_invoke(label, socket, session, command, args)
            if preserve_original_ticket or command == target:
                if preserve_original_ticket:
                    # The CLI rewrites a VALID bearer ticket via atomic rename.
                    temp = self.client_dir / ("ticket-"+str(self.replacements)+".temporary")
                    temp.write_bytes(self.session.read_bytes())
                    os.replace(temp, self.session)
                else:
                    # Emulates connect_persistent()'s stale-ticket fallback:
                    # command may report the same provider lease but the
                    # process has silently acquired a foreign Broker session.
                    self.session.write_bytes(b"foreign-broker-session-ticket")
                self.replacements += 1
            return result

        self.obj.invoke = invoke

    def test_legitimate_atomic_ticket_rewrite_keeps_original_broker_session(self):
        self.substitute_after_command("", preserve_original_ticket=True)
        before_inode = self.session.stat().st_ino
        result = self.release(self.obj, request_id="original-session")
        self.assertEqual(result["event"], "PROVIDER_RELEASED")
        self.assertTrue(result["lease_shutdown_completed"])
        self.assertFalse(result["native_process_closure_inferred"])
        self.assertEqual(self.replacements, 2)
        self.assertEqual(self.session.read_bytes(), b"synthetic-original-session")
        self.assertNotEqual(self.session.stat().st_ino, before_inode)
        self.assertFalse(self.lifecycle.closed)
        self.assertEqual([row["command"] for row in self.calls],
                         ["providers.mount.observe", "providers.release_bound"])

    def test_preexisting_foreign_ticket_is_denied_before_any_cli_effect(self):
        self.session.write_bytes(b"foreign-broker-session-ticket")
        with self.assertRaisesRegex(ValueError,
                                    "provider-release-original-session-reissued"):
            self.release(self.obj, request_id="already-foreign")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.source_checks, 0)
        self.assert_no_receipt()

    def test_cli_mount_session_renewal_cannot_authorize_release(self):
        self.substitute_after_command("providers.mount.observe",
                                      preserve_original_ticket=False)
        with self.assertRaisesRegex(ValueError,
                                    "provider-release-original-session-reissued"):
            self.release(self.obj, request_id="foreign-after-mount")
        self.assertEqual([row["command"] for row in self.calls],
                         ["providers.mount.observe"])
        self.assert_no_receipt()
        with self.assertRaisesRegex(ValueError, "provider-release-one-shot"):
            self.release(self.obj, request_id="retry")
        self.assertEqual(len(self.calls), 1)

    def test_cli_release_session_renewal_is_unknown_not_positive(self):
        self.substitute_after_command("providers.release_bound",
                                      preserve_original_ticket=False)
        with self.assertRaisesRegex(ValueError,
                                    "provider-release-original-session-reissued"):
            self.release(self.obj, request_id="foreign-after-release")
        self.assertEqual([row["command"] for row in self.calls],
                         ["providers.mount.observe", "providers.release_bound"])
        self.assert_no_receipt()
        with self.assertRaisesRegex(ValueError, "provider-release-one-shot"):
            self.release(self.obj, request_id="uncertain-effect-no-replay")
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
