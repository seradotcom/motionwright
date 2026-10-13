# Motionwright v0.1.0 — verified release-blocker assessment

The code in Cargo.toml and tauri.conf.json declares **0.1.0**. That is a
source package version, **not** a stable release or an independent product
PASS. This assessment keeps technical candidate evidence separate from
permission to distribute an accepted, signed product.

## Read-only source assessment

Run against an exact commit checkout:

    python3 tooling/v01_release_readiness.py \
      --source-sha "$(git rev-parse HEAD)" \
      --verify-checkout \
      --expect-blocked \
      --output ci-evidence/v01-release-readiness.json

Use an empty output location. The reporter creates a **new file only** and
will not replace an existing assessment from another SHA. Without --output,
only a compact sanitized status is printed. The 40-character SHA is caller-supplied. The `--verify-checkout` flag
(required by v0.1 security CI) verifies that the local Git HEAD matches
the requested SHA and the checkout root is exact. The six report metadata
files must be tracked regular files (no symbolic links or Windows NTFS
junctions in files or intermediate directories). The entire tracked worktree
must also match that commit. The check runs before and after parsing. Dirty or missing inputs,
linked metadata paths and mismatched commits fail closed; unrelated untracked
CI artifacts are allowed. This avoids reading metadata through a clean
Git-tracked symlink whose external target can change outside the commit.

This local Git comparison is not remote-origin attestation, a signed
GitHub workflow receipt, installed-binary provenance, or human review.
Without `--verify-checkout`, the output explicitly records that checkout
binding was not checked. CI still relies on separate source/artifact gates.

The report includes the 208 requirement implementation/verification counts,
the statuses of the 60 private acceptance cases, the exact Semwright Native
SDK source pin, known open RustSec warning IDs, and conservative release
blockers. It intentionally omits the private acceptance specification,
sensitive review media, reviewer identities, connection paths and tokens.

The status is currently BLOCKED_PENDING_EXTERNAL_EVIDENCE. The workflow
**passes when it confirms that blocked verdict is source-consistent**. A green
check is not permission to publish, sign, notarize, recruit users, or promote
a test case to human PASS.

## What automated GitHub Actions now establishes

The latest installed candidate workflows provide **source-specific**
technical evidence for Linux AppImage/Debian, Windows NSIS, and macOS DMG
structure. In disposable runners, Windows NSIS and Linux AppImage both
exercise fresh desktop startup, committed local SQLite creation, clean
relaunch and retained project identity. Native Film/Motion Canvas/MLT
workflows test real provider-generated media against the pinned Semwright
Broker/Driver Host. Build CI, source policies, exact-SHA provenance, and
safe first-use preflight reports are independently useful.

They do **not** establish an owner-provisioned native Broker running in a
fresh physical-device installation, signing/notarization acceptance, human
aesthetic review, or unrestricted creative capability.

## The remaining independent gates

1. Audit the original private v0.1.0 specification's **208 requirements**;
   do not infer that an EVIDENCE_PRESENT implementation status meets every
   criterion in the private text.
2. Execute all **60 acceptance scenarios** on actual installed builds.
   Use the separate private intake process in
   docs/acceptance/REVIEW_INTAKE.md and provide source- and file-hash-bound
   primary evidence plus a distinct evaluator. All 60 are still NOT_RUN in
   the public ledger until an authorized reviewer makes genuine findings.
3. Run a verified first live Semwright connection and original render in
   the installed app using the exact pinned SDK, real owner Broker/Driver
   Host and explicit effect grant; do not confuse the safe CLI --version
   preflight with a production provider handshake. The v0.1 [first-owner
   render guard](v01-owner-first-render-gate.md) now prevents dispatch before
   same-revision Film and local CLI checks, but is not native owner evidence.
4. Resolve or explicitly adjudicate open provider lifecycle/cleanup issues
   #49 and #91 and document release implications. An optional [two-lifetime
   native CI reproducibility check](reliability/v01-native-provider-repeat.md)
   gathers controlled process and frame evidence but cannot prove provider
   generation loss is fixed or authenticate an installed owner's session. No undocumented deletion
   of native MLT projects is allowed. The read-only [MLT lifetime receipt
   inventory](reliability/v01-mlt-cleanup-inventory.md) now exposes an
   incomplete closure disposition without querying or deleting live resources.
   It does not satisfy the required foreground Broker authorization. The bounded v0.1 [read-only original-job
   status observation](reliability/v01-owner-native-status-read.md) does not
   fix provider lifecycle, verify native media or authorize a release.
   The [sanitized native AV CI failure receipt](reliability/v01-native-av-failure-redaction.md)
   also preserves Timeout-to-inactive-generation evidence without publishing
   owner paths or opaque job IDs, but does not remediate the provider. The
   [passive process correlation](reliability/v01-provider-loss-correlation.md)
   records daemon/sandbox count changes without identifying an individual
   provider exit, repairing the generation, or verifying the original job.
   The [shared native CLI error redaction](reliability/v01-native-cli-failure-redaction.md)
   additionally protects baseline Motion Canvas, MLT and Native SDK CI
   failures from leaking argv or untrusted native status payloads. It
   neither retries an uncertain mutation nor resolves provider lifecycle.
5. Resolve the transitive glib unsoundness and macro-stack maintenance
   warnings that remain open in
   docs/security/known-rustsec-advisories.json. An unchanged advisory set
   and a passing cargo-audit *policy* do not mean the warnings disappeared.
6. Obtain intentional code-signing and notarization evidence where
   appropriate for the target operating systems, with independent
   installed-device smoke tests and publisher authorization.

There is **no automatic conversion** from a synthetic test, job SUCCESS,
read-only installer smoke or this machine-generated report to release
authorization. No stable v0.1.0 GitHub Release is created by this workflow.
The existing technical pre-release remains a separate proof.

## Engineering contract

tooling/v01_release_readiness.py is a side-effect-free source/Git metadata reader
except for its explicitly requested new JSON receipt. It fails closed for
duplicate JSON fields, malformed IDs, inconsistent Cargo/Tauri versions,
missing Semwright pins, forged VERIFIED requirements, unsupported acceptance
PASS, and silently closed dependency warnings. Unit tests deliberately
prove that even a synthetic 60/60 PASS with warning-free source metadata
**cannot authorize a release** without independent external review.
