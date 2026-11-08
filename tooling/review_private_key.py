"""Read bounded private-key bytes under actual platform access controls."""
import os
from pathlib import Path
import stat


def require(condition, reason):
    if not condition:
        raise ValueError('PRIVATE_KEY_' + reason)


def _windows_security(fd):
    """Inspect owner and protected DACL on the retained file handle."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    require(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)), 'TOKEN')
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        require(0 < size.value <= 65536, 'TOKEN_SIZE')
        buffer = ctypes.create_string_buffer(size.value)
        require(advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)), 'TOKEN_USER')
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
        text = wintypes.LPWSTR()
        require(advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)), 'TOKEN_SID')
        try:
            user = text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)
    descriptor = ctypes.c_void_p()
    advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    require(advapi.GetSecurityInfo(msvcrt.get_osfhandle(fd), 1, 5, None, None, None, None,
                                  ctypes.byref(descriptor)) == 0, 'SECURITY_INFO')
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    text = wintypes.LPWSTR()
    try:
        require(advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 5,
                    ctypes.byref(text), None), 'SECURITY_DESCRIPTOR')
        actual = text.value
        # Exact SDDL excludes NULL, inherited, foreign, object and callback ACEs.
        # Only the current user's explicit full-control or file-read grant is admitted.
        require(actual in tuple('O:' + user + 'D:' + control + '(A;;' + rights + ';;;' + user + ')'
                    for control in ('P', 'PAI') for rights in ('FA', 'FR')), 'USER_ONLY_DACL')
        return actual
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        kernel.LocalFree(descriptor)


def read_private_key_bytes(path):
    """Read one ordinary owner-protected key without copying or relaxing ACLs."""
    path = Path(path)
    require(path.is_absolute(), 'ABSOLUTE_PATH')
    if os.name == 'nt':
        import nsis_owned_files as files
        try:
            files._check_path_syntax(str(path))
            files._require_plain_parents(path)
            fd = files._open_publication_reader(path, 'private key')
        except (OSError, files.CaptureError) as error:
            raise ValueError('PRIVATE_KEY_PATH') from error
    else:
        require(os.name == 'posix' and hasattr(os, 'geteuid'), 'PLATFORM')
        require(not path.is_symlink() and path.parent.resolve(strict=True) == path.parent, 'PATH')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 1024 < before.st_size <= 16384, 'CUSTODY')
        if os.name == 'nt':
            files._require_regular(before, 'private key')
            files._require_path_descriptor_match(before, path.lstat(), 'private key')
            security = _windows_security(fd)
        else:
            require(before.st_uid == os.geteuid() and before.st_mode & 0o077 == 0, 'CUSTODY')
        raw = os.read(fd, 16385)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and (after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns, after.st_nlink) == (before.st_dev, before.st_ino,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink), 'UNCHANGED')
        if os.name == 'nt':
            files._require_plain_parents(path)
            files._require_path_descriptor_match(after, path.lstat(), 'private key')
            require(_windows_security(fd) == security, 'SECURITY_CHANGED')
        else:
            require(after.st_uid == before.st_uid and after.st_mode == before.st_mode, 'SECURITY_CHANGED')
        return raw
    finally:
        os.close(fd)
