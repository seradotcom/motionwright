#!/usr/bin/env python3
"""Reject unintended development-local files and text from public source.

This guard reviews only Git-tracked first-party files. It intentionally does
not forbid documented agent-facing product capabilities or API identifiers.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

FORBIDDEN_ROOTS = (
    ".agents/", ".codex/", "knowledge/", "worktrees/", ".worktrees/",
    "verification/",
)
FORBIDDEN_NAMES = {"AGENTS.md", "CLAUDE.md", "MASTER_PROMPT.md", ".cursorrules"}
REQUIRED_IGNORES = (
    "/.agents/", "/.codex/", "/knowledge/", "/worktrees/",
    "/.worktrees/", "/verification/",
)
PUBLIC_TEXT_EXTENSIONS = {".md", ".rs", ".ts", ".tsx", ".py", ".yml", ".yaml"}
PUBLIC_TEXT_ROOTS = ("docs/", "apps/", "crates/", ".github/workflows/")
METATEXT = (
    ("cross_operator_instruction", re.compile(
        r"\b(?:another agent may be|other agents['\u2019] work|"
        r"implementation agent|next agent|agent handoff)\b", re.I)),
    ("delegation_boilerplate", re.compile(
        r"\bdelegated by the user\b|impeccable:product-schema", re.I)),
    ("private_prompt_marker", re.compile(
        r"\bmaster prompt\b", re.I)),
)


class PublicSurfaceError(ValueError):
    pass


def scan_paths(tracked: list[str]) -> list[str]:
    issues: list[str] = []
    for name in tracked:
        if any(name.startswith(prefix) for prefix in FORBIDDEN_ROOTS):
            issues.append("local_development_file_tracked:" + name)
        if name in FORBIDDEN_NAMES:
            issues.append("local_instruction_file_tracked:" + name)
    return issues


def scan_text(name: str, text: str) -> list[str]:
    if not (name in ("README.md", "PRODUCT.md")
            or name.startswith(PUBLIC_TEXT_ROOTS)):
        return []
    if Path(name).suffix not in PUBLIC_TEXT_EXTENSIONS:
        return []
    found: list[str] = []
    for line_num, line in enumerate(text.splitlines(), 1):
        for code, rule in METATEXT:
            if rule.search(line):
                found.append(f"public_development_text:{name}:{line_num}:{code}")
    return found


def _tracked_names(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicSurfaceError("tracked_source_unavailable") from exc
    if result.returncode:
        raise PublicSurfaceError("tracked_source_unavailable")
    try:
        paths = [p.decode("utf-8", errors="strict")
                 for p in result.stdout.split(b"\x00") if p]
    except UnicodeDecodeError as exc:
        raise PublicSurfaceError("tracked_path_not_utf8") from exc
    if len(paths) != len(set(paths)) or not paths:
        raise PublicSurfaceError("tracked_path_list_invalid")
    return paths


def verify_public_surface(root: Path) -> list[str]:
    root = root.resolve(strict=True)
    tracked = _tracked_names(root)
    issues = scan_paths(tracked)
    try:
        ignores = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PublicSurfaceError("ignore_policy_unavailable") from exc
    for marker in REQUIRED_IGNORES:
        if marker not in ignores:
            issues.append("missing_local_ignore:" + marker)
    for name in tracked:
        if not (name in ("README.md", "PRODUCT.md")
                or name.startswith(PUBLIC_TEXT_ROOTS)):
            continue
        if Path(name).suffix not in PUBLIC_TEXT_EXTENSIONS:
            continue
        path = root / name
        try:
            if path.is_symlink() or path.stat().st_size > 2_000_000:
                issues.append("public_text_invalid:" + name)
                continue
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            issues.append("public_text_unreadable:" + name)
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
