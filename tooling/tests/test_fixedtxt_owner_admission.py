"""Source controls for the new 043c+A4 owner join; no native or network effects.

The production modules are imported whole with their actual source-lock closure.
Runtime installation and Git source-binding edges are explicit inert doubles in
the filesystem cases; input acquisition uses the real no-follow openat reader.
No fixture row or reviewed-wrapper pin is shipped in production source.
"""
from contextlib import contextmanager
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_private_evidence as crypto
import native_evidence_projection as core
import native_film180_evidence_selection as film
import native_film180_original_g2 as wrapper
import native_mlt_evidence_selection as selection
import native_mlt_g2_custody as custody
from tooling.tests.test_native_film180_evidence_selection import fixedtxt_fixture


def source_fixture(*, temporary="/home/runner/work/_temp"):
    current, plan, originals = fixedtxt_fixture()
    suffix = current["run_id"] + "-1"
    control = temporary + "/mw-fixedtxt-g2-control-" + suffix
    inputs = temporary + "/mw-fixedtxt-g2-inputs-" + suffix
    layout = {"control_root": control, "inputs_root": inputs,
        "plan": control + "/fixedtxt-plan.original.json",
        "runtime": inputs + "/source-input.original.json", "cycle": inputs + "/cycle.original.json",
        "observation_root": temporary + "/mw-fixedtxt-g2-original-" + suffix}
    cycle = {"schema": "motionwright.fixed-text-native-cycle-inputs/4", "arm": plan["arm"],
        "decoder_sha256": plan["decoder_sha256"], "runtime": {
            "semwright_revision": current["semwright_sha"],
            "native_source_sha256": {key: value["native_source_sha256"] for key, value in plan["arm_sources"].items()}}}
    cycle_raw = crypto.json_bytes(cycle)
    plan["cycle_input_sha256"] = crypto.sha(cycle_raw)
    for row in plan["members"]:
        if row["role"] == "cycle-inputs":
            row["expected_sha256"] = crypto.sha(cycle_raw)
        if (row["root"], row["relative_path"]) == ("inputs", "source-input.original.json"):
            row.update(maximum=65536, expected_sha256=None)
    plan["members"].append({"root": "inputs", "relative_path": "cycle.original.json",
        "role": "input-original", "maximum": 1024 * 1024, "expected_sha256": crypto.sha(cycle_raw)})
    plan["members"].sort(key=lambda row: (row["root"], row["relative_path"]))
    plan_raw = crypto.json_bytes(plan)
    runtime = {key: "INERT-SOURCE-CONTROL-NOT-RUNTIME" for key in custody.RUNTIME_INPUT_KEYS}
    runtime.update(schema="motionwright.native.fixedtxt-v4-g2-inputs/1", motionwright_sha=current["source_sha"],
        semwright_sha=current["semwright_sha"], run_id=current["run_id"], run_attempt=current["run_attempt"],
        private_evidence_dir=layout["observation_root"], fixedtxt_admission_id="synthetic",
        recipient_sha256="f" * 64, fixedtxt_plan={"path": layout["plan"], "bytes": len(plan_raw), "sha256": crypto.sha(plan_raw)})
    runtime_raw = crypto.json_bytes(runtime)
    row = {"schema": selection.FIXEDTXT_ADMISSION_SCHEMA, "scope": film.FIXEDTXT_SCOPE,
        "context": current, "plan_sha256": crypto.sha(plan_raw), "arm": plan["arm"], "fixture": plan["fixture"],
        "recipient_sha256": "f" * 64, "limits": plan["limits"], "single_owner": 1,
        "runtime_original": {"root": "inputs", "relative_path": "source-input.original.json",
                             "bytes": len(runtime_raw), "sha256": crypto.sha(runtime_raw)}}
    env = {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": current["run_id"], "GITHUB_SHA": current["source_sha"],
        "GITHUB_WORKSPACE": str(wrapper.ROOT), "RUNNER_TEMP": temporary, "MW_MLT_SCOPE": film.FIXEDTXT_SCOPE,
        "MOTIONWRIGHT_APPROVED_FIXEDTXT_V4_G2": "1", "MOTIONWRIGHT_APPROVED_MLT_G2": "1"}
    return {"current": current, "plan": plan, "plan_raw": plan_raw, "runtime": runtime,
        "runtime_raw": runtime_raw, "cycle": cycle, "cycle_raw": cycle_raw, "row": row, "layout": layout, "env": env}


def rebind(world):
    """Inert independently reviewed fixtures for semantic negative cases only."""
    world["plan_raw"] = crypto.json_bytes(world["plan"])
    world["row"]["plan_sha256"] = crypto.sha(world["plan_raw"])
    world["runtime"]["fixedtxt_plan"].update(bytes=len(world["plan_raw"]), sha256=crypto.sha(world["plan_raw"]))
    world["runtime_raw"] = crypto.json_bytes(world["runtime"])
    world["row"]["runtime_original"].update(bytes=len(world["runtime_raw"]), sha256=crypto.sha(world["runtime_raw"]))


@contextmanager
def admitted(world):
    with mock.patch.object(selection, "FIXEDTXT_ATTEMPTS", {"synthetic": world["row"]}), \
            mock.patch.object(selection, "FIXEDTXT_REVIEWED_WRAPPER_SHA256", "b" * 64):
        yield


def prepare(world, **changes):
    layout = world["layout"]
    arguments = dict(current=world["current"], admission_id="synthetic", recipient_sha256="f" * 64,
        plan_input_path=layout["plan"], runtime_input_path=layout["runtime"], cycle_input_path=layout["cycle"],
        cycle_raw=world["cycle_raw"], control_root=layout["control_root"], inputs_root=layout["inputs_root"],
        source_root=str(wrapper.ROOT), python_executable=sys.executable, environment=world["env"])
    arguments.update(changes)
    return custody.fixedtxt_prepare_owner_invocation(world["plan_raw"], world["runtime_raw"], **arguments)


@contextmanager
def filesystem_world():
    with tempfile.TemporaryDirectory(prefix="fixedtxt-owner-source-control-") as temporary:
        world = source_fixture(temporary=str(Path(temporary).resolve()))
        for label in ("control_root", "inputs_root"):
            Path(world["layout"][label]).mkdir(mode=0o700)
        for key, raw in (("plan", world["plan_raw"]), ("runtime", world["runtime_raw"]), ("cycle", world["cycle_raw"])):
            Path(world["layout"][key]).write_bytes(raw)
        with admitted(world), mock.patch.dict(os.environ, world["env"], clear=True), \
                mock.patch.object(custody.admission, "disposable_linux_runner", return_value=Path(temporary).resolve()), \
                mock.patch.object(core, "verify_source_bindings", return_value={"inert_source_binding_edge": True}) as source:
            world["source_check"] = source
            yield world


def acquire(world, **changes):
    arguments = dict(runtime_input_path=world["layout"]["runtime"], cycle_input_path=world["layout"]["cycle"],
        selected_fixture=world["plan"]["fixture"], source_root=str(wrapper.ROOT), python_executable=sys.executable)
    arguments.update(changes)
    return custody.fixedtxt_acquire_owner_originals(world["env"], **arguments)


class FixedTxtOwnerAdmissionTests(unittest.TestCase):
    def test_shipped_source_has_no_attempt_or_reviewed_wrapper_pin(self):
        self.assertEqual(selection.FIXEDTXT_ATTEMPTS, {})
        self.assertIsNone(selection.FIXEDTXT_REVIEWED_WRAPPER_SHA256)

    def denied_entries(self, world):
        return [lambda: prepare(world), lambda: acquire(world),
            lambda: selection.admit_fixedtxt_attempt(world["plan_raw"], current=world["current"], admission_id="synthetic", recipient_sha256="f" * 64),
            lambda: selection.admit_fixedtxt_runtime_original(world["plan_raw"], world["runtime_raw"], current=world["current"],
                admission_id="synthetic", recipient_sha256="f" * 64, plan_input_path=world["layout"]["plan"]),
            lambda: wrapper.admit_fixedtxt_wrapper_originals(Path(world["layout"]["runtime"]), Path(world["layout"]["cycle"]), selected_fixture=world["plan"]["fixture"]),
            lambda: wrapper.execute_fixed_text_v4(Path(world["layout"]["runtime"]), Path(world["layout"]["cycle"]), selected_fixture=world["plan"]["fixture"])]

    def test_empty_catalogue_denies_every_owner_entry_before_reads_parse_or_effects(self):
        world = source_fixture()
        with mock.patch.object(selection, "FIXEDTXT_ATTEMPTS", {}), \
                mock.patch.object(core, "RootHandles", side_effect=AssertionError("no input open")), \
                mock.patch.object(crypto, "strict", side_effect=AssertionError("no plan parse")), \
                mock.patch.object(custody, "strict", side_effect=AssertionError("no runtime routing")), \
                mock.patch.object(wrapper.FilmAttempt, "prepare", side_effect=AssertionError("no mkdir/runtime preparation")), \
                mock.patch.object(custody, "fixedtxt_owner_layout", side_effect=AssertionError("guard precedes layout")):
            for entry in self.denied_entries(world):
                with self.subTest(entry=entry), self.assertRaisesRegex(core.SelectionError, "no-reviewed-attempt"):
                    entry()

    def test_missing_wrapper_pin_denies_before_even_guarded_routing(self):
        world = source_fixture()
        with mock.patch.object(selection, "FIXEDTXT_ATTEMPTS", {"synthetic": world["row"]}), \
                mock.patch.object(selection, "FIXEDTXT_REVIEWED_WRAPPER_SHA256", None), \
                mock.patch.object(core, "RootHandles", side_effect=AssertionError("no input open")), \
                mock.patch.object(crypto, "strict", side_effect=AssertionError("no parse")), \
                mock.patch.object(custody, "strict", side_effect=AssertionError("no routing")), \
                mock.patch.object(wrapper.FilmAttempt, "prepare", side_effect=AssertionError("no preparation")):
            for entry in self.denied_entries(world):
                with self.subTest(entry=entry), self.assertRaisesRegex(core.SelectionError, "wrapper-pin-absent"):
                    entry()

    def test_layout_uses_verified_context_and_closed_sibling_names(self):
        world = source_fixture()
        with admitted(world):
            self.assertEqual(custody.fixedtxt_owner_layout(current=world["current"], runner_temp=world["env"]["RUNNER_TEMP"]), world["layout"])
            for root in ("/", "/tmp/../owner", "/tmp//owner", "relative", "C:\\private"):
                with self.subTest(root=root), self.assertRaises(crypto.CustodyRejected):
                    custody.fixedtxt_owner_layout(current=world["current"], runner_temp=root)

    def test_buffered_preparer_has_no_path_reads_and_returns_only_existing_argv(self):
        world = source_fixture()
        before = (world["plan_raw"], world["runtime_raw"], world["cycle_raw"])
        with admitted(world), mock.patch.object(Path, "open", side_effect=AssertionError("no reopening")), \
                mock.patch.object(Path, "resolve", side_effect=AssertionError("no resolving")), \
                mock.patch.object(Path, "mkdir", side_effect=AssertionError("no output")), \
                mock.patch("os.open", side_effect=AssertionError("no openat in buffered preparer")):
            actual = prepare(world)
        self.assertEqual(actual, [sys.executable, "-B", "-E", "-s", str(wrapper.ROOT / "tooling/native_film180_original_g2.py"),
            "--g2-inputs", world["layout"]["runtime"], "--fixed-text-v4-inputs", world["layout"]["cycle"],
            "--fixed-text-v4-fixture", world["plan"]["fixture"]])
        self.assertEqual(before, (world["plan_raw"], world["runtime_raw"], world["cycle_raw"]))

    def test_plan_and_runtime_originals_cannot_be_json_equivalent_replacements(self):
        for member in ("plan_raw", "runtime_raw"):
            world = source_fixture(); world[member] += b" "
            with self.subTest(member=member), admitted(world), self.assertRaisesRegex(core.SelectionError, "independent-(plan|runtime)-original"):
                prepare(world)

    def test_self_consistent_runtime_internal_pins_do_not_replace_external_pin(self):
        world = source_fixture()
        world["runtime"]["private_evidence_dir"] = "/synthetic/foreign"
        world["runtime_raw"] = crypto.json_bytes(world["runtime"])
        with admitted(world), self.assertRaisesRegex(core.SelectionError, "independent-runtime-original"):
            prepare(world)

    def test_runtime_descriptor_lengths_are_strict_nonboolean_and_bounded(self):
        for value in (True, False, 0, -1, 65537, 2.0):
            world = source_fixture(); world["row"]["runtime_original"]["bytes"] = value
            with self.subTest(value=value), admitted(world), mock.patch.object(crypto, "strict", side_effect=AssertionError("no parse")), \
                    self.assertRaisesRegex(core.SelectionError, "runtime-original-byte-bound"):
                prepare(world)

    def test_reviewed_wrapper_pin_must_equal_the_plan_source_binding(self):
        world = source_fixture()
        for row in world["plan"]["source_bindings"]:
            if row["path"] == "tooling/native_film180_original_g2.py": row["sha256"] = "e" * 64
        rebind(world)
        with admitted(world), self.assertRaisesRegex(core.SelectionError, "reviewed-wrapper-source-binding"):
            prepare(world)

    def test_independent_context_recipient_and_family_cannot_be_supplied_by_runtime(self):
        for field, value in (("source_sha", "e" * 40), ("run_id", "998877")):
            world = source_fixture(); other = {**world["current"], field: value}
            with self.subTest(field=field), admitted(world), self.assertRaises(crypto.CustodyRejected):
                prepare(world, current=other)
        world = source_fixture()
        with admitted(world), self.assertRaises(crypto.CustodyRejected):
            prepare(world, recipient_sha256=crypto.sha(crypto.json_bytes({"sha256": "f" * 64})))
        for scope in ("film180-g2", "repaired-g2", "normal"):
            world = source_fixture(); world["env"]["MW_MLT_SCOPE"] = scope
            with self.subTest(scope=scope), admitted(world), self.assertRaises(crypto.CustodyRejected):
                prepare(world)

    def test_more_than_one_reviewed_row_for_current_context_is_not_a_selector(self):
        world = source_fixture()
        with admitted(world), mock.patch.object(selection, "FIXEDTXT_ATTEMPTS", {"synthetic": world["row"], "another": copy.deepcopy(world["row"])}), \
                mock.patch.object(core, "RootHandles", side_effect=AssertionError("no routing")), \
                self.assertRaisesRegex(crypto.CustodyRejected, "one-independent-current-row"):
            acquire(world)

    def test_only_the_selected_runtime_input_may_have_the_acyclic_null_pin(self):
        world = source_fixture()
        for row in world["plan"]["members"]:
            if (row["root"], row["relative_path"]) == ("inputs", "cycle.original.json"): row["expected_sha256"] = None
        rebind(world)
        with admitted(world), self.assertRaisesRegex(core.SelectionError, "only-nullable-input"):
            prepare(world)

    def test_cycle_bytes_and_selected_arm_remain_bound_before_argv(self):
        world = source_fixture()
        with admitted(world), self.assertRaisesRegex(crypto.CustodyRejected, "cycle-original"):
            prepare(world, cycle_raw=world["cycle_raw"] + b" ")
        world = source_fixture(); world["cycle"]["arm"] = "GREEN"
        world["cycle_raw"] = crypto.json_bytes(world["cycle"])
        world["plan"]["cycle_input_sha256"] = crypto.sha(world["cycle_raw"])
        for row in world["plan"]["members"]:
            if row["role"] == "cycle-inputs" or (row["root"], row["relative_path"]) == ("inputs", "cycle.original.json"):
                row["expected_sha256"] = crypto.sha(world["cycle_raw"])
        rebind(world)
        with admitted(world), self.assertRaisesRegex(crypto.CustodyRejected, "cycle-selected-bindings"):
            prepare(world)

    def test_cli_does_not_normalize_aliases_before_the_owner_guard(self):
        for original in ("/a/./source.json", "/a//source.json", "/a/x/../source.json", "C:\\a\\source.json"):
            with self.subTest(original=original), self.assertRaisesRegex(wrapper.Film180G2Rejected, "CLI_noncanonical"):
                wrapper.parse_cli(["--g2-inputs", original, "--fixed-text-v4-inputs", "/a/cycle.json", "--fixed-text-v4-fixture", "fixed-causal"])

    def test_real_input_reader_captures_only_R_P_Q_once_and_forwards_same_buffers(self):
        with filesystem_world() as world:
            calls = []
            original = core.RootHandles.read
            def observed_read(handles, root, parts, maximum):
                result = original(handles, root, parts, maximum)
                calls.append((root, "/".join(parts), maximum, result[0]))
                return result
            with mock.patch.object(core.RootHandles, "read", observed_read), \
                    mock.patch.object(custody, "fixedtxt_prepare_owner_invocation", wraps=custody.fixedtxt_prepare_owner_invocation) as buffered:
                result = acquire(world)
            self.assertEqual([(root, name, maximum) for root, name, maximum, _ in calls], [
                ("inputs", "source-input.original.json", 65536), ("control", "fixedtxt-plan.original.json", film.FIXEDTXT_MAX_JSON),
                ("inputs", "cycle.original.json", 1024 * 1024)])
            self.assertIs(result["runtime_raw"], calls[0][3]); self.assertIs(result["plan_raw"], calls[1][3])
            self.assertIs(result["cycle_raw"], calls[2][3])
            self.assertIs(buffered.call_args.args[0], result["plan_raw"])
            self.assertIs(buffered.call_args.args[1], result["runtime_raw"])
            self.assertIs(buffered.call_args.kwargs["cycle_raw"], result["cycle_raw"])
            self.assertEqual(world["source_check"].call_count, 1)

    def test_untrusted_R_plan_path_is_rejected_before_P_is_opened(self):
        for foreign in ("/private/foreign-plan.json", "/tmp/../plan.json", "plan.json"):
            with self.subTest(path=foreign), filesystem_world() as world:
                world["runtime"]["fixedtxt_plan"]["path"] = foreign
                raw = crypto.json_bytes(world["runtime"])
                Path(world["layout"]["runtime"]).write_bytes(raw)
                calls = []; original = core.RootHandles.read
                def observed_read(handles, root, parts, maximum):
                    calls.append((root, "/".join(parts)))
                    return original(handles, root, parts, maximum)
                with mock.patch.object(core.RootHandles, "read", observed_read), self.assertRaisesRegex(crypto.CustodyRejected, "routing-hints"):
                    acquire(world)
                self.assertEqual(calls, [("inputs", "source-input.original.json")])

    def test_caller_cannot_replace_owned_roots_with_runtime_parent_or_other_area(self):
        world = source_fixture()
        with admitted(world), mock.patch.object(core, "RootHandles", side_effect=AssertionError("no path open")):
            for path in ("/tmp/source-input.original.json", world["layout"]["runtime"] + "/../source-input.original.json"):
                with self.subTest(path=path), self.assertRaisesRegex(crypto.CustodyRejected, "CLI-inputs"):
                    acquire(world, runtime_input_path=path)
            with self.assertRaisesRegex(crypto.CustodyRejected, "owner-input-location"):
                prepare(world, control_root=world["layout"]["inputs_root"])

    def test_input_symlinks_and_hardlinks_are_rejected_by_real_anchored_opener(self):
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), filesystem_world() as world:
                path = Path(world["layout"]["runtime"])
                alternate = Path(world["env"]["RUNNER_TEMP"]) / "alternate-original"
                path.rename(alternate)
                if kind == "symlink": path.symlink_to(alternate)
                else: os.link(alternate, path)
                with self.assertRaises((core.SelectionError, OSError)):
                    acquire(world)

    def test_missing_cycle_input_prevents_argv_and_output_creation(self):
        with filesystem_world() as world:
            Path(world["layout"]["cycle"]).unlink()
            with mock.patch.object(custody, "fixedtxt_prepare_owner_invocation", side_effect=AssertionError("no argv without Q")) as buffered, \
                    self.assertRaises(FileNotFoundError):
                acquire(world)
            buffered.assert_not_called()
            self.assertFalse(Path(world["layout"]["observation_root"]).exists())

    def test_actual_source_binding_failure_precedes_runtime_semantics_and_Q_read(self):
        with filesystem_world() as world:
            original_error = core.SelectionError("inert exact source binding failed")
            world["source_check"].side_effect = original_error
            calls = []; original = core.RootHandles.read
            def observed_read(handles, root, parts, maximum):
                calls.append((root, "/".join(parts)))
                return original(handles, root, parts, maximum)
            with mock.patch.object(core.RootHandles, "read", observed_read), \
                    mock.patch.object(selection, "admit_fixedtxt_runtime_original", side_effect=AssertionError("no semantic R")), \
                    self.assertRaises(core.SelectionError) as raised:
                acquire(world)
            self.assertIs(raised.exception, original_error)
            self.assertEqual(calls, [("inputs", "source-input.original.json"), ("control", "fixedtxt-plan.original.json")])

    def test_original_input_error_survives_handle_close_error_without_retry(self):
        world = source_fixture(); original = OSError("inert original read failure")
        handles = mock.Mock()
        handles.read.side_effect = original
        handles.close.side_effect = OSError("inert secondary close failure")
        with admitted(world), mock.patch.object(custody.admission, "disposable_linux_runner", return_value=Path(world["env"]["RUNNER_TEMP"])), \
                mock.patch.object(core, "verify_source_bindings", return_value={}), \
                mock.patch.object(core, "RootHandles", return_value=handles), self.assertRaises(OSError) as raised:
            acquire(world)
        self.assertIs(raised.exception, original)
        self.assertEqual(handles.read.call_count, 1); self.assertEqual(handles.close.call_count, 1)

    def test_actual_wrapper_prepare_consumes_held_runtime_without_reopening_it(self):
        with filesystem_world() as world:
            held = acquire(world)
            with mock.patch.object(wrapper.observed.admission, "checked_bytes", side_effect=AssertionError("no R reopen")), \
                    mock.patch.object(wrapper.observed, "verify_runtime_inputs", return_value=({"inert_runtime_edge": True}, {})), \
                    mock.patch.object(wrapper, "RUN_SHA", world["current"]["source_sha"]), \
                    mock.patch.object(wrapper, "RUN_ID", world["current"]["run_id"]), \
                    mock.patch.object(wrapper, "RUN_ATTEMPT", "1"):
                result = wrapper.FilmAttempt.prepare(Path(world["layout"]["runtime"]), fixedtxt_originals=held)
            self.assertIs(result.fixedtxt_originals, held)
            self.assertEqual(result.fixedtxt_admitted_plan, world["plan"])
            self.assertEqual(result.evidence_dir, Path(world["layout"]["observation_root"]))
            self.assertEqual(set(path.name for path in result.evidence_dir.iterdir()), {"runtime-input-custody.json"})

    def test_actual_cycle_entry_passes_the_held_Q_buffer_without_rereading(self):
        world = source_fixture(); original = ValueError("stop at actual parser edge, no runtime")
        seen = []
        def inspect(raw):
            seen.append(raw)
            raise original
        with mock.patch.object(wrapper, "regular", side_effect=AssertionError("no Q reopen")), \
                mock.patch.object(wrapper, "fixed_json", side_effect=inspect), self.assertRaises(ValueError) as raised:
            wrapper.fixed_cycle_inputs(Path("unused"), object(), original_raw=world["cycle_raw"])
        self.assertIs(raised.exception, original)
        self.assertEqual(len(seen), 1); self.assertIs(seen[0], world["cycle_raw"])


if __name__ == "__main__":
    unittest.main()
