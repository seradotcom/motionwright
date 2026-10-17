"""Pinned read-only audio metrology inside the existing Driver Host.

The original whole-medium reader owns file custody, decoder supervision,
clock validation and PCM comparison. This adapter adds one fixed FFmpeg
ebur128 method on the actual sealed input, with float decode before any s16
conversion. It creates no media and contains no production mixer/normalizer.
The engineering uncertainty allowance is not a calibration certificate.
"""
import array
import base64
import hashlib
import json
import math
import re
import stat
import sys
import types
import uuid
from pathlib import Path

READER_SHA256 = "5c66976b10357c7e97294e62bccb6844fad4277344ccb7c948e421f2fc267abc"
REQUEST_SCHEMA = "motionwright.audio-measurement-request/1"
RESULT_SCHEMA = "motionwright.host-audio-measurement/1"
MEASURE_SCHEMA = "motionwright.audio-measurement/1"
METHOD = "mw.ffmpeg-ebur128-f64-stereo48k/1"
INTEGRATED_UNCERTAINTY_LU = 0.101
TRUE_PEAK_UNCERTAINTY_DB = 0.1
PRINTED_PEAK_HALF_QUANTUM = 0.0005
PEAK_FLUSH_SAMPLES = 4800
MAX_PACKET_BYTES = 96 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_METADATA_BYTES = 128 * 1024
DIAGNOSTIC = "native_audio_measurement_rejected"
BINDING_KEYS = {"project_id", "generation", "revision", "deliverable_id",
                "canonical_dependency_sha256", "source_plan_sha256", "source_readiness_sha256",
                "premix_wav_sha256", "premix_pcm_sha256", "clock"}
PIN_KEYS = {"worker_sha256", "python_sha256", "ffmpeg_sha256", "ffprobe_sha256",
            "owner_runtime_profile_sha256", "meter_validation_sha256"}
REQUEST_KEYS = {"schema", "binding", "source_binding_sha256", "role", "input_sha256",
                "mastering_plan_sha256", "gain_milli_db", "native_request_id", "width", "height", "pins"}


def require(value, reason=DIAGNOSTIC):
    if not value:
        raise ValueError(reason)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def digest_ok(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def exact_object(value, keys):
    require(type(value) is dict and set(value) == keys, "closed_object_required")


def bounded_integer(value, low, high):
    require(type(value) is int and low <= value <= high, "bounded_integer_required")
    return value


def validate_request(request):
    exact_object(request, REQUEST_KEYS)
    require(request["schema"] == REQUEST_SCHEMA and request["role"] in
            ("canonical_premix", "mastered_wav", "final_aac"), "measurement_profile")
    source = request["binding"]
    exact_object(source, BINDING_KEYS)
    for name in ("project_id", "generation", "deliverable_id"):
        require(isinstance(source[name], str), "canonical_identity")
        identity = uuid.UUID(source[name])
        require(identity.int != 0 and str(identity) == source[name], "canonical_identity")
    bounded_integer(source["revision"], 0, 2**64 - 1)
    for key in BINDING_KEYS - {"project_id", "generation", "revision", "deliverable_id", "clock"}:
        require(digest_ok(source[key]), "canonical_digest")
    clock = source["clock"]
    exact_object(clock, {"video_frames", "fps_num", "fps_den", "sample_frames"})
    frames = bounded_integer(clock["video_frames"], 1, 2**64 - 1)
    num = bounded_integer(clock["fps_num"], 1, 120000)
    den = bounded_integer(clock["fps_den"], 1, 100000)
    samples = bounded_integer(clock["sample_frames"], 19200, 720000)
    require(num <= 120 * den and frames * den * 48000 == samples * num, "exact_sample_clock")
    actual = sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"))
    require(request["source_binding_sha256"] == actual, "source_binding_digest")
    bounded_integer(request["width"], 1, 4096)
    bounded_integer(request["height"], 1, 4096)
    exact_object(request["pins"], PIN_KEYS)
    require(all(digest_ok(value) for value in request["pins"].values()), "owner_method_pins")
    require(digest_ok(request["input_sha256"]), "input_digest")
    if request["role"] == "canonical_premix":
        require(request["input_sha256"] == source["premix_wav_sha256"] and
                all(request[name] is None for name in ("mastering_plan_sha256", "gain_milli_db", "native_request_id")),
                "premix_request_cannot_claim_master")
    else:
        require(digest_ok(request["mastering_plan_sha256"]), "mastering_plan_digest")
        bounded_integer(request["gain_milli_db"], -60000, 24000)
        if request["role"] == "mastered_wav":
            require(request["native_request_id"] is None, "wav_request_id")
        else:
            require(isinstance(request["native_request_id"], str) and
                    re.fullmatch(r"mxav-[0-9a-f]{36}", request["native_request_id"]) is not None,
                    "native_master_request_identity")
    return samples


def media_names(request):
    validate_request(request)
    source = request["binding"]
    canonical = ("mw-original-mix-" + uuid.UUID(source["project_id"]).hex + "-" +
                 uuid.UUID(source["generation"]).hex + "-r" + str(source["revision"]) + "-p" +
                 uuid.UUID(source["deliverable_id"]).hex + "-" + source["source_plan_sha256"][:16] +
                 "-" + source["premix_wav_sha256"][:16] + ".wav")
    if request["role"] == "canonical_premix":
        return canonical, canonical
    if request["role"] == "mastered_wav":
        return "mw-audio-master-" + request["mastering_plan_sha256"] + ".wav", canonical
    token = sha256(request["native_request_id"].encode("ascii"))[:16]
    return "mw-" + uuid.UUID(source["project_id"]).hex + "-r" + str(source["revision"]) + "-" + token + "-multisegment-master.mp4", canonical


def load_reader(source):
    require(isinstance(source, str) and sha256(source.encode("utf-8")) == READER_SHA256, "reader_source_pin")
    name = "_motionwright_audio_measure_pinned_reader"
    reader = types.ModuleType(name)
    sys.modules[name] = reader
    exec(compile(source, "<motionwright-pinned-whole-medium-reader>", "exec"), reader.__dict__)
    return reader


def checked_path(root, name):
    require(root.is_absolute() and isinstance(name, str) and re.fullmatch(r"[a-z0-9.-]{1,240}", name), "owner_media_name")
    require(name not in (".", ".."), "owner_media_name")
    path = root / name
    for part in (*reversed(path.parents), path):
        meta = part.lstat()
        require(not stat.S_ISLNK(meta.st_mode) and not (getattr(meta, "st_file_attributes", 0) & 0x400), "media_reparse_or_symlink")
    require(path.resolve(strict=True).parent == root.resolve(strict=True), "owner_media_containment")
    return path


def decode_argv(ffmpeg, path):
    # No resample, channel rewrite, trim or s16 conversion on the observation.
    return [str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:a:0", "-vn", "-c:a", "pcm_f64le", "-f", "f64le", "-"]


def meter_argv(ffmpeg, path, samples, mode):
    require(mode in ("integrated", "programme_peak", "physical_peak"), "closed_meter_mode")
    bounded_integer(samples, 19200, 720000)
    filters = []
    if mode != "physical_peak":
        # This window is fixed by the canonical clock before reading samples.
        # atrim is analysis only: no media output or timing correction occurs.
        filters.append("atrim=end_sample=" + str(samples))
    if mode != "integrated":
        # Drain the interpolator with 100 ms of analysis-only zeros. This
        # never changes integrated loudness or pads the delivered programme.
        filters.append("apad=pad_len=" + str(PEAK_FLUSH_SAMPLES))
    filters.extend(("ebur128=metadata=1:peak=true", "ametadata=mode=print:file=-"))
    return [str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:a:0", "-vn", "-af", ",".join(filters), "-f", "null", "-"]


def parse_metadata(raw, expected_samples):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_METADATA_BYTES, "metadata_budget")
    text = raw.decode("ascii")
    required = {"lavfi.r128.M", "lavfi.r128.S", "lavfi.r128.I", "lavfi.r128.LRA",
                "lavfi.r128.LRA.low", "lavfi.r128.LRA.high", "lavfi.r128.true_peak",
                "lavfi.r128.true_peaks_ch0", "lavfi.r128.true_peaks_ch1"}
    records = []
    current = None
    header = re.compile(r"frame:(\d+)\s+pts:(\d+)\s+pts_time:([0-9.eE+-]+)\s*")
    for line in text.splitlines():
        match = header.fullmatch(line)
        if match:
            if current is not None:
                require(set(current) == required, "incomplete_metadata_frame")
                records.append(current)
            frame, pts = int(match[1]), int(match[2])
            require(frame == len(records) and pts == frame * 4800, "metadata_window_clock")
            time = float(match[3])
            require(math.isfinite(time) and abs(time - pts / 48000) <= 0.000001, "metadata_window_clock")
            current = {}
            continue
        require(current is not None and "=" in line, "metadata_frame_required")
        key, value = line.split("=", 1)
        require(key in required and key not in current, "metadata_key")
        require(re.fullmatch(r"-?\d+\.\d{3}|-inf", value) is not None, "metadata_decimal_precision")
        number = float(value)
        if key in ("lavfi.r128.M", "lavfi.r128.S") and value == "-inf":
            number = None
        else:
            require(math.isfinite(number), "finite_meter_measurement")
        current[key] = number
    if current is not None:
        require(set(current) == required, "incomplete_metadata_frame")
        records.append(current)
    # ebur128 emits full 100 ms metadata frames. The last incomplete gated
    # window is not promoted to a complete integrated observation.
    require(len(records) == expected_samples // 4800 and len(records) >= 4, "metadata_frame_count")
    for row in records:
        peak = row["lavfi.r128.true_peak"]
        require(peak >= 0 and row["lavfi.r128.true_peaks_ch0"] >= 0 and row["lavfi.r128.true_peaks_ch1"] >= 0,
                "negative_linear_peak")
        require(abs(peak - max(row["lavfi.r128.true_peaks_ch0"], row["lavfi.r128.true_peaks_ch1"])) <= 0.0011,
                "peak_channel_join")
    return records


def peak_upper(records):
    printed = max(row["lavfi.r128.true_peak"] for row in records)
    if printed == 0:
        return None  # no finite peak with sufficient printed resolution
    return 20.0 * math.log10(printed + PRINTED_PEAK_HALF_QUANTUM) + TRUE_PEAK_UNCERTAINTY_DB


def scalar_expectation(pcm, gain_milli_db):
    """A scalar-operation test oracle, never a producer WAV or bus mixer."""
    bounded_integer(gain_milli_db, -60000, 24000)
    require(isinstance(pcm, bytes) and len(pcm) % 4 == 0, "stereo_pcm_required")
    values = array.array("h", pcm)
    if sys.byteorder != "little":
        values.byteswap()
    gain = 10.0 ** (gain_milli_db / 20000.0)
    expected = array.array("h")
    for sample in values:
        value = sample * gain
        require(-32768 <= value <= 32767, "scalar_prediction_would_clip")
        quantized = math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
        expected.append(quantized)
    if sys.byteorder != "little":
        expected.byteswap()
    return expected.tobytes()


def require_source_timing_signature(reader, pcm, expected_sha256):
    # Admission of the source's timing observability, not an independent
    # producer comparison. Reject a periodic/ambiguous source before MLT gain.
    require(sha256(pcm) == expected_sha256, "premix_pcm_digest")
    for channel in reader._channels(pcm):
        reader._lag_diagnostic(channel, channel, reader.ContentLimits())


def verify_transfer(reader, request, source_path, actual_path, samples):
    source = reader.read_reference_wav(source_path, samples)
    require(sha256(source) == request["binding"]["premix_pcm_sha256"], "premix_pcm_digest")
    actual = reader.read_reference_wav(actual_path, samples)
    expected = scalar_expectation(source, request["gain_milli_db"])
    limits = reader.ContentLimits(minimum_correlation=0.9999, maximum_relative_error=0.005,
        minimum_gain=0.995, maximum_gain=1.005, maximum_silent_sample=2,
        minimum_lag_margin=0.00001, block_samples=2048, diagnostic_lag_samples=1600,
        short_window_samples=64, minimum_short_window_rms=128.0,
        minimum_short_window_gain=0.98, maximum_short_window_gain=1.02)
    content = reader.compare_pcm(expected, actual, limits, declared_suffix_samples=0)
    return {"reference_kind": "analytic_scalar_expectation_not_producer_media",
            "premix_pcm_sha256": sha256(source), "expected_pcm_sha256": sha256(expected),
            "actual_pcm_sha256": sha256(actual), "gain_milli_db": request["gain_milli_db"],
            "all_samples_compared": True, "reader_sha256": READER_SHA256,
            "content_report_sha256": sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()),
            "content_report_retained": False,
            "content": {**{key: content[key] for key in ("reference_sample_frames", "decoded_sample_frames",
                "decoded_access_unit_suffix_samples", "reference_pcm_sha256", "decoded_pcm_sha256",
                "logical_interval", "all_logical_samples_compared", "alignment", "limits")},
                "compared_windows": len(content["windows"]),
                "compared_short_energy_windows": len(content["short_energy_windows"])}}


def run_packet(packet, argv):
    exact_object(packet, {"binding_json", "binding_sha256", "reader_source", "worker_sha256"})
    require(len(argv) == 4 and isinstance(packet["binding_json"], str), "host_arguments")
    encoded = packet["binding_json"].encode("utf-8")
    require(len(encoded) <= 16 * 1024 and sha256(encoded) == packet["binding_sha256"], "request_digest")
    request = strict_json(encoded)
    samples = validate_request(request)
    require(packet["worker_sha256"] == request["pins"]["worker_sha256"], "worker_pin")
    reader = load_reader(packet["reader_source"])
    root, ffmpeg, ffprobe = map(Path, argv[1:])
    name, canonical_name = media_names(request)
    media = checked_path(root, name)
    pins = request["pins"]
    tokens = []
    for path, expected in [(Path(sys.executable), pins["python_sha256"]), (ffmpeg, pins["ffmpeg_sha256"]),
                           (ffprobe, pins["ffprobe_sha256"]), (media, request["input_sha256"])]:
        _, token = reader._checked_file(path, expected)
        tokens.append((path, token))
    suffix_budget = 0
    decoded_frames = samples
    timing = None
    if request["role"] == "final_aac":
        entries = ("stream=index,codec_type,codec_name,profile,width,height,r_frame_rate,time_base,"
                   "start_pts,duration_ts,sample_rate,channels,nb_read_frames:"
                   "frame=media_type,stream_index,pts,best_effort_timestamp,nb_samples")
        raw_probe = reader.bounded_decoder([str(ffprobe), "-v", "error", "-count_frames", "-show_streams", "-show_frames",
            "-show_entries", entries, "-of", "json", str(media)], 4 * 1024 * 1024)
        probe = strict_json(raw_probe)
        raw_packets = reader.bounded_decoder([str(ffprobe), "-v", "error", "-show_packets", "-show_entries",
            "packet=stream_index,pts,dts,duration:packet_side_data=side_data_type,skip_samples,discard_padding",
            "-of", "json", str(media)], 4 * 1024 * 1024)
        probe["packets"] = strict_json(raw_packets).get("packets")
        clock = request["binding"]["clock"]
        timing = reader.validate_clock(probe, reader.MediaClock(frames=clock["video_frames"], width=request["width"],
            height=request["height"], fps_num=clock["fps_num"], fps_den=clock["fps_den"]))
        suffix_budget = timing["declared_decoder_suffix_budget_samples"]
        decoded_frames = timing["decoded_audio_samples_from_frames"]
    else:
        # Canonical reader parses the real RIFF chunks, not a presumed 44-byte header.
        pcm = reader.read_reference_wav(media, samples)
        if request["role"] == "canonical_premix":
            require_source_timing_signature(reader, pcm, request["binding"]["premix_pcm_sha256"])
    raw_float = reader.bounded_decoder(decode_argv(ffmpeg, media), (samples + suffix_budget) * 16)
    require(len(raw_float) == decoded_frames * 16, "float_decode_clock_mismatch")
    values = array.array("d", raw_float)
    if sys.byteorder != "little":
        values.byteswap()
    require(all(math.isfinite(value) for value in values), "nonfinite_decoded_sample")
    modes = {}
    raw_metadata = {}
    for mode in ("integrated", "programme_peak", "physical_peak"):
        raw = reader.bounded_decoder(meter_argv(ffmpeg, media, samples, mode), MAX_METADATA_BYTES)
        count = samples if mode == "integrated" else ((samples if mode == "programme_peak" else decoded_frames) + PEAK_FLUSH_SAMPLES)
        modes[mode] = parse_metadata(raw, count)
        raw_metadata[mode] = {"sha256": sha256(raw), "bytes": len(raw), "base64": base64.b64encode(raw).decode("ascii")}
    integrated = modes["integrated"][-1]["lavfi.r128.I"]
    # FFmpeg's ungated/silent initial value is -70, not defined programme loudness.
    integrated = integrated if integrated > -69.99 else None
    transfer = None
    if request["role"] == "mastered_wav":
        canonical = checked_path(root, canonical_name)
        _, token = reader._checked_file(canonical, request["binding"]["premix_wav_sha256"])
        tokens.append((canonical, token))
        transfer = verify_transfer(reader, request, canonical, media, samples)
    for path, token in tokens:
        reader._unchanged(path, token)
    report = {"schema": MEASURE_SCHEMA, "method": METHOD, "role": request["role"],
        "source_binding_sha256": request["source_binding_sha256"], "input_sha256": request["input_sha256"],
        "decoded_f64le_sha256": sha256(raw_float), "decoded_sample_frames": decoded_frames,
        "logical_sample_frames": samples, "declared_decoder_suffix_budget_samples": suffix_budget,
        "sample_rate": 48000, "channels": 2, "integrated_lufs": integrated,
        "programme_true_peak_dbtp_upper": peak_upper(modes["programme_peak"]),
        "physical_true_peak_dbtp_upper": peak_upper(modes["physical_peak"]),
        "integrated_uncertainty_lu": INTEGRATED_UNCERTAINTY_LU, "true_peak_uncertainty_db": TRUE_PEAK_UNCERTAINTY_DB,
        "programme_interval": [0, samples], "peak_flush_zero_samples": PEAK_FLUSH_SAMPLES,
        "complete_input_decoded": True, "pins": pins}
    return {"schema": RESULT_SCHEMA, "binding_sha256": packet["binding_sha256"], "reader_sha256": READER_SHA256,
        "worker_sha256": packet["worker_sha256"], "status": "MEASURED", "report": report,
        "transfer": transfer, "clock": timing, "raw_metadata": raw_metadata,
        "scope": "technical_measurement_only_no_export_listening_rights_or_editable_source_approval"}


def main(encoded_packet, compiled_reader_source=None):
    try:
        require(isinstance(encoded_packet, str) and len(encoded_packet) <= 128 * 1024)
        raw = base64.b64decode(encoded_packet, validate=True)
        require(len(raw) <= MAX_PACKET_BYTES)
        packet = strict_json(raw)
        if compiled_reader_source is not None:
            exact_object(packet, {"binding_json", "binding_sha256", "worker_sha256"})
            packet["reader_source"] = compiled_reader_source
        result = run_packet(packet, sys.argv)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        require(len(output) <= MAX_RESULT_BYTES)
    except Exception:
        print(json.dumps({"schema": RESULT_SCHEMA, "status": "REJECTED", "diagnostic": DIAGNOSTIC}))
        return 2
    sys.stdout.buffer.write(output + b"\n")
    return 0
