# Security boundary

Motionwright is a local-first creative application. Security claims in this repository are limited to controls that can be inspected or exercised from the exact source revision under test.

## Desktop WebView

The Tauri shell uses an explicit Content Security Policy. Application scripts are self-hosted; remote script origins and dynamic-code allowances are not permitted. Remote frames, embedded objects, form submission and base-URL rewriting are disabled. The WebView may reach only the Tauri IPC origin declared by the shell.

The desktop crate does not include Tauri shell, filesystem, HTTP or process plugins. Privileged application operations are explicit Rust commands backed by the Motionwright service. Source-verified native PNG preview uses only bounded binary IPC responses and server-minted session handles, never generic path-based file access; canonical AV mastering accepts only a verified session render handle and an existing measured VoiceTrack ID, with one-time effect grant prior to any owner-output file staging; see [native frame preview](native-frame-preview.md) and [one-time, source-verified MP4 delivery](master-verified-export.md). Canonical Semwright execution uses its separately verified Native SDK/owner connection rather than a generic WebView shell escape.

`tooling/source_policy.py` checks this boundary on every normal CI run and rejects dynamic-code primitives in application UI source.

## Controlled local imports

Asset admission is content-addressed and fail-closed:

- input files must be regular files, not symlinks or special filesystem entries;
- imported bytes are hashed before a project change can reference them;
- content-addressed storage is re-hashed before commit and on bounded reads;
- SVG is inspected even when its extension or declared media type is misleading;
- static SVG is allowed, while scripts, event handlers, external references, active embedded content, CSS URL loading, XML entities and external stylesheets are rejected;
- measured voice evidence enters through the dedicated audio import boundary rather than a caller-authored project mutation.

These checks do not claim that every media decoder is memory-safe. Decoder/runtime vulnerability management remains a dependency and release concern.

## Portable project bundles

A portable project bundle is a directory containing `manifest.json` and the exact blobs referenced by the project document. Import rejects:

- a symlinked bundle root;
- a symlinked manifest or blob;
- non-canonical blob paths;
- duplicate or unexpected blob digests;
- byte-size or SHA-256 mismatch;
- backup/journal corruption;
- unsupported future schemas.

Portable backups intentionally exclude execution request receipts. Import rotates the project generation so stale callers cannot resume writes against an authority recreated from an older package.

## Effect-specific desktop authority

Privileged desktop operations do not share one ambient application permission. Motionwright issues short-lived, in-memory, one-time capabilities for distinct local effects: project edit, local import, local render, local delivery and canonical workflow mutation. Project-scoped grants are tied to the exact project generation and revision plus an operation-specific subject, and a token is removed on first use even when validation fails.

Remote egress, external upload, runtime installation and publication are explicit effect kinds that cannot be granted by the current runtime. An edit or render capability therefore cannot be reused as network or publishing authority. Portable-project import is the only supported grant without an existing project scope because the destination project does not exist yet; it remains bound to the local import effect and exact source path.

The Studio requests the narrow capability immediately before its matching native operation. This is application intent separation, **not** proof of human approval, actor identity, organization authorization or consent. Extension permission descriptors likewise remain requests rather than executable authority. See `docs/security/R30_EFFECT_GRANTS.md` for the exact boundary and regression evidence.

## Credentials and private material

Project bundles are not credential containers. Semwright connection material is owner-provisioned outside project state through the canonical connection boundary. Public source policy rejects common credential signatures, private-key material, private coordination-package markers and absolute developer-home paths.

Portable export adds a runtime boundary rather than relying only on repository scanning. Project metadata and history are scanned before an export destination is created. Text-like content-addressed assets are scanned before copy, and portable import inspection applies the same checks before ingestion. Matching values are not echoed in errors. Text-like assets above the bounded scan budget fail closed; binary media remains digest-verified but is not interpreted as text.

No CI workflow in this repository automatically publishes a release or uploads user media.

## Exact-source delivery manifest

`tooling/release_manifest.py` produces a machine-readable manifest for a tested checkout. It records:

- the exact 40-character Motionwright source revision;
- package version, repository and AGPL license;
- the exact pinned Semwright revision and Native SDK package;
- SHA-256 hashes for Cargo/npm locks, `SOURCE_LOCK.json`, the license and third-party notices;
- the declared delivery boundary for publishing, project receipts and secrets.

The Security and Delivery workflow publishes that manifest as CI evidence. It is evidence about the tested source tree, not a code-signing or notarization claim.

## Dependency advisory policy

Dependency scanning is fail-closed around an explicitly reviewed warning set rather than treating a
zero exit code from `cargo audit` as proof that the dependency graph is clean.
`docs/security/known-rustsec-advisories.json` records the exact package versions, advisory classes,
dependency context and re-review triggers for warnings that remain open.

The current Linux desktop graph has two open transitive RustSec warnings:

- `RUSTSEC-2024-0429`: `glib 0.18.5`, classified by RustSec/cargo-audit as unsound. The affected
  `VariantStrIter` API is not used directly by Motionwright first-party source. The warning remains
  open because the reviewed stable Tauri/GTK dependency graph currently resolves the GTK 0.18/glib
  0.18 line; absence of direct use is a mitigation, not a fix.
- `RUSTSEC-2024-0370`: `proc-macro-error 1.0.4`, classified as unmaintained and reached
  transitively through the GTK 0.18 macro stack.

`tooling/security_advisory_policy.py` binds that review to the exact lockfile versions, rejects
first-party use of any explicitly affected API token, requires zero reported vulnerabilities and
requires the live `cargo audit --json` warning set to match the reviewed IDs and classes exactly.
A new warning, a removed warning, a changed class or a dependency-version drift fails the gate and
requires a fresh review. The Security and Delivery workflow stores the raw audit JSON and policy
result per SHA. Heavy Verification runs the same policy; neither workflow relabels an open warning
as resolved.

Source policy additionally rejects new first-party raw process or direct-network primitives unless
they are in the small reviewed execution boundary. Today raw process execution is limited to the
canonical Semwright production connection and the isolated Manim Community runtime runner.

## Evidence not yet claimed

This repository does not turn missing evidence into a PASS. In particular, platform tenant isolation, release signing/notarization, all renderer sandbox properties and production penetration testing require their own execution evidence. Optional or upstream-dependent capabilities remain gated until their real runtime is exercised.
