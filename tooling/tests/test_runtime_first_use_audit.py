"""Adversarial, offline first-use checks. No Broker, network or native render."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runtime_first_use_audit as audit  # noqa: E402
from test_runtime_first_use_atomic_receipt import AtomicSupportReceiptTests  # noqa: E402,F401


class FirstUseAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="motionwright-first-use-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = str(uuid.uuid4())
        self.generation = str(uuid.uuid4())
        self.cli = self.root / "semwright-cli.bin"
        self.cli.write_bytes(b"dummy owner provisioned executable / test only")
        self.cli.chmod(0o700)
        self.socket = self.root / "broker.socket"
        self.socket.write_bytes(b"not a live socket; test fixture")
        self.session = self.root / "owner-session.json"
        self.session.write_text('{"private":"never-export-this-token"}', encoding="utf8")
        self.session.chmod(0o600)
        self.output = self.root / "output"
        self.output.mkdir(mode=0o700)
        self.connection = self.root / "owner.json"
        self.record = {
            "schema": audit.CONNECTION_SCHEMA,
            "executable": str(self.cli),
            "executable_sha256": hashlib.sha256(self.cli.read_bytes()).hexdigest(),
            "socket": str(self.socket),
            "session_file": str(self.session),
            "output_root": str(self.output),
            "resource": "project:" + self.project,
        }
        self.write_connection()
        self.registry = self.root / "owner-native-connections"
        self.registry.mkdir(mode=0o700)
        self.saved = self.registry / (uuid.UUID(self.project).hex + ".json")
        self.save_reference()

    def write_connection(self):
        self.connection.write_text(json.dumps(self.record), encoding="utf8")
        self.connection.chmod(0o600)

    def save_reference(self, **update):
        reference = {
            "schema_version": 1,
            "project_id": self.project,
            "generation": self.generation,
            "connection_file": str(self.connection),
            **update,
        }
        self.saved.write_text(json.dumps(reference), encoding="utf8")
        self.saved.chmod(0o600)

    def inspect(self, **options):
        return audit.inspect_connection(
            project_id=options.pop("project_id", self.project),
            generation=options.pop("generation", self.generation),
            connection_file=options.pop("connection_file", self.connection),
            **options,
        )

    def fail_with(self, code, **options):
        with self.assertRaises(audit.AuditError) as context:
            self.inspect(**options)
        self.assertEqual(context.exception.code, code)

    def test_explicit_preflight_is_read_only_and_never_claims_broker_ready(self):
        originals = (self.connection.read_bytes(), self.session.read_bytes())
        report = self.inspect()
        self.assertEqual(report["schema"], audit.REPORT_SCHEMA)
        self.assertEqual(report["status"], "FILES_VERIFIED_ONLY")
        self.assertIs(report["checks"]["cli_version_checked"], False)
        self.assertIs(report["claims"]["release_ready"], False)
        self.assertEqual(report["claims"]["render_success"], "NOT_RUN")
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertNotIn("never-export-this-token", json.dumps(report))
        self.assertEqual(originals, (self.connection.read_bytes(), self.session.read_bytes()))

    def test_version_probe_only_with_explicit_opt_in(self):
        observations = []
        def fake_probe(path):
            observations.append(path)
            return "1.0.0"
        output = self.inspect(probe_version=True, version_probe=fake_probe)
        self.assertEqual(observations, [self.cli])
        self.assertEqual(output["status"], "CLI_VERSION_COMPATIBLE")
        self.assertEqual(output["semwright_observed_version"], "1.0.0")
        self.assertEqual(output["claims"]["broker_ready"], "NOT_TESTED")

    def test_mismatched_version_fails_closed(self):
        self.fail_with("cli_version_incompatible", probe_version=True,
                       version_probe=lambda _: "0.9.0")

    def test_invalid_project_uuid_rejected(self):
        self.fail_with("scope_invalid", project_id="not-a-uuid")

    def test_noncanonical_generation_rejected(self):
        self.fail_with("scope_invalid", generation=self.generation.upper())

    def test_connection_resource_mismatch_rejected(self):
        self.record["resource"] = "project:" + str(uuid.uuid4())
        self.write_connection()
        self.fail_with("resource_mismatch")

    def test_schema_mismatch_rejected(self):
        self.record["schema"] = "motionwright-semwright-connection/0"
        self.write_connection()
        self.fail_with("connection_invalid")

    def test_unknown_connection_field_rejected(self):
        self.record["hidden_session"] = "trick"
        self.write_connection()
        self.fail_with("connection_invalid")

    def test_missing_connection_field_rejected(self):
        del self.record["output_root"]
        self.write_connection()
        self.fail_with("connection_invalid")

    def test_duplicate_json_keys_rejected_instead_of_last_one_winning(self):
        self.connection.write_text('{"schema":"valid","schema":"evil"}', encoding="utf8")
        self.connection.chmod(0o600)
        self.fail_with("duplicate_json_field")

    def test_invalid_expected_digest_rejected(self):
        self.record["executable_sha256"] = "A" * 64
        self.write_connection()
        self.fail_with("digest_invalid")

    def test_modified_executable_rejected_by_streamed_digest(self):
        self.cli.write_bytes(b"changed without owner approval")
        self.fail_with("executable_digest_mismatch")

    def test_missing_executable_is_fail_closed(self):
        self.record["executable"] = str(self.root / "missing.exe")
        self.write_connection()
        self.fail_with("executable_invalid")

    def test_relative_connection_path_rejected(self):
        self.fail_with("connection_invalid", connection_file=Path("relative.json"))

    def test_relative_session_path_rejected(self):
        self.record["session_file"] = "relative-session.json"
        self.write_connection()
        self.fail_with("session_invalid")

    def test_missing_socket_rejected(self):
        self.record["socket"] = str(self.root / "unavailable.socket")
        self.write_connection()
        self.fail_with("socket_unavailable")

    def test_missing_output_root_rejected(self):
        self.record["output_root"] = str(self.root / "absent")
        self.write_connection()
        self.fail_with("output_root_invalid")

    def test_oversized_session_rejected(self):
        self.session.write_bytes(b"X" * (audit.MAX_JSON + 1))
        self.fail_with("session_invalid")

    @unittest.skipUnless(os.name == "posix", "Unix owner-only mode")
    def test_world_readable_session_rejected(self):
        self.session.chmod(0o644)
        self.fail_with("session_not_private")

    @unittest.skipUnless(os.name == "posix", "Unix directory permission gate")
    def test_world_writable_output_root_rejected(self):
        self.output.chmod(0o777)
        self.fail_with("unsafe_file_permissions")

    def test_symlink_connection_rejected(self):
        alias = self.root / "alias.json"
        try:
            alias.symlink_to(self.connection)
        except (OSError, NotImplementedError):
            self.skipTest("OS policy denies unprivileged symlink creation")
        self.fail_with("connection_invalid", connection_file=alias)

    def test_symlink_ancestor_rejected(self):
        alias = self.root / "alias-directory"
        try:
            alias.symlink_to(self.output, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS policy denies unprivileged directory symlinks")
        self.record["output_root"] = str(alias)
        self.write_connection()
        self.fail_with("output_root_invalid")


    def test_saved_registry_reference_passes_scope_validation(self):
        result = self.inspect(connection_file=None, registry_root=self.registry)
        self.assertEqual(result["connection_origin"], "saved_reference")
        self.assertTrue(result["checks"]["registry_generation"])

    def test_old_generation_registry_rejected_without_env_fallback(self):
        self.save_reference(generation=str(uuid.uuid4()))
        self.fail_with("registry_scope_mismatch",
                       connection_file=None, registry_root=self.registry)

    def test_other_project_registry_rejected(self):
        self.save_reference(project_id=str(uuid.uuid4()))
        self.fail_with("registry_scope_mismatch",
                       connection_file=None, registry_root=self.registry)

    def test_registry_version_cannot_silently_upgrade(self):
        self.save_reference(schema_version=999)
        self.fail_with("registry_invalid",
                       connection_file=None, registry_root=self.registry)

    def test_registry_unknown_fields_rejected(self):
        self.save_reference(session_bytes="must-never-be-here")
        self.fail_with("registry_invalid",
                       connection_file=None, registry_root=self.registry)

    def test_saved_relative_file_reference_rejected(self):
        self.save_reference(connection_file="../owner.json")
        self.fail_with("connection_invalid",
                       connection_file=None, registry_root=self.registry)

    def test_saved_reference_does_not_replace_a_missing_owner_file(self):
        self.save_reference(connection_file=str(self.root / "unprovisioned.json"))
        self.fail_with("connection_invalid",
                       connection_file=None, registry_root=self.registry)

    def test_mutually_exclusive_selection_modes_required(self):
        self.fail_with("selection_ambiguous",
                       connection_file=None, registry_root=None)
        self.fail_with("selection_ambiguous", registry_root=self.registry)

    def test_cli_prints_sanitized_json_and_never_session_or_paths(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = audit.main([
                "--connection-file", str(self.connection),
                "--project-id", self.project,
                "--generation", self.generation,
            ])
        self.assertEqual(result, 0)
        receipt = json.loads(stream.getvalue())
        self.assertEqual(receipt["status"], "FILES_VERIFIED_ONLY")
        self.assertNotIn("never-export-this-token", stream.getvalue())
        self.assertNotIn(str(self.root), stream.getvalue())
        self.assertNotIn(self.project, stream.getvalue())

    def test_cli_failure_returns_code_not_owner_path(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = audit.main([
                "--connection-file", str(self.root / "absent.json"),
                "--project-id", self.project, "--generation", self.generation,
            ])
        self.assertEqual(result, 2)
        receipt = json.loads(stream.getvalue())
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["failure_code"], "connection_invalid")
        self.assertNotIn(str(self.root), stream.getvalue())

    def test_receipt_is_exclusive_and_existing_bytes_are_not_replaced(self):
        receipt_path = self.root / "first-use.json"
        report = self.inspect()
        audit.write_new_receipt(receipt_path, report)
        original = receipt_path.read_bytes()
        with self.assertRaises(audit.AuditError) as failure:
            audit.write_new_receipt(receipt_path, {"unexpected": "second"})
        self.assertEqual(failure.exception.code, "receipt_write_refused")
        self.assertEqual(receipt_path.read_bytes(), original)
        self.assertNotIn("never-export-this-token", original.decode("utf8"))

    def test_receipt_destination_must_be_absolute(self):
        with self.assertRaises(audit.AuditError) as failure:
            audit.write_new_receipt(Path("relative.json"), self.inspect())
        self.assertEqual(failure.exception.code, "receipt_destination_invalid")

    def substitute_hardlink_at_open(self, target, replacement, callback):
        """Replace the checked pathname just before the product opens it.

        A real hardlink avoids requiring privileged symlink creation on
        Windows; original and substituted files have distinct inode IDs.
        Both remain under this test's disposable directory.
        """
        original_open = audit.os.open
        prior = self.root / (target.name + ".original-inode")
        self.assertNotEqual(target.stat().st_ino, replacement.stat().st_ino)
        swapped = False

        def hijacked_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(path) == target:
                swapped = True
                os.replace(target, prior)
                os.link(replacement, target)
            return original_open(path, flags, *args, **kwargs)

        try:
            with patch.object(audit.os, "open", side_effect=hijacked_open):
                callback()
        finally:
            self.assertTrue(swapped, "the original product open must be exercised")

    def test_connection_inode_swap_after_path_preflight_fails_closed(self):
        """A different valid file cannot replace the checked connection."""
        alt = self.root / "different-owner.json"
        alt.write_bytes(self.connection.read_bytes())
        alt.chmod(0o600)

        with self.assertRaises(audit.AuditError) as failure:
            self.substitute_hardlink_at_open(self.connection, alt, self.inspect)
        self.assertEqual(failure.exception.code, "connection_invalid")

    def test_executable_inode_swap_after_path_preflight_fails_closed(self):
        """An identity substitution must be rejected despite equal digest."""
        alt = self.root / "different-cli.bin"
        alt.write_bytes(self.cli.read_bytes())
        alt.chmod(0o700)

        with self.assertRaises(audit.AuditError) as failure:
            self.substitute_hardlink_at_open(self.cli, alt, self.inspect)
        self.assertEqual(failure.exception.code, "executable_changed")

    def test_registry_reference_inode_swap_after_preflight_fails_closed(self):
        """Scope-valid bytes on another inode do not inherit checked status."""
        alt = self.root / "different-reference.json"
        alt.write_bytes(self.saved.read_bytes())
        alt.chmod(0o600)

        with self.assertRaises(audit.AuditError) as failure:
            self.substitute_hardlink_at_open(
                self.saved, alt,
                lambda: self.inspect(connection_file=None, registry_root=self.registry),
            )
        self.assertEqual(failure.exception.code, "registry_invalid")

    def substitute_after_checked_open(self, target, alternate, callback):
        """Mutate only synthetic fixture paths after the descriptor opens."""
        original = audit.open_checked_regular
        former = self.root / (target.name + ".late-original")
        invoked = False

        def late_swap(path, *, code, changed_code=None):
            nonlocal invoked
            source = original(path, code=code, changed_code=changed_code)
            if not invoked and Path(path) == target:
                invoked = True
                os.replace(target, former)
                os.link(alternate, target)
            return source

        try:
            with patch.object(audit, "open_checked_regular", side_effect=late_swap):
                callback()
        finally:
            self.assertTrue(invoked, "late replacement must exercise the opened file")

    @unittest.skipIf(os.name == "nt", "Windows held file denies rename/delete sharing")
    def test_connection_file_swapped_after_descriptor_open_is_rejected(self):
        alternate = self.root / "late-valid-connection.json"
        alternate.write_bytes(self.connection.read_bytes())
        alternate.chmod(0o600)
        with self.assertRaises(audit.AuditError) as failure:
            self.substitute_after_checked_open(self.connection, alternate, self.inspect)
        self.assertEqual(failure.exception.code, "connection_invalid")

    @unittest.skipIf(os.name == "nt", "Windows held file denies rename/delete sharing")
    def test_session_file_swapped_after_descriptor_open_is_rejected(self):
        alternate = self.root / "late-owner-session.json"
        alternate.write_bytes(self.session.read_bytes())
        alternate.chmod(0o600)
        with self.assertRaises(audit.AuditError) as failure:
            self.substitute_after_checked_open(self.session, alternate, self.inspect)
        self.assertEqual(failure.exception.code, "session_invalid")

    def test_real_subprocess_arguments_are_readonly_and_secret_free(self):
        from unittest.mock import Mock
        process = Mock()
        process.stdout = io.BytesIO(b"semwright 1.0.0\n")
        process.wait.return_value = 0
        with patch.object(audit.subprocess, "Popen", return_value=process) as start:
            self.assertEqual(audit.probe_cli_version(self.cli), "1.0.0")
        args = start.call_args.args[0]
        self.assertEqual(args, [str(self.cli), "--version"])
        self.assertNotIn("never-export-this-token", json.dumps(args))
        self.assertIs(start.call_args.kwargs["stdin"], audit.subprocess.DEVNULL)
        self.assertIs(start.call_args.kwargs["stderr"], audit.subprocess.DEVNULL)

    def test_excess_cli_output_rejected_without_echo(self):
        from unittest.mock import Mock
        process = Mock()
        process.stdout = io.BytesIO(b"SECRET" * 1024)
        process.wait.return_value = 0
        with patch.object(audit.subprocess, "Popen", return_value=process):
            with self.assertRaises(audit.AuditError) as failure:
                audit.probe_cli_version(self.cli)
        self.assertEqual(failure.exception.code, "cli_version_unavailable")
        process.kill.assert_called()

    def test_unknown_source_lock_version_not_claimed_supported(self):
        original = audit.read_json

        def patched(path, **kwargs):
            if path.name == "SOURCE_LOCK.json":
                return {"dependencies": {"semwright": {
                    "revision": "0" * 40, "version": "3.0.0",
                }}}
            return original(path, **kwargs)

        with patch.object(audit, "read_json", side_effect=patched):
            self.fail_with("source_lock_invalid")

    def test_no_output_receipt_created_without_explicit_output_option(self):
        before = set(self.root.iterdir())
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = audit.main([
                "--connection-file", str(self.connection),
                "--project-id", self.project, "--generation", self.generation,
            ])
        self.assertEqual(result, 0)
        self.assertEqual(set(self.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
