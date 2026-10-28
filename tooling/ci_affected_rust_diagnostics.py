"""Closed, source-bound affected Rust diagnostics; never a release gate.

The selected names below are reviewed source tests, including explicit synthetic
transports. No native end-to-end workflow is admitted by this helper.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib

TARGETS = [{'label': 'storage-root-budget', 'package': 'motionwright-storage', 'target': ['--test', 'production_operation_budget'], 'cases': ['a_later_unknown_is_not_resolved_by_an_older_completed_observation', 'active_predecessor_immediate_writer_prevents_partial_schema6_migration', 'all_budget_dimensions_are_enforced_not_only_attempt_count', 'another_database_cannot_adopt_copied_root_id_as_new_authority', 'completed_exact_replay_is_read_only_and_never_charged_twice', 'concurrent_aliases_with_headroom_admit_only_one_original_effect', 'connection_function_does_not_expose_broker_grant_or_permission', 'direct_debit_deletion_or_increment_cannot_refund_spent_root', 'dropped_sqlite_connection_guard_blocks_store_reopen', 'failed_receipt_insert_rolls_back_debit_without_refund_or_unknown_replay', 'interrupted_read_only_observation_does_not_block_a_fresh_mutation', 'invalid_or_null_writer_marker_rejects_a_valid_project_update_but_six_allows_it', 'known_terminal_alias_is_charged_and_all_unresolved_aliases_remain_fenced', 'legacy_sqlite_writer_open_before_root_cannot_update_and_current_store_can_edit', 'legacy_writer_with_existing_open_connection_cannot_dispatch_to_enrolled_project', 'missing_schema6_cas_fence_refuses_open_without_repairing_user_database', 'one_dispatch_debits_and_original_receipt_commit_atomically', 'preexisting_unresolved_dispatch_blocks_new_root_in_same_project', 'read_only_reobservation_remains_budgeted_without_resolving_unknown_effect', 'real_cas_blob_and_import_marker_preserve_store_binding_after_reopen', 'replacing_one_cas_fence_with_foreign_trigger_preserves_count_but_refuses_open', 'rewritten_canonical_cas_fence_body_refuses_open_without_repair', 'root_is_explicit_and_same_operation_key_cannot_mint_credit', 'schema6_cas_writer_fences_and_store_identity_survive_reopen_with_spent_debit', 'terminal_without_prior_is_not_a_way_around_enrolled_budget', 'two_connections_compete_for_final_budget_only_one_admits', 'unknown_effect_blocks_changed_output_digest_and_command_in_the_same_root', 'unknown_outcome_spends_capacity_and_cannot_be_retried_or_relabelled', 'unresolved_alias_uses_original_intent_not_relabelled_payload', 'v5_prepared_statement_from_old_open_connection_is_fenced_after_actual_store_upgrade'], 'source_definitions': [{'source_sha': '40e196bd39a458c34d87a0f770e46d7378807b3f', 'source_path': 'crates/storage/tests/production_operation_budget.rs', 'source_identity': {'bytes': 105781, 'sha256': 'f4f7c62316f8701d1fb3e75e0979e25b1f1d2a01710ffb4f2b03937e63dd9b6c'}}]}, {'label': 'service-graph-admission-remove', 'package': 'motionwright-service', 'target': ['--test', 'semantic_graph_admission'], 'cases': ['admitted_authoring_context_preserves_exact_eleven_fields_without_acquiring_write_lease', 'admitted_context_rejects_foreign_or_stale_source_and_selection_before_inspection', 'admitted_removal_binds_exact_source_commits_once_and_reopens_absent', 'admitted_removal_rejection_and_revocation_preserve_saved_source_without_a_receipt', 'admitted_removal_rejects_stale_source_and_mismatched_target_without_event_or_receipt', 'admitted_removal_replays_when_graph_is_absent_but_requires_current_access_and_exact_payload', 'deliverable_excluding_the_scene_stays_blocked_without_native_inspection', 'exact_scope_and_lease_survive_one_durable_commit_without_enabling_ready', 'existing_write_adapter_denies_authoring_inspection_by_default_without_writes', 'incompatible_renderer_stays_blocked_without_inspecting_native_authority', 'independent_store_change_during_inspection_is_preserved_and_prevents_ready', 'invalid_graph_still_uses_domain_validation_under_the_held_lease', 'known_success_replays_after_head_advance_and_reopen_but_changed_payload_does_not', 'ordinary_apply_denies_graph_writes_and_admitted_seam_denies_other_changes', 'ready_context_does_not_authorize_ordinary_or_denied_source_save', 'rejected_acquisition_and_revocation_before_commit_leave_no_writes', 'rejected_inspection_cannot_return_ready_or_create_an_event', 'reopened_canonical_graph_lock_still_denies_removal_under_the_admitted_lease', 'revocation_after_ready_is_rechecked_before_apply_and_prevents_further_inspection', 'uncommitted_mismatches_in_all_four_source_fields_leave_no_writes'], 'source_definitions': [{'source_sha': 'f1f11fc25cf21fa6df160031bd0b879282fd8aa8', 'source_path': 'crates/service/tests/semantic_graph_admission.rs', 'source_identity': {'bytes': 50761, 'sha256': 'f0cdd2bbcb1574cb1ad0f259345842da245c964250c005e4c1d408a58f8af90f'}}]}, {'label': 'service-graph-persistence', 'package': 'motionwright-service', 'target': ['--test', 'semantic_graph_persistence'], 'cases': ['canonical_graph_replays_once_and_reopens_with_review_invalidation_and_original_authorship', 'graph_branch_versions_and_foreign_child_review_rejection_reopen_without_rebasing', 'graph_review_points_and_subranges_replay_once_and_survive_closed_sqlite', 'read_context_never_falls_back_for_foreign_project_branch_scene_profile_or_stamp', 'service_inspection_returns_real_descriptors_without_revision_or_native_ready', 'service_rejects_graph_writes_without_real_native_admission_and_appends_no_event', 'sqlite_rejects_all_graph_resource_aliases_and_reopened_canonical_locks_block_upsert_and_remove', 'synthetic_flow_exact_times_and_cas_rejections_survive_closed_sqlite'], 'source_definitions': [{'source_sha': '40e196bd39a458c34d87a0f770e46d7378807b3f', 'source_path': 'crates/service/tests/semantic_graph_persistence.rs', 'source_identity': {'bytes': 26402, 'sha256': 'e675ef4b0d374c173dfb305c93aa5b6ce9547826cfc419106c18ce5978ee0fd2'}}]}, {'label': 'service-graph-session', 'package': 'motionwright-service', 'target': ['--example', 'studio_review_session'], 'cases': ['graph_backend_tests::context_and_invoke_parsers_cannot_install_or_bypass_the_native_backend', 'graph_backend_tests::factory_observes_the_actual_open_store_before_synthetic_seeding', 'graph_backend_tests::graph_delegation_consumes_one_grant_and_preserves_the_opaque_failure_and_original_change', 'graph_backend_tests::graph_source_cannot_escape_the_consumed_grant_project_or_generation', 'graph_backend_tests::non_graph_edits_keep_the_service_route_and_historical_graph_source_is_not_rebased', 'graph_backend_tests::reopen_discards_grants_and_exposes_the_new_actual_store_to_the_backend', 'graph_backend_tests::stale_scope_and_missing_grant_never_reach_the_injected_backend', 'graph_backend_tests::standalone_and_desktop_fixture_flags_do_not_install_graph_authority'], 'source_definitions': [{'source_sha': 'bd17cbebc65e9e6dc1ec375ee2a87eabc9b72ccc', 'source_path': 'crates/service/examples/studio_review_session.rs', 'source_identity': {'bytes': 50482, 'sha256': '3208680183fe3dcd849f015dd8d3fa8575758e2230cd47858647368cd39545e9'}}]}, {'label': 'native-root-graph-provider-projection', 'package': 'motionwright-native', 'target': ['--lib'], 'cases': ['graph_native_probe::tests::authoring_font_binding_detects_body_change_under_equal_source_stamp', 'graph_native_probe::tests::probe_arguments_delegate_only_closed_runtime_roots_and_sealed_tools', 'graph_store_provider::tests::absent_graph_application_key_preserves_the_legacy_context_key', 'graph_store_provider::tests::copied_database_identity_preserves_reads_but_cannot_install_graph_writer', 'graph_store_provider::tests::default_and_exact_override_select_only_the_derived_spelling', 'graph_store_provider::tests::each_admission_slot_preserves_only_its_first_original_error', 'graph_store_provider::tests::environment_then_argument_then_default_preserves_empty_override', 'graph_store_provider::tests::fresh_graph_source_cannot_use_an_older_genuine_reference_revision', 'graph_store_provider::tests::generic_application_never_installs_a_graph_writer', 'graph_store_provider::tests::graph_application_key_is_optional_in_the_existing_contract', 'graph_store_provider::tests::graph_application_key_preserves_original_utf8_and_spaces', 'graph_store_provider::tests::graph_application_key_rejects_empty_oversize_and_control_bytes', 'graph_store_provider::tests::graph_application_key_routes_to_real_service_scope_and_recovery_without_authority', 'graph_store_provider::tests::graph_arguments_reject_serialized_readiness_or_authority_fields', 'graph_store_provider::tests::graph_authoring_cursor_preserves_original_source_and_selection', 'graph_store_provider::tests::graph_authoring_cursor_rejects_changed_source_before_native_probe', 'graph_store_provider::tests::graph_authoring_cursor_rejects_untyped_or_noncanonical_selector', 'graph_store_provider::tests::graph_recovery_uses_the_existing_utf8_byte_limit_only_when_requested', 'graph_store_provider::tests::graph_remove_application_local_context_never_reaches_store', 'graph_store_provider::tests::graph_remove_wire_keeps_original_source_without_graph_or_profile_substitutes', 'graph_store_provider::tests::path_aliases_and_case_changes_never_select_the_graph_binding', 'graph_store_provider::tests::present_graph_application_key_must_be_a_string_not_null_or_other_json', 'graph_store_provider::tests::private_handler_still_rejects_application_local_context_before_store_mutation', 'graph_store_provider::tests::uncertain_native_error_retains_its_original_outcome_and_code', 'manim_graph_projection::source_cycle_tests::changed_original_zip_and_false_byte_length_are_rejected', 'manim_graph_projection::source_cycle_tests::exact_original_zip_is_read_and_reconciled_with_its_own_source_closure', 'manim_graph_projection::source_cycle_tests::original_graph_carrier_rejects_escaped_names_wrong_formats_and_unbounded_bytes', 'manim_graph_projection::source_cycle_tests::original_graph_carrier_reuses_only_the_existing_output_mount', 'manim_graph_projection::source_cycle_tests::postrender_graph_input_matches_only_its_original_bundle_and_output_descriptor', 'manim_graph_projection::source_cycle_tests::preview_preserves_all_four_snapshot_fences', 'manim_graph_projection::source_cycle_tests::preview_produces_one_canonical_label_change_and_the_real_current_authorship_stamp', 'manim_graph_projection::source_cycle_tests::preview_rejects_a_noop_and_an_absent_target', 'manim_graph_projection::source_cycle_tests::preview_rejects_a_second_label_change_in_a_closed_valid_bundle', 'manim_graph_projection::source_cycle_tests::preview_rejects_changed_timing_even_when_both_bundles_are_closed', 'manim_graph_projection::source_cycle_tests::preview_rejects_style_or_historical_authorship_changes', 'manim_graph_projection::source_cycle_tests::preview_respects_the_existing_canonical_graph_vertex_content_lock', 'manim_graph_projection::source_cycle_tests::proposed_graph_source_accepts_current_source_without_mutating_the_project', 'manim_graph_projection::source_cycle_tests::proposed_graph_source_rechecks_proposed_dependencies_after_scene_selection', 'manim_graph_projection::source_cycle_tests::proposed_graph_source_rejects_domain_valid_text_outside_the_compiler_profile', 'manim_graph_projection::source_cycle_tests::proposed_graph_source_rejects_incompatible_scene_and_output', 'manim_graph_projection::source_cycle_tests::proposed_graph_source_requires_all_four_current_snapshot_fields', 'production::tests::root_budget_productive_tests::productive_mlt_continuation_keeps_root_and_fences_unknown_changed_output', 'production::tests::root_budget_productive_tests::productive_root_debits_sqlite_before_cli_and_exhausts_actual_frames', 'production::tests::root_budget_productive_tests::productive_root_refuses_changed_options_foreign_generation_and_resource_before_effects', 'production::tests::root_budget_productive_tests::productive_two_children_observe_committed_debits_from_independent_transport_process', 'production::tests::root_budget_productive_tests::productive_unknown_alias_is_refused_with_headroom_and_status_remains_budgeted', 'production::tests::root_budget_productive_tests::productive_unknown_retains_spent_root_after_restart_and_blocks_new_label', 'production::tests::root_budget_productive_tests::retained_mlt_costs_bind_exact_source_frames_and_both_mux_artifacts', 'production::tests::root_budget_productive_tests::rooted_project_disallows_unbudgeted_native_side_door_but_admits_scoped_read'], 'source_definitions': [{'source_sha': '40e196bd39a458c34d87a0f770e46d7378807b3f', 'source_path': 'crates/native/src/production/root_budget_productive_tests.rs', 'source_identity': {'bytes': 32152, 'sha256': '0f2a5e0b6f68bff224c89f740d5700868cc0532b1f23bbaaad797d148199fbfc'}}, {'source_sha': '1f47a2d275154047d4534e557bc622fa2c6578aa', 'source_path': 'crates/native/src/lib.rs', 'source_identity': {'bytes': 335815, 'sha256': 'b87595c8d0df5e1dda46a6d65b743666eb2615c9a7a985971bf8ea3fa76aecc0'}}, {'source_sha': '1f47a2d275154047d4534e557bc622fa2c6578aa', 'source_path': 'crates/native/src/manim_graph_source_cycle_tests.rs', 'source_identity': {'bytes': 19608, 'sha256': 'ec90c5ac5f6a7b88f5b243763b5f2631f6264c326ef20505bb41befa387146c1'}}, {'source_sha': '1f47a2d275154047d4534e557bc622fa2c6578aa', 'source_path': 'crates/native/src/graph_native_probe.rs', 'source_identity': {'bytes': 10925, 'sha256': 'f8cbc24c697b0ab4c4cf63150fc9e2e90d5eb24aa7563878522782ed3f43d913'}}]}, {'label': 'manim-profile-text-probe', 'package': 'motionwright-manim-profile', 'target': ['--lib'], 'cases': ['semantic_graph_text_probe::tests::authoring_availability_is_two_fixed_controls_without_a_graph', 'semantic_graph_text_probe::tests::changed_data_and_serialization_cannot_remint_the_original_probe_attempt', 'semantic_graph_text_probe::tests::exact_utf8_coverage_pins_and_original_request_are_required', 'semantic_graph_text_probe::tests::proposal_data_cannot_select_runtime_code_paths_or_authority', 'semantic_graph_text_probe::tests::purpose_selects_the_closed_outer_and_child_deadlines'], 'source_definitions': [{'source_sha': 'e68d2e1afde81a0091e50d50af4d97d08fa6eb9a', 'source_path': 'crates/manim-profile/src/semantic_graph_text_probe.rs', 'source_identity': {'bytes': 23893, 'sha256': 'ebbaa3aa3dc7ca3d0e4a55e88d27e9834263e6eb02be015379b9c404a6d739f1'}}]}, {'label': 'manim-runtime-runner', 'package': 'motionwright-manim-community-driver', 'target': ['--bin', 'motionwright-manim-runner'], 'cases': ['graph_probe_custody::tests::deadline_supervisor_releases_a_provably_blocked_stdin_writer', 'graph_probe_custody::tests::directory_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open', 'graph_probe_custody::tests::failed_child_spawn_closes_both_prepared_reader_channels', 'graph_probe_custody::tests::interrupted_reads_resume_but_failed_reads_never_manufacture_eof', 'graph_probe_custody::tests::missing_reader_pipe_keeps_exit_and_does_not_invent_stderr', 'graph_probe_custody::tests::nonzero_exit_survives_original_stream_read_failure', 'graph_probe_custody::tests::original_directories_members_and_bytes_cannot_be_rebound', 'graph_probe_custody::tests::original_process_error_takes_precedence_over_custody_failure', 'graph_probe_custody::tests::overflow_is_bounded_and_drain_digest_is_not_misreported_as_original', 'graph_probe_custody::tests::panicked_reader_preserves_known_nonzero_process_terminal', 'graph_probe_custody::tests::raw_binary_streams_have_observed_eof_and_exact_originals', 'graph_probe_custody::tests::retained_directory_open_rejects_rebound_file_symlink_and_directory', 'graph_probe_custody::tests::successful_exit_cannot_admit_a_missing_original_stream', 'graph_probe_custody::tests::text_deadline_bounds_a_child_that_never_reads_its_stdin', 'graph_probe_custody::tests::text_probe_retains_exact_stdin_and_denies_a_second_child', 'graph_probe_custody::tests::unobserved_process_terminal_keeps_its_original_error', 'tests::direct_components_reject_traversal_and_paths', 'tests::graph_custody_is_opt_in_and_records_the_same_child_binary_pipes', 'tests::graph_custody_reservation_refuses_a_second_child_dispatch', 'tests::graph_profile_probe_closes_command_and_argument_selection', 'tests::parser_rejects_extra_or_arbitrary_options', 'tests::runtime_probe_parser_distinguishes_observed_and_package_not_found', 'tests::runtime_probe_rejects_source_output_shell_and_duplicate_options', 'tests::synthetic_python_transport_observes_version_and_retains_render_guard', 'tests::synthetic_python_transport_reports_package_not_found_without_installing_or_rendering'], 'source_definitions': [{'source_sha': 'e68d2e1afde81a0091e50d50af4d97d08fa6eb9a', 'source_path': 'crates/driver-manim-community/src/bin/runtime_runner.rs', 'source_identity': {'bytes': 100899, 'sha256': '5c256d0fbfcb14acb3cef729ccd0c3e8dbaf7439a0c4225f3065763eed1c0a21'}}]}]
LOCK_PATHS = ("Cargo.lock", "SOURCE_LOCK.json",
              "apps/studio/package-lock.json", "rust-toolchain.toml")
CRATES = ("motionwright-domain", "motionwright-storage", "motionwright-service",
          "motionwright-native", "motionwright-manim-profile",
          "motionwright-manim-community-driver")
STREAM_LIMIT = 16 * 1024**2
STREAM_TOTAL_LIMIT = 128 * 1024**2
RUN_SECONDS = 45 * 60
ABORT = False


class StopRun(RuntimeError):
    pass


def request_abort(*_):
    global ABORT
    ABORT = True


def check_window(deadline=None):
    if ABORT:
        raise StopRun("Cancellation observed; no new command or positive publication")
    remaining = 45 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise StopRun("Global diagnostic deadline exhausted")
    return remaining


class ParseError(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise ParseError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: need(False, "nonfinite JSON"))


def names(value):
    need(isinstance(value, (list, tuple)) and bool(value), "empty/nonliteral roster")
    result = tuple(value)
    need(all(isinstance(x, str) and x and x == x.strip()
             and not any(ord(c) < 32 or ord(c) == 127 for c in x)
             for x in result), "invalid roster name")
    need(len(set(result)) == len(result), "duplicate roster name")
    return result


def lines(raw, rc):
    need(type(rc) is int and rc == 0, "command did not exit zero")
    need(isinstance(raw, bytes) and raw.endswith(b"\n"), "missing/truncated stdout")
    text = raw.decode("utf-8", errors="strict")
    need(not any((ord(c) < 32 and c != "\n") or ord(c) == 127
                 for c in text), "unexpected stdout control character")
    return [line for line in text.split("\n") if line != ""]


def parse_list(raw, required, *, rc):
    wanted = names(required)
    rows = lines(raw, rc)
    need(bool(rows), "empty compiled inventory")
    need(all(x.endswith(": test") for x in rows), "malformed terse test row")
    compiled = names([x[:-6] for x in rows])
    need(set(wanted).issubset(compiled), "selected case absent from compiled target")
    chosen = set(wanted)
    return {"all": compiled, "selected": wanted,
            "not_selected": tuple(x for x in compiled if x not in chosen),
            "full_count": len(compiled)}


NUM = r"(?:0|[1-9][0-9]*)"
RESULT = re.compile(
    rf"test result: ok\. (?P<passed>{NUM}) passed; "
    rf"(?P<failed>{NUM}) failed; (?P<ignored>{NUM}) ignored; "
    rf"(?P<measured>{NUM}) measured; (?P<filtered>{NUM}) filtered out; "
    rf"finished in (?P<seconds>{NUM}\.[0-9]{{2}})s"
)


def parse_test_result(raw, case, inventory, *, rc):
    compiled = names(inventory["all"])
    selected = names(inventory["selected"])
    need(set(selected).issubset(compiled) and case in selected,
         "invalid exact-case selection")
    rows = lines(raw, rc)
    need(len(rows) == 3, "expected one header, one test row and one result")
    need(rows[0] == "running 1 test", "expected one executed test")
    need(rows[1] == f"test {case} ... ok", "wrong case/status/pretty row")
    match = RESULT.fullmatch(rows[2])
    need(match is not None, "malformed/non-success libtest result")
    observed = {key: int(match[key]) for key in
                ("passed", "failed", "ignored", "measured", "filtered")}
    expected = {"passed": 1, "failed": 0, "ignored": 0,
                "measured": 0, "filtered": len(compiled) - 1}
    need(observed == expected, "libtest counters disagree with exact command")
    return {"case": case, **observed, "seconds": match["seconds"]}


def exclusive_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    need(len(raw) <= 2 * 1024**2, "receipt exceeds terminal reserve")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def git(*args, deadline=None):
    completed = subprocess.run(["git", *args], capture_output=True, timeout=min(45, check_window(deadline)),
                               env=dict(os.environ, GIT_OPTIONAL_LOCKS="0",
                                        GIT_NO_LAZY_FETCH="1"))
    check_window(deadline)
    need(completed.returncode == 0, "read-only Git observation failed")
    need(len(completed.stdout) < 16 * 1024**2, "Git observation exceeds bound")
    return completed.stdout


def source_state(binding, deadline=None):
    observe = lambda *args: git(*args, deadline=deadline)
    check_window(deadline)
    need(type(binding) is dict and set(binding) ==
         {"source_sha", "tree_sha", "sdk_revision", "locks"}, "binding schema")
    for key in ("source_sha", "tree_sha", "sdk_revision"):
        need(isinstance(binding[key], str)
             and re.fullmatch(r"[0-9a-f]{40}", binding[key]), "binding identity")
    need(type(binding["locks"]) is dict
         and set(binding["locks"]) == set(LOCK_PATHS), "lock roster differs")
    need(all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
             for v in binding["locks"].values()), "invalid lock digest")
    head = observe("rev-parse", "HEAD").decode().strip()
    tree = observe("rev-parse", "HEAD^{tree}").decode().strip()
    need(head == binding["source_sha"] == os.environ.get("GITHUB_SHA"),
         "checkout is not the authorized diagnostic S")
    need(tree == binding["tree_sha"], "authorized tree differs")
    need(os.environ.get("GITHUB_WORKFLOW_SHA") == head, "workflow source differs")
    need(not observe("status", "--porcelain=v2", "--untracked-files=no"),
         "tracked checkout is dirty")
    flags = observe("ls-files", "-v", "-z").split(b"\0")
    need(all(row.startswith(b"H ") for row in flags if row),
         "tracked source is assume-unchanged or skip-worktree")
    paths = list(LOCK_PATHS) + [".github/workflows/native-manim-e2e.yml",
                              "tooling/ci_affected_rust_diagnostics.py"]
    paths += [row["source_path"] for target in TARGETS
              for row in target["source_definitions"]]
    physical = {}
    for path in sorted(set(paths)):
        file = Path(path)
        need(file.is_file() and not file.is_symlink(), "source file is not regular")
        check_window(deadline)
        need(file.stat().st_size <= 4 * 1024**2, "source file exceeds read bound")
        raw = file.read_bytes()
        check_window(deadline)
        need(raw == observe("show", head + ":" + path), "physical source differs from S")
        physical[path] = identity(raw)
        if path in binding["locks"]:
            need(sha(raw) == binding["locks"][path], "source lock differs")
    source_lock = strict_json(Path("SOURCE_LOCK.json").read_bytes())
    sdk = source_lock["dependencies"]["semwright"]["revision"]
    need(sdk == binding["sdk_revision"], "SDK source lock differs")
    lock = tomllib.loads(Path("Cargo.lock").read_text(encoding="utf-8"))
    resolved = {row["source"] for row in lock["package"]
                if row["name"].startswith("semwright-") and "source" in row}
    need(resolved and all(value.startswith(
         "git+https://github.com/seradotcom/semwright")
         and value.endswith("#" + sdk) for value in resolved),
         "locked Semwright dependencies differ")
    return {"source_sha": head, "tree_sha": tree, "sdk_revision": sdk,
            "sdk_binding_kind": "SOURCE_LOCK_AND_CARGO_LOCK_ONLY",
            "resolved_sdk_sources": sorted(resolved), "physical_files": physical}


def command_source_guard(binding, deadline):
    head = git("rev-parse", "HEAD", deadline=deadline).decode().strip()
    need(head == binding["source_sha"], "source moved before command")
    need(not git("status", "--porcelain=v2", "--untracked-files=no", deadline=deadline),
         "tracked source changed before command")
    flags = git("ls-files", "-v", "-z", deadline=deadline).split(b"\0")
    need(all(row.startswith(b"H ") for row in flags if row),
         "index flags changed before command")
    observed = {}
    for path in LOCK_PATHS:
        check_window(deadline)
        raw = Path(path).read_bytes()
        observed[path] = sha(raw)
        need(observed[path] == binding["locks"][path], "lock changed before command")
    need(git("rev-parse", "HEAD", deadline=deadline).decode().strip() == head,
         "source moved during command admission")
    return {"source_sha": head, "locks": observed}


class Recorder:
    def __init__(self, output, record):
        self.output, self.record = output, record
        remaining = float(os.environ["AFFECTED_STOP_EPOCH"]) - time.time()
        need(remaining > 0, "job setup exhausted the diagnostic window")
        self.deadline = time.monotonic() + min(RUN_SECONDS, remaining)
        self.total = 0
        self.lock = threading.Lock()
        self.blocked = False

    def capture(self, label, argv, seconds):
        if self.blocked or ABORT or time.monotonic() >= self.deadline:
            self.blocked = True
            raise StopRun("No new command after cancellation/UNKNOWN/deadline")
        admission = command_source_guard(self.record["source_binding"], self.deadline)
        row = {"label": label, "argv": argv, "cwd": str(Path.cwd()),
               "source_admission": admission, "status": "UNKNOWN_NO_RETRY",
               "exit_code": None, "started_monotonic": time.monotonic()}
        self.record["commands"].append(row)
        exclusive_json(self.output / (label + ".intent.json"), row)
        overflow, stream_error = threading.Event(), threading.Event()
        streams, handles, workers, reader_for = {}, {}, [], {}
        row["cleanup_errors"] = []
        process = None
        forced = False

        def consume(kind, pipe):
            handle = handles[kind]
            observed, retained = 0, 0
            observed_sha, retained_sha = hashlib.sha256(), hashlib.sha256()
            eof = False
            error = None
            try:
                while True:
                    block = pipe.read(65536)
                    if not block:
                        eof = True
                        break
                    observed += len(block)
                    observed_sha.update(block)
                    with self.lock:
                        count = min(len(block), STREAM_LIMIT - retained,
                                    STREAM_TOTAL_LIMIT - self.total)
                        count = max(0, count)
                        self.total += count
                    if count:
                        handle.write(block[:count])
                        retained_sha.update(block[:count])
                        retained += count
                    if count != len(block):
                        overflow.set()
            except BaseException as exc:
                error = type(exc).__name__ + ": " + str(exc)
                stream_error.set()
            finally:
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException as exc:
                    error = (error or "") + " custody: " + repr(exc)
                    stream_error.set()
                finally:
                    try:
                        handle.close()
                    except BaseException as exc:
                        error = (error or "") + " file-close: " + repr(exc)
                        stream_error.set()
                try:
                    pipe.close()
                except BaseException as exc:
                    error = (error or "") + " pipe-close: " + repr(exc)
                    stream_error.set()
                streams[kind] = {"file": label + "." + kind + ".original",
                                 "observed_bytes": observed,
                                 "observed_sha256": observed_sha.hexdigest(),
                                 "retained_bytes": retained,
                                 "retained_sha256": retained_sha.hexdigest(),
                                 "observed_eof": eof, "error": error,
                                 "complete_original": eof and error is None
                                                       and observed == retained}

        def group_exists():
            try:
                os.killpg(process.pid, 0)
                return True
            except ProcessLookupError:
                return False
            except OSError as exc:
                item = "group-observation: " + repr(exc)
                if item not in row["cleanup_errors"]:
                    row["cleanup_errors"].append(item)
                return True

        def close_owned_group():
            for sig, delay in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
                if not group_exists():
                    break
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                except OSError as exc:
                    row["cleanup_errors"].append("group-signal: " + repr(exc))
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    process.poll()
                    if not group_exists():
                        break
                    time.sleep(0.05)

        try:
            for kind in ("stdout", "stderr"):
                handles[kind] = (self.output / (label + "." + kind + ".original")).open("xb")
            env = dict(os.environ, CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
            env.pop("RUST_TEST_NOCAPTURE", None)
            env.pop("RUST_TEST_THREADS", None)
            check_window(self.deadline)
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=env, start_new_session=True)
            row["pid"] = row["process_group"] = process.pid
            for kind in ("stdout", "stderr"):
                worker = threading.Thread(target=consume, args=(kind, getattr(process, kind)),
                                          daemon=True)
                workers.append(worker)
                reader_for[kind] = worker
                worker.start()
            deadline = min(self.deadline, time.monotonic() + seconds)
            while process.poll() is None:
                if (ABORT or overflow.is_set() or stream_error.is_set()
                        or time.monotonic() >= deadline):
                    forced = True
                    close_owned_group()
                    break
                time.sleep(0.05)
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=2)
            if any(worker.is_alive() for worker in workers) or group_exists():
                forced = True
                close_owned_group()
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(timeout=2)
            row.update(exit_code=process.poll(), forced_group_close=forced,
                       owned_group_absent=not group_exists(),
                       streams_closed=all(not w.is_alive() for w in workers),
                       streams=streams, stream_limit_exceeded=overflow.is_set())
            complete = (not forced and not ABORT and not stream_error.is_set()
                        and not overflow.is_set() and row["owned_group_absent"]
                        and not row["cleanup_errors"]
                        and row["streams_closed"] and len(streams) == 2
                        and all(v["complete_original"] for v in streams.values())
                        and type(row["exit_code"]) is int and row["exit_code"] >= 0)
            self.blocked = not complete
            row["status"] = ("UNKNOWN_NO_RETRY" if not complete else
                             "PASS_COMMAND" if row["exit_code"] == 0 else "FAIL_COMMAND")
        except BaseException as exc:
            self.blocked = True
            row["capture_error"] = repr(exc)
            if process is not None:
                try:
                    close_owned_group()
                except BaseException as cleanup_exc:
                    row["cleanup_errors"].append("group-cleanup: " + repr(cleanup_exc))
                for worker in workers:
                    if worker.ident is not None:
                        worker.join(timeout=2)
            raise StopRun("Command custody/termination is uncertain") from exc
        finally:
            for kind, handle in handles.items():
                reader = reader_for.get(kind)
                if not handle.closed and (reader is None or reader.ident is None
                                          or not reader.is_alive()):
                    try:
                        handle.flush()
                        os.fsync(handle.fileno())
                    except BaseException as cleanup_exc:
                        row["cleanup_errors"].append(kind + "-flush: " + repr(cleanup_exc))
                    finally:
                        try:
                            handle.close()
                        except BaseException as cleanup_exc:
                            row["cleanup_errors"].append(kind + "-close: " + repr(cleanup_exc))
            row["file_handles_closed"] = {kind: handle.closed for kind, handle in handles.items()}
            row["streams"] = streams
            for kind, handle in handles.items():
                if handle.closed:
                    try:
                        raw = (self.output / (label + "." + kind + ".original")).read_bytes()
                        row.setdefault("closed_original_files", {})[kind] = identity(raw)
                    except BaseException as cleanup_exc:
                        row["cleanup_errors"].append(kind + "-readback: " + repr(cleanup_exc))
            if (ABORT or time.monotonic() >= self.deadline or row["cleanup_errors"]
                    or not all(row["file_handles_closed"].values())):
                self.blocked = True
                row["status"] = "UNKNOWN_NO_RETRY"
            row["duration_seconds"] = round(time.monotonic() - row["started_monotonic"], 6)
            try:
                exclusive_json(self.output / (label + ".terminal.json"), row)
            except BaseException as custody_exc:
                self.blocked = True
                row["status"] = "UNKNOWN_TERMINAL_CUSTODY"
                row["terminal_custody_error"] = repr(custody_exc)
                raise StopRun("Original terminal custody failed; no further command") from custody_exc
        if self.blocked:
            raise StopRun("Command outcome or original custody is UNKNOWN; no retry")
        return row, (self.output / (label + ".stdout.original")).read_bytes()


def main():
    need(sys.platform.startswith("linux"), "diagnostic lane is Linux-only")
    need(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
         "diagnostic lane is manual-only")
    need(os.environ.get("AFFECTED_DIAGNOSTICS_REQUESTED") == "true"
         and os.environ.get("MANIM_REQUESTED_SCENARIO") == "source-contracts",
         "closed diagnostic selection differs")
    binding = strict_json(os.environ.get("AFFECTED_SOURCE_BINDING_JSON", ""))
    output = Path(os.environ["RUNNER_TEMP"]) / "affected-rust-diagnostics"
    output.mkdir(mode=0o700, exist_ok=False)
    record = {"schema": "motionwright.affected-rust-diagnostics/1",
              "status": "NOT_RUN", "scope": "AFFECTED_DIAGNOSTICS_ONLY",
              "source_binding": binding, "commands": [], "targets": [],
              "g0_approved": False, "g1_approved": False, "g2_approved": False,
              "g3_approved": False, "release_authorized": False,
              "native_e2e_jobs_selected": False, "automatic_retry": False,
              "selection_definition_hashes_are_historical_provenance": True,
              "github": {key: os.environ.get(key, "") for key in (
                  "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF",
                  "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}}
    exclusive_json(output / "attempt.intent.json", record)
    recorder = Recorder(output, record)
    failures = []
    try:
        need(all(record["github"].values()), "incomplete run/attempt identity")
        before = source_state(binding, recorder.deadline)
        record["source_before"] = before
        exclusive_json(output / "source-before.json", before)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, request_abort)
        commands = [
            ("format", ["cargo", "fmt", "--all", "--", "--check"], 180),
            ("strict-clippy", ["cargo", "clippy", "--locked"] +
             [arg for crate in CRATES for arg in ("-p", crate)] +
             ["--all-targets", "--", "-D", "warnings"], 1200)]
        for label, argv, seconds in commands:
            row, _ = recorder.capture(label, argv, seconds)
            if row["exit_code"] != 0:
                failures.append(label)
        for index, target in enumerate(TARGETS):
            base = ["cargo", "test", "--locked", "-p", target["package"]] + target["target"]
            state = {"label": target["label"], "selected_ids": target["cases"],
                     "status": "NOT_RUN", "results": [], "not_selected_status": "NOT_SELECTED_NO_CREDIT"}
            record["targets"].append(state)
            try:
                row, raw = recorder.capture("target-" + str(index) + "-list",
                    base + ["--", "--list", "--format", "terse", "--color", "never"], 600)
                inventory = parse_list(raw, target["cases"], rc=row["exit_code"])
                state["compiled_inventory"] = inventory
            except (ParseError, UnicodeDecodeError) as exc:
                state.update(status="FAIL_DISCOVERY", error=str(exc))
                failures.append(target["label"] + ":discovery")
                continue
            for case_index, case in enumerate(inventory["selected"]):
                row, raw = recorder.capture("target-" + str(index) + "-case-" + str(case_index),
                    base + ["--", "--exact", case, "--format", "pretty",
                            "--test-threads=1", "--color", "never"], 120)
                try:
                    result = parse_test_result(raw, case, inventory, rc=row["exit_code"])
                    result["status"] = "PASS_SELECTED_TEST"
                    state["results"].append(result)
                except (ParseError, UnicodeDecodeError) as exc:
                    state["results"].append({"case": case, "status": "FAIL_SELECTED_TEST",
                                             "error": str(exc)})
                    failures.append(target["label"] + ":" + case)
            state["status"] = ("PASS_SELECTED_SCOPE" if all(
                r["status"] == "PASS_SELECTED_TEST" for r in state["results"])
                and len(state["results"]) == len(inventory["selected"]) else "FAIL_SELECTED_SCOPE")
        after = source_state(binding, recorder.deadline)
        record["source_after"] = after
        need(before == after, "source changed during diagnostics")
        check_window(recorder.deadline)
        record["status"] = "FAIL_AFFECTED_DIAGNOSTICS" if failures else "PASS_AFFECTED_DIAGNOSTICS"
    except BaseException as exc:
        record["status"] = ("UNKNOWN_NO_RETRY" if recorder.blocked or ABORT or isinstance(exc, StopRun)
                            else "FAIL_AFFECTED_DIAGNOSTICS")
        record["error"] = repr(exc)
    finally:
        record["failures"] = failures
        record["retained_stream_bytes"] = recorder.total
        record["selected_test_count"] = sum(len(t["cases"]) for t in TARGETS)
        completed_cases = {(target["label"], result["case"]): result["status"]
                           for target in record["targets"] for result in target["results"]}
        commands_by_label = {command["label"]: command for command in record["commands"]}
        record["selected_test_matrix"] = []
        for target_index, target in enumerate(TARGETS):
            for case_index, case in enumerate(target["cases"]):
                status = completed_cases.get((target["label"], case))
                command = commands_by_label.get(
                    "target-" + str(target_index) + "-case-" + str(case_index))
                if status is None:
                    status = "UNKNOWN_NO_RETRY" if command and command.get("pid") else "NOT_RUN"
                record["selected_test_matrix"].append({
                    "target": target["label"], "case": case, "status": status,
                    "command_label": command["label"] if command else None})
        need(len(record["selected_test_matrix"]) == record["selected_test_count"],
             "selected case matrix is incomplete")
        record["passed_selected_tests"] = sum(
            r["status"] == "PASS_SELECTED_TEST" for t in record["targets"] for r in t["results"])
        if ABORT or time.monotonic() >= recorder.deadline:
            record["status"] = "UNKNOWN_NO_RETRY"
            record["publication_blocked_by_cancellation_or_deadline"] = True
        exclusive_json(output / "receipt.json", record)
        if ABORT or time.monotonic() >= recorder.deadline:
            exclusive_json(output / "publication-after-receipt.json", {
                "status": "UNKNOWN_NO_RETRY",
                "reason": "Cancellation/deadline observed after the original receipt",
                "original_receipt_sha256": sha((output / "receipt.json").read_bytes()),
                "release_authorized": False, "automatic_retry": False})
            record["status"] = "UNKNOWN_NO_RETRY"
        print(record["status"] + "; selected tests passed " +
              str(record["passed_selected_tests"]) + "/" + str(record["selected_test_count"]) +
              "; full G0/G1/G2/G3 and release approval are NOT asserted")
    return 0 if record["status"] == "PASS_AFFECTED_DIAGNOSTICS" and not ABORT and time.monotonic() < recorder.deadline else 1


if __name__ == "__main__":
    raise SystemExit(main())
