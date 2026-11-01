"""GN1 resource/custody regressions: inert processes only, never a Native Host."""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from tooling import ci_graph_dev_native_startup as gn1


def identity(size):
    return {"bytes": size, "sha256": "1" * 64, "path": "/synthetic/source-bound-input"}


_REAL_PATH_RESOLVE = Path.resolve


def resolve_fixture_ffmpeg(path, *args, **kwargs):
    # The two source-only fixture tests mock file_identity and never launch
    # ffmpeg. WSL need not install ffmpeg merely to test a numeric budget.
    # Every other path keeps the original strict realpath behavior.
    if str(path) == "/usr/bin/ffmpeg":
        return path
    return _REAL_PATH_RESOLVE(path, *args, **kwargs)


@unittest.skipUnless(sys.platform == "linux", "Linux process-resource boundary")
class StagingBudgetTests(unittest.TestCase):
    def test_oversized_staging_is_rejected_before_owner_directory_or_startup(self):
        with tempfile.TemporaryDirectory(prefix="gn1-before-owner-") as tmp:
            parent = Path(tmp).resolve()
            root = parent / "gn1-42"
            args = argparse.Namespace(repo=parent, sdk=parent,
                                      expected_source="1" * 40, root=root)
            binaries = {"native_provider": identity(gn1.MAX_NATIVE_STAGING_BYTES + 1),
                        "manim_runner": identity(4096)}
            env = {"GITHUB_SHA": "1" * 40, "RUNNER_TEMP": str(parent),
                   "GN1_RECIPIENT_JSON": "{}"}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.dict(sys.modules, {"ci_private_evidence": mock.Mock()}), \
                    mock.patch.object(gn1, "require_runner", return_value="42"), \
                    mock.patch.object(gn1, "source_identity", return_value={}), \
                    mock.patch.object(gn1, "signed_binaries", return_value=binaries), \
                    mock.patch.object(Path, "resolve", resolve_fixture_ffmpeg), \
                    mock.patch.object(gn1, "file_identity", return_value=identity(4096)), \
                    mock.patch.object(gn1, "actual_startup") as startup:
                with self.assertRaisesRegex(gn1.GN1Failure, "GN1_STAGING_INPUT_BUDGET"):
                    gn1.run_once(args)
            startup.assert_not_called()
            self.assertFalse(root.exists())

    def test_plan_covers_actual_staged_inputs_and_rejects_larger_inputs(self):
        import resource
        binaries = {"native_provider": identity(67001184),
                    "manim_runner": identity(3 * 1024 * 1024)}
        with mock.patch.object(Path, "resolve", resolve_fixture_ffmpeg), \
                mock.patch.object(gn1, "file_identity", return_value=identity(4096)), \
                mock.patch.object(resource, "getrlimit", return_value=(-1, -1)):
            plan = gn1.daemon_staging_plan(binaries)
            self.assertEqual(plan["file_size_limit_bytes"], 67001184)
            self.assertEqual(set(plan["inputs"]),
                             {"native_provider", "manim_runner", "python", "ffmpeg"})
            binaries["manim_runner"] = identity(gn1.MAX_NATIVE_STAGING_BYTES + 1)
            with self.assertRaisesRegex(gn1.GN1Failure, "GN1_STAGING_INPUT_BUDGET"):
                gn1.daemon_staging_plan(binaries)
        binaries["manim_runner"] = identity(4096)
        with mock.patch.object(Path, "resolve", resolve_fixture_ffmpeg), \
                mock.patch.object(gn1, "file_identity", return_value=identity(4096)), \
                mock.patch.object(resource, "getrlimit", return_value=(2097152, 2097152)):
            with self.assertRaisesRegex(gn1.GN1Failure, "GN1_STAGING_HARD_LIMIT_INSUFFICIENT"):
                gn1.daemon_staging_plan(binaries)

    def test_inert_write_above_old_limit_succeeds_but_admitted_budget_is_enforced(self):
        budget = 3 * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix="gn1-stage-bound-") as tmp:
            for extra in (0, 1):
                with self.subTest(extra=extra):
                    output = Path(tmp) / str(extra)
                    code = ("import os,signal,sys; "
                            "signal.signal(signal.SIGXFSZ,signal.SIG_DFL); "
                            "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
                            "os.write(fd,b'x'*int(sys.argv[2])); "
                            "os.write(fd,b'x'*int(sys.argv[3])); os.close(fd)")
                    process = subprocess.run(
                        [sys.executable, "-I", "-S", "-c", code, str(output),
                         str(budget), str(extra)],
                        preexec_fn=lambda: gn1._limits(budget), start_new_session=True,
                        capture_output=True, timeout=10, check=False,
                    )
                    self.assertEqual(process.returncode, 0 if extra == 0 else -signal.SIGXFSZ)
                    self.assertEqual(output.stat().st_size, budget)
                    self.assertEqual(process.stdout, b"")

    def test_daemon_log_keeps_two_mib_limit_with_larger_staging_budget(self):
        with tempfile.TemporaryDirectory(prefix="gn1-log-bound-") as tmp:
            for extra in (0, 1):
                with self.subTest(extra=extra):
                    path = Path(tmp) / (str(extra) + ".original.log")
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    process = subprocess.Popen(
                        [sys.executable, "-I", "-S", "-c",
                         "import os,sys; os.write(1,b'g'*int(sys.argv[1]))",
                         str(gn1.MAX_DAEMON_LOG_BYTES + extra)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        start_new_session=True, preexec_fn=lambda: gn1._limits(3 * 1024 * 1024),
                    )
                    capture = gn1.DaemonLogCapture(process, fd)
                    try:
                        process.wait(timeout=10)
                    finally:
                        gn1._stop_group(process, "synthetic", Path(tmp))
                        receipt = capture.finish()
                    self.assertEqual(path.stat().st_size, gn1.MAX_DAEMON_LOG_BYTES)
                    self.assertEqual(receipt["complete"], extra == 0)
                    self.assertEqual(receipt["overflow"], extra == 1)
                    self.assertEqual(receipt["retained_prefix_bytes"], gn1.MAX_DAEMON_LOG_BYTES)
                    self.assertFalse(receipt["automatic_retry"])

    def test_inert_daemon_log_overflow_closes_owned_group_and_records_unknown(self):
        with tempfile.TemporaryDirectory(prefix="gn1-daemon-log-") as tmp:
            root = Path(tmp).resolve()
            private = gn1.mkdir(root, "private")
            config = root / "fixture.toml"
            config.write_text("# inert fixture\n")
            script = root / "inert-daemon"
            script.write_text(
                "#!" + sys.executable + "\n"
                "import os, time\n"
                "os.write(1, b'g' * (2 * 1024 * 1024 + 1))\n"
                "time.sleep(10)\n")
            script.chmod(0o700)
            with self.assertRaisesRegex(gn1.GN1Failure, "GN1_DAEMON_LOG_CAPTURE_INCOMPLETE") as caught:
                with gn1.real_daemon("restricted", str(script), config, root / "broker.sock",
                                     {}, private, file_size_limit=3 * 1024 * 1024) as process:
                    process.wait(timeout=5)
            self.assertTrue(caught.exception.unknown)
            terminal = json.loads((private / "restricted.terminal.original.json").read_bytes())
            capture = json.loads((private / "restricted.log-capture.original.json").read_bytes())
            self.assertTrue(terminal["process_group_closed"])
            self.assertFalse(capture["complete"])
            self.assertTrue(capture["overflow"])
            self.assertEqual((private / "restricted.daemon.original.log").stat().st_size,
                             gn1.MAX_DAEMON_LOG_BYTES)


class PartialCustodyTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Actual FIFO replacement race")
    def test_partial_snapshot_fifo_replacement_cannot_block_capture(self):
        # Run the real replacement race in a bounded inert child so a
        # regression to a blocking open fails within five seconds.
        code = """
import json, os, stat, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
from tooling import ci_graph_dev_native_startup as gn1
with tempfile.TemporaryDirectory(prefix='gn1-fifo-race-') as tmp:
    root = Path(tmp).resolve()
    private = gn1.mkdir(root, 'private')
    victim = private / 'raced.original'
    victim.write_bytes(b'original')
    original_open = os.open
    replaced = False
    def replace_at_open(path, flags, *args, **kwargs):
        global replaced
        if Path(path) == victim and not replaced:
            replaced = True
            victim.unlink()
            os.mkfifo(victim, 0o600)
        return original_open(path, flags, *args, **kwargs)
    with mock.patch.object(gn1.os, 'open', side_effect=replace_at_open):
        _, count, coverage = gn1.private_snapshot(root, preserve_partial=True)
    assert replaced and stat.S_ISFIFO(victim.lstat().st_mode)
    assert not coverage['complete'] and count == 0
    assert coverage['omitted_originals'][0]['code'] == 'GN1_PRIVATE_SPECIAL_FILE'
    print(json.dumps({'fifo_race': 'REJECTED_WITHOUT_BLOCKING', 'native_host': False}))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code,
             str(Path(__file__).resolve().parents[2])],
            capture_output=True, timeout=5, check=True,
        )
        self.assertEqual(json.loads(result.stdout)["fifo_race"], "REJECTED_WITHOUT_BLOCKING")

    def test_oversized_residual_is_retained_and_omission_is_authenticated_metadata(self):
        with tempfile.TemporaryDirectory(prefix="gn1-partial-") as tmp:
            root = Path(tmp).resolve()
            private, state = gn1.mkdir(root, "private"), gn1.mkdir(root, "state")
            gn1.new_bytes(private / "failure.original.json", b'{"status":"FAIL"}\n')
            residual = state / "derived-driver-stage"
            with residual.open("xb") as file:
                file.truncate(gn1.MAX_PRIVATE_SINGLE + 1)
            with self.assertRaisesRegex(gn1.GN1Failure, "GN1_ORIGINAL_OVERSIZE"):
                gn1.private_snapshot(root)
            raw, count, coverage = gn1.private_snapshot(root, preserve_partial=True)
            self.assertFalse(coverage["complete"])
            self.assertEqual(count, 1)
            self.assertEqual(coverage["original_files_deleted"], 0)
            self.assertEqual(residual.stat().st_size, gn1.MAX_PRIVATE_SINGLE + 1)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as archive:
                self.assertNotIn("state/derived-driver-stage", archive.getnames())
                sealed_coverage = json.load(archive.extractfile("gn1-private-capture.json"))
                self.assertEqual(sealed_coverage, coverage)
                self.assertEqual(coverage["omitted_originals"][0]["code"], "GN1_ORIGINAL_OVERSIZE")
                self.assertEqual(archive.extractfile("private/failure.original.json").read(),
                                 b'{"status":"FAIL"}\n')
            self.assertLessEqual(len(raw), 34 * 1024 * 1024)
            self.assertEqual(gn1.MAX_PRIVATE_SINGLE, 8 * 1024 * 1024)
            self.assertEqual(gn1.MAX_PRIVATE_BYTES, 32 * 1024 * 1024)

    @unittest.skipIf(os.name == "nt", "Windows fixture may lack symlink privilege")
    def test_partial_snapshot_never_follows_alias_to_unselected_bytes(self):
        with tempfile.TemporaryDirectory(prefix="gn1-partial-alias-") as tmp:
            root = Path(tmp).resolve()
            private = gn1.mkdir(root, "private")
            foreign = root / "foreign"
            foreign.write_bytes(b"PRIVATE_UNSELECTED_CANARY")
            (private / "link").symlink_to(foreign)
            raw, count, coverage = gn1.private_snapshot(root, preserve_partial=True)
            self.assertEqual(count, 0)
            self.assertFalse(coverage["complete"])
            self.assertEqual(coverage["omitted_originals"][0]["code"],
                             "GN1_PRIVATE_SYMLINK_NOT_SEALED")
            self.assertNotIn(b"PRIVATE_UNSELECTED_CANARY", raw)
            self.assertTrue((private / "link").is_symlink())

    @unittest.skipUnless(sys.platform == "linux", "Private native owner 0700 mode requires Linux")
    def test_incomplete_capture_forces_public_unknown_even_after_successful_fixture(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        with mock.patch.object(sys, "path", [str(Path(__file__).resolve().parents[1]), *sys.path]):
            import ci_private_evidence as custody
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = key.public_key().public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
        recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                     "sha256": hashlib.sha256(der).hexdigest()}
        with tempfile.TemporaryDirectory(prefix="gn1-result-fixture-") as tmp:
            parent = Path(tmp).resolve()
            root = parent / "gn1-42"
            source = {"source_sha": "1" * 40, "sdk_sha": "2" * 40}
            args = argparse.Namespace(repo=parent, sdk=parent, expected_source="1" * 40, root=root)

            def fixture_startup(_repo, _sdk, owner, _first, private, marks, _staging):
                # Synthetic unit fixture only; never used as Native evidence.
                for label, stage in (("bootstrap", "BOOTSTRAP_SOCKET"),
                                     ("restricted", "RESTRICTED_SOCKET")):
                    marks.append({"stage": stage})
                    gn1.new_json(private / (label + ".start.original.json"),
                                 {"pid": 1001, "automatic_retry": False})
                state = gn1.mkdir(owner, "state")
                with (state / "derived-driver-stage").open("xb") as file:
                    file.truncate(gn1.MAX_PRIVATE_SINGLE + 1)

            env = {"GITHUB_SHA": "1" * 40, "RUNNER_TEMP": str(parent),
                   "GN1_RECIPIENT_JSON": json.dumps(recipient)}
            public_output = io.StringIO()
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(gn1, "require_runner", return_value="42"), \
                    mock.patch.object(gn1, "source_identity", return_value=source), \
                    mock.patch.object(gn1, "signed_binaries", return_value={}), \
                    mock.patch.object(gn1, "daemon_staging_plan", return_value={"fixture": True}), \
                    mock.patch.object(gn1, "actual_startup", side_effect=fixture_startup), \
                    contextlib.redirect_stdout(public_output):
                self.assertEqual(gn1.run_once(args), 1)
            public = json.loads(public_output.getvalue())
            self.assertEqual(public["status"], "UNKNOWN")
            self.assertEqual(public["code"], "GN1_PRIVATE_CAPTURE_INCOMPLETE")
            self.assertFalse(public["private_capture_complete"])
            self.assertEqual(public["Graph6_cases_accepted"], 0)
            self.assertEqual(public["G2"], "NOT_GRANTED")
            self.assertTrue((root / "state/derived-driver-stage").is_file())
            original, context = custody.decrypt_snapshot(
                (root / "transport/gn1-private.mwseal").read_bytes(), key)
            with tarfile.open(fileobj=io.BytesIO(original), mode="r") as archive:
                self.assertFalse(json.load(archive.extractfile("gn1-private-capture.json"))["complete"])
            self.assertEqual(context["run_id"], "42")


if __name__ == "__main__":
    unittest.main()
