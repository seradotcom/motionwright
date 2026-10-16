# Motionwright v0.1 — actual desktop glib 0.18.5 source backport candidate

**Scope:** security issue #89, RUSTSEC-2024-0429. This advances
the validated experiment in PR #234 to a source-bound product integration
candidate. This is not a claim of a patched installed binary, a warning
waiver, or approval to release v0.1.

## Reproducible source and Cargo graph

- tooling/upstream-crates/glib-0.18.5.crate is the exact public crates.io
  archive, pinned to SHA-256
  233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5.
- vendor/glib-0.18.5/ contains the full original 121-file crate, including
  upstream MIT LICENSE and COPYRIGHT. Only the two independently checked
  FFI mutability edits in src/variant_iter.rs are allowed: let p becomes
  let mut p, and the corresponding out-parameter &p becomes &mut p
  in VariantStrIter::impl_get.
- Root Cargo.toml adds exactly one reviewed override under patch.crates-io,
  glib = { path = "vendor/glib-0.18.5" }.
  Cargo.lock then resolves glib 0.18.5 as a local path with no registry
  source/checksum. Reverting to registry glib 0.18.5 fails source admission.
- tooling/v01_glib_product_admission.py checks the immutable upstream archive,
  all 121 vendored files, only the allowed changes, workspace override
  and Cargo.lock selection. The original archive and vendored MIT license
  are retained; no build-time remote download or mutable Git ref is needed.
- tooling/glib_owner_chain.py still requires every reviewed Tauri, Wry,
  GTK3 and WebKitGTK ancestor, with original crates.io checksums for all
  other dependencies. Only the glib source-exact local override is permitted.

## Live validation required for product admission

1. Exact-SHA CI must run source policy, reviewed owner chain and adversarial
   tests. Any dependency graph drift or new advisory fails.
2. Real Linux desktop compilation must select the corrected source, build
   the actual Tauri app and pass existing security tests and installed
   AppImage/DEB GUI launch and reopen checks.
3. Windows NSIS and macOS DMG candidates must remain source-bound and
   buildable, with signed GitHub workflow provenance (not OS code signing).
4. Cargo-audit/RustSec and Dependabot must be reviewed as genuine issues.
   Since the original glib semantic version remains 0.18.5, the advisory
   may continue to appear by version even when source bytes are changed.
   Do not silently suppress, ignore, or label it FIXED merely to pass CI.
5. Human owner-installed application validation, native provider recovery,
   foreground-authorized MLT cleanup and all 60 independent private human
   acceptance cases remain separate v0.1 release gates.

## Why a backport rather than a forced independent glib 0.20 update

The reviewed GTK3/Tauri/WebKitGTK stack resolves gtk-rs 0.18.x.
Forcing a glib 0.20 replacement alone crosses incompatible 0.x types.
This bounded source fix follows the Debian 0.18.5-7 security patch:
https://sources.debian.org/patches/rust-glib-0.18/0.18.5-7/

Issue #89 remains OPEN until the full patched product is independently
validated. Issues #49 (provider lifetime), #91 (MLT consent and lifetime),
and #126 (release acceptance) remain independently open. The public
ledger still records 0/208 independently verified requirements and 0/60
accepted private scenarios. No v0.5 creative code, Semwright Native SDK
source pin, Broker authorization or original native operation is modified.
