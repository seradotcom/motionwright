"""Observe Manim and the exact PyAV wheel loaded by the Linux Driver Host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--provision", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    import av
    import av._core
    import av.filter
    import av.logging
    import manim
    import manimpango

    root = Path(os.environ["SEMWRIGHT_TEST_MANIM_RUNTIME"]).resolve(strict=True)
    packages = (root / "site-packages").resolve(strict=True)
    private = args.provision
    if (manim.__version__ != "0.21.0" or manimpango.__version__ != "0.6.1"
            or av.__version__ != "15.0.0"):
        raise SystemExit("Installed Manim/ManimPango/PyAV versions differ from runtime schema 2")
    if not all(Path(module.__file__).resolve(strict=True).is_relative_to(packages)
               for module in (manim, manimpango, av, av._core)):
        raise SystemExit("Runtime imports escaped the installed bundle")
    if not {"buffer", "showinfo", "buffersink"} <= set(av.filter.filters_available):
        raise SystemExit("PyAV native metadata filters are unavailable")
    if not all(callable(getattr(av.logging, name, None)) for name in (
            "get_level", "set_level", "set_libav_level",
            "get_skip_repeated", "set_skip_repeated")):
        raise SystemExit("PyAV native logging observation API is unavailable")

    def file_identity(path):
        path = path.resolve(strict=True)
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        return {"path": str(path), "bytes": path.stat().st_size, "sha256": h.hexdigest()}

    wheel_info = json.loads((private / "pyav-wheel.json").read_bytes())
    wheel = private / "wheels" / wheel_info["filename"]
    observed_wheel = file_identity(wheel)
    if any(observed_wheel[key] != wheel_info[key] for key in ("bytes", "sha256")):
        raise SystemExit("Observed PyAV wheel identity changed")
    loaded = set()
    for line in Path("/proc/self/maps").read_text(encoding="utf-8").splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith("/"):
            path = Path(fields[5])
            if path.is_file() and path.resolve(strict=True).is_relative_to(packages):
                loaded.add(path.resolve(strict=True))
    native_files = []
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        if len(members) != len(set(members)):
            raise SystemExit("PyAV wheel contains duplicate members")
        for path in sorted(loaded):
            relative = path.relative_to(packages).as_posix()
            if relative not in members:
                continue
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            row = file_identity(path)
            wheel_sha = hashlib.sha256(archive.read(relative)).hexdigest()
            if wheel_sha != row["sha256"]:
                raise SystemExit("Loaded PyAV native bytes differ from the observed wheel")
            native_files.append({"path": relative, "bytes": row["bytes"],
                                 "sha256": row["sha256"], "wheel_sha256": wheel_sha})
    core = Path(av._core.__file__).resolve(strict=True).relative_to(packages).as_posix()
    if not native_files or core not in {row["path"] for row in native_files}:
        raise SystemExit("No original wheel binding for the loaded PyAV core")
    selected_python = Path(os.environ.get("SEMWRIGHT_TEST_PYTHON", sys.executable)).resolve(strict=True)
    if selected_python != Path(sys.executable).resolve(strict=True):
        raise SystemExit("Observed Python differs from the selected Driver Host interpreter")
    binaries = {"python": file_identity(selected_python)}
    versions = {"python": sys.version}
    for name in ("ffmpeg", "ffprobe"):
        selected = os.environ.get("SEMWRIGHT_TEST_" + name.upper()) or shutil.which(name)
        if not selected:
            raise SystemExit("Missing selected native executable: " + name)
        path = Path(selected).resolve(strict=True)
        binaries[name] = file_identity(path)
        versions[name] = subprocess.check_output(
            [str(path), "-version"], text=True, timeout=20).splitlines()[0]
    rows = []
    for path in sorted(packages.rglob("*")):
        if path.is_file() and not path.is_symlink():
            identity = file_identity(path)
            rows.append((path.relative_to(root).as_posix(),
                         identity["bytes"], identity["sha256"]))
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    pin = json.loads(Path("SOURCE_LOCK.json").read_bytes())["dependencies"]["semwright"]["revision"]
    receipt = {
        "schema": "motionwright.manim.runtime-inventory/2",
        "context": {"source_sha": os.environ["GITHUB_SHA"], "semwright_sha": pin,
                    "run_id": os.environ["GITHUB_RUN_ID"],
                    "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]},
        "manim_version": manim.__version__, "pyav_version": av.__version__,
        "libav_versions": {name: list(version) for name, version in sorted(av.library_versions.items())},
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "runtime_receipt_sha256": file_identity(root / "runtime.json")["sha256"],
        "binaries": binaries, "binary_versions": versions,
        "pyav_wheel": wheel_info, "pyav_native_files": native_files,
        "files": len(rows), "inventory_sha256": hashlib.sha256(payload).hexdigest(),
        "native_render_performed": False,
    }
    destination = args.output.parent
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    args.output.chmod(0o600)


if __name__ == "__main__":
    main()
