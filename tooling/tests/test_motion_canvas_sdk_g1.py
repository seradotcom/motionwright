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
