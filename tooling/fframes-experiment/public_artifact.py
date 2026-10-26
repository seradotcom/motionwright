#!/usr/bin/env python3
"""Publish bounded facts and one existing-format encrypted original capsule.

No process is launched here. Successful facts require the retained complete
captures, decoded originals, source binding and a successful single seal. CI
upload is performed by the existing workflow and is never asserted locally.
"""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import re
import struct
import sys
import tarfile
import wave

import measure as experiment

SCHEMA = "motionwright.v05-fframes-public-benchmark/2"
PIN = experiment.PIN
HEX40, HEX64 = experiment.HEX40, experiment.HEX64
PHASES = experiment.BASELINE_PHASES
ALL_PHASES = experiment.OBSERVED_PHASES + (
    "motionwright_source_head", "motionwright_source_manifest", "rustc_version",
    "ffmpeg_version", "ffprobe_version", "architecture", "source_checkout",
    "exact_source_checkout", "upstream_source_head", "upstream_tracked_before",
    "cli_startup_0", "cli_startup_1", "upstream_tracked_after",
)
SEAL_FILE = "fframes-originals.mwci-seal"


class Refused(ValueError):
    pass


def require(ok: bool, why: str) -> None:
    if not ok:
        raise Refused(why)


def strict_object(raw: bytes) -> dict:
    try:
        return experiment.strict_object(raw)
    except (AssertionError, ValueError, UnicodeError, TypeError) as error:
        raise Refused("invalid_or_duplicate_upstream_evidence") from error


def member(root: Path, relative: str, limit: int, witnesses: dict) -> bytes:
    require(type(relative) is str and "\\" not in relative
            and not relative.startswith("/") and 0 < len(relative) <= 500
            and all(part not in ("", ".", "..") for part in relative.split("/")),
            "unsafe_original_member")
    raw = experiment.read_original(root / relative, limit)
    witness = {"bytes": len(raw), "sha256": experiment.sha(raw)}
    require(relative not in witnesses or witnesses[relative] == witness,
            "original_changed_between_reads")
    witnesses[relative] = witness
    return raw


def verified_capture(root: Path, label: str, metric: dict, witnesses: dict) -> dict:
    require(label in ALL_PHASES and type(metric) is dict
            and metric.get("capture") == "processes/" + label
            and metric.get("originals_retained") is True,
            "invalid_source_capture")
    prefix = "processes/" + label + "/"
    argv_raw = member(root, prefix + "argv.json", 64 * 1024, witnesses)
    capture_raw = member(root, prefix + "capture.json", 128 * 1024, witnesses)
    require(experiment.sha(argv_raw) == metric.get("argv_sha256")
            and experiment.sha(capture_raw) == metric.get("capture_sha256"),
            "source_capture_digest")
    request, capture = strict_object(argv_raw), strict_object(capture_raw)
    require(type(request.get("argv")) is list and 0 < len(request["argv"]) <= 64
            and all(type(arg) is str and 0 < len(arg) <= 4096 and "\0" not in arg
                    for arg in request["argv"])
            and type(request.get("cwd")) is str
            and request.get("automatic_retry") is False
            and type(request.get("timeout_seconds")) is int
            and 0 < request["timeout_seconds"] <= 1350
            and type(request.get("stdout_limit_bytes")) is int
            and 0 < request["stdout_limit_bytes"] <= 8 * 1024 * 1024
            and request.get("stderr_limit_bytes") == experiment.MAX_STDERR
            and request.get("cleanup_allowance_seconds") == 10,
            "source_capture_request")
    require(experiment.complete_capture(capture) and "raised_error" not in capture,
            "source_capture_incomplete")
    stdout = member(root, prefix + "stdout.bin", request["stdout_limit_bytes"], witnesses)
    stderr = member(root, prefix + "stderr.bin", experiment.MAX_STDERR, witnesses)
    terminal = capture["terminal"]
    for name, raw in (("stdout", stdout), ("stderr", stderr)):
        require(capture.get(name + "_sha256") == experiment.sha(raw)
                and type(capture.get(name + "_retained_bytes")) is int
                and capture[name + "_retained_bytes"] == len(raw)
                and type(terminal.get(name + "_bytes")) is int
                and terminal[name + "_bytes"] == len(raw), "source_capture_stream_binding")
    require(terminal.get("stderr_sha256") == experiment.sha(stderr)
            and type(metric.get("exit_code")) is int
            and metric.get("exit_code") == terminal["exit_code"]
            and type(metric.get("elapsed_seconds")) in (int, float)
            and type(terminal.get("elapsed_seconds")) in (int, float)
            and math.isfinite(terminal["elapsed_seconds"])
            and 0 <= terminal["elapsed_seconds"] <= request["timeout_seconds"] + 15
            and metric.get("elapsed_seconds") == terminal["elapsed_seconds"],
            "invalid_source_timing")
    stdin = None
    if request.get("stdin_bytes") is not None:
        require(type(request["stdin_bytes"]) is int
                and 0 < request["stdin_bytes"] <= 32 * 1024 * 1024, "source_stdin_budget")
        stdin = member(root, prefix + "stdin.bin", 32 * 1024 * 1024, witnesses)
        require(len(stdin) == request["stdin_bytes"] == capture.get("stdin_bytes")
                and experiment.sha(stdin) == request.get("stdin_sha256")
                and experiment.sha(stdin) == capture.get("stdin_sha256"), "source_stdin_binding")
    else:
        require(request.get("stdin_sha256") is None and capture.get("stdin_bytes") == 0
                and capture.get("stdin_sha256") is None, "source_stdin_absence")
    return {"request": request, "capture": capture, "stdout": stdout,
            "stderr": stderr, "stdin": stdin}


def _png(root: Path, value: dict, capture: dict, folder: str, witnesses: dict) -> dict:
    require(type(value) is dict and value.get("pixel_format") == "rgba"
            and value.get("transport_crc_checked") is True
            and (value.get("width"), value.get("height")) == (experiment.WIDTH, experiment.HEIGHT),
            "unverified_fframes_decoded_png")
    path = value.get("encoded_path")
    require(type(path) is str and len(path.split("/")) == 2
            and path.split("/")[0] == folder and path.endswith(".png"), "preview_original_path")
    raw = member(root, path, experiment.MAX_PNG, witnesses)
    metadata = experiment.validate_png_crc_bytes(raw, experiment.WIDTH, experiment.HEIGHT,
                                                  max_bytes=experiment.MAX_PNG)
    require(metadata["animated"] is False and value.get("sha256") == experiment.sha(raw)
            and value.get("bytes") == len(raw) and capture["stdin"] == raw
            and capture["stderr"] == b"", "preview_encoded_original_binding")
    rgba = capture["stdout"]
    require(len(rgba) == experiment.WIDTH * experiment.HEIGHT * 4 == value.get("rgba_bytes")
            and experiment.sha(rgba) == value.get("rgba_sha256"), "preview_decoded_original_binding")
    args = capture["request"]["argv"]
    require(args[1:] == ["-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                        "-noautorotate", "-f", "image2pipe", "-c:v", "png", "-i", "pipe:0",
                        "-map", "0:v:0", "-an", "-sn", "-dn", "-pix_fmt", "rgba",
                        "-c:v", "rawvideo", "-f", "rawvideo", "pipe:1"],
            "png_decoder_argv")
    return {"encoded_sha256": experiment.sha(raw), "rgba_sha256": experiment.sha(rgba),
            "width": experiment.WIDTH, "height": experiment.HEIGHT,
            "independent_rgba_decode": "OBSERVED"}


def _video(root: Path, value: dict, captures: dict, witnesses: dict) -> dict:
    require(type(value) is dict and value.get("encoded_path") == "upstream-source-preview.mp4"
            and value.get("retained_descriptor_checked_before_after") is True
            and value.get("decode_capture") == "video_full_framehash"
            and value.get("metadata_capture") == "video_metadata", "video_original_contract")
    raw = member(root, value["encoded_path"], experiment.MAX_MOVIE, witnesses)
    require(len(raw) == value.get("bytes") and len(raw) > 1024
            and experiment.sha(raw) == value.get("sha256"), "video_original_binding")
    meta, decoded = captures["video_metadata"], captures["video_full_framehash"]
    witness = decoded["request"].get("file_input")
    require(type(witness) is dict and set(witness) == {
                "kind", "descriptor_path", "relative_path", "bytes", "sha256"}
            and witness["kind"] == "retained_regular_descriptor"
            and witness["relative_path"] == value["encoded_path"]
            and witness["bytes"] == len(raw) and witness["sha256"] == experiment.sha(raw)
            and type(witness["descriptor_path"]) is str
            and re.fullmatch(r"/proc/[1-9][0-9]*/fd/[0-9]+", witness["descriptor_path"]) is not None
            and meta["request"].get("file_input") == witness, "video_descriptor_binding")
    require(decoded["request"]["argv"][1:] == [
                "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-noautorotate",
                "-i", witness["descriptor_path"], "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vsync", "0", "-pix_fmt", "rgba", "-c:v", "rawvideo", "-f", "framehash",
                "-hash", "sha256", "pipe:1"] and decoded["stderr"] == b"",
            "full_video_decode_argv")
    parsed = experiment.parse_framehash(decoded["stdout"], experiment.WIDTH, experiment.HEIGHT,
                                         experiment.VIDEO_FRAMES)
    require(parsed == value.get("decode"), "video_decoded_frames_binding")
    metadata = strict_object(meta["stdout"])
    require(type(metadata.get("streams")) is list and len(metadata["streams"]) == 1,
            "video_metadata_stream_roster")
    stream = metadata["streams"][0]
    require(stream.get("codec_type") == "video" and stream.get("codec_name") == "h264"
            and (stream.get("width"), stream.get("height")) == (experiment.WIDTH, experiment.HEIGHT)
            and stream.get("r_frame_rate") == "30/1"
            and stream.get("nb_frames") == str(experiment.VIDEO_FRAMES), "video_metadata_binding")
    duration = float(metadata["format"]["duration"])
    require(math.isfinite(duration) and abs(duration - 1.0) <= 1 / experiment.FPS
            and value.get("duration_seconds") == duration, "video_duration_binding")
    return {"encoded_sha256": experiment.sha(raw), "bytes": len(raw), "codec": "h264",
            "width": experiment.WIDTH, "height": experiment.HEIGHT,
            "decoded_frames": parsed["frame_count"], "time_base": parsed["time_base"],
            "framehash_stdout_sha256": parsed["framehash_stdout_sha256"],
            "independent_full_decode": "OBSERVED"}


def _source_wav(raw: bytes, frames: int, decoded: bytes) -> None:
    with wave.open(io.BytesIO(raw), "rb") as stream:
        require((stream.getnchannels(), stream.getsampwidth(), stream.getframerate(),
                 stream.getnframes(), stream.getcomptype()) ==
                (2, 2, experiment.AUDIO_RATE, frames, "NONE"), "synthetic_wav_contract")
        samples = stream.readframes(frames + 1)
    require(len(samples) == frames * 4 and len(decoded) == frames * 8, "synthetic_pcm_size")
    pairs = zip(struct.iter_unpack("<h", samples), struct.iter_unpack("<f", decoded))
    require(all(math.isfinite(f[0]) and abs(f[0] - s[0] / 32768) <= 1e-7 for s, f in pairs),
            "synthetic_pcm_original_binding")


def _audio(root: Path, value: dict, captures: dict, witnesses: dict) -> dict:
    require(type(value) is dict and value.get("status") == "OBSERVED_SYNTHETIC_STEREO_MIX"
            and value.get("human_audio_acceptance") is False
            and value.get("baseline_hello_world_modified") is False
            and value.get("baseline_build_timings_reused") is False
            and value.get("probe_source_path") == "audio/probe.rs"
            and value.get("fixture_path") == "audio/fixture.json"
            and value.get("encoded_path") == "audio/native-mix.wav", "synthetic_audio_scope")
    probe = member(root, "audio/probe.rs", 32 * 1024, witnesses)
    require(probe == experiment.AUDIO_PROBE_RS.encode()
            and experiment.sha(probe) == value.get("probe_source_sha256"), "synthetic_probe_source")
    fixture_raw = member(root, "audio/fixture.json", 32 * 1024, witnesses)
    fixture = strict_object(fixture_raw)
    require(experiment.sha(fixture_raw) == value.get("fixture_sha256")
            and fixture.get("schema") == "motionwright.fframes-synthetic-audio-fixture/1"
            and fixture.get("scope") == "synthetic_stereo_gain_and_timeline_mix_only"
            and fixture.get("sample_rate") == experiment.AUDIO_RATE
            and fixture.get("channels") == 2 and fixture.get("output_frames") == experiment.AUDIO_FRAMES
            and fixture.get("limiter") is False and fixture.get("declick") is False
            and fixture.get("audio_acceptance") is False
            and fixture.get("tracks") == [
                {"file": "bed.wav", "start_frame": 0, "end_frame": 96000, "gain": 0.5},
                {"file": "cue.wav", "start_frame": 24000, "end_frame": 72000, "gain": 0.25}],
            "synthetic_audio_fixture")
    pcm = {}
    for name, label, frames in (("bed.wav", "audio_bed_pcm_decode", 96000),
                                 ("cue.wav", "audio_cue_pcm_decode", 48000),
                                 ("native-mix.wav", "audio_mix_pcm_decode", 96000)):
        relative = "audio/" + (name if name == "native-mix.wav" else "inputs/" + name)
        raw = member(root, relative, experiment.MAX_AUDIO, witnesses)
        capture = captures[label]
        require(capture["stdin"] == raw and capture["stderr"] == b""
                and capture["request"]["argv"][1:] == [
                    "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                    "-f", "wav", "-i", "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn",
                    "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"], "audio_decode_binding")
        pcm[name] = capture["stdout"]
        if name != "native-mix.wav":
            require(fixture.get("originals", {}).get(name) == {
                "bytes": len(raw), "sha256": experiment.sha(raw)}, "synthetic_audio_input_digest")
            _source_wav(raw, frames, pcm[name])
        else:
            require(value.get("original_wav_sha256") == experiment.sha(raw)
                    and value.get("original_wav_bytes") == len(raw), "native_mix_original_digest")
    compared = experiment.compare_audio_pcm(pcm["bed.wav"], pcm["cue.wav"], pcm["native-mix.wav"])
    require(all(value.get(key) == expected for key, expected in compared.items()),
            "native_audio_measurement_binding")
    metadata = strict_object(captures["audio_mix_metadata"]["stdout"])
    require(type(metadata.get("streams")) is list and len(metadata["streams"]) == 1,
            "native_audio_metadata_roster")
    stream = metadata["streams"][0]
    require(stream.get("codec_type") == "audio" and stream.get("codec_name") == "pcm_f32le"
            and stream.get("sample_rate") == "48000" and stream.get("channels") == 2
            and abs(float(stream.get("duration", 0)) - 2.0) <= 1 / experiment.AUDIO_RATE,
            "native_audio_metadata")
    cli = strict_object(captures["native_audio_mix_render"]["stdout"])
    require(cli.get("missing_files") == [] and cli.get("start_seconds") == 0.0
            and cli.get("end_seconds") == 2.0, "native_audio_cli_result")
    return {**compared, "original_wav_sha256": value["original_wav_sha256"],
            "fixture_sha256": experiment.sha(fixture_raw),
            "probe_source_sha256": experiment.sha(probe),
            "observation": "SYNTHETIC_STEREO_MIX_ONLY"}


def validate_commands(private: Path, captures: dict) -> None:
    argv = {key: value["request"]["argv"] for key, value in captures.items()}
    clone = argv["source_checkout"]
    require(len(clone) == 6 and clone[:5] == ["git", "clone", "--quiet", "--filter=blob:none", experiment.REPO]
            and Path(clone[5]).is_absolute(), "upstream_clone_argv")
    checkout = Path(clone[5])
    source_head = argv["motionwright_source_head"]
    require(len(source_head) == 5 and source_head[:2] == ["git", "-C"]
            and Path(source_head[2]).is_absolute() and source_head[3:] == ["rev-parse", "HEAD"],
            "motionwright_source_argv")
    require(argv["motionwright_source_manifest"] ==
            ["git", "-C", source_head[2], "show", "HEAD:Cargo.toml"], "motionwright_manifest_argv")
    require(argv["exact_source_checkout"] == ["git", "-C", str(checkout), "checkout", "--detach", "--quiet", PIN]
            and argv["upstream_source_head"] == ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            "upstream_checkout_argv")
    require(all(argv[key] == ["git", "-C", str(checkout), "diff", "--exit-code", "HEAD", "--"]
                for key in ("upstream_tracked_before", "upstream_tracked_after")), "upstream_diff_argv")
    build = ["cargo", "build", "--locked", "--quiet", "--release", "--manifest-path",
             str(checkout / "Cargo.toml"), "--target-dir", str(checkout / "target"), "-p", experiment.PKG]
    require(argv["cold_release_build"] == argv["warm_release_build"] == build + ["--bin", experiment.APP]
            and argv["audio_probe_build"] == build + ["--example", experiment.AUDIO_APP],
            "separate_baseline_and_audio_build_argv")
    binary = str(checkout / "target/release" / experiment.APP)
    require(argv["cli_startup_0"] == argv["cli_startup_1"] == [binary, "--help"], "baseline_startup_argv")
    for key, directory in (("cold_headless_native_preview", "frame-preview"),
                           ("warm_headless_native_preview", "frame-preview-repeat")):
        require(argv[key] == [binary, "frame", "1s", "-o", str(private / directory), "--scale", "0.25"],
                "baseline_frame_argv")
    require(argv["cold_bounded_video_render"] == [binary, "render", "0s..1s", "--draft", "--scale", "0.25",
                                                 "-o", str(private / "upstream-source-preview.mp4")],
            "baseline_video_render_argv")
    require(argv["native_audio_mix_render"] == [str(checkout / "target/release/examples" / experiment.AUDIO_APP),
                                                "--json", "--assets", str(private / "audio/inputs"),
                                                "audio", "render", "0s..2s", "--float", "-o",
                                                str(private / "audio/native-mix.wav")], "native_audio_render_argv")
    for tool, phases in (("ffmpeg", ("cold_png_rgba_decode", "warm_png_rgba_decode", "video_full_framehash",
                                    "audio_mix_pcm_decode", "audio_bed_pcm_decode", "audio_cue_pcm_decode")),
                         ("ffprobe", ("video_metadata", "audio_mix_metadata"))):
        version = argv[tool + "_version"]
        require(len(version) == 2 and version[1] == "-version" and Path(version[0]).is_absolute()
                and Path(version[0]).name == tool and all(argv[phase][0] == version[0] for phase in phases),
                "independent_decoder_command_binding")
    file_witness = captures["video_metadata"]["request"].get("file_input")
    require(type(file_witness) is dict, "video_descriptor_witness_required")
    descriptor = file_witness.get("descriptor_path")
    require(argv["video_metadata"][1:] == ["-v", "error", "-show_entries",
                "stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames",
                "-show_entries", "format=duration", "-of", "json", descriptor]
            and argv["audio_mix_metadata"][1:] == ["-v", "error", "-show_entries",
                "stream=codec_name,codec_type,sample_rate,channels,duration", "-of", "json",
                str(private / "audio/native-mix.wav")], "metadata_probe_argv")


def validate_originals(private: Path, source_sha: str) -> tuple[dict, dict]:
    witnesses: dict = {}
    result = strict_object(member(private, "result.json", 256 * 1024, witnesses))
    require(result.get("schema") == experiment.EXPERIMENT_SCHEMA
            and result.get("status") == "EXPERIMENTAL_UPSTREAM_SOURCE_RENDER_PASS"
            and result.get("motionwright_sha") == source_sha and result.get("fframes_exact_sha") == PIN
            and result.get("fframes_license") == "MIT" and result.get("fframes_repository") == experiment.REPO
            and result.get("runtime") == "disposable_ubuntu_24_04"
            and all(result.get(key) is False for key in (
                "source_code_admitted_to_motionwright", "semwright_driver_host_executed",
                "owner_install_permission", "backend_release_admitted", "artistic_quality_reviewed",
                "private_originals_upload_confirmed"))
            and result.get("hyperframes_same_fixture_comparison") == "NOT_RUN"
            and result.get("interactive_gpu_preview") == "NOT_RUN_HEADLESS_CI"
            and result.get("audio_score_composition") == "OBSERVED_SYNTHETIC_STEREO_MIX"
            and all(result.get(key) is True for key in (
                "cold_build_measured", "warm_build_measured", "frame_preview_measured",
                "full_video_render_measured")), "unverified_or_admitted_fframes_experiment")
    metrics = result.get("measurements")
    require(type(metrics) is dict and set(metrics) == set(ALL_PHASES), "source_process_roster")
    captures = {name: verified_capture(private, name, metrics[name], witnesses) for name in ALL_PHASES}
    validate_commands(private, captures)
    require(captures["motionwright_source_head"]["stdout"] == (source_sha + "\n").encode()
            and captures["upstream_source_head"]["stdout"] == (PIN + "\n").encode()
            and captures["upstream_tracked_before"]["stdout"] == b""
            and captures["upstream_tracked_after"]["stdout"] == b"", "source_head_or_tracked_diff")
    manifest = member(private, "motionwright-Cargo.toml", 32 * 1024, witnesses)
    require(captures["motionwright_source_manifest"]["stdout"] == manifest
            and result.get("source_manifest_sha256") == experiment.sha(manifest), "source_manifest_binding")
    sdk = experiment.declared_sdk_pin(manifest)
    require(result.get("declared_semwright_sha") == sdk, "declared_sdk_pin_binding")
    require(type(result.get("independent_tools")) is dict
            and set(result["independent_tools"]) == {"ffmpeg_sha256", "ffprobe_sha256"}
            and all(type(value) is str and HEX64.fullmatch(value) is not None
                    for value in result["independent_tools"].values())
            and type(result.get("binary_sha256")) is str
            and HEX64.fullmatch(result["binary_sha256"]) is not None,
            "independent_binary_digest_contract")
    preview = _png(private, result.get("preview_png"), captures["cold_png_rgba_decode"],
                   "frame-preview", witnesses)
    repeat = _png(private, result.get("repeated_preview_png"), captures["warm_png_rgba_decode"],
                  "frame-preview-repeat", witnesses)
    require(captures["cold_png_rgba_decode"]["stdout"] == captures["warm_png_rgba_decode"]["stdout"]
            and result.get("repeated_decoded_rgba_identical") is True
            and result.get("repeated_encoded_png_identical") is
                (preview["encoded_sha256"] == repeat["encoded_sha256"]), "repeated_pixel_comparison")
    video = _video(private, result.get("video"), captures, witnesses)
    audio = _audio(private, result.get("audio"), captures, witnesses)
    return {"source_preview": preview, "repeated_preview": repeat, "source_one_second_video": video,
            "synthetic_audio_observation": audio, "declared_semwright_sha": sdk,
            "actual_measured_seconds": {name: metrics[name]["elapsed_seconds"] for name in PHASES},
            "synthetic_audio_build_seconds": metrics["audio_probe_build"]["elapsed_seconds"],
            "synthetic_audio_render_seconds": metrics["native_audio_mix_render"]["elapsed_seconds"]}, witnesses


def _snapshot_matches(raw: bytes, witnesses: dict) -> bool:
    # Verify the just-captured archive in memory; never recapture or extract it.
    if not witnesses:
        return False
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        require(len(names) == len(set(names)), "duplicate_snapshot_member")
        index = {item.name: item for item in members}
        for relative, witness in witnesses.items():
            item = index.get("fframes/" + relative)
            if item is None or not item.isfile() or item.size != witness["bytes"]:
                return False
            with archive.extractfile(item) as stream:
                data = stream.read(item.size + 1)
            if len(data) != item.size or experiment.sha(data) != witness["sha256"]:
                return False
    return True


def _seal_source_binding(private: Path, source_sha: str, context: dict) -> dict:
    """Source-only evidence remains usable when a later native phase failed."""
    try:
        witnesses = {}
        result = strict_object(member(private, "result.json", 256 * 1024, witnesses))
        require(result.get("motionwright_sha") == source_sha, "seal_source_result")
        metrics = result.get("measurements", {})
        head = verified_capture(private, "motionwright_source_head",
                                metrics.get("motionwright_source_head"), witnesses)
        manifest_capture = verified_capture(private, "motionwright_source_manifest",
                                            metrics.get("motionwright_source_manifest"), witnesses)
        manifest = member(private, "motionwright-Cargo.toml", 32 * 1024, witnesses)
        bound = (head["stdout"] == (source_sha + "\n").encode()
                 and manifest_capture["stdout"] == manifest
                 and result.get("source_manifest_sha256") == experiment.sha(manifest)
                 and experiment.declared_sdk_pin(manifest) == context["semwright_sha"])
        return witnesses if bound else {}
    except Exception:
        # This is a truthful classification, not a substitute source receipt.
        return {}


def publish(private: Path, public: Path, source_sha: str, status: str, *,
            recipient: dict | None = None, context: dict | None = None) -> dict:
    require(type(source_sha) is str and HEX40.fullmatch(source_sha) is not None,
            "invalid_motionwright_sha")
    require(status in {"success", "failure", "cancelled", "skipped"}, "invalid_ci_status")
    require(not public.exists() and not public.is_symlink(), "existing_public_destination")
    private, public = private.resolve(), public.resolve()
    require(private != public and private not in public.parents and public not in private.parents,
            "overlapping_private_public_roots")
    primary, custody_error = None, None
    facts, witnesses = {}, {}
    if status == "success":
        try:
            facts, witnesses = validate_originals(private, source_sha)
        except BaseException as error:
            primary = error
    try:
        public.mkdir(mode=0o700, parents=True, exist_ok=False)
    except BaseException as error:
        # No destination exists for a receipt or capsule. Keep any original
        # validation/interruption failure primary; never make a second attempt.
        if primary is None:
            raise
        experiment.secondary(primary, "secondary_public_destination_failed:" + type(error).__name__)
        raise primary
    custody = {"status": "NOT_SEALED", "selected_originals_complete": False,
               "validated_originals_match_snapshot": False, "upload_confirmed": False,
               "source_binding_verified": False,
               "capsule_file": None, "capsule_bytes": None, "capsule_sha256": None}
    try:
        require(type(recipient) is dict and type(context) is dict, "private_custody_inputs_required")
        import ci_private_evidence as existing_custody
        existing_custody.validate_context(context)
        existing_custody.public_key(recipient)
        require(context["source_sha"] == source_sha, "private_custody_source_context")
        if facts:
            require(context["semwright_sha"] == facts["declared_semwright_sha"],
                    "private_custody_declared_sdk_context")
        source_witnesses = _seal_source_binding(private, source_sha, context)
        raw, complete = existing_custody.gather_snapshot({"fframes": private}, preserve_partial=True)
        source_bound = _snapshot_matches(raw, source_witnesses)
        matches = _snapshot_matches(raw, witnesses)
        encrypted = existing_custody.encrypt_snapshot(raw, recipient, context)
        existing_custody.new_bytes(public / SEAL_FILE, encrypted)
        custody.update({"status": "SEALED" if source_bound else "SEALED_UNVERIFIED_SOURCE",
                        "source_binding_verified": source_bound, "selected_originals_complete": complete,
                        "validated_originals_match_snapshot": matches, "capsule_file": SEAL_FILE,
                        "capsule_bytes": len(encrypted), "capsule_sha256": experiment.sha(encrypted)})
    except BaseException as error:
        custody_error = error
        custody["status"] = "NOT_SEALED"
        if primary is not None:
            experiment.secondary(primary, "secondary_private_capsule_failed")
    verified = (status == "success" and primary is None and custody_error is None
                and custody["source_binding_verified"] is True
                and custody["selected_originals_complete"] is True
                and custody["validated_originals_match_snapshot"] is True)
    if status != "success":
        reason = {"failure": "UPSTREAM_JOB_FAILED", "cancelled": "UPSTREAM_JOB_CANCELLED",
                  "skipped": "UPSTREAM_JOB_SKIPPED"}[status]
    elif primary is not None:
        reason = "ORIGINAL_EVIDENCE_REJECTED"
    elif custody_error is not None:
        reason = "PRIVATE_CUSTODY_NOT_SEALED"
    elif not verified:
        reason = "PRIVATE_SNAPSHOT_INCOMPLETE_OR_CHANGED"
    else:
        reason = "VERIFIED_SELECTED_EXPERIMENT"
    receipt = {
        "schema": SCHEMA, "motionwright_sha": source_sha, "fframes_sha": PIN,
        "declared_semwright_sha": facts.get("declared_semwright_sha") if verified else None,
        "semwright_driver_host_executed": False,
        "ci_evidence": "PASS" if verified else "NOT_VERIFIED", "outcome_category": reason,
        "actual_measured_seconds": facts.get("actual_measured_seconds", {}) if verified else {},
        "source_preview": facts.get("source_preview", {}) if verified else {},
        "repeated_preview": facts.get("repeated_preview", {}) if verified else {},
        "source_one_second_video": facts.get("source_one_second_video", {}) if verified else {},
        "synthetic_audio_observation": facts.get("synthetic_audio_observation", {}) if verified else {},
        "synthetic_audio_build_seconds": facts.get("synthetic_audio_build_seconds") if verified else None,
        "synthetic_audio_render_seconds": facts.get("synthetic_audio_render_seconds") if verified else None,
        "private_original_custody": custody, "plaintext_stdout_stderr_uploaded": False,
        "upstream_binary_uploaded": False, "source_code_admitted": False,
        "renderer_adoption_authorized": False, "same_brief_comparison": "NOT_RUN",
        "audio_fidelity_assessed": "SYNTHETIC_STEREO_MIX_ONLY" if verified else "NOT_VERIFIED",
        "interactive_gpu_preview": "NOT_RUN", "human_creative_acceptance": "NOT_RUN",
        "v05_release_ready": False,
    }
    try:
        experiment.new_bytes(public / "public-evidence.json", experiment.json_bytes(receipt))
    except BaseException as error:
        if primary is not None:
            experiment.secondary(primary, "secondary_public_receipt_failed")
        elif custody_error is not None:
            experiment.secondary(custody_error, "secondary_public_receipt_failed")
        else:
            raise
    if primary is not None:
        raise primary
    if custody_error is not None:
        if status == "success" or not isinstance(custody_error, Exception):
            raise custody_error
    if status == "success" and not verified:
        raise Refused("private_snapshot_incomplete_or_changed")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--job-status", required=True)
    parser.add_argument("--recipient", type=Path)
    parser.add_argument("--context", type=Path)
    args = parser.parse_args()
    try:
        recipient = (strict_object(experiment.read_original(args.recipient.resolve(strict=True), 8192))
                     if args.recipient is not None else None)
        context = (strict_object(experiment.read_original(args.context.resolve(strict=True), 8192))
                   if args.context is not None else None)
        value = publish(args.evidence_root, args.public_root, args.source_sha, args.job_status,
                        recipient=recipient, context=context)
    except BaseException:
        # Private messages, paths, argv and decoder stderr are never public output.
        print("fframes-public=NOT_VERIFIED", file=sys.stderr)
        return 1
    print("fframes-public=" + value["ci_evidence"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
