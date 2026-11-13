"""Bounded observer contracts using inert files, not a native compilation.

These fixtures exercise identity, scope and refusal logic on the real filesystem.
They confer no build, package, licensing or Phase 1 acceptance evidence.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import candidate_dependency_capture as capture
import cargo_sqlite_native_output as native


class SQLiteFixture:
    def __init__(self, root, target="x86_64-pc-windows-msvc", *, state=None,
                 source="1" * 40, tree="2" * 40):
        self.root = Path(root).resolve()
        self.target, self.source, self.tree = target, source, tree
        self.state = self.root / "state" if state is None else Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        target_relative, names = native.TARGETS[target]
        self.target_root = self.root / target_relative
        self.out_dir = self.target_root / target / "release/build/libsqlite3-sys-0123456789abcdef/out"
        self.out_dir.mkdir(parents=True)
        self.archive = self.out_dir / names[0]
        self.archive.write_bytes(b"!<arch>\nINERT UNIT FIXTURE; NO COMPILER RAN.\n")
        for name in names[1:]:
            os.link(self.archive, self.out_dir / name)
        self.rlib = self.target_root / target / "release/deps/liblibsqlite3_sys-abcdef0123456789.rlib"
        self.rlib.parent.mkdir(parents=True)
        self.rlib.write_bytes(b"!<arch>\nINERT RUST ARCHIVE UNIT FIXTURE\n")
        features = ["bundled", "bundled_bindings", "cc", "default"]
        profile = {"debug_assertions": False, "debuginfo": 0, "opt_level": "3",
                   "overflow_checks": False, "test": False}
        self.runtime = {
            "kind": "compiler-artifact", "execution_context": "target", "roles": ["runtime", "target"],
            "feature_context_assignment": "CONFIRMED_CONTEXT", "features": features,
            "fresh": False, "profile": profile, "unit_id": "3" * 64, "message_index": 2,
            "filenames": [str(self.rlib)],
            "target": {"name": "libsqlite3_sys", "kind": ["lib"], "crate_types": ["lib"],
                       "src_path": str(self.root / "original-sources/libsqlite3-sys/src/lib.rs")},
        }
        self.script = copy.deepcopy(self.runtime)
        self.script.update(execution_context="host", roles=["build", "host"],
                           unit_id="4" * 64, message_index=0,
                           filenames=[str(self.target_root / "release/build/script")],
                           target={"name": "build-script-build", "kind": ["custom-build"],
                                   "crate_types": ["bin"], "src_path": str(self.root / "original-sources/build.rs")})
        self.link = {"package_id": native.SQLITE_ID, "message_index": 1,
                     "out_dir": str(self.out_dir), "linked_libs": ["static=sqlite3"],
                     "linked_paths": ["native=" + str(self.root / "system-path-must-not-be-read"),
                                      "native=" + str(self.out_dir)], "cfgs": []}
        self.component = {"id": native.SQLITE_ID, "name": "libsqlite3-sys", "version": "0.39.1",
                          "source": native.REGISTRY, "observed_units": [
                              self.script, {"kind": "build-script-executed", "message_index": 1},
                              self.runtime]}
        self.inventory = {"build_finished_success": True, "target_triple": target,
                          "root_package_name": "motionwright-desktop",
                          "components": [self.component], "native_link_observations": [self.link]}
        self.metadata = {"workspace_root": str(self.root), "target_directory": str(self.target_root)}
        self.notices = {"issues": [], "components": []}
        for package_id, (archive_sha, files) in native.AUTHENTICATED_SOURCES.items():
            source_root = "registry/src/unit-fixture/" + package_id.split("#")[1].replace("@", "-")
            self.notices["components"].append({
                "id": package_id, "source": native.REGISTRY, "issues": [],
                "source_authentication": {
                    "status": "VERIFIED", "kind": "registry_archive",
                    "scope": "all_regular_package_files_against_original_crate",
                    "archive_sha256": archive_sha, "source_root": source_root,
                    "files": [{"source_path": source_root + "/" + name, "bytes": size, "sha256": digest}
                              for name, (size, digest) in files.items()]},
            })
        self.artifacts = [{"path": self.rlib.relative_to(self.target_root).as_posix(),
                           **capture.stable_hash(self.target_root, self.rlib)}]
        self.save()

    def save(self):
        inventory = capture._load_sibling("cargo_build_inventory")
        for unit in (self.script, self.runtime):
            unit["package_id"] = native.SQLITE_ID
            unit["executable"] = None
            unit["unit_id"] = inventory._digest({
                key: unit[key] for key in ("package_id", "target", "profile",
                                          "features", "filenames", "executable")})
        self.messages = []
        for item in (self.script, self.link, self.runtime):
            message = {key: copy.deepcopy(value) for key, value in item.items()
                       if key not in {"unit_id", "message_index", "roles", "execution_context",
                                      "feature_context_assignment"}}
            message["reason"] = message.pop("kind", "build-script-executed")
            message["package_id"] = native.SQLITE_ID
            self.messages.append(message)
        self.messages.append({"reason": "build-finished", "success": True})
        build_raw = b"".join(json.dumps(item).encode() + b"\n" for item in self.messages)
        snapshot = {"source": self.source, "tree": self.tree,
                    "fixture_only": "INERT IDENTITY RECORD; NOT AN AUTHENTIC BUILD"}
        values = {"source-before.json": snapshot, "source-after.json": snapshot,
                  "metadata.stdout": self.metadata, "cargo-components.json": self.inventory,
                  "cargo-artifacts.json": self.artifacts, "cargo-license-sources.json": self.notices}
        for name, value in values.items():
            (self.state / name).write_bytes(capture.encoded(value))
        (self.state / "build.stdout").write_bytes(build_raw)
        for label in ("metadata", "build"):
            (self.state / (label + ".completion.json")).write_bytes(capture.encoded({
                "label": label, "returncode": 0, "errors": [], "executed_once": True,
                "cwd": str(self.root), "stdout_sha256": capture.sha(
                    (self.state / (label + ".stdout")).read_bytes())}))

    def observe(self):
        return native.observe(capture, repo=self.root, state=self.state,
                              expected_source=self.source, expected_tree=self.tree,
                              expected_target=self.target)

    def verify(self, raw, **extra):
        return native.verify(capture, raw, expected_sha256=capture.sha(raw),
                             repo=self.root, state=self.state, expected_source=self.source,
                             expected_tree=self.tree, expected_target=self.target, **extra)


class SQLiteNativeOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-sqlite-output-", dir=os.environ.get("TEMP"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = SQLiteFixture(self.root)

    def test_each_closed_target_binds_live_archive_rlib_and_originals_with_limited_claims(self):
        for index, target in enumerate(native.TARGETS):
            fixture = self.fixture if index == 0 else SQLiteFixture(self.root / str(index), target)
            record = fixture.observe()
            self.assertEqual(len(record["archives"]), 2 if index == 0 else 1)
            self.assertEqual(record["runtime_unit"]["sha256"], capture.sha(fixture.rlib.read_bytes()))
            self.assertEqual(record["original_inputs"]["cargo-artifacts.json"]["sha256"],
                             capture.sha(capture.encoded(fixture.artifacts)))
            for field in ("native_license_review", "final_linker_incorporation", "full_package_inventory"):
                self.assertEqual(record[field], "NOT_VERIFIED")
            self.assertEqual(record["redistribution_permission"], "NOT_INFERRED")
            raw = capture.encoded(record)
            self.assertEqual(fixture.verify(raw), record)

    def test_windows_accepts_hardlink_or_independent_byte_exact_copy(self):
        alias = self.fixture.out_dir / "sqlite3.lib"
        self.assertEqual(self.fixture.observe()["archives"][0]["sha256"],
                         self.fixture.observe()["archives"][1]["sha256"])
        alias.unlink()
        alias.write_bytes(self.fixture.archive.read_bytes())
        self.assertEqual(self.fixture.observe()["archives"][0]["sha256"],
                         self.fixture.observe()["archives"][1]["sha256"])

    def test_windows_refuses_missing_or_different_alias(self):
        alias = self.fixture.out_dir / "sqlite3.lib"
        alias.unlink()
        with self.assertRaisesRegex(ValueError, "archive_missing"):
            self.fixture.observe()
        alias.write_bytes(b"!<arch>\nOTHER INERT BYTES")
        with self.assertRaisesRegex(ValueError, "msvc_archive_alias_mismatch"):
            self.fixture.observe()

    def test_live_archive_change_cannot_redefine_sealed_expectation(self):
        raw = capture.encoded(self.fixture.observe())
        self.fixture.archive.write_bytes(b"!<arch>\nCHANGED INERT BYTES")
        with self.assertRaisesRegex(ValueError, "observation_changed"):
            self.fixture.verify(raw)

    def test_changed_rlib_is_rejected_against_original_artifact_roster(self):
        self.fixture.rlib.write_bytes(b"!<arch>\nCHANGED RUST ARCHIVE")
        with self.assertRaisesRegex(ValueError, "rlib_identity_mismatch"):
            self.fixture.observe()

    def test_thin_empty_and_over_budget_archives_are_refused(self):
        for raw in (b"!<thin>\nINERT", b"", b"!<arch>\n"):
            self.fixture.archive.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "archive_format_invalid"):
                self.fixture.observe()
        with self.fixture.archive.open("wb") as handle:
            handle.truncate(native.MAX_ARCHIVE + 1)
        with self.assertRaisesRegex(capture.CaptureError, "file_budget_exceeded"):
            self.fixture.observe()

    def test_host_or_ambiguous_runtime_units_are_refused(self):
        self.fixture.runtime["execution_context"] = "host"
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "runtime_unit_not_unique"):
            self.fixture.observe()
        self.fixture.runtime["execution_context"] = "target"
        self.fixture.component["observed_units"].append(copy.deepcopy(self.fixture.runtime))
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "runtime_unit_not_unique"):
            self.fixture.observe()

    def test_ambiguous_build_script_outputs_are_refused(self):
        self.fixture.inventory["native_link_observations"].append(copy.deepcopy(self.fixture.link))
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "out_dir_not_unique"):
            self.fixture.observe()

    def test_sqlcipher_loadable_and_gecko_variants_are_outside_profile(self):
        for feature in ("sqlcipher", "bundled-sqlcipher", "loadable_extension", "in_gecko"):
            prior = list(self.fixture.runtime["features"])
            changed = sorted(prior + [feature])
            self.fixture.runtime["features"] = changed
            self.fixture.script["features"] = changed
            self.fixture.save()
            with self.subTest(feature=feature), self.assertRaisesRegex(ValueError, "bundled_profile_required"):
                self.fixture.observe()
            self.fixture.runtime["features"] = prior
            self.fixture.script["features"] = prior

    def test_fresh_or_unconfirmed_unit_is_refused(self):
        for field, value in (("fresh", True), ("feature_context_assignment", "AMBIGUOUS_CONTEXT")):
            prior = self.fixture.runtime[field]
            self.fixture.runtime[field] = value
            self.fixture.save()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "compiler_unit_mismatch"):
                self.fixture.observe()
            self.fixture.runtime[field] = prior

    def test_changed_unit_identity_is_refused_even_with_unchanged_files(self):
        path = self.fixture.state / "cargo-components.json"
        for field, value in (("unit_id", "f" * 64), ("package_id", "foreign@1"),
                             ("executable", str(self.fixture.rlib))):
            self.fixture.save()
            record = json.loads(path.read_bytes())
            record["components"][0]["observed_units"][2][field] = value
            path.write_bytes(capture.encoded(record))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "compiler_unit_mismatch"):
                self.fixture.observe()

    def test_original_compiler_message_must_match_inventory(self):
        messages = copy.deepcopy(self.fixture.messages)
        messages[2]["filenames"] = [str(self.fixture.out_dir / "other.rlib")]
        raw = b"".join(json.dumps(item).encode() + b"\n" for item in messages)
        (self.fixture.state / "build.stdout").write_bytes(raw)
        p = self.fixture.state / "build.completion.json"
        value = json.loads(p.read_bytes()); value["stdout_sha256"] = capture.sha(raw)
        p.write_bytes(capture.encoded(value))
        with self.assertRaisesRegex(ValueError, "compiler_unit_mismatch"):
            self.fixture.observe()

    def test_system_search_path_does_not_supply_out_dir_authority(self):
        self.fixture.link["linked_paths"] = self.fixture.link["linked_paths"][:1]
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "own_native_search_path_missing"):
            self.fixture.observe()

    def test_outside_or_host_output_cannot_substitute_for_target_directory(self):
        outside = self.root / "foreign"; outside.mkdir()
        self.fixture.link["out_dir"] = str(outside)
        self.fixture.link["linked_paths"] = ["native=" + str(outside)]
        self.fixture.save()
        with self.assertRaisesRegex(capture.CaptureError, "path_outside_anchor"):
            self.fixture.observe()
        self.fixture.link["out_dir"] = str(self.fixture.out_dir)
        self.fixture.link["linked_paths"] = ["native=" + str(self.fixture.out_dir)]
        self.fixture.metadata["target_directory"] = str(self.root)
        self.fixture.save()
        with self.assertRaises((ValueError, capture.CaptureError)):
            self.fixture.observe()

    def test_native_source_pins_and_full_crate_authentication_are_required(self):
        proof = self.fixture.notices["components"][0]["source_authentication"]
        proof["archive_sha256"] = "0" * 64
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "source_profile_mismatch"):
            self.fixture.observe()
        proof["archive_sha256"] = native.AUTHENTICATED_SOURCES[native.SQLITE_ID][0]
        proof["files"][1]["sha256"] = "0" * 64
        self.fixture.save()
        with self.assertRaisesRegex(ValueError, "source_bytes_mismatch"):
            self.fixture.observe()

    def test_source_and_completion_identity_changes_are_rejected(self):
        path = self.fixture.state / "source-after.json"
        value = json.loads(path.read_bytes()); value["tree"] = "0" * 40
        path.write_bytes(capture.encoded(value))
        with self.assertRaisesRegex(ValueError, "source_scope_mismatch"):
            self.fixture.observe()
        self.fixture.save()
        path = self.fixture.state / "build.completion.json"
        value = json.loads(path.read_bytes()); value["stdout_sha256"] = "0" * 64
        path.write_bytes(capture.encoded(value))
        with self.assertRaisesRegex(ValueError, "completion_mismatch"):
            self.fixture.observe()

    def test_record_digest_is_checked_before_untrusted_json(self):
        with self.assertRaisesRegex(ValueError, "record_digest_mismatch"):
            native.verify(capture, b"\xff", expected_sha256="0" * 64, repo=self.root,
                          state=self.fixture.state, expected_source=self.fixture.source,
                          expected_tree=self.fixture.tree, expected_target=self.fixture.target)

    def test_capture_flag_requires_closed_desktop_generated_profile_before_effects(self):
        for invalid in ({"sqlite_native_output": "true"},
                        {"sqlite_native_output": True},
                        {"sqlite_native_output": True, "tauri_generated_schemas": True,
                         "tauri_generated_command_permissions": True}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    capture.CaptureError, "sqlite_native_output_(flag|scope)_invalid"):
                capture.capture_build(repo=self.root, expected_source="1" * 40, cargo="cargo",
                    cargo_home=self.root, target_triple="unsupported-target",
                    root_package_name="foreign", manifest_path="missing", state_directory="new-state",
                    resources_directory="resources", build_command=[], **invalid)
        self.assertFalse((self.root / "new-state").exists())

    def test_workflow_requires_observer_and_exports_only_its_record_for_each_capture(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package-candidate.yml").read_text()
        self.assertEqual(workflow.count("--sqlite-native-output"), 3)
        self.assertEqual(workflow.count('- "scripts/cargo_sqlite_native_output.py"'), 2)
        self.assertIn("-p test_cargo_sqlite_native_output.py", workflow)
        for scope in ("internal-windows-cargo", "linux-cargo", "macos-cargo"):
            self.assertIn("ci-evidence/" + scope + "/state/cargo-native-outputs.json", workflow)
            self.assertNotIn("ci-evidence/" + scope + "/state/native-output/*", workflow)


if __name__ == "__main__":
    unittest.main()
