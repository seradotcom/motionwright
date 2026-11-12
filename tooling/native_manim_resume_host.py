#!/usr/bin/env python3
"""Prepare, then explicitly publish one encrypted Manim draft-release handoff.

prepare performs GETs and local exclusive writes. publish requires the exact
prepared plan digest and writes each POST intent before invoking GitHub once.
It never reads the historical private key, publishes a release, replaces an
asset, starts a render, or automatically repeats an uncertain mutation.
"""
from __future__ import annotations
import argparse
import base64
from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
import time
import zipfile

import ci_private_evidence as engine
import native_manim_resume_transfer as transfer
from native_manim_resume_transfer import selected_source_gate as check_source_gate

PUBLIC_FILES = {"recipient.json", "source-gate.json", "source-gate.stdout.log",
                "source-gate.stderr.log", "repository-preflight.original.json"}
MAX_ARTIFACT = 2 * 1024 * 1024
require, sha = engine.require, engine.sha


def save(root, name, value):
    raw = engine.json_bytes(value)
    with (root / name).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require((root / name).read_bytes() == raw, "resume_host_exclusive_json_readback")
    return sha(raw)


def bounded(path, maximum):
    if os.name != "nt":
        raw, _ = engine.regular(path, maximum)
        return raw
    # Win32 path ctime can be CreationTime while descriptor ctime names
    # ChangeTime. Reuse the source-owned held-handle and timestamp-domain
    # checks; never erase a timestamp or accept a path-only identity.
    import nsis_owned_files as owned
    require(type(maximum) is int and maximum >= 0, "resume_host_original_byte_bound")
    path = owned._validated_path(path)
    owned._require_plain_parents(path)
    namespace = path.lstat()
    owned._require_regular(namespace, "Manim retained original")
    require(namespace.st_size <= maximum, "resume_host_original_byte_bound")
    # FILE_SHARE_READ excludes concurrent writers/deleters. A conflicting
    # writer fails admission; there is no weaker sharing fallback or retry.
    fd = owned._open_publication_reader(path, "Manim retained original")
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        opened = os.fstat(stream.fileno())
        owned._require_path_descriptor_match(opened, namespace, "Manim retained original")
        change_time = owned._source_change_time(stream.fileno())
        require(opened.st_size <= maximum, "resume_host_original_byte_bound")
        raw = stream.read(opened.st_size + 1)
        require(len(raw) == opened.st_size, "resume_host_original_read_size")
        owned._require_plain_parents(path)
        current_namespace = path.lstat()
        current_opened = os.fstat(stream.fileno())
        owned._require_unchanged(current_namespace, namespace, "Manim retained original path")
        owned._require_unchanged(current_opened, opened, "Manim retained original handle")
        owned._require_path_descriptor_match(current_opened, current_namespace,
                                             "Manim retained original")
        require(owned._source_change_time(stream.fileno()) == change_time,
                "resume_host_original_change_time")
        return raw


def gh(root, label, executable, args, *, limit=512 * 1024, seconds=45):
    """Stream bounded originals; a failed POST caller must reconcile, never retry."""
    command = [str(executable), "api", *args]
    save(root, label + ".intent.original.json", {"command": command, "maximum_stdout_bytes": limit,
         "maximum_stderr_bytes": 65536, "maximum_seconds": seconds, "automatic_retry": False})
    env = {key: value for key, value in os.environ.items() if key not in {"GH_DEBUG", "GH_TRACE"}}
    env.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0")
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
    save(root, label + ".spawn.original.json", {"pid": child.pid, "automatic_retry": False})
    failed, errors = threading.Event(), []
    def copy(source, name, budget):
        try:
            count = 0
            with (root / (label + "." + name + ".original")).open("xb") as stream:
                while chunk := source.read(65536):
                    allowed = max(0, budget - count)
                    stream.write(chunk[:allowed])
                    count += len(chunk)
                    if count > budget:
                        errors.append(name + "_BUDGET")
                        failed.set()
                        break
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException as error:
            errors.append(name + ":" + type(error).__name__)
            failed.set()
        finally:
            source.close()
    threads = [threading.Thread(target=copy, args=(source, name, budget), daemon=True)
               for source, name, budget in ((child.stdout, "stdout", limit), (child.stderr, "stderr", 65536))]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    terminated = False
    while child.poll() is None:
        if failed.is_set() or time.monotonic() - started >= seconds:
            errors.append("TIMEOUT" if not failed.is_set() else "STREAM_FAILURE")
            child.kill()
            terminated = True
            break
        time.sleep(0.05)
    code = child.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
    require(not any(thread.is_alive() for thread in threads), "resume_host_original_stream_not_quiescent")
    raw = bounded(root / (label + ".stdout.original"), limit)
    error_raw = bounded(root / (label + ".stderr.original"), 65536)
    save(root, label + ".terminal.original.json", {"exit_code": code, "termination_requested": terminated,
         "errors": errors, "stdout": {"bytes": len(raw), "sha256": sha(raw)},
         "stderr": {"bytes": len(error_raw), "sha256": sha(error_raw)}, "automatic_retry": False})
    require(code == 0 and not errors and not terminated, "resume_host_api_not_known_success_no_retry:" + label)
    return raw


def get(root, label, executable, endpoint, *, limit=512 * 1024, seconds=45):
    return gh(root, label, executable, ["--method", "GET", endpoint], limit=limit, seconds=seconds)


def private_root(path):
    require(path.is_absolute() and path.is_dir() and path.resolve(strict=True) == path
            and not any(item.is_symlink() for item in (path, *path.parents)), "resume_host_canonical_private_root")
    if os.name == "nt":
        # A protected, current-user-only root is the existing A4 custody boundary.
        # Pass its path as data via an environment variable, never interpolated code.
        script = ("$ErrorActionPreference='Stop'; $a=Get-Acl -LiteralPath $env:MW_RESUME_PRIVATE_ROOT; "
                  "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                  "$rows=@($a.Access); if(-not $a.AreAccessRulesProtected -or $rows.Count -ne 1) {exit 2}; "
                  "$r=$rows[0]; $actual=$r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
                  "if($actual -ne $sid -or $r.AccessControlType.ToString() -ne 'Allow' -or "
                  "$r.FileSystemRights.ToString() -ne 'FullControl' -or "
                  "$r.InheritanceFlags.ToString() -ne 'ContainerInherit, ObjectInherit') {exit 3}; "
                  "Write-Output 'PRIVATE_ROOT_VERIFIED'")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=dict(os.environ, MW_RESUME_PRIVATE_ROOT=str(path)), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        require(result.returncode == 0 and result.stdout.strip() == b"PRIVATE_ROOT_VERIFIED",
                "resume_host_private_acl_not_verified")
    else:
        info = path.stat()
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0,
                "resume_host_private_owner_mode")


def preflight(root, executable, source, *, label="host"):
    raw = gh(root, label + "-auth-scope", executable, ["--include", "--method", "GET", "/user"], limit=128 * 1024)
    header, _, body = raw.replace(b"\r\n", b"\n").partition(b"\n\n")
    scopes = None
    for line in header.splitlines():
        if line.lower().startswith(b"x-oauth-scopes:"):
            scopes = {part.strip().decode("ascii") for part in line.split(b":", 1)[1].split(b",") if part.strip()}
    require(scopes is not None and "workflow" in scopes and bool(scopes & {"repo", "public_repo"}),
            "resume_host_workflow_write_scope_not_observed_no_POST")
    actor = engine.strict(body)
    require(type(actor.get("id")) is int and actor["id"] > 0, "resume_host_authenticated_actor_required")
    repository = engine.strict(get(root, label + "-repository", executable, "/repos/" + transfer.REPOSITORY))
    require(repository.get("full_name") == transfer.REPOSITORY
            and repository.get("permissions", {}).get("push") is True,
            "resume_host_repository_push_not_observed")
    commit = engine.strict(get(root, label + "-target-commit", executable,
                               "/repos/" + transfer.REPOSITORY + "/commits/" + source))
    require(commit.get("sha") == source, "resume_host_target_commit_changed")
    return {"workflow_scope_observed": True, "repository_push_observed": True,
            "source_sha": source, "actor_id": str(actor["id"]), "mutation_performed": False}


def checked_run(value, current):
    require(value.get("id") == int(current["run_id"]) and value.get("head_sha") == current["source_sha"]
            and value.get("head_branch") in transfer.ALLOWED_HEAD_BRANCHES
            and value.get("run_attempt") == 1 and value.get("event") == "workflow_dispatch"
            and value.get("path") == ".github/workflows/native-manim-e2e.yml"
            and value.get("status") == "in_progress" and value.get("conclusion") is None
            and value.get("repository", {}).get("full_name") == transfer.REPOSITORY,
            "resume_host_original_live_run_required")
    actor_id = str(value.get("actor", {}).get("id"))
    require(actor_id.isdecimal() and int(actor_id) > 0
            and value.get("triggering_actor", {}).get("id") == value.get("actor", {}).get("id"),
            "resume_host_original_actor_required")
    return actor_id


def prepare(args):
    os.environ["MANIM_NATIVE_SCENARIO"] = transfer.selected_scenario(args.scenario)
    from native_manim_retained_reference import ENVIRONMENT, selection
    os.environ[ENVIRONMENT] = selection(getattr(args, "recovery_continuation", "none"))
    source, run_id = args.source_sha, args.run_id
    current = {"source_sha": source, "semwright_sha": transfer.SDK, "run_id": run_id, "run_attempt": "1"}
    engine.validate_context(current)
    originals = args.originals
    private_root(originals)
    require(shutil.disk_usage(originals).free >= 128 * 1024 * 1024, "resume_host_private_disk_reserve")
    root = originals / ("resume-handoff-" + run_id + "-" + source[:12])
    root.mkdir(mode=0o700, exist_ok=False)
    save(root, "prepare-intent.original.json", {"context": current, "originals": str(originals),
         "public_artifact_download_only": True, "historical_private_key_accessed": False,
         "project_changes_applied": 0, "native_effect_invocations": 0, "automatic_retry": False})
    permission = preflight(root, args.gh, source)
    prefix = "/repos/" + transfer.REPOSITORY
    run_raw = get(root, "workflow-run", args.gh, prefix + "/actions/runs/" + run_id)
    observed_run = engine.strict(run_raw)
    actor_id = checked_run(observed_run, current)
    source_ref = "refs/heads/" + observed_run["head_branch"]
    require(permission["actor_id"] == actor_id, "resume_host_actor_differs_from_original_run")
    jobs = engine.strict(get(root, "workflow-jobs", args.gh,
                              prefix + "/actions/runs/" + run_id + "/jobs?filter=all&per_page=100"))
    require(jobs.get("total_count", 0) <= 100, "resume_host_job_roster_bound")
    selected = [row for row in jobs.get("jobs", []) if row.get("name") == transfer.JOB]
    require(len(selected) == 1 and selected[0].get("status") == "in_progress", "resume_host_live_job_required")
    steps = selected[0].get("steps", [])
    source_step = ("Require eight recovery and two reference Rust regressions"
                   if transfer.selected_scenario() == transfer.RECOVERY_SCENARIO else
                   "Require eight real committed-source recovery regressions")
    for name in ("Build the existing Manim provider and example", source_step):
        match = [step for step in steps if step.get("name") == name]
        require(len(match) == 1 and match[0].get("conclusion") == "success", "resume_host_source_gate_step_required")
    artifacts = engine.strict(get(root, "workflow-artifacts", args.gh,
                                   prefix + "/actions/runs/" + run_id + "/artifacts?per_page=100"))
    name = "manim-resume-recipient-" + source + "-" + run_id + "-1"
    require(artifacts.get("total_count", 0) <= 100, "resume_host_artifact_roster_bound")
    selected = [row for row in artifacts.get("artifacts", []) if row.get("name") == name]
    require(len(selected) == 1, "resume_host_unique_recipient_artifact")
    artifact = selected[0]
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and artifact.get("expired") is False
            and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT
            and artifact.get("workflow_run", {}).get("id") == int(run_id)
            and artifact.get("workflow_run", {}).get("head_sha") == source,
            "resume_host_artifact_source_identity")
    expected_digest = artifact.get("digest", "")
    require(expected_digest.startswith("sha256:") and engine.HEX.fullmatch(expected_digest[7:]),
            "resume_host_artifact_digest_required")
    archive = get(root, "recipient-artifact", args.gh,
                  prefix + "/actions/artifacts/" + str(artifact["id"]) + "/zip", limit=MAX_ARTIFACT)
    require(len(archive) == artifact["size_in_bytes"] and sha(archive) == expected_digest[7:],
            "resume_host_recipient_zip_original_digest")
    public = root / "public-recipient-originals"
    public.mkdir(mode=0o700)
    total = 0
    expected_public = {"recipient.json", "repository-preflight.original.json"} | set(transfer.selected_public_gate_files())
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        rows = package.infolist()
        require(len(rows) == len(expected_public) and {row.filename for row in rows} == expected_public
                and not package.comment, "resume_host_public_artifact_closed_roster")
        for row in rows:
            require(not row.is_dir() and not row.flag_bits & 1 and 0 <= row.file_size <= 4 * 1024 * 1024
                    and stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG),
                    "resume_host_public_artifact_regular_entry")
            raw = package.read(row)
            total += len(raw)
            require(total <= 4 * 1024 * 1024, "resume_host_public_artifact_expansion_budget")
            with (public / row.filename).open("xb") as stream:
                stream.write(raw)
    gate, gate_sha = check_source_gate(current, public, reader=bounded)
    if transfer.selected_scenario() == transfer.RECOVERY_SCENARIO:
        from native_manim_recovery_gate import validate_artifact
        retained = gate["runtime"]["retained_artifact"]
        origin = gate["runtime"]["frozen"]["reuse"]["origin_context"]
        # This runtime belongs to its retained origin run, not this new run's
        # artifact roster. Observe that exact ID without downloading it again.
        runtime_raw = get(root, "runtime-origin-artifact", args.gh,
                          prefix + "/actions/artifacts/" + str(retained["artifact_id"]), limit=128 * 1024)
        original = engine.strict(bounded(public / "runtime-artifact.original.json", 128 * 1024))
        fresh = {**original, "api": engine.strict(runtime_raw), "api_original_utf8": runtime_raw.decode("utf-8"),
                 "api_original_sha256": sha(runtime_raw)}
        require(validate_artifact(fresh, origin, sha(bounded(public / "runtime-receipt.json", 2 * 1024 * 1024)))
                == retained,
                "recovery_runtime_host_observed_exact_retained_artifact")
    record = engine.strict(bounded(public / "recipient.json", 16384))
    recipient = transfer.validate_recipient(record, current, actor_id, gate_sha, expected_ref=source_ref)
    transfer.check_repository_identity(bounded(public / "repository-preflight.original.json", 128 * 1024),
                                       engine.strict(run_raw)["repository"]["id"])
    require(transfer.timestamp(engine.strict(run_raw)["created_at"]) <= transfer.timestamp(record["issued_at"]),
            "resume_host_recipient_predates_run")
    files = {}
    for filename, (size, digest) in transfer.input_files().items():
        raw = bounded(originals / filename, size)
        require(len(raw) == size and sha(raw) == digest, "resume_host_original_source_changed:" + filename)
        files[filename] = raw
    plaintext = transfer.make_plaintext(files, current, recipient["sha256"])
    envelope = engine.encrypt_snapshot(plaintext, recipient, current)
    require(len(envelope) <= transfer.MAX_CIPHERTEXT, "resume_host_encrypted_input_budget")
    with (root / transfer.ASSET_NAME).open("xb") as stream:
        stream.write(envelope)
        stream.flush()
        os.fsync(stream.fileno())
    require(bounded(root / transfer.ASSET_NAME, transfer.MAX_CIPHERTEXT) == envelope,
            "resume_host_ciphertext_physical_readback")
    for filename, (size, digest) in transfer.input_files().items():
        require(sha(bounded(originals / filename, size)) == digest, "resume_host_original_changed_after_seal")
    plan = {"schema": "motionwright.manim.resume.draft-publish-plan/1", "context": current,
            "actor_id": actor_id, "source_ref": source_ref, "recipient_artifact": {"id": artifact["id"], "sha256": sha(archive)},
            "recipient_sha256": recipient["sha256"], "source_gate_sha256": gate_sha,
            "public_recipient_sha256": sha(bounded(public / "recipient.json", 16384)),
            "ciphertext": {"name": transfer.ASSET_NAME, "bytes": len(envelope), "sha256": sha(envelope)},
            "request": {"tag_name": transfer.tag(current), "target_commitish": source,
                        "name": transfer.tag(current), "body": transfer.release_body(record, current),
                        "draft": True, "prerelease": True, "generate_release_notes": False},
            "host_permissions": permission, "original_source_hashes_unchanged": True,
            "historical_private_key_accessed": False, "project_changes_applied": 0,
            "native_effect_invocations": 0, "automatic_retry": False}
    if transfer.selected_scenario() == transfer.RECOVERY_SCENARIO:
        plan.update(scenario=transfer.RECOVERY_SCENARIO, authorized_new_native_effects=4,
                    additional_reference_renders=2, equivalence_to_old_runtime=False,
                    runtime_binding=gate["runtime"])
        from native_manim_retained_reference import CONTINUATION, contract, selected
        if selected():
            plan.update(recovery_continuation=CONTINUATION, retained_reference=contract(),
                        authorized_new_native_effects=3, additional_reference_renders=1)
    plan_sha = save(root, "publish-plan.json", plan)
    print(json.dumps({"status": "ENCRYPTED_INPUT_PREPARED_NO_POST", "handoff": str(root),
                      "plan_sha256": plan_sha, "ciphertext": plan["ciphertext"], "context": current}))


def publish(args):
    root = args.handoff
    require(root.is_absolute() and root.resolve(strict=True) == root and not root.is_symlink(),
            "resume_host_canonical_handoff")
    private_root(root.parent)
    raw = bounded(root / "publish-plan.json", 65536)
    require(sha(raw) == args.plan_sha256, "resume_host_reviewed_plan_digest_required")
    plan = engine.strict(raw)
    os.environ["MANIM_NATIVE_SCENARIO"] = transfer.selected_scenario(plan.get("scenario", transfer.RESUME_SCENARIO))
    from native_manim_retained_reference import ENVIRONMENT, contract, selected, selection
    os.environ[ENVIRONMENT] = selection(plan.get("recovery_continuation", "none"))
    if transfer.selected_scenario() == transfer.RECOVERY_SCENARIO:
        require(plan.get("authorized_new_native_effects") == (3 if selected() else 4)
                and plan.get("additional_reference_renders") == (1 if selected() else 2)
                and plan.get("equivalence_to_old_runtime") is False, "recovery_runtime_reviewed_native_scope")
        require(plan.get("retained_reference") == (contract() if selected() else None),
                "recovery_runtime_reviewed_retained_reference")
    current = plan["context"]
    engine.validate_context(current)
    public = root / "public-recipient-originals"
    gate, gate_sha = check_source_gate(current, public, reader=bounded)
    if transfer.selected_scenario() == transfer.RECOVERY_SCENARIO:
        require(plan.get("runtime_binding") == gate["runtime"], "recovery_runtime_reviewed_binding_changed")
    record = engine.strict(bounded(public / "recipient.json", 16384))
    transfer.validate_recipient(record, current, plan["actor_id"], gate_sha,
                                expected_ref=plan["source_ref"])
    require(sha(bounded(public / "recipient.json", 16384)) == plan["public_recipient_sha256"],
            "resume_host_recipient_changed_after_review")
    envelope = bounded(root / transfer.ASSET_NAME, transfer.MAX_CIPHERTEXT)
    require(plan.get("ciphertext") == {"name": transfer.ASSET_NAME, "bytes": len(envelope), "sha256": sha(envelope)}
            and plan.get("request") == {"tag_name": transfer.tag(current), "target_commitish": current["source_sha"],
                "name": transfer.tag(current), "body": transfer.release_body(record, current), "draft": True,
                "prerelease": True, "generate_release_notes": False}, "resume_host_prepared_request_changed")
    prefix = "/repos/" + transfer.REPOSITORY
    permission = preflight(root, args.gh, current["source_sha"], label="publish-host")
    require(permission["actor_id"] == plan["actor_id"], "resume_host_actor_changed_before_POST")
    live_run = engine.strict(get(root, "publish-live-run", args.gh,
                                prefix + "/actions/runs/" + current["run_id"]))
    checked_run(live_run, current)
    require("refs/heads/" + live_run["head_branch"] == plan["source_ref"],
            "resume_host_live_run_branch_changed_before_POST")
    existing = get(root, "publish-prior-releases", args.gh,
                   prefix + "/releases?per_page=100&page=1", limit=2 * 1024 * 1024)
    require(transfer.find_draft(existing, record, current) is None, "resume_host_existing_draft_reconcile_no_POST")
    request_path = root / "draft-request.original.json"
    save(root, request_path.name, plan["request"])
    # gh writes the durable intent before each distinct POST. It never retries.
    response = gh(root, "create-draft-POST", args.gh,
                  ["--method", "POST", prefix + "/releases", "--input", str(request_path)])
    release = engine.strict(response)
    transfer.validate_release_header(release, record, current)
    require(release.get("assets") == [], "resume_host_new_draft_not_empty")
    release_id = release["id"]
    readback = engine.strict(get(root, "draft-id-readback", args.gh, prefix + "/releases/" + str(release_id)))
    transfer.validate_release_header(readback, record, current)
    require(readback["id"] == release_id and readback.get("assets") == [], "resume_host_draft_id_readback_changed")
    transfer.validate_recipient(record, current, plan["actor_id"], gate_sha,
                                expected_ref=plan["source_ref"])
    upload_url = "https://uploads.github.com" + prefix + "/releases/" + str(release_id) + "/assets?name=" + transfer.ASSET_NAME
    uploaded = gh(root, "upload-input-POST", args.gh,
                  ["--method", "POST", upload_url, "-H", "Content-Type: application/octet-stream",
                   "--input", str(root / transfer.ASSET_NAME)], seconds=60)
    asset = engine.strict(uploaded)
    identity = transfer.validate_release({**release, "assets": [asset]}, record, current)
    require(identity["sha256"] == sha(envelope) and identity["bytes"] == len(envelope),
            "resume_host_uploaded_ciphertext_api_digest")
    observed = engine.strict(get(root, "asset-id-readback", args.gh,
                                prefix + "/releases/assets/" + str(identity["asset_id"])))
    require(transfer.validate_release({**release, "assets": [observed]}, record, current) == identity,
            "resume_host_original_asset_id_digest_readback")
    release_after = engine.strict(get(root, "draft-final-readback", args.gh,
                                     prefix + "/releases/" + str(release_id)))
    require(transfer.validate_release(release_after, record, current) == identity,
            "resume_host_final_draft_changed")
    result = {"schema": "motionwright.manim.resume.draft-handoff/1", "status": "ENCRYPTED_DRAFT_ASSET_RETAINED",
              "context": current, "plan_sha256": sha(raw), "asset": identity,
              "api_asset_id_digest_readback": True, "ciphertext_download_readback_performed": False,
              "release_published": False, "historical_private_key_accessed": False,
              "native_effect_invocations": 0, "automatic_retry": False}
    save(root, "handoff-result.original.json", result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gh", type=Path, default=Path(r"C:\Program Files\GitHub CLI\gh.exe"))
    sub = parser.add_subparsers(dest="command", required=True)
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--source-sha", required=True)
    preflight_parser.add_argument("--originals", type=Path, required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--source-sha", required=True)
    prepare_parser.add_argument("--run-id", required=True)
    prepare_parser.add_argument("--originals", type=Path, required=True)
    prepare_parser.add_argument("--scenario", choices=(transfer.RESUME_SCENARIO, transfer.RECOVERY_SCENARIO),
                                default=transfer.RESUME_SCENARIO)
    from native_manim_retained_reference import CONTINUATION
    prepare_parser.add_argument("--recovery-continuation", choices=("none", CONTINUATION), default="none")
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--handoff", type=Path, required=True)
    publish_parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    require(args.gh.is_file(), "resume_host_github_cli_required")
    if args.command == "preflight":
        require(engine.SOURCE.fullmatch(args.source_sha), "resume_host_source_commit_required")
        private_root(args.originals)
        root = args.originals / ("resume-host-preflight-" + args.source_sha[:12])
        root.mkdir(mode=0o700, exist_ok=False)
        result = preflight(root, args.gh, args.source_sha)
        save(root, "preflight-result.json", result)
        print(json.dumps({"status": "HOST_READ_ONLY_PREFLIGHT_PASSED", **result}))
        return
    (prepare if args.command == "prepare" else publish)(args)


if __name__ == "__main__":
    main()
