#!/usr/bin/env python3
"""Check tracked public source and exclude development-local working material.

Product APIs for agents remain valid. Text findings identify specific development
instructions; they are not a general prohibition on agent-related vocabulary.
"""
from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

FORBIDDEN_ROOTS = (
    ".agents/", ".codex/", "knowledge/", "worktrees/", ".worktrees/",
    "verification/",
)
FORBIDDEN_NAMES = {"AGENTS.md", "CLAUDE.md", "MASTER_PROMPT.md", ".cursorrules"}
REQUIRED_IGNORES = tuple("/" + name for name in FORBIDDEN_ROOTS)
GENERATED_NAMES = {".git", "node_modules", "target", "dist",
                   "test-results", "playwright-report"}
PUBLIC_TEXT_EXTENSIONS = {".md", ".rs", ".ts", ".tsx", ".py", ".yml", ".yaml"}
PUBLIC_TEXT_ROOTS = ("docs/", "apps/", "crates/", ".github/workflows/")
METATEXT = (
    ("cross_operator_instruction", re.compile(
        r"\b(?:another\s+agent\s+may\s+be|other\s+agents['\u2019]\s+work|"
        r"implementation\s+agent|next\s+agent\s+(?:should|must|needs\s+to)"
        r"\s+(?:edit|implement|continue|resume|fix|finish|complete|merge|rebase)|"
        r"agent\s+handoff\s+(?:notes|instructions))\b", re.I)),
    ("delegation_boilerplate", re.compile(
        r"\bdelegated\s+by\s+the\s+user\b|impeccable:product-schema", re.I)),
    ("private_prompt_marker", re.compile(r"\bmaster\s+prompt\b", re.I)),
)


class PublicSurfaceError(ValueError):
    pass


def is_local_development_path(name: str) -> bool:
    """Match the repository-relative root entry as well as its descendants."""
    return name.partition("/")[0].casefold() in {
        prefix.rstrip("/").casefold() for prefix in FORBIDDEN_ROOTS
    }


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def iter_source_files(root: Path) -> Iterator[Path]:
    """Walk source without consulting ancestors outside this repository.

    Prune local roots before descent. A nested product directory named
    knowledge or worktrees is still source and must undergo content checks.
    """
    root = root.resolve(strict=True)
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = [
            name for name in dirs
            if name not in GENERATED_NAMES
            and not is_local_development_path(
                (parent / name).relative_to(root).as_posix())
            and not _is_link(parent / name)
        ]
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if (name not in GENERATED_NAMES
                    and not is_local_development_path(relative)
                    and not _is_link(path) and path.is_file()):
                yield path


def scan_paths(tracked: list[str]) -> list[str]:
    issues: list[str] = []
    forbidden_names = {name.casefold() for name in FORBIDDEN_NAMES}
    for name in tracked:
        if is_local_development_path(name):
            issues.append("local_development_file_tracked:" + name)
        if PurePosixPath(name).name.casefold() in forbidden_names:
            issues.append("local_instruction_file_tracked:" + name)
    return issues


def _is_public_text(name: str) -> bool:
    suffix = PurePosixPath(name).suffix.lower()
    return suffix in PUBLIC_TEXT_EXTENSIONS and (
        ("/" not in name and suffix == ".md")
        or name.startswith(PUBLIC_TEXT_ROOTS)
    )


def scan_text(name: str, text: str) -> list[str]:
    if not _is_public_text(name):
        return []
    found: list[str] = []
    for code, rule in METATEXT:
        for match in rule.finditer(text):
            line_num = text.count("\n", 0, match.start()) + 1
            item = f"public_development_text:{name}:{line_num}:{code}"
            if item not in found:
                found.append(item)
    return found


def _git(root: Path, args: list[str], data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicSurfaceError("git_source_unavailable") from exc
    allowed = {0, 1} if args[0] == "check-ignore" else {0}
    if result.returncode not in allowed:
        raise PublicSurfaceError("git_source_unavailable")
    return result.stdout


def _tracked_names(root: Path) -> list[str]:
    value = _git(root, ["ls-files", "-z", "--cached"])
    try:
        paths = [p.decode("utf-8", errors="strict")
                 for p in value.split(b"\x00") if p]
    except UnicodeDecodeError as exc:
        raise PublicSurfaceError("tracked_path_not_utf8") from exc
    if len(paths) != len(set(paths)) or not paths:
        raise PublicSurfaceError("tracked_path_list_invalid")
    if any(PurePosixPath(p).is_absolute() or ".." in PurePosixPath(p).parts
           for p in paths):
        raise PublicSurfaceError("tracked_path_list_invalid")
    return paths


def _read_text(root: Path, name: str) -> str:
    path = root / name
    cursor = path
    try:
        while cursor != root:
            if _is_link(cursor):
                raise PublicSurfaceError("public_text_invalid:" + name)
            cursor = cursor.parent
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
            raise PublicSurfaceError("public_text_invalid:" + name)
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PublicSurfaceError("public_text_unreadable:" + name) from exc


def _ignore_issues(root: Path) -> list[str]:
    ignores = _read_text(root, ".gitignore").splitlines()
    issues = ["missing_local_ignore:" + marker
              for marker in REQUIRED_IGNORES if marker not in ignores]
    probes = [probe for prefix in FORBIDDEN_ROOTS for probe in (
        prefix, prefix + ".public-source-probe",
        prefix + "nested/.public-source-probe",
    )]
    output = _git(
        root, ["check-ignore", "--verbose", "--non-matching",
               "--no-index", "-z", "--stdin"],
        b"".join(p.encode("utf-8") + b"\x00" for p in probes),
    )
    fields = output.split(b"\x00")
    if fields[-1:] == [b""]:
        fields.pop()
    if len(fields) != 4 * len(probes):
        raise PublicSurfaceError("ignore_result_invalid")
    for index, probe in enumerate(probes):
        _, _, pattern, checked = fields[index * 4:index * 4 + 4]
        if checked != probe.encode("utf-8"):
            raise PublicSurfaceError("ignore_result_invalid")
        if not pattern or pattern.startswith(b"!"):
            issues.append("ineffective_local_ignore:" + probe)
    return issues


def verify_public_surface(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    tracked = _tracked_names(root)
    issues = scan_paths(tracked)
    issues.extend(_ignore_issues(root))
    for name in tracked:
        if not _is_public_text(name):
            continue
        try:
            value = _read_text(root, name)
        except PublicSurfaceError as exc:
            issues.append(str(exc))
            continue
        issues.extend(scan_text(name, value))
    return issues


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    problems = verify_public_surface(root)
    if problems:
        for item in problems:
            print("ERROR: " + item)
        raise SystemExit(1)
    print("public-source-hygiene=PASS")


if __name__ == "__main__":
    main()
