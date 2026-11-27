"""Bind one installed AAC31 observation to the AppImage built in this Actions run."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace
import wave

import ci_private_evidence as seal
import linux_installed_aac31_readback as readback
import native_host_installed_shared_state as installed
import native_mlt_sequence_e2e as native
import source_checkout_observation as source

SAMPLES = 49_600
RATE = 48_000
WAVE_SHA = "6711ccfcb80db90c9ef9ff554400a2a7376db267523e73e43aebdfc4e420ef53"
BUNDLE_LIMIT = installed.MAX_INPUT_BYTES
CAPTURE_LIMIT = seal.MAX_FILE - 8192


def write_wave(path):
    samples = b"".join(
        struct.pack("<hh", int(4400 * math.sin(math.tau * 431 * i / RATE)),
                    int(6500 * math.sin(math.tau * 701 * i / RATE)))
        for i in range(SAMPLES)
    )
    with path.open("xb") as original:
        with wave.open(original, "wb") as pcm:
            pcm.setparams((2, 2, RATE, SAMPLES, "NONE", "not compressed"))
            pcm.writeframes(samples)
        original.flush()
        os.fsync(original.fileno())
    observed = source.regular_file(path, 44 + 4 * SAMPLES)
    installed.need(observed["sha256"] == WAVE_SHA and observed["bytes"] == 44 + 4 * SAMPLES,
                   "AAC31_SYNTHETIC_PCM_IDENTITY")
    return observed


def run_original(argv, evidence, label, *, env, seconds):
    with (evidence / (label + ".stdout")).open("xb") as out, (
            evidence / (label + ".stderr")).open("xb") as errors:
        result = subprocess.run(argv, cwd=installed.ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=errors, timeout=seconds, check=False)
        for stream in (out, errors):
            stream.flush()
            os.fsync(stream.fileno())
    installed.need(result.returncode == 0, label + "_FAILED")


def verify_recipient():
    recipient = {"spki_der_base64": os.environ.pop("PRIVATE_EVIDENCE_RECIPIENT_SPKI"),
                 "sha256": os.environ.pop("PRIVATE_EVIDENCE_RECIPIENT_SHA256")}
    seal.public_key(recipient)
    return recipient


def admit_candidate(bundle, retained_package, *, readback_only=False):
    env = os.environ
    source_sha = env["GITHUB_SHA"]
    run_id = env["GITHUB_RUN_ID"]
    installed.need(
        sys.platform == "linux" and env.get("RUNNER_OS") == "Linux"
        and env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("GITHUB_RUN_ATTEMPT") == "1" and env.get("GITHUB_JOB") == "linux-installed-aac31"
        and env.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
        and env.get("GITHUB_REF") == "refs/heads/integrate/phase1-final-composition-20261027"
        and re.fullmatch(r"[a-f0-9]{40}", source_sha) and re.fullmatch(r"[1-9][0-9]*", run_id)
        and env.get("DISPLAY") and env.get("DBUS_SESSION_BUS_ADDRESS")
        and shutil.which("bwrap") == "/usr/bin/bwrap" and native.HYBRID,
        "AAC31_ORIGINAL_WORKFLOW_REQUIRED",
    )
    runner = Path(env["RUNNER_TEMP"])
    installed.need(runner.is_absolute() and runner == runner.resolve(strict=True)
                   and runner.is_dir() and bundle.is_relative_to(runner)
                   and bundle == bundle.resolve(strict=True), "AAC31_PRIVATE_RUNNER_ROOT_REQUIRED")
    namespace = runner / ("mwa-" + run_id)
    installed.need(len(os.fsencode(namespace / "o/runtime/broker.sock")) < 108
                   and not namespace.exists() and not namespace.is_symlink(),
                   "AAC31_ORIGINAL_NAMESPACE_REQUIRED")
    [artifact] = bundle.rglob("*.AppImage")
    [receipt] = bundle.rglob("linux-candidate.json")
    inputs = {"artifact": source.regular_file(artifact, BUNDLE_LIMIT),
              "receipt": source.regular_file(receipt, 4 * 1024 * 1024)}
    installed.need(shutil.disk_usage(runner).free > 2 * inputs["artifact"]["bytes"] + 1024**3,
                   "AAC31_RUNNER_DISK_BUDGET")
    args = SimpleNamespace(repo=installed.ROOT, semwright_checkout=native.SEMWRIGHT,
                           source_sha=source_sha, semwright_sha=native.PIN, namespace=namespace,
                           artifact=artifact, candidate_receipt=receipt)
    if retained_package is not None:
        installed.need(retained_package == runner / "aac31-retained-package.json",
                       "AAC31_RETAINED_PACKAGE_PATH")
        retained = installed.read_json(retained_package, 4096)
        candidate_sha = retained["source_sha"]
        installed.need(re.fullmatch(r"[a-f0-9]{40}", candidate_sha)
                       and retained["render_step"] == "skipped", "AAC31_RETAINED_PACKAGE_IDENTITY")
        changed = source.git(args.repo, "diff", "--name-only", "-z", candidate_sha, source_sha, "--")
        installed.need(set(changed.split(b"\0")) <= {
            b"", b".github/workflows/package-candidate.yml", b"tooling/linux_installed_aac31_ci.py",
            b"tooling/native_host_installed_shared_state.py",
            b"tooling/tests/test_ci_nsis_source_selection.py",
            b"tooling/linux_installed_aac31.py", b"tooling/tests/test_linux_installed_aac31.py",
            b"tooling/linux_hybrid_original_receipt.py", b"tooling/linux_installed_aac31_readback.py",
            b".github/workflows/v05-otio-real-media.yml", b"tooling/tests/test_ci_otio_alab_readonly.py",
            b"tooling/otio-media/kdenlive_complete_g2_fixture.py",
            b"tooling/otio-media/kdenlive_native_source.py",
            b"tooling/otio-media/test_kdenlive_native_source.py",
            b"tooling/otio-media/setup_windows_fixture_ffmpeg.ps1",
            b"tooling/otio-media/kdenlive_windows_editor.ps1",
            b"tooling/otio-media/kdenlive_windows_editor.py",
            b"tooling/otio-media/kdenlive_macos_editor.py",
            b"tooling/otio-media/test_kdenlive_macos_editor.py",
            b"tooling/otio-media/kdenlive_macos_reopen.py",
            b"tooling/otio-media/test_kdenlive_macos_reopen.py",
            b"scripts/candidate_dependency_capture.py",
            b"scripts/test_candidate_dependency_capture.py",
            b"docs/creative-production/V05_OTIO_MEDIA_INTERCHANGE.md",
            b"tooling/otio-media/kdenlive_native_distribution.py",
            b"tooling/otio-media/kdenlive_native_installation.py",
            b"tooling/otio-media/test_kdenlive_native_distribution.py",
            b"tooling/otio-media/test_kdenlive_native_installation.py",
        }, "AAC31_RETAINED_PRODUCT_SOURCE_CHANGED")
        inputs["candidate_origin"] = retained
    else:
        inputs["candidate_origin"] = {"source_sha": source_sha, "run_id": run_id}
    if readback_only:
        installed.need(retained_package is not None, "AAC31_READBACK_REQUIRES_ORIGINAL_APPIMAGE")
    else:
        native.exact_inputs()
    inputs["source"] = installed.source_inputs(args)
    return args, inputs


def observe(args, inputs, evidence, owner, profile, database, apparmor, manim_inventory,
            *, inspect_delivery):
    installed.publish(evidence / "candidate-inputs.json", inputs)
    installed.publish(evidence / "sandbox-profile.json", installed.ci_verify_sdk_profile_sidecar(apparmor))
    with installed.supervise_own_processes():
        original = installed.extract_installed_bundle(
            args, evidence=evidence, candidate_source_sha=inputs["candidate_origin"]["source_sha"],
        )
    env = dict(os.environ)
    owner_paths, owner_env = native.native_owner_layout(owner, hybrid=True)
    wave_path = evidence / "aac31-stereo.wav"
    audio = write_wave(wave_path)
    with installed.supervise_own_processes(), installed.owned_process_scope(evidence / "broker-closure.json"):
        run_original([str(native.MW_BIN), "seed-installed-aac31", str(database),
                      str(wave_path), audio["sha256"]], evidence, "studio-seed", env=owner_env, seconds=60)
        seeded = installed.read_json(evidence / "studio-seed.stdout", 64 * 1024)
        request = evidence / "desktop-request.json"
        desktop = evidence / "desktop"
        desktop.mkdir(mode=0o700)
        primary = {
            "source_sha": args.source_sha, "semwright_sha": args.semwright_sha,
            "app_run": str(args.app_run), "binary": str(args.installed_binary),
            "profile": str(profile), "database": str(database), "output": str(owner_paths["output"]),
            "evidence": str(desktop), "project_title": seeded["project_title"],
            "source": {key: seeded[key] for key in ("project", "generation", "revision", "deliverable")},
        }

        def run_desktop(fields, flags, timeout):
            installed.publish(request, {**primary, **fields})
            run_original(["/usr/bin/python3", "-B", "-E", "-s",
                          str(installed.ROOT / "tooling/linux_installed_aac31.py"),
                          "--request", str(request), *flags], evidence, "installed-desktop",
                         env=env, seconds=timeout)

        resource_key = "project:" + seeded["project"]
        with native.native_owner_broker(owner_paths, owner_env, resource_key, evidence,
                                       hybrid=True, manim_inventory=manim_inventory) as (
            _, connection, ffmpeg, ffprobe, tools,
        ):
            installed.publish(evidence / "provider-bindings.json",
                              {key: native.digest(path) for key, path in tools.items()})
            run_desktop({
                "connection": str(connection), "connection_sha256": native.digest(connection),
                "wave": str(wave_path), "wave_sha256": audio["sha256"],
                "ffmpeg": str(ffmpeg), "ffmpeg_sha256": native.digest(ffmpeg),
                "ffprobe": str(ffprobe), "ffprobe_sha256": native.digest(ffprobe),
                "seconds": 90 if inspect_delivery else 540,
            }, ["--inspect-delivery"] if inspect_delivery else [], 105 if inspect_delivery else 630)
    installed.need(inputs["artifact"] == source.regular_file(args.artifact, BUNDLE_LIMIT)
                   and inputs["receipt"] == source.regular_file(args.candidate_receipt, 4 * 1024 * 1024)
                   and original["extracted_tree"] == installed.installed_bundle_inventory(args.app_run.parent)
                   and inputs["source"] == installed.source_inputs(args)
                   and audio == source.regular_file(wave_path, 44 + 4 * SAMPLES),
                   "AAC31_ORIGINAL_INPUT_CHANGED")
    name = "delivery-observation.json" if inspect_delivery else "installed-aac31.json"
    return installed.read_json(evidence / "desktop" / name)


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--retained-package", type=Path)
    parser.add_argument("--sandbox-profile", type=Path, required=True)
    parser.add_argument("--manim-inventory", type=Path)
    observation = parser.add_mutually_exclusive_group()
    observation.add_argument("--inspect-delivery", action="store_true")
    observation.add_argument("--retained-render-run")
    parser.add_argument("--restore-sha256")
    arguments = parser.parse_args()
    readonly = arguments.retained_render_run is not None
    installed.need(readonly == (arguments.restore_sha256 is not None)
                   and (readonly or arguments.manim_inventory is not None),
                   "AAC31_OBSERVATION_INPUTS_INVALID")
    os.umask(0o077)
    recipient = verify_recipient()
    args, inputs = admit_candidate(arguments.bundle, arguments.retained_package, readback_only=readonly)
    root = args.namespace
    root.mkdir(mode=0o700)
    evidence, owner, profile = root / "evidence", root / "o", root / "gui"
    for path in (evidence, owner, profile):
        path.mkdir(mode=0o700)
    database = profile / "data/com.seradotcom.motionwright/motionwright.sqlite3"
    database.parent.mkdir(mode=0o700, parents=True)
    transport = root / "transport"
    transport.mkdir(mode=0o700)
    context = {"source_sha": args.source_sha, "semwright_sha": args.semwright_sha,
               "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1"}
    terminal = {**context, "schema": "motionwright.ci.installed-aac31-custody/1",
                "observation": "UNKNOWN", "automatic_retry": False, "product_acceptance": False,
                "observation_scope": ("retained-original-readback" if readonly else
                                      "delivery-controls" if arguments.inspect_delivery else "aac31-original"),
                "candidate_source_sha": inputs["candidate_origin"]["source_sha"],
                "candidate_run_id": inputs["candidate_origin"]["run_id"]}
    installed.publish(evidence / "intent.json",
                      {**context, "render_dispatch_limit": 0 if readonly or arguments.inspect_delivery else 1,
                       "automatic_retry": False,
                       "candidate_sha256": inputs["artifact"]["sha256"]})
    report = None
    try:
        original_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        effective_limit = min(CAPTURE_LIMIT, original_limit[0]) if original_limit[0] >= 0 else CAPTURE_LIMIT
        resource.setrlimit(resource.RLIMIT_FSIZE, (effective_limit, original_limit[1]))
        try:
            if readonly:
                report = readback.run_readback(args, inputs, evidence, arguments.retained_render_run,
                                               arguments.restore_sha256, run_original)
            else:
                report = observe(args, inputs, evidence, owner, profile, database,
                                 arguments.sandbox_profile, arguments.manim_inventory,
                                 inspect_delivery=arguments.inspect_delivery)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, original_limit)
    except Exception as error:
        installed.publish(evidence / "failure.json", {"type": type(error).__name__, "detail": str(error)})
    roots = {"evidence": evidence, "owner": profile / "data", "output": owner / "output",
              "configuration": owner / "config", "projects": owner / "project", "runtime-state": owner / "state"}
    if readonly:
        roots = {"evidence": evidence}
    try:
        raw, complete = seal.gather_snapshot(roots, preserve_partial=True, max_archive_bytes=CAPTURE_LIMIT)
        encoded = seal.encrypt_snapshot(raw, recipient, context)
        installed.need(len(encoded) <= seal.MAX_FILE, "AAC31_SEALED_LIMIT")
        sealed = transport / "installed-aac31.mwseal"
        seal.new_bytes(sealed, encoded)
        terminal.update(sealed_sha256=seal.sha(encoded), complete_private_custody=complete)
        if report is not None and complete:
            if readonly:
                terminal.update(observation="READBACK_PASS", retained_render_run_id=report["retained_render_run_id"],
                                original_mp4_sha256=report["original_sha256"])
            elif arguments.inspect_delivery:
                terminal.update(observation="PROBE_PASS", source_check_enabled=report["source_check_enabled"])
            else:
                terminal.update(observation="PASS", original_mp4_sha256=report["original_sha256"],
                                presentation_samples=SAMPLES, video_frames=31)
    finally:
        installed.publish(transport / "status.json", terminal)
        print("Installed AAC31 observation " + terminal["observation"])
    return 0 if terminal["observation"] in {"PASS", "PROBE_PASS", "READBACK_PASS"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("Installed AAC31 harness stopped: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
