"""Synthetic capture and retry controls; no Broker, renderer, Git or product.

Run by file with python -B -I -S. The test fixture supplies a labelled context;
that context is never an assertion that GitHub or a native producer executed.
"""
import importlib.util
from pathlib import Path
import json
import tempfile
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "native_manim_camera120_e2e.py"
spec = importlib.util.spec_from_file_location("camera120_proposed_safety_subject", SOURCE)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class Camera120SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="camera120-synthetic-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.owner = self.root / "owner"
        self.evidence.mkdir()
        (self.owner / "runtime").mkdir(parents=True)
        self.token = b"synthetic-credential-ONLY-for-negative-control-000000"
        (self.owner / "runtime/motionwright.session").write_bytes(self.token)
        self.original = self.evidence / "observed.log"
        self.good = b"bounded synthetic public observation\n"
        self.original.write_bytes(self.good)
        self.args = types.SimpleNamespace(evidence=self.evidence, owner_work=self.owner,
                                          output=self.root / "publish")
        self.context = mock.patch.object(subject, "context", return_value={
            "classification": "SYNTHETIC_UNIT_CONTEXT_NOT_GITHUB", "GITHUB_RUN_ATTEMPT": "1"})
        self.context.start()
        self.addCleanup(self.context.stop)

    def original_and_public(self):
        return self.original.read_bytes(), self.args.output / "originals/observed.log"

    def assert_no_unreviewed_bytes_published(self):
        _, public = self.original_and_public()
        self.assertFalse(public.exists())
        report = json.loads((self.args.output / "artifact-custody.json").read_bytes())
        self.assertFalse(report["complete_for_technical_directory"])
        self.assertEqual(report["originals"], [])
        self.assertEqual(len(report["exclusions"]), 1)
        for path in self.args.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.token, path.read_bytes())
        self.assertEqual(self.original.read_bytes(), self.good)

    def test_positive_original_copy_is_scanned_and_published_with_exact_hashes(self):
        self.assertEqual(subject.finalize(self.args), 0)
        original, public = self.original_and_public()
        self.assertEqual(public.read_bytes(), original)
        report = json.loads((self.args.output / "artifact-custody.json").read_bytes())
        self.assertTrue(report["complete_for_technical_directory"])
        self.assertEqual(report["exclusions"], [])
        row = report["originals"][0]
        self.assertEqual(row["original"]["sha256"], row["private_scanned_copy"]["sha256"])
        self.assertEqual(row["retained"]["sha256"], row["private_scanned_copy"]["sha256"])
        self.assertFalse(report["formal_acceptance"])
        self.assertEqual(report["product_verdict"], "NOT_GRANTED_BY_STAGING")
        self.assertNotIn("owner", {p.name for p in self.args.output.iterdir()})

    def mutate_private_after_scan(self, grow):
        original_copy = subject.copy_original
        public_copy_calls = []
        def race(source, destination, maximum):
            if "publish-quarantine" in source.parts:
                public_copy_calls.append(str(source))
                if grow:
                    with source.open("ab") as stream:
                        stream.write(self.token)
                else:
                    source.write_bytes(b"X" * source.stat().st_size)
            return original_copy(source, destination, maximum)
        with mock.patch.object(subject, "copy_original", side_effect=race):
            self.assertEqual(subject.finalize(self.args), 1)
        self.assertEqual(len(public_copy_calls), 1)
        self.assert_no_unreviewed_bytes_published()
        report = json.loads((self.args.output / "artifact-custody.json").read_bytes())
        self.assertEqual(report["exclusions"][0]["reason"], "scanned_candidate_changed_before_publication")

    def test_same_size_mutation_between_scan_and_publication_fails_without_public_bytes(self):
        self.mutate_private_after_scan(grow=False)

    def test_growth_between_scan_and_publication_fails_without_public_ticket_bytes(self):
        self.mutate_private_after_scan(grow=True)

    def test_original_ticket_material_is_retained_privately_and_never_published(self):
        self.original.write_bytes(self.token)
        self.assertEqual(subject.finalize(self.args), 1)
        public = self.args.output / "originals/observed.log"
        self.assertFalse(public.exists())
        self.assertEqual(self.original.read_bytes(), self.token)
        private = self.root / "publish-quarantine/observed.log"
        self.assertEqual(private.read_bytes(), self.token)
        report = json.loads((self.args.output / "artifact-custody.json").read_bytes())
        self.assertFalse(report["complete_for_technical_directory"])
        self.assertEqual(report["exclusions"][0]["reason"], "original_ticket_material_detected_no_redaction")
        self.assertNotIn(self.token, (self.args.output / "artifact-custody.json").read_bytes())

    def test_rerun_is_rejected_before_source_queries_filesystem_or_processes(self):
        with mock.patch.object(subject, "context", return_value={"GITHUB_RUN_ATTEMPT": "2"}):
            with mock.patch.object(subject, "source_identity") as source, \
                 mock.patch.object(subject, "mkdir") as mkdir, \
                 mock.patch.object(subject, "Capture") as process, \
                 mock.patch.object(subject, "command") as command:
                with self.assertRaisesRegex(subject.Refused, "native reruns are forbidden"):
                    subject.run(types.SimpleNamespace())
                source.assert_not_called()
                mkdir.assert_not_called()
                process.assert_not_called()
                command.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
