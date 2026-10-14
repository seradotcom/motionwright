# Motionwright v0.1 — glib 0.18.5 owner-chain review

**Scope:** Harden the existing release-blocking RustSec-2024-0429
warning policy. The known glib advisory is OPEN and this change does not
patch, suppress or downgrade it.

## Why the original source policy was incomplete

The root Cargo.lock continues to pin glib 0.18.5. The advisory identifies
unsound Iterator implementations in the VariantStrIter API and lists
glib >=0.20.0 as patched. The reviewed Tauri 2.12.1 / GTK3 0.18.2 /
WebKitGTK 2.0.2 Linux dependency stack still resolves glib 0.18.5 and
cannot independently force a semver-incompatible glib 0.20 upgrade.

The original security_advisory_policy.py already rejects unrecognized
RustSec warning IDs/classes, vulnerabilities, changed locked versions
and direct first-party use of the affected iterator type. The reviewed
first-party token checks also now forbid the actual array_iter_str method
entry point; this is a source string check, not a whole-program call graph.
However, two changes could
go unnoticed without modifying glib's version:

- A different crate could directly import glib 0.18.5 while leaving
  the original GTK3 path intact.
- An unreviewed new client could depend on an already-allowed GTK/Wry
  crate, creating a second indirect path to glib or adding exposure in
  a non-desktop Motionwright crate.

## Bounded source graph review

tooling/glib_owner_chain.py reconstructs the reverse dependency graph
from Cargo.lock using exact crate name and semver-qualified dependencies.
It fails closed on duplicate/missing/ambiguous identities and requires:

- The 12 exact reviewed direct owners of glib 0.18.5.
- The complete exact 22-node reviewed reverse-owner chain, including
  Tauri 2.12.1, Wry 0.57.0, GTK3 0.18.2 and WebKitGTK 2.0.2.
- A single first-party owner, motionwright-desktop 0.1.0, and no
  additional unrelated root or non-desktop first-party crate.
- Registry source identity and present SHA-256 checksum fields for all
  reviewed transitive crate identities. It validates checksum format;
  it does not independently re-download crates or attest signatures.
- The status remains OPEN_UPSTREAM_BLOCKED and the old warning-set
  check still refuses any *new vulnerability*.

The reviewed direct and full reverse-owner sets are explicit in
docs/security/known-rustsec-advisories.json. Any Cargo.lock drift, new
owner, changed reviewed source, removed owner, second root or revised
advisory policy requires a new human dependency review. CI runs the
guard through Security and Delivery's existing source-boundary tests
and before the pinned cargo-audit check, and retains a bounded evidence
receipt with the exact source/review scope.

The 16 new adversarial Python tests reproduce direct and indirect
foreign-owner injection, first-party non-desktop reachability, changed
registry origin, missing checksum, fake patched advisory state and
lockfile spoofing. Original advisory tests continue to run.

## Important limitations

A Cargo.lock dependency graph includes packages for multiple platforms;
it does **not** by itself prove Linux build reachability for each edge,
nor that a vulnerable glib iterator is invoked or unreachable. The
existing first-party string check reduces obvious direct use but is not
a whole-program call-graph proof. This guard does not create an exception,
turn cargo-audit warnings into PASS, or declare the upstream vulnerability
resolved. A coordinated patched Linux stack with real installed package
and regression tests is still required to close issue #89.

Separate blockers #49 (native provider loss), #91 (foreground MLT cleanup)
and #126 (independent acceptance and signing) also remain OPEN. No one
of these source policies authorizes the Motionwright v0.1.0 release.
