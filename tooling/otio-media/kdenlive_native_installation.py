"""Install one admitted KDE distribution in the native editor qualification job."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import stat
import subprocess
import sys

from kdenlive_native_distribution import JOB, MAX_ARCHIVE_BYTES, ROOT, publish, selection

from candidate_smoke import binary_identity
from nsis_owned_files import _publication_guard
from source_checkout_observation import git, regular_file, require

MAX_EDITOR_BYTES = 128 * 1024 * 1024
MAX_PLIST_BYTES = 1024 * 1024


def windows_user_installation_root():
    import ctypes
    from ctypes import wintypes
    from uuid import UUID

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    memory = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p),
    )
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    memory.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    memory.CoTaskMemFree.restype = None
    identifier = (ctypes.c_ubyte * 16).from_buffer_copy(
        UUID("5cd7aee2-2219-4a67-b85d-6c9ce15660cb").bytes_le,
    )
    raw = ctypes.c_void_p()
    try:
        status = shell.SHGetKnownFolderPath(identifier, 0x4000, None, ctypes.byref(raw))
        programs = (Path(ctypes.wstring_at(raw)) if status == 0 and raw.value
                    else Path(os.environ["LOCALAPPDATA"]) / "Programs")
    finally:
        if raw.value:
            memory.CoTaskMemFree(raw)
    require(programs.is_absolute() and len(programs.drive) == 2 and programs.drive[1] == ":"
            and programs == programs.resolve(strict=False),
            "Windows user program files folder is not a canonical local path")
    for path in (programs, *programs.parents):
        try:
            observed = path.lstat()
        except FileNotFoundError:
            continue
        require(stat.S_ISDIR(observed.st_mode)
                and not getattr(observed, "st_file_attributes", 0) & 0x400,
                "Windows user program files folder crosses a link or reparse point")
    return programs / "Kdenlive"


def windows_user_registration():
    import winreg

    results = []
    paths = {
        "product": r"Software\KDE e.V.\Kdenlive",
        "uninstall": r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Kdenlive",
    }
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        for kind, path in paths.items():
            try:
                handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with handle:
                names = (("root", "Install_Dir"), ("mode", "Install_Mode")) if kind == "product" else (
                    ("uninstall", "UninstallString"),
                )
                fields = {}
                for key, name in names:
                    try:
                        value, representation = winreg.QueryValueEx(handle, name)
                    except FileNotFoundError:
                        value, representation = None, None
                    fields[key] = value if representation == winreg.REG_SZ else None
                results.append(fields)
    return results


def install_windows(archive, owner, original, destination):
    from windows_installer_lifecycle import custody_error, observed_terminal
    from windows_updated_application import process_identity

    require(not destination.exists() and not destination.is_symlink()
            and not windows_user_registration(),
            "Windows Kdenlive is already registered or its destination already exists")
    # KDE Craft initializes INSTDIR through NSIS MultiUser in .onInit.
    # CurrentUser selects the runner account's genuine UserProgramFiles.
    line = subprocess.list2cmdline([str(archive), "/S", "/CurrentUser"])
    environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "PATH", "USERPROFILE",
                   "APPDATA", "LOCALAPPDATA", "ProgramData", "TEMP", "TMP") if name in os.environ}
    with _publication_guard(archive, archive.lstat(), original["sha256"], original["bytes"],
                            "Kdenlive Windows installer") as (_, check):
        original_terminal, identity, errors = observed_terminal(None), None, []
        with (owner / "installer.stdout").open("xb") as stdout, (owner / "installer.stderr").open("xb") as stderr:
            try:
                process = subprocess.Popen(line, executable=str(archive), cwd=owner, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                try:
                    identity = process_identity(process, archive)
                except Exception as error:
                    errors.append(custody_error("process_identity", error))
                try:
                    original_terminal = observed_terminal(process.wait(timeout=180))
                except subprocess.TimeoutExpired:
                    original_terminal = observed_terminal(None, timed_out=True)
                except Exception as error:
                    errors.append(custody_error("installer_wait", error))
            except OSError as error:
                errors.append(custody_error("installer_launch", error))
        outcome = {"original_terminal": original_terminal, "process": identity, "custody_errors": errors}
        publish(owner / "windows-installer-terminal.json", outcome)
        check()
    require(outcome["original_terminal"] == observed_terminal(0)
            and outcome["process"] is not None and not outcome["custody_errors"],
            "native Windows Kdenlive installation did not close successfully")
    registered = windows_user_registration()
    products = [record for record in registered if "root" in record]
    uninstallers = [record for record in registered if "uninstall" in record]
    require(products and all(isinstance(record["root"], str)
                             and Path(record["root"]) == destination
                             and record["mode"] == "CurrentUser" for record in products)
            and all(record["uninstall"] == '"' + str(destination / "uninstall.exe") + '"'
                    for record in uninstallers),
            "installed Kdenlive registration does not point to the selected current user folder")
    publish(owner / "windows-installed-location.json", {
        "installation_root": str(destination), "mode": "CurrentUser",
        "product_registration_count": len(products), "uninstaller_registration_count": len(uninstallers),
    })
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
    from native_host_installed_shared_state import ci_copy_original

    mount = owner / "mount"
    mount.mkdir(mode=0o700)
    destination = owner / "installed"
    destination.mkdir(mode=0o700)
    image = owner / "mount-input.dmg"
    # DiskImages can update source file metadata even during a read-only
    # mount. Bind a separate identical image to the original before attach.
    with _publication_guard(archive, archive.lstat(), original["sha256"], original["bytes"],
                            "Kdenlive macOS image") as (_, check):
        ci_copy_original(archive, image, MAX_ARCHIVE_BYTES, expected=original)
        initial_image = image.lstat()
        command = ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen",
                   "-plist", "-mountpoint", str(mount), str(image)]
        check()
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
        finally:
            subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                           stdin=subprocess.DEVNULL, check=True, timeout=45)
        final_image = image.lstat()
        require((initial_image.st_dev, initial_image.st_ino) == (final_image.st_dev, final_image.st_ino)
                and regular_file(image, MAX_ARCHIVE_BYTES) == original,
                "Kdenlive mounting copy changed")
        check()
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
        installation_root = windows_user_installation_root()
        executable = install_windows(archive, owner, original, installation_root)
        require(executable == installation_root / "bin/kdenlive.exe",
                "editor binary outside its registered installation")
    else:
        installation_root = owner
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
              "archive": original, "installation_root": str(installation_root),
              "relative_executable": executable.relative_to(installation_root).as_posix(),
              "original_executable": executable_original}
    publish(owner / "installation.json", report)
    return report


def windows_accessibility_context():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    user.GetProcessWindowStation.argtypes = []
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.restype = wintypes.BOOL
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user.GetUserObjectInformationW.restype = wintypes.BOOL

    def name(handle):
        text = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if handle and user.GetUserObjectInformationW(handle, 2, text, ctypes.sizeof(text), ctypes.byref(needed)):
            return text.value.casefold()
        return ""

    session = wintypes.DWORD()
    user_session = bool(kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) and session.value)
    interactive_station = name(user.GetProcessWindowStation()) == "winsta0"
    desktop = user.OpenInputDesktop(0, False, 0x0001)
    try:
        default_desktop = name(desktop) == "default"
    finally:
        if desktop:
            user.CloseDesktop(desktop)
    return {"user_session": user_session, "interactive_station": interactive_station,
            "default_input_desktop": default_desktop}


def macos_accessibility_context():
    import ctypes

    services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    services.AXIsProcessTrusted.argtypes = []
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    services.CGMainDisplayID.argtypes = []
    services.CGMainDisplayID.restype = ctypes.c_uint32
    return {"accessible_process": bool(services.AXIsProcessTrusted()),
            "visible_display": bool(services.CGMainDisplayID())}


def observe_host(runner, context):
    owner = runner / f"{JOB}-{context['run_id']}-1"
    owner.mkdir(mode=0o700)
    observed = (windows_accessibility_context() if context["runner_os"] == "Windows"
                else macos_accessibility_context())
    report = {"schema": "motionwright.kdenlive-host-accessibility/1", "context": context,
              "status": "AVAILABLE" if all(observed.values()) else "HOLD",
              "observed": observed, "installer_launches": 0, "editor_launches": 0}
    publish(owner / "accessibility.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--observe-host", action="store_true")
    arguments = parser.parse_args()
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "native Kdenlive checkout differs from the workflow")
    runner = Path(os.environ["RUNNER_TEMP"])
    if arguments.observe_host:
        report = observe_host(runner, context)
        print(json.dumps(report, sort_keys=True))
        raise SystemExit(0 if report["status"] == "AVAILABLE" else 1)
    print(json.dumps(install(runner, context, distribution), sort_keys=True))


if __name__ == "__main__":
    main()
