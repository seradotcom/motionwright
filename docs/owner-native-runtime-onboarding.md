# Native runtime onboarding for installed Motionwright v0.1.0 candidates

**Scope:** an owner-selectable, locally remembered existing Semwright production connection. This does **not** bundle, install, trust, update, provision or start a Semwright Broker/Driver Host. Offline project work remains available without any native renderer. This flow is independent of the 60 human acceptance cases.

A separate [offline runtime profile installer](offline-runtime-profiles.md) now provides explicit package inspection, size/destination review, local copy, original-result recovery and preserved rollback for admitted Linux profiles. It does not provision or authorize the Broker/Driver Host described below.

## First project, first connection

1. Install the unsigned Motionwright candidate only on a machine where you have chosen to test it. The first launch opens the local project library. Choose an empty project, the editable motion example, or inspect and import an existing portable project. Creation and import are explicit; their saved results can be recovered after a lost response. Choose **Integrations** from the navigation; the Semwright Native SDK adapter is distinct from the production runtime and cannot by itself render media.
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


## Independent read-only first-use troubleshooting

The separate source-tree [runtime first-use audit](runtime-first-use-audit.md)
lets owners validate the exact saved reference, scoped connection, existing
runtime files and digest without exposing private session bytes or running a
render. It does not add a new Native SDK, provision an owner runtime or give
the application authorization to perform a Broker operation. The optional
bounded CLI --version probe requires an explicit owner flag. Results never
promote independent product acceptance or claim that a real provider is ready.
On Windows, owner connection replacement now preserves the previous
reference if promoting a verified candidate fails.

## Local hybrid owner observation for application integration

The Native Foundation interface `ProductionCoordinator::with_local_hybrid_project_owner`
accepts the current project ID, full revision stamp and the connection path already
selected by the trusted application. On a Linux provider host it observes the
existing closed hybrid owner layout (`project`, `output`, `runtime/broker.sock`
and `config`). It requires the existing three-driver, network-off owner policy
and both filesystem grants; it does not create or broaden that configuration.

The observation retains file and directory handles, original configuration bytes,
the selected connection and project digest. A bounded local socket connection
sends no protocol request and identifies the actual peer with kernel credentials.
The observed process must be the same live `semwrightd` generation running that
configuration and socket. Rechecks refuse changed paths, files, permissions,
process identity, project revision or source bytes. Existing source snapshots
remain a refusal to replay, not permission to overwrite them.

This interface performs no render, creates no accounting root and issues no
application effect grant. Its caller must still obtain the normal one-time
RenderLocal grant before the existing hybrid producer, which keeps its SDK
permission checks, cumulative budget and unresolved-outcome fences. Unsupported
hosts return an explicit error before local observation. No fallback turns a
saved JSON report into live authority.

The original CI grant entry point remains separate and keeps its run, source,
SDK and original-receipt binding. A local source custody record identifies a
local observation explicitly; it does not claim an original CI grant receipt.
This Foundation API does not by itself complete the Studio hybrid creative
journey or expand the current three-contribution renderer vocabulary.


Newly produced hybrid masters retain their own bounded reopen context in the
original mux intent and terminal result. The existing saved-master inventory,
read-only player and fresh-delivery qualification can recover that original
after all Store/coordinator instances are closed. Hybrid context keeps the
180-frame source and 32 MiB master budget; ordinary masters retain their existing
limits. Delivery still verifies the original video, source WAV, decoded PCM and
current project/owner before a new destination grant. Historical muxes without
that context remain unavailable to this reader; no receipt is backfilled and no
old production permission, accounting root or unresolved outcome is restored.


### Desktop admission for the finite hybrid master

Studio integrations can use preflightNativeHybrid and renderNativeHybridMaster
from apps/studio/src/api.ts. Studio and the Rust boundary both check options against the saved delivery recipe.
The render helper requires the existing delivery draft barrier and checks it
before preflight, after preflight and after the normal RenderLocal permission.
The Desktop commands are native_hybrid_preflight and render_native_hybrid_master;
only the provisioned local owner webview may invoke them.

The read-only preflight compiles both Motion Canvas sources and the static Manim
source, verifies the original WAV and observes the existing local owner. It
creates no root, receipt, staged file or renderer invocation. The render keeps
one coordinator through sources, timeline and master. Original audio is selected
from the sealed source result; staging remains after the first charged admission.

A completed response contains the original master SHA and, when discovery is
available, a saved-master hint for the existing review and fresh-permission
delivery APIs. Lost review capacity or a closed owner after completion keeps
the completion and a recovery notice. Failures retain their stage, original
outcome certainty and possible prior effects, with automatic retry disabled.
No caller path, Native result, resource budget or replacement owner is accepted.

This entry point retains the implemented Linux MC/static-Manim/MC route:
three contiguous, frame-aligned contributions totaling 180 frames, 1080p30
Rec.709 H264/AAC, original stereo 48 kHz PCM, the original bundled text fonts,
and a 32 MiB master. MLT inserts each source at its saved cut boundary after
verifying the original contribution's frame count.


The Output profiles panel now exposes this route for saved mixed-renderer cuts:
Check hybrid sources, Create hybrid master, Review completed hybrid original,
then Export saved original MP4. Source checks remain read-only; rendering and
delivery each require their own existing permission. A revision or draft change
during permission stops dispatch. An uncertain original cannot be retried from
this panel, and a completion arriving after a revision change remains with its
original inputs. Saved masters still provide the cold-reopen route. Ordinary
Motion Canvas mastering applies to cuts selecting only Motion Canvas scenes.
