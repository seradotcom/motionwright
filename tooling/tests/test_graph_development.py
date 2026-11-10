"""Inert report/selector boundaries; no Host, fake Ready or native execution."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("graph_development_under_test", ROOT / "tooling/graph_development.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


def report(*, listing=False):
    return {
        "errors": [],
        "config": {"workers": 1, "forbidOnly": True, "maxFailures": 1,
                   "projects": [{"id": "chromium", "name": "chromium", "repeatEach": 1, "retries": 0}]},
        "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
        "suites": [{"suites": [], "specs": [{
            "title": development.TITLE, "file": "tooling/graph_dev_first_save.spec.ts", "ok": True,
            "tests": [{"projectId": "chromium", "status": "expected", "expectedStatus": "passed",
                       "results": [] if listing else [{"status": "passed", "retry": 0, "errors": []}]}],
        }]}],
    }


class DevelopmentOriginalReportTests(unittest.TestCase):
    def test_discovery_and_success_are_distinct_and_neither_grants_formal_credit(self):
        self.assertEqual(development.selected_report(report(listing=True), listing=True),
                         {"tests": 1, "retries": 0, "listing_only": True, "formal_acceptance": False})
        self.assertFalse(development.selected_report(report(), listing=False)["formal_acceptance"])

    def test_discovery_cannot_be_reported_as_save(self):
        with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
            development.selected_report(report(listing=True), listing=False)

    def test_failure_unknown_retry_and_skipped_cannot_be_success(self):
        for status in ("failed", "timedOut", "interrupted", "skipped"):
            with self.subTest(status=status):
                value = report()
                value["suites"][0]["specs"][0]["tests"][0]["results"][0]["status"] = status
                with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
                    development.selected_report(value, listing=False)
        value = report()
        value["suites"][0]["specs"][0]["tests"][0]["results"][0]["retry"] = 1
        with self.assertRaisesRegex(ValueError, "DEV_REPORT_NOT_ONE_ACKNOWLEDGED_PASS"):
            development.selected_report(value, listing=False)

    def test_foreign_case_duplicate_result_and_report_error_are_rejected(self):
        for kind in ("foreign", "duplicate", "error"):
            value = report()
            spec = value["suites"][0]["specs"][0]
            if kind == "foreign":
                spec["file"] = "semantic-graph-service.spec.ts"
            elif kind == "duplicate":
                spec["tests"][0]["results"].append(copy.deepcopy(spec["tests"][0]["results"][0]))
            else:
                value["errors"] = [{"message": "original process not closed"}]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                development.selected_report(value, listing=False)

    def test_parallel_or_retry_config_denied(self):
        for property_name, value in (("workers", 2), ("forbidOnly", False), ("maxFailures", 2)):
            current = report()
            current["config"][property_name] = value
            with self.subTest(property_name=property_name), self.assertRaisesRegex(ValueError, "EXECUTION_POLICY"):
                development.selected_report(current, listing=False)
        current = report()
        current["config"]["projects"][0]["retries"] = 1
        with self.assertRaisesRegex(ValueError, "EXECUTION_POLICY"):
            development.selected_report(current, listing=False)


class RuntimeAuthorityCustodyTests(unittest.TestCase):
    """Exercise the actual production consumer with inert decoded records only."""
    @staticmethod
    def admitted(development_mode):
        source = {"sha": "a" * 40, "sdk_sha": "b" * 40, "run_id": "1234", "run_attempt": 1}
        authority = {"source_sha": source["sha"], "run_id": source["run_id"], "run_attempt": 1,
                     "index_sha256": "c" * 64, "actor_id": 17}
        if development_mode:
            authority.update(kind="EXPLICIT_DEVELOPMENT_ORIGINAL",
                context="mw-development/1234/1/graph-flow", request_sha256="d" * 64,
                formal_acceptance=False, automatic_retry=False)
        else:
            authority.update(status_id=29, context="mw-owner/phase1-native-graph/1234/1/graph-flow")
        return {"development": development_mode, "approval": authority,
                "index_ref": {"sha256": "c" * 64},
                "index": {"schema": development.SCHEMA if development_mode else
                          "motionwright.private.graph-producer-reference-index/0", "source": source}}

    def test_runtime_consumer_preserves_formal_status_and_has_no_status_for_development(self):
        from tooling import native_graph_browser_owner as owner
        self.assertEqual(owner.graph_runtime_authority_custody(self.admitted(False)),
                         {"independent_status_id": 29})
        value = self.admitted(True)
        actual = owner.graph_runtime_authority_custody(value)
        self.assertEqual(actual, {"independent_development_authorization": value["approval"]})
        self.assertNotIn("independent_status_id", actual)
        self.assertFalse(actual["independent_development_authorization"]["formal_acceptance"])

    def test_runtime_consumer_rejects_mixed_scope_fabricated_status_and_missing_formal_status(self):
        from tooling import native_graph_browser_owner as owner
        for change in ("schema", "status", "approval", "retry", "kind", "absent-scope"):
            value = self.admitted(True)
            if change == "schema":
                value["index"]["schema"] = "motionwright.private.graph-producer-reference-index/0"
            elif change == "status":
                value["approval"]["status_id"] = 29
            elif change == "approval":
                value["approval"]["formal_acceptance"] = True
            elif change == "retry":
                value["approval"]["automatic_retry"] = True
            elif change == "kind":
                value["approval"]["kind"] = "CALLER_APPROVED"
            else:
                del value["development"]
            with self.subTest(change=change), self.assertRaises(owner.GraphOwnerFailure):
                owner.graph_runtime_authority_custody(value)
        value = self.admitted(False)
        del value["approval"]["status_id"]
        with self.assertRaisesRegex(owner.GraphOwnerFailure, "formal-status-required"):
            owner.graph_runtime_authority_custody(value)

    def test_runtime_consumer_keeps_exact_source_run_and_index(self):
        from tooling import native_graph_browser_owner as owner
        for key, replacement in (("source_sha", "f" * 40), ("run_id", "9999"),
                                 ("run_attempt", 2), ("index_sha256", "e" * 64)):
            value = self.admitted(True)
            value["approval"][key] = replacement
            with self.subTest(key=key), self.assertRaisesRegex(owner.GraphOwnerFailure, "source-mismatch"):
                owner.graph_runtime_authority_custody(value)


class DevelopmentFailureCustodyTests(unittest.TestCase):
    """No key generation, encryption, Host, process launch or GitHub request."""
    @staticmethod
    def inputs():
        recipient = {"spki_der_base64": "INERT_PUBLIC_SPKI_REFERENCE", "sha256": "c" * 64}
        run = {"id": 1234, "run_attempt": 1, "head_sha": "a" * 40, "head_branch": "integration",
               "repository": {"full_name": "seradotcom/motionwright"},
               "head_repository": {"full_name": "seradotcom/motionwright"},
               "event": "workflow_dispatch", "actor": {"type": "User", "id": 17},
               "path": ".github/workflows/studio-service-verification.yml"}
        event = {"repository": run["repository"].copy(), "sender": run["actor"].copy(),
                 "ref": "refs/heads/integration", "inputs": {
                     "selection": "phase1-native-graph", "graph_case_mode": "development-first-save",
                     "graph_review_spki_der_base64": recipient["spki_der_base64"],
                     "graph_review_spki_sha256": recipient["sha256"]}}
        binding = {"GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "1234", "GITHUB_RUN_ATTEMPT": "1"}
        return event, run, binding, recipient

    def test_sealer_context_uses_exact_dispatch_recipient_without_claiming_a_producer(self):
        values = self.inputs()
        actual = development.sealing_context(*values, "b" * 40)
        self.assertEqual(actual, {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                                  "run_id": "1234", "run_attempt": "1"})
        self.assertNotIn("producer_index", actual)
        self.assertNotIn("status_id", actual)

    def test_sealer_rejects_foreign_recipient_actor_selection_source_and_replay(self):
        for change in ("key", "digest", "sender", "selection", "mode", "ref", "source", "attempt"):
            event, run, binding, recipient = self.inputs()
            if change == "key":
                recipient["spki_der_base64"] = "DIFFERENT_INERT_PUBLIC_KEY"
            elif change == "digest":
                recipient["sha256"] = "d" * 64
            elif change == "sender":
                event["sender"]["id"] = 18
            elif change == "selection":
                event["inputs"]["selection"] = "phase1-native-gn1"
            elif change == "mode":
                event["inputs"]["graph_case_mode"] = "all"
            elif change == "ref":
                event["ref"] = "other"
            elif change == "source":
                binding["GITHUB_SHA"] = "e" * 40
            else:
                binding["GITHUB_RUN_ATTEMPT"] = "2"
            with self.subTest(change=change), self.assertRaises(ValueError):
                development.sealing_context(event, run, binding, recipient, "b" * 40)

    def test_failed_producer_or_missing_carrier_upload_keeps_the_entire_original_e_tree(self):
        root = ROOT / "inert-private-root"
        for complete, retained in ((False, False), (False, True), (True, False)):
            with self.subTest(complete=complete, retained=retained):
                self.assertEqual(development.sealing_roots(root, producer_complete=complete,
                    carrier_retained=retained), {"owner": root / "e", "clock": root / "tools"})
        actual = development.sealing_roots(root, producer_complete=True, carrier_retained=True)
        self.assertEqual(actual["owner"], root / "e/graph-cases")
        self.assertEqual(actual["controller"], root / "e/graph-development-controller")

    def test_partial_capture_and_failed_producer_never_upgrade_a_claimed_save(self):
        source = {"sha": "a" * 40}
        receipt = {"source": source, "status": "CLOSED_GRAPH_DEVELOPMENT_OBSERVATIONS",
                   "formal_acceptance": False, "R74_reviewed": False,
                   "automatic_retry": False, "Graph6_cases_accepted": 0}
        for complete, captured in ((False, True), (True, False), (False, False)):
            self.assertEqual(development.sealing_status(producer_complete=complete,
                capture_complete=captured, receipt=receipt, expected_source=source), "UNKNOWN")
        self.assertEqual(development.sealing_status(producer_complete=True,
            capture_complete=True, receipt=receipt, expected_source=source), receipt["status"])
        for key, value in (("formal_acceptance", True), ("R74_reviewed", True),
                           ("source", {"sha": "e" * 40}), ("automatic_retry", True),
                           ("status", "UNREVIEWED_PASS")):
            changed = dict(receipt, **{key: value})
            with self.subTest(key=key):
                self.assertEqual(development.sealing_status(producer_complete=True,
                    capture_complete=True, receipt=changed, expected_source=source), "UNKNOWN")
        receipt["status"] = "FAIL"
        self.assertEqual(development.sealing_status(producer_complete=True,
            capture_complete=True, receipt=receipt, expected_source=source), "FAIL")


class IndependentOriginalJoinTests(unittest.TestCase):
    """Pure negative controls; never starts Host or manufactures acceptance."""
    @staticmethod
    def records():
        source = {"sha": "a" * 40, "run_id": "123", "run_attempt": 1}
        db = "/private/g3/store/motionwright.sqlite3"
        physical = {"path": db, "device": "24", "inode": "171"}
        asset = {"id": "asset-original", "name": "Original imported SVG",
                 "media_type": "image/svg+xml", "content_sha256": "f" * 64,
                 "source_revision": None}
        project = {"id": "project-original", "generation": "gen-original",
                   "revision": 1, "assets": [asset],
                   "production_design": {"semantic_graphs": []}}
        event = {"revision": 1, "change": {"type": "upsert_semantic_graph"}}
        oracle = {"schemaVersion": 1, "project": project, "journal": [event],
                  "journalAfterRevisionExclusive": 0, "journalComplete": True}
        reader = {"schema": "motionwright.graph-dev-live-reader-open/1",
                  "source": source, "process_id": 124, "process": {},
                  "opened_before": {}, "opened_after": {},
                  "physical_store": physical, "held_sqlite_pager_witness": {},
                  "writer_oracle": {}, "full_project": project, "journal": [event],
                  "asset_integrity_page": {
                      "project_id": project["id"], "generation": project["generation"],
                      "revision": 1, "total_assets": 1, "items": [
                          {"asset_id": asset["id"], "status": "verified", "size_bytes": 137}],
                      "next": None, "complete": True, "checked_bytes": 137},
                  "journal_after_revision_exclusive": 0, "journal_complete": True,
                  "native_callbacks_allowed": False, "automatic_retry": False,
                  "returned_live": True, "formal_release_acceptance": False}
        return reader, oracle, physical, source, db

    def test_full_history_and_exact_original_inode_join_without_formal_credit(self):
        args = self.records()
        self.assertEqual(development.checked_independent_reader(*args), {
            "project_id": "project-original", "revision": 1,
            "full_journal_events": 1, "independent_read_only": True,
            "independent_cas_asset_bytes_hashed": True,
            "formal_acceptance": False})

    def test_reader_rejects_forged_original_scope_missing_history_and_replaced_inode(self):
        for mutation in ("scope", "replay", "extra", "journal", "revision",
                         "store", "source", "reader-writable", "cas-corrupt",
                         "cas-partial-hash", "cas-missing", "cas-foreign-asset"):
            args = list(copy.deepcopy(self.records()))
            if mutation == "scope":
                args[0]["formal_release_acceptance"] = True
            elif mutation == "replay":
                args[0]["automatic_retry"] = True
            elif mutation == "extra":
                args[0]["unreviewed_field"] = "invented"
            elif mutation == "journal":
                args[0]["journal"][0]["revision"] = 2
            elif mutation == "revision":
                args[0]["full_project"]["revision"] = 2
            elif mutation == "store":
                args[0]["physical_store"] = dict(args[2], inode="999")
            elif mutation == "source":
                args[0]["source"] = {"sha": "b" * 40}
            elif mutation == "cas-corrupt":
                args[0]["asset_integrity_page"]["items"][0]["status"] = "corrupt"
            elif mutation == "cas-partial-hash":
                args[0]["asset_integrity_page"]["checked_bytes"] = 0
            elif mutation == "cas-missing":
                args[0]["asset_integrity_page"]["items"] = []
            elif mutation == "cas-foreign-asset":
                args[0]["asset_integrity_page"]["items"][0]["asset_id"] = "not-in-project"
            else:
                args[0]["native_callbacks_allowed"] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                development.checked_independent_reader(*args)


if __name__ == "__main__":
    unittest.main()
