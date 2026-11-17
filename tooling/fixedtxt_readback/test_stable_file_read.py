"""Small offline filesystem/race checks; no original media, grants or Native."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import stable_file_read as reader


def changed_stat(info, **changes):
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size",
              "st_mtime_ns", "st_ctime_ns", "st_file_attributes")
    values = {field: getattr(info, field, 0) for field in fields}
    return SimpleNamespace(**(values | changes))


class StableFileReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fixedtxt-reader-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent = self.root / "selected"
        self.parent.mkdir()
        self.path = self.parent / "fixture.bin"
        self.raw = b"offline-fixture-only"
        self.path.write_bytes(self.raw)

    @contextmanager
    def after_read(self, action):
        inspect = reader._inspect_path

        def observed(path, *, stage):
            if stage == "namespace_after":
                action()
            return inspect(path, stage=stage)

        with patch.object(reader, "_inspect_path", side_effect=observed):
            yield

    def reject(self, *, code, field=None, stage=None, limit=None):
        with self.assertRaises(reader.StableReadRejected) as caught:
            reader.read_stable(self.path, len(self.raw) if limit is None else limit)
        error = caught.exception
        self.assertEqual(error.code, code)
        if field is not None:
            self.assertEqual(error.diagnostic["field"], field)
        if stage is not None:
            self.assertEqual(error.diagnostic["stage"], stage)
        self.assertNotIn(str(self.root), json.dumps(error.diagnostic))
        self.assertNotIn(self.raw.decode(), json.dumps(error.diagnostic))
        return error

    def test_real_full_prefix_and_empty_reads(self):
        self.assertEqual(reader.read_stable(self.path, len(self.raw)), self.raw)
        self.assertEqual(reader.read_stable(self.path, len(self.raw), prefix=7),
                         self.raw[:7])
        self.assertEqual(reader.read_stable(self.path, len(self.raw), prefix=0), b"")
        self.path.write_bytes(b"")
        self.assertEqual(reader.read_stable(self.path, 0), b"")

    def test_unrelated_real_directory_content_change_is_accepted(self):
        before = self.parent.stat()

        def change_directory():
            (self.parent / "sibling").write_bytes(b"unrelated")
            (self.parent / "new-directory").mkdir()
            os.utime(self.parent, ns=(before.st_atime_ns,
                                     before.st_mtime_ns + 2_000_000_000))

        with self.after_read(change_directory):
            self.assertEqual(reader.read_stable(self.path, len(self.raw)), self.raw)
        self.assertNotEqual(self.parent.stat().st_mtime_ns, before.st_mtime_ns)

    def test_directory_metadata_projection_accepts_observed_size_drift(self):
        original_lstat = Path.lstat
        changed = False

        def change():
            nonlocal changed
            changed = True

        def observed(path):
            info = original_lstat(path)
            if changed and path == self.parent:
                return changed_stat(
                    info, st_size=info.st_size + 65536,
                    st_nlink=info.st_nlink + 1,
                    st_mtime_ns=info.st_mtime_ns + 1,
                    st_ctime_ns=info.st_ctime_ns + 1,
                    st_mode=info.st_mode ^ stat.S_IWUSR)
            return info

        with self.after_read(change), patch.object(Path, "lstat", observed):
            self.assertEqual(reader.read_stable(self.path, len(self.raw)), self.raw)

    def test_real_ancestor_replacement_is_rejected_with_component(self):
        def replace_directory():
            retained = self.root / "retained"
            self.parent.rename(retained)
            self.parent.mkdir()
            (retained / self.path.name).rename(self.path)

        with self.after_read(replace_directory):
            error = self.reject(code="input_changed_during_read",
                                field="ino", stage="namespace")
        self.assertEqual(error.diagnostic["component"], len(self.path.parts) - 2)

    def test_real_same_size_leaf_replacement_after_read_is_rejected(self):
        replacement = self.parent / "replacement"
        replacement.write_bytes(self.raw)
        info = self.path.stat()
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.after_read(lambda: replacement.replace(self.path)):
            error = self.reject(code="input_changed_during_read",
                                field="ino", stage="namespace")
        self.assertEqual(error.diagnostic["component"], len(self.path.parts) - 1)

    def test_replacement_before_open_is_rejected_and_descriptor_is_closed(self):
        replacement = self.parent / "replacement"
        replacement.write_bytes(self.raw)
        original_open = os.open
        descriptors = []

        def replaced(path, flags):
            replacement.replace(self.path)
            fd = original_open(path, flags)
            descriptors.append(fd)
            return fd

        with patch.object(reader.os, "open", side_effect=replaced):
            self.reject(code="input_changed_before_read",
                        field="ino", stage="namespace_to_fd")
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_real_same_size_leaf_mutation_during_read_is_rejected(self):
        original_fstat = os.fstat
        calls = 0
        initial = self.path.stat()

        def mutated(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                with self.path.open("r+b") as writer:
                    writer.write(b"x" * len(self.raw))
                os.utime(self.path, ns=(initial.st_atime_ns,
                                      initial.st_mtime_ns + 2_000_000_000))
            return original_fstat(fd)

        with patch.object(reader.os, "fstat", side_effect=mutated):
            self.reject(code="input_changed_during_read",
                        field="mtime_ns", stage="descriptor")

    def test_real_growth_after_first_descriptor_observation_is_rejected(self):
        original_fstat = os.fstat
        calls = 0

        def grown(fd):
            nonlocal calls
            calls += 1
            info = original_fstat(fd)
            if calls == 1:
                with self.path.open("ab") as writer:
                    writer.write(b"overflow")
            return info

        with patch.object(reader.os, "fstat", side_effect=grown):
            self.reject(code="input_changed_during_read",
                        field="size", stage="descriptor")

    def test_real_hardlink_is_rejected_before_open(self):
        os.link(self.path, self.parent / "alias")
        with patch.object(reader.os, "open") as opened:
            self.reject(code="hardlinked_input", field="nlink")
            opened.assert_not_called()

    def test_symlink_reparse_and_wrong_kind_stats_are_rejected_before_open(self):
        # Syscall doubles exercise kinds without requesting Windows link privilege.
        original_lstat = Path.lstat
        variants = (
            (self.parent, {"st_mode": stat.S_IFLNK | 0o777},
             "reparse_or_symlink_path"),
            (self.parent, {"st_file_attributes": 0x400}, "reparse_or_symlink_path"),
            (self.path, {"st_mode": stat.S_IFLNK | 0o777}, "reparse_or_symlink_path"),
            (self.path, {"st_file_attributes": 0x400}, "reparse_or_symlink_path"),
            (self.path, {"st_mode": stat.S_IFDIR | 0o700}, "path_kind"),
        )
        for target, changes, code in variants:
            def observed(path):
                info = original_lstat(path)
                return changed_stat(info, **changes) if path == target else info
            with self.subTest(target=target.name, changes=changes):
                with patch.object(Path, "lstat", observed):
                    with patch.object(reader.os, "open") as opened:
                        self.reject(code=code)
                        opened.assert_not_called()

    def test_ancestor_device_attributes_and_reparse_changes_after_read_reject(self):
        original_lstat = Path.lstat
        for field, value, diagnostic, code in (
                ("st_dev", self.parent.stat().st_dev + 1, "dev",
                 "input_changed_during_read"),
                ("st_file_attributes", 0x100, "attributes",
                 "input_changed_during_read"),
                ("st_file_attributes", 0x400, "attributes",
                 "reparse_or_symlink_path")):
            changed = False

            def change():
                nonlocal changed
                changed = True

            def observed(path):
                info = original_lstat(path)
                return (changed_stat(info, **{field: value})
                        if changed and path == self.parent else info)

            with self.subTest(field=field, value=value):
                with self.after_read(change), patch.object(Path, "lstat", observed):
                    self.reject(code=code, field=diagnostic)

    def test_ctime_difference_between_apis_is_allowed_only_for_windows(self):
        original_fstat = os.fstat

        def observed(fd):
            info = original_fstat(fd)
            return changed_stat(info, st_ctime_ns=info.st_ctime_ns + 123456789)

        for windows in (True, False):
            with self.subTest(windows=windows):
                with patch.object(reader, "_WINDOWS_STAT", windows):
                    with patch.object(reader.os, "fstat", side_effect=observed):
                        if windows:
                            self.assertEqual(reader.read_stable(
                                self.path, len(self.raw)), self.raw)
                        else:
                            self.reject(code="input_changed_before_read",
                                        field="ctime_ns", stage="namespace_to_fd")

    def test_descriptor_ctime_is_temporally_strict_even_on_windows(self):
        original_fstat = os.fstat
        calls = 0

        def observed(fd):
            nonlocal calls
            calls += 1
            info = original_fstat(fd)
            return changed_stat(info, st_ctime_ns=info.st_ctime_ns + calls)

        with patch.object(reader, "_WINDOWS_STAT", True):
            with patch.object(reader.os, "fstat", side_effect=observed):
                self.reject(code="input_changed_during_read",
                            field="ctime_ns", stage="descriptor")

    def test_namespace_ctime_is_temporally_strict_even_on_windows(self):
        original_lstat = Path.lstat
        changed = False

        def change():
            nonlocal changed
            changed = True

        def observed(path):
            info = original_lstat(path)
            return (changed_stat(info, st_ctime_ns=info.st_ctime_ns + 1)
                    if changed and path == self.path else info)

        with patch.object(reader, "_WINDOWS_STAT", True):
            with self.after_read(change), patch.object(Path, "lstat", observed):
                self.reject(code="input_changed_during_read",
                            field="ctime_ns", stage="namespace")

    def test_bounds_reject_before_open_including_prefix_reads(self):
        with patch.object(reader.os, "open") as opened:
            self.reject(code="regular_file_budget", field="size", limit=3)
            with self.assertRaises(reader.StableReadRejected):
                reader.read_stable(self.path, 3, prefix=1)
            for limit, prefix in ((True, None), (-1, None), (10, True),
                                  (10, -1), (10, 11)):
                with self.subTest(limit=limit, prefix=prefix):
                    with self.assertRaises(reader.StableReadRejected):
                        reader.read_stable(self.path, limit, prefix=prefix)
            opened.assert_not_called()

    def test_relative_parent_and_missing_paths_fail_without_path_diagnostics(self):
        invalid = (Path("relative.bin"), self.parent / ".." / "fixture.bin")
        with patch.object(reader.os, "open") as opened:
            for path in invalid:
                with self.subTest(path=str(path)):
                    with self.assertRaises(reader.StableReadRejected) as caught:
                        reader.read_stable(path, 64)
                    self.assertEqual(caught.exception.code, "path_invalid")
            opened.assert_not_called()
        self.path.unlink()
        error = self.reject(code="input_unavailable", field="errno")
        self.assertTrue(error.__suppress_context__)
        self.assertEqual(error.diagnostic["component"], len(self.path.parts) - 1)


if __name__ == "__main__":
    unittest.main()
