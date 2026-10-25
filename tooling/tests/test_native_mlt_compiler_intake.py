"""Offline compiler-source transport contracts; no download, build or native effect."""
from contextlib import ExitStack
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_custody as custody

CURRENT = {"source_sha": "a" * 40, "semwright_sha": custody.g2.SEMWRIGHT_PIN,
           "run_id": "123", "run_attempt": "1"}
ORIGINAL = b"C" * 96690
REPAIRED = b"R" * 99266
GENERATOR = b"def repair_exact_original(raw):\n    return b'R' * 99266, b'helper'\n"


def artifact():
    return {"artifact_id": 1, "run_id": 2, "run_attempt": 1,
            "source_sha": "b" * 40, "zip_sha256": "c" * 64}


def dispatch(*, reconstructed=True):
    selected = {"schema": custody.SOURCE_DISPATCH_SCHEMA if reconstructed else custody.DISPATCH_SCHEMA,
                "motionwright_sha": CURRENT["source_sha"], "semwright_sha": CURRENT["semwright_sha"],
                "official_runtime": {"artifact": artifact(), "receipt_sha256": "d" * 64,
                                     "known_original_module_sha256": "e" * 64,
                                     "known_melt_sha256": "f" * 64},
                "compiler": {"artifact": artifact(), "members": {
                    "compiled_module": "only-libmltavformat.so",
                    "compiler_receipt_path": "only-compiler-receipt.json"}},
                "recipient": {"fixture": "public key validated by existing contracts"}}
    if reconstructed:
        selected["compiler"]["source_mode"] = custody.SOURCE_MODE
    else:
        selected["compiler"]["members"].update(
            original_c_source="original.c", repaired_c_source="repaired.c")
    return selected


def tarball(entries=None):
    if entries is None:
        entries = [(custody.UPSTREAM_MEMBER, ORIGINAL, tarfile.REGTYPE)]
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, raw, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(raw) if kind in (tarfile.REGTYPE, tarfile.AREGTYPE) else 0
            member.linkname = "elsewhere" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            archive.addfile(member, io.BytesIO(raw) if member.size else None)
    return gzip.compress(data.getvalue(), mtime=0)


def packed(values):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, raw in values.items():
            archive.writestr(name, raw)
    return data.getvalue()


class CompilerSourceIntakeTests(unittest.TestCase):
    def fixture_pins(self, raw):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(custody, "UPSTREAM_ARCHIVE_SHA256", custody.sha(raw)))
        stack.enter_context(mock.patch.object(custody.admission, "ORIGINAL_SHA", custody.sha(ORIGINAL)))
        return stack

    def reconstruct(self, root, raw, *, generator=GENERATOR, generator_pin=None):
        path = root / custody.REPAIR_GENERATOR
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generator)
        output = root / "intake"
        output.mkdir()
        with self.fixture_pins(raw), \
             mock.patch.object(custody, "REPAIR_GENERATOR_SHA256",
                               custody.sha(generator) if generator_pin is None else generator_pin), \
             mock.patch.object(custody.admission, "REPAIRED_SHA", custody.sha(REPAIRED)):
            custody.reconstruct_sources(raw, root, output)
        return output

    def test_explicit_second_schema_accepts_only_two_historical_members(self):
        value = dispatch()
        with mock.patch.object(custody, "public_key"):
            self.assertEqual(custody.validate_dispatch(custody.json_bytes(value), CURRENT), value)

    def test_reconstruction_schema_refuses_four_claimed_historical_members(self):
        value = dispatch()
        value["compiler"]["members"]["original_c_source"] = "invented.c"
        with mock.patch.object(custody, "public_key"), self.assertRaisesRegex(
                custody.CustodyRejected, "compiler_member_contract"):
            custody.validate_dispatch(custody.json_bytes(value), CURRENT)

    def test_legacy_schema_still_requires_all_four_real_members(self):
        value = dispatch(reconstructed=False)
        with mock.patch.object(custody, "public_key"):
            custody.validate_dispatch(custody.json_bytes(value), CURRENT)
            del value["compiler"]["members"]["original_c_source"]
            with self.assertRaisesRegex(custody.CustodyRejected, "compiler_member_contract"):
                custody.validate_dispatch(custody.json_bytes(value), CURRENT)

    def test_source_mode_and_schema_are_explicit_not_fallbacks(self):
        for change in ("missing", "different", "extra"):
            value = dispatch()
            if change == "missing":
                del value["compiler"]["source_mode"]
            elif change == "different":
                value["compiler"]["source_mode"] = "compiler-original-source"
            else:
                value["compiler"]["url"] = "https://untrusted.invalid"
            with self.subTest(change=change), mock.patch.object(custody, "public_key"), \
                 self.assertRaises(custody.CustodyRejected):
                custody.validate_dispatch(custody.json_bytes(value), CURRENT)

    def test_exact_regular_upstream_member_is_read_without_extracting_tree(self):
        raw = tarball()
        with self.fixture_pins(raw):
            self.assertEqual(custody.original_from_upstream(raw), ORIGINAL)

    def test_archive_digest_precedes_decompression(self):
        with mock.patch.object(custody.zlib, "decompressobj") as decoder, \
             self.assertRaisesRegex(custody.CustodyRejected, "upstream_archive_digest"):
            custody.original_from_upstream(b"untrusted compressed source")
        decoder.assert_not_called()

    def test_duplicate_member_is_rejected_even_when_bytes_match(self):
        raw = tarball([(custody.UPSTREAM_MEMBER, ORIGINAL, tarfile.REGTYPE)] * 2)
        with self.fixture_pins(raw), self.assertRaisesRegex(
                custody.CustodyRejected, "upstream_duplicate_member"):
            custody.original_from_upstream(raw)

    def test_selected_symlink_and_hardlink_never_follow_a_target(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            raw = tarball([(custody.UPSTREAM_MEMBER, b"", kind)])
            with self.subTest(kind=kind), self.fixture_pins(raw), self.assertRaisesRegex(
                    custody.CustodyRejected, "upstream_selected_regular_member"):
                custody.original_from_upstream(raw)

    def test_missing_member_and_escaped_namespace_are_rejected(self):
        for name in ("different.c", "../outside.c"):
            raw = tarball([(name, ORIGINAL, tarfile.REGTYPE)])
            with self.subTest(name=name), self.fixture_pins(raw), \
                 self.assertRaises(custody.CustodyRejected):
                custody.original_from_upstream(raw)

    def test_selected_size_and_independent_consumer_hash_are_required(self):
        for raw in (tarball([(custody.UPSTREAM_MEMBER, ORIGINAL[:-1], tarfile.REGTYPE)]),
                    tarball([(custody.UPSTREAM_MEMBER, b"X" * 96690, tarfile.REGTYPE)])):
            with self.fixture_pins(raw), self.assertRaises(custody.CustodyRejected):
                custody.original_from_upstream(raw)

    def test_trailing_or_second_gzip_stream_is_not_silently_ignored(self):
        for suffix in (b"unverified trailing bytes", gzip.compress(b"", mtime=0)):
            raw = tarball() + suffix
            with self.fixture_pins(raw), self.assertRaisesRegex(
                    custody.CustodyRejected, "upstream_single_gzip_stream_budget"):
                custody.original_from_upstream(raw)

    def test_decompressed_size_and_member_count_have_independent_budgets(self):
        raw = tarball()
        with self.fixture_pins(raw), mock.patch.object(custody, "MAX_UPSTREAM_TAR", 512), \
             self.assertRaisesRegex(custody.CustodyRejected, "upstream_single_gzip_stream_budget"):
            custody.original_from_upstream(raw)
        with self.fixture_pins(raw), mock.patch.object(custody, "MAX_FILES", 0), \
             self.assertRaisesRegex(custody.CustodyRejected, "upstream_tar_budget"):
            custody.original_from_upstream(raw)

    def test_reconstruction_records_explicit_source_origins_without_build_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = self.reconstruct(Path(temporary).resolve(), tarball())
            self.assertEqual((output / "original_c_source").read_bytes(), ORIGINAL)
            self.assertEqual((output / "repaired_c_source").read_bytes(), REPAIRED)
            receipt = json.loads((output / "source-reconstruction.json").read_bytes())
            self.assertEqual(receipt["original_c_source"]["origin"], "RETRIEVED_PINNED_UPSTREAM")
            self.assertEqual(receipt["repaired_c_source"]["origin"],
                             "DETERMINISTIC_SOURCE_RECONSTRUCTION")
            self.assertFalse(receipt["source_files_selected_from_compiler_artifact"])
            self.assertEqual(receipt["compiler_artifact_source_inventory"],
                             "NOT_INSPECTED_BY_RECONSTRUCTION")
            self.assertNotIn("compiler_artifact_contains_sources", receipt)
            self.assertFalse(receipt["compiled_binary_rebuilt"])
            self.assertFalse(receipt["module_installed"])
            self.assertEqual(receipt["native_execution"], "NOT_RUN")

    def test_wrong_generator_digest_never_executes_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(custody.CustodyRejected, "source_generator_digest"):
                self.reconstruct(root, tarball(), generator=b"raise AssertionError('executed')\n",
                                 generator_pin="0" * 64)
            self.assertFalse((root / "intake/original_c_source").exists())

    def test_wrong_repaired_copy_is_rejected_before_source_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bad = b"def repair_exact_original(raw):\n    return b'X' * 99266, b'helper'\n"
            with self.assertRaisesRegex(custody.CustodyRejected, "reconstructed_source_digest"):
                self.reconstruct(root, tarball(), generator=bad)
            self.assertFalse((root / "intake/repaired_c_source").exists())

    def test_generator_namespace_change_is_rejected_before_writing_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original_reader = custody.regular
            count = 0
            def reader(path, limit=custody.MAX_FILE):
                nonlocal count
                raw, metadata = original_reader(path, limit)
                if path.name == "repair_planar_source.py":
                    count += 1
                    if count == 2:
                        return raw + b"# changed\n", metadata
                return raw, metadata
            with mock.patch.object(custody, "regular", side_effect=reader), \
                 self.assertRaisesRegex(custody.CustodyRejected, "source_generator_changed"):
                self.reconstruct(root, tarball())
            self.assertFalse((root / "intake/original_c_source").exists())

    def test_source_reconstruction_never_overwrites_retained_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "intake"
            output.mkdir()
            (output / "original_c_source").write_bytes(b"previous original")
            path = root / custody.REPAIR_GENERATOR
            path.parent.mkdir(parents=True)
            path.write_bytes(GENERATOR)
            raw = tarball()
            with self.fixture_pins(raw), \
                 mock.patch.object(custody, "REPAIR_GENERATOR_SHA256", custody.sha(GENERATOR)), \
                 mock.patch.object(custody.admission, "REPAIRED_SHA", custody.sha(REPAIRED)), \
                 self.assertRaises(FileExistsError):
                custody.reconstruct_sources(raw, root, output)
            self.assertEqual((output / "original_c_source").read_bytes(), b"previous original")

    def download(self, root, raw, *, wrong_url=False, interrupted=False):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {}
        response.geturl.return_value = "https://elsewhere.invalid" if wrong_url else custody.UPSTREAM_URL
        response.read.side_effect = [raw, TimeoutError("original transfer")] if interrupted else [raw, b""]
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(custody.urllib.request, "build_opener", return_value=opener), \
             mock.patch.object(custody, "UPSTREAM_ARCHIVE_SHA256", custody.sha(raw)), \
             mock.patch.object(custody, "regular", side_effect=lambda p, limit: (p.read_bytes(), {})):
            try:
                return custody.retrieve_upstream(root)
            finally:
                self.assertEqual(opener.open.call_count, 1)

    def test_download_is_once_and_retains_exact_response_with_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(self.download(root, b"exact source archive"), b"exact source archive")
            self.assertEqual((root / "upstream-source.tar.gz").read_bytes(), b"exact source archive")
            terminal = json.loads((root / "upstream-download.terminal.json").read_bytes())
            self.assertEqual(terminal["status"], "RECEIVED_PINNED_BYTES")
            self.assertFalse(terminal["compiler_artifact_member"])
            self.assertFalse(terminal["automatic_retry"])

    def test_response_url_change_and_redirect_are_refused_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                custody.CustodyRejected, "upstream_response_identity"):
            self.download(Path(temporary).resolve(), b"untrusted", wrong_url=True)
        with self.assertRaisesRegex(custody.CustodyRejected, "upstream_redirect_refused"):
            custody.NoUpstreamRedirect().redirect_request(None, None, 302, "", {}, custody.UPSTREAM_URL)

    def test_interrupted_download_keeps_partial_bytes_and_unknown_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaises(TimeoutError):
                self.download(root, b"original partial bytes", interrupted=True)
            self.assertEqual((root / "upstream-source.tar.gz").read_bytes(), b"original partial bytes")
            terminal = json.loads((root / "upstream-download.terminal.json").read_bytes())
            self.assertEqual(terminal["status"], "ERROR_OR_UNKNOWN")
            self.assertFalse(terminal["automatic_retry"])

    def test_oversize_transfer_keeps_original_partial_and_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(custody, "MAX_UPSTREAM_ARCHIVE", 3), self.assertRaisesRegex(
                    custody.CustodyRejected, "upstream_download_budget"):
                self.download(root, b"too large")
            self.assertEqual((root / "upstream-source.tar.gz").read_bytes(), b"too large")

    def test_changed_ELF_or_receipt_is_rejected_before_source_retrieval(self):
        expected = {"compiled_module": b"expected ELF", "compiler_receipt_path": b"expected receipt"}
        for selected in expected:
            value = dispatch()
            archive = packed({value["compiler"]["members"][name]:
                              b"changed" if name == selected else raw
                              for name, raw in expected.items()})
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                with mock.patch.object(custody, "download_artifact", return_value=archive), \
                     mock.patch.object(custody.g2, "REPAIRED_MODULE_SHA256",
                                       custody.sha(expected["compiled_module"])), \
                     mock.patch.object(custody.g2, "COMPILER_RECEIPT_SHA256",
                                       custody.sha(expected["compiler_receipt_path"])), \
                     mock.patch.object(custody, "retrieve_upstream") as download, \
                     self.assertRaisesRegex(custody.CustodyRejected, "artifact_selected_digest"):
                    custody.intake_compiler(value, root, root)
                download.assert_not_called()

    def test_legacy_missing_member_never_triggers_source_reconstruction(self):
        value = dispatch(reconstructed=False)
        archive = packed({"only-libmltavformat.so": b"ELF", "only-compiler-receipt.json": b"receipt"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(custody, "download_artifact", return_value=archive), \
                 mock.patch.object(custody.g2, "REPAIRED_MODULE_SHA256", custody.sha(b"ELF")), \
                 mock.patch.object(custody.g2, "COMPILER_RECEIPT_SHA256", custody.sha(b"receipt")), \
                 mock.patch.object(custody, "retrieve_upstream") as download, \
                 self.assertRaisesRegex(custody.CustodyRejected, "artifact_selected_regular_member"):
                custody.intake_compiler(value, root, root)
            download.assert_not_called()

    def test_historical_binary_and_receipt_remain_inputs_to_unchanged_closure(self):
        value = dispatch()
        archive = packed({"only-libmltavformat.so": b"ELF", "only-compiler-receipt.json": b"receipt"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def reconstruct(raw, repo, directory):
                (directory / "original_c_source").write_bytes(b"upstream bytes")
                (directory / "repaired_c_source").write_bytes(b"deterministic copy")
            with mock.patch.object(custody, "download_artifact", return_value=archive) as historical, \
                 mock.patch.object(custody.g2, "REPAIRED_MODULE_SHA256", custody.sha(b"ELF")), \
                 mock.patch.object(custody.g2, "COMPILER_RECEIPT_SHA256", custody.sha(b"receipt")), \
                 mock.patch.object(custody, "retrieve_upstream", return_value=b"pinned tar") as upstream, \
                 mock.patch.object(custody, "reconstruct_sources", side_effect=reconstruct) as repair, \
                 mock.patch.object(custody.admission, "source_compiler_closure") as closure:
                custody.intake_compiler(value, root, root)
                historical.assert_called_once_with(value["compiler"]["artifact"], "compiler", root)
                upstream.assert_called_once_with(root)
                repair.assert_called_once_with(b"pinned tar", root, root)
                closure.assert_called_once_with(
                    root / "original_c_source", root / "repaired_c_source", root / "compiled_module",
                    custody.sha(b"ELF"), root / "compiler_receipt_path", custody.sha(b"receipt"))
            self.assertEqual((root / "compiled_module").read_bytes(), b"ELF")
            self.assertEqual((root / "compiler_receipt_path").read_bytes(), b"receipt")


if __name__ == "__main__":
    unittest.main()
