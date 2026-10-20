#!/usr/bin/env python3
"""Read back an original external Kdenlive native save without trusting OTIO JSON.

Kdenlive >=25.04 has native OTIO import/export. This observer binds an actual
saved .kdenlive XML project to the original Motionwright portable first-party
media capsule and compares a separately reopened native project snapshot.

THIS IS NOT A GUI/PROCESS RECEIPT. The native editor must be launched, OTIO
imported, saved, closed, reopened in a distinct authorized process, edited,
and OTIO exported by a separately admitted A6 G2 runner. This read-only
observer neither opens an editor, writes a project, edits Motionwright
source, calls Semwright nor grants media rights or human acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from bridge import (
    BridgeRefused, HEX, MAX_JSON, MAX_MEDIA, SCHEMA, _nle_local_media,
    fingerprint, strict_json,
)

MAX_XML_BYTES = 5 * 1024 * 1024
MAX_XML_ELEMENTS = 30000
MAX_NATIVE_IDS = 5000
PROFILE_RATES = (24, 25, 30, 60)


class KdenliveReadbackRejected(ValueError):
    """No admitted source-bound native NLE project/reopen evidence."""


def require(ok: object, reason: str) -> None:
    if not ok:
        raise KdenliveReadbackRejected(reason)


def read_closed_bytes(path: Path, expected_sha: str, maximum: int) -> bytes:
    require(isinstance(expected_sha, str) and HEX.fullmatch(expected_sha),
            "readback_original_SHA256_missing")
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
            and not any(part.is_symlink() for part in path.parents),
            "readback_original_file_not_plain_absolute")
    actual_sha, size = fingerprint(path, maximum)
    require(size <= maximum and actual_sha == expected_sha,
            "readback_original_content_changed_from_expected_SHA")
    data = path.read_bytes()
    require(len(data) == size
            and hashlib.sha256(data).hexdigest() == expected_sha,
            "readback_original_file_mutated_during_read")
    return data


def read_capsule(bundle: Path, manifest_sha: str) -> tuple[Path, dict]:
    require(bundle.is_absolute() and bundle.is_dir() and not bundle.is_symlink()
            and not any(parent.is_symlink() for parent in bundle.parents),
            "readback_original_capsule_not_plain_absolute")
    root = bundle.resolve(strict=True)
    manifest = strict_json(
        read_closed_bytes(root / "bridge-manifest.json", manifest_sha, MAX_JSON),
        "Kdenlive-original-capsule-manifest",
    )
    require(manifest.get("schema") == SCHEMA
            and manifest.get("output_profile") == "portable-media-cut-v2"
            and manifest.get("nle_import_verified") is False
            and manifest.get("publishing_authorized") is False
            and type(manifest.get("bound_media")) is list
            and type(manifest.get("missing_scene_ids")) is list,
            "readback_original_capsule_contract_not_admitted")
    rows = manifest["bound_media"]
    require(1 <= len(rows) <= 32
            and not manifest["missing_scene_ids"]
            and len(rows) == len(manifest.get("scene_order", [])),
            "readback_original_MissingReference_cannot_be_presented_as_NLE_success")
    expected = set()
    for item in rows:
        require(type(item) is dict
                and type(item.get("source_sha256")) is str
                and HEX.fullmatch(item["source_sha256"])
                and item.get("path") == f"media/{item['source_sha256']}.mp4",
                "readback_original_media_declaration_malformed")
        original = root / item["path"]
        require(original.is_file() and not original.is_symlink()
                and fingerprint(original, MAX_MEDIA)[0] == item["source_sha256"],
                "readback_original_H264_media_SHA_missing_or_changed")
        expected.add(item["source_sha256"])
    require(len(expected) >= 1, "readback_no_admitted_original_H264")
    return root, manifest


def safe_mlt_xml(data: bytes) -> tuple[ET.Element, bool]:
    require(0 < len(data) <= MAX_XML_BYTES, "native_Kdenlive_XML_byte_budget")
    upper = data.upper()
    require(b"<!DOCTYPE" not in upper and b"<!ENTITY" not in upper
            and b"XINCLUDE" not in upper
            and re.search(rb"<\s*XI:INCLUDE\b", upper) is None
            and b"<?XML-STYLESHEET" not in upper,
            "native_Kdenlive_XML_external_entity_or_include_forbidden")
    shimmed = False
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        if "unbound prefix" not in str(error) or b"xmlns:kdenlive" in data:
            raise KdenliveReadbackRejected("native_Kdenlive_XML_not_parseable") from error
        # Kdenlive documentation explicitly notes unbound kdenlive: XML
        # attributes in some project generations. In-memory parser shim only;
        # the original bytes and SHA remain immutable and authoritative.
        patched, replacements = re.subn(
            rb"<mlt(?=\s|>)",
            b'<mlt xmlns:kdenlive="http://www.kdenlive.org/project"',
            data, count=1,
        )
        require(replacements == 1,
                "native_Kdenlive_XML_namespace_root_not_unique")
        try:
            root = ET.fromstring(patched)
        except ET.ParseError as invalid:
            raise KdenliveReadbackRejected("native_Kdenlive_XML_unbound_prefix_unrecoverable") from invalid
        shimmed = True
    require(root.tag == "mlt", "native_Kdenlive_XML_root_is_not_MLT")
    require(sum(1 for _ in root.iter()) <= MAX_XML_ELEMENTS,
            "native_Kdenlive_XML_element_count_outside_budget")
    return root, shimmed


def inspect_saved_kdenlive(
    native_path: Path, native_sha: str, bundle: Path, manifest_sha: str,
) -> dict:
    require(native_path.suffix.lower() == ".kdenlive",
            "native_Kdenlive_source_not_a_native_project_extension")
    capsule, manifest = read_capsule(bundle, manifest_sha)
    raw = read_closed_bytes(native_path, native_sha, MAX_XML_BYTES)
    project, shimmed = safe_mlt_xml(raw)
    profiles = project.findall("profile")
    require(len(profiles) == 1, "native_Kdenlive_XML_profile_missing_or_duplicated")
    profile = profiles[0]
    try:
        rate_num, rate_den = (int(profile.attrib[key]) for key in
                              ("frame_rate_num", "frame_rate_den"))
        width, height = (int(profile.attrib[key]) for key in ("width", "height"))
    except (ValueError, TypeError, KeyError) as error:
        raise KdenliveReadbackRejected("native_Kdenlive_project_profile_incomplete") from error
    require(rate_den > 0 and rate_num % rate_den == 0
            and rate_num // rate_den in PROFILE_RATES
            and 16 <= width <= 4096 and 16 <= height <= 4096,
            "native_Kdenlive_native_project_rate_or_geometry_not_supported")

    nodes = list(project.iter())
    ids = [node.attrib["id"] for node in nodes if node.tag in (
        "producer", "chain", "playlist", "tractor",
    ) and "id" in node.attrib]
    require(0 < len(ids) <= MAX_NATIVE_IDS and len(ids) == len(set(ids)),
            "native_Kdenlive_duplicate_or_missing_MLT_component_ID")
    playlists = [node for node in project.iter("playlist")]
    tractors = list(project.iter("tractor"))
    require(any(pl.attrib.get("id") == "main_bin" for pl in playlists)
            and tractors
            and any(prop.attrib.get("name") == "kdenlive:projectTractor"
                    and (prop.text or "").strip() == "1"
                    for tractor in tractors for prop in tractor.iter("property")),
            "native_Kdenlive_project_bin_or_editor_tractor_missing")
    # A SHA-matching MP4 in an unused bin entry is NOT evidence that the
    # editorial tractor actually contains the original footage. Follow the
    # saved MLT playback graph from the uniquely marked project tractor,
    # never the main_bin, to find reachable timeline entries and sources.
    objects = {
        node.attrib["id"]: node
        for node in nodes
        if node.tag in ("producer", "chain", "playlist", "tractor")
        and "id" in node.attrib
    }
    master_tractors = [
        node for node in tractors
        if any(prop.attrib.get("name") == "kdenlive:projectTractor"
               and (prop.text or "").strip() == "1"
               for prop in node.findall("property"))
    ]
    require(len(master_tractors) == 1,
            "native_Kdenlive_project_tractor_not_unique")
    reachable = set()
    timeline_entries = []

    def walk(identifier: str, ancestors: frozenset[str]) -> None:
        require(identifier in objects,
                "native_Kdenlive_playback_graph_has_dangling_producer")
        require(identifier not in ancestors and len(ancestors) < 80,
                "native_Kdenlive_playback_graph_has_cycle_or_excessive_depth")
        if identifier in reachable:
            return
        current = objects[identifier]
        require(current.attrib["id"] != "main_bin",
                "native_Kdenlive_main_bin_is_not_a_playback_timeline")
        reachable.add(identifier)
        children = (
            [item for item in current if item.tag == "track"]
            if current.tag == "tractor" else
            [item for item in current if item.tag == "entry"]
            if current.tag == "playlist" else []
        )
        parent_ids = ancestors | frozenset((identifier,))
        for child in children:
            referenced = child.attrib.get("producer")
            require(type(referenced) is str and 0 < len(referenced) <= 250,
                    "native_Kdenlive_playback_entry_has_no_original_source")
            if current.tag == "playlist":
                timeline_entries.append(child)
            walk(referenced, parent_ids)

    walk(master_tractors[0].attrib["id"], frozenset())
    require(len(timeline_entries) >= len(manifest["scene_order"]),
            "native_Kdenlive_editable_timeline_lost_original_clip_instances")

    declared = {item["source_sha256"] for item in manifest["bound_media"]}
    matched = set()
    observations = []
    for identifier in sorted(reachable):
        source_node = objects[identifier]
        if source_node.tag not in ("producer", "chain"):
            continue
        for element in source_node.findall("property"):
            if element.attrib.get("name") not in (
                "resource", "kdenlive:originalurl",
            ):
                continue
            target = (element.text or "").strip()
            if not target.lower().endswith(".mp4"):
                continue
            try:
                original_path = _nle_local_media(
                    native_path.parent, target, (capsule,),
                )
            except BridgeRefused as error:
                raise KdenliveReadbackRejected(
                    "native_Kdenlive_imported_media_path_outside_original_bundle"
                ) from error
            sha, size = fingerprint(original_path, MAX_MEDIA)
            require(sha in declared,
                    "native_Kdenlive_playback_media_not_immutable_original_SHA")
            matched.add(sha)
            observations.append({
                "original_media_sha256": sha,
                "original_media_bytes": size,
                "source_MLT_component_id": identifier,
                "authorized_bundle_relative":
                    original_path.relative_to(capsule).as_posix(),
            })
    require(matched == declared,
            "native_Kdenlive_reachable_timeline_source_missing_original_media")
    return {
        "schema": "motionwright.native.kdenlive-saved-project-readback/1",
        "native_file_sha256": native_sha,
        "source_capsule_manifest_sha256": manifest_sha,
        "native_MLT_XML_parse": "PASS_SOURCE_ONLY",
        "native_kdenlive_main_bin_and_tractor": True,
        "native_editor_timeline_entries_present": True,
        "observed_editable_timeline_entry_count": len(timeline_entries),
        "source_clip_UUID_and_inpoint_roundtrip_verified_here": False,
        "native_saved_profile_size": [width, height],
        "native_saved_frame_rate": f"{rate_num}/{rate_den}",
        "saved_source_MLT_element_count": len(nodes),
        "distinct_native_MLT_ID_count": len(ids),
        "observed_source_media": observations,
        "exact_original_source_SHA_set_conserved": True,
        "kdenlive_namespace_shim_in_memory_only": shimmed,
        "external_Kdenlive_PID_or_application_process_attested": False,
        "edited_OTIO_cut_accepted": False,
        "reopened_in_distinct_editor_process": False,
        "human_rights_or_visual_approval": False,
    }


def compare_two_native_custody_snapshots(
    original: dict, reopened: dict,
) -> dict:
    require(original["schema"] == reopened["schema"] ==
            "motionwright.native.kdenlive-saved-project-readback/1",
            "native_Kdenlive_saved_and_reopened_receipts_not_comparable")
    for field in (
        "source_capsule_manifest_sha256", "native_saved_profile_size",
        "native_saved_frame_rate", "distinct_native_MLT_ID_count",
        "exact_original_source_SHA_set_conserved",
    ):
        require(original[field] == reopened[field],
                "native_Kdenlive_reopened_project_lost_source_or_frame_clock")
    require({v["original_media_sha256"] for v in original["observed_source_media"]} ==
            {v["original_media_sha256"] for v in reopened["observed_source_media"]},
            "native_Kdenlive_reopened_project_missing_source_media")
    return {
        "schema": "motionwright.native.kdenlive-reopen-comparison/1",
        "original_project_sha256": original["native_file_sha256"],
        "reopened_project_sha256": reopened["native_file_sha256"],
        "reopened_MLT_editorial_source_and_media_match": True,
        "native_saved_project_byte_identical":
            original["native_file_sha256"] == reopened["native_file_sha256"],
        "application_process_reopen_authentication": "REQUIRES_DISTINCT_REAL_KDENLIVE_G2",
        "external_editor_or_human_acceptance": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--original-kdenlive", type=Path, required=True)
    parser.add_argument("--original-sha256", required=True)
    parser.add_argument("--reopened-kdenlive", type=Path, required=True)
    parser.add_argument("--reopened-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    require(args.receipt.is_absolute() and not args.receipt.is_symlink()
            and not args.receipt.exists() and args.receipt.parent.is_dir(),
            "native_Kdenlive_readback_receipt_must_be_new")
    original = inspect_saved_kdenlive(
        args.original_kdenlive, args.original_sha256,
        args.bundle, args.manifest_sha256,
    )
    reopened = inspect_saved_kdenlive(
        args.reopened_kdenlive, args.reopened_sha256,
        args.bundle, args.manifest_sha256,
    )
    result = compare_two_native_custody_snapshots(original, reopened)
    result["original"] = original
    result["reopened"] = reopened
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({
        "native_saved_readback_source_only": "PASS",
        "real_Kdenlive_reopen_process": "REQUIRES_SELECTED_G2",
        "original_sha256": original["native_file_sha256"],
        "reopened_sha256": reopened["native_file_sha256"],
        "human_acceptance": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
