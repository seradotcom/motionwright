#!/usr/bin/env python3
"""Bind the existing camera producer's originals to decoded media and sealed custody.

This module never dispatches a provider. The native wrapper invokes the existing
example once; the canonical coordinator owns grants, jobs and durable receipts.
A decoded-video result is not editable-source reopen or human acceptance.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from uuid import UUID

from mlt_official_runtime_intake import capture_regular
from native_manim_camera_pixels import verify as verify_pixels, verified_plan

SHA = re.compile(r"[0-9a-f]{64}\Z")
JOB = re.compile(r"manim-([0-9a-f]{32})\Z")
MAX_JSON = 4 * 1024 * 1024
MAX_MEDIA = 128 * 1024 * 1024
BINDING_KEYS = ("project_id", "generation", "revision", "scene_id", "deliverable_id")
MEDIA_NAME = "motionwright-manim-community.mp4"
EVIDENCE_NAME = "motionwright-manim-camera-evidence.json"


class CameraEvidenceError(ValueError):
    """A closed verification failed; no render retry is authorized."""


def require(ok, code):
    if not ok:
        raise CameraEvidenceError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def custody_module():
    return importlib.import_module("ci_private_evidence")


def private_bytes(path, data):
    # Reuse the existing exclusive writer. A failed write may remain partial;
    # it is never overwritten, called an atomic publication, or accepted.
    custody_module().new_bytes(path, data)


def private_json(path, value):
    custody_module().new_json(path, value)


def read(path, limit=MAX_JSON):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path
            and not any(p.is_symlink() for p in path.parents), "camera_noncanonical_file")
    return capture_regular(path, limit)[0]


def object_bytes(data):
    require(0 < len(data) <= MAX_JSON, "camera_json_size")
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "camera_duplicate_json_key")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(
                           CameraEvidenceError("camera_nonfinite_json")))
    require(type(value) is dict, "camera_json_object")
    return value


def read_object(path):
    return object_bytes(read(path))


def identity(value):
    require(type(value) is str and str(UUID(value)) == value, "camera_uuid_identity")
    return value


def seed_binding(seed, *, fixture="original"):
    require(fixture in ("original", "edited1040"), "camera_fixture_profile")
    require(type(seed) is dict, "camera_seed_contract")
    if fixture == "original":
        require(seed.get("schema") == "motionwright.native.manim-camera-seed/1"
                and seed.get("fresh_sqlite_readback") is True
                and seed.get("native_dispatch_performed") is False
                and seed.get("decoded_media_verified") is False
                and seed.get("frame_count") == 120
                and seed.get("frame_rate_num") == 30
                and seed.get("frame_rate_den") == 1, "camera_seed_contract")
        result = {key: seed.get(key) for key in BINDING_KEYS}
    else:
        require(seed.get("schema") == "motionwright.manim.camera.source-cycle/1"
                and seed.get("phase") == "source-cycle-edit"
                and seed.get("fresh_sqlite_readback") is True
                and seed.get("source_changed_by_example") is True
                and seed.get("import_staging_files_removed") is True
                and all(seed.get(key) is False for key in (
                    "native_execution_performed", "native_compiler_registered",
                    "provider_dispatch_performed", "new_render_job_created",
                    "imported_source_authority_used", "fresh_registered_source_reopen_verified",
                    "native_video_frame_content_verified", "human_acceptance",
                )), "camera_source_cycle_contract")
        stamp = seed.get("canonical_stamp")
        require(type(stamp) is dict, "camera_source_cycle_stamp")
        result = {key: seed.get(key) for key in ("project_id", "scene_id", "deliverable_id")}
        result.update(generation=stamp.get("generation"), revision=stamp.get("revision"))
    for key in ("project_id", "generation", "scene_id", "deliverable_id"):
        identity(result[key])
    require(type(result["revision"]) is int and result["revision"] >= 1,
            "camera_seed_revision")
    if fixture == "edited1040":
        require(stamp.get("resource") == "project:" + result["project_id"],
                "camera_source_cycle_stamp")
    require(type(seed.get("source_project_sha256")) is str
            and SHA.fullmatch(seed["source_project_sha256"]), "camera_project_digest")
    return result


def _checked_source_cycle_inspection(seed, binding, plan):
    inspected = seed.get("current_source_inspection")
    require(type(inspected) is dict
            and inspected.get("current_binding") == binding
            and inspected.get("current_plan") == plan
            and inspected.get("native_execution_performed") is False
            and inspected.get("imported_source_authority_used") is False,
            "camera_source_cycle_inspection")


def prepare_seed(seed, evidence, *, fixture="original"):
    """Preserve bytes emitted by the canonical Rust compiler, without rebuilding JSON."""
    binding = seed_binding(seed, fixture=fixture)
    program = seed.get("prepared_program", {})
    prepared = program.get("prepared", {})
    compiler = program.get("trusted_compiler", {})
    if fixture == "edited1040":
        _checked_source_cycle_inspection(seed, binding, prepared.get("source"))
    require(program.get("original_source_snapshot_checked") is True
            and program.get("program_semantically_bound_to_source") is True
            and program.get("native_execution_performed") is False
            and program.get("native_permissions_verified") is False
            and program.get("native_source_compiler_registered") is False
            and prepared.get("native_execution_performed") is False
            and prepared.get("permissions_verified") is False
            and prepared.get("imported_source_authority_used") is False
            and prepared.get("native_source_compiler_registered") is False
            and compiler.get("compiler_id") == "motionwright.manim.rust.planar_camera.v1.0"
            and compiler.get("native_execution_performed") is False
            and compiler.get("runtime_sample_pts_verified") is False,
            "camera_source_export_is_not_an_execution_grant")
    octets = compiler.get("plan_document")
    require(type(octets) is list and 0 < len(octets) <= 256 * 1024
            and all(type(x) is int and 0 <= x <= 255 for x in octets),
            "camera_original_plan_bytes")
    plan_raw = bytes(octets)
    plan_hash = compiler.get("plan_sha256")
    source = compiler.get("python_source")
    require(type(plan_hash) is str and SHA.fullmatch(plan_hash)
            and plan_raw.endswith(b"\n") and sha(plan_raw) == plan_hash,
            "camera_original_plan_digest")
    require(type(source) is str and 0 < len(source.encode()) <= 256 * 1024
            and sha(source.encode()) == compiler.get("source_sha256"),
            "camera_original_compiler_source_digest")
    plan = object_bytes(plan_raw)
    require(plan == prepared.get("source") and plan.get("binding") == binding,
            "camera_compiler_plan_differs_from_saved_source")
    plan_path = evidence / "compiled-camera-plan.json"
    private_bytes(plan_path, plan_raw)
    private_bytes(evidence / "compiled-camera-program.py", source.encode())
    private_json(evidence / "source-seed.json", seed)
    # Existing immutable 120-frame oracle also refuses an unsupported fixture
    # before any native effect. It does not create source or grant authority.
    verified_plan(plan_path, plan_hash, fixture=fixture)
    return {"binding": binding, "plan": plan, "plan_sha256": plan_hash,
            "program_sha256": compiler["source_sha256"],
            "plan_path": plan_path, "seed": seed}


def checked_native_evidence(evidence, prepared):
    binding = prepared["binding"]
    require(evidence.get("native_manim_camera_dispatch") == "PASS"
            and evidence.get("receipt_read_error") is None
            and all(evidence.get(k) == value for k, value in binding.items()),
            "camera_native_evidence_not_complete")
    request = evidence.get("request_id")
    require(type(request) is str and 0 < len(request) <= 96, "camera_request_identity")
    for key, suffix, command in (
        ("original_start_receipt", ":start", "driver.manim-community.camera.render.start"),
        ("original_result_receipt", ":result", "driver.manim-community.render.result"),
    ):
        receipt = evidence.get(key, {})
        require(receipt.get("request_id") == request + suffix
                and receipt.get("command") == command
                and receipt.get("project_id") == binding["project_id"]
                and receipt.get("stage") == "completed"
                and receipt.get("generation") == binding["generation"]
                and receipt.get("revision") == binding["revision"],
                "camera_original_receipt_binding")
    result = evidence.get("result", {})
    require(result.get("native_driver") == "driver:manim-community"
            and result.get("renderer") == "manim-community"
            and result.get("scene_id") == binding["scene_id"]
            and result.get("project_revision") == binding["revision"]
            and result.get("plan_sha256") == prepared["plan_sha256"]
            and result.get("profile") == {"width": 640, "height": 360, "frame_rate": 30},
            "camera_original_result_binding")
    match = JOB.fullmatch(result.get("job_ref", ""))
    require(match is not None, "camera_original_job_reference")
    artifact = result.get("artifact", {})
    token = match[1]
    relative = f"videos/mw_{token}/360p30/mw_{token}.mp4"
    require(artifact.get("relative_path") == relative
            and type(artifact.get("bytes")) is int and 0 < artifact["bytes"] <= MAX_MEDIA
            and type(artifact.get("sha256")) is str and SHA.fullmatch(artifact["sha256"])
            and type(artifact.get("camera_getters_sha256")) is str
            and SHA.fullmatch(artifact["camera_getters_sha256"]),
            "camera_original_artifact_identity")
    return result, artifact, token


def finish_render(native, prepared, evidence, output_root, work_root,
                  source_sha, semwright_sha, ffprobe, ffmpeg, *, fixture="original"):
    """Read originals after one terminal Ok; every failure leaves that Ok intact."""
    disk_native = read_object(evidence / EVIDENCE_NAME)
    require(disk_native == native, "camera_stdout_and_durable_evidence_differ")
    result, artifact, token = checked_native_evidence(native, prepared)
    relative = PurePosixPath(artifact["relative_path"])
    media = read(output_root.joinpath(*relative.parts), MAX_MEDIA)
    require(len(media) == artifact["bytes"] and sha(media) == artifact["sha256"],
            "camera_original_mp4_digest")
    original_program = read(work_root / f"mw_{token}.py", 256 * 1024)
    getters = read(work_root / f"mw_{token}.camera.json")
    require(sha(original_program) == prepared["program_sha256"],
            "camera_driver_compiler_bytes_differ")
    require(sha(getters) == artifact["camera_getters_sha256"], "camera_original_getter_digest")
    getter = object_bytes(getters)
    require(getter.get("plan_sha256") == prepared["plan_sha256"]
            and getter.get("binding") == prepared["binding"]
            and getter.get("sample_count") == 120, "camera_original_getter_binding")
    review_media = evidence / MEDIA_NAME
    private_bytes(review_media, media)
    private_bytes(evidence / "original-render-program.py", original_program)
    private_bytes(evidence / "original-camera-getters.json", getters)
    # Original native media only; never a synthetic MP4 or a replacement render.
    pixel_options = {} if fixture == "original" else {"fixture": fixture}
    decoded = verify_pixels(prepared["plan_path"], prepared["plan_sha256"],
                            review_media, artifact["sha256"], ffprobe, ffmpeg, **pixel_options)
    private_json(evidence / "decoded-camera-readback.json", decoded)
    summary = {
        "schema": "motionwright.manim.camera.native-e2e/1",
        "native_manim_camera_e2e": "PASS",
        "motionwright_sha": source_sha, "semwright_sha": semwright_sha,
        "semwright_version": "1.0.0", "manim_version": "0.21.0",
        "project": prepared["binding"],
        "source_project_sha256": prepared["seed"]["source_project_sha256"],
        "plan_sha256": prepared["plan_sha256"],
        "program_sha256": prepared["program_sha256"],
        "original_getters_sha256": sha(getters),
        "native_evidence_sha256": sha(read(evidence / EVIDENCE_NAME)),
        "decoded_readback_sha256": sha(read(evidence / "decoded-camera-readback.json")),
        "mp4": {"relative_path": artifact["relative_path"], "sha256": sha(media),
                "bytes": len(media), "width": 640, "height": 360,
                "frame_count": 120, "frame_rate_num": 30, "frame_rate_den": 1},
        "native_effect_invocations": 1,
        "sealed_custody_required": True,
        "native_editable_reopen_verified": False,
        "full_pixel_equality_verified": False,
        "human_acceptance": False,
    }
    private_json(evidence / "result.json", summary)
    return summary


def verified_publication(root, source_sha, semwright_sha, sealed_originals, *, fixture="original"):
    """Closed read-only publication; ciphertext custody and original digests are required."""
    result = read_object(root / "result.json")
    require(result.get("schema") == "motionwright.manim.camera.native-e2e/1"
            and result.get("native_manim_camera_e2e") == "PASS"
            and result.get("motionwright_sha") == source_sha
            and result.get("semwright_sha") == semwright_sha
            and result.get("semwright_version") == "1.0.0"
            and result.get("manim_version") == "0.21.0"
            and result.get("native_effect_invocations") == 1
            and result.get("sealed_custody_required") is True
            and result.get("native_editable_reopen_verified") is False
            and result.get("human_acceptance") is False, "camera_public_result_scope")
    seed = read_object(root / "source-seed.json")
    prepared = {"binding": seed_binding(seed, fixture=fixture),
                "plan_sha256": result["plan_sha256"]}
    require(result["project"] == prepared["binding"]
            and result["source_project_sha256"] == seed["source_project_sha256"],
            "camera_public_source_binding")
    plan_path = root / "compiled-camera-plan.json"
    plan = verified_plan(plan_path, result["plan_sha256"], fixture=fixture)
    require(plan["binding"] == prepared["binding"], "camera_public_plan_binding")
    if fixture == "edited1040":
        _checked_source_cycle_inspection(seed, prepared["binding"], plan)
    native_raw = read(root / EVIDENCE_NAME)
    require(sha(native_raw) == result["native_evidence_sha256"],
            "camera_public_native_evidence_digest")
    _, artifact, _ = checked_native_evidence(object_bytes(native_raw), prepared)
    require(artifact["sha256"] == result["mp4"]["sha256"]
            and artifact["bytes"] == result["mp4"]["bytes"]
            and artifact["camera_getters_sha256"] == result["original_getters_sha256"],
            "camera_public_native_artifact_digest")
    require(sha(read(root / "original-camera-getters.json")) == result["original_getters_sha256"]
            and sha(read(root / "original-render-program.py")) == result["program_sha256"]
            and sha(read(root / "compiled-camera-program.py")) == result["program_sha256"],
            "camera_public_compiler_getter_custody")
    decoded_raw = read(root / "decoded-camera-readback.json")
    decoded = object_bytes(decoded_raw)
    require(sha(decoded_raw) == result["decoded_readback_sha256"]
            and decoded.get("schema") == "motionwright.manim.camera.decoded-readback/1"
            and decoded.get("compiled_plan_sha256") == result["plan_sha256"]
            and decoded.get("trusted_media_sha256") == artifact["sha256"]
            and decoded.get("binding") == prepared["binding"]
            and decoded.get("source_bound_video_observation") == "PASS"
            and decoded.get("human_acceptance") is False
            and decoded.get("all_120_decoded_frame_motion", {}).get(
                "both_axes_moving_camera_verified_from_decoded_centroids") is True,
            "camera_public_decoded_readback")
    sealed = read_object(root / "camera-custody.json")
    require(sealed.get("schema") == "motionwright.manim.camera.sealed-custody/1"
            and sealed.get("status") == "PASS"
            and sealed.get("selected_originals_complete") is True
            and sealed.get("native_result_retained") is True
            and sealed.get("context", {}).get("source_sha") == source_sha
            and sealed.get("context", {}).get("semwright_sha") == semwright_sha
            and sealed.get("secondary_error_count") == 0,
            "camera_public_custody_incomplete")
    require(type(sealed.get("ciphertext_sha256")) is str
            and SHA.fullmatch(sealed["ciphertext_sha256"])
            and type(sealed.get("recipient_sha256")) is str
            and SHA.fullmatch(sealed["recipient_sha256"]), "camera_public_seal_identity")
    ciphertext = read(sealed_originals, 256 * 1024 * 1024)
    require(sha(ciphertext) == sealed["ciphertext_sha256"]
            and len(ciphertext) == sealed["ciphertext_bytes"]
            and ciphertext.startswith(b"MWCISEAL1\n"), "camera_public_ciphertext_missing_or_changed")
    media = read(root / MEDIA_NAME, MAX_MEDIA)
    require(len(media) == artifact["bytes"] and sha(media) == artifact["sha256"]
            and len(media) >= 12 and media[4:8] == b"ftyp", "camera_public_media_digest")
    return media, {
        "profile": "planar-camera-v1",
        "decoded_frame_count": 120,
        "source_bound_two_axis_camera_motion": True,
        "native_editable_reopen_verified": False,
        "full_pixel_equality_verified": False,
        "human_acceptance": False,
        "compiled_plan_sha256": result["plan_sha256"],
        "native_getters_sha256": result["original_getters_sha256"],
        "sealed_originals_sha256": sealed["ciphertext_sha256"],
        "sealed_originals_recipient_sha256": sealed["recipient_sha256"],
    }


class CameraCustody:
    """Retain the one invocation's outcome before later observation or cleanup."""
    def __init__(self, owner, evidence, seal_path, recipient, context, *, engine=None, profile="camera"):
        require(profile in ("camera", "editable", "graph"), "native_custody_profile")
        self.profile = profile
        self.engine = engine or custody_module()
        self.engine.public_key(recipient)
        self.engine.validate_context(context)
        require(context["run_attempt"] == "1", "camera_native_attempt_cannot_be_replayed")
        for path in (owner, evidence, seal_path.parent):
            require(path.is_absolute() and path.resolve(strict=True) == path
                    and path.is_dir() and not path.is_symlink(), "camera_custody_directory")
        if os.name == "posix":
            require(all(p.stat().st_uid == os.geteuid()
                        and stat.S_IMODE(p.stat().st_mode) & 0o077 == 0
                        for p in (owner, evidence, seal_path.parent)), "camera_custody_owner_mode")
        require(not seal_path.exists() and not seal_path.is_symlink(),
                "camera_existing_ciphertext")
        require(owner != evidence and owner not in evidence.parents and evidence not in owner.parents
                and seal_path.parent not in (owner, evidence)
                and owner not in seal_path.parents and evidence not in seal_path.parents,
                "camera_custody_roots_overlap")
        self.owner, self.evidence, self.seal_path = owner, evidence, seal_path
        self.recipient, self.context = recipient, context
        self.invoked, self.native_result, self.native_error = False, None, None
        self.native_process = None
        self.secondary_errors = []
        self.sealed = False

    def __enter__(self):
        return self

    def secondary(self, stage, error):
        self.secondary_errors.append({"stage": stage, "type": type(error).__name__,
                                      "message": str(error), "automatic_retry": False})

    def once(self, callback):
        require(not self.invoked, "camera_native_invocation_already_attempted")
        self.engine.new_json(self.evidence / "native-invocation-intent.json", {
            "context": self.context, "native_example_invocations": 1,
            "automatic_retry": False,
        })
        self.invoked = True
        try:
            result = callback()
        except BaseException as error:
            self.native_error = error
            raise
        # Keep the actual callback object before any receipt validation, decode,
        # reopen, write or sealing. Such failures cannot turn it into a new job.
        self.native_result = result
        return result

    def __exit__(self, kind, original, traceback):
        terminal = {
            "schema": f"motionwright.manim.{self.profile}.original-terminal/1",
            "native_example_invoked": self.invoked,
            "original_process_observation": self.native_process,
            "native_result": self.native_result,
            "native_error": ({"type": type(self.native_error).__name__,
                             "message": str(self.native_error)} if self.native_error else None),
            "later_error": ({"type": type(original).__name__, "message": str(original)}
                            if original is not None and original is not self.native_error else None),
            "secondary_errors": self.secondary_errors,
            "automatic_retry": False,
            "source_or_human_acceptance": False,
        }
        if self.profile == "graph" and original is not None:
            owner_envelope = getattr(original, "original_envelope", None)
            if owner_envelope is not None:
                # Preserve the whole original SDK envelope even if separate
                # stream custody failed. This remains private, not a retry.
                terminal["original_graph_owner_envelope"] = owner_envelope
        try:
            self.engine.new_json(self.evidence / "original-terminal.json", terminal)
        except BaseException as error:
            self.secondary("terminal-custody", error)
        complete = False
        try:
            snapshot, complete = self.engine.gather_snapshot(
                {"owner": self.owner, "evidence": self.evidence}, preserve_partial=True)
            envelope = self.engine.encrypt_snapshot(snapshot, self.recipient, self.context)
            self.engine.new_bytes(self.seal_path, envelope)
            self.sealed = True
            receipt = {
                "schema": f"motionwright.manim.{self.profile}.sealed-custody/1",
                "status": ("PASS" if complete and not self.secondary_errors else "INCOMPLETE"),
                "context": self.context,
                "ciphertext_sha256": sha(envelope), "ciphertext_bytes": len(envelope),
                "recipient_sha256": self.recipient["sha256"],
                "selected_originals_complete": complete,
                "native_result_retained": self.native_result is not None,
                "secondary_error_count": len(self.secondary_errors),
                "original_owner_tree_removed": False,
                "atomic_sqlite_snapshot_claimed": False,
                "producer_authenticated_by_encryption": False,
            }
            self.engine.new_json(self.evidence / f"{self.profile}-custody.json", receipt)
        except BaseException as error:
            self.secondary("sealed-custody", error)
        if self.secondary_errors or not complete:
            try:
                sys.stderr.write("Manim camera original custody is incomplete; no replay.\n")
            except BaseException:
                pass
        if original is not None:
            return False
        if self.secondary_errors or not complete:
            error = CameraEvidenceError("camera_custody_incomplete_original_retained")
            error.native_result = self.native_result
            raise error
        return False
