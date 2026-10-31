#!/usr/bin/env python3
"""Run closed Studio selections with original source and process custody."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import stat
import tarfile

CANVAS_TITLES = (
    "structural removal denial is accessible and preserves authored hierarchy through repeated revisions and reopen",
    "an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a process restart",
    "an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved",
    "a late real transform acknowledgement preserves newer invalid raw input without replay after reload",
    "another tab restores foreign input explicitly and changed recovery bytes cannot be overwritten or discarded from an older view",
    "lost reply after real Canvas commit cannot replay the already saved operation",
    "Canvas pointer readback is required before real-service commit and explicit reload restores draft",
    "authored Canvas style persists across two real revisions and reopen without losing uncommitted typography",
    "Canvas hierarchy retains parent and raw Z through two real revisions and a service restart",
    "Canvas relation add and remove preserve stable IDs and source intent through reopen",
    "Canvas removal isolates the real children denial and permits explicit repair after reopen",
    "Canvas removal isolates the real incoming relations denial and permits explicit repair after reopen",
    "Canvas removal stays pending through selection and remount before the real commit response",
    "Canvas removal stays pending through selection and remount after the real commit response",
    "Canvas removal committed with a lost reply stays unknown until real SQLite reopen and never replays",
    "Canvas removal sends nothing when its record cannot be retained and verified",
    "Canvas removal sends nothing when its pointer cannot be retained and verified",
    "saved parented Canvas text stays visible in real editorial study after service reopen",
    "saved localized profile text overrides the editorial SVG across two revisions and reopening",
)
INK_TITLE = "a saved ink token changes the actual SVG text paint across revisions and reopen"
PROGRAM_TITLES = (
    "Timeline Program renders actual saved Canvas text, style and selected locale through revisions and restart",
    "a real saved partial cut excludes an editable Program scene until an explicit full-sequence revision",
    "real SQLite profile cut with stale localized text shows only the excluded original source after reopen",
)
BRIEF_TITLES = (
    "saving the title or brief commits only that group and conserves the other raw inputs",
    "a late real reply preserves newer typing and reload never replays an already committed brief",
    "returning an in-flight field to its original value survives navigation and reload until explicitly saved",
    "a real brief validation failure preserves every input through reload and correction",
    "two real clients resolve only the overlapping brief field by keeping the saved value",
    "two real clients resolve only the overlapping brief field by keeping the draft value",
    "reopening revokes a real grant without changing the revision or clearing the refused brief",
)
BRIEF_RECOVERY_TITLES = (
    "lost brief session writer pointer stops service grant; explicit Restore saves once and survives reopen",
)
BRIEF_UNCERTAINTY_TITLES = (
    "an unrelated r+1 is reviewed without acknowledging or replaying an uncertain Brief",
    "a committed Brief followed by r+2 preserves later input until separate review",
    "Brief record retention failure is NOT_SENT and retry backup permits one explicit save",
    "Brief pointer retention failure is NOT_SENT and retry backup permits one explicit save",
    "a late Brief reply cannot consume the pending state of a new recovery writer",
    "a failed recovery fork pointer keeps the earlier Brief UNKNOWN and sends nothing",
)
ACTIVE_VOICE_TITLES = (
    "active voice selection preserves both measured sources and canonical state through keyboard save and reopen",
    "a held original selection reply blocks duplicate sends across workspace remount",
    "an unrelated r+1 leaves active voice UNKNOWN until separate saved-state review",
    "a committed active voice followed by r+2 remains UNKNOWN after lost reply and reload",
    "active voice record retention failure is NOT_SENT and reload never submits it",
    "active voice pointer retention failure is NOT_SENT and reload never submits it",
)
CAMERA_TITLES = (
    "Camera reframe keeps raw input, commits twice and reopens exact SQLite Project",
    "Camera unconfirmed response cannot replay a real SQLite mutation",
    "a peer upsert with identical saved Camera values cannot acknowledge the held original",
    "a peer detach with identical saved Camera values cannot acknowledge the held original",
    "a same-revision Camera inspection cannot release an original that is still held",
    "a late saved-Camera read for scene A preserves the exact visible author copy of scene B",
    "a successful original Camera reply closes only scene A while scene B keeps its exact author bytes",
)


# The product inputs are nominated independently of this verification proposal.
# A source review and G0 do not fill this runtime profile automatically.
CAMERA_EIGHT_SCOPE = "camera-with-v2"
CAMERA_EIGHT_REVIEWED_PROFILE = None
CAMERA_EIGHT_HISTORICAL_SOURCE = "4cc2cd42e293f11986d4dfda9c9658e884618316"
CAMERA_EIGHT_HISTORICAL_TREE = "0ce41c8d7904e2f4afe434058dc0655f85996885"
CAMERA_EIGHT_GATE_PATHS = ("tooling/ci_canvas_service.py", "tooling/tests/test_ci_canvas_service.py",
                           ".github/workflows/studio-service-verification.yml")
CAMERA_EIGHT_HISTORICAL_INPUT_PINS = (
    ("apps/studio/tests/camera-reframe-service.spec.ts", "5c9a6cd336786baa7fd22e871205c7515265b877dcdda9324378b46426a6536e"),
    ("apps/studio/tests/camera-reframe-v2-service.spec.ts", "08ae282be69784e085a7be73ae233146843e0ee156f7e27c332ace2ea50089da"),
    ("apps/studio/tests/support/scopedChangeService.ts", "2fba83ace30c032e11f3c108203c0d7a0e98821d79d081717281194108138f1d"),
    ("SOURCE_LOCK.json", "aeb60e60036d29ad96abec0dc7acbcf3302a4411c16a7ea37c4b3c58c36e0b4f"),
)
CAMERA_EIGHT_SELECTIONS = (
    ("camera", "camera-reframe-service.spec.ts", CAMERA_TITLES),
    ("camera-v2", "camera-reframe-v2-service.spec.ts", (
        "saved Manim v2 Camera preserves its authored pair through raw recovery, two UI edits and SQLite reopen",)),
)
CAMERA_EIGHT_CATALOG_SHA256 = "9deb9db56f3854b0e3c7aefe139a64c379616e9d83340bf2cd007048ad408c4a"
CAMERA_EIGHT_TIMEOUTS = {"camera": 1200, "camera-v2": 300}
CAMERA_EIGHT_DATABASES = (
    ("camera", CAMERA_TITLES[0], "camera.sqlite3", 1),
    ("camera", CAMERA_TITLES[1], "camera-lost-reply.sqlite3", 1),
    ("camera", CAMERA_TITLES[2], "camera-held-upsert.sqlite3", 2),
    ("camera", CAMERA_TITLES[3], "camera-held-detach.sqlite3", 2),
    ("camera", CAMERA_TITLES[4], "camera-held-same-revision.sqlite3", 1),
    ("camera", CAMERA_TITLES[5], "camera-late-read-scene-scope.sqlite3", 1),
    ("camera", CAMERA_TITLES[6], "camera-late-ack-scene-scope.sqlite3", 1),
    ("camera-v2", CAMERA_EIGHT_SELECTIONS[1][2][0], "camera-v2.sqlite3", 1),
)


def camera_eight_selections():
    rows = CAMERA_EIGHT_SELECTIONS
    require(isinstance(rows, tuple) and len(rows) == 2
            and tuple((label, filename, len(titles)) for label, filename, titles in rows) == (
                ("camera", "camera-reframe-service.spec.ts", 7),
                ("camera-v2", "camera-reframe-v2-service.spec.ts", 1))
            and rows[0][2] == CAMERA_TITLES
            and all(isinstance(titles, tuple) and all(isinstance(title, str) and title for title in titles)
                    for _, _, titles in rows)
            and len({(filename, title) for _, filename, titles in rows for title in titles}) == 8
            and hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                == CAMERA_EIGHT_CATALOG_SHA256, "Camera eight catalogue differs")
    return rows


def camera_eight_profile():
    """Require a later explicit source nomination; historical inputs cannot activate it."""
    profile = CAMERA_EIGHT_REVIEWED_PROFILE
    require(isinstance(profile, dict) and set(profile) == {"schema", "source_commit", "source_tree",
                "product_paths", "runner", "minimum_available_memory_bytes", "minimum_target_free_bytes",
                "cargo_jobs", "job_seconds"}
            and profile.get("schema") == "motionwright.camera-eight-runtime-profile/1",
            "Camera runtime source/resource profile is not designated")
    require(all(isinstance(profile.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", profile[key])
                for key in ("source_commit", "source_tree"))
            and profile["source_commit"] != CAMERA_EIGHT_HISTORICAL_SOURCE,
            "Camera needs a later nominated source; historical 4cc is not a runtime target")
    require(isinstance(profile.get("product_paths"), dict)
            and set(profile["product_paths"]) == {name for name, _ in CAMERA_EIGHT_HISTORICAL_INPUT_PINS}
            and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in profile["product_paths"].values())
            and profile["runner"] == "ubuntu-24.04"
            and profile["minimum_available_memory_bytes"] == 4 * 1024**3
            and profile["minimum_target_free_bytes"] == 18 * 1024**3
            and profile["cargo_jobs"] == 1 and profile["job_seconds"] == 5400
            and all(type(profile[key]) is int for key in ("minimum_available_memory_bytes",
                    "minimum_target_free_bytes", "cargo_jobs", "job_seconds")),
            "Camera nominated input or resource profile differs")
    camera_eight_selections()
    return dict(profile)


def camera_eight_source_binding(repo, observation):
    profile = camera_eight_profile()
    require(isinstance(observation, dict) and isinstance(observation.get("files"), dict),
            "Camera source observation missing")
    for name, expected in profile["product_paths"].items():
        require(observation["files"].get(name, {}).get("sha256") == expected,
                "Camera product source bytes differ: " + name)
    tree = subprocess.check_output(["git", "rev-parse", profile["source_commit"] + "^{tree}"],
                                   cwd=repo, timeout=30).decode("ascii").strip()
    require(tree == profile["source_tree"], "Camera nominated source tree differs")
    changed = subprocess.check_output(["git", "diff", "--name-only", "-z", profile["source_commit"],
                                       observation["checkout"], "--"], cwd=repo, timeout=30)
    require(changed == b"" or changed.endswith(b"\0"), "Camera source delta is not closed")
    names = changed.decode("utf-8").split("\0")[:-1] if changed else []
    require(len(names) == len(set(names)) and set(names) <= set(CAMERA_EIGHT_GATE_PATHS),
            "Camera gate changed nominated product outside its verification paths")
    for name, expected in profile["product_paths"].items():
        original = subprocess.check_output(["git", "show", profile["source_commit"] + ":" + name],
                                           cwd=repo, timeout=30)
        require(hashlib.sha256(original).hexdigest() == expected, "Camera nominated input blob differs")
    return {"nominated_input": {"commit": profile["source_commit"], "tree": tree},
            "executed_source": {"commit": observation["checkout"], "tree": observation["tree"]},
            "verification_path_delta": names, "result_transfer": False, "source_profile": profile}


def camera_eight_resources(repo, target, *, phase):
    profile = camera_eight_profile()
    require(phase in ("preflight", "build", "list-camera", "list-camera-v2", "execute-camera", "execute-camera-v2"),
            "Camera resource phase differs")
    require(sys.platform.startswith("linux") and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_JOB") == "real-service"
            and os.environ.get("RUNNER_OS") == "Linux" and os.environ.get("ImageOS") == "ubuntu24"
            and os.environ.get("ImageVersion") and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", os.environ.get("GITHUB_RUN_ID", "")) is not None
            and re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")) is not None,
            "Camera requires its genuine first hosted Linux job")
    require(repo.name == "g" and target == repo.parent / "v/t"
            and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent,
            "Camera requires its isolated existing g/v layout")
    memory = Path("/proc/meminfo").read_text(encoding="ascii")
    rows = re.findall(r"^MemAvailable:\s+([0-9]+) kB$", memory, re.MULTILINE)
    require(len(rows) == 1, "Camera memory observation is absent")
    available = int(rows[0]) * 1024
    free = shutil.disk_usage(target.parent).free
    require(available >= profile["minimum_available_memory_bytes"]
            and type(free) is int and free >= profile["minimum_target_free_bytes"],
            "Camera source build or case resource floor is unavailable")
    return {"phase": phase, "available_memory_bytes": available, "target_free_bytes": free,
            "minimum_available_memory_bytes": profile["minimum_available_memory_bytes"],
            "minimum_target_free_bytes": profile["minimum_target_free_bytes"],
            "cargo_jobs": 1, "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": "1",
            "job": "real-service", "executed_source": os.environ["GITHUB_SHA"],
            "image_os": os.environ["ImageOS"], "image_version": os.environ["ImageVersion"],
            "boundary": "PRE_COMMAND_OBSERVATION_NOT_CONTINUOUS_RESOURCE_WATCHDOG"}


def camera_eight_preflight(arguments):
    parser = argparse.ArgumentParser(description="Bind the closed Camera source before dependencies.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    camera_eight_profile()  # A missing runtime nomination refuses before filesystem/Git queries.
    repo, target, output = (path.resolve() for path in (args.repo, args.target, args.output))
    require(output == repo.parent / "v/tools/camera-preflight.json" and not output.exists(),
            "Camera preflight requires its fresh original path")
    source = source_observation(repo, CAMERA_EIGHT_SCOPE)
    resources = camera_eight_resources(repo, target, phase="preflight")
    value = {"schema": "motionwright.camera-eight-preflight/1", "source": source,
             "resources": resources, "execution": "NOT_RUN", "formal_acceptance": False}
    write_new(output, value)
    require(json.loads(output.read_bytes()) == value, "Camera preflight original readback differs")
    print(json.dumps({"status": "SOURCE_INPUTS_BOUND_NO_RUNTIME", "source": source["checkout"],
                      "receipt": str(output)}))
    return 0


def camera_eight_report(document, filename, titles, *, listing, expected=None):
    require(type(listing) is bool and (filename, titles) in {(name, names) for _, name, names in camera_eight_selections()},
            "Camera file selection differs")
    require(isinstance(document, dict) and document.get("errors") == [], "Camera original report errors")
    stats = document.get("stats", {})
    require(all(type(stats.get(key)) is int for key in ("expected", "skipped", "unexpected", "flaky")),
            "Camera report counters are not integers")
    if listing:
        require(stats["expected"] == 0 and stats["skipped"] == len(titles)
                and stats["unexpected"] == stats["flaky"] == 0,
                "Camera discovery contains executed or unexpected results")
    else:
        validate_browser_execution(document, filename, titles)
    actual = browser_result_identities(document)
    require(len(actual) == len(titles) and {pair for pair, _ in actual} == {(filename, title) for title in titles},
            "Camera exact title inventory differs")
    specs = []
    def visit(suites):
        for suite in suites:
            specs.extend(suite.get("specs", []))
            visit(suite.get("suites", []))
    visit(document["suites"])
    require(len(specs) == len(titles), "Camera original spec inventory differs")
    identities = []
    for spec in specs:
        case = spec["tests"][0]
        require(case.get("expectedStatus") == "passed" and case.get("annotations", []) == []
                and type(case.get("timeout")) is int and case["timeout"] == 120000,
                "Camera skip or original case timeout differs")
        require(isinstance(spec.get("id"), str) and 0 < len(spec["id"]) <= 512
                and all(ord(char) >= 33 for char in spec["id"])
                and type(spec.get("line")) is int and spec["line"] > 0
                and type(spec.get("column")) is int and spec["column"] > 0,
                "Camera original Playwright identity is missing")
        require(not listing or case.get("results") == [], "Camera list contains a test result")
        if not listing:
            require(type(case["results"][0].get("retry")) is int and case["results"][0]["retry"] == 0,
                    "Camera original retry counter is not zero")
        identities.append({key: spec[key] for key in ("file", "title", "id", "line", "column")})
    require(len({row["id"] for row in identities}) == len(titles), "Camera original case ID is repeated")
    identities.sort(key=lambda row: titles.index(row["title"]))
    if not listing:
        require(isinstance(expected, dict) and expected.get("status") == "LISTED_NOT_EXECUTED"
                and expected.get("identities") == identities, "Camera execution differs from its original list")
    return {"status": "LISTED_NOT_EXECUTED" if listing else "PASSED_ORIGINAL_SELECTED_CASES",
            "listed": len(titles), "executed": 0 if listing else len(titles),
            "identities": identities, "skips": 0, "retries": 0}


def camera_eight_intent(label, titles, argv, cwd, source, binary):
    return {"schema": "motionwright.camera-eight-selection-intent/1", "label": label,
            "expected_cases": len(titles), "argv": argv, "cwd": str(cwd),
            "source_commit": source["checkout"], "source_tree": source["tree"], "service_binary": binary,
            "execution": "NOT_STARTED", "retry": "FORBIDDEN"}


def camera_eight_record_attempt(selection, command):
    pid = command.get("pid")
    selection.update(command=command, attempted=type(pid) is int and pid > 0,
                     original_pid=pid if type(pid) is int and pid > 0 else None,
                     original_exit=command.get("exit_code"),
                     status="UNKNOWN" if execution_unknown(command) else "FAIL", validated_cases=0)


def camera_eight_archive_lists(root, inventory, receipt, source, command_closed):
    rows = camera_eight_selections()
    observed = receipt.get("camera_discovery", [])
    require(isinstance(observed, list) and [row.get("label") for row in observed] == [row[0] for row in rows],
            "Camera original two-list inventory differs")
    preflight = archive_json(root, inventory, "tools/camera-preflight.json")
    require(preflight.get("schema") == "motionwright.camera-eight-preflight/1"
            and preflight.get("source") == source and preflight.get("execution") == "NOT_RUN"
            and preflight.get("formal_acceptance") is False
            and preflight.get("resources", {}).get("phase") == "preflight"
            and receipt.get("camera_preflight") == {key: inventory["entries"]["tools/camera-preflight.json"][key]
                                                     for key in ("bytes", "sha256")},
            "Camera preflight original differs")
    resource_rows = receipt.get("camera_resource_observations", [])
    expected_phases = ["build", "list-camera", "list-camera-v2", "execute-camera", "execute-camera-v2"]
    require(isinstance(resource_rows, list) and [row.get("phase") for row in resource_rows] == expected_phases,
            "Camera resource observation roster differs")
    for row in [preflight.get("resources", {}), *resource_rows]:
        require(row.get("run_id") == receipt.get("run_id") and row.get("run_attempt") == receipt.get("run_attempt") == "1"
                and row.get("job") == receipt.get("job") == "real-service"
                and row.get("executed_source") == source["checkout"]
                and row.get("image_os") == "ubuntu24" and isinstance(row.get("image_version"), str)
                and row["image_version"] and type(row.get("cargo_jobs")) is int and row["cargo_jobs"] == 1
                and row.get("minimum_available_memory_bytes") == 4 * 1024**3
                and row.get("minimum_target_free_bytes") == 18 * 1024**3
                and type(row.get("available_memory_bytes")) is int and row["available_memory_bytes"] >= 4 * 1024**3
                and type(row.get("target_free_bytes")) is int and row["target_free_bytes"] >= 18 * 1024**3,
                "Camera original resource/context observation differs")
    repo, target = root.parent / "g", root / "t"
    node = receipt["node"]["path"]
    cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
    result = {}
    for observed_row, selection in zip(observed, rows):
        label, filename, titles = selection
        command = observed_row.get("command", {})
        command_closed(command, label + "-discovery")
        env = service_environment_binding(target, root / "e", label, target / "debug/examples/studio_review_session")
        env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(root / "e" / (label + "-discovery.json"))
        require(command.get("argv") == service_command(node, cli, root / "e", selection,
                                                       listing=True, scope=CAMERA_EIGHT_SCOPE)
                and command.get("cwd") == str(repo / "apps/studio")
                and command.get("selected_environment") == env,
                "Camera original list invocation differs")
        path = "e/" + label + "-discovery.json"
        actual = camera_eight_report(archive_json(root, inventory, path), filename, titles, listing=True)
        require(observed_row.get("status") == "LISTED_NOT_EXECUTED" and observed_row.get("verified") == actual
                and observed_row.get("original_report") == {key: inventory["entries"][path][key] for key in ("bytes", "sha256")},
                "Camera original list report differs")
        result[label] = actual
    all_ids = [identity["id"] for item in result.values() for identity in item["identities"]]
    require(len(all_ids) == len(set(all_ids)) == 8, "Camera two-list original IDs are not eight distinct identities")
    def original_time(value):
        require(isinstance(value, str) and value, "Camera original command time is absent")
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(instant.tzinfo is not None and instant.utcoffset() is not None,
                "Camera original command time has no timezone")
        return instant
    lists_end = [original_time(row["command"].get("finished_utc")) for row in observed]
    executions = receipt.get("selections", [])
    require(len(executions) == 2 and all(max(lists_end) <= original_time(row.get("command", {}).get("started_utc"))
                                       for row in executions),
            "Camera execution started before both original lists closed")
    return result


def camera_eight_capture_bindings(root, inventory, captures):
    """Bind eight distinct source-declared databases; do not claim archive reopen."""
    require(len(captures) == 8 and len({row.get("manifest") for row in captures}) == 8,
            "Camera requires all eight original closed stores")
    layout = {database: (label, title, participants) for label, title, database, participants in CAMERA_EIGHT_DATABASES}
    require(len(layout) == 8, "Camera source database inventory differs")
    used, roots, bound = set(), set(), []
    for capture in captures:
        name = capture.get("manifest")
        require(isinstance(name, str) and name.endswith("/closed-camera-store/manifest.json"),
                "Camera original store path differs")
        parts = name.split("/")
        require(len(parts) == 5 and parts[0] == "e", "Camera closed store path is not a direct case output")
        manifest = archive_json(root, inventory, name)
        database = manifest.get("databaseRelativePath")
        require(database in layout and database not in used, "Camera database is foreign or repeated")
        label, title, participants = layout[database]
        require(parts[1] == label + "-test-results", "Camera database belongs to another selection")
        case_root = "/".join(parts[:3])
        require(case_root not in roots, "Camera original cases share a store root")
        roots.add(case_root); used.add(database)
        _manifest, oracle, _outcome = partial_closed_store(root, inventory, case_root + "/closed-camera-store",
                                                         root / case_root / "store", database, participants, 1)
        bound.append({"label": label, "title": title, "manifest": name, "database": database,
                      "project_id": oracle["project"]["id"], "participants": participants,
                      "basis": "SOURCE_PINNED_DATABASE_AND_COMPLETE_HELPER_ORIGINALS",
                      "independent_archive_reopen": False, "additional_oracle_reexecution": False})
    require(used == set(layout), "Camera original database inventory is incomplete")
    return bound


GESTURE_TITLES = (
    "rapid pointer move and release commits one exact Canvas transform without losing the final delta",
    "pointercancel discards the transient delta and later pointerup cannot apply it",
    "quick release with Auto-key writes a single X/Y motion pair, not a base transform",
    "a pose gesture cannot overwrite a peer after same-view reopen and release",
    "a pose gesture cannot overwrite a peer after same-view reopen and cancel",
    "a keyed gesture cannot overwrite a peer after same-view reopen and release",
    "a keyed gesture cannot overwrite a peer after same-view reopen and cancel",
)
NARRATIVE_TITLES = (
    'Narrative drafts survive actual workspace navigation/reload; separate premise and objective commits survive SQLite restart',
    'a held original premise receipt preserves newer typing across actual workspace remount',
    'a held original objective receipt preserves newer typing across actual workspace remount',
    'an uncertain Narrative unrelated outcome survives reload and is reviewed before a separate intent',
    'an uncertain Narrative committed outcome survives reload and is reviewed before a separate intent',
    'a lost Narrative writer pointer blocks real grants and writes; explicit Restore commits exactly once',
    'a real objective validation refusal preserves both fields until fresh review and correction',
    "an independent service author's overlapping premise is reviewed without consuming a disjoint objective draft",
)
BEAT_DRAFT_TITLES = (
    'beat raw drafts survive actual workspace navigation and reload before one exact SQLite commit and restart',
    'a held original beat receipt preserves later raw edits through actual workspace remount and a separate save',
    'an uncertain beat committed outcome requires real project/history review before any separate intent',
    'an uncertain beat preempted outcome requires real project/history review before any separate intent',
    'a peer retime preserves the local beat draft until explicit text with saved timing is committed',
    'a peer removal retains beat text across reload and only an explicit recreation creates a new event',
    'a shorter saved scene retains invalid beat times until the author explicitly compares and repairs them',
    'a silent beat writer pointer failure blocks real grants and saves until an explicit copy restore',
    'a failed beat ACK checkpoint preserves original UNKNOWN bytes and later text until real fresh review',
    'a real original beat removal keeps later typing through restart without an automatic recreation event',
    'a beat exported during local recovery failure imports without a write and saves exactly once to SQLite after repair',
)

BEAT_TIME_TITLES = (
    'copy-only scene beat edits retain exact fractional frame times in SQLite and after process restart',
    'authored decimal and sample times save exactly while incomplete and out-of-scene inputs cannot commit',
    'Fill timing gap commits the exact shared frame boundary and preserves both beats after restart',
    'a saved sub-microsecond gap remains editable and cannot display exact coverage until filled',
)

DELIVERY_RECIPE_TITLES = (
    'two profiles retain both canonical groups through explicit saves, clear and fresh-process reopen',
    'a committed reply held across reload preserves Sent then Old until fresh history review and explicit conflict resolution',
    'typing across Film profile and mix scopes during a real save retains every unsent input',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep saved',
    'two real clients reject a stale save, review current history and resolve only the overlapping field with keep draft',
    'invalid numeric raw survives a real reload and only corrected values reach the service',
    'a full recovery budget preserves memory and blocks service writes until a verified copy fits',
    'an unreadable own copy is retained until explicit discard without losing newer typed input',
    "a new window with copied session storage offers another window's draft only by explicit recovery",
    'project content locks and a real revoked grant retain inputs for a fresh reviewed intent without replay',
    'Film and PCM guards use saved options and expose unsupported combinations without pretending a native result',
    "portable export and verified import preserve both recipes and CAS while isolating the new generation's drafts",
)

RECIPE_AUTHORITY_TITLES = (
    'real film peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'real mix peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save',
    'a real original recipe ACK preserves later raw numeric typing even when it returns to the submitted value',
    'silent recipe data recovery failure prevents a real grant or SQLite write; verified retry saves once',
    'silent recipe pointer recovery failure prevents a real grant or SQLite write; verified retry saves once',
)

HISTORICAL_REVIEW_TITLES = (
    'review raw range survives actual App navigation and reload before one exact SQLite commit and restart',
    'a held original review reply preserves later input before a distinct granted save',
    'a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent',
    'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
    'an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement',
    'an actual saved alternative retains raw rationale and records only an open manual review',
)

REVIEW_ABA_TITLE = 'ABA author input survives lost review reply, inspection, separate save and SQLite restart'
REVIEW_TITLES = (
    *HISTORICAL_REVIEW_TITLES[:3], REVIEW_ABA_TITLE,
    *HISTORICAL_REVIEW_TITLES[3:],
)

INSPECTOR_TITLES = (
    'Inspector raw objective and exact fraction survive actual App navigation before SQLite commits and restart',
    'held original Inspector reply retains later input before a separately granted save',
    'committed lost Inspector reply stays UNKNOWN through inspection and a distinct new intent',
    'denied Inspector custody prevents SQLite mutation until verified recovery and explicit save',
    'incomplete Inspector duration survives actual service restart without mutation',
    'Inspector stale input requires explicit retention against actual saved source',
)

PROFILE_METADATA_TITLES = (
    'raw fields survive A-B-A, an unrelated real revision, in-app reopen and browser reload without a profile write',
    'a delayed real save preserves later A input and selected B, then only an explicit valid save reaches SQLite',
    'an actual stale CAS refusal preserves sent and later raw input without retry before explicit conflict review',
    'an unreadable local recovery copy stays intact while later input is kept and only explicit discard permits saving',
    'a newly created output profile does not replace the active draft when its real response arrives late',
    'a real profile Save stops before its grant when recovery becomes blocked: changed-before-save',
    'a real profile Save stops before its grant when recovery becomes blocked: readback-differs',
    'an unknown own copy blocks actual Save until explicit reading proves absence and preserves the later raw',
    'a known quota failure blocks real Save until verified recovery without changing the authored raw',
    'custom cut stays empty until an explicit full-sequence choice across reload and real revisions',
    'a custom cut typed empty after a real save stays empty through ACK, reload and a peer full-sequence conflict',
    'a locally empty cut does not adopt a peer custom list and reviews repeated saved cuts without losing other raw input',
    'a draft whose profile was removed remains readable and downloadable, then editable after repeated revisions restore that exact profile',
    'a removed profile keeps corrupt recovery bytes and later window input as separate downloadable snapshots without a profile write',
)

PROFILE_AUTHORITY_TITLES = (
    'original profile reply confirms exact raw-derived fields in SQLite and after process restart',
    'held original profile reply retains later identical spelling and incomplete input before a separately granted save',
    'lost committed profile reply retains UNKNOWN through restart and explicit later intent',
    'lost committed profile reply retains UNKNOWN through restart and explicit clean inspection',
    'silent profile writer-pointer failure prevents grants and SQLite changes until verified recovery',
    'profile recovery budget blocks actual transport without evicting any retained original',
)

PROJECTION_NAVIGATION_TITLES = (
    'remembered 65-scene App opening and process restart use bounded native pages without complete reads',
    'explicit small-project browsing pages 129 native nodes and preserves off-page references',
    'a real peer revision returns typed stale-page failure and preserves the view until explicit reload',
    'a lost genuine node-page delivery is reported safely without fallback, replay or lost prior page',
    'bounded observations retain an original committed Review UNKNOWN and later authored text without replay',
    'a genuinely missing selected head preserves preference and allows an explicit healthy page choice',
)

SELECTIONS = (
    ("canvas", "canvas-draft-service.spec.ts", CANVAS_TITLES),
    ("ink", "visual-language-draft-service.spec.ts", (INK_TITLE,)),
    ("program", "program-design-service.spec.ts", PROGRAM_TITLES),
    ("brief", "brief-service.spec.ts", BRIEF_TITLES),
    ("brief-recovery", "brief-recovery-admission-service.spec.ts", BRIEF_RECOVERY_TITLES),
    ("brief-uncertain", "brief-uncertain-outcome-service.spec.ts", BRIEF_UNCERTAINTY_TITLES),
    ("active-voice", "audio-active-voice-service.spec.ts", ACTIVE_VOICE_TITLES),
    ("camera", "camera-reframe-service.spec.ts", CAMERA_TITLES),
    ("gesture", "canvas-gesture-service.spec.ts", GESTURE_TITLES),
    ("narrative", "narrative-service.spec.ts", NARRATIVE_TITLES),
    ('beat-draft', 'scene-beat-draft-service.spec.ts', BEAT_DRAFT_TITLES),
    ('beat-time', 'scene-beat-time-service.spec.ts', BEAT_TIME_TITLES),
    ('delivery-recipe', 'delivery-recipe.spec.ts', DELIVERY_RECIPE_TITLES),
    ('recipe-authority', 'delivery-recipe-authority-service.spec.ts', RECIPE_AUTHORITY_TITLES),
    ('review', 'review-service.spec.ts', REVIEW_TITLES),
    ('inspector', 'scene-inspector-service.spec.ts', INSPECTOR_TITLES),
    ('profile-metadata', 'delivery-profile-preservation.spec.ts', PROFILE_METADATA_TITLES),
    ('profile-authority', 'delivery-profile-authority-service.spec.ts', PROFILE_AUTHORITY_TITLES),
    ('projection-navigation', 'project-projection-service.spec.ts', PROJECTION_NAVIGATION_TITLES),
)
PROJECT_AUTHORING_SELECTIONS = SELECTIONS
HISTORICAL_PROJECT_AUTHORING_SELECTIONS = tuple(
    (label, filename, HISTORICAL_REVIEW_TITLES if label == "review" else titles)
    for label, filename, titles in PROJECT_AUTHORING_SELECTIONS
)
CREATIVE_PRODUCTION_SELECTIONS = (('production-design',
  'production-design-service.spec.ts',
  ('Production plan raw drafts, claim references and content-bound approval survive actual App navigation '
   'and SQLite restart',
   'Production Hero updates retain editable node identities and a human override; detach preserves objects '
   'across SQLite restart',
   'a held original Production component receipt preserves later typing across actual App remount and a '
   'separate save',
   'a held original Production plan receipt preserves later typing across actual App remount and a '
   'separate save',
   'an unknown Production plan committed result survives reload without replay and needs a fresh history '
   'review',
   'an unknown Production plan matching-peer result survives reload without replay and needs a fresh '
   'history review',
   'Production data recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft',
   'Production pointer recovery failure sends no real service grant or change; explicit healthy retry '
   'persists the retained draft')),
 ('creative-workbench',
  'creative-workbench-service.spec.ts',
  ('stage actual App updates preserve stable editable objects and a human override; detach survives SQLite '
   'restart',
   'procedural actual App updates preserve stable editable objects and a human override; detach survives '
   'SQLite restart',
   'Measured actual source bytes, versioned table and Canvas binding persist through App editing and '
   'SQLite restart',
   'Measured original binding receipt preserves later raw selection and precision across actual App '
   'remount',
   'stage an actual committed response lost in transport remains UNKNOWN through reload and requires fresh '
   'history review',
   'procedural an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'measured an actual committed response lost in transport remains UNKNOWN through reload and requires '
   'fresh history review',
   'creative workbench data backup failure sends no real grant or change; explicit healthy retry saves '
   'retained input',
   'creative workbench pointer backup failure sends no real grant or change; explicit healthy retry saves '
   'retained input')))
BRANCH_CLAIM_SELECTIONS = (('branch-authoring',
  'branch-authoring-service.spec.ts',
  ('causal: a rejected branch creation preserves the exact authored name',
   'causal: a late original branch reply preserves a later name',
   'causal: an unsaved branch name survives App navigation and reload without a mutation',
   'one explicit name creates one editable branch through SQLite and a new service process',
   'UTF-8 branch limit keeps invalid input without sending it',
   'silent branch recovery bytes loss blocks creation until exact readback',
   'silent branch recovery pointer loss blocks creation until exact readback',
   'a committed but lost branch reply survives reopen and explicit inspection without replay',
   'a later A-to-B-to-A branch name stays editable after its original reply and reload',
   'restoring an unconfirmed branch preserves both copies after silent pointer failure',
   'restoring an unconfirmed branch preserves both copies after throw pointer failure',
   'restoring an unconfirmed branch preserves both copies after write-then-throw pointer failure',
   'an ordinary branch name survives a failed first pointer read before one explicit creation',
   'an UNKNOWN branch and a deliberately cleared name survive failed first pointer read without replay',
   'an ordinary branch name survives a failed first copy read before one explicit creation',
   'an UNKNOWN branch and a deliberately cleared name survive failed first copy read without replay',
   'a failed initial pointer read can recover an empty session without discarding newly typed input')),
 ('claim-source',
  'claim-source-service.spec.ts',
  ('raw source revision and inactive source buffers survive App navigation and reload before one valid '
   'commit',
   'same-text source edit invalidates its review and supports a new situated review through SQLite reopen',
   'a committed lost Claim reply stays UNKNOWN through reload and only a separate intent writes again',
   "a held original reply preserves later source input and another claim's independent draft",
   'unconfirmed silent-copy blocks all Claim dispatch until explicit recovery succeeds',
   'unconfirmed silent-pointer blocks all Claim dispatch until explicit recovery succeeds',
   "a real external edit causes a stale save conflict without overwriting either author's work",
   'explicit removal of source and version preserves claim identity through restart',
   'a held Add reply cannot erase the next authored claim in the new-claim editor',
   'retry after initial copy read failure restores untouched raw input before one explicit real save',
   'retry after initial pointer read failure restores untouched raw input before one explicit real save',
   'an edit and return to saved values during initial copy read failure preserves later intent and original '
   'UNKNOWN',
   'an edit and return to saved values during initial pointer read failure preserves later intent and '
   'original UNKNOWN',
   'retry after the first pointer read fails accepts a confirmed empty slot without losing newly authored '
   'raw input')))
BRANCH_CLAIM_UNITS = (('src/branchDraft.test.ts',
  (('branch author intent and exact original result', 'raw name belongs to the exact id'),
   ('branch author intent and exact original result', 'raw name belongs to the exact generation'),
   ('branch author intent and exact original result', 'raw name belongs to the exact active_branch'),
   ('branch author intent and exact original result',
    'incomplete and invalid raw names are retained through the recovery codec'),
   ('branch author intent and exact original result', 'invalid name "" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "   " is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂🙂" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "name\\u0000" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'invalid name "name\x85" is rejected without rewriting input'),
   ('branch author intent and exact original result',
    'native ASCII case and UTF-8 size limits are matched without silent truncation'),
   ('branch author intent and exact original result',
    'matching and unrelated property observations cannot acknowledge UNKNOWN'),
   ('branch author intent and exact original result',
    'only the original callback with an exact added branch can clear its submitted name'),
   ('branch author intent and exact original result',
    'later author input survives the original branch reply'),
   ('branch author intent and exact original result', 'ABA author input survives the original branch reply'),
   ('branch author intent and exact original result',
    'an invalid revision original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid owner original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid missing original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid name original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid parent original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid base original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid workspace original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'an invalid extra original result keeps the unconfirmed name'),
   ('branch author intent and exact original result',
    'a rejection remains UNKNOWN while a proven local no-dispatch failure can be prepared explicitly'),
   ('branch copy readback and concurrent writers', 'raw bytes and the tab pointer must both be confirmed'),
   ('branch copy readback and concurrent writers',
    'a restored UNKNOWN carries its exact source guard through a new writer and reload'),
   ('branch copy readback and concurrent writers',
    'twenty reloads of one UNKNOWN preserve its raw name within the same recovery budget'),
   ('branch copy readback and concurrent writers',
    'silent bytes publication cannot count as a confirmed recovery'),
   ('branch copy readback and concurrent writers',
    'silent pointer publication cannot count as a confirmed recovery'),
   ('branch copy readback and concurrent writers',
    'a peer pointer installed during data publication is retained and blocks publication'),
   ('branch copy readback and concurrent writers',
    'a final byte change after pointer publication remains visible and blocks retry'),
   ('branch copy readback and concurrent writers', "another writer's bytes is never silently replaced"),
   ('branch copy readback and concurrent writers', "another writer's pointer is never silently replaced"),
   ('branch copy readback and concurrent writers',
    'full or corrupt copies stay intact when the bounded catalogue refuses another name'),
   ('branch copy readback and concurrent writers', 'a corrupted scope envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted writer envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted attempt envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers',
    'a corrupted sequence envelope cannot become a saved name'),
   ('branch copy readback and concurrent writers', 'a corrupted extra envelope cannot become a saved name'),
   ('explicit saved-source review never invents an original acknowledgement',
    'separation preserves UNKNOWN and later ABA=false without replay'),
   ('explicit saved-source review never invents an original acknowledgement',
    'separation preserves UNKNOWN and later ABA=true without replay'),
   ('explicit saved-source review never invents an original acknowledgement',
    'an original changing during bytes publication blocks the reloaded successor'),
   ('explicit saved-source review never invents an original acknowledgement',
    'an original changing during pointer publication blocks the reloaded successor'),
   ('explicit saved-source review never invents an original acknowledgement',
    'gap history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'empty history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'duplicate history cannot allow a separate creation'),
   ('explicit saved-source review never invents an original acknowledgement',
    'a source change between project reads invalidates the review'),
   ('branch initial read recovery keeps actual author decisions',
    'adopts an ordinary unread copy, keeping its exact raw name and older source revision'),
   ('branch initial read recovery keeps actual author decisions',
    'adopts untouched UNKNOWN and its exact retained origin without creating another attempt'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "" above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "  later raw branch  " above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'retains later explicit raw name "  Authored branch A  " above both edit sequences'),
   ('branch initial read recovery keeps actual author decisions',
    'A to B to A remains a later edit even when equal to the recovered raw name'),
   ('branch initial read recovery keeps actual author decisions',
    'a retained NOT_SENT becomes UNKNOWN in a new writer while its original bytes remain unchanged'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses an incompatible uncertain attempt instead of replacing its authority'),
   ('branch initial read recovery keeps actual author decisions', 'refuses a recovered foreign id'),
   ('branch initial read recovery keeps actual author decisions', 'refuses a recovered foreign generation'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses a recovered foreign active_branch'),
   ('branch initial read recovery keeps actual author decisions',
    'deduplicates exact origins and rejects differing bytes for one origin key'),
   ('branch initial read recovery keeps actual author decisions',
    'refuses edit-sequence overflow without replacing the recovered or local raw input'),
   ('branch initial read recovery keeps actual author decisions',
    'adopted work survives a failed successor backup and later typing before its next backup'),
   ('branch recovery pointer observation',
    'a failed first read remains unknown and can anchor the first successful writer'),
   ('branch recovery pointer observation', 'a previously observed pointer null cannot adopt a replacement'),
   ('branch recovery pointer observation',
    'a previously observed pointer "20000000-0000-4000-8000-000000000001" cannot adopt a replacement'),
   ('branch recovery pointer observation',
    'the first successful null anchors absence rather than granting permission for a later writer'),
   ('branch recovery pointer observation',
    'publication rechecks ownership after recovery read and preserves the original bytes'))),
 ('src/claimSourceDraft.test.ts',
  (('Claim author input, identity and exact original response',
    'isolates project, generation, branch and individual claim buffers'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision ""'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "1e"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "-"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "01"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "1.5"'),
   ('Claim author input, identity and exact original response',
    'retains incomplete or inexact source revision "9007199254740993"'),
   ('Claim author input, identity and exact original response',
    'keeps inactive source buffers and optional empty versus absent pin distinct'),
   ('Claim author input, identity and exact original response',
    'measures source fields and copies in UTF-8 bytes without truncating raw input'),
   ('Claim author input, identity and exact original response',
    'binds a source-only edit to the complete preimage and same claim ID'),
   ('Claim author input, identity and exact original response',
    'rejects a changed preimage even when the project revision matches'),
   ('Claim author input, identity and exact original response',
    'checks the existing project content lock before arming'),
   ('Claim author input, identity and exact original response',
    'checks the existing claim content lock before arming'),
   ('Claim author input, identity and exact original response',
    'rejects a capture-bound source change without rewriting its claim or capture'),
   ('Claim author input, identity and exact original response',
    'does not acknowledge an observed project with identical submitted values'),
   ('Claim author input, identity and exact original response',
    'keeps later input even after typing B and returning to the original submitted A'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong project and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong generation and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong branch and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong revision and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong target source and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong deleted other claim and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong changed other claim and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response',
    'rejects an original ACK with wrong reordered claims and keeps UNKNOWN'),
   ('Claim author input, identity and exact original response', 'rejects a reply for another local attempt'),
   ('Claim author input, identity and exact original response',
    'accepts exactly one appended claim while preserving all previous claim fields'),
   ('Claim recovery custody without replay',
    'preserves corrupt and foreign copies and rejects extra execution authority'),
   ('Claim recovery custody without replay',
    'recovers original incomplete input after a read failure without inventing an edit, UNKNOWN=false'),
   ('Claim recovery custody without replay',
    'recovers original incomplete input after a read failure without inventing an edit, UNKNOWN=true'),
   ('Claim recovery custody without replay',
    'preserves an explicit edit made while recovery is blocked, return-to-initial=false'),
   ('Claim recovery custody without replay',
    'preserves an explicit edit made while recovery is blocked, return-to-initial=true'),
   ('Claim recovery custody without replay',
    'keeps a newer ordinary draft active while retaining the earlier ordinary copy'),
   ('Claim recovery custody without replay',
    'two failed original reads do not become author edits before a successful recovery'),
   ('Claim recovery custody without replay',
    'a recovered UNKNOWN is not a later edit when separate-copy publication must be retried'),
   ('Claim recovery custody without replay',
    'refuses a foreign owner or target while recovering a failed read'),
   ('Claim recovery custody without replay',
    'does not replace an active uncertain attempt with a different recovered attempt'),
   ('Claim recovery custody without replay',
    'first pointer read failure remains unobserved until a later valid writer is read'),
   ('Claim recovery custody without replay',
    'a known writer cannot be replaced after its original data read failed'),
   ('Claim recovery custody without replay',
    'observed null is a custody observation rather than a failed pointer read'),
   ('Claim recovery custody without replay',
    'a repeated pointer read failure cannot erase a prior successful observation'),
   ('Claim recovery custody without replay',
    'pointer replacement after a first recovered observation blocks successor publication'),
   ('Claim recovery custody without replay', 'recovers a NOT_SENT checkpoint conservatively as UNKNOWN'),
   ('Claim recovery custody without replay', 'does not confirm custody after data-silent'),
   ('Claim recovery custody without replay', 'does not confirm custody after data-throw'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-silent'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-throw'),
   ('Claim recovery custody without replay', 'does not confirm custody after pointer-write-throw'),
   ('Claim recovery custody without replay',
    'detects an interleaved data replacement during pointer publication'),
   ('Claim recovery custody without replay',
    'rechecks custody after budget enumeration and preserves the interleaved writer'),
   ('Claim recovery custody without replay',
    'does not overwrite an earlier saved checkpoint with new author input'),
   ('Claim recovery custody without replay',
    'stable read-history-read on the original revision cannot release a future commit'),
   ('Claim recovery custody without replay',
    'requires complete history and a stable second read without acknowledging equal values'),
   ('Claim recovery custody without replay',
    'retains exact old UNKNOWN bytes when separate-copy phase 1 fails'),
   ('Claim recovery custody without replay',
    'retains exact old UNKNOWN bytes when separate-copy phase 2 fails'),
   ('Claim recovery custody without replay',
    'a confirmed separate edit retains the exact UNKNOWN copy and uses the later explicit base'),
   ('Claim recovery custody without replay',
    'rejects a fresh document with the same stamp but changed claim 0'),
   ('Claim recovery custody without replay',
    'rejects a fresh document with the same stamp but changed claim 1'),
   ('Claim recovery custody without replay',
    'cannot release into an unreviewed same-stamp document with changed claim 0'),
   ('Claim recovery custody without replay',
    'cannot release into an unreviewed same-stamp document with changed claim 1'),
   ('Claim recovery custody without replay',
    'explicitly choosing current values preserves the old draft object and does not dispatch'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after silent, earlier-writer=false'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after throw-before, earlier-writer=false'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after silent, earlier-writer=true'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry publishes its own pointer after throw-before, earlier-writer=true'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry remains blocked while pointer publication still silent fails'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry remains blocked while pointer publication still throw-before fails'),
   ('Claim pointer publication after a partial local write',
    'a setter that writes the new pointer then throws cannot grant the active session publication custody'),
   ('Claim pointer publication after a partial local write',
    'same-draft retry cannot overwrite another writer published after the original pointer failure'),
   ('Claim pointer publication after a partial local write',
    'same-draft pointer repair rechecks its data after publication without overwriting an interleaved '
    'replacement'))))
BRANCH_CLAIM_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/BriefWorkspace.tsx',
 'apps/studio/src/ClaimSourceEditor.tsx',
 'apps/studio/src/HistoryWorkspaces.tsx',
 'apps/studio/src/ReviewWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/branchDraft.test.ts',
 'apps/studio/src/branchDraft.ts',
 'apps/studio/src/claim-source-editor.css',
 'apps/studio/src/claimSourceDraft.test.ts',
 'apps/studio/src/claimSourceDraft.ts',
 'apps/studio/src/reviewDraft.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useBranchDraft.tsx',
 'apps/studio/src/useClaimSourceDraft.ts',
 'apps/studio/tests/branch-authoring-service.spec.ts',
 'apps/studio/tests/claim-source-service.spec.ts',
 'crates/domain/src/claim_source_edit.rs',
 'crates/domain/src/history.rs',
 'crates/domain/src/lib.rs',
 'crates/domain/tests/claim_source_edit.rs',
 'crates/service/tests/claim_source_edit.rs')
CLAIM_SOURCE_RUST_CASES = {'domain': ('source_only_edit_preserves_claim_id_order_references_and_unrelated_content',
            'invalid_preimage_id_noop_source_and_utf8_limits_are_atomic',
            'locks_protect_project_or_target_without_blocking_an_unrelated_claim',
            'wire_requires_complete_preimage_and_replacement_but_accepts_explicit_null',
            'source_change_invalidates_resolved_review_even_when_text_does_not_change',
            'new_review_requires_an_existing_claim_target',
            'immutable_capture_source_and_context_reject_edits_without_rebinding_original_evidence'),
 'service': ('exact_source_edit_reopens_with_same_identity_history_and_original_request_receipt',
             'another_client_revision_conflicts_even_when_the_claim_preimage_is_still_exact',
             'current_revision_does_not_authorize_a_stale_claim_preimage',
             'a_source_only_change_invalidates_resolved_active_branch_reviews_and_retains_their_history',
             'content_locks_reject_project_or_target_edits_without_changing_the_saved_project',
             'explicit_source_withdrawal_is_durable_and_a_rejected_noop_adds_no_content_event',
             'immutable_capture_bindings_survive_direct_service_rejections_and_durable_reopen',
             'invalid_fields_assets_future_sources_and_missing_review_targets_have_no_partial_commit')}
# Retained pre-closeout catalogues are historical inputs, never current PASS.
HISTORICAL_DEFAULT_SERVICE_SELECTIONS = (*PROJECT_AUTHORING_SELECTIONS, *CREATIVE_PRODUCTION_SELECTIONS,
                                         *BRANCH_CLAIM_SELECTIONS)
HISTORICAL_CREATIVE_PRODUCTION_SELECTIONS = CREATIVE_PRODUCTION_SELECTIONS
HISTORICAL_BRANCH_CLAIM_SELECTIONS = BRANCH_CLAIM_SELECTIONS
HISTORICAL_REVIEW_SERVICE_SELECTIONS = tuple(row for row in PROJECT_AUTHORING_SELECTIONS if row[0] == "review")
CLOSEOUT_SERVICE_ADDITIONS = {'canvas': ('saved Canvas font drives the read-only Delivery choice through real SQLite restart and rejects '
            'a mixed-font source',
            'explicit Canvas world and local reparent preserve authored channels through real SQLite reopen',
            'an uncertain Canvas reparent cannot be acknowledged by a matching peer revision and forks '
            'retained input explicitly'),
 'review': ('selected review deliverable, language and rational range persist through actual App reload and '
            'SQLite restart',
            'later explicit empty review profile survives a held original reply and saves separately to '
            'SQLite',
            'lost reply keeps original selected review profile UNKNOWN while a later empty selection '
            'survives actual SQLite recovery',
            'pre-dispatch review transport interruption keeps the saved base UNKNOWN until an independent '
            'SQLite peer advances it without replay'),
 'production-design': ('Production retries an initial pointer read for ordinary input without replacing raw '
                       'intent or replaying a real operation',
                       'Production retries an initial pointer read for UNKNOWN input without replacing raw '
                       'intent or replaying a real operation',
                       'Production retries an initial copy read for ordinary input without replacing raw '
                       'intent or replaying a real operation',
                       'Production retries an initial copy read for UNKNOWN input without replacing raw '
                       'intent or replaying a real operation',
                       'Production confirms an empty pointer after an initial read failure while retaining '
                       'the input typed during the blocker',
                       'Production component retains its first observed UNKNOWN bytes when a failed recovery '
                       'read is followed by a same-writer replacement',
                       'Production plan retains its first observed UNKNOWN bytes when a failed recovery read '
                       'is followed by a same-writer replacement',
                       'opaque source attachment raw input and a silent pointer failure preserve the '
                       'existing asset before one explicit SQLite attachment',
                       'opaque source attachment late real ACK retains two scene drafts and permits only a '
                       'separate explicit later attachment',
                       'opaque source attachment lost committed service reply preserves the exact submitted '
                       'capsule and later cleared input without replay',
                       'opaque source attachment pre-dispatch delivery interruption stays UNKNOWN on N until '
                       'a real peer advances SQLite, with no original replay',
                       'Scene boundary explicit source binding, Draft and Explicit saves preserve authored '
                       'objects and assets through SQLite reopen and journalled removal',
                       'Scene boundary keeps invalid raw endpoint JSON and its prior source when a real peer '
                       'makes explicit rebind stale'),
 'branch-authoring': ('an unchanged SQLite branch base stays UNKNOWN until a real peer advances it, '
                      'preserving later input without replay',),
 'branch-combination': ('two real strategies produce a persisted inactive candidate, then editable undo '
                        'preserves peer style and historical reviews',
                        'lost original reply preserves late choices and UNKNOWN through reload, exact read '
                        'and explicit separate intent',
                        'an independent peer wins CAS after preview; rejected combination retains raw and '
                        'never adds a phantom candidate')}
CANVAS_TITLES = (*CANVAS_TITLES, *CLOSEOUT_SERVICE_ADDITIONS["canvas"])
REVIEW_TITLES = (*REVIEW_TITLES, *CLOSEOUT_SERVICE_ADDITIONS["review"])
PROJECT_AUTHORING_SELECTIONS = tuple(
    (label, filename, CANVAS_TITLES if label == "canvas" else REVIEW_TITLES if label == "review" else titles)
    for label, filename, titles in PROJECT_AUTHORING_SELECTIONS
)
CREATIVE_PRODUCTION_SELECTIONS = tuple(
    (label, filename, (*titles, *CLOSEOUT_SERVICE_ADDITIONS.get(label, ())))
    for label, filename, titles in CREATIVE_PRODUCTION_SELECTIONS
)
BRANCH_CLAIM_SELECTIONS = (*tuple(
    (label, filename, (*titles, *CLOSEOUT_SERVICE_ADDITIONS.get(label, ())))
    for label, filename, titles in BRANCH_CLAIM_SELECTIONS
), ("branch-combination", "branch-combination-service.spec.ts", CLOSEOUT_SERVICE_ADDITIONS["branch-combination"]))
SELECTIONS = (*PROJECT_AUTHORING_SELECTIONS, *CREATIVE_PRODUCTION_SELECTIONS, *BRANCH_CLAIM_SELECTIONS)

# Each per-file process limit includes every declared test timeout and the
# configured 120s server startup. Only an original run establishes actual timing.
SELECTION_GROUPS = {
    "branch-claim": {"labels": ("branch-authoring", "claim-source", "branch-combination"), "required_tests": 35},
    "project-authoring": {"labels": tuple(row[0] for row in PROJECT_AUTHORING_SELECTIONS),
                          "required_tests": 143},
    "project-authoring-core": {"labels": tuple(row[0] for row in PROJECT_AUTHORING_SELECTIONS if row[0] != "review"), "required_tests": 132},
    "creative-production": {"labels": ("production-design", "creative-workbench"),
                            "required_tests": 30},
    "complete-authoring": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review",
                   "inspector", "profile-metadata", "profile-authority"),
        "required_tests": 69,
    },
    "authoring-drafts": {
        "labels": ("beat-draft", "beat-time", "delivery-recipe", "recipe-authority", "review"),
        "required_tests": 43,
    },
}
SELECTION_TIMEOUTS = {
    "branch-authoring": 2340,
    "claim-source": 1860,
    "branch-combination": 540,
    "production-design": 2700,
    "creative-workbench": 1260,
    "canvas": 2160,
    "ink": 180,
    "program": 600,
    "brief": 900,
    "brief-recovery": 180,
    "brief-uncertain": 900,
    "active-voice": 900,
    "camera": 1200,
    "gesture": 900,
    "narrative": 1200,
    'beat-draft': 1500,
    'beat-time': 660,
    'delivery-recipe': 1260,
    'recipe-authority': 780,
    'review': 1500,
    'inspector': 900,
    'profile-metadata': 1500,
    'profile-authority': 900,
    'projection-navigation': 1260,
}


# Synthetic browser scope is separate from every real-service selection.
BROWSER_SELECTIONS = (('browser-asset-integrity',
  'asset-integrity.spec.ts',
  ('Dependencies audits real CAS identities in pages without inventing scene consumers',
   'browser demo cannot claim local CAS integrity or fabricate Graph associations')),
 ('browser-brand-campaign',
  'brand-campaign.spec.ts',
  ('browser with no canonical BrandProfile cannot claim campaign admission',
   'two-scene source-bound brand rollout is reviewed before one canonical mutation',
   'failed synthetic late CAS never claims a successful campaign revision')),
 ('browser-canvas-native-linear-motion',
  'canvas-native-linear-motion.spec.ts',
  ('Canvas issues exactly one source-scoped native linear move and preserves base pose',
   'native move UI blocks beat-constrained scenes and never claims native render in browser demo')),
 ('browser-canvas-native-outcubic',
  'canvas-native-outcubic.spec.ts',
  ('Canvas writes one atomic cubic-out X/Y move through the saved native bridge',
   'browser demo never pretends exact native Film motion is already proven')),
 ('browser-editor',
  'editor.spec.ts',
  ('editor exposes real workspaces and browser-demo mutations',
   'renderer and locks mutate the same visible project revision',
   'keyboard focus is visible and workspace navigation remains operable',
   'theme preference persists without mutating the project revision',
   'scene selection and timeline seeking share one playhead context',
   'beat and cue selection share scene and playhead without mutating project state',
   'design preview transport steps and plays without claiming a project edit',
   'scene beat locks are enforced while unlocked beats stay synchronized with the timeline',
   'offline state is explicit while local editing remains available',
   'workflow intelligence never fabricates canonical evidence in browser demo',
   'model preflight exposes minimal context without dispatch or silent fallback',
   'canvas edits and stored alternatives use project revisions rather than local-only UI state',
   'scene duration ripple reflows the shared timeline and journals one change',
   'visual system commits a new version and project style lock is enforceable',
   'canvas hierarchy style relations and safe removal are versioned',
   'production jobs workspace never fabricates runtime evidence in browser demo mode',
   'portable project delivery is truthful in browser demo mode',
   'delivery profiles are versioned and filesystem claims remain truthful',
   'delivery variants keep lineage, replan alternate aspect ratios and require explicit crop approval',
   'audio workspace keeps measurement claims honest and versions mix intent',
   'optional renderers stay closed until a reviewed project extension is explicitly enabled',
   'Launchwright handoffs use public refs and exact output digests without remote mutation claims',
   'OpenTimelineIO export stays filesystem-gated and never implies lossless interchange in browser mode',
   'audio cue seeking and canvas share the same global editorial clock',
   'canvas scrubbing and keyframe jumps use scene-relative time without splitting the clock',
   'review point anchors capture the global timeline and seek without making an edit',
   'review time ranges are validated and historical anchors do not imply renewed approval',
   'desktop job receipts reread active jobs and preserve explicit manual refresh for terminal receipts')),
 ('browser-evaluator-experiment',
  'evaluator-experiment.spec.ts',
  ('a browser can compare original data but cannot claim trusted candidate admission',
   're-editing a candidate or selecting a different scene invalidates the previous comparison',
   'an approved owner registration sends one scoped canonical evaluator change, never an executable skill')),
 ('browser-film-preflight',
  'film-preflight.spec.ts',
  ('native semantic Film preflight is read-only and cannot be mistaken for real render',
   'unsupported Film projection prevents native dispatch for unchanged inputs',
   'browser demo cannot invent native Film semantic projection readiness')),
 ('browser-generation-journal',
  'generation-journal.spec.ts',
  ('desktop preflight, intent commit, and exact reservation are separate user actions',
   'late preflight cannot become a confirmation for another working scene',
   'browser demo never simulates provider jobs or spending')),
 ('browser-history-pagination',
  'history-pagination.spec.ts',
  ('recent-first committed history exposes all 260 revisions with bounded older pages',
   'an earlier-history read error keeps the recent page and allows retry')),
 ('browser-multi-segment-readiness',
  'multi-segment-readiness.spec.ts',
  ('Deliver checks multi-segment source readiness with only an owner preview token',
   'Browser demo has no native multi-segment readiness source')),
 ('browser-native-av-master',
  'native-av-master.spec.ts',
  ('desktop AV master is linked only to session-native visual evidence and a measured saved voice',
   'UI refuses to offer native mastering for incompatible measured voice duration',
   'browser demo never claims native AV master authority')),
 ('browser-native-multisegment-av',
  'native-multisegment-av.spec.ts',
  ('desktop multisegment final AV is source-token-bound, measured and export-authorized',
   'desktop multisegment captioned MP4 remains review-only')),
 ('browser-native-original-mix-voice-binding',
  'native-original-mix-voice-binding.spec.ts',
  ('an unbound saved profile blocks audition before permission or a native request',
   'a different saved voice binding remains blocked without a native request',
   'a matching saved voice binding enables local confirmation without dispatch')),
 ('browser-native-original-mix',
  'native-original-mix.spec.ts',
  ('browser-only Audio cannot fabricate a mixed WAV or rights receipt',
   'explicit desktop audition reads a saved recipe and leaves that project revision unchanged')),
 ('browser-native-pixel-contrast',
  'native-pixel-contrast.spec.ts',
  ('browser demo without native source never fabricates visual metrics',
   'exact native PNG and two manually chosen opaque colors measure 21:1, never change the project',
   'tampered synthetic PNG is refused before any contrast metric exists')),
 ('browser-native-preview',
  'native-preview.spec.ts',
  ('sampled native PNG readback uses only a session grant and follows the editorial playhead',
   'invalid synthetic frame bytes display a truthful readback error and a retry')),
 ('browser-native-progressive-preview',
  'native-progressive-preview.spec.ts',
  ('rapid editorial seeks coalesce delayed native reads and settle on exact source frame',)),
 ('browser-native-review-repair',
  'native-review-repair.spec.ts',
  ('a sampled native frame becomes a user-authored scoped repair, never an automatic edit',)),
 ('browser-otio-owner-cut',
  'otio-owner-cut.spec.ts',
  ('browser demo cannot admit a forged cut or mutate the active project',
   'owner reviews explicit native changes and accepts one project transaction',
   'explicit loss confirmation does not permit a tampered OTIO scene extension')),
 ('browser-portable-mp4-integrity',
  'portable-mp4-integrity.spec.ts',
  ('local portable MP4 verification is explicit, read-only and supports an independent hash',
   'browser preview cannot claim to verify local files')),
 ('browser-production-history',
  'production-history.spec.ts',
  ('history enumerates all local jobs beyond recent-window limit with no driver mutation',
   'independent status append invalidates history cursor and requires explicit restart',
   'browser demo cannot synthesize historical jobs or a mutable backend',
   'history keeps colliding job references across provider without duplicate identity rows',
   'history keeps colliding job references across generation without duplicate identity rows',
   'history keeps colliding job references across revision without duplicate identity rows',
   'an original Motion Canvas observation never decorates another provider or historical stamp')),
 ('browser-program-av-monitor',
  'program-av-monitor.spec.ts',
  ('Program AV monitor reads exact source token only for matching scene, profile and revision',
   'browser demo cannot invent final AV monitoring from a semantic project')),
 ('browser-project-mutation',
  'project-mutation.spec.ts',
  ('pending asset import shares the ordinary edit latch and publishes its revision',
   'pending voice import shares the ordinary edit latch and publishes its revision',
   'a pending portable restore cannot race source edits or publish into another revision',
   'New profile: failed native CAS does not select an unsaved delivery profile',
   'Derive selected: failed native CAS does not select an unsaved delivery profile',
   "rejected scene creation keeps the editor's draft open until a confirmed CAS",
   'rejected Canvas node creation never selects an object that was not committed',
   'a successful node request in scene A preserves a later scene B selection',
   'a rejected node request in scene A preserves a later scene B selection',
   'a successful create preserves later selection and text',
   'a successful create preserves later text on the same selected node',
   'a rejected create preserves later selection and text',
   'a rejected create preserves later text on the same selected node',
   'a successful create preserves a later transform on the same selected node',
   'a successful create preserves a later motion value on the same selected node',
   'returning to the original selection still supersedes a delayed create',
   'a successful delayed create selects its saved object when the inspector was untouched',
   'a delayed initial project read cannot roll back a project already published by a mutation')),
 ('browser-render-receipt',
  'render-receipt.spec.ts',
  ('synthetic native receipt remains scene/revision scoped across Deliver and Timeline',)),
 ('browser-reproducible-capture',
  'reproducible-capture.spec.ts',
  ('pinned first-party browser scenario yields a repeatable redacted screenshot linked to a source claim',)),
 ('browser-v01-mlt-cleanup-inventory',
  'v01-mlt-cleanup-inventory.spec.ts',
  ('installed Jobs can inspect MLT creation receipts without driver dispatch or consent',
   'foreign source receipt inventory cannot be displayed as current project',
   'browser preview cannot inspect installed MLT receipts or invent cleanup')),
 ('browser-v01-native-recovery',
  'v01-native-recovery.spec.ts',
  ('v0.1 owner sees explicit UNKNOWN recovery path without any automatic retry',
   'browser-only owner Jobs has no fabricated recovery warning')),
 ('browser-v01-original-native-status',
  'v01-original-native-status.spec.ts',
  ('owner explicitly observes only original native job; no render restart or forged media success',
   'foreign response with fake verified media is rejected, never becomes success',
   'historical, other-provider and browser-only jobs cannot trigger original status read')),
 ('browser-v01-owner-first-render',
  'v01-owner-first-render.spec.ts',
  ('v0.1 first owner render requires exact Film and rechecked CLI without faking a provider',
   'browser demo explicitly rejects owner-native dispatch without any grants')),
 ('browser-command-palette',
  'command-palette.spec.ts',
  ('palette navigates workspaces without mutating the shared project',
   'global shortcut works in focused fields, and Escape restores focus',
   'palette consumes transport keys and executes exactly one frame-step action',
   'keyboard guide, zero results, focus trap, and reopen keep the palette usable',
   'palette view actions can toggle light theme without a content edit',
   'keyboard selection follows visible grouped results rather than hidden search-score order',
   'palette footer and help stay reachable at desktop and compact viewport heights')),
 ('browser-scene-beat-preservation',
  'scene-beat-preservation.spec.ts',
  ('all raw beat fields survive workspace remount and page reload, including incomplete fractions and '
   'exponents',
   "equal saved beats under another id cannot adopt the original scope's raw draft",
   "equal saved beats under another generation cannot adopt the original scope's raw draft",
   "equal saved beats under another active_branch cannot adopt the original scope's raw draft",
   "equal saved beats under another sceneId cannot adopt the original scope's raw draft",
   "equal saved beats under another beatId cannot adopt the original scope's raw draft",
   'the original beat reply preserves later raw edits through remount and a second explicit save',
   'returning to the old objective during a save remains a later decision after the original ACK',
   'matching saved props cannot acknowledge an unresolved original beat request',
   'UNKNOWN keeps submitted and later raw values across reload without replay',
   'a silent copy write failure prevents dispatch and explicit retry recovers the draft',
   'a silent pointer write failure prevents dispatch and explicit retry recovers the draft',
   'a pointer failure leaves a readable orphan which can be explicitly restored after reload',
   'a failed ACK checkpoint retains the armed original and later input without treating reload as '
   'confirmation',
   'a concurrent retime keeps raw text until the author explicitly combines it with saved timing',
   'a shortened scene retains an out-of-range raw draft until explicit comparison and repair',
   'a concurrently removed beat keeps raw input and needs explicit recreation before any command',
   'late typing during the original removal stays recoverable without automatically recreating the beat',
   'Fill timing gap uses a retained UNKNOWN draft if its original creation reply is unavailable',
   'two windows retain separate beat writers and never choose the newest foreign copy on reload',
   'a corrupt own beat copy stays intact and cannot silently adopt a readable foreign copy',
   'a cloned tab inherits a readable draft but forks its writer before independent edits',
   "a late original reply cannot replace a different selected owner's beat draft",
   'a downloaded draft survives complete local recovery failure and imports as editable raw input',
   'import cannot overwrite current unbacked input, and foreign-owner files are rejected',
   'explicit catalog restore keeps clean checkpoint A when the saved beat is B',
   'explicit file restore keeps clean checkpoint A when the saved beat is B',
   'restoring a clean checkpoint retains the prior unsent draft as a separately recoverable copy',
   'automatic reload of a clean checkpoint follows the newer saved beat without creating author intent',
   'a closed recovery catalog exposes every retained copy after storage enumeration changes')))
BROWSER_OMITTED_SERVICE_CASES = (('project-mutation.spec.ts',
  'real asset import already persisted holds the Brief latch until its real reply is published'),
 ('project-mutation.spec.ts',
  'real voice import already persisted holds the Brief latch until its real reply is published'),
 ('project-mutation.spec.ts',
  'real import grants reject missing authority, path/effect/scope/generation mismatches, stale sources and '
  'reopen replay'),
 ('project-mutation.spec.ts',
  'real portable export and verified restore rotate generation, preserve CAS and serialize the next Brief '
  'edit'))
BROWSER_DESCRIBE_TITLES = {('project-mutation.spec.ts', 'a successful create preserves later selection and text'): ('Canvas '
                                                                                          'interaction '
                                                                                          'during delayed '
                                                                                          'creation — '
                                                                                          'synthetic UI '
                                                                                          'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves later text on the same selected node'): ('Canvas '
                                                                                                      'interaction '
                                                                                                      'during '
                                                                                                      'delayed '
                                                                                                      'creation '
                                                                                                      '— '
                                                                                                      'synthetic '
                                                                                                      'UI '
                                                                                                      'contract',),
 ('project-mutation.spec.ts', 'a rejected create preserves later selection and text'): ('Canvas interaction '
                                                                                        'during delayed '
                                                                                        'creation — '
                                                                                        'synthetic UI '
                                                                                        'contract',),
 ('project-mutation.spec.ts', 'a rejected create preserves later text on the same selected node'): ('Canvas '
                                                                                                    'interaction '
                                                                                                    'during '
                                                                                                    'delayed '
                                                                                                    'creation '
                                                                                                    '— '
                                                                                                    'synthetic '
                                                                                                    'UI '
                                                                                                    'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves a later transform on the same selected node'): ('Canvas '
                                                                                                             'interaction '
                                                                                                             'during '
                                                                                                             'delayed '
                                                                                                             'creation '
                                                                                                             '— '
                                                                                                             'synthetic '
                                                                                                             'UI '
                                                                                                             'contract',),
 ('project-mutation.spec.ts', 'a successful create preserves a later motion value on the same selected node'): ('Canvas '
                                                                                                                'interaction '
                                                                                                                'during '
                                                                                                                'delayed '
                                                                                                                'creation '
                                                                                                                '— '
                                                                                                                'synthetic '
                                                                                                                'UI '
                                                                                                                'contract',),
 ('project-mutation.spec.ts', 'returning to the original selection still supersedes a delayed create'): ('Canvas '
                                                                                                         'interaction '
                                                                                                         'during '
                                                                                                         'delayed '
                                                                                                         'creation '
                                                                                                         '— '
                                                                                                         'synthetic '
                                                                                                         'UI '
                                                                                                         'contract',),
 ('project-mutation.spec.ts', 'a successful delayed create selects its saved object when the inspector was untouched'): ('Canvas '
                                                                                                                         'interaction '
                                                                                                                         'during '
                                                                                                                         'delayed '
                                                                                                                         'creation '
                                                                                                                         '— '
                                                                                                                         'synthetic '
                                                                                                                         'UI '
                                                                                                                         'contract',),
 ('project-mutation.spec.ts', 'real asset import already persisted holds the Brief latch until its real reply is published'): ('real '
                                                                                                                               'service '
                                                                                                                               'imports '
                                                                                                                               'and '
                                                                                                                               'portable '
                                                                                                                               'restore',),
 ('project-mutation.spec.ts', 'real voice import already persisted holds the Brief latch until its real reply is published'): ('real '
                                                                                                                               'service '
                                                                                                                               'imports '
                                                                                                                               'and '
                                                                                                                               'portable '
                                                                                                                               'restore',),
 ('project-mutation.spec.ts', 'real import grants reject missing authority, path/effect/scope/generation mismatches, stale sources and reopen replay'): ('real '
                                                                                                                                                         'service '
                                                                                                                                                         'imports '
                                                                                                                                                         'and '
                                                                                                                                                         'portable '
                                                                                                                                                         'restore',),
 ('project-mutation.spec.ts', 'real portable export and verified restore rotate generation, preserve CAS and serialize the next Brief edit'): ('real '
                                                                                                                                               'service '
                                                                                                                                               'imports '
                                                                                                                                               'and '
                                                                                                                                               'portable '
                                                                                                                                               'restore',)}
BROWSER_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/AudioWorkspace.tsx',
 'apps/studio/src/BrandCampaignWorkbench.tsx',
 'apps/studio/src/BrandTasteWorkbench.tsx',
 'apps/studio/src/BriefWorkspace.tsx',
 'apps/studio/src/CameraReframeWorkbench.tsx',
 'apps/studio/src/CanvasStructureEditor.tsx',
 'apps/studio/src/CanvasWorkspace.tsx',
 'apps/studio/src/CaptionReadabilityReview.tsx',
 'apps/studio/src/CaptureSourceWorkbench.tsx',
 'apps/studio/src/CommandPalette.tsx',
 'apps/studio/src/ComponentDistillationWorkbench.tsx',
 'apps/studio/src/CompositionStudy.tsx',
 'apps/studio/src/CreativeAlternativesPanel.tsx',
 'apps/studio/src/CreativeEvaluatorWorkbench.tsx',
 'apps/studio/src/CreativePatchWorkbench.tsx',
 'apps/studio/src/CreativeRuntimeDoctorPanel.tsx',
 'apps/studio/src/CreativeWorkspaces.tsx',
 'apps/studio/src/DeliveryProfiles.tsx',
 'apps/studio/src/DeliveryVariantImpactReview.tsx',
 'apps/studio/src/DependenciesWorkspace.tsx',
 'apps/studio/src/EvaluatorExperimentWorkbench.tsx',
 'apps/studio/src/ExpandedStudy.tsx',
 'apps/studio/src/FileImportRecovery.tsx',
 'apps/studio/src/HistoryWorkspaces.tsx',
 'apps/studio/src/IdentityOriginWorkbench.tsx',
 'apps/studio/src/IntegrationsWorkspace.tsx',
 'apps/studio/src/MeasuredDataWorkbench.tsx',
 'apps/studio/src/ModelContextInspector.tsx',
 'apps/studio/src/MusicClockWorkbench.tsx',
 'apps/studio/src/MusicConformWorkbench.tsx',
 'apps/studio/src/NarrationTakeReview.tsx',
 'apps/studio/src/NarrativeWorkspace.tsx',
 'apps/studio/src/NativeAvReview.tsx',
 'apps/studio/src/NativeContrastWorkbench.tsx',
 'apps/studio/src/NativeFrameStage.tsx',
 'apps/studio/src/NativeInspectionWorkbench.tsx',
 'apps/studio/src/NativeOriginalMixReview.tsx',
 'apps/studio/src/NativePropertyFidelityWorkbench.tsx',
 'apps/studio/src/OtioCutReviewWorkbench.tsx',
 'apps/studio/src/PaidGenerationWorkspace.tsx',
 'apps/studio/src/ProceduralFieldWorkbench.tsx',
 'apps/studio/src/ProductStageWorkbench.tsx',
 'apps/studio/src/ProductionDesignWorkspace.tsx',
 'apps/studio/src/ProductionJobs.tsx',
 'apps/studio/src/ProgramDesignStudy.tsx',
 'apps/studio/src/ProjectLibraryDialog.tsx',
 'apps/studio/src/ProjectProjectionWorkspace.tsx',
 'apps/studio/src/RenderTailWorkbench.tsx',
 'apps/studio/src/ReviewWorkspace.tsx',
 'apps/studio/src/SceneBeatEditor.tsx',
 'apps/studio/src/SceneInspector.tsx',
 'apps/studio/src/SemanticSfxWorkbench.tsx',
 'apps/studio/src/TypographySafetyReview.tsx',
 'apps/studio/src/VariantContinuityReview.tsx',
 'apps/studio/src/VisualLanguageEditor.tsx',
 'apps/studio/src/WorkflowWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/audioDraft.css',
 'apps/studio/src/audioDraft.ts',
 'apps/studio/src/brand-campaign.css',
 'apps/studio/src/brandCampaign.ts',
 'apps/studio/src/brandGovernance.ts',
 'apps/studio/src/briefDraft.ts',
 'apps/studio/src/cameraReframe.ts',
 'apps/studio/src/cameraReframeDraft.ts',
 'apps/studio/src/canvasCamera.ts',
 'apps/studio/src/canvasInheritedInk.ts',
 'apps/studio/src/canvasMotion.ts',
 'apps/studio/src/canvasWorkspaceDraft.ts',
 'apps/studio/src/caption-readability.css',
 'apps/studio/src/captionReadability.ts',
 'apps/studio/src/captureSource.ts',
 'apps/studio/src/command-palette.css',
 'apps/studio/src/componentDistillation.ts',
 'apps/studio/src/creative-alternatives.css',
 'apps/studio/src/creative-runtime-doctor.css',
 'apps/studio/src/creativeAlternativeEvidence.ts',
 'apps/studio/src/creativeDoctor.ts',
 'apps/studio/src/creativeEvaluator.ts',
 'apps/studio/src/creativeProduction.ts',
 'apps/studio/src/creativeUndo.ts',
 'apps/studio/src/delivery-variant-impact.css',
 'apps/studio/src/deliveryProfileDraft.ts',
 'apps/studio/src/deliveryRecipe.ts',
 'apps/studio/src/deliveryRecipeDraft.ts',
 'apps/studio/src/deliveryVariantImpact.ts',
 'apps/studio/src/editorCommandSearch.ts',
 'apps/studio/src/evaluator-experiment.css',
 'apps/studio/src/evaluatorExperiment.ts',
 'apps/studio/src/file-import.css',
 'apps/studio/src/fileImport.ts',
 'apps/studio/src/firstNativeProduction.ts',
 'apps/studio/src/fixture.ts',
 'apps/studio/src/generationJournalApi.ts',
 'apps/studio/src/historyPaging.ts',
 'apps/studio/src/identityOrigin.ts',
 'apps/studio/src/jobRefresh.ts',
 'apps/studio/src/main.tsx',
 'apps/studio/src/measuredData.ts',
 'apps/studio/src/musicClock.ts',
 'apps/studio/src/narration-review.css',
 'apps/studio/src/narrationReviewApi.ts',
 'apps/studio/src/narrationReviewTypes.ts',
 'apps/studio/src/narrativeDraft.ts',
 'apps/studio/src/native-contrast.css',
 'apps/studio/src/nativeFrameSelection.ts',
 'apps/studio/src/nativeInspection.ts',
 'apps/studio/src/nativeMixTypes.ts',
 'apps/studio/src/nativePixelContrast.ts',
 'apps/studio/src/nativeProgressivePreview.ts',
 'apps/studio/src/nativeRecovery.ts',
 'apps/studio/src/nativeReviewRepair.ts',
 'apps/studio/src/otioCutReview.ts',
 'apps/studio/src/otioCutReviewTestFixtures.ts',
 'apps/studio/src/paidGenerationTypes.ts',
 'apps/studio/src/proceduralField.ts',
 'apps/studio/src/productStage.ts',
 'apps/studio/src/production-design.css',
 'apps/studio/src/programMaster.ts',
 'apps/studio/src/project-library.css',
 'apps/studio/src/projectLibrary.ts',
 'apps/studio/src/projectMutation.ts',
 'apps/studio/src/projectProjection.ts',
 'apps/studio/src/renderReceipt.ts',
 'apps/studio/src/renderTailImpact.ts',
 'apps/studio/src/reviewDraft.ts',
 'apps/studio/src/sceneBeatDraft.ts',
 'apps/studio/src/sceneBeatTiming.ts',
 'apps/studio/src/sceneInspectorDraft.ts',
 'apps/studio/src/scopedChangeDraft.ts',
 'apps/studio/src/semanticSfx.ts',
 'apps/studio/src/stageCapture.ts',
 'apps/studio/src/styles.css',
 'apps/studio/src/timecode.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/typographySafety.css',
 'apps/studio/src/typographySafety.ts',
 'apps/studio/src/variant-continuity.css',
 'apps/studio/src/variantContinuity.ts',
 'apps/studio/src/visualLanguageDraft.ts',
 'apps/studio/tests/asset-integrity.spec.ts',
 'apps/studio/tests/brand-campaign.spec.ts',
 'apps/studio/tests/canvas-native-linear-motion.spec.ts',
 'apps/studio/tests/canvas-native-outcubic.spec.ts',
 'apps/studio/tests/command-palette.spec.ts',
 'apps/studio/tests/editor.spec.ts',
 'apps/studio/tests/evaluator-experiment.spec.ts',
 'apps/studio/tests/film-preflight.spec.ts',
 'apps/studio/tests/fixtures/scene-beat-authority.tsx',
 'apps/studio/tests/generation-journal.spec.ts',
 'apps/studio/tests/history-pagination.spec.ts',
 'apps/studio/tests/multi-segment-readiness.spec.ts',
 'apps/studio/tests/native-av-master.spec.ts',
 'apps/studio/tests/native-multisegment-av.spec.ts',
 'apps/studio/tests/native-original-mix-voice-binding.spec.ts',
 'apps/studio/tests/native-original-mix.spec.ts',
 'apps/studio/tests/native-pixel-contrast.spec.ts',
 'apps/studio/tests/native-preview.spec.ts',
 'apps/studio/tests/native-progressive-preview.spec.ts',
 'apps/studio/tests/native-review-repair.spec.ts',
 'apps/studio/tests/otio-owner-cut.spec.ts',
 'apps/studio/tests/portable-mp4-integrity.spec.ts',
 'apps/studio/tests/production-history.spec.ts',
 'apps/studio/tests/program-av-monitor.spec.ts',
 'apps/studio/tests/project-mutation.spec.ts',
 'apps/studio/tests/render-receipt.spec.ts',
 'apps/studio/tests/reproducible-capture.spec.ts',
 'apps/studio/tests/scene-beat-preservation.spec.ts',
 'apps/studio/tests/support/scopedChangeService.ts',
 'apps/studio/tests/support/selectedSyntheticProject.ts',
 'apps/studio/tests/support/syntheticProjectReads.ts',
 'apps/studio/tests/v01-mlt-cleanup-inventory.spec.ts',
 'apps/studio/tests/v01-native-recovery.spec.ts',
 'apps/studio/tests/v01-original-native-status.spec.ts',
 'apps/studio/tests/v01-owner-first-render.spec.ts',
 'apps/studio/unit/synthetic-project-reads.test.mjs')
HISTORICAL_BROWSER_SELECTIONS = BROWSER_SELECTIONS
CREATIVE_PRODUCTION_BROWSER_SELECTIONS = (('browser-production-design',
  'production-design-preservation.spec.ts',
  ('Production component raw input survives tool navigation, scene changes, remount and reload without a '
   'save',
   'Production plan preserves exact lists, reviewer, note, shot choices and clock through navigation and '
   'reload',
   'Production component drafts cannot cross id and restore the separate author on return',
   'Production component drafts cannot cross generation and restore the separate author on return',
   'Production component drafts cannot cross active_branch and restore the separate author on return',
   'Production plan drafts cannot cross id and restore the separate author on return',
   'Production plan drafts cannot cross generation and restore the separate author on return',
   'Production plan drafts cannot cross active_branch and restore the separate author on return',
   'a deferred Production concept digest cannot overwrite or send later author input',
   'a Production digest completing after owner does not dispatch an obsolete approval',
   'a Production digest completing after revision does not dispatch an obsolete approval',
   'a Production digest completing after unmount does not dispatch an obsolete approval',
   'an original Production component ACK retains later input and permits its separate second save',
   'an original Production plan ACK retains later input and permits its separate second save',
   'observed matching Production component values do not acknowledge an unknown original result',
   'observed matching Production plan values do not acknowledge an unknown original result',
   'silent Production data recovery failure blocks actual Project dispatch and preserves raw input',
   'silent Production pointer recovery failure blocks actual Project dispatch and preserves raw input',
   'a silent Production copy removal reports unconfirmed discard and keeps the original author bytes '
   'visible',
   'an unreadable own Production copy stays warned while typing; an explicit new copy preserves both '
   'buffers',
   'retrying a readable Production recovery retains its UNKNOWN alongside newer author input')),
 ('browser-creative-workbench',
  'creative-workbench-preservation.spec.ts',
  ('stage raw author input survives navigation, scene return, remount and reload without Project writes',
   'stage author input is isolated by id and restores on return',
   'stage author input is isolated by generation and restores on return',
   'stage author input is isolated by active_branch and restores on return',
   'stage incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'stage a reply without the submitted operation cannot clear UNKNOWN',
   'stage silent recovery data loss blocks dispatch and preserves author input',
   'stage matching observed props and a lost original reply remain UNKNOWN after reload',
   'procedural raw author input survives navigation, scene return, remount and reload without Project '
   'writes',
   'procedural author input is isolated by id and restores on return',
   'procedural author input is isolated by generation and restores on return',
   'procedural author input is isolated by active_branch and restores on return',
   'procedural incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'procedural a reply without the submitted operation cannot clear UNKNOWN',
   'procedural silent recovery data loss blocks dispatch and preserves author input',
   'procedural matching observed props and a lost original reply remain UNKNOWN after reload',
   'measured raw author input survives navigation, scene return, remount and reload without Project writes',
   'measured author input is isolated by id and restores on return',
   'measured author input is isolated by generation and restores on return',
   'measured author input is isolated by active_branch and restores on return',
   'measured incomplete numeric input remains exact and cannot dispatch a Project mutation',
   'measured a reply without the submitted operation cannot clear UNKNOWN',
   'measured silent recovery data loss blocks dispatch and preserves author input',
   'measured matching observed props and a lost original reply remain UNKNOWN after reload',
   'Measured table selection preserves each dataset and binding buffer without writes',
   'Measured binding original ACK preserves later Canvas selection and raw precision',
   'Measured pending file typing cannot publish rows, proof or an obsolete error',
   'Measured pending file asset cannot publish rows, proof or an obsolete error',
   'Measured pending file owner cannot publish rows, proof or an obsolete error',
   'Measured pending file unmount cannot publish rows, proof or an obsolete error',
   'Measured pending file late-error cannot publish rows, proof or an obsolete error',
   'Measured completed byte proof tolerates label edits but is never recovered as submit authority',
   'Measured pending file cannot resurrect a buffer after an explicit saved-value choice',
   'Measured pending file cannot replace an explicitly restored copy with the same edit sequence',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after missing reload',
   'Measured new-copy action cannot bypass a repaired UNKNOWN after corrupt reload',
   'ProductStage existing Hero eligibility blocks dispatch while separate scene drafts and valid '
   'authoring remain intact')))
BROWSER_SELECTIONS = (*HISTORICAL_BROWSER_SELECTIONS, *CREATIVE_PRODUCTION_BROWSER_SELECTIONS)
REVIEW_BROWSER_TITLES = ('causal: raw comment and incomplete range survive remount and reload',
 'causal: raw comment belongs to its exact id across A-B-A',
 'causal: raw comment belongs to its exact generation across A-B-A',
 'causal: raw comment belongs to its exact active_branch across A-B-A',
 "causal: scene targets do not adopt each other's raw review",
 'causal: original add acknowledgement preserves a later author edit',
 'causal: r+1 without the submitted review cannot acknowledge or erase the draft',
 'causal: resolution raw text survives workspace remount and reload',
 'causal: alternative rationale and direction survive workspace remount and reload',
 'causal: uncertain rejection preserves input and blocks automatic or manual replay',
 'causal: denied recovery storage blocks submission and retains current raw input',
 'control: a valid original add response clears only the submitted comment',
 'control: a matching observed Project alone leaves the original author input intact',
 'transition causal: inspected UNKNOWN retains its original copy before a separate later intent',
 'transition causal: inspected UNKNOWN retains its original copy before a separate blank review',
 'an exact resolution reply retains later raw reason through reopen and a distinct save',
 'an original alternative receipt retains a later raw editorial rationale',
 'resolution and alternative reasons remain isolated by generation across A-B-A',
 'scene and whole-project review buffers stay separate when the author changes scope',
 'silent bytes write loss blocks mutation until a verified retry keeps the raw input',
 'silent pointer write loss blocks mutation until a verified retry keeps the raw input',
 'a full recovery catalogue retains prior copies and permits faithful export of blocked raw input',
 'a late original response cannot erase another project or silently clear its original UNKNOWN',
 'a later saved revision requires explicit comparison while retaining its earlier raw copy',
 'native UTF-8 comment limits reject an unsaveable request without truncation or transport',
 'range toggle causal: fraction and incomplete bound survives resource and return',
 'range toggle causal: intentionally empty bounds survives resource and return',
 'range toggle causal: fraction and incomplete bound survives playhead and return',
 'range toggle causal: intentionally empty bounds survives playhead and return')
REVIEW_BROWSER_SELECTIONS = (
    ("browser-review", "review-preservation.spec.ts", REVIEW_BROWSER_TITLES),
)
# Explicit recovery checks remain separate from historical browser and service selections.
WORKFLOW_PREVIEW_UNITS = (('src/workflowActionDraft.test.ts',
  (('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps caller identity exact and rejects whitespace, controls and UTF-8 overflow'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'decodes the closed request without a branch or effect-grant field in its retained source'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'refuses lossy non-JSON arguments while leaving incomplete author input verbatim'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'retains original action, source, args and raw field sequences before dispatch'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'cannot dispatch until the original pending copy, pointer and final readback are confirmed'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects selection changes after permission was requested without changing original identity'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not let a saved or reconstructed invocation token acknowledge an original action'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'binds success to caller request, command, project, generation and original revision'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    "accepts the contract's arbitrary JSON authority value without widening the response shape"),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'requires the native success stage completed while keeping failure stages opaque'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps UNKNOWN for malformed or foreign failures, even with a truthy outcome flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'decodes only the pinned SDK error codes, with a boolean outcome flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not turn absent, unavailable or unchecked receipts into permission to send'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps a mismatched recorded failure UNKNOWN instead of borrowing another receipt'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'preserves a known failure of the original live invocation until an explicit local acknowledgment'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'reload cannot upgrade a prior unconfirmed action using a retained known-failure flag'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'only a local pre-invoke Error with this request id can establish NOT_SENT'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'keeps exact diagnostics instead of coercing structured native failures to Object text'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'acknowledges only the original Promise and preserves later edits field by field'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'retains a malformed success as UNKNOWN with all author input'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'uses a new writer on reload or duplicated tabs and never overwrites the inherited copy'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'fails closed on a missing or corrupt pointed copy and retains independent raw bytes'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'detects silent data writes and repairs only the original attempted copy without another identity'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'detects a silently dropped pointer and data corruption at the final readback'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'does not overwrite a changed copy or pointer and keeps later author text exportable'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'stores incomplete replay JSON and slug drafts separately for every candidate'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'merges imported UNKNOWN without dropping later local fields or executing a request'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'refuses importing a different original UNKNOWN over the current one'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'validates selected copy bytes again before restoring and refuses a changed source'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects extra draft fields and forged native grant data rather than retaining executable '
    'authority'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'checks the one-time grant source independently of its opaque token'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'shows a retained prior success as history while a different live action remains UNKNOWN'),
   ('Original workflow caller custody \u2014 synthetic DTOs only',
    'rejects sequence exhaustion without dropping input'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is silent'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is throw'),
   ('Imported workflow UNKNOWN custody',
    'keeps the imported original and later text when the first adoption write is write-then-throw'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with not_checked and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with not_checked as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with not_checked'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with not_observed and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with not_observed as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with not_observed'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with unavailable and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with unavailable as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with unavailable'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with dispatching and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with dispatching as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with dispatching'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with outcome_unknown and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with outcome_unknown as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with outcome_unknown'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with future-stage and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with future-stage as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with future-stage'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains UNKNOWN after a known rejection with completed and after reload'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'decodes a retained FAILED_KNOWN claim with completed as UNKNOWN'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'refuses to close a FAILED_KNOWN claim with completed'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'does not trust a failed_known receipt when the invocation itself is uncertain'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'retains the original bytes and blocks a legacy retired failure with an uncertain receipt'),
   ('A known invocation rejection cannot settle an uncertain original workflow',
    'does not decode or close NOT_SENT when a native failure contradicts that local claim'))),
 ('src/previewRecovery.test.ts',
  (('decoded preview recovery envelope',
    'preserves bytes independently and hashes scope, source revision and metadata as well as PNG'),
   ('decoded preview recovery envelope', 'rejects a different projectId and does not mutate its input'),
   ('decoded preview recovery envelope',
    'rejects a different generation and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different branchId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different sceneId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects a different profileId and does not mutate its input'),
   ('decoded preview recovery envelope', 'rejects future revisions and incomplete source metadata'),
   ('decoded preview recovery envelope', 'rejects changed pixels with an otherwise intact envelope'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in sceneName'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in sourceRevision'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in frameIndex'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in pixelWidth'),
   ('decoded preview recovery envelope', 'rejects metadata corruption in capturedAt'),
   ('decoded preview recovery envelope',
    'rejects authority fields instead of serializing a token or authorized path'),
   ('decoded preview recovery envelope',
    'bounds raw bytes, dimensions, frame count and numeric entry'))),
 ('src/programMaster.test.ts',
  (('source-bound Program AV monitor clock projection',
    'maps current visual and audio masters to both eligible source scenes'),
   ('source-bound Program AV monitor clock projection',
    'rejects stale revisions, changed output profiles, unknown tokens and unrelated renderers'),
   ('source-bound Program AV monitor clock projection',
    'rejects silently reinterpreting a different frame rate, scene cut or master length'),
   ('source-bound Program AV monitor clock projection',
    'maps explicitly selected noncontiguous cuts onto authored project offsets'))))
WORKFLOW_PREVIEW_BROWSER_SELECTIONS = (('browser-workflow-recovery',
  'workflow-action-recovery.spec.ts',
  ('raw recorder/compiler and separate candidate inputs survive navigation and reload without dispatch',
   'the actual API sends the retained caller id and exact original args after a confirmed backup',
   'incomplete replay JSON remains raw and cannot request a grant or invoke a workflow',
   'native structured UNKNOWN diagnostics and original identity survive refresh, export and reload',
   'a not-observed receipt cannot clear UNKNOWN or manufacture a retry route',
   'reload while the reply is lost keeps the same operation with no automatic grant or dispatch',
   'a success for another generation cannot acknowledge the retained action',
   'an old unversioned success DTO remains unconfirmed instead of being treated as current authority',
   'a non-completed receipt cannot acknowledge a live action even when every source identity matches',
   'a known failure can be acknowledged explicitly without sending or losing the draft',
   'a retained known-failure flag on reload does not resolve a previous UNKNOWN automatically',
   'permission rejection before invoke is a local NOT_SENT result with the original request preserved',
   'revision drift during permission prevents dispatch without rewriting the original source stamp',
   'leaving the editor while permission is pending prevents a late dispatch',
   "a late reply settles only its original owner and does not replace another generation's input",
   'author changes made while a compile is pending survive acknowledgment and reload',
   'same-turn duplicate clicks create one original request and one grant',
   'a silently dropped pending backup invokes neither permission nor workflow and keeps raw input '
   'exportable',
   'final-copy corruption after pointer write cannot authorize dispatch or be overwritten by repair',
   'importing an original UNKNOWN into another tab merges input and never dispatches it',
   'a corrupt pointed recovery copy stays exportable and blocks new action identity',
   'an import finishing during an original invocation reports that it was not applied and preserves '
   'custody',
   'plan-only uses the same original caller protocol without a mutation grant',
   'a known rejection with not_checked cannot release the original workflow action',
   'a known rejection with not_observed cannot release the original workflow action',
   'a known rejection with unavailable cannot release the original workflow action',
   'a known rejection with dispatching cannot release the original workflow action',
   'a known rejection with outcome_unknown cannot release the original workflow action',
   'a known rejection with future-stage cannot release the original workflow action',
   'a known rejection with completed cannot release the original workflow action',
   'an imported UNKNOWN survives a silent first adoption write and later author edits',
   'an imported UNKNOWN survives a throw first adoption write and later author edits',
   'an imported UNKNOWN survives a write-then-throw first adoption write and later author edits')),
 ('browser-preview-recovery',
  'preview-recovery.spec.ts',
  ('revision changes retain the actually decoded previous PNG and label it not current',
   'a real browser reload restores the stored Blob without reusing a native token',
   'same-revision reopen remains historical and workspace navigation keeps the copy',
   'a pending native read and concurrent revision change preserve the old PNG through disconnect and '
   'reopen',
   "another id cannot display or delete the old scope's preview",
   "another generation cannot display or delete the old scope's preview",
   "another active_branch cannot display or delete the old scope's preview",
   'profile and scene selection never borrow retained pixels from another scope',
   'a late native response after selection changed never becomes a recovery copy',
   'PNG-shaped bytes that the browser cannot decode are never retained',
   'a quota fault preserves the earlier durable copy and keeps the new image recoverably honest',
   'retrying a failed recovery write confirms the latest decoded frame and preserves a peer',
   'corrupt pixels fail readback, preserve raw rows and restore an earlier valid copy',
   'a damaged own copy stays until explicit removal and a new decoded preview can be retained',
   'a late persistence acknowledgement cannot replace a newer decoded image',
   'a duplicated writer with the same timestamp cannot lose its concurrent immutable copy',
   'the real record bound refuses a new copy without deleting other writers',
   'an explicit AV choice and its in-flight component survive a scene boundary within the same master',
   'an explicit AV choice suspends outside its cut without reads and resumes on return',
   'AV authority id A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority generation A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority active_branch A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority revision A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority profile A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority frameToken A-to-B-to-A never resurrects a previous explicit choice',
   'AV authority exportToken A-to-B-to-A never resurrects a previous explicit choice',
   "a late frame from the former scene never becomes the current scene's recovery copy")))
WORKFLOW_PREVIEW_SOURCE_PATHS = ('Cargo.lock',
 'apps/desktop/src-tauri/Cargo.toml',
 'apps/desktop/src-tauri/src/main.rs',
 'apps/desktop/src-tauri/src/workflow_native.rs',
 'apps/studio/src/App.tsx',
 'apps/studio/src/NativeFrameStage.tsx',
 'apps/studio/src/RetainedPreviewFrame.tsx',
 'apps/studio/src/WorkflowActionRecovery.tsx',
 'apps/studio/src/WorkflowWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/previewRecovery.test.ts',
 'apps/studio/src/previewRecovery.ts',
 'apps/studio/src/programMaster.test.ts',
 'apps/studio/src/programMaster.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/usePreviewRecovery.ts',
 'apps/studio/src/workflowActionDraft.test.ts',
 'apps/studio/src/workflowActionDraft.ts',
 'apps/studio/tests/fixtures/preview-recovery.html',
 'apps/studio/tests/fixtures/preview-recovery.tsx',
 'apps/studio/tests/fixtures/workflow-action-recovery.html',
 'apps/studio/tests/fixtures/workflow-action-recovery.tsx',
 'apps/studio/tests/preview-recovery.spec.ts',
 'apps/studio/tests/program-av-monitor.spec.ts',
 'apps/studio/tests/workflow-action-recovery.spec.ts',
 'crates/native/src/production.rs',
 'crates/native/tests/desktop_workflow_route.rs',
 'tooling/tests/test_ci_canvas_service.py')
WORKFLOW_PREVIEW_SOURCE_PATHS += ("apps/desktop/src-tauri/src/effect_grants.rs",
                                  "crates/native/Cargo.toml", "Cargo.toml")
BROWSER_DESCRIBE_TITLES.update({('workflow-action-recovery.spec.ts', 'raw recorder/compiler and separate candidate inputs survive navigation and reload without dispatch'): (),
 ('workflow-action-recovery.spec.ts', 'the actual API sends the retained caller id and exact original args after a confirmed backup'): (),
 ('workflow-action-recovery.spec.ts', 'incomplete replay JSON remains raw and cannot request a grant or invoke a workflow'): (),
 ('workflow-action-recovery.spec.ts', 'native structured UNKNOWN diagnostics and original identity survive refresh, export and reload'): (),
 ('workflow-action-recovery.spec.ts', 'a not-observed receipt cannot clear UNKNOWN or manufacture a retry route'): (),
 ('workflow-action-recovery.spec.ts', 'reload while the reply is lost keeps the same operation with no automatic grant or dispatch'): (),
 ('workflow-action-recovery.spec.ts', 'a success for another generation cannot acknowledge the retained action'): (),
 ('workflow-action-recovery.spec.ts', 'an old unversioned success DTO remains unconfirmed instead of being treated as current authority'): (),
 ('workflow-action-recovery.spec.ts', 'a non-completed receipt cannot acknowledge a live action even when every source identity matches'): (),
 ('workflow-action-recovery.spec.ts', 'a known failure can be acknowledged explicitly without sending or losing the draft'): (),
 ('workflow-action-recovery.spec.ts', 'a retained known-failure flag on reload does not resolve a previous UNKNOWN automatically'): (),
 ('workflow-action-recovery.spec.ts', 'permission rejection before invoke is a local NOT_SENT result with the original request preserved'): (),
 ('workflow-action-recovery.spec.ts', 'revision drift during permission prevents dispatch without rewriting the original source stamp'): (),
 ('workflow-action-recovery.spec.ts', 'leaving the editor while permission is pending prevents a late dispatch'): (),
 ('workflow-action-recovery.spec.ts', "a late reply settles only its original owner and does not replace another generation's input"): (),
 ('workflow-action-recovery.spec.ts', 'author changes made while a compile is pending survive acknowledgment and reload'): (),
 ('workflow-action-recovery.spec.ts', 'same-turn duplicate clicks create one original request and one grant'): (),
 ('workflow-action-recovery.spec.ts', 'a silently dropped pending backup invokes neither permission nor workflow and keeps raw input exportable'): (),
 ('workflow-action-recovery.spec.ts', 'final-copy corruption after pointer write cannot authorize dispatch or be overwritten by repair'): (),
 ('workflow-action-recovery.spec.ts', 'importing an original UNKNOWN into another tab merges input and never dispatches it'): (),
 ('workflow-action-recovery.spec.ts', 'a corrupt pointed recovery copy stays exportable and blocks new action identity'): (),
 ('workflow-action-recovery.spec.ts', 'an import finishing during an original invocation reports that it was not applied and preserves custody'): (),
 ('workflow-action-recovery.spec.ts', 'plan-only uses the same original caller protocol without a mutation grant'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with not_checked cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with not_observed cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with unavailable cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with dispatching cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with outcome_unknown cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with future-stage cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'a known rejection with completed cannot release the original workflow action'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a silent first adoption write and later author edits'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a throw first adoption write and later author edits'): (),
 ('workflow-action-recovery.spec.ts', 'an imported UNKNOWN survives a write-then-throw first adoption write and later author edits'): (),
 ('preview-recovery.spec.ts', 'revision changes retain the actually decoded previous PNG and label it not current'): (),
 ('preview-recovery.spec.ts', 'a real browser reload restores the stored Blob without reusing a native token'): (),
 ('preview-recovery.spec.ts', 'same-revision reopen remains historical and workspace navigation keeps the copy'): (),
 ('preview-recovery.spec.ts', 'a pending native read and concurrent revision change preserve the old PNG through disconnect and reopen'): (),
 ('preview-recovery.spec.ts', "another id cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', "another generation cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', "another active_branch cannot display or delete the old scope's preview"): (),
 ('preview-recovery.spec.ts', 'profile and scene selection never borrow retained pixels from another scope'): (),
 ('preview-recovery.spec.ts', 'a late native response after selection changed never becomes a recovery copy'): (),
 ('preview-recovery.spec.ts', 'PNG-shaped bytes that the browser cannot decode are never retained'): (),
 ('preview-recovery.spec.ts', 'a quota fault preserves the earlier durable copy and keeps the new image recoverably honest'): (),
 ('preview-recovery.spec.ts', 'retrying a failed recovery write confirms the latest decoded frame and preserves a peer'): (),
 ('preview-recovery.spec.ts', 'corrupt pixels fail readback, preserve raw rows and restore an earlier valid copy'): (),
 ('preview-recovery.spec.ts', 'a damaged own copy stays until explicit removal and a new decoded preview can be retained'): (),
 ('preview-recovery.spec.ts', 'a late persistence acknowledgement cannot replace a newer decoded image'): (),
 ('preview-recovery.spec.ts', 'a duplicated writer with the same timestamp cannot lose its concurrent immutable copy'): (),
 ('preview-recovery.spec.ts', 'the real record bound refuses a new copy without deleting other writers'): (),
 ('preview-recovery.spec.ts', 'an explicit AV choice and its in-flight component survive a scene boundary within the same master'): (),
 ('preview-recovery.spec.ts', 'an explicit AV choice suspends outside its cut without reads and resumes on return'): (),
 ('preview-recovery.spec.ts', 'AV authority id A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority generation A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority active_branch A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority revision A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority profile A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority frameToken A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', 'AV authority exportToken A-to-B-to-A never resurrects a previous explicit choice'): (),
 ('preview-recovery.spec.ts', "a late frame from the former scene never becomes the current scene's recovery copy"): (),
 ('program-av-monitor.spec.ts', 'Program AV monitor reads exact source token only for matching scene, profile and revision'): (),
 ('program-av-monitor.spec.ts', 'browser demo cannot invent final AV monitoring from a semantic project'): ()})

BROWSER_SCOPES = {
    "browser-contracts": tuple(row[0] for row in BROWSER_SELECTIONS),
    "creative-production-browser": ("browser-production-design", "browser-creative-workbench"),
    "review-browser": ("browser-review",),
    "workflow-preview-browser": ("browser-program-av-monitor", "browser-workflow-recovery", "browser-preview-recovery"),
}
CREATIVE_PRODUCTION_SOURCE_PATHS = ('apps/studio/src/App.tsx',
 'apps/studio/src/CompositionStudy.tsx',
 'apps/studio/src/MeasuredDataWorkbench.tsx',
 'apps/studio/src/ProceduralFieldWorkbench.tsx',
 'apps/studio/src/ProductStageWorkbench.tsx',
 'apps/studio/src/ProductionDesignWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/brandGovernance.ts',
 'apps/studio/src/cameraReframe.ts',
 'apps/studio/src/canvasInheritedInk.ts',
 'apps/studio/src/captureSource.ts',
 'apps/studio/src/componentDistillation.ts',
 'apps/studio/src/creativeDoctor.ts',
 'apps/studio/src/creativeEvaluator.ts',
 'apps/studio/src/creativeProduction.ts',
 'apps/studio/src/creativeUndo.ts',
 'apps/studio/src/creativeWorkbenchDraft.test.ts',
 'apps/studio/src/creativeWorkbenchDraft.ts',
 'apps/studio/src/deliveryRecipe.ts',
 'apps/studio/src/deliveryRecipeDraft.ts',
 'apps/studio/src/fileImport.ts',
 'apps/studio/src/fixture.ts',
 'apps/studio/src/generationJournalApi.ts',
 'apps/studio/src/identityOrigin.ts',
 'apps/studio/src/measuredData.ts',
 'apps/studio/src/musicClock.ts',
 'apps/studio/src/narrationReviewApi.ts',
 'apps/studio/src/proceduralField.ts',
 'apps/studio/src/productStage.ts',
 'apps/studio/src/productionDesignDraft.test.ts',
 'apps/studio/src/productionDesignDraft.ts',
 'apps/studio/src/projectLibrary.ts',
 'apps/studio/src/projectMutation.ts',
 'apps/studio/src/semanticSfx.ts',
 'apps/studio/src/stageCapture.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useCreativeWorkbenchDraft.tsx',
 'apps/studio/tests/creative-workbench-preservation.spec.ts',
 'apps/studio/tests/creative-workbench-service.spec.ts',
 'apps/studio/tests/fixtures/creative-workbench-authority.html',
 'apps/studio/tests/fixtures/creative-workbench-authority.tsx',
 'apps/studio/tests/fixtures/production-design-authority.html',
 'apps/studio/tests/fixtures/production-design-authority.tsx',
 'apps/studio/tests/production-design-preservation.spec.ts',
 'apps/studio/tests/production-design-service.spec.ts',
 'apps/studio/tests/support/scopedChangeService.ts')

BROWSER_SELECTION_TIMEOUTS = {label: 180 + 30 * len(titles) for label, _, titles in BROWSER_SELECTIONS}
# Retain the Workbench budget, including startup and every per-case bound.
BROWSER_SELECTION_TIMEOUTS["browser-creative-workbench"] = 1260
BROWSER_SELECTION_TIMEOUTS["browser-review"] = 1050
BROWSER_SELECTION_TIMEOUTS.update({label: 180 + 30 * len(titles)
                                   for label, _, titles in WORKFLOW_PREVIEW_BROWSER_SELECTIONS})


CREATIVE_PRODUCTION_UNITS = (('src/productionDesignDraft.test.ts',
  (('Production author input and original response authority',
    'scopes component and plan input by project, generation, branch and component scene'),
   ('Production author input and original response authority',
    'retains exact plan text, empty lines, reviewer, source references and clock without cleaning author '
    'input'),
   ('Production author input and original response authority',
    'an observed identical hero value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'an observed identical plan value at r+1 never consumes its pending request'),
   ('Production author input and original response authority',
    'the original hero reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'the original plan reply preserves later raw input including a return to the old value'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong id without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong generation without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong active_branch without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong revision without consuming raw input'),
   ('Production author input and original response authority',
    'rejects an original response with the wrong value without consuming raw input'),
   ('Production author input and original response authority',
    'treats an unrelated observed revision as a reviewable conflict rather than silently rebasing authored '
    'intent'),
   ('Production author input and original response authority',
    'invalidates a prepared digest when input, owner, revision, pending attempt, or source target changes'),
   ('Production author input and original response authority',
    'cleans only a plan submission and binds approval to that exact content while retaining author bytes'),
   ('Production author input and original response authority',
    'keeps editable scene objects on detach and rejects a receipt that changes them'),
   ('Production author input and original response authority',
    'will not dispatch plan or component submissions through content locks'),
   ('Production durable custody',
    'recovers NOT_SENT conservatively as UNKNOWN and exports complete raw input without write authority'),
   ('Production durable custody',
    'rejects foreign scope, writer and authority fields without altering any stored bytes'),
   ('Production durable custody',
    'requires confirmed data storage before treating a Production backup as durable'),
   ('Production durable custody',
    'requires confirmed pointer storage before treating a Production backup as durable'),
   ('Production durable custody',
    'rechecks data custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rechecks pointer custody after budget enumeration before replacing any author bytes'),
   ('Production durable custody',
    'rereads the data after pointer publication and never adopts the interleaved replacement'),
   ('Production durable custody', 'retains corrupt copies and refuses to mutate a clean checkpoint'),
   ('Production durable custody',
    "refuses a full recovery budget without deleting older copies or the live author's input"),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'reads two current snapshots and complete history; matching values remain UNKNOWN until explicit '
    'separate intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'rejects truncated history and a project that changes during inspection'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'retains the old UNKNOWN through a verified two-phase new writer before releasing a separate intent'),
   ('Production UNKNOWN review without inferred acknowledgement or replay',
    'a failed second fork phase leaves a recoverable UNKNOWN and the earlier author bytes intact'))),
 ('src/creativeWorkbenchDraft.test.ts',
  (('creative workbench raw input and original response authority',
    'keeps raw integer grammar distinct from numeric coercion'),
   ('creative workbench raw input and original response authority',
    'scopes all workbenches by project, generation, branch, scene and editor kind'),
   ('creative workbench raw input and original response authority',
    'stage observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'procedural observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'measured observed matching values never acknowledge an original pending attempt'),
   ('creative workbench raw input and original response authority',
    'stage the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'procedural the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'measured the original ACK preserves later raw input and independent table buffers'),
   ('creative workbench raw input and original response authority',
    'stage raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'procedural raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'measured raw copy roundtrip does not carry execution grants or byte verification'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong id without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong generation without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong active_branch without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong revision without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'rejects the original ACK with wrong value without consuming UNKNOWN'),
   ('creative workbench raw input and original response authority',
    'stage detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'procedural detach requires the original receipt to preserve every editable node'),
   ('creative workbench raw input and original response authority',
    'an external revision needs an explicit base choice and never clears pending intent'),
   ('creative workbench raw input and original response authority',
    'invalidates asynchronous preparation when owner, revision, input or attempt changes'),
   ('creative workbench raw input and original response authority',
    'Measured keeps exact independent table, node, precision and axis buffers when selecting another '
    'table'),
   ('creative workbench raw input and original response authority',
    'Measured ACK for one table cannot mark a different hidden author buffer as saved'),
   ('creative workbench raw input and original response authority',
    'Measured reviewed new alias cannot overwrite a separately authored buffer at its saved table ID'),
   ('creative workbench raw input and original response authority',
    'Measured exact byte proof is ephemeral, tolerates label changes and rejects another source or '
    'recovered authority'),
   ('creative workbench raw input and original response authority',
    'Measured binding ACK checks the original table, datum, view and projected Canvas value'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified data readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires verified pointer readback before a recovery write succeeds'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks data custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rechecks pointer custody after the recovery budget scan'),
   ('creative workbench verified custody and uncertain outcome review',
    'rereads data after pointer publication and rejects an interleaved replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'preserves corrupt bytes, rejects foreign scope and refuses clean checkpoint replacement'),
   ('creative workbench verified custody and uncertain outcome review',
    'a full recovery budget blocks saving without deleting any previous author bytes'),
   ('creative workbench verified custody and uncertain outcome review',
    'recovering a NOT_SENT checkpoint conservatively retains UNKNOWN and rejects extra authority fields'),
   ('creative workbench verified custody and uncertain outcome review',
    'requires fresh read-history-read and retains matching values as comparison rather than ACK'),
   ('creative workbench verified custody and uncertain outcome review',
    'rejects incomplete history and a project changed during outcome inspection'),
   ('creative workbench verified custody and uncertain outcome review',
    'a separate reviewed intent verifies both records and preserves the exact earlier UNKNOWN'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects an existing Hero collision before arming an attempt and retains the exact author input'),
   ('ProductStage existing project eligibility before mutation dispatch',
    'rejects creation at the existing sixteen-Stage limit while a bound Stage can still update'))))


REVIEW_UNITS = (('src/reviewDraft.test.ts',
  (('exact review owner and raw author input', 'separates equal raw drafts under a different id'),
   ('exact review owner and raw author input',
    'separates equal raw drafts under a different generation'),
   ('exact review owner and raw author input',
    'separates equal raw drafts under a different active_branch'),
   ('exact review owner and raw author input',
    'separates comment, resolution and alternative targets'),
   ('exact review owner and raw author input',
    'preserves whitespace and incomplete input without numeric coercion'),
   ('exact review owner and raw author input', 'uses exact fractions for an authored time range'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "1e" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "1001/" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "0x1" remains unsaveable and unchanged'),
   ('exact review owner and raw author input',
    'incomplete or invalid time "Infinity" remains unsaveable and unchanged'),
   ('exact review owner and raw author input', 'range -1 to 1 cannot leave its selected scene'),
   ('exact review owner and raw author input', 'range 2 to 1 cannot leave its selected scene'),
   ('exact review owner and raw author input', 'range 0 to 8 cannot leave its selected scene'),
   ('exact review owner and raw author input',
    'clean observations update only the base while active raw intent retains its source'),
   ('original callback acknowledgement and uncertainty',
    'a valid original add receipt clears only its unchanged submitted comment'),
   ('original callback acknowledgement and uncertainty',
    'an equal observed Project cannot acknowledge or clear the original pending input'),
   ('original callback acknowledgement and uncertainty',
    'an unrelated callback identity cannot acknowledge this original request'),
   ('original callback acknowledgement and uncertainty',
    'a later body edit survives the exact original acknowledgement'),
   ('original callback acknowledgement and uncertainty',
    'a later rangeStart edit survives the exact original acknowledgement'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another id retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another generation retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'an original reply for another active_branch retains UNKNOWN'),
   ('original callback acknowledgement and uncertainty',
    'revision offset 0 cannot replace the exact original receipt'),
   ('original callback acknowledgement and uncertainty',
    'revision offset 2 cannot replace the exact original receipt'),
   ('original callback acknowledgement and uncertainty',
    'a missing review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a body review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a anchor review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'a duplicate review mismatch cannot authorize clearing'),
   ('original callback acknowledgement and uncertainty',
    'uncertain failure and reload preserve the original attempt and prohibit replay'),
   ('original callback acknowledgement and uncertainty',
    'a proven unsent storage failure can be submitted only through a new explicit attempt'),
   ('original callback acknowledgement and uncertainty',
    'resolution acknowledgement checks the exact item, anchor and authored reason'),
   ('original callback acknowledgement and uncertainty',
    'a later resolution survives both resolution and explicit reopen receipts'),
   ('original callback acknowledgement and uncertainty',
    'an authored draft cannot silently move to a later source or missing scene'),
   ('original callback acknowledgement and uncertainty',
    'the request must preserve the exact raw range and resource binding'),
   ('verified recovery custody and bounded storage',
    'a write is accepted only with exact bytes and a matching tab pointer readback'),
   ('verified recovery custody and bounded storage',
    'a cloned tab forks its writer and never rewrites the earlier copy'),
   ('verified recovery custody and bounded storage',
    'a peer byte change is preserved and blocks an overwrite'),
   ('verified recovery custody and bounded storage',
    'a peer pointer change is preserved and blocks an overwrite'),
   ('verified recovery custody and bounded storage',
    'silent write loss cannot count as confirmed custody'),
   ('verified recovery custody and bounded storage',
    'silent pointer loss keeps the written copy but cannot acknowledge custody'),
   ('verified recovery custody and bounded storage',
    'the copy-count budget blocks a new write without eviction or author loss'),
   ('verified recovery custody and bounded storage',
    'the byte budget blocks a new write without rewriting earlier recovery'),
   ('verified recovery custody and bounded storage',
    'oversized raw input remains in memory when a recovery record cannot be encoded'),
   ('verified recovery custody and bounded storage',
    'a clean acknowledged checkpoint cannot be rewritten by a later intent'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign schema recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign scope recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign writer recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign shape recovery is rejected without normalization'),
   ('verified recovery custody and bounded storage',
    'a corrupt or foreign attempt recovery is rejected without normalization'),
   ('explicit UNKNOWN inspection and separate author intent',
    'two exact source reads and contiguous history are observations, never original '
    'acknowledgement'),
   ('explicit UNKNOWN inspection and separate author intent',
    'unchanged submitted text starts blank after explicit inspection rather than replaying '
    'add_review'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a later raw intent uses a new attempt identity without consuming or changing the original '
    'UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA body input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA kind input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA timeMode input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA rangeStart input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'later ABA rangeEnd input survives explicit UNKNOWN separation'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a later ABA resolution reason survives separate intent after actual-source inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a gap journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a empty journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a duplicate journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a beyond journal cannot release UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a changed second source read invalidates the inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a foreign identity or a different pending attempt cannot reuse the inspection'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a zero-event inspection of unchanged saved state still retains UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a separate writer retains the original exact UNKNOWN bytes and verifies the new pointer'),
   ('explicit UNKNOWN inspection and separate author intent',
    'failure after staging the new writer leaves an UNKNOWN checkpoint and preserves every '
    'original byte'),
   ('explicit UNKNOWN inspection and separate author intent',
    'verifying identical retained input never rewrites its original timestamp or bytes'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a known unsent draft can be explicitly reviewed against a newer source without weakening '
    'UNKNOWN'),
   ('explicit UNKNOWN inspection and separate author intent',
    'native UTF-8 text budgets are checked before invoking a request without truncating raw input'),
   ('explicit UNKNOWN inspection and separate author intent',
    'a dismissed review cannot become a submitted resolution attempt'))),)




# Separate frontend-only cohort; no native, service or release acceptance.
WINDOW_FRONTEND_SCOPE = 'window-frontend'
WINDOW_FRONTEND_UNITS = (('src/nativeWindowPreview.test.ts',
  ('native Window authority uses source frames and the original bounded response keeps original source, '
   'reordered cut, realized segment and token-local clocks distinct',
   'native Window authority uses source frames and the original bounded response keeps cut time exact across '
   "another renderer without deriving that renderer's duration as segment offset",
   'native Window authority uses source frames and the original bounded response uses exact rational scene '
   'boundaries at 30000/1001 and never rounds a fractional source boundary',
   'native Window authority uses source frames and the original bounded response retains separate source, '
   'selected-count and prefix limits, including a late source in a short cut',
   'native Window authority uses source frames and the original bounded response invalidates old scope, '
   'selected profile, scene and intent while owning copies of the original request and reply',
   'native Window authority uses source frames and the original bounded response rejects foreign, unbounded '
   'or promoted replies without manufacturing a Full receipt',
   'native Window authority uses source frames and the original bounded response preserves an observed PASS '
   'or stored receipt replay while keeping Window authority preview-only',
   'native Window authority uses source frames and the original bounded response requires explicit warm '
   'identity and the same original token, with no cold operation hidden in reuse',
   'native Window authority uses source frames and the original bounded response retains independent '
   'unresolved attempts and refuses peer races, unconfirmed writes or changed original bytes')),
 ('src/nativeFrameSelection.test.ts',
  ('native frame selection follows authored scene timing maps the selected scene and next scene into the '
   'same canonical segment',
   'native frame selection follows authored scene timing fails closed on stale revision, profile mismatch, '
   'missing or forged grants',
   'native frame selection follows authored scene timing uses exact render fps instead of assuming 24 or '
   'display NDF rounding')),
 ('src/nativeProgressivePreview.test.ts',
  ('v0.5 progressive source-verified native preview request policy distinguishes sparse scrub frames from '
   'exact settled source frames',
   'v0.5 progressive source-verified native preview request policy limits active scrub to one read start per '
   'quarter second',
   'v0.5 progressive source-verified native preview request policy waits 120ms after the exact paused '
   'playhead target settles',
   'v0.5 progressive source-verified native preview request policy does not request a frame already '
   'presented as current',
   'v0.5 progressive source-verified native preview request policy does not initiate a second native read '
   'while another is outstanding',
   'v0.5 progressive source-verified native preview request policy never automatically retries an '
   'unsuccessful source frame',
   'v0.5 progressive source-verified native preview request policy uses a source-scoped cached frame instead '
   'of issuing another grant readback',
   'v0.5 progressive source-verified native preview request policy never uses cache as proof if the precise '
   'frame is already visible',
   'v0.5 progressive source-verified native preview request policy rejects an invalid frame index or '
   'reversed clock',
   'source-lifetime bounded native PNG byte cache has explicit small frame and byte budgets',
   'source-lifetime bounded native PNG byte cache accepts real-sized frames only within a bounded lifetime',
   'source-lifetime bounded native PNG byte cache evicts the oldest frame when a fourth verified image '
   'arrives',
   'source-lifetime bounded native PNG byte cache reads update LRU precedence before eviction',
   'source-lifetime bounded native PNG byte cache evicts by total byte budget without unbounded renderer '
   'media accumulation',
   'source-lifetime bounded native PNG byte cache rejects empty, invalid indexes and oversized media',
   'source-lifetime bounded native PNG byte cache owns copies of source bytes so callers cannot mutate an '
   'admitted cache entry',
   'source-lifetime bounded native PNG byte cache supports replacement and scoped removal with exact byte '
   'accounting',
   'native pixels stay from the actual checked PNG data requires the exact PNG signature without '
   'synthesizing a frame',
   'native pixels stay from the actual checked PNG data rejects too short or unbounded PNG containers')),
 ('src/previewRecovery.test.ts',
  ('decoded preview recovery envelope preserves bytes independently and hashes scope, source revision and '
   'metadata as well as PNG',
   'decoded preview recovery envelope rejects a different projectId and does not mutate its input',
   'decoded preview recovery envelope rejects a different generation and does not mutate its input',
   'decoded preview recovery envelope rejects a different branchId and does not mutate its input',
   'decoded preview recovery envelope rejects a different sceneId and does not mutate its input',
   'decoded preview recovery envelope rejects a different profileId and does not mutate its input',
   'decoded preview recovery envelope rejects future revisions and incomplete source metadata',
   'decoded preview recovery envelope rejects changed pixels with an otherwise intact envelope',
   'decoded preview recovery envelope rejects metadata corruption in sceneName',
   'decoded preview recovery envelope rejects metadata corruption in sourceRevision',
   'decoded preview recovery envelope rejects metadata corruption in frameIndex',
   'decoded preview recovery envelope rejects metadata corruption in pixelWidth',
   'decoded preview recovery envelope rejects metadata corruption in capturedAt',
   'decoded preview recovery envelope rejects authority fields instead of serializing a token or authorized '
   'path',
   'decoded preview recovery envelope bounds raw bytes, dimensions, frame count and numeric entry')),
 ('src/renderReceipt.test.ts',
  ('scene-bound native render receipt metadata exposes a current receipt only for the same project, '
   'generation and profile',
   "scene-bound native render receipt metadata does not turn another delivery profile into this program's "
   'frame evidence',
   'scene-bound native render receipt metadata preserves original revision and marks receipts stale after a '
   'creative edit',
   'scene-bound native render receipt metadata does not attach another project or generation, another scene, '
   'or another renderer',
   'scene-bound native render receipt metadata fails closed on unlinked or missing job receipts')),
 ('src/programMaster.test.ts',
  ('source-bound Program AV monitor clock projection maps current visual and audio masters to both eligible '
   'source scenes',
   'source-bound Program AV monitor clock projection rejects stale revisions, changed output profiles, '
   'unknown tokens and unrelated renderers',
   'source-bound Program AV monitor clock projection rejects silently reinterpreting a different frame rate, '
   'scene cut or master length',
   'source-bound Program AV monitor clock projection maps explicitly selected noncontiguous cuts onto '
   'authored project offsets')))
WINDOW_FRONTEND_UNITS_SHA256 = 'd4f8fb34a69988e4a87990656737d6ead1bf7054f98b47da2e6df2725c2e24a1'
WINDOW_DELIVERY_UNIT_FULL_NAMES = ('Film render keeps otherwise-ready admission blocked until the own copy is actually read',
 'Film render local admission dispatches once with the original source, request and grant when the gesture '
 'stays current',
 'Film render local admission preserves an original failure after dispatch instead of relabeling it as local '
 'cancellation or retrying',
 'Film render local admission refuses pending input before requesting any effect grant',
 'Film render local admission rereads the same gesture after a deferred grant and never dispatches changed '
 'input',
 'explicit local cut mode accepts old array-only recovery copies with their existing full/custom meanings',
 'explicit local cut mode does not inherit a peer custom scene list over a locally empty cut from full '
 'sequence',
 'explicit local cut mode does not let a peer full-sequence revision reinterpret a pinned empty custom cut',
 'explicit local cut mode keeps an empty edit made after a custom selection was sent through the matching '
 'ACK',
 'explicit local cut mode requires an explicit full choice without dropping unrelated raw input',
 'explicit local cut mode retains a custom empty selection separately from the canonical full sequence',
 'explicit local cut mode reviews the mode and scene list together and never adds local mode to the '
 'canonical wire',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: changed-final',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: changed-initial',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: empty-history',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: gap',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: invalid-profile',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: other-owner',
 'explicit saved profile and journal inspection does not admit a separate intent from unverified source or '
 'history: oversized-history',
 'explicit saved profile and journal inspection legacy same-spelling causal: retains later A-B-A input when '
 'the historical capture has no original edit versions',
 'explicit saved profile and journal inspection permits legacy UNKNOWN inspection while preserving its old '
 'raw and later unsent spelling',
 'explicit saved profile and journal inspection rejects a previously inspected capture after its scope or '
 'pending identity changes',
 'explicit saved profile and journal inspection reports a matching saved value without acknowledging or '
 'mutating the retained original',
 'frame rate property order acknowledges all submitted fields from Rust JSON while preserving later '
 'frame-rate and incomplete input',
 'frame rate property order keeps distinct i64 values exact rather than comparing rounded numbers or equal '
 'ratios',
 'frame rate property order projects Rust JSON property order without changing the received exact integers',
 'frame rate property order recovers historical den-num bases and pending targets without rewriting their '
 'original bytes',
 'frame rate property order rejects malformed saved or pending rate {"den":"0","num":"30"}',
 'frame rate property order rejects malformed saved or pending rate {"den":"1","num":"0"}',
 'frame rate property order rejects malformed saved or pending rate {"den":"1","num":"030"}',
 'frame rate property order rejects malformed saved or pending rate {"den":"1","num":"241"}',
 'frame rate property order rejects malformed saved or pending rate {"den":"1","num":"30","approved":true}',
 'frame rate property order rejects malformed saved or pending rate {"den":"1","num":30}',
 'frame rate property order rejects malformed saved or pending rate {"den":"9223372036854775808","num":"30"}',
 'frame rate property order rejects malformed saved or pending rate {"den":1,"num":"30"}',
 'frame rate property order rejects malformed saved or pending rate {"num":"24","num":"30","den":"1"}',
 'frame rate property order rejects malformed saved or pending rate {"num":"30","den":"1","den":"1"}',
 'frame rate property order rejects malformed saved or pending rate {"num":"30"}',
 'multi-segment master keeps otherwise-ready admission blocked until the own copy is actually read',
 'multi-segment master local admission dispatches once with the original source, request and grant when the '
 'gesture stays current',
 'multi-segment master local admission preserves an original failure after dispatch instead of relabeling it '
 'as local cancellation or retrying',
 'multi-segment master local admission refuses pending input before requesting any effect grant',
 'multi-segment master local admission rereads the same gesture after a deferred grant and never dispatches '
 'changed input',
 'original profile authority causal regressions causal: explicit field resolution cannot consume an UNKNOWN '
 'attempt',
 'original profile authority causal regressions causal: matching incoming values cannot acknowledge the '
 'original attempt',
 'original profile reply authority cannot acknowledge another original capture ID even when scope, base and '
 'profile match',
 'original profile reply authority keeps an original receipt UNKNOWN when the retained comparison source is '
 'already newer',
 'original profile reply authority rejects a modified persisted original capture: capture-id',
 'original profile reply authority rejects a modified persisted original capture: capture-profile',
 'original profile reply authority rejects a modified persisted original capture: capture-version',
 'original profile reply authority rejects a modified persisted original capture: unknown-member',
 'original profile reply authority retains an A-B-A spelling edit and unrelated incomplete fields on the '
 'original acknowledgement',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with branch',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with '
 'generation',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with '
 'late-revision',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with '
 'missing-profile',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with '
 'old-revision',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with project',
 'original profile reply authority retains the complete UNKNOWN capture after an original reply with '
 'unsent-field',
 'profile metadata recovery preserves original bytes does not overwrite a changed same-window key and does '
 'not discard later typing when its blocked copy is explicitly removed',
 'profile metadata recovery preserves original bytes keeps later raw in memory after a quota failure without '
 'changing the original recovery bytes',
 'profile metadata recovery preserves original bytes keeps oversized raw in memory without claiming that a '
 'recovery copy was saved',
 'profile metadata recovery preserves original bytes marks a mismatched recovery write as blocked while '
 'retaining both the observed bytes and input',
 'profile metadata recovery preserves original bytes preserves invalid raw values in a strict recovery '
 'roundtrip',
 'profile metadata recovery preserves original bytes recovers only an explicitly chosen same-scope copy '
 'without rewriting its original',
 'profile metadata recovery preserves original bytes refuses boolean revision while retaining the original '
 'copy and later typing',
 'profile metadata recovery preserves original bytes refuses duplicate JSON key while retaining the original '
 'copy and later typing',
 'profile metadata recovery preserves original bytes refuses extra field while retaining the original copy '
 'and later typing',
 'profile metadata recovery preserves original bytes refuses fractional revision while retaining the '
 'original copy and later typing',
 'profile metadata recovery preserves original bytes refuses mismatched sent target while retaining the '
 'original copy and later typing',
 'profile metadata recovery preserves original bytes refuses sent field missing from edits while retaining '
 'the original copy and later typing',
 'profile metadata recovery preserves original bytes refuses unknown edit key while retaining the original '
 'copy and later typing',
 'profile metadata recovery preserves original bytes refuses wrong field shape while retaining the original '
 'copy and later typing',
 'profile metadata recovery preserves original bytes rejects a cross-scope cache or persistence key before '
 'replacing any copy',
 'profile metadata recovery preserves original bytes retains inaccessible original storage and all new raw '
 'input when read access fails',
 'profile metadata recovery preserves original bytes returns A and B separately after A-B-A selection and a '
 'later revision',
 'profile recovery admission after a failed own read admits a stable absence only after explicit reading and '
 'preserves later raw without writing it',
 'profile recovery admission after a failed own read blocks a failed postwrite own read and never removes '
 'the now-observed different copy automatically',
 'profile recovery admission after a failed own read blocks a failed prewrite own read and releases only the '
 'same original bytes on explicit reading',
 'profile recovery admission after a failed own read keeps a changing re-read unknown and forbids discarding '
 'either unverified observation',
 'profile recovery admission after a failed own read keeps a known prewrite quota failure distinct from an '
 'unknown own read',
 'profile recovery admission after a failed own read keeps an unread own copy blocked when storage becomes '
 'available without an explicit read',
 'profile recovery admission after a failed own read keeps later memory input separate from an explicitly '
 'observed malformed own copy',
 'profile recovery admission after a failed own read keeps later memory input separate from an explicitly '
 'observed valid own copy',
 'profile recovery admission after a failed own read recovers a previously unread valid own copy only when '
 'no later input would be replaced',
 'raw output metadata continuity adds or deletes one text override without replacing the latest unrelated '
 'override',
 'raw output metadata continuity captures an explicit note-line edit even when a saved single note has the '
 'same display text',
 'raw output metadata continuity converts optional blanks and note lines only for explicit submission while '
 'retaining the authored spelling',
 'raw output metadata continuity distinguishes typed arrays with identical textarea display when detecting a '
 'changed base',
 'raw output metadata continuity does not confirm pending input from different generation',
 'raw output metadata continuity does not confirm pending input from older revision',
 'raw output metadata continuity does not confirm pending input from same revision',
 'raw output metadata continuity does not drop authored note lines when a peer saves a different array with '
 'the same display text',
 'raw output metadata continuity keeps A restored while B is unconfirmed and after the original B '
 'acknowledgement',
 'raw output metadata continuity keeps C typed after B was sent and rebases C only to the original reply '
 'without replay',
 'raw output metadata continuity keeps equivalent numeric spelling until explicit review or the original '
 'confirmed reply',
 'raw output metadata continuity keeps input and source revision while the visible project is behind a '
 'recovered draft',
 'raw output metadata continuity merges only edited fields into the latest profile and preserves an '
 'ambiguous saved notes array',
 'raw output metadata continuity rejects incomplete or invalid pixel input "" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "-" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "0" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "0x10" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "16385" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "1e" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "2.5" without replacing it',
 'raw output metadata continuity rejects incomplete or invalid pixel input "Infinity" without replacing it',
 'raw output metadata continuity requires separate outcome review for UNKNOWN while keeping unsent field '
 'resolution independent',
 'raw output metadata continuity requires the exact original full upsert reply and rejects partial or '
 'matching observed values',
 'raw output metadata continuity retains raw adaptation_notes across an unrelated saved revision',
 'raw output metadata continuity retains raw audio_codec across an unrelated saved revision',
 'raw output metadata continuity retains raw audio_sample_rate_hz across an unrelated saved revision',
 'raw output metadata continuity retains raw brand_profile across an unrelated saved revision',
 'raw output metadata continuity retains raw burn_in_captions across an unrelated saved revision',
 'raw output metadata continuity retains raw caption_format across an unrelated saved revision',
 'raw output metadata continuity retains raw captions across an unrelated saved revision',
 'raw output metadata continuity retains raw color_space across an unrelated saved revision',
 'raw output metadata continuity retains raw container across an unrelated saved revision',
 'raw output metadata continuity retains raw crop_approved across an unrelated saved revision',
 'raw output metadata continuity retains raw cut_label across an unrelated saved revision',
 'raw output metadata continuity retains raw frame_rate across an unrelated saved revision',
 'raw output metadata continuity retains raw framing_strategy across an unrelated saved revision',
 'raw output metadata continuity retains raw height across an unrelated saved revision',
 'raw output metadata continuity retains raw included_scene_ids across an unrelated saved revision',
 'raw output metadata continuity retains raw language across an unrelated saved revision',
 'raw output metadata continuity retains raw name across an unrelated saved revision',
 'raw output metadata continuity retains raw protected_scene_ids across an unrelated saved revision',
 'raw output metadata continuity retains raw text:abcde000-1234-4234-8234-000000000444 across an unrelated '
 'saved revision',
 'raw output metadata continuity retains raw timing_locked across an unrelated saved revision',
 'raw output metadata continuity retains raw video_codec across an unrelated saved revision',
 'raw output metadata continuity retains raw voice_track_id across an unrelated saved revision',
 'raw output metadata continuity retains raw width across an unrelated saved revision',
 'raw output metadata continuity uses UTF-8 limits without trimming valid profile names',
 'retained metadata for removed profiles bounds the visible recovery inventory without evicting original '
 'copies',
 'retained metadata for removed profiles does not mix project, generation, branch or present-profile copies',
 'retained metadata for removed profiles does not present changing storage bytes as a stable recovery copy',
 'retained metadata for removed profiles keeps a newer window draft separate from a corrupt durable copy',
 'retained metadata for removed profiles keeps submitted input explicitly unconfirmed and does not create a '
 'retry',
 'retained metadata for removed profiles lists one exact durable copy after removal without rewriting '
 'storage, and hides it again after restoration',
 'retained metadata for removed profiles retains quota-failed raw input in memory and explains that it is '
 'not a durable copy',
 'retained metadata for removed profiles surfaces failed storage observation while preserving its in-memory '
 'snapshot',
 'saved Film font source selection at the existing API boundary forwards a uniform exact saved family '
 'spelling without trimming or claiming native rights or weight support',
 'saved Film font source selection at the existing API boundary forwards an explicitly saved IBM 400 style '
 'to preflight, Full and Window without changing the source or grant barriers',
 'saved Film font source selection at the existing API boundary rejects a whitespace-only explicit family '
 'before any preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary rejects an empty explicit family before any '
 'preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary rejects case-distinct family names before '
 'any preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary rejects null following an explicit '
 'nondefault family before any preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary rejects null preceding an explicit '
 'nondefault family before any preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary rejects stale proposed fonts before any call '
 'even when the saved uniform text now names another family',
 'saved Film font source selection at the existing API boundary rejects two explicit families before any '
 'preflight invoke, render grant or Window custody callback',
 'saved Film font source selection at the existing API boundary retains the existing default for no-text '
 "without inheriting a non-text node's font",
 'saved Film font source selection at the existing API boundary retains the existing default for null-only '
 "without inheriting a non-text node's font",
 'saved Film font source selection at the existing API boundary retains the existing default for '
 "null-with-explicit-default without inheriting a non-text node's font",
 'saved Film font source selection at the existing API boundary uses only included Motion Canvas text and '
 'ignores excluded text, another renderer and non-text font metadata',
 'single-segment master keeps otherwise-ready admission blocked until the own copy is actually read',
 'single-segment master local admission dispatches once with the original source, request and grant when the '
 'gesture stays current',
 'single-segment master local admission preserves an original failure after dispatch instead of relabeling '
 'it as local cancellation or retrying',
 'single-segment master local admission refuses pending input before requesting any effect grant',
 'single-segment master local admission rereads the same gesture after a deferred grant and never dispatches '
 'changed input',
 'the current delivery gesture joins the existing Project actor compares stable values and accepts a newly '
 'allocated equivalent view',
 'the current delivery gesture joins the existing Project actor keeps an edited-then-restored input distinct '
 'from the original gesture',
 'the current delivery gesture joins the existing Project actor observes a failed creation without retrying '
 'it or replacing the active Project',
 'the current delivery gesture joins the existing Project actor observes an actual A-B-A open sequence even '
 'if React still shows the original source',
 'the current delivery gesture joins the existing Project actor observes failed edits through the same actor '
 'even when the Project never changed',
 'the current delivery gesture joins the existing Project actor refuses active branch changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses active project changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses completed actor operation changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses delivery profile changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses input after React renders changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses input before React renders changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses pending actor operation changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses pending inputs on initial capture '
 'instead of creating a grant opportunity',
 'the current delivery gesture joins the existing Project actor refuses pending or conflicted draft changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses rendered input serial changed during '
 'the grant',
 'the current delivery gesture joins the existing Project actor refuses rendered source changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses restored generation changed during '
 'the grant',
 'the current delivery gesture joins the existing Project actor refuses saved revision changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses source token or destination changed '
 'during the grant',
 'the current delivery gesture joins the existing Project actor refuses unavailable project changed during '
 'the grant',
 'the current delivery gesture joins the existing Project actor refuses unmounted view changed during the '
 'grant',
 'the current delivery gesture joins the existing Project actor refuses unready native intent changed during '
 'the grant',
 'verified master export keeps otherwise-ready admission blocked until the own copy is actually read',
 'verified master export local admission dispatches once with the original source, request and grant when '
 'the gesture stays current',
 'verified master export local admission preserves an original failure after dispatch instead of relabeling '
 'it as local cancellation or retrying',
 'verified master export local admission refuses pending input before requesting any effect grant',
 'verified master export local admission rereads the same gesture after a deferred grant and never '
 'dispatches changed input',
 'verified metadata pointer and UNKNOWN separation custody blocks a 65th profile copy without evicting or '
 'modifying any original copy',
 'verified metadata pointer and UNKNOWN separation custody keeps the original UNKNOWN capture and pointer '
 'when separation fails: new-copy-quota',
 'verified metadata pointer and UNKNOWN separation custody keeps the original UNKNOWN capture and pointer '
 'when separation fails: original-changed',
 'verified metadata pointer and UNKNOWN separation custody keeps the original UNKNOWN capture and pointer '
 'when separation fails: pointer-silent',
 'verified metadata pointer and UNKNOWN separation custody refuses a silently dropped own copy and does not '
 'claim a durable write',
 'verified metadata pointer and UNKNOWN separation custody refuses unverified writer publication: foreign',
 'verified metadata pointer and UNKNOWN separation custody refuses unverified writer publication: silent',
 'verified metadata pointer and UNKNOWN separation custody refuses unverified writer publication: throw',
 'verified metadata pointer and UNKNOWN separation custody requires explicit pointer rereading after an '
 'unreadable original pointer',
 'verified metadata pointer and UNKNOWN separation custody separates inspected later input without rewriting '
 'the original UNKNOWN bytes after an observation')
WINDOW_DELIVERY_UNIT_CATALOG_SHA256 = '75a718d5f483403191850e99510f12bc369ca1490a4a9b204647d1775b47d683'
WINDOW_FRONTEND_REVIEWED_PROFILE = {'schema': 'motionwright.window-frontend-source-profile/1',
 'source_commit': '085d12a947d4b15bd74d131e007fe908e925106f',
 'source_tree': '882e0ba3cec60ec5dfda911314d47c6620ff73ec',
 'product_paths': {'apps/studio/src/App.tsx': 'd3dc040880160652c697531f47bd305877fe7c8f83497c35ce0313150609aab0',
                   'apps/studio/src/api.ts': '07cdc9b14138f4af523def764d6f0029a7cf5689675369db17262475e7d2803a',
                   'apps/studio/src/types.ts': '83f2d7992d14744e6b05b53c548bedd3a8f18913fcfaf8864742f0b4308794e6',
                   'apps/studio/src/NativeFrameStage.tsx': 'ba1f2b7f59e2de51640e89caf6478806bb8bb0975804d19a2c72e655bf007594',
                   'apps/studio/src/nativeWindowPreview.ts': '9f254c26930aaf9397f8af8fca9b5b34da791489e264a20d6daa078df2cc98e3',
                   'apps/studio/src/nativeWindowPreview.test.ts': 'a87ced6edd3d3ab5033c73856b4c34d16cb9d573f9bd179dd9811e95bf26b887',
                   'apps/studio/tests/native-preview.spec.ts': '5696f73b076cc7a84275c279149a52004d8d43b8dfe0174b44707aeb1c907683',
                   'apps/studio/src/DeliveryProfiles.tsx': 'dbc5700826dd2e19604e16e8ba139c95d55d7238eed3bc8d72f5ddf422e9740d',
                   'apps/studio/src/deliveryProfileDraft.test.ts': '2f37e4ab4923b780b70dda7d2cb26b152725878da9828ca61598e57c6a2a5d10'},
 'runner': 'ubuntu-24.04',
 'node_heap_mib': 384,
 'build_node_heap_mib': 1024,
 'minimum_available_memory_bytes': 4294967296}
WINDOW_FRONTEND_PRODUCT_PINS = (('apps/studio/src/App.tsx', 'd3dc040880160652c697531f47bd305877fe7c8f83497c35ce0313150609aab0'),
 ('apps/studio/src/api.ts', '07cdc9b14138f4af523def764d6f0029a7cf5689675369db17262475e7d2803a'),
 ('apps/studio/src/types.ts', '83f2d7992d14744e6b05b53c548bedd3a8f18913fcfaf8864742f0b4308794e6'),
 ('apps/studio/src/NativeFrameStage.tsx', 'ba1f2b7f59e2de51640e89caf6478806bb8bb0975804d19a2c72e655bf007594'),
 ('apps/studio/src/nativeWindowPreview.ts',
  '9f254c26930aaf9397f8af8fca9b5b34da791489e264a20d6daa078df2cc98e3'),
 ('apps/studio/src/nativeWindowPreview.test.ts',
  'a87ced6edd3d3ab5033c73856b4c34d16cb9d573f9bd179dd9811e95bf26b887'),
 ('apps/studio/tests/native-preview.spec.ts',
  '5696f73b076cc7a84275c279149a52004d8d43b8dfe0174b44707aeb1c907683'),
 ('apps/studio/src/DeliveryProfiles.tsx', 'dbc5700826dd2e19604e16e8ba139c95d55d7238eed3bc8d72f5ddf422e9740d'),
 ('apps/studio/src/deliveryProfileDraft.test.ts',
  '2f37e4ab4923b780b70dda7d2cb26b152725878da9828ca61598e57c6a2a5d10'))
WINDOW_FRONTEND_DEPENDENCY_PINS = (('apps/studio/src/nativeFrameSelection.test.ts',
  'ca6fdf0eca4e47394703de11bcaf80b3241314752f7e89f6de188a2e654dc1c0'),
 ('apps/studio/src/nativeProgressivePreview.test.ts',
  'dfa5c5ef2950293bb6ece50ed02bd0da90ee142c8b2a31ec43385bdaae097a1d'),
 ('apps/studio/src/previewRecovery.test.ts',
  '65aa390a90fe1f251e108ee9f7a2224ace047f5322b7db081d64ce0119c0b559'),
 ('apps/studio/src/renderReceipt.test.ts',
  'f26196a943d76a4e1f7a84ede0192d8356d49fe654d27e6a13175eb0491ed287'),
 ('apps/studio/src/programMaster.test.ts',
  '91ced453af763e990a760d811f8d5fcb7b5cc4b2b721a65831b97e6bb1a1736a'))
WINDOW_FRONTEND_GATE_PATHS = ('tooling/ci_canvas_service.py',
 'tooling/tests/test_ci_canvas_service.py',
 '.github/workflows/studio-service-verification.yml')
WINDOW_FRONTEND_BROWSER_SELECTION = ('browser-window-frontend',
 'native-preview.spec.ts',
 ('sampled native PNG readback uses only a session grant and follows the editorial playhead',
  'invalid synthetic frame bytes display a truthful readback error and a retry',
  'Window PNG opt-in keeps source, reordered cut and local indices distinct without promoting recovery or '
  'master authority',
  'a late Window outcome cannot clear a newer pending intent or overwrite later incomplete input',
  'explicit Window reuse failure never dispatches a cold replacement or promotes its token',
  'Window PNG browser decode failure retries only the selected frame on an explicit gesture'))
WINDOW_FRONTEND_BROWSER_CATALOG_SHA256 = 'a3a5e1034ff130a63820645b92be1cf6571fd4439dec4b578fc73d83ade17435'
WINDOW_FRONTEND_SOURCE_PATHS = tuple(dict.fromkeys(
    [path for path, _sha in (*WINDOW_FRONTEND_PRODUCT_PINS, *WINDOW_FRONTEND_DEPENDENCY_PINS)]
    + ["apps/studio/src/nativeFrameSelection.ts", "apps/studio/src/nativeProgressivePreview.ts",
       "apps/studio/src/previewRecovery.ts", "apps/studio/src/renderReceipt.ts", "apps/studio/src/programMaster.ts"]))
BROWSER_SCOPES[WINDOW_FRONTEND_SCOPE] = ("browser-window-frontend",)
BROWSER_SELECTION_TIMEOUTS["browser-window-frontend"] = 360

def window_frontend_inventory():
    """The reviewed six-file Window cohort and the complete saved-font file."""
    rows = WINDOW_FRONTEND_UNITS
    require(isinstance(rows, tuple) and len(rows) == 6
            and tuple(len(names) for _path, names in rows) == (9, 3, 19, 15, 5, 4)
            and len({path for path, _names in rows}) == 6
            and all(isinstance(names, tuple) and names
                    and len(names) == len(set(names))
                    and all(isinstance(name, str) and name for name in names)
                    for _path, names in rows)
            and hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                == WINDOW_FRONTEND_UNITS_SHA256,
            "Window unit source inventory differs")
    delivery = WINDOW_DELIVERY_UNIT_FULL_NAMES
    require(isinstance(delivery, tuple) and len(delivery) == 195
            and all(isinstance(name, str) and name for name in delivery)
            and len(set(delivery)) == len(delivery)
            and isinstance(WINDOW_DELIVERY_UNIT_CATALOG_SHA256, str)
            and hashlib.sha256(json.dumps(delivery, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                == WINDOW_DELIVERY_UNIT_CATALOG_SHA256,
            "complete saved-font unit inventory has not been reviewed")
    return (*rows, ("src/deliveryProfileDraft.test.ts", delivery))


def window_frontend_profile():
    """A missing source/resource description is never runtime permission."""
    profile = WINDOW_FRONTEND_REVIEWED_PROFILE
    require(isinstance(profile, dict) and set(profile) == {
                "schema", "source_commit", "source_tree", "product_paths", "runner",
                "node_heap_mib", "build_node_heap_mib", "minimum_available_memory_bytes"}
            and profile.get("schema") == "motionwright.window-frontend-source-profile/1",
            "Window composition and hosted resource profile are not designated")
    require(all(isinstance(profile.get(key), str)
                and re.fullmatch("[0-9a-f]{40}", profile[key]) is not None
                for key in ("source_commit", "source_tree")),
            "Window source commit/tree is missing")
    require(profile["product_paths"] == dict(WINDOW_FRONTEND_PRODUCT_PINS)
            and profile["runner"] == "ubuntu-24.04"
            and type(profile["node_heap_mib"]) is int and profile["node_heap_mib"] == 384
            and type(profile["build_node_heap_mib"]) is int and profile["build_node_heap_mib"] == 1024
            and type(profile["minimum_available_memory_bytes"]) is int
            and profile["minimum_available_memory_bytes"] == 4 * 1024 ** 3,
            "Window composed bytes or bounded hosted resource profile differs")
    window_frontend_inventory()
    return dict(profile)


def window_frontend_source_binding(repo, observation):
    """Bind the actual gate checkout and the nominated product input separately."""
    profile = window_frontend_profile()
    files = observation.get("files", {})
    for path, expected in (*WINDOW_FRONTEND_PRODUCT_PINS, *WINDOW_FRONTEND_DEPENDENCY_PINS):
        require(files.get(path, {}).get("sha256") == expected,
                "Window reviewed source bytes differ: " + path)
    reference = profile["source_commit"]
    tree = subprocess.check_output(["git", "rev-parse", reference + "^{tree}"],
                                   cwd=repo, timeout=30).decode("ascii").strip()
    require(tree == profile["source_tree"], "Window nominated source tree differs")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "-z", reference, observation["checkout"], "--"],
        cwd=repo, timeout=30)
    require(changed == b"" or changed.endswith(b"\0"), "Window source delta is not closed")
    names = changed.decode("utf-8").split("\0")[:-1] if changed else []
    require(len(names) == len(set(names)) and set(names) <= set(WINDOW_FRONTEND_GATE_PATHS),
            "Window gate differs from the nominated product outside its verification paths")
    for path, expected in WINDOW_FRONTEND_PRODUCT_PINS:
        raw = subprocess.check_output(["git", "show", reference + ":" + path], cwd=repo, timeout=30)
        require(hashlib.sha256(raw).hexdigest() == expected,
                "Window nominated product blob differs: " + path)
    return {"nominated_input": {"commit": reference, "tree": tree},
            "executed_source": {"commit": observation["checkout"], "tree": observation["tree"]},
            "verification_path_delta": names, "result_transfer": False,
            "source_profile": profile}


def window_frontend_environment(*, phase="unit"):
    profile = window_frontend_profile()
    require(phase in ("unit", "build", "browser"), "Window frontend resource phase differs")
    require(sys.platform.startswith("linux") and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("ImageOS") == "ubuntu24" and os.environ.get("ImageVersion"),
            "Window frontend requires the designated genuine hosted Linux context")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", os.environ.get(key, "")) is not None,
                "Window actual run identity is missing")
    require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and os.environ.get("GITHUB_JOB") == "creative-unit-build"
            and re.fullmatch("[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")) is not None,
            "Window frontend needs the first original attempt of its existing job")
    require(os.environ.get("NODE_OPTIONS") == "--max-old-space-size=" + str(profile["node_heap_mib"])
            and os.environ.get("CI") == "true"
            and os.environ.get("MOTIONWRIGHT_CANDIDATE_BUILD") == "0",
            "Window explicit frontend resource/build environment differs")
    memory = Path("/proc/meminfo").read_text(encoding="ascii")
    rows = re.findall(r"^MemAvailable:\s+([0-9]+) kB$", memory, re.MULTILINE)
    require(len(rows) == 1 and int(rows[0]) * 1024 >= profile["minimum_available_memory_bytes"],
            "Window frontend available memory is below its designated floor")
    result = dict(os.environ)
    heap = profile["build_node_heap_mib"] if phase == "build" else profile["node_heap_mib"]
    result["NODE_OPTIONS"] = "--max-old-space-size=" + str(heap)
    for variable in PROCESS_ENVIRONMENT_KEYS:
        result.pop(variable, None)
    return {"environment": result,
            "observed": {"phase": phase, "available_memory_bytes": int(rows[0]) * 1024,
                         "minimum_available_memory_bytes": profile["minimum_available_memory_bytes"],
                         "node_options": result["NODE_OPTIONS"], "workers": 1, "retries": 0,
                         "repeat_each": 1, "runner_os": result["RUNNER_OS"],
                         "image_os": os.environ["ImageOS"], "image_version": os.environ["ImageVersion"],
                         "boundary": "PRE_COMMAND_OBSERVATION_NOT_CONTINUOUS_RESOURCE_WATCHDOG"}}


def validate_window_frontend_units(document, studio):
    """Compare complete per-file full names without counting describe groups as files."""
    rows = window_frontend_inventory()
    expected_count = sum(len(names) for _path, names in rows)
    require(isinstance(document, dict) and document.get("success") is True,
            "Window unit report is missing or unsuccessful")
    require(type(document.get("numTotalTests")) is int
            and type(document.get("numPassedTests")) is int
            and document["numTotalTests"] == document["numPassedTests"] == expected_count,
            "Window unit census differs")
    for key in ("numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites",
                "numPendingTestSuites"):
        require(type(document.get(key)) is int and document[key] == 0,
                "Window unit omissions or failures: " + key)
    require(document.get("wasInterrupted", False) is False
            and not document.get("unhandledErrors") and not document.get("errors")
            and not document.get("testExecError"), "Window unit global execution error")
    expected = {str(studio / path): set(names) for path, names in rows}
    suites = document.get("testResults")
    require(isinstance(suites, list) and len(suites) == len(expected),
            "Window unit file inventory differs")
    files, assertions = set(), []
    for suite in suites:
        require(isinstance(suite, dict), "Window unit suite is not an object")
        name = suite.get("name")
        require(isinstance(name, str) and name in expected and name not in files
                and suite.get("status") == "passed"
                and not suite.get("message") and not suite.get("testExecError"),
                "Window unit file identity or result differs")
        files.add(name)
        results = suite.get("assertionResults")
        require(isinstance(results, list) and len(results) == len(expected[name]),
                "Window unit assertion count differs")
        seen = set()
        for result in results:
            require(isinstance(result, dict), "Window unit assertion is not an object")
            parents, title, full = result.get("ancestorTitles"), result.get("title"), result.get("fullName")
            require(isinstance(parents, list) and all(isinstance(part, str) and part for part in parents)
                    and isinstance(title, str) and title and isinstance(full, str)
                    and full == " ".join((*parents, title))
                    and full in expected[name] and full not in seen,
                    "Window unit full identity differs")
            require(result.get("status") == "passed" and result.get("failureMessages") == []
                    and not result.get("failureDetails") and not result.get("retryReasons")
                    and type(result.get("retryCount", 0)) is int and result.get("retryCount", 0) == 0,
                    "Window unit assertion is not an original ordinary pass")
            seen.add(full)
            assertions.append({"file": str(Path(name).relative_to(studio)), "full_name": full,
                               "ancestor_titles": parents, "title": title})
        require(seen == expected[name], "Window unit exact names differ")
    require(files == set(expected), "Window unit file is missing")
    return {"status": "PASS", "files": len(rows), "passed": expected_count,
            "window_original_cohort": 55, "delivery_complete_file": len(WINDOW_DELIVERY_UNIT_FULL_NAMES),
            "skipped": 0, "retries": 0, "assertions": assertions,
            "evidence_scope": "FRONTEND_UNIT_ASSERTIONS_ONLY", "formal_acceptance": False}


def window_frontend_unit_admission(repo, original, source, *, invoked_output=None):
    """Recheck the original unit/build commands before starting the browser phase."""
    invoked_output = original if invoked_output is None else invoked_output
    receipt_identity, receipt = browser_report_original(original / "receipt.json")
    if receipt.get("status") == "UNKNOWN":
        raise UnknownObservation("Window original unit/build is UNKNOWN; no browser continuation",
                                 receipt.get("unsealed_command", {}))
    require(receipt.get("schema") == "motionwright.studio-unit-build-ci/1"
            and receipt.get("status") == "PASS"
            and receipt.get("requested_selection") == WINDOW_FRONTEND_SCOPE
            and receipt.get("source_before") == receipt.get("source_after") == source,
            "Window original unit/build source or outcome differs")
    require(receipt.get("selection") == [{"file": path, "titles": list(names)}
                                         for path, names in window_frontend_inventory()],
            "Window declared unit/build identity inventory differs")
    for field, variable in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"),
                            ("job", "GITHUB_JOB")):
        require(receipt.get(field) == os.environ.get(variable) and receipt.get(field),
                "Window unit/build actual job identity differs")
    require(receipt.get("service_runtime") == receipt.get("browser_runtime")
            == receipt.get("native_render") == receipt.get("installed_tauri") == "NOT_RUN"
            and receipt.get("human_acceptance") is False and receipt.get("formal_acceptance") is False,
            "Window unit/build exceeded its frontend scope")
    studio = repo / "apps/studio"
    tools = receipt.get("tools", {})
    require(set(tools) == {"node", "npm", "vitest_cli", "security_probe"},
            "Window original unit/build tools are incomplete")
    for value in tools.values():
        require(isinstance(value, dict) and isinstance(value.get("path"), str)
                and Path(value["path"]).is_absolute()
                and digest(Path(value["path"])) == {key: value.get(key) for key in ("bytes", "sha256")},
                "Window unit/build tool changed")
    cli, probe = studio / "node_modules/vitest/vitest.mjs", repo / "tooling/vite_editor_security_probe.mjs"
    require(tools["vitest_cli"]["path"] == str(cli)
            and tools["security_probe"]["path"] == str(probe), "Window unit/build tool path differs")
    wanted = (
        ("security-probe", [tools["node"]["path"], str(probe)], 60),
        ("vitest", [tools["node"]["path"], str(cli), "run",
                    *(path for path, _names in window_frontend_inventory()),
                    "--maxWorkers=1", "--no-file-parallelism", "--retry=0", "--reporter=json",
                    "--outputFile=" + str(invoked_output / "vitest.json")], 300),
        ("build", [tools["npm"]["path"], "run", "build"], 600),
    )
    commands = receipt.get("commands")
    require(isinstance(commands, list) and len(commands) == len(wanted),
            "Window original unit/build commands are incomplete")
    previous = None
    for row, (label, argv, _timeout) in zip(commands, wanted):
        require(isinstance(row, dict) and row.get("label") == label, "Window command order differs")
        command = {key: value for key, value in row.items() if key != "label"}
        require_observed_command(command)
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and not command.get("observation")
                and command.get("argv") == argv and command.get("cwd") == str(studio),
                "Window unit/build original command differs")
        _identity, retained_command = browser_report_original(original / (label + ".command.json"))
        require(retained_command == command, "Window unit/build command original changed")
        for stream in ("stdout", "stderr"):
            require(digest(original / (label + "." + stream + ".log")) == command.get(stream),
                    "Window unit/build stream original changed")
        start = datetime.fromisoformat(command.get("started_utc", ""))
        end = datetime.fromisoformat(command.get("finished_utc", ""))
        require(start.tzinfo is not None and end.tzinfo is not None and start <= end
                and (previous is None or previous <= start), "Window unit/build command time order differs")
        previous = end
    phase_sources = receipt.get("phase_source_after")
    require(isinstance(phase_sources, list) and len(phase_sources) == 3
            and all(value == source for value in phase_sources),
            "Window source was not checked after every unit/build phase")
    observed_resources = receipt.get("frontend_resource_observations")
    require(isinstance(observed_resources, list) and len(observed_resources) == 4
            and all(isinstance(value, dict)
                    and type(value.get("available_memory_bytes")) is int
                    and value["available_memory_bytes"] >= 4 * 1024 ** 3
                    and type(value.get("minimum_available_memory_bytes")) is int
                    and value.get("minimum_available_memory_bytes") == 4 * 1024 ** 3
                    and value.get("phase") == phase
                    and value.get("node_options") == "--max-old-space-size=" + str(heap)
                    and type(value.get("workers")) is int and value["workers"] == 1
                    and type(value.get("retries")) is int and value["retries"] == 0
                    and type(value.get("repeat_each")) is int and value["repeat_each"] == 1
                    for value, phase, heap in zip(observed_resources,
                        ("unit", "unit", "unit", "build"), (384, 384, 384, 1024))),
            "Window unit/build resources were not retained")
    identity, unit_report = browser_report_original(original / "vitest.json")
    verified = validate_window_frontend_units(unit_report, studio)
    require(receipt.get("vitest_original") == identity and receipt.get("vitest") == verified,
            "Window retained unit report differs")
    build = receipt.get("production_build", {})
    require(build.get("status") == "PASS"
            and build.get("scope") == "BUILD_OUTPUT_ONLY; NO_BROWSER_OR_SERVICE_EXECUTION"
            and isinstance(build.get("files"), dict) and build["files"],
            "Window production build is not retained")
    for name, identity in build["files"].items():
        prefix = "e/window-unit-build/production-build/"
        require(isinstance(name, str) and archive_member_name(prefix + name) == prefix + name,
                "Window retained build member name differs")
        for root in (original / "production-build", studio / "dist"):
            archive_ancestors(root / name)
            require(digest(root / name) == identity, "Window production build changed")
    require("index.html" in build["files"] and any(name.endswith(".js") for name in build["files"]),
            "Window production build entrypoints are missing")
    for root in (original / "production-build", studio / "dist"):
        observed = {}
        for path in root.rglob("*"):
            archive_ancestors(path)
            if path.is_file():
                require(len(observed) < 2000, "Window complete build member bound")
                observed[path.relative_to(root).as_posix()] = digest(path)
        require(observed == build["files"], "Window complete production build inventory differs")
    return {"receipt": receipt_identity, "units": verified, "last_command_finished_utc": previous.isoformat(),
            "production_build": build, "actual_source": source["checkout"],
            "run_id": receipt["run_id"], "run_attempt": receipt["run_attempt"], "job": receipt["job"]}


def window_frontend_retain_units(repo, output, source):
    original = output / "window-unit-build"
    admitted = window_frontend_unit_admission(repo, original, source)
    inventory = archive_inventory(output.parent)
    entries = {name: value for name, value in inventory["entries"].items()
               if name == "e/window-unit-build" or name.startswith("e/window-unit-build/")}
    require(entries and entries.get("e/window-unit-build", {}).get("kind") == "directory",
            "Window unit/build original inventory is missing")
    return {"admission": admitted, "retained_prefix": "e/window-unit-build",
            "original_member_entries": entries}


def window_frontend_archive_units(root, inventory, source, receipt):
    binding = receipt.get("window_unit_build_binding")
    require(isinstance(binding, dict) and binding.get("retained_prefix") == "e/window-unit-build",
            "Window browser is not bound to its original unit/build phase")
    entries = {name: value for name, value in inventory["entries"].items()
               if name == "e/window-unit-build" or name.startswith("e/window-unit-build/")}
    require(entries == binding.get("original_member_entries"),
            "Window archived unit/build original inventory differs")
    admitted = window_frontend_unit_admission(root.parent / "g", root / "e/window-unit-build", source)
    require(admitted == binding.get("admission"), "Window unit/build admission differs")
    start = datetime.fromisoformat(receipt.get("discovery", {}).get("command", {}).get("started_utc", ""))
    end = datetime.fromisoformat(admitted["last_command_finished_utc"])
    require(start.tzinfo is not None and end <= start, "Window browser began before the build closed")
    return admitted


# The existing checkout action acquires HEAD and its parent in this hosted job.
# This observer requires an ordinary single-parent child of the nominated
# product object; it never fetches, checks out, resets or retries a substitute.
WINDOW_FRONTEND_BASE_ACQUISITION = "SAME_JOB_CHECKOUT_DEPTH_2_DIRECT_SINGLE_PARENT"
WINDOW_FRONTEND_BASE_SCHEMA = "motionwright.window-base-object-observation/1"
WINDOW_FRONTEND_BASE_CAPS = (("head-before", 15), ("head-commit", 15),
                             ("base-object", 15), ("head-after", 15))


def window_frontend_base_commands(git):
    window_frontend_profile()
    require(isinstance(git, str) and Path(git).is_absolute(), "Window Git executable is not absolute")
    prefix = [git, "--no-replace-objects"]
    head = [*prefix, "rev-parse", "HEAD", "HEAD^{tree}"]
    return (head,
            [*prefix, "cat-file", "commit", "HEAD"],
            [*prefix, "rev-parse", "HEAD^1^{commit}", "HEAD^1^{tree}"],
            head)


def window_frontend_base_originals(repo, output, receipt, source=None):
    """Admit only these retained Git originals; no frontend result is admitted."""
    profile = window_frontend_profile()
    require(isinstance(receipt, dict) and receipt.get("schema") == WINDOW_FRONTEND_BASE_SCHEMA
            and receipt.get("status") == "BASE_OBJECT_VERIFIED_NO_FRONTEND_EXECUTION"
            and receipt.get("nominated_input") == {"commit": profile["source_commit"], "tree": profile["source_tree"]}
            and receipt.get("base_acquisition") == WINDOW_FRONTEND_BASE_ACQUISITION
            and receipt.get("result_transfer") is False and receipt.get("frontend_execution") == "NOT_RUN",
            "Window base object receipt has a foreign or incomplete scope")
    context = {key: os.environ.get(key) for key in (
        "GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")}
    require(receipt.get("context") == context and context["GITHUB_REPOSITORY"] == "seradotcom/motionwright"
            and context["GITHUB_RUN_ATTEMPT"] == "1" and context["GITHUB_JOB"] == "creative-unit-build",
            "Window base object receipt belongs to another source/job/attempt")
    tool = receipt.get("git")
    require(isinstance(tool, dict) and observed_tool(Path(tool.get("path", ""))) == tool,
            "Window Git executable identity changed")
    expected = window_frontend_base_commands(tool["path"])
    commands = receipt.get("commands")
    require(isinstance(commands, list) and len(commands) == len(WINDOW_FRONTEND_BASE_CAPS),
            "Window base command roster is incomplete")
    previous = None
    stdout = {}
    for row, (label, cap), argv in zip(commands, WINDOW_FRONTEND_BASE_CAPS, expected):
        require(isinstance(row, dict) and set(row) == {"label", "cap_seconds", "command"}
                and row["label"] == label and type(row["cap_seconds"]) is int and row["cap_seconds"] == cap,
                "Window base command ordering or bound differs")
        command = row["command"]
        require_observed_command(command)
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and command.get("argv") == argv and command.get("cwd") == str(repo)
                and command.get("selected_environment") == dict.fromkeys(PROCESS_ENVIRONMENT_KEYS),
                "Window base command did not close with its exact invocation")
        raw, _identity = studio68_original(output / (label + ".command.json"), 1024 * 1024)
        require(json.loads(raw) == command, "Window base original command differs")
        start = datetime.fromisoformat(command.get("started_utc", ""))
        end = datetime.fromisoformat(command.get("finished_utc", ""))
        require(start.tzinfo is not None and end.tzinfo is not None and end >= start
                and (previous is None or start >= previous), "Window base command time ordering differs")
        previous = end
        for stream in ("stdout", "stderr"):
            raw, identity = studio68_original(output / (label + "." + stream + ".log"), 16 * 1024 * 1024)
            require(command.get(stream) == identity, "Window base original stream differs")
            if stream == "stdout":
                stdout[label] = raw
    heads = []
    for label in ("head-before", "base-object", "head-after"):
        lines = stdout[label].decode("ascii").splitlines()
        require(len(lines) == 2 and all(re.fullmatch("[0-9a-f]{40}", item) for item in lines),
                "Window Git commit/tree stdout is not exact")
        heads.append({"commit": lines[0], "tree": lines[1]})
    before, base, after = heads
    require(before == after == receipt.get("executed_source")
            and before["commit"] == context["GITHUB_SHA"], "Window acquisition changed the executed checkout")
    raw_commit = stdout["head-commit"]
    header, separator, _message = raw_commit.partition(b"\n\n")
    require(separator == b"\n\n" and len(raw_commit) <= 1024 * 1024,
            "Window original commit header is malformed or exceeds its bound")
    original_oid = hashlib.sha1(b"commit " + str(len(raw_commit)).encode() + b"\0" + raw_commit).hexdigest()
    parents = [line[7:].decode("ascii") for line in header.splitlines() if line.startswith(b"parent ")]
    trees = [line[5:].decode("ascii") for line in header.splitlines() if line.startswith(b"tree ")]
    require(original_oid == before["commit"] and trees == [before["tree"]],
            "Window original commit bytes do not identify the actual checkout")
    require(parents == [profile["source_commit"]],
            "Window gate must be an ordinary single-parent child of its exact nominated base")
    require(base == receipt["nominated_input"], "Window original parent commit/tree differs from the nominated base")
    if source is not None:
        require(before == {"commit": source.get("checkout"), "tree": source.get("tree")},
                "Window base receipt and actual source observation differ")
    return {"nominated_input": base, "executed_source": before, "commands": len(commands),
            "run_id": context["GITHUB_RUN_ID"], "run_attempt": context["GITHUB_RUN_ATTEMPT"],
            "job": context["GITHUB_JOB"], "original_commit_oid": original_oid,
            "original_parent": parents[0], "last_command_finished_utc": previous.isoformat(),
            "result_transfer": False}


def window_frontend_prepare_base(repo, output):
    resources = window_frontend_environment(phase="unit")
    repo, output = (Path(os.path.abspath(path)) for path in (repo, output))
    require(os.environ.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and repo.name == "g" and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent
            and output == repo.parent / "v/tools/window-base",
            "Window base acquisition requires its own genuine hosted g/v layout")
    archive_ancestors(repo)
    archive_ancestors(output.parent.parent)
    require(repo.is_dir() and output.parent.parent.is_dir() and not output.parent.exists()
            and not output.parent.is_symlink(), "Window base originals require a fresh tools directory")
    environment = dict(resources["environment"])
    require(not any(key.startswith("GIT_") and value for key, value in environment.items()),
            "Window base acquisition rejects inherited Git overrides")
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    git = shutil.which("git")
    require(git is not None, "Window Git executable is missing")
    tool = observed_tool(Path(git))
    profile = window_frontend_profile()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": WINDOW_FRONTEND_BASE_SCHEMA, "status": "PREPARATION_REJECTED",
               "nominated_input": {"commit": profile["source_commit"], "tree": profile["source_tree"]},
               "base_acquisition": WINDOW_FRONTEND_BASE_ACQUISITION, "git": tool,
               "context": {key: os.environ.get(key) for key in (
                   "GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
               "resources": resources["observed"], "commands": [],
               "frontend_execution": "NOT_RUN", "result_transfer": False,
               "limits": {"command_seconds": [cap for _label, cap in WINDOW_FRONTEND_BASE_CAPS],
                          "workflow_step_seconds": 120,
                          "boundary": "EXISTING_COMMAND_OBSERVER; NO_CONTINUOUS_FILESYSTEM_WATCHDOG"}}
    code = 1
    try:
        for (label, cap), argv in zip(WINDOW_FRONTEND_BASE_CAPS, window_frontend_base_commands(tool["path"])):
            command = execute(argv, repo, environment, output, label, cap)
            receipt["commands"].append({"label": label, "cap_seconds": cap, "command": command})
            require_observed_command(command)
            require(type(command.get("exit_code")) is int and command["exit_code"] == 0,
                    "Window base Git command failed: " + label)
            if label == "head-before":
                raw, identity = studio68_original(output / (label + ".stdout.log"), 1024)
                require(identity == command["stdout"], "Window initial HEAD original changed")
                lines = raw.decode("ascii").splitlines()
                require(len(lines) == 2 and lines[0] == os.environ["GITHUB_SHA"]
                        and all(re.fullmatch("[0-9a-f]{40}", value) for value in lines),
                        "Window acquisition starts from a foreign checkout")
                receipt["executed_source"] = {"commit": lines[0], "tree": lines[1]}
        receipt["status"] = "BASE_OBJECT_VERIFIED_NO_FRONTEND_EXECUTION"
        receipt["admission"] = window_frontend_base_originals(repo, output, receipt)
        code = 0
    except UnknownObservation as error:
        receipt.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
        code = 124
    except BaseException as error:
        receipt.update(status="PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
    write_new(output / "receipt.json", receipt)
    original, _identity = studio68_original(output / "receipt.json", 4 * 1024 * 1024)
    require(json.loads(original) == receipt, "Window base receipt persistence readback differs")
    return code, receipt


def window_frontend_base_main(arguments):
    parser = argparse.ArgumentParser(description="Verify the exact Window parent acquired by this existing hosted checkout.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    code, receipt = window_frontend_prepare_base(args.repo, args.output)
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output / "receipt.json"),
                      "frontend_execution": "NOT_RUN"}))
    return code


def window_frontend_base_archive(root, inventory, source):
    name = "tools/window-base/receipt.json"
    receipt = archive_json(root, inventory, name)
    result = window_frontend_base_originals(root.parent / "g", root / "tools/window-base", receipt, source)
    expected = {"tools/window-base", name}
    for label, _cap in WINDOW_FRONTEND_BASE_CAPS:
        expected.update("tools/window-base/" + label + suffix for suffix in (
            ".command.json", ".stdout.log", ".stderr.log"))
    require({name for name in inventory["entries"] if name == "tools/window-base"
             or name.startswith("tools/window-base/")} == expected,
            "Window base original member roster differs")
    units = archive_json(root, inventory, "e/window-unit-build/receipt.json")
    first = datetime.fromisoformat(units.get("commands", [{}])[0].get("started_utc", ""))
    require(first.tzinfo is not None
            and datetime.fromisoformat(result["last_command_finished_utc"]) <= first,
            "Window frontend began before base acquisition closed")
    return {"receipt": {key: inventory["entries"][name][key] for key in ("bytes", "sha256")}, **result}


def window_frontend_preflight_main(arguments):
    parser = argparse.ArgumentParser(description="Check the exact Window frontend source and hosted inputs.")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(arguments)
    environment = window_frontend_environment()
    observed = source_observation(args.repo.resolve(), WINDOW_FRONTEND_SCOPE)
    base_root = args.repo.resolve().parent / "v/tools/window-base"
    raw, identity = studio68_original(base_root / "receipt.json", 4 * 1024 * 1024)
    base = window_frontend_base_originals(args.repo.resolve(), base_root, json.loads(raw), observed)
    print(json.dumps({"status": "SOURCE_INPUTS_OBSERVED_NOT_EXECUTED", "source": observed,
                      "resources": environment["observed"], "base_acquisition": base,
                      "base_receipt": identity}))
    return 0


def selected_unit_inventory(selection):
    if selection == WINDOW_FRONTEND_SCOPE:
        return window_frontend_inventory()
    require(selection in ("creative-production", "review", "workflow-preview", "branch-claim"), "unknown selected unit scope")
    rows, expected_files, expected_tests = {
        "creative-production": (CREATIVE_PRODUCTION_UNITS, 2, 67),
        "review": (REVIEW_UNITS, 1, 72),
        "workflow-preview": (WORKFLOW_PREVIEW_UNITS, 3, 79),
        "branch-claim": (BRANCH_CLAIM_UNITS, 2, 134),
    }[selection]
    require(isinstance(rows, tuple) and len(rows) == expected_files
            and len({name for name, _ in rows}) == expected_files
            and sum(len(titles) for _, titles in rows) == expected_tests,
            "selected unit inventory differs")
    require(all(isinstance(titles, tuple) and titles and len(titles) == len(set(titles))
                and all(isinstance(pair, tuple) and len(pair) == 2
                        and all(isinstance(part, str) and part for part in pair)
                        for pair in titles)
                for _, titles in rows), "selected unit identities differ")
    return rows


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


# Exact A3 R31 catalogue; this is not an inherited runtime result.
PHASE1_SERVICE47_SCOPE = "phase1-service47"
PHASE1_SERVICE47_ROSTER_SHA256 = 'ad64c74ee276ebefeec92beaa28d28c0ea5c95abfc187127d488ab15d0a52de7'
PHASE1_SERVICE47_CATALOG_SHA256 = '264db36daddb770a814ca43753f9451151f62e1097dd0a463453e4eb22cf04fa'
PHASE1_SERVICE47_SELECTIONS = (('branch-combination',
  'branch-combination-service.spec.ts',
  ('two real strategies produce a persisted inactive candidate, then editable undo preserves peer style and '
   'historical reviews',
   'lost original reply preserves late choices and UNKNOWN through reload, exact read and explicit separate '
   'intent',
   'an independent peer wins CAS after preview; rejected combination retains raw and never adds a phantom '
   'candidate')),
 ('phase1-camera',
  'camera-reframe-service.spec.ts',
  ('Camera reframe keeps raw input, commits twice and reopens exact SQLite Project',)),
 ('phase1-camera-v2',
  'camera-reframe-v2-service.spec.ts',
  ('saved Manim v2 Camera preserves its authored pair through raw recovery, two UI edits and SQLite '
   'reopen',)),
 ('phase1-canvas',
  'canvas-draft-service.spec.ts',
  ('an empty library reaches thirteen authored revisions while object, scene and workspace drafts survive a '
   'process restart',
   'an independent peer wins overlapping copy while disjoint geometry and locked drafts are preserved',
   'lost reply after real Canvas commit cannot replay the already saved operation',
   'saved parented Canvas text stays visible in real editorial study after service reopen')),
 ('phase1-delivery-recipe',
  'delivery-recipe.spec.ts',
  ('Film and PCM guards use saved options and expose unsupported combinations without pretending a native '
   'result',)),
 ('phase1-narrative-editing',
  'narrative-editing-service.spec.ts',
  ('actual editorial reorder preserves UUIDs, unaffected relationships and scenes through closed SQLite '
   'reopen',
   'a real committed result with a lost reply stays UNKNOWN across reload until reviewed as a separate '
   'intent')),
 ('production-design',
  'production-design-service.spec.ts',
  ('Scene boundary explicit source binding, Draft and Explicit saves preserve authored objects and assets '
   'through SQLite reopen and journalled removal',
   'Scene boundary keeps invalid raw endpoint JSON and its prior source when a real peer makes explicit '
   'rebind stale')),
 ('review',
  'review-service.spec.ts',
  ('ABA author input survives lost review reply, inspection, separate save and SQLite restart',
   'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
   'selected review deliverable, language and rational range persist through actual App reload and SQLite '
   'restart')),
 ('phase1-scoped-change',
  'scoped-change-service.spec.ts',
  ('Studio previews, commits, reopens, undoes and redoes against the real persisted service',
   'two Studio clients reject a stale commit and let the author resolve only the overlapping property')),
 ('phase1-ink',
  'visual-language-draft-service.spec.ts',
  ('a saved ink token changes the actual SVG text paint across revisions and reopen',)),
 ('phase1-capture',
  'scoped-change-capture.spec.ts',
  ('a middle journal loss in an isolated SQLite copy cannot receive a complete oracle',
   'a tail journal loss in an isolated SQLite copy cannot receive a complete oracle',
   'a all journal loss in an isolated SQLite copy cannot receive a complete oracle',
   'a foreign_identity project row cannot redirect reads or mutations and healthy projects remain usable',
   'a invalid_json project row cannot redirect reads or mutations and healthy projects remain usable',
   'complete capture includes every file and a paginated full oracle after all known peers close',
   'pending at close remains tainted despite exit zero and a later clean restart',
   'a timed out real response followed by exit zero never becomes clean capture evidence',
   'a real stdin transport error is permanent even when EOF lets the process exit zero',
   'a broken response pipe records the real nonzero exit and vetoes capture',
   'an unobserved process close cannot be promoted to proof by a later timeout',
   'finalization attempts every peer and retains every outcome when preparation and one close fail',
   'finalization rejects an empty-message preparation failure after actual service closure',
   'finalization rejects an empty-message client failure after actual service closure',
   'finalization rejects an empty-message external failure after actual service closure',
   'service closure preserves UNKNOWN after missing EOF observation and still closes its owner',
   'service closure preserves UNKNOWN after receipt already exists and still closes its owner',
   'service closure prevents a reserved client from spawning after admissions seal',
   'service closure seals ordinary clients after real reopen and captures a separate verified copy',
   'an owner client reserved for a failed restart cannot reuse an earlier clean launch for capture',
   'one raw capture port covers the ordinary initializer, owner-admitted peers and every restart',
   'service closure waits for raw sink flush before presenting a CLOSED barrier to its owner',
   'direct capture cannot bypass an exited process whose raw sinks are still pending',
   'a raw binding exception after spawn still drains and closes the actual child',
   'raw sink rejection permanently vetoes capture while external owner cleanup still runs',
   'raw sink deadline permanently vetoes capture while external owner cleanup still runs',
   'real stderr stays in Buffer mode when SQLite rejects a directory path')))
PHASE1_SERVICE47_GRAPH_EXCLUDED = (('semantic-graph-service.spec.ts',
  'Graph creates, edits, reopens SQLite, persists an actual review and rechecks it after editing'),
 ('semantic-graph-service.spec.ts',
  'Synthetic flow stores exact event times and rejects a real stale CAS without a new event'),
 ('semantic-graph-service.spec.ts',
  'A lost Graph reply cannot replay a committed SQLite mutation after reload'),
 ('semantic-graph-service.spec.ts',
  'Graph removal rejects a prior-session grant and stale base, then removes once through the real UI and '
  'survives SQLite restart'))
PHASE1_SERVICE47_SOURCE_PATHS = tuple("apps/studio/tests/" + row[1] for row in PHASE1_SERVICE47_SELECTIONS)
# Original listed case caps, plus 60 seconds per process for startup/closure.
# Capture adds the three in-body timeout changes (30s, 15s, 15s).
PHASE1_SERVICE47_TIMEOUTS = {
    "branch-combination": 420, "phase1-camera": 180, "phase1-camera-v2": 180,
    "phase1-canvas": 420, "phase1-delivery-recipe": 150, "phase1-narrative-editing": 300,
    "production-design": 300, "review": 420, "phase1-scoped-change": 120,
    "phase1-ink": 150, "phase1-capture": 930,
}

PHASE1_SERVICE47_JOB_SECONDS = 110 * 60
PHASE1_SERVICE47_METADATA_LIMIT = 4 * 1024 * 1024
PHASE1_SERVICE47_DEADLINE_ENV = "MOTIONWRIGHT_SERVICE47_DEADLINE_SHA256"
PHASE1_SERVICE47_TOKEN_ENV = "MOTIONWRIGHT_SERVICE47_METADATA_TOKEN"
PHASE1_SERVICE47_CUSTODY = {
    "execution_cleanup": 30, "seal_guard": 60, "seal": 300, "seal_cleanup": 30,
    "closed_upload_guard": 60, "closed_upload": 300,
    "unsealed_upload_guard": 60, "unsealed_upload": 300,
}
PHASE1_SERVICE47_EFFECT_RESERVE = sum(PHASE1_SERVICE47_CUSTODY.values())
PHASE1_SERVICE47_PREPARATION = {
    "prepare-and-checkout": 180, "python-setup": 120, "node-setup": 120,
    "rust-setup": 300, "rust-identities": 60, "shared-identities": 60,
    "dependencies": 600, "chromium": 600,
}


class Phase1Service47BudgetDenied(ValueError):
    def __init__(self, observation):
        super().__init__("Phase1 original deadline cannot admit the whole phase and pending custody")
        self.observation = observation


@contextmanager
def phase1_service47_guard():
    """A read/admission cap, never a new execution grant or original deadline."""
    require(os.name == "posix" and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            "Phase1 metadata guard requires its own timer")
    previous = signal.getsignal(signal.SIGALRM)
    def expired(_signal, _frame):
        raise TimeoutError("Phase1 original deadline admission exceeded its 60-second guard")
    signal.signal(signal.SIGALRM, expired); signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGALRM, previous)


def phase1_service47_deadline_binding(environment):
    keys = ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
            "GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA", "RUNNER_NAME")
    result = {key: environment.get(key) for key in keys}
    require(all(isinstance(value, str) and value for value in result.values()), "Phase1 original job binding absent")
    require(result["GITHUB_JOB"] == "real-service"
            and re.fullmatch(r"[0-9a-f]{40}", result["GITHUB_SHA"]) is not None
            and result["GITHUB_WORKFLOW_SHA"] == result["GITHUB_SHA"]
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", result["GITHUB_REPOSITORY"]) is not None
            and all(re.fullmatch(r"[1-9][0-9]*", result[key]) is not None
                    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
            and result["GITHUB_WORKFLOW_REF"].startswith(result["GITHUB_REPOSITORY"]
                + "/.github/workflows/studio-service-verification.yml@"), "Phase1 original job binding differs")
    return result


def phase1_service47_deadline_from_original(original, environment, now_ns, boot_id):
    """Validate a carrier-authenticated original; never manufacture an origin here."""
    binding = phase1_service47_deadline_binding(environment)
    require(isinstance(original, dict) and original.get("schema") == "motionwright.service47-job-deadline/1"
            and original.get("binding") == binding and original.get("boot_id") == boot_id
            and original.get("job_seconds") == PHASE1_SERVICE47_JOB_SECONDS
            and original.get("custody_seconds") == PHASE1_SERVICE47_CUSTODY
            and original.get("rounding_allowance_seconds") == 2,
            "Phase1 original deadline source/profile/host differs")
    begin, end = original.get("request_started_mono_ns"), original.get("response_received_mono_ns")
    require(type(now_ns) is int and type(begin) is int and type(end) is int
            and 0 < begin <= end <= now_ns, "Phase1 original clock absent, future or regressed")
    run, jobs = original.get("run"), original.get("jobs")
    require(isinstance(run, dict) and isinstance(jobs, dict), "Phase1 API originals absent")
    require(type(run.get("id")) is int and type(run.get("run_attempt")) is int
            and run.get("id") == int(binding["GITHUB_RUN_ID"])
            and run.get("run_attempt") == int(binding["GITHUB_RUN_ATTEMPT"])
            and run.get("head_sha") == binding["GITHUB_SHA"]
            and run.get("repository", {}).get("full_name") == binding["GITHUB_REPOSITORY"]
            and run.get("path", "").split("@", 1)[0] == ".github/workflows/studio-service-verification.yml"
            and run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress",
            "Phase1 original run/attempt/source/workflow differs")
    rows = jobs.get("jobs")
    require(isinstance(rows, list) and type(jobs.get("total_count")) is int
            and 0 < jobs["total_count"] == len(rows) <= 100, "Phase1 job response is incomplete or paginated")
    selected = [row for row in rows if isinstance(row, dict)
                and row.get("name") == "Real SQLite and Studio / phase1-service47"]
    require(len(selected) == 1, "Phase1 current job is absent or ambiguous")
    job = selected[0]
    require(type(job.get("run_id")) is int and type(job.get("run_attempt")) is int
            and job.get("run_id") == int(binding["GITHUB_RUN_ID"])
            and job.get("run_attempt") == int(binding["GITHUB_RUN_ATTEMPT"])
            and job.get("head_sha") == binding["GITHUB_SHA"]
            and job.get("status") == "in_progress" and job.get("conclusion") is None
            and job.get("runner_name") == binding["RUNNER_NAME"]
            and type(job.get("id")) is int and job["id"] > 0,
            "Phase1 current job/runner identity differs")
    from email.utils import parsedate_to_datetime
    server = parsedate_to_datetime(original.get("jobs_http_date", ""))
    started = datetime.fromisoformat(job.get("started_at", "").replace("Z", "+00:00"))
    require(server.tzinfo is not None and started.tzinfo is not None
            and server.utcoffset().total_seconds() == started.utcoffset().total_seconds() == 0
            and server >= started, "Phase1 authenticated job start/HTTP Date differs")
    age = server - started
    elapsed_upper_ns = ((age.days * 86400 + age.seconds + 2) * 1_000_000_000 + age.microseconds * 1000)
    # Map at response receipt and subtract the complete request interval. This
    # equals begin + H - elapsed_upper; no local wall clock resets the deadline.
    remaining_ns = PHASE1_SERVICE47_JOB_SECONDS * 1_000_000_000 - elapsed_upper_ns - (end - begin)
    deadline_ns = end + remaining_ns
    require(original.get("deadline_mono_ns") == deadline_ns and deadline_ns > now_ns,
            "Phase1 original job deadline differs or is already exhausted")
    return deadline_ns


class Phase1Service47Clock:
    def __init__(self, original, environment, *, now_ns=None, boot_id=None):
        import time
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip() if boot_id is None else boot_id
        self.deadline_ns = phase1_service47_deadline_from_original(original, environment, now_ns, boot_id)
        self.original, self.last_ns, self.unknown, self.active_process = original, now_ns, False, None
        self.expired = False
        self.admissions = []

    def admit(self, label, seconds, *, reserve=PHASE1_SERVICE47_EFFECT_RESERVE, now_ns=None):
        import time
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        require(type(seconds) is int and 0 < seconds <= PHASE1_SERVICE47_JOB_SECONDS
                and type(reserve) is int and 0 <= reserve <= PHASE1_SERVICE47_EFFECT_RESERVE
                and type(now_ns) is int and now_ns >= self.last_ns,
                "Phase1 finite phase budget or monotonic clock differs")
        self.last_ns = now_ns
        record = {"label": label, "phase_seconds": seconds, "reserve_seconds": reserve,
                  "observed_mono_ns": now_ns, "deadline_mono_ns": self.deadline_ns,
                  "remaining_ns": self.deadline_ns - now_ns, "attempted": False, "retry": "FORBIDDEN"}
        require(not self.unknown, "Phase1 UNKNOWN is terminal; no further effects or readback")
        if record["remaining_ns"] < (seconds + reserve) * 1_000_000_000:
            record["status"] = "NOT_STARTED_INSUFFICIENT_ORIGINAL_BUDGET"
            self.admissions.append(record)
            raise Phase1Service47BudgetDenied(record)
        record["status"] = "ADMITTED_COMPLETE_PHASE_AND_CUSTODY"
        self.admissions.append(record)
        return record

    @contextmanager
    def window(self, *, reserve=PHASE1_SERVICE47_EFFECT_RESERVE, cap=None, cleanup=30):
        """Bound Python work as well as subprocess waits; cleanup gets one grace."""
        import time
        require(os.name == "posix" and hasattr(signal, "setitimer")
                and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "Phase1 requires its own deadline signal")
        now = time.monotonic_ns()
        soft = self.deadline_ns - reserve * 1_000_000_000
        if cap is not None:
            require(type(cap) is int and cap > 0, "Phase1 phase window cap differs")
            # Admission receipts/fsync may consume time. Recheck the full phase
            # at the actual window boundary; never shorten it with min().
            self.admit("terminal-window", cap, reserve=reserve, now_ns=now)
            soft = now + cap * 1_000_000_000
        require(now < soft, "Phase1 original aggregate observation window is exhausted")
        previous_alarm, previous_term = signal.getsignal(signal.SIGALRM), signal.getsignal(signal.SIGTERM)
        def expired(signum, _frame):
            if self.expired:
                # Popen.wait may already have reaped the leader. A numeric PGID
                # is not an identity reservation; never signal it on this late
                # path. The first expiration uses execute's existing cleanup.
                # This hard exit proves neither descendant closure nor custody.
                os._exit(124)
            self.unknown = self.expired = True
            signal.setitimer(signal.ITIMER_REAL, cleanup)
            raise UnknownObservation("Phase1 original aggregate deadline expired; no retry", {
                "observation": "UNKNOWN_DEADLINE_NO_RETRY", "pid": getattr(self.active_process, "pid", None),
                "deadline_mono_ns": self.deadline_ns, "signal": signum,
                "diagnostic_custody": "UNSEALED_ORIGINALS_NO_FURTHER_EFFECTS"})
        signal.signal(signal.SIGALRM, expired); signal.signal(signal.SIGTERM, expired)
        signal.setitimer(signal.ITIMER_REAL, (soft - now) / 1_000_000_000)
        try:
            yield self
            if self.unknown or time.monotonic_ns() >= soft:
                self.unknown = True
                raise UnknownObservation("Phase1 original observation window did not close", {
                    "observation": "UNKNOWN_DEADLINE_NO_RETRY", "deadline_mono_ns": self.deadline_ns})
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_alarm); signal.signal(signal.SIGTERM, previous_term)


def phase1_service47_clock(root):
    """The first workflow guard supplies this pinned original, never a fresh H."""
    expected = os.environ.get(PHASE1_SERVICE47_DEADLINE_ENV, "")
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, "Phase1 authenticated original deadline absent")
    path = root / "tools/phase1-deadline.original.json"
    raw, identity = studio68_original(path, 2 * PHASE1_SERVICE47_METADATA_LIMIT + 65536)
    require(identity["sha256"] == expected, "Phase1 original deadline identity changed")
    original = json.loads(raw)
    for name, key in (("phase1-run-response.original.json", "run"), ("phase1-jobs-response.original.json", "jobs")):
        source, fingerprint = studio68_original(root / "tools" / name, PHASE1_SERVICE47_METADATA_LIMIT)
        require(fingerprint == original["response_originals"][name] and json.loads(source) == original[key],
                "Phase1 authenticated API original changed")
        transport_name = name.replace(".original.json", ".transport.original.json")
        transport, transport_fingerprint = studio68_original(root / "tools" / transport_name, 65536)
        require(transport_fingerprint == original["response_transport_originals"][transport_name]
                and json.loads(transport) == original["transport_observations"][name],
                "Phase1 authenticated HTTP Date/status original changed")
    return Phase1Service47Clock(original, os.environ)


def phase1_service47_budget_main(arguments):
    parser = argparse.ArgumentParser(description="Admit a fixed Service47 preparation phase against its original job deadline.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=tuple(PHASE1_SERVICE47_PREPARATION), required=True)
    args = parser.parse_args(arguments)
    with phase1_service47_guard():
        root = args.root.resolve(); clock = phase1_service47_clock(root)
        record = clock.admit(args.phase, PHASE1_SERVICE47_PREPARATION[args.phase])
        write_new(root / "tools" / ("phase1-admit-" + args.phase + ".json"), record)
        clock.admit(args.phase, PHASE1_SERVICE47_PREPARATION[args.phase])
    return 0


def phase1_service47_dependencies_main(arguments):
    parser = argparse.ArgumentParser(description="Install the existing locked dependencies inside the original Service47 deadline.")
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(arguments)
    with phase1_service47_guard():
        repo = args.repo.resolve(); root = repo.parent / "v"; clock = phase1_service47_clock(root)
        output = root / "tools/phase1-dependencies"
        clock.admit("dependencies", PHASE1_SERVICE47_PREPARATION["dependencies"])
        output.mkdir(exist_ok=False)
        receipt = {"status": "FAIL", "commands": [], "retry": "FORBIDDEN"}
    with clock.window():
        try:
            npm, node = shutil.which("npm"), shutil.which("node")
            require(npm and node, "Phase1 npm/node unavailable")
            for label, argv in (("dependencies", [npm, "ci", "--no-audit", "--no-fund"]),
                                ("chromium", [node, str(repo / "apps/studio/node_modules/@playwright/test/cli.js"),
                                              "install", "--with-deps", "chromium"])):
                command = execute(argv, repo / "apps/studio", os.environ, output, label,
                                  PHASE1_SERVICE47_PREPARATION[label], deadline=clock)
                receipt["commands"].append(command); require_observed_command(command)
                require(command.get("exit_code") == 0, "Phase1 locked preparation failed")
            receipt["status"] = "PASS_DEPENDENCIES_ONLY"
        except Phase1Service47BudgetDenied as error:
            receipt.update(error=str(error), budget_denial=error.observation)
        except UnknownObservation as error:
            receipt.update(status="UNKNOWN", error=str(error), unsealed_command=error.command)
        except Exception as error:
            receipt.update(error=type(error).__name__ + ": " + str(error))
        receipt["deadline_admissions"] = clock.admissions
        write_new(output / "receipt.json", receipt)
    return 0 if receipt["status"] == "PASS_DEPENDENCIES_ONLY" else 1



def phase1_branch_admission_label(label):
    return label in ("branch-combination", "branch-combination-discovery")


@contextmanager
def phase1_service47_stdin(path, command):
    """Hash and supply one held descriptor; reopening a pathname is not custody."""
    require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "Phase1 stdin requires POSIX no-follow custody")
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 16384, "Phase1 stdin original bounds")
    identity = archive_identity(before)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    with os.fdopen(os.open(path, flags), "rb", buffering=0) as stream:
        require(archive_identity(os.fstat(stream.fileno())) == identity, "Phase1 stdin changed before open")
        raw = stream.read(16385)
        expected = (json.dumps(phase1_service47_preflight_request(), separators=(",", ":")) + "\n").encode()
        require(raw == expected and len(raw) == before.st_size
                and archive_identity(os.fstat(stream.fileno())) == identity
                and archive_identity(archive_stat(path)) == identity, "Phase1 stdin original changed before spawn")
        archive_ancestors(path)
        command["stdin"] = {"path": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        stream.seek(0)
        try:
            yield stream
        finally:
            try:
                require(archive_identity(os.fstat(stream.fileno())) == identity, "Phase1 held stdin identity changed")
                stream.seek(0)
                require(stream.read(16385) == raw and archive_identity(os.fstat(stream.fileno())) == identity,
                        "Phase1 held stdin bytes changed")
                archive_ancestors(path)
                require(archive_identity(archive_stat(path)) == identity, "Phase1 stdin pathname changed")
            except BaseException as error:
                if execution_unknown(command):
                    command.setdefault("secondary_observation_errors", []).append("stdin custody: " + str(error))
                else:
                    command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="STDIN_ORIGINAL_CHANGED",
                                   error=type(error).__name__ + ": " + str(error))
                raise


def phase1_service47_selections():
    rows = PHASE1_SERVICE47_SELECTIONS
    require(isinstance(rows, tuple) and len(rows) == 11
            and len({row[0] for row in rows}) == len(rows)
            and all(isinstance(row, tuple) and len(row) == 3 and isinstance(row[2], tuple)
                    and row[2] and all(isinstance(title, str) and title for title in row[2]) for row in rows)
            and sum(len(titles) for label, _, titles in rows if label != "phase1-capture") == 20
            and [(name, len(titles)) for label, name, titles in rows if label == "phase1-capture"]
                == [("scoped-change-capture.spec.ts", 27)]
            and len({(name, title) for _, name, titles in rows for title in titles}) == 47
            and not any(name == "semantic-graph-service.spec.ts" for _, name, _ in rows)
            and hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                == PHASE1_SERVICE47_CATALOG_SHA256, "Phase1 service47 closed catalogue differs")
    return rows


def phase1_service47_plan():
    return {"requested": PHASE1_SERVICE47_SCOPE, "scope": "EXACT_ORDINARY_SERVICE20_AND_CLOSURE_CAPTURE27",
            "required": [{"label": label, "file": filename, "titles": list(titles)}
                         for label, filename, titles in phase1_service47_selections()],
            "required_tests": 47, "ordinary_service": 20, "closure_capture": 27,
            "not_selected": [{"file": filename, "title": title, "status": "NOT_SELECTED", "technical_credit": "NONE"}
                             for filename, title in PHASE1_SERVICE47_GRAPH_EXCLUDED],
            "catalogue_original_sha256": PHASE1_SERVICE47_ROSTER_SHA256,
            "catalogue_sha256": PHASE1_SERVICE47_CATALOG_SHA256,
            "catalogue_is_runtime_evidence": False, "discovery_required_before_execution": True,
            "execution_platform": "Linux", "workers": 1, "retries": 0,
            "process_timeouts_seconds": dict(PHASE1_SERVICE47_TIMEOUTS),
            "process_startup_closure_reserve_seconds": 60,
            "original_job_seconds": PHASE1_SERVICE47_JOB_SECONDS,
            "custody_reserve_seconds": dict(PHASE1_SERVICE47_CUSTODY),
            "deadline_origin": "AUTHENTICATED_CURRENT_GITHUB_JOB_START",
            "native_graph": "NOT_SELECTED", "independent_archive_reopen": False,
            "criterion_acceptance": "NOT_ASSESSED", "formal_acceptance": False}


def phase1_service47_report(document, filename, titles, *, listing, expected=None):
    require(type(listing) is bool and (filename, titles) in {(name, names) for _, name, names in phase1_service47_selections()},
            "unknown Phase1 exact report selection")
    require(isinstance(document, dict) and document.get("errors") == [], "Phase1 report errors")
    config = document.get("config", {})
    projects = config.get("projects", [])
    require(type(config.get("workers")) is int and config["workers"] == 1 and config.get("forbidOnly") is True
            and len(projects) == 1 and projects[0].get("name") == projects[0].get("id") == "chromium"
            and type(projects[0].get("repeatEach")) is int and projects[0]["repeatEach"] == 1
            and type(projects[0].get("retries")) is int and projects[0]["retries"] == 0,
            "Phase1 worker/project/retry/repeat configuration differs")
    stats = document.get("stats", {})
    require(all(type(stats.get(key)) is int for key in ("expected", "skipped", "unexpected", "flaky")),
            "Phase1 report counters must be integers")
    identities = browser_result_identities(document)
    require(len(identities) == len(titles) and {pair for pair, _ in identities} == {(filename, title) for title in titles},
            "Phase1 exact file/title inventory differs")
    specs = []
    def visit(suites):
        for suite in suites:
            specs.extend(suite.get("specs", [])); visit(suite.get("suites", []))
    visit(document["suites"])
    identity, timeouts = [], []
    for spec in specs:
        case = spec["tests"][0]
        require(case.get("expectedStatus") == "passed" and case.get("annotations", []) == []
                and type(case.get("timeout")) is int and case["timeout"] > 0
                and isinstance(spec.get("id"), str) and 0 < len(spec["id"]) <= 512
                and type(spec.get("line")) is int and spec["line"] > 0
                and type(spec.get("column")) is int and spec["column"] > 0,
                "Phase1 skip/annotation or source identity differs")
        identity.append({key: spec[key] for key in ("id", "file", "title", "line", "column")}
                        | {"project": "chromium"})
        timeouts.append({"id": spec["id"], "timeout_ms": case["timeout"]})
        if listing:
            require(case.get("results") == [], "Phase1 discovery contains an execution")
    require(len({row["id"] for row in identity}) == len(titles), "Phase1 duplicate original IDs")
    identity.sort(key=lambda row: (row["file"], row["title"]))
    timeouts.sort(key=lambda row: row["id"])
    label = next(label for label, name, names in phase1_service47_selections() if (name, names) == (filename, titles))
    require(sum(row["timeout_ms"] for row in timeouts) <= (PHASE1_SERVICE47_TIMEOUTS[label] - 60) * 1000,
            "Phase1 original case bounds exceed the process budget")
    if listing:
        require(stats["expected"] == stats["unexpected"] == stats["flaky"] == 0 and stats["skipped"] == len(titles),
                "Phase1 list counts differ")
        return {"status": "LISTED_NOT_EXECUTED", "selected": len(titles), "identities": identity,
                "observed_timeouts": timeouts, "execution_results": 0}
    verified = validate_browser_execution(document, filename, titles)
    require(isinstance(expected, dict) and expected.get("status") == "LISTED_NOT_EXECUTED"
            and expected.get("selected") == len(titles) and expected.get("identities") == identity,
            "Phase1 execution differs from its original discovery IDs")
    return {**verified, "identities": identity, "observed_timeouts": timeouts, "original_discovery_matched": True}


def phase1_service47_build_original(output, repo, target, binary):
    stdout = output / "build.stdout.log"
    original = archive_file_digest(stdout, 64 * 1024 * 1024)
    with stdout.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    require(0 < len(raw) <= 64 * 1024 * 1024, "Phase1 Cargo original is absent or oversized")
    require(original == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, "Phase1 Cargo original changed")
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    finished = [row for row in records if row.get("reason") == "build-finished"]
    artifacts = [row for row in records if row.get("reason") == "compiler-artifact"
                 and row.get("target", {}).get("name") == "studio_review_session" and row.get("executable")]
    require(len(finished) == 1 and finished[0].get("success") is True and len(artifacts) == 1,
            "Phase1 original Cargo terminal/artifact differs")
    artifact = artifacts[0]
    require(artifact.get("target", {}).get("kind") == ["example"]
            and "motionwright-service" in artifact.get("package_id", "")
            and Path(artifact["target"].get("src_path", "")) == repo / "crates/service/examples/studio_review_session.rs"
            and Path(artifact["executable"]) == binary == target / "debug/examples/studio_review_session",
            "Phase1 original Cargo executable source/path differs")
    return {"compiler_artifact": artifact, "build_finished": finished[0], "stdout_original": original,
            "stderr_original": digest(output / "build.stderr.log"), "binary": {"path": str(binary), **digest(binary)}}


def phase1_service47_preflight_request():
    return {"id": "phase1-service-preflight", "command": "bootstrap", "args": {"request": {"project_id": None}}}


def phase1_service47_validate_preflight(raw, sdk_revision):
    require(isinstance(raw, bytes) and 0 < len(raw) <= 1024 * 1024
            and re.fullmatch("[0-9a-f]{40}", sdk_revision) is not None, "Phase1 bootstrap original bounds")
    messages = [json.loads(line) for line in raw.splitlines() if line.strip()]
    require(len(messages) == 1 and set(messages[0]) == {"id", "ok", "result"}
            and messages[0]["id"] == "phase1-service-preflight" and messages[0]["ok"] is True,
            "Phase1 actual bootstrap reply differs")
    result = messages[0]["result"]
    require(result.get("test_transport") == "service-test-jsonl"
            and result.get("native_sdk", {}).get("pinned_revision") == sdk_revision,
            "Phase1 compiled SDK differs from the source lock")
    return {"compiled_sdk_revision": sdk_revision, "test_transport": "service-test-jsonl", "reply_count": 1}


def phase1_service47_preflight_original(output, sdk_revision):
    path = output / "phase1-service-preflight.stdout.log"
    original = archive_file_digest(path, 1024 * 1024)
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(original == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, "Phase1 bootstrap original changed")
    return phase1_service47_validate_preflight(raw, sdk_revision)


def phase1_service47_sdk_preflight(repo, output, binary, env, *, deadline=None, maximum_file_bytes=None, maximum_stream_bytes=None):
    label = "phase1-service-preflight"
    if deadline is not None:
        deadline.admit(label, 30)
    stdin_original = output / (label + ".stdin.original")
    raw = (json.dumps(phase1_service47_preflight_request(), separators=(",", ":")) + "\n").encode()
    with stdin_original.open("xb") as stream:
        require(stream.write(raw) == len(raw), "Phase1 stdin short write"); stream.flush(); os.fsync(stream.fileno())
    stores = output / "phase1-preflight-stores"; stores.mkdir()
    command = execute([str(binary), "--database", str(stores / "service.sqlite3")], repo, env, output, label, 30,
                      stdin_original=stdin_original, deadline=deadline,
                      **({"maximum_file_bytes": maximum_file_bytes, "maximum_stream_bytes": maximum_stream_bytes}
                         if maximum_file_bytes is not None or maximum_stream_bytes is not None else {}))
    require_observed_command(command)
    require(command["exit_code"] == 0, "Phase1 actual Service bootstrap failed")
    sdk_revision = json.loads((repo / "SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]["revision"]
    verified = phase1_service47_preflight_original(output, sdk_revision)
    return {"command": command, "stdin_original": digest(stdin_original), "verified": verified}


def phase1_service47_discovery_complete(lists):
    """Close the whole ID inventory before the first selected body can launch."""
    labels = [row[0] for row in phase1_service47_selections()]
    require(isinstance(lists, list) and [row.get("label") for row in lists] == labels
            and all(row.get("status") == "LISTED_NOT_EXECUTED"
                    and row.get("verified", {}).get("status") == "LISTED_NOT_EXECUTED"
                    and row["verified"].get("selected") == len(selection[2])
                    and row["verified"].get("execution_results") == 0
                    for row, selection in zip(lists, phase1_service47_selections())),
            "Phase1 discovery roster is incomplete")
    ids = [item["id"] for row in lists for item in row["verified"]["identities"]]
    require(len(ids) == len(set(ids)) == 47, "Phase1 complete original catalogue IDs differ")


def phase1_service47_phase_order(receipt):
    lists, executions = receipt.get("phase1_discovery"), receipt.get("selections")
    phase1_service47_discovery_complete(lists)
    labels = [row[0] for row in phase1_service47_selections()]
    require(isinstance(executions, list) and [row.get("label") for row in executions] == labels,
            "Phase1 execution roster is incomplete")
    def instant(value):
        require(isinstance(value, str) and value, "Phase1 original command timestamp absent")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None and result.utcoffset() is not None, "Phase1 timestamp timezone absent")
        return result
    first_list = min(instant(row["command"]["started_utc"]) for row in lists)
    last_list = max(instant(row["command"]["finished_utc"]) for row in lists)
    first_execution = min(instant(row["command"]["started_utc"]) for row in executions)
    require(instant(receipt["build"]["finished_utc"]) <= instant(receipt["phase1_sdk_preflight"]["command"]["started_utc"])
            and instant(receipt["phase1_sdk_preflight"]["command"]["finished_utc"]) <= first_list <= last_list <= first_execution,
            "Phase1 build/preflight/discovery/execution order differs")


def phase1_service47_archive_inputs(root, inventory, receipt, source, command_closed):
    output, repo, target = root / "e", root.parent / "g", root / "t"
    binary = target / "debug/examples/studio_review_session"
    require(receipt.get("phase1_build_original") == phase1_service47_build_original(output, repo, target, binary),
            "Phase1 Cargo original binding changed before archive")
    preflight = receipt.get("phase1_sdk_preflight", {})
    command = preflight.get("command", {}); command_closed(command, "phase1-service-preflight")
    original = inventory["entries"].get("e/phase1-service-preflight.stdin.original", {})
    expected_input = (json.dumps(phase1_service47_preflight_request(), separators=(",", ":")) + "\n").encode()
    identity = {"bytes": len(expected_input), "sha256": hashlib.sha256(expected_input).hexdigest()}
    require(original.get("kind") == "file" and {key: original.get(key) for key in identity} == identity
            and preflight.get("stdin_original") == identity
            and command.get("stdin") == {"path": "phase1-service-preflight.stdin.original", **identity}
            and command.get("argv") == [str(binary), "--database", str(output / "phase1-preflight-stores/service.sqlite3")]
            and command.get("cwd") == str(repo) and command.get("selected_environment") == service_environment_binding(target),
            "Phase1 actual bootstrap command/stdin binding differs")
    sdk_revision = json.loads((repo / "SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]["revision"]
    require(preflight.get("verified") == phase1_service47_preflight_original(output, sdk_revision),
            "Phase1 compiled SDK original changed before archive")
    lists = receipt.get("phase1_discovery", [])
    rows = phase1_service47_selections()
    require([row.get("label") for row in lists] == [row[0] for row in rows], "Phase1 archived discovery roster differs")
    result = {}
    for observed, selection in zip(lists, rows):
        label, filename, titles = selection; command = observed.get("command", {})
        command_closed(command, label + "-discovery")
        environment = service_environment_binding(target, output, label, binary)
        environment["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + "-discovery.json"))
        require(command.get("argv") == service_command(receipt["node"]["path"], repo / "apps/studio/node_modules/@playwright/test/cli.js",
                    output, selection, listing=True, scope=PHASE1_SERVICE47_SCOPE)
                and command.get("cwd") == str(repo / "apps/studio") and command.get("selected_environment") == environment,
                "Phase1 original discovery command differs")
        name = "e/" + label + "-discovery.json"
        actual = phase1_service47_report(archive_json(root, inventory, name), filename, titles, listing=True)
        require(observed.get("status") == "LISTED_NOT_EXECUTED" and observed.get("verified") == actual
                and observed.get("original_report") == {key: inventory["entries"][name][key] for key in ("bytes", "sha256")},
                "Phase1 original discovery report differs")
        result[label] = actual
    phase1_service47_phase_order(receipt)
    require(receipt.get("phase1_result") == {"ordinary_service": 20, "closure_capture": 27, "native_graph": "NOT_SELECTED",
                "catalogue_sha256": PHASE1_SERVICE47_CATALOG_SHA256, "formal_acceptance": False},
            "Phase1 complete result statement differs")
    return result


# Additive partial technical oracles. Historical all184 and Studio68 remain separate from the current all208 roster.
# A listed title is not an execution or a contractual/human acceptance decision.
PARTIAL_ORACLE_SCOPE = "criterion-partials"
PARTIAL_ORACLE_SELECTIONS = (('criterion-graph', 'semantic-graph-service.spec.ts', ('Graph creates, edits, reopens SQLite, persists an actual review and rechecks it after editing',)), ('criterion-visual', 'visual-language-draft-service.spec.ts', ('pending visual name and palette survive a real project style-lock revision', 'visual input survives a real process restart and remains editable after a second reopen', 'an empty motion-duration input stays raw and cannot silently become a saved zero', 'ten real peer revisions preserve pending visual choices and every unrelated saved decision')), ('criterion-caption', 'caption-continuity-service.spec.ts', ('fourteen authored revisions retain multi-frame caption spans, output variants and exact sidecars through portable reopen',)))
PARTIAL_ORACLE_CATALOG_SHA256 = '5ca7cbefa715272fc540b528d711f20ee2e5aed233d9a10cdad413669225d683'
PARTIAL_ORACLE_TIMEOUTS = {"criterion-graph": 300, "criterion-visual": 600, "criterion-caption": 300}
PARTIAL_ORACLE_VISUAL_SOURCE_PATHS = (
    "apps/studio/src/VisualLanguageEditor.tsx",
    "apps/studio/src/visualLanguageDraft.ts",
    "apps/studio/src/CanvasWorkspace.tsx",
    "apps/studio/src/App.tsx",
    "apps/studio/src/CompositionStudy.tsx",
    "apps/studio/src/ExpandedStudy.tsx",
    "apps/studio/src/canvasInheritedInk.ts",
    "apps/studio/src/CameraReframeWorkbench.tsx",
    "apps/studio/src/CreativePatchWorkbench.tsx",
    "apps/studio/src/ProceduralFieldWorkbench.tsx",
    "apps/studio/src/ProductStageWorkbench.tsx",
    "apps/studio/src/ProductionDesignWorkspace.tsx",
)
PARTIAL_ORACLE_SOURCE_PATHS = (*tuple("apps/studio/tests/" + row[1] for row in PARTIAL_ORACLE_SELECTIONS),
                               *PARTIAL_ORACLE_VISUAL_SOURCE_PATHS)


def partial_oracle_selections():
    rows = PARTIAL_ORACLE_SELECTIONS
    require(isinstance(rows, tuple) and len(rows) == 3
            and tuple((label, filename, len(titles)) for label, filename, titles in rows) == (
                ("criterion-graph", "semantic-graph-service.spec.ts", 1),
                ("criterion-visual", "visual-language-draft-service.spec.ts", 4),
                ("criterion-caption", "caption-continuity-service.spec.ts", 1))
            and all(isinstance(titles, tuple) and all(isinstance(title, str) and title for title in titles)
                    for _, _, titles in rows)
            and len({(filename, title) for _, filename, titles in rows for title in titles}) == 6
            and hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                == PARTIAL_ORACLE_CATALOG_SHA256,
            "partial technical oracle catalogue differs")
    return rows


def partial_oracle_discovery(document, filename, titles):
    """Exact current list-only inventory, with no execution or skip credit."""
    require((filename, titles) in {(name, names) for _, name, names in partial_oracle_selections()},
            "unknown partial technical oracle file selection")
    require(isinstance(document, dict) and document.get("errors") == [], "partial oracle discovery errors")
    stats = document.get("stats", {})
    require(all(type(stats.get(key)) is int for key in ("expected", "skipped", "unexpected", "flaky"))
            and stats.get("expected") == 0 and stats.get("skipped") == len(titles)
            and stats.get("unexpected") == 0 and stats.get("flaky") == 0,
            "partial oracle discovery must contain only list results")
    expected = {(filename, title) for title in titles}
    found = []
    for pair, case in browser_result_identities(document):
        require(pair in expected and pair not in found and case.get("expectedStatus") == "passed"
                and case.get("results") == [], "partial oracle discovery identity, skip or result differs")
        found.append(pair)
    require(set(found) == expected and len(found) == len(titles), "partial oracle discovered inventory differs")
    return {"status": "LISTED_NOT_EXECUTED", "listed": len(found), "selected": len(found),
            "execution_results": 0, "identities": [[name, title] for name, title in found]}


def partial_stored_oracle(value):
    """Validate the original ScopedChangeService oracle without replaying SQLite."""
    require(isinstance(value, dict) and set(value) == {
        "schemaVersion", "observedAt", "project", "journal", "journalAfterRevisionExclusive", "journalComplete"}
        and type(value.get("schemaVersion")) is int and value["schemaVersion"] == 1
        and type(value.get("journalAfterRevisionExclusive")) is int
        and value["journalAfterRevisionExclusive"] == 0 and value.get("journalComplete") is True,
        "partial stored oracle schema or completeness differs")
    observed = value.get("observedAt")
    require(isinstance(observed, str) and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", observed) is not None,
        "partial stored oracle observation time missing")
    datetime.fromisoformat(observed.replace("Z", "+00:00"))
    project, journal = value.get("project"), value.get("journal")
    require(isinstance(project, dict)
            and all(isinstance(project.get(key), str) and project[key]
                    for key in ("id", "generation", "active_branch"))
            and type(project.get("revision")) is int and 0 <= project["revision"] <= 9007199254740991
            and isinstance(journal, list) and len(journal) == project["revision"],
            "partial stored Project identity or complete journal differs")
    for revision, event in enumerate(journal, 1):
        require(isinstance(event, dict) and type(event.get("revision")) is int
                and event["revision"] == revision and isinstance(event.get("change"), dict)
                and isinstance(event["change"].get("type"), str) and event["change"]["type"]
                and isinstance(event.get("created_at"), str) and event["created_at"],
                "partial stored journal is incomplete or unordered")
    # Each call to observeStoredProject records a fresh timestamp. Compare the
    # complete semantic observation, retaining (not normalizing) both originals.
    return {key: value[key] for key in (
        "schemaVersion", "project", "journal", "journalAfterRevisionExclusive", "journalComplete")}


def partial_closed_store(root, inventory, prefix, source_directory, database_name, participants, preparation_count):
    manifest = archive_json(root, inventory, prefix + "/manifest.json")
    oracle = archive_json(root, inventory, prefix + "/oracle.json")
    outcome = archive_json(root, inventory, prefix + "-outcomes.json")
    require(isinstance(manifest, dict) and type(manifest.get("schemaVersion")) is int
            and manifest["schemaVersion"] == 1 and isinstance(outcome, dict)
            and type(outcome.get("schemaVersion")) is int and outcome["schemaVersion"] == 1
            and outcome.get("errors") == [] and outcome.get("capture") == manifest
            and outcome.get("oracle") == oracle, "partial finalizer original differs")
    partial_stored_oracle(oracle)
    require(manifest.get("sourceDirectory") == str(source_directory)
            and manifest.get("snapshotDirectory") == str(root / prefix / "store")
            and manifest.get("databaseRelativePath") == database_name,
            "partial captured source directory or database differs")
    preparation, closures = outcome.get("preparation"), outcome.get("closures")
    require(isinstance(preparation, list) and len(preparation) == preparation_count
            and all(isinstance(row, dict) and type(row.get("index")) is int and row["index"] == index
                    and row.get("status") == "fulfilled" and "error" not in row
                    for index, row in enumerate(preparation)), "partial preparation differs")
    lifecycle = manifest.get("lifecycle")
    require(isinstance(closures, list) and len(closures) == participants
            and all(isinstance(row, dict) and row.get("status") == "fulfilled"
                    and "error" not in row and row.get("taints") == [] for row in closures)
            and [{key: row.get(key) for key in ("databasePath", "exits", "taints")}
                 for row in closures] == lifecycle, "partial closures differ from captured lifecycle")
    require(isinstance(lifecycle, list) and len(lifecycle) == participants,
            "partial lifecycle participant inventory differs")
    for row in lifecycle:
        exits = row.get("exits")
        require(row.get("databasePath") == str(source_directory / database_name)
                and row.get("taints") == [] and isinstance(exits, list) and exits
                and all(isinstance(item, dict) and type(item.get("processId")) is int
                        and item["processId"] > 0 and type(item.get("code")) is int and item["code"] == 0
                        and item.get("signal") is None and isinstance(item.get("observedAt"), str)
                        and item["observedAt"] for item in exits), "partial original lifecycle is unclosed")
    return manifest, oracle, outcome


def partial_graph_original(original, oracle):
    project = oracle["project"]
    require(isinstance(original, dict) and set(original) == {
        "scope", "initialRevision", "finalRevision", "graph", "review", "events", "excludes"}
        and original.get("scope") == "Actual App + StudioService + SQLite only"
        and original.get("excludes") == ["native rendered output", "provider parity", "installed app", "human craft approval"],
        "partial Graph original family differs")
    initial, final = original.get("initialRevision"), original.get("finalRevision")
    require(type(initial) is int and initial >= 0 and type(final) is int
            and final == project["revision"] == initial + 4,
            "partial Graph revision differs from stored Project")
    expected = ["upsert_semantic_graph", "add_review", "resolve_review", "upsert_semantic_graph"]
    require(original.get("events") == expected
            == [event["change"]["type"] for event in oracle["journal"] if event["revision"] > initial],
            "partial Graph events differ from full stored journal")
    graph, review = original.get("graph"), original.get("review")
    design, reviews = project.get("production_design"), project.get("reviews")
    graphs = design.get("semantic_graphs") if isinstance(design, dict) else None
    require(isinstance(graph, dict) and isinstance(review, dict)
            and all(isinstance(item.get("id"), str) and item["id"] for item in (graph, review))
            and isinstance(graphs, list) and all(isinstance(item, dict) for item in graphs)
            and isinstance(reviews, list) and all(isinstance(item, dict) for item in reviews)
            and [item for item in graphs if item.get("id") == graph["id"]] == [graph]
            and [item for item in reviews if item.get("id") == review["id"]] == [review],
            "partial Graph or review differs from its unique stored object")
    authorship, anchor = graph.get("authorship"), review.get("anchor")
    snapshot = authorship.get("source_snapshot") if isinstance(authorship, dict) else None
    require(isinstance(snapshot, dict) and snapshot.get("project_id") == project["id"]
            and snapshot.get("generation") == project["generation"]
            and snapshot.get("branch_id") == project["active_branch"]
            and type(snapshot.get("revision")) is int and initial <= snapshot["revision"] < final
            and isinstance(anchor, dict) and anchor.get("resource") == "graph:" + graph["id"]
            and anchor.get("branch_id") == project["active_branch"]
            and type(anchor.get("revision")) is int and anchor["revision"] == initial + 1
            and review.get("status") == "needs_recheck", "partial Graph Project or anchored review differs")


def partial_visual_original(original, title, oracle, outcome, manifest, binary, source):
    require(isinstance(original, dict)
            and original.get("schema") == "motionwright.visual-language-service-evidence/1"
            and original.get("fixture") == "synthetic_authored_content" and original.get("test") == title
            and original.get("case_result") == "completed" and "failure" in original
            and original["failure"] is None and original.get("cleanupErrors") == []
            and original.get("evidence_scope") == "real_service_sqlite_cas_and_browser_visual_input_continuity"
            and original.get("excludes") == ["installed_tauri", "native_realization", "font_rights_acceptance", "human_acceptance"],
            "partial Visual original family, case or failure differs")
    files = source["files"]
    expected_ui = []
    for name in PARTIAL_ORACLE_VISUAL_SOURCE_PATHS:
        item = files.get(name)
        require(isinstance(item, dict) and isinstance(item.get("sha256"), str)
                and re.fullmatch("[0-9a-f]{64}", item["sha256"]) is not None,
                "partial Visual observed UI source missing")
        expected_ui.append({"path": name, "sha256": item["sha256"]})
    ui = original.get("ui_sources")
    spec = files.get("apps/studio/tests/visual-language-draft-service.spec.ts", {})
    require(isinstance(spec, dict) and isinstance(spec.get("sha256"), str)
            and re.fullmatch("[0-9a-f]{64}", spec["sha256"]) is not None,
            "partial Visual observed test source missing")
    require(isinstance(ui, dict) and ui.get("unchanged_during_case") is True
            and ui.get("start") == ui.get("end") == expected_ui
            and original.get("test_source_sha256") == spec.get("sha256")
            and original.get("inspected_source_sha256") == expected_ui[0]["sha256"],
            "partial Visual original sources differ from observed source")
    require(original.get("runner_sha256") == binary["sha256"],
            "partial Visual original runner differs from retained binary")
    require(original.get("finalization") == outcome,
            "partial Visual finalization differs from original outcome")
    semantic = partial_stored_oracle(oracle)
    initial = partial_stored_oracle(original.get("initialOracle"))
    checkpoints = original.get("checkpoints")
    require(isinstance(checkpoints, list) and len(checkpoints) >= 2
            and all(isinstance(row, dict) and isinstance(row.get("label"), str) and row["label"] for row in checkpoints)
            and checkpoints[0]["label"] == "initial complete oracle before interaction"
            and checkpoints[-1]["label"] == "observed final state before every process closes",
            "partial Visual checkpoint inventory differs")
    observed = [partial_stored_oracle(row.get("oracle")) for row in checkpoints]
    require(observed[0] == initial and observed[-1] == semantic,
            "partial Visual final checkpoint differs from closed oracle")
    revisions = []
    for checkpoint in observed:
        project = checkpoint["project"]
        require(all(project[key] == semantic["project"][key] for key in ("id", "generation", "active_branch"))
                and project["revision"] <= semantic["project"]["revision"]
                and checkpoint["journal"] == semantic["journal"][:project["revision"]],
                "partial Visual checkpoint Project or journal differs")
        revisions.append(project["revision"])
    require(revisions == sorted(revisions), "partial Visual checkpoint revisions are unordered")
    participants = 2 if title == "ten real peer revisions preserve pending visual choices and every unrelated saved decision" else 1
    services = original.get("services")
    require(isinstance(services, list) and len(services) == participants
            and all(isinstance(item, dict) for item in services)
            and [{"databasePath": item.get("databasePath"), "exits": item.get("processExits"),
                  "taints": item.get("lifecycleTaints")} for item in services] == manifest["lifecycle"],
            "partial Visual services differ from original captured lifecycle")


def partial_caption_original(original, observations):
    require(isinstance(original, dict)
            and original.get("schema") == "motionwright.caption-continuity-test/1"
            and original.get("scope") == "real_studio_service_sqlite_cas_and_shared_desktop_caption_export"
            and original.get("excludes") == ["installed_tauri", "native_burn_in_pixels", "human_acceptance"]
            and original.get("status") == "passed" and original.get("errors") == [],
            "partial Caption original family, status or errors differ")
    stores, services = original.get("stores"), original.get("services")
    values = original.get("observations")
    require(isinstance(stores, list) and len(stores) == 2 and isinstance(services, list) and len(services) == 2
            and isinstance(values, dict), "partial Caption two-store original is incomplete")
    projects = []
    for index, (manifest, oracle, outcome, binary) in enumerate(observations):
        store, service = stores[index], services[index]
        require(isinstance(store, dict) and set(store) == {"oracles", "finalization"}
                and isinstance(store.get("oracles"), list) and len(store["oracles"]) == 1
                and partial_stored_oracle(store["oracles"][0]) == partial_stored_oracle(oracle)
                and store.get("finalization") == outcome,
                "partial Caption store or finalization differs from original outcome")
        require(isinstance(service, dict) and service.get("binary") == binary["path"]
                and [{"databasePath": service.get("database"), "exits": service.get("exits"),
                      "taints": service.get("taints")}] == manifest["lifecycle"],
                "partial Caption service binary or lifecycle differs")
        projects.append(oracle["project"])
    require(values.get("original") == projects[0] and values.get("final") == projects[1],
            "partial Caption observations differ from ordered stored Projects")
    first, final = projects
    imported, descriptor = values.get("imported"), values.get("source")
    require(first["id"] == final["id"] and first["generation"] != final["generation"]
            and first["revision"] == 14 and final["revision"] == 15
            and isinstance(imported, dict) and imported.get("id") == final["id"]
            and imported.get("generation") == final["generation"]
            and imported.get("active_branch") == final["active_branch"]
            and type(imported.get("revision")) is int and imported["revision"] == 14
            and all(key in first and key in imported and first[key] == imported[key]
                    for key in ("audio", "deliverables", "scenes"))
            and isinstance(descriptor, dict) and descriptor.get("project_id") == first["id"]
            and descriptor.get("generation") == first["generation"]
            and type(descriptor.get("revision")) is int and descriptor["revision"] == first["revision"],
            "partial Caption original/imported Project identity or revision differs")


def partial_oracle_store_bindings(root, inventory, selections, captures, binary, source):
    """Join the six current spec originals to seven separately retained stores."""
    require(isinstance(binary, dict) and isinstance(binary.get("path"), str)
            and Path(binary["path"]).is_absolute() and type(binary.get("bytes")) is int and binary["bytes"] > 0
            and isinstance(binary.get("sha256"), str) and re.fullmatch("[0-9a-f]{64}", binary["sha256"]) is not None
            and isinstance(source, dict) and isinstance(source.get("files"), dict),
            "partial retained binary or source binding missing")
    layout = {
        "criterion-graph": ("graph-real-source-review-reopen", (("closed-graph-store", "store", "semantic-graph.sqlite3"),)),
        "criterion-visual": ("visual-language-real-service-evidence", (("closed-store", "service", "studio.sqlite3"),)),
        "criterion-caption": ("caption-continuity-evidence", (("closed-store-0", "original", "studio.sqlite3"),
                                                              ("closed-store-1", "reimported", "studio.sqlite3"))),
    }
    required = partial_oracle_selections()
    require([row.get("label") for row in selections] == [row[0] for row in required],
            "partial store selection differs")
    known = {row["manifest"] for row in captures}
    require(len(known) == len(captures), "partial captured store inventory repeats a manifest")
    used, case_roots, bindings = set(), set(), []
    for selection, (label, filename, titles) in zip(selections, required):
        document = archive_json(root, inventory, "e/" + label + ".json")
        validate_browser_execution(document, filename, titles)
        attachment_name, stores = layout[label]
        for pair, case in browser_result_identities(document):
            attachments = case["results"][0].get("attachments", [])
            found = [row for row in attachments if row.get("name") == attachment_name]
            require(len(found) == 1 and found[0].get("contentType") == "application/json",
                    "partial case original attachment missing or duplicate")
            relative = archive_member_name(Path(found[0].get("path", "")).relative_to(root).as_posix())
            parts = relative.split("/")
            require(parts[:2] == ["e", label + "-test-results"], "partial case attachment outside output")
            if len(parts) == 5 and parts[3] == "attachments":
                require(re.fullmatch(re.escape(attachment_name) + r"-[0-9a-f]{40}\.json", parts[4]) is not None,
                        "partial reporter attachment name differs")
                original_name = "/".join((*parts[:3], attachment_name + ".json"))
            else:
                require(len(parts) == 4 and parts[3] == attachment_name + ".json",
                        "partial original attachment name differs")
                original_name = relative
            attached = archive_json(root, inventory, relative)
            original = archive_json(root, inventory, original_name)
            require(attached == original and all(inventory["entries"][relative][key]
                    == inventory["entries"][original_name][key] for key in ("bytes", "sha256")),
                    "partial reporter copy differs from original")
            require(isinstance(original, dict), "partial case original must be a JSON object")
            case_root = original_name.rsplit("/", 1)[0]
            require(case_root not in case_roots, "partial cases share an original output root")
            case_roots.add(case_root)
            manifests, observations = [], []
            participants = 2 if label == "criterion-visual" and pair[1] == titles[-1] else 1
            for store, source_name, database_name in stores:
                prefix = case_root + "/" + store
                manifest_name = prefix + "/manifest.json"
                require(manifest_name in known and manifest_name not in used,
                        "partial case closed store missing or shared")
                used.add(manifest_name)
                manifest, oracle, outcome = partial_closed_store(
                    root, inventory, prefix, root / case_root / source_name, database_name,
                    participants, 1 if label == "criterion-graph" else 0)
                observations.append((manifest, oracle, outcome, binary))
                manifests.append(manifest_name)
            if label == "criterion-graph":
                partial_graph_original(original, observations[0][1])
            elif label == "criterion-visual":
                manifest, oracle, outcome, _ = observations[0]
                partial_visual_original(original, pair[1], oracle, outcome, manifest, binary, source)
            else:
                partial_caption_original(original, observations)
            bindings.append({"label": label, "file": pair[0], "title": pair[1],
                             "original_evidence": original_name, "manifests": manifests,
                             "original_content_join": "EXPLICIT_CURRENT_SPEC_PROJECT_JOURNAL_LIFECYCLE",
                             "technical_scope": "ORIGINAL_CASE_AND_CLOSED_STORES; NO_ARCHIVE_REOPEN",
                             "criterion_acceptance": "NOT_ASSESSED"})
    require(len(bindings) == 6 and len(used) == 7 and used == known,
            "partial case/store inventory differs")
    return bindings


def partial_oracle_list_order(discovered, selections):
    require(isinstance(discovered, list) and isinstance(selections, list)
            and len(discovered) == len(selections) == 3, "partial discovery/execution inventory differs")
    finishes, starts = [], []
    for rows, key, times in ((discovered, "finished_utc", finishes), (selections, "started_utc", starts)):
        for row in rows:
            value = row.get("command", {}).get(key)
            require(isinstance(value, str), "partial original command time missing")
            observed = datetime.fromisoformat(value)
            require(observed.tzinfo is not None and observed.utcoffset() is not None,
                    "partial original command time must include timezone")
            times.append(observed)
    require(max(finishes) <= min(starts), "partial oracle execution preceded a required list")


def is_browser_scope(selection):
    return isinstance(selection, str) and selection in BROWSER_SCOPES


def selected_selections(selection):
    """Return a closed scope; an explicit subset never claims the full roster."""
    require(isinstance(selection, str), "selection must be a single named scope")
    if is_browser_scope(selection):
        return browser_selections(selection)
    if selection == PHASE1_SERVICE47_SCOPE:
        return phase1_service47_selections()
    if selection == CAMERA_EIGHT_SCOPE:
        return camera_eight_selections()
    if selection == PARTIAL_ORACLE_SCOPE:
        return partial_oracle_selections()
    require(SELECTIONS == (*PROJECT_AUTHORING_SELECTIONS, *CREATIVE_PRODUCTION_SELECTIONS, *BRANCH_CLAIM_SELECTIONS)
            and len(PROJECT_AUTHORING_SELECTIONS) == 19
            and sum(len(row[2]) for row in PROJECT_AUTHORING_SELECTIONS) == 143
            and len(HISTORICAL_PROJECT_AUTHORING_SELECTIONS) == 19
            and sum(len(row[2]) for row in HISTORICAL_PROJECT_AUTHORING_SELECTIONS) == 135
            and len(CREATIVE_PRODUCTION_SELECTIONS) == 2
            and sum(len(row[2]) for row in CREATIVE_PRODUCTION_SELECTIONS) == 30
            and len(BRANCH_CLAIM_SELECTIONS) == 3
            and tuple((label, filename, len(titles)) for label, filename, titles in BRANCH_CLAIM_SELECTIONS) == (
                ("branch-authoring", "branch-authoring-service.spec.ts", 18),
                ("claim-source", "claim-source-service.spec.ts", 14),
                ("branch-combination", "branch-combination-service.spec.ts", 3))
            and all(isinstance(titles, tuple) and len(set(titles)) == len(titles)
                    and all(isinstance(title, str) and title for title in titles)
                    for _, _, titles in BRANCH_CLAIM_SELECTIONS),
            "service catalogue differs from its closed historical and composed scopes")
    if selection == "all":
        return SELECTIONS
    if selection == "project-authoring-core":
        require(SELECTION_GROUPS.get(selection) == {
            "labels": tuple(row[0] for row in PROJECT_AUTHORING_SELECTIONS if row[0] != "review"),
            "required_tests": 132}, "bounded project partition differs from its exact source catalogue")
    if selection in SELECTION_GROUPS:
        group = SELECTION_GROUPS[selection]
        labels, expected = group.get("labels"), group.get("required_tests")
        require(isinstance(labels, tuple) and labels and all(isinstance(x, str) for x in labels),
                "group must declare a nonempty tuple of file selections")
        require(len(set(labels)) == len(labels), "group repeats a file selection")
        require(type(expected) is int and expected > 0, "group must declare its exact case count")
        chosen = tuple(row for row in SELECTIONS if row[0] in labels)
        require(len(chosen) == len(labels), "group contains an unknown or duplicate file selection")
        require(sum(len(titles) for _, _, titles in chosen) == expected,
                "group case count differs from its declared closed scope")
        return chosen
    chosen = tuple(row for row in SELECTIONS if row[0] == selection)
    require(len(chosen) == 1, "unknown or empty service selection")
    return chosen


def selection_plan(selection):
    chosen = selected_selections(selection)
    labels = {row[0] for row in chosen}
    if selection == PHASE1_SERVICE47_SCOPE:
        return phase1_service47_plan()
    if selection == CAMERA_EIGHT_SCOPE:
        return {"requested": selection, "scope": "CAMERA_SEVEN_PLUS_SAVED_MANIM_V2",
                "required": [{"label": label, "file": filename, "titles": list(titles)}
                             for label, filename, titles in chosen],
                "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                                  "status": "NOT_SELECTED", "technical_credit": "NONE"}
                                 for label, filename, titles in SELECTIONS if label != "camera"],
                "required_tests": 8, "full_default_tests": 208, "existing_camera_cases": 7,
                "added_v2_cases": 1, "discovery_required_before_execution": True,
                "execution_platform": "Linux", "independent_archive_reopen": False,
                "criterion_acceptance": "NOT_ASSESSED", "formal_acceptance": False}
    if selection == PARTIAL_ORACLE_SCOPE:
        return {"requested": selection, "scope": "EXPLICIT_PARTIAL_TECHNICAL_SERVICE_ORACLES",
                "required": [{"label": label, "file": filename, "titles": list(titles)}
                             for label, filename, titles in chosen],
                "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                                  "status": "NOT_SELECTED", "technical_credit": "NONE"}
                                 for label, filename, titles in SELECTIONS],
                "required_tests": 6, "full_default_tests": 208,
                "discovery_required_before_execution": True, "execution_platform": "Linux",
                "criterion_acceptance": "NOT_ASSESSED", "formal_acceptance": False}
    if is_browser_scope(selection):
        selected, excluded = browser_discovery_inventory(selection)
        return {
            "requested": selection, "scope": "EXPLICIT_SYNTHETIC_BROWSER_CONTRACTS",
            "required": [{"label": label, "file": filename, "titles": list(titles)}
                         for label, filename, titles in chosen],
            "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                             "status": "NOT_SELECTED", "technical_credit": "NONE"}
                             for label, filename, titles in (*SELECTIONS, *BROWSER_SELECTIONS, *REVIEW_BROWSER_SELECTIONS,
                                                            *WORKFLOW_PREVIEW_BROWSER_SELECTIONS)
                             if label not in labels],
            "excluded_service_cases": [{"file": filename, "title": title,
                                       "status": "NOT_SELECTED", "technical_credit": "NONE"}
                                      for filename, title in BROWSER_OMITTED_SERVICE_CASES
                                      if (filename, title) in excluded],
            "required_tests": len(selected), "discovery_tests": len(selected | excluded),
            "full_default_tests": sum(len(titles) for _, _, titles in SELECTIONS),
            "formal_acceptance": False,
        }
    return {
        "requested": selection,
        "scope": "ALL_DEFINED_SERVICE_CASES" if selection == "all" else "EXPLICIT_SELECTED_SERVICE_CASES",
        "required": [{"label": label, "file": filename, "titles": list(titles)}
                     for label, filename, titles in chosen],
        "not_selected": [{"label": label, "file": filename, "titles": list(titles),
                          "status": "NOT_SELECTED", "technical_credit": "NONE"}
                         for label, filename, titles in SELECTIONS if label not in labels],
        "required_tests": sum(len(titles) for _, _, titles in chosen),
        "full_default_tests": sum(len(titles) for _, _, titles in SELECTIONS),
        "formal_acceptance": False,
    }


def validate_report(document, filename, titles):
    """Reject empty, omitted, repeated, retried or expected-failure selections."""
    require(isinstance(document, dict), "report must be an object")
    require(document.get("errors") == [], "report has missing or nonempty global errors")
    stats = document.get("stats", {})
    require(stats.get("expected") == len(titles), "selected success count differs")
    for name in ("skipped", "unexpected", "flaky"):
        require(stats.get(name) == 0, "selected " + name + " count is not zero")
    found = []

    def visit(suites):
        require(isinstance(suites, list), "suite list is missing")
        for suite in suites:
            require(isinstance(suite, dict), "suite must be an object")
            for spec in suite.get("specs", []):
                require(spec.get("file") == filename, "unexpected test file")
                require(spec.get("ok") is True, "test specification did not pass")
                title = spec.get("title")
                require(title in titles and title not in found, "unknown or duplicate test title")
                found.append(title)
                tests = spec.get("tests", [])
                require(len(tests) == 1, "each selected case must execute once")
                test = tests[0]
                require(test.get("projectId") == "chromium", "unexpected browser project")
                require(test.get("expectedStatus") == "passed", "expected failure or skip is forbidden")
                require(test.get("status") == "expected", "test is not an ordinary pass")
                results = test.get("results", [])
                require(len(results) == 1, "retry or missing original result")
                result = results[0]
                require(result.get("status") == "passed" and result.get("retry") == 0,
                        "original result must pass without retry")
                require(not result.get("error") and not result.get("errors"),
                        "original result contains an error")
            visit(suite.get("suites", []))

    visit(document.get("suites"))
    require(set(found) == set(titles) and len(found) == len(titles), "selected inventory differs")
    return {"passed": len(found), "skipped": 0, "retries": 0, "titles": found}


def digest(path):
    require(path.is_file() and not path.is_symlink(), "regular file required: " + str(path))
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": value}


def write_new(path, value):
    body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        require(stream.write(body) == len(body), "short evidence write")
        stream.flush()
        os.fsync(stream.fileno())


BRANCH_COMBINATION_SOURCE_PATHS = ('apps/desktop/src-tauri/build.rs',
 'apps/desktop/src-tauri/capabilities/desktop-owner.json',
 'apps/desktop/src-tauri/src/main.rs',
 'apps/studio/src/App.tsx',
 'apps/studio/src/BranchCombinationEditor.tsx',
 'apps/studio/src/CreativeAlternativesPanel.tsx',
 'apps/studio/src/ReviewWorkspace.tsx',
 'apps/studio/src/api.ts',
 'apps/studio/src/branchCombinationDraft.test.ts',
 'apps/studio/src/branchCombinationDraft.ts',
 'apps/studio/src/branchCombinationReview.test.ts',
 'apps/studio/src/branchCombinationReview.ts',
 'apps/studio/src/branchDraft.ts',
 'apps/studio/src/types.ts',
 'apps/studio/src/useBranchDraft.tsx',
 'apps/studio/tests/branch-combination-service.spec.ts',
 'crates/domain/src/branch_combination.rs',
 'crates/domain/src/history.rs',
 'crates/domain/src/lib.rs',
 'crates/domain/tests/branch_combination.rs',
 'crates/service/examples/studio_review_session.rs',
 'crates/service/src/branch_combination.rs',
 'crates/service/src/lib.rs',
 'crates/service/tests/branch_combination.rs')


def source_observation(repo, selection=None):
    if selection == GRAPH_SCOPE:
        index = graph_json((repo.parent / "v/e/graph-producer/producer-index.original.json").read_bytes())
        return graph_source_view(graph_source_observe(repo, os.environ["GITHUB_SHA"], tuple(index["contract"]["source_files"])))
    require(selection is None or is_browser_scope(selection) or selection in (PARTIAL_ORACLE_SCOPE, CAMERA_EIGHT_SCOPE, PHASE1_SERVICE47_SCOPE),
            "unknown source observation scope")
    if selection == CAMERA_EIGHT_SCOPE:
        camera_eight_profile()
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=repo, timeout=30).decode().strip()

    head = git("rev-parse", "HEAD")
    require(re.fullmatch("[0-9a-f]{40}", head) is not None, "invalid checkout SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(head == os.environ.get("GITHUB_SHA"), "checkout differs from the selected CI source")
    require(git("diff", "--name-only", "HEAD", "--") == "", "tracked source has changed")
    paths = (
        ".github/workflows/ci.yml", ".github/workflows/studio-service-verification.yml",
        "Cargo.lock", "apps/studio/package-lock.json",
        "crates/service/examples/studio_review_session.rs",
        "apps/studio/playwright.config.ts",
        "apps/studio/tests/support/scopedChangeService.ts",
        "apps/studio/tests/canvas-draft-service.spec.ts",
        "apps/studio/tests/visual-language-draft-service.spec.ts",
        "apps/studio/tests/program-design-service.spec.ts",
        "apps/studio/tests/brief-service.spec.ts",
        "apps/studio/tests/brief-recovery-admission-service.spec.ts",
        "apps/studio/tests/brief-uncertain-outcome-service.spec.ts",
        "apps/studio/src/App.tsx",
        "apps/studio/src/CreativeWorkspaces.tsx",
        "apps/studio/src/NarrativeWorkspace.tsx",
        "apps/studio/src/narrativeDraft.ts",
        "apps/studio/tests/narrative-service.spec.ts",
        "apps/studio/src/BriefWorkspace.tsx",
        "apps/studio/src/briefDraft.ts",
        "apps/studio/src/AudioWorkspace.tsx",
        "apps/studio/src/audioDraft.ts",
        "apps/studio/tests/audio-active-voice-service.spec.ts",
        "apps/studio/src/ProductionDesignWorkspace.tsx",
        "apps/studio/src/CameraReframeWorkbench.tsx",
        "apps/studio/src/cameraReframeDraft.ts",
        "apps/studio/src/cameraReframe.ts",
        "apps/studio/src/projectMutation.ts",
        "apps/studio/src/CanvasWorkspace.tsx",
        "apps/studio/src/canvasCamera.ts",
        "apps/studio/tests/camera-reframe-service.spec.ts",
        "apps/studio/tests/canvas-gesture-service.spec.ts",
        "apps/studio/src/CreativeAlternativesPanel.tsx",
        "apps/studio/src/DeliveryProfiles.tsx",
        "apps/studio/src/HistoryWorkspaces.tsx",
        "apps/studio/src/NativeOriginalMixReview.tsx",
        "apps/studio/src/ReviewWorkspace.tsx",
        "apps/studio/src/SceneBeatEditor.tsx",
        "apps/studio/src/audioNarrationProtectedMarkup.test.ts",
        "apps/studio/src/deliveryRecipe.test.ts",
        "apps/studio/src/deliveryRecipe.ts",
        "apps/studio/src/deliveryRecipeDraft.test.ts",
        "apps/studio/src/deliveryRecipeDraft.ts",
        "apps/studio/src/deliveryRecipeRecoveryReview.test.ts",
        "apps/studio/src/reviewDraft.test.ts",
        "apps/studio/src/reviewDraft.ts",
        "apps/studio/src/sceneBeatDraft.test.ts",
        "apps/studio/src/sceneBeatDraft.ts",
        "apps/studio/src/sceneBeatTiming.test.ts",
        "apps/studio/src/sceneBeatTiming.ts",
        "apps/studio/tests/delivery-recipe-authority-service.spec.ts",
        "apps/studio/tests/delivery-recipe-authority.spec.ts",
        "apps/studio/tests/delivery-recipe-recovery-review.spec.ts",
        "apps/studio/tests/delivery-recipe.spec.ts",
        "apps/studio/tests/fixtures/delivery-recipe-authority.html",
        "apps/studio/tests/fixtures/delivery-recipe-authority.tsx",
        "apps/studio/tests/fixtures/review-authority.html",
        "apps/studio/tests/fixtures/review-authority.tsx",
        "apps/studio/tests/fixtures/scene-beat-authority.html",
        "apps/studio/tests/fixtures/scene-beat-authority.tsx",
        "apps/studio/tests/native-original-mix-voice-binding.spec.ts",
        "apps/studio/tests/native-original-mix.spec.ts",
        "apps/studio/tests/review-preservation.spec.ts",
        "apps/studio/tests/review-service.spec.ts",
        "apps/studio/tests/scene-beat-draft-service.spec.ts",
        "apps/studio/tests/scene-beat-preservation.spec.ts",
        "apps/studio/tests/scene-beat-time-service.spec.ts",
        "apps/studio/src/SceneInspector.tsx",
        "apps/studio/src/deliveryProfileDraft.test.ts",
        "apps/studio/src/deliveryProfileDraft.ts",
        "apps/studio/src/sceneInspectorDraft.test.ts",
        "apps/studio/src/sceneInspectorDraft.ts",
        "apps/studio/tests/delivery-profile-authority-service.spec.ts",
        "apps/studio/tests/delivery-profile-authority.spec.ts",
        "apps/studio/tests/delivery-profile-preservation.spec.ts",
        "apps/studio/tests/fixtures/delivery-profile-authority.html",
        "apps/studio/tests/fixtures/delivery-profile-authority.tsx",
        "apps/studio/tests/fixtures/scene-inspector-authority.html",
        "apps/studio/tests/fixtures/scene-inspector-authority.tsx",
        "apps/studio/tests/scene-inspector-preservation.spec.ts",
        "apps/studio/tests/scene-inspector-service.spec.ts",
        "Cargo.toml",
        "SOURCE_LOCK.json",
        "rust-toolchain.toml",
        "crates/domain/Cargo.toml",
        "crates/storage/Cargo.toml",
        "crates/service/Cargo.toml",
        "apps/desktop/src-tauri/Cargo.toml",
        "apps/studio/package.json",
        "apps/studio/src/api.ts",
        "apps/studio/src/types.ts",
        "apps/studio/index.html",
        "apps/studio/vite.config.ts",
        "apps/studio/tsconfig.json",
        "apps/studio/tsconfig.app.json",
        "apps/studio/src/main.tsx",
        "apps/studio/src/styles.css",
        "apps/studio/src/fixture.ts",
        "apps/studio/src/ProjectLibraryDialog.tsx",
        "apps/studio/src/projectLibrary.ts",
        "apps/studio/src/project-library.css",
        "apps/studio/src/ProjectProjectionWorkspace.tsx",
        "apps/studio/src/projectProjection.ts",
        "apps/studio/src/projectProjection.test.ts",
        "apps/studio/src/projectMutation.test.ts",
        "apps/studio/tests/project-projection-service.spec.ts",
        "apps/studio/tests/project-projection.spec.ts",
        "apps/studio/tests/fixtures/project-projection.html",
        "apps/studio/tests/fixtures/project-projection.tsx",
        "apps/desktop/src-tauri/src/effect_grants.rs",
        "apps/desktop/src-tauri/src/project_library.rs",
        "apps/desktop/src-tauri/src/project_projection.rs",
        "apps/desktop/src-tauri/src/caption_export.rs",
        "apps/desktop/src-tauri/src/file_import.rs",
        "crates/domain/src/lib.rs",
        "crates/domain/src/canvas.rs",
        "crates/domain/src/history.rs",
        "crates/domain/src/creative.rs",
        "crates/service/src/lib.rs",
        "crates/service/src/project_creation.rs",
        "crates/service/src/project_projection.rs",
        "crates/storage/src/lib.rs",
        "crates/storage/src/project_creation.rs",
        "crates/storage/src/project_projection.rs",
        "tooling/ci_canvas_service.py",
        "tooling/tests/test_ci_canvas_service.py",
    )
    paths = tuple(dict.fromkeys((*paths, *CREATIVE_PRODUCTION_SOURCE_PATHS, *BRANCH_CLAIM_SOURCE_PATHS,
                                 *BRANCH_COMBINATION_SOURCE_PATHS,
                                 "tooling/vite_editor_security_probe.mjs")))
    if is_browser_scope(selection):
        paths = tuple(dict.fromkeys((*paths, *BROWSER_SOURCE_PATHS)))
    if selection == "workflow-preview-browser":
        paths = tuple(dict.fromkeys((*paths, *WORKFLOW_PREVIEW_SOURCE_PATHS)))
    if selection == PARTIAL_ORACLE_SCOPE:
        partial_oracle_selections()
        paths = tuple(dict.fromkeys((*paths, *PARTIAL_ORACLE_SOURCE_PATHS)))
    if selection == WINDOW_FRONTEND_SCOPE:
        window_frontend_profile()
        paths = tuple(dict.fromkeys((*paths, *WINDOW_FRONTEND_SOURCE_PATHS)))
    if selection == CAMERA_EIGHT_SCOPE:
        paths = tuple(dict.fromkeys((*paths, *(name for name, _ in CAMERA_EIGHT_HISTORICAL_INPUT_PINS))))
    if selection == PHASE1_SERVICE47_SCOPE:
        phase1_service47_selections()
        paths = tuple(dict.fromkeys((*paths, *PHASE1_SERVICE47_SOURCE_PATHS)))
    files = {}
    for name in paths:
        original = subprocess.check_output(["git", "show", head + ":" + name], cwd=repo, timeout=30)
        # Reject an aliased parent even when its current bytes happen to match.
        archive_ancestors(repo / name)
        observed = digest(repo / name)
        require(observed == {"bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()},
                "source differs from Git: " + name)
        files[name] = observed
    result = {"checkout": head, "tree": git("rev-parse", "HEAD^{tree}"), "files": files}
    if selection == WINDOW_FRONTEND_SCOPE:
        result["window_frontend_binding"] = window_frontend_source_binding(repo, result)
    if selection == CAMERA_EIGHT_SCOPE:
        result["camera_eight_binding"] = camera_eight_source_binding(repo, result)
    return result


BRANCH_COMBINATION_ADMISSION_ENV = "MOTIONWRIGHT_BRANCH_COMBINATION_ADMITTED"

PROCESS_ENVIRONMENT_KEYS = ("CARGO_TARGET_DIR", "PLAYWRIGHT_JSON_OUTPUT_FILE",
                            "MOTIONWRIGHT_SERVICE_TEST_BIN", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR")
PROCESS_OBSERVATION_SCHEMA = "motionwright.studio-command-process-observation/2"


class UnknownObservation(ValueError):
    def __init__(self, message, command):
        super().__init__(message)
        self.command = command


def execution_unknown(command):
    value = str(command.get("observation", ""))
    return value.startswith("UNKNOWN") or value == "TIMED_OUT_NO_RETRY"


def owned_process_group_state(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return "ABSENT"
    return "PRESENT"


def stop_observed_process(process, owned_group):
    errors = []
    for force in (False, True):
        try:
            if owned_group:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif process.poll() is None:
                process.kill() if force else process.terminate()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
        try:
            process.wait(timeout=2)
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
        try:
            if process.poll() is not None and (not owned_group or owned_process_group_state(process.pid) == "ABSENT"):
                break
        except BaseException as error:
            errors.append(type(error).__name__ + ": " + str(error))
    return errors


def require_observed_command(command):
    if execution_unknown(command):
        raise UnknownObservation("uncertain command; no further commands or readback", command)
    require(command.get("process_observation_schema") == PROCESS_OBSERVATION_SCHEMA
            and command.get("process_boundary") == "OWNED_POSIX_SESSION_GROUP"
            and type(command.get("pid")) is int and command["pid"] > 0
            and command.get("owned_group_after_wait") == "ABSENT"
            and command.get("escaped_descendants") == "NOT_OBSERVED"
            and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and type(command.get("exit_code")) is int and not command.get("observation"),
            "owned process group exit was not observed; no closed execution credit")


def execute(argv, cwd, env, output, label, timeout, *, stdin_original=None, deadline=None, maximum_file_bytes=None, maximum_stream_bytes=None):
    env = dict(env)
    env.pop(PHASE1_SERVICE47_TOKEN_ENV, None)
    env.pop(PHASE1_SERVICE47_DEADLINE_ENV, None)
    admission = deadline.admit(label, timeout) if deadline is not None else None
    if phase1_branch_admission_label(label):
        require(env.get(BRANCH_COMBINATION_ADMISSION_ENV) == "1", "branch combination admission is absent")
    else:
        # Never inherit the admission into a build, unrelated selector or helper.
        env.pop(BRANCH_COMBINATION_ADMISSION_ENV, None)
    observed_environment = (*PROCESS_ENVIRONMENT_KEYS, *((BRANCH_COMBINATION_ADMISSION_ENV,)
                            if phase1_branch_admission_label(label) else ()))
    if env.get("MOTIONWRIGHT_GRAPH_PRODUCER_RUN") == "1":
        observed_environment += ("CARGO_INCREMENTAL", "CARGO_PROFILE_DEV_DEBUG", "CARGO_PROFILE_TEST_DEBUG",
            "SEMWRIGHT_CHECKOUT", "SEMWRIGHT_TEST_MANIM_RUNTIME", "SEMWRIGHT_TEST_PYTHON", "SEMWRIGHT_TEST_FFMPEG")
    child_limits = None
    if maximum_stream_bytes is not None:
        require((label.startswith("graph-") or label == "phase1-service-preflight")
                and type(maximum_stream_bytes) is int and 1024 <= maximum_stream_bytes <= 8 * 1024 * 1024,
                "Graph bounded original raw stream")
    if maximum_file_bytes is not None:
        require(os.name == "posix" and (label in ("graph-cases", "phase1-service-preflight") and maximum_file_bytes == 4 * 1024 * 1024
                or label == "graph-runtime-download" and maximum_file_bytes == 128 * 1024 * 1024),
                "Graph fixed actual Store file-size limit")
        def child_limits():
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum_file_bytes, maximum_file_bytes))
    owned_group = os.name == "posix"
    command = {"argv": argv, "cwd": str(cwd), "started_utc": datetime.now(timezone.utc).isoformat(),
               "process_observation_schema": PROCESS_OBSERVATION_SCHEMA,
               "process_boundary": "OWNED_POSIX_SESSION_GROUP" if owned_group else "DIRECT_PROCESS_ONLY",
               "pid": None, "owned_group_after_wait": "NOT_OBSERVED", "escaped_descendants": "NOT_OBSERVED",
               "selected_environment": {key: env.get(key) for key in observed_environment},
               "cleanup_errors": []}
    if deadline is not None:
        command["deadline_admission"] = admission
    if env.get("MOTIONWRIGHT_GRAPH_PRODUCER_RUN") == "1":
        require(isinstance(deadline, GraphOriginalClock), "Graph process requires its original job clock")
        actual_job = next(row for row in deadline.original["jobs"]["jobs"]
                          if row["name"] == "Real SQLite and Studio / " + GRAPH_SCOPE)
        command["graph_job"] = {"source_sha": deadline.original["binding"]["GITHUB_SHA"],
            "run_id": deadline.original["binding"]["GITHUB_RUN_ID"], "run_attempt": 1,
            "job_id": actual_job["id"], "deadline_original": deadline.original_ref}
    process = None
    if stdin_original is not None:
        require(label == "phase1-service-preflight" and stdin_original == output / (label + ".stdin.original"),
                "only the bounded Phase1 SDK preflight accepts a retained stdin original")
    try:
        with (output / (label + ".stdout.log")).open("xb") as out, (output / (label + ".stderr.log")).open("xb") as err, \
                (phase1_service47_stdin(stdin_original, command) if stdin_original is not None else nullcontext(None)) as inp:
            if deadline is not None:
                command["deadline_admission"] = deadline.admit(label, timeout)
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=env,
                                           stdout=subprocess.PIPE if maximum_stream_bytes is not None else out,
                                           stderr=subprocess.PIPE if maximum_stream_bytes is not None else err,
                                           start_new_session=owned_group, **({"stdin": inp} if stdin_original is not None else {}),
                                           **({"preexec_fn": child_limits} if child_limits is not None else {}))
            except (FileNotFoundError, PermissionError) as error:
                command.update(exit_code=None, observation="NOT_STARTED_LAUNCH_ERROR", error=str(error))
            except OSError as error:
                command.update(exit_code=None, observation="UNKNOWN_PROCESS_NO_RETRY",
                               uncertainty_reason="SPAWN_HANDLE_NOT_OBSERVED", error=type(error).__name__ + ": " + str(error))
            else:
                command["pid"] = process.pid
                drains = []
                if maximum_stream_bytes is not None:
                    import threading
                    # The existing owned process remains the only producer.
                    # Charge each raw byte before writing; an exhausted stream
                    # is drained without retention and permanently marks UNKNOWN.
                    def capture(pipe, destination, observation):
                        try:
                            while block := pipe.read(65536):
                                if observation["error"] is None and observation["bytes"] + len(block) <= maximum_stream_bytes:
                                    destination.write(block); observation["bytes"] += len(block)
                                else:
                                    observation["error"] = "ORIGINAL_STREAM_ALLOCATION_EXHAUSTED"
                            observation["eof"] = True
                        except BaseException as error:
                            observation["error"] = type(error).__name__ + ": " + str(error)
                        finally:
                            pipe.close()
                    for stream, destination in (("stdout", out), ("stderr", err)):
                        observation = {"maximum_bytes": maximum_stream_bytes, "bytes": 0, "eof": False, "error": None}
                        thread = threading.Thread(target=capture, args=(getattr(process, stream), destination, observation), daemon=True)
                        drains.append((stream, thread, observation)); thread.start()
                if deadline is not None:
                    deadline.active_process = process
                try:
                    command["exit_code"] = process.wait(timeout=timeout)
                    if owned_group:
                        command["owned_group_after_wait"] = owned_process_group_state(process.pid)
                        if command["owned_group_after_wait"] != "ABSENT":
                            command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                                           uncertainty_reason="OWNED_GROUP_PRESENT_AFTER_DIRECT_EXIT")
                    if not execution_unknown(command):
                        command["diagnostic_custody"] = "DIRECT_PROCESS_EXIT_OBSERVED"
                except subprocess.TimeoutExpired:
                    command.update(exit_code=None, observation="UNKNOWN_TIMEOUT_NO_RETRY",
                                   uncertainty_reason="DIRECT_WAIT_TIMEOUT", timeout_seconds=timeout)
                except BaseException as error:
                    command.setdefault("exit_code", None)
                    command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                                   uncertainty_reason="POST_LAUNCH_OBSERVATION_ERROR",
                                   error=type(error).__name__ + ": " + str(error))
                if execution_unknown(command):
                    try:
                        command["cleanup_errors"] = stop_observed_process(process, owned_group)
                    except BaseException as error:
                        command["cleanup_errors"].append(type(error).__name__ + ": " + str(error))
                if maximum_stream_bytes is not None:
                    for stream, thread, observation in drains:
                        thread.join(timeout=1)
                        if thread.is_alive() or not observation["eof"] or observation["error"] is not None:
                            observation["error"] = observation["error"] or "ORIGINAL_STREAM_EOF_NOT_OBSERVED"
                            command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="BOUNDED_ORIGINAL_STREAM_INCOMPLETE")
                    command["bounded_original_streams"] = {stream: dict(observation) for stream, _, observation in drains}
                    if execution_unknown(command):
                        command["cleanup_errors"].extend(stop_observed_process(process, owned_group))
    except Phase1Service47BudgetDenied as error:
        command.update(exit_code=None, observation="NOT_STARTED_INSUFFICIENT_ORIGINAL_BUDGET",
                       budget_denial=error.observation, error=str(error))
    except BaseException as error:
        if process is None and isinstance(error, OSError) and not execution_unknown(command):
            raise
        if execution_unknown(command):
            command.setdefault("secondary_observation_errors", []).append(type(error).__name__ + ": " + str(error))
        else:
            command.setdefault("exit_code", None)
            command.update(observation="UNKNOWN_PROCESS_NO_RETRY",
                           uncertainty_reason="OBSERVATION_OR_STREAM_FINALIZATION_ERROR",
                           error=type(error).__name__ + ": " + str(error))
        if process is not None:
            try:
                command["cleanup_errors"].extend(stop_observed_process(process, owned_group))
            except BaseException as cleanup_error:
                command["cleanup_errors"].append(type(cleanup_error).__name__ + ": " + str(cleanup_error))
    if deadline is not None:
        if execution_unknown(command):
            deadline.unknown = True
        try:
            if process is None or owned_process_group_state(process.pid) == "ABSENT":
                deadline.active_process = None
        except BaseException as error:
            command.setdefault("secondary_observation_errors", []).append("deadline group custody: " + str(error))
            if not execution_unknown(command):
                command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="DEADLINE_GROUP_RECHECK_FAILED")
            deadline.unknown = True
    command["finished_utc"] = datetime.now(timezone.utc).isoformat()
    if stdin_original is not None:
        try:
            require(command["stdin"] == {"path": stdin_original.name, **archive_file_digest(stdin_original, 16384)},
                    "Phase1 preflight stdin changed")
        except BaseException as error:
            if execution_unknown(command):
                command.setdefault("secondary_observation_errors", []).append("stdin original: " + str(error))
            else:
                command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="STDIN_ORIGINAL_CHANGED",
                               error=type(error).__name__ + ": " + str(error))
    for stream in ("stdout", "stderr"):
        path = output / (label + "." + stream + ".log")
        try:
            command[stream] = archive_file_digest(path, ARCHIVE_LIMITS["file_bytes"])
        except BaseException as error:
            command[stream] = {"custody_error": type(error).__name__ + ": " + str(error)}
            if not execution_unknown(command):
                command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="STREAM_FINGERPRINT_FAILED")
    if execution_unknown(command):
        command["diagnostic_custody"] = "UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED"
    try:
        write_new(output / (label + ".command.json"), command)
    except BaseException as error:
        if not execution_unknown(command):
            command.update(observation="UNKNOWN_CUSTODY_NO_RETRY", uncertainty_reason="COMMAND_RECEIPT_PERSISTENCE_FAILED")
        command.update(diagnostic_custody="UNSEALED_SNAPSHOT_DESCENDANTS_NOT_OBSERVED",
                       receipt_error=type(error).__name__ + ": " + str(error))
        if deadline is not None:
            deadline.unknown = True
        raise UnknownObservation("command receipt could not be retained; no retry", command) from error
    if deadline is not None and execution_unknown(command):
        deadline.unknown = True
    return command



ARCHIVE_ROOTS = ("e", "tools")
ARCHIVE_LIMITS = {"members": 20000, "file_bytes": 512 * 1024 * 1024,
                  "total_bytes": 2 * 1024 * 1024 * 1024, "path_bytes": 1024}
ARCHIVE_CHUNK = 1024 * 1024


def archive_member_name(name):
    require(isinstance(name, str) and name and len(name.encode("utf-8")) <= ARCHIVE_LIMITS["path_bytes"],
            "archive member name exceeds bound")
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    require(len(parts) <= 64 and parts[0] in ARCHIVE_ROOTS
            and all(part not in ("", ".", "..") and not part.endswith((" ", "."))
                    and part.split(".")[0].upper() not in reserved for part in parts)
            and "\\" not in name and ":" not in name
            and all(ord(c) >= 32 and ord(c) != 127 for c in name),
            "unsafe archive member name")
    return name


def archive_stat(path):
    value = path.lstat()
    require(not stat.S_ISLNK(value.st_mode)
            and not getattr(value, "st_file_attributes", 0) & 0x400,
            "archive symlink or reparse point forbidden")
    require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode),
            "archive nonregular input forbidden")
    if stat.S_ISREG(value.st_mode):
        require(value.st_nlink == 1, "archive hard-linked input forbidden")
    return value


def archive_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def archive_ancestors(path):
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            archive_stat(parent)


def archive_file_digest(path, limit):
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
            "archive file exceeds bound")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive file changed before read")
        count, value = 0, hashlib.sha256()
        while block := stream.read(min(ARCHIVE_CHUNK, limit - count + 1)):
            count += len(block)
            require(count <= limit, "archive file grew beyond bound")
            value.update(block)
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "archive open file changed during read")
    require(count == before.st_size and archive_identity(archive_stat(path)) == archive_identity(before),
            "archive file changed during read")
    archive_ancestors(path)
    return {"bytes": count, "sha256": value.hexdigest()}


def archive_inventory(root):
    archive_ancestors(root)
    require(root.is_dir(), "archive evidence directory missing")
    entries, identities, folded, total = {}, {}, set(), 0
    pending = [root / name for name in reversed(ARCHIVE_ROOTS)]
    while pending:
        path = pending.pop()
        name = archive_member_name(path.relative_to(root).as_posix())
        require(len(entries) < ARCHIVE_LIMITS["members"] and name.casefold() not in folded,
                "archive member bound or alias collision")
        folded.add(name.casefold())
        value = archive_stat(path)
        identity = archive_identity(value)
        item = {"name": name, "kind": "directory" if stat.S_ISDIR(value.st_mode) else "file",
                "mode": stat.S_IMODE(value.st_mode)}
        if item["kind"] == "directory":
            children = []
            with os.scandir(path) as stream:
                for child in stream:
                    children.append(Path(child.path))
                    require(len(entries) + len(pending) + len(children) <= ARCHIVE_LIMITS["members"],
                            "archive member bound")
            require(archive_identity(archive_stat(path)) == identity, "archive directory changed")
            pending.extend(sorted(children, key=lambda p: p.name, reverse=True))
        else:
            item.update(archive_file_digest(path, ARCHIVE_LIMITS["file_bytes"]))
            total += item["bytes"]
            require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
        entries[name], identities[name] = item, identity
    require(all(entries.get(name, {}).get("kind") == "directory" for name in ARCHIVE_ROOTS),
            "archive e/tools directories required")
    return {"entries": dict(sorted(entries.items())), "identities": identities, "file_bytes": total}


def archive_json(root, inventory, name):
    item = inventory["entries"].get(name)
    require(item is not None and item["kind"] == "file" and item["bytes"] <= 64 * 1024 * 1024,
            "bounded original JSON required: " + name)
    path = root / name
    require(archive_file_digest(path, 64 * 1024 * 1024) == {k: item[k] for k in ("bytes", "sha256")},
            "original JSON changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON replaced before parse")
        raw = stream.read(item["bytes"] + 1)
        require(archive_identity(os.fstat(stream.fileno())) == inventory["identities"][name],
                "original JSON changed during parse")
    require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
            "original JSON changed during parse")
    return json.loads(raw)



def selected_service_store_bindings(root, inventory, selections, captures, binary_path, required_labels):
    """Bind every new service case to its original closed store and final oracle."""
    known = {row["manifest"] for row in captures}
    require(isinstance(required_labels, set) and required_labels
            and required_labels <= ({row[0] for row in CREATIVE_PRODUCTION_SELECTIONS} | {"review", "branch-authoring", "claim-source", "branch-combination"}),
            "unknown case/store binding scope")
    bound, used = [], set()
    for selection in selections:
        label = selection["label"]
        if label not in required_labels:
            continue
        document = archive_json(root, inventory, "e/" + label + ".json")
        cases = []

        def collect(suites):
            for suite in suites:
                cases.extend(suite.get("specs", []))
                collect(suite.get("suites", []))

        collect(document["suites"])
        for case in cases:
            attachments = case["tests"][0]["results"][0].get("attachments", [])
            evidence_name = label + "-evidence" if label in ("branch-authoring", "branch-combination") else label + "-service-evidence"
            originals = {}
            for name in ("closed-store-outcomes", evidence_name):
                found = [item for item in attachments if item.get("name") == name]
                require(len(found) == 1 and found[0].get("contentType") == "application/json",
                        "service case original attachment missing or duplicate")
                path = Path(found[0].get("path", ""))
                relative = archive_member_name(path.relative_to(root).as_posix())
                parts = relative.split("/")
                require(parts[:2] == ["e", label + "-test-results"],
                        "service case attachment outside its output")
                if len(parts) == 5 and parts[3] == "attachments":
                    require(re.fullmatch(re.escape(name) + r"-[0-9a-f]{40}\.json", parts[4]) is not None,
                            "service case reporter attachment name differs")
                    original_name = "/".join((*parts[:3], name + ".json"))
                else:
                    require(len(parts) == 4 and parts[3] == name + ".json",
                            "service case attachment outside its output")
                    original_name = relative
                value = archive_json(root, inventory, relative)
                if original_name != relative:
                    original = archive_json(root, inventory, original_name)
                    require(all(inventory["entries"][relative][key] == inventory["entries"][original_name][key]
                                for key in ("bytes", "sha256")) and value == original,
                            "service case reporter copy differs from original")
                    value = original
                originals[name] = (original_name, value)
            outcome_name, outcome = originals["closed-store-outcomes"]
            case_root = outcome_name.rsplit("/", 1)[0]
            manifest_name = case_root + "/closed-store/manifest.json"
            require(manifest_name in known and manifest_name not in used,
                    "service case closed store missing or shared by different cases")
            used.add(manifest_name)
            manifest = archive_json(root, inventory, manifest_name)
            oracle = archive_json(root, inventory, case_root + "/closed-store/oracle.json")
            require(outcome.get("schemaVersion") == 1 and outcome.get("errors") == []
                    and outcome.get("capture") == manifest and outcome.get("oracle") == oracle,
                    "service case finalization differs from original closed store")
            preparation, closures = outcome.get("preparation"), outcome.get("closures")
            require(isinstance(preparation, list) and preparation
                    and all(item.get("status") == "fulfilled" and not item.get("error")
                            for item in preparation), "service case preparation incomplete")
            require(isinstance(closures, list) and closures
                    and all(item.get("status") == "fulfilled" and not item.get("error")
                            and item.get("taints") == [] for item in closures)
                    and [{key: item.get(key) for key in ("databasePath", "exits", "taints")}
                         for item in closures] == manifest.get("lifecycle"),
                    "service case closures differ from captured helper lifecycle")
            require(oracle.get("schemaVersion") == 1 and oracle.get("journalComplete") is True
                    and oracle.get("journalAfterRevisionExclusive") == 0
                    and isinstance(oracle.get("journal"), list) and isinstance(oracle.get("project"), dict),
                    "service case complete stored oracle missing")
            evidence_path, evidence = originals[evidence_name]
            require(evidence_path.rsplit("/", 1)[0] == case_root
                    and evidence.get("primaryFailure") is None and evidence.get("failures") == []
                    and evidence.get("oracle" if label == "branch-combination" else "canonicalOracle") == oracle
                    and evidence.get("final") == oracle["project"],
                    "service case final Project or original failure differs")
            # These specs explicitly choose info.outputPath('service', 'studio.sqlite3').
            # Their configured TMPDIR is retained in the invocation, but is not the DB location.
            source_directory = root / case_root / "service"
            database_path = str(source_directory / "studio.sqlite3")
            require(manifest.get("sourceDirectory") == str(source_directory)
                    and manifest.get("databaseRelativePath") == "studio.sqlite3",
                    "service case captured source directory or database differs")
            peer_titles = {
                "an unknown Production plan committed result survives reload without replay and needs a fresh history review",
                "an unknown Production plan matching-peer result survives reload without replay and needs a fresh history review",
            }
            peer_titles.update(('opaque source attachment pre-dispatch delivery interruption stays UNKNOWN on N until a real peer advances SQLite, with no original replay', 'Scene boundary keeps invalid raw endpoint JSON and its prior source when a real peer makes explicit rebind stale'))
            participants = 2 if label == "production-design" and case["title"] in peer_titles else 1
            if label == "branch-combination":
                participants = 2 if case["title"] in ('two real strategies produce a persisted inactive candidate, then editable undo preserves peer style and historical reviews', 'an independent peer wins CAS after preview; rejected combination retains raw and never adds a phantom candidate') else 1
            if (label, case["title"]) in (
                ("review", "pre-dispatch review transport interruption keeps the saved base UNKNOWN until an independent SQLite peer advances it without replay"),
                ("branch-authoring", "an unchanged SQLite branch base stays UNKNOWN until a real peer advances it, preserving later input without replay"),
            ):
                participants = 2
            if label == "branch-combination" or (label in ("review", "branch-authoring") and participants == 2):
                require(isinstance(evidence.get("service"), dict) and isinstance(evidence.get("peers"), list)
                        and len(evidence["peers"]) == participants - 1 and "services" not in evidence,
                        "service and original peer participant schema differs")
                services = [evidence["service"], *evidence["peers"]]
            elif label in ("review", "branch-authoring", "claim-source"):
                require(isinstance(evidence.get("service"), dict) and "services" not in evidence,
                        "Review original singular service authority differs")
                services = [evidence["service"]]
            else:
                services = evidence.get("services")
            require(isinstance(services, list) and len(services) == participants
                    and all(isinstance(item, dict) and item.get("binaryPath") == str(binary_path)
                            and item.get("databasePath") == database_path for item in services),
                    "service case participant inventory or binary/database authority differs")
            declared_lifecycle = [{"databasePath": item["databasePath"], "exits": item.get("processExits"),
                                   "taints": item.get("lifecycleTaints")} for item in services]
            require(declared_lifecycle == manifest.get("lifecycle"),
                    "service case original process exits or taints differ from captured lifecycle")
            bound.append({"label": label, "file": case["file"], "title": case["title"],
                          "manifest": manifest_name, "outcomes": outcome_name,
                          "original_evidence": evidence_path, "services": participants,
                          "source_directory": str(source_directory), "database_path": database_path,
                          "binary_path": str(binary_path),
                          "technical_scope": "ORIGINAL_CLOSED_STORE_AND_DECLARED_ORACLE; NO_ARCHIVE_REOPEN"})
    require(len(bound) == sum(item["expected_tests"] for item in selections if item["label"] in required_labels),
            "service closed store case inventory differs")
    return bound


def creative_production_store_bindings(root, inventory, selections, captures, binary_path):
    return selected_service_store_bindings(
        root, inventory, selections, captures, binary_path,
        {row[0] for row in CREATIVE_PRODUCTION_SELECTIONS})


def archive_closed_bindings(root, inventory, source):
    if archive_json(root, inventory, "e/receipt.json").get("selection_plan", {}).get("requested") == GRAPH_SCOPE:
        return graph_archive_bindings(root, inventory, source)
    receipt = archive_json(root, inventory, "e/receipt.json")
    browser_mode = is_browser_scope(receipt.get("selection_plan", {}).get("requested"))
    tools = ("node", "npm", "python") if browser_mode else ("rustc", "cargo", "node", "npm", "python")
    for tool in tools:
        identity = inventory["entries"].get("tools/" + tool + ".txt", {})
        require(identity.get("kind") == "file" and identity.get("bytes", 0) > 0,
                "archive tool identity missing")
    require(receipt.get("status") == "PASS", "unsealed failed or incomplete service execution")
    require(receipt.get("source_before") == receipt.get("source_after") == source
            and archive_json(root, inventory, "e/source-before.json") == source,
            "archive source binding differs")
    plan = receipt.get("selection_plan", {})
    require(plan == selection_plan(plan.get("requested")), "archive selection binding differs")
    required = selected_selections(plan["requested"])
    camera_mode = plan["requested"] == CAMERA_EIGHT_SCOPE
    phase1_mode = plan["requested"] == PHASE1_SERVICE47_SCOPE
    selections = receipt.get("selections", [])
    require([x.get("label") for x in selections] == [x[0] for x in required],
            "archive selected roster differs")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for field, variable in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"),
                                ("job", "GITHUB_JOB")):
            require(receipt.get(field) == os.environ.get(variable) and receipt.get(field),
                    "archive run identity differs")

    def command_closed(command, label):
        require_observed_command(command)
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                and not command.get("observation"),
                "unsealed command or unobserved descendants")
        require(archive_json(root, inventory, "e/" + label + ".command.json") == command,
                "archive original command differs")
        for stream in ("stdout", "stderr"):
            item = inventory["entries"].get("e/" + label + "." + stream + ".log", {})
            require(item.get("kind") == "file"
                    and command.get(stream) == {k: item.get(k) for k in ("bytes", "sha256")},
                    "archive original stream differs")

    if browser_mode:
        require(receipt.get("execution_scope") == "SYNTHETIC_BROWSER_CONTRACTS"
                and receipt.get("service_runtime") == receipt.get("service_build") == "NOT_RUN"
                and receipt.get("service_target_absent") is True
                and not any(key in receipt for key in ("build", "service_binary", "retained_service_binary")),
                "browser receipt claims service execution")
        require(receipt.get("service_environment") == {
            "MOTIONWRIGHT_SERVICE_TEST_BIN": "ABSENT", "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "ABSENT"},
            "browser service authority was not excluded")
        require(all(receipt.get(key) == "NOT_RUN" for key in
                    ("installed_tauri", "native_render", "human_acceptance")),
                "browser receipt claims native or installed execution")
        repo = root.parent / "g"
        cwd = str(repo / "apps/studio")
        node = receipt.get("node", {}).get("path")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        require(isinstance(node, str) and node and receipt.get("playwright_cli", {}).get("path") == str(cli),
                "browser tool paths differ")
        for identity in (receipt.get("node", {}), receipt.get("playwright_cli", {})):
            require(type(identity.get("bytes")) is int and identity["bytes"] > 0
                    and re.fullmatch("[0-9a-f]{64}", identity.get("sha256", "")) is not None,
                    "browser executable identity missing")
        discovery = receipt.get("discovery", {})
        command_closed(discovery.get("command", {}), "browser-discovery")
        require(discovery.get("status") == "LISTED_NOT_EXECUTED"
                and discovery["command"].get("argv") == browser_command(node, cli, root / "e", scope=plan["requested"])
                and discovery["command"].get("cwd") == cwd
                and discovery["command"].get("selected_environment") == {
                    **dict.fromkeys(PROCESS_ENVIRONMENT_KEYS),
                    "PLAYWRIGHT_JSON_OUTPUT_FILE": str(root / "e/browser-discovery.json")},
                "browser discovery command differs")
        original = inventory["entries"].get("e/browser-discovery.json", {})
        require(discovery.get("original_report") == {key: original.get(key) for key in ("bytes", "sha256")}
                and discovery.get("verified") == validate_browser_discovery(
                    archive_json(root, inventory, "e/browser-discovery.json"), plan["requested"]),
                "browser discovery original differs")
    else:
        repo, target = root.parent / "g", root / "t"
        cwd = str(repo / "apps/studio")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        cargo, node = receipt.get("cargo", {}).get("path"), receipt.get("node", {}).get("path")
        require(receipt.get("target_directory") == str(target), "service target directory differs")
        for name in ("cargo", "node"):
            identity = receipt.get(name, {})
            require(isinstance(identity.get("path"), str) and Path(identity["path"]).is_absolute(),
                    "service tool path missing: " + name)
            require(observed_tool(Path(identity["path"])) == identity, "service tool identity differs: " + name)
        require(receipt.get("playwright_cli") == digest(cli), "service Playwright CLI identity differs")
        binary_path = target / "debug/examples/studio_review_session"
        require(receipt.get("service_binary", {}).get("path") == str(binary_path), "service binary path differs")
        build = receipt.get("build", {})
        command_closed(build, "build")
        require(build.get("argv") == [cargo, "build", "--locked", "-p", "motionwright-service",
                                       "--example", "studio_review_session", *(["--jobs", "1"] if camera_mode or phase1_mode else []),
                                       *(["--message-format=json"] if phase1_mode else [])]
                and build.get("cwd") == str(repo)
                and build.get("selected_environment") == service_environment_binding(target),
                "service build invocation differs")
    if plan["requested"] == PARTIAL_ORACLE_SCOPE:
        discovered = receipt.get("partial_discovery")
        require(isinstance(discovered, list) and [row.get("label") for row in discovered]
                == [row[0] for row in required], "partial oracle discovery roster is incomplete")
        for observed, (label, filename, titles) in zip(discovered, required):
            command = observed.get("command", {})
            command_closed(command, label + "-discovery")
            environment = service_environment_binding(target, root / "e", label, binary_path)
            environment["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(root / "e" / (label + "-discovery.json"))
            require(command.get("argv") == service_command(node, cli, root / "e", (label, filename, titles), listing=True)
                    and command.get("cwd") == cwd and command.get("selected_environment") == environment,
                    "partial oracle discovery invocation differs")
            original = inventory["entries"].get("e/" + label + "-discovery.json", {})
            require(observed.get("status") == "LISTED_NOT_EXECUTED"
                    and observed.get("original_report") == {key: original.get(key) for key in ("bytes", "sha256")}
                    and observed.get("verified") == partial_oracle_discovery(
                        archive_json(root, inventory, "e/" + label + "-discovery.json"), filename, titles),
                    "partial oracle discovery original differs")
        partial_oracle_list_order(discovered, selections)
    camera_lists = camera_eight_archive_lists(root, inventory, receipt, source, command_closed) if camera_mode else {}
    phase1_lists = phase1_service47_archive_inputs(root, inventory, receipt, source, command_closed) if phase1_mode else {}
    for selection, (_, filename, titles) in zip(selections, required):
        label = selection["label"]
        command_closed(selection.get("command", {}), label)
        if browser_mode:
            require(selection["command"].get("argv") == browser_command(
                node, cli, root / "e", (label, filename, titles), scope=plan["requested"])
                and selection["command"].get("cwd") == cwd
                and selection["command"].get("selected_environment") == {
                    **dict.fromkeys(PROCESS_ENVIRONMENT_KEYS),
                    "PLAYWRIGHT_JSON_OUTPUT_FILE": str(root / "e" / (label + ".json"))},
                "browser selected command differs")
        else:
            require(selection["command"].get("argv") == service_command(
                node, cli, root / "e", (label, filename, titles), scope=CAMERA_EIGHT_SCOPE if camera_mode else PHASE1_SERVICE47_SCOPE if phase1_mode else None)
                and selection["command"].get("cwd") == cwd
                and selection["command"].get("selected_environment") == service_environment_binding(
                    target, root / "e", label, binary_path), "service selected invocation differs")
        original = inventory["entries"].get("e/" + label + ".json", {})
        require(selection.get("status") == "PASS" and selection.get("expected_tests") == len(titles)
                and selection.get("original_report") == {k: original.get(k) for k in ("bytes", "sha256")},
                "archive report binding differs")
        original_document = archive_json(root, inventory, "e/" + label + ".json")
        if phase1_mode:
            verified = phase1_service47_report(original_document, filename, titles, listing=False, expected=phase1_lists[label])
        elif camera_mode:
            verified = camera_eight_report(original_document, filename, titles, listing=False, expected=camera_lists[label])
            intent_name = "e/" + label + "-intent.json"
            intent = camera_eight_intent(label, titles, selection["command"]["argv"], Path(cwd), source, receipt["service_binary"])
            require(archive_json(root, inventory, intent_name) == intent
                    and selection.get("intent") == {key: inventory["entries"][intent_name][key] for key in ("bytes", "sha256")}
                    and selection.get("launch_requested") is True and selection.get("attempted") is True
                    and type(selection.get("original_pid")) is int and selection["original_pid"] > 0
                    and selection["original_pid"] == selection["command"].get("pid")
                    and type(selection.get("original_exit")) is int and selection["original_exit"] == 0
                    and type(selection.get("validated_cases")) is int and selection["validated_cases"] == len(titles),
                    "Camera original attempt or intent differs")
        else:
            verify = validate_browser_execution if browser_mode or plan["requested"] == PARTIAL_ORACLE_SCOPE else validate_report
            verified = verify(original_document, filename, titles)
        require(selection.get("verified") == verified, "archive original selected results differ")
    if camera_mode:
        require(receipt.get("camera_execution") == {"selected_cases": 8, "launch_requested_cases": 8,
                "selected_cases_in_started_processes": 8, "validated_cases": 8, "state": "VALIDATED_ALL_SELECTED_ORIGINALS",
                "retry": "FORBIDDEN", "formal_acceptance": False}, "Camera aggregate execution is not complete")
    if browser_mode:
        window_units = None
        window_base = None
        if plan["requested"] == WINDOW_FRONTEND_SCOPE:
            window_units = window_frontend_archive_units(root, inventory, source, receipt)
            window_base = window_frontend_base_archive(root, inventory, source)
            require(receipt.get("phase_source_after") == [source, source],
                    "Window browser phase source readbacks differ")
        for name in inventory["entries"]:
            parts = name.split("/")
            require(not any(part.endswith("-stores") or part.startswith("executed-studio-service") for part in parts)
                    and not name.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".sqlite-shm")),
                    "browser archive contains service storage or executable evidence")
            if name.endswith("/manifest.json"):
                manifest = archive_json(root, inventory, name)
                require(not any(key in manifest for key in ("snapshotDirectory", "snapshotSha256", "databaseRelativePath")),
                        "browser archive contains a closed-service store claim")
        return {"receipt": {key: inventory["entries"]["e/receipt.json"][key] for key in ("bytes", "sha256")},
                "selection_plan": plan, "discovery": discovery["verified"], "captures": [],
                "service_runtime": "NOT_RUN", **({"window_units": window_units, "window_base": window_base} if window_units else {})}
    binary = receipt.get("retained_service_binary", {})
    require(binary.get("path") in ("executed-studio-service", "executed-studio-service.exe"),
            "archive retained executable name differs")
    retained = inventory["entries"].get("e/" + binary["path"], {})
    require(retained.get("kind") == "file"
            and {k: retained.get(k) for k in ("bytes", "sha256")} == {k: binary.get(k) for k in ("bytes", "sha256")}
            == {k: receipt.get("service_binary", {}).get(k) for k in ("bytes", "sha256")},
            "archive retained executable differs")
    captures = []
    for name in inventory["entries"]:
        if not name.endswith("/manifest.json"):
            continue
        manifest = archive_json(root, inventory, name)
        if not any(key in manifest for key in ("entries", "snapshotDirectory", "snapshotSha256")):
            continue
        entries = manifest.get("entries")
        require(manifest.get("schemaVersion") == 1 and isinstance(entries, list)
                and 0 < len(entries) <= ARCHIVE_LIMITS["members"], "invalid closed-store manifest")
        aggregate = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        require(manifest.get("sourceBeforeSha256") == manifest.get("sourceAfterSha256")
                == manifest.get("snapshotSha256") == aggregate, "closed-store aggregate differs")
        prefix = name.rsplit("/", 1)[0] + "/store"
        require(Path(manifest.get("snapshotDirectory", "")) == root / prefix,
                "closed-store snapshot path differs")
        actual = {}
        for member_name, item in inventory["entries"].items():
            if member_name != prefix and not member_name.startswith(prefix + "/"):
                continue
            relative = member_name[len(prefix):].lstrip("/")
            entry = {"path": relative, "kind": item["kind"]}
            if item["kind"] == "file":
                entry.update(sizeBytes=item["bytes"], sha256=item["sha256"])
            actual[relative] = entry
        require(all(isinstance(e, dict) and isinstance(e.get("path"), str) for e in entries),
                "closed-store entry malformed")
        expected = {e["path"]: e for e in entries}
        require(len(expected) == len(entries) and actual == expected,
                "complete closed-store files/directories differ")
        database = archive_member_name(prefix + "/" + manifest.get("databaseRelativePath", ""))
        require(inventory["entries"].get(database, {}).get("kind") == "file",
                "closed-store database missing")
        lifecycle = manifest.get("lifecycle")
        require(isinstance(lifecycle, list) and lifecycle, "closed-store lifecycle missing")
        for process in lifecycle:
            exits = process.get("exits")
            require(process.get("taints") == [] and isinstance(exits, list) and exits
                    and all(type(x.get("processId")) is int and x["processId"] > 0
                            and type(x.get("code")) is int and x["code"] == 0
                            and x.get("signal") is None for x in exits),
                    "unsealed closed-store process lifecycle")
        captures.append({"manifest": name, "sha256": inventory["entries"][name]["sha256"],
                         "snapshot_sha256": aggregate, "entries": len(entries),
                         "evidence_scope": manifest.get("evidenceScope")})
    result = {"receipt": {k: inventory["entries"]["e/receipt.json"][k] for k in ("bytes", "sha256")},
              "selection_plan": plan, "captures": captures}
    if camera_mode:
        result["camera_eight_cases"] = camera_eight_capture_bindings(root, inventory, captures)
    if plan["requested"] == PARTIAL_ORACLE_SCOPE:
        result["partial_oracle_cases"] = partial_oracle_store_bindings(
            root, inventory, selections, captures, receipt["service_binary"], source)
    if any(row["label"] in {item[0] for item in CREATIVE_PRODUCTION_SELECTIONS} for row in selections):
        result["creative_production_cases"] = creative_production_store_bindings(
            root, inventory, selections, captures, binary_path)
    if any(row["label"] == "review" for row in selections):
        result["review_cases"] = selected_service_store_bindings(
            root, inventory, selections, captures, binary_path, {"review"})
    if any(row["label"] in {item[0] for item in BRANCH_CLAIM_SELECTIONS} for row in selections):
        result["branch_claim_cases"] = selected_service_store_bindings(
            root, inventory, selections, captures, binary_path, {row[0] for row in BRANCH_CLAIM_SELECTIONS})
    return result


def write_closed_tar(root, target, inventory):
    with target.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, item in inventory["entries"].items():
                path = root / name
                require(archive_identity(archive_stat(path)) == inventory["identities"][name],
                        "archive source changed before copy")
                info = tarfile.TarInfo(name)
                info.mode, info.mtime = item["mode"], 0
                info.type = tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE
                if item["kind"] == "directory":
                    archive.addfile(info)
                else:
                    archive_ancestors(path)
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(path, flags), "rb") as original:
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source replaced before copy")
                        info.size = item["bytes"]
                        archive.addfile(info, original)
                        require(archive_identity(os.fstat(original.fileno())) == inventory["identities"][name],
                                "archive source changed during copy")
        stream.flush()
        os.fsync(stream.fileno())



def archive_header_bounds(path):
    """Check physical TAR headers before tarfile can allocate extended metadata."""
    last_end, count, pending_pax = 0, 0, False
    with path.open("rb") as stream:
        while True:
            header = stream.read(512)
            require(len(header) == 512, "truncated archive header")
            if header == bytes(512):
                require(not pending_pax and stream.read(512) == bytes(512), "invalid archive end marker")
                tail = stream.read(11265)
                require(len(tail) <= 10240 and not any(tail) and not stream.read(1),
                        "archive trailing data exceeds bound")
                return last_end
            member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
            count += 1
            require(count <= ARCHIVE_LIMITS["members"] * 2 and member.size >= 0,
                    "archive physical header bound")
            if member.type == tarfile.XHDTYPE:
                require(not pending_pax and member.size <= 4096, "archive extended metadata exceeds bound")
                pending_pax = True
            else:
                require(member.type in (tarfile.DIRTYPE, tarfile.REGTYPE)
                        and member.size <= ARCHIVE_LIMITS["file_bytes"]
                        and (member.type != tarfile.DIRTYPE or member.size == 0),
                        "archive physical member type or size")
                pending_pax = False
            stream.seek(((member.size + 511) // 512) * 512, 1)
            require(stream.tell() <= path.stat().st_size, "truncated archive payload")
            last_end = stream.tell()


def verify_closed_tar(path, inventory):
    archive_ancestors(path)
    before = archive_identity(archive_stat(path))
    size = archive_stat(path).st_size
    require(size <= ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264,
            "archive envelope exceeds bound")
    physical_end = archive_header_bounds(path)
    expected, seen, folded, total, last_end = inventory["entries"], set(), set(), 0, 0
    with tarfile.open(path, mode="r|") as archive:
        for member in archive:
            name = archive_member_name(member.name)
            require(len(seen) < ARCHIVE_LIMITS["members"] and name not in seen
                    and name.casefold() not in folded and name in expected,
                    "duplicate, unexpected or excessive archive member")
            seen.add(name)
            folded.add(name.casefold())
            item = expected[name]
            require(member.type == (tarfile.DIRTYPE if item["kind"] == "directory" else tarfile.REGTYPE)
                    and not member.linkname and member.sparse is None
                    and set(member.pax_headers) <= {"path"},
                    "archive member type or extension differs")
            require(member.mode == item["mode"] and member.uid == member.gid == 0
                    and member.uname == member.gname == "" and member.mtime == 0,
                    "archive member metadata differs")
            require(member.size == item.get("bytes", 0) and member.size <= ARCHIVE_LIMITS["file_bytes"],
                    "archive member size differs")
            if item["kind"] == "file":
                value, count = hashlib.sha256(), 0
                with archive.extractfile(member) as original:
                    while block := original.read(min(ARCHIVE_CHUNK, member.size - count + 1)):
                        count += len(block)
                        require(count <= member.size, "archive member grew beyond declared size")
                        value.update(block)
                require(count == member.size and value.hexdigest() == item["sha256"],
                        "archive member digest differs")
                total += count
                require(total <= ARCHIVE_LIMITS["total_bytes"], "archive total byte bound")
            last_end = member.offset_data + ((member.size + 511) // 512) * 512
    require(seen == set(expected) and total == inventory["file_bytes"],
            "archive complete member structure differs")
    require(physical_end == last_end and 1024 <= size - last_end <= 11264 and (size - last_end) % 512 == 0,
            "archive end marker or padding differs")
    with path.open("rb") as stream:
        stream.seek(last_end)
        require(not any(stream.read()), "archive trailing nonzero data")
    observed = archive_file_digest(path, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
    require(archive_identity(archive_stat(path)) == before, "archive changed during verification")
    return {"members": len(seen), "file_bytes": total, "complete_structure": True, "archive_digest": observed}


def retain_closed_archive(repo, root):
    output = root / "retained"
    archive_ancestors(output)
    require(not output.exists(), "fresh archive output directory required")
    output.mkdir()
    report = {"schema": "motionwright.studio-service-archive/1", "status": "FAIL",
              "custody": "UNSEALED_DIAGNOSTICS", "source": None,
              "human_acceptance": False, "formal_acceptance": False,
              "scope": "Original e/tools tree, direct command exits and declared helper closures; no additional app reopen"}
    try:
        # Observe the bounded receipt before traversing streams that an uncertain
        # descendant may still be changing. No source command or copy is safe yet.
        receipt_name = "e/receipt.json"
        receipt_path = root / receipt_name
        receipt_identity = archive_identity(archive_stat(receipt_path))
        receipt_digest = archive_file_digest(receipt_path, 64 * 1024 * 1024)
        receipt_inventory = {"entries": {receipt_name: {"kind": "file", **receipt_digest}},
                             "identities": {receipt_name: receipt_identity}}
        original_receipt = archive_json(root, receipt_inventory, receipt_name)
        if original_receipt.get("status") == "UNKNOWN":
            raise UnknownObservation("original execution is UNKNOWN; no source commands or archive copy",
                                     original_receipt.get("unsealed_command", {}))
        commands = [original_receipt.get("build", {}), original_receipt.get("discovery", {}).get("command", {}),
                    *(row.get("command", {}) for row in original_receipt.get("selections", [])),
                    *(row.get("command", {}) for row in original_receipt.get("partial_discovery", [])),
                    *(row.get("command", {}) for row in original_receipt.get("camera_discovery", [])),
                    *(row.get("command", {}) for row in original_receipt.get("phase1_discovery", [])),
                    original_receipt.get("phase1_sdk_preflight", {}).get("command", {})]
        for command in commands:
            if execution_unknown(command):
                raise UnknownObservation("original command is UNKNOWN; no source commands or archive copy", command)
        graph_mode = original_receipt.get("selection_plan", {}).get("requested") == GRAPH_SCOPE
        require(original_receipt.get("status") == ("CLOSED_GRAPH_OBSERVATIONS" if graph_mode else "PASS"), "original execution is not complete")
        if original_receipt.get("selection_plan", {}).get("requested") == PHASE1_SERVICE47_SCOPE:
            require(re.fullmatch(r"[0-9a-f]{64}", os.environ.get(PHASE1_SERVICE47_DEADLINE_ENV, "")) is not None
                    and original_receipt.get("phase1_deadline", {}).get("original_sha256")
                        == os.environ[PHASE1_SERVICE47_DEADLINE_ENV],
                    "Phase1 archive requires its authenticated original deadline")
        before = archive_inventory(root)
        require(before["identities"].get(receipt_name) == receipt_identity
                and archive_json(root, before, receipt_name) == original_receipt,
                "original receipt changed before archive admission")
        selected = original_receipt.get("selection_plan", {}).get("requested")
        source_mode = selected if is_browser_scope(selected) or selected in (PARTIAL_ORACLE_SCOPE, CAMERA_EIGHT_SCOPE, PHASE1_SERVICE47_SCOPE, GRAPH_SCOPE) else None
        source = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        report["source"] = source
        report["bindings"] = archive_closed_bindings(root, before, source)
        write_new(output / "input-inventory.json", before)
        archive = output / "closed-evidence.tar"
        write_closed_tar(root, archive, before)
        report["verified"] = verify_closed_tar(archive, before)
        require(archive_inventory(root) == before, "original evidence changed during retention")
        after = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        require(after == source, "source changed during retention")
        require(archive_file_digest(archive, ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
                == report["verified"]["archive_digest"], "retained archive changed")
        report["archive"] = {"path": archive.name, **report["verified"]["archive_digest"]}
        report["input_inventory"] = digest(output / "input-inventory.json")
        report["originals_equal_before_after"] = True
        report["status"], report["custody"] = "PASS", "VERIFIED_ORIGINAL_TREE_COPY"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command)
    except Exception as error:
        report["error_type"], report["error"] = type(error).__name__, str(error)
    write_new(output / "archive-receipt.json", report)
    return report


def archive_main(arguments):
    parser = argparse.ArgumentParser(description="Retain the complete synthetic CI evidence tree in a verified TAR.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(arguments)
    repo, root = (Path(os.path.abspath(p)) for p in (args.repo, args.evidence))
    require(os.environ.get("GITHUB_ACTIONS") == "true", "archive command is restricted to its synthetic CI job")
    require(repo.name == "g" and root == repo.parent / "v"
            and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent,
            "archive command requires the isolated CI g/v layout")
    if os.environ.get(GRAPH_DEADLINE_ENV) is not None:
        clock = GraphOriginalClock(root)
        receipt = graph_json((root / "e/receipt.json").read_bytes())
        require(receipt.get("status") == "CLOSED_GRAPH_OBSERVATIONS"
                and receipt.get("graph_deadline", {}).get("original") == clock.original_ref
                and receipt["graph_deadline"].get("unknown") is False,
                "Graph incomplete or UNKNOWN originals cannot be sealed")
        with clock.phase("archive"):
            report = retain_closed_archive(repo, root)
    elif os.environ.get(PHASE1_SERVICE47_DEADLINE_ENV) is not None:
        with phase1_service47_guard():
            original_raw, _ = studio68_original(root / "e/receipt.json", 64 * 1024 * 1024)
            original_receipt = json.loads(original_raw)
            clock = phase1_service47_clock(root)
            require(original_receipt.get("selection_plan", {}).get("requested") == PHASE1_SERVICE47_SCOPE
                    and original_receipt.get("status") == "PASS", "Phase1 incomplete/UNKNOWN evidence cannot be sealed")
            budget = original_receipt.get("phase1_deadline", {})
            require(budget.get("original_sha256") == os.environ[PHASE1_SERVICE47_DEADLINE_ENV]
                    and budget.get("deadline_mono_ns") == clock.deadline_ns and budget.get("unknown") is False
                    and type(budget.get("finished_mono_ns")) is int
                    and budget["finished_mono_ns"] <= clock.deadline_ns - PHASE1_SERVICE47_EFFECT_RESERVE * 1_000_000_000,
                    "Phase1 original observation did not retain its custody reserve")
            admission = clock.admit("seal", 300, reserve=750)
            write_new(root / "tools/phase1-seal-admission.json", admission)
        with clock.window(reserve=750, cap=300):
            report = retain_closed_archive(repo, root)
    else:
        report = retain_closed_archive(repo, root)
    print(json.dumps({"status": report["status"], "custody": report["custody"],
                      "receipt": str(root / "retained/archive-receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


def browser_selections(scope="browser-contracts"):
    if scope == WINDOW_FRONTEND_SCOPE:
        label, filename, titles = WINDOW_FRONTEND_BROWSER_SELECTION
        require((label, filename) == ("browser-window-frontend", "native-preview.spec.ts")
                and isinstance(titles, tuple) and len(titles) == len(set(titles)) == 6
                and all(isinstance(title, str) and title for title in titles)
                and hashlib.sha256(json.dumps(titles, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                    == WINDOW_FRONTEND_BROWSER_CATALOG_SHA256
                and BROWSER_SCOPES.get(scope) == (label,), "Window browser catalogue differs")
        return (WINDOW_FRONTEND_BROWSER_SELECTION,)
    require(BROWSER_SELECTIONS == (*HISTORICAL_BROWSER_SELECTIONS,
                                   *CREATIVE_PRODUCTION_BROWSER_SELECTIONS)
            and len(HISTORICAL_BROWSER_SELECTIONS) == 31
            and sum(len(row[2]) for row in HISTORICAL_BROWSER_SELECTIONS) == 148,
            "historical browser inventory differs")
    require(len(BROWSER_SELECTIONS) == 33, "browser file count differs")
    labels, files = set(), set()
    count = 0
    for label, filename, titles in BROWSER_SELECTIONS:
        require(label.startswith("browser-") and label not in labels, "duplicate browser selection")
        require(filename.endswith(".spec.ts") and "/" not in filename and filename not in files,
                "invalid or duplicate browser filename")
        require(isinstance(titles, tuple) and titles and all(isinstance(x, str) and x for x in titles)
                and len(set(titles)) == len(titles), "invalid browser title inventory")
        labels.add(label)
        files.add(filename)
        count += len(titles)
    require(count == 206, "browser case count differs")
    require(is_browser_scope(scope), "unknown named browser scope")
    selected_labels = BROWSER_SCOPES[scope]
    require(REVIEW_BROWSER_SELECTIONS == (
                ("browser-review", "review-preservation.spec.ts", REVIEW_BROWSER_TITLES),)
            and isinstance(REVIEW_BROWSER_TITLES, tuple)
            and len(REVIEW_BROWSER_TITLES) == len(set(REVIEW_BROWSER_TITLES)) == 29
            and all(isinstance(title, str) and title for title in REVIEW_BROWSER_TITLES)
            and "browser-review" not in labels
            and "review-preservation.spec.ts" not in files,
            "Review browser inventory differs")
    require(len(WORKFLOW_PREVIEW_BROWSER_SELECTIONS) == 2
            and tuple((label, filename, len(titles)) for label, filename, titles
                      in WORKFLOW_PREVIEW_BROWSER_SELECTIONS) == (
                ("browser-workflow-recovery", "workflow-action-recovery.spec.ts", 33),
                ("browser-preview-recovery", "preview-recovery.spec.ts", 27)),
            "workflow and preview browser inventory differs")
    for label, filename, titles in WORKFLOW_PREVIEW_BROWSER_SELECTIONS:
        require(label not in labels and filename not in files
                and isinstance(titles, tuple) and len(set(titles)) == len(titles)
                and all(isinstance(title, str) and title for title in titles),
                "workflow and preview browser identities differ")
        labels.add(label)
        files.add(filename)
    expected_labels, expected_count = {
        "browser-contracts": (tuple(row[0] for row in BROWSER_SELECTIONS), 206),
        "creative-production-browser": (("browser-production-design", "browser-creative-workbench"), 58),
        "review-browser": (("browser-review",), 29),
        "workflow-preview-browser": (("browser-program-av-monitor", "browser-workflow-recovery", "browser-preview-recovery"), 62),
    }[scope]
    require(selected_labels == expected_labels, "browser scope inventory differs")
    chosen = tuple(row for row in (*BROWSER_SELECTIONS, *REVIEW_BROWSER_SELECTIONS,
                                    *WORKFLOW_PREVIEW_BROWSER_SELECTIONS)
                   if row[0] in selected_labels)
    require(len(chosen) == len(selected_labels), "browser scope has unknown or duplicate files")
    require(sum(len(row[2]) for row in chosen) == expected_count,
            "browser scope case count differs")
    return chosen


def browser_discovery_inventory(scope="browser-contracts"):
    chosen = browser_selections(scope)
    selected = {(filename, title) for _, filename, titles in chosen for title in titles}
    exclusions = set(BROWSER_OMITTED_SERVICE_CASES)
    require(len(exclusions) == 4
            and all(filename == "project-mutation.spec.ts" for filename, _ in exclusions)
            and not selected.intersection(exclusions), "invalid excluded service inventory")
    files = {filename for _, filename, _ in chosen}
    excluded = {(filename, title) for filename, title in exclusions if filename in files}
    return selected, excluded


def browser_full_title_path(filename, title):
    return ("chromium", filename, *BROWSER_DESCRIBE_TITLES.get((filename, title), ()), title)


def browser_result_identities(document):
    found = []

    def visit(suites, parents=()):
        require(isinstance(suites, list), "browser suite list missing")
        for suite in suites:
            require(isinstance(suite, dict), "browser suite must be an object")
            hierarchy = (*parents, suite.get("title"))
            require(all(isinstance(name, str) and name for name in hierarchy), "browser suite title missing")
            for spec in suite.get("specs", []):
                require(isinstance(spec, dict), "browser case must be an object")
                pair = (spec.get("file"), spec.get("title"))
                require(all(isinstance(name, str) and name for name in pair), "browser case identity missing")
                tests = spec.get("tests", [])
                require(len(tests) == 1 and isinstance(tests[0], dict), "browser project count differs")
                case = tests[0]
                require(case.get("projectId") == case.get("projectName") == "chromium"
                        and spec.get("tags") == [], "browser project name or tags differ")
                require(("chromium", *hierarchy, pair[1]) == browser_full_title_path(*pair),
                        "browser full title path differs")
                found.append((pair, case))
            visit(suite.get("suites", []), hierarchy)

    visit(document.get("suites"))
    return found


def validate_browser_discovery(document, scope="browser-contracts"):
    """Validate names without interpreting list-only results as executed passes."""
    require(isinstance(document, dict) and document.get("errors") == [],
            "discovery has missing or nonempty global errors")
    selected, excluded = browser_discovery_inventory(scope)
    expected = selected | excluded
    stats = document.get("stats", {})
    require(stats.get("expected") == 0 and stats.get("skipped") == len(expected)
            and stats.get("unexpected") == 0 and stats.get("flaky") == 0,
            "discovery must contain only unexecuted list results")
    found = set()
    for pair, case in browser_result_identities(document):
        require(pair in expected and pair not in found, "unknown or duplicate discovered case")
        require(case.get("results") == [], "discovery contains an execution result")
        require(case.get("expectedStatus") == ("skipped" if pair in excluded else "passed"),
                "discovery skip or expected failure differs from the closed scope")
        found.add(pair)
    require(found == expected, "discovered browser inventory differs")
    return {"status": "LISTED_NOT_EXECUTED", "listed": len(expected), "selected": len(selected),
            "excluded_service": len(excluded), "execution_results": 0}


def validate_browser_execution(document, filename, titles):
    verified = validate_report(document, filename, titles)
    identities = [pair for pair, _ in browser_result_identities(document)]
    require(set(identities) == {(filename, title) for title in titles}
            and len(identities) == len(titles), "browser executed full identity differs")
    return verified


def observed_tool(path):
    path = Path(os.path.abspath(path))
    resolved = path.resolve(strict=True)
    return {"path": str(path), "resolved_path": str(resolved), **digest(resolved)}


def service_environment_binding(target, output=None, label=None, binary=None):
    value = {"CARGO_TARGET_DIR": str(target),
            "PLAYWRIGHT_JSON_OUTPUT_FILE": str(output / (label + ".json")) if label is not None else None,
            "MOTIONWRIGHT_SERVICE_TEST_BIN": str(binary) if label is not None else None,
            "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": str(output / (label + "-stores")) if label is not None else None}
    if phase1_branch_admission_label(label):
        value[BRANCH_COMBINATION_ADMISSION_ENV] = "1"
    return value


def branch_combination_environment(repo, before, binary, retained_binary, identity, *, scope=None):
    """Bind the explicit branch selector to this observed source and built bytes."""
    require(source_observation(repo, scope) == before, "branch combination source changed before dispatch")
    require(digest(binary) == digest(retained_binary) == identity,
            "branch combination binary changed before dispatch")
    return {BRANCH_COMBINATION_ADMISSION_ENV: "1"}


def service_command(node, cli, output, selection, *, listing=False, scope=None):
    label, filename, _titles = selection
    require(type(listing) is bool, "service list flag must be boolean")
    require(scope in (None, CAMERA_EIGHT_SCOPE, PHASE1_SERVICE47_SCOPE), "unknown closed service command scope")
    if scope == PHASE1_SERVICE47_SCOPE:
        require(selection in phase1_service47_selections(), "unknown Phase1 service selection")
        pattern = "^(?:" + "|".join(re.escape(" ".join(("chromium", filename, title))) for title in _titles) + ")$"
        return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                "--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only", "--reporter=json",
                "--output=" + str(output / (label + "-test-results")), "--grep", pattern,
                *(["--list"] if listing else [])]
    if scope == CAMERA_EIGHT_SCOPE:
        require(selection in camera_eight_selections(), "unknown Camera eight selection")
        pattern = "^(?:" + "|".join(re.escape(" ".join(("chromium", filename, title))) for title in _titles) + ")$"
        return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                "--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only", "--reporter=json",
                "--output=" + str(output / (label + "-test-results")), "--grep", pattern,
                *(["--list"] if listing else [])]
    if label in {row[0] for row in PARTIAL_ORACLE_SELECTIONS}:
        require(selection in partial_oracle_selections(), "unknown partial oracle selection")
        pattern = "^(?:" + "|".join(re.escape(" ".join(("chromium", filename, title)))
                                      for title in _titles) + ")$"
        return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                "--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only", "--reporter=json",
                "--output=" + str(output / (label + "-test-results")), "--grep", pattern,
                *(["--list"] if listing else [])]
    require(listing is False, "list-only service mode is reserved for its closed partial scope")
    argv = [node, str(cli), "test", "tests/" + filename, "--project=chromium",
            "--workers=1", "--retries=0", "--reporter=json",
            "--output=" + str(output / (label + "-test-results"))]
    if label == "ink":
        argv += ["--grep", re.escape(INK_TITLE) + "$"]
    return argv


def browser_command(node, cli, output, selection=None, *, scope="browser-contracts"):
    if scope == WINDOW_FRONTEND_SCOPE:
        rows = browser_selections(scope)
        require(selection is None or selection == rows[0], "Window browser selection differs")
        label, filename, titles = rows[0]
        pattern = "^(?:" + "|".join(re.escape(" ".join(browser_full_title_path(filename, title)))
                                   for title in titles) + ")$"
        return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
                "--workers=1", "--retries=0", "--repeat-each=1", "--forbid-only",
                "--reporter=json" if selection is None else "--reporter=line,json",
                "--output=" + str(output / (label + "-test-results")), "--grep", pattern,
                *(["--list"] if selection is None else [])]
    if selection is None:
        return [node, str(cli), "test",
                *("tests/" + filename for _, filename, _ in browser_selections(scope)),
                "--project=chromium", "--list", "--reporter=json"]
    label, filename, titles = selection
    require(selection in browser_selections(scope), "unknown browser file selection")
    pattern = "^(?:" + "|".join(re.escape(" ".join(browser_full_title_path(filename, title)))
                               for title in titles) + ")$"
    return [node, str(cli), "test", "tests/" + filename, "--project=chromium",
            "--workers=1", "--retries=0", "--reporter=json",
            "--output=" + str(output / (label + "-test-results")), "--grep", pattern]


def browser_report_original(path):
    with path.open("rb") as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    require(len(raw) <= 64 * 1024 * 1024, "browser report exceeds bound")
    observed = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    require(digest(path) == observed, "browser original report changed")
    return observed, json.loads(raw)


def run_browser_selection(repo, output, target, scope="browser-contracts"):
    """Run synthetic browser contracts through the canonical process observer."""
    chosen = browser_selections(scope)
    window_mode = scope == WINDOW_FRONTEND_SCOPE
    window_environment = window_frontend_environment(phase="browser") if window_mode else None
    repo, output, target = (path.resolve() for path in (repo, output, target))
    require(repo.is_dir(), "repository missing")
    if window_mode:
        require(not target.exists() and output.is_dir()
                and {path.name for path in output.iterdir()} == {"window-unit-build"},
                "Window browser requires only its already-closed unit/build originals")
        window_before = source_observation(repo, scope)
        window_binding = window_frontend_retain_units(repo, output, window_before)
    else:
        require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
        output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "selection_plan": selection_plan(scope),
              "execution_scope": "SYNTHETIC_BROWSER_CONTRACTS",
              "service_runtime": "NOT_RUN", "service_build": "NOT_RUN",
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        before = window_before if window_mode else source_observation(repo, scope)
        if window_mode:
            report["window_unit_build_binding"] = window_binding
            report["frontend_resource_observations"] = [window_environment["observed"]]
            report["phase_source_after"] = []
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        node = shutil.which("node")
        require(node is not None, "node is required for browser contracts")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = {"path": str(cli), **digest(cli)}
        report["node"] = {"path": node, **digest(Path(node))}
        env = dict(window_environment["environment"]) if window_mode else dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        report["service_environment"] = {"MOTIONWRIGHT_SERVICE_TEST_BIN": "ABSENT",
                                         "MOTIONWRIGHT_SERVICE_TEST_TMPDIR": "ABSENT"}
        discovery_env = dict(env)
        discovery_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / "browser-discovery.json")
        command = execute(browser_command(node, cli, output, scope=scope), repo / "apps/studio",
                          discovery_env, output, "browser-discovery", 180)
        report["discovery"] = {"command": command, "status": "UNADMITTED"}
        require_observed_command(command)
        require(command["exit_code"] == 0, "browser discovery process did not exit zero")
        original, document = browser_report_original(output / "browser-discovery.json")
        report["discovery"].update(original_report=original, verified=validate_browser_discovery(document, scope),
                                   status="LISTED_NOT_EXECUTED")
        if window_mode:
            after_phase = source_observation(repo, scope)
            require(after_phase == before, "Window source changed during current browser listing")
            report["phase_source_after"].append(after_phase)
        for selection in chosen:
            if window_mode:
                resources = window_frontend_environment(phase="browser")
                report["frontend_resource_observations"].append(resources["observed"])
            label, filename, titles = selection
            case_env = dict(env)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            command = execute(browser_command(node, cli, output, selection, scope=scope),
                              repo / "apps/studio", case_env, output, label,
                              BROWSER_SELECTION_TIMEOUTS[label])
            observed = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
            report["selections"].append(observed)
            require_observed_command(command)
            try:
                original, document = browser_report_original(output / (label + ".json"))
                observed["original_report"] = original
                observed["verified"] = validate_browser_execution(document, filename, titles)
                require(command["exit_code"] == 0, "selected browser process did not exit zero")
                observed["status"] = "PASS"
            except Exception as error:
                observed["error"] = str(error)
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo, scope)
        require(report["source_after"] == before, "source changed during browser execution")
        if window_mode:
            report["phase_source_after"].append(report["source_after"])
        require(digest(cli) == {key: report["playwright_cli"][key] for key in ("bytes", "sha256")},
                "Playwright CLI changed")
        require(digest(Path(node)) == {key: report["node"][key] for key in ("bytes", "sha256")},
                "Node executable changed")
        require(not target.exists(), "browser contracts created a service build target")
        report["service_target_absent"] = True
        require(len(report["selections"]) == len(chosen)
                and all(item["status"] == "PASS" for item in report["selections"]),
                "required browser selection failed")
        report["status"] = "PASS"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error"] = str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(item["expected_tests"] for item in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1



def validate_creative_production_units(document, studio, selection="creative-production"):
    """Admit only the exact selected original assertions from the locked CLI."""
    if selection == WINDOW_FRONTEND_SCOPE:
        return validate_window_frontend_units(document, studio)
    rows = selected_unit_inventory(selection)
    expected_count = sum(len(titles) for _, titles in rows)
    require(isinstance(document, dict) and document.get("success") is True,
            "unit report is missing or unsuccessful")
    require(document.get("numTotalTests") == document.get("numPassedTests") == expected_count,
            "unit test census differs")
    for key in ("numFailedTests", "numPendingTests", "numTodoTests", "numFailedTestSuites",
                "numPendingTestSuites"):
        require(document.get(key) == 0, "unit omitted or failing results: " + key)
    require(document.get("wasInterrupted", False) is False
            and not document.get("unhandledErrors") and not document.get("errors")
            and not document.get("testExecError"), "unit global execution error")
    expected = {str(studio / name): titles for name, titles in rows}
    suites = document.get("testResults")
    require(isinstance(suites, list) and len(suites) == len(rows), "unit file inventory differs")
    found, files = [], set()
    for suite in suites:
        name = suite.get("name")
        require(name in expected and name not in files and suite.get("status") == "passed"
                and not suite.get("message") and not suite.get("testExecError"),
                "unit suite identity or result differs")
        files.add(name)
        wanted, observed = set(expected[name]), set()
        assertions = suite.get("assertionResults")
        require(isinstance(assertions, list) and len(assertions) == len(wanted),
                "unit assertion count differs")
        for assertion in assertions:
            ancestors = assertion.get("ancestorTitles")
            require(isinstance(ancestors, list) and len(ancestors) == 1,
                    "unit suite hierarchy differs")
            pair = (ancestors[0], assertion.get("title"))
            require(pair in wanted and pair not in observed
                    and assertion.get("fullName") == " ".join(pair), "unit exact title differs")
            require(assertion.get("status") == "passed" and assertion.get("failureMessages") == []
                    and not assertion.get("failureDetails") and not assertion.get("retryReasons")
                    and assertion.get("retryCount", 0) == 0,
                    "unit assertion is not an original ordinary pass")
            observed.add(pair)
            found.append({"file": str(Path(name).relative_to(studio)), "suite": pair[0], "title": pair[1]})
        require(observed == wanted, "unit exact assertion inventory differs")
    require(files == set(expected), "unit file omitted")
    return {"status": "PASS", "passed": expected_count, "files": len(rows), "skipped": 0, "retries": 0,
            "assertions": found, "evidence_scope": "UNIT_ASSERTIONS_ONLY", "formal_acceptance": False}


def run_creative_production_units(repo, output, selection="creative-production"):
    """Run the selected Vitest files and production build once for a composed SHA."""
    units = selected_unit_inventory(selection)
    window_mode = selection == WINDOW_FRONTEND_SCOPE
    window_environment = window_frontend_environment() if window_mode else None
    source_scope = {"review": "review-browser", "creative-production": "creative-production-browser",
                    "workflow-preview": "workflow-preview-browser", "branch-claim": None,
                    WINDOW_FRONTEND_SCOPE: WINDOW_FRONTEND_SCOPE}[selection]
    repo, output = repo.resolve(), output.resolve()
    window_before = source_observation(repo, source_scope) if window_mode else None
    require(repo.is_dir() and not output.exists(), "fresh unit evidence and existing source required")
    output.mkdir(parents=True, exist_ok=False)
    studio = repo / "apps/studio"
    report = {"schema": "motionwright.studio-unit-build-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "commands": [], "requested_selection": selection,
              "selection": [{"file": name, "titles": list(titles) if window_mode else [list(pair) for pair in titles]}
                            for name, titles in units],
              "service_runtime": "NOT_RUN", "browser_runtime": "NOT_RUN", "native_render": "NOT_RUN",
              "installed_tauri": "NOT_RUN", "human_acceptance": False, "formal_acceptance": False}
    try:
        before = window_before if window_mode else source_observation(repo, source_scope)
        if window_mode:
            report["frontend_resource_observations"] = [window_environment["observed"]]
            report["phase_source_after"] = []
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        node, npm = shutil.which("node"), shutil.which("npm")
        require(node is not None and npm is not None, "Node and npm required for selected unit/build verification")
        node, npm = str(Path(node).resolve(strict=True)), str(Path(npm).resolve(strict=True))
        cli = studio / "node_modules/vitest/vitest.mjs"
        probe = repo / "tooling/vite_editor_security_probe.mjs"
        report["tools"] = {name: {"path": str(path), **digest(Path(path))}
                           for name, path in (("node", node), ("npm", npm), ("vitest_cli", cli), ("security_probe", probe))}
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        if window_mode:
            env = dict(window_environment["environment"])
        # An existing output cannot be mistaken for a build of this source.
        dist = studio / "dist"
        require(not dist.exists(), "fresh production build output required")
        commands = (
            ("security-probe", [node, str(probe)], 60),
            ("vitest", [node, str(cli), "run", *(name for name, _ in units),
                        *(["--maxWorkers=1", "--no-file-parallelism"] if window_mode else []),
                        "--retry=0", "--reporter=json", "--outputFile=" + str(output / "vitest.json")], 300),
            ("build", [npm, "run", "build"], 600),
        )
        for label, argv, timeout in commands:
            if window_mode:
                resources = window_frontend_environment(phase="build" if label == "build" else "unit")
                report["frontend_resource_observations"].append(resources["observed"])
                env = dict(resources["environment"])
            command = execute(argv, studio, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0 and command.get("diagnostic_custody") == "DIRECT_PROCESS_EXIT_OBSERVED"
                    and not command.get("observation"), "selected unit/build command incomplete: " + label)
            if label == "vitest":
                original, document = browser_report_original(output / "vitest.json")
                report["vitest_original"] = original
                report["vitest"] = validate_creative_production_units(document, studio, selection)
            if window_mode:
                after_phase = source_observation(repo, source_scope)
                require(after_phase == before, "Window source changed during unit/build phase")
                report["phase_source_after"].append(after_phase)
        report["source_after"] = source_observation(repo, source_scope)
        require(report["source_after"] == before, "source changed during selected unit/build execution")
        for identity in report["tools"].values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "selected unit/build executable or probe changed")
        require((dist / "index.html").is_file(), "production build index missing")
        files = {}
        for path in sorted(dist.rglob("*")):
            archive_ancestors(path)
            if path.is_file():
                require(len(files) < 2000 and path.stat().st_size <= 64 * 1024 * 1024,
                        "production build artifact bound exceeded")
                files[path.relative_to(dist).as_posix()] = digest(path)
        require(any(name.endswith(".js") for name in files), "production build JavaScript missing")
        report["production_build"] = {"status": "PASS", "files": files,
                                       "scope": "BUILD_OUTPUT_ONLY; NO_BROWSER_OR_SERVICE_EXECUTION"}
        retained = output / "production-build"
        shutil.copytree(dist, retained)
        require({path.relative_to(retained).as_posix(): digest(path) for path in sorted(retained.rglob("*"))
                 if path.is_file()} == files, "retained production build differs")
        report["status"] = "PASS"
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error_type"], report["error"] = type(error).__name__, str(error)
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


WORKFLOW_ROUTE_CASES = ('effect_grants::tests::creation_cannot_cross_workspace_registry_window_key_or_original_title',
 'effect_grants::tests::creation_expiry_and_previous_receipt_wire_shape_remain_explicit',
 'effect_grants::tests::creation_is_exactly_one_time_in_its_trusted_workspace_session',
 'effect_grants::tests::creation_scope_never_grants_an_existing_project_or_another_effect',
 'effect_grants::tests::creation_subject_preserves_structural_boundaries_and_rejects_invalid_identity',
 'effect_grants::tests::exact_grant_is_one_time',
 'effect_grants::tests::expired_grant_fails_closed_without_sleeping',
 'effect_grants::tests::grant_never_crosses_effect_boundaries_and_mismatch_consumes_it',
 'effect_grants::tests::project_generation_revision_and_subject_are_exact',
 'effect_grants::tests::project_import_is_the_only_supported_unscoped_grant',
 'effect_grants::tests::unsupported_external_effects_cannot_be_granted',
 'routed::ambiguous_route_reopens_and_refuses_same_key_without_second_effect',
 'routed::cancelled_route_retains_dispatch_intent_and_refuses_after_reopen',
 'routed::completed_route_replays_local_identity_and_retains_separate_broker_id',
 'routed::foreign_connection_and_invalid_identity_never_dispatch',
 'routed::invalid_grant_and_stale_scope_are_rejected_before_dispatch',
 'routed::known_native_rejection_preserves_code_without_provider_message',
 'routed::readonly_plan_uses_current_scope_without_a_mutation_grant',
 'routed::terminal_append_refusal_keeps_original_after_helper_return_without_public_disclosure',
 'workflow_wire_requires_original_request_identity_and_rejects_unknown_fields')


def validate_workflow_route_output(exit_code, output):
    """Admit the complete Linux route target, including its eleven grant tests."""
    require(type(exit_code) is int and exit_code == 0 and isinstance(output, str),
            "workflow route process must exit successfully")
    require(len(WORKFLOW_ROUTE_CASES) == 20 and len(set(WORKFLOW_ROUTE_CASES)) == 20,
            "workflow route inventory differs")
    rows = re.findall(r"^test (\S+) \.\.\. (\S+)[^\n]*$", output, re.MULTILINE)
    summaries = re.findall(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                           r"(\d+) measured; (\d+) filtered out;(?:[^\n]*)$", output, re.MULTILINE)
    require(len(rows) == 20 and len({name for name, _ in rows}) == 20
            and {name for name, _ in rows} == set(WORKFLOW_ROUTE_CASES)
            and all(status == "ok" for _, status in rows)
            and summaries == [("ok", "20", "0", "0", "0", "0")],
            "workflow route original closed result differs")
    return {"status": "PASS", "cases": sorted(name for name, _ in rows),
            "passed": 20, "ignored": 0,
            "scope": "NINE_REAL_ROUTE_CONTRACTS_AND_ELEVEN_GRANTS; BOUNDED_TEST_TRANSPORT_ONLY",
            "native_application_execution": "NOT_RUN", "formal_acceptance": False}


def run_workflow_route(repo, output):
    repo, output = repo.resolve(), output.resolve()
    require(repo.is_dir() and not output.exists() and not output.is_relative_to(repo),
            "fresh isolated workflow route output required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.workflow-route-ci/1", "status": "FAIL", "commands": [],
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "native_render": "NOT_RUN", "installed_tauri": "NOT_RUN",
              "human_acceptance": False, "formal_acceptance": False}
    try:
        require(sys.platform.startswith("linux"), "closed workflow route verification requires Linux")
        before = source_observation(repo, "workflow-preview-browser")
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, rustc = shutil.which("cargo"), shutil.which("rustc")
        require(cargo is not None and rustc is not None, "cargo and rustc required")
        # Preserve rustup's invocation basename while fingerprinting the resolved executable.
        tools = {name: {"invoked_path": str(Path(path).absolute()),
                        "path": str(Path(path).resolve(strict=True)),
                        **digest(Path(path).resolve(strict=True))}
                 for name, path in (("cargo", cargo), ("rustc", rustc))}
        report["tools"] = tools
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env.update(CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
        commands = (("rustc-identity", [rustc, "-Vv"], 60),
                    ("cargo-identity", [cargo, "-V"], 60),
                    ("route-contracts", [cargo, "test", "--locked", "-p", "motionwright-native",
                                         "--test", "desktop_workflow_route", "--", "--test-threads=1"], 1200),
                    ("desktop-check", [cargo, "check", "--locked", "-p", "motionwright-desktop"], 1200))
        for label, argv, timeout in commands:
            command = execute(argv, repo, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0, "workflow route command failed: " + label)
            if label == "route-contracts":
                path = output / (label + ".stdout.log")
                require(digest(path) == {key: command["stdout"][key] for key in ("bytes", "sha256")},
                        "original workflow route log changed")
                report["route_contracts"] = validate_workflow_route_output(
                    command["exit_code"], path.read_text(encoding="utf-8"))
        report["source_after"] = source_observation(repo, "workflow-preview-browser")
        require(report["source_after"] == before, "workflow route source changed")
        for identity in tools.values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "workflow route executable changed")
        report.update(status="PASS", desktop_shell="COMPILED_ONLY_NOT_LAUNCHED")
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1


def workflow_route_main(arguments):
    parser = argparse.ArgumentParser(description="Verify the exact desktop workflow route and shell compile.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    return run_workflow_route(args.repo, args.output)


def unit_build_main(arguments):
    parser = argparse.ArgumentParser(description="Verify an exact selected unit inventory and production build.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection", choices=("creative-production", "review", "workflow-preview", "branch-claim", WINDOW_FRONTEND_SCOPE),
                        default="creative-production")
    args = parser.parse_args(arguments)
    return run_creative_production_units(args.repo, args.output, args.selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--selection", default="all", choices=["all", PARTIAL_ORACLE_SCOPE, CAMERA_EIGHT_SCOPE, PHASE1_SERVICE47_SCOPE, *SELECTION_GROUPS, *(row[0] for row in SELECTIONS), *BROWSER_SCOPES])
    args = parser.parse_args()
    if is_browser_scope(args.selection):
        return run_browser_selection(args.repo, args.output, args.target, args.selection)
    if args.selection == PHASE1_SERVICE47_SCOPE:
        with phase1_service47_guard():
            clock = phase1_service47_clock(args.repo.resolve().parent / "v")
        with clock.window():
            return run_service_observation(args, deadline=clock)
    return run_service_observation(args)


def run_service_observation(args, *, deadline=None):
    selections = selected_selections(args.selection)
    partial_mode, camera_mode = args.selection == PARTIAL_ORACLE_SCOPE, args.selection == CAMERA_EIGHT_SCOPE
    phase1_mode = args.selection == PHASE1_SERVICE47_SCOPE
    listed_mode = partial_mode or camera_mode or phase1_mode
    if camera_mode:
        camera_eight_profile()  # No source/resource admission is inferred from this selector.
    require(not listed_mode or sys.platform.startswith("linux"),
            "partial service verification requires its owned POSIX process boundary")
    repo, output, target = (p.resolve() for p in (args.repo, args.output, args.target))
    if camera_mode:
        require(output == repo.parent / "v/e", "Camera requires its original isolated evidence path")
    require(repo.is_dir(), "repository missing")
    require(not output.exists() and not target.exists(), "fresh evidence and build directories required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "FAIL",
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "selections": [],
              "selection_plan": selection_plan(args.selection),
              "installed_tauri": "NOT_RUN", "native_render": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    source_mode = args.selection if listed_mode else None
    try:
        before = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        if camera_mode:
            original, preflight = browser_report_original(output.parent / "tools/camera-preflight.json")
            require(preflight.get("schema") == "motionwright.camera-eight-preflight/1"
                    and preflight.get("source") == before and preflight.get("execution") == "NOT_RUN"
                    and preflight.get("formal_acceptance") is False
                    and preflight.get("resources", {}).get("phase") == "preflight",
                    "Camera preflight original source differs")
            report["camera_preflight"] = original
            report["camera_resource_observations"] = [camera_eight_resources(repo, target, phase="build")]
        cargo, node = shutil.which("cargo"), shutil.which("node")
        require(cargo is not None and node is not None, "cargo and node are required")
        report["cargo"], report["node"] = observed_tool(Path(cargo)), observed_tool(Path(node))
        cargo, node = report["cargo"]["path"], report["node"]["path"]
        report["target_directory"] = str(target)
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env.pop(BRANCH_COMBINATION_ADMISSION_ENV, None)
        env["CARGO_TARGET_DIR"] = str(target)
        build = execute([cargo, "build", "--locked", "-p", "motionwright-service",
                         "--example", "studio_review_session", *(["--jobs", "1"] if camera_mode or phase1_mode else []),
                         *(["--message-format=json"] if phase1_mode else [])],
                        repo, env, output, "build", 1200, **({"deadline": deadline} if deadline is not None else {}))
        report["build"] = build
        require_observed_command(build)
        require(build["exit_code"] == 0, "real service build failed")
        binary = target / "debug/examples/studio_review_session"
        if os.name == "nt":
            binary = binary.with_suffix(".exe")
        report["service_binary"] = {"path": str(binary), **digest(binary)}
        retained_binary = output / ("executed-studio-service.exe" if os.name == "nt" else "executed-studio-service")
        shutil.copyfile(binary, retained_binary)
        report["retained_service_binary"] = {"path": retained_binary.name, **digest(retained_binary)}
        require(digest(retained_binary) == digest(binary), "retained service binary differs")
        require((source_observation(repo, source_mode) if source_mode else source_observation(repo)) == before,
                "source changed during service compilation")
        cli = repo / "apps/studio/node_modules/@playwright/test/cli.js"
        report["playwright_cli"] = digest(cli)
        if phase1_mode:
            report["phase1_build_original"] = phase1_service47_build_original(output, repo, target, binary)
            report["phase1_sdk_preflight"] = phase1_service47_sdk_preflight(repo, output, binary, env,
                **({"deadline": deadline} if deadline is not None else {}))
        discovery_key = "phase1_discovery" if phase1_mode else "camera_discovery" if camera_mode else "partial_discovery"
        if listed_mode:
            report[discovery_key] = []
            # Every current list closes before any selected test body is launched.
            for label, filename, titles in selections:
                stores = output / (label + "-stores")
                stores.mkdir()
                discovery_env = dict(env)
                discovery_env.update(service_environment_binding(target, output, label, binary))
                discovery_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + "-discovery.json"))
                if camera_mode:
                    report["camera_resource_observations"].append(camera_eight_resources(repo, target, phase="list-" + label))
                command = execute(service_command(node, cli, output, (label, filename, titles), listing=True,
                                                  scope=CAMERA_EIGHT_SCOPE if camera_mode else PHASE1_SERVICE47_SCOPE if phase1_mode else None),
                                  repo / "apps/studio", discovery_env, output, label + "-discovery", 60,
                                  **({"deadline": deadline} if deadline is not None else {}))
                observed = {"label": label, "command": command, "status": "FAIL"}
                report[discovery_key].append(observed)
                require_observed_command(command)
                require(command["exit_code"] == 0, "partial oracle discovery process failed")
                original, document = browser_report_original(output / (label + "-discovery.json"))
                verified = (phase1_service47_report(document, filename, titles, listing=True) if phase1_mode else
                            camera_eight_report(document, filename, titles, listing=True) if camera_mode
                            else partial_oracle_discovery(document, filename, titles))
                observed.update(original_report=original, verified=verified, status="LISTED_NOT_EXECUTED")
            if phase1_mode:
                phase1_service47_discovery_complete(report[discovery_key])
            if camera_mode:
                all_ids = [identity["id"] for row in report[discovery_key] for identity in row["verified"]["identities"]]
                require(len(all_ids) == len(set(all_ids)) == 8, "Camera two-list original IDs are not eight distinct identities")
            require(source_observation(repo, source_mode) == before, "source changed during partial oracle listing")
            require(digest(binary) == {key: report["service_binary"][key] for key in ("bytes", "sha256")}
                    and digest(cli) == report["playwright_cli"], "partial oracle input changed during listing")
        for label, filename, titles in selections:
            case_env = dict(env)
            stores = output / (label + "-stores")
            if not listed_mode:
                stores.mkdir()
            case_env["MOTIONWRIGHT_SERVICE_TEST_BIN"] = str(binary)
            case_env["MOTIONWRIGHT_SERVICE_TEST_TMPDIR"] = str(stores)
            case_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output / (label + ".json"))
            if phase1_branch_admission_label(label):
                case_env.update(branch_combination_environment(repo, before, binary, retained_binary,
                    {key: report["service_binary"][key] for key in ("bytes", "sha256")}, scope=source_mode))
            argv = service_command(node, cli, output, (label, filename, titles),
                                   scope=CAMERA_EIGHT_SCOPE if camera_mode else PHASE1_SERVICE47_SCOPE if phase1_mode else None)
            if camera_mode:
                report["camera_resource_observations"].append(camera_eight_resources(repo, target, phase="execute-" + label))
                intent = camera_eight_intent(label, titles, argv, repo / "apps/studio", before, report["service_binary"])
                intent_path = output / (label + "-intent.json")
                selection = {"label": label, "expected_tests": len(titles), "status": "PRE_LAUNCH",
                             "attempted": False, "launch_requested": False, "original_pid": None,
                             "original_exit": None, "validated_cases": 0}
                report["selections"].append(selection)
                write_new(intent_path, intent)
                identity, retained_intent = browser_report_original(intent_path)
                require(retained_intent == intent, "Camera pre-dispatch selection intent changed")
                selection.update(intent=identity, launch_requested=True)
            try:
                command = execute(argv, repo / "apps/studio", case_env, output, label,
                                  CAMERA_EIGHT_TIMEOUTS[label] if camera_mode else
                                  PHASE1_SERVICE47_TIMEOUTS[label] if phase1_mode else
                                  PARTIAL_ORACLE_TIMEOUTS[label] if partial_mode else SELECTION_TIMEOUTS[label],
                                  **({"deadline": deadline} if deadline is not None else {}))
            except UnknownObservation as error:
                if camera_mode:
                    camera_eight_record_attempt(selection, error.command)
                raise
            if camera_mode:
                camera_eight_record_attempt(selection, command)
            else:
                selection = {"label": label, "expected_tests": len(titles), "command": command, "status": "FAIL"}
                report["selections"].append(selection)
            require_observed_command(command)
            try:
                result_path = output / (label + ".json")
                selection["original_report"] = digest(result_path)
                require(result_path.stat().st_size <= 64 * 1024 * 1024, "report exceeds bound")
                document = json.loads(result_path.read_bytes())
                if phase1_mode:
                    discovered = next(row["verified"] for row in report[discovery_key] if row["label"] == label)
                    selection["verified"] = phase1_service47_report(document, filename, titles, listing=False, expected=discovered)
                elif camera_mode:
                    discovered = next(row["verified"] for row in report[discovery_key] if row["label"] == label)
                    selection["verified"] = camera_eight_report(document, filename, titles, listing=False, expected=discovered)
                else:
                    verifier = validate_browser_execution if partial_mode else validate_report
                    selection["verified"] = verifier(document, filename, titles)
                require(command["exit_code"] == 0, "selected process did not exit zero")
                selection["status"] = "PASS"
                if camera_mode:
                    selection["validated_cases"] = len(titles)
            except Exception as error:
                selection["error"] = str(error)
            if camera_mode:
                require(selection["status"] == "PASS", "Camera original execution or result admission failed")
            if command["exit_code"] is None:
                break
        report["source_after"] = source_observation(repo, source_mode) if source_mode else source_observation(repo)
        require(report["source_after"] == before, "source changed during browser execution")
        require(digest(binary) == {k: report["service_binary"][k] for k in ("bytes", "sha256")},
                "service executable changed")
        require(digest(cli) == report["playwright_cli"], "Playwright CLI changed")
        for name in ("cargo", "node"):
            require(observed_tool(Path(report[name]["path"])) == report[name], "service tool changed: " + name)
        require(digest(retained_binary) == digest(binary), "retained service binary changed")
        require(len(report["selections"]) == len(selections)
                and all(x["status"] == "PASS" for x in report["selections"]), "required selection failed")
        if phase1_mode:
            phase1_service47_phase_order(report)
            report["phase1_result"] = {"ordinary_service": 20, "closure_capture": 27, "native_graph": "NOT_SELECTED",
                                      "catalogue_sha256": PHASE1_SERVICE47_CATALOG_SHA256, "formal_acceptance": False}
        report["status"] = "PASS"
    except Phase1Service47BudgetDenied as error:
        report.update(error=str(error), budget_denial=error.observation, terminal_custody="ORIGINALS_UNSEALED_NO_RETRY")
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report["error"] = str(error)
    if camera_mode:
        started = sum(row["expected_tests"] for row in report["selections"] if row.get("attempted") is True)
        report["camera_execution"] = {
            "selected_cases": 8, "launch_requested_cases": sum(row["expected_tests"] for row in report["selections"]
                                                                 if row.get("launch_requested") is True),
            "selected_cases_in_started_processes": started, "validated_cases": sum(row.get("validated_cases", 0) for row in report["selections"]),
            "state": ("UNKNOWN" if report["status"] == "UNKNOWN" else "VALIDATED_ALL_SELECTED_ORIGINALS"
                      if report["status"] == "PASS" else "ATTEMPTED_NOT_VALIDATED" if started else "NOT_RUN"),
            "retry": "FORBIDDEN", "formal_acceptance": False}
    if deadline is not None:
        import time
        report["phase1_deadline"] = {"original_sha256": os.environ[PHASE1_SERVICE47_DEADLINE_ENV],
            "deadline_mono_ns": deadline.deadline_ns, "finished_mono_ns": time.monotonic_ns(),
            "admissions": deadline.admissions, "unknown": deadline.unknown, "retry": "FORBIDDEN"}
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"],
                      "selected_tests": sum(x["expected_tests"] for x in report["selections"]),
                      "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1





def validate_claim_source_output(exit_code, output, package):
    """Require each original domain/service assertion, not just success totals."""
    require(package in ("domain", "service"), "unknown Claim contract target")
    cases = CLAIM_SOURCE_RUST_CASES[package]
    expected = 7 if package == "domain" else 8
    require(isinstance(cases, tuple) and len(cases) == len(set(cases)) == expected,
            "Claim contract inventory differs")
    require(type(exit_code) is int and exit_code == 0 and isinstance(output, str),
            "Claim contract process must exit successfully")
    rows = re.findall(r"^test (\S+) \.\.\. (\S+)[^\n]*$", output, re.MULTILINE)
    summaries = re.findall(r"^test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                           r"(\d+) measured; (\d+) filtered out;(?:[^\n]*)$", output, re.MULTILINE)
    require(len(rows) == len({name for name, _ in rows}) == expected
            and {name for name, _ in rows} == set(cases)
            and all(status == "ok" for _, status in rows)
            and summaries == [("ok", str(expected), "0", "0", "0", "0")],
            "Claim original closed result differs")
    return {"status": "PASS", "package": package, "cases": sorted(cases),
            "passed": expected, "ignored": 0, "formal_acceptance": False}


def run_claim_source_contracts(repo, output):
    repo, output = repo.resolve(), output.resolve()
    require(repo.is_dir() and not output.exists() and not output.is_relative_to(repo),
            "fresh isolated Claim contract output required")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "motionwright.claim-source-contract-ci/1", "status": "FAIL", "commands": [],
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "job": os.environ.get("GITHUB_JOB"), "native_render": "NOT_RUN", "installed_tauri": "NOT_RUN",
              "human_acceptance": False, "formal_acceptance": False}
    try:
        require(sys.platform.startswith("linux"), "closed Claim contract verification requires Linux")
        before = source_observation(repo)
        report["source_before"] = before
        write_new(output / "source-before.json", before)
        cargo, rustc = shutil.which("cargo"), shutil.which("rustc")
        require(cargo is not None and rustc is not None, "cargo and rustc required")
        # Preserve rustup's invocation basename while fingerprinting the resolved executable.
        tools = {name: {"invoked_path": str(Path(path).absolute()),
                        "path": str(Path(path).resolve(strict=True)),
                        **digest(Path(path).resolve(strict=True))}
                 for name, path in (("cargo", cargo), ("rustc", rustc))}
        report["tools"] = tools
        env = dict(os.environ)
        for variable in PROCESS_ENVIRONMENT_KEYS:
            env.pop(variable, None)
        env.update(CARGO_TERM_COLOR="never", RUST_BACKTRACE="0")
        commands = (("rustc-identity", [rustc, "-Vv"], 60),
                    ("cargo-identity", [cargo, "-V"], 60),
                    ("domain", [cargo, "test", "--locked", "-p", "motionwright-domain",
                                "--test", "claim_source_edit", "--", "--test-threads=1"], 1200),
                    ("service", [cargo, "test", "--locked", "-p", "motionwright-service",
                                 "--test", "claim_source_edit", "--", "--test-threads=1"], 1200),
                    ("native-consumer-check", [cargo, "check", "--locked", "-p", "motionwright-native",
                                               "--all-targets"], 1200))
        for label, argv, timeout in commands:
            command = execute(argv, repo, env, output, label, timeout)
            report["commands"].append({"label": label, **command})
            require_observed_command(command)
            require(command["exit_code"] == 0, "Claim contract command failed: " + label)
            if label in ("domain", "service"):
                path = output / (label + ".stdout.log")
                require(digest(path) == {key: command["stdout"][key] for key in ("bytes", "sha256")},
                        "original Claim contract log changed")
                report[label] = validate_claim_source_output(
                    command["exit_code"], path.read_text(encoding="utf-8"), label)
        report["source_after"] = source_observation(repo)
        require(report["source_after"] == before, "Claim contract source changed")
        for identity in tools.values():
            require(digest(Path(identity["path"])) == {key: identity[key] for key in ("bytes", "sha256")},
                    "Claim contract executable changed")
        report.update(status="PASS", native_consumer="COMPILED_ONLY_NOT_EXECUTED")
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command,
                      terminal_custody="UNSEALED_NO_FURTHER_COMMANDS")
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error))
    write_new(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json")}))
    return 0 if report["status"] == "PASS" else 1

def claim_source_main(arguments):
    parser = argparse.ArgumentParser(description="Verify exact Claim domain/service contracts and native consumer compilation.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    return run_claim_source_contracts(args.repo, args.output)



# This closed profile is additive; historical selectors retain their own scope.
STUDIO68_SOURCE = "278f6777b999275e1d7c69e46e91a6439c172e90"
STUDIO68_TREE = "c6a1eea4ae15e6b041b18e1880dbec97975d0c08"
STUDIO68_SDK = "01d4a99a8473273fda5c61b7220d6da1bee94786"
STUDIO68_SCOPE = "studio-service-68"
STUDIO68_WORKFLOW = ".github/workflows/studio-service-verification.yml"
# The eight admitted phase caps total 18660 seconds. Preparation and transition
# custody consume the remaining 1140 seconds of the same original deadline.
STUDIO68_PHASE_SECONDS = {"product_g0": 1200, "dependencies": 900, "clippy": 900,
                          "build": 1200, "list": 120, "execution": 8460,
                          "readback": 5400, "retention": 480}
STUDIO68_SECONDS = 19800
STUDIO68_PREPARATION_SECONDS = 300
STUDIO68_CLEANUP_RESERVE = 30
STUDIO68_FILES = ("branch-authoring-service.spec.ts", "brand-authoring-service.spec.ts",
                  "component-distillation-service.spec.ts", "review-service.spec.ts",
                  "production-design-service.spec.ts")
STUDIO68_CASES = (('branch-authoring-service.spec.ts',
  'causal: a rejected branch creation preserves the exact authored name',
  104,
  1,
  '90d81b47503d2bbf2302-03dd744d82a4d188a156'),
 ('branch-authoring-service.spec.ts',
  'causal: a late original branch reply preserves a later name',
  132,
  1,
  '90d81b47503d2bbf2302-69a8ce3e7f07f59c290a'),
 ('branch-authoring-service.spec.ts',
  'causal: an unsaved branch name survives App navigation and reload without a mutation',
  144,
  1,
  '90d81b47503d2bbf2302-837b206a282f92967e52'),
 ('branch-authoring-service.spec.ts',
  'one explicit name creates one editable branch through SQLite and a new service process',
  156,
  1,
  '90d81b47503d2bbf2302-7fcc1b9afdac0ec5f3cd'),
 ('branch-authoring-service.spec.ts',
  'UTF-8 branch limit keeps invalid input without sending it',
  178,
  1,
  '90d81b47503d2bbf2302-07269cc82a6392981ee0'),
 ('branch-authoring-service.spec.ts',
  'silent branch recovery bytes loss blocks creation until exact readback',
  189,
  3,
  '90d81b47503d2bbf2302-9183ca5389583e9959f2'),
 ('branch-authoring-service.spec.ts',
  'silent branch recovery pointer loss blocks creation until exact readback',
  189,
  3,
  '90d81b47503d2bbf2302-7b9660954a25d7d77b16'),
 ('branch-authoring-service.spec.ts',
  'a committed but lost branch reply survives reopen and explicit inspection without replay',
  212,
  1,
  '90d81b47503d2bbf2302-3c96405ee60004b548fc'),
 ('branch-authoring-service.spec.ts',
  'a later A-to-B-to-A branch name stays editable after its original reply and reload',
  252,
  1,
  '90d81b47503d2bbf2302-e8acd27fe1e6e8050909'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after silent pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-395b85b46216f36d0b14'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after throw pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-a997a89b17615cfe9218'),
 ('branch-authoring-service.spec.ts',
  'restoring an unconfirmed branch preserves both copies after write-then-throw pointer failure',
  268,
  3,
  '90d81b47503d2bbf2302-477df73200843765aae1'),
 ('branch-authoring-service.spec.ts',
  'an ordinary branch name survives a failed first pointer read before one explicit creation',
  432,
  3,
  '90d81b47503d2bbf2302-f9d90bc2f8a9a17f4812'),
 ('branch-authoring-service.spec.ts',
  'an UNKNOWN branch and a deliberately cleared name survive failed first pointer read without replay',
  475,
  3,
  '90d81b47503d2bbf2302-455f5cb85b33c39db7c0'),
 ('branch-authoring-service.spec.ts',
  'an ordinary branch name survives a failed first copy read before one explicit creation',
  432,
  3,
  '90d81b47503d2bbf2302-32dcb94264ce18bc3d5c'),
 ('branch-authoring-service.spec.ts',
  'an UNKNOWN branch and a deliberately cleared name survive failed first copy read without replay',
  475,
  3,
  '90d81b47503d2bbf2302-9e3d078d772fde68ad7d'),
 ('branch-authoring-service.spec.ts',
  'a failed initial pointer read can recover an empty session without discarding newly typed input',
  534,
  1,
  '90d81b47503d2bbf2302-74b8225d18c9fbf996b5'),
 ('branch-authoring-service.spec.ts',
  'an unchanged SQLite branch base stays UNKNOWN until a real peer advances it, preserving later input without replay',
  573,
  1,
  '90d81b47503d2bbf2302-47a82a0a62a6289bc25e'),
 ('brand-authoring-service.spec.ts',
  'Brand policy, taste and decision saves preserve other raw fields through actual App navigation and SQLite restart',
  255,
  1,
  '06cc3d3c07c1041c9a27-51f7b6f623b36767da68'),
 ('brand-authoring-service.spec.ts',
  'a real taste commit with a lost original reply stays UNKNOWN through later typing, reload and saved-history '
  'inspection',
  324,
  1,
  '06cc3d3c07c1041c9a27-9465e47deb86aa6a2397'),
 ('brand-authoring-service.spec.ts',
  'a silently dropped pending brand copy issues zero grants and Retry only confirms bytes before a separate Save',
  391,
  1,
  '06cc3d3c07c1041c9a27-849b356225084e762e2b'),
 ('brand-authoring-service.spec.ts',
  'a failed original UNKNOWN recovery read cannot be escaped by restoring a different ordinary copy',
  441,
  1,
  '06cc3d3c07c1041c9a27-a68edfe56d68f13b33ef'),
 ('brand-authoring-service.spec.ts',
  'a reviewed selected campaign changes the real semantic Hero while preserving other scenes and proposal bytes '
  'through SQLite restart',
  515,
  1,
  '06cc3d3c07c1041c9a27-e1c75d2eccd5f3dd37b8'),
 ('brand-authoring-service.spec.ts',
  'Brand governance retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  610,
  3,
  '06cc3d3c07c1041c9a27-6d676bb999dd2eed0f61'),
 ('brand-authoring-service.spec.ts',
  'Brand campaign retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  610,
  3,
  '06cc3d3c07c1041c9a27-53c2351f2f0d5225d6b3'),
 ('brand-authoring-service.spec.ts',
  'an explicitly cleared brand rule issues zero grants and remains empty after a real SQLite restart',
  724,
  1,
  '06cc3d3c07c1041c9a27-bd6593450d22b9821a8a'),
 ('component-distillation-service.spec.ts',
  'Distillation actual App restores author input after its first pointer read fails and commits frozen samples plus '
  'independent review through SQLite restart',
  227,
  1,
  '980e3c140a1ad4daf80a-a0a5921a0ff77decc2e8'),
 ('component-distillation-service.spec.ts',
  'Distillation actual original proposal receipt preserves later author text through App remount',
  273,
  1,
  '980e3c140a1ad4daf80a-609b0fd344367726b15c'),
 ('component-distillation-service.spec.ts',
  "Distillation actual review receipt cannot erase another proposal's authoring buffer",
  290,
  1,
  '980e3c140a1ad4daf80a-c3beec29dbc70240706f'),
 ('component-distillation-service.spec.ts',
  'Distillation later rationale survives its actual immutable review receipt and cannot rewrite the saved review',
  308,
  1,
  '980e3c140a1ad4daf80a-d39e7c6c2f37f6583a1b'),
 ('component-distillation-service.spec.ts',
  'Distillation author actual reply loss retains UNKNOWN and later cleared input after a first pointer read failure, '
  'reload and current-history review',
  326,
  3,
  '980e3c140a1ad4daf80a-b4e89ac7373d28743f60'),
 ('component-distillation-service.spec.ts',
  'Distillation review actual reply loss retains UNKNOWN and later cleared input after a first pointer read failure, '
  'reload and current-history review',
  326,
  3,
  '980e3c140a1ad4daf80a-0ab52fca11b2408cb9bc'),
 ('component-distillation-service.spec.ts',
  'Distillation author pre-dispatch transport interruption keeps UNKNOWN at the saved base until a real peer advances '
  'it, without replay',
  381,
  3,
  '980e3c140a1ad4daf80a-9d4ab473013aa402ad0d'),
 ('component-distillation-service.spec.ts',
  'Distillation review pre-dispatch transport interruption keeps UNKNOWN at the saved base until a real peer advances '
  'it, without replay',
  381,
  3,
  '980e3c140a1ad4daf80a-720931aa5dc13550890a'),
 ('component-distillation-service.spec.ts',
  'Distillation data recovery failure sends no actual grant or write; healthy explicit retry retains source choices',
  479,
  3,
  '980e3c140a1ad4daf80a-91ce109d25991c55fec6'),
 ('component-distillation-service.spec.ts',
  'Distillation pointer recovery failure sends no actual grant or write; healthy explicit retry retains source choices',
  479,
  3,
  '980e3c140a1ad4daf80a-2c027117de6f56c1004b'),
 ('component-distillation-service.spec.ts',
  'Distillation author retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  507,
  3,
  '980e3c140a1ad4daf80a-bb0947a50f156b5ef4bc'),
 ('component-distillation-service.spec.ts',
  'Distillation review retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  507,
  3,
  '980e3c140a1ad4daf80a-14367a4c72c616cf5762'),
 ('production-design-service.spec.ts',
  'Production plan raw drafts, claim references and content-bound approval survive actual App navigation and SQLite '
  'restart',
  132,
  1,
  '67037f4586e4a2ca8702-8ad929248f0fd87ebc6d'),
 ('production-design-service.spec.ts',
  'Production Hero updates retain editable node identities and a human override; detach preserves objects across '
  'SQLite restart',
  172,
  1,
  '67037f4586e4a2ca8702-4ed7cb9d8f75baba6f1c'),
 ('production-design-service.spec.ts',
  'a held original Production component receipt preserves later typing across actual App remount and a separate save',
  206,
  3,
  '67037f4586e4a2ca8702-a34558b890b00165d4b3'),
 ('production-design-service.spec.ts',
  'a held original Production plan receipt preserves later typing across actual App remount and a separate save',
  206,
  3,
  '67037f4586e4a2ca8702-5fc397580d6aa396fb9d'),
 ('production-design-service.spec.ts',
  'an unknown Production plan committed result survives reload without replay and needs a fresh history review',
  239,
  3,
  '67037f4586e4a2ca8702-1f3efa40f9876dca293f'),
 ('production-design-service.spec.ts',
  'an unknown Production plan matching-peer result survives reload without replay and needs a fresh history review',
  239,
  3,
  '67037f4586e4a2ca8702-9c2e3445611ec4a8fba7'),
 ('production-design-service.spec.ts',
  'Production data recovery failure sends no real service grant or change; explicit healthy retry persists the '
  'retained draft',
  277,
  3,
  '67037f4586e4a2ca8702-a0802d6a3dfe9da72ff3'),
 ('production-design-service.spec.ts',
  'Production pointer recovery failure sends no real service grant or change; explicit healthy retry persists the '
  'retained draft',
  277,
  3,
  '67037f4586e4a2ca8702-8c9ecdfab5d436769aca'),
 ('production-design-service.spec.ts',
  'Production retries an initial pointer read for ordinary input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-394bae365fdaaf21214f'),
 ('production-design-service.spec.ts',
  'Production retries an initial pointer read for UNKNOWN input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-ebd4aded24cc5d685a18'),
 ('production-design-service.spec.ts',
  'Production retries an initial copy read for ordinary input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-90b4754b9666091fb1d0'),
 ('production-design-service.spec.ts',
  'Production retries an initial copy read for UNKNOWN input without replacing raw intent or replaying a real '
  'operation',
  344,
  3,
  '67037f4586e4a2ca8702-0be19c85e2f6ba251fb7'),
 ('production-design-service.spec.ts',
  'Production confirms an empty pointer after an initial read failure while retaining the input typed during the '
  'blocker',
  445,
  1,
  '67037f4586e4a2ca8702-f03a03c37c7e24a57882'),
 ('production-design-service.spec.ts',
  'Production component retains its first observed UNKNOWN bytes when a failed recovery read is followed by a '
  'same-writer replacement',
  476,
  3,
  '67037f4586e4a2ca8702-9fce096231ad683cfeb6'),
 ('production-design-service.spec.ts',
  'Production plan retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer '
  'replacement',
  476,
  3,
  '67037f4586e4a2ca8702-ff2419213ef0ab7dd644'),
 ('production-design-service.spec.ts',
  'opaque source attachment raw input and a silent pointer failure preserve the existing asset before one explicit '
  'SQLite attachment',
  595,
  1,
  '67037f4586e4a2ca8702-39d8e0f11784333f76cb'),
 ('production-design-service.spec.ts',
  'opaque source attachment late real ACK retains two scene drafts and permits only a separate explicit later '
  'attachment',
  644,
  1,
  '67037f4586e4a2ca8702-de940abf33c28468485b'),
 ('production-design-service.spec.ts',
  'opaque source attachment lost committed service reply preserves the exact submitted capsule and later cleared input '
  'without replay',
  683,
  1,
  '67037f4586e4a2ca8702-565026398cb1219ce997'),
 ('production-design-service.spec.ts',
  'opaque source attachment pre-dispatch delivery interruption stays UNKNOWN on N until a real peer advances SQLite, '
  'with no original replay',
  723,
  1,
  '67037f4586e4a2ca8702-dd1595820641a3998c40'),
 ('review-service.spec.ts',
  'review raw range survives actual App navigation and reload before one exact SQLite commit and restart',
  132,
  1,
  '0f11b70400fd2e6eaf8a-d7a91be5cd90ec57d924'),
 ('review-service.spec.ts',
  'a held original review reply preserves later input before a distinct granted save',
  162,
  1,
  '0f11b70400fd2e6eaf8a-dda45573a1af59fa6501'),
 ('review-service.spec.ts',
  'a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent',
  181,
  1,
  '0f11b70400fd2e6eaf8a-cdad8227f658df285515'),
 ('review-service.spec.ts',
  'ABA author input survives lost review reply, inspection, separate save and SQLite restart',
  217,
  1,
  '0f11b70400fd2e6eaf8a-01f395dac753f606e235'),
 ('review-service.spec.ts',
  'denied review custody prevents any SQLite mutation until a confirmed copy and explicit save',
  257,
  1,
  '0f11b70400fd2e6eaf8a-5b269e07dc56c2b6213f'),
 ('review-service.spec.ts',
  'an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement',
  280,
  1,
  '0f11b70400fd2e6eaf8a-b230662f6c6cc8a4565a'),
 ('review-service.spec.ts',
  'an actual saved alternative retains raw rationale and records only an open manual review',
  300,
  1,
  '0f11b70400fd2e6eaf8a-82a1d62736a84b080146'),
 ('review-service.spec.ts',
  'selected review deliverable, language and rational range persist through actual App reload and SQLite restart',
  326,
  1,
  '0f11b70400fd2e6eaf8a-058b23f0cb72848d5459'),
 ('review-service.spec.ts',
  'later explicit empty review profile survives a held original reply and saves separately to SQLite',
  366,
  1,
  '0f11b70400fd2e6eaf8a-488c23f83c87d066b732'),
 ('review-service.spec.ts',
  'lost reply keeps original selected review profile UNKNOWN while a later empty selection survives actual SQLite '
  'recovery',
  393,
  1,
  '0f11b70400fd2e6eaf8a-05217b4a6100f079e08a'),
 ('review-service.spec.ts',
  'pre-dispatch review transport interruption keeps the saved base UNKNOWN until an independent SQLite peer advances '
  'it without replay',
  440,
  1,
  '0f11b70400fd2e6eaf8a-ee41ca09d4b06f0c2fd7'))
STUDIO68_PRODUCT_PINS = {'Cargo.lock': '0c6a330de85c99f0913f4fc9e1b2af794d8cae239085952dd6400a017c67205b',
 'Cargo.toml': '8c63f973bcbc43f45c26f074259f36aa8b3a46595657d34eecf86093cddca2c9',
 'SOURCE_LOCK.json': 'aeb60e60036d29ad96abec0dc7acbcf3302a4411c16a7ea37c4b3c58c36e0b4f',
 'apps/studio/package-lock.json': 'bf7e12b36faff3d553033f903560070974774700f404ff6704ed0942094bc994',
 'apps/studio/package.json': '428526cf98577d3c960a1f13731cf89a1c1c19cb7a4e653a420d1e832c75c9b4',
 'apps/studio/playwright.config.ts': 'facf22ed7a17d6150f0bf78a9eeb4e86daed7bd746b7de59dfb1b84ea057d38c',
 'apps/studio/tests/branch-authoring-service.spec.ts': '17646fc6324af8a9da0560fe7f71cc21c4a8b56f270c6c30498a585011f4838d',
 'apps/studio/tests/brand-authoring-service.spec.ts': '0917b454baba213fd75525fc2d6d36ec39288f7e0fa81ad8ae1031fe7baa52b7',
 'apps/studio/tests/component-distillation-service.spec.ts': '64c7f67179644ccd91480e4bdfd77e260e6a0c6ec8f295803aa16e04f0872d51',
 'apps/studio/tests/production-design-service.spec.ts': '019cc59f2fc2ada5a14f85f45525677715528ae32d8c465aec0967e41d9ecdcb',
 'apps/studio/tests/review-service.spec.ts': 'b8c1597384c7bf9de4c3d8f38c6b7839ee3ed30c4fd52e2d4583f34ca2d6b168',
 'apps/studio/tests/support/scopedChangeService.ts': '2fba83ace30c032e11f3c108203c0d7a0e98821d79d081717281194108138f1d',
 'apps/studio/vite.config.ts': '6743b496e0276b2e7197330d8c78c3782fb1eaa769d55d8b59c98ef24255294a',
 'crates/service/Cargo.toml': '029581d64961e5add8afe5ca83bb197dcee15968f6abe2e8a9e3f6ed3b316333',
 'crates/service/examples/studio_review_session.rs': '35cc60ab2bf1c3bbe4dc1399ad59a7d7117ea2061ca6bf9f4b6b74a9cd363c98',
 'rust-toolchain.toml': '887f9be066a15585a2c583578e84b0fcb541126d81546276bad3d2ff00d61167'}
STUDIO68_HELPER_PINS = {
    "ci_storage_apply_recovery": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "ci_foundation_causal": "1d31c58aa7d63c927ceb2518282a433e0f728fc125da8978fb635b18cff354c2",
    "ci_sdk_native_source": "65bcda73f16423ebb644a0979bca3a7927f8e7337c124702874bce51adcf8127",
}
STUDIO68_READER_PINS = {
    "tooling/service_readback/service_archive_readback_r3.py": "71319a62dab38779fd29a96d7b88c7e32ddb24a770a771e8db14bee6f30f73c3",
    "tooling/service_readback/test_readback_r3.py": "b457817f1e05821ef84b1c7c78cca0a3b825264c6cab470747bbd9b73188e855",
}
STUDIO68_POLICIES = ("source_policy.py", "public_surface_policy.py",
                     "acceptance_policy.py", "creative_status_policy.py")
STUDIO68_RUNNER_SHA256 = "b2b2e4b0aa16ef5740505f5ab84e12e8f2b5a9fb431e89c7113441ad2c53a49b"

def studio68_context(environment, product_source, platform=None):
    platform = sys.platform if platform is None else platform
    require(platform == "linux" and environment.get("RUNNER_OS") == "Linux"
            and environment.get("RUNNER_ARCH") == "X64", "Studio68 requires Linux x64")
    require(environment.get("GITHUB_ACTIONS") == "true"
            and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and environment.get("GITHUB_REPOSITORY") == "seradotcom/motionwright"
            and environment.get("GITHUB_JOB") == STUDIO68_SCOPE,
            "Studio68 requires its explicit workflow job")
    require(product_source == STUDIO68_SOURCE, "Studio68 product source is not reviewed")
    require(all(re.fullmatch(r"[0-9a-f]{40}", environment.get(k, ""))
                for k in ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA"))
            and environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
            and environment["GITHUB_SHA"] != product_source,
            "Studio68 gate and product must have separate exact source identities")
    require(environment.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")),
            "Studio68 permits the first observed attempt only; no replay")
    require(environment.get("GITHUB_WORKFLOW_REF", "").startswith(
        "seradotcom/motionwright/" + STUDIO68_WORKFLOW + "@"), "Studio68 workflow identity")
    keys = ("GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_REPOSITORY",
            "GITHUB_JOB", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH")
    return {"gate_source_sha": environment["GITHUB_SHA"],
            "executed_product_source_sha": product_source, "github": {k: environment[k] for k in keys}}


def studio68_environment(environment, root):
    forbidden = {"RUSTC", "RUSTDOC", "RUSTFMT", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                 "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTDOCFLAGS", "CARGO_ENCODED_RUSTDOCFLAGS",
                 "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "NODE_OPTIONS", "NODE_PATH",
                 "TS_NODE_PROJECT", "NPM_CONFIG_USERCONFIG", "npm_config_userconfig",
                 "CC", "CXX", "AR", "CFLAGS", "CXXFLAGS", "LDFLAGS"}
    bad = [k for k, v in environment.items() if v and (k in forbidden or k.startswith(
        ("GIT_", "SEMWRIGHT_", "MOTIONWRIGHT_", "CARGO_TARGET_", "CARGO_PROFILE_", "CARGO_ALIAS_")))]
    require(not bad, "Studio68 inherited execution override: " + ",".join(sorted(bad)))
    value = dict(environment)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"):
        value.pop(key, None)
    value.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", CARGO_BUILD_JOBS="1",
                 CARGO_HOME=str(root / "cargo"), CARGO_TARGET_DIR=str(root / "target"),
                 NPM_CONFIG_CACHE=str(root / "npm"), PLAYWRIGHT_BROWSERS_PATH=str(root / "browsers"),
                 TMP=str(root / "tmp"), TEMP=str(root / "tmp"), TMPDIR=str(root / "tmp"),
                 XDG_CACHE_HOME=str(root / "xdg"), CI="true")
    return value


class Studio68Clock:
    def __init__(self, started_ns):
        import time
        now = time.monotonic_ns()
        require(type(started_ns) is int and 0 < started_ns <= now, "Studio68 original clock")
        self.started_ns, self.last_ns = started_ns, now
        self.deadline_ns = started_ns + STUDIO68_SECONDS * 1_000_000_000
        self.retention_started = False

    def remaining(self, cap, *, reserve=None):
        import time
        if reserve is None:
            reserve = STUDIO68_CLEANUP_RESERVE + (0 if self.retention_started else STUDIO68_PHASE_SECONDS["retention"])
        require(type(cap) is int and 0 < cap <= STUDIO68_SECONDS
                and type(reserve) is int and 0 <= reserve <= STUDIO68_CLEANUP_RESERVE + STUDIO68_PHASE_SECONDS["retention"],
                "Studio68 finite phase budget")
        now = time.monotonic_ns()
        require(now >= self.last_ns, "Studio68 clock regressed")
        self.last_ns = now
        remaining = (self.deadline_ns - now) / 1_000_000_000 - reserve
        require(remaining > 0, "Studio68 original deadline exhausted")
        return min(cap, remaining)

    def phase(self, seconds, *, retention=False):
        require(type(retention) is bool and not self.retention_started
                and (not retention or seconds == STUDIO68_PHASE_SECONDS["retention"]),
                "Studio68 retention is a single terminal phase")
        if retention:
            self.retention_started = True
        return Studio68Phase(self, seconds)


class Studio68Phase:
    def __init__(self, clock, seconds):
        import time
        self.clock = clock
        self.deadline_ns = time.monotonic_ns() + int(clock.remaining(seconds) * 1_000_000_000)

    def remaining(self, cap):
        import time
        remaining = (self.deadline_ns - time.monotonic_ns()) / 1_000_000_000
        require(remaining > 0, "Studio68 phase deadline exhausted")
        return min(remaining, self.clock.remaining(cap))


def studio68_original(path, limit):
    path = Path(path)
    archive_ancestors(path)
    before = archive_stat(path)
    require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "Studio68 original byte bound")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(os.open(path, flags), "rb") as stream:
        require(archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "Studio68 original changed before open")
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and len(raw) <= limit
                and archive_identity(os.fstat(stream.fileno())) == archive_identity(before),
                "Studio68 original changed during read")
    require(archive_identity(archive_stat(path)) == archive_identity(before),
            "Studio68 original changed after read")
    return raw, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def studio68_load_helpers(repo):
    import importlib.util
    import types
    package = sys.modules.get("tooling")
    require(package is None or list(getattr(package, "__path__", ())) == [str(repo / "tooling")],
            "Studio68 foreign tooling package already loaded")
    if package is None:
        package = types.ModuleType("tooling")
        package.__path__ = [str(repo / "tooling")]
        sys.modules["tooling"] = package
    for name, expected in STUDIO68_HELPER_PINS.items():
        path = repo / "tooling" / (name + ".py")
        raw, identity = studio68_original(path, 2 * 1024 * 1024)
        require(identity["sha256"] == expected, "Studio68 shared helper source changed: " + name)
        qualified = "tooling." + name
        require(qualified not in sys.modules, "Studio68 shared helper was preloaded")
        spec = importlib.util.spec_from_file_location(qualified, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        setattr(package, name, module)
        exec(compile(raw, str(path), "exec"), module.__dict__)
    return sys.modules["tooling.ci_sdk_native_source"]


def studio68_snapshot(repo, source, git, *, expected_tree=None, allow_node_modules=False, cargo_marker=False):
    require(re.fullmatch(r"[0-9a-f]{40}", source), "Studio68 full source SHA")
    require(git(repo, "rev-parse", "HEAD").decode().strip() == source, "Studio68 checkout SHA changed")
    tree = git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    require(expected_tree is None or tree == expected_tree, "Studio68 source tree changed")
    require(not git(repo, "diff", "--name-only", "HEAD", "--"), "Studio68 tracked source is dirty")
    generated = []
    for options in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
        generated.extend(x.decode("utf-8") for x in git(repo, "ls-files", "-z", *options).split(b"\0") if x)
    require(all((allow_node_modules and x.startswith("apps/studio/node_modules/"))
                or (cargo_marker and x == ".cargo-ok") for x in generated),
            "Studio68 unbound checkout input outside the declared npm tree")
    listing = git(repo, "ls-tree", "-r", "-z", source)
    files, folded, total = {}, set(), 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        header, encoded = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        name = encoded.decode("utf-8")
        require(mode in ("100644", "100755") and kind == "blob"
                and re.fullmatch(r"[0-9a-f]{40}", oid)
                and name and not name.startswith("/") and "\\" not in name
                and all(x not in ("", ".", "..", ".git") for x in name.split("/"))
                and name.casefold() not in folded and len(files) < 30000,
                "Studio68 source manifest contains an unsupported member")
        raw, identity = studio68_original(repo / name, 256 * 1024 * 1024)
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid,
                "Studio68 working bytes differ from the exact Git blob: " + name)
        total += len(raw)
        require(total <= 1024 * 1024 * 1024, "Studio68 source closure byte bound")
        folded.add(name.casefold())
        files[name] = {"blob": oid, "mode": mode, **identity}
    require(files, "Studio68 source closure is empty")
    return {"sha": source, "tree": tree, "files": files, "bytes": total,
            "listing_sha256": hashlib.sha256(listing).hexdigest()}


def studio68_product_identity(snapshot):
    require(snapshot.get("sha") == STUDIO68_SOURCE and snapshot.get("tree") == STUDIO68_TREE,
            "Studio68 product identity differs")
    require(all(snapshot.get("files", {}).get(path, {}).get("sha256") == value
                for path, value in STUDIO68_PRODUCT_PINS.items()), "Studio68 product input pin differs")
    return {"sha": snapshot["sha"], "tree": snapshot["tree"], "sdk_lock_sha": STUDIO68_SDK}


def studio68_command(argv, cwd, environment, output, label, cap, clock, commands, *, defer_exit_status=False):
    require(type(defer_exit_status) is bool and (not defer_exit_status or label in
            ("product-contracts", "independent-readback")), "Studio68 deferred exit has no bound inner receipt")
    require(label not in {x["label"] for x in commands}, "Studio68 command label was already attempted")
    record = {"label": label, "argv": list(argv), "status": "STARTED"}
    commands.append(record)
    try:
        result = execute(argv, cwd, environment, output, label, clock.remaining(cap))
        record.update(result)
        require_observed_command(result)
        require(defer_exit_status or result["exit_code"] == 0, "Studio68 command failed: " + label)
        clock.remaining(1)
        streams = {}
        for stream in ("stdout", "stderr"):
            path = output / (label + "." + stream + ".log")
            raw, identity = studio68_original(path, ARCHIVE_LIMITS["file_bytes"])
            require(identity == result[stream], "Studio68 command original changed")
            streams[stream] = raw
        record["status"] = "PASS_OBSERVED_COMMAND_ONLY" if result["exit_code"] == 0 else "OBSERVED_NONZERO_EXIT"
        return result, streams
    except UnknownObservation as error:
        record.update(status="UNKNOWN_NO_RETRY", retained_command=error.command)
        raise
    except BaseException:
        record["status"] = "FAIL_OR_PREPARATION_REJECTED"
        raise


def studio68_inner_terminal(command, receipt, identity, kind):
    """Preserve inner uncertainty even when the original observer exited nonzero."""
    require_observed_command(command)
    require(type(receipt) is dict and kind in ("product-g0", "readback"), "Studio68 inner receipt type")
    uncertain = []
    if kind == "product-g0":
        if receipt.get("status") == "UNKNOWN":
            uncertain.append("runner_status_UNKNOWN")
        for step in receipt.get("steps", ()):
            if type(step) is dict and step.get("status") == "UNKNOWN":
                uncertain.append("entrypoint_UNKNOWN:" + str(step.get("entry_point")))
    else:
        for case in receipt.get("cases", ()):
            observed = case.get("readback") if type(case) is dict else None
            if type(observed) is not dict or observed.get("transportAttempted") is not True:
                continue
            close = observed.get("close")
            if (observed.get("transportExecuted") is not True or type(close) is not dict
                    or type(close.get("returnCode")) is not int or close.get("taints") or close.get("errors")):
                uncertain.append("transport_or_capture_not_closed:" + str(case.get("case")))
    if uncertain:
        raise UnknownObservation("Studio68 inner receipt retains uncertainty; no further command or retry",
                                 {"outer_command": command, "inner_receipt": identity,
                                  "inner_kind": kind, "uncertainty": uncertain})
    require(type(command.get("exit_code")) is int and command["exit_code"] == 0,
            "Studio68 inner receipt cannot replace nonzero original exit")


def studio68_test_roster(raw, prefix):
    import ast
    module = ast.parse(raw)
    names = []
    for cls in module.body:
        if isinstance(cls, ast.ClassDef):
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                    require(ast.get_docstring(method) is None,
                            "Studio68 fixed product G0 source unexpectedly has a multiline test description")
                    names.append(prefix + "." + cls.name + "." + method.name)
    require(names and len(names) == len(set(names)), "Studio68 source test roster is empty or duplicated")
    return tuple(sorted(names))


def studio68_g0_receipt(receipt, expected, context, original_reader, summary_parser):
    require(receipt.get("schema") == "motionwright.unittest-execution/1"
            and receipt.get("executed_source_sha") == STUDIO68_SOURCE
            and receipt.get("tree_sha") == STUDIO68_TREE
            and receipt.get("runner_sha256") == STUDIO68_RUNNER_SHA256
            and receipt.get("require_complete") is True
            and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0
            and receipt.get("status") == "PASS" and not receipt.get("source_postcheck_error"),
            "Studio68 product G0 receipt is not an exact successful execution")
    require(receipt.get("python_executable_sha256") == expected["python_sha256"],
            "Studio68 G0 child Python differs from the observed executable")
    require(receipt.get("github") == {key: context["github"][key] for key in
            ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB")},
            "Studio68 product receipt relabelled the actual gate context")
    path = "tooling/tests/test_ci_canvas_service.py"
    require(receipt.get("test_definition_sha256") == {path: expected["definition"]},
            "Studio68 G0 test definition differs from F")
    require(receipt.get("source_locks") == expected["locks"], "Studio68 G0 source locks differ")
    labels = ("discovery:test_ci_canvas_service.py", "dotted:tooling.tests.test_ci_canvas_service")
    steps = receipt.get("steps")
    require(type(steps) is list and len(steps) == 2 and tuple(x.get("entry_point") for x in steps) == labels,
            "Studio68 G0 entrypoint roster changed")
    for index, step in enumerate(steps):
        suffix = (["discover", "-s", "tooling/tests", "-p", "test_ci_canvas_service.py", "-v"]
                  if index == 0 else ["tooling.tests.test_ci_canvas_service", "-v"])
        require(step.get("command") == [expected["python"], "-B", "-E", "-s", "-m", "unittest", *suffix],
                "Studio68 G0 original child argv changed")
        require(step.get("status") == "PASS" and type(step.get("exit_code")) is int and step["exit_code"] == 0,
                "Studio68 incomplete or uncertain G0 step")
        streams = {}
        for stream in ("stdout", "stderr"):
            row = step.get(stream, {})
            name = "receipt-" + str(index).zfill(2) + "-" + stream + ".log"
            require(row.get("file") == name and re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")),
                    "Studio68 G0 original locator changed")
            raw = original_reader(name)
            require(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == row["sha256"],
                    "Studio68 G0 original hash changed")
            streams[stream] = raw
        parsed = summary_parser(streams["stderr"].decode("utf-8", "strict"))
        counts = step.get("counts")
        require(type(counts) is dict and counts == parsed
                and all(type(counts.get(k)) is int for k in (
                    "tests_run", "passed", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"))
                and counts["tests_run"] == counts["passed"] == len(expected["ids"])
                and counts["terminal_status"] == "OK"
                and not any(counts[k] for k in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes")),
                "Studio68 G0 footer/count/omission mismatch")
        text = streams["stderr"].decode("utf-8", "strict").replace("\r\n", "\n")
        lines = [line for line in text.splitlines() if line.startswith("test_")]
        matches = [re.fullmatch(r"(test_[A-Za-z_0-9]+) \(([^\r\n)]+)\) \.\.\. ok", line) for line in lines]
        require(matches and all(match and match.group(2).rsplit(".", 1)[-1] == match.group(1)
                                for match in matches), "Studio68 G0 original individual terminal is not ok")
        observed = [match.group(2) for match in matches]
        normalized = [x if x.startswith("tooling.tests.") else "tooling.tests." + x for x in observed]
        require(len(normalized) == len(set(normalized)) == len(expected["ids"])
                and set(normalized) == set(expected["ids"]), "Studio68 G0 original IDs differ")
    return {"status": "PASS_PRODUCT_CONTRACTS_ONLY", "methods_per_entry": len(expected["ids"]),
            "entrypoints": list(labels), "executed_product_source_sha": STUDIO68_SOURCE}


def studio68_roster(document, *, executed=False):
    require(type(document) is dict and document.get("errors") == [], "Studio68 reporter errors")
    config = document.get("config", {})
    require(type(config.get("workers")) is int and config["workers"] == 1 and config.get("shard") is None
            and config.get("globalSetup") is None and config.get("globalTeardown") is None
            and config.get("fullyParallel") is False and config.get("forbidOnly") is True
            and config.get("grep") == {} and config.get("grepInvert") is None,
            "Studio68 reporter execution configuration changed")
    projects = config.get("projects")
    require(type(projects) is list and len(projects) == 1, "Studio68 exactly one Chromium project")
    project = projects[0]
    require(project.get("id") == project.get("name") == "chromium"
            and type(project.get("retries")) is int and project["retries"] == 0
            and type(project.get("repeatEach")) is int and project["repeatEach"] == 1,
            "Studio68 project selection/repetition changed")
    stats = document.get("stats", {})
    require(all(type(stats.get(k)) is int for k in ("expected", "unexpected", "flaky", "skipped"))
            and {k: stats[k] for k in ("expected", "unexpected", "flaky", "skipped")} ==
            ({"expected": 68, "unexpected": 0, "flaky": 0, "skipped": 0} if executed else
             {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 68}),
            "Studio68 reporter counts/omissions changed")
    rows = []
    def visit(suite):
        require(type(suite) is dict, "Studio68 malformed reporter suite")
        for spec in suite.get("specs", []):
            tests = spec.get("tests")
            require(type(tests) is list and len(tests) == 1, "Studio68 duplicate project case")
            test = tests[0]
            require(test.get("projectId") == test.get("projectName") == "chromium"
                    and test.get("expectedStatus") == "passed"
                    and type(test.get("timeout")) is int and test["timeout"] == 120000
                    and test.get("annotations", []) == [], "Studio68 case timeout/skip policy changed")
            identity = (spec.get("file"), spec.get("title"), spec.get("line"), spec.get("column"), spec.get("id"))
            require(type(identity[2]) is int and type(identity[3]) is int, "Studio68 case location type")
            if executed:
                results = test.get("results")
                require(spec.get("ok") is True and test.get("status") == "expected"
                        and type(results) is list and len(results) == 1
                        and results[0].get("status") == "passed"
                        and type(results[0].get("retry")) is int and results[0]["retry"] == 0
                        and results[0].get("errors") == [], "Studio68 result is incomplete or retried")
            else:
                require(test.get("results") == [], "Studio68 enumeration contains execution results")
            rows.append(identity)
        for nested in suite.get("suites", []):
            visit(nested)
    for suite in document.get("suites", []):
        visit(suite)
    require(len(rows) == 68 and len(set(rows)) == 68
            and len({row[4] for row in rows}) == 68 and set(rows) == set(STUDIO68_CASES),
            "Studio68 exact named roster changed")
    return {"scope": STUDIO68_SCOPE, "mode": "execution" if executed else "enumeration",
            "ids": sorted(row[4] for row in rows), "unique_cases": 68,
            "physical_service_evidence": "REQUIRES_CLOSED_STORE_READBACK"}


def studio68_playwright_argv(node, cli, results, *, listing=False):
    argv = [node, str(cli), "test", *("tests/" + name for name in STUDIO68_FILES), "--project=chromium", "--workers=1",
            "--retries=0", "--repeat-each=1", "--forbid-only", "--reporter=json", "--output=" + str(results)]
    return argv + (["--list"] if listing else [])


def studio68_build_argv(cargo):
    return [cargo, "build", "--locked", "--offline", "--jobs", "1", "-p", "motionwright-service",
            "--example", "studio_review_session", "--message-format=json"]


def studio68_binary_spec():
    return ({"package": "motionwright-service", "crate": "service", "name": "studio_review_session",
             "kind": "example", "source": "crates/service/examples/studio_review_session.rs"},)


def studio68_readback_admission(report, command, expected_binary, expected_ids):
    require_observed_command(command)
    require(type(command.get("exit_code")) is int and command["exit_code"] == 0,
            "Studio68 independent reader process failed")
    require(report.get("status") == "READBACK_VERIFIED" and report.get("errors") == []
            and type(report.get("expectedCases")) is int and report["expectedCases"] == 68
            and report.get("backendSourceShaAssertedByCaller") == STUDIO68_SOURCE
            and type(report.get("playwrightExitCodeReportedByCaller")) is int
            and report["playwrightExitCodeReportedByCaller"] == 0
            and report.get("binary") == expected_binary
            and report.get("receipt", {}).get("status") == "WRITTEN"
            and report.get("observationJournal", {}).get("status") == "CLOSED"
            and report.get("transportAttempted") is True and report.get("transportExecuted") is True,
            "Studio68 independent reader receipt is incomplete")
    journal = report["observationJournal"]
    require(type(journal.get("recordsAttempted")) is int and journal["recordsAttempted"] > 0
            and type(journal.get("recordsConfirmed")) is int
            and journal["recordsConfirmed"] == journal["recordsAttempted"],
            "Studio68 independent reader journal is incomplete")
    cases = report.get("cases")
    require(type(cases) is list and len(cases) == 68 and len({x.get("case") for x in cases}) == 68,
            "Studio68 independent store roster changed")
    ids = []
    for case in cases:
        require(case.get("captureStatus") == "VERIFIED" and case.get("errors") == []
                and case.get("readback", {}).get("status") == "VERIFIED"
                and case["readback"].get("errors") == [], "Studio68 closed store/readback failed")
        observed = case["readback"]
        close = observed.get("close", {})
        require(observed.get("transportAttempted") is True and observed.get("transportExecuted") is True
                and type(observed.get("processId")) is int and observed["processId"] > 0
                and type(observed.get("rpcCount")) is int and observed["rpcCount"] >= 5
                and observed.get("primaryFailure") is None
                and observed.get("projectEqual") is True and observed.get("journalEqual") is True
                and observed.get("receipt", {}).get("status") == "WRITTEN"
                and close.get("clean") is True and close.get("errors") == [] and close.get("taints") == []
                and type(close.get("returnCode")) is int and close["returnCode"] == 0
                and close.get("processId") == observed["processId"]
                and close.get("rpcCount") == observed["rpcCount"], "Studio68 original readback transport is incomplete")
        item = case.get("playwrightIdentity", {})
        require(item.get("projectId") == "chromium", "Studio68 readback project changed")
        ids.append(item.get("playwrightId"))
    require(len(set(ids)) == 68 and set(ids) == set(expected_ids), "Studio68 readback IDs are not the executed cohort")
    return {"status": "PASS_REAL_SERVICE_AND_CLOSED_STORE_READBACK", "cases": 68,
            "product_source": STUDIO68_SOURCE, "human_acceptance": "NOT_GRANTED",
            "native_rendering": "NOT_RUN", "escaped_descendants": "NOT_OBSERVED"}


def studio68_readback_journal(raw, report):
    require(type(raw) is bytes and raw.endswith(b"\n"), "Studio68 readback journal is truncated")
    rows = [json.loads(line) for line in raw.splitlines()]
    require(len(rows) == 206 == report["observationJournal"]["recordsConfirmed"]
            and all(type(row) is dict and row.get("schema") == "motionwright.readback-observation/1"
                    and type(row.get("sequence")) is int and row["sequence"] == index
                    for index, row in enumerate(rows, 1)), "Studio68 readback journal sequence/record count")
    require(rows[0].get("kind") == "run_before_transport" and rows[0].get("case") is None
            and rows[-1].get("kind") == "run_before_receipt" and rows[-1].get("case") is None
            and rows[-1].get("observation", {}).get("cases") == report["cases"],
            "Studio68 readback journal boundaries differ")
    for index, case in enumerate(report["cases"]):
        group = rows[1 + index * 3:4 + index * 3]
        require([row.get("kind") for row in group] == ["launch_intent", "transport_observed", "case_closed"]
                and all(row.get("case") == case["case"] for row in group),
                "Studio68 readback journal case order changed")
        observed = dict(group[-1].get("observation", {}))
        current = dict(case["readback"])
        observed_receipt = dict(observed.pop("receipt", {}))
        current_receipt = dict(current.pop("receipt", {}))
        require(observed_receipt.pop("status", None) == "PENDING"
                and current_receipt.pop("status", None) == "WRITTEN"
                and observed_receipt == current_receipt and observed == current,
                "Studio68 case terminal differs from its original journal")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "records": len(rows)}

def studio68_tools(product, environment, output, clock, commands, *, phase=None):
    phase = clock.phase(STUDIO68_PREPARATION_SECONDS) if phase is None else phase
    tools, identities = {}, {}
    for name in ("node", "npm", "rustup", "cc", "ar", "git"):
        found = shutil.which(name, path=environment.get("PATH"))
        require(found, "Studio68 tool unavailable: " + name)
        path = Path(found).resolve(strict=True)
        require(path.is_absolute(), "Studio68 tool must be absolute")
        tools[name] = str(path)
        identities[name] = {"path": str(path), **studio68_original(path, 256 * 1024 * 1024)[1]}
    tools["python"] = str(Path(sys.executable).resolve(strict=True))
    identities["python"] = {"path": tools["python"], **studio68_original(Path(tools["python"]), 256 * 1024 * 1024)[1]}
    def run(argv, label):
        return studio68_command(argv, product, environment, output, label, 30, phase, commands)[1]["stdout"].decode("utf-8", "strict").strip()
    node_version = run([tools["node"], "--version"], "node-version")
    require(re.fullmatch(r"v22\.[0-9]+\.[0-9]+", node_version), "Studio68 requires observed Node22")
    run([tools["node"], tools["npm"], "--version"], "npm-version")
    run([tools["python"], "-I", "-B", "-X", "utf8", "--version"], "python-version")
    run([tools["rustup"], "--version"], "rustup-version")
    run([tools["cc"], "--version"], "cc-version")
    run([tools["ar"], "--version"], "ar-version")
    run([tools["git"], "--version"], "git-version")
    rust_tools = ("cargo", "rustc", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver")
    for name in rust_tools:
        raw = run([tools["rustup"], "which", "--toolchain", "1.98.1", name], "which-" + name)
        require("\n" not in raw and Path(raw).is_absolute(), "Studio68 ambiguous Rust tool")
        path = Path(raw).resolve(strict=True)
        tools[name] = str(path)
        identities[name] = {"path": str(path), **studio68_original(path, 256 * 1024 * 1024)[1]}
    require(len({str(Path(tools[k]).parent) for k in rust_tools}) == 1,
            "Studio68 mixed Rust tool roots")
    rustc = run([tools["rustc"], "-Vv"], "rustc-version")
    require("release: 1.98.1" in rustc.splitlines() and "host: x86_64-unknown-linux-gnu" in rustc.splitlines(),
            "Studio68 compiler release/host mismatch")
    for name in ("cargo", "rustdoc", "cargo-fmt", "rustfmt", "cargo-clippy", "clippy-driver"):
        require(run([tools[name], "--version"], name + "-version"), "Studio68 unobserved tool version")
    return tools, identities


def studio68_verify_tools(identities):
    for name, row in identities.items():
        require(studio68_original(Path(row["path"]), 256 * 1024 * 1024)[1] ==
                {k: row[k] for k in ("bytes", "sha256")}, "Studio68 tool changed: " + name)


def studio68_product_g0(product, snapshot, context, tools, environment, output, clock, commands, git):
    import ast
    phase = clock.phase(STUDIO68_PHASE_SECONDS["product_g0"])
    python = tools["python"]
    bootstrap = ("import runpy,sys; from pathlib import Path; p=Path(sys.argv[1]); "
                 "sys.path.insert(0,str(p.parent)); sys.argv=sys.argv[1:]; runpy.run_path(str(p),run_name='__main__')")
    for name in STUDIO68_POLICIES:
        studio68_command([python, "-I", "-B", "-X", "utf8", "-c", bootstrap, str(product / "tooling" / name)],
                         product, environment, output, "product-" + name[:-3], 600, phase, commands)
    runner_path = product / "tooling/ci_test_runner.py"
    runner_raw, runner_identity = studio68_original(runner_path, 1024 * 1024)
    require(runner_identity["sha256"] == STUDIO68_RUNNER_SHA256, "Studio68 product G0 runner source changed")
    # Execute the verified parser bytes, never an unrelated cached .pyc.
    parser_node = next(n for n in ast.parse(runner_raw).body
                       if isinstance(n, ast.FunctionDef) and n.name == "parse_unittest_summary")
    namespace = {"re": re}
    exec(compile(ast.Module(body=[parser_node], type_ignores=[]), str(runner_path), "exec"), namespace)
    report = output / "product-g0" / "receipt.json"
    python_identity = studio68_original(Path(python), 256 * 1024 * 1024)[1]
    command, _ = studio68_command([python, "-I", "-B", "-X", "utf8", str(runner_path), "--repo", str(product),
        "--mode", "both", "--pattern", "test_ci_canvas_service.py", "--expected-source", STUDIO68_SOURCE,
        "--report", str(report), "--require-complete", "--timeout", "300"],
        product, environment, output, "product-contracts", 660, phase, commands, defer_exit_status=True)
    raw, report_identity = studio68_original(report, 4 * 1024 * 1024)
    inner = json.loads(raw)
    studio68_inner_terminal(command, inner, report_identity, "product-g0")
    test_raw, test_identity = studio68_original(product / "tooling/tests/test_ci_canvas_service.py", 2 * 1024 * 1024)
    expected = {"definition": test_identity["sha256"],
                "ids": studio68_test_roster(test_raw, "tooling.tests.test_ci_canvas_service"),
                "python": python, "python_sha256": python_identity["sha256"],
                "locks": {p: snapshot["files"][p]["sha256"] for p in
                          ("Cargo.lock", "SOURCE_LOCK.json", "apps/studio/package-lock.json")}}
    contracts = studio68_g0_receipt(inner, expected, context,
        lambda name: studio68_original(report.parent / name, 32 * 1024 * 1024)[0], namespace["parse_unittest_summary"])
    studio68_command([tools["cargo-fmt"], "fmt", "--all", "--", "--check"], product, environment,
                     output, "product-format", 120, phase, commands)
    require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE) == snapshot,
            "Studio68 product source changed during its own G0")
    return {"status": "PASS_PRODUCT_G0_FOCAL", "product": STUDIO68_SOURCE,
            "policies": list(STUDIO68_POLICIES), "contracts": contracts, "receipt": report_identity,
            "format": "OBSERVED_CARGO_FMT_WITH_OBSERVED_RUSTFMT", "gate_credit_transferred": False}


def studio68_dependency_closure(metadata, product, root, sdk, lock_raw):
    import tomllib
    require(type(metadata) is dict and metadata.get("version") == 1
            and Path(metadata.get("workspace_root", "")).resolve(strict=True) == product,
            "Studio68 Cargo metadata belongs to another checkout")
    packages = metadata.get("packages")
    require(type(packages) is list and len(packages) <= 2000, "Studio68 package roster bound")
    indexed = {p["id"]: p for p in packages}
    require(len(indexed) == len(packages), "Studio68 duplicate Cargo package")
    roots = [p["id"] for p in packages if p.get("name") == "motionwright-service"
             and Path(p.get("manifest_path", "")).absolute() == product / "crates/service/Cargo.toml"]
    require(len(roots) == 1, "Studio68 selected service package missing or ambiguous")
    nodes = {p["id"]: p for p in metadata.get("resolve", {}).get("nodes", [])}
    require(set(nodes) <= set(indexed), "Studio68 resolved package is unbound")
    selected, pending = set(), list(roots)
    while pending:
        package = pending.pop()
        if package in selected:
            continue
        require(package in nodes and len(selected) < 2000, "Studio68 unresolved dependency")
        selected.add(package)
        pending.extend(nodes[package].get("dependencies", []))
    lock = tomllib.loads(lock_raw.decode("utf-8"))
    locked = {(p["name"], p["version"], p.get("source")): p for p in lock["package"]}
    sources, sdk_snapshot = {}, None
    for package_id in sorted(selected):
        item = indexed[package_id]
        folder = Path(item["manifest_path"]).resolve(strict=True).parent
        source = item.get("source")
        key = (item["name"], item["version"], source)
        require(key in locked, "Studio68 dependency differs from Cargo.lock")
        if source is None:
            require(folder.is_relative_to(product), "Studio68 unbound local path dependency")
            continue
        require(folder.is_relative_to(root / "cargo"), "Studio68 dependency escaped the owned Cargo home")
        if source.startswith("git+"):
            require(source.startswith("git+https://github.com/seradotcom/semwright?")
                    and source.endswith("#" + STUDIO68_SDK)
                    and "rev=" + STUDIO68_SDK in source, "Studio68 unexpected Git dependency")
            sdk_root = Path(sdk.custody.git(folder, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
            if sdk_snapshot is None:
                sdk_snapshot = studio68_snapshot(sdk_root, STUDIO68_SDK, sdk.custody.git, cargo_marker=True)
                sources["sdk"] = {"root": str(sdk_root), "snapshot": sdk_snapshot}
            require(folder.is_relative_to(Path(sources["sdk"]["root"])), "Studio68 split SDK checkout")
        else:
            require(source == "registry+https://github.com/rust-lang/crates.io-index",
                    "Studio68 noncanonical registry dependency")
            raw, checksum_identity = studio68_original(folder / ".cargo-checksum.json", 4 * 1024 * 1024)
            checksum = json.loads(raw)
            require(checksum.get("package") == locked[key].get("checksum")
                    and re.fullmatch(r"[0-9a-f]{64}", checksum.get("package", "")),
                    "Studio68 registry archive checksum differs from lock")
            inventory = sdk.dependency_inventory(folder)
            require(type(checksum.get("files")) is dict and checksum["files"], "Studio68 registry file roster missing")
            for name, expected in checksum["files"].items():
                require(name in inventory and inventory[name].get("sha256") == expected,
                        "Studio68 registry source checksum differs: " + name)
            require(set(inventory) <= set(checksum["files"]) | {".cargo-checksum.json", ".cargo-ok"},
                    "Studio68 unbound registry source input")
            sources[package_id] = {"root": str(folder), "inventory": inventory,
                                   "archive_checksum": checksum["package"], "checksum_original": checksum_identity}
    require(sdk_snapshot is not None, "Studio68 actual resolved SDK checkout was not observed")
    return {"root_package": roots[0], "resolved_package_ids": sorted(selected), "sources": sources,
            "scope": "Resolved source closure; not a claim that every package target executed"}


def studio68_verify_dependencies(closure, sdk):
    for key, value in closure["sources"].items():
        folder = Path(value["root"])
        actual = (studio68_snapshot(folder, STUDIO68_SDK, sdk.custody.git, cargo_marker=True)
                  if key == "sdk" else sdk.dependency_inventory(folder))
        require(actual == value["snapshot" if key == "sdk" else "inventory"],
                "Studio68 dependency changed after compilation: " + key)


def studio68_npm_inventory(root, sdk):
    inventory = sdk.dependency_inventory(root)
    # The pinned Vite configuration uses its documented node_modules caches.
    # Cache output is retained separately; it never becomes immutable package input.
    generated = (".vite/", ".vite-temp/")
    return {"files": {k: v for k, v in inventory.items() if not k.startswith(generated)},
            "generated_cache_roots": [".vite", ".vite-temp"]}


def studio68_seal_binary(raw, code, product, root, sdk):
    binaries = sdk.select_binaries(raw, code, product, root / "target", studio68_binary_spec(), testing=False)
    observed = binaries["studio_review_session"]
    original = Path(observed["absolute_path"])
    retained = root / "evidence/tools/studio_review_session"
    retained.parent.mkdir(parents=True, exist_ok=False)
    data, identity = studio68_original(original, 256 * 1024 * 1024)
    require(identity == {k: observed[k] for k in ("bytes", "sha256")}, "Studio68 compiled binary changed before retention")
    with retained.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    retained.chmod(0o500)
    require(studio68_original(retained, 256 * 1024 * 1024)[1] == identity,
            "Studio68 retained executable readback differs")
    sdk.verify_binaries(binaries, root / "target")
    return {"producer_original": observed, "retained": {"path": str(retained), **identity},
            "executed_product_source_sha": STUDIO68_SOURCE, "executable_target": studio68_binary_spec()[0]}


def studio68_reader_argv(python, reader, root, original, binary):
    return [python, "-I", "-B", "-X", "utf8", str(reader),
            "--results-root", str(root / "results"), "--output", str(root / "readback"),
            "--expected-cases", "68", "--playwright-exit-code", "0",
            "--playwright-json", str(root / "playwright-execution.json"),
            "--expected-playwright-sha256", original["execution"]["sha256"],
            "--expected-roster-json", str(root / "playwright-list.json"),
            "--expected-roster-sha256", original["list"]["sha256"],
            "--provenance-receipt", str(root / "source-build-binary.json"),
            "--expected-provenance-sha256", original["provenance"]["sha256"],
            "--provenance-reviewed", "--execute-readback", "--expected-binary-sha256", binary["sha256"],
            "--backend-source-sha", STUDIO68_SOURCE, "--rpc-timeout-seconds", "10"]


def studio68_record_execution_attempt(receipt, output, argv, cwd):
    """Persist selection intent before dispatch; it is not evidence of a spawned process."""
    state = receipt["service_case_observation"]
    require(state["launch_attempted"] is False and receipt["service_cases"] == "NOT_RUN",
            "Studio68 execution was already attempted")
    intent = {"schema": "motionwright.studio68-playwright-attempt/1", "context": receipt["context"],
              "argv": list(argv), "cwd": str(cwd), "selected_cases": len(STUDIO68_CASES),
              "selected_ids": [row[4] for row in STUDIO68_CASES],
              "meaning": "INTENT_BEFORE_CHILD; PROCESS_AND_CASE_START_NOT_YET_OBSERVED",
              "retry_authorized": False}
    path = output / "playwright-execution-attempt.json"
    write_new(path, intent)
    raw, identity = studio68_original(path, 2 * 1024 * 1024)
    require(json.loads(raw) == intent, "Studio68 pre-dispatch selection intent changed")
    state.update(launch_attempted=True, attempt_original={"path": path.name, **identity})
    receipt["service_cases"] = "ATTEMPTED_NOT_VALIDATED"


def studio68_record_execution_process(receipt, commands):
    """Preserve an observed command/PID without converting a selected roster to started cases."""
    state = receipt["service_case_observation"]
    rows = [row for row in commands if isinstance(row, dict) and row.get("label") == "playwright-execution"]
    if not rows:
        return
    row = rows[-1]
    original = row.get("retained_command") if isinstance(row.get("retained_command"), dict) else row
    state["command_observation"] = {
        "record_status": row.get("status"), "observation": original.get("observation"),
        "exit_code": original.get("exit_code"), "diagnostic_custody": original.get("diagnostic_custody"),
        "owned_group_after_wait": original.get("owned_group_after_wait"),
        "escaped_descendants": original.get("escaped_descendants"),
    }
    pid = original.get("pid")
    if type(pid) is int and pid > 0:
        state["process_start_observed"] = True
        state["process_identity"] = {"pid": pid, "started_utc": original.get("started_utc"),
                                     "process_boundary": original.get("process_boundary")}



def studio68_main(arguments):
    import time
    started_ns = time.monotonic_ns()  # One origin before any controlled IO.
    parser = argparse.ArgumentParser(description="One exact 68-case Studio service cohort with independent closed-store readback")
    parser.add_argument("--gate-repo", required=True, type=Path)
    parser.add_argument("--product-repo", required=True, type=Path)
    parser.add_argument("--product-source", required=True)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(arguments)
    context = studio68_context(os.environ, args.product_source)
    clock = Studio68Clock(started_ns)
    gate, product = args.gate_repo.resolve(strict=True), args.product_repo.resolve(strict=True)
    root = args.root.absolute()
    require(root.is_absolute() and not root.exists() and root.parent.resolve(strict=True) == root.parent
            and root.parent == Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
            and gate != product and not gate.is_relative_to(product) and not product.is_relative_to(gate)
            and not root.is_relative_to(gate) and not root.is_relative_to(product), "Studio68 isolated owned namespaces")
    environment = studio68_environment(os.environ, root)
    root.mkdir()
    output = root / "evidence"
    output.mkdir()
    for name in ("cargo", "target", "npm", "browsers", "tmp", "xdg"):
        (root / name).mkdir()
    commands = []
    receipt = {"schema": "motionwright.studio68-execution/1", "context": context,
               "started_mono_ns": started_ns, "deadline_mono_ns": clock.deadline_ns,
               "commands": commands, "status": "PREPARING", "service_cases": "NOT_RUN",
               "service_case_observation": {"selected_cases": 68, "launch_attempted": False,
                   "process_start_observed": False, "process_identity": None,
                   "individual_case_start": "NOT_OBSERVED", "playwright_validated_cases": 0,
                   "closed_readback_validated_cases": 0},
               "retry_authorized": False, "human_acceptance": "NOT_GRANTED",
               "process_boundary": "OWNED_POSIX_SESSION_GROUP", "escaped_descendants": "NOT_OBSERVED"}
    exit_code = 1
    try:
        preparation = clock.phase(STUDIO68_PREPARATION_SECONDS)
        sdk = studio68_load_helpers(gate)
        git = sdk.custody.git
        gate_before = studio68_snapshot(gate, context["gate_source_sha"], git)
        product_before = studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE)
        studio68_product_identity(product_before)
        require(Path(__file__).resolve(strict=True) == gate / "tooling/ci_canvas_service.py", "Studio68 helper came from another checkout")
        for path, expected in STUDIO68_READER_PINS.items():
            require(gate_before["files"].get(path, {}).get("sha256") == expected, "Studio68 independent reader pin differs")
        write_new(output / "gate-source-before.json", gate_before)
        write_new(output / "product-source-before.json", product_before)
        preparation.remaining(1)
        receipt["gate_tree_sha"], receipt["executed_product_tree_sha"] = gate_before["tree"], product_before["tree"]
        tools, tool_identities = studio68_tools(product, environment, output, clock, commands, phase=preparation)
        environment.update(CARGO=tools["cargo"], RUSTC=tools["rustc"], RUSTDOC=tools["rustdoc"], RUSTFMT=tools["rustfmt"],
                           CC=tools["cc"], AR=tools["ar"], CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=tools["cc"],
                           RUSTUP_TOOLCHAIN="1.98.1", PATH=os.pathsep.join(
                               (str(Path(tools["node"]).parent), str(Path(tools["cargo"]).parent), environment.get("PATH", ""))))
        receipt["tools"] = tool_identities
        receipt["product_g0"] = studio68_product_g0(product, product_before, context, tools, environment, output, clock, commands, git)
        require(shutil.disk_usage(root).free >= 18 * 1024**3, "Studio68 compiler disk reserve unavailable")
        memory = Path("/proc/meminfo").read_text(encoding="ascii")
        available = re.search(r"(?m)^MemAvailable:\s+([0-9]+) kB$", memory)
        require(available and int(available.group(1)) * 1024 >= 4 * 1024**3, "Studio68 compiler memory reserve unavailable")
        phase = clock.phase(STUDIO68_PHASE_SECONDS["dependencies"])
        def run(argv, label, cap, selected_phase, *, cwd=product, env=environment, defer_exit_status=False):
            return studio68_command(argv, cwd, env, output, label, cap, selected_phase, commands,
                                    defer_exit_status=defer_exit_status)
        barrier = sdk.ProducerBarrier(lambda argv, label, cap, selected_phase, cwd=product, env=environment:
                                      run(argv, label, cap, selected_phase, cwd=cwd, env=env), tools["cargo"])
        barrier([tools["cargo"], "fetch", "--locked"], "locked-fetch", 600, phase)
        _, metadata = barrier([tools["cargo"], "metadata", "--locked", "--offline", "--format-version", "1"],
                              "resolved-dependencies", 120, phase)
        closure = studio68_dependency_closure(json.loads(metadata["stdout"]), product, root, sdk,
                                              studio68_original(product / "Cargo.lock", 16 * 1024 * 1024)[0])
        write_new(output / "compiled-source-dependencies.json", closure)
        studio = product / "apps/studio"
        run([tools["node"], tools["npm"], "ci", "--no-audit", "--no-fund"], "npm-ci", 600, phase, cwd=studio)
        cli = studio / "node_modules/@playwright/test/cli.js"
        require(cli.resolve(strict=True).is_relative_to(studio / "node_modules"), "Studio68 Playwright CLI escaped dependency tree")
        run([tools["node"], str(cli), "install", "--with-deps", "chromium"], "chromium-install", 600, phase, cwd=studio)
        npm = studio68_npm_inventory(studio / "node_modules", sdk)
        write_new(output / "npm-source-before.json", npm)
        barrier([tools["cargo"], "clippy", "--locked", "--offline", "--jobs", "1", "-p", "motionwright-service",
                 "--example", "studio_review_session", "--", "-D", "warnings"], "selected-clippy", 900,
                clock.phase(STUDIO68_PHASE_SECONDS["clippy"]))
        build_phase = clock.phase(STUDIO68_PHASE_SECONDS["build"])
        built, streams = barrier(studio68_build_argv(tools["cargo"]), "service-example-build", 1200, build_phase)
        barrier.begin_sealing()
        binary = studio68_seal_binary(streams["stdout"], built["exit_code"], product, root, sdk)
        studio68_verify_dependencies(closure, sdk)
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before,
                "Studio68 product source changed before observation")
        studio68_verify_tools(tool_identities)
        provenance = {"schema": "motionwright.studio68-source-build-binary/1", "context": context,
                      "product_g0": receipt["product_g0"], "product_source": STUDIO68_SOURCE, "product_tree": STUDIO68_TREE,
                      "gate_source": context["gate_source_sha"], "gate_tree": gate_before["tree"],
                      "binary": binary, "build_command": built, "tools": tool_identities,
                      "sdk_source": STUDIO68_SDK, "sdk_tree": closure["sources"]["sdk"]["snapshot"]["tree"],
                      "retry_authorized": False, "formal_acceptance": "NOT_GRANTED"}
        write_new(output / "source-build-binary.json", provenance)
        receipt["binary"] = binary
        barrier.begin_observation()
        pw_env = dict(environment, MOTIONWRIGHT_SERVICE_TEST_BIN=binary["retained"]["path"],
                      MOTIONWRIGHT_SERVICE_TEST_TMPDIR=str(root / "tmp"), MOTIONWRIGHT_PLAYWRIGHT_PORT="14361")
        originals = {"provenance": studio68_original(output / "source-build-binary.json", 8 * 1024 * 1024)[1]}
        build_phase.remaining(1)
        for listing, label in ((True, "list"), (False, "execution")):
            cap = STUDIO68_PHASE_SECONDS[label]
            reporter = output / ("playwright-" + label + ".json")
            current_env = dict(pw_env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(reporter))
            args_pw = studio68_playwright_argv(tools["node"], cli, output / "results", listing=listing)
            # No Cargo producer remains reachable through this barrier phase.
            selected_phase = clock.phase(cap)
            if not listing:
                studio68_record_execution_attempt(receipt, output, args_pw, studio)
            try:
                result, _ = barrier(args_pw, "playwright-" + label, cap, selected_phase, studio, current_env)
            finally:
                if not listing:
                    studio68_record_execution_process(receipt, commands)
            raw, originals[label] = studio68_original(reporter, 64 * 1024 * 1024)
            document = json.loads(raw)
            require(document.get("config", {}).get("argv") == args_pw, "Studio68 reporter argv differs from observed launch")
            require(Path(document["config"].get("rootDir", "")).resolve(strict=True) == studio / "tests"
                    and Path(document["config"].get("configFile", "")).resolve(strict=True) == studio / "playwright.config.ts",
                    "Studio68 reporter source roots changed")
            receipt[label] = studio68_roster(document, executed=not listing)
            if not listing:
                receipt["service_case_observation"]["playwright_validated_cases"] = 68
                receipt["service_case_observation"]["individual_case_start"] = "OBSERVED_IN_ORIGINAL_EXECUTION_REPORT"
                receipt["service_cases"] = "PLAYWRIGHT_VERIFIED_READBACK_PENDING"
            require(studio68_original(Path(binary["retained"]["path"]), 256 * 1024 * 1024)[1] ==
                    {k: binary["retained"][k] for k in ("bytes", "sha256")}, "Studio68 executed service binary changed")
        require(receipt["list"]["ids"] == receipt["execution"]["ids"], "Studio68 list/execution IDs differ")
        require(studio68_npm_inventory(studio / "node_modules", sdk) == npm, "Studio68 installed package sources changed")
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before,
                "Studio68 source changed before independent readback")
        require(json.loads(studio68_original(output / "source-build-binary.json", 8 * 1024 * 1024)[0]) == provenance,
                "Studio68 independent source receipt changed")
        reader = gate / "tooling/service_readback/service_archive_readback_r3.py"
        require(studio68_original(reader, 2 * 1024 * 1024)[1]["sha256"] == STUDIO68_READER_PINS[reader.relative_to(gate).as_posix()],
                "Studio68 independent reader changed before execution")
        read_command, _ = run(studio68_reader_argv(tools["python"], reader, output, originals, binary["retained"]),
                             "independent-readback", 5400, clock.phase(STUDIO68_PHASE_SECONDS["readback"]),
                             env=pw_env, defer_exit_status=True)
        retention_phase = clock.phase(STUDIO68_PHASE_SECONDS["retention"], retention=True)
        read_raw, read_identity = studio68_original(output / "readback/readback-report.json", 32 * 1024 * 1024)
        read_report = json.loads(read_raw)
        studio68_inner_terminal(read_command, read_report, read_identity, "readback")
        receipt["readback"] = studio68_readback_admission(read_report, read_command,
            {k: binary["retained"][k] for k in ("path", "sha256")}, receipt["execution"]["ids"])
        receipt["readback_original"] = read_identity
        journal_raw, _ = studio68_original(output / "readback/execution-observations.jsonl", 128 * 1024 * 1024)
        receipt["readback_journal_original"] = studio68_readback_journal(journal_raw, read_report)
        sealed = output / "readback/sealed-playwright-results.zip"
        require(read_report.get("archive", {}).get("archive") == str(sealed), "Studio68 readback archive locator changed")
        sealed_identity = archive_file_digest(sealed, ARCHIVE_LIMITS["total_bytes"])
        require(sealed_identity["sha256"] == read_report["archive"].get("archiveSha256"), "Studio68 sealed ZIP changed")
        seal_raw, seal_identity = studio68_original(output / "readback/seal-manifest.json", 32 * 1024 * 1024)
        seal = json.loads(seal_raw)
        require(seal.get("schema") == "motionwright.playwright-results-seal/1"
                and {k: seal.get(k) for k in ("archive", "archiveSha256", "treeSha256")} == read_report["archive"]
                and seal.get("sourceDirectory") == str(output / "results"), "Studio68 sealed original manifest differs")
        receipt["sealed_originals"] = {"archive": {"path": str(sealed), **sealed_identity}, "manifest": seal_identity}
        require(studio68_snapshot(product, STUDIO68_SOURCE, git, expected_tree=STUDIO68_TREE, allow_node_modules=True) == product_before
                and studio68_snapshot(gate, context["gate_source_sha"], git) == gate_before,
                "Studio68 final source readback differs")
        studio68_verify_dependencies(closure, sdk)
        studio68_verify_tools(tool_identities)
        sdk.verify_binaries({"studio_review_session": binary["producer_original"]}, root / "target")
        require(studio68_original(Path(binary["retained"]["path"]), 256 * 1024 * 1024)[1] ==
                {k: binary["retained"][k] for k in ("bytes", "sha256")}, "Studio68 retained executable changed after readback")
        require(studio68_npm_inventory(studio / "node_modules", sdk) == npm, "Studio68 package sources changed after readback")
        for row in commands:
            require(row.get("status") == "PASS_OBSERVED_COMMAND_ONLY", "Studio68 a command is not admitted")
            for stream in ("stdout", "stderr"):
                require(studio68_original(output / (row["label"] + "." + stream + ".log"), ARCHIVE_LIMITS["file_bytes"])[1] == row[stream],
                        "Studio68 final original stream readback differs")
        retention_phase.remaining(1)
        clock.remaining(1, reserve=0)
        receipt["service_case_observation"]["closed_readback_validated_cases"] = 68
        receipt.update(status="PASS_STUDIO68_REAL_SERVICE_CLOSED_READBACK", service_cases=68,
                       scope="Five exact F278 service specs and closed Project/journal readback; no installed/native/human acceptance")
        exit_code = 0
    except UnknownObservation as error:
        receipt.update(status="UNKNOWN_NO_RETRY", error=str(error), uncertain_command=error.command)
        exit_code = 124
    except BaseException as error:
        receipt.update(status="FAIL_OR_PREPARATION_REJECTED", error=type(error).__name__ + ": " + str(error))
    receipt["finished_mono_ns"] = time.monotonic_ns()
    receipt["exit_code"] = exit_code
    write_new(output / "STUDIO68_RESULT.json", receipt)
    print(json.dumps({"status": receipt["status"], "gate": context["gate_source_sha"], "product": STUDIO68_SOURCE,
                      "receipt": str(output / "STUDIO68_RESULT.json"), "service_cases": receipt["service_cases"]}), flush=True)
    return exit_code

import time

# Existing Graph J1/R74 selection. All outputs below remain private evidence;
# source intake, external owner admission and closed custody are not G2/A5 PASS.
GRAPH_SCOPE = "phase1-native-graph"
GRAPH_REPOSITORY = "seradotcom/motionwright"
GRAPH_WORKFLOW = ".github/workflows/studio-service-verification.yml"
GRAPH_DEADLINE_ENV = "MOTIONWRIGHT_GRAPH_DEADLINE_SHA256"
GRAPH_TOKEN_ENV = "MOTIONWRIGHT_GRAPH_METADATA_TOKEN"
GRAPH_INDEX_SCHEMA = "motionwright.private.graph-producer-reference-index/0"
GRAPH_BYTES = 1024 * 1024
GRAPH_BUDGET = {
    "producer_bytes": 384 * GRAPH_BYTES, "producer_members": 768,
    "case_bytes": 260 * GRAPH_BYTES, "case_members": 3000,
    "shared_bytes": 104 * GRAPH_BYTES, "shared_members": 1232,
    "owner_bytes": 48 * GRAPH_BYTES, "owner_files": 768,
    "journal_bytes": 32 * GRAPH_BYTES, "journal_files": 384,
    "store_file_bytes": 4 * GRAPH_BYTES, "store_files": 4,
}
GRAPH_PHASES = {
    "prepare": 1200, "runtime": 1200, "sdk-build": 1800, "product-build": 1800,
    "producer-index": 600, "review-upload": 300, "approval-wait": 1200,
    "discovery": 60, "cases": 780, "closure": 300, "archive": 600, "final-upload": 300,
}
GRAPH_CASES = (
    ("graph-source-review", "Graph creates, edits, reopens SQLite, persists an actual review and rechecks it after editing", (5, 0)),
    ("graph-flow", "Synthetic flow stores exact event times and rejects a real stale CAS without a new event", (2, 0)),
    ("graph-lost-reply", "A lost Graph reply cannot replay a committed SQLite mutation after reload", (5, 0)),
    ("graph-removal", "Graph removal rejects a prior-session grant and stale base, then removes once through the real UI and survives SQLite restart", (2, 2, 0)),
    ("graph-label-causal-diagram", "Graph causal_diagram follows the edge midpoint through real saves and restores manual placement after SQLite restart", (5, 2, 0)),
    ("graph-label-system-flow", "Graph system_flow follows the edge midpoint through real saves and restores manual placement after SQLite restart", (5, 2, 0)),
)
GRAPH_ROLES = ("semwright", "semwrightd", "native_provider", "shared_state_actor", "manim_runner",
               "python3", "ffmpeg", "ordinary_service", "native_graph_session", "semwright_sandbox")
GRAPH_SDK_ROLES = ("semwright", "semwrightd", "semwright_sandbox")
GRAPH_RUST_ROLES = tuple(role for role in GRAPH_ROLES if role not in ("python3", "ffmpeg"))
GRAPH_TARGETS = {
    "semwright": ("semwright-cli", "semwright", "bin"),
    "semwrightd": ("semwright-daemon", "semwrightd", "bin"),
    "semwright_sandbox": ("semwright-plugin-host", "semwright-sandbox", "bin"),
    "native_provider": ("motionwright-native", "motionwright-native-provider", "bin"),
    "shared_state_actor": ("motionwright-native", "shared-state-actor", "example"),
    "manim_runner": ("motionwright-manim-community-driver", "motionwright-manim-runner", "bin"),
    "ordinary_service": ("motionwright-service", "studio_review_session", "example"),
    "native_graph_session": ("motionwright-native", "native-graph-review-session", "example"),
}
GRAPH_IMPORTS = tuple("tooling/" + name + ".py" for name in (
    "native_graph_browser_owner", "native_host_shared_state", "native_host_installed_shared_state",
    "native_manim_graph_runtime_intake", "native_av_failure_guard", "candidate_smoke",
    "linux_first_use_custody", "linux_cold_start", "linux_first_use_accessible",
    "source_checkout_observation", "storage_schema_contract", "linux_gui_probe",
    "native_provider_lifecycle", "runtime_first_use_audit", "manim_bundled_font_intake", "mlt_official_runtime_intake"))
GRAPH_CALLGRAPH = (
    "apps/studio/tests/semantic-graph-service.spec.ts", "apps/studio/tests/support/scopedChangeService.ts",
    "apps/studio/src/SemanticGraphWorkbench.tsx", "apps/studio/src/semanticGraphService.ts",
    "apps/studio/src/App.tsx", "apps/studio/src/api.ts", "apps/studio/src/projectMutation.ts",
    "apps/studio/src/semanticGraphDraft.ts", "apps/studio/src/ProductionDesignWorkspace.tsx",
    "crates/service/examples/studio_review_session.rs", "crates/native/examples/native-graph-review-session.rs",
    "crates/native/src/production/transport_custody.rs", "crates/native/src/production/graph_authoring.rs",
    "crates/native/src/production.rs")
GRAPH_AUTHORITY = (*GRAPH_IMPORTS, *GRAPH_CALLGRAPH,
    "tooling/ci_canvas_service.py", "tooling/ci_private_evidence.py", "tooling/requirements/mlt-g2-custody.txt", GRAPH_WORKFLOW,
    "apps/studio/tests/support/nativeGraphCase.ts", "apps/studio/playwright.config.ts",
    *("apps/studio/tests/support/graph-owner/" + name for name in (
        "producer_guard.mjs", "factory.mjs", "sdk_ticket_prewrite.mjs", "custody.mjs", "contracts.mjs",
        "same_store_reopen.mjs", "opened_store_descriptor.mjs", "opened_store_identity_join.mjs",
        "schemas/spec.r2.json", "schemas/final.r2.json")))
GRAPH_CONTRACT_FIELDS = {"source_files", "sdk_tree", "sdk_files", "builds", "ordinary_preflight",
    "runtime", "callback_roster", "native_enforcement", "native_enforcing_source", "cases", "writer_sources", "execution"}
GRAPH_API_BYTES, GRAPH_API_PAGES = 256 * 1024, 4


def graph_case_layout(cases_root, case_id):
    """Pure path calculation; only the original index and R74 admit a case."""
    raw = os.fspath(cases_root)
    require(isinstance(raw, str) and "\0" not in raw and raw == os.path.normpath(raw)
            and Path(raw).is_absolute() and not raw.startswith("//"), "Graph canonical cases root")
    cases = sorted(row[0] for row in GRAPH_CASES)
    require(case_id in cases, "Graph undeclared case layout")
    private_root = Path(raw) / ("g" + str(cases.index(case_id) + 1))
    owner_socket = private_root / "owner/runtime/broker.sock"
    require(len(str(owner_socket).encode("utf-8")) <= 100, "Graph owner socket path exceeds 100 bytes")
    return {"private_root": private_root, "database_path": private_root / "store/motionwright.sqlite3",
            "owner_socket": owner_socket}


def graph_collected_case_roots(cases_root):
    """Require the same six exclusive roots, never select a historical alias."""
    cases_root = Path(cases_root)
    require(cases_root.is_dir() and cases_root.resolve(strict=True) == cases_root, "Graph canonical cases root absent")
    rows = [(case, graph_case_layout(cases_root, case)["private_root"]) for case in sorted(row[0] for row in GRAPH_CASES)]
    require(all(root.is_dir() and not root.is_symlink() and root.resolve(strict=True) == root for _, root in rows),
            "Graph final canonical case root absent or aliased")
    identities = [(root.stat().st_dev, root.stat().st_ino) for _, root in rows]
    require(len(set(identities)) == 6, "Graph final case roots share physical identity")
    expected = {root / "parent-source-binding.original.json" for _, root in rows}
    require(set(cases_root.rglob("parent-source-binding.original.json")) == expected,
            "Graph final original case root bijection")
    return rows


def graph_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Graph duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Graph nonfinite JSON")))


def graph_ref(path, maximum=512 * GRAPH_BYTES):
    path = Path(path)
    raw, value = studio68_original(path, maximum)
    return {"path": str(path), "sha256": value["sha256"], "size_bytes": value["bytes"]}


def graph_read(reference, maximum=512 * GRAPH_BYTES):
    require(type(reference) is dict and set(reference) == {"path", "sha256", "size_bytes"}
            and type(reference["size_bytes"]) is int and 0 <= reference["size_bytes"] <= maximum
            and re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is not None, "Graph original reference")
    path = Path(reference["path"])
    require(path.is_absolute() and path.resolve(strict=True) == path, "Graph original canonical path")
    raw, value = studio68_original(path, maximum)
    require(value == {"bytes": reference["size_bytes"], "sha256": reference["sha256"]}, "Graph original changed")
    return raw


def graph_put(path, raw):
    path = Path(path)
    require(path.parent.resolve(strict=True) == path.parent and type(raw) is bytes, "Graph publication path")
    with path.open("xb") as stream:
        require(stream.write(raw) == len(raw), "Graph original short write")
        stream.flush(); os.fsync(stream.fileno())
    require(studio68_original(path, max(len(raw), 1))[0] == raw, "Graph publication readback")
    return graph_ref(path)


def graph_put_json(path, value):
    return graph_put(path, (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())


def graph_run_binding(run, binding):
    require(type(run) is dict and type(run.get("id")) is int and str(run["id"]) == binding["run_id"]
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1 and run.get("head_sha") == binding["source_sha"]
            and run.get("repository", {}).get("full_name") == GRAPH_REPOSITORY
            and run.get("head_repository", {}).get("full_name") == GRAPH_REPOSITORY
            and run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress"
            and run.get("path", "").split("@", 1)[0] == GRAPH_WORKFLOW
            and type(run.get("workflow_id")) is int and run["workflow_id"] > 0
            and run.get("actor", {}).get("type") == "User"
            and type(run.get("actor", {}).get("id")) is int and run["actor"]["id"] > 0,
            "Graph R74 original run actor/source")


def graph_status_core(run, rows, binding):
    """Pure validation, not an approval writer or production admission flag."""
    require(set(binding) == {"source_sha", "run_id", "case_id", "index_sha256"}
            and re.fullmatch(r"[0-9a-f]{40}", binding["source_sha"])
            and re.fullmatch(r"[1-9][0-9]*", binding["run_id"])
            and re.fullmatch(r"[0-9a-f]{64}", binding["index_sha256"])
            and binding["case_id"] in {row[0] for row in GRAPH_CASES}, "Graph R74 binding")
    graph_run_binding(run, binding)
    context = "mw-owner/" + GRAPH_SCOPE + "/" + binding["run_id"] + "/1/" + binding["case_id"]
    require(type(rows) is list and len(rows) <= GRAPH_API_PAGES * 100, "Graph R74 status bound")
    previous = 2**53 - 1
    for row in rows:
        require(type(row) is dict and type(row.get("id")) is int and 0 < row["id"] < previous,
                "Graph R74 status order")
        previous = row["id"]
    chosen = next((row for row in rows if type(row.get("context")) is str and row["context"].lower() == context.lower()), None)
    require(chosen is not None, "Graph R74 status not present")
    require(chosen.get("state") == "success" and chosen.get("creator", {}).get("type") == "User"
            and type(chosen["creator"].get("id")) is int and chosen["creator"]["id"] == run["actor"]["id"]
            and chosen.get("target_url") == "https://github.com/" + GRAPH_REPOSITORY + "/actions/runs/" + binding["run_id"]
            and chosen.get("description") == "admission-sha256:" + binding["index_sha256"],
            "Graph R74 newest status not authorized")
    return {"context": context, "status_id": chosen["id"], "actor_id": run["actor"]["id"],
            "source_sha": binding["source_sha"], "run_id": binding["run_id"], "run_attempt": 1,
            "index_sha256": binding["index_sha256"], "meaning": "OWNER_ADMISSION_ONLY_NEVER_PRODUCT_OR_A5_ACCEPTANCE"}


def graph_api_get(suffix, token, publish, label, end):
    import ssl
    import urllib.error
    import urllib.request
    require(suffix.startswith(("/actions/runs/", "/commits/")) and time.monotonic() < end, "Graph fixed API request deadline")
    url = "https://api.github.com/repos/" + GRAPH_REPOSITORY + suffix
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError("Graph R74 redirect refused")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"})
    started = time.monotonic_ns()
    try:
        response = opener.open(request, timeout=max(.001, min(12, end - time.monotonic())))
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(GRAPH_API_BYTES + 1)
        original = publish(label + ".response.original.json", raw)
        meta = {"url": url, "status": response.status, "date": response.headers.get("Date"),
                "age": response.headers.get("Age", "0"), "link": response.headers.get("Link", ""),
                "request_id": response.headers.get("X-GitHub-Request-Id"),
                "started_mono_ns": started, "finished_mono_ns": time.monotonic_ns(), "body": original}
        publish(label + ".transport.original.json", (json.dumps(meta, sort_keys=True) + "\n").encode())
        require(response.status == 200 and meta["age"] == "0" and type(meta["date"]) is str
                and 0 < len(raw) <= GRAPH_API_BYTES and time.monotonic() <= end, "Graph R74 HTTP response denied")
        return graph_json(raw), meta


def graph_api_observation(index, index_ref, token, publish, label, end):
    require(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
            and os.environ.get("GITHUB_REPOSITORY") == GRAPH_REPOSITORY
            and os.environ.get("GITHUB_API_URL") == "https://api.github.com"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_JOB") == "real-service"
            and os.environ.get("GITHUB_SHA") == index["source"]["sha"]
            and os.environ.get("GITHUB_WORKFLOW_SHA") == index["source"]["sha"]
            and os.environ.get("GITHUB_RUN_ID") == index["source"]["run_id"]
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and index["source"]["run_attempt"] == 1 and type(token) is str and token, "Graph authenticated job context")
    run, run_meta = graph_api_get("/actions/runs/" + index["source"]["run_id"], token, publish, label + "-run", end)
    graph_run_binding(run, {"source_sha": index["source"]["sha"], "run_id": index["source"]["run_id"]})
    rows, pages = [], []
    contexts = {("mw-owner/" + GRAPH_SCOPE + "/" + index["source"]["run_id"] + "/1/" + row[0]).lower() for row in GRAPH_CASES}
    for page in range(1, GRAPH_API_PAGES + 1):
        value, meta = graph_api_get("/commits/" + index["source"]["sha"] + "/statuses?per_page=100&page=" + str(page),
                                    token, publish, label + "-statuses-" + str(page), end)
        require(type(value) is list and len(value) <= 100, "Graph status page")
        rows.extend(value); pages.append(meta)
        seen = {row.get("context", "").lower() for row in rows if type(row.get("context")) is str}
        if contexts <= seen or len(value) < 100 and 'rel="next"' not in meta["link"]:
            break
        require(page < GRAPH_API_PAGES, "Graph status pagination bound")
    return run, rows, {"run": run_meta, "pages": pages, "index": index_ref}


def graph_source_view(raw):
    require(type(raw) is dict and set(raw) == {"sha", "tree", "files"}, "Graph explicit source observation")
    files = {}
    for name, row in raw["files"].items():
        require(type(row) is dict and set(row) == {"git_bytes", "git_sha256", "checkout", "checkout_encoding"}
                and row["checkout_encoding"] == "EXACT_GIT_BYTES"
                and row["checkout"] == {"bytes": row["git_bytes"], "sha256": row["git_sha256"]},
                "Graph Linux checkout bytes are not original Git bytes")
        files[name] = row["checkout"]
    return {"checkout": raw["sha"], "tree": raw["tree"], "files": files}


def graph_process_original(value, expected, remember):
    require(set(value) == {"command", "stdout", "stderr"}, "Graph producer process fields")
    command = graph_json(remember(value["command"], GRAPH_BYTES))
    require(command.get("argv") == expected["argv"] and command.get("cwd") == expected["cwd"]
            and command.get("selected_environment") == expected["selected_environment"], "Graph original command binding")
    require_observed_command(command)
    require(command.get("exit_code") == 0 and command.get("cleanup_errors") == []
            and not any(key in command for key in ("observation", "uncertainty_reason", "error", "receipt_error", "secondary_observation_errors")),
            "Graph original process incomplete")
    require(datetime.fromisoformat(command["finished_utc"]) >= datetime.fromisoformat(command["started_utc"]), "Graph original process time")
    for name in ("stdout", "stderr"):
        raw = remember(value[name], 64 * GRAPH_BYTES)
        require(command.get(name) == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, "Graph command stream original differs")
    return command, remember(value["stdout"], 64 * GRAPH_BYTES)


def graph_verify_producer_index(index_ref, case_id):
    """Original byte/causality checks. The caller must separately authenticate R74.

    This function never turns a reference map into authority. The owner invokes
    it only after independently observing the exact external status and binding
    this module's original bytes before import.
    """
    refs, physical = {}, {}
    def remember(ref, maximum=512 * GRAPH_BYTES):
        require(len(refs) < 512 or ref["path"] in refs, "Graph original count")
        path = Path(ref["path"])
        before = path.lstat()
        raw = graph_read(ref, maximum)
        stamp = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        require(ref["path"] not in refs or refs[ref["path"]] == ref and physical[ref["path"]] == stamp,
                "Graph original path retargeted")
        refs[ref["path"]], physical[ref["path"]] = ref, stamp
        return raw
    index = graph_json(remember(index_ref, GRAPH_BYTES))
    require(set(index) == {"schema", "source", "binaries", "runtime", "source_original", "sdk_original", "builds",
                           "ordinary_preflight", "runtime_originals", "callback_roster", "native_enforcement", "contract"}
            and index["schema"] == GRAPH_INDEX_SCHEMA and set(index["contract"]) == GRAPH_CONTRACT_FIELDS,
            "Graph acyclic index contract fields")
    contract, source, binaries = index["contract"], index["source"], index["binaries"]
    require(set(binaries) == set(GRAPH_ROLES) and len({ref["path"] for ref in binaries.values()}) == 10,
            "Graph ten distinct runtime roles")
    execution = contract["execution"]
    require(set(execution) == {"scope", "job", "job_id", "deadline_original", "deadline_unix_ms", "producer_root", "cases_root", "budget"}
            and execution["scope"] == GRAPH_SCOPE and execution["job"] == "real-service"
            and execution["budget"] == GRAPH_BUDGET, "Graph finite original allocation")
    require(set(contract["cases"]) == {row[0] for row in GRAPH_CASES} and case_id in contract["cases"], "Graph closed six-case roster")
    for name in GRAPH_AUTHORITY:
        require(name in contract["source_files"], "Graph authority source omitted: " + name)
    for ref_name, root_name, expected_sha, expected_tree, files in (
        ("source_original", "repository_root", source["sha"], source["tree"], contract["source_files"]),
        ("sdk_original", "sdk_root", source["sdk_sha"], contract["sdk_tree"], contract["sdk_files"])):
        value = graph_json(remember(index[ref_name], 8 * GRAPH_BYTES))
        require(value["run_id"] == source["run_id"] and str(value["run_attempt"]) == str(source["run_attempt"]) == "1"
                and value["source_before"] == value["source_after"], "Graph source original run/attempt or before/after")
        view = graph_source_view(value["source_before"])
        require(view == {"checkout": expected_sha, "tree": expected_tree, "files": files}, "Graph exact source original differs")
        require(set(value["retained_files"]) == set(files), "Graph retained source roster")
        root = Path(source[root_name])
        for name, identity in files.items():
            require(name and not name.startswith("/") and "\\" not in name
                    and all(part not in ("", ".", "..") for part in name.split("/")), "Graph relative source name")
            remember({"path": str(root / name), "sha256": identity["sha256"], "size_bytes": identity["bytes"]})
            retained = value["retained_files"][name]
            require(Path(retained["path"]).is_relative_to(Path(execution["producer_root"]))
                    and retained["path"] != str(root / name)
                    and {"bytes": retained["size_bytes"], "sha256": retained["sha256"]} == identity, "Graph retained source differs")
            remember(retained)
    locks = ("Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json")
    require(set(source["locks"]) == set(locks) and all(source["locks"][name] == contract["source_files"][name]["sha256"] for name in locks), "Graph exact four locks")
    lock = graph_json(remember({"path": str(Path(source["repository_root"]) / "SOURCE_LOCK.json"),
        "sha256": contract["source_files"]["SOURCE_LOCK.json"]["sha256"], "size_bytes": contract["source_files"]["SOURCE_LOCK.json"]["bytes"]}))
    require(lock["dependencies"]["semwright"]["revision"] == source["sdk_sha"], "Graph SOURCE_LOCK SDK pin")
    require(set(index["builds"]) == set(contract["builds"]) == set(GRAPH_RUST_ROLES), "Graph eight build roles")
    writer_roles = {*GRAPH_RUST_ROLES, "source_observation", "sdk_observation", "ordinary_preflight", "runtime"}
    require(set(contract["writer_sources"]) == writer_roles, "Graph writer source roster")
    for names in contract["writer_sources"].values():
        require(type(names) is list and 1 <= len(names) <= 16 and len(set(names)) == len(names)
                and all(name in contract["source_files"] for name in names), "Graph writer source binding")
    for role in GRAPH_RUST_ROLES:
        original, expected = index["builds"][role], contract["builds"][role]
        require(set(original) == {"process", "retained_binary"}, "Graph build original fields")
        command, stdout = graph_process_original(original["process"], expected["command"], remember)
        root_name = "sdk_root" if role in GRAPH_SDK_ROLES else "repository_root"
        require(command["cwd"] == source[root_name] and all(flag in command["argv"] for flag in ("build", "--locked", "--message-format=json")), "Graph original Cargo build")
        rows = [graph_json(line) for line in stdout.splitlines() if line]
        ends = [row for row in rows if row.get("reason") == "build-finished"]
        require(len(ends) == 1 and rows[-1] == ends[0] and ends[0].get("success") is True, "Graph Cargo terminal")
        matches = [row for row in rows if row.get("reason") == "compiler-artifact" and row.get("executable") == binaries[role]["path"]]
        require(len(matches) == 1, "Graph Cargo executable bijection")
        artifact = matches[0]
        target = {"package_id": artifact.get("package_id"), **{key: artifact.get("target", {}).get(key) for key in ("name", "kind", "src_path")}}
        require(target == expected["target"] and artifact.get("fresh") is False and artifact.get("profile", {}).get("test") is False,
                "Graph Cargo target source/cache")
        relative = Path(target["src_path"]).relative_to(source[root_name]).as_posix()
        require(relative in contract["sdk_files" if role in GRAPH_SDK_ROLES else "source_files"], "Graph target source omitted")
        remember(binaries[role]); remember(original["retained_binary"])
        require(original["retained_binary"]["path"] != binaries[role]["path"]
                and all(original["retained_binary"][key] == binaries[role][key] for key in ("sha256", "size_bytes")), "Graph retained executable differs")
    command, stdout = graph_process_original(index["ordinary_preflight"]["process"], contract["ordinary_preflight"], remember)
    stdin_ref = index["ordinary_preflight"]["stdin"]
    require(command["argv"][0] == binaries["ordinary_service"]["path"]
            and graph_json(remember(stdin_ref, 16384)) == phase1_service47_preflight_request()
            and command.get("stdin") == {"path": Path(stdin_ref["path"]).name, "sha256": stdin_ref["sha256"], "bytes": stdin_ref["size_bytes"]}, "Graph compiled SDK preflight input")
    phase1_service47_validate_preflight(stdout, source["sdk_sha"])
    runtime, originals = index["runtime"], index["runtime_originals"]
    require(set(originals) == {"producer", "inventory", "receipt", "intake", "profile_files"}, "Graph runtime originals")
    graph_process_original(originals["producer"], contract["runtime"]["command"], remember)
    inventory = graph_json(remember(originals["inventory"], 2 * GRAPH_BYTES))
    intake = graph_json(remember(originals["intake"], 2 * GRAPH_BYTES))
    require(inventory == contract["runtime"]["inventory"] and intake == contract["runtime"]["intake"]
            and originals["inventory"]["path"] == runtime["manifest"] and originals["inventory"]["sha256"] == runtime["manifest_sha256"]
            and originals["intake"]["path"] == runtime["intake_receipt"] and originals["intake"]["sha256"] == runtime["intake_receipt_sha256"], "Graph original runtime binding")
    require(inventory["schema"] == "motionwright.manim.runtime-inventory/2" and inventory["context"] == {
        "source_sha": source["sha"], "semwright_sha": source["sdk_sha"], "run_id": source["run_id"], "run_attempt": "1"}
        and inventory["manim_version"] == "0.21.0" and inventory["pyav_version"] == "15.0.0"
        and inventory["native_render_performed"] is False, "Graph real runtime producer context")
    remember(originals["receipt"], 2 * GRAPH_BYTES)
    require(originals["receipt"]["path"] == str(Path(runtime["root"]) / "runtime.json")
            and originals["receipt"]["sha256"] == inventory["runtime_receipt_sha256"], "Graph runtime receipt")
    for role, label in (("python3", "python"), ("ffmpeg", "ffmpeg")):
        remember(binaries[role])
        require(inventory["binaries"][label] == {"path": binaries[role]["path"], "sha256": binaries[role]["sha256"], "bytes": binaries[role]["size_bytes"]}, "Graph runtime executable original")
    require(intake["schema"] == "motionwright.manim.graph-runtime-source-intake/1" and intake["status"] == "SOURCE_INTAKE_ONLY"
            and intake["profile_id"] == "mw-manim-semantic-graph-v1" and intake["source_root"] == source["repository_root"]
            and intake["runtime_root"] == runtime["root"] and all(intake[key] is False for key in (
                "profile_admitted", "native_render_performed", "compiled_runner_pin_agreement_verified", "native_font_open_observed", "pango_layout_observed")), "Graph source intake is not acceptance")
    require(originals["profile_files"] == contract["runtime"]["profile_files"] and len(originals["profile_files"]) == 15
            and intake["files_count"] == len(intake["files"]) == 15, "Graph fifteen runtime originals")
    by_name = {row["relative_path"]: row["ref"] for row in originals["profile_files"]}
    require(len(by_name) == 15, "Graph runtime duplicate path")
    for row in intake["files"]:
        ref = by_name[row["runtime_relative_path"]]
        require(ref["path"] == str(Path(runtime["root"]) / row["runtime_relative_path"])
                and ref["sha256"] == row["sha256"] and ref["size_bytes"] == row["size_bytes"]
                and contract["source_files"][row["source_relative_path"]] == {"bytes": row["size_bytes"], "sha256": row["sha256"]}, "Graph source/runtime asset binding")
        remember(ref, 8 * GRAPH_BYTES)
    closure = Path(runtime["root"]) / "graph-profile-v1/source-closure.json"
    remember({"path": str(closure), "sha256": originals["intake"]["sha256"], "size_bytes": originals["intake"]["size_bytes"]}, 2 * GRAPH_BYTES)
    for field in ("compiler_source", "source_lock"):
        row = intake[field]
        require(contract["source_files"][row["relative_path"]] == {"bytes": row["size_bytes"], "sha256": row["sha256"]}, "Graph compiler/source lock binding")
    roster = graph_json(remember(index["callback_roster"], GRAPH_BYTES))
    native = graph_json(remember(index["native_enforcement"], GRAPH_BYTES))
    require(roster == contract["callback_roster"] and native == contract["native_enforcement"]
            and contract["native_enforcing_source"] == contract["source_files"]["crates/native/examples/native-graph-review-session.rs"]
            and contract["native_enforcing_source"]["sha256"] != "f0cc2efc496f1ab13458395c10b09f502a5cbdc12f906827378c5e40b1716dd9", "Graph reviewed Native cumulative enforcement source")
    require(roster["cases"] == [{"case_id": case, "title": title, "callback_caps": list(caps)} for case, title, caps in GRAPH_CASES], "Graph callback source roster")
    for case, title, caps in GRAPH_CASES:
        row = contract["cases"][case]
        require(set(row) == {"title", "callback_caps", "roster_source_files"} and row["title"] == title
                and row["callback_caps"] == list(caps) and set(GRAPH_CALLGRAPH) <= set(row["roster_source_files"])
                and len(set(row["roster_source_files"])) == len(row["roster_source_files"])
                and all(name in contract["source_files"] for name in row["roster_source_files"]), "Graph callback callgraph binding")
    deadline = graph_json(remember(execution["deadline_original"], 8 * GRAPH_BYTES + 65536))
    require(deadline["schema"] == "motionwright.graph-job-deadline/1" and deadline["job_seconds"] == 10800
            and deadline["deadline_unix_ms"] == execution["deadline_unix_ms"]
            and deadline["binding"]["GITHUB_SHA"] == source["sha"] and deadline["binding"]["GITHUB_RUN_ID"] == source["run_id"]
            and deadline["binding"]["GITHUB_RUN_ATTEMPT"] == "1", "Graph original deadline binding")
    jobs = [row for row in deadline["jobs"]["jobs"] if row.get("id") == execution["job_id"]
            and row.get("name") == "Real SQLite and Studio / " + GRAPH_SCOPE]
    require(len(jobs) == 1, "Graph producer original job")
    job_started = datetime.fromisoformat(jobs[0]["started_at"].replace("Z", "+00:00"))
    for process in [*(item["process"] for item in index["builds"].values()),
                    index["ordinary_preflight"]["process"], index["runtime_originals"]["producer"]]:
        command = graph_json(remember(process["command"], GRAPH_BYTES))
        require(command.get("graph_job") == {"source_sha": source["sha"], "run_id": source["run_id"],
                "run_attempt": 1, "job_id": execution["job_id"], "deadline_original": execution["deadline_original"]}
                and command.get("deadline_admission", {}).get("deadline_mono_ns") == deadline["deadline_mono_ns"],
                "Graph producer command belongs to another original job")
        begin = datetime.fromisoformat(command["started_utc"]); end = datetime.fromisoformat(command["finished_utc"])
        require(job_started <= begin <= end and (end - job_started).total_seconds() < 10800,
                "Graph producer command predates or exceeds its original job")
    for ref in tuple(refs.values()):
        remember(ref)
    return {"index": index, "references": list(refs.values()),
            "physical": {name: list(value) for name, value in physical.items()},
            "meaning": "ORIGINALS_VALIDATED_REQUIRES_SEPARATE_AUTHENTICATED_R74"}


def graph_deadline_from_original(original, environment, now_ns, boot_id):
    binding = phase1_service47_deadline_binding(environment)
    require(binding["GITHUB_REPOSITORY"] == GRAPH_REPOSITORY and binding["GITHUB_RUN_ATTEMPT"] == "1"
            and original.get("schema") == "motionwright.graph-job-deadline/1"
            and original.get("binding") == binding and original.get("boot_id") == boot_id
            and original.get("job_seconds") == 10800 and original.get("phase_seconds") == GRAPH_PHASES
            and original.get("rounding_allowance_seconds") == 2, "Graph original deadline binding")
    begin, end = original.get("request_started_mono_ns"), original.get("response_received_mono_ns")
    require(type(begin) is int and type(end) is int and 0 < begin <= end <= now_ns, "Graph original clock")
    run, jobs = original["run"], original["jobs"]
    require(type(run.get("id")) is int and run["id"] == int(binding["GITHUB_RUN_ID"])
            and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
            and run.get("head_sha") == binding["GITHUB_SHA"] and run.get("repository", {}).get("full_name") == GRAPH_REPOSITORY
            and run.get("path", "").split("@", 1)[0] == GRAPH_WORKFLOW
            and run.get("event") == "workflow_dispatch" and run.get("status") == "in_progress"
            and run.get("actor", {}).get("type") == "User" and type(run["actor"].get("id")) is int and run["actor"]["id"] > 0,
            "Graph actual original dispatch actor")
    rows = jobs.get("jobs")
    require(type(rows) is list and type(jobs.get("total_count")) is int and 0 < jobs["total_count"] == len(rows) <= 100,
            "Graph original jobs incomplete")
    selected = [row for row in rows if row.get("name") == "Real SQLite and Studio / " + GRAPH_SCOPE]
    require(len(selected) == 1, "Graph current original job absent or ambiguous")
    job = selected[0]
    require(type(job.get("run_id")) is int and job["run_id"] == int(binding["GITHUB_RUN_ID"])
            and type(job.get("run_attempt")) is int and job["run_attempt"] == 1
            and job.get("head_sha") == binding["GITHUB_SHA"] and job.get("status") == "in_progress"
            and job.get("conclusion") is None and job.get("runner_name") == binding["RUNNER_NAME"]
            and type(job.get("id")) is int and job["id"] > 0, "Graph actual job/runner binding")
    from email.utils import parsedate_to_datetime
    server = parsedate_to_datetime(original["jobs_http_date"])
    start = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    require(server.tzinfo is not None and start.tzinfo is not None
            and server.utcoffset().total_seconds() == start.utcoffset().total_seconds() == 0 and server >= start,
            "Graph original job start and HTTP Date")
    age = server - start
    elapsed = (age.days * 86400 + age.seconds + 2) * 1000000000 + age.microseconds * 1000
    deadline = end + 10800 * 1000000000 - elapsed - (end - begin)
    require(original.get("deadline_mono_ns") == deadline and deadline > now_ns
            and type(original.get("clock_anchor_mono_ns")) is int
            and end <= original["clock_anchor_mono_ns"] <= now_ns
            and original.get("deadline_unix_ms") == original["clock_anchor_wall_ms"]
                + (deadline - original["clock_anchor_mono_ns"]) // 1000000, "Graph original aggregate deadline changed")
    return deadline


class GraphOriginalClock(Phase1Service47Clock):
    """Reuse the existing signal/owned-process boundary with Graph's own origin."""
    def __init__(self, root):
        self.root = root
        ref = graph_ref(root / "tools/graph-deadline.original.json", 8 * GRAPH_BYTES + 65536)
        require(ref["sha256"] == os.environ.get(GRAPH_DEADLINE_ENV), "Graph authenticated deadline pin")
        original = graph_json(graph_read(ref, 8 * GRAPH_BYTES + 65536))
        for name, field in (("graph-run-response.original.json", "run"), ("graph-jobs-response.original.json", "jobs")):
            raw, identity = studio68_original(root / "tools" / name, 4 * GRAPH_BYTES)
            require(identity == original["response_originals"][name] and graph_json(raw) == original[field], "Graph authenticated API original changed")
            transport_name = name.replace(".original.json", ".transport.original.json")
            raw, identity = studio68_original(root / "tools" / transport_name, 65536)
            require(identity == original["response_transport_originals"][transport_name]
                    and graph_json(raw) == original["transport_observations"][name], "Graph HTTP transport original changed")
        now = time.monotonic_ns()
        self.deadline_ns = graph_deadline_from_original(original, os.environ, now, Path("/proc/sys/kernel/random/boot_id").read_text().strip())
        self.original, self.original_ref, self.last_ns = original, ref, now
        self.unknown = self.expired = False; self.active_process = None
        self.admissions = []; self.phase_name = None; self.phase_end = None

    def future(self, phase):
        names = list(GRAPH_PHASES)
        require(phase in GRAPH_PHASES, "Graph closed phase")
        return sum(GRAPH_PHASES[name] for name in names[names.index(phase) + 1:])

    def admit(self, label, seconds, *, reserve=None, now_ns=None):
        now = time.monotonic_ns() if now_ns is None else now_ns
        reserve = self.future(self.phase_name) if reserve is None and self.phase_name else (reserve or 0)
        require(type(seconds) is int and 0 < seconds <= 10800 and type(reserve) is int and 0 <= reserve < 10800
                and now >= self.last_ns and not self.unknown, "Graph phase cap, clock or UNKNOWN")
        self.last_ns = now
        record = {"phase": self.phase_name, "label": label, "seconds": seconds, "reserve_seconds": reserve,
                  "observed_mono_ns": now, "deadline_mono_ns": self.deadline_ns, "retry": "FORBIDDEN"}
        require(now + (seconds + reserve) * 1000000000 <= self.deadline_ns
                and (self.phase_end is None or now + seconds * 1000000000 <= self.phase_end),
                "Graph full phase and remaining custody cannot fit before effect")
        self.admissions.append(record)
        return record

    @contextmanager
    def phase(self, name):
        require(self.phase_name is None and name in GRAPH_PHASES, "Graph nested or foreign phase")
        seconds, future = GRAPH_PHASES[name], self.future(name)
        admitted = self.admit(name, seconds, reserve=future)
        self.phase_name, self.phase_end = name, time.monotonic_ns() + seconds * 1000000000
        graph_put_json(self.root / "tools" / ("graph-phase-" + name + ".intent.original.json"), admitted)
        try:
            with self.window(reserve=future):
                signal.setitimer(signal.ITIMER_REAL, max(.001, (self.phase_end - time.monotonic_ns()) / 1000000000))
                yield self
                require(not self.unknown and time.monotonic_ns() < self.phase_end, "Graph phase did not close")
            terminal_root = self.root / ("retained" if name == "archive" else "tools")
            graph_put_json(terminal_root / ("graph-phase-" + name + ".terminal.original.json"), {
                "meaning": "PHASE_COMMANDS_AND_OBSERVATIONS_RETURNED_ONLY", "finished_mono_ns": time.monotonic_ns(),
                "deadline_original": self.original_ref, "admissions": self.admissions})
        finally:
            self.phase_name = self.phase_end = None


def graph_layout(repo):
    repo = repo.resolve(strict=True); root = repo.parent / "v"
    require(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux" and repo.name == "g"
            and Path(os.environ.get("GITHUB_WORKSPACE", "")) == repo.parent
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "Graph actual first-attempt Linux g/v job")
    return repo, root


def graph_source_observe(repo, expected, paths):
    import importlib.util
    module_path = repo / "tooling/source_checkout_observation.py"
    # SDK callers pass the already source-bound Motionwright helper explicitly.
    helper_root = Path(os.environ["GITHUB_WORKSPACE"]) / "g"
    module_path = helper_root / "tooling/source_checkout_observation.py"
    module_spec = importlib.util.spec_from_file_location("graph_existing_source_observer", module_path)
    module = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(module)
    return module.source_observation(repo, expected, tuple(sorted(set(paths))))


def graph_tree_allocation(root, maximum_bytes, maximum_members):
    require(root.is_dir() and root.resolve(strict=True) == root, "Graph allocated root")
    # The allocated root itself is a TAR member and consumes its directory slot.
    total, members, directories = 0, 1, 1
    require(maximum_members >= 1, "Graph root member allocation exhausted")
    for item in sorted(root.rglob("*")):
        state = archive_stat(item); members += 1
        require(members <= maximum_members, "Graph member allocation exhausted")
        if stat.S_ISDIR(state.st_mode):
            directories += 1
        else:
            require(stat.S_ISREG(state.st_mode) and state.st_nlink == 1, "Graph allocated original is not regular")
            total += state.st_size
            require(total <= maximum_bytes, "Graph byte allocation exhausted")
    return {"bytes": total, "members": members, "directories": directories}


def graph_producer_room(root, extra_bytes, extra_members):
    observed = graph_tree_allocation(root, GRAPH_BUDGET["producer_bytes"], GRAPH_BUDGET["producer_members"])
    # Leave a finite 8 MiB/96-member remainder for the index, source observations,
    # six consumed-case records and original review seal metadata.
    require(observed["bytes"] + extra_bytes + 8 * GRAPH_BYTES <= GRAPH_BUDGET["producer_bytes"]
            and observed["members"] + extra_members + 96 <= GRAPH_BUDGET["producer_members"],
            "Graph original producer/copy allocation cannot fit before publication")


def graph_copy_original(source, destination, producer):
    reference = graph_ref(source, 256 * GRAPH_BYTES)
    parents = []
    current = destination.parent
    while not current.exists():
        parents.append(current); current = current.parent
    graph_producer_room(producer, reference["size_bytes"], len(parents) + 1)
    for parent in reversed(parents):
        parent.mkdir(mode=0o700)
    result = graph_put(destination, graph_read(reference, 256 * GRAPH_BYTES))
    require(all(result[key] == reference[key] for key in ("sha256", "size_bytes")), "Graph retained original copy differs")
    return result


def graph_command(env, argv, cwd, producer, label, seconds, clock, *, maximum_file_bytes=None):
    env = dict(env); env.pop(GRAPH_TOKEN_ENV, None)
    graph_producer_room(producer, 16 * GRAPH_BYTES + 65536, 3)
    result = execute(argv, cwd, env, producer, label, seconds, deadline=clock, maximum_stream_bytes=8 * GRAPH_BYTES,
                     maximum_file_bytes=maximum_file_bytes)
    require_observed_command(result); require(result.get("exit_code") == 0, "Graph real producer command failed: " + label)
    graph_tree_allocation(producer, GRAPH_BUDGET["producer_bytes"], GRAPH_BUDGET["producer_members"])
    return {"command": graph_ref(producer / (label + ".command.json")),
            "stdout": graph_ref(producer / (label + ".stdout.log")), "stderr": graph_ref(producer / (label + ".stderr.log"))}, result


def graph_cargo_targets(metadata, roles, root):
    result = {}
    for role in roles:
        package_name, target_name, kind = GRAPH_TARGETS[role]
        packages = [item for item in metadata["packages"] if item["name"] == package_name
                    and Path(item["manifest_path"]).is_relative_to(root)]
        require(len(packages) == 1, "Graph actual Cargo package absent/ambiguous: " + role)
        package = packages[0]
        targets = [row for row in package["targets"] if row["name"] == target_name and row["kind"] == [kind]]
        require(len(targets) == 1, "Graph actual Cargo target absent/ambiguous: " + role)
        target = targets[0]
        require(Path(target["src_path"]).is_relative_to(root), "Graph Cargo target escaped source")
        result[role] = {"package_id": package["id"], "name": target["name"], "kind": target["kind"], "src_path": target["src_path"]}
    return result


def graph_build_group(repo, root, producer, sdk, cargo, env, targets, clock):
    roles = GRAPH_SDK_ROLES if sdk else tuple(role for role in GRAPH_RUST_ROLES if role not in GRAPH_SDK_ROLES)
    source_root = root if sdk else repo
    phase, label = ("sdk-build", "graph-sdk-build") if sdk else ("product-build", "graph-product-build")
    arguments = [cargo, "build", "--locked", "--jobs", "1", "--message-format=json",
                 "--config", "profile.dev.debug=0", "--config", "profile.test.debug=0"]
    packages = list(dict.fromkeys(GRAPH_TARGETS[role][0] for role in roles))
    for package in packages:
        arguments += ["-p", package]
    for role in roles:
        arguments += ["--" + GRAPH_TARGETS[role][2], GRAPH_TARGETS[role][1]]
    env = dict(env, CARGO_TARGET_DIR=str(root / "target" if sdk else repo.parent / "v/t"))
    require(not Path(env["CARGO_TARGET_DIR"]).exists(), "Graph fresh target required; cached binaries are not producers")
    with clock.phase(phase):
        original, command = graph_command(env, arguments, source_root, producer, label, 1740, clock)
        rows = [graph_json(line) for line in graph_read(original["stdout"], 64 * GRAPH_BYTES).splitlines() if line]
        require(len([row for row in rows if row.get("reason") == "build-finished"]) == 1
                and rows[-1].get("reason") == "build-finished" and rows[-1].get("success") is True, "Graph actual Cargo build terminal")
        outputs, records, contracts = {}, {}, {}
        expected_command = {key: command[key] for key in ("argv", "cwd", "selected_environment")}
        for role in roles:
            target = targets[role]
            matches = [row for row in rows if row.get("reason") == "compiler-artifact" and row.get("executable")
                       and {"package_id": row.get("package_id"), **{key: row.get("target", {}).get(key) for key in ("name", "kind", "src_path")}} == target]
            require(len(matches) == 1 and matches[0].get("fresh") is False and matches[0].get("profile", {}).get("test") is False,
                    "Graph actual executable was not freshly built: " + role)
            binary = Path(matches[0]["executable"])
            outputs[role] = graph_ref(binary, 256 * GRAPH_BYTES)
            retained = graph_copy_original(binary, producer / "binaries" / role, producer)
            records[role] = {"process": original, "retained_binary": retained}
            contracts[role] = {"command": expected_command, "target": target}
    return outputs, records, contracts


def graph_runtime_inventory(repo, runtime, producer):
    # Existing Manim /2 producer body, retaining its actual observed wheel/imports.
    import hashlib, json, os, platform, shutil, subprocess, sys, zipfile
    from pathlib import Path
    sys.path.insert(0, str(runtime / "site-packages"))
    import av
    import av._core
    import av.filter
    import av.logging
    import manim
    root = runtime.resolve(strict=True)
    packages = (root / "site-packages").resolve(strict=True)
    private = producer / "runtime-provision"
    if manim.__version__ != "0.21.0" or av.__version__ != "15.0.0":
        raise SystemExit("Installed Manim/PyAV versions differ from runtime schema 2")
    if not all(Path(module.__file__).resolve(strict=True).is_relative_to(packages)
               for module in (manim, av, av._core)):
        raise SystemExit("Runtime imports escaped the installed bundle")
    if not {"buffer", "showinfo", "buffersink"} <= set(av.filter.filters_available):
        raise SystemExit("PyAV native metadata filters are unavailable")
    if not all(callable(getattr(av.logging, name, None)) for name in (
            "get_level", "set_level", "set_libav_level",
            "get_skip_repeated", "set_skip_repeated")):
        raise SystemExit("PyAV native logging observation API is unavailable")

    def file_identity(path):
        path = path.resolve(strict=True)
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        return {"path": str(path), "bytes": path.stat().st_size, "sha256": h.hexdigest()}

    wheel_info = json.loads((private / "pyav-wheel.json").read_bytes())
    wheel = private / "wheels" / wheel_info["filename"]
    observed_wheel = file_identity(wheel)
    if any(observed_wheel[key] != wheel_info[key] for key in ("bytes", "sha256")):
        raise SystemExit("Observed PyAV wheel identity changed")
    loaded = set()
    for line in Path("/proc/self/maps").read_text(encoding="utf-8").splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            path = Path(fields[5])
            if path.is_file() and path.resolve(strict=True).is_relative_to(packages):
                loaded.add(path.resolve(strict=True))
    native_files = []
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise SystemExit("PyAV wheel contains duplicate members")
        for path in sorted(loaded):
            relative = path.relative_to(packages).as_posix()
            if relative not in members:
                continue
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            row = file_identity(path)
            wheel_sha = hashlib.sha256(archive.read(relative)).hexdigest()
            if wheel_sha != row["sha256"]:
                raise SystemExit("Loaded PyAV native bytes differ from the observed wheel")
            native_files.append({"path": relative, "bytes": row["bytes"],
                                 "sha256": row["sha256"], "wheel_sha256": wheel_sha})
    core = Path(av._core.__file__).resolve(strict=True).relative_to(packages).as_posix()
    if not native_files or core not in {row["path"] for row in native_files}:
        raise SystemExit("No original wheel binding for the loaded PyAV core")
    selected_python = Path(os.environ.get("SEMWRIGHT_TEST_PYTHON", sys.executable)).resolve(strict=True)
    if selected_python != Path(sys.executable).resolve(strict=True):
        raise SystemExit("Observed Python differs from the selected Driver Host interpreter")
    binaries = {"python": file_identity(selected_python)}
    versions = {"python": sys.version}
    for name in ("ffmpeg", "ffprobe"):
        selected = os.environ.get("SEMWRIGHT_TEST_" + name.upper()) or shutil.which(name)
        if not selected:
            raise SystemExit("Missing selected native executable: " + name)
        path = Path(selected).resolve(strict=True)
        binaries[name] = file_identity(path)
        versions[name] = subprocess.check_output(
            [str(path), "-version"], text=True, timeout=20).splitlines()[0]
    rows = []
    for path in sorted(packages.rglob("*")):
        if path.is_symlink():
            raise SystemExit("Runtime inventory refuses a symbolic link")
        if path.is_file():
            identity = file_identity(path)
            rows.append((path.relative_to(root).as_posix(),
                         identity["bytes"], identity["sha256"]))
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    pin = json.loads((repo / "SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]["revision"]
    receipt = {
        "schema": "motionwright.manim.runtime-inventory/2",
        "context": {"source_sha": os.environ["GITHUB_SHA"], "semwright_sha": pin,
                    "run_id": os.environ["GITHUB_RUN_ID"],
                    "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]},
        "manim_version": manim.__version__, "pyav_version": av.__version__,
        "libav_versions": {name: list(version) for name, version in sorted(av.library_versions.items())},
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "runtime_receipt_sha256": file_identity(root / "runtime.json")["sha256"],
        "binaries": binaries, "binary_versions": versions,
        "pyav_wheel": wheel_info, "pyav_native_files": native_files,
        "files": len(rows), "inventory_sha256": hashlib.sha256(payload).hexdigest(),
        "native_render_performed": False,
    }
    destination = producer
    with (destination / "runtime-inventory.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    (destination / "runtime-inventory.json").chmod(0o600)
    graph_put_json(producer / "runtime-package-rows.original.json", rows)


def graph_runtime_inner(repo, runtime, producer):
    repo, root = graph_layout(repo); clock = GraphOriginalClock(root)
    admitted = graph_json((root / "tools/graph-phase-runtime.intent.original.json").read_bytes())
    clock.phase_name = "runtime"; clock.phase_end = admitted["observed_mono_ns"] + GRAPH_PHASES["runtime"] * 1000000000
    require(not runtime.exists(), "Graph fresh real runtime required")
    runtime.mkdir(mode=0o700); (runtime / "site-packages").mkdir(mode=0o700)
    provision = producer / "runtime-provision"; provision.mkdir(mode=0o700); (provision / "wheels").mkdir(mode=0o700)
    env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop(GRAPH_TOKEN_ENV, None)
    graph_producer_room(producer, 144 * GRAPH_BYTES + 65536, 6)
    graph_command(env, [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-cache-dir",
        "--no-deps", "--only-binary=:all:", "--dest", str(provision / "wheels"), "av==15.0.0"],
        repo, producer, "graph-runtime-download", 180, clock, maximum_file_bytes=128 * GRAPH_BYTES)
    wheels = list((provision / "wheels").glob("av-15.0.0-*.whl"))
    require(len(wheels) == 1, "Graph one original PyAV wheel")
    wheel = wheels[0]; original = graph_ref(wheel, 128 * GRAPH_BYTES)
    install, _ = graph_command(env, [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--no-compile", "--quiet",
        "--target", str(runtime / "site-packages"), "--report", "-",
        "manim==0.21.0", str(wheel)], repo, producer, "graph-runtime-install", 780, clock)
    graph_json(graph_read(install["stdout"], 8 * GRAPH_BYTES))
    graph_copy_original(Path(install["stdout"]["path"]), provision / "pip-install.json", producer)
    require(graph_ref(wheel) == original, "Graph PyAV wheel changed after installation")
    graph_put_json(provision / "pyav-wheel.json", {"filename": wheel.name, "bytes": original["size_bytes"], "sha256": original["sha256"]})
    graph_put_json(runtime / "runtime.json", {"schema": 2, "manim_version": "0.21.0", "pyav_version": "15.0.0",
        "source_color_policy": "srgb-rgba8-opaque-v1", "color_recipe": "srgb-to-rec709-full12-v2"})
    clock.admit("runtime-real-import-and-inventory", 60)
    graph_runtime_inventory(repo, runtime, producer)
    sys.path.insert(0, str(repo / "tooling"))
    import native_manim_graph_runtime_intake as intake
    require(Path(intake.__file__).resolve() == repo / "tooling/native_manim_graph_runtime_intake.py", "Graph existing intake origin")
    intake.prepare_graph_runtime(repo, runtime, producer / "graph-runtime-intake.original.json")
    print(json.dumps({"status": "SOURCE_INTAKE_ONLY", "native_render_performed": False, "profile_admitted": False}))
    return 0


def graph_source_paths(repo, targets, intake_rows):
    extra = ("Cargo.toml", "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json",
             "apps/studio/package.json", "crates/manim-profile/src/semantic_graph_source.rs",
             "apps/desktop/src-tauri/src/effect_grants.rs", "apps/desktop/src-tauri/src/effect_authority.rs",
             "crates/manim-profile/src/semantic_graph_helper_loader.py", "crates/manim-profile/src/semantic_graph_text_probe.py")
    return tuple(sorted(set((*GRAPH_AUTHORITY, *extra,
        *(Path(value["src_path"]).relative_to(repo).as_posix() for value in targets.values()),
        *(row["source_relative_path"] for row in intake_rows)))))


def graph_source_receipt(source_root, expected, names, before, producer, label):
    after = graph_source_observe(source_root, expected, names)
    require(before == after, "Graph source changed while producing originals")
    view = graph_source_view(before)
    retained = {name: graph_copy_original(source_root / name, producer / (label + "-source") / name, producer) for name in names}
    receipt = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "source_before": before, "source_after": after, "retained_files": retained}
    return graph_put_json(producer / (label + "-source-original.json"), receipt), view


def graph_produce(repo, recipient):
    repo, root = graph_layout(repo); clock = GraphOriginalClock(root)
    output, producer, runtime, sdk = root / "e", root / "e/graph-producer", root / "runtime", root / "sdk"
    for case, _, _ in GRAPH_CASES:
        graph_case_layout(output / "graph-cases", case)
    require(not output.exists() and not sdk.exists() and not runtime.exists(), "Graph once-only fresh production roots")
    output.mkdir(mode=0o700); producer.mkdir(mode=0o700); (producer / "consumption").mkdir(mode=0o700)
    source_sha = os.environ["GITHUB_SHA"]
    env = dict(os.environ, CARGO_INCREMENTAL="0", CARGO_PROFILE_DEV_DEBUG="0", CARGO_PROFILE_TEST_DEBUG="0",
        MOTIONWRIGHT_GRAPH_PRODUCER_RUN="1", SEMWRIGHT_CHECKOUT=str(sdk),
        SEMWRIGHT_TEST_MANIM_RUNTIME=str(runtime), SEMWRIGHT_TEST_PYTHON=str(Path(sys.executable).resolve()),
        PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop(GRAPH_TOKEN_ENV, None)
    cargo, node, npm = (shutil.which(name) for name in ("cargo", "node", "npm"))
    require(cargo and node and npm, "Graph real cargo/node/npm unavailable")
    cargo, node, npm = (str(Path(value).resolve()) for value in (cargo, node, npm))
    lock = graph_json((repo / "SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]
    require(lock["repository"] == "https://github.com/seradotcom/semwright"
            and re.fullmatch(r"[0-9a-f]{40}", lock["revision"]), "Graph source-owned immutable SDK pin")
    try:
        with clock.phase("prepare"):
            # Reuse the existing hash-pinned custody dependency list. The public
            # recipient must parse before a build/runtime can consume its phase.
            preparation_source = graph_source_observe(repo, source_sha, (
                "tooling/ci_private_evidence.py", "tooling/mlt_official_runtime_intake.py",
                "tooling/requirements/mlt-g2-custody.txt", "tooling/source_checkout_observation.py"))
            graph_put_json(producer / "custody-preparation-source.original.json", preparation_source)
            graph_command(env, [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                "--only-binary=:all:", "--require-hashes", "-r", str(repo / "tooling/requirements/mlt-g2-custody.txt")],
                repo, producer, "graph-custody-dependencies", 90, clock)
            sys.path.insert(0, str(repo / "tooling"))
            import ci_private_evidence as seal
            require(Path(seal.__file__).resolve() == repo / "tooling/ci_private_evidence.py", "Graph existing sealer origin")
            seal.public_key(recipient)
            # This is the same locked Studio/system preparation used by the
            # existing workflows. Every actual command now has owned originals.
            graph_command(env, [npm, "ci", "--no-audit", "--no-fund"], repo / "apps/studio", producer, "graph-npm", 300, clock)
            graph_command(env, [node, str(repo / "apps/studio/node_modules/@playwright/test/cli.js"), "install", "--with-deps", "chromium"],
                          repo / "apps/studio", producer, "graph-chromium", 300, clock)
            graph_command(env, ["sudo", "apt-get", "update"], repo, producer, "graph-apt-update", 60, clock)
            graph_command(env, ["sudo", "apt-get", "install", "--no-install-recommends", "-y", "bubblewrap", "ffmpeg", "fontconfig",
                "libcairo2-dev", "libpango1.0-dev", "pkg-config", "python3-pip", "python3-dev", "libpipewire-0.3-dev", "clang"],
                repo, producer, "graph-apt-install", 180, clock)
            sdk.mkdir(mode=0o700)
            for label, arguments, seconds in (
                ("graph-sdk-init", ["git", "init", str(sdk)], 20),
                ("graph-sdk-fetch", ["git", "-C", str(sdk), "fetch", "--no-tags", "--depth", "1", lock["repository"], lock["revision"]], 120),
                ("graph-sdk-checkout", ["git", "-C", str(sdk), "checkout", "--detach", "FETCH_HEAD"], 20)):
                graph_command(env, arguments, repo, producer, label, seconds, clock)
            sdk_preparation = graph_source_observe(sdk, lock["revision"], ("Cargo.lock", "Cargo.toml", "scripts/dev/ci-driver-bwrap-profile.sh"))
            graph_put_json(producer / "sdk-preparation-source.original.json", sdk_preparation)
            graph_command(env, ["bash", str(sdk / "scripts/dev/ci-driver-bwrap-profile.sh"), str(producer / "sandbox-profile.txt")],
                          repo, producer, "graph-sandbox-profile", 30, clock)
            metadata = {}
            for name, source_root in (("sdk", sdk), ("product", repo)):
                process, _ = graph_command(env, [cargo, "metadata", "--no-deps", "--locked", "--format-version", "1"], source_root,
                                           producer, "graph-" + name + "-metadata", 30, clock)
                metadata[name] = graph_json(graph_read(process["stdout"], 64 * GRAPH_BYTES))
            sdk_targets = graph_cargo_targets(metadata["sdk"], GRAPH_SDK_ROLES, sdk)
            product_targets = graph_cargo_targets(metadata["product"], tuple(role for role in GRAPH_RUST_ROLES if role not in GRAPH_SDK_ROLES), repo)
            sys.path.insert(0, str(repo / "tooling"))
            import native_manim_graph_runtime_intake as intake
            _, _, intake_rows = intake._inputs(repo)
            source_paths = graph_source_paths(repo, product_targets, intake_rows)
            sdk_paths = tuple(sorted(set(("Cargo.toml", "Cargo.lock", "scripts/dev/ci-driver-bwrap-profile.sh",
                *(Path(value["src_path"]).relative_to(sdk).as_posix() for value in sdk_targets.values()),
                *(Path(row["manifest_path"]).relative_to(sdk).as_posix() for row in metadata["sdk"]["packages"]
                  if row["name"] in {GRAPH_TARGETS[role][0] for role in GRAPH_SDK_ROLES})))))
            source_before = graph_source_observe(repo, source_sha, source_paths)
            sdk_before = graph_source_observe(sdk, lock["revision"], sdk_paths)
            require(all(sdk_before["files"][name] == value for name, value in sdk_preparation["files"].items()), "Graph SDK preparation source changed")
            require(all(source_before["files"][name] == value for name, value in preparation_source["files"].items()), "Graph custody preparation source changed")
        with clock.phase("runtime"):
            runtime_process, runtime_command = graph_command(env, [str(Path(sys.executable).resolve()), "-I", "-B",
                str(repo / "tooling/ci_canvas_service.py"), "graph-runtime", "--repo", str(repo), "--runtime", str(runtime), "--producer", str(producer)],
                repo, producer, "graph-runtime-producer", 1140, clock)
        sdk_bins, sdk_builds, sdk_contracts = graph_build_group(repo, sdk, producer, True, cargo, env, sdk_targets, clock)
        product_bins, product_builds, product_contracts = graph_build_group(repo, sdk, producer, False, cargo, env, product_targets, clock)
        with clock.phase("producer-index"):
            preflight_env = dict(env, CARGO_TARGET_DIR=str(root / "t"))
            graph_producer_room(producer, 18 * GRAPH_BYTES + 65536, 9)
            preflight = phase1_service47_sdk_preflight(repo, producer, Path(product_bins["ordinary_service"]["path"]), preflight_env,
                deadline=clock, maximum_file_bytes=GRAPH_BUDGET["store_file_bytes"], maximum_stream_bytes=GRAPH_BYTES)
            preflight_process = {name: graph_ref(producer / ("phase1-service-preflight." + suffix))
                                 for name, suffix in (("command", "command.json"), ("stdout", "stdout.log"), ("stderr", "stderr.log"))}
            source_original, source_view = graph_source_receipt(repo, source_sha, source_paths, source_before, producer, "product")
            sdk_original, sdk_view = graph_source_receipt(sdk, lock["revision"], sdk_paths, sdk_before, producer, "sdk")
            inventory = graph_json((producer / "runtime-inventory.json").read_bytes())
            runtime_intake = graph_json((producer / "graph-runtime-intake.original.json").read_bytes())
            binaries = {**sdk_bins, **product_bins, **{role: {"path": inventory["binaries"][name]["path"],
                "sha256": inventory["binaries"][name]["sha256"], "size_bytes": inventory["binaries"][name]["bytes"]}
                for role, name in (("python3", "python"), ("ffmpeg", "ffmpeg"))}}
            source = {"repository_root": str(repo), "sha": source_sha, "tree": source_view["tree"], "sdk_root": str(sdk),
                "sdk_sha": lock["revision"], "locks": {name: source_view["files"][name]["sha256"] for name in (
                    "Cargo.lock", "SOURCE_LOCK.json", "rust-toolchain.toml", "apps/studio/package-lock.json")},
                "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": 1}
            inventory_ref = graph_ref(producer / "runtime-inventory.json")
            intake_ref = graph_ref(producer / "graph-runtime-intake.original.json")
            profiles = [{"relative_path": row["runtime_relative_path"], "ref": graph_ref(runtime / row["runtime_relative_path"])} for row in runtime_intake["files"]]
            # The reviewer receives faithful copies of originals outside producer,
            # while live FileRefs keep the same original paths and inodes.
            review_copies = []
            outside = [clock.original_ref, graph_ref(runtime / "runtime.json"),
                       graph_ref(runtime / "graph-profile-v1/source-closure.json"), *(row["ref"] for row in profiles)]
            outside += [graph_ref(root / "tools" / name) for name in (
                "graph-run-response.original.json", "graph-run-response.transport.original.json",
                "graph-jobs-response.original.json", "graph-jobs-response.transport.original.json")]
            for number, original in enumerate(outside):
                copy_ref = graph_copy_original(Path(original["path"]), producer / "review-originals" / (str(number) + ".original"), producer)
                review_copies.append({"original": original, "retained": copy_ref})
            roster = {"cases": [{"case_id": case, "title": title, "callback_caps": list(caps)} for case, title, caps in GRAPH_CASES],
                "meaning": "SOURCE_DERIVED_DECLARATION_REQUIRES_INDEPENDENT_CODE_REVIEW"}
            native = {"source": source_view["files"]["crates/native/examples/native-graph-review-session.rs"],
                "source_path": "crates/native/examples/native-graph-review-session.rs",
                "meaning": "CUMULATIVE_PRE_EFFECT_ENFORCEMENT_REQUIRES_INDEPENDENT_CODE_REVIEW"}
            roster_ref = graph_put_json(producer / "callback-roster.original.json", roster)
            native_ref = graph_put_json(producer / "native-enforcement-source.original.json", native)
            job = next(row for row in clock.original["jobs"]["jobs"] if row["name"] == "Real SQLite and Studio / " + GRAPH_SCOPE)
            execution = {"scope": GRAPH_SCOPE, "job": "real-service", "job_id": job["id"], "deadline_original": clock.original_ref,
                "deadline_unix_ms": clock.original["deadline_unix_ms"], "producer_root": str(producer),
                "cases_root": str(output / "graph-cases"), "budget": GRAPH_BUDGET}
            contract = {"source_files": source_view["files"], "sdk_tree": sdk_view["tree"], "sdk_files": sdk_view["files"],
                "builds": {**sdk_contracts, **product_contracts},
                "ordinary_preflight": {key: preflight["command"][key] for key in ("argv", "cwd", "selected_environment")},
                "runtime": {"command": {key: runtime_command[key] for key in ("argv", "cwd", "selected_environment")},
                            "inventory": inventory, "intake": runtime_intake, "profile_files": profiles},
                "callback_roster": roster, "native_enforcement": native,
                "native_enforcing_source": source_view["files"]["crates/native/examples/native-graph-review-session.rs"],
                "cases": {case: {"title": title, "callback_caps": list(caps), "roster_source_files": list(GRAPH_CALLGRAPH)} for case, title, caps in GRAPH_CASES},
                "writer_sources": {role: ["tooling/ci_canvas_service.py", GRAPH_WORKFLOW] for role in (*GRAPH_RUST_ROLES, "source_observation", "sdk_observation", "ordinary_preflight", "runtime")},
                "execution": execution}
            index = {"schema": GRAPH_INDEX_SCHEMA, "source": source, "binaries": binaries,
                "runtime": {"root": str(runtime), "fontconfig_root": str(Path("/etc/fonts").resolve()),
                    "manifest": inventory_ref["path"], "manifest_sha256": inventory_ref["sha256"],
                    "intake_receipt": intake_ref["path"], "intake_receipt_sha256": intake_ref["sha256"]},
                "source_original": source_original, "sdk_original": sdk_original, "builds": {**sdk_builds, **product_builds},
                "ordinary_preflight": {"process": preflight_process, "stdin": graph_ref(producer / "phase1-service-preflight.stdin.original")},
                "runtime_originals": {"producer": runtime_process, "inventory": inventory_ref, "receipt": graph_ref(runtime / "runtime.json"),
                    "intake": intake_ref, "profile_files": profiles}, "callback_roster": roster_ref, "native_enforcement": native_ref,
                "contract": contract}
            encoded = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
            require(len(encoded) <= GRAPH_BYTES, "Graph exact private index bound")
            index_ref = graph_put(producer / "producer-index.original.json", encoded)
            # Acyclic originals validation precedes the external decision. It is
            # not an approval and cannot launch an owner or a Service case.
            graph_verify_producer_index(index_ref, GRAPH_CASES[0][0])
            graph_runtime_unchanged(index, producer)
            graph_put_json(producer / "review-request.original.json", {"index": index_ref,
                "source": source, "execution": execution, "outside_original_copies": review_copies,
                "contexts": ["mw-owner/" + GRAPH_SCOPE + "/" + source["run_id"] + "/1/" + row[0] for row in GRAPH_CASES],
                "meaning": "REQUIRES_INDEPENDENT_REVIEW_AND_EXTERNAL_STATUS_NO_NATIVE_CASE_EXECUTED"})
            graph_tree_allocation(producer, GRAPH_BUDGET["producer_bytes"], GRAPH_BUDGET["producer_members"])
            plaintext = seal.gather_snapshot({"producer": producer})
            sealed = seal.encrypt_snapshot(plaintext, recipient, {"source_sha": source_sha, "semwright_sha": source["sdk_sha"],
                "run_id": source["run_id"], "run_attempt": "1"})
            require(len(sealed) <= seal.MAX_FILE, "Graph existing encrypted carrier file bound; no splitting or enlarged sealer")
            # Carrier is outside the original e/tools tree. Originals remain in
            # their actual paths/inodes and are never replaced by a download.
            review = root / "graph-review"; review.mkdir(mode=0o700)
            seal.new_bytes(review / "producer-originals.mwseal", sealed)
            envelope_ref = graph_ref(review / "producer-originals.mwseal")
            graph_put_json(review / "carrier.original.json", {"index_sha256": index_ref["sha256"],
                "source_sha": source_sha, "semwright_sha": source["sdk_sha"], "run_id": source["run_id"], "run_attempt": 1,
                "envelope": {"filename": "producer-originals.mwseal", "sha256": envelope_ref["sha256"], "size_bytes": envelope_ref["size_bytes"]},
                "recipient_spki_sha256": recipient["sha256"],
                "meaning": "ENCRYPTED_REVIEW_CARRIER_ONLY_NO_OWNER_ADMISSION"})
            graph_put_json(output / "source-before.json", source_view)
        print(json.dumps({"status": "AWAITING_EXTERNAL_R74_REVIEW", "index_sha256": index_ref["sha256"],
                          "source": source_sha, "native_cases_executed": 0}))
        return 0
    except BaseException as error:
        graph_put_json(output / "receipt.json", {"schema": "motionwright.canvas-service-ci/1", "status": "UNKNOWN" if clock.unknown else "DENY",
            "selection_plan": {"requested": GRAPH_SCOPE}, "error": type(error).__name__ + ": " + str(error),
            "native_cases_executed": 0, "automatic_retry": False, "formal_acceptance": False})
        raise


def graph_runtime_unchanged(index, producer):
    runtime = Path(index["runtime"]["root"]); packages = runtime / "site-packages"
    rows = []
    for path in sorted(packages.rglob("*")):
        require(not path.is_symlink(), "Graph runtime link appeared")
        if path.is_file():
            ref = graph_ref(path)
            rows.append([path.relative_to(runtime).as_posix(), ref["size_bytes"], ref["sha256"]])
    baseline = graph_json((producer / "runtime-package-rows.original.json").read_bytes())
    require(rows == baseline and len(rows) == index["contract"]["runtime"]["inventory"]["files"]
            and hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
                == index["contract"]["runtime"]["inventory"]["inventory_sha256"], "Graph complete shared runtime changed")
    return {"files": len(rows), "inventory_sha256": index["contract"]["runtime"]["inventory"]["inventory_sha256"],
            "native_render_performed_by_this_check": False}


def graph_wait_for_external_review(repo):
    repo, root = graph_layout(repo); clock = GraphOriginalClock(root)
    producer = root / "e/graph-producer"; index_ref = graph_ref(producer / "producer-index.original.json", GRAPH_BYTES)
    index = graph_json(graph_read(index_ref, GRAPH_BYTES))
    token = os.environ.pop(GRAPH_TOKEN_ENV, "")
    require(token, "Graph readonly status credential absent")
    wait_root = root / "tools/graph-approval-observations"; wait_root.mkdir(mode=0o700)
    with clock.phase("approval-wait"):
        try:
            for attempt in range(40):
                clock.admit("read-only-status-observation", 15)
                def publish(name, raw):
                    observed = graph_tree_allocation(root / "tools", GRAPH_BUDGET["shared_bytes"], GRAPH_BUDGET["shared_members"])
                    require(observed["bytes"] + len(raw) + 8 * GRAPH_BYTES <= GRAPH_BUDGET["shared_bytes"]
                            and observed["members"] + 64 <= GRAPH_BUDGET["shared_members"], "Graph shared approval-original capacity")
                    return graph_put(wait_root / name, raw)
                run, rows, observed = graph_api_observation(index, index_ref, token, publish, "poll-" + str(attempt), time.monotonic() + 12)
                accepted = {}
                for case, _, _ in GRAPH_CASES:
                    context = "mw-owner/" + GRAPH_SCOPE + "/" + index["source"]["run_id"] + "/1/" + case
                    newest = next((row for row in rows if type(row.get("context")) is str and row["context"].lower() == context.lower()), None)
                    if newest is None:
                        continue
                    require(newest.get("creator", {}).get("type") == "User"
                            and newest["creator"].get("id") == run["actor"]["id"], "Graph newest pending status is foreign")
                    if newest.get("state") == "pending":
                        # Pending is a read-only wait, never an owner admission.
                        continue
                    accepted[case] = graph_status_core(run, rows, {"source_sha": index["source"]["sha"],
                        "run_id": index["source"]["run_id"], "case_id": case, "index_sha256": index_ref["sha256"]})
                if len(accepted) == 6:
                    graph_verify_producer_index(index_ref, GRAPH_CASES[0][0])
                    graph_runtime_unchanged(index, producer)
                    graph_put_json(root / "e/r74-wait-complete.original.json", {"index": index_ref, "statuses": accepted,
                        "observation": observed, "meaning": "EXTERNAL_OWNER_ADMISSION_OBSERVED_CASE_CONSUMERS_MUST_REVALIDATE",
                        "native_cases_executed": 0})
                    return 0
                # Polling ends before any product effect. Each actual response is
                # an original. No failed HTTP observation is retried.
                clock.admit("read-only-review-wait", 30)
                time.sleep(30)
            raise ValueError("Graph external review did not arrive inside its original 20-minute phase")
        except BaseException as error:
            graph_put_json(root / "e/receipt.json", {"schema": "motionwright.canvas-service-ci/1", "status": "UNKNOWN" if clock.unknown else "DENY",
                "selection_plan": {"requested": GRAPH_SCOPE}, "error": type(error).__name__ + ": " + str(error),
                "native_cases_executed": 0, "automatic_retry": False, "formal_acceptance": False})
            raise
        finally:
            token = None


def graph_report(document, *, listing, expected=None):
    filename = "semantic-graph-service.spec.ts"; titles = tuple(row[1] for row in GRAPH_CASES)
    require(document.get("errors") == [], "Graph original report errors")
    config, stats = document.get("config", {}), document.get("stats", {})
    projects = config.get("projects", [])
    require(config.get("workers") == 1 and config.get("forbidOnly") is True and config.get("maxFailures") == 1 and len(projects) == 1
            and projects[0].get("id") == projects[0].get("name") == "chromium"
            and projects[0].get("repeatEach") == 1 and projects[0].get("retries") == 0, "Graph original serial/no-retry reporter config")
    specs = []
    def visit(suites):
        require(type(suites) is list, "Graph reporter suites")
        for suite in suites:
            specs.extend(suite.get("specs", [])); visit(suite.get("suites", []))
    visit(document.get("suites"))
    require(len(specs) == 6 and {row.get("title") for row in specs} == set(titles), "Graph original six titles")
    identities = []
    for row in specs:
        tests = row.get("tests", [])
        require(row.get("file") == filename and type(row.get("id")) is str and row["id"] and len(tests) == 1,
                "Graph original source/test identity")
        case = tests[0]
        require(case.get("expectedStatus") == "passed" and case.get("annotations", []) == []
                and case.get("timeout") == 120000 and case.get("projectId") == "chromium", "Graph caller timeout/skip changed")
        if listing:
            require(case.get("results") == [], "Graph discovery performed an execution")
        identities.append({key: row[key] for key in ("id", "file", "title", "line", "column")})
    identities.sort(key=lambda row: row["title"])
    require(len({row["id"] for row in identities}) == 6, "Graph duplicate caller IDs")
    if listing:
        require(stats.get("expected") == stats.get("unexpected") == stats.get("flaky") == 0 and stats.get("skipped") == 6,
                "Graph discovery counters")
        return {"status": "LISTED_NOT_EXECUTED", "identities": identities, "case_timeout_ms": 120000, "selected": 6}
    verified = validate_browser_execution(document, filename, titles)
    require(expected and expected.get("identities") == identities and expected.get("selected") == 6,
            "Graph execution does not match actual discovery")
    return {**verified, "identities": identities, "original_discovery_matched": True}


def graph_validate_allocation_union(observations, caps):
    """Check the one existing pool from its original immutable reservations."""
    by_sequence, by_id = {}, {}
    for row in observations:
        require(type(row) is dict and set(row) == {"id", "kind", "bytes", "files", "sequence"}
                and type(row["sequence"]) is int and row["sequence"] >= 0
                and type(row["bytes"]) is int and row["bytes"] > 0
                and type(row["files"]) is int and row["files"] > 0, "Graph original pool reservation shape")
        require(row["sequence"] not in by_sequence or by_sequence[row["sequence"]] == row,
                "Graph original pool sequence replaced")
        require(row["id"] not in by_id or by_id[row["id"]] == row, "Graph original pool ID replaced")
        by_sequence[row["sequence"]] = row; by_id[row["id"]] = row
    require(set(by_sequence) == set(range(len(by_sequence))), "Graph original pool missing sequence")
    require(sum(row["bytes"] for row in by_id.values()) <= 512 * GRAPH_BYTES
            and sum(row["files"] for row in by_id.values()) <= 4096, "Graph original pool aggregate exhausted")
    expected = {
        "aggregate-unavailable": ("aggregate-holdback", 252 * GRAPH_BYTES, 1096),
        "directory-members": ("archive-directory-capacity", 1, 256),
        "original-sqlite-files": ("kernel-bounded-original-store", 16 * GRAPH_BYTES, 4),
        "parent-metadata": ("metadata", 4 * GRAPH_BYTES, 128),
        "parent-close-and-errors": ("close", 4 * GRAPH_BYTES, 64),
        "service-evidence-metadata": ("evidence", 16 * GRAPH_BYTES, 128),
        "python-owner": ("external-owner", GRAPH_BUDGET["owner_bytes"], GRAPH_BUDGET["owner_files"]),
        "python-owner-raw": ("owner-raw", 2 * GRAPH_BYTES, 2),
        "case-consumption": ("immutable-case-consumption", 8192, 1),
        "sdk-ticket-prewrite": ("sdk-ticket-prewrite", (1 + len(caps) + 8 * sum(caps)) * 256,
                                 (1 + len(caps) + 8 * sum(caps)) * 3),
    }
    for name, values in expected.items():
        require(name in by_id and tuple(by_id[name][key] for key in ("kind", "bytes", "files")) == values,
                "Graph source-bound reservation missing or changed: " + name)
    journals = [row for row in by_id.values() if row["kind"] == "native-journal"]
    raws = [row for row in by_id.values() if row["kind"] == "service-raw"]
    copies = [row for row in by_id.values() if row["kind"] == "archive-copy"]
    reopens = [row for row in by_id.values() if row["kind"] == "post-seal-originals"]
    require(len(journals) == len(caps) and all(row["bytes"] == GRAPH_BUDGET["journal_bytes"]
            and row["files"] == GRAPH_BUDGET["journal_files"] for row in journals), "Graph journal precharge roster")
    require(len(raws) == len(caps) + 1 and all(row["bytes"] == 2 * GRAPH_BYTES and row["files"] == 2 for row in raws),
            "Graph Service raw precharge roster")
    require(len(copies) == 1 and copies[0]["bytes"] <= 16 * GRAPH_BYTES and copies[0]["files"] <= 4,
            "Graph original Store copy reservation")
    require(len(reopens) == 1 and reopens[0]["id"] == "independent-same-store-reopen"
            and reopens[0]["bytes"] <= 20 * GRAPH_BYTES + 768 * 1024 and reopens[0]["files"] <= 268,
            "Graph independent reopen reservation")
    require(len(by_id) == len(expected) + len(journals) + len(raws) + 2, "Graph undeclared pool reservation")
    return {"bytes": sum(row["bytes"] for row in by_id.values()) - 252 * GRAPH_BYTES,
            "members": sum(row["files"] for row in by_id.values()) - 1096, "reservations": len(by_id)}


def graph_archive_bindings(root, inventory, source):
    """Existing whole-evidence collector's Graph branch, after all reporters."""
    receipt = archive_json(root, inventory, "e/receipt.json")
    require(receipt.get("status") == "CLOSED_GRAPH_OBSERVATIONS" and receipt.get("formal_acceptance") is False
            and receipt.get("selection_plan") == {"requested": GRAPH_SCOPE, "required_tests": 6, "workers": 1, "retries": 0}
            and receipt.get("source_before") == receipt.get("source_after") == source, "Graph whole-run source/closure")
    index_ref = receipt["producer_index"]
    index = graph_json(graph_read(index_ref, GRAPH_BYTES))
    require(index["source"]["sha"] == os.environ.get("GITHUB_SHA") and index["source"]["run_id"] == os.environ.get("GITHUB_RUN_ID")
            and str(index["source"]["run_attempt"]) == os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "Graph final same-run/attempt/source")
    def retained(ref, maximum=512 * GRAPH_BYTES):
        path = Path(ref["path"])
        require(path.is_relative_to(root), "Graph final original escaped retained tree")
        name = path.relative_to(root).as_posix(); row = inventory["entries"].get(name, {})
        require(row.get("kind") == "file" and row.get("sha256") == ref["sha256"] and row.get("bytes") == ref["size_bytes"], "Graph final retained original differs")
        return graph_read(ref, maximum)
    retained(index_ref, GRAPH_BYTES)
    for name in ("discovery", "execution"):
        observation = receipt[name]
        command = graph_json(retained(observation["process"]["command"], GRAPH_BYTES))
        require_observed_command(command); require(command.get("exit_code") == 0 and command.get("cleanup_errors") == [], "Graph final reporter process")
        expected_argv = [str(Path(shutil.which("node")).resolve()), str(Path(index["source"]["repository_root"]) / "apps/studio/node_modules/@playwright/test/cli.js"),
            "test", "semantic-graph-service.spec.ts", "--project=chromium", "--workers=1", "--retries=0", "--repeat-each=1",
            "--max-failures=1", "--forbid-only", "--reporter=json", "--output=" + str(root / "e/graph-cases")]
        require(command["argv"] == expected_argv + (["--list"] if name == "discovery" else [])
                and command["cwd"] == str(Path(index["source"]["repository_root"]) / "apps/studio")
                and command["selected_environment"]["PLAYWRIGHT_JSON_OUTPUT_FILE"] == str(root / ("e/graph-discovery.json" if name == "discovery" else "e/graph-execution.json"))
                and command["selected_environment"]["MOTIONWRIGHT_SERVICE_TEST_BIN"] == index["binaries"]["ordinary_service"]["path"],
                "Graph final reporter original argv/source/selection")
        for stream in ("stdout", "stderr"):
            raw = retained(observation["process"][stream], 64 * GRAPH_BYTES)
            require(command[stream] == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, "Graph original reporter raw differs")
        document = graph_json(retained(observation["report"], 64 * GRAPH_BYTES))
        verified = graph_report(document, listing=name == "discovery", expected=receipt["discovery"]["verified"] if name == "execution" else None)
        require(verified == observation["verified"], "Graph late original reporter changed")
    cases_root = Path(index["contract"]["execution"]["cases_root"])
    require(cases_root == root / "e/graph-cases", "Graph final cases root differs from original index")
    case_roots = graph_collected_case_roots(cases_root)
    cases, allocated_bytes, allocated_members = [], 0, 0
    for admitted_case, case_root in case_roots:
        state = graph_tree_allocation(case_root, GRAPH_BUDGET["case_bytes"], GRAPH_BUDGET["case_members"])
        require(state["directories"] <= 256, "Graph directory precharge exhausted")
        allocated_bytes += state["bytes"]; allocated_members += state["members"]
        parent_ref = graph_ref(case_root / "parent-source-binding.original.json", 4 * GRAPH_BYTES)
        parent = graph_json(retained(parent_ref, 4 * GRAPH_BYTES))
        require(parent["source"] == index["source"] and parent["binaries"] == index["binaries"] and parent["runtime"] == index["runtime"]
                and parent["producer_originals"]["index"] == index_ref and parent["execution"] == index["contract"]["execution"]
                and parent["approval"]["context"] == "mw-owner/" + GRAPH_SCOPE + "/" + index["source"]["run_id"] + "/1/" + admitted_case,
                "Graph case exact original admission")
        final_ref = graph_ref(case_root / "owner/owner-final.original.json", 4 * GRAPH_BYTES)
        final = graph_json(retained(final_ref, 4 * GRAPH_BYTES)); case = final["case_id"]
        require(case == admitted_case and case not in {row["case_id"] for row in cases}
                and final["schema"] == "motionwright.graph-browser-owner-final/6" and final["status"] == "CLOSED"
                and final["source"] == index["source"] and final["errors"] == []
                and final["native_authoring_approved"] is final["formal_acceptance"] is False
                and all(final[key] is True for key in ("service_closure_validated", "same_physical_database", "owner_cohort_absent", "owner_streams_eof", "capture_permitted")), "Graph final original owner incomplete")
        owner_independent = graph_json(retained(graph_ref(case_root / "owner/producer-originals-independent.original.json")))
        require(owner_independent["index"] == index_ref and owner_independent["approval"]["status_id"] == parent["approval"]["status_id"]
                and set(owner_independent["actual_rust_build_roles"]) == set(GRAPH_RUST_ROLES)
                and set(owner_independent["actual_runtime_roles"]) == set(GRAPH_ROLES)
                and owner_independent["binary_source_causality_validated"] is True, "Graph independent owner original missing")
        consumed = graph_json(retained(graph_ref(Path(index["contract"]["execution"]["producer_root"]) / "consumption" / (case + ".original.json"))))
        require(consumed["case_id"] == case and consumed["private_root"] == str(case_root) and consumed["index"] == index_ref
                and consumed["status_id"] == parent["approval"]["status_id"], "Graph consumed case/root differs")
        for name in ("spec_original", "source_inputs", "owner_close", "database_initial", "database_final", "service_closure"):
            retained(final[name], 4 * GRAPH_BYTES)
        inputs = graph_json(retained(final["source_inputs"], 4 * GRAPH_BYTES))
        require(inputs["binary_source_causality_validated_by_this_helper"] is True, "Graph actual producer causality absent")
        owner_close = graph_json(retained(final["owner_close"], 4 * GRAPH_BYTES))
        cohort = owner_close.get("cohort", {})
        require(owner_close.get("status") == "CLOSED" and owner_close.get("lifecycle", {}).get("status") == "CLOSED"
                and cohort.get("status") == "PASS" and cohort.get("subreaper") is True
                and cohort.get("complete_empty_descendant_tree", {}).get("root_observed") is True
                and cohort["complete_empty_descendant_tree"].get("truncated") is False
                and len(cohort["complete_empty_descendant_tree"].get("processes", [])) == 1,
                "Graph complete owner descendant closure missing")
        terminal = graph_json(retained(graph_ref(case_root / "owner-parent-terminal.original.json"), 4 * GRAPH_BYTES))
        require(terminal["exit"]["code"] == 0 and terminal["exit"]["signal"] is None, "Graph owner actual process exit")
        for raw in terminal["raw"].values():
            require(raw["eof"] is True, "Graph owner EOF absent")
            retained({key: raw[key] for key in ("path", "sha256", "size_bytes")}, GRAPH_BYTES)
        expected_caps = index["contract"]["cases"][case]["callback_caps"]
        tickets = graph_json(retained(graph_ref(case_root / "sdk-ticket-roster-binding.original.json"), GRAPH_BYTES))
        require(tickets["failed"] is False and tickets["bootstrap_attempted"] is True
                and tickets["attempted_clients"] == tickets["confirmed_clients"] == len(expected_caps)
                and [row["callback_cap"] for row in tickets["bindings"]] == expected_caps
                and len(final["client_roster"]) == len(expected_caps), "Graph complete original SDK ticket roster")
        for client, ticket in zip(final["client_roster"], tickets["bindings"]):
            require(client["status"] == "READY" and client["id"] == ticket["id"] and ticket["confirmed"] is True
                    and ticket["attempted"] is True and ticket["request_issued"] is True, "Graph Native client tuple")
            retained(client["connection_original"], GRAPH_BYTES); retained(client["session_original"], GRAPH_BYTES)
            graph_tree_allocation(Path(client["connection_original"]["path"] + ".graph-originals"), GRAPH_BUDGET["journal_bytes"], GRAPH_BUDGET["journal_files"])
        reopen_paths = list(case_root.glob("*/terminal.original.json"))
        reopens = [(path, graph_json(path.read_bytes())) for path in reopen_paths if graph_json(path.read_bytes()).get("schema") == "motionwright.graph.same-original-store-reopen/1"]
        require(len(reopens) == 1, "Graph once-only original independent reopen")
        reopen_path, reopen = reopens[0]
        retained(graph_ref(reopen_path), 4 * GRAPH_BYTES)
        require(reopen.get("status") == "CLOSED" and reopen.get("source") == index["source"]
                and reopen.get("original_owner_final") == final_ref and reopen.get("synthetic_adapter_only") is False
                and reopen.get("service_process", {}).get("code") == 0 and reopen["service_process"].get("signal") is None
                and reopen.get("physical_store_before") == reopen.get("physical_store_after")
                and all(reopen.get(key) is True for key in ("exact_project_equal", "exact_journal_equal", "original_seal_preserved", "owned_process_group_absent",
                    "actual_child_opened_descriptor_observed", "same_spawn_process_incarnation_observed", "held_store_digest_matches_initializer_physical_identity",
                    "original_preseal_held_pager_witness_bound")) and reopen.get("formal_acceptance") is False and reopen.get("gate_credit") is False,
                "Graph original reopen witness/EOF/identity join incomplete")
        for name in ("expected_oracle", "observed_oracle", "original_preseal_held_pager_witness", "original_preseal_response_raw", "original_service_closure"):
            ref = reopen[name]
            retained({key: ref[key] for key in ("path", "sha256", "size_bytes")}, 16 * GRAPH_BYTES)
        for ref in reopen["opened_descriptor_observations"]:
            retained(ref, GRAPH_BYTES)
        for raw in reopen["raw"].values():
            require(raw["eof"] is True, "Graph independent reopen EOF missing")
            retained({key: raw[key] for key in ("path", "sha256", "size_bytes")}, GRAPH_BYTES)
        manifests = list((case_root / "artifacts").rglob("manifest.json"))
        captures = []
        for manifest_path in manifests:
            manifest = graph_json(retained(graph_ref(manifest_path), 16 * GRAPH_BYTES))
            if "snapshotDirectory" not in manifest:
                continue
            entries = manifest["entries"]
            aggregate = hashlib.sha256(json.dumps(entries, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            require(manifest["sourceBeforeSha256"] == manifest["sourceAfterSha256"] == manifest["snapshotSha256"] == aggregate
                    and manifest["databaseRelativePath"] == "motionwright.sqlite3"
                    and Path(manifest["snapshotDirectory"]) == manifest_path.parent / "store", "Graph original Store copy manifest")
            require(graph_tree_allocation(Path(manifest["snapshotDirectory"]), 16 * GRAPH_BYTES, 5)["bytes"] <= 16 * GRAPH_BYTES,
                    "Graph original Store copy allocation")
            actual = []
            snapshot = Path(manifest["snapshotDirectory"])
            for file in [snapshot, *sorted(snapshot.rglob("*"))]:
                relative = file.relative_to(snapshot).as_posix(); relative = "" if relative == "." else relative
                row = {"path": relative, "kind": "directory" if file.is_dir() else "file"}
                if file.is_file():
                    ref = graph_ref(file); retained(ref)
                    row.update(sizeBytes=ref["size_bytes"], sha256=ref["sha256"])
                actual.append(row)
            require(sorted(actual, key=lambda row: row["path"]) == sorted(entries, key=lambda row: row["path"]), "Graph complete Store copy entries differ")
            captures.append(graph_ref(manifest_path))
        require(len(captures) == 1, "Graph exactly one retained closed original Store")
        pool_rows = []
        for path in sorted(case_root.glob("reservation-*.original.json")):
            pool_rows.extend(graph_json(retained(graph_ref(path), 4 * GRAPH_BYTES))["allocations"])
        for path in sorted(case_root.glob("copy-*.intent.original.json")):
            pool_rows.append(graph_json(retained(graph_ref(path), 4 * GRAPH_BYTES))["allocation"])
        pool_rows.append(reopen["allocation"])
        pool = graph_validate_allocation_union(pool_rows, expected_caps)
        cases.append({"case_id": case, "root": str(case_root), "owner": final_ref, "reopen": graph_ref(reopen_path),
                      "capture": captures[0], "allocation": state, "original_pool": pool, "status_id": parent["approval"]["status_id"]})
    require({row["case_id"] for row in cases} == {row[0] for row in GRAPH_CASES}, "Graph final six-case bijection")
    producer_state = graph_tree_allocation(root / "e/graph-producer", GRAPH_BUDGET["producer_bytes"], GRAPH_BUDGET["producer_members"])
    shared_members = len(inventory["entries"]) - allocated_members - producer_state["members"]
    shared_bytes = sum(row.get("bytes", 0) for row in inventory["entries"].values()) - allocated_bytes - producer_state["bytes"]
    require(0 <= shared_members <= GRAPH_BUDGET["shared_members"] and 0 <= shared_bytes <= GRAPH_BUDGET["shared_bytes"], "Graph complete shared allocation")
    runtime_after = graph_runtime_unchanged(index, root / "e/graph-producer")
    require(receipt["runtime_after"] == runtime_after and receipt["runtime_before"] == runtime_after, "Graph complete runtime before/after")
    return {"meaning": "CLOSED_ORIGINAL_GRAPH_OBSERVATIONS_NOT_G2_OR_A5", "producer_index": index_ref, "cases": cases,
            "producer": producer_state, "shared": {"members": shared_members, "bytes": shared_bytes}, "formal_acceptance": False}


def graph_run_cases(repo):
    repo, root = graph_layout(repo); clock = GraphOriginalClock(root)
    output, producer = root / "e", root / "e/graph-producer"
    index_ref = graph_ref(producer / "producer-index.original.json", GRAPH_BYTES)
    index = graph_json(graph_read(index_ref, GRAPH_BYTES))
    require((output / "r74-wait-complete.original.json").is_file() and not (output / "receipt.json").exists(), "Graph real review wait absent or prior attempt")
    verified = graph_verify_producer_index(index_ref, GRAPH_CASES[0][0])
    token = os.environ.get(GRAPH_TOKEN_ENV, ""); require(token, "Graph case independent metadata credential absent")
    env = dict(os.environ, MOTIONWRIGHT_GRAPH_NATIVE_CASE="all", MOTIONWRIGHT_GRAPH_PRODUCER_ORIGINAL=index_ref["path"],
        MOTIONWRIGHT_GRAPH_PRODUCER_RUN="1", MOTIONWRIGHT_SERVICE_TEST_BIN=index["binaries"]["ordinary_service"]["path"],
        MOTIONWRIGHT_SERVICE_TEST_TMPDIR=str(root / "x"), SEMWRIGHT_CHECKOUT=index["source"]["sdk_root"],
        SEMWRIGHT_TEST_MANIM_RUNTIME=index["runtime"]["root"], SEMWRIGHT_TEST_PYTHON=index["binaries"]["python3"]["path"],
        SEMWRIGHT_TEST_FFMPEG=index["binaries"]["ffmpeg"]["path"], PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    node = str(Path(shutil.which("node")).resolve()); cli = str(repo / "apps/studio/node_modules/@playwright/test/cli.js")
    argv = [node, cli, "test", "semantic-graph-service.spec.ts", "--project=chromium", "--workers=1", "--retries=0",
            "--repeat-each=1", "--max-failures=1", "--forbid-only", "--reporter=json", "--output=" + str(output / "graph-cases")]
    report = {"schema": "motionwright.canvas-service-ci/1", "status": "DENY", "formal_acceptance": False,
              "selection_plan": {"requested": GRAPH_SCOPE, "required_tests": 6, "workers": 1, "retries": 0},
              "run_id": index["source"]["run_id"], "run_attempt": "1", "job": "real-service", "producer_index": index_ref,
              "source_before": graph_source_view(graph_json(graph_read(index["source_original"], 8 * GRAPH_BYTES))["source_before"]),
              "native_render": "PER_CASE_ORIGINALS_REQUIRE_A5_ASSESSMENT", "installed_tauri": "NOT_RUN", "human_acceptance": "NOT_RUN"}
    try:
        with clock.phase("discovery"):
            discovery_env = dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(output / "graph-discovery.json")); discovery_env.pop(GRAPH_TOKEN_ENV, None)
            command = execute([*argv, "--list"], repo / "apps/studio", discovery_env, output, "graph-discovery", 45,
                              deadline=clock, maximum_stream_bytes=GRAPH_BYTES)
            require_observed_command(command); require(command["exit_code"] == 0, "Graph actual discovery failed")
            discovery = graph_report(graph_json((output / "graph-discovery.json").read_bytes()), listing=True)
            report["discovery"] = {"process": {name: graph_ref(output / ("graph-discovery." + suffix)) for name, suffix in (
                ("command", "command.json"), ("stdout", "stdout.log"), ("stderr", "stderr.log"))},
                "report": graph_ref(output / "graph-discovery.json"), "verified": discovery}
        with clock.phase("cases"):
            report["runtime_before"] = graph_runtime_unchanged(index, producer)
            case_env = dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=str(output / "graph-execution.json"))
            # Kernel file-size limits are installed before Playwright and inherited
            # by its actual Service/owner/Native children. No launcher substitute.
            command = execute(argv, repo / "apps/studio", case_env, output, "graph-cases", 750, deadline=clock,
                              maximum_file_bytes=GRAPH_BUDGET["store_file_bytes"], maximum_stream_bytes=GRAPH_BYTES)
            require_observed_command(command); require(command["exit_code"] == 0, "Graph actual six-case process failed")
            execution = graph_report(graph_json((output / "graph-execution.json").read_bytes()), listing=False, expected=discovery)
            report["execution"] = {"process": {name: graph_ref(output / ("graph-cases." + suffix)) for name, suffix in (
                ("command", "command.json"), ("stdout", "stdout.log"), ("stderr", "stderr.log"))},
                "report": graph_ref(output / "graph-execution.json"), "verified": execution}
        with clock.phase("closure"):
            for case, _, _ in GRAPH_CASES:
                graph_verify_producer_index(index_ref, case)
            report["runtime_after"] = graph_runtime_unchanged(index, producer)
            source_original = graph_json(graph_read(index["source_original"], 8 * GRAPH_BYTES))
            report["source_after"] = graph_source_view(graph_source_observe(repo, index["source"]["sha"], tuple(index["contract"]["source_files"])))
            require(report["source_after"] == report["source_before"], "Graph final exact source changed")
            sdk_after = graph_source_observe(Path(index["source"]["sdk_root"]), index["source"]["sdk_sha"], tuple(index["contract"]["sdk_files"]))
            require(graph_source_view(sdk_after)["tree"] == index["contract"]["sdk_tree"], "Graph final SDK tree changed")
            report["status"] = "CLOSED_GRAPH_OBSERVATIONS"
            report["graph_deadline"] = {"original": clock.original_ref, "finished_mono_ns": time.monotonic_ns(),
                "deadline_mono_ns": clock.deadline_ns, "unknown": False, "retry": "FORBIDDEN"}
    except UnknownObservation as error:
        report.update(status="UNKNOWN", error=str(error), unsealed_command=error.command)
    except BaseException as error:
        report.update(status="UNKNOWN" if clock.unknown else "DENY", error=type(error).__name__ + ": " + str(error))
    graph_put_json(output / "receipt.json", report)
    print(json.dumps({"status": report["status"], "receipt": str(output / "receipt.json"), "formal_acceptance": False}))
    return 0 if report["status"] == "CLOSED_GRAPH_OBSERVATIONS" else 1


def graph_public_upload_closure(root, clock, token):
    """Select only this public fixture's closed originals; UNKNOWN stays private.

    Secret scanning is supplementary. Local grant scope comes from the reviewed
    source, original SDK connection/manifest and complete actual process closure.
    Effect grants must additionally expire under the source-owned 45-second TTL.
    """
    receipt = graph_json((root / "e/receipt.json").read_bytes())
    require(receipt.get("status") == "CLOSED_GRAPH_OBSERVATIONS" and receipt.get("formal_acceptance") is False,
            "Graph public upload HOLD: original fixture or credential closure is UNKNOWN")
    inventory = archive_inventory(root)
    proof = graph_archive_bindings(root, inventory, receipt["source_before"])
    retained = root / "retained"
    require(retained.is_dir() and {path.name for path in retained.iterdir()} == {
        "closed-evidence.tar", "input-inventory.json", "archive-receipt.json", "graph-phase-archive.terminal.original.json"},
        "Graph public upload HOLD: final retained selection is not closed")
    archive = graph_json((retained / "archive-receipt.json").read_bytes())
    require(archive.get("status") == "PASS" and archive.get("custody") == "VERIFIED_ORIGINAL_TREE_COPY"
            and archive.get("formal_acceptance") is False and archive.get("originals_equal_before_after") is True
            and archive_file_digest(retained / "closed-evidence.tar", ARCHIVE_LIMITS["total_bytes"] + ARCHIVE_LIMITS["members"] * 4096 + 11264)
                == archive["verified"]["archive_digest"], "Graph public upload HOLD: archive original changed or is unsealed")
    index = graph_json(graph_read(proof["producer_index"], GRAPH_BYTES))
    grant_name = "apps/desktop/src-tauri/src/effect_grants.rs"
    grant_identity = index["contract"]["source_files"][grant_name]
    grant_raw = graph_read({"path": str(Path(index["source"]["repository_root"]) / grant_name),
        "sha256": grant_identity["sha256"], "size_bytes": grant_identity["bytes"]})
    # The whole file is part of the independently reviewed index, not a new
    # caller-supplied TTL/approval. Reject any different source-owned lifetime.
    require(b"const GRANT_TTL: Duration = Duration::from_secs(45);" in grant_raw,
            "Graph public upload HOLD: source-owned grant expiry differs")
    latest_close = 0.0
    for item in proof["cases"]:
        case_root = Path(item["root"])
        final = graph_json(graph_read(item["owner"], 4 * GRAPH_BYTES))
        spec = graph_json(graph_read(final["spec_original"], GRAPH_BYTES))
        descriptor = graph_json((case_root / "owner/owner-descriptor.json").read_bytes())
        require(descriptor.get("session_reuse_across_wrapper_instances_allowed") is False
                and descriptor.get("binding_ref_transfers_allowed") is False
                and descriptor.get("automatic_retry") is False
                and descriptor["socket"] == spec["paths"]["owner_socket"]
                and Path(descriptor["socket"]).is_relative_to(case_root), "Graph public upload HOLD: reusable or foreign SDK scope")
        manifest_record = descriptor["driver_manifest"]
        manifest = graph_json(graph_read({"path": manifest_record["path"], "sha256": manifest_record["sha256"],
            "size_bytes": manifest_record["bytes"]}, GRAPH_BYTES))
        require(manifest["secrets"] == [] and manifest["network"] is False and manifest["system_config"] == [],
                "Graph public upload HOLD: external credential or authority channel")
        for client in final["client_roster"]:
            connection = graph_json(graph_read(client["connection_original"], GRAPH_BYTES))
            require(connection["socket"] == descriptor["socket"] and connection["session_file"] == client["session_original"]["path"]
                    and Path(connection["session_file"]).is_relative_to(case_root / "owner")
                    and Path(connection["output_root"]).is_relative_to(case_root / "owner"), "Graph public upload HOLD: foreign fixture connection")
        closure = graph_json(graph_read(final["service_closure"], GRAPH_BYTES))
        latest_close = max(latest_close, datetime.fromisoformat(closure["recorded_at"].replace("Z", "+00:00")).timestamp())
    # No new deadline. Reserve the complete upload before any bounded expiry
    # wait; a wall/monotonic disagreement holds the payload rather than guessing.
    now = time.time()
    predicted = clock.original["clock_anchor_wall_ms"] / 1000 + (time.monotonic_ns() - clock.original["clock_anchor_mono_ns"]) / 1e9
    require(abs(now - predicted) <= 2, "Graph public upload HOLD: expiry clock disagreement")
    wait = max(0, int(latest_close + 50 - now) + 1)
    require(wait <= 55, "Graph public upload HOLD: grant close timestamp is not current")
    if wait:
        clock.admit("source-owned-fixture-grant-expiry", wait, reserve=GRAPH_PHASES["final-upload"])
        time.sleep(wait)
    require(time.time() >= latest_close + 50, "Graph public upload HOLD: effect grants have not expired")
    top_files = {"receipt.json", "source-before.json", "r74-wait-complete.original.json", "graph-discovery.json", "graph-execution.json"}
    raw_suffixes = (".command.json", ".stdout.log", ".stderr.log")
    secrets = [value.encode() for value in (token,) if type(value) is str and value]
    forbidden = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:[A-Z]+ )?PRIVATE " + b"KEY-----)")
    for name, row in inventory["entries"].items():
        parts = Path(name).parts
        require(not any(part.casefold() in {"knowledge", ".git", ".aws", ".ssh", "private", "private-inputs"} for part in parts),
                "Graph public upload HOLD: private input path")
        if parts[0] == "e" and len(parts) > 1:
            require(parts[1] in {"graph-producer", "graph-cases"} or len(parts) == 2
                    and (parts[1] in top_files or any(parts[1] == label + suffix
                        for label in ("graph-discovery", "graph-cases") for suffix in raw_suffixes)),
                    "Graph public upload HOLD: unselected original")
        if parts[0] == "tools" and len(parts) > 1:
            require(parts[1] == "graph-approval-observations" or len(parts) == 2 and (
                parts[1] in {"rustc.txt", "cargo.txt", "node.txt", "npm.txt", "python.txt",
                    "graph-run-response.original.json", "graph-run-response.transport.original.json",
                    "graph-jobs-response.original.json", "graph-jobs-response.transport.original.json",
                    "graph-deadline.original.json", "graph-review-upload-admission.original.json"}
                or any(parts[1] == "graph-phase-" + phase + suffix for phase in GRAPH_PHASES
                       for suffix in (".intent.original.json", ".terminal.original.json"))),
                "Graph public upload HOLD: unselected metadata original")
        if row["kind"] != "file":
            continue
        path = root / name; previous = b""
        with path.open("rb") as stream:
            while block := stream.read(GRAPH_BYTES):
                combined = previous + block
                require(not forbidden.search(combined) and not any(value in combined for value in secrets),
                        "Graph public upload HOLD: external credential material")
                previous = combined[-4096:]
    require(archive_inventory(root) == inventory, "Graph public upload HOLD: originals changed during data closure")
    return {"meaning": "PUBLIC_SYNTHETIC_FIXTURE_ORIGINALS_ONLY", "index": proof["producer_index"],
            "cases": [row["case_id"] for row in proof["cases"]], "members": len(inventory["entries"]),
            "bytes": inventory["file_bytes"], "local_sdk_channels_closed": True,
            "source_bound_effect_grants_expired": True, "external_credentials_allowed": False,
            "private_inputs_allowed": False, "formal_acceptance": False}


def graph_budget_main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True); parser.add_argument("--phase", choices=("review-upload", "final-upload"), required=True)
    args = parser.parse_args(arguments); repo, root = graph_layout(args.repo); clock = GraphOriginalClock(root)
    phase = args.phase
    data_closure = None
    if phase == "final-upload":
        token = os.environ.pop(GRAPH_TOKEN_ENV, "")
        require(token, "Graph final external credential exclusion requires its actual readonly job token")
        data_closure = graph_public_upload_closure(root, clock, token)
    record = clock.admit(phase, GRAPH_PHASES[phase], reserve=clock.future(phase))
    if data_closure is not None:
        record["data_closure"] = data_closure
    folder = root / "retained" if phase == "final-upload" and (root / "retained").exists() else root / "tools"
    graph_put_json(folder / ("graph-" + phase + "-admission.original.json"), record)
    return 0


def graph_main(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("produce", "wait", "run"))
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.phase == "produce":
        recipient = {"spki_der_base64": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_DER_BASE64", ""),
                     "sha256": os.environ.get("MOTIONWRIGHT_GRAPH_REVIEW_SPKI_SHA256", "")}
        return graph_produce(args.repo, recipient)
    return graph_wait_for_external_review(args.repo) if args.phase == "wait" else graph_run_cases(args.repo)


def graph_runtime_main(arguments):
    parser = argparse.ArgumentParser()
    for name in ("repo", "runtime", "producer"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(arguments)
    return graph_runtime_inner(args.repo.resolve(), args.runtime.resolve(), args.producer.resolve())


if __name__ == "__main__":
    if sys.argv[1:2] == ["graph"]:
        raise SystemExit(graph_main(sys.argv[2:]))
    if sys.argv[1:2] == ["graph-budget"]:
        raise SystemExit(graph_budget_main(sys.argv[2:]))
    if sys.argv[1:2] == ["graph-runtime"]:
        raise SystemExit(graph_runtime_main(sys.argv[2:]))
    if sys.argv[1:2] == ["phase1-budget"]:
        raise SystemExit(phase1_service47_budget_main(sys.argv[2:]))
    if sys.argv[1:2] == ["phase1-dependencies"]:
        raise SystemExit(phase1_service47_dependencies_main(sys.argv[2:]))
    if sys.argv[1:2] == ["window-base"]:
        raise SystemExit(window_frontend_base_main(sys.argv[2:]))
    if sys.argv[1:2] == ["camera-preflight"]:
        raise SystemExit(camera_eight_preflight(sys.argv[2:]))
    if sys.argv[1:2] == ["window-preflight"]:
        raise SystemExit(window_frontend_preflight_main(sys.argv[2:]))
    if sys.argv[1:2] == ["studio68"]:
        raise SystemExit(studio68_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "claim-source-contracts":
        raise SystemExit(claim_source_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "workflow-route":
        raise SystemExit(workflow_route_main(sys.argv[2:]))
    sys.exit(archive_main(sys.argv[2:]) if sys.argv[1:2] == ["archive"]
             else unit_build_main(sys.argv[2:]) if sys.argv[1:2] == ["unit-build"] else main())
