"""Source tests with explicitly synthetic originals; never native evidence.

The filesystem and process mechanics below are real. Process tests use an
explicit single-process containment double, not a pretend cgroup or SDK Host.
"""
from __future__ import annotations

import copy
import base64
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

TOOLING = Path(__file__).resolve().parents[1]
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

import native_fixedtxt_g2_admission as fixed
import native_fixedtxt_g2_supervisor as supervisor
import native_evidence_projection as core


def joined_originals():
    """Synthetic joined bytes; intentionally not a pixel/render fixture."""
    fixture = "flow-control-base"
    plan = {"arm": "RED", "fixture": fixture,
            "arm_sources": {"RED": {"native_source_sha256": "e" * 64}}}
    base = "observation/" + fixture + "/"
    host = base + "native-result.host-stdin/"
    config = {"renderInputDigest": "1" * 64, "fontResourcesDigest": "2" * 64}
    helper = b"// Synthetic helper, never executed.\n"
    stdin = b"globalThis.__SEMWRIGHT_RENDER_INPUT__ = " + fixed.canonical(config).rstrip(b"\n") + b";\n" + helper
    provider = {"id": "driver:motion-canvas", "kind": "driver", "version": "synthetic-1"}
    capture = {"provider": provider, "provider_process_id": 45, "tool": "motion-node",
        "job_id": "synthetic-host-job", "request_id": "synthetic-host-request",
        "parent_request_id": "synthetic-broker-parent", "session_id": "synthetic-host-session",
        "stdin_bytes": len(stdin), "stdin_sha256": fixed.sha(stdin), "terminal_file_required": True}
    terminal = {"write_state": "write_all_completed", "partial_delivery_possible": False,
        "io_error_kind": None, "raw_os_error": None, "child_consumption": "not_observed",
        "native_render_success": "not_inferred"}
    join = {"matched": True, "ordinal": 1, "capture": capture, "terminal": terminal,
        "pipe_write_all_completed": True, "child_consumption": "not_observed",
        "native_render_success": "not_inferred",
        "joined_to": "original completed render-start Broker parent and persisted session",
        "Host_job_id_equals_MC_job_ref_claimed": False}
    manifest = {"frames": [{"index": index, "file": f"frames/{index:06d}.png",
        "bytes": 3, "sha256": fixed.sha(b"png")} for index in range(180)],
        "native_observations": {"render_input_digest": config["renderInputDigest"],
                                "font_resources_sha256": config["fontResourcesDigest"]}}
    manifest_raw = fixed.canonical(manifest)
    artifact = {"directory": "actual-result-suffix", "frame_count": 180,
                "manifest_bytes": len(manifest_raw), "manifest_sha256": fixed.sha(manifest_raw)}
    native = {"schema": "motionwright.native.fixed-text-render-result/4", "arm": "RED",
        "fixture": fixture, "frame_count": 180, "native_adapter_sha256": "e" * 64,
        "project_resource": "project:11111111-1111-4111-8111-111111111111", "generation": 2, "revision": 3,
        "segments": [{"frame_count": 180, "job_ref": "synthetic-mc-job", "artifact": artifact}],
        "native_render_result": {"replayed": False, "result": {"provenance_checked": True,
            "data": {"state": "succeeded", "job_ref": "synthetic-mc-job", "artifact": artifact}}}}
    receipt = {"project_id": "11111111-1111-4111-8111-111111111111", "generation": 2, "revision": 3,
        "request_id": "fixed-text-v4-red-" + fixture + ":motion:0:render-start", "stage": "completed",
        "command": "driver.motion-canvas.render.start", "payload": {
            "broker_request_id": capture["parent_request_id"], "result": {"provenance_checked": True,
                "execution": {"provenance": {"provider": provider["id"], "source": provider["kind"],
                    "provider_version": provider["version"]}}}}}
    values = {
        base + "native-result.json": native,
        host + "native-return.snapshot.json": {"schema": "motionwright.private-native-return-snapshot/1",
            "native_result": "returned_success", "wire_bytes": False, "automatic_retry": False, "value": native},
        host + "capture-0001.json": capture,
        host + "capture-0001.terminal.json": terminal,
        host + "session.original.json": {"schema": "semwright.host-stdin-custody/1", "provider": provider,
            "tool": "motion-node", "driver_sha256": "3" * 64, "tool_sha256": "4" * 64},
        host + "broker-receipt.snapshot.json": {"schema": "motionwright.private-production-receipt-snapshot/1",
            "source": "existing StudioService receipt", "wire_bytes": False, "lookup_error": None,
            "snapshot_grants_authority": False, "receipt": receipt},
        host + "readback.observation.json": {"schema": "motionwright.private-host-stdin-readback/1",
            "native_success_returned": True, "native_return_snapshot_retained": True,
            "original_identity_unchanged": True, "new_capture_ordinals_retained": [1], "capture_read_errors": [],
            "identity_error": None, "native_return_snapshot_error": None, "receipt_lookup_error": None,
            "receipt_retention_error": None, "selected_capture_join": join},
        base + "native-result.fixed-text/host-binding.observation.json": {
            "schema": "motionwright.private-fixed-text-original-host-binding/4", "arm": "RED", "fixture": fixture,
            "original_sources_joined": True, "selected_capture_join": join,
            "selected_pipe_write_all_completed": True, "original_Host_stdin_sha256": fixed.sha(stdin),
            "original_artifact_manifest_sha256": fixed.sha(manifest_raw), "original_config": config},
    }
    originals = {key: fixed.canonical(value) for key, value in values.items()}
    originals[host + "capture-0001.stdin"] = stdin
    originals[base + "fixed-text-original-readback/artifact-manifest.json"] = manifest_raw
    cycle = fixed.canonical({"runtime": {"driver_sha256": "3" * 64, "node_sha256": "4" * 64,
                                         "render_helper": fixed.descriptor(helper)}})
    return plan, originals, cycle


class JoinedOriginalTests(unittest.TestCase):
    def test_directory_is_joined_to_originals_and_host_mc_ids_stay_distinct(self):
        plan, originals, cycle = joined_originals()
        value = fixed.resolve_native_directory(plan, originals, cycle_raw=cycle)
        self.assertEqual(value["physical_relative_directory"], "output/actual-result-suffix")
        self.assertEqual(value["archive_slot"], fixed.NATIVE_SLOT)
        self.assertNotEqual(value["host_job_id"], value["motion_canvas_job_ref"])
        self.assertEqual(value["frame_count"], 180)
        self.assertIs(value["native_success_verified"], False)

    def test_missing_original_or_parent_mismatch_cannot_resolve_a_slot(self):
        plan, originals, cycle = joined_originals()
        path = "observation/flow-control-base/native-result.host-stdin/broker-receipt.snapshot.json"
        absent = dict(originals); del absent[path]
        with self.assertRaises(KeyError):
            fixed.resolve_native_directory(plan, absent, cycle_raw=cycle)
        receipt = json.loads(originals[path]); receipt["receipt"]["payload"]["broker_request_id"] = "other-parent"
        originals[path] = fixed.canonical(receipt)
        with self.assertRaisesRegex(fixed.FixedTxtRejected, "original-render-parent"):
            fixed.resolve_native_directory(plan, originals, cycle_raw=cycle)

    def test_changed_stdin_helper_and_adapter_never_resolve(self):
        for kind in ("stdin", "helper", "adapter"):
            with self.subTest(kind=kind):
                plan, originals, cycle = joined_originals()
                if kind == "stdin":
                    originals["observation/flow-control-base/native-result.host-stdin/capture-0001.stdin"] += b"x"
                elif kind == "helper":
                    parsed = json.loads(cycle); parsed["runtime"]["render_helper"]["sha256"] = "0" * 64
                    cycle = fixed.canonical(parsed)
                else:
                    plan["arm_sources"]["RED"]["native_source_sha256"] = "0" * 64
                with self.assertRaises(fixed.FixedTxtRejected):
                    fixed.resolve_native_directory(plan, originals, cycle_raw=cycle)

    def test_traversal_replayed_and_incomplete_frame_roster_are_rejected(self):
        for mutation in ("directory", "replay", "frames"):
            with self.subTest(mutation=mutation):
                plan, originals, cycle = joined_originals()
                base = "observation/flow-control-base/"
                native = json.loads(originals[base + "native-result.json"])
                if mutation == "directory":
                    native["segments"][0]["artifact"]["directory"] = "../foreign"
                    native["native_render_result"]["result"]["data"]["artifact"]["directory"] = "../foreign"
                elif mutation == "replay":
                    native["native_render_result"]["replayed"] = True
                else:
                    native["frame_count"] = 179
                originals[base + "native-result.json"] = fixed.canonical(native)
                returned = json.loads(originals[base + "native-result.host-stdin/native-return.snapshot.json"])
                returned["value"] = native
                originals[base + "native-result.host-stdin/native-return.snapshot.json"] = fixed.canonical(returned)
                with self.assertRaises(fixed.FixedTxtRejected):
                    fixed.resolve_native_directory(plan, originals, cycle_raw=cycle)


def external_fixture():
    """Build finite synthetic input bytes using the real source validators.

    No source catalogue row is patched and no fixture is published as review.
    """
    import native_film180_evidence_selection as film
    import native_mlt_g2_observation as observed
    import native_mlt_g2_custody as custody
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    current = {"source_sha": "a" * 40, "semwright_sha": observed.SEMWRIGHT_PIN,
               "run_id": "34567", "run_attempt": "1"}
    layout = fixed.owner_layout(current=current, runner_temp="/synthetic-private-runner")
    arms = {"RED": {"source_sha": current["semwright_sha"], "tree_sha": "e" * 40,
                     "native_source_sha256": "e" * 64},
            "GREEN": {"source_sha": "b" * 40, "tree_sha": "f" * 40,
                       "native_source_sha256": "f" * 64}}
    cycle = {"schema": "motionwright.fixed-text-native-cycle-inputs/4", "arm": "RED", "decoder_sha256": "d" * 64,
             "runtime": {"semwright_revision": current["semwright_sha"],
                         "native_source_sha256": {arm: row["native_source_sha256"] for arm, row in arms.items()}}}
    cycle_raw = fixed.canonical(cycle)
    mandatory = film.fixedtxt_required_members("flow-control-base", fixed.NATIVE_SLOT)
    members = []
    for (root, path), role in mandatory.items():
        bound = film.FIXEDTXT_RGBA_BYTES if role == "selected-rgba" else (
            512 * 1024 if role == "selection-plan" else 32768)
        members.append({"root": root, "relative_path": path, "role": role, "maximum": bound,
                        "expected_sha256": fixed.sha(cycle_raw) if role == "cycle-inputs" else None})
    members.extend([
        {"root": "inputs", "relative_path": fixed.RUNTIME_MEMBER, "role": "input-original", "maximum": 65536,
         "expected_sha256": None},
        {"root": "inputs", "relative_path": fixed.CYCLE_MEMBER, "role": "input-original", "maximum": 1024 * 1024,
         "expected_sha256": fixed.sha(cycle_raw)},
        {"root": "observation", "relative_path": "flow-control-base/native-result.host-stdin/broker-receipt.snapshot.json",
         "role": "attempt-original", "maximum": 8 * 1024 * 1024, "expected_sha256": None},
    ])
    for suffix, bound in (("intent.json", 65536), ("spawned.json", 65536), ("stdout", 1024 * 1024 + 1),
                          ("stderr", 1024 * 1024 + 1), ("terminal.json", 65536)):
        members.append({"root": "control", "relative_path": "supervision/work." + suffix,
                        "role": "attempt-original", "maximum": bound, "expected_sha256": None})
    plan = {"schema": fixed.PLAN_SCHEMA, "scope": fixed.SCOPE, "context": current, "arm": "RED",
        "fixture": "flow-control-base", "case_roster": film.fixedtxt_case_roster(), "arm_sources": arms,
        "source_bindings": [{"path": path, "sha256": "c" * 64} for path in fixed.source_paths()],
        "cycle_input_sha256": fixed.sha(cycle_raw), "decoder_sha256": "d" * 64,
        "native_directory": fixed.NATIVE_SLOT, "native_directory_selector": fixed.SELECTOR,
        "owner_count": 1, "expected_fixture_count": 1, "default_fixture_count": 12, "render_frames": 180,
        "selected_frames": list(film.fixedtxt_frames("flow-control-base")),
        "limits": {"capsule_bytes": 128 * 1024 * 1024, "files": 512, "attempt_seconds": 600, "custody_seconds": 120},
        "members": sorted(members, key=lambda row: (row["root"], row["relative_path"]))}
    plan_raw = fixed.canonical(plan)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    recipient = {"spki_der_base64": base64.b64encode(der).decode(), "sha256": fixed.sha(der)}
    runtime = {name: "synthetic-not-installed" for name in observed.RUNTIME_INPUT_KEYS}
    runtime.update(schema="motionwright.native.fixedtxt-v4-g2-inputs/1", private_evidence_dir=layout["observation_root"],
        motionwright_sha=current["source_sha"], semwright_sha=current["semwright_sha"], run_id=current["run_id"],
        run_attempt="1", fixedtxt_admission_id="synthetic-source-test", recipient_sha256=recipient["sha256"],
        fixedtxt_plan={"path": layout["plan"], **fixed.descriptor(plan_raw)})
    runtime_raw = fixed.canonical(runtime)
    artifact = {"artifact_id": 456, "run_id": 234, "run_attempt": 1, "source_sha": "b" * 40, "zip_sha256": "b" * 64}
    dispatch = {"schema": fixed.DISPATCH_SCHEMA, "motionwright_sha": current["source_sha"],
        "semwright_sha": current["semwright_sha"], "recipient": recipient,
        "official_runtime": {"artifact": artifact, "receipt_sha256": "1" * 64,
                             "known_original_module_sha256": "2" * 64, "known_melt_sha256": "3" * 64},
        "compiler": {"artifact": artifact, "source_mode": custody.SOURCE_MODE,
            "members": {"compiled_module": "synthetic.so", "compiler_receipt_path": "synthetic-receipt.json"}},
        "fixedtxt": {"schema": fixed.COMMITMENT_SCHEMA, "admission_id": "synthetic-source-test", "context": current,
            "scope": fixed.SCOPE, "arm": "RED", "fixture": "flow-control-base", "single_owner": 1,
            "plan_original": fixed.descriptor(plan_raw), "runtime_original": fixed.descriptor(runtime_raw),
            "cycle_original": fixed.descriptor(cycle_raw), "wrapper_sha256": "c" * 64, "limits": plan["limits"]}}
    return current, plan_raw, runtime_raw, cycle_raw, fixed.canonical(dispatch)


class ExternalAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = external_fixture()

    def admit(self, values=None):
        current, plan, runtime, cycle, dispatch = values or self.fixture
        return fixed.admit_inputs(dispatch, plan, runtime, cycle, current=current,
                                  runner_temp="/synthetic-private-runner")

    def test_external_commitment_breaks_self_hash_without_populating_catalogue(self):
        import native_mlt_evidence_selection as selected
        held = self.admit()
        self.assertIsInstance(held, fixed.HeldInputs)
        self.assertEqual(selected.FIXEDTXT_ATTEMPTS, {})
        self.assertIsNone(selected.FIXEDTXT_REVIEWED_WRAPPER_SHA256)
        with self.assertRaises(Exception):
            selected.fixedtxt_owner_selection_guard()

    def test_any_changed_p_r_q_buffer_is_rejected_before_json_semantics(self):
        for index in (1, 2, 3):
            with self.subTest(original=index):
                values = list(self.fixture); values[index] += b" "
                with self.assertRaisesRegex(fixed.FixedTxtRejected, "independent-"):
                    self.admit(values)

    def test_wrong_run_fixture_recipient_or_limit_cannot_borrow_originals(self):
        for mutation in ("run", "fixture", "recipient", "limit"):
            with self.subTest(mutation=mutation):
                values = list(self.fixture); dispatch = json.loads(values[4])
                if mutation == "run":
                    dispatch["fixedtxt"]["context"]["run_id"] = "34568"
                elif mutation == "fixture":
                    dispatch["fixedtxt"]["fixture"] = "fixed-causal"
                elif mutation == "recipient":
                    dispatch["recipient"]["sha256"] = "0" * 64
                else:
                    dispatch["fixedtxt"]["limits"]["custody_seconds"] = True
                values[4] = fixed.canonical(dispatch)
                with self.assertRaises((ValueError, AssertionError)):
                    self.admit(values)

    def test_plan_requires_source_closure_all180_and_supervisor_originals(self):
        current, raw, *_ = self.fixture
        for mutation in ("helper", "png", "terminal"):
            plan = json.loads(raw)
            if mutation == "helper":
                plan["source_bindings"] = plan["source_bindings"][:-1]
            else:
                key = "native-png" if mutation == "png" else "attempt-original"
                index = next(i for i, row in enumerate(plan["members"])
                             if row["role"] == key and (mutation != "terminal" or row["relative_path"].endswith("work.terminal.json")))
                del plan["members"][index]
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, AssertionError)):
                fixed.validate_plan(plan, current=current)

    def test_standalone_external_wrapper_denies_before_nonexistent_input_reads(self):
        current, _, _, _, dispatch = self.fixture
        env = {"GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Linux",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": current["source_sha"], "GITHUB_RUN_ID": current["run_id"],
            "MW_MLT_SCOPE": fixed.SCOPE, "MW_MLT_G2_ADMISSION": dispatch.decode(),
            "RUNNER_TEMP": "/synthetic-private-runner", "GITHUB_WORKSPACE": "/synthetic-source"}
        layout = fixed.owner_layout(current=current, runner_temp=env["RUNNER_TEMP"])
        with self.assertRaisesRegex(supervisor.SupervisionRejected, "work-containment-required"):
            fixed.acquire_wrapper_inputs(env, runtime_input_path=layout["runtime"], cycle_input_path=layout["cycle"],
                selected_fixture="flow-control-base", source_root=env["GITHUB_WORKSPACE"])


class ActualRootHandlesTests(unittest.TestCase):
    def test_same_buffer_read_empty_original_and_alias_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); root.chmod(0o700)
            (root / "original.bin").write_bytes(b"original")
            (root / "empty.bin").write_bytes(b"")
            handles = core.RootHandles({"control": str(root)})
            try:
                raw, identity = handles.read("control", ("original.bin",), 32)
                (root / "original.bin").write_bytes(b"replacement")
                self.assertEqual(raw, b"original")
                self.assertEqual(len(identity), 9)
                self.assertEqual(handles.read("control", ("empty.bin",), 0)[0], b"")
                os.link(root / "original.bin", root / "hard-link.bin")
                os.symlink(root / "empty.bin", root / "redirect.bin")
                for name in ("original.bin", "hard-link.bin", "redirect.bin"):
                    with self.subTest(name=name), self.assertRaises((OSError, core.SelectionError)):
                        handles.read("control", (name,), 32)
            finally:
                handles.close()

    def test_unknown_owner_retains_inputs_and_work_streams_through_real_handles(self):
        """Real temporary Git/filesystem, synthetic data, no native invocation."""
        import io
        import tarfile
        _, p, r, q, external = external_fixture()
        plan, runtime, dispatch = map(json.loads, (p, r, external))
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / "synthetic-source"; source.mkdir()
            bindings = []
            for name in fixed.source_paths():
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
                raw = ("Explicit synthetic source binding fixture: " + name + "\n").encode()
                path.write_bytes(raw)
                bindings.append({"path": name, "sha256": fixed.sha(raw)})
            def git(*args):
                return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.PIPE).decode().strip()
            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=Source Fixture", "-c", "user.email=source-fixture@example.invalid",
                "commit", "-qm", "Synthetic source fixture only")
            current = {**plan["context"], "source_sha": git("rev-parse", "HEAD")}
            layout = fixed.owner_layout(current=current, runner_temp=str(temp))
            plan.update(context=current, source_bindings=bindings)
            p = fixed.canonical(plan)
            runtime.update(motionwright_sha=current["source_sha"], private_evidence_dir=layout["observation_root"],
                fixedtxt_plan={"path": layout["plan"], **fixed.descriptor(p)})
            r = fixed.canonical(runtime)
            dispatch.update(motionwright_sha=current["source_sha"])
            dispatch["fixedtxt"].update(context=current, plan_original=fixed.descriptor(p),
                runtime_original=fixed.descriptor(r), wrapper_sha256=next(row["sha256"] for row in bindings
                    if row["path"] == "tooling/native_film180_original_g2.py"))
            held = fixed.admit_inputs(fixed.canonical(dispatch), p, r, q, current=current, runner_temp=str(temp))
            for root in (layout["control_root"], layout["inputs_root"]):
                Path(root).mkdir(mode=0o700)
            for path, raw in ((layout["plan"], p), (layout["runtime"], r), (layout["cycle"], q)):
                Path(path).write_bytes(raw)
            supervision = Path(layout["control_root"]) / "supervision"; supervision.mkdir(mode=0o700)
            for row in plan["members"]:
                if row["root"] == "control" and row["relative_path"].startswith("supervision/"):
                    (Path(layout["control_root"]) / row["relative_path"]).write_bytes(b"unknown mechanical original\n")
            raw, manifest = fixed.capture_selected(held, current=current, runner_temp=str(temp),
                source_root=str(source), roots={"control": layout["control_root"], "inputs": layout["inputs_root"],
                    "observation": layout["observation_root"], "owner": None})
            self.assertIs(manifest["planned_originals_complete"], False)
            self.assertIs(manifest["native_success_verified"], False)
            self.assertIsNone(manifest["directory_resolution"])
            self.assertGreater(len(manifest["failures"]), 180)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
                self.assertEqual(archive.extractfile("inputs/" + fixed.CYCLE_MEMBER).read(), q)
                self.assertEqual(archive.extractfile("control/supervision/work.stderr").read(),
                                 b"unknown mechanical original\n")
            with self.assertRaisesRegex(fixed.FixedTxtRejected, "complete-snapshot-roster"):
                fixed.verify_snapshot(raw, held, current=current, runner_temp=str(temp))

    def test_literal_stream_extension_keeps_legacy_exclusion_and_no_follow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); root.chmod(0o700)
            (root / "supervision").mkdir()
            (root / "supervision/work.stderr").write_bytes(b"")
            row = {"root": "control", "relative_path": "supervision/work.stderr",
                   "role": "attempt-original", "maximum": 1024 * 1024 + 1}
            handles = core.RootHandles({"control": str(root)})
            try:
                with self.assertRaisesRegex(core.SelectionError, "excluded-before-open"):
                    handles.read("control", ("supervision", "work.stderr"), row["maximum"])
                self.assertEqual(fixed._read_admitted_original(handles, row, row["relative_path"],
                    plan={"fixture": "flow-control-base"})[0], b"")
                foreign = {**row, "relative_path": "supervision/foreign.stderr"}
                (root / foreign["relative_path"]).write_bytes(b"not selected")
                with self.assertRaisesRegex(fixed.FixedTxtRejected, "closed-original-stream-roster"):
                    fixed._read_admitted_original(handles, foreign, foreign["relative_path"],
                        plan={"fixture": "flow-control-base"})
                (root / row["relative_path"]).unlink()
                os.symlink(root / foreign["relative_path"], root / row["relative_path"])
                with self.assertRaisesRegex(fixed.FixedTxtRejected, "regular-single-link"):
                    fixed._read_admitted_original(handles, row, row["relative_path"],
                        plan={"fixture": "flow-control-base"})
            finally:
                handles.close()


def complete_snapshot_fixture():
    """Synthetic original buffers exercising the actual selected-frame reader."""
    import native_film180_evidence_selection as film
    current, p, r, q, external = external_fixture()
    plan, runtime, cycle, dispatch = map(json.loads, (p, r, q, external))
    _, joined, joined_cycle = joined_originals()
    cycle["runtime"].update(json.loads(joined_cycle)["runtime"])
    q = fixed.canonical(cycle)
    plan["cycle_input_sha256"] = fixed.sha(q)
    for row in plan["members"]:
        if row["role"] == "cycle-inputs" or (row["root"], row["relative_path"]) == ("inputs", fixed.CYCLE_MEMBER):
            row["expected_sha256"] = fixed.sha(q)
    readback = "observation/flow-control-base/fixed-text-original-readback/"
    font_name = readback + "font-0000.original.bin"
    plan["members"].append({"root": "observation", "relative_path": font_name.split("/", 1)[1],
        "role": "font-original", "maximum": 16384, "expected_sha256": None})
    plan["members"].sort(key=lambda row: (row["root"], row["relative_path"]))
    p = fixed.canonical(plan)
    runtime["fixedtxt_plan"].update(fixed.descriptor(p))
    r = fixed.canonical(runtime)
    dispatch["fixedtxt"].update(plan_original=fixed.descriptor(p), runtime_original=fixed.descriptor(r),
                               cycle_original=fixed.descriptor(q))
    external = fixed.canonical(dispatch)
    held = fixed.admit_inputs(external, p, r, q, current=current, runner_temp="/synthetic-private-runner")
    originals = {row["root"] + "/" + row["relative_path"]: b'{"source_only_fixture":true}\n'
                 for row in plan["members"]}
    originals.update(joined)
    originals.update({"control/" + fixed.PLAN_MEMBER: p, "inputs/" + fixed.RUNTIME_MEMBER: r,
                      "inputs/" + fixed.CYCLE_MEMBER: q, "observation/fixed-text-inputs.original.json": q})
    png = b"png"  # Manifest fixture declares these bytes; no PNG/render claim.
    for index in range(180):
        originals["owner/" + fixed.NATIVE_SLOT + f"/frames/{index:06d}.png"] = png
    font = b"synthetic font original; no glyph acceptance"
    originals[font_name] = font
    originals[readback + "font-source-inventory.json"] = fixed.canonical({
        "originals": [{"role": "bundled", "path": "fonts/synthetic.woff2", "retained": "font-0000.original.bin",
                       "bytes": len(font), "sha256": fixed.sha(font)}],
        "glyph_fidelity_accepted": False, "browser_readiness_is_glyph_fidelity": False})
    rgba = b"\x00\x11\x22\xff" * (1920 * 1080)
    selected = []
    for frame in film.fixedtxt_frames(plan["fixture"]):
        stem = f"frame-{frame:06d}"
        originals[readback + stem + ".original.png"] = png
        originals[readback + stem + ".decoder.stdout"] = rgba
        originals[readback + stem + ".decoder.stderr"] = b""
        capture = {"state": "TERMINAL_OBSERVED", "automatic_retry": False,
            "terminal": {"whole_process_completed": True, "exit_code": 0, "timed_out": False,
                "output_budget_exceeded": False, "pipes_closed": True, "stdin_complete": True, "pipe_errors": []},
            "original_streams": {name: {"present": True, **fixed.descriptor(raw), "buffer_collected_from_process": True}
                                 for name, raw in (("stdout", rgba), ("stderr", b""))},
            "invocation": {"callable": "decoded_rgba_png_capture", "width": 1920, "height": 1080,
                "decoder": "/synthetic/ffmpeg", "decoder_sha256": plan["decoder_sha256"],
                "input_png_sha256": fixed.sha(png)}}
        originals[readback + stem + ".decoder.capture.json"] = fixed.canonical(capture)
        selected.append({"frame": frame, "png_sha256": fixed.sha(png), "png_bytes": len(png),
            "rgba_sha256": fixed.sha(rgba), "rgba_bytes": len(rgba),
            "original_rgba_file": stem + ".decoder.stdout", "compared_with_original_SDK_pixel_sample": False})
    originals[readback + "selected-originals.json"] = fixed.canonical({
        "schema": "motionwright.native.fixed-text-selected-originals/4", "arm": plan["arm"], "fixture": plan["fixture"],
        "manifest_sha256": fixed.sha(originals[readback + "artifact-manifest.json"]),
        "native_observation_sha256": fixed.sha(originals[readback + "native-observations.ndjson"]),
        "original_Host_stdin_sha256": fixed.sha(originals["observation/flow-control-base/native-result.host-stdin/capture-0001.stdin"]),
        "original_font_readiness_sha256": fixed.sha(originals[readback + "font-readiness-receipt.json"]),
        "selected_frames": selected, "actual_browser_readiness_observed": True, "native_effect_repeated": False,
        "oracle_v3_reused": False, "causal_pixel_verdict": "NOT_EVALUATED", "glyph_fidelity_accepted": False,
        "formal_acceptance": False, "human_acceptance": False})
    resolved = fixed.resolve_native_directory(plan, originals, cycle_raw=q)
    layout = fixed.owner_layout(current=current, runner_temp="/synthetic-private-runner")
    intent = {"schema": "motionwright.native.fixedtxt-supervised-stage/1", "phase": "work",
        "argv": ["/synthetic/python", "-B", "-E", "-s", "/synthetic/source/tooling/native_film180_original_g2.py",
            "--g2-inputs", layout["runtime"], "--fixed-text-v4-inputs", layout["cycle"],
            "--fixed-text-v4-fixture", plan["fixture"], "--fixedtxt-external-g2"],
        "automatic_retry": False, "process_started": False, "exit_code": None, "deadline_exceeded": False,
        "overflow": False, "native_success_inferred": False, "original_complete": False, "containment_empty": None,
        "attempt_started_monotonic": 1000.0, "attempt_deadline_monotonic": 1600.0,
        "stage_deadline_monotonic": 1480.0, "execution_deadline_monotonic": 1479.75}
    terminal = {**intent, "process_started": True, "exit_code": 0, "original_complete": True,
        "containment_empty": True, "pid": 456, "native_outcome": "NOT_INFERRED", "terminal_observed_monotonic": 1100.0,
        "streams": {name: {"observed_bytes": len(originals["control/supervision/work." + name]),
            "retained_bytes": len(originals["control/supervision/work." + name]), "eof": True,
            "observed_sha256": fixed.sha(originals["control/supervision/work." + name])} for name in ("stdout", "stderr")}}
    originals["control/supervision/work.intent.json"] = fixed.canonical(intent)
    originals["control/supervision/work.spawned.json"] = fixed.canonical({"pid": 456, "automatic_retry": False})
    originals["control/supervision/work.terminal.json"] = fixed.canonical(terminal)
    observations = []
    for index, row in enumerate(plan["members"], 1):
        name = row["root"] + "/" + row["relative_path"]
        physical = (resolved["physical_relative_directory"] + row["relative_path"][len(fixed.NATIVE_SLOT):]
                    if row["role"] == "native-png" else row["relative_path"])
        observations.append({"archive_path": name, "physical_relative_path": physical,
            "identity": [1, index, stat.S_IFREG | 0o600, 1000, 1000, len(originals[name]), 1, 1, 1],
            **fixed.descriptor(originals[name])})
    manifest = {"schema": "motionwright.ci.fixedtxt-selected-snapshot/2", "context": current,
        "plan_sha256": fixed.sha(p), "dispatch_sha256": fixed.sha(external),
        "scope": "explicit-original-files-at-read-time", "files": observations, "failures": [],
        "directory_resolution": resolved, "planned_originals_complete": True, "decoder_originals_verified": True,
        "supervisor_originals_verified": True,
        "complete_owner_state_claimed": False, "native_operation_retried": False, "native_success_verified": False,
        "physical_acceptance": False, "human_acceptance": False, "release_authorized": False}
    return current, held, originals, manifest


class OfflineOriginalReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = complete_snapshot_fixture()

    def verify(self, originals, manifest, suffix=b""):
        current, held, _, _ = self.fixture
        plaintext = fixed._pack_originals(originals, fixed.canonical(manifest)) + suffix
        return fixed.verify_snapshot(plaintext, held, current=current, runner_temp="/synthetic-private-runner")

    def test_real_reader_binds_all180_and_full_rgba_without_decoder_or_acceptance(self):
        _, _, originals, manifest = self.fixture
        result = self.verify(originals, manifest)
        self.assertEqual(result["frame_count"], 180)
        self.assertEqual(result["selected_decode_count"], 3)
        self.assertIs(result["decoder_invoked_by_intake"], False)
        self.assertIs(result["native_success_verified"], False)
        self.assertIs(result["human_acceptance"], False)

    def test_changed_unselected_native_png_cannot_hide_behind_valid_selected_rgba(self):
        _, _, original, before = self.fixture
        originals, manifest = dict(original), copy.deepcopy(before)
        name = "owner/" + fixed.NATIVE_SLOT + "/frames/000178.png"
        originals[name] = b"bad"
        row = next(row for row in manifest["files"] if row["archive_path"] == name)
        row.update(fixed.descriptor(originals[name]))
        with self.assertRaisesRegex(core.SelectionError, "all180-original-binding"):
            self.verify(originals, manifest)

    def test_tar_extensions_missing_member_and_altered_physical_mapping_are_rejected(self):
        _, _, originals, manifest = self.fixture
        with self.assertRaisesRegex(fixed.FixedTxtRejected, "canonical-ustar"):
            self.verify(originals, manifest, suffix=b"hidden trailing bytes")
        missing = dict(originals); del missing["owner/" + fixed.NATIVE_SLOT + "/frames/000002.png"]
        with self.assertRaisesRegex(fixed.FixedTxtRejected, "complete-snapshot-roster"):
            self.verify(missing, manifest)
        changed = copy.deepcopy(manifest)
        next(row for row in changed["files"] if row["archive_path"].endswith("frames/000003.png"))[
            "physical_relative_path"] = "output/unjoined/frames/000003.png"
        with self.assertRaisesRegex(fixed.FixedTxtRejected, "original-observation"):
            self.verify(originals, changed)


class SingleProcessContainmentDouble:
    """Test-only mechanical child; makes NO claim about descendant containment."""
    def __init__(self, path):
        self.path = Path(path)
        self.stops = 0

    def child_enter(self):
        self.path.write_text(str(os.getpid()))

    def populated(self):
        if not self.path.exists():
            return False
        try:
            os.kill(int(self.path.read_text()), 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self):
        self.stops += 1
        try:
            os.killpg(int(self.path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


class MechanicalBudgetTests(unittest.TestCase):
    def run_child(self, code, *, limit=4096, seconds=3):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        group = SingleProcessContainmentDouble(root / "mechanical-child.pid")
        clock = supervisor.AttemptClock({"attempt_seconds": seconds, "custody_seconds": 1})
        record = supervisor._run_original([sys.executable, "-c", code], environment=dict(os.environ),
            directory=root, phase="work", clock=clock, containment=group, stream_limit=limit)
        return record, root, clock, group

    def test_actual_stdout_stderr_and_terminal_retained_with_no_replay(self):
        record, root, clock, _ = self.run_child("import sys; sys.stdout.buffer.write(b'out'); sys.stderr.buffer.write(b'err')")
        self.assertEqual((root / "work.stdout").read_bytes(), b"out")
        self.assertEqual((root / "work.stderr").read_bytes(), b"err")
        self.assertEqual(record["exit_code"], 0)
        self.assertIs(record["original_complete"], True)
        self.assertEqual(record["streams"]["stdout"]["observed_sha256"], fixed.sha(b"out"))
        with self.assertRaisesRegex(supervisor.SupervisionRejected, "no-retry"):
            clock.reserve("work")
        self.assertIs(json.loads((root / "work.terminal.json").read_bytes())["native_success_inferred"], False)

    def test_deadline_kills_mechanical_child_and_keeps_unknown_original(self):
        before = time.monotonic()
        record, root, clock, group = self.run_child("import time; print('started', flush=True); time.sleep(20)", seconds=2)
        self.assertLess(time.monotonic() - before, 1.5)
        self.assertTrue(record["deadline_exceeded"])
        self.assertEqual(record["native_outcome"], "UNKNOWN_NO_RETRY")
        self.assertGreaterEqual(group.stops, 1)
        self.assertIs(record["containment_empty"], True)
        self.assertEqual((root / "work.stdout").read_bytes(), b"started\n")
        self.assertGreater(clock.reserve("custody"), time.monotonic())

    def test_overflow_preserves_bounded_prefix_and_never_claims_complete(self):
        record, root, _, group = self.run_child("import sys; sys.stdout.buffer.write(b'x'*65536); sys.stdout.flush()", limit=32)
        self.assertIs(record["overflow"], True)
        self.assertIs(record["original_complete"], False)
        self.assertEqual((root / "work.stdout").stat().st_size, 33)
        self.assertGreaterEqual(group.stops, 1)

    def test_boolean_and_reversed_budgets_cannot_start_a_clock(self):
        for limits in ({"attempt_seconds": True, "custody_seconds": 1},
                       {"attempt_seconds": 1, "custody_seconds": 1},
                       {"attempt_seconds": 2, "custody_seconds": 0}):
            with self.subTest(limits=limits), self.assertRaises(supervisor.SupervisionRejected):
                supervisor.AttemptClock(limits)

    def test_preexec_membership_write_uses_retained_cloexec_fd_before_exec(self):
        class RetainedFdDouble(SingleProcessContainmentDouble):
            def child_enter(self):
                os.write(self.fd, str(os.getpid()).encode())

            def populated(self):
                return self.path.stat().st_size > 0 and super().populated()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = RetainedFdDouble(root / "mechanical-child.pid")
            group.fd = os.open(group.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
            try:
                record = supervisor._run_original([sys.executable, "-c", "pass"], environment=dict(os.environ),
                    directory=root, phase="work", clock=supervisor.AttemptClock({"attempt_seconds": 3, "custody_seconds": 1}),
                    containment=group, stream_limit=4096)
                self.assertEqual(record["exit_code"], 0)
                self.assertEqual(record["pid"], int(group.path.read_text()))
            finally:
                os.close(group.fd)

    def test_actual_cancel_during_popen_retains_returned_handle_and_kills_mechanical_child(self):
        class CancelDuringPreexec(SingleProcessContainmentDouble):
            def child_enter(self):
                super().child_enter()
                os.kill(os.getppid(), signal.SIGTERM)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = CancelDuringPreexec(root / "mechanical-child.pid")
            clock = supervisor.AttemptClock({"attempt_seconds": 3, "custody_seconds": 1})
            prior = signal.signal(signal.SIGTERM, supervisor._cancel)
            try:
                with self.assertRaises(InterruptedError):
                    supervisor._run_original([sys.executable, "-c", "import time; time.sleep(20)"],
                        environment=dict(os.environ), directory=root, phase="work", clock=clock,
                        containment=group, stream_limit=4096)
                spawned = json.loads((root / "work.spawned.json").read_bytes())
                terminal = json.loads((root / "work.terminal.json").read_bytes())
                self.assertEqual(terminal["pid"], spawned["pid"])
                self.assertIs(terminal["process_started"], True)
                self.assertEqual(terminal["native_outcome"], "UNKNOWN_NO_RETRY")
                self.assertIs(terminal["original_complete"], False)
                self.assertFalse(group.populated())
                self.assertGreater(clock.reserve("custody"), time.monotonic())
            finally:
                signal.signal(signal.SIGTERM, prior)
                supervisor._CANCEL_SIGNAL = None
                supervisor._CANCEL_DELIVERED = False


if __name__ == "__main__":
    unittest.main()
