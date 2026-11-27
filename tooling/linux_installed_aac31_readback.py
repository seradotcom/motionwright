"""Recover the closed synthetic AAC31 original without granting another render."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import io
import lzma
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile

import ci_private_evidence as seal
from linux_hybrid_original_receipt import Source, original_master
import native_host_installed_shared_state as installed
import native_mlt_sequence_e2e as native
from source_checkout_observation import regular_file

MAX_COMPRESSED = 350_000
MAX_EXPANDED = 8 * 1024 * 1024
PARTS = 10
APP_DATA = "gui/data/com.seradotcom.motionwright/"
CONNECTION = "o/config/motionwright-connection.json"
WAVE_SHA = "6711ccfcb80db90c9ef9ff554400a2a7376db267523e73e43aebdfc4e420ef53"
SHA = re.compile(r"[0-9a-f]{64}")
RUN_ID = re.compile(r"[1-9][0-9]{0,19}")


def original_members(stem, cas):
    installed.need(re.fullmatch(r"mw-hybrid-[0-9a-f]{20}", stem)
                   and re.fullmatch(r"[0-9a-f-]{36}", cas), "invalid retained source paths")
    sources = {
        APP_DATA + "motionwright.sqlite3" + suffix for suffix in ("", "-wal", "-shm")
    }
    sources.update({
        APP_DATA + f".motionwright-cas-{cas}/{leaf}" for leaf in ("catalog.json", "root.lock")
    })
    sources.update({APP_DATA + "blobs/.motionwright-cas-root-v1.json",
                    APP_DATA + f"blobs/sha256/67/{WAVE_SHA}", CONNECTION})
    sources.update({"o/output/" + stem + suffix for suffix in (
        "-master.mp4", "-source.wav", "-visual.mkv", "-master.mp4.color-observation.json",
        "-master.mp4.native-input.mlt",
    )})
    return sources


def recover_inputs(runner, run, expected_sha):
    installed.need(RUN_ID.fullmatch(run) and run != os.environ["GITHUB_RUN_ID"]
                   and SHA.fullmatch(expected_sha), "invalid retained render selection")
    parts = [os.environ.pop(f"AAC31_ORIGINAL_{part}", "") for part in range(PARTS)]
    while parts and not parts[-1]:
        parts.pop()
    installed.need(parts and all(0 < len(part) <= 48_000 for part in parts),
                   "incomplete retained original")
    packed = base64.b64decode("".join(parts), validate=True)
    installed.need(len(packed) <= MAX_COMPRESSED and seal.sha(packed) == expected_sha,
                   "retained original transport digest differs")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
    raw = decoder.decompress(packed, max_length=MAX_EXPANDED + 1)
    installed.need(decoder.eof and not decoder.unused_data and len(raw) <= MAX_EXPANDED,
                   "retained original exceeds its expansion budget")
    root = runner / ("mwa-" + run)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        by_name = {member.name: member for member in members}
        installed.need(len(members) == len(by_name) == 14
                       and all(member.isfile() and member.sparse is None for member in members)
                       and sum(member.size for member in members) <= MAX_EXPANDED,
                       "unexpected retained original member set")
        index = by_name.pop("original.json")
        installed.need(index.size <= 8192, "retained original manifest exceeds its budget")
        meta = seal.strict(archive.extractfile(index).read())
        installed.need(set(meta) == {"schema", "render_run_id", "render_source_sha", "render_seal_sha256",
                                     "source", "project_title", "stem", "cas", "master", "files"}
                       and meta["schema"] == "motionwright.aac31.retained-original/1"
                       and meta["render_run_id"] == run
                       and re.fullmatch(r"[0-9a-f]{40}", meta["render_source_sha"])
                       and SHA.fullmatch(meta["render_seal_sha256"])
                       and meta["project_title"] == "Phase 1 AAC 31-frame qualification fixture",
                       "invalid retained original identity")
        expected = original_members(meta["stem"], meta["cas"])
        installed.need(set(by_name) == set(meta["files"]) == expected, "unexpected retained original roster")
        verified = {}
        for name, member in by_name.items():
            with archive.extractfile(member) as original:
                data = original.read()
            installed.need(meta["files"][name] == {"bytes": len(data), "sha256": seal.sha(data)},
                           "retained original member was replaced")
            verified[name] = data
    # Materialize the namespace only after authenticating the complete roster.
    root.mkdir(mode=0o700)
    for name, data in verified.items():
        target = root / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        seal.new_bytes(target, data)
    database = root / APP_DATA / "motionwright.sqlite3"
    output = root / "o/output"
    master = original_master(database, Source(**meta["source"]), output)
    installed.need(meta["master"] == {"receipt_id": master.id, "sha256": master.sha256, "bytes": master.size}
                   and master.path.name == meta["stem"] + "-master.mp4", "retained mux receipt identity differs")
    return root, meta


@contextmanager
def observation_connection(root, meta, evidence):
    connection = root / CONNECTION
    record = installed.read_json(connection, 64 * 1024)
    cli = native.SW_BINS / "semwright"
    daemon_bin = native.SW_BINS / "semwrightd"
    home, state, runtime = [root / "o" / name for name in ("home", "state", "runtime")]
    for folder in (home, state, runtime):
        folder.mkdir(mode=0o700)
    socket = runtime / "broker.sock"
    session = runtime / "motionwright.session"
    installed.need(record == {
        "schema": "motionwright-semwright-connection/1",
        "executable": str(cli), "executable_sha256": regular_file(cli, installed.MAX_INPUT_BYTES)["sha256"],
        "socket": str(socket), "session_file": str(session), "output_root": str(root / "o/output"),
        "resource": "project:" + meta["source"]["project"],
    }, "read-only launch could not recover the original CLI and connection identity")
    config = root / "o/config/readback.toml"
    seal.new_bytes(config, b'drivers = []\ndriver_network = false\n\n[policy]\nprofile = "observe"\nallow = []\n')
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home),
           "XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(state), "XDG_CONFIG_HOME": str(config.parent)}
    log_path = evidence / "readback-broker.log"
    with log_path.open("xb") as log:
        daemon = subprocess.Popen(
            [str(daemon_bin), "--config", str(config), "--socket", str(socket)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True,
        )
        try:
            native.wait_for_socket(daemon, socket, log_path)
            native.run_json([str(cli), "--socket", str(socket), "--session-file", str(session),
                             "--json", "capabilities", "search", "project"], env=env)
            info = session.lstat()
            installed.need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                           and info.st_mode & 0o777 == 0o600 and info.st_size > 0,
                           "read-only broker did not issue a private local session")
            yield connection
        finally:
            native.close_native_owner(daemon, log)


def run_readback(args, inputs, evidence, run, sha, run_original):
    root, meta = recover_inputs(args.namespace.parent, run, sha)
    with installed.supervise_own_processes():
        original = installed.extract_installed_bundle(
            args, evidence=evidence, candidate_source_sha=inputs["candidate_origin"]["source_sha"],
        )
    installed.publish(evidence / "retained-render.json", {
        "run_id": run, "source_sha": meta["render_source_sha"], "seal_sha256": meta["render_seal_sha256"],
        "restore_sha256": sha, "source": meta["source"], "master": meta["master"], "render_dispatch_limit": 0,
    })
    output = root / "o/output"
    wave = output / (meta["stem"] + "-source.wav")
    ffmpeg, ffprobe = Path("/usr/bin/ffmpeg"), Path("/usr/bin/ffprobe")
    desktop = evidence / "desktop"
    desktop.mkdir(mode=0o700)
    request = evidence / "desktop-request.json"
    with installed.supervise_own_processes(), installed.owned_process_scope(evidence / "broker-closure.json"):
        with observation_connection(root, meta, evidence) as connection:
            installed.publish(request, {
                "source_sha": args.source_sha, "semwright_sha": args.semwright_sha,
                "app_run": str(args.app_run), "binary": str(args.installed_binary),
                "profile": str(root / "gui"), "database": str(root / APP_DATA / "motionwright.sqlite3"),
                "output": str(output), "evidence": str(desktop), "project_title": meta["project_title"],
                "source": meta["source"], "connection": str(connection), "connection_sha256": native.digest(connection),
                "wave": str(wave), "wave_sha256": WAVE_SHA, "ffmpeg": str(ffmpeg),
                "ffmpeg_sha256": native.digest(ffmpeg), "ffprobe": str(ffprobe),
                "ffprobe_sha256": native.digest(ffprobe), "seconds": 120,
                "retained_master": {"render_run_id": run, **meta["master"]},
            })
            run_original(["/usr/bin/python3", "-B", "-E", "-s",
                          str(installed.ROOT / "tooling/linux_installed_aac31.py"),
                          "--request", str(request), "--verify-retained-original"],
                         evidence, "installed-desktop", env=dict(os.environ), seconds=210)
    installed.need(original["extracted_tree"] == installed.installed_bundle_inventory(args.app_run.parent),
                   "readback changed the installed program")
    return installed.read_json(desktop / "installed-aac31.json")
