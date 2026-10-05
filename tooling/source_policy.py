#!/usr/bin/env python3
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text())
REV = LOCK["dependencies"]["semwright"]["revision"]

errors = []

cargo = (ROOT / "Cargo.toml").read_text()
if f'rev = "{REV}"' not in cargo:
    errors.append("Cargo.toml does not use SOURCE_LOCK Semwright revision")

desktop = (ROOT / "apps/desktop/src-tauri/src/main.rs").read_text()
if REV not in desktop:
    errors.append("desktop Native SDK receipt does not match SOURCE_LOCK")

for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
        continue
    rel = path.relative_to(ROOT).as_posix()
    if any(part in {"target", "dist", "test-results", "playwright-report"} for part in path.parts):
        continue
    if re.search(r"(^|/)(MASTER_[^/]*|plantillas_agentes)(/|$)", rel, re.IGNORECASE):
        errors.append(f"private coordination material must not be published: {rel}")
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        continue
    developer_home = "/home/" + "sergio/"
    if developer_home in text:
        errors.append(f"absolute developer home path leaked: {rel}")
    if re.search(r"gh[opsu]_[A-Za-z0-9]{20,}", text):
        errors.append(f"GitHub credential-like token found: {rel}")

license_text = (ROOT / "LICENSE").read_text()
if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text.upper():
    errors.append("LICENSE is not AGPL")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"source-policy=ok semwright={REV}")
