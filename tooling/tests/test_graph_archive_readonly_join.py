"""Source-only negative oracles for authentic Graph R74 read-only handoff archive.

All fixture bytes are test-generated; no Store, SDK, Host or physical status.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "ci_canvas_service.py"
spec = importlib.util.spec_from_file_location("graph_archive_reader_under_test", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def encoding(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class OriginalFixture:
    def __init__(self):
        self.files = {}
        self.source = {"sha": "9bdc" * 10, "run_id": "verified-run", "run_attempt": 1}
        self.physical = {"device": 17, "inode": 44, "mode": 33152}
        self.database = "/original/ext4/store/motionwright.sqlite3"
        self.context = {"helper_id": "h-authentic", "launch_index": 0,
                        "database_path": self.database}
        self.project = {"id": "project-original", "revision": 8}
        self.digest = "d" * 64
        self.index = {"source": self.source, "binaries":
                      {"ordinary_service": {"path": "/source-built/bin/studio_service"}}}
        self.first_request = {"id": "handoff-id", "command": "prepare_read_only_handoff", "args": None}
        self.first_response = {"id": "handoff-id", "ok": True,
                               "result": {"read_only_handoff": True, "journal_mode": "delete"}}
        self.second_request = {"id": "identity-id", "command": "opened_store_identity_witness", "args": None}
        self.second_response = {"id": "identity-id", "ok": True, "result": self.digest}
        self.writer_bytes = (encoding(self.first_response) + b"\n"
                             + encoding(self.second_response) + b"\n")
        self.raw = self.put("writer-stdout", self.writer_bytes)
        self.request = self.put("handoff-request", encoding(self.first_request) + b"\n")
        self.identity_request = self.put("identity-request", encoding(self.second_request) + b"\n")
        self.preseal = {"source": self.source, "process": {"pid": 5884},
                        "context": self.context, "physical_store": self.physical,
                        "held_store_digest": self.digest}
        self.preseal_ref = self.put("preseal", encoding(self.preseal))
        self.handoff = {
            "schema": "motionwright.graph.original-read-only-handoff/1",
            "source": self.source, "process_id": 5884, "context": self.context,
            "writer_original": self.preseal_ref,
            "before": self.physical, "after": self.physical,
            "request_original": self.request, "response": self.first_response,
            "identity_request_original": self.identity_request,
            "identity_response": self.second_response, "held_store_digest": self.digest,
            "read_only_handoff": True, "journal_mode": "delete",
            "synthetic_adapter_only": False, "automatic_retry": False, "gate_credit": False,
        }
        self.handoff_ref = self.put("handoff", encoding(self.handoff))
        self.closure = {"status": "CLOSED", "participants": [{
            "helper_id": "h-authentic", "database_path": self.database,
            "process_streams": [{"process_id": 5884, "exit_index": 0,
                                 "stdout_ended": True, "stderr_ended": True,
                                 "jsonl_reader_closed": True}],
            "process_exits": [{"process_id": 5884, "code": 0, "signal": None}],
        }]}
        self.closure_ref = self.put("service-closure", encoding(self.closure))
        self.final_ref = self.put("owner-final", b"{}")
        self.final = {"service_closure": self.closure_ref}
        self.bootstrap_req = {"id": "same-store-read-0", "command": "bootstrap",
                              "args": {"request": {"project_id": self.project["id"]}}}
        self.bootstrap_ref = self.put("bootstrap-request", encoding(self.bootstrap_req) + b"\n")
        self.bootstrap_resp = {"id": "same-store-read-0", "ok": True, "result": {
            "read_only": True, "project": self.project, "test_transport": "service-test-jsonl",
            "evidence_scope": "real_service_sqlite_cas_read_only; excludes_tauri_installer_native_render",
        }}
        self.child_raw = self.put("read-only-child-stdout", encoding(self.bootstrap_resp) + b"\n")
        self.oracle_ref = self.put("original-oracle", encoding({"project": self.project, "journal": []}))
        self.intent = {"source": self.source, "argv": [
            "/source-built/bin/studio_service", "--database", self.database, "--read-only",
        ], "original_writer_read_only_handoff": self.handoff_ref,
            "read_only_child_required": True, "original_owner_final": self.final_ref}
        self.reopen = {
            "read_only_child_bootstrap_proved": True,
            "original_writer_read_only_handoff": self.handoff_ref,
            "original_writer_handoff_raw": self.raw,
            "original_preseal_held_pager_witness": self.preseal_ref,
            "original_preseal_response_raw": {"stream": self.raw},
            "original_service_closure": self.closure_ref,
            "physical_store_before": self.physical, "physical_store_after": self.physical,
            "expected_oracle": self.oracle_ref, "requests": [self.bootstrap_ref],
            "raw": {"stdout": dict(self.child_raw, eof=True)},
            "service_process": {"pid": 5895}, "original_owner_final": self.final_ref,
        }

    def put(self, name, raw):
        ref = {"path": "/fixture-originals/" + name,
               "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        self.files[ref["path"]] = raw
        return ref

    def retained(self, ref, maximum=1024 * 1024):
        raw = self.files[ref["path"]]
        if len(raw) > maximum or ref["size_bytes"] != len(raw) or ref["sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("Original source fixture digest differs")
        return raw

    def replace_handoff(self):
        self.handoff_ref = self.put("handoff-mutated", encoding(self.handoff))
        self.reopen["original_writer_read_only_handoff"] = self.handoff_ref
        self.intent["original_writer_read_only_handoff"] = self.handoff_ref

    def check(self):
        gate.graph_archive_readonly_handoff(self.reopen, self.intent,
                                            self.index, self.final, self.retained)


class GraphArchiveReadOnlyOriginalsTests(unittest.TestCase):
    def test_authentic_source_bound_handoff_reader_and_closure_pass(self):
        OriginalFixture().check()

    def test_three_new_originals_are_mandatory_and_cannot_be_relabeled(self):
        for field in ("original_writer_read_only_handoff",
                      "original_writer_handoff_raw", "read_only_child_bootstrap_proved"):
            fixture = OriginalFixture()
            fixture.reopen.pop(field)
            with self.subTest(field=field), self.assertRaises((ValueError, KeyError)):
                fixture.check()

    def test_mismatched_first_writer_handoff_and_closed_process_rejected(self):
        for kind in ("writer_pid", "sqlite_identity", "closure_missing",
                     "handoff_delete_false", "writer_response_missing",
                     "wrong_source", "same_child_pid"):
            fixture = OriginalFixture()
            if kind == "writer_pid":
                fixture.handoff["process_id"] = 7777
                fixture.replace_handoff()
            elif kind == "sqlite_identity":
                fixture.handoff["after"] = {"device": 17, "inode": 101}
                fixture.replace_handoff()
            elif kind == "closure_missing":
                fixture.closure["status"] = "UNKNOWN"
                fixture.closure_ref = fixture.put("closed-mutated", encoding(fixture.closure))
                fixture.final["service_closure"] = fixture.closure_ref
                fixture.reopen["original_service_closure"] = fixture.closure_ref
            elif kind == "handoff_delete_false":
                fixture.handoff["response"]["result"]["journal_mode"] = "wal"
                fixture.replace_handoff()
            elif kind == "writer_response_missing":
                fixture.raw = fixture.put("writer-truncated", encoding(fixture.second_response) + b"\n")
                fixture.reopen["original_writer_handoff_raw"] = fixture.raw
                fixture.reopen["original_preseal_response_raw"]["stream"] = fixture.raw
            elif kind == "wrong_source":
                fixture.handoff["source"] = {"sha": "unreviewed"}
                fixture.replace_handoff()
            elif kind == "same_child_pid":
                fixture.reopen["service_process"]["pid"] = 5884
            with self.subTest(kind=kind), self.assertRaises((ValueError, KeyError)):
                fixture.check()

    def test_mutated_child_bootstrap_or_writable_reader_is_rejected(self):
        for kind in ("reader_writable", "child_bootstrap_writable", "foreign_project",
                     "different_handoff_ref", "child_raw_eof_absent"):
            fixture = OriginalFixture()
            if kind == "reader_writable":
                fixture.intent["argv"].pop()
            elif kind == "child_bootstrap_writable":
                fixture.bootstrap_resp["result"]["read_only"] = False
            elif kind == "foreign_project":
                fixture.bootstrap_resp["result"]["project"] = {"id": "foreign", "revision": 8}
            elif kind == "different_handoff_ref":
                fixture.intent["original_writer_read_only_handoff"] = fixture.preseal_ref
            elif kind == "child_raw_eof_absent":
                fixture.reopen["raw"]["stdout"]["eof"] = False
            if kind in {"child_bootstrap_writable", "foreign_project"}:
                fixture.child_raw = fixture.put("reader-mutated", encoding(fixture.bootstrap_resp) + b"\n")
                fixture.reopen["raw"]["stdout"] = dict(fixture.child_raw, eof=True)
            with self.subTest(kind=kind), self.assertRaises((ValueError, KeyError)):
                fixture.check()


if __name__ == "__main__":
    unittest.main()
