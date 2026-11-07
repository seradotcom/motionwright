"""Source-only Graph provider-release checks, with real scratch files.

Synthetic replies never authenticate a Semwright command, Host or render.
The production source function is exercised; Stage G2 must independently
observe a live provider lease shutdown, original CLI raw/terminal and SQLite.
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

SOURCE=Path(__file__).resolve().parents[1]/"native_graph_browser_owner.py"
functions=[node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
           if isinstance(node,ast.FunctionDef) and node.name=="graph_quiesce_v6"]
assert len(functions)==1

def need(ok,code):
    if not ok: raise ValueError(code)

def serialize(obj):
    return (json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()

def sha(b):return hashlib.sha256(b).hexdigest()

def ref(path):
    b=Path(path).read_bytes()
    return {"path":str(path),"sha256":sha(b),"size_bytes":len(b)}

def identity(path):
    p=Path(path); stat=p.stat()
    return {"path":str(p),"device":stat.st_dev,"inode":stat.st_ino,"sha256":sha(p.read_bytes())}

class LeaseLedger:
    def __init__(self):self.failed=False;self.status="READY";self.seals=0
    def seal(self):self.seals+=1
    def snapshot(self):return [{"status":self.status}]

class SourceOnlyReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="graph-provider-release-source-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=self.root/"motionwright.sqlite3";self.store.write_bytes(b"synthetic-sqlite-identity")
        self.client_dir=self.root/"clients";self.client_dir.mkdir()
        self.session=self.client_dir/"client-original.session"
        self.session.write_bytes(b"synthetic-original-session")
        self.mount={"provider":"driver:motionwright","connection":"connection-original",
                    "provider_generation":7,
                    "mount":{"root":"motionwright-data","path":str(self.root),
                             "read":True,"write":True,"execute":False}}
        self.calls=[]
        self.source_checks=0
        self.release_error=None
        self.changed_mount=False
        self.source_error=None
        self.original_missing=False
        self.changed_store=False
        self.daemon_exit=None
        self.invalid_envelope=None
        self.ledger=LeaseLedger()
        self.lifecycle=SimpleNamespace(evidence=self.root,children=[],closed=False)
        self.daemon=SimpleNamespace(child=SimpleNamespace(poll=lambda:self.daemon_exit))
        self.obj=SimpleNamespace(closed=False,native_quiescence=None,
                    client_ledger=self.ledger, daemon=self.daemon,
                    lifecycle=self.lifecycle,paths={"clients":self.client_dir},
                    database=self.store,database_initial=identity(self.store),
                    clients=[{"status":"CLIENT_READY_NOT_ACCEPTANCE",
                              "session":{"path":str(self.session)},
                              "actual_mount_observation":self.mount}])

        def source_check(_owner):
            self.source_checks+=1
            if self.source_error:raise ValueError(self.source_error)

        def invoke(label,socket,session,command,args):
            self.calls.append({"label":label,"session":str(session),
                               "command":command,"args":args})
            if self.release_error and command=="providers.release_bound":
                raise ValueError(self.release_error)
            if command=="providers.mount.observe":
                output={**self.mount,"binding_ref":"new-session-bound-original"}
                if self.changed_mount:output["connection"]="foreign-provider-lease"
            elif command=="providers.release_bound":
                need(args=={"provider":"driver:motionwright","root":"motionwright-data",
                            "binding_ref":"new-session-bound-original"},
                     "synthetic-release-argument-drift")
                output={"provider":"driver:motionwright","connection":"connection-original",
                        "provider_generation":7,"binding_ref":"new-session-bound-original",
                        "catalog_revision":9,"lease_shutdown_completed":True,
                        "native_process_closure_inferred":False}
            else:raise AssertionError("Unrecognized source-only CLI")
            stdout=self.root/(label+".stdout.original")
            stderr=self.root/(label+".stderr.original")
            # Real self.invoke() returns the validated semwright CLI envelope,
            # not output directly. Reproduce that exact shape in these tests.
            envelope={"ok":True,"data":output}
            if self.invalid_envelope == command + ":false_ok":
                envelope["ok"]=False
            elif self.invalid_envelope == command + ":missing_data":
                envelope.pop("data")
            elif self.invalid_envelope == command + ":bare_data":
                envelope=output
            stdout.write_bytes(serialize(envelope))
            stderr.write_bytes(b"")
            (self.root/(label+".terminal.json")).write_bytes(
                serialize({"status":"SUCCESS","synthetic":True}))
            if not self.original_missing:
                self.lifecycle.children.append(SimpleNamespace(
                    label=label,terminal={"status":"SUCCESS"},stdout=stdout,stderr=stderr))
            if command=="providers.release_bound" and self.changed_store:
                # Preserve the original inode in this source-only attack fixture.
                # Unlinking it before creating the replacement lets Linux
                # immediately reuse the freed inode, making a changed Store
                # look identical on st_dev/st_ino in hosted Python.
                original = identity(self.store)
                self.store.rename(self.root / "held-original-sqlite-inode")
                self.store.write_bytes(b"replacement-synthetic-sqlite")
                replacement = identity(self.store)
                need((replacement["device"], replacement["inode"]) !=
                     (original["device"], original["inode"]),
                     "source-only-store-replacement-fixture-did-not-change-inode")
            return envelope
        self.obj.invoke=invoke
        self.obj.socket=self.root/"broker.sock"

        def write_json(path,value):
            Path(path).write_bytes(serialize(value))
        namespace={
            "require":need,"observe_source_before_close":source_check,
            "file_identity":identity,"original_ref":ref,"new_json":write_json,
            "_OWNER_SPEC":{"source":{"sha":"a"*40}},
            "os":os,"Path":Path,
        }
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(SOURCE),"exec"),namespace)
        self.release=namespace["graph_quiesce_v6"]

    def assert_no_receipt(self):
        self.assertFalse((self.root/"owner-provider-release.original.json").exists())
        self.assertIs(self.obj.native_quiescence,False)

    def test_binding_release_uses_exact_same_session_and_preserves_live_parent(self):
        original=self.release(self.obj,request_id="original-request")
        self.assertEqual((original["event"],original["status"]),
                         ("PROVIDER_RELEASED","RELEASED"))
        self.assertTrue(original["lease_shutdown_completed"])
        self.assertFalse(original["native_process_closure_inferred"])
        self.assertFalse(self.lifecycle.closed)
        self.assertEqual(self.ledger.seals,1)
        self.assertEqual([item["command"] for item in self.calls],
                         ["providers.mount.observe","providers.release_bound"])
        self.assertTrue(all(item["session"]==str(self.session) for item in self.calls))
        self.assertEqual(self.source_checks,1)
        record=json.loads(Path(original["receipt_path"]).read_text())
        self.assertEqual(record["original_mount_observation"],self.mount)
        self.assertFalse(record["native_process_closure_inferred"])
        self.assertFalse(record["graph_acceptance"])
        self.assertFalse(record["automatic_retry"])
        self.assertTrue(record["owner_broker_remains_live"])
        self.assertEqual(record["original_store"]["inode"],record["current_store"]["inode"])
        with self.assertRaisesRegex(ValueError,"provider-release-one-shot"):
            self.release(self.obj,request_id="no-retry")
        self.assertEqual(len(self.calls),2)

    def test_mount_command_must_return_actual_ok_data_cli_envelope(self):
        self.invalid_envelope="providers.mount.observe:bare_data"
        with self.assertRaisesRegex(ValueError,
                "provider-release-fresh-mount-cli-envelope-not-authenticated"):
            self.release(self.obj,request_id="mount-envelope")
        self.assert_no_receipt()
        self.assertEqual(len(self.calls),1)

    def test_mount_command_false_ok_cannot_be_promoted(self):
        self.invalid_envelope="providers.mount.observe:false_ok"
        with self.assertRaisesRegex(ValueError,
                "provider-release-fresh-mount-cli-envelope-not-authenticated"):
            self.release(self.obj,request_id="mount-denied")
        self.assert_no_receipt()
        self.assertEqual(len(self.calls),1)

    def test_release_response_missing_data_cannot_be_promoted(self):
        self.invalid_envelope="providers.release_bound:missing_data"
        with self.assertRaisesRegex(ValueError,
                "provider-release-bound-cli-envelope-not-authenticated"):
            self.release(self.obj,request_id="release-no-data")
        self.assert_no_receipt()
        self.assertEqual(len(self.calls),2)

    def test_release_response_false_ok_cannot_be_promoted(self):
        self.invalid_envelope="providers.release_bound:false_ok"
        with self.assertRaisesRegex(ValueError,
                "provider-release-bound-cli-envelope-not-authenticated"):
            self.release(self.obj,request_id="release-denied")
        self.assert_no_receipt()
        self.assertEqual(len(self.calls),2)

    def test_provider_only_shutdown_preserves_live_owner_until_final_service_barrier(self):
        # Execute the real production finalization helper from its source AST.
        # ProviderLease.shutdown() is not an OwnerLifecycle.close() call.
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "graph_native_final_lifecycle_receipt"]
        self.assertEqual(len(nodes), 1)
        namespace = {"require": need}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"),
             namespace)
        finish = namespace["graph_native_final_lifecycle_receipt"]
        calls = []
        def finish_lifecycle(*, reason):
            calls.append(reason)
            self.lifecycle.closed = True
            return {"status": "CLOSED", "reason": reason}
        self.lifecycle.close = finish_lifecycle
        record = {"schema": "motionwright.graph-mounted-provider-release/1",
                  "provider_lease_shutdown_completed": True,
                  "native_process_closure_inferred": False,
                  "owner_broker_remains_live": True}
        self.assertFalse(self.lifecycle.closed)
        returned = finish(self.obj, reason="explicit-close",
                          native_quiescence={"record": record})
        self.assertEqual(returned["status"], "CLOSED")
        self.assertEqual(calls, ["explicit-close"])
        self.assertTrue(self.lifecycle.closed)
        with self.assertRaisesRegex(ValueError, "graph-provider-release-final-lifecycle-not-ready"):
            finish(self.obj, reason="explicit-close",
                   native_quiescence={"record": record})
        self.assertEqual(calls, ["explicit-close"])

    def test_broker_release_cannot_claim_native_close_or_reuse_old_quiescence(self):
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "graph_native_final_lifecycle_receipt"]
        namespace = {"require": need}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
        finish = namespace["graph_native_final_lifecycle_receipt"]
        self.lifecycle.close = lambda *, reason: {"status": "CLOSED"}
        for invalid in (
            {"schema": "motionwright.graph-native-owner-quiescence/1",
             "provider_lease_shutdown_completed": True,
             "native_process_closure_inferred": False,
             "owner_broker_remains_live": True},
            {"schema": "motionwright.graph-mounted-provider-release/1",
             "provider_lease_shutdown_completed": False,
             "native_process_closure_inferred": False,
             "owner_broker_remains_live": True},
            {"schema": "motionwright.graph-mounted-provider-release/1",
             "provider_lease_shutdown_completed": True,
             "native_process_closure_inferred": True,
             "owner_broker_remains_live": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError,
                                             "graph-provider-release-final-lifecycle-not-ready"):
                    finish(self.obj, reason="explicit-close",
                           native_quiescence={"record": invalid})

    def test_changed_source_denied_before_any_cli_command(self):
        self.source_error="changed-source"
        with self.assertRaisesRegex(ValueError,"changed-source"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()
        self.assertEqual(self.calls,[])

    def test_missing_broker_denies_before_source_or_cli(self):
        self.daemon_exit=1
        with self.assertRaisesRegex(ValueError,"provider-release-owner-not-live"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()
        self.assertEqual(self.calls,[])

    def test_foreign_lease_refuses_before_shutdown(self):
        self.changed_mount=True
        with self.assertRaisesRegex(ValueError,"provider-release-lease-or-generation-changed"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()
        self.assertEqual([x["command"] for x in self.calls],["providers.mount.observe"])

    def test_denied_upstream_command_is_not_positive_and_never_retried(self):
        self.release_error="source-only-permission-denied"
        with self.assertRaisesRegex(ValueError,"source-only-permission-denied"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()
        with self.assertRaisesRegex(ValueError,"provider-release-one-shot"):
            self.release(self.obj,request_id="retry")
        self.assertEqual(len(self.calls),2)

    def test_original_cli_custody_missing_is_not_a_release_receipt(self):
        self.original_missing=True
        with self.assertRaisesRegex(ValueError,"provider-release-original-command-not-complete"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()

    def test_replaced_store_after_release_denies_without_rebinding(self):
        self.changed_store=True
        with self.assertRaisesRegex(ValueError,"provider-release-original-store-changed"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()

    def test_unknown_client_denies_before_release(self):
        self.ledger.status="UNKNOWN"
        with self.assertRaisesRegex(ValueError,"provider-release-client-unknown"):
            self.release(self.obj,request_id="q")
        self.assert_no_receipt()
        self.assertEqual(self.calls,[])

if __name__=="__main__":
    unittest.main()
