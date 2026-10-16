"""The SFX audio CI must retain the trusted native Browser mount contract."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".github/workflows/native-av-master-e2e.yml"
SFX = ROOT / ".github/workflows/semantic-sfx-native-av.yml"


def job_environment(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if source.count("\nenv:\n") != 1:
        raise AssertionError("expected one top-level pinned runtime environment")
    return source.split("\nenv:\n", 1)[1].split("\njobs:\n", 1)[0]


class PinnedNativeBrowserMountTests(unittest.TestCase):
    def test_specialized_sfx_browser_install_and_render_are_inside_pinned_runtime(self):
        source = job_environment(SFX)
        control = job_environment(BASELINE)
        pin = "  PLAYWRIGHT_BROWSERS_PATH: '0'"
        self.assertIn(pin, source)
        self.assertIn(pin, control)
        self.assertEqual(source.count(pin), 1)
        script = SFX.read_text(encoding="utf-8")
        baseline = BASELINE.read_text(encoding="utf-8")
        for value in (
            "working-directory: _semwright/integrations/motion-canvas/runtime",
            "npm ci --ignore-scripts",
            "npx --no-install playwright install --with-deps firefox",
            "npm run probe",
        ):
            with self.subTest(value=value):
                self.assertIn(value, script)
                self.assertIn(value, baseline)

    def test_no_external_global_browser_cache_override(self):
        script = SFX.read_text(encoding="utf-8")
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH: /home/runner", script)
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH: ~/.cache", script)
        self.assertNotIn("playwright install chromium", script)
        self.assertIn("repository: seradotcom/semwright", script)
        self.assertIn("steps.pin.outputs.sha", script)


if __name__ == "__main__":
    unittest.main()
