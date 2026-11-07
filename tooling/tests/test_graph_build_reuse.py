"""Real temporary FS/TAR/zlib controls; these inert fixtures are not Native."""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

PATH = Path(__file__).resolve().parents[1] / "native_graph_build_reuse.py"
SPEC = importlib.util.spec_from_file_location("graph_reuse_fixture", PATH)
reuse = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(reuse)


def inert_elf():
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"; header[18:20] = b"\x3e\x00"
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<HH", header, 54, 56, 1)
    interpreter = b"/lib64/ld-linux-x86-64.so.2\0"
    segment = bytearray(56)
    struct.pack_into("<I", segment, 0, 3)
    struct.pack_into("<Q", segment, 8, 120)
    struct.pack_into("<Q", segment, 32, len(interpreter))
    return bytes(header + segment) + interpreter + b"INERT_NOT_A_PROGRAM"


class ReuseInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="graph-original-inputs-")
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_single_link_stable_handles_reject_symlink_hardlink_and_fifo(self):
        source = self.root / "file"; source.write_bytes(b"original")
        expected = {"bytes": 8, "sha256": reuse.sha(b"original")}
        self.assertEqual(reuse.identity(source), expected)
        alias = self.root / "alias"; alias.symlink_to(source)
        with self.assertRaises(reuse.Rejected): reuse.identity(alias)
        alias.unlink(); os.link(source, alias)
        with self.assertRaises(reuse.Rejected): reuse.identity(source)
        alias.unlink()
        if hasattr(os, "mkfifo"):
            fifo = self.root / "fifo"; os.mkfifo(fifo)
            with self.assertRaises(reuse.Rejected): reuse.identity(fifo)
        with self.assertRaises(reuse.Rejected):
            with reuse.stable_reader(source, 100):
                source.write_bytes(b"different")

    def test_exclusive_zlib_copy_hash_and_interpreter_without_execution(self):
        raw = inert_elf(); source = self.root / "inert.zlib"; source.write_bytes(zlib.compress(raw, 6))
        expected = {"size_bytes": len(raw), "sha256": reuse.sha(raw)}
        target = self.root / "copied-inert"
        observed = reuse.expand_elf(source, target, expected)
        self.assertEqual(observed, {"path": str(target), **expected})
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertEqual(reuse.elf_interpreter(target), "/lib64/ld-linux-x86-64.so.2")
        with self.assertRaises(FileExistsError): reuse.expand_elf(source, target, expected)
        for label, compressed, bound in (("trailing", zlib.compress(raw) + b"extra", len(raw)),
                ("quota", zlib.compress(raw), len(raw) - 1), ("truncated", zlib.compress(raw)[:-1], len(raw))):
            source = self.root / (label + ".zlib"); source.write_bytes(compressed)
            with self.subTest(label=label), self.assertRaises(reuse.Rejected):
                reuse.expand_elf(source, self.root / label, {**expected, "size_bytes": bound})

    def test_material_change_cannot_be_renamed_as_reused_build(self):
        source, tree = "a" * 40, "b" * 40
        allowed = sorted(["tooling/native_graph_build_reuse.py", "crates/native/examples/native_manim_editable/recovery.rs"])
        value = reuse.source_comparison(reuse.ORIGIN_TREE, tree, source, allowed)
        self.assertEqual(value["new_builds"], 0)
        self.assertFalse(value["native_authority"])
        for changed in (allowed + ["Cargo.lock"], ["crates/service/src/lib.rs"], list(reversed(allowed)), allowed + allowed):
            with self.subTest(changed=changed), self.assertRaises(reuse.Rejected):
                reuse.source_comparison(reuse.ORIGIN_TREE, tree, source, changed)
        with self.assertRaises(reuse.Rejected):
            reuse.source_comparison(reuse.ORIGIN_TREE, tree, source, allowed, dirty=True)

    @contextmanager
    def capsule(self, *, duplicate=False, link=False, hidden=False):
        """Pin an explicitly inert 124-member fixture, never a production pin."""
        index_raw = reuse.encoded({"inert_test_fixture": True})
        items = {"producer/producer-index.original.json": index_raw,
                 **{"producer/inert/%03d" % index: ("fixture-%03d" % index).encode() for index in range(122)}}
        rows = [{"path": name, "bytes": len(raw), "sha256": reuse.sha(raw)} for name, raw in sorted(items.items())]
        manifest = {"schema": "motionwright.graph.origin-build-capsule/1", "origin": reuse.ORIGIN,
            "origin_source_tree": reuse.ORIGIN_TREE, "origin_sdk_tree": reuse.SDK_TREE,
            "origin_index_sha256": reuse.sha(index_raw), "origin_review_sha256": reuse.ORIGIN_REVIEW_SHA,
            "roles": list(reuse.ROLES), "current_context": None, "original_snapshot_complete": False,
            "current_source_or_abi_verified": False, "owner_state_included": False, "owner_authority": False,
            "runtime_tree_included": False, "members": rows,
            "binaries": {role: {"size_bytes": 1} for role in reuse.ROLES}}
        manifest_raw = reuse.encoded(manifest)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            def add(name, data, symlink=False):
                info = tarfile.TarInfo(name); info.mode = 0o600; info.size = len(data)
                if symlink:
                    info.type = tarfile.SYMTYPE; info.linkname = "/not-an-original"; info.size = 0
                archive.addfile(info, io.BytesIO(data))
            add("manifest.json", manifest_raw)
            for number, (name, raw) in enumerate(sorted(items.items())):
                add(name, raw, symlink=link and number == 0)
            if duplicate:
                add(rows[0]["path"], items[rows[0]["path"]])
        data = output.getvalue()
        if hidden:
            data = data[:-1] + b"x"
        archive_path = self.root / ("capsule-%s%s%s.tar" % (duplicate, link, hidden))
        archive_path.write_bytes(data)
        with ExitStack() as stack:
            for key, value in {"CAPSULE": {"bytes": len(data), "sha256": reuse.sha(data)},
                "MANIFEST_SHA": reuse.sha(manifest_raw), "MANIFEST_BYTES": len(manifest_raw),
                "PAYLOAD_BYTES": sum(map(len, items.values())), "ORIGIN_INDEX_SHA": reuse.sha(index_raw)}.items():
                stack.enter_context(patch.object(reuse, key, value))
            yield archive_path, items

    def test_exact_124_member_extraction_is_physical_and_once_only(self):
        with self.capsule() as (source, expected):
            target = self.root / "retained"
            manifest, index = reuse.retain_capsule(source, target)
            self.assertTrue(index["inert_test_fixture"])
            self.assertEqual(len(manifest["members"]), 123)
            for name, raw in expected.items(): self.assertEqual((target / name).read_bytes(), raw)
            self.assertEqual(json.loads((target / "intake-terminal.original.json").read_bytes())["members"], 124)
            with self.assertRaises(FileExistsError): reuse.retain_capsule(source, target)

    def test_tar_duplicate_link_and_nonzero_hidden_trailer_are_authentic_negatives(self):
        for label, options in (("duplicate", {"duplicate": True}), ("link", {"link": True}), ("trailer", {"hidden": True})):
            with self.subTest(label=label), self.capsule(**options) as (source, _):
                target = self.root / label
                with self.assertRaises(reuse.Rejected): reuse.retain_capsule(source, target)
                negative = json.loads((target / "intake-negative.original.json").read_bytes())
                self.assertEqual(negative["status"], "INCOMPLETE_NO_RETRY")
                self.assertFalse((target / "intake-terminal.original.json").exists())
                self.assertTrue(source.exists())

    def release_layout(self, source, name):
        producer = self.root / name; producer.mkdir()
        intake = producer / "origin-transport/intake"; intake.mkdir(parents=True)
        retained = producer / "origin-build52"; reuse.retain_capsule(source, retained)
        cipher = intake / "graph52-build-inputs.mwseal"; cipher.write_bytes(b"authenticated-test-ciphertext")
        tar = intake / "graph52-build-inputs.original.tar"; tar.write_bytes(source.read_bytes())
        current = {"source_sha": "a" * 40, "semwright_sha": reuse.ORIGIN["semwright_sha"],
                   "run_id": "99999999999", "run_attempt": "1"}
        proof = reuse.source_comparison(reuse.ORIGIN_TREE, "b" * 40, current["source_sha"], [])
        reuse.new_json(intake / "intake-terminal.original.json", {"status": "AUTHENTICATED_ORIGINAL_BUILD_INPUTS",
            "context": current, "capsule": reuse.CAPSULE, "owner_authority": False, "automatic_retry": False,
            "asset": reuse.identity(cipher), "capsule_path": str(tar)})
        return intake, retained, proof, current

    def test_release_deletes_only_two_verified_copies_and_preserves_source(self):
        with self.capsule() as (source, _):
            intake, retained, proof, current = self.release_layout(source, "success")
            negative = intake / "earlier-negative.original.json"; negative.write_bytes(b"keep")
            ref = reuse.release_verified_transport(intake, retained, proof, current)
            result = json.loads(Path(ref["path"]).read_bytes())
            self.assertEqual(result["released"], ["graph52-build-inputs.mwseal", "graph52-build-inputs.original.tar"])
            self.assertTrue(source.exists() and (retained / "producer/producer-index.original.json").exists())
            self.assertEqual(negative.read_bytes(), b"keep")
            self.assertFalse((intake / "graph52-build-inputs.original.tar").exists())
            with self.assertRaises((FileNotFoundError, FileExistsError, reuse.Rejected)):
                reuse.release_verified_transport(intake, retained, proof, current)

    def test_release_first_failure_retains_partial_absence_without_retry(self):
        with self.capsule() as (source, _):
            intake, retained, proof, current = self.release_layout(source, "negative")
            original_unlink = Path.unlink
            first = OSError("inert injected unlink failure")
            def unlink(path, *args, **kwargs):
                if path == intake / "graph52-build-inputs.original.tar": raise first
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, "unlink", unlink), self.assertRaises(OSError) as caught:
                reuse.release_verified_transport(intake, retained, proof, current)
            self.assertIs(caught.exception, first)
            record = json.loads((intake / "release-negative.original.json").read_bytes())
            self.assertEqual(record["released_before_error"], ["graph52-build-inputs.mwseal"])
            self.assertEqual(record["status"], "UNKNOWN_NO_RETRY")
            self.assertTrue(source.exists() and (intake / "graph52-build-inputs.original.tar").exists())
            self.assertFalse((intake / "release-terminal.original.json").exists())


if __name__ == "__main__":
    unittest.main()
