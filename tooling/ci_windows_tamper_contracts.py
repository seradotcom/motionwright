"""Run the two closed, offline tamper-evidence suites in both import modes."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tooling"))
from tooling.ci_test_runner import run_suite
from release_manifest import write_new_manifest
from windows_installer_lifecycle import require, source_observation

# Literal counts make accidental omissions fail; the exact method identities
# below are then read from the source-bound definitions and original logs.
ROSTERS = {
    "test_windows_tamper_artifact": {"ArtifactFixtures": 16, "OwnedTransportControls": 3},
    "test_windows_uninstall_tamper": {"WindowsUninstallTamperTests": 23},
}
ZERO_COUNTS = ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")
GITHUB_KEYS = ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
               "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def method_roster(root, module, expected_classes):
    tree = ast.parse((root / "tooling/tests" / (module + ".py")).read_bytes())
    classes = [
        item for item in tree.body if isinstance(item, ast.ClassDef)
        and any(isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
                for method in item.body)
    ]
    require(expected_classes
            and {item.name for item in classes} == set(expected_classes)
            and len(classes) == len(expected_classes), "tamper_contract_class_inventory")
    methods = [
        item.name + "." + method.name for item in classes for method in item.body
        if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
    ]
    all_methods = [
        item.name for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name.startswith("test_")
    ]
    expected_count = sum(expected_classes.values())
    require(all(
        sum(isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
            for method in item.body) == expected_classes[item.name] for item in classes)
        and len(methods) == len(set(methods)) == len(all_methods) == expected_count
        and not any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "load_tests" for item in ast.walk(tree)),
        "tamper_contract_method_inventory")
    return methods


def verify(*, root, source, output):
    before = source_observation(root, source)
    methods = {name: method_roster(root, name, classes)
               for name, classes in ROSTERS.items()}
    report = output / "receipt.json"
    code, receipt = run_suite(
        root, "tooling/tests", [name + ".py" for name in ROSTERS], "both",
        report, source, True, 180)
    entries = [
        (("discovery:" + name + ".py") if mode == "discovery"
         else "dotted:tooling.tests." + name, name, mode)
        for mode in ("discovery", "dotted") for name in ROSTERS
    ]
    require(code == 0 and receipt.get("status") == "PASS"
            and receipt.get("schema") == "motionwright.unittest-execution/1"
            and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and receipt.get("require_complete") is True
            and receipt.get("executed_source_sha") == source
            and receipt.get("tree_sha") == before["tree"]
            and not receipt.get("source_postcheck_error")
            and receipt.get("runner_sha256") == digest(root / "tooling/ci_test_runner.py")
            and receipt.get("test_definition_sha256") == {
                "tooling/tests/" + name + ".py":
                    digest(root / "tooling/tests" / (name + ".py")) for name in ROSTERS}
            and [step.get("entry_point") for step in receipt.get("steps", [])]
                == [entry[0] for entry in entries],
            "tamper_contract_receipt_identity")
    require(all(receipt.get("github", {}).get(key) == os.environ.get(key, "")
                for key in GITHUB_KEYS), "tamper_contract_job_identity")
    inventory = {}
    for index, (step, (entry, module, mode)) in enumerate(zip(receipt["steps"], entries)):
        count = len(methods[module])
        counts = step.get("counts", {})
        require(step.get("status") == "PASS" and type(step.get("exit_code")) is int
                and step["exit_code"] == 0 and counts.get("terminal_status") == "OK"
                and type(counts.get("tests_run")) is int and counts["tests_run"] == count
                and type(counts.get("passed")) is int and counts["passed"] == count
                and all(type(counts.get(key)) is int and counts[key] == 0 for key in ZERO_COUNTS),
                "tamper_contract_omission")
        for stream in ("stdout", "stderr"):
            path = report.with_name(f"{report.stem}-{index:02d}-{stream}.log")
            require(step.get(stream) == {"file": path.name, "sha256": digest(path)},
                    "tamper_contract_log_binding")
        raw = report.with_name(f"{report.stem}-{index:02d}-stderr.log").read_text(encoding="utf-8")
        observed = re.findall(
            r"(?m)^test_\w+ \(([^()\n]+)\) \.\.\. ok$", raw.replace("\r\n", "\n"))
        prefix = module if mode == "discovery" else "tooling.tests." + module
        expected = {prefix + "." + method for method in methods[module]}
        require(len(observed) == count and set(observed) == expected,
                "tamper_contract_original_names")
        inventory[entry] = sorted(observed)
    after = source_observation(root, source)
    require(after == before, "tamper_contract_source_changed")
    write_new_manifest(root, output / "readback.json", {
        "schema": "motionwright.windows-uninstall-tamper-contracts/1",
        "platform": sys.platform, "source_before": before, "source_after": after,
        "receipt_sha256": digest(report), "github": receipt["github"],
        "entrypoint_methods": inventory,
        "unique_controls": sum(map(len, methods.values())), "entrypoints": 4, "skipped": 0,
        "scope": "OFFLINE_ORIGINAL_EVIDENCE_CONTRACTS",
        "artifact_download": "NOT_RUN", "installer_execution": "NOT_RUN",
        "human_acceptance": "NOT_RUN", "release_published": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        verify(root=args.repo, source=args.expected_source, output=args.output)
    except Exception as error:
        print("tamper-contracts=INCOMPLETE " + type(error).__name__, file=sys.stderr)
        return 1
    print("tamper-contracts=PASS installer=NOT_RUN human=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
