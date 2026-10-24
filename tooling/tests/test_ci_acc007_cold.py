"""Causal source controls only. These tests never run bwrap, install wheels or ML."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("acc007_test_subject", ROOT / "tooling/ci_acc007_cold.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def original_command():
    return {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0,
            "owned_group_after_wait": "ABSENT", "log_custody": "VERIFIED_SNAPSHOT",
            "escaped_descendants": "NOT_OBSERVED"}


def frozen_context():
    return {"source_sha": "a" * 40, "workflow_ref": "seradotcom/motionwright/.github/workflows/performance-acceptance.yml@refs/heads/cold-candidate",
            "run_id": "101", "run_attempt": "1", "job": "waveform-lazy"}


def environment():
    return dict(zip(("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB"), frozen_context().values()))


def plan_fixture():
    return {"schema": "motionwright.acc007-cold-launch/2", "mode": "inference",
            "host_namespaces": {"pid": 1, "net": 2, "mnt": 3},
            "request_sha256": "b" * 64, "request_identity": [1, 2, 33152, 1, 15, 8, 9],
            "bwrap": "/usr/bin/bwrap", "read_only_mounts": ["/usr", "/frozen/profile", "/private/venv", "/private/seals"],
            "system_symlinks": {"/bin": "usr/bin"}, "rw_source": "/private/case", "attempt": "/private/case",
            "python": "/private/venv/bin/python3.12", "adapter": "/source/tooling/ci_acc007_cold.py",
            "plan_path": "/private/seals/inference.plan.json"}


def boundary_fixture(plan):
    return {"schema": "motionwright.acc007-cold-boundary/2", "plan_sha256": gate.digest(gate.encoded(plan)),
            "pid": 1, "status_fd_closed": True, "namespaces": {"pid": 101, "net": 102, "mnt": 103},
            "limits": {"as": [4294967296] * 2, "cpu": [180, 180], "fsize": [262144, 262144], "core": [0, 0]},
            "threads": gate.THREADS, "network_denied": True, "read_only_verified": True,
            "unexpected_fds": [], "capabilities": 0, "no_new_privs": 1,
            "request_sha256": plan["request_sha256"], "request_identity": plan["request_identity"]}


def status_fixture():
    return (gate.encoded({"child-pid": 9081, "pid-namespace": 101, "net-namespace": 102, "mnt-namespace": 103})
            + gate.encoded({"exit-code": 0}))


def freeze_fixture(root):
    current = frozen_context()
    result = {"schema": "motionwright.acc007-cold-freeze/2", "scope": gate.SCOPE,
            "source_sha": current["source_sha"], "workflow_ref": current["workflow_ref"],
            "authorization_record": {"reference": "synthetic/A1", "sha256": "1" * 64},
            "private_input_channel": {"reference": "synthetic/private-transfer", "sha256": "2" * 64},
            "recipient": {"public_key": {}, "custodian_record": "synthetic/custody-not-a-real-key"},
            "repo": str(root / "source"), "fixture_root": str(root / "fixture"), "fixture_archive": str(root / "fixture.zip"),
            "checkpoint": str(root / "checkpoint"), "wheelhouse": str(root / "wheels"), "execution_root": str(root / "case"),
            "source_files": {p: {"bytes": 1, "sha256": "3" * 64} for p in gate.SOURCE_FILES},
            "tools": {k: {"path": str(root / k), "bytes": 1, "sha256": "4" * 64} for k in ("python", "bwrap", "time", "git")}}
    result["tools"]["bwrap"].update(bytes=gate.BWRAP_BINARY[0], sha256=gate.BWRAP_BINARY[1])
    return result


class ColdSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_valid_context_and_closed_freeze(self):
        current = gate.context(environment())
        self.assertEqual(current, frozen_context())
        self.assertEqual(gate.validate_freeze(freeze_fixture(self.root), current)["scope"], gate.SCOPE)

    def test_rerun_attempt_is_refused(self):
        env = environment(); env["GITHUB_RUN_ATTEMPT"] = "2"
        with self.assertRaisesRegex(gate.Rejected, "no_replayed_run"):
            gate.context(env)

    def test_wrong_job_and_source_are_refused(self):
        for key, value in (("GITHUB_JOB", "native-production"), ("GITHUB_SHA", "main")):
            env = environment(); env[key] = value
            with self.assertRaises(gate.Rejected):
                gate.context(env)
        value = freeze_fixture(self.root); value["source_sha"] = "c" * 40
        with self.assertRaisesRegex(gate.Rejected, "freeze_source_run_binding"):
            gate.validate_freeze(value, frozen_context())

    def test_missing_private_transfer_is_not_authorized_by_key(self):
        value = freeze_fixture(self.root); value["private_input_channel"] = {}
        with self.assertRaisesRegex(gate.Rejected, "external_record_required"):
            gate.validate_freeze(value, frozen_context())

    def test_missing_custodian_record_is_rejected(self):
        value = freeze_fixture(self.root); value["recipient"]["custodian_record"] = ""
        with self.assertRaisesRegex(gate.Rejected, "real_custodian_record_required"):
            gate.validate_freeze(value, frozen_context())

    def test_source_roster_cannot_omit_supervisor(self):
        value = freeze_fixture(self.root); del value["source_files"]["tooling/ci_storage_apply_recovery.py"]
        with self.assertRaisesRegex(gate.Rejected, "source_roster"):
            gate.validate_freeze(value, frozen_context())

    def test_unreviewed_bwrap_binary_is_not_accepted_by_version_name(self):
        value = freeze_fixture(self.root); value["tools"]["bwrap"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(gate.Rejected, "reviewed_bwrap_binary_required"):
            gate.validate_freeze(value, frozen_context())

    def test_authorized_digest_mismatch_precedes_gate_effects(self):
        path = self.root / "freeze.json"; path.write_bytes(gate.encoded(freeze_fixture(self.root)))
        with mock.patch.object(gate, "Gate") as constructor, mock.patch.dict(os.environ, environment(), clear=True):
            self.assertEqual(gate.main(["--freeze", str(path), "--authorized-freeze-sha256", "0" * 64]), 78)
            constructor.assert_not_called()

    def test_json_duplicate_and_nan_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(gate.Rejected):
                gate.strict(raw)

    def test_physical_preserves_exact_newline_and_inode(self):
        path = self.root / "request.json"; path.write_bytes(b'{"a":1}')
        observation, raw = gate.physical(path, keep=True)
        self.assertEqual(raw, b'{"a":1}')
        self.assertEqual(observation["identity"][1], path.stat().st_ino)
        self.assertNotEqual(observation["sha256"], gate.digest(raw + b"\n"))

    def test_physical_symlink_and_hardlink_are_rejected(self):
        path = self.root / "original"; path.write_bytes(b"original")
        linked = self.root / "linked"; linked.symlink_to(path)
        with self.assertRaises(gate.Rejected):
            gate.physical(linked)
        hard = self.root / "hard"; os.link(path, hard)
        with self.assertRaisesRegex(gate.Rejected, "single_link"):
            gate.physical(path)

    def test_replacement_during_physical_read_is_rejected(self):
        path = self.root / "original"; path.write_bytes(b"original")
        real_read = os.read
        changed = False
        def replacing(fd, size):
            nonlocal changed
            raw = real_read(fd, size)
            if not changed:
                changed = True
                replacement = self.root / "other"; replacement.write_bytes(b"original")
                replacement.replace(path)
            return raw
        with mock.patch.object(gate.os, "read", side_effect=replacing):
            with self.assertRaisesRegex(gate.Rejected, "changed"):
                gate.physical(path)

    def test_exclusive_write_never_replaces_original(self):
        path = self.root / "original"; gate.new_bytes(path, b"first")
        with self.assertRaises(FileExistsError):
            gate.new_bytes(path, b"second")
        self.assertEqual(path.read_bytes(), b"first")

    def test_boolean_pin_size_is_rejected(self):
        path = self.root / "x"; path.write_bytes(b"x")
        with self.assertRaisesRegex(gate.Rejected, "pin_fields"):
            gate.pinned(path, {"bytes": True, "sha256": gate.digest(b"x")})

    def test_plain_group_absence_does_not_prove_namespace(self):
        plan = plan_fixture()
        with self.assertRaises(gate.Rejected):
            gate.terminal_proof(original_command(), b"", boundary_fixture(plan), plan, child_absent=True)

    def test_complete_namespace_proof_keeps_original_not_observed(self):
        plan = plan_fixture(); command = original_command(); before = copy.deepcopy(command)
        result = gate.terminal_proof(command, status_fixture(), boundary_fixture(plan), plan, child_absent=True)
        self.assertEqual(result["status"], "PID_NAMESPACE_INIT_TERMINATION_OBSERVED")
        self.assertEqual(command, before)
        self.assertEqual(result["original_process_escaped_descendants"], "NOT_OBSERVED")

    def test_unknown_is_terminal_even_with_complete_namespace_data(self):
        plan = plan_fixture(); command = original_command(); command["observation"] = "UNKNOWN_TIMEOUT_NO_RETRY"
        command["exit_code"] = None
        with self.assertRaises(gate.Uncertain):
            gate.terminal_proof(command, status_fixture(), boundary_fixture(plan), plan, child_absent=True)

    def test_boolean_exit_is_not_zero(self):
        command = original_command(); command["exit_code"] = False
        with self.assertRaises(gate.Rejected):
            gate.process_success(command)

    def test_child_still_present_is_rejected(self):
        plan = plan_fixture()
        with self.assertRaisesRegex(gate.Rejected, "still_present"):
            gate.terminal_proof(original_command(), status_fixture(), boundary_fixture(plan), plan, child_absent=False)

    def test_status_fd_leak_and_readonly_loss_are_rejected(self):
        plan = plan_fixture()
        for key in ("status_fd_closed", "read_only_verified", "network_denied"):
            boundary = boundary_fixture(plan); boundary[key] = False
            with self.assertRaises(gate.Rejected):
                gate.terminal_proof(original_command(), status_fixture(), boundary, plan, child_absent=True)

    def test_host_namespace_and_request_hash_cannot_be_reused(self):
        plan = plan_fixture()
        for mutate in (lambda b: b["namespaces"].update(pid=1), lambda b: b.update(request_sha256="c" * 64),
                       lambda b: b.update(plan_sha256="d" * 64)):
            boundary = boundary_fixture(plan); mutate(boundary)
            with self.assertRaises(gate.Rejected):
                gate.terminal_proof(original_command(), status_fixture(), boundary, plan, child_absent=True)

    def test_boolean_limits_are_not_observed_integers(self):
        plan = plan_fixture(); boundary = boundary_fixture(plan); boundary["limits"]["core"] = [False, False]
        with self.assertRaisesRegex(gate.Rejected, "limit_boolean"):
            gate.terminal_proof(original_command(), status_fixture(), boundary, plan, child_absent=True)

    def test_bwrap_status_requires_original_start_end_once(self):
        for raw in (gate.encoded({"exit-code": 0}), status_fixture() + gate.encoded({"exit-code": 0}),
                    status_fixture().replace(b'"exit-code":0', b'"exit-code":false')):
            with self.assertRaises(gate.Rejected):
                gate.status_documents(raw)

    def test_future_bwrap_optional_fields_do_not_erase_required_fields(self):
        rows = status_fixture().splitlines(); start = json.loads(rows[0]); start["future-field"] = 999
        raw = gate.encoded({"future-object": True}) + gate.encoded(start) + rows[1] + b"\n"
        self.assertEqual(gate.status_documents(raw)[0]["child-pid"], 9081)

    def test_namespaces_root_ro_and_single_worker_rw_are_in_command(self):
        plan = plan_fixture(); argv = gate.sandbox_argv(plan, 7, 8)
        for flag in ("--unshare-pid", "--as-pid-1", "--unshare-net", "--unshare-user", "--disable-userns", "--die-with-parent"):
            self.assertIn(flag, argv)
        self.assertEqual(argv.count("--bind"), 1)
        split = argv.index("--")
        self.assertIn(["--remount-ro", "/"], [argv[i:i + 2] for i in range(split)])
        self.assertEqual(argv[split + 1], plan["python"])
        self.assertNotEqual(argv[split + 1], "/usr/bin/python3.12")
        self.assertIn("-S", argv[split + 1:])

    def test_launcher_descriptor_boolean_or_collision_is_rejected(self):
        for args in ((True, 8), (7, 7), (1, 8)):
            with self.assertRaises(gate.Rejected):
                gate.sandbox_argv(plan_fixture(), *args)

    def test_changed_plan_is_rejected_before_exec_adapter(self):
        path = self.root / "plan.json"; raw = gate.encoded(plan_fixture()); path.write_bytes(raw)
        self.assertEqual(gate.read_plan(path, gate.digest(raw))["mode"], "inference")
        value = plan_fixture(); value["mode"] = "control"; path.write_bytes(gate.encoded(value))
        with mock.patch.object(gate.os, "execve") as execute:
            with self.assertRaisesRegex(gate.Rejected, "plan_preseal_differs"):
                gate.launch(path, gate.digest(raw))
            execute.assert_not_called()

    def resource_original(self):
        return (b'\tUser time (seconds): 2.20\n\tSystem time (seconds): 0.30\n'
                b'\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:03.40\n'
                b'\tMaximum resident set size (kbytes): 1048576\n\tExit status: 0\n')

    def test_resource_original_positive(self):
        result = gate.resource_values(self.resource_original())
        self.assertEqual(result["cpu_seconds"], 2.5)
        self.assertEqual(result["wall_seconds"], 3.4)
        self.assertFalse(result["aggregate_memory_limit_claimed"])

    def test_resource_missing_duplicate_or_over_budget_is_rejected(self):
        raw = self.resource_original()
        for bad in (raw.replace(b'Exit status: 0', b'Exit status: 1'), raw + b'Exit status: 0\n',
                    raw.replace(b'0:03.40', b'3:00.01'), raw.replace(b'1048576', b'4194305'),
                    raw.replace(b'2.20', b'nan')):
            with self.assertRaises(gate.Rejected):
                gate.resource_values(bad)

    def make_gate(self, process=None, custody=None):
        value = freeze_fixture(self.root)
        return gate.Gate(value, gate.encoded(value), frozen_context(), process_engine=process, custody_engine=custody)

    def test_unknown_stops_all_subsequent_processes(self):
        engine = types.SimpleNamespace(UnknownObservation=RuntimeError, execution_unknown=lambda r: True,
                                       execute=mock.Mock(return_value={"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None}))
        subject = self.make_gate(engine)
        with self.assertRaises(gate.Uncertain):
            subject.execute(["synthetic"], "first")
        with self.assertRaisesRegex(gate.Rejected, "no_command_after_unknown"):
            subject.execute(["must-not-run"], "second")
        self.assertEqual(engine.execute.call_count, 1)

    def test_incomplete_stream_custody_cannot_be_success(self):
        command = original_command(); command["log_custody"] = "INCOMPLETE"
        with self.assertRaises(gate.Rejected):
            gate.process_success(command)

    def test_exact_post_source_change_prevents_success(self):
        subject = self.make_gate(); subject.seals.mkdir(parents=True)
        source = self.root / "source-file"; source.write_bytes(b"before")
        subject.before = {"one": gate.physical(source)}
        source.write_bytes(b"after!")
        with self.assertRaisesRegex(gate.Rejected, "source_or_input_changed"):
            subject.after()
        with self.assertRaisesRegex(gate.Rejected, "not_retried"):
            subject.after()

    def test_private_retention_excludes_model_and_runtime_trees(self):
        custody = types.SimpleNamespace(gather_snapshot=mock.Mock(return_value=(b"originals", True)),
                                        encrypt_snapshot=mock.Mock(return_value=b"sealed"),
                                        new_bytes=mock.Mock())
        subject = self.make_gate(custody=custody); subject.payload.mkdir(parents=True)
        subject.after_started = True; subject.sem_sha = "f" * 40
        subject.freeze["recipient"]["public_key"] = {"sha256": "9" * 64}
        subject.result["status"] = "UNKNOWN_NO_RETRY"
        result = subject.retain()
        roots = custody.gather_snapshot.call_args.args[0]
        self.assertEqual(roots, {"attempt": subject.payload})
        self.assertFalse(result["model_and_wheels_embedded"])
        self.assertEqual(result["status"], "UNKNOWN_NO_RETRY")
        self.assertTrue(custody.gather_snapshot.call_args.kwargs["preserve_partial"])

    def test_partial_private_custody_cannot_publish_success(self):
        custody = types.SimpleNamespace(gather_snapshot=mock.Mock(return_value=(b"partial", False)),
                                        encrypt_snapshot=mock.Mock(return_value=b"sealed"), new_bytes=mock.Mock())
        subject = self.make_gate(custody=custody); subject.payload.mkdir(parents=True)
        subject.after_started = True; subject.sem_sha = "f" * 40
        subject.freeze["recipient"]["public_key"] = {"sha256": "9" * 64}
        subject.result["status"] = "TECHNICAL_COLD_FIXTURE_VERIFIED"
        self.assertEqual(subject.retain()["status"], "FAIL_INCOMPLETE_CUSTODY")

    def test_partial_custody_preserves_unknown_in_public_receipt(self):
        custody = types.SimpleNamespace(gather_snapshot=mock.Mock(return_value=(b"partial", False)),
                                        encrypt_snapshot=mock.Mock(return_value=b"sealed"), new_bytes=mock.Mock())
        subject = self.make_gate(custody=custody); subject.payload.mkdir(parents=True)
        subject.after_started = True; subject.sem_sha = "f" * 40
        subject.freeze["recipient"]["public_key"] = {"sha256": "9" * 64}
        subject.result["status"] = "UNKNOWN_NO_RETRY"
        result = subject.retain()
        self.assertEqual(result["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(result["execution_status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(result["custody_status"], "INCOMPLETE")

    def test_source_lock_pin_is_bound_before_output_namespace(self):
        source = self.root / "source"; source.mkdir()
        path = source / "SOURCE_LOCK.json"
        path.write_bytes(gate.encoded({"dependencies": {"semwright": {"revision": "a" * 40}}}))
        self.assertEqual(gate.source_lock_pin(source), "a" * 40)
        path.write_bytes(gate.encoded({"dependencies": {"semwright": {"revision": True}}}))
        with self.assertRaisesRegex(gate.Rejected, "source_lock_semwright_pin"):
            gate.source_lock_pin(source)
        self.assertFalse((self.root / "case").exists())

    def test_frozen_roster_retains_model_separately_and_no_archive_limit_increase(self):
        self.assertEqual(len(gate.FIXTURE_PINS), 23)
        self.assertEqual(gate.MODEL[0], 377664473)
        self.assertFalse(any(name.endswith('.pth') or name.endswith('.whl') for name in gate.FIXTURE_PINS))
        self.assertEqual(len(gate.PRIMITIVE_PINS), 3)

    def test_valid_synthetic_run_reaches_scoped_result(self):
        subject = self.make_gate(); subject.prepare = mock.Mock()
        subject.fixture = self.root / "fixture"
        subject.request = {"sha256": "e" * 64}; subject.construction = {"sha256": "f" * 64}
        row = {"stdout": {"path": "original.stdout"}, "stderr": {"path": "original.stderr"}}
        subject.namespaced = mock.Mock(return_value=row)
        subject.execute = mock.Mock(return_value=original_command())
        subject.original = mock.Mock(return_value=gate.encoded({"status": "CONSISTENCY_VALID", "backend_authority": False,
                                                               "request_sha256": "e" * 64, "unresolved_word_count": 0}))
        subject.after = mock.Mock()
        result = subject.run()
        self.assertEqual(result["status"], "TECHNICAL_COLD_FIXTURE_VERIFIED")
        self.assertEqual(subject.namespaced.call_args_list, [mock.call("control"), mock.call("inference")])
        self.assertFalse(result["SDK_authority"])
        self.assertFalse(result["human_acceptance"])

    def test_physical_control_failure_blocks_inference(self):
        subject = self.make_gate(); subject.prepare = mock.Mock()
        subject.namespaced = mock.Mock(side_effect=gate.Rejected("read_only_not_enforced"))
        subject.execute = mock.Mock()
        with self.assertRaises(gate.Rejected):
            subject.run()
        self.assertEqual(subject.namespaced.call_args_list, [mock.call("control")])
        subject.execute.assert_not_called()

    def test_workflow_has_four_default_jobs_g0_and_no_unadmitted_inference(self):
        text = (ROOT / ".github/workflows/performance-acceptance.yml").read_text()
        self.assertEqual(re.findall(r"^  ([a-z][a-z0-9-]+):$", text.split("jobs:\n", 1)[1], re.M),
                         ["revision-ten", "waveform-lazy", "responsiveness-service", "ui-selection"])
        self.assertIn("--pattern test_ci_acc007_cold.py --mode both --require-complete", text)
        self.assertIn("--expected-source", text)
        self.assertNotIn("ci_acc007_cold.py --freeze", text)
        for command in ("waveform_bench --release", "acceptance_bench --release", "Maximum resident set size"):
            self.assertIn(command, text)


class ColdMountClosureTests(unittest.TestCase):
    """Source/argv controls; no namespace, bwrap, wheel install or ML execution."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="c-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def mounts(self, **changes):
        inputs = dict(base_prefix=Path("/opt/python/3.12/x64"), venv=Path("/private/runtime"),
                      fixture=Path("/intake/fixture"), checkpoint=Path("/intake/model.pth"),
                      seals=Path("/private/seals"), adapter=Path("/source/tooling/ci_acc007_cold.py"))
        inputs.update(changes)
        return gate.closed_read_only_mounts(**inputs)

    def test_allowed_layout_exports_exact_24_files_without_fixture_parent(self):
        mounts = self.mounts()
        expected = {"/intake/fixture/" + name for name in {*gate.FIXTURE_PINS, "MANIFEST.json"}}
        self.assertEqual(len(expected), 24)
        self.assertEqual({p for p in mounts if p.startswith("/intake/fixture/")}, expected)
        self.assertNotIn("/intake/fixture", mounts)
        self.assertNotIn("/intake", mounts)
        self.assertNotIn("/etc", mounts)
        self.assertIn("/private/runtime", mounts)
        self.assertIn("/private/seals", mounts)
        self.assertIn("/opt/python/3.12/x64", mounts)
        self.assertEqual(len(mounts), len(set(mounts)))

    def test_actual_plan_cannot_export_extra_socket_or_credential(self):
        fixture = self.root / "f"
        fixture.mkdir()
        for name in {*gate.FIXTURE_PINS, "MANIFEST.json"}:
            path = fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic nominal source; not real fixture admission")
        secret = fixture / "unrelated-credential"
        secret.write_bytes(b"synthetic credential canary")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        ambient = fixture / ".s"
        sock.bind(str(ambient))  # Local inert inode only; no listen/connect.
        value = freeze_fixture(self.root)
        value["fixture_root"] = str(fixture)
        subject = gate.Gate(value, gate.encoded(value), frozen_context())
        subject.fixture = fixture
        subject.base_prefix = Path("/opt/python/3.12/x64")
        subject.seals.mkdir(parents=True)
        request = subject.seals / "request.json"
        request.write_bytes(b"{}\n")
        subject.request = gate.physical(request)
        with mock.patch.object(gate, "namespace_ids", return_value={"pid": 1, "net": 2, "mnt": 3}):
            plan = subject.plan("control")
        argv = gate.sandbox_argv(plan, 7, 8)
        exported = {Path(argv[i + 1]) for i, value in enumerate(argv) if value == "--ro-bind"}
        for extra in (secret, ambient):
            self.assertTrue(extra.exists())
            self.assertFalse(any(source == extra or source in extra.parents for source in exported))
        self.assertEqual({p.relative_to(fixture).as_posix() for p in exported if fixture in p.parents},
                         {*gate.FIXTURE_PINS, "MANIFEST.json"})
        self.assertEqual(plan["read_only_checks"], [str(subject.venv), str(fixture),
                         value["checkpoint"], str(subject.seals), str(subject.source / "tooling/ci_acc007_cold.py")])

    def test_root_home_state_and_noncanonical_runtime_bases_are_rejected(self):
        for base in ("/", "/home", "/home/runner", "/root", "/run", "/var", "/tmp", "/etc", "/opt"):
            with self.subTest(base=base), self.assertRaisesRegex(gate.Rejected, "runtime_base_prefix_scope"):
                self.mounts(base_prefix=Path(base))
        with self.assertRaisesRegex(gate.Rejected, "mount_path_not_absolute"):
            self.mounts(base_prefix=Path("/opt/python/../credentials"))

    def test_system_ancestor_cannot_reexpose_private_inputs(self):
        for key in ("venv", "fixture", "checkpoint", "seals", "adapter"):
            for parent in ("/usr", "/opt/python/3.12/x64"):
                with self.subTest(key=key, parent=parent), self.assertRaisesRegex(gate.Rejected, "system_mount_overlaps_private_input"):
                    self.mounts(**{key: Path(parent) / "unrelated-private-input"})
        with self.assertRaisesRegex(gate.Rejected, "system_mount_overlaps_private_input"):
            self.mounts(fixture=Path("/opt"))

    def test_owned_directory_cannot_reexpose_nominal_fixture_parent(self):
        for key in ("venv", "seals"):
            with self.subTest(key=key), self.assertRaisesRegex(gate.Rejected, "owned_mount_overlaps_frozen_input"):
                self.mounts(**{key: Path("/intake")})

    def test_only_explicit_loader_cache_can_enter_empty_etc(self):
        for cache in ("/etc", "/etc/environment", "/etc/ld.so.preload", "/run/agent.sock"):
            with self.subTest(cache=cache), self.assertRaisesRegex(gate.Rejected, "unreviewed_system_configuration"):
                self.mounts(loader_cache=cache)
        for cache in (None, "/etc/ld.so.cache"):
            plan = plan_fixture()
            plan["read_only_mounts"] = self.mounts(loader_cache=cache)
            argv = gate.sandbox_argv(plan, 7, 8)
            self.assertIn(["--dir", "/etc"], [argv[i:i + 2] for i in range(argv.index("--"))])
            sources = {argv[i + 1] for i, value in enumerate(argv) if value == "--ro-bind"}
            self.assertNotIn("/etc", sources)
            self.assertEqual({p for p in sources if p.startswith("/etc/")}, {cache} if cache else set())

    def test_legacy_system_directories_are_closed_and_cannot_enclose_inputs(self):
        self.assertIn("/lib", self.mounts(legacy_dirs=("/lib",)))
        with self.assertRaisesRegex(gate.Rejected, "unreviewed_system_mount"):
            self.mounts(legacy_dirs=("/run",))
        with self.assertRaisesRegex(gate.Rejected, "system_mount_overlaps_private_input"):
            self.mounts(legacy_dirs=("/lib",), fixture=Path("/lib/private-fixture"))


if __name__ == "__main__":
    unittest.main()
