"""Adversarial source-only controls for the real-OS SQLite authority G2 runner."""
import unittest

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


if __name__ == "__main__":
    unittest.main()
