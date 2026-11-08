"""Focal parser/filesystem controls, using inert archives; no Native evidence."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_frozen_runtime_restore as reuse


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def inline(value):
    raw = reuse.frozen.encoded(value)
    return {**identity(raw), "original_utf8": raw.decode()}


def members():
    return {**{"metadata/" + name: b"inert parser fixture\n" for name in reuse.frozen.RUNTIME_METADATA},
            **{"wheels/control_%02d-0-py3-none-any.whl" % index: b"inert parser fixture\n"
               for index in range(32)}}


def archive(path, contents, *, duplicate=None, symbolic=None, altered=None):
    with path.open("xb") as stream, tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as writer:
        for name, raw in contents.items():
            if altered == name:
                raw = b"changed fixture!!!!\n"
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid, entry.mtime = len(raw), 0o600, 0, 0, 0
            if symbolic == name:
                entry.type, entry.linkname, entry.size = tarfile.SYMTYPE, "/tmp/outside", 0
                writer.addfile(entry)
            else:
                writer.addfile(entry, io.BytesIO(raw))
            if duplicate == name:
                writer.addfile(entry, io.BytesIO(raw))
    path.chmod(0o600)
    return identity(path.read_bytes())


def command_fixture(argv, seconds):
    return {"intent": inline({"argv": argv, "maximum_seconds": seconds,
        "stream_bytes_each": 4 * reuse.MIB, "owned_process_group": True,
        "preparation_watchdog_bytes": reuse.frozen.MAX_PREPARATION_BYTES,
        "preparation_watchdog_files": reuse.frozen.MAX_PREPARATION_FILES,
        "preparation_hard_disk_quota_claimed": False, "automatic_retry": False}),
        "spawn": inline({"pid": 42, "process_group": 42, "automatic_retry": False}),
        "terminal": inline({"spawned": True, "exit_code": 0, "terminated": False,
            "process_group_term_sent": False, "process_group_kill_sent": False,
            "group_survived_parent_exit": False, "stream_limit_exceeded": False,
            "stream_errors": [], "streams_settled": True, "first_error_type": None,
            "preparation_observed_peak": {"bytes": 0, "files": 0},
            "preparation_hard_disk_quota_claimed": False, "automatic_retry": False}),
        "stdout": identity(b""), "stderr": identity(b"")}


class RuntimeRestoreFocals(unittest.TestCase):
    def test_reviewed_origin_retains_context_and_tar_identity(self):
        receipt, artifact = reuse.origin_metadata()
        self.assertEqual(receipt["context"], reuse.ORIGIN)
        self.assertEqual(receipt["archive"], artifact["tar"])
        self.assertEqual(55, len(receipt["archive_members"]))
        self.assertEqual(211, len(receipt["native_observation"]["files"]))
        self.assertFalse(receipt["old_runtime_equivalence_claimed"])

    def test_context_rejects_origin_replay_sdk_change_and_attempt_change(self):
        current = {**reuse.ORIGIN, "source_sha": "a" * 40, "run_id": "1"}
        self.assertEqual(current, reuse.context(current))
        for update in ({"run_id": reuse.ORIGIN["run_id"]}, {"run_attempt": "2"},
                       {"semwright_sha": "b" * 40}, {"run_id": True}, {"source_sha": "a" * 39}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                reuse.context({**current, **update})

    def test_linux_arguments_keep_posix_paths_for_host_metadata_validation(self):
        argv = reuse.install_argv("/usr/bin/python3", "/tmp/runtime", "/home/runner/work/_temp/frozen")
        self.assertIn("/home/runner/work/_temp/frozen/wheels", argv)
        self.assertIn("/tmp/runtime/site-packages", argv)
        self.assertTrue({"--no-index", "--no-deps", "--no-compile", "--require-hashes"}.issubset(argv))
        self.assertNotIn("wheel", argv)

    def test_new_command_rejects_network_build_and_unknown_or_surviving_exit(self):
        argv = reuse.install_argv("/usr/bin/python3", "/tmp/runtime", "/tmp/work")
        row = command_fixture(argv, 300)
        reuse.validate_command(row, argv, 300)
        for changed in ([word for word in argv if word != "--no-index"], argv + ["https://example.invalid/wheel"]):
            broken = copy.deepcopy(row)
            value = reuse.inline_json(broken["intent"])
            value["argv"] = changed
            broken["intent"] = inline(value)
            with self.assertRaisesRegex(ValueError, "current_command_intent"):
                reuse.validate_command(broken, argv, 300)
        for update in ({"exit_code": None}, {"exit_code": 1}, {"group_survived_parent_exit": True},
                       {"streams_settled": False}, {"stream_errors": ["OSError"]},
                       {"preparation_observed_peak": {"bytes": 1536 * reuse.MIB + 1, "files": 0}}):
            broken = copy.deepcopy(row)
            value = reuse.inline_json(broken["terminal"])
            value.update(update)
            broken["terminal"] = inline(value)
            with self.subTest(update=update), self.assertRaises(ValueError):
                reuse.validate_command(broken, argv, 300)

    def test_inline_original_rejects_duplicate_keys_and_digest_change(self):
        row = inline({"complete": True})
        self.assertEqual({"complete": True}, reuse.inline_json(row))
        row["original_utf8"] = row["original_utf8"].replace("true", "null")
        with self.assertRaises(ValueError):
            reuse.inline_json(row)
        raw = b'{"exit":0,"exit":1}\n'
        with self.assertRaises(ValueError):
            reuse.inline_json({**identity(raw), "original_utf8": raw.decode()})

    def test_original_child_python_is_separate_from_custody_controller(self):
        origin, _artifact = reuse.origin_metadata()
        python = "/usr/bin/python3"
        observed = {"python_executable": python, "python_version": origin["python_version"],
                    "pip_version": "24.0", "uid": 1001, "euid": 1001}
        self.assertEqual(observed, reuse.validate_python_inspection(inline(observed), origin, python))
        for changed in ({"python_executable": "/tmp/manim-custody-venv/bin/python"},
                        {"pip_version": "25.0"}, {"uid": 0, "euid": 0},
                        {"python_version": origin["python_version"] + " changed"}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                reuse.validate_python_inspection(inline({**observed, **changed}), origin, python)
        self.assertEqual(python, reuse.inspect_argv(python)[0])
        self.assertNotIn("PYTHONPATH", reuse.PYTHON_INSPECTION)

    def extract(self, root, *, duplicate=None, symbolic=None, altered=None, preexisting=None):
        contents = members()
        path, work = root / "runtime.tar", root / "work"
        work.mkdir(mode=0o700)
        for name in ("origin", "wheels"):
            (work / name).mkdir(mode=0o700)
        expected = archive(path, contents, duplicate=duplicate, symbolic=symbolic, altered=altered)
        if preexisting:
            (work / "origin" / preexisting).write_bytes(b"original remains")
        return reuse.extract_originals(path, expected, {name: identity(raw) for name, raw in contents.items()},
                                       work, lambda: None), work

    def test_extracts_only_all_55_regular_originals_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            observed, work = self.extract(Path(directory))
            self.assertEqual(55, len(observed))
            self.assertEqual(23, len(list((work / "origin").iterdir())))
            self.assertEqual(32, len(list((work / "wheels").iterdir())))
            self.assertEqual(b"inert parser fixture\n", (work / "origin/installed-files.json").read_bytes())

    def test_extraction_rejects_changed_duplicate_and_symbolic_members(self):
        name = "metadata/input-lock.json"
        for options in ({"duplicate": name}, {"symbolic": name}, {"altered": name}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    self.extract(Path(directory), **options)

    def test_extraction_preserves_existing_destination_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileExistsError):
                self.extract(root, preexisting="input-lock.json")
            self.assertEqual(b"original remains", (root / "work/origin/input-lock.json").read_bytes())
        contents = members()
        contents["wheels/../outside.whl"] = contents.pop("wheels/control_00-0-py3-none-any.whl")
        with tempfile.TemporaryDirectory() as directory:
            root, work = Path(directory), Path(directory) / "work"
            work.mkdir()
            (work / "origin").mkdir()
            (work / "wheels").mkdir()
            path = root / "archive.tar"
            expected = archive(path, contents)
            with self.assertRaises(ValueError):
                reuse.extract_originals(path, expected, {name: identity(raw) for name, raw in contents.items()},
                                        work, lambda: None)
            self.assertFalse((root / "outside.whl").exists())


if __name__ == "__main__":
    unittest.main()
