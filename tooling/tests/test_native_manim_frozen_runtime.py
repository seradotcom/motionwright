"""Local FS/container/process controls; no package install, network, or Native credit.

The receipt fixtures exercise metadata validation only. Their wheel hash claims
are not proof that those wheels exist, and no fixture is a rendered runtime.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tooling/native_manim_frozen_runtime.py"
SPEC = importlib.util.spec_from_file_location("test_frozen_runtime_actual_module", PATH)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def hashed(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@unittest.skipUnless(sys.platform == "linux", "Linux file handles and owned process groups")
class FrozenRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-fr-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def put(self, relative, raw):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(0o600)
        return path

    def sparse(self, name, length):
        path = self.root / name
        with path.open("xb") as output:
            output.truncate(length)
        path.chmod(0o600)
        return path

    def wheel(self, name="demo-1.0-py3-none-any.whl", *, metadata_name="Demo", extra=None):
        path = self.root / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/__init__.py", b"VALUE = 1\n")
            archive.writestr("demo-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: " + metadata_name + "\nVersion: 1.0\n")
            if extra is not None:
                entry, body = extra
                archive.writestr(entry, body)
        path.chmod(0o600)
        return path

    def test_regular_byte_identity_and_real_symlink_hardlink_denials(self):
        source = self.put("source", b"bounded original\0bytes")
        actual, raw = runtime.regular(source, 64, collect=True)
        self.assertEqual(actual, hashed(raw))
        (self.root / "link").symlink_to(source)
        with self.assertRaises(ValueError):
            runtime.regular(self.root / "link", 64)
        os.link(source, self.root / "hardlink")
        with self.assertRaises(ValueError):
            runtime.regular(source, 64)

    def test_fifo_is_rejected_before_a_blocking_read_or_writer(self):
        fifo = self.root / "fifo"; os.mkfifo(fifo, 0o600)
        code = """import runpy,sys
m=runpy.run_path(sys.argv[1])
try: m['regular'](sys.argv[2], 64)
except ValueError: print('FIFO_REJECTED_WITHOUT_WRITER')
else: raise AssertionError('FIFO admitted')
"""
        completed = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(PATH), str(fifo)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "FIFO_REJECTED_WITHOUT_WRITER")

    def test_inventory_reads_real_bytes_and_rejects_linked_directories(self):
        source = self.put("installed/site-packages/demo.py", b"version = 1\n")
        root = self.root / "installed"
        before = runtime.inventory(root)
        self.assertEqual(before["files"], [{"path": "site-packages/demo.py", **hashed(source.read_bytes())}])
        source.write_bytes(b"version = 2\n")
        self.assertNotEqual(runtime.inventory(root), before)
        (root / "alias").symlink_to(root / "site-packages", target_is_directory=True)
        with self.assertRaises(ValueError):
            runtime.inventory(root)

    def test_wheel_metadata_observes_real_zip_identity_and_expansion(self):
        wheel = self.wheel()
        value = runtime.wheel_metadata(wheel, {"name": "demo", "version": "1.0"})
        self.assertEqual({key: value[key] for key in ("bytes", "sha256")}, hashed(wheel.read_bytes()))
        with zipfile.ZipFile(wheel) as archive:
            self.assertEqual(value["expanded_bytes"], sum(row.file_size for row in archive.infolist()))
            self.assertEqual(value["expanded_files"], 2)
        with self.assertRaisesRegex(ValueError, "wheel_identity"):
            runtime.wheel_metadata(wheel, {"name": "foreign", "version": "1.0"})

    def test_wheel_traversal_symlink_and_expansion_bomb_are_rejected(self):
        linked = zipfile.ZipInfo("demo/link")
        linked.create_system = 3; linked.external_attr = (stat.S_IFLNK | 0o777) << 16
        for index, extra in enumerate((("../outside", b"outside"), (linked, b"../../outside"))):
            with self.subTest(index=index):
                wheel = self.wheel("bad" + str(index) + ".whl", extra=extra)
                with self.assertRaises(ValueError):
                    runtime.wheel_metadata(wheel, {"name": "demo", "version": "1.0"})
        # A real ZIP header with a dishonest expansion claim must be denied
        # before allocating/decompressing that declared payload.
        wheel = self.wheel("oversized.whl")
        raw = bytearray(wheel.read_bytes()); offset = raw.index(b"PK\x01\x02")
        struct.pack_into("<I", raw, offset + 24, runtime.MAX_INSTALLED + 1)
        wheel.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "wheel_roster"):
            runtime.wheel_metadata(wheel, {"name": "demo", "version": "1.0"})

    def test_tar_is_physically_read_back_and_original_files_survive(self):
        wheel = self.wheel(); note = self.put("note.json", b'{"scope":"test-only"}\n')
        selected = {"wheels/demo.whl": wheel, "metadata/note.json": note}
        output = self.root / "retained.tar"
        value, members = runtime.tar_originals(output, selected)
        self.assertEqual(value, hashed(output.read_bytes()))
        with tarfile.open(output, "r:") as archive:
            recovered = {row.name: archive.extractfile(row).read() for row in archive.getmembers()}
        self.assertEqual(recovered, {name: path.read_bytes() for name, path in selected.items()})
        self.assertEqual(members, {name: hashed(data) for name, data in recovered.items()})
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_tar_budget_and_unsafe_name_deny_before_output_creation(self):
        selected = {"wheels/part" + str(i) + ".whl": self.sparse("part" + str(i), runtime.MAX_INPUT) for i in range(3)}
        output = self.root / "too-large.tar"
        with self.assertRaisesRegex(ValueError, "tar_budget"):
            runtime.tar_originals(output, selected)
        self.assertFalse(output.exists())
        small = self.put("small", b"original")
        with self.assertRaisesRegex(ValueError, "relative_name"):
            runtime.tar_originals(output, {"../outside": small})
        self.assertFalse(output.exists())

    def metadata_fixture(self, label="metadata"):
        """Inert metadata claims plus real local FS; not authentic wheel custody."""
        lock, lock_raw = runtime.input_lock()
        folder = self.root / label; folder.mkdir()
        installed = folder / "runtime"; installed.mkdir()
        fixture = installed / "unit-fixture.txt"; fixture.write_bytes(b"not an installed renderer\n"); fixture.chmod(0o600)
        manifest = runtime.inventory(installed)
        context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "1", "run_attempt": "1"}
        wheels = [{"name": row["name"], "version": row["version"], "input_kind": row["kind"],
            "input_sha256": row["sha256"], "sha256": row["sha256"], "bytes": 1,
            "expanded_bytes": 1, "expanded_files": 1,
            "filename": row["filename"] if row["kind"] == "wheel" else row["name"] + "-" + row["version"] + "-py3-none-any.whl"}
            for row in lock["entries"]]
        wheel_lock = {"schema": "motionwright.manim.exact-wheel-lock/1", "context": context,
            "input_lock_sha256": runtime.LOCK_SHA, "wheels": wheels, "source_builds": 3,
            "sdist_rebuild_byte_reproducible_claimed": False}
        python = Path(sys.executable).resolve(strict=True)
        python_identity = runtime.regular(python, runtime.MAX_INPUT)
        native = {"schema": "motionwright.manim.imported-native-observation/1", "runtime_path": str(installed),
            "python_version": sys.version, "machine": "x86_64", "system": "Linux",
            "manim_version": "0.21.0", "manimpango_version": "0.6.1", "pyav_version": "15.0.0",
            "fontconfig_version": 1, "libav_versions": {}, "files": [{"path": str(python), **python_identity}],
            "scope": "native_files_mapped_by_preparation_imports", "all_system_dependencies_claimed": False,
            "native_effect_invocations": 0}
        members = {"wheels/" + row["filename"]: {key: row[key] for key in ("bytes", "sha256")} for row in wheels}
        members.update({"metadata/" + name: hashed(b"{}\n") for name in runtime.RUNTIME_METADATA})
        members.update({"metadata/input-lock.json": hashed(lock_raw),
            "metadata/wheel-lock.json": hashed(runtime.encoded(wheel_lock)),
            "metadata/installed-files.json": hashed(runtime.encoded(manifest)),
            "metadata/native-observation.json": hashed(runtime.encoded(native))})
        receipt = {"schema": "motionwright.manim.frozen-runtime/1", "status": "EXACT_WHEELS_AND_INSTALLED_FILES_RETAINED",
            "context": context, "runtime_path": str(installed), "input_lock_sha256": runtime.LOCK_SHA,
            "wheel_lock": members["metadata/wheel-lock.json"], "installed_manifest": members["metadata/installed-files.json"],
            "python": python_identity, "python_version": sys.version, "native_observation": native,
            "archive": {"bytes": runtime.MIB, "sha256": "c" * 64}, "archive_members": members,
            "archive_physical_readback": True, "old_runtime_equivalence_claimed": False,
            "sdist_rebuild_byte_reproducible_claimed": False, "native_effect_invocations": 0,
            "project_inputs_read": False, "automatic_retry": False}
        for name, value in (("wheel-lock.json", wheel_lock), ("installed-files.json", manifest), ("receipt.json", receipt)):
            runtime.json_new(folder / name, value)
        return folder, installed, receipt, wheel_lock

    def replace_json(self, path, value):
        # Deliberate corruption of an inert parser fixture, never a production original.
        path.write_bytes(runtime.encoded(value)); path.chmod(0o600)

    def test_metadata_only_validation_and_physical_check_reject_changed_installed_bytes(self):
        folder, installed, _receipt, _wheel_lock = self.metadata_fixture()
        value = runtime.validate_receipt(folder / "receipt.json")
        self.assertFalse(value["archive_content_read_by_binding_validation"])
        self.assertFalse(value["old_runtime_equivalence_claimed"])
        self.assertEqual(runtime.check(installed, folder / "receipt.json"), value)
        (installed / "unit-fixture.txt").write_bytes(b"changed after readback\n")
        with self.assertRaisesRegex(ValueError, "installed_bytes_changed"):
            runtime.check(installed, folder / "receipt.json")

    def test_receipt_rejects_changed_lock_and_unselected_archive_member(self):
        folder, _installed, receipt, _wheel_lock = self.metadata_fixture()
        with (folder / "wheel-lock.json").open("ab") as output:
            output.write(b" ")
        with self.assertRaisesRegex(ValueError, "receipt_chain"):
            runtime.validate_receipt(folder / "receipt.json")
        folder, _installed, receipt, _wheel_lock = self.metadata_fixture("extra-member")
        receipt["archive_members"]["unselected/private"] = hashed(b"private")
        self.replace_json(folder / "receipt.json", receipt)
        with self.assertRaisesRegex(ValueError, "archive_binding"):
            runtime.validate_receipt(folder / "receipt.json")

    def test_aggregate_wheel_expansion_denies_even_with_rebound_metadata_hashes(self):
        folder, _installed, receipt, wheel_lock = self.metadata_fixture()
        wheel_lock["wheels"][0]["expanded_bytes"] = runtime.MAX_INSTALLED
        raw = runtime.encoded(wheel_lock)
        receipt["wheel_lock"] = hashed(raw)
        receipt["archive_members"]["metadata/wheel-lock.json"] = hashed(raw)
        self.replace_json(folder / "wheel-lock.json", wheel_lock)
        self.replace_json(folder / "receipt.json", receipt)
        with self.assertRaisesRegex(ValueError, "aggregate_expansion"):
            runtime.validate_receipt(folder / "receipt.json")

    def test_command_success_retains_real_streams_and_terminal_without_retry(self):
        work = self.root / "command"; work.mkdir()
        argv = [sys.executable, "-I", "-B", "-c", "import sys; print('real stdout'); print('real stderr',file=sys.stderr)"]
        runtime.command(work, "success", argv, dict(os.environ), seconds=3)
        terminal = runtime.object_file(work / "success.terminal.json")
        self.assertTrue(terminal["spawned"] and terminal["streams_settled"])
        self.assertEqual(terminal["exit_code"], 0)
        self.assertFalse(terminal["terminated"] or terminal["automatic_retry"])
        self.assertEqual((work / "success.stdout.log").read_bytes(), b"real stdout\n")
        self.assertEqual((work / "success.stderr.log").read_bytes(), b"real stderr\n")
        with self.assertRaises(FileExistsError):
            runtime.command(work, "success", argv, dict(os.environ), seconds=3)

    @staticmethod
    def live(pid):
        try:
            state = Path("/proc") / str(pid) / "stat"
            return state.read_text().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
        except FileNotFoundError:
            return False

    def test_timeout_stops_real_parent_and_term_ignoring_child_and_settles_pipes(self):
        work = self.root / "timeout"; work.mkdir()
        child_file, heartbeat = work / "child.pid", work / "heartbeat"
        code = """import os,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
pid=os.fork()
if pid == 0:
 with open(sys.argv[1],'x') as out: out.write(str(os.getpid()))
 end=time.monotonic()+12
 while time.monotonic()<end:
  with open(sys.argv[2],'ab') as out: out.write(b'.')
  time.sleep(.025)
 os._exit(0)
time.sleep(12)
"""
        try:
            with self.assertRaisesRegex(ValueError, "command_failed"):
                runtime.command(work, "timeout", [sys.executable, "-I", "-B", "-c", code, str(child_file), str(heartbeat)],
                                dict(os.environ), seconds=1)
            terminal = runtime.object_file(work / "timeout.terminal.json")
            self.assertTrue(terminal["spawned"] and terminal["terminated"] and terminal["streams_settled"])
            self.assertTrue(terminal["process_group_term_sent"] and terminal["process_group_kill_sent"])
            self.assertTrue(child_file.is_file(), "the real child must have reached its loop")
            self.assertFalse(self.live(int(child_file.read_text())))
            before = heartbeat.stat().st_size; time.sleep(.05)
            self.assertEqual(heartbeat.stat().st_size, before)
        finally:
            # A failed guard test must not leave its deliberately finite writer alive.
            if child_file.exists():
                pid = int(child_file.read_text())
                if self.live(pid):
                    os.kill(pid, signal.SIGKILL)

    def test_real_post_spawn_receipt_write_failure_cleans_child_and_preserves_first_error(self):
        work = self.root / "spawn-failure"; work.mkdir()
        # Exclusive creation of the real spawn receipt fails only after Popen.
        (work / "failure.spawn.json").mkdir()
        argv = [sys.executable, "-I", "-B", "-c", "import time; time.sleep(12)"]
        started = time.monotonic()
        with self.assertRaises((FileExistsError, IsADirectoryError)):
            runtime.command(work, "failure", argv, dict(os.environ), seconds=3)
        terminal = runtime.object_file(work / "failure.terminal.json")
        self.assertTrue(terminal["spawned"] and terminal["terminated"] and terminal["streams_settled"])
        self.assertTrue(terminal["process_group_term_sent"])
        self.assertIn(terminal["first_error_type"], ("FileExistsError", "IsADirectoryError"))
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue((work / "failure.spawn.json").is_dir())

    def test_preparation_watchdog_observes_real_sparse_excess_and_cleans_the_child(self):
        work = self.root / "watchdog"; work.mkdir()
        large = self.sparse("observed-excess", runtime.MAX_PREPARATION_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "preparation_watchdog_budget"):
            runtime.command(work, "watchdog", [sys.executable, "-I", "-B", "-c", "import time; time.sleep(12)"],
                            dict(os.environ), seconds=3, observed_roots=(self.root,))
        terminal = runtime.object_file(work / "watchdog.terminal.json")
        self.assertTrue(terminal["spawned"] and terminal["terminated"] and terminal["streams_settled"])
        self.assertEqual(terminal["first_error_type"], "ValueError")
        self.assertFalse(terminal["preparation_hard_disk_quota_claimed"])
        self.assertEqual(large.stat().st_size, runtime.MAX_PREPARATION_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
