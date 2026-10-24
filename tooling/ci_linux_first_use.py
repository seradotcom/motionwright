"""Closed Linux first-use source gate and one disposable installed attempt.

The source gate preserves unittest originals. The installed gate delegates all
interaction and SQLite semantics to the reviewed accessible/cold-start helpers.
An interrupted or unconfirmed installed attempt is never retried by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import ci_test_runner as runner
    from . import source_checkout_observation as observation
    from . import storage_schema_contract as storage
else:
    import ci_test_runner as runner
    import source_checkout_observation as observation
    import storage_schema_contract as storage

TEST_PATH = "tooling/tests/test_linux_first_use_accessible.py"
TEST_MODULE = "tooling.tests.test_linux_first_use_accessible"
PATTERN = "test_linux_first_use_accessible.py"
SOURCE_JOB = "linux-first-use-source"
SOURCE_SCHEMA = "motionwright.linux-first-use-source/1"
INSTALL_SCHEMA = "motionwright.linux-first-use-installed/1"
G0_PATTERNS = (
    "test_public_surface_policy.py", "test_creative_status_policy.py",
    "test_acceptance_session.py", "test_acceptance_evidence_identity.py",
    "test_native_av_media_probe.py", "test_verify_delivered_media.py",
    "test_runtime_first_use_audit.py", "test_ci_job_plan.py",
    "test_source_policy_credentials.py",
)
FIRST_USE_G0_PATTERN = "test_runtime_first_use_audit.py"
FIRST_USE_ATOMIC_PATH = "tooling/tests/test_runtime_first_use_atomic_receipt.py"
FIRST_USE_G0_CASES = frozenset((
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_cli_preserves_uncertainty_after_real_receipt_publication',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_cli_refusal_keeps_preexisting_report_unchanged',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_failed_publication_preserves_a_foreign_destination',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_first_fsync_sees_no_final_public_name',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_fsync_error_never_exposes_partial_final_receipt',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_post_link_readback_failure_preserves_complete_receipt',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_post_publish_directory_sync_error_keeps_complete_report',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_stage_link_failure_does_not_publish_or_keep_partial',
    'test_runtime_first_use_atomic_receipt.AtomicSupportReceiptTests.test_two_simultaneous_writers_have_one_complete_winner',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_cli_failure_returns_code_not_owner_path',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_cli_prints_sanitized_json_and_never_session_or_paths',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_connection_file_swapped_after_descriptor_open_is_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_connection_inode_swap_after_path_preflight_fails_closed',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_connection_resource_mismatch_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_duplicate_json_keys_rejected_instead_of_last_one_winning',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_excess_cli_output_rejected_without_echo',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_executable_inode_swap_after_path_preflight_fails_closed',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_explicit_preflight_is_read_only_and_never_claims_broker_ready',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_invalid_expected_digest_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_invalid_project_uuid_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_mismatched_version_fails_closed',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_missing_connection_field_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_missing_executable_is_fail_closed',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_missing_output_root_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_missing_socket_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_modified_executable_rejected_by_streamed_digest',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_mutually_exclusive_selection_modes_required',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_no_output_receipt_created_without_explicit_output_option',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_noncanonical_generation_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_old_generation_registry_rejected_without_env_fallback',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_other_project_registry_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_oversized_session_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_real_subprocess_arguments_are_readonly_and_secret_free',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_receipt_destination_must_be_absolute',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_receipt_is_exclusive_and_existing_bytes_are_not_replaced',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_registry_reference_inode_swap_after_preflight_fails_closed',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_registry_unknown_fields_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_registry_version_cannot_silently_upgrade',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_relative_connection_path_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_relative_session_path_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_saved_reference_does_not_replace_a_missing_owner_file',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_saved_registry_reference_passes_scope_validation',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_saved_relative_file_reference_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_schema_mismatch_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_session_file_swapped_after_descriptor_open_is_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_symlink_ancestor_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_symlink_connection_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_unknown_connection_field_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_unknown_source_lock_version_not_claimed_supported',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_version_probe_only_with_explicit_opt_in',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_world_readable_session_rejected',
    'test_runtime_first_use_audit.FirstUseAuditTests.test_world_writable_output_root_rejected',
))
PATHS = (
    ".github/workflows/package-candidate.yml",
    "tooling/ci_linux_first_use.py", "tooling/tests/test_ci_linux_first_use.py",
    "tooling/runtime_first_use_audit.py", FIRST_USE_ATOMIC_PATH,
    "tooling/ci_test_runner.py", "tooling/linux_first_use_accessible.py", TEST_PATH,
    "tooling/linux_cold_start.py", "tooling/linux_gui_probe.py",
    "tooling/source_checkout_observation.py", "tooling/storage_schema_contract.py",
    "tooling/package_receipt.py", "tooling/candidate_smoke.py",
    "SOURCE_LOCK.json", "Cargo.lock", "apps/studio/package-lock.json",
) + tuple("tooling/tests/" + pattern for pattern in G0_PATTERNS)
LOCKS = ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")
EXPECTED_CASES = frozenset((
    "SelectionAndAttemptTests.test_exact_target_one_edit_one_action_and_no_commit_claim",
    "SelectionAndAttemptTests.test_same_title_other_application_has_no_authority",
    "SelectionAndAttemptTests.test_duplicate_applications_never_select_first",
    "SelectionAndAttemptTests.test_duplicate_dialogs_never_select_first",
    "SelectionAndAttemptTests.test_duplicate_inputs_never_select_first",
    "SelectionAndAttemptTests.test_duplicate_buttons_never_select_enabled_one",
    "SelectionAndAttemptTests.test_wrong_label_cannot_be_used_as_input",
    "SelectionAndAttemptTests.test_hidden_input_cannot_be_used",
    "SelectionAndAttemptTests.test_control_from_another_application_is_blocked",
    "SelectionAndAttemptTests.test_input_must_be_editable_and_have_both_interfaces",
    "SelectionAndAttemptTests.test_existing_input_text_is_not_overwritten",
    "SelectionAndAttemptTests.test_successful_setter_without_observed_text_never_creates",
    "SelectionAndAttemptTests.test_disabled_create_waits_for_real_state_without_reediting",
    "SelectionAndAttemptTests.test_pid_reuse_before_edit_is_rejected",
    "SelectionAndAttemptTests.test_identity_is_rechecked_after_selection_before_edit",
    "SelectionAndAttemptTests.test_pid_reuse_after_edit_never_clicks",
    "SelectionAndAttemptTests.test_replaced_input_with_same_label_and_text_cannot_inherit_intent",
    "SelectionAndAttemptTests.test_ambiguous_activation_action_is_refused",
    "SelectionAndAttemptTests.test_unrecognized_button_action_is_refused",
    "SelectionAndAttemptTests.test_setter_timeout_cannot_be_retried",
    "SelectionAndAttemptTests.test_action_timeout_cannot_be_retried",
    "SelectionAndAttemptTests.test_false_setter_is_not_no_effect_permission_to_retry",
    "SelectionAndAttemptTests.test_false_action_is_unconfirmed_and_never_retried",
    "SelectionAndAttemptTests.test_edit_journal_failure_prevents_effect",
    "SelectionAndAttemptTests.test_create_journal_failure_prevents_button_effect",
    "SelectionAndAttemptTests.test_restart_on_same_journal_is_refused_even_with_new_binding",
    "SelectionAndAttemptTests.test_expired_deadline_cannot_send_edit",
    "SelectionAndAttemptTests.test_expired_deadline_after_edit_cannot_send_create",
    "SelectionAndAttemptTests.test_tree_limit_prevents_unbounded_cycle_or_partial_selection",
    "SelectionAndAttemptTests.test_tree_read_error_does_not_hide_other_matches",
    "X11IdentityTests.test_pid_and_start_time_are_both_bound",
    "X11IdentityTests.test_duplicate_window_titles_are_ambiguous",
    "X11IdentityTests.test_pid_absence_or_zero_is_not_title_authority",
    "ColdOracleIntegrationContractTests.test_reuses_same_data_and_real_oracles_with_no_second_interaction",
    "ColdOracleIntegrationContractTests.test_worker_timeout_stops_without_relaunch_or_fresh_retry",
    "ColdOracleIntegrationContractTests.test_existing_scratch_is_preserved_without_launch",
    "JournalFilesystemTests.test_exclusive_entries_keep_original_bytes",
    "SourceCohortIntegrationTests.test_shared_oracle_requires_explicit_current_source_cohort",
    "SourceCohortIntegrationTests.test_explicit_cohort_reaches_readiness_and_both_real_sqlite_snapshots",
    "SourceCohortIntegrationTests.test_foreign_source_cohort_is_rejected_before_launch_or_scratch",
    "SourceCohortIntegrationTests.test_cli_admits_source_before_probe_and_rechecks_before_publication",
    "SourceCohortIntegrationTests.test_cli_source_drift_retains_originals_and_does_not_publish",
    "WorkerBudgetTests.test_slow_initialization_cannot_renew_budget_or_edit",
    "WorkerBudgetTests.test_waiting_after_initialization_uses_remaining_budget",
    "WorkerBudgetTests.test_registration_stops_at_original_deadline",
    "WorkerBudgetTests.test_available_budget_keeps_one_edit_and_one_action",
    "WorkerBudgetTests.test_missing_reply_stays_unknown_without_second_effect",
    "WorkerBudgetTests.test_inherited_deadline_includes_process_startup_time",
    "WorkerBudgetTests.test_parent_cannot_extend_worker_local_bound",
    "WorkerBudgetTests.test_already_expired_parent_cannot_initialize_or_mutate",
    "WorkerBudgetTests.test_nonfinite_or_nonpositive_parent_deadline_is_refused",
    "BridgeBudgetTests.test_large_tree_stops_between_native_child_reads",
    "BridgeBudgetTests.test_expired_budget_prevents_native_setter",
    "BridgeBudgetTests.test_text_count_cannot_authorize_read_after_deadline",
    "ParentDeadlineContractTests.test_parent_passes_one_absolute_budget_with_custody_reserve",
    "ParentDeadlineContractTests.test_hidden_worker_cli_preserves_parent_deadline",
))
CLAIMS = {
    "semwright_production_runtime": "NOT_CONFIGURED",
    "native_renderer_execution": "NOT_RUN", "human_install_acceptance": "NOT_RUN",
    "human_creative_acceptance": "NOT_RUN", "fresh_physical_device": "NOT_PROVEN",
    "signed_distribution": "NOT_CLAIMED",
}


class GateError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise GateError(reason)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    identity = observation.regular_file(path, maximum=4 * 1024 * 1024)
    raw = path.read_bytes()
    require(identity == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            "json_changed_during_read")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs), identity


def load_json(path):
    return read_json(path)[0]


def publish(path, value):
    """Create and fsync a record; never replace an earlier intent or terminal."""
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def environment(*, installed=False):
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
            "disposable_linux_actions_required")
    require(Path(sys.executable).resolve(strict=True) == Path("/usr/bin/python3").resolve(strict=True),
            "system_python_required_for_apt_gi")
    names = ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    result = {name: os.environ.get(name, "") for name in names}
    require(re.fullmatch(r"[0-9a-f]{40}", result["GITHUB_SHA"]), "full_source_sha_required")
    require(re.fullmatch(r"[1-9][0-9]*", result["GITHUB_RUN_ID"])
            and re.fullmatch(r"[1-9][0-9]*", result["GITHUB_RUN_ATTEMPT"])
            and result["GITHUB_WORKFLOW_REF"], "run_identity_required")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "explicit_dispatch_required")
    if installed:
        require(result["GITHUB_RUN_ATTEMPT"] == "1", "installed_attempt_one_only_no_replay")
    return result


def fresh_directory(path, repo):
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    path = path.absolute()
    require(path == path.resolve() and path != temp and path.is_relative_to(temp),
            "fresh_namespace_must_be_below_runner_temp_without_links")
    require(not path.is_relative_to(repo.resolve(strict=True)), "evidence_must_be_outside_checkout")
    require(path.parent.is_dir(), "namespace_parent_missing")
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def observe(repo, source):
    before = observation.source_observation(repo, source, PATHS)
    cohort = storage.admit_checkout(repo, source)
    storage.validate_cohort(cohort, source_sha=source, source_tree=before["tree"])
    return {"source": before, "storage_cohort": cohort}


def require_same(before, after):
    require(before == after, "source_or_storage_cohort_changed")


def installed_namespace(github):
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    return temp / ("linux-first-use-" + github["GITHUB_RUN_ID"] + "-"
                   + github["GITHUB_RUN_ATTEMPT"] + "-" + github["GITHUB_SHA"])


def original_log(folder, ref):
    require(isinstance(ref, dict) and set(ref) == {"file", "sha256"}, "log_identity_fields")
    name = ref["file"]
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            and name not in (".", ".."), "log_name_not_a_basename")
    path = folder / name
    actual = observation.regular_file(path, maximum=8 * 1024 * 1024)
    require(actual["sha256"] == ref["sha256"], "original_log_hash_mismatch")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == actual["sha256"], "original_log_changed_during_read")
    return raw


def nominal_results(raw, module):
    text = raw.decode("utf-8", "strict").replace("\r\n", "\n")
    # Accept Python 3.12 and 3.13 verbose IDs, with or without the repeated method.
    rows = re.findall(r"(?m)^(test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\) \.\.\. (.+)$", text)
    require(len(rows) == 56, "expected_56_nominal_terminal_results")
    ids = []
    for method, name, status in rows:
        require(status == "ok", "nominal_result_not_ok")
        prefix = module + "."
        require(name.startswith(prefix), "foreign_test_module")
        suffix = name[len(prefix):]
        if suffix.endswith("." + method):
            suffix = suffix[:-(len(method) + 1)]
        require("." not in suffix, "nested_or_foreign_test_class")
        ids.append(suffix + "." + method)
    require(len(set(ids)) == len(ids) and set(ids) == EXPECTED_CASES,
            "nominal_roster_missing_extra_or_duplicate")
    return sorted(ids)


def admit_execution(folder, receipt, state, github):
    source = state["source"]
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("status") == "PASS"
            and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and receipt.get("require_complete") is True
            and "source_postcheck_error" not in receipt, "source_execution_not_complete")
    require(receipt.get("executed_source_sha") == source["sha"]
            and receipt.get("tree_sha") == source["tree"], "execution_source_mismatch")
    require(receipt.get("platform", "").startswith("Linux-"), "execution_not_linux")
    expected_github = {**github, "GITHUB_JOB": SOURCE_JOB}
    require(receipt.get("github") == expected_github, "execution_run_identity_mismatch")
    require(receipt.get("runner_sha256") == source["files"]["tooling/ci_test_runner.py"]["checkout"]["sha256"],
            "runner_source_mismatch")
    require(receipt.get("test_definition_sha256") == {
        TEST_PATH: source["files"][TEST_PATH]["checkout"]["sha256"]}, "test_definition_mismatch")
    require(receipt.get("source_locks") == {
        p: source["files"][p]["checkout"]["sha256"] for p in LOCKS}, "source_locks_mismatch")
    require(isinstance(receipt.get("python_executable_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["python_executable_sha256"]),
            "python_identity_missing")
    steps = receipt.get("steps")
    labels = ("discovery:" + PATTERN, "dotted:" + TEST_MODULE)
    require(isinstance(steps, list) and len(steps) == 2, "both_entry_points_required")
    result = {}
    for index, (step, label) in enumerate(zip(steps, labels)):
        require(step.get("entry_point") == label and step.get("status") == "PASS"
                and type(step.get("exit_code")) is int and step["exit_code"] == 0,
                "entry_point_failed_or_missing")
        tail = (["discover", "-s", "tooling/tests", "-p", PATTERN, "-v"] if index == 0
                else [TEST_MODULE, "-v"])
        require(step.get("command") == ["/usr/bin/python3", "-B", "-E", "-s", "-m", "unittest", *tail],
                "entry_point_command_changed")
        original_log(folder, step["stdout"])
        stderr = original_log(folder, step["stderr"])
        counts = runner.parse_unittest_summary(stderr.decode("utf-8", "strict"))
        stated = step.get("counts")
        require(isinstance(stated, dict) and stated == counts
                and all(type(stated[key]) is int for key in counts if key != "terminal_status")
                and counts["terminal_status"] == "OK"
                and type(counts["tests_run"]) is int and counts["tests_run"] == 56
                and counts["passed"] == 56
                and all(type(counts[key]) is int and counts[key] == 0 for key in (
                    "failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "source_counts_incomplete_or_changed")
        module = PATTERN[:-3] if index == 0 else TEST_MODULE
        result[label] = nominal_results(stderr, module)
    return result


def first_use_nominal_results(raw, mode):
    """Close this G0 family across regular and short-description verbose IDs."""
    require(mode in ("discovery", "dotted"), "first_use_g0_entry_mode")
    prefix = "" if mode == "discovery" else "tooling.tests."
    expected = {prefix + case for case in FIRST_USE_G0_CASES}
    text = raw.decode("utf-8", "strict").replace("\r\n", "\n")
    header = re.compile(
        r"^(test_[A-Za-z0-9_]+) \(([A-Za-z0-9_.]+)\)(?: \.\.\. (.*))?$"
    )
    observed = []
    pending = None
    needs_description = False
    for line in text.splitlines():
        found = header.fullmatch(line)
        if found:
            require(pending is None, "first_use_g0_missing_terminal")
            method, name, tail = found.groups()
            identity = name if name.endswith("." + method) else name + "." + method
            require(identity in expected and identity not in observed,
                    "first_use_g0_foreign_duplicate_or_wrong_mode")
            if tail == "ok":
                observed.append(identity)
            else:
                require(tail in (None, ""), "first_use_g0_non_success")
                pending = identity
                needs_description = tail is None
            continue
        if pending is not None:
            require((needs_description and bool(line[:-7]) and line.endswith(" ... ok"))
                    or (not needs_description and line == "ok"),
                    "first_use_g0_missing_or_non_success_terminal")
            observed.append(pending)
            pending = None
            continue
        require(line != "ok" and not line.endswith(" ... ok"), "first_use_g0_orphan_terminal")
    require(pending is None and len(observed) == len(set(observed)) == 52
            and set(observed) == expected, "first_use_g0_nominal_roster_incomplete")
    return sorted(identity[len(prefix):] for identity in observed)


def admit_g0(folder, receipt, state, github):
    """Recheck the existing CI fast-policy families from their original streams."""
    source = state["source"]
    require(isinstance(source.get("files"), dict) and set(source["files"]) == set(PATHS),
            "g0_source_dependency_closure_missing")
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("status") == "PASS" and type(receipt.get("exit_code")) is int
            and receipt["exit_code"] == 0 and receipt.get("require_complete") is True
            and "source_postcheck_error" not in receipt, "g0_execution_incomplete")
    require(receipt.get("executed_source_sha") == source["sha"]
            and receipt.get("tree_sha") == source["tree"]
            and receipt.get("github") == {**github, "GITHUB_JOB": SOURCE_JOB}, "g0_source_or_run_mismatch")
    require(receipt.get("platform", "").startswith("Linux-")
            and receipt.get("runner_sha256") == source["files"]["tooling/ci_test_runner.py"]["checkout"]["sha256"],
            "g0_runner_or_platform_mismatch")
    require(isinstance(receipt.get("python_executable_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["python_executable_sha256"]),
            "g0_python_identity_missing")
    require(receipt.get("test_definition_sha256") == {
        "tooling/tests/" + p: source["files"]["tooling/tests/" + p]["checkout"]["sha256"]
        for p in G0_PATTERNS}, "g0_test_definitions_mismatch")
    require(receipt.get("source_locks") == {
        p: source["files"][p]["checkout"]["sha256"] for p in LOCKS}, "g0_source_locks_mismatch")
    selections = [("discovery:" + p, ["discover", "-s", "tooling/tests", "-p", p, "-v"])
                  for p in G0_PATTERNS]
    selections += [("dotted:tooling.tests." + p[:-3], ["tooling.tests." + p[:-3], "-v"])
                   for p in sorted(G0_PATTERNS)]
    steps = receipt.get("steps")
    require(isinstance(steps, list) and len(steps) == len(selections), "all_g0_entry_points_required")
    admitted = {}
    for step, (label, tail) in zip(steps, selections):
        require(step.get("entry_point") == label and step.get("status") == "PASS"
                and type(step.get("exit_code")) is int and step["exit_code"] == 0
                and step.get("command") == ["/usr/bin/python3", "-B", "-E", "-s", "-m", "unittest", *tail],
                "g0_entrypoint_failed_or_changed")
        original_log(folder, step["stdout"])
        stderr = original_log(folder, step["stderr"])
        counts = runner.parse_unittest_summary(stderr.decode("utf-8", "strict"))
        stated = step.get("counts")
        require(isinstance(stated, dict) and stated == counts
                and all(type(stated[k]) is int for k in counts if k != "terminal_status")
                and counts["terminal_status"] == "OK" and counts["tests_run"] > 0
                and counts["passed"] == counts["tests_run"]
                and all(counts[k] == 0 for k in (
                    "failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "g0_counts_incomplete_or_changed")
        if label in ("discovery:" + FIRST_USE_G0_PATTERN,
                     "dotted:tooling.tests." + FIRST_USE_G0_PATTERN[:-3]):
            require(counts["tests_run"] == counts["passed"] == 52,
                    "first_use_g0_requires_fifty_two")
            first_use_nominal_results(stderr, label.split(":", 1)[0])
        admitted[label] = counts["tests_run"]
    return admitted


def run_source(repo, source, output):
    github = environment()
    require(github["GITHUB_SHA"] == source and os.environ.get("GITHUB_JOB") == SOURCE_JOB,
            "source_job_identity_mismatch")
    before = observe(repo, source)
    output = fresh_directory(output, repo)
    publish(output / "intent.json", {"schema": SOURCE_SCHEMA, "status": "STARTED",
            "github": github, "before": before, "started_at_utc": timestamp()})
    record = {"schema": SOURCE_SCHEMA, "status": "FAIL", "github": github,
              "source_before": before, "scope": "56 first-use source cases twice plus separately counted G0; no installed window",
              "claims": CLAIMS, "system_python": "/usr/bin/python3",
              "physical_source_case": "JournalFilesystemTests.test_exclusive_entries_keep_original_bytes",
              "fixture_boundary": "Controller/X11 are synthetic; SQLite fixtures use real SQLite; dirfd journal uses Linux filesystem"}
    code = 1
    try:
        code, g0 = runner.run_suite(repo, "tooling/tests", list(G0_PATTERNS), "both",
                                   output / "g0.json", source, True, 180)
        require(code == 0, "canonical_g0_runner_failed")
        record["g0_entry_points"] = admit_g0(output, g0, before, github)
        code, execution = runner.run_suite(repo, "tooling/tests", [PATTERN], "both",
                                          output / "execution.json", source, True, 180)
        after = observe(repo, source)
        record["source_after"] = after
        require_same(before, after)
        require(code == 0, "canonical_source_runner_failed")
        record["test_ids"] = admit_execution(output, execution, before, github)
        record["invocations"] = 112
        record["status"] = "PASS"
        code = 0
    except Exception as error:
        record["reason"] = str(error)
        for name in ("g0.json", "execution.json"):
            if (output / name).is_file() and load_json(output / name).get("status") == "UNKNOWN":
                record["status"] = "UNKNOWN"
        code = code or 1
    finally:
        record["completed_at_utc"] = timestamp()
        publish(output / "source-gate.json", record)
    return code


def admit_source_gate(folder, state, github):
    gate, gate_identity = read_json(folder / "source-gate.json")
    require(gate.get("schema") == SOURCE_SCHEMA and gate.get("status") == "PASS"
            and type(gate.get("invocations")) is int and gate["invocations"] == 112
            and gate.get("system_python") == "/usr/bin/python3"
            and gate.get("github") == github and gate.get("claims") == CLAIMS,
            "source_gate_not_admitted_for_this_run")
    require_same(gate.get("source_before"), state)
    require_same(gate.get("source_after"), state)
    execution, execution_identity = read_json(folder / "execution.json")
    ids = admit_execution(folder, execution, state, github)
    require(gate.get("test_ids") == ids, "source_gate_roster_changed")
    g0, g0_identity = read_json(folder / "g0.json")
    require(gate.get("g0_entry_points") == admit_g0(folder, g0, state, github),
            "source_g0_results_changed")
    intent, intent_identity = read_json(folder / "intent.json")
    require(intent.get("schema") == SOURCE_SCHEMA and intent.get("status") == "STARTED"
            and intent.get("github") == github and intent.get("before") == state,
            "source_intent_mismatch")
    identities = {"source-gate.json": gate_identity, "execution.json": execution_identity,
                  "g0.json": g0_identity, "intent.json": intent_identity}
    require(all(observation.regular_file(folder / name) == identity
                for name, identity in identities.items()), "source_original_changed_after_admission")
    return identities


def reserve(repo, source, namespace, source_evidence):
    github = environment(installed=True)
    require(github["GITHUB_SHA"] == source, "installed_source_mismatch")
    require(namespace.absolute() == installed_namespace(github), "installed_namespace_must_match_run")
    state = observe(repo, source)
    originals = admit_source_gate(source_evidence, state, github)
    namespace = fresh_directory(namespace, repo)
    publish(namespace / "reservation.json", {
        "schema": INSTALL_SCHEMA, "status": "RESERVED", "retry": "FORBIDDEN",
        "github": github, "source_before": state, "source_gate_originals": originals,
        "source_evidence": str(source_evidence.resolve(strict=True)),
        "namespace": str(namespace), "created_at_utc": timestamp(), "claims": CLAIMS,
    })
    return 0


def validate_window(result, source, semwright, cohort):
    require(isinstance(result, dict) and result.get("schema") == "motionwright.ci.installed-cold-relaunch/2",
            "canonical_window_receipt_required")
    require(result.get("motionwright_sha") == source and result.get("semwright_sha") == semwright
            and result.get("storage_cohort") == cohort, "window_source_or_storage_mismatch")
    require(result.get("status") == "WINDOW_OBSERVED" and result.get("window_title") == "Motionwright"
            and result.get("scope") == "extracted_unsigned_linux_appimage_rootless_xvfb"
            and type(result.get("launch_count")) is int and result["launch_count"] == 2,
            "two_installed_windows_not_observed")
    require(all(isinstance(result.get(key), dict) and result[key].get("status") == "WINDOW_OBSERVED"
                for key in ("first_launch", "reopened_launch")), "both_launch_results_required")
    action = result.get("first_use_action")
    require(isinstance(action, dict) and action.get("schema") == "motionwright.ci.accessible-action/1"
            and action.get("status") == "ACTION_ACCEPTED" and action.get("phase") == "ACTION_ACCEPTED"
            and action.get("edit_intended") is True and action.get("create_intended") is True
            and action.get("project_commit") == "NOT_VERIFIED_BY_ACTION", "accessible_action_unconfirmed")
    binding = action.get("binding")
    require(isinstance(binding, dict) and set(binding) == {"window", "pid", "start_time"}
            and type(binding["pid"]) is int and binding["pid"] > 0
            and isinstance(binding["window"], str) and re.fullmatch(r"0x[0-9a-f]+", binding["window"])
            and isinstance(binding["start_time"], str) and binding["start_time"].isdigit(),
            "accessible_process_binding_missing")
    require(result.get("claims") == CLAIMS, "installed_scope_promoted")
    persistence = result.get("persistence")
    require(isinstance(persistence, dict) and persistence.get("status") == "PASS"
            and type(persistence.get("schema_version")) is int
            and persistence["schema_version"] == cohort["storage_schema"]
            and type(persistence.get("native_receipts")) is int and persistence["native_receipts"] == 0,
            "persistence_not_proven")
    for key in ("one_project_after_both_launches", "same_project_id", "same_generation", "same_revision",
                "same_domain_document_sha256", "same_event_count", "sqlite_integrity_checked_twice"):
        require(persistence.get(key) is True, "persistence_invariant_missing:" + key)
    for key in ("project_identity_sha256", "generation_sha256", "document_sha256"):
        require(isinstance(persistence.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", persistence[key]),
                "persistence_identity_missing:" + key)
    return result


def installed(repo, source, namespace, app_run, binary, artifact, candidate_receipt):
    github = environment(installed=True)
    require(source == github["GITHUB_SHA"], "installed_source_mismatch")
    require(os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            "xvfb_and_session_dbus_required")
    namespace = namespace.absolute()
    temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    require(namespace == namespace.resolve(strict=True) and namespace != temp
            and namespace == installed_namespace(github), "installed_namespace_not_canonical")
    reservation, reservation_identity = read_json(namespace / "reservation.json")
    require(reservation.get("schema") == INSTALL_SCHEMA and reservation.get("status") == "RESERVED"
            and reservation.get("retry") == "FORBIDDEN" and reservation.get("github") == github
            and reservation.get("namespace") == str(namespace), "reservation_identity_mismatch")
    state = observe(repo, source)
    require_same(reservation.get("source_before"), state)
    originals = admit_source_gate(Path(reservation["source_evidence"]), state, github)
    require(originals == reservation["source_gate_originals"], "source_gate_originals_changed")
    semwright = load_json(repo / "SOURCE_LOCK.json")["dependencies"]["semwright"]["revision"]
    require(isinstance(semwright, str) and re.fullmatch(r"[0-9a-f]{40}", semwright), "exact_sdk_source_required")
    app_target = app_run.resolve(strict=True)
    require(app_target.is_relative_to(namespace) and binary.resolve(strict=True).is_relative_to(namespace),
            "installed_payload_outside_namespace")
    inputs = {"app_run_target": observation.regular_file(app_target),
              "installed_binary": observation.regular_file(binary),
              "appimage": observation.regular_file(artifact),
              "candidate_receipt": observation.regular_file(candidate_receipt)}
    scratch = namespace / "isolated-gui"
    require(not scratch.exists() and not scratch.is_symlink(), "gui_namespace_already_used")
    output = namespace / "window.json"
    command = ["/usr/bin/python3", "-B", "-E", "-s", str(repo / "tooling/linux_first_use_accessible.py"),
               "--app-run", str(app_run), "--scratch", str(scratch), "--source-sha", source,
               "--semwright-sha", semwright, "--storage-source-repo", str(repo),
               "--output", str(output), "--seconds", "24"]
    # This create-only record is the effect boundary. Even absent child output
    # does not permit another invocation using the same or a different namespace.
    publish(namespace / "launch-intent.json", {
        "schema": INSTALL_SCHEMA, "status": "INTENDED", "retry": "FORBIDDEN",
        "github": github, "source_before": state, "inputs_before": inputs,
        "reservation_original": reservation_identity,
        "command": command, "started_at_utc": timestamp(), "claims": CLAIMS,
    })
    terminal = {"schema": INSTALL_SCHEMA, "status": "UNKNOWN", "retry": "FORBIDDEN",
                "github": github, "source_before": state, "inputs_before": inputs,
                "reservation_original": reservation_identity,
                "claims": CLAIMS, "installed_attempts": 1}
    try:
        with (namespace / "helper.stdout").open("xb") as out, (namespace / "helper.stderr").open("xb") as err:
            process = subprocess.run(command, cwd=repo, stdout=out, stderr=err, timeout=110, check=False)
            terminal["helper_exit_code"] = process.returncode
        require(process.returncode == 0, "canonical_installed_attempt_not_successful_no_replay")
        result, window_identity = read_json(output)
        validate_window(result, source, semwright, state["storage_cohort"])
        after = observe(repo, source)
        terminal["source_after"] = after
        require_same(state, after)
        after_inputs = {"app_run_target": observation.regular_file(app_target),
                        "installed_binary": observation.regular_file(binary),
                        "appimage": observation.regular_file(artifact),
                        "candidate_receipt": observation.regular_file(candidate_receipt)}
        require(inputs == after_inputs, "installed_input_identity_changed")
        terminal["inputs_after"] = after_inputs
        # This rereads the retained database using the shared read-only oracle.
        # It does not create a project, issue an action or start another window.
        if __package__:
            from .linux_cold_start import read_snapshot, attest_persistence
        else:
            from linux_cold_start import read_snapshot, attest_persistence
        snapshot = read_snapshot(scratch / "data", storage_cohort=state["storage_cohort"])
        require(result["persistence"] == attest_persistence(snapshot, snapshot), "retained_sqlite_differs_from_receipt")
        required = ("window.json", "helper.stdout", "helper.stderr", "isolated-gui/app-run-first.log",
                    "isolated-gui/app-run-second.log", "isolated-gui/data/motionwright.sqlite3",
                    "isolated-gui/first-use-atspi/launch-intent.json",
                    "isolated-gui/first-use-atspi/result.json")
        terminal["originals"] = {p: observation.regular_file(namespace / p) for p in required}
        require(terminal["originals"]["window.json"] == window_identity,
                "window_receipt_changed_after_admission")
        require(observation.regular_file(namespace / "reservation.json") == reservation_identity,
                "reservation_changed_after_admission")
        terminal["status"] = "PASS"
    except Exception as error:
        terminal["reason"] = str(error)
    finally:
        terminal["completed_at_utc"] = timestamp()
        publish(namespace / "installed-gate.json", terminal)
    return 0 if terminal["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("source", "reserve", "installed"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--namespace", type=Path)
    parser.add_argument("--source-evidence", type=Path)
    parser.add_argument("--app-run", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--candidate-receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve(strict=True)
        if args.mode == "source":
            require(args.output is not None, "source_output_required")
            return run_source(repo, args.source, args.output)
        if args.mode == "reserve":
            require(args.namespace is not None and args.source_evidence is not None, "reservation_inputs_required")
            return reserve(repo, args.source, args.namespace, args.source_evidence)
        require(all((args.namespace, args.app_run, args.binary, args.artifact, args.candidate_receipt)),
                "installed_inputs_required")
        return installed(repo, args.source, args.namespace, args.app_run, args.binary, args.artifact, args.candidate_receipt)
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as error:
        print("Linux first-use gate refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
