#!/usr/bin/env python3
"""Three independently admitted Hero profiles using one shared preparation.

The preparation reuses the existing FixedTxt compiler/runtime commands and its
owned child supervisor. It has its own clock, source index, per-profile grants
and private custody. No FixedTxt P/R/Q, R28, historical owner or result is reused
as authority. A failed/unknown profile is never dispatched a second time.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import zipfile

MIB = 1024 * 1024
REPO = "seradotcom/motionwright"
WORKFLOW = ".github/workflows/native-profiles-e2e.yml"
SCOPE = "hero-profiles-three"
ROSTER = ("landscape", "portrait", "square")
SIZES = {"landscape": [1920, 1080], "portrait": [1080, 1920], "square": [1080, 1080]}
LIMITS = {"attempt_seconds": 3900, "preparation_seconds": 1200, "review_seconds": 480,
          "native_seconds_each": 540, "custody_seconds_each": 120,
          "upload_seconds_each": 60,
          "final_seconds": 180,
          "stream_bytes": 8 * MIB, "capsule_bytes": 512 * MIB, "files": 4096,
          "memory_bytes": 1536 * MIB, "memory_high_bytes": 1408 * MIB,
          "memory_max_bytes": 1472 * MIB, "processes": 512, "disk_reserve_bytes": 10 * 1024**3}
OPERATIONS = {"motionwright_render": 1, "motionwright_master": 1,
              "motion_canvas_render_start": 1, "mlt_frames_encode": 1, "mlt_av_mux": 1,
              "mlt_sync_probe": 0, "seed_hero": 1, "bundle_import": 1,
              "reopen_before_native": 1, "reopen_after_native": 1,
              "automatic_retry": 0, "simultaneous_owner_limit": 1}
SDK_BINS = ("semwright", "semwrightd", "semwright-sandbox", "semwright-motion-canvas-driver",
            "semwright-mlt-video-driver", "semwright-mlt-runtime-runner")
SOURCE_PATHS = (
    "SOURCE_LOCK.json", "Cargo.toml", "Cargo.lock", "crates/native/Cargo.toml",
    "crates/native/examples/native-render-e2e.rs",
    "crates/native/examples/native_render_profiles/mod.rs",
    "crates/native/src/production.rs", "tooling/native_profiles_workflow.py",
    "tooling/native_profiles_custody.py", "tooling/native_owner_resources.py", "tooling/native_av_master_e2e.py",
    "tooling/native_av_media_probe.py", "tooling/creative_frame_evidence.py",
    "tooling/native_av_failure_guard.py", "tooling/native_provider_lifecycle.py",
    "tooling/native_provider_loss_correlation.py", "tooling/native_provider_broker_events.py",
    "tooling/native_provider_live_watch.py", "tooling/native_fixedtxt_g2_supervisor.py",
    "tooling/native_fixedtxt_workflow.py", "tooling/native_fixedtxt_g2_admission.py",
    "tooling/native_manim_frozen_runtime.py",
    "tooling/native_fixedtxt_session.py", "tooling/native_mlt_g2_custody.py",
    "tooling/native_mlt_g2_observation.py", "tooling/native_mlt_runtime_admission.py",
    "tooling/native_mlt_evidence_selection.py", "tooling/mlt_official_runtime_intake.py",
    "tooling/ci_private_evidence.py", "tooling/native_evidence_projection.py",
    "tooling/requirements/mlt-g2-custody.txt", WORKFLOW,
)
SHA = re.compile(r"[0-9a-f]{64}\Z")
GIT = re.compile(r"[0-9a-f]{40}\Z")
DECIMAL = re.compile(r"[1-9][0-9]*\Z")
OWNER_ACTOR = 69337222


class ProfileRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise ProfileRejected(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor(raw):
    return {"bytes": len(raw), "sha256": digest(raw)}


def strict(raw, maximum=65536):
    need(type(raw) is bytes and 0 < len(raw) <= maximum, "profiles-json-budget")
    def pairs(rows):
        result = {}
        for key, value in rows:
            need(key not in result, "profiles-json-duplicate")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ProfileRejected("profiles-json-constant")))


def read(path, maximum=65536):
    from ci_private_evidence import regular
    return regular(Path(path), maximum)[0]


def write(path, value):
    from ci_private_evidence import new_bytes
    new_bytes(Path(path), value if type(value) is bytes else canonical(value))


def output(**values):
    need(all(type(value) is str and "\n" not in value and "\r" not in value for value in values.values()),
         "profiles-output-lines")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("".join(key + "=" + value + "\n" for key, value in values.items()))


def context(environment=None):
    env = os.environ if environment is None else environment
    need(sys.platform == "linux" and all(env.get(key) == value for key, value in (
        ("GITHUB_ACTIONS", "true"), ("CI", "true"), ("RUNNER_OS", "Linux"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_REPOSITORY", REPO),
        ("GITHUB_RUN_ATTEMPT", "1"), ("MOTIONWRIGHT_NATIVE_SCOPE", SCOPE))), "profiles-workflow-context")
    lock = strict(Path(env["GITHUB_WORKSPACE"]).joinpath("SOURCE_LOCK.json").read_bytes())
    value = {"source_sha": env.get("GITHUB_SHA"), "semwright_sha": lock["dependencies"]["semwright"]["revision"],
             "run_id": env.get("GITHUB_RUN_ID"), "run_attempt": "1"}
    need(all(type(value[key]) is str and GIT.fullmatch(value[key]) for key in ("source_sha", "semwright_sha"))
         and type(value["run_id"]) is str and DECIMAL.fullmatch(value["run_id"]), "profiles-context-tuple")
    return value


def checked_request(raw, current):
    value = strict(raw, 16384)
    need(type(value) is dict and set(value) == {"schema", "source_sha", "source_tree", "semwright_sha",
        "semwright_tree", "roster", "limits", "operations_per_profile", "runtime_bootstrap_sha256", "recipient", "owner_resources"}
        and value["schema"] == "motionwright.native.hero-profiles-request/1", "profiles-request-shape")
    need(value["source_sha"] == current["source_sha"] and value["semwright_sha"] == current["semwright_sha"]
         and all(type(value[key]) is str and GIT.fullmatch(value[key]) for key in
                 ("source_sha", "source_tree", "semwright_sha", "semwright_tree")), "profiles-source-tuple")
    need(value["roster"] == list(ROSTER) and canonical(value["limits"]) == canonical(LIMITS)
         and canonical(value["operations_per_profile"]) == canonical(OPERATIONS),
         "profiles-closed-roster-operations-and-budgets")
    from native_owner_resources import OWNER_RESOURCE_POLICY
    need(canonical(value["owner_resources"]) == canonical(OWNER_RESOURCE_POLICY),
         "profiles-explicit-owner-resource-policy")
    need(type(value["runtime_bootstrap_sha256"]) is str and SHA.fullmatch(value["runtime_bootstrap_sha256"]),
         "profiles-runtime-bootstrap-pin")
    from ci_private_evidence import public_key
    public_key(value["recipient"])
    return value


def root_path(current):
    base = Path(os.environ["RUNNER_TEMP"])
    need(base.is_absolute() and str(base).startswith("/home/runner/work/_temp")
         and base.resolve(strict=True) == base, "profiles-disposable-runner-root")
    result = base / ("hp" + current["run_id"] + "-1")
    need(result.resolve(strict=True) == result and stat.S_IMODE(result.stat().st_mode) == 0o700
         and result.stat().st_uid == os.geteuid(), "profiles-private-session-root")
    return result


def case_path(root, profile):
    need(profile in ROSTER, "profiles-exact-case")
    path = root / profile
    need(len(str(path / "owner/runtime/broker.sock").encode()) <= 107, "profiles-AF-UNIX-bound")
    return path


def load_originals(current):
    root = root_path(current)
    raw = read(root / "request.original.json", 16384)
    request = checked_request(raw, current)
    clock_raw = read(root / "clock.original.json")
    clock = strict(clock_raw)
    need(set(clock) == {"schema", "context", "request", "boot_id", "started_monotonic", "deadline_monotonic"}
         and clock["schema"] == "motionwright.native.hero-profiles-clock/1"
         and clock["context"] == current and clock["request"] == descriptor(raw)
         and clock["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
         "profiles-original-clock-binding")
    start, end = clock["started_monotonic"], clock["deadline_monotonic"]
    need(all(type(n) in (float, int) and math.isfinite(n) for n in (start, end))
         and 0 <= start <= time.monotonic() < end and end - start == LIMITS["attempt_seconds"],
         "profiles-original-clock-expired-or-changed")
    bootstrap_raw = read(root / "runtime-bootstrap.original.json", 16384)
    need(digest(bootstrap_raw) == request["runtime_bootstrap_sha256"], "profiles-runtime-bootstrap-changed")
    import native_mlt_g2_custody as runtime
    bootstrap = runtime.validate_dispatch(bootstrap_raw, current, scenario=runtime.FILM_SCOPE)
    need(bootstrap["recipient"] == request["recipient"], "profiles-one-private-recipient")
    return root, raw, request, clock_raw, clock, bootstrap_raw, bootstrap


def git(root, *args):
    environment = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_GRAFT_FILE": os.devnull, "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.check_output(["/usr/bin/git", "--no-replace-objects", "-C", str(root),
        "-c", "core.fsmonitor=false", *args], env=environment, timeout=20)


def source_state(current, request):
    root, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
    files = {}
    for checkout, sha, tree in ((root, current["source_sha"], request["source_tree"]),
                               (sdk, current["semwright_sha"], request["semwright_tree"])):
        need(checkout.resolve(strict=True) == checkout
             and git(checkout, "rev-parse", "HEAD").decode().strip() == sha
             and git(checkout, "rev-parse", "HEAD^{tree}").decode().strip() == tree
             and not git(checkout, "diff", "--name-only", "HEAD", "--"), "profiles-physical-source-tree")
    for name in SOURCE_PATHS:
        raw = git(root, "show", current["source_sha"] + ":" + name)
        need(read(root / name, 8 * MIB) == raw, "profiles-physical-source-blob")
        files[name] = descriptor(raw)
    return {"context": current, "source_tree": request["source_tree"],
            "semwright_tree": request["semwright_tree"], "files": files}


def clean_environment(current, *, preparation=False):
    # Credentials are added only to the two original runtime-intake API stages.
    allowed = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "CARGO_HOME", "RUSTUP_HOME",
               "LD_LIBRARY_PATH", "PKG_CONFIG_PATH") if preparation else ("PATH", "LANG", "LC_ALL")
    value = {key: os.environ[key] for key in allowed if key in os.environ}
    need(value.get("PATH") and not any(key.startswith(("GIT_", "GH_", "ACTIONS_")) for key in value),
         "profiles-private-child-environment")
    value.update(CI="true", GITHUB_ACTIONS="true", RUNNER_OS="Linux", GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REPOSITORY=REPO, GITHUB_RUN_ATTEMPT="1", GITHUB_SHA=current["source_sha"],
        GITHUB_RUN_ID=current["run_id"], GITHUB_WORKSPACE=os.environ["GITHUB_WORKSPACE"],
        RUNNER_TEMP=os.environ["RUNNER_TEMP"], SEMWRIGHT_CHECKOUT=os.environ["SEMWRIGHT_CHECKOUT"],
        MOTIONWRIGHT_NATIVE_SCOPE=SCOPE, GITHUB_JOB="hero-profiles", PYTHONDONTWRITEBYTECODE="1", PLAYWRIGHT_BROWSERS_PATH="0",
        MW_NATIVE_RESOURCE_ORIGIN=os.environ["MW_NATIVE_RESOURCE_ORIGIN"], MW_NATIVE_RESOURCE_SCOPE="hero",
        MW_MLT_SCOPE="film180-g2", MOTIONWRIGHT_APPROVED_MLT_G2="1",
        CARGO_BUILD_JOBS="1", CARGO_INCREMENTAL="0", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
        RAYON_NUM_THREADS="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    return value


class PreparationClock:
    def __init__(self, clock):
        self.started, self.total = clock["started_monotonic"], clock["deadline_monotonic"]
        self.until = self.started + LIMITS["preparation_seconds"]
        self.used = set()

    def reserve(self, phase):
        need(re.fullmatch(r"prepare-[0-9]{2}", phase) and phase not in self.used,
             "profiles-preparation-once")
        self.used.add(phase)
        # A failed installation still receives its sole privileged handoff;
        # it cannot consume any subsequent Native budget or restart setup.
        deadline = self.total - LIMITS["final_seconds"] if phase == "prepare-11" else self.until
        need(time.monotonic() < deadline, "profiles-preparation-original-deadline")
        return deadline


def preparation_commands(current, bootstrap):
    """Same provider/build/runtime recipe as FixedTxt; Hero source has no overlay.

    Stages 01..07 reuse the package/compiler/runtime commands. Stages 08..12
    reuse the Film runtime-only admission. The two FixedTxt-specific examples
    are not required by that admission and are not built. Stage 00 binds Hero
    sources; stages 13/14 build only Hero and run its real Store controls.
    """
    repo, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
    scripts, python = repo / "tooling", sys.executable
    base = Path(os.environ["RUNNER_TEMP"])
    control = base / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
    installed = base / ("mw-mlt-g2-installation-" + current["run_id"] + "-1")
    common = ["PATH=/usr/sbin:/usr/bin:/sbin:/bin", "CI=true", "GITHUB_ACTIONS=true", "RUNNER_OS=Linux",
        "RUNNER_TEMP=" + str(base), "GITHUB_WORKSPACE=" + str(repo), "GITHUB_SHA=" + current["source_sha"],
        "GITHUB_RUN_ID=" + current["run_id"], "GITHUB_RUN_ATTEMPT=1", "GITHUB_EVENT_NAME=workflow_dispatch",
        "MW_MLT_SCOPE=film180-g2", "MOTIONWRIGHT_APPROVED_MLT_G2=1", "PYTHONDONTWRITEBYTECODE=1"]
    return [
        [python, "-B", "-E", "-s", str(scripts / "native_profiles_workflow.py"), "source-inputs"],
        ["sudo", "-n", "apt-get", "update"],
        ["sudo", "-n", "apt-get", "install", "--no-install-recommends", "-y", "bubblewrap",
         "apparmor-profiles", "apparmor-utils", "libmlt7=7.22.0-1build6", "melt=7.22.0-1build6",
         "ffmpeg", "libpipewire-0.3-dev", "pkg-config", "clang"],
        ["bash", str(sdk / "scripts/dev/ci-driver-bwrap-profile.sh"),
         str(root_path(current) / "sandbox-profile.original.txt")],
        ["npm", "ci", "--ignore-scripts"],
        ["npx", "--no-install", "playwright", "install", "--with-deps", "firefox"],
        ["cargo", "build", "--manifest-path", str(sdk / "Cargo.toml"), "--locked", "-p", "semwright-daemon",
         "-p", "semwright-cli", "--bins"],
        ["cargo", "build", "--manifest-path", str(sdk / "Cargo.toml"), "--locked", "-p", "semwright-plugin-host",
         "--bin", "semwright-sandbox", "-p", "semwright-driver-motion-canvas", "--bin", "semwright-motion-canvas-driver",
         "-p", "semwright-mlt-video-driver", "--bin", "semwright-mlt-video-driver", "-p", "semwright-mlt-video-driver",
         "--bin", "semwright-mlt-runtime-runner"],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "prepare"],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "guard"],
        ["sudo", "-n", "env", "-i", *common, python, "-B", "-E", "-s",
         str(scripts / "native_mlt_runtime_admission.py"), "--original-source", str(control / "intake/original_c_source"),
         "--repaired-source", str(control / "intake/repaired_c_source"), "--compiled-module", str(control / "intake/compiled_module"),
         "--compiled-module-sha256", "4e9b6d9ec6efe97807ba9c53bfdca8299e60e5582c40eca355f866a058ed7eac",
         "--compiler-receipt", str(control / "intake/compiler_receipt_path"), "--compiler-receipt-sha256",
         "e16d8d73cdb780cd8fb26bc2b43c2a27db0d8972050c508c9d6432e67b5018a8", "--installed-original-sha256",
         bootstrap["official_runtime"]["known_original_module_sha256"], "--admit", "--new-runner-private-evidence", str(installed)],
        ["sudo", "-n", "env", "-i", *common, python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"),
         "handoff", "--uid", str(os.getuid()), "--gid", str(os.getgid())],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "inputs"],
        ["cargo", "build", "--manifest-path", str(repo / "Cargo.toml"), "--locked", "-p", "motionwright-native",
         "--example", "native-render-e2e"],
        ["cargo", "test", "--manifest-path", str(repo / "Cargo.toml"), "--locked", "-p", "motionwright-native",
         "--example", "native-render-e2e", "profile_store_tests", "--", "--test-threads=1"],
    ]


def stage_complete(value):
    return (value.get("original_complete") is True and value.get("exit_code") == 0
            and value.get("containment_empty") is True and value.get("native_outcome") == "NOT_INFERRED"
            and value.get("overflow") is False and value.get("deadline_exceeded") is False
            and "descendants_survived_wrapper" not in value)


def group(current, phase, *, initially_empty=True):
    import native_owner_resources as resources
    need(phase in ("preparation", "work", "custody"), "profiles-closed-resource-leaf")
    value = resources.OwnedLeaf("hero", current, phase, initially_empty=initially_empty)
    try:
        for name, expected in (("memory.max", LIMITS["memory_max_bytes"]),
                               ("memory.high", LIMITS["memory_high_bytes"]), ("pids.max", LIMITS["processes"])):
            need((value.path / name).read_text().strip() == str(expected), "profiles-kernel-resource-bound")
        return value
    except BaseException:
        value.close()
        raise


def shared_identity(current, request, readonly):
    import native_fixedtxt_session as shared
    shared.verify_mounts(readonly)
    source = source_state(current, request)
    repo, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
    binaries = [sdk / "target/debug" / name for name in SDK_BINS]
    binaries += [repo / "target/debug/examples/native-render-e2e"]
    rows = []
    for path in binaries:
        identity = shared.hash_file(path)
        identity["links"] = path.stat().st_nlink
        with path.open("rb") as stream:
            header = stream.read(20)
        need(header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(header[18:20], "little") == 62,
             "profiles-current-x86-64-ELF")
        rows.append(identity)
    for name in ("node", "ffmpeg", "ffprobe", "melt"):
        value = shutil.which(name)
        need(value is not None, "profiles-runtime-executable-absent")
        physical = Path(value).resolve(strict=True)
        rows.append({**shared.hash_file(physical), "links": physical.stat().st_nlink})
    physical_python = Path(sys.executable).resolve(strict=True)
    rows.append({**shared.hash_file(physical_python), "links": physical_python.stat().st_nlink})
    runtime = sdk / "integrations/motion-canvas/runtime"
    files = {name: {**shared.hash_file(runtime / name, 4 * MIB), "links": (runtime / name).stat().st_nlink}
             for name in ("package.json", "package-lock.json", "render.mjs")}
    shared.verify_mounts(readonly)
    return {"schema": "motionwright.native.hero-shared-identity/1", "context": current,
        "source": source, "binaries": rows, "runtime_sources": files,
        "readonly": descriptor(canonical(readonly)), "preparation_reused_for": list(ROSTER),
        "fixedtxt_authority_inherited": False, "native_calls": 0}


def verify_shared(root, current, index):
    """Reuse verified immutable mounts and file identities, without recompiling."""
    import native_fixedtxt_session as shared
    raw = read(root / "shared-identity.original.json", 512 * 1024)
    readonly_raw = read(root / "shared-readonly.original.json")
    need(descriptor(raw) == index["shared_identity"] and descriptor(readonly_raw) == index["shared_readonly"],
         "profiles-shared-original-binding")
    identity, readonly = strict(raw, 512 * 1024), strict(readonly_raw)
    need(identity["context"] == current and readonly["context"] == current,
         "profiles-shared-current-context")
    shared.verify_mounts(readonly)
    for row in identity["binaries"] + list(identity["runtime_sources"].values()):
        path = Path(row["path"])
        now = path.lstat()
        need(path.resolve(strict=True) == path and stat.S_ISREG(now.st_mode) and now.st_nlink == row["links"]
             and (now.st_dev, now.st_ino, now.st_size, stat.S_IMODE(now.st_mode))
                 == (row["device"], row["inode"], row["bytes"], row["mode"]), "profiles-shared-file-replaced")
        # Tools outside the two immutable mounts are physically rehashed.
        if not any(path.is_relative_to(Path(target["path"])) for target in readonly["targets"]):
            need({**shared.hash_file(path), "links": now.st_nlink} == row, "profiles-external-runtime-tool-changed")
    shared.verify_mounts(readonly)
    return {"shared_identity": descriptor(raw), "shared_readonly": descriptor(readonly_raw),
            "immutable_mounts_reobserved": True, "outside_tools_rehashed": True}


def freeze_shared(current, clock):
    import native_fixedtxt_session as shared
    import native_fixedtxt_g2_supervisor as owned
    root = root_path(current)
    directory = root / "freeze"
    directory.mkdir(mode=0o700, exist_ok=False)
    repo, python_root = Path(os.environ["GITHUB_WORKSPACE"]), Path(sys.executable).parent.parent
    need(python_root.parent == Path(os.environ["RUNNER_TEMP"])
         and python_root.name == "mw-hero-custody-python-" + current["run_id"] + "-1"
         and not python_root.is_relative_to(repo) and not repo.is_relative_to(python_root), "profiles-shared-roots")
    timeline, containment, results = PreparationClock(clock), group(current, "preparation"), []
    try:
        for index, path in enumerate((repo, python_root)):
            need(path.resolve(strict=True) == path and not any(p.is_symlink() for p in (path, *path.parents)),
                 "profiles-freeze-canonical-root")
            need(not shared.mount_rows(Path("/proc/self/mountinfo").read_bytes(), str(path)),
                 "profiles-freeze-preexisting-mount")
            for offset, argv in enumerate((["sudo", "-n", "mount", "--bind", str(path), str(path)],
                ["sudo", "-n", "mount", "-o", "remount,bind,ro", str(path)])):
                result = owned._run_original(argv, environment={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
                    directory=directory, phase="prepare-%02d" % (15 + 2 * index + offset), clock=timeline,
                    containment=containment, stream_limit=65536)
                need(stage_complete(result), "profiles-freeze-incomplete-no-retry")
            identity = path.stat()
            mounts = shared.mount_rows(Path("/proc/self/mountinfo").read_bytes(), str(path))
            need(len(mounts) == 1 and "ro" in mounts[0]["options"] and "rw" not in mounts[0]["options"],
                 "profiles-readonly-not-observed")
            results.append({"path": str(path), "identity": [identity.st_dev, identity.st_ino], "mounts": mounts})
    finally:
        containment.close()
    # Literal schema of the reusable mount checker; it is a physical mount
    # observation, never a FixedTxt template, P/R/Q or execution admission.
    result = {"schema": "motionwright.native.fixedtxt-shared-readonly/1", "context": current,
              "targets": results, "observed_monotonic": time.monotonic()}
    shared.verify_mounts(result)
    write(root / "shared-readonly.original.json", result)
    return result


def prepare():
    import native_fixedtxt_g2_supervisor as owned
    current = context()
    root, request_raw, request, clock_raw, clock, bootstrap_raw, bootstrap = load_originals(current)
    need(shutil.disk_usage(root).free >= LIMITS["disk_reserve_bytes"], "profiles-initial-disk-reserve")
    source = source_state(current, request)
    write(root / "source-before.original.json", source)
    commands = preparation_commands(current, bootstrap)
    directory = root / "preparation"
    directory.mkdir(mode=0o700, exist_ok=False)
    write(root / "preparation-intent.original.json", {"context": current, "commands": commands,
        "original_fixedtxt_recipe_stages": [*range(1, 8), *range(10, 15)], "hero_source_stage": 0,
        "additional_profile_stages": [13, 14], "unneeded_examples_not_built": ["fixed_text_compile_v4", "native-film180-e2e"], "limits": LIMITS, "automatic_retry": False,
        "new_native_authority": False, "native_calls": 0})
    timeline, containment = PreparationClock(clock), group(current, "preparation")
    primary = None
    previous = Path.cwd()
    try:
        for number, argv in enumerate(commands):
            environment = clean_environment(current, preparation=True)
            environment.update(MW_MLT_SCOPE="film180-g2", MOTIONWRIGHT_APPROVED_MLT_G2="1",
                               MW_MLT_G2_ADMISSION=bootstrap_raw.decode())
            if number == 8:
                need(os.environ.get("GH_TOKEN"), "profiles-runtime-intake-read-token")
                environment["GH_TOKEN"] = os.environ["GH_TOKEN"]
            cwd = (Path(environment["SEMWRIGHT_CHECKOUT"]) / "integrations/motion-canvas/runtime"
                   if number in (4, 5) else Path(environment["SEMWRIGHT_CHECKOUT"]) if number in (6, 7)
                   else Path(environment["GITHUB_WORKSPACE"]))
            write(directory / ("prepare-%02d.cwd.json" % number), {"cwd": str(cwd)})
            try:
                os.chdir(cwd)
                result = owned._run_original(argv, environment=environment, directory=directory,
                    phase="prepare-%02d" % number, clock=timeline, containment=containment,
                    stream_limit=LIMITS["stream_bytes"])
                need(stage_complete(result), "profiles-preparation-original-incomplete")
            except BaseException as error:
                if number == 10:
                    primary = error
                elif primary is not None:
                    primary.add_note("Original runtime handoff also failed; no retry.")
                    raise primary from error
                else:
                    raise
            finally:
                os.chdir(previous)
            if number == 11 and primary is not None:
                raise primary
    finally:
        containment.close()
    stdout = read(directory / "prepare-14.stdout", LIMITS["stream_bytes"]).decode("utf-8", "strict")
    need(re.search(r"^test result: ok\. 3 passed; 0 failed; 0 ignored;", stdout, re.M)
         and "running 3 tests" in stdout, "profiles-real-Store-controls-not-three-passing")
    runtime = verify_runtime(current)
    write(root / "runtime-observed.original.json", runtime)
    readonly = freeze_shared(current, clock)
    identity = shared_identity(current, request, readonly)
    write(root / "shared-identity.original.json", identity)
    need(source == identity["source"], "profiles-source-changed-during-preparation")
    runner = capture_runner_identity(root, current, clock["deadline_monotonic"])
    import native_owner_resources as resources
    prefix = resources.resource_originals("hero", "prepare")
    resource_prefix = resources.validate_resource_originals(prefix, scope="hero", current=current, action="prepare",
        helper_sha256=source["files"]["tooling/native_owner_resources.py"]["sha256"])
    write(root / "resource-prefix.original.json", resource_prefix)
    cases = []
    for profile in ROSTER:
        case = case_path(root, profile)
        case.mkdir(mode=0o700, exist_ok=False)
        (case / "control").mkdir(mode=0o700, exist_ok=False)
        cases.append({"profile": profile, "namespace": str(case), "width": SIZES[profile][0],
            "height": SIZES[profile][1], "frames": 180, "frame_rate": [30, 1], "operations": OPERATIONS})
    index = {"schema": "motionwright.native.hero-preparation-index/1", "context": current,
        "request": descriptor(request_raw), "clock": descriptor(clock_raw), "limits": LIMITS,
        "shared_identity": descriptor(canonical(identity)), "shared_readonly": descriptor(canonical(readonly)),
        "runtime_bootstrap": descriptor(bootstrap_raw), "source": source, "cases": cases,
        "preparation_complete": True, "native_calls": 0, "source_gate_or_native_grant": False,
        "fixedtxt_authority_inherited": False, "created_monotonic": time.monotonic()}
    index["runner"] = runner
    index["runtime_observation"] = descriptor(canonical(runtime))
    index["resource_prefix"] = descriptor(canonical(resource_prefix))
    write(root / "preparation-index.original.json", index)
    from native_profiles_custody import seal_preparation
    public = seal_preparation(root, current, request)
    output(directory=str(root / "prepared-sealed"), index_sha256=digest(canonical(index)),
           artifact_name="motionwright-hero-preparation-" + current["run_id"] + "-1")
    need(public["selected_originals_complete"], "profiles-preparation-custody-incomplete")
    return public


def checked_run(value, current, *, run_id=None, completed=False):
    selected = current["run_id"] if run_id is None else run_id
    need(type(value) is dict and value.get("id") == int(selected) and value.get("run_attempt") == 1
         and value.get("head_sha") == current["source_sha"] and value.get("event") == "workflow_dispatch"
         and value.get("path") == WORKFLOW and value.get("repository", {}).get("full_name") == REPO
         and value.get("head_repository", {}).get("full_name") == REPO
         and value.get("actor", {}).get("id") == OWNER_ACTOR
         and value.get("triggering_actor", {}).get("id") == OWNER_ACTOR, "profiles-original-run-API-binding")
    if completed:
        need(value.get("status") == "completed" and value.get("conclusion") == "success",
             "profiles-permission-delivery-run-not-successful")
    else:
        need(value.get("status") == "in_progress" and value.get("conclusion") is None,
             "profiles-owner-run-no-longer-active")
    return value


def capture_runner_identity(root, current, deadline):
    from native_fixedtxt_workflow import ReadOnlyApi
    directory = root / "runner-api"
    directory.mkdir(mode=0o700, exist_ok=False)
    api = ReadOnlyApi(directory, min(deadline, time.monotonic() + 90))
    try:
        run_raw = api.get("repos/" + REPO + "/actions/runs/" + current["run_id"])
        jobs_raw = api.get("repos/" + REPO + "/actions/runs/" + current["run_id"] + "/attempts/1/jobs?per_page=10")
    finally:
        api.close()
    run, jobs = checked_run(strict(run_raw), current), strict(jobs_raw)
    rows = [row for row in jobs.get("jobs", []) if row.get("name") == "hero-profiles"]
    need(type(jobs.get("total_count")) is int and jobs["total_count"] == len(jobs["jobs"]) <= 10
         and len(rows) == 1, "profiles-current-job-roster")
    row = rows[0]
    need(row.get("run_id") == int(current["run_id"]) and row.get("head_sha") == current["source_sha"]
         and row.get("run_attempt") == 1 and row.get("status") == "in_progress"
         and row.get("conclusion") is None and type(row.get("id")) is int and row["id"] > 0
         and type(row.get("runner_id")) is int and row["runner_id"] > 0
         and type(row.get("runner_name")) is str and row["runner_name"], "profiles-current-live-runner")
    write(directory / "run.original.json", run_raw)
    write(directory / "jobs.original.json", jobs_raw)
    return {"job_id": row["id"], "runner_id": row["runner_id"], "runner_name": row["runner_name"],
            "workflow_id": run["workflow_id"], "run_api": descriptor(run_raw), "jobs_api": descriptor(jobs_raw)}


def checked_index(index_raw, *, current, request_raw, clock_raw):
    index, request = strict(index_raw, 512 * 1024), checked_request(request_raw, current)
    need(type(index) is dict and set(index) == {"schema", "context", "request", "clock", "limits",
        "shared_identity", "shared_readonly", "runtime_bootstrap", "runtime_observation", "source", "cases", "runner", "resource_prefix",
        "preparation_complete", "native_calls", "source_gate_or_native_grant", "fixedtxt_authority_inherited",
        "created_monotonic"} and index["schema"] == "motionwright.native.hero-preparation-index/1",
        "profiles-index-shape")
    need(index["context"] == current and index["request"] == descriptor(request_raw)
         and index["clock"] == descriptor(clock_raw) and canonical(index["limits"]) == canonical(LIMITS)
         and index["preparation_complete"] is True and type(index["native_calls"]) is int and index["native_calls"] == 0
         and index["source_gate_or_native_grant"] is False and index["fixedtxt_authority_inherited"] is False,
         "profiles-index-original-binding")
    need(type(index["resource_prefix"]) is dict and set(index["resource_prefix"]) == {"bytes", "sha256"}
         and type(index["resource_prefix"]["bytes"]) is int and 0 < index["resource_prefix"]["bytes"] <= 65536
         and type(index["resource_prefix"]["sha256"]) is str and SHA.fullmatch(index["resource_prefix"]["sha256"]),
         "profiles-index-real-resource-prefix")
    need(type(index["cases"]) is list and len(index["cases"]) == 3, "profiles-index-three-only")
    roots = set()
    for profile, row in zip(ROSTER, index["cases"], strict=True):
        path = PurePosixPath(row.get("namespace", ""))
        need(set(row) == {"profile", "namespace", "width", "height", "frames", "frame_rate", "operations"}
             and row["profile"] == profile and canonical([row["width"], row["height"]]) == canonical(SIZES[profile])
             and type(row["frames"]) is int and row["frames"] == 180
             and canonical(row["frame_rate"]) == canonical([30, 1])
             and canonical(row["operations"]) == canonical(OPERATIONS)
             and path.is_absolute() and str(path) == row["namespace"]
             and path.name == profile and path.parent.name == "hp" + current["run_id"] + "-1"
             and re.fullmatch(r"/home/runner/work/_temp(?:/[A-Za-z0-9_-]+)?", str(path.parent.parent))
             and not {".", ".."} & set(path.parts)
             and len(str(path / "owner/runtime/broker.sock").encode()) <= 107,
             "profiles-index-case-namespace-or-operations")
        roots.add(row["namespace"])
    need(len(roots) == 3 and type(index["runner"]) is dict and all(type(index["runner"].get(key)) is int
         and index["runner"][key] > 0 for key in ("job_id", "runner_id", "workflow_id")), "profiles-index-runner")
    return index


def delivery_name(run_id, index_sha256):
    need(type(run_id) is str and DECIMAL.fullmatch(run_id)
         and type(index_sha256) is str and SHA.fullmatch(index_sha256), "profiles-delivery-name-input")
    return "motionwright-hero-permissions-" + run_id + "-1-" + index_sha256


def checked_permissions(raw, index_raw, *, current, request_raw, clock_raw, prepared_artifact):
    index = checked_index(index_raw, current=current, request_raw=request_raw, clock_raw=clock_raw)
    value = strict(raw, 48 * 1024)
    need(set(value) == {"schema", "context", "preparation_index", "preparation_artifact", "owner_actor_id", "grants"}
         and value["schema"] == "motionwright.native.hero-profile-owner-admissions/1"
         and value["context"] == current and value["preparation_index"] == descriptor(index_raw)
         and value["preparation_artifact"] == prepared_artifact and value["owner_actor_id"] == OWNER_ACTOR,
         "profiles-permission-original-context")
    need(type(value["grants"]) is list and len(value["grants"]) == 3, "profiles-three-separate-grants")
    ids = set()
    for row, grant in zip(index["cases"], value["grants"], strict=True):
        need(set(grant) == {"profile", "admission_id", "namespace", "runner", "source_index_sha256",
            "request_sha256", "shared_identity_sha256", "operations", "limits", "width", "height", "frames",
            "owner_authorized", "release_authorized", "G2_claimed"}
            and grant["profile"] == row["profile"] and grant["namespace"] == row["namespace"]
            and grant["runner"] == index["runner"] and grant["source_index_sha256"] == digest(index_raw)
            and grant["request_sha256"] == digest(request_raw)
            and grant["shared_identity_sha256"] == index["shared_identity"]["sha256"]
            and canonical(grant["operations"]) == canonical(OPERATIONS) and canonical(grant["limits"]) == canonical(LIMITS)
            and canonical([grant["width"], grant["height"]]) == canonical(SIZES[row["profile"]])
            and type(grant["frames"]) is int and grant["frames"] == 180
            and grant["owner_authorized"] is True and grant["release_authorized"] is False and grant["G2_claimed"] is False
            and type(grant["admission_id"]) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", grant["admission_id"]),
            "profiles-independent-owner-permission")
        need(grant["admission_id"] not in ids, "profiles-permission-reuse")
        ids.add(grant["admission_id"])
    return value


def artifact_identity(item):
    return {key: item.get(key) for key in ("id", "name", "size_in_bytes", "digest", "expired", "workflow_run")}


def loaded_permission(root, current, request_raw, clock_raw, index_raw, profile):
    """Read the once-consumed original grant; it never performs another GET."""
    need(profile in ROSTER, "profiles-permission-exact-profile")
    folder = root / "permissions"
    consumed = strict(read(folder / "consumed.original.json"))
    raw = read(folder / "owner-permissions.original.json", 48 * 1024)
    need(type(consumed) is dict and set(consumed) == {"context", "preparation_index", "prepared_artifact",
        "permissions", "delivery_run", "artifact", "native_calls", "automatic_retry", "owner_actor_id",
        "verified_monotonic"} and consumed["context"] == current
        and consumed["preparation_index"] == descriptor(index_raw) and consumed["permissions"] == descriptor(raw)
        and type(consumed["native_calls"]) is int and consumed["native_calls"] == 0
        and consumed["automatic_retry"] is False and consumed["owner_actor_id"] == OWNER_ACTOR,
        "profiles-consumed-permission-original")
    prepared = consumed["prepared_artifact"]
    need(type(prepared) is dict and set(prepared) == {"artifact_id", "artifact_sha256"}
         and type(prepared["artifact_id"]) is int and prepared["artifact_id"] > 0
         and type(prepared["artifact_sha256"]) is str and SHA.fullmatch(prepared["artifact_sha256"]),
         "profiles-consumed-preparation-artifact")
    value = checked_permissions(raw, index_raw, current=current, request_raw=request_raw,
                                clock_raw=clock_raw, prepared_artifact=prepared)
    delivery = strict(read(folder / "delivery-run.original.json"))
    item = strict(read(folder / "delivery-artifact.original.json"))
    need(type(consumed["delivery_run"]) is int and consumed["delivery_run"] > 0
         and consumed["delivery_run"] != int(current["run_id"]), "profiles-consumed-independent-courier")
    checked_run(delivery, current, run_id=str(consumed["delivery_run"]), completed=True)
    checked_artifact(item, run=delivery, name=delivery_name(current["run_id"], digest(index_raw)), maximum=65536)
    index = checked_index(index_raw, current=current, request_raw=request_raw, clock_raw=clock_raw)
    need(artifact_identity(item) == consumed["artifact"]
         and delivery["workflow_id"] == index["runner"]["workflow_id"], "profiles-consumed-delivery-binding")
    # The retained ZIP is small; checking its one member binds the permission
    # bytes to the API digest before any separate profile starts.
    need(permission_zip(read(folder / "permissions.original.zip"), item) == raw,
         "profiles-consumed-ZIP-original-binding")
    return value["grants"][ROSTER.index(profile)]


def checked_artifact(item, *, run, name, maximum):
    need(type(item) is dict and type(item.get("id")) is int and item["id"] > 0 and item.get("name") == name
         and type(item.get("size_in_bytes")) is int and 0 < item["size_in_bytes"] <= maximum
         and item.get("expired") is False and type(item.get("digest")) is str
         and re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"])
         and item.get("workflow_run", {}).get("id") == run["id"]
         and item.get("workflow_run", {}).get("head_sha") == run["head_sha"], "profiles-original-artifact-API")
    return item


def permission_zip(raw, item):
    need(len(raw) == item["size_in_bytes"] and "sha256:" + digest(raw) == item["digest"],
         "profiles-permission-ZIP-identity")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        need(len(infos) == 1 and infos[0].filename == "owner-permissions.original.json"
             and not infos[0].is_dir() and not infos[0].flag_bits & 1
             and infos[0].file_size <= 48 * 1024 and infos[0].compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
             and stat.S_IFMT(infos[0].external_attr >> 16) in (0, stat.S_IFREG), "profiles-permission-ZIP-roster")
        original = archive.read(infos[0])
    need(len(original) == infos[0].file_size, "profiles-permission-ZIP-member-size")
    return original


def receive_permissions():
    from native_fixedtxt_workflow import ReadOnlyApi
    current = context()
    root, request_raw, request, clock_raw, clock, _, _ = load_originals(current)
    index_raw = read(root / "preparation-index.original.json", 512 * 1024)
    index = checked_index(index_raw, current=current, request_raw=request_raw, clock_raw=clock_raw)
    prepared = {"artifact_id": int(os.environ["MW_PROFILE_PREPARATION_ARTIFACT_ID"]),
                "artifact_sha256": os.environ["MW_PROFILE_PREPARATION_ARTIFACT_SHA256"]}
    need(prepared["artifact_id"] > 0 and SHA.fullmatch(prepared["artifact_sha256"]), "profiles-preparation-upload")
    until = min(index["created_monotonic"] + LIMITS["review_seconds"], clock["deadline_monotonic"]
                - 3 * (LIMITS["native_seconds_each"] + LIMITS["custody_seconds_each"]) - LIMITS["final_seconds"])
    need(time.monotonic() < until, "profiles-review-preserves-three-Native-reserves")
    directory = root / "permissions"
    directory.mkdir(mode=0o700, exist_ok=False)
    api = ReadOnlyApi(directory, until)
    name = delivery_name(current["run_id"], digest(index_raw))
    target = None
    try:
        target = checked_run(strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"])), current)
        upload = strict(api.get("repos/" + REPO + "/actions/artifacts/" + str(prepared["artifact_id"])))
        checked_artifact(upload, run=target, name="motionwright-hero-preparation-" + current["run_id"] + "-1",
                         maximum=256 * MIB)
        need(upload["digest"] == "sha256:" + prepared["artifact_sha256"], "profiles-preparation-upload-readback")
        write(directory / "preparation-artifact.original.json", upload)
        route = "repos/" + REPO + "/actions/artifacts?name=" + name + "&per_page=2&page=1"
        while True:
            listing = strict(api.get(route))
            need(type(listing.get("total_count")) is int and listing["total_count"] <= 1
                 and listing["total_count"] == len(listing.get("artifacts", [])), "profiles-unique-permission-delivery")
            if listing["artifacts"]:
                item = listing["artifacts"][0]
                break
            need(time.monotonic() + 5 < until, "profiles-permission-window-expired")
            time.sleep(5)
        write(directory / "consume.intent.json", {"context": current, "artifact": artifact_identity(item),
            "automatic_retry": False, "native_calls": 0})
        delivery_id = item.get("workflow_run", {}).get("id")
        need(type(delivery_id) is int and delivery_id > 0 and delivery_id != int(current["run_id"]),
             "profiles-permission-independent-delivery-run")
        while True:
            delivery = strict(api.get("repos/" + REPO + "/actions/runs/" + str(delivery_id)))
            if delivery.get("status") == "completed":
                checked_run(delivery, current, run_id=str(delivery_id), completed=True)
                break
            checked_run(delivery, current, run_id=str(delivery_id))
            need(time.monotonic() + 3 < until, "profiles-permission-delivery-close-deadline")
            time.sleep(3)
        need(delivery["workflow_id"] == target["workflow_id"] == index["runner"]["workflow_id"],
             "profiles-permission-same-reviewed-workflow")
        checked_artifact(item, run=delivery, name=name, maximum=65536)
        latest = strict(api.get("repos/" + REPO + "/actions/artifacts/" + str(item["id"])))
        need(artifact_identity(latest) == artifact_identity(item), "profiles-permission-artifact-changed")
        archive = api.get("repos/" + REPO + "/actions/artifacts/" + str(item["id"]) + "/zip")
        write(directory / "permissions.original.zip", archive)
        raw = permission_zip(archive, item)
        permissions = checked_permissions(raw, index_raw, current=current, request_raw=request_raw,
                                          clock_raw=clock_raw, prepared_artifact=prepared)
        final_listing = strict(api.get(route))
        need(final_listing.get("total_count") == 1 and len(final_listing.get("artifacts", [])) == 1
             and artifact_identity(final_listing["artifacts"][0]) == artifact_identity(item),
             "profiles-permission-late-ambiguity")
        checked_run(strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"])), current)
        need(time.monotonic() < until, "profiles-permission-consumed-Native-reserve")
        write(directory / "owner-permissions.original.json", raw)
        write(directory / "delivery-run.original.json", delivery)
        write(directory / "delivery-artifact.original.json", item)
        write(directory / "consumed.original.json", {"context": current, "preparation_index": descriptor(index_raw),
            "prepared_artifact": prepared, "permissions": descriptor(raw), "delivery_run": delivery_id,
            "artifact": artifact_identity(item), "native_calls": 0, "automatic_retry": False,
            "owner_actor_id": OWNER_ACTOR, "verified_monotonic": time.monotonic()})
        return permissions
    finally:
        api.close()


def verify_runtime(current):
    import native_mlt_g2_custody as runtime
    import native_mlt_g2_observation as observed
    base = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
    raw = read(base / "records/g2-inputs.json")
    value = strict(raw)
    need(value["schema"] == runtime.FILM_INPUT_SCHEMA, "profiles-original-runtime-only-schema")
    closure, alias = observed.verify_runtime_inputs({key: value[key] for key in runtime.RUNTIME_INPUT_KEYS},
        source_sha=current["source_sha"], semwright_sha=current["semwright_sha"])
    return {"input": descriptor(raw), "source_compiler": closure, "installed_melt_alias": alias,
            "known_melt_sha256": value["known_melt_sha256"],
            "compiled_module_sha256": value["compiled_module_sha256"], "native_calls": 0}


@contextmanager
def original_mlt_observer(daemon_pid, output_root, evidence):
    """The same passive actual-process witness as the admitted Film180 route."""
    import native_mlt_g2_observation as observed
    current = context()
    runtime = verify_runtime(current)
    directory = evidence / "profile-original-live-MLT"
    directory.mkdir(mode=0o700, exist_ok=False)
    write(directory / "runtime-before.original.json", runtime)
    watcher, primary, stopped = None, None, False
    errors = []

    def finish():
        nonlocal stopped
        if stopped or watcher is None:
            return
        stopped = True
        for label, operation in (("stop", watcher.stop), ("retain", lambda: watcher.retain_intermediate(directory))):
            try:
                operation()
            except BaseException as error:
                errors.append(type(error).__name__)
                write(directory / (label + ".negative.json"), {"error_type": type(error).__name__, "automatic_retry": False})
        diagnostic = watcher.diagnostic()
        raw = canonical(diagnostic)
        need(len(raw) <= 3 * MIB, "profiles-live-MLT-diagnostic-bound")
        write(directory / "observer-diagnostic.original.json", raw)
        need(not errors and diagnostic["bounded_journal_complete"] is True,
             "profiles-live-MLT-original-incomplete")

    def confirm(result):
        finish()
        artifact = result.get("artifact", {})
        relative = artifact.get("path", "")
        parsed = PurePosixPath(relative)
        need(type(relative) is str and parsed.as_posix() == relative and not parsed.is_absolute()
             and not {".", ".."} & set(parsed.parts) and relative.endswith(".mp4"), "profiles-live-MLT-output")
        path = output_root / relative
        from native_fixedtxt_session import hash_file
        actual = hash_file(path, 256 * MIB)
        proof = watcher.require_one(path)
        need(artifact.get("sha256") == actual["sha256"]
             and proof.get("published_output_sha256") == actual["sha256"]
             and proof.get("published_output_bytes") == actual["bytes"]
             and runtime["compiled_module_sha256"] == observed.REPAIRED_MODULE_SHA256,
             "profiles-live-MLT-native-result-binding")
        write(evidence / "profile-actual-MLT-module.original.json", {
            "schema": "motionwright.native.hero-profile-live-MLT/1", "context": current,
            "master": {"bytes": actual["bytes"], "sha256": actual["sha256"]},
            "actual_mapped_module_sha256": observed.REPAIRED_MODULE_SHA256, "live_process_proof": proof,
            "native_effect_started_by_witness": False, "automatic_retry": False})

    try:
        watcher = observed.LiveObserver(daemon_pid, output_root, runtime["known_melt_sha256"])
        watcher.start()
        yield confirm
    except BaseException as error:
        primary = error
    finally:
        try:
            finish()
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                primary.add_note("Original profile MLT witness custody also failed; no effect was repeated.")
        if watcher is not None:
            try:
                watcher.close()
            except BaseException as error:
                if primary is None:
                    primary = error
        if primary is not None:
            raise primary


def kernel_observation(containment):
    import native_owner_resources as resources
    parent = resources.leaf_observation(containment.path.parent)
    raw = {name: (containment.path / name).read_text().strip()
           for name in ("memory.max", "memory.high", "memory.peak", "memory.events", "pids.max", "pids.current", "cgroup.events")}
    need(raw["memory.max"] == str(LIMITS["memory_max_bytes"])
         and raw["memory.high"] == str(LIMITS["memory_high_bytes"])
         and raw["pids.max"] == str(LIMITS["processes"]), "profiles-kernel-limits-changed")
    events = {key: int(value) for key, value in (line.split() for line in raw["memory.events"].splitlines())}
    return {"path": str(containment.path), "device": containment.identity.st_dev,
            "inode": containment.identity.st_ino, "originals": raw, "events": events,
            "peak_bytes": int(raw["memory.peak"]), "empty": not containment.populated(), "aggregate_parent": parent}


def kernel_complete(before, after):
    return (after["empty"] is True and after["peak_bytes"] <= LIMITS["memory_bytes"]
            and after["aggregate_parent"]["peak_bytes"] <= LIMITS["memory_bytes"]
            and all(after["aggregate_parent"]["events"].get(key, 0) == before["aggregate_parent"]["events"].get(key, 0)
                    for key in ("oom", "oom_kill", "oom_group_kill"))
            and all(after["events"].get(key, 0) == before["events"].get(key, 0)
                    for key in ("oom", "oom_kill", "oom_group_kill")))


def native_child(profile):
    """Only this entrypoint opens a new Hero Store and dispatches its producers."""
    current = context()
    root, request_raw, request, clock_raw, clock, _, _ = load_originals(current)
    index_raw = read(root / "preparation-index.original.json", 512 * 1024)
    index = checked_index(index_raw, current=current, request_raw=request_raw, clock_raw=clock_raw)
    grant = loaded_permission(root, current, request_raw, clock_raw, index_raw, profile)
    case = case_path(root, profile)
    intent = strict(read(case / "control/owner.started.original.json"))
    need(intent["grant"] == grant and intent["context"] == current and intent["profile"] == profile
         and intent["automatic_retry"] is False and intent["index"] == descriptor(index_raw),
         "profiles-original-case-start-binding")
    need(Path("/proc/self/cgroup").read_text().splitlines()
         == ["0::/motionwright-hero-" + current["run_id"] + "-1/work"], "profiles-Native-owned-cohort")
    need(time.monotonic() < intent["native_deadline_monotonic"]
         and intent["native_deadline_monotonic"] < intent["local_custody_deadline_monotonic"]
             < intent["deferred_upload"]["latest_completion_monotonic"] <= clock["deadline_monotonic"],
         "profiles-Native-original-clock")
    need(os.environ.get("MOTIONWRIGHT_E2E_FIXTURE") == profile
         and not any(key.startswith(("GH_", "ACTIONS_")) or "TOKEN" in key or "SECRET" in key
                     for key in os.environ), "profiles-no-Native-transport-credentials")
    verify_shared(root, current, index)
    runtime = verify_runtime(current)
    write(case / "control/runtime-before.original.json", runtime)
    # env and module import happen only after the independent grant is consumed.
    import native_av_master_e2e as producer
    need(producer.FIXTURE == profile and producer.IS_HERO and producer.EXPECTED_FRAMES == 180
         and list(producer.EXPECTED_SIZE) == SIZES[profile], "profiles-exact-physical-producer")
    producer.main(profile_evidence=case / "evidence", profile_owner=case / "owner")


def case_proof(case, profile, current):
    evidence = case / "evidence"
    result = strict(read(evidence / "result.json", 4 * MIB), 4 * MIB)
    decoded = strict(read(evidence / "decoded-media-proof.json", 4 * MIB), 4 * MIB)
    need(result.get("native_av_master_e2e") == "PASS" and result.get("fixture") == profile
         and result.get("motionwright_sha") == current["source_sha"]
         and result.get("semwright_sha") == current["semwright_sha"] and result.get("frame_count") == 180
         and result.get("decoded_video_frames") == 180
         and decoded.get("master_sha256") == result.get("master_sha256")
         and decoded.get("source_wav_sha256") == result.get("audio_sha256"), "profiles-physical-native-result")
    reopen = [strict(read(evidence / name)) for name in
        ("profile-before-native-reopen.json", "profile-after-native-reopen.json")]
    for row in reopen:
        need(row.get("schema") == "motionwright.native.hero-profile-reopen/1" and row.get("profile") == profile
             and [row.get("width"), row.get("height")] == SIZES[profile] and row.get("frames") == 180
             and all(row.get(key) is True for key in ("source_project_equal", "imported_project_equal",
                 "source_events_equal", "imported_events_equal", "generation_rotated", "stores_closed_before_receipt"))
             and row.get("new_apply") is False and row.get("native_calls") == 0, "profiles-two-real-Store-reopens")
    need(reopen[0] == reopen[1], "profiles-Store-state-changed-through-Native")
    loaded = strict(read(evidence / "profile-actual-MLT-module.original.json", 4 * MIB), 4 * MIB)
    need(loaded.get("context") == current and loaded.get("master", {}).get("sha256") == result["master_sha256"]
         and loaded.get("native_effect_started_by_witness") is False, "profiles-actual-MLT-proof")
    from native_profiles_custody import file_digest
    root = case / "owner/output"
    relative = result.get("artifact_directory", "")
    artifact = PurePosixPath(relative)
    need(type(relative) is str and relative and artifact.as_posix() == relative and not artifact.is_absolute()
         and not {".", ".."} & set(artifact.parts), "profiles-native-artifact-directory")
    manifest_raw = read(root / relative / "artifact-manifest.json", 8 * MIB)
    need(manifest_raw == read(evidence / "artifact-manifest.json", 8 * MIB)
         and digest(manifest_raw) == result["artifact_manifest_sha256"], "profiles-native-manifest-identity")
    manifest = strict(manifest_raw, 8 * MIB)
    plan, frames = manifest.get("plan", {}), manifest.get("frames")
    need(type(frames) is list and len(frames) == 180
         and (plan.get("first_frame"), plan.get("end_frame_exclusive"), plan.get("frame_count")) == (0, 180, 180),
         "profiles-all-180-native-PNG-required")
    expected = {"owner/output/" + relative + "/artifact-manifest.json": descriptor(manifest_raw)}
    names = []
    for ordinal, frame in enumerate(frames):
        name = "frames/%06d.png" % ordinal
        need(frame.get("file") == name and frame.get("index") == ordinal and type(frame.get("bytes")) is int
             and 8 < frame["bytes"] <= 16 * MIB and type(frame.get("sha256")) is str and SHA.fullmatch(frame["sha256"]),
             "profiles-original-frame-record")
        physical = root / relative / name
        # The custodian hashes each original once while retaining it, then
        # joins these expected rows to the actual TAR member readback.
        actual = {"bytes": frame["bytes"], "sha256": frame["sha256"]}
        need(physical.parent.resolve(strict=True) == physical.parent, "profiles-frame-parent-alias")
        fd = os.open(physical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == frame["bytes"],
                 "profiles-frame-physical-type-and-size")
            header = os.read(fd, 33)
            after = os.fstat(fd)
            named = physical.lstat()
            identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns)
            need(identity(before) == identity(after) == identity(named), "profiles-frame-header-read-changed")
        finally:
            os.close(fd)
        import struct
        need(len(header) == 33 and header[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
             and list(struct.unpack(">II", header[16:24])) == SIZES[profile], "profiles-original-frame-dimensions")
        expected["owner/output/" + relative + "/" + name] = actual
        names.append(Path(name).name)
    directory = root / relative / "frames"
    need(sorted(path.name for path in directory.iterdir()) == names, "profiles-extra-or-missing-original-frame")
    for relative_name, expected_sha, maximum in (
        ("evidence/motionwright-master.mp4", result["master_sha256"], 256 * MIB),
        ("owner/output/acceptance-audio.wav", result["audio_sha256"], 8 * MIB)):
        actual = file_digest(case / relative_name, maximum)
        need(actual["sha256"] == expected_sha, "profiles-original-master-or-WAV-changed")
        expected[relative_name] = actual
    for relative_name in ("owner/motionwright-data/motionwright.sqlite3", "owner/motionwright-import/motionwright.sqlite3",
        "evidence/hero.motionwright", "evidence/editable-project.json", "evidence/imported-project.original.json",
        "evidence/source-backup.original.json", "evidence/imported-backup.original.json",
        "evidence/source-before-native-backup.original.json", "evidence/imported-before-native-backup.original.json",
        "evidence/source-after-native-backup.original.json", "evidence/imported-after-native-backup.original.json",
        "evidence/bundle-import-check.json", "evidence/profile-before-native-reopen.json",
        "evidence/profile-after-native-reopen.json", "evidence/decoded-media-proof.json"):
        expected[relative_name] = file_digest(case / relative_name, 64 * MIB)
    return {"profile": profile, "source_sha": current["source_sha"], "semwright_sha": current["semwright_sha"],
        "size": SIZES[profile], "frames": 180, "master_sha256": result["master_sha256"],
        "wav_sha256": result["audio_sha256"], "reopen": descriptor(canonical(reopen[0])),
        "required_originals": expected, "decoded_pcm_frames": decoded["decoded_pcm_frames"],
        "source_pcm_frames": 288000, "audio_scope": "actual_distinct_stereo_transport_tones",
        "physical_execution_completed": True, "independent_review_pending": True,
        "G2_claimed": False, "release_authorized": False}


def run_cases():
    import native_fixedtxt_g2_supervisor as owned
    current = context()
    root, request_raw, request, clock_raw, clock, _, _ = load_originals(current)
    index_raw = read(root / "preparation-index.original.json", 512 * 1024)
    index = checked_index(index_raw, current=current, request_raw=request_raw, clock_raw=clock_raw)
    receive_permissions()
    for ordinal, profile in enumerate(ROSTER):
        case = case_path(root, profile)
        control = case / "control"
        grant = loaded_permission(root, current, request_raw, clock_raw, index_raw, profile)
        if ordinal:
            previous = strict(read(case_path(root, ROSTER[ordinal - 1]) / "control/closed.original.json"))
            need(previous["mechanics_complete"] is True and previous["physical_execution_completed"] is True
                 and previous["context"] == current, "profiles-previous-owner-not-complete-no-continuation")
        need(not (case / "owner").exists() and not (case / "evidence").exists(), "profiles-new-owner-only")
        shared = verify_shared(root, current, index)
        need(shutil.disk_usage(case).free >= LIMITS["disk_reserve_bytes"], "profiles-original-disk-reserve")
        started = time.monotonic()
        total = LIMITS["native_seconds_each"] + LIMITS["custody_seconds_each"]
        need(started + (3 - ordinal) * total + ordinal * LIMITS["upload_seconds_each"]
             + LIMITS["final_seconds"] <= clock["deadline_monotonic"],
             "profiles-remaining-original-reserves")
        timeline = owned.AttemptClock({"attempt_seconds": total, "custody_seconds": LIMITS["custody_seconds_each"]},
            started=started, upload_seconds=LIMITS["upload_seconds_each"],
            ceiling=min(started + total, clock["deadline_monotonic"]))
        write(control / "owner.started.original.json", {"context": current, "profile": profile, "grant": grant,
            "index": descriptor(index_raw), "shared_before": shared, "started_monotonic": started,
            "native_deadline_monotonic": timeline.work, "local_custody_deadline_monotonic": timeline.custody_end,
            "deferred_upload": {"seconds": LIMITS["upload_seconds_each"],
                "schedule": "AFTER_SERIAL_PROFILE_EXECUTION",
                "latest_completion_monotonic": clock["deadline_monotonic"] - LIMITS["final_seconds"]
                    - (2 - ordinal) * LIMITS["upload_seconds_each"],
                "within_local_processing_window_claimed": False, "actions_step_timeout_seconds": 60},
            "automatic_retry": False, "prior_owners_reused": False})
        environment = clean_environment(current)
        environment["MOTIONWRIGHT_E2E_FIXTURE"] = profile
        executable = str(Path(os.environ["GITHUB_WORKSPACE"]) / "tooling/native_profiles_workflow.py")
        native = [sys.executable, "-B", "-E", "-s", executable, "run-profile", profile]
        custody = [sys.executable, "-B", "-E", "-s", executable, "seal-profile", profile]
        groups, primary, work, sealed = [], None, None, None
        try:
            for phase in ("work", "custody"):
                groups.append(group(current, phase))
            before = kernel_observation(groups[0])
            write(control / "kernel-before.original.json", before)
            try:
                work = owned._run_original(native, environment=environment, directory=control, phase="work",
                    clock=timeline, containment=groups[0], stream_limit=LIMITS["stream_bytes"])
            except BaseException as error:
                primary = error
            after = kernel_observation(groups[0])
            write(control / "kernel-after.original.json", after)
            try:
                write(control / "shared-after.original.json", verify_shared(root, current, index))
                write(control / "runtime-after.original.json", verify_runtime(current))
            except BaseException as error:
                if primary is None:
                    primary = error
            write(control / "native-observation.original.json", {"context": current, "profile": profile,
                "work_terminal_complete": work is not None and stage_complete(work),
                "kernel_complete": kernel_complete(before, after), "cohort_empty": after["empty"],
                "error_type": None if primary is None else type(primary).__name__,
                "automatic_retry": False, "G2_claimed": False})
            custody_before = None
            try:
                custody_before = kernel_observation(groups[1])
                sealed = owned._run_original(custody, environment=environment, directory=control, phase="custody",
                    clock=timeline, containment=groups[1], stream_limit=1024 * 1024)
            except BaseException as error:
                if primary is None:
                    primary = error
            # The final wrapper terminal is retained in the session capsule;
            # it cannot appear inside the capsule whose write it observes.
            custody_after = kernel_observation(groups[1])
            write(control / "custody-kernel.original.json", {"before": custody_before, "after": custody_after})
            complete = (primary is None and work is not None and sealed is not None
                        and stage_complete(work) and stage_complete(sealed) and kernel_complete(before, after))
            complete = complete and custody_before is not None and kernel_complete(custody_before, custody_after)
            proof = None
            if complete:
                try:
                    public = strict(read(case / "sealed/public.json"))
                    proof_raw = read(control / "physical-proof.original.json", 256 * 1024)
                    need(public.get("selected_originals_complete") is True
                         and public.get("claims", {}).get("physical_proof") == descriptor(proof_raw),
                         "profiles-original-physical-proof-custody")
                    proof = strict(proof_raw, 256 * 1024)
                    need(proof.get("profile") == profile and proof.get("source_sha") == current["source_sha"]
                         and proof.get("semwright_sha") == current["semwright_sha"]
                         and proof.get("physical_execution_completed") is True,
                         "profiles-original-physical-proof-context")
                except BaseException as error:
                    primary, complete = error, False
            final = {"context": current, "profile": profile, "mechanics_complete": complete,
                "physical_execution_completed": proof is not None, "proof": proof,
                "native_outcome": "COMPLETED_PENDING_INDEPENDENT_REVIEW" if complete else "UNKNOWN_NO_RETRY",
                "automatic_retry": False, "G2_claimed": False, "release_authorized": False,
                "publication_state": "PENDING_DEFERRED_ACTIONS_UPLOAD",
                "publication_within_local_processing_window_claimed": False,
                "error_type": None if primary is None else type(primary).__name__}
            write(control / "closed.original.json", final)
            if (case / "sealed/public.json").is_file():
                output(**{profile + "_directory": str(case / "sealed")})
            need(complete, "profiles-original-case-incomplete-no-retry")
        finally:
            for containment in groups:
                containment.close()


def release_shared():
    import native_fixedtxt_session as shared
    import native_fixedtxt_g2_supervisor as owned
    current = context()
    root, _, _, _, clock, _, _ = load_originals(current)
    if not (root / "shared-readonly.original.json").is_file():
        return
    receipt = strict(read(root / "shared-readonly.original.json"))
    shared.verify_mounts(receipt)
    for phase in ("preparation", "work", "custody"):
        containment = group(current, phase)
        try:
            need(not containment.populated(), "profiles-release-live-cohort")
        finally:
            containment.close()
    directory = root / "release"
    directory.mkdir(mode=0o700, exist_ok=False)
    class ReleaseClock:
        started = clock["started_monotonic"]
        total = min(clock["deadline_monotonic"] - 120, time.monotonic() + 30)
        used = set()
        def reserve(self, phase):
            need(phase in ("release-0", "release-1") and phase not in self.used
                 and time.monotonic() < self.total, "profiles-release-original-clock")
            self.used.add(phase)
            return self.total
    timeline, containment = ReleaseClock(), group(current, "preparation")
    try:
        for ordinal, row in enumerate(reversed(receipt["targets"])):
            result = owned._run_original(["sudo", "-n", "umount", "--", row["path"]],
                environment={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}, directory=directory,
                phase="release-" + str(ordinal), clock=timeline, containment=containment, stream_limit=65536)
            need(stage_complete(result), "profiles-release-not-closed-no-retry")
            need(not shared.mount_rows(Path("/proc/self/mountinfo").read_bytes(), row["path"]),
                 "profiles-release-mount-remains")
        write(root / "released.original.json", {"context": current, "owned_mounts_released": True,
            "prior_owner_cleanup_inferred": False, "automatic_retry": False})
    finally:
        containment.close()


def deliver_permissions():
    # A courier only: the owner independently checks the complete index and all
    # three grants, then authenticates this workflow run through read-only API.
    current = context()
    raw = os.environ.get("MW_PROFILE_PERMISSIONS", "").encode()
    value = strict(raw, 48 * 1024)
    target = value.get("context", {})
    need(value.get("schema") == "motionwright.native.hero-profile-owner-admissions/1"
         and target.get("source_sha") == current["source_sha"]
         and target.get("semwright_sha") == current["semwright_sha"] and target.get("run_attempt") == "1"
         and type(target.get("run_id")) is str and DECIMAL.fullmatch(target["run_id"])
         and target["run_id"] != current["run_id"] and value.get("owner_actor_id") == OWNER_ACTOR
         and type(value.get("grants")) is list and [row.get("profile") for row in value["grants"]] == list(ROSTER),
         "profiles-courier-three-original-permissions")
    index_sha = value.get("preparation_index", {}).get("sha256")
    name = delivery_name(target["run_id"], index_sha)
    directory = Path(os.environ["RUNNER_TEMP"]) / ("hero-permissions-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    from native_owner_resources import new
    new(directory / "owner-permissions.original.json", raw)
    output(directory=str(directory), artifact_name=name)


def record_uploads():
    from native_fixedtxt_workflow import ReadOnlyApi
    current = context()
    root, _, _, _, clock, _, _ = load_originals(current)
    directory = root / "upload-readback"
    directory.mkdir(mode=0o700, exist_ok=False)
    api = ReadOnlyApi(directory, min(clock["deadline_monotonic"] - 150, time.monotonic() + 30))
    rows = []
    try:
        run = checked_run(strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"])), current)
        for profile in ROSTER:
            artifact_id = os.environ.get("MW_PROFILE_" + profile.upper() + "_ARTIFACT_ID", "")
            expected = os.environ.get("MW_PROFILE_" + profile.upper() + "_ARTIFACT_SHA256", "")
            if not artifact_id and not expected:
                rows.append({"profile": profile, "artifact_uploaded": False})
                continue
            need(DECIMAL.fullmatch(artifact_id) and SHA.fullmatch(expected), "profiles-upload-output-pair")
            item_raw = api.get("repos/" + REPO + "/actions/artifacts/" + artifact_id)
            item = strict(item_raw)
            checked_artifact(item, run=run, name="motionwright-hero-originals-" + current["run_id"] + "-1-" + profile,
                             maximum=LIMITS["capsule_bytes"] + 17 * MIB)
            need(item["digest"] == "sha256:" + expected, "profiles-upload-original-digest")
            write(directory / (profile + ".artifact.original.json"), item_raw)
            public_raw = read(case_path(root, profile) / "sealed/public.json")
            public = strict(public_raw)
            from native_profiles_custody import file_digest
            need(file_digest(case_path(root, profile) / "sealed/sealed-originals.bin", LIMITS["capsule_bytes"] + 17 * MIB)
                 == public["ciphertext"], "profiles-uploaded-ciphertext-original-changed")
            rows.append({"profile": profile, "artifact_uploaded": True, "artifact": artifact_identity(item),
                         "public": descriptor(public_raw), "ciphertext": public["ciphertext"],
                         "publication_within_local_processing_window_claimed": False,
                         "deferred_actions_step_timeout_seconds": LIMITS["upload_seconds_each"]})
        write(root / "uploads.original.json", {"context": current, "profiles": rows,
            "all_three_artifacts_observed": all(row["artifact_uploaded"] for row in rows),
            "native_result_inferred_from_upload": False, "G2_claimed": False, "automatic_retry": False})
    finally:
        api.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["prepare"]:
        prepare()
    elif args == ["receive-and-run"]:
        run_cases()
    elif args == ["source-inputs"]:
        current = context()
        root, _, request, _, _, _, _ = load_originals(current)
        need(Path("/proc/self/cgroup").read_text().splitlines()
             == ["0::/motionwright-hero-" + current["run_id"] + "-1/preparation"],
             "profiles-source-stage-original-cohort")
        write(root / "hero-source-inputs.original.json", source_state(current, request))
    elif args == ["release-shared"]:
        release_shared()
    elif args == ["deliver"]:
        deliver_permissions()
    elif args == ["record-uploads"]:
        record_uploads()
    elif args == ["seal-session"]:
        from native_profiles_custody import seal_session
        current = context()
        root, _, request, _, _, _, _ = load_originals(current)
        seal_session(root, current, request)
        output(directory=str(root / "session-sealed"))
    elif len(args) == 2 and args[0] in ("run-profile", "seal-profile") and args[1] in ROSTER:
        if args[0] == "run-profile":
            native_child(args[1])
        else:
            from native_profiles_custody import seal_case
            current = context()
            root, _, request, _, _, _, _ = load_originals(current)
            seal_case(root, args[1], current, request)
    else:
        raise ProfileRejected("profiles-closed-command")


if __name__ == "__main__":
    import native_fixedtxt_g2_supervisor as owned
    previous = {kind: signal.signal(kind, owned._cancel) for kind in (signal.SIGTERM, signal.SIGINT)}
    try:
        main()
    except Exception as error:
        # Closed class only; original private streams are kept by the owner.
        code = (error.args[0] if type(error) is ProfileRejected and len(error.args) == 1
                and type(error.args[0]) is str and re.fullmatch(r"profiles-[A-Za-z0-9-]{1,100}", error.args[0])
                else "unclassified")
        print("hero-profile-stage=INCOMPLETE_NO_RETRY error_type=" + type(error).__name__ + " code=" + code,
              file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
