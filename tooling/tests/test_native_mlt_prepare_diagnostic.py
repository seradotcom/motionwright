"""No-secret, fail-closed preparation error telemetry; no physical Native effect."""
import inspect
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import native_mlt_g2_custody as custody


class PrepareFailureDiagnostics(unittest.TestCase):
    def setUp(self):
        self.old_phase = custody._PREPARE_PHASE
        custody._mark_prepare_phase("before_context")

    def tearDown(self):
        custody._mark_prepare_phase(self.old_phase)

    def test_public_failure_code_is_a_source_literal_only(self):
        custody._mark_prepare_phase("runtime_artifact")
        result = custody._safe_failure_diagnostic(
            custody.CustodyRejected("artifact_API_failed_no_retry"), "prepare")
        self.assertEqual(result, {
            "phase": "runtime_artifact", "error_class": "CustodyRejected",
            "error_code": "artifact_API_failed_no_retry",
            "automatic_retry": False, "originals_disclosed": False})

    def test_private_exception_payload_is_never_printable(self):
        custody._mark_prepare_phase("compiler_source_reconstruction")
        private = "SECRET-CANARY E:\\Motionwright-private\\gn1-recovery-private.pkcs8.pem"
        cases = [custody.CustodyRejected(private), ValueError(private),
                 KeyError(private), RuntimeError(private), Exception(private)]
        for error in cases:
            with self.subTest(kind=type(error).__name__):
                obj = custody._safe_failure_diagnostic(error, "prepare")
                rendered = json.dumps(obj, sort_keys=True)
                self.assertNotIn("SECRET-CANARY", rendered)
                self.assertNotIn("pkcs8", rendered)
                self.assertEqual(obj["phase"], "compiler_source_reconstruction")
                self.assertEqual(obj["error_code"], "WITHHELD")
                self.assertFalse(obj["automatic_retry"])
                self.assertFalse(obj["originals_disclosed"])

    def test_other_actions_never_borrow_prepare_phase(self):
        custody._mark_prepare_phase("compiler_artifact")
        for action in (None, "guard", "handoff", "inputs", "begin", "exit", "seal"):
            with self.subTest(action=action):
                result = custody._safe_failure_diagnostic(
                    custody.CustodyRejected("source_HEAD_changed"), action)
                self.assertEqual(result["phase"], "not_applicable")
                self.assertEqual(result["error_code"], "source_HEAD_changed")

    def test_arbitrary_types_and_phase_names_fail_closed(self):
        custody._mark_prepare_phase("original_source_guard")
        before = custody._PREPARE_PHASE
        with self.assertRaises(custody.CustodyRejected):
            custody._mark_prepare_phase("SECRET-CANARY")
        self.assertEqual(custody._PREPARE_PHASE, before)
        exc_type = type("SECRET-CANARY", (Exception,), {})
        result = custody._safe_failure_diagnostic(exc_type("private"), "prepare")
        self.assertEqual(result["error_class"], "OTHER")
        self.assertNotIn("SECRET-CANARY", json.dumps(result))

    def test_prepare_markers_cover_the_producer_preflight_and_intake(self):
        source = inspect.getsource(custody.prepare)
        expected = [
            '_mark_prepare_phase("before_context")',
            '_mark_prepare_phase("external_dispatch")',
            '_mark_prepare_phase("original_scope")',
            '_mark_prepare_phase("original_source_guard")',
            '_mark_prepare_phase("runtime_artifact")',
            '_mark_prepare_phase("runtime_receipt")',
            '_mark_prepare_phase("compiler_artifact")',
            '_mark_prepare_phase("preparation_finished")',
        ]
        last = -1
        for marker in expected:
            found = source.find(marker)
            self.assertGreater(found, last, marker)
            last = found
        compiler_source = inspect.getsource(custody.intake_compiler)
        self.assertIn('_mark_prepare_phase("compiler_source_reconstruction")',
                      compiler_source)
        self.assertIn('_mark_prepare_phase("compiler_source_closure")',
                      compiler_source)

    def test_reporter_retains_exit_two_and_safe_projection_only(self):
        main_guard = inspect.getsource(custody)
        reporter = main_guard.rsplit('if __name__ == "__main__":', 1)[1]
        self.assertIn('_safe_failure_diagnostic(error, action)', reporter)
        self.assertIn('raise SystemExit(2)', reporter)
        self.assertNotIn('str(error)', reporter)
        self.assertNotIn('traceback.print_exc', reporter)


if __name__ == "__main__":
    unittest.main()
