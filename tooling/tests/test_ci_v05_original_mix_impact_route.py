"""Source-only tests for bounded original mix CI event routing."""

import re
import unittest
from fnmatch import fnmatchcase
from pathlib import Path


WORKFLOW = (Path(__file__).resolve().parents[2] /
            ".github/workflows/v05-original-native-mix.yml")


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def job(text, name, next_name):
    return text.split("  " + name + ":\n", 1)[1].split(
        "  " + next_name + ":\n", 1)[0]


class OriginalMixImpactRouteTests(unittest.TestCase):
    def test_only_changed_native_mix_source_can_trigger_push(self):
        text = workflow_text()
        event = text.split("  push:\n", 1)[1].split("  pull_request:\n", 1)[0]
        self.assertIn(
            "branches: ['integrate/phase1-final-composition-20261027']",
            event)
        source_paths = [line.strip().split("'", 2)[1]
                        for line in event.splitlines()
                        if line.strip().startswith("- '")]
        self.assertEqual(source_paths, [
            "crates/native/src/original_mix.rs",
            "crates/native/src/original_mix/**",
            "crates/native/tests/original_mix.rs"])
        self.assertIn("  workflow_dispatch:", text)
        self.assertIn("  pull_request:", text)

    def test_push_runs_exact_scoped_native_rust_test_only(self):
        text = workflow_text()
        block = job(text, "original-mix-rust-source-impact",
                    "native-source-and-independent-meter")
        self.assertIn("if: $" + "{{ github.event_name == 'push' }}", block)
        self.assertIn("persist-credentials: false", block)
        self.assertIn("toolchain: '1.98.1'", block)
        self.assertIn('test "$GITHUB_REF" = refs/heads/integrate/phase1-final-composition-20261027', block)
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = 1', block)
        self.assertIn("python3 -B -m unittest -q tooling.tests.test_ci_v05_original_mix_impact_route", block)
        self.assertIn("python3 -B tooling/source_policy.py", block)
        self.assertEqual(
            block.count("cargo test --locked -p motionwright-native --test original_mix"),
            1)
        for forbidden in ("ffmpeg", "playwright", "cargo clippy",
                          "native_mix_fixture", "R74", "G2"):
            self.assertNotIn(forbidden.lower(), block.lower())

    def test_original_full_jobs_and_saved_mix_skip_push(self):
        text = workflow_text()
        full = job(text, "native-source-and-independent-meter", "studio-audition")
        studio = job(text, "studio-audition", "saved-mix-g0")
        saved_g0 = job(text, "saved-mix-g0", "saved-mix")
        self.assertIn("github.event_name != 'push'", full)
        self.assertIn("github.event_name != 'push'", studio)
        self.assertIn("if: $" + "{{ github.event_name != 'push' }}", saved_g0)
        self.assertIn("cargo test --locked -p motionwright-native --test original_mix", full)
        self.assertIn("original_mix_e2e.py", full)
        self.assertIn("playwright", studio)
        saved = text.split("  saved-mix:\n", 1)[1]
        self.assertIn("inputs.verification == 'saved-mix'", saved)


    def test_nested_semantic_mix_source_routes_and_selected_unit(self):
        text = workflow_text()
        push = text.split("  push:\n", 1)[1].split(
            "  pull_request:\n", 1)[0]
        pr = text.split("  pull_request:\n", 1)[1].split(
            "  workflow_dispatch:\n", 1)[0]
        for event, segment in (("push", push), ("pull_request", pr)):
            globs = [line.strip().split("'", 2)[1]
                     for line in segment.splitlines()
                     if line.strip().startswith("- '")]
            for path in ("crates/native/src/original_mix/semantic_bus.rs",
                         "crates/native/src/original_mix/future_semantic.rs"):
                with self.subTest(event=event, path=path):
                    self.assertTrue(
                        any(fnmatchcase(path, glob) for glob in globs),
                        f"{event} misses nested source {path}")
        block = job(text, "original-mix-rust-source-impact",
                    "native-source-and-independent-meter")
        self.assertEqual(
            block.count("cargo test --locked -p motionwright-native --lib "),
            1)
        self.assertIn(
            "original_mix::semantic_bus::tests::"
            "additive_semantic_sfx_keeps_valid_negative_pcm16_endpoint_and_rejects_overflow",
            block)
        # The exact test filters all other library tests. Its successful
        # summary legitimately contains a nonzero filtered-out count.
        self.assertNotIn("0 filtered out;'", block)
        match = re.search(
            r'(?m)^\s*grep -Eq \'([^\']+)\' "\$RUNNER_TEMP/'
            r'original-mix-rust-source-impact/semantic-unit\.original\.log"$',
            block)
        self.assertIsNotNone(match, "Selected semantic unit requires a strict summary oracle")
        oracle = re.compile(match.group(1))
        for filtered in (0, 1, 641, 1000):
            good = (f"test result: ok. 1 passed; 0 failed; 0 ignored; "
                    f"0 measured; {filtered} filtered out; finished in 0.01s")
            with self.subTest(filtered=filtered):
                self.assertIsNotNone(oracle.fullmatch(good))
        for bad in (
            "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 642 filtered out; finished in 0.00s",
            "test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 641 filtered out; finished in 0.00s",
            "test result: ok. 1 passed; 0 failed; 1 ignored; 0 measured; 641 filtered out; finished in 0.00s",
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; garbage filtered out; finished in 0.00s",
            "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 640 filtered out; finished in 0.00s",
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 641 filtered out; finished in 0.01s EXTRA",
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(oracle.fullmatch(bad))
        self.assertIn("NOT_RUN", block)
        self.assertIn("test -f crates/native/src/original_mix/semantic_bus.rs", block)


    def test_unity_pcm16_regression_uses_existing_rust_source_run_once(self):
        text = workflow_text()
        block = job(text, "original-mix-rust-source-impact",
                    "native-source-and-independent-meter")
        # Source admission must reuse the original test target once, rather
        # than initiating a second compiled run for a new PCM regression.
        self.assertEqual(
            block.count("cargo test --locked -p motionwright-native --test original_mix"),
            1)
        self.assertIn(
            'cargo test --locked -p motionwright-native --test original_mix'
            ' 2>&1 | tee "$RUNNER_TEMP/original-mix-rust-source-impact/roster.original.log"',
            block)
        self.assertIn(
            "fn unity_gain_original_pcm16_source_retains_exact_quantized_samples()",
            block)
        self.assertIn(
            "if grep -Fq 'fn unity_gain_original_pcm16_source_retains_exact_quantized_samples()' crates/native/tests/original_mix.rs; then",
            block)
        self.assertIn(
            "test \"$(grep -Fxc 'test unity_gain_original_pcm16_source_retains_exact_quantized_samples ... ok' \"$RUNNER_TEMP/original-mix-rust-source-impact/roster.original.log\")\" -eq 1",
            block)
        self.assertIn("NOT_RUN: unity PCM16 source test is absent", block)
        self.assertIn('set -euo pipefail', block)
        self.assertIn('if: always()', block)
        self.assertIn('original-mix-rust-source-impact/', block)


    def test_emitted_pcm16_meter_original_receipt_is_in_same_compiled_roster(self):
        block = job(workflow_text(), "original-mix-rust-source-impact",
                    "native-source-and-independent-meter")
        # The source-gated integration suite already runs once; do not launch
        # a second Cargo test to verify the original PCM16 meter receipt.
        self.assertEqual(
            block.count("cargo test --locked -p motionwright-native --test original_mix"),
            1)
        self.assertIn(
            "if grep -Fq 'fn emitted_pcm16_peak_and_rms_receipts_follow_actual_quantized_samples()' crates/native/tests/original_mix.rs; then",
            block)
        self.assertIn(
            """test "$(grep -Fxc 'test emitted_pcm16_peak_and_rms_receipts_follow_actual_quantized_samples ... ok' "$RUNNER_TEMP/original-mix-rust-source-impact/roster.original.log")" -eq 1""",
            block)
        self.assertIn("NOT_RUN: emitted PCM16 meter source test is absent", block)



    def test_semantic_unit_original_and_digest_are_retained_in_existing_artifact(self):
        text = workflow_text()
        block = job(text, "original-mix-rust-source-impact",
                    "native-source-and-independent-meter")
        original = '"$RUNNER_TEMP/original-mix-rust-source-impact/semantic-unit.original.log"'
        digest = '"$RUNNER_TEMP/original-mix-rust-source-impact/semantic-unit.original.sha256"'
        self.assertEqual(block.count("cargo test --locked -p motionwright-native --lib "), 1)
        self.assertIn("2>&1 | tee " + original, block)
        self.assertIn("grep -Eq ", block)
        self.assertEqual(block.count(original), 3)
        self.assertIn("sha256sum " + original + " > " + digest, block)
        self.assertNotIn('"$RUNNER_TEMP/original-mix-semantic-unit.original.log"', block)
        self.assertIn("path: $" + "{{ runner.temp }}/original-mix-rust-source-impact/", text)

if __name__ == "__main__":
    unittest.main()
