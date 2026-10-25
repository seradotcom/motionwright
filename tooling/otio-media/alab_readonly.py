"""Fixed read-only ALab intake and original ffprobe metadata custody.

The caller owns exact source/tree/recipient admission and the existing outer collector.
No frame decode, edit, NLE, adoption, origin inference or automatic retry.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import math
import os
from pathlib import Path, PurePosixPath
import queue
import re
import socket
import stat
import struct
import subprocess
import threading
import time
from urllib.parse import urlsplit
import zlib

REQUEST_SCHEMA = "motionwright.otio.external-alab-readonly-request/1"
RESULT_SCHEMA = "motionwright.otio.external-alab-readonly-result/1"
MODE = "external-alab-readonly"
JOB_NAME = "admitted-native-kdenlive-gui"
MAX_JSON = 2 * 1024 * 1024
PROBE_SECONDS = 20

ARCHIVE = {'url': 'https://dpel-assets.aswf.io/alab-trailer/animal_logic_alab_trailer_otio_and_media.zip',
 'etag': '"f2a8ead4fcfe846fcaf69708ec024539-69"',
 'bytes': 573418844,
 'full_archive_sha256': None,
 'complete_archive_download_allowed': False,
 'redirects_allowed': False,
 'http_status': 206,
 'content_encoding': 'identity',
 'content_range': 'Exact requested inclusive byte range and total 573418844',
 'url_is_not_a_cli_argument': True}

MEMBERS = [{'name': 'ALab_h264_MOVs/mk020_0010.mov',
  'bytes': 7627921,
  'compressed_bytes': 6584449,
  'crc32': 'cea98e78',
  'local_header_offset': 19271423,
  'compression': 8,
  'sha256': '86c40f8271339a4bab948d11ddbdd172588816df5ff52d056bd4e54378069e1a',
  'retained_path': 'originals/ALab_h264_MOVs/mk020_0010.mov',
  'kind': 'video-original'},
 {'name': 'ALab_h264_MOVs/mk020_0020.mov',
  'bytes': 20227644,
  'compressed_bytes': 19188563,
  'crc32': '6a572d77',
  'local_header_offset': 195242704,
  'compression': 8,
  'sha256': '08aac3e29ea980e80993aa6baaef1b83b4b0598c3ae95685434886a1c326b2f6',
  'retained_path': 'originals/ALab_h264_MOVs/mk020_0020.mov',
  'kind': 'video-original'},
 {'name': 'ALab_mk020_final_edit.h264.otio',
  'bytes': 218949,
  'compressed_bytes': 5664,
  'crc32': '7ab8eb52',
  'local_header_offset': 525749861,
  'compression': 8,
  'sha256': '6c90cbc64000e5de1d6450c95b1f5845db2862c053fa2591d02d370505f3bd28',
  'retained_path': 'originals/ALab_mk020_final_edit.h264.otio',
  'kind': 'original-source-notice'},
 {'name': 'README.md',
  'bytes': 8729,
  'compressed_bytes': 2657,
  'crc32': 'e0a8ec14',
  'local_header_offset': 573403887,
  'compression': 8,
  'sha256': 'e5964177d9826d6f80c3318a01e3404d3bc2f24d8cdd143a4b566b0b3ff295a0',
  'retained_path': 'originals/README.md',
  'kind': 'original-source-notice'},
 {'name': 'ALab_mk020_final_edit_LICENSE.txt',
  'bytes': 2622,
  'compressed_bytes': 1294,
  'crc32': '1321a557',
  'local_header_offset': 271741218,
  'compression': 8,
  'sha256': '4263d9c4ad26b95bda3398c406ef9787f696f236fb88ab509693371983174021',
  'retained_path': 'originals/ALab_mk020_final_edit_LICENSE.txt',
  'kind': 'original-source-notice'}]

LIMITS = {'range_bytes_budget': 33554432,
 'one_request_max': 2097152,
 'max_uncompressed_member_bytes': 25165824,
 'max_media_members': 2,
 'max_text_members': 3,
 'media_range_requests': 18,
 'additional_notice_requests_max': 9,
 'all_range_requests_max': 27,
 'per_range_attempts': 1,
 'per_media_probe_attempts': 1,
 'inner_total_wall_seconds_max': 110,
 'outer_wall_seconds': 120,
 'ffprobe_stdout_bytes_max': 2097152,
 'ffprobe_stderr_bytes_max': 2097152,
 'metadata_only': True}

PROHIBITED_EFFECTS = {'decode': False,
 'reencode': False,
 'trim': False,
 'rename_originals': False,
 'launch_nle': False,
 'render': False,
 'create_canonical_scene_ids': False,
 'adopt_assets': False,
 'apply_owner_cut': False,
 'grant_rights': False,
 'infer_source_origin': False,
 'human_acceptance': False,
 'release_authorized': False}

REQUEST_KEYS = {
    "schema", "context", "source_tree", "job_name", "recipient_spki_sha256",
    "admission_reference", "archive", "members", "limits", "mode",
    "prohibited_effects",
}
CONTEXT_KEYS = {"source_sha", "semwright_sha", "run_id", "run_attempt"}

class Refused(ValueError):
    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record if record is not None else {}

class Unknown(Refused):
    """An uncertain operation must not be automatically retried."""

def require(condition, message):
    if not condition:
        raise Refused(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def strict_object(raw, label):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"Duplicate JSON field in {label}")
            value[key] = item
        return value
    def reject_constant(value):
        raise Refused(f"Non-finite JSON value in {label}: {value}")
    def finite_float(value):
        number = float(value)
        require(math.isfinite(number), f"Non-finite JSON number in {label}")
        return number
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=reject_constant, parse_float=finite_float)
    except (UnicodeError, TypeError, ValueError) as error:
        if isinstance(error, Refused):
            raise
        raise Refused(f"Invalid JSON in {label}") from error
    require(type(value) is dict, f"{label} must be an object")
    return value

def _exact_value(actual, expected, label):
    require(type(actual) is type(expected), f"Wrong type for {label}")
    if type(expected) is dict:
        require(set(actual) == set(expected), f"Wrong fields for {label}")
        for key in expected:
            _exact_value(actual[key], expected[key], f"{label}.{key}")
    elif type(expected) is list:
        require(len(actual) == len(expected), f"Wrong length for {label}")
        for index, item in enumerate(expected):
            _exact_value(actual[index], item, f"{label}[{index}]")
    else:
        require(actual == expected, f"Unsupported {label}")

def validate_request(value):
    require(type(value) is dict and set(value) == REQUEST_KEYS,
            "Request fields are missing or unsupported")
    require(type(value["schema"]) is str and value["schema"] == REQUEST_SCHEMA,
            "Wrong request schema")
    context = value["context"]
    require(type(context) is dict and set(context) == CONTEXT_KEYS,
            "Wrong context fields")
    for key in ("source_sha", "semwright_sha"):
        require(type(context[key]) is str
                and re.fullmatch(r"[a-f0-9]{40}", context[key]) is not None,
                f"Invalid context {key}")
    require(type(context["run_id"]) is str
            and re.fullmatch(r"[1-9][0-9]{0,23}", context["run_id"]) is not None,
            "run_id must be a nonzero decimal string")
    require(type(context["run_attempt"]) is int and context["run_attempt"] == 1,
            "Only exact integer run_attempt 1 is admitted")
    require(type(value["source_tree"]) is str
            and re.fullmatch(r"[a-f0-9]{40}", value["source_tree"]) is not None,
            "Invalid source tree")
    require(type(value["recipient_spki_sha256"]) is str
            and re.fullmatch(r"[a-f0-9]{64}", value["recipient_spki_sha256"]) is not None,
            "Invalid recipient digest")
    reference = value["admission_reference"]
    require(type(reference) is str and 0 < len(reference) <= 4096
            and reference.strip() == reference
            and not any(ord(char) < 32 for char in reference),
            "A real external admission reference is required")
    for key, expected in (("mode", MODE), ("job_name", JOB_NAME),
                          ("archive", ARCHIVE), ("members", MEMBERS),
                          ("limits", LIMITS), ("prohibited_effects", PROHIBITED_EFFECTS)):
        _exact_value(value[key], expected, key)

def validate_context(request, environ):
    expected = {
        "GITHUB_SHA": request["context"]["source_sha"],
        "GITHUB_RUN_ID": request["context"]["run_id"],
        "GITHUB_RUN_ATTEMPT": str(request["context"]["run_attempt"]),
        "GITHUB_JOB": request["job_name"],
    }
    require(all(environ.get(key) == value for key, value in expected.items()),
            "Request does not match the current GitHub execution")
    # The caller checks source/tree/SDK and real recipient admission pre/post execution.

def _safe_path(root, relative):
    root = Path(root)
    require(type(relative) is str and relative and "\\" not in relative
            and ":" not in relative and "\x00" not in relative
            and not relative.startswith("/"), "Unsafe evidence path spelling")
    parts = relative.split("/")
    require(all(part not in ("", ".", "..") and not part.endswith((".", " "))
                and re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?",
                                 part) is None for part in parts),
            "Unsafe evidence path component")
    parsed = PurePosixPath(relative)
    require(not parsed.is_absolute() and parsed.as_posix() == relative
            and root.is_absolute() and root.is_dir() and not root.is_symlink()
            and root.resolve() == root, "Unsafe evidence root or path")
    path = root.joinpath(*parts)
    current = root
    for part in parts[:-1]:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o700)
        require(current.is_dir() and not current.is_symlink(),
                "Unsafe evidence directory")
    return path

def _open_new(root, relative):
    path = _safe_path(root, relative)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return path, os.fdopen(os.open(path, flags, 0o600), "wb")

def _identity(path, maximum=256 * 1024 * 1024):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(),
            f"Not a regular file: {path.name}")
    require(0 <= before.st_size <= maximum, f"File exceeds limit: {path.name}")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "File identity changed while opening")
        observed = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            observed += len(block)
            require(observed <= maximum, "File grew beyond its limit")
            digest.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = path.lstat()
    signature = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
    )
    require(signature(before) == signature(opened) == signature(after)
            == signature(final) and observed == before.st_size,
            "File changed during identity observation")
    return {"path": str(path), "bytes": observed, "sha256": digest.hexdigest(),
            "device": before.st_dev, "inode": before.st_ino,
            "mtime_ns": before.st_mtime_ns}

def _descriptor(root, path):
    identity = _identity(path)
    return {"path": Path(path).relative_to(root).as_posix(),
            "bytes": identity["bytes"], "sha256": identity["sha256"]}

def write_new(root, relative, raw):
    require(type(raw) is bytes, "Evidence must be original bytes")
    path, stream = _open_new(root, relative)
    with stream:
        stream.write(raw)
        stream.flush()
    return _descriptor(root, path)

def _json_bytes(value):
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")

def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unknown("ALab inner deadline expired; no retry")
    return remaining

class _HeaderTee:
    """Capture bytes actually read by HTTPResponse's header parser."""
    def __init__(self, wrapped, callback):
        self.wrapped, self.callback, self.active = wrapped, callback, True
    def readline(self, *args, **kwargs):
        raw = self.wrapped.readline(*args, **kwargs)
        if self.active and raw:
            self.callback(raw)
        return raw
    def __getattr__(self, name):
        return getattr(self.wrapped, name)

def _resolve_ipv4(host, port, deadline):
    """One bounded resolver; a late daemon cannot connect or write evidence."""
    answers = queue.Queue(maxsize=1)
    def resolve():
        try:
            value = socket.getaddrinfo(host, port, family=socket.AF_INET,
                                       type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            answer = (True, value)
        except BaseException as error:
            answer = (False, error)
        try:
            answers.put_nowait(answer)
        except queue.Full:
            pass
    worker = threading.Thread(target=resolve, name="alab-dns-once", daemon=True)
    worker.start()
    try:
        success, value = answers.get(timeout=min(10, _remaining(deadline)))
    except queue.Empty as error:
        raise Unknown("Fixed-host DNS observation exceeded its bound; no retry") from error
    _remaining(deadline)
    if not success:
        raise Refused(f"Fixed-host DNS resolution failed: {type(value).__name__}: {value}") from value
    require(type(value) is list and value, "No fixed-host IPv4 address")
    for family, socktype, protocol, _, address in value:
        if (family == socket.AF_INET and socktype == socket.SOCK_STREAM
                and protocol == socket.IPPROTO_TCP and type(address) is tuple
                and len(address) == 2 and address[1] == port):
            socket.inet_pton(socket.AF_INET, address[0])
            return address
    raise Refused("Resolver returned no supported fixed-host IPv4 endpoint")

def _http_transport(start, length, deadline, on_header, on_body):
    """One fixed HTTPS range, without redirects or retries."""
    location = urlsplit(ARCHIVE["url"])
    hostname, port = location.hostname, 443
    connection = http.client.HTTPSConnection(
        hostname, port=port, timeout=min(10, _remaining(deadline)))
    response, primary = None, None
    holder = {}
    expired = threading.Event()
    class CapturedResponse(http.client.HTTPResponse):
        def begin(self):
            tee = _HeaderTee(self.fp, on_header)
            self.fp = tee
            try:
                return super().begin()
            finally:
                tee.active = False
    connection.response_class = CapturedResponse
    def check_deadline():
        if expired.is_set():
            raise Unknown("Fixed HTTPS range deadline expired; no retry")
        return _remaining(deadline)
    def expire():
        expired.set()
        sock = holder.get("socket")
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
    def create_fixed_connection(address, timeout=None, source_address=None):
        require(address == (hostname, port) and source_address is None,
                "Unexpected HTTPS destination or source binding")
        endpoint = _resolve_ipv4(hostname, port, deadline)
        check_deadline()
        owned = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        holder["socket"] = owned
        try:
            check_deadline()
            owned.settimeout(min(10, check_deadline()))
            owned.connect(endpoint)
            check_deadline()
            return owned
        except BaseException:
            owned.close()
            raise
    def connect_fixed_https():
        require(getattr(connection, "_tunnel_host", None) is None,
                "HTTP tunneling is not admitted")
        owned = connection._create_connection(
            (connection.host, connection.port),
            connection.timeout, connection.source_address)
        connection.sock = owned
        try:
            wrapped = connection._context.wrap_socket(
                owned, server_hostname=hostname, do_handshake_on_connect=False)
            connection.sock = wrapped
            holder["socket"] = wrapped
            check_deadline()
            wrapped.settimeout(min(10, check_deadline()))
            wrapped.do_handshake()
            check_deadline()
        except BaseException:
            current = connection.sock
            if current is not None:
                current.close()
            connection.sock = None
            raise
    connection._create_connection = create_fixed_connection
    connection.connect = connect_fixed_https
    timer = threading.Timer(_remaining(deadline), expire)
    timer.daemon = True
    timer.start()
    try:
        connection.request("GET", location.path, headers={
            "Range": f"bytes={start}-{start + length - 1}",
            "If-Match": ARCHIVE["etag"], "Accept-Encoding": "identity",
            "Connection": "close", "User-Agent": "Motionwright-ALab-Readonly/1",
        })
        check_deadline()
        response = connection.getresponse()
        metadata = {"status": response.status,
                    "headers": list(response.headers.raw_items())}
        count = 0
        while True:
            check_deadline()
            sock = holder.get("socket")
            if sock is not None:
                sock.settimeout(min(10, check_deadline()))
            block = response.read(min(65536, length + 1 - count))
            if not block:
                break
            count += len(block)
            on_body(block)
            if count > length:
                break
        check_deadline()
        return metadata
    except BaseException as error:
        primary = error
        if response is not None:
            error.http_metadata = {"status": response.status,
                                   "headers": list(response.headers.raw_items())}
        if expired.is_set() and not isinstance(error, Unknown):
            uncertain = Unknown("Fixed HTTPS range deadline expired; no retry")
            uncertain.http_metadata = getattr(error, "http_metadata", None)
            primary = uncertain
            raise uncertain from error
        raise
    finally:
        timer.cancel()
        errors = []
        for owned in (response, connection):
            if owned is not None:
                try:
                    owned.close()
                except BaseException as error:
                    errors.append(f"{type(error).__name__}: {error}")
        if errors:
            if primary is not None:
                primary.add_note("HTTP closure errors: " + "; ".join(errors))
            else:
                raise Refused("HTTP closure failed: " + "; ".join(errors))

def _header_map(metadata):
    require(type(metadata) is dict and type(metadata.get("status")) is int
            and type(metadata.get("headers")) is list,
            "HTTP terminal metadata unavailable")
    values = {}
    for pair in metadata["headers"]:
        require(type(pair) in (tuple, list) and len(pair) == 2
                and all(type(item) is str for item in pair),
                "Malformed HTTP header observation")
        key, value = pair
        key = key.lower()
        require(key not in values, f"Duplicate HTTP header: {key}")
        values[key] = value.strip()
    return values

def inflate_exact(raw, member):
    require(type(raw) is bytes and len(raw) == member["compressed_bytes"],
            "Compressed member size mismatch")
    expected = member["bytes"]
    require(type(expected) is int and 0 < expected <= LIMITS["max_uncompressed_member_bytes"],
            "Uncompressed member exceeds budget")
    decoder = zlib.decompressobj(-15)
    try:
        result = decoder.decompress(raw, expected + 1)
        require(len(result) <= expected, "DEFLATE output exceeds exact size")
        require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                "DEFLATE is truncated or has trailing/unconsumed bytes")
        tail = decoder.flush()  # EOF and bounded consumption were checked first.
        require(len(result) + len(tail) == expected,
                "DEFLATE final output size mismatch")
        result += tail
    except zlib.error as error:
        raise Refused("Invalid raw DEFLATE") from error
    require(f"{zlib.crc32(result) & 0xffffffff:08x}" == member["crc32"],
            "Original member CRC mismatch")
    require(sha(result) == member["sha256"], "Original member SHA mismatch")
    return result

class Intake:
    def __init__(self, root, deadline, transport=None):
        self.root = Path(root)
        self.deadline = deadline
        self.transport = transport or _http_transport
        self.requests = self.requested_bytes = self.observed_bytes = 0
        self.ranges, self.members = [], []

    def fetch(self, start, length):
        require(type(start) is int and type(length) is int
                and 0 <= start < ARCHIVE["bytes"]
                and 0 < length <= LIMITS["one_request_max"]
                and start + length <= ARCHIVE["bytes"],
                "Invalid fixed archive range")
        require(self.requests < LIMITS["all_range_requests_max"],
                "Range request budget exhausted")
        require(self.requested_bytes + length <= LIMITS["range_bytes_budget"],
                "Requested range byte budget exhausted")
        _remaining(self.deadline)
        self.requests += 1
        self.requested_bytes += length
        prefix = f"http/range-{self.requests:02d}"
        raw_headers, raw_body = bytearray(), bytearray()
        header_observed = body_observed = 0
        metadata, primary = None, None
        record = {
            "request_number": self.requests, "start": start, "length": length,
            "end_inclusive": start + length - 1, "url": ARCHIVE["url"],
            "etag": ARCHIVE["etag"], "automatic_retry": False,
            "complete": False, "validation_status": "NOT_CLASSIFIED",
        }
        write_new(self.root, prefix + ".request.json", _json_bytes(record))
        def header(block):
            nonlocal header_observed
            require(type(block) is bytes, "Non-byte HTTP header")
            header_observed += len(block)
            raw_headers.extend(block[:max(0, 65537 - len(raw_headers))])
            require(header_observed <= 65536, "HTTP headers exceed 64KiB")
            _remaining(self.deadline)
        def body(block):
            nonlocal body_observed
            require(type(block) is bytes, "Non-byte HTTP body")
            body_observed += len(block)
            self.observed_bytes += len(block)
            raw_body.extend(block[:max(0, length + 1 - len(raw_body))])
            require(body_observed <= length, "HTTP body exceeds requested range")
            require(self.observed_bytes <= LIMITS["range_bytes_budget"],
                    "Observed range byte budget exhausted")
            _remaining(self.deadline)
        try:
            metadata = self.transport(start, length, self.deadline, header, body)
        except BaseException as error:
            primary = error
            metadata = getattr(error, "http_metadata", None)
        record["response"] = metadata
        headers_ended = raw_headers.endswith(b"\r\n\r\n") or raw_headers.endswith(b"\n\n")
        record["header_capture"] = {
            "observed_bytes": header_observed, "retained_bytes": len(raw_headers),
            "complete": metadata is not None and headers_ended
            and header_observed == len(raw_headers) and header_observed <= 65536,
        }
        record["body_capture"] = {
            "observed_bytes": body_observed, "retained_bytes": len(raw_body),
            "complete": primary is None and body_observed == len(raw_body) == length,
        }
        record["observed_bytes_total"] = self.observed_bytes
        record["retained_body_bytes"] = len(raw_body)
        if primary is not None:
            record["error"] = {"type": type(primary).__name__, "message": str(primary)}
        custody_errors = []
        for key, suffix, data in (
            ("headers", ".headers.original.bin", bytes(raw_headers)),
            ("body", ".body.original.bin", bytes(raw_body)),
        ):
            try:
                record[key] = write_new(self.root, prefix + suffix, data)
            except BaseException as error:
                custody_errors.append(f"{key}: {type(error).__name__}: {error}")
        record["custody_errors"] = custody_errors
        try:
            record["terminal"] = write_new(
                self.root, prefix + ".terminal.original.json", _json_bytes(record))
        except BaseException as error:
            custody_errors.append(f"terminal: {type(error).__name__}: {error}")
        self.ranges.append(record)
        if primary is not None:
            failure = Unknown if isinstance(primary, (TimeoutError, socket.timeout, Unknown)) else Refused
            raise failure(f"Range {self.requests} failed; no retry: {primary}", record) from primary
        require(not custody_errors, "HTTP originals could not be fully retained")
        headers = _header_map(metadata)
        require(metadata["status"] == 206, "Archive server did not return 206")
        require(headers.get("etag") == ARCHIVE["etag"], "Archive ETag changed")
        require(headers.get("content-range") ==
                f"bytes {start}-{start + length - 1}/{ARCHIVE['bytes']}",
                "Archive Content-Range mismatch")
        require(headers.get("content-length") == str(length),
                "Archive Content-Length mismatch")
        require(headers.get("content-encoding", "identity") == "identity",
                "Encoded HTTP response is not admitted")
        require("transfer-encoding" not in headers, "Transfer coding not admitted")
        require(len(raw_body) == length, "Truncated archive range")
        require(record["header_capture"]["complete"] and record["body_capture"]["complete"],
                "HTTP original capture incomplete")
        record["complete"] = True
        record["validation_status"] = "VALIDATED_RANGE"
        return bytes(raw_body)

    def member(self, row):
        offset = row["local_header_offset"]
        header = self.fetch(offset, 30)
        signature, version, flags, method, _, _, crc, csize, usize, nlen, xlen = struct.unpack("<4s5H3I2H", header)
        require(signature == b"PK\x03\x04" and version <= 20 and method == row["compression"] == 8,
                "Unsupported local ZIP header")
        require(flags & ~(0x0008 | 0x0800) == 0, "Unsupported ZIP flags")
        descriptor_present = bool(flags & 0x0008)
        expected_crc = int(row["crc32"], 16)
        if descriptor_present:
            require(crc in (0, expected_crc) and csize in (0, row["compressed_bytes"])
                    and usize in (0, row["bytes"]),
                    "Descriptor-backed header contradicts fixed index")
        else:
            require((crc, csize, usize) ==
                    (expected_crc, row["compressed_bytes"], row["bytes"]),
                    "Local ZIP identity differs from fixed index")
        require(nlen == len(row["name"].encode("ascii")) and nlen + xlen > 0,
                "ZIP filename length mismatch")
        name_extra = self.fetch(offset + 30, nlen + xlen)
        require(name_extra[:nlen] == row["name"].encode("ascii"), "ZIP member name mismatch")
        compressed = bytearray()
        remaining = row["compressed_bytes"]
        cursor = offset + 30 + nlen + xlen
        descriptor_bytes = b""
        while remaining:
            amount = min(remaining, LIMITS["one_request_max"])
            extra = 16 if amount == remaining and descriptor_present else 0
            require(amount + extra <= LIMITS["one_request_max"],
                    "Fixed descriptor tail needs another request; not admitted")
            block = self.fetch(cursor, amount + extra)
            compressed.extend(block[:amount])
            if extra:
                descriptor_bytes = block[amount:]
            cursor += amount
            remaining -= amount
        descriptor = None
        if descriptor_present:
            require(len(descriptor_bytes) == 16, "Missing ZIP data descriptor")
            signed = descriptor_bytes.startswith(b"PK\x07\x08")
            content = descriptor_bytes[4:16] if signed else descriptor_bytes[:12]
            require(struct.unpack("<III", content) ==
                    (expected_crc, row["compressed_bytes"], row["bytes"]),
                    "ZIP data descriptor differs from fixed index")
            descriptor = {
                "signed": signed, "bytes": 16 if signed else 12,
                "crc32": row["crc32"], "compressed_bytes": row["compressed_bytes"],
                "uncompressed_bytes": row["bytes"],
                "adjacent_peek_hex": "" if signed else descriptor_bytes[12:].hex(),
                "adjacent_peek_interpreted": False,
            }
        original = inflate_exact(bytes(compressed), row)
        saved = write_new(self.root, row["retained_path"], original)
        saved["crc32"] = row["crc32"]
        self.members.append({**saved, "name": row["name"], "descriptor": descriptor,
                             "local_header_flags": flags})
        return saved

def run_command(argv, root, label, deadline, limit=MAX_JSON):
    """Retain one terminal and original binary streams before interpretation."""
    root = Path(root)
    require(type(argv) is list and argv and all(type(x) is str for x in argv),
            "Command argv must be explicit strings")
    require(re.fullmatch(r"[a-z0-9_-]{1,64}", label) is not None,
            "Invalid process evidence label")
    require(type(limit) is int and 0 < limit <= MAX_JSON, "Invalid process output limit")
    _remaining(deadline)
    prefix = f"process/{label}"
    record = {"stdout": b"", "stderr": b"", "originals": {}, "custody_errors": []}
    terminal = {
        "spawned": False, "exit_code": None, "execution_status": "NOT_STARTED",
        "timed_out": False, "overflow": False, "forced": False,
        "reader_errors": [], "cleanup_errors": [], "primary_error": None,
    }
    record["terminal"] = terminal
    def remember_primary(error):
        if terminal["primary_error"] is None:
            terminal["primary_error"] = {"type": type(error).__name__, "message": str(error)}
        return error
    primary, child = None, None
    outputs, threads = {}, []
    overflow, reader_failure = threading.Event(), threading.Event()
    started = time.monotonic()
    try:
        record["originals"]["argv"] = write_new(
            root, prefix + ".argv.original.json",
            _json_bytes({"argv": argv, "cwd": str(root), "automatic_retry": False}))
        for name in ("stdout", "stderr"):
            path, stream = _open_new(root, prefix + f".{name}.original.bin")
            outputs[name] = (path, stream)
            terminal[name] = {"observed_bytes": 0, "retained_bytes": 0, "complete": False}
        child = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        terminal["spawned"] = True
        terminal["pid"] = getattr(child, "pid", None)
        def drain(name, pipe):
            state, output = terminal[name], outputs[name][1]
            try:
                while True:
                    block = pipe.read(65536)
                    if not block:
                        state["complete"] = not overflow.is_set()
                        break
                    state["observed_bytes"] += len(block)
                    keep = block[:max(0, limit - state["retained_bytes"])]
                    if keep:
                        output.write(keep)
                        state["retained_bytes"] += len(keep)
                    if state["observed_bytes"] > limit:
                        overflow.set()
            except BaseException as error:
                terminal["reader_errors"].append({
                    "stream": name, "type": type(error).__name__, "message": str(error)})
                reader_failure.set()
            finally:
                try:
                    pipe.close()
                except BaseException as error:
                    terminal["reader_errors"].append({
                        "stream": name, "type": type(error).__name__, "message": str(error)})
                    reader_failure.set()
        for name in ("stdout", "stderr"):
            thread = threading.Thread(target=drain, args=(name, getattr(child, name)), daemon=True)
            threads.append(thread)
            thread.start()
        command_deadline = min(deadline, time.monotonic() + PROBE_SECONDS)
        while child.poll() is None:
            if overflow.is_set():
                terminal["overflow"] = True
                raise Refused("Process output exceeds the retained prefix budget")
            if reader_failure.is_set():
                raise Refused("Process original stream custody failed")
            if time.monotonic() >= command_deadline:
                terminal["timed_out"] = True
                raise Unknown("Process timed out; no retry")
            time.sleep(0.01)
        terminal["exit_code"] = child.returncode
        if type(child.returncode) is not int:
            raise Unknown("Exact process terminal unavailable; no retry")
        if child.returncode != 0:
            raise Refused("Process returned a nonzero terminal")
    except BaseException as error:
        primary = remember_primary(error if isinstance(error, Refused) else Refused(str(error)))
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    terminal["forced"] = True
                    child.kill()
                terminal["exit_code"] = child.wait(timeout=2)
            except BaseException as error:
                terminal["cleanup_errors"].append({
                    "type": type(error).__name__, "message": str(error)})
                primary = remember_primary(Unknown("Process terminal remains uncertain; no retry"))
        for thread in threads:
            thread.join(timeout=1)
        if any(thread.is_alive() for thread in threads):
            primary = remember_primary(Unknown("Original stream reader did not close; no retry"))
            terminal["cleanup_errors"].append({"message": "reader still active"})
        terminal["overflow"] = terminal["overflow"] or overflow.is_set()
        if terminal["overflow"] and primary is None:
            primary = remember_primary(Refused("Process output exceeds the retained prefix budget"))
        if terminal["reader_errors"] and primary is None:
            primary = remember_primary(Refused("Process original stream custody failed"))
        if terminal["spawned"] and type(terminal["exit_code"]) is not int:
            terminal["observed_invalid_exit_code"] = terminal["exit_code"]
            terminal["observed_invalid_exit_code_type"] = type(terminal["exit_code"]).__name__
            terminal["exit_code"] = None
            primary = remember_primary(Unknown("Exact process terminal unavailable; no retry"))
        if (terminal["spawned"] and terminal["exit_code"] is not None
                and terminal["exit_code"] != 0 and primary is None):
            primary = remember_primary(Refused("Process returned a nonzero terminal"))
        if (terminal["spawned"] and primary is None
                and not all(terminal[name]["complete"] for name in ("stdout", "stderr"))):
            primary = remember_primary(Refused("Original process streams are incomplete"))
        terminal["execution_status"] = (
            "UNKNOWN_NO_RETRY" if isinstance(primary, Unknown)
            else "TERMINAL_OBSERVED" if terminal["spawned"] else "NOT_STARTED")
        terminal["elapsed_seconds"] = time.monotonic() - started
        for name, (path, output) in outputs.items():
            try:
                output.flush()
                output.close()
                record["originals"][name] = _descriptor(root, path)
                record[name] = path.read_bytes()
            except BaseException as error:
                record["custody_errors"].append({
                    "stage": name, "type": type(error).__name__, "message": str(error)})
        try:
            record["originals"]["terminal"] = write_new(
                root, prefix + ".terminal.original.json", _json_bytes(terminal))
        except BaseException as error:
            record["custody_errors"].append({
                "stage": "terminal", "type": type(error).__name__, "message": str(error)})
        if record["custody_errors"] and primary is None:
            primary = remember_primary(Refused("Original process custody is incomplete"))
        if primary is not None:
            primary.record = record
    if primary is not None:
        raise primary
    return record

def _read_request(path):
    path = Path(path)
    require(path.is_absolute(), "Request path must be absolute")
    identity = _identity(path, MAX_JSON)
    require(identity["bytes"] > 0, "Empty request")
    raw = path.read_bytes()
    require(len(raw) == identity["bytes"] and sha(raw) == identity["sha256"],
            "Request changed while reading")
    return raw, identity

def _public_process_record(record):
    return {key: record[key] for key in ("terminal", "originals", "custody_errors")
            if key in record}

def external_alab_readonly(request_path, evidence_root, ffprobe, *,
                           transport=None, command_runner=None, environ=None):
    """Fixed CLI entry; injectable I/O is only for explicit component tests."""
    raw_request, request_identity = _read_request(request_path)
    request = strict_object(raw_request, "ALab request")
    validate_request(request)
    environment = os.environ if environ is None else environ
    validate_context(request, environment)
    root, executable = Path(evidence_root), Path(ffprobe)
    require(root.is_absolute() and not root.exists() and not root.is_symlink(),
            "A fresh absolute evidence root is required")
    require(root.parent.is_dir() and not root.parent.is_symlink()
            and root.parent.resolve() == root.parent,
            "Evidence parent must be a canonical existing directory")
    require(executable.is_absolute(), "ffprobe path must be absolute")
    tool_identity = _identity(executable)
    require(tool_identity["bytes"] > 0, "Empty ffprobe executable")
    runner = command_runner or run_command
    started = time.monotonic()
    deadline = started + LIMITS["inner_total_wall_seconds_max"] - 5
    old_umask = os.umask(0o077)
    try:
        root.mkdir(mode=0o700)
    except BaseException:
        os.umask(old_umask)
        raise
    intake = Intake(root, deadline, transport)
    result = {
        "schema": RESULT_SCHEMA, "request_sha256": sha(raw_request),
        "context": request["context"], "source_tree": request["source_tree"],
        "job_name": request["job_name"], "status": "FAILED",
        "execution_status": "NOT_STARTED",
        "intake": {"requests": 0, "requested_bytes": 0, "observed_bytes": 0,
                   "ranges": [], "members": [],
                   "source_tree_and_recipient_admission": "EXTERNAL_CALLER_PRE_POST_PROOF"},
        "tools": [],
        "media": [{"path": row["retained_path"], "status": "NOT_STARTED"}
                  for row in MEMBERS if row["kind"] == "video-original"],
        "originals": [], "prohibited_effects": dict(PROHIBITED_EFFECTS),
        "source_origin": "NOT_OBSERVED", "decoded_frames_verified": False,
        "candidate_admitted": False, "human_acceptance": False,
        "release_authorized": False, "automatic_retry": False,
    }
    primary = None
    try:
        write_new(root, "request.original.json", raw_request)
        for row in MEMBERS:
            intake.member(row)
        result["execution_status"] = "TERMINAL_OBSERVED"
        def command(argv, label):
            require(_identity(executable) == tool_identity,
                    "ffprobe physical identity changed before execution")
            observed = runner(argv, root, label, deadline, limit=MAX_JSON)
            terminal = observed.get("terminal")
            if (type(terminal) is not dict
                    or terminal.get("execution_status") != "TERMINAL_OBSERVED"
                    or type(terminal.get("exit_code")) is not int):
                raise Unknown("ffprobe terminal is absent, ambiguous or uncertain; no retry", observed)
            if terminal["exit_code"] != 0:
                raise Refused("ffprobe returned a nonzero terminal", observed)
            require(not observed.get("custody_errors"), "ffprobe original custody is incomplete")
            require(set(observed["originals"]) == {"argv", "stdout", "stderr", "terminal"},
                    "ffprobe originals are incomplete")
            for descriptor in observed["originals"].values():
                path = _safe_path(root, descriptor["path"])
                require(_descriptor(root, path) == descriptor, "ffprobe original descriptor mismatch")
            require(_identity(executable) == tool_identity,
                    "ffprobe physical identity changed after execution")
            return observed
        version = command([str(executable), "-version"], "version")
        require(version["stdout"].startswith(b"ffprobe version "),
                "Executable did not identify itself as ffprobe")
        result["tools"].append({"name": "ffprobe", "identity_before": tool_identity,
                                "version": _public_process_record(version)})
        media_rows = [row for row in MEMBERS if row["kind"] == "video-original"]
        for index, row in enumerate(media_rows):
            path = _safe_path(root, row["retained_path"])
            argv = [str(executable), "-v", "error", "-protocol_whitelist", "file",
                    "-threads", "1", "-max_alloc", "268435456", "-show_streams",
                    "-show_format", "-of", "json", str(path)]
            observed = command(argv, f"media-{index}")
            parsed = strict_object(observed["stdout"], f"ffprobe media {index}")
            require(type(parsed.get("streams")) is list
                    and all(type(stream) is dict for stream in parsed["streams"])
                    and type(parsed.get("format")) is dict,
                    "ffprobe did not return all-streams/format metadata")
            result["media"][index] = {
                "path": row["retained_path"], "bytes": row["bytes"],
                "sha256": row["sha256"], "crc32": row["crc32"],
                "status": "METADATA_ORIGINALS_RETAINED",
                "process": _public_process_record(observed), "observations": parsed,
                "source_origin": "NOT_OBSERVED", "decoded_frames_verified": False,
            }
        result["tools"][0]["identity_after"] = _identity(executable)
        require(result["tools"][0]["identity_after"] == tool_identity,
                "ffprobe executable changed")
        require(_identity(Path(request_path), MAX_JSON) == request_identity,
                "Request changed during execution")
        validate_context(request, environment)
        for row in MEMBERS:
            path = _safe_path(root, row["retained_path"])
            observed = _descriptor(root, path)
            require(observed["bytes"] == row["bytes"] and observed["sha256"] == row["sha256"],
                    "Retained original changed")
        _remaining(deadline)
        result["status"] = "READONLY_METADATA_ORIGINALS_RETAINED"
    except BaseException as error:
        primary = error
        result["status"] = "UNKNOWN_NO_RETRY" if isinstance(error, Unknown) else "FAILED"
        if isinstance(error, Unknown):
            result["execution_status"] = "UNKNOWN_NO_RETRY"
        elif intake.requests:
            result["execution_status"] = "TERMINAL_OBSERVED"
        result["intake"]["error"] = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, Refused) and error.record:
            result["intake"]["failure_record"] = _public_process_record(error.record)
    finally:
        result["intake"].update({
            "requests": intake.requests, "requested_bytes": intake.requested_bytes,
            "observed_bytes": intake.observed_bytes, "ranges": intake.ranges,
            "members": intake.members, "elapsed_seconds": time.monotonic() - started,
            "descriptor_adjustment": "Bit3 permits zero local CRC/sizes only with a matching fixed-index data descriptor. Last range includes16descriptor/peek bytes; DEFLATE sees exactly compressed_bytes.",
        })
        custody_errors = []
        try:
            files = sorted(path for path in root.rglob("*") if path.is_file())
            result["originals"] = [_descriptor(root, path) for path in files]
            for descriptor in result["originals"]:
                require(_descriptor(root, _safe_path(root, descriptor["path"])) == descriptor,
                        "Evidence changed before final custody")
        except BaseException as error:
            custody_errors.append({"type": type(error).__name__, "message": str(error)})
            if not isinstance(primary, Unknown):
                result["status"] = "FAILED"
        if custody_errors:
            result["intake"]["final_custody_errors"] = custody_errors
        try:
            write_new(root, "result.json", _json_bytes(result))
        except BaseException as error:
            if primary is not None:
                primary.add_note(f"Final result custody also failed: {type(error).__name__}: {error}")
                raise primary from error
            raise Refused("Final result custody failed") from error
        finally:
            os.umask(old_umask)
    return result
