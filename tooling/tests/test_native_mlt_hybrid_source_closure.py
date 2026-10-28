"""Hybrid source admission against real temporary Git objects and file bytes.

No build, native executable, renderer, service, network or G2 acceptance runs.
These tests exercise the production Core verifier, not a mock hash verifier.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_evidence_projection as core  # noqa: E402
import native_mlt_g2_observation as g2  # noqa: E402


CRITICAL_CURRENT_PRODUCER = frozenset({
    "crates/native/examples/native-mlt-sequence-e2e.rs",
    "crates/native/examples/native_mlt_sequence_hybrid/mod.rs",
    "crates/native/src/production.rs",
    "crates/native/src/production/hybrid_budget.rs",
    "crates/native/src/production/hybrid_mlt.rs",
    "crates/native/src/manim_static_projection.rs",
    "crates/storage/src/root_operation_budget.rs",
    "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
    "crates/native/Cargo.toml",
})


@unittest.skipUnless(sys.platform == "linux", "The original anchored Core reader requires Linux")
class HybridSourceClosureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hybrid-source-closure-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.originals = {}
        # This fixture contains explicitly synthetic text; it is never built,
        # imported as code, or presented as a real Motionwright source receipt.
        for name in sorted(g2.HYBRID_SOURCE_PATHS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = ("synthetic source-binding unit fixture: " + name + "\n").encode()
            path.write_bytes(content)
            self.originals[name] = content
        self.git("init", "-q")
        self.git("add", "--all")
        self.git("-c", "user.name=Hybrid source test", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--no-verify", "-qm", "source closure fixture")
        self.source_sha = self.git("rev-parse", "HEAD").decode().strip()
        self.bindings = [{"path": name, "sha256": hashlib.sha256(raw).hexdigest()}
                         for name, raw in sorted(self.originals.items())]

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                                 "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15, check=False)
        self.assertEqual(result.returncode, 0, "Temporary source-fixture Git operation failed")
        return result.stdout

    def verify(self, bindings=...):
        return g2.verify_hybrid_source_closure(self.root, self.source_sha,
                                             self.bindings if bindings is ... else bindings)

    def test_actual_current_rust_and_cargo_inputs_are_required(self):
        self.assertTrue(CRITICAL_CURRENT_PRODUCER <= g2.HYBRID_SOURCE_PATHS)
        observed = self.verify()
        self.assertEqual(observed["source_sha"], self.source_sha)
        self.assertEqual(observed["source_bindings"], self.bindings)
        self.assertIs(observed["source_bytes_bound"], True)
        self.assertEqual(observed["native_effects"], 0)

    def test_omitting_any_required_member_fails_before_the_core_reads(self):
        for missing in sorted(g2.HYBRID_SOURCE_PATHS):
            with self.subTest(missing=missing):
                rows = [row for row in self.bindings if row["path"] != missing]
                with patch.object(core, "verify_source_bindings",
                                  side_effect=AssertionError("Core must not read an incomplete roster")) as reader:
                    with self.assertRaisesRegex(g2.G2Unverified, "hybrid_source_closure_incomplete"):
                        self.verify(rows)
                    reader.assert_not_called()

    def test_changed_working_file_is_rejected_even_with_a_clean_commit_identity(self):
        for changed in sorted(CRITICAL_CURRENT_PRODUCER):
            with self.subTest(changed=changed):
                path = self.root / changed
                path.write_bytes(self.originals[changed] + b"different physical source\n")
                try:
                    self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), self.source_sha)
                    with self.assertRaisesRegex(core.SelectionError, "physical-source-differs-from-git"):
                        self.verify()
                finally:
                    path.write_bytes(self.originals[changed])

    def test_repinning_changed_uncommitted_bytes_does_not_make_them_current_source(self):
        name = "crates/native/src/production/hybrid_budget.rs"
        changed = b"changed uncommitted budget source; no build or native effects\n"
        (self.root / name).write_bytes(changed)
        rows = copy.deepcopy(self.bindings)
        next(row for row in rows if row["path"] == name)["sha256"] = hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(core.SelectionError, "source-git-binding"):
            self.verify(rows)

    def test_missing_actual_rust_source_cannot_be_replaced_by_its_declared_hash(self):
        path = self.root / "crates/native/examples/native_mlt_sequence_hybrid/mod.rs"
        path.unlink()
        with self.assertRaises((OSError, core.SelectionError)):
            self.verify()

    def test_duplicate_source_and_malformed_path_do_not_weaken_core_admission(self):
        with self.assertRaisesRegex(core.SelectionError, "source-preflight-path"):
            self.verify(self.bindings + [dict(self.bindings[0])])
        for bad in (None, {}, [None], [{"path": ["Cargo.lock"], "sha256": "0" * 64}]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(g2.G2Unverified, "hybrid_source_closure_incomplete"):
                    self.verify(bad)

    def test_original_mismatched_git_object_is_rejected(self):
        old = self.source_sha
        name = "crates/native/src/production/hybrid_mlt.rs"
        (self.root / name).write_bytes(b"new committed fixture source\n")
        self.git("add", name)
        self.git("-c", "user.name=Hybrid source test", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--no-verify", "-qm", "changed fixture")
        self.assertNotEqual(self.git("rev-parse", "HEAD").decode().strip(), old)
        # The old source SHA and old pin still cannot excuse new physical bytes.
        with self.assertRaisesRegex(core.SelectionError, "physical-source-differs-from-git"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
