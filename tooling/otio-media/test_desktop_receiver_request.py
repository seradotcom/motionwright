#!/usr/bin/env python3
"""Desktop request staging controls only; no media decoder or NLE is run."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import bridge

class DesktopReceiverRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="otio-desktop-request-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / "owner-bundle"
        self.bundle.mkdir()
        self.private = self.root / "private-staging"
        self.private.mkdir()
        self.request = {"schema": bridge.KDENLIVE_REQUEST_SCHEMA,
            "capsule_manifest_sha256": "a" * 64,
            "received_otio": "received.otio", "received_sha256": "b" * 64,
            "edited_otio": "edited.otio", "edited_sha256": "c" * 64}
        self.request_path = self.private / "request.json"
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.output = self.private / "proposal.json"
        self.project = self.private / "source-project.json"

    def argv(self, *selection):
        return ["bridge.py", "preview-kdenlive-cut", "--bundle", str(self.bundle),
            "--raw-root", str(self.bundle), "--current-project", str(self.project),
            "--media-root", str(self.bundle), "--output", str(self.output), *selection]

    def invoke(self, arguments, result):
        with patch.object(sys, "argv", arguments), patch.object(sys, "stdout", io.StringIO()), \
                patch.object(sys, "stderr", io.StringIO()), \
                patch.object(bridge, "preview_kdenlive_cut", return_value=result) as reader:
            bridge.main()
            return reader.call_args

    def test_private_request_is_read_without_writing_into_the_original_bundle(self):
        result = {"status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED", "edit_imported": False}
        args = self.invoke(self.argv("--request-file", str(self.request_path)), result)
        self.assertEqual(args.args, (self.bundle, self.bundle, self.request, self.project))
        self.assertEqual(args.kwargs, {"authorized_media_roots": (self.bundle,)})
        self.assertEqual(json.loads(self.output.read_bytes()), result)
        self.assertEqual(list(self.bundle.iterdir()), [])
        self.assertEqual(json.loads(self.request_path.read_bytes()), self.request)

    def test_legacy_request_relative_to_raw_root_remains_supported(self):
        old = self.bundle / "request.json"
        old.write_bytes(self.request_path.read_bytes())
        args = self.invoke(self.argv("--request", "request.json"),
            {"status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"})
        self.assertEqual(args.args[2], self.request)
        self.assertEqual([p.name for p in self.bundle.iterdir()], ["request.json"])

    def test_relative_private_request_and_ambiguous_selections_fail_before_native_reader(self):
        for selection in (("--request-file", "relative.json"),
                ("--request", "request.json", "--request-file", str(self.request_path))):
            with self.subTest(selection=selection), patch.object(sys, "argv", self.argv(*selection)), \
                    patch.object(sys, "stderr", io.StringIO()), \
                    patch.object(bridge, "preview_kdenlive_cut") as reader:
                with self.assertRaises(SystemExit) as error:
                    bridge.main()
                self.assertEqual(error.exception.code, 2)
                reader.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_duplicate_private_request_keys_are_not_last_value_wins_authority(self):
        self.request_path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
        with patch.object(sys, "argv", self.argv("--request-file", str(self.request_path))), \
                patch.object(sys, "stderr", io.StringIO()), \
                patch.object(bridge, "preview_kdenlive_cut") as reader:
            with self.assertRaises(SystemExit) as error:
                bridge.main()
            self.assertEqual(error.exception.code, 2)
            reader.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_rejected_loss_is_preserved_and_never_promoted_to_an_owner_proposal(self):
        result = {"status": "REJECTED_LOSS", "rejection_code": "native_missing_reference_observed"}
        with self.assertRaises(SystemExit) as error:
            self.invoke(self.argv("--request-file", str(self.request_path)), result)
        self.assertEqual(error.exception.code, 3)
        self.assertEqual(json.loads(self.output.read_bytes()), result)

if __name__ == "__main__":
    unittest.main()
