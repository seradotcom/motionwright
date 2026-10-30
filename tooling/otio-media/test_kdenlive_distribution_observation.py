"""Finite synthetic/source controls; no AppImage, package install or GUI calls."""
from pathlib import Path
import os
import json
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import kdenlive_distribution_observation as observation


class DistributionObservationTests(unittest.TestCase):
    def environment(self):
        source = "a" * 40
        return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_JOB": "kdenlive-distribution-observation", "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64", "MW_KDENLIVE_DISTRIBUTION_SCOPE": "kdenlive-distribution",
                "GITHUB_SHA": source, "GITHUB_WORKFLOW_SHA": source, "EXPECTED_SOURCE_SHA": source,
                "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/" + observation.WORKFLOW + "@refs/heads/example",
                "GITHUB_RUN_ID": "123"}

    def test_only_exact_manual_scope_is_admitted(self):
        env = self.environment()
        self.assertEqual(observation.current_context(env, "linux")["run_id"], "123")
        for name, bad in (("MW_KDENLIVE_DISTRIBUTION_SCOPE", "kdenlive-gui"),
                          ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "other/repo"),
                          ("GITHUB_JOB", "admitted-native-kdenlive-gui")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                observation.current_context({**env, name: bad}, "linux")

    def test_attempt_and_source_mismatch_refuse(self):
        env = self.environment()
        for name, bad in (("GITHUB_RUN_ATTEMPT", "2"), ("EXPECTED_SOURCE_SHA", "b" * 40),
                          ("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_RUN_ID", "0")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                observation.current_context({**env, name: bad}, "linux")

    def test_download_and_extraction_argv_are_closed(self):
        args = observation.download_argv(Path("/private/image"), Path("/private/headers"))
        self.assertEqual(args[0], "/usr/bin/curl")
        self.assertEqual(args[-1], observation.APPIMAGE_URL)
        self.assertNotIn("--retry", args)
        self.assertEqual(observation.extraction_argv(Path("/private/image")),
                         ["/private/image", "--appimage-extract"])
        self.assertNotIn("AppRun", " ".join(args))

    def test_wrong_archive_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = root / "image"
            image.write_bytes(b"synthetic wrong archive")
            with mock.patch.object(observation.commands, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "extraction refused"):
                    observation.check_image(image, root)
                execute.assert_not_called()

    def test_regular_hook_bytes_and_internal_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            hook = root / "hook.sh"
            hook.write_bytes(b"export QT_ACCESSIBILITY=0\n")
            (root / "alias.sh").symlink_to("hook.sh")
            resolved, link = observation.resolve_member(root, "alias.sh")
            self.assertEqual(resolved, hook)
            self.assertEqual(link, "hook.sh")
            self.assertEqual(resolved.read_bytes(), b"export QT_ACCESSIBILITY=0\n")

    def test_member_traversal_and_outside_alias_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "appdir"
            root.mkdir()
            (parent / "outside").write_bytes(b"outside")
            (root / "escape").symlink_to("../outside")
            for value in ("../outside", "/etc/passwd", "a\\b", "a:b", "escape"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    observation.resolve_member(root, value)

    def test_linked_directory_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            actual = root / "actual"
            actual.mkdir()
            (actual / "hook").write_bytes(b"hook")
            (root / "alias").symlink_to("actual", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "linked member parent"):
                observation.resolve_member(root, "alias/hook")
            rows = observation.namespace(root)
            self.assertEqual([r["path"] for r in rows], ["actual", "actual/hook", "alias"])

    def test_namespace_bound_is_enforced_without_following_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "first").write_bytes(b"one")
            (root / "second").write_bytes(b"two")
            with self.assertRaisesRegex(ValueError, "namespace budget"):
                observation.namespace(root, maximum=1)

    def test_selected_objects_include_bridge_and_qt_but_not_editor_invocation(self):
        for name in ("usr/lib/libQt6Core.so.6.9.2", "usr/plugins/platforms/libqxcb.so",
                     "usr/lib/libqspiaccessiblebridge.so", "AppRun.wrapped", "usr/bin/kdenlive"):
            self.assertTrue(observation.selected_name(name), name)
        self.assertFalse(observation.selected_name("usr/share/wallpaper.png"))

    def test_synthetic_elf_hints_are_not_loaded_version_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "libQt6Core.so.6.9.2"
            header = b"\x7fELF\x02\x01" + b"\0" * 58
            path.write_bytes(header + b"QT_ACCESSIBILITY\0org.a11y.Bus\0")
            result = observation.elf_observation(path, root)
            self.assertEqual(result["filename_version_hint"], "6.9.2")
            self.assertTrue(result["marker_bytes_present"]["QT_ACCESSIBILITY"])
            self.assertFalse(result["loaded_runtime_verified"])
            self.assertFalse(result["version_function_executed"])

    def test_non_elf_and_wrong_class_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "file"
            for data in (b"ordinary file", b"\x7fELF\x01\x01" + b"\0" * 58):
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "ELF64"):
                    observation.elf_observation(path, root)

    def test_exclusive_originals_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "original"
            observation.new_bytes(path, b"first original")
            with self.assertRaises(FileExistsError):
                observation.new_bytes(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"first original")

    def test_unknown_command_stops_before_extraction_and_keeps_original_row(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory).resolve()
            originals = owner / "originals"
            originals.mkdir()
            context = {**observation.current_context(self.environment(), "linux"), "semwright_sha": "c" * 40}
            source = {"files": {}}
            observation.new_json(originals / "STARTED.original.json", {
                "context": context, "source": source, "deadline_monotonic": time.monotonic() + 600})
            row = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "argv": ["/usr/bin/curl"], "pid": 123}
            with mock.patch.object(observation, "context_and_source", return_value=(context, source)), \
                    mock.patch.object(observation, "layout", return_value=(owner, originals, owner / "public")), \
                    mock.patch.object(observation.commands, "execute", return_value=row) as execute:
                with self.assertRaisesRegex(ValueError, "no replay"):
                    observation.observe()
            self.assertEqual(execute.call_count, 1)
            result = json.loads((originals / "OBSERVATION_RESULT.original.json").read_bytes())
            self.assertEqual(result["commands"], [row])
            self.assertEqual(result["extraction_processes_with_pid_observed"], 0)
            self.assertEqual(result["status"], "NOT_VERIFIED")

    def synthetic_seal_fixture(self, root):
        originals = root / "originals"
        originals.mkdir()
        context = {**observation.current_context(self.environment(), "linux"), "semwright_sha": "c" * 40}
        recipient = {"sha256": "b" * 64, "spki_der_base64": "explicitly synthetic; crypto mocked"}
        raw = json.dumps(recipient).encode()
        observation.new_bytes(originals / "recipient.public.original.json", raw)
        observation.new_json(originals / "STARTED.original.json", {
            "context": context, "recipient_raw_sha256": observation.sha(raw),
            "recipient_spki_sha256": recipient["sha256"]})
        custody = SimpleNamespace(public_key=mock.Mock(),
            gather_snapshot=mock.Mock(return_value=(b"synthetic captured bytes", False)),
            encrypt_snapshot=mock.Mock(return_value=b"synthetic envelope; NOT CRYPTO EVIDENCE"),
            new_bytes=observation.new_bytes, new_json=observation.new_json)
        return originals, context, custody

    def test_malformed_terminal_still_gets_one_partial_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            originals, context, custody = self.synthetic_seal_fixture(root)
            observation.new_bytes(originals / "OBSERVATION_RESULT.original.json", b"malformed original")
            with mock.patch.dict(sys.modules, {"ci_private_evidence": custody}), \
                    mock.patch.object(observation, "current_context", return_value=context), \
                    mock.patch.object(observation, "layout", return_value=(root, originals, root / "public")):
                observation.seal()
            custody.gather_snapshot.assert_called_once()
            self.assertTrue(custody.gather_snapshot.call_args.kwargs["preserve_partial"])
            custody.encrypt_snapshot.assert_called_once()
            self.assertEqual((originals / "OBSERVATION_RESULT.original.json").read_bytes(), b"malformed original")
            projection = json.loads((root / "public/projection.json").read_bytes())
            self.assertEqual(projection["status"], "NOT_VERIFIED")
            self.assertFalse(projection["G2_verified"])

    def test_recipient_mutation_refuses_encryption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            originals, context, custody = self.synthetic_seal_fixture(root)
            (originals / "recipient.public.original.json").write_bytes(b'{"sha256":"different"}')
            with mock.patch.dict(sys.modules, {"ci_private_evidence": custody}), \
                    mock.patch.object(observation, "current_context", return_value=context), \
                    mock.patch.object(observation, "layout", return_value=(root, originals, root / "public")):
                with self.assertRaisesRegex(ValueError, "recipient changed"):
                    observation.seal()
            custody.gather_snapshot.assert_not_called()
            custody.encrypt_snapshot.assert_not_called()

    def test_rejected_terminal_read_still_preserves_partial_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            originals, context, custody = self.synthetic_seal_fixture(root)
            observation.new_bytes(originals / "OBSERVATION_RESULT.original.json", b"original bytes")
            with mock.patch.dict(sys.modules, {"ci_private_evidence": custody}), \
                    mock.patch.object(observation, "current_context", return_value=context), \
                    mock.patch.object(observation, "layout", return_value=(root, originals, root / "public")), \
                    mock.patch.object(observation, "capture_regular", side_effect=RuntimeError("bounded read refused")):
                observation.seal()
            custody.gather_snapshot.assert_called_once()
            custody.encrypt_snapshot.assert_called_once()
            projection = json.loads((root / "public/projection.json").read_bytes())
            self.assertEqual(projection["status"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
