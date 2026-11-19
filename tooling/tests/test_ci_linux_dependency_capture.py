"""Real Git/filesystem contract tests with explicitly inert package records.

No Cargo compiler, patchelf, package installer, application or Native runs here.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_linux_dependency_capture as gate
import test_candidate_cargo_producer as fixtures
from test_candidate_receipt_source import git


class LinuxDependencyCaptureTests(unittest.TestCase):
    def setUp(self):
        fixtures.CandidateCargoProducerTests.setUp(self)
        config = self.root / "apps/desktop/src-tauri/tauri.conf.json"
        config.write_bytes((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_bytes())
        from candidate_smoke import NOTICE_FILES
        for original, _ in NOTICE_FILES:
            path = self.root / original
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / original).read_bytes())
        git(self.root, "add", "apps/desktop/src-tauri/tauri.conf.json",
            *(original for original, _ in NOTICE_FILES))
        git(self.root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "Linux resource fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")
        fixtures.CandidateCargoProducerTests.cargo_fixture(self, gate.TARGET)
        binary = self.root / gate.BINARY
        binary.parent.mkdir(parents=True)
        self.cargo_binary.replace(binary)
        self.cargo_binary = binary
        notice = b"Inert notice fixture; not a production license.\n"
        digest = gate.capture.sha(notice)
        self.cargo_index_data["components"][0]["notices"] = [{"sha256": digest}]
        self.cargo_index = self.root / gate.BASE / "resources/cargo/index.json"
        self.cargo_index.parent.mkdir(parents=True)
        self.notice_path = self.cargo_index.parent / "texts" / (digest + ".txt")
        self.notice_path.parent.mkdir()
        self.notice_path.write_bytes(notice)
        self.custody = {
            "profile": {"source": self.sha, "target": gate.TARGET, "schema": gate.PROFILE},
            "fixture_only": "No compiler or generator executed",
        }
        self.custody_path = self.root / gate.BASE / "state/tauri-generated-schemas.json"
        gate.capture.write_new(self.custody_path, gate.capture.encoded(self.custody))
        digest = gate.capture.sha(self.custody_path.read_bytes())
        self.cargo_index_data["capture"] = {"generated_metadata_sha256": digest}
        self.save_cargo_index()
        self.complete = {
            "source": self.sha, "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes()),
            "artifact_identities_sha256": self.cargo_index_data["artifact_identities_sha256"],
            "full_package_inventory": "NOT_VERIFIED", "generated_metadata_sha256": digest,
        }
        self.complete_path = self.root / gate.BASE / "state/capture-complete.json"
        gate.capture.write_new(self.complete_path, gate.capture.encoded(self.complete))
        self.output = self.root / "ci-evidence/linux-cargo-binding.json"
        # Existing inert Cargo tests retain seven deterministic source paths;
        # the real Linux witness and fail-closed byte checks are covered by
        # test_linux_native_notice_resources on synthetic dpkg originals.
        self.fake_native_mapping = {
            (self.root / gate.native_notices.ROOT / pkg / "copyright").as_posix():
            "licenses/linux-system-notices/" + pkg + "/copyright"
            for pkg, _, _ in gate.native_notices.SPEC
        }
        self.enterContext(mock.patch.object(
            gate.native_notices, "stage", return_value={"claims": {"G3": "STOP"}}))
        self.enterContext(mock.patch.object(
            gate.native_notices, "mapping", return_value=self.fake_native_mapping))

    save_cargo_index = fixtures.CandidateCargoProducerTests.save_cargo_index

    def package_fixture(self):
        from candidate_smoke import _verify_cargo_notice_subtree
        from package_receipt import make_receipt

        gate.stage_resources(self.root, self.sha)
        paths = []
        for kind, suffix in (("deb", "deb"), ("appimage", "AppImage")):
            path = self.root / gate.BINARY.parent / "bundle" / kind / ("Motionwright." + suffix)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"INERT PACKAGE FIXTURE " + kind.encode())
            paths.append(path)
        self.candidate = make_receipt(
            self.root, self.sha, "linux", "x86_64", paths,
            cargo_index=self.cargo_index, cargo_binary=self.cargo_binary, require_cargo_binding=True)
        gate.capture.write_new(self.root / "ci-evidence/linux-candidate.json",
                               gate.capture.encoded(self.candidate))
        _, _, _, observed = gate.cargo_binding(self.root, self.sha)
        notices = _verify_cargo_notice_subtree(
            capture=gate.capture, index_raw=self.cargo_index.read_bytes(),
            anchor=self.root / gate.BASE / "resources", relative=Path("cargo"))
        self.smokes = {}
        for row in self.candidate["artifacts"]:
            kind = row["bundle_kind"]
            smoke = {
                "schema_version": 1, "candidate": self.candidate["candidate"],
                "bundle_kind": kind,
                "candidate_archive": {key: row[key] for key in ("filename", "bytes", "sha256")},
                "automated_inspection": {
                    "status": "PASS", "method": kind + "_extract",
                    "binary": {"binary_bytes": observed["bytes"], "binary_sha256": observed["sha256"]},
                    "cargo_build_binding": {**observed, "notice_resources": notices},
                },
                "claims": {"human_install_acceptance": "NOT_RUN",
                           "runtime_renderer_acceptance": "SEPARATE_GATE", "signing": "NOT_CLAIMED",
                           "notarization": "NOT_CLAIMED", "release_published": False},
            }
            self.smokes[kind] = smoke
            gate.capture.write_new(self.smoke_path(kind), gate.capture.encoded(smoke))
        self.frontend = {
            "source_sha": self.sha, "source_tree": observed["source_tree"],
            "observed_npm_source_and_notice_authentication": "VERIFIED",
            "source_binding": "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "frontend_index_sha256": "4" * 64,
        }
        for phase in ("before-build", "after-bundle"):
            gate.capture.write_new(self.root / gate.BASE / "frontend" / (phase + ".json"),
                                   gate.capture.encoded(self.frontend))

    def smoke_path(self, kind):
        return self.root / ("ci-evidence/linux-" + kind + "-smoke.json")

    def test_linux_layout_does_not_mutate_windows_defaults(self):
        defaults = (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY)
        binding = gate.stage_resources(self.root, self.sha)
        self.assertEqual(binding["target"], gate.TARGET)
        self.assertEqual(defaults, (gate.shared.BASE, gate.shared.TARGET, gate.shared.BINARY))
        self.assertFalse((self.root / gate.shared.BASE / "tauri-resources.json").exists())
        mapped = json.loads((self.root / gate.BASE / "tauri-resources.json").read_bytes())
        self.assertEqual(len(mapped["bundle"]["resources"]), 13)
        self.assertEqual(
            {value for value in mapped["bundle"]["resources"].values()
             if value.startswith("licenses/linux-system-notices/")},
            set(self.fake_native_mapping.values()))
        self.assertIn("licenses/cargo/index.json", mapped["bundle"]["resources"].values())


    def test_real_capture_sized_index_keeps_staging_digest_binding(self):
        # Size of the original Linux index from failed run 43380010798.
        raw = self.cargo_index.read_bytes()
        raw += b" " * (17305904 - len(raw))
        self.cargo_index.write_bytes(raw)
        self.complete["cargo_index_sha256"] = gate.capture.sha(raw)
        self.complete_path.write_bytes(gate.capture.encoded(self.complete))
        binding = gate.stage_resources(self.root, self.sha)
        self.assertEqual(binding["cargo_index_sha256"], gate.capture.sha(raw))
        self.assertEqual(binding["status"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
        self.cargo_index.write_bytes(raw[:-1] + b"\n")
        with self.assertRaisesRegex(ValueError, "windows_cargo_completion_mismatch"):
            gate.cargo_binding(self.root, self.sha)

    def test_oversized_cargo_index_cannot_publish_resource_overlay(self):
        raw = b"{}" + b" " * (gate.capture.MAX_CARGO_INDEX_BYTES - 1)
        self.cargo_index.write_bytes(raw)
        self.complete["cargo_index_sha256"] = gate.capture.sha(raw)
        self.complete_path.write_bytes(gate.capture.encoded(self.complete))
        with self.assertRaisesRegex(gate.capture.CaptureError, "file_budget_exceeded"):
            gate.stage_resources(self.root, self.sha)
        self.assertFalse((self.root / gate.BASE / "tauri-resources.json").exists())

    def test_both_package_observations_bind_exact_bytes_without_full_inventory_credit(self):
        self.package_fixture()
        result = gate.verify_packages(self.root, self.sha)
        self.assertEqual(set(result["packages"]), {"deb", "appimage"})
        for row in result["packages"].values():
            self.assertEqual(row["binary_binding"], "CAPTURED_ROOT_BINARY_BYTE_EXACT")
            self.assertTrue(row["cargo_notices"]["subtree_closed"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(result["redistribution_permission"], "NOT_INFERRED")
        self.assertFalse(result["published"])
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            gate.verify_packages(self.root, self.sha)
        self.assertEqual(self.output.read_bytes(), original)

    def test_wrong_capture_links_or_crossed_generator_profile_refuse_staging(self):
        for field, value in (("cargo_index_sha256", "0" * 64),
                             ("artifact_identities_sha256", "1" * 64),
                             ("generated_metadata_sha256", "2" * 64), ("source", "3" * 40)):
            self.complete_path.write_bytes(gate.capture.encoded({**self.complete, field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.stage_resources(self.root, self.sha)
            self.assertFalse((self.root / gate.BASE / "tauri-resources.json").exists())
        self.complete_path.write_bytes(gate.capture.encoded(self.complete))
        crossed = copy.deepcopy(self.custody)
        crossed["profile"]["schema"] = "motionwright.windows-cargo-generated-metadata/2"
        self.custody_path.write_bytes(gate.capture.encoded(crossed))
        digest = gate.capture.sha(self.custody_path.read_bytes())
        self.cargo_index_data["capture"]["generated_metadata_sha256"] = digest
        self.save_cargo_index()
        self.complete_path.write_bytes(gate.capture.encoded({
            **self.complete, "generated_metadata_sha256": digest,
            "cargo_index_sha256": gate.capture.sha(self.cargo_index.read_bytes())}))
        with self.assertRaisesRegex(ValueError, "cargo_generated_custody_profile_mismatch"):
            gate.stage_resources(self.root, self.sha)

    def test_overlay_or_notice_extras_cannot_pass_package_readback(self):
        self.package_fixture()
        overlay = self.root / gate.BASE / "tauri-resources.json"
        original = overlay.read_bytes()
        altered = json.loads(original)
        altered["bundle"]["resources"]["foreign"] = "foreign"
        overlay.write_bytes(gate.capture.encoded(altered))
        with self.assertRaisesRegex(ValueError, "linux_cargo_overlay_changed"):
            gate.verify_packages(self.root, self.sha)
        overlay.write_bytes(original)
        (self.notice_path.parent / "unexpected.txt").write_bytes(b"Unlisted notice")
        with self.assertRaisesRegex(ValueError, "candidate_cargo_"):
            gate.verify_packages(self.root, self.sha)
        self.assertFalse(self.output.exists())

    def test_either_package_cannot_substitute_legacy_or_wrong_binary_binding(self):
        self.package_fixture()
        for kind in ("deb", "appimage"):
            original = copy.deepcopy(self.smokes[kind])
            for field, value in (("status", "NOT_VERIFIED"), ("sha256", "0" * 64),
                                 ("cargo_index_sha256", "1" * 64), ("source_tree", "2" * 40)):
                changed = copy.deepcopy(original)
                changed["automated_inspection"]["cargo_build_binding"][field] = value
                self.smoke_path(kind).write_bytes(gate.capture.encoded(changed))
                with self.subTest(kind=kind, field=field), self.assertRaisesRegex(
                        ValueError, "linux_package_cargo_binding_mismatch"):
                    gate.verify_packages(self.root, self.sha)
                self.assertFalse(self.output.exists())
            self.smoke_path(kind).write_bytes(gate.capture.encoded(original))

    def test_wrong_archive_or_changed_frontend_cannot_receive_report(self):
        self.package_fixture()
        original = self.smokes["appimage"]
        changed = copy.deepcopy(original)
        changed["candidate_archive"]["sha256"] = "0" * 64
        self.smoke_path("appimage").write_bytes(gate.capture.encoded(changed))
        with self.assertRaisesRegex(ValueError, "linux_package_observation_mismatch"):
            gate.verify_packages(self.root, self.sha)
        self.smoke_path("appimage").write_bytes(gate.capture.encoded(original))
        path = self.root / gate.BASE / "frontend/after-bundle.json"
        path.write_bytes(gate.capture.encoded({**self.frontend, "frontend_index_sha256": "5" * 64}))
        with self.assertRaisesRegex(ValueError, "linux_frontend_authentication_required"):
            gate.verify_packages(self.root, self.sha)
        self.assertFalse(self.output.exists())

    def test_changed_capture_binary_rejected_before_bundle_process(self):
        gate.stage_resources(self.root, self.sha)
        self.cargo_binary.write_bytes(b"changed captured binary")
        environment = {"RUSTFLAGS": gate.RUSTFLAGS, "PATCHELF": gate.PATCHELF, "NO_STRIP": "1"}
        with mock.patch.object(gate.capture, "run_captured") as process:
            with self.assertRaisesRegex(gate.capture.CaptureError,
                                        "installed_binary_differs_from_cargo_capture"):
                gate.bundle_packages(self.root, self.sha, environment)
            process.assert_not_called()


class LinuxScopeTests(unittest.TestCase):
    def test_scope_and_link_override_refusals_precede_effects(self):
        env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_LINUX_CANDIDATE_SCOPE": "linux_bundle", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "1" * 40, "GITHUB_RUN_ID": "123",
        }
        self.assertEqual(gate.linux_source(env), "1" * 40)
        for key, value in (("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "other/repo"),
                           ("GITHUB_REF", "refs/heads/main"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("MW_LINUX_CANDIDATE_SCOPE", "all"), ("GITHUB_SHA", "1" * 8),
                           ("GITHUB_RUN_ID", "0")):
            with self.subTest(key=key), mock.patch.dict(os.environ, {**env, key: value}, clear=True), \
                 mock.patch.object(gate.shared, "authenticate_frontend") as frontend, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(["frontend"]), 2)
                frontend.assert_not_called()
        selected = {"RUSTFLAGS": gate.RUSTFLAGS, "PATCHELF": gate.PATCHELF, "NO_STRIP": "1"}
        self.assertEqual(gate.link_environment(selected), selected)
        for key, value in (("RUSTFLAGS", ""), ("PATCHELF", "/foreign/patchelf"),
                           ("NO_STRIP", ""), ("CARGO_ENCODED_RUSTFLAGS", "override"),
                           ("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS", "override")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.link_environment({**selected, key: value})


    def test_installed_scope_requires_its_exact_original_job(self):
        env = {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_LINUX_CANDIDATE_SCOPE": "linux_first_use", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "linux-first-use-installed",
            "GITHUB_SHA": "1" * 40, "GITHUB_RUN_ID": "123",
        }
        self.assertEqual(gate.linux_source(env), env["GITHUB_SHA"])
        for key, value in (("GITHUB_JOB", "linux"), ("GITHUB_JOB", ""),
                           ("GITHUB_JOB", "linux-first-use-source"),
                           ("MW_LINUX_CANDIDATE_SCOPE", "linux_first_use_source"),
                           ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_REF", "refs/heads/main"),
                           ("GITHUB_EVENT_NAME", "pull_request")):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(
                    ValueError, "linux_dependency_scope_refused"):
                gate.linux_source({**env, key: value})


class InstalledLinuxCaptureWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tooling.tests.test_ci_candidate_macos_scope import job_source
        workflow = (ROOT / ".github/workflows/package-candidate.yml").read_text(encoding="utf-8")
        cls.installed = job_source(workflow, "linux-first-use-installed")
        cls.bundle = job_source(workflow, "linux")

    def test_original_capture_and_bound_prelaunch_precede_one_installed_attempt(self):
        body = self.installed
        chain = (
            "tooling/ci_linux_first_use.py reserve",
            "tooling/native_host_installed_shared_state.py --ci-controls",
            "scripts/candidate_dependency_capture.py",
            "tooling/ci_linux_dependency_capture.py stage",
            "tooling/ci_linux_dependency_capture.py bundle",
            "tooling/ci_linux_dependency_capture.py frontend-recheck",
            "--require-cargo-binding",
            "--output ci-evidence/linux-appimage-prelaunch.json",
            "tooling/ci_linux_first_use.py installed",
            "--output ci-evidence/linux-appimage-smoke.json",
            "tooling/ci_linux_dependency_capture.py verify",
            "tooling/native_host_installed_shared_state.py --ci-run",
        )
        positions = [body.index(token) for token in chain]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(body.count('"$cargo_executable" build --locked --release --bins'), 1)
        self.assertEqual(body.count("tooling/ci_linux_first_use.py installed"), 1)
        self.assertNotIn("tauri build", body)
        self.assertIn("--cargo-index ci-evidence/linux-cargo/resources/cargo/index.json", body)
        self.assertIn('--cargo-binary "$MW_LINUX_BUNDLE_ROOT/motionwright-desktop"', body)

    def test_packaging_environment_and_payload_root_cannot_cross_into_native(self):
        import re
        body = self.installed
        header = body.split("    steps:\n", 1)[0]
        self.assertIn("      MW_LINUX_BUNDLE_ROOT: " + gate.BINARY.parent.as_posix() + "\n", header)
        self.assertNotIn("target/release", body)
        self.assertNotIn("GITHUB_ENV", body)
        for variable in ("RUSTFLAGS", "CARGO_HOME", "CARGO_TARGET_DIR", "PATCHELF", "NO_STRIP"):
            self.assertNotRegex(header, r"(?m)^      " + variable + ":")
            self.assertIn("export " + variable + "=", body)
        native = body.split("      - name: Same-job original installed Desktop to Native and back\n", 1)[1]
        for variable in ("RUSTFLAGS", "CARGO_HOME", "CARGO_TARGET_DIR", "PATCHELF", "NO_STRIP"):
            self.assertNotIn("export " + variable + "=", native)
        producer = (ROOT / "tooling/native_host_installed_shared_state.py").read_text(encoding="utf-8")
        expected = (gate.BINARY.parent / "bundle/appimage").as_posix()
        self.assertIn('repo / "' + expected + '"', producer)
        self.assertNotIn('repo / "target/release/bundle/appimage"', producer)

    def test_all_cargo_original_paths_are_retained_before_native_without_private_quota_growth(self):
        import ast
        def upload_paths(body, label):
            selected = body.split("      - name: " + label + "\n", 1)[1]
            selected = selected.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
            return {line.strip() for line in selected.splitlines() if line.strip()}
        self.assertEqual(
            upload_paths(self.installed, "Preserve original installed Linux dependency records"),
            upload_paths(self.bundle, "Preserve original isolated Linux dependency records"))
        self.assertTrue({
            "ci-evidence/linux-cargo/tauri-inputs/*.json",
            "ci-evidence/linux-cargo/tauri-inputs/pre/*.original",
            "ci-evidence/linux-cargo/tauri-inputs/post/*.original",
        }.issubset(upload_paths(self.installed, "Preserve original installed Linux dependency records")))
        self.assertLess(self.installed.index("Preserve original installed Linux dependency records"),
                        self.installed.index("Same-job original installed Desktop to Native and back"))
        self.assertIn("always() && github.event_name == 'workflow_dispatch' && "
                      "inputs.verification_scope == 'linux_first_use' && "
                      "steps.installed_candidate_build.outcome != 'skipped'", self.installed)
        custody = ast.parse((ROOT / "tooling/linux_first_use_custody.py").read_text(encoding="utf-8"))
        declarations = {node.targets[0].id: node.value for node in custody.body
                        if isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)}
        self.assertEqual(ast.literal_eval(declarations["MAX_MEMBERS"]), 192)
        retained = ast.literal_eval(declarations["REPOSITORY_FILES"])
        self.assertIn("linux-appimage-prelaunch.json", retained)
        self.assertIn("linux-cargo-binding.json", retained)


class LinuxFrontendLayoutTests(unittest.TestCase):
    def test_explicit_linux_base_rechecks_real_archives_without_mutating_windows_default(self):
        from test_ci_windows_dependency_capture import FrontendInputCustodyTests
        FrontendInputCustodyTests.setUp(self)
        before_base = gate.shared.BASE
        self.directory = self.f.repo / gate.BASE / "frontend"
        self.f.public_copy()
        prior = FrontendInputCustodyTests.prepare_recheck(self)
        result = gate.shared.authenticate_frontend(self.f.repo, self.f.head,
                                                   recheck=True, base=gate.BASE)
        self.assertEqual(result, prior)
        self.assertEqual(gate.shared.BASE, before_base)
        self.assertTrue((self.directory / "after-bundle.json").is_file())
        self.assertFalse((self.f.repo / before_base / "frontend/after-bundle.json").exists())




class TauriInputObservationTests(unittest.TestCase):
    """Inert files and a fake capture writer; no tool, JS, bundle or Native executes."""

    symlink_denied = False

    def setUp(self):
        import tempfile
        self.temporary = tempfile.TemporaryDirectory(prefix="mw-tauri-input-controls-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = "a" * 40
        self.tree = "b" * 40
        self.directory = self.root / "observations"
        self.directory.mkdir()
        (self.root / "apps/desktop").mkdir(parents=True)
        self.studio = self.root / "apps/studio"
        self.studio.mkdir()
        self.input = self.root / "input.js"
        self.input.write_bytes(b"alpha")
        self.tools = self.root / "toolchain"
        self.bin = self.tools / "bin"
        self.bin.mkdir(parents=True)
        for name in ("node", "npm"):
            path = self.bin / name
            path.write_bytes(b"INERT TOOL FIXTURE; NEVER EXECUTED\n")
            path.chmod(0o755)
        npm_package = self.tools / "lib/node_modules/npm/package.json"
        npm_package.parent.mkdir(parents=True)
        npm_package.write_text('{"name":"npm","version":"0.0.0-fixture"}')
        self.environment = {
            "PATH": str(self.bin), "RUSTFLAGS": gate.RUSTFLAGS,
            "PATCHELF": gate.PATCHELF, "NO_STRIP": "1",
        }
        self.cli = self.studio / "node_modules/@tauri-apps/cli"
        self.native = self.studio / "node_modules/@tauri-apps/cli-linux-x64-gnu"
        self.cli.mkdir(parents=True)
        self.native.mkdir()
        (self.studio / "node_modules/.bin").mkdir()
        self.put_json(self.studio / "package.json", {"devDependencies": {"@tauri-apps/cli": "2.12.1"}})
        self.put_json(self.studio / "package-lock.json", {"lockfileVersion": 3, "packages": {
            "node_modules/@tauri-apps/cli": {"version": "2.12.1", "bin": {"tauri": "tauri.js"},
                "optionalDependencies": {"@tauri-apps/cli-linux-x64-gnu": "2.12.1"}},
            "node_modules/@tauri-apps/cli-linux-x64-gnu": {"version": "2.12.1"},
        }})
        self.put_json(self.cli / "package.json", {
            "name": "@tauri-apps/cli", "version": "2.12.1", "bin": {"tauri": "./tauri.js"},
            "main": "main.js"})
        self.put_json(self.native / "package.json", {
            "name": "@tauri-apps/cli-linux-x64-gnu", "version": "2.12.1",
            "main": "fixture.node", "os": ["linux"], "cpu": ["x64"]})
        (self.cli / "tauri.js").write_bytes(b"INERT WRAPPER\n")
        (self.cli / "main.js").write_bytes(b"INERT MAIN A\n")
        (self.cli / "index.js").write_bytes(b"INERT LOADER\n")
        (self.native / "fixture.node").write_bytes(b"INERT NOT A LOADABLE MODULE\n")
        base = self.root / gate.BASE
        base.mkdir(parents=True)
        self.put_json(base / "tauri-resources.json", {"bundle": {"resources": {}}})

    def put_json(self, path, value):
        path.write_bytes(gate.capture.encoded(value))

    def link(self, target, path):
        if type(self).symlink_denied:
            self.skipTest("Filesystem symlink creation denied; no permission or junction fallback")
        try:
            path.symlink_to(target)
        except OSError as error:
            if isinstance(error, PermissionError) or getattr(error, "winerror", None) == 1314:
                type(self).symlink_denied = True
                self.skipTest("Filesystem symlink creation denied; POSIX link controls remain pending")
            raise

    def observer(self, label):
        directory = self.directory / label
        directory.mkdir()
        return gate._CliInputObservation(self.root, self.source, self.tree,
                                         self.environment, directory, label)

    def minimal_collect(self, observer):
        observer.file("inert-input", self.root, self.input, self.input, retain=True, limit=64)
        observer.record["collection_complete"] = True

    def bundle_originals(self, command, cwd, directory, *, code=0):
        request = {"command": command, "cwd": str(cwd), "label": "bundle", "executed_once": True}
        streams = {"stdout": b"INERT CAPTURE FIXTURE\n", "stderr": b""}
        for suffix, raw in streams.items():
            gate.capture.write_new(directory / ("bundle." + suffix), raw)
        gate.capture.write_new(directory / "bundle.request.json", gate.capture.encoded(request))
        complete = {**request, "returncode": code, "errors": [],
                    **{name + "_sha256": gate.capture.sha(raw) for name, raw in streams.items()}}
        gate.capture.write_new(directory / "bundle.completion.json", gate.capture.encoded(complete))
        return complete

    def invoke(self, *, callback=None, failure=None, collect=None):
        binding = ({"tree": self.tree}, {}, {}, {})
        # The new bundle-tool cache has a source-bound real runner parent; the
        # fake runner never fetches or executes a vendor binary in these tests.
        runner = self.root / "runner-temp"
        runner.mkdir(exist_ok=True)
        bundle_environment = {
            **self.environment, "RUNNER_TEMP": str(runner),
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
        }
        expected_home = runner / "motionwright-pinned-tauri-123-1"
        expected_environment = {**bundle_environment, "XDG_CACHE_HOME": str(expected_home)}
        fake_pins = {
            "schema": gate.toolpins.SCHEMA,
            "status": "PINNED_VENDOR_TOOLS_STAGED_BEFORE_TAURI_BUNDLE",
            "tools": [
                {"name": asset["name"], "cache": asset["cache"],
                 "asset_id": asset["asset_id"],
                 "original": {"bytes": asset["bytes"], "sha256": asset["sha256"]},
                 "allowed_post_sha256": asset["sha256"], "post_rule": "INERT_FIXTURE"}
                for asset in gate.toolpins.ASSETS
            ],
            "total_bytes": 0, "claims": {"G3": "STOP"},
        }
        def verified(cache, record, *, post=False):
            self.assertEqual(Path(cache), expected_home)
            self.assertEqual(record["schema"], fake_pins["schema"])
            return {"status": "POST_FIXTURE" if post else "PRE_FIXTURE",
                    "tools": [], "claims": {"G3": "STOP"}}
        def run(command, cwd, directory, label, **kwargs):
            self.assertEqual(label, "bundle")
            self.assertEqual(kwargs, {"env": expected_environment, "timeout": 900})
            complete = self.bundle_originals(command, cwd, directory, code=23 if failure else 0)
            if callback:
                callback()
            if failure is not None:
                raise failure
            return complete
        chosen = collect if collect is not None else self.minimal_collect
        with mock.patch.object(gate, "cargo_binding", return_value=binding), \
             mock.patch.object(gate.shared, "resource_mapping", return_value={}), \
             mock.patch.object(gate.native_notices, "mapping", return_value={}), \
             mock.patch.object(gate._CliInputObservation, "collect", side_effect=chosen, autospec=True), \
             mock.patch.object(gate.toolpins, "stage", return_value=fake_pins), \
             mock.patch.object(gate.toolpins, "verify_cached", side_effect=verified), \
             mock.patch.object(gate.capture, "run_captured", side_effect=run) as process:
            try:
                return gate.bundle_packages(self.root, self.source, bundle_environment)
            finally:
                self.process_calls = process.call_args_list

    def sidecar(self, name):
        return json.loads((self.root / gate.CLI_INPUTS / name).read_bytes())

    def test_small_original_bytes_and_all_file_identity_fields_are_retained(self):
        observer = self.observer("pre")
        raw = observer.file("input", self.root, self.input, self.input, retain=True, limit=64)
        item = observer.record["roles"][0]
        self.assertEqual(raw, b"alpha")
        self.assertEqual(item["sha256"], gate.capture.sha(raw))
        self.assertEqual((self.root / item["retained_path"]).read_bytes(), raw)
        self.assertEqual(item["file_before"], item["file_after"])
        self.assertEqual(item["file_before"], item["path_before"]["nodes"][-1]["stat"])
        self.assertEqual(item["file_before"], item["endpoint_admission"]["nodes"][-1]["stat"])
        self.assertIn("st_ctime_ns", item["file_before"])
        self.assertIn("st_size", item["path_before"]["nodes"][0]["stat"])

    def test_same_size_content_drift_with_restored_mtime_is_not_equivalent(self):
        first = self.observer("pre")
        first.file("input", self.root, self.input, self.input, retain=True, limit=64)
        info = self.input.stat()
        self.input.write_bytes(b"bravo")
        os.utime(self.input, ns=(info.st_atime_ns, info.st_mtime_ns))
        second = self.observer("post")
        second.file("input", self.root, self.input, self.input, retain=True, limit=64)
        self.assertNotEqual(gate._cli_projection(first.record["roles"]),
                            gate._cli_projection(second.record["roles"]))

    def test_equal_bytes_at_a_new_inode_are_not_equivalent(self):
        first = self.observer("pre")
        first.file("input", self.root, self.input, self.input, retain=True, limit=64)
        original = self.input.stat()
        replacement = self.root / "replacement.js"
        replacement.write_bytes(b"alpha")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        replacement.replace(self.input)
        second = self.observer("post")
        second.file("input", self.root, self.input, self.input, retain=True, limit=64)
        self.assertNotEqual(first.record["roles"][0]["file_before"]["st_ino"],
                            second.record["roles"][0]["file_before"]["st_ino"])
        self.assertNotEqual(gate._cli_projection(first.record["roles"]),
                            gate._cli_projection(second.record["roles"]))

    def test_endpoint_trace_must_bind_to_the_file_selected_for_reading(self):
        observer = self.observer("pre")
        replacement = self.root / "replacement.js"
        replacement.write_bytes(b"bravo")
        saved = self.root / "saved.js"
        original_admitted = gate.capture.admitted
        changed = False
        def admitted(root, path, **kwargs):
            nonlocal changed
            if Path(path) == self.input and not changed:
                changed = True
                self.input.rename(saved)
                replacement.rename(self.input)
            return original_admitted(root, path, **kwargs)
        with mock.patch.object(gate.capture, "admitted", side_effect=admitted):
            with self.assertRaisesRegex(ValueError, "endpoint_identity_disagrees"):
                observer.file("input", self.root, self.input, self.input, retain=True, limit=64)
        self.assertFalse((observer.directory / "input.original").exists())

    def test_per_file_budget_keeps_partial_originals_and_the_actual_error(self):
        original = ValueError("original admission failure")
        def collect(observer):
            self.minimal_collect(observer)
            observer.record["collection_complete"] = False
            raise original
        with mock.patch.object(gate._CliInputObservation, "collect", side_effect=collect, autospec=True):
            record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                     self.directory, "pre")
        self.assertIs(record["_collection_exception"], original)
        self.assertFalse(record["collection_complete"])
        self.assertEqual((self.directory / "pre/inert-input.original").read_bytes(), b"alpha")
        index = json.loads((self.directory / "pre.json").read_bytes())
        self.assertEqual(index["error"]["message"], "original admission failure")

    def test_small_read_and_retention_budgets_are_enforced_before_publication(self):
        observer = self.observer("pre")
        with self.assertRaisesRegex(gate.capture.CaptureError, "file_budget_exceeded"):
            observer.file("too-large", self.root, self.input, self.input, retain=True, limit=4)
        self.assertFalse((observer.directory / "too-large.original").exists())
        with mock.patch.object(gate, "CLI_MAX_RETAINED", 5):
            observer.file("first", self.root, self.input, self.input, retain=True, limit=64)
            with self.assertRaisesRegex(gate.capture.CaptureError, "file_budget_exceeded"):
                observer.file("second", self.root, self.input, self.input, retain=True, limit=64)
        self.assertEqual((observer.directory / "first.original").read_bytes(), b"alpha")
        self.assertFalse((observer.directory / "second.original").exists())

    def test_hash_budget_is_bounded_without_retaining_binary_bytes(self):
        observer = self.observer("pre")
        with mock.patch.object(gate, "CLI_MAX_HASHED", 4):
            with self.assertRaisesRegex(gate.capture.CaptureError, "artifact_budget_exceeded"):
                observer.file("native", self.root, self.input, self.input, retain=False, limit=128)
        self.assertEqual(list(observer.directory.iterdir()), [])

    def test_metadata_budget_is_explicit_and_original_files_survive(self):
        def collect(observer):
            self.minimal_collect(observer)
            observer.record["tool_search"] = {"oversized": "x" * 10000}
        with mock.patch.object(gate._CliInputObservation, "collect", side_effect=collect, autospec=True), \
             mock.patch.object(gate, "CLI_MAX_RECORD", 6000):
            record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                     self.directory, "pre")
        self.assertFalse(record["collection_complete"])
        self.assertEqual(str(record["_collection_exception"]), "tauri_input_record_budget")
        self.assertEqual((self.directory / "pre/inert-input.original").read_bytes(), b"alpha")
        self.assertLessEqual((self.directory / "pre.json").stat().st_size, 6000)

    def test_bad_json_keeps_the_original_lock_without_executing_tools(self):
        (self.studio / "package-lock.json").write_bytes(b'{"lockfileVersion":3,"lockfileVersion":3}')
        record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                 self.directory, "pre")
        self.assertFalse(record["collection_complete"])
        self.assertIn("duplicate_json_key", record["error"]["message"])
        self.assertEqual((self.directory / "pre/studio-lock.original").read_bytes(),
                         (self.studio / "package-lock.json").read_bytes())

    def test_crossed_native_version_and_escaping_main_refuse_before_binary_read(self):
        for number, change in enumerate(({"version": "9.9.9"}, {"main": "../outside.node"})):
            path = self.native / "package.json"
            data = json.loads(path.read_bytes())
            data.update(version="2.12.1", main="fixture.node")
            data.update(change)
            self.put_json(path, data)
            record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                     self.directory, "pre" + str(number))
            self.assertFalse(record["collection_complete"])
            self.assertNotIn("native-module", {item["role"] for item in record["roles"]})

    def test_ambiguous_parent_components_are_rejected_without_collapsing_them(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_parent_component"):
            gate._cli_target(self.root, self.root, "redirect/../input.js")
        with self.assertRaisesRegex(ValueError, "ambiguous_parent_component"):
            gate._cli_lexical(self.root, self.root / "redirect/../input.js")
        observer = self.observer("pre")
        observer.record["path_literal"] = str(self.root / "redirect/../toolchain/bin")
        with self.assertRaisesRegex(ValueError, "ambiguous_parent_component"):
            observer.tool("node")

    def test_anchor_components_consume_the_declared_trace_budget(self):
        record = {}
        with mock.patch.object(gate, "CLI_MAX_PARTS", len(self.root.parts) - 1):
            with self.assertRaisesRegex(ValueError, "anchor_invalid_or_budget"):
                gate._cli_trace(self.root, self.input, record, links=True)
        self.assertNotIn("nodes", record)

    def test_path_priority_and_absence_are_recorded_without_running_node(self):
        earlier = self.root / "earlier/bin"
        earlier.mkdir(parents=True)
        observer = self.observer("pre")
        observer.record["path_literal"] = os.pathsep.join((str(earlier), str(self.bin)))
        with mock.patch.object(gate.capture.subprocess, "Popen", side_effect=AssertionError("no process")):
            observer.tool("node")
        search = observer.record["tool_search"]["node"]
        self.assertEqual(search["candidates"][0]["status"], "ABSENT")
        self.assertEqual(search["selection"], str(self.bin / "node"))
        earlier_node = earlier / "node"
        earlier_node.write_bytes(b"INERT EARLIER TOOL")
        earlier_node.chmod(0o755)
        second = self.observer("post")
        second.record["path_literal"] = observer.record["path_literal"]
        second.tool("node")
        self.assertNotEqual(gate._cli_projection(search),
                            gate._cli_projection(second.record["tool_search"]["node"]))

    def test_relative_symlink_chain_is_preserved_and_endpoint_admitted_independently(self):
        links = self.root / "links"
        links.mkdir()
        first, second = links / "first", links / "second"
        self.link("second", first)
        self.link("../input.js", second)
        observer = self.observer("pre")
        with self.assertRaisesRegex(gate.capture.CaptureError, "redirected_path"):
            gate.capture.stable_read(self.root, first)
        observer.file("wrapper", self.root, first, self.input, retain=True, limit=64)
        nodes = observer.record["roles"][0]["path_before"]["nodes"]
        self.assertEqual([item["target_literal"] for item in nodes if item["kind"] == "link"],
                         ["second", "../input.js"])

    def test_linked_wrong_endpoint_is_rejected_even_when_bytes_match(self):
        wrong = self.root / "wrong.js"
        wrong.write_bytes(b"alpha")
        alias = self.root / "alias"
        self.link("wrong.js", alias)
        observer = self.observer("pre")
        with self.assertRaisesRegex(ValueError, "endpoint_unexpected"):
            observer.file("wrapper", self.root, alias, self.input, retain=True, limit=64)
        self.assertFalse((observer.directory / "wrapper.original").exists())

    def test_cycle_escape_and_link_depth_refuse_with_literal_partials(self):
        first, second = self.root / "first", self.root / "second"
        self.link("second", first)
        self.link("first", second)
        trace = {}
        with self.assertRaisesRegex(ValueError, "link_cycle_or_budget"):
            gate._cli_trace(self.root, first, trace, links=True)
        self.assertTrue(any(item.get("target_literal") == "second" for item in trace["nodes"]))
        first.unlink()
        self.link("../outside", first)
        with self.assertRaisesRegex(ValueError, "link_escape"):
            gate._cli_trace(self.root, first, {}, links=True)
        first.unlink()
        second.unlink()
        self.link("second", first)
        self.link("input.js", second)
        with mock.patch.object(gate, "CLI_MAX_LINKS", 1):
            with self.assertRaisesRegex(ValueError, "link_cycle_or_budget"):
                gate._cli_trace(self.root, first, {}, links=True)

    def test_link_change_during_readlink_is_refused(self):
        alias = self.root / "alias"
        self.link("input.js", alias)
        other = self.root / "other.js"
        other.write_bytes(b"alpha")
        original = gate.os.readlink
        changed = False
        def readlink(path):
            nonlocal changed
            value = original(path)
            if Path(path) == alias and not changed:
                changed = True
                alias.unlink()
                alias.symlink_to("other.js")
            return value
        with mock.patch.object(gate.os, "readlink", side_effect=readlink):
            with self.assertRaisesRegex(ValueError, "link_changed"):
                gate._cli_trace(self.root, alias, {}, links=True)

    def test_full_static_roster_does_not_claim_native_load_or_archive_authentication(self):
        self.link("../@tauri-apps/cli/tauri.js", self.studio / "node_modules/.bin/tauri")
        record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                 self.directory, "pre")
        self.assertTrue(record["collection_complete"], record.get("error"))
        self.assertEqual(len(record["roles"]), 12)
        self.assertEqual(len({item["role"] for item in record["roles"]}), 12)
        self.assertEqual(record["declared_package_relation"]["cli_main"], "main.js")
        self.assertEqual((self.directory / "pre/cli-main.original").read_bytes(),
                         (self.cli / "main.js").read_bytes())
        for key, value in gate.CLI_CLAIMS.items():
            self.assertEqual(record[key], value)
        self.assertFalse((self.directory / "pre/native-module.original").exists())
        self.assertFalse((self.directory / "pre/node.original").exists())


    def test_cli_main_is_retained_and_missing_bridge_refuses_before_bundle(self):
        # The absent launcher deliberately stops this inert fixture after main;
        # neither the incomplete collection nor this control claims a loaded module.
        record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                 self.directory, "bridge-present")
        self.assertFalse(record["collection_complete"])
        main = next(row for row in record["roles"] if row["role"] == "cli-main")
        self.assertEqual(main["status"], "OBSERVED")
        self.assertEqual(main["sha256"], gate.capture.sha(b"INERT MAIN A\n"))
        self.assertEqual(record["declared_package_relation"]["cli_main"], "main.js")
        self.assertEqual((self.directory / "bridge-present/cli-main.original").read_bytes(),
                         b"INERT MAIN A\n")
        self.assertEqual(record["roles"][-1]["role"], "tauri-launcher")
        (self.cli / "main.js").unlink()
        with self.assertRaises(FileNotFoundError):
            self.invoke(collect=gate._CliInputObservation.collect)
        self.assertEqual(self.process_calls, [])
        pre = self.sidecar("pre.json")
        self.assertFalse(pre["collection_complete"])
        self.assertEqual(pre["roles"][-1]["role"], "cli-main")
        self.assertEqual(pre["roles"][-1]["error"]["type"], "FileNotFoundError")
        self.assertTrue((self.root / gate.CLI_INPUTS / "pre/cli-package.original").is_file())
        self.assertFalse((self.root / gate.CLI_INPUTS / "pre/cli-main.original").exists())

    def test_cli_main_manifest_relation_is_required_before_bundle(self):
        path = self.cli / "package.json"
        original = json.loads(path.read_bytes())
        for index, value in enumerate((None, "index.js", "../main.js")):
            with self.subTest(main=value):
                altered = dict(original)
                if value is None:
                    altered.pop("main")
                else:
                    altered["main"] = value
                self.put_json(path, altered)
                record = gate._cli_phase(self.root, self.source, self.tree, self.environment,
                                         self.directory, "manifest-" + str(index))
                self.assertFalse(record["collection_complete"])
                self.assertEqual(record["error"]["message"], "tauri_input_installed_metadata_mismatch")
                self.assertNotIn("cli-main", [row["role"] for row in record["roles"]])
        with self.assertRaisesRegex(ValueError, "tauri_input_installed_metadata_mismatch"):
            self.invoke(collect=gate._CliInputObservation.collect)
        self.assertEqual(self.process_calls, [])

    def test_cli_main_drift_during_pre_read_refuses_before_bundle(self):
        path = self.cli / "main.js"
        before = path.stat()
        original_reader = gate._cli_expected_read
        changed = False
        def change_after_read(anchor, target, *args, **kwargs):
            nonlocal changed
            raw = original_reader(anchor, target, *args, **kwargs)
            if target == path and not changed:
                changed = True
                path.write_bytes(b"INERT MAIN B\n")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
            return raw
        with mock.patch.object(gate, "_cli_expected_read", side_effect=change_after_read):
            with self.assertRaisesRegex(ValueError, "tauri_input_changed_during_observation"):
                self.invoke(collect=gate._CliInputObservation.collect)
        self.assertTrue(changed)
        self.assertEqual(self.process_calls, [])
        self.assertEqual((self.root / gate.CLI_INPUTS / "pre/cli-main.original").read_bytes(),
                         b"INERT MAIN A\n")
        self.assertEqual((self.root / gate.CLI_INPUTS / "post/cli-main.original").read_bytes(),
                         b"INERT MAIN B\n")
        self.assertFalse(self.sidecar("comparison.json")["observations_match"])

    def test_one_unchanged_bundle_command_and_bound_post_originals(self):
        self.invoke()
        self.assertEqual(len(self.process_calls), 1)
        call = self.process_calls[0]
        self.assertEqual(call.args[:2], (
            ["npm", "--prefix", "../studio", "exec", "--", "tauri", "bundle", "--target",
             gate.TARGET, "--features", "tauri/custom-protocol", "--bundles", "deb,appimage",
             "--config", "../../ci-evidence/linux-cargo/tauri-resources.json",
             "--no-binary-patching", "--no-sign", "--ci"],
            self.root / "apps/desktop"))
        pinned_pre = json.loads(
            (self.root / gate.BASE / "tauri-tool-pins.pre.json").read_bytes())
        pinned_post = json.loads(
            (self.root / gate.BASE / "tauri-tool-pins.post.json").read_bytes())
        self.assertEqual(pinned_pre["source"], self.source)
        self.assertEqual(pinned_pre["source_tree"], self.tree)
        self.assertEqual((pinned_pre["run_id"], pinned_pre["run_attempt"]), ("123", "1"))
        self.assertEqual(pinned_pre["pre_bundle"]["status"], "PRE_FIXTURE")
        self.assertEqual(pinned_pre["claims"]["G3"], "STOP")
        self.assertEqual(pinned_post["source"], self.source)
        self.assertEqual(pinned_post["status"], "POST_FIXTURE")
        self.assertEqual(pinned_post["claims"]["G3"], "STOP")
        self.assertEqual(pinned_post["pre_receipt_sha256"],
                         gate.capture.sha((self.root / gate.BASE /
                                          "tauri-tool-pins.pre.json").read_bytes()))
        comparison = self.sidecar("comparison.json")
        self.assertTrue(comparison["observations_match"])
        self.assertTrue(comparison["bundle_binding_complete"])
        self.assertIsNone(comparison["primary_error"])
        self.assertTrue(all(item["status"] == "OBSERVED"
                            for item in self.sidecar("bundle-records.json")["files"].values()))

    def test_bundle_primary_survives_post_drift_with_both_original_inputs(self):
        original = RuntimeError("original inert bundle failure")
        with self.assertRaises(RuntimeError) as raised:
            self.invoke(failure=original, callback=lambda: self.input.write_bytes(b"bravo"))
        self.assertIs(raised.exception, original)
        self.assertEqual(len(self.process_calls), 1)
        comparison = self.sidecar("comparison.json")
        self.assertFalse(comparison["observations_match"])
        self.assertEqual(comparison["primary_error"]["message"], str(original))
        directory = self.root / gate.CLI_INPUTS
        self.assertEqual((directory / "pre/inert-input.original").read_bytes(), b"alpha")
        self.assertEqual((directory / "post/inert-input.original").read_bytes(), b"bravo")

    def test_post_retention_failure_keeps_bundle_primary_and_partial_originals(self):
        original = RuntimeError("primary bundle")
        write = gate.capture.write_new
        def fail(path, raw):
            if Path(path).name == "post.json":
                raise OSError("post retention refused")
            return write(path, raw)
        with mock.patch.object(gate.capture, "write_new", side_effect=fail):
            with self.assertRaises(RuntimeError) as raised:
                self.invoke(failure=original)
        self.assertIs(raised.exception, original)
        failure = self.sidecar("post-failure.json")
        self.assertEqual(failure["post_error"]["message"], "post retention refused")
        self.assertEqual(failure["primary_error"]["message"], "primary bundle")
        self.assertTrue((self.root / gate.CLI_INPUTS / "bundle-records.json").is_file())
        self.assertTrue((self.root / gate.CLI_INPUTS / "post/inert-input.original").is_file())

    def test_pre_exception_object_survives_and_absent_bundle_records_are_explicit(self):
        original = PermissionError("inert input admission denied")
        calls = 0
        def collect(observer):
            nonlocal calls
            calls += 1
            self.minimal_collect(observer)
            observer.record["collection_complete"] = False
            raise original if calls == 1 else ValueError("post input failure")
        with self.assertRaises(PermissionError) as raised:
            self.invoke(collect=collect)
        self.assertIs(raised.exception, original)
        self.assertEqual(self.process_calls, [])
        self.assertTrue(all(item["status"] == "ABSENT"
                            for item in self.sidecar("bundle-records.json")["files"].values()))
        self.assertEqual(self.sidecar("pre.json")["error"]["type"], "PermissionError")

    def test_successful_bundle_cannot_hide_same_size_input_drift(self):
        before = self.input.stat()
        def mutate():
            self.input.write_bytes(b"bravo")
            os.utime(self.input, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "pre_post_changed_or_incomplete"):
            self.invoke(callback=mutate)
        self.assertFalse(self.sidecar("comparison.json")["observations_match"])

    def test_phase_json_tampering_cannot_be_linked_as_original(self):
        def mutate():
            path = self.root / gate.CLI_INPUTS / "pre.json"
            path.write_bytes(path.read_bytes().replace(b'"phase": "pre"', b'"phase": "bad"'))
        with self.assertRaisesRegex(ValueError, "phase_record_changed"):
            self.invoke(callback=mutate)
        self.assertFalse((self.root / gate.CLI_INPUTS / "comparison.json").exists())
        self.assertEqual(self.sidecar("post-failure.json")["post_error"]["message"],
                         "tauri_input_phase_record_changed")

    def test_retained_small_original_mutation_is_refused(self):
        def mutate():
            (self.root / gate.CLI_INPUTS / "pre/inert-input.original").write_bytes(b"bravo")
        with self.assertRaisesRegex(ValueError, "retained_original_changed"):
            self.invoke(callback=mutate)
        self.assertFalse((self.root / gate.CLI_INPUTS / "comparison.json").exists())

    def test_replay_and_existing_sidecar_are_refused_without_process(self):
        path = self.root / gate.BASE / "bundle.stdout"
        path.write_bytes(b"existing original")
        with self.assertRaisesRegex(ValueError, "bundle_replay_refused"):
            self.invoke()
        self.assertEqual(self.process_calls, [])
        self.assertEqual(path.read_bytes(), b"existing original")
        path.unlink()
        (self.root / gate.CLI_INPUTS).mkdir()
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(self.process_calls, [])

    def test_missing_unclosed_or_mismatched_bundle_records_are_not_authenticated(self):
        command = ["INERT", "fixture"]
        initial = gate._cli_bundle_records(self.root, command)
        self.assertFalse(initial["binding_complete"])
        self.assertTrue(all(item["status"] == "ABSENT" for item in initial["files"].values()))
        base = self.root / gate.BASE
        complete = self.bundle_originals(command, self.root / "apps/desktop", base)
        self.assertTrue(gate._cli_bundle_records(self.root, command)["binding_complete"])
        complete["stdout_final"] = False
        self.put_json(base / "bundle.completion.json", complete)
        self.assertFalse(gate._cli_bundle_records(self.root, command)["binding_complete"])
        complete.pop("stdout_final")
        self.put_json(base / "bundle.completion.json", complete)
        (base / "bundle.stdout").write_bytes(b"different inert stream")
        self.assertFalse(gate._cli_bundle_records(self.root, command)["binding_complete"])

    def test_observer_adds_no_process_or_js_execution_and_keeps_timeout(self):
        import ast
        tree = ast.parse((ROOT / "tooling/ci_linux_dependency_capture.py").read_bytes())
        observers = [node for node in tree.body if
                     isinstance(node, (ast.FunctionDef, ast.ClassDef)) and
                     (node.name.startswith("_cli_") or node.name == "_CliInputObservation")]
        forbidden = {"Popen", "run_captured", "run", "system", "exec", "eval", "resolve", "normpath"}
        for node in observers:
            calls = [item.func for item in ast.walk(node) if isinstance(item, ast.Call)]
            names = {func.attr if isinstance(func, ast.Attribute) else func.id
                     for func in calls if isinstance(func, (ast.Attribute, ast.Name))}
            self.assertFalse(names & forbidden, node.name)
        bundle = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "bundle_packages")
        calls = [node for node in ast.walk(bundle) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "run_captured"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(next(ast.literal_eval(value.value) for value in calls[0].keywords
                              if value.arg == "timeout"), 900)


    def test_intent_tampering_is_refused_before_post_publication(self):
        def mutate():
            path = self.root / gate.CLI_INPUTS / "intent.json"
            path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "tauri_input_intent_changed"):
            self.invoke(callback=mutate)
        self.assertFalse((self.root / gate.CLI_INPUTS / "bundle-records.json").exists())

    def test_pre_retention_error_cannot_replace_the_original_admission_error(self):
        original = PermissionError("original pre admission")
        calls = 0
        def collect(observer):
            nonlocal calls
            calls += 1
            self.minimal_collect(observer)
            raise original if calls == 1 else ValueError("post admission")
        write = gate.capture.write_new
        def fail(path, raw):
            if Path(path).name == "pre.json":
                raise OSError("pre retention failure")
            return write(path, raw)
        with mock.patch.object(gate.capture, "write_new", side_effect=fail):
            with self.assertRaises(PermissionError) as raised:
                self.invoke(collect=collect)
        self.assertIs(raised.exception, original)
        self.assertEqual(self.process_calls, [])
        self.assertTrue(any("Phase retention also failed" in note for note in original.__notes__))
        self.assertEqual((self.root / gate.CLI_INPUTS / "pre/inert-input.original").read_bytes(), b"alpha")
        self.assertEqual(self.sidecar("comparison.json")["phase_records"]["pre"]["status"], "ABSENT")

    def test_redirected_output_ancestor_cannot_receive_post_sidecars(self):
        if type(self).symlink_denied:
            self.skipTest("Filesystem symlink creation denied; no permission or junction fallback")
        moved = self.root / "moved-cargo"
        original = RuntimeError("inert bundle failure")
        def redirect():
            base = self.root / gate.BASE
            base.rename(moved)
            self.link(str(moved), base)
        with self.assertRaises(RuntimeError) as raised:
            self.invoke(failure=original, callback=redirect)
        self.assertIs(raised.exception, original)
        self.assertFalse((moved / "tauri-inputs/bundle-records.json").exists())
        self.assertFalse((moved / "tauri-inputs/comparison.json").exists())
        self.assertEqual((moved / "tauri-inputs/pre/inert-input.original").read_bytes(), b"alpha")


    def test_actual_open_handle_is_bound_to_the_expected_file_identity(self):
        if os.name == "nt":
            self.skipTest("Windows refused rename of an open file (WinError 32); POSIX ABA control pending")
        observer = self.observer("pre")
        replacement = self.root / "replacement.js"
        held = self.root / "held.js"
        replacement.write_bytes(b"bravo")
        opened = gate.os.open
        switched = False
        def swap(path, flags, *args, **kwargs):
            nonlocal switched
            if Path(path) == self.input and not switched:
                switched = True
                self.input.rename(held)
                replacement.rename(self.input)
                descriptor = opened(path, flags, *args, **kwargs)
                try:
                    self.input.rename(replacement)
                    held.rename(self.input)
                except BaseException:
                    os.close(descriptor)
                    raise
                return descriptor
            return opened(path, flags, *args, **kwargs)
        with mock.patch.object(gate.os, "open", side_effect=swap):
            with self.assertRaisesRegex(ValueError, "handle_identity_disagrees"):
                observer.file("input", self.root, self.input, self.input, retain=True, limit=64)
        item = observer.record["roles"][0]
        self.assertTrue(item["reader"]["descriptor_closed"])
        self.assertEqual(self.input.read_bytes(), b"alpha")
        self.assertFalse((observer.directory / "input.original").exists())

    def test_post_collect_error_survives_a_second_retained_original_failure(self):
        original = PermissionError("original post observation")
        calls = 0
        def collect(observer):
            nonlocal calls
            calls += 1
            self.minimal_collect(observer)
            if calls == 2:
                raise original
        def mutate():
            (self.root / gate.CLI_INPUTS / "pre/inert-input.original").write_bytes(b"bravo")
        with self.assertRaises(PermissionError) as raised:
            self.invoke(collect=collect, callback=mutate)
        self.assertIs(raised.exception, original)
        self.assertTrue(any("Post readback/retention also failed" in note for note in original.__notes__))
        failure = self.sidecar("post-failure.json")
        self.assertIsNone(failure["primary_error"])
        self.assertEqual(failure["post_error"]["message"], "original post observation")


    def test_foreign_inert_descriptor_is_rejected_and_closed_without_renaming(self):
        observer = self.observer("pre")
        replacement = self.root / "foreign.js"
        replacement.write_bytes(b"bravo")
        opened = gate.os.open
        def wrong_descriptor(path, flags, *args, **kwargs):
            selected = replacement if Path(path) == self.input else path
            return opened(selected, flags, *args, **kwargs)
        with mock.patch.object(gate.os, "open", side_effect=wrong_descriptor):
            with self.assertRaisesRegex(ValueError, "handle_identity_disagrees"):
                observer.file("input", self.root, self.input, self.input, retain=True, limit=64)
        self.assertTrue(observer.record["roles"][0]["reader"]["descriptor_closed"])
        self.assertEqual(self.input.read_bytes(), b"alpha")
        self.assertFalse((observer.directory / "input.original").exists())




if __name__ == "__main__":
    unittest.main()
