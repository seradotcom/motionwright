"""Real Linux FSIZE regressions with inert children; no Native/Host effects."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import native_graph_executable_inputs as inputs
import native_graph_browser_owner as owner
import ci_canvas_service as gate


def historical_input_plan():
    sizes = {"native_provider": 66908064, "manim_runner": 4092912,
             "python3": 8020928, "ffmpeg": 342488}
    return inputs.daemon_staging_contract({role: {"path": "/inert/" + role,
        "sha256": "1" * 64, "size_bytes": size} for role, size in sizes.items()})


@unittest.skipUnless(sys.platform == "linux", "Actual Linux kernel boundary")
class GraphStagingFileLimitsTests(unittest.TestCase):
    def run_child(self, source, *arguments):
        child = subprocess.run([sys.executable, "-I", "-B", "-c",
            "import sys; sys.path.insert(0, " + repr(str(TOOLING)) + ");\n" + source,
            *map(str, arguments)], stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr.decode(errors="replace"))
        return json.loads(child.stdout)

    def test_exact_staging_ceiling_uses_four_elf_sizes_and_preserves_other_caps(self):
        plan = historical_input_plan()
        self.assertEqual(plan["case_hard_file_bytes"], 66908064)
        self.assertEqual(plan["staging_total_bytes"], 79364392)
        for name in ("case_soft_file_bytes", "other_child_file_bytes", "driver_file_bytes"):
            self.assertEqual(plan[name], 4194304)
        self.assertTrue(plan["charged_to_producer_allocation"])
        self.assertTrue(plan["original_capture_limits_unchanged"])
        self.assertEqual(plan["simultaneous_owner_limit"], 1)
        malformed = dict(plan["inputs"])
        malformed["native_provider"] = {**malformed["native_provider"], "size_bytes": 67108865}
        with self.assertRaisesRegex(ValueError, "Graph native input reference"):
            inputs.daemon_staging_contract(malformed)

    def test_hard_four_cannot_be_raised_and_preflight_denies_before_core_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "core-project-would-start"
            result = self.run_child('''import json, pathlib, resource
import native_graph_browser_owner as owner
plan = json.loads(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE, (4194304, 4194304))
try:
    resource.setrlimit(resource.RLIMIT_FSIZE, (4194304, plan["case_hard_file_bytes"]))
except (ValueError, PermissionError):
    denied_raise = True
else:
    denied_raise = False
try:
    owner.verify_staging_file_limits(plan)
    pathlib.Path(sys.argv[2]).write_text("unexpected core effect")
except owner.GraphOwnerFailure as error:
    code = str(error)
else:
    code = "ACCEPTED"
print(json.dumps({"denied_raise": denied_raise, "code": code,
                  "diagnostic": owner.closed_runtime_diagnostic()}))
''', json.dumps(historical_input_plan()), marker)
            self.assertFalse(marker.exists())
            self.assertTrue(result["denied_raise"])
            self.assertEqual(result["code"], "graph-inherited-file-size-limits-incompatible")
            self.assertEqual(result["diagnostic"], {"soft_bytes": 4194304, "hard_bytes": 4194304,
                "required_file_bytes": 66908064, "staging_total_bytes": 79364392,
                "terminal_kind": "NOT_STARTED"})

    def test_actual_elf_copy_fails_at_four_and_succeeds_only_in_staging_child(self):
        # Read one genuine installed ELF. Its source remains untouched and no
        # executable from it is invoked. Both copies are disposable test output.
        binary = Path("/usr/bin/python3").resolve(strict=True)
        size = binary.stat().st_size
        self.assertGreater(size, 4194304)
        self.assertLessEqual(size, 67108864)
        before = hashlib.sha256(binary.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_child('''import errno, hashlib, json, os, pathlib, resource, subprocess
import native_graph_browser_owner as owner
source = pathlib.Path(sys.argv[1]); target = pathlib.Path(sys.argv[2]); ceiling = source.stat().st_size
resource.setrlimit(resource.RLIMIT_FSIZE, (4194304, ceiling))
copy_code = """import errno, hashlib, json, os, pathlib, resource, sys
source, target = map(pathlib.Path, sys.argv[1:])
try:
    with source.open('rb') as original, target.open('xb') as copied:
        while block := original.read(65536):
            copied.write(block)
        copied.flush(); os.fsync(copied.fileno())
except OSError as error:
    if error.errno != errno.EFBIG: raise
    result = {'status':'EFBIG','size':target.stat().st_size}
else:
    result = {'status':'COPIED','size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
result['limits'] = resource.getrlimit(resource.RLIMIT_FSIZE)
print(json.dumps(result))
"""
results = {}
for role, bound in (("ordinary",4194304),("restricted",ceiling)):
    child = subprocess.run([sys.executable,'-I','-B','-c',copy_code,str(source),str(target / role)],
        preexec_fn=owner.owned_child_file_limit_callback(bound),
        capture_output=True, timeout=10)
    if child.returncode != 0: raise RuntimeError(child.stderr.decode())
    results[role] = json.loads(child.stdout)
results['parent_limits'] = resource.getrlimit(resource.RLIMIT_FSIZE)
print(json.dumps(results))
''', binary, temporary)
            self.assertEqual(result["ordinary"], {"status": "EFBIG", "size": 4194304,
                "limits": [4194304, 4194304]})
            self.assertEqual(result["restricted"], {"status": "COPIED", "size": size,
                "sha256": before, "limits": [size, size]})
            self.assertEqual(result["parent_limits"], [4194304, size])
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), before)

    def test_lifecycle_grants_ceiling_only_to_bound_restricted_daemon(self):
        result = self.run_child('''import json, resource
import native_graph_browser_owner as owner
plan=json.loads(sys.argv[1]); resource.setrlimit(resource.RLIMIT_FSIZE,(4194304,plan['case_hard_file_bytes']))
lifecycle=object.__new__(owner.OwnerLifecycle)
lifecycle.staging_plan=plan; lifecycle.restricted_daemon_executable='/source/semwrightd'
bounds={label:lifecycle.child_file_limit(label,['/source/semwrightd']) for label in
        ('owner-bootstrap','owner-config-paths','owner-project-create','client-0001-mount','owner-restricted')}
try: lifecycle.child_file_limit('owner-restricted',['/other/daemon'])
except owner.GraphOwnerFailure: rejected=True
else: rejected=False
print(json.dumps({'bounds':bounds,'rejected':rejected,'parent':resource.getrlimit(resource.RLIMIT_FSIZE)}))
''', json.dumps(historical_input_plan()))
        self.assertEqual(result["bounds"].pop("owner-restricted"), 66908064)
        self.assertEqual(set(result["bounds"].values()), {4194304})
        self.assertTrue(result["rejected"])
        self.assertEqual(result["parent"], [4194304, 66908064])

    def test_constructor_staging_preflight_precedes_bootstrap_project(self):
        tree = ast.parse(Path(owner.__file__).read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GraphBrowserOwner")
        init = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        check = [node.lineno for node in ast.walk(init) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "bind_staging_limits"]
        project = [node.lineno for node in ast.walk(init) if isinstance(node, ast.Constant) and node.value == "project.create"]
        self.assertEqual(len(check), 1)
        self.assertEqual(len(project), 1)
        self.assertLess(check[0], project[0])


class GraphClosedRuntimeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.text = ("OWNER_ERROR:RUNTIME_INVALID OWNER_STAGE:STAGING_LIMITS "
            "OWNER_FSIZE:4194304/4194304 OWNER_STAGING_FILE:66908064 "
            "OWNER_STAGING_TOTAL:79364392 OWNER_TERMINAL:NOT_STARTED")

    def test_closed_numeric_diagnostic_survives_without_private_error_or_capture_permission(self):
        result = gate.graph_closed_runtime_diagnostics(self.text + " /private/TOKEN credential=SECRET")
        self.assertEqual(result, [{"stage": "STAGING_LIMITS", "soft_bytes": 4194304, "hard_bytes": 4194304,
            "required_file_bytes": 66908064, "staging_total_bytes": 79364392, "terminal_kind": "NOT_STARTED"}])
        self.assertNotIn("TOKEN", json.dumps(result))
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("capture_permitted", result[0])

    def test_foreign_stage_terminal_or_oversized_numbers_cannot_cross_diagnostic_boundary(self):
        for text in (self.text.replace("STAGING_LIMITS", "PRIVATE_STAGE"),
                     self.text.replace("NOT_STARTED", "NOT_STARTED_PRIVATE_SECRET"),
                     self.text.replace("66908064", "67108865"),
                     self.text.replace("79364392", "109051905"),
                     self.text.replace("4194304/4194304", "4194304/9007199254740992")):
            with self.subTest(text=text):
                self.assertEqual(gate.graph_closed_runtime_diagnostics(text), [])


if __name__ == "__main__":
    unittest.main()
