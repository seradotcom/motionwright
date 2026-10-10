# Canonical Semwright sealed-browser size gap — owner/security review

**Status:** BLOCKED ON CURRENT CORE CONTRACT. This document does not authorize a Core change or claim that the new Motionwright v0.5 renderer is already bookable/executable through the canonical Host.

## Exact reproducible evidence

Semwright's verified `origin/main` on 2026-10-09 is
`d2da9a495a53fe279a1ca4de61f0e24646350f22`.
The last Motionwright locked integration source was
`8fa191250ae68274182570c65f067f7a60f85625`.
Both publish `MAX_SEALED_TOOL_EXECUTABLE_BYTES=256 * 1024 * 1024`
at `crates/platform-linux-sys/src/launch.rs`. Neither limit has been
changed or silently bypassed.

The verified Playwright 1.55.1 / Chromium 140.0.7339.186 v1193 installation
returned these SHA-bound candidate sizes in GitHub Actions run
`38009988089`, `sealed-browser-admission`:

| Executable | Bytes | SHA-256 | Fits current Host? |
| --- | ---: | --- | --- |
| Full Chromium | 460,016,488 | `5386de944dc312f838d69a974253d23bdb029acc072e28e6b11aa478c8b504d5` | No |
| Chromium Headless Shell | 304,364,144 | `003728e0b77eb9d52e4d258594bd55ce22ecd245eb6d3b6858fbd844c901ad7d` | No |

Both files were installed exclusively in the owner-pinned
`runtime/hyperframes/.browsers` tree. The direct first-party native
profile can run with Chromium's own user-namespace sandbox and has
source-bound frame, exact clock, opacity, transparency, type and FFV1
evidence in multiple GitHub Actions executions.

When the identical typed job runs via canonical Semwright Broker/Driver Host,
the user-supplied `document`, `source`, `runtime`, `modules`, `assets`
and `fonts` gates pass. A browser launch from the readonly runtime **data**
mount is rejected with `browser_binary_exec_denied`. This is an expected
Host security control and must **not** be weakened. No canonical Broker render
has passed, and comparing its frames to a competent direct-engine agent
remains an unmet gate.

## Required direction: existing Host capability, not a new backend

A proposed host extension (for the Semwright Core owners to review) must
provide a bounded, **owner-approved** large sealed executable class, keeping
the normal default maximum of 256 MiB unchanged. The targeted first class
is a Linux-only sealed-browser executable, at most **320 MiB**. This
accommodates the pinned 304,364,144-byte headless shell without admitting
460,016,488-byte full Chromium.

Suggested manifest contract, **not an implemented or granted field**:

    tools:
      - name: chromium
        root: owner-chromium-binary
        sha256: 003728e0...
        sealed_executable_profile: owner_reviewed_linux_browser_320mib

Critical invariants:

1. Default sealed tools remain capped at 256 MiB, even in old manifests.
   Unknown declarations fail closed on existing Hosts.
2. The owner must affirmatively install/approve a **versioned manifest**
   naming one actual regular executable and exact SHA; agents cannot request
   or upgrade the larger size class through a model command, URL, environment
   toggle or untrusted document.
3. Before staging and **again** before execution, the Host verifies SHA,
   size, regular-file and no-symlink constraints with safe open handles.
   Changing a file between verification and launch must fail.
4. A larger executable is still copied into an ephemeral, privately staged,
   Host-sealed executable descriptor. The material remains in the existing
   binary-code mount (e.g. `/plugin/tools/chromium`); neither workspaces nor
   installed npm packages become executable data mounts.
5. The Host continues enforcing Bubblewrap, AppArmor,
   `NoNewPrivs`, restricted network, no ambient filesystem, fixed
   tool/dependency allowlists, explicit runtime job identity, cancellation
   and its process/file/CPU/RSS limits. Larger binary byte count must not
   imply greater runtime privileges.
6. Limit cumulative staged bytes per driver and maximum concurrent
   admitted large binaries to prevent denial-of-service from independent
   huge memfd copies. Release all sealed descriptors on cancellation/error.
7. Owner policy and tooling must have negative tests for oversized,
   unsigned, stale-digest, cyclic dependency, symlink and ungranted binaries.
   Live checksum/size checks must happen on the **actual execution file**,
   not merely a string recorded in a manifest.
8. Platform portability matters: Linux-only profile cannot change Windows
   Job Objects or macOS App Sandbox semantics without their own review.

This **is not** a request to raise the global default to 320 MiB. Generic
untrusted driver manifests must not be able to grant themselves a larger
class. Security owners should decide whether to expose this as a versioned
manifest permission with a separate owner-specific approval gate, or
implement a less costly general sealed-browser-host primitive.

## Candidate Motionwright integration *after* owner Core admission

Once that Core feature exists and passes independent conformance, a
Motionwright-specific implementation should:

- Pin the reviewed Semwright Core SHA in `SOURCE_LOCK.json` and fail
  on mismatch; never silently run against a historic checkout.
- Add the Chromium binary as a **separately sealed Host dependency** of
  `hyperframes-runner` and pass its Host-owned tool path through typed argv.
- Prefer `chromium_headless_shell` only where its full runtime dependency
  tree and exact browser version remain certified. Do not substitute full
  Chromium, an ambient `which chromium`, or shell commands.
- Continue using the already reviewed rootless, no-new-privileges,
  enforced AppArmor/Bubblewrap condition for nested sandbox selection.
  Outside that exact Host, require Chromium's native sandbox. Never adopt a
  project-provided `--no-sandbox` flag.
- Run the **same** typed input through canonical Broker and direct
  HyperFrames on disposable CI. Require equal frame counts, 90/90 pixel
  hashes for the acceptance fixture, exact 30000/1001 timing, source digest,
  alpha, native effect readback, no ungranted request, same logical attempt
  reuse, and stale revision refusal.
- Require explicit human approval of actual contact sheets and temporal
  video; binary admission and technical CI success do not imply creative
  superiority over HyperFrames alone.

## Current operating policy

Motionwright `native-profile` direct rendering and 27 source-bound HTML
recipe previews remain available as independently tested source-producing
tools. A Host-mediated native result **must not** be represented as verified
until the canonical job proves its actual rendered frames and preserves its
same-source signature. Do not suppress the failing `canonical-broker` gate,
assert that v0.5 is fully production-ready, disable AppArmor, execute data
mounts, or merge unreviewed Semwright Core changes.

The failing `canonical-broker` CI is a product blocker, not an ignorable
flaky-test status.


## Potential module-relative resource bundle dependency (under investigation)

The Linux Host currently materializes an executable `/plugin/tools/chromium`
from a sealed Host descriptor. Chromium's sources load ICU and headless
resource packs using a path derived from the executable/module directory:
see [Chromium ICU initialization](https://chromium.googlesource.com/chromium/src/+/1cde1cb8d/base/i18n/icu_util.cc)
and [Headless ResourceBundle initialization](https://chromium.googlesource.com/chromium/src/+/111.0.5563.110/headless/lib/headless_content_main_delegate.cc).
A browser launched from a different module directory can therefore lack
`icudtl.dat` or other non-executable `.pak`/snapshot resources even though
the exact binary digest and execution permission are correct.

**This is a hypothesis, not an established cause for the latest Host failure.**
The E2E now emits only a fixed, scrubbed error class for the owner-installed
binary version-probe failure. We must wait for source-bound CI evidence
before changing the Host.

If module-relative sidecars are confirmed necessary, the correct follow-up
is not to execute the original browser from a data mount. Extend the
**existing Semwright Host** with an owner-approved, versioned bundle of
strictly named, SHA-256-verified **read-only non-executable data files**
mounted alongside the individually sealed executable, without granting
write or execute rights to any source directory. Host-managed immutable
sidecars must be bounded by individual and aggregate budgets, reject
symlink path traversal and digest drift, remain unavailable to other tools
unless explicitly granted, and be dropped on cleanup. Shared libraries are
**executable code** and require their own independently reviewed sealed-tool
authority; they must not be smuggled into a data-only sidecar exception.

A generic mount of the whole npm/browser tree at `/plugin/tools` would
make the confinement review much harder and is **not** approved here.
Document the exact sidecars required by the Playwright-pinned browser,
create adversarial fixture tests, verify that no private source can
select runtime sidecars, and retest byte-identical direct/Host pixels
before treating this as resolved.
