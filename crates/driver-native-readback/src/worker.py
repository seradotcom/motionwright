"""Fixed Host adapter. The pinned Motionwright reader owns every media verdict.

Rust appends a base64 JSON packet and invokes Python with -I -B -. The packet
contains no executable code selected by a request: reader_source is the exact
compile-time source, checked in Rust and again here before exec. Tool and mount
paths reach argv only through Driver Host typed resolution.
"""
import base64
import hashlib
import json
import sys
import types
from pathlib import Path

READER_SHA256 = "5c66976b10357c7e97294e62bccb6844fad4277344ccb7c948e421f2fc267abc"
SCHEMA = "motionwright.host-original-mix-readback/1"
DIAGNOSTIC = "original_mix_host_readback_rejected"
MAX_PACKET_BYTES = 48 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_COMPLETE_REPORT_BYTES = 16 * 1024 * 1024


def _require(value):
    if not value:
        raise ValueError(DIAGNOSTIC)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _load_reader(source):
    _require(isinstance(source, str) and _digest(source.encode("utf-8")) == READER_SHA256)
    name = "_motionwright_pinned_native_mlt_mix_readback"
    reader = types.ModuleType(name)
    # dataclasses consults the module registry; __name__ must not be __main__.
    sys.modules[name] = reader
    exec(compile(source, "<motionwright-pinned-readback>", "exec"), reader.__dict__)
    return reader


def _safe_name(value, suffix):
    _require(isinstance(value, str) and 1 <= len(value) <= 240)
    _require(value.endswith(suffix) and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in value))
    _require(value not in (".", "..") and "/" not in value and "\\" not in value)
    return value


def _summary(report):
    """Reduce transport volume only, after the single full inspection succeeds.

    All windows have already been compared by inspect_master. The complete
    canonical report digest is retained; the complete window arrays are not
    claimed as a persisted artifact. No new audio thresholds or verdicts exist
    in this adapter.
    """
    _require(report.get("schema") == "motionwright.native-original-mix-readback/1")
    _require(report.get("technical_content_readback") == "PASS")
    scope = ("listening_approval", "rights_approval", "editable_source_reopen_verified",
             "independent_loudness_verified")
    _require(all(report.get(key) is False for key in scope))
    content = report["content"]
    keep = ("reference_sample_frames", "decoded_sample_frames",
            "decoded_access_unit_suffix_samples", "reference_pcm_sha256",
            "decoded_pcm_sha256", "logical_interval", "all_logical_samples_compared",
            "alignment", "limits")
    result = {key: report[key] for key in ("schema", "technical_content_readback",
              "reference_wav_sha256", "final_mp4_sha256", "clock", *scope)}
    result["content"] = {key: content[key] for key in keep}
    result["content"]["compared_windows"] = len(content["windows"])
    result["content"]["compared_short_energy_windows"] = len(content["short_energy_windows"])
    # Serialize incrementally under an explicit total budget. This is a
    # diagnostic transport budget, not truncation of a successful comparison.
    digest = hashlib.sha256()
    size = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False)
    for text in encoder.iterencode(report):
        chunk = text.encode("ascii")
        size += len(chunk)
        _require(size <= MAX_COMPLETE_REPORT_BYTES)
        digest.update(chunk)
    result["complete_reader_report_sha256"] = digest.hexdigest()
    result["complete_reader_report_bytes"] = size
    result["complete_reader_report_retained"] = False
    return result


def run_packet(packet, argv):
    _require(isinstance(packet, dict) and set(packet) == {
        "binding_json", "binding_sha256", "reader_source", "reference_name", "master_name"})
    _require(len(argv) == 4)
    _require(isinstance(packet["binding_json"], str))
    encoded = packet["binding_json"].encode("utf-8")
    _require(len(encoded) <= 16 * 1024)
    binding = json.loads(encoded)
    _require(isinstance(binding, dict))
    _require(_digest(encoded) == packet["binding_sha256"])
    reader = _load_reader(packet["reader_source"])
    clock = reader.MediaClock(**binding["clock"])
    # Geometry and exact sample-clock guards precede any tool or media work.
    samples = clock.samples
    _require(samples <= 15 * 48_000)
    output_root, ffmpeg, ffprobe = (Path(item) for item in argv[1:])
    _require(output_root.is_dir() and not output_root.is_symlink())
    reference = output_root / _safe_name(packet["reference_name"], ".wav")
    master = output_root / _safe_name(packet["master_name"], ".mp4")
    # Host seals these executables. This extra readback binds their exact bytes
    # to the owner installation pins retained in the inspection receipt.
    pins = binding["tool_pins"]
    tool_tokens = []
    for executable, expected in ((Path(sys.executable), pins["python_sha256"]),
                                 (ffmpeg, pins["ffmpeg_sha256"]),
                                 (ffprobe, pins["ffprobe_sha256"])):
        _, token = reader._checked_file(executable, expected)
        tool_tokens.append((executable, token))
    report = reader.inspect_master(
        reference, master,
        reference_sha256=binding["reference_wav_sha256"],
        master_sha256=binding["master_sha256"],
        clock=clock, ffmpeg=ffmpeg, ffprobe=ffprobe,
    )
    for executable, token in tool_tokens:
        reader._unchanged(executable, token)
    return {"schema": SCHEMA, "binding_sha256": packet["binding_sha256"],
            "reader_sha256": READER_SHA256, "tool_pins": pins,
            "status": "PASS", "report": _summary(report)}


def main(encoded_packet):
    try:
        _require(isinstance(encoded_packet, str) and len(encoded_packet) <= 64 * 1024)
        raw = base64.b64decode(encoded_packet, validate=True)
        _require(len(raw) <= MAX_PACKET_BYTES)
        packet = json.loads(raw)
        result = run_packet(packet, sys.argv)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False).encode("ascii")
        _require(len(output) <= MAX_RESULT_BYTES)
    except Exception:
        # Paths, exception text and decoder stderr never cross this boundary.
        print(json.dumps({"schema": SCHEMA, "status": "REJECTED", "diagnostic": DIAGNOSTIC}))
        return 2
    sys.stdout.buffer.write(output + b"\n")
    return 0
