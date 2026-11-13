"""Retain actual synthetic PCM inputs and bounded command observations.

This wrapper uses the existing process observer. It does not grant Native Host,
human listening, mastering, or release authority.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

import ci_storage_apply_recovery as custody

MAX_FILE = 4 * 1024 * 1024
MAX_EVIDENCE = 16 * 1024 * 1024


class OriginalCommandError(AssertionError):
    def __init__(self, command, secondary=None):
        self.command = command
        self.original_exit_code = command.get("exit_code")
        self.secondary = secondary
        super().__init__(
            "Original PCM command did not complete with verified custody: "
            f"exit_code={self.original_exit_code!r}, "
            f"observation={command.get('observation')!r}"
            + (f", custody_error={type(secondary).__name__}" if secondary else "")
        )


class OriginalPcmCustody:
    def __init__(self, directory, cwd, context):
        self.directory = Path(directory).resolve(strict=True)
        self.cwd = Path(cwd).resolve(strict=True)
        self.context = dict(context)
        self.command_root = self.directory / "commands"
        self.command_root.mkdir(exist_ok=False)
        self.commands = {}
        self.stopped = False
        self.originals = {}

    def observe(self, path):
        identity = custody.fingerprint(path, self.directory, MAX_FILE)
        prior = self.originals.setdefault(identity["path"], identity)
        custody.require(identity == prior, "retained original changed after first observation")
        return identity

    def retain(self, name, raw):
        custody.require(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) is not None,
                        "unsafe original source name")
        custody.require(isinstance(raw, bytes) and len(raw) <= MAX_FILE,
                        "original source exceeds its byte bound")
        path = self.directory / name
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        identity = self.observe(path)
        custody.require(identity["bytes"] == len(raw)
                        and identity["sha256"] == hashlib.sha256(raw).hexdigest(),
                        "retained original source differs from supplied bytes")
        return identity

    def _failure(self, label, command, secondary=None):
        self.stopped = True
        error = OriginalCommandError(command, secondary)
        try:
            custody.write_new(self.command_root / label / "failure-observation.json", {
                "schema": "motionwright.original-pcm-command-failure/1",
                "kind": "DERIVED_FAILURE_OBSERVATION_NOT_REPLACEMENT_ORIGINAL",
                "command": command,
                "secondary_error": None if secondary is None else
                    type(secondary).__name__ + ": " + str(secondary),
            })
        except Exception as retention_error:
            error.add_note("Secondary failure observation could not be retained: "
                           + type(retention_error).__name__)
        return error

    def run(self, argv, label, timeout=45, inputs=()):
        custody.require(not self.stopped, 'prior command failure forbids another command')
        try:
            return self._run(argv, label, timeout, inputs)
        except BaseException:
            # A prelaunch refusal has no invented child observation; it still
            # prevents a caller from continuing this failed execution.
            self.stopped = True
            raise

    def _run(self, argv, label, timeout, inputs):
        custody.require(not self.stopped, 'prior command failure forbids another command')
        custody.require(re.fullmatch(r"[a-z0-9][a-z0-9-]*", label) is not None,
                        "unsafe command label")
        custody.require(isinstance(timeout, (int, float)) and 0 < timeout <= 45,
                        "command timeout exceeds original execution bound")
        # Reserving this whole directory prevents any earlier stdout, stderr,
        # or metadata identity from being reused before a child can start.
        output = self.command_root / label
        output.mkdir(exist_ok=False)
        inputs = list(inputs)
        for original in inputs:
            custody.require(self.observe(self.directory / original["path"]) == original,
                            "original input changed before command")
        executable = shutil.which(str(argv[0]))
        custody.require(executable is not None, "original command executable absent")
        executable = Path(executable).resolve(strict=True)
        binary = {"absolute_path": str(executable),
                  **custody.fingerprint(executable, executable.parent)}
        context = {**self.context, "timeout_seconds": timeout,
                   "original_inputs": inputs, "executable": binary}
        try:
            command = custody.execute(
                [str(executable), *map(str, argv[1:])], self.cwd, dict(os.environ),
                output, label, timeout, context,
            )
        except custody.UnknownObservation as error:
            raise self._failure(label, error.command, error) from error
        if custody.execution_unknown(command):
            raise self._failure(label, command)
        try:
            metadata = self.observe(output / (label + ".command.json"))
            custody.require(custody.original_json(
                output / (label + ".command.json"), output)[0] == command,
                "command metadata differs from observed process")
            for stream in ("stdout", "stderr"):
                self.observe(output / command[stream]["path"])
            stdout = custody.read_original(output, command, "stdout")
            stderr = custody.read_original(output, command, "stderr")
            for original in inputs:
                custody.require(self.observe(self.directory / original["path"]) == original,
                                "original input changed during command")
            custody.require(
                {"absolute_path": str(executable),
                 **custody.fingerprint(executable, executable.parent)} == binary,
                "original command executable changed",
            )
        except Exception as error:
            raise self._failure(label, command, error) from error
        if command.get("observation") != "DIRECT_PROCESS_EXIT_OBSERVED" or command.get("exit_code") != 0:
            raise self._failure(label, command)
        self.commands[label] = metadata
        return {"stdout": stdout, "stderr": stderr, "original": metadata}

    def inventory(self):
        custody.require(not self.stopped, 'failed command cannot produce a passing inventory')
        originals = []
        for path in sorted(self.directory.rglob("*")):
            info = path.lstat()
            custody.require(not stat.S_ISLNK(info.st_mode)
                            and not getattr(info, "st_file_attributes", 0) & 0x400,
                            "linked original inventory entry")
            if not stat.S_ISDIR(info.st_mode):
                originals.append(self.observe(path))
        custody.require(sum(item["bytes"] for item in originals) <= MAX_EVIDENCE,
                        "original PCM evidence exceeds its byte bound")
        return originals
