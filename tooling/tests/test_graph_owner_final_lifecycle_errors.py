"""Exact Graph owner final source-only RED/GREEN; no CLI, Host or Native work.

The owner source is loaded as AST to exercise its actual finalizer and literal
schema, without importing effectful Graph runtime configuration.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "native_graph_browser_owner.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
SCHEMA = ast.literal_eval(next(
    item.value for item in TREE.body if isinstance(item, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "_FINAL_SCHEMA"
            for target in item.targets)
))
SOURCE_REF = {"path": "/tmp/retained-owner-original.json",
              "sha256": "a" * 64, "size_bytes": 1}
SOURCE_ID = {
    "repository_root": "/tmp/original-checkout", "sha": "a" * 40,
    "tree": "b" * 40, "sdk_root": "/tmp/source-sdk", "sdk_sha": "c" * 40,
    "locks": {name: "d" * 64 for name in
              ("Cargo.lock", "SOURCE_LOCK.json",
               "apps/studio/package-lock.json", "rust-toolchain.toml")},
    "run_id": "42466202899", "run_attempt": 1,
}


class GraphOwnerFailure(ValueError):
    pass


def need(condition, label):
    if not condition:
        raise GraphOwnerFailure(label)


def source_namespace():
    namespace = {
        "GraphOwnerFailure": GraphOwnerFailure, "require": need,
        "re": re, "os": os, "Path": Path,
        "_FINAL_SCHEMA": SCHEMA,
        "_OWNER_SPEC": {"case_id": "graph-flow", "source": SOURCE_ID},
    }
    selected = [item for item in TREE.body if isinstance(item, ast.FunctionDef)
                and item.name in ("validate_literal_schema",
                                  "graph_native_final_lifecycle_receipt",
                                  "graph_final_cohort_signal_free",
                                  "graph_close_v6")]
    if len(selected) != 4:
        raise AssertionError("Original Graph finalizer functions changed")
    exec(compile(ast.Module(body=selected, type_ignores=[]),
                 str(SOURCE), "exec"), namespace)
    return namespace


def receipt(errors):
    return {
        "schema": "motionwright.graph-browser-owner-final/6",
        "case_id": "graph-flow", "status": "UNKNOWN",
        "source": SOURCE_ID, "spec_original": SOURCE_REF,
        "source_inputs": SOURCE_REF, "client_roster": [],
        "service_closure": SOURCE_REF, "service_closure_validated": True,
        "owner_close": SOURCE_REF,
        "database_initial": None, "database_final": SOURCE_REF,
        "same_physical_database": True, "owner_cohort_absent": True,
        "owner_streams_eof": True, "capture_permitted": False,
        "errors": errors, "native_authoring_approved": False,
        "formal_acceptance": False,
    }


class LeaseLedger:
    def __init__(self):
        self.failed = False
        self.sealed = False

    def seal(self):
        self.sealed = True


class GraphOriginalFinalLifecycleTests(unittest.TestCase):
    def test_literal_schema_admits_its_actual_missing_release_error(self):
        selected = source_namespace()
        selected["validate_literal_schema"](
            receipt(["PROVIDER_RELEASE_ORIGINAL_MISSING"]), SCHEMA
        )
        with self.assertRaisesRegex(GraphOwnerFailure, "literal-schema-enum"):
            selected["validate_literal_schema"](
                receipt(["UNKNOWN_EXTERNAL_GRAPH_CLOSE_OK"]), SCHEMA
            )

    def exercise_original_final(self, *, close_failure):
        selected = source_namespace()
        output, closed = {}, []
        quota = SimpleNamespace(closing=False, failed={})
        ledger = LeaseLedger()
        evidence = Path("/tmp/mw-a4-graph-original-source-only")
        def close(*, reason):
            closed.append(reason)
            if close_failure:
                raise RuntimeError("inert-close-failure-no-real-process")
            return {"status": "CLOSED"}
        owner = SimpleNamespace(
            closed=False, native_quiescence=False, client_ledger=ledger,
            lifecycle=SimpleNamespace(
                closed=False, close=close, evidence=evidence, children=[]),
            database=Path("/tmp/motionwright.sqlite3"),
            database_initial={"device": 123, "inode": 456},
            database_initial_original=None, clients=[],
            spec_original=SOURCE_REF, source_inputs={"sources": {}, "binaries": {}},
        )
        selected.update(
            require_subquota=lambda: quota,
            literal_service_barrier=lambda *_: SOURCE_REF,
            observe_source_before_close=lambda *_: None,
            close_complete_cohort=lambda: ({
                "status": "PASS", "authority": "process_custody_only_not_revocation",
                "subreaper": True, "signals": []
            }, True),
            file_identity=lambda *_: {
                "device": 123, "inode": 456, "path": "/tmp/motionwright.sqlite3",
                "sha256": "f" * 64, "size_bytes": 1
            },
            new_json=lambda path, value: output.setdefault(path.name, value),
            original_ref=lambda *_: SOURCE_REF,
        )
        result = selected["graph_close_v6"](
            owner, service_barrier=SOURCE_REF,
            request_id="graph-flow-original", reason="explicit-close"
        )
        self.assertEqual(closed, ["explicit-close"])
        self.assertTrue(ledger.sealed)
        self.assertTrue(owner.closed)
        self.assertTrue(quota.closing)
        self.assertEqual(result["event"], "FINAL_RECEIPT")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["capture_permitted"])
        final = output["owner-final.original.json"]
        self.assertEqual(final["status"], "UNKNOWN")
        self.assertFalse(final["native_authoring_approved"])
        self.assertFalse(final["formal_acceptance"])
        self.assertFalse(final["capture_permitted"])
        return final, output

    def test_missing_quiescence_clean_owner_close_stays_unknown(self):
        final, originals = self.exercise_original_final(close_failure=False)
        self.assertEqual(final["errors"], ["PROVIDER_RELEASE_ORIGINAL_MISSING"])
        self.assertEqual(originals["owner-complete-close.original.json"]["status"],
                         "UNKNOWN")

    def test_missing_quiescence_failed_owner_close_keeps_both_causes(self):
        final, originals = self.exercise_original_final(close_failure=True)
        self.assertEqual(final["errors"], [
            "OWNER_CLOSE_FAILED", "PROVIDER_RELEASE_ORIGINAL_MISSING"
        ])
        self.assertIsNone(originals["owner-complete-close.original.json"]["lifecycle"])


if __name__ == "__main__":
    unittest.main()
