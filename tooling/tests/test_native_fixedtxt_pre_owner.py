"""G0 real openat/ZIP-free selection controls, never Native or G2 evidence."""
from pathlib import Path
import ast
import json
import os
import sys
import tempfile
import unittest

import re

TOOLING = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLING))
import native_evidence_projection as core
import native_film180_evidence_selection as selection
import native_fixedtxt_g2_admission as fixed
import native_fixedtxt_workflow as route

def exact_clock_script(workflow_text, job_name):
    """Extract only the reviewed owner's original inline clock without PyYAML.

    The private-evidence custody venv contains no PyYAML. An unrelated job,
    duplicate clock step, missing literal marker, or changed indentation fails
    before a source test can compare the Python AST.
    """
    lines = workflow_text.splitlines()
    header = "  " + job_name + ":"
    if lines.count(header) != 1:
        raise ValueError("FixedTxt reviewed owner job missing or repeated")
    first = lines.index(header) + 1
    limit = next((i for i in range(first, len(lines))
                  if re.fullmatch(r"  [a-z][a-z0-9-]*:", lines[i])), len(lines))
    scope = lines[first:limit]
    name = "      - name: Retain the one original clock before checkout and preparation"
    if scope.count(name) != 1:
        raise ValueError("FixedTxt literal original clock step missing or repeated")
    start = scope.index(name)
    stop = next((i for i in range(start + 1, len(scope))
                 if scope[i].startswith("      - name: ")
                 or scope[i].startswith("      - uses: ")), len(scope))
    step = scope[start:stop]
    if step.count("        id: fixedtxt_clock") != 1 or step.count("        run: |") != 1:
        raise ValueError("FixedTxt original clock step identity missing")
    marker = "          python3 -B - <<'PY_CLOCK'"
    end = "          PY_CLOCK"
    if step.count(marker) != 1 or step.count(end) != 1:
        raise ValueError("FixedTxt inline original clock missing or repeated")
    open_at, close_at = step.index(marker), step.index(end)
    if not open_at < close_at:
        raise ValueError("FixedTxt inline clock order changed")
    body = step[open_at + 1:close_at]
    if not body or any(line and not line.startswith("          ") for line in body):
        raise ValueError("FixedTxt inline clock indentation changed")
    return "\n".join(line[10:] if line else "" for line in body) + "\n"


# Capacity-only data; this is not an admitted request, R28 or Native result.
CAPACITY_REQUEST = {'fixture': 'fixed-multirun-control', 'limits': {'attempt_seconds': 3900, 'capsule_bytes': 268435456, 'custody_seconds': 300, 'files': 1024}, 'role_limits': {'authored-film': 1048576, 'canvas-observations': 4194304, 'case-final-custody': 1048576, 'case-inputs': 1048576, 'compile-component': 1048576, 'compiled-project': 1048576, 'cycle-inputs': 1048576, 'cycle-observation': 1048576, 'decoder-stderr': 65536, 'decoder-terminal': 65536, 'final-custody': 1048576, 'fixture-expectations': 1048576, 'fixture-set': 1048576, 'font-inventory': 1048576, 'font-readiness': 1048576, 'generated-inventory': 1048576, 'host-capture': 2097152, 'host-readback': 2097152, 'host-readback-intent': 1048576, 'host-return-snapshot': 1048576, 'host-session': 1048576, 'host-source-binding': 1048576, 'host-stdin': 2097152, 'host-terminal': 1048576, 'native-frame-manifest': 1048576, 'native-observations': 8388608, 'native-observations-receipt': 1048576, 'native-png': 786432, 'native-result': 1048576, 'owner-close': 1048576, 'owner-closed-original': 1048576, 'owner-shutdown-intent': 1048576, 'owner-shutdown-terminal': 1048576, 'owner-workspace': 1048576, 'owner-workspace-terminal': 1048576, 'project-snapshot-original': 1048576, 'selected-originals': 1048576, 'selected-png': 786432, 'selected-rgba': 8294400, 'selection-plan': 524288}, 'sources': {'caption_source': {'bytes': 35}, 'managed_project': {'bytes': 6675}, 'measurement_protocol': {'bytes': 35228}, 'project_files': [], 'red_native': {'bytes': 33809}, 'template_film': {'bytes': 9584}}, 'review_capsule_bytes': 268435456, 'preparation_stream_bytes': 1048576}

class PreOwnerCapture(unittest.TestCase):
    def roots(self, root):
        result = {name: str(root / name) for name in ("control", "inputs", "observation")}
        for name in ("control", "inputs"):
            Path(result[name]).mkdir(mode=0o700)
        result["owner"] = None
        return result

    @unittest.skipUnless(os.name == "posix", "real no-follow openat requires Linux/POSIX")
    def test_real_prefix_stream_with_absent_owner_is_read_once_and_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            roots = self.roots(Path(temporary))
            observation = Path(roots["observation"])
            observation.mkdir(mode=0o700)
            raw = b"fixed-text-compile-v4 refused: original G0 diagnostic\n"
            name = "fixed.source-fixtures.stderr"
            (observation / name).write_bytes(raw)
            row = {"root": "observation", "relative_path": name,
                   "role": "attempt-original", "maximum": 16384}
            readable, opened = fixed._selected_root_handles(roots)
            try:
                self.assertNotIn("owner", readable)
                captured, identity = fixed._read_admitted_original(readable["observation"], row, name,
                    plan={"fixture": "fixed-multirun-control"})
                self.assertEqual(captured, raw)
                self.assertEqual(identity[5], len(raw))
                self.assertTrue(all(item.stable_roots() for item in opened))
            finally:
                fixed._close_selected_handles(opened)
            self.assertEqual((observation / name).read_bytes(), raw)
            self.assertTrue(all(not item.fds for item in opened))

    @unittest.skipUnless(os.name == "posix", "real no-follow openat requires Linux/POSIX")
    def test_missing_private_mode_alias_and_symlink_observation_keep_control_inputs(self):
        for failure in ("absent", "public-mode", "symlink", "alias"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                roots = self.roots(root)
                control = Path(roots["control"])
                (control / "one.json").write_bytes(b'{"G0":true}\n')
                path = Path(roots["observation"])
                if failure == "public-mode":
                    path.mkdir(mode=0o755)
                    path.chmod(0o755)
                elif failure == "symlink":
                    path.symlink_to(control, target_is_directory=True)
                elif failure == "alias":
                    roots["observation"] = roots["control"]
                readable, opened = fixed._selected_root_handles(roots)
                try:
                    self.assertEqual(set(readable), {"control", "inputs"})
                    raw, _ = readable["control"].read("control", ["one.json"], 100)
                    self.assertEqual(raw, b'{"G0":true}\n')
                finally:
                    fixed._close_selected_handles(opened)

    @unittest.skipUnless(os.name == "posix", "real no-follow openat requires Linux/POSIX")
    def test_stream_overflow_wrong_role_and_neighbor_are_negative_without_truncation(self):
        for failure in ("overflow", "role", "neighbor"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                roots = self.roots(Path(temporary))
                observation = Path(roots["observation"])
                observation.mkdir(mode=0o700)
                name = "fixed.source-fixtures.stderr" if failure != "neighbor" else "other.stderr"
                raw = b"x" * (16385 if failure == "overflow" else 8)
                (observation / name).write_bytes(raw)
                row = {"root": "observation", "relative_path": name,
                    "role": "owner-original" if failure == "role" else "attempt-original", "maximum": 16384}
                readable, opened = fixed._selected_root_handles(roots)
                try:
                    with self.assertRaises(ValueError):
                        fixed._read_admitted_original(readable["observation"], row, name,
                            plan={"fixture": "fixed-multirun-control"})
                finally:
                    fixed._close_selected_handles(opened)
                self.assertEqual((observation / name).read_bytes(), raw)

    def test_prefix_does_not_admit_a_case_host_png_or_other_fixture(self):
        allowed = fixed._pre_owner_observation_paths("fixed-multirun-control")
        self.assertEqual(len(allowed), 11)
        self.assertTrue({name for name, _ in fixed.PRE_OWNER_DIAGNOSTICS} <= allowed)
        self.assertTrue({"fixed-multirun-control/owner-workspace.json",
            "fixed-multirun-control/native-result.host-stdin/capture-0001.stdin",
            "fixed-multirun-control/fixed-text-original-readback/frame-000000.original.png",
            "canonical-fixture-set/fixtures/fixed-causal/film.original.json"}.isdisjoint(allowed))

    def test_all_twelve_capacities_and_inline_reservations_keep_original_physical_limits(self):
        source = (TOOLING / "native_fixedtxt_workflow.py").read_text()
        node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                    and node.name == "preflight_request_capacity")
        request = json.loads(json.dumps(CAPACITY_REQUEST))
        fixture_names = selection.FIXEDTXT_KEYS
        self.assertEqual(sum(bound for _, bound in fixed.PRE_OWNER_DIAGNOSTICS), 61440)
        for fixture in fixture_names:
            request["fixture"] = fixture
            request["role_limits"]["native-png"] = request["role_limits"]["selected-png"] = (
                750 if fixture == "fixed-counter-captions" else 768) * 1024
            capacity = route.preflight_request_capacity(request, session=True, source_zip_bytes=86035)
            self.assertEqual(capacity["mandatory_role_counts"]["native-png"], 180)
            self.assertEqual(capacity["mandatory_role_counts"]["selected-rgba"], len(selection.fixedtxt_frames(fixture)))
            self.assertLess(capacity["declared_capsule_bound"], 256 * 1024**2)
            if fixture == "fixed-counter-captions":
                self.assertEqual(capacity["declared_capsule_bound"], 268421184)
        workflow = (TOOLING.parent / ".github/workflows/native-mlt-sequence-e2e.yml").read_text(encoding="utf-8")
        for job in ("fixedtxt-original-owner", "fixedtxt-session-owner"):
            clock = exact_clock_script(workflow, job)
            inline = [item for item in ast.parse(clock).body
                      if isinstance(item, ast.FunctionDef)
                      and item.name == "preflight_request_capacity"]
            self.assertEqual(len(inline), 1)
            self.assertEqual(ast.dump(inline[0], include_attributes=False),
                             ast.dump(node, include_attributes=False))


    def test_pr_source_contract_includes_original_clock_and_new_owner_modules(self):
        workflow = (TOOLING.parent / ".github/workflows/native-mlt-sequence-e2e.yml").read_text(encoding="utf-8")
        paths = workflow.split("  pull_request:", 1)[1].split("\npermissions:", 1)[0]
        for item in ("tooling/native_fixedtxt_observer_overlay.py",
                     "tooling/tests/test_native_fixedtxt_pre_owner.py",
                     "tooling/tests/test_native_owner_release.py",
                     "tooling/tests/test_native_fixedtxt_external_g2.py"):
            with self.subTest(path=item):
                self.assertIn("      - '" + item + "'", paths)
                if item.startswith("tooling/tests/"):
                    self.assertIn("--pattern " + Path(item).name, workflow)
        self.assertIn("'dotted:tooling.tests.test_native_fixedtxt_pre_owner':6", workflow)
        self.assertIn("'dotted:tooling.tests.test_native_owner_release':4", workflow)
        complete = {"test_native_fixedtxt_external_g2.py": 22,
                    "test_native_fixedtxt_workflow.py": 39,
                    "test_native_fixedtxt_pre_owner.py": 6,
                    "test_native_owner_release.py": 4}
        for filename, count in complete.items():
            with self.subTest(source_roster=filename):
                self.assertIn("'dotted:tooling.tests." + Path(filename).stem +
                              "':" + str(count), workflow)
                tree = ast.parse((TOOLING / "tests" / filename).read_text(encoding="utf-8"))
                selected = sum(1 for cls in tree.body if isinstance(cls, ast.ClassDef)
                               for item in cls.body if isinstance(item, ast.FunctionDef)
                               and item.name.startswith("test_"))
                self.assertEqual(selected, count)
        self.assertEqual(sum(complete.values()), 71)
        for job in ("fixedtxt-original-owner", "fixedtxt-session-owner"):
            self.assertEqual(len([n for n in ast.parse(exact_clock_script(workflow, job)).body
                                  if isinstance(n, ast.FunctionDef)
                                  and n.name == "preflight_request_capacity"]), 1)
        with self.assertRaises(ValueError):
            exact_clock_script(workflow.replace(
                "  fixedtxt-session-owner:", "  other-session-owner:", 1), "fixedtxt-session-owner")
        with self.assertRaises(ValueError):
            exact_clock_script(workflow.replace(
                "      - name: Retain the one original clock before checkout and preparation",
                "      - name: Changed original clock identity", 1), "fixedtxt-original-owner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
