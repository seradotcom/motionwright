#!/usr/bin/env python3
"""Read-only, source-bound release scope planning; never a release verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import sys

CATALOG_PATH = "tooling/release_scope_catalog.json"
SCRIPT_PATH = "tooling/release_scope_plan.py"
CATALOG_SCHEMA = "motionwright.release-scope-catalog/1"
OBSERVATION_SCHEMA = "motionwright.release-scope-observation/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
MAX_JSON = 32 * 1024 * 1024


class ScopeError(ValueError):
    """A stable refusal, never permission to dispatch or retry."""


def require(condition, code):
    if not condition:
        raise ScopeError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def decode(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_JSON, "json_size")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=lambda unused: (_ for _ in ()).throw(ScopeError("json_nonfinite")))
    except (UnicodeError, ValueError) as error:
        if isinstance(error, ScopeError):
            raise
        raise ScopeError("json_invalid") from error


def read_json_file(path):
    """Read a bounded regular original; refuse replacement or size changes."""
    path = Path(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_JSON,
            "observation_file_size_or_type")
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require(identity(opened) == identity(before), "observation_file_changed")
        raw = stream.read(MAX_JSON + 1)
        after = os.fstat(stream.fileno())
    require(identity(after) == identity(opened) and len(raw) == opened.st_size,
            "observation_file_changed")
    return decode(raw)


def fields(value, expected, code):
    require(isinstance(value, dict) and set(value) == set(expected), code)


def strings(value, code, *, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(item, str) and item for item in value)
            and len(set(value)) == len(value), code)


def _sha(value, pattern, code):
    require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)


def _number(value, code):
    require(type(value) is int and value > 0, code)
    return value


def _job_blocks(text):
    # This is a reader for reviewed, hash-bound definitions, not a YAML parser.
    # Unsupported formatting refuses review rather than inventing semantics.
    require(text.count("\njobs:\n") == 1, "unsupported_jobs_layout")
    jobs = text.split("\njobs:\n", 1)[1]
    starts = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):[ \t]*$", jobs))
    require(bool(starts), "missing_jobs")
    result = {}
    for index, match in enumerate(starts):
        name = match.group(1)
        require(name not in result, "duplicate_workflow_job")
        result[name] = jobs[match.end():starts[index + 1].start() if index + 1 < len(starts) else len(jobs)]
    return result


def _guard(block, indent):
    found = re.findall(r"(?m)^" + " " * indent + r"if: (.+)$", block)
    require(len(found) <= 1, "ambiguous_guard")
    if not found:
        return None
    require(found[0] not in ("|", ">", "|-", ">-"), "unsupported_guard_layout")
    return found[0]


def _step_block(block, name):
    starts = list(re.finditer(r"(?m)^      - ", block))
    matched = []
    for index, start in enumerate(starts):
        part = block[start.start():starts[index + 1].start() if index + 1 < len(starts) else len(block)]
        if part.split("\n", 1)[0] == "      - name: " + name:
            matched.append(part)
    require(len(matched) == 1, "required_step_missing_or_ambiguous")
    return matched[0]


def _validate_observation_catalog(catalog):
    fields(catalog, ("schema", "workflows"), "catalog_fields")
    require(catalog["schema"] == CATALOG_SCHEMA and isinstance(catalog["workflows"], list),
            "catalog_schema")
    seen = set()
    for entry in catalog["workflows"]:
        fields(entry, ("path", "sha256", "input_options", "input_dispositions",
                       "jobs", "cases", "limitations"), "catalog_workflow_fields")
        path = entry["path"]
        require(isinstance(path, str) and WORKFLOW.fullmatch(path) and path not in seen,
                "catalog_workflow_path")
        seen.add(path)
        _sha(entry["sha256"], SHA256, "catalog_workflow_hash")
        strings(entry["limitations"], "catalog_limitations")
        options = entry["input_options"]
        require(isinstance(options, dict) and bool(options), "catalog_input_options")
        for key, values in options.items():
            require(KEY.fullmatch(key) is not None, "catalog_input_key")
            strings(values, "catalog_input_values")
        jobs = entry["jobs"]
        require(isinstance(jobs, dict) and bool(jobs), "catalog_jobs")
        api_names = set()
        for job_id, job in jobs.items():
            require(KEY.fullmatch(job_id) is not None, "catalog_job_id")
            fields(job, ("api_name", "guard", "required_steps"), "catalog_job_fields")
            require(isinstance(job["api_name"], str) and job["api_name"]
                    and job["api_name"] not in api_names, "catalog_api_job_name")
            api_names.add(job["api_name"])
            require(job["guard"] is None or isinstance(job["guard"], str) and job["guard"],
                    "catalog_job_guard")
            require(isinstance(job["required_steps"], list) and job["required_steps"], "catalog_steps")
            step_names = set()
            for step in job["required_steps"]:
                fields(step, ("name", "guard"), "catalog_step_fields")
                require(isinstance(step["name"], str) and step["name"]
                        and step["name"] not in step_names, "catalog_step_name")
                step_names.add(step["name"])
                require(step["guard"] is None or isinstance(step["guard"], str) and step["guard"],
                        "catalog_step_guard")
        require(isinstance(entry["cases"], list) and entry["cases"], "catalog_cases")
        cases, covered_jobs = {}, set()
        for case in entry["cases"]:
            fields(case, ("id", "inputs", "required_jobs", "events", "push_branch",
                          "run_title", "purpose"), "catalog_case_fields")
            require(isinstance(case["id"], str) and KEY.fullmatch(case["id"])
                    and case["id"] not in cases, "catalog_case_id")
            cases[case["id"]] = case
            fields(case["inputs"], options, "catalog_case_inputs")
            require(all(value in options[key] for key, value in case["inputs"].items()),
                    "catalog_case_input_value")
            strings(case["required_jobs"], "catalog_required_jobs")
            require(set(case["required_jobs"]) <= set(jobs), "catalog_unknown_job")
            covered_jobs.update(case["required_jobs"])
            strings(case["events"], "catalog_events")
            require(set(case["events"]) <= {"workflow_dispatch", "push"}, "catalog_event_unsupported")
            require(case["push_branch"] == "main" if "push" in case["events"]
                    else case["push_branch"] is None, "catalog_push_branch")
            require(isinstance(case["run_title"], str)
                    and case["run_title"].count("{source_sha}") == 1
                    and "{" not in case["run_title"].replace("{source_sha}", "")
                    and "}" not in case["run_title"].replace("{source_sha}", ""), "catalog_run_title")
            require(isinstance(case["purpose"], str) and case["purpose"], "catalog_purpose")
        require(covered_jobs == set(jobs), "catalog_uncovered_job")
        dispositions = entry["input_dispositions"]
        fields(dispositions, options, "catalog_disposition_inputs")
        for key, values in options.items():
            fields(dispositions[key], values, "catalog_missing_input_disposition")
            for value in values:
                row = dispositions[key][value]
                fields(row, ("covered_by", "basis"), "catalog_disposition_fields")
                require(isinstance(row["covered_by"], str) and row["covered_by"] in cases
                        and isinstance(row["basis"], str) and row["basis"],
                        "catalog_disposition_case")
    return catalog



REVIEWED_CATALOG_SCHEMA = "motionwright.release-scope-catalog/2"
PLAN_KINDS = {"TECHNICAL_SCOPE", "COMPONENT_PREREQUISITE",
              "CONDITIONAL_ALTERNATIVE", "NEEDS_DISPOSITION", "PINNED_PRODUCT_DIAGNOSTIC"}
INPUT_BINDINGS = {"PUBLIC_RSA3072_SPKI_DER_BASE64", "PUBLIC_RECIPIENT_SHA256",
                  "REPAIRED_G2_ORIGINAL_SOURCE_ADMISSION",
                  "FILM180_G2_ORIGINAL_SOURCE_ADMISSION", "CANDIDATE_SOURCE_SHA",
                  "PUBLIC_RSA3072_RECIPIENT_JSON"}


def _text(value, code, *, nullable=False):
    require(value is None and nullable or isinstance(value, str) and bool(value), code)


def _source_outline(text):
    """Read reviewed literal source layouts; do not evaluate YAML or expressions."""
    lines = text.split("\n")
    roots = [index for index, line in enumerate(lines) if line == "jobs:"]
    require(len(roots) == 1, "unsupported_jobs_layout")

    def source_fields(block, indent):
        result = {}
        pattern = re.compile(r"^" + " " * indent + r"([A-Za-z0-9_-]+):(?: (.*))?$")
        for index, line in enumerate(block):
            match = pattern.match(line)
            if not match:
                continue
            key, value = match.group(1), match.group(2) or ""
            require(key not in result, "duplicate_source_field")
            if value in ("|", "|-", ">", ">-"):
                end = index + 1
                while end < len(block) and (not block[end].strip()
                        or block[end].startswith(" " * (indent + 2))):
                    end += 1
                value += "\n" + "\n".join(block[index + 1:end])
            result[key] = value
        return result

    starts = []
    for index in range(roots[0] + 1, len(lines)):
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", lines[index])
        if match:
            starts.append((match.group(1), index))
    require(bool(starts), "missing_jobs")
    jobs = {}
    for position, (job_id, start) in enumerate(starts):
        require(job_id not in jobs, "duplicate_workflow_job")
        end = starts[position + 1][1] if position + 1 < len(starts) else len(lines)
        block = lines[start + 1:end]
        meta = source_fields(block, 4)
        step_root = [index for index, line in enumerate(block) if line == "    steps:"]
        require(len(step_root) == 1, "unsupported_steps_layout")
        step_starts = []
        for index in range(step_root[0] + 1, len(block)):
            match = re.match(r"^(    |      )- (.+)$", block[index])
            if match:
                step_starts.append((index, len(match.group(1)), match.group(2)))
        steps = []
        for number, (index, indent, first) in enumerate(step_starts):
            last = step_starts[number + 1][0] if number + 1 < len(step_starts) else len(block)
            part = block[index:last]
            values = source_fields([" " * (indent + 2) + first, *part[1:]], indent + 2)
            steps.append({"ordinal": number + 1, "name": values.get("name"),
                          "guard": values.get("if"), "run": values.get("run"),
                          "uses": values.get("uses"), "raw": "\n".join(part)})
        jobs[job_id] = {"source_name": meta.get("name", job_id),
                        "guard": meta.get("if"), "steps": steps, "raw": "\n".join(block)}
    preamble = "\n".join(lines[:roots[0]]) + ("\n" if roots[0] else "")
    return preamble, jobs



def _declared_inputs(preamble):
    """Read the closed literal dispatch-input forms used by reviewed definitions."""
    lines = preamble.split("\n")
    starts = [index for index, line in enumerate(lines)
              if re.fullmatch(r"  workflow_dispatch:\s*", line)]
    require(len(starts) <= 1, "ambiguous_dispatch_declaration")
    if not starts:
        return {}
    start = starts[0] + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#") and not line.startswith("    "):
            break
        end += 1
    block = lines[start:end]
    roots = [index for index, line in enumerate(block) if line == "    inputs:"]
    if not roots:
        return {}
    require(len(roots) == 1, "ambiguous_dispatch_inputs")
    declared = []
    for index in range(roots[0] + 1, len(block)):
        match = re.fullmatch(r"      ([A-Za-z0-9_-]+):\s*", block[index])
        if match:
            declared.append((match.group(1), index))

    def scalar(raw):
        if raw.startswith('"') and raw.endswith('"'):
            try:
                value = json.loads(raw)
            except ValueError as error:
                raise ScopeError("unsupported_input_scalar") from error
            require(isinstance(value, str), "unsupported_input_scalar")
            return value
        if raw.startswith("'") and raw.endswith("'"):
            return raw[1:-1].replace("''", "'")
        if raw == "true":
            return True
        if raw == "false":
            return False
        require(bool(raw) and re.fullmatch(r"[A-Za-z0-9_-]+", raw), "unsupported_input_scalar")
        return raw

    result = {}
    for position, (name, index) in enumerate(declared):
        require(name not in result, "duplicate_dispatch_input")
        last = declared[position + 1][1] if position + 1 < len(declared) else len(block)
        section = block[index + 1:last]
        values, options = {}, []
        for line in section:
            match = re.fullmatch(r"        (type|required|default|options):(?: (.*))?", line)
            if match:
                key, raw = match.group(1), match.group(2) or ""
                require(key not in values, "duplicate_dispatch_input_field")
                if key == "options":
                    values[key] = raw
                    if raw:
                        require(raw.startswith("[") and raw.endswith("]"), "unsupported_input_options")
                        options = [scalar(part.strip()) for part in raw[1:-1].split(",")]
                else:
                    values[key] = scalar(raw)
            else:
                option = re.fullmatch(r" {8,10}- (.+)", line)
                if option:
                    options.append(scalar(option.group(1)))
        kind = values.get("type")
        require(kind in ("choice", "boolean", "string"), "unsupported_dispatch_input_type")
        default = values.get("default", False if kind == "boolean" else "")
        result[name] = {"type": kind, "required": values.get("required", False),
                        "default": default, "options": options}
    return result


def _kind(step):
    if step["run"] is not None:
        return "EXECUTABLE_STEP"
    action = step["uses"] or ""
    if action.startswith("actions/upload-artifact@"):
        return "ARTIFACT_RETENTION"
    if action.startswith(("actions/dependency-review-action@", "actions/attest@")):
        return "VERIFICATION_ACTION"
    return None


def _input_value(value, spec):
    if isinstance(value, dict):
        fields(value, ("binding",), "catalog_input_binding_fields")
        require(spec["type"] == "string" and isinstance(value["binding"], str)
                and value["binding"] in INPUT_BINDINGS,
                "catalog_input_binding")
    elif spec["type"] == "boolean":
        require(type(value) is bool, "catalog_boolean_input")
    elif spec["type"] == "choice":
        require(isinstance(value, str) and value in spec["options"], "catalog_choice_input")
    else:
        require(isinstance(value, str), "catalog_string_input")


def _validate_reviewed_scope(entry):
    fields(entry, ("path", "sha256", "preamble_sha256", "evidence_class", "purpose",
                   "inputs", "input_dispositions", "jobs", "cases",
                   "job_dispositions", "limitations"), "reviewed_scope_fields")
    require(isinstance(entry["path"], str) and WORKFLOW.fullmatch(entry["path"]),
            "reviewed_scope_path")
    _sha(entry["sha256"], SHA256, "reviewed_scope_hash")
    _sha(entry["preamble_sha256"], SHA256, "reviewed_preamble_hash")
    _text(entry["evidence_class"], "reviewed_evidence_class")
    _text(entry["purpose"], "reviewed_purpose")
    strings(entry["limitations"], "reviewed_limitations")
    inputs = entry["inputs"]
    require(isinstance(inputs, dict), "reviewed_inputs")
    for name, spec in inputs.items():
        require(isinstance(name, str) and KEY.fullmatch(name), "reviewed_input_name")
        fields(spec, ("type", "required", "default", "options"), "reviewed_input_fields")
        require(isinstance(spec["type"], str) and spec["type"] in ("choice", "boolean", "string")
                and type(spec["required"]) is bool, "reviewed_input_type")
        if spec["type"] == "choice":
            strings(spec["options"], "reviewed_input_choices")
        else:
            require(spec["options"] == [], "reviewed_input_options")
        _input_value(spec["default"], spec)
    jobs = entry["jobs"]
    require(isinstance(jobs, dict) and bool(jobs), "reviewed_jobs")
    for job_id, job in jobs.items():
        require(isinstance(job_id, str) and KEY.fullmatch(job_id), "reviewed_job_id")
        fields(job, ("source_name", "guard", "runs_on", "needs", "strategy", "source_sha256",
                     "instance_sets", "steps"),
               "reviewed_job_fields")
        _sha(job["source_sha256"], SHA256, "reviewed_job_source_hash")
        _text(job["source_name"], "reviewed_job_name")
        _text(job["guard"], "reviewed_job_guard", nullable=True)
        require(isinstance(job["runs_on"], (str, list)) or job["runs_on"] is None,
                "reviewed_runner")
        require(isinstance(job["needs"], (str, list)) or job["needs"] is None,
                "reviewed_dependencies")
        require(job["strategy"] is None or isinstance(job["strategy"], dict),
                "reviewed_matrix")
        require(isinstance(job["instance_sets"], dict), "reviewed_instance_sets")
        for case_id, rows in job["instance_sets"].items():
            require(isinstance(case_id, str) and KEY.fullmatch(case_id) and isinstance(rows, list)
                    and bool(rows) and all(isinstance(row, dict) for row in rows),
                    "reviewed_instance_set")
            require(len({canonical(row) for row in rows}) == len(rows),
                    "duplicate_reviewed_matrix_member")
        require(isinstance(job["steps"], list), "reviewed_steps")
        seen = set()
        for step in job["steps"]:
            fields(step, ("ordinal", "name", "guard", "kind", "source_block_sha256", "api_binding"),
                   "reviewed_step_fields")
            ordinal = _number(step["ordinal"], "reviewed_step_ordinal")
            require(ordinal not in seen, "duplicate_reviewed_step")
            seen.add(ordinal)
            _text(step["name"], "reviewed_step_name", nullable=True)
            _text(step["guard"], "reviewed_step_guard", nullable=True)
            require(isinstance(step["kind"], str) and step["kind"] in ("EXECUTABLE_STEP", "ARTIFACT_RETENTION",
                                     "VERIFICATION_ACTION"), "reviewed_step_kind")
            _sha(step["source_block_sha256"], SHA256, "reviewed_step_hash")
            require(step["api_binding"] == ("EXPLICIT_NAME" if step["name"] else
                    "REQUIRES_ORIGINAL_RUN_MAPPING"), "reviewed_step_binding")
    require(isinstance(entry["cases"], list) and bool(entry["cases"]), "reviewed_cases")
    case_ids, covered_jobs, covered_steps = set(), set(), {key: set() for key in jobs}
    for case in entry["cases"]:
        fields(case, ("id", "inputs", "kind", "required_job_instances", "coverage_basis",
                      "original_attempt", "blocking_reason"), "reviewed_case_fields")
        require(isinstance(case["id"], str) and KEY.fullmatch(case["id"])
                and case["id"] not in case_ids, "reviewed_case_id")
        case_ids.add(case["id"])
        require(isinstance(case["kind"], str) and case["kind"] in PLAN_KINDS, "reviewed_case_kind")
        fields(case["inputs"], inputs, "reviewed_case_inputs")
        for name, value in case["inputs"].items():
            _input_value(value, inputs[name])
        _text(case["coverage_basis"], "reviewed_coverage_basis")
        require(case["original_attempt"] is None or type(case["original_attempt"]) is int
                and case["original_attempt"] == 1, "reviewed_original_attempt")
        _text(case["blocking_reason"], "reviewed_blocking_reason", nullable=True)
        require(bool(case["blocking_reason"]) == (case["kind"] in
                ("NEEDS_DISPOSITION", "PINNED_PRODUCT_DIAGNOSTIC")),
                "reviewed_disposition_reason")
        require(isinstance(case["required_job_instances"], list)
                and bool(case["required_job_instances"]), "reviewed_instances")
        instance_ids = set()
        for instance in case["required_job_instances"]:
            fields(instance, ("job_id", "display_name", "matrix", "name_binding", "required_steps"),
                   "reviewed_instance_fields")
            job_id = instance["job_id"]
            require(isinstance(job_id, str) and job_id in jobs, "reviewed_instance_job")
            _text(instance["display_name"], "reviewed_instance_name")
            require(isinstance(instance["matrix"], dict)
                    and all(isinstance(key, str) and KEY.fullmatch(key)
                            and type(value) in (str, int, bool)
                            for key, value in instance["matrix"].items()), "reviewed_instance_matrix")
            identity = canonical({"job_id": job_id, "matrix": instance["matrix"]})
            require(identity not in instance_ids, "duplicate_reviewed_instance")
            instance_ids.add(identity)
            require(instance["name_binding"] in
                    ("literal_definition", "reviewed_literal_matrix_expansion",
                     "proposed_github_default_requires_original_readback",
                     "reviewed_literal_input_expansion"),
                    "reviewed_instance_name_binding")
            ordinals = instance["required_steps"]
            require(isinstance(ordinals, list) and bool(ordinals)
                    and all(type(value) is int and value > 0 for value in ordinals)
                    and len(set(ordinals)) == len(ordinals), "reviewed_instance_steps")
            available = {step["ordinal"] for step in jobs[job_id]["steps"]}
            require(set(ordinals) <= available, "reviewed_instance_unknown_step")
            covered_jobs.add(job_id)
            covered_steps[job_id].update(ordinals)
        for job_id in {row["job_id"] for row in case["required_job_instances"]}:
            actual_set = {canonical(row["matrix"]) for row in case["required_job_instances"]
                          if row["job_id"] == job_id}
            expected_set = jobs[job_id]["instance_sets"].get(case["id"])
            require(isinstance(expected_set, list) and actual_set ==
                    {canonical(row) for row in expected_set}, "reviewed_matrix_member_missing")
    for job_id, job in jobs.items():
        expected_cases = {case["id"] for case in entry["cases"] if any(
            row["job_id"] == job_id for row in case["required_job_instances"])}
        require(set(job["instance_sets"]) == expected_cases, "reviewed_instance_set_cases")
    fields(entry["input_dispositions"], inputs, "reviewed_disposition_inputs")
    for name, spec in inputs.items():
        keys = spec["options"] if spec["type"] == "choice" else (
            ["true", "false"] if spec["type"] == "boolean" else [])
        dispositions = entry["input_dispositions"][name]
        fields(dispositions, keys, "reviewed_missing_input_disposition")
        for value, row in dispositions.items():
            fields(row, ("covered_by", "basis"), "reviewed_input_disposition_fields")
            strings(row["covered_by"], "reviewed_disposition_cases")
            require(set(row["covered_by"]) <= case_ids, "reviewed_disposition_case")
            _text(row["basis"], "reviewed_disposition_basis")
    require(isinstance(entry["job_dispositions"], list), "reviewed_job_dispositions")
    disposed = set()
    for row in entry["job_dispositions"]:
        fields(row, ("job_id", "guard", "kind", "reason"), "reviewed_job_disposition_fields")
        job_id = row["job_id"]
        require(isinstance(job_id, str) and job_id in jobs
                and job_id not in disposed and job_id not in covered_jobs,
                "reviewed_job_disposition_id")
        disposed.add(job_id)
        require(row["guard"] == jobs[job_id]["guard"] and
                row["kind"] == "ORIGINAL_PR_CHANGE_REVIEW", "reviewed_job_disposition")
        _text(row["reason"], "reviewed_job_disposition_reason")
    require(covered_jobs | disposed == set(jobs), "reviewed_uncovered_job")
    for job_id in covered_jobs:
        for step in jobs[job_id]["steps"]:
            # Failure diagnostics remain retained in the definition but are not
            # success-path obligations. No arbitrary skip waiver is supported.
            failure_only = step["guard"] in ("failure()", "failure() || cancelled()")
            require(step["ordinal"] in covered_steps[job_id] or failure_only,
                    "reviewed_uncovered_step")


def validate_catalog(catalog):
    if isinstance(catalog, dict) and catalog.get("schema") == CATALOG_SCHEMA:
        return _validate_observation_catalog(catalog)
    fields(catalog, ("schema", "workflows", "reviewed_scopes", "limitations"), "catalog_fields")
    require(catalog["schema"] == REVIEWED_CATALOG_SCHEMA, "catalog_schema")
    _validate_observation_catalog({"schema": CATALOG_SCHEMA, "workflows": catalog["workflows"]})
    strings(catalog["limitations"], "catalog_limitations")
    require(isinstance(catalog["reviewed_scopes"], list), "reviewed_scopes")
    seen = {entry["path"] for entry in catalog["workflows"]}
    for entry in catalog["reviewed_scopes"]:
        _validate_reviewed_scope(entry)
        require(entry["path"] not in seen, "duplicate_catalog_workflow")
        seen.add(entry["path"])
    return catalog


def review_scope_definition(raw, entry):
    require(isinstance(raw, bytes) and digest(raw) == entry["sha256"], "workflow_definition_changed")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ScopeError("workflow_encoding") from error
    preamble, jobs = _source_outline(text)
    require(_declared_inputs(preamble) == entry["inputs"], "workflow_declared_inputs_changed")
    require(digest(preamble.encode("utf-8")) == entry["preamble_sha256"],
            "workflow_dispatch_or_preamble_changed")
    require(set(jobs) == set(entry["jobs"]), "workflow_job_inventory_changed")
    for job_id, contract in entry["jobs"].items():
        actual = jobs[job_id]
        require(actual["guard"] == contract["guard"], "workflow_job_guard_changed")
        require(digest(actual["raw"].encode("utf-8")) == contract["source_sha256"],
                "workflow_job_source_changed")
        require(actual["source_name"] == contract["source_name"], "workflow_job_name_changed")
        source_steps = {step["ordinal"]: step for step in actual["steps"] if _kind(step)}
        require(set(source_steps) == {step["ordinal"] for step in contract["steps"]},
                "workflow_executable_step_inventory_changed")
        for step in contract["steps"]:
            original = source_steps[step["ordinal"]]
            require(original["guard"] == step["guard"], "workflow_step_guard_changed")
            require(original["name"] == step["name"] and _kind(original) == step["kind"],
                    "workflow_step_identity_changed")
            require(digest(original["raw"].encode("utf-8")) == step["source_block_sha256"],
                    "workflow_step_source_changed")


def _reviewed_obligations(source, workflows, entry, observations, gaps, dispositions):
    path = entry["path"]
    error = None
    try:
        require(path in workflows, "catalog_workflow_missing")
        review_scope_definition(workflows[path], entry)
    except ScopeError as caught:
        error = str(caught)
        gaps.append({"workflow": path, "code": error, "status": "NEEDS_REVIEW"})
    result = []
    for case in entry["cases"]:
        identity = {"source": source, "workflow": path, "workflow_sha256": entry["sha256"],
                    "case_id": case["id"], "inputs": case["inputs"],
                    "case_kind": case["kind"], "original_attempt": case["original_attempt"],
                    "required_job_instances": case["required_job_instances"]}
        row = {**identity, "obligation_id": digest(canonical(identity)),
               "definition_review": "NEEDS_REVIEW" if error else "REVIEWED",
               "purpose": entry["purpose"], "evidence_class": entry["evidence_class"],
               "coverage_basis": case["coverage_basis"], "limitations": entry["limitations"],
               "required_source_jobs": entry["jobs"],
               "execution_identity_role": ("INDEPENDENT_COMPONENT_ORIGINALS"
                    if case["kind"] == "COMPONENT_PREREQUISITE" else
                    "PINNED_PRODUCT_ORIGINALS" if case["kind"] == "PINNED_PRODUCT_DIAGNOSTIC"
                    else "EXACT_CANDIDATE_SOURCE"),
               "observation": {"status": "READBACK_REQUIRED",
                               "code": "scope_observation_binding_not_implemented",
                               "supplied_observation_count": len(observations.get((path, case["id"]), [])),
                               "evidence_admission": "NOT_PERFORMED"}}
        if error:
            row["observation"] = {"status": "UNREVIEWED_DEFINITION", "code": error}
        if case["kind"] in ("NEEDS_DISPOSITION", "PINNED_PRODUCT_DIAGNOSTIC"):
            dispositions.append({"workflow": path, "case_id": case["id"],
                                 "status": ("NEEDS_SOURCE_RECONCILIATION" if
                                    case["kind"] == "PINNED_PRODUCT_DIAGNOSTIC" else "NEEDS_DISPOSITION"),
                                 "reason": case["blocking_reason"]})
        row["planning_status"] = ("NEEDS_REVIEW" if error else
                                  "NEEDS_DISPOSITION" if case["kind"] == "NEEDS_DISPOSITION" else
                                  "NEEDS_SOURCE_RECONCILIATION" if case["kind"] == "PINNED_PRODUCT_DIAGNOSTIC"
                                  else "REVIEWED_SCOPE")
        # Supplying API success cannot implement a scope-specific evidence reader.
        # These records are intentionally never admitted or relabeled as a run.
        result.append(row)
    return result

def review_definition(raw, entry):
    require(isinstance(raw, bytes) and digest(raw) == entry["sha256"], "workflow_definition_changed")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ScopeError("workflow_encoding") from error
    blocks = _job_blocks(text)
    require(set(blocks) == set(entry["jobs"]), "workflow_job_inventory_changed")
    for job_id, job in entry["jobs"].items():
        block = blocks[job_id]
        require(_guard(block, 4) == job["guard"], "workflow_job_guard_changed")
        # The supported catalog has literal job names or GitHub's job-ID default.
        names = re.findall(r"(?m)^    name: (.+)$", block)
        require(len(names) <= 1 and (names[0] if names else job_id) == job["api_name"],
                "workflow_job_name_changed")
        for step in job["required_steps"]:
            require(_guard(_step_block(block, step["name"]), 8) == step["guard"],
                    "workflow_step_guard_changed")

def check_observation(observation, source, entry, case):
    fields(observation, ("schema", "workflow", "workflow_sha256", "case_id", "source",
                         "inputs", "run_json", "run_sha256", "jobs_json", "jobs_sha256"),
           "observation_fields")
    require(observation["schema"] == OBSERVATION_SCHEMA, "observation_schema")
    require(observation["source"] == source, "observation_source_mismatch")
    require(observation["workflow"] == entry["path"]
            and observation["workflow_sha256"] == entry["sha256"], "observation_workflow_mismatch")
    require(observation["case_id"] == case["id"] and observation["inputs"] == case["inputs"],
            "observation_scope_mismatch")
    originals = {}
    for kind in ("run", "jobs"):
        body = observation[kind + "_json"]
        require(isinstance(body, str), "original_json_required")
        raw = body.encode("utf-8")
        _sha(observation[kind + "_sha256"], SHA256, "original_hash_invalid")
        require(digest(raw) == observation[kind + "_sha256"], "original_hash_mismatch")
        originals[kind] = decode(raw)
    run = originals["run"]
    require(isinstance(run, dict), "original_run_object")
    run_id = _number(run.get("id"), "run_id")
    attempt = _number(run.get("run_attempt"), "run_attempt")
    require(run.get("repository", {}).get("full_name") == source["repository"],
            "run_repository_mismatch")
    require(run.get("head_sha") == source["sha"] and run.get("path") == entry["path"],
            "run_source_or_workflow_mismatch")
    require(run.get("event") in case["events"], "run_event_mismatch")
    if run["event"] == "push":
        require(run.get("head_branch") == case["push_branch"], "run_push_branch_mismatch")
    require(run.get("display_title") == case["run_title"].replace("{source_sha}", source["sha"]),
            "run_original_scope_title_mismatch")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "run_not_successful")
    container = originals["jobs"]
    require(isinstance(container, dict) and isinstance(container.get("jobs"), list),
            "original_jobs_object")
    jobs = container["jobs"]
    require(type(container.get("total_count")) is int and container["total_count"] == len(jobs)
            and bool(jobs), "jobs_pagination_incomplete")
    expected_names = {job["api_name"] for job in entry["jobs"].values()}
    actual, identities = {}, set()
    for job in jobs:
        require(isinstance(job, dict), "job_object")
        job_id = _number(job.get("id"), "job_id")
        require(job_id not in identities, "duplicate_job_id")
        identities.add(job_id)
        name = job.get("name")
        require(isinstance(name, str) and name in expected_names and name not in actual,
                "unknown_or_duplicate_job_name")
        require(type(job.get("run_id")) is int and job["run_id"] == run_id
                and type(job.get("run_attempt")) is int and job["run_attempt"] == attempt
                and job.get("head_sha") == source["sha"], "job_source_or_attempt_mismatch")
        actual[name] = job
    checked = []
    for job_key in case["required_jobs"]:
        contract = entry["jobs"][job_key]
        require(contract["api_name"] in actual, "required_job_missing")
        job = actual[contract["api_name"]]
        require(job.get("status") == "completed" and job.get("conclusion") == "success",
                "required_job_not_successful")
        require(isinstance(job.get("steps"), list) and job["steps"], "job_steps_missing")
        step_names, numbers = {}, set()
        for step in job["steps"]:
            require(isinstance(step, dict), "step_object")
            number = _number(step.get("number"), "step_number")
            require(number not in numbers, "duplicate_step_number")
            numbers.add(number)
            name = step.get("name")
            require(isinstance(name, str) and name and name not in step_names, "duplicate_step_name")
            step_names[name] = step
        checked_steps = []
        for requirement in contract["required_steps"]:
            name = requirement["name"]
            require(name in step_names, "required_step_missing")
            step = step_names[name]
            require(step.get("status") == "completed" and step.get("conclusion") == "success",
                    "required_step_not_successful")
            checked_steps.append({"name": name, "number": step["number"]})
        checked.append({"workflow_job": job_key, "job_id": job["id"], "name": job["name"],
                        "required_steps": checked_steps})
    return {"status": "UNADMITTED_EXECUTION_OBSERVATIONS", "run_id": run_id,
            "run_attempt": attempt, "source_sha": source["sha"],
            "workflow_definition_sha": source["sha"], "workflow_sha256": entry["sha256"],
            "original_run_sha256": observation["run_sha256"],
            "original_jobs_sha256": observation["jobs_sha256"], "jobs": checked,
            "scope_evidence_readback": "NOT_PERFORMED",
            "originals_authentication": "CALLER_MUST_VERIFY_RETAINED_GITHUB_RESPONSES"}


def _build_observation_plan(source, workflows, catalog, observations=None):
    fields(source, ("repository", "sha", "tree"), "source_fields")
    require(isinstance(source["repository"], str)
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]),
            "source_repository")
    _sha(source["sha"], SHA40, "source_sha")
    _sha(source["tree"], SHA40, "source_tree")
    require(isinstance(workflows, dict) and bool(workflows), "workflow_inventory_empty")
    for path, raw in workflows.items():
        require(isinstance(path, str) and WORKFLOW.fullmatch(path) and isinstance(raw, bytes),
                "workflow_inventory_invalid")
    validate_catalog(catalog)
    observations = [] if observations is None else observations
    require(isinstance(observations, list), "observations_array")
    entries = {entry["path"]: entry for entry in catalog["workflows"]}
    gaps, obligations, observation_errors = [], [], []
    observation_map = {}
    for observation in observations:
        if not isinstance(observation, dict):
            observation_errors.append({"code": "observation_object"})
            continue
        key = (observation.get("workflow"), observation.get("case_id"))
        if not all(isinstance(value, str) for value in key):
            observation_errors.append({"code": "observation_key"})
            continue
        observation_map.setdefault(key, []).append(observation)
    known_keys = {(entry["path"], case["id"]) for entry in entries.values() for case in entry["cases"]}
    for key in sorted(set(observation_map) - known_keys):
        observation_errors.append({"workflow": key[0], "case_id": key[1], "code": "unreviewed_observation_scope"})
    for path in sorted(set(workflows) | set(entries)):
        if path not in entries:
            gaps.append({"workflow": path, "code": "workflow_not_reviewed", "status": "NEEDS_REVIEW"})
            continue
        entry = entries[path]
        review_error = None
        try:
            require(path in workflows, "catalog_workflow_missing")
            review_definition(workflows[path], entry)
        except ScopeError as error:
            review_error = str(error)
            gaps.append({"workflow": path, "code": review_error, "status": "NEEDS_REVIEW"})
        for case in entry["cases"]:
            identity = {"source": source, "workflow": path, "workflow_sha256": entry["sha256"],
                        "case_id": case["id"], "inputs": case["inputs"],
                        "required_jobs": {key: entry["jobs"][key] for key in case["required_jobs"]}}
            row = {**identity, "obligation_id": digest(canonical(identity)),
                   "definition_review": "NEEDS_REVIEW" if review_error else "REVIEWED",
                   "purpose": case["purpose"], "limitations": entry["limitations"],
                   "observation": {"status": "MISSING"}}
            candidates = observation_map.get((path, case["id"]), [])
            if review_error:
                row["observation"] = {"status": "UNREVIEWED_DEFINITION", "code": review_error}
            elif len(candidates) > 1:
                row["observation"] = {"status": "REJECTED", "code": "select_one_original_run_attempt"}
            elif candidates:
                try:
                    row["observation"] = check_observation(candidates[0], source, entry, case)
                except (ScopeError, TypeError, AttributeError) as error:
                    row["observation"] = {"status": "REJECTED",
                                          "code": str(error) if isinstance(error, ScopeError)
                                          else "observation_structure_invalid"}
            obligations.append(row)
    plan_complete = not gaps
    observed_complete = (plan_complete and bool(obligations) and not observation_errors
                         and all(row["observation"]["status"] == "UNADMITTED_EXECUTION_OBSERVATIONS"
                                 for row in obligations))
    result = {
        "schema": "motionwright.release-scope-plan/1", "mode": "read-only",
        "source": source, "catalog_sha256": digest(canonical(catalog)),
        "workflow_inventory_sha256": {path: digest(raw) for path, raw in sorted(workflows.items())},
        "scope_plan_complete": plan_complete,
        "scope_observations_complete": observed_complete,
        "status": "NEEDS_REVIEW" if gaps else
                  ("UNADMITTED_EXECUTION_OBSERVATIONS" if observed_complete else "INCOMPLETE"),
        "review_gaps": gaps, "obligations": obligations, "observation_errors": observation_errors,
        "evidence_admission": "NOT_PERFORMED", "candidate_readiness": "NOT_ASSESSED",
        "technical_criteria_verdict": "NOT_GRANTED", "release_verdict": "NOT_GRANTED",
        "human_acceptance": "NOT_RUN", "automatic_dispatch": False, "retry_authorized": False,
        "accounting": {"executions_avoided": 0, "minutes_saved": 0,
                       "reason": "read-only-planning-does-not-execute-or-cancel-workflows"}}
    result["plan_id"] = digest(canonical(result))
    return result



def build_plan(source, workflows, catalog, observations=None):
    """Describe reviewed cases; an unsupported reader never admits observations."""
    validate_catalog(catalog)
    if catalog["schema"] == CATALOG_SCHEMA:
        result = _build_observation_plan(source, workflows, catalog, observations)
        result["definition_review_complete"] = not result["review_gaps"]
        result["scope_dispositions"] = []
        del result["plan_id"]
        result["plan_id"] = digest(canonical(result))
        return result
    observations = [] if observations is None else observations
    require(isinstance(observations, list), "observations_array")
    reviewed = {entry["path"]: entry for entry in catalog["reviewed_scopes"]}
    reviewed_keys = {(path, case["id"]) for path, entry in reviewed.items()
                     for case in entry["cases"]}
    mapped, supported_or_unknown = {}, []
    for observation in observations:
        if isinstance(observation, dict):
            key = (observation.get("workflow"), observation.get("case_id"))
            if all(isinstance(part, str) for part in key) and key in reviewed_keys:
                mapped.setdefault(key, []).append(observation)
                continue
        supported_or_unknown.append(observation)
    result = _build_observation_plan(
        source, workflows, {"schema": CATALOG_SCHEMA, "workflows": catalog["workflows"]},
        supported_or_unknown)
    gaps = [gap for gap in result["review_gaps"] if not (
        gap["code"] == "workflow_not_reviewed" and gap["workflow"] in reviewed)]
    dispositions = []
    for path, entry in sorted(reviewed.items()):
        result["obligations"].extend(
            _reviewed_obligations(source, workflows, entry, mapped, gaps, dispositions))
    definition_complete = not gaps
    plan_complete = definition_complete and not dispositions
    observed_complete = (plan_complete and bool(result["obligations"])
                         and not result["observation_errors"]
                         and all(row["observation"]["status"] ==
                                 "UNADMITTED_EXECUTION_OBSERVATIONS"
                                 for row in result["obligations"]))
    result.update(
        schema="motionwright.release-scope-plan/2",
        catalog_sha256=digest(canonical(catalog)),
        catalog_schema=catalog["schema"],
        definition_review_complete=definition_complete,
        scope_plan_complete=plan_complete,
        scope_observations_complete=observed_complete,
        review_gaps=gaps,
        scope_dispositions=dispositions,
        planning_limitations=catalog["limitations"],
        authority={"planning": "DESCRIPTIVE_ONLY", "ownership_or_budget_lease": "NOT_VALIDATED",
                   "authorizes_dispatch": False, "execution_admission": "NOT_PERFORMED"},
        status=("NEEDS_REVIEW" if gaps else "NEEDS_DISPOSITION" if dispositions else
                "UNADMITTED_EXECUTION_OBSERVATIONS" if observed_complete else
                "READBACK_REQUIRED" if reviewed else "INCOMPLETE"))
    result["definitions"] = [
        {"workflow": path, "definition_review": (
            "NEEDS_REVIEW" if any(gap.get("workflow") == path for gap in gaps) else "REVIEWED")}
        for path in sorted(set(workflows) | set(reviewed)
                           | {entry["path"] for entry in catalog["workflows"]})]
    del result["plan_id"]
    result["plan_id"] = digest(canonical(result))
    return result


def _git(repo, *arguments):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0")
    try:
        process = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScopeError("git_read_unavailable") from error
    require(process.returncode == 0, "git_read_failed")
    return process.stdout


def read_source(repo, sha):
    _sha(sha, SHA40, "source_sha")
    require(_git(repo, "cat-file", "-t", sha).strip() == b"commit", "source_not_commit")
    tree = _git(repo, "cat-file", "-p", sha).split(b"\n", 1)[0]
    require(tree.startswith(b"tree "), "commit_tree_missing")
    tree = tree[5:].decode("ascii")
    _sha(tree, SHA40, "source_tree")
    records = _git(repo, "ls-tree", "-rz", "--full-tree", sha, "--", ".github/workflows",
                   CATALOG_PATH, SCRIPT_PATH).split(b"\0")
    files = {}
    for record in filter(None, records):
        meta, encoded_path = record.split(b"\t", 1)
        mode, kind, oid = meta.split(b" ")
        path = encoded_path.decode("utf-8")
        if path not in (CATALOG_PATH, SCRIPT_PATH) and not WORKFLOW.fullmatch(path):
            continue
        require(mode == b"100644" or mode == b"100755", "source_file_not_regular")
        require(kind == b"blob", "source_file_not_blob")
        size = int(_git(repo, "cat-file", "-s", oid.decode("ascii")).strip())
        require(0 < size <= 2 * 1024 * 1024, "source_file_size")
        raw = _git(repo, "cat-file", "blob", oid.decode("ascii"))
        require(len(raw) == size, "source_blob_size_changed")
        files[path] = raw
    return tree, files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--planner-sha", help="Exact helper/catalog commit; defaults to source SHA")
    parser.add_argument("--observations", type=Path,
                        help="Retained original API response bundles; read only, never authenticated here")
    args = parser.parse_args(argv)
    try:
        tree, files = read_source(args.repo, args.source_sha)
        planner_sha = args.planner_sha or args.source_sha
        planner_tree, planner_files = ((tree, files) if planner_sha == args.source_sha
                                       else read_source(args.repo, planner_sha))
        require(CATALOG_PATH in planner_files and SCRIPT_PATH in planner_files,
                "source_planner_or_catalog_missing")
        actual = Path(__file__).read_bytes()
        expected = planner_files[SCRIPT_PATH]
        require(actual == expected or
                (os.name == "nt" and b"\r" not in expected
                 and actual == expected.replace(b"\n", b"\r\n")), "executed_planner_source_mismatch")
        observations = read_json_file(args.observations) if args.observations else None
        plan = build_plan({"repository": args.repository, "sha": args.source_sha, "tree": tree},
                          {path: raw for path, raw in files.items() if WORKFLOW.fullmatch(path)},
                          decode(planner_files[CATALOG_PATH]), observations)
        plan["execution"] = {"planner_source_sha": planner_sha, "planner_tree_sha": planner_tree,
                             "catalog_source_sha": planner_sha,
                             "planner_git_sha256": digest(expected),
                             "planner_checkout_sha256": digest(actual),
                             "catalog_git_sha256": digest(planner_files[CATALOG_PATH])}
        # Include actual implementation identity in the report digest.
        del plan["plan_id"]
        plan["plan_id"] = digest(canonical(plan))
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0 if plan["scope_plan_complete"] and plan["scope_observations_complete"] else 3
    except (ScopeError, OSError, UnicodeError, ValueError) as error:
        code = str(error) if isinstance(error, ScopeError) else "source_or_input_unavailable"
        print(json.dumps({"schema": "motionwright.release-scope-refusal/1", "code": code,
                          "release_verdict": "NOT_GRANTED", "retry_authorized": False}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
