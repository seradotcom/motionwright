#!/usr/bin/env python3
"""One original HyperFrames Actions scenario using the existing seven-stage runner.

A direct Provider/Driver Host job uses a separate empty owner project. Its three
actual input files must equal the prepared export. The later Broker job has its
own identity. Native work is never retried; the shared /2 collector preserves
the selected originals even when the scenario stops before its final stage.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import ci_private_evidence as custody
import native_evidence_projection as selection
import native_hyperframes_e2e as stages
import native_hyperframes_projection as projection
from native_hyperframes_evidence import require, sha

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tooling" / "hyperframes"))
import owner_provision  # noqa: E402

SCENARIO = "hyperframes-first-party-direct-mediated-edit"
DIRECT_SCHEMA = "motionwright.hyperframes.direct-host-reference/1"
FILES = ("index.html", "semwright-hyperframes.json", "source-inventory.json")


def verify_runtime_intake(args, current, bindings, binaries):
    value = stages.document(args.intake / "runtime-intake.json")
    policies, _ = direct_contract()
    selection.project(value, policies["hf-runtime-intake"])
    require(value["schema"] == "motionwright.hyperframes.runtime-intake/1"
            and value["context"] == current and value["status"] == "RUNTIME_FINALIZED"
            and value["runtime_lock_sha256"] == args.runtime_lock_sha256
            and value["source_bindings_sha256"] == sha(custody.json_bytes(bindings))
            and value["node"]["sha256"] == binaries["node"]["sha256"]
            and value["node"]["bytes"] == binaries["node"]["bytes"],
            "runtime_intake_original_binding")
    for row in value["system_dependency_observation"]["files"]:
        path = Path(row["path"])
        require(path.is_absolute() and path == path.resolve(strict=True)
                and path.is_relative_to(Path("/usr/lib")) and path.name == row["name"]
                and re.fullmatch(r"[A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)?", path.name),
                "observed_system_library_scope")
        raw = custody.regular(path, 256 * 1024 * 1024)[0]
        require(len(raw) == row["bytes"] and sha(raw) == row["sha256"],
                "observed_system_library_changed")
    return value


def bound_file(path, maximum=512 * 1024 * 1024):
    path = Path(path)
    raw = custody.regular(path, maximum)[0]
    require(len(raw) > 0, "empty_bound_binary")
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}


def require_directory(path, *, empty=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path
            and path.is_dir() and path.stat().st_mode & 0o077 == 0,
            "private_canonical_owner_root")
    if empty:
        require(not any(path.iterdir()), "owner_root_already_used")
    return path


def context():
    source = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    require(sys.platform == "linux" and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[0-9a-f]{40}", source)
            and re.fullmatch(r"[1-9][0-9]{0,19}", run_id),
            "original_linux_actions_attempt_required")
    lock = stages.document(ROOT / "SOURCE_LOCK.json")
    require(lock["dependencies"]["semwright"]["revision"] == stages.SEMWRIGHT,
            "reviewed_sdk_source_pin")
    return {"source_sha": source, "semwright_sha": stages.SEMWRIGHT,
            "run_id": run_id, "run_attempt": "1", "scenario": SCENARIO}


def source_bindings(current):
    values = [{"path": path,
               "sha256": sha(custody.regular(ROOT / path, 16 * 1024 * 1024)[0])}
              for path in stages.SOURCE_PATHS]
    selection.verify_source_bindings(ROOT, current["source_sha"], values)
    return {"schema": "motionwright.native.source-bindings/1",
            "source_sha": current["source_sha"], "source_bindings": values}


def owner_material(runtime, project, output, binaries):
    # This fixture grants only these three private roots and the hashed Node
    # file, using the existing provisioner and its closed SDK54 authority.
    return owner_provision.make(SimpleNamespace(
        runtime=str(runtime), project=str(project), output=str(output),
        driver=binaries["driver"]["path"], node=binaries["node"]["path"],
        approved_license_terms=True, approved_sandbox_controls=True,
        approved_runtime_executables=True))


def direct_config(args, phase, binaries):
    phase_root = args.direct_host / phase
    phase_root.mkdir(mode=0o700)
    for name in ("project", "state", "evidence", "config"):
        (phase_root / name).mkdir(mode=0o700)
    request = args.records / (phase + "-direct-request.json")
    material = owner_material(args.dependencies, phase_root / "project",
                              args.direct_output, binaries)
    manifest_path = phase_root / "config" / "manifest.json"
    custody.new_json(manifest_path, material["manifest"])
    value = {
        "schema": "motionwright.hyperframes.direct-owner/1", "phase": phase,
        "source_sha": args.source_sha, "semwright_sha": args.sem_sha,
        "manifest": bound_file(manifest_path), "driver": binaries["driver"],
        "node": binaries["node"], "sandbox_helper": binaries["sandbox_helper"],
        "request": bound_file(request),
        "runtime": str(args.dependencies), "exported_source": str(args.direct_project),
        "project": str(phase_root / "project"), "output": str(args.direct_output),
        "state": str(phase_root / "state"), "evidence": str(phase_root / "evidence"),
    }
    config = phase_root / "config" / "direct-owner.json"
    custody.new_json(config, value)
    return config, sha(custody.regular(config)[0])


def execute_stages(args, binaries):
    for phase in ("original", "edited"):
        stages.run(SimpleNamespace(**vars(args), stage="prepare-" + phase))
        config, digest = direct_config(args, phase, binaries)
        # Attempt reserves an exclusive intent before exactly one process call.
        # Failure/timeout propagates; no later phase or mutation is attempted.
        result = stages.Attempt(args).command(
            phase + ".direct-host",
            [binaries["direct"]["path"], str(config), digest], timeout=1020)
        require(result == {"status": "DIRECT_HOST_REFERENCE_VERIFIED", "automatic_retry": False},
                "direct_reference_not_verified")
        stages.run(SimpleNamespace(**vars(args), stage="render-" + phase))
        stages.run(SimpleNamespace(**vars(args), stage="compare-" + phase))
    stages.run(SimpleNamespace(**vars(args), stage="selection"))


def direct_contract():
    F, H = projection.F, projection.H
    fields = {
        "schema": F(selection.Literal(DIRECT_SCHEMA)),
        "context": F(projection.CONTEXT), "phase": F(projection.PHASE),
        "source": F(projection.SOURCE), "result": F(projection.JOB),
    }
    for key in ("request_sha256", "runtime_lock_sha256", "manifest_sha256",
                "driver_sha256", "node_sha256", "sandbox_helper_sha256", "session_sha256"):
        fields[key] = F(H)
    for key in ("separate_empty_project", "prepared_export_equal_before",
                "prepared_export_equal_after", "direct_provider_without_broker",
                "original_job_terminal_observed", "host_shutdown_observed"):
        fields[key] = F(selection.Literal(True))
    for key in ("automatic_retry", "formal_acceptance"):
        fields[key] = F(selection.Literal(False))
    policies = {**projection.POLICIES,
                "hf-direct-host-reference": selection.Object(fields),
                "hf-direct-host-terminal": projection.JOB}
    policies["hf-direct-host-intent"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.direct-host-intent/1")),
        "config_sha256": F(H), "request_sha256": F(H), "source_fingerprint": F(H),
        "automatic_retry": F(selection.Literal(False))})
    policies["hf-direct-call-intent"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.direct-call-intent/1")),
        "command": F(selection.String(values=("driver.hyperframes.project.apply",
                                             "driver.hyperframes.render.start"))),
        "request_id": F(selection.String(128)),
        "descriptor_sha256": F(H), "input_sha256": F(H),
        "automatic_retry": F(selection.Literal(False))})
    policies["hf-direct-call-failure"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.direct-call-failure/1")),
        "code": F(selection.String(64)), "outcome_known": F(bool),
        "automatic_retry": F(selection.Literal(False)),
        "originals_retained": F(selection.Literal(True))})
    policies["hf-direct-host-failure"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.direct-host-failure/1")),
        "state": F(selection.Literal("NOT_VERIFIED")),
        "original_outcome_may_be_unknown": F(selection.Literal(True)),
        "automatic_retry": F(selection.Literal(False)),
        "originals_retained": F(selection.Literal(True))})
    policies["hf-actions-intent"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.actions-intent/1")),
        "context": F(projection.CONTEXT), "runtime_lock_sha256": F(H),
        "automatic_retry": F(selection.Literal(False))})
    policies["hf-actions-failure"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.actions-failure/1")),
        "context": F(projection.CONTEXT), "error_type": F(selection.String(128)),
        "original_outcome_may_be_unknown": F(selection.Literal(True)),
        "automatic_retry": F(selection.Literal(False)),
        "originals_retained": F(selection.Literal(True))})
    binary = selection.Object({"sha256": F(H), "bytes": F(selection.Number(1, 536870912, True))})
    policies["hf-actions-inputs"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.actions-native-inputs/1")),
        "context": F(projection.CONTEXT), "recipient_sha256": F(H),
        "binaries": F(selection.Object({key: F(binary) for key in
            ("mw", "direct", "driver", "node", "sem_cli", "daemon", "sandbox_helper")})),
        "automatic_retry": F(selection.Literal(False))})
    versioned_binary = selection.Object({
        "sha256": F(H), "bytes": F(selection.Number(1, 536870912, True)),
        "version": F(selection.String(512))})
    tool = selection.Object({
        "sha256": F(H), "bytes": F(selection.Number(1, 536870912, True)),
        "version": F(selection.String(512)),
        "relative_path": F(selection.String(1024, r"[A-Za-z0-9_.@+/-]+"))})
    system_file = selection.Object({
        "name": F(selection.String(128, r"[A-Za-z0-9_+.-]+")),
        "sha256": F(H), "bytes": F(selection.Number(1, 268435456, True))})
    policies["hf-runtime-intake"] = selection.Object({
        "schema": F(selection.Literal("motionwright.hyperframes.runtime-intake/1")),
        "context": F(projection.CONTEXT), "status": F(selection.Literal("RUNTIME_FINALIZED")),
        "source_bindings_sha256": F(H), "runtime_lock_sha256": F(H), "npm_lock_sha256": F(H),
        "hyperframes_version": F(selection.Literal("0.8.181")),
        "hyperframes_commit": F(selection.Literal("ae02e5aceff4742e5d72c20f0859eb3e6b449035")),
        "public_distribution_integrity": F(selection.Literal(
            "sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw==")),
        "node": F(versioned_binary),
        "tools": F(selection.Object({key: F(tool) for key in ("browser", "ffmpeg", "ffprobe")})),
        "chrome_archive": F(selection.Object({
            "url": F(selection.String(256, r"https://storage\.googleapis\.com/chrome-for-testing-public/[0-9.]+/linux64/chrome-linux64\.zip")),
            "version": F(selection.String(40, r"[0-9]+(?:\.[0-9]+){3}")),
            "sha256": F(H), "bytes": F(selection.Number(1, 536870912, True))})),
        "system_dependency_observation": F(selection.Object({
            "ldd_only": F(selection.Literal(True)),
            "immutable_system_closure_attested": F(selection.Literal(False)),
            "files": F(selection.Array(system_file, 256, 1))})),
        "native_render_executed": F(selection.Literal(False)),
        "automatic_retry": F(selection.Literal(False))})
    roles = dict(projection.ROLE_CONTRACT)
    roles[("single", "origin", "runtime-intake")] = (1, 1)
    roles[("single", "attempt-custody", "actions-intent")] = (1, 1)
    roles[("single", "attempt-custody", "actions-inputs")] = (1, 1)
    roles[("single", "attempt-custody", "actions-failure")] = (0, 1)
    for phase in ("original", "edited"):
        for layer, role in (
            ("persistence", "direct-host-native-html"),
            ("persistence", "direct-host-native-composition"),
            ("persistence", "direct-host-native-inventory"),
            ("attempt-custody", "direct-host-job-intent"),
            ("attempt-custody", "direct-host-job-binding"),
            ("readback", "direct-host-terminal"),
            ("readback", "direct-host-reference"),
            ("attempt-custody", "direct-host-intent"),
            ("attempt-custody", "direct-host-apply-intent"),
            ("attempt-custody", "direct-host-start-intent"),
        ):
            roles[(phase, layer, role)] = (1, 1)
        for role in ("direct-host-failure", "direct-host-apply-failure", "direct-host-start-failure"):
            roles[(phase, "attempt-custody", role)] = (0, 1)
    return policies, roles


def extended_spec(args, current, bindings, *, incomplete):
    # The existing selector retains its strict success path. Failure mode uses
    # the same fixed role roster and records missing required members explicitly.
    spec = projection.build_spec(args, current, bindings, incomplete=incomplete)
    spec["roots"]["direct-host"] = str(args.direct_host)
    spec["roots"]["runtime-intake"] = str(args.intake)
    policies, roles = direct_contract()

    def select(path, phase, layer, role, kind, policy=None, *, root="direct-host", optional=False):
        parts = selection.member_path(path)
        require(not selection.excluded(parts), "direct_selection_before_open")
        raw = None
        try:
            raw = custody.regular(Path(spec["roots"][root]) / path)[0]
        except FileNotFoundError:
            if optional:
                spec["absence_guards"].append({"root": root, "relative_path": path,
                    "phase": phase, "layer": layer, "role": role})
                return
        except (OSError, ValueError):
            if not incomplete:
                raise
        spec["members"].append({
            "root": root, "relative_path": path, "phase": phase,
            "layer": layer, "role": role, "kind": kind, "required": not optional,
            "expected_sha256": None if raw is None else sha(raw),
            "bytes": None if raw is None else len(raw), "policy": policy,
        })

    select("runtime-intake.json", "single", "origin", "runtime-intake",
           "json-projection", "hf-runtime-intake", root="runtime-intake")
    for path, role, policy, optional in (
        ("actions-intent.json", "actions-intent", "hf-actions-intent", False),
        ("actions-native-inputs.json", "actions-inputs", "hf-actions-inputs", False),
        ("actions-failure.json", "actions-failure", "hf-actions-failure", True),
    ):
        select(path, "single", "attempt-custody", role, "json-projection", policy,
               root="records", optional=optional)
    for phase in ("original", "edited"):
        for file, role, policy, optional in (
            ("direct-host-intent.json", "direct-host-intent", "hf-direct-host-intent", False),
            ("003-apply.intent.json", "direct-host-apply-intent", "hf-direct-call-intent", False),
            ("004-start.intent.json", "direct-host-start-intent", "hf-direct-call-intent", False),
            ("003-apply.failure.json", "direct-host-apply-failure", "hf-direct-call-failure", True),
            ("004-start.failure.json", "direct-host-start-failure", "hf-direct-call-failure", True),
            ("direct-host-failure.json", "direct-host-failure", "hf-direct-host-failure", True),
        ):
            select(phase + "/evidence/" + file, phase, "attempt-custody",
                   role, "json-projection", policy, optional=optional)
        try:
            source = stages.document(args.records / (phase + "-source.json"))["source"]
            fingerprint = source["fingerprint"]
            require(isinstance(fingerprint, str)
                    and re.fullmatch(r"[0-9a-f]{64}", fingerprint),
                    "direct_selected_source_fingerprint")
            attempt = stages.direct_attempt(phase, fingerprint)
        except (OSError, ValueError, KeyError, TypeError):
            require(incomplete, "direct_source_not_prepared")
            fingerprint, attempt = "unprepared-" + phase, "unprepared-" + phase
        bundle = phase + "/project/source-" + fingerprint + "/"
        for file, role, kind, policy in (
            ("index.html", "direct-host-native-html", "original", None),
            ("semwright-hyperframes.json", "direct-host-native-composition",
             "json-original", "hf-native-source"),
            ("source-inventory.json", "direct-host-native-inventory",
             "json-original", "hf-source-inventory"),
        ):
            select(bundle + file, phase, "persistence", role, kind, policy)
        for suffix, role, policy in (
            ("intent", "direct-host-job-intent", "hf-job-intent"),
            ("binding", "direct-host-job-binding", "hf-job-binding"),
        ):
            select(phase + "/project/native-job-" + attempt + "." + suffix + ".json",
                   phase, "attempt-custody", role, "json-projection", policy)
        select(phase + "/evidence/terminal.json", phase, "readback",
               "direct-host-terminal", "json-original", "hf-direct-host-terminal")
        select(phase + "/evidence/direct-host-reference.json", phase, "readback",
               "direct-host-reference", "json-original", "hf-direct-host-reference")
    return selection._checked_spec(spec, policies, roles), policies, roles


def verify_direct_binding(args, current, manifest):
    """Independent selected-source checks supplement the generic /2 reader."""
    policies, roles = direct_contract()
    selection.validate_selected_manifest(manifest, expected_context=current, role_contract=roles)
    inputs = stages.document(args.records / "actions-native-inputs.json")
    require(inputs["context"] == current, "original_binary_context_changed")
    original_manifest = sha(custody.regular(args.private_config / "manifest.json")[0])
    for phase in ("original", "edited"):
        reference = stages.document(args.direct_host / phase / "evidence" / "direct-host-reference.json")
        source = stages.document(args.records / (phase + "-source.json"))["source"]
        terminal = stages.document(args.direct_host / phase / "evidence" / "terminal.json")
        request = args.records / (phase + "-direct-request.json")
        selection.project(reference, policies["hf-direct-host-reference"], exact=True)
        require(reference["context"] == current and reference["phase"] == phase
                and reference["source"] == source
                and reference["result"] == terminal
                and reference["request_sha256"] == sha(custody.regular(request)[0])
                and reference["runtime_lock_sha256"] == args.runtime_lock_sha256
                and terminal["directory"] == "render-" + stages.direct_attempt(phase, source["fingerprint"]),
                "direct_host_original_context_or_terminal")
        require(reference["manifest_sha256"] == original_manifest, "direct_manifest_differs_from_mediated")
        for key in ("driver", "node", "sandbox_helper"):
            require(reference[key + "_sha256"] == inputs["binaries"][key]["sha256"],
                    "direct_native_binary_differs_from_mediated")
        for file in FILES:
            rel = "source-" + source["fingerprint"] + "/" + file
            require(custody.regular(args.direct_project / rel)[0]
                    == custody.regular(args.direct_host / phase / "project" / rel)[0],
                    "direct_host_selected_input_changed")
        mediated = stages.document(args.records / (phase + "-render.json"))["hyperframes"]
        require(reference["result"]["job_ref"] != mediated["job_ref"]
                and reference["result"]["directory"] != mediated["result"]["directory"],
                "direct_and_mediated_jobs_must_be_distinct")


def capture_and_seal(args, current, bindings, recipient, sealed, *, completed):
    spec, policies, roles = extended_spec(args, current, bindings, incomplete=not completed)
    custody.new_json(args.records / "actions-selection-spec.json", spec)
    archive, manifest = selection.capture_selected_originals(
        spec, policies=policies, role_contract=roles)
    custody.new_json(args.records / "actions-selection-manifest.json", manifest)
    # Seal the first capture before interpreting completeness. There is no
    # second collector attempt, alternate root traversal or plaintext upload.
    envelope = custody.encrypt_snapshot(archive, recipient,
        {key: current[key] for key in custody.CONTEXT_KEYS})
    custody.new_bytes(sealed, envelope)
    verified = False
    if completed:
        verify_direct_binding(args, current, manifest)
        verified = True
    return {"selected_originals_complete": verified,
            "selected_manifest_sha256": sha(custody.json_bytes(manifest)),
            "sealed_originals_sha256": sha(envelope), "sealed_originals_bytes": len(envelope)}


def run(options):
    current = context()
    recipient = {"spki_der_base64": os.environ.get("PRIVATE_EVIDENCE_RECIPIENT_SPKI", ""),
                 "sha256": os.environ.get("PRIVATE_EVIDENCE_RECIPIENT_SHA256", "")}
    custody.public_key(recipient)
    bindings_doc = source_bindings(current)
    runtime = require_directory(options.runtime)
    intake = require_directory(options.intake)
    require(not intake.is_relative_to(runtime) and not runtime.is_relative_to(intake)
            and not intake.is_relative_to(options.owner)
            and not options.owner.is_relative_to(intake), "isolated_runtime_intake")
    parent = require_directory(options.owner.parent)
    require(options.owner.is_absolute() and parent / options.owner.name == options.owner
            and not options.owner.exists(), "fresh_owner_required")
    require(not options.owner.is_relative_to(ROOT) and not ROOT.is_relative_to(options.owner)
            and not options.owner.is_relative_to(runtime)
            and not runtime.is_relative_to(options.owner), "isolated_owner_and_runtime")
    require(options.sealed.parent.is_absolute()
            and options.sealed.parent.resolve(strict=True) == options.sealed.parent
            and not options.sealed.exists()
            and not options.sealed.is_relative_to(options.owner), "fresh_sealed_destination")
    require(options.public_result.parent.is_absolute()
            and options.public_result.parent.resolve(strict=True) == options.public_result.parent
            and not options.public_result.exists()
            and not options.public_result.is_relative_to(options.owner),
            "fresh_public_result_destination")
    binaries = {name: bound_file(getattr(options, name)) for name in
                ("mw", "direct", "driver", "node", "sem_cli", "daemon", "sandbox_helper")}
    require(all(os.access(value["path"], os.X_OK) for value in binaries.values()),
            "original_executables_required")
    options.owner.mkdir(mode=0o700)
    roots = {}
    for name in ("records", "database", "direct-source", "mediated-source",
                 "direct-output", "mediated-output", "direct-host", "config", "broker-state",
                 "broker-runtime", "broker-log"):
        roots[name] = options.owner / name
        roots[name].mkdir(mode=0o700)
    bindings_file = roots["records"] / "source-bindings.json"
    custody.new_json(bindings_file, bindings_doc)
    custody.new_json(roots["records"] / "actions-native-inputs.json", {
        "schema": "motionwright.hyperframes.actions-native-inputs/1", "context": current,
        "binaries": binaries, "recipient_sha256": recipient["sha256"],
        "automatic_retry": False})
    runtime_lock_sha = sha(custody.regular(runtime / "runtime-lock.json")[0])
    args = SimpleNamespace(
        source_root=ROOT, source_sha=current["source_sha"], sem_sha=current["semwright_sha"],
        source_bindings=bindings_file, source_bindings_sha256=sha(custody.regular(bindings_file)[0]),
        mw_binary=Path(binaries["mw"]["path"]), mw_binary_sha256=binaries["mw"]["sha256"],
        sem_cli=Path(binaries["sem_cli"]["path"]), sem_cli_sha256=binaries["sem_cli"]["sha256"],
        socket=roots["broker-runtime"] / "broker.sock",
        session_file=roots["config"] / "client.session", private_config=roots["config"],
        records=roots["records"], data_root=roots["database"],
        direct_project=roots["direct-source"], mediated_project=roots["mediated-source"],
        direct_output=roots["direct-output"], mediated_output=roots["mediated-output"],
        direct_host=roots["direct-host"], dependencies=runtime, intake=intake,
        runtime_lock_sha256=runtime_lock_sha)
    verify_runtime_intake(args, current, bindings_doc, binaries)
    material = owner_material(runtime, args.mediated_project, args.mediated_output, binaries)
    manifest_path = roots["config"] / "manifest.json"
    custody.new_json(manifest_path, material["manifest"])
    owner_path = roots["config"] / "owner.toml"
    custody.new_bytes(owner_path, material["owner"].replace("__OWNER_SET_MANIFEST_PATH__",
        json.dumps(str(manifest_path))[1:-1]).encode())
    env = {key: value for key, value in os.environ.items()
           if key not in {"DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"}}
    env.update(XDG_RUNTIME_DIR=str(roots["broker-runtime"]),
               XDG_STATE_HOME=str(roots["broker-state"]),
               SEMWRIGHT_TEST_DRIVER_SANDBOX="1",
               SEMWRIGHT_TEST_SANDBOX_HELPER=binaries["sandbox_helper"]["path"])
    custody.new_json(roots["records"] / "actions-intent.json", {
        "schema": "motionwright.hyperframes.actions-intent/1", "context": current,
        "runtime_lock_sha256": runtime_lock_sha, "automatic_retry": False})
    completed, failure_type, capture = False, None, None
    daemon = None
    log = (roots["broker-log"] / "semwrightd.log").open("xb")
    try:
        daemon = subprocess.Popen(
            [binaries["daemon"]["path"], "--config", str(owner_path), "--socket", str(args.socket)],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        custody.new_json(roots["records"] / "broker-started.json", {
            "schema": "motionwright.hyperframes.broker-started/1",
            "pid": daemon.pid, "automatic_retry": False})
        deadline = time.monotonic() + 30
        while not args.socket.is_socket():
            require(daemon.poll() is None and time.monotonic() < deadline,
                    "original_broker_unavailable")
            time.sleep(0.05)
        execute_stages(args, binaries)
        verify_runtime_intake(args, current, bindings_doc, binaries)
        completed = True
    except Exception as error:
        failure_type = type(error).__name__
        custody.new_json(roots["records"] / "actions-failure.json", {
            "schema": "motionwright.hyperframes.actions-failure/1", "context": current,
            "error_type": failure_type, "original_outcome_may_be_unknown": True,
            "automatic_retry": False, "originals_retained": True})
    finally:
        log.flush()
        os.fsync(log.fileno())
        try:
            capture = capture_and_seal(args, current, bindings_doc["source_bindings"],
                                       recipient, options.sealed, completed=completed)
        except Exception as error:
            completed = False
            failure_type = failure_type or type(error).__name__
            custody.new_json(roots["records"] / "actions-custody-failure.json", {
                "schema": "motionwright.hyperframes.actions-custody-failure/1",
                "error_type": type(error).__name__, "automatic_retry": False,
                "originals_retained": True})
        if completed and daemon is not None and daemon.poll() is None:
            # Only the disposable owner Broker is stopped here after all original
            # native jobs were observed terminal. This is not a product close receipt.
            try:
                os.killpg(daemon.pid, signal.SIGTERM)
                daemon.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                completed = False
                failure_type = "OwnerBrokerShutdownNotObserved"
        log.close()
    result = {"schema": "motionwright.hyperframes.actions/1",
              "status": "SCENARIO_VERIFIED" if completed else "NOT_VERIFIED",
              "context": current, "custody": capture, "failure_type": failure_type,
              "automatic_retry": False, "formal_acceptance": False,
              "human_acceptance": False, "release_authorized": False}
    custody.new_json(roots["records"] / "actions-result.json", result)
    custody.new_json(options.public_result, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("owner", "runtime", "intake", "sealed", "public-result", "mw", "direct", "driver",
                 "node", "sem-cli", "daemon", "sandbox-helper"):
        parser.add_argument("--" + name, type=Path, required=True)
    options = parser.parse_args()
    try:
        result = run(options)
    except Exception:
        print("hyperframes-actions=NOT_VERIFIED; automatic_retry=false")
        return 1
    print("hyperframes-actions=" + result["status"] + "; automatic_retry=false")
    return 0 if result["status"] == "SCENARIO_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
