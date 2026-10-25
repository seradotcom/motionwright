"""Observe an actual Cargo build and produce authenticated Cargo notice resources.

A fresh state directory retains original stdout/stderr and exact source binding.
This does not authorize redistribution or establish final package/native closure.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, stat, subprocess, sys, threading, time
from pathlib import Path

MAX_STREAM = 128 * 1024 * 1024
MAX_SOURCE = 512 * 1024 * 1024
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

class CaptureError(RuntimeError):
    pass

def sha(data):
    return hashlib.sha256(data).hexdigest()

def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()

def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CaptureError("duplicate_json_key")
        result[key] = value
    return result

def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise CaptureError("invalid_json") from error

def within(root, path):
    root = root.resolve(strict=True)
    path = Path(path)
    candidate = path if path.is_absolute() else root / path
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise CaptureError("path_outside_anchor") from error
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise CaptureError("invalid_relative_path")
    return root, relative

def admitted(root, path, directory=False):
    root, relative = within(root, path)
    current = root
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & REPARSE:
            raise CaptureError("redirected_path")
    wanted = stat.S_ISDIR if directory else stat.S_ISREG
    if not wanted(info.st_mode):
        raise CaptureError("unexpected_file_type")
    return current

def new_directory(root, path):
    root, relative = within(root, path)
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if not current.exists():
            current.mkdir()
        admitted(root, current, directory=True)
    target = current / relative.parts[-1]
    target.mkdir(exist_ok=False)
    return admitted(root, target, directory=True)

def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

def stable_read(root, path, limit=MAX_SOURCE):
    path = admitted(root, path)
    before = path.stat()
    if before.st_size > limit:
        raise CaptureError("file_budget_exceeded")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        raw = handle.read(limit + 1)
        finished = os.fstat(handle.fileno())
    after = admitted(root, path).stat()
    if len(raw) > limit or not identity(before) == identity(opened) == identity(finished) == identity(after):
        raise CaptureError("file_changed_during_read")
    return raw

def stable_hash(root, path, limit=1024 * 1024 * 1024):
    path = admitted(root, path)
    before = path.stat()
    if before.st_size > limit:
        raise CaptureError("artifact_budget_exceeded")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        while block := handle.read(1024 * 1024):
            count += len(block)
            if count > limit:
                raise CaptureError("artifact_budget_exceeded")
            digest.update(block)
        finished = os.fstat(handle.fileno())
    after = admitted(root, path).stat()
    if not identity(before) == identity(opened) == identity(finished) == identity(after) or count != before.st_size:
        raise CaptureError("artifact_changed_during_read")
    return {"bytes": count, "sha256": digest.hexdigest()}

def _load_sibling(name):
    """Load the physical sibling, independently of discovery or dotted imports."""
    import importlib.util

    path = Path(__file__).resolve(strict=True).with_name(name + ".py").resolve(strict=True)
    existing = sys.modules.get(name)
    if existing is not None:
        try:
            same = Path(getattr(existing, "__file__", None)).resolve(strict=True) == path
        except (OSError, TypeError, ValueError):
            same = False
        if not same:
            raise CaptureError("sibling_module_identity_mismatch:" + name)
        return existing
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise CaptureError("sibling_module_unavailable:" + name)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise
    return module

def git(repo, *args):
    # Use the same bounded, read-only plumbing boundary as original notices.
    sources = _load_sibling("cargo_license_sources")
    _git, _Problem = sources._git, sources._Problem
    try:
        return _git(Path(repo), *args)
    except _Problem as error:
        raise CaptureError(error.code) from error

def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def source_snapshot(repo, expected):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise CaptureError("expected_full_source_sha_required")
    repo = repo.resolve(strict=True)
    top = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
    if top != repo or git(repo, "rev-parse", "HEAD").decode().strip() != expected:
        raise CaptureError("source_sha_or_root_mismatch")
    tracked = {}
    for entry in git(repo, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise CaptureError("unsupported_tracked_mode")
        tracked[name.decode("utf-8")] = (mode, oid)
    indexed = {}
    for entry in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, oid, stage = header.decode("ascii").split()
        if stage != "0":
            raise CaptureError("unmerged_index")
        indexed[name.decode("utf-8")] = (mode, oid)
    if indexed != tracked:
        raise CaptureError("index_differs_from_source")
    files, total = [], 0
    for name, (_, oid) in sorted(tracked.items()):
        raw = stable_read(repo, name)
        total += len(raw)
        if total > MAX_SOURCE:
            raise CaptureError("source_budget_exceeded")
        transformation = "EXACT"
        if blob(raw) != oid:
            sources = _load_sibling("cargo_license_sources")
            _GitRepo, _Root, _Problem = sources._GitRepo, sources._Root, sources._Problem
            try:
                git_repo = _GitRepo(_Root.admit(repo, "workspace"), ())
                if git_repo.head != expected:
                    raise CaptureError("source_sha_changed")
                proof = git_repo.verify_bytes(tuple(Path(name).parts), raw, oid)
            except _Problem as error:
                raise CaptureError("tracked_bytes_differ:" + name) from error
            if proof["git_line_endings"] != "lf_blob_to_crlf_worktree":
                raise CaptureError("unexpected_source_transformation")
            transformation = "EXACT_LF_TO_CRLF"
        files.append({"path": name, "git_blob": oid, "bytes": len(raw),
                      "sha256": sha(raw), "checkout_transform": transformation})
    return {"source": expected, "tree": git(repo, "rev-parse", "HEAD^{tree}").decode().strip(), "files": files}

def run_captured(command, cwd, directory, label, env=None, stream_limit=MAX_STREAM, timeout=1800):
    if not command or any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise CaptureError("invalid_command")
    outpath, errpath = directory / (label + ".stdout"), directory / (label + ".stderr")
    out, err = outpath.open("xb"), errpath.open("xb")
    record = {"command": command, "cwd": str(cwd), "label": label, "executed_once": True}
    write_new(directory / (label + ".request.json"), encoded(record))
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
        out.close(); err.close()
        raise CaptureError("invalid_capture_deadline")
    process, errors = None, []
    def consume(pipe, sink):
        count = 0
        try:
            while block := pipe.read(65536):
                remaining = max(0, stream_limit - count)
                sink.write(block[:remaining])
                count += len(block)
                if count > stream_limit:
                    errors.append("stream_budget_exceeded")
                    process.kill()
                    break
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            sink.close()
            pipe.close()
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threads = [threading.Thread(target=consume, args=(process.stdout, out), daemon=True),
                   threading.Thread(target=consume, args=(process.stderr, err), daemon=True)]
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.start()
        try:
            code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            errors.append("process_timeout")
            try:
                process.kill()
            except OSError:
                pass
            try:
                code = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                code = None
                errors.append("process_reap_unconfirmed")
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            errors.append("unclosed_process_stream")
            try:
                process.kill()
            except OSError:
                pass
        record.update(returncode=code, errors=errors)
    except Exception as error:
        out.close()
        err.close()
        record.update(returncode=None, errors=[type(error).__name__])
        write_new(directory / (label + ".completion.json"), encoded(record))
        raise CaptureError("process_not_completed") from error
    if "unclosed_process_stream" in errors:
        record.update(stdout_final=False, stderr_final=False)
        write_new(directory / (label + ".completion.json"), encoded(record))
        raise CaptureError("capture_stream_custody_unclosed")
    record.update(stdout_sha256=sha(outpath.read_bytes()), stderr_sha256=sha(errpath.read_bytes()))
    write_new(directory / (label + ".completion.json"), encoded(record))
    if code != 0 or errors:
        raise CaptureError("process_failed:" + label)
    return record

def cargo_messages(raw):
    messages = []
    for line in raw.splitlines():
        if len(line) > 8 * 1024 * 1024:
            raise CaptureError("cargo_line_budget_exceeded")
        if not line.lstrip().startswith(b"{"):
            continue
        try:
            item = parse_json(line)
        except CaptureError:
            if b'"reason"' in line:
                raise
            continue
        if not isinstance(item, dict) or "reason" not in item:
            continue
        if item["reason"] not in {"compiler-artifact", "compiler-message", "build-script-executed", "build-finished"}:
            raise CaptureError("unknown_cargo_record")
        messages.append(item)
    if not messages or len(messages) > 100000:
        raise CaptureError("missing_or_excessive_cargo_records")
    return messages

def artifact_identities(metadata, messages):
    root = Path(metadata["target_directory"]).resolve(strict=True)
    paths = set()
    for item in messages:
        if item["reason"] == "compiler-artifact":
            paths.update(item["filenames"])
            if item.get("executable"):
                paths.add(item["executable"])
    result = []
    for filename in sorted(paths):
        path = admitted(root, Path(filename))
        result.append({"path": path.relative_to(root).as_posix(), **stable_hash(root, path)})
    return result


def _binding_require(condition, code):
    if not condition:
        raise CaptureError(code)


def _binding_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", value)


def _binding_path(value):
    _binding_require(isinstance(value, str) and 0 < len(value) <= 4096
                     and not any(char in value for char in ("\\", ":", "\0"))
                     and all(part not in ("", ".", "..") for part in value.split("/")),
                     "artifact_path_invalid")
    return value


def _binding_digest(row):
    _binding_require(isinstance(row, dict) and type(row.get("bytes")) is int
                     and 0 <= row["bytes"] <= 1024 * 1024 * 1024
                     and isinstance(row.get("sha256"), str)
                     and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
                     "artifact_digest_invalid")
    return {"bytes": row["bytes"], "sha256": row["sha256"]}


def _binding_roster(artifacts):
    _binding_require(isinstance(artifacts, list) and 0 < len(artifacts) <= 100000,
                     "artifact_roster_invalid")
    result, folded = {}, set()
    for item in artifacts:
        _binding_require(isinstance(item, dict) and set(item) == {"path", "bytes", "sha256"},
                         "artifact_identity_invalid")
        name = _binding_path(item["path"])
        _binding_require(name.casefold() not in folded, "duplicate_artifact_identity")
        folded.add(name.casefold())
        result[name] = {"path": name, **_binding_digest(item)}
    return result


def bind_artifacts(metadata, inventory, artifacts, *, public_root_id):
    """Bind each observed root executable to its original artifact byte identity."""
    target_root = Path(metadata["target_directory"]).resolve(strict=True)
    by_path = _binding_roster(artifacts)
    _binding_require(isinstance(public_root_id, str) and 0 < len(public_root_id) <= 8192
                     and not any(ord(c) < 32 for c in public_root_id),
                     "public_root_identity_invalid")
    observations = inventory["root_binary_observations"]
    _binding_require(isinstance(observations, list) and 0 < len(observations) <= 256,
                     "root_binary_not_observed")
    roots, seen_units = [], set()
    for item in observations:
        _binding_require(isinstance(item, dict) and _binding_name(item.get("name"))
                         and isinstance(item.get("unit_id"), str)
                         and re.fullmatch(r"[0-9a-f]{64}", item["unit_id"]),
                         "root_binary_observation_invalid")
        _binding_require(item["unit_id"] not in seen_units, "duplicate_root_binary_observation")
        seen_units.add(item["unit_id"])
        executable = admitted(target_root, Path(item["executable"]))
        relative = executable.relative_to(target_root).as_posix()
        captured = by_path.get(relative)
        _binding_require(captured is not None, "root_binary_not_in_artifact_roster")
        recorded = _binding_digest(captured)
        _binding_require(stable_hash(target_root, executable) == recorded,
                         "root_binary_changed_after_capture")
        roots.append({"package_id": public_root_id, "package": inventory["root_package_name"],
                      "target": inventory["target_triple"], "name": item["name"],
                      "unit_id": item["unit_id"], "path": relative, **recorded})
    roots.sort(key=lambda row: (row["name"], row["unit_id"], row["path"]))
    return {"artifact_identities": artifacts,
            "artifact_identities_sha256": sha(encoded(artifacts)),
            "root_binary_observations": roots}


def verify_binary_binding(index_raw, *, expected_index_sha256, expected_source,
                          expected_source_tree, expected_target, expected_package,
                          expected_binary, binary_root, binary_path):
    """Require a trusted candidate index digest and exact captured executable bytes.

    The caller retains responsibility for the actual package extraction and the
    provenance of the expected index digest. This is not a signature, a complete
    package inventory, or proof of an unobserved strip/sign transformation.
    """
    _binding_require(isinstance(index_raw, bytes) and 0 < len(index_raw) <= 16 * 1024 * 1024,
                     "cargo_index_budget_exceeded")
    _binding_require(isinstance(expected_index_sha256, str)
                     and re.fullmatch(r"[0-9a-f]{64}", expected_index_sha256)
                     and sha(index_raw) == expected_index_sha256,
                     "cargo_index_digest_mismatch")
    _binding_require(isinstance(expected_source, str)
                     and re.fullmatch(r"[0-9a-f]{40}", expected_source)
                     and isinstance(expected_source_tree, str)
                     and re.fullmatch(r"[0-9a-f]{40}", expected_source_tree)
                     and isinstance(expected_target, str)
                     and re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+){2,4}", expected_target)
                     and not expected_target.startswith("universal-")
                     and _binding_name(expected_package) and _binding_name(expected_binary),
                     "candidate_binding_scope_invalid")
    index = parse_json(index_raw)
    _binding_require(isinstance(index, dict)
                     and index.get("schema") == "motionwright.cargo-build-notices/1"
                     and index.get("source") == expected_source
                     and index.get("source_tree") == expected_source_tree
                     and index.get("target") == expected_target
                     and index.get("root_package") == expected_package,
                     "cargo_index_candidate_scope_mismatch")
    artifacts = index.get("artifact_identities")
    by_path = _binding_roster(artifacts)
    _binding_require(sha(encoded(artifacts)) == index.get("artifact_identities_sha256"),
                     "artifact_roster_digest_mismatch")
    roots = index.get("root_binary_observations")
    _binding_require(isinstance(roots, list) and 0 < len(roots) <= 256,
                     "root_binary_observations_invalid")
    units = set()
    for row in roots:
        _binding_require(isinstance(row, dict)
                         and _binding_name(row.get("name"))
                         and row.get("target") == expected_target
                         and row.get("package") == expected_package
                         and isinstance(row.get("package_id"), str)
                         and 0 < len(row["package_id"]) <= 8192
                         and isinstance(row.get("unit_id"), str)
                         and re.fullmatch(r"[0-9a-f]{64}", row["unit_id"]),
                         "root_binary_candidate_scope_mismatch")
        _binding_require(row["unit_id"] not in units, "duplicate_root_binary_observation")
        units.add(row["unit_id"])
        path = _binding_path(row.get("path"))
        _binding_require(path in by_path and _binding_digest(by_path[path]) == _binding_digest(row),
                         "root_binary_artifact_identity_mismatch")
    selected = [row for row in roots if row["name"] == expected_binary]
    _binding_require(len(selected) == 1, "root_binary_selection_not_unique")
    root = selected[0]
    components = index.get("components")
    _binding_require(isinstance(components, list) and 0 < len(components) <= 4096
                     and all(isinstance(row, dict) for row in components),
                     "cargo_component_set_invalid")
    matches = [row for row in components if row.get("component_id") == root["package_id"]
               and row.get("name") == expected_package]
    _binding_require(len(matches) == 1, "root_binary_component_identity_mismatch")
    observed = stable_hash(Path(binary_root), Path(binary_path))
    _binding_require(observed == _binding_digest(root),
                     "installed_binary_differs_from_cargo_capture")
    return {"status": "CAPTURED_ROOT_BINARY_BYTE_EXACT",
            "cargo_index_sha256": expected_index_sha256,
            "artifact_identities_sha256": index["artifact_identities_sha256"],
            "source": expected_source, "source_tree": expected_source_tree,
            "package_id": root["package_id"], "target": expected_target,
            "name": expected_binary, "unit_id": root["unit_id"], **observed}

def public_native_reviews(reviews, by_id, repo):
    """Publish native-review references using the same public component IDs."""
    if not isinstance(reviews, list) or len(reviews) > 4096:
        raise CaptureError("native_reviews_invalid")
    result = []
    for review in reviews:
        if not isinstance(review, dict):
            raise CaptureError("native_review_invalid")
        identity = review.get("component_id")
        if not isinstance(identity, str) or identity not in by_id:
            raise CaptureError("native_review_component_unresolved")
        component = by_id[identity]
        if not isinstance(component, dict) or component.get("id") != identity:
            raise CaptureError("native_review_component_identity_mismatch")
        result.append({**review, "component_id": public_component(component, repo)["component_id"]})
    return result

def public_component(item, repo):
    keys = ("name", "version", "roles", "possible_roles", "license_expression", "source_authentication", "notices", "issues")
    result = {key: item.get(key) for key in keys}
    source = item.get("source")
    if source is None:
        try:
            relative = Path(item["manifest_path"]).relative_to(repo).as_posix()
        except ValueError as error:
            raise CaptureError("path_component_outside_workspace") from error
        result["component_id"] = "cargo:path:" + relative + "#" + item["name"] + "@" + item["version"]
        result["source"] = {"kind": "workspace_path", "manifest": relative}
    else:
        from urllib.parse import urlsplit
        url = urlsplit(source.split("+", 1)[-1])
        if url.username is not None or url.password is not None:
            raise CaptureError("credential_in_source_url")
        result["component_id"], result["source"] = item["id"], source
    return result

def validate_cargo_build(command, *, cargo, cwd, manifest, target, package,
                         metadata_features=(), no_default_features=False):
    """Accept one explicit Cargo build whose metadata arguments have the same scope."""
    if not command or any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise CaptureError("invalid_command")
    executable = Path(shutil.which(cargo) or cargo).resolve(strict=True)
    invoked = Path(shutil.which(command[0]) or command[0]).resolve(strict=True)
    if executable.name.lower() not in ("cargo", "cargo.exe") or invoked != executable:
        raise CaptureError("actual_cargo_executable_required")
    if len(command) < 2 or command[1] != "build":
        raise CaptureError("single_cargo_build_required")
    flags = {"--locked", "--offline", "--frozen", "--release", "--no-default-features", "--bins"}
    valued = {"--target", "--package", "--manifest-path", "--features",
              "--message-format", "--jobs", "--profile"}
    aliases = {"-p": "--package", "-F": "--features", "-j": "--jobs"}
    selected, index = {}, 2
    while index < len(command):
        token = command[index]
        key, separator, value = token.partition("=")
        key = aliases.get(key, key)
        if key not in flags | valued:
            raise CaptureError("unsupported_cargo_build_argument:" + key)
        if key in selected:
            raise CaptureError("duplicate_cargo_build_argument:" + key)
        if key in flags:
            if separator:
                raise CaptureError("unexpected_cargo_flag_value")
            selected[key] = True
        else:
            if not separator:
                index += 1
                if index == len(command):
                    raise CaptureError("missing_cargo_argument_value")
                value = command[index]
            if not value:
                raise CaptureError("empty_cargo_argument_value")
            selected[key] = value
        index += 1
    if not selected.get("--locked") or selected.get("--message-format") != "json-render-diagnostics":
        raise CaptureError("locked_cargo_json_arguments_required")
    if selected.get("--target") != target or selected.get("--package") != package:
        raise CaptureError("cargo_target_or_package_scope_mismatch")
    if "--manifest-path" in selected:
        candidate = Path(selected["--manifest-path"])
        candidate = candidate if candidate.is_absolute() else Path(cwd) / candidate
        if candidate.resolve(strict=True) != manifest.resolve(strict=True):
            raise CaptureError("cargo_manifest_scope_mismatch")
    def feature_set(values):
        return sorted({feature for value in values for feature in re.split(r"[,\s]+", value) if feature})
    features = feature_set([selected.get("--features", "")])
    if features != feature_set(metadata_features):
        raise CaptureError("cargo_feature_scope_mismatch")
    if bool(selected.get("--no-default-features")) != bool(no_default_features):
        raise CaptureError("cargo_default_feature_scope_mismatch")
    if "--release" in selected and "--profile" in selected:
        raise CaptureError("ambiguous_cargo_profile")
    if "--jobs" in selected and not re.fullmatch(r"[1-9][0-9]*", selected["--jobs"]):
        raise CaptureError("positive_cargo_jobs_required")
    return {"kind": "cargo", "target": target, "root_package": package,
            "binary_target_selection": "all-bins" if selected.get("--bins") else "cargo-default",
            "features": features, "default_features": not no_default_features,
            "profile": selected.get("--profile", "release" if selected.get("--release") else "dev"),
            "cargo_executable": stable_hash(executable.parent, executable),
            "metadata_and_build_driver_match": True}

def revalidate_build_driver(driver, cargo):
    executable = Path(shutil.which(cargo) or cargo).resolve(strict=True)
    if stable_hash(executable.parent, executable) != driver["cargo_executable"]:
        raise CaptureError("cargo_executable_changed")

def capture_build(*, repo, expected_source, cargo, cargo_home, target_triple,
                  root_package_name, manifest_path, state_directory, resources_directory,
                  build_command, metadata_features=(), no_default_features=False, env=None, build_cwd=None):
    derive_components = _load_sibling("cargo_build_inventory").derive_components
    collect_cargo_licenses = _load_sibling("cargo_license_sources").collect_cargo_licenses
    repo = Path(repo).resolve(strict=True)
    before = source_snapshot(repo, expected_source)
    manifest = admitted(repo, manifest_path)
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+){2,4}", target_triple) or target_triple.startswith("universal-"):
        raise CaptureError("single_rust_target_required")
    cwd = repo if build_cwd is None else Path(build_cwd).resolve(strict=True)
    if cwd != repo:
        cwd = admitted(repo, cwd, directory=True)
    driver = validate_cargo_build(build_command, cargo=cargo, cwd=cwd,
                                  manifest=manifest, target=target_triple,
                                  package=root_package_name, metadata_features=metadata_features,
                                  no_default_features=no_default_features)
    effective_env = dict(os.environ if env is None else env)
    effective_env["CARGO_HOME"] = str(Path(cargo_home).resolve(strict=True))
    state = new_directory(repo, state_directory)
    resources = new_directory(repo, resources_directory)
    write_new(state / "source-before.json", encoded(before))
    metadata_command = [cargo, "metadata", "--locked", "--format-version=1",
                        "--filter-platform", target_triple, "--manifest-path", str(manifest)]
    if metadata_features:
        metadata_command += ["--features", ",".join(metadata_features)]
    if no_default_features:
        metadata_command.append("--no-default-features")
    metadata_receipt = run_captured(metadata_command, cwd, state, "metadata", env=effective_env)
    revalidate_build_driver(driver, cargo)
    metadata_raw = (state / "metadata.stdout").read_bytes()
    if len(metadata_raw) > 32 * 1024 * 1024:
        raise CaptureError("metadata_budget_exceeded")
    metadata = parse_json(metadata_raw)
    root_packages = [p for p in metadata["packages"] if p["name"] == root_package_name]
    if len(root_packages) != 1 or Path(root_packages[0]["manifest_path"]) != manifest:
        raise CaptureError("root_package_manifest_mismatch")
    build_receipt = run_captured(build_command, cwd, state, "build", env=effective_env)
    revalidate_build_driver(driver, cargo)
    after = source_snapshot(repo, expected_source)
    write_new(state / "source-after.json", encoded(after))
    if before != after:
        raise CaptureError("source_changed_during_build")
    messages = cargo_messages((state / "build.stdout").read_bytes())
    inventory = derive_components(metadata, messages, root_package_name=root_package_name,
                                  target_triple=target_triple)
    write_new(state / "cargo-components.json", encoded(inventory))
    artifacts = artifact_identities(metadata, messages)
    write_new(state / "cargo-artifacts.json", encoded(artifacts))
    lock = stable_read(repo, "Cargo.lock", 8 * 1024 * 1024)
    notices = collect_cargo_licenses(inventory["components"], workspace_root=repo,
                                    cargo_home=Path(cargo_home), lock_bytes=lock)
    write_new(state / "cargo-license-sources.json", encoded(
        {key: value for key, value in notices.items() if key != "texts"}))
    if notices.get("issues") or any(c.get("issues") for c in notices["components"]):
        raise CaptureError("cargo_notice_coverage_incomplete")
    if source_snapshot(repo, expected_source) != before:
        raise CaptureError("source_changed_during_notice_collection")
    by_id = {c["id"]: c for c in inventory["components"]}
    if set(by_id) != {c["id"] for c in notices["components"]} or len(by_id) != len(notices["components"]):
        raise CaptureError("cargo_notice_component_set_mismatch")
    components = [public_component({**by_id[c["id"]], **c}, repo) for c in notices["components"]]
    referenced_texts = {n["sha256"] for c in components for n in c["notices"]}
    if referenced_texts != set(notices["texts"]):
        raise CaptureError("cargo_notice_text_set_mismatch")
    for digest, text in notices["texts"].items():
        if sha(text) != digest:
            raise CaptureError("cargo_notice_digest_mismatch")
        write_new(resources / "cargo" / "texts" / (digest + ".txt"), text)
    roots = [row for row in inventory["components"] if row["id"] == inventory["root_package_id"]]
    if len(roots) != 1:
        raise CaptureError("root_component_not_unique")
    binding = bind_artifacts(metadata, inventory, artifacts,
                             public_root_id=public_component(roots[0], repo)["component_id"])
    index = {"schema": "motionwright.cargo-build-notices/1", "source": expected_source,
             "source_tree": before["tree"], "target": target_triple,
             "root_package": root_package_name, "cargo_lock_sha256": sha(lock), "build_driver": driver,
             "capture": {"metadata_sha256": metadata_receipt["stdout_sha256"],
                         "build_stdout_sha256": build_receipt["stdout_sha256"],
                         "build_stderr_sha256": build_receipt["stderr_sha256"]},
             "components": components, **binding,
             "native_content_reviews": public_native_reviews(notices.get("native_content_reviews", []), by_id, repo),
             "coverage": {"cargo_units": "OBSERVED", "original_cargo_notices": "VERIFIED",
                          "native_static_dynamic_and_installer": "NOT_VERIFIED",
                          "frontend": "SEPARATE_INDEX_REQUIRED", "full_package_inventory": "NOT_VERIFIED"},
             "redistribution_permission": "NOT_INFERRED",
             "execution_boundary": "isolated CI job; pre/post byte revalidation is not an adversarial filesystem snapshot"}
    write_new(resources / "cargo" / "index.json", encoded(index))
    write_new(state / "capture-complete.json", encoded(
        {"source": expected_source, "cargo_index_sha256": sha(encoded(index)),
         "artifact_identities_sha256": binding["artifact_identities_sha256"],
         "resources": str(resources), "full_package_inventory": "NOT_VERIFIED"}))
    return index

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "cargo-home", "manifest", "state-directory", "resources-directory"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("expected-source", "target", "root-package"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--build-cwd", type=Path)
    parser.add_argument("--features", action="append", default=[])
    parser.add_argument("--no-default-features", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        capture_build(repo=args.repo, expected_source=args.expected_source, cargo=args.cargo,
                      cargo_home=args.cargo_home, target_triple=args.target,
                      root_package_name=args.root_package, manifest_path=args.manifest,
                      state_directory=args.state_directory, resources_directory=args.resources_directory,
                      build_command=command, metadata_features=args.features,
                      no_default_features=args.no_default_features, build_cwd=args.build_cwd)
    except (CaptureError, OSError, ValueError, KeyError) as error:
        print("dependency-capture-failed:" + str(error), file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
