# Security boundary

Motionwright is a local-first creative application. Security claims in this repository are limited to controls that can be inspected or exercised from the exact source revision under test.

## Desktop WebView

The Tauri shell uses an explicit Content Security Policy. Application scripts are self-hosted; remote script origins and dynamic-code allowances are not permitted. Remote frames, embedded objects, form submission and base-URL rewriting are disabled. The WebView may reach only the Tauri IPC origin declared by the shell.

The desktop crate does not include Tauri shell, filesystem, HTTP or process plugins. Privileged application operations are explicit Rust commands backed by the Motionwright service. Source-verified native PNG preview uses only bounded binary IPC responses and server-minted session handles, never generic path-based file access; canonical AV mastering accepts only a verified session render handle and an existing measured VoiceTrack ID, with one-time effect grant prior to any owner-output file staging; see [native frame preview](native-frame-preview.md), [one-time, source-verified MP4 delivery](master-verified-export.md), [explicit unsigned portable hash receipt](portable-mp4-integrity.md), and [token-only, 16 MiB-bounded local video review](native-av-review.md). Canonical Semwright execution uses its separately verified Native SDK/owner connection rather than a generic WebView shell escape.

`tooling/source_policy.py` checks this boundary on every normal CI run and rejects dynamic-code primitives in application UI source.

## Controlled local imports

Asset admission is content-addressed and fail-closed:

- input files must be regular files, not symlinks or special filesystem entries;
- imported bytes are hashed before a project change can reference them;
- content-addressed storage is re-hashed before commit and on bounded reads; [owned CAS shard/staging boundaries](cas-shard-boundary.md) reject symlinked internal directories, use create-new destination admission, and preserve private Unix file permissions;
- SVG is inspected even when its extension or declared media type is misleading;
- static SVG is allowed, while scripts, event handlers, external references, active embedded content, CSS URL loading, XML entities and external stylesheets are rejected;
- measured voice evidence enters through the dedicated audio import boundary rather than a caller-authored project mutation.

The read-only [asset integrity inspection](asset-integrity.md) can rehash existing content-addressed blobs associated with the exact project revision. It accepts no caller-selected source paths and uses bounded per-file and per-page work budgets; this is not Project Graph admission.

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

The current locked dependency graph records three open RustSec advisories:

- `RUSTSEC-2024-0429`: `glib 0.18.5`, classified as unsound and reached through the Linux
  Tauri/GTK stack. The selected local source contains the reviewed two-line upstream backport.
  Its verifier compares the complete vendored source against the authenticated registry archive
  and exact patch, and checks the Cargo override and dependency owner graph. The affected
  `VariantStrIter` API is not used directly by Motionwright first-party source. The upstream
  advisory remains `OPEN_UPSTREAM_BLOCKED`; source verification does not grant product acceptance.
- `RUSTSEC-2026-0192`: `ttf-parser 0.25.1`, classified as unmaintained. The locked graph
  contains a direct Manim profile dependency and a Semwright Motion Canvas dependency through the
  service. The policy binds the registry checksum, SDK source and owner graph; deployed runtime
  reachability remains unverified. This maintenance warning remains `OPEN_UPSTREAM_UNMAINTAINED`.
- `RUSTSEC-2024-0370`: `proc-macro-error 1.0.4`, classified as unmaintained and reached
  transitively through the GTK 0.18 macro stack. It remains `OPEN_UPSTREAM_TRANSITIVE`.

`tooling/security_advisory_policy.py` binds that inventory to the exact lockfile versions, rejects
first-party use of any explicitly affected API token and requires zero reported vulnerabilities.
The live `cargo audit --json` warnings must match the recorded IDs and classes, with one explicit
coverage exception: cargo-audit can omit the local-path `glib` package from its registry warning
output. That absence is permitted only after the complete backport source and owner checks pass;
it is not evidence that the upstream advisory is resolved. Any other missing warning, a new
warning, a changed class or dependency drift fails the gate and requires a fresh review.

The Security and Delivery workflow stores the raw audit JSON and policy result per SHA. Heavy
Verification runs the same policy. All three advisories remain open.

Source policy additionally rejects new first-party raw process or direct-network primitives unless
they are in the small reviewed execution boundary. Today raw process execution is limited to the
canonical Semwright production connection and the isolated Manim Community runtime runner.

## Evidence not yet claimed

This repository does not turn missing evidence into a PASS. The [installed candidate package smoke](installed-candidate-smoke.md) runs only on disposable CI runners; it validates extracted binaries and one rootless Linux AppImage window without weakening Gatekeeper/WebKit security, and still records human installation acceptance as NOT_RUN. In particular, platform tenant isolation, release signing/notarization, all renderer sandbox properties and production penetration testing require their own execution evidence. Optional or upstream-dependent capabilities remain gated until their real runtime is exercised.
