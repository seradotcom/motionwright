"""Cleanup contracts for the optional original MLT owner workspace."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_sequence_e2e as producer
import native_mlt_g2_observation as g2


class OwnerWorkspaceCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.session = SimpleNamespace(
            evidence_dir=self.evidence, inputs={"run_id": "123", "run_attempt": "1"},
            secondary=mock.Mock())
        patch = mock.patch.object(g2.admission, "disposable_linux_runner", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def files(self, directory):
        data = {"motionwright.sqlite3": b"database original",
                "motionwright.sqlite3-wal": b"uncheckpointed journal original",
                "motionwright.sqlite3-shm": b"shared memory original",
                "native-receipt.json": b'{"terminal":"UNKNOWN","private_path":"/owner/private"}'}
        state = Path(directory) / "state"
        state.mkdir()
        for name, raw in data.items():
            (state / name).write_bytes(raw)
        return {state / name: raw for name, raw in data.items()}

    def assertOriginals(self, originals):
        for path, raw in originals.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), raw)

    def test_normal_workspace_is_removed_after_success(self):
        with producer.native_owner_workspace() as raw:
            path = Path(raw)
            self.assertTrue(path.is_dir())
        self.assertFalse(path.exists())

    def test_normal_cleanup_preserves_the_original_exception(self):
        error = RuntimeError("original failure")
        with self.assertRaises(RuntimeError) as raised:
            with producer.native_owner_workspace() as raw:
                path = Path(raw)
                raise error
        self.assertIs(raised.exception, error)
        self.assertFalse(path.exists())

    def test_selected_workspace_records_identity_before_body_and_retains_success(self):
        with producer.native_owner_workspace(self.session) as raw:
            path = Path(raw)
            original = self.files(path)
            marker = g2.strict_object((self.evidence / "owner-workspace.json").read_bytes())
            self.assertEqual(marker["path"], str(path))
            self.assertEqual(marker["inode"], path.stat().st_ino)
            self.assertFalse(marker["native_effect_started"])
        self.assertOriginals(original)
        terminal = g2.strict_object((self.evidence / "owner-workspace-terminal.json").read_bytes())
        self.assertTrue(terminal["originals_retained"])
        self.assertFalse(terminal["workspace_deleted"])
        self.assertFalse(terminal["native_success_inferred"])

    def test_selected_failure_retains_database_wal_shm_and_same_exception(self):
        error = RuntimeError("unknown native command")
        with self.assertRaises(RuntimeError) as raised:
            with producer.native_owner_workspace(self.session) as raw:
                original = self.files(raw)
                raise error
        self.assertIs(raised.exception, error)
        self.assertOriginals(original)
        terminal = g2.strict_object((self.evidence / "owner-workspace-terminal.json").read_bytes())
        self.assertEqual(terminal["body_exception_type"], "RuntimeError")
        self.assertFalse(terminal["native_success_inferred"])

    def test_terminal_custody_failure_does_not_replace_native_failure(self):
        original_write = g2.new_json
        def write(path, value):
            if path.name == "owner-workspace-terminal.json":
                raise OSError("custody unavailable")
            return original_write(path, value)
        error = RuntimeError("original unknown")
        with mock.patch.object(g2, "new_json", side_effect=write):
            with self.assertRaises(RuntimeError) as raised:
                with producer.native_owner_workspace(self.session) as raw:
                    original = self.files(raw)
                    raise error
        self.assertIs(raised.exception, error)
        self.assertOriginals(original)
        self.session.secondary.assert_called_once_with("owner_workspace_terminal_custody")

    def test_terminal_custody_failure_after_success_is_not_silent(self):
        original_write = g2.new_json
        def write(path, value):
            if path.name == "owner-workspace-terminal.json":
                raise OSError("custody unavailable")
            return original_write(path, value)
        with mock.patch.object(g2, "new_json", side_effect=write):
            with self.assertRaises(OSError):
                with producer.native_owner_workspace(self.session) as raw:
                    original = self.files(raw)
        self.assertOriginals(original)
        self.session.secondary.assert_called_once_with("owner_workspace_terminal_custody")

    def test_failed_initial_marker_never_enters_native_body_or_cleans_unknown_state(self):
        entered = mock.Mock()
        with mock.patch.object(g2, "new_json", side_effect=OSError("initial custody unavailable")):
            with self.assertRaises(OSError):
                with producer.native_owner_workspace(self.session):
                    entered()
        entered.assert_not_called()
        leftovers = list(self.root.glob("motionwright-native-av-g2-*"))
        self.assertEqual(len(leftovers), 1)
        self.assertTrue(leftovers[0].is_dir())

    def test_base_exception_preserves_original_workspace_and_identity(self):
        error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt) as raised:
            with producer.native_owner_workspace(self.session) as raw:
                original = self.files(raw)
                raise error
        self.assertIs(raised.exception, error)
        self.assertOriginals(original)


if __name__ == "__main__":
    unittest.main()
