#!/usr/bin/env python3
"""Verify a bounded preview reconstruction from preserved native PNG originals.

This verifier does not run a renderer, grant Store authority, or accept a master.
It checks declared producer identities, byte-bound receipts and files, explicit
segment dependencies, and decoded RGBA. Native Host provenance is an additional
external gate. Synthetic fixtures exercise this verifier only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
import uuid
import warnings
import zlib

from PIL import Image


SCHEMA = "motionwright.native-incremental-reconstruction/1"
RESULT_SCHEMA = "motionwright.native-incremental-reconstruction-result/1"
DEFAULT_MAX_FRAMES = 4096
MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 33_177_600
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
HEXREV = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
U64_MAX = (1 << 64) - 1


class VerificationError(ValueError):
    """A required predicate failed; no fallback is permitted."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def need(ok, code, detail):
    if not ok:
        raise VerificationError(code, detail)


def uint(value, label, maximum=U64_MAX):
    need(type(value) is int and 0 <= value <= maximum, "INVALID_INTEGER", label)
    return value


def label(value, name):
    need(isinstance(value, str) and 0 < len(value) <= 512, "INVALID_ID", name)
    return value


def digest(value, name):
    need(isinstance(value, str) and HEX256.fullmatch(value), "INVALID_DIGEST", name)
    return value


def object_value(value, name):
    need(isinstance(value, dict), "INVALID_OBJECT", name)
    return value


def array_value(value, name, maximum):
    need(isinstance(value, list) and len(value) <= maximum, "INVALID_ARRAY", name)
    return value


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "DUPLICATE_JSON_KEY", key)
        result[key] = value
    return result


def read_manifest(path, with_sha256=False):
    path = Path(path)
    need(path.stat().st_size <= 16 * 1024 * 1024, "MANIFEST_TOO_LARGE", str(path))
    data = path.read_bytes()
    need(len(data) <= 16 * 1024 * 1024, "MANIFEST_TOO_LARGE", str(path))
    parsed = json.loads(data.decode("utf-8-sig"), object_pairs_hook=reject_duplicate_keys)
    return (parsed, hashlib.sha256(data).hexdigest()) if with_sha256 else parsed


def source_binding(value):
    value = object_value(value, "source")
    for key in ("motionwright_sha", "sdk_revision"):
        need(isinstance(value.get(key), str) and HEXREV.fullmatch(value[key]),
             "INVALID_SOURCE", key)
    digest(value.get("locks_sha256"), "source.locks_sha256")
    return {key: value[key] for key in ("motionwright_sha", "sdk_revision", "locks_sha256")}


def project_binding(value):
    value = object_value(value, "project")
    generation = value.get("generation")
    need(isinstance(generation, str), "INVALID_GENERATION", "generation must be a canonical UUID")
    try:
        canonical = str(uuid.UUID(generation))
    except (ValueError, AttributeError) as exc:
        raise VerificationError("INVALID_GENERATION", "generation must be a canonical UUID") from exc
    need(generation == canonical, "INVALID_GENERATION", "generation must be a canonical UUID")
    return {"id": label(value.get("id"), "project.id"),
            "generation": generation}


def segments_of(state, maximum):
    segments = array_value(state.get("segments"), "segments", maximum)
    need(segments, "NO_SEGMENTS", "a bounded nonempty timeline is required")
    end = 0
    identities = set()
    for segment in segments:
        object_value(segment, "segment")
        name = label(segment.get("id"), "segment.id")
        need(name not in identities, "DUPLICATE_SEGMENT", name)
        identities.add(name)
        start = uint(segment.get("start_frame"), "segment.start_frame", maximum)
        stop = uint(segment.get("end_frame_exclusive"), "segment.end_frame_exclusive", maximum)
        need(start == end and stop > start, "SEGMENT_ORDER_OR_OVERLAP", name)
        for key in ("plan_sha256", "film_sha256", "dependencies_sha256"):
            digest(segment.get(key), f"{name}.{key}")
        label(segment.get("capture_id"), "segment.capture_id")
        end = stop
    return segments, end


def segment_signature(segment):
    return tuple(segment[key] for key in (
        "id", "start_frame", "end_frame_exclusive", "plan_sha256",
        "film_sha256", "dependencies_sha256"))


def plain_chain(path, allow_missing=False):
    """Check the original path before resolve can hide links or junctions."""
    path = Path(path).absolute()
    need(".." not in path.parts, "PATH_TRAVERSAL", str(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            need(allow_missing, "MISSING_ORIGINAL", str(part))
            continue
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        junction = getattr(part, "is_junction", lambda: False)()
        need(not stat.S_ISLNK(info.st_mode) and not reparse and not junction,
             "LINKED_ORIGINAL", str(part))
    return path


def relative_descriptor(raw_path):
    label(raw_path, "file.path")
    need(not raw_path.startswith("/") and "\\" not in raw_path and ":" not in raw_path,
         "NONCANONICAL_DESCRIPTOR_PATH", raw_path)
    parts = raw_path.split("/")
    need(all(part not in ("", ".", "..") for part in parts),
         "NONCANONICAL_DESCRIPTOR_PATH", raw_path)
    return parts


def png_profile(data, name):
    """Bounded 8-bit truecolor PNG only; preserve color metadata semantics."""
    need(data.startswith(b"\x89PNG\r\n\x1a\n"), "NOT_PNG", name)
    need(len(data) >= 33 and data[8:16] == b"\x00\x00\x00\rIHDR", "PNG_DECODE_FAILED", name)
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", data[16:29])
    need(depth == 8 and color_type in (2, 6) and compression == 0 and filtering == 0 and interlace in (0, 1),
         "PNG_PROFILE_UNSUPPORTED", "only 8-bit RGB/RGBA PNG is admitted: " + name)
    metadata = {}
    offset = 8
    ended = False
    while offset < len(data):
        need(offset + 12 <= len(data), "PNG_DECODE_FAILED", name)
        size = int.from_bytes(data[offset:offset + 4], "big")
        stop = offset + 12 + size
        need(stop <= len(data), "PNG_DECODE_FAILED", name)
        chunk_type = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        expected_crc = int.from_bytes(data[offset + 8 + size:stop], "big")
        need(zlib.crc32(chunk_type + payload) & 0xffffffff == expected_crc, "PNG_DECODE_FAILED", name)
        need(chunk_type != b"acTL", "PNG_PROFILE_UNSUPPORTED", "animated PNG is not admitted: " + name)
        if chunk_type in (b"iCCP", b"gAMA", b"sRGB", b"cHRM", b"cICP", b"mDCV", b"cLLI"):
            key = chunk_type.decode("ascii")
            need(key not in metadata, "PNG_COLOR_METADATA_DUPLICATE", name)
            metadata[key] = hashlib.sha256(payload).hexdigest()
        offset = stop
        if chunk_type == b"IEND":
            need(size == 0 and offset == len(data), "PNG_DECODE_FAILED", name)
            ended = True
            break
    need(ended, "PNG_DECODE_FAILED", name)
    fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return width, height, fingerprint


class Files:
    def __init__(self, root):
        self.root = plain_chain(root).resolve(strict=True)
        need(self.root.is_dir(), "ARTIFACT_ROOT", str(self.root))
        self.seen_paths = {}
        self.seen_file_ids = {}
        self.read_bytes = 0
        self.read_files = 0
        self.hashed_files = 0
        self.hashed_bytes = 0
        self.files_read_by_kind = {}
        self.bytes_read_by_kind = {}
        self.read_and_hash_wall_seconds = 0.0
        self.decoded_frames = 0
        self.decoded_bytes = 0
        self.png_profile_wall_seconds = 0.0
        self.png_decode_wall_seconds = 0.0
        self.rgba_hash_wall_seconds = 0.0

    def read(self, record, owner, kind="original"):
        started = time.perf_counter()
        object_value(record, "file record")
        raw_path = record.get("path")
        candidate = self.root.joinpath(*relative_descriptor(raw_path))
        plain_chain(candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise VerificationError("MISSING_ORIGINAL", raw_path) from exc
        need(resolved.is_relative_to(self.root), "OUTSIDE_ARTIFACT_ROOT", raw_path)
        need(resolved.is_file(), "NOT_REGULAR_FILE", raw_path)
        file_stat = resolved.stat()
        size = uint(record.get("bytes"), "file.bytes", MAX_FILE_BYTES)
        need(size > 0 and file_stat.st_size == size, "SIZE_MISMATCH", raw_path)
        expected = digest(record.get("sha256"), "file.sha256")
        need(self.read_bytes + self.decoded_bytes + size <= MAX_TOTAL_BYTES,
             "AGGREGATE_BYTE_BUDGET", raw_path)
        need(resolved not in self.seen_paths, "DUPLICATE_ORIGINAL_PATH", raw_path)
        file_id = (file_stat.st_dev, file_stat.st_ino)
        if file_stat.st_ino:
            need(file_id not in self.seen_file_ids, "ALIASED_ORIGINAL", raw_path)
            self.seen_file_ids[file_id] = owner
        self.seen_paths[resolved] = owner
        # Validate the descriptor before and after the read. fstat prevents a
        # checked regular file from being silently replaced at open time.
        with candidate.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            need(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino, opened.st_size) ==
                 (file_stat.st_dev, file_stat.st_ino, file_stat.st_size), "ORIGINAL_REPLACED_DURING_READ", raw_path)
            data = handle.read(size + 1)
        self.read_bytes += len(data)
        self.read_files += 1
        self.files_read_by_kind[kind] = self.files_read_by_kind.get(kind, 0) + 1
        self.bytes_read_by_kind[kind] = self.bytes_read_by_kind.get(kind, 0) + len(data)
        try:
            plain_chain(candidate)
            observed_digest = hashlib.sha256(data).hexdigest()
            self.hashed_files += 1
            self.hashed_bytes += len(data)
            need(len(data) == size and observed_digest == expected,
                 "CORRUPT_ORIGINAL", raw_path)
        finally:
            self.read_and_hash_wall_seconds += time.perf_counter() - started
        return resolved, data

    def png(self, record, owner):
        path, data = self.read(record, owner, kind="capture_png")
        profile_started = time.perf_counter()
        width, height, color_metadata = png_profile(data, str(path))
        self.png_profile_wall_seconds += time.perf_counter() - profile_started
        need(width > 0 and height > 0 and width * height <= MAX_PIXELS,
             "PIXEL_BUDGET", str(path))
        rgba_size = width * height * 4
        need(self.read_bytes + self.decoded_bytes + rgba_size <= MAX_TOTAL_BYTES,
             "AGGREGATE_BYTE_BUDGET", str(path))
        decode_started = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    need(image.format == "PNG" and getattr(image, "n_frames", 1) == 1,
                         "NOT_SINGLE_FRAME_PNG", str(path))
                    need(image.size == (width, height), "PNG_DECODE_FAILED", str(path))
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    rgba = image.convert("RGBA").tobytes()
        except VerificationError:
            raise
        except Exception as exc:
            raise VerificationError("PNG_DECODE_FAILED", str(path)) from exc
        self.decoded_frames += 1
        self.decoded_bytes += len(rgba)
        self.png_decode_wall_seconds += time.perf_counter() - decode_started
        hash_started = time.perf_counter()
        rgba_sha256 = hashlib.sha256(rgba).hexdigest()
        self.rgba_hash_wall_seconds += time.perf_counter() - hash_started
        return {"path": str(path), "relative_path": record["path"],
                "sha256": record["sha256"], "bytes": record["bytes"],
                "width": width, "height": height,
                "rgba_sha256": rgba_sha256,
                "color_metadata_sha256": color_metadata}


def verify(manifest, artifact_root, max_frames=DEFAULT_MAX_FRAMES):
    """Return a preview-only report, or raise VerificationError without output."""
    started = time.perf_counter()
    uint(max_frames, "max_frames", 100_000)
    need(max_frames > 0, "FRAME_BUDGET", "max_frames must be positive")
    object_value(manifest, "manifest")
    need(manifest.get("schema") == SCHEMA, "SCHEMA", "unsupported schema")
    kind = manifest.get("evidence_kind")
    need(kind in ("native", "synthetic"), "EVIDENCE_KIND", "native or synthetic required")
    source = source_binding(manifest.get("source"))
    project = project_binding(manifest.get("project"))
    before = object_value(manifest.get("before"), "before")
    after = object_value(manifest.get("after"), "after")
    old_revision = uint(before.get("revision"), "before.revision")
    new_revision = uint(after.get("revision"), "after.revision")
    need(old_revision < U64_MAX and new_revision == old_revision + 1,
         "NONCONSECUTIVE_REVISION", "after must be exactly before + 1")
    for state in (before, after):
        digest(state.get("film_sha256"), "state.film_sha256")
        digest(state.get("dependencies_sha256"), "state.dependencies_sha256")
    need((before["film_sha256"], before["dependencies_sha256"]) !=
         (after["film_sha256"], after["dependencies_sha256"]),
         "NO_OP", "the authoritative state must change")
    old_segments, old_end = segments_of(before, max_frames)
    new_segments, end = segments_of(after, max_frames)
    need(old_end == end, "TIMELINE_LENGTH_CHANGED_UNSUPPORTED",
         "this bounded profile compares equal-length timelines")
    old_by_id = {item["id"]: item for item in old_segments}

    intervals = array_value(manifest.get("invalidated_ranges"), "invalidated_ranges", max_frames)
    need(intervals, "NO_INVALIDATION", "a real change requires an invalidation range")
    dirty = set()
    previous_end = 0
    for interval in intervals:
        object_value(interval, "invalidated range")
        start = uint(interval.get("start_frame"), "invalidated.start", end)
        stop = uint(interval.get("end_frame_exclusive"), "invalidated.end", end)
        need(start >= previous_end and stop > start, "INVALIDATION_ORDER_OR_OVERLAP", str(interval))
        causes = array_value(interval.get("causes"), "invalidated.causes", 256)
        need(causes and len(set(map(str, causes))) == len(causes), "INVALIDATION_CAUSES", str(interval))
        for cause in causes:
            label(cause, "invalidation cause")
        dirty.update(range(start, stop))
        previous_end = stop

    # Digest or mapping changes force the entire corresponding segment dirty.
    # Provider-specific dependency closure must also be represented in ranges.
    for segment in new_segments:
        previous = old_by_id.get(segment["id"])
        if previous is None or segment_signature(previous) != segment_signature(segment):
            needed = set(range(segment["start_frame"], segment["end_frame_exclusive"]))
            need(needed <= dirty, "UNDECLARED_DIRTY_SEGMENT", segment["id"])
    if before["dependencies_sha256"] != after["dependencies_sha256"]:
        need(dirty == set(range(end)), "GLOBAL_DEPENDENCIES_REQUIRE_FULL_INVALIDATION",
             "changed shared SDK/font/asset/output context cannot reuse old segment frames")

    files = Files(artifact_root)
    captures = {}
    job_ids = set()
    receipt_digests = set()
    actual_costs = {"before": 0, "after_dirty": 0, "full_after": 0}
    capture_list = array_value(manifest.get("captures"), "captures", max_frames + 2)
    need(capture_list, "NO_CAPTURES", "original captures required")
    for capture in capture_list:
        object_value(capture, "capture")
        capture_id = label(capture.get("id"), "capture.id")
        need(capture_id not in captures, "DUPLICATE_CAPTURE", capture_id)
        role = capture.get("role")
        need(role in actual_costs, "CAPTURE_ROLE", capture_id)
        state = before if role == "before" else after
        need(source_binding(capture.get("source")) == source, "SOURCE_MISMATCH", capture_id)
        need(project_binding(capture.get("project")) == project, "PROJECT_MISMATCH", capture_id)
        need(uint(capture.get("revision"), "capture.revision") == state["revision"],
             "STALE_CAPTURE", capture_id)
        for key in ("film_sha256", "dependencies_sha256"):
            need(digest(capture.get(key), f"capture.{key}") == state[key],
                 "CAPTURE_STATE_MISMATCH", f"{capture_id}.{key}")
        job_id = label(capture.get("job_id"), "capture.job_id")
        need(job_id not in job_ids, "DUPLICATE_PRODUCER_JOB", job_id)
        job_ids.add(job_id)
        need(uint(capture.get("first_frame"), "capture.first_frame") == 0,
             "NONZERO_NATIVE_PREFIX", capture_id)
        prefix_end = uint(capture.get("end_frame_exclusive"), "capture.end_frame", max_frames)
        need(0 < prefix_end <= end, "CAPTURE_BOUNDS", capture_id)
        receipt = capture.get("receipt")
        receipt_path, _ = files.read(receipt, f"{capture_id}.receipt", kind="producer_receipt")
        need(receipt["sha256"] not in receipt_digests, "DUPLICATE_PRODUCER_RECEIPT", capture_id)
        receipt_digests.add(receipt["sha256"])
        frames = array_value(capture.get("frames"), "capture.frames", max_frames)
        need(len(frames) == prefix_end, "INCOMPLETE_NATIVE_PREFIX", capture_id)
        decoded = []
        for index, frame in enumerate(frames):
            object_value(frame, "frame")
            need(uint(frame.get("source_index"), "frame.source_index", max_frames) == index,
                 "FRAME_ORDER_OR_DUPLICATE", capture_id)
            decoded.append(files.png(frame, f"{capture_id}.frame.{index}"))
        actual_costs[role] += prefix_end
        captures[capture_id] = {"binding": capture, "decoded": decoded,
                                "receipt_path": str(receipt_path)}

    full_id = label(manifest.get("full_after_capture_id"), "full_after_capture_id")
    need(full_id in captures, "MISSING_FULL_AFTER", full_id)
    full = captures[full_id]
    need(full["binding"]["role"] == "full_after" and len(full["decoded"]) == end,
         "NOT_FULL_AFTER", full_id)
    need(sum(value["binding"]["role"] == "full_after" for value in captures.values()) == 1,
         "AMBIGUOUS_FULL_AFTER", full_id)

    previous_frames = {}
    used_captures = {full_id}
    for segment in old_segments:
        capture_id = segment["capture_id"]
        need(capture_id in captures, "MISSING_CAPTURE", capture_id)
        capture = captures[capture_id]
        need(capture["binding"]["role"] == "before", "BEFORE_CAPTURE_ROLE", capture_id)
        need(len(capture["decoded"]) >= segment["end_frame_exclusive"], "SHORT_CAPTURE", capture_id)
        used_captures.add(capture_id)
        for index in range(segment["start_frame"], segment["end_frame_exclusive"]):
            previous_frames[index] = capture["decoded"][index]

    rebuilt = []
    reused = generated = changed_dirty = 0
    reference_comparisons = change_comparisons = color_comparisons = 0
    selected_bytes = reused_bytes = generated_bytes = 0
    comparison_started = time.perf_counter()
    for segment in new_segments:
        capture_id = segment["capture_id"]
        need(capture_id in captures, "MISSING_CAPTURE", capture_id)
        capture = captures[capture_id]
        role = capture["binding"]["role"]
        need(role in ("before", "after_dirty"), "REFERENCE_USED_AS_PRODUCER", capture_id)
        need(len(capture["decoded"]) >= segment["end_frame_exclusive"], "SHORT_CAPTURE", capture_id)
        indexes = range(segment["start_frame"], segment["end_frame_exclusive"])
        if role == "before":
            previous = old_by_id.get(segment["id"])
            need(previous is not None and segment_signature(previous) == segment_signature(segment),
                 "STALE_SEGMENT_REUSE", segment["id"])
            need(capture_id == previous["capture_id"], "ORIGINAL_CAPTURE_REBOUND", segment["id"])
            need(not any(index in dirty for index in indexes), "INVALIDATED_FRAME_REUSED", segment["id"])
        used_captures.add(capture_id)
        for index in indexes:
            frame = capture["decoded"][index]
            reference = full["decoded"][index]
            observed = (frame["width"], frame["height"], frame["rgba_sha256"])
            expected = (reference["width"], reference["height"], reference["rgba_sha256"])
            reference_comparisons += 1
            need(observed == expected, "RGBA_MISMATCH", f"frame {index}")
            color_comparisons += 1
            need(frame["color_metadata_sha256"] == reference["color_metadata_sha256"],
                 "COLOR_METADATA_MISMATCH", f"frame {index}")
            if role == "before":
                reused += 1
                reused_bytes += frame["bytes"]
            else:
                generated += 1
                generated_bytes += frame["bytes"]
                old = previous_frames[index]
                old_pixels = (old["width"], old["height"], old["rgba_sha256"])
                if index in dirty:
                    change_comparisons += 1
                    if observed != old_pixels:
                        changed_dirty += 1
            selected_bytes += frame["bytes"]
            binding = capture["binding"]
            rebuilt.append({"frame_index": index, "source_index": index,
                            "capture_id": capture_id, "producer_role": role,
                            "source_revision": binding["revision"],
                            **frame,
                            "lineage": {
                                "producer_job_id": binding["job_id"],
                                "producer_receipt": {key: binding["receipt"][key]
                                                     for key in ("path", "sha256", "bytes")},
                                "producer_film_sha256": binding["film_sha256"],
                                "producer_dependencies_sha256": binding["dependencies_sha256"],
                                "segment": {key: segment[key] for key in (
                                    "id", "start_frame", "end_frame_exclusive", "plan_sha256",
                                    "film_sha256", "dependencies_sha256")},
                            },
                            "comparison_reference": {
                                "capture_id": full_id,
                                "producer_job_id": full["binding"]["job_id"],
                                "producer_receipt_sha256": full["binding"]["receipt"]["sha256"],
                                "source_index": index,
                                "source_revision": full["binding"]["revision"],
                                "film_sha256": full["binding"]["film_sha256"],
                                "dependencies_sha256": full["binding"]["dependencies_sha256"],
                                **{key: reference[key] for key in (
                                    "relative_path", "sha256", "bytes", "rgba_sha256", "color_metadata_sha256")},
                                "used_as_reconstruction_source": False,
                            }})
    comparison_seconds = time.perf_counter() - comparison_started
    need(used_captures == set(captures), "UNUSED_CAPTURE", "unaccounted producer capture")
    need(generated > 0, "NO_DIRTY_PRODUCTION", "at least one after_dirty frame is required")
    need(changed_dirty > 0, "NO_VISUAL_CHANGE", "a dirty frame must differ from before")

    costs = object_value(manifest.get("costs"), "costs")
    setup = object_value(object_value(costs.get("setup"), "costs.setup").get("before"), "setup.before")
    operation = object_value(costs.get("operation"), "costs.operation")
    validation = object_value(costs.get("validation"), "costs.validation")
    for section, role in ((setup, "before"), (operation, "after_dirty"), (validation, "full_after")):
        need(uint(section.get("rendered_frames"), f"cost.{role}") == actual_costs[role],
             "RENDER_COST_MISMATCH", role)
    for dimension in ("encode", "transfer"):
        value = object_value(operation.get(dimension, {"measured": False}), f"operation.{dimension}")
        need(value.get("measured") is False and not any(
            number != 0 for key, number in value.items() if key.startswith("avoided")),
             "UNSUPPORTED_COST_CLAIM", dimension)
    net_avoided = actual_costs["full_after"] - actual_costs["after_dirty"]
    return {
        "schema": RESULT_SCHEMA, "status": "PREVIEW_ONLY",
        "result": "PASS_PREVIEW_RECONSTRUCTION" if reused else "NO_INCREMENTAL_REUSE",
        "master_eligible": False, "store_authority_granted": False,
        "native_execution_verified": False, "acceptance_granted": False,
        "evidence_kind": kind, "source": source, "project": project,
        "artifact_root": str(files.root),
        "before_revision": old_revision, "after_revision": new_revision,
        "comparison": "DECODED_RGBA_AND_COLOR_METADATA_MATCH_ALL_RECONSTRUCTED_FRAMES",
        "capture_provenance_verification": "BOUND_FILES_AND_DECLARED_IDENTITIES_ONLY",
        "invalidation_scope": "DECLARED_RANGES_AND_SEGMENT_PLAN_FILM_DEPENDENCY_DIGESTS",
        "frames": rebuilt, "reused_frames": reused, "after_dirty_frames_used": generated,
        "dirty_frames_changed_from_before": changed_dirty,
        "costs": {
            "setup": {"before": {"rendered_frames": actual_costs["before"]}},
            "operation": {"rendered_frames": actual_costs["after_dirty"],
                          "first_frame": 0, "prefix_skip_claimed": False,
                          "rendered_frames_avoided_vs_full_after": max(0, net_avoided),
                          "net_rendered_frames_avoided_vs_full_after": net_avoided,
                          "encode": {"measured": False, "performed_work": "UNKNOWN", "avoided": 0},
                          "transfer": {"measured": False, "performed_work": "UNKNOWN", "avoided": 0}},
            "validation": {"rendered_frames": actual_costs["full_after"],
                           "measurement_scope": "HELPER_COMPLETED_FILE_READS_HASHES_DECODES_AND_DIGEST_COMPARISONS",
                           "file_read_bytes_are_disk_io_measurement": False,
                           "producer_receipt_files_read": files.files_read_by_kind.get("producer_receipt", 0),
                           "producer_receipt_bytes_read": files.bytes_read_by_kind.get("producer_receipt", 0),
                           "capture_png_files_read": files.files_read_by_kind.get("capture_png", 0),
                           "capture_png_bytes_read": files.bytes_read_by_kind.get("capture_png", 0),
                           "decoded_frames": files.decoded_frames,
                           "original_bytes_read": files.read_bytes,
                           "decoded_rgba_bytes": files.decoded_bytes,
                           "original_bytes_sha256_hashed": files.hashed_bytes,
                           "decoded_rgba_bytes_sha256_hashed": files.decoded_bytes,
                           "reference_frame_dimension_and_rgba_digest_comparisons": reference_comparisons,
                           "reference_color_metadata_digest_comparisons": color_comparisons,
                           "dirty_before_dimension_and_rgba_digest_comparisons": change_comparisons,
                           "file_read_and_hash_wall_seconds": files.read_and_hash_wall_seconds,
                           "png_profile_wall_seconds": files.png_profile_wall_seconds,
                           "png_decode_wall_seconds": files.png_decode_wall_seconds,
                           "rgba_hash_wall_seconds": files.rgba_hash_wall_seconds,
                           "comparison_and_selection_wall_seconds": comparison_seconds,
                           "verifier_wall_seconds": time.perf_counter() - started},
            "reconstruction": {
                "selection": {"frames": len(rebuilt), "png_bytes": selected_bytes,
                              "reused_before_frames": reused, "reused_before_png_bytes": reused_bytes,
                              "after_dirty_frames": generated, "after_dirty_png_bytes": generated_bytes},
                "materialization": {"executed": False, "status": "NOT_RUN",
                                    "source_files_reread": 0, "source_bytes_reread": 0,
                                    "files_written": 0, "bytes_written": 0,
                                    "output_files_rehashed": 0, "output_bytes_rehashed": 0,
                                    "wall_seconds": None},
            },
            "total_original_rendered_frames": sum(actual_costs.values()),
            "end_to_end_speedup_claimed": False,
        },
    }


def materialize_preview(report, output_dir):
    """Copy only selected original PNGs; preserve source paths and lineage."""
    need(report["result"] == "PASS_PREVIEW_RECONSTRUCTION", "NOT_INCREMENTAL", report["result"])
    output = plain_chain(output_dir, allow_missing=True)
    need(not output.exists(), "OUTPUT_EXISTS", str(output))
    expected_copy_bytes = sum(frame["bytes"] for frame in report["frames"])
    validation = report["costs"]["validation"]
    need(validation["original_bytes_read"] + validation["decoded_rgba_bytes"] + 2 * expected_copy_bytes <=
         MAX_TOTAL_BYTES, "AGGREGATE_BYTE_BUDGET", "verification plus source rereads and output readbacks")
    originals = Files(report["artifact_root"])
    started = time.perf_counter()
    copied_outputs = None
    files_written = bytes_written = 0
    measurement = {
        "executed": True, "status": "IN_PROGRESS",
        "measurement_scope": "HELPER_LOCAL_COPY_AND_READBACK_ONLY_NO_NATIVE_ENCODE_OR_NETWORK_TRANSFER",
    }
    report["costs"]["reconstruction"]["materialization"] = measurement
    try:
        output.mkdir(parents=True, exist_ok=False)
        copied_outputs = Files(output)
        for frame in report["frames"]:
            source, data = originals.read({"path": frame["relative_path"], "bytes": frame["bytes"],
                                           "sha256": frame["sha256"]}, f"preview.{frame['frame_index']}",
                                          kind="selected_source")
            need(str(source) == frame["path"], "ORIGINAL_PATH_REBOUND", frame["relative_path"])
            target = output / f"frame-{frame['frame_index']:08d}.png"
            with target.open("xb") as handle:
                written = handle.write(data)
            files_written += 1
            bytes_written += written
            need(written == len(data), "INCOMPLETE_PREVIEW_WRITE", target.name)
            materialized = {"path": target.name, "sha256": frame["sha256"], "bytes": written}
            copied_outputs.read(materialized, f"materialized.{frame['frame_index']}", kind="materialized_output")
            frame["reconstructed_path"] = str(target.resolve())
            frame["materialized"] = materialized
        report["preview_output"] = str(output.resolve())
        measurement["status"] = "COMPLETE_VERIFIED_COPY_OF_SELECTED_ORIGINAL_PNGS"
    except Exception:
        measurement["status"] = "FAILED"
        report["status"] = "FAIL"
        report["result"] = "FAIL_PREVIEW_MATERIALIZATION"
        raise
    finally:
        measurement.update({
            "source_files_reread": originals.read_files,
            "source_bytes_reread": originals.read_bytes,
            "files_written": files_written, "bytes_written": bytes_written,
            "output_files_rehashed": copied_outputs.hashed_files if copied_outputs else 0,
            "output_bytes_rehashed": copied_outputs.hashed_bytes if copied_outputs else 0,
            "wall_seconds": time.perf_counter() - started,
        })
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    args = parser.parse_args(argv)
    code = 0
    report_destination = None
    input_digest = None
    report = None
    try:
        if args.report:
            destination = plain_chain(args.report, allow_missing=True)
            need(not destination.exists(), "REPORT_EXISTS", str(destination))
            report_destination = destination
        if args.output_dir:
            output_destination = plain_chain(args.output_dir, allow_missing=True)
            need(not output_destination.exists(), "OUTPUT_EXISTS", str(output_destination))
            if report_destination:
                need(not output_destination.is_relative_to(report_destination),
                     "OUTPUT_REPORT_COLLISION", str(output_destination))
        manifest, input_digest = read_manifest(args.manifest, with_sha256=True)
        report = verify(manifest, args.artifact_root, args.max_frames)
        if report["result"] != "PASS_PREVIEW_RECONSTRUCTION":
            code = 2
        elif args.output_dir:
            report = materialize_preview(report, args.output_dir)
    except (VerificationError, OSError, ValueError, TypeError, KeyError) as exc:
        code = 1
        partial_costs = report.get("costs") if isinstance(report, dict) and report.get("result") == \
            "FAIL_PREVIEW_MATERIALIZATION" else None
        report = {"schema": RESULT_SCHEMA, "status": "FAIL", "result": "REJECTED",
                  "master_eligible": False, "store_authority_granted": False,
                  "native_execution_verified": False, "acceptance_granted": False,
                  "error_code": getattr(exc, "code", type(exc).__name__), "error": str(exc)}
        if partial_costs is not None:
            report["costs"] = partial_costs
    if input_digest:
        report["input_manifest_sha256"] = input_digest
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if report_destination:
        try:
            plain_chain(report_destination, allow_missing=True)
            report_destination.parent.mkdir(parents=True, exist_ok=True)
            with report_destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
        except (OSError, VerificationError) as exc:
            print(json.dumps({"status": "FAIL", "error": str(exc), "master_eligible": False}))
            return 1
    print(rendered, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
