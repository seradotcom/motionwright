#!/usr/bin/env python3
"""Seal Playwright service evidence, verify it, and optionally read a fresh copy.

Runtime-only tool. Python standard library; no repository modifications, npm,
fixture seeding, grants, mutations, or automatic retries. Transport is opt-in.
The observed protocol and capture format come from ScopedChangeService.
"""

import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import zipfile


SCHEMA = "motionwright.service-archive-readback/3"
HELPER_SHA256 = "c5f29e1f840e95ffc6e1013bc34d77b834d4385402f6a8c1e1b962f31de4e914"
SAFE_INTEGER = 9007199254740991
EVIDENCE_NAMES = (
    "brand-authoring-service-evidence.json",
    "component-distillation-service-evidence.json",
    "review-service-evidence.json",
    "branch-authoring-evidence.json",
    "production-design-service-evidence.json",
)
READ_COMMANDS = {"bootstrap", "reopen", "get_project", "project_history"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact(value):
    # storeTree's entries have ordered keys and integer sizes. Do not sort keys:
    # its aggregate digest is SHA256(JSON.stringify(entries)).
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest_json(value):
    return hashlib.sha256(compact(value).encode("utf-8")).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON property: " + key)
        result[key] = value
    return result


def parse_json(text):
    def reject_constant(value):
        raise ValueError("Non-finite JSON number: " + value)
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8"))


def write_json(path, value, exclusive=True):
    path = Path(path)
    pending = path.with_name(path.name + ".pending-" + uuid.uuid4().hex)
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publication occurs only after flush, fsync and close succeeded. A
        # hard link is an exclusive same-filesystem publication on POSIX/NTFS;
        # unlike rename on POSIX, it cannot replace an existing receipt.
        if exclusive:
            os.link(pending, path)
        else:
            os.replace(pending, path)
    except BaseException as error:
        raise OSError("JSON was not published; inspect retained temporary " + str(pending) + ": " + str(error)) from error
    if exclusive:
        try:
            pending.unlink()
        except OSError:
            # The fully closed receipt is already published. Retaining its
            # extra link is harmless and must not turn publication into failure.
            pass


class DurableObservations:
    """Caller-owned facts, independent of per-copy and final JSON receipts."""
    def __init__(self, path):
        self.path, self.sequence, self.confirmed, self.errors = path, 0, 0, []
        self.stream = path.open("x", encoding="utf-8", newline="\n")

    def record(self, kind, case, observation):
        self.sequence += 1
        try:
            self.stream.write(compact({"schema": "motionwright.readback-observation/1", "sequence": self.sequence,
                                       "at": now(), "kind": kind, "case": case, "observation": observation}) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.confirmed += 1
        except BaseException as error:
            self.errors.append("Observation record " + str(self.sequence) + ": " + type(error).__name__ + ": " + str(error))
            raise

    def close(self):
        errors = list(self.errors)
        try:
            self.stream.flush(); os.fsync(self.stream.fileno())
        except BaseException as error:
            errors.append("Observation flush: " + type(error).__name__ + ": " + str(error))
        try:
            self.stream.close()
        except BaseException as error:
            errors.append("Observation close: " + type(error).__name__ + ": " + str(error))
        return errors


def copy_external_file(source, destination, expected_hash=None):
    require(source.is_absolute() and source.is_file(), "External evidence must be an existing absolute file")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None:
        require(re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is not None and digest == expected_hash.lower(),
                "External evidence differs from its expected SHA-256: " + str(source))
    with destination.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    require(sha256(source) == digest and sha256(destination) == digest, "External evidence changed while being preserved")
    return {"source": str(source), "copy": str(destination), "sha256": digest}


def report_relative(path, output_directory):
    require(isinstance(path, str) and isinstance(output_directory, str), "Playwright attachment/outputDir must be paths")
    require(".." not in path.replace("\\", "/").split("/") and ".." not in output_directory.replace("\\", "/").split("/"),
            "Playwright paths must not contain traversal")
    windows = bool(re.match(r"^[A-Za-z]:[/\\]", output_directory) or output_directory.startswith("\\\\"))
    path_type = PureWindowsPath if windows else PurePosixPath
    attachment, directory = path_type(path), path_type(output_directory)
    require(attachment.is_absolute() and directory.is_absolute(), "Playwright paths must be absolute")
    relative = attachment.relative_to(directory)
    return normal_relative(PurePosixPath(*relative.parts).as_posix())


def attachment_bytes(attachment, output_directory, extracted):
    require(isinstance(attachment, dict) and attachment.get("contentType") == "application/json", "Expected a JSON attachment")
    require(("path" in attachment) != ("body" in attachment), "Attachment must have exactly one path or body")
    if "body" in attachment:
        require(isinstance(attachment["body"], str), "Attachment body must be base64 text")
        return base64.b64decode(attachment["body"], validate=True), None
    relative = report_relative(attachment["path"], output_directory)
    path = extracted.joinpath(*relative.split("/"))
    require(path.is_file(), "Playwright attachment is absent from the sealed results: " + relative)
    return path.read_bytes(), relative


def expected_playwright_ids(report, expected_cases):
    require(isinstance(report, dict) and report.get("errors") == [], "Expected Playwright list has errors")
    identities = {}
    def visit(suites):
        require(isinstance(suites, list), "Expected Playwright list suites are invalid")
        for suite in suites:
            require(isinstance(suite, dict) and isinstance(suite.get("specs", []), list), "Expected suite is invalid")
            for spec in suite.get("specs", []):
                require(isinstance(spec, dict) and isinstance(spec.get("id"), str) and spec["id"]
                        and isinstance(spec.get("tests"), list) and spec["tests"], "Expected spec has no original ID/tests")
                for test in spec["tests"]:
                    require(isinstance(test, dict) and isinstance(test.get("projectId"), str) and test.get("results") == [],
                            "Expected roster must be original --list output, not a fabricated execution")
                    identity = (spec["id"], test["projectId"])
                    require(identity not in identities, "Expected Playwright ID/project pair is duplicated")
                    identities[identity] = {key: spec.get(key) for key in ("file", "line", "column")}
            visit(suite.get("suites", []))
    visit(report.get("suites"))
    stats = report.get("stats")
    require(isinstance(stats, dict) and type(stats.get("skipped")) is int and stats["skipped"] == len(identities)
            and all(type(stats.get(key)) is int and stats[key] == 0 for key in ("expected", "unexpected", "flaky")),
            "Expected roster does not describe an unexecuted Playwright list")
    require(len(identities) == expected_cases, "Expected Playwright roster cardinality differs")
    return identities


def bind_playwright_roster(report, extracted, outcome_paths, expected_cases, expected_ids=None):
    """Exact original (spec.id, projectId) -> closed-store directory bijection.

    Paths and base64 bodies are the two observed JSON reporter attachment forms.
    No test title, slug, ordinal, inferred ID, or count alone establishes a join.
    """
    require(isinstance(report, dict) and report.get("errors") == [], "Original Playwright report has errors")
    projects = report.get("config", {}).get("projects")
    require(isinstance(projects, list) and projects, "Original Playwright projects are missing")
    by_project = {}
    for project in projects:
        require(isinstance(project, dict) and isinstance(project.get("id"), str) and project["id"] not in by_project,
                "Playwright project identities are missing or duplicated")
        by_project[project["id"]] = project
    candidates = {str(path.parent.relative_to(extracted)).replace("\\", "/"): path for path in outcome_paths}
    require(len(candidates) == len(outcome_paths) == expected_cases, "Closed-store directory roster is not unique or complete")
    joined, identities, directories = [], set(), set()

    def visit(suites):
        require(isinstance(suites, list), "Playwright suite list is invalid")
        for suite in suites:
            require(isinstance(suite, dict), "Playwright suite is invalid")
            specs = suite.get("specs", [])
            require(isinstance(specs, list), "Playwright specs are invalid")
            for spec in specs:
                require(isinstance(spec, dict) and spec.get("ok") is True and isinstance(spec.get("id"), str) and spec["id"],
                        "Playwright spec did not pass or has no original ID")
                tests = spec.get("tests")
                require(isinstance(tests, list) and tests, "Playwright execution records are missing")
                for test in tests:
                    require(isinstance(test, dict) and test.get("projectId") in by_project, "Unknown Playwright project")
                    identity = (spec["id"], test["projectId"])
                    require(identity not in identities, "Duplicate original Playwright test ID/project pair")
                    identities.add(identity)
                    results = test.get("results")
                    require(test.get("expectedStatus") == "passed" and test.get("status") == "expected"
                            and isinstance(results, list) and len(results) == 1, "Playwright case failed, skipped, or retried")
                    result = results[0]
                    require(isinstance(result, dict) and result.get("status") == "passed" and type(result.get("retry")) is int and result["retry"] == 0
                            and result.get("errors") == [] and result.get("error") is None, "Playwright result is not a clean first-attempt pass")
                    attachments = result.get("attachments")
                    require(isinstance(attachments, list) and all(isinstance(item, dict) for item in attachments), "Playwright attachments are missing")
                    copies = [item for item in attachments if item.get("name") == "closed-store-outcomes"]
                    require(len(copies) == 1, "Expected one original closed-store-outcomes attachment")
                    output_directory = by_project[test["projectId"]].get("outputDir")
                    raw, attachment_path = attachment_bytes(copies[0], output_directory, extracted)
                    matches = [case for case, path in candidates.items() if path.read_bytes() == raw]
                    require(len(matches) == 1, "Original outcomes attachment has no unique exact-byte case match")
                    case = matches[0]
                    require(case not in directories, "Multiple Playwright IDs map to one closed-store case")
                    directories.add(case)
                    if attachment_path is not None:
                        require(attachment_path.startswith(case + "/"), "Outcomes attachment path belongs to another case")
                    outcomes = parse_json(raw.decode("utf-8"))
                    snapshot = report_relative(outcomes.get("capture", {}).get("snapshotDirectory"), output_directory)
                    require(snapshot == case + "/closed-store/store", "Original captured snapshot directory belongs to another case")
                    evidence = [name for name in EVIDENCE_NAMES if (candidates[case].parent / name).is_file()]
                    require(len(evidence) == 1, "Expected one recognized scenario evidence file for roster binding")
                    evidence_copies = [item for item in attachments if item.get("name") == evidence[0][:-5]]
                    require(len(evidence_copies) == 1, "Original scenario evidence attachment is missing or repeated")
                    evidence_raw, evidence_path = attachment_bytes(evidence_copies[0], output_directory, extracted)
                    require(evidence_raw == (candidates[case].parent / evidence[0]).read_bytes(), "Scenario attachment bytes differ from its case")
                    require(evidence_path is None or evidence_path.startswith(case + "/"), "Scenario attachment path belongs to another case")
                    joined.append({"playwrightId": spec["id"], "projectId": test["projectId"], "case": case,
                                   "file": spec.get("file"), "line": spec.get("line"), "column": spec.get("column"),
                                   "outcomesSha256": hashlib.sha256(raw).hexdigest(), "evidenceFile": evidence[0],
                                   "evidenceSha256": hashlib.sha256(evidence_raw).hexdigest()})
            visit(suite.get("suites", []))

    visit(report.get("suites"))
    stats = report.get("stats")
    require(isinstance(stats, dict) and type(stats.get("expected")) is int and stats["expected"] == len(joined)
            and all(type(stats.get(key)) is int and stats[key] == 0 for key in ("unexpected", "skipped", "flaky")),
            "Playwright totals do not describe this exact passing roster")
    require(len(joined) == expected_cases and directories == set(candidates), "Original Playwright IDs and closed-store directories are not an exact bijection")
    if expected_ids is not None:
        require(identities == set(expected_ids), "Executed Playwright IDs differ from the independent expected roster")
        for row in joined:
            same_json(expected_ids[(row["playwrightId"], row["projectId"])], {key: row[key] for key in ("file", "line", "column")}, "$.expectedPlaywrightLocation")
    return joined


def same_json(expected, actual, at="$"):
    """JSON comparison with JS-number equality, preserving bool/number identity."""
    if type(expected) is bool or type(actual) is bool:
        require(type(expected) is type(actual) and expected == actual, at + ": boolean mismatch")
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        require(math.isfinite(expected) and math.isfinite(actual) and expected == actual, at + ": number mismatch")
    elif isinstance(expected, dict) and isinstance(actual, dict):
        require(expected.keys() == actual.keys(), at + ": object property mismatch")
        for key in expected:
            same_json(expected[key], actual[key], at + "." + key)
    elif isinstance(expected, list) and isinstance(actual, list):
        require(len(expected) == len(actual), at + ": array length mismatch")
        for index, (left, right) in enumerate(zip(expected, actual)):
            same_json(left, right, at + "[" + str(index) + "]")
    else:
        require(type(expected) is type(actual) and expected == actual, at + ": value/type mismatch")


def safe_integer(value):
    return type(value) is int and 0 <= value <= SAFE_INTEGER


def normal_relative(value, allow_root=False):
    require(isinstance(value, str), "A relative path must be text")
    if value == "" and allow_root:
        return value
    require(value and "\\" not in value and "\x00" not in value and ":" not in value,
            "Unsafe relative path: " + repr(value))
    parts = value.split("/")
    require(all(part not in ("", ".", "..") for part in parts), "Non-normal relative path: " + repr(value))
    require(not PurePosixPath(value).is_absolute(), "Absolute archive path")
    # These spellings alias names or streams on the target Windows filesystem.
    reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}
    require(all(not part.endswith((".", " ")) and part.split(".")[0].upper() not in reserved for part in parts),
            "Ambiguous Windows path: " + repr(value))
    return value


def regular_kind(path):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400),
            "Symlink/junction/reparse entry refused: " + str(path))
    if stat.S_ISDIR(info.st_mode):
        return "directory", info
    require(stat.S_ISREG(info.st_mode), "Non-regular entry refused: " + str(path))
    return "file", info


def tree_entries(directory):
    entries, aliases = [], set()

    def visit(path, name):
        normal_relative(name, allow_root=True)
        alias = name.casefold()
        require(alias not in aliases, "Case-aliased path in store: " + name)
        aliases.add(alias)
        kind, info = regular_kind(path)
        if kind == "directory":
            entries.append({"path": name, "kind": "directory"})
            # JavaScript Array.sort compares UTF-16 code units.
            for child in sorted(path.iterdir(), key=lambda item: item.name.encode("utf-16-be")):
                visit(child, name + "/" + child.name if name else child.name)
        else:
            require(safe_integer(info.st_size), "File size exceeds safe capture range")
            digest = sha256(path)
            require(path.stat().st_size == info.st_size, "File changed size while hashing: " + name)
            entries.append({"path": name, "kind": "file", "sizeBytes": info.st_size, "sha256": digest})

    visit(Path(directory), "")
    return entries


def clean_exits(exits, taints, at):
    require(isinstance(taints, list) and not taints, at + ": lifecycle taints are present")
    require(isinstance(exits, list) and exits, at + ": no process exit observed")
    for item in exits:
        require(isinstance(item, dict) and type(item.get("code")) is int and item["code"] == 0
                and item.get("signal") is None, at + ": unclean process exit")


def validate_oracle(oracle):
    require(isinstance(oracle, dict) and type(oracle.get("schemaVersion")) is int and oracle["schemaVersion"] == 1, "Unsupported oracle schema")
    require(type(oracle.get("journalAfterRevisionExclusive")) is int and oracle["journalAfterRevisionExclusive"] == 0 and oracle.get("journalComplete") is True,
            "Oracle does not claim a complete journal from revision zero")
    project, journal = oracle.get("project"), oracle.get("journal")
    require(isinstance(project, dict) and isinstance(project.get("id"), str) and project["id"], "Missing Project identity")
    require(safe_integer(project.get("revision")), "Project revision is not a safe integer")
    require(isinstance(journal, list), "Oracle journal is not an array")
    for index, event in enumerate(journal, 1):
        require(isinstance(event, dict) and safe_integer(event.get("revision")) and event["revision"] == index,
                "Oracle journal is gapped, repeated, or unordered at revision " + str(index))
    require(len(journal) == project["revision"], "Oracle journal is truncated or ahead of Project")
    return project, journal


def verify_case(case_root):
    capture_root = case_root / "closed-store"
    outcomes = read_json(case_root / "closed-store-outcomes.json")
    require(type(outcomes.get("schemaVersion")) is int and outcomes["schemaVersion"] == 1 and outcomes.get("errors") == [], "Store finalization has errors")
    preparations = outcomes.get("preparation")
    require(isinstance(preparations, list) and all(item.get("status") == "fulfilled" and "error" not in item for item in preparations),
            "Store preparation did not complete")
    closures = outcomes.get("closures")
    require(isinstance(closures, list) and closures, "No store process closures recorded")
    for index, item in enumerate(closures):
        require(item.get("status") == "fulfilled" and "error" not in item, "A store process close was rejected")
        clean_exits(item.get("exits"), item.get("taints"), "closure " + str(index))
    manifest, oracle = read_json(capture_root / "manifest.json"), read_json(capture_root / "oracle.json")
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == 1, "Unsupported capture schema")
    same_json(manifest, outcomes.get("capture"), "$.outcomes.capture")
    same_json(oracle, outcomes.get("oracle"), "$.outcomes.oracle")
    validate_oracle(oracle)
    entries = tree_entries(capture_root / "store")
    same_json(entries, manifest.get("entries"), "$.capture.entries")
    digest = digest_json(entries)
    for name in ("sourceBeforeSha256", "sourceAfterSha256", "snapshotSha256"):
        require(manifest.get(name) == digest, "Capture inventory digest differs: " + name)
    lifecycle = manifest.get("lifecycle")
    require(isinstance(lifecycle, list) and len(lifecycle) == len(closures), "Capture participant count differs from closure records")
    for index, (life, closure) in enumerate(zip(lifecycle, closures)):
        same_json(life, {key: closure[key] for key in ("databasePath", "exits", "taints")}, "$.capture.lifecycle[" + str(index) + "]")
    database = normal_relative(manifest.get("databaseRelativePath"))
    require(any(item["path"] == database and item["kind"] == "file" for item in entries), "Database is absent from capture inventory")
    evidence_paths = [case_root / name for name in EVIDENCE_NAMES if (case_root / name).is_file()]
    require(len(evidence_paths) == 1, "Expected one recognized scenario evidence file in " + str(case_root))
    evidence = read_json(evidence_paths[0])
    require(evidence.get("primaryFailure") is None and evidence.get("failures") == [], "Scenario reports a failure")
    same_json(evidence.get("final"), oracle["project"], "$.scenario.final")
    oracle_key = "closedStoreOracle" if evidence_paths[0].name == "brand-authoring-service-evidence.json" else "canonicalOracle"
    same_json(evidence.get(oracle_key), oracle, "$.scenario." + oracle_key)
    services = evidence.get("services") if evidence_paths[0].name in ("component-distillation-service-evidence.json", "production-design-service-evidence.json") else [evidence.get("service")]
    require(isinstance(services, list) and services and all(isinstance(value, dict) for value in services), "Scenario service records are missing")
    for index, service in enumerate(services):
        clean_exits(service.get("processExits"), service.get("lifecycleTaints"), "scenario service " + str(index))
    return {"oracle": oracle, "manifest": manifest, "treeSha256": digest,
            "evidenceFile": evidence_paths[0].name, "originalBinaryPaths": [service.get("binaryPath") for service in services]}


def seal_and_extract(source, output):
    before = tree_entries(source)
    archive = output / "sealed-playwright-results.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as target:
        for item in before:
            if item["path"]:
                target.write(source / item["path"], item["path"] + ("/" if item["kind"] == "directory" else ""))
    same_json(before, tree_entries(source), "$.sourceAfterSeal")
    archive_hash = sha256(archive)
    extracted = output / "extracted"
    extracted.mkdir()
    seen = set()
    with zipfile.ZipFile(archive) as sealed:
        for member in sealed.infolist():
            name = normal_relative(member.filename[:-1] if member.is_dir() else member.filename)
            require(name.casefold() not in seen, "Duplicate/aliased archive member: " + name)
            seen.add(name.casefold())
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode), "Archive contains a symlink")
            destination = extracted / name
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with sealed.open(member) as source_bytes, destination.open("xb") as target_bytes:
                    shutil.copyfileobj(source_bytes, target_bytes, 1024 * 1024)
    same_json(before, tree_entries(extracted), "$.extractedResults")
    require(sha256(archive) == archive_hash, "Sealed archive changed during extraction")
    seal = {"schema": "motionwright.playwright-results-seal/1", "createdAt": now(), "sourceDirectory": str(source),
            "archive": str(archive), "archiveSha256": archive_hash, "treeSha256": digest_json(before), "entries": before}
    write_json(output / "seal-manifest.json", seal)
    return extracted, before, seal


class JsonlReader:
    """Sequential, correlated RPC; stdin EOF closes the service, as in the helper."""
    def __init__(self, binary, database, output, timeout):
        self.timeout, self.incoming, self.sequence = timeout, queue.Queue(), 0
        self.token, self.pending, self.taints = str(uuid.uuid4()), None, []
        self.output = output
        self.process = self.transcript = self.stderr = None
        self.stdout_thread = self.stderr_thread = None
        self.command = [str(binary), "--database", str(database), "--desktop-invoke-fixture"]

    def start(self):
        # The caller owns this object before Popen. Even a partial thread/file
        # startup therefore reaches close() and records whether a process ran.
        try:
            self.transcript = (self.output / "transport.jsonl").open("x", encoding="utf-8", newline="\n")
            self.stderr = (self.output / "stderr.log").open("xb")
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.stdout_thread = threading.Thread(target=self._stdout, daemon=True)
            self.stderr_thread = threading.Thread(target=self._stderr, daemon=True)
            self.stdout_thread.start(); self.stderr_thread.start()
        except BaseException:
            self.taints.append("startup_failure")
            raise

    def record(self, direction, payload):
        self.transcript.write(compact({"at": now(), "processId": self.process.pid, "direction": direction, "payload": payload}) + "\n")
        self.transcript.flush()

    def _stdout(self):
        try:
            while True:
                line = self.process.stdout.readline(64 * 1024 * 1024 + 1)
                if not line:
                    self.incoming.put(("eof", None)); return
                if len(line) > 64 * 1024 * 1024:
                    self.incoming.put(("error", "JSONL response exceeds 64 MiB")); return
                self.incoming.put(("line", line.decode("utf-8")))
        except BaseException as error:
            self.incoming.put(("error", str(error)))

    def _stderr(self):
        try:
            for chunk in iter(lambda: self.process.stderr.read(65536), b""):
                self.stderr.write(chunk)
            self.stderr.flush()
        except BaseException as error:
            self.incoming.put(("error", "stderr capture failed: " + str(error)))

    def rpc(self, command, request):
        require(command in READ_COMMANDS, "Only the four observed readback commands are admitted")
        require(self.process is not None and self.transcript is not None, "Service transport has not started")
        require(self.pending is None, "Another RPC is still pending")
        self.sequence += 1
        identity = "archive-readback:" + self.token + ":" + str(self.sequence)
        payload = {"id": identity, "command": command, "args": {"request": request}}
        self.pending = identity
        self.record("request", payload)
        try:
            self.process.stdin.write((compact(payload) + "\n").encode("utf-8")); self.process.stdin.flush()
            kind, value = self.incoming.get(timeout=self.timeout)
            require(kind == "line", "Service ended or reader failed before response: " + str(value))
            try:
                response = parse_json(value)
            except BaseException:
                self.record("invalid_response", {"raw": value})
                raise
            self.record("response", response)
            require(isinstance(response, dict) and response.get("id") == identity and type(response.get("ok")) is bool,
                    "Uncorrelated or invalid JSONL response")
            self.pending = None
            if response["ok"]:
                require("result" in response, "Successful response has no result")
                return response["result"]
            require(isinstance(response.get("error"), str), "Unexpected error payload for a readback command")
            raise ValueError(command + ": " + response["error"])
        except queue.Empty as error:
            self.taints.append("request_timeout")
            raise TimeoutError(command + " timed out; no request was retried") from error
        except BaseException:
            self.taints.append("request_or_protocol_failure")
            raise

    def close(self):
        errors = []
        if self.pending is not None:
            self.taints.append("pending_at_close")
        try:
            if self.process is None:
                errors.append("No service process was created")
            else:
                try:
                    self.process.stdin.close()
                except BaseException as error:
                    errors.append("stdin close: " + str(error))
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.taints.append("close_deadline")
                    self.process.kill()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.taints.append("unobserved_exit")
                for thread in (self.stdout_thread, self.stderr_thread):
                    if thread is not None and thread.ident is not None:
                        thread.join(timeout=2)
                    if thread is not None and thread.is_alive():
                        errors.append("A transport capture thread did not finish")
                while not self.incoming.empty():
                    kind, value = self.incoming.get_nowait()
                    if kind != "eof":
                        self.record("unexpected_after_close", {"kind": kind, "value": value})
                        errors.append("Unexpected or unconsumed transport output")
                if self.process.returncode != 0:
                    errors.append("Service exit code was " + str(self.process.returncode))
        except BaseException as error:
            errors.append("close failure: " + str(error))
        finally:
            for label, stream, thread in (("transcript", self.transcript, None), ("stderr destination", self.stderr, self.stderr_thread)):
                if stream is not None:
                    if thread is not None and thread.is_alive():
                        errors.append(label + " is still owned by a running capture thread")
                        continue
                    try:
                        stream.close()
                    except BaseException as error:
                        errors.append(label + " close: " + str(error))
            if self.process is not None:
                for label, stream, thread in (("stdout pipe", self.process.stdout, self.stdout_thread), ("stderr pipe", self.process.stderr, self.stderr_thread)):
                    if stream is not None and (thread is None or not thread.is_alive()):
                        try:
                            stream.close()
                        except BaseException as error:
                            errors.append(label + " close: " + str(error))
        code = self.process.returncode if self.process is not None else None
        return {"observedAt": now(), "processId": self.process.pid if self.process is not None else None, "returnCode": code,
                "taints": self.taints, "errors": errors, "command": self.command,
                "rpcCount": self.sequence, "clean": not errors and not self.taints and code == 0}


def new_readback_observation(destination):
    return {"status": "FAILED", "startedAt": now(), "transportAttempted": False, "transportExecuted": False,
            "processId": None, "rpcCount": 0, "primaryFailure": None, "errors": [], "close": None,
            "outputDirectory": str(destination), "receipt": {"path": str(destination / "readback.json"),
            "status": "PENDING", "requiresCallerReport": True}}


def read_fresh_copy(binary, expected_hash, case_root, capture, destination, timeout, observation=None, checkpoint=None):
    # main installs this exact object in its case roster before entering here.
    # Neither a return value nor an optional JSON write owns execution facts.
    result = observation if observation is not None else new_readback_observation(destination)
    store, oracle, reader = destination / "store", capture["oracle"], None
    try:
        destination.mkdir(parents=True)
        shutil.copytree(case_root / "closed-store" / "store", store, copy_function=shutil.copy2)
        same_json(capture["manifest"]["entries"], tree_entries(store), "$.freshCopy")
        require(sha256(binary) == expected_hash, "Service binary changed before invocation")
        reader = JsonlReader(binary, store / capture["manifest"]["databaseRelativePath"], destination, timeout)
        if checkpoint:
            checkpoint("launch_intent", result)
        # This records calling start(), separately from observing an actual PID.
        result["transportAttempted"] = True
        reader.start()
        result["transportExecuted"] = reader.process is not None
        result["processId"] = reader.process.pid if reader.process is not None else None
        if checkpoint:
            checkpoint("transport_observed", result)
        bootstrap = reader.rpc("bootstrap", {"project_id": None})
        require(bootstrap.get("test_transport") == "service-test-jsonl" and bootstrap.get("fixture", {}).get("label") == "synthetic_test_fixture",
                "Binary did not identify the observed real-service test transport")
        require(bootstrap.get("desktop_invoke_fixture") is True and bootstrap.get("native_sdk", {}).get("mode") == "tauri",
                "Binary did not identify the requested desktop invoke fixture")
        identity = oracle["project"]["id"]
        reopened = reader.rpc("reopen", {"project_id": identity})
        same_json(oracle["project"], reopened, "$.reopenedProject")
        project = reader.rpc("get_project", {"project_id": identity})
        same_json(oracle["project"], project, "$.project")
        journal, cursor = [], 0
        while True:
            page = reader.rpc("project_history", {"project_id": identity, "after_revision": cursor, "limit": 100})
            require(isinstance(page, list) and len(page) <= 100, "History response is not a bounded page")
            if not page:
                break
            for event in page:
                require(isinstance(event, dict) and safe_integer(event.get("revision")) and event["revision"] == cursor + 1
                        and event["revision"] <= project["revision"], "Readback journal is gapped, repeated, unordered, or ahead")
                cursor = event["revision"]; journal.append(event)
        require(cursor == project["revision"], "Readback journal is truncated before Project revision")
        same_json(oracle["journal"], journal, "$.journal")
        same_json(project, reader.rpc("get_project", {"project_id": identity}), "$.projectAfterHistory")
        observed = {"schemaVersion": 1, "observedAt": now(), "project": project, "journal": journal,
                    "journalAfterRevisionExclusive": 0, "journalComplete": True}
        result.update(projectId=identity, generation=project.get("generation"), revision=project["revision"], journalEntries=len(journal),
                      projectEqual=True, journalEqual=True, comparison="Complete Project and journal; observation timestamp is not compared")
        write_json(destination / "observed-oracle.json", observed)
    except BaseException as error:
        result["primaryFailure"] = type(error).__name__ + ": " + str(error)
        result["errors"].append(result["primaryFailure"])
    finally:
        if reader is not None:
            result["transportExecuted"] = result["transportExecuted"] or reader.process is not None
            result["processId"] = reader.process.pid if reader.process is not None else result["processId"]
            result["rpcCount"] = reader.sequence
            try:
                result["close"] = reader.close()
                if not result["close"]["clean"]:
                    result["errors"].append("Readback process did not close cleanly")
            except BaseException as error:
                result["close"] = {"observedAt": now(), "processId": reader.process.pid if reader.process is not None else None,
                                   "returnCode": reader.process.returncode if reader.process is not None else None,
                                   "taints": reader.taints, "errors": ["close raised: " + str(error)],
                                   "command": reader.command, "rpcCount": reader.sequence, "clean": False}
                result["errors"].append("Readback close raised: " + str(error))
        try:
            require(sha256(binary) == expected_hash, "Service binary changed during readback")
            same_json(capture["manifest"]["entries"], tree_entries(case_root / "closed-store" / "store"), "$.unopenedSnapshotAfterReadback")
        except BaseException as error:
            result["errors"].append(type(error).__name__ + ": " + str(error))
        result["finishedAt"] = now()
        result["status"] = "FAILED" if result["errors"] else "VERIFIED"
        if checkpoint:
            try:
                checkpoint("case_closed", result)
            except BaseException as error:
                result["status"] = "FAILED"
                result["errors"].append("Observation checkpoint: " + type(error).__name__ + ": " + str(error))
        try:
            result["receipt"]["status"] = "WRITTEN"
            write_json(destination / "readback.json", result)
        except BaseException as error:
            result["receipt"]["status"] = "FAILED"
            result["status"] = "FAILED"
            result["errors"].append("Per-copy receipt: " + type(error).__name__ + ": " + str(error))
            if checkpoint:
                try:
                    checkpoint("case_receipt_failed", result)
                except BaseException as observation_error:
                    result["errors"].append("Receipt failure checkpoint: " + type(observation_error).__name__ + ": " + str(observation_error))
    return result


def refresh_transport_facts(report):
    observations = [case.get("readback") for case in report.get("cases", []) if isinstance(case.get("readback"), dict)]
    report["transportAttempted"] = report.get("transportAttempted", False) or any(value.get("transportAttempted") for value in observations)
    report["transportExecuted"] = report.get("transportExecuted", False) or any(value.get("transportExecuted") for value in observations)


def emit_report(report, path):
    # Terminal fallback retains the same facts when every optional JSON receipt
    # fails. An unavailable stdout is tried once on stderr; transport is never retried.
    payload = {"status": report["status"], "report": str(path), "transportAttempted": report["transportAttempted"],
               "transportExecuted": report["transportExecuted"]}
    if report["status"] == "FAILED":
        payload["observation"] = report
    try:
        print(compact(payload), flush=True)
    except BaseException as error:
        report["status"] = "FAILED"
        report["errors"].append("Terminal stdout: " + type(error).__name__ + ": " + str(error))
        try:
            print(compact({"status": "FAILED", "observation": report}), file=sys.stderr, flush=True)
        except BaseException:
            # All delivery channels can fail; this must still return failure,
            # not invent a successful receipt or a claim that nothing executed.
            pass


def main(observation=None):
    report = observation if observation is not None else {}
    report.update(schema=SCHEMA, status="FAILED", transportAttempted=False, transportExecuted=False, cases=[], errors=[])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory outside results-root; never reused")
    parser.add_argument("--expected-cases", required=True, type=int)
    parser.add_argument("--playwright-exit-code", type=int)
    parser.add_argument("--playwright-json", type=Path, help="Original JSON reporter output, mandatory for execution")
    parser.add_argument("--expected-playwright-sha256", help="External original report SHA-256, mandatory for execution")
    parser.add_argument("--expected-roster-json", type=Path, help="Original Playwright --list JSON of the expected source selection")
    parser.add_argument("--expected-roster-sha256", help="External expected --list JSON SHA-256, mandatory for execution")
    parser.add_argument("--provenance-receipt", type=Path, help="External A6/source/binary receipt, mandatory for execution")
    parser.add_argument("--expected-provenance-sha256", help="External receipt SHA-256, mandatory for execution")
    parser.add_argument("--a6-receipt-reviewed", action="store_true", help="Caller explicitly confirms external A6 binding review; not authentication by this tool")
    parser.add_argument("--execute-readback", action="store_true", help="Opt in only after A6 supplies the exact executable and receipt")
    parser.add_argument("--expected-binary-sha256")
    parser.add_argument("--backend-source-sha")
    parser.add_argument("--rpc-timeout-seconds", type=float, default=10)
    args = parser.parse_args()
    require(args.results_root.is_absolute() and args.output.is_absolute(), "Input/output paths must be absolute")
    source, output = args.results_root.resolve(), args.output.resolve()
    require(source.is_dir() and output != source and source not in output.parents and output not in source.parents,
            "Output must be a separate fresh directory outside the results tree")
    require(args.expected_cases > 0 and 0 < args.rpc_timeout_seconds <= 60, "Invalid expected count or RPC timeout")
    output.mkdir(parents=True, exist_ok=False)
    report.update(startedAt=now(), sourceResults=str(source), outputDirectory=str(output),
                  scope="independent closed-store archive and Project/journal readback; not native rendering or human acceptance",
                  helperReferenceSha256=HELPER_SHA256, expectedCases=args.expected_cases,
                  playwrightExitCodeReportedByCaller=args.playwright_exit_code, backendSourceShaAssertedByCaller=args.backend_source_sha,
                  sourceAuthentication="NOT_ESTABLISHED_BY_THIS_SCRIPT", archive=None, binary=None, playwrightRoster=None,
                  observationJournal={"path": str(output / "execution-observations.jsonl"), "status": "NOT_OPENED"},
                  receipt={"path": str(output / "readback-report.json"), "status": "PENDING", "fallbackPath": None})
    durable, external = None, []
    try:
        if args.execute_readback:
            require(args.playwright_json is not None and args.expected_playwright_sha256,
                    "Execution requires the original Playwright JSON and its external SHA-256; a count is insufficient")
            require(args.expected_roster_json is not None and args.expected_roster_sha256,
                    "Execution requires an independent original Playwright --list roster and its external SHA-256")
            require(args.provenance_receipt is not None and args.expected_provenance_sha256 and args.a6_receipt_reviewed,
                    "Execution requires the external A6 receipt, its SHA-256, and explicit caller review of the binding")
        if args.provenance_receipt:
            receipt = copy_external_file(args.provenance_receipt, output / "provenance-receipt.original", args.expected_provenance_sha256)
            external.append(receipt)
            report["provenanceReceipt"] = {**receipt, "externalReviewAssertedByCaller": args.a6_receipt_reviewed,
                                           "authentication": "NOT_ESTABLISHED_BY_THIS_SCRIPT"}
        original_playwright = None
        expected_ids = None
        if args.expected_roster_json:
            expected = copy_external_file(args.expected_roster_json, output / "playwright-roster.original.json", args.expected_roster_sha256)
            external.append(expected)
            report["expectedPlaywrightRoster"] = expected
            expected_ids = expected_playwright_ids(read_json(Path(expected["copy"])), args.expected_cases)
        if args.playwright_json:
            preserved = copy_external_file(args.playwright_json, output / "playwright-report.original.json", args.expected_playwright_sha256)
            external.append(preserved)
            report["playwrightReport"] = preserved
            original_playwright = read_json(Path(preserved["copy"]))
        extracted, source_entries, seal = seal_and_extract(source, output)
        report["archive"] = {key: seal[key] for key in ("archive", "archiveSha256", "treeSha256")}
        outcome_paths = sorted(extracted.rglob("closed-store-outcomes.json"), key=lambda path: path.as_posix())
        require(len(outcome_paths) == args.expected_cases, "Found " + str(len(outcome_paths)) + " closed-store outcomes; expected " + str(args.expected_cases))
        if original_playwright is not None:
            report["playwrightRoster"] = bind_playwright_roster(original_playwright, extracted, outcome_paths, args.expected_cases, expected_ids)
        roster_by_case = {entry["case"]: entry for entry in report["playwrightRoster"] or []}
        prepared = []
        for index, outcomes in enumerate(outcome_paths, 1):
            case_root, case_id = outcomes.parent, str(outcomes.parent.relative_to(extracted)).replace("\\", "/")
            item = {"case": case_id, "captureStatus": "FAILED", "readback": None, "errors": []}
            if case_id in roster_by_case:
                item["playwrightIdentity"] = {key: roster_by_case[case_id][key] for key in ("playwrightId", "projectId")}
            report["cases"].append(item)
            try:
                capture = verify_case(case_root)
                item.update(captureStatus="VERIFIED", treeSha256=capture["treeSha256"], evidenceFile=capture["evidenceFile"],
                            originalBinaryPaths=capture["originalBinaryPaths"], projectId=capture["oracle"]["project"]["id"],
                            revision=capture["oracle"]["project"]["revision"])
                destination = output / "readback-copies" / (str(index).zfill(3) + "-" + hashlib.sha256(case_id.encode()).hexdigest()[:12])
                prepared.append((item, case_root, capture, destination))
            except BaseException as error:
                item["errors"].append(type(error).__name__ + ": " + str(error))
        require(all(item["captureStatus"] == "VERIFIED" for item in report["cases"]), "One or more captured scenarios failed validation; no service was invoked")
        if args.execute_readback:
            require(args.playwright_exit_code == 0, "Readback requires the caller's successful Playwright exit receipt")
            require(report["playwrightRoster"] is not None, "The original Playwright ID roster is required")
            require(re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_binary_sha256 or "") is not None, "An exact A6 binary SHA-256 is required")
            require(re.fullmatch(r"[0-9a-fA-F]{40}", args.backend_source_sha or "") is not None, "The backend source SHA must be supplied from the externally reviewed A6 receipt")
            configured = os.environ.get("MOTIONWRIGHT_SERVICE_TEST_BIN", "")
            binary = Path(configured)
            require(configured and binary.is_absolute() and binary.is_file(), "MOTIONWRIGHT_SERVICE_TEST_BIN must be an existing absolute executable path")
            expected_hash = args.expected_binary_sha256.lower()
            require(sha256(binary) == expected_hash, "Executable differs from the supplied A6 binary SHA-256")
            report["binary"] = {"path": str(binary), "sha256": expected_hash}
            durable = DurableObservations(output / "execution-observations.jsonl")
            report["observationJournal"]["status"] = "OPEN"
            durable.record("run_before_transport", None, report)
            for item, case_root, capture, destination in prepared:
                result = new_readback_observation(destination)
                item["readback"] = result  # Caller owns facts before any possible launch.
                try:
                    read_fresh_copy(binary, expected_hash, case_root, capture, destination, args.rpc_timeout_seconds, result,
                                    lambda kind, facts: durable.record(kind, item["case"], facts))
                except BaseException as error:
                    result["status"] = "FAILED"
                    result["errors"].append("Caller boundary: " + type(error).__name__ + ": " + str(error))
                    result.setdefault("finishedAt", now())
                finally:
                    refresh_transport_facts(report)
                # No later service is launched after a custody/transport failure.
                require(result["status"] == "VERIFIED", "Independent readback failed; no operation was retried")
            report["status"] = "READBACK_VERIFIED"
        else:
            report["status"] = "ARCHIVE_VERIFIED_READBACK_NOT_RUN"
        same_json(source_entries, tree_entries(source), "$.originalResultsAfterRun")
        same_json(source_entries, tree_entries(extracted), "$.extractedResultsAfterRun")
        require(sha256(Path(seal["archive"])) == seal["archiveSha256"], "Sealed archive changed after verification")
        for preserved in external:
            require(sha256(Path(preserved["source"])) == preserved["sha256"] and sha256(Path(preserved["copy"])) == preserved["sha256"],
                    "External original evidence changed during readback")
    except BaseException as error:
        report["status"] = "FAILED"
        report["errors"].append(type(error).__name__ + ": " + str(error))
    finally:
        refresh_transport_facts(report)
        report["finishedAt"] = now()
        if durable is not None:
            try:
                durable.record("run_before_receipt", None, report)
            except BaseException as error:
                report["status"] = "FAILED"
                report["errors"].append("Final observation: " + type(error).__name__ + ": " + str(error))
            errors = durable.close()
            report["observationJournal"]["status"] = "FAILED" if errors else "CLOSED"
            report["observationJournal"]["recordsAttempted"] = durable.sequence
            report["observationJournal"]["recordsConfirmed"] = durable.confirmed
            if errors:
                report["status"] = "FAILED"; report["errors"].extend(errors)
        path = output / "readback-report.json"
        try:
            report["receipt"]["status"] = "WRITTEN"
            write_json(path, report)
        except BaseException as error:
            report["status"] = "FAILED"
            report["receipt"]["status"] = "FAILED"
            report["errors"].append("Final receipt: " + type(error).__name__ + ": " + str(error))
            fallback = output / "readback-report.failure.json"
            report["receipt"]["fallbackPath"] = str(fallback)
            try:
                write_json(fallback, report)
            except BaseException as fallback_error:
                report["errors"].append("Fallback receipt: " + type(fallback_error).__name__ + ": " + str(fallback_error))
        emit_report(report, path)
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    caller_observation = {}
    try:
        sys.exit(main(caller_observation))
    except Exception as error:
        caller_observation.setdefault("errors", []).append(type(error).__name__ + ": " + str(error))
        caller_observation["status"] = "FAILED"
        refresh_transport_facts(caller_observation)
        emit_report(caller_observation, caller_observation.get("receipt", {}).get("path", "not_written"))
        sys.exit(1)
