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
            "    if: github.event_name != 'workflow_dispatch' || (inputs.verification_scope != 'foundation-causal' && inputs.verification_scope != 'foundation-import-budget')\n", "", 1)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         "a2acf46f418b035186c9a7b9dce068b3f266e99703f4c773da183a579eb45725")
        self.assertIn("default: full", text)
        self.assertIn("options: [full, foundation-causal, foundation-import-budget]", text)

    def test_selected_workflow_requires_g0_and_pins_one_sequential_linux_execution(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        selected = text.split("\n  foundation-causal:\n", 1)[1]
        self.assertIn("needs: foundation-causal-g0", selected)
        self.assertIn("github.event_name == 'workflow_dispatch' && (inputs.verification_scope == 'foundation-causal' || inputs.verification_scope == 'foundation-import-budget')", selected)
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
        self.assertIn("options: [full, foundation-causal, foundation-import-budget]", text)
        self.assertIn("default: full", text)
        self.assertEqual(text.count(
            "github.event_name == 'workflow_dispatch' && "
            "(inputs.verification_scope == 'foundation-causal' || "
            "inputs.verification_scope == 'foundation-import-budget')"), 2)
        self.assertIn(
            "FOUNDATION_CAUSAL_SELECTION: $" + "{{ inputs.verification_scope }}", text)
        self.assertIn('--selection "$FOUNDATION_CAUSAL_SELECTION"', text)
        self.assertEqual(text.count("gate/tooling/ci_foundation_causal.py"), 1)
        self.assertEqual(text.count("needs: foundation-causal-g0"), 1)
        self.assertNotIn("\n  foundation-import-budget:", text)


if __name__ == "__main__":
    unittest.main()
