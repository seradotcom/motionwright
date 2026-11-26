"""First-party inert byte fixtures for the actual Desktop offline copier.

Only the original entry/lock sources are real runtime code. Node/browser/tool
files are host ELF/Mach-O headers with no program body and must never be executed. This is
filesystem/SQLite coverage, not runtime readiness or Native acceptance.
"""
from pathlib import Path
import hashlib
import importlib.util
import json

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("runtime_profile_bundle", REPO / "tooling/runtime_profile_bundle.py")
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)


def executable_header():
    raw = bytearray(128)
    if bundle.host_platform() == "macos-aarch64":
        raw[:4] = b"\xcf\xfa\xed\xfe"
        raw[4:8] = b"\x0c\0\0\x01"
        raw[12:16] = b"\x02\0\0\0"
    else:
        raw[:6] = b"\x7fELF\x02\x01"
        raw[18] = 0x3e
    return bytes(raw)


def write(root, relative, data, executable=False):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o700 if executable else 0o600)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def create(sdk_runtime: Path, destination: Path):
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    mc, hf = destination / "motion_canvas", destination / "hyperframes"
    mc.mkdir(mode=0o700)
    for relative, expected in bundle.MC_PINS.items():
        data = (sdk_runtime / Path(relative).name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        write(mc, relative, data)
    write(mc, "tools/node", executable_header())
    write(mc, "runtime/node_modules/playwright-core/.local-browsers/firefox-0" + bundle.firefox_suffix(bundle.host_platform()), executable_header())
    write(mc, "runtime/empty-data.txt", b"")
    mc_report = bundle.create_description(mc, "motion_canvas")
    assert mc_report["executed"] is False and mc_report["downloaded"] is False and mc_report["locks_modified"] is False
    if bundle.host_platform() == "macos-aarch64":
        return mc, None
    hf.mkdir(mode=0o700)
    runner = (REPO / "native/hyperframes/semwright-hyperframes-runner.mjs").read_bytes()
    metadata = (REPO / "tooling/hyperframes/fixtures/hyperframes-0.8.181-original.json").read_bytes()
    assert hashlib.sha256(runner).hexdigest() == bundle.HF_RUNNER
    assert hashlib.sha256(metadata).hexdigest() == bundle.HF_METADATA
    npm = encoded({"packages": {"node_modules/hyperframes": {
        "version": "0.8.181", "resolved": bundle.HF_URL, "integrity": bundle.HF_INTEGRITY}}})
    package = encoded({"name": "hyperframes", "version": "0.8.181"})
    rows = []
    for relative, data, role in [
        ("semwright-hyperframes-runner.mjs", runner, "runner"),
        ("origin/hyperframes-0.8.181.json", metadata, "dependency"),
        ("package-lock.json", npm, "package-lock"),
        ("node_modules/hyperframes/package.json", package, "dependency"),
        ("node_modules/hyperframes/bin/hyperframes.mjs", b"// Inert source fixture. Never execute.\n", "hyperframes-cli"),
        ("tools/chrome", executable_header(), "browser"), ("tools/ffmpeg", executable_header(), "ffmpeg"), ("tools/ffprobe", executable_header(), "ffprobe"),
    ]:
        write(hf, "runtime/" + relative, data, role in {"browser", "ffmpeg", "ffprobe"})
        rows.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "role": role})
    write(hf, "tools/node", executable_header())
    lock = {"schema": "semwright.hyperframes.runtime-lock/1", "hyperframes_version": "0.8.181",
            "hyperframes_commit": "ae02e5aceff4742e5d72c20f0859eb3e6b449035", "node_version": "v22.22.0",
            "node_sha256": hashlib.sha256(executable_header()).hexdigest(), "browser_version": "inert browser byte fixture",
            "ffmpeg_version": "inert ffmpeg byte fixture", "ffprobe_version": "inert ffprobe byte fixture", "files": rows}
    write(hf, "runtime/runtime-lock.json", encoded(lock))
    hf_report = bundle.create_description(hf, "hyperframes")
    for report in (mc_report, hf_report):
        assert report["executed"] is False and report["downloaded"] is False and report["locks_modified"] is False
    return mc, hf
