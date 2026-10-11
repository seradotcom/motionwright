# Native runtime onboarding for installed Motionwright v0.1.0 candidates

**Scope:** an owner-selectable, locally remembered existing Semwright production connection. This does **not** bundle, install, trust, update, provision or start a Semwright Broker/Driver Host. Offline project work remains available without any native renderer. This flow is independent of the 60 human acceptance cases.

## First project, first connection

1. Install the unsigned Motionwright candidate only on a machine where you have chosen to test it. The first launch creates an application-owned local project in the normal platform app-data directory. Choose **Integrations** from the navigation; the Semwright Native SDK adapter is distinct from the production runtime and cannot by itself render media.
2. Provision a separate, operator-owned Semwright Broker/Driver Host and a canonical Motionwright connection JSON using the pinned Semwright Native SDK integration contract. The owner connection must be bound to the exact `project:<UUID>` resource displayed in the Integrations panel. Motionwright does not mint session tickets or bypass Broker policy.
3. In the **installed desktop app**, select **Choose Semwright connection**. A native OS file picker—not the webview—selects the existing JSON connection file. The picker returns only a boolean selection result to the UI, never its path, contents, private session file or provider credentials.
4. Before saving the local reference, the trusted desktop side invokes `ProductionConnection::load` (strict schema, absolute paths, non-symlinked files, executable SHA-256, owner socket and private session existence), checks the project resource, and performs a **read-only, bounded CLI version probe** against Semwright `1.0.0`. A changed project generation/revision invalidates selection. A mismatch does not replace a previously working connection.
5. **Integrations → Semwright production runtime** reports READY only if the current connection passes the same read-only checks. The source dependency pin in `SOURCE_LOCK.json` is not falsely presented as an attestation that an arbitrary CLI binary was built from that source SHA. Rendering still requires explicit one-time local effects and native Broker/Driver Host policy.
6. Close and reopen Motionwright. The app remembers *only* the selected file path for that same project and generation, in a restricted local settings file. Every new operation reloads and validates the canonical connection; stored settings do not contain secrets or durable execution permission.

Use **Remove saved selection** to revoke Motionwright's remembered reference. It does **not** kill active provider jobs, delete Semwright session material or revoke OS/Broker permissions. If a separate `MOTIONWRIGHT_SEMWRIGHT_CONNECTION` environment path was explicitly configured by the owner, it remains a supported fallback after removal. A saved selection takes precedence over that fallback. Invalid, stale or malicious saved references **fail closed** rather than silently downgrading to environment authority.

### Canonical connection fields (reference only)

The trusted owner-provisioned connection follows the existing `motionwright-semwright-connection/1` schema. Its required fields are `schema`, `executable`, `executable_sha256`, `socket`, `session_file`, `output_root` and `resource`. Filesystem paths must be absolute and point to owner-authorized objects; the executable hash is checked against the bytes on disk. On Unix, connection files and runtime resources must not be group/world writable. The file and its private contents remain outside the WebView; do not paste it into chat, a browser, an issue or a project asset.

### Scope and test evidence

- Rust registry tests: saved reference survives reopening, projects/generations stay isolated, replacement does not copy source session bytes, symlinks and permissive file modes are rejected, corrupted scopes fail closed.
- Desktop Rust compile/tests and native OS packaging must pass CI on Linux, macOS and Windows. The native file picker requires a real owner gesture and is **not** simulated as a successful picker in a headless test.
- Browser-mode Playwright verifies the controls are disabled without a desktop host and do not mutate the project revision.
- Installed Linux/Windows/macOS candidate smoke tests remain separate. Packaging, a passing runtime probe and a configured file do **not** imply first real render, provider lifecycle recovery, human installation sign-off or commercial release readiness. Run independent acceptance intake to prove those.

This is a bounded v0.1.0 onboarding improvement, **not** a complete fresh-machine installation flow. Runtime provisioning and first-person owner acceptance must still be tested on a device with an authorized live Semwright provider. Provider-generation loss issue #49 remains open.
