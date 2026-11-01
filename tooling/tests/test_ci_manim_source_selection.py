"""Exercise the Manim development source gate without native work or acceptance."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"
CONTROL = "python3 -B -m unittest discover -s tooling/tests -p 'test_ci_manim_source_selection.py' -v"


def bash_path():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        return str(candidate) if candidate.is_file() else None
    return shutil.which("bash")


class DevelopmentSourceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = bash_path()
        if cls.bash is None:
            raise AssertionError("A Bash interpreter is required for the workflow boundary")
        text = WORKFLOW.read_text(encoding="utf-8")
        block = text.split("      - name: Quick source and conformance policies\n", 1)[1]
        block = block.split("      - name: Install pinned private-custody test dependencies outside the checkout\n", 1)[0]
        body = block.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] if line.startswith("          ") else line for line in body.splitlines())
        if script.count(CONTROL) != 1:
            raise AssertionError("The workflow must run the source-selection controls once")
        # Remove only this suite's invocation to avoid recursive execution.
        script = script.replace(CONTROL, "")
        interpreter = shlex.quote(sys.executable.replace("\\", "/"))
        cls.script = script.replace("python3 ", interpreter + " ")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-manim-selection-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.acceptance = {
            "cases": [{"status": "NOT_RUN", "independent": False} for _ in range(60)],
            "expansion_cases": [{"status": "NOT_RUN", "independent": False} for _ in range(64)],
        }
        self.creative = {
            "v05_release_ready": False,
            "requirements": [{"complete_requirement_acceptance": "NOT_COMPLETED"} for _ in range(64)],
        }
        self.environment = {
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MANIM_REQUESTED_SCENARIO": "source-contracts",
            "GITHUB_RUN_ATTEMPT": "1",
        }

    def execute(self, *, environment=None, failures=None):
        paths = [self.root / "docs/acceptance/acceptance-status.json",
                 self.root / "docs/creative-production/requirements-status.json"]
        for path, document in zip(paths, [self.acceptance, self.creative]):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document), encoding="utf-8")
        originals = [path.read_bytes() for path in paths]
        tooling = self.root / "tooling"
        tooling.mkdir(exist_ok=True)
        codes = {"source_policy": 0, "public_surface_policy": 0,
                 "acceptance_policy": 97, "creative_status_policy": 98}
        codes.update(failures or {})
        for name, code in codes.items():
            (tooling / (name + ".py")).write_text(
                "from pathlib import Path\n"
                f"with Path('calls.txt').open('a') as stream: stream.write({name + chr(10)!r})\n"
                f"raise SystemExit({code})\n", encoding="utf-8")
        calls = self.root / "calls.txt"
        calls.unlink(missing_ok=True)
        env = os.environ.copy()
        env.update(self.environment)
        env.update(environment or {})
        result = subprocess.run([self.bash, "--noprofile", "--norc", "-c", self.script],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(originals, [path.read_bytes() for path in paths], "The gate must not rewrite acceptance")
        return result, calls.read_text().splitlines() if calls.exists() else []

    def test_exact_development_source_scope_keeps_acceptance_unasserted(self):
        result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["source_policy", "public_surface_policy"])
        self.assertIn("historical-assessment=STALE_NOT_PROMOTED F-G0=NOT_RUN G3=STOP", result.stdout)

    def test_other_events_refs_scenarios_and_attempts_keep_full_policies(self):
        variants = [("GITHUB_REPOSITORY", "other/motionwright"),
                    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "push"),
                    ("GITHUB_REF", "refs/heads/main"), ("GITHUB_REF", "refs/tags/v1.0.0"),
                    ("MANIM_REQUESTED_SCENARIO", "static-scene"), ("MANIM_REQUESTED_SCENARIO", "camera120"),
                    ("MANIM_REQUESTED_SCENARIO", "camera120-source-cycle"),
                    ("MANIM_REQUESTED_SCENARIO", "editable-source-cycle"),
                    ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", "")]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                result, calls = self.execute(environment={key: value})
                self.assertEqual(result.returncode, 97, result.stderr)
                self.assertEqual(calls, ["source_policy", "public_surface_policy", "acceptance_policy"])
                result, calls = self.execute(environment={key: value}, failures={"acceptance_policy": 0})
                self.assertEqual(result.returncode, 98, result.stderr)
                self.assertEqual(calls[-1], "creative_status_policy")

    def test_source_and_public_surface_failures_always_block(self):
        for policy in ("source_policy", "public_surface_policy"):
            with self.subTest(policy=policy):
                result, calls = self.execute(failures={policy: 83})
                self.assertEqual(result.returncode, 83, result.stderr)
                self.assertEqual(calls[-1], policy)

    def test_any_acceptance_claim_or_incomplete_roster_blocks_development(self):
        base = copy.deepcopy(self.acceptance)
        for key in ("cases", "expansion_cases"):
            for field, value in (("status", "PASS"), ("status", "UNKNOWN"),
                                 ("independent", True), ("independent", 0)):
                with self.subTest(key=key, field=field, value=value):
                    self.acceptance = copy.deepcopy(base)
                    self.acceptance[key][-1][field] = value
                    result, _ = self.execute()
                    self.assertNotEqual(result.returncode, 0)
            self.acceptance = copy.deepcopy(base)
            self.acceptance[key].pop()
            result, _ = self.execute()
            self.assertNotEqual(result.returncode, 0)
        self.acceptance = base

    def test_creative_release_claim_or_incomplete_roster_blocks_development(self):
        base = copy.deepcopy(self.creative)
        for value in (True, "false", 0):
            self.creative = copy.deepcopy(base)
            self.creative["v05_release_ready"] = value
            result, _ = self.execute()
            self.assertNotEqual(result.returncode, 0)
        self.creative = copy.deepcopy(base)
        self.creative["requirements"][-1]["complete_requirement_acceptance"] = "COMPLETED"
        result, _ = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.creative = copy.deepcopy(base)
        self.creative["requirements"].pop()
        result, _ = self.execute()
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
