#!/usr/bin/env python3
"""Provision the reviewed public HyperFrames distribution before native work.

This is an Actions setup consumer of the existing offline finalizer. It never
opens a Broker, renders, retries a process or grants additional Host authority.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

import ci_private_evidence as custody
import native_hyperframes_actions as actions
from native_hyperframes_evidence import require, sha

SCHEMA = "motionwright.hyperframes.runtime-intake/1"
INTEGRITY = "sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw=="
REVISION_PROGRAM = """
const path = require('node:path');
const {createRequire} = require('node:module');
const req = createRequire(path.join(process.argv[1], 'package.json'));
const entry = req.resolve('puppeteer-core');
const value = req(path.join(path.dirname(entry), 'revisions.js')).PUPPETEER_REVISIONS;
process.stdout.write(JSON.stringify({chrome:value.chrome}) + '\\n');
"""


def chrome_version(raw):
    value = json.loads(raw)
    require(type(value) is dict and set(value) == {"chrome"}
            and type(value["chrome"]) is str
            and re.fullmatch(r"[1-9][0-9]{1,3}\.[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", value["chrome"]),
            "resolved_puppeteer_chrome_revision")
    return value["chrome"]


def chrome_url(version):
    require(chrome_version(json.dumps({"chrome": version})) == version, "chrome_version")
    return ("https://storage.googleapis.com/chrome-for-testing-public/"
            + version + "/linux64/chrome-linux64.zip")


def archive_entries(archive):
    rows, seen, total = [], set(), 0
    for row in archive.infolist():
        path = row.filename.rstrip("/")
        require(path not in seen and (path.startswith("chrome-linux64/")
                or path == "chrome-linux64" and row.is_dir())
                and actions.owner_provision.relative(path),
                "chrome_archive_namespace")
        seen.add(path)
        mode = row.external_attr >> 16
        require(not stat.S_ISLNK(mode) and not row.flag_bits & 1,
                "chrome_archive_redirect_or_encryption")
        if row.is_dir():
            require(row.file_size == 0, "chrome_directory_payload")
        else:
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG)
                    and 0 < row.file_size <= 512 * 1024 * 1024,
                    "chrome_archive_regular_file")
            total += row.file_size
        require(len(rows) < 30000 and total <= 1024 * 1024 * 1024,
                "chrome_archive_budget")
        rows.append(row)
    require(any(row.filename == "chrome-linux64/chrome" and not row.is_dir() for row in rows),
            "chrome_archive_executable_absent")
    return rows


def execute(label, argv, owner, env, *, timeout=300):
    # Setup commands reserve originals independently; they cannot be replayed.
    custody.new_json(owner / (label + ".intent.json"), {
        "schema": "motionwright.hyperframes.runtime-setup-command/1",
        "label": label, "argv_sha256": sha(custody.json_bytes(argv)),
        "automatic_retry": False, "native_render_executed": False})
    output, error = owner / (label + ".stdout"), owner / (label + ".stderr")
    with output.open("xb") as out, error.open("xb") as err:
        result = subprocess.run(argv, cwd=owner, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, check=False, timeout=timeout)
        out.flush()
        err.flush()
        os.fsync(out.fileno())
        os.fsync(err.fileno())
    stdout = custody.regular(output, 4 * 1024 * 1024)[0]
    stderr = custody.regular(error, 4 * 1024 * 1024)[0]
    custody.new_json(owner / (label + ".exit.json"), {
        "schema": "motionwright.hyperframes.runtime-setup-exit/1",
        "exit_code": result.returncode, "stdout_sha256": sha(stdout),
        "stdout_bytes": len(stdout), "stderr_sha256": sha(stderr),
        "stderr_bytes": len(stderr), "automatic_retry": False,
        "native_render_executed": False})
    require(result.returncode == 0, "runtime_setup_command_failed")
    return stdout


def copy_original(source, target, executable=False):
    raw = custody.regular(source, 512 * 1024 * 1024)[0]
    require(len(raw) > 0, "runtime_setup_empty_original")
    custody.new_bytes(target, raw)
    if executable:
        target.chmod(0o700)
    return {"sha256": sha(raw), "bytes": len(raw)}


def system_dependencies(outputs):
    paths = set()
    for output in outputs:
        text = output.decode("utf-8", errors="strict")
        require("not found" not in text, "runtime_system_dependency_missing")
        for line in text.splitlines():
            match = re.search(r"(?:=>\s+)?(/[^\s()]+)\s+\(0x[0-9a-fA-F]+\)", line)
            if match:
                paths.add(Path(match.group(1)).resolve(strict=True))
    require(0 < len(paths) <= 256, "runtime_system_dependency_roster")
    rows = []
    for path in sorted(paths):
        raw = custody.regular(path, 256 * 1024 * 1024)[0]
        require(len(raw) > 0, "runtime_system_dependency_empty")
        rows.append({"name": path.name, "path": str(path), "sha256": sha(raw), "bytes": len(raw)})
    return {"ldd_only": True, "immutable_system_closure_attested": False, "files": rows}


def run(options):
    current = actions.context()
    bindings = actions.source_bindings(current)
    for root in (options.runtime, options.evidence):
        actions.require_directory(root.parent)
        require(root.is_absolute() and not root.exists() and not root.is_relative_to(actions.ROOT),
                "fresh_runtime_setup_root")
    require(not options.runtime.is_relative_to(options.evidence)
            and not options.evidence.is_relative_to(options.runtime), "runtime_setup_roots_overlap")
    node = Path(options.node).resolve(strict=True)
    ffmpeg = Path(options.ffmpeg).resolve(strict=True)
    ffprobe = Path(options.ffprobe).resolve(strict=True)
    npm = shutil.which("npm")
    ldd = shutil.which("ldd")
    require(npm is not None and ldd is not None, "runtime_setup_tools_absent")
    options.runtime.mkdir(mode=0o700)
    options.evidence.mkdir(mode=0o700)
    work, runtime = options.evidence, options.runtime
    for name in ("home", "npm-cache"):
        (work / name).mkdir(mode=0o700)
    custody.new_bytes(work / "empty-user-npmrc", b"")
    custody.new_bytes(work / "empty-global-npmrc", b"")
    env = {key: value for key, value in os.environ.items()
           if key not in {"NODE_OPTIONS", "NODE_PATH", "NPM_TOKEN", "NODE_AUTH_TOKEN"}
           and not key.lower().startswith("npm_config_")}
    env.update(HOME=str(work / "home"), NPM_CONFIG_USERCONFIG=str(work / "empty-user-npmrc"),
               NPM_CONFIG_GLOBALCONFIG=str(work / "empty-global-npmrc"),
               NPM_CONFIG_CACHE=str(work / "npm-cache"), PUPPETEER_SKIP_DOWNLOAD="true",
               NPM_CONFIG_AUDIT="false", NPM_CONFIG_FUND="false", NPM_CONFIG_BIN_LINKS="false")
    copy_original(actions.ROOT / "tooling/hyperframes/package.json", runtime / "package.json")
    copy_original(actions.ROOT / "native/hyperframes/semwright-hyperframes-runner.mjs",
                  runtime / "semwright-hyperframes-runner.mjs")
    (runtime / "origin").mkdir(mode=0o700)
    copy_original(actions.ROOT / "tooling/hyperframes/fixtures/hyperframes-0.8.181-original.json",
                  runtime / "origin/hyperframes-0.8.181.json")
    flags = ["--prefix", str(runtime), "--ignore-scripts", "--bin-links=false",
             "--no-audit", "--no-fund", "--loglevel=error", "--registry=https://registry.npmjs.org"]
    execute("npm-resolve", [npm, "install", "--package-lock-only", *flags], work, env)
    execute("npm-install", [npm, "ci", "--omit=dev", *flags], work, env, timeout=600)
    version = chrome_version(execute("puppeteer-revision",
        [str(node), "--eval", REVISION_PROGRAM, str(runtime)], work, env, timeout=30))
    url = chrome_url(version)
    custody.new_json(work / "chrome-download.intent.json", {
        "schema": "motionwright.hyperframes.chrome-intake/1", "url": url,
        "automatic_retry": False, "native_render_executed": False})
    archive_path = work / "chrome-linux64.zip"
    total = 0
    deadline = time.monotonic() + 300
    with urllib.request.urlopen(url, timeout=60) as response, archive_path.open("xb") as output:
        require(response.geturl() == url and response.status == 200, "chrome_original_endpoint")
        while data := response.read(1024 * 1024):
            total += len(data)
            require(total <= 512 * 1024 * 1024 and time.monotonic() < deadline,
                    "chrome_download_budget")
            output.write(data)
        output.flush()
        os.fsync(output.fileno())
    archive_raw = custody.regular(archive_path, 512 * 1024 * 1024)[0]
    require(0 < len(archive_raw) == total, "chrome_download_original")
    with zipfile.ZipFile(archive_path) as archive:
        rows = archive_entries(archive)
        for row in rows:
            target = runtime / row.filename.rstrip("/")
            if row.is_dir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                raw = archive.read(row)
                require(len(raw) == row.file_size, "chrome_archive_entry_length")
                custody.new_bytes(target, raw)
                if row.external_attr >> 16 & 0o111:
                    target.chmod(0o700)
    (runtime / "tools").mkdir(mode=0o700)
    copy_original(ffmpeg, runtime / "tools/ffmpeg", executable=True)
    copy_original(ffprobe, runtime / "tools/ffprobe", executable=True)
    roles = {"browser": runtime / "chrome-linux64/chrome",
             "ffmpeg": runtime / "tools/ffmpeg", "ffprobe": runtime / "tools/ffprobe"}
    versions, tools = {}, {}
    for role, path in roles.items():
        raw = execute("version-" + role, [str(path), "--version" if role == "browser" else "-version"],
                      work, env, timeout=30)
        lines = raw.decode("utf-8", errors="strict").splitlines()
        require(lines and 0 < len(lines[0]) <= 512, "actual_tool_version")
        versions[role] = lines[0]
        tools[role] = {**actions.bound_file(path), "version": lines[0],
                       "relative_path": path.relative_to(runtime).as_posix()}
    ldd_outputs = [execute("ldd-" + role, [ldd, str(path)], work, env, timeout=30)
                   for role, path in {"node": node, **roles}.items()]
    system = system_dependencies(ldd_outputs)
    command = [str(node), str(actions.ROOT / "tooling/hyperframes/prepare-runtime.mjs"),
               "--runtime", str(runtime)]
    for role, path in roles.items():
        command.extend(["--" + role, path.relative_to(runtime).as_posix(),
                        "--" + role + "-version", versions[role]])
    finalized = json.loads(execute("offline-finalizer", command, work, env, timeout=300))
    lock = custody.regular(runtime / "runtime-lock.json", 8 * 1024 * 1024)[0]
    require(finalized["runtime_lock_sha256"] == sha(lock)
            and finalized["native_executed"] is False, "runtime_finalizer_original")
    node_version = execute("version-node", [str(node), "--version"], work, env, timeout=30).decode().strip()
    result = {"schema": SCHEMA, "context": current, "status": "RUNTIME_FINALIZED",
              "source_bindings_sha256": sha(custody.json_bytes(bindings)),
              "hyperframes_version": actions.owner_provision.VERSION,
              "hyperframes_commit": actions.owner_provision.COMMIT,
              "public_distribution_integrity": INTEGRITY,
              "runtime_lock_sha256": sha(lock),
              "npm_lock_sha256": sha(custody.regular(runtime / "package-lock.json", 8 * 1024 * 1024)[0]),
              "node": {**actions.bound_file(node), "version": node_version},
              "tools": tools,
              "chrome_archive": {"url": url, "version": version, "sha256": sha(archive_raw),
                                 "bytes": len(archive_raw)},
              "system_dependency_observation": system,
              "native_render_executed": False, "automatic_retry": False}
    custody.new_json(work / "runtime-intake.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "evidence", "node", "ffmpeg", "ffprobe"):
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        run(parser.parse_args())
    except Exception:
        print("hyperframes-runtime-intake=NOT_VERIFIED; automatic_retry=false")
        return 1
    print("hyperframes-runtime-intake=RUNTIME_FINALIZED; native_render_executed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
