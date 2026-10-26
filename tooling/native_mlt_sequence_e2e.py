#!/usr/bin/env python3
"""Exact-SHA source-bound two-segment Motion Canvas / MLT FFV1 video E2E.

CI only. Provisions a 33-scene application-owned project and pinned Semwright
Broker/Driver Host, renders 32+1 real Motion Canvas segments, and assembles
FFV1/PCM Matroska lossless intermediary through semantic MLT, not an AAC master.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import struct
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from native_av_failure_guard import run_sanitized_json
from native_mlt_caption_pixels import prove_native_captioned_master
from native_mlt_public_artifact import (
    PublicationError, SOURCE_WAV_NAME, mode_contract, read_synthetic_source_wav,
    verify_source_unchanged, verify_staged_source_receipt,
)

from mlt_failure_diagnostic import safe_observation_diagnostic

ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text(encoding="utf-8"))
PIN = SOURCE_LOCK["dependencies"]["semwright"]["revision"]
SEMWRIGHT = Path(os.environ.get("SEMWRIGHT_CHECKOUT", ROOT / "_semwright")).resolve()
SW_BINS = SEMWRIGHT / "target" / "debug"
MW_BIN = ROOT / "target" / "debug" / "examples" / "native-mlt-sequence-e2e"
RUNTIME = SEMWRIGHT / "integrations" / "motion-canvas" / "runtime"
GITHUB_SHA = os.environ.get("GITHUB_SHA", "unknown")
EVIDENCE = ROOT / "verification" / "native-mlt-sequence-e2e" / GITHUB_SHA
MODE = os.environ.get("MOTIONWRIGHT_MLT_MODE", "pcm")
HYBRID = MODE == "hybrid-av"
if MODE not in {"pcm", "video-only", "final-mp4", "captioned-mp4", "hybrid-av"}:
    raise SystemExit("Only closed native MLT smoke modes are accepted")
CONTRACT = mode_contract(MODE)
VIDEO_ONLY = MODE in {"video-only", "final-mp4", "captioned-mp4"}
FINAL_MASTER = MODE in {"final-mp4", "captioned-mp4"}
CAPTIONED_MASTER = MODE == "captioned-mp4"
PROFILE = "lossless-video-only" if VIDEO_ONLY else "lossless"
EXPECTED_SCOPE = (
    "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    if VIDEO_ONLY else "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
)
VIDEO_FILE = "native-two-segment-video-only.mkv" if VIDEO_ONLY else "native-two-segment-lossless.mkv"


def inspect_decoded_ci_stereo(pcm: bytes, *, mode: str = "final-mp4") -> dict:
    """Independent decoded-content evidence for the actual 33-frame CI
    source. The 48 kHz stereo WAV authored by the Rust E2E contains distinct
    400 Hz left and 600 Hz right sawtooth fundamentals. AAC stream headers,
    MP4 byte hashes or a nonempty decoded WAV alone do NOT prove the actual
    speaker content was retained and not swapped, duplicated or muted.
    """
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo oracle admits only its measured-voice fixture")
    contract = mode_contract(mode)
    if len(pcm) % 4:
        raise AssertionError("Decoded H.264/AAC master has partial stereo PCM sample")
    frames = len(pcm) // 4
    if not contract.pcm_frames <= frames <= contract.pcm_frames + 1024:
        raise AssertionError("Decoded AAC samples exceed one bounded access unit")
    start = 4_800
    end = min(48_000, frames - 2_048)
    if end - start < 24_000:
        raise AssertionError("Decoded AAC lacks a stable measured content window")

    def fundamental(channel: int, frequency: int) -> float:
        cosine = sine = 0.0
        count = 0
        for frame in range(start, end, 8):
            sample = struct.unpack_from("<hh", pcm, frame * 4)[channel] / 32_768.0
            phase = 2.0 * math.pi * frequency * frame / 48_000
            cosine += sample * math.cos(phase)
            sine += sample * math.sin(phase)
            count += 1
        return 2.0 * math.hypot(cosine, sine) / count

    left_400 = fundamental(0, 400)
    left_600 = fundamental(0, 600)
    right_400 = fundamental(1, 400)
    right_600 = fundamental(1, 600)
    if (left_400 < 0.08 or right_600 < 0.06
        or left_400 < 4 * max(left_600, 1e-7)
        or right_600 < 4 * max(right_400, 1e-7)):
        raise AssertionError("Actual decoded AAC lost distinct authored stereo CI signals")
    return {
        "decoded_master_pcm_frames": frames,
        "decoded_stereo_content": "distinct_source_400hz_left_600hz_right",
        "left_400_amplitude": round(left_400, 5),
        "right_600_amplitude": round(right_600, 5),
    }


def inspect_native_master_decoded_content(master: Path, ffmpeg: Path, env: dict,
                                          *, mode: str = "final-mp4", g2=None) -> dict:
    """Decode the real signed-off owner MP4 using fixed CI ffmpeg arguments;
    no application runtime, local backend or user-controlled filters.
    Temp disk output ensures even a hostile decoder cannot grow Python RAM.
    """
    if mode == "hybrid-av":
        return _inspect_hybrid_original_audio(master,ffmpeg,env,mode=mode,g2=g2)
    if mode not in {"final-mp4", "captioned-mp4"}:
        raise AssertionError("Legacy stereo decoder admits only its measured-voice fixture")
    contract = mode_contract(mode)
    with tempfile.TemporaryFile() as output:
        decoded = subprocess.run(
            [str(ffmpeg), "-nostdin", "-v", "error", "-i", str(master),
             "-map", "0:a:0", "-acodec", "pcm_s16le", "-ac", "2",
             "-ar", "48000", "-f", "s16le", "-"],
            env=env, stdout=output, stderr=subprocess.PIPE,
            timeout=45, check=False,
        )
        if decoded.returncode != 0:
            raise AssertionError("Independent bounded CI AAC-to-PCM decoder failed")
        output.seek(0, 2)
        size = output.tell()
        if not (contract.pcm_frames * 4 <= size <= (contract.pcm_frames + 1024) * 4):
            raise AssertionError("Decoded source speaker PCM exceeded the AV clock budget")
        output.seek(0)
        pcm = output.read((contract.pcm_frames + 1024) * 4 + 1)
        if len(pcm) != size:
            raise AssertionError("Decoded AV speaker bytes changed during bounded read")
        if g2 is not None:
            g2.inspect_pcm(pcm)
        return inspect_decoded_ci_stereo(pcm, mode=mode)

def _inspect_hybrid_original_audio(master: Path, ffmpeg: Path, env: dict,
                                          *, mode: str = "hybrid-av", g2=None) -> dict:
    """Decode the retained private master bytes through the existing executor."""
    from manim_native_readback import run_bounded
    from native_mlt_loaded_module import _verified_binary
    from native_mlt_g2_observation import hybrid_master_probe_input, hybrid_master_probe_binding
    if mode!="hybrid-av" or g2 is None or not getattr(g2,"hybrid",False):
        raise AssertionError("Hybrid PCM decode requires its existing original G2 owner")
    contract=mode_contract(mode)
    argv=[str(ffmpeg),"-nostdin","-v","error","-i","pipe:0",
          "-map","0:a:0","-acodec","pcm_s16le","-ac","2","-ar","48000","-f","s16le","-"]
    capture={}; primary=None; original=None; binding=None; binary_sha=None
    try:
        master_bytes,original=hybrid_master_probe_input(g2,master)
        binary_sha=digest(ffmpeg)
        with _verified_binary(ffmpeg,binary_sha,768*1024*1024):
            pcm,_terminal=run_bounded(argv,(contract.pcm_frames+1024)*4,45,
                                      input_bytes=master_bytes,capture=capture)
        binding=hybrid_master_probe_binding(g2,original,capture,pcm)
    except BaseException as error:
        primary=error
        raise
    finally:
        capture["argv"]=argv
        if binary_sha is not None:capture["expected_executable_sha256"]=binary_sha
        if original is not None:capture["original_private_master"]=original
        if binding is not None:capture["master_probe_binding"]=binding
        try:g2.retain_hybrid_capture("hybrid-original-audio-decode",capture)
        except BaseException:
            if primary is None:raise
    if len(pcm)%4 or not contract.pcm_frames*4 <= len(pcm) <= (contract.pcm_frames+1024)*4:
        raise AssertionError("Decoded source speaker PCM exceeded the AV clock budget")
    return g2.inspect_hybrid_pcm(pcm,master_input=binding)



def retain_native_master_packets(master: Path, ffprobe: Path, env: dict, g2) -> None:
    """Keep the one original packet document without granting clock acceptance."""

    if getattr(g2,"hybrid",False):
        from manim_native_readback import run_bounded
        from native_mlt_loaded_module import _verified_binary
        from native_mlt_g2_observation import hybrid_master_probe_input, hybrid_master_probe_binding
        argv=[str(ffprobe),"-v","error","-select_streams","a:0","-show_streams","-show_packets","-of","json","pipe:0"]
        capture={}; primary=None; original=None; binding=None; binary_sha=None
        try:
            master_bytes,original=hybrid_master_probe_input(g2,master)
            binary_sha=digest(ffprobe)
            with _verified_binary(ffprobe,binary_sha,768*1024*1024):
                raw,_terminal=run_bounded(argv,1048576,45,input_bytes=master_bytes,capture=capture)
            binding=hybrid_master_probe_binding(g2,original,capture,raw)
        except BaseException as error:
            primary=error
            raise
        finally:
            capture["argv"]=argv
            if binary_sha is not None:capture["expected_executable_sha256"]=binary_sha
            if original is not None:capture["original_private_master"]=original
            if binding is not None:capture["master_probe_binding"]=binding
            try:g2.retain_hybrid_capture("hybrid-original-packet-probe",capture)
            except BaseException:
                if primary is None:raise
        g2.retain_packet_document(raw)
        g2.packet_observation["decoder_input"]=binding
        return
    try:
        with tempfile.TemporaryFile() as output:
            observed = subprocess.run(
                [str(ffprobe), "-v", "error", "-select_streams", "a:0",
                 "-show_streams", "-show_packets", "-of", "json", str(master)],
                env=env, stdout=output, stderr=subprocess.PIPE, timeout=45, check=False,
            )
            output.seek(0, 2)
            if observed.returncode != 0 or not 0 < output.tell() <= 1_048_576:
                raise AssertionError("Original packet observation was not bounded and successful")
            output.seek(0)
            g2.retain_packet_document(output.read(1_048_577))
    except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired):
        # This is a read-only witness failure after the native terminal; it
        # cannot suppress retention/decoding of the same original master.
        g2.secondary("original_packet_observation")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def retain_native_master_source(output_root: Path, evidence_root: Path,
                                receipt: dict, mode: str) -> dict:
    """Retain the admitted staged bytes; never regenerate or replace a source."""
    try:
        if (not output_root.is_dir() or output_root.is_symlink()
            or not evidence_root.is_dir() or evidence_root.is_symlink()):
            raise PublicationError("native_mlt_source_wav_root_invalid")
        native_root = output_root.resolve()
        retained_root = evidence_root.resolve()
        if (native_root == retained_root
            or native_root in retained_root.parents
            or retained_root in native_root.parents):
            raise PublicationError("native_mlt_source_wav_roots_overlap")
        # This admits the closed CI basename and source identity before any
        # source file is opened. The private native receipt is never copied.
        relative = verify_staged_source_receipt(receipt, mode)
        source = native_root / relative
        data, proof, token = read_synthetic_source_wav(source, mode)
        destination = retained_root / SOURCE_WAV_NAME
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_BINARY", 0))
        with os.fdopen(os.open(destination, flags, 0o600), "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        copied, copied_proof, _ = read_synthetic_source_wav(destination, mode)
        if copied != data or copied_proof != proof:
            raise PublicationError("native_mlt_source_wav_copy_mismatch")
        verify_source_unchanged(source, token)
        return proof
    except (PublicationError, OSError):
        raise AssertionError("native_master_source_retention_refused") from None


def run_json(argv: list[str], *, env: dict[str, str], timeout: int = 30) -> dict:
    return run_sanitized_json(argv, env=env, timeout=timeout)



def run_original_seed(argv, *, env, evidence, current):
    """Capture the existing seed command once before parsing and before assemble."""
    from native_mlt_g2_observation import new_bytes
    import native_mlt_evidence_selection as selected
    current=selected.checked_context(current,"repaired-g2")
    selected.require(type(argv) is list and len(argv)==3 and argv[1]=="seed",
                     "ffv1-seed-command-shape")
    captures=[]
    def retain(record):
        selected.require(not captures,"ffv1-seed-capture-repeated")
        captures.append(record)
        selected.require(type(record) is dict and set(record)=={
            "argv","timeout_seconds","stdout","stderr","exit_code","deadline_exceeded",
            "error_class","error","automatic_retry"},"ffv1-seed-capture-fields")
        selected.require(record["argv"]==argv and record["timeout_seconds"]==30
            and record["automatic_retry"] is False,"ffv1-seed-capture-identity")
        metadata={key:record[key] for key in ("argv","timeout_seconds","exit_code",
            "deadline_exceeded","error_class","error","automatic_retry")}
        metadata.update(schema=selected.FFV1_SEED_CAPTURE_SCHEMA,context=current,
            capture_scope="existing-run-sanitized-json-callback-before-parse",
            before_assemble=True,native_outcome_inferred=False)
        failures=[]
        for stream,maximum,relative in (
                ("stdout",65536,selected.FFV1_SEED_STDOUT_PATH),
                ("stderr",262144,selected.FFV1_SEED_STDERR_PATH)):
            body=record[stream]
            selected.require(type(body) is bytes,"ffv1-seed-stream-type")
            prefix=body[:maximum]
            item=dict(relative_path=relative,sha256=selected.sha(prefix),bytes=len(prefix),
                returned_buffer_sha256=selected.sha(body),returned_buffer_bytes=len(body),
                prefix_truncated=len(body)>maximum,retained=False,stream_eof_attested=False)
            metadata[stream]=item
            try:
                new_bytes(evidence / Path(relative).name,prefix)
                item["retained"]=True
            except BaseException as error:
                failures.append(error)
            if len(body)>maximum:
                failures.append(ValueError("ffv1-seed-stream-budget"))
        try:
            packed=selected.core.canonical(metadata)
            selected.require(len(packed)<=65536,"ffv1-seed-terminal-budget")
            new_bytes(evidence / Path(selected.FFV1_SEED_TERMINAL_PATH).name,packed)
        except BaseException as error:
            failures.append(error)
        if failures:
            raise failures[0]
    # The shared guard preserves its original failure if stream custody fails.
    # A successful seed with failed custody stops here, before any assemble call.
    result=run_sanitized_json(argv,env=env,timeout=30,capture=retain)
    selected.require(len(captures)==1,"ffv1-seed-original-capture-absent")
    original=captures[0]["stdout"]
    selected.require(0<len(original)<=65536,"ffv1-seed-original-stdout-budget")
    admitted=selected.core.project(selected.core.strict(original),selected.FFV1_SEED,exact=True)
    selected.require(admitted==result and admitted["resource"]=="project:"+admitted["project_id"],
                     "ffv1-seed-original-input")
    return result



def exact_inputs() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("native render E2E is GitHub-Actions-only")
    observed = subprocess.check_output(
        ["git", "-C", str(SEMWRIGHT), "rev-parse", "HEAD"], text=True
    ).strip()
    if observed != PIN:
        raise SystemExit(f"Semwright checkout mismatch: expected {PIN}, got {observed}")
    for path in [
        SW_BINS / "semwright",
        SW_BINS / "semwrightd",
        SW_BINS / "semwright-sandbox",
        SW_BINS / "semwright-motion-canvas-driver",
        SW_BINS / "semwright-mlt-video-driver",
        SW_BINS / "semwright-mlt-runtime-runner",
        MW_BIN,
        RUNTIME / "package-lock.json",
    ]:
        if not path.exists():
            raise SystemExit(f"missing exact acceptance input: {path}")
    if not shutil.which("bwrap"):
        raise SystemExit("bubblewrap is required; no unsandboxed fallback is accepted")


def semwright_version() -> str:
    with (SEMWRIGHT / "Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["workspace"]["package"]["version"]


def wait_for_socket(process: subprocess.Popen[bytes], socket: Path, log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(log.read_text(encoding="utf-8", errors="replace"))
        if socket.is_socket():
            return
        time.sleep(0.05)
    raise AssertionError("semwrightd did not expose the owner socket")


@contextmanager
def native_owner_workspace(g2=None):
    """Keep G2 owner originals in place for later sealed custody.

    Ordinary runs retain their existing temporary-directory cleanup. A selected
    G2 run records its workspace before any seed/native effect and never deletes
    that workspace, including when the canonical command has an unknown result.
    """
    if g2 is None:
        with tempfile.TemporaryDirectory(prefix="motionwright-native-av-") as raw:
            yield raw
        return
    from native_mlt_g2_observation import new_json, admission
    runner = admission.disposable_linux_runner()
    root = Path(tempfile.mkdtemp(prefix="motionwright-native-av-g2-", dir=runner))
    info = root.lstat()
    if (root.parent != runner or root.resolve(strict=True) != root
        or not root.is_dir() or root.is_symlink()):
        raise AssertionError("G2 owner workspace is not a fresh canonical runner child")
    identity = {
        "schema": "motionwright.native.mlt-g2-owner-workspace/1",
        "motionwright_sha": GITHUB_SHA, "semwright_sha": PIN,
        "run_id": g2.inputs["run_id"], "run_attempt": g2.inputs["run_attempt"],
        "path": str(root), "device": info.st_dev, "inode": info.st_ino,
        "uid": info.st_uid, "gid": info.st_gid,
        "custody_policy": "retain_originals_in_place_for_sealing",
        "native_effect_started": False,
    }
    new_json(g2.evidence_dir / "owner-workspace.json", identity)
    primary = None
    try:
        yield str(root)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            retained = root.lstat()
            if ((retained.st_dev, retained.st_ino, retained.st_uid, retained.st_gid)
                != (info.st_dev, info.st_ino, info.st_uid, info.st_gid) or not root.is_dir()
                or root.is_symlink()):
                raise AssertionError("G2 owner workspace identity changed before custody")
            new_json(g2.evidence_dir / "owner-workspace-terminal.json", {
                "schema": "motionwright.native.mlt-g2-owner-workspace-terminal/1",
                "workspace": identity, "originals_retained": root.is_dir(),
                "workspace_deleted": False,
                "body_exception_type": None if primary is None else type(primary).__name__,
                "native_success_inferred": False,
            })
        except Exception:
            g2.secondary("owner_workspace_terminal_custody")
            if primary is None:
                raise





def run_hybrid_original_json(argv, *, env, evidence, label, timeout):
    """Capture via the existing first-party supervisor; never launch a second call."""
    from native_mlt_g2_observation import new_bytes,new_json,bounded_error
    if (type(argv) is not list or type(label) is not str or label not in ("hybrid-seed","hybrid-assemble")
            or not argv or argv[0] != str(MW_BIN)
            or (label=="hybrid-seed" and (len(argv)!=7 or argv[1]!="seed-hybrid" or timeout!=60))
            or (label=="hybrid-assemble" and (len(argv)!=6 or argv[1]!="assemble-hybrid" or timeout!=2100))):
        raise AssertionError("hybrid_original_command_identity")
    new_json(evidence/(label+".intent.json"),{
        "argv":argv,"timeout_seconds":timeout,"automatic_retry":False,
        "source_only_Service_command":label=="hybrid-seed",
        "native_owner_command":label=="hybrid-assemble",
    })
    captures=[]
    def retain(record):
        if captures or type(record) is not dict or record.get("argv") != argv or record.get("timeout_seconds") != timeout:
            raise AssertionError("hybrid_original_command_capture_identity")
        captures.append(record)
        terminal={k:v for k,v in record.items() if k not in ("stdout","stderr")}
        terminal.update(schema="motionwright.native.hybrid-original-command/1",label=label,
                        raw_error_is_private=True,native_terminal_inferred_from_exit=False)
        errors=[]
        for stream in ("stdout","stderr"):
            body=record.get(stream)
            if type(body) is not bytes or len(body)>1024*1024:
                errors.append(ValueError("hybrid_original_command_stream_budget"))
                continue
            terminal[stream]={"sha256":hashlib.sha256(body).hexdigest(),"bytes":len(body),"retained":False}
            try:
                new_bytes(evidence/(label+"."+stream+".bin"),body)
                terminal[stream]["retained"]=True
            except BaseException as error:errors.append(error)
        terminal["custody_errors"]=[bounded_error(e) for e in errors]
        try:new_json(evidence/(label+".terminal.json"),terminal)
        except BaseException as error:errors.append(error)
        if errors:raise errors[0]
    result=run_sanitized_json(argv,env=env,timeout=timeout,capture=retain)
    if len(captures)!=1:
        raise AssertionError("hybrid_original_command_capture_absent")
    return result


def hybrid_manim_manifest(paths,ffmpeg,ffprobe):
    """Same admitted Manim driver/runner contract, on the existing owner session."""
    import native_manim_e2e as manim
    from native_mlt_g2_observation import new_json
    if (manim.PIN != PIN or manim.FFMPEG != ffmpeg or manim.FFPROBE != ffprobe
            or any(not path.exists() for path in (manim.MW_DRIVER,manim.MW_RUNNER,manim.PYTHON,
                manim.MANIM_RUNTIME/"runtime.json",manim.MANIM_RUNTIME/"site-packages"))):
        raise AssertionError("hybrid_MCE_MLT_Manim_runtime_inputs_are_not_the_same_pinned_tools")
    runtime_evidence=EVIDENCE/"manim-runtime-originals"
    runtime_evidence.mkdir(mode=0o700)
    inventory=manim.retain_runtime_inventory(runtime_evidence)
    driver=paths["bin"]/"motionwright-manim-community-driver"
    runner=paths["bin"]/"motionwright-manim-runner"
    shutil.copyfile(manim.MW_DRIVER,driver); driver.chmod(0o700)
    shutil.copyfile(manim.MW_RUNNER,runner); runner.chmod(0o700)
    manifest={
        "manifest_version":1,"protocol":8,"id":"manim-community","version":"0.1.0",
        "publisher":"motionwright","executable":str(driver),"sha256":digest(driver),
        "application":{"desktop_id":None,"process_names":["motionwright-manim-runner","python3","ffmpeg"],
                       "supported_versions":[f"Manim Community {manim.MANIM_VERSION}"]},
        "transport":"stdio_v1",
        "mounts":[{"root":"manim-runtime","read_only":True},{"root":"manim-work","read_only":False},
                  {"root":"manim-output","read_only":False},{"root":"fontconfig","read_only":True}],
        "system_config":[],"secrets":[],
        "tools":[{"root":"manim-runner-root","name":"manim-runner","sha256":digest(runner),
                  "mounts":["manim-runtime","manim-work","manim-output","fontconfig"],"dependencies":["python3","ffmpeg"]},
                 {"root":"python-root","name":"python3","sha256":digest(manim.PYTHON),"mounts":[],"dependencies":[]},
                 {"root":"ffmpeg-root","name":"ffmpeg","sha256":digest(ffmpeg),"mounts":[],"dependencies":[]}],
        "network":False,"loopback_port":None,
        "resources":{"open_files":512,"processes":256,"cpu_seconds":300,"operation_cpu_seconds":0,
                     "address_space_bytes":4294967296,"file_size_bytes":1073741824},
        "request_timeout_ms":300000,
        "interfaces":{"dynamic_capabilities":False,"cooperative_cancellation":True,"events":False,
                      "health":True,"progress":False,"artifacts":False,"native_refs":False,"host_tools":True},
    }
    target=paths["config"]/"manim-driver.json"
    write_private_json(target,manifest)
    new_json(EVIDENCE/"hybrid-manim-provider-binding.json",{
        "driver_sha256":digest(driver),"runner_sha256":digest(runner),"python_sha256":digest(manim.PYTHON),
        "runtime_inventory_sha256":inventory,"native_manim_source_profile":"static-v2",
        "same_owner_Broker_session":True,"physical_provider_probe_still_required":True,
    })
    return target,[("manim-runtime",manim.MANIM_RUNTIME,False),("manim-work",paths["manim-work"],True),
                   ("manim-output",paths["output"],True),("manim-runner-root",runner,False),
                   ("python-root",manim.PYTHON,False)]


def finish_hybrid_original(paths,result,ffmpeg,ffprobe,env,g2):
    from native_mlt_g2_observation import new_json
    from native_mlt_caption_pixels import inspect_original_hybrid_pixels
    if (g2 is None or not getattr(g2,"hybrid",False) or type(result) is not dict
            or result.get("schema")!="motionwright.native.hybrid-av-summary/1"):
        raise AssertionError("hybrid_original_result_scope")
    original=paths["output"]/result["master_artifact_path"]
    g2.retain_master(original,result["master_artifact_sha256"],result["master_artifact_bytes"])
    # All originals exist before this independent reader starts. It cannot
    # redispatch a renderer, native import or mux, including after readback fails.
    pixels=inspect_original_hybrid_pixels(paths["output"],EVIDENCE/"native-hybrid",result,
        ffmpeg=ffmpeg,executable_sha256=digest(ffmpeg),retain_decode=g2.retain_hybrid_capture)
    new_json(EVIDENCE/"hybrid-pixel-observation.json",pixels)
    private_master=g2.evidence_dir/"original-native-master.mp4"
    retain_native_master_packets(private_master,ffprobe,env,g2)
    audio=inspect_native_master_decoded_content(private_master,ffmpeg,env,mode="hybrid-av",g2=g2)
    new_json(EVIDENCE/"hybrid-audio-content.json",audio)
    completed=g2.complete_hybrid(pixels)
    receipt={
        "schema":"motionwright.native.hybrid-public-source/1","motionwright_sha":GITHUB_SHA,"semwright_sha":PIN,
        "project_sha256":result["project_sha256"],"master_sha256":result["master_artifact_sha256"],
        "visual_sha256":result["visual_artifact_sha256"],"source_wave_sha256":g2.provenance["sha256"],
        "source_audio_kind":"first_party_synthetic_Film180_source",
        "width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":180,"source_segment_frames":[60,60,60],
        "source_renderers":["motion_canvas","manim_static","motion_canvas"],
        "original_PNG_frames_bound":pixels["original_PNG_frames_bound"],
        "original_PNG_decoder_invocations":pixels["original_PNG_decoder_invocations"],
        "exact_original_PNG_reuses":pixels["equal_original_PNG_frames_reused"],
        "native_cut_and_content_screen":pixels["fixed_cut_and_content_screen"],"source_audio_content_screen":"PASS",
        "source_vs_output_differences_retained":True,
        "visual_exact_clock_observed":pixels["visual_exact_clock_observed"],
        "master_exact_clock_observed":pixels["master_exact_clock_observed"],
        "pixel_observation_sha256":digest(EVIDENCE/"hybrid-pixel-observation.json"),
        "audio_observation_sha256":digest(EVIDENCE/"hybrid-audio-content.json"),
        "original_source_reopened_unchanged":True,"live_MLT_module_observed":True,
        "native_effect_retried":False,"packet_PTS_and_AAC_priming_verified":False,
        "Host_receiver_closure_proved":False,"physical_acceptance":False,"human_approval":False,
        "media_publication_permission_inferred":False,
    }
    new_json(EVIDENCE/"hybrid-public-source.json",receipt)
    new_json(EVIDENCE/"hybrid-g2-observation.json",completed)
    return {"native_hybrid_original_observation":"RETAINED","motionwright_sha":GITHUB_SHA,"semwright_sha":PIN,
            "frame_count":180,"evidence":"private-g2-producer","physical_acceptance":False,"human_approval":False}


def close_native_owner(daemon,log_stream):
    """Same existing termination/waits; closing a process proves no Host receipt."""
    primary=None
    try:
        if daemon.poll() is None:
            pid=daemon.pid
            daemon.terminate()
            try:daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pid,signal.SIGKILL)
                daemon.wait(timeout=5)
    except BaseException as error:primary=error
    try:log_stream.close()
    except BaseException as error:
        if primary is None:primary=error
    if primary is not None:raise primary


def main(g2=None):
    global EVIDENCE
    exact_inputs()
    if HYBRID and (g2 is None or not getattr(g2,"hybrid",False)):
        raise AssertionError("hybrid_requires_its_fresh_original_G2_contract")
    if g2 is not None:
        observed = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        if observed != GITHUB_SHA:
            raise AssertionError("G2 checkout must equal the exact executing source")
    if g2 is not None:
        # Private /2 roots must not overlap the executing source checkout.
        # The existing producer writes its originals once in the retained G2
        # session; ordinary workflow output keeps its established location.
        EVIDENCE = g2.evidence_dir / "producer"
    EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=False)

    with native_owner_workspace(g2) as raw:
        root = Path(raw).resolve()
        root.chmod(0o700)
        paths: dict[str, Path] = {}
        for name in [
            "runtime",
            "state",
            "home",
            "config",
            "bin",
            "project",
            "media",
            "output",
            "scratch",
            "motionwright-data",
        ]:
            path = root / name
            path.mkdir(mode=0o700)
            paths[name] = path
        if HYBRID:
            paths["manim-work"] = root / "manim-work"
            paths["manim-work"].mkdir(mode=0o700)

        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}
        }
        env.update(
            HOME=str(paths["home"]),
            XDG_RUNTIME_DIR=str(paths["runtime"]),
            XDG_STATE_HOME=str(paths["state"]),
            RUST_BACKTRACE="1",
            SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
            SEMWRIGHT_TEST_SANDBOX_HELPER=str(SW_BINS / "semwright-sandbox"),
        )

        if g2 is not None:
            env = g2.seed_environment(env)
        elif any(key in env for key in ("MOTIONWRIGHT_MLT_ORIGINAL_WAV", "MOTIONWRIGHT_MLT_ORIGINAL_WAV_SHA256")):
            raise AssertionError("An original WAV requires the explicit G2 input envelope")
        database = paths["motionwright-data"] / "motionwright.sqlite3"
        if HYBRID:
            seed_argv=[str(MW_BIN),"seed-hybrid",str(database),str(g2.evidence_dir/"original-source.wav"),
                       g2.provenance["sha256"],str(g2.provenance["bytes"]),str(EVIDENCE/"hybrid-source.original.json")]
            seeded=run_hybrid_original_json(seed_argv,env=env,evidence=EVIDENCE,label="hybrid-seed",timeout=60)
        else:
            seed_argv=[str(MW_BIN),"seed",str(database)]
            seeded=(run_original_seed(seed_argv,env=env,evidence=EVIDENCE,
                        current={"source_sha":GITHUB_SHA,"semwright_sha":PIN,
                            "run_id":g2.inputs["run_id"],"run_attempt":g2.inputs["run_attempt"]})
                    if g2 is not None else run_json(seed_argv,env=env))
        if ((seeded["width"], seeded["height"]) != (CONTRACT.width, CONTRACT.height)
            or (seeded["fps_num"], seeded["fps_den"]) != (CONTRACT.fps_num, CONTRACT.fps_den)
            or seeded["scene_count"] != CONTRACT.scene_count
            or seeded["deliverable_count"] != CONTRACT.deliverable_count):
            raise AssertionError(f"unexpected real default profiles or source scenes: {seeded}")

        driver = paths["bin"] / "semwright-motion-canvas-driver"
        node_tool = paths["bin"] / "semwright-motion-node"
        mlt_driver = paths["bin"] / "semwright-mlt-video-driver"
        mlt_runner = paths["bin"] / "semwright-mlt-runtime-runner"
        shutil.copyfile(SW_BINS / "semwright-motion-canvas-driver", driver)
        shutil.copyfile(Path(shutil.which("node") or ""), node_tool)
        shutil.copyfile(SW_BINS / "semwright-mlt-video-driver", mlt_driver)
        shutil.copyfile(SW_BINS / "semwright-mlt-runtime-runner", mlt_runner)
        for executable in [driver, node_tool, mlt_driver, mlt_runner]:
            executable.chmod(0o700)

        melt = Path(shutil.which("melt") or "").resolve()
        ffprobe = Path(shutil.which("ffprobe") or "").resolve()
        ffmpeg = Path(shutil.which("ffmpeg") or "").resolve()
        for executable in [melt, ffprobe, ffmpeg]:
            if not executable.is_file():
                raise SystemExit(f"missing pinned MLT runtime tool: {executable}")
        mlt_runtime = melt.parent.parent.resolve()

        manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "motion-canvas",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(driver),
            "sha256": digest(driver),
            "application": {
                "desktop_id": None,
                "process_names": ["node"],
                "supported_versions": ["Motion Canvas 3.17.2", "Node 22.22.0"],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": False},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "runtime", "read_only": True, "execute": True},
                {"root": "fontconfig", "read_only": True},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "motion-node-tool",
                    "name": "motion-node",
                    "sha256": digest(node_tool),
                    "mounts": ["project", "output", "runtime", "fontconfig"],
                }
            ],
            "network": False,
            "loopback_port": None,
            "resources": {
                "open_files": 512,
                "processes": 256,
                "cpu_seconds": 300,
                "operation_cpu_seconds": 0,
                "address_space_bytes": 4_294_967_296,
                "file_size_bytes": 1_073_741_824,
            },
            "request_timeout_ms": 300_000,
            "interfaces": {
                "dynamic_capabilities": False,
                "cooperative_cancellation": True,
                "events": False,
                "health": True,
                "progress": True,
                "artifacts": True,
                "native_refs": False,
                "host_tools": True,
            },
        }
        manifest_path = paths["config"] / "motion-canvas-driver.json"
        write_private_json(manifest_path, manifest)

        mlt_manifest = {
            "manifest_version": 1,
            "protocol": 7,
            "id": "mlt-video",
            "version": semwright_version(),
            "publisher": "semwright",
            "executable": str(mlt_driver),
            "sha256": digest(mlt_driver),
            "application": {
                "desktop_id": None,
                "process_names": ["melt"],
                "supported_versions": [],
            },
            "transport": "stdio_v1",
            "mounts": [
                {"root": "project", "read_only": True},
                {"root": "media", "read_only": True},
                {"root": "output", "read_only": False},
                {"root": "mlt-runtime", "read_only": True, "execute": True},
                {"root": "scratch", "read_only": False},
            ],
            "system_config": [],
            "secrets": [],
            "tools": [
                {
                    "root": "mlt-runner-root",
                    "name": "mlt-runner",
                    "sha256": digest(mlt_runner),
                    "mounts": ["mlt-runtime", "scratch", "project", "media", "output"],
                    "dependencies": ["melt", "ffprobe", "ffmpeg"],
                },
                {
                    "root": "melt-root",
                    "name": "melt",
                    "sha256": digest(melt),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffprobe-root",
                    "name": "ffprobe",
                    "sha256": digest(ffprobe),
                    "mounts": [],
                    "dependencies": [],
                },
                {
                    "root": "ffmpeg-root",
                    "name": "ffmpeg",
                    "sha256": digest(ffmpeg),
                    "mounts": [],
                    "dependencies": [],
                },
            ],
            "network": False,
            "loopback_port": None,
            "resources": {
                "open_files": 512,
                "processes": 256,
                "cpu_seconds": 300,
                "operation_cpu_seconds": 0,
                "address_space_bytes": 4_294_967_296,
                "file_size_bytes": 1_073_741_824,
            },
            "request_timeout_ms": 300_000,
            "interfaces": {
                "dynamic_capabilities": False,
                "cooperative_cancellation": False,
                "events": False,
                "health": True,
                "progress": False,
                "artifacts": False,
                "native_refs": False,
                "host_tools": True,
            },
        }
        mlt_manifest_path = paths["config"] / "mlt-video-driver.json"
        write_private_json(mlt_manifest_path, mlt_manifest)

        grants = [
            ("project", paths["project"], True),
            ("media", paths["media"], False),
            ("output", paths["output"], True),
            ("runtime", RUNTIME, False),
            ("fontconfig", Path("/etc/fonts"), False),
            ("motion-node-tool", node_tool, False),
            ("mlt-runtime", mlt_runtime, False),
            ("scratch", paths["scratch"], True),
            ("mlt-runner-root", mlt_runner, False),
            ("melt-root", melt, False),
            ("ffprobe-root", ffprobe, False),
            ("ffmpeg-root", ffmpeg, False),
        ]
        driver_manifests=[manifest_path,mlt_manifest_path]
        if HYBRID:
            manim_manifest,manim_grants=hybrid_manim_manifest(paths,ffmpeg,ffprobe)
            driver_manifests.append(manim_manifest)
            grants.extend(manim_grants)
        config_lines = [
            "drivers = [" + ", ".join(json.dumps(str(path)) for path in driver_manifests) + "]",
            "driver_network = false",
            "",
            "[policy]",
            'profile = "workspace"' if HYBRID else 'profile = "observe"',
            ('allow = ["driver:motion-canvas", "driver:mlt-video", "driver:manim-community"]'
             if HYBRID else 'allow = ["driver:motion-canvas", "driver:mlt-video"]'),
        ]
        for name, path, write in grants:
            config_lines.extend(
                [
                    "",
                    "[[policy.filesystem]]",
                    f"name = {json.dumps(name)}",
                    f"path = {json.dumps(str(path.resolve()))}",
                    "read = true",
                    f"write = {'true' if write else 'false'}",
                ]
            )
        config_path = paths["config"] / "owner.toml"
        config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
        config_path.chmod(0o600)

        socket = paths["runtime"] / "broker.sock"
        session = paths["runtime"] / "motionwright.session"
        daemon_log = EVIDENCE / "semwrightd.log"
        log_stream = daemon_log.open("wb")
        daemon = subprocess.Popen(
            [
                str(SW_BINS / "semwrightd"),
                "--config",
                str(config_path),
                "--socket",
                str(socket),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=log_stream,
            start_new_session=True,
        )

        primary_owner_error = None
        try:
            wait_for_socket(daemon, socket, daemon_log)
            discovered = run_json(
                [
                    str(SW_BINS / "semwright"),
                    "--socket",
                    str(socket),
                    "--session-file",
                    str(session),
                    "--json",
                    "capabilities",
                    "search",
                    "motion-canvas",
                    "--provider",
                    "driver:motion-canvas",
                ],
                env=env,
            )
            capabilities = discovered.get("data", {}).get("capabilities", [])
            if not capabilities:
                raise AssertionError("pinned Motion Canvas provider exposed no capabilities")
            required_mlt = (
                "driver.mlt-video.frames.encode",
                "driver.mlt-video.project.create",
                "driver.mlt-video.sequence.create",
                "driver.mlt-video.sequence.list",
                "driver.mlt-video.track.create",
                "driver.mlt-video.track.list",
                "driver.mlt-video.asset.import",
                "driver.mlt-video.clip.insert",
                "driver.mlt-video.sequence.duration",
                "driver.mlt-video.render.plan",
                "driver.mlt-video.render.start",
                "driver.mlt-video.render.status",
                "driver.mlt-video.render.result",
                "driver.mlt-video.project.close",
            )
            for capability_name in required_mlt:
                described = run_json(
                    [
                        str(SW_BINS / "semwright"),
                        "--socket",
                        str(socket),
                        "--session-file",
                        str(session),
                        "--json",
                        "capabilities",
                        "describe",
                        capability_name,
                    ],
                    env=env,
                )
                payload = described.get("data", {})
                capability = payload.get("capability", {})
                provenance = payload.get("provenance", {})
                if capability.get("name") != capability_name:
                    raise AssertionError(
                        f"pinned MLT capability describe mismatch: {capability_name}: {capability}"
                    )
                if provenance.get("provider") != "driver:mlt-video":
                    raise AssertionError(
                        f"pinned MLT capability has unexpected provider: "
                        f"{capability_name}: {provenance}"
                    )
            if HYBRID:
                for command,provider in [
                    ("driver.manim-community.doctor","driver:manim-community"),
                    ("driver.manim-community.static.render.start","driver:manim-community"),
                    ("driver.manim-community.render.status","driver:manim-community"),
                    ("driver.manim-community.render.result","driver:manim-community"),
                    ("driver.mlt-video.av.mux","driver:mlt-video"),
                ]:
                    described=run_json([str(SW_BINS/"semwright"),"--socket",str(socket),"--session-file",str(session),
                                        "--json","capabilities","describe",command],env=env)
                    payload=described.get("data",{})
                    if (payload.get("capability",{}).get("name")!=command
                            or payload.get("provenance",{}).get("provider")!=provider):
                        raise AssertionError("hybrid_existing_provider_not_provisioned")
            if not session.is_file() or (session.stat().st_mode & 0o777) != 0o600:
                raise AssertionError("Semwright did not create a private persistent session ticket")

            connection_path = paths["config"] / "motionwright-connection.json"
            write_private_json(
                connection_path,
                {
                    "schema": "motionwright-semwright-connection/1",
                    "executable": str((SW_BINS / "semwright").resolve()),
                    "executable_sha256": digest(SW_BINS / "semwright"),
                    "socket": str(socket),
                    "session_file": str(session),
                    "output_root": str(paths["output"]),
                    "resource": seeded["resource"],
                },
            )

            try:
                def assemble_original():
                    if HYBRID:
                        return run_hybrid_original_json(
                            [str(MW_BIN),"assemble-hybrid",str(database),str(connection_path),seeded["project_sha256"],
                             str(EVIDENCE/"native-hybrid")],env=env,evidence=EVIDENCE,label="hybrid-assemble",timeout=2100)
                    return run_json(
                        [
                            str(MW_BIN), "assemble", str(database), str(connection_path),
                            str(EVIDENCE / "native-multisegment-timeline-evidence.json"),
                        ],
                        env=env, timeout=1050,
                    )
                result = (
                    g2.run_original(assemble_original, daemon.pid, paths["output"])
                    if g2 is not None else assemble_original()
                )
            except (AssertionError, subprocess.TimeoutExpired):
                if HYBRID:
                    raise  # The existing G2 session retained this original; do not replace it with a diagnostic.
                # The pinned renderer's raw failure receipt contains a local
                # stack and owner paths: NEVER print or upload that receipt.
                # Persist only a fixed vocabulary derived before the owner's
                # temporary, private output root is destroyed.
                safe = safe_observation_diagnostic(paths["output"])
                try:
                    write_private_json(EVIDENCE / "safe-observation-failure.json", safe)
                except OSError:
                    pass  # Never replace the original native failure.
                print(json.dumps(safe, sort_keys=True), file=sys.stderr)
                raise
            if HYBRID:
                return finish_hybrid_original(paths,result,ffmpeg,ffprobe,env,g2)
            if result.get("native_mlt_multisegment_e2e") != "PASS":
                raise AssertionError("Real source-bound MLT timeline was not successful: " + repr(result))

            # Inspect the actual source journal produced by Semwright-backed
            # native MLT project.create. This is not a mock allocation count
            # and never claims the provider project was actually closed.
            inventory_path = EVIDENCE / "native-mlt-lifetime-inventory.json"
            if (not inventory_path.is_file() or inventory_path.is_symlink()
                or not 1 <= inventory_path.stat().st_size <= 16_384):
                raise AssertionError("Native MLT lifetime source evidence was missing or oversized")
            inventory_bytes = inventory_path.read_bytes()
            if (result.get("native_mlt_lifetime_inventory_sha256")
                != hashlib.sha256(inventory_bytes).hexdigest()):
                raise AssertionError("Native MLT lifetime receipt bytes differ from Rust source")
            inventory = json.loads(inventory_bytes)
            expected_keys = {
                "schema", "project_id", "generation", "revision",
                "source_receipts_scanned", "confirmed_native_creations_current_revision",
                "confirmed_native_creations_historical",
                "unconfirmed_or_invalid_creation_requests",
                "observed_close_request_identities", "disposition",
                "complete_source_receipt_snapshot",
                "native_provider_liveness_verified",
                "foreground_cleanup_consent_obtained",
                "verified_native_project_cleanup",
                "automatic_destructive_cleanup_allowed",
            }
            if not isinstance(inventory, dict) or set(inventory) != expected_keys:
                raise AssertionError("Native MLT lifetime receipt schema exposes untrusted fields")
            current = inventory.get("confirmed_native_creations_current_revision")
            receipt_total = inventory.get("source_receipts_scanned")
            if (
                inventory["schema"] != "motionwright.v01-mlt-cleanup-inventory/1"
                or inventory["project_id"] != seeded["project_id"]
                or inventory["generation"] != seeded["generation"]
                or inventory["revision"] != seeded["revision"]
                or type(current) is not int or not 1 <= current <= 4
                or type(receipt_total) is not int or receipt_total < current
                or result.get("native_mlt_lifetime_confirmed_current_creations") != current
                or result.get("native_mlt_lifetime_receipts_scanned") != receipt_total
                or result.get("native_mlt_lifetime_close_requests_observed") != 0
                or result.get("native_mlt_cleanup_verified") is not False
                or inventory["unconfirmed_or_invalid_creation_requests"] != 0
                or inventory["observed_close_request_identities"] != 0
                or inventory["disposition"] != "project_lifetime_unverified_foreground_consent_required"
                or inventory["complete_source_receipt_snapshot"] is not True
                or any(inventory[key] is not False for key in (
                    "native_provider_liveness_verified",
                    "foreground_cleanup_consent_obtained",
                    "verified_native_project_cleanup",
                    "automatic_destructive_cleanup_allowed",
                ))
            ):
                raise AssertionError("Real MLT project creation provenance/lifetime is unconfirmed")
            if (result.get("frame_count") != CONTRACT.video_frames
                or result.get("segment_count") != len(CONTRACT.source_segment_frames)):
                raise AssertionError("Native timeline lost the 32+1 source partition")
            if result.get("evidence_scope") != EXPECTED_SCOPE or result.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT result falsely claimed final H.264/AAC master")

            evidence_path = EVIDENCE / "native-multisegment-timeline-evidence.json"
            evidence = json.loads(evidence_path.read_text())
            if (evidence.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"
                or result.get("provider_project_cleanup") != "not_requested_requires_foreground_broker_consent"):
                raise AssertionError("Native MLT receipt did not disclose pending foreground-only cleanup")
            if evidence.get("evidence_scope") != EXPECTED_SCOPE or evidence.get("native_render_profile") != PROFILE:
                raise AssertionError("MLT output identity and exact closed profile diverged")
            if evidence.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("Native MLT claimed wrong audio presence")
            if VIDEO_ONLY and evidence.get("native_frame_count_observed") != CONTRACT.video_frames:
                raise AssertionError("New native video-only profile lacks actual decoded frame count")
            if evidence.get("source", {}).get("total_frames") != CONTRACT.video_frames:
                raise AssertionError("Real FFV1 intermediate frame total was altered")
            records = evidence.get("source", {}).get("verified_video_segments", [])
            if (len(records) != len(CONTRACT.source_segment_frames)
                or [item["frame_count"] for item in records] != list(CONTRACT.source_segment_frames)):
                raise AssertionError("Real native source FFV1s are not the exact two source segments")
            if [item["output_start_frame"] for item in records] != [
                sum(CONTRACT.source_segment_frames[:index])
                for index in range(len(CONTRACT.source_segment_frames))
            ]:
                raise AssertionError("Native output clip start frames were not preserved")
            for record in records:
                artifact = paths["output"] / record["artifact_path"]
                if (not artifact.is_file() or artifact.stat().st_size != record["artifact_bytes"]
                    or digest(artifact) != record["artifact_sha256"]):
                    raise AssertionError("Native source FFV1 bytes changed since Semwright verification")

            video_path = paths["output"] / result["artifact_path"]
            if not video_path.is_file() or video_path.stat().st_size != result["artifact_bytes"]:
                raise AssertionError("Native MLT timeline artifact does not exist with measured size")
            if digest(video_path) != result["artifact_sha256"]:
                raise AssertionError("Native MLT timeline SHA-256 mismatch")
            meta_result = subprocess.run(
                [
                    str(ffprobe), "-v", "error", "-count_frames",
                    "-show_entries", "stream=codec_name,codec_type,width,height,nb_read_frames,r_frame_rate",
                    "-of", "json", str(video_path),
                ],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=True,
            )
            streams = json.loads(meta_result.stdout)["streams"]
            videos = [item for item in streams if item.get("codec_type") == "video"]
            audios = [item for item in streams if item.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != (0 if VIDEO_ONLY else 1):
                raise AssertionError("MLT native render audio presence differs from exact closed profile")
            if not VIDEO_ONLY and audios[0].get("codec_name") != "pcm_s16le":
                raise AssertionError("Native MLT PCM transport audio differs")
            if result.get("transport_pcm_audio") is VIDEO_ONLY:
                raise AssertionError("MLT result contradicted source-bound profile")
            if evidence.get("native_frame_count_observed") not in (None, CONTRACT.video_frames):
                raise AssertionError("MLT native frame count observation contradicted source")
            video = videos[0]
            if (video.get("codec_name") != "ffv1"
                or (video.get("width"), video.get("height")) != (CONTRACT.width, CONTRACT.height)
                or video.get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                or video.get("nb_read_frames") != str(CONTRACT.video_frames)):
                raise AssertionError("Native actual MLT video codec/frame provenance failed: " + repr(video))

            shutil.copyfile(video_path, EVIDENCE / VIDEO_FILE)
            master_check = {}
            if FINAL_MASTER:
                av_receipt = json.loads(
                    (EVIDENCE / "native-multisegment-av-master-evidence.json").read_text()
                )
                master_scope = (
                    "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
                    if CAPTIONED_MASTER else
                    "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
                )
                if (av_receipt["frame_count"] != CONTRACT.video_frames
                    or av_receipt["measured_voice"]["audio_pcm_frames"] != CONTRACT.pcm_frames
                    or av_receipt["measured_voice"].get("caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or result.get("native_captioned_master_requested") is not CAPTIONED_MASTER
                    or result.get("source_bound_caption_cue_count")
                       != (2 if CAPTIONED_MASTER else None)
                    or av_receipt["evidence_scope"] != master_scope):
                    raise AssertionError("Native final AV master source/voice provenance differs")
                relative = result.get("master_artifact_path")
                if not isinstance(relative, str) or not relative.endswith(".mp4"):
                    raise AssertionError("Native master has no bounded MP4 output")
                master = paths["output"] / relative
                if not master.is_file():
                    raise AssertionError("Native final AV mux did not publish a real MP4")
                if (master.stat().st_size != result.get("master_artifact_bytes")
                    or digest(master) != result.get("master_artifact_sha256")
                    or digest(master) != av_receipt["master"]["artifact"]["sha256"]):
                    raise AssertionError("Actual MP4 bytes do not match both native receipts")
                if g2 is not None:
                    g2.retain_master(master, result["master_artifact_sha256"], result["master_artifact_bytes"])
                    retain_native_master_packets(master, ffprobe, env, g2)
                mux_probe = subprocess.run(
                    [
                        str(ffprobe), "-v", "error", "-count_frames",
                        "-show_entries",
                        "stream=codec_type,codec_name,nb_read_frames,width,height,r_frame_rate,sample_rate,channels",
                        "-of", "json", str(master),
                    ],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=45, check=True,
                )
                av_streams = json.loads(mux_probe.stdout)["streams"]
                if len(av_streams) != 2:
                    raise AssertionError("Final MP4 must have exactly H264 video and AAC stereo audio")
                v = [stream for stream in av_streams if stream.get("codec_type") == "video"]
                a = [stream for stream in av_streams if stream.get("codec_type") == "audio"]
                if (len(v) != 1 or len(a) != 1 or v[0].get("codec_name") != "h264"
                    or v[0].get("nb_read_frames") != str(CONTRACT.video_frames)
                    or (v[0].get("width"),v[0].get("height")) != (CONTRACT.width, CONTRACT.height)
                    or v[0].get("r_frame_rate") != f"{CONTRACT.fps_num}/{CONTRACT.fps_den}"
                    or a[0].get("codec_name") != "aac"
                    or a[0].get("sample_rate") != str(CONTRACT.sample_rate)
                    or a[0].get("channels") != CONTRACT.channels):
                    raise AssertionError("Independent MP4 decoder did not verify exact AV output")
                decoded = av_receipt["decoded_audio"]
                wav_path = paths["output"] / decoded["path"]
                if not wav_path.is_file() or digest(wav_path) != decoded["sha256"]:
                    raise AssertionError("MLT native decoded PCM audit is missing or changed")
                # The actual MP4, not the receipt or source WAV, must retain
                # the authored, distinct 400/600 Hz left/right test signals.
                try:
                    decoded_content = inspect_native_master_decoded_content(master, ffmpeg, env, mode=MODE, g2=g2)
                finally:
                    if g2 is not None:
                        try:
                            from native_aac_clock_custody import retain_clock_diagnostic
                            clock_capture = retain_clock_diagnostic(
                                evidence_dir=g2.evidence_dir, phase="original", scenario="repaired-g2",
                                master=master, master_sha256=result["master_artifact_sha256"],
                                ffprobe=Path(ffprobe).resolve(strict=True), env=env,
                                source_document=EVIDENCE / "native-multisegment-timeline-evidence.json",
                                original_wav=g2.evidence_dir / "original-source.wav",
                                packets=g2.evidence_dir / "original-master-packets.json",
                                decoded_pcm=g2.evidence_dir / "original-master-decoded.s16le")
                            if clock_capture["state"] != "CAPTURED":
                                g2.secondary("original_clock_custody_incomplete")
                        except Exception:
                            g2.secondary("original_clock_custody_failed")
                write_private_json(EVIDENCE / "decoded-stereo-content-proof.json", decoded_content)
                retained_source = retain_native_master_source(
                    paths["output"], EVIDENCE, av_receipt, MODE,
                )
                shutil.copyfile(master, EVIDENCE / "native-multisegment-master.mp4")
                master_check = {
                    "retained_source_audio": retained_source,
                    "final_master_frames":CONTRACT.video_frames,
                    "final_master_video_codec":"h264",
                    "final_master_audio_codec":"aac",
                    "final_master_audio_rate":CONTRACT.sample_rate,
                    "final_master_audio_channels":CONTRACT.channels,
                    "final_master_sha256":digest(master),
                    "final_master_bytes":master.stat().st_size,
                    "measured_voice_pcm_frames":CONTRACT.pcm_frames,
                    "decoded_audio_sha256":decoded["sha256"],
                    "final_evidence_scope":av_receipt["evidence_scope"],
                    **decoded_content,
                }
                if CAPTIONED_MASTER:
                    # Independent FFmpeg readback of native FFV1 and actual
                    # lossy H264. This comparison neither adds a glyph nor
                    # synthesizes a new render or MLT composition.
                    proof = prove_native_captioned_master(
                        video_path, master, ffmpeg,
                        source_sha=GITHUB_SHA,
                        semwright_sha=PIN,
                        selected_cues=av_receipt["measured_voice"]["caption_cue_count"],
                    )
                    if (proof["original_ffv1_sha256"] != digest(video_path)
                        or proof["actual_master_mp4_sha256"] != digest(master)
                        or proof["captioned_master_decoded_pixels"] != "PASS"
                        or proof["native_cue_count"] != 2
                        or proof["commercial_release_authorized"] is not False):
                        raise AssertionError(
                            "Original native caption glyphs did not survive the actual lossy MLT master"
                        )
                    write_private_json(
                        EVIDENCE / "captioned-master-pixel-evidence.json", proof,
                    )
                    master_check.update({
                        "captioned_master_decoded_pixels": "PASS",
                        "captioned_source_cue_count": 2,
                        "native_caption_mask_pixels": {
                            key: value["native_caption_mask_pixels"]
                            for key, value in proof["active_caption_frames"].items()
                        },
                        "native_captioned_human_legibility": "NOT_RUN",
                    })
            if g2 is not None:
                master_check.update(g2.complete())
            write_private_json(
                EVIDENCE / "result.json",
                {
                    "native_mlt_multisegment_e2e": "PASS",
                    "motionwright_sha": GITHUB_SHA,
                    "semwright_sha": PIN,
                    "semwright_version": semwright_version(),
                    "project": seeded,
                    "motion_canvas_driver_sha256": digest(driver),
                    "mlt_driver_sha256": digest(mlt_driver),
                    "mlt_runner_sha256": digest(mlt_runner),
                    "melt_sha256": digest(melt),
                    "ffprobe_sha256": digest(ffprobe),
                    "ffmpeg_sha256": digest(ffmpeg),
                    "rendered_source_frames": list(CONTRACT.source_segment_frames),
                    "assembled_video_frames": CONTRACT.video_frames,
                    "mode": MODE,
                    "evidence_scope": EXPECTED_SCOPE,
                    "native_render_profile": PROFILE,
                    "provider_project_cleanup": evidence["provider_project_cleanup"],
                    "native_mlt_lifetime_inventory_sha256": digest(inventory_path),
                    "native_mlt_lifetime_confirmed_current_creations": current,
                    "native_mlt_lifetime_receipts_scanned": receipt_total,
                    "native_mlt_lifetime_close_requests_observed": 0,
                    "native_mlt_cleanup_verified": False,
                    "assembled_video_codec": "ffv1",
                    "assembled_video_has_audio": not VIDEO_ONLY,
                    "transport_audio_codec": None if VIDEO_ONLY else "pcm_s16le",
                    "video_bytes": video_path.stat().st_size,
                    "video_sha256": digest(video_path),
                    **master_check,
                },
            )
        except BaseException as error:
            primary_owner_error=error
            raise
        finally:
            try:close_native_owner(daemon,log_stream)
            except BaseException as cleanup:
                if g2 is not None:
                    try:
                        from native_mlt_g2_observation import bounded_error
                        g2.observer_error_details["existing_owner_close"]=bounded_error(cleanup)
                        g2.secondary("existing_owner_close")
                    except BaseException:pass
                if primary_owner_error is None:raise

    summary = {
        "native_mlt_multisegment_e2e": "PASS",
        "motionwright_sha": GITHUB_SHA,
        "semwright_sha": PIN,
        "evidence": str(EVIDENCE.relative_to(ROOT)) if g2 is None else "private-g2-producer",
    }
    if g2 is None:
        print(json.dumps(summary, sort_keys=True))
    return summary


def run_g2_with_frozen_expectation(session):
    """Freeze existing preflight metadata before the CLI leaves this G2 session."""
    original_error=None
    try:
        return main(session)
    except BaseException as error:
        original_error=error
        raise
    finally:
        try:
            from native_mlt_g2_custody import freeze_ffv1_preflight_expectation
            freeze_ffv1_preflight_expectation(os.environ,session.evidence_dir)
        except BaseException:
            if original_error is None:
                raise
            # A failed/UNKNOWN original command remains primary even if the
            # metadata writer or secondary-receipt writer also fails.
            try:
                session.secondary("ffv1_preflight_expectation_custody")
            except BaseException:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    selected=parser.add_mutually_exclusive_group()
    selected.add_argument("--g2-inputs", type=Path)
    selected.add_argument("--hybrid-inputs", type=Path)
    args = parser.parse_args()
    if args.hybrid_inputs is not None:
        if not HYBRID:
            raise SystemExit("--hybrid-inputs requires MOTIONWRIGHT_MLT_MODE=hybrid-av")
        from native_mlt_g2_observation import G2Session
        with G2Session.prepare_hybrid(args.hybrid_inputs,source_sha=GITHUB_SHA,semwright_sha=PIN,source_root=ROOT) as session:
            summary=main(session)
        print(json.dumps(summary,sort_keys=True))
    elif args.g2_inputs is None:
        main()
    else:
        from native_mlt_g2_observation import G2Session
        with G2Session.prepare(
            args.g2_inputs, source_sha=GITHUB_SHA, semwright_sha=PIN, mode=MODE,
        ) as session:
            summary = run_g2_with_frozen_expectation(session)
        # A secondary custody failure cannot print an overall success line.
        print(json.dumps(summary, sort_keys=True))
