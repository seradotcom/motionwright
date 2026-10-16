# Motionwright v0.1 — isolated glib 0.18.5 backport conformance lab

**Experimental; NOT adopted by the application.** Security issue #89 remains
OPEN. This lab tests a candidate source-level fix for RUSTSEC-2024-0429. It
does not change Motionwright Cargo.lock, the Semwright Native SDK pin, Tauri,
GTK3 or v0.5 creative functionality.

## Original crate and strictly bounded patch

The locked crates.io glib 0.18.5 artifact has SHA-256:

233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5

This is independently downloaded and checked at run time, not accepted from
an untrusted cache. The CI-only source generator refuses an existing output
directory, unsafe archive entry, incorrect digest, symlink, missing file,
extra file or any deviation other than these two edits to the original
src/variant_iter.rs in VariantStrIter::impl_get:

- let p: *mut libc::c_char becomes let mut p: *mut libc::c_char
- the FFI out-parameter &p becomes &mut p

The original crate has 121 files. The CI candidate contains those exact
121 files, with only the expected two edits. The full third-party source is
NOT checked into this repository and is generated only on a disposable CI
runner from the verified archive, so no new code is silently adopted.

The minimal correction follows the source-level rationale of Debian's
rust-glib-0.18 version 0.18.5-7 security backport:
https://sources.debian.org/patches/rust-glib-0.18/0.18.5-7/

That backport fixes a shared Rust reference passed to a C function which
mutates the pointer through its out-parameter. Merely passing an iteration
test does not prove complete memory safety or actual product remediation.

## Reproducible CI experiment

The GitHub workflow v01-glib-backport-conformance.yml:

1. Downloads and checks the immutable original crate bytes and generates the
   exact two-edit candidate in the disposable runner, rejecting all other
   changes via tooling/v01_glib_backport_integrity.py.
2. Runs adversarial Python source-integrity tests. Tests reject archive
   traversal, unexpected modifications, already-patched source and forged
   original archive hashes.
3. Checks that original product Cargo.lock STILL resolves registry glib
   0.18.5 and that no application Cargo patch was inserted.
4. Compiles a separate Rust fixture against the candidate glib source with
   native GLib headers and tests empty, Unicode, forward, reverse and mixed
   VariantStrIter calls.

All Rust builds and GLib system packages stay in GitHub Actions, not on an
owner workstation.

## Why the advisory is still OPEN

This probe does NOT link the candidate into Motionwright's Tauri GTK3/GLib
application, prove a patched AppImage/DEB binary, run a real installed Linux
GUI, or replace the full desktop dependency graph. It intentionally does
not change root Cargo.toml, Cargo.lock or dependencies.

An adoption PR must bind the corrected glib source to the COMPLETE stable
desktop build, independently check its actual compiled/link identity, validate
installed Linux GUI/relaunch and native-production paths, and run Windows and
macOS packaging plus security review. Do not suppress RustSec or Dependabot
only to produce green badges. #89 remains OPEN until there is genuine product
evidence and a defensible resolution.

Release issue #126 still has 0/208 independently VERIFIED requirements and
0/60 private human acceptance cases. Issues #49 provider lifecycle and #91
foreground Broker-consented MLT cleanup remain open independently. No stable
release is authorized.
