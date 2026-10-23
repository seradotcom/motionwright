#!/usr/bin/env python3
"""Observe Apple package metadata on one retained candidate without launching it.

The original build and this observation keep separate source/run identities.
Process and stream custody comes from the existing Foundation observer.
A completed observation is not signing, notarization or release acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import sys
import zipfile

try:
    from tooling import ci_foundation_causal as observer
    from tooling import ci_storage_apply_recovery as custody
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_foundation_causal as observer
    import ci_storage_apply_recovery as custody

WORKFLOW = ".github/workflows/macos-package-metadata.yml"
HELPER = "tooling/ci_macos_package_metadata.py"
TEST = "tooling/tests/test_ci_macos_package_metadata.py"
SCOPE = "retained_macos_79929"
SOURCE = "79929a71173d8f56422007ed944c7d6db42d879f"
SDK = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
RUN, ATTEMPT, JOB, ARTIFACT = 39905976169, 1, 120258026519, 12481147459
REPOSITORY, REPOSITORY_ID = "seradotcom/motionwright", 1406314887
ZIP_SHA = "50f503af62973d2939a3df6af694c120c7675779298dc43bba0936d0151ef49c"
ZIP_BYTES = 10651085
DMG_NAME = "Motionwright_0.1.0_aarch64.dmg"
DMG_MEMBER = "target/release/bundle/dmg/" + DMG_NAME
CANDIDATE_MEMBER = "ci-evidence/macos-candidate.json"
SMOKE_MEMBER = "ci-evidence/macos-dmg-smoke.json"
MEMBERS = {
    DMG_MEMBER: (10689130, "93a9bc75f756853a9e9553f29ba9a3a432c22a8259c6b032a037d03904c5be0b"),
    CANDIDATE_MEMBER: (728, "a9d4266a5a1101dd355fbc43a40f1d7a2d0f46c793c08985cc1bcd1ee370ec22"),
    SMOKE_MEMBER: (1218, "9f3d6ac0b0d2940723bbd46ed3628282822a4214ddeb587e47cb510ed55696d1"),
}
BINARY = (35513744, "af033fee782aecf28ffd536e6756c173df6ec20c711bd8fe561a815f6330d2c5")
PINS = {
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
}
GATE_FILES = (WORKFLOW, HELPER, TEST, "tooling/ci_test_runner.py", *PINS)
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
require = custody.require


def strict_object(raw):
    value = custody.strict_json(raw)
    require(type(value) is dict, "original JSON root is not an object")
    return value


def exact_integer(value, expected):
    return type(value) is int and value == expected


def execution_context(environment, runtime_platform=None):
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "darwin" and environment.get("RUNNER_OS") == "macOS",
            "metadata observation requires a real disposable macOS runner")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("MW_MAC_METADATA_SCOPE") == SCOPE,
            "metadata observation requires its explicit reviewed selection")
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY
            and environment.get("GITHUB_JOB") == "macos-package-metadata",
            "wrong repository or selected job")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA40.fullmatch(environment.get(key, "")) is not None, "missing exact gate source")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"], "gate source mismatch")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(REPOSITORY + "/" + WORKFLOW + "@"),
            "wrong workflow definition")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", environment.get(key, "")) is not None, "missing observation run identity")
    require(environment["GITHUB_RUN_ATTEMPT"] == "1",
            "automatic retry is not an authorized metadata observation")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
            "RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME", "ImageOS", "ImageVersion")
    return {"product_source_sha": SOURCE, "original_run_id": RUN,
            "original_run_attempt": ATTEMPT, "original_job_id": JOB,
            "original_artifact_id": ARTIFACT, "original_artifact_sha256": ZIP_SHA,
            "observer_source_sha": environment["GITHUB_SHA"],
            **{key: environment.get(key, "") for key in keys}}


def verify_api(artifact, run, job):
    require(exact_integer(artifact.get("id"), ARTIFACT)
            and artifact.get("name") == "motionwright-macos-candidate"
            and exact_integer(artifact.get("size_in_bytes"), ZIP_BYTES)
            and artifact.get("digest") == "sha256:" + ZIP_SHA
            and artifact.get("expired") is False, "retained artifact identity differs")
    workflow = artifact.get("workflow_run")
    require(type(workflow) is dict
            and exact_integer(workflow.get("id"), RUN)
            and exact_integer(workflow.get("repository_id"), REPOSITORY_ID)
            and exact_integer(workflow.get("head_repository_id"), REPOSITORY_ID)
            and workflow.get("head_sha") == SOURCE
            and workflow.get("head_branch") == "main", "artifact producer identity differs")
    require(exact_integer(run.get("id"), RUN)
            and exact_integer(run.get("run_attempt"), ATTEMPT)
            and run.get("head_sha") == SOURCE and run.get("head_branch") == "main"
            and run.get("event") == "push" and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("path") == ".github/workflows/package-candidate.yml",
            "original package run identity or terminal differs")
    repository = run.get("repository")
    require(type(repository) is dict and exact_integer(repository.get("id"), REPOSITORY_ID)
            and repository.get("full_name") == REPOSITORY, "original run repository differs")
    require(exact_integer(job.get("id"), JOB) and exact_integer(job.get("run_id"), RUN)
            and exact_integer(job.get("run_attempt"), ATTEMPT)
            and job.get("head_sha") == SOURCE and job.get("name") == "macos"
            and job.get("status") == "completed" and job.get("conclusion") == "success",
            "original macOS job identity or terminal differs")


def read_zip(raw, expected_sha=ZIP_SHA, members=None):
    """Read a bounded exact archive without extracting arbitrary member paths."""
    members = MEMBERS if members is None else members
    require(type(raw) is bytes and 0 < len(raw) <= 64 * 1024 * 1024,
            "artifact archive is outside its bound")
    require(hashlib.sha256(raw).hexdigest() == expected_sha, "artifact ZIP digest differs")
    result = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) == len(members)
                and {item.filename for item in infos} == set(members),
                "archive member roster differs or contains duplicates")
        for item in infos:
            name = item.filename
            path = PurePosixPath(name)
            mode = item.external_attr >> 16
            require(not path.is_absolute() and ".." not in path.parts
                    and "\\" not in name and "\0" not in name
                    and not item.is_dir() and not item.flag_bits & 1
                    and stat.S_IFMT(mode) in (0, stat.S_IFREG),
                    "unsafe archive member")
            size, digest = members[name]
            require(type(size) is int and item.file_size == size
                    and 0 < size <= 32 * 1024 * 1024, "archive member size differs")
            value = archive.read(item)
            require(len(value) == size and hashlib.sha256(value).hexdigest() == digest,
                    "archive member bytes differ")
            result[name] = value
    return result


def verify_receipts(candidate, smoke):
    expected = {"application": "Motionwright", "arch": "arm64", "platform": "macos",
                "semwright_revision": SDK, "source_revision": SOURCE, "version": "0.1.0"}
    require(exact_integer(candidate.get("schema_version"), 1)
            and exact_integer(smoke.get("schema_version"), 1)
            and candidate.get("candidate") == expected
            and smoke.get("candidate") == expected, "original candidate source differs")
    size, digest = MEMBERS[DMG_MEMBER]
    entry = {"bundle_kind": "dmg", "bytes": size, "filename": DMG_NAME, "sha256": digest}
    require(candidate.get("artifacts") == [entry]
            and type(candidate["artifacts"][0]["bytes"]) is int, "original DMG receipt differs")
    require(smoke.get("bundle_kind") == "dmg"
            and smoke.get("candidate_archive") == {key: entry[key] for key in ("bytes", "filename", "sha256")}
            and type(smoke["candidate_archive"]["bytes"]) is int, "original smoke archive differs")
    inspection = smoke.get("automated_inspection")
    require(type(inspection) is dict and inspection.get("status") == "PASS"
            and inspection.get("method") == "dmg_readonly_mount_copy"
            and inspection.get("gui_window_probe") == "NOT_RUN"
            and inspection.get("bundle_executable") == "motionwright-desktop"
            and inspection.get("bundle_identifier") == "com.seradotcom.motionwright",
            "original bounded smoke scope differs")
    binary = inspection.get("binary")
    require(type(binary) is dict and exact_integer(binary.get("binary_bytes"), BINARY[0])
            and binary.get("binary_sha256") == BINARY[1]
            and binary.get("binary_filename") == "motionwright-desktop"
            and binary.get("binary_format") == "MACHO64_arm64", "original Mach-O identity differs")
    claims = candidate.get("claims")
    require(type(claims) is dict and claims.get("published") is False
            and claims.get("human_install_acceptance") == "NOT_RUN"
            and claims.get("distribution_signature") == "NOT_CLAIMED"
            and claims.get("notarization") == "NOT_CLAIMED", "candidate claims exceeded original scope")
    claims = smoke.get("claims")
    require(type(claims) is dict and claims.get("release_published") is False
            and claims.get("human_install_acceptance") == "NOT_RUN"
            and claims.get("signing") == "NOT_CLAIMED"
            and claims.get("notarization") == "NOT_CLAIMED", "smoke claims exceeded original scope")


def mounted_device(raw, mount):
    value = plistlib.loads(raw)
    require(type(value) is dict and type(value.get("system-entities")) is list,
            "attach did not retain a system-entity observation")
    selected = [item for item in value["system-entities"]
                if type(item) is dict and item.get("mount-point") == str(mount)]
    require(len(selected) == 1 and type(selected[0].get("dev-entry")) is str
            and re.fullmatch(r"/dev/disk[0-9]+(?:s[0-9]+)*", selected[0]["dev-entry"]) is not None,
            "attach did not identify the uniquely owned mount")
    foreign = [item for item in value["system-entities"]
               if type(item) is dict and "mount-point" in item and item["mount-point"] != str(mount)]
    require(not foreign, "attach reported an additional mount")
    return selected[0]["dev-entry"]


def verify_bundle(app, root):
    require(app.is_dir() and not app.is_symlink(), "missing unique original app")
    plist = app / "Contents/Info.plist"
    before = custody.fingerprint(plist, root, 1024 * 1024)
    value = plistlib.loads(plist.read_bytes())
    require(type(value) is dict and value.get("CFBundleExecutable") == "motionwright-desktop"
            and value.get("CFBundleIdentifier") == "com.seradotcom.motionwright"
            and value.get("CFBundleShortVersionString") == "0.1.0", "original bundle metadata differs")
    require(custody.fingerprint(plist, root, 1024 * 1024) == before, "bundle metadata changed")
    binary = app / "Contents/MacOS/motionwright-desktop"
    identity = custody.fingerprint(binary, root, 64 * 1024 * 1024)
    require((identity["bytes"], identity["sha256"]) == BINARY, "original Mach-O differs")
    return {"plist": before, "binary": identity}



def readonly_mount_identity(mount, device):
    """Bind the owned immutable filesystem; resources require separate admission."""
    require(mount.is_dir() and not mount.is_symlink()
            and os.path.ismount(mount), "original DMG mount is absent or replaced")
    info = mount.lstat()
    block = os.stat(device, follow_symlinks=False)
    require(stat.S_ISBLK(block.st_mode) and block.st_rdev == info.st_dev,
            "mounted filesystem differs from the observed block device")
    readonly = getattr(os, "ST_RDONLY", 0)
    require(readonly and os.statvfs(mount).f_flag & readonly,
            "original DMG filesystem is not read-only")
    return {"path": str(mount), "device": device, "readonly": True,
            "identity": [info.st_dev, info.st_ino, info.st_mode],
            "device_identity": [block.st_dev, block.st_ino, block.st_rdev]}


BUNDLE_ENTRY_LIMIT = 4096
BUNDLE_WALK_LIMIT = 65536
BUNDLE_DEPTH_LIMIT = 64
BUNDLE_BYTES_LIMIT = 128 * 1024 * 1024


def bundle_resource_inventory(app, mount, expected_mount):
    """Bound reachable resource metadata to the observed immutable volume.

    Content authority is the retained DMG; this is not an atomic snapshot.
    """
    app, mount = Path(app).absolute(), Path(mount).absolute()
    require(app.parent == mount, "bundle is outside the original mount")
    device = expected_mount["identity"][0]
    entries, complete, active = {}, set(), set()
    budget = {"lookups": 0, "bytes": 0}

    def identity(info):
        return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns]

    def inspect(path):
        budget["lookups"] += 1
        require(budget["lookups"] <= BUNDLE_WALK_LIMIT, "bundle lookup budget exceeded")
        relative = path.relative_to(mount)
        require(len(relative.parts) <= BUNDLE_DEPTH_LIMIT
                and len(str(relative)) <= 8192, "bundle path budget exceeded")
        info = path.lstat()
        require(info.st_dev == device, "bundle resource crosses a device boundary")
        require(not (getattr(info, "st_file_attributes", 0) & 0x400)
                or stat.S_ISLNK(info.st_mode), "bundle has an unbound reparse resource")
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode), "bundle has a special resource")
        return info

    def target_parts(path, info):
        raw = os.readlink(path)
        require(type(raw) is str and 0 < len(raw) <= 8192 and "\0" not in raw,
                "bundle link target exceeds its bound")
        require(identity(inspect(path)) == identity(info), "bundle link changed")
        target = Path(raw)
        if target.is_absolute():
            require(target.is_relative_to(mount), "bundle link leaves original volume")
            parts = target.relative_to(mount).parts
        else:
            parts = path.parent.relative_to(mount).parts + target.parts
        return raw, list(parts)

    def resolve(path):
        pending = list(path.relative_to(mount).parts)
        current, links = mount, 0
        while pending:
            require(len(pending) <= BUNDLE_DEPTH_LIMIT * 2,
                    "bundle link resolution exceeds its bound")
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                require(current != mount, "bundle link leaves original volume")
                current = current.parent
                continue
            candidate = current / part
            info = inspect(candidate)
            if stat.S_ISLNK(info.st_mode):
                links += 1
                require(links <= 40, "bundle link is cyclic or exceeds its bound")
                _, replacement = target_parts(candidate, info)
                pending = replacement + pending
                current = mount
            else:
                require(not pending or stat.S_ISDIR(info.st_mode),
                        "bundle link traverses a nondirectory")
                current = candidate
        return current

    def visit(path, depth=0):
        require(depth <= BUNDLE_DEPTH_LIMIT, "bundle graph depth exceeded")
        info = inspect(path)
        key = path.relative_to(mount).as_posix()
        directory_id = (info.st_dev, info.st_ino)
        require(not (stat.S_ISDIR(info.st_mode) and directory_id in active),
                "bundle contains a cyclic directory link")
        if key in complete:
            require(entries[key]["identity"] == identity(info), "bundle resource changed")
            return
        require(len(entries) < BUNDLE_ENTRY_LIMIT, "bundle entry budget exceeded")
        row = {"path": key, "identity": identity(info)}
        entries[key] = row
        if stat.S_ISLNK(info.st_mode):
            raw, _ = target_parts(path, info)
            resolved = resolve(path)
            row.update(kind="symlink", target=raw,
                       resolved=resolved.relative_to(mount).as_posix())
            visit(resolved, depth + 1)
        elif stat.S_ISDIR(info.st_mode):
            row["kind"] = "directory"
            active.add(directory_id)
            try:
                names = []
                with os.scandir(path) as children:
                    for child in children:
                        require(len(names) < BUNDLE_ENTRY_LIMIT,
                                "bundle directory entry budget exceeded")
                        names.append(child.name)
                require(identity(inspect(path)) == identity(info),
                        "bundle directory changed during enumeration")
                for name in sorted(names):
                    visit(path / name, depth + 1)
            finally:
                active.remove(directory_id)
        else:
            row["kind"] = "file"
            budget["bytes"] += info.st_size
            require(0 <= info.st_size and budget["bytes"] <= BUNDLE_BYTES_LIMIT,
                    "bundle resource byte budget exceeded")
        require(identity(inspect(path)) == identity(info), "bundle resource changed")
        complete.add(key)

    root = inspect(mount)
    require(identity(root)[:3] == expected_mount["identity"], "original mount identity changed")
    require(stat.S_ISDIR(inspect(app).st_mode), "original app is not an ordinary directory")
    visit(app)
    rows = [entries[key] for key in sorted(entries)]
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    require(len(encoded) <= 4 * 1024 * 1024, "bundle inventory receipt exceeds its bound")
    return {"schema": "motionwright.macos-bundle-resource-boundary/1",
            "device": device, "entry_count": len(rows),
            "regular_bytes": budget["bytes"], "entries": rows,
            "inventory_sha256": hashlib.sha256(encoded).hexdigest(),
            "content_authority": "ORIGINAL_RETAINED_DMG_ON_OBSERVED_READONLY_VOLUME",
            "atomic_snapshot": False}


def observe_mounted_commands(command, app, dmg, mount, device, expected_mount):
    """Observe only the original app; refuse a writable copy or mount change."""
    require(app.parent == mount and not app.is_symlink(),
            "metadata target is outside the original DMG mount")
    original_mount = readonly_mount_identity(mount, device)
    require(original_mount == expected_mount, "original DMG mount changed after attach")
    original_resources = bundle_resource_inventory(app, mount, original_mount)
    original_bundle = verify_bundle(app, mount)

    def bound_command(label, argv, timeout):
        require(readonly_mount_identity(mount, device) == original_mount,
                "original DMG mount changed before metadata command")
        require(bundle_resource_inventory(app, mount, original_mount) == original_resources,
                "original resource boundary changed before metadata command")
        row = command(label, argv, timeout)
        require(readonly_mount_identity(mount, device) == original_mount,
                "original DMG mount changed during metadata command")
        require(bundle_resource_inventory(app, mount, original_mount) == original_resources,
                "original resource boundary changed during metadata command")
        return row

    observations = observe_commands(bound_command, app, dmg)
    require(verify_bundle(app, mount) == original_bundle, "original mounted bundle changed")
    require(bundle_resource_inventory(app, mount, original_mount) == original_resources,
            "original resource boundary changed after metadata commands")
    return {"mount_identity": original_mount, "bundle": original_bundle,
            "bundle_resources": original_resources, "observations": observations}


def observation_commands(app, dmg):
    return (
        ("host", ["/usr/bin/sw_vers"]),
        ("architecture", ["/usr/bin/uname", "-m"]),
        ("xcrun-version", ["/usr/bin/xcrun", "--version"]),
        ("xcode-version", ["/usr/bin/xcodebuild", "-version"]),
        ("security-policy-status", ["/usr/sbin/spctl", "--status"]),
        ("app-signature-display", ["/usr/bin/codesign", "--display", "--verbose=4", str(app)]),
        ("app-signature-verify", ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)]),
        ("app-ticket-validate", ["/usr/bin/xcrun", "stapler", "validate", "-v", str(app)]),
        ("app-policy-assessment", ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)]),
        ("dmg-signature-display", ["/usr/bin/codesign", "--display", "--verbose=4", str(dmg)]),
        ("dmg-ticket-validate", ["/usr/bin/xcrun", "stapler", "validate", "-v", str(dmg)]),
        ("dmg-policy-assessment", ["/usr/sbin/spctl", "--assess", "--type", "open",
                                   "--context", "context:primary-signature", "--verbose=4", str(dmg)]),
    )


def observe_commands(command, app, dmg):
    """Nonzero metadata exits are observations, never an inferred unsigned state."""
    result = {}
    for label, argv in observation_commands(app, dmg):
        row = command(label, argv, 90)
        require(type(row.get("exit_code")) is int, "metadata command has no observed integer exit")
        result[label] = {"exit_code": row["exit_code"],
                         "interpretation": "ORIGINAL_TOOL_OUTPUT_REQUIRES_REVIEW"}
    return result


def write_intent(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def run(gate, output):
    gate, output = Path(gate).absolute(), Path(output).absolute()
    environment = dict(os.environ)
    context = execution_context(environment)
    require(not output.exists() and not output.is_relative_to(gate),
            "observation output must be a fresh directory outside the gate checkout")
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    logs = evidence / "commands"
    logs.mkdir()
    workspace = output / "work"
    workspace.mkdir()
    environment.update(TMPDIR=str(workspace), TMP=str(workspace), TEMP=str(workspace),
                       XDG_CACHE_HOME=str(workspace / "cache"), PYTHONDONTWRITEBYTECODE="1",
                       GH_PROMPT_DISABLED="1")
    mount = workspace / "mount"
    mount.mkdir()
    commands = []
    result = {"schema": "motionwright.retained-macos-metadata-observation/1",
              "context": context, "started_utc": custody.now(),
              "status": "NOT_COMPLETE", "signing": "NOT_CLASSIFIED",
              "notarization": "NOT_CLASSIFIED", "gatekeeper": "NOT_CLASSIFIED",
              "application_launched": False, "package_rebuilt": False,
              "formal_acceptance": False, "release_authorized": False}
    owned_mount = False
    primary_unknown = False

    def command(label, argv, timeout):
        write_intent(logs / (label + ".intent.json"),
                     {"argv": argv, "context": context, "at_utc": custody.now(), "no_retry": True})
        return observer.command(argv, gate, environment, logs, label, timeout, context,
                                (evidence, commands))

    def successful(label, argv, timeout=60):
        row = command(label, argv, timeout)
        require(row["exit_code"] == 0, "prerequisite command failed: " + label)
        return row

    try:
        before = custody.source_snapshot(gate, context["observer_source_sha"], GATE_FILES)
        for name, digest in PINS.items():
            require(before["files"][name]["sha256"] == digest, "process custody dependency changed")
        result["gate_source"] = before
        result["retained_gate_sources"] = observer.retain_source(gate, before, evidence)
        gh = shutil.which("gh")
        require(gh is not None, "GitHub CLI is absent")
        gh = str(Path(gh).resolve(strict=True))
        tools = {gh, "/usr/bin/hdiutil",
                 *(argv[0] for _, argv in observation_commands(Path("app"), Path("dmg")))}
        result["tools"] = {}
        for tool in sorted(tools):
            resolved = Path(tool).resolve(strict=True)
            result["tools"][tool] = {"resolved": str(resolved),
                                    **custody.fingerprint(resolved, Path("/"))}
        api = {}
        paths = {"artifact": f"actions/artifacts/{ARTIFACT}",
                 "run": f"actions/runs/{RUN}/attempts/{ATTEMPT}",
                 "job": f"actions/jobs/{JOB}"}
        for label, suffix in paths.items():
            row = successful("api-" + label, [gh, "api", "repos/" + REPOSITORY + "/" + suffix])
            api[label] = strict_object(observer.original_stream(logs, row, "stdout"))
        verify_api(api["artifact"], api["run"], api["job"])
        archive = successful("artifact-zip", [gh, "api",
            f"repos/{REPOSITORY}/actions/artifacts/{ARTIFACT}/zip"], 120)
        raw = observer.original_stream(logs, archive, "stdout")
        require(len(raw) == ZIP_BYTES, "original ZIP byte count differs")
        members = read_zip(raw)
        verify_receipts(strict_object(members[CANDIDATE_MEMBER]), strict_object(members[SMOKE_MEMBER]))
        originals = evidence / "originals"
        originals.mkdir()
        for name, data in members.items():
            with (originals / PurePosixPath(name).name).open("xb") as stream:
                stream.write(data)
        dmg = originals / DMG_NAME
        original_dmg = custody.fingerprint(dmg, evidence)
        result["original_dmg"] = original_dmg
        attach = successful("attach-readonly", ["/usr/bin/hdiutil", "attach", "-readonly",
                              "-nobrowse", "-mountpoint", str(mount), "-plist", str(dmg)], 120)
        result["mounted_device"] = mounted_device(observer.original_stream(logs, attach, "stdout"), mount)
        owned_mount = True
        apps = [path for path in mount.iterdir() if path.suffix == ".app" and path.is_dir() and not path.is_symlink()]
        require(len(apps) == 1, "mounted artifact does not have one original application")
        app = apps[0]
        result["mount_identity"] = readonly_mount_identity(mount, result["mounted_device"])
        result["bundle_resources"] = bundle_resource_inventory(app, mount, result["mount_identity"])
        located = successful("stapler-location", ["/usr/bin/xcrun", "--find", "stapler"])
        location = observer.original_stream(logs, located, "stdout").decode("utf-8").strip()
        require(0 < len(location) < 8192 and "\n" not in location
                and Path(location).is_absolute(), "stapler tool location is invalid")
        stapler = Path(location).resolve(strict=True)
        result["stapler_tool"] = {"resolved": str(stapler),
                                  **custody.fingerprint(stapler, Path("/"))}
        observed = observe_mounted_commands(command, app, dmg, mount, result["mounted_device"],
                                             result["mount_identity"])
        require(observed["bundle_resources"] == result["bundle_resources"],
                "original resource boundary changed after admission")
        result.update(observed)
        require(custody.fingerprint(stapler, Path("/")) ==
                {key: value for key, value in result["stapler_tool"].items() if key != "resolved"},
                "stapler tool changed during observation")
        for tool in result["tools"].values():
            require(custody.fingerprint(Path(tool["resolved"]), Path("/")) ==
                    {key: value for key, value in tool.items() if key != "resolved"},
                    "observation tool changed")
        require(readonly_mount_identity(mount, result["mounted_device"]) == result["mount_identity"],
                "original mount changed after metadata observation")
        require(verify_bundle(app, mount) == result["bundle"], "observed original bundle changed")
        require(bundle_resource_inventory(app, mount, result["mount_identity"]) == result["bundle_resources"],
                "observed original resource boundary changed")
        require(custody.fingerprint(dmg, evidence) == original_dmg, "observed DMG changed")
        after = custody.source_snapshot(gate, context["observer_source_sha"], GATE_FILES)
        require(after == before, "gate source changed during observation")
        result["source_after"] = after
        result["status"] = "OBSERVATION_COMPLETE_REVIEW_REQUIRED"
    except observer.UnknownExecution as error:
        primary_unknown = True
        result.update(status="UNKNOWN_NO_RETRY", error=str(error),
                      uncertain_command=error.command)
    except Exception as error:
        result.update(status="FAILED_OBSERVATION", error=type(error).__name__ + ": " + str(error))
    finally:
        # Cleanup is limited to the mount whose successful attach was observed.
        # It never retries the uncertain command, repeats the mount, or launches the app.
        if owned_mount:
            try:
                detached = command("detach-owned-mount", ["/usr/bin/hdiutil", "detach", result["mounted_device"]], 60)
                result["detach_exit_code"] = detached["exit_code"]
                if detached["exit_code"] != 0:
                    result["cleanup"] = "OWNED_MOUNT_DETACH_FAILED"
                    if not primary_unknown:
                        result["status"] = "FAILED_OBSERVATION"
                else:
                    result["cleanup"] = "OWNED_MOUNT_DETACHED"
            except observer.UnknownExecution as error:
                result.update(status="UNKNOWN_NO_RETRY", cleanup="OWNED_MOUNT_DETACH_UNKNOWN",
                              cleanup_uncertain_command=error.command)
            except Exception as error:
                result["cleanup"] = "OWNED_MOUNT_DETACH_NOT_CONFIRMED"
                result["cleanup_error"] = type(error).__name__ + ": " + str(error)
                if not primary_unknown:
                    result["status"] = "FAILED_OBSERVATION"
        else:
            result["cleanup"] = "NO_CONFIRMED_OWNED_MOUNT"
        if result["status"] == "OBSERVATION_COMPLETE_REVIEW_REQUIRED":
            try:
                for entry in commands:
                    require(custody.fingerprint(evidence / entry["receipt"]["path"], evidence)
                            == entry["receipt"], "original command receipt changed")
                    for stream in ("stdout", "stderr"):
                        observer.original_stream(evidence / entry["folder"], entry["command"], stream)
            except observer.UnknownExecution as error:
                result.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
            except Exception as error:
                result.update(status="FAILED_OBSERVATION", error=type(error).__name__ + ": " + str(error))
        result["commands"] = commands
        result["finished_utc"] = custody.now()
        custody.write_new(evidence / "observation.json", result)
    return 0 if result["status"] == "OBSERVATION_COMPLETE_REVIEW_REQUIRED" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    return run(args.gate, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
