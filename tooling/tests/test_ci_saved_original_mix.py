"""Admission controls for selected SavedMix CI; these fixtures never run Rust."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_saved_original_mix as gate

SHA = "a" * 40


def environment(repo=None):
    value = {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_SHA": SHA, "GITHUB_WORKFLOW_SHA": SHA, "GITHUB_JOB": "saved-mix",
        "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1", "RUNNER_OS": "Linux",
        "GITHUB_REPOSITORY": "owner/repository", "SAVED_MIX_SELECTION": "saved-mix",
        "GITHUB_WORKFLOW_REF": "owner/repository/" + gate.WORKFLOW + "@refs/heads/candidate",
    }
    if repo is not None:
        value["GITHUB_WORKSPACE"] = str(repo)
    return value


def output_for(name, count):
    return ("\nrunning 1 test\ntest " + name + " ... ok\n\n"
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
            + str(count - 1) + " filtered out; finished in 0.01s\n\n").encode()


def job_block(source, name):
    return re.search(r"(?ms)^  " + re.escape(name)
                     + r":\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)", source).group()


class SourceContracts(unittest.TestCase):
    def test_five_nominal_groups_match_the_actual_rust_test_sources(self):
        paths = {
            "original_mix": "crates/native/tests/original_mix.rs",
            "saved_original_mix": "crates/native/tests/saved_original_mix.rs",
            "saved_mix_native_consumers": "crates/native/tests/saved_mix_native_consumers.rs",
            "av_master": "apps/desktop/src-tauri/src/av_master.rs",
            "original_mix_native": "apps/desktop/src-tauri/src/original_mix_native.rs",
        }
        self.assertEqual([len(gate.CASE_NAMES[k]) for k in paths], [21, 12, 19, 11, 7])
        for group, path in paths.items():
            text = (ROOT / path).read_text(encoding="utf-8")
            names = re.findall(r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+(\w+)\s*\(", text)
            self.assertEqual(set(names), set(gate.CASE_NAMES[group]), group)
            self.assertEqual(len(names), len(gate.CASE_NAMES[group]), group)
        self.assertEqual(sum(len(x["names"]) for x in gate.GROUPS.values()), 70)

    def test_unix_consumer_file_and_two_desktop_cases_are_not_windows_cases(self):
        consumer = (ROOT / "crates/native/tests/saved_mix_native_consumers.rs").read_text()
        desktop = (ROOT / "apps/desktop/src-tauri/src/av_master.rs").read_text()
        self.assertIn("#![cfg(unix)]", consumer)
        unix = re.findall(r"#\[cfg\(unix\)\]\s*#\[test\]\s*fn\s+(\w+)", desktop)
        self.assertEqual(set(unix), {
            "rejects_symlink_substituted_prior_master_stage", "refuses_symlinked_measured_voice_source"})
        self.assertEqual(70 - len(gate.CASE_NAMES["saved_mix_native_consumers"]) - len(unix), 49)

    def test_original_source_meter_and_audition_job_bodies_are_preserved(self):
        source = (ROOT / gate.WORKFLOW).read_text()
        condition = "    if: ${{ github.event_name != 'push' && (github.event_name != 'workflow_dispatch' || inputs.verification != 'saved-mix') }}\n"
        expected = {
            "native-source-and-independent-meter": "1948234b985e9b092cbfe4a44fa07247ef9a065a6a13bea48125f6ba9c4c48bb",
            "studio-audition": "f196ebaf1c925def5b25114085afa5d7d8efad8be2e8c1e098be9993a8b4953b",
        }
        for name, digest in expected.items():
            block = job_block(source, name)
            self.assertEqual(block.count(condition), 1)
            restored = block.replace(condition, "").rstrip() + "\n"
            self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), digest)

    def test_workflow_defaults_to_original_and_selects_linux_without_cached_binaries(self):
        source = (ROOT / gate.WORKFLOW).read_text()
        self.assertIn("default: original\n        options:\n          - original\n          - saved-mix", source)
        block = job_block(source, "saved-mix")
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.verification == 'saved-mix'", block)
        self.assertIn("needs: saved-mix-g0", block)
        self.assertIn("runs-on: ubuntu-24.04", block)
        self.assertIn("ref: ${{ github.sha }}", block)
        self.assertIn("persist-credentials: false", block)
        self.assertIn("components: clippy", block)
        self.assertIn("toolchain: '1.98.1'", block)
        self.assertNotIn("rust-cache", block)
        self.assertNotIn("continue-on-error", block)
        self.assertIn('--target "$RUNNER_TEMP/saved-mix-target"', block)
        self.assertIn('--output "$RUNNER_TEMP/saved-mix-evidence"', block)

    def test_g0_uses_common_runner_both_modes_and_retains_failure_originals(self):
        source = (ROOT / gate.WORKFLOW).read_text()
        block = job_block(source, "saved-mix-g0")
        self.assertIn("tooling/ci_test_runner.py", block)
        self.assertIn("--mode both --pattern test_ci_saved_original_mix.py --require-complete", block)
        self.assertIn("--pattern test_native_original_mix_custody.py", block)
        self.assertIn('--expected-source "$GITHUB_SHA"', block)
        for name in ("saved-mix-g0", "saved-mix"):
            selected = job_block(source, name)
            self.assertIn("if: always()", selected)
            self.assertIn("if-no-files-found: error", selected)
            self.assertIn("github.run_attempt", selected)
            for action in re.findall(r"uses: ([^\n]+)", selected):
                self.assertRegex(action, r"@[0-9a-f]{40}$")


class StreamedWholeSource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saved-mix-real-git-source-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.name = "tracked-source.bin"
        self.raw = b"actual bounded source bytes\n" * 645278
        self.assertGreater(len(self.raw), 16 * 1024 * 1024)
        self.path = self.repo / self.name
        self.path.write_bytes(self.raw)
        required = set(gate.GATE_FILES) | {
            "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "vendor/glib-0.18.5/Cargo.toml"}
        for name in required:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Git snapshot fixture; never compiled or executed.\n")
        gate.custody.git(self.repo, "init", "--quiet")
        gate.custody.git(self.repo, "add", "--", self.name, *sorted(required))
        gate.custody.git(self.repo, "-c", "user.name=Source custody test",
                         "-c", "user.email=source-custody-test@localhost",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m",
                         "Bind the actual large source fixture")
        self.source = gate.custody.git(self.repo, "rev-parse", "HEAD").decode().strip()

    def test_large_blob_uses_existing_aggregate_budget_and_keeps_legacy_query_bound(self):
        with self.assertRaisesRegex(ValueError, "Git source query failed or exceeded its bound"):
            gate.custody.source_snapshot(self.repo, self.source, [self.name])
        value = gate.snapshot(self.repo, self.source)
        self.assertEqual(gate.source_after(self.repo, self.source, value), value)
        observed = value["files"][self.name]
        self.assertEqual(observed["bytes"], len(self.raw))
        self.assertEqual(observed["sha256"], hashlib.sha256(self.raw).hexdigest())
        expected = hashlib.sha1(b"blob " + str(len(self.raw)).encode() + b"\0" + self.raw).hexdigest()
        self.assertEqual(observed["git_blob_oid"], expected)

    def test_same_length_source_tamper_is_refused_even_when_git_status_is_clean(self):
        before = gate.snapshot(self.repo, self.source)
        gate.custody.git(self.repo, "update-index", "--assume-unchanged", self.name)
        with self.path.open("r+b") as stream:
            stream.write(b"X")
        self.assertEqual(gate.custody.git(self.repo, "status", "--porcelain=v1"), b"")
        with self.assertRaisesRegex(ValueError, "committed Git blob"):
            gate.snapshot(self.repo, self.source)
        with self.assertRaisesRegex(ValueError, "committed Git blob"):
            gate.source_after(self.repo, self.source, before)

    def test_aggregate_source_budget_is_enforced_without_reading_past_it(self):
        with self.assertRaisesRegex(ValueError, "unbounded or nonregular artifact"):
            gate.custody.source_snapshot(
                self.repo, self.source, [self.name], total_source_limit=len(self.raw)-1)
        for invalid in (0, True, gate.MAX_SOURCE_TOTAL + 1):
            with self.subTest(invalid_budget=invalid):
                with self.assertRaisesRegex(ValueError, "aggregate source byte budget"):
                    gate.custody.source_snapshot(
                        self.repo, self.source, [self.name], total_source_limit=invalid)


class Admission(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saved-mix-g0-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo, self.target, self.output = (self.root / name for name in ("repo", "target", "output"))
        for path in (self.repo, self.target, self.output):
            path.mkdir()
        self.ctx = gate.context(environment(), SHA, "saved-mix", "linux")
        self.name = gate.GROUPS["saved_original_mix"]["names"][0]
        self.roster = gate.GROUPS["saved_original_mix"]["names"]
        executable = self.target / "test-executable"
        executable.write_bytes(b"synthetic executable identity; never launched")
        self.binary = {"absolute_path": str(executable), **gate.custody.fingerprint(executable, self.target)}
        self.command = {
            "observation": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0,
            "log_custody": "VERIFIED_SNAPSHOT", "argv": gate.case_argv(self.binary, self.name),
            "context": self.ctx,
        }

    def test_exact_dispatch_context_is_accepted(self):
        self.assertEqual(self.ctx["GITHUB_SHA"], SHA)
        self.assertEqual(self.ctx["GITHUB_RUN_ATTEMPT"], "1")

    def test_wrong_os_event_selection_source_and_workflow_are_rejected(self):
        changes = (
            {"RUNNER_OS": "Windows"}, {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_SHA": "b" * 40}, {"GITHUB_WORKFLOW_SHA": "b" * 40},
            {"SAVED_MIX_SELECTION": "original"}, {"GITHUB_JOB": "native"},
            {"GITHUB_RUN_ATTEMPT": "0"}, {"GITHUB_RUN_ID": ""},
            {"GITHUB_WORKFLOW_REF": "foreign/repository/" + gate.WORKFLOW + "@main"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(gate.VerificationError):
                gate.context(dict(environment(), **change), SHA, "saved-mix", "linux")
        with self.assertRaises(gate.VerificationError):
            gate.context(environment(), SHA, "saved-mix", "win32")

    def test_inherited_compiler_product_and_test_overrides_are_rejected(self):
        for key in ("RUSTC", "RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                    "RUSTC_BOOTSTRAP", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_TARGET",
                    "CARGO_ALIAS_TEST", "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER",
                    "MOTIONWRIGHT_TEST_PATH", "MOTIONWRIGHT_PROVIDER", "CC", "RUSTDOCFLAGS"):
            with self.subTest(key=key), self.assertRaises(gate.VerificationError):
                gate.validated_environment({key: "override"})

    def test_build_environment_pins_tools_and_disables_incremental_outputs(self):
        tools = {name: {"absolute_path": str(self.root / name)} for name in ("rustc", "cargo")}
        value = gate.build_environment({"PATH": "original"}, self.target, tools)
        self.assertEqual(value["RUSTC"], tools["rustc"]["absolute_path"])
        self.assertEqual(value["CARGO"], tools["cargo"]["absolute_path"])
        self.assertEqual(value["RUSTUP_TOOLCHAIN"], "1.98.1")
        self.assertEqual(value["CARGO_TARGET_DIR"], str(self.target))
        self.assertEqual(value["CARGO_INCREMENTAL"], "0")
        self.assertEqual(value["CARGO_PROFILE_TEST_DEBUG"], "0")

    def test_original_stream_is_admitted_only_while_bytes_and_identity_match(self):
        path = self.output / "original.stdout.log"
        path.write_bytes(b"original\n")
        ref = gate.custody.fingerprint(path, self.output)
        command = {"log_custody": "VERIFIED_SNAPSHOT", "stdout": ref}
        self.assertEqual(gate.original_stream(command, "stdout", self.output), b"original\n")
        path.write_bytes(b"tampered\n")
        with self.assertRaises(gate.VerificationError):
            gate.original_stream(command, "stdout", self.output)

    def test_missing_foreign_or_unsealed_original_stream_is_rejected(self):
        for reference in (None, {"path": "../outside"}, {"path": "absent"}):
            with self.subTest(reference=reference), self.assertRaises((gate.VerificationError, OSError)):
                gate.original_stream({"log_custody": "VERIFIED_SNAPSHOT", "stdout": reference},
                                     "stdout", self.output)
        with self.assertRaises(gate.VerificationError):
            gate.original_stream({"log_custody": "INCOMPLETE"}, "stdout", self.output)

    def compile_messages(self):
        spec = gate.BINARIES["saved_original_mix"]
        return [
            {"reason": "compiler-artifact", "fresh": False,
             "executable": self.binary["absolute_path"],
             "target": {"name": spec["target"], "kind": [spec["kind"]],
                        "src_path": str(self.repo / spec["source"])},
             "profile": {"test": True}, "manifest_path": str(self.repo / spec["manifest"])},
            {"reason": "build-finished", "success": True},
        ]

    def select(self, messages, code=0):
        return gate.select_binary("\n".join(json.dumps(x) for x in messages).encode(), code,
                                  gate.BINARIES["saved_original_mix"], self.repo, self.target)

    def test_compiler_artifact_binds_selected_source_and_executable(self):
        self.assertEqual(self.select(self.compile_messages()), self.binary)

    def test_failed_duplicate_missing_or_foreign_compiler_artifacts_are_rejected(self):
        original = self.compile_messages()
        mutations = []
        value = copy.deepcopy(original); value[-1]["success"] = False; mutations.append(value)
        value = copy.deepcopy(original); value.append(value[0]); mutations.append(value)
        mutations.append(original[:1])
        value = copy.deepcopy(original); value[0]["manifest_path"] = str(self.root / "foreign/Cargo.toml"); mutations.append(value)
        value = copy.deepcopy(original); value[0]["profile"]["test"] = False; mutations.append(value)
        value = copy.deepcopy(original); value[0]["target"]["kind"] = ["lib"]; mutations.append(value)
        value = copy.deepcopy(original); value[0]["fresh"] = True; mutations.append(value)
        for messages in mutations:
            with self.subTest(messages=messages), self.assertRaises(gate.VerificationError):
                self.select(messages)
        with self.assertRaises(gate.VerificationError):
            self.select(original, 1)

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for raw in (b'{"reason":1,"reason":2}', b'{"n":NaN}', b'{"n":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(gate.VerificationError):
                gate.strict_json(raw)

    def test_roster_accepts_exact_names_and_consistent_optional_footer(self):
        raw = "\n".join(name + ": test" for name in self.roster).encode() + b"\n"
        self.assertEqual(gate.listing(raw, 0), self.roster)
        self.assertEqual(gate.listing(raw + b"\n12 tests, 0 benchmarks\n", 0), self.roster)
        self.assertEqual(set(gate.admit_group("saved_original_mix", self.roster)), set(self.roster))

    def test_roster_rejects_omission_duplicate_extra_benchmark_and_false_footer(self):
        for names in (self.roster[:-1], self.roster + (self.roster[0],), self.roster + ("unreviewed",)):
            with self.subTest(names=names), self.assertRaises(gate.VerificationError):
                gate.admit_group("saved_original_mix", names)
        for raw in (b"case: test\ncase: test\n", b"case: benchmark\n", b"case: test\n2 tests, 0 benchmarks\n",
                    b"case: test\nignored\n", b""):
            with self.subTest(raw=raw), self.assertRaises(gate.VerificationError):
                gate.listing(raw, 0)

    def test_desktop_selection_does_not_claim_unselected_names(self):
        selected = gate.GROUPS["av_master"]["names"]
        self.assertEqual(set(gate.admit_group("av_master", selected + ("other::case",))), set(selected))
        with self.assertRaises(gate.VerificationError):
            gate.admit_group("av_master", selected + ("av_master::tests::unreviewed",))

    def test_one_exact_observed_case_can_pass_without_nocapture(self):
        result = gate.admit_case(self.name, output_for(self.name, len(self.roster)),
                                 self.command, self.binary, self.binary, self.roster, self.ctx)
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn("--nocapture", self.command["argv"])
        self.assertIn("--exact", self.command["argv"])

    def test_unknown_nonzero_foreign_context_and_changed_executable_never_pass(self):
        rows = [
            dict(self.command, observation="UNKNOWN_TIMEOUT_NO_RETRY", exit_code=None),
            dict(self.command, exit_code=1), dict(self.command, log_custody="INCOMPLETE"),
            dict(self.command, context={}), dict(self.command, argv=["different"]),
        ]
        for row in rows:
            with self.subTest(row=row), self.assertRaises(gate.VerificationError):
                gate.admit_case(self.name, output_for(self.name, 4), row, self.binary,
                                self.binary, self.roster, self.ctx)
        with self.assertRaises(gate.VerificationError):
            gate.admit_case(self.name, output_for(self.name, 4), self.command,
                            self.binary, dict(self.binary, sha256="0" * 64), self.roster, self.ctx)

    def test_duplicate_ignored_omitted_extra_and_false_result_counts_never_pass(self):
        valid = output_for(self.name, 4)
        variants = (
            b"", valid + valid, valid.replace(b"... ok", b"... ignored"),
            valid.replace(b"0 ignored", b"1 ignored"), valid.replace(b"3 filtered", b"0 filtered"),
            valid + b"test foreign ... ok\n", valid.replace(b"1 passed", b"0 passed"),
            valid.replace(self.name.encode(), b"foreign"),
            valid + b"test foreign ... unrecognized\n",
        )
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(gate.VerificationError):
                gate.admit_case(self.name, raw, self.command, self.binary,
                                self.binary, self.roster, self.ctx)

    def test_retained_executable_is_exact_and_never_overwrites_a_prior_copy(self):
        retained = gate.retain_binary(self.binary, self.output, "fixture")
        self.assertEqual(retained["sha256"], self.binary["sha256"])
        with self.assertRaises(FileExistsError):
            gate.retain_binary(self.binary, self.output, "fixture")

    def test_changed_or_oversize_executable_is_not_admitted(self):
        Path(self.binary["absolute_path"]).write_bytes(b"changed")
        with self.assertRaises(gate.VerificationError):
            gate.retain_binary(self.binary, self.output, "changed")
        with mock.patch.object(gate, "MAX_BINARY", 1), self.assertRaises(gate.VerificationError):
            gate.retain_binary(self.binary, self.output, "oversize")

    def test_output_roots_must_be_new_external_and_unlinked(self):
        for path in (self.output, self.repo / "new", self.root / ".." / "escape"):
            with self.subTest(path=path), self.assertRaises(gate.VerificationError):
                gate.new_directory(path, self.repo)
        linked = mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=linked), self.assertRaises(gate.VerificationError):
            gate.new_directory(self.root / "link-output", self.repo)

    def test_preexisting_generated_desktop_state_is_not_a_source_input(self):
        (self.repo / "apps/desktop/src-tauri/gen").mkdir(parents=True)
        with mock.patch.object(gate.custody, "source_snapshot") as underlying:
            with self.assertRaises(gate.VerificationError):
                gate.snapshot(self.repo, SHA)
            underlying.assert_not_called()

    def generated_fixture(self):
        for relative in gate.GENERATED_FILES:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"fixture":"generated by synthetic build"}', encoding="utf-8")
        return sorted(gate.GENERATED_FILES)

    def generated_git(self, names):
        def read(_repo, *args):
            return b"" if "--ignored" not in args else b"\0".join(x.encode() for x in names) + b"\0"
        return read

    def observe_schemas(self):
        inventories = [gate.custody.git(self.repo, "ls-files", "-z", *options)
                       for options in (("--others", "--exclude-standard"),
                                       ("--others", "--ignored", "--exclude-standard"))]
        return gate.generated_schemas(self.repo, {}, inventories)

    def test_only_four_bounded_generated_schema_objects_are_retained(self):
        names = self.generated_fixture()
        with mock.patch.object(gate.custody, "git", side_effect=self.generated_git(names)):
            value = self.observe_schemas()
        self.assertEqual(set(value), gate.GENERATED_FILES)

    def test_unknown_untracked_paths_and_duplicate_generated_inventory_are_rejected(self):
        names = self.generated_fixture()
        for bad in (names + ["apps/desktop/src-tauri/gen/unreviewed.rs"], names + [names[0]], ["../foreign"]):
            with self.subTest(bad=bad), mock.patch.object(
                    gate.custody, "git", side_effect=self.generated_git(bad)), self.assertRaises(gate.VerificationError):
                self.observe_schemas()

    def test_malformed_or_unbounded_generated_json_is_rejected(self):
        names = self.generated_fixture()
        path = self.repo / names[0]
        for raw in ('[]', '{"n":NaN}', '{"x":1,"x":2}', 'not-json'):
            path.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), mock.patch.object(
                    gate.custody, "git", side_effect=self.generated_git(names)), self.assertRaises(ValueError):
                self.observe_schemas()
        path.write_text('{"valid":true}', encoding="utf-8")
        with mock.patch.object(gate, "MAX_GENERATED", 1), mock.patch.object(
                gate.custody, "git", side_effect=self.generated_git(names)), self.assertRaises(gate.VerificationError):
            self.observe_schemas()

    def test_physical_source_tampering_fails_even_when_status_reports_clean(self):
        path = self.repo / "Cargo.toml"
        original = b"original"
        path.write_bytes(original)
        blob_oid = hashlib.sha1(b"blob " + str(len(original)).encode() + b"\0" + original).hexdigest()
        before = {"sha": SHA, "tree": "b" * 40, "files": {
            "Cargo.toml": {**gate.custody.fingerprint(path, self.repo), "git_blob_oid": blob_oid}}}
        def git(_repo, *args):
            return SHA.encode() if args[0] == "rev-parse" else b""
        with mock.patch.object(gate.custody, "git", side_effect=git), mock.patch.object(
                gate, "source_paths", return_value=("Cargo.toml",)):
            self.assertEqual(gate.source_after(self.repo, SHA, before), before)
            path.write_bytes(b"tampered")
            with self.assertRaises(gate.VerificationError):
                gate.source_after(self.repo, SHA, before)


    def test_tracked_inventory_rejects_missing_lock_duplicate_or_nonregular_inputs(self):
        names = sorted(set(gate.GATE_FILES) | {
            "Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "vendor/glib-0.18.5/Cargo.toml"})
        rows = []
        for name in names:
            path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"reviewed fixture input")
            rows.append(b"100644 blob " + b"b" * 40 + b"\t" + name.encode() + b"\0")
        raw = b"".join(rows)
        with mock.patch.object(gate.custody, "git", return_value=raw):
            self.assertEqual(gate.source_paths(self.repo, SHA), tuple(names))
        malformed = (
            b"".join(row for row in rows if not row.endswith(b"\tCargo.lock\0")),
            raw + rows[0], raw.replace(b"100644", b"120000", 1),
            raw + b"100644 blob " + b"c" * 40 + b"\t../outside\0",
        )
        for changed in malformed:
            with self.subTest(changed=changed), mock.patch.object(
                    gate.custody, "git", return_value=changed), self.assertRaises(gate.VerificationError):
                gate.source_paths(self.repo, SHA)
        with mock.patch.object(gate.custody, "git", return_value=raw), mock.patch.object(
                gate, "MAX_SOURCE_TOTAL", 1), self.assertRaises(gate.VerificationError):
            gate.source_paths(self.repo, SHA)

    def test_source_postcheck_rejects_changed_commit_status_or_inventory(self):
        before = {"sha": SHA, "files": {}}
        for head, status, names in (("b" * 40, b"", ()), (SHA, b" M Cargo.lock\n", ()),
                                    (SHA, b"", ("unexpected",))):
            def git(_repo, *args):
                return head.encode() if args[0] == "rev-parse" else status
            with self.subTest(head=head, status=status, names=names), mock.patch.object(
                    gate.custody, "git", side_effect=git), mock.patch.object(
                    gate, "source_paths", return_value=names), self.assertRaises(gate.VerificationError):
                gate.source_after(self.repo, SHA, before)



class SyntheticExecution(unittest.TestCase):
    """Control the gate's orchestration with inert bytes; never invoke a native tool."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saved-mix-orchestration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "source"; self.repo.mkdir()
        self.target, self.output = self.root / "target", self.root / "evidence"
        self.env = environment(self.repo)
        self.tools = {}
        for name in ("rustup", "cargo", "rustc", "cargo-clippy", "clippy-driver"):
            path = self.root / name
            path.write_bytes(("inert synthetic " + name).encode())
            self.tools[name] = {"absolute_path": str(path), **gate.custody.fingerprint(path, self.root)}
        self.before = {"sha": SHA, "tree": "b" * 40, "files": {}}
        self.calls = []
        self.mode = "pass"
        self.ctx = gate.context(self.env, SHA, "saved-mix", "linux")
        self.rosters = {name: tuple(gate.GROUPS[name]["names"]) for name in gate.BINARIES if name != "desktop"}
        self.rosters["desktop"] = (gate.GROUPS["av_master"]["names"]
                                    + gate.GROUPS["original_mix_native"]["names"] + ("other::case",))

    def schemas(self, _repo, *_args):
        return {relative: gate.custody.fingerprint(self.repo / relative, self.repo, gate.MAX_GENERATED)
                for relative in sorted(gate.GENERATED_FILES) if (self.repo / relative).exists()}

    def execute(self, argv, cwd, env, output, label, timeout, ctx):
        self.calls.append((label, argv))
        stdout, stderr, code, observation = b"", b"", 0, "DIRECT_PROCESS_EXIT_OBSERVED"
        if label.startswith("compile-"):
            name = label.removeprefix("compile-")
            spec = gate.BINARIES[name]
            executable = self.target / (name + ".inert")
            executable.write_bytes(("never executed: " + name).encode())
            artifact = {"reason": "compiler-artifact", "fresh": False, "profile": {"test": True},
                        "target": {"name": spec["target"], "kind": [spec["kind"]],
                                   "src_path": str(self.repo / spec["source"])},
                        "manifest_path": str(self.repo / spec["manifest"]), "executable": str(executable)}
            stdout = (json.dumps(artifact) + "\n" + json.dumps({"reason": "build-finished", "success": True}) + "\n").encode()
            if name == "desktop":
                for relative in gate.GENERATED_FILES:
                    path = self.repo / relative; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{"synthetic":"generated"}', encoding="utf-8")
            if self.mode == "compile-failure":
                code, stdout, stderr = 1, b"", b"controlled synthetic compilation failure\n"
        elif label.startswith("list-"):
            names = self.rosters[label.removeprefix("list-")]
            stdout = "\n".join(name + ": test" for name in names).encode() + b"\n"
        elif label.startswith("case-"):
            name = argv[2]
            binary = next(key for key in self.rosters if str(self.target / (key + ".inert")) == argv[0])
            stdout = output_for(name, len(self.rosters[binary]))
            if label == "case-00" and self.mode in ("case-failure", "unknown"):
                code = 1 if self.mode == "case-failure" else None
                observation = ("DIRECT_PROCESS_EXIT_OBSERVED" if code == 1 else "UNKNOWN_TIMEOUT_NO_RETRY")
                stdout = stdout.replace(b"... ok", b"... FAILED")
            if label == "case-40":
                if self.mode == "log-tamper":
                    (output / "compile-original_mix.stdout.log").write_bytes(b"tampered original")
                elif self.mode == "metadata-tamper":
                    (output / "compile-original_mix.command.json").write_bytes(b'{"forged":true}')
                elif self.mode == "binary-tamper":
                    (self.target / "original_mix.inert").write_bytes(b"substituted")
                elif self.mode == "schema-tamper":
                    (self.repo / sorted(gate.GENERATED_FILES)[0]).write_text('{"changed":true}', encoding="utf-8")
        row = {"argv": argv, "cwd": str(cwd), "context": ctx, "exit_code": code,
               "observation": observation, "log_custody": "VERIFIED_SNAPSHOT"}
        for stream, data in (("stdout", stdout), ("stderr", stderr)):
            path = output / (label + "." + stream + ".log")
            with path.open("xb") as handle:
                handle.write(data)
            row[stream] = gate.custody.fingerprint(path, output)
        gate.custody.write_new(output / (label + ".command.json"), row)
        return row

    def inventory(self, execution, phase):
        if self.mode == "inventory-unknown" and phase == "postcheck":
            execution.unknown = True
            raise gate.VerificationError("synthetic inventory UNKNOWN")
        return []

    def run_gate(self, mode="pass"):
        self.mode = mode
        original_context = gate.context
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
                gate, "context", side_effect=lambda env, sha, selection:
                    original_context(env, sha, selection, "linux")), mock.patch.object(
                gate, "snapshot", return_value=self.before), mock.patch.object(
                gate, "source_after", side_effect=(gate.VerificationError("synthetic source changed")
                                                  if mode == "source-tamper" else None),
                return_value=self.before), mock.patch.object(
                gate, "capture_tools", return_value=self.tools), mock.patch.object(
                gate, "generated_permissions", return_value={}), mock.patch.object(
                gate, "generated_inventory", side_effect=self.inventory), mock.patch.object(
                gate, "generated_schemas", side_effect=self.schemas), mock.patch.object(
                gate.custody, "execute", side_effect=self.execute):
            return gate.run(self.repo, SHA, self.output, self.target, "saved-mix")

    def test_complete_synthetic_flow_keeps_70_rows_four_binaries_and_originals(self):
        code, receipt = self.run_gate()
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "PASS_SELECTED_SAVED_MIX_70")
        self.assertEqual((receipt["passed"], receipt["not_run"]), (70, 0))
        self.assertEqual(len(receipt["binaries"]), 4)
        self.assertEqual(len(receipt["retained_generated_schemas"]), 4)
        self.assertEqual(receipt["source_before"], receipt["source_after"])
        self.assertEqual(len(receipt["commands"]), len(receipt["command_originals"]))
        self.assertEqual(len([x for x in self.calls if x[0].startswith("case-")]), 70)
        check = next(argv for label, argv in self.calls if label == "desktop-check")
        clippy = next(argv for label, argv in self.calls if label == "strict-clippy")
        self.assertIn("--locked", check); self.assertIn("--locked", clippy)
        self.assertEqual(clippy[-3:], ["--", "-D", "warnings"])
        self.assertIn("--all-targets", clippy)
        self.assertEqual(json.loads((self.output / "receipt.json").read_text())["passed"], 70)

    def test_case_failure_is_recorded_as_failed_and_remaining_cases_are_not_run(self):
        code, receipt = self.run_gate("case-failure")
        self.assertEqual(code, 2)
        self.assertEqual((receipt["failed"], receipt["not_run"]), (1, 69))
        self.assertEqual(receipt["cases"][0]["command"]["exit_code"], 1)
        self.assertEqual(len(receipt["commands"]), len(receipt["command_originals"]))
        self.assertEqual(len([x for x in self.calls if x[0].startswith("case-")]), 1)

    def test_timeout_stays_unknown_without_retry_or_further_selected_invocations(self):
        code, receipt = self.run_gate("unknown")
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(receipt["postcheck"], "NOT_RUN_UNKNOWN_PROCESS_OR_CUSTODY")
        self.assertNotIn("source_after", receipt)
        self.assertEqual((receipt["unknown"], receipt["not_run"]), (1, 69))
        self.assertEqual(len([x for x in self.calls if x[0].startswith("case-")]), 1)

    def test_compile_failure_does_not_start_any_selected_case(self):
        code, receipt = self.run_gate("compile-failure")
        self.assertEqual(code, 2)
        self.assertEqual(receipt["not_run"], 70)
        self.assertEqual((len(receipt["commands"]), len(receipt["command_originals"])), (1, 1))
        self.assertEqual(len(self.calls), 1)

    def test_unknown_final_inventory_stops_postcheck_and_never_claims_pass(self):
        code, receipt = self.run_gate("inventory-unknown")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(receipt["postcheck"], "STOPPED_UNKNOWN_PROCESS_OR_CUSTODY")
        self.assertNotIn("artifacts_before_receipt", receipt)

    def test_source_change_after_successful_synthetic_cases_blocks_overall_pass(self):
        code, receipt = self.run_gate("source-tamper")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertIn("source changed", receipt["postcheck_error"])

    def test_changed_original_stdout_blocks_overall_pass(self):
        code, receipt = self.run_gate("log-tamper")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertIn("original stream changed", receipt["postcheck_error"])

    def test_changed_original_command_metadata_blocks_overall_pass(self):
        code, receipt = self.run_gate("metadata-tamper")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertIn("original stream changed", receipt["postcheck_error"])

    def test_changed_already_executed_binary_blocks_overall_pass(self):
        code, receipt = self.run_gate("binary-tamper")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertIn("compiled test executable changed", receipt["postcheck_error"])

    def test_changed_generated_desktop_schema_blocks_overall_pass(self):
        code, receipt = self.run_gate("schema-tamper")
        self.assertEqual((code, receipt["passed"]), (2, 70))
        self.assertIn("schemas changed during tests", receipt["postcheck_error"])




class GeneratedPermissionContracts(unittest.TestCase):
    """Actual Git inventory custody; fixture permissions are not Native results."""
    BOOTSTRAP = b"""# Automatically generated - DO NOT EDIT!

[[permission]]
identifier = "allow-bootstrap"
description = "Enables the bootstrap command without any pre-configured scope."
commands.allow = ["bootstrap"]

[[permission]]
identifier = "deny-bootstrap"
description = "Denies the bootstrap command without any pre-configured scope."
commands.deny = ["bootstrap"]
"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="saved-mix-generated-output-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "source"; self.repo.mkdir()
        self.output = self.root / "originals"; self.output.mkdir()
        for name in ("Cargo.lock", "apps/desktop/src-tauri/build.rs"):
            path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
        (self.repo / ".gitignore").write_text(
            "apps/desktop/src-tauri/gen/\napps/desktop/src-tauri/permissions/autogenerated/\nignored-extra.bin\n",
            encoding="utf-8")
        gate.custody.git(self.repo, "init", "--quiet")
        gate.custody.git(self.repo, "add", "--", ".gitignore", "Cargo.lock",
                         "apps/desktop/src-tauri/build.rs")
        self.policy = gate.generated_permissions(self.repo)
        self.path = self.repo / (gate.GENERATED_PERMISSION_ROOT + "bootstrap.toml")
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(self.BOOTSTRAP)
        self.execution = gate.Execution(self.repo, self.output, dict(os.environ),
                                         {"test": "original Git inventory; no Native effects"})

    def test_source_roster_and_independent_provider_byte_expectations(self):
        self.assertEqual(len(self.policy), 63)
        self.assertEqual(sum(map(len, self.policy.values())), 27834)
        self.assertEqual(self.policy[gate.GENERATED_PERMISSION_ROOT + "bootstrap.toml"], self.BOOTSTRAP)
        self.assertEqual(hashlib.sha256(self.BOOTSTRAP).hexdigest(),
                         "e249330f5e035d21dbf7ed00caa59fd1795c8f9500d4e5c5da7d8f989eb21c3e")
        self.assertEqual(hashlib.sha256(
            self.policy[gate.GENERATED_PERMISSION_ROOT + "apply_change.toml"]).hexdigest(),
            "d86b13e4fd459ab458d94026ae2e881905db91d087bd6c098af36c1c458f8edb")

    def test_real_git_inventory_retains_both_streams_before_permission_admission(self):
        raw = gate.generated_inventory(self.execution, "focused")
        value = gate.generated_schemas(self.repo, self.policy, raw)
        self.assertEqual(set(value), {self.path.relative_to(self.repo).as_posix()})
        self.assertEqual(len(self.execution.commands), 2)
        for index, command in enumerate(self.execution.commands):
            self.assertEqual(command["exit_code"], 0)
            self.assertEqual(command["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
            self.assertEqual(gate.original_stream(command, "stdout", self.output), raw[index])
            self.assertEqual(gate.original_stream(command, "stderr", self.output), b"")
        self.assertIn(self.path.relative_to(self.repo).as_posix().encode() + b"\0", raw[1])

    def test_inherited_git_overrides_cannot_hide_inputs_or_change_cargo_environment(self):
        foreign = self.root / "foreign"; foreign.mkdir()
        gate.custody.git(foreign, "init", "--quiet")
        excludes = self.root / "exclude-everything"; excludes.write_text("*\n")
        overrides = {
            "GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
            "GIT_INDEX_FILE": str(foreign / ".git/index"),
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile",
            "GIT_CONFIG_VALUE_0": str(excludes), "GIT_NO_REPLACE_OBJECTS": "0",
        }
        (self.repo / "injected.rs").write_bytes(b"inert rejected input")
        self.execution.env.update(overrides)
        before = dict(self.execution.env)
        raw = gate.generated_inventory(self.execution, "isolated")
        self.assertEqual(self.execution.env, before)
        self.assertIn(b"injected.rs\0", raw[0])
        with self.assertRaisesRegex(gate.VerificationError, "unexpected untracked"):
            gate.generated_schemas(self.repo, self.policy, raw)
        for command in self.execution.commands:
            self.assertEqual(command["argv"][1:4],
                             ["--no-replace-objects", "-c", "core.fsmonitor=false"])
        with mock.patch.dict(os.environ, overrides):
            self.assertEqual(gate.custody.git(
                self.repo, "ls-files", "-z", "--others", "--exclude-standard"), raw[0])

    def test_unexpected_untracked_and_ignored_paths_preserve_original_rejected_roster(self):
        (self.repo / "injected.rs").write_bytes(b"inert rejected source")
        (self.repo / "ignored-extra.bin").write_bytes(b"inert rejected input")
        raw = gate.generated_inventory(self.execution, "rejected")
        with self.assertRaisesRegex(gate.VerificationError, "unexpected untracked or ignored"):
            gate.generated_schemas(self.repo, self.policy, raw)
        self.assertIn(b"injected.rs\0", gate.original_stream(
            self.execution.commands[0], "stdout", self.output))
        self.assertIn(b"ignored-extra.bin\0", gate.original_stream(
            self.execution.commands[1], "stdout", self.output))
        self.assertEqual(len(self.execution.originals), 2)

    def test_duplicate_inventory_is_distinct_from_an_unreviewed_path(self):
        name = self.path.relative_to(self.repo).as_posix().encode() + b"\0"
        with self.assertRaisesRegex(gate.VerificationError, "duplicated untracked"):
            gate.generated_schemas(self.repo, self.policy, [name, name])

    def test_valid_toml_with_added_scope_is_rejected_byte_for_byte(self):
        self.path.write_bytes(self.BOOTSTRAP + b'\n[scope]\nallow = ["foreign"]\n')
        raw = gate.generated_inventory(self.execution, "changed")
        with self.assertRaisesRegex(gate.VerificationError, "exact source contract"):
            gate.generated_schemas(self.repo, self.policy, raw)

    def test_empty_preexisting_permission_directory_blocks_preflight(self):
        self.path.unlink()
        with self.assertRaisesRegex(gate.VerificationError, "absent before compilation"):
            gate.snapshot(self.repo, SHA)

    def test_redirected_permission_parent_blocks_preflight_before_git(self):
        self.path.unlink()
        self.path.parent.rmdir()
        parent = self.path.parent.parent
        original_lstat = Path.lstat
        def observed(path, *args, **kwargs):
            if path == parent:
                return mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", observed), mock.patch.object(
                gate.custody, "git") as git:
            with self.assertRaisesRegex(gate.VerificationError, "parent redirected"):
                gate.snapshot(self.repo, SHA)
        git.assert_not_called()

    def test_hardlinked_permission_cannot_be_admitted(self):
        outside = self.root / "outside.toml"
        outside.write_bytes(self.BOOTSTRAP)
        self.path.unlink()
        os.link(outside, self.path)
        raw = gate.generated_inventory(self.execution, "linked")
        with self.assertRaises(ValueError):
            gate.generated_schemas(self.repo, self.policy, raw)

    def test_changed_provider_or_nonliteral_command_policy_is_rejected(self):
        path = self.repo / "Cargo.lock"
        original = path.read_text()
        checksum = gate.GENERATED_PROVIDERS["tauri-build"][1]
        path.write_text(original.replace(checksum, "0" * 64), encoding="utf-8")
        with self.assertRaisesRegex(gate.VerificationError, "provider differs"):
            gate.generated_permissions(self.repo)
        path.write_text(original, encoding="utf-8")
        build = self.repo / "apps/desktop/src-tauri/build.rs"
        raw = build.read_text()
        for value in (raw.replace('"bootstrap",', 'unreviewed_expression,'),
                      raw.replace('"list_projects",', '"bootstrap",')):
            build.write_text(value, encoding="utf-8")
            with self.assertRaises(gate.VerificationError):
                gate.generated_permissions(self.repo)
        build.write_text(raw, encoding="utf-8")
        with mock.patch.object(gate, "MAX_PERMISSION_TOTAL", 1):
            with self.assertRaisesRegex(gate.VerificationError, "aggregate generated permissions"):
                gate.generated_permissions(self.repo)

    def test_generator_source_must_match_the_original_source_snapshot(self):
        names = ("Cargo.lock", "apps/desktop/src-tauri/build.rs")
        before = {"files": {name: gate.custody.fingerprint(self.repo / name, self.repo)
                            for name in names}}
        self.assertEqual(gate.generated_permissions(self.repo, before), self.policy)
        with (self.repo / names[1]).open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(gate.VerificationError, "differs from snapshot"):
            gate.generated_permissions(self.repo, before)


class SharedObservationStopContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.execution = gate.Execution(self.root, self.root, {}, {})

    def test_real_nonzero_exit_retains_complete_command_and_stream_inventory(self):
        self.execution.env = dict(os.environ)
        argv = [sys.executable, "-I", "-c",
                "import sys; sys.stdout.buffer.write(b'known output\\n'); "
                "sys.stderr.buffer.write(b'known failure\\n'); sys.exit(7)"]
        with self.assertRaisesRegex(gate.VerificationError, "command failed or became UNKNOWN: known-failure"):
            self.execution.run(argv, "known-failure", 30)
        self.assertFalse(self.execution.unknown)
        self.assertEqual(len(self.execution.commands), 1)
        self.assertEqual(len(self.execution.originals), 1)
        command = self.execution.commands[0]
        self.assertEqual(command["exit_code"], 7)
        self.assertEqual(command["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        raw = (self.root / "known-failure.command.json").read_bytes()
        self.assertEqual(json.loads(raw), command)
        self.assertEqual(self.execution.originals[0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(gate.original_stream(command, "stdout", self.root), b"known output\n")
        self.assertEqual(gate.original_stream(command, "stderr", self.root), b"known failure\n")

    def test_live_group_unknown_is_sticky_and_forbids_second_command(self):
        row = {"observation": "UNKNOWN_PROCESS_NO_RETRY", "exit_code": 0, "pid": 123}
        with mock.patch.object(gate.custody, "execute", return_value=row) as execute, \
                mock.patch.object(gate.custody, "fingerprint") as readback:
            with self.assertRaises(gate.VerificationError):
                self.execution.run(["synthetic"], "first", 120)
            with self.assertRaisesRegex(gate.VerificationError, "prior UNKNOWN"):
                self.execution.run(["forbidden"], "second", 120)
        execute.assert_called_once()
        readback.assert_not_called()
        self.assertTrue(self.execution.unknown)
        self.assertEqual(self.execution.commands, [row])

    def test_persistence_unknown_is_sticky_and_keeps_unsealed_command(self):
        row = {"observation": "UNKNOWN_CUSTODY_NO_RETRY", "pid": 456}
        with mock.patch.object(gate.custody, "execute", side_effect=gate.custody.UnknownObservation("partial", row)) as execute:
            with self.assertRaises(gate.VerificationError):
                self.execution.run(["synthetic"], "first", 120)
            with self.assertRaisesRegex(gate.VerificationError, "prior UNKNOWN"):
                self.execution.run(["forbidden"], "second", 120)
        execute.assert_called_once()
        self.assertTrue(self.execution.unknown)
        self.assertEqual(self.execution.commands, [row])

    def test_metadata_readback_failure_keeps_known_exit_and_stops_commands(self):
        row = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0,
               "pid": 123, "log_custody": "VERIFIED_SNAPSHOT"}
        with mock.patch.object(gate.custody, "execute", return_value=row) as execute, \
                mock.patch.object(gate.custody, "fingerprint", side_effect=OSError("metadata unavailable")):
            with self.assertRaisesRegex(gate.VerificationError, "custody readback failed"):
                self.execution.run(["synthetic"], "first", 120)
            with self.assertRaisesRegex(gate.VerificationError, "prior UNKNOWN"):
                self.execution.run(["forbidden"], "second", 120)
        self.assertTrue(self.execution.unknown)
        self.assertEqual(row["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        unsealed = self.execution.commands[0]
        self.assertEqual(unsealed["original_command"], row)
        self.assertEqual(unsealed["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
        self.assertEqual(unsealed["exit_code"], 0)
        self.assertEqual(unsealed["secondary_observation_errors"], ["OSError: metadata unavailable"])
        self.assertEqual(self.execution.originals, [])
        execute.assert_called_once()

    def test_stream_readback_failure_after_metadata_remains_unknown(self):
        row = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0,
               "pid": 456, "log_custody": "VERIFIED_SNAPSHOT"}
        original = {"path": "first.command.json"}
        with mock.patch.object(gate.custody, "execute", return_value=row) as execute, \
                mock.patch.object(gate.custody, "fingerprint", return_value=original), \
                mock.patch.object(gate, "original_stream", side_effect=[
                    json.dumps(row).encode(), OSError("stdout unavailable")]):
            with self.assertRaisesRegex(gate.VerificationError, "custody readback failed"):
                self.execution.run(["synthetic"], "first", 120)
            with self.assertRaisesRegex(gate.VerificationError, "prior UNKNOWN"):
                self.execution.run(["forbidden"], "second", 120)
        self.assertTrue(self.execution.unknown)
        self.assertEqual(self.execution.commands[0]["original_command"], row)
        self.assertEqual(self.execution.commands[0]["secondary_observation_errors"], ["OSError: stdout unavailable"])
        self.assertEqual(self.execution.originals, [original])
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
