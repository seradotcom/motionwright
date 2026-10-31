# Third-party notices

Motionwright is AGPL-3.0-or-later. Dependencies remain subject to their own licenses.

## Semwright

Motionwright consumes the public Semwright source repository at an exact revision recorded in `SOURCE_LOCK.json`. At the observed revision, Semwright declares MIT OR Apache-2.0 licensing. Motionwright does not copy or relicense Semwright source in this repository.

## Impeccable

Development-only design guidance may use `pbakaus/impeccable` under its Apache-2.0 license. That tooling is not a Motionwright runtime dependency or part of the distributed application. Original third-party licensing remains applicable to any reused material.

## gtk-rs glib 0.18.5 — source-bound v0.1 security backport

The Linux Tauri/GTK3 dependency graph uses glib 0.18.5. For the v0.1 security remediation candidate, Motionwright retains a source-exact copy of the crates.io release in `tooling/upstream-crates/glib-0.18.5.crate` and a vendored copy in `vendor/glib-0.18.5/`. The original upstream package declares the **MIT** license; its full copyright, license, and notices are preserved in the vendored directory. The sole source modification is the two-token Rust FFI out-parameter fix in `src/variant_iter.rs` to address the unsound `VariantStrIter::impl_get`. Its independent source-digest and complete 121-file verification is enforced by `tooling/v01_glib_product_admission.py`. Upstream advisory RUSTSEC-2024-0429 remains tracked as open pending actual built-product and distribution verification.

Other dependency notices will be generated/validated in CI as the dependency graph is finalized.
