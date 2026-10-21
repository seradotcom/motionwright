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
from test_storage_schema_contract import cohort as storage_cohort_fixture, observed_source as observed_storage_source

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



class StorageCohortProducerControls(unittest.TestCase):
    def test_fixed_installed_consumer_accepts_historical_cohorts_and_closes_four(self):
        for version in (2, 3):
            value = storage_cohort_fixture(version)
            self.assertEqual(producer.require_installed_storage_consumer(value), value)
        with self.assertRaisesRegex(nsis.ContractError, "schema4_installed_oracle_requires_reviewed_successor"):
            producer.require_installed_storage_consumer(storage_cohort_fixture(4))
        for value in (4, True, {"storage_schema": 4}):
            with self.subTest(value=value), self.assertRaises(producer.storage.StorageContractError):
                producer.require_installed_storage_consumer(value)

    def physical_refusal(self, *, changed=False):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            root = workspace / "nsis-physical/default-uninstall/originals"
            (root / "compile").mkdir(parents=True)
            observation, raw = observed_storage_source(4)
            path = workspace / "product" / producer.storage.SOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(raw.replace(b"4", b"3") if changed else raw)
            args = SimpleNamespace(case="default-uninstall", gate_sha="a" * 40, product_sha="a" * 40,
                oracle_sha="016cd88c4ef20ada70fcc446361c40e4a0b81ca1")
            with mock.patch.dict(os.environ, {"GITHUB_WORKSPACE": str(workspace)}), \
                 mock.patch.object(producer, "physical_scope", return_value={"run_attempt": 1}), \
                 mock.patch.object(nsis, "source_observation", return_value=observation), \
                 mock.patch.object(producer, "observe_non_elevated_token") as token_probe, \
                 mock.patch.object(producer, "actual_profile") as profile, \
                 mock.patch.object(producer, "admit_compiled") as compiled, \
                 mock.patch.object(producer, "run_default_uninstall_case") as launch, \
                 mock.patch.object(producer, "finalize_case_result") as finalize, \
                 mock.patch("sys.stdout", new=io.StringIO()):
                result = producer.run_physical_case(args)
            token_probe.assert_not_called()
            profile.assert_not_called()
            compiled.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(result, 1)
            value = finalize.call_args.args[2]
            self.assertEqual(value["status"], "INCOMPLETE")
            self.assertEqual(value["error"]["state"], "ERROR")
            self.assertEqual((root / "source-before.json").read_text().count("PASS"), 0)
            return value

    def test_physical_four_stops_before_token_profile_or_installer(self):
        value = self.physical_refusal()
        self.assertEqual(value["storage_cohort"], storage_cohort_fixture(4))

    def test_changed_storage_declaration_cannot_select_a_historical_physical_cohort(self):
        value = self.physical_refusal(changed=True)
        self.assertNotIn("storage_cohort", value)

    def test_legacy_lifecycle_four_is_rejected_before_evidence_or_native_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {"GITHUB_WORKSPACE": str(root), "GITHUB_SHA": "a" * 40,
                   "GITHUB_REPOSITORY": "example/motionwright", "GITHUB_RUN_ID": "7",
                   "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ACTIONS": "true",
                   "GITHUB_EVENT_NAME": "workflow_dispatch", "MW_NSIS_LIFECYCLE_SCOPE": "nsis_lifecycle"}
            with mock.patch.dict(os.environ, env), mock.patch.object(producer.os, "name", "nt"), \
                 mock.patch.object(life, "source_observation", return_value={}), \
                 mock.patch.object(producer.storage, "admit_checkout", return_value=storage_cohort_fixture(4)), \
                 mock.patch.object(producer.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(nsis.ContractError, "schema4_installed_oracle"):
                    producer.run_legacy_lifecycle()
                launch.assert_not_called()
            self.assertFalse((root / "ci-evidence").exists())

    def test_snapshot_wrappers_keep_four_source_bound_and_default_v1_separate(self):
        # The real capture/reader is exercised by lifecycle controls; this call
        # boundary ensures a selected cohort cannot silently fall back to v1.
        selected = storage_cohort_fixture(4)
        arguments = (Path("root"), "before", str(uuid.uuid4()), "before",
                     Path("install"), Path("database"), [], "create-library-project/1")
        with mock.patch.object(life, "capture_snapshot", return_value={"path": "capture"}) as capture:
            producer.snapshot(*arguments, storage_cohort=selected)
            self.assertEqual(capture.call_args.kwargs["storage_cohort"], selected)
            producer.snapshot(*arguments)
            self.assertNotIn("storage_cohort", capture.call_args.kwargs)
        with mock.patch.object(life, "snapshot_value", return_value={}) as read:
            producer.read_snapshot(Path("root"), {}, arguments[2], "before", [], storage_cohort=selected)
            self.assertEqual(read.call_args.kwargs["storage_cohort"], selected)
            producer.read_snapshot(Path("root"), {}, arguments[2], "before", [])
            self.assertNotIn("storage_cohort", read.call_args.kwargs)

    def test_fixed_oracle_roster_does_not_claim_new_cohort_helper_at_old_source(self):
        self.assertEqual(producer.LEGACY_ORACLE_PATHS, (
            "tooling/windows_updated_application.py", "tooling/windows_updated_application_ui.ps1",
            "tooling/tests/test_windows_updated_application.py",
            "tooling/tests/test_windows_updated_application_ui_contract.py",
            "docs/reliability/windows-updated-application.md", "tooling/windows_cold_start.py",
            "tooling/windows_installer_lifecycle.py", "tooling/nsis_owned_files.py",
            "tooling/release_manifest.py"))
        self.assertIn("tooling/storage_schema_contract.py", nsis.GATE_PATHS)
        self.assertIn("tooling/storage_schema_contract.py", life.SOURCE_PATHS)
        self.assertIn(producer.storage.SOURCE_PATH, nsis.PRODUCT_PATHS)



class TokenDiagnosticControls(unittest.TestCase):
    """Only OS boundaries are mocked; callback/admission and custody are real."""

    def observe(self, value, callback=None, *, process=None):
        from types import SimpleNamespace as NS
        kernel, advapi = mock.Mock(), mock.Mock()
        kernel.GetCurrentProcess.return_value = 123
        advapi.OpenProcessToken.return_value = True
        def information(token, kind, buffer, size, needed):
            needed._obj.value = 32
            return True
        advapi.GetTokenInformation.side_effect = information
        advapi.GetSidSubAuthorityCount.return_value = NS(contents=NS(value=1))
        advapi.GetSidSubAuthority.return_value = NS(contents=NS(value=value["integrity_rid"]))
        def sid(original, output):
            output._obj.value = value["user_sid"]
            return True
        advapi.ConvertSidToStringSidW.side_effect = sid
        casts = [NS(contents=NS(value=int(value["elevated"]))),
                 NS(contents=NS(value=value["session_id"])),
                 NS(contents=NS(sid=456)), NS(contents=NS(sid=789)), 456]
        with mock.patch.object(producer.os, "name", "nt"), \
             mock.patch.object(producer.ctypes, "WinDLL", side_effect=[kernel, advapi], create=True), \
             mock.patch.object(producer.ctypes, "cast", side_effect=casts):
            try:
                return producer.observe_non_elevated_token(process, before_admission=callback)
            finally:
                self.assertEqual(kernel.CloseHandle.call_count, 1)

    def test_original_token_is_retained_before_rejected_privilege_admission(self):
        value = dict(token(), elevated=True, session_id=0)
        seen = []
        with self.assertRaisesRegex(nsis.ContractError, "actual_non_elevated_interactive_token_required"):
            self.observe(value, lambda original: seen.append(original))
        self.assertEqual(seen, [value])

    def test_default_token_api_and_child_handle_keep_the_same_admission(self):
        self.assertEqual(self.observe(token()), token())
        child = mock.Mock(_handle=456)
        self.assertEqual(self.observe(token(), process=child), token())

    def test_callback_cannot_relabel_the_actual_token_before_admission(self):
        value = dict(token(), elevated=True)
        def rewrite(copy):
            copy["elevated"] = False
        with self.assertRaises(nsis.ContractError):
            self.observe(value, rewrite)

    def test_token_custody_failure_prevents_admission_and_closes_handle(self):
        with mock.patch.object(producer, "admit_token") as admit:
            def fail(value):
                raise OSError("synthetic custody failure")
            with self.assertRaises(OSError):
                self.observe(token(), fail)
            admit.assert_not_called()

    def test_bounded_original_is_exclusive_and_keeps_unadmitted_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            value = dict(token(), integrity_rid=0x3000, session_id=0)
            producer.retain_original_token(root, value)
            original = (root / "actual-token-original.json").read_bytes()
            report = json.loads(original)
            self.assertEqual(report["observation"], value)
            self.assertEqual(report["admission"], "NOT_YET_ATTEMPTED")
            with self.assertRaises(Exception):
                producer.retain_original_token(root, token())
            self.assertEqual((root / "actual-token-original.json").read_bytes(), original)

    def test_token_original_bounds_reject_unbounded_or_wrongly_typed_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for changes in ({"user_sid": "x" * 257}, {"integrity_rid": True},
                            {"session_id": -1}, {"elevated": 1}):
                with self.subTest(changes=changes), self.assertRaises(nsis.ContractError):
                    producer.retain_original_token(root, dict(token(), **changes))
            self.assertFalse((root / "actual-token-original.json").exists())

    def test_closed_contract_code_and_exact_phase_survive_diagnostics(self):
        error = nsis.ContractError("actual_non_elevated_interactive_token_required")
        actual = producer.physical_case_error("token_observation", error)
        self.assertEqual(actual, {"stage": "token_observation", "type": "ContractError",
                         "code": "actual_non_elevated_interactive_token_required", "state": "ERROR"})
        self.assertEqual(producer.physical_case_error("profile_observation", error)["stage"],
                         "profile_observation")

    def test_arbitrary_error_text_or_phase_never_leaks_into_diagnostics(self):
        for message in ("C:\\private\\secret.txt", "token_error\nsecret", "secret/token",
                        "x" * 200, "Bearer hidden-value"):
            with self.subTest(message=message):
                result = producer.physical_case_error("C:\\private", nsis.ContractError(message))
                self.assertEqual(result["stage"], "physical_case")
                self.assertEqual(result["code"], "ContractError")
                self.assertNotIn(message, json.dumps(result))
        result = producer.physical_case_error("token_observation", OSError("private token detail"))
        self.assertEqual(result["code"], "OSError")



class SourceBoundStorageProducerControls(unittest.TestCase):
    """Source composition and original custody only; native boundaries are mocked."""

    def storage_transport(self, version=3, crlf=False):
        control = ProducerControls("test_transported_variants_share_one_main_and_full_profile_with_actual_resource_captures")
        control.setUp()
        self.addCleanup(control.doCleanups)
        root, summary, fixed = control.transport_fixture()
        preparation_path = root / "inputs/predecessor-admission.json"
        preparation = json.loads(preparation_path.read_bytes())
        before_observation, before_raw = observed_storage_source(
            version, source=fixed["source"], tree=fixed["tree"])
        after_observation, after_raw = observed_storage_source(
            4, source=control.fixture.source, tree=control.fixture.tree, crlf=crlf)
        literal = {"bytes": len(before_raw), "sha256": hashlib.sha256(before_raw).hexdigest(),
                   "git_blob": hashlib.sha1(b"blob " + str(len(before_raw)).encode() + b"\0" + before_raw).hexdigest()}
        work = root / "inputs/predecessor"
        before = producer.retain_storage_originals(work / "before-storage", before_observation, before_raw,
            provenance={"kind": "RETAINED_REVIEWED_GIT_BLOB", "source_sha": fixed["source"],
                        "source_tree": fixed["tree"], "path": producer.storage.SOURCE_PATH,
                        "git_blob": literal["git_blob"]})
        after = producer.retain_storage_originals(work / "after-storage", after_observation, after_raw,
            provenance={"kind": "ORIGINAL_PRODUCT_CHECKOUT", "source_sha": control.fixture.source,
                        "source_tree": control.fixture.tree, "path": producer.storage.SOURCE_PATH})
        preparation.update(storage_cohort=after["cohort"], storage_originals={"before": before, "after": after})
        preparation_path.write_text(json.dumps(preparation))
        summary["source_before"]["product"] = after_observation
        summary["source_after"] = copy.deepcopy(summary["source_before"])
        (root / "compile-summary.json").write_text(json.dumps(summary))
        nsis.write_new(root / "original-source/product" / producer.storage.SOURCE_PATH,
                       after_raw.replace(b"\r\n", b"\n"))
        return control, root, fixed, literal, preparation

    def admit_transport(self, setup):
        control, root, fixed, literal, preparation = setup
        with mock.patch.object(producer, "FIXED_PREDECESSOR", fixed), \
             mock.patch.object(producer, "FIXED_PREDECESSOR_STORAGE", literal):
            return producer.admit_compiled(root, "b" * 40, control.fixture.source,
                control.fixture.run, storage_cohort=preparation["storage_cohort"])

    def consumer_fixture(self, version=3):
        import test_windows_updated_application as fixtures
        fixture = fixtures.StorageSourceAdmissionContracts(
            "test_explicit_source_four_admits_originals_without_native_execution")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if version != 3:
            before, unused, value = fixtures.schema_four_snapshots(version)
            cohort, observation, raw = fixtures.storage_fixture(version)
            fixture.value["storage_cohorts"]["before"] = cohort
            fixture.before_cohort, fixture.before_source, fixture.before_raw = cohort, observation, raw
            fixture.value["refs"].update(
                before_storage_source=fixture.ref("selected-before.rs", raw),
                before_storage_observation=fixture.ref("selected-before-observation.json", observation))
            db = fixture.root / ("selected-" + str(version) + ".sqlite3")
            fixtures.make_cohort_db(db, before)
            family = {name: None for name in fixtures.oracle.FAMILY}
            family[fixtures.oracle.FAMILY[0]] = fixture.ref("selected-original.sqlite3", db.read_bytes())
            for role in ("before_snapshot", "recovered_snapshot"):
                fixture.replace_original(role, lambda snap: snap.update(
                    storage_cohort=cohort, sqlite_family=family))
            if version == 2:
                # Only this synthetic pair changes; production's literal family is immutable.
                for field, value in (("LEGACY_FIRST_USE_SOURCE", fixture.profile["source_sha"]),
                    ("LEGACY_FIRST_USE_BINARY", next(x["sha256"] for x in fixture.profile["files"]
                                                   if x["kind"] == "main"))):
                    patcher = mock.patch.object(fixtures.oracle.cold, field, value)
                    patcher.start()
                    fixture.addCleanup(patcher.stop)
        compiled = {"summary": fixtures.oracle.read_json(
            fixture.evidence, fixture.value["refs"]["compile_summary"]),
            "profile_document": fixture.profile, "storage_cohorts": fixture.value["storage_cohorts"],
            "storage_original_paths": {key: life.bound_file(fixture.evidence, fixture.value["refs"][key])
                                      for key in fixtures.oracle.STORAGE_REFS}}
        oracle_root = Path(producer.__file__).parents[1]
        observation = {"sha": producer.REVIEWED_SCHEMA4_ORACLE_SHA, "tree": "f" * 40,
                       "files": {path: {"checkout": life.file_identity(oracle_root / path)}
                                 for path in producer.ORACLE_PATHS}}
        patcher = mock.patch.object(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", fixture.value["product_sha"])
        patcher.start()
        fixture.addCleanup(patcher.stop)
        consumer = producer.admit_storage_consumer(fixture.evidence, compiled, fixture.after_cohort,
            oracle_root, producer.REVIEWED_SCHEMA4_ORACLE_SHA, observation)
        return fixture, compiled, consumer, observation

    def assemble(self, fixture, compiled, consumer):
        refs = {key: value for key, value in fixture.value["refs"].items()
                if key not in ("before_storage_source", "before_storage_observation")}
        project = {"project_id": fixture.value["project_id"], "generation": fixture.value["generation"],
                   "revision": fixture.value["revision"], "title": fixture.value["old_title"]}
        with mock.patch.object(nsis, "parse_checkpoint", fixture.gate.parse_checkpoint), \
             mock.patch.object(nsis, "validate_live_context", fixture.gate.validate_live_context), \
             mock.patch.object(nsis, "validate_termination", fixture.gate.validate_termination):
            return producer.assemble_updated_application_input(
                fixture.evidence, compiled, refs, source=fixture.value["product_sha"],
                gate_sha=fixture.value["gate_sha"], run=fixture.value["run"], install=fixture.install,
                database=fixture.database, keys=fixture.value["registry_keys"], project=project,
                **({"storage_consumer": consumer} if consumer is not None else {}))

    def test_oracle_closure_is_eleven_existing_files_without_relabelling_legacy(self):
        import windows_updated_application as oracle
        self.assertEqual(len(producer.LEGACY_ORACLE_PATHS), 9)
        self.assertEqual(len(producer.ORACLE_PATHS), 11)
        self.assertEqual(producer.ORACLE_PATHS, oracle.SOURCE_PATHS)
        for path in producer.ORACLE_PATHS:
            self.assertTrue((Path(producer.__file__).parents[1] / path).is_file())
        self.assertNotIn("tooling/storage_schema_contract.py", producer.LEGACY_ORACLE_PATHS)

    def test_unfrozen_product_and_bare_oracle_pin_do_not_admit_four(self):
        with mock.patch.object(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", None):
            with self.assertRaisesRegex(nsis.ContractError, "product_source_not_frozen"):
                producer.require_reviewed_product_source("a" * 40)
        for value in (None, producer.REVIEWED_SCHEMA4_ORACLE_SHA, {"oracle_sha": producer.REVIEWED_SCHEMA4_ORACLE_SHA}):
            with self.subTest(value=value), self.assertRaisesRegex(nsis.ContractError, "requires_reviewed_successor"):
                producer.require_installed_storage_consumer(storage_cohort_fixture(4), consumer=value)
        with mock.patch.object(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", "a" * 40):
            with self.assertRaisesRegex(nsis.ContractError, "outside_reviewed_contract"):
                producer.require_reviewed_product_source("b" * 40)

    def test_transported_originals_admit_each_explicit_cohort_without_native_effects(self):
        for version in (2, 3, 4):
            with self.subTest(before=version), mock.patch.object(producer.subprocess, "Popen") as native:
                setup = self.storage_transport(version, crlf=version == 3)
                result = self.admit_transport(setup)
                self.assertEqual(result["storage_cohorts"]["before"]["storage_schema"], version)
                self.assertEqual(result["storage_cohorts"]["after"]["storage_schema"], 4)
                self.assertEqual(set(result["storage_original_paths"]),
                    {"before_storage_source", "before_storage_observation"})
                native.assert_not_called()

    def test_missing_originals_and_current_source_mismatches_refuse_transport(self):
        for fault in ("missing-old", "changed-old", "missing-observation", "changed-git", "changed-cohort"):
            with self.subTest(fault=fault):
                setup = self.storage_transport()
                unused, root, fixed, literal, preparation = setup
                work = root / "inputs/predecessor"
                old = life.bound_file(work, preparation["storage_originals"]["before"]["source"])
                if fault == "missing-old":
                    old.unlink()
                elif fault == "changed-old":
                    old.write_bytes(b"pub const STORAGE_SCHEMA_VERSION: u32 = 4;\n")
                elif fault == "missing-observation":
                    life.bound_file(work, preparation["storage_originals"]["before"]["observation"]).unlink()
                elif fault == "changed-git":
                    (root / "original-source/product" / producer.storage.SOURCE_PATH).write_bytes(b"changed")
                else:
                    preparation["storage_cohort"]["source_tree"] = "f" * 40
                    (root / "inputs/predecessor-admission.json").write_text(json.dumps(preparation))
                with mock.patch.object(producer.subprocess, "Popen") as native, \
                     self.assertRaises((nsis.ContractError, life.LifecycleError,
                                        producer.storage.StorageContractError, OSError)):
                    self.admit_transport(setup)
                native.assert_not_called()

    def test_original_retention_is_exclusive_and_keeps_first_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "retained"
            observation, raw = observed_storage_source(4, crlf=True)
            first = producer.retain_storage_originals(root, observation, raw, provenance={"synthetic": True})
            original = (root / "source.rs").read_bytes()
            with self.assertRaises((FileExistsError, nsis.ContractError)):
                producer.retain_storage_originals(root, observation, raw, provenance={"replacement": True})
            self.assertEqual((root / "source.rs").read_bytes(), original)
            admitted = producer.admit_retained_storage(root.parent, first,
                source_sha="a" * 40, source_tree="b" * 40)
            self.assertEqual(admitted[2], raw)
            self.assertEqual(admitted[0]["source_input"]["checkout_encoding"], "EXACT_LF_TO_CRLF")

    def test_input_two_assembly_preserves_two_three_and_four_before_cohorts(self):
        for version in (2, 3, 4):
            with self.subTest(before=version):
                fixture, compiled, consumer, unused = self.consumer_fixture(version)
                with mock.patch.object(producer.subprocess, "Popen") as native:
                    path, pin = self.assemble(fixture, compiled, consumer)
                value = json.loads(path.read_bytes())
                self.assertEqual(value["schema"], "motionwright.windows-updated-application-input/2")
                self.assertEqual(value["storage_cohorts"]["before"]["storage_schema"], version)
                self.assertEqual(value["storage_cohorts"]["after"]["storage_schema"], 4)
                self.assertEqual(len(value["refs"]), 13)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), pin)
                for key in ("project_id", "generation", "revision", "old_title"):
                    self.assertEqual(value[key], fixture.value[key])
                native.assert_not_called()
                fixture.doCleanups()

    def test_input_one_stays_without_cohort_fields_or_extra_refs(self):
        import test_windows_updated_application as fixtures
        fixture = fixtures.OriginalAdmissionContracts("test_complete_original_bindings_delegate_to_canonical_nsis_reader")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path, unused = self.assemble(fixture, {}, None)
        value = json.loads(path.read_bytes())
        self.assertEqual(value["schema"], "motionwright.windows-updated-application-input/1")
        self.assertNotIn("storage_cohorts", value)
        self.assertEqual(len(value["refs"]), 11)

    def test_unknown_recovery_never_publishes_input_or_replays_observer(self):
        fixture, compiled, consumer, unused = self.consumer_fixture()
        fixture.replace_original("recovery_terminal", lambda record: record.update(
            original_terminal=life.observed_terminal(None, timed_out=True)))
        with mock.patch.object(producer, "invoke_updated_observer") as observer, \
             mock.patch.object(producer.subprocess, "Popen") as native, \
             self.assertRaisesRegex(nsis.ContractError, "requires_known_normal_recovery"):
            self.assemble(fixture, compiled, consumer)
        self.assertFalse((fixture.evidence / "updated-application-input.json").exists())
        observer.assert_not_called()
        native.assert_not_called()

    def test_changed_historical_original_after_admission_prevents_observer_input(self):
        fixture, compiled, consumer, unused = self.consumer_fixture()
        path = life.bound_file(fixture.evidence, consumer.refs["before_storage_source"])
        path.write_bytes(b"altered after original admission")
        with mock.patch.object(producer.subprocess, "Popen") as native, \
             self.assertRaises((life.LifecycleError, nsis.ContractError, OSError)):
            self.assemble(fixture, compiled, consumer)
        self.assertFalse((fixture.evidence / "updated-application-input.json").exists())
        native.assert_not_called()

    def test_changed_or_incomplete_oracle_closure_is_not_a_capability(self):
        fixture, compiled, consumer, original = self.consumer_fixture()
        oracle_root = Path(producer.__file__).parents[1]
        for fault in ("old-pin", "missing-helper", "changed-bytes"):
            observation = copy.deepcopy(original)
            pin = producer.REVIEWED_SCHEMA4_ORACLE_SHA
            if fault == "old-pin":
                pin = producer.LEGACY_ORACLE_SHA
            elif fault == "missing-helper":
                observation["files"].pop("tooling/storage_schema_contract.py")
            else:
                observation["files"]["tooling/storage_schema_contract.py"]["checkout"]["sha256"] = "0" * 64
            with self.subTest(fault=fault), self.assertRaisesRegex(nsis.ContractError, "schema4_oracle_"):
                producer.admit_storage_consumer(fixture.evidence, compiled, fixture.after_cohort,
                                               oracle_root, pin, observation)

    def test_loaded_dependency_cannot_differ_from_reviewed_oracle_bytes(self):
        fixture, compiled, consumer, original = self.consumer_fixture()
        changed = fixture.evidence / "different-lifecycle.py"
        changed.write_bytes(b"unrelated source bytes")
        with mock.patch.object(life, "__file__", str(changed)), \
             mock.patch.object(producer.subprocess, "Popen") as native, \
             self.assertRaisesRegex(nsis.ContractError, "loaded_dependency_changed"):
            producer.admit_storage_consumer(fixture.evidence, compiled, fixture.after_cohort,
                Path(producer.__file__).parents[1], producer.REVIEWED_SCHEMA4_ORACLE_SHA, original)
        native.assert_not_called()

    def test_modern_missing_compile_originals_refuse_before_token_profile_or_installer(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "nsis-physical/default-uninstall/originals"
            (root / "compile").mkdir(parents=True)
            observation, raw = observed_storage_source(4)
            path = workspace / "product" / producer.storage.SOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
            args = SimpleNamespace(case="default-uninstall", gate_sha="b" * 40,
                product_sha="a" * 40, oracle_sha=producer.REVIEWED_SCHEMA4_ORACLE_SHA)
            with mock.patch.dict(os.environ, {"GITHUB_WORKSPACE": str(workspace)}), \
                 mock.patch.object(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", args.product_sha), \
                 mock.patch.object(producer, "physical_scope", return_value={"run_attempt": 1}), \
                 mock.patch.object(nsis, "source_observation", return_value=observation), \
                 mock.patch.object(producer, "observe_non_elevated_token") as token_probe, \
                 mock.patch.object(producer, "actual_profile") as profile, \
                 mock.patch.object(producer, "admit_compiled", side_effect=nsis.ContractError("storage_originals_missing")), \
                 mock.patch.object(producer, "run_default_uninstall_case") as launch, \
                 mock.patch.object(producer, "finalize_case_result") as finalize, \
                 mock.patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(producer.run_physical_case(args), 1)
            token_probe.assert_not_called()
            profile.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(finalize.call_args.args[2]["error"]["stage"], "compile_admission")

    def test_workflow_g0_remains_available_and_effectful_scopes_require_freeze(self):
        body = (Path(producer.__file__).parents[1] / ".github/workflows/windows-nsis-interruption.yml").read_text()
        self.assertEqual(body.count(producer.REVIEWED_SCHEMA4_ORACLE_SHA), 3)
        self.assertNotIn(producer.LEGACY_ORACLE_SHA, body)
        product_input = body.split("      product_source_sha:\n", 1)[1].split("      oracle_source_sha:\n", 1)[0]
        self.assertIn("required: false", product_input)
        begin = body.index("          import os\n")
        end = body.index("          PY\n", begin)
        code = "\n".join(line[10:] for line in body[begin:end].splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            for scope in ("g0", "compile", "physical"):
                output = Path(temporary) / (scope + ".txt")
                env = {"REQUESTED_SCOPE": scope, "GITHUB_EVENT_NAME": "workflow_dispatch",
                       "REQUESTED_ORACLE_SHA": producer.REVIEWED_SCHEMA4_ORACLE_SHA,
                       "REQUESTED_PRODUCT_SHA": "" if scope == "g0" else "a" * 40,
                       "GITHUB_SHA": "b" * 40, "GITHUB_OUTPUT": str(output)}
                with mock.patch.dict(os.environ, env), \
                     mock.patch.object(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", None):
                    if scope == "g0":
                        exec(compile(code, "<workflow-identity>", "exec"), {})
                        self.assertIn("product_sha=" + "b" * 40, output.read_text())
                    else:
                        with self.assertRaisesRegex(nsis.ContractError, "product_source_not_frozen"):
                            exec(compile(code, "<workflow-identity>", "exec"), {})
                        self.assertFalse(output.exists())

    def test_case_callbacks_keep_current_and_historical_cohorts_separate(self):
        from contextlib import ExitStack
        for case in ("default-uninstall", "interruption-recovery"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "originals"
                install = Path(temporary) / "install"
                install.mkdir()
                def retained(relative, raw=b"synthetic nonexecutable"):
                    path = root / relative
                    nsis.write_new(path, raw)
                    return path
                main = retained("compile/current-main.bin")
                normal = retained("compile/normal.bin")
                optin = retained("compile/optin.bin", b"distinct optin nonexecutable")
                profile = retained("compile/inputs/predecessor/input/predecessor.json", b"{}")
                retained("compile/inputs/predecessor/input/previous-setup.exe")
                retained("compile/compile-summary.json", b"{}")
                terminal = life.retained_ref(root, retained("terminal.json", b"{}"))
                old_main = life.retained_ref(root, retained("old-main.bin", b"old"))
                previous = {"motionwright-desktop.exe": life.ref_identity(old_main)}
                current = {"motionwright-desktop.exe": life.file_identity(main)}
                for name in ("resource-alpha.dat", "resource-beta.dat"):
                    path = install / "mw-ci-render" / name
                    nsis.write_new(path, name.encode())
                    current["mw-ci-render/" + name] = life.file_identity(path)
                compiled = {"profile": profile, "main_path": main, "main": life.file_identity(main),
                            "setups": {"normal": normal, "opt-in": optin},
                            "previous": previous, "current": current}
                before = storage_cohort_fixture(3, source="d" * 40, tree="e" * 40)
                after = storage_cohort_fixture(4)
                consumer = producer._StorageConsumerAdmission({"before": before, "after": after}, {}, mock.Mock())
                selected = after if case == "default-uninstall" else before
                snapshots_seen, reads_seen, states = [], [], {}
                project = {"project_id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
                           "revision": 0, "title": "Synthetic retained project"}
                def capture(root_arg, label, operation, position, *args, **kwargs):
                    self.assertEqual(kwargs, {"storage_cohort": selected})
                    snapshots_seen.append(label)
                    ref = life.retained_ref(root, retained("snapshots/" + label + ".json", b"{}"))
                    states[ref["path"]] = {"installed": (
                        {"motionwright-desktop.exe": old_main} if label == "previous-after" else {}),
                        "registry": {"32": {}, "64": {}},
                        "sqlite_family": {name: None for name in (
                            "motionwright.sqlite3", "motionwright.sqlite3-wal", "motionwright.sqlite3-shm")},
                        "project": project}
                    return ref
                def read(root_arg, reference, *args, **kwargs):
                    self.assertEqual(kwargs, {"storage_cohort": selected})
                    reads_seen.append(reference["path"])
                    return states[reference["path"]]
                live = {"checkpoint": {"stage": "mw-stage-{ABCDEF12-1234-4234-8234-123456789ABC}\\payload.bin"},
                        "terminal": {"exit_code": 99}, "checkpoint_ref": terminal,
                        "context_ref": terminal, "terminal_ref": terminal}
                def observe(root_arg, label, phase, executable, arguments, environment,
                            token_value, source, run, callback, *, operation, **kwargs):
                    code = 99 if label == "interrupt-once" else 0
                    outcome = {"original_terminal": life.observed_terminal(code), "custody_errors": [],
                               "live_observation": live if code == 99 else None}
                    return {**outcome, "observation": callback(operation, outcome)}, terminal
                context = (root, compiled, {}, token(), "a" * 40,
                    {"repository": "example/motionwright", "run_id": 1, "run_attempt": 1},
                    install, Path(temporary) / "database/motionwright.sqlite3", ["Software\\Example"],
                    "create-library-project/1")
                input_path = root / "unit-input.json"
                with ExitStack() as stack:
                    def patch(target, name, **kwargs):
                        return stack.enter_context(mock.patch.object(target, name, **kwargs))
                    patch(producer, "REVIEWED_SCHEMA4_PRODUCT_SHA", new="a" * 40)
                    patch(producer, "snapshot", side_effect=capture)
                    patch(producer, "read_snapshot", side_effect=read)
                    patch(producer, "observe_installer", side_effect=observe)
                    patch(life, "read_pe_versions")
                    patch(life, "require_pe_version")
                    patch(life, "installed_registry")
                    patch(producer, "admit_default_install")
                    patch(producer, "admit_mixed_uninstall")
                    patch(producer, "admit_interrupted_state", return_value={})
                    patch(producer, "admit_recovered_state", return_value={})
                    patch(producer, "create_private_checkpoint_directory", return_value={})
                    patch(producer, "interrupt_observer", return_value=mock.Mock())
                    bootstrap = patch(producer, "bootstrap_previous",
                                      return_value=(terminal, "create-library-project/1"))
                    assemble = patch(producer, "assemble_updated_application_input",
                                     return_value=(input_path, "a" * 64))
                    observer = patch(producer, "invoke_updated_observer")
                    if case == "default-uninstall":
                        producer.run_default_uninstall_case(context, storage_consumer=consumer)
                        self.assertEqual(len(snapshots_seen), 4)
                        self.assertEqual(len(reads_seen), 4)
                        bootstrap.assert_not_called()
                        observer.assert_not_called()
                    else:
                        workspace = Path(temporary) / "workspace/nsis-physical/case"
                        producer.run_interruption_recovery_case(context, gate=Path(temporary),
                            gate_sha="b" * 40, oracle=Path(temporary),
                            oracle_sha=producer.REVIEWED_SCHEMA4_ORACLE_SHA, workspace=workspace,
                            storage_consumer=consumer)
                        self.assertEqual(len(snapshots_seen), 5)
                        self.assertEqual(len(reads_seen), 6)
                        self.assertEqual(bootstrap.call_args.kwargs, {"storage_cohort": before})
                        self.assertIs(assemble.call_args.kwargs["storage_consumer"], consumer)
                        observer.assert_called_once()

if __name__ == "__main__":
    unittest.main()
