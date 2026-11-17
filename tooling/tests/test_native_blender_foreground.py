"""Pure phase guards and inert command custody; no TTY, Broker or Native effects."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_foreground as foreground
import native_blender_original_evidence as evidence

DIGEST = "a" * 64


class TestTerminal:
    """Test state only; never a process, terminal or authorization."""
    __test__ = False
    def __init__(self, pid):
        self.identity = {"pid": pid, "session": 44}
        self.terminal = None
        self.acquisitions = 1
        self.transitions = []
        self.fail_enter = False

    def check(self):
        pass

    def enter(self):
        self.transitions.append("enter")
        if self.fail_enter:
            raise RuntimeError("original lease failure")
        self.terminal = object()

    def leave(self):
        if self.terminal is None:
            raise RuntimeError("no active test lease")
        self.transitions.append("leave")
        self.terminal = None

    def close(self):
        if self.terminal is not None:
            self.leave()


class TestChannel:
    __test__ = False
    def __init__(self):
        self.pending = []
        self.sent = []
        self.reject_ack = False
        self.closed = False

    def recv(self, maximum):
        if not self.pending:
            raise BlockingIOError()
        return self.pending.pop(0)

    def send(self, raw):
        self.sent.append(raw)
        return 0 if self.reject_ack else len(raw)

    def close(self):
        self.closed = True


def request(sequence):
    phase = foreground.PHASES[sequence // 2]
    direction = "enter" if sequence % 2 == 0 else "leave"
    return f"MWBLENDER-TTY/1 {DIGEST} {sequence} {direction} {phase}\n".encode()


class ForegroundProtocolTests(unittest.TestCase):
    def coordinator(self):
        terminals = {phase: TestTerminal(70 + index)
                     for index, phase in enumerate(foreground.PHASES)}
        record = {}
        # Constructor platform selection is tested here as source coordination,
        # never as admission to a real owner or a physical acceptance attempt.
        with mock.patch.object(foreground.sys, "platform", "linux"):
            coordinator = foreground.ForegroundPhaseCoordinator(terminals, DIGEST, record)
        coordinator.child = SimpleNamespace(pid=99, poll=lambda: None)
        coordinator.child_identity = {"pid": 99, "parent": 42, "group": 99,
                                      "session": 99, "start_time": 101}
        coordinator.parent_socket = TestChannel()
        patcher = mock.patch.object(foreground, "process_identity",
                                    return_value=coordinator.child_identity.copy())
        patcher.start()
        self.addCleanup(patcher.stop)
        return coordinator

    def test_six_closed_messages_and_exact_acknowledgements(self):
        for sequence in range(6):
            self.assertEqual(foreground.parse_request(request(sequence), DIGEST, sequence),
                             (foreground.PHASES[sequence // 2],
                              "enter" if sequence % 2 == 0 else "leave"))
            self.assertEqual(foreground.acknowledgement(DIGEST, sequence),
                             f"MWBLENDER-TTY/1 {DIGEST} {sequence} ack\n".encode())

    def test_order_digest_extra_fields_and_replay_are_rejected(self):
        for sequence in range(6):
            original = request(sequence)
            invalid = (original.replace(DIGEST.encode(), b"b" * 64),
                       original + b"another\n", original[:-1] + b" pid=123\n",
                       original.replace(b"/1 ", b"/2 "), original.replace(b"\n", b"\r\n"),
                       original.replace(b"_original", b"_other"))
            for raw in invalid:
                if raw != original:
                    with self.subTest(sequence=sequence, raw=raw), self.assertRaises(
                            foreground.ForegroundCoordinationError):
                        foreground.parse_request(raw, DIGEST, sequence)
            with self.assertRaises(foreground.ForegroundCoordinationError):
                foreground.parse_request(original, DIGEST, (sequence + 1) % 6)

    def test_ambiguous_types_and_exhausted_sequences_cannot_parse(self):
        for raw in ("text", bytearray(request(0)), b"", b"X" * 257, b"approve abc\n"):
            with self.assertRaises(foreground.ForegroundCoordinationError):
                foreground.parse_request(raw, DIGEST, 0)
        for sequence in (True, -1, 6, 100):
            with self.assertRaises(foreground.ForegroundCoordinationError):
                foreground.parse_request(request(0), DIGEST, sequence)
        for digest in ("A" * 64, "g" * 64, "a" * 63, None):
            with self.assertRaises(foreground.ForegroundCoordinationError):
                foreground.parse_request(request(0), digest, 0)

    def test_worker_thread_cannot_manage_foreground(self):
        failures = []
        def worker():
            try:
                foreground.main_thread()
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIn("requires_main_thread", str(failures[0]))

    def test_full_protocol_returns_each_separate_owner_before_the_next(self):
        owner = self.coordinator()
        for sequence in range(6):
            owner.parent_socket.pending.append(request(sequence))
            owner.service()
            self.assertLessEqual(sum(item.terminal is not None
                                     for item in owner.terminals.values()), 1)
        owner.parent_socket.pending.append(b"")
        owner.service()
        owner.close(returncode=0)
        self.assertTrue(owner.record["complete"])
        self.assertFalse(owner.record["broker_consent_granted"])
        self.assertEqual(owner.parent_socket.sent,
                         [foreground.acknowledgement(DIGEST, index) for index in range(6)])
        self.assertTrue(all(item.transitions == ["enter", "leave"]
                            for item in owner.terminals.values()))

    def test_wrong_identity_is_rejected_before_any_terminal_effect(self):
        owner = self.coordinator()
        owner.parent_socket.pending.append(request(0))
        with mock.patch.object(foreground, "process_identity",
                               return_value={**owner.child_identity, "start_time": 102}):
            with self.assertRaisesRegex(foreground.ForegroundCoordinationError, "process_changed"):
                owner.service()
        self.assertEqual(owner.sequence, 0)
        self.assertEqual(owner.terminals["create_original"].transitions, [])

    def test_partial_oversized_and_early_eof_fail_closed(self):
        for raw, code in ((b"x" * 257, "byte_bound"), (b"incomplete", "incomplete_message_eof")):
            owner = self.coordinator()
            owner.parent_socket.pending.append(raw)
            if len(raw) <= 256:
                owner.service()
                owner.parent_socket.pending.append(b"")
            with self.assertRaisesRegex(foreground.ForegroundCoordinationError, code):
                owner.service()
            self.assertEqual(owner.sequence, 0)
        owner = self.coordinator()
        owner.parent_socket.pending.append(b"x")
        owner.service()
        owner.partial_deadline = time.monotonic() - 1
        with self.assertRaisesRegex(foreground.ForegroundCoordinationError, "timeout"):
            owner.service()

    def test_eof_with_active_lease_rejects_then_returns_that_lease(self):
        owner = self.coordinator()
        owner.parent_socket.pending.append(request(0))
        owner.service()
        owner.parent_socket.pending.append(b"")
        with self.assertRaisesRegex(foreground.ForegroundCoordinationError, "active_lease_eof"):
            owner.service()
        owner.close(returncode=1)
        self.assertEqual(owner.terminals["create_original"].transitions, ["enter", "leave"])
        self.assertFalse(owner.record["complete"])

    def test_failed_ack_consumes_transition_and_cannot_replay(self):
        owner = self.coordinator()
        owner.parent_socket.reject_ack = True
        owner.parent_socket.pending.append(request(0))
        with self.assertRaisesRegex(foreground.ForegroundCoordinationError, "ack_incomplete"):
            owner.service()
        self.assertEqual(owner.sequence, 1)
        owner.parent_socket.pending.append(request(0))
        with self.assertRaisesRegex(foreground.ForegroundCoordinationError, "order_or_identity"):
            owner.service()
        self.assertEqual(owner.terminals["create_original"].transitions, ["enter"])
        owner.close(returncode=1)
        self.assertFalse(owner.record["complete"])

    def test_failed_native_prefix_never_becomes_success(self):
        for returncode in (0, 7):
            owner = self.coordinator()
            for sequence in range(2):
                owner.parent_socket.pending.append(request(sequence))
                owner.service()
            owner.parent_socket.pending.append(b"")
            owner.service()
            if returncode == 0:
                with self.assertRaisesRegex(foreground.ForegroundCoordinationError,
                                            "without_complete_sequence"):
                    owner.close(returncode=returncode)
            else:
                owner.close(returncode=returncode)
            self.assertFalse(owner.record["complete"])

    def test_foreign_parallel_lease_and_bad_requests_do_not_dispatch(self):
        for mutation in ("parallel", "wrong"):
            owner = self.coordinator()
            if mutation == "parallel":
                owner.terminals["edit_original"].terminal = object()
            owner.parent_socket.pending.append(request(1 if mutation == "wrong" else 0))
            with self.assertRaises(foreground.ForegroundCoordinationError):
                owner.service()
            self.assertEqual(owner.sequence, 0)
            self.assertEqual(owner.terminals["create_original"].transitions, [])


class ForegroundPublicationCustodyTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Actual inherited Unix channel requires Linux")
    def test_inert_linux_child_uses_only_the_inherited_channel(self):
        terminals = {phase: TestTerminal(70 + index)
                     for index, phase in enumerate(foreground.PHASES)}
        phase_record = {}
        owner = foreground.ForegroundPhaseCoordinator(terminals, DIGEST, phase_record)
        script = (
            "import os,socket,sys\n"
            "fd=int(os.environ['MOTIONWRIGHT_BLENDER_PHASE_FD'])\n"
            "os.set_inheritable(fd,False)\n"
            "channel=socket.socket(fileno=fd);channel.settimeout(3)\n"
            "phases=('create_original','edit_original','verify_edited')\n"
            "for sequence in range(6):\n"
            " phase=phases[sequence//2];direction='enter' if sequence%2==0 else 'leave'\n"
            " channel.sendall(f'MWBLENDER-TTY/1 {sys.argv[1]} {sequence} {direction} {phase}\\n'.encode())\n"
            " ack=b''\n"
            " while not ack.endswith(b'\\n') and len(ack)<256:\n"
            "  part=channel.recv(1)\n"
            "  if not part:raise RuntimeError('premature channel EOF')\n"
            "  ack+=part\n"
            " assert ack==f'MWBLENDER-TTY/1 {sys.argv[1]} {sequence} ack\\n'.encode()\n"
            "channel.close()\n"
            "sys.stdout.buffer.write(b'inert channel complete')\n")
        with tempfile.TemporaryDirectory(prefix="blender-inert-channel-") as directory:
            root = Path(directory)
            record = {}
            with (root / "stdout").open("xb") as out, (root / "stderr").open("xb") as err:
                returned = evidence._bounded_original_command(
                    [sys.executable, "-I", "-c", script, DIGEST],
                    env=dict(os.environ), stdout=out, stderr=err, timeout=8,
                    record=record, phase_coordination=owner)
            self.assertEqual(returned.returncode, 0)
            self.assertTrue(record["stream_custody"]["complete"])
            self.assertTrue(phase_record["complete"])
            self.assertFalse(phase_record["broker_consent_granted"])
            self.assertEqual(phase_record["child_identity"]["pid"], owner.child.pid)
            self.assertEqual((root / "stdout").read_bytes(), b"inert channel complete")
            self.assertEqual((root / "stderr").read_bytes(), b"")

    def test_channel_denial_allows_original_nonzero_terminal_to_publish_once(self):
        with tempfile.TemporaryDirectory(prefix="blender-phase-source-custody-") as directory:
            root = Path(directory)
            marker = root / "channel-closed"
            class Denial:
                closed = False
                def prepare(self, env):
                    return env, ()
                def bind(self, child):
                    self.child = child
                def service(self):
                    raise RuntimeError("original channel denial")
                def close(self, returncode=None):
                    if not self.closed:
                        self.closed = True
                        marker.write_bytes(b"closed")
            script = (
                "import pathlib,sys,time\n"
                "marker=pathlib.Path(sys.argv[1]);deadline=time.monotonic()+3\n"
                "while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)\n"
                "sys.stdout.buffer.write(b'original terminal bytes')\n"
                "sys.stderr.buffer.write(b'original known denial')\n"
                "sys.exit(7 if marker.exists() else 8)\n")
            record = {}
            with (root / "stdout").open("xb") as out, (root / "stderr").open("xb") as err:
                returned = evidence._bounded_original_command(
                    [sys.executable, "-I", "-c", script, str(marker)],
                    env=dict(os.environ), stdout=out, stderr=err, timeout=5,
                    record=record, phase_coordination=Denial())
            self.assertEqual(returned.returncode, 7)
            self.assertEqual((root / "stdout").read_bytes(), b"original terminal bytes")
            self.assertEqual((root / "stderr").read_bytes(), b"original known denial")
            self.assertTrue(record["stream_custody"]["complete"])
            self.assertEqual(record["foreground_coordination_error"]["message"],
                             "original channel denial")


if __name__ == "__main__":
    unittest.main()
