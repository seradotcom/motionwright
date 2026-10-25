"""Private, one-shot AAC frame-clock custody around existing native originals.

This module starts only two read-only ffprobe observations. It never renders,
runs a packet probe or PCM decoder, retries, admits a runtime, or grants media
acceptance. The existing selected-evidence seal authenticates custody separately.
"""
from __future__ import annotations

import base64
from fractions import Fraction
import json
from pathlib import Path

from ci_private_evidence import new_bytes, regular
from native_av_failure_guard import run_sanitized_json
import native_aac_clock_inspection as clock
import native_evidence_projection as core

SCHEMA = "motionwright.native.aac-clock-capture/1"
MAX_PROBE = clock.MAX_PROBE
MAX_REPORT = 128 * 1024
MAX_CARRIER = 5 * 1024 * 1024
MODES = ("default", "manual")
FRAME_ENTRIES = (
    "frame=media_type,stream_index,pts,best_effort_timestamp,nb_samples,channels,"
    "pkt_pos,pkt_size,sample_fmt,duration,pkt_duration:"
    "stream=index,codec_name,codec_type,sample_rate,channels,time_base,start_pts,"
    "start_time,duration_ts,duration,nb_frames,initial_padding,nb_read_packets"
)


def require(ok, code):
    core.require(ok, code)


def packed(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def desc(raw):
    return {"sha256": core.sha(raw), "bytes": len(raw)}


def encoded(raw, maximum):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, "clock-carrier-byte-budget")
    return {"encoding": "base64", **desc(raw),
            "data": base64.b64encode(raw).decode("ascii")}


def decoded(value, maximum):
    require(type(value) is dict and set(value) == {"encoding", "sha256", "bytes", "data"}
            and value["encoding"] == "base64" and type(value["data"]) is str
            and type(value["bytes"]) is int and 0 < value["bytes"] <= maximum
            and len(value["data"]) <= 4 * ((maximum + 2) // 3), "clock-carrier-fields")
    raw = base64.b64decode(value["data"], validate=True)
    require(base64.b64encode(raw).decode("ascii") == value["data"]
            and desc(raw) == {k: value[k] for k in ("sha256", "bytes")},
            "clock-carrier-original-changed")
    return raw


def frame_argv(ffprobe, master, mode):
    require(mode in MODES, "clock-mode")
    result = [str(ffprobe), "-v", "error"]
    if mode == "manual":
        result += ["-flags2", "+skip_manual"]
    return result + ["-select_streams", "a:0", "-show_frames", "-show_streams",
                     "-show_entries", FRAME_ENTRIES, "-of", "json", str(master)]


def derive_anchor(document, identity, *, scenario, phase):
    """Derive from the existing selected source, never a caller's numeric offset."""
    require(phase in ("original", "edited"), "clock-phase")
    if scenario == "repaired-g2":
        from native_mlt_evidence_selection import TIMELINE
        require(phase == "original", "clock-mlt-phase")
        value = core.project(document, TIMELINE)
        source = value["source"]
        segments = source["verified_video_segments"]
        require([s["output_start_frame"] for s in segments] == [0, 32]
                and [s["frame_count"] for s in segments] == [32, 1]
                and source["total_frames"] == value["frame_count"] == 33,
                "clock-verified-output-coverage")
        point = Fraction(segments[0]["output_start_frame"] * source["fps_den"],
                         source["fps_num"])
        kind, authorial = "NATIVE_VERIFIED_OUTPUT_TIMELINE", False
    else:
        require(scenario == "film180-g2", "clock-scenario")
        from native_film180_evidence_selection import validate_project
        value = validate_project(document, phase)
        start = value["scenes"][0]["start"]
        point = Fraction(int(start["num"]), int(start["den"]))
        kind, authorial = "SAVED_AUTHORIAL_PROJECT", True
    require(point >= 0 and (point * clock.RATE).denominator == 1, "clock-source-grid")
    return {"kind": kind, "document": identity,
            "presentation_start": {"num": point.numerator, "den": point.denominator},
            "authorial_project_read_by_capture": authorial,
            "source_reopened_by_this_capture": False}


def carrier_policy():
    """Closed selected role; base64 preserves original JSON formatting and bytes."""
    from native_evidence_projection import Object, Field, String, Number, Literal, Array, Nullable
    def obj(**fields):
        return Object({k: Field(v) for k, v in fields.items()})
    sha = String(64, pattern=r"[0-9a-f]{64}")
    count = Number(0, 2**63 - 1, integer=True)
    identity = obj(sha256=sha, bytes=count)
    def raw(maximum):
        return obj(encoding=Literal("base64"), sha256=sha,
                   bytes=Number(1, maximum, integer=True),
                   data=String(4 * ((maximum + 2) // 3), pattern=r"[A-Za-z0-9+/]*={0,2}"))
    terminal = obj(
        argv=Array(String(4096), 20, 1), timeout_seconds=Literal(60),
        exit_code=Nullable(Number(-255, 255, integer=True)), deadline_exceeded=bool,
        error_class=Nullable(String(128, pattern=r"[A-Za-z_][A-Za-z0-9_]*")),
        automatic_retry=Literal(False), stdout=identity, stderr=identity,
        stdout_original_included=bool, stderr_original_included=Literal(False),
        terminal_captured_before_json_parse=Literal(True))
    anchor = obj(
        kind=String(values=("NATIVE_VERIFIED_OUTPUT_TIMELINE", "SAVED_AUTHORIAL_PROJECT")),
        document=identity,
        presentation_start=obj(num=count, den=Number(1, 2**63 - 1, integer=True)),
        authorial_project_read_by_capture=bool, source_reopened_by_this_capture=Literal(False))
    return obj(
        schema=Literal(SCHEMA), phase=String(values=("original", "edited")),
        scenario=String(values=("repaired-g2", "film180-g2")),
        state=String(values=("CAPTURED", "INCOMPLETE")),
        anchor=anchor, original_wav=identity, master=identity,
        decoded_pcm=Nullable(identity), ffprobe=identity,
        ffprobe_path=String(4096), master_path=String(4096),
        master_unchanged_after_observation=bool, ffprobe_unchanged_after_observation=bool,
        packets=Nullable(raw(MAX_PROBE)),
        captures=Array(obj(mode=String(values=MODES), terminal=Nullable(terminal),
                           runner_failed=bool, original_stdout=Nullable(raw(MAX_PROBE))), 2, 2),
        report=Nullable(raw(MAX_REPORT)),
        report_status=String(values=("NOT_RUN", "FAIL", "INSUFFICIENT_EVIDENCE",
                                    "CONSISTENT_WITHIN_SCOPE")),
        original_capture_authentication=Literal("EXTERNAL_CUSTODY_REQUIRED"),
        original_pcm_decoder_equivalence=Literal("NOT_ATTESTED_BY_THIS_CAPTURE"),
        native_operation_started=Literal(False), native_operation_retried=Literal(False),
        packet_probe_reinvoked=Literal(False), pcm_decoder_reinvoked=Literal(False),
        media_reencoded_or_trimmed=Literal(False),
        physical_AV_master_acceptance=Literal(False), human_acceptance=Literal(False),
        release_authorized=Literal(False))


def _capture_mode(evidence_dir, phase, mode, ffprobe, master, env, runner):
    row = {"mode": mode, "terminal": None, "runner_failed": False,
           "original_stdout": None}
    label = phase + "-aac-" + mode + "-frames"
    argv = frame_argv(ffprobe, master, mode)
    callback_count = 0

    def capture(event):
        nonlocal callback_count
        require(callback_count == 0, "clock-duplicate-capture-callback")
        callback_count += 1
        stdout, stderr = event["stdout"], event["stderr"]
        require(type(stdout) is bytes and type(stderr) is bytes, "clock-original-stream-types")
        require(event["argv"] == argv and type(event["timeout_seconds"]) is int
                and event["timeout_seconds"] == 60 and event["automatic_retry"] is False
                and (event["exit_code"] is None or type(event["exit_code"]) is int)
                and type(event["deadline_exceeded"]) is bool,
                "clock-original-invocation-binding")
        kept = 0 < len(stdout) <= MAX_PROBE
        # No truncated stream can become an original. Oversized streams retain
        # their observed digest/length and make the capture incomplete.
        if kept:
            new_bytes(evidence_dir / (label + ".stdout"), stdout)
        if len(stderr) <= 64 * 1024:
            new_bytes(evidence_dir / (label + ".stderr"), stderr)
        terminal = {
            "argv": argv, "timeout_seconds": 60, "exit_code": event["exit_code"],
            "deadline_exceeded": event["deadline_exceeded"],
            "error_class": event["error_class"], "automatic_retry": False,
            "stdout": desc(stdout), "stderr": desc(stderr),
            "stdout_original_included": kept, "stderr_original_included": False,
            "terminal_captured_before_json_parse": True,
        }
        new_bytes(evidence_dir / (label + ".terminal.json"), packed(terminal))
        # Assign only after durable terminal custody. The caller does not
        # infer a terminal when the callback or its exclusive write failed.
        row["terminal"] = terminal
        if kept:
            row["original_stdout"] = encoded(stdout, MAX_PROBE)

    try:
        runner(argv, env=env, timeout=60, capture=capture)
        require(callback_count == 1 and row["terminal"] is not None, "clock-missing-terminal-callback")
    except Exception:
        row["runner_failed"] = True
    return row


def _successful(row):
    terminal = row["terminal"]
    return (terminal is not None and row["runner_failed"] is False
            and type(terminal["exit_code"]) is int and terminal["exit_code"] == 0
            and terminal["deadline_exceeded"] is False
            and terminal["error_class"] is None and row["original_stdout"] is not None)


def retain_clock_diagnostic(*, evidence_dir, phase, scenario, master, master_sha256,
                            ffprobe, env, source_document, original_wav, packets,
                            decoded_pcm, runner=run_sanitized_json):
    """Called in finally after the original packet/PCM attempts, never instead of them."""
    evidence_dir, master, ffprobe = map(Path, (evidence_dir, master, ffprobe))
    require(phase in ("original", "edited") and scenario in ("repaired-g2", "film180-g2"),
            "clock-fixed-source-scope")
    # This exclusive marker latches the entire diagnostic before any launch.
    # Existing or partially written state is never reused as another attempt.
    new_bytes(evidence_dir / (phase + "-aac-clock-attempt.json"), packed({
        "schema": "motionwright.native.aac-clock-attempt/1", "phase": phase,
        "scenario": scenario, "automatic_retry": False, "native_operation_started": False}))
    source_raw = regular(Path(source_document), core.MAX_JSON)[0]
    anchor = derive_anchor(core.strict(source_raw), desc(source_raw), scenario=scenario, phase=phase)
    wav = regular(Path(original_wav), clock.MAX_SOURCE_FRAMES * 4 + 65536)[0]
    master_raw = regular(master, clock.MAX_MASTER)[0]
    require(core.sha(master_raw) == master_sha256, "clock-native-master-changed")
    executable = regular(ffprobe, 64 * 1024 * 1024)[0]
    rows = [_capture_mode(evidence_dir, phase, mode, ffprobe, master, env, runner) for mode in MODES]
    try:
        master_same = regular(master, clock.MAX_MASTER)[0] == master_raw
    except (OSError, ValueError):
        master_same = False
    try:
        executable_same = regular(ffprobe, 64 * 1024 * 1024)[0] == executable
    except (OSError, ValueError):
        executable_same = False
    try:
        packet_raw = regular(Path(packets), MAX_PROBE)[0]
        require(len(packet_raw) > 0, "clock-empty-original-packets")
    except (OSError, ValueError):
        packet_raw = None
    try:
        pcm = regular(Path(decoded_pcm), (clock.MAX_SOURCE_FRAMES + clock.MAX_PADDING_FRAMES) * 4)[0]
        require(len(pcm) > 0, "clock-empty-original-pcm")
    except (OSError, ValueError):
        pcm = None
    report = None
    eligible = (master_same and executable_same and packet_raw is not None and pcm is not None
                and all(_successful(row) for row in rows))
    if eligible:
        try:
            inputs = dict(original_wav=wav, master=master_raw, packets=packet_raw,
                          decoded_pcm=pcm,
                          default_frames=decoded(rows[0]["original_stdout"], MAX_PROBE),
                          manual_frames=decoded(rows[1]["original_stdout"], MAX_PROBE))
            binding = {name: desc(raw) for name, raw in inputs.items()}
            binding.update(schema=clock.BINDING_SCHEMA, presentation_start=anchor["presentation_start"])
            report = clock.inspect_originals(binding=binding, **inputs)
        except (OSError, ValueError, AssertionError, OverflowError, RecursionError):
            report = None
    value = {
        "schema": SCHEMA, "phase": phase, "scenario": scenario,
        "state": "CAPTURED" if report is not None else "INCOMPLETE",
        "anchor": anchor, "original_wav": desc(wav), "master": desc(master_raw),
        "decoded_pcm": desc(pcm) if pcm is not None else None,
        "ffprobe": desc(executable), "ffprobe_path": str(ffprobe), "master_path": str(master),
        "master_unchanged_after_observation": master_same,
        "ffprobe_unchanged_after_observation": executable_same,
        "packets": encoded(packet_raw, MAX_PROBE) if packet_raw is not None else None,
        "captures": rows, "report": encoded(packed(report), MAX_REPORT) if report is not None else None,
        "report_status": report["status"] if report is not None else "NOT_RUN",
        "original_capture_authentication": "EXTERNAL_CUSTODY_REQUIRED",
        "original_pcm_decoder_equivalence": "NOT_ATTESTED_BY_THIS_CAPTURE",
        "native_operation_started": False, "native_operation_retried": False,
        "packet_probe_reinvoked": False, "pcm_decoder_reinvoked": False,
        "media_reencoded_or_trimmed": False, "physical_AV_master_acceptance": False,
        "human_acceptance": False, "release_authorized": False,
    }
    core.project(value, carrier_policy(), exact=True)
    raw = packed(value)
    require(len(raw) <= MAX_CARRIER, "clock-complete-carrier-budget")
    new_bytes(evidence_dir / (phase + "-aac-clock-capture.json"), raw)
    return value


def verify_selected_carrier(value, *, phase, scenario, source_document, source_identity,
                            original_wav, master, decoded_pcm, packet_identity):
    """Pure readback from selected originals; no lookup, subprocess or authority mint."""
    core.project(value, carrier_policy(), exact=True)
    require(value["phase"] == phase and value["scenario"] == scenario
            and value["state"] == "CAPTURED", "clock-selected-capture-incomplete")
    anchor = derive_anchor(source_document, source_identity, phase=phase, scenario=scenario)
    require(value["anchor"] == anchor, "clock-selected-source-anchor-changed")
    for key, raw in (("original_wav", original_wav), ("master", master), ("decoded_pcm", decoded_pcm)):
        require(value[key] == desc(raw), "clock-selected-original-changed")
    require(value["master_unchanged_after_observation"] is True
            and value["ffprobe_unchanged_after_observation"] is True,
            "clock-observed-input-changed")
    require([row["mode"] for row in value["captures"]] == list(MODES),
            "clock-mode-order-or-duplicate")
    inputs = dict(original_wav=original_wav, master=master, decoded_pcm=decoded_pcm)
    inputs["packets"] = decoded(value["packets"], MAX_PROBE)
    require(desc(inputs["packets"]) == packet_identity, "clock-packet-original-not-the-selected-probe")
    for row in value["captures"]:
        require(_successful(row), "clock-original-terminal-not-successful")
        terminal = row["terminal"]
        require(terminal["argv"] == frame_argv(value["ffprobe_path"], value["master_path"], row["mode"]),
                "clock-decode-mode-argv-changed")
        raw = decoded(row["original_stdout"], MAX_PROBE)
        require(terminal["stdout"] == desc(raw) and terminal["stdout_original_included"] is True,
                "clock-stdout-original-terminal-binding")
        inputs[row["mode"] + "_frames"] = raw
    binding = {name: desc(raw) for name, raw in inputs.items()}
    binding.update(schema=clock.BINDING_SCHEMA, presentation_start=anchor["presentation_start"])
    expected = clock.inspect_originals(binding=binding, **inputs)
    observed_raw = decoded(value["report"], MAX_REPORT)
    require(observed_raw == packed(expected) and value["report_status"] == expected["status"],
            "clock-inspection-does-not-match-originals")
    return expected
