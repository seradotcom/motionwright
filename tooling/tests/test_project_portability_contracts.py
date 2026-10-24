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
        condition = re.search(r"(?m)^        if: (.+)$", block).group(1).replace("&&", " and ")
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

if __name__ == "__main__":
    unittest.main()
