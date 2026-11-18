"""Compose real Linux Cargo capture with exact DEB/AppImage inspection.

Only the existing isolated bundle and exact installed first-use CI scopes are admitted. This does not
grant redistribution, full dependency closure, Native execution or acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

import ci_windows_dependency_capture as shared
import linux_tauri_bundle_tool_pins as toolpins

ROOT = Path(__file__).resolve().parents[1]
capture = shared.capture
require = shared.require
TARGET = "x86_64-unknown-linux-gnu"
BASE = Path("ci-evidence/linux-cargo")
BINARY = Path("target/linux-captured") / TARGET / "release/motionwright-desktop"
PROFILE = "motionwright.linux-cargo-generated-metadata/1"
RUNPATH = "$ORIGIN/../lib"
RUSTFLAGS = "-C link-arg=-Wl,--enable-new-dtags,-rpath," + RUNPATH
PATCHELF = "/usr/bin/patchelf"
LAYOUT = {"base": BASE, "target": TARGET, "binary": BINARY, "profile_schema": PROFILE}

# These sidecars observe available inputs, never which JavaScript/native module ran.
CLI_INPUTS = BASE / "tauri-inputs"
CLI_MAX_RETAINED = 4 * 1024 * 1024  # Per phase, original small files only.
CLI_MAX_HASHED = 256 * 1024 * 1024  # Per phase, accepted hash-only bytes.
CLI_MAX_RECORD = 1024 * 1024
CLI_MAX_PATH = 4096
CLI_MAX_LINKS = 16
CLI_MAX_PARTS = 128
CLI_MAX_PATH_ENTRIES = 64
CLI_CLAIMS = {
    "native_module_loaded": "NOT_OBSERVED",
    "generator_source_authenticated": "NOT_VERIFIED",
    "archive_authentication": "NOT_PERFORMED",
}


def _cli_stat(info):
    """Keep original fields; namespace-only directory fields are compared separately."""
    return {key: getattr(info, key, None) for key in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
        "st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns",
        "st_file_attributes", "st_reparse_tag")}


def _cli_problem(error):
    return {"type": type(error).__name__, "message": str(error)[:512],
            "notes": [str(note)[:512] for note in getattr(error, "__notes__", [])[:4]]}


def _cli_plain_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and
            not getattr(info, "st_file_attributes", 0) & capture.REPARSE,
            "tauri_input_anchor_redirected")
    return info


def _cli_lexical(anchor, value):
    text = os.fspath(value)
    require(isinstance(text, str) and 0 < len(text) <= CLI_MAX_PATH and "\0" not in text,
            "tauri_input_path_budget")
    path = Path(text)
    require(".." not in path.parts, "tauri_input_ambiguous_parent_component")
    candidate = path if path.is_absolute() else anchor / path
    try:
        relative = candidate.relative_to(anchor)
    except ValueError as error:
        raise ValueError("tauri_input_link_escape") from error
    require(candidate != anchor and len(relative.parts) <= CLI_MAX_PARTS,
            "tauri_input_link_escape")
    return candidate


def _cli_target(anchor, parent, literal):
    """Allow leading parent components only from an already observed physical parent."""
    require(isinstance(literal, str) and 0 < len(literal) <= CLI_MAX_PATH
            and "\0" not in literal, "tauri_input_link_target_budget")
    target = Path(literal)
    if target.is_absolute():
        return _cli_lexical(anchor, target)
    current, descended = parent, False
    for part in target.parts:
        if part == "..":
            require(not descended, "tauri_input_ambiguous_parent_component")
            require(current != anchor, "tauri_input_link_escape")
            current = current.parent
        elif part != ".":
            descended = True
            current /= part
    return _cli_lexical(anchor, current)


def _cli_directory_trace(directory, record):
    require(directory.is_absolute() and len(directory.parts) <= CLI_MAX_PARTS,
            "tauri_input_anchor_invalid_or_budget")
    record.update(requested=str(directory), nodes=[])
    current = Path(directory.anchor)
    for part in ("", *directory.parts[1:]):
        current = current / part if part else current
        info = current.lstat()
        item = {"path": str(current), "kind": "directory", "stat": _cli_stat(info)}
        record["nodes"].append(item)
        if stat.S_ISLNK(info.st_mode):
            item["kind"] = "link"
            item["target_literal"] = os.readlink(current)
            item["stat_after_readlink"] = _cli_stat(current.lstat())
            require(item["stat"] == item["stat_after_readlink"], "tauri_input_anchor_changed")
        require(stat.S_ISDIR(info.st_mode) and
                not getattr(info, "st_file_attributes", 0) & capture.REPARSE,
                "tauri_input_anchor_redirected_or_not_directory")


def _cli_trace(anchor, requested, record, *, links):
    """Trace literal links; never resolve them into a new admission anchor."""
    require(anchor.is_absolute() and anchor != Path(anchor.anchor), "tauri_input_anchor_invalid")
    _cli_directory_trace(anchor, record)
    record.update(anchor=str(anchor), requested=str(requested))
    candidate = _cli_lexical(anchor, requested)
    pending, current, seen, count = list(candidate.relative_to(anchor).parts), anchor, set(), len(record["nodes"])
    while pending:
        count += 1
        require(count <= CLI_MAX_PARTS, "tauri_input_walk_budget")
        current /= pending.pop(0)
        info = current.lstat()
        item = {"path": str(current), "stat": _cli_stat(info)}
        record["nodes"].append(item)
        if stat.S_ISLNK(info.st_mode):
            item["kind"] = "link"
            require(links, "tauri_input_unexpected_link")
            key = os.path.normcase(str(current))
            require(key not in seen and len(seen) < CLI_MAX_LINKS, "tauri_input_link_cycle_or_budget")
            seen.add(key)
            target = os.readlink(current)
            item["target_literal"] = target
            item["stat_after_readlink"] = _cli_stat(current.lstat())
            require(item["stat"] == item["stat_after_readlink"], "tauri_input_link_changed")
            require(isinstance(target, str) and 0 < len(target) <= CLI_MAX_PATH
                    and "\0" not in target, "tauri_input_link_target_budget")
            destination = _cli_target(anchor, current.parent, target)
            item["target_lexical"] = str(destination)
            pending = list(destination.relative_to(anchor).parts) + pending
            current = anchor
        else:
            require(not getattr(info, "st_file_attributes", 0) & capture.REPARSE,
                    "tauri_input_reparse_refused")
            item["kind"] = "directory" if stat.S_ISDIR(info.st_mode) else "file"
            if pending:
                require(stat.S_ISDIR(info.st_mode), "tauri_input_parent_not_directory")
            else:
                require(stat.S_ISREG(info.st_mode), "tauri_input_not_regular")
                record["endpoint"] = str(current)
                return current
    raise ValueError("tauri_input_endpoint_missing")


def _cli_projection(value):
    """Exclude phase custody paths and directory namespace churn, not file/link metadata."""
    if isinstance(value, list):
        return [_cli_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _cli_projection(item) for key, item in value.items()
              if key not in ("retained_path", "retained_identity", "retained_reader")}
    if value.get("kind") == "directory" and "stat" in result:
        result["stat"] = {key: item for key, item in result["stat"].items()
                          if key not in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
    return result



def _cli_expected_read(root, path, expected, record, *, retain, limit):
    """Bind returned bytes/digest to this exact expected regular-file handle."""
    path = capture.admitted(root, path)
    named = _cli_stat(path.lstat())
    record["named_before"] = named
    require(named == expected, "tauri_input_named_identity_disagrees")
    if expected["st_size"] > limit:
        raise capture.CaptureError("file_budget_exceeded" if retain else "artifact_budget_exceeded")
    fields = ["st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid",
              "st_size", "st_mtime_ns"]
    # CPython Windows namespace/handle ctime semantics differ. Keep both raw
    # observations and compare each API in full; birthtime bridges the APIs.
    fields.append("st_birthtime_ns" if os.name == "nt" else "st_ctime_ns")
    record["cross_api_identity_fields"] = fields
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    failure = None
    try:
        opened = _cli_stat(os.fstat(fd))
        record["handle_before"] = opened
        require(stat.S_ISREG(opened["st_mode"]) and
                all(opened[key] == expected[key] for key in fields),
                "tauri_input_handle_identity_disagrees")
        digest, contents, count = hashlib.sha256(), bytearray(), 0
        while True:
            block = os.read(fd, min(1024 * 1024, limit + 1 - count))
            if not block:
                break
            count += len(block)
            record["read_bytes"] = count
            if count > limit:
                raise capture.CaptureError("file_budget_exceeded" if retain else "artifact_budget_exceeded")
            digest.update(block)
            if retain:
                contents.extend(block)
        finished = _cli_stat(os.fstat(fd))
        record["handle_after"] = finished
        after = _cli_stat(capture.admitted(root, path).lstat())
        record["named_after"] = after
        require(opened == finished and named == after == expected and
                all(finished[key] == expected[key] for key in fields) and count == expected["st_size"],
                "tauri_input_changed_during_expected_read")
        return bytes(contents) if retain else {"bytes": count, "sha256": digest.hexdigest()}
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            os.close(fd)
            record["descriptor_closed"] = True
        except OSError as close_error:
            record["descriptor_closed"] = False
            if failure is None:
                raise
            failure.add_note("Input descriptor close also failed: " + str(_cli_problem(close_error)))


def _cli_verify_saved(repo, path, expected, limit, code):
    path = capture.admitted(repo, path)
    require(_cli_stat(path.lstat()) == expected["identity"], code)
    reader = {}
    actual = _cli_expected_read(repo, path, expected["identity"], reader, retain=False, limit=limit)
    require(actual == {key: expected[key] for key in ("bytes", "sha256")}, code)
    return actual, reader


class _CliInputObservation:
    def __init__(self, repo, source, tree, environment, directory, phase):
        self.repo, self.directory = repo, directory
        self.output_owner = _cli_owner(directory)
        self.retained, self.hashed = 0, 0
        self.record = {
            "schema": "motionwright.linux-tauri-input-observation/1",
            "source": source, "source_tree": tree, "phase": phase,
            "cwd": str(repo / "apps/desktop"), "path_literal": environment.get("PATH"),
            "roles": [], "tool_search": {}, "collection_complete": False,
            **CLI_CLAIMS,
            "limits": {
                "retained_original_bytes_per_phase": CLI_MAX_RETAINED,
                "accepted_hash_only_bytes_per_phase": CLI_MAX_HASHED,
                "small_reader_extra_bytes_per_file": 1,
                "hash_reader_extra_bytes_per_file": 1,
                "record_bytes_per_phase": CLI_MAX_RECORD,
                "max_roles": 16, "max_path_entries": CLI_MAX_PATH_ENTRIES,
                "max_links_per_trace": CLI_MAX_LINKS, "max_parts_per_trace": CLI_MAX_PARTS,
            },
            "limitations": [
                "Sequential observations, not an atomic snapshot or proof of immobility during bundle.",
                "PATH selection is static; no Node/npm/JavaScript or native loader probe is executed.",
                "Directory size, mtime, ctime and nlink are retained as raw namespace observations; "
                "directory comparison uses the remaining object identity fields.",
                "The existing bundle timeout starts after Popen; 900 seconds is not an aggregate deadline.",
            ],
        }

    def file(self, role, anchor, requested, expected, *, retain, limit):
        require(len(self.record["roles"]) < 16 and
                role not in {item["role"] for item in self.record["roles"]}, "tauri_input_role_roster")
        item = {"role": role, "expected_endpoint": str(expected), "status": "INCOMPLETE",
                "path_before": {}, "endpoint_admission": {}}
        self.record["roles"].append(item)
        try:
            endpoint = _cli_trace(anchor, requested, item["path_before"], links=True)
            require(endpoint == expected, "tauri_input_endpoint_unexpected")
            # The expected endpoint is declared by the role/profile, independently of readlink.
            _cli_trace(anchor, expected, item["endpoint_admission"], links=False)
            capture.admitted(anchor, expected)
            before = expected.lstat()
            item["file_before"] = _cli_stat(before)
            require(item["path_before"]["nodes"][-1]["stat"] ==
                    item["endpoint_admission"]["nodes"][-1]["stat"] == item["file_before"],
                    "tauri_input_endpoint_identity_disagrees")
            if retain:
                remaining = CLI_MAX_RETAINED - self.retained
                item["reader"] = {}
                raw = _cli_expected_read(anchor, expected, item["file_before"], item["reader"],
                                         retain=True, limit=min(limit, remaining))
                require(len(raw) <= remaining, "tauri_input_retention_budget")
                self.retained += len(raw)
                target = self.directory / (role + ".original")
                capture.admitted(self.repo, target.parent, directory=True)
                require(_cli_owner(self.directory) == self.output_owner, "tauri_input_output_owner_changed")
                capture.write_new(target, raw)
                metadata, saved_reader = _cli_stat(target.lstat()), {}
                require(_cli_expected_read(self.repo, target, metadata, saved_reader, retain=True, limit=limit)
                        == raw, "tauri_input_retained_readback")
                item.update(bytes=len(raw), sha256=capture.sha(raw),
                            retained_path=str(target.relative_to(self.repo)),
                            retained_identity=metadata, retained_reader=saved_reader)
            else:
                remaining = CLI_MAX_HASHED - self.hashed
                item["reader"] = {}
                observed = _cli_expected_read(anchor, expected, item["file_before"], item["reader"],
                                              retain=False, limit=min(limit, remaining))
                self.hashed += observed["bytes"]
                item.update(observed)
                raw = None
            item["file_after"] = _cli_stat(expected.lstat())
            item["path_after"] = {}
            _cli_trace(anchor, requested, item["path_after"], links=True)
            require(item["file_before"] == item["file_after"] == item["path_after"]["nodes"][-1]["stat"] and
                    _cli_projection(item["path_before"]) == _cli_projection(item["path_after"]),
                    "tauri_input_changed_during_observation")
            item["status"] = "OBSERVED"
            return raw
        except Exception as error:
            item["error"] = _cli_problem(error)
            raise

    def json(self, role, path, limit=128 * 1024):
        return capture.parse_json(self.file(role, self.repo, path, path, retain=True, limit=limit))

    def tool(self, name):
        literal = self.record["path_literal"]
        require(isinstance(literal, str) and len(literal) <= 32768 and "\0" not in literal,
                "tauri_input_path_environment_invalid")
        entries = literal.split(os.pathsep)
        require(len(entries) <= CLI_MAX_PATH_ENTRIES, "tauri_input_path_entries_budget")
        search = {"name": name, "candidates": [], "selection": None}
        self.record["tool_search"][name] = search
        cwd = Path(self.record["cwd"])
        for raw in entries:
            require(len(raw) <= CLI_MAX_PATH, "tauri_input_path_budget")
            entry = (cwd if raw == "" else _cli_lexical(Path(Path(raw).anchor), raw)
                     if Path(raw).is_absolute() else _cli_target(Path(cwd.anchor), cwd, raw))
            anchor, requested = entry.parent, entry / name
            candidate = {"entry_literal": raw, "candidate_literal": str(requested), "trace": {}}
            search["candidates"].append(candidate)
            try:
                endpoint = _cli_trace(anchor, requested, candidate["trace"], links=True)
            except FileNotFoundError as error:
                candidate.update(status="ABSENT", missing_path=str(error.filename))
                continue
            if not os.access(endpoint, os.X_OK):
                candidate["status"] = "NOT_EXECUTABLE"
                continue
            expected = [requested]
            if name == "npm":
                expected += [anchor / "lib/node_modules/npm/bin/npm-cli.js",
                             anchor / "share/nodejs/npm/bin/npm-cli.js"]
            else:
                expected += [entry / "nodejs"]
            require(endpoint in expected, "tauri_input_tool_endpoint_outside_profile")
            candidate["status"] = "SELECTED"
            search["selection"] = str(requested)
            self.file(name, anchor, requested, endpoint, retain=name == "npm",
                      limit=256 * 1024 if name == "npm" else 128 * 1024 * 1024)
            return anchor, entry, endpoint
        raise ValueError("tauri_input_tool_not_found:" + name)

    def collect(self):
        self.record["cwd_trace"] = {}
        _cli_directory_trace(Path(self.record["cwd"]), self.record["cwd_trace"])
        studio = self.repo / "apps/studio"
        lock = self.json("studio-lock", studio / "package-lock.json", 2 * 1024 * 1024)
        manifest = self.json("studio-package", studio / "package.json")
        require(isinstance(lock, dict) and lock.get("lockfileVersion") == 3
                and isinstance(lock.get("packages"), dict), "tauri_input_lock_profile")
        packages = lock["packages"]
        cli_name, native_name = "@tauri-apps/cli", "@tauri-apps/cli-linux-x64-gnu"
        cli_lock, native_lock = packages.get("node_modules/" + cli_name), packages.get("node_modules/" + native_name)
        require(isinstance(cli_lock, dict) and isinstance(native_lock, dict), "tauri_input_lock_packages")
        version = cli_lock.get("version")
        require(isinstance(version, str) and version == native_lock.get("version")
                and manifest.get("devDependencies", {}).get(cli_name) == version
                and cli_lock.get("bin") == {"tauri": "tauri.js"}
                and cli_lock.get("optionalDependencies", {}).get(native_name) == version,
                "tauri_input_declared_version_or_bin_mismatch")
        cli, native = studio / "node_modules" / cli_name, studio / "node_modules" / native_name
        cli_package = self.json("cli-package", cli / "package.json")
        native_package = self.json("native-package", native / "package.json")
        require(cli_package.get("name") == cli_name and cli_package.get("version") == version
                and cli_package.get("bin") in ({"tauri": "tauri.js"}, {"tauri": "./tauri.js"})
                and cli_package.get("main") == "main.js"
                and native_package.get("name") == native_name
                and native_package.get("version") == version,
                "tauri_input_installed_metadata_mismatch")
        main = native_package.get("main")
        require(isinstance(main, str) and re.fullmatch(r"[A-Za-z0-9_.-]+\.node", main)
                and native_package.get("os") == ["linux"] and native_package.get("cpu") == ["x64"],
                "tauri_input_native_entry_profile")
        self.record["declared_package_relation"] = {
            "cli": cli_name, "native": native_name, "version": version,
            "wrapper": "tauri.js", "installed_bin_literal": cli_package["bin"],
            "cli_main": cli_package["main"], "loader_candidate": "index.js", "native_main": main,
            "basis": "Observed lock and installed metadata; neither archive authentication nor load tracing.",
        }
        wrapper = cli / "tauri.js"
        self.file("cli-wrapper", self.repo, wrapper, wrapper, retain=True, limit=256 * 1024)
        self.file("cli-main", self.repo, cli / "main.js", cli / "main.js",
                  retain=True, limit=256 * 1024)
        self.file("cli-loader", self.repo, cli / "index.js", cli / "index.js",
                  retain=True, limit=256 * 1024)
        self.file("native-module", self.repo, native / main, native / main,
                  retain=False, limit=128 * 1024 * 1024)
        self.file("tauri-launcher", self.repo, studio / "node_modules/.bin/tauri", wrapper,
                  retain=False, limit=256 * 1024)
        self.tool("node")
        anchor, entry, npm = self.tool("npm")
        npm_package = npm.parent.parent / "package.json" if npm.name == "npm-cli.js" else anchor / "lib/node_modules/npm/package.json"
        raw = self.file("npm-package", anchor, npm_package, npm_package, retain=True, limit=128 * 1024)
        declared = capture.parse_json(raw)
        require(declared.get("name") == "npm" and isinstance(declared.get("version"), str),
                "tauri_input_npm_metadata_profile")
        self.record["declared_npm_version"] = declared["version"]
        self.record["collection_complete"] = True


def _cli_phase(repo, source, tree, environment, directory, phase, bundle_records=None):
    capture.admitted(repo, directory, directory=True)
    parent_owner = _cli_owner(directory)
    child = capture.new_directory(repo, directory / phase)
    observer = _CliInputObservation(repo, source, tree, environment, child, phase)
    collection_error = None
    try:
        observer.collect()
    except Exception as error:
        collection_error = error
        observer.record["collection_complete"] = False
        observer.record["error"] = _cli_problem(error)
    if bundle_records is not None:
        observer.record["bundle_records"] = bundle_records
    observer.record["retained_original_bytes"] = observer.retained
    observer.record["accepted_hash_only_bytes"] = observer.hashed
    try:
        raw = capture.encoded(observer.record)
        if len(raw) > CLI_MAX_RECORD:
            collection_error = collection_error or ValueError("tauri_input_record_budget")
            observer.record["collection_complete"] = False
            observer.record["error"] = _cli_problem(collection_error)
            observer.record["metadata_budget_error"] = "METADATA_NOT_RETAINED_RECORD_BUDGET"
            observer.record["roles"] = [
                {"role": item["role"], "status": "METADATA_NOT_RETAINED",
                 **{key: item[key] for key in ("retained_path", "retained_identity", "bytes", "sha256") if key in item}}
                for item in observer.record["roles"]]
            observer.record["tool_search"] = {"status": "METADATA_NOT_RETAINED_RECORD_BUDGET"}
            raw = capture.encoded(observer.record)
        require(len(raw) <= CLI_MAX_RECORD, "tauri_input_record_budget")
        capture.admitted(repo, directory, directory=True)
        require(_cli_owner(directory) == parent_owner, "tauri_input_output_owner_changed")
        index = directory / (phase + ".json")
        capture.write_new(index, raw)
        metadata = _cli_stat(index.lstat())
        index_reader = {}
        require(_cli_expected_read(repo, index, metadata, index_reader, retain=True, limit=CLI_MAX_RECORD)
                == raw, "tauri_input_phase_record_readback_changed")
    except Exception as retention_error:
        if collection_error is not None:
            collection_error.add_note("Phase retention also failed: " + str(_cli_problem(retention_error)))
            raise collection_error
        raise
    # In-memory custody companions are deliberately outside the self-hashed JSON.
    observer.record["_retained_record"] = {
        "path": str(index.relative_to(repo)), "identity": metadata,
        "bytes": len(raw), "sha256": capture.sha(raw), "reader": index_reader}
    observer.record["_collection_exception"] = collection_error
    return observer.record


def _cli_bundle_records(repo, command):
    result = {"files": {}, "binding_complete": False}
    parsed = {}
    for suffix in ("request.json", "completion.json", "stdout", "stderr"):
        relative = BASE / ("bundle." + suffix)
        item = {"path": relative.as_posix()}
        result["files"][suffix] = item
        try:
            path = capture.admitted(repo, relative)
            before = _cli_stat(path.lstat())
            item["reader"] = {}
            if suffix.endswith(".json"):
                raw = _cli_expected_read(repo, path, before, item["reader"],
                                         retain=True, limit=64 * 1024)
                item.update(bytes=len(raw), sha256=capture.sha(raw))
                parsed[suffix] = capture.parse_json(raw)
                require(isinstance(parsed[suffix], dict), "tauri_bundle_record_shape")
            else:
                item.update(_cli_expected_read(repo, path, before, item["reader"],
                                               retain=False, limit=capture.MAX_STREAM))
            after = _cli_stat(path.lstat())
            require(before == after, "tauri_bundle_record_changed")
            item.update(status="OBSERVED", identity_before=before, identity_after=after)
        except FileNotFoundError:
            item["status"] = "ABSENT"
        except Exception as error:
            item.update(status="UNAVAILABLE", error=_cli_problem(error))
    request, complete = parsed.get("request.json", {}), parsed.get("completion.json", {})
    request = request if isinstance(request, dict) else {}
    complete = complete if isinstance(complete, dict) else {}
    expected = {"command": command, "cwd": str(repo / "apps/desktop"), "label": "bundle", "executed_once": True}
    result["binding_complete"] = (
        all(item.get("status") == "OBSERVED" for item in result["files"].values())
        and all(type(request.get(key)) is type(value) and type(complete.get(key)) is type(value)
                and request.get(key) == value and complete.get(key) == value for key, value in expected.items())
        and complete.get("stdout_final", True) is True and complete.get("stderr_final", True) is True
        and all(complete.get(name + "_sha256") == result["files"][name].get("sha256") for name in ("stdout", "stderr"))
    )
    result["completion_returncode"] = complete.get("returncode")
    result["completion_errors"] = complete.get("errors")
    result["limitations"] = "Existing producer originals only; absent or unclosed records are not synthesized."
    return result


def _cli_post(repo, source, tree, environment, directory, owner, pre, command, primary):
    capture.admitted(repo, directory, directory=True)
    require(_cli_owner(directory) == owner["directory"], "tauri_input_output_owner_changed")
    intent = directory / "intent.json"
    _cli_verify_saved(repo, intent, owner["intent"], 64 * 1024, "tauri_input_intent_changed")
    bundle = _cli_bundle_records(repo, command)
    raw_bundle = capture.encoded(bundle)
    require(len(raw_bundle) <= 64 * 1024, "tauri_input_bundle_record_budget")
    capture.admitted(repo, directory, directory=True)
    require(_cli_owner(directory) == owner["directory"], "tauri_input_output_owner_changed")
    capture.write_new(directory / "bundle-records.json", raw_bundle)
    post = _cli_phase(repo, source, tree, environment, directory, "post", bundle)
    post_primary = post.get("_collection_exception")
    try:
        for phase_record in (pre, post):
            if phase_record:
                for item in phase_record["roles"]:
                    if "retained_path" in item:
                        path = capture.admitted(repo, item["retained_path"])
                        _cli_verify_saved(repo, path, {"identity": item["retained_identity"],
                                                      **{key: item[key] for key in ("bytes", "sha256")}},
                                          CLI_MAX_RETAINED, "tauri_input_retained_original_changed")
        keys = ("source", "source_tree", "cwd", "cwd_trace", "path_literal", "roles", "tool_search",
                "declared_package_relation", "declared_npm_version")
        equal = bool(pre and pre["collection_complete"] and post["collection_complete"]
                     and all(_cli_projection(pre.get(key)) == _cli_projection(post.get(key)) for key in keys))
        record = {
            "schema": "motionwright.linux-tauri-input-comparison/1", "source": source, "source_tree": tree,
            "observations_match": equal, "pre_complete": bool(pre and pre["collection_complete"]),
            "post_complete": post["collection_complete"], "bundle_binding_complete": bundle["binding_complete"],
            "primary_error": _cli_problem(primary) if primary is not None else None, **CLI_CLAIMS,
            "changed_fields": [key for key in keys if pre is None or
                               _cli_projection(pre.get(key)) != _cli_projection(post.get(key))],
            "phase_records": {},
            "limitations": "Pre/post observations are sequential, not an atomic snapshot or proof of inputs throughout bundle.",
        }
        for phase, observed in (("pre", pre), ("post", post)):
            relative = directory / (phase + ".json")
            try:
                path = capture.admitted(repo, relative)
                expected = observed.get("_retained_record") if observed else None
                require(expected is not None, "tauri_input_phase_record_changed")
                digest, reader = _cli_verify_saved(repo, path, expected, CLI_MAX_RECORD,
                                                   "tauri_input_phase_record_changed")
                record["phase_records"][phase] = {
                    "status": "OBSERVED", "identity": expected["identity"], "reader": reader, **digest}
            except FileNotFoundError:
                record["phase_records"][phase] = {"status": "ABSENT"}
                require(observed is None, "tauri_input_phase_record_missing")
        raw = capture.encoded(record)
        capture.admitted(repo, directory, directory=True)
        require(len(raw) <= 64 * 1024 and _cli_owner(directory) == owner["directory"],
                "tauri_input_comparison_budget_or_owner")
        capture.write_new(directory / "comparison.json", raw)
        if post.get("_collection_exception") is not None:
            raise post["_collection_exception"]
        require(equal, "tauri_input_pre_post_changed_or_incomplete")
        require(bundle["binding_complete"], "tauri_input_bundle_originals_incomplete")
        require(primary is not None or (bundle["completion_returncode"] == 0 and
                                       bundle["completion_errors"] == []),
                "tauri_input_bundle_completion_refused")
    except Exception as secondary:
        if post_primary is not None and secondary is not post_primary:
            post_primary.add_note("Post readback/retention also failed: " + str(_cli_problem(secondary)))
            raise post_primary
        raise


def _cli_owner(directory):
    info = _cli_plain_directory(directory)
    return info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_uid", None), getattr(info, "st_gid", None)



def _cli_finish(repo, source, tree, environment, directory, owner, pre, command, primary):
    try:
        _cli_post(repo, source, tree, environment, directory, owner, pre, command, primary)
    except Exception as error:
        failure = {
            "schema": "motionwright.linux-tauri-input-post-failure/1",
            "source": source, "primary_error": _cli_problem(primary) if primary else None,
            "post_error": _cli_problem(error), **CLI_CLAIMS,
            "partial_originals": "Retained create-only under pre/ and post/ when reached.",
        }
        try:
            capture.admitted(repo, directory, directory=True)
            require(_cli_owner(directory) == owner["directory"], "tauri_input_output_owner_changed")
            raw = capture.encoded(failure)
            require(len(raw) <= 64 * 1024, "tauri_input_failure_record_budget")
            capture.write_new(directory / "post-failure.json", raw)
        except Exception as retention_error:
            error.add_note("Post-failure retention also failed: " + str(_cli_problem(retention_error)))
            if primary is not None:
                primary.add_note("Tauri post-failure retention also failed: " + str(_cli_problem(retention_error)))
        if primary is None:
            raise
        primary.add_note("Tauri post observation failed: " + str(_cli_problem(error)))



def linux_source(environment):
    required = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "seradotcom/motionwright",
        "GITHUB_REF": "refs/heads/integrate/phase1-final-composition-20261027",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    require(all(environment.get(key) == value for key, value in required.items()),
            "linux_dependency_scope_refused")
    scope = environment.get("MW_LINUX_CANDIDATE_SCOPE")
    require(scope == "linux_bundle" or
            (scope == "linux_first_use" and
             environment.get("GITHUB_JOB") == "linux-first-use-installed"),
            "linux_dependency_scope_refused")
    source = environment.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None
            and re.fullmatch(r"[1-9][0-9]*", environment.get("GITHUB_RUN_ID", "")) is not None,
            "linux_dependency_identity_invalid")
    return source


def link_environment(environment):
    expected = {"RUSTFLAGS": RUSTFLAGS, "PATCHELF": PATCHELF, "NO_STRIP": "1"}
    require(all(environment.get(key) == value for key, value in expected.items())
            and not environment.get("CARGO_ENCODED_RUSTFLAGS")
            and not environment.get("CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS"),
            "linux_captured_link_environment_mismatch")
    return expected


def cargo_binding(repo, source):
    return shared.cargo_binding(repo, source, **LAYOUT)


def stage_resources(repo, source):
    return shared.stage_resources(repo, source, **LAYOUT)


def observe_link_layout(repo, source, runner_temp, environment):
    """Observe real patchelf on a disposable copy; never modify Cargo output."""
    selected_environment = link_environment(environment)
    snapshot, _, _, binary = cargo_binding(repo, source)
    tool = Path(PATCHELF)
    require(tool.resolve(strict=True) == tool, "linux_patchelf_redirected")
    tool_identity = capture.stable_hash(tool.parent, tool)
    directory = capture.new_directory(repo, repo / BASE / "link-layout")
    scratch = capture.new_directory(runner_temp, runner_temp / "motionwright-linux-link-layout")
    copied = scratch / "motionwright-desktop"
    original = capture.stable_read(repo, BINARY)
    require({"bytes": len(original), "sha256": capture.sha(original)}
            == {key: binary[key] for key in ("bytes", "sha256")},
            "linux_link_input_changed")
    capture.write_new(copied, original)
    del original
    commands = {
        "version": [PATCHELF, "--version"],
        "runpath": [PATCHELF, "--print-rpath", str(copied)],
        "same-runpath": [PATCHELF, "--set-rpath", RUNPATH, str(copied)],
    }
    observations = {}
    for label, command in commands.items():
        observations[label] = capture.run_captured(
            command, repo, directory, label, env=environment,
            stream_limit=64 * 1024, timeout=20)
        if label == "runpath":
            require(capture.stable_read(directory, "runpath.stdout", 4096)
                    == (RUNPATH + "\n").encode(), "linux_compiled_runpath_mismatch")
    observed = capture.stable_hash(scratch, copied)
    expected = {key: binary[key] for key in ("bytes", "sha256")}
    require(observed == expected, "linux_patchelf_changed_captured_copy")
    require(capture.stable_hash(repo, BINARY) == expected
            and capture.stable_hash(tool.parent, tool) == tool_identity
            and capture.source_snapshot(repo, source) == snapshot,
            "linux_link_probe_input_changed")
    record = {
        "schema": "motionwright.linux-captured-link-layout/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "status": "SAME_RUNPATH_PRESERVES_CAPTURED_COPY",
        "binary": expected, "runpath": RUNPATH,
        "environment": selected_environment, "patchelf": tool_identity,
        "observations": observations,
        "package_byte_identity": "SEPARATE_EXTRACTION_GATE",
    }
    capture.write_new(directory / "result.json", capture.encoded(record))
    return record


def bundle_packages(repo, source, environment):
    """Package the verified captured executable without another Cargo build."""
    link_environment(environment)
    before = cargo_binding(repo, source)
    mapped = shared.resource_mapping(repo, before[2], base=BASE)
    overlay = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay) == {"bundle": {"resources": mapped}},
            "linux_cargo_overlay_changed")
    command = [
        "npm", "--prefix", "../studio", "exec", "--", "tauri", "bundle",
        "--target", TARGET, "--features", "tauri/custom-protocol",
        "--bundles", "deb,appimage",
        "--config", "../../" + (BASE / "tauri-resources.json").as_posix(),
        "--no-binary-patching", "--no-sign", "--ci",
    ]
    # Reserve a fresh public sidecar; existing bundle originals are never replayed.
    require(isinstance(environment.get("PATH"), str)
            and len(environment["PATH"]) <= 32768 and "\0" not in environment["PATH"],
            "tauri_input_path_environment_invalid")
    for suffix in ("request.json", "completion.json", "stdout", "stderr"):
        try:
            (repo / BASE / ("bundle." + suffix)).lstat()
        except FileNotFoundError:
            continue
        raise ValueError("tauri_input_bundle_replay_refused")
    # Tauri's default cache is mutable and unverified. Use one fresh private
    # cache only for this real packaging invocation, without changing the
    # parent environment or a previously built candidate.
    runner = Path(environment["RUNNER_TEMP"]).resolve(strict=True)
    cache_home = capture.new_directory(
        runner, runner / ("motionwright-pinned-tauri-" +
                          environment["GITHUB_RUN_ID"] + "-" +
                          environment["GITHUB_RUN_ATTEMPT"]))
    environment = dict(environment)
    environment["XDG_CACHE_HOME"] = str(cache_home)
    pinned = toolpins.stage(cache_home)
    before_tool_cache = toolpins.verify_cached(cache_home, pinned)
    pinned.update({
        "source": source, "source_tree": before[0]["tree"],
        "run_id": environment["GITHUB_RUN_ID"],
        "run_attempt": environment["GITHUB_RUN_ATTEMPT"],
        "cache_root": str(cache_home),
        "pre_bundle": before_tool_cache,
    })
    pinned_raw = capture.encoded(pinned)
    require(len(pinned_raw) <= toolpins.MAX_REPORT,
            "linux_pinned_tools_pre_receipt_budget")
    capture.write_new(repo / BASE / "tauri-tool-pins.pre.json", pinned_raw)
    require(capture.stable_read(repo, BASE / "tauri-tool-pins.pre.json",
                                toolpins.MAX_REPORT) == pinned_raw,
            "linux_pinned_tools_pre_receipt_changed")
    directory = capture.new_directory(repo, repo / CLI_INPUTS)
    owner = _cli_owner(directory)
    intent = {
        "schema": "motionwright.linux-tauri-input-intent/1",
        "source": source, "source_tree": before[0]["tree"],
        "command": command, "cwd": str(repo / "apps/desktop"),
        "path_literal": environment["PATH"], "bundle_timeout": 900, **CLI_CLAIMS,
        "limits": {
            "retained_original_bytes_both_phases": 2 * CLI_MAX_RETAINED,
            "phase_json_bytes_each": CLI_MAX_RECORD,
            "other_sidecar_json_bytes_each": 64 * 1024,
            "bundle_original_stream_bytes_each": capture.MAX_STREAM,
        },
        "limitations": [
            "No additional process, Node/npm invocation, JS evaluation or archive download.",
            "No atomic snapshot, continuous immobility, module-load or generator-source claim.",
            "900 seconds remains the existing run_captured timeout after Popen, not an aggregate deadline.",
        ],
    }
    raw = capture.encoded(intent)
    require(len(raw) <= 64 * 1024, "tauri_input_intent_budget")
    capture.write_new(directory / "intent.json", raw)
    owner = {"directory": owner, "intent": {
        "identity": _cli_stat((directory / "intent.json").lstat()),
        "bytes": len(raw), "sha256": capture.sha(raw)}}
    pre = None
    try:
        pre = _cli_phase(repo, source, before[0]["tree"], environment, directory, "pre")
        if pre.get("_collection_exception") is not None:
            raise pre["_collection_exception"]
        require(pre["collection_complete"], "tauri_input_pre_observation_incomplete")
        # Directly before spawning tauri, validate pinned original bytes:
        # there is no accepted unverified fallback.
        require(toolpins.verify_cached(cache_home, pinned) == before_tool_cache,
                "linux_tool_cache_changed_before_bundler_spawn")
        capture.run_captured(command, repo / "apps/desktop", repo / BASE, "bundle",
                             env=environment, timeout=900)
        after_tool_cache = toolpins.verify_cached(cache_home, pinned, post=True)
        after_tool_cache.update({
            "schema": "motionwright.tauri-appimage-pinned-tool-cache-post/1",
            "source": source, "source_tree": before[0]["tree"],
            "run_id": environment["GITHUB_RUN_ID"],
            "run_attempt": environment["GITHUB_RUN_ATTEMPT"],
            "pre_receipt_sha256": capture.sha(pinned_raw),
            "bundler_completion_sha256": capture.sha(capture.stable_read(
                repo, BASE / "bundle.completion.json", 64 * 1024)),
        })
        after_raw = capture.encoded(after_tool_cache)
        require(len(after_raw) <= toolpins.MAX_REPORT,
                "linux_pinned_tools_post_receipt_budget")
        capture.write_new(repo / BASE / "tauri-tool-pins.post.json", after_raw)
        require(capture.stable_read(repo, BASE / "tauri-tool-pins.post.json",
                                    toolpins.MAX_REPORT) == after_raw,
                "linux_pinned_tools_post_receipt_changed")
        require(cargo_binding(repo, source) == before
                and capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
                    == overlay, "linux_captured_bundle_input_changed")
    except BaseException as primary:
        _cli_finish(repo, source, before[0]["tree"], environment, directory, owner, pre, command, primary)
        raise
    _cli_finish(repo, source, before[0]["tree"], environment, directory, owner, pre, command, None)


def verify_packages(repo, source):
    """Join each real extraction report to the same original Cargo capture."""
    from candidate_smoke import _verify_cargo_notice_subtree
    from package_receipt import make_receipt
    from npm_archive_authentication import relative_path

    original_binding = cargo_binding(repo, source)
    snapshot, complete, raw, observed = original_binding
    mapped = shared.resource_mapping(repo, raw, base=BASE)
    overlay = capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024)
    require(capture.parse_json(overlay) == {"bundle": {"resources": mapped}},
            "linux_cargo_overlay_changed")
    original_receipt = capture.stable_read(repo, "ci-evidence/linux-candidate.json", 1024 * 1024)
    candidate = capture.parse_json(original_receipt)
    rows = candidate.get("artifacts")
    require(isinstance(rows, list) and len(rows) == 2
            and {row.get("bundle_kind") for row in rows} == {"deb", "appimage"},
            "linux_candidate_pair_required")
    artifacts = []
    for row in rows:
        name = relative_path(row.get("filename"))
        require("/" not in name, "linux_candidate_filename_invalid")
        artifacts.append(repo / BINARY.parent / "bundle" / row["bundle_kind"] / name)
    rebuilt = make_receipt(
        repo, source, "linux", "x86_64", artifacts,
        cargo_index=repo / BASE / "resources/cargo/index.json",
        cargo_binary=repo / BINARY, require_cargo_binding=True)
    require(candidate == rebuilt, "linux_candidate_changed")
    expected_notices = _verify_cargo_notice_subtree(
        capture=capture, index_raw=raw, anchor=repo / BASE / "resources",
        relative=Path("cargo"))
    packages = {}
    for row in rows:
        kind = row["bundle_kind"]
        smoke_raw = capture.stable_read(repo, "ci-evidence/linux-" + kind + "-smoke.json",
                                        2 * 1024 * 1024)
        smoke = capture.parse_json(smoke_raw)
        inspection = smoke.get("automated_inspection", {})
        require(smoke.get("schema_version") == 1 and smoke.get("bundle_kind") == kind
                and smoke.get("candidate") == candidate["candidate"]
                and smoke.get("candidate_archive") == {
                    key: row[key] for key in ("filename", "bytes", "sha256")}
                and inspection.get("status") == "PASS"
                and inspection.get("method") == kind + "_extract",
                "linux_package_observation_mismatch")
        require(inspection.get("cargo_build_binding") == {
                    **observed, "notice_resources": expected_notices},
                "linux_package_cargo_binding_mismatch")
        identity = inspection.get("binary", {})
        require(identity.get("binary_bytes") == observed["bytes"]
                and identity.get("binary_sha256") == observed["sha256"],
                "linux_extracted_binary_identity_mismatch")
        require(smoke.get("claims") == {
                    "human_install_acceptance": "NOT_RUN",
                    "runtime_renderer_acceptance": "SEPARATE_GATE",
                    "signing": "NOT_CLAIMED", "notarization": "NOT_CLAIMED",
                    "release_published": False},
                "linux_package_claims_changed")
        packages[kind] = {
            "archive": smoke["candidate_archive"],
            "smoke_sha256": capture.sha(smoke_raw),
            "binary_binding": observed["status"], "cargo_notices": expected_notices,
        }
    frontend = shared.read_json(repo, BASE / "frontend/after-bundle.json")
    require(frontend == shared.read_json(repo, BASE / "frontend/before-build.json")
            and frontend.get("source_sha") == source
            and frontend.get("source_tree") == snapshot["tree"]
            and frontend.get("observed_npm_source_and_notice_authentication") == "VERIFIED"
            and frontend.get("source_binding") == "EXACT_HEAD_TREE_INDEX_AND_TRACKED_BYTES_REVALIDATED",
            "linux_frontend_authentication_required")
    require(cargo_binding(repo, source) == original_binding
            and capture.stable_read(repo, "ci-evidence/linux-candidate.json", 1024 * 1024)
                == original_receipt
            and capture.stable_read(repo, BASE / "tauri-resources.json", 2 * 1024 * 1024) == overlay,
            "linux_package_inputs_changed_during_readback")
    report = {
        "schema": "motionwright.linux-packaged-cargo-binding/1",
        "source": source, "source_tree": snapshot["tree"], "target": TARGET,
        "cargo_index_sha256": complete["cargo_index_sha256"],
        "artifact_identities_sha256": complete["artifact_identities_sha256"],
        "generated_metadata_sha256": complete["generated_metadata_sha256"],
        "captured_binary": {key: observed[key] for key in ("bytes", "sha256")},
        "packages": packages,
        "frontend_index_sha256": frontend["frontend_index_sha256"],
        "frontend_observed_npm_authentication": "VERIFIED",
        "full_package_inventory": "NOT_VERIFIED", "human_install_acceptance": "NOT_RUN",
        "native_renderer_execution": "NOT_RUN", "distribution_signature": "NOT_CLAIMED",
        "redistribution_permission": "NOT_INFERRED", "published": False,
    }
    capture.write_new(repo / "ci-evidence/linux-cargo-binding.json", capture.encoded(report))
    return report


def verify_package_inventory(repo, source, *, kind, directory, expected_sha256, payload=None):
    """Consume a detached inventory without expanding the candidate receipt."""
    import candidate_package_inventory as inventory_reader

    require(kind in ("deb", "appimage"), "linux_inventory_kind")
    candidate = shared.read_json(repo, "ci-evidence/linux-candidate.json")
    rows = [row for row in candidate["artifacts"] if row["bundle_kind"] == kind]
    require(len(rows) == 1, "linux_inventory_package_required")
    name = inventory_reader.relative(rows[0]["filename"])
    require("/" not in name, "linux_inventory_package_filename")
    return inventory_reader.consume(
        repo=repo, source=source, kind=kind,
        artifact=repo / BINARY.parent / "bundle" / kind / name,
        receipt=repo / "ci-evidence/linux-candidate.json", directory=directory,
        expected_sha256=expected_sha256, roots=None if payload is None else {"payload": payload})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("frontend", "stage", "bundle", "frontend-recheck", "verify"))
    args = parser.parse_args(argv)
    try:
        source = linux_source(os.environ)
        require(Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True) == ROOT,
                "linux_dependency_checkout_mismatch")
        link_environment(os.environ)
        if args.phase.startswith("frontend"):
            shared.authenticate_frontend(ROOT, source, base=BASE,
                                         recheck=args.phase == "frontend-recheck")
        elif args.phase == "stage":
            observe_link_layout(ROOT, source, Path(os.environ["RUNNER_TEMP"]).resolve(strict=True),
                                dict(os.environ))
            stage_resources(ROOT, source)
        elif args.phase == "bundle":
            bundle_packages(ROOT, source, dict(os.environ))
        else:
            verify_packages(ROOT, source)
        print("linux-dependency-" + args.phase + "=verified formal_acceptance=NOT_GRANTED")
    except (capture.CaptureError, OSError, ValueError, KeyError, TypeError) as error:
        detail = str(error)
        code = detail if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", detail) else type(error).__name__
        print("linux-dependency-" + args.phase + "-failed:" + code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
