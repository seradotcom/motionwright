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
        self.physical = {"path": str(self.root / ("other-source" if mode == "database_outside_case" else "g1") / "store" / "motionwright.sqlite3"),
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
        if self.mode == "writer_success_with_error_field":
            writer_results[1]["error"] = "Hidden unauthorized failure alongside success."
        if self.mode == "writer_raw_missing_handoff":
            original_writer_results = [writer_results[0], writer_results[2]]
        elif self.mode == "writer_raw_reordered":
            original_writer_results = [writer_results[1], writer_results[0], writer_results[2]]
        elif self.mode == "writer_raw_duplicate":
            original_writer_results = [*writer_results, writer_results[1]]
        elif self.mode == "writer_raw_extra_unmatched":
            # Original writer emitted an additional successful response that
            # the controlled request roster did not authorize.
            original_writer_results = [*writer_results,
                                       {"id": "unmatched-effect", "ok": True,
                                        "result": {"mutation": "unreviewed"}}]
        elif self.mode == "writer_raw_legitimate_project_reads":
            # Actual StudioService calls observeStoredProject() in the case
            # body, preseal, and again after prepare_read_only_handoff. These
            # source-owned read replies must not invalidate the three RPCs.
            original_writer_results = [
                {"id": "session:observed-before", "ok": True, "result": project},
                writer_results[0],
                {"id": "session:observed-after", "ok": True, "result": project},
                writer_results[1], writer_results[2],
            ]
        elif self.mode in ("writer_raw_legitimate_denied_effect",
                           "writer_raw_denied_effect_nonstring_error",
                           "writer_raw_legitimate_typed_graph_denial",
                           "writer_raw_legitimate_projection_denial",
                           "writer_raw_wrong_graph_error_code"):
            # Native Graph mutation errors and projection failures are typed
            # JSON objects from the actual Rust source, not fabricated strings.
            typed = {"schema": "motionwright.graph-mutation-error/1",
                     "request_id": "ABCDEF12-3456-4789-aBcD-EF1234567890",
                     "outcome_known": False, "code": "graph_mutation_failed"}
            if self.mode == "writer_raw_legitimate_denied_effect":
                denial = "stale revision"
            elif self.mode == "writer_raw_legitimate_typed_graph_denial":
                denial = typed
            elif self.mode == "writer_raw_legitimate_projection_denial":
                denial = {"code": "stale_revision", "expected": "revision:2",
                          "actual": "revision:3"}
            elif self.mode == "writer_raw_wrong_graph_error_code":
                denial = {**typed, "code": "invented_error_code"}
            else:
                denial = {"invented": "structured-error-envelope"}
            original_writer_results = [
                {"id": "session:denied-effect", "ok": False, "error": denial},
                *writer_results,
            ]
        else:
            original_writer_results = writer_results
        writer_lines = [json.dumps(x, separators=(",", ":")).encode() + b"\n"
                        for x in original_writer_results]
        writer_raw = b"".join(writer_lines)
        stdout_ref = self.put("writer/stdout.original", writer_raw)
        source_requests = {row["id"]: row for row in
                           (preseal_request, handoff_request, identity_request)}
        if self.mode == "writer_raw_legitimate_project_reads":
            for name in ("observed-before", "observed-after"):
                row = {"id": "session:" + name, "command": "get_project",
                       "args": {"request": {"project_id": project["id"]}}}
                source_requests[row["id"]] = row
        if self.mode in ("writer_raw_legitimate_denied_effect",
                         "writer_raw_denied_effect_nonstring_error",
                         "writer_raw_legitimate_typed_graph_denial",
                         "writer_raw_legitimate_projection_denial",
                         "writer_raw_wrong_graph_error_code"):
            source_requests["session:denied-effect"] = {
                "id": "session:denied-effect",
                "command": ("project_scene_page"
                            if self.mode == "writer_raw_legitimate_projection_denial"
                            else "apply_change"),
                "args": {"request": {"project_id": project["id"], "revision": 0,
                                     "request_id": "ABCDEF12-3456-4789-aBcD-EF1234567890"}}}
        authorized = [row for row in original_writer_results if row["id"] in source_requests]
        self.writer_roster_ref = self.put("g1/original-service-writer-rpc-roster.original.json", {
            "schema": "motionwright.graph.original-writer-rpc-roster/1",
            "source": self.source, "binary": self.native,
            "database_path": self.physical["path"], "process_id": 101,
            "records": [{"request": {"id": source_requests[row["id"]]["id"],
                                     "command": source_requests[row["id"]]["command"],
                                     "args_sha256": hashlib.sha256(json.dumps(
                                         source_requests[row["id"]]["args"],
                                         separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()},
                         "response": row} for row in authorized],
            "automatic_retry": False, "gate_credit": False,
            "synthetic_adapter_only": False,
        })
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
        owner_root = self.root / "g1" / "owner"
        client_id = "original-native-client"
        session = self.put("g1/owner/clients/" + client_id + ".session",
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
        self.put("g1/owner/clients/" + client_id + ".custody.json",
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
            basename = "g1/owner/" + label_name
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
                         "g1/owner/owner-provider-release.original.json")
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
        connection = self.put("g1/owner/clients/" + client_id + ".connection.json",
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
        lifecycle = {"status": "CLOSED", "taints": [], "start_failures": []}
        cohort = {"status": "PASS", "authority": "process_custody_only_not_revocation",
                  "subreaper": True,
                  "signals": ([] if self.mode != "release_emergency_signaled"
                              else [{"pid": 451, "signal": 9}]),
                  "complete_empty_descendant_tree": {
                      "root_observed": True, "truncated": False,
                      "processes": [{"pid": owner_pid, "ppid": 0}],
                  }}
        if self.mode == "release_subreaper_missing":
            cohort.pop("subreaper")
        elif self.mode == "release_subreaper_false":
            cohort["subreaper"] = False
        if self.mode == "release_cohort_extra_member":
            cohort["complete_empty_descendant_tree"]["processes"].append(
                {"pid": 889, "ppid": owner_pid})
        if self.mode == "release_owner_final_unknown":
            lifecycle["status"] = "UNKNOWN"
        self.owner_complete = self.put(
            "g1/owner/owner-complete-close.original.json", {
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
        # The inert parent stdout is produced after the final original exists,
        # just as the real owner writes its receipt before emitting FINAL_RECEIPT.
        self.final.update(schema="motionwright.graph-browser-owner-final/6",
                          case_id="graph-flow", status="CLOSED",
                          owner_cohort_absent=True, owner_streams_eof=True,
                          capture_permitted=True)
        final_ref = self.put("g1/owner/owner-final.original.json", self.final)
        final_event = {
            "event": "FINAL_RECEIPT", "id": "close-graph-flow",
            "case_id": "graph-flow", "status": "CLOSED",
            "receipt_path": final_ref["path"], "receipt_sha256": final_ref["sha256"],
            "owner_cohort_absent": True, "owner_streams_eof": True,
            "capture_permitted": True,
        }
        if self.mode == "parent_final_wrong_id":
            final_event["id"] = "close-foreign-case"
        if self.mode == "parent_final_wrong_case":
            final_event["case_id"] = "foreign-case"
        if self.mode == "parent_final_wrong_status":
            final_event["status"] = "UNKNOWN"
        if self.mode == "parent_final_wrong_receipt_path":
            final_event["receipt_path"] = str(self.root / "other-source" / "final.json")
        if self.mode == "parent_final_wrong_receipt_hash":
            final_event["receipt_sha256"] = "0" * 64
        if self.mode == "parent_final_false_cohort":
            final_event["owner_cohort_absent"] = False
        if self.mode == "parent_final_false_streams":
            final_event["owner_streams_eof"] = False
        if self.mode == "parent_final_false_capture":
            final_event["capture_permitted"] = False
        if self.mode == "parent_final_extra_field":
            final_event["accepted"] = True
        final_slot = next(i for i, event in enumerate(responses)
                          if event["event"] == "FINAL_RECEIPT")
        responses[final_slot] = final_event
        owner_stdout = self.put(
            "g1/owner-parent.stdout.original",
            b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n"
                     for row in responses))
        owner_stderr = self.put("g1/raw/python-owner.stderr.original", b"")
        stderr_ref = {**owner_stderr, "eof": self.mode != "release_parent_stderr_no_eof"}
        if self.mode == "release_parent_stderr_digest_changed":
            stderr_ref["sha256"] = "0" * 64
        if self.mode == "release_parent_stderr_foreign_path":
            stderr_ref["path"] = str(self.root / "other-source" / "stderr.original")
        parent_raw = {"stdout": {**owner_stdout, "eof": True}}
        if self.mode != "release_parent_stderr_missing":
            parent_raw["stderr"] = stderr_ref
        self.put("g1/owner-parent-terminal.original.json", {
            "exit": {"process_id": owner_pid, "code": 0, "signal": None},
            "raw": parent_raw,
        })
        # Inert retained originals are only source-oracle test inputs. They
        # must match the exact private GraphCaseFactoryCore.request protocol
        # published before the genuine owner stdin write.
        controls = [
            {"op": "new-client", "id": self.final["client_roster"][0]["id"]},
            {"op": "quiesce", "id": "release-graph-flow"},
            {"op": "close", "id": "close-graph-flow", "service_barrier": {
                "closed": True, "receipt_path": closure_ref["path"],
                "receipt_sha256": closure_ref["sha256"]}},
        ]
        if self.mode == "control_wrong_client_id":
            controls[0]["id"] = "different-client-request"
        if self.mode == "control_duplicate_request_id":
            controls[1]["id"] = controls[0]["id"]
        if self.mode == "control_wrong_release_id":
            controls[1]["id"] = "release-other-case"
        if self.mode == "control_wrong_barrier":
            controls[2]["service_barrier"]["receipt_sha256"] = "0" * 64
        if self.mode == "control_extra_field":
            controls[0]["approved"] = True
        if self.mode == "control_swapped_order":
            controls[0], controls[1] = controls[1], controls[0]
        for index, value in enumerate(controls):
            if self.mode == "control_missing_new_client" and index == 0:
                continue
            if self.mode == "control_missing_quiesce" and index == 1:
                continue
            raw = json.dumps(value, separators=(",", ":")).encode()
            if self.mode == "control_missing_newline" and index == 0:
                self.put("g1/control-0.original.jsonl", raw)
            elif self.mode == "control_repeated_raw_line" and index == 0:
                self.put("g1/control-0.original.jsonl", (raw + b"\n") * 2)
            else:
                self.put(f"g1/control-{index}.original.jsonl", raw + b"\n")
        intent = {"schema": "motionwright.graph.same-original-store-reopen/1", "source": self.source,
                  "binary": self.ordinary, "argv": [self.ordinary["path"], "--database",
                         self.physical["path"], *([] if self.mode == "writable_argv" else ["--read-only"])],
                  "read_only_child_required": True, "original_writer_read_only_handoff": handoff_ref,
                  "original_writer_rpc_roster": self.writer_roster_ref,
                  "original_preseal_held_pager_witness": preseal_ref, "original_service_closure": closure_ref,
                  "allowed_commands": ["bootstrap", "get_project", "project_history",
                                       "opened_store_identity_witness"], "automatic_retry": False}
        self.reopen_path = self.root / "g1" / "independent-reopen" / "terminal.original.json"
        self.put("g1/independent-reopen/intent.original.json", intent)
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
        if self.mode == "reader_raw_reordered":
            reader_results[0], reader_results[1] = reader_results[1], reader_results[0]
        if self.mode == "reader_success_with_error_field":
            reader_results[1]["error"] = "Hidden unauthorized failure alongside success."
        reader_raw = b"".join(json.dumps(x, separators=(",", ":")).encode() + b"\n"
                              for x in reader_results)
        reader_stdout = self.put("g1/independent-reopen/service.stdout.original", reader_raw)
        reader_stderr = self.put("g1/independent-reopen/service.stderr.original", b"")
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
        first_index = next((i for i, row in enumerate(original_writer_results)
                            if row["id"] == preseal_request["id"]), 0)
        first_line = writer_lines[first_index]
        first_offset = sum(map(len, writer_lines[:first_index]))
        self.reopen = {"physical_store_before": self.physical,
                       "original_writer_read_only_handoff": handoff_ref,
                       "original_writer_handoff_raw": stdout_ref,
                       "original_writer_rpc_roster": self.writer_roster_ref,
                       "original_preseal_held_pager_witness": preseal_ref,
                       "original_service_closure": closure_ref,
                       "original_preseal_response_raw": {
                           "stream": stdout_ref, "offset": first_offset, "size_bytes": len(first_line),
                           "sha256": hashlib.sha256(first_line).hexdigest()},
                       "read_only_child_bootstrap_proved": self.mode != "bootstrap_flag_false",
                       "service_process": {"pid": 202, "code": 0, "signal": None},
                       "raw": {"stdout": {**reader_stdout, "eof": self.mode != "reader_stdout_no_eof"},
                               "stderr": {**reader_stderr, "eof": self.mode != "reader_stderr_no_eof"}},
                       "requests": reader_refs, "expected_oracle": oracle_ref,
                       "observed_oracle": observed_ref,
                       "opened_descriptor_observations": desc_refs}
        if self.mode == "reader_stderr_missing":
            self.reopen["raw"].pop("stderr")
        elif self.mode == "reader_stderr_bad_digest":
            self.reopen["raw"]["stderr"]["sha256"] = "0" * 64
        elif self.mode == "reader_stderr_foreign_path":
            self.reopen["raw"]["stderr"]["path"] = str(self.root / "other-source" / "stderr.original")
        elif self.mode == "reader_raw_unknown_stream":
            self.reopen["raw"]["extra"] = {**reader_stderr, "eof": True}
        if self.mode.startswith("missing_"):
            self.reopen.pop(self.mode.removeprefix("missing_"), None)


class GraphR74ReadOnlyArchiveTests(unittest.TestCase):
    def check(self, mode="valid", *, fixture=None):
        fixture = InertOriginals(mode) if fixture is None else fixture
        with mock.patch.object(gate, "graph_ref",
                               side_effect=lambda path, maximum=gate.GRAPH_BYTES * 512: fixture.refs[str(path)]):
            return gate.graph_verify_reopen_archive_originals(
                fixture.reopen, fixture.index, fixture.final,
                fixture.reopen_path, fixture.retained)

    def test_original_r74_slot_and_typed_graph_case_id_are_distinct(self):
        fixture = InertOriginals()
        slots = {typed: gate.graph_case_layout(fixture.root, typed)["private_root"]
                 for typed, _, _ in gate.GRAPH_CASES}
        self.assertEqual(len(set(slots.values())), 6)
        self.assertEqual(slots["graph-flow"], fixture.root / "g1")
        self.assertEqual(fixture.reopen_path.parent.parent, fixture.root / "g1")
        self.assertEqual(fixture.final["case_id"], "graph-flow")
        self.assertNotEqual(fixture.reopen_path.parent.parent.name,
                            fixture.final["case_id"])
        # The historical fixture's "graph-flow/" directory never existed
        # in the real producer. It must be refused before reading any
        # attacker-controlled original refs, despite its semantic ID.
        handoff_ref = fixture.reopen["original_writer_read_only_handoff"]
        handoff = json.loads(fixture.retained(handoff_ref))
        legacy = fixture.root / "graph-flow" / "independent-reopen" / "terminal.original.json"
        with self.assertRaisesRegex(ValueError, "not canonical g1-g6"):
            gate.graph_verify_mounted_provider_release(
                handoff, fixture.index, fixture.final, fixture.reopen,
                legacy, fixture.retained)

    def test_original_handoff_and_distinct_read_only_reader_source_fixture(self):
        self.assertIs(self.check(), True)

    def test_preseal_response_is_a_range_in_the_complete_original_writer_stream(self):
        fixture = InertOriginals("writer_raw_legitimate_project_reads")
        segment = fixture.reopen["original_preseal_response_raw"]
        self.assertEqual(set(segment), {"stream", "offset", "size_bytes", "sha256"})
        self.assertGreater(segment["offset"], 0)
        self.assertIs(self.check(fixture=fixture), True)

    def test_preseal_response_range_cannot_change_stream_bounds_or_hash(self):
        for mutation in ("stream", "offset", "length", "empty", "hash", "boolean-offset"):
            with self.subTest(mutation=mutation):
                fixture = InertOriginals("writer_raw_legitimate_project_reads")
                segment = fixture.reopen["original_preseal_response_raw"]
                if mutation == "stream": segment["stream"] = fixture.reopen["original_service_closure"]
                elif mutation == "offset": segment["offset"] += 1
                elif mutation == "length": segment["size_bytes"] -= 1
                elif mutation == "empty": segment["size_bytes"] = 0
                elif mutation == "hash": segment["sha256"] = "0" * 64
                elif mutation == "boolean-offset": segment["offset"] = True
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(fixture=fixture)

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

    def test_original_writer_legitimate_project_reads_are_fully_bound(self):
        # A real StudioService executes observeStoredProject() during preseal
        # and again after its handoff. Those read-only replies are authentic,
        # not an excuse to accept an unpaired/unmatched native mutation.
        self.assertIs(self.check("writer_raw_legitimate_project_reads"), True)

    def test_original_writer_expected_denial_retains_strict_wire_envelope(self):
        # A stale-CAS effect may be rejected by the actual writer before
        # its clean handoff; the denial remains in the bound original roster.
        self.assertIs(self.check("writer_raw_legitimate_denied_effect"), True)

    def test_original_writer_accepts_actual_structured_native_mutation_error(self):
        # GraphInvokeFailure::to_value is a closed object in the compiled
        # Native backend, not an Error.message converted to a string.
        self.assertIs(self.check("writer_raw_legitimate_typed_graph_denial"), True)

    def test_original_writer_accepts_actual_typed_projection_failure(self):
        # project_projection::ProjectionFailure is serde-serialized as a
        # tagged JSON object, including its revision fields.
        self.assertIs(self.check("writer_raw_legitimate_projection_denial"), True)

    def test_original_writer_rejects_forged_structured_denial(self):
        # A forged object with neither the native mutation schema nor the
        # projection failure schema is not an arbitrary source-side rejection.
        for mode in ("writer_raw_denied_effect_nonstring_error",
                     "writer_raw_wrong_graph_error_code"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_unmatched_successful_writer_response_cannot_receive_custody_credit(self):
        # An extra raw response absent from the original writer's complete
        # source-owned request/response roster is never given closure credit.
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.check("writer_raw_extra_unmatched")

    def test_original_successful_service_response_cannot_carry_hidden_error_fields(self):
        # The exact compiled StudioService Session emits ONLY id/ok/result on
        # success, not a success envelope with a hidden error side channel.
        # Both pre-handoff author writer and second independent reader must
        # match that original wire protocol before source custody is credited.
        for mode in ("writer_success_with_error_field", "reader_success_with_error_field"):
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

    def test_provider_release_needs_actual_subreaper_for_final_empty_tree(self):
        for mode in ("release_subreaper_missing", "release_subreaper_false"):
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

    def test_original_final_receipt_must_bind_source_final_and_close_id(self):
        for mode in ("parent_final_wrong_id", "parent_final_wrong_case",
                     "parent_final_wrong_status", "parent_final_wrong_receipt_path",
                     "parent_final_wrong_receipt_hash", "parent_final_false_cohort",
                     "parent_final_false_streams", "parent_final_false_capture",
                     "parent_final_extra_field"):
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

    def test_original_owner_control_stdin_requests_are_source_bound_one_shot(self):
        # The parent logs an original control line BEFORE each private owner
        # stdin effect. The R74 archive must not infer these requests from
        # an apparently valid owner stdout or a forged final JSON receipt.
        for mode in ("control_missing_new_client", "control_missing_quiesce",
                     "control_wrong_client_id", "control_duplicate_request_id",
                     "control_wrong_release_id", "control_wrong_barrier",
                     "control_extra_field", "control_swapped_order",
                     "control_missing_newline", "control_repeated_raw_line"):
            with self.subTest(mode=mode):
                with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
                    self.check(mode)

    def test_original_reader_stdout_preserves_synchronous_request_order(self):
        # studio_review_session::run() replies and flushes each response
        # before accepting the next request. Reordered raw stdout is not
        # compatible with the original single-process Session transport.
        with self.assertRaises((ValueError, KeyError, TypeError, AssertionError)):
            self.check("reader_raw_reordered")

    def test_independent_reader_both_original_raw_streams_and_eof_are_required(self):
        for mode in ("reader_stderr_missing", "reader_stderr_no_eof",
                     "reader_stderr_bad_digest", "reader_stderr_foreign_path",
                     "reader_stdout_no_eof", "reader_raw_unknown_stream"):
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
