"""Adversarial source-only controls for the real-OS SQLite authority G2 runner."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from tooling import ci_sqlite_os_path_g2 as gate


def successful(platform):
    names = gate.CASES[platform]
    body = "running " + str(len(names)) + " tests\n"
    body += "".join(f"test {case} ... ok\n" for case in names)
    body += (
        "\ntest result: ok. " + str(len(names)) +
        " passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    )
    return body.encode()


class PhysicalSQLitePathG2Contracts(unittest.TestCase):
    def test_windows_three_positive(self):
        result = gate.verify_rust_result(successful("windows"), b"", "windows", 0)
        self.assertEqual(result["summary"], [3, 0, 0, 0, 0])
        self.assertCountEqual(result["observed"], gate.CASES["windows"])

    def test_macos_two_positive(self):
        result = gate.verify_rust_result(successful("macos"), b"", "macos", 0)
        self.assertEqual(result["summary"], [2, 0, 0, 0, 0])
        self.assertCountEqual(result["observed"], gate.CASES["macos"])

    def test_nonzero_process_exit_rejected(self):
        with self.assertRaisesRegex(gate.VerificationError, "exit"):
            gate.verify_rust_result(successful("windows"), b"", "windows", 1)

    def test_missing_selected_test_rejected(self):
        original = successful("windows")
        modified = original.replace(
            f"test {gate.CASES['windows'][0]} ... ok\n".encode(), b""
        )
        with self.assertRaisesRegex(gate.VerificationError, "number"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_duplicate_selected_test_rejected(self):
        original = successful("macos")
        line = f"test {gate.CASES['macos'][0]} ... ok\n".encode()
        with self.assertRaisesRegex(gate.VerificationError, "number"):
            gate.verify_rust_result(original.replace(line, line + line), b"", "macos", 0)

    def test_replaced_result_with_ignored_rejected(self):
        original = successful("windows")
        modified = original.replace(b" ... ok\n", b" ... ignored\n", 1)
        with self.assertRaisesRegex(gate.VerificationError, "omitted"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_wrong_test_substituted_rejected(self):
        original = successful("macos")
        modified = original.replace(gate.CASES["macos"][0].encode(), b"unreviewed_test")
        with self.assertRaisesRegex(gate.VerificationError, "omitted"):
            gate.verify_rust_result(modified, b"", "macos", 0)

    def test_terminal_ignored_count_rejected(self):
        original = successful("windows")
        modified = original.replace(
            b"3 passed; 0 failed; 0 ignored", b"2 passed; 0 failed; 1 ignored"
        )
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(modified, b"", "windows", 0)

    def test_filtered_out_test_rejected(self):
        original = successful("macos")
        modified = original.replace(b"0 filtered out", b"1 filtered out")
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(modified, b"", "macos", 0)

    def test_duplicate_terminal_status_rejected(self):
        original = successful("windows")
        tail = b"test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n"
        with self.assertRaisesRegex(gate.VerificationError, "ambiguous"):
            gate.verify_rust_result(original + tail, b"", "windows", 0)

    def test_no_summary_rejected(self):
        original = successful("macos")
        truncated = original.split(b"test result:")[0]
        with self.assertRaisesRegex(gate.VerificationError, "summary"):
            gate.verify_rust_result(truncated, b"", "macos", 0)

    def test_fake_success_without_real_os_rejected(self):
        with self.assertRaisesRegex(gate.VerificationError, "invalid OS scope"):
            gate.verify_rust_result(successful("windows"), b"", "linux", 0)


class CheckoutHistoryContracts(unittest.TestCase):
    """Exercise the runner's ancestry guard against genuine shallow Git clones."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="sqlite-path-history-")
        cls.root = Path(cls.scratch.name)
        cls.repo = cls.root / "origin"
        cls.repo.mkdir()
        cls.env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith("GIT_")}
        cls.env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME="Source fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Source fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        cls.git(cls.repo, "init", "--quiet", "--initial-branch=main")
        (cls.repo / "product.txt").write_text("unchanged product\n", encoding="utf-8", newline="\n")
        cls.git(cls.repo, "add", "product.txt")
        cls.git(cls.repo, "commit", "--quiet", "-m", "Product source")
        cls.base = cls.git(cls.repo, "rev-parse", "HEAD").strip()
        cls.paths = (
            gate.WORKFLOW, "tooling/ci_sqlite_os_path_g2.py",
            "tooling/tests/test_ci_sqlite_os_path_g2.py",
        )
        for name in cls.paths:
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("initial gate\n", encoding="utf-8")
        cls.git(cls.repo, "add", "--", *cls.paths)
        cls.git(cls.repo, "commit", "--quiet", "-m", "Original path gate")
        cls.parent = cls.git(cls.repo, "rev-parse", "HEAD").strip()
        (cls.repo / cls.paths[1]).write_text("repaired gate\n", encoding="utf-8")
        cls.git(cls.repo, "add", "--", cls.paths[1])
        cls.git(cls.repo, "commit", "--quiet", "-m", "Repair checkout history")
        cls.head = cls.git(cls.repo, "rev-parse", "HEAD").strip()

    @classmethod
    def tearDownClass(cls):
        # Git marks object files read-only on Windows; delete only this owned fixture.
        def writable_retry(function, path, _exc):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            function(path)
        shutil.rmtree(cls.root, onerror=writable_retry)
        cls.scratch.cleanup()

    @classmethod
    def git(cls, repo, *args):
        proc = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
            env=cls.env, capture_output=True, timeout=35,
        )
        if proc.returncode:
            raise AssertionError(proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8")

    def setUp(self):
        self.base_patch = mock.patch.object(gate, "SOURCE_BASE", self.base)
        self.parent_patch = mock.patch.object(gate, "PARENT", self.parent)
        self.base_patch.start()
        self.parent_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.addCleanup(self.parent_patch.stop)

    def clone(self, depth):
        destination = self.root / self.id().rsplit(".", 1)[-1]
        self.git(self.root, "clone", "--quiet", "--depth", str(depth),
                 self.repo.as_uri(), str(destination))
        return destination

    def test_original_depth_one_reproduces_missing_ancestry(self):
        checkout = self.clone(1)
        self.assertEqual(self.git(checkout, "rev-list", "--parents", "-n", "1", "HEAD").strip(),
                         self.head)
        with self.assertRaisesRegex(gate.VerificationError, "ancestry"):
            gate.verify_checkout_history(checkout, self.head)

    def test_depth_three_verifies_exact_history_and_product_scope(self):
        checkout = self.clone(3)
        gate.verify_checkout_history(checkout, self.head)
        self.assertEqual(self.git(checkout, "rev-parse", "HEAD").strip(), self.head)

    def test_deepening_same_failed_clone_restores_required_history(self):
        checkout = self.clone(1)
        with self.assertRaises(gate.VerificationError):
            gate.verify_checkout_history(checkout, self.head)
        self.git(checkout, "fetch", "--quiet", "--depth", "3", "origin", "main")
        gate.verify_checkout_history(checkout, self.head)

    def test_depth_two_still_lacks_reviewed_product_base(self):
        checkout = self.clone(2)
        with self.assertRaisesRegex(gate.VerificationError, "Git source lookup"):
            gate.verify_checkout_history(checkout, self.head)

    def test_foreign_expected_sha_is_rejected(self):
        checkout = self.clone(3)
        with self.assertRaisesRegex(gate.VerificationError, "checkout SHA"):
            gate.verify_checkout_history(checkout, self.parent)

    def test_unreviewed_extra_commit_is_rejected(self):
        checkout = self.clone(3)
        self.git(checkout, "commit", "--allow-empty", "--quiet", "-m", "Extra commit")
        new_head = self.git(checkout, "rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(gate.VerificationError, "ancestry"):
            gate.verify_checkout_history(checkout, new_head)

    def test_product_delta_rejected_even_with_reviewed_parent(self):
        checkout = self.clone(3)
        (checkout / "product.txt").write_text("changed product\n", encoding="utf-8")
        self.git(checkout, "add", "product.txt")
        self.git(checkout, "commit", "--quiet", "--amend", "--no-edit")
        new_head = self.git(checkout, "rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(gate.VerificationError, "unreviewed files"):
            gate.verify_checkout_history(checkout, new_head)

    def test_dirty_product_or_untracked_files_rejected(self):
        checkout = self.clone(3)
        (checkout / "product.txt").write_text("dirty product\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.VerificationError, "checkout differs"):
            gate.verify_checkout_history(checkout, self.head)


    def test_crlf_checkout_is_rejected_and_recreated_blob_is_exact(self):
        checkout = self.clone(3)
        source = checkout / "product.txt"
        blob = self.git(checkout, "show", self.head + ":product.txt").encode()
        self.git(checkout, "config", "--local", "core.autocrlf", "true")
        source.unlink()
        # Recreated bytes need fresh index stat data. This does not change the
        # reviewed blob, and lets the strict byte guard observe clean CRLF.
        self.git(checkout, "-c", "core.autocrlf=true", "checkout-index",
                 "--force", "--index", "--", "product.txt")
        self.assertEqual(source.read_bytes(), blob.replace(b"\n", b"\r\n"))
        gate.verify_checkout_history(checkout, self.head)
        self.assertEqual(self.git(checkout, "write-tree").strip(),
                         self.git(checkout, "rev-parse", "HEAD^{tree}").strip())
        with mock.patch.object(gate, "SOURCES", {"product.txt": gate.digest(blob)}), \
                mock.patch.object(gate, "TEST_PATH", {}):
            with self.assertRaisesRegex(gate.VerificationError, "product.txt; local="):
                gate.source_identity(checkout, self.head)
            self.git(checkout, "config", "--local", "core.autocrlf", "false")
            source.unlink()
            self.git(checkout, "checkout-index", "--force", "--index", "--", "product.txt")
            self.assertEqual(source.read_bytes(), blob)
            observed = gate.source_identity(checkout, self.head)
            self.assertEqual(observed["product.txt"]["sha256"], gate.digest(blob))
            self.assertEqual(self.git(checkout, "write-tree").strip(),
                             self.git(checkout, "rev-parse", "HEAD^{tree}").strip())

    def test_second_repair_requires_four_commits_for_reviewed_product_base(self):
        checkout = self.clone(3)
        self.git(checkout, "commit", "--allow-empty", "--quiet", "-m", "Second repair")
        head = self.git(checkout, "rev-parse", "HEAD").strip()
        with mock.patch.object(gate, "PARENT", self.head):
            for depth in (3, 4):
                destination = self.root / ("second-repair-" + str(depth))
                self.git(self.root, "clone", "--quiet", "--depth", str(depth),
                         checkout.as_uri(), str(destination))
                if depth == 3:
                    with self.assertRaisesRegex(gate.VerificationError, "Git source lookup"):
                        gate.verify_checkout_history(destination, head)
                else:
                    gate.verify_checkout_history(destination, head)


class PreflightReceiptContracts(unittest.TestCase):
    def test_rejected_source_has_receipt_and_zero_test_executions(self):
        with tempfile.TemporaryDirectory(prefix="sqlite-preflight-") as scratch:
            root = Path(scratch).resolve()
            report = root / "rejected.json"
            head = "a" * 40
            env = {"GITHUB_SHA": head, "GITHUB_RUN_ID": "123",
                   "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "windows"}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(gate, "check_environment", return_value={}), \
                    mock.patch.object(gate, "source_identity",
                                      side_effect=gate.VerificationError("missing parent")), \
                    mock.patch.object(gate.subprocess, "run") as execute:
                with self.assertRaisesRegex(gate.VerificationError, "missing parent"):
                    gate.run_selected(root, head, "windows", report)
                execute.assert_not_called()
            receipt = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "SOURCE_PREFLIGHT_REJECTED")
            self.assertEqual(receipt["tests_executed"], 0)
            self.assertEqual(receipt["test_status"], "NOT_RUN")
            self.assertFalse(receipt["source_verified"])
            self.assertFalse(receipt["human_acceptance"])
            self.assertFalse(receipt["release_authorized"])


if __name__ == "__main__":
    unittest.main()
