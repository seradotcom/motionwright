"""Synthetic controls for the focused SDK verification and process receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "motion_canvas_sdk_g1.py"
SPEC = importlib.util.spec_from_file_location("motion_canvas_sdk_g1", SOURCE)
assert SPEC is not None and SPEC.loader is not None
sdk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdk)


def rust_output(ids, *, passed=None, failed=0, ignored=0, measured=0, filtered=7):
    passed = len(ids) if passed is None else passed
    rows = [f"running {len(ids)} tests"]
    rows.extend(f"test {case} ... ok" for case in ids)
    state = "ok" if failed == 0 else "FAILED"
    rows.append(f"test result: {state}. {passed} passed; {failed} failed; "
                f"{ignored} ignored; {measured} measured; {filtered} filtered out; "
                "finished in 0.01s")
    return ("\n".join(rows) + "\n").encode()


def valid_context():
    sha = "a" * 40
    return {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_SHA": sha, "GITHUB_WORKFLOW_SHA": sha,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "123", "GITHUB_JOB": "sdk-contracts", "RUNNER_OS": "Linux",
        "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + sdk.WORKFLOW
            + "@refs/heads/verify/sdk",
    }


def child_runner(root, **kwargs):
    return sdk.CommandRunner(root, {"scope": "synthetic-only"},
                             dict(os.environ), **kwargs)


def tiny_child(runner, root, script, *, timeout=5):
    return runner.command("synthetic-child", [
        str(Path(sys.executable).resolve()), "-B", "-I", "-S", "-c", script],
        root, timeout)


def readonly_link_fixture(root):
    """Synthetic Cargo records and bytes; no compiler or SDK is executed."""
    source = root / "sdk"
    cargo = root / "cargo"
    target = root / "target"
    deps = target / "debug" / "deps"
    deps.mkdir(parents=True)
    rows = []
    registry = "registry+https://github.com/rust-lang/crates.io-index#"
    for name in sorted(set(sdk.READONLY_SDK_LIBRARIES)
                       | set(sdk.READONLY_REGISTRY_LIBRARIES)):
        if name in sdk.READONLY_SDK_LIBRARIES:
            folder = source / "crates" / sdk.READONLY_SDK_LIBRARIES[name]
            package = ("path+" + folder.as_uri() + "#"
                       + name.replace("_", "-") + "@1.0.0")
            entry = folder / "src" / "lib.rs"
        else:
            crate, version = sdk.READONLY_REGISTRY_LIBRARIES[name]
            package = registry + crate + "@" + version
            entry = (cargo / "registry" / "src" / "synthetic-index"
                     / (crate + "-" + version) / "src" / "lib.rs")
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"// synthetic original source\n")
        artifact = deps / ("lib" + name + "-0123456789abcdef.rlib")
        artifact.write_bytes(("synthetic artifact: " + name).encode())
        rows.append({
            "reason": "compiler-artifact", "package_id": package,
            "target": {"name": name, "kind": ["lib"], "crate_types": ["lib"],
                       "src_path": str(entry)},
            "profile": {"test": False}, "features": [], "fresh": False,
            "filenames": [str(artifact)], "executable": None,
        })
    native = target / "debug" / "build" / "libsqlite3-sys-synthetic" / "out"
    native.mkdir(parents=True)
    (native / "libsqlite3.a").write_bytes(b"synthetic static archive")
    rows.append({
        "reason": "build-script-executed",
        "package_id": registry + "libsqlite3-sys@0.38.2", "out_dir": str(native),
        "linked_paths": ["native=" + str(native)], "linked_libs": ["static=sqlite3"],
    })
    rows.append({"reason": "build-finished", "success": True})
    return rows, target, source, cargo


def cargo_json(rows):
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


def readonly_observation_fixture(root):
    """Synthetic serialized Host report; no SDK, Host, renderer or process runs."""
    source, output, work = root / "sdk", root / "evidence", root / "private"
    pins, expected, observed, inputs = [], {}, {}, []
    counter = 0
    def sealed(path, data):
        nonlocal counter
        counter += 1
        token = {"device": 1, "inode": counter, "size": len(data), "mode": 0o100600,
                 "uid": 1000, "gid": 1000, "nlink": 1, "mtime_seconds": 1,
                 "mtime_nanoseconds": 0, "ctime_seconds": 1, "ctime_nanoseconds": 0}
        record = {"path": str(path), "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "identity": token}
        external = {**record, "identity": [1, counter, len(data), 1000000000, 1000000000]}
        return record, external
    for key, path in (
        ("sdk_lock", source / "Cargo.lock"), ("driver", root / "driver"),
        ("sandbox", root / "sandbox"), ("new_fixture_source", root / "fixture.rs"),
        ("new_fixture_harness", root / "fixture-test"),
    ):
        observed[key], expected[key] = sealed(path, ("synthetic:" + key).encode())
    for original in sdk.READONLY_INPUTS:
        pin = dict(original)
        value = {"id": pin["project_id"], "generation": pin["generation"], "revision": 1,
                 "assets": [], "audio": [], "scenes": []}
        raw = json.dumps(value).encode()
        record, external = sealed(source / pin["path"], raw)
        pin.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        pins.append(pin)
        expected[pin["label"]] = external
        inputs.append({"label": pin["label"], "relative_source_path": pin["path"],
                       "record": record, "original_utf8": raw.decode(),
                       "project_identity_before": {key: value[key] for key in (
                           "id", "generation", "revision")}})
    def serialized(value, name):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return {name + "_utf8": text, name + "_bytes": len(text.encode()),
                name + "_sha256": hashlib.sha256(text.encode()).hexdigest()}
    names = sorted({name for _, name in sdk.READONLY_CALLS})
    names += ["synthetic.unselected." + str(i) for i in range(33 - len(names))]
    caps = [{"descriptor": {"name": name, "risk": "read_only", "idempotency": "read_only"},
             "aliases": [], "tags": [], "object_types": []} for name in names]
    catalog = {"capabilities": caps, "original_sdk_digest": sdk.READONLY_CATALOG_DIGEST,
               **serialized(caps, "serialized_value")}
    hosts = []
    for pin in pins:
        folder = work / pin["label"]
        copied_file = json.loads(json.dumps(next(x for x in inputs
                                                 if x["label"] == pin["label"])["record"]))
        copied_file["path"] = str(folder / "project/semwright-motion.json")
        copied_driver = json.loads(json.dumps(observed["driver"]))
        copied_driver["path"] = str(folder / "bin/semwright-motion-canvas-driver")
        copied_driver["identity"]["mode"] = 0o100700
        project = {"directory": str(folder / "project"),
                   "directory_identity": {"mode": 0o40700}, "file": copied_file}
        manifest = {
            "executable": copied_driver["path"], "sha256": copied_driver["sha256"],
            "network": False, "tools": [], "secrets": [],
            "mounts": [{"root": "project", "read_only": True, "execute": False}],
        }
        identity = {"id": "driver:motion-canvas", "kind": "driver", "version": "1.0.0",
                    "namespace": "driver.motion-canvas.", "application": None,
                    "origin": "driver-manifest:motionwright-ci-fixture"}
        hosts.append({
            "label": pin["label"], "state": "COMPLETED", "manifest": manifest,
            "grants": [{"name": "project", "path": str(folder / "project"),
                        "read": True, "write": False}],
            "network_opt_in": False, "project_before": project, "project_after": project,
            "driver_before": copied_driver, "driver_after": copied_driver,
            "host_state_directory": str(folder / "host-state"),
            "shutdown": {"state": "COMPLETED", "closed_token_cancelled": True},
            "identity_before": identity, "identity_after": identity,
            "catalog_before": catalog, "catalog_after": catalog,
            "project_identity_after": {
                "id": pin["project_id"], "generation": pin["generation"], "revision": 1,
                "fingerprint": pin["sha256"], "derived_from_unchanged_original_bytes": True},
        })
    calls = []
    args = [{}, {}, {"kind": "polygon"}, {},
            {"node_ref": "synthetic-node-reference", "property": "sides"},
            {"profile": {"first_frame": 0, "end_frame_exclusive": 2, "scale": "full",
                         "transparent": False, "timeout_ms": 10000}},
            {}, {}, {}, {"scene_ref": "synthetic-scene-reference"}, {}]
    for ordinal, ((label, name), arg) in enumerate(zip(sdk.READONLY_CALLS, args), 1):
        response = {"synthetic": True}
        if ordinal == 4:
            response = {"items": [{"id": "polygon", "reference": "synthetic-node-reference"}]}
        if ordinal == 9:
            response = {"items": [{"id": "cue-scene", "reference": "synthetic-scene-reference"}]}
        descriptor = next(x["descriptor"] for x in caps if x["descriptor"]["name"] == name)
        calls.append({
            "ordinal": ordinal, "host": label, "command": name, "args": arg,
            "session": "motionwright-sdk-readonly-" + label,
            "request_id": "synthetic-request-" + str(ordinal), "descriptor": descriptor,
            "descriptor_digest": "d" * 64, "state": "RETURNED", "response": response,
            **serialized(response, "response_value"),
        })
    watched = [*observed.values(), *(x["record"] for x in inputs)]
    report = {
        "schema": "motionwright.motion-canvas-readonly-host.v1",
        "started_unix_ms": 1000, "ended_unix_ms": 2000, "elapsed_millis": 1000,
        "terminal": "PASSED", "sdk_source_expected": {
            "commit": sdk.SDK_SHA, "tree": sdk.SDK_TREE,
            "complete_checkout_binding": "REQUIRED_FROM_CALLER"},
        "fixture_source_role": "NEW_SEPARATE_LIBTEST", "source_root": str(source),
        "output_directory": str(output), "private_work_root": str(work),
        "private_work_upload_allowed": False, "inputs": inputs, "hosts": hosts,
        "calls": calls, "events": [], "observed_files": observed,
        "input_postcheck": [{"path": r["path"], "before": r, "after": r, "unchanged": True}
                            for r in watched],
        "scope": dict(sdk.READONLY_SCOPE),
        "counts_expected": {"provider_connections": 2, "provider_shutdowns": 2,
                            "execute_calls": 11, "libtest_methods": 1, "original_fixture_files": 2},
        "counts": {"provider_connections_attempted": 2, "provider_connections_completed": 2,
                   "provider_shutdowns_completed": 2, "execute_calls_attempted": 11,
                   "execute_calls_completed": 11},
    }
    def event(kind, data):
        report["events"].append({"kind": kind, "data": data, "unix_ms": 1001,
                                 "elapsed_millis": len(report["events"])})
    event("fixture_preflight_complete", {
        "state": "SOURCE_INPUTS_PINNED", "sdk_source_expected": sdk.SDK_SHA,
        "complete_checkout_binding": "REQUIRED_FROM_CALLER",
        "original_inputs": inputs, "observed_files": observed})
    for host in hosts:
        label = host["label"]
        event("connect_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME",
                                 "manifest": host["manifest"], "grants": host["grants"]})
        event("connect_outcome", {"host": label, "state": "CONNECTED",
                                  "identity": host["identity_before"]})
        event("catalog_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME"})
        event("catalog_outcome", {"host": label, "state": "RETURNED", "count": 33,
                                  "original_sdk_digest": sdk.READONLY_CATALOG_DIGEST})
        for call in [x for x in calls if x["host"] == label]:
            intent = {k: v for k, v in call.items()
                      if k != "response" and not k.startswith("response_value_")}
            intent["state"] = "UNKNOWN_UNTIL_OUTCOME"
            event("execute_intent", intent)
            event("execute_outcome", call)
        event("catalog_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME"})
        event("catalog_outcome", {"host": label, "state": "RETURNED", "count": 33,
                                  "original_sdk_digest": sdk.READONLY_CATALOG_DIGEST})
        event("shutdown_intent", {"host": label, "state": "UNKNOWN_UNTIL_OUTCOME",
                                   "cleanup_after_error": False})
        event("shutdown_outcome", {"host": label, "state": "COMPLETED"})
    payload = json.dumps(report).encode()
    observation, external_report = sealed(output / "readonly-host-observation.json", payload)
    terminal = {"state": "PASSED", "observation": observation,
                "readonly_fixture_complete": True, "native_render_calls": 0,
                "node_tool_calls": 0, "formal_acceptance": False}
    stdout = ("\n".join("MW_SDK_READONLY_EVENT " + json.dumps(x) for x in report["events"])
              + "\nMW_SDK_READONLY_TERMINAL " + json.dumps(terminal) + "\n").encode()
    return report, source, output, work, expected, stdout, external_report, pins


class SdkG1Controls(unittest.TestCase):
    def test_rust_footer_and_exact_id_positive(self):
        for host, ids, filtered in (
            (False, (sdk.UNIT_IDS[0],), 43), (True, sdk.HOST_IDS, 0)
        ):
            with self.subTest(host=host):
                got = sdk.parse_rust_result(rust_output(ids, filtered=filtered),
                                            ids, host=host)
                self.assertEqual(got["counts"]["passed"], len(ids))
                self.assertEqual(set(got["selected_ids"]), set(ids))
                self.assertFalse(got["physical_native_render"])

    def test_rust_zero_tests_is_rejected(self):
        with self.assertRaises(sdk.Refused):
            sdk.parse_rust_result(rust_output((), filtered=44), (sdk.UNIT_IDS[0],))
        with self.assertRaises(sdk.Refused):
            sdk.parse_rust_result(rust_output((), filtered=0), ())

    def test_rust_ignored_or_extra_ids_rejected(self):
        ids = (sdk.UNIT_IDS[0],)
        bad = (
            rust_output(ids, ignored=1),
            rust_output(ids, measured=1),
            rust_output(ids + (sdk.UNIT_IDS[1],)),
            rust_output((sdk.UNIT_IDS[1],)),
            rust_output(ids).replace(b" ... ok\n", b" ... ignored\n"),
        )
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(sdk.Refused):
                    sdk.parse_rust_result(payload, ids)
        with self.assertRaises(sdk.Refused):
            sdk.parse_rust_result(rust_output(sdk.HOST_IDS, filtered=1),
                                  sdk.HOST_IDS, host=True)

    def test_rust_failed_or_duplicate_footer_rejected(self):
        ids = (sdk.UNIT_IDS[0],)
        for payload in (rust_output(ids, failed=1),
                        rust_output(ids) + rust_output(ids),
                        rust_output(ids).replace(b" ... ok\n", b" ... FAILED\n")):
            with self.subTest(payload=payload):
                with self.assertRaises(sdk.Refused):
                    sdk.parse_rust_result(payload, ids)

    def test_host_environment_requires_explicit_enable_and_exact_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            helper = root / "sandbox"
            helper.write_bytes(b"synthetic")
            good = {"SEMWRIGHT_TEST_MOTION_CANVAS": "1",
                    "SEMWRIGHT_TEST_SANDBOX_HELPER": str(helper)}
            sdk.validate_host_environment(good, helper)
            for update in (
                {"SEMWRIGHT_TEST_MOTION_CANVAS": ""},
                {"SEMWRIGHT_TEST_MOTION_CANVAS": "true"},
                {"SEMWRIGHT_TEST_SANDBOX_HELPER": str(root / "other")},
                {"SEMWRIGHT_TEST_MOTION_CANVAS_RENDER": "1"},
            ):
                with self.subTest(update=update):
                    with self.assertRaises(sdk.Refused):
                        sdk.validate_host_environment(good | update, helper)

    def test_context_requires_positive_attempt_and_exact_source_job(self):
        good = valid_context()
        self.assertEqual(sdk.validate_context(good, "a" * 40)["GITHUB_JOB"],
                         "sdk-contracts")
        self.assertEqual(sdk.validate_context(good | {"GITHUB_RUN_ATTEMPT": "2"},
                                              "a" * 40)["GITHUB_RUN_ATTEMPT"], 2)
        for update in (
            {"GITHUB_RUN_ATTEMPT": "0"},
            {"GITHUB_RUN_ATTEMPT": "true"},
            {"GITHUB_RUN_ATTEMPT": "-1"},
            {"GITHUB_SHA": "b" * 40},
            {"GITHUB_WORKFLOW_SHA": "b" * 40},
            {"GITHUB_JOB": "native-roundtrip"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + sdk.CALLEE
             + "@refs/heads/verify/sdk"},
            {"RUNNER_ENVIRONMENT": "self-hosted"},
        ):
            with self.subTest(update=update):
                with self.assertRaises(sdk.Refused):
                    sdk.validate_context(good | update, "a" * 40)

    def test_source_tree_rejects_duplicate_unsafe_or_link_entries(self):
        def row(path=b"a.txt", mode=b"100644"):
            return mode + b" blob " + b"a" * 40 + b"\t" + path + b"\0"
        self.assertEqual(list(sdk.parse_tree(row())), ["a.txt"])
        for payload in (
            row() + row(), row(b"../outside"), row(b"/absolute"),
            row(b"a\\b"), row(b".git/config"), row(mode=b"120000"),
            row(mode=b"160000"), row(b"a//b"), row()[:-1],
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(sdk.Refused):
                    sdk.parse_tree(payload)

    def test_compiler_artifacts_bind_names_and_executable_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target, source = root / "target", root / "source"
            target.mkdir()
            source.mkdir()
            executable = target / "test-bin"
            executable.write_bytes(b"synthetic-executable")
            message = {
                "reason": "compiler-artifact", "executable": str(executable),
                "profile": {"test": True},
                "target": {"name": "host_conformance",
                           "src_path": str(source / "tests/host_conformance.rs")},
            }
            expected = {("host_conformance", True): "tests/host_conformance.rs"}
            def payload(item=message, success=True):
                return (json.dumps(item) + "\n" + json.dumps(
                    {"reason": "build-finished", "success": success}) + "\n").encode()
            got = sdk.compiler_artifacts(payload(), target, expected, source)
            self.assertEqual(got[("host_conformance", True)]["bytes"],
                             len(b"synthetic-executable"))
            altered = json.loads(json.dumps(message))
            altered["target"]["src_path"] = str(root / "other/tests/host_conformance.rs")
            for data in (payload(altered), payload(success=1),
                         payload() + payload(), payload(success=False)):
                with self.subTest(data=data):
                    with self.assertRaises(sdk.Refused):
                        sdk.compiler_artifacts(data, target, expected, source)
            altered = json.loads(json.dumps(message))
            altered["executable"] = str(root / "outside")
            with self.assertRaises(sdk.Refused):
                sdk.compiler_artifacts(payload(altered), target, expected, source)

    def test_source_lock_exact_hash_and_sdk_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            mw, upstream = root / "mw", root / "sdk"
            mw.mkdir()
            (upstream / "scripts/dev").mkdir(parents=True)
            lock = json.dumps({"dependencies": {
                "semwright": {"revision": sdk.SDK_SHA}}}).encode()
            (mw / "SOURCE_LOCK.json").write_bytes(lock)
            (upstream / "Cargo.lock").write_bytes(b"fixture-lock")
            (upstream / sdk.POLICY_PATH).write_bytes(b"fixture-policy")
            with mock.patch.object(sdk, "MW_LOCK", hashlib.sha256(lock).hexdigest()), \
                 mock.patch.object(sdk, "SDK_LOCK",
                                   hashlib.sha256(b"fixture-lock").hexdigest()), \
                 mock.patch.object(sdk, "POLICY_SHA",
                                   hashlib.sha256(b"fixture-policy").hexdigest()):
                sdk.verify_source_lock(mw, upstream)
                (upstream / "Cargo.lock").write_bytes(b"changed")
                with self.assertRaises(sdk.Refused):
                    sdk.verify_source_lock(mw, upstream)
                (upstream / "Cargo.lock").write_bytes(b"fixture-lock")
                bad_lock = lock.replace(sdk.SDK_SHA.encode(), b"b" * 40)
                (mw / "SOURCE_LOCK.json").write_bytes(bad_lock)
                with mock.patch.object(sdk, "MW_LOCK",
                                       hashlib.sha256(bad_lock).hexdigest()):
                    with self.assertRaises(sdk.Refused):
                        sdk.verify_source_lock(mw, upstream)

    def test_bounded_capture_records_intent_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runner = child_runner(root)
            original = sdk.subprocess.Popen
            seen = []
            def launch(*args, **kwargs):
                record, intent = sdk.read_json_record(
                    root / "000-synthetic-child/intent.json")
                self.assertEqual(intent["state"], "UNKNOWN_UNTIL_OUTCOME")
                self.assertEqual(intent["argv"], args[0])
                seen.append(record)
                return original(*args, **kwargs)
            with mock.patch.object(sdk.subprocess, "Popen", side_effect=launch):
                result = tiny_child(runner, root, "print('synthetic-output')")
            self.assertEqual(len(seen), 1)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(runner.bytes(result).strip(), b"synthetic-output")
            _, outcome = sdk.read_json_record(
                root / "000-synthetic-child/outcome.json")
            self.assertEqual(outcome["drain_and_test_validation"], "PENDING")
            self.assertIs(type(outcome["exit_code"]), int)
            self.assertEqual(outcome["exit_code"], 0)

    def test_output_overflow_keeps_uncertainty_and_partial_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runner = child_runner(root, stream_cap=8, total_cap=16)
            with self.assertRaises(sdk.Incomplete):
                tiny_child(runner, root, "import sys;sys.stdout.write('x'*64)")
            self.assertTrue(runner.uncertain)
            self.assertEqual(runner.launches, 1)
            self.assertLessEqual((root / "000-synthetic-child/stdout.original")
                                 .stat().st_size, 8)
            _, capture = sdk.read_json_record(
                root / "000-synthetic-child/capture.json")
            self.assertEqual(capture["status"], "UNKNOWN")
            self.assertFalse(capture["writers_alive"])
            self.assertTrue(capture["errors"])

    def test_timeout_is_unknown_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runner = child_runner(root)
            with self.assertRaises(sdk.Incomplete):
                tiny_child(runner, root, "import time;time.sleep(3)", timeout=0.12)
            self.assertTrue(runner.uncertain)
            before = runner.launches
            with self.assertRaises(sdk.Refused):
                tiny_child(runner, root, "print('would be an unintended replay')")
            self.assertEqual(runner.launches, before)
            _, outcome = sdk.read_json_record(
                root / "000-synthetic-child/outcome.json")
            self.assertTrue(outcome["timed_out"])

    def test_late_capture_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            runner = child_runner(root)
            result = tiny_child(runner, root, "print('original')")
            Path(result["logs"]["stdout"]["path"]).write_bytes(b"replaced\n")
            with self.assertRaises(sdk.Refused):
                runner.bytes(result)

    def test_existing_journal_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            journal = root / "original.json"
            original = sdk.write_json_new(journal, {"state": "original"})
            with self.assertRaises(FileExistsError):
                sdk.write_json_new(journal, {"state": "replacement"})
            self.assertEqual(original, sdk.file_record(journal))
            (root / "000-synthetic-child").mkdir()
            runner = child_runner(root)
            with self.assertRaises(FileExistsError):
                tiny_child(runner, root, "print('unintended')")
            self.assertEqual(runner.launches, 0)

    def test_source_and_tool_mutation_prevents_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            tool = root / "tool"
            tool.write_bytes(b"first")
            before = sdk.file_record(tool)
            tool.write_bytes(b"other")
            with self.assertRaises(sdk.Refused):
                sdk.same_file(before, sdk.file_record(tool), "tool")
            with self.assertRaises(sdk.Refused):
                sdk.require_same_snapshot({"tree": "first"}, {"tree": "other"})

    def test_build_seal_uses_final_test_profile_driver(self):
        for omit_driver in (False, True):
            with self.subTest(omit_driver=omit_driver):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve()
                    target, source = root / "target", root / "sdk"
                    target.mkdir()
                    source.mkdir()
                    driver = target / "semwright-motion-canvas-driver"
                    driver.write_bytes(b"development-profile-original")
                    old_driver = sdk.file_record(driver)
                    calls = []
                    outputs = {}
                    targets = (
                        ("semwright-sandbox", False,
                         "crates/plugin-host/src/semwright-sandbox.rs"),
                        ("semwright_driver_motion_canvas", True,
                         "crates/driver-motion-canvas/src/lib.rs"),
                        ("host_conformance", True,
                         "crates/driver-motion-canvas/tests/host_conformance.rs"),
                        ("semwright-motion-canvas-driver", False,
                         "crates/driver-motion-canvas/src/main.rs"),
                    )
                    def compile_fixture(label, argv, cwd, timeout):
                        calls.append(argv)
                        self.assertEqual(cwd, source)
                        stage = len(calls)
                        selected = targets[:1] if stage == 1 else targets[1:]
                        self.assertLessEqual(stage, 2)
                        rows = []
                        for name, is_test, path in selected:
                            binary = target / name
                            binary.write_bytes(("final-test-profile:" + name).encode())
                            if omit_driver and name == "semwright-motion-canvas-driver":
                                continue
                            rows.append({
                                "reason": "compiler-artifact",
                                "target": {"name": name,
                                           "src_path": str(source / path)},
                                "profile": {"test": is_test},
                                "executable": str(binary),
                            })
                        rows.append({"reason": "build-finished", "success": True})
                        outputs[label] = ("\n".join(map(json.dumps, rows)) + "\n").encode()
                        return label
                    runner = mock.Mock()
                    runner.env = {"CARGO_TARGET_DIR": str(target)}
                    runner.command.side_effect = compile_fixture
                    runner.bytes.side_effect = outputs.__getitem__
                    if omit_driver:
                        with self.assertRaises(sdk.Refused):
                            sdk.prepare_original_executables(runner, "cargo", source)
                        continue
                    records, observation = sdk.prepare_original_executables(
                        runner, "cargo", source)
                    self.assertEqual(len(records), 4)
                    self.assertEqual(calls[0][-4:],
                                     ["-p", "semwright-plugin-host",
                                      "--bin", "semwright-sandbox"])
                    self.assertIn("--no-run", calls[1])
                    self.assertTrue(observation[
                        "execution_uses_prepared_harnesses_without_cargo"])
                    self.assertEqual(records[("semwright-motion-canvas-driver", False)],
                                     sdk.file_record(driver))
                    with self.assertRaises(sdk.Refused):
                        sdk.same_file(old_driver, sdk.file_record(driver), "old build")
                    sdk.record_binaries(records)
                    driver.write_bytes(b"changed-after-build-window")
                    with self.assertRaises(sdk.Refused):
                        sdk.record_binaries(records)

    def test_direct_harness_preserves_seals_and_package_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "sdk"
            package = source / "crates/driver-motion-canvas"
            package.mkdir(parents=True)
            records = {}
            for name, is_test in (
                ("semwright-sandbox", False),
                ("semwright-motion-canvas-driver", False),
                ("semwright_driver_motion_canvas", True),
                ("host_conformance", True),
            ):
                binary = root / name
                binary.write_bytes(("synthetic:" + name).encode())
                records[(name, is_test)] = sdk.file_record(binary)
            runner = mock.Mock()
            runner.command.return_value = {"original_result": "synthetic"}
            key = ("semwright_driver_motion_canvas", True)
            arguments = [sdk.UNIT_IDS[0], "--exact", "--nocapture"]
            got = sdk.execute_original_harness(
                runner, "original-case", records, key, arguments, source)
            self.assertEqual(got, {"original_result": "synthetic"})
            runner.command.assert_called_once_with(
                "original-case", [records[key]["path"], *arguments],
                package, 180, env=None)
            with self.assertRaises(sdk.Refused):
                sdk.execute_original_harness(
                    runner, "wrong-target", records,
                    ("semwright-motion-canvas-driver", False), [], source)
            self.assertEqual(runner.command.call_count, 1)
            def mutate_during_execution(*args, **kwargs):
                Path(records[("semwright-motion-canvas-driver", False)]["path"]) \
                    .write_bytes(b"unexpected-runtime-relink")
                return {"original_result": "synthetic"}
            runner.command.side_effect = mutate_during_execution
            with self.assertRaises(sdk.Refused):
                sdk.execute_original_harness(
                    runner, "mutated-case", records, key, arguments, source)
            self.assertEqual(runner.command.call_count, 2)

    def test_readonly_link_selects_exact_versions_and_seals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            rows, target, source, cargo = readonly_link_fixture(root)
            other_sha2 = json.loads(json.dumps(
                next(row for row in rows if row.get("target", {}).get("name") == "sha2")))
            other_sha2["package_id"] = (
                "registry+https://github.com/rust-lang/crates.io-index#sha2@0.10.9")
            rows.insert(0, other_sha2)
            result = sdk.readonly_link_inputs(cargo_json(rows), target, source, cargo)
            self.assertEqual(result["direct_library_count"], 11)
            self.assertTrue(result["externs"]["sha2"]["package_id"].endswith("sha2@0.11.0"))
            self.assertEqual(set(result["externs"]), {
                "semwright_backend_api", "semwright_driver_host", "semwright_driver_sdk",
                "semwright_driver_motion_canvas", "semwright_policy", "semwright_types",
                "serde_json", "tokio", "tokio_util", "sha2", "hex",
            })
            snapshot = sdk.readonly_dependency_snapshot(target)
            sdk.check_readonly_link_seals(result, snapshot)
            # A changed transitive search input invalidates the seal even if
            # all eleven selected direct rlibs retain their original bytes.
            extra = target / "debug" / "deps" / "libunselected-added.rlib"
            extra.write_bytes(b"new transitive input")
            with self.assertRaises(sdk.Refused):
                sdk.check_readonly_link_seals(result, snapshot)

    def test_readonly_link_rejects_unbound_or_incomplete_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            rows, target, source, cargo = readonly_link_fixture(root)
            cases = {}
            cases["missing_library"] = rows[1:]
            cases["duplicate_library"] = [rows[0], *rows]
            cases["uncompleted"] = rows[:-1]
            for name, change in (
                    ("outside_artifact", lambda value: value[0].update(
                        filenames=[str(root / "foreign.rlib")])),
                    ("different_sdk_package", lambda value: next(
                        row for row in value if row.get("target", {}).get("name")
                        == "semwright_backend_api").update(
                        package_id="path+file:///foreign#semwright-backend-api@1.0.0")),
                    ("foreign_source", lambda value: next(
                        row for row in value if row.get("target", {}).get("name")
                        == "semwright_backend_api")["target"].update(
                        src_path=str(root / "foreign.rs"))),
                    ("registry_escape", lambda value: next(
                        row for row in value if row.get("target", {}).get("name")
                        == "serde_json")["target"].update(src_path=str(root / "foreign.rs"))),
                    ("wrong_sha2_version", lambda value: next(
                        row for row in value if row.get("target", {}).get("name")
                        == "sha2").update(package_id=
                            "registry+https://github.com/rust-lang/crates.io-index#sha2@0.10.9")),
                    ("dynamic_sqlite", lambda value: value[-2].update(
                        linked_libs=["dylib=sqlite3"])),
                    ("numeric_success", lambda value: value[-1].update(success=1)),
                    ("failed_build", lambda value: value[-1].update(success=False))):
                altered = json.loads(json.dumps(rows))
                change(altered)
                cases[name] = altered
            for name, values in cases.items():
                with self.subTest(name=name), self.assertRaises(sdk.Refused):
                    sdk.readonly_link_inputs(cargo_json(values), target, source, cargo)

    def test_readonly_observation_requires_complete_original_custody(self):
        with tempfile.TemporaryDirectory() as directory:
            values = readonly_observation_fixture(Path(directory).resolve())
            report, source, output, work, expected, stdout, report_file, pins = values
            with mock.patch.object(sdk, "READONLY_INPUTS", tuple(pins)), \
                    mock.patch.object(sdk, "SDK_LOCK", expected["sdk_lock"]["sha256"]):
                def validate(value=report, external=expected, captured=stdout, record=report_file):
                    return sdk.validate_readonly_observation(
                        value, source, output, work, external, captured, record)
                result = validate()
                self.assertEqual(result["execute_calls"], 11)
                self.assertEqual(result["captured_intent_outcome_events"], 39)
                self.assertFalse(result["physical_native_render"])
                self.assertFalse(result["human_acceptance"])
                changes = (
                    ("unknown_terminal", lambda v: v.update(terminal="UNKNOWN")),
                    ("numeric_completion", lambda v: v["scope"].update(readonly_fixture_complete=1)),
                    ("unreviewed_pass_field", lambda v: v.update(release_pass=True)),
                    ("different_source", lambda v: v["sdk_source_expected"].update(commit="a" * 40)),
                    ("missing_host", lambda v: v["hosts"].pop()),
                    ("replayed_query", lambda v: v["calls"][1].update(
                        request_id=v["calls"][0]["request_id"])),
                    ("omitted_query", lambda v: v["calls"].pop()),
                    ("unfinished_query", lambda v: v["calls"][0].update(state="UNKNOWN")),
                    ("unbound_serialized_bytes", lambda v: v["calls"][0].update(
                        response_value_utf8='{"synthetic":false}')),
                    ("missing_utf8", lambda v: v["inputs"][0].pop("original_utf8")),
                    ("changed_original", lambda v: v["inputs"][0].update(original_utf8="{}")),
                    ("missing_postcheck", lambda v: v["input_postcheck"].pop()),
                    ("changed_identity", lambda v: v["input_postcheck"][0]["after"]["identity"].update(
                        inode=99999)),
                    ("unknown_shutdown", lambda v: v["hosts"][0]["shutdown"].update(state="UNKNOWN")),
                    ("changed_provider", lambda v: v["hosts"][0]["identity_after"].update(
                        origin="different-provider")),
                    ("write_granted", lambda v: v["hosts"][0]["grants"][0].update(write=True)),
                    ("private_upload", lambda v: v.update(private_work_upload_allowed=True)),
                    ("different_library_driver", lambda v: v["observed_files"]["driver"].update(
                        sha256="a" * 64)),
                    ("missing_catalog", lambda v: v["hosts"][0]["catalog_before"]["capabilities"].pop()),
                    ("missing_event", lambda v: v["events"].pop()),
                    ("human_promotion", lambda v: v["scope"].update(human_evaluation=True)),
                )
                for name, mutate in changes:
                    with self.subTest(name=name), self.assertRaises(sdk.Refused):
                        altered = json.loads(json.dumps(report))
                        mutate(altered)
                        validate(altered)
                external = json.loads(json.dumps(expected))
                external["new_fixture_harness"]["sha256"] = "b" * 64
                with self.assertRaises(sdk.Refused):
                    validate(external=external)
                for captured in (b"", stdout + stdout, stdout.replace(
                        b"MW_SDK_READONLY_EVENT ", b"omitted ", 1)):
                    with self.subTest(capture=captured[:30]), self.assertRaises(sdk.Refused):
                        validate(captured=captured)
                with self.assertRaises(sdk.Refused):
                    validate(record={**report_file, "sha256": "f" * 64})

    def test_unittest_zero_skip_and_exact_method_inventory(self):
        module = "tooling.tests.test_motion_canvas_sdk_g1"
        rows = [f"{name} ({module}.SdkG1Controls.{name}) ... ok"
                for name in sdk.QA_METHODS]
        output = ("\n".join(rows) + f"\n\nRan {len(sdk.QA_METHODS)} tests in 0.001s\n\nOK\n").encode()
        good = sdk.parse_unittest_result(output, module)
        self.assertEqual(good["tests_run"], len(sdk.QA_METHODS))
        self.assertEqual(good["skipped"], 0)
        for data in (output.replace(b"OK\n", b"OK (skipped=1)\n"),
                     output.replace(f"Ran {len(sdk.QA_METHODS)}".encode(), b"Ran 0"),
                     output.replace(sdk.QA_METHODS[0].encode(),
                                    b"test_invented", 1),
                     output.replace(module.encode(), b"other.module", 1)):
            with self.subTest(data=data):
                with self.assertRaises(sdk.Refused):
                    sdk.parse_unittest_result(data, module)


if __name__ == "__main__":
    unittest.main()
