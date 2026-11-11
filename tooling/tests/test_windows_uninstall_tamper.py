"""Offline tamper-consumer tests; synthetic PE/setup bytes are never executed."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installer_lifecycle as lifecycle
import windows_uninstall_tamper as tamper
from test_windows_installer_lifecycle import Fixture
import test_windows_installer_lifecycle as existing_lifecycle_tests


class TamperFixture:
    """Compose the existing immutable fixture API without changing its class."""
    def __init__(self, base):
        self.producer_root = base / "producer"
        self.evidence_root = base / "case"
        self.producer_root.mkdir()
        self.evidence_root.mkdir()
        self.fx = Fixture(self.producer_root)
        self.serial = 0
        self.targets = {
            "main": "motionwright-desktop.exe",
            "resource": "mw-ci-render/resource-alpha.dat",
            "external_binary": "mw-ci-current-sidecar.exe",
        }
        resource = self.fx.blob("resource-alpha.bin", b"authentic synthetic resource bytes")
        binary = self.fx.blob("current-sidecar.bin", b"synthetic external PE; never executable")
        self.source_files = {
            self.targets["main"]: self.fx.new_main,
            self.targets["resource"]: resource,
            self.targets["external_binary"]: binary,
        }
        for role, relative in self.targets.items():
            self.fx.compiled_doc["current_payloads"][relative] = {
                "kind": role, **self.fx.ident(self.source_files[relative]),
                "symbol": {"main": "MW_MAIN", "resource": "MW_RESOURCE_0",
                           "external_binary": "MW_BINARY_0"}[role],
            }
            self.fx.new_files[relative] = self.source_files[relative]
        defines = self.fx.rendered.split("Section Install\n", 1)[0]
        rendered = defines + existing_lifecycle_tests.WindowsLifecycleTests.resource_render(
            [self.targets["resource"]])
        rendered_ref = self.fx.blob("tamper-render.nsi", rendered.encode("utf-8-sig"))
        self.fx.admission["refs"]["rendered"] = rendered_ref
        self.fx.compiled_doc["rendered_installer"] = self.fx.ident(rendered_ref)
        self.fx.edit_admission_doc("compiled", self.fx.compiled_doc)
        self.fx.context = self.fx.validate_inputs()
        admission_ref = self.producer_json("admission.json", self.fx.admission)
        self.fx.admission_digest = admission_ref["sha256"]
        self.fx.record = self.fx.make_record()
        record_ref = self.producer_json("record.json", self.fx.record)
        result_ref = self.producer_json("result.json", {
            "schema": "motionwright.windows-lifecycle-verification/1",
            "source_before": self.fx.source_record, "source_after": self.fx.source_record,
            "admission": admission_ref, "record": record_ref, "result": self.fx.verify(),
        })
        self.producer_refs = {"admission": admission_ref, "record": record_ref, "result": result_ref}
        self.binding = {
            "artifact_id": 123456, "artifact_name": tamper.ARTIFACT_NAME,
            "artifact_bytes": 4096, "artifact_sha256": "e" * 64,
            "source_sha": self.fx.source, "run": copy.deepcopy(self.fx.run),
        }

    def producer_json(self, name, value):
        return self.fx.blob(name, (json.dumps(value, sort_keys=True) + "\n").encode())

    def blob(self, body, label="bytes"):
        self.serial += 1
        name = f"{label}-{self.serial}.original"
        path = self.evidence_root / name
        with path.open("xb") as stream:
            stream.write(body)
        return {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    def js(self, value):
        return self.blob((json.dumps(value, sort_keys=True) + "\n").encode(), "record")

    def copy_original(self, reference):
        return self.blob(lifecycle.bound_file(self.producer_root, reference).read_bytes())

    def make(self, role="resource"):
        case_id = str(uuid.uuid4())
        install_root = r"E:\isolated-fixtures" + "\\" + case_id
        database = r"E:\normal-profile\com.seradotcom.motionwright\motionwright.sqlite3"
        files = {name: self.copy_original(ref) for name, ref in self.source_files.items()}
        files["uninstall.exe"] = self.copy_original(self.fx.new_self)
        family = {name: None if ref is None else self.copy_original(ref)
                  for name, ref in self.fx.family.items()}
        project = copy.deepcopy(self.fx.project)
        keys = self.fx.context["registry_keys"]
        empty_registry = {view: {key: None for key in keys} for view in ("32", "64")}
        registry = copy.deepcopy(empty_registry)
        registry["64"][keys[0]] = {
            name: {"type": 1, "value": value} for name, value in {
                "DisplayName": "Motionwright", "DisplayVersion": "0.1.1",
                "InstallLocation": '"' + install_root + '"',
                "UninstallString": '"' + install_root + r'\uninstall.exe"',
            }.items()
        }
        registry["64"][keys[1]] = {"": {"type": 1, "value": install_root}}
        target = self.targets[role]
        pristine = lifecycle.bound_file(self.evidence_root, files[target]).read_bytes()
        changed_ref = self.blob(bytes([pristine[0] ^ 1]) + pristine[1:])
        foreign = self.blob(b"Foreign fixture bytes remain after a refused uninstall.\n")
        foreign_name = "user-created-foreign.txt"
        changed_files = {**files, target: changed_ref, foreign_name: foreign}
        record = {
            "schema": tamper.SCHEMA, "scope": tamper.SCOPE, "case_id": case_id,
            "source_sha": self.fx.source, "source_tree": self.fx.tree,
            "run": copy.deepcopy(self.fx.run), "role": role,
            "producer_binding": copy.deepcopy(self.binding),
            "producer_refs": copy.deepcopy(self.producer_refs),
            "family_origin": {
                "source_fixture": self.fx.record["operations"][1]["after"],
                "project_origin": self.fx.record["project_origin"],
            },
            "mutation": {"relative": target, "offset": 0},
            "foreign_fixture": {"relative": foreign_name, "reference": foreign},
            "source_before": copy.deepcopy(self.fx.source_record),
            "source_after": copy.deepcopy(self.fx.source_record),
            "claims": copy.deepcopy(tamper.CLAIMS), "operations": [],
        }
        setup = str(lifecycle.bound_file(self.producer_root, self.fx.admission["refs"]["current_setup"]))
        for index, phase in enumerate(("install", "uninstall")):
            oid = str(uuid.uuid4())
            process = {"pid": 700 + index, "creation_filetime": 123456700 + index}
            executable = self.fx.ident(self.fx.new_setup if phase == "install" else files["uninstall.exe"])
            executable_path = setup if phase == "install" else str(PureWindowsPath(install_root) / "uninstall.exe")
            original = lifecycle.observed_terminal(0 if phase == "install" else 2)
            context = {"operation_id": oid, "process": process, "executable": executable,
                       "source_sha": self.fx.source, "run": self.fx.run}
            terminal = self.js({"schema": "motionwright.windows-lifecycle-terminal/1",
                "phase": phase, "context": context, "original_terminal": original})
            intent = self.js({
                "schema": tamper.INTENT_SCHEMA, "source_sha": self.fx.source, "run": self.fx.run,
                "case_id": case_id, "role": role, "phase": phase,
                "command": tamper.COMMANDS[phase], "operation_id": oid,
                "executable": executable, "executable_path": executable_path,
                "arguments": ["/S", ("/D=" if phase == "install" else "_?=") + install_root],
                "launch_limit": 1, "replay_authorized": False,
            })
            before_files = {} if phase == "install" else changed_files
            after_files = files if phase == "install" else changed_files
            snapshots = []
            for position, inventory, reg in (
                ("before", before_files, empty_registry if phase == "install" else registry),
                ("after", after_files, registry),
            ):
                snapshots.append(self.js({
                    "schema": "motionwright.windows-lifecycle-snapshot/1",
                    "operation_id": oid, "position": position,
                    "install_root": install_root, "database": database,
                    "installed": inventory, "registry": reg,
                    "sqlite_family": family, "project": project,
                }))
            record["operations"].append({
                "phase": phase, "operation_id": oid, "launch_count": 1,
                "command": tamper.COMMANDS[phase], "process": process,
                "executable": executable, "executable_path": executable_path,
                "terminal": original, "terminal_receipt": terminal, "custody_errors": [],
                "before": snapshots[0], "after": snapshots[1], "intent": intent,
            })
        return record

    def verify(self, record, **overrides):
        kwargs = {
            "producer_root": self.producer_root, "producer_binding": self.binding,
            "expected_source": self.fx.source, "expected_run": self.fx.run,
            "expected_role": record["role"], "pe_reader": self.fx.pe,
        }
        kwargs.update(overrides)
        return tamper.validate_uninstall_tamper(self.evidence_root, record, **kwargs)

    def edit_snapshot(self, record, operation, position, edit):
        row = record["operations"][operation]
        value = lifecycle.bound_json(self.evidence_root, row[position])
        edit(value)
        row[position] = self.js(value)

    def edit_intent(self, record, operation, edit):
        row = record["operations"][operation]
        value = lifecycle.bound_json(self.evidence_root, row["intent"])
        edit(value)
        row["intent"] = self.js(value)

    def change_terminal(self, record, terminal):
        row = record["operations"][1]
        row["terminal"] = terminal
        value = lifecycle.bound_json(self.evidence_root, row["terminal_receipt"])
        value["original_terminal"] = terminal
        row["terminal_receipt"] = self.js(value)


class WindowsUninstallTamperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motionwright-tamper-consumer-")
        self.addCleanup(self.temp.cleanup)
        self.fx = TamperFixture(Path(self.temp.name))

    def baseline(self, role="resource"):
        record = self.fx.make(role)
        self.assertEqual(self.fx.verify(record)["status"], "SOURCE_BOUND_TAMPER_REFUSAL_OBSERVATIONS")
        return record

    def test_three_roles_retain_original_error_and_limited_scope(self):
        for role in tamper.ROLES:
            with self.subTest(role=role):
                result = self.fx.verify(self.fx.make(role))
                self.assertEqual(result["role"], role)
                self.assertEqual(result["original_terminal"], lifecycle.observed_terminal(2))
                self.assertEqual(result["directory_preservation"], "NOT_OBSERVED")
                self.assertEqual(result["project_observation_scope"], "HISTORICAL_PREDECESSOR_PRESERVATION")
                self.assertEqual(result["current_application_storage_open"], "NOT_RUN")
                self.assertEqual(result["current_application_storage_migration"], "NOT_RUN")
                self.assertEqual(result["human_acceptance"], "NOT_RUN")
                self.assertFalse(result["release_published"])

    def test_admit_producer_recalculates_originals_and_wrapper_binding(self):
        result = tamper.admit_producer(
            self.fx.producer_root, self.fx.producer_refs, self.fx.binding,
            self.fx.fx.source, self.fx.fx.run, pe_reader=self.fx.fx.pe)
        self.assertEqual(set(result), {"inputs", "record", "result", "compiled", "source"})
        wrapper = lifecycle.bound_json(self.fx.producer_root, self.fx.producer_refs["result"])
        wrapper["result"]["project_preserved_during_installer_operations"] = False
        body = (json.dumps(wrapper, sort_keys=True) + "\n").encode()
        path = self.fx.producer_root / "result.json"
        path.write_bytes(body)
        refs = copy.deepcopy(self.fx.producer_refs)
        refs["result"] = {"path": "result.json", "bytes": len(body),
                          "sha256": hashlib.sha256(body).hexdigest()}
        with self.assertRaisesRegex(lifecycle.LifecycleError, "tamper_producer_result_binding"):
            tamper.admit_producer(self.fx.producer_root, refs, self.fx.binding,
                self.fx.fx.source, self.fx.fx.run, pe_reader=self.fx.fx.pe)

    def test_independent_artifact_and_source_identity_are_required(self):
        baseline = self.baseline()
        for key, value, reason in (
            ("artifact_id", 123457, "binding_changed"),
            ("artifact_name", "another-artifact", "artifact_name"),
            ("artifact_bytes", True, "artifact_size"),
            ("artifact_sha256", "f" * 64, "binding_changed"),
            ("source_sha", "f" * 40, "artifact_source"),
            ("run", {**self.fx.fx.run, "run_attempt": 2}, "artifact_run"),
        ):
            record = copy.deepcopy(baseline)
            record["producer_binding"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                self.fx.verify(record)

    def test_source_checkout_or_family_reference_cannot_be_relabelled(self):
        baseline = self.baseline()
        for mode, reason in (("source", "source_observation_changed"),
                             ("family", "family_origin_binding")):
            record = copy.deepcopy(baseline)
            if mode == "source":
                record["source_after"]["tree"] = "f" * 40
            else:
                record["family_origin"]["source_fixture"] = self.fx.fx.record["operations"][2]["after"]
            with self.subTest(mode=mode), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                self.fx.verify(record)

    def test_target_role_and_symbol_must_match_compiled_original(self):
        inputs = self.fx.fx.context
        compiled = self.fx.fx.compiled_doc
        for role, relative in self.fx.targets.items():
            self.assertEqual(tamper.validate_target_role(compiled, inputs, role, relative)["kind"], role)
        for role, relative, edit, reason in (
            ("main", self.fx.targets["resource"], {}, "target_role"),
            ("resource", self.fx.targets["resource"], {"symbol": "MW_BINARY_0"}, "target_symbol"),
            ("resource", self.fx.targets["resource"], {"symbol": "MW_RESOURCE_00"}, "target_symbol"),
            ("external_binary", self.fx.targets["external_binary"], {"sha256": "f" * 64}, "payload_binding"),
        ):
            changed = copy.deepcopy(compiled)
            changed["current_payloads"][relative].update(edit)
            with self.subTest(role=role, edit=edit), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                tamper.validate_target_role(changed, inputs, role, relative)

    def test_exact_xor_one_byte_is_required_not_just_length_and_digest(self):
        baseline = self.baseline()
        target = baseline["mutation"]["relative"]
        pristine = lifecycle.bound_json(self.fx.evidence_root, baseline["operations"][0]["after"])
        body = lifecycle.bound_file(self.fx.evidence_root, pristine["installed"][target]).read_bytes()
        variants = [
            (body, "same_size_changed_identity"),
            (bytes([body[0] ^ 2]) + body[1:], "not_one_xor_byte"),
            (bytes([body[0] ^ 1, body[1] ^ 1]) + body[2:], "not_one_xor_byte"),
            (bytes([body[0] ^ 1]) + body[1:] + b"x", "same_size_changed_identity"),
        ]
        for changed, reason in variants:
            record = copy.deepcopy(baseline)
            ref = self.fx.blob(changed)
            for position in ("before", "after"):
                self.fx.edit_snapshot(record, 1, position,
                    lambda snapshot, ref=ref: snapshot["installed"].__setitem__(target, ref))
            with self.subTest(reason=reason, size=len(changed)), self.assertRaisesRegex(
                    lifecycle.LifecycleError, reason):
                self.fx.verify(record)

    def test_offset_requires_strict_in_range_integer(self):
        baseline = self.baseline()
        for offset in (True, -1, 10**9):
            record = copy.deepcopy(baseline)
            record["mutation"]["offset"] = offset
            with self.subTest(offset=offset), self.assertRaisesRegex(lifecycle.LifecycleError, "tamper_offset"):
                self.fx.verify(record)

    def test_exit_zero_does_not_prove_refusal(self):
        record = self.baseline()
        self.fx.change_terminal(record, lifecycle.observed_terminal(0))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "tamper_uninstall_terminal"):
            self.fx.verify(record)

    def test_exit_three_and_timeouts_stay_unknown_even_with_matching_files(self):
        baseline = self.baseline()
        for terminal in (lifecycle.observed_terminal(3),
                         lifecycle.observed_terminal(None, timed_out=True),
                         lifecycle.observed_terminal(None)):
            record = copy.deepcopy(baseline)
            self.fx.change_terminal(record, terminal)
            with self.subTest(terminal=terminal), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "tamper_uninstall_terminal"):
                self.fx.verify(record)
            self.assertEqual(record["operations"][1]["terminal"]["state"], "UNKNOWN")

    def test_forged_error_label_cannot_hide_actual_zero(self):
        record = self.baseline()
        self.fx.change_terminal(record, {"state": "ERROR", "exit_code": 0, "reason": "EXIT_OBSERVED"})
        with self.assertRaisesRegex(lifecycle.LifecycleError, "does_not_match_original_exit"):
            self.fx.verify(record)

    def test_update_or_substituted_arguments_cannot_supply_exit_two(self):
        baseline = self.baseline()
        for arguments in (["/S", "/UPDATE"], ["/S", "_?=E:\\another-install"]):
            record = copy.deepcopy(baseline)
            self.fx.edit_intent(record, 1, lambda intent: intent.__setitem__("arguments", arguments))
            with self.subTest(arguments=arguments), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "tamper_intent_binding"):
                self.fx.verify(record)

    def test_setup_and_original_self_executable_are_bound(self):
        baseline = self.baseline()
        for operation, field, value in (
            (0, "executable", {"bytes": 9, "sha256": "a" * 64}),
            (1, "executable_path", r"E:\another\uninstall.exe"),
        ):
            record = copy.deepcopy(baseline)
            record["operations"][operation][field] = value
            with self.subTest(operation=operation), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "tamper_executable_binding"):
                self.fx.verify(record)

    def test_original_terminal_receipt_cannot_be_substituted(self):
        record = self.baseline()
        terminal = lifecycle.bound_json(self.fx.evidence_root, record["operations"][1]["terminal_receipt"])
        terminal["context"]["source_sha"] = "f" * 40
        record["operations"][1]["terminal_receipt"] = self.fx.js(terminal)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "tamper_terminal_receipt_binding"):
            self.fx.verify(record)

    def test_replay_and_incomplete_custody_do_not_receive_credit(self):
        baseline = self.baseline()
        for field, value, reason in (
            ("launch_count", 2, "operation_replay"),
            ("launch_count", True, "operation_replay"),
            ("custody_errors", [{"stage": "post_effect_observation"}], "custody_incomplete"),
        ):
            record = copy.deepcopy(baseline)
            record["operations"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                self.fx.verify(record)
            self.assertEqual(record["operations"][1]["terminal"], lifecycle.observed_terminal(2))

    def test_existing_install_registration_or_payload_breaks_freshness(self):
        baseline = self.baseline()
        for mode in ("file", "registration"):
            record = copy.deepcopy(baseline)
            after = lifecycle.bound_json(self.fx.evidence_root, record["operations"][0]["after"])
            self.fx.edit_snapshot(record, 0, "before", lambda snapshot:
                snapshot.__setitem__("installed" if mode == "file" else "registry",
                                     after["installed"] if mode == "file" else after["registry"]))
            with self.subTest(mode=mode), self.assertRaisesRegex(lifecycle.LifecycleError, "install_not_fresh"):
                self.fx.verify(record)

    def test_installed_payload_set_must_equal_compiled_inventory(self):
        record = self.baseline()
        missing = self.fx.targets["external_binary"]
        self.fx.edit_snapshot(record, 0, "after", lambda snapshot: snapshot["installed"].pop(missing))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "fresh_install_payloads"):
            self.fx.verify(record)

    def test_other_payload_cannot_change_between_install_and_refusal(self):
        record = self.baseline()
        changed = self.fx.blob(b"another uninstaller; not the generated original")
        for position in ("before", "after"):
            self.fx.edit_snapshot(record, 1, position,
                lambda snapshot: snapshot["installed"].__setitem__("uninstall.exe", changed))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "unrelated_file_changed"):
            self.fx.verify(record)

    def test_refusal_preserves_complete_observed_file_set(self):
        baseline = self.baseline()
        for mode in ("missing-payload", "missing-foreign", "extra", "changed"):
            record = copy.deepcopy(baseline)
            def edit(snapshot):
                if mode == "missing-payload":
                    snapshot["installed"].pop(self.fx.targets["main"])
                elif mode == "missing-foreign":
                    snapshot["installed"].pop(record["foreign_fixture"]["relative"])
                elif mode == "extra":
                    snapshot["installed"]["extra.dat"] = self.fx.blob(b"unexpected extra bytes")
                else:
                    snapshot["installed"][self.fx.targets["external_binary"]] = self.fx.blob(b"changed")
            self.fx.edit_snapshot(record, 1, "after", edit)
            with self.subTest(mode=mode), self.assertRaisesRegex(lifecycle.LifecycleError, "files_not_preserved"):
                self.fx.verify(record)

    def test_uninstall_registration_must_be_present_and_preserved(self):
        baseline = self.baseline()
        key = self.fx.fx.context["registry_keys"][0]
        for mode in ("absent-before-and-after", "changed-after"):
            record = copy.deepcopy(baseline)
            if mode == "absent-before-and-after":
                for position in ("before", "after"):
                    self.fx.edit_snapshot(record, 1, position,
                        lambda snapshot: [view.__setitem__(key, None)
                                          for view in snapshot["registry"].values()])
                reason = "registry_install_missing"
            else:
                def edit(snapshot):
                    snapshot["registry"]["32"][self.fx.fx.context["registry_keys"][1]] = {
                        "changed": {"type": 1, "value": "unexpected"}}
                self.fx.edit_snapshot(record, 1, "after", edit)
                reason = "tamper_registry_changed"
            with self.subTest(mode=mode), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                self.fx.verify(record)

    def test_sqlite_wal_shm_and_project_must_match_nonvacuous_original(self):
        baseline = self.baseline()
        for member in ("motionwright.sqlite3", "motionwright.sqlite3-wal", "motionwright.sqlite3-shm", "project"):
            record = copy.deepcopy(baseline)
            def edit(snapshot):
                if member == "project":
                    snapshot["project"]["generation"] = str(uuid.uuid4())
                else:
                    snapshot["sqlite_family"][member] = None
            self.fx.edit_snapshot(record, 1, "after", edit)
            with self.subTest(member=member), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "project_or_sqlite_family_changed"):
                self.fx.verify(record)

    def test_two_equally_empty_sqlite_families_do_not_prove_preservation(self):
        record = self.baseline()
        def empty(snapshot):
            snapshot["sqlite_family"] = {key: None for key in snapshot["sqlite_family"]}
            snapshot["project"] = None
        for operation in (0, 1):
            for position in ("before", "after"):
                self.fx.edit_snapshot(record, operation, position, empty)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "project_or_sqlite_family_changed"):
            self.fx.verify(record)

    def test_changed_retained_bytes_are_not_trusted_from_reference(self):
        record = self.baseline()
        before = lifecycle.bound_json(self.fx.evidence_root, record["operations"][1]["before"])
        ref = before["installed"][record["mutation"]["relative"]]
        path = self.fx.evidence_root / ref["path"]
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with self.assertRaisesRegex(lifecycle.LifecycleError, "retained_bytes_mismatch"):
            self.fx.verify(record)

    def test_scope_claims_and_snapshot_locations_are_closed(self):
        baseline = self.baseline()
        for mode, reason in (("claims", "tamper_claims"), ("path", "snapshot_location_changed"),
                             ("role", "tamper_role")):
            record = copy.deepcopy(baseline)
            if mode == "claims":
                record["claims"]["human_acceptance"] = "PASS"
            elif mode == "role":
                record["role"] = "uninstaller"
            else:
                self.fx.edit_snapshot(record, 1, "after",
                    lambda snapshot: snapshot.__setitem__("install_root", r"E:\other"))
            with self.subTest(mode=mode), self.assertRaisesRegex(lifecycle.LifecycleError, reason):
                self.fx.verify(record, expected_role="resource")


if __name__ == "__main__":
    unittest.main()
