"""Inert synthetic object-graph fixtures; no network, Git command or product acceptance."""
from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cargo_notice_supplements as supplement


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def object_id(kind, raw):
    return hashlib.sha1((kind + " " + str(len(raw))).encode() + b"\0" + raw).hexdigest()


def manifest(inherited=False):
    common = '[package]\nname="fixture"\n'
    fields = ('version.workspace=true\nlicense.workspace=true\nrepository.workspace=true\n'
              if inherited else 'version="1.2.3"\nlicense="MIT"\nrepository="https://github.com/Nugine/simd"\n')
    return (common + fields).encode()


class Fixture:
    def __init__(self, root, inherited=False):
        self.root = root
        (root / "objects").mkdir()
        self.identity = {"id": supplement.REGISTRY + "#fixture@1.2.3", "name": "fixture",
                         "version": "1.2.3", "source": supplement.REGISTRY, "license": "MIT"}
        self.crate_sha = digest(b"synthetic authenticated crate fixture")
        self.package = "crates/fixture"
        self.notice = b"Original fixture copyright and notice.\n"
        self.original = manifest(inherited)
        self.files = {self.package + "/Cargo.toml": self.original,
                      self.package + "/src/lib.rs": b"pub fn fixture() {}\n", "LICENSE": self.notice}
        self.workspace = "Cargo.toml" if inherited else None
        if inherited:
            self.files["Cargo.toml"] = (
                b'[workspace]\nmembers=["crates/fixture"]\n[workspace.package]\n'
                b'version="1.2.3"\nlicense="MIT"\nrepository="https://github.com/Nugine/simd"\n')
        self.modes = {}
        self.extra_members = {}
        self.rebuild()

    def put(self, kind, raw):
        oid = object_id(kind, raw)
        relative = "objects/" + oid
        (self.root / relative).write_bytes(raw)
        self.objects[oid] = {"type": kind, "oid": oid, "path": relative,
                             "bytes": len(raw), "sha256": digest(raw)}
        return oid

    def tree(self, prefix=""):
        direct, directories = {}, set()
        for path, raw in self.files.items():
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" in rest:
                directories.add(rest.split("/", 1)[0])
            else:
                direct[rest] = (self.modes.get(path, "100644"), self.put("blob", raw))
        for name in directories:
            direct[name] = ("40000", self.tree(prefix + name + "/"))
        ordered = sorted(direct, key=lambda name: name.encode() + (b"/" if direct[name][0] == "40000" else b""))
        raw = b"".join(direct[name][0].encode() + b" " + name.encode() + b"\0"
                       + bytes.fromhex(direct[name][1]) for name in ordered)
        return self.put("tree", raw)

    def commit_body(self, root_oid):
        return (b"tree " + root_oid.encode() + b"\nauthor Fixture <fixture@example.invalid> 0 +0000\n"
                b"committer Fixture <fixture@example.invalid> 0 +0000\n"
                b"gpgsig retained fixture signature\n tree not-a-header-inside-continuation\n\nFixture message\n")

    def rebuild(self):
        self.objects = {}
        self.root_oid = self.tree()
        self.commit = self.put("commit", self.commit_body(self.root_oid))
        self.members = {path[len(self.package) + 1:]: raw for path, raw in self.files.items()
                        if path.startswith(self.package + "/")}
        self.members["Cargo.toml.orig"] = self.members.pop("Cargo.toml")
        self.members["Cargo.toml"] = manifest()
        self.members[".cargo_vcs_info.json"] = json.dumps(
            {"git": {"sha1": self.commit}, "path_in_vcs": self.package}).encode()
        self.members.update(self.extra_members)
        special = {"Cargo.toml": "cargo_normalized_manifest", "Cargo.toml.orig": "git_package_manifest",
                   ".cargo_vcs_info.json": "cargo_vcs_metadata", "Cargo.lock": "cargo_packaged_lock"}
        self.record = {
            "schema": "motionwright.cargo-notice-supplement/1",
            "component": {**self.identity, "crate_sha256": self.crate_sha},
            "repository": "https://github.com/Nugine/simd", "commit": self.commit,
            "package_path": self.package, "workspace_manifest": self.workspace,
            "objects": list(self.objects.values()),
            "members": [{"path": path, "kind": special.get(path, "git_exact"),
                         "bytes": len(raw), "sha256": digest(raw)} for path, raw in self.members.items()],
            "notices": [{"kind": "upstream_git", "upstream_path": "LICENSE",
                         "declared_by": self.package + "/Cargo.toml"}],
        }
        self.seal()

    def seal(self):
        raw = (json.dumps(self.record, sort_keys=True) + "\n").encode()
        (self.root / "record.json").write_bytes(raw)
        self.record_sha = digest(raw)

    def change_member(self, name, raw):
        self.members[name] = raw
        row = next(row for row in self.record["members"] if row["path"] == name)
        row.update(bytes=len(raw), sha256=digest(raw))
        self.seal()

    def change_object(self, oid, raw):
        row = next(row for row in self.record["objects"] if row["oid"] == oid)
        (self.root / row["path"]).write_bytes(raw)
        row.update(bytes=len(raw), sha256=digest(raw))
        self.seal()

    def repoint_commit(self, raw):
        self.commit = self.put("commit", raw)
        self.record["commit"] = self.commit
        self.record["objects"] = list(self.objects.values())
        self.change_member(".cargo_vcs_info.json", json.dumps(
            {"git": {"sha1": self.commit}, "path_in_vcs": self.package}).encode())

    def call(self, **changes):
        arguments = {"expected_record_sha256": self.record_sha,
                     "component_identity": self.identity,
                     "crate_members": self.members, "expected_crate_sha256": self.crate_sha}
        arguments.update(changes)
        return supplement.verify_supplement(self.root, **arguments)


class CargoNoticeSupplementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-notice-supplement-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(Path(self.temp.name))

    def blocked(self, code, callback=None):
        with self.assertRaises(supplement.SupplementError) as caught:
            (callback or self.fixture.call)()
        self.assertEqual(str(caught.exception), "cargo_supplement_" + code)

    def test_raw_commit_tree_blob_chain_and_lazy_members_preserve_originals(self):
        fixture = self.fixture
        counts = {}
        class Lazy(Mapping):
            def __len__(self):
                return len(fixture.members)
            def __iter__(self):
                return iter(fixture.members)
            def __getitem__(self, key):
                counts[key] = counts.get(key, 0) + 1
                return fixture.members[key]
        before = copy.deepcopy(fixture.members)
        result = fixture.call(crate_members=Lazy())
        self.assertEqual(counts, {key: 1 for key in fixture.members})
        self.assertEqual(fixture.members, before)
        self.assertEqual(result.texts, {digest(fixture.notice): fixture.notice})
        self.assertEqual(result.notices[0]["source_kind"], "supplemental_upstream_git")
        self.assertEqual(result.notices[0]["provenance"]["commit"], fixture.commit)
        self.assertEqual(result.source_proof["git_root_tree"], fixture.root_oid)
        self.assertTrue(result.source_proof["all_archive_members_classified"])
        self.assertEqual(result.source_proof["redistribution_permission"], "NOT_INFERRED")
        self.assertEqual(result.source_proof["full_package_inventory"], "NOT_VERIFIED")

    def test_record_requires_independently_expected_digest(self):
        self.blocked("record_digest_mismatch",
                     lambda: self.fixture.call(expected_record_sha256="0" * 64))

    def test_changed_complete_commit_body_is_not_proved_by_same_tree(self):
        row = self.fixture.objects[self.fixture.commit]
        raw = (self.fixture.root / row["path"]).read_bytes()
        self.fixture.change_object(self.fixture.commit, raw.replace(b"Fixture message", b"Altered message"))
        self.blocked("object_oid_mismatch")

    def test_api_json_is_not_a_raw_commit(self):
        self.fixture.repoint_commit(json.dumps({"tree": {"sha": self.fixture.root_oid}}).encode())
        self.blocked("commit_malformed")

    def test_two_tree_headers_are_rejected_even_with_a_matching_object_id(self):
        raw = b"tree " + self.fixture.root_oid.encode() + b"\n" + self.fixture.commit_body(self.fixture.root_oid)
        self.fixture.repoint_commit(raw)
        self.blocked("commit_tree_ambiguous")

    def test_tree_bytes_are_hashed_before_path_lookup(self):
        row = self.fixture.objects[self.fixture.root_oid]
        raw = bytearray((self.fixture.root / row["path"]).read_bytes())
        raw[-1] ^= 1
        self.fixture.change_object(self.fixture.root_oid, bytes(raw))
        self.blocked("object_oid_mismatch")

    def test_blob_bytes_cannot_be_replaced_by_updating_sha256_only(self):
        oid = object_id("blob", self.fixture.notice)
        self.fixture.change_object(oid, b"Substituted original notice.\n")
        self.blocked("object_oid_mismatch")

    def test_reachable_readme_is_not_a_supplemental_notice(self):
        self.fixture.files["README.md"] = b"Authentic upstream README, not a license notice.\n"
        self.fixture.rebuild()
        self.fixture.record["notices"][0]["upstream_path"] = "README.md"
        self.fixture.seal()
        self.blocked("notice_name_invalid")

    def test_upstream_notice_basename_preserves_existing_notice_convention(self):
        for name in ("LICENSE", "LICENCE.txt", "copying", "Notice-BSD",
                     "COPYRIGHT_notice", "LICENSE-MIT", "LICENSE-APACHE-2.0", "LICENSE.spdx"):
            with self.subTest(name=name):
                self.fixture.files[name] = self.fixture.notice
                self.fixture.rebuild()
                self.fixture.record["notices"][0]["upstream_path"] = name
                self.fixture.seal()
                result = self.fixture.call()
                self.assertEqual(result.notices[0]["source_path"], name)
                self.assertEqual(result.texts, {digest(self.fixture.notice): self.fixture.notice})

    def test_notice_must_be_reachable_at_the_explicit_ancestor_path(self):
        self.fixture.record["notices"][0]["upstream_path"] = "LICENSE.absent"
        self.fixture.seal()
        self.blocked("git_blob_path_invalid")
        self.fixture.record["notices"][0]["upstream_path"] = "unrelated/LICENSE"
        self.fixture.seal()
        self.blocked("notice_association_invalid")

    def test_selected_git_symlink_and_submodule_are_not_followed(self):
        for mode in ("120000", "160000"):
            with self.subTest(mode=mode):
                self.fixture.modes["LICENSE"] = mode
                self.fixture.rebuild()
                self.blocked("git_blob_path_invalid")

    def test_duplicate_tree_entries_are_not_a_path_proof(self):
        oid = object_id("blob", self.fixture.notice)
        entry = b"100644 LICENSE\0" + bytes.fromhex(oid)
        new_root = self.fixture.put("tree", entry + entry)
        self.fixture.repoint_commit(self.fixture.commit_body(new_root))
        self.blocked("tree_alias_or_order")

    def test_original_manifest_and_all_source_members_must_match_git(self):
        self.fixture.change_member("src/lib.rs", b"pub fn injected() {}\n")
        self.blocked("source_git_mismatch")
        self.fixture.rebuild()
        self.fixture.change_member("Cargo.toml.orig", manifest() + b"# changed\n")
        self.blocked("original_manifest_git_mismatch")

    def test_unlisted_member_and_missing_source_are_rejected(self):
        self.fixture.members["injected.rs"] = b"unreviewed"
        self.blocked("member_roster_mismatch")
        del self.fixture.members["injected.rs"]
        del self.fixture.members["src/lib.rs"]
        self.blocked("member_roster_mismatch")

    def test_vcs_dirty_commit_path_and_component_substitution_are_rejected(self):
        fixture = self.fixture
        for changed in (
            {"git": {"sha1": fixture.commit, "dirty": True}, "path_in_vcs": fixture.package},
            {"git": {"sha1": "0" * 40}, "path_in_vcs": fixture.package},
            {"git": {"sha1": fixture.commit}, "path_in_vcs": "other"},
        ):
            with self.subTest(vcs=changed):
                fixture.change_member(".cargo_vcs_info.json", json.dumps(changed).encode())
                self.blocked("vcs_mismatch_or_dirty")
        fixture.rebuild()
        self.blocked("component_binding_mismatch",
                     lambda: fixture.call(expected_crate_sha256="0" * 64))

    def test_workspace_inheritance_is_bound_to_reachable_original_manifests(self):
        self.fixture.original = manifest(True)
        self.fixture.files[self.fixture.package + "/Cargo.toml"] = self.fixture.original
        self.fixture.files["Cargo.toml"] = (
            b'[workspace]\nmembers=["crates/fixture"]\n[workspace.package]\n'
            b'version="1.2.3"\nlicense="MIT"\nrepository="https://github.com/Nugine/simd"\n')
        self.fixture.workspace = "Cargo.toml"
        self.fixture.rebuild()
        self.assertTrue(self.fixture.call().source_proof["all_archive_members_classified"])
        self.fixture.files["crates/Cargo.toml"] = b'[workspace]\n[workspace.package]\nlicense="MIT"\n'
        self.fixture.rebuild()
        self.blocked("workspace_mismatch")

    def test_packaged_cargo_lock_is_classified_without_git_byte_credit(self):
        fixture = self.fixture
        fixture.extra_members["Cargo.lock"] = (
            b'version=4\n[[package]]\nname="fixture"\nversion="1.2.3"\n')
        fixture.rebuild()
        result = fixture.call()
        row = next(row for row in result.source_proof["members"] if row["path"] == "Cargo.lock")
        self.assertEqual(row["kind"], "cargo_packaged_lock")
        self.assertFalse(row["git_byte_match"])
        fixture.change_member("Cargo.lock", b'version=4\n[[package]]\nname="other"\nversion="1"\n')
        self.blocked("packaged_lock_component_mismatch")

    def test_packaged_lock_cannot_exempt_source_code_or_malformed_lock(self):
        row = next(row for row in self.fixture.record["members"] if row["path"] == "src/lib.rs")
        row["kind"] = "cargo_packaged_lock"
        self.fixture.seal()
        self.blocked("packaged_lock_path_invalid")
        self.fixture.extra_members["Cargo.lock"] = b'version=4\npackage=[]\ncode="unreviewed"\n'
        self.fixture.rebuild()
        self.blocked("packaged_lock_invalid")

    def test_source_changes_are_not_normalized_implicitly(self):
        self.fixture.change_member("src/lib.rs", b"pub fn fixture() {}\r\n")
        self.blocked("source_git_mismatch")

    def test_lf_to_crlf_rule_is_explicit_scoped_and_reports_the_transform(self):
        fixture = self.fixture
        name = "src/lib.rs"
        original = fixture.members[name]
        packaged = original.replace(b"\n", b"\r\n")
        fixture.change_member(name, packaged)
        row = next(row for row in fixture.record["members"] if row["path"] == name)
        row["kind"] = "git_lf_to_crlf"; fixture.seal()
        self.blocked("transform_scope_unapproved")
        # Synthetic whitelist only within this fixture; production remains closed.
        identity = {key: fixture.record["component"][key] for key in
                    ("id", "name", "version", "source", "license", "crate_sha256")}
        identity.update({key: fixture.record[key] for key in ("repository", "commit", "package_path")})
        rule = {name: {"git_bytes": len(original), "git_sha256": digest(original),
                       "git_oid": object_id("blob", original),
                       "bytes": len(packaged), "sha256": digest(packaged)}}
        with mock.patch.object(supplement, "LF_CRLF_IDENTITY", identity), \
                mock.patch.object(supplement, "LF_CRLF_MEMBERS", rule):
            result = fixture.call()
            member = next(row for row in result.source_proof["members"] if row["path"] == name)
            self.assertEqual(member["transform"], "LF_TO_CRLF")
            self.assertFalse(member["git_byte_match"])
            self.assertEqual(fixture.members[name], packaged)
            fixture.change_member(name, packaged + b"changed")
            self.blocked("transform_identity_mismatch")

    def test_lf_to_crlf_rejects_mixed_bare_cr_nul_encoding_and_content_changes(self):
        original = b"one\ntwo\n"
        supplement._lf_to_crlf(original, b"one\r\ntwo\r\n")
        for source, packaged in (
            (original, b"one\r\ntwo\n"), (original, b"one\rtwo\r"),
            (original, b"one\r\nchanged\r\n"), (b"one\r\n", b"one\r\n"),
            (b"one\0\n", b"one\0\r\n"), (b"\xff\n", b"\xff\r\n"),
        ):
            with self.subTest(source=source, packaged=packaged), \
                    self.assertRaises((supplement.SupplementError, UnicodeError)):
                supplement._lf_to_crlf(source, packaged)

    def test_object_path_escape_and_alias_are_rejected(self):
        self.fixture.record["objects"][0]["path"] = "../outside"
        self.fixture.seal()
        self.blocked("path_invalid")
        self.fixture.rebuild()
        self.fixture.record["objects"][1]["path"] = self.fixture.record["objects"][0]["path"]
        self.fixture.seal()
        self.blocked("object_path_alias")

    def test_record_and_member_budgets_fail_closed(self):
        with mock.patch.object(supplement, "MAX_RECORD", 8):
            self.blocked("file_budget")
        with mock.patch.object(supplement, "MAX_MEMBERS", 1):
            self.blocked("member_budget")
        with mock.patch.object(supplement, "MAX_SOURCE", 8):
            self.blocked("source_budget")

    def test_empty_notice_and_unknown_kind_do_not_get_license_credit(self):
        self.fixture.files["LICENSE"] = b""
        self.fixture.rebuild()
        self.blocked("notice_text_invalid")
        self.fixture.record["notices"][0]["kind"] = "SPDX_FALLBACK"
        self.fixture.seal()
        self.blocked("notice_kind_unsupported")

    def test_mozilla_document_requires_original_header_and_separate_custody(self):
        fixture = self.fixture
        fixture.identity["license"] = "MPL-2.0"
        original = manifest().replace(b'license="MIT"', b'license="MPL-2.0"')
        fixture.files[fixture.package + "/Cargo.toml"] = original
        fixture.files[fixture.package + "/src/lib.rs"] = supplement.MPL_HEADER + b"pub fn fixture() {}\n"
        fixture.rebuild()
        fixture.change_member("Cargo.toml", original)
        official = b"Synthetic official-document fixture; no real license admission.\n"
        (fixture.root / "official").mkdir()
        (fixture.root / "official/MPL.txt").write_bytes(official)
        receipt = {"schema": "motionwright.official-license-acquisition/1",
                   "requested_url": supplement.MPL_TEXT_URL, "final_url": supplement.MPL_TEXT_URL,
                   "http_status": 200, "content_type": "text/plain",
                   "bytes": len(official), "sha256": digest(official)}
        raw = json.dumps(receipt).encode()
        (fixture.root / "official/receipt.json").write_bytes(raw)
        fixture.record["notices"] = [{"kind": "mozilla_mpl_2_0", "header_member": "src/lib.rs",
            "header_offset": 0,
            "text": {"path": "official/MPL.txt", "bytes": len(official), "sha256": digest(official)},
            "receipt": {"path": "official/receipt.json", "bytes": len(raw), "sha256": digest(raw)}}]
        fixture.seal()
        result = fixture.call()
        self.assertEqual([row["source_kind"] for row in result.notices], [
            "upstream_source_license_header", "official_license_text_referenced_by_source"])
        self.assertEqual(result.texts[digest(official)], official)
        fixture.record["notices"][0]["header_offset"] = 1
        fixture.seal()
        self.blocked("official_header_mismatch")


if __name__ == "__main__":
    unittest.main()
