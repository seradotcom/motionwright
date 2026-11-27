"""Offline contract fixtures only; these tests never render or grant acceptance."""
import copy
import json
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import native_fixedtxt_session as serial
import native_fixedtxt_workflow as route
import review_fixedtxt_result_matrix_gn1_bound as reader
import fixedtxt_typed_matrix_probe as typed
import preparation_session as preparation

TEMPLATE = (Path(__file__).parent / "request-contract-fixture.json").read_bytes()
CONTEXT = {"source_sha": "76f05572ec7a41035fc42054e27d01c869cd6176",
           "semwright_sha": "797e70884d35a068c72149ffae3e6df365bb0b64",
           "run_id": "43681879798", "run_attempt": "1"}


class ContractOriginals:
    def __init__(self, values):
        self.values = values

    def small(self, name, maximum):
        raw = self.values[name]
        if not 0 < len(raw) <= maximum:
            raise ValueError("contract_member_bound")
        return raw


def contract(*, session, fixture="fixed-counter-captions"):
    template = json.loads(TEMPLATE)
    template["arm"] = "GREEN"
    template_raw = serial.canonical(template)
    index = serial.ROSTER.index(fixture)
    if session:
        request_raw = serial.derive_request(template_raw, index)
    else:
        request = copy.deepcopy(template)
        request["fixture"] = fixture
        if fixture == "fixed-counter-captions":
            for role in ("native-png", "selected-png"):
                request["role_limits"][role] = 750 * 1024
        request_raw = serial.canonical(request)
    request = json.loads(request_raw)
    start = 100.0 + (index * 5 if session else 0)
    clock = {"schema": "motionwright.native.fixedtxt-original-clock/1",
             "context": CONTEXT, "boot_id": "11111111-1111-1111-1111-111111111111",
             "started_monotonic": start, "deadline_monotonic": start + request["limits"]["attempt_seconds"],
             "request_sha256": reader.sha(request_raw)}
    clock_raw = serial.canonical(clock)
    values = {"control/request.original.json": request_raw, "control/clock.original.json": clock_raw}
    runner_temp = "/home/runner/work/_temp"
    if session:
        session_clock = {"schema": serial.SESSION_SCHEMA, "context": CONTEXT,
                         "boot_id": clock["boot_id"], "started_monotonic": 100.0,
                         "deadline_monotonic": 100.0 + template["limits"]["attempt_seconds"],
                         "template": serial.descriptor(template_raw), "roster": list(serial.ROSTER)}
        session_clock_raw = serial.canonical(session_clock)
        session_plan = {"schema": serial.PLAN_SCHEMA, "context": CONTEXT, "arm": "GREEN",
                        "runner_temp": runner_temp, "session_clock": serial.descriptor(session_clock_raw),
                        "template": serial.descriptor(template_raw),
                        "cases": [{"index": i, "fixture": f,
                                   "request": serial.descriptor(serial.derive_request(template_raw, i)),
                                   "namespace": serial.namespace(runner_temp, serial.digest(serial.derive_request(template_raw, i)))}
                                  for i, f in enumerate(serial.ROSTER)]}
        session_plan_raw = serial.canonical(session_plan)
        runner_temp = session_plan["cases"][index]["namespace"]
        binding = {"schema": serial.BINDING_SCHEMA, "context": CONTEXT,
                   "index": index, "fixture": fixture, "arm": "GREEN",
                   "request": serial.descriptor(request_raw), "clock": serial.descriptor(clock_raw),
                   "session_plan": serial.descriptor(session_plan_raw),
                   "session_clock": serial.descriptor(session_clock_raw),
                   "namespace": runner_temp, "effective_deadline_monotonic": session_clock["deadline_monotonic"]}
        values.update({"control/session-clock.original.json": session_clock_raw,
                       "control/session-plan.original.json": session_plan_raw,
                       "control/session-template.original.json": template_raw,
                       "control/session-case.original.json": serial.canonical(binding)})
    members = [{"root": "control", "relative_path": key.removeprefix("control/"),
                "role": "input-original", "maximum": len(raw), "expected_sha256": reader.sha(raw)}
               for key, raw in values.items()]
    for role, frames in (("native-png", range(180)), ("selected-png", reader.FIXTURE_FRAMES[fixture])):
        members.extend({"root": "owner" if role == "native-png" else "observation",
                        "relative_path": f"frames/{frame:06d}.png",
                        "role": role, "maximum": request["role_limits"][role], "expected_sha256": None}
                       for frame in frames)
    plan = {"context": CONTEXT, "arm": "GREEN", "fixture": fixture,
            "limits": request["limits"], "members": members}
    selection = reader.closed_selection("GREEN", fixture)
    return ContractOriginals(values), plan, selection, runner_temp


class TransportOriginalContracts(unittest.TestCase):
    def observe(self, data):
        originals, plan, selection, namespace = data
        return reader.original_transport_binding(originals, plan, route, serial, selection, namespace)

    def test_individual_and_session_captions_keep_distinct_exact_limits(self):
        individual = self.observe(contract(session=False))
        session = self.observe(contract(session=True))
        self.assertEqual(individual["png_limit_bytes"], 750 * 1024)
        self.assertEqual(session["png_limit_bytes"], 746 * 1024)
        self.assertEqual(individual["mode"], "individual")
        self.assertEqual(session["session_index"], 8)
        self.assertEqual(len(session["session_original_sha256"]), 4)

    def test_control_and_other_fixtures_keep_768_kib_in_both_modes(self):
        for fixture in ("fixed-multirun-control", "fixed-causal", "fixed-transform-clip"):
            for session in (False, True):
                with self.subTest(fixture=fixture, session=session):
                    self.assertEqual(self.observe(contract(session=session, fixture=fixture))["png_limit_bytes"], 768 * 1024)

    def test_crossed_individual_and_session_plan_limits_are_rejected(self):
        for session, invalid in ((False, 746 * 1024), (True, 750 * 1024)):
            data = contract(session=session)
            for row in data[1]["members"]:
                if row["role"] in ("native-png", "selected-png"):
                    row["maximum"] = invalid
            with self.subTest(session=session), self.assertRaisesRegex(reader.ReadRejected, "physical_predispatch_PNG_role_bound"):
                self.observe(data)

    def test_partial_session_cannot_fall_back_to_individual(self):
        data = contract(session=True)
        data[1]["members"] = [r for r in data[1]["members"] if r["relative_path"] != "session-case.original.json"]
        with self.assertRaisesRegex(reader.ReadRejected, "physical_partial_session_originals"):
            self.observe(data)

    def test_session_namespace_requires_all_four_originals(self):
        data = contract(session=True)
        data[1]["members"] = [r for r in data[1]["members"] if r["relative_path"] not in serial.SESSION_MEMBERS]
        with self.assertRaisesRegex(reader.ReadRejected, "physical_session_namespace_without_originals"):
            self.observe(data)

    def test_mutated_original_or_unpinned_member_is_rejected(self):
        for changed in ("bytes", "hash", "role"):
            data = contract(session=True)
            row = next(r for r in data[1]["members"] if r["relative_path"] == "session-case.original.json")
            if changed == "bytes":
                raw = data[0].values["control/session-case.original.json"]
                data[0].values["control/session-case.original.json"] = raw.replace(b'"index":8', b'"index":7')
            elif changed == "hash":
                row["expected_sha256"] = None
            else:
                row["role"] = "native-observations"
            with self.subTest(changed=changed), self.assertRaises(reader.ReadRejected):
                self.observe(data)

    def test_request_clock_digest_is_not_inferred_from_selection(self):
        data = contract(session=False)
        name = "control/clock.original.json"
        clock = json.loads(data[0].values[name])
        clock["request_sha256"] = "0" * 64
        raw = serial.canonical(clock)
        data[0].values[name] = raw
        row = next(r for r in data[1]["members"] if r["relative_path"] == "clock.original.json")
        row["maximum"], row["expected_sha256"] = len(raw), reader.sha(raw)
        with self.assertRaisesRegex(reader.ReadRejected, "physical_original_request_clock_plan_binding"):
            self.observe(data)

    def test_original_clock_shape_boot_and_duration_are_preserved_offline(self):
        for field, value in (("boot_id", "foreign"), ("started_monotonic", -1),
                             ("deadline_monotonic", 999999.0), ("extra", True)):
            data = contract(session=False)
            name = "control/clock.original.json"
            clock = json.loads(data[0].values[name])
            clock[field] = value
            raw = serial.canonical(clock)
            data[0].values[name] = raw
            row = next(r for r in data[1]["members"] if r["relative_path"] == "clock.original.json")
            row["maximum"], row["expected_sha256"] = len(raw), reader.sha(raw)
            with self.subTest(field=field), self.assertRaises(reader.ReadRejected):
                self.observe(data)

    def test_namespace_mismatch_is_rejected_by_original_session_validator(self):
        data = list(contract(session=True))
        data[3] += "0"
        with self.assertRaisesRegex(serial.SessionRejected, "fixedtxt-session-namespace-full-digest-binding"):
            self.observe(data)

    def test_missing_original_png_is_rejected(self):
        data = contract(session=True)
        data[1]["members"].remove(next(r for r in data[1]["members"] if r["role"] == "native-png"))
        with self.assertRaisesRegex(reader.ReadRejected, "physical_predispatch_PNG_role_bound"):
            self.observe(data)

    def test_png_over_the_validated_session_bound_never_reaches_decoder(self):
        limit = self.observe(contract(session=True))["png_limit_bytes"]
        with self.assertRaisesRegex(reader.ReadRejected, "physical_decoder_stdin_bound"):
            reader.decode_digest(b"x" * (limit + 1), None, None, None, limit)

    def test_duplicate_member_cannot_replace_its_original_pin(self):
        data = contract(session=True)
        data[1]["members"].append(copy.deepcopy(data[1]["members"][0]))
        with self.assertRaisesRegex(reader.ReadRejected, "physical_original_plan_duplicate_member"):
            self.observe(data)


class TypedSourceContracts(unittest.TestCase):
    def test_exact_current_sources_preserve_unknown_and_reject_foreign_source(self):
        for arm in ("RED", "GREEN"):
            with self.subTest(arm=arm):
                for source in (typed.OBSERVED[arm], typed.CURRENT_OBSERVED[arm]):
                    value, missing = typed.actual_layout({}, arm=arm, source_sha=source)
                    self.assertIsNone(value)
                    self.assertEqual(missing, list(typed.FIELDS))
                    other_arm = "GREEN" if arm == "RED" else "RED"
                    with self.assertRaisesRegex(typed.ProbeRejected, "typed_probe_exact_observer_source"):
                        typed.actual_layout({}, arm=other_arm, source_sha=source)
                with self.assertRaisesRegex(typed.ProbeRejected, "typed_probe_exact_observer_source"):
                    typed.actual_layout({}, arm=arm, source_sha="0" * 64)


        legacy = (44617, typed.FIXTURE_HELPER_SHA)
        candidate = (44695, "e00805121f062abae5554a2419e26e53fde701f919b44b79c19a9a4eb6cd65c1")
        old_revision = CONTEXT["semwright_sha"]
        for revision, tree, pair, helper in (
            (old_revision, "c" * 40, typed.OBSERVED, legacy),
            (typed.CURRENT_SDK, typed.CURRENT_SDK_TREE, typed.CURRENT_OBSERVED, candidate),
        ):
            with self.subTest(revision=revision):
                self.assertEqual(self.compiler_binding(revision, tree, pair, helper), helper[1])
                crossed_helper = legacy if helper == candidate else candidate
                with self.assertRaisesRegex(typed.ProbeRejected, "typed_geometry_original_fixture_helper_source"):
                    self.compiler_binding(revision, tree, pair, crossed_helper)
                crossed_pair = dict(pair)
                other = typed.OBSERVED if pair == typed.CURRENT_OBSERVED else typed.CURRENT_OBSERVED
                crossed_pair["GREEN"] = other["GREEN"]
                with self.assertRaisesRegex(typed.ProbeRejected, "typed_geometry_current_fixture_observer_pair"):
                    self.compiler_binding(revision, tree, crossed_pair, helper)

    def compiler_binding(self, revision, tree, pair, helper):
        fixture = "flow-text-control"
        common_path = "inputs/common-sources.original.json"
        values = {
            common_path: serial.canonical({
                "schema": "motionwright.fixed-text-common-compiler-sources/1",
                "semwright_revision": revision,
                "files": [{"path": route.FIXTURE_HELPER, "bytes": helper[0], "sha256": helper[1]}],
            }),
            "inputs/film.json": b"synthetic Film original",
            "inputs/expectations.json": b"synthetic fixture expectations original",
        }

        def descriptor(path):
            return serial.descriptor(values[path])

        read = ContractOriginals(values).small

        def member_json(path, maximum):
            return route.strict(read(path, maximum), maximum)

        values["inputs/cycle.json"] = serial.canonical({
            "schema": "motionwright.fixed-text-native-cycle-inputs/4", "arm": "RED",
            "common_sources": descriptor(common_path),
        })
        roster = [{"key": name} for name in sorted(typed.FIXTURE_ROSTER)]
        selected = next(row for row in roster if row["key"] == fixture)
        selected["film"] = {"path": f"fixtures/{fixture}/film.original.json", **descriptor("inputs/film.json")}
        selected["expectations"] = {"path": f"fixtures/{fixture}/expectations.json",
                                    **descriptor("inputs/expectations.json")}
        values["inputs/fixture-set.json"] = serial.canonical({
            "schema": "motionwright-fixedtxt-film-fixture-set/4", "fixture_count": 12,
            "compiler": {"runtime_source_sha256": pair["RED"]}, "fixtures": roster,
        })
        plan = {
            "arm": "RED", "fixture": fixture, "context": {"semwright_sha": revision},
            "arm_sources": {arm: {"source_sha": revision, "tree_sha": tree, "native_source_sha256": digest}
                            for arm, digest in pair.items()},
            "members": [{"role": role, "root": "inputs", "relative_path": path}
                        for role, path in (("cycle-inputs", "cycle.json"), ("fixture-set", "fixture-set.json"))],
        }
        originals = SimpleNamespace(small=read, json=member_json, descriptor=descriptor)
        bound = typed.bind_fixture_sources(
            originals, plan, "inputs/film.json", "inputs/expectations.json", pair["RED"], route)
        return bound["fixture_helper_source_sha256"]


class ReadbackOutputOwnershipTests(unittest.TestCase):
    """Real directory ownership and immutable receipts; no Native operation."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()

        def inspect(path, *, directory=False):
            if path.is_symlink() or (directory and not path.is_dir()):
                raise ValueError("test_path_rejected")

        def new_bytes(path, raw, **_):
            with path.open("xb") as stream:
                stream.write(raw)

        self.base = SimpleNamespace(
            absolute_e=lambda value, **_: Path(value),
            inspect_path=inspect, disk_admission=lambda *_, **__: None,
            json_bytes=lambda value: json.dumps(value, sort_keys=True).encode(),
            new_bytes=new_bytes)

    def test_existing_partial_output_cannot_receive_another_terminal(self):
        path = self.root / "existing"
        path.mkdir()
        original = path / "previous.started.json"
        original.write_bytes(b"previous original")
        owner = None
        with self.assertRaisesRegex(reader.ReadRejected, "new_private_output_required"):
            owner = reader.ReadbackOutput(self.base, path, self.source, {})
        self.assertIsNone(owner)
        self.assertEqual({p.name: p.read_bytes() for p in path.iterdir()},
                         {"previous.started.json": b"previous original"})

    def test_lost_directory_creation_race_cannot_acquire_the_winner(self):
        path = self.root / "race"
        real_mkdir = Path.mkdir

        def competing_mkdir(candidate, *args, **kwargs):
            real_mkdir(candidate, *args, **kwargs)
            (candidate / "winner.original").write_bytes(b"winner")
            raise FileExistsError(str(candidate))

        owner = None
        with patch.object(Path, "mkdir", competing_mkdir), self.assertRaises(FileExistsError):
            owner = reader.ReadbackOutput(self.base, path, self.source, {})
        self.assertIsNone(owner)
        self.assertEqual({p.name: p.read_bytes() for p in path.iterdir()},
                         {"winner.original": b"winner"})

    def test_owned_failure_receipt_is_exclusive_and_preserves_started_original(self):
        path = self.root / "own"
        owner = reader.ReadbackOutput(self.base, path, self.source, {})
        started = {"status": "STARTED", "native_calls": 0}
        failed = {"status": "PHYSICAL_READBACK_INCOMPLETE", "native_calls": 0}
        owner.write("readback.started.json", started)
        owner.write("readback.terminal.json", failed)
        with self.assertRaises(FileExistsError):
            owner.write("readback.terminal.json", {"status": "replacement"})
        self.assertEqual(json.loads((path / "readback.started.json").read_bytes()), started)
        self.assertEqual(json.loads((path / "readback.terminal.json").read_bytes()), failed)

    def test_replaced_owned_directory_cannot_receive_a_terminal(self):
        path = self.root / "replace"
        owner = reader.ReadbackOutput(self.base, path, self.source, {})
        original = self.root / "retained"
        path.rename(original)
        path.mkdir()
        with self.assertRaisesRegex(reader.ReadRejected, "readback_output_identity_changed"):
            owner.write("readback.terminal.json", {"status": "incomplete"})
        self.assertEqual(list(path.iterdir()), [])
        self.assertEqual(list(original.iterdir()), [])



class PreparationOutputOwnershipTests(unittest.TestCase):
    """Exercise real output ownership without creating a Native permission."""

    def setUp(self):
        ReadbackOutputOwnershipTests.setUp(self)

        def read_stable(path, maximum):
            raw = path.read_bytes()
            if len(raw) > maximum:
                raise ValueError("test_read_bound")
            return raw

        self.base.read_stable = read_stable

    def test_preexisting_or_lost_creation_race_never_acquires_foreign_output(self):
        existing = self.root / "existing-preparation"
        existing.mkdir()
        (existing / "retained.original").write_bytes(b"previous")
        owner = None
        with self.assertRaisesRegex(preparation.ReviewRejected, "new_private_output_required"):
            owner = preparation.PreparationOutput(self.base, existing, self.source, {})
        self.assertIsNone(owner)
        self.assertEqual({p.name: p.read_bytes() for p in existing.iterdir()},
                         {"retained.original": b"previous"})
        path = self.root / "preparation-race"
        real_mkdir = Path.mkdir

        def competing_mkdir(candidate, *args, **kwargs):
            real_mkdir(candidate, *args, **kwargs)
            (candidate / "winner.original").write_bytes(b"winner")
            raise FileExistsError(str(candidate))

        with patch.object(Path, "mkdir", competing_mkdir), self.assertRaises(FileExistsError):
            owner = preparation.PreparationOutput(self.base, path, self.source, {})
        self.assertIsNone(owner)
        self.assertEqual({p.name: p.read_bytes() for p in path.iterdir()},
                         {"winner.original": b"winner"})

    def test_replaced_directory_cannot_receive_permission_or_failure_receipt(self):
        path = self.root / "preparation-replaced"
        owner = preparation.PreparationOutput(self.base, path, self.source, {})
        retained = self.root / "preparation-original"
        path.rename(retained)
        path.mkdir()
        for name in preparation.PreparationOutput.NAMES:
            with self.subTest(name=name), self.assertRaisesRegex(
                    preparation.ReviewRejected, "preparation_output_identity_changed"):
                owner.write_raw(name, b"offline fixture only")
        with self.assertRaisesRegex(preparation.ReviewRejected, "preparation_output_identity_changed"):
            owner.read_dispatch()
        self.assertEqual(list(path.iterdir()), [])
        self.assertEqual(list(retained.iterdir()), [])

    def test_dispatch_is_exact_exclusive_and_closed_to_unexpected_names(self):
        path = self.root / "preparation-dispatch"
        owner = preparation.PreparationOutput(self.base, path, self.source, {})
        raw = b'{"offline_fixture":true,"native_calls":0}'
        owner.write_raw("dispatch.original.json", raw)
        self.assertEqual(owner.read_dispatch(), raw)
        with self.assertRaises(FileExistsError):
            owner.write_raw("dispatch.original.json", b"replacement")
        for name in ("unrelated.json", "../escaped.json", "nested/dispatch.original.json"):
            with self.subTest(name=name), self.assertRaisesRegex(
                    preparation.ReviewRejected, "preparation_output_name"):
                owner.write_raw(name, b"unexpected")
        self.assertEqual({p.name: p.read_bytes() for p in path.iterdir()},
                         {"dispatch.original.json": raw})

    def test_owned_failure_preserves_started_and_never_replaces_terminal(self):
        path = self.root / "preparation-failure"
        owner = preparation.PreparationOutput(self.base, path, self.source, {})
        started = {"status": "STARTED", "native_calls": 0}
        failed = {"status": "PREPARATION_REVIEW_INCOMPLETE", "R28_issued": False}
        owner.write("review.started.json", started)
        owner.write("review.terminal.json", failed)
        with self.assertRaises(FileExistsError):
            owner.write("review.terminal.json", {"status": "replacement"})
        self.assertEqual(json.loads((path / "review.started.json").read_bytes()), started)
        self.assertEqual(json.loads((path / "review.terminal.json").read_bytes()), failed)

    def test_physical_reader_pins_exact_preparation_bytes_before_import(self):
        path = Path(preparation.__file__)
        raw = path.read_bytes()
        self.assertEqual(reader.sha(raw), reader.OUTER_SHA)
        loaded = reader.load(path, reader.OUTER_SHA, "preparation_dependency_fixture")
        self.assertTrue(callable(loaded.verify_preparation))
        changed = self.root / "changed-preparation.py"
        changed.write_bytes(raw + b"\nraise AssertionError('must never execute')\n")
        with self.assertRaisesRegex(reader.ReadRejected, "physical_reader_dependency_digest"):
            reader.load(changed, reader.OUTER_SHA, "changed_dependency_fixture")
class DecoderNamespaceTests(unittest.TestCase):
    # Two original Windows observations differed only in directory st_size.
    # Trace SHA-256: 240c925e0752ec3ac0fa07a2f309d42c0dcf23638dd5f7e4d6999fdfa7ef21cd
    ROWS = (
        (11699299553584006063, 281474979259182, 16895, 1, 0,
         1792463341308406100, 1792256567244601900, 16),
        (11699299553584006063, 3659174699789933, 33279, 1, 87638016,
         1788887299039865900, 1792258113047420000, 32),
    )

    def compare(self, rows, platform='nt'):
        return reader.decoder_namespace_identity(rows, platform=platform)

    def test_observed_directory_size_change_is_windows_only_and_nonmutating(self):
        before = [list(row) for row in self.ROWS]
        after = copy.deepcopy(before)
        after[0][4] = 65536
        retained = copy.deepcopy((before, after))
        self.assertEqual(self.compare(before), self.compare(after))
        self.assertNotEqual(self.compare(before, 'posix'), self.compare(after, 'posix'))
        self.assertEqual((before, after), retained)
        self.assertEqual(self.compare(before)[-1], self.ROWS[-1])

    def test_every_other_directory_identity_field_remains_exact(self):
        for index in (0, 1, 2, 3, 5, 6, 7):
            rows = [list(row) for row in self.ROWS]
            rows[0][index] ^= 1
            with self.subTest(field=index):
                self.assertNotEqual(self.compare(rows), self.compare(self.ROWS))

    def test_all_regular_file_fields_including_size_remain_exact(self):
        for index in range(8):
            rows = [list(row) for row in self.ROWS]
            rows[-1][index] ^= 1
            with self.subTest(field=index):
                if index == 3:
                    with self.assertRaisesRegex(reader.ReadRejected, 'physical_decoder_namespace_kind'):
                        self.compare(rows)
                else:
                    self.assertNotEqual(self.compare(rows), self.compare(self.ROWS))

    def test_redirects_wrong_kinds_hardlinks_and_malformed_chains_are_rejected(self):
        invalid = [[], [self.ROWS[-1]], self.ROWS * 33]
        for row_index, field, value in ((0, 2, 33279), (1, 2, 16895),
                                       (0, 2, 41471), (1, 3, 2),
                                       (0, 7, 0x400), (1, 7, 0x400),
                                       (0, 4, True)):
            rows = [list(row) for row in self.ROWS]
            rows[row_index][field] = value
            invalid.append(rows)
        invalid.append([self.ROWS[0][:-1], self.ROWS[-1]])
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(reader.ReadRejected):
                self.compare(rows)

    def test_decoder_view_retains_raw_sizes_and_cannot_inspect_other_paths(self):
        rows = [list(row) for row in self.ROWS]
        base = SimpleNamespace(inspect_path=lambda path: rows, identity=object())
        observed = []
        path = Path('ffmpeg.exe')
        view = reader.DecoderPathView(base, path, observed)
        view.inspect_path(path)
        rows[0][4] = 65536
        view.inspect_path(path)
        self.assertEqual([row['raw_namespace_identity'][0][4] for row in observed], [0, 65536])
        self.assertIs(view.identity, base.identity)
        self.assertEqual(base.inspect_path(path)[0][4], 65536)
        with self.assertRaisesRegex(reader.ReadRejected, 'physical_decoder_path_scope'):
            view.inspect_path(Path('foreign.exe'))
        with self.assertRaisesRegex(reader.ReadRejected, 'physical_decoder_path_scope'):
            view.inspect_path(path, directory=True)
        for _ in range(14):
            view.inspect_path(path)
        with self.assertRaisesRegex(reader.ReadRejected, 'physical_decoder_observation_bound'):
            view.inspect_path(path)


if __name__ == "__main__":
    unittest.main()
