"""Exercise the actual inline internal-candidate producer with synthetic bytes."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_source_lock_admission import write_distribution_fixture
from source_lock_admission import verify_distribution_admission
import nsis_owned_files as owned
import release_manifest

WORKFLOW = ROOT / ".github/workflows/package-candidate.yml"


def producer():
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.split("      - name: Prepare isolated Windows candidate capture\n", 1)[1]
    block = step.split("      - name: Bundle the exact isolated Windows candidate\n", 1)[0]
    raw = block.split("          @'\n", 1)[1].split("          '@ | python -B -", 1)[0]
    return compile(textwrap.dedent(raw), str(WORKFLOW) + ":internal-capture", "exec")


class InternalWindowsCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-internal-candidate-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "consumer"
        self.fixture = write_distribution_fixture(self.root)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            (self.root / name).write_bytes(b"Synthetic internal control; no real license grant.\n")
        self.payload = self.root / "target/release/motionwright-desktop.exe"
        self.payload.parent.mkdir(parents=True)
        self.payload.write_bytes(self.fixture["payload_bytes"])
        self.environment_file = self.root / "job.env"
        self.environment_file.write_bytes(b"")
        self.environment = {
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "MW_WINDOWS_CANDIDATE_SCOPE": "windows_installed",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "123456",
            "GITHUB_ENV": str(self.environment_file),
        }

    def execute(self, **changes):
        environment = {**self.environment, **changes}
        with mock.patch.dict(os.environ, environment), \
             mock.patch.object(sys, "path", list(sys.path)), \
             mock.patch.object(release_manifest, "_checkout") as checkout, \
             contextlib.redirect_stdout(io.StringIO()):
            namespace = {}
            exec(producer(), namespace)
        checkout.assert_has_calls([mock.call(self.root, "a" * 40)] * 2)
        return namespace

    def test_internal_capture_binds_real_fixture_bytes_and_denies_a_changed_payload(self):
        result = self.execute()
        record = json.loads((self.root / result["record_path"]).read_bytes())
        admitted = verify_distribution_admission(
            self.root, arch="x64", record_path=result["record_path"],
            record_sha256=result["pin"],
        )
        self.assertEqual(admitted["sha256"], hashlib.sha256(self.payload.read_bytes()).hexdigest())
        self.assertIn("automated", record["review"]["issuer"])
        self.assertIn("Public redistribution clearance and human acceptance are not asserted", record["review"]["basis"])
        output = self.root / "captures"
        output.mkdir()
        owned.capture_owned_file(
            self.payload, output / "good.bin", output / "good.nsh", "MW_MAIN",
            expected_payload=admitted,
        )
        self.assertEqual((output / "good.bin").read_bytes(), self.fixture["payload_bytes"])
        self.payload.write_bytes(self.payload.read_bytes() + b"altered")
        with self.assertRaisesRegex(owned.CaptureError, "distribution_admission_payload_mismatch"):
            owned.capture_owned_file(
                self.payload, output / "bad.bin", output / "bad.nsh", "MW_MAIN",
                expected_payload=admitted,
            )
        self.assertFalse((output / "bad.bin").exists())
        self.assertFalse((output / "bad.nsh").exists())

    def test_other_events_scopes_repositories_and_attempts_create_no_admission(self):
        for key, value in (
            ("GITHUB_ACTIONS", "false"), ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_EVENT_NAME", "pull_request"), ("MW_WINDOWS_CANDIDATE_SCOPE", "all"),
            ("MW_WINDOWS_CANDIDATE_SCOPE", "nsis_lifecycle"),
            ("GITHUB_REPOSITORY", "foreign/repository"), ("GITHUB_RUN_ATTEMPT", "2"),
        ):
            with self.subTest(key=key, value=value), \
                 mock.patch.object(release_manifest, "_checkout") as checkout:
                with self.assertRaisesRegex(ValueError, "internal_candidate_scope_refused"):
                    self.execute(**{key: value})
                checkout.assert_not_called()
                self.assertFalse((self.root / "ci-evidence/internal-windows-capture").exists())
                self.assertEqual(self.environment_file.read_bytes(), b"")

    def test_missing_retained_review_bytes_cannot_create_admission(self):
        (self.root / "LICENSE").unlink()
        with self.assertRaises(ValueError):
            self.execute()
        self.assertFalse((self.root / "ci-evidence/internal-windows-capture").exists())
        self.assertEqual(self.environment_file.read_bytes(), b"")

    def test_internal_route_does_not_export_payload_admission_or_target_cache(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        windows = text.split("\n  windows:\n", 1)[1].split("\n  windows-update:\n", 1)[0]
        external = "github.event_name != 'workflow_dispatch' || inputs.verification_scope != 'windows_installed'"
        self.assertIn("save-if: " + "${{ " + external + " }}", windows)
        self.assertIn("tauri bundle --bundles nsis --no-binary-patching --no-sign --ci", windows)
        self.assertIn("tauri build --no-bundle --ci -- --locked", windows)
        allowed = {
            "ci-evidence/windows-candidate.json",
            "ci-evidence/windows-nsis-smoke.json",
            "ci-evidence/windows-nsis-cold-relaunch.json",
            "ci-evidence/windows-nsis-cold-relaunch-diagnostic.json",
        }
        uploads = [part for part in re.split(r"(?m)^      - ", windows)
                   if "uses: actions/upload-artifact@" in part]
        self.assertEqual(len(uploads), 3)
        for step in uploads:
            paths = re.findall(r"(?m)^            (\S+)\s*$", step)
            self.assertNotIn("internal-windows-capture", step)
            if "target/release/bundle/nsis/*-setup.exe" in paths:
                self.assertIn("if: " + external, step)
            else:
                self.assertTrue(paths)
                self.assertLessEqual(set(paths), allowed)


if __name__ == "__main__":
    unittest.main()
