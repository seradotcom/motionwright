"""One original ZIP, closed ciphertext names and hashes, no extracted disk duplicates.

All samples are short synthetic ZIPs. No RSA keys, Native renderer,
GitHub action, encrypted project payload or release decision is used.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import types
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
from native_manim_artifact_original import OriginalCiphertextArchive, identity

ITEMS = {
    "edited.mwseal": b"MWCISEAL1\nnew-native-phase",
    "resume-outer.mwseal": b"MWCISEAL1\nowner-envelope",
    "resume-outer.public.json": b'{"ciphertext_sha256":"synthetic-not-an-approval"}',
}


def example_archive(items):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in items:
            archive.writestr(name, value)
    return data.getvalue()


class OriginalZipCustodyTests(unittest.TestCase):
    def setUp(self):
        self.raw = example_archive(sorted(ITEMS.items()))
        self.live = [self.raw]

    def open_original(self, *, raw=None, selected=None, capsule=1024, max_archive=4096):
        raw = self.raw if raw is None else raw
        selected = set(ITEMS) if selected is None else selected
        return OriginalCiphertextArchive(
            raw, identity(raw), selected, reread_original=lambda: self.live[0],
            max_archive=max_archive, max_capsule=capsule)

    def test_archive_members_are_authenticated_without_disk_extraction(self):
        original = self.open_original()
        for name, raw in ITEMS.items():
            self.assertEqual(original.member(name, identity(raw)), raw)
        self.assertFalse(hasattr(original, "extract"))
        original.finish()

    def test_wrong_member_digest_is_rejected(self):
        original = self.open_original()
        with self.assertRaisesRegex(ValueError, "member_digest"):
            original.member("edited.mwseal", identity(b"forged"))
        original.abort()

    def test_orig_zip_changed_after_admission_is_rejected(self):
        original = self.open_original()
        original.member("edited.mwseal", identity(ITEMS["edited.mwseal"]))
        self.live[0] = self.raw + b"changed"
        with self.assertRaisesRegex(ValueError, "zip_changed"):
            original.finish()

    def test_extra_or_missing_ciphertext_member_is_rejected(self):
        extra = example_archive([*ITEMS.items(), ("foreign.mwseal", b"forged")])
        with self.assertRaisesRegex(ValueError, "closed_member_roster"):
            self.open_original(raw=extra)
        missing = example_archive(list(ITEMS.items())[:-1])
        with self.assertRaisesRegex(ValueError, "closed_member_roster"):
            self.open_original(raw=missing)

    def test_duplicate_entry_and_large_json_are_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate = example_archive([*ITEMS.items(), ("edited.mwseal", b"duplicate")])
        with self.assertRaisesRegex(ValueError, "closed_member_roster"):
            self.open_original(raw=duplicate)
        oversized = example_archive([
            ("edited.mwseal", ITEMS["edited.mwseal"]),
            ("resume-outer.mwseal", ITEMS["resume-outer.mwseal"]),
            ("resume-outer.public.json", b"x" * 65537),
        ])
        with self.assertRaisesRegex(ValueError, "closed_member_roster"):
            self.open_original(raw=oversized, max_archive=100000)

    def test_unselected_member_is_rejected(self):
        original = self.open_original()
        with self.assertRaisesRegex(ValueError, "member_not_selected"):
            original.member("original-reference.mwseal")
        original.abort()

    def test_unknown_source_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "selected_names"):
            self.open_original(selected={"edited.mwseal", "foreign.mwseal"})

    def test_current_lineage_routes_original_zip_and_keeps_legacy_reader(self):
        lineage = (ROOT / "tooling/native_manim_recovery_lineage.py").read_text(encoding="utf-8")
        snapshot = (ROOT / "tooling/native_manim_resume_lineage.py").read_text(encoding="utf-8")
        custody = (ROOT / "tooling/native_manim_editable_cycle.py").read_text(encoding="utf-8")
        for name, source in (
            ("native_manim_recovery_lineage.py", lineage),
            ("native_manim_resume_lineage.py", snapshot),
            ("native_manim_editable_cycle.py", custody),
        ):
            ast.parse(source, filename=name)
        tree = ast.parse(lineage)
        functions = {node.name: node for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        intake = ast.get_source_segment(lineage, functions["intake"])
        verify = ast.get_source_segment(lineage, functions["verify"])
        self.assertIn("OriginalCiphertextArchive(", intake)
        self.assertNotIn('destination = root / "ciphertexts"', intake)
        self.assertNotIn("engine.new_bytes(destination / member.filename", intake)
        self.assertIn('"ciphertext_origin": OriginalCiphertextArchive.MODE', intake)
        self.assertIn('mode in (None, OriginalCiphertextArchive.MODE)', verify)
        self.assertIn('original_member("resume-outer.public.json")', verify)
        self.assertIn("archive.finish()", verify)
        self.assertIn("original_reader=None", snapshot)
        self.assertIn("original_ciphertext = raw if original_reader is not None else None", snapshot)
        self.assertIn('if "archive_ciphertext_bytes" in phase', custody)
        self.assertIn('phase.get("archive_ciphertext_origin")', custody)
        self.assertIn('cipher = read(phase["sealed_originals"]', custody)
        self.assertIn('custody.get("ciphertext_sha256")', custody)



class InMemoryLineageAdapterTests(unittest.TestCase):
    """Execute the real source functions with synthetic, bounded input only."""

    def source_symbol(self, filename, symbol, names):
        path = ROOT / "tooling" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets = [node for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                   and node.name == symbol]
        self.assertEqual(len(targets), 1)
        module = ast.fix_missing_locations(ast.Module(body=targets, type_ignores=[]))
        exec(compile(module, str(path), "exec"), names)
        return names[symbol]

    def test_snapshot_consumes_verified_zip_member_without_a_cipher_file(self):
        current = {"source_sha": "a" * 40}
        header_sha = "b" * 64
        payload = b"file payload originally retained"
        observed = {"size": len(payload), "sha256": identity(payload)["sha256"],
                    "before": "unchanged", "after": "unchanged",
                    "namespace_after": "unchanged"}
        manifest = {"schema": "motionwright.ci.private-snapshot/1",
                    "scope": "selected-original-files-at-read-time",
                    "complete_owner_state_claimed": False,
                    "selected_originals_complete": True,
                    "roots": {"evidence": {"status": "CAPTURED", "files": [
                        {"archive_path": "evidence/token.txt", "observation": observed}]}}}
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in (("snapshot-manifest.json", json.dumps(manifest).encode()),
                              ("evidence/token.txt", payload)):
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        tar_raw = tar_stream.getvalue()
        magic = b"MWCISEAL1\n"
        header = json.dumps({
            "context": current, "recipient_spki_sha256": header_sha,
            "plaintext_bytes": len(tar_raw)}).encode()
        cipher = magic + len(header).to_bytes(4, "big") + header + b"synthetic-cipher"
        zipped = example_archive([
            ("edited.mwseal", cipher), ("resume-outer.public.json", b'{"private":false}')])
        archive = OriginalCiphertextArchive(
            zipped, identity(zipped), {"edited.mwseal", "resume-outer.public.json"},
            reread_original=lambda: zipped, max_archive=10000, max_capsule=10000)
        engine = types.SimpleNamespace(
            MAGIC=magic, MAX_FILES=8, strict=lambda raw: json.loads(raw),
            decrypt_snapshot=lambda raw, key: (tar_raw, current))
        def require(condition, reason):
            if not condition:
                raise ValueError(reason)
        names = {"Path": Path, "read": lambda path, maximum: (_ for _ in ()).throw(
                     AssertionError("Disk ciphertext read is prohibited")),
                 "identity": identity, "require": require, "sha": lambda raw: identity(raw)["sha256"],
                 "io": io, "tarfile": tarfile, "PurePosixPath": PurePosixPath,
                 "object_bytes": lambda raw: json.loads(raw),
                 "engine": engine, "MAX_CAPSULE": 65536, "RECIPIENT_SHA": header_sha}
        Snapshot = self.source_symbol("native_manim_resume_lineage.py", "Snapshot", names)
        reader = lambda: archive.member("edited.mwseal", identity(cipher))
        snapshot = Snapshot(Path("E:/not-materialized.mwseal"), identity(cipher),
                            current, object(), complete=True, original_reader=reader)
        self.assertEqual(snapshot.original_ciphertext, cipher)
        self.assertEqual(snapshot.raw("evidence/token.txt", 1024), payload)
        snapshot.close()
        archive.finish()

    def test_original_phase_custody_works_without_cipher_disk_and_still_denies_changes(self):
        current = {"source_sha": "a" * 40}
        native = b"native-original"
        source = b"retained-original-source"
        admission = b"original-video-admission"
        cipher = b"MWCISEAL1\none-original-ciphertext"
        def digest(raw):
            return identity(raw)["sha256"]
        result = {
            "status": "PASS_REQUIRES_SEALED_CUSTODY",
            "native_effect_invocations": 1,
            "native_result_original_retained": True,
            "automatic_retry": False,
            "native_evidence_sha256": digest(native),
            "source_retention_sha256": digest(source),
            "native_video_admission_sha256": digest(admission),
        }
        custody = {
            "schema": "motionwright.manim.editable.sealed-custody/1",
            "status": "PASS", "context": current,
            "selected_originals_complete": True, "native_result_retained": True,
            "secondary_error_count": 0, "ciphertext_bytes": len(cipher),
            "ciphertext_sha256": digest(cipher),
        }
        def require(condition, reason):
            if not condition:
                raise ValueError(reason)
        def read(path, maximum=None):
            data = {
                "motionwright-manim-evidence.json": native,
                "editable-source-retention.json": source,
                "native-video-admission.json": admission,
                "not-materialized.mwseal": cipher,
            }
            return data[path.name]
        def read_object(path):
            return result if path.name == "result.json" else custody
        names = {"read": read, "read_object": read_object, "require": require,
                 "sha": digest, "Path": Path}
        verify_phase = self.source_symbol("native_manim_editable_cycle.py",
                                          "verify_phase", names)
        phase = {
            "current": current, "evidence": Path("E:/inert"),
            "native_path": Path("E:/inert/motionwright-manim-evidence.json"),
            "phase_observation": result, "sealed_custody": custody,
            "sealed_originals": Path("E:/not-materialized.mwseal"),
            "archive_ciphertext_origin": OriginalCiphertextArchive.MODE,
            "archive_ciphertext_bytes": cipher,
        }
        self.assertEqual(verify_phase(phase, current), custody)
        with self.assertRaisesRegex(ValueError, "original_ciphertext_changed"):
            verify_phase({**phase, "archive_ciphertext_bytes": cipher + b"x"}, current)
        with self.assertRaisesRegex(ValueError, "original_zip_member_contract"):
            verify_phase({**phase, "archive_ciphertext_origin": "forged"}, current)
        with mock.patch.object(Path, "exists", return_value=True):
            with self.assertRaisesRegex(ValueError, "original_zip_member_contract"):
                verify_phase(phase, current)
        # Historic physical ciphertext reader remains supported and hashed.
        legacy = {key: value for key, value in phase.items()
                  if not key.startswith("archive_ciphertext_")}
        self.assertEqual(verify_phase(legacy, current), custody)

if __name__ == "__main__":
    unittest.main()
