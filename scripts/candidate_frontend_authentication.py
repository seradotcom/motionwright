"""Bind one emitted frontend index to original locked package archives and files.

The pure verifier authenticates observed NPM modules and notices. The build wrapper
also binds observed local source bytes to Git. Virtual origins, CSS/native closure,
platform signing and redistribution permission remain unverified. Archive access is local.
"""
from __future__ import annotations
import argparse, hashlib, json, os, posixpath, re, stat
from pathlib import Path
from npm_archive_authentication import authenticate_npm_sources, archive_members, json_object, relative_path

MAX_EMITTED_ENTRIES = 100000
MAX_EMITTED_DEPTH = 32
MAX_EMITTED_BYTES = 128 * 1024 * 1024

class FrontendEvidenceError(ValueError):
    pass

def require(condition, code):
    if not condition:
        raise FrontendEvidenceError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def row_digest(row, code):
    require(isinstance(row, dict) and type(row.get("bytes")) is int and row["bytes"] >= 0
            and isinstance(row.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), code)
    return {"bytes": row["bytes"], "sha256": row["sha256"]}

def checked_rows(rows, key, code, maximum=100000):
    require(isinstance(rows, list) and len(rows) <= maximum, code)
    result = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get(key), str)
                and row[key] not in result, code)
        result[row[key]] = row
    return result

def authenticate_frontend(index_bytes, lock_bytes, archives, assets):
    require(isinstance(index_bytes, bytes) and len(index_bytes) <= 16*1024*1024,
            "index_budget")
    index = json_object(index_bytes)
    require(index.get("schema") == "motionwright.frontend-notices/1"
            and index.get("completion") == "OBSERVED_RENDERED_JS_MODULE_CLOSURE_ONLY",
            "unsupported_frontend_index")
    require(index.get("full_package_inventory") == "NOT_VERIFIED"
            and index.get("non_chunk_asset_dependency_closure") == "NOT_VERIFIED"
            and index.get("redistribution_permission") == "NOT_VERIFIED",
            "unsupported_completeness_claim")
    binding = index.get("source_binding", {})
    require(binding.get("mode") == "EXACT_HEAD_AND_TRACKED_BLOBS"
            and isinstance(binding.get("source_sha"), str)
            and re.fullmatch(r"[0-9a-f]{40}", binding["source_sha"]), "source_binding_required")
    lock_record = index.get("package_lock", {})
    lock_path = relative_path(lock_record.get("path"))
    require(row_digest(lock_record, "lock_record_invalid") ==
            {"bytes": len(lock_bytes), "sha256": sha(lock_bytes)}, "source_lock_mismatch")
    components = checked_rows(index.get("components"), "id", "component_set_invalid", 1024)
    require(len({c.get("lock_key") for c in components.values()}) == len(components),
            "duplicate_component_lock_identity")
    modules = checked_rows(index.get("modules"), "module_id_sha256", "module_set_invalid")
    outputs = checked_rows(index.get("original_outputs"), "path", "output_set_invalid")
    texts = checked_rows(index.get("generated_notice_assets"), "path", "text_set_invalid")
    require(isinstance(assets, dict) and set(assets) == set(outputs) | set(texts)
            and not set(outputs) & set(texts), "emitted_asset_set_mismatch")
    require(sum(len(raw) for raw in assets.values() if isinstance(raw, bytes)) <= 128*1024*1024,
            "emitted_asset_budget")
    seen_case = set()
    for name, row in {**outputs, **texts}.items():
        relative_path(name)
        require(name.casefold() not in seen_case, "emitted_asset_case_collision")
        seen_case.add(name.casefold())
        raw = assets[name]
        require(isinstance(raw, bytes) and row_digest(row, "output_digest_invalid") ==
                {"bytes": len(raw), "sha256": sha(raw)}, "emitted_asset_bytes_mismatch:"+name)
    selected = set()
    for output in outputs.values():
        require(output.get("kind") in ("chunk", "asset"), "output_kind_invalid")
        if output["kind"] == "chunk":
            chunk = checked_rows(output.get("modules"), "module_id_sha256", "chunk_modules_invalid")
            for key, item in chunk.items():
                require(type(item.get("rollup_rendered_length")) is int and
                        item["rollup_rendered_length"] > 0 and key in modules,
                        "rendered_module_identity_missing")
            selected.update(chunk)
    require(selected == set(modules), "rendered_module_set_mismatch")
    observations = {key: {} for key in components}
    referenced_texts = set()
    prefix = posixpath.dirname(lock_path)
    def package_path(key, part):
        return posixpath.join(prefix, relative_path(key), relative_path(part))
    for module_id, module in modules.items():
        require(re.fullmatch(r"[0-9a-f]{64}", module_id), "module_identity_invalid")
        row_digest(module.get("rollup_transformed_code"), "transformed_code_record_invalid")
        kind = module.get("kind")
        require(kind in ("npm", "local", "virtual"), "module_kind_invalid")
        component_id = module.get("component_id")
        if kind != "npm":
            require(component_id is None and module.get("npm_license_evidence") is False,
                    "non_npm_component_claim")
            continue
        require(component_id in components and module.get("npm_license_evidence") is True,
                "npm_component_identity_missing")
        component = components[component_id]
        member = relative_path(module.get("source_path_in_package"))
        require(module.get("path") == package_path(component.get("lock_key"), member),
                "module_package_path_mismatch")
        descriptor = row_digest(module.get("raw_source"), "raw_source_record_invalid")
        old = observations[component_id].setdefault(member, {"path": member, **descriptor})
        require(old == {"path": member, **descriptor}, "raw_source_observation_conflict")
    require(isinstance(archives, dict) and len(archives) <= 1024
            and sum(len(raw) for raw in archives.values() if isinstance(raw, bytes)) <= 256*1024*1024,
            "archive_set_budget")
    require(set(archives) == {c.get("lock_key") for c in components.values()},
            "archive_component_set_mismatch")
    reports = []
    for component_id, component in sorted(components.items()):
        require(observations[component_id] and component.get("role") == "frontend-emitted",
                "unobserved_frontend_component")
        key = relative_path(component.get("lock_key"))
        metadata = component.get("package_metadata", {})
        require(metadata.get("path") == package_path(key, "package.json"),
                "package_manifest_path_mismatch")
        manifest_observation = {"path": "package.json", **row_digest(metadata, "package_metadata_invalid")}
        require(observations[component_id].setdefault("package.json", manifest_observation) == manifest_observation,
                "manifest_source_observation_conflict")
        notices = checked_rows(component.get("notices"), "source_path_in_package",
                               "component_notices_invalid", 128)
        require(bool(notices), "component_notices_missing")
        for member, notice in notices.items():
            relative_path(member)
            descriptor = row_digest(notice, "notice_digest_invalid")
            asset = "assets/licenses/frontend/texts/" + descriptor["sha256"] + ".txt"
            require(notice.get("asset") == asset and asset in texts and
                    descriptor == row_digest(texts[asset], "notice_asset_invalid"),
                    "notice_asset_binding_mismatch")
            require(notice.get("evidence_kind") in ("license-text", "notice-text"),
                    "notice_evidence_kind_invalid")
            old = observations[component_id].setdefault(member, {"path": member, **descriptor})
            require(old == {"path": member, **descriptor}, "notice_source_observation_conflict")
            referenced_texts.add(asset)
        archive = archives[key]
        require(isinstance(archive, bytes), "archive_bytes_required")
        authenticated = authenticate_npm_sources(lock_bytes, key, archive,
                                                  list(observations[component_id].values()))
        for field in ("name", "version"):
            require(component.get(field) == authenticated[field], "component_identity_mismatch")
        for field in ("resolved", "integrity"):
            require(component.get("source", {}).get(field) == authenticated[field],
                    "component_archive_identity_mismatch")
        _, package_raw = archive_members(archive)
        original_package = json_object(package_raw)
        require(metadata.get("license_declaration") == original_package.get("license"),
                "license_declaration_mismatch")
        reports.append({"component_id": component_id, "role": "frontend-emitted",
                        "source_authentication": authenticated, "notices": list(notices.values()),
                        "license_declaration": original_package.get("license")})
    require(referenced_texts == set(texts), "unreferenced_notice_asset")
    return {"schema": "motionwright.authenticated-frontend-notices/1",
            "source_sha": binding["source_sha"], "frontend_index_sha256": sha(index_bytes),
            "frontend_index_bytes": len(index_bytes), "package_lock_sha256": sha(lock_bytes),
            "components": reports, "emitted_assets": [row for _, row in sorted({**outputs, **texts}.items())],
            "observed_npm_source_and_notice_authentication": "VERIFIED",
            "local_module_source_authentication": "WRAPPER_VALIDATION_REQUIRED",
            "virtual_module_origin_authentication": "NOT_VERIFIED",
            "virtual_module_ids": sorted(k for k, m in modules.items() if m["kind"] == "virtual"),
            "source_binding": "CALLER_MUST_REVALIDATE_EXACT_GIT_SOURCE_AND_TRACKED_INPUTS",
            "full_package_inventory": "NOT_VERIFIED",
            "non_chunk_asset_dependency_closure": "NOT_VERIFIED",
            "redistribution_permission": "NOT_INFERRED"}

def emitted_snapshot(out, index_name):
    """Read a bounded tree, retaining empty directories and the separate index."""
    from candidate_dependency_capture import admitted, stable_read
    entries, files, folded, pending, total = {}, {}, set(), [(out, 0)], 0
    while pending:
        current, depth = pending.pop()
        if current != out:
            admitted(out, current, directory=True)
        with os.scandir(current) as listing:
            for entry in listing:
                require(len(entries) < MAX_EMITTED_ENTRIES, "emitted_entry_count_budget")
                require(depth < MAX_EMITTED_DEPTH, "emitted_path_depth_budget")
                name = relative_path(Path(entry.path).relative_to(out).as_posix())
                require(name not in entries and name.casefold() not in folded,
                        "emitted_entry_collision")
                folded.add(name.casefold())
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = admitted(out, entry.path, directory=True)
                    entries[name] = "directory"
                    pending.append((child, depth + 1))
                else:
                    admitted(out, entry.path)
                    entries[name] = "file"
                    limit = MAX_EMITTED_BYTES - total
                    if name == index_name:
                        limit = min(limit, 16 * 1024 * 1024)
                    require(info.st_size <= limit, "emitted_asset_budget")
                    raw = stable_read(out, name, limit)
                    total += len(raw)
                    require(total <= MAX_EMITTED_BYTES, "emitted_asset_budget")
                    files[name] = raw
    return entries, files

def authenticate_build(*, repo, expected_source, output_directory, archive_root,
                       archive_map, receipt_path):
    from candidate_dependency_capture import admitted, source_snapshot, stable_read, write_new, encoded
    repo = Path(repo).resolve(strict=True)
    before = source_snapshot(repo, expected_source)
    out = admitted(repo, output_directory, directory=True)
    index_name = "assets/licenses/frontend/index.json"
    index_raw = stable_read(out, index_name, 16*1024*1024)
    index = json_object(index_raw)
    require(index.get("source_binding", {}).get("source_sha") == expected_source,
            "frontend_source_sha_mismatch")
    tracked = {r["path"]: r for r in before["files"]}
    captures = checked_rows(index["source_binding"].get("tracked_inputs"), "path", "tracked_inputs_invalid")
    require(captures, "tracked_inputs_missing")
    for name, row in captures.items():
        require(name in tracked and all(row.get(k) == tracked[name][k]
                for k in ("bytes", "sha256", "git_blob")), "tracked_input_bytes_mismatch")
    local_modules = []
    modules = checked_rows(index.get("modules"), "module_id_sha256", "module_set_invalid")
    for key, module in sorted(modules.items()):
        if module.get("kind") != "local":
            continue
        name = relative_path(module.get("path"))
        require(name in captures and name in tracked, "local_module_tracked_input_required")
        raw = module.get("raw_source")
        descriptor = row_digest(raw, "local_module_raw_source_invalid")
        require(descriptor == row_digest(tracked[name], "tracked_source_digest_invalid")
                and raw.get("git_blob") == tracked[name]["git_blob"],
                "local_module_source_mismatch")
        local_modules.append({"module_id_sha256": key, "path": name,
                              **descriptor, "git_blob": tracked[name]["git_blob"]})
    lock_path = relative_path(index["package_lock"]["path"])
    require(lock_path in captures, "tracked_lock_required")
    lock = stable_read(repo, lock_path, 16*1024*1024)
    entries, files = emitted_snapshot(out, index_name)
    require(files.get(index_name) == index_raw, "index_changed_during_authentication")
    assets = {name: raw for name, raw in files.items() if name != index_name}
    require(isinstance(archive_map, dict), "archive_map_required")
    archive_root = Path(archive_root).resolve(strict=True)
    archives = {relative_path(key): stable_read(archive_root, relative_path(path), 32*1024*1024)
                for key, path in archive_map.items()}
    report = authenticate_frontend(index_raw, lock, archives, assets)
    admitted(repo, out, directory=True)
    final_entries, final_files = emitted_snapshot(out, index_name)
    require(final_files.get(index_name) == index_raw, "index_changed_during_authentication")
    require(final_entries == entries, "emitted_asset_set_changed")
    require(final_files == files, "emitted_asset_changed")
    require(source_snapshot(repo, expected_source) == before, "source_changed_during_authentication")
    report["local_module_source_authentication"] = (
        "EXACT_TRACKED_GIT_RAW_SOURCE_BYTES_VERIFIED" if local_modules
        else "NO_OBSERVED_LOCAL_MODULES")
    report["local_modules"] = local_modules
    report["source_binding"] = "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED"
    report["source_tree"] = before["tree"]
    report["limitations"] = ["Readback at bounded phases is not a filesystem lock against concurrent ABA mutation.",
                             "The final platform package needs a detached package-hash inventory."]
    target = Path(receipt_path)
    target = target if target.is_absolute() else repo/target
    admitted(repo, target.parent, directory=True)
    require(target.parent.resolve() != out and out not in target.parents,
            "detached_receipt_required")
    write_new(target, encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("repo", "output-directory", "archive-root", "archive-map", "receipt"):
        parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args(argv)
    from candidate_dependency_capture import stable_read, CaptureError
    try:
        mapping_path = args.archive_map.resolve(strict=True)
        mapping = json_object(stable_read(mapping_path.parent, mapping_path.name, 1024*1024))
        report = authenticate_build(repo=args.repo, expected_source=args.expected_source,
                    output_directory=args.output_directory, archive_root=args.archive_root,
                    archive_map=mapping, receipt_path=args.receipt)
        print(json.dumps({"source":report["source_sha"],"frontend_index_sha256":report["frontend_index_sha256"],
                          "components":len(report["components"]),"full_package_inventory":"NOT_VERIFIED"}))
    except (ValueError, CaptureError, OSError, KeyError, TypeError) as error:
        print("frontend-authentication-failed:"+str(error))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
