"""Collector integration with real bounded crate and raw Git-object fixtures.

The disposable repositories are created by the existing collector fixture.
No test executes Cargo, upstream source, Native, installers, or network calls.
Import fixture modules, not their TestCase classes, to avoid duplicate rosters.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import candidate_dependency_capture as publication
import cargo_license_sources as collector
import cargo_notice_supplements as supplement
import test_cargo_license_sources as collector_fixtures
import test_cargo_notice_supplements as supplement_fixtures

INDEX_PATH = "third_party/cargo-notices/index.json"
INDEX_PARTS = tuple(INDEX_PATH.split("/"))
INDEX_SCHEMA = "motionwright.cargo-notice-supplement-index/1"
MATERIAL_PATH = "third_party/cargo-notices/fixture-1.2.3"


def encoded(value):
    # NaN is intentional in one negative fixture; the consumer must reject it.
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


class IntegrationFixture:
    """Bind the supplement fixture to an actual archive and a tracked index."""
    def __init__(self, root):
        self.collection = collector_fixtures.Fixture(root)
        self.workspace = self.collection.workspace
        self.material_root = self.workspace / MATERIAL_PATH
        self.material_root.mkdir(parents=True)
        self.upstream = supplement_fixtures.Fixture(self.material_root)
        self.attributes = self.workspace / ".gitattributes"
        self.attributes.write_bytes(b"third_party/cargo-notices/** -text\n")
        self.component = self.collection.registry("fixture", license_expression="MIT", notices={})
        self.component["id"] = self.upstream.identity["id"]
        self.package_root = Path(self.component["manifest_path"]).parent
        self.index_path = self.workspace / INDEX_PATH
        self.record_path = self.material_root / "record.json"
        self.archive_path = (self.collection.cargo / "registry/cache" /
                             collector_fixtures.INDEX / "fixture-1.2.3.crate")
        self.install_members(self.upstream.members)
        self.upstream.crate_sha = collector_fixtures.sha(self.archive_path.read_bytes())
        self.upstream.record["component"]["crate_sha256"] = self.upstream.crate_sha
        self.upstream.seal()
        self.index = {"schema": INDEX_SCHEMA, "components": [{
            "name": self.component["name"], "version": self.component["version"],
            "source": self.component["source"], "crate_sha256": self.upstream.crate_sha,
            "record": self.record_descriptor(),
        }]}
        self.write_index(self.index)

    def install_members(self, members):
        for name, raw in members.items():
            target = self.package_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        raw = collector_fixtures.crate_bytes("fixture-1.2.3", members)
        self.collection.repin_archive(self.component, raw)

    def record_descriptor(self):
        raw = self.record_path.read_bytes()
        return {"path": self.record_path.relative_to(self.workspace).as_posix(),
                "bytes": len(raw), "sha256": collector_fixtures.sha(raw)}

    def write_index(self, value, *, commit=True):
        self.index_path.write_bytes(encoded(value))
        if commit:
            self.collection.commit()

    def write_index_bytes(self, raw):
        self.index_path.write_bytes(raw)
        self.collection.commit()

    def seal_record(self):
        self.upstream.seal()
        self.index["components"][0]["record"] = self.record_descriptor()
        self.write_index(self.index)

    def notice_object(self):
        oid = supplement_fixtures.object_id("blob", self.upstream.notice)
        return self.material_root / self.upstream.objects[oid]["path"]

    def collect(self, component=None):
        return self.collection.collect([self.component if component is None else component])

    def head(self):
        return self.collection.git(self.workspace, "rev-parse", "HEAD").decode().strip()

    def remove_from_head_keep_local(self, path):
        # Commit deletion in the disposable repository, then restore only locally.
        raw = path.read_bytes()
        path.unlink()
        self.collection.commit()
        path.write_bytes(raw)

    def enable_generic_crlf_checkout(self, path):
        # The generic collector can prove this conversion. The supplemental
        # index/material boundary must still require raw tracked bytes.
        self.attributes.write_bytes(b"third_party/cargo-notices/** text\n")
        self.collection.commit()
        self.collection.git(self.workspace, "config", "core.autocrlf", "true")
        raw = path.read_bytes()
        assert b"\n" in raw and b"\r" not in raw and b"\0" not in raw
        path.write_bytes(raw.replace(b"\n", b"\r\n"))


class CargoNoticeSupplementIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("git"), "real Git fixture capability is required")
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_TERMINAL_PROMPT="0")
        self.enterContext(mock.patch.dict(os.environ, environment, clear=True))
        self.temp = tempfile.TemporaryDirectory(prefix="mw-cargo-supplement-integration-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = IntegrationFixture(Path(self.temp.name).resolve(strict=True))

    def codes(self, result):
        return {issue["code"] for issue in result["issues"]}

    def assert_blocked(self, result, code):
        self.assertIn(code, self.codes(result))
        self.assertEqual(result["texts"], {})
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(len(result["components"]), 1)
        self.assertEqual(result["components"][0]["notices"], [])
        self.assertNotEqual(result["components"][0]["source_authentication"]["status"], "VERIFIED")

    def spy(self):
        # Root's hook resolves module.verify_supplement dynamically. Keep the
        # real verifier; this records calls and never synthesizes its result.
        return mock.patch.object(supplement, "verify_supplement",
                                 wraps=supplement.verify_supplement)

    def test_registry_archive_retains_authentication_and_adds_bound_supplement(self):
        fixture = self.fixture
        expected_head = fixture.head()
        expected_index = fixture.index_path.read_bytes()
        with self.spy() as verify:
            result = fixture.collect()
        self.assertEqual(result["issues"], [])
        verify.assert_called_once()
        item = result["components"][0]
        auth = item["source_authentication"]
        self.assertEqual(auth["status"], "VERIFIED")
        self.assertEqual(auth["kind"], "registry_archive")
        self.assertEqual(auth["archive_sha256"], fixture.upstream.crate_sha)
        self.assertEqual(auth["scope"], "all_regular_package_files_against_original_crate")
        self.assertEqual(auth["license_expression_check"], "GRAMMAR_ONLY_NO_ALTERNATIVE_SELECTED")
        retained = auth["supplemental_notices"]
        index = retained["index"]
        self.assertEqual(index["source_path"], INDEX_PATH)
        self.assertEqual(index["bytes"], len(expected_index))
        self.assertEqual(index["sha256"], collector_fixtures.sha(expected_index))
        self.assertEqual(index["git_blob"], supplement_fixtures.object_id("blob", expected_index))
        self.assertEqual(index["git_line_endings"], "raw")
        self.assertEqual(index["git_head"], expected_head)
        materials = retained["materials"]
        self.assertEqual(materials["status"], "VERIFIED")
        self.assertEqual(materials["git_head"], expected_head)
        self.assertEqual(materials["source_root"], MATERIAL_PATH)
        expected_paths = {MATERIAL_PATH + "/record.json"} | {
            MATERIAL_PATH + "/" + row["path"] for row in fixture.upstream.record["objects"]}
        actual_paths = {row["source_path"] for row in materials["files"]}
        self.assertEqual(actual_paths, expected_paths)
        for row in materials["files"]:
            raw = (fixture.workspace / row["source_path"]).read_bytes()
            self.assertEqual(row["bytes"], len(raw))
            self.assertEqual(row["sha256"], collector_fixtures.sha(raw))
            self.assertEqual(row["git_blob"], supplement_fixtures.object_id("blob", raw))
            self.assertEqual(row["git_line_endings"], "raw")
        proof = retained["source_proof"]
        self.assertEqual(proof["commit"], fixture.upstream.commit)
        self.assertNotEqual(proof["commit"], expected_head)
        self.assertEqual(proof["crate_sha256"], fixture.upstream.crate_sha)
        self.assertEqual(proof["record_sha256"], fixture.index["components"][0]["record"]["sha256"])
        self.assertTrue(proof["all_archive_members_classified"])
        self.assertEqual(proof["redistribution_permission"], "NOT_INFERRED")
        self.assertEqual(proof["full_package_inventory"], "NOT_VERIFIED")
        self.assertEqual(result["texts"], {
            collector_fixtures.sha(fixture.upstream.notice): fixture.upstream.notice})
        self.assertEqual(item["notices"][0]["source_kind"], "supplemental_upstream_git")
        self.assertEqual(item["notices"][0]["provenance"]["upstream_path"], "LICENSE")
        self.assertEqual(item["notices"][0]["provenance"]["commit"], fixture.upstream.commit)

    def test_absent_index_retains_missing_notice_without_verifier(self):
        self.fixture.index_path.unlink()
        self.fixture.collection.commit()
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "license_text_missing")

    def test_index_without_component_retains_missing_notice_without_verifier(self):
        self.fixture.write_index({"schema": INDEX_SCHEMA, "components": []})
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "license_text_missing")

    def test_untracked_index_cannot_supply_an_independent_record_digest(self):
        self.fixture.remove_from_head_keep_local(self.fixture.index_path)
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "git_required_file_untracked")

    def test_modified_index_cannot_replace_the_tracked_record_digest(self):
        path = self.fixture.index_path
        path.write_bytes(path.read_bytes() + b" ")
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "git_worktree_bytes_mismatch")

    def test_untracked_material_is_rejected_before_verifier(self):
        self.fixture.remove_from_head_keep_local(self.fixture.notice_object())
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "git_worktree_members_mismatch")

    def test_modified_material_is_rejected_before_verifier(self):
        path = self.fixture.notice_object()
        path.write_bytes(path.read_bytes() + b"changed")
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "git_worktree_bytes_mismatch")

    def test_untracked_and_ignored_extra_materials_are_not_exempt(self):
        extra = self.fixture.material_root / "unlisted.txt"
        extra.write_bytes(b"Unlisted local material.\n")
        for ignored in (False, True):
            with self.subTest(ignored=ignored):
                if ignored:
                    (self.fixture.workspace / ".git/info/exclude").write_text(
                        MATERIAL_PATH + "/unlisted.txt\n", encoding="utf-8")
                with self.spy() as verify:
                    result = self.fixture.collect()
                verify.assert_not_called()
                self.assert_blocked(result, "git_worktree_members_mismatch")

    def test_tracked_extra_material_is_not_part_of_the_record_closure(self):
        (self.fixture.material_root / "unlisted.txt").write_bytes(b"Tracked but unreferenced.\n")
        self.fixture.collection.commit()
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_called_once()
        self.assert_blocked(result, "cargo_notice_supplement_record_invalid")

    def test_global_read_budget_reserves_verifier_proof_before_invocation(self):
        fixture = self.fixture
        fixture_bytes = (
            sum(row["bytes"] for row in fixture.upstream.record["objects"])
            + len(fixture.record_path.read_bytes()) + len(fixture.index_path.read_bytes())
            + len(fixture.archive_path.read_bytes()) + len(fixture.collection.lock_bytes)
            + sum(len(raw) for raw in fixture.upstream.members.values()))
        self.assertLess(fixture_bytes, supplement.MAX_PROOF - 1)
        with mock.patch.object(collector, "MAX_TOTAL_READ_BYTES", supplement.MAX_PROOF - 1), \
             self.spy() as verify:
            result = fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "collection_byte_budget_exceeded")

    def test_archive_checksum_failure_precedes_supplement_verification(self):
        path = self.fixture.archive_path
        path.write_bytes(path.read_bytes() + b"\0")
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "registry_archive_checksum_mismatch")

    def test_manifest_identity_failure_precedes_supplement_verification(self):
        changed = {**self.fixture.component, "license": "Apache-2.0"}
        with self.spy() as verify:
            result = self.fixture.collect(changed)
        verify.assert_not_called()
        self.assert_blocked(result, "component_license_metadata_mismatch")

    def test_index_entries_must_match_the_exact_lock_identity_and_checksum(self):
        changes = (("name", "other"), ("version", "9.9.9"),
                   ("source", "registry+https://example.invalid/index"),
                   ("crate_sha256", "0" * 64))
        for key, value in changes:
            with self.subTest(field=key):
                index = copy.deepcopy(self.fixture.index)
                index["components"][0][key] = value
                self.fixture.write_index(index)
                with self.spy() as verify:
                    result = self.fixture.collect()
                verify.assert_not_called()
                self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_index_record_path_is_fixed_to_the_component_namespace(self):
        for path in ("../record.json", "third_party/cargo-notices/other-1.2.3/record.json",
                     MATERIAL_PATH + "/objects/record.json", MATERIAL_PATH + "/Record.json"):
            with self.subTest(path=path):
                index = copy.deepcopy(self.fixture.index)
                index["components"][0]["record"]["path"] = path
                self.fixture.write_index(index)
                with self.spy() as verify:
                    result = self.fixture.collect()
                verify.assert_not_called()
                self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_duplicate_index_component_is_not_ambiguous_authority(self):
        index = copy.deepcopy(self.fixture.index)
        index["components"].append(copy.deepcopy(index["components"][0]))
        self.fixture.write_index(index)
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_duplicate_index_json_key_is_rejected(self):
        raw = encoded(self.fixture.index)
        duplicated = b'{"schema":' + json.dumps(INDEX_SCHEMA).encode() + b"," + raw[1:]
        self.fixture.write_index_bytes(duplicated)
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_non_json_nan_in_index_is_rejected(self):
        index = copy.deepcopy(self.fixture.index)
        index["components"][0]["record"]["bytes"] = float("nan")
        raw = encoded(index)
        self.assertIn(b"NaN", raw)
        self.fixture.write_index_bytes(raw)
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_record_bytes_and_digest_are_bound_before_verifier(self):
        for field, value in (("bytes", self.fixture.record_descriptor()["bytes"] + 1),
                             ("sha256", "0" * 64)):
            with self.subTest(field=field):
                index = copy.deepcopy(self.fixture.index)
                index["components"][0]["record"][field] = value
                self.fixture.write_index(index)
                with self.spy() as verify:
                    result = self.fixture.collect()
                verify.assert_not_called()
                self.assert_blocked(result, "cargo_notice_supplement_record_invalid")

    def test_real_verifier_failure_is_preserved_without_notice_credit(self):
        self.fixture.upstream.record["commit"] = "0" * 40
        self.fixture.seal_record()
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_called_once()
        self.assert_blocked(result, "cargo_notice_supplement_invalid")

    def test_index_mutation_after_real_verification_invalidates_collection(self):
        original = supplement.verify_supplement
        path = self.fixture.index_path
        def verify_then_mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_bytes(path.read_bytes() + b" ")
            return result
        with mock.patch.object(supplement, "verify_supplement", side_effect=verify_then_mutate) as verify:
            result = self.fixture.collect()
        verify.assert_called_once()
        self.assert_blocked(result, "git_worktree_bytes_mismatch")

    def test_material_mutation_after_real_verification_invalidates_collection(self):
        original = supplement.verify_supplement
        path = self.fixture.notice_object()
        def verify_then_mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_bytes(path.read_bytes() + b"changed")
            return result
        with mock.patch.object(supplement, "verify_supplement", side_effect=verify_then_mutate) as verify:
            result = self.fixture.collect()
        verify.assert_called_once()
        self.assert_blocked(result, "git_worktree_bytes_mismatch")

    def test_existing_archive_notice_does_not_read_or_validate_supplement_index(self):
        files = dict(self.fixture.upstream.members)
        files["LICENSE"] = collector_fixtures.NOTICE
        self.fixture.install_members(files)
        self.fixture.write_index_bytes(b'{"invalid": "supplement index must not be consulted"}\n')
        requested = []
        original = collector._GitRepo.read_tracked
        def observe(repo, relative, limit, budget):
            requested.append(relative)
            return original(repo, relative, limit, budget)
        with mock.patch.object(collector._GitRepo, "read_tracked", autospec=True, side_effect=observe), \
             self.spy() as verify:
            result = self.fixture.collect()
        self.assertEqual(result["issues"], [])
        verify.assert_not_called()
        self.assertNotIn(INDEX_PARTS, requested)
        self.assertNotIn("supplemental_notices", result["components"][0]["source_authentication"])
        self.assertEqual(result["texts"], {
            collector_fixtures.sha(collector_fixtures.NOTICE): collector_fixtures.NOTICE})
        self.assertEqual(result["components"][0]["source_authentication"]["kind"], "registry_archive")

    def test_public_component_preserves_supplement_source_proof_and_notice_provenance(self):
        result = self.fixture.collect()
        self.assertEqual(result["issues"], [])
        original = result["components"][0]
        public = publication.public_component(original, self.fixture.workspace)
        self.assertEqual(public["source_authentication"], original["source_authentication"])
        self.assertEqual(public["notices"], original["notices"])
        self.assertEqual(public["source"], self.fixture.component["source"])
        self.assertEqual(public["notices"][0]["source_kind"], "supplemental_upstream_git")
        self.assertEqual(public["notices"][0]["provenance"]["record_sha256"],
                         self.fixture.index["components"][0]["record"]["sha256"])
        self.assertEqual(public["source_authentication"]["supplemental_notices"]["source_proof"][
            "redistribution_permission"], "NOT_INFERRED")
        self.assertNotIn(str(self.fixture.workspace), json.dumps(public, sort_keys=True))

    def test_index_requires_raw_bytes_even_when_generic_git_crlf_proof_is_available(self):
        self.fixture.enable_generic_crlf_checkout(self.fixture.index_path)
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "cargo_notice_supplement_index_invalid")

    def test_material_requires_raw_bytes_even_when_generic_git_crlf_proof_is_available(self):
        self.fixture.enable_generic_crlf_checkout(self.fixture.notice_object())
        with self.spy() as verify:
            result = self.fixture.collect()
        verify.assert_not_called()
        self.assert_blocked(result, "cargo_notice_supplement_record_invalid")


if __name__ == "__main__":
    unittest.main()
