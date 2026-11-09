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
            r'original-mix-semantic-unit\.original\.log"$',
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


if __name__ == "__main__":
    unittest.main()
