"""Execute the real Manim workflow selection; local fixtures grant no Native credit."""
from __future__ import annotations

import ast
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
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"
CONTROL = "python3 -B -m unittest discover -s tooling/tests -p 'test_ci_manim_source_selection.py' -v"


def bash_path():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        return str(candidate) if candidate.is_file() else None
    return shutil.which("bash")


def step_body(text, name, next_name):
    block = text.split("      - name: " + name + "\n", 1)[1]
    block = block.split("      - name: " + next_name + "\n", 1)[0]
    body = block.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] if line.startswith("          ") else line
                     for line in body.splitlines())


class DevelopmentNativeSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = bash_path()
        if cls.bash is None:
            raise unittest.SkipTest("Bash is required to execute the workflow boundary")
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        script = step_body(cls.text, "Quick source and conformance policies",
                           "Install pinned private-custody test dependencies outside the checkout")
        if script.count(CONTROL) != 1:
            raise AssertionError("Existing source-selection controls must remain wired once")
        control = [line.strip() for line in script.splitlines() if CONTROL in line][0]
        if control != "MW_MANIM_DEVELOPMENT_NATIVE=false " + CONTROL:
            raise AssertionError("Historical source controls must select their own default mode")
        script = script.replace(control, "")
        interpreter = shlex.quote(sys.executable.replace("\\", "/"))
        cls.script = script.replace("python3 ", interpreter + " ")
        probe = "import os; print(os.environ[\"MW_MANIM_DEVELOPMENT_NATIVE\"])"
        cls.control_probe = control.replace(CONTROL, interpreter + " -B -c " + shlex.quote(probe))

        publication = step_body(cls.text, "Verify four original editable phases and sealed custody",
                                "Verify static originals and completed sealed custody")
        code = publication.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        tree = ast.parse(code, filename=str(WORKFLOW))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "development_native_projection"]
        if len(functions) != 1:
            raise AssertionError("The original public summary needs one development projection")
        namespace = {"os": os}
        module = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
        exec(compile(module, str(WORKFLOW), "exec"), namespace)
        cls.project = staticmethod(namespace["development_native_projection"])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="mw-manim-native-selection-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.acceptance = {
            "cases": [{"status": "NOT_RUN", "independent": False} for _ in range(60)],
            "expansion_cases": [{"status": "NOT_RUN", "independent": False} for _ in range(64)],
        }
        self.creative = {
            "v05_release_ready": False,
            "requirements": [{"complete_requirement_acceptance": "NOT_COMPLETED"}
                             for _ in range(64)],
        }
        self.environment = {
            "GITHUB_REPOSITORY": "seradotcom/motionwright",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
            "MW_MANIM_SCENARIO": "editable-source-cycle",
            "GITHUB_RUN_ATTEMPT": "1",
            "MW_MANIM_DEVELOPMENT_NATIVE": "false",
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
        self.assertEqual(originals, [path.read_bytes() for path in paths],
                         "Development selection must not modify recorded acceptance")
        return result, calls.read_text().splitlines() if calls.exists() else []

    def test_flag_is_explicit_boolean_and_default_false(self):
        block = self.text.split("      development_native_diagnostics:\n", 1)[1]
        block = block.split("      affected_rust_diagnostics:", 1)[0]
        self.assertIn("        type: boolean\n", block)
        self.assertIn("        default: false\n", block)
        self.assertIn("MW_MANIM_DEVELOPMENT_NATIVE: ${{ inputs.development_native_diagnostics && 'true' || 'false' }}",
                      self.text)

    def test_source_control_subprocess_does_not_consume_or_clear_native_diagnostic_mode(self):
        environment = os.environ.copy()
        environment["MW_MANIM_DEVELOPMENT_NATIVE"] = "true"
        script = self.control_probe + '; printf "%s\\n" "$MW_MANIM_DEVELOPMENT_NATIVE"'
        result = subprocess.run([self.bash, "--noprofile", "--norc", "-c", script],
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["false", "true"])

    def test_default_native_selection_keeps_both_strict_acceptance_policies(self):
        result, calls = self.execute()
        self.assertEqual(result.returncode, 97, result.stderr)
        self.assertEqual(calls, ["source_policy", "public_surface_policy", "acceptance_policy"])
        result, calls = self.execute(failures={"acceptance_policy": 0})
        self.assertEqual(result.returncode, 98, result.stderr)
        self.assertEqual(calls[-1], "creative_status_policy")

    def test_only_explicit_development_editable_attempt_defers_historical_acceptance(self):
        result, calls = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["source_policy", "public_surface_policy"])
        self.assertIn("formal-G2=NOT_GRANTED G3=STOP release=false", result.stdout)
        self.assertNotIn("dev-manim-source=PASS", result.stdout)

    def test_exact_a4_donor_requires_explicit_native_mode_and_keeps_attempt_boundary(self):
        donor = {"GITHUB_REF": "refs/heads/fix/native-production-a1-211feef-20261101-a4"}
        result, calls = self.execute(environment={**donor, "MW_MANIM_DEVELOPMENT_NATIVE": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["source_policy", "public_surface_policy"])
        self.assertIn("formal-G2=NOT_GRANTED G3=STOP release=false", result.stdout)
        result, calls = self.execute(environment=donor)
        self.assertEqual(result.returncode, 97, result.stderr)
        self.assertEqual(calls[-1], "acceptance_policy")
        for override in (
            {"GITHUB_RUN_ATTEMPT": "2"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_REPOSITORY": "other/motionwright"},
            {"MW_MANIM_SCENARIO": "source-contracts"},
            {"GITHUB_REF": donor["GITHUB_REF"] + "-extra"},
            {"GITHUB_REF": donor["GITHUB_REF"].replace("/heads/", "/tags/")},
        ):
            with self.subTest(override=override):
                result, _ = self.execute(environment={
                    **donor, "MW_MANIM_DEVELOPMENT_NATIVE": "true", **override})
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_existing_development_source_exception_remains_source_only(self):
        result, calls = self.execute(environment={"MW_MANIM_SCENARIO": "source-contracts"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["source_policy", "public_surface_policy"])
        self.assertIn("F-G0=NOT_RUN G3=STOP", result.stdout)

    def test_development_flag_rejects_other_repository_event_branch_scope_or_attempt(self):
        variants = [
            ("GITHUB_REPOSITORY", "other/motionwright"),
            ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/main"), ("GITHUB_REF", "refs/tags/v1.0.0"),
            ("GITHUB_REF", "refs/heads/fix/native-gate-closeout-20261030-a4"),
            ("MW_MANIM_SCENARIO", "source-contracts"), ("MW_MANIM_SCENARIO", "static-scene"),
            ("MW_MANIM_SCENARIO", "camera120"), ("MW_MANIM_SCENARIO", "camera120-source-cycle"),
            ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ATTEMPT", ""),
        ]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                result, calls = self.execute(environment={
                    "MW_MANIM_DEVELOPMENT_NATIVE": "true", key: value})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, ["source_policy", "public_surface_policy"])

    def test_non_boolean_development_flag_never_defers_acceptance(self):
        for value in ("", "1", "TRUE", "yes"):
            with self.subTest(value=value):
                result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": value})
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_source_and_public_surface_errors_always_stop_development(self):
        for policy in ("source_policy", "public_surface_policy"):
            with self.subTest(policy=policy):
                result, calls = self.execute(
                    environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"},
                    failures={policy: 83})
                self.assertEqual(result.returncode, 83, result.stderr)
                self.assertEqual(calls[-1], policy)

    def test_existing_acceptance_or_independence_claim_cannot_be_deferred(self):
        original = copy.deepcopy(self.acceptance)
        for group in ("cases", "expansion_cases"):
            for key, value in (("status", "PASS"), ("status", "UNKNOWN"),
                               ("independent", True), ("independent", 0)):
                with self.subTest(group=group, key=key, value=value):
                    self.acceptance = copy.deepcopy(original)
                    self.acceptance[group][-1][key] = value
                    result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
                    self.assertNotEqual(result.returncode, 0)
            self.acceptance = copy.deepcopy(original)
            self.acceptance[group].pop()
            result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
            self.assertNotEqual(result.returncode, 0)

    def test_creative_release_or_completed_requirement_cannot_be_deferred(self):
        original = copy.deepcopy(self.creative)
        for value in (True, "false", 0):
            self.creative = copy.deepcopy(original)
            self.creative["v05_release_ready"] = value
            result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
            self.assertNotEqual(result.returncode, 0)
        self.creative = copy.deepcopy(original)
        self.creative["requirements"][-1]["complete_requirement_acceptance"] = "COMPLETED"
        result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
        self.assertNotEqual(result.returncode, 0)
        self.creative = copy.deepcopy(original)
        self.creative["requirements"].pop()
        result, _ = self.execute(environment={"MW_MANIM_DEVELOPMENT_NATIVE": "true"})
        self.assertNotEqual(result.returncode, 0)

    def test_development_projection_preserves_physical_facts_without_final_acceptance(self):
        original = {
            "status": "VERIFIED_SCOPED_EDITABLE_SOURCE_CYCLE", "native_effect_invocations": 4,
            "decoded_frame_count_per_phase": 24, "human_acceptance": False,
            "automatic_retry": False, "phases": ["original", "reopen-original", "edited", "reopen-edited"],
            "formal_acceptance": True, "release_authorized": True, "F_selected": True,
            "formal_G2": "PASS", "G3": "PASS",
        }
        before = copy.deepcopy(original)
        with patch.dict(os.environ, {"MW_MANIM_DEVELOPMENT_NATIVE": "true"}):
            result = self.project(original)
        self.assertEqual(original, before, "The physical result must remain unchanged")
        self.assertIsNot(result, original)
        self.assertEqual(result["status"], "VERIFIED_DEVELOPMENT_EDITABLE_SOURCE_CYCLE")
        self.assertNotEqual(result["status"], original["status"])
        self.assertEqual(result["verification_scope"], "development-native-diagnostic")
        for key in ("formal_acceptance", "release_authorized", "F_selected"):
            self.assertIs(result[key], False)
        self.assertEqual(result["formal_G2"], "NOT_GRANTED")
        self.assertEqual(result["G3"], "NOT_GRANTED")
        for key in ("native_effect_invocations", "decoded_frame_count_per_phase",
                    "human_acceptance", "automatic_retry", "phases"):
            self.assertEqual(result[key], original[key])

    def test_development_projection_cannot_promote_unknown_or_incomplete_physical_result(self):
        for status in ("UNKNOWN", "NOT_RUN", "FAILED", "PASS", None):
            with self.subTest(status=status), patch.dict(os.environ, {"MW_MANIM_DEVELOPMENT_NATIVE": "true"}):
                with self.assertRaisesRegex(ValueError, "completed physical verification"):
                    self.project({"status": status})

    def test_default_projection_does_not_rewrite_normal_native_verification(self):
        original = {"status": "VERIFIED_SCOPED_EDITABLE_SOURCE_CYCLE", "phases": [1, 2, 3, 4]}
        with patch.dict(os.environ, {"MW_MANIM_DEVELOPMENT_NATIVE": "false"}):
            self.assertIs(self.project(original), original)


if __name__ == "__main__":
    unittest.main()
