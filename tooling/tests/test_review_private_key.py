"""Protected private-key bytes from real filesystem custody."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def protect_fixture(path):
    """Set a real protected user-only Windows DACL for an inert fixture."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    token = wintypes.HANDLE()
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    assert adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token))
    try:
        size = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        assert adv.GetTokenInformation(token, 1, buf, size, ctypes.byref(size))
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents.value
        text = wintypes.LPWSTR()
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        assert adv.ConvertSidToStringSidW(sid, ctypes.byref(text))
        identity = text.value
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)
    # Elevated Windows accounts may create files owned by the Administrators group.
    # Establish the actual user as owner instead of accepting a broader owner.
    owner = subprocess.run(['icacls', str(path), '/setowner', '*' + identity], capture_output=True)
    assert owner.returncode == 0, owner.stderr
    result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + identity + ':F'], capture_output=True)
    assert result.returncode == 0, result.stderr


class ProtectedKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='protected-key-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'inert.pem'
        self.raw = b'inert fixture bytes\n' * 100
        self.path.write_bytes(self.raw)
        if os.name == 'nt':
            protect_fixture(self.path)
        else:
            self.path.chmod(0o600)

    def read(self):
        from review_private_key import read_private_key_bytes
        return read_private_key_bytes(self.path)

    def test_protected_file_reads_exact_bytes(self):
        try:
            actual = self.read()
        except ValueError as error:
            if os.name != 'nt':
                raise
            failure = error.__traceback__
            while failure and failure.tb_frame.f_code.co_name != '_windows_security':
                failure = failure.tb_next
            observed = {} if failure is None else failure.tb_frame.f_locals
            self.fail(str(error) + '; expected fixture SID: ' + str(observed.get('user'))
                      + '; held-handle descriptor: ' + str(observed.get('actual')))
        self.assertEqual(actual, self.raw)

    def test_hardlink_is_rejected(self):
        os.link(self.path, self.path.with_suffix('.link'))
        with self.assertRaises(ValueError): self.read()

    def test_broad_permissions_are_rejected(self):
        if os.name == 'nt':
            result = subprocess.run(['icacls', str(self.path), '/grant', '*S-1-1-0:R'], capture_output=True)
            self.assertEqual(result.returncode, 0)
        else:
            self.path.chmod(0o644)
        with self.assertRaises(ValueError): self.read()

    def test_short_file_is_rejected(self):
        self.path.write_bytes(b'short')
        with self.assertRaises(ValueError): self.read()

    @unittest.skipUnless(os.name == 'posix', 'POSIX named pipe')
    def test_named_pipe_is_rejected_without_waiting_for_writer(self):
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        with self.assertRaises(ValueError): self.read()

    def test_directory_reparse_is_rejected(self):
        target = self.path.parent / 'real'; target.mkdir()
        source = target / 'key'; source.write_bytes(self.raw)
        if os.name == 'nt':
            protect_fixture(source)
            alias = self.path.parent / 'alias'
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0)
        else:
            source.chmod(0o600)
            alias = self.path.parent / 'alias'; alias.symlink_to(target, target_is_directory=True)
        self.path = alias / 'key'
        with self.assertRaises(ValueError): self.read()


if __name__ == '__main__': unittest.main()
