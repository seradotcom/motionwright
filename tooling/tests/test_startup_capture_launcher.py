"""Source and owned-fixture controls; no installed application is launched."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tooling import startup_capture_launcher as launcher


def source_manifest():
    sources = {role: {"path": "tooling/" + role + ".py", "sha256": chr(97 + index) * 64}
               for index, role in enumerate(launcher.SOURCE_ROLES)}
    sources["launcher"]["path"] = launcher.LAUNCHER_PATH
    sources["core"]["sha256"] = launcher.CORE_SOURCE_SHA256
    return {"schema": launcher.MANIFEST_SCHEMA, "scope": "source-fixture", "budgets_seconds": dict(launcher.SECONDS),
            "sources": sources, "plan": {"path": "tooling/fixtures/startup-capture/plan.json", "sha256": "f" * 64},
            "required_environment": ["HOME"]}


def plan_template(manifest, parent):
    return {"operation_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "parent": str(parent),
            "sources": {role: dict(manifest["sources"][source]) for role, source in launcher.BOUND_PLAN_ROLES.items()}}


def workflow_text(manifest_sha="a" * 64, launcher_sha="b" * 64):
    return ("name: Synthetic source-only pair\non: workflow_dispatch\njobs:\n  source-controls:\n"
            "    runs-on: ubuntu-24.04\n    env:\n"
            f'      STARTUP_CAPTURE_MANIFEST_PATH: "{launcher.MANIFEST_PATH}"\n'
            f'      STARTUP_CAPTURE_MANIFEST_SHA256: "{manifest_sha}"\n'
            f'      STARTUP_CAPTURE_LAUNCHER_PATH: "{launcher.LAUNCHER_PATH}"\n'
            f'      STARTUP_CAPTURE_LAUNCHER_SHA256: "{launcher_sha}"\n'
            "    steps:\n      - run: source_fixture_only\n").encode()


def ci_environment(source="a" * 40):
    return {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "fixture/repository", "GITHUB_SHA": source,
            "GITHUB_WORKFLOW_SHA": source, "GITHUB_WORKFLOW_REF": "fixture/repository/" + launcher.WORKFLOW_PATH + "@refs/heads/source-fixture",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "source-controls"}


def context_fixture(repo):
    manifest = source_manifest()
    template = plan_template(manifest, repo / "private")
    manifest["plan"]["sha256"] = launcher.digest(launcher.canonical(template))
    sources = launcher._sources(repo, manifest)
    started = 1_000_000_000
    plan = launcher.prepared_plan(template, sources, started)
    environment = launcher.allowlisted_environment({"HOME": str(repo / "home")})
    workflow = {**launcher._workflow_identity(ci_environment()), "sha256": "c" * 64,
                "original_base64": base64.b64encode(workflow_text()).decode()}
    def identity(pid, role):
        return {"pid": pid, "start_ticks": 5, "parent_pid": 12, "uid": 1000, "session_id": pid,
                "process_group_id": pid, "source": sources[role],
                "executable": {"path": "/python", "sha256": "d" * 64}, "cmdline_sha256": "e" * 64}
    data = {"schema": launcher.SCHEMA, "repo": str(repo), "started_ns": started,
            "deadlines": launcher.original_deadlines(started, now_ns=2_000_000_000),
            "launcher": identity(321, "launcher"), "outer": identity(654, "outer"), "workflow": workflow,
            "manifest": {"path": str(repo / launcher.MANIFEST_PATH), "sha256": launcher.digest(launcher.canonical(manifest)),
                         "value": manifest, "original_base64": base64.b64encode(launcher.canonical(manifest)).decode()},
            "plan_template": {"path": str(repo / manifest["plan"]["path"]), "sha256": manifest["plan"]["sha256"], "value": template},
            "plan_sha256": launcher.digest(launcher.canonical(plan)), "environment": environment,
            "transport": {"fd": 99, "identity": [1, 2, 3]}}
    raw = launcher.canonical(data)
    context = launcher.LaunchContext(data, launcher.digest(raw), [1, 2, 3], raw)
    return context, plan, sources


class LauncherSourceControls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="startup-launcher-controls-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_original_clock_rejects_missing_bool_future_and_expired_before_fd_access(self):
        now = 50 * launcher.NS
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=now), \
                mock.patch.object(launcher, "_pipe_identity") as reader, mock.patch.object(launcher.os, "close") as close:
            for started in (None, True, now + 1, now - 24 * launcher.NS):
                with self.subTest(started=started), self.assertRaises(launcher.LauncherError):
                    launcher.read_launch_context(99, started_ns=started)
            reader.assert_not_called(); close.assert_not_called()

    def test_context_frame_cannot_replace_original_cli_clock(self):
        started = 10 * launcher.NS
        raw = launcher.canonical({"started_ns": started + 1, "transport": {"fd": 99, "identity": [1, 2, 3]}})
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=started + 2), \
                mock.patch.object(launcher, "_pipe_identity", return_value=[1, 2, 3]), \
                mock.patch.object(launcher.os, "set_blocking", create=True), \
                mock.patch.object(launcher.select, "select", return_value=([99], [], [])), \
                mock.patch.object(launcher.os, "read", side_effect=[raw, b""]), \
                mock.patch.object(launcher.os, "close") as close:
            with self.assertRaisesRegex(launcher.LauncherError, "CLI original clock") as caught:
                launcher.read_launch_context(99, started_ns=started)
            self.assertEqual(caught.exception.prefix, raw)
            close.assert_called_once_with(99)

    def test_context_read_requires_eof_and_one_close_without_replay(self):
        started = 10 * launcher.NS
        raw = launcher.canonical({"started_ns": started, "transport": {"fd": 99, "identity": [1, 2, 3]}})
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=started + 2), \
                mock.patch.object(launcher, "_pipe_identity", return_value=[1, 2, 3]), \
                mock.patch.object(launcher.os, "set_blocking", create=True), \
                mock.patch.object(launcher.select, "select", return_value=([99], [], [])), \
                mock.patch.object(launcher.os, "read", side_effect=[raw, b""]) as read, \
                mock.patch.object(launcher.os, "close") as close:
            bound = launcher.read_launch_context(99, started_ns=started)
            self.assertEqual(bound.raw, raw); self.assertEqual(read.call_count, 2)
            close.assert_called_once_with(99)
        with mock.patch.object(launcher.os, "close", side_effect=OSError("uncertain")) as close:
            owned = launcher.OwnedFD(99, "original")
            self.assertFalse(owned.close()); self.assertFalse(owned.close())
            close.assert_called_once_with(99)

    def test_loader_python_and_shell_overrides_reject_before_any_spawn(self):
        for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME", "BASH_ENV", "ENV", "GCONV_PATH"):
            with self.subTest(key=key), mock.patch.object(launcher, "_spawn_worker") as spawn, self.assertRaises(launcher.LauncherError):
                launcher.allowlisted_environment({key: "injected"})
            spawn.assert_not_called()
        source = {"HOME": str(self.root), "DISPLAY": ":99", "XDG_CONFIG_HOME": str(self.root / "profile"),
                  "LD_AUDIT": "", "SECRET_TOKEN": "not-a-child-input"}
        clean = launcher.allowlisted_environment(source)
        self.assertEqual(clean["DISPLAY"], ":99")
        self.assertEqual(clean["XDG_CONFIG_HOME"], source["XDG_CONFIG_HOME"])
        self.assertNotIn("SECRET_TOKEN", clean); self.assertNotIn("LD_AUDIT", clean)
        self.assertEqual({k: clean[k] for k in launcher.PYTHON_ENV}, launcher.PYTHON_ENV)

    def test_workflow_anchor_is_literal_in_the_actual_job_environment(self):
        raw = workflow_text()
        anchor = launcher.workflow_literals(raw, "source-controls")
        self.assertEqual(anchor["STARTUP_CAPTURE_MANIFEST_SHA256"], "a" * 64)
        for changed in (raw.replace(b'      STARTUP_CAPTURE_MANIFEST_SHA256:', b'      # STARTUP_CAPTURE_MANIFEST_SHA256:'),
                        raw.replace(b'      STARTUP_CAPTURE_MANIFEST_SHA256:', b'      "STARTUP_CAPTURE_MANIFEST_SHA256":'),
                        raw.replace(b"    env:\n", b"    env:\n      <<: *foreign\n"),
                        raw.replace(b"    env:\n", b'    env:\n      "STARTUP_CAPTURE_MANIFEST_SHA256": "' + b"c" * 64 + b'"\n'),
                        raw.replace(b"    env:\n", b"    env: &foreign\n"),
                        raw.replace(b'"' + b"a" * 64 + b'"', b'"${{ inputs.digest }}"')):
            with self.subTest(changed=changed[:100]), self.assertRaises(launcher.LauncherError):
                launcher.workflow_literals(changed, "source-controls")
        with self.assertRaises(launcher.LauncherError):
            launcher.workflow_literals(raw, "foreign-job")

    def test_original_workflow_source_is_distinct_from_checkout_source(self):
        environment = ci_environment()
        environment["GITHUB_WORKFLOW_SHA"] = "b" * 40
        value = launcher._workflow_identity(environment)
        self.assertEqual(value["source_sha"], "a" * 40)
        self.assertEqual(value["workflow_sha"], "b" * 40)
        for key, replacement in (("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_WORKFLOW_REF", "foreign/workflow@ref"),
                                 ("GITHUB_SHA", "main"), ("GITHUB_ACTIONS", "false")):
            with self.subTest(key=key), self.assertRaises(launcher.LauncherError):
                launcher._workflow_identity({**environment, key: replacement})

    def test_manifest_forbids_self_hash_future_workflow_and_changed_budgets(self):
        manifest = source_manifest()
        self.assertEqual(launcher.validate_manifest(manifest), manifest)
        for changed in ({**manifest, "sha256": "0" * 64}, {**manifest, "workflow_sha256": "0" * 64},
                        {**manifest, "budgets_seconds": {**launcher.SECONDS, "custody": 41}},
                        {**manifest, "budgets_seconds": {**launcher.SECONDS, "operation": True}}):
            with self.subTest(changed=changed.keys()), self.assertRaises(launcher.LauncherError):
                launcher.validate_manifest(changed)
        foreign = copy.deepcopy(manifest); foreign["sources"]["core"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(launcher.LauncherError, "stable core"):
            launcher.validate_manifest(foreign)

    def test_plan_cannot_supply_runtime_fields_or_change_source_hashes(self):
        manifest = source_manifest(); template = plan_template(manifest, self.root)
        sources = launcher._sources(self.root, manifest)
        for field in launcher.RUNTIME_FIELDS:
            with self.subTest(field=field), self.assertRaisesRegex(launcher.LauncherError, "runtime"):
                launcher.prepared_plan({**template, field: None}, sources, 1)
        foreign = copy.deepcopy(template); foreign["sources"]["bootstrap"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(launcher.LauncherError, "source digest"):
            launcher.prepared_plan(foreign, sources, 1)

    def test_foreign_plan_and_unbound_context_reject_before_kernel_or_source_reader(self):
        context, plan, _ = context_fixture(self.root)
        foreign = copy.deepcopy(plan); foreign["sources"]["bootstrap"]["sha256"] = "0" * 64
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=2 * launcher.NS), \
                mock.patch.object(launcher, "_verify_kernel_binding") as kernel, \
                mock.patch.object(launcher, "_admission_bytes") as reader:
            for candidate, supplied in ((None, plan), (context.data, plan), (context, foreign)):
                with self.subTest(candidate=type(candidate).__name__), self.assertRaises(launcher.LauncherError):
                    launcher.verify_child_launch_context(candidate, "b" * 64, supplied, expected_launcher_sha256="a" * 64)
            kernel.assert_not_called(); reader.assert_not_called()

    def test_context_positive_rechecks_parent_and_manifest_and_returns_descriptors(self):
        context, plan, sources = context_fixture(self.root)
        readback = {key: context.data[key] for key in ("manifest", "workflow", "plan_template")}
        readback["plan"] = plan
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=2 * launcher.NS), \
                mock.patch.object(launcher.os, "getppid", return_value=321), mock.patch.object(launcher.os, "getpid", return_value=654), \
                mock.patch.object(launcher, "_verify_kernel_binding") as kernel, \
                mock.patch.object(launcher, "_admission_bytes", return_value=readback) as reader, \
                mock.patch.dict(launcher.os.environ, context.data["environment"], clear=True):
            verified = launcher.verify_child_launch_context(context, "b" * 64, plan, expected_launcher_sha256="a" * 64)
        self.assertEqual(kernel.call_count, 2); reader.assert_called_once()
        self.assertEqual(verified["sources"], sources)
        self.assertEqual(verified["sources"]["bootstrap"]["path"], sources["bootstrap"]["path"])
        self.assertFalse(verified["provenance"]["human_review_authenticated"])

    def test_self_consistent_foreign_manifest_cannot_replace_actual_workflow_readback(self):
        context, plan, _ = context_fixture(self.root)
        actual = {key: copy.deepcopy(context.data[key]) for key in ("manifest", "workflow", "plan_template")}
        actual["plan"] = copy.deepcopy(plan)
        data = copy.deepcopy(context.data)
        data["manifest"]["sha256"] = "0" * 64
        raw = launcher.canonical(data)
        foreign = launcher.LaunchContext(data, launcher.digest(raw), context.transport_identity, raw)
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=2 * launcher.NS), \
                mock.patch.object(launcher.os, "getppid", return_value=321), mock.patch.object(launcher.os, "getpid", return_value=654), \
                mock.patch.object(launcher, "_verify_kernel_binding"), \
                mock.patch.object(launcher, "_admission_bytes", return_value=actual), \
                mock.patch.dict(launcher.os.environ, context.data["environment"], clear=True), \
                self.assertRaisesRegex(launcher.LauncherError, "workflow/manifest"):
            launcher.verify_child_launch_context(foreign, "b" * 64, plan, expected_launcher_sha256="a" * 64)

    def test_direct_launch_cannot_supply_a_plausible_context_parent(self):
        context, plan, _ = context_fixture(self.root)
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=2 * launcher.NS), \
                mock.patch.object(launcher.os, "getppid", return_value=999), \
                mock.patch.object(launcher, "_verify_kernel_binding") as kernel, \
                self.assertRaisesRegex(launcher.LauncherError, "direct launch"):
            launcher.verify_child_launch_context(context, "b" * 64, plan, expected_launcher_sha256="a" * 64)
        kernel.assert_not_called()

    def test_late_entry_clock_rejects_before_preparation_or_directories(self):
        with mock.patch.object(launcher, "_spawn_worker") as spawn, mock.patch.object(Path, "mkdir") as mkdir:
            with self.assertRaises(launcher.LauncherError):
                launcher.run_launcher(self.root, {}, started_ns=1, clock=lambda: 25 * launcher.NS)
            spawn.assert_not_called(); mkdir.assert_not_called()

    def test_main_captures_clock_before_silent_parser_without_a_second_origin(self):
        with mock.patch.object(launcher.time, "monotonic_ns", return_value=123), \
                mock.patch.object(launcher.sys, "argv", ["launcher", "--repo", "fixture"]), \
                mock.patch.object(launcher, "run_launcher", return_value={}) as run, \
                mock.patch.object(launcher, "emit_result", return_value=124):
            self.assertEqual(launcher.main(), 124)
            run.assert_called_once_with("fixture", started_ns=123)
        with mock.patch.object(launcher.sys, "argv", ["launcher", "--help"]), \
                mock.patch.object(launcher.sys, "stdout") as out, mock.patch.object(launcher, "run_launcher") as run:
            with self.assertRaises(launcher.LauncherError): launcher.main()
            out.write.assert_not_called(); run.assert_not_called()

    def test_first_parent_close_error_does_not_skip_or_repeat_other_endpoints(self):
        endpoints = [launcher.OwnedFD(number, str(number)) for number in (90, 91, 92, 93)]
        attempts = []
        def close(fd):
            attempts.append(fd)
            if fd == 91: raise OSError("uncertain first close")
        with mock.patch.object(launcher, "_require_linux"), mock.patch.object(launcher, "_pipe", side_effect=[tuple(endpoints[:2]), tuple(endpoints[2:])]), \
                mock.patch.object(launcher.os, "fork", return_value=555, create=True), \
                mock.patch.object(launcher.os, "close", side_effect=close), \
                mock.patch.object(launcher.os, "pidfd_open", create=True) as pidfd:
            with self.assertRaises(launcher.LauncherError) as caught:
                launcher._spawn_worker(lambda _fd: None, time.monotonic_ns() + launcher.NS)
            child = caught.exception.custody
            self.assertIsInstance(child, launcher.Child)
            child.close(); child.close()
            self.assertEqual(attempts.count(91), 1)
            self.assertEqual(attempts.count(92), 1)
            self.assertTrue(any(item["close_error"] for item in child.metadata()["descriptors"]))
            pidfd.assert_not_called()

    def test_unverified_handle_never_signals_a_foreign_pidfd(self):
        child = launcher.Child(123, launcher.OwnedFD(90, "result"), launcher.OwnedFD(91, "gate"),
                               launcher.OwnedFD(92, "pidfd"), None)
        with mock.patch.object(launcher.signal, "pidfd_send_signal", create=True) as send:
            child.kill_once(1)
            send.assert_not_called()
        self.assertEqual(child.signal_attempts, [])

    def test_external_phase_retention_does_not_repair_missing_eof_or_clock(self):
        started = 1 * launcher.NS
        deadlines = launcher.original_deadlines(started, now_ns=started)
        phase = b'{"phase":"observed-prefix"}\n'
        value = {"status": "CAPTURE_CUSTODY_COMPLETE", "start_monotonic_ns": started,
                 "operation_deadline_ns": deadlines["operation_ns"], "join_deadline_ns": deadlines["join_ns"],
                 "cleanup_deadline_ns": deadlines["cleanup_ns"], "phase_durable": False,
                 "phase_original_base64": base64.b64encode(phase).decode(),
                 "collector": {"phase": {"bytes": len(phase), "sha256": launcher.digest(phase),
                                           "eof": False, "truncated": False, "errors": []}}}
        prepared = {"context": {"started_ns": started, "deadlines": deadlines, "workflow": {},
                                "manifest": {"sha256": "a" * 64, "value": {"scope": "source-fixture"}}}}
        def capture(item):
            return {"complete": True, "stdout": launcher.canonical(item), "stderr": b"", "stdout_metadata": {"eof": True, "truncated": False}}
        files, record, complete = launcher.external_payload(capture(value), prepared)
        self.assertEqual(files["phase.original"], phase); self.assertFalse(complete)
        self.assertTrue(record["phase"]["retained_prefix"])
        self.assertFalse(record["this_record_claims_writer_completion"])
        value["collector"]["phase"]["eof"] = True
        self.assertTrue(launcher.external_payload(capture(value), prepared)[2])
        value["cleanup_deadline_ns"] += 1
        self.assertFalse(launcher.external_payload(capture(value), prepared)[2])

    def test_external_persistence_limits_are_checked_before_fork(self):
        files = {"outer.stdout.original": b"x" * (launcher.OUTER_STDOUT_LIMIT + 1), "outer.stderr.original": b"",
                 "phase.original": b"", "launcher.json": b"{}"}
        with mock.patch.object(launcher, "_spawn_worker") as spawn, self.assertRaisesRegex(launcher.LauncherError, "before fork"):
            launcher.persist_external(None, self.root, files, 123)
        spawn.assert_not_called()

    def test_h_expired_output_does_not_spawn_print_or_extend_deadline(self):
        result = {"deadlines": {"custody_ns": 40}, "status": "UNKNOWN"}
        with mock.patch.object(launcher, "_spawn_worker") as spawn, mock.patch.object(launcher.sys, "stdout") as out:
            self.assertEqual(launcher.emit_result(result, clock=lambda: 40), 124)
            spawn.assert_not_called(); out.write.assert_not_called()

    def test_fixture_double_timeout_retains_both_prefixes_and_unknown_terminal(self):
        fixture = LauncherLinuxFixtures("test_frozen_pair_real_parent_context_wait_eof_and_external_readback")
        fixture.root = self.root; fixture.repo = self.root / "repository"
        child = mock.Mock(pid=123, returncode=None)
        child.poll.return_value = None
        child.communicate.side_effect = [
            subprocess.TimeoutExpired(["owned-fixture"], 45, output=b"first-prefix", stderr=b"first-error"),
            subprocess.TimeoutExpired(["owned-fixture"], 3, output=b"first-prefix-and-tail", stderr=b"retained-error")]
        with mock.patch.object(subprocess, "Popen", return_value=child), \
                mock.patch.object(os, "pidfd_open", return_value=91, create=True), \
                mock.patch.object(signal, "SIGKILL", 9, create=True), \
                mock.patch.object(signal, "pidfd_send_signal", create=True) as send, \
                mock.patch.object(os, "close") as close, self.assertRaises(AssertionError):
            fixture.execute_launcher({})
        send.assert_called_once_with(91, 9, None, 0)
        close.assert_called_once_with(91)
        self.assertEqual((self.root / "launcher.stdout.original").read_bytes(), b"first-prefix-and-tail")
        self.assertEqual((self.root / "launcher.timeout-01.stdout.original").read_bytes(), b"first-prefix")
        terminal = json.loads((self.root / "launcher.terminal.json").read_bytes())
        self.assertEqual(terminal["status"], "UNKNOWN")
        self.assertIsNone(terminal["exit_code"])
        self.assertFalse(terminal["stdout_stderr_eof"])
        self.assertEqual(terminal["descendant_closure"], "NOT_OBSERVED")


LINUX = sys.platform.startswith("linux") and hasattr(os, "fork") and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")


@unittest.skipUnless(LINUX, "owned Linux pidfd fixtures require Linux")
class LauncherLinuxFixtures(unittest.TestCase):
    """Synthetic admission only; retained roots are available to the source gate."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="startup-launcher-linux-originals-")).resolve()
        self.repo = self.root / "repository"; self.repo.mkdir(mode=0o700)
        self.private = self.root / "private"; self.private.mkdir(mode=0o700)
        self.home = self.root / "home"; self.home.mkdir(mode=0o700)
        sys.stderr.write("STARTUP_LAUNCHER_FIXTURE_ORIGINALS=" + str(self.root) + "\n")

    def git(self, *arguments):
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LANG": "C",
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        result = subprocess.run(["/usr/bin/git", "-C", str(self.repo), *arguments], env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def write(self, relative, raw):
        target = self.repo / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return target

    def frozen_pair(self):
        source_root = Path(__file__).resolve().parents[2]
        launcher_raw = (source_root / "tooling/startup_capture_launcher.py").read_bytes()
        core_raw = (source_root / "tooling/startup_capture_core.py").read_bytes()
        collector_raw = (source_root / "tooling/startup_capture_collector.py").read_bytes()
        self.assertEqual(launcher.digest(core_raw), launcher.CORE_SOURCE_SHA256)
        self.write(launcher.LAUNCHER_PATH, launcher_raw)
        self.write("tooling/startup_capture_core.py", core_raw)
        self.write("tooling/startup_capture_collector.py", collector_raw)
        launcher_sha = launcher.digest(launcher_raw)
        actor = '''"""Owned source-fixture actor, not the production outer or consumer."""
import argparse, base64, hashlib, json, os, pathlib, sys, types
parser=argparse.ArgumentParser(add_help=False)
parser.add_argument('--plan', required=True)
parser.add_argument('--launch-context-fd', type=int, required=True)
parser.add_argument('--started-ns', type=int, required=True)
args=parser.parse_args()
path=pathlib.Path(__file__).resolve().parent/'startup_capture_launcher.py'
raw=path.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'LAUNCHER_PIN'
mod=types.ModuleType('_frozen_pair_launcher');mod.__file__=str(path);sys.modules[mod.__name__]=mod
exec(compile(raw,str(path),'exec'),mod.__dict__)
context=mod.read_launch_context(args.launch_context_fd,started_ns=args.started_ns)
plan=mod.strict_json(pathlib.Path(args.plan).read_bytes(),65536)
verified=mod.verify_child_launch_context(context,hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    plan,expected_launcher_sha256='LAUNCHER_PIN')
phase=b'{"fixture":"owned-L-to-O-channel"}\\n'
result={'schema':'motionwright.startup-capture-outer/1','scope':'SOURCE_FIXTURE_ONLY',
 'status':'CAPTURE_CUSTODY_COMPLETE','start_monotonic_ns':verified['started_ns'],
 'operation_deadline_ns':verified['operation_ns'],'join_deadline_ns':verified['join_ns'],
 'cleanup_deadline_ns':verified['cleanup_ns'],'phase_durable':False,
 'phase_original_base64':base64.b64encode(phase).decode(),
 'collector':{'phase':{'bytes':len(phase),'sha256':hashlib.sha256(phase).hexdigest(),
 'eof':True,'truncated':False,'errors':[]}},
 'effective_environment':dict(os.environ),'verified_manifest_sha':verified['provenance']['manifest_sha256'],
 'installed_result':'NOT_RUN','formal_acceptance':False}
data=mod.canonical(result);os.set_blocking(1,False)
mod._write_pipe(1,data,verified['custody_ns'])
os._exit(0)
'''.replace("LAUNCHER_PIN", launcher_sha).encode()
        self.write("tooling/frozen_outer.py", actor)
        bootstrap = b'"""Unexecuted source-fixture bootstrap identity."""\n'
        observer = b'"""Unexecuted source-fixture observer identity."""\n'
        self.write("tooling/fixture_bootstrap.py", bootstrap)
        self.write("tooling/fixture_observer.py", observer)
        paths = {"launcher": (launcher.LAUNCHER_PATH, launcher_raw), "outer": ("tooling/frozen_outer.py", actor),
                 "bootstrap": ("tooling/fixture_bootstrap.py", bootstrap), "core": ("tooling/startup_capture_core.py", core_raw),
                 "collector": ("tooling/startup_capture_collector.py", collector_raw), "observer": ("tooling/fixture_observer.py", observer)}
        manifest = {"schema": launcher.MANIFEST_SCHEMA, "scope": "source-fixture", "budgets_seconds": dict(launcher.SECONDS),
                    "sources": {role: {"path": path, "sha256": launcher.digest(raw)} for role, (path, raw) in paths.items()},
                    "plan": {"path": "tooling/fixtures/startup-capture/plan.json", "sha256": "0" * 64},
                    "required_environment": ["HOME"]}
        template = plan_template(manifest, self.private)
        template_raw = launcher.canonical(template)
        manifest["plan"]["sha256"] = launcher.digest(template_raw)
        self.write(manifest["plan"]["path"], template_raw)
        manifest_raw = launcher.canonical(manifest)
        self.write(launcher.MANIFEST_PATH, manifest_raw)
        self.write(launcher.WORKFLOW_PATH, workflow_text(launcher.digest(manifest_raw), launcher_sha))
        self.git("init", "--quiet")
        self.git("add", "--all")
        self.git("-c", "user.name=Source fixture", "-c", "user.email=source-fixture@example.invalid", "commit", "--quiet", "-m", "Frozen synthetic source-only pair")
        source = self.git("rev-parse", "HEAD").decode().strip()
        environment = {**launcher.allowlisted_environment({"HOME": str(self.home), "DISPLAY": ":99"}), **ci_environment(source),
                       "UNRELATED_SECRET": "must-not-reach-outer"}
        return manifest, template, environment

    def execute_launcher(self, environment):
        argv = [sys.executable, "-I", "-B", str(self.repo / launcher.LAUNCHER_PATH), "--repo", str(self.repo)]
        child = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pidfd = None
        timeout = False; eof = False
        stdout = stderr = b""
        errors = []; timed_out_prefixes = []
        try:
            try:
                pidfd = os.pidfd_open(child.pid)
            except OSError as exc:
                errors.append("owned child pidfd unavailable: " + repr(exc))
            try:
                stdout, stderr = child.communicate(timeout=45)
                eof = True
            except subprocess.TimeoutExpired as first:
                timeout = True
                stdout = first.output or b""; stderr = first.stderr or b""
                timed_out_prefixes.append((stdout, stderr))
                if pidfd is not None:
                    try:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
                    except OSError as exc:
                        errors.append("owned pidfd signal uncertain: " + repr(exc))
                try:
                    stdout, stderr = child.communicate(timeout=3)
                    eof = True
                except subprocess.TimeoutExpired as second:
                    stdout = second.output if second.output is not None else stdout
                    stderr = second.stderr if second.stderr is not None else stderr
                    timed_out_prefixes.append((stdout, stderr))
                    errors.append("UNKNOWN after owned-child cleanup; descendant pipe closure not observed")
        except BaseException as exc:
            errors.append("fixture observation error: " + repr(exc))
        finally:
            child.poll()
            if pidfd is not None:
                owned, pidfd = pidfd, None
                try: os.close(owned)
                except OSError as exc: errors.append("pidfd close uncertain: " + repr(exc))
            for stream in (child.stdout, child.stderr):
                try: stream.close()
                except OSError as exc: errors.append("fixture read descriptor close uncertain: " + repr(exc))
            (self.root / "launcher.stdout.original").write_bytes(stdout)
            (self.root / "launcher.stderr.original").write_bytes(stderr)
            for index, (out_prefix, err_prefix) in enumerate(timed_out_prefixes, 1):
                (self.root / f"launcher.timeout-{index:02d}.stdout.original").write_bytes(out_prefix)
                (self.root / f"launcher.timeout-{index:02d}.stderr.original").write_bytes(err_prefix)
            (self.root / "launcher.terminal.json").write_bytes(launcher.canonical({"argv": argv, "pid": child.pid,
                "exit_code": child.returncode, "harness_timeout": timeout, "stdout_stderr_eof": eof,
                "status": "UNKNOWN" if timeout or errors or not eof else "TERMINAL_OBSERVED",
                "errors": errors, "descendant_closure": "NOT_OBSERVED", "scope": "SOURCE_FIXTURE_ONLY"}))
        self.assertFalse(timeout or errors or not eof,
                         "UNKNOWN launcher exceeded fixture safety observation or custody; originals retained")
        return child.returncode, stdout, stderr

    def test_frozen_pair_real_parent_context_wait_eof_and_external_readback(self):
        manifest, template, environment = self.frozen_pair()
        code, stdout, stderr = self.execute_launcher(environment)
        self.assertEqual(code, 0, (stdout + stderr).decode(errors="replace"))
        result = launcher.strict_json(stdout, 4 * 1024 * 1024)
        self.assertEqual(result["status"], "EXTERNAL_CAPTURE_PERSISTED")
        self.assertEqual(result["scope"], "source-fixture")
        self.assertEqual(result["deadlines"]["custody_ns"], result["started_ns"] + 40 * launcher.NS)
        self.assertLess(result["finished_monotonic_ns"], result["deadlines"]["custody_ns"])
        self.assertEqual(result["retained_outer"]["original_wait"]["exit_code"], 0)
        self.assertIsNotNone(result["retained_outer"]["pidfd_exit_monotonic_ns"])
        persistence = result["persistence"]
        self.assertEqual(persistence["status"], "PERSISTED_AND_READ_BACK")
        self.assertEqual(persistence["writer"]["original_wait"]["exit_code"], 0)
        self.assertTrue(persistence["writer"]["result_eof"])
        directory = Path(persistence["directory"])
        for name, expected in persistence["files"].items():
            raw = (directory / name).read_bytes()
            self.assertEqual({"bytes": len(raw), "sha256": launcher.digest(raw)}, expected)
        outer = launcher.strict_json((directory / "outer.stdout.original").read_bytes(), launcher.OUTER_STDOUT_LIMIT)
        self.assertNotIn("UNRELATED_SECRET", outer["effective_environment"])
        self.assertNotIn("GITHUB_SHA", outer["effective_environment"])
        self.assertEqual(outer["effective_environment"]["HOME"], str(self.home))
        self.assertEqual((directory / "phase.original").read_bytes(), b'{"fixture":"owned-L-to-O-channel"}\n')
        self.assertFalse(result["formal_acceptance"])

    def test_foreign_working_plan_stops_before_outer_or_preparation_directories(self):
        manifest, template, environment = self.frozen_pair()
        foreign = copy.deepcopy(template)
        foreign["sources"]["bootstrap"]["sha256"] = "f" * 64
        self.write(manifest["plan"]["path"], launcher.canonical(foreign))
        code, stdout, _ = self.execute_launcher(environment)
        self.assertEqual(code, 124)
        result = launcher.strict_json(stdout, 4 * 1024 * 1024)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertNotIn("retained_outer", result)
        self.assertEqual(list(self.private.iterdir()), [])

    def observe_stall(self, function, label):
        started = time.monotonic_ns(); deadline = started + 40 * launcher.NS
        handle = launcher._spawn_worker(function, deadline)
        try:
            ready_deadline = time.monotonic() + 3
            while not handle.prefix and time.monotonic() < ready_deadline:
                launcher._drain_child(handle, 65536)
                time.sleep(.005)
            self.assertTrue(handle.prefix, "fixture did not reach its named causal stall")
            original = bytes(handle.prefix)
            advance = 39_500_000_000
            clock = lambda: time.monotonic_ns() + advance
            self.assertFalse(launcher.observe_worker(handle, deadline, limit=65536, clock=clock))
            self.assertEqual(handle.wait["exit_code"], -signal.SIGKILL)
            self.assertTrue(handle.eof); self.assertIsNotNone(handle.exited_ns)
            self.assertEqual(len(handle.signal_attempts), 1)
            self.assertTrue(bytes(handle.prefix).startswith(original))
            (self.root / (label + ".prefix.original")).write_bytes(bytes(handle.prefix))
            (self.root / (label + ".custody.json")).write_bytes(launcher.canonical({"scope": "SOURCE_FIXTURE_ONLY",
                "simulated_deadline_advance_ns": advance, "original_deadline_ns": deadline,
                "child": handle.metadata(), "no_budget_reset": True}))
        finally:
            if handle.wait is None:
                handle.kill_once(time.monotonic_ns())
                launcher.observe_worker(handle, deadline, limit=65536)
            handle.close()

    def test_stalled_git_preparation_remains_unknown_with_owned_wait_and_prefix(self):
        _, _, environment = self.frozen_pair()
        started = time.monotonic_ns()
        parent_pid = os.getpid()
        def stall(fd):
            def blocked_git(*_args, **_kwargs):
                os.write(fd, b"original-prefix-before-Git-read\n")
                time.sleep(60)
            launcher._git_bytes = blocked_git
            return launcher._prepare(self.repo, launcher._workflow_identity(environment),
                                     launcher.allowlisted_environment(environment), started, parent_pid)
        self.observe_stall(stall, "preparation-stall")
        self.assertEqual(list(self.private.iterdir()), [])

    def test_stalled_fsync_keeps_partial_original_and_does_not_block_watchdog(self):
        path = self.root / "partial-before-fsync.original"
        expected = b"exact-prefix-that-reached-the-owned-file"
        def stall(fd):
            def blocked_fsync(_descriptor):
                os.write(fd, b"original-prefix-before-fsync\n")
                time.sleep(60)
            launcher.os.fsync = blocked_fsync
            launcher._write_new(path, expected)
        self.observe_stall(stall, "fsync-stall")
        self.assertEqual(path.read_bytes(), expected)

    def test_stalled_final_output_does_not_extend_h_or_consume_existing_bytes(self):
        read, write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        expected = bytearray()
        try:
            while True:
                try:
                    count = os.write(write, b"original-filled-sink" * 200)
                    expected.extend((b"original-filled-sink" * 200)[:count])
                except BlockingIOError:
                    break
            started = time.monotonic_ns()
            deadline = started + 40 * launcher.NS
            result = {"status": "UNKNOWN", "deadlines": {"custody_ns": deadline},
                      "original_prefixes": {"held": base64.b64encode(b"preserve-this-prefix").decode()}}
            code = launcher.emit_result(result, write, clock=lambda: time.monotonic_ns() + 39_500_000_000)
            self.assertEqual(code, 124)
            actual = bytearray()
            while True:
                try:
                    raw = os.read(read, 65536)
                    if not raw: break
                    actual.extend(raw)
                except BlockingIOError:
                    break
            self.assertEqual(actual, expected)
            (self.root / "output-stall.prefill.original").write_bytes(bytes(actual))
            (self.root / "output-stall.original-clock.json").write_bytes(launcher.canonical({
                "started_ns": started, "custody_ns": deadline, "simulated_deadline_advance_ns": 39_500_000_000,
                "exit_code": code, "output_retried": False}))
        finally:
            os.close(read); os.close(write)


if __name__ == "__main__":
    unittest.main()
