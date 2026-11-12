"""GN1 source/fixture regression only; these tests never spawn a Native Host."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tooling import ci_graph_dev_native_startup as gn1


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, check=True)
    return p.stdout.strip().decode("utf-8")


def fixture_repositories(root: Path):
    repo, sdk = root / "motionwright", root / "semwright"
    repo.mkdir()
    sdk.mkdir()
    for path in (repo, sdk):
        git(path, "init", "-q")
        git(path, "config", "user.name", "GN1 Source Fixture")
        git(path, "config", "user.email", "gn1@example.invalid")
    (sdk / "README").write_text("reviewed sdk source\n", encoding="utf-8")
    git(sdk, "add", "README")
    git(sdk, "commit", "-qm", "sdk")
    pin = git(sdk, "rev-parse", "HEAD")
    (repo / "SOURCE_LOCK.json").write_text(json.dumps({
        "dependencies": {"semwright": {"revision": pin}}
    }), encoding="utf-8")
    git(repo, "add", "SOURCE_LOCK.json")
    git(repo, "commit", "-qm", "source")
    return repo, sdk, git(repo, "rev-parse", "HEAD")


class SourceIdentityTests(unittest.TestCase):
    def test_real_source_and_pinned_sdk_git_identity(self):
        with tempfile.TemporaryDirectory(prefix="gn1-git-") as tmp:
            repo, sdk, sha = fixture_repositories(Path(tmp))
            result = gn1.source_identity(repo, sdk, sha)
            self.assertEqual(result["source_sha"], sha)
            self.assertEqual(result["sdk_sha"], git(sdk, "rev-parse", "HEAD"))
            self.assertEqual(len(result["source_lock_sha256"]), 64)
            with self.assertRaises(gn1.GN1Failure):
                gn1.source_identity(repo, sdk, "f" * 40)
            (sdk / "README").write_text("changed\n", encoding="utf-8")
            with self.assertRaises(gn1.GN1Failure):
                gn1.source_identity(repo, sdk, sha)
            git(sdk, "add", "README")
            git(sdk, "commit", "-qm", "different-sdk-commit")
            with self.assertRaisesRegex(gn1.GN1Failure, "GN1_SDK_HEAD_MISMATCH"):
                gn1.source_identity(repo, sdk, sha)

    def test_native_child_environment_has_closed_values_not_only_closed_names(self):
        # Inert source regression. No Native child, credential or Store accessed.
        with tempfile.TemporaryDirectory(prefix="gn1-env-") as temp:
            root = Path(temp).resolve()
            sdk = root / "pinned-sdk"
            sdk.mkdir()
            canaries = {
                "PATH": "/synthetic/foreign-executable",
                "LANG": "SYNTHETIC_FOREIGN_LOCALE",
                "LC_ALL": "SYNTHETIC_FOREIGN_LOCALE",
                "SSL_CERT_FILE": "/synthetic/foreign-cert.pem",
                "SSL_CERT_DIR": "/synthetic/foreign-certs",
                "XDG_DATA_DIRS": "/synthetic/foreign-data",
                "GITHUB_TOKEN": "SYNTHETIC_NOT_A_CREDENTIAL",
                "GN1_RECIPIENT_JSON": "SYNTHETIC_PRIVATE_RECIPIENT",
                "LD_PRELOAD": "/synthetic/unreviewed.so",
                "PYTHONPATH": "/synthetic/unreviewed",
                "GN1_UNREVIEWED_CHILD_CANARY": "SYNTHETIC_UNREVIEWED",
            }
            with mock.patch.dict(os.environ, canaries, clear=True):
                result = gn1._env(root, root / "runtime", root / "state",
                                  root / "home", sdk)
            self.assertEqual(result, {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
                "HOME": str(root / "home"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_CACHE_HOME": str(root / "manim-work"),
                "TMPDIR": str(root / "manim-work"),
                "SEMWRIGHT_CHECKOUT": str(sdk),
                "RUST_BACKTRACE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            self.assertNotIn("SYNTHETIC_", json.dumps(result))
            self.assertFalse(set(canaries) & set(result) -
                             {"PATH", "LANG", "LC_ALL"})

    def test_default_disallows_nonrunner_and_wrong_attempt(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(gn1.GN1Failure):
                gn1.require_runner()
        env = {"GITHUB_ACTIONS": "true",
               "GITHUB_EVENT_NAME": "workflow_dispatch",
               "GITHUB_RUN_ATTEMPT": "2",
               "GITHUB_REPOSITORY": "seradotcom/motionwright",
               "GITHUB_RUN_ID": "123"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(gn1.GN1Failure):
                gn1.require_runner()

    def test_owner_root_must_be_new_under_runner_temp(self):
        with tempfile.TemporaryDirectory(prefix="gn1-fresh-") as tmp:
            parent = Path(tmp).resolve()
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(parent)}):
                root = parent / "gn1-456"
                self.assertEqual(gn1.require_owner_root(root, "456"), root)
                root.mkdir()
                with self.assertRaises(gn1.GN1Failure):
                    gn1.require_owner_root(root, "456")
                with self.assertRaises(gn1.GN1Failure):
                    gn1.require_owner_root(parent / "gn1-foreign", "456")


class PrivateOriginalTests(unittest.TestCase):
    def test_seal_snapshot_collects_only_owned_original_bytes(self):
        with tempfile.TemporaryDirectory(prefix="gn1-seal-") as tmp:
            root = Path(tmp)
            private = gn1.mkdir(root, "private")
            gn1.new_bytes(private / "startup.original.log", b"private-canary-data")
            gn1.mkdir(root, "transport")
            raw, count = gn1.private_snapshot(root)
            self.assertEqual(count, 1)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as archive:
                names = archive.getnames()
                self.assertEqual(names, ["private/startup.original.log"])
                self.assertEqual(archive.extractfile(names[0]).read(), b"private-canary-data")
                self.assertNotIn("transport", names)
            with self.assertRaises(gn1.GN1Failure):
                gn1.new_bytes(private / "startup.original.log", b"replayed")

    def test_aggregate_ciphertext_has_separate_bounded_write_limit(self):
        with tempfile.TemporaryDirectory(prefix="gn1-capacity-") as tmp:
            root = Path(tmp)
            payload = b"abcdefghi"
            with self.assertRaises(gn1.GN1Failure):
                gn1.new_bytes(root / "single-original", payload, max_bytes=8)
            gn1.new_bytes(root / "sealed-envelope", payload, max_bytes=9)
            self.assertEqual((root / "sealed-envelope").read_bytes(), payload)
            with self.assertRaises(gn1.GN1Failure):
                gn1.new_bytes(root / "sealed-envelope", payload, max_bytes=9)
        code = (Path(__file__).resolve().parents[1] /
                "ci_graph_dev_native_startup.py").read_text(encoding="utf-8")
        self.assertIn("max_bytes=MAX_PRIVATE_BYTES + 2 * 1024 * 1024 + 8192", code)

    def test_owner_stage_marker_is_not_evidence_of_launched_daemon(self):
        with tempfile.TemporaryDirectory(prefix="gn1-owner-original-") as tmp:
            private = gn1.mkdir(Path(tmp), "private")
            empty = gn1.original_owner_start_observation(private, [])
            self.assertEqual(empty["physical_owner_startup"], "NOT_EXECUTED")
            self.assertEqual(empty["original_start_receipts"], 0)
            stages = [{"stage": "BOOTSTRAP_SOCKET"}]
            missing = gn1.original_owner_start_observation(private, stages)
            self.assertEqual(missing["physical_owner_startup"], "UNKNOWN")
            self.assertTrue(missing["unobserved_launch_possible"])
            self.assertEqual(missing["original_start_receipts"], 0)
            gn1.new_json(private / "bootstrap.start.original.json",
                         {"pid": 1001, "automatic_retry": False})
            first = gn1.original_owner_start_observation(private, stages)
            self.assertEqual(first["physical_owner_startup"], "RECORDED_REAL_START_NOT_G2")
            self.assertEqual(first["original_start_receipts"], 1)
            stages.append({"stage": "RESTRICTED_SOCKET"})
            incomplete = gn1.original_owner_start_observation(private, stages)
            self.assertEqual(incomplete["physical_owner_startup"], "UNKNOWN")
            self.assertEqual(incomplete["original_start_receipts"], 1)
            gn1.new_json(private / "restricted.start.original.json",
                         {"pid": 1002, "automatic_retry": False})
            complete = gn1.original_owner_start_observation(private, stages)
            self.assertEqual(complete["original_start_receipts"], 2)
            self.assertFalse(complete["unobserved_launch_possible"])
            self.assertEqual(complete["physical_owner_startup"], "RECORDED_REAL_START_NOT_G2")

    def test_private_encryption_is_independently_authenticated(self):
        # The private reviewer key is generated in memory for this synthetic
        # fixture only; no Native process and no files leave the temp root.
        import base64
        import hashlib
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        # The production GN1 CLI imports first-party tools from its script
        # directory; reproduce that import context without native execution.
        with mock.patch.object(sys, "path",
                               [str(Path(__file__).resolve().parents[1]), *sys.path]):
            import ci_private_evidence as custody
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                     "sha256": hashlib.sha256(der).hexdigest()}
        context = {"source_sha": "1" * 40, "semwright_sha": "2" * 40,
                   "run_id": "42", "run_attempt": "1"}
        with tempfile.TemporaryDirectory(prefix="gn1-sealed-") as tmp:
            root = Path(tmp)
            private = gn1.mkdir(root, "private")
            gn1.new_bytes(private / "secret.original.log", b"CANARY_PRIVATE_GN1")
            original, count = gn1.private_snapshot(root)
            self.assertEqual(count, 1)
            sealed = custody.encrypt_snapshot(original, recipient, context)
            self.assertNotIn(b"CANARY_PRIVATE_GN1", sealed)
            unsealed, recovered_context = custody.decrypt_snapshot(sealed, key)
            self.assertEqual(unsealed, original)
            self.assertEqual(recovered_context, context)
            damaged = sealed[:-1] + bytes([sealed[-1] ^ 1])
            with self.assertRaises(Exception):
                custody.decrypt_snapshot(damaged, key)

    @unittest.skipIf(os.name == "nt", "Windows test user may lack symlink privilege")
    def test_private_symlink_denied_before_encrypted_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="gn1-symlink-") as tmp:
            root = Path(tmp)
            folder = gn1.mkdir(root, "private")
            outside = root / "foreign"
            outside.write_bytes(b"secret")
            (folder / "foreign").symlink_to(outside)
            with self.assertRaises(gn1.GN1Failure):
                gn1.private_snapshot(root)

    def test_public_status_is_permanently_not_g2_or_r74(self):
        source = {"source_sha": "1" * 40, "sdk_sha": "2" * 40}
        public = gn1.simple_public(source, "42")
        self.assertEqual(public["Graph6_cases_accepted"], 0)
        self.assertEqual(public["R74_statuses_issued"], 0)
        self.assertEqual(public["G2"], "NOT_GRANTED")
        self.assertFalse(public["formal_acceptance"])
        self.assertFalse(public["automatic_retry"])
        self.assertEqual(public["G3"], "STOP")
        self.assertEqual(len(set(gn1.STAGES)), len(gn1.STAGES))
        self.assertEqual(gn1.STAGES[-1], "PRIVATE_SEAL")

    def test_actual_daemon_entrypoint_never_runs_in_plan(self):
        with mock.patch.object(gn1, "run_once", side_effect=AssertionError("native run")):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                result = gn1.main([
                    "--mode", "plan", "--repo", "/not-used", "--sdk", "/not-used",
                    "--expected-source", "1" * 40, "--root", "/not-used",
                ])
            self.assertEqual(result, 0)
            value = json.loads(buffer.getvalue())
            self.assertEqual(value["Graph6_cases_accepted"], 0)
            self.assertEqual(value["mode"], "PLAN_ONLY_NO_EFFECTS")


class WorkflowBoundaryTests(unittest.TestCase):
    def test_original_first_party_graph_manifest_is_reused_without_grant_edits(self):
        source = (Path(__file__).resolve().parents[1] /
                  "ci_graph_dev_native_startup.py").read_text()
        self.assertIn("graph.graph_manifest(", source)
        self.assertIn("graph.graph_owner_config(", source)
        self.assertIn("shared.owner_config(", source)
        self.assertIn('root, "shared-state", selected=True', source)
        self.assertIn("GN1_SOURCE_CHANGED_AFTER_REAL_OWNER", source)
        self.assertIn("GN1_CARGO_EXECUTABLE_ALIAS_FORBIDDEN", source)
        self.assertNotIn("no-sandbox", source)
        self.assertNotIn("synthetic-authority", source)

    @unittest.skipUnless(sys.platform == "linux", "Execute actual Bash G0 boundary")
    def test_a4_gn1_development_scope_is_exact_and_keeps_other_acceptance_gates(self):
        text = (Path(__file__).resolve().parents[2] /
                ".github/workflows/studio-service-verification.yml").read_text()
        start = text.index('          if [[ "$GITHUB_EVENT_NAME" != "workflow_dispatch"')
        end = text.index("          fi\n", start) + len("          fi\n")
        script = "\n".join(line[10:] for line in text[start:end].splitlines())
        script = script.replace("python -B tooling/acceptance_policy.py", "printf 'acceptance\\n'")
        script = script.replace("python -B tooling/creative_status_policy.py", "printf 'creative\\n'")
        a1 = "refs/heads/integrate/phase1-final-composition-20261027"
        a4 = "refs/heads/fix/native-production-causal-20261106-a4"
        cases = [
            ("workflow_dispatch", a1, "phase1-service47", True),
            ("workflow_dispatch", a1, "phase1-native-graph", True),
            ("workflow_dispatch", a1, "phase1-native-gn1", True),
            ("workflow_dispatch", a4, "phase1-native-gn1", True),
            ("workflow_dispatch", a4, "phase1-native-graph", True),
            ("workflow_dispatch", a4 + "-extra", "phase1-native-graph", False),
            ("workflow_dispatch", a4.replace("/heads/", "/tags/"), "phase1-native-graph", False),
            ("pull_request", a4, "phase1-native-graph", False),
            ("push", a4, "phase1-native-graph", False),
            ("workflow_dispatch", a4, "phase1-service47", False),
            ("workflow_dispatch", a4 + "-extra", "phase1-native-gn1", False),
            ("workflow_dispatch", a4.replace("/heads/", "/tags/"), "phase1-native-gn1", False),
            ("pull_request", a4, "phase1-native-gn1", False),
            ("push", a4, "phase1-native-gn1", False),
        ]
        for event, ref, selection, deferred in cases:
            with self.subTest(event=event, ref=ref, selection=selection):
                result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script],
                    env={**os.environ, "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref,
                         "REQUESTED_G0_SELECTION": selection},
                    capture_output=True, text=True, timeout=5, check=True)
                self.assertEqual(result.stdout.splitlines(),
                                 [] if deferred else ["acceptance", "creative"])

    def test_registered_studio_gn1_selection_excludes_regular_service_and_g3(self):
        studio = (Path(__file__).resolve().parents[2] /
                  ".github/workflows/studio-service-verification.yml").read_text()
        native = (Path(__file__).resolve().parents[2] /
                  ".github/workflows/graph-native-startup-dev.yml").read_text()
        self.assertIn("phase1-native-gn1, criterion-partials", studio)
        self.assertIn('"$REQUESTED_G0_SELECTION" != "phase1-native-gn1"', studio)
        self.assertIn(
            "workflow_dispatch:refs/heads/integrate/phase1-final-composition-20261027:phase1-native-gn1)",
            studio)
        self.assertIn("inputs.selection != 'phase1-native-gn1'", studio)
        self.assertIn("needs: g0", studio)
        self.assertIn("uses: ./.github/workflows/graph-native-startup-dev.yml", studio)
        self.assertIn("recipient_json: ${{ format(", studio)
        self.assertIn("one_original_owner: allow-one-original", studio)
        self.assertIn("workflow_call:", native)
        self.assertIn("cancel-in-progress: false", native)
        self.assertNotIn("\n  push:", native)
        self.assertNotIn("\n  pull_request:", native)
        self.assertIn("inputs.one_original_owner == 'allow-one-original'", native)

    def test_studio_gn1_save_is_a_separate_explicit_manual_consent(self):
        repo = Path(__file__).resolve().parents[2]
        studio = (repo / ".github/workflows/studio-service-verification.yml").read_text()
        native = (repo / ".github/workflows/graph-native-startup-dev.yml").read_text()
        # Explicit manual Studio dispatch can select the original GN1 owner
        # WITHOUT silently granting the distinct real project-edit Save.
        choice = studio.split("      gn1_one_original_save:\n", 1)
        self.assertEqual(len(choice), 2,
                         "Registered Studio workflow lacks separate GN1 Save consent")
        declaration = choice[1].split("\n      graph_review_spki_der_base64:", 1)[0]
        self.assertIn("type: choice", declaration)
        self.assertIn("options: [deny, allow-one-original-save]", declaration)
        self.assertIn("default: deny", declaration)
        call = studio.split("\n  phase1-native-gn1:\n", 1)
        self.assertEqual(len(call), 2)
        self.assertIn("uses: ./.github/workflows/graph-native-startup-dev.yml", call[1])
        self.assertIn("one_original_owner: allow-one-original", call[1])
        self.assertIn("one_original_save: ${{ inputs.gn1_one_original_save }}", call[1])
        self.assertNotIn("one_original_save: allow-one-original-save", call[1])
        # The branch-local effect workflow still requires exact separate
        # consent and has NO automatic push/PR Native execution.
        self.assertIn("if: inputs.one_original_save == 'allow-one-original-save'", native)
        self.assertIn("default: deny", native)
        self.assertNotIn("\n  push:", native)
        self.assertNotIn("\n  pull_request:", native)

    def test_workflow_is_one_manual_nonretry_effect_and_ciphertext_only(self):
        text = (Path(__file__).resolve().parents[2] /
                ".github/workflows/graph-native-startup-dev.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("\n  push:", text)
        self.assertNotIn("\n  pull_request:", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("one_original_owner", text)
        self.assertIn("allow-one-original", text)
        self.assertIn("test \"$GITHUB_RUN_ATTEMPT\" = 1", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("SEMWRIGHT_TEST_DRIVER_SANDBOX: '1'", text)
        self.assertIn("SEMWRIGHT_TEST_SANDBOX_HELPER:", text)
        self.assertIn("cargo build --locked", text)
        self.assertIn("graph-gn1-dev-", text)
        self.assertIn("/transport", text)
        self.assertIn("path: ${{ runner.temp }}/gn1-${{ github.run_id }}/transport", text)
        self.assertNotIn("graph produce", text)
        self.assertNotIn("graph wait", text)
        self.assertNotIn("graph run", text)
        self.assertNotIn("--no-" + "sandbox", text)
        self.assertNotIn("--disable-" + "web-security", text)


if __name__ == "__main__":
    unittest.main()
