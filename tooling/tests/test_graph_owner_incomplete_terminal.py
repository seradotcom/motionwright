"""Source-only Graph Native owner receipt controls on the reviewed 43537 source.

Synthetic closure records test only the Python finalizer's error classification.
They cannot prove real provider release, original streams, SQLite, or G2.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from tooling.tests.test_graph_owner_final_lifecycle_errors import (
    GraphOwnerFailure,
    LeaseLedger,
    SOURCE_ID,
    SOURCE_REF,
    source_namespace,
)


class IncompleteOwnerTerminalTests(unittest.TestCase):
    def run_source_finalizer(self, *, terminal_status):
        namespace = source_namespace()
        recorded, observed_calls = {}, []
        cohort = {
            "status": "PASS", "authority": "process_custody_only_not_revocation",
            "subreaper": True, "signals": [],
        }
        release_record = {
            "schema": "motionwright.graph-mounted-provider-release/1",
            "source": SOURCE_ID, "owner_pid": os.getpid(),
            "provider_lease_shutdown_completed": True,
            "native_process_closure_inferred": False,
            "owner_broker_remains_live": True,
        }
        quota = SimpleNamespace(closing=False, failed={})
        ledger = LeaseLedger()
        evidence = Path("/tmp/mw-a4-graph-incomplete-original-inert")
        def owned_close(*, reason):
            observed_calls.append(("close", reason))
            return {"status": terminal_status, "taints": (
                ["original-process-terminal-incomplete"]
                if terminal_status == "INCOMPLETE" else []
            )}
        owner = SimpleNamespace(
            closed=False, native_quiescence={"ref": SOURCE_REF, "record": release_record},
            client_ledger=ledger,
            lifecycle=SimpleNamespace(
                closed=False, close=owned_close, evidence=evidence, children=[]),
            database=Path("/tmp/motionwright.sqlite3"),
            database_initial={"device": 123, "inode": 456},
            database_initial_original=None,
            clients=[], spec_original=SOURCE_REF,
            source_inputs={"sources": {}, "binaries": {}},
        )
        def read_original(path, maximum, *, expected_sha=None, root=None):
            observed_calls.append(("read", str(path), expected_sha, str(root)))
            self.assertEqual(str(path), SOURCE_REF["path"])
            self.assertEqual(expected_sha, SOURCE_REF["sha256"])
            self.assertEqual(root, evidence)
            return b'{"synthetic":true}', SOURCE_REF
        namespace.update(
            require_subquota=lambda: quota,
            literal_service_barrier=lambda *_: SOURCE_REF,
            read_original=read_original,
            strict_json=lambda _: release_record,
            close_complete_cohort=lambda: (cohort, True),
            file_identity=lambda *_: {
                "device": 123, "inode": 456, "path": "/tmp/motionwright.sqlite3",
                "sha256": "f" * 64, "size_bytes": 1,
            },
            new_json=lambda path, value: recorded.setdefault(path.name, value),
            original_ref=lambda *_: SOURCE_REF,
        )
        result = namespace["graph_close_v6"](
            owner, service_barrier=SOURCE_REF,
            request_id="synthetic-a4-source-only", reason="explicit-close"
        )
        self.assertTrue(owner.closed)
        self.assertTrue(ledger.sealed)
        self.assertTrue(quota.closing)
        self.assertEqual(observed_calls[-1], ("close", "explicit-close"))
        self.assertEqual(result["event"], "FINAL_RECEIPT")
        self.assertEqual(recorded["owner-complete-close.original.json"]["lifecycle"]["status"],
                         terminal_status)
        return result, recorded["owner-final.original.json"]

    def test_incomplete_original_lifecycle_is_explicit_owner_close_failure(self):
        terminal, original = self.run_source_finalizer(terminal_status="INCOMPLETE")
        self.assertEqual(terminal["status"], "UNKNOWN")
        self.assertFalse(terminal["capture_permitted"])
        self.assertEqual(original["errors"], ["OWNER_CLOSE_FAILED"])
        self.assertFalse(original["native_authoring_approved"])
        self.assertFalse(original["formal_acceptance"])

    def test_original_closed_lifecycle_regression_without_effect_claim(self):
        terminal, original = self.run_source_finalizer(terminal_status="CLOSED")
        self.assertEqual(terminal["status"], "CLOSED")
        self.assertEqual(original["errors"], [])
        self.assertTrue(terminal["capture_permitted"])
        self.assertFalse(original["native_authoring_approved"])
        self.assertFalse(original["formal_acceptance"])


if __name__ == "__main__":
    unittest.main()
