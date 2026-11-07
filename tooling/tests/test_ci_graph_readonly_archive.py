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
            "native_graph_session": self.native, "ordinary_service": self.ordinary,
            "semwright": {"path": str(self.root / "bin" / "semwright")},
            "semwrightd": {"path": str(self.root / "bin" / "semwrightd")}}}
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
        owner_pid = 777
        owner_root = self.root / "graph-flow" / "owner"
        client_id = "original-native-client"
        session = self.put("graph-flow/owner/clients/" + client_id + ".session",
                           b"synthetic-inert-session-not-a-runtime-token")
        original_mount = {
            "provider": "driver:motionwright", "connection": "real-original-lease",
            "provider_generation": 7,
            "mount": {"root": "motionwright-data",
                      "path": str(Path(self.physical["path"]).parent),
                      "read": True, "write": True, "execute": False},
        }
        if self.mode == "release_mount_wrong_store":
            original_mount["mount"]["path"] = str(owner_root / "other-store")
        custody_mount = ({**original_mount, "provider": "other"}
                         if self.mode == "release_custody_changed" else original_mount)
        self.put("graph-flow/owner/clients/" + client_id + ".custody.json",
                 {"actual_mount_observation": custody_mount})
        fresh_mount = {**original_mount, "binding_ref": "source-bound-binding-one"}
        if self.mode == "release_fresh_foreign_connection":
            fresh_mount["connection"] = "foreign-lease"
        if self.mode == "release_fresh_foreign_generation":
            fresh_mount["provider_generation"] = 8
        released = {
            "provider": original_mount["provider"],
            "connection": original_mount["connection"],
            "provider_generation": original_mount["provider_generation"],
            "binding_ref": fresh_mount["binding_ref"],
            "catalog_revision": 11, "lease_shutdown_completed": True,
            "native_process_closure_inferred": False,
        }
        if self.mode == "release_binding_changed":
            released["binding_ref"] = "other-binding"
        if self.mode == "release_not_drained":
            released["lease_shutdown_completed"] = False
        if self.mode == "release_native_process_inferred":
            released["native_process_closure_inferred"] = True

        def make_cli(label, command, args, expected):
            label_name = "native-provider-release-" + label
            basename = "graph-flow/owner/" + label_name
            envelope = {"ok": True, "data": expected}
            if self.mode == "release_cli_false_ok" and label == "bound":
                envelope["ok"] = False
            elif self.mode == "release_cli_no_data" and label == "bound":
                envelope.pop("data")
            stdout = self.put(basename + ".stdout.original",
                              json.dumps(envelope, separators=(",", ":")).encode() + b"\n")
            stderr = self.put(basename + ".stderr.original", b"")
            def identity(ref):
                return {"path": ref["path"], "sha256": ref["sha256"],
                        "bytes": ref["size_bytes"]}
            terminal = {
                "schema": "motionwright.native.graph-browser-owner/1",
                "label": label_name, "status": "SUCCESS",
                "pid": 454 if label == "mount" else 455,
                "exit_code": 0, "automatic_retry": False,
                "forced_termination": False, "deadline_exceeded": False,
                "descendant_cleanup_required": False,
                "writers_closed_within_owned_group": True,
                "stdout": identity(stdout), "stderr": identity(stderr),
            }
            if self.mode == "release_cli_bad_exit" and label == "bound":
                terminal["exit_code"] = 1
            if self.mode == "release_cli_forced" and label == "bound":
                terminal["forced_termination"] = True
            terminal_ref = (None if self.mode == "release_cli_no_terminal" and label == "bound"
                            else self.put(basename + ".terminal.json", terminal))
            argv = [self.index["binaries"]["semwright"]["path"], "--socket",
                    str(owner_root / "runtime" / "broker.sock"),
                    "--session-file", session["path"], "--json", "execute", command,
                    "--args-json", json.dumps(args, separators=(",", ":"))]
            if self.mode == "release_cli_wrong_binary" and label == "bound":
                argv[0] = str(self.root / "bin" / "other-cli")
            if self.mode == "release_cli_other_session" and label == "bound":
                argv[4] = str(owner_root / "clients" / "foreign.session")
            if self.mode == "release_cli_other_binding" and label == "bound":
                argv[-1] = '{"provider":"other","root":"motionwright-data","binding_ref":"wrong"}'
            intent = {
                "schema": "motionwright.native.graph-browser-owner/1",
                "label": label_name, "argv": argv, "cwd": None,
                "automatic_retry": False, "owned_process_group": True,
            }
            if self.mode != "release_cli_no_intent" or label != "bound":
                self.put(basename + ".intent.json", intent)
            return {"terminal": terminal_ref or {
                       "path": str(owner_root / (label_name + ".terminal.json")),
                       "sha256": "0" * 64, "size_bytes": 0},
                    "stdout": stdout, "stderr": stderr}

        mount_originals = make_cli("mount", "providers.mount.observe", {
            "provider": "driver:motionwright", "root": "motionwright-data",
        }, fresh_mount)
        release_originals = make_cli("bound", "providers.release_bound", {
            "provider": "driver:motionwright", "root": "motionwright-data",
            "binding_ref": fresh_mount["binding_ref"],
        }, released)
        provider = {
            "schema": "motionwright.graph-mounted-provider-release/1",
            "source": ({"sha": "b" * 40} if self.mode == "release_wrong_source"
                       else self.source),
            "owner_pid": owner_pid,
            "source_verified_before_release": True,
            "provider_lease_shutdown_completed": True,
            "owner_broker_remains_live": True,
            "native_process_closure_inferred": False,
            "original_session": session,
            "original_mount_observation": original_mount,
            "fresh_mount_observation": fresh_mount,
            "provider_release_observation": released,
            "mount_command_originals": mount_originals,
            "release_command_originals": release_originals,
            "original_store": {"path": self.physical["path"],
                               "device": int(self.physical["device"]),
                               "inode": int(self.physical["inode"])},
            "current_store": {"path": self.physical["path"],
                              "device": int(self.physical["device"]),
                              "inode": int(self.physical["inode"])},
            "automatic_retry": False, "graph_acceptance": False,
        }
        if self.mode == "release_wrong_session":
            provider["original_session"] = {**session, "path": "/foreign/session"}
        if self.mode == "release_wrong_owner":
            provider["owner_pid"] = 101
        if self.mode == "release_wrong_inode":
            provider["current_store"]["inode"] += 1
        provider_path = ("other-source/owner-provider-release.original.json"
                         if self.mode == "release_foreign_case" else
                         "graph-flow/owner/owner-provider-release.original.json")
        provider_ref = self.put(provider_path, provider)
        if self.mode == "release_old_quiescence_key":
            handoff["native_owner_quiescence"] = provider_ref
        if self.mode != "release_missing":
            handoff["native_provider_release"] = provider_ref

        provider_event = {
            "event": "PROVIDER_RELEASED",
            "id": "release-graph-flow", "status": "RELEASED",
            "receipt_path": provider_ref["path"],
            "receipt_sha256": provider_ref["sha256"],
            "owner_pid": owner_pid,
            "lease_shutdown_completed": True,
            "native_process_closure_inferred": False,
        }
        if self.mode == "release_owner_event_changed":
            provider_event["receipt_sha256"] = "0" * 64
        connection = self.put("graph-flow/owner/clients/" + client_id + ".connection.json",
                              {"schema": "motionwright-semwright-connection/1",
                               "session_file": session["path"]})
        ready_event = {"event": "READY", "schema": "motionwright.graph-browser-owner/6",
                       "case_id": "graph-flow", "source_sha": self.source["sha"],
                       "owner_pid": owner_pid, "private_root": str(owner_root),
                       "native_authoring_approved": False}
        client_event = {"event": "CLIENT", "id": "client-request-one",
                        "client_id": client_id,
                        "child_env": {"MOTIONWRIGHT_GRAPH_CONNECTION": connection["path"]},
                        "connection_receipt": connection["path"]}
        if self.mode == "parent_ready_wrong_source":
            ready_event["source_sha"] = "0" * 40
        if self.mode == "parent_ready_wrong_private_root":
            ready_event["private_root"] = str(self.root / "other-source")
        if self.mode == "parent_client_wrong_id":
            client_event["id"] = "foreign-request"
        if self.mode == "parent_client_wrong_connection":
            client_event["connection_receipt"] = str(self.root / "other-source" / "client.json")
        responses = []
        if self.mode != "parent_missing_ready":
            responses.append(ready_event)
        if self.mode == "parent_duplicate_ready":
            responses.append(ready_event)
        if self.mode != "parent_missing_client":
            responses.append(client_event)
        if self.mode == "parent_duplicate_client":
            responses.append(client_event)
        if self.mode == "release_owner_event_duplicate":
            responses.append(provider_event)
        if self.mode != "release_owner_event_missing":
            responses.append(provider_event)
        if self.mode == "parent_client_after_release":
            responses.append(client_event)
        responses.append({"event": "FINAL_RECEIPT", "id": "close-graph-flow"})
        if self.mode == "release_parent_error_before_final":
            responses.insert(1, {"event": "ERROR", "code": "NATIVE_ORIGINAL_FAILED",
                                 "runtime_stage": "PROVIDER_RELEASED"})
        if self.mode == "release_parent_error_after_final":
            responses.append({"event": "ERROR", "code": "NATIVE_ORIGINAL_FAILED",
                              "runtime_stage": "OWNER_CLOSE"})
        owner_stdout = self.put(
            "graph-flow/owner-parent.stdout.original",
            b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n"
                     for row in responses))
        owner_stderr = self.put("graph-flow/raw/python-owner.stderr.original", b"")
        stderr_ref = {**owner_stderr, "eof": self.mode != "release_parent_stderr_no_eof"}
        if self.mode == "release_parent_stderr_digest_changed":
            stderr_ref["sha256"] = "0" * 64
        if self.mode == "release_parent_stderr_foreign_path":
            stderr_ref["path"] = str(self.root / "other-source" / "stderr.original")
        parent_raw = {"stdout": {**owner_stdout, "eof": True}}
        if self.mode != "release_parent_stderr_missing":
            parent_raw["stderr"] = stderr_ref
        self.put("graph-flow/owner-parent-terminal.original.json", {
            "exit": {"process_id": owner_pid, "code": 0, "signal": None},
            "raw": parent_raw,
        })
        lifecycle = {"status": "CLOSED", "taints": [], "start_failures": []}
        cohort = {"status": "PASS", "authority": "process_custody_only_not_revocation",
                  "signals": ([] if self.mode != "release_emergency_signaled"
                              else [{"pid": 451, "signal": 9}]),
                  "complete_empty_descendant_tree": {
                      "root_observed": True, "truncated": False,
                      "processes": [{"pid": owner_pid, "ppid": 0}],
                  }}
        if self.mode == "release_cohort_extra_member":
            cohort["complete_empty_descendant_tree"]["processes"].append(
                {"pid": 889, "ppid": owner_pid})
        if self.mode == "release_owner_final_unknown":
            lifecycle["status"] = "UNKNOWN"
        self.owner_complete = self.put(
            "graph-flow/owner/owner-complete-close.original.json", {
                "lifecycle": lifecycle, "cohort": cohort, "status": "CLOSED",
                "owner_streams_eof": True, "source_validated_before_close": True,
            })
        self.native_client_session = session
        self.native_client_id = client_id
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
                      "database_final": self.put("writer/store-last.json", self.physical),
                      "owner_close": self.owner_complete,
                      "client_roster": [{"id": "client-request-one",
                                         "client_id": self.native_client_id,
                                         "status": "READY",
                                         "connection_original": connection,
                                         "session_original": self.native_client_session}]}
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

    def test_provider_only_release_requires_original_same_session_broker_lease(self):
        for mode in ("release_missing", "release_old_quiescence_key",
                     "release_wrong_source", "release_wrong_session",
                     "release_wrong_owner", "release_wrong_inode",
                     "release_foreign_case", "release_mount_wrong_store",
                     "release_custody_changed", "release_fresh_foreign_connection",
                     "release_fresh_foreign_generation", "release_binding_changed",
                     "release_not_drained", "release_native_process_inferred"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_provider_release_rejects_changed_cli_originals_and_envelopes(self):
        for mode in ("release_cli_false_ok", "release_cli_no_data",
                     "release_cli_bad_exit", "release_cli_forced",
                     "release_cli_no_terminal", "release_cli_wrong_binary",
                     "release_cli_other_session", "release_cli_other_binding",
                     "release_cli_no_intent"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_provider_release_emits_only_one_original_owner_event(self):
        for mode in ("release_owner_event_changed", "release_owner_event_missing",
                     "release_owner_event_duplicate",
                     "release_emergency_signaled", "release_owner_final_unknown",
                     "release_cohort_extra_member"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_parent_original_error_events_deny_clean_provider_release(self):
        for mode in ("release_parent_error_before_final",
                     "release_parent_error_after_final"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_parent_original_stderr_custody_must_be_complete(self):
        for mode in ("release_parent_stderr_missing",
                     "release_parent_stderr_no_eof",
                     "release_parent_stderr_digest_changed",
                     "release_parent_stderr_foreign_path"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_parent_ready_and_clients_match_original_request_roster(self):
        for mode in ("parent_missing_ready", "parent_duplicate_ready",
                     "parent_ready_wrong_source", "parent_ready_wrong_private_root",
                     "parent_missing_client", "parent_duplicate_client",
                     "parent_client_wrong_id", "parent_client_wrong_connection",
                     "parent_client_after_release"):
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
