#!/usr/bin/env python3
"""Seal closed service-test stores and compare a fresh-copy JSONL readback.

Only existing complete Project, journal and asset expectations are compared.
The source/runner build relationship and the expected browser-case inventory
must be verified by the calling workflow; --source-commit records that binding.
No service process is started before the complete original capture archive is
sealed and extracted. Store originals are never opened through SQLite.
"""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    raise SystemExit("Run this verifier with Python -B -I -S")

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import stat
import subprocess
import threading
import time
from types import SimpleNamespace
import zipfile

MAX_INPUT_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_TREE_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_STDOUT_BYTES = 32 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_TOTAL_LOG_BYTES = 256 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REQUESTS = 10_000
TIMEOUT_SECONDS = 30
REAL_FILES = {
    "audio-draft-preservation.spec.ts", "brief-preservation.spec.ts",
    "brief-service.spec.ts", "canvas-draft-service.spec.ts",
    "caption-continuity-service.spec.ts", "delivery-profile-preservation.spec.ts",
    "delivery-recipe.spec.ts", "file-import-service.spec.ts",
    "project-lifecycle-service.spec.ts", "scoped-change-capture.spec.ts",
    "scoped-change-service.spec.ts",
}


class VerificationError(Exception):
    """The evidence does not meet the declared contract."""



def require(condition, message):
    if not condition:
        raise VerificationError(message)



def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()



def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result



def reject_nonfinite(value):
    raise VerificationError(f"Non-finite JSON number: {value}")



def parse_json(value: str | bytes):
    return json.loads(value, parse_float=Decimal, parse_constant=reject_nonfinite,
                      object_pairs_hook=unique_object)



def canonical_number(value: int | Decimal):
    """Compare JSON numeric values without rounding exact integers or decimals."""
    decimal = Decimal(value) if type(value) is int else value
    require(decimal.is_finite(), "Non-finite JSON number")
    if decimal.is_zero():
        return (0, (0,), 0)
    sign, digits, exponent = decimal.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return (sign, tuple(digits), exponent)



def canonical(value):
    """Tagged comparison: 0 == 0.0, but false != 0 and '1' != 1."""
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("boolean", value)
    if type(value) is int or type(value) is Decimal:
        return ("number", canonical_number(value))
    if type(value) is str:
        return ("string", value)
    if type(value) is list:
        return ("array", tuple(canonical(child) for child in value))
    if type(value) is dict:
        return ("object", tuple((key, canonical(child))
                                for key, child in sorted(value.items())))
    raise VerificationError(f"Unsupported JSON value type: {type(value).__name__}")



def verify_declared_files(root: Path, declared):
    require(type(declared) is dict, "Missing file manifest")
    for name, identity in declared.items():
        safe_relative(name)
        require(type(identity) is dict and type(identity.get("bytes")) is int
                and identity["bytes"] >= 0, f"Invalid size for {name}")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256", ""))),
                f"Invalid SHA-256 for {name}")
    require(files(root) == declared,
            f"Extracted file inventory or bytes differ from the manifest: {root.name}")



def verify_cas(root: Path):
    media = files(root)
    for name, identity in media.items():
        parts = PurePosixPath(name).parts
        if parts[0] == "sha256":
            require(len(parts) == 3 and parts[1] == parts[2][:2]
                    and parts[2] == identity["sha256"],
                    f"CAS path does not identify its exact bytes: {name}")
    return media



def inspect_capture(captured: Path, record):
    verify_declared_files(captured, record["source_files"])
    manifest = parse_json((captured / "manifest.json").read_bytes())
    oracle = parse_json((captured / "oracle.json").read_bytes())
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1
            and type(oracle.get("schemaVersion")) is int and oracle["schemaVersion"] == 1,
            "Unsupported capture/oracle schema")
    require(oracle.get("journalComplete") is True
            and type(oracle.get("journalAfterRevisionExclusive")) is int
            and oracle["journalAfterRevisionExclusive"] == 0,
            "Oracle does not declare a complete journal from revision zero")
    source_hash = manifest.get("sourceBeforeSha256")
    require(type(source_hash) is str and bool(re.fullmatch(r"[0-9a-f]{64}", source_hash))
            and source_hash == manifest.get("sourceAfterSha256")
            == manifest.get("snapshotSha256"), "Capture was not byte-stable")
    lifecycle = manifest.get("lifecycle")
    require(type(lifecycle) is list and lifecycle, "Missing process closure evidence")
    for item in lifecycle:
        require(item.get("taints") == [] and item.get("exits"),
                "Capture has a tainted or unclosed service process")
        for observed_exit in item["exits"]:
            require(type(observed_exit.get("code")) is int
                    and observed_exit["code"] == 0
                    and observed_exit.get("signal") is None,
                    "Capture contains a non-clean service exit")
    declared = {}
    for entry in manifest["entries"]:
        safe_relative(entry["path"], allow_empty=entry["kind"] == "directory")
        if entry["kind"] == "file":
            require(entry["path"] not in declared, "Duplicate capture manifest entry")
            declared[entry["path"]] = {"bytes": entry["sizeBytes"], "sha256": entry["sha256"]}
        else:
            require(entry["kind"] == "directory", "Unsupported capture manifest entry")
    verify_declared_files(captured / "store", declared)
    expected = oracle["project"]
    revision = expected["revision"]
    require(type(revision) is int and revision >= 0, "Invalid exact project revision")
    require(expected["id"] == record["project_id"] and revision == record["revision"],
            "Capture index does not match its project oracle")
    require(type(oracle["journal"]) is list and len(oracle["journal"]) == revision,
            "The journal length does not match the current revision")
    require(all(type(event.get("revision")) is int and event["revision"] == index + 1
                for index, event in enumerate(oracle["journal"])),
            "The oracle journal is not contiguous from revision 1")
    database = safe_relative(manifest["databaseRelativePath"])
    require((captured / "store" / database).is_file(), "Captured database is absent")
    return oracle, captured / "store" / database



def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")



def identity_bytes(raw):
    return {"bytes": len(raw), "sha256": sha256_bytes(raw)}



def exact_member(raw, wanted):
    """Return an original top-level JSON value, not a reserialization."""
    parse_json(raw)  # Reject duplicate keys, invalid/non-finite JSON first.
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder(parse_float=Decimal)
    position = 0
    while text[position].isspace():
        position += 1
    require(text[position] == "{", "Excerpt source is not an object")
    position += 1
    while True:
        while text[position].isspace():
            position += 1
        if text[position] == "}":
            break
        key, position = decoder.raw_decode(text, position)
        while text[position].isspace():
            position += 1
        require(text[position] == ":", "Malformed object member")
        position += 1
        while text[position].isspace():
            position += 1
        start = position
        _, position = decoder.raw_decode(text, position)
        if key == wanted:
            start_bytes = len(text[:start].encode("utf-8"))
            end_bytes = len(text[:position].encode("utf-8"))
            return raw[start_bytes:end_bytes], start_bytes, end_bytes
        while text[position].isspace():
            position += 1
        if text[position] == ",":
            position += 1
        else:
            require(text[position] == "}", "Malformed object separator")
            break
    raise VerificationError(f"Missing original JSON member: {wanted}")



def inspect_manifest(capture, record):
    """Byte/custody checks only; no Project oracle is inferred here."""
    verify_declared_files(capture, record["source_files"])
    manifest = parse_json((capture / "manifest.json").read_bytes())
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1, "Unknown capture schema")
    entries = manifest["entries"]
    require(type(entries) is list, "Missing capture entries")
    # storeTree uses JSON.stringify in the source helper. Entry values have
    # only strings and integer byte counts; insertion order is preserved.
    tree_bytes = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    digest = sha256_bytes(tree_bytes)
    require(digest == manifest["sourceBeforeSha256"]
              == manifest["sourceAfterSha256"] == manifest["snapshotSha256"],
              "Capture tree digest or before/after identity differs")
    lifecycle = manifest.get("lifecycle")
    require(type(lifecycle) is list and lifecycle, "Missing closure evidence")
    for participant in lifecycle:
        require(participant.get("taints") == [] and participant.get("exits"),
                  "A capture participant was tainted or has no observed exit")
        require(source_path(participant["databasePath"]) == source_path(source_database(manifest)),
                "Closure receipt belongs to another database")
        for result in participant["exits"]:
            require(type(result.get("code")) is int and result["code"] == 0
                      and result.get("signal") is None, "A process did not exit cleanly")
    declared_files, declared_directories, names = {}, set(), set()
    for entry in entries:
        kind, name = entry["kind"], entry["path"]
        safe_relative(name, allow_empty=kind == "directory")
        require(name not in names, "Duplicate file/directory capture entry")
        names.add(name)
        if kind == "file":
            declared_files[name] = {"bytes": entry["sizeBytes"], "sha256": entry["sha256"]}
        else:
            require(kind == "directory", "Unknown capture entry kind")
            declared_directories.add(name)
    store = capture / "store"
    verify_declared_files(store, declared_files)
    actual_directories = {""} | {p.relative_to(store).as_posix()
                                      for p in store.rglob("*") if p.is_dir()}
    require(actual_directories == declared_directories, "Directory inventory differs")
    database = store / safe_relative(manifest["databaseRelativePath"])
    require(database.is_file(), "No captured database")
    media = verify_cas(store / "blobs")
    return manifest, database, media



def seal(args):
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    require(not output.exists(), "Seal output must not exist")
    output.mkdir(parents=True)
    classification = browser_inventory(args.browser_report.resolve(strict=True), source)
    original_inputs, payloads, provenance = {}, {}, []

    def add_source(path, member=None):
        raw = path.read_bytes()
        relative = path.relative_to(source).as_posix()
        original_inputs[relative] = identity_bytes(raw)
        key = (relative, member)
        for existing in provenance:
            if (existing["source"], existing.get("member")) == key:
                return existing["archive_path"]
        index = len(provenance)
        suffix = path.suffix if member is None else ".json"
        name = f"source-evidence/{index:03d}{suffix}"
        entry = {"source": relative, "source_identity": identity_bytes(raw), "archive_path": name}
        if member is None:
            included = raw
            entry["selection"] = "entire_original_file"
        else:
            pointer = (member,) if type(member) is str else member
            included, start, end = exact_pointer(raw, pointer)
            entry.update({"selection": "exact_original_top_level_value_bytes" if type(member) is str
                          else "exact_original_json_value_bytes", "member": member,
                          "json_pointer": "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in pointer),
                          "source_byte_range": [start, end],
                          "omitted_source_scope": "Other original members, including transport transcripts/grants, are not included."})
        entry["included_identity"] = identity_bytes(included)
        payloads[name] = included
        provenance.append(entry)
        return name

    cases_by_directory = {}
    for case in classification["cases"]:
        for directory in case["artifact_directories"]:
            require(directory not in cases_by_directory, "Ambiguous test attachment directory")
            cases_by_directory[directory] = case
    missing_attachment_title = "complete capture includes every file and a paginated full oracle after all known peers close"
    records = []
    fallback_used = False
    excluded_non_capture_manifests = []
    for path in sorted(source.rglob("manifest.json")):
        manifest = parse_json(path.read_bytes())
        capture_keys = ("sourceBeforeSha256", "sourceAfterSha256", "snapshotSha256", "lifecycle")
        apparent_capture = (any(key in manifest for key in capture_keys) or (path.parent / "store").is_dir()
                            or re.fullmatch(r"closed-store(?:-\d+)?|closed-target-store|original-closed-store", path.parent.name))
        if not apparent_capture:
            excluded_non_capture_manifests.append(path.relative_to(source).as_posix())
            continue
        require(all(key in manifest for key in capture_keys), "Incomplete closed capture manifest")
        capture = path.parent
        require(source_path(manifest["snapshotDirectory"]) == source_path(str(capture / "store")),
                "Original manifest does not identify this capture store")
        relative = capture.relative_to(source).as_posix()
        directory = capture.relative_to(source).parts[0]
        case = cases_by_directory.get(directory)
        mapping_basis = "browser_result_attachment_path"
        if case is None:
            candidates = [item for item in classification["cases"] if item["file"] == "scoped-change-capture.spec.ts"
                          and item["title"] == missing_attachment_title and item["attachment_count"] == 0
                          and not item["artifact_directories"]
                          and not item["source_attachment_paths"]
                          and not any(row["test"]["test_id"] == item["test_id"]
                                      and row["test"]["project_id"] == item["project_id"] for row in records)]
            require(len(candidates) == 1 and not fallback_used and capture.name == "closed-store-1"
                      and directory.startswith("scoped-change-capture-comp-"),
                      "Capture cannot be uniquely tied to its browser result")
            case = candidates[0]
            fallback_used = True
            mapping_basis = "unique_source_test_title_and_artifact_slug; test_has_no_attachment"
        require(case["result"] == "passed" and case["transport"] == "real_jsonl",
                  "Closed capture test did not pass over the real transport")
        has_oracle = (capture / "oracle.json").is_file()
        record = {"index": len(records), "source": relative,
                  "archive_path": f"closed-stores/{len(records):03d}",
                  "source_files": files(capture), "oracle_present": has_oracle,
                  "test": {key: value for key, value in case.items()
                           if key not in {"artifact_directories", "source_attachment_paths"}},
                  "test_mapping_basis": mapping_basis, "evidence": {}}
        inspect_manifest(capture, record)
        file = case["file"]
        if file == "scoped-change-capture.spec.ts" and capture.name == "original-closed-store":
            require(not has_oracle and not (capture / "other-project-oracles.json").exists(),
                    "A custody-only healthy control has unexpected Project oracle data")
        if has_oracle:
            record["category"] = "standalone_oracle"
            oracle = parse_json((capture / "oracle.json").read_bytes())
            primary = {**record, "project_id": oracle["project"]["id"], "revision": oracle["project"]["revision"]}
            inspect_capture(capture, primary)
        elif file == "delivery-recipe.spec.ts":
            record["category"] = "original_delivery_zip_expectations"
            original_zip = capture.with_suffix(".zip")
            with zipfile.ZipFile(original_zip) as bundle:
                validated_zip_entries(bundle, MAX_INPUT_TREE_BYTES)
                require(bundle.testzip() is None, "Original delivery ZIP CRC mismatch")
                require(bundle.read("manifest.json") == path.read_bytes(), "Delivery ZIP belongs to a different capture")
                expected = parse_json(bundle.read("readback-expectations.json"))
                require(expected["databasePath"] == source_database(manifest),
                          "Delivery expectations refer to another source store")
                names = {entry.filename for entry in bundle.infolist() if not entry.is_dir()}
                wanted = set(record["source_files"]) | {"readback-expectations.json"}
                require(names == wanted, "Delivery ZIP has unindexed/missing files")
                for name, identity in record["source_files"].items():
                    require(identity_bytes(bundle.read(name)) == identity, "Delivery ZIP bytes differ from original capture")
            record["evidence"]["delivery_zip"] = add_source(original_zip)
        elif file == "project-mutation.spec.ts":
            record["category"] = "original_mutation_observations"
            original_evidence = capture.parent / "canonical-mutation-real-service-evidence.json"
            record["evidence"]["mutation_observations"] = add_source(original_evidence, "observations")
            if (capture.parent / "portable-export.motionwright" / "manifest.json").exists():
                record["evidence"]["portable_manifest"] = add_source(capture.parent / "portable-export.motionwright" / "manifest.json")
        elif file == "project-lifecycle-service.spec.ts":
            record["category"] = "deliberately_empty_library"
        elif file == "scoped-change-capture.spec.ts" and capture.name == "original-closed-store":
            record["category"] = "healthy_control_for_deliberately_damaged_copy"
            original_evidence = capture.parent / "capture-lifecycle-negative.json"
            record["evidence"]["negative_observed"] = add_source(original_evidence, "observed")
            observed = parse_json(payloads[record["evidence"]["negative_observed"]])
            require(observed.get("damage") in {"middle", "tail", "all", "foreign_identity", "invalid_json"},
                      "Unrecognized deliberate negative control")
            record["negative_scope"] = {**observed,
                "captured_store": "healthy original before separate damaged-copy mutation",
                "readback_claim": "custody only; no complete structured pre-closure oracle is claimed for this control"}
            outcome = capture.parent / "rejected-damaged-finalization-outcomes.json"
            require(observed["damage"] not in {"middle", "tail", "all"} or outcome.is_file(),
                    "Original journal-loss rejection outcome is missing")
            if outcome.exists():
                record["evidence"]["negative_outcomes"] = add_source(outcome)
                result = parse_json(outcome.read_bytes())
                require(result["oracle"] is None and result["capture"] is None,
                          "Deliberately damaged store unexpectedly received a complete oracle/capture")
        else:
            raise VerificationError("Unclassified capture without oracle: " + relative)
        if file == "project-lifecycle-service.spec.ts":
            lifecycle = capture.parent / "project-lifecycle-evidence.json"
            for member in ("status", "projectIndex", "completeProjectOracles"):
                record["evidence"][member] = add_source(lifecycle, member)
            require(parse_json(payloads[record["evidence"]["status"]]) == "passed", "Lifecycle original result did not pass")
            if not has_oracle:
                index = parse_json(payloads[record["evidence"]["projectIndex"]])
                oracles = parse_json(payloads[record["evidence"]["completeProjectOracles"]])
                require(index == {"items": [], "next_cursor": None, "truncated": False} and oracles == [],
                          "No-oracle lifecycle store was not explicitly empty")
                record["evidence"]["empty_closure_outcomes"] = add_source(capture.parent / "empty-store-outcomes.json")
        records.append(record)
    require(bool(records), "No complete closed-store captures were found")
    apparent_directories = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_dir()
                            and re.fullmatch(r"closed-store(?:-\d+)?|closed-target-store|original-closed-store", path.name)}
    require(apparent_directories <= {record["source"] for record in records},
            "An apparent closed-store directory has no complete capture manifest")
    oracle_sources = {p.parent.relative_to(source).as_posix() for p in source.rglob("oracle.json")}
    require(oracle_sources == {record["source"] for record in records if record["oracle_present"]},
              "Orphan or uncounted oracle")
    declarations = declared_capture_inventory(classification, source, records, add_source)
    index = {"schema": "motionwright.complete-store-custody/1", "source_commit_operator_supplied": args.source_commit,
             "scope": "All complete closed-store capture manifests, including deliberate empty cases and healthy controls. Archive inclusion alone is not a Project/history readback PASS.",
             "category_counts": dict(Counter(record["category"] for record in records)),
             "closed_stores": len(records), "oracle_present": sum(record["oracle_present"] for record in records),
             "source_evidence": provenance, "excluded_non_capture_manifests": excluded_non_capture_manifests,
             "original_capture_declarations": declarations, "declared_capture_inventory_equal": True,
             "records": records}
    payloads["browser-classification.json"] = encoded(classification)
    payloads["custody-index.json"] = encoded(index)
    archive_path = output / "closed-stores-custody.zip"
    file_manifest = {name: identity_bytes(raw) for name, raw in payloads.items()}
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for record in records:
            capture = source / record["source"]
            for path in sorted(capture.rglob("*")):
                require(not path.is_symlink(), "Capture contains a symlink")
                name = record["archive_path"] + "/" + path.relative_to(capture).as_posix()
                archive.write(path, name)
                if path.is_file():
                    file_manifest[name] = record["source_files"][path.relative_to(capture).as_posix()]
        for name, raw in payloads.items():
            archive.writestr(name, raw)
        archive.writestr("SHA256-MANIFEST.json", encoded(file_manifest))
    extracted = output / "custody-verification-extracted"
    extracted.mkdir()
    safe_extract(archive_path, extracted, 512 * 1024 * 1024)
    actual = files(extracted)
    require({name: value for name, value in actual.items() if name != "SHA256-MANIFEST.json"} == file_manifest,
              "Custody extraction differs from every-file manifest")
    for record in records:
        inspect_manifest(extracted / record["archive_path"], record)
        require(files(source / record["source"]) == record["source_files"], "An original capture changed")
    for name, original in original_inputs.items():
        require(identity_bytes((source / name).read_bytes()) == original, "An original evidence source changed")
    verification = {"schema": "motionwright.custody-archive-verification/1", "source_commit": args.source_commit,
                    "archive": archive_path.name, "archive_identity": {"bytes": archive_path.stat().st_size, "sha256": sha256_file(archive_path)},
                    "closed_stores": len(records), "category_counts": index["category_counts"],
                    "all_capture_manifests_verified": True, "all_capture_files_and_directories_preserved": True,
                    "all_helper_process_exits_clean": True, "all_sources_unchanged": True,
                    "zip_crc_verified": True, "all_extracted_files_sha256_verified": True,
                    "includes_manifest_identity": True, "files": actual,
                    "independent_project_readback": "pending; inclusion is custody only"}
    (output / "custody-archive-verification.json").write_bytes(encoded(verification))
    (output / "capture-inventory.json").write_bytes(encoded(index))
    (output / "browser-classification.json").write_bytes(encoded(classification))
    return archive_path, verification, index



def validate_expected(project, history, *, full_oracle=None):
    require(type(project) is dict and type(history) is list, "Missing original Project/history")
    for key in ("id", "generation", "active_branch"):
        require(type(project.get(key)) is str and bool(project[key].strip()), "Invalid Project identity")
    revision = project.get("revision")
    require(type(revision) is int and revision >= 0, "Invalid Project revision")
    require(len(history) == revision and all(type(event.get("revision")) is int
              and event["revision"] == index + 1 for index, event in enumerate(history)),
              "Original journal is not complete and contiguous from revision 1")
    if full_oracle is not None:
        require(full_oracle.get("schemaVersion") == 1 and full_oracle.get("journalComplete") is True
                  and type(full_oracle.get("journalAfterRevisionExclusive")) is int
                  and full_oracle["journalAfterRevisionExclusive"] == 0, "Original oracle is not complete")



def expected_projects(extracted, captured, record, manifest):
    """Read source-format selectors; do not create or persist new oracle data."""
    selected = {}
    def add(project, history, origin, cas=None, full_oracle=None):
        validate_expected(project, history, full_oracle=full_oracle)
        expected = {"project": project, "history": history, "origin": origin, "cas": cas}
        prior = selected.get(project["id"])
        if prior is not None:
            require(canonical(prior["project"]) == canonical(project)
                      and canonical(prior["history"]) == canonical(history),
                      "Original expectation sources disagree")
            if cas is not None:
                require(prior["cas"] is None or canonical(prior["cas"]) == canonical(cas),
                        "Original per-asset CAS expectations disagree")
                prior["cas"] = cas
        else:
            selected[project["id"]] = expected

    def add_oracle(oracle, origin):
        add(oracle["project"], oracle["journal"], origin, full_oracle=oracle)

    if record["oracle_present"]:
        oracle = parse_json((captured / "oracle.json").read_bytes())
        add_oracle(oracle, "capture/oracle.json")
    sidecar = captured / "other-project-oracles.json"
    if sidecar.exists():
        for index, oracle in enumerate(parse_json(sidecar.read_bytes())):
            add_oracle(oracle, f"capture/other-project-oracles.json/{index}")
    evidence = record["evidence"]
    if "completeProjectOracles" in evidence:
        for index, oracle in enumerate(parse_json((extracted / evidence["completeProjectOracles"]).read_bytes())):
            add_oracle(oracle, f"original lifecycle /completeProjectOracles/{index}")
    if "delivery_zip" in evidence:
        with zipfile.ZipFile(extracted / evidence["delivery_zip"]) as original:
            validated_zip_entries(original, MAX_INPUT_TREE_BYTES)
            require(original.testzip() is None, "Original delivery ZIP failed CRC")
            require(original.read("manifest.json") == (captured / "manifest.json").read_bytes(), "Wrong delivery capture")
            source = parse_json(original.read("readback-expectations.json"))
        require(source["databasePath"] == source_database(manifest),
                  "Wrong delivery source database")
        for index, expected in enumerate(source["projects"]):
            add(expected["project"], expected["history"], f"original delivery ZIP readback-expectations.json/projects/{index}", expected["cas"])
    if "mutation_observations" in evidence:
        observations = parse_json((extracted / evidence["mutation_observations"]).read_bytes())
        require(any(canonical(item) == canonical(manifest) for item in observations["closedStoreCaptures"]),
                  "Mutation observation belongs to another capture")
        require("closeFailures" not in observations and "captureFailure" not in observations, "Original mutation closure failed")
        if "portable_manifest" in evidence and source_path(manifest["sourceDirectory"]).name == "source-service":
            raw = (extracted / evidence["portable_manifest"]).read_bytes()
            require(sha256_bytes(raw) == observations["manifest"]["sha256"], "Portable backup identity differs")
            original = parse_json(raw)
            require(canonical(original["backup"]["project"]) == canonical(observations["source"]), "Portable source Project differs")
            add(original["backup"]["project"], original["backup"]["events"], "original portable manifest /backup")
        else:
            reopen = observations["reopen"]
            require(reopen["verification"] == "new_process_reopen_of_unmodified_complete_store", "No original process reopen observation")
            require(canonical(reopen["project"]) == canonical(observations["final"]), "Original final/reopen Projects disagree")
            add(reopen["project"], reopen["history"], "original mutation /observations/reopen", reopen["cas"])
            if "portable_manifest" in evidence:
                require(source_path(manifest["sourceDirectory"]).name == "target-service", "Unknown portable target store")
                add_oracle(observations["targetOriginalOracle"], "original mutation /observations/targetOriginalOracle")
    return selected



def verify_expected_cas(expected, media):
    project = expected["project"]
    assets = project.get("assets")
    require(type(assets) is list, "Original Project has no complete asset array")
    by_id = {}
    for asset in assets:
        digest = asset.get("content_sha256")
        require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "Original asset has no CAS digest")
        require(asset["id"] not in by_id, "Duplicate original asset identity")
        name = f"sha256/{digest[:2]}/{digest}"
        require(name in media and media[name]["sha256"] == digest, "Referenced Project media is absent or corrupt")
        by_id[asset["id"]] = {"sha256": digest, "size_bytes": media[name]["bytes"]}
    if expected["cas"] is not None:
        recorded = {}
        for item in expected["cas"]:
            require(item["asset_id"] not in recorded, "Duplicate expected CAS asset")
            recorded[item["asset_id"]] = {"sha256": item["sha256"], "size_bytes": item["size_bytes"]}
        require(canonical(recorded) == canonical(by_id), "Original per-asset CAS evidence differs")
    return len(by_id)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def no_links(path):
    """Reject POSIX links and every Windows reparse point before resolution."""
    path = Path(path)
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, "st_file_attributes", 0) & 0x400),
                f"Link or reparse point is forbidden: {item}")


def safe_relative(value, *, allow_empty=False):
    require(type(value) is str, "A relative path must be a string")
    if value == "" and allow_empty:
        return PurePosixPath("")
    require(value and not value.startswith("/") and "\\" not in value,
            "A relative path must be canonical POSIX syntax")
    parts = value[:-1].split("/") if value.endswith("/") else value.split("/")
    for part in parts:
        require(part not in ("", ".", "..") and part[-1:] not in (" ", ".")
                and not any(ord(char) < 32 or char in '<>:"|?*' for char in part),
                f"Unsafe or nonportable path component: {part!r}")
        require(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part),
                f"Windows device path is forbidden: {part!r}")
    return PurePosixPath(*parts)


def source_path(value):
    require(type(value) is str and "\x00" not in value, "Invalid original source path")
    path = PureWindowsPath(value) if "\\" in value or re.match(r"^[A-Za-z]:", value) else PurePosixPath(value)
    require(path.is_absolute() and ".." not in path.parts, "Original source path is not absolute")
    return path


def source_database(manifest):
    return str(source_path(manifest["sourceDirectory"]) / safe_relative(manifest["databaseRelativePath"]))


def sha256_file(path):
    no_links(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode), f"Not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            f"File changed during hashing: {path}")
    return digest.hexdigest()


def file_identity(path):
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


def tree(root):
    """Inventory regular files and all directories with fixed resource bounds."""
    no_links(root)
    if not root.exists():
        return {}, set()
    require(root.is_dir(), f"Not a directory: {root}")
    inventory, directories, count, total = {}, {""}, 0, 0
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        folded = set()
        for name in sorted(subdirs + filenames):
            count += 1
            require(count <= MAX_ENTRIES, "Input tree contains too many entries")
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            require(name.casefold() not in folded, "Case-insensitive path collision")
            folded.add(name.casefold())
            no_links(path)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
            else:
                require(stat.S_ISREG(info.st_mode), "Input contains a non-regular file")
                total += info.st_size
                require(info.st_size <= MAX_INPUT_FILE_BYTES and total <= MAX_INPUT_TREE_BYTES,
                        "Input file/tree byte budget exceeded")
                inventory[relative] = file_identity(path)
        subdirs.sort()
    return inventory, directories


def files(root):
    return tree(root)[0]


def checked_json_file(path):
    no_links(path)
    require(path.is_file() and path.stat().st_size <= MAX_INPUT_FILE_BYTES,
            "JSON source is not a bounded regular file")
    return parse_json(path.read_bytes())


def write_new_json(path, value):
    no_links(path)
    with path.open("xb") as stream:
        stream.write(encoded(value))


def validated_zip_entries(bundle, maximum):
    entries = bundle.infolist()
    require(len(entries) <= MAX_ENTRIES and sum(item.file_size for item in entries) <= maximum,
            "ZIP entry/byte budget exceeded")
    names, prefixes = set(), {}
    for entry in entries:
        path = safe_relative(entry.filename)
        key = path.as_posix().casefold()
        require(key not in names, "Duplicate or case-colliding ZIP entry")
        names.add(key)
        require(not entry.flag_bits & 1, "Encrypted ZIP entries are forbidden")
        mode = entry.external_attr >> 16
        require(not mode or stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                "ZIP links and special files are forbidden")
        require(entry.file_size <= MAX_INPUT_FILE_BYTES, "ZIP member byte budget exceeded")
        for index in range(1, len(path.parts) + 1):
            prefix = PurePosixPath(*path.parts[:index]).as_posix()
            is_directory = index < len(path.parts) or entry.is_dir()
            prior = prefixes.setdefault(prefix.casefold(), (prefix, is_directory))
            require(prior == (prefix, is_directory), "Conflicting ZIP file/directory prefix")
    return entries


def safe_extract(archive, destination, max_bytes):
    no_links(archive)
    no_links(destination)
    require(destination.is_dir() and not any(destination.iterdir()), "Extraction directory is not fresh")
    with zipfile.ZipFile(archive) as bundle:
        entries = validated_zip_entries(bundle, max_bytes)
        total = 0
        for entry in entries:
            path = destination / safe_relative(entry.filename)
            no_links(path)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with bundle.open(entry) as source, path.open("xb") as target:
                while chunk := source.read(min(65536, entry.file_size - size + 1)):
                    size += len(chunk)
                    total += len(chunk)
                    require(size <= entry.file_size and total <= max_bytes,
                            "ZIP decompression exceeded its declared byte budget")
                    target.write(chunk)
            require(size == entry.file_size, "Truncated ZIP member")


def browser_inventory(report_path, artifacts):
    raw = report_path.read_bytes()
    report = parse_json(raw)
    require(report.get("errors", []) == [], "Browser report has global errors")
    cases, seen = [], set()

    def visit(suite):
        for spec in suite.get("specs", []):
            filename = PurePosixPath(spec["file"].replace("\\", "/")).name
            for test in spec["tests"]:
                require(filename in REAL_FILES or (filename == "project-mutation.spec.ts"
                        and spec["title"].startswith("real ")),
                        "Browser report includes a case outside the real JSONL suites")
                require(test["status"] == "expected" and test.get("expectedStatus", "passed") == "passed"
                        and len(test["results"]) == 1, "Case was skipped, failed, expected to fail or retried")
                result = test["results"][0]
                require(result["status"] == "passed" and result.get("retry", 0) == 0
                        and not result.get("error") and result.get("errors", []) == [],
                        "Browser attempt is not an unretried success")
                identity = (spec["id"], test.get("projectId", test.get("projectName", "")))
                require(identity not in seen, "Duplicate browser case/project identity")
                seen.add(identity)
                item = {"file": filename, "title": spec["title"], "test_id": spec["id"],
                        "project_id": identity[1], "line": spec["line"], "status": test["status"],
                        "result": result["status"], "transport": "real_jsonl", "artifact_directories": [],
                        "source_attachment_paths": [], "attachment_count": len(result.get("attachments", []))}
                for attachment in result.get("attachments", []):
                    if "path" not in attachment:
                        continue
                    path = Path(attachment["path"])
                    require(path.is_absolute(), "Attachment path is not absolute")
                    no_links(path)
                    require(path.is_file(), "Attachment is not a regular file")
                    relative = path.resolve(strict=True).relative_to(artifacts)
                    require(len(relative.parts) >= 2, "Attachment has no distinct case directory")
                    item["source_attachment_paths"].append(relative.as_posix())
                    directory = relative.parts[0]
                    if directory not in item["artifact_directories"]:
                        item["artifact_directories"].append(directory)
                cases.append(item)
                require(len(cases) <= MAX_REQUESTS, "Browser case budget exceeded")
        for child in suite.get("suites", []):
            visit(child)

    for suite in report["suites"]:
        visit(suite)
    stats = report["stats"]
    require(bool(cases) and stats["expected"] == len(cases) and stats["unexpected"] == 0
            and stats["flaky"] == 0 and stats["skipped"] == 0,
            "Browser totals do not describe only unretried successful JSONL cases")
    return {"schema": "motionwright.browser-transport-classification/1",
            "source_report": report_path.name, "source_report_sha256": sha256_bytes(raw),
            "stats": {**stats, "duration": str(stats["duration"])},
            "counts": {"real_jsonl_passed": len(cases)},
            "classification_basis": "Source opt-in suites and original browser result attachment paths; expected case identity is verified upstream.",
            "cases": cases}


def exact_pointer(raw, pointer):
    """Select existing nested JSON bytes, preserving original numeric spelling."""
    selected, offset = raw, 0
    for part in pointer:
        if type(part) is str:
            selected, start, _ = exact_member(selected, part)
        else:
            require(type(part) is int and part >= 0, "Invalid original JSON array selector")
            array = parse_json(selected)
            require(type(array) is list and part < len(array), "Original JSON array selector is out of range")
            text = selected.decode("utf-8")
            position = text.index("[") + 1
            decoder = json.JSONDecoder(parse_float=Decimal)
            for index in range(part + 1):
                while text[position].isspace():
                    position += 1
                begin = position
                _, position = decoder.raw_decode(text, position)
                if index < part:
                    while text[position].isspace():
                        position += 1
                    require(text[position] == ",", "Malformed original JSON array")
                    position += 1
            start, end = len(text[:begin].encode("utf-8")), len(text[:position].encode("utf-8"))
            selected = selected[start:end]
        offset += start
    return selected, offset, offset + len(selected)


def declared_capture_inventory(classification, source, records, add_source):
    """Require a bijection with original declarations; never infer Project data."""
    declarations, verified = [], set()
    positive_title = "complete capture includes every file and a paginated full oracle after all known peers close"
    manifest_keys = {"sourceBeforeSha256", "sourceAfterSha256", "snapshotSha256", "lifecycle"}
    for case in classification["cases"]:
        actual = {row["source"]: row for row in records if row["test"]["test_id"] == case["test_id"]
                  and row["test"]["project_id"] == case["project_id"]}
        selected = set()
        node_count = 0

        def register(relative, *, basis, original=None, attachment=None, pointer=()):
            require(relative in actual, "An original declared capture is absent or belongs to another browser case")
            record = actual[relative]
            if original is not None:
                manifest = checked_json_file(source / relative / "manifest.json")
                require(canonical(manifest) == canonical(original), "Original attachment capture declaration differs from its manifest")
            row = {"source": relative, "test_id": case["test_id"], "project_id": case["project_id"], "basis": basis,
                   "manifest_identity": record["source_files"]["manifest.json"]}
            if attachment is not None:
                row["original_value"] = add_source(attachment, pointer)
            declarations.append(row)
            selected.add(relative)

        def walk(value, attachment, pointer):
            nonlocal node_count
            node_count += 1
            require(node_count <= 1_000_000, "Original declaration traversal budget exceeded")
            if type(value) is dict:
                if (manifest_keys - {"lifecycle"}) & value.keys():
                    require(manifest_keys <= value.keys(), "Original attachment contains a partial capture declaration")
                    snapshot = source_path(value["snapshotDirectory"])
                    require(snapshot.name == "store", "Original capture snapshot is not a complete store directory")
                    relative = snapshot.parent.relative_to(source_path(str(source))).as_posix()
                    register(relative, basis="original_attached_capture_manifest", original=value,
                             attachment=attachment, pointer=pointer)
                    return
                for key, child in value.items():
                    if key not in {"transcript", "services", "stderr"}:
                        walk(child, attachment, (*pointer, key))
            elif type(value) is list:
                for index, child in enumerate(value):
                    walk(child, attachment, (*pointer, index))

        for relative in case["source_attachment_paths"]:
            attachment = source / safe_relative(relative)
            if attachment.suffix.lower() != ".json":
                continue
            original = checked_json_file(attachment)
            if type(original) is not dict:
                continue
            for key in ("capture", "captures", "finalization", "stores", "observations"):
                if key in original:
                    walk(original[key], attachment, (key,))
            if case["file"] == "project-lifecycle-service.spec.ts" and attachment.name == "project-lifecycle-evidence.json":
                require(original.get("status") == "passed" and type(original.get("completeProjectOracles")) is list,
                        "Original lifecycle completion evidence is absent")
                register((attachment.parent / "closed-store-1").relative_to(source).as_posix(),
                         basis="original_lifecycle_fixture_output_path", attachment=attachment, pointer=("projectIndex",))
            observed = original.get("observed")
            if (case["file"] == "scoped-change-capture.spec.ts" and attachment.name == "capture-lifecycle-negative.json"
                    and type(observed) is dict and observed.get("damage") in {"middle", "tail", "all", "foreign_identity", "invalid_json"}):
                damage = observed["damage"]
                if damage in {"middle", "tail", "all"}:
                    expected_title = f"a {damage} journal loss in an isolated SQLite copy cannot receive a complete oracle"
                    expected_fixture = "deliberate_journal_loss_in_separate_sqlite_copy"
                else:
                    expected_title = f"a {damage} project row cannot redirect reads or mutations and healthy projects remain usable"
                    expected_fixture = "deliberately_damaged_project_row_in_separate_sqlite_copy"
                require(case["title"] == expected_title and observed.get("fixture") == expected_fixture,
                        "Healthy control does not match its original negative fixture")
                register((attachment.parent / "original-closed-store").relative_to(source).as_posix(),
                         basis="original_negative_fixture_output_path", attachment=attachment, pointer=("observed",))
        if case["file"] == "scoped-change-capture.spec.ts" and case["title"] == positive_title:
            require(case["attachment_count"] == 0 and not case["source_attachment_paths"] and not case["artifact_directories"],
                    "The original attachment-free capture fixture unexpectedly has attachments")
            directories = [path for path in source.iterdir() if path.is_dir() and path.name.startswith("scoped-change-capture-comp-")]
            require(len(directories) == 1, "Original attachment-free fixture directory is missing or ambiguous")
            register((directories[0] / "closed-store-1").relative_to(source).as_posix(),
                     basis="original_attachment_free_fixture_output_path")
        require(selected == set(actual), "A capture has no original declaration for this browser case")
        if case["file"] != "scoped-change-capture.spec.ts":
            require(bool(selected), "Successful service case has no original declared capture")
        verified.update(selected)
    require(verified == {row["source"] for row in records}, "Original declarations do not cover every closed capture")
    return declarations


class ReadbackUnknown(VerificationError):
    """An incomplete process observation is retained and never replayed here."""


def run_jsonl(runner, database, checks, logs, remaining_budget):
    require(0 < len(checks) <= MAX_REQUESTS, "Readback request count budget exceeded")
    wire = b"".join((json.dumps(item, separators=(",", ":")) + "\n").encode("utf-8") for item in checks)
    require(len(wire) <= MAX_REQUEST_BYTES, "Readback request byte budget exceeded")
    require(remaining_budget > MAX_STDERR_BYTES + len(wire), "Aggregate process log budget exhausted")
    maximum = min(MAX_STDOUT_BYTES, remaining_budget - MAX_STDERR_BYTES - len(wire))
    logs.mkdir(parents=True, exist_ok=False)
    temp = logs / "tmp"
    temp.mkdir()
    request_path, stdout_path, stderr_path = (logs / name for name in ("requests.jsonl", "responses.jsonl", "stderr.log"))
    with request_path.open("xb") as stream:
        stream.write(wire)
    stdout, stderr = stdout_path.open("xb"), stderr_path.open("xb")
    started_at, started = utc_now(), time.monotonic()
    overflow, errors, counts = threading.Event(), [], [0, 0]
    process, threads, timed_out, incomplete = None, [], False, False
    command = [str(runner), "--database", str(database)]

    def stop():
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError as error:
                if process.poll() is None:
                    errors.append("kill_" + type(error).__name__)

    def drain(source, target, index, limit):
        try:
            while chunk := source.read(min(16384, limit - counts[index] + 1)):
                accepted = min(len(chunk), limit - counts[index])
                require(target.write(chunk[:accepted]) == accepted, "Short log write")
                counts[index] += accepted
                if len(chunk) != accepted:
                    overflow.set()
                    stop()
                    break
        except (OSError, ValueError, VerificationError) as error:
            errors.append("pipe_" + type(error).__name__)
            stop()
        finally:
            try:
                source.close()
                target.flush()
            except (OSError, ValueError) as error:
                errors.append("flush_" + type(error).__name__)

    try:
        environment = dict(os.environ)
        environment.update({"TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)})
        with request_path.open("rb") as requests:
            process = subprocess.Popen(command, stdin=requests, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       bufsize=0, cwd=logs, env=environment)
        threads = [threading.Thread(target=drain, args=(source, target, index, limit), daemon=True)
                   for index, (source, target, limit) in enumerate(((process.stdout, stdout, maximum),
                                                                  (process.stderr, stderr, MAX_STDERR_BYTES)))]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                incomplete = True
        for thread in threads:
            thread.join(timeout=5)
        incomplete |= any(thread.is_alive() for thread in threads)
    except BaseException as error:
        errors.append("start_or_wait_" + type(error).__name__)
        stop()
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                incomplete = True
        for thread in threads:
            thread.join(timeout=5)
        incomplete |= any(thread.is_alive() for thread in threads)
    result = {"command": command, "process_id": process.pid if process else None,
              "started_at": started_at, "observed_at": utc_now(), "elapsed_seconds": time.monotonic() - started,
              "exit_code": process.returncode if process else None, "timed_out": timed_out,
              "timeout_seconds": TIMEOUT_SECONDS, "output_limit_exceeded": overflow.is_set(),
              "prefix_only": overflow.is_set() or incomplete, "pipe_errors": errors,
              "pipe_cleanup_incomplete": incomplete, "retry_attempted": False,
              "captured_bytes": {"stdout": counts[0], "stderr": counts[1]},
              "maximum_bytes": {"stdout": maximum, "stderr": MAX_STDERR_BYTES},
              "stdin_bytes": len(wire), "stdin_sha256": sha256_bytes(wire)}
    result["complete"] = (result["exit_code"] == 0 and not timed_out and not overflow.is_set()
                          and not errors and not incomplete)
    result["state"] = "PASS" if result["complete"] else "UNKNOWN" if timed_out or incomplete else "FAIL"
    if not incomplete:
        stdout.close()
        stderr.close()
        result.update({"stdout": file_identity(stdout_path), "stderr": file_identity(stderr_path)})
    else:
        # Live drainers still own the handles. Their prefixes are not immutable evidence.
        result.update({"stdout": None, "stderr": None})
    write_new_json(logs / "process.json", result)
    if result["state"] == "UNKNOWN":
        raise ReadbackUnknown(f"Read-only process observation is incomplete; no retry: {logs.name}")
    require(result["complete"], f"Read-only process failed; original process receipt retained: {logs.name}")
    require(sha256_file(request_path) == sha256_bytes(wire), "Readback requests changed")
    return stdout_path.read_bytes(), result


def readback_plan(expected, media):
    checks = [{"id": 0, "command": "list_projects", "args": {"request": {"cursor": None, "limit": 100}}}]
    layout = []
    for project_id, item in expected.items():
        revision = item["project"]["revision"]
        require(revision // 100 + len(checks) + 3 <= MAX_REQUESTS, "Journal request budget exceeded")
        asset_count = verify_expected_cas(item, media)
        start = len(checks)
        checks.append({"id": len(checks), "command": "get_project", "args": {"request": {"project_id": project_id}}})
        for after in range(0, max(revision, 1), 100):
            checks.append({"id": len(checks), "command": "project_history", "args": {"request": {
                "project_id": project_id, "after_revision": after, "limit": 100}}})
        checks.append({"id": len(checks), "command": "project_history", "args": {"request": {
            "project_id": project_id, "after_revision": revision, "limit": 100}}})
        layout.append((project_id, start, len(checks), asset_count))
    return checks, layout


def compare_responses(raw, checks, layout, expected, original_index=None):
    require(len(raw) <= MAX_STDOUT_BYTES, "Response byte budget exceeded")
    responses = [parse_json(line) for line in raw.splitlines() if line.strip()]
    require(len(responses) == len(checks), "Runner reply count differs")
    for number, response in enumerate(responses):
        require(type(response) is dict and response.get("ok") is True
                and type(response.get("id")) is int and response["id"] == number,
                "Runner rejected or mismatched a read-only request")
    listing = responses[0]["result"]
    require(type(listing) is dict and type(listing.get("items")) is list
            and listing.get("truncated") is False and listing.get("next_cursor") is None,
            "Project listing is incomplete")
    ids = [row["id"] for row in listing["items"]]
    require(len(ids) == len(set(ids)) and set(ids) == set(expected),
            "The reopened library has missing or additional unobserved projects")
    if original_index is not None:
        require(canonical(listing) == canonical(original_index), "Full original project index differs")
    projects = []
    for project_id, start, end, asset_count in layout:
        item = expected[project_id]
        require(canonical(responses[start]["result"]) == canonical(item["project"]), "Full original Project differs")
        history = []
        for response in responses[start + 1:end - 1]:
            require(type(response["result"]) is list, "History response is not an array")
            history.extend(response["result"])
        require(canonical(history) == canonical(item["history"]), "Full original journal differs")
        require(responses[end - 1]["result"] == [], "Journal has uncaptured later events")
        projects.append({"project_id": project_id, "revision": item["project"]["revision"],
                         "original_evidence_selector": item["origin"], "full_project_equal": True,
                         "full_journal_equal": True, "journal_contiguous_and_eof": True,
                         "referenced_cas_assets_verified": asset_count})
    return projects


def empty_counts(database):
    # This is called only on an extracted work copy, never on a captured original.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("projects", "events")}


def readback(args):
    output = args.output.resolve()
    require(not output.exists(), "Readback output must be new")
    archive, runner = args.archive.resolve(strict=True), args.runner.resolve(strict=True)
    require(sha256_file(archive) == args.expected_archive_sha256, "Wrong custody archive")
    require(sha256_file(runner) == args.expected_runner_sha256, "Wrong frozen runner")
    output.mkdir(parents=True)
    report = {"schema": "motionwright.original-formats-readback/1", "all_checks_passed": False,
              "source_commit_operator_supplied": args.source_commit, "archive_sha256": args.expected_archive_sha256,
              "runner_sha256": args.expected_runner_sha256, "stores": [], "custody_only": [],
              "excludes": ["installed_tauri", "native_realization", "human_acceptance",
                           "independent_authentication_of_operator_supplied_runner_source"]}
    try:
        extracted = output / "extracted"
        extracted.mkdir()
        safe_extract(archive, extracted, MAX_INPUT_TREE_BYTES)
        declared = checked_json_file(extracted / "SHA256-MANIFEST.json")
        require({name: value for name, value in files(extracted).items() if name != "SHA256-MANIFEST.json"} == declared,
                "Custody extraction file manifest differs")
        index = checked_json_file(extracted / "custody-index.json")
        require(index["source_commit_operator_supplied"] == args.source_commit, "Custody source identity differs")
        for entry in index["source_evidence"]:
            require(file_identity(extracted / safe_relative(entry["archive_path"])) == entry["included_identity"],
                    "Original expectation bytes changed")
        remaining = MAX_TOTAL_LOG_BYTES
        for position, record in enumerate(index["records"]):
            require(type(record["index"]) is int and record["index"] == position, "Noncontiguous custody record index")
            captured = extracted / safe_relative(record["archive_path"])
            manifest, database, media_before = inspect_manifest(captured, record)
            if record["category"] == "healthy_control_for_deliberately_damaged_copy":
                report["custody_only"].append({"index": position, "source": record["source"],
                    "reason": record["negative_scope"], "test_result": record["test"]["result"],
                    "process_started": False, "full_project_equal": None, "full_journal_equal": None})
                continue
            expected = expected_projects(extracted, captured, record, manifest)
            is_empty = record["category"] == "deliberately_empty_library"
            require(bool(expected) != is_empty, "Unexpected absence/presence of original Project expectations")
            checks, layout = readback_plan(expected, media_before)
            before = empty_counts(database) if is_empty else None
            if is_empty:
                require(before == {"projects": 0, "events": 0}, "Expected empty library contains canonical rows")
            raw, process = run_jsonl(runner, database, checks, output / "readbacks" / f"{position:03d}", remaining)
            remaining -= process["stdin_bytes"] + sum(process["captured_bytes"].values())
            original_index = (checked_json_file(extracted / record["evidence"]["projectIndex"])
                              if "projectIndex" in record["evidence"] else None)
            projects = compare_responses(raw, checks, layout, expected, original_index)
            require(verify_cas(captured / "store" / "blobs") == media_before, "Readback changed CAS files")
            if is_empty:
                require(empty_counts(database) == before, "Read-only reopen populated an empty library")
            report["stores"].append({"index": position, "source": record["source"], "category": record["category"],
                "exit_code": process["exit_code"], "complete_project_index_equal": True, "projects": projects,
                "empty_library_rows_before_after": before, "history_applicable": not is_empty,
                "cas_files_verified": len(media_before), "cas_unchanged": True,
                "stdin_sha256": process["stdin_sha256"], "stdout_sha256": process["stdout"]["sha256"],
                "stderr_sha256": process["stderr"]["sha256"], "process_receipt": f"readbacks/{position:03d}/process.json"})
        require(sha256_file(archive) == args.expected_archive_sha256
                and sha256_file(runner) == args.expected_runner_sha256, "Readback changed archive/runner identity")
        require(bool(report["stores"]), "Custody-only controls do not establish a store readback")
        report.update({"all_checks_passed": True, "stores_reopened": len(report["stores"]),
                       "projects_fully_compared": sum(len(row["projects"]) for row in report["stores"]),
                       "empty_libraries_verified": sum(not row["history_applicable"] for row in report["stores"]),
                       "custody_only_controls": len(report["custody_only"]), "archive_unchanged": True,
                       "runner_unchanged": True, "state": "PASS"})
    except BaseException as error:
        report.update({"error": f"{type(error).__name__}: {error}",
                       "state": "UNKNOWN" if isinstance(error, ReadbackUnknown) else "FAIL"})
        write_new_json(output / "verification.json", report)
        raise
    write_new_json(output / "verification.json", report)
    return report


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-artifacts", type=Path, required=True)
    parser.add_argument("--browser-report", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--expected-runner-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    output_created = False
    receipt = {"schema": "motionwright.closed-store-readback/1", "state": "FAIL", "all_checks_passed": False,
               "source_commit_operator_supplied": args.source_commit, "started_at": utc_now(),
               "expected_browser_inventory_authenticated_here": False,
               "runner_build_source_authenticated_here": False, "formal_acceptance": False,
               "scope": "real_service_sqlite_cas_copy_readback; synthetic_test_content",
               "excludes": ["installed_tauri", "native_realization", "human_acceptance"], "retry_attempted": False}
    try:
        require(bool(re.fullmatch(r"[0-9a-f]{40}", args.source_commit)), "Source binding is not a full commit SHA")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", args.expected_runner_sha256)), "Runner binding is not SHA-256")
        for path in (args.browser_artifacts, args.browser_report, args.runner, args.output):
            require(path.is_absolute(), "All input and output paths must be absolute")
            no_links(path)
        source = args.browser_artifacts.resolve(strict=True)
        report_path, runner = args.browser_report.resolve(strict=True), args.runner.resolve(strict=True)
        output = args.output.resolve()
        require(not output.exists() and source != output and source not in output.parents
                and output not in source.parents, "Output must be fresh and outside browser artifacts")
        require(output not in report_path.parents and output not in runner.parents, "Output overlaps an input")
        require(source.is_dir(), "Browser artifacts are not a directory")
        source_before = tree(source)
        report_before, runner_before = file_identity(report_path), file_identity(runner)
        require(report_before["bytes"] <= MAX_INPUT_FILE_BYTES, "Browser report byte budget exceeded")
        require(runner_before["sha256"] == args.expected_runner_sha256, "Runner SHA-256 differs")
        output.mkdir(parents=True, exist_ok=False)
        output_created = True
        receipt.update({"runner": runner_before, "browser_report": report_before})
        write_new_json(output / "start.json", {**receipt, "state": "STARTED"})
        archive, seal_receipt, index = seal(SimpleNamespace(source=source, browser_report=report_path,
                                                          output=output / "custody", source_commit=args.source_commit))
        receipt.update({"archive": seal_receipt["archive_identity"], "closed_stores": index["closed_stores"],
                        "category_counts": index["category_counts"]})
        result = readback(SimpleNamespace(archive=archive, runner=runner, output=output / "readback",
            expected_archive_sha256=seal_receipt["archive_identity"]["sha256"],
            expected_runner_sha256=args.expected_runner_sha256, source_commit=args.source_commit))
        require(tree(source) == source_before, "Browser artifacts changed during sealing/readback")
        require(file_identity(report_path) == report_before and file_identity(runner) == runner_before,
                "Browser report or runner bytes changed")
        receipt.update({"state": "PASS", "all_checks_passed": True, "all_original_inputs_unchanged": True,
                        "stores_reopened": result["stores_reopened"], "projects_fully_compared": result["projects_fully_compared"],
                        "empty_libraries_verified": result["empty_libraries_verified"],
                        "custody_only_controls": result["custody_only_controls"], "archive_unchanged": True})
    except BaseException as error:
        receipt.update({"error": f"{type(error).__name__}: {error}",
                        "state": "UNKNOWN" if isinstance(error, ReadbackUnknown) else "FAIL"})
    receipt["observed_at"] = utc_now()
    if output_created:
        write_new_json(output / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=True))
    return 0 if receipt["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
