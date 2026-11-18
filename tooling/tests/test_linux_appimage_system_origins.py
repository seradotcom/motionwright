"""Synthetic negative and byte-identity tests for isolated Linux AppImage origin reader.

No dpkg queries, application execution, installation or Native effects.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_appimage_system_origins as origin


def member(name, data, kind="ELF"):
    return {"path": name, "kind": "file", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "native_format": kind}


def elf_fixture(build_id=b"\x13" * 20):
    """Single inert ELF64 PT_NOTE fixture; no host native execution."""
    import struct
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", header, 16, 3, 62, 1)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HHH", header, 52, 64, 56, 1)
    offset = 64 + 56
    note = struct.pack("<III", 4, len(build_id), 3) + b"GNU\x00" + build_id
    return bytes(header) + struct.pack(
        "<IIQQQQQQ", 4, 4, offset, 0, 0, len(note), len(note), 4) + note


class AppImageInstalledOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-origin-inert-", dir=os.environ.get("TEMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(strict=True)
        self.original = b"\x7fELF source bytes -- inert fixture\n"
        self.current = member("usr/lib/libexample.so.1", self.original)
        self.pkg = "libexample:amd64"
        self.installed = {self.pkg: "1.2.3-1"}
        self.system_path = "/usr/lib/x86_64-linux-gnu/libexample.so.1"
        self.manifests = {self.pkg: (
            "/.\n/usr\n/usr/lib\n/usr/lib/x86_64-linux-gnu\n"
            + self.system_path + "\n/usr/share/doc/libexample/copyright\n").encode()}
        self.make(self.system_path, self.original)

    def make(self, path, data):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def match(self, items=None, *, installed=None, manifests=None):
        return origin.join_origins(
            [self.current] if items is None else items,
            self.installed if installed is None else installed,
            self.manifests if manifests is None else manifests,
            self.root,
        )

    def test_exact_installed_package_file_is_scoped_match(self):
        matched, missing = self.match()
        self.assertEqual(missing, [])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["origin"]["system_path"], self.system_path)
        self.assertEqual(matched[0]["origin"]["package"], self.pkg)
        self.assertEqual(matched[0]["status"], "INSTALLED_HOST_BYTES_EXACT")

    def test_same_basename_elsewhere_does_not_claim_origin(self):
        self.make("/usr/local/lib/libexample.so.1", self.original)
        matched, missing = self.match(manifests={self.pkg: b"/usr/local/lib/libexample.so.1\n"})
        self.assertEqual(matched, [])
        self.assertEqual(missing[0]["reason"], "NO_EXACT_REGULAR_INSTALLED_SOURCE")

    def test_changed_system_file_is_not_a_match(self):
        self.make(self.system_path, self.original + b"altered")
        matched, missing = self.match()
        self.assertEqual(matched, [])
        self.assertEqual(len(missing), 1)

    def test_same_size_wrong_bytes_are_not_a_match(self):
        changed = b"Z" + self.original[1:]
        self.make(self.system_path, changed)
        matched, missing = self.match()
        self.assertEqual(matched, [])
        self.assertEqual(len(missing), 1)

    def test_missing_real_file_is_unresolved(self):
        (self.root / self.system_path.lstrip("/")).unlink()
        matched, missing = self.match()
        self.assertEqual(matched, [])
        self.assertEqual(len(missing), 1)

    def test_shared_source_file_claims_are_ambiguous(self):
        second = "other-package:amd64"
        matched, unresolved = self.match(
            installed={**self.installed, second: "3.0.0"},
            manifests={**self.manifests, second: (self.system_path + "\n").encode()},
        )
        self.assertEqual(matched, [])
        self.assertEqual(unresolved[0]["reason"], "MULTIPLE_EXACT_CLAIMS")

    def test_uninstalled_manifest_cannot_be_used(self):
        with self.assertRaisesRegex(ValueError, "uninstalled_package_manifest"):
            self.match(installed={})

    def test_symlinked_system_source_is_not_treated_as_regular(self):
        direct = self.root / self.system_path.lstrip("/")
        renamed = direct.with_name("libexample.so.1.real")
        direct.rename(renamed)
        try:
            direct.symlink_to(renamed)
        except OSError as error:
            self.skipTest("symlink permission unavailable: " + str(error))
        matched, missing = self.match()
        self.assertEqual(matched, [])
        self.assertEqual(len(missing), 1)

    def test_duplicate_manifest_line_is_refused(self):
        with self.assertRaisesRegex(ValueError, "manifest_duplicate"):
            origin.list_paths((self.system_path + "\n" + self.system_path + "\n").encode())

    def test_manifest_escape_is_refused(self):
        for payload in (b"/usr/lib/../lib/libexample.so\n", b"relative/path\n",
                        b"//etc/passwd\n", b"/usr/./lib\n"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "manifest_path"):
                    origin.list_paths(payload)

    def test_dpkg_root_directory_placeholder_is_supported(self):
        self.assertEqual(origin.list_paths(b"/.\n/usr\n/usr/lib\n"), {"/usr", "/usr/lib"})

    def test_oversize_package_list_is_explicitly_unobserved_without_read(self):
        from unittest import mock
        info = self.root / "dpkg-info-large"
        info.mkdir()
        small = b"/usr/lib/libexample.so\n"
        (info / "small.list").write_bytes(small)
        oversized = info / "large.list"
        oversized.write_bytes(b"x" * 128)
        with mock.patch.object(origin, "MAX_MANIFEST", 64):
            with mock.patch.object(origin.inventory.capture, "stable_read",
                                   wraps=origin.inventory.capture.stable_read) as reader:
                observed, skipped = origin.captured_manifests(
                    info, {"small": "1.0", "large": "2.0"})
        self.assertEqual(observed, {"small": small})
        self.assertEqual(skipped, [{
            "package": "large", "observed_stat_bytes": 128,
            "reason": "PER_MANIFEST_BUDGET", "provenance": "NOT_OBSERVED",
        }])
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(reader.call_args.args[1].name, "small.list")

    def test_total_manifest_budget_does_not_admit_unread_package(self):
        from unittest import mock
        info = self.root / "dpkg-info-total"
        info.mkdir()
        raw_a = b"/usr/share/doc/a/copyright\n"
        raw_b = b"/usr/share/doc/b/copyright\n"
        (info / "a.list").write_bytes(raw_a)
        (info / "b.list").write_bytes(raw_b)
        with mock.patch.object(origin, "MAX_MANIFEST_TOTAL", len(raw_a)):
            observed, skipped = origin.captured_manifests(
                info, {"a": "1.0", "b": "2.0"})
        self.assertEqual(observed, {"a": raw_a})
        self.assertEqual(skipped, [{
            "package": "b", "observed_stat_bytes": len(raw_b),
            "reason": "TOTAL_MANIFEST_BUDGET", "provenance": "NOT_OBSERVED",
        }])
        self.assertEqual(origin.candidate_index(observed, {"a": "1.0", "b": "2.0"},
                         {"/usr/share/doc/b/copyright"}), {})

    def test_malformed_status_and_duplicate_packages_refused(self):
        for raw in (b"libexample:amd64\t1.0\n",
                    b"libexample:amd64\t1.0\tinstall ok installed\n"
                    b"libexample:amd64\t1.0\tinstall ok installed\n",
                    b"../evil\t1.0\tinstall ok installed\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "dpkg_"):
                    origin.installed_versions(raw)

    def test_only_installed_packages_admitted(self):
        raw = (b"libexample:amd64\t1.2\tinstall ok installed\n"
               b"unused\t1.0\tdeinstall ok config-files\n")
        self.assertEqual(origin.installed_versions(raw), {"libexample:amd64": "1.2"})

    def test_non_usr_generated_bootstrap_stays_unresolved(self):
        generated = member("AppRun", b"#!/bin/sh\n", kind="NOT_IDENTIFIED")
        matched, unresolved = self.match(items=[generated])
        self.assertEqual(matched, [])
        self.assertEqual(len(unresolved), 1)

    def test_explicit_byte_budget_does_not_promote_missing_proof(self):
        matched, unresolved = self.match()
        self.assertEqual(len(matched), 1)
        constrained, missing = origin.join_origins(
            [self.current], self.installed, self.manifests, self.root, budget=1)
        self.assertEqual(constrained, [])
        self.assertEqual(len(missing), 1)

    def test_path_rebasing_is_bounded_to_known_multiarch_directories(self):
        selected = origin.host_candidates("usr/lib/libexample.so.1")
        self.assertEqual(set(selected), {
            "/usr/lib/libexample.so.1",
            "/usr/lib/x86_64-linux-gnu/libexample.so.1",
            "/lib/x86_64-linux-gnu/libexample.so.1",
        })
        self.assertEqual(origin.host_candidates("AppRun.wrapped"), ())
        self.assertEqual(origin.host_candidates("usr/share/doc/example/copyright"),
                         ("/usr/share/doc/example/copyright",))


    def test_original_libproxy_manifest_subdirectory_has_exact_member_path(self):
        target_path = "/usr/lib/x86_64-linux-gnu/libproxy/libpxbackend-1.0.so"
        self.assertIn(target_path,
                      origin.host_candidates("usr/lib/libpxbackend-1.0.so"))
        self.assertNotIn(target_path,
                         origin.host_candidates("usr/lib/libpxbackend-2.0.so"))
        self.assertNotIn(target_path,
                         origin.host_candidates("usr/lib/some-other.so"))
        source = self.make(target_path, self.original)
        package = "libproxy1v5:amd64"
        installed = {package: "0.5.4-4build1"}
        manifests = {package: (target_path + "\n").encode()}
        row = member("usr/lib/libpxbackend-1.0.so", self.original)
        matches, pending = origin.join_origins(
            [row], installed, manifests, self.root)
        self.assertEqual(pending, [])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["origin"]["system_path"], target_path)
        self.assertEqual(matches[0]["origin"]["package"], package)

    def test_original_libproxy_subdirectory_supports_scoped_elf_build_id(self):
        image_bytes = elf_fixture() + b"AppImage transformed bytes"
        host_bytes = elf_fixture()
        root = self.root / "system"
        payload = self.root / "payload"
        host_path = "/usr/lib/x86_64-linux-gnu/libproxy/libpxbackend-1.0.so"
        host = root / host_path.lstrip("/")
        image = payload / "usr/lib/libpxbackend-1.0.so"
        host.parent.mkdir(parents=True, exist_ok=True)
        image.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(host_bytes)
        image.write_bytes(image_bytes)
        pkg = "libproxy1v5:amd64"
        row = member("usr/lib/libpxbackend-1.0.so", image_bytes)
        result = origin.observe_elf_build_id_relations(
            [row], {"roots": [{"members": [row]}]},
            {pkg: "0.5.4-4build1"}, {pkg: (host_path + "\n").encode()},
            payload, system_root=root)
        self.assertEqual((result["same_gnu_build_id"], result["no_match"]),
                         (1, 0))
        self.assertEqual(result["matches"][0]["origin"]["package"], pkg)
        self.assertEqual(result["matches"][0]["status"],
                         "SAME_GNU_BUILD_ID_NOT_PROOF_OF_SOURCE_BYTES")
        self.assertEqual(result["claims"]["G3"], "STOP")

    def test_listed_copyright_original_bytes_retained_without_legal_approval(self):
        license_path = "/usr/share/doc/libexample/copyright"
        copyright_raw = b"Example package license notice retained, not reviewed\n"
        self.make(license_path, copyright_raw)
        matched, missing = self.match()
        self.assertEqual(missing, [])
        details, original_bytes = origin.original_package_records(
            matched, self.manifests, self.root)
        self.assertEqual(set(details), {self.pkg})
        self.assertEqual(details[self.pkg]["copyright"]["status"],
                         "LISTED_REGULAR_COPYRIGHT_BYTES_OBSERVED")
        self.assertEqual(details[self.pkg]["copyright"]["license_review"],
                         "NOT_PERFORMED")
        self.assertIn(copyright_raw, original_bytes.values())
        self.assertIn(self.manifests[self.pkg], original_bytes.values())

    def test_unlisted_copyright_never_counts_as_attested_notice(self):
        license_path = "/usr/share/doc/libexample/copyright"
        self.make(license_path, b"Not declared as an installed package file\n")
        reduced = {self.pkg: (self.system_path + "\n").encode()}
        matched, missing = self.match(manifests=reduced)
        self.assertEqual(missing, [])
        details, originals = origin.original_package_records(
            matched, reduced, self.root)
        self.assertEqual(details[self.pkg]["copyright"]["status"], "NOT_OBSERVED")
        self.assertFalse(details[self.pkg]["copyright"]["listed_in_package_manifest"])
        self.assertEqual(len(originals), 1)


    def test_listed_copyright_symlink_inside_doc_tree_keeps_original_bytes(self):
        alias = self.root / "usr/share/doc/libexample/copyright"
        alias.parent.mkdir(parents=True, exist_ok=True)
        content = b"Original shared copyright notice (not a legal review)\n"
        target = self.make("/usr/share/doc/other-package/copyright", content)
        try:
            alias.symlink_to(target)
        except OSError as exc:
            self.skipTest("symlink permission unavailable: " + str(exc))
        matched, unresolved = self.match()
        self.assertEqual(unresolved, [])
        records, originals = origin.original_package_records(
            matched, self.manifests, self.root)
        self.assertEqual(records[self.pkg]["copyright"]["status"],
                         "LISTED_REGULAR_COPYRIGHT_BYTES_OBSERVED")
        self.assertIn(content, originals.values())
        self.assertEqual(records[self.pkg]["copyright"]["license_review"],
                         "NOT_PERFORMED")

    def test_listed_copyright_symlink_outside_doc_tree_never_counts_as_notice(self):
        alias = self.root / "usr/share/doc/libexample/copyright"
        alias.parent.mkdir(parents=True, exist_ok=True)
        outside = self.make("/usr/lib/other-copyright", b"Not a permitted notice\n")
        try:
            alias.symlink_to(outside)
        except OSError as exc:
            self.skipTest("symlink permission unavailable: " + str(exc))
        matched, unresolved = self.match()
        self.assertEqual(unresolved, [])
        records, originals = origin.original_package_records(
            matched, self.manifests, self.root)
        self.assertEqual(records[self.pkg]["copyright"]["status"], "NOT_OBSERVED")
        self.assertEqual(len(originals), 1)

    def test_notices_outside_documentation_root_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "origin_notice_path"):
            origin.regular_host_bytes(self.root, "/usr/lib/private/copyright",
                                      origin.MAX_COPYRIGHT)

    def test_unknown_roster_is_not_assumed_equal_to_appimage_exclusive_files(self):
        shared = member("usr/share/applications/Motionwright.desktop", b"desktop", "NOT_IDENTIFIED")
        origin_known = member("Motionwright.png", b"tracked png", "NOT_IDENTIFIED")
        base = {
            "platform": "linux", "arch": "x86_64", "source": "1" * 40,
            "source_tree": "2" * 40, "run_id": "42", "run_attempt": 1,
            "candidate_receipt": {"bytes": 2, "sha256": "3" * 64},
            "cargo_index": {"bytes": 2, "sha256": "4" * 64},
            "artifact_identities_sha256": "5" * 64,
            "generated_metadata_sha256": "6" * 64,
        }
        claims = {"formal_g2": "NOT_GRANTED", "formal_g3": "NOT_GRANTED",
                  "redistribution_permission": "NOT_INFERRED"}
        app = {
            "context": {**base, "kind": "appimage"},
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "claims": claims,
            "roots": [{"role": "payload", "members":
                       [self.current, shared, origin_known]}],
            "gaps": [{"code": "MEMBER_ORIGIN_UNRESOLVED",
                      "members": ["payload:" + self.current["path"],
                                  "payload:" + shared["path"]]}],
        }
        deb = {
            "context": {**base, "kind": "deb"},
            "custody": {"status": "DIRECT_CAPTURED_EXTRACTION"},
            "claims": claims,
            "roots": [{"role": "payload", "members": [shared]}],
        }
        pending, scope = origin.unresolved_members(app, deb, base["source"])
        self.assertEqual({r["path"] for r in pending},
                         {self.current["path"], shared["path"]})
        self.assertEqual(scope["appimage_exclusive_regular"], 2)
        self.assertEqual(scope["original_unresolved_regular"], 2)
        self.assertEqual(scope["exclusive_already_related"], 1)
        self.assertEqual(scope["shared_with_deb_unresolved"], 1)


    def _observed_elf_fixture(self, host_bytes, image_bytes, *, changed_image=False,
                              budget=origin.MAX_SOURCE_READ_TOTAL):
        fixture = elf_fixture
        host = self.root / "host"
        payload = self.root / "payload"
        target = host / self.system_path.lstrip("/")
        image = payload / "usr/lib/libexample.so.1"
        for path in (target, image):
            path.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(host_bytes)
        image.write_bytes(image_bytes)
        item = member("usr/lib/libexample.so.1", image_bytes)
        if changed_image:
            image.write_bytes(image_bytes + b"x")
        app = {"roots": [{"members": [item]}]}
        result = origin.observe_elf_build_id_relations(
            [item], app, self.installed, self.manifests,
            payload, system_root=host, budget=budget)
        return result, fixture

    def test_original_elf_gnu_id_matches_stable_declared_package_without_byte_equality(self):
        fixture = elf_fixture
        a, b = fixture(), fixture() + b"only the AppImage payload differs"
        self.assertNotEqual(hashlib.sha256(a).hexdigest(),
                            hashlib.sha256(b).hexdigest())
        result, _ = self._observed_elf_fixture(a, b)
        self.assertEqual((result["scanned_elf"], result["same_gnu_build_id"],
                          result["no_match"]), (1, 1, 0))
        self.assertEqual(result["matches"][0]["status"],
                         "SAME_GNU_BUILD_ID_NOT_PROOF_OF_SOURCE_BYTES")
        self.assertEqual(result["matches"][0]["origin"]["package"], self.pkg)
        self.assertEqual(result["matches"][0]["gnu_build_id"], "13" * 20)
        self.assertEqual(result["claims"]["G3"], "STOP")
        self.assertFalse(result["claims"]["build_id_proves_byte_equality"])

    def test_different_elf_gnu_id_refuses_relation(self):
        fixture = elf_fixture
        result, _ = self._observed_elf_fixture(
            fixture(build_id=b"\x22" * 20), fixture())
        self.assertEqual(result["same_gnu_build_id"], 0)
        self.assertEqual(result["unresolved"][0]["reason"],
                         "NO_DECLARED_HOST_GNU_BUILD_ID_MATCH")

    def test_changed_original_extracted_elf_refuses_relation(self):
        fixture = elf_fixture
        with self.assertRaisesRegex(ValueError, "origin_live_elf_bytes_changed"):
            self._observed_elf_fixture(fixture(), fixture(), changed_image=True)

    def test_bounded_elf_byte_budget_is_not_false_match(self):
        fixture = elf_fixture
        result, _ = self._observed_elf_fixture(
            fixture(), fixture(), budget=64)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["unresolved"][0]["reason"], "ORIGINAL_ELF_BUDGET")


if __name__ == "__main__":
    unittest.main()
