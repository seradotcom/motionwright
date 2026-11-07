"""Physical diagnostic custody controls; these are not Native case executions."""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_private_evidence as seal
import native_graph_negative_evidence as negative
import ci_canvas_service as controller


class GraphNegativeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.key.public_key().public_bytes(serialization.Encoding.DER,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": hashlib.sha256(der).hexdigest()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gn-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "v"
        (self.root / "e/graph-producer").mkdir(parents=True)
        (self.root / "tools").mkdir()
        self.cases = [(case, "g" + str(i)) for i, case in enumerate(sorted(row[0] for row in controller.GRAPH_CASES), 1)]
        self.binding = {"context": {"source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "42", "run_attempt": "1"},
            "index_sha256": "1" * 64, "receipt_sha256": "2" * 64, "receipt_status": "UNKNOWN",
            "review_carrier_sha256": "3" * 64, "recipient_spki_sha256": self.recipient["sha256"]}
        self.budget = {"maximum_bytes": 104 * negative.MIB, "maximum_members": 1232}
        self.admission = {"seconds": 600, "reserve_seconds": 300, "retry": "FORBIDDEN"}

    def put(self, name, raw):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def run_seal(self, **kwargs):
        return negative.seal_once(self.root, self.cases, self.binding, kwargs.get("recipient", self.recipient),
                                  kwargs.get("budget", self.budget), self.admission)

    def recover(self):
        raw = (self.root / "graph-negative/negative-originals.mwseal").read_bytes()
        plaintext, context = seal.decrypt_snapshot(raw, self.key)
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            members = {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers()}
        return members, json.loads(members["negative-manifest.json"]), context

    def test_real_encrypted_unknown_originals_roundtrip_excludes_live_store_and_keeps_no_plaintext_copy(self):
        original = b'{"status":"UNKNOWN","private_error":"capability-secret-first-cause"}\n'
        receipt = self.put("e/receipt.json", original)
        self.put("e/graph-cases/g1/owner/owner-error.original.json", b'{"status":"UNKNOWN","capture_permitted":false}')
        self.put("e/graph-cases/g1/store/motionwright.sqlite3", b"LIVE-STORE-DO-NOT-CAPTURE")
        self.put("e/graph-cases/g1/owner/state/staged-driver", b"UNSELECTED-ELF")
        self.put("e/graph-cases/semantic-graph-service-click-chromium/error-context.md", b"Production button disabled")
        self.put("e/graph-cases/g1/owner/clients/client-1.connection.json.graph-originals/requests/1.json", b'{"outcome":"UNKNOWN"}')
        before = negative.identity(receipt.stat())
        carrier = self.run_seal()
        members, manifest, context = self.recover()
        self.assertEqual(members["e/receipt.json"], original)
        self.assertIn("e/graph-cases/semantic-graph-service-click-chromium/error-context.md", members)
        self.assertIn("e/graph-cases/g1/owner/clients/client-1.connection.json.graph-originals/requests/1.json", members)
        self.assertFalse(any("/store/" in path or "/state/" in path for path in members))
        self.assertEqual(context, self.binding["context"])
        self.assertEqual(manifest["binding"], self.binding)
        self.assertEqual(manifest["effects"], "UNKNOWN")
        self.assertEqual(manifest["quiescence"], "UNKNOWN")
        self.assertFalse(manifest["formal_acceptance"] or manifest["store_capture_permitted"])
        self.assertEqual(carrier["public_original_upload"], "HOLD")
        self.assertEqual(negative.identity(receipt.stat()), before)
        self.assertTrue(any(row["status"] == "ABSENT_AT_READ_TIME" for row in manifest["records"]))
        self.assertEqual({p.name for p in (self.root / "graph-negative").iterdir()},
            {"attempt.original.json", "carrier.original.json", "negative-originals.mwseal"})
        for path in (self.root / "graph-negative").iterdir():
            self.assertNotIn(b"capability-secret-first-cause", path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unstable_file_is_not_copied_and_independent_first_error_survives(self):
        self.put("e/receipt.json", b'{"status":"UNKNOWN"}')
        target = self.put("e/graph-cases/g1/owner/owner-error.original.json", b"early-error")
        actual_read = os.read
        touched = []
        def changing_read(fd, amount):
            data = actual_read(fd, amount)
            if data == b"early-error" and not touched:
                touched.append(True)
                with target.open("ab") as output:
                    output.write(b"-still-writing")
            return data
        with patch.object(negative.os, "read", changing_read):
            self.run_seal()
        members, manifest, _ = self.recover()
        self.assertTrue(touched)
        self.assertIn("e/receipt.json", members)
        self.assertNotIn("e/graph-cases/g1/owner/owner-error.original.json", members)
        row = next(row for row in manifest["records"] if row.get("path") == "e/graph-cases/g1/owner/owner-error.original.json")
        self.assertEqual(row["status"], "NOT_CAPTURED")
        self.assertEqual(target.read_bytes(), b"early-error-still-writing")

    def test_continuation_seals_only_new_five_namespace_and_keeps_historical_unknown(self):
        cases = [("graph-flow", "g1"), ("graph-label-causal-diagram", "g2"),
                 ("graph-label-system-flow", "g3"), ("graph-lost-reply", "g4"), ("graph-removal", "g5")]
        case_root = "e/gc42-1"
        binding = {**self.binding, "continuation": {
            "selector": controller.GRAPH_CONTINUATION, "origin": controller.GRAPH_CONTINUATION_ORIGIN}}
        self.put("e/receipt.json", b'{"status":"UNKNOWN"}')
        current = b'{"status":"UNKNOWN","first_error":"current-private-error"}'
        self.put(case_root + "/g1/owner/owner-error.original.json", current)
        old = self.put("e/graph-cases/g6/owner/owner-error.original.json", b"HISTORICAL-UNKNOWN-DO-NOT-REUSE")
        for changed_root, changed_cases in (("e/graph-cases", cases), ("e/gc43-1", cases),
                (case_root, cases + [("graph-source-review", "g6")])):
            with self.subTest(root=changed_root, count=len(changed_cases)), self.assertRaises(seal.CustodyRejected):
                negative.seal_once(self.root, changed_cases, binding, self.recipient,
                                  self.budget, self.admission, case_root=changed_root)
            self.assertFalse((self.root / "graph-negative").exists())
        negative.seal_once(self.root, cases, binding, self.recipient, self.budget, self.admission, case_root=case_root)
        members, manifest, context = self.recover()
        self.assertEqual(members[case_root + "/g1/owner/owner-error.original.json"], current)
        self.assertFalse(any(name.startswith("e/graph-cases/") for name in members))
        self.assertFalse(any(b"HISTORICAL-UNKNOWN-DO-NOT-REUSE" in raw for raw in members.values()))
        self.assertEqual(old.read_bytes(), b"HISTORICAL-UNKNOWN-DO-NOT-REUSE")
        self.assertEqual(manifest["effects"], "UNKNOWN")
        self.assertEqual(manifest["quiescence"], "UNKNOWN")
        self.assertFalse(manifest["native_acceptance"] or manifest["formal_acceptance"])
        self.assertEqual(context, binding["context"])

    def test_source_authored_closure_jsons_are_retained_for_each_case_but_no_store_is_opened(self):
        self.put("e/receipt.json", b'{"status":"UNKNOWN","private_error":"first-cause"}')
        # Names come from the actual finalizeGraphCase call sites, not from the
        # collector's new lookup; every variant has different owner storage.
        names = {"graph-source-review": "closed-graph-store", "graph-flow": "closed-flow-store",
            "graph-lost-reply": "closed-graph-lost-reply-store", "graph-removal": "closed-graph-removal-store",
            "graph-label-causal-diagram": "closed-graph-label-store", "graph-label-system-flow": "closed-graph-label-store"}
        expected = {}
        for case, directory in self.cases:
            prefix = "e/graph-cases/" + directory + "/artifacts/" + names[case]
            for suffix in ("-service-closure.json", "-outcomes.json", "-outcomes-before-capture.json"):
                raw = seal.json_bytes({"case": case, "status": "UNKNOWN", "original": suffix})
                self.put(prefix + suffix, raw); expected[prefix + suffix] = raw
            self.put(prefix + "/motionwright.sqlite3", b"LIVE-STORE-DO-NOT-CAPTURE")
        self.run_seal()
        members, manifest, _ = self.recover()
        self.assertEqual({name: members.get(name) for name in expected}, expected)
        self.assertTrue(all((self.root / name).read_bytes() == raw for name, raw in expected.items()))
        self.assertFalse(any(".sqlite3" in name for name in members))
        self.assertIn(b"first-cause", members["e/receipt.json"])
        self.assertEqual((manifest["effects"], manifest["quiescence"]), ("UNKNOWN", "UNKNOWN"))

    def test_physical_shared_rejection_reports_closed_stage_without_bypassing_reservation(self):
        self.put("e/receipt.json", b"private-first-error")
        progress = {}
        try:
            negative.seal_once(self.root, self.cases, self.binding, self.recipient,
                {"maximum_bytes": negative.RESERVATION["bytes"], "maximum_members": 1232},
                self.admission, progress=progress)
        except seal.CustodyRejected as error:
            result = controller.graph_negative_failure_diagnostic(error, progress)
        else:
            self.fail("physical reservation denial was bypassed")
        self.assertEqual(result["stage"], "SHARED_BEFORE")
        self.assertEqual(result["code"], "negative_shared_reservation")
        self.assertEqual(result["error_type"], "CustodyRejected")
        self.assertEqual((result["effects"], result["quiescence"]), ("UNKNOWN", "UNKNOWN"))
        self.assertFalse((self.root / "graph-negative").exists())

    def test_private_exception_text_and_paths_never_enter_public_failure_diagnostic(self):
        secret = "capability-secret-credential=/private/path"
        class MustNotStringify(Exception):
            def __str__(self):
                raise AssertionError("private exception was stringified")
        for error in (seal.CustodyRejected(secret), OSError(secret), MustNotStringify(secret)):
            with self.subTest(kind=type(error).__name__):
                result = controller.graph_negative_failure_diagnostic(error, {"stage": secret})
                self.assertEqual(result["stage"], "UNCLASSIFIED")
                self.assertEqual(result["code"], "UNCLASSIFIED")
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn("private/path", json.dumps(result))

    def test_file_and_parent_aliases_are_never_followed_even_in_private_selection(self):
        self.put("e/receipt.json", b"first-error")
        secret = self.put("unselected/owner-error.original.json", b"FOREIGN_SECRET")
        case = self.root / "e/graph-cases/g1"; case.mkdir(parents=True)
        (case / "owner").symlink_to(secret.parent, target_is_directory=True)
        (case / "parent-close-error.original.json").symlink_to(secret)
        os.link(secret, case / "owner-parent-terminal.original.json")
        self.run_seal()
        members, manifest, _ = self.recover()
        self.assertFalse(any(b"FOREIGN_SECRET" in raw for raw in members.values()))
        for name in ("owner/owner-error.original.json", "parent-close-error.original.json", "owner-parent-terminal.original.json"):
            row = next(row for row in manifest["records"] if row.get("path") == "e/graph-cases/g1/" + name)
            self.assertEqual(row["status"], "NOT_CAPTURED")

    def test_oversized_original_is_retained_in_place_without_prefix_or_budget_increase(self):
        self.put("e/receipt.json", b"first-error")
        target = self.put("e/graph-cases/g1/owner/owner-error.original.json", b"x" * (negative.MAX_FILE + 1))
        before = negative.identity(target.stat())
        carrier = self.run_seal()
        members, manifest, _ = self.recover()
        self.assertNotIn("e/graph-cases/g1/owner/owner-error.original.json", members)
        self.assertEqual(negative.identity(target.stat()), before)
        self.assertLess(carrier["envelope"]["bytes"], negative.RESERVATION["bytes"])
        self.assertEqual(manifest["effects"], "UNKNOWN")

    def test_shared_capacity_and_wrong_recipient_deny_before_creating_attempt(self):
        self.put("e/receipt.json", b"first-error")
        with self.assertRaises(seal.CustodyRejected):
            self.run_seal(budget={"maximum_bytes": negative.RESERVATION["bytes"], "maximum_members": 1232})
        self.assertFalse((self.root / "graph-negative").exists())
        changed = dict(self.recipient, sha256="0" * 64)
        with self.assertRaises(seal.CustodyRejected):
            self.run_seal(recipient=changed)
        self.assertFalse((self.root / "graph-negative").exists())

    def test_interrupted_once_only_attempt_cannot_be_repeated_or_overwritten(self):
        self.put("e/receipt.json", b"first-error")
        with patch.object(negative, "collect", side_effect=OSError("read interruption")):
            with self.assertRaises(OSError):
                self.run_seal()
        intent = self.root / "graph-negative/attempt.original.json"
        before = intent.read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_seal()
        self.assertEqual(intent.read_bytes(), before)
        self.assertFalse((self.root / "graph-negative/negative-originals.mwseal").exists())

    def test_ciphertext_tampering_cannot_recover_or_change_the_bound_context(self):
        self.put("e/receipt.json", b"first-error")
        self.run_seal()
        envelope = (self.root / "graph-negative/negative-originals.mwseal").read_bytes()
        with self.assertRaises(Exception):
            seal.decrypt_snapshot(envelope[:-1] + bytes([envelope[-1] ^ 1]), self.key)

    def entrypoint_fixture(self, *, bad_digest=False, bad_namespace=False):
        # Only CI layout/authenticated deadline construction are fixture inputs;
        # the actual pinned loader, file reader, crypto, clock window and sink run.
        repo = self.root.parent / "g"
        (repo / "tooling").mkdir(parents=True)
        files = {}
        for name in ("ci_canvas_service", "mlt_official_runtime_intake", "ci_private_evidence", "native_graph_negative_evidence"):
            relative = "tooling/" + name + ".py"; raw = (ROOT / relative).read_bytes()
            (repo / relative).write_bytes(raw)
            files[relative] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if bad_digest:
            files["tooling/native_graph_negative_evidence.py"]["sha256"] = "0" * 64
        index = {"schema": controller.GRAPH_INDEX_SCHEMA,
            "source": {"sha": "a" * 40, "sdk_sha": "b" * 40, "run_id": "42", "run_attempt": 1, "repository_root": str(repo)},
            "contract": {"source_files": files, "execution": {"budget": controller.GRAPH_BUDGET,
                "cases_root": str(self.root / ("e/gc42-1" if bad_namespace else "e/graph-cases"))}}}
        index_path = self.put("e/graph-producer/producer-index.original.json", seal.json_bytes(index))
        index_ref = controller.graph_ref(index_path)
        self.put("e/receipt.json", seal.json_bytes({"status": "UNKNOWN", "formal_acceptance": False,
            "selection_plan": {"requested": controller.GRAPH_SCOPE}, "producer_index": index_ref, "run_id": "42", "run_attempt": "1"}))
        self.put("graph-review/carrier.original.json", seal.json_bytes({"index_sha256": index_ref["sha256"],
            "source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "42", "run_attempt": 1,
            "recipient_spki_sha256": self.recipient["sha256"]}))
        clock = controller.GraphOriginalClock.__new__(controller.GraphOriginalClock)
        clock.root = self.root; clock.last_ns = time.monotonic_ns(); clock.deadline_ns = clock.last_ns + 1200 * 10**9
        clock.unknown = clock.expired = False; clock.active_process = None; clock.admissions = []
        clock.phase_name = clock.phase_end = None
        output = self.root / "github-output"
        environment = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "42", "GITHUB_OUTPUT": str(output)}
        modules = {name: sys.modules.get(name) for name in ("mlt_official_runtime_intake", "ci_private_evidence", "native_graph_negative_evidence")}
        with patch.dict(sys.modules):
            # A fresh python -I invocation has no imported custody modules.
            # Retain the guard: copied source bytes must supply every module.
            for name in modules:
                sys.modules.pop(name, None)
            with patch.object(controller, "graph_layout", return_value=(repo, self.root)), \
                    patch.object(controller, "GraphOriginalClock", return_value=clock), patch.dict(os.environ, environment):
                result = controller.graph_negative_originals(repo, self.recipient)
            self.assertTrue(all(name not in sys.modules for name in modules))
        self.assertTrue(all(sys.modules.get(name) is old for name, old in modules.items()))
        return result, output

    def test_actual_controller_pinned_imports_and_upload_flag_preserve_unknown(self):
        result, output = self.entrypoint_fixture()
        self.assertEqual(result, 0)
        self.assertEqual(output.read_text(), "sealed=true\n")
        members, manifest, _ = self.recover()
        self.assertEqual(manifest["binding"]["receipt_sha256"], hashlib.sha256(members["e/receipt.json"]).hexdigest())
        self.assertEqual(manifest["binding"]["receipt_status"], "UNKNOWN")

    def test_changed_collector_source_denies_before_attempt_and_upload_flag(self):
        with self.assertRaises(ValueError):
            self.entrypoint_fixture(bad_digest=True)
        self.assertFalse((self.root / "graph-negative").exists())
        self.assertFalse((self.root / "github-output").exists())

    def test_actual_controller_rejects_case_namespace_before_attempt_and_upload_flag(self):
        with self.assertRaisesRegex(ValueError, "Graph original case namespace"):
            self.entrypoint_fixture(bad_namespace=True)
        self.assertFalse((self.root / "graph-negative").exists())
        self.assertFalse((self.root / "github-output").exists())

    def test_workflow_uploads_only_the_ciphertext_namespace_and_keeps_public_hold(self):
        workflow = ROOT / ".github/workflows/studio-service-verification.yml"
        text = workflow.read_text()
        negative_step = text.split("- name: Seal selected negative Graph originals", 1)[1].split("- name: Retain verified TAR", 1)[0]
        self.assertIn("steps.graph-cases.outcome == 'failure'", negative_step)
        self.assertIn("steps.graph-negative.outputs.sealed == 'true'", negative_step)
        self.assertIn("v/graph-negative/negative-originals.mwseal", negative_step)
        self.assertNotIn("github.token", negative_step)
        self.assertNotIn("path: v/e", negative_step)
        source = (ROOT / "tooling/ci_canvas_service.py").read_text()
        module = ast.parse(source)
        positive = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "graph_public_upload_closure")
        self.assertIn("CLOSED_GRAPH_OBSERVATIONS", ast.get_source_segment(source, positive))
        self.assertIn("original fixture or credential closure is UNKNOWN", ast.get_source_segment(source, positive))
        self.assertIn('"graph-negative-evidence-tests", 30, clock', source)


    def test_four_case_diagnostic_seals_only_current_four_and_preserves_two_unknowns(self):
        cases = [("graph-label-causal-diagram", "g2"), ("graph-label-system-flow", "g3"),
                 ("graph-lost-reply", "g4"), ("graph-removal", "g5")]
        case_root = "e/gc42-1"
        continuation = {"schema": "motionwright.graph-unstarted-continuation/2",
            "selector": controller.GRAPH_FOUR_CONTINUATION,
            "origin": controller.GRAPH_FOUR_CONTINUATION_ORIGIN,
            "prior_origins": [controller.GRAPH_CONTINUATION_ORIGIN],
            "selected_cases": [row[0] for row in controller.GRAPH_FOUR_CONTINUATION_CASES],
            "required_current_cases": 4, "historical_cleanup_claimed": False,
            "historical_owner_credentials_or_tickets_reused": False}
        binding = {**self.binding, "continuation": continuation}
        self.put("e/receipt.json", b'{"status":"UNKNOWN"}')
        current = b'{"status":"UNKNOWN","first_error":"current-four-private-error"}'
        self.put(case_root + "/g4/owner/owner-error.original.json", current)
        excluded = [self.put(case_root + "/" + name + "/owner/owner-error.original.json", b"EXCLUDED-UNKNOWN")
                    for name in ("g1", "g6")]
        old = self.put("e/gc42372165893-1/g1/owner/owner-error.original.json", b"HISTORICAL-FLOW-UNKNOWN")
        for changed_root, changed_cases in (("e/gc42372165893-1", cases),
                (case_root, [("graph-flow", "g1")] + cases), (case_root, cases + [("graph-source-review", "g6")])):
            with self.subTest(root=changed_root, count=len(changed_cases)), self.assertRaises(seal.CustodyRejected):
                negative.seal_once(self.root, changed_cases, binding, self.recipient,
                                  self.budget, self.admission, case_root=changed_root)
            self.assertFalse((self.root / "graph-negative").exists())
        negative.seal_once(self.root, cases, binding, self.recipient, self.budget, self.admission, case_root=case_root)
        members, manifest, context = self.recover()
        self.assertEqual(members[case_root + "/g4/owner/owner-error.original.json"], current)
        self.assertFalse(any(b"EXCLUDED-UNKNOWN" in raw or b"HISTORICAL-FLOW-UNKNOWN" in raw for raw in members.values()))
        self.assertEqual(old.read_bytes(), b"HISTORICAL-FLOW-UNKNOWN")
        self.assertTrue(all(path.read_bytes() == b"EXCLUDED-UNKNOWN" for path in excluded))
        self.assertEqual(manifest["binding"]["continuation"], continuation)
        self.assertEqual(manifest["effects"], "UNKNOWN")
        self.assertEqual(manifest["quiescence"], "UNKNOWN")
        self.assertFalse(manifest["native_acceptance"] or manifest["formal_acceptance"])
        self.assertEqual(context, binding["context"])


if __name__ == "__main__":
    unittest.main()
