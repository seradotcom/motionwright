"""Causal admission and source custody controls; no Rust execution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from tooling import ci_foundation_causal as gate


class FoundationCausalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="foundation-causal-g0-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pair = gate.PAIRS[0]
        self.context = {"source": self.pair["baseline"]["sha"], "attempt": "1"}
        self.binary = {"absolute_path": str(self.root / "test-binary"),
                       "path": "test-binary", "bytes": 5, "sha256": "a" * 64,
                       "identity": {"size": 5, "mtime_ns": 1}}
        self.names = self.pair["baseline"]["targets"]["jobs"]
        self.roster = {"all": tuple(self.names), "selected": tuple(self.names)}

    def context_environment(self):
        return {
            "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "FOUNDATION_CAUSAL_SELECTION": "foundation-causal",
            "GITHUB_SHA": "1" * 40, "GITHUB_WORKFLOW_SHA": "1" * 40,
            "GITHUB_JOB": "foundation-causal", "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "example/project",
            "GITHUB_WORKFLOW_REF": "example/project/" + gate.WORKFLOW + "@refs/heads/verify/fixture",
        }

    def raw_case(self, name, failure=None, total=4):
        state = "FAILED" if failure else "ok"
        text = "running 1 test\ntest " + name + " ... " + state + "\n"
        if failure:
            text += ("\nfailures:\n\n---- " + name + " stdout ----\n\nthread '" + name
                     + "' panicked at " + failure["source"] + ":" + str(failure["line"])
                     + ":9:\n" + "\n".join(failure["markers"]) + "\n")
        text += ("\ntest result: " + state + ". " + ("0 passed; 1 failed" if failure else
                  "1 passed; 0 failed") + "; 0 ignored; 0 measured; " + str(total - 1)
                 + " filtered out; finished in 0.01s\n")
        return text.encode()

    def observed(self, name, role="baseline", pair=None):
        pair = self.pair if pair is None else pair
        return {"observation": "DIRECT_PROCESS_EXIT_OBSERVED",
                "log_custody": "VERIFIED_SNAPSHOT",
                "argv": gate.case_argv(self.binary, pair, role, name),
                "context": self.context,
                "exit_code": 101 if role == "baseline" and name in pair["failures"] else 0}

    def admit(self, name, raw=None, row=None, role="baseline"):
        failure = self.pair["failures"].get(name) if role == "baseline" else None
        return gate.admit_case(self.pair, role, name,
            self.raw_case(name, failure) if raw is None else raw,
            self.observed(name, role) if row is None else row,
            self.binary, dict(self.binary), self.roster, self.context)

    def test_catalogue_has_33_baseline_and_55_repair_invocations(self):
        pairs = gate.validate_catalogue()
        counts = [sum(len(names) for p in pairs for names in p[r]["targets"].values())
                  for r in gate.ROLES]
        self.assertEqual(counts, [33, 55])
        self.assertEqual(sum(len(p["failures"]) for p in pairs), 25)
        self.assertEqual(len({p[r]["sha"] for p in pairs for r in gate.ROLES}), 8)

    def test_svg_decoded_seven_are_a_subset_and_budget_four_are_distinct(self):
        svg = gate.PAIRS[1]
        references = svg["repair"]["targets"]["svg-references"]
        self.assertEqual(len(references), 13)
        self.assertEqual(references, svg["baseline"]["targets"]["svg-references"])
        self.assertEqual(len(set(references[:7])), 7)
        budget = svg["repair"]["targets"]["svg-budget"]
        self.assertEqual(len(budget), 4)
        self.assertTrue(set(references).isdisjoint(budget))

    def test_baseline_fifo_is_forbidden_even_when_added_to_the_selection(self):
        pair = copy.deepcopy(gate.PAIRS[2])
        name = "file_import::source_open_tests::replaced_fifo_source_is_rejected_without_waiting_for_a_writer"
        pair["baseline"]["targets"]["import-storage"].append(name)
        with self.assertRaisesRegex(ValueError, "FIFO"):
            gate.case_argv(self.binary, pair, "baseline", name)

    def test_duplicate_or_removed_cases_and_missing_control_are_rejected(self):
        for mutation in ("duplicate", "remove", "all_fail"):
            pairs = copy.deepcopy(gate.PAIRS)
            names = pairs[0]["baseline"]["targets"]["jobs"]
            if mutation == "duplicate":
                names[1] = names[0]
            elif mutation == "remove":
                names.pop()
            else:
                pairs[0]["failures"][names[-1]] = next(iter(pairs[0]["failures"].values()))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_catalogue(pairs)

    def test_frozen_source_requires_full_sha_and_tree(self):
        for key in ("sha", "tree"):
            pairs = copy.deepcopy(gate.PAIRS)
            pairs[0]["repair"][key] = "abc123"
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.validate_catalogue(pairs)

    def test_explicit_linux_selected_context_is_admitted(self):
        result = gate.execution_context(self.context_environment(), "linux")
        self.assertEqual(result["GITHUB_RUN_ATTEMPT"], "1")
        self.assertNotIn("FOUNDATION_CAUSAL_SELECTION", result)

    def test_host_automatic_wrong_workflow_and_unbound_attempt_are_rejected(self):
        cases = [("RUNNER_OS", "Windows"), ("RUNNER_ARCH", "ARM64"),
                 ("GITHUB_EVENT_NAME", "push"), ("FOUNDATION_CAUSAL_SELECTION", "full"),
                 ("GITHUB_WORKFLOW_SHA", "2" * 40), ("GITHUB_RUN_ATTEMPT", "0"),
                 ("GITHUB_JOB", "controlled-imports"),
                 ("GITHUB_WORKFLOW_REF", "example/project/other.yml@refs/heads/main")]
        for key, value in cases:
            env = dict(self.context_environment(), **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.execution_context(env, "linux")
        with self.assertRaises(ValueError):
            gate.execution_context(self.context_environment(), "win32")

    def test_build_overrides_are_rejected_instead_of_silently_reused(self):
        for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER",
                    "RUSTC", "CARGO_BUILD_TARGET", "RUST_TEST_THREADS",
                    "MOTIONWRIGHT_TEST_BYPASS"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                gate.build_environment({key: "value"}, self.root / "target", self.root / "tmp")
        env = gate.build_environment({}, self.root / "target", self.root / "tmp")
        self.assertEqual(env["CARGO_INCREMENTAL"], "0")
        self.assertEqual(env["RUST_BACKTRACE"], "0")

    def test_compilation_and_clippy_are_locked_and_target_specific(self):
        for spec in gate.TARGETS.values():
            compiled = gate.compile_argv("cargo-exact", spec)
            strict = gate.clippy_argv("cargo-exact", spec)
            self.assertEqual(compiled[0], "cargo-exact")
            self.assertIn("--locked", compiled)
            self.assertIn("--no-run", compiled)
            self.assertIn("--message-format=json", compiled)
            self.assertIn("--locked", strict)
            self.assertEqual(strict[-3:], ["--", "-D", "warnings"])
            self.assertNotIn("--all-targets", compiled + strict)

    def test_source_inventory_includes_locks_dependencies_vendor_and_workspace_manifests(self):
        source = self.pair["baseline"]
        names = set(gate.ROOT_INPUTS) | set(gate.CORE_INPUTS) | set(source["fixture_sha256"])
        names |= {gate.TARGETS[t]["source"] for t in source["targets"]}
        names |= {".cargo/config.toml", "vendor/reviewed/source.rs",
                  "apps/desktop/src-tauri/Cargo.toml", "crates/native/build.rs",
                  "apps/studio/src/App.tsx"}
        raw = b"\0".join(p.encode() for p in sorted(names)) + b"\0"
        with patch.object(gate.custody, "git", return_value=raw):
            selected = gate.input_paths(self.root, source["sha"], source)
        self.assertIn("vendor/reviewed/source.rs", selected)
        self.assertIn("apps/desktop/src-tauri/Cargo.toml", selected)
        self.assertIn("crates/native/build.rs", selected)
        self.assertNotIn("apps/studio/src/App.tsx", selected)

    def test_missing_lock_or_fixture_is_not_an_attributed_source(self):
        source = self.pair["baseline"]
        for missing in ("Cargo.lock", next(iter(source["fixture_sha256"]))):
            names = set(gate.ROOT_INPUTS) | set(gate.CORE_INPUTS) | set(source["fixture_sha256"])
            names.discard(missing)
            raw = b"\0".join(p.encode() for p in sorted(names)) + b"\0"
            with self.subTest(missing=missing), patch.object(gate.custody, "git", return_value=raw):
                with self.assertRaises(ValueError):
                    gate.input_paths(self.root, source["sha"], source)

    def test_physical_bytes_are_checked_even_when_git_status_claims_clean(self):
        path = self.root / "sample.rs"
        original = b"source original\n"
        path.write_bytes(original)
        blob = hashlib.sha1(b"blob " + str(len(original)).encode() + b"\0" + original).hexdigest()
        raw_commit = ("tree " + "2" * 40 + "\n\nsynthetic custody fixture\n").encode()
        sha = hashlib.sha1(b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit).hexdigest()
        def fake_git(_repo, *args):
            if args[:1] == ("rev-parse",):
                return sha.encode()
            if args == ("cat-file", "commit", sha):
                return raw_commit
            if args[:1] == ("status",):
                return b""
            if args[:1] == ("ls-tree",):
                return ("100644 blob " + blob + "\tsample.rs\0").encode()
            if args == ("cat-file", "blob", blob):
                return original
            self.fail("unexpected Git read " + repr(args))
        with patch.object(gate.custody, "git", side_effect=fake_git):
            snapshot = gate.custody.source_snapshot(self.root, sha, ("sample.rs",), "2" * 40)
            self.assertEqual(snapshot["files"]["sample.rs"]["sha256"], hashlib.sha256(original).hexdigest())
            path.write_bytes(b"hidden mutation\n")
            with self.assertRaisesRegex(ValueError, "worktree bytes differ"):
                gate.custody.source_snapshot(self.root, sha, ("sample.rs",), "2" * 40)

    def test_fixture_hash_is_checked_beyond_the_selected_commit(self):
        source = self.pair["baseline"]
        files = {name: {"sha256": "0" * 64} for name in source["fixture_sha256"]}
        with patch.object(gate, "input_paths", return_value=tuple(files)), \
             patch.object(gate.custody, "source_snapshot", return_value={"files": files}):
            with self.assertRaisesRegex(ValueError, "frozen fixture changed"):
                gate.product_snapshot(self.root, source)

    def test_retained_copy_preserves_original_bytes_and_rejects_changed_destination(self):
        source = self.root / "source"
        source.write_bytes(b"original immutable source")
        output = self.root / "output"
        output.mkdir()
        retained = gate.retain_file(source, self.root, output, "sources")
        destination = output / retained["path"]
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        destination.write_bytes(b"changed retained copy")
        with self.assertRaises(ValueError):
            gate.retain_file(source, self.root, output, "sources")

    def test_retained_copy_rejects_changed_source_during_copy(self):
        source = self.root / "source"
        source.write_bytes(b"before")
        output = self.root / "output"
        output.mkdir()
        def mutate(src, dst, _length):
            dst.write(src.read())
            source.write_bytes(b"after!")
        with patch.object(gate.shutil, "copyfileobj", side_effect=mutate):
            with self.assertRaises(ValueError):
                gate.retain_file(source, self.root, output, "sources")

    def compile_fixture(self):
        repo = self.root / "repo"
        repo.mkdir()
        target = self.root / "target"
        target.mkdir()
        path = target / "test-binary"
        path.write_bytes(b"synthetic binary bytes; never executed")
        spec = gate.TARGETS["jobs"]
        artifact = {"reason": "compiler-artifact", "executable": str(path),
            "target": {"name": spec["name"], "kind": ["test"], "src_path": str(repo / spec["source"])},
            "profile": {"test": True}, "manifest_path": str(repo / "crates/service/Cargo.toml")}
        return repo, target, spec, artifact

    def cargo_json(self, items):
        return ("\n".join(json.dumps(x) for x in items) + "\n").encode()

    def test_compiled_binary_is_bound_to_manifest_source_and_physical_bytes(self):
        repo, target, spec, artifact = self.compile_fixture()
        raw = self.cargo_json([artifact, {"reason": "build-finished", "success": True}])
        observed = gate.select_binary(raw, 0, repo, target, spec)
        self.assertEqual(observed["sha256"], hashlib.sha256((target / "test-binary").read_bytes()).hexdigest())

    def test_compiler_failure_is_never_baseline_red(self):
        repo, target, spec, artifact = self.compile_fixture()
        raw = self.cargo_json([artifact, {"reason": "build-finished", "success": True}])
        for code in (1, 101, -9):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "compilation failure"):
                gate.select_binary(raw, code, repo, target, spec)

    def test_ambiguous_missing_or_unsuccessful_cargo_artifact_is_rejected(self):
        repo, target, spec, artifact = self.compile_fixture()
        variants = [[artifact, artifact, {"reason": "build-finished", "success": True}],
                    [{"reason": "build-finished", "success": True}],
                    [artifact, {"reason": "build-finished", "success": False}], [artifact]]
        for items in variants:
            with self.subTest(items=len(items)), self.assertRaises(ValueError):
                gate.select_binary(self.cargo_json(items), 0, repo, target, spec)

    def test_foreign_manifest_source_or_binary_path_is_rejected(self):
        repo, target, spec, artifact = self.compile_fixture()
        outside = self.root / "outside"
        outside.write_bytes(b"not a compiled target")
        for field in ("manifest", "source", "binary"):
            changed = copy.deepcopy(artifact)
            if field == "manifest":
                changed["manifest_path"] = str(repo / "other/Cargo.toml")
            elif field == "source":
                changed["target"]["src_path"] = str(repo / "other.rs")
            else:
                changed["executable"] = str(outside)
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.select_binary(self.cargo_json([changed, {"reason": "build-finished", "success": True}]),
                                   0, repo, target, spec)

    def roster_bytes(self, names):
        return ("\n".join(name + ": test" for name in names) +
                "\n\n" + str(len(names)) + " tests, 0 benchmarks\n").encode()

    def test_enumeration_requires_every_integration_case_and_no_extra(self):
        result = gate.selected_roster(self.roster_bytes(self.names), 0,
                                      gate.TARGETS["jobs"], self.names, "baseline")
        self.assertEqual(set(result["selected"]), set(self.names))
        for names in (self.names[:-1], [*self.names, "extra_case"], [*self.names, self.names[0]], []):
            with self.subTest(names=len(names)), self.assertRaises(ValueError):
                gate.selected_roster(self.roster_bytes(names), 0, gate.TARGETS["jobs"],
                                     self.names, "baseline")

    def test_unselected_storage_names_are_retained_without_credit(self):
        names = gate.PAIRS[2]["repair"]["targets"]["import-storage"]
        result = gate.selected_roster(self.roster_bytes([*names, "unrelated::case"]), 0,
                                      gate.TARGETS["import-storage"], names, "repair")
        self.assertEqual(result["not_selected"], ("unrelated::case",))
        self.assertEqual(result["not_selected_credit"], "NONE")
        with self.assertRaises(ValueError):
            gate.selected_roster(self.roster_bytes([*names, names[0] + "_new"]), 0,
                                 gate.TARGETS["import-storage"], names, "repair")

    def test_enumeration_error_or_ignored_listing_is_rejected(self):
        raw = self.roster_bytes(self.names)
        with self.assertRaises(ValueError):
            gate.selected_roster(raw, 1, gate.TARGETS["jobs"], self.names, "baseline")
        with self.assertRaises(ValueError):
            gate.selected_roster(raw.replace(b": test", b": ignored", 1), 0,
                                 gate.TARGETS["jobs"], self.names, "baseline")

    def test_all_25_named_causal_sites_and_messages_are_admitted(self):
        count = 0
        for pair in gate.PAIRS:
            for name, failure in pair["failures"].items():
                target_names = next(names for names in pair["baseline"]["targets"].values() if name in names)
                roster = {"all": tuple(target_names), "selected": tuple(target_names)}
                result = gate.admit_case(pair, "baseline", name,
                    self.raw_case(name, failure, len(target_names)),
                    self.observed(name, pair=pair), self.binary, dict(self.binary), roster, self.context)
                self.assertEqual(result["status"], "EXPECTED_CAUSAL_RED")
                count += 1
        self.assertEqual(count, 25)

    def test_baseline_controls_and_repair_require_real_pass(self):
        control = next(name for name in self.names if name not in self.pair["failures"])
        self.assertEqual(self.admit(control)["status"], "PASS")
        repaired = self.names[0]
        self.assertEqual(self.admit(repaired, role="repair")["status"], "PASS")
        bad = self.observed(repaired, role="repair")
        bad["exit_code"] = 101
        with self.assertRaises(ValueError):
            self.admit(repaired, raw=self.raw_case(repaired, self.pair["failures"][repaired]),
                       row=bad, role="repair")

    def test_baseline_pass_cannot_replace_expected_red(self):
        name = self.names[0]
        row = self.observed(name)
        row["exit_code"] = 0
        with self.assertRaises(ValueError):
            self.admit(name, raw=self.raw_case(name), row=row)

    def test_nonassertion_exit_is_not_causal_red(self):
        name = self.names[0]
        for code in (1, 2, 124, -9, None):
            row = self.observed(name)
            row["exit_code"] = code
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.admit(name, row=row)

    def test_wrong_source_line_or_assertion_message_is_not_causal_red(self):
        name = self.names[0]
        failure = self.pair["failures"][name]
        raw = self.raw_case(name, failure)
        variants = [
            raw.replace(failure["source"].encode(), b"other.rs"),
            raw.replace((":" + str(failure["line"]) + ":9:").encode(), b":1:9:"),
            raw.replace(failure["markers"][0].encode(), b"setup failed"),
            raw.replace(b"left: Running", b"left: Completed"),
        ]
        for changed in variants:
            with self.subTest(output=changed[-80:]), self.assertRaises(ValueError):
                self.admit(name, raw=changed)

    def test_unrelated_or_additional_panic_is_rejected(self):
        name = self.names[0]
        raw = self.raw_case(name, self.pair["failures"][name])
        for changed in (raw.replace(("thread '" + name + "'").encode(), b"thread 'setup'"),
                        raw + b"thread 'cleanup' panicked at other.rs:1:2:\ncleanup failed\n"):
            with self.assertRaises(ValueError):
                self.admit(name, raw=changed)

    def test_exact_case_cannot_be_replaced_ignored_repeated_or_filtered(self):
        name = next(n for n in self.names if n not in self.pair["failures"])
        raw = self.raw_case(name)
        variants = [raw.replace(b" ... ok", b" ... ignored"),
                    raw.replace(b"1 passed; 0 failed", b"0 passed; 0 failed"),
                    raw.replace(b"0 ignored", b"1 ignored"),
                    raw.replace(b"3 filtered out", b"4 filtered out"),
                    raw.replace(("test " + name + " ... ok").encode(), b"test unrelated ... ok"),
                    raw + ("test " + name + " ... ok\n").encode(),
                    raw + raw]
        for changed in variants:
            with self.subTest(output=changed[-80:]), self.assertRaises(ValueError):
                self.admit(name, raw=changed)

    def test_command_context_and_binary_identity_are_bound(self):
        name = self.names[0]
        for mutation in ("argv", "context", "observation", "log_custody"):
            row = self.observed(name)
            if mutation == "argv":
                row["argv"] = [*row["argv"], "--skip", name]
            elif mutation == "context":
                row["context"] = dict(self.context, attempt="2")
            elif mutation == "observation":
                row["observation"] = "UNKNOWN_TIMEOUT_NO_RETRY"
            else:
                row["log_custody"] = "INCOMPLETE"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.admit(name, row=row)
        with self.assertRaises(ValueError):
            gate.admit_case(self.pair, "baseline", name,
                self.raw_case(name, self.pair["failures"][name]), self.observed(name),
                self.binary, dict(self.binary, sha256="b" * 64), self.roster, self.context)

    def test_unknown_command_is_not_retried_or_converted_to_a_result(self):
        executed = Mock(return_value={"observation": "UNKNOWN_TIMEOUT_NO_RETRY"})
        with patch.object(gate.custody, "execute", executed):
            with self.assertRaises(gate.UnknownExecution):
                gate.command(["synthetic-not-executed"], self.root, {}, self.root, "unknown", 1, {})
        self.assertEqual(executed.call_count, 1)

    def test_unknown_stops_repair_and_every_later_pair(self):
        callback = Mock(return_value={"status": "UNKNOWN"})
        result = gate.run_pairs(callback)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual([x["id"] for x in result], ["jobs"])
        self.assertEqual(result[0]["status"], "UNKNOWN")

    def test_observed_failure_retains_failure_and_runs_unblocked_pairs(self):
        observed = []
        def callback(pair, role):
            observed.append((pair["id"], role))
            return {"status": "FAIL" if (pair["id"], role) == ("jobs", "baseline")
                    else "PASS_EXACT_CAUSAL_ROLE"}
        result = gate.run_pairs(callback)
        self.assertEqual(len(observed), 8)
        self.assertEqual(result[0]["status"], "FAIL")
        self.assertTrue(all(x["status"] == "PASS_EXACT_CAUSAL_PAIR" for x in result[1:]))

    def complete_role(self, role="baseline"):
        return {"results": [{"name": n, "status": "EXPECTED_CAUSAL_RED"
                    if role == "baseline" and n in self.pair["failures"] else "PASS"}
                    for n in self.names],
                "source_before": {"sha": "source", "files": {"x": "hash"}},
                "source_after": {"sha": "source", "files": {"x": "hash"}},
                "clippy": "PASS" if role == "repair" else "NOT_SELECTED"}

    def test_role_requires_all_results_source_custody_and_strict_clippy(self):
        result = self.complete_role()
        self.assertEqual(gate.role_complete(self.pair, "baseline", result)["passing_cases"], 2)
        for mutation in ("missing", "duplicate", "wrong_status", "changed_source", "clippy"):
            role = "repair" if mutation == "clippy" else "baseline"
            result = self.complete_role(role)
            if mutation == "missing":
                result["results"].pop()
            elif mutation == "duplicate":
                result["results"][-1] = result["results"][0]
            elif mutation == "wrong_status":
                result["results"][0]["status"] = "PASS"
            elif mutation == "changed_source":
                result["source_after"]["files"]["x"] = "changed"
            else:
                result["clippy"] = "NOT_RUN"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.role_complete(self.pair, role, result)

    def original_fixture(self):
        output = self.root / "output"
        folder = output / "tools"
        folder.mkdir(parents=True)
        row = {"log_custody": "VERIFIED_SNAPSHOT"}
        for stream in ("stdout", "stderr"):
            path = folder / ("original." + stream + ".log")
            path.write_bytes(b"original " + stream.encode())
            row[stream] = gate.custody.fingerprint(path, folder)
        command_path = folder / "original.command.json"
        gate.custody.write_new(command_path, row)
        observed = [{"folder": "tools", "command": row,
                     "receipt": gate.custody.fingerprint(command_path, output)}]
        source = self.root / "source"
        source.write_bytes(b"retained source")
        receipt = {"retained_gate": {"source": gate.retain_file(source, self.root, output, "sources")}}
        return output, observed, receipt

    def test_terminal_audit_rechecks_original_commands_logs_and_retained_source(self):
        output, observed, receipt = self.original_fixture()
        result = gate.verify_originals(output, observed, receipt)
        self.assertEqual(result["commands"], 1)
        self.assertEqual(len(result["files"]), 4)
        self.assertEqual(result["status"], "VERIFIED_BEFORE_TERMINAL_RECEIPT")

    def test_terminal_audit_rejects_changed_stdout(self):
        output, observed, receipt = self.original_fixture()
        (output / "tools/original.stdout.log").write_bytes(b"replaced")
        with self.assertRaisesRegex(ValueError, "original command output changed"):
            gate.verify_originals(output, observed, receipt)

    def test_terminal_audit_rejects_replaced_command_receipt(self):
        output, observed, receipt = self.original_fixture()
        (output / "tools/original.command.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "command receipt changed"):
            gate.verify_originals(output, observed, receipt)

    def test_terminal_audit_rejects_replaced_retained_source(self):
        output, observed, receipt = self.original_fixture()
        (output / receipt["retained_gate"]["source"]["path"]).write_bytes(b"replaced")
        with self.assertRaisesRegex(ValueError, "retained source or executable changed"):
            gate.verify_originals(output, observed, receipt)

    def test_existing_automatic_workflow_and_default_jobs_are_preserved(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        original = text.split("\n  foundation-causal-g0:\n", 1)[0]
        start = original.index("  workflow_dispatch:\n") + len("  workflow_dispatch:\n")
        end = original.index("\npermissions:\n", start)
        original = original[:start] + original[end:]
        original = original.replace(
            "    if: github.event_name != 'workflow_dispatch' || (inputs.verification_scope != 'foundation-causal' && inputs.verification_scope != 'foundation-import-budget' && inputs.verification_scope != 'foundation-current' && inputs.verification_scope != 'foundation-projection-diagnostic' && inputs.verification_scope != 'foundation-composed-current')\n", "", 1)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         "a2acf46f418b035186c9a7b9dce068b3f266e99703f4c773da183a579eb45725")
        self.assertIn("default: full", text)
        self.assertIn("options: [full, foundation-causal, foundation-import-budget, foundation-current, foundation-projection-diagnostic, foundation-composed-current]", text)

    def test_selected_workflow_requires_g0_and_pins_one_sequential_linux_execution(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        selected = text.split("\n  foundation-causal:\n", 1)[1]
        self.assertIn("needs: foundation-causal-g0", selected)
        self.assertIn("github.event_name == 'workflow_dispatch' && (inputs.verification_scope == 'foundation-causal' || inputs.verification_scope == 'foundation-import-budget' || inputs.verification_scope == 'foundation-current' || inputs.verification_scope == 'foundation-projection-diagnostic' || inputs.verification_scope == 'foundation-composed-current')", selected)
        self.assertIn("runs-on: ubuntu-24.04", selected)
        self.assertIn("toolchain: 1.98.1", selected)
        self.assertIn("components: clippy", selected)
        self.assertIn("fetch-depth: 0", selected)
        self.assertEqual(selected.count("gate/tooling/ci_foundation_causal.py"), 1)
        self.assertNotIn("matrix:", selected)
        self.assertNotIn("continue-on-error", selected)
        self.assertIn("if: always()", selected)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref)
                            for ref in re.findall(r"uses: [^@\s]+@([^\s]+)", selected)))


class ImportBudgetCausalTests(unittest.TestCase):
    context_environment = FoundationCausalTests.context_environment
    raw_case = FoundationCausalTests.raw_case
    observed = FoundationCausalTests.observed
    roster_bytes = FoundationCausalTests.roster_bytes

    def setUp(self):
        FoundationCausalTests.setUp(self)
        self.pair = gate.IMPORT_BUDGET_PAIR

    def test_reviewed_pair_pins_rosters_fixtures_and_assertions_are_frozen(self):
        pair = gate.validate_import_budget()[0]
        digest = hashlib.sha256(json.dumps(
            pair, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "f3ea904aec670392c4577167bba8537109cbb0030367d523d2e637d2d2c6ba56")
        self.assertEqual([sum(map(len, pair[role]["targets"].values()))
                          for role in gate.ROLES], [21, 27])
        self.assertEqual(len(pair["failures"]), 15)
        self.assertEqual(len({name for names in pair["repair"]["targets"].values()
                              for name in names}), 27)
        self.assertEqual([pair[role]["sha"] for role in gate.ROLES],
                         ["d3cbb9e875ebf39a9bcd1c02ca2786d852378e40",
                          "ec0ed076efe7e9fabbf1385ca83bd747e3b2fade"])

    def test_selection_is_closed_and_never_combines_the_five_pairs(self):
        self.assertEqual([p["id"] for p in gate.selected_pairs("foundation-causal")],
                         ["jobs", "svg", "retained-import", "bounded-receipt"])
        self.assertEqual([p["id"] for p in gate.selected_pairs("foundation-import-budget")],
                         ["import-resource-budget"])
        for value in ("full", "all", "", "import-resource-budget",
                      "foundation-causal,foundation-import-budget", "1" * 40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.selected_pairs(value)

    def test_explicit_selection_must_match_environment_in_both_directions(self):
        for selection in gate.SELECTIONS:
            environment = dict(self.context_environment(),
                               FOUNDATION_CAUSAL_SELECTION=selection)
            result = gate.execution_context(environment, "linux", selection)
            self.assertEqual(result["GITHUB_JOB"], "foundation-causal")
            other = next(s for s in gate.SELECTIONS if s != selection)
            with self.assertRaises(ValueError):
                gate.execution_context(environment, "linux", other)
        with self.assertRaises(ValueError):
            gate.execution_context(self.context_environment(), "linux", "full")

    def test_cli_passes_only_the_closed_choice_and_preserves_old_default(self):
        arguments = ["--workspace", str(self.root), "--output", str(self.root / "output"),
                     "--execute"]
        for selection in gate.SELECTIONS:
            with patch.object(gate, "run", return_value=0) as invoked:
                self.assertEqual(gate.main(arguments + ["--selection", selection]), 0)
                invoked.assert_called_once_with(self.root, self.root / "output", selection)
        with patch.object(gate, "run", return_value=0) as invoked:
            self.assertEqual(gate.main(arguments), 0)
            self.assertEqual(invoked.call_args.args[2], "foundation-causal")

    def test_cli_rejects_unselected_scope_before_any_execution(self):
        arguments = ["--workspace", str(self.root), "--output", str(self.root / "output"),
                     "--execute"]
        with patch.object(gate, "run") as invoked, patch("sys.stderr"), \
                self.assertRaises(SystemExit) as stopped:
            gate.main(arguments + ["--selection", "full"])
        self.assertEqual(stopped.exception.code, 2)
        invoked.assert_not_called()

    def test_import_catalogue_rejects_missing_duplicate_moved_or_uncausal_cases(self):
        for mutation in ("missing", "duplicate", "moved", "repair_replaced", "wrong_cause"):
            pair = copy.deepcopy(self.pair)
            target = pair["repair"]["targets"]["budget-service"]
            if mutation == "missing":
                target.pop()
            elif mutation == "duplicate":
                target[-1] = target[0]
            elif mutation == "moved":
                name = pair["baseline"]["targets"]["budget-service"].pop()
                pair["baseline"]["targets"]["budget-storage"].append(name)
            elif mutation == "repair_replaced":
                target[-1] = "unreviewed_case"
            else:
                next(iter(pair["failures"].values()))["markers"][-1] = "got Err("
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_import_budget(pair)

    def test_new_storage_roster_stays_in_budget_namespace_for_both_roles(self):
        names = self.pair["baseline"]["targets"]["budget-storage"]
        historical = gate.PAIRS[2]["repair"]["targets"]["import-storage"]
        raw = self.roster_bytes([*names, *historical, "unrelated::test"])
        for role in gate.ROLES:
            result = gate.selected_roster(raw, 0, gate.TARGETS["budget-storage"], names, role)
            self.assertEqual(result["selected"], tuple(names))
            self.assertEqual(set(result["not_selected"]), {*historical, "unrelated::test"})
            self.assertEqual(result["not_selected_credit"], "NONE")

    def test_old_storage_roster_never_absorbs_new_budget_cases(self):
        budget = self.pair["baseline"]["targets"]["budget-storage"]
        for role in gate.ROLES:
            names = gate.PAIRS[2][role]["targets"]["import-storage"]
            result = gate.selected_roster(self.roster_bytes([*names, *budget]), 0,
                                          gate.TARGETS["import-storage"], names, role)
            self.assertEqual(result["selected"], tuple(names))
            self.assertEqual(result["not_selected"], tuple(budget))

    def test_service_unit_roster_selects_six_and_retains_unselected_names(self):
        names = self.pair["repair"]["targets"]["budget-service-unit"]
        other = ["audio::tests::historical_mix", "jobs::tests::unrelated"]
        result = gate.selected_roster(self.roster_bytes([*names, *other]), 0,
                                      gate.TARGETS["budget-service-unit"], names, "repair")
        self.assertEqual(result["selected"], tuple(names))
        self.assertEqual(result["not_selected"], tuple(other))

    def test_budget_enumeration_rejects_missing_duplicate_and_extra_in_namespace(self):
        names = self.pair["repair"]["targets"]["budget-storage"]
        for changed in (names[:-1], [*names, names[0]],
                        [*names, "file_import::source_budget_tests::unreviewed_case"]):
            with self.assertRaises(ValueError):
                gate.selected_roster(self.roster_bytes(changed), 0,
                                     gate.TARGETS["budget-storage"], names, "repair")

    def case_fixture(self, name, role):
        names = next(names for names in self.pair[role]["targets"].values() if name in names)
        all_names = [*names, "unselected::control"]
        roster = {"all": tuple(all_names), "selected": tuple(names)}
        failure = self.pair["failures"].get(name) if role == "baseline" else None
        raw = self.raw_case(name, failure, len(all_names))
        observed = self.observed(name, role, self.pair)
        return raw, observed, roster

    def admit_fixture(self, name, role="baseline", raw=None, observed=None):
        original_raw, original_observed, roster = self.case_fixture(name, role)
        return gate.admit_case(
            self.pair, role, name, original_raw if raw is None else raw,
            original_observed if observed is None else observed,
            self.binary, dict(self.binary), roster, self.context)

    def test_all_fifteen_predicted_reds_require_exact_site_and_admitted_value(self):
        for name in self.pair["failures"]:
            with self.subTest(name=name):
                self.assertEqual(self.admit_fixture(name)["status"], "EXPECTED_CAUSAL_RED")
        failures = self.pair["failures"]
        self.assertEqual(failures[
            "source_byte_boundary_applies_to_asset_voice_and_both_host_constructors"]["line"],
            375)
        self.assertEqual(failures[
            "known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives"]["line"],
            411)
        self.assertEqual(failures[
            "packet_byte_budget_rejects_valid_pcm_before_publication"]["line"], 357)

    def test_wrong_err_or_missing_got_ok_never_establishes_import_red(self):
        for name in self.pair["failures"]:
            raw, _, _ = self.case_fixture(name, "baseline")
            for changed in (raw.replace(b"got Ok(", b"got Err("),
                            raw.replace(b"got Ok(", b"")):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.admit_fixture(name, raw=changed)

    def test_import_red_rejects_wrong_source_track_caller_or_limit(self):
        for name, failure in self.pair["failures"].items():
            raw, _, _ = self.case_fixture(name, "baseline")
            changed_outputs = [
                raw.replace(failure["source"].encode(), b"setup.rs"),
                raw.replace((":" + str(failure["line"]) + ":9:").encode(), b":1:9:"),
                raw.replace(failure["markers"][0].encode(), b"expected a different limit"),
            ]
            for changed in changed_outputs:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.admit_fixture(name, raw=changed)

    def test_import_red_rejects_compile_exit_pass_and_ignored_case(self):
        name = next(iter(self.pair["failures"]))
        raw, observed, _ = self.case_fixture(name, "baseline")
        for code in (0, 1, 2, 124, -9, None):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.admit_fixture(name, observed=dict(observed, exit_code=code))
        with self.assertRaises(ValueError):
            self.admit_fixture(name, raw=raw.replace(b" ... FAILED", b" ... ignored"))

    def test_six_baseline_controls_and_twenty_seven_repairs_require_pass(self):
        counts = []
        for role in gate.ROLES:
            names = [n for cases in self.pair[role]["targets"].values() for n in cases
                     if role == "repair" or n not in self.pair["failures"]]
            counts.append(len(names))
            for name in names:
                self.assertEqual(self.admit_fixture(name, role)["status"], "PASS")
                _, observed, _ = self.case_fixture(name, role)
                with self.assertRaises(ValueError):
                    self.admit_fixture(name, role, observed=dict(observed, exit_code=101))
        self.assertEqual(counts, [6, 27])

    def test_complete_import_roles_reject_missing_extra_changed_or_unclippied_result(self):
        for role in gate.ROLES:
            names = [n for cases in self.pair[role]["targets"].values() for n in cases]
            result = {"results": [
                {"name": n, "status": "EXPECTED_CAUSAL_RED"
                 if role == "baseline" and n in self.pair["failures"] else "PASS"}
                for n in names],
                "source_before": {"sha": self.pair[role]["sha"]},
                "source_after": {"sha": self.pair[role]["sha"]},
                "clippy": "PASS" if role == "repair" else "NOT_SELECTED"}
            self.assertEqual(gate.role_complete(self.pair, role, result)["cases"], len(names))
            for mutation in ("missing", "extra", "changed", "clippy"):
                changed = copy.deepcopy(result)
                if mutation == "missing":
                    changed["results"].pop()
                elif mutation == "extra":
                    changed["results"].append(changed["results"][0])
                elif mutation == "changed":
                    changed["source_after"]["sha"] = "0" * 40
                else:
                    changed["clippy"] = "NOT_RUN"
                with self.subTest(role=role, mutation=mutation), self.assertRaises(ValueError):
                    gate.role_complete(self.pair, role, changed)

    def synthetic_scope(self, unknown=False):
        source = self.root / "gate" / "tooling" / "ci_foundation_causal.py"
        source.parent.mkdir(parents=True)
        source.write_text("# synthetic gate path only\n", encoding="utf-8")
        tool = self.root / "synthetic-tool"
        tool.write_bytes(b"synthetic tool; not executed")
        fingerprint = {"absolute_path": str(tool), **gate.custody.fingerprint(tool, self.root)}
        seen = []

        def role(pair, selected_role, *unused):
            seen.append((pair["id"], selected_role))
            return {"status": "UNKNOWN" if unknown else "PASS_EXACT_CAUSAL_ROLE",
                    "results": [] if unknown else [
                        {"name": name, "status": "EXPECTED_CAUSAL_RED"
                         if selected_role == "baseline" and name in pair["failures"] else "PASS"}
                        for names in pair[selected_role]["targets"].values() for name in names]}

        output = self.root / "output"
        with patch.object(gate, "__file__", str(source)), \
                patch.dict(gate.os.environ, {"GITHUB_WORKSPACE": str(self.root)}, clear=True), \
                patch.object(gate, "execution_context", return_value={"GITHUB_SHA": "1" * 40}), \
                patch.object(gate, "build_environment", return_value={}), \
                patch.object(gate.custody, "source_snapshot",
                             return_value={"sha": "1" * 40, "files": {}}) as snapshot, \
                patch.object(gate, "retain_source", return_value={}), \
                patch.object(gate, "tools_identity", return_value={"rustc": fingerprint}), \
                patch.object(gate, "run_role", side_effect=role), \
                patch.object(gate, "verify_originals",
                             return_value={"status": "SYNTHETIC_TERMINAL_CHECK"}) as terminal, \
                patch("builtins.print"):
            code = gate.run(self.root, output, "foundation-import-budget")
        return code, json.loads((output / "receipt.json").read_text(encoding="utf-8")), \
            seen, snapshot.call_count, terminal.call_count

    def test_import_terminal_receipt_counts_only_48_and_omits_historical_pairs(self):
        code, receipt, seen, snapshots, terminals = self.synthetic_scope()
        self.assertEqual(code, 0)
        self.assertEqual(seen, [("import-resource-budget", "baseline"),
                                ("import-resource-budget", "repair")])
        self.assertEqual(receipt["status"], "PASS_IMPORT_BUDGET_CAUSAL_PAIR")
        self.assertEqual(receipt["expected_invocations"], 48)
        self.assertEqual(receipt["actual_case_results"], 48)
        self.assertEqual(receipt["selected_pairs"], ["import-resource-budget"])
        self.assertEqual(receipt["not_selected_pairs"],
                         ["jobs", "svg", "retained-import", "bounded-receipt"])
        self.assertEqual(receipt["not_selected_credit"], "NONE")
        self.assertEqual(receipt["not_run_pairs"], [])
        self.assertFalse(receipt["formal_acceptance"])
        self.assertFalse(receipt["operations_retried"])
        self.assertEqual((snapshots, terminals), (2, 1))

    def test_unknown_import_stops_before_repair_and_never_seals_or_replays(self):
        code, receipt, seen, snapshots, terminals = self.synthetic_scope(unknown=True)
        self.assertEqual(code, 1)
        self.assertEqual(seen, [("import-resource-budget", "baseline")])
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(receipt["actual_case_results"], 0)
        self.assertEqual(receipt["terminal_custody"], "UNSEALED_DESCENDANTS_NOT_OBSERVED")
        self.assertFalse(receipt["operations_retried"])
        self.assertEqual((snapshots, terminals), (1, 0))

    def test_both_choices_share_one_guarded_workflow_observer(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(
            encoding="utf-8")
        self.assertIn("options: [full, foundation-causal, foundation-import-budget, foundation-current, foundation-projection-diagnostic, foundation-composed-current]", text)
        self.assertIn("default: full", text)
        self.assertEqual(text.count(
            "github.event_name == 'workflow_dispatch' && "
            "(inputs.verification_scope == 'foundation-causal' || "
            "inputs.verification_scope == 'foundation-import-budget' || "
            "inputs.verification_scope == 'foundation-current' || inputs.verification_scope == 'foundation-projection-diagnostic' || inputs.verification_scope == 'foundation-composed-current')"), 2)
        self.assertIn(
            "FOUNDATION_CAUSAL_SELECTION: $" + "{{ inputs.verification_scope }}", text)
        self.assertIn('--selection "$FOUNDATION_CAUSAL_SELECTION"', text)
        self.assertEqual(text.count("gate/tooling/ci_foundation_causal.py"), 1)
        self.assertEqual(text.count("needs: foundation-causal-g0"), 1)
        self.assertNotIn("\n  foundation-import-budget:", text)



class CurrentCompositionCausalTests(unittest.TestCase):
    raw_case = FoundationCausalTests.raw_case
    context_environment = FoundationCausalTests.context_environment

    def setUp(self):
        FoundationCausalTests.setUp(self)
        self.pair = copy.deepcopy(gate.CURRENT_COMPOSITION_PAIR)
        # These identities exercise validation only; no source or binary is run.
        for index, role in enumerate(gate.ROLES, 1):
            self.pair[role]["sha"] = str(index) * 40
            self.pair[role]["tree"] = str(index + 2) * 40
            self.pair[role]["fixture_sha256"] = {
                name: "a" * 64 for name in gate.CURRENT_REQUIRED_FIXTURES[role]}
        for index, failure in enumerate(self.pair["failures"].values(), 100):
            failure["line"] = index

    def case_names(self, role):
        return [name for names in self.pair[role]["targets"].values() for name in names]

    def admit_current(self, name, role, raw=None, row=None):
        selected = next(names for names in self.pair[role]["targets"].values()
                        if name in names)
        roster = {"all": (*selected, "unselected_control"),
                  "selected": tuple(selected)}
        failure = self.pair["failures"].get(name) if role == "baseline" else None
        command = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED",
                   "log_custody": "VERIFIED_SNAPSHOT",
                   "argv": gate.case_argv(self.binary, self.pair, role, name),
                   "context": self.context, "exit_code": 101 if failure else 0}
        return gate.admit_case(
            self.pair, role, name,
            self.raw_case(name, failure, len(roster["all"])) if raw is None else raw,
            command if row is None else row, self.binary, dict(self.binary),
            roster, self.context)

    def phase_result(self):
        rows = [{"name": name, "status": "PASS"} for name in self.case_names("repair")]
        for index, name in enumerate(gate.CURRENT_PHASE_CASES):
            row = next(row for row in rows if row["name"] == name)
            checkpoint = gate.custody.CHECKPOINTS[name]
            row["phase_custody"] = {
                "status": "COMPLETE_OBSERVED_PHASE_CUSTODY", "test_name": name,
                "checkpoint": checkpoint, "request_id": "synthetic-request-" + str(index),
                "phases": list(gate.custody.PHASES),
                "processes": [{"mode": "writer"}, {"mode": "observer"}, {"mode": "observer"}],
                "custody_manifest": {"path": checkpoint + "/custody.json",
                                     "bytes": 1, "sha256": "a" * 64},
                "files": {str(number): {
                    "path": checkpoint + "/original-" + str(number),
                    "bytes": 1, "sha256": "b" * 64} for number in range(15)},
            }
        ddl = next(row for row in rows if row["name"] == gate.CURRENT_DDL_CASE)
        ddl.update(retained_child_originals=0, retained_phase_jsons=0)
        result = {"pair": "foundation-current", "role": "repair", "results": rows,
                  "source_before": {"synthetic": True}, "source_after": {"synthetic": True},
                  "clippy": "PASS", "phase_root": "foundation-current/repair/phases"}
        result["phase_scope"] = gate.current_phase_scope(result)
        return result

    def test_current_selector_is_separate_from_five_historical_pairs(self):
        with patch.object(gate, "CURRENT_COMPOSITION_PAIR", self.pair):
            selected = gate.selected_pairs("foundation-current")
        self.assertEqual([pair["id"] for pair in selected], ["foundation-current"])
        self.assertEqual([pair["id"] for pair in gate.selected_pairs("foundation-causal")],
                         ["jobs", "svg", "retained-import", "bounded-receipt"])
        self.assertEqual([pair["id"] for pair in gate.selected_pairs("foundation-import-budget")],
                         ["import-resource-budget"])

    def test_pending_source_refuses_before_context_or_any_command(self):
        self.pair["baseline"]["sha"] = None
        with (patch.object(gate, "CURRENT_COMPOSITION_PAIR", self.pair),
              patch.object(gate, "execution_context") as context,
              patch.object(gate, "command") as command):
            with self.assertRaisesRegex(ValueError, "pins are pending"):
                gate.run(self.root, self.root / "not-created", "foundation-current")
        context.assert_not_called()
        command.assert_not_called()
        self.assertFalse((self.root / "not-created").exists())

    def test_all_source_trees_fixture_hashes_and_sites_must_be_resolved(self):
        gate.validate_current_composition(self.pair)
        for role in gate.ROLES:
            for key in ("sha", "tree"):
                pair = copy.deepcopy(self.pair)
                pair[role][key] = None
                with self.subTest(role=role, key=key), self.assertRaises(ValueError):
                    gate.validate_current_composition(pair)
            for path in gate.CURRENT_REQUIRED_FIXTURES[role]:
                pair = copy.deepcopy(self.pair)
                pair[role]["fixture_sha256"][path] = None
                with self.subTest(role=role, path=path), self.assertRaises(ValueError):
                    gate.validate_current_composition(pair)
        for name in self.pair["failures"]:
            pair = copy.deepcopy(self.pair)
            pair["failures"][name]["line"] = None
            with self.subTest(name=name), self.assertRaises(ValueError):
                gate.validate_current_composition(pair)

    def test_current_roster_is_130_distinct_contracts_in_twelve_targets(self):
        source = self.pair["repair"]["targets"]
        identities = [(gate.TARGETS[key]["package"], gate.TARGETS[key]["kind"],
                       gate.TARGETS[key]["name"], name)
                      for key, names in source.items() for name in names]
        self.assertEqual((len(source), len(identities), len(set(identities))), (12, 130, 130))
        self.assertEqual(len(self.case_names("baseline")), 5)
        self.assertEqual(gate.current_inventory_digest(source),
                         gate.CURRENT_INVENTORY_DIGESTS["repair"])

    def test_receipt_integrity_overlap_and_two_unit_binaries_are_not_duplicated(self):
        targets = self.pair["repair"]["targets"]
        self.assertEqual(set(targets["receipt"]), set(gate.PAIRS[3]["repair"]["targets"]["receipt"]))
        self.assertEqual(len(targets["receipt"]), 12)
        units = [(key, gate.TARGETS[key]["package"]) for key in targets
                 if gate.TARGETS[key]["kind"] == "lib"]
        self.assertEqual(set(units), {("current-storage-unit", "motionwright-storage"),
                                      ("current-service-unit", "motionwright-service")})
        self.assertEqual((len(targets["current-storage-unit"]),
                          len(targets["current-service-unit"])), (32, 11))

    def test_missing_duplicate_or_moved_contract_never_matches_current_digest(self):
        for mutation in ("remove", "duplicate", "move"):
            pair = copy.deepcopy(self.pair)
            cases = pair["repair"]["targets"]["receipt"]
            if mutation == "remove":
                cases.pop()
            elif mutation == "duplicate":
                cases[-1] = cases[0]
            else:
                other = pair["repair"]["targets"]["jobs"]
                cases[0], other[0] = other[0], cases[0]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_current_composition(pair)

    def test_current_causal_markers_and_distinct_variants_are_closed(self):
        for mutation in ("same-source", "wrong-message", "wrong-source", "extra-failure"):
            pair = copy.deepcopy(self.pair)
            name = next(iter(pair["failures"]))
            if mutation == "same-source":
                pair["repair"]["sha"] = pair["baseline"]["sha"]
            elif mutation == "wrong-message":
                pair["failures"][name]["markers"] = ["any error"]
            elif mutation == "wrong-source":
                pair["failures"][name]["source"] = "crates/service/src/lib.rs"
            else:
                pair["failures"][self.case_names("baseline")[-1]] = copy.deepcopy(pair["failures"][name])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_current_composition(pair)

    def test_compile_clippy_and_deadlines_keep_the_existing_locked_contract(self):
        for target in self.pair["repair"]["targets"]:
            spec = gate.TARGETS[target]
            compile_command = gate.compile_argv("cargo", spec)
            clippy_command = gate.clippy_argv("cargo", spec)
            self.assertIn("--locked", compile_command)
            self.assertIn("--no-run", compile_command)
            self.assertIn("--message-format=json", compile_command)
            self.assertIn("--locked", clippy_command)
            self.assertEqual(clippy_command[-3:], ["--", "-D", "warnings"])
        self.assertEqual((gate.CASE_TIMEOUT, gate.BUILD_TIMEOUT), (120, 1500))
        self.assertEqual(gate.TEST_OPTIONS, ("--test-threads=1", "--format", "pretty"))

    def test_unit_namespaces_select_only_two_or_thirty_two_storage_cases(self):
        spec = gate.TARGETS["current-storage-unit"]
        for role, count in (("baseline", 2), ("repair", 32)):
            names = self.pair[role]["targets"]["current-storage-unit"]
            raw = "".join(name + ": test\n" for name in [*names, "unrelated::control"]).encode()
            roster = gate.selected_roster(raw, 0, spec, names, role)
            self.assertEqual(len(roster["selected"]), count)
            self.assertEqual(roster["not_selected"], ("unrelated::control",))
            extra = raw + b"verified_blob_capture_budget_tests::unreviewed: test\n"
            with self.assertRaises(ValueError):
                gate.selected_roster(extra, 0, spec, names, role)

    def test_service_unit_selection_retains_unselected_names_without_credit(self):
        spec = gate.TARGETS["current-service-unit"]
        for role, count in (("baseline", 3), ("repair", 11)):
            names = self.pair[role]["targets"]["current-service-unit"]
            raw = ("\n".join(name + ": test" for name in [*names, "audio_import_tests::unselected"])
                   + "\n").encode()
            roster = gate.selected_roster(raw, 0, spec, names, role)
            self.assertEqual(len(roster["selected"]), count)
            self.assertEqual(roster["not_selected_credit"], "NONE")
            self.assertEqual(roster["not_selected"], ("audio_import_tests::unselected",))

    def test_three_originalmix_reds_and_two_controls_require_named_results(self):
        for name in self.case_names("baseline"):
            row = self.admit_current(name, "baseline")
            self.assertEqual(row["status"],
                             "EXPECTED_CAUSAL_RED" if name in self.pair["failures"] else "PASS")
        for name in self.case_names("repair"):
            self.assertEqual(self.admit_current(name, "repair")["status"], "PASS")

    def test_wrong_causal_site_message_or_success_is_not_red(self):
        for name, failure in self.pair["failures"].items():
            target_names = next(names for names in self.pair["baseline"]["targets"].values()
                                if name in names)
            total = len(target_names) + 1
            for raw in (
                self.raw_case(name, {**failure, "line": failure["line"] + 1}, total),
                self.raw_case(name, {**failure, "markers": ["unrelated error"]}, total),
                self.raw_case(name, None, total),
            ):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.admit_current(name, "baseline", raw=raw)

    def test_current_fixture_digest_is_checked_beyond_source_sha(self):
        source = self.pair["repair"]
        files = {name: {"sha256": value} for name, value in source["fixture_sha256"].items()}
        observed = {"files": files}
        with (patch.object(gate, "input_paths", return_value=tuple(files)),
              patch.object(gate.custody, "source_snapshot", return_value=observed)):
            self.assertEqual(gate.product_snapshot(self.root, source), observed)
            files[next(iter(files))]["sha256"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "frozen fixture changed"):
                gate.product_snapshot(self.root, source)

    def test_current_dependency_is_exact_and_legacy_gate_inventory_is_preserved(self):
        root = Path(gate.__file__).resolve().parents[1]
        snapshot = {"files": {name: {"sha256": digest}
                              for name, digest in gate.CURRENT_DEPENDENCY_HASHES.items()}}
        result = gate.current_dependency_preflight(root, snapshot)
        self.assertEqual(result["cohort"], "binding4")
        self.assertEqual(result["historical_default"], "historical5fa")
        self.assertFalse(result["independent_observer_pid_or_snapshot_digest_binding"])
        self.assertEqual(gate.selected_gate_files("foundation-causal"), gate.GATE_FILES)
        self.assertEqual(gate.selected_gate_files("foundation-import-budget"), gate.GATE_FILES)
        self.assertTrue(set(gate.CURRENT_DEPENDENCY_HASHES)
                        <= set(gate.selected_gate_files("foundation-current")))
        for name in gate.CURRENT_DEPENDENCY_HASHES:
            changed = copy.deepcopy(snapshot)
            changed["files"][name]["sha256"] = "0" * 64
            with self.subTest(path=name), self.assertRaises(ValueError):
                gate.current_dependency_preflight(root, changed)

    def test_current_dependency_rejects_another_loaded_module(self):
        foreign = self.root / "foreign.py"
        foreign.write_text("# synthetic fixture", encoding="utf-8")
        snapshot = {"files": {name: {"sha256": digest}
                              for name, digest in gate.CURRENT_DEPENDENCY_HASHES.items()}}
        with patch.object(gate.custody, "__file__", str(foreign)), self.assertRaises(ValueError):
            gate.current_dependency_preflight(Path(gate.__file__).resolve().parents[1], snapshot)

    def test_existing_environment_rejects_parent_child_and_evidence_overrides(self):
        for name in ("MOTIONWRIGHT_TEST_APPLY_CHILD", "MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT",
                     "MOTIONWRIGHT_TEST_BINDING_MIGRATION_CHILD"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                gate.build_environment({name: "foreign"}, self.root / "target", self.root / "tmp")

    def test_phase_environment_is_owned_and_applies_only_to_four_current_parents(self):
        output = self.root / "output"
        phase_root = output / "phases"
        phase_root.mkdir(parents=True)
        for name in gate.CURRENT_PHASE_CASES:
            env = gate.current_phase_environment(self.pair, "repair", name, {}, output, phase_root)
            self.assertEqual(env, {gate.custody.EVIDENCE_ROOT: str(phase_root)})
        self.assertEqual(gate.current_phase_environment(
            self.pair, "repair", gate.CURRENT_DDL_CASE, {}, output, phase_root), {})
        name = gate.CURRENT_PHASE_CASES[0]
        (phase_root / gate.custody.CHECKPOINTS[name]).mkdir()
        with self.assertRaisesRegex(ValueError, "no reuse"):
            gate.current_phase_environment(self.pair, "repair", name, {}, output, phase_root)
        with self.assertRaises(ValueError):
            gate.current_phase_environment(self.pair, "repair", name, {}, output, self.root)

    def test_current_phase_scope_counts_twenty_snapshots_twelve_children_and_ddl_parent(self):
        result = self.phase_result()
        scope = gate.current_phase_scope(result)
        self.assertEqual((scope["phase_cases"], scope["phase_jsons_retained"],
                          scope["child_process_originals_retained"]), (4, 20, 12))
        self.assertEqual((scope["ddl_parent_cases"], scope["ddl_retained_child_originals"],
                          scope["ddl_retained_phase_jsons"]), (1, 0, 0))
        self.assertFalse(scope["raw_database_or_full_cas_retained"])
        self.assertFalse(scope["independent_observer_pid_or_snapshot_digest_binding"])
        self.assertEqual(gate.role_complete(self.pair, "repair", result)["cases"], 130)

    def test_parent_pass_without_current_phases_or_children_is_incomplete(self):
        for mutation in ("missing", "child", "snapshot", "duplicate-request"):
            result = self.phase_result()
            phases = [row for row in result["results"] if row["name"] in gate.CURRENT_PHASE_CASES]
            if mutation == "missing":
                phases[0].pop("phase_custody")
            elif mutation == "child":
                phases[0]["phase_custody"]["processes"].pop()
            elif mutation == "snapshot":
                phases[0]["phase_custody"]["phases"].pop()
            else:
                phases[1]["phase_custody"]["request_id"] = phases[0]["phase_custody"]["request_id"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.current_phase_scope(result)

    def test_ddl_and_other_cases_cannot_inherit_phase_or_child_credit(self):
        for mutation in ("ddl-children", "ddl-phases", "foreign-phase"):
            result = self.phase_result()
            ddl = next(row for row in result["results"] if row["name"] == gate.CURRENT_DDL_CASE)
            if mutation == "ddl-children":
                ddl["retained_child_originals"] = 1
            elif mutation == "ddl-phases":
                ddl["retained_phase_jsons"] = 1
            else:
                ddl["phase_custody"] = next(
                    row["phase_custody"] for row in result["results"] if "phase_custody" in row)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.current_phase_scope(result)

    def test_terminal_phase_readback_reuses_canonical_binding4_admission(self):
        result = self.phase_result()
        output = self.root / "output"
        root = output / result["phase_root"]
        for checkpoint in gate.custody.CHECKPOINTS.values():
            (root / checkpoint).mkdir(parents=True)
        phases = {row["name"]: row["phase_custody"] for row in result["results"]
                  if row["name"] in gate.CURRENT_PHASE_CASES}
        def admit(container, name, cohort):
            self.assertEqual((container, cohort), (root, "binding4"))
            return phases[name]
        with patch.object(gate.custody, "admit_phase_custody", side_effect=admit) as invoked:
            originals = gate.verify_current_phase_originals(output, result)
        self.assertEqual((invoked.call_count, len(originals)), (4, 64))
        with (patch.object(gate.custody, "admit_phase_custody", return_value={}),
              self.assertRaisesRegex(ValueError, "originals changed")):
            gate.verify_current_phase_originals(output, result)
        (root / "unlisted").mkdir()
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            gate.verify_current_phase_originals(output, result)

    def test_unknown_current_baseline_stops_repair_without_replay(self):
        calls = []
        def execute(pair, role):
            calls.append((pair["id"], role))
            return {"status": "UNKNOWN"}
        rows = gate.run_pairs(execute, [self.pair])
        self.assertEqual(calls, [("foundation-current", "baseline")])
        self.assertEqual(rows[0]["status"], "UNKNOWN")
        self.assertEqual(len(rows[0]["roles"]), 1)

    def test_current_scope_has_one_manual_g0_guarded_workflow_and_no_new_executor(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        self.assertIn("options: [full, foundation-causal, foundation-import-budget, foundation-current, foundation-projection-diagnostic, foundation-composed-current]", text)
        self.assertEqual(text.count("gate/tooling/ci_foundation_causal.py"), 1)
        self.assertEqual(text.count("needs: foundation-causal-g0"), 1)
        self.assertEqual(text.count("|| inputs.verification_scope == 'foundation-current' || inputs.verification_scope == 'foundation-projection-diagnostic' || inputs.verification_scope == 'foundation-composed-current')"), 2)
        self.assertNotIn("\n  foundation-current:", text)
        self.assertIn("default: full", text)



class ProjectionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="projection-diagnostic-g0-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = copy.deepcopy(gate.PROJECTION_DIAGNOSTIC_SOURCE)
        self.pair = copy.deepcopy(gate.PROJECTION_DIAGNOSTIC)
        self.pair["repair"] = self.source
        self.name = "all_large_project_pages_reopen_without_selected_imported_blobs"

    def test_frozen_diagnostic_selects_only_the_complete_case_and_original_deadline(self):
        selected = gate.selected_pairs("foundation-projection-diagnostic")
        self.assertEqual(len(selected), 1)
        self.assertEqual(set(selected[0]), {"id", "failures", "repair"})
        self.assertEqual(selected[0]["failures"], {})
        self.assertEqual(selected[0]["repair"]["targets"],
                         {"project-media-independence": [self.name]})
        self.assertEqual(gate.CASE_TIMEOUT, 120)
        self.assertEqual(gate.PASS_STATUSES["foundation-projection-diagnostic"],
                         "PASS_EXACT_PROJECTION_DIAGNOSTIC")
        self.assertRegex(selected[0]["repair"]["sha"], r"^[0-9a-f]{40}$")
        self.assertRegex(selected[0]["repair"]["tree"], r"^[0-9a-f]{40}$")

    def test_unfrozen_source_refuses_before_context_commands_or_output(self):
        for key in ("sha", "tree"):
            source = copy.deepcopy(self.source)
            source[key] = None
            pair = dict(self.pair, repair=source)
            destination = self.root / ("unstarted-" + key)
            with (patch.object(gate, "PROJECTION_DIAGNOSTIC_SOURCE", source),
                  patch.object(gate, "PROJECTION_DIAGNOSTIC", pair),
                  patch.object(gate, "execution_context") as context,
                  patch.object(gate, "command") as command,
                  self.assertRaisesRegex(ValueError, "not frozen")):
                gate.run(self.root, destination, "foundation-projection-diagnostic")
            context.assert_not_called()
            command.assert_not_called()
            self.assertFalse(destination.exists())

    def test_changed_case_digest_role_or_expected_failure_is_rejected(self):
        for mutation in ("missing", "duplicate", "foreign", "digest", "baseline",
                         "causal_failure", "detached_source"):
            source = copy.deepcopy(self.source)
            pair = dict(self.pair, repair=source, failures={})
            if mutation == "missing":
                source["targets"]["project-media-independence"] = []
            elif mutation == "duplicate":
                source["targets"]["project-media-independence"].append(self.name)
            elif mutation == "foreign":
                source["targets"]["project-media-independence"] = ["smaller_fixture"]
            elif mutation == "digest":
                source["fixture_sha256"] = dict.fromkeys(source["fixture_sha256"], "0" * 64)
            elif mutation == "baseline":
                pair["baseline"] = source
            elif mutation == "causal_failure":
                pair["failures"] = {self.name: {"line": 1}}
            else:
                pair["repair"] = dict(source, sha="1" * 40)
            with (self.subTest(mutation=mutation),
                  patch.object(gate, "PROJECTION_DIAGNOSTIC_SOURCE", source),
                  patch.object(gate, "PROJECTION_DIAGNOSTIC", pair),
                  self.assertRaises(ValueError)):
                gate.selected_pairs("foundation-projection-diagnostic")

    def test_only_one_repair_callback_runs_and_causal_pass_cannot_be_reused(self):
        for status, expected in (
                ("PASS_EXACT_DIAGNOSTIC_ROLE", "PASS_EXACT_DIAGNOSTIC_EXECUTION"),
                ("PASS_EXACT_CAUSAL_ROLE", "FAIL"), ("FAIL", "FAIL"),
                ("UNKNOWN", "UNKNOWN")):
            observed = {"status": status, "results": []}
            callback = Mock(return_value=observed)
            rows = gate.run_projection_diagnostic(callback, [self.pair])
            callback.assert_called_once_with(self.pair, "repair")
            self.assertEqual(rows[0]["status"], expected)
            self.assertEqual(rows[0]["roles"], [observed])
        with self.assertRaises(ValueError):
            gate.run_projection_diagnostic(Mock(), [self.pair, self.pair])

    def test_complete_diagnostic_keeps_custody_and_clippy_requirements(self):
        result = {"results": [{"name": self.name, "status": "PASS"}],
                  "source_before": {"sha": self.source["sha"], "files": {"x": "hash"}},
                  "source_after": {"sha": self.source["sha"], "files": {"x": "hash"}},
                  "clippy": "PASS"}
        admitted = gate.role_complete(self.pair, "repair", result)
        self.assertEqual(admitted["status"], "PASS_EXACT_DIAGNOSTIC_ROLE")
        self.assertEqual(admitted["cases"], 1)
        self.assertEqual(admitted["expected_assertion_failures"], 0)
        for mutation in ("missing", "duplicate", "failed", "source", "clippy"):
            changed = copy.deepcopy(result)
            if mutation == "missing":
                changed["results"] = []
            elif mutation == "duplicate":
                changed["results"] *= 2
            elif mutation == "failed":
                changed["results"][0]["status"] = "FAIL"
            elif mutation == "source":
                changed["source_after"]["files"]["x"] = "replaced"
            else:
                changed["clippy"] = "NOT_RUN"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.role_complete(self.pair, "repair", changed)

    def synthetic_scope(self, role_status):
        source = self.root / "gate" / "tooling" / "ci_foundation_causal.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic observer")
        tool = self.root / "synthetic-rustc"
        tool.write_bytes(b"tool")
        fingerprint = {"absolute_path": str(tool),
                       **gate.custody.fingerprint(tool, self.root)}
        seen = []

        def observe(pair, role, *unused):
            seen.append((pair["id"], role))
            return {"status": role_status, "results": [] if role_status == "UNKNOWN"
                    else [{"name": self.name,
                           "status": "PASS" if role_status == "PASS_EXACT_DIAGNOSTIC_ROLE"
                           else "FAIL"}]}

        output = self.root / "output"
        with (patch.object(gate, "__file__", str(source)),
              patch.dict(gate.os.environ, {"GITHUB_WORKSPACE": str(self.root)}, clear=True),
              patch.object(gate, "execution_context", return_value={"GITHUB_SHA": "1" * 40}),
              patch.object(gate, "build_environment", return_value={}),
              patch.object(gate.custody, "source_snapshot",
                           return_value={"sha": "1" * 40, "files": {}}) as snapshots,
              patch.object(gate, "retain_source", return_value={}),
              patch.object(gate, "tools_identity", return_value={"rustc": fingerprint}),
              patch.object(gate, "run_role", side_effect=observe),
              patch.object(gate, "verify_originals",
                           return_value={"status": "SYNTHETIC_TERMINAL_CHECK"}) as terminal,
              patch("builtins.print")):
            code = gate.run(self.root, output, "foundation-projection-diagnostic")
        return (code, json.loads((output / "receipt.json").read_text(encoding="utf-8")),
                seen, snapshots.call_count, terminal.call_count)

    def test_terminal_success_counts_one_diagnostic_without_acceptance_or_causal_credit(self):
        code, receipt, seen, snapshots, terminal = self.synthetic_scope(
            "PASS_EXACT_DIAGNOSTIC_ROLE")
        self.assertEqual(code, 0)
        self.assertEqual(receipt["status"], "PASS_EXACT_PROJECTION_DIAGNOSTIC")
        self.assertEqual(receipt["expected_invocations"], 1)
        self.assertEqual(receipt["actual_case_results"], 1)
        self.assertEqual(receipt["mode"], "ONE_SOURCE_PHASE_DIAGNOSTIC")
        self.assertEqual(receipt["case_timeout_seconds"], 120)
        self.assertEqual(receipt["not_selected_credit"], "NONE")
        self.assertFalse(receipt["formal_acceptance"])
        self.assertFalse(receipt["operations_retried"])
        self.assertEqual(seen, [("foundation-projection-diagnostic", "repair")])
        self.assertEqual((snapshots, terminal), (2, 1))

    def test_unknown_preserves_uncertainty_without_replay_or_terminal_seal(self):
        code, receipt, seen, snapshots, terminal = self.synthetic_scope("UNKNOWN")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(receipt["actual_case_results"], 0)
        self.assertEqual(receipt["expected_invocations"], 1)
        self.assertEqual(receipt["terminal_custody"], "UNSEALED_DESCENDANTS_NOT_OBSERVED")
        self.assertFalse(receipt["operations_retried"])
        self.assertEqual(seen, [("foundation-projection-diagnostic", "repair")])
        self.assertEqual((snapshots, terminal), (1, 0))

    def test_observed_failure_never_becomes_diagnostic_pass(self):
        code, receipt, seen, snapshots, terminal = self.synthetic_scope("FAIL")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["actual_case_results"], 1)
        self.assertEqual(seen, [("foundation-projection-diagnostic", "repair")])
        self.assertEqual((snapshots, terminal), (2, 1))



class ComposedCurrentCausalTests(unittest.TestCase):
    context_environment = FoundationCausalTests.context_environment
    case_names = CurrentCompositionCausalTests.case_names
    phase_result = CurrentCompositionCausalTests.phase_result
    original_fixture = FoundationCausalTests.original_fixture

    def setUp(self):
        FoundationCausalTests.setUp(self)
        self.pair = copy.deepcopy(gate.COMPOSED_COMPOSITION_PAIR)

    def test_composed_source_is_exact_and_historical_catalogues_are_unchanged(self):
        self.assertEqual(gate.selected_pairs("foundation-composed-current"), [self.pair])
        self.assertEqual(self.pair["repair"]["sha"], "be7d0377425f9eec201852450b5a7c53e4a1f403")
        self.assertEqual(self.pair["repair"]["tree"], "5f9e92f6640a39c7adddb249501399cc97e958f8")
        self.assertEqual(self.pair["baseline"], gate.CURRENT_COMPOSITION_PAIR["baseline"])
        self.assertEqual(self.pair["repair"]["fixture_sha256"][
            "crates/service/tests/project_projection_media_independence.rs"],
            "c1d9b2ca22a7770390bff776b6bf6aca8f2c154341fe039fb51882569a496b50")
        historical = {name: getattr(gate, name) for name in (
            "PAIRS", "IMPORT_BUDGET_PAIR", "CURRENT_COMPOSITION_PAIR",
            "PROJECTION_DIAGNOSTIC_SOURCE")}
        digest = hashlib.sha256(json.dumps(
            historical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "78a45e48baf2d388c3a854e4ff4653947aaf0a1e6c7815ef12eb2908e8f99348")
        self.assertEqual(gate.selected_pairs("foundation-current")[0]["repair"]["sha"],
                         "307c7e762cb91e6383922dc2d6cdeeef8d3d4ed7")
        self.assertEqual(gate.selected_pairs("foundation-projection-diagnostic")[0]["repair"]["sha"],
                         "fb5dd1840c7f6b97600430311a33a051744f826b")

    def test_composed_pins_cannot_be_missing_replaced_or_inherited_from_history(self):
        for role in gate.ROLES:
            for key in ("sha", "tree"):
                for value in (None, "f" * 40):
                    pair = copy.deepcopy(self.pair)
                    pair[role][key] = value
                    with self.subTest(role=role, key=key, value=value), self.assertRaises(ValueError):
                        gate.validate_composed_current(pair)
        for name in self.pair["repair"]["fixture_sha256"]:
            pair = copy.deepcopy(self.pair)
            pair["repair"]["fixture_sha256"][name] = "f" * 64
            with self.subTest(path=name), self.assertRaises(ValueError):
                gate.validate_composed_current(pair)
        pair = copy.deepcopy(self.pair)
        pair["failures"][next(iter(pair["failures"]))]["line"] += 1
        with self.assertRaises(ValueError):
            gate.validate_composed_current(pair)

    def test_pending_composed_pin_refuses_before_context_commands_or_output(self):
        pair = copy.deepcopy(self.pair)
        pair["repair"]["sha"] = None
        with (patch.object(gate, "COMPOSED_COMPOSITION_PAIR", pair),
              patch.object(gate, "execution_context") as context,
              patch.object(gate, "command") as command):
            with self.assertRaises(ValueError):
                gate.run(self.root, self.root / "not-created", "foundation-composed-current")
        context.assert_not_called()
        command.assert_not_called()
        self.assertFalse((self.root / "not-created").exists())

    def test_composed_roster_executes_each_contract_once_and_mix_precedes_large_case(self):
        targets = self.pair["repair"]["targets"]
        identities = [(gate.TARGETS[key]["package"], gate.TARGETS[key]["kind"],
                       gate.TARGETS[key]["name"], name)
                      for key, names in targets.items() for name in names]
        self.assertEqual((len(identities), len(set(identities))), (130, 130))
        self.assertEqual(len(self.case_names("baseline")), 5)
        self.assertEqual(len(self.pair["failures"]), 3)
        self.assertEqual(tuple(targets), gate.COMPOSED_TARGET_ORDER)
        self.assertEqual(tuple(targets)[-2:],
                         ("original-mix-source", "project-media-independence"))
        self.assertEqual(len(targets["original-mix-source"]), 8)
        self.assertEqual(gate.current_inventory_digest(targets),
                         gate.CURRENT_INVENTORY_DIGESTS["repair"])
        for mutation in ("missing", "duplicate", "moved", "order"):
            pair = copy.deepcopy(self.pair)
            changed = pair["repair"]["targets"]
            if mutation == "missing":
                changed["original-mix-source"].pop()
            elif mutation == "duplicate":
                changed["original-mix-source"].append(changed["original-mix-source"][0])
            elif mutation == "moved":
                changed["original-mix-source"][0], changed["jobs"][0] = (
                    changed["jobs"][0], changed["original-mix-source"][0])
            else:
                changed["original-mix-source"] = changed.pop("original-mix-source")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.validate_composed_current(pair)

    def test_composed_dispatch_requires_first_attempt_and_separate_gate_source(self):
        env = self.context_environment()
        env["FOUNDATION_CAUSAL_SELECTION"] = "foundation-composed-current"
        self.assertEqual(gate.execution_context(
            env, "linux", "foundation-composed-current")["GITHUB_RUN_ATTEMPT"], "1")
        for mutation in ("attempt", "gate-is-product", "wrong-selection"):
            changed = dict(env)
            if mutation == "attempt":
                changed["GITHUB_RUN_ATTEMPT"] = "2"
            elif mutation == "gate-is-product":
                changed["GITHUB_SHA"] = changed["GITHUB_WORKFLOW_SHA"] = (
                    self.pair["repair"]["sha"])
            else:
                changed["FOUNDATION_CAUSAL_SELECTION"] = "foundation-current"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.execution_context(changed, "linux", "foundation-composed-current")
        # The historical selector keeps its previously reviewed admission.
        historical = self.context_environment()
        historical["GITHUB_RUN_ATTEMPT"] = "2"
        gate.execution_context(historical, "linux", "foundation-causal")

    def test_composed_fixture_bytes_and_loaded_custody_keep_the_existing_admission(self):
        source = self.pair["repair"]
        observed = {"files": {name: {"sha256": digest}
                              for name, digest in source["fixture_sha256"].items()}}
        with (patch.object(gate, "input_paths", return_value=tuple(observed["files"])),
              patch.object(gate.custody, "source_snapshot", return_value=observed)):
            gate.product_snapshot(self.root, source)
            observed["files"][next(iter(observed["files"]))]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "frozen fixture changed"):
                gate.product_snapshot(self.root, source)
        self.assertEqual(gate.selected_gate_files("foundation-composed-current"),
                         gate.selected_gate_files("foundation-current"))
        snapshot = {"files": {name: {"sha256": digest}
                              for name, digest in gate.COMPOSED_DEPENDENCY_HASHES.items()}}
        root = Path(gate.__file__).resolve().parents[1]
        gate.current_dependency_preflight(root, snapshot, "foundation-composed-current")
        foreign = self.root / "foreign-custody.py"
        foreign.write_bytes(b"not the loaded custody implementation")
        with patch.object(gate.custody, "__file__", str(foreign)), self.assertRaises(ValueError):
            gate.current_dependency_preflight(root, snapshot, "foundation-composed-current")

    def test_composed_phase_environment_is_limited_to_the_four_repair_parents(self):
        output = self.root / "output"
        phase_root = output / "phases"
        phase_root.mkdir(parents=True)
        for name in gate.CURRENT_PHASE_CASES:
            self.assertEqual(gate.current_phase_environment(
                self.pair, "repair", name, {}, output, phase_root),
                {gate.custody.EVIDENCE_ROOT: str(phase_root)})
            self.assertEqual(gate.current_phase_environment(
                self.pair, "baseline", name, {}, output, phase_root), {})
        self.assertEqual(gate.current_phase_environment(
            self.pair, "repair", gate.CURRENT_DDL_CASE, {}, output, phase_root), {})
        name = gate.CURRENT_PHASE_CASES[0]
        (phase_root / gate.custody.CHECKPOINTS[name]).mkdir()
        with self.assertRaisesRegex(ValueError, "no reuse"):
            gate.current_phase_environment(self.pair, "repair", name, {}, output, phase_root)

    def test_composed_parent_pass_requires_current_phases_source_custody_and_clippy(self):
        result = self.phase_result()
        result["pair"] = "foundation-composed-current"
        self.assertEqual(gate.role_complete(self.pair, "repair", result)["cases"], 130)
        for mutation in ("phase", "clippy", "source", "missing-case"):
            changed = copy.deepcopy(result)
            if mutation == "phase":
                next(row for row in changed["results"] if "phase_custody" in row).pop("phase_custody")
            elif mutation == "clippy":
                changed["clippy"] = "NOT_RUN"
            elif mutation == "source":
                changed["source_after"] = {"changed": True}
            else:
                changed["results"].pop()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.role_complete(self.pair, "repair", changed)

    def test_composed_terminal_audit_invokes_the_canonical_phase_reader(self):
        output, observed, receipt = self.original_fixture()
        role = {"role": "repair", "retained_source": {}, "binaries": {}}
        receipt["pairs"] = [{"id": "foundation-composed-current", "roles": [role]}]
        with patch.object(gate, "verify_current_phase_originals", return_value={}) as phases:
            gate.verify_originals(output, observed, receipt)
        phases.assert_called_once_with(output, role)

    def test_composed_command_keeps_the_deadline_and_unknown_has_no_retry(self):
        self.assertEqual(gate.CASE_TIMEOUT, 120)
        row = {"observation": "UNKNOWN_TIMEOUT_NO_RETRY", "exit_code": None}
        with patch.object(gate.custody, "execute", return_value=row) as invoked:
            with self.assertRaises(gate.UnknownExecution):
                gate.command(["synthetic"], self.root, {}, self.root, "case", 120, {})
        invoked.assert_called_once()
        self.assertEqual(invoked.call_args.args[5], 120)

    def synthetic_scope(self, unknown_role=None, fail_repair=False):
        source = self.root / "gate" / "tooling" / "ci_foundation_causal.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic gate path; not executable product")
        tool = self.root / "synthetic-rustc"
        tool.write_bytes(b"synthetic tool")
        fingerprint = {"absolute_path": str(tool),
                       **gate.custody.fingerprint(tool, self.root)}
        seen = []

        def observe(pair, role, *unused):
            seen.append((pair["id"], role))
            status = ("UNKNOWN" if role == unknown_role else
                      "FAIL" if role == "repair" and fail_repair else "PASS_EXACT_CAUSAL_ROLE")
            rows = [] if status == "UNKNOWN" else [
                {"name": name, "status": "EXPECTED_CAUSAL_RED"
                 if role == "baseline" and name in pair["failures"] else "PASS"}
                for names in pair[role]["targets"].values() for name in names]
            if status == "FAIL":
                rows[0]["status"] = "FAIL"
            return {"status": status, "results": rows}

        output = self.root / "output"
        with (patch.object(gate, "__file__", str(source)),
              patch.dict(gate.os.environ, {"GITHUB_WORKSPACE": str(self.root)}, clear=True),
              patch.object(gate, "execution_context", return_value={"GITHUB_SHA": "1" * 40}),
              patch.object(gate, "build_environment", return_value={}),
              patch.object(gate.custody, "source_snapshot",
                           return_value={"sha": "1" * 40, "files": {}}) as snapshots,
              patch.object(gate, "current_dependency_preflight",
                           return_value={"status": "SYNTHETIC_DEPENDENCY_CHECK"}) as dependency,
              patch.object(gate, "retain_source", return_value={}),
              patch.object(gate, "tools_identity", return_value={"rustc": fingerprint}),
              patch.object(gate, "run_role", side_effect=observe),
              patch.object(gate, "verify_originals",
                           return_value={"status": "SYNTHETIC_TERMINAL_CHECK"}) as terminal,
              patch("builtins.print")):
            code = gate.run(self.root, output, "foundation-composed-current")
        return (code, json.loads((output / "receipt.json").read_text(encoding="utf-8")),
                seen, snapshots.call_count, terminal.call_count, dependency.call_count)

    def test_composed_terminal_counts_135_without_historical_or_acceptance_credit(self):
        code, receipt, seen, snapshots, terminal, dependency = self.synthetic_scope()
        self.assertEqual(code, 0)
        self.assertEqual(seen, [("foundation-composed-current", "baseline"),
                                ("foundation-composed-current", "repair")])
        self.assertEqual((receipt["expected_invocations"], receipt["actual_case_results"]), (135, 135))
        self.assertEqual(receipt["status"],
                         "PASS_FROZEN_COMPOSED_CURRENT_AND_ORIGINAL_MIX_CAUSAL_PAIR")
        self.assertEqual(receipt["composed_product_source"], self.pair["repair"]["sha"])
        self.assertEqual(receipt["composed_product_tree"], self.pair["repair"]["tree"])
        self.assertEqual(receipt["historical_result_credit"], "NONE")
        self.assertEqual(receipt["not_selected_credit"], "NONE")
        self.assertEqual(receipt["case_timeout_seconds"], 120)
        self.assertTrue({"foundation-current", "foundation-projection-diagnostic"}
                        <= set(receipt["not_selected_pairs"]))
        self.assertFalse(receipt["formal_acceptance"])
        self.assertFalse(receipt["operations_retried"])
        self.assertEqual((snapshots, terminal, dependency), (2, 1, 1))

    def test_composed_unknown_baseline_stops_repair_without_replay_or_seal(self):
        code, receipt, seen, snapshots, terminal, dependency = self.synthetic_scope("baseline")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(seen, [("foundation-composed-current", "baseline")])
        self.assertEqual(receipt["actual_case_results"], 0)
        self.assertEqual((snapshots, terminal, dependency), (1, 0, 1))
        self.assertFalse(receipt["operations_retried"])

    def test_composed_unknown_repair_preserves_baseline_without_replay_or_seal(self):
        code, receipt, seen, snapshots, terminal, dependency = self.synthetic_scope("repair")
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "UNKNOWN")
        self.assertEqual(len(seen), 2)
        self.assertEqual(receipt["actual_case_results"], 5)
        self.assertEqual(receipt["terminal_custody"], "UNSEALED_DESCENDANTS_NOT_OBSERVED")
        self.assertEqual((snapshots, terminal, dependency), (1, 0, 1))

    def test_composed_observed_failure_does_not_inherit_historical_pass(self):
        code, receipt, seen, snapshots, terminal, dependency = self.synthetic_scope(fail_repair=True)
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(len(seen), 2)
        self.assertEqual(receipt["actual_case_results"], 135)
        self.assertEqual((snapshots, terminal, dependency), (2, 1, 1))

    def test_composed_workflow_keeps_one_executor_and_exact_linux_consumer_check(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        g0 = text.split("\n  foundation-causal-g0:\n", 1)[1].split("\n  foundation-causal:\n", 1)[0]
        for pattern in ("test_ci_foundation_causal.py", "test_ci_storage_apply_recovery.py",
                        "test_ci_saved_original_mix.py"):
            self.assertIn("--pattern " + pattern, g0)
        selected = text.split("\n  foundation-causal:\n", 1)[1]
        self.assertEqual(text.count("gate/tooling/ci_foundation_causal.py"), 1)
        self.assertEqual(text.count("needs: foundation-causal-g0"), 1)
        self.assertEqual(text.count(
            "if: inputs.verification_scope == 'foundation-composed-current'"), 1)
        consumer = selected.split("- name: Verify frozen Linux consumer contracts", 1)[1]
        consumer = consumer.split("- uses: dtolnay/rust-toolchain@", 1)[0]
        self.assertIn('test "$GITHUB_RUN_ATTEMPT" = 1', consumer)
        self.assertIn("be7d0377425f9eec201852450b5a7c53e4a1f403", consumer)
        self.assertIn("--pattern test_linux_cold_start.py --mode both --require-complete", consumer)
        self.assertIn("--expected-source be7d0377425f9eec201852450b5a7c53e4a1f403", consumer)
        self.assertIn("composed-consumer-source/tooling/ci_test_runner.py", consumer)
        self.assertIn("verification/foundation-composed-linux-consumer/receipt.json", consumer)
        self.assertNotIn("inputs.product", consumer)
        self.assertIn("'verification' || 'verification/foundation-causal'", selected)
        self.assertNotIn("matrix:", selected)
        self.assertNotIn("continue-on-error", selected)


    def test_composed_dependency_closure_reads_the_real_loaded_gate_files(self):
        root = Path(gate.__file__).resolve().parents[1]
        loaded = Path(gate.custody.__file__).resolve(strict=True)
        self.assertEqual(loaded, root / "tooling/ci_storage_apply_recovery.py")
        snapshot = {"files": {
            name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in gate.COMPOSED_DEPENDENCY_HASHES}}
        observed = gate.current_dependency_preflight(
            root, snapshot, "foundation-composed-current")
        self.assertEqual(observed["files"], gate.COMPOSED_DEPENDENCY_HASHES)
        self.assertEqual(hashlib.sha256(loaded.read_bytes()).hexdigest(),
                         "f89f31122b6f02d0032037706f3feb175bf0b18c2157ac808f040a7eb626c767")
        self.assertEqual((observed["product_source"], observed["product_tree"]),
                         (self.pair["repair"]["sha"], self.pair["repair"]["tree"]))
        source, tree, _ = gate.custody.binding4_product_profile("current-process")
        self.assertEqual((source, tree), (self.pair["repair"]["sha"], self.pair["repair"]["tree"]))

    def test_composed_originals_do_not_retarget_the_historical_dependency_profile(self):
        root = Path(gate.__file__).resolve().parents[1]
        snapshot = {"files": {
            name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in gate.COMPOSED_DEPENDENCY_HASHES}}
        with self.assertRaisesRegex(ValueError, "dependency differs"):
            gate.current_dependency_preflight(root, snapshot)
        with self.assertRaisesRegex(ValueError, "unknown current dependency profile"):
            gate.current_dependency_preflight(root, snapshot, "foundation-projection-diagnostic")
        self.assertEqual(gate.CURRENT_DEPENDENCY_HASHES["tooling/ci_storage_apply_recovery.py"],
                         "463cf14f9e3d907c8782bc7505574b214f646028baddb38efccba67d62984653")
        self.assertNotEqual(gate.CURRENT_DEPENDENCY_HASHES, gate.COMPOSED_DEPENDENCY_HASHES)


class SharedObservationStopContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_unknown_owned_group_stops_before_receipt_readback(self):
        row = {"observation": "UNKNOWN_PROCESS_NO_RETRY", "exit_code": 0, "pid": 123}
        with patch.object(gate.custody, "execute", return_value=row) as execute, \
                patch.object(gate.custody, "fingerprint") as readback:
            with self.assertRaises(gate.UnknownExecution) as caught:
                gate.command(["synthetic"], self.root, {}, self.root, "case", 120, {}, (self.root, []))
        self.assertEqual(caught.exception.command, row)
        execute.assert_called_once()
        readback.assert_not_called()

    def test_unretained_shared_receipt_remains_unknown_with_command(self):
        row = {"observation": "UNKNOWN_CUSTODY_NO_RETRY", "pid": 456}
        with patch.object(gate.custody, "execute", side_effect=gate.custody.UnknownObservation("partial", row)):
            with self.assertRaises(gate.UnknownExecution) as caught:
                gate.command(["synthetic"], self.root, {}, self.root, "case", 120, {})
        self.assertEqual(caught.exception.command, row)

    def test_post_command_metadata_error_does_not_allow_later_pair(self):
        row = {"observation": "DIRECT_PROCESS_EXIT_OBSERVED", "exit_code": 0, "log_custody": "VERIFIED_SNAPSHOT"}
        with patch.object(gate.custody, "execute", return_value=row), \
                patch.object(gate.custody, "fingerprint", side_effect=OSError("metadata absent")):
            with self.assertRaises(gate.UnknownExecution) as caught:
                gate.command(["synthetic"], self.root, {}, self.root, "case", 120, {}, (self.root, []))
        self.assertEqual(caught.exception.command, row)

    def test_ambiguous_spawn_stream_error_stops_role_scheduler(self):
        workspace, output = self.root / "workspace", self.root / "evidence"
        workspace.mkdir()
        output.mkdir()
        pair = {"id": "synthetic-unknown", "baseline": {
            "sha": "1" * 40, "tree": "2" * 40, "targets": {}}, "repair": {
            "sha": "3" * 40, "tree": "4" * 40, "targets": {}}}
        original_open = Path.open
        visits = []

        class FailingClose:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self.stream.__enter__()

            def __exit__(self, *error):
                self.stream.__exit__(*error)
                raise OSError("stderr finalization failed")

        def opened(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            return FailingClose(stream) if mode == "xb" and path.name.endswith(".stderr.log") else stream

        def callback(spec, role):
            visits.append(role)
            return gate.run_role(spec, role, self.root, workspace, output,
                                 self.root / "unused-target", self.root / "unused-temp", {}, {}, {})

        with patch.object(Path, "open", opened), \
                patch.object(gate.custody.subprocess, "Popen", side_effect=OSError("spawn handle unavailable")) as spawn, \
                patch.object(gate.custody, "stop_observed_process") as stop:
            results = gate.run_pairs(callback, [pair])
        self.assertEqual(visits, ["baseline"])
        self.assertEqual(results[0]["status"], "UNKNOWN")
        command = results[0]["roles"][0]["unsealed_command"]
        self.assertEqual(command["uncertainty_reason"], "SPAWN_HANDLE_NOT_OBSERVED")
        self.assertEqual(command["secondary_observation_errors"], ["OSError: stderr finalization failed"])
        self.assertFalse((output / pair["id"] / "repair").exists())
        spawn.assert_called_once()
        stop.assert_not_called()


    def test_stream_custody_failure_stops_role_scheduler_after_known_exit(self):
        workspace, output = self.root / "workspace", self.root / "evidence"
        workspace.mkdir()
        output.mkdir()
        pair = {"id": "synthetic-stream-custody", "baseline": {
            "sha": "1" * 40, "tree": "2" * 40, "targets": {}}, "repair": {
            "sha": "3" * 40, "tree": "4" * 40, "targets": {}}}
        visits = []
        original_fingerprint = gate.custody.fingerprint

        class ObservedExit:
            pid = 123456789

            def wait(self, timeout=None):
                return 0

        def fingerprint(path, *args, **kwargs):
            if str(path).endswith((".stdout.log", ".stderr.log")):
                raise OSError("controlled stream readback failure")
            return original_fingerprint(path, *args, **kwargs)

        def callback(spec, role):
            visits.append(role)
            return gate.run_role(spec, role, self.root, workspace, output,
                                 self.root / "unused-target", self.root / "unused-temp", {}, {}, {})

        with patch.object(gate.custody.subprocess, "Popen", return_value=ObservedExit()) as spawn, \
                patch.object(gate.custody, "supports_owned_process_group", return_value=False), \
                patch.object(gate.custody, "fingerprint", side_effect=fingerprint), \
                patch.object(gate.custody, "stop_observed_process") as stop:
            results = gate.run_pairs(callback, [pair])
        self.assertEqual(visits, ["baseline"])
        self.assertEqual(results[0]["status"], "UNKNOWN")
        command = results[0]["roles"][0]["unsealed_command"]
        self.assertEqual(command["exit_code"], 0)
        self.assertEqual(command["pid"], 123456789)
        self.assertEqual(command["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
        self.assertEqual(command["uncertainty_reason"], "STREAM_LOG_CUSTODY_FAILED")
        self.assertEqual(command["log_custody"], "INCOMPLETE")
        self.assertFalse((output / pair["id"] / "repair").exists())
        spawn.assert_called_once()
        stop.assert_not_called()



    def assert_original_readback_stops_role_scheduler(self, phase):
        workspace, output = self.root / ("workspace-" + phase), self.root / ("evidence-" + phase)
        target, temporary = self.root / ("target-" + phase), self.root / ("temp-" + phase)
        for folder in (workspace, output, target, temporary):
            folder.mkdir()
        target_name = next(iter(gate.TARGETS))
        pair = {"id": "synthetic-original-readback-" + phase, "baseline": {
            "sha": "1" * 40, "tree": "2" * 40, "targets": {target_name: ["synthetic-case"]}},
            "repair": {"sha": "3" * 40, "tree": "4" * 40,
                       "targets": {target_name: ["synthetic-case"]}}}
        executable = target / "synthetic-binary"
        executable.write_bytes(b"retained synthetic test binary")
        binary = {"absolute_path": str(executable),
                  **gate.custody.fingerprint(executable, target)}
        visits = []
        original_read = gate.custody.read_original
        trigger = target_name + ("-build.stdout.log" if phase == "build" else "-case-01.stdout.log")

        class ObservedExit:
            pid = 123456789

            def wait(self, timeout=None):
                return 0

        def replace_before_read(folder, command, stream):
            path = folder / command[stream]["path"]
            if path.name == trigger:
                path.write_bytes(b"controlled replacement after original seal")
            return original_read(folder, command, stream)

        def callback(spec, role):
            visits.append(role)
            return gate.run_role(spec, role, self.root, workspace, output, target, temporary,
                                 {}, {}, {"cargo": {"absolute_path": "synthetic-cargo"}})

        with patch.object(gate.custody.subprocess, "Popen", return_value=ObservedExit()) as spawn, \
                patch.object(gate.custody, "supports_owned_process_group", return_value=False), \
                patch.object(gate.custody, "read_original", side_effect=replace_before_read), \
                patch.object(gate.custody, "stop_observed_process") as stop, \
                patch.object(gate, "product_snapshot", return_value={"synthetic": True}) as snapshot, \
                patch.object(gate, "retain_source", return_value={}), \
                patch.object(gate, "select_binary", return_value=binary), \
                patch.object(gate, "retain_file", return_value={}), \
                patch.object(gate, "selected_roster", return_value=["synthetic-case"]):
            results = gate.run_pairs(callback, [pair])
        self.assertEqual(visits, ["baseline"])
        self.assertEqual(results[0]["status"], "UNKNOWN")
        role = results[0]["roles"][0]
        command = role["unsealed_command"]
        self.assertEqual(command["observation"], "UNKNOWN_CUSTODY_NO_RETRY")
        self.assertEqual(command["uncertainty_reason"], "ORIGINAL_STREAM_READBACK_FAILED")
        self.assertEqual((command["pid"], command["exit_code"]), (123456789, 0))
        self.assertEqual(command["original_command"]["observation"], "DIRECT_PROCESS_EXIT_OBSERVED")
        original_path = output / pair["id"] / "baseline" / (trigger[:-len(".stdout.log")] + ".command.json")
        self.assertEqual(json.loads(original_path.read_bytes()), command["original_command"])
        self.assertEqual(role["results"], [])
        self.assertNotIn("source_after", role)
        self.assertFalse((output / pair["id"] / "repair").exists())
        self.assertEqual(spawn.call_count, 2 if phase == "build" else 4)
        snapshot.assert_called_once()
        stop.assert_not_called()

    def test_build_original_changed_after_seal_stops_before_repair(self):
        self.assert_original_readback_stops_role_scheduler("build")

    def test_case_original_changed_after_seal_escapes_semantic_failure_catch(self):
        self.assert_original_readback_stops_role_scheduler("case")


if __name__ == "__main__":
    unittest.main()
