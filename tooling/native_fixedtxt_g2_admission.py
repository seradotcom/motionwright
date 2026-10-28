"""Review candidate for FixedTxt P/2 on the existing external G2 boundary.

This module supplies data validation and selected-original custody, not an
approval issuer or a native dispatcher. Legacy P/1 and its empty catalogue are
unchanged. A5 must review this new selection meaning before A6 composes a
producer. Nothing here manufactures a reviewed dispatch from producer bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import io
import json
import re
import tarfile
from pathlib import PurePosixPath

SCOPE = "fixedtxt-v4-one-fixture"
DISPATCH_SCHEMA = "motionwright.native.fixedtxt-g2-dispatch/1"
COMMITMENT_SCHEMA = "motionwright.native.fixedtxt-external-commitment/1"
PLAN_SCHEMA = "motionwright.native.fixedtxt-originals-plan/2"
SELECTOR = "original-fixedtxt-host-joined-artifact-directory/1"
# This is an ARCHIVE SLOT, never a physical output path or reserved job name.
NATIVE_SLOT = "output/__original_host_artifact__"
HELPER_PATHS = (
    "tooling/native_fixedtxt_g2_admission.py",
    "tooling/native_fixedtxt_g2_supervisor.py",
)
PLAN_MEMBER = "fixedtxt-plan.original.json"
RUNTIME_MEMBER = "source-input.original.json"
CYCLE_MEMBER = "cycle.original.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")


class FixedTxtRejected(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise FixedTxtRejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    # Exactly the existing ci_private_evidence canonical encoding.
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def strict(raw, maximum):
    need(type(raw) is bytes and 0 < len(raw) <= maximum, "fixedtxt-v2-json-bound")
    def pairs(values):
        result = {}
        for key, value in values:
            need(key not in result, "fixedtxt-v2-duplicate-key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(FixedTxtRejected("fixedtxt-v2-json-constant")))
    need(type(value) is dict, "fixedtxt-v2-object-required")
    return value


def descriptor(raw):
    need(type(raw) is bytes, "fixedtxt-v2-original-buffer")
    return {"bytes": len(raw), "sha256": sha(raw)}


def checked_descriptor(value, maximum):
    need(type(value) is dict and set(value) == {"bytes", "sha256"}
         and type(value["bytes"]) is int and 0 < value["bytes"] <= maximum
         and type(value["sha256"]) is str and SHA.fullmatch(value["sha256"]),
         "fixedtxt-v2-original-descriptor")
    return value


def source_paths():
    from native_film180_evidence_selection import FIXEDTXT_SOURCE_PATHS
    return tuple(sorted(set(FIXEDTXT_SOURCE_PATHS) | set(HELPER_PATHS)))


def validate_plan(value, *, current):
    """Reuse all P/1 bounds; only P/2's explicit archive-slot rule is new."""
    from native_film180_evidence_selection import fixedtxt_validate_plan, FIXEDTXT_SOURCE_PATHS
    need(type(value) is dict and value.get("schema") == PLAN_SCHEMA
         and value.get("native_directory_selector") == SELECTOR
         and value.get("native_directory") == NATIVE_SLOT,
         "fixedtxt-v2-closed-directory-selector")
    bindings = value.get("source_bindings")
    need(type(bindings) is list and all(type(row) is dict for row in bindings)
         and [row.get("path") for row in bindings] == list(source_paths()),
         "fixedtxt-v2-complete-source-closure")
    for row in bindings:
        need(set(row) == {"path", "sha256"} and type(row["sha256"]) is str
             and SHA.fullmatch(row["sha256"]), "fixedtxt-v2-source-pin")
    # Pure structural projection only: no pathname is opened/reserved and this
    # projected P/1 is never admitted, serialized, executed or returned.
    shape = copy.deepcopy(value)
    shape["schema"] = "motionwright.native.fixedtxt-originals-plan/1"
    del shape["native_directory_selector"]
    shape["source_bindings"] = [row for row in shape["source_bindings"]
                                if row["path"] in FIXEDTXT_SOURCE_PATHS]
    fixedtxt_validate_plan(shape, current=current)
    required = ("observation", value["fixture"] + "/native-result.host-stdin/broker-receipt.snapshot.json")
    rows = {(row["root"], row["relative_path"]): row for row in value["members"]}
    need(required in rows and rows[required]["role"] == "attempt-original"
         and rows[required]["maximum"] == 8 * 1024 * 1024
         and rows[required]["expected_sha256"] is None,
         "fixedtxt-v2-original-broker-receipt-required")
    for suffix, bound in (("intent.json", 65536), ("spawned.json", 65536),
                          ("stdout", 1024 * 1024 + 1), ("stderr", 1024 * 1024 + 1),
                          ("terminal.json", 65536)):
        row = rows.get(("control", "supervision/work." + suffix))
        need(row is not None and row["role"] == "attempt-original"
             and row["maximum"] == bound and row["expected_sha256"] is None,
             "fixedtxt-v2-original-supervision-required")
    need(len(canonical(value)) <= 512 * 1024, "fixedtxt-v2-plan-bound")
    return value


def validate_dispatch(raw, current):
    """The existing g2_admission owner input is the external boundary.

    This checks an independently supplied commitment. It never creates that
    commitment, infers review from booleans, or imports P/R/Q as authority.
    """
    import native_mlt_g2_custody as custody
    from ci_private_evidence import validate_context
    from native_film180_evidence_selection import FIXEDTXT_KEYS, FIXEDTXT_ARMS
    validate_context(current)
    need(current["run_attempt"] == "1", "fixedtxt-v2-first-attempt")
    value = strict(raw, 512 * 1024)
    need(set(value) == {"schema", "motionwright_sha", "semwright_sha", "official_runtime",
                       "compiler", "recipient", "fixedtxt"}
         and value["schema"] == DISPATCH_SCHEMA, "fixedtxt-v2-dispatch-fields")
    # Preserve the exact existing official-runtime, compiler artifact and
    # RSA-recipient admission. This is not a new installation authority.
    base = {key: item for key, item in value.items() if key != "fixedtxt"}
    base["schema"] = custody.FILM_DISPATCH_SCHEMA
    custody.validate_dispatch(canonical(base), current, scenario=custody.FILM_SCOPE)
    row = value["fixedtxt"]
    need(type(row) is dict and set(row) == {
        "schema", "admission_id", "context", "scope", "arm", "fixture", "single_owner",
        "plan_original", "runtime_original", "cycle_original", "wrapper_sha256", "limits"}
        and row["schema"] == COMMITMENT_SCHEMA and row["scope"] == SCOPE
        and row["context"] == current and type(row["single_owner"]) is int
        and row["single_owner"] == 1 and row["arm"] in FIXEDTXT_ARMS
        and row["fixture"] in FIXEDTXT_KEYS, "fixedtxt-v2-external-one-owner-context")
    need(type(row["admission_id"]) is str
         and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", row["admission_id"])
         and type(row["wrapper_sha256"]) is str and SHA.fullmatch(row["wrapper_sha256"]),
         "fixedtxt-v2-external-identity")
    for key, maximum in (("plan_original", 512 * 1024), ("runtime_original", 65536),
                          ("cycle_original", 1024 * 1024)):
        checked_descriptor(row[key], maximum)
    limits = row["limits"]
    need(type(limits) is dict and set(limits) == {
        "capsule_bytes", "files", "attempt_seconds", "custody_seconds"}
        and all(type(n) is int for n in limits.values())
        and 0 < limits["capsule_bytes"] <= 256 * 1024 * 1024
        and 0 < limits["files"] <= 4096
        and 0 < limits["custody_seconds"] < limits["attempt_seconds"] <= 2**31 - 1,
        "fixedtxt-v2-external-limits")
    return value


def owner_layout(*, current, runner_temp):
    from ci_private_evidence import validate_context
    validate_context(current)
    need(current["run_attempt"] == "1", "fixedtxt-v2-first-attempt")
    need(type(runner_temp) is str and 0 < len(runner_temp) <= 768
         and "\\" not in runner_temp and all(32 <= ord(ch) < 127 for ch in runner_temp),
         "fixedtxt-v2-owner-temp")
    root = PurePosixPath(runner_temp)
    need(root.is_absolute() and root != PurePosixPath("/") and str(root) == runner_temp
         and ".." not in root.parts, "fixedtxt-v2-owner-temp")
    suffix = current["run_id"] + "-1"
    control, inputs = (root / ("mw-fixedtxt-g2-" + kind + "-" + suffix)
                       for kind in ("control", "inputs"))
    return {"control_root": str(control), "inputs_root": str(inputs),
            "plan": str(control / PLAN_MEMBER), "runtime": str(inputs / RUNTIME_MEMBER),
            "cycle": str(inputs / CYCLE_MEMBER),
            "observation_root": str(root / ("mw-fixedtxt-g2-original-" + suffix))}


@dataclass(frozen=True)
class HeldInputs:
    """Validated originals only, not an execution grant or an acceptance result."""
    dispatch_raw: bytes
    plan_raw: bytes
    runtime_raw: bytes
    cycle_raw: bytes


def admit_inputs(dispatch_raw, plan_raw, runtime_raw, cycle_raw, *, current, runner_temp):
    from native_mlt_g2_observation import RUNTIME_INPUT_KEYS
    value = validate_dispatch(dispatch_raw, current)
    row = value["fixedtxt"]
    for key, original in (("plan_original", plan_raw), ("runtime_original", runtime_raw),
                          ("cycle_original", cycle_raw)):
        need(type(original) is bytes and descriptor(original) == row[key],
             "fixedtxt-v2-independent-" + key)
    plan = validate_plan(strict(plan_raw, 512 * 1024), current=current)
    need(canonical(plan) == plan_raw, "fixedtxt-v2-canonical-plan")
    need((plan["arm"], plan["fixture"], plan["limits"]) ==
         (row["arm"], row["fixture"], row["limits"]), "fixedtxt-v2-external-plan-selection")
    need([item["sha256"] for item in plan["source_bindings"]
          if item["path"] == "tooling/native_film180_original_g2.py"] == [row["wrapper_sha256"]],
         "fixedtxt-v2-reviewed-wrapper")
    layout = owner_layout(current=current, runner_temp=runner_temp)
    runtime = strict(runtime_raw, 65536)
    need(set(runtime) == RUNTIME_INPUT_KEYS | {"schema", "private_evidence_dir",
         "fixedtxt_admission_id", "fixedtxt_plan", "recipient_sha256"}
         and runtime["schema"] == "motionwright.native.fixedtxt-v4-g2-inputs/1",
         "fixedtxt-v2-runtime-shape")
    need(runtime["fixedtxt_admission_id"] == row["admission_id"]
         and runtime["recipient_sha256"] == value["recipient"]["sha256"]
         and runtime["private_evidence_dir"] == layout["observation_root"]
         and runtime["fixedtxt_plan"] == {"path": layout["plan"], **descriptor(plan_raw)}
         and all(runtime[name] == current[source] for name, source in (
             ("motionwright_sha", "source_sha"), ("semwright_sha", "semwright_sha"),
             ("run_id", "run_id"), ("run_attempt", "run_attempt"))),
         "fixedtxt-v2-runtime-context")
    need(sha(cycle_raw) == plan["cycle_input_sha256"], "fixedtxt-v2-cycle-original")
    cycle = strict(cycle_raw, 1024 * 1024)
    need(cycle.get("schema") == "motionwright.fixed-text-native-cycle-inputs/4"
         and cycle.get("arm") == plan["arm"] and cycle.get("decoder_sha256") == plan["decoder_sha256"]
         and type(cycle.get("runtime")) is dict
         and cycle["runtime"].get("semwright_revision") == current["semwright_sha"]
         and cycle["runtime"].get("native_source_sha256") == {
             arm: source["native_source_sha256"] for arm, source in plan["arm_sources"].items()},
         "fixedtxt-v2-cycle-source")
    members = {(item["root"], item["relative_path"]): item for item in plan["members"]}
    for name, raw, pin in ((RUNTIME_MEMBER, runtime_raw, None), (CYCLE_MEMBER, cycle_raw, sha(cycle_raw))):
        member = members.get(("inputs", name))
        need(member is not None and member["role"] == "input-original"
             and member["expected_sha256"] == pin and len(raw) <= member["maximum"],
             "fixedtxt-v2-planned-input-original")
    need([item for item in plan["members"] if item["role"] == "input-original"
          and item["expected_sha256"] is None] == [members[("inputs", RUNTIME_MEMBER)]],
         "fixedtxt-v2-only-runtime-nullable-input")
    return HeldInputs(dispatch_raw, plan_raw, runtime_raw, cycle_raw)


def acquire_inputs(dispatch_raw, *, environment, source_root):
    """Capture P/R/Q once through the actual existing no-follow RootHandles."""
    import native_evidence_projection as core
    import native_mlt_g2_observation as g2
    import native_mlt_runtime_admission as admission
    env = environment
    need(all(env.get(key) == expected for key, expected in (
        ("GITHUB_ACTIONS", "true"), ("CI", "true"), ("RUNNER_OS", "Linux"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_RUN_ATTEMPT", "1"),
        ("MW_MLT_SCOPE", SCOPE))), "fixedtxt-v2-existing-manual-owner-boundary")
    current = {"source_sha": env.get("GITHUB_SHA"), "semwright_sha": g2.SEMWRIGHT_PIN,
               "run_id": env.get("GITHUB_RUN_ID"), "run_attempt": "1"}
    validate_dispatch(dispatch_raw, current)  # before routing or filesystem access
    layout = owner_layout(current=current, runner_temp=env.get("RUNNER_TEMP"))
    need(str(admission.disposable_linux_runner()) == env.get("RUNNER_TEMP")
         and source_root == env.get("GITHUB_WORKSPACE"), "fixedtxt-v2-observed-owner-roots")
    handles = core.RootHandles({"control": layout["control_root"], "inputs": layout["inputs_root"]})
    primary = None
    try:
        readings = [handles.read(root, core.member_path(name), maximum) for root, name, maximum in (
            ("control", PLAN_MEMBER, 512 * 1024), ("inputs", RUNTIME_MEMBER, 65536),
            ("inputs", CYCLE_MEMBER, 1024 * 1024))]
        need(len({identity[:2] for _, identity in readings}) == 3, "fixedtxt-v2-aliased-inputs")
        held = admit_inputs(dispatch_raw, *(raw for raw, _ in readings), current=current,
                            runner_temp=env["RUNNER_TEMP"])
        plan = strict(held.plan_raw, 512 * 1024)
        core.verify_source_bindings(source_root, current["source_sha"], plan["source_bindings"])
        need(handles.stable_roots(), "fixedtxt-v2-input-roots-changed")
        return held
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            handles.close()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Original FixedTxt input-handle close also failed; nothing retried.")


def current_context(environment):
    import native_mlt_g2_observation as g2
    from ci_private_evidence import validate_context
    need(all(environment.get(key) == expected for key, expected in (
        ("GITHUB_ACTIONS", "true"), ("CI", "true"), ("RUNNER_OS", "Linux"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_RUN_ATTEMPT", "1"),
        ("MW_MLT_SCOPE", SCOPE))), "fixedtxt-v2-existing-manual-owner-boundary")
    return validate_context({"source_sha": environment.get("GITHUB_SHA"),
        "semwright_sha": g2.SEMWRIGHT_PIN, "run_id": environment.get("GITHUB_RUN_ID"),
        "run_attempt": "1"})


def owner_input_view(held, *, environment):
    """Revalidate retained originals for the existing wrapper, without rereading."""
    need(type(held) is HeldInputs, "fixedtxt-v2-held-originals-required")
    current = current_context(environment)
    need(environment.get("MW_MLT_G2_ADMISSION", "").encode() == held.dispatch_raw,
         "fixedtxt-v2-original-dispatch-changed")
    admit_inputs(held.dispatch_raw, held.plan_raw, held.runtime_raw, held.cycle_raw,
                 current=current, runner_temp=environment.get("RUNNER_TEMP"))
    dispatch = validate_dispatch(held.dispatch_raw, current)
    return {"plan": strict(held.plan_raw, 512 * 1024),
        "runtime": strict(held.runtime_raw, 65536), "plan_raw": held.plan_raw,
        "runtime_raw": held.runtime_raw, "cycle_raw": held.cycle_raw, "current": current,
        "admission_id": dispatch["fixedtxt"]["admission_id"],
        "recipient_sha256": dispatch["recipient"]["sha256"],
        "layout": owner_layout(current=current, runner_temp=environment["RUNNER_TEMP"])}


def acquire_wrapper_inputs(environment, *, runtime_input_path, cycle_input_path,
                           selected_fixture, source_root):
    """The new explicit wrapper path requires the actual owner's kernel scope."""
    from native_fixedtxt_g2_supervisor import require_work_containment
    current = current_context(environment)
    raw = environment.get("MW_MLT_G2_ADMISSION", "").encode()
    dispatch = validate_dispatch(raw, current)
    layout = owner_layout(current=current, runner_temp=environment.get("RUNNER_TEMP"))
    need(runtime_input_path == layout["runtime"] and cycle_input_path == layout["cycle"]
         and selected_fixture == dispatch["fixedtxt"]["fixture"],
         "fixedtxt-v2-exact-wrapper-selection")
    require_work_containment(current, source_root=source_root)
    return acquire_inputs(raw, environment=environment, source_root=source_root)


def resolve_native_directory(plan, originals, *, cycle_raw):
    """Resolve one physical suffix from held, mutually joined ORIGINAL bytes.

    A caller-supplied path/receipt flag cannot resolve it. Host job_id and the
    Motion Canvas job_ref deliberately remain distinct identities.
    """
    prefix = "observation/" + plan["fixture"] + "/"
    host = prefix + "native-result.host-stdin/"
    def read(name, maximum=8 * 1024 * 1024):
        return strict(originals[name], maximum)
    native = read(prefix + "native-result.json")
    returned = read(host + "native-return.snapshot.json")
    need(returned.get("schema") == "motionwright.private-native-return-snapshot/1"
         and returned.get("native_result") == "returned_success"
         and returned.get("wire_bytes") is False and returned.get("automatic_retry") is False
         and returned.get("value") == native, "fixedtxt-v2-original-native-return")
    need(native.get("schema") == "motionwright.native.fixed-text-render-result/4"
         and native.get("arm") == plan["arm"] and native.get("fixture") == plan["fixture"]
         and type(native.get("frame_count")) is int and native["frame_count"] == 180
         and type(native.get("segments")) is list and len(native["segments"]) == 1,
         "fixedtxt-v2-original-native-case")
    segment = native["segments"][0]
    artifact = segment.get("artifact")
    response = native.get("native_render_result")
    need(type(response) is dict and response.get("replayed") is False
         and response.get("result", {}).get("provenance_checked") is True,
         "fixedtxt-v2-original-result-provenance")
    data = response["result"].get("data", {})
    need(type(artifact) is dict and segment.get("frame_count") == 180
         and data.get("state") == "succeeded" and data.get("job_ref") == segment.get("job_ref")
         and data.get("artifact") == artifact and artifact.get("frame_count") == 180,
         "fixedtxt-v2-original-artifact-identity")
    name = artifact.get("directory")
    need(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name)
         and name not in (".", "..", "__original_host_artifact__"),
         "fixedtxt-v2-original-artifact-directory")
    binding = read(prefix + "native-result.fixed-text/host-binding.observation.json")
    readback = read(host + "readback.observation.json")
    capture = read(host + "capture-0001.json", 8192)
    terminal = read(host + "capture-0001.terminal.json", 2048)
    session = read(host + "session.original.json", 8192)
    receipt_snapshot = read(host + "broker-receipt.snapshot.json")
    receipt = receipt_snapshot.get("receipt")
    need(receipt_snapshot.get("schema") == "motionwright.private-production-receipt-snapshot/1"
         and receipt_snapshot.get("source") == "existing StudioService receipt"
         and receipt_snapshot.get("wire_bytes") is False
         and receipt_snapshot.get("lookup_error") is None
         and receipt_snapshot.get("snapshot_grants_authority") is False and type(receipt) is dict,
         "fixedtxt-v2-original-broker-receipt")
    request_id = "fixed-text-v4-" + plan["arm"].lower() + "-" + plan["fixture"] + ":motion:0:render-start"
    payload = receipt.get("payload", {})
    provenance = payload.get("result", {}).get("execution", {}).get("provenance", {})
    need(type(receipt.get("project_id")) is str
         and "project:" + receipt["project_id"] == native.get("project_resource")
         and receipt.get("generation") == native.get("generation")
         and receipt.get("revision") == native.get("revision")
         and receipt.get("request_id") == request_id and receipt.get("stage") == "completed"
         and receipt.get("command") == "driver.motion-canvas.render.start"
         and payload.get("result", {}).get("provenance_checked") is True
         and capture.get("parent_request_id") == payload.get("broker_request_id"),
         "fixedtxt-v2-original-render-parent")
    cycle = strict(cycle_raw, 1024 * 1024)
    runtime = cycle.get("runtime", {})
    provider = session.get("provider", {})
    need(session.get("schema") == "semwright.host-stdin-custody/1"
         and provider.get("id") == "driver:motion-canvas" and provider.get("kind") == "driver"
         and session.get("tool") == "motion-node"
         and session.get("driver_sha256") == runtime.get("driver_sha256")
         and session.get("tool_sha256") == runtime.get("node_sha256")
         and capture.get("provider") == provider and capture.get("tool") == session["tool"]
         and provenance.get("provider") == provider["id"]
         and provenance.get("source") == provider["kind"]
         and provenance.get("provider_version") == provider.get("version"),
         "fixedtxt-v2-original-provider-and-binary-identity")
    for key in ("job_id", "request_id", "parent_request_id", "session_id"):
        need(type(capture.get(key)) is str and 0 < len(capture[key]) <= 512
             and not any(ord(ch) < 32 or ord(ch) == 127 for ch in capture[key]),
             "fixedtxt-v2-original-host-identity")
    stdin = originals[host + "capture-0001.stdin"]
    need(type(capture.get("provider_process_id")) is int and capture["provider_process_id"] > 0
         and capture.get("terminal_file_required") is True
         and type(stdin) is bytes and 0 < len(stdin) <= 65536
         and capture.get("stdin_bytes") == len(stdin) and capture.get("stdin_sha256") == sha(stdin),
         "fixedtxt-v2-original-stdin")
    need(terminal == {"write_state": "write_all_completed", "partial_delivery_possible": False,
         "io_error_kind": None, "raw_os_error": None, "child_consumption": "not_observed",
         "native_render_success": "not_inferred"}, "fixedtxt-v2-original-pipe-terminal")
    join = readback.get("selected_capture_join", {})
    need(readback.get("schema") == "motionwright.private-host-stdin-readback/1"
         and readback.get("native_success_returned") is True
         and readback.get("native_return_snapshot_retained") is True
         and readback.get("original_identity_unchanged") is True
         and readback.get("new_capture_ordinals_retained") == [1]
         and readback.get("capture_read_errors") == []
         and all(readback.get(key) is None for key in ("identity_error", "native_return_snapshot_error",
                 "receipt_lookup_error", "receipt_retention_error"))
         and join.get("matched") is True and type(join.get("ordinal")) is int and join["ordinal"] == 1
         and join.get("capture") == capture and join.get("terminal") == terminal
         and join.get("pipe_write_all_completed") is True
         and join.get("joined_to") == "original completed render-start Broker parent and persisted session"
         and join.get("child_consumption") == "not_observed"
         and join.get("native_render_success") == "not_inferred"
         and join.get("Host_job_id_equals_MC_job_ref_claimed") is False,
         "fixedtxt-v2-original-selected-join")
    readback_root = prefix + "fixed-text-original-readback/"
    manifest_raw = originals[readback_root + "artifact-manifest.json"]
    need(binding.get("schema") == "motionwright.private-fixed-text-original-host-binding/4"
         and binding.get("arm") == plan["arm"] and binding.get("fixture") == plan["fixture"]
         and binding.get("original_sources_joined") is True
         and binding.get("selected_capture_join") == join
         and binding.get("selected_pipe_write_all_completed") is True
         and binding.get("original_Host_stdin_sha256") == sha(stdin)
         and binding.get("original_artifact_manifest_sha256") == sha(manifest_raw)
         and artifact.get("manifest_sha256") == sha(manifest_raw)
         and type(artifact.get("manifest_bytes")) is int and artifact["manifest_bytes"] == len(manifest_raw),
         "fixedtxt-v2-original-host-artifact-join")
    manifest = strict(manifest_raw, 1024 * 1024)
    config = binding.get("original_config", {})
    line, separator, helper = stdin.partition(b"\n")
    program_prefix = b"globalThis.__SEMWRIGHT_RENDER_INPUT__ = "
    need(separator == b"\n" and line.startswith(program_prefix) and line.endswith(b";"),
         "fixedtxt-v2-original-host-program-prefix")
    need(strict(line[len(program_prefix):-1], 65536) == config
         and descriptor(helper) == {key: runtime.get("render_helper", {}).get(key)
                                    for key in ("bytes", "sha256")},
         "fixedtxt-v2-original-host-program-and-helper")
    native_observations = manifest.get("native_observations", {})
    need(native_observations.get("render_input_digest") == config.get("renderInputDigest")
         and type(config.get("renderInputDigest")) is str and SHA.fullmatch(config["renderInputDigest"])
         and type(config.get("fontResourcesDigest")) is str and SHA.fullmatch(config["fontResourcesDigest"])
         and native_observations.get("font_resources_sha256") == config.get("fontResourcesDigest")
         and native.get("native_adapter_sha256") == plan["arm_sources"][plan["arm"]]["native_source_sha256"],
         "fixedtxt-v2-original-runtime-input-binding")
    frames = manifest.get("frames")
    need(type(frames) is list and len(frames) == 180, "fixedtxt-v2-all180-frame-roster")
    for index, row in enumerate(frames):
        need(type(row) is dict and type(row.get("index")) is int and row["index"] == index
             and row.get("file") == f"frames/{index:06d}.png"
             and type(row.get("bytes")) is int and 0 < row["bytes"] <= 32 * 1024 * 1024
             and type(row.get("sha256")) is str and SHA.fullmatch(row["sha256"]),
             "fixedtxt-v2-original-frame-member")
    return {"physical_relative_directory": "output/" + name,
            "archive_slot": NATIVE_SLOT, "original_result_sha256": sha(originals[prefix + "native-result.json"]),
            "original_return_sha256": sha(originals[host + "native-return.snapshot.json"]),
            "original_broker_receipt_sha256": sha(originals[host + "broker-receipt.snapshot.json"]),
            "original_host_stdin_sha256": sha(stdin), "original_manifest_sha256": sha(manifest_raw),
            "host_parent_request_id": capture["parent_request_id"], "host_session_id": capture["session_id"],
            "host_job_id": capture["job_id"], "motion_canvas_job_ref": segment["job_ref"],
            "frame_count": 180, "native_success_verified": False, "human_acceptance": False}


def _read_admitted_original(handles, row, physical, *, plan):
    """P/2's literal stream roster, on the same anchored no-follow handles.

    Core's ordinary reader still excludes raw streams. This new reviewed P/2
    meaning admits only the mandatory FixedTxt stdout/stderr originals below;
    it does not rename files or change Core/legacy's exclusion policy.
    """
    import os
    import stat
    import native_evidence_projection as core
    from native_film180_evidence_selection import fixedtxt_frames, FIXEDTXT_RGBA_BYTES
    parts = core.member_path(physical)
    if not core.excluded(parts):
        return handles.read(row["root"], parts, row["maximum"])
    base = plan["fixture"] + "/"
    allowed = {("observation", base + "fixed.project-inspect.stdout"):
               ("project-snapshot-original", 8 * 1024 * 1024)}
    for frame in fixedtxt_frames(plan["fixture"]):
        stem = base + f"fixed-text-original-readback/frame-{frame:06d}.decoder."
        allowed[("observation", stem + "stdout")] = ("selected-rgba", FIXEDTXT_RGBA_BYTES)
        allowed[("observation", stem + "stderr")] = ("decoder-stderr", 256 * 1024)
    for stream in ("stdout", "stderr"):
        allowed[("control", "supervision/work." + stream)] = ("attempt-original", 1024 * 1024 + 1)
    admitted = allowed.get((row["root"], physical))
    need(admitted is not None and physical == row["relative_path"]
         and row["role"] == admitted[0] and type(row["maximum"]) is int
         and 0 <= row["maximum"] <= admitted[1], "fixedtxt-v2-closed-original-stream-roster")
    maximum = row["maximum"]
    parents = handles.parent(row["root"], parts)
    fd = -1
    try:
        before_name = os.stat(parts[-1], dir_fd=parents[-1][0], follow_symlinks=False)
        need(stat.S_ISREG(before_name.st_mode) and before_name.st_nlink == 1,
             "fixedtxt-v2-stream-regular-single-link")
        need(0 <= before_name.st_size <= maximum, "fixedtxt-v2-stream-byte-budget")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parents[-1][0])
        before = os.fstat(fd)
        need(core.identity(before_name) == core.identity(before), "fixedtxt-v2-stream-changed-before-open")
        raw = bytearray()
        while True:
            block = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            need(len(raw) <= maximum, "fixedtxt-v2-stream-byte-budget")
        after = os.fstat(fd)
        after_name = os.stat(parts[-1], dir_fd=parents[-1][0], follow_symlinks=False)
        need(core.identity(before) == core.identity(after) == core.identity(after_name)
             and len(raw) == before.st_size
             and all(core.identity(os.fstat(parent)) == mark for parent, mark in parents),
             "fixedtxt-v2-stream-changed-during-read")
        return bytes(raw), core.identity(before)
    finally:
        if fd >= 0:
            os.close(fd)
        handles.close_parents(parents)


def verify_supervisor_originals(originals, *, plan, current, runner_temp):
    """Verify the original work-stage mechanics, without inferring a render."""
    import math
    prefix = "control/supervision/work."
    intent = strict(originals[prefix + "intent.json"], 65536)
    spawned = strict(originals[prefix + "spawned.json"], 65536)
    terminal = strict(originals[prefix + "terminal.json"], 65536)
    fields = {"schema", "phase", "argv", "automatic_retry", "process_started", "exit_code",
        "deadline_exceeded", "overflow", "native_success_inferred", "original_complete", "containment_empty",
        "attempt_started_monotonic", "attempt_deadline_monotonic", "stage_deadline_monotonic",
        "execution_deadline_monotonic"}
    need(set(intent) == fields and intent["schema"] == "motionwright.native.fixedtxt-supervised-stage/1"
         and intent["phase"] == "work" and intent["exit_code"] is None and intent["containment_empty"] is None
         and all(intent[key] is False for key in ("automatic_retry", "process_started", "deadline_exceeded",
             "overflow", "native_success_inferred", "original_complete")), "fixedtxt-v2-work-original-intent")
    layout = owner_layout(current=current, runner_temp=runner_temp)
    argv = intent["argv"]
    need(type(argv) is list and len(argv) == 12 and all(type(value) is str for value in argv)
         and argv[0].startswith("/") and argv[1:4] == ["-B", "-E", "-s"]
         and argv[4].startswith("/") and argv[4].endswith("/tooling/native_film180_original_g2.py")
         and argv[5:] == ["--g2-inputs", layout["runtime"], "--fixed-text-v4-inputs", layout["cycle"],
                          "--fixed-text-v4-fixture", plan["fixture"], "--fixedtxt-external-g2"],
         "fixedtxt-v2-work-original-command")
    need(set(terminal) == fields | {"pid", "streams", "native_outcome", "terminal_observed_monotonic"}
         and all(terminal[key] == intent[key] for key in fields - {
             "process_started", "exit_code", "original_complete", "containment_empty"})
         and terminal["process_started"] is True and terminal["original_complete"] is True
         and terminal["containment_empty"] is True and type(terminal["exit_code"]) is int
         and terminal["exit_code"] == 0 and terminal["native_outcome"] == "NOT_INFERRED",
         "fixedtxt-v2-work-original-terminal")
    need(set(spawned) == {"pid", "automatic_retry"} and type(spawned["pid"]) is int
         and spawned["pid"] > 0 and spawned["automatic_retry"] is False
         and type(terminal["pid"]) is int and terminal["pid"] == spawned["pid"],
         "fixedtxt-v2-work-original-pid")
    times = [terminal[key] for key in ("attempt_started_monotonic", "execution_deadline_monotonic",
        "stage_deadline_monotonic", "attempt_deadline_monotonic", "terminal_observed_monotonic")]
    need(all(type(value) in (int, float) and math.isfinite(value) for value in times),
         "fixedtxt-v2-work-original-clock")
    start, execution, stage, total, finished = times
    need(0 <= start <= execution < stage < total and start <= finished <= stage
         and abs(total - start - plan["limits"]["attempt_seconds"]) < 0.000001
         and abs(total - stage - plan["limits"]["custody_seconds"]) < 0.000001,
         "fixedtxt-v2-work-original-budget")
    streams = terminal["streams"]
    need(type(streams) is dict and set(streams) == {"stdout", "stderr"}, "fixedtxt-v2-work-original-streams")
    for name in streams:
        raw, observed = originals[prefix + name], streams[name]
        need(type(observed) is dict and set(observed) == {"observed_bytes", "retained_bytes", "eof", "observed_sha256"}
             and observed["eof"] is True and type(observed["observed_bytes"]) is int
             and type(observed["retained_bytes"]) is int
             and observed["observed_bytes"] == observed["retained_bytes"] == len(raw) <= 1024 * 1024
             and observed["observed_sha256"] == sha(raw), "fixedtxt-v2-work-original-stream-binding")
    return {"original_work_stage_verified": True, "native_success_verified": False}


def capture_selected(held, *, current, runner_temp, source_root, roots):
    """One no-follow read per selected file, including all 180 original PNGs.

    This intentionally does not walk or claim the whole owner workspace. Extra
    journals/configuration/socket files are outside this explicit selection.
    """
    import native_evidence_projection as core
    from native_film180_evidence_selection import fixedtxt_validate_selected_frames
    need(type(held) is HeldInputs, "fixedtxt-v2-held-originals-required")
    admit_inputs(held.dispatch_raw, held.plan_raw, held.runtime_raw, held.cycle_raw,
                 current=current, runner_temp=runner_temp)
    plan = strict(held.plan_raw, 512 * 1024)
    layout = owner_layout(current=current, runner_temp=runner_temp)
    need(type(roots) is dict and set(roots) == {"control", "inputs", "observation", "owner"}
         and roots["control"] == layout["control_root"] and roots["inputs"] == layout["inputs_root"]
         and roots["observation"] == layout["observation_root"], "fixedtxt-v2-source-owned-roots")
    # Existing owner receipt independently fixes the only native owner's root.
    from native_mlt_g2_custody import checked_owner_workspace
    from pathlib import Path
    if roots["owner"] is not None:
        owner = checked_owner_workspace({"RUNNER_TEMP": runner_temp}, current,
                                         Path(roots["observation"]) / plan["fixture"])
        need(str(owner) == str(roots["owner"]), "fixedtxt-v2-original-owner-root")
    core.verify_source_bindings(source_root, current["source_sha"], plan["source_bindings"])
    # When no original owner identity can be verified, retain P/R/Q and the
    # outer work originals. No guessed owner path is read, and all unresolved
    # native members remain explicitly missing, never evidence of no effect.
    readable = (roots if roots["owner"] is not None else
                {key: roots[key] for key in ("control", "inputs")})
    handles = core.RootHandles(readable)
    originals, observations, failed, identities = {}, [], [], set()
    total, resolved, primary = 0, None, None
    try:
        static = [row for row in plan["members"] if row["role"] != "native-png"]
        dynamic = [row for row in plan["members"] if row["role"] == "native-png"]
        def take(row, physical):
            nonlocal total
            key = row["root"] + "/" + row["relative_path"]
            need(row["root"] in readable, "fixedtxt-v2-original-owner-identity-unavailable")
            raw, identity = _read_admitted_original(handles, row, physical, plan=plan)
            need(identity[:2] not in identities, "fixedtxt-v2-aliased-selected-original")
            identities.add(identity[:2])
            need(row["expected_sha256"] is None or row["expected_sha256"] == sha(raw),
                 "fixedtxt-v2-frozen-original-changed")
            total += len(raw)
            need(total <= plan["limits"]["capsule_bytes"], "fixedtxt-v2-selected-byte-budget")
            originals[key] = raw
            observations.append({"archive_path": key, "physical_relative_path": physical,
                                 "identity": list(identity), **descriptor(raw)})
        for row in static:
            try:
                take(row, row["relative_path"])
            except (OSError, ValueError, core.SelectionError) as error:
                failed.append({"archive_path": row["root"] + "/" + row["relative_path"],
                               "error_class": type(error).__name__, "automatic_retry": False})
        for key, expected in (("control/" + PLAN_MEMBER, held.plan_raw),
                              ("inputs/" + RUNTIME_MEMBER, held.runtime_raw),
                              ("inputs/" + CYCLE_MEMBER, held.cycle_raw)):
            if key in originals and originals[key] != expected:
                failed.append({"archive_path": key, "error_class": "FrozenOriginalChanged",
                               "automatic_retry": False})
        try:
            resolved = resolve_native_directory(plan, originals, cycle_raw=held.cycle_raw)
        except (KeyError, TypeError, ValueError):
            resolved = None
        for row in dynamic:
            if resolved is None:
                failed.append({"archive_path": row["root"] + "/" + row["relative_path"],
                               "error_class": "OriginalHostJoinUnavailable", "automatic_retry": False})
                continue
            relative = resolved["physical_relative_directory"] + row["relative_path"][len(NATIVE_SLOT):]
            try:
                take(row, relative)
            except (OSError, ValueError, core.SelectionError) as error:
                failed.append({"archive_path": row["root"] + "/" + row["relative_path"],
                               "error_class": type(error).__name__, "automatic_retry": False})
        need(handles.stable_roots(), "fixedtxt-v2-selected-roots-changed")
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            handles.close()
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Original selected handle close failed; no second read attempted.")
    core.verify_source_bindings(source_root, current["source_sha"], plan["source_bindings"])
    complete = not failed and len(originals) == len(plan["members"])
    verified = supervisor_verified = False
    if complete:
        try:
            fixedtxt_validate_selected_frames(originals, plan=plan)
            verified = True
        except (KeyError, TypeError, ValueError, core.SelectionError):
            pass
        try:
            verify_supervisor_originals(originals, plan=plan, current=current, runner_temp=runner_temp)
            supervisor_verified = True
        except (KeyError, TypeError, ValueError):
            pass
    manifest = {"schema": "motionwright.ci.fixedtxt-selected-snapshot/2", "context": current,
        "plan_sha256": sha(held.plan_raw), "dispatch_sha256": sha(held.dispatch_raw),
        "scope": "explicit-original-files-at-read-time", "files": observations, "failures": failed,
        "directory_resolution": resolved, "planned_originals_complete": complete,
        "decoder_originals_verified": verified, "complete_owner_state_claimed": False,
        "supervisor_originals_verified": supervisor_verified,
        "native_operation_retried": False, "native_success_verified": False,
        "physical_acceptance": False, "human_acceptance": False, "release_authorized": False}
    metadata = canonical(manifest)
    need(len(metadata) <= 512 * 1024, "fixedtxt-v2-snapshot-metadata-budget")
    raw = _pack_originals(originals, metadata)
    need(len(raw) + 8192 + 64 + 512 * 1024 <= plan["limits"]["capsule_bytes"],
         "fixedtxt-v2-complete-envelope-budget")
    return raw, manifest


def _pack_originals(originals, metadata):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in [*sorted(originals.items()), ("snapshot-manifest.json", metadata)]:
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(data), 0o600, 0
            archive.addfile(item, io.BytesIO(data))
    return stream.getvalue()


def capture_owner(environment):
    """Read-only custody child for an independently supplied external dispatch.

    It never starts the wrapper/renderer and never claims upload authorization.
    A partial result preserves its failures and cannot report native success.
    """
    import os
    from pathlib import Path
    import native_mlt_g2_observation as g2
    from native_mlt_g2_custody import checked_owner_workspace
    from ci_private_evidence import encrypt_snapshot, new_bytes, new_json
    env = environment
    source_root = str(Path(__file__).resolve().parents[1])
    raw_dispatch = env.get("MW_MLT_G2_ADMISSION", "").encode()
    held = acquire_inputs(raw_dispatch, environment=env, source_root=source_root)
    current = {"source_sha": env["GITHUB_SHA"], "semwright_sha": g2.SEMWRIGHT_PIN,
               "run_id": env["GITHUB_RUN_ID"], "run_attempt": "1"}
    plan = strict(held.plan_raw, 512 * 1024)
    layout = owner_layout(current=current, runner_temp=env["RUNNER_TEMP"])
    try:
        owner = checked_owner_workspace(env, current, Path(layout["observation_root"]) / plan["fixture"])
    except (OSError, ValueError, KeyError, TypeError):
        owner = None
    roots = {"control": layout["control_root"], "inputs": layout["inputs_root"],
             "observation": layout["observation_root"], "owner": None if owner is None else str(owner)}
    plaintext, manifest = capture_selected(held, current=current, runner_temp=env["RUNNER_TEMP"],
                                         source_root=source_root, roots=roots)
    dispatch = validate_dispatch(raw_dispatch, current)
    envelope = encrypt_snapshot(plaintext, dispatch["recipient"], current)
    public = {"schema": "motionwright.native.fixedtxt-custody-public/2", "scope": SCOPE,
        "context": current, "arm": plan["arm"], "fixture": plan["fixture"],
        "plan_sha256": sha(held.plan_raw), "external_dispatch_sha256": sha(raw_dispatch),
        "recipient_spki_sha256": dispatch["recipient"]["sha256"],
        "sealed_originals_sha256": sha(envelope), "sealed_originals_bytes": len(envelope),
        "planned_originals_complete": manifest["planned_originals_complete"],
        "decoder_originals_verified": manifest["decoder_originals_verified"],
        "supervisor_originals_verified": manifest["supervisor_originals_verified"],
        "retained_file_count": len(manifest["files"]), "missing_file_count": len(manifest["failures"]),
        "native_success_verified": False, "native_operation_retried": False,
        "physical_acceptance": False, "human_acceptance": False, "release_authorized": False,
        "upload_authorization": "NOT_GRANTED_BY_COLLECTOR"}
    need(len(envelope) + len(canonical(public)) <= plan["limits"]["capsule_bytes"],
         "fixedtxt-v2-two-file-capsule-bound")
    target = Path(env["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-sealed-" + current["run_id"] + "-1")
    target.mkdir(mode=0o700, exist_ok=False)
    new_bytes(target / "sealed-originals.bin", envelope)
    new_json(target / "public-evidence.json", public)
    return public


def verify_snapshot(plaintext, held, *, current, runner_temp):
    """Independent bounded rereading of a retained capsule, never of an owner.

    This is only a custody/selection check. It neither runs decoders nor grants
    glyph, render, human or release acceptance. Partial capsules are rejected
    as complete evidence while their encrypted original bytes remain intact.
    """
    from native_film180_evidence_selection import fixedtxt_validate_selected_frames
    need(type(held) is HeldInputs, "fixedtxt-v2-held-originals-required")
    admit_inputs(held.dispatch_raw, held.plan_raw, held.runtime_raw, held.cycle_raw,
                 current=current, runner_temp=runner_temp)
    plan = strict(held.plan_raw, 512 * 1024)
    need(type(plaintext) is bytes and 0 < len(plaintext) <= plan["limits"]["capsule_bytes"],
         "fixedtxt-v2-snapshot-bound")
    expected = {row["root"] + "/" + row["relative_path"]: row for row in plan["members"]}
    originals, manifest_raw = {}, None
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
        for member in archive:
            need(member.isreg() and not member.pax_headers and member.name not in originals
                 and member.name in {*expected, "snapshot-manifest.json"},
                 "fixedtxt-v2-snapshot-member")
            need(member.mode == 0o600 and member.mtime == 0 and member.uid == member.gid == 0
                 and member.uname == member.gname == "", "fixedtxt-v2-snapshot-header")
            if member.name == "snapshot-manifest.json":
                need(manifest_raw is None and 0 < member.size <= 512 * 1024,
                     "fixedtxt-v2-one-snapshot-manifest")
                manifest_raw = archive.extractfile(member).read(512 * 1024 + 1)
                continue
            row = expected[member.name]
            need(0 <= member.size <= row["maximum"], "fixedtxt-v2-snapshot-member-bound")
            data = archive.extractfile(member).read(row["maximum"] + 1)
            need(len(data) == member.size
                 and (row["expected_sha256"] is None or sha(data) == row["expected_sha256"]),
                 "fixedtxt-v2-snapshot-member-pin")
            originals[member.name] = data
    need(manifest_raw is not None and set(originals) == set(expected),
         "fixedtxt-v2-complete-snapshot-roster")
    need(_pack_originals(originals, manifest_raw) == plaintext,
         "fixedtxt-v2-canonical-ustar-originals")
    manifest = strict(manifest_raw, 512 * 1024)
    need(set(manifest) == {"schema", "context", "plan_sha256", "dispatch_sha256", "scope",
         "files", "failures", "directory_resolution", "planned_originals_complete",
         "decoder_originals_verified", "complete_owner_state_claimed", "native_operation_retried",
         "supervisor_originals_verified",
         "native_success_verified", "physical_acceptance", "human_acceptance", "release_authorized"}
         and canonical(manifest) == manifest_raw
         and manifest["schema"] == "motionwright.ci.fixedtxt-selected-snapshot/2"
         and manifest["context"] == current and manifest["plan_sha256"] == sha(held.plan_raw)
         and manifest["dispatch_sha256"] == sha(held.dispatch_raw)
         and manifest["scope"] == "explicit-original-files-at-read-time"
         and manifest["failures"] == [] and manifest["planned_originals_complete"] is True
         and manifest["decoder_originals_verified"] is True
         and manifest["supervisor_originals_verified"] is True
         and all(manifest[name] is False for name in ("complete_owner_state_claimed",
             "native_operation_retried", "native_success_verified", "physical_acceptance",
             "human_acceptance", "release_authorized")), "fixedtxt-v2-snapshot-original-scope")
    for key, original in (("control/" + PLAN_MEMBER, held.plan_raw),
                          ("inputs/" + RUNTIME_MEMBER, held.runtime_raw),
                          ("inputs/" + CYCLE_MEMBER, held.cycle_raw)):
        need(originals[key] == original, "fixedtxt-v2-snapshot-original-input-changed")
    resolved = resolve_native_directory(plan, originals, cycle_raw=held.cycle_raw)
    need(manifest["directory_resolution"] == resolved, "fixedtxt-v2-snapshot-directory-join")
    rows = manifest["files"]
    need(type(rows) is list and len(rows) == len(expected), "fixedtxt-v2-snapshot-observation-roster")
    seen, identities = set(), set()
    for row in rows:
        need(type(row) is dict and set(row) == {"archive_path", "physical_relative_path", "identity", "bytes", "sha256"}
             and row["archive_path"] in expected and row["archive_path"] not in seen,
             "fixedtxt-v2-snapshot-observation-member")
        key = row["archive_path"]
        declared = expected[key]
        physical = (resolved["physical_relative_directory"] + declared["relative_path"][len(NATIVE_SLOT):]
                    if declared["role"] == "native-png" else declared["relative_path"])
        identity = row["identity"]
        import stat
        need(row["physical_relative_path"] == physical
             and {name: row[name] for name in ("bytes", "sha256")} == descriptor(originals[key])
             and type(identity) is list and len(identity) == 9
             and all(type(value) is int for value in identity)
             and stat.S_ISREG(identity[2]) and identity[5] == len(originals[key]) and identity[8] == 1
             and tuple(identity[:2]) not in identities, "fixedtxt-v2-snapshot-original-observation")
        seen.add(key)
        identities.add(tuple(identity[:2]))
    selected = fixedtxt_validate_selected_frames(originals, plan=plan)
    verify_supervisor_originals(originals, plan=plan, current=current, runner_temp=runner_temp)
    return {"selected_originals_verified": True, "context": current,
        "arm": plan["arm"], "fixture": plan["fixture"], "frame_count": 180,
        "selected_decode_count": selected["selected_decode_count"], "decoder_invoked_by_intake": False,
        "native_success_verified": False, "physical_acceptance": False,
        "human_acceptance": False, "release_authorized": False}


def verify_encrypted(envelope, public_raw, held, *, current, runner_temp, private_key):
    """Offline recipient entry point; never pass the recipient key to a runner."""
    from ci_private_evidence import decrypt_snapshot
    dispatch = validate_dispatch(held.dispatch_raw, current)
    plan = strict(held.plan_raw, 512 * 1024)
    public = strict(public_raw, 512 * 1024)
    need(type(envelope) is bytes and len(envelope) + len(public_raw) <= plan["limits"]["capsule_bytes"],
         "fixedtxt-v2-encrypted-capsule-budget")
    need(set(public) == {"schema", "scope", "context", "arm", "fixture", "plan_sha256",
        "external_dispatch_sha256", "recipient_spki_sha256", "sealed_originals_sha256",
        "sealed_originals_bytes", "planned_originals_complete", "decoder_originals_verified",
        "supervisor_originals_verified",
        "retained_file_count", "missing_file_count", "native_success_verified", "native_operation_retried",
        "physical_acceptance", "human_acceptance", "release_authorized", "upload_authorization"}
        and canonical(public) == public_raw
        and public["schema"] == "motionwright.native.fixedtxt-custody-public/2"
        and public["scope"] == SCOPE and public["context"] == current
        and (public["arm"], public["fixture"]) == (plan["arm"], plan["fixture"])
        and public["plan_sha256"] == sha(held.plan_raw)
        and public["external_dispatch_sha256"] == sha(held.dispatch_raw)
        and public["recipient_spki_sha256"] == dispatch["recipient"]["sha256"]
        and type(public["sealed_originals_bytes"]) is int
        and public["sealed_originals_bytes"] == len(envelope)
        and public["sealed_originals_sha256"] == sha(envelope)
        and public["planned_originals_complete"] is True and public["decoder_originals_verified"] is True
        and public["supervisor_originals_verified"] is True
        and type(public["retained_file_count"]) is int and public["retained_file_count"] == len(plan["members"])
        and type(public["missing_file_count"]) is int and public["missing_file_count"] == 0
        and public["upload_authorization"] == "NOT_GRANTED_BY_COLLECTOR"
        and all(public[name] is False for name in ("native_success_verified", "native_operation_retried",
            "physical_acceptance", "human_acceptance", "release_authorized")),
        "fixedtxt-v2-public-capsule-binding")
    plaintext, encrypted_context = decrypt_snapshot(envelope, private_key)
    need(encrypted_context == current, "fixedtxt-v2-encrypted-context")
    return verify_snapshot(plaintext, held, current=current, runner_temp=runner_temp)


if __name__ == "__main__":
    import os
    import sys
    # There is deliberately no native producer command in this review candidate.
    need(sys.argv[1:] == ["--capture-selected"], "fixedtxt-v2-custody-only-command")
    try:
        public = capture_owner(os.environ)
        need(public["planned_originals_complete"] is True and public["decoder_originals_verified"] is True
             and public["supervisor_originals_verified"] is True, "fixedtxt-v2-custody-incomplete-originals")
    except Exception:
        print("FixedTxt original custody incomplete; no retry or native acceptance.", file=sys.stderr)
        raise SystemExit(2) from None
