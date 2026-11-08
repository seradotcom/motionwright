"""Review-original I/O with platform-native retained-file custody checks."""
import os
from pathlib import Path

import ci_private_evidence as seal


def _require(value, reason):
    if not value:
        raise ValueError('REVIEW_ORIGINAL_' + reason)


def _windows_path(path):
    import nsis_owned_files as files
    path = Path(path)
    _require(path.is_absolute() and path.parent.resolve(strict=True) == path.parent,
             'CANONICAL_PARENT')
    files._check_path_syntax(str(path))
    files._require_plain_parents(path)
    return path


def _stamp(info):
    return tuple(getattr(info, name, None) for name in (
        'st_dev', 'st_ino', 'st_size', 'st_mode', 'st_nlink', 'st_uid', 'st_gid',
        'st_mtime_ns', 'st_ctime_ns', 'st_birthtime_ns', 'st_file_attributes'))


def _read_windows(path, limit, expected=None):
    import nsis_owned_files as files
    path = _windows_path(path)
    _require(type(limit) is int and 0 <= limit <= seal.MAX_FILE, 'BYTE_LIMIT')
    _require(path.resolve(strict=True) == path, 'CANONICAL_INPUT')
    namespace = path.lstat()
    files._require_regular(namespace, 'review original')
    fd = files._open_publication_reader(path, 'review original')
    try:
        before = os.fstat(fd)
        files._require_regular(before, 'review original')
        _require(before.st_nlink == 1 and 0 <= before.st_size <= limit, 'BOUNDED_SINGLE_FILE')
        files._require_path_descriptor_match(before, namespace, 'review original')
        if expected is not None:
            _require((before.st_dev, before.st_ino, before.st_size) == expected,
                     'CREATED_OBJECT_CHANGED')
        changed = files._source_change_time(fd)
        chunks, count = [], 0
        while count <= before.st_size:
            block = os.read(fd, min(65536, before.st_size + 1 - count))
            if not block:
                break
            chunks.append(block)
            count += len(block)
        after = os.fstat(fd)
        namespace_after = path.lstat()
        # Descriptor ctime and pathname ctime can represent different Windows
        # timestamps. Preserve each complete stamp within its own domain.
        _require(_stamp(before) == _stamp(after), 'DESCRIPTOR_CHANGED')
        _require(_stamp(namespace) == _stamp(namespace_after), 'NAMESPACE_CHANGED')
        files._require_path_descriptor_match(after, namespace_after, 'review original')
        _require(files._source_change_time(fd) == changed, 'CHANGE_TIME_CHANGED')
        files._require_plain_parents(path)
        _require(count == before.st_size, 'READ_SIZE')
        return b''.join(chunks)
    finally:
        os.close(fd)


def read_bytes(path, limit=seal.MAX_FILE):
    """Read one bounded original without changing producer capture semantics."""
    if os.name != 'nt':
        return seal.regular(path, limit)[0]
    return _read_windows(path, limit)


def new_bytes(path, data, *, owner=None, mode=0o600):
    """Create an original once and verify its exact bytes and created identity."""
    if os.name != 'nt':
        return seal.new_bytes(path, data, owner=owner, mode=mode)
    _require(owner is None and isinstance(data, bytes) and len(data) <= seal.MAX_FILE,
             'WRITE_INPUT')
    path = _windows_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY
                 | getattr(os, 'O_NOINHERIT', 0), mode)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, memoryview(data)[offset:])
            _require(written > 0, 'SHORT_WRITE')
            offset += written
        os.fsync(fd)
        written = os.fstat(fd)
        created = (written.st_dev, written.st_ino, written.st_size)
    finally:
        # Final write times can settle at close. The later read handle excludes
        # shared writers/deletion and must still identify this created object.
        os.close(fd)
    _require(_read_windows(path, seal.MAX_FILE, created) == data, 'WRITE_READBACK')


def new_json(path, value, **kwargs):
    """Serialize originals identically to the shared authenticated producer."""
    if os.name != 'nt':
        return seal.new_json(path, value, **kwargs)
    return new_bytes(path, seal.json_bytes(value), **kwargs)
