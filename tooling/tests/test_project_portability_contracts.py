"""Closed CI rosters must execute every applicable import case, including NTFS."""
import ast
from pathlib import Path
import re
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]


def workflow_functions():
    text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
    start = text.index("          import hashlib, json, os, re, subprocess, sys, time\n")
    end = text.index("      - name: Retain source binding and selective test evidence", start)
    tree = ast.parse(textwrap.dedent(text[start:end]))
    selected = {"storage_import_cases", "portability_test_options", "verify_test_output"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    if {node.name for node in functions} != selected or len(functions) != len(selected):
        raise AssertionError("closed portability functions missing or duplicated")
    scope = {"re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "portability-contracts", "exec"), scope)
    return scope


def passing_log(names):
    return "".join(f"test {name} ... ok\n" for name in sorted(names)) + (
        f"test result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 180 filtered out;\n"
    )


class PortabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = workflow_functions()

    def test_linux_and_macos_include_all_eighteen_cases(self):
        linux = self.functions["storage_import_cases"]("Linux")
        self.assertEqual(len(linux), 18)
        self.assertEqual(linux, self.functions["storage_import_cases"]("macOS"))
        self.assertEqual(sum("fifo_" in name for name in linux), 1)
        self.assertEqual(sum("symlink" in name for name in linux), 2)

    def test_windows_has_seventeen_actual_cases_not_fifteen_plus_ignored(self):
        windows = self.functions["storage_import_cases"]("Windows")
        self.assertEqual(len(windows), 17)
        self.assertEqual(sum("symlink" in name for name in windows), 2)
        self.assertFalse(any("fifo_" in name for name in windows))
        self.assertEqual(self.functions["portability_test_options"]("full", "storage", "Windows"),
                         ["--test-threads=1", "--include-ignored"])

    def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            self.functions["storage_import_cases"]("unrecognized")

    def test_unrelated_suites_do_not_include_ignored(self):
        fn = self.functions["portability_test_options"]
        self.assertEqual(fn("full", "service", "Windows"), ["--test-threads=1"])
        self.assertEqual(fn("full", "storage", "Linux"), ["--test-threads=1"])
        self.assertEqual(fn("otio_export", "desktop-otio-export", "Windows"), ["--nocapture"])

    def test_exact_current_rosters_are_admitted(self):
        for platform in ("Linux", "macOS", "Windows"):
            with self.subTest(platform=platform):
                names = self.functions["storage_import_cases"](platform)
                self.assertTrue(self.functions["verify_test_output"](0, passing_log(names), names)[0])

    def test_historical_six_case_roster_cannot_accept_current_result(self):
        names = self.functions["storage_import_cases"]("Linux")
        historical = {name for name in names if "::tests::" in name or "::staging_tests::" in name}
        self.assertEqual(len(historical), 6)
        self.assertFalse(self.functions["verify_test_output"](0, passing_log(names), historical)[0])

    def test_two_ignored_windows_symlinks_do_not_count_as_pass(self):
        names = self.functions["storage_import_cases"]("Windows")
        text = passing_log(names)
        for name in names:
            if "symlink" in name:
                text = text.replace(f"test {name} ... ok", f"test {name} ... ignored, privilege required")
        text = text.replace("17 passed; 0 failed; 0 ignored", "15 passed; 0 failed; 2 ignored")
        self.assertFalse(self.functions["verify_test_output"](0, text, names)[0])

    def test_missing_duplicate_or_additional_case_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        first, second = sorted(names)[:2]
        text = passing_log(names)
        changes = (text.replace(f"test {first} ... ok\n", ""),
                   text.replace(f"test {first} ... ok", f"test {second} ... ok"),
                   "test unowned ... ok\n" + text)
        for changed in changes:
            self.assertFalse(self.functions["verify_test_output"](0, changed, names)[0])

    def test_nonzero_exit_or_missing_or_duplicate_summary_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        text = passing_log(names)
        for code, changed in ((1, text), (0, text.split("test result:")[0]),
                              (0, text + text.splitlines()[-1] + "\n")):
            self.assertFalse(self.functions["verify_test_output"](code, changed, names)[0])

    def test_failed_status_or_contradictory_summary_is_rejected(self):
        names = self.functions["storage_import_cases"]("Linux")
        text = passing_log(names)
        first = sorted(names)[0]
        for changed in (text.replace(f"test {first} ... ok", f"test {first} ... FAILED"),
                        text.replace("18 passed; 0 failed", "17 passed; 1 failed"),
                        text.replace("test result: ok", "test result: FAILED")):
            self.assertFalse(self.functions["verify_test_output"](0, changed, names)[0])



def cas_workflow_functions():
    text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
    start = text.index("          import hashlib, json, os, re, subprocess, sys, time\n")
    end = text.index("      - name: Retain source binding and selective test evidence", start)
    tree = ast.parse(textwrap.dedent(text[start:end]))
    selected = {
        "verify_test_output", "schema_atomicity_cases", "cas_retention_cases",
        "cas_os_names", "complete_cas_roster", "admit_cas_cases",
        "review_cas_rosters", "cas_dependency_paths", "cas_admit_parent",
        "cas_finish_status", "cas_source_postcheck",
    }
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in selected]
    if {node.name for node in definitions} != selected or len(definitions) != len(selected):
        raise AssertionError("CAS contract functions missing or duplicated")
    scope = {"re": re}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "cas-portability-contracts", "exec"), scope)
    return scope


class CasPortabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = cas_workflow_functions()

    def complete_log(self, names):
        return "".join(name + ": test\n" for name in sorted(names)) + f"\n{len(names)} tests, 0 benchmarks\n"

    def result_log(self, names, filtered=0):
        return passing_log(names).replace("180 filtered out", f"{filtered} filtered out")

    def test_real_os_rosters_are_eight_unix_seven_windows(self):
        fn = self.functions["cas_retention_cases"]
        unix, windows = fn("Linux"), fn("Windows")
        self.assertEqual(unix, fn("macOS"))
        self.assertEqual((len(unix), len(windows)), (8, 7))
        self.assertEqual(unix - windows, {
            "cas_retention_process_tests::real_unix_fifo_control_entries_reject_without_blocking"})
        self.assertTrue(all(name.startswith("cas_retention_process_tests::real_") for name in unix))

    def test_os_scope_is_explicit_and_never_implicitly_repeats_linux(self):
        fn = self.functions["cas_os_names"]
        self.assertEqual(fn("both"), {"Windows", "macOS"})
        self.assertEqual(fn("windows"), {"Windows"})
        self.assertEqual(fn("macos"), {"macOS"})
        self.assertEqual(fn("linux"), {"Linux"})
        for value in ("", "all", "ubuntu", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fn(value)
        with self.assertRaises(ValueError):
            self.functions["cas_retention_cases"]("unknown")

    def test_schema_fourteen_preserves_all_original_eleven_and_exact_three_additions(self):
        all_cases = self.functions["schema_atomicity_cases"]()
        added = {
            "cas_v4_upgrade_preserves_project_receipts_and_sequence_values",
            "import_marker_removed_by_late_trigger_rolls_back_project_and_events",
            "committed_import_marker_survives_project_deletion_with_foreign_keys_enabled",
        }
        original = {
            "failed_schema_upgrade_never_publishes_partial_new_tables_or_version",
            "valid_fresh_open_and_reopen_keep_exact_schema_and_no_extra_objects",
            "too_new_version_fails_without_rewriting_original_schema",
            "production_order_allocation_failure_cannot_publish_a_partial_append_or_claim",
            "production_order_backfill_failure_rolls_back_ddl_rows_and_sequence_counter",
            "production_order_concurrent_migrations_publish_one_mapping_per_receipt",
            "production_order_extension_trigger_failure_rolls_back_every_receipt_effect",
            "production_order_migration_preserves_receipts_extension_foreign_keys_and_audit_triggers",
            "production_order_missing_mapping_is_never_silently_backfilled_or_hidden",
            "production_order_project_mismatch_is_rejected_from_both_scopes",
            "production_order_reserved_object_collisions_are_rejected_without_adoption",
        }
        self.assertEqual(all_cases, original | added)
        self.assertEqual(len(all_cases), 14)
        verify = self.functions["verify_test_output"]
        self.assertFalse(verify(0, self.result_log(all_cases), original)[0])
        self.assertTrue(verify(0, self.result_log(all_cases), all_cases)[0])
        self.assertFalse(verify(0, self.result_log(all_cases | {"foreign"}), all_cases)[0])

    def test_complete_list_requires_names_unique_footer_and_real_zero_exit(self):
        names = self.functions["cas_retention_cases"]("Linux") | {"outside::control"}
        log = self.complete_log(names)
        fn = self.functions["complete_cas_roster"]
        self.assertEqual(set(fn(0, log)), names)
        first = sorted(names)[0]
        for code, changed in (
            (False, log), (1, log), (0, log.split("\n\n")[0]),
            (0, log + f"{first}: test\n"),
            (0, log.replace("9 tests", "8 tests")),
            (0, log + "unrecognized compiler diagnostics\n"),
        ):
            with self.subTest(code=code, text=changed), self.assertRaises(ValueError):
                fn(code, changed)

    def test_missing_or_unreviewed_selected_namespace_stops_before_execution(self):
        process = self.functions["cas_retention_cases"]("Linux")
        schema = self.functions["schema_atomicity_cases"]()
        valid = {"cas-process": tuple(process | {"file_import::unselected"}),
                 "schema-atomicity": tuple(schema)}
        fn = self.functions["review_cas_rosters"]
        fn(valid, process)
        changed_sets = [
            {**valid, "cas-process": tuple(process - {next(iter(process))})},
            {**valid, "cas-process": (*valid["cas-process"], "cas_retention_process_tests::foreign")},
            {**valid, "schema-atomicity": (*valid["schema-atomicity"], "foreign_schema")},
            {**valid, "schema-atomicity": tuple(schema - {next(iter(schema))})},
        ]
        for changed in changed_sets:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                fn(changed, process)

    def test_filtered_out_cases_are_named_and_never_receive_success_credit(self):
        expected = self.functions["cas_retention_cases"]("Windows")
        complete = tuple(sorted(expected | {"outside::one", "outside::two"}))
        result = self.functions["admit_cas_cases"](0, self.result_log(expected, 2), expected, complete)
        self.assertEqual(result["status"], "PASS_SELECTED_CASES")
        self.assertEqual(result["not_selected_tests"], ["outside::one", "outside::two"])
        self.assertEqual(result["not_selected_status"], "NOT_SELECTED")
        self.assertEqual(result["not_selected_success_credit"], 0)
        self.assertEqual(result["selected_omissions"], 0)

    def test_filtered_count_must_match_the_complete_original_list(self):
        expected = self.functions["cas_retention_cases"]("Linux")
        complete = tuple(expected | {"outside::one"})
        for filtered in (0, 2, 180):
            with self.subTest(filtered=filtered), self.assertRaises(ValueError):
                self.functions["admit_cas_cases"](0, self.result_log(expected, filtered), expected, complete)

    def test_selected_omission_skip_duplicate_false_exit_and_extra_cannot_pass(self):
        expected = self.functions["cas_retention_cases"]("Linux")
        complete = tuple(expected)
        text = self.result_log(expected)
        first, second = sorted(expected)[:2]
        for code, changed in (
            (False, text), (101, text),
            (0, text.replace(f"test {first} ... ok\n", "")),
            (0, text.replace(f"test {first} ... ok", f"test {second} ... ok")),
            (0, text.replace(f"test {first} ... ok", f"test {first} ... ignored")),
            (0, text + "test foreign ... ok\n"),
            (0, text + text.splitlines()[-1] + "\n"),
        ):
            with self.subTest(code=code, text=changed), self.assertRaises(ValueError):
                self.functions["admit_cas_cases"](code, changed, expected, complete)

    def test_exact_two_path_child_is_required_without_cross_sha_credit(self):
        product, current = "a" * 40, "b" * 40
        paths = [".github/workflows/project-portability.yml",
                 "tooling/tests/test_project_portability_contracts.py"]
        fn = self.functions["cas_admit_parent"]
        fn(current, product, [current, product], paths)
        for actual, parent, ancestry, changed in (
            (product, product, [product, product], paths),
            (current, product, [current, "c" * 40], paths),
            (current, product, [current, product, "c" * 40], paths),
            (current, product, [current, product], paths + ["crates/storage/src/lib.rs"]),
            (current, product, [current, product], paths[:1]),
            ("b" * 7, product, [current, product], paths),
        ):
            with self.subTest(ancestry=ancestry, paths=changed), self.assertRaises(ValueError):
                fn(actual, parent, ancestry, changed)

    def test_dependency_closure_includes_other_manifests_cargo_config_and_domain_sources(self):
        required = {
            ".gitattributes", "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json",
            "rust-toolchain.toml", "crates/storage/src/lib.rs",
            "crates/storage/src/cas_retention.rs",
            "crates/storage/src/cas_retention_process_tests.rs",
            "crates/storage/src/apply_receipt_binding.rs",
            "crates/storage/tests/schema_migration_atomicity.rs",
            "tooling/ci_storage_apply_recovery.py", "tooling/ci_foundation_causal.py",
            "tooling/ci_test_runner.py",
        }
        extra = {".cargo/config.toml", "crates/domain/src/production.rs",
                 "apps/desktop/src-tauri/Cargo.toml", "vendor/x/Cargo.toml"}
        fn = self.functions["cas_dependency_paths"]
        self.assertTrue(extra <= set(fn(sorted(required | extra | {"README.md"}))))
        self.assertNotIn("README.md", fn(sorted(required | extra | {"README.md"})))
        for bad in (sorted(required - {"Cargo.lock"}), [*sorted(required), "Cargo.lock"]):
            with self.assertRaises(ValueError):
                fn(bad)

    def test_unknown_and_changed_sources_cannot_be_promoted_by_passing_cases(self):
        good = {
            "status": "NOT_RUN", "source_postcheck": "VERIFIED_UNCHANGED",
            "binary_and_tool_postcheck": "VERIFIED_UNCHANGED",
            "results": [
                {"suite": "cas-process", "status": "PASS_SELECTED_CASES"},
                {"suite": "schema-atomicity", "status": "PASS_SELECTED_CASES"},
            ],
        }
        fn = self.functions["cas_finish_status"]
        self.assertEqual(fn(good), "PASS_SELECTED_SOURCE_G2")
        self.assertEqual(fn({**good, "status": "UNKNOWN_NO_RETRY"}), "UNKNOWN_NO_RETRY")
        for changed in (
            {**good, "source_postcheck": "REJECTED"},
            {**good, "binary_and_tool_postcheck": "NOT_RUN"},
            {**good, "failure": {"type": "ChangedSource"}},
            {**good, "results": good["results"][:1]},
            {**good, "results": [good["results"][0], good["results"][0]]},
        ):
            self.assertEqual(fn(changed), "FAIL_OR_INCOMPLETE")

    def test_real_matrix_routes_only_explicit_cas_os_and_preserves_existing_scopes(self):
        import json
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        expression = re.search(r"(?m)^        os: \$\{\{ (.+) \}\}$", text).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        def matrix(event, scope, os_scope="both"):
            return eval(expression, {"__builtins__": {}, "fromJSON": json.loads,
                "github": SimpleNamespace(event_name=event),
                "inputs": SimpleNamespace(verification_scope=scope, os_scope=os_scope)})
        all_platforms = ["ubuntu-latest", "macos-latest", "windows-latest"]
        for event in ("push", "pull_request"):
            self.assertEqual(matrix(event, "cas_retention", "linux"), all_platforms)
        self.assertEqual(matrix("workflow_dispatch", "full"), all_platforms)
        self.assertEqual(matrix("workflow_dispatch", "derived_cache"), ["macos-latest", "windows-latest"])
        self.assertEqual(matrix("workflow_dispatch", "otio_export"), ["macos-latest", "windows-latest"])
        self.assertEqual(matrix("workflow_dispatch", "project_projection"), ["ubuntu-latest"])
        for chosen, expected in (
            ("windows", ["windows-latest"]), ("macos", ["macos-latest"]),
            ("both", ["macos-latest", "windows-latest"]), ("linux", ["ubuntu-latest"]),
        ):
            self.assertEqual(matrix("workflow_dispatch", "cas_retention", chosen), expected)

    def test_every_workflow_entrypoint_requires_g0_and_retains_cas_originals(self):
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        self.assertIn("  g0-source-controls:\n    runs-on: ubuntu-24.04\n", text)
        self.assertEqual(text.count("    needs: g0-source-controls\n"), 2)
        for policy in ("source_policy", "acceptance_policy", "creative_status_policy", "public_surface_policy"):
            self.assertIn("python -B tooling/" + policy + ".py", text)
        self.assertIn("--mode both --require-complete", text)
        self.assertIn("inputs.verification_scope == 'cas_retention'", text)
        self.assertIn("cas-retention-" + "$" + "{{ github.sha }}-" + "$" + "{{ github.run_id }}-" + "$" + "{{ github.run_attempt }}", text)
        self.assertIn("if-no-files-found: error", text)

    def test_cas_checkout_fetches_the_actual_reviewed_parent_before_guard_queries(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        expression = re.search(r"(?m)^          fetch-depth: \$\{\{ (.+) \}\}$", text).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        for event, scope, expected in (
            ("workflow_dispatch", "cas_retention", 2),
            ("workflow_dispatch", "project_projection", 2),
            ("workflow_dispatch", "full", 1),
            ("pull_request", "cas_retention", 1),
        ):
            observed = eval(expression, {"__builtins__": {},
                "github": SimpleNamespace(event_name=event),
                "inputs": SimpleNamespace(verification_scope=scope)})
            self.assertEqual(observed, expected)

    def test_prior_unknown_never_calls_another_source_command(self):
        calls = []
        report = {"status": "UNKNOWN_NO_RETRY"}
        self.functions["cas_source_postcheck"](
            report, {"original": 1}, lambda: calls.append("unexpected-read"), (RuntimeError,))
        self.assertEqual(calls, [])
        self.assertEqual(report["status"], "UNKNOWN_NO_RETRY")
        self.assertEqual(report["source_postcheck"], "NOT_OBSERVED_AFTER_UNKNOWN")

    def test_cas_does_not_restore_unbound_workspace_target_before_strict_source_snapshot(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        platform = text[text.index("  file-import-platforms:\n"):]
        expression = re.search(r"      - uses: Swatinem/rust-cache@v2\n        if: (.+)\n", platform).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        for event, scope, expected in (
            ("workflow_dispatch", "cas_retention", False),
            ("workflow_dispatch", "full", True),
            ("workflow_dispatch", "project_projection", True),
            ("workflow_dispatch", "derived_cache", True),
            ("workflow_dispatch", "otio_export", True),
            ("pull_request", "cas_retention", True),
        ):
            observed = eval(expression, {"__builtins__": {},
                "github": SimpleNamespace(event_name=event),
                "inputs": SimpleNamespace(verification_scope=scope)})
            self.assertIs(observed, expected)

    def test_postcheck_unknown_stays_unknown_and_changed_source_stays_rejected(self):
        class Unknown(Exception):
            command = {"observation": "UNKNOWN_CUSTODY_NO_RETRY"}
        calls = []
        def failed_read():
            calls.append("one-read")
            raise Unknown("original source readback uncertain")
        report = {"status": "NOT_RUN"}
        fn = self.functions["cas_source_postcheck"]
        fn(report, {"original": 1}, failed_read, (Unknown,))
        self.assertEqual(calls, ["one-read"])
        self.assertEqual(report["uncertain_command"], Unknown.command)
        self.assertEqual(self.functions["cas_finish_status"](report), "UNKNOWN_NO_RETRY")
        changed = {"status": "NOT_RUN"}
        fn(changed, {"original": 1}, lambda: {"different": 2}, (Unknown,))
        self.assertEqual(changed["source_postcheck"], "REJECTED")
        self.assertEqual(self.functions["cas_finish_status"](changed), "FAIL_OR_INCOMPLETE")
        good = {"status": "NOT_RUN"}
        fn(good, {"original": 1}, lambda: {"original": 1}, (Unknown,))
        self.assertEqual(good["source_postcheck"], "VERIFIED_UNCHANGED")


class CasWindowsCheckoutBoundaryTests(unittest.TestCase):
    def test_canonical_byte_configuration_precedes_checkout_only_for_windows_cas(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        job = text.split("\n  file-import-platforms:\n", 1)[1]
        marker = "      - name: Prepare byte-exact source for Windows CAS\n"
        self.assertEqual(job.count(marker), 1)
        self.assertLess(job.index(marker), job.index("      - uses: actions/checkout@v4\n"))
        block = job.split(marker, 1)[1].split("      - uses:", 1)[0]
        condition = re.search(r"(?m)^        if: (.+)$", block).group(1).replace("&&", " and ").replace("||", " or ")
        for os_name, event, scope, expected in (
            ("Windows", "workflow_dispatch", "cas_retention", True),
            ("macOS", "workflow_dispatch", "cas_retention", False),
            ("Linux", "workflow_dispatch", "cas_retention", False),
            ("Windows", "pull_request", "cas_retention", False),
            ("Windows", "workflow_dispatch", "full", False),
            ("Windows", "workflow_dispatch", "derived_cache", False),
        ):
            self.assertIs(eval(condition, {"__builtins__": {}}, {
                "runner": SimpleNamespace(os=os_name), "github": SimpleNamespace(event_name=event),
                "inputs": SimpleNamespace(verification_scope=scope)}), expected)
        for setting in ("GIT_CONFIG_COUNT=1", "GIT_CONFIG_KEY_0=core.autocrlf",
                        "GIT_CONFIG_VALUE_0=false", "$env:GITHUB_ENV", "$ErrorActionPreference = 'Stop'"):
            self.assertIn(setting, block)
        self.assertNotIn("git reset", block)
        self.assertNotIn("checkout-index", block)

    def test_crlf_rejection_and_precheckout_lf_do_not_admit_actual_mutation(self):
        import os
        import subprocess
        import tempfile
        from tooling import ci_storage_apply_recovery as custody
        with tempfile.TemporaryDirectory(prefix="mw-cas-checkout-", dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT")) as folder:
            root = Path(folder)
            repo = root / "repo"
            repo.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
            def git(*args, extra=None):
                configured = dict(env)
                configured.update(extra or {})
                result = subprocess.run(["git", "-c", "core.hooksPath=" + str(root / "empty-hooks"),
                    "-c", "user.name=Verification", "-c", "user.email=verification@invalid.example",
                    "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                    env=configured, capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                return result.stdout
            git("init")
            original = b"frozen-source-line-one\nfrozen-source-line-two\n"
            target = repo / "fixture.txt"
            target.write_bytes(original)
            git("-c", "core.autocrlf=false", "add", "fixture.txt")
            git("commit", "-m", "source byte identity fixture")
            source = git("rev-parse", "HEAD").decode().strip()
            target.unlink()
            git("-c", "core.autocrlf=true", "checkout-index", "--force", "--all")
            self.assertEqual(target.read_bytes(), original.replace(b"\n", b"\r\n"))
            with self.assertRaises(custody.VerificationError):
                custody.source_snapshot(repo, source, ["fixture.txt"])
            target.unlink()
            git("checkout-index", "--force", "--all", extra={
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false"})
            self.assertEqual(target.read_bytes(), original)
            accepted = custody.source_snapshot(repo, source, ["fixture.txt"])
            self.assertEqual(accepted["sha"], source)
            target.write_bytes(original + b"changed\n")
            with self.assertRaises(custody.VerificationError):
                custody.source_snapshot(repo, source, ["fixture.txt"])

def cas_candidate_workflow_functions():
    text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
    start = text.index("          import hashlib, json, os, re, subprocess, sys, time\n")
    end = text.index("      - name: Retain source binding and selective test evidence", start)
    tree = ast.parse(textwrap.dedent(text[start:end]))
    wanted = {"cas_candidate_os_names", "cas_source_binding_mode", "cas_candidate_manifest",
              "cas_candidate_tree_entries", "cas_candidate_source_projection", "cas_candidate_source_binding",
              "cas_candidate_runtime", "cas_candidate_tool_binding", "cas_candidate_build_environment",
              "candidate_source_manifest", "gc_proxy_groups", "gc_proxy_actor_controls",
              "gc_proxy_expected_rosters", "gc_proxy_review_rosters", "gc_proxy_not_applicable",
              "gc_proxy_environment", "gc_proxy_formatter_environment", "gc_proxy_finish_status",
              "gc_proxy_candidate_manifest"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if {node.name for node in functions} != wanted or len(functions) != len(wanted):
        raise AssertionError("candidate source contract functions missing or duplicated")
    scope = cas_workflow_functions()
    exec(compile(ast.Module(body=functions, type_ignores=[]), "candidate-source-contracts", "exec"), scope)
    return scope


class CasCandidateBindingContractTests(unittest.TestCase):
    """Pure synthetic admission controls; no native CAS, Cargo or workflow dispatch."""

    @classmethod
    def setUpClass(cls):
        cls.functions = cas_candidate_workflow_functions()

    def fixture(self, os_scope="all"):
        import hashlib
        locks = {"Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json"}
        verifiers = {".github/workflows/project-portability.yml", "tooling/tests/test_project_portability_contracts.py",
                     "tooling/ci_foundation_causal.py", "tooling/ci_storage_apply_recovery.py", "tooling/ci_test_runner.py"}
        files = {}
        entries = []
        for path in sorted(locks | verifiers | {"crates/storage/src/synthetic-closure.rs"}):
            raw = ("synthetic manifest-only fixture: " + path).encode()
            oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            files[path] = {"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "git_blob_oid": oid}
            entries.append(("100644 blob " + oid + "\t" + path).encode() + b"\0")
        raw_tree = b"".join(entries)
        snapshot = {"sha": "a" * 40, "tree": "b" * 40, "files": files}
        chosen = self.functions["cas_candidate_os_names"](os_scope)
        platforms = [name for name in ("Linux", "macOS", "Windows") if name in chosen]
        pin = lambda row: {key: row[key] for key in ("bytes", "sha256")}
        manifest = {
            "schema": "motionwright.cas-candidate-source-binding/1", "repository": "seradotcom/motionwright",
            "source_sha": snapshot["sha"], "tree_sha": snapshot["tree"],
            "source_binding": "ALL_TRACKED_REGULAR_GIT_BLOBS_AND_EXACT_EXECUTED_BYTES", "platforms": platforms,
            "closure": self.functions["cas_candidate_source_projection"](snapshot, raw_tree),
            "locks": {path: pin(files[path]) for path in sorted(locks)},
            "verification_sources": {path: pin(files[path]) for path in sorted(verifiers)},
            "tool_policy": {"rust_toolchain": "1.98.1", "host_triples": {
                name: {"Linux": "x86_64-unknown-linux-gnu", "macOS": "aarch64-apple-darwin",
                       "Windows": "x86_64-pc-windows-msvc"}[name] for name in platforms},
                "preapproved_binary_pins": {}},
            "rosters": {name: {"cas-process": sorted(self.functions["cas_retention_cases"](name)),
                               "schema-atomicity": sorted(self.functions["schema_atomicity_cases"]())} for name in platforms},
        }
        return manifest, snapshot, raw_tree

    def encode(self, manifest):
        import hashlib
        import json
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return raw, hashlib.sha256(raw.encode()).hexdigest()

    def admit(self, manifest, os_scope="all", source="a" * 40):
        raw, pin = self.encode(manifest)
        return self.functions["cas_candidate_manifest"](raw, pin, source, os_scope)

    def tools(self, host="x86_64-unknown-linux-gnu"):
        return {"cargo": {"absolute_path": str(Path.cwd() / "synthetic-toolchain" / "cargo"),
                          "bytes": 10, "sha256": "c" * 64, "version": "cargo 1.98.1 (abc123 2026-10-01)\n"},
                "rustc": {"absolute_path": str(Path.cwd() / "synthetic-toolchain" / "rustc"),
                          "bytes": 20, "sha256": "d" * 64,
                          "version": "rustc 1.98.1 (abc123 2026-10-01)\nbinary: rustc\nhost: " + host + "\nrelease: 1.98.1\n"}}

    def runtime(self, manifest, os_scope="all", platform="Linux"):
        raw, pin = self.encode(manifest)
        inputs = {"verification_scope": "cas_candidate", "product_source_sha": "", "os_scope": os_scope,
                  "candidate_source_manifest": raw, "candidate_manifest_sha256": pin}
        environment = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_SHA": manifest["source_sha"],
            "MW_CAS_WORKFLOW_SHA": manifest["source_sha"], "GITHUB_JOB": "file-import-platforms",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_ID": "12345", "RUNNER_OS": platform,
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/project-portability.yml@refs/heads/synthetic"}
        return inputs, environment

    def test_candidate_exact_source_closure_and_independent_8_7_14_rosters_are_admitted(self):
        manifest, snapshot, raw_tree = self.fixture()
        self.assertEqual(self.admit(manifest), manifest)
        result = self.functions["cas_candidate_source_binding"](manifest, snapshot, raw_tree)
        self.assertEqual(result["source_sha"], "a" * 40)
        self.assertFalse(result["manifest_is_authority"])
        self.assertEqual(result["historical_pass_credit"], 0)
        self.assertEqual([len(manifest["rosters"][name]["cas-process"]) for name in manifest["platforms"]], [8, 8, 7])
        self.assertEqual([len(manifest["rosters"][name]["schema-atomicity"]) for name in manifest["platforms"]], [14, 14, 14])

    def test_legacy_parent_rule_and_candidate_manifest_modes_cannot_be_mixed(self):
        mode = self.functions["cas_source_binding_mode"]
        old = {"verification_scope": "cas_retention", "os_scope": "both", "product_source_sha": "9" * 40}
        self.assertEqual(mode(old), "REVIEWED_DIRECT_CHILD_977")
        for key in ("candidate_source_manifest", "candidate_manifest_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                mode({**old, key: "nonempty"})
        manifest, _, _ = self.fixture()
        inputs, _ = self.runtime(manifest)
        self.assertEqual(mode(inputs), "REVIEWED_COMPOSED_CANDIDATE")
        with self.assertRaises(ValueError):
            mode({**inputs, "product_source_sha": "9" * 40})
        with self.assertRaises(ValueError):
            self.functions["cas_os_names"]("all")
        with self.assertRaises(ValueError):
            self.functions["cas_admit_parent"]("a" * 40, "9" * 40, ["a" * 40, "b" * 40],
                [".github/workflows/project-portability.yml", "tooling/tests/test_project_portability_contracts.py"])

    def test_manifest_byte_pin_duplicate_keys_nonfinite_and_floats_are_rejected(self):
        import hashlib
        manifest, _, _ = self.fixture()
        raw, pin = self.encode(manifest)
        fn = self.functions["cas_candidate_manifest"]
        with self.assertRaises(ValueError):
            fn(raw + "\n", pin, "a" * 40, "all")
        changes = [raw.replace('"files":10', '"files":10,"files":10', 1),
                   raw.replace('"files":10', '"files":10.0', 1),
                   raw.replace('"files":10', '"files":NaN', 1),
                   raw.replace('"files":10', '"files":Infinity', 1),
                   raw.replace('"files":10', '"files":-Infinity', 1)]
        for changed in changes:
            self.assertNotEqual(changed, raw)
            with self.subTest(changed=changed[:80]), self.assertRaises(ValueError):
                fn(changed, hashlib.sha256(changed.encode()).hexdigest(), "a" * 40, "all")

    def test_manifest_unknown_authority_fields_source_aliases_and_typed_counts_are_rejected(self):
        import copy
        manifest, _, _ = self.fixture()
        mutations = (("source", "c" * 40), ("tree", True), ("files", True), ("bytes", False), ("authority", True))
        for kind, value in mutations:
            changed = copy.deepcopy(manifest)
            if kind == "source": changed["source_sha"] = value
            elif kind == "tree": changed["tree_sha"] = value
            elif kind in ("files", "bytes"): changed["closure"][kind] = value
            else: changed["authorized"] = value
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.admit(changed)
        with self.assertRaises(ValueError):
            self.admit(manifest, source="d" * 40)

    def test_all_tracked_closure_rejects_missing_extra_or_replaced_physical_file(self):
        import copy
        manifest, snapshot, raw_tree = self.fixture()
        path = "crates/storage/src/synthetic-closure.rs"
        for kind in ("missing", "extra", "physical-sha", "bytes", "tree", "source"):
            changed = copy.deepcopy(snapshot)
            if kind == "missing": del changed["files"][path]
            elif kind == "extra": changed["files"]["extra.rs"] = copy.deepcopy(changed["files"][path])
            elif kind == "physical-sha": changed["files"][path]["sha256"] = "0" * 64
            elif kind == "bytes": changed["files"][path]["bytes"] = True
            elif kind == "tree": changed["tree"] = "e" * 40
            else: changed["sha"] = "e" * 40
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.functions["cas_candidate_source_binding"](manifest, changed, raw_tree)

    def test_git_blob_oids_are_not_executed_file_sha256_and_cannot_be_substituted(self):
        import copy
        manifest, snapshot, raw_tree = self.fixture()
        path = next(iter(snapshot["files"]))
        changed = copy.deepcopy(snapshot)
        changed["files"][path]["git_blob_oid"] = changed["files"][path]["sha256"][:40]
        with self.assertRaises(ValueError):
            self.functions["cas_candidate_source_binding"](manifest, changed, raw_tree)
        changed = copy.deepcopy(manifest)
        changed["closure"]["git_tree_listing_sha256"] = changed["closure"]["executed_file_metadata_sha256"]
        with self.assertRaises(ValueError):
            self.functions["cas_candidate_source_binding"](changed, snapshot, raw_tree)

    def test_git_tree_rejects_symlinks_submodules_duplicates_traversal_and_case_aliases(self):
        _, _, raw_tree = self.fixture()
        first = raw_tree.split(b"\0")[0]
        modes = (raw_tree.replace(b"100644 blob", b"120000 blob", 1),
                 raw_tree.replace(b"100644 blob", b"160000 commit", 1),
                 raw_tree + first + b"\0", raw_tree[:-1],
                 b"100644 blob " + b"a" * 40 + b"\t../outside.rs\0",
                 b"100644 blob " + b"a" * 40 + b"\tSource.rs\0" + b"100644 blob " + b"a" * 40 + b"\tsource.rs\0")
        for changed in modes:
            with self.subTest(prefix=changed[:50]), self.assertRaises(ValueError):
                self.functions["cas_candidate_tree_entries"](changed)

    def test_lock_and_verifier_pins_bind_actual_current_bytes_even_with_matching_closure(self):
        import copy
        manifest, snapshot, raw_tree = self.fixture()
        for category in ("locks", "verification_sources"):
            for path in manifest[category]:
                changed = copy.deepcopy(manifest)
                changed[category][path]["sha256"] = "f" * 64
                self.admit(changed)
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.functions["cas_candidate_source_binding"](changed, snapshot, raw_tree)
            changed = copy.deepcopy(manifest)
            changed[category].pop(next(iter(changed[category])))
            with self.assertRaises(ValueError): self.admit(changed)

    def test_manifest_cannot_reduce_substitute_duplicate_or_move_required_rosters(self):
        import copy
        manifest, _, _ = self.fixture()
        for platform in manifest["platforms"]:
            for suite in ("cas-process", "schema-atomicity"):
                for mutation in ("missing", "duplicate", "foreign", "extra"):
                    changed = copy.deepcopy(manifest)
                    rows = changed["rosters"][platform][suite]
                    if mutation == "missing": rows.pop()
                    elif mutation == "duplicate": rows[-1] = rows[0]
                    elif mutation == "foreign": rows[-1] = "unreviewed::case"
                    else: rows.append("unreviewed::extra")
                    with self.subTest(platform=platform, suite=suite, mutation=mutation), self.assertRaises(ValueError):
                        self.admit(changed)
        changed = copy.deepcopy(manifest)
        changed["rosters"]["Windows"] = copy.deepcopy(changed["rosters"]["Linux"])
        with self.assertRaises(ValueError): self.admit(changed)

    def test_platform_selection_is_nonempty_closed_and_matches_requested_os_exactly(self):
        import copy
        manifest, _, _ = self.fixture()
        for platforms in ([], ["Linux"], ["Linux", "Linux"], ["Windows", "macOS", "Linux"], [True], ["BSD"]):
            changed = copy.deepcopy(manifest)
            changed["platforms"] = platforms
            with self.subTest(platforms=platforms), self.assertRaises(ValueError): self.admit(changed)
        for os_scope in (None, True, [], "unreviewed"):
            with self.subTest(os_scope=os_scope), self.assertRaises(ValueError):
                self.functions["cas_candidate_os_names"](os_scope)
        for choice in ("windows", "macos", "both", "linux", "all"):
            value, _, _ = self.fixture(choice)
            self.assertEqual(self.admit(value, choice), value)

    def test_candidate_requires_first_exact_workflow_source_run_job_and_real_platform(self):
        manifest, _, _ = self.fixture()
        inputs, environment = self.runtime(manifest)
        fn = self.functions["cas_candidate_runtime"]
        result = fn(manifest, inputs, environment, "Linux")
        self.assertEqual(result["other_platform_execution_credit"], 0)
        replacements = {"GITHUB_SHA": "c" * 40, "MW_CAS_WORKFLOW_SHA": "d" * 40,
            "GITHUB_RUN_ATTEMPT": "2", "GITHUB_RUN_ID": "01", "GITHUB_JOB": "other-job",
            "GITHUB_REPOSITORY": "foreign/repository", "RUNNER_OS": "Windows",
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/another.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_ACTIONS": "false"}
        for key, value in replacements.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                fn(manifest, inputs, {**environment, key: value}, "Linux")
        with self.assertRaises(ValueError): fn(manifest, inputs, environment, "BSD")

    def test_observed_tool_hashes_are_not_fabricated_preapproved_pins(self):
        import copy
        manifest, _, _ = self.fixture()
        tools = self.tools()
        fn = self.functions["cas_candidate_tool_binding"]
        result = fn(manifest, "Linux", tools)
        self.assertFalse(result["preapproved_binary_pins_supplied"])
        self.assertIsNone(result["preapproved_binary_pins"])
        self.assertEqual(result["observed_physical_binary_identities"]["rustc"]["sha256"], "d" * 64)
        manifest = copy.deepcopy(manifest)
        manifest["tool_policy"]["preapproved_binary_pins"]["Linux"] = result["observed_physical_binary_identities"]
        self.admit(manifest)
        self.assertTrue(fn(manifest, "Linux", tools)["preapproved_binary_pins_supplied"])

    def test_preapproved_tool_pin_mismatch_wrong_release_or_host_and_typed_size_are_rejected(self):
        import copy
        manifest, _, _ = self.fixture()
        observed = self.functions["cas_candidate_tool_binding"](manifest, "Linux", self.tools())
        manifest["tool_policy"]["preapproved_binary_pins"]["Linux"] = observed["observed_physical_binary_identities"]
        for mutation in ("hash", "size-bool", "cargo-version", "rust-version", "host", "duplicate-release", "missing"):
            tools = self.tools()
            if mutation == "hash": tools["cargo"]["sha256"] = "e" * 64
            elif mutation == "size-bool": tools["cargo"]["bytes"] = True
            elif mutation == "cargo-version": tools["cargo"]["version"] = "cargo 1.98.0\n"
            elif mutation == "rust-version": tools["rustc"]["version"] = tools["rustc"]["version"].replace("1.98.1", "1.98.0")
            elif mutation == "host": tools = self.tools("aarch64-apple-darwin")
            elif mutation == "duplicate-release": tools["rustc"]["version"] += "release: 1.98.1\n"
            else: del tools["rustc"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.functions["cas_candidate_tool_binding"](manifest, "Linux", tools)
        for mutation in ("host", "missing-pin", "boolean-pin"):
            changed = copy.deepcopy(manifest)
            if mutation == "host": changed["tool_policy"]["host_triples"]["Linux"] = "aarch64-apple-darwin"
            elif mutation == "missing-pin": del changed["tool_policy"]["preapproved_binary_pins"]["Linux"]["cargo"]
            else: changed["tool_policy"]["preapproved_binary_pins"]["Linux"]["cargo"]["bytes"] = True
            with self.assertRaises(ValueError): self.admit(changed)

    def test_selected_cas_or_schema_omission_never_becomes_candidate_pass(self):
        for platform in ("Linux", "macOS", "Windows"):
            for names in (self.functions["cas_retention_cases"](platform), self.functions["schema_atomicity_cases"]()):
                first = sorted(names)[0]
                text = "".join(f"test {name} ... ok\n" for name in sorted(names))
                text += f"test result: ok. {len(names)} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n"
                result = self.functions["admit_cas_cases"](0, text, names, tuple(sorted(names)))
                self.assertEqual(result["selected_omissions"], 0)
                changed = text.replace(f"test {first} ... ok", f"test {first} ... ignored")
                changed = changed.replace(f"{len(names)} passed; 0 failed; 0 ignored", f"{len(names)-1} passed; 0 failed; 1 ignored")
                with self.subTest(platform=platform), self.assertRaises(ValueError):
                    self.functions["admit_cas_cases"](0, changed, names, tuple(sorted(names)))

    def test_candidate_compilation_uses_observed_rustc_host_and_no_inherited_wrappers(self):
        import copy
        manifest, _, _ = self.fixture()
        tools = self.tools()
        initial = {"CARGO_TARGET_DIR": "synthetic-fresh-target", "RUSTUP_TOOLCHAIN": "1.98.1"}
        fn = self.functions["cas_candidate_build_environment"]
        result = fn(initial, manifest, "Linux", tools)
        self.assertEqual(initial, {"CARGO_TARGET_DIR": "synthetic-fresh-target", "RUSTUP_TOOLCHAIN": "1.98.1"})
        self.assertEqual(result["CARGO"], tools["cargo"]["absolute_path"])
        self.assertEqual(result["RUSTC"], tools["rustc"]["absolute_path"])
        self.assertEqual(result["CARGO_BUILD_RUSTC"], tools["rustc"]["absolute_path"])
        self.assertEqual(result["CARGO_BUILD_TARGET"], manifest["tool_policy"]["host_triples"]["Linux"])
        keys = ("CARGO", "RUSTC", "CARGO_BUILD_RUSTC", "CARGO_BUILD_TARGET", "RUSTC_WRAPPER",
                "RUSTC_WORKSPACE_WRAPPER", "CARGO_BUILD_RUSTC_WRAPPER", "CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER")
        for key in keys:
            with self.subTest(key=key), self.assertRaises(ValueError):
                fn({**initial, key: "unreviewed-executable-or-host"}, manifest, "Linux", tools)
        for key in keys[4:]: self.assertEqual(result[key], "")
        self.assertEqual(fn(result, manifest, "Linux", tools), result)
        changed = copy.deepcopy(tools)
        changed["rustc"]["absolute_path"] = "relative-rustc"
        with self.assertRaises(ValueError): fn(initial, manifest, "Linux", changed)

    def test_candidate_unknown_status_prevents_further_source_queries_and_pass(self):
        calls = []
        report = {"schema": "motionwright.cas-candidate-selected/1", "status": "UNKNOWN_NO_RETRY", "results": []}
        self.functions["cas_source_postcheck"](report, {}, lambda: calls.append("source replay"), (RuntimeError,))
        self.assertEqual(calls, [])
        self.assertEqual(report["source_postcheck"], "NOT_OBSERVED_AFTER_UNKNOWN")
        self.assertEqual(self.functions["cas_finish_status"](report), "UNKNOWN_NO_RETRY")

    def test_candidate_matrix_and_g0_preserve_legacy_os_semantics_and_retain_originals(self):
        import json
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        expression = re.search(r"(?m)^        os: \$\{\{ (.+) \}\}$", text).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        def matrix(scope, choice):
            return eval(expression, {"__builtins__": {}, "fromJSON": json.loads,
                "github": SimpleNamespace(event_name="workflow_dispatch"),
                "inputs": SimpleNamespace(verification_scope=scope, os_scope=choice)})
        choices = {"windows": ["windows-latest"], "macos": ["macos-latest"],
                   "both": ["macos-latest", "windows-latest"], "linux": ["ubuntu-latest"]}
        for choice, expected in choices.items():
            self.assertEqual(matrix("cas_retention", choice), expected)
            self.assertEqual(matrix("cas_candidate", choice), expected)
        self.assertEqual(matrix("cas_candidate", "all"), ["ubuntu-latest", "macos-latest", "windows-latest"])
        self.assertIn("Reject mixed CAS source bindings before selecting native jobs", text)
        self.assertIn('namespace["cas_os_names"](inputs.get("os_scope"))', text)
        self.assertIn('cas-input-event.original.json', text)
        self.assertIn('candidate-source-manifest.original.json', text)
        self.assertIn("MW_CAS_WORKFLOW_SHA: ${{ github.workflow_sha }}", text)
        self.assertEqual(text.count("    needs: g0-source-controls\n"), 2)
        self.assertIn("if: always() && github.event_name == 'workflow_dispatch' && (inputs.verification_scope == 'cas_retention' || inputs.verification_scope == 'cas_candidate' || inputs.verification_scope == 'gc_proxy_candidate')", text)

    def test_candidate_windows_lf_is_set_before_checkout_without_changing_legacy_boundary(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        job = text.split("\n  file-import-platforms:\n", 1)[1]
        marker = "      - name: Prepare byte-exact source for Windows CAS candidate\n"
        self.assertEqual(job.count(marker), 1)
        self.assertLess(job.index(marker), job.index("      - uses: actions/checkout@v4\n"))
        block = job.split(marker, 1)[1].split("      - uses:", 1)[0]
        condition = re.search(r"(?m)^        if: (.+)$", block).group(1).replace("&&", " and ").replace("||", " or ")
        for platform, scope, expected in (("Windows", "cas_candidate", True), ("Linux", "cas_candidate", False),
                                           ("macOS", "cas_candidate", False), ("Windows", "cas_retention", False)):
            self.assertIs(eval(condition, {"__builtins__": {}}, {"runner": SimpleNamespace(os=platform),
                "github": SimpleNamespace(event_name="workflow_dispatch"),
                "inputs": SimpleNamespace(verification_scope=scope)}), expected)
        self.assertIn("GIT_CONFIG_VALUE_0=false", block)
        self.assertNotIn("git reset", block)

    def test_candidate_input_preflight_uses_only_pure_functions_and_never_builds(self):
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        start = text.index("      - name: Reject mixed CAS source bindings before selecting native jobs\n")
        end = text.index("      - name: Enforce G0 policies and closed portability selectors", start)
        block = text[start:end]
        self.assertNotIn("subprocess.run", block)
        self.assertNotIn("run_cas_retention(actual", block)
        self.assertNotIn("cargo test", block)
        self.assertIn('"product_execution": "NOT_RUN"', block)
        self.assertIn('"manifest_is_authority": False', block)
        self.assertLess(block.index("cas-input-event.original.json"), block.index('mode = namespace["cas_source_binding_mode"]'))


class GcProxyCandidateContractTests(unittest.TestCase):
    """Synthetic source/admission controls only; no Rust, process cuts or GC runs."""

    @classmethod
    def setUpClass(cls):
        cls.functions = cas_candidate_workflow_functions()

    def fixture(self, os_scope="all"):
        value, snapshot, tree = CasCandidateBindingContractTests.fixture(self, os_scope)
        value["schema"] = "motionwright.gc-proxy-candidate-source-binding/1"
        value["rosters"] = {name: self.functions["gc_proxy_expected_rosters"](name) for name in value["platforms"]}
        return value, snapshot, tree

    def encode(self, value):
        return CasCandidateBindingContractTests.encode(self, value)

    def admit(self, value, choice="all"):
        raw, digest = self.encode(value)
        return self.functions["gc_proxy_candidate_manifest"](raw, digest, "a" * 40, choice)

    def runtime(self, value, os_scope="all", platform="Linux"):
        inputs, env = CasCandidateBindingContractTests.runtime(self, value, os_scope, platform)
        inputs["verification_scope"] = "gc_proxy_candidate"
        return inputs, env

    def rosters(self, platform="Linux"):
        result = {"storage-lib": ["unselected_storage::test_outside_scope"],
                  "schema-atomicity": [], "render-window-cache": []}
        for group in self.functions["gc_proxy_groups"](platform).values():
            result[group["binary"]].extend(sorted(group["tests"]))
        return {name: tuple(sorted(rows)) for name, rows in result.items()}

    def report(self, platform="Linux"):
        groups = self.functions["gc_proxy_groups"](platform)
        rosters = self.rosters(platform)
        rows = []
        for name, group in groups.items():
            expected = set(group["tests"])
            filtered = len(rosters[group["binary"]]) - len(expected)
            log = passing_log(expected).replace("180 filtered out", f"{filtered} filtered out")
            result = self.functions["admit_cas_cases"](0, log, expected, rosters[group["binary"]])
            rows.append({"suite": name, "binary": group["binary"], "command": {"exit_code": 0}, **result,
                "auxiliary_tests": [{"id": item, "role": "AUXILIARY_PROCESS_HARNESS_ENTRY",
                                     "independent_functional_success_credit": 0} for item in group["auxiliary_tests"]]})
        return {"runner_os": platform, "results": rows, "complete_rosters": rosters,
                "source_postcheck": "VERIFIED_UNCHANGED", "binary_and_tool_postcheck": "VERIFIED_UNCHANGED",
                "source_format": {"status": "PASS_SOURCE_FORMAT", "command": {"exit_code": 0}}}

    def test_closed_six_groups_have_71_unix_67_windows_and_one_auxiliary_actor(self):
        fn = self.functions["gc_proxy_groups"]
        self.assertEqual(fn("Linux"), fn("macOS"))
        expected = {"Linux": [16, 14, 10, 8, 9, 14], "macOS": [16, 14, 10, 8, 9, 14],
                    "Windows": [16, 12, 9, 7, 9, 14]}
        for platform, counts in expected.items():
            groups = fn(platform)
            self.assertEqual(list(groups), ["derived-journal", "derived-cache", "render-window-cache",
                                           "cas-process", "apply-crash", "schema-atomicity"])
            self.assertEqual([len(row["tests"]) for row in groups.values()], counts)
            self.assertEqual(sum(counts), 67 if platform == "Windows" else 71)
            self.assertEqual({row["binary"] for row in groups.values()},
                             {"storage-lib", "schema-atomicity", "render-window-cache"})
            auxiliary = [item for row in groups.values() for item in row["auxiliary_tests"]]
            self.assertEqual(auxiliary, ["cas_retention::derived::tests::derived_journal_actor"])
            self.assertEqual(len([name for name in groups["derived-cache"]["tests"]
                                  if name.startswith("derived_cache::tests::")]), 8)

    def test_windows_exclusions_are_exactly_two_cache_symlinks_one_window_symlink_and_fifo(self):
        excluded = self.functions["gc_proxy_not_applicable"]("Windows")
        self.assertEqual({row["id"] for row in excluded}, {
            "derived_cache::cache_symlink_tests::cache_shard_symlink_cannot_exfiltrate_manifest_or_become_a_hit",
            "derived_cache::cache_symlink_tests::dangling_manifest_symlink_is_never_silent_miss_or_repaired_through_link",
            "symlink_manifests_never_escape_eviction_directory_and_tombstone_can_retry",
            "cas_retention_process_tests::real_unix_fifo_control_entries_reject_without_blocking"})
        self.assertTrue(all(row["status"] == "NOT_APPLICABLE" and row["success_credit"] == 0 for row in excluded))
        self.assertEqual(self.functions["gc_proxy_not_applicable"]("Linux"), [])
        self.assertEqual(self.functions["gc_proxy_not_applicable"]("macOS"), [])

    def test_candidate_manifest_binds_full_source_and_closed_gc_rosters(self):
        for choice in ("linux", "macos", "windows", "both", "all"):
            value, snapshot, tree = self.fixture(choice)
            self.assertEqual(self.admit(value, choice), value)
            bound = self.functions["cas_candidate_source_binding"](value, snapshot, tree)
            self.assertEqual(bound["source_sha"], "a" * 40)
            self.assertEqual(bound["tree_sha"], "b" * 40)
            self.assertIs(bound["manifest_is_authority"], False)
            self.assertEqual(bound["historical_pass_credit"], 0)

    def test_cas_and_gc_manifest_profiles_are_not_aliases_or_legacy_parent_substitutes(self):
        gc, _, _ = self.fixture()
        old, _, _ = CasCandidateBindingContractTests.fixture(self)
        raw, pin = self.encode(gc)
        with self.assertRaises(ValueError):
            self.functions["cas_candidate_manifest"](raw, pin, "a" * 40, "all")
        raw, pin = self.encode(old)
        with self.assertRaises(ValueError):
            self.functions["gc_proxy_candidate_manifest"](raw, pin, "a" * 40, "all")
        inputs, environment = self.runtime(gc)
        with self.assertRaises(ValueError):
            self.functions["cas_source_binding_mode"]({**inputs, "product_source_sha": "9" * 40})
        with self.assertRaises(ValueError):
            self.functions["cas_source_binding_mode"]({**inputs, "verification_scope": "cas_retention"})
        with self.assertRaises(ValueError):
            self.functions["cas_candidate_runtime"](gc, {**inputs, "verification_scope": "cas_candidate"}, environment, "Linux")
        with self.assertRaises(ValueError):
            self.functions["cas_candidate_runtime"](old, inputs, environment, "Linux")

    def test_gc_manifest_rejects_missing_extra_duplicate_moved_and_old_partial_groups(self):
        import copy
        original, _, _ = self.fixture()
        for platform in original["platforms"]:
            for group in original["rosters"][platform]:
                for mutation in ("missing", "duplicate", "extra", "old-cache-eight", "move"):
                    if mutation == "old-cache-eight" and group != "derived-cache":
                        continue
                    value = copy.deepcopy(original)
                    rows = value["rosters"][platform][group]
                    if mutation == "missing": rows.pop()
                    elif mutation == "duplicate": rows[-1] = rows[0]
                    elif mutation == "extra": rows.append("unreviewed::extra")
                    elif mutation == "old-cache-eight": rows[:] = [name for name in rows if name.startswith("derived_cache::tests::")]
                    else: value["rosters"][platform]["foreign-group"] = value["rosters"][platform].pop(group)
                    with self.subTest(platform=platform, group=group, mutation=mutation), self.assertRaises(ValueError):
                        self.admit(value)

    def test_gc_raw_manifest_rejects_duplicate_float_boolean_unknown_and_wrong_byte_pin(self):
        import copy
        import hashlib
        original, _, _ = self.fixture()
        raw, digest = self.encode(original)
        fn = self.functions["gc_proxy_candidate_manifest"]
        with self.assertRaises(ValueError): fn(raw + "\n", digest, "a" * 40, "all")
        for text in (raw.replace('"files":10', '"files":10,"files":10', 1),
                     raw.replace('"files":10', '"files":10.0', 1),
                     raw.replace('"files":10', '"files":NaN', 1)):
            self.assertNotEqual(text, raw)
            with self.assertRaises(ValueError): fn(text, hashlib.sha256(text.encode()).hexdigest(), "a" * 40, "all")
        for kind in ("count-bool", "size-bool", "source", "tree", "authority"):
            value = copy.deepcopy(original)
            if kind == "count-bool": value["closure"]["files"] = True
            elif kind == "size-bool": value["closure"]["bytes"] = False
            elif kind == "source": value["source_sha"] = "c" * 40
            elif kind == "tree": value["tree_sha"] = True
            else: value["release_authorized"] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.admit(value)

    def test_gc_current_tree_and_physical_lock_bytes_are_not_historical_credit(self):
        import copy
        value, snapshot, tree = self.fixture()
        for kind in ("source", "tree", "physical", "lock"):
            changed = copy.deepcopy(snapshot)
            if kind == "source": changed["sha"] = "9" * 40
            elif kind == "tree": changed["tree"] = "9" * 40
            elif kind == "physical": changed["files"]["crates/storage/src/synthetic-closure.rs"]["sha256"] = "0" * 64
            else: changed["files"]["Cargo.lock"]["bytes"] += 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.functions["cas_candidate_source_binding"](value, changed, tree)

    def test_gc_runtime_requires_exact_first_manual_workflow_sha_and_platform(self):
        value, _, _ = self.fixture()
        inputs, environment = self.runtime(value)
        fn = self.functions["cas_candidate_runtime"]
        self.assertEqual(fn(value, inputs, environment, "Linux")["other_platform_execution_credit"], 0)
        for key, bad in {"GITHUB_SHA": "9" * 40, "MW_CAS_WORKFLOW_SHA": "c" * 40,
                         "GITHUB_RUN_ATTEMPT": "2", "GITHUB_EVENT_NAME": "pull_request",
                         "GITHUB_JOB": "storage-contracts", "RUNNER_OS": "macOS"}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                fn(value, inputs, {**environment, key: bad}, "Linux")
        with self.assertRaises(ValueError): fn(value, {**inputs, "os_scope": "windows"}, environment, "Linux")

    def test_three_complete_binary_rosters_allow_unselected_storage_without_credit(self):
        fn = self.functions["gc_proxy_review_rosters"]
        for platform in ("Linux", "macOS", "Windows"):
            rosters = self.rosters(platform)
            self.assertEqual(fn(rosters, platform), self.functions["gc_proxy_groups"](platform))
            report = self.report(platform)
            self.assertTrue(all("unselected_storage::test_outside_scope" in row["not_selected_tests"]
                                for row in report["results"] if row["binary"] == "storage-lib"))

    def test_complete_roster_rejects_old_apply_seven_cache_eight_extra_names_or_binary(self):
        import copy
        original = self.rosters()
        for kind in ("apply-seven", "cache-eight", "missing-actor", "unreviewed-derived", "integration-extra",
                     "schema-missing", "binary-missing", "binary-extra", "duplicate"):
            changed = copy.deepcopy(original)
            lib = list(changed["storage-lib"])
            if kind == "apply-seven": lib = [name for name in lib if "same_base_competing_writer" not in name]
            elif kind == "cache-eight": lib = [name for name in lib if not name.startswith("derived_cache::") or name.startswith("derived_cache::tests::")]
            elif kind == "missing-actor": lib.remove("cas_retention::derived::tests::derived_journal_actor")
            elif kind == "unreviewed-derived": lib.append("cas_retention::derived::tests::new_unreviewed_case")
            elif kind == "integration-extra": changed["render-window-cache"] += ("unreviewed_integration_case",)
            elif kind == "schema-missing": changed["schema-atomicity"] = changed["schema-atomicity"][:-1]
            elif kind == "binary-missing": del changed["render-window-cache"]
            elif kind == "binary-extra": changed["foreign"] = ("test_foreign",)
            else: lib.append(lib[0])
            changed["storage-lib"] = tuple(lib)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.functions["gc_proxy_review_rosters"](changed, "Linux")

    def test_each_gc_group_demands_real_terminal_names_counts_and_zero_ignored(self):
        for platform in ("Linux", "Windows"):
            rosters = self.rosters(platform)
            for name, group in self.functions["gc_proxy_groups"](platform).items():
                expected = set(group["tests"])
                filtered = len(rosters[group["binary"]]) - len(expected)
                text = passing_log(expected).replace("180 filtered out", f"{filtered} filtered out")
                first = sorted(expected)[0]
                changes = ((False, text), (1, text), (0, text.replace(f"test {first} ... ok\n", "")),
                    (0, text.replace(f"test {first} ... ok", f"test {first} ... ignored")),
                    (0, text.replace(f"{filtered} filtered out", f"{filtered + 1} filtered out")),
                    (0, text + f"test {first} ... ok\n"))
                for code, changed in changes:
                    with self.subTest(platform=platform, group=name, code=code), self.assertRaises(ValueError):
                        self.functions["admit_cas_cases"](code, changed, expected, rosters[group["binary"]])

    def test_gc_finish_closes_all_six_groups_without_independent_actor_credit(self):
        for platform in ("Linux", "macOS", "Windows"):
            report = self.report(platform)
            self.assertEqual(self.functions["gc_proxy_finish_status"](report), "PASS_SELECTED_SOURCE_G2")
            auxiliary = [item for row in report["results"] for item in row["auxiliary_tests"]]
            self.assertEqual(len(auxiliary), 1)
            self.assertEqual(auxiliary[0]["independent_functional_success_credit"], 0)

    def test_gc_finish_rejects_missing_duplicate_failed_reordered_or_fabricated_group(self):
        import copy
        for kind in ("missing", "duplicate", "reordered", "failed", "omitted", "names", "actor-credit",
                     "actor-bool", "command-bool", "summary", "format-missing", "format-bool",
                     "source-changed", "binary-changed"):
            value = copy.deepcopy(self.report())
            if kind == "missing": value["results"].pop()
            elif kind == "duplicate": value["results"][1] = copy.deepcopy(value["results"][0])
            elif kind == "reordered": value["results"].reverse()
            elif kind == "failed": value["results"][0]["status"] = "FAIL"
            elif kind == "omitted": value["results"][0]["selected_omissions"] = True
            elif kind == "names": value["results"][0]["selected_tests"].pop()
            elif kind == "actor-credit": value["results"][0]["auxiliary_tests"][0]["independent_functional_success_credit"] = 1
            elif kind == "actor-bool": value["results"][0]["auxiliary_tests"][0]["independent_functional_success_credit"] = False
            elif kind == "command-bool": value["results"][0]["command"]["exit_code"] = False
            elif kind == "summary": value["results"][0]["summary"] = [("ok", "1", "0", "0", "0", "0")]
            elif kind == "format-missing": del value["source_format"]
            elif kind == "format-bool": value["source_format"]["command"]["exit_code"] = False
            elif kind == "source-changed": value["source_postcheck"] = "REJECTED"
            else: value["binary_and_tool_postcheck"] = "NOT_RUN"
            with self.subTest(kind=kind):
                self.assertEqual(self.functions["gc_proxy_finish_status"](value), "FAIL_OR_INCOMPLETE")

    def test_unknown_dominates_complete_gc_report_and_never_runs_source_postcommands(self):
        report = self.report()
        report["status"] = "UNKNOWN_NO_RETRY"
        def poisoned():
            self.fail("UNKNOWN must not run another source observation")
        self.functions["cas_source_postcheck"](report, {}, poisoned, (RuntimeError,))
        self.assertEqual(report["source_postcheck"], "NOT_OBSERVED_AFTER_UNKNOWN")
        self.assertEqual(self.functions["gc_proxy_finish_status"](report), "UNKNOWN_NO_RETRY")

    def test_inherited_literal_actor_controls_are_rejected_even_empty_or_windows_case_aliases(self):
        import tempfile
        import os
        controls = self.functions["gc_proxy_actor_controls"]()
        self.assertEqual(len(controls), 28)
        self.assertIn("MOTIONWRIGHT_TEST_DERIVED_EXPECT_SUCCESS", controls)
        self.assertIn("MOTIONWRIGHT_TEST_APPLY_CONCURRENT", controls)
        self.assertNotIn("MOTIONWRIGHT_TEST_ROOT", controls)
        with tempfile.TemporaryDirectory(dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT")) as folder:
            def untouched(environment):
                self.fail("actor override reached build-environment callback")
            for key in controls:
                for spelling in (key, key.lower()):
                    with self.subTest(key=spelling), self.assertRaises(ValueError):
                        self.functions["gc_proxy_environment"]({spelling: ""}, Path(folder), untouched)

    def test_legitimate_test_root_is_contained_without_giving_actor_authority(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory(dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT")) as folder:
            parent = Path(folder).resolve()
            private = parent / "owned"
            private.mkdir()
            seen = []
            def isolated(environment):
                seen.append(dict(environment))
                self.assertNotIn("MOTIONWRIGHT_TEST_ROOT", environment)
                return {**environment, "CARGO_INCREMENTAL": "0"}
            fn = self.functions["gc_proxy_environment"]
            for input_root in (None, parent, private):
                env = {"PATH": "synthetic"}
                if input_root is not None: env["MOTIONWRIGHT_TEST_ROOT"] = str(input_root)
                result = fn(env, private, isolated)
                self.assertEqual(result["MOTIONWRIGHT_TEST_ROOT"], str(private))
                self.assertEqual(result["CARGO_INCREMENTAL"], "0")
                self.assertFalse(set(result) & self.functions["gc_proxy_actor_controls"]())
            self.assertEqual(len(seen), 3)

    def test_test_root_sibling_relative_case_alias_and_unknown_control_are_rejected(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory(dir=os.environ.get("MOTIONWRIGHT_TEST_ROOT")) as folder:
            parent = Path(folder).resolve()
            private, sibling = parent / "owned", parent / "foreign"
            private.mkdir(); sibling.mkdir()
            cases = ({"MOTIONWRIGHT_TEST_ROOT": str(sibling)}, {"MOTIONWRIGHT_TEST_ROOT": "relative"},
                     {"MOTIONWRIGHT_TEST_ROOT": str(private), "motionwright_test_root": str(private)},
                     {"MOTIONWRIGHT_TEST_UNREVIEWED_ACTOR": "1"})
            for env in cases:
                with self.subTest(env=env), self.assertRaises(ValueError):
                    self.functions["gc_proxy_environment"](env, private, dict)

    def test_formatter_uses_observed_toolchain_binaries_without_inventing_approved_pins(self):
        tools = CasCandidateBindingContractTests.tools(self)
        parent = Path(tools["cargo"]["absolute_path"]).parent
        formatters = {name: {"absolute_path": str(parent / name), "bytes": 4, "sha256": "f" * 64,
                             "version": "rustfmt 1.9.0-stable (synthetic)\n"} for name in ("cargo-fmt", "rustfmt")}
        fn = self.functions["gc_proxy_formatter_environment"]
        result = fn({}, tools, formatters)
        self.assertEqual(result["RUSTFMT"], formatters["rustfmt"]["absolute_path"])
        with self.assertRaises(ValueError): fn({"RUSTFMT": str(parent / "unreviewed")}, tools, formatters)
        for name in formatters:
            changed = {key: dict(row) for key, row in formatters.items()}
            changed[name]["absolute_path"] = str(parent.parent / "foreign" / name)
            with self.assertRaises(ValueError): fn({}, tools, changed)

    def test_gc_mode_reuses_existing_jobs_and_explicit_matrix_without_automatic_dispatch(self):
        import json
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\n    (?:needs:|runs-on:)", text),
                         ["g0-source-controls", "storage-contracts", "file-import-platforms"])
        job = text.split("\n  file-import-platforms:\n", 1)[1]
        expression = re.search(r"(?m)^        os: \$\{\{ fromJSON\((.*)\) \}\}$", job).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        expected = {"linux": ["ubuntu-latest"], "macos": ["macos-latest"], "windows": ["windows-latest"],
                    "both": ["macos-latest", "windows-latest"], "all": ["ubuntu-latest", "macos-latest", "windows-latest"]}
        for choice, wanted in expected.items():
            observed = eval(expression, {"__builtins__": {}}, {
                "github": SimpleNamespace(event_name="workflow_dispatch"),
                "inputs": SimpleNamespace(verification_scope="gc_proxy_candidate", os_scope=choice)})
            self.assertEqual(json.loads(observed), wanted)
        # Pull requests retain existing full semantics; they cannot activate this explicit candidate mode.
        start = text.index("      - name: Reject mixed CAS source bindings before selecting native jobs\n")
        end = text.index("      - name: Enforce G0 policies and closed portability selectors\n", start)
        precheck = text[start:end]
        self.assertIn("github.event_name == 'workflow_dispatch'", precheck)
        self.assertIn('"gc_proxy_candidate_manifest"', precheck)
        self.assertNotIn('subprocess.run', precheck)

    def test_gc_windows_lf_precedes_checkout_and_g0_and_originals_cannot_be_skipped(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        job = text.split("\n  file-import-platforms:\n", 1)[1]
        marker = "      - name: Prepare byte-exact source for Windows CAS candidate\n"
        self.assertLess(job.index(marker), job.index("      - uses: actions/checkout@v4\n"))
        block = job.split(marker, 1)[1].split("      - uses:", 1)[0]
        condition = re.search(r"(?m)^        if: (.+)$", block).group(1).replace("&&", " and ").replace("||", " or ")
        for os_name, event, expected in (("Windows", "workflow_dispatch", True), ("Linux", "workflow_dispatch", False),
                                        ("macOS", "workflow_dispatch", False), ("Windows", "pull_request", False)):
            self.assertIs(eval(condition, {"__builtins__": {}}, {
                "runner": SimpleNamespace(os=os_name), "github": SimpleNamespace(event_name=event),
                "inputs": SimpleNamespace(verification_scope="gc_proxy_candidate")}), expected)
        self.assertIn("    needs: g0-source-controls", job)
        self.assertIn("--mode both --require-complete", text)
        upload = text.split("      - name: Retain complete CAS source, binary and command originals", 1)[1]
        self.assertIn("if: always() && github.event_name == 'workflow_dispatch'", upload)
        self.assertIn("inputs.verification_scope == 'gc_proxy_candidate'", upload)
        self.assertIn("if-no-files-found: error", upload)

    def test_gc_job_budget_contains_one_build_all_selected_groups_and_custody_commands(self):
        from types import SimpleNamespace
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        job = text.split("\n  file-import-platforms:\n", 1)[1]
        expression = re.search(r"(?m)^    timeout-minutes: \$\{\{ (.*) \}\}$", job).group(1)
        expression = expression.replace("&&", " and ").replace("||", " or ")
        def minutes(scope):
            return eval(expression, {"__builtins__": {}}, {
                "github": SimpleNamespace(event_name="workflow_dispatch"),
                "inputs": SimpleNamespace(verification_scope=scope)})
        self.assertEqual(minutes("cas_retention"), 60)
        self.assertEqual(minutes("cas_candidate"), 60)
        self.assertEqual(minutes("full"), 45)
        self.assertEqual(minutes("gc_proxy_candidate"), 105)
        # 1200 compilation + eight tool queries + three complete lists + format;
        # all six groups can finish within their own bounds before the job expires.
        seconds = 1200 + 8 * 60 + 3 * 60 + 180 + sum(
            row["timeout_seconds"] for row in self.functions["gc_proxy_groups"]("Linux").values())
        self.assertGreaterEqual(minutes("gc_proxy_candidate") * 60 - seconds, 600)

    def test_gc_executor_has_one_compile_three_binaries_and_shared_process_custody(self):
        text = (ROOT / ".github/workflows/project-portability.yml").read_text(encoding="utf-8")
        start = text.index("          import hashlib, json, os, re, subprocess, sys, time\n")
        end = text.index("      - name: Retain source binding and selective test evidence", start)
        tree = ast.parse(textwrap.dedent(text[start:end]))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_cas_retention")
        executor = ast.unparse(node)
        self.assertEqual(executor.count("command('compile-selected'"), 1)
        self.assertIn("['--test', 'render_window_cache']", executor)
        self.assertIn("gc_proxy_review_rosters(rosters, real_os)", executor)
        self.assertIn("group['timeout_seconds']", executor)
        self.assertIn("observer.command(", executor)
        self.assertNotIn("subprocess.run(", executor)
        self.assertIn("observer.UnknownExecution", executor)
        self.assertIn("gc_proxy_finish_status(report) if gc_proxy else cas_finish_status(report)", executor)
        self.assertLess(executor.index("command('g0-source-format'"), executor.index("command('compile-selected'"))
        self.assertIn("observer.original_stream(logs, formatted, 'stderr')", executor)
        self.assertLess(executor.index("gc-proxy-inherited-formatter-override-before-version"),
                        executor.index("formatters[name]['absolute_path'], '--version'"))


if __name__ == "__main__":
    unittest.main()
