#!/usr/bin/env python3
"""Read-only, source-bound release scope planning; never a release verdict."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
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
TYPED_LOCATORS_SCHEMA = "motionwright.release-reader-locators/1"
TYPED_BINDINGS_SCHEMA = "motionwright.release-reader-bindings/1"
RETAINED_COMPILE_FAMILY = "nsis-compile-originals/1"
# Reviewed implementation identities, not caller-supplied executable code. The
# optional private reader is read as data and is never imported or executed.
RETAINED_READERS = {RETAINED_COMPILE_FAMILY: {
    "bytes": 52442,
    "sha256": "dfe02ef9627e4d2395964a6c7a2e31a4abbc8dc73143add5fd032da1fde3140e"}}
TYPED_MAX_SINGLE = 128 * 1024 * 1024
TYPED_MAX_CAPTURE = 2 * 1024 * 1024
TYPED_MAX_STREAMED = 512 * 1024 * 1024


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
                  "PUBLIC_RSA3072_RECIPIENT_JSON",
                  "FIXEDTXT_WORKFLOW_ORIGINAL_REQUEST_JSON",
                  "FIXEDTXT_R28_ORIGINAL_DISPATCH_JSON",
                  "FIXEDTXT_R28_ORIGINAL_DELIVERY_JSON"}


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
            steps.append({"ordinal": number + 1, "id": values.get("id"),
                          "name": values.get("name"),
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



# Exact cumulative Graph source diagnostics. Raw source blocks are authenticated
# again by review_scope_definition, and their earlier positive archive anchor
# must remain required in every case. No arbitrary guard or hash is exempted.
REVIEWED_FAILURE_DIAGNOSTICS = {'64e62942aeb82cee4b8a85a36f1383e064660d61404703b083b747478b9ca4db': ['EXECUTABLE_STEP', 'Admit the complete Service47 unsealed upload', "always() && matrix.selection == 'phase1-service47' && steps.archive.outcome != 'success'", 'archive'], 'bc2d20cf3bbf830078b9b84d3b601fa6b1920e455d3f509a8622a5dbc023fdda': ['ARTIFACT_RETENTION', 'Preserve unsealed diagnostics when archive custody fails', "always() && steps.archive.outcome != 'success' && (matrix.selection != 'phase1-service47' || steps.phase1-unsealed-upload-admission.outputs.admitted == 'true') && (matrix.selection != 'phase1-native-graph' || steps.graph-final-upload-admission.outputs.admitted == 'true')", 'archive']}


def _retention_failure_reference(step):
    """Recognize reviewed failure diagnostics and authenticate their positive anchor."""
    reviewed = REVIEWED_FAILURE_DIAGNOSTICS.get(step.get("source_block_sha256"))
    if reviewed is not None and [step.get("kind"), step.get("name"), step.get("guard")] == reviewed[:3]:
        return reviewed[3]
    if step["kind"] != "ARTIFACT_RETENTION":
        return None
    match = re.fullmatch(
        r"always\(\) && steps\.([A-Za-z_][A-Za-z0-9_-]*)\.outcome != 'success'",
        step["guard"] or "")
    return match.group(1) if match else None


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
            failure_only = (step["guard"] in ("failure()", "failure() || cancelled()")
                            or _retention_failure_reference(step) is not None)
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

        for step in contract["steps"]:
            reference = _retention_failure_reference(step)
            if reference is None:
                continue
            anchors = [item for item in actual["steps"] if item["id"] == reference]
            require(len(anchors) == 1, "diagnostic_retention_anchor")
            anchor = anchors[0]
            require(anchor["ordinal"] < step["ordinal"]
                    and _kind(anchor) == "EXECUTABLE_STEP"
                    and anchor["guard"] == "success()", "diagnostic_retention_anchor")
            for case in entry["cases"]:
                for instance in case["required_job_instances"]:
                    if instance["job_id"] != job_id:
                        continue
                    require(anchor["ordinal"] in instance["required_steps"],
                            "diagnostic_retention_anchor_not_required")
                    require(step["ordinal"] not in instance["required_steps"],
                            "diagnostic_retention_positive_obligation")


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


def _typed_identity(info):
    # Reading can update atime. It is not a content or replacement identity.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", 0))


def _typed_stat(path, *, directory=False):
    require(path.is_absolute() and ".." not in path.parts, "typed_path_not_absolute")
    current = path
    while True:
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "typed_link_or_reparse")
        if current.parent == current:
            break
        current = current.parent
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "typed_file_type")
    if not directory:
        require(0 <= info.st_size <= TYPED_MAX_SINGLE, "typed_file_size")
    return info


def _typed_descriptor_matches(path, named, opened):
    named_id, opened_id = _typed_identity(named), _typed_identity(opened)
    if named_id == opened_id:
        return True
    # Windows Python can infer executable bits from the .exe filename for
    # lstat, while the already-open descriptor reports rw bits without them.
    # Admit only that observed delta. All remaining fields and permission bits
    # still match; path/path and descriptor/descriptor stability use full mode.
    return (os.name == "nt" and path.suffix.lower() == ".exe"
            and stat.S_ISREG(named.st_mode) and stat.S_ISREG(opened.st_mode)
            and opened.st_mode & 0o111 == 0
            and named.st_mode == opened.st_mode | 0o111
            and named_id[:2] + named_id[3:] == opened_id[:2] + opened_id[3:])


def _typed_read(path, expected, budget, *, capture=False):
    """Two bounded byte reads, each tied to its descriptor and named original."""
    path = Path(path)
    require(isinstance(expected, dict) and type(expected.get("bytes")) is int
            and 0 <= expected["bytes"] <= TYPED_MAX_SINGLE, "typed_expected_size")
    _sha(expected.get("sha256"), SHA256, "typed_expected_hash")
    require(not capture or expected["bytes"] <= TYPED_MAX_CAPTURE, "typed_capture_size")
    before = _typed_stat(path)
    require(before.st_size == expected["bytes"], "typed_original_size_mismatch")
    identity, first, raw = _typed_identity(before), None, None
    for pass_index in range(2):
        require(_typed_identity(_typed_stat(path)) == identity, "typed_original_changed")
        hasher, count, chunks = hashlib.sha256(), 0, []
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            require(_typed_descriptor_matches(path, before, opened),
                    "typed_opened_identity_mismatch")
            opened_identity = _typed_identity(opened)
            while True:
                chunk = stream.read(min(256 * 1024, expected["bytes"] - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                budget["streamed_bytes"] += len(chunk)
                require(count <= expected["bytes"], "typed_original_grew")
                require(budget["streamed_bytes"] <= TYPED_MAX_STREAMED, "typed_stream_budget")
                hasher.update(chunk)
                if capture and pass_index == 0:
                    chunks.append(chunk)
            require(_typed_identity(os.fstat(stream.fileno())) == opened_identity,
                    "typed_original_changed")
        require(_typed_identity(_typed_stat(path)) == identity, "typed_original_changed")
        observed = {"bytes": count, "sha256": hasher.hexdigest()}
        require(observed == {key: expected[key] for key in ("bytes", "sha256")},
                "typed_original_digest_mismatch")
        require(first is None or observed == first, "typed_read_passes_disagree")
        first = observed
        if capture and pass_index == 0:
            raw = b"".join(chunks)
    budget["files_read"] += 1
    return raw


def _typed_member(name):
    require(isinstance(name, str) and name and "\\" not in name
            and ":" not in name and "\0" not in name,
            "typed_unsafe_member")
    require(not PurePosixPath(name).is_absolute()
            and all(part and part not in (".", "..") and not part.endswith((" ", "."))
                    and not re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                    for part in name.split("/")), "typed_unsafe_member")
    return name


def _typed_inventory(root):
    _typed_stat(root, directory=True)
    pending, names, folded, entries = [root], set(), set(), 0
    while pending:
        current = pending.pop()
        with os.scandir(current) as children:
            for child in children:
                entries += 1
                require(entries <= 256, "typed_extraction_entry_limit")
                path = Path(child.path)
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _typed_stat(path, directory=True)
                    pending.append(path)
                else:
                    _typed_stat(path)
                    name = _typed_member(path.relative_to(root).as_posix())
                    require(name.casefold() not in folded, "typed_member_collision")
                    names.add(name)
                    folded.add(name.casefold())
    return names


def _retained_reader_constants(raw):
    """Read only literal declarations from an already hash-pinned reader."""
    selected = {"PINS", "GATE", "PRODUCT", "TREES", "RUN", "ATTEMPT", "REPO",
                "WORKFLOW", "GIT_BINDING_SHA", "GIT_BINDING_BYTES"}
    result = {}
    for node in ast.parse(raw.decode("utf-8")).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        names = ([target.id] if isinstance(target, ast.Name) else
                 [part.id for part in target.elts] if isinstance(target, ast.Tuple)
                 and all(isinstance(part, ast.Name) for part in target.elts) else [])
        if not selected.intersection(names):
            continue
        if names == ["PINS"]:
            value = node.value
            require(isinstance(value, ast.Call) and not value.keywords and len(value.args) == 1
                    and isinstance(value.func, ast.Attribute) and value.func.attr == "loads"
                    and isinstance(value.func.value, ast.Name) and value.func.value.id == "json",
                    "typed_reader_declaration")
            encoded = ast.literal_eval(value.args[0])
            require(isinstance(encoded, str), "typed_reader_declaration")
            values = [decode(encoded.encode("utf-8"))]
        else:
            literal = ast.literal_eval(node.value)
            values = [literal] if isinstance(target, ast.Name) else list(literal)
        require(len(values) == len(names), "typed_reader_declaration")
        for name, value in zip(names, values):
            if name in selected:
                require(name not in result, "typed_reader_duplicate_declaration")
                result[name] = value
    require(set(result) == selected, "typed_reader_declaration_missing")
    return result


def _retained_compile_readback(locator, binding, candidate):
    """Reuse exact retained inputs; do not reexecute the private reader or product."""
    fields(locator, ("family", "reader", "report", "execution_receipt", "originals",
                     "integrity", "git_bindings"), "typed_locator_fields")
    fields(binding, ("family", "report", "execution_receipt"), "typed_binding_fields")
    expected_reader = RETAINED_READERS[locator["family"]]
    budget = {"streamed_bytes": 0, "files_read": 0}
    reader_path, report_path, execution_path = (
        Path(locator[key]) for key in ("reader", "report", "execution_receipt"))
    reader_raw = _typed_read(reader_path, expected_reader, budget, capture=True)
    constants = _retained_reader_constants(reader_raw)
    pins = constants["PINS"]
    execution = decode(_typed_read(execution_path, binding["execution_receipt"], budget, capture=True))
    require(isinstance(execution, dict), "typed_execution_object_required")
    if execution.get("status") == "UNKNOWN" or execution.get("exit_code") is None:
        raise ScopeError("typed_execution_unknown")
    require(execution.get("schema") == "motionwright.a6.read-only-original-admission-execution/1"
            and execution.get("status") == "READER_EXIT_OBSERVED"
            and type(execution.get("exit_code")) is int and execution["exit_code"] == 0,
            "typed_reader_execution_not_successful")
    report = decode(_typed_read(report_path, binding["report"], budget, capture=True))
    require(isinstance(report, dict), "typed_report_object_required")
    for key in ("native_retry", "originals_written", "product_execution"):
        require(execution.get(key) is False, "typed_execution_effect_not_read_only")
    require(execution.get("source_unchanged") is True
            and execution.get("source_before") == execution.get("source_after"),
            "typed_reader_source_changed")
    require({key: execution["report"][key] for key in ("bytes", "sha256")} == binding["report"],
            "typed_execution_report_binding")
    require(report.get("schema") == "motionwright.private-nsis-compile-admission/1"
            and report.get("status") ==
            "EXACT_TWO_VARIANT_COMPILE_OBSERVATIONS_ADMITTED_WITH_LIMITATIONS",
            "typed_retained_report_scope")
    require({key: report["reader_implementation"][key] for key in ("bytes", "sha256")}
            == expected_reader, "typed_report_reader_identity")
    expected_source = {"gate_sha": constants["GATE"], "gate_tree": constants["TREES"]["gate"],
                       "product_sha": constants["PRODUCT"], "product_tree": constants["TREES"]["product"]}
    expected_github = {"repository": constants["REPO"], "run_id": constants["RUN"],
                       "attempt": constants["ATTEMPT"], "event": "workflow_dispatch",
                       "workflow": constants["WORKFLOW"],
                       "jobs": {pin["name"]: int(key) for key, pin in pins["jobs"].items()},
                       "artifact_ids": pins["artifact_ids"]}
    require(canonical(report["source"]) == canonical(expected_source)
            and canonical(report["github"]) == canonical(expected_github),
            "typed_retained_identity_mismatch")
    require(report["originals"]["archives"] == pins["artifacts"], "typed_archive_roster")
    require(report["originals"]["all_77_zip_members_crc_and_sha256_reread"] is True
            and report["originals"]["all_77_retained_extraction_members_sha256_reread"] is True
            and report["originals"]["original_bytes_modified"] is False,
            "typed_prior_original_read_incomplete")
    require(report["unexecuted_scopes"] == {
        "physical_gate_implementation": "NOT_IMPLEMENTED_IN_THIS_GATE", "setup_execution": "NOT_RUN",
        "application_execution": "NOT_RUN", "installation": "NOT_RUN", "upgrade": "NOT_RUN",
        "interruption": "NOT_RUN", "recovery": "NOT_RUN", "installed_edit_reopen": "NOT_RUN",
        "predecessor_profile": "NOT_ADMITTED", "human_acceptance": "NOT_RUN"},
        "typed_compile_scope_promoted")
    require(report["decision_limits"]["technical_criteria_verdict"] == "NOT_ISSUED"
            and report["decision_limits"]["release_readiness"] == "NOT_EVALUATED"
            and all(report["decision_limits"][key] is False for key in (
                "release_publication", "other_sha_result_transfer", "autodispatch_or_retry",
                "product_reexecuted_by_reader")), "typed_retained_verdict_promoted")
    # Rebase retained control files only by their recorded basename. No path or
    # command from a report is executed, downloaded, imported or extracted.
    before = execution["source_before"]
    require(isinstance(before, list) and len(before) == 3, "typed_reader_source_roster")
    require({key: before[0][key] for key in ("bytes", "sha256")} == expected_reader,
            "typed_execution_reader_identity")
    require(before[0]["path"] == report["reader_implementation"]["path"],
            "typed_recorded_reader_path")
    names = set()
    for item in before:
        name = _typed_member(PureWindowsPath(item["path"]).name)
        require(name.casefold() not in names, "typed_reader_source_collision")
        names.add(name.casefold())
        _typed_read(reader_path if item is before[0] else reader_path.parent / name, item, budget)
    for key in ("stdout", "stderr", "input_control_receipt"):
        item = execution[key]
        _typed_read(execution_path.parent / _typed_member(PureWindowsPath(item["path"]).name), item, budget)
    originals, integrity = Path(locator["originals"]), Path(locator["integrity"])
    metadata = {}
    require(set(report["originals"]["metadata"]) == set(pins["metadata"]), "typed_metadata_roster")
    for name, canonical_pin in pins["metadata"].items():
        _typed_member(name)
        root = integrity if name == "custody.json" else originals
        value = decode(_typed_read(root / name, report["originals"]["metadata"][name], budget, capture=True))
        reader_canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(digest(reader_canonical) == canonical_pin, "typed_reader_original_binding")
        metadata[name] = value
    _typed_read(Path(locator["git_bindings"]), {
        "bytes": constants["GIT_BINDING_BYTES"], "sha256": constants["GIT_BINDING_SHA"]}, budget)
    for job in pins["jobs"].values():
        item = job["log"]
        _typed_read(originals / _typed_member(item["path"]), item, budget)
    custody = metadata["custody.json"]
    require([entry["id"] for entry in custody["artifacts"]] == pins["artifact_ids"],
            "typed_custody_artifact_roster")
    member_count = 0
    for entry in custody["artifacts"]:
        artifact_id, members = entry["id"], entry["members"]
        pin = pins["artifacts"][str(artifact_id)]
        _typed_read(originals / ("artifact-" + str(artifact_id) + ".zip.original"), pin, budget)
        require(len(members) == pin["member_count"], "typed_member_count")
        names = [_typed_member(member["path"]) for member in members]
        require(len(names) == len({name.casefold() for name in names}), "typed_member_collision")
        extraction = integrity / "extract" / ("artifact-" + str(artifact_id))
        require(_typed_inventory(extraction) == set(names), "typed_retained_member_roster")
        for member in members:
            require(member["directory"] is False and member["original_crc_checked"] is True
                    and member["retained_in_original_zip"] is True,
                    "typed_prior_member_read_incomplete")
            require(member["extracted_path"] == "extract/artifact-" + str(artifact_id) + "/" + member["path"],
                    "typed_extraction_binding")
            _typed_read(extraction / member["path"], member, budget)
        require(sum(member["bytes"] for member in members) == pin["expanded_bytes"],
                "typed_expanded_size")
        member_count += len(members)
    # Detect replacement after dependent reads; this is not an atomic snapshot.
    for path, identity in ((reader_path, expected_reader), (report_path, binding["report"]),
                           (execution_path, binding["execution_receipt"])):
        _typed_read(path, identity, budget)
    return {
        "family": locator["family"], "status": "HISTORICAL_RETAINED_BYTES_VERIFIED",
        "candidate_disposition": "READBACK_REQUIRED",
        "retained_result_reuse": "EXACT_RETAINED_READER_RESULT_WITH_EXPLICIT_BINDING",
        "binding_authentication": "EXTERNAL_REVIEW_REQUIRED_NOT_AUTHENTICATED_BY_PLANNER",
        "reader_implementation": expected_reader, "retained_report": binding["report"],
        "retained_execution_receipt": binding["execution_receipt"],
        "source": expected_source, "github": expected_github,
        "required_steps": {key: value["steps"] for key, value in pins["jobs"].items()},
        "original_archives": pins["artifacts"], "retained_members_rehashed": member_count,
        "requested_product_identity_matches": candidate["repository"] == constants["REPO"]
            and candidate["sha"] == constants["PRODUCT"] and candidate["tree"] == constants["TREES"]["product"],
        "scope": "COMPILE_ONLY", "unexecuted_scopes": report["unexecuted_scopes"],
        "reader_execution": "NOT_REEXECUTED", "product_execution": "NOT_RUN",
        "zip_crc_revalidation": "NOT_REPEATED_EXACT_ARCHIVE_BYTES_OF_RETAINED_READER_VERIFIED",
        "input_stability": "TWO_HASH_MATCHING_READS_WITH_DESCRIPTOR_AND_PATH_BINDING",
        "atomic_whole_corpus_snapshot": False, "writes_after_final_observation": "NOT_EXCLUDED",
        "evidence_admission": "NOT_PERFORMED", "release_verdict": "NOT_GRANTED",
        "human_acceptance": "NOT_RUN", "retry_authorized": False,
        "resources": budget}


def attach_typed_readbacks(plan, locators, bindings):
    """Add optional historical readbacks without changing scope completeness.

    Bindings are a separate, explicitly reviewed input. They authenticate no
    person or service here. Their digest is recorded; absent, unknown or invalid
    families remain READBACK_REQUIRED. API conclusions cannot supply a binding.
    """
    fields(locators, ("schema", "entries"), "typed_locators_fields")
    require(locators["schema"] == TYPED_LOCATORS_SCHEMA and isinstance(locators["entries"], list)
            and len(locators["entries"]) <= 16, "typed_locators_schema")
    if bindings is None:
        entries, binding_hash = [], None
    else:
        fields(bindings, ("schema", "entries"), "typed_bindings_fields")
        require(bindings["schema"] == TYPED_BINDINGS_SCHEMA and isinstance(bindings["entries"], list)
                and len(bindings["entries"]) <= 16, "typed_bindings_schema")
        entries, binding_hash = bindings["entries"], digest(canonical(bindings))
    mapped = {}
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("family"), str)
                and entry["family"] not in mapped, "typed_binding_family_duplicate")
        mapped[entry["family"]] = entry
    rows, seen = [], set()
    for locator in locators["entries"]:
        require(isinstance(locator, dict) and isinstance(locator.get("family"), str),
                "typed_locator_family")
        family = locator["family"]
        require(family not in seen, "typed_locator_family_duplicate")
        seen.add(family)
        row = {"family": family, "status": "READBACK_REQUIRED", "candidate_disposition": "READBACK_REQUIRED",
               "evidence_admission": "NOT_PERFORMED", "retry_authorized": False}
        if family not in RETAINED_READERS:
            row["code"] = "typed_family_unsupported"
        elif family not in mapped:
            row["code"] = "typed_reviewed_binding_missing"
        else:
            try:
                row = _retained_compile_readback(locator, mapped[family], plan["source"])
            except (ScopeError, OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
                row["code"] = str(error) if isinstance(error, ScopeError) else "typed_original_unavailable_or_invalid"
                if row["code"] == "typed_execution_unknown":
                    row["execution_outcome"] = "UNKNOWN"
        rows.append(row)
    # Never set observation completeness, verdicts or dispatch permissions here.
    result = {**plan, "typed_evidence": {"schema": "motionwright.release-typed-readbacks/1",
              "reviewed_binding_sha256": binding_hash, "readbacks": rows,
              "retained_readbacks_complete": bool(rows) and all(
                  row["status"] == "HISTORICAL_RETAINED_BYTES_VERIFIED" for row in rows),
              "scope_completeness_effect": "NONE", "candidate_admission": "NOT_PERFORMED"}}
    result.pop("plan_id", None)
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
    parser.add_argument("--typed-evidence-locators", type=Path,
                        help="Optional private paths for a supported retained evidence family; no execution")
    parser.add_argument("--reviewed-reader-bindings", type=Path,
                        help="Separate reviewed report/receipt hashes; external review is not authenticated here")
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
        require(args.typed_evidence_locators is not None or args.reviewed_reader_bindings is None,
                "typed_bindings_without_locators")
        if args.typed_evidence_locators is not None:
            plan = attach_typed_readbacks(
                plan, read_json_file(args.typed_evidence_locators),
                read_json_file(args.reviewed_reader_bindings) if args.reviewed_reader_bindings else None)
        plan["execution"] = {"planner_source_sha": planner_sha, "planner_tree_sha": planner_tree,
                             "catalog_source_sha": planner_sha,
                             "planner_git_sha256": digest(expected),
                             "planner_checkout_sha256": digest(actual),
                             "catalog_git_sha256": digest(planner_files[CATALOG_PATH])}
        # Include actual implementation identity in the report digest.
        del plan["plan_id"]
        plan["plan_id"] = digest(canonical(plan))
        print(json.dumps(plan, indent=2, sort_keys=True))
        typed_complete = (args.typed_evidence_locators is None
                          or plan["typed_evidence"]["retained_readbacks_complete"])
        return 0 if plan["scope_plan_complete"] and plan["scope_observations_complete"] and typed_complete else 3
    except (ScopeError, OSError, UnicodeError, ValueError) as error:
        code = str(error) if isinstance(error, ScopeError) else "source_or_input_unavailable"
        print(json.dumps({"schema": "motionwright.release-scope-refusal/1", "code": code,
                          "release_verdict": "NOT_GRANTED", "retry_authorized": False}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
