"""Focal IO/selector/clock controls; none is evidence of Native or cgroup G2."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "native_owner_resources.py"
SPEC = importlib.util.spec_from_file_location("native_owner_resources_focal", SOURCE)
resource = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resource)


class OriginalIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_origin_mode_is_exact_under_private_umask_and_creation_is_exclusive(self):
        before = os.umask(0o077)
        try:
            resource.new(self.root / "origin.json", {"actual": True}, mode=0o440)
        finally:
            os.umask(before)
        self.assertEqual(stat.S_IMODE((self.root / "origin.json").stat().st_mode), 0o440)
        self.assertEqual(resource.strict(resource.regular(self.root / "origin.json")), {"actual": True})
        with self.assertRaises(FileExistsError):
            resource.new(self.root / "origin.json", {"changed": True})
        self.assertEqual(resource.strict(resource.regular(self.root / "origin.json")), {"actual": True})

    def test_actual_symlink_hardlink_fifo_and_quota_are_rejected(self):
        original = self.root / "source"
        resource.new(original, b"original")
        (self.root / "alias").symlink_to(original)
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(self.root / "alias")
        os.link(original, self.root / "hardlink")
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(original)
        (self.root / "hardlink").unlink()
        os.mkfifo(self.root / "fifo", 0o600)
        started = time.monotonic()
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(self.root / "fifo")
        self.assertLess(time.monotonic() - started, 0.5)
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(original, 7)

    def test_actual_fifo_substitution_after_lstat_never_blocks(self):
        original = self.root / "source"
        resource.new(original, b"original")
        real_open = os.open
        switched = False
        def opening(path, flags, *args, **kwargs):
            nonlocal switched
            if Path(path) == original and not switched:
                switched = True
                original.unlink()
                os.mkfifo(original, 0o600)
            return real_open(path, flags, *args, **kwargs)
        started = time.monotonic()
        with mock.patch.object(resource.os, "open", side_effect=opening):
            with self.assertRaises(resource.ResourceRejected):
                resource.regular(original)
        self.assertTrue(switched)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_mutable_group_file_and_different_owner_are_rejected(self):
        path = self.root / "origin"
        resource.new(path, b"original")
        path.chmod(0o660)
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(path)
        path.chmod(0o600)
        with self.assertRaises(resource.ResourceRejected):
            resource.regular(path, owner=os.getuid() + 1)


class ClosedPolicyTests(unittest.TestCase):
    def test_no_shell_external_pid_or_arbitrary_argv_selector(self):
        original = {"source_root": "/nonexistent/source", "base_temp": "/nonexistent/temp",
                    "context": {"run_id": "123"}, "job": "hero-profiles"}
        for action, case in (("/bin/sh", None), ("prepare", 1), ("run-profile", None),
                             ("bootstrap", 3), ("kill-pid", None), ("receive-and-run;sh", None)):
            with self.subTest(action=action, case=case):
                with self.assertRaises(resource.ResourceRejected):
                    resource.selected_command("hero", action, case, original, {})
        original["job"] = "fixedtxt-session-owner"
        for action, case in (("prepare", None), ("begin-case", -1), ("begin-case", 12),
                             ("open-session", 0), ("--run-one", 0), ("source-policy", 0)):
            with self.subTest(action=action, case=case):
                with self.assertRaises(resource.ResourceRejected):
                    resource.selected_command("fixedtxt", action, case, original, {})

    def test_group_names_never_accept_a_foreign_owner_or_path(self):
        for run in ("0", "001", "123/../../user.slice", "-1", "1\n", "1;sh"):
            with self.subTest(run=run):
                with self.assertRaises(resource.ResourceRejected):
                    resource.group_path("hero", {"run_id": run, "run_attempt": "1"})
        self.assertEqual(str(resource.group_path("hero", {"run_id": "123", "run_attempt": "1"})),
                         "/sys/fs/cgroup/motionwright-hero-123-1")
        self.assertEqual(str(resource.group_path("fixedtxt", {"run_id": "123", "run_attempt": "1"})),
                         "/sys/fs/cgroup/motionwright-fixedtxt-123-1")

    def test_duplicate_and_nonfinite_origin_values_are_denied(self):
        for raw in (b'{"uid":1,"uid":2}', b'{"time":NaN}', b'{"time":Infinity}'):
            with self.assertRaises(resource.ResourceRejected):
                resource.strict(raw)

    def test_nonroot_process_cannot_enter_or_acquire_root_ids(self):
        if os.geteuid() == 0:
            self.skipTest("This negative is specifically for the nonroot caller")
        source = """import errno, os, sys
original = (os.getresuid(), os.getresgid(), os.getgroups())
try:
    os.setresuid(0, 0, 0)
except OSError as error:
    assert error.errno in (errno.EPERM, errno.EINVAL)
    assert original == (os.getresuid(), os.getresgid(), os.getgroups())
    print('NONROOT_ROOT_REENTRY_DENIED_KERNEL=' + str(error.errno))
else:
    raise AssertionError('nonroot acquired root IDs')
"""
        result = subprocess.run([sys.executable, "-I", "-B", "-c", source],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(result.stdout, (b"NONROOT_ROOT_REENTRY_DENIED_KERNEL=1\n",
                                     b"NONROOT_ROOT_REENTRY_DENIED_KERNEL=22\n"))
        with self.assertRaises(resource.ResourceRejected):
            resource.drop_privileges(os.getuid(), os.getgid())

    def test_environment_clear_update_uses_only_validated_original(self):
        source = r'''import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("actual_resources", sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
original = {"MW_NATIVE_RESOURCE_ORIGIN": "/owned/origin.original.json", "PATH": "/usr/bin:/bin", "HOME": "/owned/home"}
expected = module.controlled_environment({"scope": "hero"}, original)
os.environ.clear()
os.environ.update(expected)
assert os.environ["MW_NATIVE_RESOURCE_ORIGIN"] == original["MW_NATIVE_RESOURCE_ORIGIN"]
assert os.environ["MW_NATIVE_RESOURCE_SCOPE"] == "hero"
assert dict(os.environ) == expected
print("ORIGINAL_ENVIRONMENT_TRANSITION_OBSERVED")
'''
        result = subprocess.run([sys.executable, "-I", "-B", "-c", source, str(SOURCE)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"ORIGINAL_ENVIRONMENT_TRANSITION_OBSERVED\n")

    def test_runner_environment_publication_rejects_other_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "_runner_file_commands"
            commands.mkdir(mode=0o700)
            actual = commands / "set_env_real"
            resource.new(actual, b"")
            with mock.patch.dict(os.environ, {"GITHUB_ENV": str(actual)}):
                resource.append_runner_environment(root, os.getuid(), "KEY=original")
            self.assertEqual(actual.read_bytes(), b"KEY=original\n")
            other = root / "not-a-runner-command"
            resource.new(other, b"unchanged")
            with mock.patch.dict(os.environ, {"GITHUB_ENV": str(other)}):
                with self.assertRaises(resource.ResourceRejected):
                    resource.append_runner_environment(root, os.getuid(), "KEY=bad")
            self.assertEqual(other.read_bytes(), b"unchanged")


class ClockBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.current = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "123", "run_attempt": "1"}
        self.started = time.monotonic() - 10
        self.end = self.started + 900
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.request = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "schema": "motionwright.native.fixedtxt-workflow-request/2",
                        "owner_resources": copy.deepcopy(resource.OWNER_RESOURCE_POLICY),
                        "limits": {"attempt_seconds": 900, "custody_seconds": 120},
                        "preparation_seconds": 300, "native_seconds": 240}
        self.initial = self.make_clock(self.base, self.request, self.started)
        self.origin = {"context": self.current, "base_temp": str(self.base), "uid": os.getuid(),
                       "job": "fixedtxt-session-owner"}
        self.session = self.base / "mw-fixedtxt-session-123-1"
        self.session.mkdir(mode=0o700)
        resource.new(self.session / "session-clock.original.json", {
            "schema": "motionwright.native.fixedtxt-session-clock/1", "context": self.current,
            "boot_id": self.boot, "started_monotonic": self.started, "deadline_monotonic": self.end})
        self.case = self.base / ("fx" + "c" * 20)
        self.case.mkdir(mode=0o700)
        case_request = copy.deepcopy(self.request)
        case_request["limits"]["attempt_seconds"] = 600
        self.case_start = time.monotonic() - 1
        self.case_binding = self.make_clock(self.case, case_request, self.case_start)
        rows = [{"index": n, "namespace": str(self.base / ("fx" + str(n) * 20)),
                 "request": self.case_binding["request"]} for n in range(12)]
        rows[1]["namespace"] = str(self.case)
        resource.new(self.session / "session-plan.original.json", {"context": self.current, "cases": rows})
        self.env = {"RUNNER_TEMP": str(self.case), "MW_FIXEDTXT_SESSION_ROOT": str(self.session)}

    def make_clock(self, base, request, start):
        folder = base / "mw-fixedtxt-g2-control-123-1"
        folder.mkdir(mode=0o700)
        request_raw = resource.canonical(request)
        resource.new(folder / "request.original.json", request_raw)
        resource.new(folder / "clock.original.json", {"schema": "motionwright.native.fixedtxt-original-clock/1",
            "context": self.current, "boot_id": self.boot, "started_monotonic": start,
            "deadline_monotonic": start + request["limits"]["attempt_seconds"],
            "request_sha256": resource.descriptor(request_raw)["sha256"]})
        return resource.clock_originals("fixedtxt", self.current, base, uid=os.getuid())[0]

    def tearDown(self):
        self.temporary.cleanup()

    def test_current_case_and_session_deadlines_only_shorten(self):
        with mock.patch.object(resource, "base_temp", side_effect=lambda value: Path(value)):
            deadline, binding = resource.action_deadline("fixedtxt", "receive-and-run", 1, self.origin, self.env)
            self.assertEqual(deadline, min(self.end, self.case_start + 600))
            preparation, _ = resource.action_deadline("fixedtxt", "prepare", 1, self.origin, self.env)
            self.assertEqual(preparation, min(self.case_start + 300, deadline - 240 - 120))
            self.assertEqual(binding["initial"], self.initial)
            self.assertEqual(binding["selected"], self.case_binding)

    def test_case_namespace_context_and_request_mismatch_fail_closed(self):
        with mock.patch.object(resource, "base_temp", side_effect=lambda value: Path(value)):
            with self.assertRaises(resource.ResourceRejected):
                resource.action_deadline("fixedtxt", "receive-and-run", 2, self.origin, self.env)
            folder = self.case / "mw-fixedtxt-g2-control-123-1"
            path = folder / "request.original.json"
            changed = resource.strict(path.read_bytes())
            changed["source_sha"] = "d" * 40
            path.unlink()
            resource.new(path, changed)
            with self.assertRaises(resource.ResourceRejected):
                resource.action_deadline("fixedtxt", "receive-and-run", 1, self.origin, self.env)

    def test_expired_original_is_readable_only_for_final_observation(self):
        past = self.base / "past"
        past.mkdir(mode=0o700)
        folder = past / "mw-fixedtxt-g2-control-123-1"
        folder.mkdir(mode=0o700)
        request_raw = resource.canonical(self.request)
        resource.new(folder / "request.original.json", request_raw)
        resource.new(folder / "clock.original.json", {"schema": "motionwright.native.fixedtxt-original-clock/1",
            "context": self.current, "boot_id": self.boot, "started_monotonic": time.monotonic() - 1000,
            "deadline_monotonic": time.monotonic() - 100,
            "request_sha256": resource.descriptor(request_raw)["sha256"]})
        # Keep exact original arithmetic rather than two separate clock reads.
        clock = resource.strict((folder / "clock.original.json").read_bytes())
        clock["deadline_monotonic"] = clock["started_monotonic"] + 900
        (folder / "clock.original.json").unlink()
        resource.new(folder / "clock.original.json", clock)
        with self.assertRaises(resource.ResourceRejected):
            resource.clock_originals("fixedtxt", self.current, past, uid=os.getuid())
        actual, _ = resource.clock_originals("fixedtxt", self.current, past, uid=os.getuid(), allow_expired=True)
        self.assertEqual(actual["deadline_monotonic"], clock["deadline_monotonic"])


class PortablePrefixTests(unittest.TestCase):
    def prefix(self):
        current = {"source_sha": "a" * 40, "semwright_sha": "b" * 40, "run_id": "123", "run_attempt": "1"}
        origin = {"schema": "motionwright.native.owner-resource-origin/1", "scope": "fixedtxt", "context": current,
            "job": "fixedtxt-session-owner", "uid": 1001, "gid": 121,
            "owner_resources": copy.deepcopy(resource.OWNER_RESOURCE_POLICY), "leaves": list(resource.LEAVES),
            "source_root": "/home/runner/work/motionwright/motionwright",
            "source_closure": {"tooling/native_owner_resources.py": {"bytes": 100, "sha256": "d" * 64}},
            "cgroup": "/sys/fs/cgroup/motionwright-fixedtxt-123-1", "automatic_retry": False,
            "Native_authority_granted": False, "memory_physical_limit_bytes": resource.LIMIT,
            "memory_max_bytes": resource.MAXIMUM, "memory_high_bytes": resource.HIGH,
            "physical_reserve_bytes": resource.RESERVE, "pids_max": resource.TASKS,
            "identities": {name: [1, n + 100] for n, name in enumerate(("parent", *resource.LEAVES))}}
        raw = resource.canonical(origin)
        join = {"context": current, "origin": resource.descriptor(raw)}
        before = {"parent": {"members": [], "peak_bytes": 1000, "events": {"oom": 0, "oom_kill": 0}},
                  "leaves": {name: {"members": [101] if name == "controller" else [],
                                    "populated": name == "controller"} for name in resource.LEAVES}}
        before["physical_available_bytes"] = resource.LIMIT + resource.RESERVE
        for name, row in {"parent": before["parent"], **before["leaves"]}.items():
            row.update(device=1, inode=origin["identities"][name][1], peak_bytes=1000, current_bytes=1000,
                       events={"oom": 0, "oom_kill": 0}, pids=int(name in ("parent", "controller")),
                       populated=name in ("parent", "controller"))
            row["originals"] = {"memory.max": str(resource.MAXIMUM), "memory.high": str(resource.HIGH),
                "memory.swap.max": "0", "memory.oom.group": "1", "pids.max": str(resource.TASKS),
                "memory.peak": "1000", "memory.current": "1000", "pids.current": str(row["pids"]),
                "memory.events": "oom 0\noom_kill 0", "cgroup.events": "populated " + str(int(row["populated"]))}
        intent = {**join, "schema": "motionwright.native.owner-resource-command/1", "scope": "fixedtxt",
            "action": "prepare", "case_index": 0, "Native_outcome": "NOT_INFERRED", "automatic_retry": False,
            "controller_pid": 101, "argv": ["/original/python", "/original/native_fixedtxt_workflow.py", "--prepare-originals"],
            "before": before, "stream_bytes_each": 65536}
        controller = {**join, "pid": 101, "uid": [1001] * 3, "gid": [121] * 3, "groups": [],
            "membership": ["0::/motionwright-fixedtxt-123-1/controller"], "privileges_dropped_before_exec": True}
        spawned = {**join, "pid": 102, "automatic_retry": False}
        coordinator = {**join, "pid": 102, "parent_pid": 101, "uid": [1001] * 3, "gid": [121] * 3,
            "membership": ["0::/motionwright-fixedtxt-123-1/coordinator"], "argv": intent["argv"],
            "Native_authority_granted": False, "automatic_retry": False}
        values = dict(zip(resource.PREFIX_FILES, (intent, controller, spawned, coordinator)))
        originals = {"origin.original.json": raw,
            **{"commands/case-00-prepare/" + name: resource.canonical(value) for name, value in values.items()}}
        return current, originals

    def test_prefix_has_no_future_terminal_and_validates_without_linux_observers(self):
        current, originals = self.prefix()
        with mock.patch.object(resource, "observe_tree", side_effect=AssertionError("must remain portable")):
            result = resource.validate_resource_originals(originals, scope="fixedtxt", current=current,
                action="prepare", case=0, helper_sha256="d" * 64)
        self.assertFalse(result["guard_completion_observed"])
        self.assertEqual(result["Native_outcome"], "NOT_INFERRED")
        with self.assertRaises(resource.ResourceRejected):
            resource.validate_resource_originals(originals, scope="fixedtxt", current=current,
                action="prepare", case=0, completed=True, helper_sha256="d" * 64)
        self.assertNotIn("pwd", resource.__dict__)

    def test_source_policy_pid_uid_and_completed_prefix_substitution_are_denied(self):
        current, originals = self.prefix()
        for name, field, value in (
            ("origin.original.json", "owner_resources", {**resource.OWNER_RESOURCE_POLICY, "swap_max_bytes": False}),
            ("commands/case-00-prepare/coordinator-entry.original.json", "pid", 999),
            ("commands/case-00-prepare/controller-entry.original.json", "uid", [0, 1001, 0]),
            ("commands/case-00-prepare/controller-entry.original.json", "membership", ["0::/foreign/controller"])):
            altered = dict(originals)
            row = resource.strict(altered[name]); row[field] = value
            altered[name] = resource.canonical(row)
            with self.subTest(field=field):
                with self.assertRaises(resource.ResourceRejected):
                    resource.validate_resource_originals(altered, scope="fixedtxt", current=current,
                        action="prepare", case=0, helper_sha256="d" * 64)
        with self.assertRaises(resource.ResourceRejected):
            resource.validate_resource_originals(originals, scope="fixedtxt", current=current,
                action="prepare", case=0, helper_sha256="e" * 64)

    def test_active_guard_logs_and_future_terminal_are_excluded_from_own_capture(self):
        select = resource.journal_selection("prepare", 0)
        self.assertTrue(select(("origin.original.json",)))
        self.assertTrue(select(("case-00-prepare", "coordinator-entry.original.json")))
        self.assertFalse(select(("case-00-prepare", "stdout.original")))
        self.assertFalse(select(("case-00-prepare", "terminal.original.json")))
        self.assertTrue(select(("bootstrap", "terminal.original.json")))
        self.assertTrue(select(("bootstrap", "stdout.original")))

    def test_observed_cap_identity_and_counter_substitution_are_denied(self):
        current, originals = self.prefix()
        name = "commands/case-00-prepare/intent.original.json"
        for container, field, value in (("originals", "memory.max", "max"),
                                       ("originals", "memory.high", str(resource.MAXIMUM)),
                                       ("originals", "memory.swap.max", "1"),
                                       ("originals", "pids.max", "1024"),
                                       (None, "inode", 999), (None, "peak_bytes", resource.LIMIT + 1)):
            altered = dict(originals)
            intent = resource.strict(altered[name])
            target = intent["before"]["parent"]
            if container is not None:
                target = target[container]
            target[field] = value
            altered[name] = resource.canonical(intent)
            with self.subTest(field=field):
                with self.assertRaises(resource.ResourceRejected):
                    resource.validate_resource_originals(altered, scope="fixedtxt", current=current,
                        action="prepare", case=0, helper_sha256="d" * 64)

    def test_closed_join_requires_actual_return_and_preserves_negative_fields(self):
        current, originals = self.prefix()
        prefix = "commands/case-00-prepare/"
        intent = resource.strict(originals[prefix + "intent.original.json"])
        after = copy.deepcopy(intent["before"])
        final = copy.deepcopy(after)
        for row in [final["parent"], *final["leaves"].values()]:
            row.update(members=[], populated=False, pids=0)
            row["originals"].update({"pids.current": "0", "cgroup.events": "populated 0"})
        common = {"context": current, "origin": intent["origin"], "action": "prepare", "case_index": 0,
                  "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
        terminal = {**common, "schema": "motionwright.native.owner-resource-terminal/1", "scope": "fixedtxt",
            "original_complete": True, "exit_code": 0, "reason": None, "first_error_type": None,
            "secondary_error_types": [], "killed_owned_leaves": [], "surviving_owned_leaves": [],
            "controller_pid": 101, "child_pid": 102, "minimum_physical_available_bytes": resource.RESERVE,
            "after": after, "streams": {name: {"eof": True, "observed_bytes": 0, "retained_bytes": 0,
                "sha256": resource.descriptor(b"")["sha256"]} for name in ("stdout", "stderr")}}
        returned = {**common, "schema": "motionwright.native.owner-resource-returned/1",
                    "all_owned_groups_empty": True, "observed": final}
        originals[prefix + "terminal.original.json"] = resource.canonical(terminal)
        originals[prefix + "returned.original.json"] = resource.canonical(returned)
        result = resource.validate_resource_originals(originals, scope="fixedtxt", current=current,
            action="prepare", case=0, completed=True, helper_sha256="d" * 64)
        self.assertTrue(result["guard_completion_observed"])
        self.assertEqual(result["Native_outcome"], "NOT_INFERRED")
        for key, value in (("exit_code", False), ("reason", "ORIGINAL_DEADLINE"),
                           ("killed_owned_leaves", ["coordinator"]), ("child_pid", 999)):
            altered = dict(originals)
            changed = {**terminal, key: value}
            altered[prefix + "terminal.original.json"] = resource.canonical(changed)
            with self.subTest(key=key):
                with self.assertRaises(resource.ResourceRejected):
                    resource.validate_resource_originals(altered, scope="fixedtxt", current=current,
                        action="prepare", case=0, completed=True, helper_sha256="d" * 64)


if __name__ == "__main__":
    unittest.main()
