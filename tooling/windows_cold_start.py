"""Real installed Windows NSIS cold-boot/relaunch smoke, GitHub runner only.

Inspect the actually installed x86_64 PE (never a source checkout executable),
observe a real top-level Motionwright HWND, and read the same committed SQLite
project twice across two separate process lifetimes. Does not connect to,
provision, approve, or simulate a Semwright provider. No human PASS claim.
"""
from __future__ import annotations

import argparse
import ctypes
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
EXPECTED_FIRST_UI_TITLE = "motionwright ci first use"
EXPECTED_SCHEMA = 2


class ColdStartError(RuntimeError):
    """Sanitized technical failure, no executable argv or source media."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ColdStartError(reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_installed_candidate(receipt_file: Path, executable: Path, source_sha: str) -> dict:
    require(SHA40.fullmatch(source_sha) is not None, "Expected source SHA is malformed")
    require(receipt_file.is_file() and not receipt_file.is_symlink(),
            "Exact-source Windows candidate receipt is missing")
    require(executable.is_file() and not executable.is_symlink(),
            "Installed Windows candidate executable is unavailable")
    require(executable.suffix.lower() == ".exe",
            "Installed Windows candidate has the wrong executable type")
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 1 and receipt.get("bundle_kind") == "nsis",
            "Windows NSIS candidate receipt has the wrong schema or bundle")
    candidate = receipt.get("candidate")
    inspection = receipt.get("automated_inspection")
    require(isinstance(candidate, dict) and isinstance(inspection, dict),
            "Windows NSIS candidate is missing verified source evidence")
    require(candidate.get("platform") == "windows"
            and candidate.get("application") == "Motionwright"
            and candidate.get("source_revision") == source_sha,
            "Installed Windows candidate does not belong to the requested source SHA")
    binary = inspection.get("binary")
    require(inspection.get("method") == "nsis_silent_current_user"
            and inspection.get("status") == "PASS"
            and isinstance(binary, dict),
            "Windows NSIS installation has no completed technical smoke")
    require(binary.get("binary_filename") == executable.name
            and binary.get("binary_bytes") == executable.stat().st_size
            and binary.get("binary_format") == "PE64_x86_64",
            "Installed Windows executable identity changed since packaging")
    digest = sha256_file(executable)
    require(SHA64.fullmatch(digest) is not None
            and digest == binary.get("binary_sha256"),
            "Installed Windows executable bytes disagree with its signed-off CI receipt")
    claims = receipt.get("claims")
    require(isinstance(claims, dict)
            and claims.get("human_install_acceptance") == "NOT_RUN"
            and claims.get("runtime_renderer_acceptance") == "SEPARATE_GATE"
            and claims.get("release_published") is False,
            "Windows NSIS smoke must not claim release or human approval")
    return {"source_sha": source_sha, "installed_binary_sha256": digest}


def read_project_snapshot(database: Path) -> dict:
    require(database.is_file() and not database.is_symlink(),
            "Installed application did not persist a regular SQLite database")
    require(0 < database.stat().st_size < 64 * 1024 * 1024,
            "Installed project database violates first-use size bounds")
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
            require(db.execute("PRAGMA quick_check").fetchone() == ("ok",),
                    "Installed SQLite project integrity check failed")
            rows = db.execute(
                "SELECT id, generation, revision, title, document_json FROM projects ORDER BY id"
            ).fetchall()
    except sqlite3.Error as error:
        raise ColdStartError("Installed desktop did not produce a valid project store") from error
    require(len(rows) == 1, "Installed fresh-run project count is not exactly one")
    project_id, generation, revision, title, document_json = rows[0]
    try:
        uuid.UUID(project_id)
        uuid.UUID(generation)
        encoded = document_json.encode("utf-8")
        require(len(encoded) <= MAX_DOCUMENT_BYTES,
                "Installed first-use project JSON is too large")
        doc = json.loads(document_json)
    except (ValueError, AttributeError, TypeError) as error:
        raise ColdStartError("Installed first-use project identity is malformed") from error
    require(isinstance(doc, dict)
            and doc.get("id") == project_id
            and doc.get("generation") == generation
            and doc.get("revision") == revision
            and doc.get("title") == title,
            "SQLite source project identity conflicts with saved JSON")
    require(title == EXPECTED_FIRST_UI_TITLE and type(revision) is int and revision >= 0,
            "Installed first-use project was not initialized as expected")
    return {
        "project_id": project_id,
        "generation": generation,
        "revision": revision,
        "title": title,
        "document_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def known_databases(appdata_roots: list[Path]) -> list[Path]:
    """Inspect only selected Windows app-data roots, not arbitrary user files."""
    candidates: set[Path] = set()
    for root in appdata_roots:
        if not root.is_dir():
            continue
        for item in root.glob("*/motionwright.sqlite3"):
            if item.name == "motionwright.sqlite3" and item.parent.name.lower() == "com.seradotcom.motionwright":
                candidates.add(item)
        direct = root / "motionwright.sqlite3"
        if direct.exists():
            candidates.add(direct)
    return sorted(candidates)


def _empty_library_ready(database: Path) -> bool:
    """Only a fresh, empty real SQLite store permits one native GUI Create."""
    if not database.is_file() or database.is_symlink():
        return False
    try:
        with closing(sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True, timeout=3,
        )) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            return (
                connection.execute("PRAGMA quick_check(1)").fetchone() == ("ok",)
                and connection.execute("PRAGMA user_version").fetchone() == (EXPECTED_SCHEMA,)
                and connection.execute("SELECT COUNT(*) FROM projects").fetchone() == (0,)
            )
    except sqlite3.Error:
        return False


def _create_first_project_in_native_window(hwnd: int) -> None:
    """One accessible real WebView2 action; never write the database directly."""
    require(os.name == "nt" and os.getenv("GITHUB_ACTIONS") == "true",
            "Real first-use UI action is restricted to disposable CI Windows")
    script = Path(__file__).with_name("windows_first_project_ui.ps1")
    require(script.is_file() and not script.is_symlink(),
            "Native project-creation UI script is unavailable")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-WindowHandle", str(hwnd), "-ProjectTitle", EXPECTED_FIRST_UI_TITLE],
            capture_output=True, text=True, timeout=21, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ColdStartError("Native accessible project-creation action was not confirmed") from error
    if result.returncode != 0 or result.stdout.strip() != "VISIBLE_PROJECT_CREATE_ACTIVATED":
        # Only a closed technical stage enum enters the public runner log.
        # Never include raw UI automation stderr, arguments, process paths
        # or arbitrary text from a downloaded/installed binary.
        safe_stderr = getattr(result, "stderr", "")
        if not isinstance(safe_stderr, str):
            safe_stderr = ""
        # Only the script's own closed taxonomy is admitted, never arbitrary
        # PowerShell error details, host paths, titles, or project data.
        allowed = {
            "PRECHECK", "UIA_LOAD", "WINDOW_LOOKUP", "ELEMENT_QUERY",
            "FORM_FIND", "FORM_AMBIGUOUS", "INPUT_NOT_FOCUSABLE",
            "INPUT_ACTIVATE", "INPUT_FOCUS", "INPUT_FOCUS_VERIFY",
            "INPUT_VALUE_PATTERN", "INPUT_INITIAL_VALUE", "INPUT_KEYSTROKE",
            "INPUT_VALUE_READBACK", "BUTTON_ENABLE",
            "INVOKE_LOOKUP", "INVOKE_UNKNOWN",
        }
        markers = re.findall(
            r"(?m)^MOTIONWRIGHT_UI_STAGE:([A-Z_]+)\s*$", safe_stderr,
        )
        stage = markers[0] if len(markers) == 1 and markers[0] in allowed else "ACTION_CONFIRMATION_UNAVAILABLE"
        raise ColdStartError(
            "Native accessible project-creation action was not confirmed: " + stage
        )


def windows_motionwright_window(process_id: int) -> int | None:
    """Find an actually visible title-owned HWND, never match diagnostic logs."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    windows: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    @callback_type
    def callback(hwnd: int, _: int) -> int:
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == process_id and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if 0 < length <= 200:
                name = ctypes.create_unicode_buffer(length + 1)
                copied = user32.GetWindowTextW(hwnd, name, length + 1)
                if copied == len("Motionwright") and name.value == "Motionwright":
                    windows.append(int(hwnd))
        return 1

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    if not user32.EnumWindows(callback, 0):
        raise ColdStartError("Windows could not enumerate candidate application windows")
    return windows[0] if windows else None


def graceful_close(hwnd: int, process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        raise ColdStartError("NSIS candidate shutdown requires Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    require(bool(user32.PostMessageW(hwnd, 0x0010, 0, 0)),
            "Installed Motionwright window did not accept a close message")
    try:
        result = process.wait(timeout=12)
    except subprocess.TimeoutExpired as error:
        raise ColdStartError("Installed Motionwright did not exit after its window closed") from error
    require(result == 0, "Installed Motionwright exited nonzero after window closure")


def boot_once(
    binary: Path, env: dict[str, str], database_roots: list[Path],
    log_path: Path, expected_db: Path | None = None, seconds: int = 55,
) -> tuple[dict, Path, int]:
    creation_sent = False
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            [str(binary)],
            cwd=binary.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        started = time.monotonic()
        try:
            while time.monotonic() - started < seconds:
                if process.poll() is not None:
                    raise ColdStartError(
                        "Installed desktop exited before a persistent project and real HWND existed "
                        f"(exit_code={process.returncode})"
                    )
                observed = known_databases(database_roots)
                if len(observed) > 1:
                    raise ColdStartError("Installed desktop created multiple candidate project stores")
                hwnd = windows_motionwright_window(process.pid)
                if hwnd is not None and observed:
                    database = observed[0]
                    if expected_db is None and not creation_sent and _empty_library_ready(database):
                        # This flag is set before action: an uncertain result
                        # must never cause a second Create on the same launch.
                        creation_sent = True
                        _create_first_project_in_native_window(hwnd)
                    if expected_db is not None and database != expected_db:
                        raise ColdStartError("Relaunched desktop switched to another project database")
                    try:
                        snapshot = read_project_snapshot(database)
                    except ColdStartError:
                        # The real installed app may still be writing its first
                        # SQLite transaction. Do not synthesize an observed store.
                        time.sleep(0.3)
                        continue
                    require(process.poll() is None,
                            "Installed desktop terminated during first-use source readback")
                    graceful_close(hwnd, process)
                    return snapshot, database, int(time.monotonic() - started)
                time.sleep(0.4)
            raise ColdStartError(
                "Installed desktop did not create a real titled HWND and committed project within its deadline"
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=12)


def validate_evidence_paths(scratch: Path, output: Path) -> None:
    require(scratch.is_absolute() and output.is_absolute(),
            "Installed bootstrap requires bounded absolute runner paths")


def native_windows_launch_environment(source: dict[str, str]) -> dict[str, str]:
    """The disposable runner itself is the isolation boundary.

    Do not forge USERPROFILE/APPDATA/LOCALAPPDATA for a Windows WebView2 process:
    known-folder APIs and runtime registration still depend on the actual
    Windows account. Removing only the optional native connection prevents
    ambient provider authority without inventing a new user profile.
    """
    environment = dict(source)
    environment.pop("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", None)
    return environment


def run_installed_relaunch(
    *, executable: Path, smoke_receipt: Path, expected_sha: str,
    scratch: Path, output: Path,
) -> dict:
    require(os.name == "nt" and os.getenv("GITHUB_ACTIONS") == "true",
            "Installed Windows GUI bootstrap is restricted to a disposable GitHub Windows runner")
    validate_evidence_paths(scratch, output)
    require(not scratch.exists(), "Installed bootstrap scratch must be newly created")
    require(not output.exists(), "Installed bootstrap evidence must not be overwritten")
    source = check_installed_candidate(smoke_receipt, executable, expected_sha)
    scratch.mkdir(parents=True, exist_ok=False)
    # Keep the real, disposable hosted-runner Windows account. WebView2's
    # Known Folders, COM activation and user-data storage are not modeled by
    # merely replacing APPDATA and USERPROFILE environment strings.
    environment = native_windows_launch_environment(os.environ)
    require("APPDATA" in environment and "LOCALAPPDATA" in environment,
            "Windows hosted runner does not expose native application-data directories")
    roots = [Path(environment["APPDATA"]), Path(environment["LOCALAPPDATA"])]
    require(not known_databases(roots),
            "GitHub runner already contains an application project store before cold launch")
    first, db_path, first_seconds = boot_once(
        executable, environment, roots, scratch / "first-launch.log"
    )
    second, repeated_path, second_seconds = boot_once(
        executable, environment, roots, scratch / "relaunch.log", expected_db=db_path
    )
    require(db_path == repeated_path and first == second,
            "Installed Windows relaunch replaced or mutated the first saved project")
    require(sha256_file(executable) == source["installed_binary_sha256"],
            "Installed Windows executable bytes changed during cold boot")
    location_scope = "isolated_runner_appdata" if db_path.is_relative_to(scratch) else "disposable_runner_profile_appdata"
    result = {
        "schema": "motionwright-windows-nsis-cold-relaunch/1",
        "source_sha": source["source_sha"],
        "installed_binary_sha256": source["installed_binary_sha256"],
        "installed_nsis_candidate": True,
        "first_launch_real_visible_hwnd": True,
        "second_launch_real_visible_hwnd": True,
        "separate_process_lifetimes": True,
        "same_database_reopened": True,
        "same_project_generation_revision_document": True,
        "project": first,
        "store_location_scope": location_scope,
        "first_launch_observation_seconds": first_seconds,
        "second_launch_observation_seconds": second_seconds,
        "semwright_connection": "UNCONFIGURED",
        "renderer_runtime_acceptance": "NOT_RUN",
        "human_install_acceptance": "NOT_RUN",
        "release_published": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return result


def write_sanitized_failure_record(
    *, output: Path, scratch: Path, expected_sha: str, error: BaseException,
) -> None:
    """Keep a bounded failure explanation without uploading native app logs.

    There is no owner Semwright session configured in this fixture, but raw
    diagnostic output may still contain runner paths and must not be released.
    """
    if os.name != "nt" or os.getenv("GITHUB_ACTIONS") != "true" or not output.is_absolute():
        return
    diagnostic_path = output.with_name("windows-nsis-cold-relaunch-diagnostic.json")
    categories = {
        "webview2": b"webview2",
        "sqlite": b"sqlite",
        "application_data": b"app data",
        "panic": b"panicked",
        "win32": b"win32",
        "dll": b"missing dll",
    }
    logs = {}
    for label in ("first-launch", "relaunch"):
        source = scratch / (label + ".log")
        if source.is_file() and not source.is_symlink():
            with source.open("rb") as stream:
                excerpt = stream.read(65536).lower()
            logs[label] = {
                "file_bytes": source.stat().st_size,
                "known_error_signals": {
                    key: needle in excerpt for key, needle in categories.items()
                },
            }
    description = (
        str(error)[:220] if isinstance(error, ColdStartError)
        else type(error).__name__
    )
    record = {
        "schema": "motionwright-windows-nsis-cold-relaunch-failure/1",
        "source_sha": expected_sha if SHA40.fullmatch(expected_sha) else "INVALID",
        "result": "FAIL",
        "stage": "installed_process_cold_boot_or_relaunch",
        "reason": description,
        "logs": logs,
        "raw_application_logs_uploaded": False,
        "native_provider_authority": "NOT_CONFIGURED",
        "human_install_acceptance": "NOT_RUN",
    }
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    with diagnostic_path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-binary", required=True, type=Path)
    parser.add_argument("--candidate-smoke", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_installed_relaunch(
            executable=args.installed_binary, smoke_receipt=args.candidate_smoke,
            expected_sha=args.expected_source_sha, scratch=args.scratch,
            output=args.output,
        )
    except (ColdStartError, OSError, ValueError, sqlite3.Error) as error:
        try:
            write_sanitized_failure_record(
                output=args.output, scratch=args.scratch,
                expected_sha=args.expected_source_sha, error=error,
            )
        except (OSError, ValueError):
            # Never mask the original bootstrap failure with diagnostic errors.
            pass
        parser.exit(1, "windows-installed-cold-relaunch: " + str(error) + "\n")
    print(
        "windows-installed-cold-relaunch=PASS source=" + result["source_sha"]
        + " real_hwnd=2 sqlite_project_reuse=PASS human_acceptance=NOT_RUN"
    )


if __name__ == "__main__":
    main()
