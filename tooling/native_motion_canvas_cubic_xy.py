"""Closed read-only custody join for the single-pair cubic X/Y development run.

This reader consumes original files after the Host has closed. It neither
starts a provider nor decodes or creates media. The original pixel oracle and
all-frame RGBA comparison retain their own evidence and exact source identity.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path, PurePosixPath
import re

from native_motion_canvas_roundtrip import validated_native_job_ref, uuid

SCHEMA = "motionwright.motion-canvas-cubic-xy-pair/1"
FRAMES = (0, 2, 8, 30)
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILE = 24 * 1024 * 1024
FILES = {
    "identity": "cycle/original/source.identity.original.json",
    "project": "cycle/original/project.original.json",
    "film": "cycle/original/film.original.json",
    "exported_film": "motionwright-direct-parity-film.json",
    "inspection": "cycle/original/native/project-inspect.json",
    "binding": "cycle/original/native/binding-verification.original.json",
    "result": "result.json",
    "parity": "native-direct-mediated-parity.json",
}


class CubicXYRefused(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise CubicXYRefused(reason)


def custody():
    import ci_private_evidence
    return ci_private_evidence


def exact_integer(value, minimum=0):
    return type(value) is int and value >= minimum


def sha(value):
    return hashlib.sha256(value).hexdigest()


def path_in(root, name):
    require(type(name) is str and 0 < len(name) <= 1024
            and "\\" not in name and ":" not in name
            and all(32 <= ord(c) != 127 for c in name), "unsafe_reference_path")
    relative = PurePosixPath(name)
    require(not relative.is_absolute()
            and all(part not in {"", ".", ".."} for part in name.split("/")),
            "unsafe_reference_path")
    root = Path(root)
    require(root.is_absolute() and root.resolve(strict=True) == root
            and not any(p.is_symlink() for p in (root, *root.parents)),
            "noncanonical_reference_root")
    path = root.joinpath(*relative.parts)
    require(path.resolve(strict=True) == path
            and not any(p.is_symlink() for p in (path, *path.parents)),
            "noncanonical_reference_path")
    return path


def capture_reference(root, label, path):
    require(label in {"owner", "evidence"}, "unknown_reference_root")
    path = Path(path)
    name = path.relative_to(root).as_posix()
    raw, _ = custody().regular(path_in(root, name), MAX_FILE)
    require(0 < len(raw), "empty_original")
    return {"root": label, "path": name, "bytes": len(raw), "sha256": sha(raw)}


def read_reference(roots, ref):
    require(type(ref) is dict and set(ref) == {"root", "path", "bytes", "sha256"}
            and ref["root"] in {"owner", "evidence"}
            and exact_integer(ref["bytes"], 1) and ref["bytes"] <= MAX_FILE
            and type(ref["sha256"]) is str and HEX.fullmatch(ref["sha256"]),
            "invalid_original_reference")
    raw, _ = custody().regular(path_in(roots[ref["root"]], ref["path"]), ref["bytes"])
    require(len(raw) == ref["bytes"] and sha(raw) == ref["sha256"],
            "original_changed_after_capture")
    return raw


def capture(owner, evidence, *, context, source, jobs, host_pid, artifacts):
    require(set(artifacts) == {"mediated", "direct"}, "artifact_legs")
    witness = {"schema": SCHEMA, "selection": "out-cubic-xy",
        "context": context, "source": source, "jobs": dict(jobs), "host_pid": host_pid,
        "files": {name: capture_reference(evidence, "evidence", evidence / relative)
                  for name, relative in FILES.items()}, "artifacts": {},
        "automatic_retry": False, "formal_acceptance": False, "human_visual_acceptance": False}
    require(artifacts["mediated"] != artifacts["direct"], "artifact_reuse")
    for leg, root in artifacts.items():
        witness["artifacts"][leg] = {
            "manifest": capture_reference(owner, "owner", root / "artifact-manifest.json"),
            "frames": [capture_reference(owner, "owner", root / "frames" / f"{i:06d}.png")
                       for i in range(60)]}
    return witness


def validate_geometry(geometry):
    fixed = {
        "schema": "motionwright.native-out-cubic-pixels/2",
        "native_original_pixels_verified": True,
        "interpolation": "ease_out_cubic", "native_motion_easing": "out_cubic",
        "position_sampled_frames": list(FRAMES),
        "position_roi_pixels": [1370, 580, 320, 300],
        "position_authored_start": [1410, 620], "position_authored_end": [1510, 750],
        "position_solid_rgb": [119, 198, 250], "position_rgb_tolerance": 2,
        "position_axis_tolerance_px": 1.5,
        "position_pixel_center_convention": "column+0.5,row+0.5",
        "position_x_verified": True, "position_y_verified": True,
        "opacity_sampled_frames": [0, 15, 30],
        "pixel_difference_interpreted_as_visual_approval": False}
    require(type(geometry) is dict
            and all(type(geometry.get(k)) is type(v) and geometry[k] == v for k, v in fixed.items()),
            "geometry_source_contract_changed")
    rows = geometry.get("position_geometry")
    require(type(rows) is list and len(rows) == 4, "geometry_frame_roster")
    for frame, row in zip(FRAMES, rows):
        require(type(row) is dict and type(row.get("frame")) is int and row["frame"] == frame,
                "geometry_frame_roster")
        progress = 1.0 - (1.0 - frame / 30.0) ** 3
        left, top = 1410 + 100 * progress, 620 + 130 * progress
        expected = [left + 70, top + 45]
        require(row.get("expected_center") == expected, "geometry_expected_position")
        observed, edges, errors = (row.get(k) for k in
                                   ("observed_center", "bounding_edges", "axis_error_px"))
        for values, length in ((observed, 2), (edges, 4), (errors, 2)):
            require(type(values) is list and len(values) == length
                    and all(type(v) in {int, float} and math.isfinite(v) for v in values),
                    "geometry_nonfinite_or_shape")
        require(all(abs(a - b) <= 1.5 for a, b in zip(observed, expected))
                and errors == [abs(a - b) for a, b in zip(observed, expected)]
                and all(abs(a - b) <= 1.5 for a, b in zip(
                    edges, [left, top, left + 140, top + 90])),
                "geometry_independent_axis_bounds")
        count = row.get("solid_rgb_pixels")
        require(exact_integer(count, 1) and 139 * 89 - 4 <= count <= 141 * 91
                and count >= (edges[2] - edges[0]) * (edges[3] - edges[1]) - 4,
                "geometry_solid_shape")
    full, half = (geometry.get(k) for k in
                 ("opacity_full_mean_rgb_distance", "opacity_half_to_final_mean_rgb_distance"))
    require(all(type(v) in {int, float} and math.isfinite(v) for v in (full, half))
            and full >= 18.0 and 0.5 <= half < full * 0.45, "geometry_opacity_control")


def validate_source_tile(project, identity):
    uuid(identity.get("project_id"))
    uuid(identity.get("generation"))
    scene_id = uuid(identity.get("scene_id"))
    scenes = project.get("scenes")
    require(type(scenes) is list and len(scenes) == 1 and scenes[0].get("id") == scene_id,
            "source_scene_changed")
    nodes = scenes[0].get("nodes")
    require(type(nodes) is list and 1 <= len(nodes) <= 32, "source_nodes_unbounded")
    tiles = [node for node in nodes if type(node) is dict
             and node.get("name") == "Native exact cubic-out position tile"]
    require(len(tiles) == 1, "source_cubic_tile_not_unique")
    node = tiles[0]
    uuid(node.get("id"))
    require(node.get("kind") == "rectangle" and node.get("parent_id") is None
            and node.get("coordinate_space") == "project_pixels"
            and node.get("style", {}).get("fill") == "#77C6FA"
            and node.get("style", {}).get("stroke") is None
            and node.get("style", {}).get("stroke_width") == 0,
            "source_cubic_tile_shape")
    for field, expected in {"x":1510, "y":750, "width":140, "height":90,
                            "rotation_deg":0, "opacity":1}.items():
        require(type(node.get(field)) in {int, float} and node[field] == expected,
                "source_cubic_tile_geometry")
    keys = node.get("keyframes")
    require(type(keys) is list and len(keys) == 4, "source_cubic_keyframe_roster")
    expected_keys = ((0, "x", 1410, "linear"), (0, "y", 620, "linear"),
                     (1, "x", 1510, "ease_out_cubic"), (1, "y", 750, "ease_out_cubic"))
    for key, (second, prop, value, interpolation) in zip(keys, expected_keys):
        require(type(key) is dict and type(key.get("value")) in {int, float}
                and key["value"] == value and key.get("property") == prop
                and key.get("interpolation") == interpolation
                and type(key.get("at")) is dict and set(key["at"]) == {"num", "den"}
                and type(key["at"]["num"]) is str and key["at"]["num"] == str(second)
                and type(key["at"]["den"]) is str and key["at"]["den"] == "1",
                "source_cubic_keyframe_changed")


def inspect(owner, evidence, witness, *, context, source, jobs, hosts, completed, closure, terminal):
    require(type(witness) is dict and witness.get("schema") == SCHEMA
            and witness.get("selection") == "out-cubic-xy"
            and witness.get("context") == context and witness.get("source") == source,
            "source_or_selection_changed")
    require(context.get("source_sha") == source.get("motionwright_sha")
            and context.get("semwright_sha") == source.get("sdk_revision")
            and context.get("run_attempt") == "1", "context_not_source_bound")
    require(witness.get("automatic_retry") is False
            and witness.get("formal_acceptance") is False
            and witness.get("human_visual_acceptance") is False, "acceptance_not_available")
    leg_jobs = witness.get("jobs")
    require(type(leg_jobs) is dict and set(leg_jobs) == {"mediated", "direct"},
            "job_legs")
    for value in leg_jobs.values():
        validated_native_job_ref(value)
    require(jobs == [leg_jobs["mediated"], leg_jobs["direct"]]
            and len(set(jobs)) == 2 and completed == ["original"], "job_or_stage_count")
    pid = witness.get("host_pid")
    require(exact_integer(pid, 1) and hosts == [pid]
            and type(terminal) is dict and terminal.get("pid") == pid
            and type(terminal.get("exit_code")) is int
            and terminal["exit_code"] in {0, -15}
            and terminal.get("group_quiescent") is True, "host_terminal_not_verified")
    require(type(closure) is dict and closure.get("host_identity", {}).get("pid") == pid
            and closure.get("final_inventory_verified") is True
            and closure.get("broker_group_closed") is True
            and closure.get("observed_descendants_closed") is True
            and closure.get("automatic_retry") is False, "host_closure_not_verified")
    roots = {"owner": owner, "evidence": evidence}
    refs = witness.get("files")
    require(type(refs) is dict and set(refs) == set(FILES), "original_file_roster")
    values = {}
    for name, relative in FILES.items():
        require(refs[name].get("root") == "evidence" and refs[name].get("path") == relative,
                "original_file_role_changed")
        values[name] = custody().strict(read_reference(roots, refs[name]))
    identity, project, result, parity = (values[k] for k in ("identity", "project", "result", "parity"))
    validate_source_tile(project, identity)
    revision = identity.get("revision")
    require(exact_integer(revision) and identity.get("frame_count") == 60
            and (identity.get("width"), identity.get("height"), identity.get("frame_rate"))
                == (1920, 1080, "30/1"), "source_profile_changed")
    for key, project_key in (("project_id", "id"), ("generation", "generation"), ("revision", "revision")):
        require(identity.get(key) == project.get(project_key) == result.get("project", {}).get(key),
                "project_identity_changed")
    for report in (result, parity):
        require(report.get("motionwright_sha") == context["source_sha"]
                and report.get("semwright_sha") == context["semwright_sha"],
                "result_source_changed")
    require(result.get("native_render_e2e") == "PASS" and result.get("frame_count") == 60
            and result.get("direct_mediated_native_frame_parity") == "PASS"
            and parity.get("project_generation") == identity["generation"]
            and parity.get("project_revision") == revision
            and parity.get("direct_job_distinct_from_mediated") is True,
            "native_result_not_verified")
    require(values["film"] == values["exported_film"]
            and result.get("direct_mediated_same_film_sha256") == refs["exported_film"]["sha256"]
            and parity.get("source_film_sha256") == refs["exported_film"]["sha256"],
            "film_identity_changed")
    binding = values["binding"]
    authoring = values["inspection"].get("project", {}).get("authoring", {})
    require(authoring.get("intent") == values["film"]
            and binding.get("binding_derivation") == "VERIFIED_BY_SELECTED_SDK"
            and binding.get("native_execution") is False
            and binding.get("film_sha256") == refs["film"]["sha256"]
            and binding.get("inspection_sha256") == refs["inspection"]["sha256"],
            "sdk_binding_not_original")
    for name in ("intent_digest", "realization_digest", "projection_digest"):
        require(type(binding.get(name)) is str and HEX.fullmatch(binding[name])
                and authoring.get(name) == binding[name], "sdk_binding_digest_changed")
    pixels = parity.get("pixels", {})
    require(type(pixels.get("frame_count")) is int and pixels["frame_count"] == 60
            and pixels.get("all_frames_decoded_rgba_pixel_identical") is True
            and pixels.get("raw_rgba_pixel_format") == "rgba_1920x1080"
            and type(pixels.get("ordered_rgba_frame_digests_sha256")) is str
            and HEX.fullmatch(pixels["ordered_rgba_frame_digests_sha256"]),
            "all_frame_parity_not_verified")
    geometry = result.get("native_cubic_out_region")
    validate_geometry(geometry)
    artifacts = witness.get("artifacts")
    require(type(artifacts) is dict and set(artifacts) == {"mediated", "direct"}, "artifact_legs")
    require(artifacts["mediated"]["manifest"]["path"] != artifacts["direct"]["manifest"]["path"],
            "artifact_reuse")
    observed_frames = {}
    for leg, artifact in artifacts.items():
        require(type(artifact) is dict and set(artifact) == {"manifest", "frames"}
                and artifact["manifest"].get("root") == "owner"
                and type(artifact["frames"]) is list and len(artifact["frames"]) == 60,
                "artifact_frame_roster")
        manifest_ref = artifact["manifest"]
        require(manifest_ref["path"].endswith("/artifact-manifest.json"),
                "artifact_manifest_role")
        base = manifest_ref["path"][:-len("artifact-manifest.json")]
        require(base.startswith("output/"), "artifact_outside_output")
        manifest = custody().strict(read_reference(roots, manifest_ref))
        require(parity.get("native_manifest_sha256", {}).get(leg) == manifest_ref["sha256"],
                "manifest_parity_not_bound")
        if leg == "mediated":
            require(result.get("artifact_manifest_sha256") == manifest_ref["sha256"]
                    and result.get("artifact_directory") == base[len("output/"):-1],
                    "mediated_artifact_identity_changed")
        plan = manifest.get("plan", {})
        require(all(type(plan.get(k)) is int and plan[k] == v for k, v in
                    {"first_frame": 0, "end_frame_exclusive": 60, "frame_count": 60}.items())
                and type(manifest.get("frames")) is list and len(manifest["frames"]) == 60,
                "manifest_frame_plan")
        hashes = []
        for index, (ref, row) in enumerate(zip(artifact["frames"], manifest["frames"])):
            relative = f"frames/{index:06d}.png"
            require(type(row) is dict and type(row.get("index")) is int and row["index"] == index
                    and row.get("file") == relative and ref.get("root") == "owner"
                    and ref.get("path") == base + relative
                    and row.get("bytes") == ref.get("bytes")
                    and row.get("sha256") == ref.get("sha256"), "manifest_frame_binding")
            raw = read_reference(roots, ref)
            require(raw.startswith(b"\x89PNG\r\n\x1a\n"), "original_png_signature")
            hashes.append(sha(raw))
        observed_frames[leg] = {str(index): hashes[index] for index in FRAMES}
    return {"schema": "motionwright.motion-canvas-cubic-xy-readback/1",
        "status": "VERIFIED_AFTER_HOST_CLOSURE", "context": context, "source": source,
        "project_id": identity["project_id"], "generation": identity["generation"],
        "revision": revision, "host_pid": pid, "native_jobs": jobs,
        "completed_stages": ["original"], "verified_frames_per_leg": 60,
        "verified_original_pngs": 120, "sampled_frame_sha256": observed_frames,
        "original_geometry": geometry,
        "ordered_rgba_frame_digests_sha256": pixels["ordered_rgba_frame_digests_sha256"],
        "same_original_film": True, "sdk_binding_original_verified": True,
        "native_execution_performed": False, "additional_media_decode_performed": False,
        "automatic_retry": False, "formal_acceptance": False, "human_visual_acceptance": False,
        "source_edit_or_reopen_proven_by_this_scope": False}
