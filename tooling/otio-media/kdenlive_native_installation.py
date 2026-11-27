"""Install one admitted KDE distribution in the native editor qualification job."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

from kdenlive_native_distribution import JOB, MAX_ARCHIVE_BYTES, ROOT, publish, selection

from candidate_smoke import binary_identity
from nsis_owned_files import _publication_guard
from source_checkout_observation import git, regular_file, require

MAX_EDITOR_BYTES = 128 * 1024 * 1024
MAX_PLIST_BYTES = 1024 * 1024


def install_windows(archive, owner, original):
    from windows_installer_lifecycle import observed_terminal
    from windows_installer_producer import (
        command_line, launch_once_and_observe, require_original_image,
    )

    destination = owner / "installed"
    require(not destination.exists() and not destination.is_symlink()
            and not any(character in str(destination) for character in ('"', "\r", "\n", "\0")),
            "Windows editor installation destination is unsafe")
    # Use the original Windows launcher and leave NSIS's last /D= unquoted,
    # including a destination with spaces. The archive stays held read-only.
    line = command_line(archive, ["/S", "/D=" + str(destination)])
    environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "PATH", "USERPROFILE",
                   "APPDATA", "LOCALAPPDATA", "ProgramData", "TEMP", "TMP") if name in os.environ}
    with _publication_guard(archive, archive.lstat(), original["sha256"], original["bytes"],
                            "Kdenlive Windows installer") as (_, check):
        def inspect(process, _identity):
            require_original_image(process, archive)

        with (owner / "installer.stdout").open("xb") as stdout, (owner / "installer.stderr").open("xb") as stderr:
            outcome = launch_once_and_observe(archive, line, environment, timeout=180,
                                              live_observer=inspect, stdout=stdout, stderr=stderr)
        publish(owner / "windows-installer-terminal.json", outcome)
        check()
    require(outcome["original_terminal"] == observed_terminal(0)
            and outcome["process"] is not None and not outcome["custody_errors"],
            "native Windows Kdenlive installation did not close successfully")
    return destination / "bin/kdenlive.exe"


def bundle_identity(app):
    info = app / "Contents/Info.plist"
    original = regular_file(info, MAX_PLIST_BYTES)
    metadata = plistlib.loads(info.read_bytes())
    require(regular_file(info, MAX_PLIST_BYTES) == original
            and metadata.get("CFBundleIdentifier") == "org.kde.Kdenlive"
            and metadata.get("CFBundleVersion") == "26.08.3"
            and metadata.get("CFBundleExecutable") == "kdenlive",
            "macOS distribution did not contain the pinned Kdenlive bundle")
    path = app / "Contents/MacOS/kdenlive"
    return path, {"plist": original, "binary": regular_file(path, MAX_EDITOR_BYTES)}


def install_macos(archive, owner, original):
    from ci_macos_package_metadata import mounted_device, readonly_mount_identity

    mount = owner / "mount"
    mount.mkdir(mode=0o700)
    destination = owner / "installed"
    destination.mkdir(mode=0o700)
    command = ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
               "-plist", "-mountpoint", str(mount), str(archive)]
    with _publication_guard(archive, archive.lstat(), original["sha256"], original["bytes"],
                            "Kdenlive macOS image") as (_, check):
        attach = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                check=True, timeout=60)
        require(0 < len(attach.stdout) <= 128 * 1024, "native DMG attach response exceeds bounds")
        device = mounted_device(attach.stdout, mount)
        try:
            mounted = readonly_mount_identity(mount, device)
            publish(owner / "macos-readonly-mount.json", mounted)
            [app] = [path for path in mount.iterdir() if path.name.casefold() == "kdenlive.app"]
            _, source = bundle_identity(app)
            staged = destination / app.name
            subprocess.run(["/usr/bin/ditto", str(app), str(staged)], stdin=subprocess.DEVNULL,
                           check=True, timeout=120)
            path, installed = bundle_identity(staged)
            require(installed == source and bundle_identity(app)[1] == source
                    and readonly_mount_identity(mount, device) == mounted,
                    "installed Kdenlive binary or original mount changed")
            check()
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                           stdin=subprocess.DEVNULL, check=True, timeout=45)
    require(not os.path.ismount(mount), "Kdenlive source image did not detach")
    return path


def install(runner, context, distribution):
    owner = runner / f"{JOB}-{context['run_id']}-1"
    archive = owner / distribution.filename
    receipt_path = owner / "distribution.json"
    receipt_original = regular_file(receipt_path, 8192)
    receipt = json.loads(receipt_path.read_bytes())
    original = regular_file(archive, MAX_ARCHIVE_BYTES)
    require(receipt.get("context") == context and receipt.get("archive") == distribution.filename
            and receipt.get("url") == distribution.url
            and receipt.get("status") == "VERIFIED_OFFICIAL_ARCHIVE"
            and receipt.get("original") == original and original["sha256"] == distribution.sha256
            and regular_file(receipt_path, 8192) == receipt_original,
            "Kdenlive archive no longer matches the admitted distribution")
    publish(owner / "installation-intent.json", {"context": context, "archive": original,
                                                "installation_attempts": 1, "automatic_retry": False})
    if context["runner_os"] == "Windows":
        executable = install_windows(archive, owner, original)
    else:
        executable = install_macos(archive, owner, original)
    require(executable.is_relative_to(owner / "installed"), "editor binary outside its installation")
    executable_original = regular_file(executable, MAX_EDITOR_BYTES)
    system, arch = (("windows", "x86_64") if context["runner_os"] == "Windows"
                    else ("macos", "arm64"))
    native = binary_identity(executable, system, arch)
    require((native["binary_bytes"], native["binary_sha256"])
            == (executable_original["bytes"], executable_original["sha256"])
            and regular_file(archive, MAX_ARCHIVE_BYTES) == original,
            "installed executable or original archive changed")
    executable_original["format"] = native["binary_format"]
    report = {"schema": "motionwright.kdenlive-native-installation/1", "context": context,
              "archive": original, "relative_executable": executable.relative_to(owner).as_posix(),
              "original_executable": executable_original}
    publish(owner / "installation.json", report)
    return report


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "native Kdenlive checkout differs from the workflow")
    print(json.dumps(install(Path(os.environ["RUNNER_TEMP"]), context, distribution), sort_keys=True))


if __name__ == "__main__":
    main()
