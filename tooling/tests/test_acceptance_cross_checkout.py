#!/usr/bin/env python3
"""Synthetic v0.1 privacy regressions for another Motionwright Git worktree.

No owner/private scenario evidence or human PASS claim is created here.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "acceptance_session.py"
SPEC = importlib.util.spec_from_file_location("acceptance_session", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class CrossCheckoutPrivateEvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mw-v01-cross-worktree-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.other_checkout = self.root / "other-worktree"
        self.other_checkout.mkdir()
        self.evidence_root = self.other_checkout / "private" / "evidence"
        (self.evidence_root / "ACC-001").mkdir(parents=True)
        self.file = self.evidence_root / "ACC-001" / "example.bin"
        self.file.write_bytes(b"synthetic evidence only")
        self.manifest = self.other_checkout / "private" / "session.json"
        self.manifest.write_text(
            json.dumps(mod.create_session(source_sha="b" * 40)), encoding="utf-8",
        )

    def linked_to_motionwright(self):
        # Simulate the exact roots returned by git worktree list. The test
        # does not mutate the current Git repository or linked checkouts.
        return patch.object(
            mod, "_motionwright_worktree_roots",
            return_value=(mod.ROOT.resolve(), self.other_checkout.resolve()),
        )

    def test_worktree_source_enumeration_includes_current_checkout(self):
        roots = mod._motionwright_worktree_roots()
        self.assertTrue(roots)
        self.assertTrue(any(mod.ROOT.resolve().is_relative_to(root) for root in roots))

    def test_plain_private_folder_outside_motionwright_still_works(self):
        result = mod.fingerprint(self.evidence_root, "ACC-001/example.bin")
        self.assertEqual(result["size_bytes"], len(self.file.read_bytes()))
        self.assertEqual(mod.load_private_manifest(self.manifest)["schema_version"], 1)
        counts = mod.validate_session(
            mod.create_session(source_sha="b" * 40), evidence_root=self.evidence_root,
        )
        self.assertEqual(counts, {"NOT_RUN": 60, "PASS": 0, "FAIL": 0, "BLOCKED": 0})

    def test_unrelated_git_repository_does_not_break_owner_downloads(self):
        # Many Windows home directories also happen to contain an unrelated
        # .git marker. Only same-Motionwright worktree membership is blocked.
        (self.other_checkout / ".git").mkdir()
        self.assertEqual(
            mod.fingerprint(self.evidence_root, "ACC-001/example.bin")["size_bytes"],
            len(self.file.read_bytes()),
        )

    def test_other_motionwright_main_checkout_rejects_evidence(self):
        (self.other_checkout / ".git").mkdir()
        with self.linked_to_motionwright():
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.fingerprint(self.evidence_root, "ACC-001/example.bin")
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.load_private_manifest(self.manifest)

    def test_other_motionwright_linked_worktree_git_file_rejected(self):
        # Linked worktrees have a .git *file*, not a repository directory.
        (self.other_checkout / ".git").write_text(
            "gitdir: C:/placeholder/never-read/.git/worktrees/example\n",
            encoding="utf-8",
        )
        with self.linked_to_motionwright():
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.fingerprint(self.evidence_root, "ACC-001/example.bin")
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.verify_private_session(self.manifest, evidence_root=self.evidence_root)
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.outside_public_checkout(self.manifest.parent, purpose="manifest")

    def test_unmarked_nested_folder_in_other_worktree_rejected(self):
        self.assertFalse((self.evidence_root / ".git").exists())
        with self.linked_to_motionwright():
            with self.assertRaisesRegex(mod.IntakeError, "outside all Motionwright worktrees"):
                mod.outside_public_checkout(self.evidence_root, purpose="evidence root")

    def test_worktree_discovery_failure_is_conservative(self):
        with patch.object(
            mod, "_motionwright_worktree_roots",
            side_effect=mod.IntakeError("Motionwright worktree authority is unavailable"),
        ):
            with self.assertRaisesRegex(mod.IntakeError, "worktree authority is unavailable"):
                mod.fingerprint(self.evidence_root, "ACC-001/example.bin")


if __name__ == "__main__":
    unittest.main()
