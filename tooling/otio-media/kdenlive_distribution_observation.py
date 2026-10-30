#!/usr/bin/env python3
"""One static inventory of the pinned distribution; never start its editor.

AppImage runtime extraction is an observed subprocess, not a GUI operation.
Library presence, filenames, package metadata and ELF declarations do not prove
which libraries any previous or future Kdenlive process loads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_storage_apply_recovery as commands
from mlt_official_runtime_intake import capture_regular

APPIMAGE_SHA256 = "38ec1bdec007ccd9171f951ba00ded8cc2dd43570c14a438e88fabb4f0a81f4f"
APPIMAGE_URL = "https://download.kde.org/Attic/kdenlive/25.08/linux/kdenlive-25.08.3-x86_64.AppImage"
WORKFLOW = ".github/workflows/v05-otio-real-media.yml"
SELF = "tooling/otio-media/kdenlive_distribution_observation.py"
TEST = "tooling/otio-media/test_kdenlive_distribution_observation.py"
SOURCE_PATHS = (WORKFLOW, SELF, TEST, "tooling/ci_storage_apply_recovery.py",
                "tooling/ci_private_evidence.py", "tooling/mlt_official_runtime_intake.py",
                "tooling/ci_test_runner.py", "tooling/requirements/mlt-g2-custody.txt",
                "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml",
                "apps/studio/package-lock.json")
LAUNCHERS = ("AppRun", "apprun-hooks/craft-runenv-hook.sh",
             "apprun-hooks/linuxdeploy-plugin-qt-hook.sh")
MARKERS = (b"QT_ACCESSIBILITY", b"QT_LINUX_ACCESSIBILITY_ALWAYS_ON",
           b"org.a11y.Bus", b"org.a11y.Status", b"org.a11y.atspi.Registry")
MAX_IMAGE = 1024 * 1024 * 1024
MAX_ELF = 128 * 1024 * 1024
MAX_HASHED = 512 * 1024 * 1024
MAX_MEMBERS = 50000
MAX_SELECTED = 48
MAX_LOG = 16 * 1024 * 1024


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate JSON member")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    need(type(value) is dict, "JSON object required")
    return value


def new_bytes(path, raw):
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    need(path.read_bytes() == raw, "exclusive original readback differs")


def new_json(path, value):
    new_bytes(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def current_context(env=None, platform=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    required = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": "kdenlive-distribution-observation", "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64", "MW_KDENLIVE_DISTRIBUTION_SCOPE": "kdenlive-distribution"}
    need(platform == "linux" and all(env.get(k) == v for k, v in required.items()),
         "only the explicit distribution observation is admitted")
    source = env.get("GITHUB_SHA", "")
    need(re.fullmatch(r"[0-9a-f]{40}", source)
         and env.get("EXPECTED_SOURCE_SHA") == source == env.get("GITHUB_WORKFLOW_SHA")
         and env.get("GITHUB_WORKFLOW_REF", "").startswith(
             "seradotcom/motionwright/" + WORKFLOW + "@"), "source/workflow identity differs")
    run = env.get("GITHUB_RUN_ID", "")
    need(re.fullmatch(r"[1-9][0-9]*", run), "run identity missing")
    return {"source_sha": source, "run_id": run, "run_attempt": "1"}


def layout(context):
    temp = Path(os.environ["RUNNER_TEMP"])
    need(temp.is_absolute() and temp.is_dir() and temp.resolve(strict=True) == temp
         and all(not p.is_symlink() for p in (temp, *temp.parents)), "runner temp is not canonical")
    owner = temp / ("kdenlive-distribution-" + context["run_id"] + "-1")
    return owner, owner / "originals", temp / ("kdenlive-distribution-public-" + context["run_id"] + "-1")


def context_and_source():
    context = current_context()
    need(os.getuid() > 0, "disposable non-root observation required")
    snapshot = commands.source_snapshot(ROOT, context["source_sha"], SOURCE_PATHS)
    lock = strict((ROOT / "SOURCE_LOCK.json").read_bytes())
    sdk = lock["dependencies"]["semwright"]["revision"]
    need(re.fullmatch(r"[0-9a-f]{40}", sdk), "selected SDK identity missing")
    context["semwright_sha"] = sdk
    return context, snapshot


def preflight():
    context, source = context_and_source()
    owner, originals, public = layout(context)
    need(not owner.exists() and not owner.is_symlink()
         and not public.exists() and not public.is_symlink(), "observation paths already reserved")
    owner.mkdir(mode=0o700)
    originals.mkdir(mode=0o700)
    new_json(originals / "PREPARATION.original.json", {
        "context": context, "source": source, "deadline_monotonic": time.monotonic() + 12 * 60,
        "whole_job_minutes": 15, "sealing_reserve_seconds": 120,
        "editor_launches": 0, "automatic_retry": False})


def start():
    import ci_private_evidence as custody
    context, source = context_and_source()
    recipient_raw = os.environ["DISTRIBUTION_RECIPIENT_JSON"].encode()
    need(0 < len(recipient_raw) <= 8192, "public recipient size")
    recipient = strict(recipient_raw)
    custody.public_key(recipient)
    owner, originals, public = layout(context)
    preparation = strict((originals / "PREPARATION.original.json").read_bytes())
    need(preparation["context"] == context and preparation["source"]["files"] == source["files"]
         and not public.exists() and not public.is_symlink()
         and not (originals / "STARTED.original.json").exists(), "preparation identity changed or repeated")
    for name in ("commands", "launchers", "sources", "metadata", "controls"):
        (originals / name).mkdir(mode=0o700)
    for name in source["files"]:
        target = originals / "sources" / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        new_bytes(target, (ROOT / name).read_bytes())
    new_bytes(originals / "recipient.public.original.json", recipient_raw)
    new_json(originals / "STARTED.original.json", {
        "kind": "PINNED_KDENLIVE_STATIC_DISTRIBUTION_OBSERVATION", "context": context,
        "source": source, "deadline_monotonic": preparation["deadline_monotonic"],
        "recipient_spki_sha256": recipient["sha256"], "recipient_raw_sha256": sha(recipient_raw),
        "appimage_sha256": APPIMAGE_SHA256, "appimage_url": APPIMAGE_URL,
        "editor_launches": 0, "GUI_operations": 0, "native_render_calls": 0,
        "extraction_calls_maximum": 1, "automatic_retry": False,
        "loaded_runtime_verified": False, "G2_verified": False, "formal_acceptance": False})


def download_argv(image, headers):
    return ["/usr/bin/curl", "--fail", "--location", "--proto", "=https",
            "--proto-redir", "=https", "--connect-timeout", "30", "--max-time", "240",
            "--max-filesize", str(MAX_IMAGE), "--dump-header", str(headers),
            "--output", str(image), APPIMAGE_URL]


def extraction_argv(image):
    return [str(image), "--appimage-extract"]


def check_image(image, owner):
    observed = commands.fingerprint(image, owner, MAX_IMAGE)
    need(observed["sha256"] == APPIMAGE_SHA256, "distribution digest differs; extraction refused")
    return observed


def resolve_member(root, relative):
    need(type(relative) is str and relative and "\\" not in relative
         and not relative.startswith("/") and ":" not in relative
         and all(p not in ("", ".", "..") for p in relative.split("/")), "invalid member path")
    need(root.is_absolute() and root.resolve(strict=True) == root
         and all(not p.is_symlink() for p in (root, *root.parents)), "distribution root is not canonical")
    requested = root / relative
    for parent in requested.parents:
        if parent == root:
            break
        need(not parent.is_symlink(), "linked member parent")
    before = requested.lstat()
    resolved = requested.resolve(strict=True)
    need(resolved.is_relative_to(root) and resolved != root
         and resolved.is_file(), "member escaped distribution or is not regular")
    link = os.readlink(requested) if stat.S_ISLNK(before.st_mode) else None
    after = requested.lstat()
    need(commands.fingerprint(resolved, root, MAX_ELF)["bytes"] <= MAX_ELF
         and (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
         == (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns)
         and requested.resolve(strict=True) == resolved, "member changed during resolution")
    return resolved, link


def selected_name(relative):
    name = Path(relative).name
    return (name in ("kdenlive", "AppRun.wrapped") or name.startswith(("libQt6Core.so", "libQt6Gui.so", "libQt6DBus.so", "libQt6XcbQpa.so"))
            or name in ("libqxcb.so", "libqspiaccessiblebridge.so", "libqtaccessiblewidgets.so")
            or "atspi" in name.lower() or "accessib" in name.lower())


def namespace(root, maximum=MAX_MEMBERS):
    result = []
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for item in entries:
                need(len(result) < maximum, "distribution namespace budget exceeded")
                relative = Path(item.path).relative_to(root).as_posix()
                info = item.stat(follow_symlinks=False)
                kind = "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "regular" if stat.S_ISREG(info.st_mode) else "other"
                row = {"path": relative, "kind": kind, "bytes": info.st_size}
                if kind == "symlink":
                    row["link_target"] = os.readlink(item.path)
                if kind == "directory":
                    stack.append(Path(item.path))
                result.append(row)
    return sorted(result, key=lambda row: row["path"])


def elf_observation(path, root):
    before = commands.fingerprint(path, root, MAX_ELF)
    present = {marker.decode(): False for marker in MARKERS}
    with path.open("rb") as handle:
        header = handle.read(64)
        need(header.startswith(b"\x7fELF") and len(header) == 64
             and header[4:6] == b"\x02\x01", "not an ELF64 little-endian object")
        tail = header
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            joined = tail + chunk
            for marker in MARKERS:
                present[marker.decode()] |= marker in joined
            tail = joined[-128:]
    need(commands.fingerprint(path, root, MAX_ELF) == before, "ELF changed during static inspection")
    version = re.search(r"\.so\.(\d+\.\d+\.\d+)$", path.name)
    return {"original": before, "filename_version_hint": version.group(1) if version else None,
            "marker_bytes_present": present, "version_function_executed": False,
            "loaded_runtime_verified": False}


def observe():
    context, source = context_and_source()
    owner, originals, _ = layout(context)
    started = strict((originals / "STARTED.original.json").read_bytes())
    need(started["context"] == context and source["files"] == started["source"]["files"], "observation source changed")
    new_json(originals / "OBSERVATION_INTENT.original.json", {"context": context, "automatic_retry": False})
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    env.update(LC_ALL="C", LANG="C")
    observed_commands = []
    report = {"kind": "STATIC_DISTRIBUTION_FILES_ONLY", "context": context, "status": "NOT_VERIFIED",
              "editor_launches": 0, "GUI_operations": 0, "native_render_calls": 0,
              "automatic_retry": False, "original_unknown_run_replayed": False,
              "loaded_runtime_verified": False, "G2_verified": False, "formal_acceptance": False}

    def command(label, argv, seconds, cwd=ROOT):
        need(time.monotonic() + seconds + 30 < started["deadline_monotonic"], "observation time budget")
        row = commands.execute(argv, cwd, env, originals / "commands", label, seconds, context)
        observed_commands.append(row)
        need(not commands.execution_unknown(row) and row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
             and row.get("owned_group_after_wait") == "ABSENT" and row.get("exit_code") == 0
             and row.get("log_custody") == "VERIFIED_SNAPSHOT", "command failed or UNKNOWN; no replay:" + label)
        need(all(row[s]["bytes"] <= MAX_LOG for s in ("stdout", "stderr")), "command original exceeds observation budget")
        return commands.read_original(originals / "commands", row, "stdout")

    try:
        image = owner / "kdenlive-25.08.3-x86_64.AppImage"
        command("download-once", download_argv(image, originals / "download.headers.original"), 250)
        image_original = check_image(image, owner)
        new_json(originals / "appimage.original.json", {"url": APPIMAGE_URL, "original": image_original,
            "expected_distribution_version": "25.08.3", "editor_version_executed": False,
            "input_archive_not_copied_into_selected_evidence": True})
        image.chmod(0o700)
        extraction = owner / "extracted"
        extraction.mkdir(mode=0o700)
        command("distribution-extract-once", extraction_argv(image), 180, extraction)
        appdir = extraction / "squashfs-root"
        need(appdir.is_dir() and appdir.resolve(strict=True) == appdir, "extracted distribution missing")
        image_after = check_image(image, owner)
        need((image_after["sha256"], image_after["bytes"]) == (image_original["sha256"], image_original["bytes"]), "AppImage changed after extraction")
        rows = namespace(appdir)
        new_json(originals / "namespace.original.json", rows)
        launchers = []
        for relative in LAUNCHERS:
            resolved, link = resolve_member(appdir, relative)
            raw, identity = capture_regular(resolved, 4 * 1024 * 1024)
            filename = relative.replace("/", "__")
            new_bytes(originals / "launchers" / filename, raw)
            launchers.append({"path": relative, "resolved_relative": resolved.relative_to(appdir).as_posix(),
                "symlink_target": link, "retained": "launchers/" + filename, "bytes": len(raw), "sha256": sha(raw),
                "regular_observation": identity})
        new_json(originals / "launchers.original.json", launchers)
        selected = [row for row in rows if row["kind"] in ("regular", "symlink") and selected_name(row["path"])]
        need(len(selected) <= MAX_SELECTED, "selected ELF namespace budget")
        targets, aliases, total = {}, [], 0
        for row in selected:
            resolved, link = resolve_member(appdir, row["path"])
            aliases.append({"path": row["path"], "resolved_relative": resolved.relative_to(appdir).as_posix(), "symlink_target": link})
            if resolved in targets:
                continue
            with resolved.open("rb") as stream:
                magic = stream.read(4)
            if magic != b"\x7fELF":
                continue
            total += resolved.stat().st_size
            need(total <= MAX_HASHED, "selected ELF aggregate read budget")
            record = elf_observation(resolved, appdir)
            label = "appdir-elf-" + str(len(targets))
            command(label, ["/usr/bin/readelf", "--file-header", "--dynamic", "--notes", str(resolved)], 15)
            record["readelf_original_prefix"] = "commands/" + label
            targets[resolved] = record
        need(any(p.name.startswith("libQt6Core.so") for p in targets)
             and any(p.name.startswith("libQt6Gui.so") for p in targets), "core Qt ELF objects not observed")
        command("runner-package-versions", ["/usr/bin/dpkg-query", "--show",
            "--showformat=${binary:Package}\t${Version}\t${Architecture}\n",
            "libatspi2.0-0t64", "at-spi2-core", "gir1.2-atspi-2.0", "binutils"], 15)
        command("runner-atspi-package-files", ["/usr/bin/dpkg-query", "--listfiles", "libatspi2.0-0t64", "at-spi2-core"], 15)
        atspi, link = resolve_member(Path("/usr"), "lib/x86_64-linux-gnu/libatspi.so.0")
        atspi_observation = elf_observation(atspi, Path("/usr"))
        command("runner-atspi-elf", ["/usr/bin/readelf", "--file-header", "--dynamic", "--notes", str(atspi)], 15)
        for row in rows:
            if row["kind"] != "regular" or Path(row["path"]).name not in (
                "qconfig.pri", "Qt6CoreConfigVersion.cmake", "org.kde.kdenlive.appdata.xml", "org.kde.kdenlive.metainfo.xml"):
                continue
            resolved, _ = resolve_member(appdir, row["path"])
            raw, _ = capture_regular(resolved, 2 * 1024 * 1024)
            new_bytes(originals / "metadata" / row["path"].replace("/", "__"), raw)
        after_context, after_source = context_and_source()
        need(after_context == context and after_source["files"] == source["files"], "source changed during observation")
        report.update(status="STATIC_DISTRIBUTION_OBSERVATION_COMPLETE", appdir_elfs=list(targets.values()),
            aliases=aliases, runner_atspi=atspi_observation, runner_atspi_alias=link,
            extraction_processes=1, hashed_selected_elf_bytes=total,
            interpretation="Declared ELF NEEDED/SONAME/RPATH and filename version hints only; no ldd, library load, AT-SPI init, editor or GUI action")
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        if isinstance(error, commands.UnknownObservation):
            report["unretained_command_observation"] = error.command
        raise
    finally:
        report["command_count"] = len(observed_commands)
        report["commands"] = observed_commands
        report["extraction_processes_with_pid_observed"] = sum(
            row.get("argv", [])[1:] == ["--appimage-extract"] and row.get("pid") is not None
            for row in observed_commands)
        new_json(originals / "OBSERVATION_RESULT.original.json", report)


def seal():
    import ci_private_evidence as custody
    context = current_context()
    owner, originals, public = layout(context)
    started = strict((originals / "STARTED.original.json").read_bytes())
    need(all(started["context"][key] == value for key, value in context.items()), "seal belongs to another observation")
    context = started["context"]
    recipient_raw = (originals / "recipient.public.original.json").read_bytes()
    recipient = strict(recipient_raw)
    need(sha(recipient_raw) == started["recipient_raw_sha256"]
         and recipient["sha256"] == started["recipient_spki_sha256"], "recipient changed since admission")
    custody.public_key(recipient)
    need(not public.exists() and not public.is_symlink(), "distribution evidence already sealed")
    public.mkdir(mode=0o700)
    result_path = originals / "OBSERVATION_RESULT.original.json"
    try:
        result = strict(capture_regular(result_path, 4 * 1024 * 1024)[0]) if result_path.exists() else None
    except Exception:
        result = None  # Preserve the malformed original in the one partial snapshot.
    bootstrap = owner.parent / ("kdenlive-distribution-bootstrap-" + context["run_id"] + "-1")
    payload, complete = custody.gather_snapshot({"distribution": originals, "bootstrap": bootstrap}, preserve_partial=True)
    encrypted = custody.encrypt_snapshot(payload, recipient, context)
    custody.new_bytes(public / "original-evidence.mwseal", encrypted)
    status = "STATIC_DISTRIBUTION_OBSERVED" if complete and result and result.get("status") == "STATIC_DISTRIBUTION_OBSERVATION_COMPLETE" else "NOT_VERIFIED"
    custody.new_json(public / "projection.json", {"schema": "motionwright.kdenlive-distribution-observation/1",
        "context": context, "status": status, "ciphertext_sha256": sha(encrypted),
        "snapshot_collection_complete": complete, "snapshot_is_atomic_process_state": False,
        "editor_launches": 0, "GUI_operations": 0, "native_render_calls": 0,
        "loaded_runtime_verified": False, "G2_verified": False, "automatic_retry": False,
        "original_unknown_run_replayed": False, "formal_acceptance": False, "release_authorized": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("preflight", "start", "observe", "seal"):
        modes.add_argument("--" + mode, action="store_true")
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.start:
        start()
    elif args.observe:
        observe()
    else:
        seal()


if __name__ == "__main__":
    main()
