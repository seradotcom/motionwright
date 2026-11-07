"""Negative R74 archive acceptance tests; source-only, never Graph Native."""
import copy
import hashlib
import json
import pathlib
import tempfile
import unittest

from tooling import ci_canvas_service as graph


class ReadOnlyArchiveFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a6-r74-readonly-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.refmap = {}
        self.source = {"sha": "a" * 40, "run_id": "42400000001", "run_attempt": 1}
        self.physical = {"path": "/original/store.sqlite3", "device": 6, "ino": 9}
        self.context = {"helper_id": "native-1", "launch_index": 0}
        self.binary = {"path": "/original/ordinary-service", "sha256": "c" * 64, "size_bytes": 100}
        self.final_ref = {"path": "/original/owner-final.json", "sha256": "d" * 64, "size_bytes": 200}
        self.reopen_path = self.root / "case/independent-reopen/terminal.original.json"
        self.preseal_ref = None
        self.handoff_ref = None
        self.raw_writer_ref = None
        self.closed_ref = None
        self.bootstrap_ref = None
        self.reopen = None
        self.final = None
        self.index = {"source": self.source, "binaries": {"ordinary_service": self.binary}}
        self.build()

    def original(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = data if isinstance(data, bytes) else json.dumps(data, sort_keys=True).encode("utf-8") + b"\n"
        path.write_bytes(raw)
        ref = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
               "size_bytes": len(raw)}
        self.refmap[str(path)] = raw
        return ref

    def retained(self, ref, max_bytes):
        self.assertEqual(set(ref), {"path", "sha256", "size_bytes"})
        raw = self.refmap[ref["path"]]
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
        self.assertEqual(len(raw), ref["size_bytes"])
        self.assertLessEqual(len(raw), max_bytes)
        return raw

    def build(self):
        self.preseal_ref = self.original("case/original-preseal.json", {
            "phase": "original-service-before-owner-seal",
            "source": self.source, "context": self.context,
            "process": {"pid": 510}, "physical_store": self.physical,
            "synthetic_adapter_only": False,
            "held_store_digest": "STABLE_NATIVE_SQLITE_VFS_DIGEST",
            "raw_stdout_path": str(self.root / "case/original-service.stdout"),
        })
        response = {"id": "handoff-1", "ok": True,
                    "result": {"read_only_handoff": True, "journal_mode": "delete"}}
        witness = {"id": "handoff-1-held-identity", "ok": True,
                   "result": {"VFS": "STABLE_NATIVE_SQLITE_VFS_DIGEST"}}
        r1 = self.original("case/handoff-request.jsonl",
                           {"id": response["id"], "command": "prepare_read_only_handoff", "args": None})
        r2 = self.original("case/identity-request.jsonl",
                           {"id": witness["id"], "command": "opened_store_identity_witness", "args": None})
        self.raw_writer_ref = self.original("case/original-service.stdout",
                        b"".join(json.dumps(x).encode() + b"\n" for x in (response, witness)))
        self.handoff_ref = self.original("case/original-handoff.json", {
            "schema": "motionwright.graph.original-read-only-handoff/1",
            "source": self.source, "phase": "after-admissions-seal-before-writer-exit",
            "context": self.context, "process_id": 510,
            "before": self.physical, "after": self.physical,
            "writer_original": self.preseal_ref,
            "request_original": r1, "response": response,
            "identity_request_original": r2, "identity_response": witness,
            "held_store_digest": "STABLE_NATIVE_SQLITE_VFS_DIGEST",
            "read_only_handoff": True, "journal_mode": "delete",
            "synthetic_adapter_only": False, "automatic_retry": False,
            "gate_credit": False,
        })
        self.closed_ref = self.original("case/owner-service-closure.json", {
            "status": "CLOSED", "database_path": "/original/store.sqlite3",
            "participants": [{
                "helper_id": "native-1", "database_path": "/original/store.sqlite3",
                "process_streams": [{"process_id": 510, "exit_index": 0,
                                      "stdout_ended": True, "stderr_ended": True,
                                      "jsonl_reader_closed": True}],
                "process_exits": [{"process_id": 510, "code": 0, "signal": None}],
            }]
        })
        self.final = {"service_closure": self.closed_ref}
        self.expected_ref = self.original("case/oracle-original.json", {
            "journalComplete": True, "journalAfterRevisionExclusive": 0,
            "project": {"id": "synthetic-fixture", "revision": 0}, "journal": []})
        self.observed_ref = self.original("case/independent-reopen/observed-oracle.original.json", {
            "journalComplete": True, "journalAfterRevisionExclusive": 0,
            "project": {"id": "synthetic-fixture", "revision": 0}, "journal": []})
        self.bootstrap_ref = self.original("case/independent-reopen/requests/0.original.jsonl", {
            "id": "same-store-read-0", "command": "bootstrap",
            "args": {"request": {"project_id": "synthetic-fixture"}}})
        child_reply = {"id": "same-store-read-0", "ok": True, "result": {
            "read_only": True, "test_transport": "service-test-jsonl",
            "evidence_scope": "real_service_sqlite_cas_read_only; excludes_tauri_installer_native_render",
            "project": {"id": "synthetic-fixture", "revision": 0},
        }}
        child_raw = self.original("case/independent-reopen/service.stdout.original",
                    json.dumps(child_reply).encode() + b"\n")
        self.reopen = {
            "source": self.source, "synthetic_adapter_only": False, "gate_credit": False,
            "read_only_child_bootstrap_proved": True, "original_writer_read_only_handoff": self.handoff_ref,
            "original_writer_handoff_raw": self.raw_writer_ref,
            "original_preseal_held_pager_witness": self.preseal_ref,
            "original_service_closure": self.closed_ref,
            "physical_store_before": self.physical, "physical_store_after": self.physical,
            "service_process": {"pid": 511, "code": 0, "signal": None},
            "original_owner_final": self.final_ref, "requests": [self.bootstrap_ref],
            "expected_oracle": self.expected_ref, "observed_oracle": self.observed_ref,
            "raw": {"stdout": {**child_raw, "eof": True}},
        }
        intent = {
            "source": self.source, "binary": self.binary,
            "argv": [self.binary["path"], "--database", "/original/store.sqlite3", "--read-only"],
            "original_writer_read_only_handoff": self.handoff_ref,
            "original_owner_final": self.final_ref, "physical_store_before": self.physical,
        }
        self.intent = self.original("case/independent-reopen/intent.original.json", intent)

    def verify(self, reopen=None):
        return graph.graph_verify_readonly_archive_originals(
            self.reopen if reopen is None else reopen, self.reopen_path,
            self.final, self.index, self.retained)

    def test_custody_validation_is_source_registered(self):
        p = pathlib.Path(__file__).resolve().parents[2]
        code = (p / "tooling/ci_canvas_service.py").read_text(encoding="utf-8")
        self.assertIn('"tooling/tests/test_ci_graph_readonly_archive.py"', code)
        self.assertEqual(graph.graph_verify_readonly_archive_originals.__name__,
                         "graph_verify_readonly_archive_originals")

    def test_complete_original_handoff_and_distinct_reader_non_g2(self):
        value = self.verify()
        self.assertEqual(value["writer_pid"], 510)
        self.assertEqual(value["reader_pid"], 511)
        self.assertTrue(value["reader_read_only"])
        self.assertEqual(value["writer_journal_mode"], "delete")
        self.assertFalse(value["formal_acceptance"])

    def test_missing_handoff_raw_and_bootstrap_proof_are_fatal(self):
        for missing in ("original_writer_read_only_handoff", "original_writer_handoff_raw",
                        "read_only_child_bootstrap_proved", "original_service_closure",
                        "original_preseal_held_pager_witness"):
            case = copy.deepcopy(self.reopen)
            case.pop(missing)
            with self.subTest(field=missing), self.assertRaises((ValueError, KeyError, TypeError)):
                self.verify(case)
        for field, replacement in (("read_only_child_bootstrap_proved", False),
                                   ("synthetic_adapter_only", True),
                                   ("gate_credit", True)):
            case = copy.deepcopy(self.reopen)
            case[field] = replacement
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError, TypeError)):
                self.verify(case)

    def test_original_writer_raw_identity_and_distinct_clean_close(self):
        cases = [
            ("wrong_writer_ref", lambda x: x.__setitem__("original_writer_handoff_raw", self.bootstrap_ref)),
            ("wrong_handoff_ref", lambda x: x.__setitem__("original_writer_read_only_handoff", self.preseal_ref)),
            ("wrong_original_store", lambda x: x.__setitem__("physical_store_before", {"ino": 999})),
            ("writer_reader_same_pid", lambda x: x["service_process"].__setitem__("pid", 510)),
            ("reader_exit_nonzero", lambda x: x["service_process"].__setitem__("code", 1)),
            ("reader_signalled", lambda x: x["service_process"].__setitem__("signal", "SIGKILL")),
        ]
        for label, mutate in cases:
            candidate = copy.deepcopy(self.reopen)
            mutate(candidate)
            with self.subTest(case=label), self.assertRaises((ValueError, KeyError, TypeError)):
                self.verify(candidate)

    def test_changed_oracle_and_bootstrap_project_cannot_replace_source(self):
        original = self.reopen["observed_oracle"]
        candidate = copy.deepcopy(self.reopen)
        candidate["observed_oracle"] = self.bootstrap_ref
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.verify(candidate)
        raw = self.reopen["raw"]["stdout"]
        saved = self.refmap[raw["path"]]
        replacement = json.loads(saved)
        replacement["result"]["project"]["id"] = "unrelated"
        newraw = json.dumps(replacement).encode() + b"\n"
        self.refmap[raw["path"]] = newraw
        self.reopen["raw"]["stdout"] = {**raw, "sha256": __import__("hashlib").sha256(newraw).hexdigest(),
                                        "size_bytes": len(newraw)}
        try:
            with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                self.verify()
        finally:
            self.reopen["raw"]["stdout"] = raw
            self.refmap[raw["path"]] = saved

    def test_child_cannot_open_a_different_absolute_sqlite_file(self):
        original = self.refmap[self.intent["path"]]
        wrong = json.loads(original)
        wrong["argv"][2] = "/unreviewed/other.sqlite3"
        self.refmap[self.intent["path"]] = json.dumps(wrong).encode() + b"\n"
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.verify()
        self.refmap[self.intent["path"]] = original

    def test_writable_child_or_missing_raw_readonly_response_is_fatal(self):
        self.assertEqual(self.verify()["reader_pid"], 511)
        original = self.refmap[self.intent["path"]]
        wrong = json.loads(original)
        wrong["argv"][-1] = "--read-write"
        self.refmap[self.intent["path"]] = json.dumps(wrong).encode() + b"\n"
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.verify()
        self.refmap[self.intent["path"]] = original
        raw = next(ref for ref in self.reopen["raw"].values())
        saved = self.refmap[raw["path"]]
        altered = json.loads(saved)
        altered["result"]["read_only"] = False
        self.refmap[raw["path"]] = json.dumps(altered).encode() + b"\n"
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.verify()
        self.refmap[raw["path"]] = saved

if __name__ == "__main__":
    unittest.main()
