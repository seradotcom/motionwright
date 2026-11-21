"""Read-only system file provenance for original AppImage-only members.

A package database path is not proof that a packager copied it. Only equal
rehashed regular host files may be recorded as observed matches. Native
linker closure, license review and formal release gates remain ungranted.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

import candidate_package_inventory as inventory

SCHEMA = "motionwright.linux-appimage-installed-origin-observation/1"
MAX_STATUS = 4 * 1024 * 1024
# Original Ubuntu package lists for azure-cli (3,018,872 bytes) and
# google-cloud-cli (3,487,802 bytes) exceeded the former 2 MiB per-file cap.
# Keep a strict 4 MiB per-file limit and the unchanged 64 MiB aggregate bound.
MAX_MANIFEST = 4 * 1024 * 1024
MAX_MANIFEST_TOTAL = 64 * 1024 * 1024
MAX_PACKAGES = 10000
MAX_FILE_READ = 64 * 1024 * 1024
MAX_SOURCE_READ_TOTAL = 512 * 1024 * 1024
# WebKit's original notice is 273584 bytes; retain it within explicit bounds.
MAX_COPYRIGHT = 512 * 1024
MAX_COPYRIGHT_TOTAL = 16 * 1024 * 1024
MAX_REPORT = 16 * 1024 * 1024
PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?\Z")
VERSION = re.compile(r"[0-9][A-Za-z0-9.+:~\-]*\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")


def need(ok, code):
    if not ok:
        raise ValueError(code)


def digest(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def sealed_inventory(raw, expected):
    need(SHA.fullmatch(expected) is not None and
         0 < len(raw) <= inventory.MAX_REPORT and
         digest(raw)["sha256"] == expected, "origin_external_inventory_seal")
    value = inventory.capture.parse_json(raw)
    inventory.validate(value)
    return value


def unresolved_members(app, deb, source):
    need(SHA1.fullmatch(source) is not None, "origin_expected_source")
    a, d = app["context"], deb["context"]
    need(a["kind"] == "appimage" and d["kind"] == "deb"
         and a["platform"] == d["platform"] == "linux"
         and a["arch"] == d["arch"] == "x86_64"
         and a["source"] == d["source"] == source, "origin_source_context")
    for key in ("source_tree", "run_id", "run_attempt", "candidate_receipt",
                "cargo_index", "artifact_identities_sha256",
                "generated_metadata_sha256"):
        need(a[key] == d[key], "origin_cross_original:" + key)
    need(a["run_attempt"] == 1, "origin_original_attempt")
    for record in (app, deb):
        need(record["custody"]["status"] == "DIRECT_CAPTURED_EXTRACTION"
             and len(record["roots"]) == 1
             and record["roots"][0]["role"] == "payload"
             and record["claims"]["formal_g2"] == "NOT_GRANTED"
             and record["claims"]["formal_g3"] == "NOT_GRANTED"
             and record["claims"]["redistribution_permission"] == "NOT_INFERRED",
             "origin_original_claims_or_custody")
    deb_paths = {r["path"] for r in deb["roots"][0]["members"]}
    app_files = {r["path"]: r for r in app["roots"][0]["members"]
                 if r["kind"] == "file"}
    exclusive = {path for path in app_files if path not in deb_paths}
    unknown = next((g["members"] for g in app["gaps"]
                    if g["code"] == "MEMBER_ORIGIN_UNRESOLVED"), [])
    need(len(unknown) == len(set(unknown)), "origin_unknown_roster_duplicate")
    selected = []
    for member in unknown:
        need(member.startswith("payload:") and member[8:] in app_files,
             "origin_unknown_member_not_regular")
        selected.append(app_files[member[8:]])
    pending_paths = {row["path"] for row in selected}
    return selected, {
        "appimage_exclusive_regular": len(exclusive),
        "original_unresolved_regular": len(selected),
        "exclusive_already_related": len(exclusive - pending_paths),
        "shared_with_deb_unresolved": len(pending_paths - exclusive),
    }


def installed_versions(raw):
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_STATUS,
         "origin_dpkg_status_budget")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("origin_dpkg_status_encoding") from exc
    result, seen = {}, set()
    for line in lines:
        fields = line.split("\t")
        need(len(fields) == 3, "origin_dpkg_status_fields")
        name, version, status = fields
        fields_status = status.split(" ")
        need(PACKAGE.fullmatch(name) is not None
             and len(fields_status) == 3
             and fields_status[0] in ("install", "deinstall", "purge", "unknown")
             and fields_status[1] in ("ok", "reinstreq")
             and fields_status[2] in ("installed", "config-files", "not-installed",
                                      "half-installed", "unpacked", "half-configured",
                                      "triggers-awaited", "triggers-pending"),
             "origin_dpkg_status_entry")
        need(name not in seen, "origin_duplicate_dpkg_package")
        seen.add(name)
        if status == "install ok installed":
            need(VERSION.fullmatch(version) is not None, "origin_installed_version")
            result[name] = version
    need(0 < len(result) <= MAX_PACKAGES, "origin_dpkg_status_roster")
    return result


def host_candidates(member):
    inventory.relative(member)
    if not member.startswith("usr/"):
        return ()
    result = {"/" + member}
    if member.startswith("usr/lib/"):
        tail = member[len("usr/lib/"):]
        if tail.startswith("x86_64-linux-gnu/"):
            result.add("/lib/" + tail)
        elif tail:
            result.add("/usr/lib/x86_64-linux-gnu/" + tail)
            result.add("/lib/x86_64-linux-gnu/" + tail)
    if member == "usr/lib/libpxbackend-1.0.so":
        # Exact libproxy1v5:amd64 package .list original from physical Linux
        # run 44251587423; not a wildcard filename or basename search.
        result.add("/usr/lib/x86_64-linux-gnu/libproxy/libpxbackend-1.0.so")
    return tuple(sorted(result))


def list_paths(raw):
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_MANIFEST,
         "origin_dpkg_manifest_budget")
    try:
        rows = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("origin_dpkg_manifest_encoding") from exc
    seen = set()
    for row in rows:
        if row == "/.":
            continue
        need(row.startswith("/") and not row.startswith("//")
             and "\x00" not in row and "\r" not in row
             and all(p not in ("", ".", "..") for p in row.split("/")[1:]),
             "origin_dpkg_manifest_path")
        need(row not in seen, "origin_dpkg_manifest_duplicate")
        seen.add(row)
    return seen


def candidate_index(manifests, installed, wanted):
    result, consumed = defaultdict(list), 0
    for package, raw in sorted(manifests.items()):
        need(package in installed and PACKAGE.fullmatch(package) is not None,
             "origin_uninstalled_package_manifest")
        consumed += len(raw)
        need(consumed <= MAX_MANIFEST_TOTAL, "origin_manifest_total_budget")
        paths = list_paths(raw)
        for entry in paths & wanted:
            result[entry].append({
                "package": package, "version": installed[package],
                "manifest": digest(raw), "system_path": entry,
            })
    return result


def regular_hash(root, path, limit=MAX_FILE_READ):
    """No following package-file symlinks, redirected roots or directories."""
    need(path.startswith("/") and not path.startswith("//"), "origin_host_path")
    root = Path(root).resolve(strict=True)
    parts = path.lstrip("/").split("/")
    need(all(p not in ("", ".", "..") for p in parts), "origin_host_path")
    current = root
    try:
        for part in parts:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                return None
        info = current.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        return inventory.capture.stable_hash(root, current, limit)
    except (FileNotFoundError, NotADirectoryError):
        return None


def join_origins(extras, installed, manifests, root, *, budget=MAX_SOURCE_READ_TOTAL):
    need(type(budget) is int and 0 < budget <= MAX_SOURCE_READ_TOTAL, "origin_budget")
    wanted = {c for row in extras for c in host_candidates(row["path"])}
    index = candidate_index(manifests, installed, wanted)
    observed, matched, unresolved = {}, [], []
    consumed = 0
    for row in sorted(extras, key=lambda r: r["path"]):
        matches = []
        for candidate in host_candidates(row["path"]):
            for origin in index.get(candidate, ()):
                if candidate not in observed:
                    if consumed + row["bytes"] > budget:
                        observed[candidate] = None
                    else:
                        observed[candidate] = regular_hash(
                            root, candidate, min(MAX_FILE_READ, budget - consumed))
                        consumed += (observed[candidate] or {}).get("bytes", 0)
                if observed[candidate] == {"bytes": row["bytes"], "sha256": row["sha256"]}:
                    matches.append(origin)
        if len(matches) == 1:
            matched.append({
                "member": row["path"], "sha256": row["sha256"],
                "native_format": row["native_format"], "bytes": row["bytes"],
                "origin": matches[0], "status": "INSTALLED_HOST_BYTES_EXACT",
            })
        else:
            unresolved.append({
                "member": row["path"], "native_format": row["native_format"],
                "reason": "MULTIPLE_EXACT_CLAIMS" if matches
                          else "NO_EXACT_REGULAR_INSTALLED_SOURCE",
            })
    need(len(matched) + len(unresolved) == len(extras), "origin_accounting")
    return matched, unresolved


def captured_status():
    need(sys.platform == "linux", "origin_linux_only")
    # Debian's format tokens are not shell-expanded and this is read-only.
    template = "$" + "{binary:Package}\\t" + "$" + "{Version}\\t" + "$" + "{Status}\\n"
    command = ["dpkg-query", "--show", "--showformat=" + template]
    proc = subprocess.run(command, check=False, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=30)
    need(proc.returncode == 0 and len(proc.stdout) <= MAX_STATUS
         and len(proc.stderr) < 65536, "origin_dpkg_query_failed")
    installed_versions(proc.stdout)
    return proc.stdout, {"command": command, "returncode": 0,
                         "stdout": digest(proc.stdout),
                         "stderr": digest(proc.stderr)}


def captured_manifests(info, installed):
    """Observe bounded originals; never treat unexamined .list files as evidence.

    An oversized regular installed package list is explicitly UNOBSERVED.
    Its paths cannot supply a match and its absence never grants closure.
    Redirected/nonregular lists still fail closed; source changes still fail.
    """
    root = Path(info).resolve(strict=True)
    need(root.is_dir(), "origin_dpkg_info_directory")
    manifests, skipped, total = {}, [], 0
    rows = sorted(root.iterdir(), key=lambda p: p.name)
    need(len(rows) <= MAX_PACKAGES * 5, "origin_dpkg_info_roster")
    for path in rows:
        if path.suffix != ".list" or path.name[:-5] not in installed:
            continue
        name = path.name[:-5]
        meta = path.lstat()
        need(stat.S_ISREG(meta.st_mode) and
             not getattr(meta, "st_file_attributes", 0) & 0x400,
             "origin_dpkg_manifest_nonregular")
        need(meta.st_size >= 0, "origin_dpkg_manifest_size")
        if meta.st_size > MAX_MANIFEST or total + meta.st_size > MAX_MANIFEST_TOTAL:
            skipped.append({
                "package": name, "observed_stat_bytes": meta.st_size,
                "reason": ("PER_MANIFEST_BUDGET" if meta.st_size > MAX_MANIFEST
                           else "TOTAL_MANIFEST_BUDGET"),
                "provenance": "NOT_OBSERVED",
            })
            continue
        raw = inventory.capture.stable_read(root, path, MAX_MANIFEST)
        total += len(raw)
        need(total <= MAX_MANIFEST_TOTAL, "origin_dpkg_list_budget")
        manifests[name] = raw
    return manifests, skipped


def regular_host_bytes(root, path, limit):
    """Observe listed copyright bytes, including doc-tree-local symlink aliases.

    The declared package alias remains bound by path/stat before and after.
    A resolved target outside /usr/share/doc, missing, nonregular, or above
    budget produces no license evidence. No notice is treated as legal approval.
    """
    need(path.startswith("/usr/share/doc/") and not path.startswith("//"),
         "origin_notice_path")
    root = Path(root).resolve(strict=True)
    parts = path.lstrip("/").split("/")
    need(parts and all(p not in ("", ".", "..") for p in parts),
         "origin_notice_path")
    alias = root.joinpath(*parts)
    docs = root / "usr/share/doc"
    try:
        before = alias.lstat()
        target = alias.resolve(strict=True)
        if not target.is_relative_to(docs):
            return None
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        raw = inventory.capture.stable_read(root, target, limit)
        after = alias.lstat()
        identity = lambda i: (i.st_dev, i.st_ino, i.st_mode,
                              i.st_size, i.st_mtime_ns, i.st_ctime_ns)
        need(identity(before) == identity(after)
             and target == alias.resolve(strict=True),
             "origin_copyright_alias_changed")
        return raw
    except (FileNotFoundError, NotADirectoryError):
        return None


def _documentation_alias_notice(root, path, manifests, limit, owner_cache):
    """Read a declared doc-directory link only with one original target owner."""
    root = Path(root).resolve(strict=True)
    docs = root / "usr/share/doc"
    alias = root / path.lstrip("/")
    directory = alias.parent
    try:
        before = directory.lstat()
        if not stat.S_ISLNK(before.st_mode):
            return None
        literal = os.readlink(directory)
        need(0 < len(literal) <= 4096, "origin_doc_alias_target_budget")
        target = alias.resolve(strict=True)
        if not target.is_relative_to(docs):
            return None
        host_path = "/" + target.relative_to(root).as_posix()
        if host_path not in owner_cache:
            owners, consumed = [], 0
            need(len(manifests) <= MAX_PACKAGES, "origin_doc_alias_package_budget")
            for package, raw in sorted(manifests.items()):
                consumed += len(raw)
                need(consumed <= MAX_MANIFEST_TOTAL and PACKAGE.fullmatch(package),
                     "origin_doc_alias_manifest_budget")
                if host_path in list_paths(raw):
                    owners.append(package)
            owner_cache[host_path] = owners
        owners = owner_cache[host_path]
        if len(owners) != 1:
            return None
        data = regular_host_bytes(root, host_path, limit)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode,
                                 info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        need(identity(before) == identity(directory.lstat())
             and os.readlink(directory) == literal
             and alias.resolve(strict=True) == target,
             "origin_doc_alias_changed")
        if data is None:
            return None
        owner = owners[0]
        original = manifests[owner]
        filename = "package-lists/" + hashlib.sha256(owner.encode("utf-8")).hexdigest() + ".original"
        return data, {
            "directory": "/" + directory.relative_to(root).as_posix(),
            "target_literal": literal,
            "resolved_copyright": host_path,
            "target_owner": owner,
            "target_manifest": {"file": filename, **digest(original)},
        }, filename, original
    except (FileNotFoundError, NotADirectoryError):
        return None


def original_package_records(matched, manifests, root, *, copyright_budget=MAX_COPYRIGHT_TOTAL):
    """Retain declared notice originals within per-file and aggregate bounds."""
    need(type(copyright_budget) is int and 0 <= copyright_budget <= MAX_COPYRIGHT_TOTAL,
         "origin_copyright_total_budget")
    result, originals, owner_cache = {}, {}, {}
    consumed = 0
    for package in sorted({m["origin"]["package"] for m in matched}):
        need(package in manifests, "origin_package_manifest_missing")
        raw = manifests[package]
        name = hashlib.sha256(package.encode("utf-8")).hexdigest()
        copyright_path = "/usr/share/doc/" + package.split(":", 1)[0] + "/copyright"
        declared = list_paths(raw)
        listed = copyright_path in declared
        limit = min(MAX_COPYRIGHT, copyright_budget - consumed)
        copyright_raw, lineage = None, None
        if limit > 0:
            if listed:
                copyright_raw = regular_host_bytes(root, copyright_path, limit)
            elif copyright_path.rsplit("/", 1)[0] in declared:
                observed = _documentation_alias_notice(
                    root, copyright_path, manifests, limit, owner_cache)
                if observed is not None:
                    copyright_raw, lineage, filename, original = observed
                    originals[filename] = original
        if copyright_raw is not None:
            consumed += len(copyright_raw)
            need(consumed <= copyright_budget, "origin_copyright_total_budget")
            notice = {
                "status": ("DECLARED_DOC_ALIAS_COPYRIGHT_BYTES_OBSERVED" if lineage
                           else "LISTED_REGULAR_COPYRIGHT_BYTES_OBSERVED"),
                "path": copyright_path, **digest(copyright_raw),
                "listed_in_package_manifest": listed,
                "license_review": "NOT_PERFORMED",
            }
            if lineage:
                notice["documentation_alias"] = lineage
        else:
            notice = {
                "status": "NOT_OBSERVED", "listed_in_package_manifest": listed,
                "license_review": "NOT_PERFORMED",
            }
        result[package] = {
            "manifest": {"file": "package-lists/" + name + ".original", **digest(raw)},
            "copyright": notice,
        }
        originals["package-lists/" + name + ".original"] = raw
        if copyright_raw is not None:
            originals["copyright/" + name + ".original"] = copyright_raw
    return result, originals


def observe_elf_build_id_relations(pending, app, installed, manifests,
                                   payload_root, *, system_root=Path("/"),
                                   budget=MAX_SOURCE_READ_TOTAL):
    """Only build-id equivalence to the *current* installed host is observed.

    Every candidate requires a package's original .list entry, stable host
    bytes and exact original extracted ELF bytes. Matching GNU build-id is not
    byte equality, pre-bundle custody, loader closure or legal clearance.
    """
    import linux_elf_build_id as elf

    need(type(budget) is int and 0 < budget <= MAX_SOURCE_READ_TOTAL,
         "origin_elf_budget")
    original_files = {row["path"]: row for row in app["roots"][0]["members"]
                      if row["kind"] == "file"}
    chosen = [row for row in pending if row["native_format"] == "ELF"]
    wanted = {path for row in chosen for path in host_candidates(row["path"])}
    owned = candidate_index(manifests, installed, wanted)
    source_cache, matches, unresolved = {}, [], []
    spent = 0
    payload_root = Path(payload_root).resolve(strict=True)
    for row in sorted(chosen, key=lambda item: item["path"]):
        path = row["path"]
        if not path.startswith("usr/"):
            unresolved.append({"member": path, "reason": "NO_DECLARED_SYSTEM_LIBRARY_PATH"})
            continue
        original = original_files[path]
        if original["bytes"] > elf.MAX_BINARY or spent + original["bytes"] > budget:
            unresolved.append({"member": path, "reason": "ORIGINAL_ELF_BUDGET"})
            continue
        raw = inventory.capture.stable_read(payload_root, payload_root / path,
                                            elf.MAX_BINARY)
        need(digest(raw) == {key: original[key] for key in ("bytes", "sha256")},
             "origin_live_elf_bytes_changed")
        spent += len(raw)
        try:
            target_build_id = elf.gnu_build_id(raw)
        except ValueError:
            target_build_id = None
        if target_build_id is None:
            unresolved.append({"member": path, "reason": "NO_VALID_TARGET_GNU_BUILD_ID"})
            continue
        candidates = {}
        for host in host_candidates(path):
            for package in owned.get(host, ()):
                if host not in source_cache:
                    try:
                        source = elf.observed_library(
                            system_root, host, limit=min(
                                elf.MAX_BINARY, budget - spent))
                    except ValueError:
                        source = None
                    source_cache[host] = source
                    spent += (source or {}).get("bytes", 0)
                source = source_cache[host]
                if source is None or source["gnu_build_id"] != target_build_id:
                    continue
                key = (package["package"], source["resolved_path"],
                       source["sha256"])
                candidates[key] = {"origin": package, "host": source}
        if len(candidates) == 1:
            record = next(iter(candidates.values()))
            matches.append({
                "member": path,
                "gnu_build_id": target_build_id,
                "appimage_elf_bytes": original["bytes"],
                "appimage_elf_sha256": original["sha256"],
                "origin": record["origin"],
                "installed_host": record["host"],
                "status": "SAME_GNU_BUILD_ID_NOT_PROOF_OF_SOURCE_BYTES",
            })
        else:
            unresolved.append({
                "member": path,
                "reason": "AMBIGUOUS_GNU_BUILD_ID_OR_PACKAGE"
                          if candidates else "NO_DECLARED_HOST_GNU_BUILD_ID_MATCH",
            })
    need(len(chosen) == len(matches) + len(unresolved),
         "origin_elf_member_accounting")
    return {
        "scanned_elf": len(chosen),
        "same_gnu_build_id": len(matches),
        "no_match": len(unresolved),
        "host_source_and_target_bytes_read_budget": budget,
        "host_and_appimage_bytes_read": spent,
        "matches": matches,
        "unresolved": unresolved,
        "claims": {
            "build_id_proves_byte_equality": False,
            "prebundle_host_source_capture": "NOT_PERFORMED",
            "packager_transform_chain_custody": "NOT_ATTESTED",
            "redistribution_permission": "NOT_INFERRED",
            "native_linker_closure": "NOT_VERIFIED",
            "G3": "STOP",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("appimage-inventory", "deb-inventory", "live-payload", "output-directory"):
        parser.add_argument("--" + flag, type=Path, required=True)
    for flag in ("appimage-sha256", "deb-sha256", "expected-source"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    need(sys.platform == "linux", "origin_linux_runner_required")
    import ci_linux_dependency_capture as gate
    source = gate.linux_source(os.environ)
    need(source == args.expected_source, "origin_ci_source")
    def load(path, seal):
        path = path.absolute()
        raw = inventory.capture.stable_read(Path(path.anchor), path, inventory.MAX_REPORT)
        return sealed_inventory(raw, seal)
    app = load(args.appimage_inventory, args.appimage_sha256)
    deb = load(args.deb_inventory, args.deb_sha256)
    pending, scope = unresolved_members(app, deb, source)
    need(inventory.observe_tree(args.live_payload, "payload") == app["roots"][0],
         "origin_live_payload_changed")
    status_raw, status_receipt = captured_status()
    installed = installed_versions(status_raw)
    manifests, skipped_manifests = captured_manifests("/var/lib/dpkg/info", installed)
    matched, unresolved = join_origins(pending, installed, manifests, Path("/"))
    if skipped_manifests:
        for row in unresolved:
            if row["reason"] == "NO_EXACT_REGULAR_INSTALLED_SOURCE":
                row["reason"] = "NO_EXACT_OBSERVED_SOURCE_IN_INCOMPLETE_CATALOG"
    packages, package_originals = original_package_records(matched, manifests, Path("/"))
    elf_relations = observe_elf_build_id_relations(
        pending, app, installed, manifests, args.live_payload)
    retained_copyright_bytes = sum(
        item["copyright"].get("bytes", 0) for item in packages.values())
    elf_packages, elf_originals = original_package_records(
        elf_relations["matches"], manifests, Path("/"),
        copyright_budget=MAX_COPYRIGHT_TOTAL - retained_copyright_bytes)
    result = {
        "schema": SCHEMA, "source": source,
        "source_tree": app["context"]["source_tree"],
        "run_id": app["context"]["run_id"], "run_attempt": app["context"]["run_attempt"],
        "original_inventories": {"appimage_sha256": args.appimage_sha256,
                                 "deb_sha256": args.deb_sha256},
        "installed_status": status_receipt,
        "installed_package_manifests_scanned": len(manifests),
        "installed_package_catalog_complete": not skipped_manifests,
        "unobserved_package_manifests": skipped_manifests,
        "unobserved_manifest_count": len(skipped_manifests),
        "original_scope": scope,
        "host_byte_matches": len(matched),
        "unresolved_count": len(unresolved),
        "matched_installed_packages": len(packages),
        "package_originals": packages,
        "matches": matched, "unresolved": unresolved,
        "elf_host_build_id_witness": elf_relations,
        "elf_host_package_originals": elf_packages,
        "elf_host_package_original_folder": "elf-sources",
        "claims": {
            "original_package_file_equal_to_installed_host_file": "SCOPED_OBSERVATION",
            "absence_of_matching_origin_is_proven": False,
            "unread_package_list_is_never_match_credit": True,
            "packager_copy_causality": "NOT_ATTESTED",
            "native_linker_closure": "NOT_VERIFIED",
            "native_license_review": "NOT_PERFORMED",
            "full_package_inventory": "NOT_VERIFIED",
            "redistribution_permission": "NOT_INFERRED",
            "G2": "NOT_GRANTED", "G3": "STOP",
            "human_acceptance": False, "release_authorized": False,
        },
    }
    output = inventory.capture.encoded(result)
    need(len(output) <= MAX_REPORT, "origin_report_budget")
    folder = args.output_directory.absolute()
    need(folder.parent.is_dir() and not os.path.lexists(folder),
         "origin_output_already_exists")
    folder.mkdir(mode=0o700)
    (folder / "package-lists").mkdir(mode=0o700)
    (folder / "copyright").mkdir(mode=0o700)
    (folder / "elf-sources").mkdir(mode=0o700)
    (folder / "elf-sources/package-lists").mkdir(mode=0o700)
    (folder / "elf-sources/copyright").mkdir(mode=0o700)
    inventory.capture.write_new(folder / "status.original", status_raw)
    for name, raw in sorted(package_originals.items()):
        limit = MAX_MANIFEST if name.startswith("package-lists/") else MAX_COPYRIGHT
        inventory.capture.write_new(folder / name, raw)
        need(inventory.capture.stable_read(folder, name, limit) == raw,
             "origin_package_original_readback")
    for name, raw in sorted(elf_originals.items()):
        limit = MAX_MANIFEST if name.startswith("package-lists/") else MAX_COPYRIGHT
        inventory.capture.write_new(folder / "elf-sources" / name, raw)
        need(inventory.capture.stable_read(folder, "elf-sources/" + name, limit) == raw,
             "origin_elf_package_original_readback")
    inventory.capture.write_new(folder / "report.json", output)
    need(inventory.capture.stable_read(folder, "report.json", MAX_REPORT) == output,
         "origin_report_readback")
    print("appimage-system-bytes-exact=" + str(len(matched))
          + " unresolved=" + str(len(unresolved)) + " G3=STOP")


if __name__ == "__main__":
    main()
