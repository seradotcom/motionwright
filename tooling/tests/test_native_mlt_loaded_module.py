"""Controls for real-process MLT avformat binary attestation (no live PID)."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mlt_loaded_module as verifier

MODULE = "/usr/lib/x86_64-linux-gnu/mlt-7/libmltavformat.so"
INODE = 46553
DEVICE = (8, 1)


def maps(*, swap: bool = False, executable: bool = True,
         inode: int = INODE, deleted: bool = False) -> str:
    path = MODULE + (" (deleted)" if deleted else "")
    blocks = [
        f"7f9b0000-7f9b4000 r--p 00000000 08:01 {inode} {path}",
        f"7f9b4000-7f9c6000 {'r-xp' if executable else 'r--p'} 00004000 08:01 {inode} {path}",
        f"7f9c6000-7f9c9000 r--p 0001a000 08:01 {inode} {path}",
        "7f9ca000-7f9cb000 r-xp 00000000 08:01 98 /lib/libm.so.6",
    ]
    if swap:
        blocks.append(
            "7f9cc000-7f9cd000 r-xp 00000000 08:01 1234 /tmp/libmltavformat.so"
        )
    return "\n".join(blocks)


def argv(output: str = "/work/native-master.mp4") -> bytes:
    return (b"/usr/bin/melt\0"
            b"/work/native-project.mlt\0"
            b"-silent\0"
            b"-consumer\0" + f"avformat:{output}".encode() + b"\0")


class NativeLoadedModuleTests(unittest.TestCase):
    def test_exact_original_module_inode_and_exec_segment_positive(self):
        report = verifier.parse_maps(maps(), MODULE, DEVICE, INODE)
        self.assertEqual(report["mapped_segments"], 3)
        self.assertEqual(report["mapped_module_inode"], INODE)
        self.assertTrue(report["mapped_executable"])

    def test_alternate_mlt_module_not_hidden_behind_valid_original(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "alternate"):
            verifier.parse_maps(maps(swap=True), MODULE, DEVICE, INODE)

    def test_stale_loaded_module_inode_rejected(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "inode"):
            verifier.parse_maps(maps(inode=INODE + 1), MODULE, DEVICE, INODE)

    def test_missing_executable_module_mapping_rejected(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "executable"):
            verifier.parse_maps(maps(executable=False), MODULE, DEVICE, INODE)

    def test_deleted_original_shared_object_rejected(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "alternate"):
            verifier.parse_maps(maps(deleted=True), MODULE, DEVICE, INODE)

    def test_trust_root_cannot_be_replaced_with_mutable_tmp(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "trusted"):
            verifier.parse_maps(maps(), "/tmp/libmltavformat.so", DEVICE, INODE)

    def test_actual_original_consumer_command_is_output_bound(self):
        result = verifier.verified_melt_cmdline(argv(), "/work/native-master.mp4")
        self.assertEqual(result["selected_original_output_path"], "/work/native-master.mp4")
        self.assertEqual(result["actual_original_MLT_consumer"], "avformat")

    def test_different_output_claim_and_duplicate_consumers_fail(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "target"):
            verifier.verified_melt_cmdline(argv("/work/foreign.mp4"), "/work/native-master.mp4")
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "target"):
            verifier.verified_melt_cmdline(
                argv() + b"-consumer\0avformat:/work/native-master.mp4\0",
                "/work/native-master.mp4",
            )

    def test_same_basename_in_a_foreign_output_root_is_rejected(self):
        # Matching a filename is not sufficient authority for this job.
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "target"):
            verifier.verified_melt_cmdline(
                argv("/tmp/foreign-owner/native-master.mp4"), "/work/native-master.mp4"
            )

    def test_bad_pid_arguments_or_missing_bound_command_fail(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "missing"):
            verifier.verified_melt_cmdline(b"/usr/bin/melt\0file.mlt\0", "/work/native-master.mp4")
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "admitted"):
            verifier.verified_melt_cmdline(argv(), "../native-master.mp4")

    def test_original_constrained_environment_accepted(self):
        report = verifier.verified_restricted_environment(
            b"LC_ALL=C\0PATH=/usr/bin:/bin\0MLT_NO_VDPAU=1\0"
        )
        self.assertTrue(report["restricted_loader_overrides_absent"])

    def test_ld_and_mlt_module_override_vectors_rejected(self):
        for key in sorted(verifier.FORBIDDEN_ENV):
            with self.subTest(key=key):
                with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "override"):
                    verifier.verified_restricted_environment(
                        b"LC_ALL=C\0" + key.encode() + b"=/tmp\0"
                    )

    def test_proc_starttime_stays_bound_to_original_process_not_reused_pid(self):
        # The /proc stat comm can itself contain a closing parenthesis. The
        # counter must come from field 22, not from an accidental split.
        fields = ["S"] + [str(i) for i in range(4, 29)]
        observed = b"123 (melt (owned)) " + " ".join(fields).encode() + b"\n"
        self.assertEqual(verifier.parse_process_starttime(observed), 22)
        reused = observed.replace(b" 22 ", b" 230 ", 1)
        self.assertNotEqual(
            verifier.parse_process_starttime(reused),
            verifier.parse_process_starttime(observed),
        )

    def test_proc_stat_missing_original_starttime_fails_closed(self):
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "incomplete"):
            verifier.parse_process_starttime(b"10 (melt) S 1 2 3\n")
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "malformed"):
            verifier.parse_process_starttime(b"garbage with no process stat")
        with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "invalid"):
            verifier.parse_process_starttime(b"")

    def test_sha256_rejects_a_changed_loaded_binary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "libmltavformat.so"
            raw = b"ELF-original-mock" * 10
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(verifier.sha256(path, digest, 4096), digest)
            path.write_bytes(raw + b"x")
            with self.assertRaisesRegex(verifier.LoadedModuleUnverified, "sha256_changed"):
                verifier.sha256(path, digest, 4096)


class _ObservedBinaryPath:
    """Real fixture bytes plus a deterministic simulated pathname replacement."""

    def __init__(self, local: Path, reported: str, replace_after_digest=False):
        self.local = local
        self.reported = reported
        self.name = reported.rsplit("/", 1)[-1]
        self.replace_after_digest = replace_after_digest
        self.changed = None
        self.stat_calls = 0
        self.lstat_calls = 0

    def __fspath__(self):
        return str(self.local)

    def __str__(self):
        return self.reported

    def as_posix(self):
        return self.reported

    def is_absolute(self):
        return self.local.is_absolute()

    def is_file(self):
        return self.local.is_file()

    def is_symlink(self):
        return self.local.is_symlink()

    def resolve(self, strict=False):
        if strict and not self.local.exists():
            raise FileNotFoundError(str(self.local))
        return self

    def open(self, *args, **kwargs):
        return self.local.open(*args, **kwargs)

    def observed_stat(self):
        actual = self.local.stat()
        keys = ("st_dev", "st_ino", "st_mode", "st_size",
                "st_mtime_ns", "st_ctime_ns")
        values = {key: getattr(actual, key) for key in keys}
        if self.changed == "replacement":
            values["st_ino"] += 1
        elif self.changed == "rewrite":
            values["st_mtime_ns"] += 1
            values["st_ctime_ns"] += 1
        return SimpleNamespace(**values)

    def stat(self):
        value = self.observed_stat()
        self.stat_calls += 1
        # The earlier pathname-only implementation hashes A, sees A again,
        # and then consults B in a third stat before comparing mappings.
        if self.replace_after_digest and self.stat_calls == 2:
            self.changed = "replacement"
        return value

    def lstat(self):
        value = self.observed_stat()
        self.lstat_calls += 1
        # The descriptor-backed implementation must retain A even when the
        # pathname changes just after its first post-hash consistency check.
        if self.replace_after_digest and self.lstat_calls == 1:
            self.changed = "replacement"
        return value


class NativeLoadedModuleBindingTests(unittest.TestCase):
    def observe(self, *, replace_after_digest=False, rewrite_after_maps=False):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            melt_file = local / "melt"
            module_file = local / "libmltavformat.so"
            melt_bytes = b"synthetic-melt-for-identity-control"
            module_bytes = b"synthetic-module-A-for-identity-control"
            melt_file.write_bytes(melt_bytes)
            module_file.write_bytes(module_bytes)
            melt = _ObservedBinaryPath(melt_file, "/usr/bin/melt")
            module = _ObservedBinaryPath(
                module_file, MODULE, replace_after_digest=replace_after_digest
            )
            fields = ["S"] + [str(i) for i in range(4, 29)]
            stat_bytes = ("123 (melt) " + " ".join(fields) + "\n").encode()
            files = {name: mock.MagicMock() for name in
                     ("stat", "maps", "cmdline", "environ", "exe")}
            files["stat"].read_bytes.return_value = stat_bytes
            files["cmdline"].read_bytes.return_value = argv()

            def read_maps():
                inode = module.observed_stat().st_ino
                return (
                    f"1000-2000 r-xp 00000000 08:01 {inode} {MODULE}\n"
                ).encode()

            def read_environment():
                if rewrite_after_maps:
                    module.changed = "rewrite"
                return b"LC_ALL=C\0"

            files["maps"].read_bytes.side_effect = read_maps
            files["environ"].read_bytes.side_effect = read_environment
            proc = mock.MagicMock()
            proc.is_dir.return_value = True
            proc.__truediv__.side_effect = lambda name: files[name]
            proc_root = mock.MagicMock()
            proc_root.__truediv__.return_value = proc
            with mock.patch.object(verifier, "Path", return_value=proc_root), \
                    mock.patch.object(verifier.sys, "platform", "linux"), \
                    mock.patch.object(verifier.os, "readlink",
                                      return_value="/usr/bin/melt"), \
                    mock.patch.object(verifier.os, "major", return_value=8,
                                      create=True), \
                    mock.patch.object(verifier.os, "minor", return_value=1,
                                      create=True):
                return verifier.inspect_live_original(
                    123, melt, hashlib.sha256(melt_bytes).hexdigest(),
                    module, hashlib.sha256(module_bytes).hexdigest(),
                    "/work/native-master.mp4",
                )

    def test_same_hashed_descriptor_and_mapping_accept_without_temporal_claim(self):
        receipt = self.observe()
        self.assertTrue(receipt["original_runtime_loaded_module_attested"])
        self.assertEqual(receipt["mapped_native_module"]["mapped_segments"], 1)
        self.assertFalse(receipt["module_installed_before_original_effect"])
        self.assertTrue(receipt["module_installation_order_requires_separate_evidence"])
        self.assertFalse(receipt["native_av_master_decoded_right_tail_verified"])
        self.assertFalse(receipt["human_audio_acceptance"])

    def test_replacement_between_digest_and_mapping_cannot_bind_A_hash_to_B_inode(self):
        with self.assertRaisesRegex(
            verifier.LoadedModuleUnverified, "inode_or_device_changed"
        ):
            self.observe(replace_after_digest=True)

    def test_same_inode_generation_change_after_maps_prevents_receipt(self):
        with self.assertRaisesRegex(
            verifier.LoadedModuleUnverified, "binary_replaced_during_loaded_module_read"
        ):
            self.observe(rewrite_after_maps=True)


if __name__ == "__main__":
    unittest.main()
