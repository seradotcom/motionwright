"""Offline contracts for the signed Ubuntu MLT file inventory."""
import copy
import datetime as dt
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from tooling import mlt_official_runtime_intake as intake

NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
FPR = "A" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pin(data):
    return {"sha256": sha(data), "size": len(data)}


def release(index=b"Package: other\n\n", **fields):
    compressed = lzma.compress(index)
    values = {
        "Origin": "Ubuntu", "Label": "Ubuntu", "Suite": "noble", "Version": "24.04",
        "Codename": "noble", "Date": "Thu, 25 Apr 2024 15:10:33 UTC",
        "Architectures": "amd64 arm64", "Components": "main universe",
    }
    values.update(fields)
    rows = [key + ": " + value for key, value in values.items()]
    rows += ["SHA256:", " " + sha(index) + " " + str(len(index)) + " " + intake.INDEX,
             " " + sha(compressed) + " " + str(len(compressed)) + " " + intake.INDEX + ".xz"]
    return ("\n".join(rows) + "\n").encode(), compressed


def signature(**changes):
    values = {
        "fingerprint": FPR, "date": "2024-04-25", "timestamp": "1714057833",
        "expiry": "0", "version": "4", "reserved": "0", "pub": "1", "hash": "10",
        "class": "01", "primary": FPR,
    }
    values.update(changes)
    good = "[GNUPG:] GOODSIG " + values["fingerprint"][-16:] + " Ubuntu archive\n"
    return (good + "[GNUPG:] VALIDSIG " + " ".join(values.values()) + "\n").encode()


def package_row(name, **changes):
    fields = {"Package": name, "Source": "mlt", "Version": intake.VERSION,
              "Architecture": "amd64", "Filename": "pool/universe/m/mlt/" + name + "_" +
              intake.VERSION + "_amd64.deb", "Size": "16", "SHA256": "b" * 64}
    fields.update(changes)
    return "\n".join(key + ": " + value for key, value in fields.items()) + "\n\n"


def packages(**changes):
    return (package_row("libmlt7", **changes) + package_row("melt")).encode()


def elf():
    data = bytearray(96)
    data[:7] = b"\x7fELF\x02\x01\x01"
    data[16:18] = (3).to_bytes(2, "little")
    data[18:20] = (62).to_bytes(2, "little")
    data[52:54] = (64).to_bytes(2, "little")
    return bytes(data)


def tar_entries(entries):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, data, kind, link in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = link
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return result.getvalue()


def deb_archive(entries=None):
    if entries is None:
        entries = [("debian-binary", b"2.0\n"), ("control.tar.xz", b"control"),
                   ("data.tar.zst", b"data")]
    result = bytearray(b"!<arch>\n")
    for name, data in entries:
        header = ((name + "/").ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6) +
                  "100644".ljust(8) + str(len(data)).ljust(10)).encode() + b"\x60\n"
        result.extend(header + data + (b"\n" if len(data) % 2 else b""))
    return bytes(result)


def runtime_tar(extra=(), target_data=None, target_type=tarfile.REGTYPE):
    entries = [(".", b"", tarfile.DIRTYPE, ""),
               ("./usr", b"", tarfile.DIRTYPE, ""),
               ("./usr/bin", b"", tarfile.DIRTYPE, ""),
               ("./usr/bin/melt", elf() if target_data is None else target_data, target_type, "other")]
    return tar_entries(entries + list(extra))


class Response(io.BytesIO):
    def __init__(self, content, url, headers=None, status=200):
        super().__init__(content)
        self.url, self.headers, self.status = url, headers or {}, status

    def geturl(self):
        return self.url


class OfficialRuntimeIntakeTests(unittest.TestCase):
    def test_fixed_release_records_absent_valid_until_without_freshness_claim(self):
        data, _ = release()
        result = intake.parse_release(data, NOW)
        self.assertIsNone(result["valid_until"])
        self.assertEqual(result["valid_until_policy"], "fixed_release_pocket_no_freshness_claim")
        self.assertEqual(result["date"], "Thu, 25 Apr 2024 15:10:33 UTC")

    def test_supplied_valid_until_is_preserved(self):
        value = "Wed, 21 Oct 2026 00:00:00 UTC"
        data, _ = release(**{"Valid-Until": value})
        result = intake.parse_release(data, NOW)
        self.assertEqual(result["valid_until"], value)
        self.assertEqual(result["valid_until_policy"], "supplied_date_enforced")

    def test_expired_invalid_or_future_release_dates_fail(self):
        for change in ({"Valid-Until": "Mon, 19 Oct 2026 00:00:00 UTC"},
                       {"Valid-Until": "not-a-date"},
                       {"Date": "Wed, 21 Oct 2026 00:00:00 UTC"},
                       {"Date": "Thu, 25 Apr 2024 15:10:33"}):
            with self.subTest(change=change):
                data, _ = release(**change)
                with self.assertRaises(intake.IntakeError):
                    intake.parse_release(data, NOW)

    def test_release_identity_must_match_the_fixed_distribution(self):
        for key, value in (("Origin", "Elsewhere"), ("Suite", "noble-updates"),
                           ("Version", "24.10"), ("Codename", "jammy"),
                           ("Architectures", "arm64"), ("Components", "main")):
            with self.subTest(key=key):
                data, _ = release(**{key: value})
                with self.assertRaises(intake.IntakeError):
                    intake.parse_release(data, NOW)

    def test_duplicate_fields_are_case_insensitive_and_control_lines_are_rejected(self):
        data, _ = release()
        for extra in (b"origin: Ubuntu\n", b"Other: a\x0bHidden: b\n"):
            with self.subTest(extra=extra):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_release(extra + data, NOW)

    def test_duplicate_or_missing_signed_index_entries_fail(self):
        data, _ = release()
        duplicate = data + data.splitlines(keepends=True)[-1]
        missing = b"\n".join(line for line in data.splitlines() if not line.endswith(b".xz")) + b"\n"
        for value in (duplicate, missing):
            with self.subTest(value=value[-80:]):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_release(value, NOW)

    def test_valid_signature_keeps_primary_and_signing_fingerprints(self):
        result = intake.parse_signature_status(signature(), 0, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["fingerprint"], FPR)
        self.assertEqual(result[0]["primary_fingerprint"], FPR)
        self.assertEqual(result[0]["hash_algorithm"], 10)

    def test_gpg_failure_never_becomes_good_from_a_validsig(self):
        for tag in ("BADSIG", "ERRSIG", "NO_PUBKEY", "EXPKEYSIG", "REVKEYSIG", "FAILURE"):
            with self.subTest(tag=tag):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(signature() + ("[GNUPG:] " + tag + " x\n").encode(), 0, NOW)
        with self.assertRaises(intake.IntakeError):
            intake.parse_signature_status(signature(), 1, NOW)

    def test_missing_duplicate_or_unframed_signature_status_fails(self):
        for data in (b"[GNUPG:] GOODSIG AAAAAAAAAAAAAAAA user\n",
                     signature() + signature(), b"VALIDSIG " + FPR.encode() + b"\n",
                     signature().replace(FPR[-16:].encode(), b"B" * 16, 1)):
            with self.subTest(data=data[:30]):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(data, 0, NOW)

    def test_weak_expired_future_and_wrong_class_signatures_fail(self):
        for change in ({"hash": "2"}, {"expiry": "1"},
                       {"timestamp": str(int(NOW.timestamp()) + 100), "date": "2026-10-20"},
                       {"class": "00"}, {"date": "2024-04-24"}):
            with self.subTest(change=change):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(signature(**change), 0, NOW)

    def test_xz_roundtrip_requires_both_signed_digests(self):
        index = packages()
        data, compressed = release(index)
        binding = intake.parse_release(data, NOW)
        self.assertEqual(intake.expand_index(compressed, binding), index)

    def test_compressed_digest_and_size_mismatches_fail(self):
        data, compressed = release()
        binding = intake.parse_release(data, NOW)
        for key, value in (("sha256", "0" * 64), ("size", len(compressed) + 1)):
            invalid = copy.deepcopy(binding)
            invalid["compressed"][key] = value
            with self.subTest(key=key):
                with self.assertRaises(intake.IntakeError):
                    intake.expand_index(compressed, invalid)

    def test_truncated_concatenated_and_trailing_xz_fail_even_with_matching_outer_pin(self):
        data, compressed = release()
        binding = intake.parse_release(data, NOW)
        for candidate in (compressed[:-2], compressed + compressed, compressed + b"tail"):
            invalid = copy.deepcopy(binding)
            invalid["compressed"] = pin(candidate)
            with self.subTest(size=len(candidate)):
                with self.assertRaises(intake.IntakeError):
                    intake.expand_index(candidate, invalid)

    def test_uncompressed_limit_and_digest_mismatch_fail(self):
        data, compressed = release()
        binding = intake.parse_release(data, NOW)
        for key, value in (("size", intake.LIMIT_INDEX + 1), ("size", 1), ("sha256", "c" * 64)):
            invalid = copy.deepcopy(binding)
            invalid["uncompressed"][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(intake.IntakeError):
                    intake.expand_index(compressed, invalid)

    def test_packages_bind_only_the_two_exact_versioned_urls(self):
        result = intake.select_packages(packages())
        self.assertEqual(set(result), {"libmlt7", "melt"})
        for name, row in result.items():
            self.assertEqual(row["version"], "7.22.0-1build6")
            self.assertEqual(row["architecture"], "amd64")
            self.assertEqual(row["url"], intake.ARCHIVE + row["filename"])

    def test_wrong_package_version_architecture_source_or_url_fails(self):
        for change in ({"Version": "7.22.0-1build7"}, {"Architecture": "arm64"},
                       {"Source": "other"}, {"Filename": "../melt.deb"},
                       {"Filename": "https://elsewhere.invalid/melt.deb"}):
            with self.subTest(change=change):
                with self.assertRaises(intake.IntakeError):
                    intake.select_packages(packages(**change))

    def test_duplicate_package_and_digest_fields_fail(self):
        for data in (packages() + package_row("melt").encode(),
                     packages().replace(b"SHA256:", b"SHA256: " + b"b" * 64 + b"\nsha256:", 1)):
            with self.subTest(data=data[-80:]):
                with self.assertRaises(intake.IntakeError):
                    intake.select_packages(data)

    def test_missing_package_bad_digest_and_unbounded_size_fail(self):
        for data in (package_row("melt").encode(), packages(SHA256="bad"),
                     packages(Size="0"), packages(Size=str(intake.LIMIT_PACKAGE + 1))):
            with self.subTest(data=data[-80:]):
                with self.assertRaises(intake.IntakeError):
                    intake.select_packages(data)

    def test_debian_container_profile_is_ordered_and_complete(self):
        self.assertEqual(intake.package_container(deb_archive()),
                         ["debian-binary", "control.tar.xz", "data.tar.zst"])

    def test_debian_container_duplicate_extra_reordered_and_truncated_members_fail(self):
        entries = [("debian-binary", b"2.0\n"), ("control.tar.xz", b"control"),
                   ("data.tar.zst", b"data")]
        candidates = [deb_archive(entries + [entries[-1]]),
                      deb_archive(entries + [("unexpected", b"x")]),
                      deb_archive([entries[0], entries[2], entries[1]]),
                      deb_archive()[:-1], b"invalid"]
        for raw in candidates:
            with self.subTest(size=len(raw)):
                with self.assertRaises(intake.IntakeError):
                    intake.package_container(raw)

    def test_concatenated_and_trailing_tar_data_are_rejected(self):
        raw = runtime_tar()
        for candidate in (raw + raw, raw + b"extra", raw[:-1]):
            with self.subTest(size=len(candidate)):
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(candidate, "/usr/bin/melt")

    def test_only_the_unique_regular_amd64_member_is_returned(self):
        raw = runtime_tar(extra=[("./unrelated", b"ignored", tarfile.REGTYPE, "")])
        self.assertEqual(intake.extract_regular_member(raw, "/usr/bin/melt"), elf())

    def test_symbolic_and_hardlink_targets_are_never_followed(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(runtime_tar(target_type=kind), "/usr/bin/melt")

    def test_duplicate_canonical_archive_member_is_rejected(self):
        raw = runtime_tar(extra=[("usr/bin/melt", elf(), tarfile.REGTYPE, "")])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt")

    def test_traversal_absolute_and_control_archive_paths_are_rejected(self):
        for path in ("../outside", "/outside", "usr/../outside", "usr\\outside", "bad\nname"):
            with self.subTest(path=path):
                raw = runtime_tar(extra=[(path, b"x", tarfile.REGTYPE, "")])
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(raw, "/usr/bin/melt")

    def test_target_parent_links_are_rejected(self):
        entries = [("usr", b"", tarfile.DIRTYPE, ""),
                   ("usr/bin", b"", tarfile.SYMTYPE, "elsewhere"),
                   ("usr/bin/melt", elf(), tarfile.REGTYPE, "")]
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(tar_entries(entries), "/usr/bin/melt")

    def test_missing_wrong_architecture_and_non_elf_targets_fail(self):
        bad_arch = bytearray(elf())
        bad_arch[18:20] = (183).to_bytes(2, "little")
        candidates = [runtime_tar(target_data=b"text"), runtime_tar(target_data=bytes(bad_arch)),
                      tar_entries([("other", elf(), tarfile.REGTYPE, "")])]
        for raw in candidates:
            with self.subTest(size=len(raw)):
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(raw, "/usr/bin/melt")

    def test_tar_and_member_size_limits_are_enforced(self):
        raw = runtime_tar()
        with mock.patch.object(intake, "LIMIT_MEMBER", 64):
            with self.assertRaises(intake.IntakeError):
                intake.extract_regular_member(raw, "/usr/bin/melt")
        with mock.patch.object(intake, "LIMIT_TAR", len(raw) - 1):
            with self.assertRaises(intake.IntakeError):
                intake.extract_regular_member(raw, "/usr/bin/melt")

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence"
            path.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                intake.exclusive(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"original")

    def test_regular_read_detects_a_real_append_during_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input"
            path.write_bytes(b"original")
            real_read = os.read
            changed = False

            def read_then_change(fd, count):
                nonlocal changed
                value = real_read(fd, count)
                if not changed:
                    changed = True
                    with path.open("ab") as handle:
                        handle.write(b"mutation")
                return value

            with mock.patch.object(intake.os, "read", side_effect=read_then_change):
                with self.assertRaises(intake.IntakeError):
                    intake.read_regular(path, 100)
            self.assertEqual(path.read_bytes(), b"originalmutation")

    def test_redirect_or_nonallowlisted_download_never_falls_back(self):
        with self.assertRaises(intake.IntakeError):
            intake.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid")
        with mock.patch.object(intake.urllib.request, "build_opener") as opened:
            with self.assertRaises(intake.IntakeError):
                intake.download("pool/../untrusted.deb", Path("unused"), 100)
            opened.assert_not_called()

    def test_download_checks_http_identity_size_and_signed_content(self):
        cases = [
            (Response(b"abc", intake.ARCHIVE + intake.RELEASE, {"Content-Length": "3"}), 3, pin(b"abc"), True),
            (Response(b"abc", "https://elsewhere.invalid"), 10, None, False),
            (Response(b"abc", intake.ARCHIVE + intake.RELEASE, {"Content-Encoding": "gzip"}), 10, None, False),
            (Response(b"abc", intake.ARCHIVE + intake.RELEASE, {"Content-Length": "4"}), 10, None, False),
            (Response(b"abcdef", intake.ARCHIVE + intake.RELEASE), 3, None, False),
            (Response(b"abc", intake.ARCHIVE + intake.RELEASE), 10, pin(b"xyz"), False),
        ]
        for response, limit, expected, succeeds in cases:
            with self.subTest(succeeds=succeeds, limit=limit), tempfile.TemporaryDirectory() as temporary:
                opener = SimpleNamespace(open=lambda request, timeout: response)
                with mock.patch.object(intake.urllib.request, "build_opener", return_value=opener):
                    destination = Path(temporary) / "download"
                    if succeeds:
                        self.assertEqual(intake.download(intake.RELEASE, destination, limit, expected)["sha256"],
                                         sha(b"abc"))
                    else:
                        with self.assertRaises(intake.IntakeError):
                            intake.download(intake.RELEASE, destination, limit, expected)

    def test_runtime_bindings_are_closed_and_never_claim_native_execution(self):
        selected = intake.select_packages(packages())
        members = {
            name: {"installed_path": path, "sha256": sha(elf()), "package_sha256": selected[name]["sha256"]}
            for name, (_, path) in intake.PACKAGE_MEMBERS.items()
        }
        receipt = intake.build_receipt({}, {}, [], {"keyring": {}}, selected, members)
        self.assertEqual(receipt["schema"], "motionwright.mlt.official-runtime-intake/1")
        self.assertEqual(set(receipt["runtime_bindings"]), {"original_module", "melt"})
        for value in receipt["runtime_bindings"].values():
            self.assertEqual(set(value), {"installed_path", "sha256"})
        for key in ("observer_source", "repaired_only_compile", "package_installation",
                    "native_execution", "loaded_module_observation", "physical_AAC_master"):
            self.assertEqual(receipt[key], "NOT_RUN")
        members["melt"]["package_sha256"] = "e" * 64
        with self.assertRaises(intake.IntakeError):
            intake.build_receipt({}, {}, [], {"keyring": {}}, selected, members)

    def test_failed_collection_records_one_attempt_without_runtime_receipt_or_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "intake"
            source = "a" * 40
            env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "GITHUB_SHA": source,
                   "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
            helper_source = Path(intake.__file__).read_bytes()
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(intake, "read_regular", return_value=helper_source), \
                    mock.patch.object(intake, "collect", side_effect=intake.IntakeError("signature rejected")) as collect:
                with self.assertRaises(intake.IntakeError):
                    intake.main(["--output", str(root), "--source-sha", source, "--source-tree", "b" * 40])
            self.assertEqual(collect.call_count, 1)
            self.assertFalse((root / "official-runtime-receipt.json").exists())
            attempt = json.loads((root / "attempt.json").read_text())
            self.assertEqual(attempt["status"], "FAILED_SOURCE_INTAKE")
            self.assertEqual(attempt["native_execution"], "NOT_RUN")
            self.assertFalse(json.loads((root / "intent.json").read_text())["automatic_retry"])


if __name__ == "__main__":
    unittest.main()
