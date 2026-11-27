#!/usr/bin/env python3
import hashlib
import json
import pathlib
import re
import sys

# An embedded Python runtime can pin sys.path to an old worktree. Bind every
# product policy import to the *exact source checkout* this script attests.
ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLING = ROOT / "tooling"
sys.path.insert(0, str(TOOLING))

from public_surface_policy import (
    PublicSurfaceError, has_unrelated_legacy_product_identity, iter_source_files, verify_public_surface,
)

from source_lock_admission import (
    SourceAdmissionError, load_source_lock, verify_source_lock,
)


def _require_checkout_policy_module(name: str) -> None:
    module = sys.modules.get(name)
    location = getattr(module, "__file__", None)
    if not isinstance(location, str):
        raise ImportError("source_policy_module_not_checkout")
    if pathlib.Path(location).resolve(strict=True).parent != TOOLING:
        raise ImportError("source_policy_module_not_checkout")


for name in ("public_surface_policy", "source_lock_admission"):
    try:
        _require_checkout_policy_module(name)
    except (ImportError, OSError, ValueError):
        print("ERROR: source-policy import was not from the active checkout",
              file=sys.stderr)
        raise SystemExit(1) from None
try:
    LOCK = load_source_lock(ROOT)
    verify_source_lock(ROOT, LOCK)
except SourceAdmissionError as exc:
    print(f"ERROR: Semwright source admission refused: {exc}", file=sys.stderr)
    raise SystemExit(1) from None
except (OSError, ValueError):
    print("ERROR: Semwright source admission refused: source_lock_unavailable",
          file=sys.stderr)
    raise SystemExit(1) from None
REV = LOCK["dependencies"]["semwright"]["revision"]
VERSION = LOCK["dependencies"]["semwright"]["version"]

errors = []

try:
    errors.extend(verify_public_surface(ROOT))
except PublicSurfaceError as exc:
    errors.append("public source hygiene unavailable: " + str(exc))

cargo = (ROOT / "Cargo.toml").read_text()
if f'rev = "{REV}"' not in cargo:
    errors.append("Cargo.toml does not use SOURCE_LOCK Semwright revision")

# The Linux glib FFI backport is a source substitution. Independently
# rehash the exact original crate and EVERY vendored source file, not just
# the Cargo.toml path or glib semantic version. No warning is waived.
try:
    from v01_glib_product_admission import admit_patched_glib
    _require_checkout_policy_module("v01_glib_product_admission")
    _require_checkout_policy_module("v01_glib_backport_integrity")
    admit_patched_glib(ROOT)
except (ImportError, OSError, ValueError) as error:
    # Keep untrusted exception paths private; expose a fixed diagnostic family
    # and an optional bounded product verifier code, not a misleading PASS.
    reason = ("import_error" if isinstance(error, ImportError)
              else "filesystem_error" if isinstance(error, OSError)
              else str(error) if type(error).__name__ == "ProductBackportError"
              and re.fullmatch(r"[a-z][a-z0-9_]{2,90}", str(error))
              else "value_error")
    errors.append("glib 0.18.5 patched source custody is unverified: " + reason)

# Tauri IPC diagnostics must use the three reviewed source substitutions.
# Check the complete upstream archive and every vendored file independently.
try:
    from v01_tauri_ipc_product_admission import admit_patched_tauri
    _require_checkout_policy_module("v01_tauri_ipc_product_admission")
    admit_patched_tauri(ROOT)
except (ImportError, OSError, ValueError) as error:
    # Report only a bounded fixed verifier code, never paths or raw source.
    code = str(error) if type(error).__name__ == "TauriSourceAdmissionError" else ""
    if re.fullmatch(r"[a-z][a-z0-9_]{2,90}", code):
        errors.append("Tauri 2.12.1 IPC diagnostic source custody is unverified: " + code)
    else:
        errors.append("Tauri 2.12.1 IPC diagnostic source custody is unverified")

desktop = (ROOT / "apps/desktop/src-tauri/src/main.rs").read_text()
if REV not in desktop:
    errors.append("desktop Native SDK receipt does not match SOURCE_LOCK")
if f'const SEMWRIGHT_VERSION: &str = "{VERSION}";' not in desktop:
    errors.append("desktop supported Semwright version does not match SOURCE_LOCK")

# Browser mode is an explicit non-authoritative demo, but it must not
# advertise a different Native SDK source pin from the installed build.
fixture = (ROOT / "apps/studio/src/fixture.ts").read_text()
demo_pin = re.search(r'pinned_revision:\s*"([0-9a-f]{40})"', fixture)
if demo_pin is None or demo_pin.group(1) != REV:
    errors.append("browser-demo SDK source pin differs from SOURCE_LOCK")

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

# These sources form one reviewed process boundary. The host chooses fixed
# protected executables and stages the embedded modules in a private workspace.
# The one-use grant and retained dispatch precede launch. The process group or
# job bounds the child, and readback precedes any no-clobber publication.
reviewed_otio_sources = {
    "apps/desktop/src-tauri/src/otio_bridge.rs":
        "2cc1789c73aee6a84d3d947c782b7a2407e6e4d834587f7fe89a3a7eb9a7ee56",
    "apps/desktop/src-tauri/src/otio_bridge_macos.rs":
        "3337b54d13cb20ef0e0453a974d3e4e53b2359c7f62cadad501ee8dafe5427b8",
    "apps/desktop/src-tauri/src/otio_bridge_windows_process.rs":
        "e763b91bdf94bc8b8424693f0780cdc930a805fab11a7bbff1bf9891a94c2067",
    "apps/desktop/src-tauri/src/otio_bridge_windows_runtime.rs":
        "e2ba0917ccb5d82cd42f20546ffa7e847d7fdcaa0068dfd63043ead3459f3903",
    "tooling/manim_native_readback.py":
        "14fc30caefed5a220d437bfc32c738ff83fca3ccc5da19abfe4343b1dea3637a",
    "tooling/mlt_official_runtime_intake.py":
        "0d5e640f0b4750c30666bcdad1968d9883c1ab86866b391949f4bf45fa1f1467",
    "tooling/otio-media/bridge.py":
        "e7d4f10b66a5a5c1a0b9278d979a7d8158ae93e6014c7568587fdb8e97f075ea",
    "tooling/otio-media/kdenlive_firstparty_audio_track.py":
        "3995594d644563b9d89fa7d7543afa006144167763048d72f93209ceeecb93a1",
}
otio_sources_admitted = False
if any((ROOT / rel).exists() for rel in reviewed_otio_sources):
    otio_sources_admitted = True
    for rel, expected_digest in reviewed_otio_sources.items():
        path = ROOT / rel
        try:
            # Normalize CRLF exactly as the process-fixture source checks do.
            raw = path.read_text(encoding="utf-8").encode("utf-8")
            verified = not path.is_symlink() and hashlib.sha256(raw).hexdigest() == expected_digest
        except (UnicodeDecodeError, OSError):
            verified = False
        if not verified:
            errors.append(f"unreviewed OTIO execution source: {rel}")
            otio_sources_admitted = False

for path in iter_source_files(ROOT):
    rel = path.relative_to(ROOT).as_posix()
    if re.search(r"(^|/)(MASTER_[^/]*|plantillas_agentes)(/|$)", rel, re.IGNORECASE):
        errors.append(f"private coordination material must not be published: {rel}")
    try:
        text = path.read_text(encoding="utf-8")
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
    if re.search(r"(?<![A-Za-z0-9_])sk-(?:proj-)?[A-Za-z0-9_-]{24,}", text):
        errors.append(f"API credential-like token found: {rel}")
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    if private_key_marker in text:
        errors.append(f"private key material found: {rel}")
    if has_unrelated_legacy_product_identity(rel, text):
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
            "CreateProcessW" + "(",
        ]
        process_allowlist = {
            "crates/native/src/production.rs",
            "crates/storage/src/apply_crash_tests.rs",
            "crates/service/tests/project_portability_authority.rs",
            # Storage recovery child processes are compiled only under cfg(test).
            "crates/storage/src/cas_retention_process_tests.rs",
            "crates/driver-manim-community/src/bin/runtime_runner.rs",
        }
        # Exact reviewed sources for bounded tests and acceptance fixtures.
        # Keep every fixture outside the path-only process allowlist.
        # read_text normalizes newlines; the reviewed UTF-8 Git source uses LF.
        reviewed_process_fixture_sources = {
            # The bounded FIFO child exists only in cfg(test).
            "crates/native/src/production/brand_fonts.rs":
                "898dbb96ea14e2a24698bd2a97442eaa61debb55b91e627fd40a3ecb9ff14d4c",
            # Retained SQLite identity uses a bounded child only in cfg(test).
            "crates/storage/src/sqlite_identity.rs":
                "d85c4a7fcba781467fab6db7b0035882ae9bc212bc85978c23066865be23bd91",
            # Supported-OS cfg(test) StoreReader custody: both original-process
            # probes execute this same test binary with a fixed --exact child
            # name and explicit ignored helper, no shell or project-selected arguments. Any
            # modification to that reviewed source invalidates this SHA gate.
            "crates/storage/src/read_only.rs":
                "7dde67a8686564ad83f658042608608c75c018cbd079f98b26adb21d16720b9d",
            # N83 acceptance CLI, not cfg(test): fixed Git identity reads and
            # the committed preview helper under the admitted runner's owned
            # process group/deadline; no shell or project-selected executable.
            "crates/native/examples/native-incremental-e2e.rs":
                "19cb956685fc30eebd6ff5b2b8ea282988d8b40d39b64f94cc175d16a1624e6d",
        }
        reviewed_process_fixture = (
            rel in reviewed_process_fixture_sources
            and hashlib.sha256(text.encode("utf-8")).hexdigest()
                == reviewed_process_fixture_sources[rel]
        )
        reviewed_production_process = otio_sources_admitted and rel in reviewed_otio_sources
        if (any(token in text for token in process_tokens)
                and rel not in process_allowlist
                and not reviewed_process_fixture and not reviewed_production_process):
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
