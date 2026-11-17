"""Negative admission, closure and private sealing boundaries; no Native execution."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tooling import ci_tauri_audio_audition as gate

ROOT = Path(__file__).resolve().parents[2]

def admitted_environment():
    return {
        "CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_WORKFLOW_REF": gate.WORKFLOW + "fix/selected-source",
        "MW_AUDIO_DIAGNOSTIC_SELECTION": "tauri-audition",
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
    }

class DevelopmentAdmission(unittest.TestCase):
    def test_exact_explicit_source_selection_and_attempt(self):
        with patch.object(gate.sys, "platform", "linux"):
            value = gate.validate_environment(admitted_environment(), "a" * 40)
            self.assertEqual(value, {"source_sha": "a" * 40, "run_id": "123456", "run_attempt": "1"})

    def test_wrong_platform_source_event_identity_or_selection_cannot_enter(self):
        changes = [
            ("CI", "false"), ("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REPOSITORY", "other/motionwright"),
            ("GITHUB_WORKFLOW_REF", "other/repo/.github/workflows/v05-original-native-mix.yml@refs/heads/a"),
            ("GITHUB_WORKFLOW_REF", gate.WORKFLOW.replace("@refs/heads/", "@refs/tags/") + "tag"),
            ("MW_AUDIO_DIAGNOSTIC_SELECTION", "saved-mix"), ("GITHUB_SHA", "b" * 40),
            ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ID", "12\n"), ("GITHUB_RUN_ATTEMPT", "2"),
        ]
        with patch.object(gate.sys, "platform", "linux"):
            for key, value in changes:
                environment = admitted_environment()
                environment[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(gate.Rejected):
                    gate.validate_environment(environment, "a" * 40)
            with self.assertRaises(gate.Rejected):
                gate.validate_environment(admitted_environment(), "")
        with patch.object(gate.sys, "platform", "win32"), self.assertRaises(gate.Rejected):
            gate.validate_environment(admitted_environment(), "a" * 40)

    def test_json_cannot_hide_duplicates_constants_or_oversize(self):
        for raw in (b'{"source":"a","source":"b"}', b'{"gain":NaN}', b'[]', b'',
                    b'{"a":"' + b"x" * (512 * 1024) + b'"}'):
            with self.subTest(raw_length=len(raw)), self.assertRaises((gate.Rejected, ValueError)):
                gate.strict(raw)
        self.assertEqual(gate.strict(b'{"ok":true}'), {"ok": True})

    def test_only_exact_empty_supervised_tree_allows_case_selection(self):
        original = {"status": "PASS", "supervisor_pid": 101,
                    "complete_empty_descendant_tree": {
                        "root_observed": True, "truncated": False, "processes": [{"pid": 101}]}}
        self.assertTrue(gate.closed_writer_observed(original))
        invalid = []
        value = copy.deepcopy(original); value["status"] = "FAIL"; invalid.append(value)
        value = copy.deepcopy(original); value.pop("supervisor_pid"); invalid.append(value)
        value = copy.deepcopy(original); value["supervisor_pid"] = True; invalid.append(value)
        value = copy.deepcopy(original); value["complete_empty_descendant_tree"]["truncated"] = True; invalid.append(value)
        value = copy.deepcopy(original); value["complete_empty_descendant_tree"]["root_observed"] = False; invalid.append(value)
        value = copy.deepcopy(original); value["complete_empty_descendant_tree"]["processes"].append({"pid": 102}); invalid.append(value)
        value = copy.deepcopy(original); value["complete_empty_descendant_tree"]["processes"] = [{"pid": 102}]; invalid.append(value)
        value = copy.deepcopy(original); value["supervisor_pid"] = 1; value["complete_empty_descendant_tree"]["processes"] = [{"pid": True}]; invalid.append(value)
        invalid.append({"status": "PASS", "complete_empty_descendant_tree": {
            "root_observed": True, "truncated": False, "processes": [{}]}})
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(gate.closed_writer_observed(value))

    def test_manual_route_retains_old_routes_and_cannot_be_push(self):
        text = (ROOT / ".github/workflows/v05-original-native-mix.yml").read_text(encoding="utf-8")
        block = text.split("  tauri-audition:\n", 1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.verification == 'tauri-audition'", block)
        self.assertIn('MOTIONWRIGHT_DEVELOPMENT_AUDITION_BUILD_SOURCE_SHA:', block)
        self.assertIn('--bin motionwright-desktop --example native-render-e2e', block)
        self.assertIn('xvfb-run', block)
        self.assertIn('dbus-run-session', block)
        self.assertIn('ci_tauri_audio_audition.py seal', block)
        self.assertIn('/sealed-originals.bin', block)
        self.assertIn('/public-evidence.json', block)
        upload = block.split("name: Upload only sealed development originals", 1)[1]
        self.assertNotIn('motionwright-tauri-audition-', upload)
        self.assertIn('if: always()', block)
        for name, end in (("native-source-and-independent-meter", "studio-audition"),
                          ("studio-audition", "saved-mix-g0"), ("saved-mix-g0", "saved-mix")):
            original = text.split("  " + name + ":\n", 1)[1].split("  " + end + ":\n", 1)[0]
            self.assertIn("inputs.verification != 'tauri-audition'", original)
        concurrency = text.split("concurrency:\n", 1)[1].split("jobs:", 1)[0]
        self.assertIn("inputs.verification != 'tauri-audition'", concurrency)

    @unittest.skipUnless(os.name == "posix", "Private FD policy is Linux-only")
    def test_exclusive_private_output_retains_first_and_rejects_link(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value).resolve()
            root.chmod(0o700)
            target = root / "original.json"
            gate.publish(target, {"first": True})
            with self.assertRaises(FileExistsError):
                gate.publish(target, {"second": True})
            self.assertEqual(json.loads(target.read_text()), {"first": True})
            alias = root / "alias.json"
            os.link(target, alias)
            with self.assertRaises(gate.Rejected):
                gate.regular(target)
            alias.unlink()
            linked = root / "symlink.json"
            linked.symlink_to(target)
            with self.assertRaises(gate.Rejected):
                gate.regular(linked)
            target.chmod(0o644)
            with self.assertRaises(gate.Rejected):
                gate.regular(target)

    @unittest.skipUnless(os.name == "posix", "Private FD policy is Linux-only")
    def test_owner_transport_link_is_metadata_only_and_ci_scope_rejects_it(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value).resolve()
            root.chmod(0o700)
            # Deliberately nonexistent target: a successful inventory must
            # neither follow nor open this runtime lock reference.
            (root / "closed-runtime-lock").symlink_to(root / "never-opened-target")
            self.assertEqual(gate.inventory(root, owner_metadata=True), (1, 4096))
            with self.assertRaises(gate.Rejected):
                gate.inventory(root)

if __name__ == "__main__":
    unittest.main()
