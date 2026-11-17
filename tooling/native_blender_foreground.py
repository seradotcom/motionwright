"""Foreground coordination for one Blender cycle; never Broker consent.

The six-message private channel only lends the real owner terminal to an
already-created daemon. Challenge input continues to come from the operator.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import sys
import threading
import time

PHASES = ("create_original", "edit_original", "verify_edited")
FD_ENV = "MOTIONWRIGHT_BLENDER_PHASE_FD"
MAX_MESSAGE = 256
MAX_CHANNEL_BYTES = 6 * MAX_MESSAGE


class ForegroundCoordinationError(RuntimeError):
    pass


def need(condition, code):
    if not condition:
        raise ForegroundCoordinationError(code)


def main_thread():
    need(threading.current_thread() is threading.main_thread(),
         "foreground_coordination_requires_main_thread")


def process_identity(pid):
    need(sys.platform == "linux" and type(pid) is int and pid > 0,
         "foreground_linux_process_identity_required")
    with Path(f"/proc/{pid}/stat").open("rb") as stream:
        raw = stream.read(4097)
    need(0 < len(raw) <= 4096, "foreground_process_stat_bound")
    end = raw.rfind(b") ")
    need(end > 0 and raw[:raw.find(b" ")].decode("ascii") == str(pid),
         "foreground_process_stat_identity")
    fields = raw[end + 2:].split()
    need(len(fields) >= 20 and fields[0] not in {b"Z", b"X"},
         "foreground_process_not_live")
    values = [int(fields[index]) for index in (1, 2, 3, 19)]
    need(all(value > 0 for value in values), "foreground_process_fields")
    return {"pid": pid, "parent": values[0], "group": values[1],
            "session": values[2], "start_time": values[3]}


def parse_request(raw, digest, sequence):
    need(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
         "foreground_attempt_digest")
    need(type(sequence) is int and 0 <= sequence < 6, "foreground_sequence_exhausted")
    phase = PHASES[sequence // 2]
    transition = "enter" if sequence % 2 == 0 else "leave"
    expected = f"MWBLENDER-TTY/1 {digest} {sequence} {transition} {phase}\n".encode("ascii")
    need(type(raw) is bytes and 0 < len(raw) <= MAX_MESSAGE and raw == expected,
         "foreground_message_order_or_identity")
    return phase, transition


def acknowledgement(digest, sequence):
    need(type(sequence) is int and 0 <= sequence < 6
         and type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
         "foreground_ack_identity")
    return f"MWBLENDER-TTY/1 {digest} {sequence} ack\n".encode("ascii")


class ForegroundDaemonTerminal:
    """Retain one Popen identity across its setup and one Native phase."""
    def __init__(self, daemon, acquire, restore):
        main_thread()
        self.daemon, self.acquire_terminal, self.restore_terminal = daemon, acquire, restore
        self.owner_group, self.owner_session = os.getpgrp(), os.getsid(0)
        self.identity = process_identity(daemon.pid)
        need(self.identity["parent"] == os.getpid()
             and self.identity["group"] == daemon.pid
             and self.identity["session"] == self.owner_session,
             "foreground_daemon_not_original_child_group")
        self.terminal = None
        self.transitions = []
        self.acquisitions = 0

    def check(self):
        main_thread()
        need(self.daemon.poll() is None and process_identity(self.daemon.pid) == self.identity
             and os.getpgrp() == self.owner_group and os.getsid(0) == self.owner_session,
             "foreground_daemon_or_owner_changed")

    def enter(self):
        self.check()
        need(self.terminal is None and self.acquisitions < 2,
             "foreground_daemon_lease_already_consumed")
        self.acquisitions += 1
        entry = {"transition": "enter", "attempt": self.acquisitions,
                 "identity": dict(self.identity), "acknowledged": False}
        self.transitions.append(entry)
        self.terminal = self.acquire_terminal(self.daemon.pid)
        fd, previous = self.terminal
        self.check()
        need(previous == self.owner_group and os.tcgetpgrp(fd) == self.daemon.pid,
             "foreground_daemon_transfer_not_observed")
        entry["acknowledged"] = True

    def leave(self):
        main_thread()
        need(self.terminal is not None, "foreground_daemon_lease_not_active")
        terminal, self.terminal = self.terminal, None
        entry = {"transition": "leave", "attempt": self.acquisitions,
                 "identity": dict(self.identity), "acknowledged": False}
        self.transitions.append(entry)
        # restore_terminal rejects an unrelated foreground and always closes fd.
        # A dead daemon does not prevent returning a still-owned foreground.
        if os.getpgrp() != self.owner_group or os.getsid(0) != self.owner_session:
            os.close(terminal[0])
            raise ForegroundCoordinationError("foreground_owner_changed_before_restore")
        self.restore_terminal(terminal[0], terminal[1], self.daemon.pid)
        fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0))
        try:
            need(os.tcgetpgrp(fd) == self.owner_group,
                 "foreground_owner_restore_not_observed")
        finally:
            os.close(fd)
        entry["acknowledged"] = True

    def close(self):
        main_thread()
        if self.terminal is not None:
            self.leave()


class ForegroundPhaseCoordinator:
    """The existing supervisor services this channel only on its main thread."""
    def __init__(self, terminals, attempt_sha256, record):
        main_thread()
        need(sys.platform == "linux" and type(terminals) is dict
             and tuple(terminals) == PHASES, "foreground_three_original_phases_required")
        need(re.fullmatch(r"[0-9a-f]{64}", attempt_sha256 or "") is not None,
             "foreground_attempt_digest")
        need(type(record) is dict and not record, "foreground_record_must_be_new")
        identities = [terminal.identity for terminal in terminals.values()]
        need(len({item["pid"] for item in identities}) == 3
             and len({item["session"] for item in identities}) == 1,
             "foreground_phase_groups_not_distinct")
        for terminal in terminals.values():
            terminal.check()
            need(terminal.terminal is None and terminal.acquisitions == 1,
                 "foreground_phase_setup_lease_not_returned")
        self.terminals, self.digest, self.record = terminals, attempt_sha256, record
        self.parent_socket = self.child_socket = self.child = None
        self.child_identity = None
        self.sequence = self.received = 0
        self.buffer = bytearray()
        self.partial_deadline = None
        self.eof = self.prepared = self.closed = False
        record.update(schema="motionwright.blender.foreground-phase-coordination/1",
                      attempt_sha256=attempt_sha256, phases=list(PHASES),
                      transitions=[], complete=False, broker_consent_granted=False,
                      automatic_retry=False)

    def prepare(self, env):
        main_thread()
        need(not self.prepared and not self.closed and FD_ENV not in env,
             "foreground_channel_already_prepared")
        self.prepared = True
        self.parent_socket, self.child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.parent_socket.setblocking(False)
        self.parent_socket.set_inheritable(False)
        self.child_socket.set_inheritable(False)
        return {**env, FD_ENV: str(self.child_socket.fileno())}, (self.child_socket.fileno(),)

    def bind(self, child):
        main_thread()
        need(self.prepared and self.child is None and not self.closed,
             "foreground_child_already_bound")
        self.child = child
        self.child_socket.close()
        self.child_socket = None
        self.child_identity = process_identity(child.pid)
        need(child.poll() is None and self.child_identity["parent"] == os.getpid()
             and self.child_identity["group"] == child.pid
             and self.child_identity["session"] == child.pid,
             "foreground_cycle_child_not_original_session")
        self.record["child_identity"] = dict(self.child_identity)

    def service(self):
        main_thread()
        need(not self.closed and self.child is not None, "foreground_channel_not_bound")
        if self.eof:
            return
        if self.partial_deadline is not None:
            need(time.monotonic() < self.partial_deadline, "foreground_partial_message_timeout")
        try:
            chunk = self.parent_socket.recv(MAX_MESSAGE + 1)
        except BlockingIOError:
            return
        if not chunk:
            self.eof = True
            need(not self.buffer, "foreground_incomplete_message_eof")
            need(all(item.terminal is None for item in self.terminals.values()),
                 "foreground_active_lease_eof")
            return
        self.received += len(chunk)
        need(self.received <= MAX_CHANNEL_BYTES, "foreground_channel_byte_bound")
        if not self.buffer:
            self.partial_deadline = time.monotonic() + 2.0
        self.buffer.extend(chunk)
        need(len(self.buffer) <= MAX_MESSAGE, "foreground_message_byte_bound")
        if b"\n" not in self.buffer:
            return
        raw = bytes(self.buffer)
        self.buffer.clear()
        self.partial_deadline = None
        sequence = self.sequence
        phase, transition = parse_request(raw, self.digest, sequence)
        need(self.child.poll() is None
             and process_identity(self.child.pid) == self.child_identity,
             "foreground_cycle_process_changed")
        terminal = self.terminals[phase]
        terminal.check()
        need(all(item.terminal is None for name, item in self.terminals.items() if name != phase),
             "foreground_parallel_lease_denied")
        self.sequence += 1  # The request is consumed before any terminal effect.
        entry = {"sequence": sequence, "phase": phase, "transition": transition,
                 "terminal_acknowledged": False, "channel_acknowledged": False}
        self.record["transitions"].append(entry)
        getattr(terminal, transition)()
        entry["terminal_acknowledged"] = True
        ack = acknowledgement(self.digest, sequence)
        need(self.parent_socket.send(ack) == len(ack), "foreground_ack_incomplete")
        entry["channel_acknowledged"] = True

    def close(self, returncode=None):
        main_thread()
        if self.closed:
            return
        self.closed = True
        errors = []
        for terminal in self.terminals.values():
            try:
                terminal.close()
            except BaseException as error:
                errors.append(type(error).__name__ + ":" + str(error)[:256])
        for channel in (self.child_socket, self.parent_socket):
            if channel is not None:
                try:
                    channel.close()
                except OSError as error:
                    errors.append(type(error).__name__)
        self.record.update(received_bytes=self.received, eof_observed=self.eof,
                           sequence_consumed=self.sequence, process_returncode=returncode,
                           cleanup_errors=errors,
                           daemon_transitions={phase: terminal.transitions
                                               for phase, terminal in self.terminals.items()})
        complete = (returncode == 0 and self.sequence == 6 and self.eof and not errors
                    and len(self.record["transitions"]) == 6
                    and all(item["channel_acknowledged"] for item in self.record["transitions"]))
        self.record["complete"] = complete
        need(not errors, "foreground_cleanup_failed")
        if returncode == 0:
            need(complete, "foreground_success_without_complete_sequence")
