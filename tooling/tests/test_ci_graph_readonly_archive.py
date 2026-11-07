"""Inert R74 Graph original read-only archive controls; never Native/G2 evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "ci_canvas_service.py"
SPEC = importlib.util.spec_from_file_location("graph_ci_readonly_archive", SOURCE)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class InertOriginals:
    def __init__(self, mode="valid"):
        self.mode = mode
        self.root = Path(tempfile.gettempdir()).resolve() / "mw-r74-readonly-source-fixture"
        self.files = {}
        self.refs = {}
        self.source = {"sha": "a" * 40, "run_id": "12345678", "run_attempt": 1}
        self.physical = {"path": str(self.root / ("other-source" if mode == "database_outside_case" else "graph-flow") / "store" / "motionwright.sqlite3"),
                         "device": "2049", "inode": "98765"}
        dev, ino = int(self.physical["device"]), int(self.physical["inode"])
        major = ((dev >> 8) & 0xfff) | ((dev >> 32) & 0xfffff000)
        minor = (dev & 0xff) | ((dev >> 12) & 0xffffff00)
        self.held = hashlib.sha256(f"linux:{major}:{minor}:{ino}".encode()).hexdigest()
        self.native = {"path": str(self.root / "bin" / "native-graph-review-session")}
        self.ordinary = {"path": str(self.root / "bin" / "studio-review-session")}
        self.index = {"source": self.source, "binaries": {
            "native_graph_session": self.native, "ordinary_service": self.ordinary}}
        self.make()

    def put(self, name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":")).encode()
        file = str(self.root / name)
        ref = {"path": file, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        self.files[file] = raw
        self.refs[file] = ref
        return ref

    def request(self, name, value):
        return self.put(name, json.dumps(value, separators=(",", ":")).encode() + b"\n")

    def retained(self, ref, maximum=gate.GRAPH_BYTES * 512):
        path = ref["path"]
        raw = self.files[path]
        assert ref == self.refs[path], "Inert fixture original ref/identity changed"
        assert len(raw) <= maximum, "Inert fixture quota exceeded"
        return raw

    def make(self):
        project = {"id": "00000000-0000-4000-8000-000000000001", "revision": 2}
        journal = [{"revision": 1, "change": {"type": "rename_project"}},
                   {"revision": 2, "change": {"type": "upsert_semantic_graph"}}]
        context = {"helper_id": "11111111-1111-4111-8111-111111111111",
                   "binary_path": self.native["path"], "database_path": self.physical["path"],
                   "launch_index": 0}
        preseal_request = {"id": "preseal", "command": "opened_store_identity_witness", "args": None}
        handoff_request = {"id": "handoff", "command": "prepare_read_only_handoff", "args": None}
        identity_request = {"id": "after-handoff", "command": "opened_store_identity_witness", "args": None}
        preref = self.request("writer/preseal.request.jsonl", preseal_request)
        href = self.request("writer/handoff.request.jsonl", handoff_request)
        iref = self.request("writer/identity.request.jsonl", identity_request)
        response = lambda request, result: {"id": request["id"], "ok": True, "result": result}
        writer_results = [response(preseal_request, self.held),
                          response(handoff_request, {"read_only_handoff": True, "journal_mode": "delete"}),
                          response(identity_request, self.held)]
        if self.mode == "writer_raw_missing_handoff":
            original_writer_results = [writer_results[0], writer_results[2]]
        elif self.mode == "writer_raw_reordered":
            original_writer_results = [writer_results[1], writer_results[0], writer_results[2]]
        elif self.mode == "writer_raw_duplicate":
            original_writer_results = [*writer_results, writer_results[1]]
        else:
            original_writer_results = writer_results
        writer_lines = [json.dumps(x, separators=(",", ":")).encode() + b"\n"
                        for x in original_writer_results]
        writer_raw = b"".join(writer_lines)
        stdout_ref = self.put("writer/stdout.original", writer_raw)
        preseal = {"source": self.source, "physical_store": self.physical,
                   "binary": self.native, "context": context, "process": {"pid": 101},
                   "held_store_digest": self.held, "request_original": preref,
                   "response": writer_results[0], "raw_stdout_path": stdout_ref["path"]}
        preseal_ref = self.put("writer/preseal.original.json", preseal)
        handoff = {"schema": "motionwright.graph.original-read-only-handoff/1",
                   "source": {"sha": "b" * 40} if self.mode == "handoff_wrong_source" else self.source,
                   "phase": "after-admissions-seal-before-writer-exit",
                   "writer_original": preseal_ref, "before": self.physical, "after": self.physical,
                   "read_only_handoff": True, "journal_mode": "delete",
                   "automatic_retry": False, "gate_credit": False, "synthetic_adapter_only": False,
                   "held_store_digest": "0" * 64 if self.mode == "handoff_wrong_digest" else self.held,
                   "process_id": 303 if self.mode == "handoff_wrong_pid" else 101,
                   "context": context, "request_original": href, "response": writer_results[1],
                   "identity_request_original": iref, "identity_response": writer_results[2]}
        handoff_ref = self.put("writer/handoff.original.json", handoff)
        closure = {"status": "CLOSED", "database_path": self.physical["path"],
                   "participants": [{"helper_id": context["helper_id"],
                      "binary_path": context["binary_path"], "database_path": context["database_path"],
                      "process_streams": [{"process_id": 101, "exit_index": 0, "stdout_ended": True,
                                           "stderr_ended": True, "jsonl_reader_closed": True}],
                      "process_exits": [{"process_id": 101, "code": 0, "signal": None}]}]}
        closure_ref = self.put("writer/closure.original.json", closure)
        self.final = {"service_closure": closure_ref,
                      "database_initial": self.put("writer/store-first.json", self.physical),
                      "database_final": self.put("writer/store-last.json", self.physical)}
        intent = {"schema": "motionwright.graph.same-original-store-reopen/1", "source": self.source,
                  "binary": self.ordinary, "argv": [self.ordinary["path"], "--database",
                         self.physical["path"], *([] if self.mode == "writable_argv" else ["--read-only"])],
                  "read_only_child_required": True, "original_writer_read_only_handoff": handoff_ref,
                  "original_preseal_held_pager_witness": preseal_ref, "original_service_closure": closure_ref,
                  "allowed_commands": ["bootstrap", "get_project", "project_history",
                                       "opened_store_identity_witness"], "automatic_retry": False}
        self.reopen_path = self.root / "graph-flow" / "independent-reopen" / "terminal.original.json"
        self.put("graph-flow/independent-reopen/intent.original.json", intent)
        reqs = [
            ("bootstrap", {"request": {"project_id": project["id"]}}, {
                "read_only": self.mode != "bootstrap_writable", "test_transport": "service-test-jsonl",
                "evidence_scope": "real_service_sqlite_cas_read_only; excludes_tauri_installer_native_render",
                "project": project}),
            ("get_project", {"request": {"project_id": project["id"]}}, project),
            ("project_history", {"request": {"project_id": project["id"],
                                            "after_revision": 0, "limit": 100}},
             [] if self.mode == "journal_missing_event" else journal),
            ("project_history", {"request": {"project_id": project["id"],
                                            "after_revision": 2 if self.mode != "journal_missing_event" else 0,
                                            "limit": 100}}, []),
            ("opened_store_identity_witness", None, self.held),
            ("get_project", {"request": {"project_id": project["id"]}}, project),
            ("opened_store_identity_witness", None, self.held),
        ]
        reader_refs, reader_results = [], []
        for i, (command, args, result) in enumerate(reqs):
            request = {"id": f"same-store-read-{i}", "command": command, "args": args}
            reader_refs.append(self.request(f"reader/requests/{i}.jsonl", request))
            reader_results.append(response(request, result))
        if self.mode == "reader_raw_duplicate":
            reader_results.append(reader_results[0])
        reader_raw = b"".join(json.dumps(x, separators=(",", ":")).encode() + b"\n"
                              for x in reader_results)
        reader_stdout = self.put("reader/stdout.original", reader_raw)
        desc_refs = []
        for i in range(2):
            obs = {"source": self.source, "binary": self.ordinary, "held_store_digest": self.held,
                   "phase": ("reversed" if self.mode == "descriptor_wrong_phase" else
                             ["after-first-project-and-complete-journal", "after-second-project-before-eof"][i]),
                   "synthetic_adapter_only": False, "gate_credit": False,
                   "observation": {"process": {"pid": 202},
                       "source_physical": {"path": str(self.root / "other.sqlite3"),
                                           **{k: self.physical[k] for k in ("device", "inode")}}
                                          if self.mode == "descriptor_other_store" else self.physical,
                       "descriptor_observed_live": True}}
            desc_refs.append(self.put(f"reader/descriptor-{i}.original.json", obs))
        oracle_ref = self.put("reader/oracle.original.json", {"project": project, "journal": journal})
        observed_ref = self.put("reader/observed.original.json", {
            "project": project, "journal": journal,
            "journalComplete": True, "journalAfterRevisionExclusive": 0})
        first_line = writer_lines[0]
        self.reopen = {"physical_store_before": self.physical,
                       "original_writer_read_only_handoff": handoff_ref,
                       "original_writer_handoff_raw": stdout_ref,
                       "original_preseal_held_pager_witness": preseal_ref,
                       "original_service_closure": closure_ref,
                       "original_preseal_response_raw": {
                           "stream": stdout_ref, "offset": 0, "size_bytes": len(first_line),
                           "sha256": hashlib.sha256(first_line).hexdigest()},
                       "read_only_child_bootstrap_proved": self.mode != "bootstrap_flag_false",
                       "service_process": {"pid": 202, "code": 0, "signal": None},
                       "raw": {"stdout": {**reader_stdout, "eof": True}},
                       "requests": reader_refs, "expected_oracle": oracle_ref,
                       "observed_oracle": observed_ref,
                       "opened_descriptor_observations": desc_refs}
        if self.mode.startswith("missing_"):
            self.reopen.pop(self.mode.removeprefix("missing_"), None)


class GraphR74ReadOnlyArchiveTests(unittest.TestCase):
    def check(self, mode="valid"):
        fixture = InertOriginals(mode)
        with mock.patch.object(gate, "graph_ref",
                               side_effect=lambda path: fixture.refs[str(path)]):
            return gate.graph_verify_readonly_reopen_originals(
                fixture.reopen, fixture.index, fixture.final,
                fixture.reopen_path, fixture.retained)

    def test_original_handoff_and_distinct_read_only_reader_source_fixture(self):
        self.assertIs(self.check(), True)

    def test_missing_or_false_required_originals_are_rejected(self):
        for mode in ("missing_original_writer_read_only_handoff",
                     "missing_original_writer_handoff_raw",
                     "missing_read_only_child_bootstrap_proved",
                     "bootstrap_flag_false", "bootstrap_writable", "writable_argv"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_changed_native_writer_pid_source_digest_or_raw_is_rejected(self):
        for mode in ("handoff_wrong_source", "handoff_wrong_digest", "handoff_wrong_pid",
                     "writer_raw_missing_handoff", "writer_raw_reordered", "writer_raw_duplicate"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_wrong_reader_store_journal_or_replayed_raw_is_rejected(self):
        for mode in ("descriptor_other_store", "descriptor_wrong_phase", "database_outside_case",
                     "journal_missing_event", "reader_raw_duplicate"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)


if __name__ == "__main__":
    unittest.main()
