"""Causal stdlib/exec/clock controls. No mount or cgroup/Native G2 is claimed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLING = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_resources_focal", TOOLING / "native_owner_resources.py")
resource = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource)


class SharedReleaseTests(unittest.TestCase):
    def test_only_closed_global_release_uses_fresh_system_python_without_site(self):
        for scope, job in (("hero", "hero-profiles"), ("fixedtxt", "fixedtxt-session-owner")):
            origin = {"source_root": "/absent/source", "base_temp": "/absent/temp",
                      "context": {"run_id": "123"}, "job": job}
            command = resource.selected_command(scope, "release-shared", None, origin, {})
            self.assertEqual(command, ["/usr/bin/python3.12", "-B", "-E", "-s", "-S",
                "/absent/source/tooling/native_owner_resources.py", "release", "--scope", scope])
            with self.assertRaises(resource.ResourceRejected):
                resource.selected_command(scope, "release-shared", 0, origin, {})
        origin["job"] = "fixedtxt-original-owner"
        with self.assertRaises(resource.ResourceRejected):
            resource.selected_command("fixedtxt", "release-shared", None, origin, {})

    def test_release_never_extends_original_clock_or_upload_reserve(self):
        self.assertEqual(resource.release_deadline("hero", 1000, None, 800), 830)
        self.assertEqual(resource.release_deadline("hero", 1000, None, 870), 880)
        self.assertEqual(resource.release_deadline("fixedtxt", 1000, 180, 950), 970)
        self.assertEqual(resource.release_deadline("fixedtxt", 1000, 20, 960), 980)
        for args in (("hero", 1000, None, 880), ("fixedtxt", 1000, 180, 970),
                     ("fixedtxt", 1000, 0, 800), ("hero", float("nan"), None, 800)):
            with self.assertRaises(resource.ResourceRejected):
                resource.release_deadline(*args)

    def test_actual_stdlib_supervisor_retains_positive_and_negative_child_originals(self):
        # A real private process group is sufficient for this IO/stdlib test.
        # It is explicitly not an owner cgroup and cannot grant Native or G2.
        program = r'''
import json, os, signal, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import native_owner_resources as resources
import native_fixedtxt_session
import native_fixedtxt_g2_supervisor as supervisor
assert sys.flags.no_site == 1
assert not any(name == "cryptography" or name.startswith("cryptography.") for name in sys.modules)
class ProcessGroup:
    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        self.pid = None
    def child_enter(self):
        os.write(self.write_fd, str(os.getpid()).encode())
    def populated(self):
        if self.pid is None:
            try: raw = os.read(self.read_fd, 64)
            except BlockingIOError: raw = b""
            if raw: self.pid = int(raw)
        if self.pid is None: return False
        try: os.killpg(self.pid, 0)
        except ProcessLookupError: return False
        return True
    def stop(self):
        if self.populated(): os.killpg(self.pid, signal.SIGKILL)
    def close(self):
        os.close(self.read_fd); os.close(self.write_fd)
class Clock:
    started = time.monotonic()
    total = started + 5
    def reserve(self, phase): return self.total
root = Path(sys.argv[2])
answers = []
for number, code in enumerate((0, 23)):
    path = root / str(number); path.mkdir(mode=0o700)
    group = ProcessGroup()
    try:
        result = supervisor._run_original([sys.executable, "-B", "-E", "-s", "-S", "-c",
            "import sys;sys.stdout.write('original-out');sys.stderr.write('original-err');sys.exit(" + str(code) + ")"],
            environment={"PATH": "/usr/bin:/bin"}, directory=path, phase="release-" + str(number),
            clock=Clock(), containment=group, stream_limit=65536, original_writer=resources.new)
    finally: group.close()
    assert result["exit_code"] == code and result["original_complete"] is True
    assert result["native_outcome"] == "NOT_INFERRED" and result["containment_empty"] is True
    assert (path / ("release-" + str(number) + ".stdout")).read_bytes() == b"original-out"
    assert (path / ("release-" + str(number) + ".stderr")).read_bytes() == b"original-err"
    assert json.loads((path / ("release-" + str(number) + ".terminal.json")).read_bytes())["exit_code"] == code
    answers.append(code)
assert not any(name == "cryptography" or name.startswith("cryptography.") for name in sys.modules)
print(json.dumps({"observed_exit_codes": answers, "stdlib_only": True, "G2": False}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-B", "-E", "-s", "-S", "-c", program,
                                     str(TOOLING), temporary], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout), {"observed_exit_codes": [0, 23], "stdlib_only": True, "G2": False})

    def test_actual_exec_discards_a_mapped_file_before_fresh_python(self):
        # This directly exercises the causal exec boundary. It does not claim
        # that a filesystem bind was unmounted or that Native has run.
        program = r'''
import mmap, os, sys
path = sys.argv[1]
handle = open(path, "r+b")
mapped = mmap.mmap(handle.fileno(), 0)
assert path in open("/proc/self/maps").read()
child = "import os,sys; p=sys.argv[1]; assert p not in open('/proc/self/maps').read(); assert sys.flags.no_site == 1; print('ACTUAL_EXEC_MAPPING_DISCARDED')"
os.execve(sys.executable, [sys.executable, "-B", "-E", "-s", "-S", "-c", child, path], dict(os.environ))
'''
        with tempfile.TemporaryDirectory() as temporary:
            mapped = Path(temporary) / "owned-runtime-library.original"
            mapped.write_bytes(b"x" * 4096)
            result = subprocess.run([sys.executable, "-B", "-E", "-s", "-S", "-c", program, str(mapped)],
                                    capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"ACTUAL_EXEC_MAPPING_DISCARDED\n")


if __name__ == "__main__":
    unittest.main()
