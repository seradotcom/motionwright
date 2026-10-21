"""Offline lifecycle consumer regressions. All installer/PE rows are synthetic."""
from __future__ import annotations
import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_installer_lifecycle as lifecycle
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_storage_schema_contract import cohort as storage_cohort_fixture
from windows_cold_start import ColdStartError
from release_manifest import ManifestError


class Fixture:
    def __init__(self, root):
        self.root = root
        self.serial = 0
        self.source = "a" * 40
        self.tree = "b" * 40
        self.old_source = "c" * 40
        self.old_tree = "d" * 40
        self.run = {"repository": "example/motionwright", "run_id": 42, "run_attempt": 1}
        self.old_main = self.blob("previous-main.exe", b"synthetic old PE; never executable")
        self.new_main = self.blob("current-main.exe", b"synthetic new PE; never executable")
        self.old_self = self.blob("previous-self.exe", b"synthetic old self")
        self.new_self = self.blob("current-self.exe", b"synthetic new self")
        self.foreign = self.blob("foreign.txt", b"foreign user bytes; preserve exactly")
        self.old_setup = self.blob("Motionwright_0.1.0_x64-setup.exe", b"synthetic old setup")
        self.new_setup = self.blob("Motionwright_0.1.1_x64-setup.exe", b"synthetic new setup")
        self.source_record = {"checkout": self.source, "tree": self.tree, "files": {
            name: {"bytes": 1, "sha256": "e" * 64} for name in lifecycle.SOURCE_PATHS}}
        old_meta = {"source_revision": self.old_source, "version": "0.1.0"}
        self.previous = {"source_sha": self.old_source,
            "source_before": {"checkout": self.old_source, "tree": self.old_tree},
            "source_after": {"checkout": self.old_source, "tree": self.old_tree},
            "candidate": {"candidate": old_meta}, "candidate_after": {"candidate": old_meta}}
        self.previous_ref = self.js("previous-capture", self.previous)
        self.profile = {"schema": "motionwright.nsis-predecessor/1",
            "identity": {"application": "Motionwright", "bundle_id": "com.seradotcom.motionwright", "arch": "x64"},
            "version": "0.1.0", "source_sha": self.old_source, "source_tree": self.old_tree,
            "target": {"version": "0.1.1", "main_sha256": self.new_main["sha256"]},
            "package": {"path": self.old_setup["path"], **self.ident(self.old_setup)},
            "capture_receipt": self.previous_ref,
            "files": [
                {"relative": "motionwright-desktop.exe", "source": self.old_main["path"],
                 "kind": "main", **self.ident(self.old_main)},
                {"relative": "uninstall.exe", "source": self.old_self["path"],
                 "kind": "uninstaller", **self.ident(self.old_self)}]}
        self.profile_ref = self.js("profile", self.profile)
        self.overlay_ref = self.js("version-overlay", {"version": "0.1.1"})
        self.inputs_doc = {"source_sha": self.source, "source_before": self.source_record,
            "source_after": self.source_record, "profile": self.profile,
            "profile_sha256": self.profile_ref["sha256"], "source_version": "0.1.0",
            "version_scope": "ISOLATED_INSTALLER_AND_PE_FIXTURE",
            "version_overlay": self.ident(self.overlay_ref)}
        self.inputs_ref = self.js("inputs", self.inputs_doc)
        self.candidate_doc = {"schema_version": 1, "candidate": {
            "source_revision": self.source, "application": "Motionwright",
            "version": "0.1.0", "platform": "windows", "arch": "x86_64"},
            "artifacts": [{"bundle_kind": "nsis", "filename": self.new_setup["path"],
                           **self.ident(self.new_setup)}]}
        self.candidate_ref = self.js("candidate", self.candidate_doc)
        defines = {"VERSION": "0.1.1", "PRODUCTNAME": "Motionwright",
            "BUNDLEID": "com.seradotcom.motionwright", "ARCH": "x64",
            "INSTALLMODE": "currentUser", "MAINBINARYNAME": "motionwright-desktop",
            "MANUFACTURER": "seradotcom"}
        self.rendered = "\n".join(f'!define {key} "{value}"' for key, value in defines.items()) + "\n"
        self.rendered += ("Section Install\n"
                          '!insertmacro MWINST_INSTALL_PAYLOAD "${MAINBINARYNAME}.exe" MW_MAIN\n'
                          "SectionEnd\nSection Uninstall\nSectionEnd\n")
        self.rendered_ref = self.blob("installer.nsi", self.rendered.encode("utf-8-sig"))
        self.compiled_doc = {"schema": "motionwright-windows-nsis-compiled-fixture/1",
            "status": "COMPILED_SOURCE_BOUND", "source_sha": self.source, "source_tree": self.tree,
            "source_before": self.source_record, "source_after": self.source_record,
            "inputs_receipt": self.ident(self.inputs_ref), "candidate_receipt": self.ident(self.candidate_ref),
            "setup": self.ident(self.new_setup), "rendered_installer": self.ident(self.rendered_ref),
            "predecessor_profile": self.ident(self.profile_ref), "profile_sha256": self.profile_ref["sha256"],
            "current_payloads": {"motionwright-desktop.exe": {"kind": "main",
                                 **self.ident(self.new_main)}}}
        self.compiled_ref = self.js("compiled", self.compiled_doc)
        self.admission = {"schema": "motionwright.windows-lifecycle-inputs/1",
            "source_sha": self.source, "source_tree": self.tree, "run": self.run,
            "refs": {"compiled": self.compiled_ref, "inputs": self.inputs_ref,
                "candidate": self.candidate_ref, "profile": self.profile_ref,
                "rendered": self.rendered_ref, "previous_setup": self.old_setup,
                "current_setup": self.new_setup, "previous_capture": self.previous_ref,
                "previous_main": self.old_main, "current_main": self.new_main,
                "version_overlay": self.overlay_ref}}
        self.context = self.validate_inputs()
        self.admission_digest = self.js("admission", self.admission)["sha256"]
        self.install_path = str(root.parent / "installed-fixture")
        self.database_path = str(root.parent / "profile" / "motionwright.sqlite3")
        self.project = {"project_id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
            "revision": 0, "title": "motionwright ci first use", "document_sha256": "f" * 64}
        self.db_ref = self.blob("project.sqlite3", b"SQLite format 3\0synthetic preservation bytes")
        self.wal_ref = self.blob("project.sqlite3-wal", b"synthetic WAL bytes")
        self.shm_ref = self.blob("project.sqlite3-shm", b"")
        self.family = {"motionwright.sqlite3": self.db_ref,
            "motionwright.sqlite3-wal": self.wal_ref, "motionwright.sqlite3-shm": self.shm_ref}
        self.old_files = {"motionwright-desktop.exe": self.old_main, "uninstall.exe": self.old_self}
        self.new_files = {"motionwright-desktop.exe": self.new_main,
                          "uninstall.exe": self.new_self, "foreign.txt": self.foreign}
        self.record = self.make_record()

    @staticmethod
    def ident(ref):
        return {key: ref[key] for key in ("bytes", "sha256")}

    def blob(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(body)
        return {"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    def js(self, label, value):
        self.serial += 1
        return self.blob(f"{label}-{self.serial}.json",
                         (json.dumps(value, sort_keys=True) + "\n").encode())

    def pe(self, path):
        ref = self.old_main if path.name == self.old_main["path"] else self.new_main
        v = [0, 1, 0, 0] if ref is self.old_main else [0, 1, 1, 0]
        return {"binary": self.ident(ref), "file_version": v, "product_version": v,
                "strings": {"040904b0": {"FileVersion": ".".join(map(str, v)),
                                       "ProductVersion": ".".join(map(str, v[:3]))}}}

    def validate_inputs(self, pe_reader=None):
        return lifecycle.validate_inputs(self.root, self.admission, self.source, self.run,
                                         pe_reader=pe_reader or self.pe)

    def registry(self, v=None, *, metadata=True):
        uninstall, product = self.context["registry_keys"]
        values = None if v is None else {name: {"type": 1, "value": value} for name, value in {
            "DisplayVersion": v, "DisplayName": "Motionwright",
            "InstallLocation": '"' + self.install_path + '"',
            "UninstallString": '"' + self.install_path + r'\uninstall.exe"'}.items()}
        meta = {"": {"type": 1, "value": self.install_path}} if metadata else None
        return {"32": {uninstall: None, product: None},
                "64": {uninstall: values, product: meta}}

    def snapshot(self, operation, position, files, reg, project=False):
        return {"schema": "motionwright.windows-lifecycle-snapshot/1",
            "operation_id": operation, "position": position,
            "install_root": self.install_path, "database": self.database_path,
            "installed": files, "registry": reg,
            "sqlite_family": self.family if project else {name: None for name in self.family},
            "project": self.project if project else None}

    def make_record(self):
        operations = []
        for index, phase in enumerate(lifecycle.PHASES):
            oid = str(uuid.uuid4())
            before_files = ({}, {**self.old_files, "foreign.txt": self.foreign}, self.new_files)[index]
            after_files = (self.old_files, self.new_files,
                           {"foreign.txt": self.foreign, "uninstall.exe": self.new_self})[index]
            before_reg = (self.registry(None, metadata=False), self.registry("0.1.0"),
                          self.registry("0.1.1"))[index]
            after_reg = (self.registry("0.1.0"), self.registry("0.1.1"),
                         self.registry(None))[index]
            before = self.snapshot(oid, "before", before_files, before_reg, index > 0)
            after = self.snapshot(oid, "after", after_files, after_reg, index > 0)
            executable = self.ident((self.old_setup, self.new_setup, self.new_self)[index])
            process = {"pid": index + 100, "creation_filetime": 123456789 + index}
            terminal = lifecycle.observed_terminal(0)
            context = {"operation_id": oid, "process": process, "executable": executable,
                       "source_sha": self.source, "run": self.run}
            terminal_ref = self.js("terminal", {"schema": "motionwright.windows-lifecycle-terminal/1",
                "phase": phase, "context": context, "original_terminal": terminal})
            operations.append({"phase": phase, "operation_id": oid, "launch_count": 1,
                "command": ("nsis_install_previous", "nsis_upgrade_current",
                            "nsis_uninstall_current_in_place")[index],
                "process": process, "executable": executable, "terminal": terminal,
                "terminal_receipt": terminal_ref, "custody_errors": [],
                "before": self.js("snapshot-before", before), "after": self.js("snapshot-after", after)})
        origin = {"schema": "motionwright-windows-nsis-cold-relaunch/1",
            "source_sha": self.old_source, "installed_binary_sha256": self.old_main["sha256"],
            "first_use_contract": lifecycle.CURRENT_FIRST_USE_CONTRACT,
            "first_launch_real_visible_hwnd": True, "second_launch_real_visible_hwnd": True,
            "separate_process_lifetimes": True, "same_project_generation_revision_document": True,
            "project": self.project, "human_install_acceptance": "NOT_RUN"}
        return {"schema": "motionwright.windows-installer-lifecycle/1",
            "source_sha": self.source, "source_tree": self.tree, "run": self.run,
            "admission_sha256": self.admission_digest,
            "scope": "DISPOSABLE_WINDOWS_FIXTURE_UPGRADE", "operations": operations,
            "project_origin": self.js("project-origin", origin),
            "interruption": {"status": "NOT_RUN", "reason": "OBSERVABLE_PID_START_GUID_CHECKPOINT_REQUIRED"},
            "claims": {"human_acceptance": "NOT_RUN", "published_product_upgrade": "NOT_CLAIMED",
                       "release_published": False}}

    def verify(self):
        return lifecycle.validate_lifecycle(self.root, self.record, self.context, self.admission_digest)

    def edit_snapshot(self, index, position, mutate):
        row = self.record["operations"][index]
        doc = lifecycle.bound_json(self.root, row[position])
        mutate(doc)
        row[position] = self.js("edited-snapshot", doc)

    def edit_admission_doc(self, role, doc):
        self.admission["refs"][role] = self.js("edited-" + role, doc)


class WindowsLifecycleTests(unittest.TestCase):
    @staticmethod
    def resource_render(paths, *, create=None, remove=None):
        parents = {path.rpartition("/")[0] for path in paths}
        ancestors = set()
        for parent in parents:
            while parent:
                ancestors.add(parent)
                parent = parent.rpartition("/")[0]
        create = sorted(parents) if create is None else create
        remove = sorted(ancestors, key=lambda item: (-len(item.split("/")), item)) if remove is None else remove
        lines = ["Section Install",
                 '!insertmacro MWINST_INSTALL_PAYLOAD "${MAINBINARYNAME}.exe" MW_MAIN']
        for parent in create:
            lines.extend([
                'StrCpy $MWINST_Relative "' + parent.replace("/", "\\") + '"',
                "ClearErrors", r'CreateDirectory "$INSTDIR\$MWINST_Relative"',
                "${If} ${Errors}",
                '!insertmacro MWINST_FAIL "A resource directory could not be created."',
                "${EndIf}"])
        for index, path in enumerate(paths):
            lines.append('!insertmacro MWINST_INSTALL_PAYLOAD "' + path.replace("/", "\\")
                         + '" MW_RESOURCE_' + str(index))
        lines.extend(["SectionEnd", "Section Uninstall"])
        for index, path in enumerate(paths):
            symbol = "MW_RESOURCE_" + str(index)
            lines.append('!insertmacro MWINST_DELETE_OWNED "' + path.replace("/", "\\")
                         + '" "${' + symbol + '_SHA256}" "${' + symbol + '_SIZE}"')
        for parent in remove:
            lines.extend(['StrCpy $MWINST_Relative "' + parent.replace("/", "\\") + '"',
                          r'RMDir "$INSTDIR\$MWINST_Relative"'])
        return "\n".join([*lines, "SectionEnd", ""])

    def test_rendered_resources_bind_directories_and_ancestors(self):
        paths = ["root.dat", "one/alpha.dat", "one/deeper/beta.dat", "two/gamma.dat"]
        result = lifecycle.validate_rendered_resource_directories(self.resource_render(paths), paths)
        self.assertEqual(set(result["install_directories"]), {"", "one", "one/deeper", "two"})
        self.assertEqual(set(result["uninstall_ancestors"]), {"one", "one/deeper", "two"})
        self.assertEqual(result["uninstall_ancestors"][0], "one/deeper")
        self.assertEqual(lifecycle.validate_rendered_resource_directories(
            self.resource_render([]), []),
            {"install_directories": [], "uninstall_ancestors": []})

    def test_rendered_resources_reject_residual_template_tokens(self):
        paths = ["mw-ci-render/resource-alpha.dat", "mw-ci-render/resource-beta.dat"]
        rendered = self.resource_render(paths)
        for operation in ("CreateDirectory", "RMDir"):
            with self.subTest(operation=operation):
                broken = rendered.replace(
                    operation + r' "$INSTDIR\$MWINST_Relative"',
                    operation + ' "$INSTDIR{{this}}"')
                with self.assertRaisesRegex(lifecycle.LifecycleError, "unexpanded_template_expression"):
                    lifecycle.validate_rendered_resource_directories(broken, paths)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "unexpanded_template_expression"):
            lifecycle.validate_rendered_resource_directories(rendered + "; {{unexpected}}\n", paths)

    def test_rendered_resources_reject_missing_directory_or_ancestor(self):
        paths = ["one/two/resource.dat"]
        for rendered in (self.resource_render(paths, create=[]),
                         self.resource_render(paths, remove=["one/two"])):
            with self.subTest(rendered=rendered):
                with self.assertRaisesRegex(lifecycle.LifecycleError, "mismatch"):
                    lifecycle.validate_rendered_resource_directories(rendered, paths)

    def test_rendered_resources_reject_extra_directory_or_ancestor(self):
        paths = ["one/resource.dat"]
        for rendered in (self.resource_render(paths, create=["one", "extra"]),
                         self.resource_render(paths, remove=["one", "extra"])):
            with self.subTest(rendered=rendered):
                with self.assertRaisesRegex(lifecycle.LifecycleError, "mismatch"):
                    lifecycle.validate_rendered_resource_directories(rendered, paths)

    def test_rendered_resources_reject_foreign_dynamic_or_alias_directory(self):
        paths = ["one/resource.dat"]
        for value in ("other", "$TEMP", "..\\outside", "one\\..\\outside"):
            with self.subTest(value=value):
                rendered = self.resource_render(paths).replace(
                    'StrCpy $MWINST_Relative "one"', 'StrCpy $MWINST_Relative "' + value + '"', 1)
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.validate_rendered_resource_directories(rendered, paths)

    def test_rendered_resources_reject_recursive_duplicate_or_unguarded_instructions(self):
        paths = ["one/two/resource.dat"]
        rendered = self.resource_render(paths)
        cases = [
            rendered.replace('RMDir "', 'RMDir /r "', 1),
            self.resource_render(paths, create=["one/two", "one/two"]),
            self.resource_render(paths, remove=["one", "one/two"]),
            rendered.replace("ClearErrors\nCreateDirectory", "CreateDirectory", 1),
            rendered.replace("${If} ${Errors}\n", "", 1),
            rendered.replace("CreateDirectory ", 'StrCpy $MWINST_Relative "other"\nCreateDirectory ', 1),
        ]
        for broken in cases:
            with self.subTest(rendered=broken):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.validate_rendered_resource_directories(broken, paths)

    def test_rendered_resources_reject_missing_section_or_unbound_resource_calls(self):
        paths = ["one/resource.dat"]
        rendered = self.resource_render(paths)
        for broken in (rendered.replace("Section Uninstall", "Section Other"),
                       rendered + "Section Install\nSectionEnd\n",
                       rendered.replace("MW_RESOURCE_0_SIZE", "MW_RESOURCE_1_SIZE"),
                       rendered.replace('"one\\resource.dat" MW_RESOURCE_0',
                                        '"other\\resource.dat" MW_RESOURCE_0')):
            with self.subTest(rendered=broken):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.validate_rendered_resource_directories(broken, paths)

    def test_unexpanded_render_is_rejected_before_pe_or_installer_observation(self):
        broken = self.fx.rendered.replace(
            "Section Install\n", 'Section Install\nCreateDirectory "$INSTDIR{{this}}"\n')
        reference = self.fx.blob("unexpanded-render.nsi", broken.encode("utf-8-sig"))
        self.fx.admission["refs"]["rendered"] = reference
        self.fx.compiled_doc["rendered_installer"] = self.fx.ident(reference)
        self.fx.edit_admission_doc("compiled", self.fx.compiled_doc)
        pe = mock.Mock(side_effect=AssertionError("PE observation must remain unsent"))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "unexpanded_template_expression"):
            self.fx.validate_inputs(pe)
        pe.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "evidence"
        self.root.mkdir()
        self.fx = Fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_synthetic_complete_contract_preserves_scope_and_source_version(self):
        result = self.fx.verify()
        self.assertEqual(result["status"], "SOURCE_BOUND_FIXTURE_OBSERVATIONS")
        self.assertEqual(result["source_version"], "0.1.0")
        self.assertEqual(result["fixture_installer_version"], "0.1.1")
        self.assertEqual(result["human_acceptance"], "NOT_RUN")
        self.assertEqual(result["interruption"], "NOT_RUN")
        self.assertFalse(result["release_published"])
        self.assertEqual(result["authenticity"], "REQUIRES_EXTERNAL_ORIGINAL_WORKFLOW_ADMISSION")

    def test_same_version_compiler_fixture_is_not_upgrade(self):
        self.fx.profile["target"]["version"] = "0.1.0"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "upgrade_versions"):
            self.fx.edit_admission_doc("profile", self.fx.profile)
            self.fx.inputs_doc["profile"] = self.fx.profile
            self.fx.inputs_doc["profile_sha256"] = self.fx.admission["refs"]["profile"]["sha256"]
            self.fx.edit_admission_doc("inputs", self.fx.inputs_doc)
            self.fx.compiled_doc["inputs_receipt"] = self.fx.ident(self.fx.admission["refs"]["inputs"])
            self.fx.compiled_doc["predecessor_profile"] = self.fx.ident(self.fx.admission["refs"]["profile"])
            self.fx.compiled_doc["profile_sha256"] = self.fx.admission["refs"]["profile"]["sha256"]
            self.fx.edit_admission_doc("compiled", self.fx.compiled_doc)
            self.fx.validate_inputs()

    def test_pe_file_version_mismatch_rejects_metadata_only_upgrade(self):
        def pe(path):
            value = self.fx.pe(path)
            if path.name == "current-main.exe":
                value["file_version"] = [0, 1, 0, 0]
            return value
        with self.assertRaisesRegex(lifecycle.LifecycleError, "pe_version_mismatch"):
            self.fx.validate_inputs(pe)

    def test_pe_product_version_mismatch_is_not_hidden_by_file_version(self):
        def pe(path):
            value = self.fx.pe(path)
            value["product_version"] = [9, 9, 9, 0]
            return value
        with self.assertRaisesRegex(lifecycle.LifecycleError, "pe_version_mismatch"):
            self.fx.validate_inputs(pe)

    def test_pe_version_string_disagrees_with_fixed_info(self):
        value = self.fx.pe(self.root / "current-main.exe")
        value["strings"]["040904b0"]["ProductVersion"] = "0.1.0"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "pe_string_version"):
            lifecycle.require_pe_version(value, "0.1.1", self.fx.ident(self.fx.new_main))

    def test_fixed_pe_versions_decode_both_actual_version_words(self):
        words = [0xFEEF04BD, 0x10000, 1, 65536, 1, 65536, 0, 0, 0, 0, 0, 0, 0]
        self.assertEqual(lifecycle.fixed_pe_versions(words),
                         {"file_version": [0, 1, 1, 0], "product_version": [0, 1, 1, 0]})
        words[0] = 0
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.fixed_pe_versions(words)

    def test_fixed_pe_versions_accept_nsis_zero_structure_and_reject_other_headers(self):
        # Fixed resource words observed in an original NSIS-compiled fixture.
        words = [0xFEEF04BD, 0, 1, 65536, 1, 65536, 0, 0, 4, 1, 0, 0, 0]
        expected = {"file_version": [0, 1, 1, 0], "product_version": [0, 1, 1, 0]}
        self.assertEqual(lifecycle.fixed_pe_versions(words), expected)
        standard = words.copy()
        standard[1] = 0x10000
        self.assertEqual(lifecycle.fixed_pe_versions(tuple(standard)), expected)
        for structure in (1, 0x20000):
            rejected = words.copy()
            rejected[1] = structure
            with self.subTest(structure=structure), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "^pe_fixed_info_signature$"):
                lifecycle.fixed_pe_versions(rejected)
        for signature in (0, 0xFEEF04BC):
            rejected = words.copy()
            rejected[0] = signature
            with self.subTest(signature=signature), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "^pe_fixed_info_signature$"):
                lifecycle.fixed_pe_versions(rejected)
        for index, value in ((0, True), (1, False), (2, True),
                             (1, -1), (1, 0x100000000)):
            rejected = words.copy()
            rejected[index] = value
            with self.subTest(index=index, value=value), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "^pe_fixed_info_invalid$"):
                lifecycle.fixed_pe_versions(rejected)
        for rejected in (words[:-1], [*words, 0]):
            with self.subTest(size=len(rejected)), self.assertRaisesRegex(
                    lifecycle.LifecycleError, "^pe_fixed_info_invalid$"):
                lifecycle.fixed_pe_versions(rejected)

    def test_noncanonical_versions_and_bool_sizes_are_rejected(self):
        for value in ("0.1.01", "0.1", "0.1.1-ci", "0.1.65536", True):
            with self.subTest(value=value), self.assertRaises(lifecycle.LifecycleError):
                lifecycle.version(value)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.identity({"bytes": True, "sha256": "a" * 64})

    def test_source_or_run_cannot_be_relabelled(self):
        for field, replacement in (("source_sha", "f" * 40), ("run", {**self.fx.run, "run_attempt": 2})):
            saved = self.fx.admission[field]
            self.fx.admission[field] = replacement
            with self.subTest(field=field), self.assertRaises(lifecycle.LifecycleError):
                self.fx.validate_inputs()
            self.fx.admission[field] = saved

    def test_missing_consumer_source_binding_is_rejected(self):
        self.fx.source_record["files"].pop("tooling/windows_installer_lifecycle.py")
        for role, doc in (("inputs", self.fx.inputs_doc), ("compiled", self.fx.compiled_doc)):
            self.fx.edit_admission_doc(role, doc)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "source_observation"):
            self.fx.validate_inputs()

    def test_tampered_package_and_reference_alias_are_rejected(self):
        (self.root / self.fx.new_setup["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "retained_bytes"):
            self.fx.validate_inputs()
        self.fx.new_setup["path"] = "../escape.exe"
        with self.assertRaises(ValueError):
            self.fx.validate_inputs()

    def test_duplicate_json_keys_are_rejected(self):
        ref = self.fx.blob("duplicate.json", b'{"a":1,"a":2}')
        with self.assertRaisesRegex(lifecycle.LifecycleError, "json_not_strict"):
            lifecycle.bound_json(self.root, ref)

    def test_missing_phase_and_unbound_terminal_are_rejected(self):
        self.fx.record["operations"].pop()
        with self.assertRaisesRegex(lifecycle.LifecycleError, "mandatory_lifecycle"):
            self.fx.verify()
        self.fx.record = self.fx.make_record()
        self.fx.record["operations"][1]["terminal_receipt"] = self.fx.record["operations"][0]["terminal_receipt"]
        with self.assertRaisesRegex(lifecycle.LifecycleError, "original_terminal_receipt"):
            self.fx.verify()

    def test_replay_count_or_reused_process_is_rejected(self):
        row = self.fx.record["operations"][1]
        row["launch_count"] = 2
        with self.assertRaisesRegex(lifecycle.LifecycleError, "operation_order_or_replay"):
            self.fx.verify()
        row["launch_count"] = 1
        row["process"] = self.fx.record["operations"][0]["process"]
        with self.assertRaisesRegex(lifecycle.LifecycleError, "process_identity_reused"):
            self.fx.verify()

    def test_exit_two_remains_original_error_and_never_complete(self):
        self.fx.record["operations"] = self.fx.record["operations"][:2]
        row = self.fx.record["operations"][1]
        row["terminal"] = lifecycle.observed_terminal(2)
        row["after"] = None
        result = self.fx.verify()
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["originals"][-1]["original_terminal"]["exit_code"], 2)
        self.assertEqual(result["originals"][-1]["original_terminal"]["state"], "ERROR")

    def test_exit_three_and_timeout_remain_unknown(self):
        for terminal in (lifecycle.observed_terminal(3),
                         lifecycle.observed_terminal(None, timed_out=True)):
            self.fx.record["operations"] = self.fx.record["operations"][:2]
            self.fx.record["operations"][1]["terminal"] = terminal
            result = self.fx.verify()
            self.assertEqual(result["originals"][-1]["original_terminal"], terminal)
            self.assertEqual(result["status"], "INCOMPLETE")

    def test_operation_after_unknown_is_forbidden(self):
        self.fx.record["operations"][1]["terminal"] = lifecycle.observed_terminal(None, timed_out=True)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "operation_after_error_or_unknown"):
            self.fx.verify()

    def test_unknown_cannot_be_relabelled_as_ack(self):
        self.fx.record["operations"][1]["terminal"] = {
            "state": "ACK", "exit_code": 3, "reason": "EXIT_OBSERVED"}
        with self.assertRaisesRegex(lifecycle.LifecycleError, "terminal_state_does_not"):
            self.fx.verify()

    def test_later_custody_error_blocks_complete_without_changing_ack(self):
        self.fx.record["operations"] = self.fx.record["operations"][:2]
        self.fx.record["operations"][1]["custody_errors"] = [{"stage": "after", "state": "UNKNOWN"}]
        result = self.fx.verify()
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["originals"][-1]["original_terminal"], lifecycle.observed_terminal(0))

    def test_installed_registry_version_must_match_real_fixture_version(self):
        def mutation(doc):
            key = self.fx.context["registry_keys"][0]
            doc["registry"]["64"][key]["DisplayVersion"]["value"] = "0.1.0"
        self.fx.edit_snapshot(1, "after", mutation)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "registry_version"):
            self.fx.verify()

    def test_shared_registry_views_do_not_create_a_false_duplicate(self):
        for index in range(3):
            for position in ("before", "after"):
                def mutation(doc):
                    doc["registry"]["32"] = copy.deepcopy(doc["registry"]["64"])
                self.fx.edit_snapshot(index, position, mutation)
        self.assertEqual(self.fx.verify()["status"], "SOURCE_BOUND_FIXTURE_OBSERVATIONS")

    def test_wal_or_shm_loss_is_not_project_preservation(self):
        for name in ("motionwright.sqlite3-wal", "motionwright.sqlite3-shm"):
            saved = self.fx.record["operations"][1]["after"]
            self.fx.edit_snapshot(1, "after", lambda doc: doc["sqlite_family"].__setitem__(name, None))
            with self.subTest(name=name), self.assertRaisesRegex(lifecycle.LifecycleError, "upgrade_project"):
                self.fx.verify()
            self.fx.record["operations"][1]["after"] = saved

    def test_missing_family_member_is_rejected_even_if_database_hash_matches(self):
        self.fx.edit_snapshot(1, "after", lambda doc: doc["sqlite_family"].pop("motionwright.sqlite3-shm"))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "sqlite_family_incomplete"):
            self.fx.verify()

    def test_altered_database_bytes_are_detected_from_retained_file(self):
        (self.root / self.fx.db_ref["path"]).write_bytes(b"changed database")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "retained_bytes"):
            self.fx.verify()

    def test_uninstall_cannot_remove_foreign_file_or_keep_owned_main(self):
        saved = self.fx.record["operations"][2]["after"]
        self.fx.edit_snapshot(2, "after", lambda doc: doc["installed"].pop("foreign.txt"))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "uninstall_removed_foreign"):
            self.fx.verify()
        self.fx.record["operations"][2]["after"] = saved
        self.fx.edit_snapshot(2, "after", lambda doc: doc["installed"].__setitem__(
            "motionwright-desktop.exe", self.fx.new_main))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "uninstall_removed_foreign"):
            self.fx.verify()

    def test_installed_uninstaller_must_be_the_observed_new_self(self):
        self.fx.record["operations"][2]["executable"] = self.fx.ident(self.fx.old_self)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "original_terminal_receipt_binding"):
            self.fx.verify()

    def test_human_acceptance_and_timing_kill_are_not_admitted(self):
        self.fx.record["claims"]["human_acceptance"] = "PASS"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "lifecycle_claims"):
            self.fx.verify()
        self.fx.record["claims"]["human_acceptance"] = "NOT_RUN"
        self.fx.record["interruption"] = {"status": "PASS", "reason": "SLEPT_AND_KILLED"}
        with self.assertRaisesRegex(lifecycle.LifecycleError, "interruption_not_supported"):
            self.fx.verify()

    def test_snapshot_operation_cross_join_is_rejected(self):
        self.fx.record["operations"][1]["before"] = self.fx.record["operations"][0]["after"]
        with self.assertRaisesRegex(lifecycle.LifecycleError, "snapshot_phase_binding"):
            self.fx.verify()

    def test_predecessor_mutation_before_update_cannot_be_hidden(self):
        self.fx.edit_snapshot(1, "before", lambda doc: doc["installed"].__setitem__(
            "motionwright-desktop.exe", self.fx.new_main))
        with self.assertRaisesRegex(lifecycle.LifecycleError, "predecessor_bytes_changed"):
            self.fx.verify()

    def test_unconfirmed_launch_keeps_unknown_without_fabricating_pid(self):
        self.fx.record["operations"] = self.fx.record["operations"][:1]
        row = self.fx.record["operations"][0]
        row["process"] = None
        row["terminal"] = lifecycle.observed_terminal(None)
        row["after"] = None
        result = self.fx.verify()
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["originals"][0]["original_terminal"]["state"], "UNKNOWN")
        context = self.context_for_row(row)
        captured = []
        value = lifecycle.retain_observed_result(self.root, "install", row["terminal"],
            lambda: None, context=context,
            publisher=lambda root, path, record: captured.append(record),
            diagnostic_stream=io.StringIO())
        self.assertEqual(value["original_terminal"], row["terminal"])
        self.assertIsNone(captured[0]["context"]["process"])

    def test_confirmed_exit_cannot_have_an_invented_or_missing_pid(self):
        self.fx.record["operations"][0]["process"] = None
        with self.assertRaisesRegex(lifecycle.LifecycleError, "missing_process_for_observed_exit"):
            self.fx.verify()

    def context_for_row(self, row):
        return {"operation_id": row["operation_id"], "process": row["process"],
                "executable": row["executable"], "source_sha": self.fx.source, "run": self.fx.run}

    def context(self):
        row = self.fx.record["operations"][1]
        return {"operation_id": row["operation_id"], "process": row["process"],
                "executable": row["executable"], "source_sha": self.fx.source, "run": self.fx.run}

    def test_post_effect_capture_failure_preserves_ack_before_any_derived_work(self):
        called = []
        def publish(root, path, value):
            called.append(("publish", path.name, copy.deepcopy(value)))
        def observe():
            called.append(("observe",))
            raise OSError("synthetic capture failure")
        original = lifecycle.observed_terminal(0)
        result = lifecycle.retain_observed_result(self.root, "upgrade", original, observe,
            context=self.context(), publisher=publish, diagnostic_stream=io.StringIO())
        self.assertEqual(result["original_terminal"], original)
        self.assertEqual(called[0][0:2], ("publish", "upgrade-terminal.json"))
        self.assertEqual(called[1], ("observe",))
        self.assertEqual(called[2][0:2], ("publish", "upgrade-custody-diagnostic.json"))

    def test_commit_uncertain_keeps_unknown_custody_and_original_exit(self):
        calls = []
        def publish(root, path, value):
            calls.append(copy.deepcopy(value))
            if len(calls) == 1:
                raise ManifestError("manifest_output_commit_uncertain")
        original = lifecycle.observed_terminal(2)
        result = lifecycle.retain_observed_result(self.root, "upgrade", original, lambda: None,
            context=self.context(), publisher=publish, diagnostic_stream=io.StringIO())
        self.assertEqual(result["original_terminal"], original)
        self.assertEqual(result["custody_errors"][0]["state"], "UNKNOWN")
        self.assertEqual(result["custody_errors"][0]["code"], "manifest_output_commit_uncertain")
        self.assertEqual(len(calls), 2)

    def test_broken_stderr_and_two_failed_writes_never_mask_native_unknown(self):
        class Broken:
            def write(self, value):
                raise BrokenPipeError("synthetic closed stderr")
            def flush(self):
                raise BrokenPipeError("synthetic closed stderr")
        original = lifecycle.observed_terminal(None, timed_out=True)
        publisher = mock.Mock(side_effect=OSError("synthetic write failed"))
        observer = mock.Mock(side_effect=OSError("synthetic read failed"))
        result = lifecycle.retain_observed_result(self.root, "upgrade", original, observer,
            context=self.context(), publisher=publisher, diagnostic_stream=Broken())
        self.assertEqual(result["original_terminal"], original)
        self.assertEqual(publisher.call_count, 2)
        self.assertEqual(observer.call_count, 1)
        self.assertEqual(len(result["custody_errors"]), 3)

    def test_existing_terminal_receipt_is_not_replaced_or_retried(self):
        target = self.root / "upgrade-terminal.json"
        target.write_bytes(b"older exact receipt")
        result = lifecycle.retain_observed_result(self.root, "upgrade",
            lifecycle.observed_terminal(0), lambda: None, context=self.context(),
            diagnostic_stream=io.StringIO())
        self.assertEqual(target.read_bytes(), b"older exact receipt")
        self.assertTrue(result["custody_errors"])
        self.assertEqual(result["original_terminal"]["state"], "ACK")

    def test_zero_byte_sidecar_has_a_real_present_identity(self):
        observed = lifecycle.file_identity(self.root / self.fx.shm_ref["path"])
        self.assertEqual(observed, self.fx.ident(self.fx.shm_ref))
        self.assertEqual(observed["bytes"], 0)

    def test_capture_real_sqlite_family_uses_separate_inspection_copy(self):
        parent = Path(self.temp.name)
        installed = parent / "installed"
        installed.mkdir()
        (installed / "motionwright-desktop.exe").write_bytes(b"synthetic installed bytes")
        profile = parent / "profile"
        profile.mkdir()
        db_path = profile / "motionwright.sqlite3"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA user_version=3")
            connection.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
            project = {"id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
                       "revision": 0, "title": "motionwright ci first use"}
            connection.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                (project["id"], project["generation"], 0, project["title"], json.dumps(project)))
            connection.commit()
            original = {path.name: path.read_bytes() for path in profile.iterdir()}
            captured = lifecycle.capture_snapshot(self.root, snapshot_name="sqlite-capture",
                operation=str(uuid.uuid4()), position="before", install_root=installed,
                database=db_path, registry_keys=self.fx.context["registry_keys"],
                registry_reader=lambda _: self.fx.registry("0.1.0"))
            document = lifecycle.bound_json(self.root, captured)
            self.assertEqual(document["project"]["project_id"], project["id"])
            self.assertEqual({path.name: path.read_bytes() for path in profile.iterdir()}, original)
            self.assertTrue(all(value is not None for value in document["sqlite_family"].values()))
            for name, reference in document["sqlite_family"].items():
                self.assertEqual(lifecycle.bound_file(self.root, reference).read_bytes(), original[name])
        finally:
            connection.close()

    def test_missing_identity_custody_does_not_erase_an_observed_exit_zero(self):
        context = self.context()
        context["process"] = None
        original = lifecycle.observed_terminal(0)
        captured = []
        result = lifecycle.retain_observed_result(self.root, "upgrade", original,
            lambda: None, context=context,
            publisher=lambda root, path, value: captured.append(copy.deepcopy(value)),
            diagnostic_stream=io.StringIO())
        self.assertEqual(result["original_terminal"], original)
        self.assertEqual(captured[0]["original_terminal"], original)
        self.assertEqual(result["custody_errors"][0]["code"], "process_identity_unavailable")

    def test_outer_custody_stage_cannot_downgrade_uncertain_publication(self):
        error = ManifestError("manifest_output_commit_uncertain")
        for stage in ("lifecycle_sequence", "record_publication", "lifecycle_verification"):
            with self.subTest(stage=stage):
                observed = lifecycle.custody_error(stage, error)
                self.assertEqual(observed["state"], "UNKNOWN")
                self.assertEqual(observed["code"], "manifest_output_commit_uncertain")

    def test_missing_appdata_tail_is_recorded_absent_without_creating_it(self):
        parent = Path(self.temp.name)
        installed = parent / "empty-install"
        installed.mkdir()
        db_path = parent / "missing-profile/application/motionwright.sqlite3"
        ref = lifecycle.capture_snapshot(self.root, snapshot_name="absent-profile",
            operation=str(uuid.uuid4()), position="before", install_root=installed,
            database=db_path, registry_keys=self.fx.context["registry_keys"],
            registry_reader=lambda _: self.fx.registry(None, metadata=False))
        value = lifecycle.bound_json(self.root, ref)
        self.assertTrue(all(item is None for item in value["sqlite_family"].values()))
        self.assertFalse((parent / "missing-profile").exists())

    def test_snapshot_registry_mutation_refuses_complete_receipt(self):
        parent = Path(self.temp.name)
        installed = parent / "empty-install"
        installed.mkdir()
        profile = parent / "empty-profile"
        profile.mkdir()
        reader = mock.Mock(side_effect=[self.fx.registry("0.1.0"), self.fx.registry("0.1.1")])
        with self.assertRaisesRegex(lifecycle.LifecycleError, "registry_changed_during_capture"):
            lifecycle.capture_snapshot(self.root, snapshot_name="changed-registry",
                operation=str(uuid.uuid4()), position="before", install_root=installed,
                database=profile / "motionwright.sqlite3",
                registry_keys=self.fx.context["registry_keys"], registry_reader=reader)
        self.assertFalse((self.root / "changed-registry/snapshot.json").exists())


    def test_project_origin_requires_the_source_selected_first_use_contract(self):
        original = lifecycle.bound_json(self.root, self.fx.record["project_origin"])
        self.assertEqual(original["first_use_contract"], lifecycle.CURRENT_FIRST_USE_CONTRACT)
        for replacement in (None, True, "automatic-project-679cc48b/1", "arbitrary"):
            changed = copy.deepcopy(original)
            if replacement is None:
                changed.pop("first_use_contract")
            else:
                changed["first_use_contract"] = replacement
            self.fx.record["project_origin"] = self.fx.js("wrong-first-use-contract", changed)
            with self.subTest(replacement=replacement), self.assertRaisesRegex(
                lifecycle.LifecycleError, "project_origin_not_bound",
            ):
                self.fx.verify()

    def test_real_legacy_sqlite_family_survives_every_lifecycle_snapshot_and_reopen(self):
        from windows_cold_start import LEGACY_FIRST_USE_CONTRACT, LEGACY_FIRST_USE_TITLE
        parent = Path(self.temp.name)
        installed = parent / "legacy-install"
        installed.mkdir()
        (installed / "motionwright-desktop.exe").write_bytes(b"synthetic installed fixture")
        profile = parent / "legacy-profile"
        profile.mkdir()
        database = profile / "motionwright.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA user_version=2")
            connection.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
            document = {"id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
                        "revision": 0, "title": LEGACY_FIRST_USE_TITLE}
            encoded = json.dumps(document)
            connection.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                (document["id"], document["generation"], 0, document["title"], encoded))
            connection.commit()
            original = {path.name: path.read_bytes() for path in profile.iterdir()}
            projects = []
            for index, phase in enumerate(("upgrade", "uninstall")):
                for position in ("before", "after"):
                    ref = lifecycle.capture_snapshot(
                        self.root, snapshot_name="legacy-" + phase + "-" + position,
                        operation=str(uuid.uuid4()), position=position, install_root=installed,
                        database=database, registry_keys=self.fx.context["registry_keys"],
                        registry_reader=lambda _: self.fx.registry("0.1.0"),
                        first_use_contract=LEGACY_FIRST_USE_CONTRACT,
                    )
                    captured = lifecycle.bound_json(self.root, ref)
                    projects.append(captured["project"])
                    self.assertEqual({path.name: path.read_bytes() for path in profile.iterdir()}, original)
                    for name, retained in captured["sqlite_family"].items():
                        self.assertEqual(lifecycle.bound_file(self.root, retained).read_bytes(), original[name])
            self.assertTrue(all(item == projects[0] for item in projects))
            self.assertEqual(projects[0]["project_id"], document["id"])
            self.assertEqual(projects[0]["generation"], document["generation"])
            self.assertEqual(projects[0]["document_sha256"], hashlib.sha256(encoded.encode()).hexdigest())
        finally:
            connection.close()
        self.assertEqual(lifecycle.read_project_snapshot(
            database, first_use_contract=LEGACY_FIRST_USE_CONTRACT,
        ), projects[0])

    def test_lifecycle_snapshot_does_not_infer_legacy_contract_from_existing_title(self):
        from windows_cold_start import ColdStartError, LEGACY_FIRST_USE_TITLE
        parent = Path(self.temp.name)
        installed = parent / "title-only-install"
        installed.mkdir()
        profile = parent / "title-only-profile"
        profile.mkdir()
        database = profile / "motionwright.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA user_version=3")
            connection.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
            project = {"id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
                       "revision": 0, "title": LEGACY_FIRST_USE_TITLE}
            connection.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                (project["id"], project["generation"], 0, project["title"], json.dumps(project)))
            connection.commit()
        finally:
            connection.close()
        original = database.read_bytes()
        with self.assertRaisesRegex(ColdStartError, "initialized"):
            lifecycle.capture_snapshot(
                self.root, snapshot_name="wrong-contract",
                operation=str(uuid.uuid4()), position="before", install_root=installed,
                database=database, registry_keys=self.fx.context["registry_keys"],
                registry_reader=lambda _: self.fx.registry("0.1.0"),
            )
        self.assertEqual(database.read_bytes(), original)
        self.assertFalse((self.root / "wrong-contract/snapshot.json").exists())


class TemplateFlowError(ValueError):
    """The source exceeds the deliberately closed NSIS control-flow subset."""


class UninstallTemplateFlow:
    """Interpret actual payload-flow macros with explicitly simulated guard results.

    This is not an NSIS VM or a filesystem/registry oracle. The tested slice starts
    at uninstall flag initialization and ends before association/registration
    cleanup. Optional hooks, the running-process check, native calls and compiler
    object iteration require the separate compiled Windows gate.
    """

    LOGIC = {"If", "AndIf", "OrIf", "ElseIf", "Else", "EndIf"}
    OWNED = (
        "motionwright-desktop.exe", r"assets\alpha data.dat", r"assets\beta.dat",
        "helper-one.exe", "helper-two.exe",
    )

    def __init__(self, template, guard, overrides=None, modified=None, missing=()):
        self.template = template
        self.constants = dict(re.findall(r"(?m)^!define (MWOWN_\w+) ([0-9]+)\s*$", guard))
        required = {"MWOWN_MATCHED", "MWOWN_MISSING", "MWOWN_PRESERVED",
                    "MWOWN_DELETE_MARKED", "MWOWN_DELETE_UNCERTAIN"}
        if not required <= self.constants.keys():
            raise TemplateFlowError("missing native result constants")
        self.baselines = {name: (("synthetic baseline " + name) * (index + 1)).encode()
                          for index, name in enumerate(self.OWNED)}
        self.files = {**self.baselines, "uninstall.exe": b"synthetic retained self",
                      "foreign.txt": b"foreign bytes"}
        self.files.update(modified or {})
        for name in missing:
            self.files.pop(name, None)
        self.initial_files = dict(self.files)
        self.overrides = overrides or {}
        self.calls = []
        self.diagnostics = []
        self.model_exit_code = 0
        self.abort = None
        self.registration_phase_reached = False
        self.variables = {"$INSTDIR": r"C:\synthetic-install"}
        self.declared = {"$0", "$INSTDIR", "$MWOWN_LastReason"}
        self.declared.update("$" + name for name in re.findall(r"(?m)^Var (\w+)\s*$", template))
        self.constants.update(MAINBINARYNAME="motionwright-desktop",
                              PRODUCTNAME="Synthetic Motionwright")
        for index, name in enumerate(self.OWNED):
            symbol = ("MW_MAIN" if index == 0 else
                      "MW_RESOURCE_" + str(index - 1) if index < 3 else
                      "MW_BINARY_" + str(index - 3))
            self.constants[symbol + "_SHA256"] = hashlib.sha256(self.baselines[name]).hexdigest()
            self.constants[symbol + "_SIZE"] = str(len(self.baselines[name]))
        self.macros = {}
        for match in re.finditer(r"(?ms)^!macro (\w+)([^\n]*)\n(.*?)^!macroend\s*$", template):
            name, parameters, body = match.groups()
            if name in self.macros:
                raise TemplateFlowError("duplicate macro: " + name)
            self.macros[name] = (parameters.split(), body)
        sections = re.findall(r"(?ms)^Section Uninstall\s*\n(.*?)^SectionEnd\s*$", template)
        if len(sections) != 1:
            raise TemplateFlowError("uninstall section is not unique")
        section = sections[0]
        start = "  StrCpy $MWINST_UninstallPreserved 0"
        end = "  ; Delete app associations"
        if section.count(start) != 1 or section.count(end) != 1:
            raise TemplateFlowError("payload-flow boundaries are not unique")
        prefix, payload = section.split(start)
        expected_hook = [
            "!ifmacrodef NSIS_HOOK_PREUNINSTALL",
            "!insertmacro NSIS_HOOK_PREUNINSTALL", "!endif",
        ]
        if [line.strip() for line in prefix.splitlines() if line.strip()] != expected_hook:
            raise TemplateFlowError("unsupported operations before payload preflight")
        payload = start + payload.split(end)[0]
        self.instructions = self._expand(self._render_payload(payload), {}, ())
        self.program, position = self._sequence(0)
        if position != len(self.instructions):
            raise TemplateFlowError("unbalanced conditional at payload boundary")

    @staticmethod
    def _tokens(line):
        tokens, position = [], 0
        for match in re.finditer(r'"[^"\r\n]*"|[^\s"]+', line):
            if line[position:match.start()].strip():
                raise TemplateFlowError("unsupported quoting")
            token = match.group()
            tokens.append(token[1:-1] if token.startswith('"') else token)
            position = match.end()
        if line[position:].strip():
            raise TemplateFlowError("unterminated quoted token")
        return tokens

    def _render_payload(self, payload):
        def render(match):
            collection, body = match.groups()
            rows = (self.OWNED[1:3] if collection == "resources" else self.OWNED[3:5])
            field = "{{this.[1]}}" if collection == "resources" else "{{this}}"
            return "".join(body.replace(field, name).replace("{{@index}}", str(index))
                           for index, name in enumerate(rows))
        rendered = re.sub(r"(?ms)^\s*{{#each (resources|binaries)}}\s*\n(.*?)^\s*{{/each}}\s*$",
                          render, payload)
        if "{{" in rendered or "}}" in rendered:
            raise TemplateFlowError("unsupported template iteration or field")
        return rendered

    def _substitute(self, line, parameters):
        def replace(match):
            name = match.group(1)
            if name in parameters:
                return parameters[name]
            if name in self.constants:
                return self.constants[name]
            if name in self.LOGIC:
                return match.group()
            raise TemplateFlowError("unknown source symbol: " + name)
        return re.sub(r"\${(\w+)}", replace, line)

    def _expand(self, source, parameters, stack):
        output = []
        for raw in source.splitlines():
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            tokens = self._tokens(self._substitute(line, parameters))
            if tokens[0] == "!insertmacro" and len(tokens) >= 2:
                name = tokens[1]
                if name not in {"MWOWN_CHECK", "MWOWN_DELETE", "CheckIfAppIsRunning"}:
                    if name not in self.macros or name in stack:
                        raise TemplateFlowError("unknown or recursive macro: " + name)
                    keys, body = self.macros[name]
                    if len(keys) != len(tokens) - 2:
                        raise TemplateFlowError("macro argument count: " + name)
                    output.extend(self._expand(body, dict(zip(keys, tokens[2:])), stack + (name,)))
                    continue
            output.append(tokens)
        return output

    def _condition(self, tokens):
        if len(tokens) != 4 or tokens[2] not in {"=", "==", "!=", "S==", "S!="}:
            raise TemplateFlowError("unsupported conditional expression")
        return tokens[1:]

    def _sequence(self, position):
        nodes = []
        while position < len(self.instructions):
            tokens = self.instructions[position]
            if tokens[0] in {"${ElseIf}", "${Else}", "${EndIf}"}:
                break
            if tokens[0] == "${If}":
                branches, fallback = [], []
                while True:
                    conditions = [self._condition(self.instructions[position])]
                    position += 1
                    connective = None
                    while position < len(self.instructions) and self.instructions[position][0] in {
                            "${AndIf}", "${OrIf}"}:
                        extra = self.instructions[position]
                        kind = extra[0]
                        if connective is not None and connective != kind:
                            raise TemplateFlowError("mixed logical connectives are unsupported")
                        connective = kind
                        conditions.append(self._condition(extra))
                        position += 1
                    body, position = self._sequence(position)
                    branches.append((connective, conditions, body))
                    if position >= len(self.instructions):
                        raise TemplateFlowError("unterminated conditional")
                    if self.instructions[position][0] == "${ElseIf}":
                        continue
                    if self.instructions[position] == ["${Else}"]:
                        fallback, position = self._sequence(position + 1)
                    if position >= len(self.instructions) or self.instructions[position] != ["${EndIf}"]:
                        raise TemplateFlowError("ambiguous conditional terminator")
                    position += 1
                    break
                nodes.append(("conditional", branches, fallback))
                continue
            self._validate_instruction(tokens)
            nodes.append(("instruction", tokens))
            position += 1
        return nodes, position

    def _validate_instruction(self, tokens):
        operation = tokens[0]
        if operation == "StrCpy" and len(tokens) == 3 and tokens[1] in self.declared:
            return
        if operation == "SetErrorLevel" and len(tokens) == 2 and re.fullmatch(r"[0-9]+", tokens[1]):
            return
        if operation in {"Abort", "DetailPrint"} and len(tokens) == 2:
            return
        if operation == "!insertmacro":
            if tokens[1] in {"MWOWN_CHECK", "MWOWN_DELETE"} and len(tokens) == 8:
                return
            if tokens[1] == "CheckIfAppIsRunning" and len(tokens) == 4:
                return
        raise TemplateFlowError("unsupported instruction: " + " ".join(tokens))

    def _value(self, token):
        def replace(match):
            name = match.group()
            if name not in self.variables:
                raise TemplateFlowError("uninitialized variable: " + name)
            return self.variables[name]
        value = re.sub(r"\$(?:[A-Za-z_]\w*|[0-9])", replace, token)
        if "$" in value:
            raise TemplateFlowError("unsupported runtime substitution")
        return value

    def _true(self, expression):
        left, operator, right = expression
        left, right = self._value(left), self._value(right)
        if operator == "=":
            if not re.fullmatch(r"-?[0-9]+", left) or not re.fullmatch(r"-?[0-9]+", right):
                raise TemplateFlowError("unsupported nondecimal integer comparison")
            return int(left) == int(right)
        # LogicLib == / != use StrCmp; S== / S!= use StrCmpS.
        equal = left == right if operator.startswith("S") else left.casefold() == right.casefold()
        return not equal if operator.endswith("!=") else equal

    def _execute(self, nodes):
        for node in nodes:
            if self.abort is not None:
                return
            if node[0] == "conditional":
                selected = node[2]
                for connective, conditions, body in node[1]:
                    values = [self._true(condition) for condition in conditions]
                    if (any(values) if connective == "${OrIf}" else all(values)):
                        selected = body
                        break
                self._execute(selected)
                continue
            tokens = node[1]
            operation = tokens[0]
            if operation == "StrCpy":
                self.variables[tokens[1]] = self._value(tokens[2])
            elif operation == "SetErrorLevel":
                self.model_exit_code = int(tokens[1])
            elif operation == "Abort":
                self.abort = self._value(tokens[1])
            elif operation == "DetailPrint":
                self.diagnostics.append(self._value(tokens[1]))
            else:
                name, *arguments = tokens[1:]
                arguments = [self._value(arg) if arg != "$0" else arg for arg in arguments]
                if name == "CheckIfAppIsRunning":
                    if arguments != [r"C:\synthetic-install\motionwright-desktop.exe", "Synthetic Motionwright"]:
                        raise TemplateFlowError("unexpected running-process check binding")
                    self.calls.append(("RUNNING_PROCESS_CHECK", None))
                    continue
                prefix, root, relative, digest, size, result_variable = arguments
                if (prefix != "un." or root != r"C:\synthetic-install" or result_variable != "$0"
                        or relative not in self.baselines):
                    raise TemplateFlowError("unexpected native guard target")
                baseline = self.baselines[relative]
                if digest != hashlib.sha256(baseline).hexdigest() or size != str(len(baseline)):
                    raise TemplateFlowError("native guard baseline binding mismatch")
                phase = "CHECK" if name == "MWOWN_CHECK" else "DELETE"
                key = (phase, relative)
                if key in self.calls:
                    raise TemplateFlowError("repeated native request is not admitted by this fixture")
                self.calls.append(key)
                default = ("MWOWN_MATCHED", "matched") if phase == "CHECK" else ("MWOWN_DELETE_MARKED", "delete_marked")
                result, reason = self.overrides.get(key, default)
                self.variables["$0"] = self.constants.get(result, str(result))
                self.variables["$MWOWN_LastReason"] = reason
                if phase == "DELETE" and self.variables["$0"] == self.constants["MWOWN_DELETE_MARKED"]:
                    self.files.pop(relative, None)

    def run(self):
        self._execute(self.program)
        self.registration_phase_reached = self.abort is None
        return self


class UninstallTemplateFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        windows = Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/windows"
        cls.template = (windows / "installer.nsi").read_text(encoding="utf-8-sig")
        cls.guard = (windows / "uninstall-ownership.nsh").read_text(encoding="utf-8-sig")

    def flow(self, overrides=None, modified=None, missing=(), template=None):
        return UninstallTemplateFlow(self.template if template is None else template, self.guard,
                                     overrides, modified, missing).run()

    def assert_complete(self, flow):
        self.assertIsNone(flow.abort)
        self.assertTrue(flow.registration_phase_reached)
        self.assertEqual(flow.model_exit_code, 0)
        for name in ("uninstall.exe", "foreign.txt"):
            self.assertEqual(flow.files[name], flow.initial_files[name])

    def assert_blocked(self, flow, code=2, before_effects=False):
        self.assertIsNotNone(flow.abort)
        self.assertFalse(flow.registration_phase_reached)
        self.assertEqual(flow.model_exit_code, code)
        if before_effects:
            self.assertFalse(any(phase == "DELETE" for phase, _ in flow.calls))
            self.assertEqual(flow.files, flow.initial_files)

    def test_mixed_content_mismatch_preserves_each_role_and_removes_intact_payloads(self):
        for role in UninstallTemplateFlow.OWNED:
            for reason in ("hash_mismatch", "size_mismatch"):
                with self.subTest(role=role, reason=reason):
                    responses = {(phase, role): ("MWOWN_PRESERVED", reason) for phase in ("CHECK", "DELETE")}
                    flow = self.flow(responses, {role: b"authored modified bytes"})
                    self.assert_complete(flow)
                    self.assertEqual(set(flow.files), {role, "uninstall.exe", "foreign.txt"})
                    self.assertEqual(flow.files[role], b"authored modified bytes")
                    self.assertEqual(flow.calls.count(("DELETE", role)), 1)

    def test_all_modified_payloads_can_complete_with_their_bytes_retained(self):
        modified = {name: ("changed " + name).encode() for name in UninstallTemplateFlow.OWNED}
        responses = {(phase, name): ("MWOWN_PRESERVED", "hash_mismatch")
                     for phase in ("CHECK", "DELETE") for name in modified}
        flow = self.flow(responses, modified)
        self.assert_complete(flow)
        self.assertEqual(flow.files, flow.initial_files)

    def test_missing_listed_payloads_do_not_block_other_removals(self):
        for name in UninstallTemplateFlow.OWNED:
            with self.subTest(name=name):
                responses = {(phase, name): ("MWOWN_MISSING", "file_missing") for phase in ("CHECK", "DELETE")}
                flow = self.flow(responses, missing=(name,))
                self.assert_complete(flow)
                self.assertEqual(set(flow.files), {"uninstall.exe", "foreign.txt"})

    def test_all_checks_precede_running_process_check_and_any_disposition(self):
        flow = self.flow()
        self.assert_complete(flow)
        self.assertEqual(flow.calls, [("CHECK", name) for name in UninstallTemplateFlow.OWNED] +
                         [("RUNNING_PROCESS_CHECK", None)] +
                         [("DELETE", name) for name in UninstallTemplateFlow.OWNED])

    def test_hard_preflight_reasons_refuse_before_every_effect(self):
        reasons = (
            "parent_not_ordinary_directory", "file_not_ordinary", "file_redirected_or_aliased",
            "file_unavailable", "file_read_count_invalid", "sha256_cleanup_failed",
            "sha256_provider_cleanup_failed", "file_close_failed", "root_close_failed",
            "file_identity_changed", "size_changed", "new_unknown_reason", "",
        )
        for reason in reasons:
            for name in UninstallTemplateFlow.OWNED:
                with self.subTest(reason=reason, name=name):
                    flow = self.flow({("CHECK", name): ("MWOWN_PRESERVED", reason)})
                    self.assert_blocked(flow, before_effects=True)
                    self.assertEqual(sum(phase == "CHECK" for phase, _ in flow.calls), 5)

    def test_safe_reason_with_wrong_preflight_result_is_not_preservation_authority(self):
        for code in ("MWOWN_DELETE_MARKED", "MWOWN_DELETE_UNCERTAIN", "MWOWN_PUBLISHED", "999"):
            for reason in ("hash_mismatch", "size_mismatch"):
                with self.subTest(code=code, reason=reason):
                    flow = self.flow({("CHECK", UninstallTemplateFlow.OWNED[0]): (code, reason)})
                    self.assert_blocked(flow, before_effects=True)

    def test_reason_must_match_exactly_in_both_operations(self):
        for reason in ("HASH_MISMATCH", "Size_Mismatch", "hash_mismatch ", "size_mismatch_extra",
                       "file_close_failed:hash_mismatch"):
            for phase in ("CHECK", "DELETE"):
                with self.subTest(reason=reason, phase=phase):
                    flow = self.flow({(phase, UninstallTemplateFlow.OWNED[0]): ("MWOWN_PRESERVED", reason)})
                    self.assert_blocked(flow, before_effects=phase == "CHECK")

    def test_delete_rechecks_content_changed_after_a_successful_preflight(self):
        name = UninstallTemplateFlow.OWNED[1]
        for reason in ("hash_mismatch", "size_mismatch"):
            with self.subTest(reason=reason):
                flow = self.flow({("DELETE", name): ("MWOWN_PRESERVED", reason)},
                                 {name: b"changed after preflight"})
                self.assert_complete(flow)
                self.assertEqual(flow.files[name], b"changed after preflight")
                self.assertEqual(set(flow.files), {name, "uninstall.exe", "foreign.txt"})

    def test_preflight_mismatch_does_not_skip_a_later_matching_delete_check(self):
        name = UninstallTemplateFlow.OWNED[1]
        flow = self.flow({("CHECK", name): ("MWOWN_PRESERVED", "hash_mismatch")})
        self.assert_complete(flow)
        self.assertIn(("DELETE", name), flow.calls)
        self.assertNotIn(name, flow.files)

    def test_prior_safe_mismatch_cannot_mask_later_hard_preflight(self):
        first, later = UninstallTemplateFlow.OWNED[:2]
        flow = self.flow({("CHECK", first): ("MWOWN_PRESERVED", "hash_mismatch"),
                          ("CHECK", later): ("MWOWN_PRESERVED", "file_close_failed")})
        self.assert_blocked(flow, before_effects=True)

    def test_prior_safe_mismatch_cannot_mask_later_hard_delete(self):
        first, later = UninstallTemplateFlow.OWNED[:2]
        flow = self.flow({("DELETE", first): ("MWOWN_PRESERVED", "size_mismatch"),
                          ("DELETE", later): ("MWOWN_PRESERVED", "file_unavailable")})
        self.assert_blocked(flow)
        self.assertEqual([name for phase, name in flow.calls if phase == "DELETE"], [first, later])
        self.assertEqual(flow.files, flow.initial_files)

    def test_hard_delete_stops_without_rollback_or_further_dispositions(self):
        for reason in ("file_not_ordinary", "file_unavailable", "file_read_count_invalid",
                       "sha256_cleanup_failed", "file_close_failed", "root_close_failed",
                       "unknown_delete_reason"):
            with self.subTest(reason=reason):
                name = UninstallTemplateFlow.OWNED[2]
                flow = self.flow({("DELETE", name): ("MWOWN_PRESERVED", reason)})
                self.assert_blocked(flow)
                self.assertEqual([item for phase, item in flow.calls if phase == "DELETE"],
                                 list(UninstallTemplateFlow.OWNED[:3]))
                self.assertFalse(any(item in flow.files for item in UninstallTemplateFlow.OWNED[:2]))
                self.assertTrue(all(item in flow.files for item in UninstallTemplateFlow.OWNED[2:]))

    def test_unknown_delete_stops_once_even_if_reason_looks_like_content_mismatch(self):
        name = UninstallTemplateFlow.OWNED[1]
        for reason in ("close_after_delete_failed", "root_close_after_delete_failed", "hash_mismatch", "size_mismatch"):
            with self.subTest(reason=reason):
                flow = self.flow({("DELETE", name): ("MWOWN_DELETE_UNCERTAIN", reason)})
                self.assert_blocked(flow, code=3)
                self.assertEqual(flow.calls.count(("DELETE", name)), 1)
                self.assertEqual([item for phase, item in flow.calls if phase == "DELETE"],
                                 list(UninstallTemplateFlow.OWNED[:2]))
                self.assertEqual(flow.variables["$MWOWN_LastReason"], reason)

    def test_wrong_delete_result_with_safe_reason_still_stops(self):
        for code in ("MWOWN_MATCHED", "MWOWN_PUBLISHED", "999"):
            with self.subTest(code=code):
                flow = self.flow({("DELETE", UninstallTemplateFlow.OWNED[1]): (code, "hash_mismatch")})
                self.assert_blocked(flow)
                self.assertNotIn(("DELETE", UninstallTemplateFlow.OWNED[2]), flow.calls)

    def test_interpreter_rejects_unsupported_instruction_even_in_untaken_branch(self):
        marker = "  StrCpy $MWINST_UninstallPreserved 0"
        for instruction in ('Delete "$INSTDIR\\foreign.txt"', "Goto +2", "Call HiddenOperation"):
            with self.subTest(instruction=instruction):
                inserted = marker + "\n  ${If} 0 = 1\n    " + instruction + "\n  ${EndIf}"
                with self.assertRaisesRegex(TemplateFlowError, "unsupported instruction"):
                    self.flow(template=self.template.replace(marker, inserted, 1))

    def test_interpreter_rejects_ambiguous_or_unknown_logic(self):
        marker = "  StrCpy $MWINST_UninstallPreserved 0"
        for condition in ("${If} $0 < 2\n  ${EndIf}",
                          "${If} 0 = 0\n  ${AndIf} 1 = 1\n  ${OrIf} 2 = 2\n  ${EndIf}",
                          "${Else}\n  ${EndIf}"):
            with self.subTest(condition=condition):
                with self.assertRaises(TemplateFlowError):
                    self.flow(template=self.template.replace(marker, marker + "\n  " + condition, 1))

    def test_interpreter_rejects_unknown_macro_and_template_field(self):
        marker = "  StrCpy $MWINST_UninstallPreserved 0"
        for extra in ('!insertmacro UnknownOperation "payload"',
                      '{{#each other_collection}}\n{{this}}\n{{/each}}'):
            with self.subTest(extra=extra):
                with self.assertRaises(TemplateFlowError):
                    self.flow(template=self.template.replace(marker, marker + "\n  " + extra, 1))

    def test_distinct_resource_and_binary_bindings_are_checked_against_source_arguments(self):
        for original, wrong in (("${MW_RESOURCE_{{@index}}_SHA256}", "${MW_MAIN_SHA256}"),
                                ("${MW_BINARY_{{@index}}_SIZE}", "${MW_MAIN_SIZE}")):
            with self.subTest(original=original):
                with self.assertRaisesRegex(TemplateFlowError, "baseline binding mismatch"):
                    self.flow(template=self.template.replace(original, wrong))



class StorageCohortLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-storage-cohort-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "evidence"
        self.root.mkdir()
        self.fx = Fixture(self.root)
        self.installed = self.parent / "installed"
        self.installed.mkdir()
        (self.installed / "motionwright-desktop.exe").write_bytes(b"synthetic installed bytes")
        self.profile = self.parent / "profile"
        self.profile.mkdir()
        self.database = self.profile / "motionwright.sqlite3"
        self.selected = storage_cohort_fixture(4)
        self.operation = str(uuid.uuid4())

    def seed_four(self):
        db = sqlite3.connect(self.database)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA user_version=4")
        db.execute("CREATE TABLE projects(id TEXT,generation TEXT,revision INTEGER,title TEXT,document_json TEXT)")
        project = {"id": str(uuid.uuid4()), "generation": str(uuid.uuid4()),
                   "revision": 0, "title": "motionwright ci first use", "schema_version": 2}
        db.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                   (project["id"], project["generation"], 0, project["title"], json.dumps(project)))
        db.execute("""CREATE TABLE receipts(project_id TEXT,request_id TEXT,request_sha256 TEXT,
                   result_json TEXT,created_at TEXT,
                   apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK(apply_binding_version IN (0,1)),
                   apply_binding_json TEXT)""")
        db.commit()
        return db, project

    def capture(self, label, *, selected=True):
        return lifecycle.capture_snapshot(self.root, snapshot_name=label, operation=self.operation,
            position="before", install_root=self.installed, database=self.database,
            registry_keys=self.fx.context["registry_keys"], registry_reader=lambda _: self.fx.registry("0.1.0"),
            **({"storage_cohort": self.selected} if selected else {}))

    def bind_inputs(self, value):
        self.fx.inputs_doc["storage_cohort"] = value
        self.fx.source_record["files"][lifecycle.storage.SOURCE_PATH] = self.selected["source_input"]["checkout"]
        self.fx.inputs_ref = self.fx.js("cohort-inputs", self.fx.inputs_doc)
        self.fx.admission["refs"]["inputs"] = self.fx.inputs_ref
        self.fx.compiled_doc["inputs_receipt"] = self.fx.ident(self.fx.inputs_ref)
        self.fx.admission["refs"]["compiled"] = self.fx.js("cohort-compiled", self.fx.compiled_doc)

    def test_four_capture_retains_original_family_and_separate_inspection(self):
        _, project = self.seed_four()
        original = {p.name: p.read_bytes() for p in self.profile.iterdir()}
        reference = self.capture("four-capture")
        value = lifecycle.snapshot_value(self.root, reference, self.operation, "before",
            self.fx.context["registry_keys"], storage_cohort=self.selected)
        self.assertEqual(value["schema"], "motionwright.windows-lifecycle-snapshot/2")
        self.assertEqual(value["storage_cohort"], self.selected)
        self.assertEqual(value["project"]["project_id"], project["id"])
        self.assertEqual({p.name: p.read_bytes() for p in self.profile.iterdir()}, original)
        self.assertTrue(all(value["sqlite_family"].values()))
        for name, ref in value["sqlite_family"].items():
            self.assertEqual(lifecycle.bound_file(self.root, ref).read_bytes(), original[name])

    def test_historical_default_does_not_publish_a_four_snapshot(self):
        self.seed_four()
        original = {p.name: p.read_bytes() for p in self.profile.iterdir()}
        with self.assertRaises(ColdStartError):
            self.capture("default-on-four", selected=False)
        self.assertFalse((self.root / "default-on-four/snapshot.json").exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.profile.iterdir()}, original)

    def test_v2_snapshot_needs_explicit_matching_source_and_does_not_downgrade_v1(self):
        self.seed_four()
        reference = self.capture("bound-four")
        keys = self.fx.context["registry_keys"]
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.snapshot_value(self.root, reference, self.operation, "before", keys)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "snapshot_storage_cohort_mismatch"):
            lifecycle.snapshot_value(self.root, reference, self.operation, "before", keys,
                storage_cohort=storage_cohort_fixture(4, source="c" * 40))
        legacy = self.fx.snapshot(self.operation, "before", {}, self.fx.registry(None))
        old = self.fx.js("legacy", legacy)
        self.assertEqual(lifecycle.snapshot_value(self.root, old, self.operation, "before", keys), legacy)
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.snapshot_value(self.root, old, self.operation, "before", keys, storage_cohort=self.selected)

    def test_explicit_input_cohort_binds_source_tree_and_retained_source_bytes(self):
        self.bind_inputs(self.selected)
        observed = self.fx.validate_inputs()
        self.assertEqual(observed["storage_cohort"], self.selected)
        self.assertEqual(observed["source_sha"], self.selected["source_sha"])
        self.assertEqual(observed["source_tree"], self.selected["source_tree"])

    def test_foreign_input_cohort_or_original_bytes_fail_before_pe_observation(self):
        for field, replacement in (("source_sha", "c" * 40), ("source_tree", "c" * 40),
                                    ("source_input", storage_cohort_fixture(3)["source_input"])):
            value = copy.deepcopy(self.selected)
            value[field] = replacement
            self.bind_inputs(value)
            with self.subTest(field=field), mock.patch.object(lifecycle, "read_pe_versions") as pe:
                with self.assertRaises((lifecycle.LifecycleError, lifecycle.storage.StorageContractError)):
                    self.fx.validate_inputs(pe_reader=pe)
                pe.assert_not_called()

    def test_document_version_four_is_not_storage_four_acceptance(self):
        db, project = self.seed_four()
        project["schema_version"] = 4
        db.execute("UPDATE projects SET document_json=?", (json.dumps(project),))
        db.commit()
        original = {p.name: p.read_bytes() for p in self.profile.iterdir()}
        with self.assertRaises(ColdStartError):
            self.capture("wrong-document")
        self.assertFalse((self.root / "wrong-document/snapshot.json").exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.profile.iterdir()}, original)

    def test_invalid_cohort_is_rejected_before_creating_snapshot_output(self):
        self.selected = {"storage_schema": 4}
        with self.assertRaises(lifecycle.storage.StorageContractError):
            self.capture("invalid-admission")
        self.assertFalse((self.root / "invalid-admission").exists())

    def test_missing_binding_columns_never_publish_a_four_snapshot(self):
        db, _ = self.seed_four()
        db.execute("ALTER TABLE receipts DROP COLUMN apply_binding_json")
        db.commit()
        with self.assertRaises(ColdStartError):
            self.capture("missing-binding-column")
        self.assertFalse((self.root / "missing-binding-column/snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
