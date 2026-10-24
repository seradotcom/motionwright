"""Exercise the distribution observation producer with real isolated files.

Every source/admission fixture is explicitly synthetic. These tests grant no
distribution rights and do not establish checkout-to-binary build causality.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import uuid

TEST_FILE = Path(__file__).resolve()
TOOLING = TEST_FILE.parents[1]
SCRIPT = TOOLING / "distribution_build_record.py"
sys.path.insert(0, str(TOOLING))
sys.path.insert(0, str(TEST_FILE.parent))

from test_source_lock_admission import (
    distribution_fixture_snapshot,
    pin_distribution_fixture,
    write_distribution_fixture,
    write_distribution_reference,
)

RUNTIME_ROOT = Path(
    os.environ.get("MOTIONWRIGHT_TEST_ROOT") or tempfile.gettempdir()
).resolve(strict=True)
REFUSAL = "distribution_build_record_rejected"
LARGE_PAYLOAD = bytes(range(256)) * 4099 + b"synthetic payload end\n"


def _load_producer() -> ModuleType:
    module = ModuleType("distribution_build_record_tested")
    module.__file__ = str(SCRIPT)
    module.__package__ = ""
    exec(compile(SCRIPT.read_bytes(), str(SCRIPT), "exec", dont_inherit=True),
         module.__dict__)
    return module


producer = _load_producer()


class _BinaryStdout:
    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, value):
        raise AssertionError("producer stdout must be explicit UTF-8 bytes")

    def flush(self):
        self.buffer.flush()


def _payload_identity(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _fd_is_closed(fd):
    try:
        os.fstat(fd)
    except OSError as error:
        return error.errno == errno.EBADF
    return False


def _open_mutation_writer(path):
    """Attempt a real write handle; preserve Win32 sharing-error identity."""
    if os.name != "nt":
        return os.open(path, os.O_WRONLY)

    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # GENERIC_WRITE; share read/write/delete; OPEN_EXISTING; no truncation.
    handle = create(str(path), 0x40000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(
            handle, os.O_WRONLY | os.O_BINARY | getattr(os, "O_NOINHERIT", 0),
        )
    except BaseException:
        close(handle)
        raise


def _fifo_swap_probe(arguments):
    """A bounded subprocess contains the open-after-lstat FIFO regression."""
    root, payload = Path(arguments[0]), Path(arguments[1])
    token = arguments[2]
    retained = payload.with_name("fifo-original.bin")
    original_open = producer.owned_files._open_publication_reader
    opened = []
    swapped = False

    def swap_then_open(path, label):
        nonlocal swapped
        if path != payload or swapped:
            raise AssertionError("unexpected publication-reader invocation")
        path.rename(retained)
        os.mkfifo(path)
        swapped = True
        fd = original_open(path, label)
        opened.append(fd)
        return fd

    output, errors = _BinaryStdout(), io.StringIO()
    with (
        mock.patch.object(
            producer.owned_files, "_open_publication_reader",
            side_effect=swap_then_open,
        ),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        result = producer.main([
            "--repo-root", str(root), "--arch", "x64",
            "--payload", str(payload),
        ])
    print(json.dumps({
        "token": token,
        "swapped": swapped,
        "reader_count": len(opened),
        "fds_closed": bool(opened) and all(_fd_is_closed(fd) for fd in opened),
        "returncode": result,
        "stdout": output.buffer.getvalue().decode("utf-8"),
        "stderr": errors.getvalue(),
    }, sort_keys=True))
    return 0


class DistributionBuildRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir=RUNTIME_ROOT, prefix="mw-distribution-build-",
        )
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name).resolve(strict=True)
        self.root = self.scratch / "consumer"
        self.fixture = write_distribution_fixture(self.root, status="unresolved")
        self.payload = self.root / "main.bin"
        self.payload_bytes = self.fixture["payload_bytes"]
        self.payload.write_bytes(self.payload_bytes)
        self.working = self.scratch / "unrelated-cwd"
        self.working.mkdir()
        (self.working / "SOURCE_LOCK.json").write_bytes(b"not the source root\n")
        for name in ("source_lock_admission.py", "nsis_owned_files.py"):
            (self.working / name).write_text(
                'raise RuntimeError("untrusted import path was used")\n',
                encoding="utf-8",
            )
        self.tooling = self.root / "tooling"
        self.tooling.mkdir()
        for name in (
            "distribution_build_record.py", "source_lock_admission.py",
            "nsis_owned_files.py",
        ):
            (self.tooling / name).write_bytes((TOOLING / name).read_bytes())
        self.script = self.tooling / SCRIPT.name
        runtime = self.scratch / "tmp"
        runtime.mkdir()
        self.environment = dict(os.environ)
        for name in (
            "MW_NSIS_DISTRIBUTION_ADMISSION",
            "MW_NSIS_DISTRIBUTION_ADMISSION_SHA256",
            "MW_NSIS_PREDECESSOR_MANIFEST",
            "MW_NSIS_PREDECESSOR_MANIFEST_SHA256",
        ):
            self.environment.pop(name, None)
        self.environment.update({
            "TEMP": str(runtime), "TMP": str(runtime), "TMPDIR": str(runtime),
            "MOTIONWRIGHT_TEST_ROOT": str(runtime),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self.working),
        })

    def arguments(self, *, arch="x64", root=None, payload=None):
        return [
            "--repo-root", str(self.root if root is None else root),
            "--arch", arch,
            "--payload", str(self.payload if payload is None else payload),
        ]

    def cli(self, arguments=None):
        return subprocess.run(
            [sys.executable, "-B", str(self.script),
             *(self.arguments() if arguments is None else arguments)],
            cwd=self.working, env=self.environment, capture_output=True,
            timeout=30, check=False,
        )

    def consumer_cli(self, stem, *, disposition=True):
        capture = self.root / (stem + ".capture.bin")
        include = self.root / (stem + ".nsh")
        environment = dict(self.environment)
        if disposition:
            environment.update({
                "MW_NSIS_DISTRIBUTION_ADMISSION": self.fixture["record_path"],
                "MW_NSIS_DISTRIBUTION_ADMISSION_SHA256":
                    self.fixture["record_sha256"],
            })
        result = subprocess.run([
            sys.executable, "-B", str(self.tooling / "nsis_owned_files.py"),
            "--source", str(self.payload), "--capture", str(capture),
            "--include", str(include), "--symbol", "MW_MAIN",
            "--prepare-distribution", "--arch", "x64",
        ], cwd=self.working, env=environment, capture_output=True,
            timeout=30, check=False)
        return result, capture, include

    def set_payload(self, data):
        self.payload.write_bytes(data)
        self.payload_bytes = data

    @contextmanager
    def read_hook(self, action=None):
        original_open = producer.owned_files._open_publication_reader
        original_read = os.read
        seen = {"fds": [], "reads": [], "fired": False}

        def opened(path, label):
            fd = original_open(path, label)
            seen["fds"].append(fd)
            return fd

        def read(fd, maximum):
            block = original_read(fd, maximum)
            if fd in seen["fds"]:
                seen["reads"].append((maximum, len(block)))
                if block and not seen["fired"]:
                    seen["fired"] = True
                    if action is not None:
                        action(fd, block)
            return block

        with (
            mock.patch.object(
                producer.owned_files, "_open_publication_reader",
                side_effect=opened,
            ),
            mock.patch.object(producer.os, "read", side_effect=read),
        ):
            yield seen

    def assert_closed(self, seen):
        self.assertEqual(len(seen["fds"]), 1)
        self.assertTrue(all(_fd_is_closed(fd) for fd in seen["fds"]),
                        "retained payload descriptor leaked")

    def call_during_read(self, action):
        record, refusal = None, None
        with self.read_hook(action) as seen:
            try:
                record = producer.distribution_build_record(
                    self.root, arch="x64", payload=self.payload,
                )
            except producer.DistributionBuildRecordError as error:
                refusal = str(error)
        self.assert_closed(seen)
        self.assertTrue(seen["fired"], "the real payload read was not reached")
        return record, refusal, seen

    def make_symlink(self, target, link, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if error.errno in {
                errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP,
            } or getattr(error, "winerror", None) == 1314:
                self.skipTest("real symlink creation is unavailable")
            raise

    def assert_write_mutation(self, kind):
        self.set_payload(LARGE_PAYLOAD)
        original = self.payload_bytes
        before = self.payload.lstat()
        observed = {}

        def mutate(payload_fd, block):
            observed["change_before"] = (
                producer.owned_files._source_change_time(payload_fd)
            )
            try:
                writer = _open_mutation_writer(self.payload)
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) in {32, 33}:
                    observed["blocked"] = error.winerror
                    return
                raise
            try:
                if kind == "growth":
                    os.lseek(writer, 0, os.SEEK_END)
                    self.assertEqual(os.write(writer, b"real appended bytes\n"), 20)
                elif kind == "truncate":
                    os.ftruncate(writer, len(original) // 2)
                elif kind == "same_size":
                    os.lseek(writer, 0, os.SEEK_SET)
                    self.assertEqual(os.write(writer, b"Q" * 32), 32)
                else:
                    raise AssertionError("unknown mutation")
                os.fsync(writer)
            finally:
                os.close(writer)
            if kind == "same_size":
                os.utime(self.payload, ns=(before.st_atime_ns, before.st_mtime_ns))
            observed["mutated"] = True
            observed["change_after"] = (
                producer.owned_files._source_change_time(payload_fd)
            )

        record, refusal, _ = self.call_during_read(mutate)
        actual = self.payload.read_bytes()
        if "blocked" in observed:
            self.assertEqual(os.name, "nt")
            self.assertIn(observed["blocked"], {32, 33})
            self.assertEqual(actual, original)
            current = self.payload.lstat()
            self.assertEqual(
                (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns),
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
            )
            self.assertIsNone(refusal)
            self.assertEqual(record["payload"], _payload_identity(original))
            print(f"{self.id()}: Windows prevented {kind}; "
                  f"winerror={observed['blocked']}", file=sys.stderr)
            return

        self.assertTrue(observed.get("mutated"), "no mutation was demonstrated")
        self.assertIsNone(record)
        self.assertEqual(refusal, REFUSAL)
        if kind == "growth":
            self.assertEqual(actual, original + b"real appended bytes\n")
        elif kind == "truncate":
            self.assertEqual(actual, original[:len(original) // 2])
        else:
            self.assertEqual(actual, b"Q" * 32 + original[32:])
            self.assertNotEqual(actual, original)
            self.assertEqual(len(actual), len(original))
            self.assertEqual(self.payload.lstat().st_mtime_ns, before.st_mtime_ns)
            self.assertNotEqual(
                observed["change_before"], observed["change_after"],
                "the filesystem did not expose the real metadata change",
            )
        print(f"{self.id()}: detected real {kind} mutation", file=sys.stderr)

    def test_real_cli_is_deterministic_read_only_and_independent_of_cwd(self):
        self.set_payload(LARGE_PAYLOAD)
        before = distribution_fixture_snapshot(self.scratch)
        environment_before = dict(os.environ)
        for arch in ("x64", "arm64", "x86"):
            with self.subTest(arch=arch):
                first = self.cli(self.arguments(arch=arch))
                second = self.cli(self.arguments(arch=arch))
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(first.stderr, b"")
                self.assertEqual(second.stderr, b"")
                self.assertEqual(first.stdout, second.stdout)
                record = json.loads(first.stdout.decode("utf-8"))
                self.assertEqual(set(record), {"schema", "source", "scope", "payload"})
                self.assertEqual(record["schema"], "motionwright.distribution-build/1")
                self.assertEqual(record["source"], self.fixture["document"]["source"])
                self.assertEqual(record["scope"], {
                    "action": "prepare-distribution",
                    "component": "motionwright.desktop.main",
                    "destination_class": "windows.nsis.capture-include",
                    "arch": arch,
                    "policy": "motionwright.nsis-main-distribution/1",
                })
                self.assertEqual(record["payload"], _payload_identity(self.payload_bytes))
                self.assertEqual(first.stdout, (
                    json.dumps(record, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False) + "\n"
                ).encode("utf-8"))
        with self.read_hook() as seen:
            direct = producer.distribution_build_record(
                self.root, arch="x64", payload=self.payload,
            )
        self.assert_closed(seen)
        self.assertTrue(seen["fired"])
        self.assertGreaterEqual(len(seen["reads"]), 2)
        self.assertTrue(all(0 < count <= producer.owned_files.COPY_CHUNK_BYTES
                            for count, _ in seen["reads"]))
        self.assertEqual(sum(count for _, count in seen["reads"]),
                         len(self.payload_bytes))
        self.assertEqual(direct["payload"], _payload_identity(self.payload_bytes))
        self.assertEqual(dict(os.environ), environment_before)
        self.assertEqual(distribution_fixture_snapshot(self.scratch), before)

    def test_invalid_source_refuses_before_opening_payload(self):
        for case in ("missing_lock", "unverified", "changed_evidence"):
            with self.subTest(case=case):
                root = self.scratch / case
                fixture = write_distribution_fixture(root)
                payload = root / "main.bin"
                payload.write_bytes(fixture["payload_bytes"])
                lock_path = root / "SOURCE_LOCK.json"
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
                entry = lock["dependencies"]["semwright"]
                if case == "missing_lock":
                    lock_path.unlink()
                elif case == "unverified":
                    entry["verification"]["status"] = "unresolved"
                    lock_path.write_text(json.dumps(lock), encoding="utf-8")
                else:
                    evidence = root / entry["verification"]["evidence"]["path"]
                    with evidence.open("ab") as writer:
                        writer.write(b"\nreal changed evidence\n")
                before = distribution_fixture_snapshot(self.scratch)
                with mock.patch.object(
                    producer.owned_files, "_open_publication_reader",
                    wraps=producer.owned_files._open_publication_reader,
                ) as opened:
                    with self.assertRaises(producer.DistributionBuildRecordError) as failure:
                        producer.distribution_build_record(root, arch="x64", payload=payload)
                self.assertEqual(str(failure.exception), REFUSAL)
                opened.assert_not_called()
                result = self.cli(self.arguments(root=root, payload=payload))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, (REFUSAL + os.linesep).encode())
                self.assertEqual(distribution_fixture_snapshot(self.scratch), before)

    def test_cli_argument_errors_are_static_and_help_uses_stderr(self):
        private = str(self.scratch / "private-argument-marker")
        cases = (
            [],
            self.arguments(arch=private),
            [*self.arguments(), "--output", private],
            ["--repo-root", private, "--arch", "x64"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.cli(arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, (REFUSAL + os.linesep).encode())
                self.assertNotIn(private.encode(), result.stderr)
        help_result = self.cli(["--help"])
        self.assertEqual(help_result.returncode, 0)
        self.assertEqual(help_result.stdout, b"")
        self.assertIn(b"--repo-root", help_result.stderr)

    def test_zero_directory_and_bounded_size_refuse_before_open(self):
        empty = self.root / "empty.bin"
        empty.touch()
        directory = self.root / "directory.bin"
        directory.mkdir()
        for payload in (empty, directory):
            with self.subTest(payload=payload.name):
                with mock.patch.object(
                    producer.owned_files, "_open_publication_reader",
                    wraps=producer.owned_files._open_publication_reader,
                ) as opened:
                    with self.assertRaises(producer.DistributionBuildRecordError):
                        producer.distribution_build_record(
                            self.root, arch="x64", payload=payload,
                        )
                opened.assert_not_called()
        # Exercise the real size boundary without allocating a multi-GiB file.
        with mock.patch.object(
            producer.admission, "MAX_DISTRIBUTION_PAYLOAD_BYTES",
            len(self.payload_bytes) - 1,
        ), mock.patch.object(
            producer.owned_files, "_open_publication_reader",
            wraps=producer.owned_files._open_publication_reader,
        ) as opened:
            with self.assertRaises(producer.DistributionBuildRecordError):
                producer.distribution_build_record(
                    self.root, arch="x64", payload=self.payload,
                )
            opened.assert_not_called()
        with mock.patch.object(
            producer.admission, "MAX_DISTRIBUTION_PAYLOAD_BYTES",
            len(self.payload_bytes),
        ):
            record = producer.distribution_build_record(
                self.root, arch="x64", payload=self.payload,
            )
        self.assertEqual(record["payload"], _payload_identity(self.payload_bytes))

    def test_real_leaf_and_parent_symlinks_are_rejected(self):
        leaf = self.root / "linked.bin"
        self.make_symlink(self.payload, leaf)
        target_directory = self.root / "media"
        target_directory.mkdir()
        target = target_directory / "main.bin"
        target.write_bytes(self.payload_bytes)
        parent = self.root / "linked-directory"
        self.make_symlink(target_directory, parent, directory=True)
        for path in (leaf, parent / "main.bin"):
            with self.subTest(path=path.name):
                with mock.patch.object(
                    producer.owned_files, "_open_publication_reader",
                    wraps=producer.owned_files._open_publication_reader,
                ) as opened:
                    with self.assertRaises(producer.DistributionBuildRecordError):
                        producer.distribution_build_record(
                            self.root, arch="x64", payload=path,
                        )
                opened.assert_not_called()
        self.assertTrue(leaf.is_symlink())
        self.assertTrue(parent.is_symlink())
        self.assertEqual(self.payload.read_bytes(), self.payload_bytes)
        self.assertEqual(target.read_bytes(), self.payload_bytes)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "POSIX FIFO only")
    def test_fifo_substitution_after_lstat_refuses_without_blocking(self):
        token = uuid.uuid4().hex
        result = subprocess.run([
            sys.executable, "-B", str(TEST_FILE), "--fifo-swap-probe",
            str(self.root), str(self.payload), token,
        ], cwd=self.working, env=self.environment, capture_output=True,
            timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(report, {
            "token": token, "swapped": True, "reader_count": 1,
            "fds_closed": True, "returncode": 2, "stdout": "",
            "stderr": REFUSAL + "\n",
        })
        self.assertTrue(stat.S_ISFIFO(self.payload.lstat().st_mode))
        self.assertEqual(
            self.payload.with_name("fifo-original.bin").read_bytes(),
            self.payload_bytes,
        )
        # Never read or recursively snapshot the FIFO.

    def test_cargo_mutation_during_read_produces_no_stdout_record(self):
        cargo = self.root / "Cargo.lock"
        before = cargo.read_bytes()
        context = producer.admission.distribution_source_context(self.root)

        def change_cargo(fd, block):
            with cargo.open("ab") as writer:
                writer.write(b"# real change during payload observation\n")
                writer.flush()
                os.fsync(writer.fileno())

        output, errors = _BinaryStdout(), io.StringIO()
        with (
            self.read_hook(change_cargo) as seen,
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            code = producer.main(self.arguments())
        self.assert_closed(seen)
        self.assertTrue(seen["fired"])
        self.assertEqual(code, 2)
        self.assertEqual(output.buffer.getvalue(), b"")
        self.assertEqual(errors.getvalue(), REFUSAL + "\n")
        self.assertEqual(cargo.read_bytes(),
                         before + b"# real change during payload observation\n")
        self.assertNotEqual(
            producer.admission.distribution_source_context(self.root), context,
        )

    def test_real_payload_growth_is_detected_or_prevented_by_windows(self):
        self.assert_write_mutation("growth")

    def test_real_payload_truncation_is_detected_or_prevented_by_windows(self):
        self.assert_write_mutation("truncate")

    def test_same_size_overwrite_with_restored_mtime_is_detected_or_prevented(self):
        self.assert_write_mutation("same_size")

    def test_real_same_bytes_path_replacement_is_detected_or_prevented(self):
        self.set_payload(LARGE_PAYLOAD)
        original = self.payload_bytes
        before = self.payload.lstat()
        replacement = self.root / "replacement.bin"
        replacement.write_bytes(original)
        replacement_identity = replacement.lstat()
        observed = {}

        def replace(fd, block):
            try:
                os.replace(replacement, self.payload)
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) in {5, 32, 33}:
                    observed["blocked"] = error.winerror
                    return
                raise
            observed["replaced"] = True

        record, refusal, _ = self.call_during_read(replace)
        self.assertEqual(self.payload.read_bytes(), original)
        after = self.payload.lstat()
        if "blocked" in observed:
            self.assertEqual(os.name, "nt")
            self.assertIn(observed["blocked"], {5, 32, 33})
            self.assertEqual((after.st_dev, after.st_ino),
                             (before.st_dev, before.st_ino))
            self.assertEqual(replacement.read_bytes(), original)
            replacement_after = replacement.lstat()
            self.assertEqual(
                (replacement_after.st_dev, replacement_after.st_ino),
                (replacement_identity.st_dev, replacement_identity.st_ino),
            )
            self.assertIsNone(refusal)
            self.assertEqual(record["payload"], _payload_identity(original))
            # call_during_read already proved the retained descriptor closed.
            # The same operation must now succeed, without recreating either
            # file or changing permissions. An unrelated ACL refusal fails.
            os.replace(replacement, self.payload)
            self.assertFalse(replacement.exists())
            released = self.payload.lstat()
            self.assertEqual(
                (released.st_dev, released.st_ino),
                (replacement_identity.st_dev, replacement_identity.st_ino),
            )
            self.assertNotEqual(
                (released.st_dev, released.st_ino), (before.st_dev, before.st_ino),
            )
            self.assertEqual(self.payload.read_bytes(), original)
            print(f"{self.id()}: Windows prevented replacement while retained "
                  f"and allowed it after close; winerror={observed['blocked']}",
                  file=sys.stderr)
        else:
            self.assertTrue(observed.get("replaced"))
            self.assertFalse(replacement.exists())
            self.assertEqual((after.st_dev, after.st_ino),
                             (replacement_identity.st_dev, replacement_identity.st_ino))
            self.assertNotEqual((after.st_dev, after.st_ino),
                                (before.st_dev, before.st_ino))
            self.assertIsNone(record)
            self.assertEqual(refusal, REFUSAL)

    def test_read_and_final_change_time_errors_close_fd_and_emit_no_json(self):
        for failure in ("read", "change_time"):
            with self.subTest(failure=failure):
                output, errors = _BinaryStdout(), io.StringIO()
                original_change_time = producer.owned_files._source_change_time
                change_calls = []

                def change_time(fd):
                    value = original_change_time(fd)
                    change_calls.append(fd)
                    if failure == "change_time" and len(change_calls) == 2:
                        raise OSError(errno.EIO, "private injected metadata failure",
                                      str(self.payload))
                    return value

                def read_fault(fd, block):
                    if failure == "read":
                        raise OSError(errno.EIO, "private injected read failure",
                                      str(self.payload))

                with (
                    self.read_hook(read_fault) as seen,
                    mock.patch.object(
                        producer.owned_files, "_source_change_time",
                        side_effect=change_time,
                    ),
                    redirect_stdout(output),
                    redirect_stderr(errors),
                ):
                    code = producer.main(self.arguments())
                self.assert_closed(seen)
                self.assertTrue(seen["fired"], "no real read preceded the fault")
                if failure == "change_time":
                    self.assertEqual(len(change_calls), 2)
                self.assertEqual(code, 2)
                self.assertEqual(output.buffer.getvalue(), b"")
                self.assertEqual(errors.getvalue(), REFUSAL + "\n")
                self.assertEqual(self.payload.read_bytes(), self.payload_bytes)

    def test_interrupt_and_programming_error_remain_visible_and_close_fd(self):
        for exception in (KeyboardInterrupt, RuntimeError):
            with self.subTest(exception=exception.__name__):
                def interrupt(fd, block):
                    raise exception("injected after a real read")

                with self.read_hook(interrupt) as seen:
                    with self.assertRaises(exception):
                        producer.distribution_build_record(
                            self.root, arch="x64", payload=self.payload,
                        )
                self.assert_closed(seen)
                self.assertTrue(seen["fired"])
                self.assertEqual(self.payload.read_bytes(), self.payload_bytes)

    def test_emitted_bytes_interoperate_with_explicit_synthetic_permission(self):
        self.fixture = write_distribution_fixture(self.root, status="permitted")
        preloaded_identity = dict(self.fixture["document"]["payload"])
        self.set_payload(self.payload_bytes + b"distinct producer-observed fixture bytes\n")
        expected = _payload_identity(self.payload_bytes)
        self.assertNotEqual(expected, preloaded_identity)
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        record = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(record["payload"], expected)
        self.assertEqual(set(record), {"schema", "source", "scope", "payload"})

        # Custody is permitted only after exit 0. Preserve stdout bytes exactly;
        # this replaces the fixture's preloaded build record and its pin.
        reference = write_distribution_reference(
            self.root, "evidence/distribution-build.json", result.stdout,
        )
        self.fixture["document"]["payload"] = expected
        self.fixture["document"]["build_provenance"] = reference
        pin_distribution_fixture(self.root, self.fixture)
        self.assertEqual(
            (self.root / reference["path"]).read_bytes(), result.stdout,
        )
        self.assertEqual(reference["sha256"], hashlib.sha256(result.stdout).hexdigest())
        before = distribution_fixture_snapshot(self.root)
        admitted = producer.admission.verify_distribution_admission(
            self.root, arch="x64", record_path=self.fixture["record_path"],
            record_sha256=self.fixture["record_sha256"],
        )
        self.assertEqual(admitted, expected)
        self.assertEqual(distribution_fixture_snapshot(self.root), before)
        consumed, capture, include = self.consumer_cli("synthetic-interop")
        self.assertEqual(consumed.returncode, 0, consumed.stderr)
        self.assertEqual(capture.read_bytes(), self.payload_bytes)
        self.assertIn(expected["sha256"], include.read_text(encoding="utf-8-sig"))
        after = distribution_fixture_snapshot(self.root)
        self.assertEqual(set(after) - set(before), {
            capture.relative_to(self.root).as_posix(),
            include.relative_to(self.root).as_posix(),
        })
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(self.fixture["document"]["review"]["issuer"],
                         "synthetic-test-fixture-not-an-authority")

    def test_observation_does_not_override_negative_or_missing_admission(self):
        observed = self.cli()
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertEqual(observed.stderr, b"")
        record = json.loads(observed.stdout.decode("utf-8"))
        self.assertEqual(set(record), {"schema", "source", "scope", "payload"})
        write_distribution_reference(
            self.root, "evidence/observation-without-authority.json", observed.stdout,
        )
        for status in ("unresolved", "prohibited", "missing"):
            with self.subTest(status=status):
                document = self.fixture["document"]
                document["status"] = "unresolved" if status == "missing" else status
                document["payload"] = None
                document["build_provenance"] = None
                pin_distribution_fixture(self.root, self.fixture)
                before = distribution_fixture_snapshot(self.scratch)
                result, capture, include = self.consumer_cli(
                    "negative-" + status, disposition=status != "missing",
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(("distribution_admission_" + status).encode(),
                              result.stderr)
                self.assertFalse(capture.exists())
                self.assertFalse(include.exists())
                self.assertEqual(distribution_fixture_snapshot(self.scratch), before)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fifo-swap-probe":
        raise SystemExit(_fifo_swap_probe(sys.argv[2:]))
    unittest.main()
