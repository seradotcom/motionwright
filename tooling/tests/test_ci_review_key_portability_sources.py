"""Keep R74 review-key portability checks triggered by their actual inputs.

Tests here are source-only; they never open a reviewer key, fetch an artifact,
publish a GitHub status, or admit Graph/Native work.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/review-key-portability.yml"
SEAL = ROOT / "tooling/ci_private_evidence.py"
CONSUMERS = (
    ROOT / "tooling/graph_r74_external_reviewer.py",
    ROOT / "tooling/graph_review_artifact.py",
)
INERT_TEST = "tooling.tests.test_ci_review_key_portability_sources"


def _event_paths(source: str, event: str) -> tuple[str, ...]:
    # Ignore YAML 1.1's boolean treatment of on; the exact top-level
    # GitHub source structure is intentionally inspected here.
    marker = "  " + event + ":\n"
    assert source.count(marker) == 1, event
    text = source.split(marker, 1)[1]
    if event == "push":
        text = text.split("  pull_request:", 1)[0]
    else:
        text = text.split("\npermissions:", 1)[0]
    return tuple(re.findall(r"^\s{6}- '([^']+)'$", text, re.M))


class ReviewSealCITriggerTests(unittest.TestCase):
    def test_both_events_watch_real_evidence_seal_and_this_regression(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        required = (
            "tooling/ci_private_evidence.py",
            "tooling/tests/test_ci_review_key_portability_sources.py",
            "tooling/graph_r74_external_reviewer.py",
            "tooling/graph_review_artifact.py",
            "tooling/review_private_key.py",
            "tooling/review_original_io.py",
            "tooling/nsis_owned_files.py",
        )
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                paths = _event_paths(source, event)
                for path in required:
                    self.assertIn(path, paths, (event, path))

    def test_inert_regression_runs_without_fixtures_first(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        check = "python -B -m unittest " + INERT_TEST + " -v"
        self.assertEqual(source.count(check), 1)
        self.assertLess(
            source.index(check),
            source.index("Install hash-pinned artifact fixture dependencies"),
        )

    def test_seal_is_current_actual_review_dependency_not_legacy_glob(self):
        self.assertTrue(SEAL.is_file())
        for consumer in CONSUMERS:
            with self.subTest(consumer=consumer.name):
                source = consumer.read_text(encoding="utf-8")
                self.assertRegex(
                    source,
                    r"(?:^|\n)(?:import ci_private_evidence as seal"
                    r"|from ci_private_evidence import )",
                )


if __name__ == "__main__":
    unittest.main()
