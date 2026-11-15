"""Observe complete extracted member sets without granting distribution rights.

The v1 document deliberately separates an observed filesystem namespace from
an archive's complete member namespace, native/linker closure and legal review.
It never turns an extracted directory, a Cargo notice index, or a caller's
expected list into full_package_inventory. Readers recompute all relations.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import posixpath
import re
import stat
import sys

import ci_windows_dependency_capture as shared
import nsis_owned_files as owned

capture = shared.capture
SCHEMA = "motionwright.candidate-package-inventory/1"
MAX_MEMBERS = 4096
MAX_DEPTH = 16
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_REPORT = 32 * 1024 * 1024
MAX_STREAM = 8 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
PROFILES = {
    "appimage": ("linux", {"payload"}),
    "deb": ("linux", {"payload"}),
    "dmg": ("macos", {"volume", "application"}),
    "nsis": ("windows", {"application"}),
}
CLAIMS = {
    "full_package_inventory": "NOT_VERIFIED",
    "container_member_namespace": "NOT_VERIFIED",
    "final_native_linker_incorporation": "NOT_VERIFIED",
    "native_license_review": "NOT_VERIFIED",
    "redistribution_permission": "NOT_INFERRED",
    "human_install_acceptance": "NOT_RUN",
    "native_renderer_execution": "NOT_RUN",
    "formal_g2": "NOT_GRANTED",
    "formal_g3": "NOT_GRANTED",
    "published": False,
}


class InventoryError(ValueError):
    pass


def require(value, code):
    if not value:
        raise InventoryError(code)


def digest(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024
            and "\\" not in value and ":" not in value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value),
            "inventory_member_path_invalid")
    parts = value.split("/")
    require(len(parts) <= MAX_DEPTH and all(p not in ("", ".", "..") for p in parts)
            and all(p == p.rstrip(" .") for p in parts), "inventory_member_path_invalid")
    return value


def fields(value, expected, code):
    require(isinstance(value, dict) and set(value) == set(expected), code)


def file_identity(value):
    fields(value, ("bytes", "sha256"), "inventory_digest_fields")
    require(type(value["bytes"]) is int and 0 <= value["bytes"] <= MAX_FILE
            and isinstance(value["sha256"], str) and SHA.fullmatch(value["sha256"]),
            "inventory_digest_invalid")
    return value


def stat_record(info):
    # Access time is deliberately not a stability predicate: reading can change
    # it. Every comparison is within the same stat API; Windows ctime domains
    # are joined by nsis_owned_files, not equated to one another.
    names = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
             "st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns",
             "st_file_attributes", "st_reparse_tag")
    return {name: getattr(info, name, None) for name in names}


STAT_FIELDS = set(stat_record(os.stat_result((0,) * 10)))


def plain_directory(path):
    path = owned._validated_path(path)
    owned._require_plain_parents(path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not owned._is_link_or_reparse(info)
            and info.st_ino != 0, "inventory_plain_directory_required")
    return path, info


def stable_file(path, limit=MAX_FILE):
    """Hash one retained, non-reparse descriptor; never execute its bytes."""
    path = owned._validated_path(path)
    owned._require_plain_parents(path)
    before = path.lstat()
    owned._require_regular(before, "inventory member")
    require(before.st_nlink == 1 and 0 <= before.st_size <= min(limit, MAX_FILE),
            "inventory_file_budget_or_hardlink")
    fd = owned._open_publication_reader(path, "inventory member")
    try:
        opened = os.fstat(fd)
        change = owned._source_change_time(fd)
        owned._require_path_descriptor_match(opened, before, "inventory member")
        owned._require_unchanged(path.lstat(), before, "inventory member")
        total, header, sha = 0, b"", hashlib.sha256()
        while True:
            block = os.read(fd, min(1024 * 1024, before.st_size - total + 1))
            if not block:
                break
            if len(header) < 8:
                header = (header + block)[:8]
            total += len(block)
            require(total <= before.st_size, "inventory_file_grew")
            sha.update(block)
        require(total == before.st_size, "inventory_file_short_read")
        owned._require_plain_parents(path)
        final = path.lstat()
        descriptor = os.fstat(fd)
        owned._require_unchanged(descriptor, opened, "inventory member")
        owned._require_unchanged(final, before, "inventory member")
        owned._require_path_descriptor_match(descriptor, final, "inventory member")
        require(owned._source_change_time(fd) == change, "inventory_file_change_time")
        require(stat_record(final) == stat_record(before), "inventory_file_metadata_changed")
        native = ("ELF" if header.startswith(b"\x7fELF") else
                  "PE_CANDIDATE" if header.startswith(b"MZ") else
                  "AR_ARCHIVE" if header == b"!<arch>\n" else
                  "MACHO_CANDIDATE" if header[:4] in (
                      b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                      b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                      b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                      b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca") else "NOT_IDENTIFIED")
        return {"bytes": total, "sha256": sha.hexdigest()}, native
    finally:
        os.close(fd)


def _namespace(root):
    root, info = plain_directory(root)
    rows, folded, budget = [], set(), 0
    anchor = stat_record(info)

    def walk(directory, prefix):
        nonlocal budget
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                budget += 1
                require(budget <= MAX_MEMBERS, "inventory_member_budget")
                entries.append(entry.name)
        for name in sorted(entries):
            path = relative(prefix + name)
            require(path.casefold() not in folded, "inventory_case_alias")
            folded.add(path.casefold())
            item = root / Path(*path.split("/"))
            observed = item.lstat()
            require(observed.st_ino != 0 and observed.st_dev == info.st_dev,
                    "inventory_member_identity_or_mount")
            base = {"path": path, "stat": stat_record(observed)}
            if stat.S_ISLNK(observed.st_mode):
                # Symlink literals are observed, never followed. Other reparse
                # types (including junctions) remain refusals.
                target = os.readlink(item)
                require(isinstance(target, str) and 0 < len(target) <= 4096
                        and "\0" not in target, "inventory_link_target")
                rows.append({**base, "kind": "symlink", "target": target})
            elif owned._is_link_or_reparse(observed):
                raise InventoryError("inventory_reparse_member")
            elif stat.S_ISDIR(observed.st_mode):
                rows.append({**base, "kind": "directory"})
                walk(item, path + "/")
            else:
                owned._require_regular(observed, "inventory member")
                require(observed.st_nlink == 1 and 0 <= observed.st_size <= MAX_FILE,
                        "inventory_file_budget_or_hardlink")
                rows.append({**base, "kind": "file"})
    walk(root, "")
    require(stat_record(root.lstat()) == anchor, "inventory_root_changed")
    return anchor, sorted(rows, key=lambda r: r["path"])


def observe_tree(root, role):
    """Enumerate actual names twice, including empty directories and links."""
    root = owned._validated_path(root)
    anchor, before = _namespace(root)
    rows, total = [], 0
    for row in before:
        if row["kind"] == "file":
            identity, native = stable_file(root / Path(*row["path"].split("/")),
                                           MAX_TOTAL - total)
            total += identity["bytes"]
            require(total <= MAX_TOTAL, "inventory_total_budget")
            rows.append({**row, **identity, "native_format": native})
        else:
            rows.append(dict(row))
    require(_namespace(root) == (anchor, before), "inventory_namespace_changed")
    return {"role": role, "root_locator_sha256": capture.sha(str(root).encode()),
            "root_stat": anchor, "members": rows, "member_count": len(rows),
            "file_bytes": total, "members_sha256": capture.sha(capture.encoded(rows))}


def _profile(kind):
    require(kind in PROFILES, "inventory_profile")
    if kind in ("appimage", "deb"):
        import ci_linux_dependency_capture as profile
    elif kind == "dmg":
        import ci_macos_dependency_capture as profile
    else:
        profile = shared
    return profile


def candidate_artifact_roster(candidate, kind):
    """Validate every path-bearing receipt field before constructing paths.

    The existing receipt permits exactly appimage+deb on Linux, one dmg on
    macOS, or one nsis on Windows. Rebuilding the receipt still authenticates
    all other fields and bytes; it cannot be the first path boundary.
    """
    require(kind in PROFILES, "inventory_candidate_kind")
    platform = PROFILES[kind][0]
    require(isinstance(candidate, dict)
            and isinstance(candidate.get("candidate"), dict)
            and candidate["candidate"].get("platform") == platform,
            "inventory_candidate_platform")
    allowed = {"linux": {"appimage", "deb"}, "macos": {"dmg"},
               "windows": {"nsis"}}[platform]
    artifacts = candidate.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == len(allowed),
            "inventory_candidate_artifacts")
    seen = set()
    for row in artifacts:
        require(isinstance(row, dict)
                and isinstance(row.get("bundle_kind"), str)
                and row["bundle_kind"] in allowed
                and row["bundle_kind"] not in seen,
                "inventory_candidate_bundle_roster")
        seen.add(row["bundle_kind"])
        name = relative(row.get("filename"))
        require("/" not in name, "inventory_candidate_filename")
    require(seen == allowed, "inventory_candidate_bundle_roster")
    return artifacts


def authenticate_inputs(repo, source, kind, artifact, receipt):
    """Reuse existing source/Cargo/receipt authority, not caller assertions."""
    repo, _ = plain_directory(repo)
    profile = _profile(kind)
    require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
            and re.fullmatch(r"[1-9][0-9]*", os.environ.get("GITHUB_RUN_ID", "")),
            "inventory_first_run_identity_required")
    raw = capture.stable_read(repo, receipt, 1024 * 1024)
    candidate = capture.parse_json(raw)
    artifacts = candidate_artifact_roster(candidate, kind)
    require(candidate["candidate"].get("source_revision") == source,
            "inventory_candidate_source")
    snapshot, complete, index_raw, binary = profile.cargo_binding(repo, source)
    from package_receipt import make_receipt
    paths = [repo / profile.BINARY.parent / "bundle" / row["bundle_kind"] / row["filename"]
             for row in artifacts]
    rebuilt = make_receipt(
        repo, source, PROFILES[kind][0], candidate["candidate"]["arch"], paths,
        cargo_index=repo / profile.BASE / "resources/cargo/index.json",
        cargo_binary=repo / profile.BINARY, require_cargo_binding=True)
    require(rebuilt == candidate, "inventory_candidate_rebuild_mismatch")
    matches = [r for r in artifacts if r["bundle_kind"] == kind]
    require(len(matches) == 1, "inventory_candidate_kind")
    row = matches[0]
    expected = repo / profile.BINARY.parent / "bundle" / kind / row["filename"]
    artifact = owned._validated_path(artifact)
    require(artifact == owned._validated_path(expected), "inventory_original_package_required")
    observed, _ = stable_file(artifact)
    require(observed == {k: row[k] for k in ("bytes", "sha256")},
            "inventory_package_bytes_changed")
    # This also revalidates the exact Cargo notice subtree and resource mapping.
    profile.resource_mapping(repo, index_raw) if profile is shared else shared.resource_mapping(
        repo, index_raw, base=profile.BASE)
    context = {
        "source": source, "source_tree": snapshot["tree"], "kind": kind,
        "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": 1,
        "platform": PROFILES[kind][0], "arch": candidate["candidate"]["arch"],
        "candidate_receipt": digest(raw),
        "package": {k: row[k] for k in ("filename", "bytes", "sha256")},
        "cargo_index": digest(index_raw),
        "artifact_identities_sha256": complete["artifact_identities_sha256"],
        "generated_metadata_sha256": complete["generated_metadata_sha256"],
    }
    origins = [{"id": "git:" + r["path"], "kind": "TRACKED_SOURCE_BYTES",
                "path": r["path"], "git_blob": r["git_blob"],
                "checkout_transform": r["checkout_transform"],
                "bytes": r["bytes"], "sha256": r["sha256"]} for r in snapshot["files"]]
    origins.append({"id": "cargo:root", "kind": "CAPTURED_CARGO_ROOT",
                    "bytes": binary["bytes"], "sha256": binary["sha256"],
                    "index_sha256": capture.sha(index_raw)})
    index = capture.parse_json(index_raw)
    components = []
    for component in index["components"]:
        notices = sorted({n["sha256"] for n in component.get("notices", [])})
        components.append({"component_id": component["component_id"],
                           "notice_sha256": notices})
    for sha in sorted({s for c in components for s in c["notice_sha256"]}):
        identity, _ = stable_file(repo / profile.BASE / "resources/cargo/texts" / (sha + ".txt"))
        require(identity["sha256"] == sha, "inventory_cargo_notice_changed")
        origins.append({"id": "cargo:notice:" + sha, "kind": "AUTHENTICATED_CARGO_NOTICE_BYTES",
                        **identity, "index_sha256": capture.sha(index_raw)})
    origins.append({"id": "cargo:index", "kind": "AUTHENTICATED_CARGO_INDEX_BYTES",
                    **digest(index_raw), "index_sha256": capture.sha(index_raw)})
    return {"context": context, "origins": sorted(origins, key=lambda x: x["id"]),
            "components": sorted(components, key=lambda x: x["component_id"])}


def _members(roots):
    return {r["role"] + ":" + m["path"]: m for r in roots for m in r["members"]}


def _link_relations(roots):
    result = []
    for root in roots:
        names = {m["path"]: m for m in root["members"]}
        for row in root["members"]:
            if row["kind"] != "symlink":
                continue
            seen, current, target, status = {row["path"]}, row["path"], None, None
            while True:
                literal = names[current]["target"]
                if literal.startswith("/") or "\\" in literal or ":" in literal:
                    status = "EXTERNAL_LITERAL_NOT_FOLLOWED"
                    break
                # Do not normalize through an intermediate symlink or a
                # regular file (e.g. file/../target). A literal can be retained
                # completely while its filesystem resolution remains a gap.
                parts = literal.split("/")
                if any(part in ("", ".", "..") for part in parts):
                    status = "COMPLEX_LITERAL_NOT_RESOLVED"
                    break
                resolved = posixpath.join(posixpath.dirname(current), literal)
                parents = resolved.split("/")[:-1]
                prefixes = ["/".join(parents[:i]) for i in range(1, len(parents) + 1)]
                if any(prefix not in names or names[prefix]["kind"] != "directory"
                       for prefix in prefixes):
                    status = "COMPLEX_LITERAL_NOT_RESOLVED"
                    break
                if resolved in seen:
                    status = "CYCLE_NOT_FOLLOWED"
                    break
                seen.add(resolved)
                if resolved not in names:
                    status = "MISSING_MEMBER_NOT_FOLLOWED"
                    break
                if names[resolved]["kind"] != "symlink":
                    target, status = root["role"] + ":" + resolved, "INTERNAL_MEMBER_LITERAL"
                    break
                current = resolved
            result.append({"member": root["role"] + ":" + row["path"],
                           "target_member": target, "status": status})
    return result


def _format_observation(kind, roots, nsis_listing):
    mapping = {r["role"]: r for r in roots}
    if kind == "deb":
        control = [m for m in mapping["payload"]["members"]
                   if m["path"] == "DEBIAN" or m["path"].startswith("DEBIAN/")]
        has_control = any(m["path"] == "DEBIAN/control" and m["kind"] == "file"
                          for m in control)
        return {"data_namespace": "OBSERVED", "control_namespace":
                "OBSERVED_IN_RAW_EXTRACTION" if has_control else "NOT_OBSERVED",
                "control_member_count": len(control),
                "archive_headers_and_duplicate_entries": "NOT_OBSERVED"}
    if kind == "appimage":
        return {"squashfs_extracted_namespace": "OBSERVED",
                "appimage_runtime_prefix": "NOT_INVENTORIED",
                "squashfs_container_metadata": "NOT_INVENTORIED"}
    if kind == "dmg":
        volume, application = mapping["volume"], mapping["application"]
        applications = [m for m in volume["members"] if m["kind"] == "directory"
                        and "/" not in m["path"] and m["path"].endswith(".app")]
        require(len(applications) == 1, "inventory_dmg_one_application_required")
        source_application = applications[0]
        source_root, copied_root = source_application["stat"], application["root_stat"]
        # A second scan of the original directory is not a copied application.
        # Compare recorded identity and permissions; copying changes inodes/times.
        require((source_root["st_dev"], source_root["st_ino"])
                != (copied_root["st_dev"], copied_root["st_ino"]),
                "inventory_dmg_copy_root_aliases_source")
        require(stat.S_IMODE(source_root["st_mode"]) == stat.S_IMODE(copied_root["st_mode"]),
                "inventory_dmg_copy_root_permissions_differ")
        prefix = source_application["path"] + "/"
        def comparable(row, path):
            # ditto copies bytes, link literals and file permissions; copied
            # inode/timestamps are not source volume identities.
            value = {"path": path, "kind": row["kind"],
                     "mode": stat.S_IMODE(row["stat"]["st_mode"])}
            for key in ("bytes", "sha256", "target", "native_format"):
                if key in row:
                    value[key] = row[key]
            return value
        expected = [comparable(m, m["path"][len(prefix):])
                    for m in volume["members"] if m["path"].startswith(prefix)]
        actual = [comparable(m, m["path"]) for m in application["members"]]
        require(expected == actual, "inventory_dmg_copy_namespace_or_bytes_differ")
        return {"volume_namespace": "OBSERVED", "application_namespace": "OBSERVED",
                "volume_application_directory": source_application["path"],
                "application_copy": "ALL_OBSERVED_MEMBERS_BYTE_AND_LINK_EXACT",
                "application_root_permissions": "MATCH_OBSERVED_SOURCE",
                "application_copy_identity": "DISTINCT_RECORDED_ROOT_IDENTITY",
                "volume_members_outside_application": len(volume["members"]) - len(expected) - 1,
                # A directory scan observes none of the mount lifecycle. There
                # is no admitted Darwin owner or caller flag that can grant it.
                "readonly_mount": "NOT_OBSERVED",
                "mount_device_identity": "NOT_OBSERVED",
                "detach": "NOT_OBSERVED",
                "process_subtree_closure": "NOT_VERIFIED",
                "dmg_container_metadata": "NOT_INVENTORIED"}
    if nsis_listing is not None:
        selected = [r for r in mapping["application"]["members"]
                    if r["path"] == "uninstall.exe" and r["kind"] == "file"]
        require(len(selected) == 1 and {k: selected[0][k] for k in ("bytes", "sha256")}
                == nsis_listing["selected_identity"], "inventory_nsis_selected_identity")
    return {"installed_application_namespace": "OBSERVED",
            "original_setup_listing": nsis_listing,
            "all_listed_member_bytes": "NOT_OBSERVED",
            "installer_program_and_effects": "NOT_INVENTORIED"}


def _derive(context, roots, origins, components, custody, nsis_listing):
    members = _members(roots)
    regular = {key: row for key, row in members.items() if row["kind"] == "file"}
    by_identity = {}
    for origin in origins:
        by_identity.setdefault((origin["bytes"], origin["sha256"]), []).append(origin["id"])
    relations = []
    for key, row in sorted(regular.items()):
        matches = sorted(by_identity.get((row["bytes"], row["sha256"]), []))
        relations.append({"member": key, "origin_ids": matches,
                          "status": "BYTE_EQUAL_TO_AUTHENTICATED_ORIGIN" if matches
                          else "ORIGIN_UNRESOLVED"})
    notice_members = {}
    for key, row in regular.items():
        notice_members.setdefault(row["sha256"], []).append(key)
    component_links = [
        {"component_id": c["component_id"],
         "relation": "CAPTURED_CARGO_COMPONENT_NOT_FINAL_LINK_PROOF",
         "notice_sha256": c["notice_sha256"],
         "notice_members": {sha: sorted(notice_members.get(sha, []))
                            for sha in c["notice_sha256"]},
         "legal_review": "NOT_INFERRED"} for c in components]
    links = _link_relations(roots)
    format_observation = _format_observation(context["kind"], roots, nsis_listing)
    linked_origins = {origin for row in relations for origin in row["origin_ids"]}
    gaps = [{"code": "CONTAINER_NAMESPACE_AND_METADATA_NOT_CLOSED", "members": []},
            {"code": "FINAL_NATIVE_LINKER_GRAPH_NOT_OBSERVED", "members": []},
            {"code": "NATIVE_LICENSE_REVIEW_NOT_PERFORMED", "members": []}]
    if "cargo:root" not in linked_origins:
        gaps.append({"code": "CAPTURED_CARGO_ROOT_BYTES_NOT_OBSERVED", "members": []})
    if "cargo:index" not in linked_origins:
        gaps.append({"code": "AUTHENTICATED_CARGO_INDEX_BYTES_NOT_OBSERVED", "members": []})
    unknown = [r["member"] for r in relations if not r["origin_ids"]]
    if unknown:
        gaps.append({"code": "MEMBER_ORIGIN_UNRESOLVED", "members": unknown})
    native = [key for key, row in regular.items() if row["native_format"] != "NOT_IDENTIFIED"]
    if native:
        gaps.append({"code": "NATIVE_TRANSITIVE_ORIGIN_AND_LICENSE_UNCLOSED",
                     "members": sorted(native)})
    bad_links = [r["member"] for r in links if r["status"] != "INTERNAL_MEMBER_LITERAL"]
    if bad_links:
        gaps.append({"code": "LINK_RESOLUTION_NOT_CLOSED", "members": sorted(bad_links)})
    missing = sorted({sha for c in component_links for sha, names in c["notice_members"].items()
                      if not names})
    if missing:
        gaps.append({"code": "CARGO_NOTICE_BYTES_NOT_IN_OBSERVED_MEMBERS", "members": missing})
    if custody["status"] == "NOT_OBSERVED":
        gaps.append({"code": "EXTRACTION_CUSTODY_NOT_OBSERVED", "members": []})
    if context["kind"] == "deb" and format_observation["control_namespace"] == "NOT_OBSERVED":
        gaps.append({"code": "DEB_CONTROL_NAMESPACE_NOT_OBSERVED", "members": []})
    if context["kind"] == "dmg":
        gaps.extend({"code": code, "members": []} for code in (
            "DMG_READONLY_MOUNT_NOT_OBSERVED", "DMG_MOUNT_DEVICE_IDENTITY_NOT_BOUND",
            "DMG_DETACH_NOT_OBSERVED"))
    if context["kind"] == "nsis":
        gaps.append({"code": "NSIS_LISTED_MEMBER_BYTES_NOT_ALL_OBSERVED", "members": []})
    return {
        "member_relations": relations, "component_notice_relations": component_links,
        "symlink_relations": links, "format_observation": format_observation,
        "closure": {"observed_roots": "COMPLETE_MEMBER_SETS",
                    "all_regular_member_origins": "CLOSED" if not unknown else "GAPS",
                    "cargo_notice_presence": "CLOSED" if not missing else "GAPS",
                    "captured_root_binary": "PRESENT_BYTE_EXACT" if "cargo:root" in linked_origins else "GAP",
                    "container_and_native_relations": "GAPS"},
        "gaps": sorted(gaps, key=lambda x: x["code"]), "claims": dict(CLAIMS),
    }


def compose(inputs, roots, *, custody=None, nsis_listing=None):
    """Build from actual observations; consumer validates all derived joins."""
    roots = sorted(roots, key=lambda x: x["role"])
    identities = {(m["bytes"], m["sha256"]) for m in _members(roots).values()
                  if m["kind"] == "file"}
    origins = [r for r in inputs["origins"] if (r["bytes"], r["sha256"]) in identities]
    custody = custody or {"status": "NOT_OBSERVED", "input": None, "operations": []}
    report = {"schema": SCHEMA, "context": inputs["context"], "roots": roots,
              "origins": origins, "components": inputs["components"], "custody": custody}
    report.update(_derive(inputs["context"], roots, origins, inputs["components"],
                          custody, nsis_listing))
    validate(report)
    return report


def validate(report):
    """Closed structural consumer; authenticity also needs consume() and seal."""
    fields(report, ("schema", "context", "roots", "origins", "components", "custody",
                    "member_relations", "component_notice_relations", "symlink_relations",
                    "format_observation", "closure", "gaps", "claims"),
           "inventory_document_fields")
    require(report["schema"] == SCHEMA, "inventory_schema")
    context = report["context"]
    fields(context, ("source", "source_tree", "kind", "platform", "arch", "run_id",
                     "run_attempt", "candidate_receipt", "package", "cargo_index",
                     "artifact_identities_sha256", "generated_metadata_sha256"),
           "inventory_context_fields")
    require(context["kind"] in PROFILES
            and context["platform"] == PROFILES[context["kind"]][0]
            and context["arch"] in ("x86_64", "amd64", "aarch64", "arm64")
            and type(context["run_attempt"]) is int and context["run_attempt"] == 1
            and isinstance(context["run_id"], str) and re.fullmatch(r"[1-9][0-9]*", context["run_id"])
            and isinstance(context["source"], str) and SHA1.fullmatch(context["source"])
            and isinstance(context["source_tree"], str) and SHA1.fullmatch(context["source_tree"]),
            "inventory_context_identity")
    for key in ("artifact_identities_sha256", "generated_metadata_sha256"):
        require(isinstance(context[key], str) and SHA.fullmatch(context[key]), "inventory_context_digest")
    for key in ("candidate_receipt", "cargo_index"):
        file_identity(context[key])
    package = context["package"]
    fields(package, ("filename", "bytes", "sha256"), "inventory_package_fields")
    require("/" not in relative(package["filename"]), "inventory_package_name")
    file_identity({k: package[k] for k in ("bytes", "sha256")})
    roots = report["roots"]
    require(isinstance(roots, list) and all(isinstance(r, dict) for r in roots)
            and [r.get("role") for r in roots]
            == sorted(PROFILES[context["kind"]][1]), "inventory_root_roles")
    all_bytes, all_members = 0, 0
    for root in roots:
        fields(root, ("role", "root_locator_sha256", "root_stat", "members", "member_count",
                      "file_bytes", "members_sha256"), "inventory_root_fields")
        require(isinstance(root["root_locator_sha256"], str)
                and SHA.fullmatch(root["root_locator_sha256"]), "inventory_root_locator")
        fields(root["root_stat"], STAT_FIELDS, "inventory_stat_fields")
        require(type(root["root_stat"]["st_mode"]) is int
                and stat.S_ISDIR(root["root_stat"]["st_mode"])
                and not (root["root_stat"]["st_file_attributes"] or 0) & 0x400
                and type(root["root_stat"]["st_ino"]) is int
                and root["root_stat"]["st_ino"] != 0, "inventory_root_stat")
        rows = root["members"]
        require(isinstance(rows, list) and len(rows) <= MAX_MEMBERS, "inventory_member_budget")
        names, folded, total = {}, set(), 0
        for row in rows:
            require(isinstance(row, dict) and row.get("kind") in ("file", "directory", "symlink"),
                    "inventory_member_kind")
            keys = {"path", "kind", "stat"}
            if row["kind"] == "file":
                keys |= {"bytes", "sha256", "native_format"}
            elif row["kind"] == "symlink":
                keys.add("target")
            fields(row, keys, "inventory_member_fields")
            name = relative(row["path"])
            require(name.casefold() not in folded, "inventory_duplicate_or_case_alias")
            folded.add(name.casefold())
            fields(row["stat"], STAT_FIELDS, "inventory_stat_fields")
            info = row["stat"]
            required_stat = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
                             "st_size", "st_mtime_ns", "st_ctime_ns")
            require(all(type(info[k]) is int for k in required_stat)
                    and all(v is None or type(v) is int for v in info.values()),
                    "inventory_stat_types")
            require(type(info["st_ino"]) is int and info["st_ino"] != 0
                    and type(info["st_mode"]) is int
                    and info["st_dev"] == root["root_stat"]["st_dev"], "inventory_stat_identity")
            is_kind = {"file": stat.S_ISREG, "directory": stat.S_ISDIR,
                       "symlink": stat.S_ISLNK}[row["kind"]]
            require(is_kind(info["st_mode"]), "inventory_stat_kind")
            if row["kind"] != "symlink":
                require(not (info["st_file_attributes"] or 0) & 0x400, "inventory_reparse_member")
            if row["kind"] == "file":
                file_identity({k: row[k] for k in ("bytes", "sha256")})
                require(row["bytes"] == info["st_size"] and info["st_nlink"] == 1
                        and row["native_format"] in ("ELF", "PE_CANDIDATE", "AR_ARCHIVE",
                                                     "MACHO_CANDIDATE", "NOT_IDENTIFIED"),
                        "inventory_file_metadata")
                total += row["bytes"]
            elif row["kind"] == "symlink":
                require(isinstance(row["target"], str) and 0 < len(row["target"]) <= 4096
                        and "\0" not in row["target"], "inventory_link_target")
            names[name] = row
        require(list(names) == sorted(names), "inventory_member_order")
        for name in names:
            parent = posixpath.dirname(name)
            require(not parent or (parent in names and names[parent]["kind"] == "directory"),
                    "inventory_parent_missing_or_not_directory")
        require(type(root["member_count"]) is int and root["member_count"] == len(rows)
                and type(root["file_bytes"]) is int and root["file_bytes"] == total
                and root["members_sha256"] == capture.sha(capture.encoded(rows)),
                "inventory_roster_digest_or_count")
        all_bytes += total
        all_members += len(rows)
    require(all_bytes <= MAX_TOTAL and all_members <= MAX_MEMBERS, "inventory_aggregate_budget")
    origins = report["origins"]
    require(isinstance(origins, list) and len(origins) <= MAX_MEMBERS * 4, "inventory_origin_budget")
    origin_ids = []
    for origin in origins:
        require(isinstance(origin, dict), "inventory_origin_fields")
        kind = origin.get("kind")
        common = {"id", "kind", "bytes", "sha256"}
        if kind == "TRACKED_SOURCE_BYTES":
            fields(origin, common | {"path", "git_blob", "checkout_transform"}, "inventory_origin_fields")
            relative(origin["path"])
            require(origin["id"] == "git:" + origin["path"]
                    and SHA1.fullmatch(origin["git_blob"])
                    and origin["checkout_transform"] in ("EXACT", "EXACT_LF_TO_CRLF"),
                    "inventory_git_origin")
        else:
            require(kind in ("CAPTURED_CARGO_ROOT", "AUTHENTICATED_CARGO_NOTICE_BYTES",
                             "AUTHENTICATED_CARGO_INDEX_BYTES"), "inventory_origin_kind")
            fields(origin, common | {"index_sha256"}, "inventory_origin_fields")
            expected = {"CAPTURED_CARGO_ROOT": "cargo:root",
                        "AUTHENTICATED_CARGO_INDEX_BYTES": "cargo:index",
                        "AUTHENTICATED_CARGO_NOTICE_BYTES": "cargo:notice:" + origin["sha256"]}[kind]
            require(origin["id"] == expected and origin["index_sha256"] == context["cargo_index"]["sha256"],
                    "inventory_cargo_origin")
        file_identity({k: origin[k] for k in ("bytes", "sha256")})
        origin_ids.append(origin["id"])
    require(origin_ids == sorted(set(origin_ids)), "inventory_origin_roster")
    components = report["components"]
    require(isinstance(components, list) and len(components) <= MAX_MEMBERS, "inventory_component_budget")
    component_ids = []
    for row in components:
        fields(row, ("component_id", "notice_sha256"), "inventory_component_fields")
        require(isinstance(row["component_id"], str) and 0 < len(row["component_id"]) <= 2048
                and isinstance(row["notice_sha256"], list)
                and row["notice_sha256"] == sorted(set(row["notice_sha256"]))
                and all(isinstance(s, str) and SHA.fullmatch(s) for s in row["notice_sha256"]),
                "inventory_component_identity")
        component_ids.append(row["component_id"])
    require(component_ids == sorted(set(component_ids)), "inventory_component_roster")
    custody = report["custody"]
    fields(custody, ("status", "input", "operations"), "inventory_custody_fields")
    require(custody["status"] in ("NOT_OBSERVED", "DIRECT_CAPTURED_EXTRACTION"),
            "inventory_custody_status")
    if custody["status"] == "NOT_OBSERVED":
        require(custody["input"] is None and custody["operations"] == [], "inventory_custody_unobserved")
    else:
        require(context["kind"] != "dmg", "inventory_dmg_mount_custody_unavailable")
        file_identity(custody["input"])
        require(isinstance(custody["operations"], list) and 1 <= len(custody["operations"]) <= 3,
                "inventory_operation_count")
        labels = []
        for op in custody["operations"]:
            fields(op, ("label", "request", "completion", "stdout", "stderr", "subtree",
                        "binding", "closure"), "inventory_operation_fields")
            require(op["label"] == "extract", "inventory_operation_label")
            for key in ("request", "completion", "stdout", "stderr", "subtree", "binding", "closure"):
                file_identity(op[key])
            labels.append(op["label"])
        require(labels == ["extract"] and context["kind"] in ("appimage", "deb"),
                "inventory_operation_order")
    require(isinstance(report["format_observation"], dict), "inventory_format_fields")
    nsis_listing = report["format_observation"].get("original_setup_listing") if context["kind"] == "nsis" else None
    if nsis_listing is not None:
        fields(nsis_listing, ("listing", "members", "selected_member", "selected_identity",
                             "static_reference_sha256"), "inventory_nsis_listing_fields")
        file_identity(nsis_listing["listing"])
        file_identity(nsis_listing["selected_identity"])
        require(isinstance(nsis_listing["static_reference_sha256"], str)
                and SHA.fullmatch(nsis_listing["static_reference_sha256"]),
                "inventory_nsis_reference_digest")
        require(isinstance(nsis_listing["members"], list)
                and 1 <= len(nsis_listing["members"]) <= MAX_MEMBERS, "inventory_nsis_members")
        selected = nsis_listing["selected_member"]
        fields(selected, ("path", "listed_bytes"), "inventory_nsis_selected_fields")
        canonical_names = []
        for row in nsis_listing["members"]:
            fields(row, ("path", "canonical_path", "kind", "listed_bytes", "properties"),
                   "inventory_nsis_member_fields")
            relative(row["canonical_path"])
            require(isinstance(row["path"], str) and row["path"].replace("\\", "/") == row["canonical_path"]
                    and row["kind"] in ("directory", "file")
                    and (row["listed_bytes"] is None or type(row["listed_bytes"]) is int
                         and 0 <= row["listed_bytes"] <= MAX_FILE)
                    and isinstance(row["properties"], dict)
                    and row["properties"].get("Path") == row["path"]
                    and all(isinstance(k, str) and isinstance(v, str)
                            for k, v in row["properties"].items()), "inventory_nsis_member_identity")
            canonical_names.append(row["canonical_path"])
        require(canonical_names == sorted(set(canonical_names))
                and len({name.casefold() for name in canonical_names}) == len(canonical_names),
                "inventory_nsis_member_roster")
        chosen = [r for r in nsis_listing["members"] if r["path"] == selected["path"]]
        require(len(chosen) == 1 and chosen[0]["kind"] == "file"
                and chosen[0]["listed_bytes"] == selected["listed_bytes"]
                and (selected["listed_bytes"] is None
                     or selected["listed_bytes"] == nsis_listing["selected_identity"]["bytes"]),
                "inventory_nsis_selected_identity")
    identities = {(m["bytes"], m["sha256"]) for m in _members(roots).values()
                  if m["kind"] == "file"}
    require(all((o["bytes"], o["sha256"]) in identities for o in origins),
            "inventory_unused_origin")
    derived = _derive(context, roots, origins, components, custody, nsis_listing)
    require(all(report[k] == v for k, v in derived.items()), "inventory_relations_or_claims_changed")
    require(len(capture.encoded(report)) <= MAX_REPORT, "inventory_report_budget")
    return report


def _layout(kind, artifact, work):
    artifact, work = str(artifact), Path(work)
    if kind == "appimage":
        return {"payload": work / "squashfs-root"}, [
            ("extract", [artifact, "--appimage-extract"], work)]
    if kind == "deb":
        # One raw extraction contains both data and control. --extract alone
        # cannot establish the DEBIAN/control observation.
        return {"payload": work / "payload"}, [
            ("extract", ["/usr/bin/dpkg-deb", "--raw-extract", artifact, str(work / "payload")], work)]
    # Do not expose a dormant command recipe as an extraction fallback. A
    # Darwin owner must establish real subtree and mount authority first.
    require(kind != "dmg", "inventory_full_process_closure_unavailable")
    raise InventoryError("inventory_no_nsis_install_authority")


def _tool_identities(commands):
    return {tool: stable_file(Path(tool))[0] for tool in sorted({row[1][0] for row in commands})}


def _operation_summary(directory, commands):
    import candidate_package_extraction as extraction
    rows = []
    for label, command, cwd in commands:
        originals = {key: capture.stable_read(
            directory, label + "." + suffix, MAX_STREAM if key in ("stdout", "stderr")
            else extraction.core.STATUS_LIMIT if key == "subtree" else extraction.MAX_RECORD)
                     for key, suffix in extraction.SUFFIXES.items()}
        expected = extraction.request(command, cwd, label)
        extraction.validate_originals(originals, expected, extraction.sources())
        rows.append({"label": label, **{key: digest(raw) for key, raw in originals.items()}})
    return rows


def _write_report(directory, report):
    directory, _ = plain_directory(directory)
    raw = capture.encoded(validate(report))
    require(len(raw) <= MAX_REPORT, "inventory_report_budget")
    capture.write_new(directory / "inventory.json", raw)
    return digest(raw)


def observe_existing(*, repo, source, kind, artifact, receipt, roots, directory,
                     nsis_listing=None):
    """Read existing roots. Missing extraction custody remains an explicit gap."""
    repo = owned._validated_path(repo)
    artifact = owned._validated_path(artifact)
    before = authenticate_inputs(repo, source, kind, artifact, receipt)
    selected_output = owned._validated_path(directory)
    require(all(not selected_output.is_relative_to(owned._validated_path(path))
                and not owned._validated_path(path).is_relative_to(selected_output)
                for path in roots.values()), "inventory_output_overlaps_observation")
    require(set(roots) == PROFILES[kind][1], "inventory_root_roles")
    directory = capture.new_directory(repo, directory)
    observed = [observe_tree(path, role) for role, path in sorted(roots.items())]
    require(authenticate_inputs(repo, source, kind, artifact, receipt) == before,
            "inventory_inputs_changed")
    return _write_report(directory, compose(before, observed, nsis_listing=nsis_listing))


def _close_package_reader(fd, primary_error):
    """Keep a pending owner/retained exception if this one close is uncertain."""
    try:
        os.close(fd)
    except OSError as closing:
        if primary_error is None:
            raise
        primary_error.add_note("inventory_package_descriptor_close_failed:" + type(closing).__name__)


def extract_and_observe(*, repo, source, kind, artifact, receipt, work, directory, environment=None):
    """Replace, never repeat, one existing owned Linux extraction.

    This function runs package extraction tools only when explicitly invoked by
    the existing CI scope. It builds nothing, launches no installed application
    or Native host, and performs no download. Originals survive every failure.
    """
    # No equivalent kernel subtree closure is admitted for macOS here.
    # Keep its existing workflow; never create a new inventory extraction with
    # only hdiutil/ditto exit statuses or a caller-supplied "closed" boolean.
    require(kind in ("appimage", "deb"), "inventory_full_process_closure_unavailable")
    import candidate_package_extraction as extraction
    profile = _profile(kind)
    admitted_source = profile.linux_source(os.environ)
    require(admitted_source == source
            and Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == repo,
            "inventory_extraction_scope_mismatch")
    extraction.require_supported()
    repo = owned._validated_path(repo)
    artifact = owned._validated_path(artifact)
    before = authenticate_inputs(repo, source, kind, artifact, receipt)
    work = owned._validated_path(work)
    owned._require_plain_parents(work)
    require(not os.path.lexists(work) and not work.is_relative_to(repo)
            and not repo.is_relative_to(work), "inventory_fresh_external_work_required")
    directory = capture.new_directory(repo, directory)
    work.mkdir()
    plain_directory(work)
    roots, commands = _layout(kind, artifact, work)
    tools = _tool_identities(commands)
    request = {"schema": SCHEMA + "/extraction-input", "context": before["context"],
               "work": str(work), "roots": {k: str(v) for k, v in roots.items()},
               "tools": tools, "fresh_work_created": True}
    request_raw = capture.encoded(request)
    capture.write_new(directory / "input.json", request_raw)
    package_path = owned._validated_path(artifact)
    package_stat = package_path.lstat()
    package_fd = owned._open_publication_reader(package_path, "candidate package")
    try:
        opened = os.fstat(package_fd)
        change = owned._source_change_time(package_fd)
        owned._require_path_descriptor_match(opened, package_stat, "candidate package")
        label, argv, cwd = commands[0]
        extraction.run_owned(argv, cwd, directory, label, env=environment,
                             stream_limit=MAX_STREAM, timeout=90)
        observed = [observe_tree(path, role) for role, path in sorted(roots.items())]
        require(_tool_identities(commands) == tools, "inventory_extraction_tool_changed")
        require(authenticate_inputs(repo, source, kind, artifact, receipt) == before,
                "inventory_inputs_changed")
        owned._require_plain_parents(package_path)
        owned._require_unchanged(os.fstat(package_fd), opened, "candidate package")
        owned._require_unchanged(package_path.lstat(), package_stat, "candidate package")
        require(owned._source_change_time(package_fd) == change
                and stat_record(package_path.lstat()) == stat_record(package_stat),
                "inventory_package_changed_during_extraction")
        custody = {"status": "DIRECT_CAPTURED_EXTRACTION", "input": digest(request_raw),
                   "operations": _operation_summary(directory, commands)}
        return _write_report(directory, compose(before, observed, custody=custody))
    finally:
        _close_package_reader(package_fd, sys.exception())


def consume(*, repo, source, kind, artifact, receipt, directory, expected_sha256,
            roots=None, nsis_listing=None):
    """Authenticate a sealed original; optionally reread live observed roots.

    A detached DMG volume is never silently recreated. Without roots, consumption
    authenticates retained observations and original streams, not a fresh scan.
    """
    require(isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256),
            "inventory_external_seal_required")
    directory, _ = plain_directory(directory)
    raw = capture.stable_read(directory, "inventory.json", MAX_REPORT)
    require(capture.sha(raw) == expected_sha256, "inventory_external_seal_changed")
    report = validate(capture.parse_json(raw))
    repo = owned._validated_path(repo)
    artifact = owned._validated_path(artifact)
    before = authenticate_inputs(repo, source, kind, artifact, receipt)
    require(report["context"] == before["context"], "inventory_context_changed")
    observed = report["roots"]
    if roots is not None:
        require(set(roots) == PROFILES[kind][1], "inventory_root_roles")
        require([observe_tree(path, role) for role, path in sorted(roots.items())] == observed,
                "inventory_live_member_set_changed")
    custody = report["custody"]
    allowed = {"inventory.json"}
    if custody["status"] == "DIRECT_CAPTURED_EXTRACTION":
        input_raw = capture.stable_read(directory, "input.json", 1024 * 1024)
        require(digest(input_raw) == custody["input"], "inventory_extraction_input_changed")
        request = capture.parse_json(input_raw)
        fields(request, ("schema", "context", "work", "roots", "tools", "fresh_work_created"),
               "inventory_extraction_input_fields")
        require(request["schema"] == SCHEMA + "/extraction-input"
                and request["context"] == before["context"] and request["fresh_work_created"] is True,
                "inventory_extraction_input_context")
        work = owned._validated_path(request["work"])
        expected_roots, commands = _layout(kind, artifact, work)
        require(request["roots"] == {k: str(v) for k, v in expected_roots.items()}
                and all(r["root_locator_sha256"] == capture.sha(
                    str(expected_roots[r["role"]]).encode()) for r in observed),
                "inventory_extraction_root_binding")
        require(_tool_identities(commands) == request["tools"], "inventory_extraction_tool_changed")
        require(_operation_summary(directory, commands) == custody["operations"],
                "inventory_extraction_originals_changed")
        allowed.add("input.json")
        allowed |= {label + "." + suffix for label, _, _ in commands
                    for suffix in ("request.json", "completion.json", "stdout", "stderr",
                                   "subtree.original", "binding.json", "closure.json")}
    require({p.name for p in directory.iterdir()} == allowed, "inventory_original_roster")
    expected = compose(before, observed, custody=custody, nsis_listing=nsis_listing)
    require(expected == report, "inventory_authenticated_relations_changed")
    require(authenticate_inputs(repo, source, kind, artifact, receipt) == before
            and capture.stable_read(directory, "inventory.json", MAX_REPORT) == raw,
            "inventory_inputs_changed")
    return {"schema": SCHEMA + "/reference", **digest(raw), "kind": kind,
            "observation": "COMPLETE_RECORDED_ROOT_MEMBER_SETS",
            "readback": "LIVE_ROOTS_REVALIDATED" if roots is not None else "RETAINED_ORIGINALS_REVALIDATED",
            "custody": custody["status"], "member_count": sum(r["member_count"] for r in observed),
            "file_bytes": sum(r["file_bytes"] for r in observed),
            "gap_codes": [g["code"] for g in report["gaps"]],
            "full_package_inventory": "NOT_VERIFIED",
            "redistribution_permission": "NOT_INFERRED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("extract", "observe", "verify"))
    parser.add_argument("--kind", choices=tuple(PROFILES), required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--root", action="append", default=[], help="role=absolute_path")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    try:
        repo = Path(__file__).resolve().parents[1]
        profile = _profile(args.kind)
        source = (profile.linux_source(os.environ) if args.kind in ("appimage", "deb")
                  else profile.macos_source(os.environ) if args.kind == "dmg"
                  else profile.internal_source(os.environ))
        require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == repo,
                "inventory_checkout_mismatch")
        roots = {}
        for item in args.root:
            role, separator, value = item.partition("=")
            require(separator and role not in roots and Path(value).is_absolute(),
                    "inventory_root_argument")
            roots[role] = Path(value)
        common = dict(repo=repo, source=source, kind=args.kind, artifact=args.artifact,
                      receipt=args.candidate_receipt, directory=args.directory)
        if args.action == "extract":
            require(args.work is not None and not roots and args.expected_sha256 is None,
                    "inventory_extract_arguments")
            result = extract_and_observe(**common, work=args.work, environment=dict(os.environ))
        elif args.action == "observe":
            require(args.work is None and args.expected_sha256 is None, "inventory_observe_arguments")
            result = observe_existing(**common, roots=roots)
        else:
            require(args.work is None, "inventory_verify_arguments")
            result = consume(**common, expected_sha256=args.expected_sha256, roots=roots or None)
        print(capture.encoded(result).decode(), end="")
        return 0
    except (InventoryError, capture.CaptureError, owned.CaptureError,
            OSError, ValueError, KeyError, TypeError) as error:
        detail = str(error)
        code = detail if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", detail) else type(error).__name__
        print("candidate-package-inventory-failed:" + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
