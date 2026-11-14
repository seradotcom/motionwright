"""Preparation and original-byte transport for the existing FixedTxt owner.

The second invocation of the same workflow only carries an external R28
dispatch. It creates no commit status, native result, numerical oracle or
alternative execution permit. The existing P/R/Q validators and supervisor
remain the effect boundary.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time

from native_owner_resources import OWNER_RESOURCE_POLICY
import zipfile


REPO = "seradotcom/motionwright"
WORKFLOW_PATH = ".github/workflows/native-mlt-sequence-e2e.yml"
WORKFLOW_ENDPOINT = "repos/" + REPO + "/actions/workflows/native-mlt-sequence-e2e.yml"
SCOPE = "fixedtxt-v4-one-fixture"
DELIVERY_MAX = 65536
DELIVERY_MEMBERS_MAX = 60 * 1024
API_JOURNAL_MAX = 1024 * 1024
# Retain every original GET under the existing 1 MiB journal, including
# legitimate reviewer latency. Do not poll external R28 state every second.
DELIVERY_POLL_SECONDS = 20.0
NATIVE_PATH = "integrations/composition/motion/native.ts"
HISTORICAL_PAIR = {
    "RED": "dfe6c0fc226d7e9017eb718c7e745d942c4067a54f968d4483b80bbdd51b4fa8",
    "GREEN": "a400b38608e169a20e7ade099aa8632c11629152951b9e62a0fb66858b530f36",
}
PAIR = {
    "RED": "9a54b51d25e0fa8521718fd03f9f29145e7bfc0395ff8fe40bd6d3fbccb23a96",
    "GREEN": "63ffb19cf10e042d988608fca632780dc2cdc9e7766c3d1cc1e90d4f981e43c6",
}
FIXTURE_HELPER = "crates/driver-motion-canvas/examples/support/fixed_text_fixtures_v4.rs"
FIXTURE_HELPER_SHA256 = "372b1aab1412fa61b7003b897729f9ac2e6152f3cc5334890256ba025a1984f6"
CALIBRATION_KEYS = ("fixed-multirun-control", "fixed-multirun", "fixed-camera-scene")
SHA = re.compile(r"[0-9a-f]{64}\Z")
RUN = re.compile(r"[1-9][0-9]*\Z")


class WorkflowRejected(ValueError):
    pass


def need(value, reason):
    if not value:
        raise WorkflowRejected(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def strict(raw, maximum=DELIVERY_MAX):
    need(type(raw) is bytes and 0 < len(raw) <= maximum, "fixedtxt-workflow-json-bound")
    def pairs(rows):
        value = {}
        for key, item in rows:
            need(key not in value, "fixedtxt-workflow-duplicate-key")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(WorkflowRejected("nonfinite JSON")))
    need(type(value) is dict, "fixedtxt-workflow-object")
    return value


def artifact_name(target_run, request_sha256=None):
    need(type(target_run) is str and RUN.fullmatch(target_run), "fixedtxt-delivery-target")
    need(request_sha256 is None or type(request_sha256) is str and SHA.fullmatch(request_sha256),
         "fixedtxt-delivery-case-digest")
    suffix = "" if request_sha256 is None else "-" + request_sha256
    return "motionwright-fixedtxt-r28-delivery-" + target_run + "-1" + suffix


def original_descriptor(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def select_delivery_artifact(listing, target_run, request_sha256=None):
    """Select the complete name result BEFORE provenance, digest or content filters."""
    need(type(listing) is dict and type(listing.get("total_count")) is int
         and type(listing.get("artifacts")) is list, "fixedtxt-delivery-list-shape")
    count, items = listing["total_count"], listing["artifacts"]
    need(count in (0, 1) and len(items) == count, "fixedtxt-delivery-not-unique")
    if count == 0:
        return None
    item = items[0]
    need(type(item) is dict and item.get("name") == artifact_name(target_run, request_sha256),
         "fixedtxt-delivery-name")
    need(type(item.get("id")) is int and item["id"] > 0, "fixedtxt-delivery-artifact-id")
    return item


def checked_workflow(workflow):
    need(type(workflow) is dict and type(workflow.get("id")) is int and workflow["id"] > 0
         and workflow.get("path") == WORKFLOW_PATH and workflow.get("state") == "active",
         "fixedtxt-delivery-workflow")
    return workflow["id"]


def checked_run(run, *, run_id, source_sha, workflow_id):
    need(type(run) is dict and type(run.get("id")) is int and str(run["id"]) == run_id
         and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
         and run.get("event") == "workflow_dispatch"
         and run.get("head_sha") == source_sha and run.get("workflow_id") == workflow_id
         and run.get("path") == WORKFLOW_PATH, "fixedtxt-delivery-exact-run")
    repo, head, actor = run.get("repository"), run.get("head_repository"), run.get("actor")
    need(type(repo) is dict and type(repo.get("id")) is int and repo["id"] > 0
         and repo.get("full_name") == REPO and type(head) is dict and head.get("id") == repo["id"],
         "fixedtxt-delivery-repository")
    need(type(actor) is dict and actor.get("type") == "User"
         and type(actor.get("id")) is int and actor["id"] > 0,
         "fixedtxt-delivery-external-user")
    return repo["id"], actor["id"]


def checked_delivery_runs(target, delivery, workflow, *, current):
    workflow_id = checked_workflow(workflow)
    original = checked_run(target, run_id=current["run_id"],
                           source_sha=current["source_sha"], workflow_id=workflow_id)
    need(type(delivery.get("id")) is int and str(delivery["id"]) != current["run_id"],
         "fixedtxt-delivery-must-be-independent-invocation")
    sender = checked_run(delivery, run_id=str(delivery["id"]),
                         source_sha=current["source_sha"], workflow_id=workflow_id)
    need(sender == original, "fixedtxt-delivery-actor-or-repository-changed")
    timestamps = [run.get("created_at") for run in (target, delivery)]
    need(all(type(value) is str and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value)
             for value in timestamps), "fixedtxt-original-server-times")
    need(datetime.fromisoformat(timestamps[1]) >= datetime.fromisoformat(timestamps[0]),
         "fixedtxt-delivery-precedes-target")
    return workflow_id, original[0], original[1]


def checked_artifact(item, *, run, expected_name, maximum):
    need(type(item) is dict and item.get("name") == expected_name
         and type(item.get("id")) is int and item["id"] > 0
         and item.get("expired") is False and type(item.get("size_in_bytes")) is int
         and 0 < item["size_in_bytes"] <= maximum
         and type(item.get("digest")) is str and item["digest"].startswith("sha256:")
         and SHA.fullmatch(item["digest"][7:]), "fixedtxt-delivery-artifact-metadata")
    origin = item.get("workflow_run")
    need(type(origin) is dict and origin.get("id") == run["id"]
         and origin.get("head_sha") == run["head_sha"]
         and origin.get("repository_id") == run["repository"]["id"]
         and origin.get("head_repository_id") == run["repository"]["id"],
         "fixedtxt-delivery-artifact-run")
    return item


def artifact_identity(item):
    # The list and individual endpoints may include different link fields.
    # Every security-relevant original identity must remain exactly equal.
    need(type(item) is dict and type(item.get("workflow_run")) is dict,
         "fixedtxt-delivery-selected-artifact-disappeared")
    fields = ("id", "name", "size_in_bytes", "digest", "expired", "created_at")
    return {**{name: item.get(name) for name in fields}, "workflow_run": {
        name: item["workflow_run"].get(name)
        for name in ("id", "head_sha", "repository_id", "head_repository_id")}}


def delivery_members(archive, *, item):
    need(type(archive) is bytes and len(archive) == item["size_in_bytes"] <= DELIVERY_MAX
         and sha(archive) == item["digest"][7:], "fixedtxt-delivery-original-zip")
    names = {"dispatch.original.json", "target-run.original.json", "delivery-run.original.json",
             "workflow.original.json", "review-artifact.original.json", "handoff.original.json"}
    with zipfile.ZipFile(io.BytesIO(archive)) as packet:
        entries = packet.infolist()
        need(len(entries) == len(names) and {entry.filename for entry in entries} == names,
             "fixedtxt-delivery-closed-member-roster")
        need(sum(entry.file_size for entry in entries) <= DELIVERY_MEMBERS_MAX,
             "fixedtxt-delivery-uncompressed-bound")
        originals = {}
        for entry in entries:
            need(not entry.is_dir() and not entry.flag_bits & 1
                 and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                 and 0 < entry.file_size <= DELIVERY_MEMBERS_MAX, "fixedtxt-delivery-regular-member")
            with packet.open(entry) as stream:
                raw = stream.read(entry.file_size + 1)
            need(len(raw) == entry.file_size, "fixedtxt-delivery-member-length")
            originals[entry.filename] = raw
        need(packet.testzip() is None, "fixedtxt-delivery-CRC")
    return originals


def validate_delivery_originals(originals, *, current, target, delivery, workflow,
                               review_artifact, held_plan, held_runtime, held_cycle, session=None):
    import native_fixedtxt_g2_admission as fixed
    live_identity = checked_delivery_runs(target, delivery, workflow, current=current)
    need(delivery.get("status") == "completed" and delivery.get("conclusion") == "success",
         "fixedtxt-delivery-not-terminal-success")
    need(target.get("status") == "in_progress", "fixedtxt-original-run-no-longer-live")
    recorded_target = strict(originals["target-run.original.json"])
    recorded_delivery = strict(originals["delivery-run.original.json"])
    recorded_workflow = strict(originals["workflow.original.json"])
    recorded_identity = checked_delivery_runs(recorded_target, recorded_delivery, recorded_workflow, current=current)
    need(recorded_delivery["id"] == delivery["id"] and recorded_workflow["id"] == workflow["id"],
         "fixedtxt-delivery-recorded-origin")
    need(recorded_identity == live_identity
         and recorded_target["created_at"] == target["created_at"]
         and recorded_delivery["created_at"] == delivery["created_at"]
         and recorded_target.get("status") == recorded_delivery.get("status") == "in_progress"
         and recorded_delivery.get("conclusion") is None,
         "fixedtxt-delivery-original-server-identity")
    handoff = strict(originals["handoff.original.json"])
    request_sha256 = None if session is None else session["binding"]["request"]["sha256"]
    expected_session = {} if session is None else {"request_sha256": request_sha256}
    need(all(handoff.get(key) == value for key, value in expected_session.items()),
         "fixedtxt-delivery-independent-case-request")
    need(set(handoff) == set(expected_session) | {"schema", "target_run_id", "delivery_run_id", "source_sha", "workflow_id",
                         "review_artifact_id", "review_artifact_sha256", "dispatch_original"}
         and handoff["schema"] == "motionwright.native.fixedtxt-workflow-delivery/1"
         and handoff["target_run_id"] == current["run_id"]
         and handoff["delivery_run_id"] == str(delivery["id"])
         and handoff["source_sha"] == current["source_sha"]
         and handoff["workflow_id"] == workflow["id"], "fixedtxt-delivery-handoff-binding")
    review = strict(originals["review-artifact.original.json"])
    for observed in (review, review_artifact):
        checked_artifact(observed, run=target,
            expected_name="motionwright-fixedtxt-preparation-" + current["run_id"] + "-1"
                + ("" if request_sha256 is None else "-" + request_sha256),
            maximum=256 * 1024 * 1024)
        need(observed["id"] == handoff["review_artifact_id"]
             and observed["digest"] == "sha256:" + handoff["review_artifact_sha256"],
             "fixedtxt-delivery-reviewed-original-artifact")
        need(type(observed.get("created_at")) is str
             and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", observed["created_at"])
             and datetime.fromisoformat(observed["created_at"]) <= datetime.fromisoformat(delivery["created_at"]),
             "fixedtxt-review-original-must-precede-external-delivery")
    raw = originals["dispatch.original.json"]
    need(original_descriptor(raw) == handoff["dispatch_original"], "fixedtxt-delivery-original-dispatch")
    return fixed.admit_inputs(raw, held_plan, held_runtime, held_cycle,
                              current=current, runner_temp=os.environ["RUNNER_TEMP"])


class ReadOnlyApi:
    """Bounded GET originals; errors consume the call and are never replayed."""
    def __init__(self, directory, deadline):
        self.path = Path(directory) / "transport.requests.ndjson"
        self.stream = self.path.open("xb")
        self.deadline = deadline
        self.bytes = 0

    def get(self, route, *, maximum=DELIVERY_MAX):
        need(route.startswith("repos/" + REPO + "/actions/")
             and "\n" not in route and "#" not in route, "fixedtxt-closed-API-route")
        remaining = self.deadline - time.monotonic()
        need(remaining > 0, "fixedtxt-original-deadline-before-GET")
        # Reserve storage BEFORE issuing the read so even overflow bytes and
        # an unknown exit can be durably retained inside the original cap.
        need(self.bytes + ((maximum + 8192 + 2) * 4 + 2) // 3 + 4096 <= API_JOURNAL_MAX,
             "fixedtxt-API-journal-reservation-before-GET")
        command = ["/usr/bin/gh", "api", "--method", "GET", route]
        env = {"PATH": "/usr/bin:/bin", "GH_HOST": "github.com", "GH_TOKEN": os.environ.get("GH_TOKEN", "")}
        need(env["GH_TOKEN"], "fixedtxt-read-token-unavailable")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, close_fds=True)
        readers, chunks, overflow = selectors.DefaultSelector(), {"stdout": bytearray(), "stderr": bytearray()}, False
        deadline = min(self.deadline, time.monotonic() + 60)
        try:
            for name in chunks:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                readers.register(pipe, selectors.EVENT_READ, name)
            while readers.get_map() or process.poll() is None:
                if time.monotonic() >= deadline:
                    process.kill()
                    raise WorkflowRejected("fixedtxt-GET-original-deadline-no-retry")
                for key, _ in readers.select(min(0.05, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        readers.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    bound = maximum if key.data == "stdout" else 8192
                    chunks[key.data].extend(data[:max(0, bound + 1 - len(chunks[key.data]))])
                    if len(chunks[key.data]) > bound:
                        overflow = True
                        process.kill()
                        raise WorkflowRejected("fixedtxt-GET-overflow-no-retry")
            code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=max(0.001, min(1, self.deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
            for key in list(readers.get_map().values()):
                key.fileobj.close()
            readers.close()
            row = canonical({"argv": command, "exit_code": process.poll(), "overflow": overflow,
                "stdout_base64": base64.b64encode(chunks["stdout"]).decode(),
                "stderr_base64": base64.b64encode(chunks["stderr"]).decode(), "automatic_retry": False})
            need(self.bytes + len(row) <= API_JOURNAL_MAX, "fixedtxt-API-original-journal-bound")
            self.stream.write(row)
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.bytes += len(row)
        need(code == 0 and not overflow, "fixedtxt-GET-failed-no-retry")
        return bytes(chunks["stdout"])

    def close(self):
        self.stream.close()


def original_clock(raw, request, *, current):
    value = strict(raw)
    need(set(value) == {"schema", "context", "boot_id", "started_monotonic", "deadline_monotonic",
                       "request_sha256"}
         and value["schema"] == "motionwright.native.fixedtxt-original-clock/1"
         and value["context"] == current
         and value["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
         "fixedtxt-original-clock-context")
    start, total = value["started_monotonic"], value["deadline_monotonic"]
    need(all(type(n) in (int, float) and math.isfinite(n) for n in (start, total))
         and 0 <= start <= time.monotonic() < total
         and abs(total - start - request["limits"]["attempt_seconds"]) < 0.000001,
         "fixedtxt-original-clock-deadline")
    return value


def delivery_poll_delay(deadline):
    """One source-bound read every 20s at most, never beyond the original clock.

    Every GET remains a separately retained original. No extra API quota,
    clock extension, result replay, or Native admission is introduced.
    """
    return min(DELIVERY_POLL_SECONDS, max(0.0, deadline - time.monotonic()))


def wait_for_owner(*, current, request, clock, directory, originals, review, session=None):
    import native_fixedtxt_g2_admission as fixed
    from ci_private_evidence import new_bytes, new_json
    import native_fixedtxt_session as serial
    wait_until = serial.effective_deadline(clock, session) - request["limits"]["custody_seconds"] - request["native_seconds"]
    request_sha256 = None if session is None else session["binding"]["request"]["sha256"]
    need(time.monotonic() < wait_until, "fixedtxt-preparation-exhausted-native-reserve")
    api = ReadOnlyApi(directory, wait_until)
    try:
        workflow = strict(api.get(WORKFLOW_ENDPOINT))
        target = strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"]))
        checked_run(target, run_id=current["run_id"], source_sha=current["source_sha"],
                    workflow_id=checked_workflow(workflow))
        route = "repos/" + REPO + "/actions/artifacts?name=" + artifact_name(current["run_id"], request_sha256) + "&per_page=2&page=1"
        while True:
            need(time.monotonic() < wait_until, "fixedtxt-external-delivery-not-observed-before-deadline")
            item = select_delivery_artifact(strict(api.get(route)), current["run_id"], request_sha256)
            if item is not None:
                break
            time.sleep(delivery_poll_delay(wait_until))
        # Selection is now consumed, before content/provenance checks. No fallback.
        new_json(Path(directory) / "transport.consume.intent.json",
                 {"artifact_id": item["id"], "context": current, "automatic_retry": False})
        delivery_id = item.get("workflow_run", {}).get("id")
        need(type(delivery_id) is int and delivery_id > 0, "fixedtxt-delivery-source-run")
        delivery_route = "repos/" + REPO + "/actions/runs/" + str(delivery_id)
        while True:
            delivery = strict(api.get(delivery_route))
            checked_delivery_runs(target, delivery, workflow, current=current)
            if delivery.get("status") == "completed":
                break
            need(delivery.get("status") in {"queued", "in_progress"}, "fixedtxt-delivery-run-state")
            time.sleep(delivery_poll_delay(wait_until))
        checked_artifact(item, run=delivery, expected_name=artifact_name(current["run_id"], request_sha256), maximum=DELIVERY_MAX)
        fresh = select_delivery_artifact(strict(api.get(route)), current["run_id"], request_sha256)
        need(artifact_identity(fresh) == artifact_identity(item), "fixedtxt-delivery-selection-changed")
        actual = strict(api.get("repos/" + REPO + "/actions/artifacts/" + str(item["id"])))
        need(artifact_identity(actual) == artifact_identity(item), "fixedtxt-delivery-metadata-changed")
        archive = api.get("repos/" + REPO + "/actions/artifacts/" + str(item["id"]) + "/zip")
        new_bytes(Path(directory) / "transport.original.zip", archive)
        members = delivery_members(archive, item=item)
        target = strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"]))
        reviewed = strict(api.get("repos/" + REPO + "/actions/artifacts/" + str(review["artifact_id"])))
        need(reviewed.get("digest") == "sha256:" + review["artifact_sha256"], "fixedtxt-preparation-upload-digest")
        held = validate_delivery_originals(members, current=current, target=target, delivery=delivery,
            workflow=workflow, review_artifact=reviewed, held_plan=originals["plan"],
            held_runtime=originals["runtime"], held_cycle=originals["cycle"], session=session)
        final_selection = select_delivery_artifact(strict(api.get(route)), current["run_id"], request_sha256)
        need(artifact_identity(final_selection) == artifact_identity(item),
             "fixedtxt-delivery-not-unique-before-owner")
        need(time.monotonic() < wait_until, "fixedtxt-delivery-consumed-native-reserve")
        new_bytes(Path(directory) / "dispatch.original.json", held.dispatch_raw)
        new_json(Path(directory) / "transport.terminal.json", {
            "context": current, "delivery_run_id": delivery_id, "artifact_id": item["id"],
            "archive_sha256": sha(archive), "dispatch_original": original_descriptor(held.dispatch_raw),
            "review_artifact_id": review["artifact_id"], "original_deadline_monotonic": clock["deadline_monotonic"],
            "native_effects": 0, "automatic_retry": False})
        return held
    finally:
        api.close()


def api_stream_file_limit():
    import resource
    prior = resource.getrlimit(resource.RLIMIT_FSIZE)
    maximum = min([DELIVERY_MAX, *(bound for bound in prior if bound != resource.RLIM_INFINITY)])
    need(0 < maximum <= DELIVERY_MAX, "fixedtxt-delivery-original-file-limit")
    return maximum


def limit_api_stream_files():
    """The courier applies this only in its gh child, before exec or bytes."""
    import resource
    maximum = api_stream_file_limit()
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))


def preflight_request_capacity(value, *, source_zip_bytes=0, session=False):
    """Reject an already impossible declaration before checkout/install effects.

    This pure subset also runs in the inline bootstrap. Its closed role counts
    are compared with the existing R28 selector after checkout. Unknown future
    originals are still checked by the unchanged final plan validator.
    """
    fixture = value["fixture"]
    counts_by_fixture = {
        "fixed-causal": 4, "flow-control-base": 3, "flow-control-with-fixed": 3,
        "flow-text-control": 4, "fixed-multirun": 4, "fixed-multirun-control": 4,
        "fixed-transform-clip": 3, "fixed-camera-scene": 4, "fixed-counter-captions": 7,
        "fixed-layer-behind": 4, "fixed-layer-equal": 4, "fixed-layer-front": 4,
    }
    need(type(fixture) is str and fixture in counts_by_fixture, "fixedtxt-capacity-closed-fixture")
    once = (
        "authored-film", "canvas-observations", "case-final-custody", "case-inputs",
        "compile-component", "compiled-project", "cycle-inputs", "cycle-observation",
        "final-custody", "fixture-expectations", "fixture-set", "font-inventory",
        "font-readiness", "generated-inventory", "host-capture", "host-readback",
        "host-readback-intent", "host-return-snapshot", "host-session", "host-source-binding",
        "host-stdin", "host-terminal", "native-frame-manifest", "native-observations",
        "native-observations-receipt", "native-result", "owner-close", "owner-closed-original",
        "owner-shutdown-intent", "owner-shutdown-terminal", "owner-workspace",
        "owner-workspace-terminal", "project-snapshot-original", "selected-originals", "selection-plan",
    )
    counts = {role: 1 for role in once}
    counts["native-png"] = 180
    # The nine bundled originals are selected before R28, including their
    # declared per-file bounds in the unchanged whole-capsule accounting.
    counts["font-original"] = 9
    counts.update({role: counts_by_fixture[fixture] for role in
                   ("selected-png", "selected-rgba", "decoder-stderr", "decoder-terminal")})
    roles, limits = value["role_limits"], value["limits"]
    need(type(roles) is dict and set(roles) == set(counts)
         and all(type(bound) is int and 0 <= bound <= 256 * 1024 * 1024
                 and (bound > 0 or role == "decoder-stderr") for role, bound in roles.items()),
         "fixedtxt-capacity-closed-role-bounds")
    need(roles["selection-plan"] == 512 * 1024 and roles["selected-rgba"] == 1920 * 1080 * 4,
         "fixedtxt-capacity-unchanged-full-originals")
    need(type(limits) is dict and set(limits) == {"capsule_bytes", "files", "attempt_seconds", "custody_seconds"}
         and type(limits["capsule_bytes"]) is int and 0 < limits["capsule_bytes"] <= 256 * 1024 * 1024
         and type(limits["files"]) is int and 0 < limits["files"] <= 4096,
         "fixedtxt-capacity-existing-limits")
    bounds = [roles[role] for role, count in counts.items() for _ in range(count)]
    # The exact twelve fixed workflow additions in make_plan: runtime input,
    # Broker receipt, five supervision and five transport originals. Reserve
    # the three starting originals too; their existing maxima are unchanged.
    bounds.extend([65536, 8 * 1024 * 1024,
                   65536, 65536, 1024 * 1024 + 1, 1024 * 1024 + 1, 65536,
                   1024 * 1024, 65536, 65536, 65536, 65536,
                   49152, 65536, 16384])
    # Retained historical GREEN, derived RED, and reversible observer proof.
    bounds.extend([128 * 1024, 128 * 1024, 16384])
    # Six explicit pre-owner diagnostics, 60 KiB raw. The existing physical
    # selection and all PNG/RGBA bounds remain unchanged, including captions.
    bounds.extend([4096, 16384, 4096, 16384, 16384, 4096])
    need(type(session) is bool, "fixedtxt-session-capacity-selection")
    # The five active resource originals fit inside the existing session reserve.
    # A standalone case explicitly reserves those same five bounded metadata files.
    extra_bytes, extra_files = (2 * 1024 * 1024, 192) if session else (5 * 65536, 5)
    need(len(bounds) + extra_files <= limits["files"], "fixedtxt-preparation-declared-file-budget")
    # Same USTAR headers/padding, metadata, record rounding, envelope and
    # public-projection reserve as the existing R28 plan validator.
    total = sum(bounds) + extra_bytes
    tar_bound = sum(512 + ((bound + 511) // 512) * 512 for bound in bounds)
    # Reserve every added original's header and worst-case padding as well.
    tar_bound += extra_bytes + extra_files * 1024
    tar_bound += 512 + 512 * 1024 + 1024
    tar_bound = ((tar_bound + 10239) // 10240) * 10240
    capsule_bound = tar_bound + 8192 + 64 + 512 * 1024
    need(total <= 512 * 1024 * 1024 and capsule_bound <= limits["capsule_bytes"],
         "fixedtxt-preparation-declared-capsule-budget")
    # The intermediate encrypted review uses the same USTAR packer. Reserve
    # its known mandatory subset before setup, not just the final native plan.
    # P/R/Q and starting-original maxima are the existing readers' bounds.
    # Unknown future API/runtime originals still require the exact later check.
    review = value["review_capsule_bytes"]
    stream = value["preparation_stream_bytes"]
    need(type(review) is int and 0 < review <= limits["capsule_bytes"]
         and type(stream) is int and 0 < stream <= 8 * 1024 * 1024
         and type(source_zip_bytes) is int and 0 <= source_zip_bytes <= 256 * 1024 * 1024,
         "fixedtxt-preparation-review-existing-bounds")
    sources = value["sources"]
    review_bounds = [49152, 65536, 16384, 512 * 1024, 65536, 1024 * 1024]
    review_bounds.extend([128 * 1024, 128 * 1024, 16384])
    for key, maximum in (("template_film", 4 * 1024 * 1024), ("managed_project", 4 * 1024 * 1024),
        ("caption_source", 65536), ("red_native", 2 * 1024 * 1024), ("measurement_protocol", 65536)):
        bound = sources[key]["bytes"]
        need(type(bound) is int and 0 < bound <= maximum, "fixedtxt-review-declared-source-size")
        review_bounds.append(bound)
    need(type(sources["project_files"]) is list and len(sources["project_files"]) <= 256,
         "fixedtxt-review-declared-asset-roster")
    for item in sources["project_files"]:
        bound = item["original"]["bytes"]
        need(type(bound) is int and 0 < bound <= 16 * 1024 * 1024, "fixedtxt-review-declared-asset-size")
        review_bounds.append(bound)
    # Fifteen existing preparation children each retain stdout/stderr under
    # the declared ceiling and four metadata originals. Their unknown metadata
    # payloads contribute headers only here; no future size is certified.
    review_bounds.extend([stream, stream, 0, 0, 0, 0] * 15)
    # The intake archive appears verbatim twice: API original + source-kit copy.
    # Before download its size is unknown; after download this same arithmetic
    # runs with the observed length before the second copy or next command.
    review_bounds.extend([source_zip_bytes, source_zip_bytes])
    review_total = sum(review_bounds) + extra_bytes
    review_tar = sum(512 + ((bound + 511) // 512) * 512 for bound in review_bounds)
    review_tar += extra_bytes + extra_files * 1024
    review_tar += 512 + 512 * 1024 + 1024
    review_tar = ((review_tar + 10239) // 10240) * 10240
    # Existing encrypted envelope/public-projection reserves plus the review
    # ZIP's existing 8192 bytes. Keep collect_workflow_originals' 1 MiB reserve.
    review_bound = review_tar + 8192 + 64 + 512 * 1024 + 8192
    need(len(review_bounds) + extra_files <= limits["files"] and review_total <= review - 1024 * 1024
         and review_bound <= review, "fixedtxt-preparation-declared-review-capsule-budget")
    return {"mandatory_role_counts": counts, "declared_files": len(bounds) + extra_files,
            "declared_raw_bytes": total, "declared_capsule_bound": capsule_bound,
            "review_declared_files": len(review_bounds) + extra_files, "review_declared_raw_bytes": review_total,
            "review_declared_capsule_bound": review_bound, "source_zip_observed_bytes": source_zip_bytes,
            "future_original_sizes_verified": False, "session_provenance_reserved": session}


def checked_owner_resources(request):
    """A /2 request explicitly binds the unchanged physical owner ceilings."""
    need(type(request) is dict
         and request.get("schema") == "motionwright.native.fixedtxt-workflow-request/2",
         "fixedtxt-owner-resource-request-version")
    need(type(request.get("owner_resources")) is dict
         and canonical(request["owner_resources"]) == canonical(OWNER_RESOURCE_POLICY),
         "fixedtxt-owner-resource-request-policy")
    return request["owner_resources"]


def checked_request(raw, *, current):
    """Preparation data only. Actual P/R/Q still require an external R28 dispatch."""
    from native_film180_evidence_selection import fixedtxt_required_members, FIXEDTXT_KEYS
    import native_fixedtxt_g2_admission as fixed
    import native_mlt_g2_custody as custody
    value = strict(raw, 49152)
    checked_owner_resources(value)
    need(set(value) == {"schema", "source_sha", "source_tree", "semwright_sha", "semwright_tree",
         "arm", "fixture", "purpose", "admission_id", "limits", "preparation_seconds", "native_seconds",
         "upload_seconds", "attempt_minutes", "preparation_stream_bytes", "review_capsule_bytes", "role_limits", "sources", "owner_resources"}
         and value["schema"] == "motionwright.native.fixedtxt-workflow-request/2",
         "fixedtxt-preparation-request-fields")
    need(value["source_sha"] == current["source_sha"]
         and value["semwright_sha"] == current["semwright_sha"]
         and all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key])
                 for key in ("source_sha", "source_tree", "semwright_sha", "semwright_tree")),
         "fixedtxt-preparation-source-tuple")
    need(value["arm"] in PAIR and value["fixture"] in FIXEDTXT_KEYS
         and value["purpose"] in ("auxiliary-calibration", "candidate24")
         and re.fullmatch(r"[a-z][a-z0-9-]{0,95}", value["admission_id"]),
         "fixedtxt-preparation-one-selection")
    if value["purpose"] == "auxiliary-calibration":
        need(value["arm"] == "RED" and value["fixture"] in CALIBRATION_KEYS,
             "fixedtxt-independent-calibration-input-roster")
    limits = value["limits"]
    need(type(limits) is dict and set(limits) == {"capsule_bytes", "files", "attempt_seconds", "custody_seconds"}
         and all(type(n) is int for n in limits.values())
         and 0 < limits["capsule_bytes"] <= 256 * 1024 * 1024
         and 0 < limits["files"] <= 4096 and 0 < limits["custody_seconds"] < limits["attempt_seconds"] <= 65 * 60
         and limits["attempt_seconds"] % 60 == 0, "fixedtxt-original-existing-job-and-capsule-bounds")
    need(type(value["attempt_minutes"]) is int and 0 < value["attempt_minutes"] <= 65
         and value["attempt_minutes"] * 60 == limits["attempt_seconds"], "fixedtxt-original-job-clock-equality")
    for key in ("preparation_seconds", "native_seconds", "upload_seconds", "preparation_stream_bytes",
                "review_capsule_bytes"):
        need(type(value[key]) is int and value[key] > 0, "fixedtxt-preparation-positive-budget")
    need(value["preparation_seconds"] + value["native_seconds"] + limits["custody_seconds"]
         < limits["attempt_seconds"] and value["upload_seconds"] < limits["custody_seconds"]
         and value["preparation_stream_bytes"] <= 8 * 1024 * 1024
         and value["review_capsule_bytes"] <= limits["capsule_bytes"],
         "fixedtxt-preparation-budget-inside-original-attempt")
    mandatory = fixedtxt_required_members(value["fixture"], fixed.NATIVE_SLOT)
    need(type(value["role_limits"]) is dict and set(value["role_limits"]) == set(mandatory.values())
         and all(type(n) is int and 0 <= n <= 256 * 1024 * 1024
                 for n in value["role_limits"].values()), "fixedtxt-predeclared-original-member-budgets")
    for role, bound in (("selection-plan", 512 * 1024), ("selected-rgba", 1920 * 1080 * 4)):
        need(value["role_limits"][role] == bound, "fixedtxt-unchanged-required-member-budget")
    need(all(n > 0 or role == "decoder-stderr" for role, n in value["role_limits"].items()),
         "fixedtxt-predeclared-member-nonempty")
    sources = value["sources"]
    need(type(sources) is dict and set(sources) == {"artifact", "template_film", "managed_project",
         "caption_source", "red_native", "measurement_protocol", "project_files"},
         "fixedtxt-preparation-original-source-members")
    custody.artifact_descriptor(sources["artifact"])
    seen = set()
    def member(row, maximum):
        need(type(row) is dict and set(row) == {"member", "bytes", "sha256"}, "fixedtxt-source-member-shape")
        custody.safe_member(row["member"])
        need(row["member"] not in seen and type(row["bytes"]) is int and 0 < row["bytes"] <= maximum
             and type(row["sha256"]) is str and SHA.fullmatch(row["sha256"]), "fixedtxt-source-member-original")
        seen.add(row["member"])
    for key, maximum in (("template_film", 4 * 1024 * 1024), ("managed_project", 4 * 1024 * 1024),
        ("caption_source", 65536), ("red_native", 2 * 1024 * 1024), ("measurement_protocol", 65536)):
        member(sources[key], maximum)
    need(sources["red_native"]["sha256"] == HISTORICAL_PAIR["RED"], "fixedtxt-original-reviewed-red-source")
    files = sources["project_files"]
    need(type(files) is list and len(files) <= 256, "fixedtxt-source-project-roster-bound")
    relative = set()
    for item in files:
        need(type(item) is dict and set(item) == {"relative", "original"}, "fixedtxt-source-project-row")
        custody.safe_member(item["relative"])
        need(item["relative"] != "semwright-motion.json" and item["relative"] not in relative,
             "fixedtxt-source-project-relative")
        relative.add(item["relative"])
        member(item["original"], 16 * 1024 * 1024)
    need(sources["managed_project"]["bytes"] + sum(item["original"]["bytes"] for item in files)
         <= 32 * 1024 * 1024, "fixedtxt-original-source-assets-budget")
    capacity = preflight_request_capacity(value)
    actual_counts = {role: list(mandatory.values()).count(role) for role in set(mandatory.values())}
    need(capacity["mandatory_role_counts"] == actual_counts, "fixedtxt-capacity-existing-roster-changed")
    return value


def workflow_context(environment=None):
    env = os.environ if environment is None else environment
    need(all(env.get(key) == expected for key, expected in (("GITHUB_REPOSITORY", REPO),
        ("GITHUB_ACTIONS", "true"), ("CI", "true"), ("RUNNER_OS", "Linux"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_RUN_ATTEMPT", "1"),
        ("MW_MLT_SCOPE", SCOPE))), "fixedtxt-existing-workflow-selection")
    import native_mlt_g2_observation as g2
    current = {"source_sha": env.get("GITHUB_SHA"), "semwright_sha": g2.SEMWRIGHT_PIN,
               "run_id": env.get("GITHUB_RUN_ID"), "run_attempt": "1"}
    from ci_private_evidence import validate_context
    validate_context(current)
    return current


def owned_layout(current):
    import native_fixedtxt_g2_admission as fixed
    layout = fixed.owner_layout(current=current, runner_temp=os.environ["RUNNER_TEMP"])
    return {key: Path(value) for key, value in layout.items()}


def load_original_request(current):
    from ci_private_evidence import regular
    control = owned_layout(current)["control_root"]
    raw = regular(control / "request.original.json", 49152)[0]
    request = checked_request(raw, current=current)
    clock_raw = regular(control / "clock.original.json", 65536)[0]
    clock = original_clock(clock_raw, request, current=current)
    need(clock["request_sha256"] == sha(raw), "fixedtxt-original-request-clock-pin")
    return raw, request, clock_raw, clock


def admitted_original_clock(plan, *, current, runner_temp, include_session=False):
    import native_fixedtxt_g2_admission as fixed
    import native_evidence_projection as core
    layout = fixed.owner_layout(current=current, runner_temp=runner_temp)
    handles = core.RootHandles({"control": layout["control_root"]})
    try:
        rows = {(row["root"], row["relative_path"]): row for row in plan["members"]}
        originals = {}
        for name, maximum in (("request.original.json", 49152), ("clock.original.json", 65536)):
            row = rows.get(("control", name))
            need(row is not None and row["role"] == "input-original"
                 and type(row["expected_sha256"]) is str and 0 < row["maximum"] <= maximum,
                 "fixedtxt-original-clock-requires-independent-plan-pin")
            raw, identity = handles.read("control", core.member_path(name), row["maximum"])
            need(sha(raw) == row["expected_sha256"], "fixedtxt-reviewed-clock-original-changed")
            originals[name] = raw
        need(handles.stable_roots(), "fixedtxt-reviewed-clock-roots-changed")
    finally:
        handles.close()
    request = checked_request(originals["request.original.json"], current=current)
    clock = original_clock(originals["clock.original.json"], request, current=current)
    need(request["limits"] == plan["limits"] and request["arm"] == plan["arm"]
         and request["fixture"] == plan["fixture"]
         and clock["request_sha256"] == sha(originals["request.original.json"]),
         "fixedtxt-independent-clock-plan-binding")
    import native_fixedtxt_session as serial
    session = serial.clock_from_plan(plan, current=current, runner_temp=runner_temp,
        request_raw=originals["request.original.json"], clock_raw=originals["clock.original.json"])
    need(session is not None or not Path(runner_temp).name.startswith("fx"),
         "fixedtxt-session-required-clock-originals")
    need(time.monotonic() < serial.effective_deadline(clock, session), "fixedtxt-session-reviewed-deadline")
    return (clock, request, session) if include_session else (clock, request)


def current_session(current, request_raw=None, clock_raw=None):
    import native_fixedtxt_session as serial
    control = owned_layout(current)["control_root"]
    if request_raw is None:
        from ci_private_evidence import regular
        request_raw = regular(control / "request.original.json", 49152)[0]
        clock_raw = regular(control / "clock.original.json", 65536)[0]
    session = serial.clock_from_control(control, request_raw, clock_raw,
        current=current, runner_temp=os.environ["RUNNER_TEMP"])
    need(session is not None or not Path(os.environ["RUNNER_TEMP"]).name.startswith("fx"),
         "fixedtxt-session-required-clock-originals")
    return session


def read_git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_GRAFT_FILE=os.devnull)
    command = ["/usr/bin/git", "--no-replace-objects", "-C", str(root), "-c", "core.fsmonitor=false", *args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=20, env=env, check=False)
    need(result.returncode == 0 and len(result.stderr) <= 8192, "fixedtxt-readonly-Git-failed")
    return result.stdout


def check_sources(current, request, *, physical_arm):
    import native_mlt_g2_custody as custody
    from ci_private_evidence import regular
    root, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
    source = custody.source_guard(root, current, scenario=SCOPE)
    need(source["tree"] == request["source_tree"] and not read_git(root, "diff", "--cached", "--name-only"),
         "fixedtxt-current-source-index-or-tree")
    need(read_git(sdk, "rev-parse", "HEAD").decode().strip() == current["semwright_sha"]
         and read_git(sdk, "rev-parse", "HEAD^{tree}").decode().strip() == request["semwright_tree"]
         and not read_git(sdk, "diff", "--cached", "--name-only"), "fixedtxt-current-SDK-index-or-tree")
    changed = set(filter(None, read_git(sdk, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                                      "--name-only", "-z", "HEAD", "--").decode().split("\0")))
    need(changed <= ({NATIVE_PATH} if physical_arm == "RED" else set()), "fixedtxt-SDK-unreviewed-source-change")
    need(sha(read_git(sdk, "show", current["semwright_sha"] + ":" + NATIVE_PATH)) == PAIR["GREEN"]
         and sha(regular(sdk / NATIVE_PATH, 2 * 1024 * 1024)[0]) == PAIR[physical_arm]
         and sha(regular(sdk / FIXTURE_HELPER, 2 * 1024 * 1024)[0]) == FIXTURE_HELPER_SHA256,
         "fixedtxt-reviewed-arm-and-fixture-source")
    return source


class PreparationClock:
    """The existing child supervisor consumes the original setup interval."""
    def __init__(self, original, request, session=None):
        import native_fixedtxt_session as serial
        self.started = original["started_monotonic"]
        self.total = serial.effective_deadline(original, session)
        self.until = serial.preparation_deadline(original, request, session)
        self.close_until = self.total - request["upload_seconds"]
        self.used = set()

    def reserve(self, phase):
        need(re.fullmatch(r"prepare-[0-9]{2}", phase) and phase not in self.used,
             "fixedtxt-preparation-no-retry")
        self.used.add(phase)
        # The sole privileged handoff is closure of an installation that may
        # already have failed. It may consume the original close reserve, but
        # cannot extend the attempt or authorize another setup/native effect.
        deadline = self.close_until if phase == "prepare-13" else self.until
        need(time.monotonic() < deadline, "fixedtxt-original-preparation-deadline")
        return deadline


def preparation_commands(request, current):
    """Closed provider/build/runtime route; never a renderer or acceptance probe."""
    root, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
    python = sys.executable
    control = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
    installed = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-installation-" + current["run_id"] + "-1")
    runtime = sdk / "integrations/motion-canvas/runtime"
    scripts = root / "tooling"
    common_env = ["PATH=/usr/sbin:/usr/bin:/sbin:/bin", "CI=true", "GITHUB_ACTIONS=true", "RUNNER_OS=Linux",
        "RUNNER_TEMP=" + os.environ["RUNNER_TEMP"], "GITHUB_WORKSPACE=" + str(root),
        "GITHUB_SHA=" + current["source_sha"], "GITHUB_RUN_ID=" + current["run_id"],
        "GITHUB_RUN_ATTEMPT=1", "GITHUB_EVENT_NAME=workflow_dispatch", "MW_MLT_SCOPE=" + SCOPE,
        "MOTIONWRIGHT_APPROVED_MLT_G2=1", "PYTHONDONTWRITEBYTECODE=1"]
    import native_mlt_g2_custody as custody
    bootstrap = custody.runtime_preparation_dispatch(
        (owned_layout(current)["control_root"] / "runtime-bootstrap.original.json").read_bytes(), current, scenario=SCOPE)
    return [
        [python, "-B", "-E", "-s", str(scripts / "native_fixedtxt_workflow.py"), "--prepare-source-inputs"],
        ["sudo", "-n", "apt-get", "update"],
        ["sudo", "-n", "apt-get", "install", "--no-install-recommends", "-y", "bubblewrap",
         "apparmor-profiles", "apparmor-utils", "libmlt7=7.22.0-1build6", "melt=7.22.0-1build6",
         "ffmpeg", "libpipewire-0.3-dev", "pkg-config", "clang"],
        ["bash", str(sdk / "scripts/dev/ci-driver-bwrap-profile.sh"),
         str(owned_layout(current)["control_root"] / "sandbox-profile.original.txt")],
        ["npm", "ci", "--ignore-scripts"],
        ["npx", "--no-install", "playwright", "install", "--with-deps", "firefox"],
        ["cargo", "build", "--manifest-path", str(sdk / "Cargo.toml"), "--locked", "-p", "semwright-daemon",
         "-p", "semwright-cli", "--bins",
         "--features", "semwright-daemon/private-host-stdin-custody"],
        ["cargo", "build", "--manifest-path", str(sdk / "Cargo.toml"), "--locked",
         "-p", "semwright-plugin-host", "--bin", "semwright-sandbox",
         "-p", "semwright-driver-motion-canvas", "--bin", "semwright-motion-canvas-driver",
         "-p", "semwright-mlt-video-driver", "--bin", "semwright-mlt-video-driver",
         "-p", "semwright-mlt-video-driver", "--bin", "semwright-mlt-runtime-runner"],
        ["cargo", "build", "--manifest-path", str(sdk / "Cargo.toml"), "--locked",
         "-p", "semwright-driver-motion-canvas", "--example", "fixed_text_compile_v4"],
        ["cargo", "build", "--manifest-path", str(root / "Cargo.toml"), "--locked",
         "-p", "motionwright-native", "--example", "native-film180-e2e"],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "prepare"],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "guard"],
        ["sudo", "-n", "env", "-i", *common_env, python, "-B", "-E", "-s",
         str(scripts / "native_mlt_runtime_admission.py"), "--original-source", str(control / "intake/original_c_source"),
         "--repaired-source", str(control / "intake/repaired_c_source"),
         "--compiled-module", str(control / "intake/compiled_module"), "--compiled-module-sha256",
         "4e9b6d9ec6efe97807ba9c53bfdca8299e60e5582c40eca355f866a058ed7eac",
         "--compiler-receipt", str(control / "intake/compiler_receipt_path"), "--compiler-receipt-sha256",
         "e16d8d73cdb780cd8fb26bc2b43c2a27db0d8972050c508c9d6432e67b5018a8",
         "--installed-original-sha256", bootstrap["official_runtime"]["known_original_module_sha256"],
         "--admit", "--new-runner-private-evidence", str(installed)],
        ["sudo", "-n", "env", "-i", *common_env, python, "-B", "-E", "-s",
         str(scripts / "native_mlt_g2_custody.py"), "handoff", "--uid", str(os.getuid()), "--gid", str(os.getgid())],
        [python, "-B", "-E", "-s", str(scripts / "native_mlt_g2_custody.py"), "inputs"],
    ]


def execute_preparation_commands(request, current, clock):
    import native_fixedtxt_g2_supervisor as owner
    from ci_private_evidence import new_json
    control = owned_layout(current)["control_root"]
    commands = preparation_commands(request, current)
    need(len(commands) == 15, "fixedtxt-review-existing-preparation-roster-changed")
    directory = control / "preparation"
    directory.mkdir(mode=0o700, exist_ok=False)
    child_environment = dict(os.environ)
    child_environment["MW_MLT_G2_ADMISSION"] = (control / "runtime-bootstrap.original.json").read_text()
    group = owner.OwnerCgroup(current["run_id"], "preparation")
    timeline = PreparationClock(clock, request, current_session(current))
    new_json(control / "preparation-intent.original.json", {"context": current, "commands": commands,
        "attempt_started_monotonic": timeline.started, "preparation_deadline_monotonic": timeline.until,
        "attempt_deadline_monotonic": timeline.total, "stream_limit": request["preparation_stream_bytes"],
        "automatic_retry": False, "native_effects": 0})
    try:
        root, sdk = Path(os.environ["GITHUB_WORKSPACE"]), Path(os.environ["SEMWRIGHT_CHECKOUT"])
        previous = Path.cwd()
        admission_error = None
        for number, argv in enumerate(commands):
            cwd = (sdk / "integrations/motion-canvas/runtime" if argv[0] in {"npm", "npx"}
                   else sdk if argv[0] == "cargo" and argv[3] == str(sdk / "Cargo.toml") else root)
            new_json(directory / ("prepare-%02d.cwd.json" % number), {"cwd": str(cwd)})
            try:
                os.chdir(cwd)
                try:
                    result = owner._run_original(argv, environment=child_environment, directory=directory,
                        phase="prepare-%02d" % number, clock=timeline, containment=group,
                        stream_limit=request["preparation_stream_bytes"])
                    need(result["original_complete"] and result["exit_code"] == 0
                         and not result["overflow"] and not result["deadline_exceeded"]
                         and result.get("native_outcome") == "NOT_INFERRED"
                         and "descendants_survived_wrapper" not in result,
                         "fixedtxt-original-preparation-incomplete-no-retry")
                except BaseException as error:
                    if number == 12:  # exactly the existing installation command
                        admission_error = error
                    elif admission_error is not None:
                        admission_error.add_note("Original privileged handoff also failed; nothing retried.")
                        raise admission_error from error
                    else:
                        raise
            finally:
                os.chdir(previous)
            if number == 13 and admission_error is not None:
                raise admission_error  # preserve the copied failure originals; no inputs/native stage
    finally:
        group.close()


def intake_sources(request, current):
    import native_mlt_g2_custody as custody
    from ci_private_evidence import new_bytes
    layout = owned_layout(current)
    inputs = layout["inputs_root"]
    inputs.mkdir(mode=0o700, exist_ok=False)
    intake = layout["control_root"] / "source-intake"
    intake.mkdir(mode=0o700, exist_ok=False)
    sources = request["sources"]
    archive = custody.download_artifact(sources["artifact"], "source", intake)
    need(len(archive) <= request["review_capsule_bytes"], "fixedtxt-source-zip-inside-predeclared-capsule-bound")
    preflight_request_capacity(request, source_zip_bytes=len(archive),
                               session=current_session(current) is not None)
    new_bytes(inputs / "source-kit.original.zip", archive)
    originals = {}
    def retain(row, name):
        raw = custody.zip_member(archive, row["member"], row["sha256"], row["bytes"])
        need(len(raw) == row["bytes"], "fixedtxt-original-source-length")
        destination = inputs / name
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        new_bytes(destination, raw)
        return {"path": str(destination), "bytes": len(raw), "sha256": sha(raw)}
    for key in ("template_film", "managed_project", "caption_source", "red_native", "measurement_protocol"):
        originals[key] = retain(sources[key], "source/" + key + ".original")
    originals["project_files"] = [{"relative": item["relative"],
        "original": retain(item["original"], "source/project/" + item["relative"])} for item in sources["project_files"]]
    project = strict(Path(originals["managed_project"]["path"]).read_bytes(), 4 * 1024 * 1024)
    need(type(project.get("assets")) is list and len(project["assets"]) == len(originals["project_files"]),
         "fixedtxt-authored-Project-asset-roster")
    rows = {item["relative"]: item["original"] for item in originals["project_files"]}
    need(set(rows) == {asset["path"] for asset in project["assets"]}
         and all((rows[asset["path"]]["bytes"], rows[asset["path"]]["sha256"])
                 == (asset["bytes"], asset["sha256"]) for asset in project["assets"]),
         "fixedtxt-authored-Project-original-assets")
    need(Path(originals["caption_source"]["path"]).read_bytes() == b"An editable caption stays in place.",
         "fixedtxt-original-caption")
    return originals


def apply_reviewed_arm(request, sources):
    """Derive both arms from original bytes, then overlay only this disposable SDK."""
    from ci_private_evidence import regular, new_bytes
    import native_fixedtxt_observer_overlay as overlay
    sdk = Path(os.environ["SEMWRIGHT_CHECKOUT"])
    path = sdk / NATIVE_PATH
    original, observed = regular(path, overlay.MAX_SOURCE_BYTES)
    need(PAIR == overlay.OBSERVED and HISTORICAL_PAIR == overlay.HISTORICAL,
         "fixedtxt-reviewed-observer-pair")
    need(sha(original) == PAIR["GREEN"], "fixedtxt-original-GREEN-source-before-overlay")
    red_path = Path(sources["red_native"]["path"])
    inputs = red_path.parents[1]
    need(red_path == inputs / overlay.BUNDLE_PATHS["historical_red"], "fixedtxt-original-RED-custody-path")
    historical_red = regular(red_path, overlay.MAX_SOURCE_BYTES)[0]
    bundle = overlay.build_bundle(historical_red, original)
    for relative, raw in bundle.items():
        if relative != overlay.BUNDLE_PATHS["historical_red"]:
            new_bytes(inputs / relative, raw)
    if request["arm"] == "RED":
        replacement = bundle[overlay.BUNDLE_PATHS["observed_red"]]
        need(sha(replacement) == PAIR["RED"], "fixedtxt-derived-RED-source-before-overlay")
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            need(before.st_nlink == 1 and stat.S_ISREG(before.st_mode)
                 and (before.st_dev, before.st_ino) == (path.stat().st_dev, path.stat().st_ino),
                 "fixedtxt-physical-native-file-replaced")
            os.ftruncate(fd, 0)
            with os.fdopen(fd, "wb", closefd=False) as output:
                output.write(replacement)
                output.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
    need(sha(regular(path, 2 * 1024 * 1024)[0]) == PAIR[request["arm"]],
         "fixedtxt-reviewed-overlay-readback")


def make_cycle(request, current, sources):
    import native_film180_original_g2 as wrapper
    from ci_private_evidence import new_bytes
    layout = owned_layout(current)
    sdk = Path(os.environ["SEMWRIGHT_CHECKOUT"])
    need(wrapper.SEM == sdk and wrapper.PIN == current["semwright_sha"], "fixedtxt-original-wrapper-SDK")
    tracked = read_git(sdk, "ls-files", "-z").decode().split("\0")
    required = sorted({name for name in tracked if name and
                       (name.endswith(".rs") or Path(name).name in {"Cargo.toml", "Cargo.lock"})})
    need(1 <= len(required) <= 4096, "fixedtxt-common-source-roster-bound")
    common = {"schema": "motionwright.fixed-text-common-compiler-sources/1",
              "semwright_revision": current["semwright_sha"], "files": []}
    for relative in required:
        row = wrapper.fixed_descriptor(sdk / relative, 16 * 1024 * 1024)
        common["files"].append({"path": relative, "bytes": row["bytes"], "sha256": row["sha256"]})
    common_raw = canonical(common)
    need(len(common_raw) <= 4 * 1024 * 1024, "fixedtxt-common-source-manifest-bound")
    common_path = layout["inputs_root"] / "common-sources.original.json"
    new_bytes(common_path, common_raw)
    node_name, decoder_name = shutil.which("node"), shutil.which("ffmpeg")
    need(node_name and decoder_name, "fixedtxt-actual-runtime-binaries")
    node, decoder = Path(node_name).resolve(strict=True), Path(decoder_name).resolve(strict=True)
    def binary(path):
        need(path.is_file() and not path.is_symlink(), "fixedtxt-real-built-binary")
        return wrapper.digest(path)
    runtime = wrapper.MOTION_RUNTIME
    cycle = {"schema": "motionwright.fixed-text-native-cycle-inputs/4", "arm": request["arm"],
        **{key: sources[key] for key in ("template_film", "managed_project", "caption_source", "project_files")},
        "runtime": {"semwright_revision": current["semwright_sha"],
            "driver_sha256": binary(wrapper.SW_BIN / "semwright-motion-canvas-driver"),
            "node_sha256": binary(node), "native_source_sha256": dict(PAIR),
            "render_helper": wrapper.fixed_descriptor(runtime / "render.mjs", 2 * 1024 * 1024),
            "dependency_lock": wrapper.fixed_descriptor(runtime / "package-lock.json", 2 * 1024 * 1024),
            "core_package": wrapper.fixed_descriptor(runtime / "node_modules/@motion-canvas/core/package.json", 2 * 1024 * 1024),
            "core_renderer": wrapper.fixed_descriptor(runtime / "node_modules/@motion-canvas/core/lib/app/Renderer.js", 2 * 1024 * 1024)},
        "compiler_binary_sha256": binary(sdk / "target/debug/examples/fixed_text_compile_v4"),
        "decoder_sha256": binary(decoder),
        "common_sources": {"path": str(common_path), **original_descriptor(common_raw)}}
    raw = canonical(cycle)
    need(len(raw) <= 1024 * 1024, "fixedtxt-existing-Q-bound")
    new_bytes(layout["cycle"], raw)
    return raw


def make_plan(request, current, cycle_raw, source, *, known):
    import native_fixedtxt_g2_admission as fixed
    import native_film180_evidence_selection as selection
    cycle = strict(cycle_raw, 1024 * 1024)
    members = []
    for (root, relative), role in selection.fixedtxt_required_members(request["fixture"], fixed.NATIVE_SLOT).items():
        members.append({"root": root, "relative_path": relative, "role": role,
                        "maximum": request["role_limits"][role],
                        "expected_sha256": sha(cycle_raw) if role == "cycle-inputs" else None})
    members.extend([
        {"root": "inputs", "relative_path": fixed.RUNTIME_MEMBER, "role": "input-original", "maximum": 65536,
         "expected_sha256": None},
        {"root": "observation", "relative_path": request["fixture"] + "/native-result.host-stdin/broker-receipt.snapshot.json",
         "role": "attempt-original", "maximum": 8 * 1024 * 1024, "expected_sha256": None}])
    for name, bound in fixed.PRE_OWNER_DIAGNOSTICS:
        members.append({"root": "observation", "relative_path": name, "role": "attempt-original",
                        "maximum": bound, "expected_sha256": None})
    for suffix, bound in (("intent.json", 65536), ("spawned.json", 65536),
        ("stdout", 1024 * 1024 + 1), ("stderr", 1024 * 1024 + 1), ("terminal.json", 65536)):
        members.append({"root": "control", "relative_path": "supervision/work." + suffix,
                        "role": "attempt-original", "maximum": bound, "expected_sha256": None})
    for name, bound in (("transport.requests.ndjson", API_JOURNAL_MAX), ("transport.original.zip", DELIVERY_MAX),
        ("transport.consume.intent.json", 65536), ("dispatch.original.json", DELIVERY_MAX), ("transport.terminal.json", 65536)):
        members.append({"root": "control", "relative_path": name, "role": "attempt-original",
                        "maximum": bound, "expected_sha256": None})
    for (root, relative), raw in known.items():
        members.append({"root": root, "relative_path": relative, "role": "input-original",
                        "maximum": max(1, len(raw)), "expected_sha256": sha(raw)})
    plan = {"schema": fixed.PLAN_SCHEMA, "scope": SCOPE, "context": current,
        "arm": request["arm"], "fixture": request["fixture"], "case_roster": selection.fixedtxt_case_roster(),
        "arm_sources": {arm: {"source_sha": current["semwright_sha"], "tree_sha": request["semwright_tree"],
                              "native_source_sha256": digest} for arm, digest in PAIR.items()},
        "source_bindings": [{"path": name, "sha256": digest} for name, digest in sorted(source["files"].items())],
        "cycle_input_sha256": sha(cycle_raw), "decoder_sha256": cycle["decoder_sha256"],
        "native_directory": fixed.NATIVE_SLOT, "native_directory_selector": fixed.SELECTOR,
        "owner_count": 1, "expected_fixture_count": 1, "default_fixture_count": 12, "render_frames": 180,
        "selected_frames": list(selection.fixedtxt_frames(request["fixture"])), "limits": request["limits"],
        "members": sorted(members, key=lambda row: (row["root"], row["relative_path"]))}
    fixed.validate_plan(plan, current=current)
    return canonical(plan)


def preparation_snapshot(current, request, plan_raw, runtime_raw, cycle_raw, known, recipient):
    import native_fixedtxt_g2_admission as fixed
    from ci_private_evidence import encrypt_snapshot, new_bytes
    originals = {root + "/" + relative: raw for (root, relative), raw in known.items()}
    originals.update({"control/" + fixed.PLAN_MEMBER: plan_raw, "inputs/" + fixed.RUNTIME_MEMBER: runtime_raw,
                      "inputs/" + fixed.CYCLE_MEMBER: cycle_raw})
    metadata = canonical({"schema": "motionwright.native.fixedtxt-preparation-snapshot/1", "context": current,
        "purpose": request["purpose"], "files": [{"path": path, **original_descriptor(raw)}
                                                  for path, raw in sorted(originals.items())],
        "P": original_descriptor(plan_raw), "R": original_descriptor(runtime_raw), "Q": original_descriptor(cycle_raw),
        "native_effects": 0, "admission_issued": False, "oracle_values_approved": False})
    need(len(metadata) <= 512 * 1024, "fixedtxt-preparation-manifest-bound")
    plain = fixed._pack_originals(originals, metadata)
    sealed = encrypt_snapshot(plain, recipient, current)
    need(len(sealed) <= request["review_capsule_bytes"], "fixedtxt-preparation-review-capsule-budget")
    directory = Path(os.environ["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-review-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    new_bytes(directory / "sealed-preparation.bin", sealed)
    # These are original-byte descriptors, not a successful native/oracle result.
    public = canonical({"schema": "motionwright.native.fixedtxt-preparation-public/1", "context": current,
        "purpose": request["purpose"], "P": original_descriptor(plan_raw), "R": original_descriptor(runtime_raw),
        "Q": original_descriptor(cycle_raw), "sealed_preparation": original_descriptor(sealed),
        "recipient_sha256": recipient["sha256"], "native_effects": 0, "admission_issued": False,
        "numerical_oracle_approved": False, "native_success_inferred": False})
    need(len(sealed) + len(public) + 8192 <= request["review_capsule_bytes"], "fixedtxt-preparation-ZIP-total-budget")
    new_bytes(directory / "preparation-public.json", public)
    return directory


def retain_owner_resource_prefix(current, session):
    """Keep only this preparation guard's genuine, immutable active prefix."""
    import native_owner_resources as resources
    from ci_private_evidence import new_bytes
    index = None if session is None else session["binding"]["index"]
    originals = resources.resource_originals("fixedtxt", "prepare", index)
    destination = owned_layout(current)["control_root"] / "owner-resources"
    destination.mkdir(mode=0o700, exist_ok=False)
    for name, raw in sorted(originals.items()):
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        new_bytes(path, raw)


def prior_resource_custody(current, session, *, completed_count=None):
    """Only a completed case with actual ZIP/ciphertext readback covers bytes."""
    if session is None:
        return {}, []
    import native_fixedtxt_session as serial
    from ci_private_evidence import regular
    covered, references = {}, []
    count = session["binding"]["index"] if completed_count is None else completed_count
    need(type(count) is int and 0 <= count <= len(serial.ROSTER), "fixedtxt-resource-custody-completed-count")
    for index in range(count):
        root = serial.session_root()
        raw = regular(root / ("case-%02d.resource-custody.original.json" % index), 1024 * 1024)[0]
        reference = strict(raw, 1024 * 1024)
        closed = strict(regular(root / ("case-%02d.closed.original.json" % index), 65536)[0])
        expected_request = session["plan"]["cases"][index]["request"]
        need(reference.get("schema") == "motionwright.native.fixedtxt-resource-custody-reference/1"
             and reference.get("context") == current and reference.get("index") == index
             and reference.get("request") == expected_request
             and reference.get("ciphertext_publication_readback") is True
             and reference.get("automatic_retry") is False
             and closed.get("context") == current and closed.get("index") == index
             and closed.get("request") == expected_request and closed.get("mechanics_complete") is True
             and closed.get("resource_custody_reference") == original_descriptor(raw),
             "fixedtxt-resource-custody-reference-case")
        artifact = reference["workflow_artifact"]
        need(artifact == artifact_identity(closed["artifacts"]["workflow"])
             and artifact["workflow_run"]["id"] == int(current["run_id"])
             and artifact["workflow_run"]["head_sha"] == current["source_sha"]
             and artifact["expired"] is False and artifact["digest"] == "sha256:" + reference["zip"]["sha256"]
             and artifact["size_in_bytes"] == reference["zip"]["bytes"],
             "fixedtxt-resource-custody-reference-upload")
        manifest = reference["resource_manifest"]
        manifest_raw = canonical(manifest)
        need(original_descriptor(manifest_raw) == reference["resource_manifest_original"]
             and manifest.get("schema") == "motionwright.native.fixedtxt-resource-custody-manifest/1"
             and manifest.get("context") == current and manifest.get("case_index") == index
             and manifest.get("request") == expected_request and manifest.get("failures") == []
             and manifest.get("origin") == reference["origin"]
             and manifest.get("active_action") == "seal-workflow"
             and manifest.get("active_guard_completion_claimed") is False,
             "fixedtxt-resource-custody-reference-manifest")
        for row in manifest["originals"]:
            need(type(row) is dict and set(row) == {"path", "bytes", "sha256"}
                 and type(row["path"]) is str and 0 < len(row["path"]) <= 256
                 and "\\" not in row["path"] and not row["path"].startswith("/")
                 and all(part not in ("", ".", "..") for part in row["path"].split("/"))
                 and row["path"] not in covered and type(row["bytes"]) is int
                 and 0 <= row["bytes"] <= 8 * 1024 * 1024 and SHA.fullmatch(row["sha256"]),
                 "fixedtxt-resource-custody-covered-original")
            covered[row["path"]] = {"bytes": row["bytes"], "sha256": row["sha256"]}
        references.append({"case_index": index, "request": expected_request,
            "reference": original_descriptor(raw), "origin": reference["origin"],
            "workflow_artifact": artifact, "ciphertext": reference["ciphertext"],
            "resource_manifest": reference["resource_manifest_original"]})
    return covered, references


def select_owner_resource_custody(current, request, request_raw, session, *, action, completed_count=None):
    """Select the not-yet-custodied tail; no live stream or future terminal."""
    import native_owner_resources as resources
    from ci_private_evidence import regular
    index = None if session is None or action == "seal-session" else session["binding"]["index"]
    covered, references = prior_resource_custody(current, session, completed_count=completed_count)
    active_case = index if action == "seal-workflow" else None
    active = resources.resource_originals("fixedtxt", action, active_case)
    origin_raw = active["origin.original.json"]
    origin = resources.strict(origin_raw)
    need(origin["context"] == current and all(row["origin"] == original_descriptor(origin_raw) for row in references),
         "fixedtxt-resource-custody-single-original-origin")
    journal = Path(os.environ["MW_NATIVE_RESOURCE_ORIGIN"]).parent / "journal"
    need(journal.resolve(strict=True) == journal and not journal.is_symlink(), "fixedtxt-resource-custody-real-journal")
    select = resources.journal_selection(action, active_case)
    retained, seen, failures = {}, set(), []
    total = 0
    for path in sorted(journal.rglob("*")):
        relative = path.relative_to(journal)
        need(not path.is_symlink(), "fixedtxt-resource-custody-no-journal-link")
        if not path.is_file() or not select(relative.parts):
            continue
        name = "origin.original.json" if relative.as_posix() == "origin.original.json" else "commands/" + relative.as_posix()
        need(name not in seen and len(seen) < request["limits"]["files"], "fixedtxt-resource-custody-original-roster")
        seen.add(name)
        maximum = 4 * 1024 * 1024 if path.name in ("stdout.original", "stderr.original") else 65536
        raw = regular(path, maximum)[0]
        if name in covered:
            need(original_descriptor(raw) == covered[name], "fixedtxt-resource-custody-covered-byte-changed")
            continue
        total += len(raw)
        need(total <= request["review_capsule_bytes"] - 1024 * 1024,
             "fixedtxt-resource-custody-original-byte-budget")
        retained[name] = raw
    need(set(covered) <= seen, "fixedtxt-resource-custody-covered-original-disappeared")
    for name, raw in active.items():
        need(name in retained or name in covered, "fixedtxt-resource-custody-active-prefix-missing")
        need(original_descriptor(raw) == (original_descriptor(retained[name]) if name in retained else covered[name]),
             "fixedtxt-resource-custody-active-prefix-changed")
    manifest = {"schema": "motionwright.native.fixedtxt-resource-custody-manifest/1", "context": current,
        "case_index": index, "request": original_descriptor(request_raw),
        "origin": original_descriptor(origin_raw), "active_action": action,
        "active_guard_completion_claimed": False, "previous_custody": references,
        "originals": [{"path": name, **original_descriptor(raw)} for name, raw in sorted(retained.items())],
        "failures": failures, "Native_outcome": "NOT_INFERRED", "automatic_retry": False}
    need(strict(request_raw, 49152) == request, "fixedtxt-resource-custody-original-request")
    need(len(canonical(manifest)) <= 512 * 1024, "fixedtxt-resource-custody-manifest-budget")
    return retained, manifest


def readback_uploaded_workflow(artifact, directory, *, current, request, deadline, expected_ciphertext, expected_public):
    """One bounded GET, streamed ZIP and two exact member digests; no retry."""
    from ci_private_evidence import new_bytes, new_json
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    maximum = request["review_capsule_bytes"]
    need(0 < artifact["size_in_bytes"] <= maximum and shutil.disk_usage(directory).free >= 10 * 1024**3 + maximum,
         "fixedtxt-resource-publication-original-disk-budget")
    until = min(deadline, time.monotonic() + 60)
    need(time.monotonic() + 2 < until, "fixedtxt-resource-publication-original-deadline")
    route = "repos/" + REPO + "/actions/artifacts/" + str(artifact["id"]) + "/zip"
    argv = ["/usr/bin/gh", "api", "--method", "GET", route]
    environment = {"PATH": "/usr/bin:/bin", "GH_HOST": "github.com", "GH_TOKEN": os.environ.get("GH_TOKEN", "")}
    need(environment["GH_TOKEN"], "fixedtxt-resource-publication-token-absent")
    new_json(directory / "get.intent.json", {"context": current, "argv": argv,
        "artifact": artifact_identity(artifact), "deadline_monotonic": until,
        "maximum_bytes": maximum, "automatic_retry": False, "native_calls": 0})
    process = None
    selector = selectors.DefaultSelector()
    sizes, hashes = {"stdout": 0, "stderr": 0}, {key: hashlib.sha256() for key in ("stdout", "stderr")}
    eof, error, code = set(), None, None
    secondary_errors = []
    group_state, group_killed, group_survived_parent = "not_spawned", False, False
    def failed(failure, phase):
        nonlocal error
        if error is None:
            error = failure
        else:
            secondary_errors.append({"phase": phase, "type": type(failure).__name__})
    archive_path = directory / "workflow-artifact.original.zip"
    streams = {}
    try:
        # Both reservations and every post-spawn mutation share this cleanup.
        streams["stdout"] = archive_path.open("xb")
        streams["stderr"] = (directory / "get.stderr.original").open("xb")
        process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        new_json(directory / "get.spawned.json", {"context": current, "pid": process.pid, "automatic_retry": False})
        for name in streams:
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map() or process.poll() is None:
            need(time.monotonic() < until - 1, "fixedtxt-resource-publication-GET-deadline-no-retry")
            for key, _ in selector.select(timeout=0.05):
                name = key.data
                raw = os.read(key.fileobj.fileno(), 65536)
                if not raw:
                    eof.add(name)
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                limit = artifact["size_in_bytes"] if name == "stdout" else 8192
                before = sizes[name]
                sizes[name] += len(raw)
                retained = raw[:max(0, limit - before)]
                streams[name].write(retained)
                hashes[name].update(retained)
                need(sizes[name] <= limit, "fixedtxt-resource-publication-GET-overflow-no-retry")
        code = process.wait(timeout=max(0.01, until - time.monotonic()))
        need(code == 0 and eof == {"stdout", "stderr"} and sizes["stdout"] == artifact["size_in_bytes"]
             and hashes["stdout"].hexdigest() == artifact["digest"].removeprefix("sha256:"),
             "fixedtxt-resource-publication-original-ZIP-differs")
    except BaseException as failure:
        failed(failure, "read")
    finally:
        if process is not None:
            # This Popen created a new session. Observe its own group even if
            # the parent already exited; surviving children remain a negative.
            try:
                parent_exited = process.poll() is not None
                os.killpg(process.pid, 0)
                group_state = "present"
                if parent_exited:
                    group_survived_parent = True
                    failed(WorkflowRejected("fixedtxt-resource-publication-group-survived-parent"), "group")
                os.killpg(process.pid, signal.SIGKILL)
                group_killed = True
            except ProcessLookupError:
                group_state = "absent"
            except BaseException as failure:
                group_state = "unknown"
                failed(failure, "group-kill")
            try:
                process.wait(timeout=max(0.01, min(1, until - time.monotonic())))
            except BaseException as failure:
                failed(failure, "parent-wait")
            code = process.poll()
            for name in ("stdout", "stderr"):
                pipe = getattr(process, name)
                if pipe is not None and not pipe.closed:
                    try:
                        pipe.close()
                    except BaseException as failure:
                        failed(failure, name + "-pipe-close")
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                group_state = "absent"
            except BaseException as failure:
                group_state = "unknown"
                failed(failure, "group-observe")
            else:
                group_state = "present"
                failed(WorkflowRejected("fixedtxt-resource-publication-group-still-observed"), "group-observe")
        try:
            selector.close()
        except BaseException as failure:
            failed(failure, "selector-close")
        for name, stream in streams.items():
            try:
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException as failure:
                failed(failure, name + "-flush")
            try:
                stream.close()
            except BaseException as failure:
                failed(failure, name + "-close")
        terminal = {"context": current, "exit_code": code,
            "first_error_type": None if error is None else type(error).__name__,
            "first_error": str(error) if isinstance(error, WorkflowRejected) else None,
            "secondary_errors": secondary_errors,
            "observed_bytes": sizes, "retained_sha256": {name: value.hexdigest() for name, value in hashes.items()},
            "owned_process_group": None if process is None else process.pid,
            "process_group_kill_sent": group_killed, "group_survived_parent_exit": group_survived_parent,
            "final_process_group_state": group_state,
            "complete": error is None and code == 0 and eof == {"stdout", "stderr"} and group_state == "absent",
            "automatic_retry": False, "native_calls": 0}
        try:
            new_json(directory / "get.terminal.json", terminal)
        except BaseException as failure:
            failed(failure, "terminal-original")
    if error is not None:
        raise error
    expected = {"sealed-workflow-originals.bin": expected_ciphertext, "workflow-public.json": expected_public}
    verify_workflow_zip_members(archive_path, artifact, expected, maximum, deadline=until)
    value = {"schema": "motionwright.native.fixedtxt-workflow-upload-readback/1", "context": current,
        "artifact": artifact_identity(artifact), "zip": {"bytes": sizes["stdout"], "sha256": hashes["stdout"].hexdigest()},
        "members": expected, "ciphertext_publication_readback": True, "automatic_retry": False, "native_calls": 0}
    new_json(directory / "ciphertext-readback.original.json", value)
    return value


def verify_workflow_zip_members(archive_path, artifact, expected, maximum, *, deadline=None):
    """Compare streamed member bytes, without decrypting or decoding media."""
    need(set(expected) == {"sealed-workflow-originals.bin", "workflow-public.json"}
         and sum(row["bytes"] for row in expected.values()) <= maximum,
         "fixedtxt-resource-publication-closed-member-budget")
    before = archive_path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == artifact["size_in_bytes"],
         "fixedtxt-resource-publication-original-ZIP-type")
    fd = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    opened = os.fstat(fd)
    identity = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
    if identity(opened) != identity(before):
        os.close(fd)
        raise WorkflowRejected("fixedtxt-resource-publication-original-ZIP-open-race")
    with os.fdopen(fd, "rb") as original, zipfile.ZipFile(original) as archive:
        rows = archive.infolist()
        need(len(rows) == 2 and {row.filename for row in rows} == set(expected)
             and sum(row.file_size for row in rows) <= maximum,
             "fixedtxt-resource-publication-closed-member-roster")
        for row in rows:
            need(deadline is None or time.monotonic() < deadline, "fixedtxt-resource-publication-member-deadline")
            need(not row.is_dir() and not row.flag_bits & 1
                 and stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG)
                 and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                 and row.file_size == expected[row.filename]["bytes"],
                 "fixedtxt-resource-publication-regular-member")
            size, digest = 0, hashlib.sha256()
            with archive.open(row) as stream:
                while True:
                    need(deadline is None or time.monotonic() < deadline, "fixedtxt-resource-publication-member-deadline")
                    raw = stream.read(65536)
                    if not raw:
                        break
                    size += len(raw)
                    need(size <= row.file_size, "fixedtxt-resource-publication-member-overflow")
                    digest.update(raw)
            need({"bytes": size, "sha256": digest.hexdigest()} == expected[row.filename],
                 "fixedtxt-resource-publication-member-differs")
        need(identity(os.fstat(original.fileno())) == identity(before), "fixedtxt-resource-publication-open-ZIP-mutated")
    after = archive_path.lstat()
    need(identity(before) == identity(after)
         and after.st_size == artifact["size_in_bytes"], "fixedtxt-resource-publication-original-ZIP-mutated")


def _owner_diagnostic_limits(fixture):
    from native_film180_evidence_selection import fixedtxt_frames
    fixedtxt_frames(fixture)
    names = {
        "original-attempt-error.json": 1024 * 1024,
        fixture + "/native-result.fixed-text/render-terminal.original-value.json": 8 * 1024 * 1024,
        fixture + "/native-result.host-stdin/native-return.snapshot.json": 8 * 1024 * 1024,
    }
    for name, maximum in (
        ("semwrightd.original.private.log", 1024 * 1024),
        ("original-attempt-error.json", 1024 * 1024),
        ("broker-start.intent.json", 65536),
        ("broker-original-identity.json", 65536),
        ("broker-end-observation.json", 65536),
    ):
        names[fixture + "/" + name] = maximum
    return names


def _read_owner_diagnostic(handles, name, maximum, *, fixture):
    """Read only the closed diagnostic names through held no-follow parent handles.

    Absence before selection is conditional. Disappearance or mutation after
    the first leaf stat is a failed original, never a successful omission.
    Core's ordinary raw-stream exclusion remains unchanged.
    """
    import native_evidence_projection as core
    admitted = (_owner_diagnostic_limits(fixture) | _failed_render_input_limits(fixture)).get(name)
    need(admitted is not None and type(maximum) is int and 0 <= maximum <= admitted,
         "fixedtxt-owner-diagnostic-closed-roster")
    parts = core.member_path(name)
    try:
        parents = handles.parent("observation", parts)
    except FileNotFoundError:
        return None
    fd = -1
    try:
        try:
            before_name = os.stat(parts[-1], dir_fd=parents[-1][0], follow_symlinks=False)
        except FileNotFoundError:
            return None
        need(stat.S_ISREG(before_name.st_mode) and before_name.st_nlink == 1,
             "fixedtxt-owner-diagnostic-regular-single-link")
        need(0 <= before_name.st_size <= maximum, "ORIGINAL_RESOURCE_BOUND")
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parents[-1][0])
        before = os.fstat(fd)
        need(core.identity(before_name) == core.identity(before),
             "fixedtxt-owner-diagnostic-changed-before-open")
        raw = bytearray()
        while True:
            block = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            need(len(raw) <= maximum, "ORIGINAL_RESOURCE_BOUND")
        after = os.fstat(fd)
        after_name = os.stat(parts[-1], dir_fd=parents[-1][0], follow_symlinks=False)
        need(core.identity(before) == core.identity(after) == core.identity(after_name)
             and len(raw) == before.st_size
             and all(core.identity(os.fstat(parent)) == mark for parent, mark in parents),
             "fixedtxt-owner-diagnostic-changed-during-read")
        return bytes(raw)
    finally:
        if fd >= 0:
            os.close(fd)
        handles.close_parents(parents)



# Negative diagnostics are deliberately outside P's required Native roster.
# No success resolver, decoder, Native call, retry or timeout is added here.
FAILED_OUTPUT_METADATA_MAX = 65536
FAILED_OUTPUT_RECEIPT_MAX = 65536


def _failed_render_input_limits(fixture, *, status_timeout=False):
    from native_film180_evidence_selection import fixedtxt_frames
    fixedtxt_frames(fixture)
    limits = {fixture + "/" + name: maximum for name, maximum in (
        ("owner-workspace.json", 65536),
        ("saved-production-project.json", 4 * 1024 * 1024),
        ("fixed-text-case.json", 65536),
        ("canonical-component/evidence/project.derived.json", 4 * 1024 * 1024),
        ("native-result.host-stdin/broker-receipt.snapshot.json", 8 * 1024 * 1024),
        ("native-result.host-stdin/session.original.json", 8192),
        ("native-result.host-stdin/capture-0001.json", 8192),
        ("native-result.host-stdin/capture-0001.terminal.json", 2048),
        ("native-result.host-stdin/capture-0001.stdin", 65536),
        ("native-result.host-stdin/readback.observation.json", 8 * 1024 * 1024),
    )}
    if status_timeout:
        del limits[fixture + "/saved-production-project.json"]
    return limits


def _failed_status_timeout_job(original, request, owner):
    """Bind a lost read-only status reply; do not manufacture a Native terminal."""
    def decoded(value, maximum=4096):
        need(type(value) is dict and value.get("encoding") == "hex"
             and type(value.get("bytes")) is int and 0 <= value["bytes"] <= maximum
             and type(value.get("value")) is str
             and len(value["value"]) == value["bytes"] * 2
             and re.fullmatch(r"[0-9a-f]*", value["value"]) is not None,
             "failed-output-status-original-bytes")
        raw = bytes.fromhex(value["value"])
        need(value.get("sha256") == sha(raw), "failed-output-status-original-digest")
        return raw
    need(type(original) is dict and type(owner) is dict, "failed-output-status-query-shape")
    error, call = original.get("error", {}), original.get("failed_cli_call", {})
    need(type(error) is dict and type(call) is dict, "failed-output-status-query-shape")
    need(original.get("schema") == "motionwright.private-native-return-snapshot/2"
         and original.get("capture_representation") == "complete_observed_cli_bytes"
         and original.get("wire_bytes") is True and original.get("automatic_retry") is False
         and original.get("native_result") == "returned_error"
         and error.get("code") == "Timeout" and error.get("outcome_known") is False
         and call.get("command") == "driver.motion-canvas.render.status"
         and call.get("mutation") is False
         and type(call.get("elapsed_ms")) is int and 0 < call["elapsed_ms"] <= 3_900_000,
         "failed-output-status-query-unknown")
    prefix = "fixed-text-v4-" + request["arm"].lower() + "-" + request["fixture"] + ":motion:0:render-status-"
    number = re.fullmatch(re.escape(prefix) + r"(0|[1-9][0-9]{0,2})",
                          call.get("request_id") if type(call.get("request_id")) is str else "")
    transport = call.get("transport", {})
    need(type(transport) is dict, "failed-output-status-query-transport")
    entries = transport.get("entries")
    need(number is not None and int(number[1]) <= 330
         and transport.get("schema") == "motionwright.production.private-transport-custody/1"
         and transport.get("authority") is False and transport.get("durable") is False
         and transport.get("mutex_poisoned") is False
         and type(entries) is list and len(entries) == 1,
         "failed-output-status-query-transport")
    entry = entries[0]
    need(type(entry) is dict and type(entry.get("transport_terminal")) is dict
         and type(entry["transport_terminal"].get("original_error")) is dict,
         "failed-output-status-query-original")
    need(type(entry) is dict and entry.get("command") == call["command"]
         and entry.get("mutation") is False and entry.get("automatic_retry") is False
         and entry.get("process_started") is True and entry.get("process_exit") is None
         and type(entry.get("pid")) is int and entry["pid"] > 0
         and type(entry.get("connection_identity")) is str and SHA.fullmatch(entry["connection_identity"])
         and type(entry.get("executable_sha256")) is str and SHA.fullmatch(entry["executable_sha256"])
         and entry.get("transport_terminal", {}).get("kind") == "error"
         and entry["transport_terminal"].get("original_error", {}).get("code") == "Timeout"
         and entry["transport_terminal"]["original_error"].get("outcome_known") is True,
         "failed-output-status-query-original")
    args_raw = decoded(entry.get("args_json"))
    args = strict(args_raw, 4096)
    need(type(args) is dict and set(args) == {"job_ref"}, "failed-output-status-query-arguments")
    argv = entry.get("argv")
    need(type(argv) is list and len(argv) == 9
         and all(type(v) is dict and v.get("platform_encoding") == "unix_bytes" for v in argv),
         "failed-output-status-query-argv")
    values = [decoded(v.get("original")) for v in argv]
    owner_path = owner.get("path")
    need(type(owner_path) is str and owner_path.startswith("/") and "\\" not in owner_path
         and all(p not in ("", ".", "..") for p in owner_path.split("/")[1:])
         and values == [b"--socket", (owner_path + "/runtime/broker.sock").encode(),
             b"--session-file", (owner_path + "/runtime/motionwright.session").encode(),
             b"--json", b"execute", b"driver.motion-canvas.render.status", b"--args-json", args_raw],
         "failed-output-status-query-owner")
    return args["job_ref"]


def _failed_render_join(current, request, originals, plan_raw, cycle_raw, *, status_timeout=False):
    """Join existing negative originals; neither issue nor weaken admission."""
    import native_fixedtxt_g2_admission as fixed
    from native_film180_evidence_selection import fixedtxt_frames
    fixture, arm = request["fixture"], request["arm"]
    prefix, host = fixture + "/", fixture + "/native-result.host-stdin/"
    def value(name, maximum=8 * 1024 * 1024):
        return strict(originals[prefix + name], maximum)
    plan, cycle = strict(plan_raw, 512 * 1024), strict(cycle_raw, 1024 * 1024)
    bindings = plan.get("source_bindings")
    need(plan.get("schema") == fixed.PLAN_SCHEMA and plan.get("context") == current
         and plan.get("arm") == arm and plan.get("fixture") == fixture
         and plan.get("cycle_input_sha256") == sha(cycle_raw)
         and type(bindings) is list and all(type(row) is dict for row in bindings)
         and [row.get("path") for row in bindings] == list(fixed.source_paths())
         and all(set(row) == {"path", "sha256"} and type(row["sha256"]) is str
                 and SHA.fullmatch(row["sha256"]) for row in bindings),
         "failed-output-source-binding")
    runtime = cycle.get("runtime", {})
    case = value("fixed-text-case.json", 65536)
    need(cycle.get("schema") == "motionwright.fixed-text-native-cycle-inputs/4"
         and cycle.get("arm") == arm and runtime.get("semwright_revision") == current["semwright_sha"]
         and case.get("schema") == "motionwright.private-fixed-text-render-case/4"
         and case.get("arm") == arm and case.get("key") == fixture
         and case.get("runtime") == runtime
         and plan.get("arm_sources", {}).get(arm, {}).get("source_sha") == current["semwright_sha"]
         and plan["arm_sources"][arm].get("native_source_sha256")
             == runtime.get("native_source_sha256", {}).get(arm),
         "failed-output-case-runtime-binding")
    need(type(status_timeout) is bool, "failed-output-negative-mode")
    if status_timeout:
        negative_name = "native-result.host-stdin/native-return.snapshot.json"
        terminal, result = None, None
        job = _failed_status_timeout_job(value(negative_name), request,
                                        value("owner-workspace.json", 65536))
    else:
        negative_name = "native-result.fixed-text/render-terminal.original-value.json"
        terminal = value(negative_name)
        result = terminal.get("result", {})
        data = result.get("data", {})
        need(terminal.get("replayed") is False and result.get("provenance_checked") is True
             and type(data) is dict and data.get("state") == "failed"
             and type(data.get("host_failure")) is dict
             and data["host_failure"].get("outcome_known") is False,
             "failed-output-original-unknown-terminal")
        job = data.get("job_ref")
    need(type(job) is str and len(job) <= 320 and job.isascii(), "failed-output-job-reference")
    fields = job.split(":")
    need(len(fields) == 7 and fields[0] == "mc1" and fields[5] == "render_job"
         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", fields[1])
         and re.fullmatch(r"[0-9a-f]{32}", fields[2])
         and re.fullmatch(r"[1-9][0-9]{0,19}", fields[3]) and int(fields[3]) <= 2**64 - 1
         and SHA.fullmatch(fields[4]) and re.fullmatch(r"render-[0-9a-f]{32}", fields[6]),
         "failed-output-job-reference")
    compiled_raw = originals[prefix + "canonical-component/evidence/project.derived.json"]
    compiled = strict(compiled_raw, 4 * 1024 * 1024)
    declared = case.get("compiled_project", {})
    need(type(declared.get("bytes")) is int and declared["bytes"] == len(compiled_raw)
         and declared.get("sha256") == sha(compiled_raw) and type(compiled.get("revision")) is int
         and (compiled.get("id"), compiled.get("generation"), compiled.get("revision"))
             == (fields[1], fields[2], int(fields[3])),
         "failed-output-motion-project-binding")
    snapshot = value("native-result.host-stdin/broker-receipt.snapshot.json")
    receipt = snapshot.get("receipt", {})
    payload = receipt.get("payload", {})
    started = payload.get("result", {})
    # A lost status query has no completed Native result or saved-project
    # export. Retain the exact Studio identity observed in the original start
    # receipt and label that single source; do not invent an independent read.
    studio = ({"id": receipt.get("project_id"), "generation": receipt.get("generation"),
               "revision": receipt.get("revision")} if status_timeout else
              value("saved-production-project.json", 4 * 1024 * 1024))
    expected_request = "fixed-text-v4-" + arm.lower() + "-" + fixture + ":motion:0:render-start"
    need(snapshot.get("schema") == "motionwright.private-production-receipt-snapshot/1"
         and snapshot.get("source") == "existing StudioService receipt"
         and snapshot.get("wire_bytes") is False and snapshot.get("lookup_error") is None
         and snapshot.get("snapshot_grants_authority") is False
         and all(type(studio.get(key)) is str and re.fullmatch(
             r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", studio[key])
             for key in ("id", "generation"))
         and type(studio.get("revision")) is int and 0 <= studio["revision"] <= 2**64 - 1
         and (receipt.get("project_id"), receipt.get("generation"), receipt.get("revision"))
             == (studio["id"], studio["generation"], studio["revision"])
         and receipt.get("request_id") == expected_request and receipt.get("stage") == "completed"
         and receipt.get("command") == "driver.motion-canvas.render.start"
         and started.get("provenance_checked") is True
         and started.get("data", {}).get("job_ref") == job
         and started["data"].get("state") == "starting",
         "failed-output-original-render-start")
    if not status_timeout:
        status_receipt = terminal.get("receipt", {})
        status_payload = status_receipt.get("payload", {})
        status_request = status_receipt.get("request_id")
        status_prefix = "fixed-text-v4-" + arm.lower() + "-" + fixture + ":motion:0:render-status-"
        status_number = re.fullmatch(re.escape(status_prefix) + r"(0|[1-9][0-9]{0,2})",
                                    status_request if type(status_request) is str else "")
        need(status_receipt.get("command") == "driver.motion-canvas.render.status"
             and status_receipt.get("stage") == "completed"
             and (status_receipt.get("project_id"), status_receipt.get("generation"), status_receipt.get("revision"))
                 == (studio["id"], studio["generation"], studio["revision"])
             and status_number is not None and int(status_number[1]) <= 330
             and status_payload.get("job_ref") == job and status_payload.get("result") == result,
             "failed-output-original-status-receipt")
    session = value("native-result.host-stdin/session.original.json", 8192)
    capture = value("native-result.host-stdin/capture-0001.json", 8192)
    pipe = value("native-result.host-stdin/capture-0001.terminal.json", 2048)
    readback = value("native-result.host-stdin/readback.observation.json")
    provider = session.get("provider", {})
    need(session.get("schema") == "semwright.host-stdin-custody/1"
         and provider.get("id") == "driver:motion-canvas" and provider.get("kind") == "driver"
         and type(provider.get("version")) is str and 0 < len(provider["version"]) <= 128
         and session.get("tool") == "motion-node"
         and session.get("driver_sha256") == runtime.get("driver_sha256")
         and session.get("tool_sha256") == runtime.get("node_sha256")
         and all(type(runtime.get(key)) is str and SHA.fullmatch(runtime[key])
                 for key in ("driver_sha256", "node_sha256"))
         and capture.get("provider") == provider and capture.get("tool") == session["tool"]
         and type(capture.get("provider_process_id")) is int and capture["provider_process_id"] > 0
         and capture.get("parent_request_id") == payload.get("broker_request_id")
         and capture.get("terminal_file_required") is True,
         "failed-output-host-provider-binding")
    for response in ((started,) if status_timeout else (started, result)):
        execution = response.get("execution", {})
        provenance = execution.get("provenance", {})
        need(execution.get("dry_run") is False and execution.get("backend") == "driver:motion-canvas"
             and provenance.get("provider") == provider["id"] and provenance.get("source") == provider["kind"]
             and provenance.get("provider_version") == provider["version"],
             "failed-output-provider-provenance")
    for key in ("job_id", "request_id", "parent_request_id", "session_id"):
        need(type(capture.get(key)) is str and 0 < len(capture[key]) <= 512
             and all(ord(ch) >= 32 and ord(ch) != 127 for ch in capture[key]),
             "failed-output-host-identity")
    need(pipe == {"write_state": "write_all_completed", "partial_delivery_possible": False,
         "io_error_kind": None, "raw_os_error": None, "child_consumption": "not_observed",
         "native_render_success": "not_inferred"}, "failed-output-original-pipe-terminal")
    join = readback.get("selected_capture_join", {})
    need(readback.get("schema") == "motionwright.private-host-stdin-readback/1"
         and readback.get("native_success_returned") is False
         and readback.get("native_return_snapshot_retained") is True
         and readback.get("original_identity_unchanged") is True
         and readback.get("new_capture_ordinals_retained") == [1]
         and readback.get("capture_read_errors") == [] and readback.get("automatic_retry") is False
         and all(readback.get(key) is None for key in ("identity_error", "native_return_snapshot_error",
                 "receipt_lookup_error", "receipt_retention_error"))
         and join.get("matched") is True and type(join.get("ordinal")) is int and join["ordinal"] == 1
         and join.get("capture") == capture and join.get("terminal") == pipe
         and join.get("pipe_write_all_completed") is True
         and join.get("joined_to") == "original completed render-start Broker parent and persisted session"
         and join.get("child_consumption") == "not_observed"
         and join.get("native_render_success") == "not_inferred"
         and join.get("Host_job_id_equals_MC_job_ref_claimed") is False,
         "failed-output-original-session-join")
    stdin = originals[host + "capture-0001.stdin"]
    need(0 < len(stdin) <= 65536 and capture.get("stdin_bytes") == len(stdin)
         and capture.get("stdin_sha256") == sha(stdin), "failed-output-original-stdin")
    line, separator, helper = stdin.partition(b"\n")
    program_prefix = b"globalThis.__SEMWRIGHT_RENDER_INPUT__ = "
    need(separator == b"\n" and line.startswith(program_prefix) and line.endswith(b";"),
         "failed-output-original-program-prefix")
    config = strict(line[len(program_prefix):-1], 65536)
    need(original_descriptor(helper) == {key: runtime.get("render_helper", {}).get(key)
                                        for key in ("bytes", "sha256")},
         "failed-output-original-helper")
    expected = {"authoring": True, "name": "frames", "width": 1920, "height": 1080,
                "fpsNum": 30, "fpsDen": 1, "firstFrame": 0, "endFrameExclusive": 180}
    need(all(type(config.get(key)) is type(wanted) and config[key] == wanted
             for key, wanted in expected.items())
         and config.get("textPixelObservationFrames") == list(fixedtxt_frames(fixture))
         and all(type(config.get(key)) is str and SHA.fullmatch(config[key])
                 for key in ("renderInputDigest", "fontResourcesDigest", "dependencyLockDigest"))
         and config["dependencyLockDigest"] == runtime.get("dependency_lock", {}).get("sha256"),
         "failed-output-original-render-config")
    return {"motion_canvas_job_ref": job, "directory": fields[6],
        "motion_canvas_project": {"id": fields[1], "generation": fields[2],
                                 "revision": int(fields[3]), "fingerprint": fields[4]},
        "studio_project": {key: studio[key] for key in ("id", "generation", "revision")},
        "host": {key: capture[key] for key in ("job_id", "request_id", "parent_request_id", "session_id")},
        "render_input_digest": config["renderInputDigest"], "host_stdin": original_descriptor(stdin),
        "render_helper": original_descriptor(helper),
        "terminal": None if status_timeout else original_descriptor(originals[prefix + negative_name]),
        "status_query_return": original_descriptor(originals[prefix + negative_name]) if status_timeout else None,
        "native_terminal_observed": not status_timeout,
        "studio_identity_source": ("completed_render_start_receipt_only" if status_timeout
                                  else "saved_project_and_completed_receipts"),
        "native_state": "unconfirmed" if status_timeout else "failed", "host_outcome_known": False,
        "Host_job_id_equals_MC_job_ref_claimed": False}


def _failed_output_receipt(directory_fd, maximum):
    """One literal read. A missing leaf is distinct from a changed original."""
    import native_evidence_projection as core
    name, fd = "native-failure-receipt.json", -1
    try:
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None, None
        need(stat.S_ISREG(named.st_mode) and named.st_nlink == 1, "failed-output-receipt-type")
        need(0 < named.st_size <= min(maximum, FAILED_OUTPUT_RECEIPT_MAX), "ORIGINAL_RESOURCE_BOUND")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        opened = os.fstat(fd)
        need(core.identity(named) == core.identity(opened), "failed-output-receipt-open-race")
        raw = bytearray()
        while True:
            block = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            need(len(raw) <= maximum, "ORIGINAL_RESOURCE_BOUND")
        need(core.identity(opened) == core.identity(os.fstat(fd))
             == core.identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
             and len(raw) == opened.st_size, "failed-output-receipt-mutated")
        return bytes(raw), list(core.identity(opened))
    finally:
        if fd >= 0:
            os.close(fd)


def _observe_failed_output(owner_handles, joined, receipt_maximum):
    """Stat only this output and at most 180 frame leaves; never read a PNG."""
    import native_evidence_projection as core
    parents, frames_fd = [], -1
    result = {"directory_status": "absent", "entries": [], "frames": [], "receipt_status": "absent"}
    try:
        try:
            parents = owner_handles.parent("owner", ("output", joined["directory"], "selected-leaf"))
        except FileNotFoundError:
            return result, None
        fd = parents[-1][0]
        result["directory_status"] = "present"
        result["directory_identity"] = list(core.identity(os.fstat(fd)))
        def inventory(handle, maximum, frame_names=False):
            rows = []
            with os.scandir(handle) as entries:
                for entry in entries:
                    need(len(rows) < maximum, "failed-output-inventory-count-bound")
                    name = entry.name
                    # The pinned renderer gives Firefox these three XDG
                    # directories inside this output. Observe their metadata only.
                    runtime_directory = not frame_names and name in {".cache", ".config", ".data"}
                    need(type(name) is str and (runtime_directory or
                         re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", name)),
                         "failed-output-inventory-name")
                    if frame_names:
                        need(re.fullmatch(r"[0-9]{6}\.png", name) and int(name[:6]) < 180,
                             "failed-output-frame-name")
                    info = os.stat(name, dir_fd=handle, follow_symlinks=False)
                    ordinary = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    need(not runtime_directory or stat.S_ISDIR(info.st_mode),
                         "failed-output-runtime-directory-type")
                    need(ordinary or not frame_names and stat.S_ISDIR(info.st_mode),
                         "failed-output-inventory-type")
                    rows.append({"name": name, "kind": "file" if ordinary else "directory",
                                 "bytes": info.st_size, "identity": list(core.identity(info))})
            rows.sort(key=lambda row: row["name"])
            return rows
        def unchanged(handle, rows):
            need(all(list(core.identity(os.stat(row["name"], dir_fd=handle, follow_symlinks=False)))
                     == row["identity"] for row in rows), "failed-output-inventory-mutated")
        result["entries"] = inventory(fd, 24)
        frame_entry = next((row for row in result["entries"] if row["name"] == "frames"), None)
        if frame_entry is not None:
            need(frame_entry["kind"] == "directory", "failed-output-frames-type")
            frames_fd = os.open("frames", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            need(list(core.identity(os.fstat(frames_fd))) == frame_entry["identity"],
                 "failed-output-frames-open-race")
            result["frames"] = inventory(frames_fd, 180, True)
            unchanged(frames_fd, result["frames"])
            need(list(core.identity(os.fstat(frames_fd))) == frame_entry["identity"],
                 "failed-output-frames-mutated")
        raw, receipt_identity = _failed_output_receipt(fd, receipt_maximum)
        receipt_entry = next((row for row in result["entries"]
                              if row["name"] == "native-failure-receipt.json"), None)
        need((receipt_entry is None) == (raw is None), "failed-output-receipt-inventory-race")
        if raw is not None:
            value = strict(raw, FAILED_OUTPUT_RECEIPT_MAX)
            need(receipt_entry["identity"] == receipt_identity and value.get("version") == 2
                 and value.get("ok") is False and value.get("render_input_digest") == joined["render_input_digest"],
                 "failed-output-receipt-input-binding")
            result["receipt_status"] = "original_observed"
            result["receipt"] = original_descriptor(raw)
        unchanged(fd, result["entries"])
        need(all(core.identity(os.fstat(parent)) == mark for parent, mark in parents),
             "failed-output-parent-mutated")
        return result, raw
    finally:
        if frames_fd >= 0:
            os.close(frames_fd)
        owner_handles.close_parents(parents)


def _collect_failed_output(handles, known, current, request, byte_budget, file_budget):
    """Conditional encrypted custody within the workflow's existing budget."""
    import native_evidence_projection as core
    import native_fixedtxt_g2_admission as fixed
    fixture = request["fixture"]
    terminal_name = fixture + "/native-result.fixed-text/render-terminal.original-value.json"
    status_name = fixture + "/native-result.host-stdin/native-return.snapshot.json"
    status_timeout = False
    if ("owner_diagnostics", terminal_name) in known:
        negative_name = terminal_name
    elif ("owner_diagnostics", status_name) in known:
        observed = strict(known[("owner_diagnostics", status_name)], 8 * 1024 * 1024)
        if (type(observed) is not dict or observed.get("native_result") != "returned_error"
                or type(observed.get("error")) is not dict
                or observed["error"].get("code") != "Timeout"):
            return {}, []
        negative_name, status_timeout = status_name, True
    else:
        return {}, []
    metadata_name = fixture + "/failed-output.observation.json"
    report = {"schema": "motionwright.private-fixedtxt-failed-output/1", "context": current,
        "fixture": fixture, "arm": request["arm"], "join_status": "not_observed",
        "output_observation": None, "automatic_retry": False, "native_calls": 0,
        "native_outcome_reinterpreted": False, "formal_acceptance": False,
        "PNG_bytes_read": False, "decoder_invoked": False}
    extra, failures, total = {}, [], 0
    owner_handles = None
    def retain(root, name, raw, *, reserve=True):
        nonlocal total
        need(len(extra) + int(reserve) < file_budget
             and len(raw) + total + (FAILED_OUTPUT_METADATA_MAX if reserve else 0) <= byte_budget,
             "ORIGINAL_RESOURCE_BOUND")
        extra[(root, name)] = raw
        total += len(raw)
    try:
        need(file_budget >= 1 and byte_budget >= FAILED_OUTPUT_METADATA_MAX, "ORIGINAL_RESOURCE_BOUND")
        originals = {negative_name: known[("owner_diagnostics", negative_name)]}
        for name, maximum in _failed_render_input_limits(fixture, status_timeout=status_timeout).items():
            need(len(extra) + 1 < file_budget, "ORIGINAL_RESOURCE_BOUND")
            raw = _read_owner_diagnostic(handles, name,
                min(maximum, max(0, byte_budget - total - FAILED_OUTPUT_METADATA_MAX)), fixture=fixture)
            need(raw is not None, "failed-output-join-original-absent")
            retain("owner_diagnostics", name, raw)
            originals[name] = raw
        joined = _failed_render_join(current, request, originals,
            known[("control", fixed.PLAN_MEMBER)], known[("inputs", fixed.CYCLE_MEMBER)],
            status_timeout=status_timeout)
        report["join_status"], report["joined"] = "originals_joined", joined
        report["originals"] = {name: original_descriptor(raw) for name, raw in originals.items()}
        owner = strict(originals[fixture + "/owner-workspace.json"], 65536)
        path, runner = Path(owner["path"]), Path(os.environ["RUNNER_TEMP"])
        need(owner.get("schema") == "motionwright.native.mlt-g2-owner-workspace/1"
             and owner.get("motionwright_sha") == current["source_sha"]
             and owner.get("semwright_sha") == current["semwright_sha"]
             and owner.get("run_id") == current["run_id"] and owner.get("run_attempt") == current["run_attempt"] == "1"
             and path.is_absolute() and path.parent == runner
             and path.name.startswith("motionwright-native-av-g2-"), "failed-output-owner-binding")
        owner_handles = core.RootHandles({"owner": str(path)})
        info = os.fstat(owner_handles.fds["owner"])
        need(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.getuid()
             and (info.st_dev, info.st_ino, info.st_uid, info.st_gid)
                 == (owner.get("device"), owner.get("inode"), owner.get("uid"), owner.get("gid")),
             "failed-output-owner-physical-identity")
        observation, receipt = _observe_failed_output(owner_handles, joined,
            min(FAILED_OUTPUT_RECEIPT_MAX, max(0, byte_budget - total - FAILED_OUTPUT_METADATA_MAX)))
        need(core.identity(os.fstat(owner_handles.fds["owner"]))
             == core.identity(owner_handles.initial["owner"]), "failed-output-owner-mutated")
        report["output_observation"] = observation
        if receipt is not None:
            retain("failed_output", fixture + "/native-failure-receipt.original.json", receipt)
            observation["receipt_status"] = "original_retained"
    except Exception as error:
        reason = str(error) if isinstance(error, WorkflowRejected) else type(error).__name__
        report["failure"] = reason
        if (report["output_observation"] is not None
                and report["output_observation"]["receipt_status"] == "original_observed"):
            report["output_observation"]["receipt_status"] = "not_retained"
        failures.append({"root": "failed_output", "relative_path": metadata_name, "reason": reason})
    finally:
        if owner_handles is not None:
            owner_handles.close()
    try:
        raw = canonical(report)
        need(len(raw) <= FAILED_OUTPUT_METADATA_MAX, "ORIGINAL_RESOURCE_BOUND")
        retain("failed_output", metadata_name, raw, reserve=False)
    except WorkflowRejected:
        failures.append({"root": "failed_output", "relative_path": metadata_name,
                         "reason": "ORIGINAL_RESOURCE_BOUND"})
    return extra, failures


def collect_workflow_originals(current, request, *, partial=False):
    """Retain this route's private originals; never walk an owner or a checkout."""
    from ci_private_evidence import regular
    layout = owned_layout(current)
    import native_fixedtxt_session as serial
    runtime = serial.runtime_control(current, current_session(current))
    roots = {"control": layout["control_root"], "inputs": layout["inputs_root"], "runtime_custody": runtime}
    paths, failures = [], []
    for label, directory in roots.items():
        if not directory.exists():
            failures.append({"root": label, "reason": "ORIGINAL_ROOT_ABSENT"})
            continue
        need(directory.resolve(strict=True) == directory and not directory.is_symlink(),
             "fixedtxt-workflow-original-root")
        for path in sorted(directory.rglob("*")):
            need(not path.is_symlink(), "fixedtxt-workflow-original-symlink")
            if path.is_file():
                paths.append((label, path.relative_to(directory).as_posix(), path))
    # P/R/Q and the starting clock take precedence in a failed preparation's
    # partial envelope. Omitted bytes are named explicitly, never a PASS.
    primary = {"fixedtxt-plan.original.json", "source-input.original.json", "cycle.original.json",
               "request.original.json", "clock.original.json", "runtime-bootstrap.original.json"}
    paths.sort(key=lambda row: (row[1] not in primary, row[0], row[1]))
    known, total = {}, 0
    bound = max(0, request["review_capsule_bytes"] - 1024 * 1024)
    for label, name, path in paths:
        available = bound - total
        if len(known) >= request["limits"]["files"] or path.lstat().st_size > available:
            failures.append({"root": label, "relative_path": name, "reason": "ORIGINAL_RESOURCE_BOUND"})
            if not partial:
                raise WorkflowRejected("fixedtxt-preparation-original-inventory-budget")
            continue
        try:
            raw = regular(path, max(1, available))[0]
            known[(label, name)] = raw
            total += len(raw)
        except Exception as error:
            failures.append({"root": label, "relative_path": name, "reason": type(error).__name__})
            if not partial:
                raise
    # Conditional, private diagnostics are outside P's required Native roster.
    # The root and every parent are held open. Never resolve or walk owner paths
    # and then open them again by their pathname.
    import native_evidence_projection as core
    handles = None
    try:
        try:
            handles = core.RootHandles({"observation": str(layout["observation_root"])})
        except FileNotFoundError:
            pass  # No owner observation existed at this selection boundary.
        if handles is not None:
            for name, maximum in _owner_diagnostic_limits(request["fixture"]).items():
                try:
                    available = min(maximum, max(0, bound - total))
                    if len(known) >= request["limits"]["files"]:
                        available = 0
                    raw = _read_owner_diagnostic(handles, name, available, fixture=request["fixture"])
                    if raw is None:
                        continue
                    need(len(known) < request["limits"]["files"], "ORIGINAL_RESOURCE_BOUND")
                    known[("owner_diagnostics", name)] = raw
                    total += len(raw)
                except Exception as error:
                    reason = ("ORIGINAL_RESOURCE_BOUND" if isinstance(error, WorkflowRejected)
                              and str(error) == "ORIGINAL_RESOURCE_BOUND" else type(error).__name__)
                    failures.append({"root": "owner_diagnostics", "relative_path": name, "reason": reason})
                    if not partial:
                        raise
            extra, negative_failures = _collect_failed_output(handles, known, current, request,
                max(0, bound - total), max(0, request["limits"]["files"] - len(known)))
            need(set(extra).isdisjoint(known), "failed-output-original-collision")
            known.update(extra)
            total += sum(map(len, extra.values()))
            failures.extend(negative_failures)
    except Exception as error:
        if not any(row["root"] == "owner_diagnostics" for row in failures):
            failures.append({"root": "owner_diagnostics", "reason": type(error).__name__})
        if not partial:
            raise
    finally:
        if handles is not None:
            handles.close()
    need(partial or not failures, "fixedtxt-preparation-originals-incomplete")
    return known, failures


def seal_workflow_originals():
    """One final encrypted copy also covers preparation/transport failures.

    It starts no provider and never repeats the selected native custody child.
    Completeness here concerns only the explicitly named workflow roots.
    """
    import native_fixedtxt_g2_admission as fixed
    import native_mlt_g2_custody as custody
    from ci_private_evidence import regular, encrypt_snapshot, new_bytes
    current = workflow_context()
    request_raw, request, clock_raw, clock = load_original_request(current)
    session = current_session(current, request_raw, clock_raw)
    layout = owned_layout(current)
    bootstrap = custody.runtime_preparation_dispatch(
        regular(layout["control_root"] / "runtime-bootstrap.original.json", 16384)[0], current, scenario=SCOPE)
    directory = Path(os.environ["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-workflow-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    resource_known, resource_manifest = select_owner_resource_custody(current, request, request_raw, session,
        action="seal-workflow")
    resource_manifest_raw = canonical(resource_manifest)
    new_bytes(layout["control_root"] / "resource-workflow-manifest.original.json", resource_manifest_raw)
    known, failures = collect_workflow_originals(current, request, partial=True)
    total = sum(len(raw) for raw in known.values())
    for name, raw in sorted(resource_known.items()):
        if len(known) >= request["limits"]["files"] or total + len(raw) > request["review_capsule_bytes"] - 1024 * 1024:
            failures.append({"root": "owner_resources", "relative_path": name, "reason": "ORIGINAL_RESOURCE_BOUND"})
            continue
        known[("owner_resources", name)] = raw
        total += len(raw)
    metadata = canonical({"schema": "motionwright.native.fixedtxt-workflow-snapshot/1", "context": current,
        "files": [{"path": root + "/" + name, **original_descriptor(raw)}
                  for (root, name), raw in sorted(known.items())], "failures": failures,
        "scope": ["FixedTxt control", "FixedTxt inputs", "existing runtime custody", "original owner-resource tail",
                  "seven conditional literal owner diagnostics; no owner-tree completeness claim"],
        "resource_custody_manifest": original_descriptor(resource_manifest_raw),
        "native_result_inferred": False, "automatic_retry": False})
    need(len(metadata) <= 512 * 1024, "fixedtxt-workflow-original-manifest-bound")
    originals = {root + "/" + name: raw for (root, name), raw in known.items()}
    envelope = encrypt_snapshot(fixed._pack_originals(originals, metadata), bootstrap["recipient"], current)
    need(len(envelope) + 8192 <= request["review_capsule_bytes"], "fixedtxt-workflow-envelope-bound")
    new_bytes(directory / "sealed-workflow-originals.bin", envelope)
    public = canonical({"schema": "motionwright.native.fixedtxt-workflow-public/1", "context": current,
        "sealed_originals": original_descriptor(envelope), "recipient_sha256": bootstrap["recipient"]["sha256"],
        "workflow_original_files": len(known), "workflow_original_failures": len(failures),
        "resource_custody_manifest": original_descriptor(resource_manifest_raw),
        "native_acceptance_inferred": False, "numerical_oracle_approved": False, "automatic_retry": False,
        "within_original_deadline_at_seal": time.monotonic() < __import__("native_fixedtxt_session").effective_deadline(clock, session)})
    new_bytes(directory / "workflow-public.json", public)
    need(time.monotonic() < __import__("native_fixedtxt_session").effective_deadline(clock, session), "fixedtxt-original-clock-exhausted-at-seal")


def prepare_originals():
    import native_mlt_g2_custody as custody
    import native_mlt_g2_observation as g2
    from ci_private_evidence import regular, new_bytes, new_json
    current = workflow_context()
    request_raw, request, clock_raw, clock = load_original_request(current)
    layout = owned_layout(current)
    import native_fixedtxt_session as serial
    session = current_session(current, request_raw, clock_raw)
    reuse = session is not None and session["binding"]["index"] > 0
    retain_owner_resource_prefix(current, session)
    need(time.monotonic() < serial.preparation_deadline(clock, request, session),
         "fixedtxt-preparation-expired-before-source-intake")
    source_before = check_sources(current, request, physical_arm=request["arm"] if reuse else "GREEN")
    new_json(layout["control_root"] / "source-before.original.json", source_before)
    bootstrap_raw = regular(layout["control_root"] / "runtime-bootstrap.original.json", 16384)[0]
    bootstrap = custody.runtime_preparation_dispatch(bootstrap_raw, current, scenario=SCOPE)
    if reuse:
        serial.reuse_preparation(current, request, session)
    else:
        execute_preparation_commands(request, current, clock)
        if session is not None:
            serial.record_shared_preparation(current, request, session)
    sources = strict(regular(layout["inputs_root"] / "source-descriptors.original.json", 65536)[0])
    source_after = check_sources(current, request, physical_arm=request["arm"])
    need(source_before == source_after, "fixedtxt-preparation-changed-Motionwright-source")
    new_json(layout["control_root"] / "source-after.original.json", source_after)
    cycle_raw = make_cycle(request, current, sources)
    serial.verify_reused_cycle(cycle_raw, session)
    runtime_control = serial.runtime_control(current, session)
    prepared = strict(regular(runtime_control / "records/g2-inputs.json", 65536)[0])
    need(set(prepared) == g2.RUNTIME_INPUT_KEYS | {"schema", "private_evidence_dir"}
         and prepared["schema"] == "motionwright.native.fixedtxt-runtime-preparation/1",
         "fixedtxt-real-prepared-runtime-shape")
    # Verify installed runtime originals now; the wrapper repeats verification
    # before the native effect. No receipt is synthesized by this composition.
    g2.verify_runtime_inputs({key: prepared[key] for key in g2.RUNTIME_INPUT_KEYS},
                             source_sha=current["source_sha"], semwright_sha=current["semwright_sha"])
    runtime_copies = layout["inputs_root"] / "runtime-originals"
    runtime_copies.mkdir(mode=0o700, exist_ok=False)
    for key in ("original_c_source", "repaired_c_source", "compiled_module", "compiler_receipt_path",
                "official_runtime_receipt_path", "installation_receipt_path"):
        raw = regular(Path(prepared[key]))[0]
        new_bytes(runtime_copies / key, raw)
    known, _ = collect_workflow_originals(current, request)
    serial.check_final_provenance_budget(known, session)
    # Preparation's raw streams remain verbatim in the independently reviewed
    # encrypted preparation artifact. The final P keeps its existing literal
    # stream selector unchanged; the delivered handoff pins that original
    # preparation artifact ID and digest instead of widening its stream roster.
    import native_evidence_projection as core
    planned_known = {key: raw for key, raw in known.items()
                     if key[0] in {"control", "inputs"} and not core.excluded(core.member_path(key[1]))}
    plan_raw = make_plan(request, current, cycle_raw, source_after, known=planned_known)
    runtime = {key: prepared[key] for key in g2.RUNTIME_INPUT_KEYS}
    runtime.update(schema="motionwright.native.fixedtxt-v4-g2-inputs/1",
        private_evidence_dir=str(layout["observation_root"]), fixedtxt_admission_id=request["admission_id"],
        fixedtxt_plan={"path": str(layout["plan"]), **original_descriptor(plan_raw)},
        recipient_sha256=bootstrap["recipient"]["sha256"])
    runtime_raw = canonical(runtime)
    need(len(runtime_raw) <= 65536, "fixedtxt-existing-R-bound")
    new_bytes(layout["plan"], plan_raw)
    new_bytes(layout["runtime"], runtime_raw)
    serial.record_first_prepared(current, request, session, known, plan_raw, runtime_raw, cycle_raw)
    review = preparation_snapshot(current, request, plan_raw, runtime_raw, cycle_raw, known, bootstrap["recipient"])
    need(time.monotonic() < serial.preparation_deadline(clock, request, session),
         "fixedtxt-original-preparation-budget-exhausted")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("review_directory=" + str(review) + "\n")


def native_environment(environment, dispatch_raw):
    # The producer can read API metadata only before this boundary. No token or
    # runner artifact credential reaches the existing wrapper/provider tree.
    blocked = ("GH_", "ACTIONS_", "INPUT_", "GIT_CONFIG_")
    result = {key: value for key, value in environment.items()
              if not key.upper().startswith(blocked) and "TOKEN" not in key.upper()
              and key not in {"MW_FIXEDTXT_REQUEST", "MW_FIXEDTXT_DELIVERY", "MW_FIXEDTXT_BOOTSTRAP"}}
    result.update(MW_MLT_G2_ADMISSION=dispatch_raw.decode("utf-8"),
        MW_FIXEDTXT_WORKFLOW_ROUTE="original-r28-delivery/1", MOTIONWRIGHT_APPROVED_MLT_G2="1",
        MOTIONWRIGHT_APPROVED_FIXEDTXT_V4_G2="1")
    return result


def receive_and_run():
    import native_fixedtxt_g2_admission as fixed
    from ci_private_evidence import regular
    current = workflow_context()
    request_raw, request, clock_raw, clock = load_original_request(current)
    session = current_session(current, request_raw, clock_raw)
    layout = owned_layout(current)
    originals = {key: regular(layout[key], maximum)[0] for key, maximum in
                 (("plan", 512 * 1024), ("runtime", 65536), ("cycle", 1024 * 1024))}
    review = {"artifact_id": int(os.environ["MW_FIXEDTXT_REVIEW_ARTIFACT_ID"]),
              "artifact_sha256": os.environ["MW_FIXEDTXT_REVIEW_ARTIFACT_SHA256"]}
    need(review["artifact_id"] > 0 and SHA.fullmatch(review["artifact_sha256"]), "fixedtxt-real-preparation-upload")
    held = wait_for_owner(current=current, request=request, clock=clock, directory=layout["control_root"],
                          originals=originals, review=review, session=session)
    plan = fixed.strict(held.plan_raw, 512 * 1024)
    admitted_original_clock(plan, current=current, runner_temp=os.environ["RUNNER_TEMP"])
    check_sources(current, request, physical_arm=request["arm"])
    environment = native_environment(dict(os.environ), held.dispatch_raw)
    # The existing owner reopens and independently revalidates the committed
    # originals. exec preserves its required real parent/child command shape.
    owner = str(Path(os.environ["GITHUB_WORKSPACE"]) / "tooling/native_fixedtxt_g2_supervisor.py")
    os.execve(sys.executable, [sys.executable, "-B", "-E", "-s", owner, "--run-one"], environment)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    need(args in (["--prepare-originals"], ["--prepare-source-inputs"], ["--receive-and-run"],
                  ["--seal-workflow-originals"]),
         "fixedtxt-closed-workflow-command")
    if args == ["--prepare-originals"]:
        prepare_originals()
    elif args == ["--prepare-source-inputs"]:
        from ci_private_evidence import new_json
        current = workflow_context()
        _, request, _, clock = load_original_request(current)
        need(Path("/proc/self/cgroup").read_text().splitlines()
             == ["0::/motionwright-fixedtxt-" + current["run_id"] + "-1/preparation"],
             "fixedtxt-source-intake-original-preparation-containment")
        sources = intake_sources(request, current)
        apply_reviewed_arm(request, sources)
        new_json(owned_layout(current)["inputs_root"] / "source-descriptors.original.json", sources)
    elif args == ["--seal-workflow-originals"]:
        seal_workflow_originals()
    else:
        receive_and_run()


if __name__ == "__main__":
    import native_fixedtxt_g2_supervisor as original_owner
    previous_handlers = {kind: signal.signal(kind, original_owner._cancel)
                         for kind in (signal.SIGTERM, signal.SIGINT)}
    try:
        main()
    except Exception as error:
        print("FixedTxt workflow stopped; original evidence retained; no automatic retry: " + type(error).__name__,
              file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        for kind, handler in previous_handlers.items():
            signal.signal(kind, handler)
