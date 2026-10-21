"""Synthetic producer controls. No installer, token API or native UI runs here."""
from __future__ import annotations
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installer_producer as producer
from test_windows_installer_lifecycle import Fixture

nsis = producer.nsis
life = producer.life


def token():
    return {"elevated": False, "integrity_rid": 0x2000,
            "user_sid": "S-1-5-21-101-202-303-1001", "session_id": 1}


class ProducerControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-nsis-producer-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)
        self.exe = life.bound_file(self.root, self.fixture.new_setup)
        self.process = mock.Mock()
        self.process.pid = 123
        self.process._handle = 456
        self.process.wait.return_value = 0
        self.process.poll.return_value = None
        self.identity = {"pid": 123, "creation_filetime": 134000000000000001}

    def launch(self, **kwargs):
        with mock.patch.object(producer.subprocess, "Popen", return_value=self.process) as popen, \
             mock.patch.object(producer, "process_identity", return_value=self.identity):
            result = producer.launch_once_and_observe(self.exe, "fixed command", {}, **kwargs)
        self.assertEqual(popen.call_count, 1)
        return result

    def state_fixture(self):
        f = self.fixture
        oid = str(uuid.uuid4())
        before = f.snapshot(oid, "before", {**f.old_files, "foreign.txt": f.foreign},
                            f.registry("0.1.0"), project=True)
        checkpoint = {"stage": "mw-stage-{ABCDEF12-1234-4234-8234-123456789ABC}\\payload.bin"}
        stage = checkpoint["stage"].replace("\\", "/")
        after = copy.deepcopy(before)
        after["installed"].pop("motionwright-desktop.exe")
        after["installed"][stage] = f.new_main
        return before, after, checkpoint, stage

    def test_legacy_literal_pins_survive_extraction(self):
        source = Path(producer.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        fixed = next(x for x in tree.body if isinstance(x, ast.Assign)
                     and any(isinstance(n, ast.Name) and n.id == "FIXED_PREDECESSOR" for n in x.targets))
        value = json.loads(ast.literal_eval(fixed.value.args[0]))
        self.assertEqual(value["run"], 39032220978)
        self.assertEqual(value["source"], "679cc48baf7c6f9bb4a0b803508c9b2a9891d944")
        self.assertEqual(value["packageArtifact"]["id"], 12101032310)
        self.assertEqual(value["evidenceArtifact"]["id"], 12101216495)
        self.assertEqual(value["package"]["sha256"],
                         "c071870c1c5f12f1728389f526471bee43b23eb7ad0bae3410147349a8da9f46")
        self.assertIn("FAIL_PRESERVED_AS_HISTORICAL_RESULT", source)

    def test_legacy_three_operations_are_still_the_same_closed_sequence(self):
        tree = ast.parse(Path(producer.__file__).read_text(encoding="utf-8"))
        fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "run_legacy_lifecycle")
        calls = [x for x in ast.walk(fn) if isinstance(x, ast.Call)
                 and isinstance(x.func, ast.Name) and x.func.id == "observe_phase"]
        self.assertEqual([ast.literal_eval(x.args[0]) for x in calls], ["install", "upgrade", "uninstall"])
        self.assertEqual([ast.literal_eval(x.args[2]) for x in calls],
                         ["nsis_install_previous", "nsis_upgrade_current", "nsis_uninstall_current_in_place"])
        workflow = (Path(producer.__file__).parents[1] / ".github/workflows/package-candidate.yml").read_text()
        self.assertEqual(workflow.count("tooling/windows_installer_producer.py prepare-legacy"), 1)
        self.assertEqual(workflow.count("tooling/windows_installer_producer.py legacy-lifecycle"), 1)
        self.assertNotIn("def observe_phase", workflow)
        self.assertNotIn("def fetch_artifact", workflow)

    def test_launch_ack_is_original_and_occurs_once(self):
        result = self.launch()
        self.assertEqual(result["original_terminal"], life.observed_terminal(0))
        self.assertEqual(result["process"], self.identity)
        self.assertEqual(result["custody_errors"], [])

    def test_launch_opaque_oserror_stays_unknown_without_retry(self):
        with mock.patch.object(producer.subprocess, "Popen", side_effect=OSError("opaque")) as popen:
            result = producer.launch_once_and_observe(self.exe, "one command", {})
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(result["original_terminal"], life.observed_terminal(None))
        self.assertIsNone(result["process"])
        self.assertEqual(result["custody_errors"][0]["stage"], "installer_launch")

    def test_launch_timeout_stays_unknown_without_kill(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        result = self.launch()
        self.assertEqual(result["original_terminal"], life.observed_terminal(None, timed_out=True))
        self.process.kill.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_original_typed_nonzero_is_preserved(self):
        for code in (2, 3, 4, 99):
            self.process.wait.return_value = code
            with self.subTest(code=code):
                self.assertEqual(self.launch()["original_terminal"], life.observed_terminal(code))

    def test_actual_child_token_mismatch_blocks_live_callback(self):
        observer = mock.Mock()
        with mock.patch.object(producer, "observe_non_elevated_token",
                               return_value={**token(), "session_id": 2}):
            result = self.launch(token=token(), live_observer=observer)
        observer.assert_not_called()
        self.assertEqual(result["original_terminal"], life.observed_terminal(0))
        self.assertTrue(result["custody_errors"])

    def test_preflight_elevated_unknown_or_noninteractive_tokens_refused(self):
        self.assertEqual(producer.admit_token(token()), token())
        for change in ({"elevated": True}, {"elevated": None}, {"integrity_rid": 0x3000},
                       {"session_id": 0}, {"user_sid": "unknown"}, {"integrity_rid": True}):
            with self.subTest(change=change), self.assertRaises(nsis.ContractError):
                producer.admit_token({**token(), **change})

    def test_current_default_install_has_no_destination_override(self):
        self.assertEqual(producer.installer_arguments("default"), ["/S"])
        self.assertEqual(producer.command_line(self.exe, ["/S"]),
                         subprocess.list2cmdline([str(self.exe), "/S"]))
        with self.assertRaises(nsis.ContractError):
            producer.installer_arguments("default", self.root)
        self.assertTrue(producer.installer_arguments("recover", self.root)[-1].startswith("/D="))
        self.assertTrue(producer.installer_arguments("uninstall", self.root)[-1].startswith("_?="))

    def test_unknown_scopes_and_arbitrary_paths_are_rejected(self):
        for argv in (["unknown"], ["physical", "--case", "foreign"],
                     ["physical", "--case", "default-uninstall", "--input", str(self.root)],
                     ["prepare-legacy", "--product-sha", "a" * 40]):
            with self.subTest(argv=argv), mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                producer.main(argv)

    def test_physical_scope_requires_explicit_hosted_first_attempt(self):
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "MW_NSIS_VERIFICATION_SCOPE": "physical",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "example/motionwright"}
        with mock.patch.object(producer.os, "name", "nt"):
            self.assertEqual(producer.physical_scope(environment, "default-uninstall")["run_id"], 42)
            for change in ({"GITHUB_EVENT_NAME": "push"}, {"GITHUB_RUN_ATTEMPT": "2"},
                           {"RUNNER_ENVIRONMENT": "self-hosted"}, {"MW_NSIS_VERIFICATION_SCOPE": "compile"}):
                with self.subTest(change=change), self.assertRaises(nsis.ContractError):
                    producer.physical_scope({**environment, **change}, "default-uninstall")

    def test_full_profile_family_uses_canonical_helper(self):
        f = self.fixture
        path = life.bound_file(self.root, f.profile_ref)
        result = producer.admit_profile_family(path, f.profile_ref["sha256"], f.ident(f.new_main),
                                                self.root / "real-canonical-profile")
        self.assertIs(result["enabled"], True)
        self.assertEqual(result["files"], 2)
        self.assertEqual((self.root / "real-canonical-profile/mw-predecessor-file-0000.capture.bin").read_bytes(),
                         life.bound_file(self.root, f.old_main).read_bytes())

    def test_profile_wrong_arch_missing_sibling_and_digest_fail_before_any_effect(self):
        f = self.fixture
        path = life.bound_file(self.root, f.profile_ref)
        document = copy.deepcopy(f.profile)
        document["identity"]["arch"] = "x86_64"
        bad = f.js("wrong-arch", document)
        with self.assertRaises(Exception):
            producer.admit_profile_family(life.bound_file(self.root, bad), bad["sha256"],
                                            f.ident(f.new_main), self.root / "wrong-arch-probe")
        life.bound_file(self.root, f.old_self).unlink()
        with self.assertRaises(Exception):
            producer.admit_profile_family(path, f.profile_ref["sha256"], f.ident(f.new_main),
                                            self.root / "missing-family-probe")
        with self.assertRaises(nsis.ContractError):
            producer.admit_profile_family(path, "0" * 64, f.ident(f.new_main),
                                            self.root / "wrong-pin-probe")

    def test_interrupted_state_preserves_foreign_database_registry_and_stage(self):
        before, after, checkpoint, stage = self.state_fixture()
        result = producer.admit_interrupted_state(before, after, checkpoint,
            {k: self.fixture.ident(v) for k, v in self.fixture.old_files.items()},
            self.fixture.ident(self.fixture.new_main), self.fixture.context["registry_keys"])
        self.assertEqual(result["state"], "INTERRUPTED_STATE_ADMITTED")
        self.assertEqual(result["stage"], stage)
        self.assertEqual(result["foreign_preserved"], ["foreign.txt"])

    def test_interrupted_partial_wrong_stage_main_and_foreign_fail(self):
        before, after, checkpoint, stage = self.state_fixture()
        changes = []
        for key, value in (("motionwright-desktop.exe", self.fixture.old_main),
                           (stage, self.fixture.old_main), ("foreign.txt", self.fixture.old_main)):
            row = copy.deepcopy(after)
            row["installed"][key] = value
            changes.append(row)
        row = copy.deepcopy(after)
        row["sqlite_family"]["motionwright.sqlite3-wal"] = None
        changes.append(row)
        row = copy.deepcopy(after)
        row["registry"] = self.fixture.registry("0.1.1")
        changes.append(row)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(nsis.ContractError):
                producer.admit_interrupted_state(before, changed, checkpoint,
                    {k: self.fixture.ident(v) for k, v in self.fixture.old_files.items()},
                    self.fixture.ident(self.fixture.new_main), self.fixture.context["registry_keys"])

    def test_recovery_requires_complete_new_payload_and_preserves_foreign_stage(self):
        f = self.fixture
        before, interrupted, checkpoint, stage = self.state_fixture()
        after = copy.deepcopy(before)
        after["installed"] = {**f.new_files, stage: f.new_main}
        after["registry"] = f.registry("0.1.1")
        result = producer.admit_recovered_state(before, interrupted, after,
            {"motionwright-desktop.exe": f.ident(f.new_main)},
            {k: f.ident(v) for k, v in f.old_files.items()}, f.context["registry_keys"], stage)
        self.assertEqual(result["state"], "RECOVERED_STATE_ADMITTED")
        for key in ("foreign.txt", stage):
            changed = copy.deepcopy(after)
            changed["installed"].pop(key)
            with self.subTest(key=key), self.assertRaises(nsis.ContractError):
                producer.admit_recovered_state(before, interrupted, changed,
                    {"motionwright-desktop.exe": f.ident(f.new_main)},
                    {k: f.ident(v) for k, v in f.old_files.items()}, f.context["registry_keys"], stage)

    def test_normal_recovery_clears_interrupt_variable_and_rejects_opt_in_reuse(self):
        f = self.fixture
        normal = life.bound_file(self.root, f.new_setup)
        opt = life.bound_file(self.root, f.old_setup)
        source = {"MOTIONWRIGHT_NSIS_INTERRUPT_ID": "{original}", "APPDATA": "native"}
        result = producer.recovery_environment(source, normal, opt)
        self.assertNotIn("MOTIONWRIGHT_NSIS_INTERRUPT_ID", result)
        self.assertEqual(result["APPDATA"], "native")
        self.assertIn("MOTIONWRIGHT_NSIS_INTERRUPT_ID", source)
        with self.assertRaises(nsis.ContractError):
            producer.recovery_environment(source, normal, normal)

    def test_mixed_uninstall_requires_modified_owned_foreign_and_removed_intact(self):
        f = self.fixture
        changed = f.blob("user-modified-resource", b"deliberate synthetic edit")
        intact = f.blob("intact-resource", b"original synthetic resource")
        original = f.blob("original-resource", b"original alpha")
        current = {"alpha": f.ident(original), "beta": f.ident(intact),
                   "motionwright-desktop.exe": f.ident(f.new_main)}
        before = f.snapshot(str(uuid.uuid4()), "before",
            {"alpha": changed, "beta": intact, **f.new_files}, f.registry("0.1.1"))
        after = copy.deepcopy(before)
        after["installed"] = {"alpha": changed, "foreign.txt": f.foreign, "uninstall.exe": f.new_self}
        after["registry"] = f.registry()
        result = producer.admit_mixed_uninstall(before, after, current,
            f.context["registry_keys"], "alpha", "beta", "foreign.txt")
        self.assertEqual(result["state"], "MIXED_UNINSTALL_OBSERVED")
        for key in ("alpha", "foreign.txt"):
            changed_after = copy.deepcopy(after)
            changed_after["installed"].pop(key)
            with self.subTest(key=key), self.assertRaises(nsis.ContractError):
                producer.admit_mixed_uninstall(before, changed_after, current,
                    f.context["registry_keys"], "alpha", "beta", "foreign.txt")
        after["installed"]["beta"] = intact
        with self.assertRaises(nsis.ContractError):
            producer.admit_mixed_uninstall(before, after, current,
                f.context["registry_keys"], "alpha", "beta", "foreign.txt")

    def test_ack_survives_failed_post_effect_custody_but_never_authorizes_next_effect(self):
        f = self.fixture
        outcome = {"original_terminal": life.observed_terminal(0), "process": self.identity,
                   "custody_errors": [], "token": token(), "live_observation": None}
        after = mock.Mock(side_effect=OSError("snapshot refused"))
        with mock.patch.object(producer, "launch_once_and_observe", return_value=outcome) as launch, \
             mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(nsis.ContractError):
                producer.observe_installer(self.root, "ack-custody", "upgrade", self.exe,
                    ["/S"], {}, token(), f.source, f.run, after)
        self.assertEqual(launch.call_count, 1)
        retained = json.loads((self.root / "operations/ack-custody/upgrade-terminal.json").read_bytes())
        self.assertEqual(retained["original_terminal"], life.observed_terminal(0))
        self.assertEqual(json.loads((self.root / "operations/ack-custody/operation.json").read_bytes())
                         ["original_terminal"], life.observed_terminal(0))

    def test_unknown_result_cannot_be_promoted_by_final_state(self):
        row = {"original_terminal": life.observed_terminal(None), "custody_errors": [], "observation": {"matches": True}}
        with self.assertRaises(nsis.ContractError):
            producer.require_known_success(row)

    def test_checkpoint_wrong_identity_does_not_terminate(self):
        f = self.fixture
        folder = self.root / "checkpoint"
        folder.mkdir()
        operation = "{12345678-1234-4234-8234-123456789ABC}"
        compiled = {"setups": {"opt-in": self.exe}, "main": f.ident(f.new_main),
                    "profile_pin": f.profile_ref["sha256"]}
        callback = producer.interrupt_observer(self.root, folder, operation, compiled, "b" * 40, f.source, f.run)
        text = ("motionwright.nsis-checkpoint/1|id=" + operation
            + "|pid=999|creation_filetime=134000000000000001|phase=main-withdrawn-before-publication"
            + "|stage=mw-stage-{ABCDEF12-1234-4234-8234-123456789ABC}\\payload.bin|candidate_sha256="
            + compiled["main"]["sha256"] + "|candidate_size=" + str(compiled["main"]["bytes"]) + "\r\n")
        (folder / ("motionwright-nsis-" + operation + ".checkpoint")).write_bytes(text.encode("utf-16-le"))
        with mock.patch.object(producer, "process_identity", return_value=self.identity), \
             mock.patch.object(producer, "require_original_image"), \
             mock.patch.object(producer, "terminate_original_once") as stop:
            with self.assertRaisesRegex(nsis.ContractError, "checkpoint_original_identity_mismatch"):
                callback(self.process, self.identity)
        stop.assert_not_called()

    def test_partial_checkpoint_remains_original_and_never_terminates(self):
        folder = self.root / "partial"
        folder.mkdir()
        operation = "{12345678-1234-4234-8234-123456789ABC}"
        callback = producer.interrupt_observer(self.root, folder, operation,
            {"setups": {"opt-in": self.exe}}, "b" * 40, self.fixture.source, self.fixture.run)
        path = folder / ("motionwright-nsis-" + operation + ".checkpoint")
        path.write_bytes(b"x")
        with mock.patch.object(producer, "terminate_original_once") as stop:
            with self.assertRaises(nsis.ContractError):
                callback(self.process, self.identity)
        stop.assert_not_called()
        self.assertEqual(path.read_bytes(), b"x")

    def test_single_original_termination_receipt_and_known_wait_are_required(self):
        f = self.fixture
        folder = self.root / "known-checkpoint"
        folder.mkdir()
        operation = "{12345678-1234-4234-8234-123456789ABC}"
        compiled = {"setups": {"opt-in": self.exe}, "main": f.ident(f.new_main),
                    "profile_pin": f.profile_ref["sha256"]}
        callback = producer.interrupt_observer(self.root, folder, operation, compiled, "b" * 40, f.source, f.run)
        text = ("motionwright.nsis-checkpoint/1|id=" + operation
            + "|pid=123|creation_filetime=134000000000000001|phase=main-withdrawn-before-publication"
            + "|stage=mw-stage-{ABCDEF12-1234-4234-8234-123456789ABC}\\payload.bin|candidate_sha256="
            + compiled["main"]["sha256"] + "|candidate_size=" + str(compiled["main"]["bytes"]) + "\r\n")
        (folder / ("motionwright-nsis-" + operation + ".checkpoint")).write_bytes(text.encode("utf-16-le"))
        terminal = {"termination_attempts": 1, "same_original_handle": True,
            "terminate_returned_success": True, "wait_result": "WAIT_OBJECT_0",
            "exit_code": 99, "ambiguous": False, "state": "TERMINATED"}
        with mock.patch.object(producer, "process_identity", return_value=self.identity), \
             mock.patch.object(producer, "require_original_image"), \
             mock.patch.dict(os.environ, {"GITHUB_JOB": "physical"}), \
             mock.patch.object(producer, "terminate_original_once", return_value=terminal) as stop:
            result = callback(self.process, self.identity)
        stop.assert_called_once_with(self.process)
        self.assertEqual(result["terminal"]["exit_code"], 99)
        self.assertEqual(result["checkpoint"]["creation_filetime"], str(self.identity["creation_filetime"]))

    def test_workflow_has_positive_physical_boundaries_and_one_compile(self):
        path = Path(producer.__file__).parents[1] / ".github/workflows/windows-nsis-interruption.yml"
        body = path.read_text()
        self.assertIn("default: g0", body)
        self.assertIn("options: [g0, compile, physical]", body)
        self.assertIn("scope not in ('g0', 'compile', 'physical')", body)
        physical = body.split("\n  physical:\n", 1)[1]
        self.assertIn("max-parallel: 1", physical)
        self.assertEqual(physical.count("tooling/windows_installer_producer.py"), 1)
        self.assertNotIn("cargo ", physical)
        for step in physical.split("\n      - ")[1:]:
            self.assertIn("github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'physical'", step)
        compiler = ast.parse(Path(nsis.__file__).read_text())
        calls = [x for x in ast.walk(compiler) if isinstance(x, ast.Constant) and x.value == "build-real-application"]
        self.assertEqual(len(calls), 1)


    def transport_fixture(self):
        """Small synthetic original files exercise the real transport readers."""
        f = self.fixture
        root = self.root / "transport"
        target = root / "target"
        main = target / "release/motionwright-desktop.exe"
        nsis.write_new(main, b"MZsynthetic-current-main-not-executable")
        identity = life.file_identity(main)
        application = nsis.retain_built_application(main, root, identity)
        family = root / "inputs/predecessor/input"
        family.mkdir(parents=True)
        for name, ref in [("previous-main.exe", f.old_main), ("previous-uninstall.exe", f.old_self),
                          ("previous-setup.exe", f.old_setup), ("previous-capture-receipt.json", f.previous_ref)]:
            nsis.write_new(family / name, life.bound_file(self.root, ref).read_bytes())
        profile = copy.deepcopy(f.profile)
        profile["package"]["path"] = "previous-setup.exe"
        profile["capture_receipt"]["path"] = "previous-capture-receipt.json"
        for row, source in zip(profile["files"], ("previous-main.exe", "previous-uninstall.exe")):
            row["source"] = source
        profile["target"]["main_sha256"] = identity["sha256"]
        nsis.write_json(family / "predecessor.json", profile)
        pin = life.file_identity(family / "predecessor.json")["sha256"]
        resources = {}
        for name, raw in (("resource-alpha.dat", b"synthetic alpha"), ("resource-beta.dat", b"synthetic beta")):
            path = root / "inputs" / name
            nsis.write_new(path, raw)
            resources["mw-ci-render/" + name] = {"kind": "resource", **life.file_identity(path)}
        source = {"checkout": f.source, "tree": f.tree, "files": {}}
        preparation = {"fixture_scope": "two_synthetic_resources_only", "profile_sha256": pin,
            "new_main_before_bundle": identity, "source_sha": f.source,
            "expected_fixture_payloads": resources, "profile": profile,
            "source_before": source, "source_after": source}
        nsis.write_json(root / "inputs/predecessor-admission.json", preparation)
        expected = {"PRODUCTNAME": "Motionwright", "BUNDLEID": "com.seradotcom.motionwright",
            "ARCH": "x64", "INSTALLMODE": "currentUser", "VERSION": "0.1.1",
            "MAINBINARYNAME": "motionwright-desktop", "MAINBINARYSRCPATH": str(main)}
        variants = []
        for variant in ("normal", "opt-in"):
            render = target / "release/nsis/x64"
            render.mkdir(parents=True)
            text = "!define MWINST_CI_INTERRUPTION\n" if variant == "opt-in" else ""
            text += "".join(f'!define {key} "{value}"\n' for key, value in expected.items())
            text += 'Section Install\n!insertmacro MWINST_INSTALL_PAYLOAD "${MAINBINARYNAME}.exe" MW_MAIN\n'
            text += ('StrCpy $MWINST_Relative "mw-ci-render"\nClearErrors\n'
                     'CreateDirectory "$INSTDIR\\$MWINST_Relative"\n${If} ${Errors}\n'
                     '!insertmacro MWINST_FAIL "A resource directory could not be created."\n${EndIf}\n')
            for index, name in enumerate(resources):
                text += f'!insertmacro MWINST_INSTALL_PAYLOAD "{name}" MW_RESOURCE_{index}\n'
            text += "SectionEnd\nSection Uninstall\n"
            for index, name in enumerate(resources):
                text += '!insertmacro MWINST_DELETE_OWNED "' + name + '" "${MW_RESOURCE_' + str(index) + '_SHA256}" "${MW_RESOURCE_' + str(index) + '_SIZE}"\n'
            text += 'StrCpy $MWINST_Relative "mw-ci-render"\nRMDir "$INSTDIR\\$MWINST_Relative"\nSectionEnd\n'
            nsis.write_new(render / "installer.nsi", text.encode("utf-8-sig"))
            captures = {"MW_MAIN": (main.read_bytes(), identity)}
            for index, (name, value) in enumerate(resources.items()):
                captures["MW_RESOURCE_" + str(index)] = ((root / "inputs" / Path(name).name).read_bytes(),
                                                          {key: value[key] for key in ("bytes", "sha256")})
            for symbol, (raw, ident) in captures.items():
                path = render / ("mw-owned-" + symbol + ".bin")
                nsis.write_new(path, raw)
                include = (f'!define {symbol}_SOURCE "{path}"\n'
                           f'!define {symbol}_SHA256 "{ident["sha256"]}"\n'
                           f'!define {symbol}_SIZE {ident["bytes"]}\n')
                nsis.write_new(render / ("mw-owned-" + symbol + ".nsh"), include.encode("utf-8-sig"))
            producer.emit_predecessor_include(render / "mw-predecessor.nsh",
                application="Motionwright", bundle_id="com.seradotcom.motionwright", arch="x64",
                target_version="0.1.1", target_main_sha256=identity["sha256"],
                environment={"MW_NSIS_PREDECESSOR_MANIFEST": str(family / "predecessor.json"),
                             "MW_NSIS_PREDECESSOR_MANIFEST_SHA256": pin})
            nsis.write_new(target / "release/bundle/nsis/Motionwright_0.1.1_x64-setup.exe",
                           b"MZsynthetic-setup-" + variant.encode())
            folder = root / "variants" / variant
            nsis.retain_variant(target, folder)
            observation = nsis.inspect_variant(folder, variant, identity, expected, application)
            observation["predecessor_capture"] = nsis.inspect_predecessor_capture(folder, family / "predecessor.json", pin)
            observation["predecessor_profile"] = "ADMITTED"
            observation["synthetic_resources"] = nsis.inspect_synthetic_resources(folder, resources)
            variants.append(observation)
        binding = {"product": {"sha": f.source, "tree": f.tree}, "gate": {"sha": "b" * 40, "tree": "c" * 40}}
        report = {"schema": "motionwright.nsis-interruption-compile/1", "compile": "TWO_VARIANTS_COMPILED",
            "gate_sha": "b" * 40, "product_sha": f.source, "run_id": str(f.run["run_id"]), "run_attempt": "1",
            "source_before": binding, "source_after": binding, "predecessor_profile": "ADMITTED",
            "real_application": identity, "variants": variants}
        nsis.write_json(root / "compile-summary.json", report)
        # This authority is deliberately synthetic. The real fixed pin is tested separately.
        fixed = copy.deepcopy(producer.FIXED_PREDECESSOR)
        fixed.update(source=profile["source_sha"], tree=profile["source_tree"])
        for label, ref in (("main", f.old_main), ("self", f.old_self), ("package", f.old_setup), ("receipt", f.previous_ref)):
            fixed[label].update(f.ident(ref))
        return root, report, fixed

    def test_transported_variants_share_one_main_and_full_profile_with_actual_resource_captures(self):
        root, unused, fixed = self.transport_fixture()
        with mock.patch.object(producer, "FIXED_PREDECESSOR", fixed):
            result = producer.admit_compiled(root, "b" * 40, self.fixture.source, self.fixture.run)
        self.assertEqual(set(result["current"]), {"motionwright-desktop.exe",
            "mw-ci-render/resource-alpha.dat", "mw-ci-render/resource-beta.dat"})
        self.assertNotEqual(life.file_identity(result["setups"]["normal"]), life.file_identity(result["setups"]["opt-in"]))
        self.assertEqual(result["profile_document"]["target"]["main_sha256"], result["main"]["sha256"])

    def test_transported_resource_changed_after_capture_is_rejected(self):
        root, unused, fixed = self.transport_fixture()
        changed = root / "variants/normal/render/x64/mw-owned-MW_RESOURCE_0.bin"
        changed.write_bytes(b"tampered resource")
        with mock.patch.object(producer, "FIXED_PREDECESSOR", fixed), \
             self.assertRaisesRegex(nsis.ContractError, "variant_original_transport_changed"):
            producer.admit_compiled(root, "b" * 40, self.fixture.source, self.fixture.run)

    def test_transported_summary_wrong_run_or_profile_authority_rejected(self):
        root, unused, fixed = self.transport_fixture()
        with mock.patch.object(producer, "FIXED_PREDECESSOR", fixed), self.assertRaises(nsis.ContractError):
            producer.admit_compiled(root, "b" * 40, self.fixture.source, {**self.fixture.run, "run_id": 43})
        with self.assertRaisesRegex(nsis.ContractError, "predecessor_literal_authority_changed"):
            producer.admit_compiled(root, "b" * 40, self.fixture.source, self.fixture.run)

    def test_observer_controller_timeout_retains_unknown_and_never_replays(self):
        output = self.root / "observer"
        output.mkdir()
        outcome = {"original_terminal": life.observed_terminal(None, timed_out=True), "custody_errors": []}
        with mock.patch.object(producer, "launch_once_and_observe", return_value=outcome) as launch:
            with self.assertRaisesRegex(nsis.ContractError, "updated_application_incomplete"):
                producer.invoke_updated_observer(output, [sys.executable, "synthetic-oracle.py"], {})
        self.assertEqual(launch.call_count, 1)
        terminal = json.loads((output / "observer-terminal.json").read_bytes())
        self.assertEqual(terminal["original_terminal"], outcome["original_terminal"])

    def test_observer_controller_ack_is_original(self):
        output = self.root / "observer-ack"
        output.mkdir()
        outcome = {"original_terminal": life.observed_terminal(0), "custody_errors": []}
        with mock.patch.object(producer, "launch_once_and_observe", return_value=outcome) as launch:
            result = producer.invoke_updated_observer(output, [sys.executable, "synthetic-oracle.py"], {})
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(result, life.observed_terminal(0))
        self.assertEqual(json.loads((output / "observer-terminal.json").read_bytes())["original_terminal"], result)

    def test_current_default_state_cannot_credit_previous_version_or_override(self):
        f = self.fixture
        before = f.snapshot(str(uuid.uuid4()), "before", {}, f.registry(metadata=False))
        after = f.snapshot(before["operation_id"], "after",
            {"motionwright-desktop.exe": f.new_main, "uninstall.exe": f.new_self}, f.registry("0.1.1"))
        current = {"motionwright-desktop.exe": f.ident(f.new_main)}
        value = producer.admit_default_install(before, after, current, f.context["registry_keys"],
                                                Path(f.install_path), ["/S"])
        self.assertFalse(value["destination_override"])
        for changed, args in (({**after, "registry": f.registry("0.1.0")}, ["/S"]),
                              (after, ["/S", "/D=" + f.install_path])):
            with self.subTest(args=args), self.assertRaises((nsis.ContractError, life.LifecycleError)):
                producer.admit_default_install(before, changed, current, f.context["registry_keys"],
                                                Path(f.install_path), args)


    def test_final_projection_budget_does_not_hide_known_observation(self):
        output = self.root / "final-projection"
        output.mkdir()
        result = {"status": "OBSERVED", "observation": {"original_terminal": "ACK"}}
        budget = {"bytes": nsis.MAX_DIAGNOSTIC_BYTES - 1, "files": 1}
        with mock.patch.object(producer, "synthetic_upload_budget", return_value=budget):
            producer.finalize_case_result(output, self.root, result)
        retained = json.loads((output / "case-result.json").read_bytes())
        self.assertEqual(retained["status"], "INCOMPLETE")
        self.assertEqual(retained["observation"], {"original_terminal": "ACK"})
        self.assertEqual(retained["upload_admission"]["state"], "NOT_ADMITTED")
        self.assertFalse(retained["upload_admission"]["originals_removed"])


if __name__ == "__main__":
    unittest.main()
