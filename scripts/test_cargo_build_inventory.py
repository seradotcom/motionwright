from copy import deepcopy
import unittest
import json
from pathlib import Path

import cargo_build_inventory as inventory


TRIPLE = "x86_64-pc-windows-gnu"
ROOT = "app"
NAMES = ("app", "runtime", "leaf", "builder", "derive", "macro_support", "dev_only")


def package_id(name):
    return "path+file:///fixture/" + name + "#" + name + "@0.1.0"


def target(name, kind="lib"):
    suffix = {"lib": "src/lib.rs", "bin": "src/main.rs",
              "proc-macro": "src/lib.rs", "custom-build": "build.rs",
              "example": "examples/demo.rs"}[kind]
    return {
        "name": "build-script-build" if kind == "custom-build" else name,
        "kind": [kind],
        "crate_types": ["bin" if kind in ("custom-build", "example") else kind],
        "src_path": "/fixture/" + name + "/" + suffix,
        "edition": "2021",
    }


def package(meta, name):
    return next(p for p in meta["packages"] if p["id"] == package_id(name))


def node(meta, name):
    return next(n for n in meta["resolve"]["nodes"] if n["id"] == package_id(name))


def edge(meta, parent, child, kind=None):
    parent_node = node(meta, parent)
    child_id = package_id(child)
    existing = next((d for d in parent_node["deps"] if d["pkg"] == child_id), None)
    if existing is None:
        existing = {"name": child, "pkg": child_id, "dep_kinds": []}
        parent_node["deps"].append(existing)
        parent_node["dependencies"].append(child_id)
    existing["dep_kinds"].append({"kind": kind, "target": None})
    package(meta, parent)["dependencies"].append({
        "name": child, "source": package(meta, child)["source"],
        "req": "^0.1.0", "kind": kind, "rename": None,
        "optional": False, "uses_default_features": True,
        "features": [], "target": None,
    })


def artifact(meta, name, kind=None):
    pkg = package(meta, name)
    chosen = next(t for t in pkg["targets"]
                  if kind is None or kind in t["kind"])
    category = chosen["kind"][0]
    filename = "/fixture/target/" + name + "/" + category + ".artifact"
    return {
        "reason": "compiler-artifact", "package_id": pkg["id"],
        "manifest_path": pkg["manifest_path"], "target": deepcopy(chosen),
        "profile": {"opt_level": "0", "debuginfo": 2,
                    "debug_assertions": True, "overflow_checks": True, "test": False},
        "features": [], "filenames": [filename],
        "executable": filename if category == "bin" else None, "fresh": False,
    }


def fixture():
    meta = {"version": 1, "packages": [],
            "workspace_members": [package_id(ROOT)],
            "resolve": {"root": package_id(ROOT), "nodes": []}}
    for name in NAMES:
        kind = "bin" if name == ROOT else "proc-macro" if name == "derive" else "lib"
        targets = [target(name, kind)]
        if name == ROOT:
            targets += [target(name, "custom-build"), target(name, "example")]
        meta["packages"].append({
            "id": package_id(name), "name": name, "version": "0.1.0",
            "source": None, "manifest_path": "/fixture/" + name + "/Cargo.toml",
            "license": "MIT", "license_file": None, "targets": targets,
            "dependencies": [], "features": {},
        })
        meta["resolve"]["nodes"].append({
            "id": package_id(name), "dependencies": [], "deps": [], "features": []})
    edge(meta, ROOT, "runtime")
    edge(meta, "runtime", "leaf")
    edge(meta, ROOT, "builder", "build")
    edge(meta, ROOT, "derive")
    edge(meta, "derive", "macro_support")
    edge(meta, ROOT, "dev_only", "dev")
    messages = [artifact(meta, name) for name in NAMES if name != "dev_only"]
    messages.insert(0, artifact(meta, ROOT, "custom-build"))
    messages.append({"reason": "build-finished", "success": True})
    return meta, messages


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.meta, self.messages = fixture()

    def derive(self):
        return inventory.derive_components(
            self.meta, self.messages, root_package_name=ROOT, target_triple=TRIPLE)

    def rejects(self, code=None):
        with self.assertRaises(inventory.InventoryError) as caught:
            self.derive()
        if code is not None:
            self.assertEqual(code, caught.exception.code)
        return caught.exception

    def unit(self, name, kind=None):
        return next(m for m in self.messages
                    if m.get("reason") == "compiler-artifact"
                    and m.get("package_id") == package_id(name)
                    and (kind is None or kind in m["target"]["kind"]))

    def remove_artifacts(self, name):
        self.messages = [m for m in self.messages
                         if not (m.get("reason") == "compiler-artifact"
                                 and m.get("package_id") == package_id(name))]

    def test_observed_components_and_dev_metadata_scope(self):
        result = self.derive()
        by_name = {p["name"]: p for p in result["components"]}
        self.assertEqual(set(NAMES) - {"dev_only"}, set(by_name))
        self.assertEqual(["runtime", "target"], by_name["leaf"]["roles"])
        self.assertEqual(["build", "host"], by_name["builder"]["roles"])
        self.assertEqual(["host", "proc-macro"], by_name["derive"]["roles"])
        self.assertEqual(["host"], by_name["macro_support"]["roles"])
        self.assertEqual(7, result["compiler_unit_count"])
        self.assertEqual(1, len(result["metadata_only_development_targets"]))
        self.assertTrue(any("examples/tests/benches" in w for w in result["warnings"]))
        self.assertEqual("NOT_VERIFIED", result["full_package_inventory"])
        self.assertEqual("NOT_ASSESSED", result["license_rights"])

    def test_transitive_runtime_omission_is_rejected(self):
        self.remove_artifacts("leaf")
        self.rejects("runtime_package_not_observed")

    def test_diagnostic_does_not_replace_runtime_artifact(self):
        original = deepcopy(self.unit("leaf"))
        self.remove_artifacts("leaf")
        self.messages.insert(0, {"reason": "compiler-message",
                                "package_id": original["package_id"],
                                "target": original["target"], "message": {}})
        self.rejects("runtime_package_not_observed")

    def test_missing_terminal_success_is_rejected(self):
        self.messages.pop()
        self.rejects("build_finished_not_unique_last")

    def test_duplicate_terminal_success_is_rejected(self):
        self.messages.append(deepcopy(self.messages[-1]))
        self.rejects("build_finished_not_unique_last")

    def test_failure_terminal_is_rejected(self):
        self.messages[-1]["success"] = False
        self.rejects("build_failed")

    def test_terminal_success_must_be_boolean(self):
        self.messages[-1]["success"] = 1
        self.rejects("build_success_invalid")

    def test_foreign_resolve_root_is_rejected(self):
        self.meta["resolve"]["root"] = package_id("dev_only")
        self.rejects("resolve_root_mismatch")

    def test_root_build_script_does_not_replace_root_binary(self):
        self.messages = [m for m in self.messages
                         if not (m.get("package_id") == package_id(ROOT)
                                 and m.get("target", {}).get("kind") == ["bin"])]
        self.rejects("root_binary_not_observed")

    def test_actual_dev_dependency_unit_is_rejected(self):
        self.messages.insert(0, artifact(self.meta, "dev_only"))
        self.rejects("observed_package_outside_admitted_graph")

    def test_test_profile_unit_is_rejected(self):
        self.unit("runtime")["profile"]["test"] = True
        self.rejects("development_unit_forbidden")

    def test_example_metadata_is_allowed_but_example_build_unit_is_rejected(self):
        self.derive()
        self.messages.insert(0, artifact(self.meta, ROOT, "example"))
        self.rejects("development_unit_forbidden")

    def test_same_package_host_and_target_units_keep_features_and_ambiguity(self):
        edge(self.meta, ROOT, "runtime", "build")
        node(self.meta, "runtime")["features"] = ["host-feature", "target-feature"]
        package(self.meta, "runtime")["features"] = {"host-feature": [], "target-feature": []}
        first = self.unit("runtime")
        first["features"] = ["target-feature"]
        other = deepcopy(first)
        other["features"] = ["host-feature"]
        other["filenames"] = ["/fixture/other-output/runtime.lib"]
        self.messages.insert(0, other)
        result = self.derive()
        component = next(p for p in result["components"] if p["name"] == "runtime")
        self.assertEqual(["build", "host", "runtime", "target"], component["roles"])
        units = component["observed_units"]
        self.assertEqual(2, len(units))
        self.assertEqual({("host-feature",), ("target-feature",)},
                         {tuple(u["features"]) for u in units})
        self.assertTrue(all(u["execution_context"] == "NOT_DETERMINABLE_FROM_CARGO_JSON"
                            for u in units))
        self.assertNotEqual(units[0]["unit_id"], units[1]["unit_id"])

    def test_native_static_sqlite_link_does_not_claim_native_license(self):
        self.messages.insert(0, {
            "reason": "build-script-executed", "package_id": package_id(ROOT),
            "linked_libs": ["static=sqlite3"],
            "linked_paths": ["native=/fixture/native/sqlite"],
            "cfgs": [], "env": [], "out_dir": "/fixture/target/app/out",
        })
        result = self.derive()
        self.assertEqual(1, len(result["native_link_observations"]))
        observation = result["native_link_observations"][0]
        self.assertEqual(["static=sqlite3"], observation["linked_libs"])
        self.assertEqual("NOT_VERIFIED", observation["native_library_identity"])
        self.assertEqual("NOT_VERIFIED", observation["native_library_license"])
        self.assertNotIn("sqlite3", {p["name"] for p in result["components"]})
        self.assertEqual("METADATA_DECLARATION_ONLY",
                         next(p for p in result["components"] if p["name"] == ROOT)
                         ["license_evidence"])

    def test_build_script_output_cannot_replace_runtime_library(self):
        package(self.meta, "leaf")["targets"].append(target("leaf", "custom-build"))
        self.remove_artifacts("leaf")
        self.messages.insert(0, {
            "reason": "build-script-executed", "package_id": package_id("leaf"),
            "linked_libs": [], "linked_paths": [], "cfgs": [], "env": [],
            "out_dir": "/fixture/target/leaf/out",
        })
        self.rejects("runtime_package_not_observed")

    def test_source_package_id_mismatch_is_rejected(self):
        self.unit("runtime")["package_id"] = (
            "registry+https://example.invalid/index#runtime@0.1.0")
        self.rejects("observed_package_identity_unknown")

    def test_manifest_identity_mismatch_is_rejected(self):
        self.unit("runtime")["manifest_path"] = "/fixture/other/Cargo.toml"
        self.rejects("artifact_manifest_identity_mismatch")

    def test_target_identity_mismatch_is_rejected(self):
        self.unit("runtime")["target"]["src_path"] = "/fixture/runtime/src/other.rs"
        self.rejects("observed_target_identity_mismatch")

    def test_artifact_cannot_introduce_undeclared_features(self):
        self.unit("runtime")["features"] = ["unreported-feature"]
        self.rejects("artifact_features_outside_metadata")

    def test_duplicate_compiler_unit_is_rejected(self):
        self.messages.insert(0, deepcopy(self.unit("leaf")))
        self.rejects("duplicate_compiler_unit")

    def test_different_units_cannot_claim_same_output_filename(self):
        self.unit("leaf")["filenames"] = deepcopy(self.unit("runtime")["filenames"])
        self.rejects("artifact_filename_identity_collision")

    def test_root_binary_requires_observed_executable(self):
        self.unit(ROOT, "bin")["executable"] = None
        self.rejects("root_binary_executable_missing")

    def test_executable_must_be_one_of_artifact_outputs(self):
        self.unit(ROOT, "bin")["executable"] = "/fixture/foreign/executable.exe"
        self.rejects("executable_not_in_artifact_filenames")

    def test_null_declared_license_does_not_invent_license_rights(self):
        package(self.meta, "runtime")["license"] = None
        result = self.derive()
        component = next(p for p in result["components"] if p["name"] == "runtime")
        self.assertIsNone(component["license"])
        self.assertEqual("METADATA_DECLARATION_ONLY", component["license_evidence"])
        self.assertEqual("NOT_ASSESSED", result["license_rights"])

    def test_unobserved_host_metadata_entry_is_not_manufactured_as_component(self):
        self.remove_artifacts("builder")
        result = self.derive()
        self.assertNotIn("builder", {p["name"] for p in result["components"]})
        pending = {p["id"]: p for p in result["unobserved_host_graph_packages"]}
        self.assertEqual("NOT_OBSERVED", pending[package_id("builder")]["status"])

    def test_duplicate_package_identity_is_rejected(self):
        self.meta["packages"].append(deepcopy(package(self.meta, "leaf")))
        self.rejects()

    def test_duplicate_resolve_node_is_rejected(self):
        self.meta["resolve"]["nodes"].append(deepcopy(node(self.meta, "leaf")))
        self.rejects()

    def test_missing_transitive_resolve_node_is_rejected(self):
        self.meta["resolve"]["nodes"] = [n for n in self.meta["resolve"]["nodes"]
                                          if n["id"] != package_id("leaf")]
        self.rejects("resolve_node_missing")

    def test_resolve_dependencies_and_dep_kinds_must_agree(self):
        node(self.meta, "runtime")["dependencies"] = []
        self.rejects()

    def test_missing_artifact_fields_are_rejected(self):
        del self.unit("leaf")["fresh"]
        self.rejects("artifact_missing_field")

    def test_invalid_artifact_boolean_type_is_rejected(self):
        self.unit("leaf")["fresh"] = "false"
        self.rejects("artifact_fresh_invalid")

    def test_invalid_package_source_type_is_rejected(self):
        package(self.meta, "leaf")["source"] = 37
        self.rejects()

    def test_non_json_value_is_rejected(self):
        self.meta["untrusted_extension"] = object()
        self.rejects()

    def test_message_count_is_bounded(self):
        self.messages = [{"reason": "build-finished", "success": True}] * (
            inventory.MAX_MESSAGES + 1)
        self.rejects("messages_invalid")

    def test_message_after_terminal_is_rejected(self):
        self.messages.append(deepcopy(self.unit("leaf")))
        self.rejects("build_finished_not_unique_last")

    def test_unknown_message_reason_is_rejected(self):
        self.messages.insert(0, {"reason": "untrusted-result"})
        self.rejects("message_reason_unsupported")

    def test_artifact_path_traversal_is_rejected_without_file_access(self):
        self.unit("leaf")["filenames"] = ["/fixture/target/../foreign/leaf.lib"]
        self.rejects("artifact_filenames_invalid")

    def test_inputs_are_not_mutated(self):
        original_meta = deepcopy(self.meta)
        original_messages = deepcopy(self.messages)
        self.derive()
        self.assertEqual(original_meta, self.meta)
        self.assertEqual(original_messages, self.messages)



class FeatureAdmissionTests(unittest.TestCase):
    setUp = InventoryTests.setUp
    derive = InventoryTests.derive
    rejects = InventoryTests.rejects
    unit = InventoryTests.unit

    def optional_child(self, program, *, parent_features=("enable",),
                       alias="dev_only", child_features=None):
        edge(self.meta, "runtime", "dev_only")
        declaration = package(self.meta, "runtime")["dependencies"][-1]
        declaration["optional"] = True
        declaration["rename"] = None if alias == "dev_only" else alias
        dependency = next(d for d in node(self.meta, "runtime")["deps"]
                          if d["pkg"] == package_id("dev_only"))
        dependency["name"] = alias
        package(self.meta, "runtime")["features"]["enable"] = list(program)
        node(self.meta, "runtime")["features"] = ["enable"]
        self.unit("runtime")["features"] = list(parent_features)
        package(self.meta, "dev_only")["features"] = dict(child_features or {})
        node(self.meta, "dev_only")["features"] = sorted(child_features or {})
        return declaration

    def observe_child(self, features=()):
        message = artifact(self.meta, "dev_only")
        message["features"] = list(features)
        self.messages.insert(0, message)
        return message

    def test_workspace_resolve_union_does_not_require_inactive_optional(self):
        self.optional_child(["dep:dev_only"], parent_features=())
        result = self.derive()
        self.assertNotIn("dev_only", {p["name"] for p in result["components"]})
        self.assertEqual(["INACTIVE_IN_OBSERVED_UNITS"],
                         [e["status"] for e in result["feature_admission"]])

    def test_active_optional_without_artifact_still_refuses(self):
        self.optional_child(["dep:dev_only"])
        self.rejects("runtime_package_not_observed")

    def test_active_optional_with_artifact_has_confirmed_runtime_role(self):
        self.optional_child(["dep:dev_only"])
        self.observe_child()
        child = next(p for p in self.derive()["components"] if p["name"] == "dev_only")
        self.assertEqual(["runtime", "target"], child["roles"])

    def test_weak_forward_does_not_activate_optional_dependency(self):
        self.optional_child(["dev_only?/extra"], child_features={"extra": []})
        self.assertNotIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_weak_forward_requires_feature_when_dependency_is_active(self):
        self.optional_child(["dep:dev_only", "dev_only?/extra"],
                            child_features={"extra": []})
        child_unit = self.observe_child()
        self.rejects("dependency_feature_vector_not_observed")
        child_unit["features"] = ["extra"]
        self.assertIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_strong_forward_activates_dependency_without_dep_prefix(self):
        self.optional_child(["dev_only/extra"], child_features={"extra": []})
        self.observe_child(["extra"])
        self.assertIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_rename_uses_manifest_alias_and_retains_package_identity(self):
        self.optional_child(["dep:renamed_optional"], alias="renamed_optional")
        self.observe_child()
        child = next(p for p in self.derive()["components"] if p["name"] == "dev_only")
        self.assertEqual(package_id("dev_only"), child["id"])
        package(self.meta, "runtime")["features"]["enable"] = ["dep:dev_only"]
        self.rejects("feature_dependency_alias_unknown")

    def test_local_feature_chain_activates_optional_dependency(self):
        self.optional_child(["local"])
        package(self.meta, "runtime")["features"]["local"] = ["dep:dev_only"]
        node(self.meta, "runtime")["features"] = ["enable", "local"]
        self.unit("runtime")["features"] = ["enable", "local"]
        self.observe_child()
        self.assertIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_host_only_feature_is_not_claimed_as_confirmed_runtime(self):
        self.optional_child(["dep:dev_only"], parent_features=())
        edge(self.meta, ROOT, "runtime", "build")
        other = deepcopy(self.unit("runtime"))
        other["features"] = ["enable"]
        other["filenames"] = ["/fixture/distinct-unit/runtime.artifact"]
        self.messages.insert(0, other)
        self.observe_child()
        result = self.derive()
        child = next(p for p in result["components"] if p["name"] == "dev_only")
        self.assertNotIn("runtime", child["roles"])
        self.assertEqual(["build", "host", "runtime", "target"], child["possible_roles"])
        self.assertTrue(all(u["feature_context_assignment"] ==
                            "NOT_DETERMINABLE_FROM_CARGO_JSON"
                            for u in child["observed_units"]))
        shared = next(p for p in result["components"] if p["name"] == "runtime")
        self.assertEqual(["build", "host", "runtime", "target"], shared["roles"])
        self.assertEqual({(), ("enable",)},
                         {tuple(u["features"]) for u in shared["observed_units"]})

    def test_default_and_explicit_features_cannot_be_unioned_across_units(self):
        declaration = self.optional_child(["dep:dev_only"],
                                          child_features={"default": [], "extra": []})
        declaration["features"] = ["extra"]
        first = self.observe_child(["default"])
        second = deepcopy(first)
        second["features"] = ["extra"]
        second["filenames"] = ["/fixture/other-unit/dev_only.artifact"]
        self.messages.insert(0, second)
        self.rejects("dependency_feature_vector_not_observed")
        first["features"] = ["default", "extra"]
        self.assertIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_disabled_default_features_do_not_require_default_unit_feature(self):
        declaration = self.optional_child(["dep:dev_only"],
                                          child_features={"default": [], "extra": []})
        declaration["uses_default_features"] = False
        declaration["features"] = ["extra"]
        self.observe_child(["extra"])
        self.assertIn("dev_only", {p["name"] for p in self.derive()["components"]})

    def test_active_declared_dependency_cannot_disappear_from_resolve(self):
        self.optional_child(["dep:dev_only"])
        self.observe_child()
        current = node(self.meta, "runtime")
        current["deps"] = [d for d in current["deps"] if d["pkg"] != package_id("dev_only")]
        current["dependencies"].remove(package_id("dev_only"))
        self.rejects("active_dependency_missing_from_resolve")

    def test_target_predicate_is_part_of_declaration_identity(self):
        declaration = self.optional_child(["dep:dev_only"])
        declaration["target"] = "cfg(windows)"
        dependency = next(d for d in node(self.meta, "runtime")["deps"]
                          if d["pkg"] == package_id("dev_only"))
        dependency["dep_kinds"][0]["target"] = "cfg(windows)"
        self.observe_child()
        self.derive()
        declaration["target"] = "cfg(unix)"
        self.rejects("resolve_declaration_not_unique")


class RetainedCargoBuildTests(unittest.TestCase):
    CAPTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "cargo_inventory"

    def captured(self):
        metadata = json.loads((self.CAPTURE / "metadata.stdout").read_text(encoding="utf-8"))
        messages = [json.loads(line) for line in
                    (self.CAPTURE / "build.stdout").read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        return metadata, messages

    def test_real_build_does_not_manufacture_workspace_optional_package(self):
        metadata, messages = self.captured()
        self.assertIn("fixture-never", {p["name"] for p in metadata["packages"]})
        result = inventory.derive_components(metadata, messages,
                    root_package_name="inventory-fixture-app", target_triple=TRIPLE)
        observed = {m["package_id"] for m in messages
                    if m["reason"] in ("compiler-artifact", "build-script-executed")}
        self.assertEqual(observed, {p["id"] for p in result["components"]})
        self.assertNotIn("fixture-never", {p["name"] for p in result["components"]})
        self.assertEqual("NOT_VERIFIED", result["full_package_inventory"])
        self.assertTrue(result["root_binary_observations"])

    def test_real_build_missing_mandatory_dependency_artifact_still_refuses(self):
        metadata, messages = self.captured()
        packages = {p["id"]: p for p in metadata["packages"]}
        root = next(p for p in packages.values() if p["name"] == "inventory-fixture-app")
        root_node = next(n for n in metadata["resolve"]["nodes"] if n["id"] == root["id"])
        candidates = [d["pkg"] for d in root_node["deps"]
                      if any(k["kind"] in (None, "normal") for k in d["dep_kinds"])
                      and not any("proc-macro" in t["kind"] for t in packages[d["pkg"]]["targets"])
                      and any(decl["name"] == packages[d["pkg"]]["name"]
                              and decl["kind"] in (None, "normal") and not decl["optional"]
                              for decl in root["dependencies"])]
        self.assertTrue(candidates, "retained fixture must have a mandatory normal library")
        victim = candidates[0]
        self.assertTrue(any(m.get("package_id") == victim and m["reason"] == "compiler-artifact"
                            for m in messages))
        messages = [m for m in messages if not
                    (m.get("package_id") == victim and m["reason"] == "compiler-artifact")]
        with self.assertRaises(inventory.InventoryError) as caught:
            inventory.derive_components(metadata, messages,
                        root_package_name="inventory-fixture-app", target_triple=TRIPLE)
        self.assertEqual("runtime_package_not_observed", caught.exception.code)



class SourceResolutionTests(unittest.TestCase):
    setUp = InventoryTests.setUp
    derive = InventoryTests.derive
    rejects = InventoryTests.rejects
    def runtime_declaration(self):
        return next(d for d in package(self.meta, ROOT)["dependencies"]
                    if d["name"] == "runtime" and d["kind"] is None)
    def test_source_override_records_declared_and_effective_identity(self):
        for declared in (
            "registry+https://github.com/rust-lang/crates.io-index",
            "git+https://example.invalid/runtime?rev=original",
        ):
            with self.subTest(declared_source=declared):
                self.setUp()
                self.runtime_declaration()["source"] = declared
                result = self.derive()
                observations = [r for r in result["source_resolutions"]
                                if r["parent_id"] == package_id(ROOT)
                                and r["package_id"] == package_id("runtime")
                                and r["kind"] == "normal"]
                self.assertEqual(1, len(observations))
                observed = observations[0]
                self.assertEqual(declared, observed["declared_source"])
                self.assertIsNone(observed["effective_source"])
                self.assertEqual("CARGO_RESOLVE_GRAPH", observed["resolution_origin"])
                self.assertEqual("CALLER_REQUIRED", observed["source_authentication"])
                self.assertEqual("RESOLVED_EDGE_ONLY", observed["emission_evidence"])
                self.assertTrue(observed["active_in_observed_feature_graph"])
                component = next(c for c in result["components"]
                                 if c["id"] == package_id("runtime"))
                self.assertIsNone(component["source"])
                self.assertEqual({package_id("runtime")},
                                 {u["package_id"] for u in component["observed_units"]})
                self.assertEqual("NOT_VERIFIED", result["full_package_inventory"])
    def test_source_override_does_not_bypass_dependency_name_or_alias(self):
        for field, value in (("name", "unrelated_package"),
                             ("rename", "different_alias")):
            with self.subTest(field=field):
                self.setUp()
                declaration = self.runtime_declaration()
                declaration["source"] = "registry+https://github.com/rust-lang/crates.io-index"
                declaration[field] = value
                self.rejects("resolve_declaration_not_unique")


if __name__ == "__main__":
    unittest.main()
