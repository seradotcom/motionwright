"""Offline contract fixtures only; these tests never render or grant acceptance."""
import copy
import json
from pathlib import Path
import unittest

import native_fixedtxt_session as serial
import native_fixedtxt_workflow as route
import review_fixedtxt_result_matrix_gn1_bound as reader
import fixedtxt_typed_matrix_probe as typed

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
                value, missing = typed.actual_layout({}, arm=arm, source_sha=typed.OBSERVED[arm])
                self.assertIsNone(value)
                self.assertEqual(missing, list(typed.FIELDS))
                with self.assertRaisesRegex(typed.ProbeRejected, "typed_probe_exact_observer_source"):
                    typed.actual_layout({}, arm=arm, source_sha="0" * 64)


if __name__ == "__main__":
    unittest.main()
