"""Serial FixedTxt preparation reuse; this module never issues Native authority.

Every case retains a v1 request/clock and requires its own original external
R28.  The original session clock can only shorten a case.  Shared compiler and
runtime files are frozen read-only after the first fifteen preparation stages;
later cases retain those stages as provenance, never as new executions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time

MIB = 1024 * 1024
SESSION_SCHEMA = "motionwright.native.fixedtxt-session-clock/1"
PLAN_SCHEMA = "motionwright.native.fixedtxt-session-plan/1"
BINDING_SCHEMA = "motionwright.native.fixedtxt-session-case/1"
ROSTER = (
    "fixed-multirun-control", "fixed-multirun", "fixed-causal",
    "flow-control-base", "flow-control-with-fixed", "flow-text-control",
    "fixed-transform-clip", "fixed-camera-scene", "fixed-counter-captions",
    "fixed-layer-behind", "fixed-layer-equal", "fixed-layer-front",
)
SESSION_MEMBERS = {
    "session-clock.original.json": 65536,
    "session-plan.original.json": 65536,
    "session-template.original.json": 49152,
    "session-case.original.json": 65536,
}
# Added final-plan provenance is explicitly bounded before any setup effect.
# Streams remain in the preparation envelope under the existing exclusion rule.
SESSION_FINAL_RESERVE = 2 * MIB
SESSION_FILE_RESERVE = 192
UNIX_PATH_LIMIT = 107
OWNER_SOCKET_TAIL = "/motionwright-native-av-g2-" + "x" * 8 + "/runtime/broker.sock"
SHA = re.compile(r"[0-9a-f]{64}\Z")


class SessionRejected(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise SessionRejected(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor(raw):
    return {"bytes": len(raw), "sha256": digest(raw)}


def strict(raw, maximum=65536):
    import native_fixedtxt_workflow as route
    return route.strict(raw, maximum)


def read(path, maximum=65536):
    from ci_private_evidence import regular
    return regular(Path(path), maximum)[0]


def write(path, raw):
    from ci_private_evidence import new_bytes
    new_bytes(Path(path), raw)


def output(**values):
    need(all(type(value) is str and "\n" not in value and "\r" not in value
             for value in values.values()), "fixedtxt-session-output-lines")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("".join(key + "=" + value + "\n" for key, value in values.items()))


def namespace(base, request_sha256):
    """A short physical name with an independently retained full digest.

    Creation is exclusive.  A truncated-prefix collision is a negative result,
    never permission to reuse another case's directory or any of its originals.
    """
    need(type(base) is str and base.startswith("/") and str(PurePosixPath(base)) == base
         and not {".", ".."} & set(PurePosixPath(base).parts)
         and type(request_sha256) is str and SHA.fullmatch(request_sha256),
         "fixedtxt-session-namespace-input")
    name = base + "/fx" + request_sha256[:20]
    need(len((name + OWNER_SOCKET_TAIL).encode()) <= UNIX_PATH_LIMIT,
         "fixedtxt-session-AF_UNIX-path-bound")
    return name


def derive_request(template_raw, index):
    need(type(index) is int and 0 <= index < len(ROSTER), "fixedtxt-session-case-index")
    template = strict(template_raw, 49152)
    need(template.get("purpose") == "candidate24" and template.get("fixture") == ROSTER[0]
         and template.get("arm") in ("RED", "GREEN")
         and type(template.get("admission_id")) is str
         and re.fullmatch(r"[a-z][a-z0-9-]{0,83}", template["admission_id"]),
         "fixedtxt-session-original-template-selection")
    if index == 0:
        return template_raw
    value = copy.deepcopy(template)
    value["fixture"] = ROSTER[index]
    value["admission_id"] = template["admission_id"] + "-case-%02d" % index
    if value["fixture"] == "fixed-counter-captions":
        # Transport only: no PNG rewrite, omitted frame or reduced decode.
        for role in ("native-png", "selected-png"):
            value["role_limits"][role] = min(value["role_limits"][role], 750 * 1024)
    raw = canonical(value)
    need(len(raw) <= 49152, "fixedtxt-session-derived-request-bound")
    return raw


def artifact_suffix(binding):
    if binding is None:
        return ""
    need(type(binding) is dict and type(binding.get("request")) is dict
         and type(binding["request"].get("sha256")) is str
         and SHA.fullmatch(binding["request"]["sha256"]), "fixedtxt-session-artifact-binding")
    return "-" + binding["request"]["sha256"]


def validate_clock_bundle(raws, request_raw, clock_raw, *, current, runner_temp=None):
    """Offline-capable check of literal originals; no environment grant.

    Callers loading a reviewed P must first check each original against the
    independent P digest.  The v1 clock is returned unchanged by its own parser.
    """
    present = set(raws) & set(SESSION_MEMBERS)
    if not present:
        return None
    need(present == set(SESSION_MEMBERS), "fixedtxt-session-clock-roster")
    for name, maximum in SESSION_MEMBERS.items():
        need(type(raws[name]) is bytes and 0 < len(raws[name]) <= maximum,
             "fixedtxt-session-original-size")
    session = strict(raws["session-clock.original.json"])
    plan = strict(raws["session-plan.original.json"])
    binding = strict(raws["session-case.original.json"])
    clock, request = strict(clock_raw), strict(request_raw, 49152)
    template_raw = raws["session-template.original.json"]
    template = strict(template_raw, 49152)
    need(set(session) == {"schema", "context", "boot_id", "started_monotonic", "deadline_monotonic",
                          "template", "roster"}
         and session["schema"] == SESSION_SCHEMA and session["context"] == current
         and session["template"] == descriptor(template_raw)
         and session["roster"] == list(ROSTER), "fixedtxt-session-original-clock-binding")
    need(set(plan) == {"schema", "context", "arm", "runner_temp", "session_clock", "template", "cases"}
         and plan["schema"] == PLAN_SCHEMA and plan["context"] == current
         and plan["arm"] == template["arm"]
         and plan["session_clock"] == descriptor(raws["session-clock.original.json"])
         and plan["template"] == descriptor(template_raw), "fixedtxt-session-plan-binding")
    need(set(binding) == {"schema", "context", "index", "fixture", "arm", "request", "clock",
                          "session_plan", "session_clock", "namespace", "effective_deadline_monotonic"}
         and binding["schema"] == BINDING_SCHEMA and binding["context"] == current,
         "fixedtxt-session-case-shape")
    index = binding["index"]
    expected_request = derive_request(template_raw, index)
    need(request_raw == expected_request and binding["request"] == descriptor(request_raw)
         and binding["clock"] == descriptor(clock_raw)
         and binding["session_plan"] == descriptor(raws["session-plan.original.json"])
         and binding["session_clock"] == descriptor(raws["session-clock.original.json"])
         and binding["fixture"] == request["fixture"] == ROSTER[index]
         and binding["arm"] == request["arm"] == template["arm"], "fixedtxt-session-case-identity")
    expected_cases = [{"index": number, "fixture": fixture,
        "request": descriptor(derive_request(template_raw, number)),
        "namespace": namespace(plan["runner_temp"], digest(derive_request(template_raw, number)))}
        for number, fixture in enumerate(ROSTER)]
    need(plan["cases"] == expected_cases and len({row["namespace"] for row in expected_cases}) == len(ROSTER)
         and binding["namespace"] == expected_cases[index]["namespace"]
         and (runner_temp is None or runner_temp == binding["namespace"]),
         "fixedtxt-session-namespace-full-digest-binding")
    need(set(clock) == {"schema", "context", "boot_id", "started_monotonic", "deadline_monotonic", "request_sha256"}
         and clock["schema"] == "motionwright.native.fixedtxt-original-clock/1"
         and clock["context"] == current and clock["request_sha256"] == digest(request_raw)
         and clock["boot_id"] == session["boot_id"]
         and type(session["boot_id"]) is str
         and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session["boot_id"]),
         "fixedtxt-session-original-v1-clock")
    ss, st = session["started_monotonic"], session["deadline_monotonic"]
    cs, ct = clock["started_monotonic"], clock["deadline_monotonic"]
    need(all(type(n) in (int, float) and math.isfinite(n) for n in (ss, st, cs, ct))
         and 0 <= ss <= cs < st and cs < ct
         and abs(st - ss - template["limits"]["attempt_seconds"]) < 1e-6
         and abs(ct - cs - request["limits"]["attempt_seconds"]) < 1e-6
         and (index != 0 or (cs, ct) == (ss, st)), "fixedtxt-session-original-durations")
    deadline = min(st, ct)
    need(type(binding["effective_deadline_monotonic"]) in (int, float)
         and binding["effective_deadline_monotonic"] == deadline
         and cs < deadline, "fixedtxt-session-never-extends-case")
    return {"binding": binding, "session": session, "plan": plan,
            "effective_deadline_monotonic": deadline}


def clock_from_control(control, request_raw, clock_raw, *, current, runner_temp):
    names = [name for name in SESSION_MEMBERS if (Path(control) / name).exists()]
    raws = {name: read(Path(control) / name, SESSION_MEMBERS[name]) for name in names}
    return validate_clock_bundle(raws, request_raw, clock_raw, current=current, runner_temp=runner_temp)


def clock_from_plan(plan, *, current, runner_temp, request_raw, clock_raw):
    import native_fixedtxt_g2_admission as fixed
    import native_evidence_projection as core
    rows = {(row["root"], row["relative_path"]): row for row in plan["members"]}
    names = [name for name in SESSION_MEMBERS if ("control", name) in rows]
    if not names:
        return None
    need(set(names) == set(SESSION_MEMBERS), "fixedtxt-session-reviewed-clock-roster")
    handles = core.RootHandles({"control": fixed.owner_layout(current=current, runner_temp=runner_temp)["control_root"]})
    raws = {}
    try:
        for name in names:
            row = rows[("control", name)]
            need(row["role"] == "input-original" and type(row["expected_sha256"]) is str
                 and 0 < row["maximum"] <= SESSION_MEMBERS[name], "fixedtxt-session-independent-plan-pin")
            raw, _ = handles.read("control", core.member_path(name), row["maximum"])
            need(digest(raw) == row["expected_sha256"], "fixedtxt-session-reviewed-original-changed")
            raws[name] = raw
        need(handles.stable_roots(), "fixedtxt-session-reviewed-roots-changed")
    finally:
        handles.close()
    return validate_clock_bundle(raws, request_raw, clock_raw, current=current, runner_temp=runner_temp)


def effective_deadline(clock, session):
    return clock["deadline_monotonic"] if session is None else session["effective_deadline_monotonic"]


def preparation_deadline(clock, request, session):
    # Preserve the case's setup cap and all original Native/custody reserves.
    return min(clock["started_monotonic"] + request["preparation_seconds"],
               effective_deadline(clock, session) - request["native_seconds"] - request["limits"]["custody_seconds"])


def quiescent(current):
    import native_fixedtxt_g2_supervisor as owner
    result = []
    for phase in ("preparation", "work", "custody"):
        group = owner.OwnerCgroup(current["run_id"], phase)
        try:
            need(not group.populated(), "fixedtxt-session-prior-owner-still-live")
            result.append({"phase": phase, "device": group.identity.st_dev,
                           "inode": group.identity.st_ino, "populated": False})
        finally:
            group.close()
    return result


def session_root():
    root = Path(os.environ["MW_FIXEDTXT_SESSION_ROOT"])
    need(root.is_absolute() and root.resolve(strict=True) == root
         and not any(p.is_symlink() for p in (root, *root.parents)), "fixedtxt-session-real-root")
    return root


def open_session():
    import native_fixedtxt_workflow as route
    current = route.workflow_context()
    template_raw, template, clock_raw, clock = route.load_original_request(current)
    need(os.environ.get("MW_FIXEDTXT_SESSION_CASES") == "all-twelve-originals",
         "fixedtxt-session-explicit-original-roster")
    root = Path(os.environ["RUNNER_TEMP"])
    need(str(root).startswith("/home/runner/work/_temp") and root.resolve(strict=True) == root,
         "fixedtxt-session-disposable-runner-root")
    # Check all twelve declarations before further installation or build effects.
    for index in range(len(ROSTER)):
        raw = derive_request(template_raw, index)
        value = route.checked_request(raw, current=current)
        route.preflight_request_capacity(value, session=True)
    control = route.owned_layout(current)["control_root"]
    bootstrap_raw = read(control / "runtime-bootstrap.original.json", 16384)
    session = {"schema": SESSION_SCHEMA, "context": current, "boot_id": clock["boot_id"],
        "started_monotonic": clock["started_monotonic"], "deadline_monotonic": clock["deadline_monotonic"],
        "template": descriptor(template_raw), "roster": list(ROSTER)}
    session_raw = canonical(session)
    plan = {"schema": PLAN_SCHEMA, "context": current, "arm": template["arm"], "runner_temp": str(root),
        "session_clock": descriptor(session_raw), "template": descriptor(template_raw),
        "cases": [{"index": index, "fixture": fixture,
                   "request": descriptor(derive_request(template_raw, index)),
                   "namespace": namespace(str(root), digest(derive_request(template_raw, index)))}
                  for index, fixture in enumerate(ROSTER)]}
    need(len({row["namespace"] for row in plan["cases"]}) == len(ROSTER), "fixedtxt-session-prefix-collision")
    directory = root / ("mw-fixedtxt-session-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    for name, raw in (("session-clock.original.json", session_raw),
                      ("session-plan.original.json", canonical(plan)),
                      ("session-template.original.json", template_raw),
                      ("first-clock.original.json", clock_raw),
                      ("runtime-bootstrap.original.json", bootstrap_raw)):
        write(directory / name, raw)
    output(session_root=str(directory))


def begin_case(index):
    import native_fixedtxt_workflow as route
    import native_fixedtxt_g2_admission as fixed
    current = route.workflow_context()
    root = session_root()
    template_raw = read(root / "session-template.original.json", 49152)
    raw = derive_request(template_raw, index)
    request = route.checked_request(raw, current=current)
    session_raw = read(root / "session-clock.original.json")
    session = strict(session_raw)
    plan_raw = read(root / "session-plan.original.json")
    plan = strict(plan_raw)
    need(plan["context"] == current, "fixedtxt-session-context-changed")
    if index:
        previous = strict(read(root / ("case-%02d.closed.original.json" % (index - 1))))
        need(previous["context"] == current and previous["index"] == index - 1
             and previous["mechanics_complete"] is True and previous["native_acceptance"] is False,
             "fixedtxt-session-prior-case-not-closed")
        import native_owner_resources as resources
        resources.resource_originals("fixedtxt", "close-case", index - 1, completed=True)
    need(not any((root / ("case-%02d.started.original.json" % later)).exists()
                 for later in range(index, len(ROSTER))), "fixedtxt-session-no-case-retry-or-overlap")
    empty = quiescent(current)
    # No deletion of previous originals to create room. Existing physical floor.
    need(shutil.disk_usage(plan["runner_temp"]).free >= 10 * 1024**3, "fixedtxt-session-original-disk-floor")
    now = time.monotonic()
    if index == 0:
        clock_raw = read(root / "first-clock.original.json")
        clock = strict(clock_raw)
    else:
        clock = {"schema": "motionwright.native.fixedtxt-original-clock/1", "context": current,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "started_monotonic": now, "deadline_monotonic": now + request["limits"]["attempt_seconds"],
            "request_sha256": digest(raw)}
        clock_raw = canonical(clock)
    destination = namespace(plan["runner_temp"], digest(raw))
    binding = {"schema": BINDING_SCHEMA, "context": current, "index": index,
        "fixture": request["fixture"], "arm": request["arm"], "request": descriptor(raw),
        "clock": descriptor(clock_raw), "session_plan": descriptor(plan_raw),
        "session_clock": descriptor(session_raw), "namespace": destination,
        "effective_deadline_monotonic": min(clock["deadline_monotonic"], session["deadline_monotonic"])}
    raws = {"session-clock.original.json": session_raw, "session-plan.original.json": plan_raw,
        "session-template.original.json": template_raw, "session-case.original.json": canonical(binding)}
    bound = validate_clock_bundle(raws, raw, clock_raw, current=current, runner_temp=destination)
    need(now < preparation_deadline(clock, request, bound), "fixedtxt-session-reserves-exhausted-before-case")
    # Durable intent precedes namespace creation. A failed creation is consumed.
    write(root / ("case-%02d.started.original.json" % index), canonical({"context": current,
        "binding": binding, "quiescent": empty, "observed_monotonic": now, "automatic_retry": False}))
    Path(destination).mkdir(mode=0o700, exist_ok=False)
    layout = fixed.owner_layout(current=current, runner_temp=destination)
    control = Path(layout["control_root"])
    control.mkdir(mode=0o700, exist_ok=False)
    for name, value in {**raws, "request.original.json": raw, "clock.original.json": clock_raw,
                       "runtime-bootstrap.original.json": read(root / "runtime-bootstrap.original.json", 16384)}.items():
        write(control / name, value)
    suffix = artifact_suffix(binding)
    stem = current["run_id"] + "-1" + suffix
    output(case_temp=destination, fixture=request["fixture"], request_sha256=digest(raw),
           review_name="motionwright-fixedtxt-preparation-" + stem,
           workflow_name="motionwright-fixedtxt-workflow-" + stem,
           selected_name="motionwright-fixedtxt-originals-" + stem)


def unescape_mount(value):
    return re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)


def mount_rows(raw, path):
    need(type(raw) is bytes and 0 < len(raw) <= MIB, "fixedtxt-session-mount-table-bound")
    result = []
    for line in raw.decode("utf-8", "strict").splitlines():
        fields = line.split()
        need("-" in fields and len(fields) >= 10, "fixedtxt-session-kernel-mount-shape")
        destination = unescape_mount(fields[4])
        if destination == path or destination.startswith(path + "/"):
            split = fields.index("-")
            result.append({"mount_id": fields[0], "parent_id": fields[1], "device": fields[2],
                "root": unescape_mount(fields[3]), "mountpoint": destination,
                "options": fields[5].split(","), "fs_type": fields[split + 1],
                "source": unescape_mount(fields[split + 2]), "super_options": fields[split + 3].split(",")})
    return result


def verify_mounts(receipt):
    need(type(receipt) is dict and set(receipt) == {"schema", "context", "targets", "observed_monotonic"}
         and receipt["schema"] == "motionwright.native.fixedtxt-shared-readonly/1"
         and type(receipt["targets"]) is list and len(receipt["targets"]) == 2,
         "fixedtxt-session-readonly-receipt")
    raw = Path("/proc/self/mountinfo").read_bytes()
    actual = []
    for target in receipt["targets"]:
        path = Path(target["path"])
        need(path.resolve(strict=True) == path and not any(p.is_symlink() for p in (path, *path.parents)),
             "fixedtxt-session-frozen-root-replaced")
        observed = path.stat()
        need([observed.st_dev, observed.st_ino] == target["identity"], "fixedtxt-session-frozen-inode-changed")
        rows = mount_rows(raw, str(path))
        need(rows == target["mounts"] and len(rows) == 1 and rows[0]["mountpoint"] == str(path)
             and "ro" in rows[0]["options"] and "rw" not in rows[0]["options"],
             "fixedtxt-session-shared-mount-no-longer-readonly")
        actual.append(target)
    return actual


def freeze_shared(current, session):
    """One recorded bind/remount pair per owned root; never retried on ambiguity."""
    need(session["binding"]["index"] == 0, "fixedtxt-session-freeze-only-after-first-preparation")
    root = session_root()
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    python_root = Path(sys.executable).parent.parent
    targets = [workspace, python_root]
    need(workspace != python_root and not workspace.is_relative_to(python_root)
         and not python_root.is_relative_to(workspace), "fixedtxt-session-disjoint-shared-roots")
    need(python_root.name.startswith("mw-fixedtxt-custody-python-")
         and python_root.parent == Path(session["plan"]["runner_temp"]),
         "fixedtxt-session-owned-custody-interpreter")
    directory = root / "freeze"
    directory.mkdir(mode=0o700, exist_ok=False)
    import native_fixedtxt_workflow as route
    _, request, _, clock = route.load_original_request(current)
    now, until = time.monotonic(), preparation_deadline(clock, request, session)
    need(now < until, "fixedtxt-session-expired-before-freeze")
    import native_fixedtxt_g2_supervisor as owner
    timeline = route.PreparationClock(clock, request, session)
    group = owner.OwnerCgroup(current["run_id"], "preparation")
    results = []
    try:
        for index, path in enumerate(targets):
            need(path.is_absolute() and path.resolve(strict=True) == path
                 and not any(p.is_symlink() for p in (path, *path.parents)), "fixedtxt-session-shared-root-canonical")
            before = mount_rows(Path("/proc/self/mountinfo").read_bytes(), str(path))
            need(not before, "fixedtxt-session-shared-root-already-or-nested-mounted")
            for offset, command in enumerate((["sudo", "-n", "mount", "--bind", str(path), str(path)],
                ["sudo", "-n", "mount", "-o", "remount,bind,ro", str(path)])):
                # Distinct original preparation stages under the existing owned
                # cgroup/supervisor. A timeout retains terminal UNKNOWN and
                # kills the owned cohort; no mount command is replayed.
                phase = "prepare-%02d" % (15 + 2 * index + offset)
                result = owner._run_original(command,
                    environment={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
                    directory=directory, phase=phase, clock=timeline, containment=group, stream_limit=65536)
                need(result["exit_code"] == 0 and result["original_complete"] is True
                     and result["containment_empty"] is True and result["native_outcome"] == "NOT_INFERRED"
                     and not result["overflow"] and not result["deadline_exceeded"],
                     "fixedtxt-session-freeze-incomplete-no-retry")
            inode = path.stat()
            rows = mount_rows(Path("/proc/self/mountinfo").read_bytes(), str(path))
            need(len(rows) == 1 and rows[0]["mountpoint"] == str(path)
                 and "ro" in rows[0]["options"] and "rw" not in rows[0]["options"],
                 "fixedtxt-session-freeze-not-observed")
            results.append({"path": str(path), "identity": [inode.st_dev, inode.st_ino], "mounts": rows})
    finally:
        group.close()
    receipt = {"schema": "motionwright.native.fixedtxt-shared-readonly/1", "context": current,
               "targets": results, "observed_monotonic": time.monotonic()}
    verify_mounts(receipt)
    write(root / "shared-readonly.original.json", canonical(receipt))
    return receipt


def hash_file(path, maximum=512 * MIB):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path and not path.is_symlink(),
         "fixedtxt-session-real-shared-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        start = os.fstat(fd)
        need(stat.S_ISREG(start.st_mode) and 0 < start.st_size <= maximum, "fixedtxt-session-shared-file-bound")
        value, total = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, MIB)
            if not chunk:
                break
            total += len(chunk)
            need(total <= maximum, "fixedtxt-session-shared-file-growth")
            value.update(chunk)
        end = os.fstat(fd)
        stamp = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        need(total == start.st_size and stamp(start) == stamp(end) == stamp(path.stat()),
             "fixedtxt-session-shared-file-changed-during-read")
        return {"path": str(path), "bytes": total, "sha256": value.hexdigest(),
                "device": start.st_dev, "inode": start.st_ino, "mode": stat.S_IMODE(start.st_mode)}
    finally:
        os.close(fd)


def shared_identity(current, request, *, readonly):
    import native_film180_original_g2 as wrapper
    import native_fixedtxt_workflow as route
    verify_mounts(readonly)
    source = route.check_sources(current, request, physical_arm=request["arm"])
    bins = [wrapper.SW_BIN / name for name in ("semwright", "semwrightd", "semwright-sandbox",
            "semwright-motion-canvas-driver", "semwright-mlt-video-driver", "semwright-mlt-runtime-runner")]
    bins += [wrapper.SEM / "target/debug/examples/fixed_text_compile_v4",
             Path(os.environ["GITHUB_WORKSPACE"]) / "target/debug/examples/native-film180-e2e",
             Path(sys.executable).resolve(strict=True)]
    for name in ("node", "ffmpeg"):
        physical = shutil.which(name)
        need(physical is not None, "fixedtxt-session-runtime-executable-missing")
        bins.append(Path(physical).resolve(strict=True))
    binaries = [hash_file(path) for path in bins]
    verify_mounts(readonly)
    return {"schema": "motionwright.native.fixedtxt-shared-identity/1", "context": current,
        "arm": request["arm"], "source": source, "semwright_tree": request["semwright_tree"],
        "native_source_sha256": route.PAIR[request["arm"]], "binaries": binaries,
        "readonly_original": descriptor(canonical(readonly))}


def runtime_control(current, session):
    base = os.environ["RUNNER_TEMP"] if session is None else session["plan"]["cases"][0]["namespace"]
    return Path(base) / ("mw-mlt-g2-control-" + current["run_id"] + "-1")


def record_shared_preparation(current, request, session):
    """Called after the existing fifteen stages and before first P/R/Q."""
    import native_fixedtxt_workflow as route
    readonly = freeze_shared(current, session)
    identity = shared_identity(current, request, readonly=readonly)
    root = session_root()
    write(root / "shared-identity.original.json", canonical(identity))
    control = route.owned_layout(current)["control_root"]
    for name in ("shared-readonly.original.json", "shared-identity.original.json"):
        write(control / name, read(root / name, MIB))
    destination = control / "shared-freeze"
    destination.mkdir(mode=0o700, exist_ok=False)
    for path in sorted((root / "freeze").iterdir()):
        need(path.is_file() and not path.is_symlink(), "fixedtxt-session-freeze-original-type")
        write(destination / path.name, read(path, 65536))


def record_first_prepared(current, request, session, known, plan_raw, runtime_raw, cycle_raw):
    if session is None or session["binding"]["index"] != 0:
        return
    import native_fixedtxt_g2_admission as fixed
    # Preserve only the first preparation snapshot's exact known originals.
    # Future work/transport/owner files are deliberately outside this roster.
    rows = [{"root": label, "relative_path": name, **descriptor(raw)}
            for (label, name), raw in sorted(known.items())]
    value = {"schema": "motionwright.native.fixedtxt-first-prepared/1", "context": current,
        "request": session["binding"]["request"], "namespace": session["binding"]["namespace"],
        "P": descriptor(plan_raw), "R": descriptor(runtime_raw), "Q": descriptor(cycle_raw),
        "files": rows, "new_native_authority": False}
    raw = canonical(value)
    need(len(raw) <= 256 * 1024, "fixedtxt-session-first-prepared-roster-bound")
    write(session_root() / "first-prepared.original.json", raw)


def _copy_first_originals(current, session):
    import native_fixedtxt_g2_admission as fixed
    import native_fixedtxt_workflow as route
    first = session["plan"]["cases"][0]
    layout = fixed.owner_layout(current=current, runner_temp=first["namespace"])
    source_roots = {"control": Path(layout["control_root"]), "inputs": Path(layout["inputs_root"]),
                    "runtime_custody": runtime_control(current, session)}
    ref_raw = read(session_root() / "first-prepared.original.json", 256 * 1024)
    ref = strict(ref_raw, 256 * 1024)
    need(set(ref) == {"schema", "context", "request", "namespace", "P", "R", "Q", "files", "new_native_authority"}
         and ref["schema"] == "motionwright.native.fixedtxt-first-prepared/1" and ref["context"] == current
         and ref["request"] == first["request"] and ref["namespace"] == first["namespace"]
         and ref["new_native_authority"] is False and type(ref["files"]) is list,
         "fixedtxt-session-first-originals-binding")
    current_layout = route.owned_layout(current)
    destination = current_layout["control_root"] / "reused-preparation"
    destination.mkdir(mode=0o700, exist_ok=False)
    write(current_layout["control_root"] / "first-prepared.original.json", ref_raw)
    first_closed = read(session_root() / "case-00.closed.original.json")
    write(current_layout["control_root"] / "first-case-closed.original.json", first_closed)
    seen, total = set(), 0
    for row in ref["files"]:
        need(type(row) is dict and set(row) == {"root", "relative_path", "bytes", "sha256"}
             and row["root"] in source_roots, "fixedtxt-session-first-original-row")
        from native_mlt_g2_custody import safe_member
        safe_member(row["relative_path"])
        key = row["root"] + "/" + row["relative_path"]
        need(key not in seen and type(row["bytes"]) is int and 0 <= row["bytes"] <= 256 * MIB,
             "fixedtxt-session-first-original-unique")
        seen.add(key)
        total += row["bytes"]
        need(total <= 256 * MIB and len(seen) <= 4096, "fixedtxt-session-first-original-total")
        raw = read(source_roots[row["root"]] / row["relative_path"], max(1, row["bytes"]))
        need(descriptor(raw) == {"bytes": row["bytes"], "sha256": row["sha256"]},
             "fixedtxt-session-first-original-modified")
        # Runtime custody keeps its real root. Identical source assets are copied
        # once into current inputs; the first descriptor/Q retain their original
        # physical paths. The first snapshot inventory binds both views exactly.
        if row["root"] == "control" or (row["root"] == "inputs" and row["relative_path"]
                in {"source-descriptors.original.json", fixed.CYCLE_MEMBER}):
            target = destination / key
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            write(target, raw)
    for label, name, path, maximum in (("P", "control/" + fixed.PLAN_MEMBER, layout["plan"], 512 * 1024),
        ("R", "inputs/" + fixed.RUNTIME_MEMBER, layout["runtime"], 65536),
        ("Q", "inputs/" + fixed.CYCLE_MEMBER, layout["cycle"], MIB)):
        raw = read(path, maximum)
        need(descriptor(raw) == ref[label], "fixedtxt-session-first-PRQ-modified")
        target = destination / name
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if target.exists():
            need(read(target, maximum) == raw, "fixedtxt-session-first-PRQ-overlap")
        else:
            write(target, raw)
    return ref, source_roots, destination


def reuse_preparation(current, request, session):
    """No GET, npm, apt, compiler or overlay mutation occurs in this path."""
    import native_fixedtxt_workflow as route
    need(0 < session["binding"]["index"] < len(ROSTER), "fixedtxt-session-reuse-case-index")
    root = session_root()
    readonly_raw = read(root / "shared-readonly.original.json", MIB)
    readonly = strict(readonly_raw, MIB)
    identity_raw = read(root / "shared-identity.original.json", MIB)
    identity = strict(identity_raw, MIB)
    observed = shared_identity(current, request, readonly=readonly)
    need(observed == identity, "fixedtxt-session-reused-build-or-runtime-changed")
    ref, source_roots, retained = _copy_first_originals(current, session)
    layout = route.owned_layout(current)
    inputs = layout["inputs_root"]
    inputs.mkdir(mode=0o700, exist_ok=False)
    first_sources = strict(read(source_roots["inputs"] / "source-descriptors.original.json"))
    import native_fixedtxt_observer_overlay as overlay
    names = {"source-kit.original.zip", *overlay.BUNDLE_PATHS.values()}
    for key in ("template_film", "managed_project", "caption_source", "red_native", "measurement_protocol"):
        path = Path(first_sources[key]["path"])
        need(path.is_relative_to(source_roots["inputs"]), "fixedtxt-session-reused-source-root")
        names.add(path.relative_to(source_roots["inputs"]).as_posix())
    for item in first_sources["project_files"]:
        path = Path(item["original"]["path"])
        need(path.is_relative_to(source_roots["inputs"]), "fixedtxt-session-reused-asset-root")
        names.add(path.relative_to(source_roots["inputs"]).as_posix())
    expected = {(row["root"], row["relative_path"]): row for row in ref["files"]}
    for name in sorted(names):
        row = expected.get(("inputs", name))
        need(row is not None, "fixedtxt-session-source-not-in-original-preparation")
        raw = read(source_roots["inputs"] / name, max(1, row["bytes"]))
        need(descriptor(raw) == {"bytes": row["bytes"], "sha256": row["sha256"]},
             "fixedtxt-session-reused-source-bytes-changed")
        path = inputs / name
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        write(path, raw)
    sources = copy.deepcopy(first_sources)
    for key in ("template_film", "managed_project", "caption_source", "red_native", "measurement_protocol"):
        sources[key]["path"] = str(inputs / Path(first_sources[key]["path"]).relative_to(source_roots["inputs"]))
    for original, item in zip(first_sources["project_files"], sources["project_files"]):
        item["original"]["path"] = str(inputs / Path(original["original"]["path"]).relative_to(source_roots["inputs"]))
    write(inputs / "source-descriptors.original.json", canonical(sources))
    for name, raw in (("shared-readonly.original.json", readonly_raw), ("shared-identity.original.json", identity_raw)):
        write(layout["control_root"] / name, raw)
    # Explicit provenance: the fifteen prior children are not this case's work.
    receipt = {"schema": "motionwright.native.fixedtxt-preparation-reuse/1", "context": current,
        "case_request": session["binding"]["request"], "first_prepared": descriptor(canonical(ref)),
        "first_namespace": session["plan"]["cases"][0]["namespace"],
        "shared_identity": descriptor(identity_raw), "shared_readonly": descriptor(readonly_raw),
        "retained_original_prefix": "control/reused-preparation/", "new_preparation_children": 0,
        "original_preparation_children": 15, "new_native_authority": False,
        "observed_monotonic": time.monotonic(), "quiescent": quiescent(current)}
    write(layout["control_root"] / "preparation-reuse.original.json", canonical(receipt))


def verify_reused_cycle(cycle_raw, session):
    if session is None or session["binding"]["index"] == 0:
        return
    import native_fixedtxt_g2_admission as fixed
    import native_fixedtxt_workflow as route
    old = strict(read(route.owned_layout(session["binding"]["context"])["control_root"]
                      / "reused-preparation/inputs" / fixed.CYCLE_MEMBER, MIB), MIB)
    new = strict(cycle_raw, MIB)
    def without_paths(value):
        if type(value) is dict:
            return {k: without_paths(v) for k, v in value.items() if k != "path"}
        if type(value) is list:
            return [without_paths(v) for v in value]
        return value
    need(without_paths(old) == without_paths(new), "fixedtxt-session-Q-physical-identity-changed")


def check_final_provenance_budget(known, session):
    if session is None:
        return
    import native_evidence_projection as core
    extras = [raw for (label, name), raw in known.items() if label == "control"
        and (name.startswith(("session-", "shared-", "reused-preparation/", "owner-resources/"))
             or name in {"first-prepared.original.json", "first-case-closed.original.json", "preparation-reuse.original.json"})
        and not core.excluded(core.member_path(name))]
    need(len(extras) <= SESSION_FILE_RESERVE and sum(map(len, extras)) <= SESSION_FINAL_RESERVE,
         "fixedtxt-session-provenance-original-budget")


def verify_resource_case_closure(current, request, request_raw, session, artifacts, layout):
    """Close original guards, then read back the published encrypted custody."""
    import native_fixedtxt_workflow as route
    import native_owner_resources as resources
    index = session["binding"]["index"]
    verified_guards = {}
    for action in ("begin-case", "prepare", "receive-and-run", "seal-workflow"):
        originals = resources.resource_originals("fixedtxt", action, index, completed=True)
        verified_guards[action] = {name: descriptor(raw) for name, raw in sorted(originals.items())}
    workflow = Path(os.environ["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-workflow-" + current["run_id"] + "-1")
    public_raw = read(workflow / "workflow-public.json", 65536)
    public = strict(public_raw, 65536)
    cipher = hash_file(workflow / "sealed-workflow-originals.bin", request["review_capsule_bytes"])
    cipher = {key: cipher[key] for key in ("bytes", "sha256")}
    manifest_raw = read(layout["control_root"] / "resource-workflow-manifest.original.json", 512 * 1024)
    manifest = strict(manifest_raw, 512 * 1024)
    need(public.get("schema") == "motionwright.native.fixedtxt-workflow-public/1"
         and public.get("context") == current and public.get("sealed_originals") == cipher
         and public.get("workflow_original_failures") == 0
         and public.get("resource_custody_manifest") == descriptor(manifest_raw)
         and public.get("native_acceptance_inferred") is False and public.get("numerical_oracle_approved") is False
         and public.get("automatic_retry") is False and public.get("within_original_deadline_at_seal") is True,
         "fixedtxt-resource-workflow-public-original")
    need(manifest.get("schema") == "motionwright.native.fixedtxt-resource-custody-manifest/1"
         and manifest.get("context") == current and manifest.get("case_index") == index
         and manifest.get("request") == descriptor(request_raw)
         and manifest.get("active_action") == "seal-workflow"
         and manifest.get("active_guard_completion_claimed") is False and manifest.get("failures") == [],
         "fixedtxt-resource-workflow-manifest-original")
    clock = strict(read(layout["control_root"] / "clock.original.json"))
    readback = route.readback_uploaded_workflow(artifacts["workflow"],
        layout["control_root"] / "resource-workflow-upload-readback", current=current, request=request,
        deadline=effective_deadline(clock, session), expected_ciphertext=cipher, expected_public=descriptor(public_raw))
    # The source action uploads exactly these two retained files. The bounded
    # ZIP readback above joins their bytes to the server's artifact digest.
    origin = read(layout["control_root"] / "owner-resources/origin.original.json")
    value = {"schema": "motionwright.native.fixedtxt-resource-custody-reference/1", "context": current,
        "index": index, "request": descriptor(request_raw), "origin": descriptor(origin),
        "workflow_artifact": route.artifact_identity(artifacts["workflow"]), "zip": readback["zip"],
        "ciphertext": cipher, "public_original": descriptor(public_raw),
        "resource_manifest_original": descriptor(manifest_raw), "resource_manifest": manifest,
        "guards_closed": verified_guards, "ciphertext_publication_readback": True,
        "native_acceptance": False, "automatic_retry": False}
    raw = canonical(value)
    need(len(raw) <= MIB and manifest["origin"] == descriptor(origin), "fixedtxt-resource-custody-reference-bound")
    write(session_root() / ("case-%02d.resource-custody.original.json" % index), raw)
    return descriptor(raw)


def resource_session_tail(current, root):
    """Use published case custody; retain only the remaining genuine tail."""
    import native_fixedtxt_workflow as route
    request_raw = read(root / "session-template.original.json", 49152)
    request = route.checked_request(request_raw, current=current)
    plan = strict(read(root / "session-plan.original.json"))
    count = 0
    for index in range(len(ROSTER)):
        if not (root / ("case-%02d.closed.original.json" % index)).exists():
            break
        count += 1
    need(not any((root / ("case-%02d.closed.original.json" % index)).exists()
                 for index in range(count, len(ROSTER))), "fixedtxt-resource-session-closed-cases-not-contiguous")
    session = {"plan": plan, "binding": {"index": count}}
    return route.select_owner_resource_custody(current, request, request_raw, session,
        action="seal-session", completed_count=count)


def close_case(index):
    """Advance only after actual owner/custody closure and actual artifact outputs."""
    import native_fixedtxt_workflow as route
    current = route.workflow_context()
    request_raw, request, clock_raw, clock = route.load_original_request(current)
    layout = route.owned_layout(current)
    session = clock_from_control(layout["control_root"], request_raw, clock_raw,
                                 current=current, runner_temp=os.environ["RUNNER_TEMP"])
    need(session is not None and session["binding"]["index"] == index,
         "fixedtxt-session-close-case-binding")
    import native_fixedtxt_g2_admission as fixed
    plan_raw = read(layout["plan"], 512 * 1024)
    runtime_raw = read(layout["runtime"])
    cycle_raw = read(layout["cycle"], MIB)
    dispatch_raw = read(layout["control_root"] / "dispatch.original.json", 16384)
    fixed.admit_inputs(dispatch_raw, plan_raw, runtime_raw, cycle_raw,
                       current=current, runner_temp=os.environ["RUNNER_TEMP"])
    plan = strict(plan_raw, 512 * 1024)
    verification_originals = {"control/request.original.json": request_raw, "control/clock.original.json": clock_raw}
    verification_originals.update({"control/" + name: read(layout["control_root"] / name, bound)
                                  for name, bound in SESSION_MEMBERS.items()})
    for suffix in ("intent.json", "spawned.json", "terminal.json", "stdout", "stderr"):
        name = "supervision/work." + suffix
        verification_originals["control/" + name] = read(layout["control_root"] / name, MIB)
    for row in plan["members"]:
        name = row["relative_path"]
        if row["root"] == "control" and (name.startswith("owner-resources/")
                or name == "reused-preparation/control/clock.original.json"):
            verification_originals["control/" + name] = read(layout["control_root"] / name, min(row["maximum"], 65536))
    work_verified = fixed.verify_supervisor_originals(verification_originals,
        plan=plan, current=current, runner_temp=os.environ["RUNNER_TEMP"])
    need(work_verified == {"original_work_stage_verified": True, "native_success_verified": False},
         "fixedtxt-session-work-original-verification")
    terminals = {}
    for phase in ("work", "custody"):
        raw = read(layout["control_root"] / ("supervision/" + phase + ".terminal.json"))
        terminal = strict(raw)
        need(terminal.get("phase") == phase and terminal.get("exit_code") == 0
             and terminal.get("process_started") is True and terminal.get("original_complete") is True
             and terminal.get("containment_empty") is True
             and terminal.get("deadline_exceeded") is False and terminal.get("overflow") is False
             and terminal.get("native_outcome") == "NOT_INFERRED"
             and "descendants_survived_wrapper" not in terminal,
             "fixedtxt-session-owner-or-custody-not-closed")
        terminals[phase] = descriptor(raw)
    custody_originals = {suffix: read(layout["control_root"] / ("supervision/custody." + suffix), MIB)
                         for suffix in ("intent.json", "spawned.json", "terminal.json", "stdout", "stderr")}
    verify_custody_stage(custody_originals, request, clock, session,
                         source_root=os.environ["GITHUB_WORKSPACE"])
    # The current complete selected capsule must preserve those work originals;
    # quiescence now cannot erase UNKNOWN or incomplete capture in its past.
    selected = Path(os.environ["RUNNER_TEMP"]) / ("mw-fixedtxt-g2-sealed-" + current["run_id"] + "-1")
    public_raw = read(selected / "public-evidence.json", 512 * 1024)
    public = strict(public_raw, 512 * 1024)
    sealed = hash_file(selected / "sealed-originals.bin", request["limits"]["capsule_bytes"])
    dispatch = fixed.validate_dispatch(dispatch_raw, current)
    verify_selected_public(public, public_raw, plan, plan_raw, dispatch_raw, dispatch,
                           sealed, current=current)
    owner_close = verify_current_owner_close(layout, plan, cycle_raw)
    empty = quiescent(current)
    readonly = strict(read(session_root() / "shared-readonly.original.json", MIB), MIB)
    expected = strict(read(session_root() / "shared-identity.original.json", MIB), MIB)
    need(shared_identity(current, request, readonly=readonly) == expected,
         "fixedtxt-session-shared-identity-changed-after-owner")
    directory = layout["control_root"] / "session-upload-readback"
    directory.mkdir(mode=0o700, exist_ok=False)
    api = route.ReadOnlyApi(directory, deadline=effective_deadline(clock, session))
    suffix = artifact_suffix(session["binding"])
    artifacts = {}
    try:
        target = strict(api.get("repos/" + route.REPO + "/actions/runs/" + current["run_id"]))
        workflow = strict(api.get(route.WORKFLOW_ENDPOINT))
        route.checked_run(target, run_id=current["run_id"], source_sha=current["source_sha"],
                          workflow_id=route.checked_workflow(workflow))
        need(target.get("status") == "in_progress", "fixedtxt-session-run-not-live-at-close")
        for role, prefix in (("review", "motionwright-fixedtxt-preparation-"),
            ("workflow", "motionwright-fixedtxt-workflow-"),
            ("selected", "motionwright-fixedtxt-originals-")):
            identifier = os.environ["MW_FIXEDTXT_" + role.upper() + "_ARTIFACT_ID"]
            checksum = os.environ["MW_FIXEDTXT_" + role.upper() + "_ARTIFACT_SHA256"]
            need(re.fullmatch(r"[1-9][0-9]*", identifier) and SHA.fullmatch(checksum),
                 "fixedtxt-session-actual-upload-outputs")
            raw = api.get("repos/" + route.REPO + "/actions/artifacts/" + identifier)
            value = strict(raw)
            route.checked_artifact(value, run=target, expected_name=prefix + current["run_id"] + "-1" + suffix,
                                   maximum=256 * MIB)
            need(value["id"] == int(identifier) and value["digest"] == "sha256:" + checksum,
                 "fixedtxt-session-upload-readback-differs")
            artifacts[role] = value
            write(session_root() / ("case-%02d.%s-artifact.original.json" % (index, role)), raw)
    finally:
        api.close()
    resource_reference = verify_resource_case_closure(current, request, request_raw, session, artifacts, layout)
    need(time.monotonic() < effective_deadline(clock, session), "fixedtxt-session-expired-at-case-close")
    record = {"schema": "motionwright.native.fixedtxt-session-case-closed/1", "context": current,
        "index": index, "request": descriptor(request_raw), "binding": descriptor(canonical(session["binding"])),
        "terminals": terminals, "quiescent": empty, "artifacts": artifacts,
        "selected_public": descriptor(public_raw), "selected_envelope": {k: sealed[k] for k in ("bytes", "sha256")},
        "owner_close_originals": owner_close, "resource_custody_reference": resource_reference,
        "observed_monotonic": time.monotonic(), "effective_deadline_monotonic": effective_deadline(clock, session),
        "mechanics_complete": True, "native_acceptance": False, "automatic_retry": False}
    write(session_root() / ("case-%02d.closed.original.json" % index), canonical(record))


def verify_selected_public(public, public_raw, plan, plan_raw, dispatch_raw, dispatch, sealed, *, current):
    need(set(public) == {"schema", "scope", "context", "arm", "fixture", "plan_sha256",
        "external_dispatch_sha256", "recipient_spki_sha256", "sealed_originals_sha256",
        "sealed_originals_bytes", "planned_originals_complete", "decoder_originals_verified",
        "supervisor_originals_verified", "retained_file_count", "missing_file_count",
        "native_success_verified", "native_operation_retried", "physical_acceptance",
        "human_acceptance", "release_authorized", "upload_authorization"}
        and canonical(public) == public_raw and public["schema"] == "motionwright.native.fixedtxt-custody-public/2"
        and public["scope"] == "fixedtxt-v4-one-fixture" and public["context"] == current
        and (public["arm"], public["fixture"]) == (plan["arm"], plan["fixture"])
        and public["plan_sha256"] == digest(plan_raw) and public["external_dispatch_sha256"] == digest(dispatch_raw)
        and public["recipient_spki_sha256"] == dispatch["recipient"]["sha256"]
        and type(public["sealed_originals_bytes"]) is int and public["sealed_originals_bytes"] == sealed["bytes"]
        and public["sealed_originals_sha256"] == sealed["sha256"]
        and public["planned_originals_complete"] is True and public["decoder_originals_verified"] is True
        and public["supervisor_originals_verified"] is True
        and type(public["retained_file_count"]) is int and public["retained_file_count"] == len(plan["members"])
        and type(public["missing_file_count"]) is int and public["missing_file_count"] == 0
        and public["upload_authorization"] == "NOT_GRANTED_BY_COLLECTOR"
        and sealed["bytes"] + len(public_raw) <= plan["limits"]["capsule_bytes"]
        and all(public[name] is False for name in ("native_success_verified", "native_operation_retried",
            "physical_acceptance", "human_acceptance", "release_authorized")),
        "fixedtxt-session-selected-originals-incomplete-or-unbound")


def verify_custody_stage(originals, request, clock, session, *, source_root):
    intent, spawned, terminal = (strict(originals[name], 65536)
                                  for name in ("intent.json", "spawned.json", "terminal.json"))
    fields = {"schema", "phase", "argv", "automatic_retry", "process_started", "exit_code",
        "deadline_exceeded", "overflow", "native_success_inferred", "original_complete", "containment_empty",
        "attempt_started_monotonic", "attempt_deadline_monotonic", "stage_deadline_monotonic",
        "execution_deadline_monotonic"}
    need(set(intent) == fields and intent["schema"] == "motionwright.native.fixedtxt-supervised-stage/1"
         and intent["phase"] == "custody" and intent["exit_code"] is None and intent["containment_empty"] is None
         and all(intent[name] is False for name in ("automatic_retry", "process_started", "deadline_exceeded",
             "overflow", "native_success_inferred", "original_complete")), "fixedtxt-session-custody-intent")
    argv = intent["argv"]
    need(type(argv) is list and len(argv) == 6 and all(type(s) is str for s in argv)
         and argv[0].startswith("/") and argv[1:4] == ["-B", "-E", "-s"]
         and argv[4:] == [source_root + "/tooling/native_fixedtxt_g2_admission.py", "--capture-selected"],
         "fixedtxt-session-custody-original-command")
    need(set(terminal) == fields | {"pid", "streams", "native_outcome", "terminal_observed_monotonic"}
         and all(terminal[key] == intent[key] for key in fields - {
             "process_started", "exit_code", "original_complete", "containment_empty"})
         and terminal["process_started"] is True and terminal["original_complete"] is True
         and terminal["containment_empty"] is True and type(terminal["exit_code"]) is int
         and terminal["exit_code"] == 0 and terminal["native_outcome"] == "NOT_INFERRED",
         "fixedtxt-session-custody-original-terminal")
    need(set(spawned) == {"pid", "automatic_retry"} and type(spawned["pid"]) is int
         and spawned["pid"] > 0 and spawned["automatic_retry"] is False
         and type(terminal["pid"]) is int and terminal["pid"] == spawned["pid"],
         "fixedtxt-session-custody-original-pid")
    start, total, stage, execution, finished = (terminal[name] for name in
        ("attempt_started_monotonic", "attempt_deadline_monotonic", "stage_deadline_monotonic",
         "execution_deadline_monotonic", "terminal_observed_monotonic"))
    need(all(type(v) in (int, float) and math.isfinite(v) for v in (start, total, stage, execution, finished))
         and start == clock["started_monotonic"] and total == effective_deadline(clock, session)
         and start <= execution < stage <= total - request["upload_seconds"]
         and start <= finished <= stage, "fixedtxt-session-custody-original-clock")
    need(type(terminal["streams"]) is dict and set(terminal["streams"]) == {"stdout", "stderr"},
         "fixedtxt-session-custody-stream-roster")
    for name, row in terminal["streams"].items():
        raw = originals[name]
        need(set(row) == {"observed_bytes", "retained_bytes", "eof", "observed_sha256"}
             and type(row["observed_bytes"]) is int and type(row["retained_bytes"]) is int
             and row["observed_bytes"] == row["retained_bytes"] == len(raw) <= MIB
             and row["eof"] is True and row["observed_sha256"] == digest(raw),
             "fixedtxt-session-custody-stream-binding")


def verify_current_owner_close(layout, plan, cycle_raw):
    """Read existing closure originals; never call fixed_owner_close's mutator."""
    import native_fixedtxt_g2_admission as fixed
    import native_evidence_projection as core
    prefix = plan["fixture"] + "/"
    names = [prefix + name for name in (
        "native-result.json", "native-result.host-stdin/native-return.snapshot.json",
        "native-result.fixed-text/host-binding.observation.json",
        "native-result.host-stdin/readback.observation.json", "native-result.host-stdin/capture-0001.json",
        "native-result.host-stdin/capture-0001.terminal.json", "native-result.host-stdin/session.original.json",
        "native-result.host-stdin/broker-receipt.snapshot.json", "native-result.host-stdin/capture-0001.stdin",
        "fixed-text-original-readback/artifact-manifest.json", "owner-closed.original.json",
        "owner-custody-close.observation.json", "owner-shutdown.intent.json", "owner-shutdown.terminal.json")]
    rows = {row["relative_path"]: row for row in plan["members"] if row["root"] == "observation"}
    handles = core.RootHandles({"observation": str(layout["observation_root"])})
    originals = {}
    try:
        for name in names:
            row = rows.get(name)
            need(row is not None and 0 < row["maximum"] <= 8 * MIB,
                 "fixedtxt-session-required-owner-original")
            raw, _ = handles.read("observation", core.member_path(name), row["maximum"])
            if row["expected_sha256"] is not None:
                need(digest(raw) == row["expected_sha256"], "fixedtxt-session-owner-original-plan-pin")
            originals["observation/" + name] = raw
        need(handles.stable_roots(), "fixedtxt-session-owner-original-roots-changed")
    finally:
        handles.close()
    fixed.resolve_native_directory(plan, originals, cycle_raw=cycle_raw)
    view = {name[len("observation/" + prefix):]: raw for name, raw in originals.items()}
    verify_owner_close_originals(view)
    return {name: descriptor(raw) for name, raw in sorted(originals.items())}


def verify_owner_close_originals(originals):
    raw = originals["owner-closed.original.json"]
    closed = strict(raw, 8192)
    selected = strict(originals["native-result.host-stdin/readback.observation.json"], 8 * MIB)["selected_capture_join"]
    intent = strict(originals["owner-shutdown.intent.json"], MIB)
    terminal = strict(originals["owner-shutdown.terminal.json"], MIB)
    observation = strict(originals["owner-custody-close.observation.json"], MIB)
    counters = closed.get("loss_counters_at_observer_close")
    # Same original receiver predicate as fixed_owner_close; no terminate/wait.
    need(set(closed) == {"schema", "status", "reason", "capture_records_written", "terminal_records_written",
                        "input_bytes_reserved", "queued_captures_discarded", "queue_disconnection_proven",
                        "loss_counters_at_observer_close", "expected_job_coverage", "child_consumption", "native_render_success"}
         and closed.get("schema") == "semwright.host-stdin-custody-close/1"
         and closed.get("status") == "observer_closed" and closed.get("reason") in {"owner_shutdown", "channel_closed"}
         and closed.get("queue_disconnection_proven") is True
         and all(type(closed.get(name)) is int for name in
                 ("capture_records_written", "terminal_records_written", "input_bytes_reserved", "queued_captures_discarded"))
         and closed["queued_captures_discarded"] == 0 and type(counters) is dict
         and set(counters) == {"capture_full", "capture_closed", "capture_oversized", "terminal_receiver_lost"}
         and all(type(value) is int and value == 0 for value in counters.values())
         and closed["capture_records_written"] == closed["terminal_records_written"] == 1
         and closed["input_bytes_reserved"] == selected["capture"]["stdin_bytes"]
         and selected.get("matched") is True and selected.get("ordinal") == 1
         and selected.get("pipe_write_all_completed") is True
         and closed.get("child_consumption") == "not_observed" and closed.get("native_render_success") == "not_inferred"
         and closed.get("expected_job_coverage") == "caller_must_join_original_job_and_parent_ids",
         "fixedtxt-session-original-receiver-not-closed-or-losses")
    need(set(intent) == {"pid", "original_process_identity", "method", "native_command_terminals", "automatic_retry",
                         "signal_is_receiver_close_evidence"}
         and type(intent["pid"]) is int and intent["pid"] > 0
         and intent["method"] == "existing_semwrightd_Unix_terminate_signal_handler"
         and type(intent["native_command_terminals"]) is dict and intent["native_command_terminals"]
         and set(intent["native_command_terminals"].values()) == {"SUCCESS_REPORTED"}
         and intent["automatic_retry"] is False and intent["signal_is_receiver_close_evidence"] is False,
         "fixedtxt-session-original-shutdown-intent")
    need(set(terminal) == {"pid", "observed_exit_code", "elapsed_seconds", "termination_requested", "wait_error_type",
                           "receiver_close_inferred_from_process_exit", "automatic_retry"}
         and terminal["pid"] == intent["pid"] and type(terminal["observed_exit_code"]) is int
         and terminal["observed_exit_code"] == 0 and terminal["wait_error_type"] is None
         and type(terminal["elapsed_seconds"]) in (int, float) and math.isfinite(terminal["elapsed_seconds"])
         and terminal["elapsed_seconds"] >= 0 and type(terminal["termination_requested"]) is bool
         and terminal["receiver_close_inferred_from_process_exit"] is False and terminal["automatic_retry"] is False,
         "fixedtxt-session-original-shutdown-terminal")
    need(observation == {"loss_free_close_observed": True, "original_close_record_observed": True,
        "original_close_sha256": digest(raw), "original_daemon_exit_code": 0,
        "termination_requested": terminal["termination_requested"], "child_consumption": "not_observed",
        "formal_acceptance": False, "global_job_coverage_accepted": False, "automatic_retry": False},
        "fixedtxt-session-original-close-observation-binding")


def release_shared():
    """Discard custody mappings before releasing the two original bind mounts."""
    import native_owner_resources as resources
    resources.handoff_shared_release("fixedtxt")


def seal_session():
    """Retain bounded session journals, including incomplete/rejected attempts."""
    import native_fixedtxt_workflow as route
    import native_fixedtxt_g2_admission as fixed
    import native_mlt_g2_custody as custody
    from ci_private_evidence import encrypt_snapshot
    current = route.workflow_context()
    root = session_root()
    bootstrap = custody.runtime_preparation_dispatch(read(root / "runtime-bootstrap.original.json", 16384),
                                                       current, scenario=route.SCOPE)
    session = strict(read(root / "session-clock.original.json"))
    need(time.monotonic() < session["deadline_monotonic"], "fixedtxt-session-custody-original-deadline")
    resource_tail, resource_manifest = resource_session_tail(current, root)
    resource_manifest_raw = canonical(resource_manifest)
    write(root / "resource-session-manifest.original.json", resource_manifest_raw)
    originals, total = {}, 0
    # Only this small session journal root, never any owner or shared checkout.
    for path in sorted(root.rglob("*")):
        need(not path.is_symlink(), "fixedtxt-session-journal-symlink")
        if not path.is_file():
            continue
        raw = read(path, 2 * MIB)
        total += len(raw)
        need(total <= 8 * MIB and len(originals) < 512, "fixedtxt-session-journal-total-bound")
        originals[path.relative_to(root).as_posix()] = raw
    for name, raw in sorted(resource_tail.items()):
        total += len(raw)
        need(len(raw) <= 2 * MIB and total <= 8 * MIB and len(originals) < 512,
             "fixedtxt-session-resource-tail-inside-original-limits")
        originals["owner_resources/" + name] = raw
    metadata = canonical({"schema": "motionwright.native.fixedtxt-session-custody/1", "context": current,
        "files": [{"path": name, **descriptor(raw)} for name, raw in sorted(originals.items())],
        "resource_custody_manifest": descriptor(resource_manifest_raw),
        "case_roster": list(ROSTER), "native_acceptance": False, "G2_accepted": False, "F_accepted": False,
        "automatic_retry": False})
    need(len(metadata) <= 512 * 1024, "fixedtxt-session-journal-manifest-bound")
    envelope = encrypt_snapshot(fixed._pack_originals(originals, metadata), bootstrap["recipient"], current)
    need(len(envelope) <= 16 * MIB, "fixedtxt-session-journal-envelope-bound")
    directory = root.parent / ("mw-fixedtxt-session-custody-" + current["run_id"] + "-1")
    directory.mkdir(mode=0o700, exist_ok=False)
    write(directory / "sealed-session-originals.bin", envelope)
    write(directory / "session-public.json", canonical({"schema": "motionwright.native.fixedtxt-session-public/1",
        "context": current, "sealed_originals": descriptor(envelope),
        "recipient_sha256": bootstrap["recipient"]["sha256"], "original_files": len(originals),
        "native_acceptance": False, "G2_accepted": False, "F_accepted": False, "automatic_retry": False}))
    need(time.monotonic() < session["deadline_monotonic"], "fixedtxt-session-custody-finished-after-deadline")
    output(directory=str(directory))


def main():
    parser = argparse.ArgumentParser()
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--open-session", action="store_true")
    command.add_argument("--begin-case", type=int)
    command.add_argument("--close-case", type=int)
    command.add_argument("--release-shared", action="store_true")
    command.add_argument("--seal-session", action="store_true")
    args = parser.parse_args()
    if args.open_session:
        open_session()
    elif args.begin_case is not None:
        begin_case(args.begin_case)
    elif args.close_case is not None:
        close_case(args.close_case)
    elif args.release_shared:
        release_shared()
    else:
        seal_session()


if __name__ == "__main__":
    main()
