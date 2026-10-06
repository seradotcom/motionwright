# Security boundary

Motionwright is a local-first creative application. Security claims in this repository are limited to controls that can be inspected or exercised from the exact source revision under test.

## Desktop WebView

The Tauri shell uses an explicit Content Security Policy. Application scripts are self-hosted; remote script origins and dynamic-code allowances are not permitted. Remote frames, embedded objects, form submission and base-URL rewriting are disabled. The WebView may reach only the Tauri IPC origin declared by the shell.

The desktop crate does not include Tauri shell, filesystem, HTTP or process plugins. Privileged application operations are explicit Rust commands backed by the Motionwright service. Canonical Semwright execution uses its separately verified Native SDK/owner connection rather than a generic WebView shell escape.

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

## Credentials and private material

Project bundles are not credential containers. Semwright connection material is owner-provisioned outside project state through the canonical connection boundary. Public source policy rejects common credential signatures, private-key material, private coordination-package markers and absolute developer-home paths.

No CI workflow in this repository automatically publishes a release or uploads user media.

## Exact-source delivery manifest

`tooling/release_manifest.py` produces a machine-readable manifest for a tested checkout. It records:

- the exact 40-character Motionwright source revision;
- package version, repository and AGPL license;
- the exact pinned Semwright revision and Native SDK package;
- SHA-256 hashes for Cargo/npm locks, `SOURCE_LOCK.json`, the license and third-party notices;
- the declared delivery boundary for publishing, project receipts and secrets.

The Security and Delivery workflow publishes that manifest as CI evidence. It is evidence about the tested source tree, not a code-signing or notarization claim.

## Evidence not yet claimed

This repository does not turn missing evidence into a PASS. In particular, platform tenant isolation, release signing/notarization, all renderer sandbox properties and production penetration testing require their own execution evidence. Optional or upstream-dependent capabilities remain gated until their real runtime is exercised.
