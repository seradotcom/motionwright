#!/usr/bin/env python3
"""Four frozen Linux Host/Bootstrap/Root stdin-custody tests, never a replay.

The existing Foundation observer retains process and stream observations. This
gate selects sources, producers, fixtures and exact cases; it does not claim
child consumption, native rendering, installed application or human acceptance.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys

try:
    from tooling import ci_foundation_causal as observer
    from tooling import ci_storage_apply_recovery as custody
    from tooling import ci_sdk_native_source as sdk_gate
except ModuleNotFoundError as error:
    if error.name != "tooling":
        raise
    import ci_foundation_causal as observer
    import ci_storage_apply_recovery as custody
    import ci_sdk_native_source as sdk_gate

WORKFLOW = ".github/workflows/sdk-daemon-stdin-custody.yml"
HELPER = "tooling/ci_sdk_daemon_stdin_g2.py"
TEST = "tooling/tests/test_ci_sdk_daemon_stdin_g2.py"
PREPARER = "tooling/fixtures/sdk-daemon-stdin/prepare_linux_fixture.py"
SDK_SHA = "da05d8e4a02fd5acc70d3c37f108b02bca761481"
SDK_TREE = "6afba9b4a3d6d6cc31e2b51f8391ff576b1f20d8"
SDK_PARENT = "fbcd4f6f5ba3c614cd14d9765226d0e2e2a862dc"
SCOPE = "linux-real-host-g2"
SOURCE_SCOPE = "source-controls"
SCHEMA = "motionwright.sdk-daemon-stdin-g2/1"
RUST = "1.98.1"
CASE_PREFIX = "host_stdin_custody::bridge_tests::"
CASES = tuple(CASE_PREFIX + name for name in (
    "linux_real_host_nonempty_capture_drains_to_bound_original_and_terminal",
    "linux_real_host_metadata_collision_preserves_partial_bytes_and_native_job",
    "linux_genuine_capture_stop_during_consumer_terminal_wait_stays_incomplete",
    "linux_genuine_capture_late_reserved_permit_is_counted_not_false_eof",
))
GATE_PINS = {
    "tooling/ci_foundation_causal.py": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "tooling/ci_sdk_native_source.py": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
    PREPARER: "9fed06ab15f3ac1dd8c98cbbc3c42087cb5afec9e081adb2e5a3614beccb4567",
}
GATE_FILES = (WORKFLOW, HELPER, TEST, *GATE_PINS, "tooling/ci_test_runner.py")
SDK_PINS = {
    "Cargo.lock": "69a2746b64e96d4fd39f3aaa900692b7ac55b54032303785f816f92cf2802a7e",
    "crates/daemon/src/host_stdin_custody.rs": "6de074258791796c44244ef2fea7cb7149afb6cc1d0435c1bc885fa46ca94037",
    "crates/driver-host/src/bin/fixture.rs": "9411cf69142b555fe70d01a492bd822cc0a1f2b830e5df1f33d9550e35b1ac0b",
    "crates/daemon/src/host_stdin_custody_bridge_tests.rs": "06d3559bb5c25049b84863cc2d5a22424394d35a9353f50433b7e7ec39b4b8c5",
    "crates/platform-linux-sys/src/sandbox_main.rs": "065f2dd6632f205ec6315b6adda5987df09d2e00b4e11137e72768ee369b8d18",
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
require = custody.require
FIXTURE_VARIABLES = ("SEMWRIGHT_TEST_STDIN_CUSTODY_CONFIG", "SEMWRIGHT_TEST_SANDBOX_HELPER")


class CapabilityUnavailable(custody.VerificationError):
    """An unavailable prerequisite is NOT_RUN, never an omitted PASS."""


def admit_request(environment):
    event = environment.get("GITHUB_EVENT_NAME")
    scope = environment.get("MW_DAEMON_SCOPE")
    require(event in {"push", "workflow_dispatch"} and scope in {SOURCE_SCOPE, SCOPE},
            "unsupported event or selected scope")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"):
        require(SHA.fullmatch(environment.get(key, "")), "full workflow source SHA required")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"], "workflow source differs")
    if scope == SCOPE:
        require(event == "workflow_dispatch", "real cases are never automatic")
        require(environment.get("MW_DAEMON_GATE_SHA") == environment["GITHUB_SHA"],
                "reviewed gate source differs")
        require(environment.get("MW_DAEMON_SDK_SHA") == SDK_SHA, "unreviewed SDK source")
        require(environment.get("GITHUB_RUN_ATTEMPT") == "1", "only first attempt; no UNKNOWN replay")
    return {"event": event, "scope": scope, "gate_source_sha": environment["GITHUB_SHA"],
            "sdk_source_sha": SDK_SHA if scope == SCOPE else None}


def execution_context(environment, runtime_platform=None):
    admit_request(environment)
    runtime_platform = sys.platform if runtime_platform is None else runtime_platform
    require(runtime_platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "selected Linux x64 runner required")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("MW_DAEMON_SCOPE") == SCOPE, "explicit real G2 dispatch required")
    for key in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "MW_DAEMON_GATE_SHA"):
        require(SHA.fullmatch(environment.get(key, "")), "full gate source SHA required")
    require(environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
            == environment["MW_DAEMON_GATE_SHA"], "gate, workflow and reviewed source differ")
    require(environment.get("MW_DAEMON_SDK_SHA") == SDK_SHA, "unreviewed SDK source")
    require(environment.get("GITHUB_RUN_ATTEMPT") == "1", "only first attempt; no UNKNOWN replay")
    require(re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")), "run ID required")
    require(environment.get("GITHUB_JOB") == "linux-real-host-g2", "unexpected logical job")
    repository = environment.get("GITHUB_REPOSITORY", "")
    require(repository == "seradotcom/motionwright" and
            environment.get("GITHUB_WORKFLOW_REF", "").startswith(repository + "/" + WORKFLOW + "@"),
            "unexpected workflow identity")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_OS", "RUNNER_ARCH",
            "RUNNER_NAME", "ImageOS", "ImageVersion")
    return {"sdk_source_sha": SDK_SHA, "sdk_source_tree": SDK_TREE,
            **{key: environment.get(key, "") for key in keys}}


def input_paths(repo):
    raw = custody.git(repo, "ls-tree", "-r", "--name-only", "-z", SDK_SHA)
    paths = tuple(x.decode("utf-8", "strict") for x in raw.split(b"\0") if x)
    require(paths and len(paths) == len(set(paths)) and len(paths) <= 10000,
            "empty, duplicate or oversized SDK inventory")
    require(all(not Path(p).is_absolute() and ".." not in Path(p).parts for p in paths),
            "unsafe source inventory path")
    required = {*SDK_PINS, "Cargo.toml", "rust-toolchain.toml", "crates/daemon/Cargo.toml",
                "crates/driver-host/Cargo.toml", "crates/plugin-host/Cargo.toml",
                "crates/daemon/src/semwrightd.rs", "crates/driver-host/src/bin/tool_fixture.rs",
                "crates/plugin-host/src/semwright-sandbox.rs"}
    require(required <= set(paths), "compiled source closure is incomplete")
    # All tracked inputs, including manifests, build scripts and non-Rust data.
    return tuple(sorted(paths))


def source_snapshot(repo, paths):
    snapshot = custody.source_snapshot(repo, SDK_SHA, paths, SDK_TREE)
    require(snapshot["parents"] == [SDK_PARENT], "SDK ancestry changed")
    for path, digest in SDK_PINS.items():
        require(snapshot["files"][path]["sha256"] == digest, "reviewed SDK source changed: " + path)
    return snapshot


def target(package, crate, name, source):
    return {"package": package, "crate": crate, "name": name, "kind": "bin", "source": source}


DRIVER_SPECS = (
    target("semwright-driver-host", "driver-host", "semwright-driver-fixture", "crates/driver-host/src/bin/fixture.rs"),
    target("semwright-driver-host", "driver-host", "semwright-tool-fixture", "crates/driver-host/src/bin/tool_fixture.rs"),
)
SANDBOX_SPECS = (
    target("semwright-plugin-host", "plugin-host", "semwright-sandbox", "crates/plugin-host/src/semwright-sandbox.rs"),
)
TEST_SPECS = (
    target("semwright-daemon", "daemon", "semwrightd", "crates/daemon/src/semwrightd.rs"),
)


def producer_plan(cargo):
    return (
        ("driver-fixtures", [cargo, "build", "--locked", "-p", "semwright-driver-host",
          "--features", "test-tools", "--bin", "semwright-driver-fixture", "--bin",
          "semwright-tool-fixture", "--message-format=json"], DRIVER_SPECS, False),
        ("sandbox", [cargo, "build", "--locked", "-p", "semwright-plugin-host", "--bin",
          "semwright-sandbox", "--message-format=json"], SANDBOX_SPECS, False),
        ("daemon-tests", [cargo, "test", "--locked", "-p", "semwright-daemon", "--bin",
          "semwrightd", "--features", "private-host-stdin-custody", "--no-run",
          "--message-format=json"], TEST_SPECS, True),
    )


def compiled_paths(raw, code, repo, target_root, specs, testing):
    selected = sdk_gate.select_binary_paths(raw, code, repo, target_root, specs, testing=testing)
    rows = sdk_gate.cargo_rows(raw, code)
    for item in rows:
        if item.get("reason") != "compiler-artifact" or item.get("executable") not in selected.values():
            continue
        feature = ("private-host-stdin-custody" if item["target"]["name"] == "semwrightd"
                   else "test-tools" if item["target"]["name"] in
                   {"semwright-driver-fixture", "semwright-tool-fixture"} else None)
        require(feature is None or feature in item.get("features", []), "selected fixture feature absent")
    return selected


def compiled_roster(raw, code):
    require(type(code) is int and code == 0, "compiled enumeration did not exit zero")
    listed = observer.selected_roster(raw, code, {"prefixes": (CASE_PREFIX,)}, CASES, "repair")
    text = raw.decode("utf-8", "strict").replace("\r\n", "\n")
    counts = re.findall(r"(?m)^(\d+) tests?, 0 benchmarks?\s*$", text)
    require(counts == [str(len(listed["all"]))], "enumeration summary does not match its own IDs")
    return listed


def case_argv(binary, name):
    require(name in CASES, "case not selected")
    return [binary["absolute_path"], "--exact", name, "--ignored", "--nocapture",
            "--test-threads=1", "--color", "never"]


def admit_case(name, raw, row, binary, after, listed, context):
    require(name in CASES and name in listed["selected"] and name in listed["all"],
            "case was not selected and enumerated")
    require(row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and row.get("log_custody") == "VERIFIED_SNAPSHOT"
            and row.get("owned_group_after_wait") == "ABSENT", "case custody is incomplete")
    require(row.get("argv") == case_argv(binary, name) and row.get("context") == context
            and binary == after, "case command, source context or binary differs")
    require(type(row.get("exit_code")) is int and row["exit_code"] == 0, "actual selected test failed")
    text = custody.ANSI.sub("", raw.decode("utf-8", "strict").replace("\r\n", "\n"))
    require(re.findall(r"(?m)^running (\d+) tests?\s*$", text) == ["1"], "wrong case execution count")
    require(re.findall(r"(?m)^test (.+) \.\.\. (ok|FAILED|ignored)\s*$", text) == [(name, "ok")],
            "case missing, duplicated, substituted, failed or ignored")
    summaries = re.findall(r"(?m)^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; "
                          r"(\d+) ignored; (\d+) measured; (\d+) filtered out;", text)
    expected = ("ok", "1", "0", "0", "0", str(len(listed["all"]) - 1))
    require(summaries == [expected] and not re.search(r"(?m)^thread '.+'(?: \(\d+\))? panicked at", text),
            "terminal result is incomplete or contains a panic")
    return {"name": name, "status": "PASS_EXACT_REAL_HOST_CASE", "binary_sha256": binary["sha256"],
            "command": row, "child_consumption": "NOT_OBSERVED", "native_render": "NOT_INFERRED"}


def fixture_payload(raw, fixture, binaries):
    value = custody.strict_json(raw)
    expected = {*FIXTURE_VARIABLES, "driver_sha256", "tool_sha256", "manifest_sha256", "config_sha256"}
    require(type(value) is dict and set(value) == expected, "fixture output fields differ")
    require(value[FIXTURE_VARIABLES[0]] == str(fixture / "config.toml")
            and value[FIXTURE_VARIABLES[1]] == binaries["semwright-sandbox"]["absolute_path"],
            "fixture config/helper path differs")
    for key, name in (("driver_sha256", "semwright-driver-fixture"), ("tool_sha256", "semwright-tool-fixture")):
        require(value[key] == binaries[name]["sha256"], "fixture copied binary differs")
    require(all(type(value[k]) is str and re.fullmatch(r"[0-9a-f]{64}", value[k])
                for k in ("driver_sha256", "tool_sha256", "manifest_sha256", "config_sha256")),
            "fixture digest malformed")
    return value


def verify_fixture(raw, fixture, binaries, evidence):
    value = fixture_payload(raw, fixture, binaries)
    expected = {"driver": (0o500, value["driver_sha256"]), "tool": (0o500, value["tool_sha256"]),
                "manifest.json": (0o600, value["manifest_sha256"]),
                "config.toml": (0o600, value["config_sha256"])}
    require(set(p.name for p in fixture.iterdir()) == set(expected), "fixture file roster differs")
    info = fixture.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, "fixture is not private and owned")
    result = {}
    for name, (mode, digest) in expected.items():
        path = fixture / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == mode, "fixture file mode/owner differs")
        observed = custody.fingerprint(path, fixture)
        require(observed["sha256"] == digest and observed["bytes"] > 0, "fixture file changed")
        result[name] = {"original": observed,
                        "retained": observer.retain_file(path, fixture, evidence, "fixtures", observed)}
    return {"configuration": value, "files": result}


def check_budget(raw_meminfo, free_bytes):
    match = re.findall(r"(?m)^MemAvailable:\s+([0-9]+) kB$", raw_meminfo.decode("ascii", "strict"))
    require(len(match) == 1 and type(free_bytes) is int, "resource observation incomplete")
    memory = int(match[0]) * 1024
    if memory < 2 * 1024**3 or free_bytes < 12 * 1024**3:
        raise CapabilityUnavailable("insufficient disposable runner memory or disk budget")
    return {"available_memory_bytes": memory, "free_disk_bytes": free_bytes,
            "cargo_jobs": 2, "minimum_memory_bytes": 2 * 1024**3, "minimum_disk_bytes": 12 * 1024**3}


def landlock_abi():
    require(sys.platform == "linux" and platform.machine() == "x86_64", "Linux x64 ABI probe required")
    library = ctypes.CDLL(None, use_errno=True)
    library.syscall.restype = ctypes.c_long
    observed = library.syscall(ctypes.c_long(444), ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    if observed < 3:
        raise CapabilityUnavailable("Landlock ABI V3 unavailable: observed=" + str(observed)
                                    + "; errno=" + str(ctypes.get_errno()))
    return int(observed)


def sandbox_probe_argv(bwrap, helper, true_binary, libraries):
    require(type(libraries) is tuple and len(libraries) == len(set(libraries))
            and {"/usr", "/lib", "/etc"} <= set(libraries)
            and set(libraries) <= {"/usr", "/lib", "/lib64", "/etc"}, "sandbox probe roots differ")
    argv = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    for root in libraries:
        argv.extend(["--ro-bind", root, root])
    argv.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/dev/shm",
                 "--dir", "/plugin", "--ro-bind", true_binary, "/plugin/bin", "--ro-bind", helper,
                 "/plugin/helper", "--chdir", "/tmp", "--", "/plugin/helper", "--", "/plugin/bin"])
    return argv


def admit_capability(row):
    if custody.execution_unknown(row):
        raise observer.UnknownExecution("capability observation is UNKNOWN; no retry", row)
    require(row.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and row.get("log_custody") == "VERIFIED_SNAPSHOT"
            and row.get("owned_group_after_wait") == "ABSENT", "capability observation is incomplete")
    if type(row.get("exit_code")) is not int or row["exit_code"] != 0:
        raise CapabilityUnavailable("actual isolation capability failed; no security relaxation or fallback")


def isolated_environment(environment, target_root, temporary, run_root):
    forbidden = [key for key in environment if key.startswith("SEMWRIGHT_")
                 or key in {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "NODE_OPTIONS"}]
    require(not forbidden, "inherited sandbox/fixture overrides: " + ",".join(sorted(forbidden)))
    env = observer.build_environment(environment, target_root, temporary)
    runner = Path(environment["RUNNER_TEMP"]).resolve(strict=True)
    for key in ("CARGO_HOME", "RUSTUP_HOME"):
        value = Path(env.get(key, "")).resolve(strict=True)
        require(value.is_relative_to(runner) and value != runner, "cache not isolated: " + key)
    require(target_root.is_relative_to(run_root) and temporary.is_relative_to(run_root),
            "target or temporary directory escaped run root")
    env.update(CARGO_BUILD_JOBS="2", RUSTUP_TOOLCHAIN=RUST, PYTHONDONTWRITEBYTECODE="1")
    for key, name in (("XDG_RUNTIME_DIR", "runtime"), ("XDG_CACHE_HOME", "cache"),
                      ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        path = run_root / ("xdg-" + name)
        path.mkdir(mode=0o700)
        env[key] = str(path)
    return env


def final_counts(results, listed):
    require(len(results) == len(CASES) and {r["name"] for r in results} == set(CASES)
            and all(r["status"] == "PASS_EXACT_REAL_HOST_CASE" for r in results),
            "selected cases incomplete, duplicated or not successful")
    require(tuple(listed["selected"]) == CASES, "compiled selected roster differs")
    return {"selected": 4, "passed": 4, "failed": 0, "ignored_selected": 0,
            "not_selected": len(listed["not_selected"]), "not_selected_credit": "NONE"}


def observe_cases(report, binary, binaries, listed, sdk, target_root, test_env, logs,
                  evidence, fixture, raw_fixture, context, command):
    for index, name in enumerate(CASES):
        sdk_gate.verify_binaries(binaries, target_root)
        require(verify_fixture(raw_fixture, fixture, binaries, evidence) == report["fixture"],
                "fixture changed before case")
        case_context = {**context, "case_name": name}
        report["currently_selected_case"] = name
        row = command(case_argv(binary, name), sdk, test_env, logs, "case-" + str(index), 180, case_context)
        raw = observer.original_stream(logs, row, "stdout") + b"\n" + observer.original_stream(logs, row, "stderr")
        after = {"absolute_path": binary["absolute_path"], **custody.fingerprint(Path(binary["absolute_path"]), target_root)}
        report["cases"].append(admit_case(name, raw, row, binary, after, listed, case_context))


def postcheck_sources(report, gate, sdk, paths):
    errors = []
    for key, callback in (("source", lambda: source_snapshot(sdk, paths)),
                          ("gate", lambda: custody.source_snapshot(gate, report["context"]["GITHUB_SHA"], GATE_FILES))):
        before = report.get(key + "_before")
        if before is None or key + "_after" in report:
            continue
        try:
            after = callback()
            report[key + "_after"] = after
            require(after == before, key + " source changed")
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
    if errors:
        report["source_postcheck_errors"] = errors
        if report["status"] != "UNKNOWN_NO_RETRY":
            report["status"] = "FAIL_OR_INCOMPLETE"


def run(gate, sdk, output):
    gate, sdk, output = gate.absolute(), sdk.absolute(), output.absolute()
    runner = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(output.is_relative_to(runner) and output != runner and not output.exists()
            and not output.is_relative_to(gate) and not output.is_relative_to(sdk),
            "fresh run directory under RUNNER_TEMP required")
    output.mkdir(mode=0o700)
    evidence, target_root, temporary = (output / name for name in ("evidence", "target", "tmp"))
    for directory in (evidence, target_root, temporary):
        directory.mkdir(mode=0o700)
    logs = evidence / "commands"; logs.mkdir(mode=0o700)
    observations = []
    report = {"schema": SCHEMA, "started_at_utc": custody.now(), "status": "NOT_RUN_PREPARATION",
              "sdk_source_sha": SDK_SHA, "sdk_source_tree": SDK_TREE, "cases": [],
              "installed_application": "NOT_RUN", "native_render": "NOT_RUN",
              "child_consumption": "NOT_OBSERVED", "human_acceptance": False,
              "release_authorized": False, "sdk_pin_changed": False, "retry_authorized": False,
              "internal_rust_tempdirs": "REMOVED_BY_TEST_DROP_NOT_RETAINED",
              "physical_scope": "One Bootstrap/Host/Root positive, one Root collision, and two real Host captures with consumer/mpsc seams"}
    custody.write_new(evidence / "started.json", {**report, "status": "UNKNOWN_IF_NO_TERMINAL_RECEIPT"})

    def observed(argv, cwd, env, folder, label, timeout, ctx):
        return observer.command(argv, cwd, env, folder, label, timeout, ctx, (evidence, observations))

    paths = ()
    try:
        context = execution_context(os.environ); report["context"] = context
        for module, name in ((observer, "ci_foundation_causal.py"), (custody, "ci_storage_apply_recovery.py"),
                             (sdk_gate, "ci_sdk_native_source.py")):
            require(Path(module.__file__).resolve() == gate / "tooling" / name, "observer module origin differs")
        before_gate = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
        require(all(before_gate["files"][p]["sha256"] == h for p, h in GATE_PINS.items()),
                "reviewed gate dependency changed")
        report["gate_before"] = before_gate
        report["gate_sources"] = observer.retain_source(gate, before_gate, evidence)
        paths = input_paths(sdk)
        before = source_snapshot(sdk, paths); report["source_before"] = before
        report["sdk_sources"] = observer.retain_source(sdk, before, evidence)
        env = isolated_environment(os.environ, target_root, temporary, output)
        report["resources"] = check_budget(Path("/proc/meminfo").read_bytes(), shutil.disk_usage(output).free)
        report["landlock_abi"] = landlock_abi()
        bwrap = shutil.which("bwrap", path=env.get("PATH"))
        if not bwrap:
            raise CapabilityUnavailable("Bubblewrap is unavailable; no sandbox fallback")
        bwrap_path = Path(bwrap).resolve(strict=True)
        report["bubblewrap"] = {"absolute_path": str(bwrap_path), **custody.fingerprint(bwrap_path, bwrap_path.parent)}
        probe = observed([str(bwrap_path), "--unshare-all", "--die-with-parent", "--new-session",
                          "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--", "/bin/true"],
                         gate, env, logs, "bubblewrap-capability", 30, context)
        admit_capability(probe)
        tools = observer.tools_identity(gate, logs, env, context, (evidence, observations))
        report["tools"] = tools
        barrier = sdk_gate.ProducerBarrier(observed, tools["cargo"]["absolute_path"])
        prepared = {"collections": {}, "steps": [], "clippy": "NOT_SELECTED_G2"}
        for label, argv, specs, testing in producer_plan(tools["cargo"]["absolute_path"]):
            row = barrier(argv, sdk, env, logs, label, 1800, context)
            compiled_paths(observer.original_stream(logs, row, "stdout"), row["exit_code"],
                           sdk, target_root, specs, testing)
            prepared["collections"][label] = {"label": label, "command": row, "specs": specs, "testing": testing}
            prepared["steps"].append(label)
        barrier.begin_sealing()
        sealed = sdk_gate.seal_compiled_role(prepared, sdk, target_root, logs, evidence, barrier)
        binaries = {name: item for group in sealed.values() for name, item in group.items()}
        require(set(binaries) == {"semwright-driver-fixture", "semwright-tool-fixture", "semwright-sandbox", "semwrightd"},
                "compiled executable closure differs")
        report["binaries"] = binaries
        barrier.begin_observation()
        report["producer_barrier"] = {"status": "SEALED_BEFORE_OBSERVATION", "commands": prepared["steps"],
                                      "auxiliary_executables": 3, "test_executables": 1}
        true_binary = Path("/usr/bin/true").resolve(strict=True)
        report["capability_true_binary"] = {"absolute_path": str(true_binary),
                                            **custody.fingerprint(true_binary, true_binary.parent)}
        libraries = tuple(p for p in ("/usr", "/lib", "/lib64", "/etc") if Path(p).is_dir())
        enforced = barrier(sandbox_probe_argv(str(bwrap_path), binaries["semwright-sandbox"]["absolute_path"],
                           str(true_binary), libraries), gate, env, logs, "actual-helper-enforcement", 30, context)
        admit_capability(enforced)
        report["helper_enforcement"] = {"status": "EXACT_HELPER_EXIT_ZERO_BEFORE_CASES",
                                       "abi_required": 3, "command": enforced,
                                       "native_payload": "NOT_RUN_BY_CAPABILITY_PROBE"}
        fixture_parent = output / "fixture-parent"; fixture_parent.mkdir(mode=0o700)
        fixture = fixture_parent / "fixture"
        row = barrier([tools["python"]["absolute_path"], "-I", "-B", str(gate / PREPARER),
                       "--driver", binaries["semwright-driver-fixture"]["absolute_path"],
                       "--tool", binaries["semwright-tool-fixture"]["absolute_path"],
                       "--sandbox-helper", binaries["semwright-sandbox"]["absolute_path"],
                       "--sdk-source", str(sdk), "--output", str(fixture)],
                      gate, env, logs, "prepare-fixture", 45, context)
        require(type(row["exit_code"]) is int and row["exit_code"] == 0, "fixture preparer failed")
        raw_fixture = observer.original_stream(logs, row, "stdout")
        report["fixture"] = verify_fixture(raw_fixture, fixture, binaries, evidence)
        test_env = {**env, **{k: report["fixture"]["configuration"][k] for k in FIXTURE_VARIABLES}}
        binary = binaries["semwrightd"]
        row = barrier([binary["absolute_path"], "--list", "--format", "terse"], sdk, env, logs,
                      "compiled-roster", 45, context)
        listed = compiled_roster(observer.original_stream(logs, row, "stdout"), row["exit_code"])
        report["roster"] = listed
        custody.write_new(evidence / "compiled-roster.json", listed)
        observe_cases(report, binary, binaries, listed, sdk, target_root, test_env, logs,
                      evidence, fixture, raw_fixture, context, barrier)
        report["source_after"] = source_snapshot(sdk, paths)
        require(report["source_after"] == before, "SDK source changed during observation")
        report["gate_after"] = custody.source_snapshot(gate, context["GITHUB_SHA"], GATE_FILES)
        require(report["gate_after"] == before_gate, "gate source changed during observation")
        sdk_gate.verify_binaries(binaries, target_root)
        require(verify_fixture(raw_fixture, fixture, binaries, evidence) == report["fixture"], "fixture changed after cases")
        for tool in (*tools.values(), report["bubblewrap"], report["capability_true_binary"]):
            tool_path = Path(tool["absolute_path"])
            require(custody.fingerprint(tool_path, tool_path.parent) ==
                    {key: tool[key] for key in ("path", "bytes", "sha256", "identity")}, "tool identity changed")
        sdk_gate.verify_commands(evidence, observations)
        report["counts"] = final_counts(report["cases"], listed)
        report["status"] = "PASS_EXACT_FOUR_REAL_HOST_G2_CASES"
        report.pop("currently_selected_case", None)
    except (observer.UnknownExecution, custody.UnknownObservation) as error:
        report.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
    except CapabilityUnavailable as error:
        report.update(status="NOT_RUN_CAPABILITY", error=str(error))
    except BaseException as error:
        report.update(status="FAIL_OR_INCOMPLETE" if report["cases"] or report.get("currently_selected_case")
                      else "NOT_RUN_PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
    postcheck_sources(report, gate, sdk, paths)
    report["commands"] = observations
    report["ended_at_utc"] = custody.now()
    # Tuples from the shared roster become JSON arrays before comparing readback.
    report = custody.strict_json(json.dumps(report, allow_nan=False).encode("utf-8"))
    custody.write_new(evidence / "receipt.json", report)
    original = custody.fingerprint(evidence / "receipt.json", evidence)
    require(custody.strict_json((evidence / "receipt.json").read_bytes()) == report
            and custody.fingerprint(evidence / "receipt.json", evidence) == original, "final receipt readback differs")
    custody.write_new(evidence / "receipt-readback.json", original)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.gate, args.sdk, args.output)
    print(json.dumps({"status": report["status"], "sdk_source_sha": SDK_SHA,
                      "counts": report.get("counts"), "retry_authorized": False}))
    return 0 if report["status"] == "PASS_EXACT_FOUR_REAL_HOST_G2_CASES" else 1


if __name__ == "__main__":
    raise SystemExit(main())
