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
            "    if: github.event_name != 'workflow_dispatch' || inputs.verification_scope != 'foundation-causal'\n", "", 1)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         "a2acf46f418b035186c9a7b9dce068b3f266e99703f4c773da183a579eb45725")
        self.assertIn("default: full", text)
        self.assertIn("options: [full, foundation-causal]", text)

    def test_selected_workflow_requires_g0_and_pins_one_sequential_linux_execution(self):
        text = (Path(gate.__file__).resolve().parents[1] / gate.WORKFLOW).read_text(encoding="utf-8")
        selected = text.split("\n  foundation-causal:\n", 1)[1]
        self.assertIn("needs: foundation-causal-g0", selected)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.verification_scope == 'foundation-causal'", selected)
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


if __name__ == "__main__":
    unittest.main()
