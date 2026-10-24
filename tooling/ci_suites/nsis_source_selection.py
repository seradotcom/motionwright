"""Select and admit closed source contracts using the existing runner.

The default retains NSIS/source-lock contracts. Explicit distribution_source
selects the producer contracts, with only the POSIX FIFO inapplicable on
Windows. Platform exclusions never confer execution credit.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import unittest
from tooling.ci_test_runner import (
    git, parse_unittest_summary, run_suite, selected_files, source_state,
)

ROOT = Path(__file__).resolve().parents[2]
NSIS_IDS = tuple([
    "OwnedFilesTests.test_exact_capture_and_include_bind_the_owned_bytes",
    "OwnedFilesTests.test_real_source_changes_or_windows_rename_block_are_observed",
    "OwnedFilesTests.test_unavailable_change_time_never_publishes_an_include",
    "OwnedFilesTests.test_existing_outputs_are_never_overwritten_even_when_empty",
    "OwnedFilesTests.test_concurrent_include_creation_is_not_replaced",
    "OwnedFilesTests.test_path_and_symbol_injection_rejected_before_outputs",
    "OwnedFilesTests.test_devices_ads_and_ambiguous_paths_are_rejected_before_io",
    "OwnedFilesTests.test_real_source_and_parent_links_are_rejected",
    "PredecessorProfileTests.test_profile_binds_all_physical_inputs_and_emits_only_declared_ownership",
    "PredecessorProfileTests.test_absent_profile_produces_an_exclusive_disabled_include",
    "PredecessorProfileTests.test_missing_or_changed_independent_manifest_pin_never_publishes_authority",
    "PredecessorProfileTests.test_profile_for_another_target_cannot_authorize_this_installer",
    "PredecessorProfileTests.test_changed_package_receipt_or_owned_bytes_are_rejected_with_captures_retained",
    "PredecessorProfileTests.test_owned_aliases_role_confusion_missing_self_and_unsafe_paths_are_rejected",
    "PredecessorProfileTests.test_duplicate_json_keys_and_non_file_inputs_do_not_produce_an_include",
    "PredecessorProfileTests.test_preexisting_capture_is_not_replaced_or_reused",
    "PredecessorProfileTests.test_real_cli_uses_the_pinned_profile_and_rejects_argument_mode_confusion",
    "PublicationIntegrityTests.test_changed_capture_with_restored_mtime_is_rejected_before_publication",
    "PublicationIntegrityTests.test_changed_pending_with_restored_mtime_is_rejected_before_publication",
    "PublicationIntegrityTests.test_windows_guards_block_writes_and_capture_deletion_through_publication",
    "PublicationIntegrityTests.test_windows_preexisting_capture_writer_is_rejected_before_publication",
    "PublicationIntegrityTests.test_publication_failure_releases_handles_and_retains_owned_files",
    "PublicationIntegrityTests.test_empty_capture_publishes_the_exact_empty_digest",
    "PublicationIntegrityTests.test_changed_predecessor_capture_is_rejected_before_publication",
    "PublicationIntegrityTests.test_capture_replacement_or_size_change_never_publishes",
    "DistributionOwnedFilesTests.test_real_cli_negative_refuses_before_outputs_and_independent_custody_continues",
    "DistributionOwnedFilesTests.test_real_cli_synthetic_permitted_control_binds_bytes_not_actual_rights",
    "DistributionOwnedFilesTests.test_real_cli_main_mode_and_pinned_context_are_required_without_fallback",
    "DistributionOwnedFilesTests.test_expected_payload_mismatch_refuses_before_any_capture_or_include",
    "DistributionOwnedFilesTests.test_expected_payload_is_read_on_one_retained_descriptor_before_output_creation",
    "DistributionOwnedFilesTests.test_actual_source_mutation_after_prehash_cannot_publish_an_include",
    "DistributionOwnedFilesTests.test_actual_template_selects_required_main_mode_without_environment_interpolation",
    "DistributionOwnedFilesTests.test_admitted_main_capture_mutation_cannot_publish_an_include"
])
SOURCE_LOCK_IDS = tuple([
    "SourceLockAdmissionTests.test_windows_path_and_descriptor_clock_difference_preserves_stable_admission",
    "SourceLockAdmissionTests.test_windows_descriptor_change_time_still_detects_mutation_during_read",
    "SourceLockAdmissionTests.test_windows_path_change_time_still_detects_mutation_during_read",
    "SourceLockAdmissionTests.test_windows_other_path_descriptor_identity_fields_must_still_match",
    "SourceLockAdmissionTests.test_posix_path_descriptor_change_time_mismatch_is_still_rejected",
    "SourceLockAdmissionTests.test_verified_fixed_bytes_are_read_only_and_do_not_launch_external_effects",
    "SourceLockAdmissionTests.test_unknown_or_boolean_outer_schema_is_not_admitted",
    "SourceLockAdmissionTests.test_other_repository_is_refused_even_when_all_record_bindings_match",
    "SourceLockAdmissionTests.test_missing_or_legacy_verification_is_not_admission",
    "SourceLockAdmissionTests.test_unverified_or_unavailable_states_fail_closed",
    "SourceLockAdmissionTests.test_verification_cannot_move_to_another_revision_or_repository",
    "SourceLockAdmissionTests.test_rehashed_evidence_cannot_change_source_identity_or_license",
    "SourceLockAdmissionTests.test_missing_or_changed_evidence_record_is_rejected",
    "SourceLockAdmissionTests.test_missing_or_changed_captured_source_bytes_are_rejected",
    "SourceLockAdmissionTests.test_wrong_git_blob_hash_or_declared_size_is_rejected",
    "SourceLockAdmissionTests.test_boolean_length_is_not_an_evidence_byte_count",
    "SourceLockAdmissionTests.test_evidence_requires_each_distinct_upstream_file",
    "SourceLockAdmissionTests.test_distinct_sources_cannot_share_one_local_evidence_path",
    "SourceLockAdmissionTests.test_rehashed_cargo_bytes_must_keep_declared_version_and_license",
    "SourceLockAdmissionTests.test_duplicate_json_keys_are_rejected_even_with_a_matching_record_hash",
    "SourceLockAdmissionTests.test_evidence_reference_cannot_escape_or_use_an_absolute_path",
    "SourceLockAdmissionTests.test_captured_source_cannot_escape_the_evidence_root",
    "SourceLockAdmissionTests.test_device_names_and_alternate_streams_are_refused_before_open",
    "SourceLockAdmissionTests.test_oversized_evidence_record_is_rejected_with_correct_hash",
    "SourceLockAdmissionTests.test_oversized_source_is_rejected_even_with_updated_proof",
    "SourceLockAdmissionTests.test_hardlinked_source_is_rejected_without_modifying_either_name",
    "SourceLockAdmissionTests.test_symlinked_source_is_rejected_without_modifying_target",
    "SourceLockAdmissionTests.test_symlinked_evidence_parent_is_rejected",
    "SourceLockAdmissionTests.test_source_lock_loader_is_bounded_read_only_and_has_no_external_effects",
    "SourceLockAdmissionTests.test_source_lock_loader_rejects_duplicate_status_before_last_value_wins",
    "SourceLockAdmissionTests.test_small_deep_json_returns_a_static_source_admission_refusal",
    "SourceLockAdmissionTests.test_rehashed_deep_cargo_metadata_returns_a_static_admission_refusal",
    "SourceLockAdmissionTests.test_source_lock_loader_rejects_bytes_above_its_bound",
    "DistributionAdmissionTests.test_synthetic_permitted_control_is_read_only_not_a_real_permission",
    "DistributionAdmissionTests.test_exact_negative_records_refuse_after_valid_source_and_leave_it_valid",
    "DistributionAdmissionTests.test_missing_or_unpinned_record_cannot_supply_disposition",
    "DistributionAdmissionTests.test_source_input_drift_is_distinct_from_source_consistency_failure",
    "DistributionAdmissionTests.test_unverified_source_cannot_be_masked_by_a_negative_disposition",
    "DistributionAdmissionTests.test_scope_binding_rejects_each_mismatched_caller_dimension",
    "DistributionAdmissionTests.test_record_json_rejects_unknown_missing_duplicate_and_oversized_inputs",
    "DistributionAdmissionTests.test_review_is_explicit_bounded_and_does_not_execute_its_text",
    "DistributionAdmissionTests.test_evidence_identity_and_portable_record_path_cannot_be_substituted",
    "DistributionAdmissionTests.test_permitted_requires_a_bounded_payload_and_actual_pinned_build_record",
    "DistributionAdmissionTests.test_rehashed_build_evidence_must_bind_the_same_source_scope_and_payload",
    "DistributionAdmissionTests.test_negative_record_cannot_carry_positive_payload_or_build_authority"
])
ROSTERS = {
    "test_nsis_owned_files": (33, NSIS_IDS, "nsis_source_suite"),
    "test_source_lock_admission": (45, SOURCE_LOCK_IDS, "source_lock_suite"),
}
WINDOWS_ONLY = frozenset({
    "PublicationIntegrityTests.test_windows_guards_block_writes_and_capture_deletion_through_publication",
    "PublicationIntegrityTests.test_windows_preexisting_capture_writer_is_rejected_before_publication",
})
SOURCE_PATHS = (
    ".github/workflows/package-candidate.yml",
    "tooling/ci_test_runner.py",
    "tooling/ci_suites/__init__.py",
    "tooling/ci_suites/nsis_source_selection.py",
    "tooling/ci_suites/nsis_source_suite.py",
    "tooling/ci_suites/source_lock_suite.py",
    "tooling/tests/test_ci_nsis_source_selection.py",
    "tooling/nsis_owned_files.py",
    "tooling/tests/test_nsis_owned_files.py",
    "tooling/source_lock_admission.py",
    "tooling/tests/test_source_lock_admission.py",
    "tooling/public_surface_policy.py",
    "tooling/tests/test_public_surface_policy.py",
    "tooling/package_receipt.py",
    "tooling/release_manifest.py",
    "tooling/v01_release_readiness.py",
    "tooling/source_checkout_observation.py",
    "apps/desktop/src-tauri/windows/installer.nsi",
    "apps/desktop/src-tauri/windows/uninstall-ownership.nsh",
    "Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json",
)
GITHUB_KEYS = ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
               "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")
PREFIX = "MOTIONWRIGHT_NSIS_SELECTION "
ZERO_COUNTS = ("failures", "errors", "skipped", "expected_failures",
               "unexpected_successes")

DISTRIBUTION_IDS = (
    'DistributionBuildRecordTests.test_real_cli_is_deterministic_read_only_and_independent_of_cwd',
    'DistributionBuildRecordTests.test_invalid_source_refuses_before_opening_payload',
    'DistributionBuildRecordTests.test_cli_argument_errors_are_static_and_help_uses_stderr',
    'DistributionBuildRecordTests.test_zero_directory_and_bounded_size_refuse_before_open',
    'DistributionBuildRecordTests.test_real_leaf_and_parent_symlinks_are_rejected',
    'DistributionBuildRecordTests.test_fifo_substitution_after_lstat_refuses_without_blocking',
    'DistributionBuildRecordTests.test_cargo_mutation_during_read_produces_no_stdout_record',
    'DistributionBuildRecordTests.test_real_payload_growth_is_detected_or_prevented_by_windows',
    'DistributionBuildRecordTests.test_real_payload_truncation_is_detected_or_prevented_by_windows',
    'DistributionBuildRecordTests.test_same_size_overwrite_with_restored_mtime_is_detected_or_prevented',
    'DistributionBuildRecordTests.test_real_same_bytes_path_replacement_is_detected_or_prevented',
    'DistributionBuildRecordTests.test_read_and_final_change_time_errors_close_fd_and_emit_no_json',
    'DistributionBuildRecordTests.test_interrupt_and_programming_error_remain_visible_and_close_fd',
    'DistributionBuildRecordTests.test_emitted_bytes_interoperate_with_explicit_synthetic_permission',
    'DistributionBuildRecordTests.test_observation_does_not_override_negative_or_missing_admission',
)
DISTRIBUTION_ROSTERS = {
    'test_distribution_build_record': (15, DISTRIBUTION_IDS, 'distribution_source_suite'),
}
DISTRIBUTION_POSIX_ONLY = frozenset({
    'DistributionBuildRecordTests.test_fifo_substitution_after_lstat_refuses_without_blocking',
})
DISTRIBUTION_SOURCE_PATHS = (
    '.github/workflows/package-candidate.yml',
    'tooling/ci_test_runner.py',
    'tooling/ci_suites/__init__.py',
    'tooling/ci_suites/nsis_source_selection.py',
    'tooling/ci_suites/distribution_source_suite.py',
    'tooling/tests/test_ci_nsis_source_selection.py',
    'tooling/distribution_build_record.py',
    'tooling/tests/test_distribution_build_record.py',
    'tooling/nsis_owned_files.py',
    'tooling/source_lock_admission.py',
    'tooling/tests/test_source_lock_admission.py',
    'tooling/public_surface_policy.py',
    'tooling/tests/test_public_surface_policy.py',
    'Cargo.lock',
    'SOURCE_LOCK.json',
    'apps/studio/package-lock.json',
)

def scope_rosters(scope):
    require(scope in ("nsis_source", "distribution_source"), "unknown source verification scope")
    return ROSTERS if scope == "nsis_source" else DISTRIBUTION_ROSTERS

def scope_source_paths(scope):
    scope_rosters(scope)
    paths = SOURCE_PATHS if scope == "nsis_source" else DISTRIBUTION_SOURCE_PATHS
    require(len(paths) == len(set(paths)), "duplicate source closure member")
    return paths

def module_scope(module):
    if module in ROSTERS:
        return "nsis_source"
    require(module in DISTRIBUTION_ROSTERS, "unknown original source-test module")
    return "distribution_source"

def scope_identities(scope):
    rosters = scope_rosters(scope)
    return [(mode, module,
             "discovery:" + adapter + ".py" if mode == "discovery"
             else "dotted:tooling.ci_suites." + adapter)
            for mode in ("discovery", "dotted")
            for module, (_, _, adapter) in rosters.items()]

def require(condition, message):
    if not condition:
        raise ValueError(message)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def strict_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON member")
        value[key] = item
    return value

def read_original(path, limit=16 * 1024 * 1024):
    """Bound a stable original, refusing symlink/reparse path components."""
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "original path contains an alias")
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit,
            "original is not a bounded regular file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        closed = os.fstat(stream.fileno())
    after = path.stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                              value.st_mtime_ns)
    require(identity(before) == identity(opened) == identity(closed) == identity(after)
            and before.st_ctime_ns == after.st_ctime_ns
            and opened.st_ctime_ns == closed.st_ctime_ns
            and len(raw) == before.st_size, "original changed during read")
    return raw

def source_snapshot(root, expected_source, scope="nsis_source"):
    paths = scope_source_paths(scope)
    source = source_state(root, expected_source)
    tracked = git(root, "ls-files", "--error-unmatch", "--", *paths).splitlines()
    require(sorted(tracked) == sorted(paths), "source closure is incomplete")
    files = {}
    for relative in paths:
        raw = read_original(root / relative)
        files[relative] = {"bytes": len(raw), "sha256": digest(raw)}
    require(source_state(root, source["executed_source_sha"]) == source,
            "source changed during observation")
    return {**source, "files": files}

def validate_definition(raw, expected):
    require(len(expected) == len(set(expected)) and bool(expected),
            "invalid nominal roster")
    tree = ast.parse(raw)
    require(not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "load_tests" for node in ast.walk(tree)),
            "dynamic original load_tests is not admitted")
    methods = [f"{cls.name}.{method.name}" for cls in tree.body
               if isinstance(cls, ast.ClassDef) for method in cls.body
               if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
               and method.name.startswith("test_")]
    test_classes = [cls.name for cls in tree.body if isinstance(cls, ast.ClassDef)
                    and any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and m.name.startswith("test_") for m in cls.body)]
    require(len(test_classes) == len(set(test_classes)), "duplicate test class")
    all_methods = [node for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name.startswith("test_")]
    require(sorted(methods) == sorted(expected) and len(all_methods) == len(expected),
            "source method roster differs from closed contract")

def selection_contract(module, mode, platform):
    scope = module_scope(module)
    rosters = scope_rosters(scope)
    require(mode in ("discovery", "dotted") and platform in ("linux", "win32"),
            "unknown contract or platform")
    total, names, _ = rosters[module]
    require(len(names) == len(set(names)) == total, "closed roster is inconsistent")
    prefix = ("" if mode == "discovery" else "tooling.tests.") + module + "."
    unavailable = frozenset()
    reason = "requires real Windows sharing semantics; mandatory Windows gate"
    if module == "test_nsis_owned_files" and platform == "linux":
        unavailable = WINDOWS_ONLY
    elif scope == "distribution_source" and platform == "win32":
        unavailable = DISTRIBUTION_POSIX_ONLY
        reason = "requires a real POSIX FIFO; mandatory Linux gate"
    require(unavailable.issubset(names), "unknown platform exclusion")
    return {
        "schema": ("motionwright.nsis-source-selection/1" if scope == "nsis_source"
                   else "motionwright.distribution-source-selection/1"),
        "original_module": module, "entry_point": mode, "platform": platform,
        "required_total": total, "required_selected": total - len(unavailable),
        "expected_ids": sorted(prefix + name for name in names),
        "selected_ids": sorted(prefix + name for name in names if name not in unavailable),
        "not_applicable": [{"id": prefix + name, "status": "NOT_APPLICABLE", "reason": reason}
                           for name in sorted(unavailable)],
    }

def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case

def select_original_suite(module, adapter_module):
    require(Path.cwd().resolve() == ROOT and sys.platform in ("linux", "win32"),
            "unexpected checkout or platform")
    scope = module_scope(module)
    rosters = scope_rosters(scope)
    before = source_snapshot(ROOT, None, scope)
    _, _, adapter = rosters[module]
    require(adapter_module in (adapter, "tooling.ci_suites." + adapter),
            "unrecognized adapter entry point")
    mode = "discovery" if adapter_module == adapter else "dotted"
    contract = selection_contract(module, mode, sys.platform)
    # Only this scope's originals are loaded. Dependency fixture modules remain
    # source-bound, but their unrelated test classes receive no execution credit.
    for original, (_, names, _) in rosters.items():
        paths = selected_files(ROOT, "tooling/tests", [original + ".py"])
        require(len(paths) == 1, "ambiguous original test file")
        validate_definition(read_original(paths[0]), names)
    loader = unittest.TestLoader()
    suite = (loader.discover(str(ROOT / "tooling/tests"), pattern=module + ".py")
             if mode == "discovery" else
             loader.loadTestsFromName("tooling.tests." + module))
    original_cases = list(cases(suite))
    ids = [case.id() for case in original_cases]
    require(not loader.errors and sorted(ids) == contract["expected_ids"],
            "original import/discovery or runtime roster differs")
    selected = [case for case in original_cases if case.id() in contract["selected_ids"]]
    require(sorted(case.id() for case in selected) == contract["selected_ids"],
            "selected runtime roster differs")
    require(source_snapshot(ROOT, before["executed_source_sha"], scope) == before,
            "source changed while loading tests")
    print(PREFIX + json.dumps({**contract, "discovered_ids": sorted(ids), "source": before},
                             sort_keys=True), flush=True)
    return unittest.TestSuite(selected)

def parse_selection(stdout):
    text = stdout.decode("utf-8", "strict")
    records = [line[len(PREFIX):] for line in text.splitlines() if line.startswith(PREFIX)]
    require(len(records) == 1, "exactly one original selection record is required")
    record = json.loads(records[0], object_pairs_hook=strict_object)
    require(isinstance(record, dict), "selection record must be an object")
    return record

def executed_test_ids(stderr):
    """Bind every verbose unittest header to one terminal success."""
    header = re.compile(r"^(test_\w+) \(([^()\n]+)\) \.\.\. (.*)$")
    failed_status = re.compile(r"(?:FAIL|ERROR|skipped(?: .*)?|expected failure|unexpected success)")
    observed = []
    pending = None
    for line in stderr.splitlines():
        match = header.fullmatch(line)
        if match:
            require(pending is None, "new test before previous terminal status")
            short, identity, tail = match.groups()
            require(identity.rsplit(".", 1)[-1] == short,
                    "test label and identity differ")
            require(identity not in observed, "duplicate executed identity")
            require(failed_status.fullmatch(tail) is None,
                    "non-successful test status")
            pending = identity
            if tail == "ok":
                observed.append(pending)
                pending = None
            continue
        require(failed_status.fullmatch(line) is None, "non-successful test status")
        if line == "ok":
            require(pending is not None, "orphan test success")
            observed.append(pending)
            pending = None
            continue
        if pending is not None:
            require(not line.startswith(("Ran ", "FAILED", "OK"))
                    and not re.fullmatch(r"-{5,}", line),
                    "test has no terminal success before summary")
    require(pending is None, "unfinished test result")
    return observed


def validate_receipt(root, report, before, after, platform, github, scope="nsis_source"):
    rosters = scope_rosters(scope)
    paths = scope_source_paths(scope)
    require(platform in ("linux", "win32"), "unknown receipt platform")
    require(before == after, "source changed during execution")
    require(set(before) == {"executed_source_sha", "tree_sha", "files"}
            and set(before["files"]) == set(paths), "incomplete source observation")
    require(all(isinstance(row, dict) and set(row) == {"bytes", "sha256"} and type(row.get("bytes")) is int
                and row["bytes"] >= 0 and isinstance(row.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                for row in before["files"].values()), "invalid physical source fingerprint")
    require(all(isinstance(before.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", before[key])
                for key in ("executed_source_sha", "tree_sha")), "invalid source identity")
    require(set(github) == set(GITHUB_KEYS) and all(isinstance(v, str) and v for v in github.values())
            and github["GITHUB_SHA"] == before["executed_source_sha"], "missing job identity")
    raw_receipt = read_original(report)
    receipt = json.loads(raw_receipt, object_pairs_hook=strict_object)
    adapter_paths = ["tooling/ci_suites/" + adapter + ".py" for _, _, adapter in rosters.values()]
    identities = scope_identities(scope)
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("status") == "PASS" and type(receipt.get("exit_code")) is int
            and receipt["exit_code"] == 0 and receipt.get("require_complete") is True
            and receipt.get("executed_source_sha") == before["executed_source_sha"]
            and receipt.get("tree_sha") == before["tree_sha"]
            and receipt.get("github") == github and not receipt.get("source_postcheck_error")
            and receipt.get("runner_sha256") == before["files"]["tooling/ci_test_runner.py"]["sha256"]
            and receipt.get("test_definition_sha256") == {
                path: before["files"][path]["sha256"] for path in adapter_paths}
            and receipt.get("source_locks") == {
                path: before["files"][path]["sha256"] for path in
                ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}
            and [step.get("entry_point") for step in receipt.get("steps", [])]
                == [label for _, _, label in identities],
            "source runner receipt identity or completeness differs")
    require(isinstance(receipt.get("platform"), str)
            and receipt["platform"].startswith("Linux" if platform == "linux" else "Windows"),
            "runner platform differs")
    admitted = []
    for index, (mode, module, _) in enumerate(identities):
        contract = selection_contract(module, mode, platform)
        step = receipt["steps"][index]
        count = contract["required_selected"]
        adapter = rosters[module][2]
        suffix = ["-B", "-E", "-s", "-m", "unittest"]
        suffix += (["discover", "-s", "tooling/ci_suites", "-p", adapter + ".py", "-v"]
                   if mode == "discovery" else ["tooling.ci_suites." + adapter, "-v"])
        command = step.get("command")
        require(isinstance(command, list) and len(command) == len(suffix) + 1
                and isinstance(command[0], str) and bool(command[0])
                and command[1:] == suffix, "original runner command differs")
        expected_counts = {"terminal_status": "OK", "tests_run": count, "passed": count,
                           **{key: 0 for key in ZERO_COUNTS}}
        require(step.get("status") == "PASS" and type(step.get("exit_code")) is int
                and step["exit_code"] == 0 and step.get("counts") == expected_counts
                and all(type(step["counts"].get(key)) is int for key in
                        ("tests_run", "passed", *ZERO_COUNTS)),
                "source tests omitted, skipped or failed")
        streams = {}
        for stream in ("stdout", "stderr"):
            path = report.with_name(f"{report.stem}-{index:02d}-{stream}.log")
            raw = read_original(path)
            require(step.get(stream) == {"file": path.name, "sha256": digest(raw)},
                    "original process log binding differs")
            streams[stream] = raw
        record = parse_selection(streams["stdout"])
        require(record == {**contract, "discovered_ids": contract["expected_ids"],
                            "source": before}, "original selection or source record differs")
        stderr = streams["stderr"].decode("utf-8", "strict").replace("\r\n", "\n").replace("\r", "\n")
        require(parse_unittest_summary(stderr) == expected_counts, "original result summary differs")
        observed = executed_test_ids(stderr)
        require(sorted(observed) == contract["selected_ids"], "original executed names differ")
        admitted.append({"entry_point": step["entry_point"], "executed_ids": sorted(observed),
                         "not_applicable": contract["not_applicable"]})
    admitted_record = {"schema": ("motionwright.nsis-source-admission/1" if scope == "nsis_source"
                                   else "motionwright.distribution-source-admission/1"), "status": "PASS",
            "source_before": before, "source_after": after, "platform": platform,
            "github": github, "receipt_sha256": digest(raw_receipt), "entry_points": admitted,
            "unique_applicable_controls": sum(selection_contract(m, "discovery", platform)["required_selected"] for m in rosters),
            "invocations": sum(len(item["executed_ids"]) for item in admitted),
            "nsis_compilation": "NOT_RUN", "installer_execution": "NOT_RUN",
            "human_acceptance": "NOT_RUN", "release_authorized": False}
    if scope == "distribution_source":
        admitted_record.update(verification_scope=scope, source_tests_only=True,
            distribution_authorization="NOT_GRANTED", payload_build_causality="NOT_PROVEN",
            installed_application="NOT_RUN", signing="NOT_RUN",
            historical_nsis_source_suites="NOT_SELECTED", historical_nsis_success_credit=0)
    return admitted_record

def run_source_gate(root, output, expected_source, scope="nsis_source"):
    rosters = scope_rosters(scope)
    root = root.resolve(strict=True)
    output = output.resolve()
    require(not output.exists() and not output.is_relative_to(root), "existing or in-checkout output refused")
    before = source_snapshot(root, expected_source, scope)
    output.mkdir()
    temporary = output / "temporary"
    temporary.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR", "MOTIONWRIGHT_TEST_ROOT"):
        os.environ[key] = str(temporary)
    report = output / "receipt.json"
    with (output / "source-before.json").open("x", encoding="utf-8") as stream:
        json.dump(before, stream, indent=2, sort_keys=True)
    patterns = [adapter + ".py" for _, _, adapter in rosters.values()]
    code, receipt = run_suite(root, "tooling/ci_suites", patterns, "both", report,
                              expected_source, True, 180)
    # UNKNOWN retains originals and ends here: no additional source query or retry.
    if receipt["status"] == "UNKNOWN":
        return code if type(code) is int and code != 0 else 124
    require(type(code) is int, "source runner exit is not an integer")
    after = source_snapshot(root, expected_source, scope)
    with (output / "source-after.json").open("x", encoding="utf-8") as stream:
        json.dump(after, stream, indent=2, sort_keys=True)
    if code:
        return code
    admitted = validate_receipt(root, report, before, after, sys.platform,
                               {key: os.environ[key] for key in GITHUB_KEYS}, scope)
    with (output / "readback.json").open("x", encoding="utf-8") as stream:
        json.dump(admitted, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": admitted["status"],
        "source_sha": before["executed_source_sha"], "verification_scope": scope,
        "invocations": admitted["invocations"], "platform": sys.platform}))
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--scope", choices=("nsis_source", "distribution_source"), default="nsis_source")
    args = parser.parse_args(argv)
    try:
        return run_source_gate(args.repo, args.output, args.expected_source, args.scope)
    except (ValueError, OSError, SyntaxError) as exc:
        print("Selected source gate refused: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
