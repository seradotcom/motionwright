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
import zipfile


REPO = "seradotcom/motionwright"
WORKFLOW_PATH = ".github/workflows/native-mlt-sequence-e2e.yml"
WORKFLOW_ENDPOINT = "repos/" + REPO + "/actions/workflows/native-mlt-sequence-e2e.yml"
SCOPE = "fixedtxt-v4-one-fixture"
DELIVERY_MAX = 65536
DELIVERY_MEMBERS_MAX = 60 * 1024
API_JOURNAL_MAX = 1024 * 1024
NATIVE_PATH = "integrations/composition/motion/native.ts"
PAIR = {
    "RED": "dfe6c0fc226d7e9017eb718c7e745d942c4067a54f968d4483b80bbdd51b4fa8",
    "GREEN": "a400b38608e169a20e7ade099aa8632c11629152951b9e62a0fb66858b530f36",
}
FIXTURE_HELPER = "crates/driver-motion-canvas/examples/support/fixed_text_fixtures_v4.rs"
FIXTURE_HELPER_SHA256 = "3e1da867921c1f0a82342ac0c9c4cc85f7081ad5ad8c5dab0ade449f63a1c907"
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


def artifact_name(target_run):
    need(type(target_run) is str and RUN.fullmatch(target_run), "fixedtxt-delivery-target")
    return "motionwright-fixedtxt-r28-delivery-" + target_run + "-1"


def original_descriptor(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def select_delivery_artifact(listing, target_run):
    """Select the complete name result BEFORE provenance, digest or content filters."""
    need(type(listing) is dict and type(listing.get("total_count")) is int
         and type(listing.get("artifacts")) is list, "fixedtxt-delivery-list-shape")
    count, items = listing["total_count"], listing["artifacts"]
    need(count in (0, 1) and len(items) == count, "fixedtxt-delivery-not-unique")
    if count == 0:
        return None
    item = items[0]
    need(type(item) is dict and item.get("name") == artifact_name(target_run),
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
                               review_artifact, held_plan, held_runtime, held_cycle):
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
    need(set(handoff) == {"schema", "target_run_id", "delivery_run_id", "source_sha", "workflow_id",
                         "review_artifact_id", "review_artifact_sha256", "dispatch_original"}
         and handoff["schema"] == "motionwright.native.fixedtxt-workflow-delivery/1"
         and handoff["target_run_id"] == current["run_id"]
         and handoff["delivery_run_id"] == str(delivery["id"])
         and handoff["source_sha"] == current["source_sha"]
         and handoff["workflow_id"] == workflow["id"], "fixedtxt-delivery-handoff-binding")
    review = strict(originals["review-artifact.original.json"])
    for observed in (review, review_artifact):
        checked_artifact(observed, run=target,
            expected_name="motionwright-fixedtxt-preparation-" + current["run_id"] + "-1",
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


def wait_for_owner(*, current, request, clock, directory, originals, review):
    import native_fixedtxt_g2_admission as fixed
    from ci_private_evidence import new_bytes, new_json
    wait_until = clock["deadline_monotonic"] - request["limits"]["custody_seconds"] - request["native_seconds"]
    need(time.monotonic() < wait_until, "fixedtxt-preparation-exhausted-native-reserve")
    api = ReadOnlyApi(directory, wait_until)
    try:
        workflow = strict(api.get(WORKFLOW_ENDPOINT))
        target = strict(api.get("repos/" + REPO + "/actions/runs/" + current["run_id"]))
        checked_run(target, run_id=current["run_id"], source_sha=current["source_sha"],
                    workflow_id=checked_workflow(workflow))
        route = "repos/" + REPO + "/actions/artifacts?name=" + artifact_name(current["run_id"]) + "&per_page=2&page=1"
        while True:
            need(time.monotonic() < wait_until, "fixedtxt-external-delivery-not-observed-before-deadline")
            item = select_delivery_artifact(strict(api.get(route)), current["run_id"])
            if item is not None:
                break
            time.sleep(min(1, max(0, wait_until - time.monotonic())))
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
            time.sleep(min(1, max(0, wait_until - time.monotonic())))
        checked_artifact(item, run=delivery, expected_name=artifact_name(current["run_id"]), maximum=DELIVERY_MAX)
        fresh = select_delivery_artifact(strict(api.get(route)), current["run_id"])
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
            held_runtime=originals["runtime"], held_cycle=originals["cycle"])
        final_selection = select_delivery_artifact(strict(api.get(route)), current["run_id"])
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


def preflight_request_capacity(value, *, source_zip_bytes=0):
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
    need(len(bounds) <= limits["files"], "fixedtxt-preparation-declared-file-budget")
    # Same USTAR headers/padding, metadata, record rounding, envelope and
    # public-projection reserve as the existing R28 plan validator.
    total = sum(bounds)
    tar_bound = sum(512 + ((bound + 511) // 512) * 512 for bound in bounds)
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
    review_total = sum(review_bounds)
    review_tar = sum(512 + ((bound + 511) // 512) * 512 for bound in review_bounds)
    review_tar += 512 + 512 * 1024 + 1024
    review_tar = ((review_tar + 10239) // 10240) * 10240
    # Existing encrypted envelope/public-projection reserves plus the review
    # ZIP's existing 8192 bytes. Keep collect_workflow_originals' 1 MiB reserve.
    review_bound = review_tar + 8192 + 64 + 512 * 1024 + 8192
    need(len(review_bounds) <= limits["files"] and review_total <= review - 1024 * 1024
         and review_bound <= review, "fixedtxt-preparation-declared-review-capsule-budget")
    return {"mandatory_role_counts": counts, "declared_files": len(bounds),
            "declared_raw_bytes": total, "declared_capsule_bound": capsule_bound,
            "review_declared_files": len(review_bounds), "review_declared_raw_bytes": review_total,
            "review_declared_capsule_bound": review_bound, "source_zip_observed_bytes": source_zip_bytes,
            "future_original_sizes_verified": False}


def checked_request(raw, *, current):
    """Preparation data only. Actual P/R/Q still require an external R28 dispatch."""
    from native_film180_evidence_selection import fixedtxt_required_members, FIXEDTXT_KEYS
    import native_fixedtxt_g2_admission as fixed
    import native_mlt_g2_custody as custody
    value = strict(raw, 49152)
    need(set(value) == {"schema", "source_sha", "source_tree", "semwright_sha", "semwright_tree",
         "arm", "fixture", "purpose", "admission_id", "limits", "preparation_seconds", "native_seconds",
         "upload_seconds", "attempt_minutes", "preparation_stream_bytes", "review_capsule_bytes", "role_limits", "sources"}
         and value["schema"] == "motionwright.native.fixedtxt-workflow-request/1",
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
    need(sources["red_native"]["sha256"] == PAIR["RED"], "fixedtxt-original-reviewed-red-source")
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


def admitted_original_clock(plan, *, current, runner_temp):
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
    return clock, request


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
    def __init__(self, original, request):
        self.started, self.total = original["started_monotonic"], original["deadline_monotonic"]
        self.until = self.started + request["preparation_seconds"]
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
         "-p", "semwright-cli", "--bins"],
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
         "e0d32f13536d568762be4b426edf9dce11602a3ec0eea4bc334d5346316b515a",
         "--compiler-receipt", str(control / "intake/compiler_receipt_path"), "--compiler-receipt-sha256",
         "cefa3ed54d02eaf894245248c07a39439489566d56a94b7f356748eba63cb2e8",
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
    timeline = PreparationClock(clock, request)
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
                         and not result["overflow"] and not result["deadline_exceeded"],
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
    preflight_request_capacity(request, source_zip_bytes=len(archive))
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
    """Only the disposable checkout; preserve the committed original before RED overlay."""
    from ci_private_evidence import regular, new_bytes
    sdk = Path(os.environ["SEMWRIGHT_CHECKOUT"])
    path = sdk / NATIVE_PATH
    original, observed = regular(path, 2 * 1024 * 1024)
    need(sha(original) == PAIR["GREEN"], "fixedtxt-original-GREEN-source-before-overlay")
    new_bytes(Path(sources["red_native"]["path"]).parents[1] / "committed-native.original.ts", original)
    if request["arm"] == "RED":
        replacement = Path(sources["red_native"]["path"]).read_bytes()
        need(sha(replacement) == PAIR["RED"], "fixedtxt-original-RED-source-before-overlay")
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


def collect_workflow_originals(current, request, *, partial=False):
    """Retain this route's private originals; never walk an owner or a checkout."""
    from ci_private_evidence import regular
    layout = owned_layout(current)
    runtime = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
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
        if len(known) >= request["limits"]["files"] or path.stat().st_size > bound - total:
            failures.append({"root": label, "relative_path": name, "reason": "ORIGINAL_RESOURCE_BOUND"})
            if not partial:
                raise WorkflowRejected("fixedtxt-preparation-original-inventory-budget")
            continue
        try:
            raw = regular(path, max(1, bound-total))[0]
            known[(label, name)] = raw
            total += len(raw)
        except Exception as error:
            failures.append({"root": label, "relative_path": name, "reason": type(error).__name__})
            if not partial:
                raise
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
    _, request, _, clock = load_original_request(current)
    layout = owned_layout(current)
    bootstrap = custody.runtime_preparation_dispatch(
        regular(layout["control_root"] / "runtime-bootstrap.original.json", 16384)[0], current, scenario=SCOPE)
    directory = Path(os.environ["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-workflow-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    known, failures = collect_workflow_originals(current, request, partial=True)
    metadata = canonical({"schema": "motionwright.native.fixedtxt-workflow-snapshot/1", "context": current,
        "files": [{"path": root + "/" + name, **original_descriptor(raw)}
                  for (root, name), raw in sorted(known.items())], "failures": failures,
        "scope": ["FixedTxt control", "FixedTxt inputs", "existing runtime custody"],
        "native_result_inferred": False, "automatic_retry": False})
    need(len(metadata) <= 512 * 1024, "fixedtxt-workflow-original-manifest-bound")
    originals = {root + "/" + name: raw for (root, name), raw in known.items()}
    envelope = encrypt_snapshot(fixed._pack_originals(originals, metadata), bootstrap["recipient"], current)
    need(len(envelope) + 8192 <= request["review_capsule_bytes"], "fixedtxt-workflow-envelope-bound")
    new_bytes(directory / "sealed-workflow-originals.bin", envelope)
    public = canonical({"schema": "motionwright.native.fixedtxt-workflow-public/1", "context": current,
        "sealed_originals": original_descriptor(envelope), "recipient_sha256": bootstrap["recipient"]["sha256"],
        "workflow_original_files": len(known), "workflow_original_failures": len(failures),
        "native_acceptance_inferred": False, "numerical_oracle_approved": False, "automatic_retry": False,
        "within_original_deadline_at_seal": time.monotonic() < clock["deadline_monotonic"]})
    new_bytes(directory / "workflow-public.json", public)
    need(time.monotonic() < clock["deadline_monotonic"], "fixedtxt-original-clock-exhausted-at-seal")


def prepare_originals():
    import native_mlt_g2_custody as custody
    import native_mlt_g2_observation as g2
    from ci_private_evidence import regular, new_bytes, new_json
    current = workflow_context()
    request_raw, request, clock_raw, clock = load_original_request(current)
    layout = owned_layout(current)
    need(time.monotonic() < clock["started_monotonic"] + request["preparation_seconds"],
         "fixedtxt-preparation-expired-before-source-intake")
    source_before = check_sources(current, request, physical_arm="GREEN")
    new_json(layout["control_root"] / "source-before.original.json", source_before)
    bootstrap_raw = regular(layout["control_root"] / "runtime-bootstrap.original.json", 16384)[0]
    bootstrap = custody.runtime_preparation_dispatch(bootstrap_raw, current, scenario=SCOPE)
    execute_preparation_commands(request, current, clock)
    sources = strict(regular(layout["inputs_root"] / "source-descriptors.original.json", 65536)[0])
    source_after = check_sources(current, request, physical_arm=request["arm"])
    need(source_before == source_after, "fixedtxt-preparation-changed-Motionwright-source")
    new_json(layout["control_root"] / "source-after.original.json", source_after)
    cycle_raw = make_cycle(request, current, sources)
    runtime_control = Path(os.environ["RUNNER_TEMP"]) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")
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
    review = preparation_snapshot(current, request, plan_raw, runtime_raw, cycle_raw, known, bootstrap["recipient"])
    need(time.monotonic() < clock["started_monotonic"] + request["preparation_seconds"],
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
    _, request, _, clock = load_original_request(current)
    layout = owned_layout(current)
    originals = {key: regular(layout[key], maximum)[0] for key, maximum in
                 (("plan", 512 * 1024), ("runtime", 65536), ("cycle", 1024 * 1024))}
    review = {"artifact_id": int(os.environ["MW_FIXEDTXT_REVIEW_ARTIFACT_ID"]),
              "artifact_sha256": os.environ["MW_FIXEDTXT_REVIEW_ARTIFACT_SHA256"]}
    need(review["artifact_id"] > 0 and SHA.fullmatch(review["artifact_sha256"]), "fixedtxt-real-preparation-upload")
    held = wait_for_owner(current=current, request=request, clock=clock, directory=layout["control_root"],
                          originals=originals, review=review)
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
