"""Synthetic source-only Native owner transition tests.

The extracted production function is executed, but all daemon/cohort receipts
are explicit mechanical doubles. No Semwright Host, SQLite producer, native
render or G2 acceptance is exercised or implied by these tests.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "native_graph_browser_owner.py"
FUNCTIONS = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
             if isinstance(node, ast.FunctionDef) and node.name == "graph_quiesce_v6"]
if len(FUNCTIONS) != 1:
    raise AssertionError("Exactly one production Graph quiescence function is required")


def require(value, reason):
    if not value:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def original_ref(path):
    raw = Path(path).read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw)}


class DummyLedger:
    def __init__(self):
        self.failed = False
        self.status = "READY"
        self.seals = 0

    def seal(self):
        self.seals += 1

    def snapshot(self):
        return [{"status": self.status}]


class DummyLifecycle:
    def __init__(self, root, stop_status="CLOSED"):
        self.evidence = root
        self.closed = False
        self.receipt = None
        self.stop_status = stop_status
        self.close_calls = 0
        stream = lambda: SimpleNamespace(eof=True, failure=None)
        self.children = [SimpleNamespace(closed=True, out=stream(), err=stream())]

    def close(self, *, reason):
        self.close_calls += 1
        if self.close_calls > 1:
            raise AssertionError("Native owner process tree was closed more than once")
        if reason != "explicit-close":
            raise AssertionError("Closure must not extend or renew authority")
        self.receipt = {"status": self.stop_status}
        self.closed = True
        (self.evidence / "owner-close.json").write_bytes(canonical(self.receipt))
        return self.receipt


class GraphNativeQuiescenceSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="graph-native-quiescence-mechanism-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "motionwright.sqlite3"
        self.db.write_bytes(b"synthetic owner identity only")
        metadata = self.db.stat()
        identity = {"device": metadata.st_dev, "inode": metadata.st_ino}
        self.absent = True
        self.source_error = None
        self.source_checks = 0
        self.lifecycle = DummyLifecycle(self.root)
        self.ledger = DummyLedger()
        self.daemon = SimpleNamespace(
            child=SimpleNamespace(poll=lambda: None),
            terminal={"status": "STOPPED", "writers_closed_within_owned_group": True})
        self.owner = SimpleNamespace(
            closed=False, native_quiescence=None, client_ledger=self.ledger,
            lifecycle=self.lifecycle, daemon=self.daemon, database=self.db,
            database_initial=identity)
        cohort_path = self.root / "complete-descendant-scope.original.json"
        self.cohort = {"status": "PASS", "synthetic": True,
                       "signals": [], "reaped": [],
                       "authority": "process_custody_only_not_revocation"}
        cohort_path.write_bytes(canonical(self.cohort))

        def source_check(_owner):
            self.source_checks += 1
            if self.source_error:
                raise ValueError(self.source_error)

        def cohort():
            return (self.cohort, self.absent)

        def new_json(path, value):
            with Path(path).open("xb") as output:
                output.write(canonical(value))

        namespace = {
            "require": require,
            "observe_source_before_close": source_check,
            "close_complete_cohort": cohort,
            "new_json": new_json,
            "original_ref": original_ref,
            "file_identity": lambda path: {
                "device": Path(path).stat().st_dev,
                "inode": Path(path).stat().st_ino},
            "_OWNER_SPEC": {"source": {"sha": "a" * 40}},
            "_COHORT_RECEIPT": cohort_path,
            "os": os,
        }
        module = ast.Module(body=FUNCTIONS, type_ignores=[])
        exec(compile(module, str(SOURCE), "exec"), namespace)
        self.quiesce = namespace["graph_quiesce_v6"]

    def assert_no_positive_original(self):
        self.assertFalse((self.root / "owner-native-quiescence.original.json").exists())
        self.assertIs(self.owner.native_quiescence, False)

    def test_closes_once_with_original_source_and_empty_owner_cohort(self):
        reply = self.quiesce(self.owner, request_id="q-1")
        self.assertEqual(reply["event"], "NATIVE_QUIESCED")
        self.assertEqual(reply["status"], "QUIESCED")
        self.assertTrue(reply["owner_cohort_absent"])
        self.assertEqual(reply["owner_pid"], os.getpid())
        self.assertEqual((self.source_checks, self.lifecycle.close_calls), (1, 1))
        self.assertEqual(self.ledger.seals, 1)
        record = json.loads(Path(reply["receipt_path"]).read_text())
        self.assertTrue(record["native_lifecycle_closed"])
        self.assertTrue(record["native_owner_streams_eof"])
        self.assertFalse(record["graph_acceptance"])
        self.assertFalse(record["automatic_retry"])
        self.assertEqual(record["original_store"], record["current_store"])
        with self.assertRaisesRegex(ValueError, "native-quiescence-one-shot"):
            self.quiesce(self.owner, request_id="q-2")
        self.assertEqual(self.lifecycle.close_calls, 1)

    def test_final_phase_reuses_real_native_close_without_second_lifecycle_close(self):
        self.quiesce(self.owner, request_id="quiesce-original")
        ast_source = ast.parse(SOURCE.read_text(encoding="utf-8"))
        helpers = [node for node in ast_source.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "graph_native_final_lifecycle_receipt"]
        self.assertEqual(len(helpers), 1, "One real final lifecycle reuse helper required")
        namespace = {"require": require}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), str(SOURCE), "exec"),
             namespace)
        final = namespace["graph_native_final_lifecycle_receipt"]
        self.assertIs(final(self.owner, reason="explicit-close",
                            native_quiescence=self.owner.native_quiescence),
                      self.lifecycle.receipt)
        self.assertEqual(self.lifecycle.close_calls, 1)
        close_fn = [node for node in ast_source.body if isinstance(node, ast.FunctionDef)
                    and node.name == "graph_close_v6"]
        self.assertEqual(len(close_fn), 1)
        final_calls = [n.func.id for n in ast.walk(close_fn[0])
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("graph_native_final_lifecycle_receipt", final_calls)
        self.lifecycle.receipt = {"status": "UNKNOWN"}
        with self.assertRaisesRegex(ValueError, "native-quiescence-final-lifecycle"):
            final(self.owner, reason="explicit-close",
                  native_quiescence=self.owner.native_quiescence)
        self.assertEqual(self.lifecycle.close_calls, 1)

    def test_emergency_sigkill_cannot_claim_clean_native_quiescence(self):
        # The real Linux subreaper can return status=PASS after SIGKILLing
        # surviving provider descendants. That proves absence, not an
        # authorized and settled provider shutdown before SQLite handoff.
        self.cohort["signals"] = [{"pid": 43210, "signal": 9}]
        (self.root / "complete-descendant-scope.original.json").write_bytes(
            canonical(self.cohort))
        with self.assertRaisesRegex(ValueError, "native-quiescence-owner-processes-not-closed"):
            self.quiesce(self.owner, request_id="q-sigkill")
        self.assert_no_positive_original()
        self.assertEqual(self.lifecycle.close_calls, 1)

    def test_foreign_cohort_keeps_transition_denied_without_a_positive_receipt(self):
        self.absent = False
        with self.assertRaisesRegex(ValueError, "native-quiescence-owner-processes-not-closed"):
            self.quiesce(self.owner, request_id="q")
        self.assert_no_positive_original()
        with self.assertRaisesRegex(ValueError, "native-quiescence-one-shot"):
            self.quiesce(self.owner, request_id="retry")
        self.assertEqual(self.lifecycle.close_calls, 1)

    def test_changed_source_refuses_before_native_close(self):
        self.source_error = "source-original-changed"
        with self.assertRaisesRegex(ValueError, "source-original-changed"):
            self.quiesce(self.owner, request_id="q")
        self.assert_no_positive_original()
        self.assertEqual(self.lifecycle.close_calls, 0)

    def test_missing_or_failed_client_blocks_native_close(self):
        self.ledger.status = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "native-quiescence-client-unknown"):
            self.quiesce(self.owner, request_id="q")
        self.assert_no_positive_original()
        self.assertEqual(self.lifecycle.close_calls, 0)

    def test_exited_daemon_is_not_represented_as_graceful_quiescence(self):
        self.daemon.child.poll = lambda: 2
        with self.assertRaisesRegex(ValueError, "native-quiescence-daemon-already-exited"):
            self.quiesce(self.owner, request_id="q")
        self.assert_no_positive_original()
        self.assertEqual(self.lifecycle.close_calls, 0)

    def test_incomplete_native_owner_close_never_emits_a_positive_original(self):
        self.lifecycle.stop_status = "INCOMPLETE"
        with self.assertRaisesRegex(ValueError, "native-quiescence-owner-processes-not-closed"):
            self.quiesce(self.owner, request_id="q")
        self.assert_no_positive_original()
        self.assertEqual(self.lifecycle.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
