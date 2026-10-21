#!/usr/bin/env python3
"""Pinned, isolated service reference evidence; never a human or release verdict."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import wave
from fractions import Fraction

PRODUCT_SHA = "cbf3e7ed1f2d6d3145ebd2a743aba0c5639cad38"
PRODUCT_TREE = "c94c6af4207fc069e2b53c03441b8b0d077e4e18"
SOURCES = {
    "crates/service/examples/acceptance_bench.rs": "442eb5d300a80de0a2e779b1bd9eb3d553e2cc618b48c407876942cdc864e6bf",
    "crates/service/examples/responsiveness_bench.rs": "59c28838a3a43af25d20fe5803d5b4a926a3ed9e0f18e7b3f2e26bce701bd659",
}
PROFILES = {"S": (12, 300, 300, 80, None, 2000),
            "M": (120, 3000, 1800, None, 5, 5000),
            "L": (500, 15000, 7200, None, None, None)}
BRIEFS = ("harbor-safety", "museum-night", "bike-route")
OPERATIONS = ("01-scene-objective", "02-project-title", "03-narrative-premise",
              "04-scene-status", "05-marker", "06-renderer-choice",
              "07-retained-style-lock", "08-second-status",
              "09-second-objective", "10-final-objective")
PLAN = (("normative-nine", "acceptance_bench", None, None, 1800),
        ("responsiveness-S", "responsiveness_bench", "S", None, 900),
        ("responsiveness-M", "responsiveness_bench", "M", None, 900),
        ("responsiveness-L", "responsiveness_bench", "L", None, 900),
        ("negative-truncated", "responsiveness_bench", "S", "truncated-profile", 600),
        ("negative-uneditable", "responsiveness_bench", "S", "uneditable-reopen", 600))
PENDING = ("studio_selection_s_p95", "studio_scrub_or_proxy_ui_blocking",
           "large_scene_object_projection_paging", "installed_first_use",
           "native_render_cache_avoided_work")
LIMITS = {
    "L_scene_object_projection_paging": "NOT_RUN; full Project service APIs, event history is not projection paging",
    "UI_selection_and_scrub": "NOT_RUN; service timing is not input-to-paint or installed WebView timing",
    "native_render_and_saved_work": "NOT_RUN; no inferred FPS, cache savings or native media fidelity",
    "human_acceptance": "NOT_RUN; formal contractual decisions are separate",
    "candidate_composition": "Pinned product source only; no PASS transfer to gate SHA or later main",
    "cold_disk": "NOT_RUN; SQLite close/reopen retains the OS page cache",
    "historical_compatibility_four": "Unchanged; not rerun or credited to this product SHA",
}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def integer(value, label):
    require(type(value) is int and value >= 0, f"{label}: expected nonnegative integer")
    return value


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, f"duplicate JSON key: {key}")
        out[key] = value
    return out


def decode_json(data):
    def invalid(value):
        raise ContractError(f"nonfinite JSON: {value}")
    return json.loads(data, object_pairs_hook=unique_pairs, parse_constant=invalid)


def read_json(path):
    return decode_json(Path(path).read_bytes())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def contained(root, value):
    root = Path(root).resolve(strict=True)
    path = Path(value)
    require(path.is_absolute(), f"relative artifact reference: {path}")
    require(not path.is_symlink(), f"symlink artifact: {path}")
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(root), f"artifact escapes custody root: {path}")
    cursor = path
    while cursor != root:
        require(not cursor.is_symlink(), f"symlink artifact ancestor: {cursor}")
        require(cursor.parent != cursor, f"artifact has no custody ancestor: {path}")
        cursor = cursor.parent
    return resolved


def files_under(root):
    root = Path(root).resolve(strict=True)
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            require(not path.is_symlink(), f"symlink directory in custody: {path}")
        for name in files:
            path = Path(directory) / name
            require(not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), f"nonregular custody member: {path}")
            result.append(path)
    return sorted(result)


def inventory(root):
    return {p.relative_to(root).as_posix(): {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in files_under(root) if p.relative_to(root).as_posix() != "custody-manifest.json"}


def verify_inventory(root, expected):
    require(inventory(Path(root)) == expected, "custody inventory differs: missing, added or changed bytes")


def rational(value):
    require(isinstance(value, dict) and set(value) == {"num", "den"}, "invalid rational")
    require(type(value["num"]) in (str, int) and type(value["den"]) in (str, int), "invalid rational types")
    return Fraction(int(value["num"]), int(value["den"]))


def check_shape(shape, size, truncated=False):
    scenes, nodes, seconds, beats, variants, _ = PROFILES[size]
    expected = {"scenes": scenes, "canvas_nodes": nodes - int(truncated),
                "width": 1920, "height": 1080, "frame_rate": "30/1",
                "voice_sample_rate_hz": 8000, "voice_seconds": seconds,
                "contiguous_scene_timeline": True}
    for key, value in expected.items():
        require(shape.get(key) == value and type(shape.get(key)) is type(value), f"{size} shape differs: {key}")
    require(rational(shape["scene_duration"]) == seconds, "scene duration differs")
    require(rational(shape["voice_duration"]) == seconds, "voice duration differs")
    if beats is not None:
        require(integer(shape.get("beats"), "beats") == beats, "S beat count differs")
    if variants is not None:
        require(integer(shape.get("deliverable_variants"), "variants") == variants, "M variant count differs")
    for key in ("claims", "locks", "node_property_locks"):
        require(integer(shape.get(key), key) > 0, f"empty authored {key}")


def project_shape(project):
    scenes = project["scenes"]
    total = Fraction(0)
    contiguous = True
    for scene in scenes:
        contiguous = contiguous and rational(scene["start"]) == total
        total += rational(scene["duration"])
    master = project["deliverables"][0]
    voice = project["audio"]["voice_tracks"][0]
    duration = rational(voice["measured_duration"])
    rate = rational(master["frame_rate"])
    return {"scenes": len(scenes), "beats": sum(len(s["beats"]) for s in scenes),
            "canvas_nodes": sum(len(s["nodes"]) for s in scenes),
            "scene_duration": {"num": total.numerator, "den": total.denominator},
            "contiguous_scene_timeline": contiguous,
            "voice_seconds": int(duration), "voice_duration": voice["measured_duration"],
            "voice_sample_rate_hz": voice["sample_rate_hz"],
            "width": master["width"], "height": master["height"],
            "frame_rate": f"{rate.numerator}/{rate.denominator}",
            "deliverable_variants": len(project["deliverables"]),
            "claims": len(project["brief"]["claims"]), "locks": len(project["locks"]),
            "node_property_locks": sum(len(n["property_locks"]) for s in scenes for n in s["nodes"])}


def identities(project):
    return {"project_id": project["id"], "generation": project["generation"],
            "active_branch": project["active_branch"], "branch_ids": [b["id"] for b in project["branches"]],
            "scenes": [{"id": s["id"], "beat_ids": [b["id"] for b in s["beats"]],
                        "nodes": [{"id": n["id"], "property_locks": n["property_locks"]} for n in s["nodes"]]}
                       for s in project["scenes"]],
            "assets": project["assets"], "claims": project["brief"]["claims"],
            "deliverables": project["deliverables"]}


def chain(operations, previous, names=None):
    require(isinstance(operations, list), "operation samples are not an array")
    integer(previous, "previous revision")
    if names is not None:
        require([o.get("name") for o in operations] == list(names), "operation roster differs")
    requests = []
    for op in operations:
        require(integer(op["previous_revision"], "previous revision") == previous
                and integer(op["revision"], "revision") == previous + 1, "revision gap or replay")
        integer(op["micros"], "operation micros")
        request = op.get("request_id")
        require(isinstance(request, str) and request.startswith("reference-"), "missing real operation request id")
        requests.append(request)
        previous += 1
    require(len(set(requests)) == len(requests), "duplicate operation request id")
    return previous, requests


def check_metrics(case, responsiveness):
    size = case["size"]
    operations = case["measured_operations"] if responsiveness else case["operations"]
    expected_count = 40 if responsiveness else 10
    require(len(operations) == expected_count, "measured sample cardinality differs")
    values = [integer(op["micros"], "sample micros") for op in operations]
    metric = case["small_commit"]
    require(integer(metric["samples"], "sample count") == expected_count
            and metric["samples_micros"] == values, "raw samples differ from operations")
    p95 = sorted(values)[(len(values) * 95 + 99) // 100 - 1]
    require(integer(metric["p95_micros"], "p95") == p95 and integer(metric["max_micros"], "max") == max(values),
            "computed p95/max differs")
    require(metric["percentile_method"] == "nearest-rank" and type(metric["outliers_removed"]) is int
            and metric["outliers_removed"] == 0, "sampling contract differs")
    required = 250 if size == "S" else None
    require(metric["required_budget_ms"] == required and metric["budget_ms"] == required, "commit budget was changed")
    if required is not None:
        require(p95 < required * 1000, "S small commit p95 violates strict 250 ms limit")
    limit = PROFILES[size][5]
    require(case["required_reopen_budget_ms"] == limit and case["reopen_budget_ms"] == limit, "open budget was changed")
    for key in ("reference_open_micros", "reopen_micros", "final_reopen_micros"):
        value = integer(case[key], key)
        if limit is not None:
            require(value < limit * 1000, f"{size} {key} violates strict {limit} ms limit")
    require(case["budget_failures"] == [], "producer recorded budget failures")
    require(case["max_commit_micros"] == max(o["micros"] for o in case["operations"]), "ten-change maximum differs")


def check_negative(case, negative, exit_code):
    require(type(exit_code) is int and exit_code == 1 and case["status"] == "FAIL", "negative must actually fail with producer exit 1")
    require(case["negative_fixture"] == negative and case["size"] == "S", "negative identity differs")
    if negative == "truncated-profile":
        require(case["stage"] == "validate_persisted_reference_shape", "truncated negative failed at wrong stage")
        require(case.get("error") == "reference object count differs after persisted readback", "truncated negative has wrong cause")
        require(case["normative_reference"] is False and case.get("reference_shape_verified") is not True, "truncated profile was promoted")
        check_shape(case["dataset"], "S", truncated=True)
    elif negative == "uneditable-reopen":
        require(case["stage"] == "positive_cas_edit_after_reopen", "uneditable negative failed at wrong stage")
        require(case["normative_reference"] is True and case["reference_shape_verified"] is True, "uneditable fixture shape was not valid")
        edit = case["post_reopen_edit"]
        require(edit["status"] == "FAIL" and edit.get("class") == "domain_locked", "negative did not prove DomainError::Locked")
        require(bool(edit.get("error")) and case.get("error") == edit["error"], "negative error propagation differs")
        require(case["negative_injection"]["name"] == "negative-content-lock", "missing actual Content-lock injection")
        for key in ("full_project_equal_after_reopen", "claims_ids_and_all_locks_equal_after_reopen", "cas_bytes_equal_after_reopen"):
            require(case[key] is True, f"negative failed before reopen invariant: {key}")
        check_shape(case["dataset"], "S")
    else:
        raise ContractError("unreviewed negative fixture")


def semantic_families(project):
    # Exclude UUIDs, title, source labels, order, repetitions and numbered labels.
    def normalized(value):
        return re.sub(r"\s+", " ", re.sub(r"\b\d+\b", "", value)).strip().casefold()
    brief = (normalized(project["brief"]["objective"]),
             tuple(sorted({normalized(c["text"]) for c in project["brief"]["claims"]})))
    scenes = tuple(sorted({(normalized(s["objective"]),
                            tuple(sorted({normalized(b["objective"]) for b in s["beats"]})))
                           for s in project["scenes"]}))
    nodes = tuple(sorted({normalized(n["text"]) for s in project["scenes"] for n in s["nodes"] if n.get("text")}))
    require(brief[0] and brief[1] and scenes and nodes, "empty authored semantic family")
    return brief, scenes, nodes


def check_diversity(projects):
    require(set(projects) == {(b, s) for b in BRIEFS for s in PROFILES}, "diversity inventory differs")
    for size in PROFILES:
        families = [semantic_families(projects[(brief, size)]) for brief in BRIEFS]
        for index, label in enumerate(("brief", "scenes/beats", "node text")):
            require(len({family[index] for family in families}) == 3,
                    f"{size} {label} diversity is only labels, ordering, repetition or UUID changes")


def verify_sqlite(database, expected, temporary):
    # SQLite reads disposable copies; retained WAL/SHM are never opened for mutation.
    database = Path(database)
    originals = {str(database) + suffix: digest(str(database) + suffix)
                 for suffix in ("", "-wal", "-shm") if Path(str(database) + suffix).exists()}
    with tempfile.TemporaryDirectory(prefix="sqlite-readback-", dir=temporary) as scratch:
        copy = Path(scratch) / database.name
        for original in originals:
            shutil.copyfile(original, Path(scratch) / Path(original).name)
        connection = sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True)
        try:
            require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "retained SQLite integrity failure")
            rows = connection.execute("SELECT id,generation,revision,title,document_json FROM projects").fetchall()
            require(len(rows) == 1, "retained fixture database must contain exactly one project")
            row = rows[0]
            require(decode_json(row[4]) == expected, "SQLite document differs from retained project")
            require(tuple(row[:4]) == (expected["id"], expected["generation"], expected["revision"], expected["title"]),
                    "SQLite authority columns differ from retained project")
        finally:
            connection.close()
    require(all(digest(path) == value for path, value in originals.items()), "SQLite verification changed original bytes")


def verify_wav_and_cas(folder, project, size):
    source = folder / "reference-voice.wav"
    seconds = PROFILES[size][2]
    with wave.open(str(source), "rb") as wav:
        require((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes(), wav.getcomptype())
                == (1, 2, 8000, seconds * 8000, "NONE"), "physical WAV shape differs")
        decoded = 0
        while True:
            data = wav.readframes(65536)
            if not data:
                break
            require(len(data) % 2 == 0, "truncated PCM sample")
            decoded += len(data) // 2
        require(decoded == seconds * 8000, "physical WAV frames are truncated")
    require(source.stat().st_size == 44 + seconds * 8000 * 2, "original WAV size differs")
    expected = {asset["content_sha256"] for asset in project["assets"]}
    require(len(expected) == 1 and digest(source) in expected, "source WAV is not the retained asset content")
    descriptors = []
    for sha in sorted(expected):
        require(re.fullmatch(r"[0-9a-f]{64}", sha), "invalid CAS digest")
        blob = contained(folder, folder / "target-store" / "blobs" / "sha256" / sha[:2] / sha)
        require(digest(blob) == sha and blob.stat().st_size == source.stat().st_size, "real CAS bytes differ from original WAV")
        descriptors.append({"sha256": sha, "size_bytes": blob.stat().st_size})
    for name in ("cas-before-edits.json", "final-cas.json"):
        path = folder / name
        if path.exists():
            require(read_json(path) == descriptors, f"retained CAS descriptors differ: {name}")
    return descriptors


def check_history(path, revision):
    history = read_json(path)
    require(history["kind"] == "event_history_only" and history["scene_object_projection_claim"] is False,
            "history was promoted to projection paging")
    require([event["revision"] for event in history["events"]] == list(range(1, revision + 1)),
            "retained history is incomplete or reordered")
    after = 0
    pages = history["pages"]
    require(bool(pages), "missing event history pages")
    for index, page in enumerate(pages):
        require(page["after_revision"] == after and 0 <= integer(page["returned"], "page size") <= 7, "invalid event history page")
        require(page["returned"] > 0 or index == len(pages) - 1, "history stopped before last page")
        after += page["returned"]
    require(after == revision and pages[-1]["returned"] == 0, "history pagination did not terminate at revision")


def check_case(case, folder, responsiveness, negative, exit_code, temporary):
    size = case["size"]
    require(size in PROFILES, "unknown profile")
    scenes, nodes, seconds, beats, variants, _ = PROFILES[size]
    require(case["required_shape"] == {"scenes": scenes, "canvas_nodes": nodes,
            "duration_seconds": seconds, "beats": beats, "deliverable_variants": variants,
            "width": 1920, "height": 1080, "frame_rate": "30/1"}, "required shape contract changed")
    require(case["dataset_family"] == "normative_reference_profile", "wrong dataset family")
    require(case["requested_scene_count"] == scenes and case["scene_count"] == scenes, "scene cardinality differs")
    require(case["negative_fixture"] == negative, "unexpected injected fixture")
    require(contained(folder, case["artifact_directory"]) == folder, "case directory differs")
    for filename in ("genesis-project.json", "genesis-backup.json", "import-plan.json", "imported-project.json",
                     "prepared-project.json", "reference-voice.wav", "genesis-store/genesis.sqlite3", "target-store/motionwright.sqlite3"):
        contained(folder, folder / filename)
    prepared = read_json(folder / "prepared-project.json")
    check_shape(project_shape(prepared), size, negative == "truncated-profile")
    check_shape(case["dataset"], size, negative == "truncated-profile")
    verify_sqlite(folder / "genesis-store" / "genesis.sqlite3", read_json(folder / "genesis-project.json"), temporary)
    verify_wav_and_cas(folder, prepared, size)
    if negative == "truncated-profile":
        check_negative(case, negative, exit_code)
        verify_sqlite(folder / "target-store" / "motionwright.sqlite3", prepared, temporary)
        require("operations" not in case and "post_reopen_edit" not in case, "truncated negative continued past shape failure")
        return {"scope": "causal-negative", "cause": negative}
    require(case["normative_reference"] is True and case["reference_shape_verified"] is True, "reference shape was not verified")
    require(case["revision_start"] == prepared["revision"] and case["revision_delta"] == 10, "revision-ten source differs")
    previous, request_ids = chain(case["operations"], prepared["revision"], OPERATIONS)
    require(case["revision_end"] == previous, "revision-ten endpoint differs")
    baseline = read_json(folder / "identities-before-edits.json")
    require(baseline == identities(prepared), "baseline identity snapshot differs")
    after_ten = read_json(folder / "project-after-ten.json")
    reopened = read_json(folder / "project-after-reopen.json")
    require(after_ten["revision"] == previous, "revision-ten project differs")
    for project in (after_ten, reopened):
        require(identities(project) == baseline, "retained project identity/claim/variant/property lock changed")
        require(all(lock in project["locks"] for lock in prepared["locks"]), "retained authored lock was lost")
        check_shape(project_shape(project), size)
    check_history(folder / "history-after-ten.json", previous)
    for key in ("first_editable_scene_preserved_after_revision_ten", "full_project_equal_after_reopen",
                "claims_ids_and_all_locks_equal_after_reopen", "cas_bytes_equal_after_reopen"):
        require(case[key] is True, f"missing reopen invariant: {key}")
    if negative == "uneditable-reopen":
        check_negative(case, negative, exit_code)
        previous, more_ids = chain([case["negative_injection"]], previous, ["negative-content-lock"])
        request_ids += more_ids
        require(reopened["revision"] == previous, "Content-lock revision did not persist")
        extra = [lock for lock in reopened["locks"] if lock not in after_ten["locks"]]
        require(len(extra) == 1 and extra[0]["kind"] == "content", "real content lock absent after reopen")
        for key in set(after_ten) - {"revision", "updated_at", "locks"}:
            require(reopened[key] == after_ten[key], f"negative injection changed unrelated project field: {key}")
        require("small_commit" not in case and "final_revision" not in case, "uneditable negative continued past failed edit")
        verify_sqlite(folder / "target-store" / "motionwright.sqlite3", reopened, temporary)
        return {"scope": "causal-negative", "cause": negative}
    require(exit_code == 0 and case["status"] == "PASS" and case["stage"] == "complete", "positive case did not complete")
    require(after_ten == reopened, "full Project differs across SQLite close/reopen")
    edit = case["post_reopen_edit"]
    require(edit["status"] == "PASS", "real edit after reopen failed")
    previous, more_ids = chain([edit], previous)
    request_ids += more_ids
    edited = read_json(folder / "project-edited-after-reopen.json")
    require(edited["revision"] == previous and identities(edited) == baseline, "post-reopen edit was not retained")
    if responsiveness:
        previous, more_ids = chain(case["warmup_operations"], previous, [f"warmup-{n}" for n in range(5)])
        request_ids += more_ids
        previous, more_ids = chain(case["measured_operations"], previous, [f"sample-{n}" for n in range(40)])
        request_ids += more_ids
    require(len(set(request_ids)) == len(request_ids), "request id replay across sample phases")
    require(case["all_warmup_samples_retained"] is True, "warmup samples omitted")
    check_metrics(case, responsiveness)
    final = read_json(folder / "final-project.json")
    require(final["revision"] == previous == case["final_revision"] == case["event_count"], "final durable revision count differs")
    require(identities(final) == baseline, "final sample changed authored identities")
    require(all(lock in final["locks"] for lock in reopened["locks"]), "final sample lost a lock")
    check_shape(project_shape(final), size)
    check_history(folder / "final-history.json", previous)
    verify_wav_and_cas(folder, final, size)
    verify_sqlite(folder / "target-store" / "motionwright.sqlite3", final, temporary)
    database = folder / "target-store" / "motionwright.sqlite3"
    observed_files = {str(database) + suffix: Path(str(database) + suffix).stat().st_size
                      for suffix in ("", "-wal", "-shm") if Path(str(database) + suffix).exists()}
    require(len(case["database_files"]) == len(observed_files), "SQLite/WAL/SHM duplicate or missing member")
    require({str(contained(folder, row["path"])): row["bytes"] for row in case["database_files"]} == observed_files,
            "SQLite/WAL/SHM file inventory differs")
    require(case["database_bytes"] == database.stat().st_size, "SQLite byte size differs")
    return {"scope": "service-reference", "size": size, "revision": previous,
            "p95_micros": case["small_commit"]["p95_micros"], "L_projection_paging": "NOT_RUN"}


def check_context(report, suite, product, binary, compiler):
    require(report["source_sha"] == PRODUCT_SHA and report["source_binding_consistent"] is True, "source SHA binding differs")
    require(report["source_attribution_verified"] is False, "producer attribution field was promoted")
    for phase in ("before", "after"):
        context = report[f"source_context_{phase}"]
        require(context == read_json(suite / f"context-{phase}.json"), "context original differs")
        require(context["source_sha_compiled"] == PRODUCT_SHA == context["source_sha_runtime"], "compiled/runtime SHA differs")
        require(context["compiler_version"] == compiler and compiler != "NOT_CAPTURED", "compiler binding missing or mismatched")
        exe = context["executable"]
        require(Path(exe["path"]).resolve() == binary.resolve() and exe["sha256"] == digest(binary)
                and exe["size_bytes"] == binary.stat().st_size, "executed binary binding differs")
        require(context["observed_os"] == "linux" and context["hardware_description_verified"] is False,
                "producer platform or hardware assertion changed")
        sources = report[f"retained_sources_{phase}"]
        require(sources == read_json(suite / f"source-{phase}.json"), "retained source original differs")
        require(set(sources) == set(SOURCES) | {"Cargo.lock"}, "retained source roster differs")
        for relative, entry in sources.items():
            source = contained(product, entry["source"])
            require(source == (product / relative).resolve(), "retained source points to another checkout path")
            copy = contained(suite, entry["retained"])
            require(copy == suite / f"source-{phase}" / Path(relative).name, "retained source destination differs")
            require(entry["sha256"] == digest(source) == digest(copy) and entry["bytes"] == source.stat().st_size == copy.stat().st_size,
                    "retained source bytes differ")
    require(report["source_context_before"] == report["source_context_after"], "runtime context changed during execution")
    require(report["human_feedback"].startswith("NOT_RUN") and report["comparative_claims"].startswith("NONE"),
            "unsupported human/comparative promotion")
    require(set(report["pending_capabilities"]) == set(PENDING), "pending capability roster changed")
    require(all(report["pending_capabilities"][name]["status"] == "NOT_RUN" for name in PENDING), "unmeasured capability promoted")
    require(report["pending_capabilities"]["studio_selection_s_p95"]["required_limit_ms"] == 100
            and report["pending_capabilities"]["studio_scrub_or_proxy_ui_blocking"]["required_limit_ms"] == 200,
            "unmeasured UI limits changed")


def validate_invocation(directory, plan, receipt, product, binary, compiler, temporary):
    label, example, selected, negative, _ = plan
    require(receipt["status"] == "COMPLETED", "execution is UNKNOWN or not complete")
    roots = list((directory / "fixtures").iterdir())
    require(len(roots) == 1 and roots[0].is_dir(), "producer suite directory cardinality differs")
    suite = roots[0].resolve()
    require((directory / "stdout.log").read_bytes() == (suite / "report.json").read_bytes(), "stdout/report original bytes differ")
    report = read_json(suite / "report.json")
    require(contained(suite, report["artifact_directory"]) == suite, "report root differs")
    expected_schema = "motionwright.responsiveness.v1" if selected else "motionwright.performance.reference.v1"
    require(report["schema"] == expected_schema and report["generated_by"] == f"motionwright-service/examples/{example}",
            "wrong producer or configuration-error report")
    check_context(report, suite, product, binary, compiler)
    if selected:
        require(report["brief"] == "responsiveness" and report["size"] == selected, "responsiveness inventory differs")
        case = read_json(suite / f"responsiveness-{selected}" / "case-report.json")
        for key, value in case.items():
            if key != "artifact_directory":
                require(report[key] == value, f"responsiveness original case differs: {key}")
        rows = [case]
    else:
        require(report["briefs"] == 3 and report["datasets"] == 9 and len(report["results"]) == 9, "normative nine missing")
        rows = report["results"]
        require({(c["brief"], c["size"]) for c in rows} == {(b, s) for b in BRIEFS for s in PROFILES}, "exact nine inventory differs")
    require(report["status"] == ("FAIL" if negative else "PASS"), "suite result differs from expected control")
    require(read_json(suite / "partial-results.json") == rows, "partial/final case originals differ")
    expected_dirs = {f"{c['brief']}-{c['size']}" for c in rows} | {"source-before", "source-after"}
    require({p.name for p in suite.iterdir() if p.is_dir()} == expected_dirs, "unexpected or missing fixture directory")
    admissions, prepared, reopened = [], {}, {}
    for case in rows:
        folder = suite / f"{case['brief']}-{case['size']}"
        require(read_json(folder / "case-report.json") == case, "case original differs from suite")
        require(contained(suite, case["context_file"]) == suite / "context-before.json", "case context reference differs")
        admissions.append(check_case(case, folder, selected is not None, negative, receipt["returncode"], temporary))
        if not selected:
            prepared[(case["brief"], case["size"])] = read_json(folder / "prepared-project.json")
            reopened[(case["brief"], case["size"])] = read_json(folder / "project-after-reopen.json")
    if not selected:
        check_diversity(prepared)
        check_diversity(reopened)
    resource = (directory / "resource-usage.txt").read_text(encoding="utf-8")
    match = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", resource)
    require(match is not None, "measured process RSS not retained")
    return {"invocation": label, "status": "PASS", "actual_fixture_count": len(rows),
            "report_sha256": digest(suite / "report.json"), "producer_exit_code": receipt["returncode"],
            "peak_rss_kib": int(match.group(1)), "cases": admissions,
            "diversity_after_sqlite_reopen": "OBSERVED" if not selected else "NOT_APPLICABLE"}


def probe(argv, cwd=None):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=30, check=False)
    require(result.returncode == 0, f"probe failed: {argv[0]} ({result.returncode}): {result.stderr.decode(errors='replace')}")
    return result.stdout


def source_identity(root, expected_sha, expected_tree=None):
    require(re.fullmatch(r"[0-9a-f]{40}", expected_sha), "invalid source SHA")
    head = probe(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    tree = probe(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"]).decode().strip()
    status = probe(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"]).decode()
    require(head == expected_sha and (expected_tree is None or tree == expected_tree), "checkout SHA/tree differs from reviewed source")
    require(not status, "checkout contains tracked or untracked modifications")
    return {"sha": head, "tree": tree, "git_status_porcelain": status}


def pinned_product(root, requested_sha):
    require(requested_sha == PRODUCT_SHA, "product SHA is not reviewed in this runner")
    identity = source_identity(root, PRODUCT_SHA, PRODUCT_TREE)
    identity["producer_files"] = {}
    for relative, expected in SOURCES.items():
        actual = digest(root / relative)
        blob = probe(["git", "-C", str(root), "show", f"{PRODUCT_SHA}:{relative}"])
        require(actual == expected == hashlib.sha256(blob).hexdigest(), f"pinned producer bytes differ: {relative}")
        identity["producer_files"][relative] = {"sha256": actual, "bytes": len(blob)}
    return identity


def stop_owned_process(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_process(argv, directory, cwd, environment, timeout):
    directory.mkdir(parents=True, exist_ok=False)
    receipt = {"argv": argv, "cwd": str(cwd), "timeout_seconds": timeout,
               "started_unix": time.time(), "status": "STARTING", "returncode": None,
               "retry_count": 0, "timeout_policy": "UNKNOWN; preserve originals; no retry"}
    write_json(directory / "execution.json", receipt)
    started = time.monotonic()
    with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
        process = None
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
            receipt.update(pid=process.pid, status="RUNNING")
            write_json(directory / "execution.json", receipt)
            receipt["returncode"] = process.wait(timeout=timeout)
            receipt["status"] = "COMPLETED"
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            receipt["status"] = "UNKNOWN"
            receipt["reason"] = "owned process interrupted or timed out; native outcome not inferred"
            if process is not None:
                stop_owned_process(process)
                receipt["returncode_after_stop"] = process.returncode
        except OSError as error:
            receipt["status"] = "START_FAILED"
            receipt["reason"] = str(error)
    receipt.update(finished_unix=time.time(), elapsed_seconds=time.monotonic() - started)
    write_json(directory / "execution.json", receipt)
    return receipt


def observed_machine(output):
    observed = {"platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
                "disk": dict(zip(("total", "used", "free"), shutil.disk_usage(output))),
                "environment_scope": {key: os.environ.get(key) for key in
                    ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME", "GITHUB_REPOSITORY", "GITHUB_RUN_ID",
                     "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_WORKFLOW_SHA", "GITHUB_SHA")}}
    for filename, source in (("cpuinfo.txt", "/proc/cpuinfo"), ("meminfo.txt", "/proc/meminfo"), ("os-release.txt", "/etc/os-release")):
        data = Path(source).read_bytes()
        (output / filename).write_bytes(data)
        observed[filename] = hashlib.sha256(data).hexdigest()
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", (output / "meminfo.txt").read_text(), re.MULTILINE)
    require(match is not None, "available memory not observed")
    observed["available_memory_bytes"] = int(match.group(1)) * 1024
    observed["operational_preflight_minimums"] = {"free_disk_bytes": 8 * 1024**3, "available_memory_bytes": 2 * 1024**3,
        "meaning": "runner resource admission only; not normative performance thresholds",
        "logical_source_wav_floor_bytes": 604800616, "source_plus_CAS_floor_bytes": 1209601232}
    write_json(output / "machine.json", observed)
    require(observed["disk"]["free"] >= 8 * 1024**3, "insufficient operational disk budget")
    require(observed["available_memory_bytes"] >= 2 * 1024**3, "insufficient operational memory budget")
    return observed


def execute(args):
    gate = Path(args.gate).resolve(strict=True)
    product = Path(args.product).resolve(strict=True)
    output_arg = Path(args.output)
    require(output_arg.is_absolute() and not output_arg.exists(), "output must be new and absolute; do not overwrite or retry old evidence")
    output = output_arg.resolve()
    require(not output.is_relative_to(product) and not output.is_relative_to(gate), "fixtures must be outside both source checkouts")
    require(not gate.is_relative_to(output) and not product.is_relative_to(output), "output cannot contain a source checkout")
    output.mkdir(parents=True, exist_ok=False)
    admission = {"schema": "motionwright.performance-reference-runner.v1", "status": "FAIL",
                 "gate_sha": args.gate_sha, "product_sha": args.product_sha, "product_tree": PRODUCT_TREE,
                 "source_attribution_scope": "independent checkout, Git tree, producer hashes and executed binary",
                 "expected_invocations": 6, "expected_fixtures": 14, "invocations": [], "limits": LIMITS,
                 "formal_acceptance": False, "release_authorized": False, "old_pass_transferred": False,
                 "actual_avoided_CI_minutes": None, "ci_savings_claim": "No invented saving or transferred compatibility result"}
    deadline = time.monotonic() + 6000
    try:
        require(sys.platform == "linux", "G2 runner requires the explicitly selected disposable Linux host")
        require(Path("/usr/bin/time").is_file(), "GNU time required for separate process resource evidence")
        (output / "checks").mkdir()
        temporary = output / "tmp"
        temporary.mkdir()
        observed_machine(output)
        product_before = pinned_product(product, args.product_sha)
        gate_before = source_identity(gate, args.gate_sha)
        require(Path(__file__).resolve() == gate / "tooling" / "ci_performance_reference.py", "runner was loaded outside attested gate checkout")
        write_json(output / "source-before.json", {"product": product_before, "gate": gate_before})
        probe(["git", "-C", str(product), "archive", "--format=tar", f"--output={output / 'product-source.tar'}", PRODUCT_SHA])
        gate_members = [".github/workflows/performance-reference-profiles.yml", "tooling/ci_performance_reference.py",
                        "tooling/tests/test_ci_performance_reference.py"]
        probe(["git", "-C", str(gate), "archive", "--format=tar", f"--output={output / 'gate-source.tar'}", args.gate_sha, "--", *gate_members])
        compiler = probe(["rustc", "--version"], product).decode().strip()
        (output / "rustc-version.txt").write_bytes(probe(["rustc", "-vV"], product))
        (output / "cargo-version.txt").write_bytes(probe(["cargo", "--version"], product))
        target = output.parent / (output.name + "-build-target")
        require(not target.exists(), "build target must be new; no uncertain build retry")
        target.mkdir()
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("MOTIONWRIGHT_") and key not in
                       ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER")}
        environment.update(GITHUB_SHA=PRODUCT_SHA, RUSTC_VERSION=compiler, CARGO_TARGET_DIR=str(target),
                           CARGO_TERM_COLOR="never", RUST_BACKTRACE="1", TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
        build = run_process(["cargo", "build", "--locked", "--release", "-p", "motionwright-service",
                             "--example", "acceptance_bench", "--example", "responsiveness_bench"],
                            output / "build", product, environment, 900)
        admission["build"] = build
        require(build["status"] == "COMPLETED" and build["returncode"] == 0,
                "build failed or UNKNOWN; compilation is not benchmark execution")
        (output / "bin").mkdir()
        binaries = {}
        for example in ("acceptance_bench", "responsiveness_bench"):
            source = target / "release" / "examples" / example
            destination = output / "bin" / example
            shutil.copy2(source, destination)
            binaries[example] = destination
        write_json(output / "executed-binaries.json", {name: {"sha256": digest(path), "bytes": path.stat().st_size}
                    for name, path in binaries.items()})
        (output / "runs").mkdir()
        for plan in PLAN:
            label, example, selected, negative, timeout = plan
            if time.monotonic() >= deadline:
                admission["invocations"].append({"invocation": label, "status": "NOT_RUN", "reason": "overall operational budget exhausted before starting"})
                break
            directory = output / "runs" / label
            process_environment = dict(environment)
            process_environment.update(MOTIONWRIGHT_REFERENCE_PROFILES="normative",
                                       MOTIONWRIGHT_BENCH_ARTIFACT_ROOT=str(directory / "fixtures"),
                                       MOTIONWRIGHT_BENCH_HARDWARE_DESCRIPTION="independently observed in machine.json; no fixed CPU/RAM performance promise")
            if selected:
                process_environment["MOTIONWRIGHT_RESPONSIVENESS_PROFILE"] = selected
            if negative:
                process_environment["MOTIONWRIGHT_BENCH_NEGATIVE"] = negative
            receipt = run_process(["/usr/bin/time", "-v", "-o", str(directory / "resource-usage.txt"), str(binaries[example])],
                                  directory, product, process_environment, min(timeout, max(1, int(deadline - time.monotonic()))))
            if receipt["status"] != "COMPLETED":
                admission["invocations"].append({"invocation": label, "status": receipt["status"], "reason": "not eligible for benchmark or negative PASS"})
                break
            try:
                item = validate_invocation(directory, plan, receipt, product, binaries[example], compiler, temporary)
            except (ContractError, KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
                item = {"invocation": label, "status": "FAIL", "reason": str(error), "producer_exit_code": receipt["returncode"]}
            admission["invocations"].append(item)
            write_json(output / "checks" / f"{label}.json", item)
            write_json(output / "partial-admission.json", admission)
        product_after = pinned_product(product, args.product_sha)
        gate_after = source_identity(gate, args.gate_sha)
        write_json(output / "source-after.json", {"product": product_after, "gate": gate_after})
        require(product_after == product_before and gate_after == gate_before, "source changed while executing")
        admission["actual_validated_fixture_count"] = sum(item.get("actual_fixture_count", 0) for item in admission["invocations"])
        require(len(admission["invocations"]) == 6 and admission["actual_validated_fixture_count"] == 14
                and all(item["status"] == "PASS" for item in admission["invocations"]), "selective reference gate is incomplete or failed")
        admission["status"] = "PASS"
    except (ContractError, KeyError, TypeError, ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        admission["error"] = str(error)
        admission["status"] = "UNKNOWN" if admission.get("build", {}).get("status") == "UNKNOWN" or any(
            item["status"] == "UNKNOWN" for item in admission["invocations"]) else "FAIL"
    completed = {item["invocation"] for item in admission["invocations"]}
    for label, *_ in PLAN:
        if label not in completed:
            admission["invocations"].append({"invocation": label, "status": "NOT_RUN", "reason": "execution stopped; no retry or transferred result"})
    write_json(output / "admission.json", admission)
    try:
        members = inventory(output)
        write_json(output / "custody-manifest.json", {"schema": "motionwright.performance-reference-custody.v1",
                   "product_sha": args.product_sha, "gate_sha": args.gate_sha, "status": admission["status"], "members": members})
        verify_inventory(output, members)
    except (ContractError, OSError) as error:
        admission["status"] = "FAIL"
        admission["custody_error"] = str(error)
        write_json(output / "admission.json", admission)
    print(json.dumps({"status": admission["status"], "output": str(output),
                      "gate_sha": args.gate_sha, "product_sha": args.product_sha,
                      "actual_validated_fixture_count": admission.get("actual_validated_fixture_count", 0),
                      "human_acceptance": "NOT_RUN", "L_scene_object_projection_paging": "NOT_RUN"}, sort_keys=True))
    return 0 if admission["status"] == "PASS" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("gate", "gate-sha", "product", "product-sha", "output"):
        run.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except (ContractError, OSError) as error:
        print(f"reference-runner admission failure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
