"""Offline contracts for the signed Ubuntu MLT file inventory."""
import copy
from contextlib import contextmanager
import datetime as dt
import errno
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from tooling import mlt_official_runtime_intake as intake

NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
FPR = "F6ECB3762474EDA9D21B7022871920D1991BC93C"


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
               ("./usr/bin/melt-7", elf() if target_data is None else target_data, target_type, "other"),
               ("./usr/bin/melt", b"", tarfile.SYMTYPE, "melt-7")]
    return tar_entries(entries + list(extra))


class Response(io.BytesIO):
    def __init__(self, content, url, headers=None, status=200):
        super().__init__(content)
        self.url, self.headers, self.status = url, headers or {}, status

    def geturl(self):
        return self.url


@contextmanager
def collection_fixture(root, keyring):
    real_capture = intake.capture_regular

    def capture(path, limit):
        path = Path(path)
        if path.as_posix() == "/usr/lib/os-release":
            data = b'ID=ubuntu\nVERSION_ID="24.04"\n'
            return data, {}
        if path.as_posix() in ("/usr/bin/gpgv", "/usr/bin/dpkg-deb"):
            return b"tool bytes are mocked; no native command runs", {}
        data, observation = real_capture(path, limit)
        if path == keyring:
            # Exercise the observed non-root/group-writable metadata case.
            # Neither the file nor any OS permissions are changed.
            for name in ("before", "after", "namespace_after"):
                observation[name]["uid"] = 1001
                observation[name]["gid"] = 1001
                observation[name]["mode"] = intake.stat.S_IFREG | 0o664
        return data, observation

    with mock.patch.object(intake, "KEYRING", keyring), \
            mock.patch.object(intake.platform, "system", return_value="Linux"), \
            mock.patch.object(intake.platform, "machine", return_value="x86_64"), \
            mock.patch.object(intake, "capture_regular", side_effect=capture):
        yield


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
                    intake.extract_regular_member(candidate, "/usr/bin/melt-7")

    def test_only_the_unique_regular_amd64_member_is_returned(self):
        raw = runtime_tar(extra=[("./unrelated", b"ignored", tarfile.REGTYPE, "")])
        self.assertEqual(intake.extract_regular_member(raw, "/usr/bin/melt-7"), elf())

    def test_symbolic_and_hardlink_targets_are_never_followed(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(runtime_tar(target_type=kind), "/usr/bin/melt-7")

    def test_duplicate_canonical_archive_member_is_rejected(self):
        raw = runtime_tar(extra=[("usr/bin/melt-7", elf(), tarfile.REGTYPE, "")])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_traversal_absolute_and_control_archive_paths_are_rejected(self):
        for path in ("../outside", "/outside", "usr/../outside", "usr\\outside", "bad\nname"):
            with self.subTest(path=path):
                raw = runtime_tar(extra=[(path, b"x", tarfile.REGTYPE, "")])
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_target_parent_links_are_rejected(self):
        entries = [("usr", b"", tarfile.DIRTYPE, ""),
                   ("usr/bin", b"", tarfile.SYMTYPE, "elsewhere"),
                   ("usr/bin/melt-7", elf(), tarfile.REGTYPE, "")]
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(tar_entries(entries), "/usr/bin/melt-7")

    def test_missing_wrong_architecture_and_non_elf_targets_fail(self):
        bad_arch = bytearray(elf())
        bad_arch[18:20] = (183).to_bytes(2, "little")
        candidates = [runtime_tar(target_data=b"text"), runtime_tar(target_data=bytes(bad_arch)),
                      tar_entries([("other", elf(), tarfile.REGTYPE, "")])]
        for raw in candidates:
            with self.subTest(size=len(raw)):
                with self.assertRaises(intake.IntakeError):
                    intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_tar_and_member_size_limits_are_enforced(self):
        raw = runtime_tar()
        with mock.patch.object(intake, "LIMIT_MEMBER", 64):
            with self.assertRaises(intake.IntakeError):
                intake.extract_regular_member(raw, "/usr/bin/melt-7")
        with mock.patch.object(intake, "LIMIT_TAR", len(raw) - 1):
            with self.assertRaises(intake.IntakeError):
                intake.extract_regular_member(raw, "/usr/bin/melt-7")

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
            name: {"installed_path": path, "sha256": sha(elf()),
                   "package_sha256": selected[name]["sha256"],
                   "archive_alias": intake.PACKAGE_ALIASES.get(path)}
            for name, (_, path) in intake.PACKAGE_MEMBERS.items()
        }
        signatures = intake.parse_signature_status(signature(), 0, NOW)
        keyring_pin = pin(b"retained keyring bytes")
        observation = {"path": str(intake.KEYRING), **keyring_pin}
        receipt = intake.build_receipt({}, {}, signatures, {"keyring": keyring_pin},
                                       selected, members, observation)
        self.assertEqual(receipt["trust_anchor"]["kind"], "source-pinned-ubuntu-archive-signing-key")
        self.assertEqual(receipt["trust_anchor"]["signing_fingerprint"], FPR)
        self.assertEqual(receipt["trust_anchor"]["primary_fingerprint"], FPR)
        self.assertEqual(receipt["trust_anchor"]["source_observation"], observation)
        self.assertEqual(receipt["schema"], "motionwright.mlt.official-runtime-intake/1")
        self.assertEqual(set(receipt["runtime_bindings"]), {"original_module", "melt"})
        for value in receipt["runtime_bindings"].values():
            self.assertEqual(set(value), {"installed_path", "sha256"})
        for key in ("observer_source", "repaired_only_compile", "package_installation",
                    "native_execution", "loaded_module_observation", "physical_AAC_master"):
            self.assertEqual(receipt[key], "NOT_RUN")
        members["melt"]["package_sha256"] = "e" * 64
        with self.assertRaises(intake.IntakeError):
            intake.build_receipt({}, {}, signatures, {"keyring": keyring_pin},
                                 selected, members, observation)

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

    def test_signing_and_primary_fingerprints_must_both_be_source_authorized(self):
        foreign = "B" * 40
        for changes in ({"fingerprint": foreign, "primary": foreign},
                        {"fingerprint": foreign}, {"primary": foreign}):
            with self.subTest(changes=changes):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(signature(**changes), 0, NOW)

    def test_matching_long_key_id_cannot_replace_the_full_authority_fingerprint(self):
        collision = "B" * 24 + FPR[-16:]
        self.assertNotEqual(collision, FPR)
        for changes in ({"fingerprint": collision, "primary": collision},
                        {"fingerprint": collision}, {"primary": collision}):
            with self.subTest(changes=changes):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(signature(**changes), 0, NOW)

    def test_extra_unauthorized_duplicate_or_weak_signatures_fail_closed(self):
        collision = "B" * 24 + FPR[-16:]
        for extra in (signature(fingerprint=collision, primary=collision),
                      signature(primary="B" * 40), signature(), signature(hash="2")):
            with self.subTest(extra=extra[-50:]):
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(signature() + extra, 0, NOW)

    def test_goodsig_accepts_only_exact_authorized_long_id_or_full_fingerprint(self):
        original = signature()
        full = original.replace(FPR[-16:].encode(), FPR.encode(), 1)
        self.assertEqual(intake.parse_signature_status(full, 0, NOW)[0]["fingerprint"], FPR)
        for identity in (FPR[-17:], FPR[:-1], "B" * 16):
            with self.subTest(identity=identity):
                data = original.replace(FPR[-16:].encode(), identity.encode(), 1)
                with self.assertRaises(intake.IntakeError):
                    intake.parse_signature_status(data, 0, NOW)

    def test_keyring_capture_retains_same_handle_metadata_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "keyring"
            path.write_bytes(b"original key material")
            data, observation = intake.capture_regular(path, 100)
            self.assertEqual(data, b"original key material")
            self.assertEqual(observation["path"], str(path))
            self.assertEqual(observation["sha256"], sha(data))
            self.assertEqual(observation["size"], len(data))
            self.assertEqual(observation["before"], observation["after"])
            self.assertEqual(observation["before"], observation["namespace_after"])
            actual = path.lstat()
            for key, attr in (("device", "st_dev"), ("inode", "st_ino"), ("mode", "st_mode"),
                              ("uid", "st_uid"), ("gid", "st_gid"), ("size", "st_size"),
                              ("mtime_ns", "st_mtime_ns"), ("ctime_ns", "st_ctime_ns")):
                self.assertEqual(observation["before"][key], getattr(actual, attr))

    def test_keyring_capture_does_not_convert_permissions_into_signing_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "keyring"
            path.write_bytes(b"untrusted candidate key material")
            actual = path.stat()
            attrs = {name: getattr(actual, name) for name in (
                "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            attrs.update(st_uid=1001, st_gid=1001, st_mode=intake.stat.S_IFREG | 0o664)
            observed = SimpleNamespace(**attrs)
            with mock.patch.object(intake.os, "fstat", return_value=observed), \
                    mock.patch.object(Path, "lstat", return_value=observed), \
                    mock.patch.object(intake.os, "chmod") as chmod:
                data, observation = intake.capture_regular(path, 100)
            self.assertEqual(data, b"untrusted candidate key material")
            self.assertEqual(observation["before"]["uid"], 1001)
            self.assertEqual(observation["before"]["mode"] & 0o777, 0o664)
            chmod.assert_not_called()
            with self.assertRaises(intake.IntakeError):
                intake.parse_signature_status(signature(fingerprint="B" * 40, primary="B" * 40), 0, NOW)

    def test_keyring_metadata_change_during_same_handle_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "keyring"
            path.write_bytes(b"key material")
            original = path.stat()
            attrs = {name: getattr(original, name) for name in (
                "st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_gid",
                "st_mtime_ns", "st_ctime_ns")}
            attrs["st_uid"] += 1
            with mock.patch.object(intake.os, "fstat", side_effect=[
                    original, SimpleNamespace(**attrs)]):
                with self.assertRaisesRegex(intake.IntakeError, "changed during read"):
                    intake.capture_regular(path, 100)

    def test_collection_preserves_original_keyring_observation_before_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root, keyring = parent / "intake", parent / "keyring"
            root.mkdir()
            keyring.write_bytes(b"candidate key material")
            with collection_fixture(root, keyring), \
                    mock.patch.object(intake.os, "chmod") as chmod, \
                    mock.patch.object(intake, "download", side_effect=intake.IntakeError("stop before network")) as download, \
                    mock.patch.object(intake, "run_tool") as run:
                with self.assertRaisesRegex(intake.IntakeError, "stop before network"):
                    intake.collect(root, {})
            download.assert_called_once()
            run.assert_not_called()
            chmod.assert_not_called()
            observation = json.loads((root / "raw/keyring-source-observation.json").read_text())
            self.assertEqual(observation["before"]["uid"], 1001)
            self.assertEqual(observation["before"]["mode"] & 0o777, 0o664)
            self.assertEqual(observation["before"], observation["after"])
            self.assertEqual(observation["before"], observation["namespace_after"])
            copied = (root / "raw/ubuntu-archive-keyring.gpg").read_bytes()
            self.assertEqual(copied, keyring.read_bytes())
            self.assertEqual(observation["sha256"], sha(copied))
            self.assertFalse((root / "official-runtime-receipt.json").exists())

    def test_mutated_keyring_snapshot_after_gpgv_is_rejected_before_release_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root, keyring = parent / "intake", parent / "keyring"
            root.mkdir()
            keyring.write_bytes(b"candidate key material")

            def download(relative, destination, limit, pin=None):
                intake.exclusive(destination, b"mocked signed input; no download")

            def run(command, output, error, work, *, status=None, stdin=None):
                self.assertEqual(command[0], "/usr/bin/gpgv")
                status.write(signature())
                status.flush()
                intake.exclusive(output, release()[0])
                intake.exclusive(error, b"")
                (root / "raw/ubuntu-archive-keyring.gpg").write_bytes(b"changed snapshot")
                return 0

            with collection_fixture(root, keyring), \
                    mock.patch.object(intake, "download", side_effect=download) as downloaded, \
                    mock.patch.object(intake, "run_tool", side_effect=run) as invoked, \
                    mock.patch.object(intake, "parse_release") as parsed:
                with self.assertRaisesRegex(intake.IntakeError, "authenticated digest"):
                    intake.collect(root, {})
            downloaded.assert_called_once()
            invoked.assert_called_once()
            parsed.assert_not_called()
            self.assertEqual(keyring.read_bytes(), b"candidate key material")
            self.assertFalse((root / "official-runtime-receipt.json").exists())

    def test_receipt_rejects_missing_authority_or_mismatched_keyring_snapshot(self):
        selected = intake.select_packages(packages())
        members = {
            name: {"installed_path": path, "sha256": sha(elf()),
                   "package_sha256": selected[name]["sha256"],
                   "archive_alias": intake.PACKAGE_ALIASES.get(path)}
            for name, (_, path) in intake.PACKAGE_MEMBERS.items()
        }
        signatures = intake.parse_signature_status(signature(), 0, NOW)
        original = pin(b"keyring")
        foreign = copy.deepcopy(signatures)
        foreign[0]["primary_fingerprint"] = "B" * 40
        for values, observation in (([], original), (foreign, original),
                                    (signatures, pin(b"replacement"))):
            with self.subTest(signatures=values, observation=observation):
                with self.assertRaises(intake.IntakeError):
                    intake.build_receipt({}, {}, values, {"keyring": original},
                                         selected, members, observation)



class AuthenticatedXzStreamTests(unittest.TestCase):
    @staticmethod
    def binding(compressed, expanded):
        return {"compressed": pin(compressed), "uncompressed": pin(expanded)}

    def test_signed_content_followed_by_empty_stream_is_admitted(self):
        expanded = packages()
        compressed = lzma.compress(expanded) + lzma.compress(b"")
        self.assertEqual(intake.expand_index(compressed, self.binding(compressed, expanded)), expanded)

    def test_multiple_streams_require_the_exact_signed_expanded_file(self):
        pieces = [b"Package: first\n\n", packages(), b"Package: last\n\n"]
        compressed = b"".join(lzma.compress(x) for x in pieces)
        expanded = b"".join(pieces)
        self.assertEqual(intake.expand_index(compressed, self.binding(compressed, expanded)), expanded)

    def test_aligned_zero_padding_between_and_after_streams_is_admitted(self):
        expanded = packages()
        compressed = lzma.compress(expanded) + b"\x00" * 8 + lzma.compress(b"") + b"\x00" * 4
        self.assertEqual(intake.expand_index(compressed, self.binding(compressed, expanded)), expanded)

    def test_misaligned_zero_padding_is_rejected_even_when_compressed_pin_matches(self):
        expanded = packages()
        first = lzma.compress(expanded)
        for size in (1, 2, 3, 5, 6, 7):
            for suffix in (b"", lzma.compress(b"")):
                compressed = first + b"\x00" * size + suffix
                with self.subTest(size=size, suffix=bool(suffix)):
                    with self.assertRaises(intake.IntakeError):
                        intake.expand_index(compressed, self.binding(compressed, expanded))

    def test_nonzero_trailing_data_is_not_treated_as_padding(self):
        expanded = packages()
        for suffix in (b"tail", b"\x00\x00\x00x", b"\x00" * 4 + b"tail"):
            compressed = lzma.compress(expanded) + suffix
            with self.subTest(suffix=suffix):
                with self.assertRaises(intake.IntakeError):
                    intake.expand_index(compressed, self.binding(compressed, expanded))

    def test_second_stream_cannot_exceed_the_total_authenticated_output_budget(self):
        expected = b"small"
        compressed = lzma.compress(expected) + lzma.compress(b"x" * 100_000)
        with self.assertRaisesRegex(intake.IntakeError, "authenticated size"):
            intake.expand_index(compressed, self.binding(compressed, expected))

    def test_empty_stream_chain_has_a_finite_stream_count(self):
        expanded = packages()
        compressed = lzma.compress(expanded) + lzma.compress(b"") * 32
        with self.assertRaisesRegex(intake.IntakeError, "too many XZ"):
            intake.expand_index(compressed, self.binding(compressed, expanded))

    def test_empty_suffix_cannot_be_appended_without_the_original_compressed_pin(self):
        expanded = packages()
        original = lzma.compress(expanded)
        with self.assertRaisesRegex(intake.IntakeError, "authenticated digest or size"):
            intake.expand_index(original + lzma.compress(b""), self.binding(original, expanded))

    def test_truncated_second_stream_cannot_hide_after_a_complete_first_stream(self):
        expanded = packages()
        compressed = lzma.compress(expanded) + lzma.compress(b"")[:-4]
        with self.assertRaises(intake.IntakeError):
            intake.expand_index(compressed, self.binding(compressed, expanded))

    def test_padding_only_and_empty_files_are_not_xz_indices(self):
        for compressed in (b"", b"\x00" * 4, b"\x00" * 32):
            with self.subTest(size=len(compressed)):
                with self.assertRaises(intake.IntakeError):
                    intake.expand_index(compressed, self.binding(compressed, b"x"))




class OfficialMeltAliasTests(unittest.TestCase):
    def test_official_melt_alias_binds_only_the_canonical_regular_elf(self):
        data = intake.extract_regular_member(runtime_tar(), "/usr/bin/melt-7")
        self.assertEqual(data, elf())
        self.assertEqual(intake.PACKAGE_MEMBERS["melt"][1], "/usr/bin/melt-7")

    def test_old_alias_is_not_an_allowlisted_regular_executable(self):
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(runtime_tar(), "/usr/bin/melt")

    def test_missing_official_alias_cannot_silently_change_the_package_profile(self):
        raw = tar_entries([("usr/bin/melt-7", elf(), tarfile.REGTYPE, "")])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_alias_with_an_alternate_relative_target_is_rejected(self):
        self.assert_bad_alias(tarfile.SYMTYPE, "another-melt")

    def test_alias_with_an_absolute_target_is_rejected(self):
        self.assert_bad_alias(tarfile.SYMTYPE, "/usr/bin/melt-7")

    def test_alias_with_traversal_or_normalized_target_is_rejected(self):
        for link in ("../bin/melt-7", "./melt-7", "../../outside", "other/../melt-7"):
            with self.subTest(link=link):
                self.assert_bad_alias(tarfile.SYMTYPE, link)

    def test_alias_cannot_be_a_hardlink(self):
        self.assert_bad_alias(tarfile.LNKTYPE, "melt-7")

    def test_alias_cannot_be_an_ordinary_file(self):
        self.assert_bad_alias(tarfile.REGTYPE, "")

    def test_duplicate_alias_under_equivalent_tar_path_is_rejected(self):
        raw = runtime_tar(extra=[("usr/bin/melt", b"", tarfile.SYMTYPE, "melt-7")])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_alias_never_recursively_resolves_a_second_link(self):
        raw = tar_entries([("usr/bin/melt", b"", tarfile.SYMTYPE, "melt-7"),
                           ("usr/bin/melt-7", b"", tarfile.SYMTYPE, "melt-final"),
                           ("usr/bin/melt-final", elf(), tarfile.REGTYPE, "")])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt-7")

    def test_module_member_remains_an_ordinary_file_without_an_executable_alias(self):
        path = intake.PACKAGE_MEMBERS["libmlt7"][1]
        raw = tar_entries([(path.removeprefix("/"), elf(), tarfile.REGTYPE, "")])
        self.assertEqual(intake.extract_regular_member(raw, path), elf())

    def test_receipt_cannot_omit_or_retarget_the_signed_alias(self):
        selected = intake.select_packages(packages())
        signatures = intake.parse_signature_status(signature(), 0, NOW)
        keyring_pin = pin(b"retained keyring bytes")
        observation = {"path": str(intake.KEYRING), **keyring_pin}
        for altered in (None, {}, {"archive_path": "usr/bin/melt",
                                   "linkname": "elsewhere",
                                   "target_path": "usr/bin/elsewhere"}):
            members = {
                name: {"installed_path": path, "sha256": sha(elf()),
                       "package_sha256": selected[name]["sha256"],
                       "archive_alias": intake.PACKAGE_ALIASES.get(path)}
                for name, (_, path) in intake.PACKAGE_MEMBERS.items()
            }
            members["melt"]["archive_alias"] = altered
            with self.subTest(alias=altered):
                with self.assertRaises(intake.IntakeError):
                    intake.build_receipt({}, {}, signatures, {"keyring": keyring_pin},
                                         selected, members, observation)

    def assert_bad_alias(self, kind, link):
        raw = tar_entries([("usr/bin/melt-7", elf(), tarfile.REGTYPE, ""),
                           ("usr/bin/melt", elf() if kind == tarfile.REGTYPE else b"",
                            kind, link)])
        with self.assertRaises(intake.IntakeError):
            intake.extract_regular_member(raw, "/usr/bin/melt-7")


class BinaryReadbackTests(unittest.TestCase):
    def test_regular_capture_preserves_crlf_ctrl_z_and_every_byte(self):
        raw = b"prefix\r\nline\x1aafter-eof\r\n" + bytes(range(256)) * 3 + b"\x00tail"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original.bin"
            path.write_bytes(raw)
            observed, receipt = intake.capture_regular(path, len(raw))
            self.assertEqual(observed, raw)
            self.assertEqual(receipt["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(receipt["size"], len(raw))
            self.assertEqual(receipt["before"], receipt["after"])
            self.assertEqual(receipt["after"], receipt["namespace_after"])

    def test_regular_capture_preserves_empty_original_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "empty-original.bin"
            path.write_bytes(b"")
            observed, receipt = intake.capture_regular(path, 0)
            self.assertEqual(observed, b"")
            self.assertEqual(receipt["path"], str(path))
            self.assertEqual(receipt["size"], 0)
            self.assertEqual(receipt["sha256"], hashlib.sha256(b"").hexdigest())
            self.assertEqual(receipt["before"], receipt["after"])
            self.assertEqual(receipt["before"], receipt["namespace_after"])
            actual = path.lstat()
            self.assertTrue(intake.stat.S_ISREG(actual.st_mode))
            for key, attr in (("device", "st_dev"), ("inode", "st_ino"),
                              ("size", "st_size"), ("mode", "st_mode"),
                              ("uid", "st_uid"), ("gid", "st_gid"),
                              ("mtime_ns", "st_mtime_ns"), ("ctime_ns", "st_ctime_ns")):
                self.assertEqual(receipt["before"][key], getattr(actual, attr))

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "mkfifo"),
                         "Linux FIFO open regression with an external child deadline")
    def test_regular_capture_replaced_fifo_is_rejected_without_writer_or_blocking_open(self):
        # The child calls the actual helper once. Its parent owns the deadline,
        # so a regression in os.open cannot strand a reader thread in this suite.
        child_source = r'''
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from unittest import mock

spec = importlib.util.spec_from_file_location("capture_regular_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
path = Path(sys.argv[2])
assert stat.S_ISREG(path.lstat().st_mode)
real_is_symlink = Path.is_symlink
real_close = os.close
leaf_checks = 0

def check_then_replace(candidate):
    global leaf_checks
    if candidate != path:
        return real_is_symlink(candidate)
    leaf_checks += 1
    assert leaf_checks == 1
    assert stat.S_ISREG(candidate.lstat().st_mode)
    was_symlink = real_is_symlink(candidate)
    assert not was_symlink
    candidate.unlink()
    os.mkfifo(candidate, 0o600)
    assert stat.S_ISFIFO(candidate.lstat().st_mode)
    return was_symlink

with mock.patch.object(Path, "is_symlink", check_then_replace), \
        mock.patch.object(module.os, "read", side_effect=AssertionError("nonregular read")) as reader, \
        mock.patch.object(module.os, "close", wraps=real_close) as closer:
    try:
        module.capture_regular(path, 64)
    except module.IntakeError as error:
        assert str(error) == "input is not a bounded regular file"
    else:
        raise AssertionError("FIFO was admitted as an original file")
    reader.assert_not_called()
    assert closer.call_count == 1
    closed_fd = closer.call_args.args[0]

try:
    os.fstat(closed_fd)
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError("rejected FIFO descriptor was not closed")
assert leaf_checks == 1
assert stat.S_ISFIFO(path.lstat().st_mode)
print(json.dumps({"pid": os.getpid(), "leaf_checks": leaf_checks,
                  "capture_calls": leaf_checks, "rejection": "bounded_regular",
                  "read_calls": reader.call_count, "close_calls": closer.call_count},
                 sort_keys=True), flush=True)
'''
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "replaced-original.bin"
            path.write_bytes(b"observed ordinary original")
            child = subprocess.Popen(
                [sys.executable, "-I", "-c", child_source,
                 str(Path(intake.__file__).resolve(strict=True)), str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = child.communicate(timeout=5)
            except BaseException as primary:
                # Keep the supervision failure primary. Kill and reap this same
                # inert child; do not start another capture or FIFO writer.
                try:
                    child.kill()
                except BaseException as secondary:
                    if hasattr(primary, "add_note"):
                        primary.add_note("FIFO test child kill failed: " + repr(secondary))
                try:
                    child.communicate(timeout=5)
                except BaseException as secondary:
                    if hasattr(primary, "add_note"):
                        primary.add_note("FIFO test child reap unconfirmed: " + repr(secondary))
                raise
            self.assertEqual(child.returncode, 0, (child.pid, stdout, stderr))
            self.assertEqual(stderr, b"")
            self.assertEqual(json.loads(stdout), {
                "pid": child.pid, "leaf_checks": 1, "capture_calls": 1,
                "rejection": "bounded_regular", "read_calls": 0, "close_calls": 1,
            })
            self.assertTrue(intake.stat.S_ISFIFO(path.lstat().st_mode))

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "O_NOFOLLOW"),
                         "Linux O_NOFOLLOW rejects a substituted leaf symlink")
    def test_regular_capture_replaced_symlink_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "replaced-original.bin"
            target = root / "other-original.bin"
            original = b"other original must not be followed"
            path.write_bytes(b"observed ordinary original")
            target.write_bytes(original)
            real_is_symlink = Path.is_symlink
            leaf_checks = 0

            def check_then_replace(candidate):
                nonlocal leaf_checks
                if candidate != path:
                    return real_is_symlink(candidate)
                leaf_checks += 1
                self.assertEqual(leaf_checks, 1)
                self.assertTrue(intake.stat.S_ISREG(candidate.lstat().st_mode))
                was_symlink = real_is_symlink(candidate)
                self.assertFalse(was_symlink)
                candidate.unlink()
                candidate.symlink_to(target)
                return was_symlink

            with mock.patch.object(Path, "is_symlink", check_then_replace), \
                    mock.patch.object(intake.os, "read",
                                      side_effect=AssertionError("symlink target read")) as reader:
                with self.assertRaises(OSError) as caught:
                    intake.capture_regular(path, 64)
                reader.assert_not_called()
            self.assertEqual(caught.exception.errno, errno.ELOOP)
            self.assertEqual(leaf_checks, 1)
            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_bytes(), original)


    def test_regular_capture_read_primary_survives_close_failure(self):
        for primary in (OSError(errno.EIO, "original read failed"),
                        KeyboardInterrupt("original read interrupted"),
                        SystemExit("original read stopped")):
            with self.subTest(primary=type(primary).__name__), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / "original.bin"
                path.write_bytes(b"original bytes")
                real_close = os.close
                secondary = OSError(errno.EIO, "secondary close failed")

                def close_then_fail(fd):
                    real_close(fd)
                    raise secondary

                caught = None
                with mock.patch.object(intake.os, "read", side_effect=primary) as reader, \
                        mock.patch.object(intake.os, "close", side_effect=close_then_fail) as closer:
                    try:
                        intake.capture_regular(path, 64)
                    except BaseException as error:
                        caught = error
                    self.assertIs(caught, primary)
                    self.assertEqual(reader.call_count, 1)
                    self.assertEqual(closer.call_count, 1)
                    fd = closer.call_args.args[0]
                self.assertEqual(primary.__notes__, ["capture_regular close failed: " + repr(secondary)])
                with self.assertRaises(OSError) as closed:
                    os.fstat(fd)
                self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_regular_capture_fstat_primary_survives_close_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original.bin"
            path.write_bytes(b"original bytes")
            primary = OSError(errno.EIO, "original metadata failed")
            secondary = OSError(errno.EIO, "secondary close failed")
            real_close = os.close

            def close_then_fail(fd):
                real_close(fd)
                raise secondary

            with mock.patch.object(intake.os, "fstat", side_effect=primary) as metadata, \
                    mock.patch.object(intake.os, "read", side_effect=AssertionError("read after metadata error")) as reader, \
                    mock.patch.object(intake.os, "close", side_effect=close_then_fail) as closer:
                with self.assertRaises(OSError) as caught:
                    intake.capture_regular(path, 64)
                self.assertIs(caught.exception, primary)
                self.assertEqual(metadata.call_count, 1)
                reader.assert_not_called()
                self.assertEqual(closer.call_count, 1)
                fd = closer.call_args.args[0]
            self.assertEqual(primary.__notes__, ["capture_regular close failed: " + repr(secondary)])
            with self.assertRaises(OSError) as closed:
                os.fstat(fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_regular_capture_close_failure_after_valid_read_returns_no_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original.bin"
            raw = b"valid original\r\n\x1a\x00tail"
            path.write_bytes(raw)
            secondary = OSError(errno.EIO, "close failed after read")
            real_close = os.close

            def close_then_fail(fd):
                real_close(fd)
                raise secondary

            with mock.patch.object(intake.os, "close", side_effect=close_then_fail) as closer:
                with self.assertRaises(OSError) as caught:
                    intake.capture_regular(path, len(raw))
                self.assertIs(caught.exception, secondary)
                self.assertEqual(closer.call_count, 1)
                fd = closer.call_args.args[0]
            self.assertEqual(path.read_bytes(), raw)
            with self.assertRaises(OSError) as closed:
                os.fstat(fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_regular_capture_close_diagnostic_failure_keeps_original_error(self):
        class Primary(OSError):
            def add_note(self, _note):
                raise RuntimeError("secondary note persistence failed")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "original.bin"
            path.write_bytes(b"original bytes")
            primary = Primary(errno.EIO, "original read failed")
            secondary = OSError(errno.EIO, "secondary close failed")
            real_close = os.close

            def close_then_fail(fd):
                real_close(fd)
                raise secondary

            with mock.patch.object(intake.os, "read", side_effect=primary) as reader, \
                    mock.patch.object(intake.os, "close", side_effect=close_then_fail) as closer:
                with self.assertRaises(Primary) as caught:
                    intake.capture_regular(path, 64)
                self.assertIs(caught.exception, primary)
                self.assertEqual(reader.call_count, 1)
                self.assertEqual(closer.call_count, 1)
                fd = closer.call_args.args[0]
            with self.assertRaises(OSError) as closed:
                os.fstat(fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)


if __name__ == "__main__":
    unittest.main()
