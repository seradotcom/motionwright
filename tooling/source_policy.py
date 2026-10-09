#!/usr/bin/env python3
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "SOURCE_LOCK.json").read_text())
REV = LOCK["dependencies"]["semwright"]["revision"]
VERSION = LOCK["dependencies"]["semwright"]["version"]

errors = []

cargo = (ROOT / "Cargo.toml").read_text()
if f'rev = "{REV}"' not in cargo:
    errors.append("Cargo.toml does not use SOURCE_LOCK Semwright revision")

desktop = (ROOT / "apps/desktop/src-tauri/src/main.rs").read_text()
if REV not in desktop:
    errors.append("desktop Native SDK receipt does not match SOURCE_LOCK")
if f'const SEMWRIGHT_VERSION: &str = "{VERSION}";' not in desktop:
    errors.append("desktop supported Semwright version does not match SOURCE_LOCK")

tauri_config = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text())
csp = tauri_config.get("app", {}).get("security", {}).get("csp", "")
directives = {}
for raw_directive in csp.split(";"):
    parts = raw_directive.strip().split()
    if parts:
        directives[parts[0].lower()] = [token.lower() for token in parts[1:]]
if directives.get("default-src") != ["'self'"]:
    errors.append("Tauri CSP default-src must be exactly 'self'")
for directive in ("object-src", "frame-src", "base-uri", "form-action"):
    if directives.get(directive) != ["'none'"]:
        errors.append(f"Tauri CSP {directive} must be exactly 'none'")
script_tokens = directives.get("script-src", directives.get("default-src", []))
if script_tokens != ["'self'"]:
    errors.append("Tauri CSP script-src must be exactly 'self'")
connect_tokens = directives.get("connect-src", directives.get("default-src", []))
allowed_connect = {"ipc:", "http://ipc.localhost"}
if set(connect_tokens) != allowed_connect:
    errors.append("Tauri CSP connect-src must contain only the local IPC origins")

desktop_cargo = (ROOT / "apps/desktop/src-tauri/Cargo.toml").read_text()
for suffix in ("shell", "fs", "http", "process"):
    plugin = "tauri-plugin-" + suffix
    if plugin in desktop_cargo:
        errors.append(f"desktop shell must not expose privileged plugin {plugin}")

workflow_text = "\n".join(
    path.read_text(errors="ignore")
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml"))
)
automatic_publish_tokens = [
    "cargo " + "publish",
    "npm " + "publish",
    "gh " + "release create",
    "softprops/" + "action-gh-release",
    "ncipollo/" + "release-action",
]
if any(token in workflow_text for token in automatic_publish_tokens):
    errors.append("repository workflows must not automatically publish packages or releases")

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
    if re.search(r"AKIA[0-9A-Z]{16}", text):
        errors.append(f"AWS access-key-like token found: {rel}")
    if re.search(r"xox[baprs]-[A-Za-z0-9-]{20,}", text):
        errors.append(f"Slack credential-like token found: {rel}")
    if re.search(r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}", text):
        errors.append(f"API credential-like token found: {rel}")
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    if private_key_marker in text:
        errors.append(f"private key material found: {rel}")
    legacy_product_alias = ("sequence" + "wright").lower()
    if legacy_product_alias in text.lower():
        errors.append(f"unrelated legacy product identity leaked into Motionwright: {rel}")
    private_pack_markers = [
        "Semwright_Creative_" + "Native_App",
        "PRIMER_" + "CICLO",
    ]
    if any(marker.lower() in text.lower() for marker in private_pack_markers):
        errors.append(f"private specification package marker leaked: {rel}")
    if rel.startswith(("apps/studio/src/", "apps/desktop/src-tauri/src/")):
        dynamic_code_tokens = [
            "dangerouslySetInner" + "HTML",
            "ev" + "al(",
            "new " + "Function(",
            "document." + "write(",
        ]
        if any(token in text for token in dynamic_code_tokens):
            errors.append(f"dynamic code or raw HTML execution primitive found: {rel}")

    if rel.startswith((".github/workflows/", "apps/", "crates/", "tooling/")):
        sandbox_bypass_tokens = [
            "--no-" + "sandbox",
            "--disable-" + "web-security",
            "--disable-" + "setuid-sandbox",
            "--disable-" + "seccomp-filter-sandbox",
        ]
        if any(token in text for token in sandbox_bypass_tokens):
            errors.append(f"sandbox or host-protection bypass found: {rel}")

    if rel.startswith(("crates/", "apps/desktop/src-tauri/src/")):
        process_tokens = [
            "std::process::" + "Command",
            "tokio::process::" + "Command",
            "Command::" + "new(",
        ]
        process_allowlist = {
            "crates/native/src/production.rs",
            "crates/driver-manim-community/src/bin/runtime_runner.rs",
            "crates/driver-hyperframes/src/bin/runtime_runner.rs",
        }
        if any(token in text for token in process_tokens) and rel not in process_allowlist:
            errors.append(f"unreviewed first-party process execution primitive found: {rel}")

        direct_network_tokens = [
            "std::net::" + "TcpStream",
            "std::net::" + "UdpSocket",
            "tokio::net::" + "TcpStream",
            "tokio::net::" + "UdpSocket",
            "reqwest::" + "Client",
            "hyper::" + "Client",
            "ureq::" + "Agent",
        ]
        if any(token in text for token in direct_network_tokens):
            errors.append(f"unreviewed first-party direct network primitive found: {rel}")

license_text = (ROOT / "LICENSE").read_text()
if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text.upper():
    errors.append("LICENSE is not AGPL")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"source-policy=ok semwright={REV} version={VERSION}")
