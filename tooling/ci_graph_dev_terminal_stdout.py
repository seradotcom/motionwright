"""Portable, bounded GN1 stdout EOF observation after a child exits.

Windows select.select accepts sockets, not subprocess pipes. Windows
PeekNamedPipe and POSIX select avoid both WinError 10038 and blocking
cross-thread fd close on a pipe inherited by another process. Never
claim success without a real EOF from the owned stream.
"""
from __future__ import annotations

import os
import select
import time


def _windows_peek(fd: int) -> tuple[int, bool]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    probe = kernel.PeekNamedPipe
    probe.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    probe.restype = wintypes.BOOL
    available = wintypes.DWORD()
    handle = msvcrt.get_osfhandle(fd)
    if probe(wintypes.HANDLE(handle), None, 0, None,
             ctypes.byref(available), None):
        return available.value, False
    code = ctypes.get_last_error()
    if code in (109, 232, 233):  # broken pipe / no data / not connected
        return 0, True
    raise OSError(code, "GN1_WIN_PIPE_PEEK_UNAVAILABLE")


def read_live_stdout(pipe, *, maximum: int, wait_seconds: float) -> bytes | None:
    """One bounded chunk from a live child pipe.

    Return None only when the deadline expires with no observed bytes, b""
    only for actual EOF. Never call select() on a Windows subprocess pipe.
    This does not consume more than maximum bytes, or close a live writer.
    """
    if (type(maximum) is not int or not 0 < maximum <= 65536
            or type(wait_seconds) not in (int, float)
            or not 0 < wait_seconds <= 120.0
            or not hasattr(pipe, "fileno")):
        raise ValueError("GN1_LIVE_STDOUT_READER_CONTRACT")
    deadline = time.monotonic() + wait_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if os.name == "nt":
            available, eof = _windows_peek(pipe.fileno())
            if eof:
                return b""
            if available <= 0:
                time.sleep(min(0.01, remaining))
                continue
            count = min(maximum, available)
        else:
            readable, _, _ = select.select([pipe], [], [], remaining)
            if not readable:
                return None
            count = maximum
        return os.read(pipe.fileno(), count)


def read_terminal_stdout(pipe, *, maximum: int, wait_seconds: float = 1.0) -> dict:
    if (type(maximum) is not int or maximum <= 0
            or type(wait_seconds) not in (int, float)
            or not 0 < wait_seconds <= 5.0
            or not hasattr(pipe, "fileno") or not hasattr(pipe, "close")):
        raise ValueError("GN1_TERMINAL_STDOUT_READER_CONTRACT")

    kept = bytearray()
    eof = False
    over_cap = False
    read_error = False
    deadline_exceeded = False
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                deadline_exceeded = True
                break
            if os.name == "nt":
                available, eof = _windows_peek(pipe.fileno())
                if eof:
                    break
                if available == 0:
                    time.sleep(min(0.01, remaining))
                    continue
                length = min(65536, available, maximum + 1 - len(kept))
            else:
                readable, _, _ = select.select([pipe], [], [], remaining)
                if not readable:
                    deadline_exceeded = True
                    break
                length = min(65536, maximum + 1 - len(kept))
            chunk = os.read(pipe.fileno(), length)
            if not chunk:
                eof = True
                break
            kept.extend(chunk)
            if len(kept) > maximum:
                over_cap = True
                break
    except (OSError, ValueError):
        read_error = True
    finally:
        # All reads happen on this same thread. No unsafe cross-thread
        # os.close while Windows ReadFile is pending.
        try:
            pipe.close()
        except (OSError, ValueError):
            read_error = True

    return {
        "raw": bytes(kept),
        "eof": eof and not deadline_exceeded and not read_error,
        "over_cap": over_cap,
        "read_error": read_error,
        "reader_finished": True,
        "deadline_exceeded": deadline_exceeded,
    }
