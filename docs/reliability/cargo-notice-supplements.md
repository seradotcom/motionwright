# Cargo notice supplements bound to exact source

The Windows captured build at source
`1b4ef8868a06f4ba9539ec29f6aff288ad8cab67`
completed Cargo metadata and compilation, then stopped at notice coverage.
Ten authenticated registry archives did not contain the original notice texts.
This supplement adds retained source material for those exact locked archives.
It does not change that historical result or establish an installed candidate.

## Admission boundary

The collector first authenticates the registry archive against the tracked
Cargo.lock checksum, every ordinary extracted source file against that archive,
and the metadata identity and license declaration against the manifest.
Only a registry component with no original notice path can use this route.
An invalid archive, manifest, declared license-file or existing notice cannot
fall back to a supplement. Other source kinds retain their existing protocols.

`third_party/cargo-notices/index.json` is a bounded, closed schema tracked at the
candidate's exact HEAD. Every entry must match an exact name, version, registry
and checksum in that HEAD's Cargo.lock. Entries identify one fixed component
directory and the exact bytes and SHA-256 of its record. Duplicate JSON keys,
non-finite numbers, duplicate identities, path aliases and alternative roots
are refused. An absent entry still means missing notice text.

Before and after independent verification, the collector authenticates the
complete material directory against HEAD and the clean index. Raw Git bytes
are required; checkout line-ending conversions do not qualify here. The
material namespace is exactly record.json, its declared objects, and its
referenced official documents/receipt. Extra local, ignored or tracked files
are rejected. The index and HEAD are rechecked after verification.
The verifier is loaded only from its physical sibling module.

The original crate members are supplied through a lazy mapping that re-reads
the authenticated physical source and checks its recorded digest. The
independent material reader retains its descriptor and checks ordinary,
single-link files and stable path/descriptor identities. Its maximum read
budget is reserved in the collector's existing global budget; both tracked
material snapshots have a separate bound. These checks protect the controlled
snapshot; they do not claim atomicity against a hostile concurrent writer.

## Retained source joins

Raw commit, tree and blob objects establish the selected source paths at the
exact upstream revision. The commit and each retained object are checked by
their Git object identifier and SHA-256. Archive member coverage is a complete
bijection, not a claim based only on .cargo_vcs_info.json.

| Component | Version | Upstream repository | Exact commit | Package path |
| --- | --- | --- | --- | --- |
| alloc-stdlib | 0.3.0 | dropbox/rust-alloc-no-stdlib | 0a81fd6928ea3b33c8cd484aa4575d50ffb98012 | alloc-stdlib |
| jsonschema-regex | 0.58.6 | Stranger6667/jsonschema | 55ac1664384793c5d3b20745d3f9c169dfe2c06f | crates/jsonschema-regex |
| jsonschema-value | 0.58.6 | Stranger6667/jsonschema | 55ac1664384793c5d3b20745d3f9c169dfe2c06f | crates/jsonschema-value |
| selectors | 0.38.0 | servo/stylo | 572ecba2d1600e7c3d490586692a209faf703baa | selectors |
| tauri-plugin | 2.7.1 | tauri-apps/tauri | 30da1fd6e17de6107ecc850c95dfb16b5729f2dd | crates/tauri-plugin |
| uuid-simd | 0.8.0 | Nugine/simd | d74c030d9dc4f3cae02146d1f497ff62726ef09a | crates/uuid-simd |
| vsimd | 0.8.0 | Nugine/simd | d74c030d9dc4f3cae02146d1f497ff62726ef09a | crates/vsimd |
| webview2-com-macros | 0.8.1 | wravery/webview2-rs | dffa41a8a46d3f5565eefbff2de57d38d399f158 | crates/callback-macros |
| webview2-com-sys | 0.39.1 | wravery/webview2-rs | edc2caf886175ccaebe86078c9cfe1ae2a187328 | crates/bindings |
| webview2-com | 0.39.1 | wravery/webview2-rs | edc2caf886175ccaebe86078c9cfe1ae2a187328 | crates/webview2-com |

The 151 archive members have explicit classifications: 111 exact Git source
members, ten exact original package manifests, ten Cargo-normalized manifests,
ten Cargo VCS metadata files, eight packaged lockfiles, and two narrowly pinned
LF-to-CRLF source expansions. The two SIMD packages have no packaged Cargo.lock.

The expansion exception applies only to webview2-com-macros 0.8.1, its exact
crate checksum, repository, commit, package path, README.md and src/lib.rs.
Both input and output byte counts, SHA-256 values and Git object identifiers
are pinned. No general normalization fallback is available. Normalized
Cargo.toml and packaged Cargo.lock are not reported as Git-byte-identical.

For conventional upstream notices, the record explicitly associates a
LICENSE/LICENCE/COPYING/NOTICE/COPYRIGHT path in the package or an ancestor
with the authenticated manifest's declaration. This does not claim that the
manifest named a license-file. Tauri retains LICENSE-MIT,
LICENSE-APACHE-2.0 and LICENSE.spdx with their actual names and bytes.

Selectors instead retains the exact MPL header in selectors/lib.rs and the
official plain-text MPL document it references. The source header has Git
provenance. The full document has a separate reviewed acquisition receipt
for https://www.mozilla.org/media/MPL/2.0/index.txt, HTTP 200, text/plain,
16726 bytes, SHA-256
`3f3d9e0024b1921b067d6f7f88deb4a60cbe7a78e76c64e3f1d7fc3b779b9d04`.
The receipt is a consistency record, not a cryptographic server attestation.
The document is not represented as a missing upstream Git blob.

## Output and limits

The principal authentication remains registry_archive. Supplement index,
candidate HEAD, material descriptors and upstream source proof are retained
under source_authentication.supplemental_notices. Each notice retains its
source_kind and provenance. Its text is packaged through the existing
digest-named Cargo notice resource pipeline. Upstream objects are not inserted
into the registry archive's authenticated file roster.

The existing Windows contract step runs the offline verifier and integration
tests. No workflow, job, dispatch, source fetch or build is added by this route.

The proof preserves redistribution_permission=NOT_INFERRED and
full_package_inventory=NOT_VERIFIED. License grammar checking does not select
an alternative. Native-content review, including libsqlite3-sys, remains
independent and required. This work grants no R74, G2, G3, Graph6, Native Save,
installer, audio or human acceptance credit.


## Additional Unix source-bound records

The original Linux capture 43376136446 at
`3b7e95b53d4cc9efa7f734f0696ce739696b699b` completed compilation, then refused
notice coverage for the two exact dlopen archives below. Their published crates
omit the original MIT notice, which is retained from their common upstream root.

| Component | Version | Crate SHA-256 | Exact upstream commit | Package path |
| --- | --- | --- | --- | --- |
| dlopen2 | 0.8.2 | 5e2c5bd4158e66d1e215c49b837e11d62f3267b30c92f1d171c4d3105e3dc4d4 | cc80e4a0a90d499b677fdf7743699b4b3a43a989 | dlopen2 |
| dlopen2_derive | 0.4.3 | 0fbbb781877580993a8707ec48672673ec7b81eeba04cfd2310bd28c08e47c8f | cc80e4a0a90d499b677fdf7743699b4b3a43a989 | dlopen2-derive |

The repository is https://github.com/OpenByteDev/dlopen2. Its original
`LICENSE` is 1184 bytes with SHA-256
`39fa265207450e77c62e90c5594a06c085b655d8374c7ced4bf7894b6bd95dd2`.
Both records preserve all 53 archive members: 42 exact source files, two declared
parent README copies, two Cargo-normalized manifests, two packaged lockfiles,
two VCS metadata files, two explicitly transformed original manifests and one
explicitly transformed source file.

The only new line-ending transformations are `Cargo.toml.orig` in each package
and `src/common.rs` in `dlopen2_derive 0.4.3`. Each is an exact LF-to-CRLF
expansion with a pinned component, crate checksum, upstream revision, package
path, member, Git blob identifier, original/packaged size and original/packaged
SHA-256. The original manifest is classified
`git_package_manifest_lf_to_crlf` and carries `git_byte_match=false`.
No transformed manifest is labeled as raw Git bytes.

Each package declares `readme = "../README.md"`; Cargo places the byte-exact root
README at `README.md` in that archive. The new `git_declared_parent_readme`
classification requires this exact declaration and normalized destination,
plus the pinned root blob and exact bytes. It does not authorize arbitrary
path relocation, a symlink traversal or a new license notice.

These two profiles extend the existing closed source verifier only for the
observed packaging identities. The original ten Windows records and their
byte proofs remain unchanged. Missing or altered material still prevents
capture completion. Historical builds remain failed originals; package
installation, native-content review and redistribution clearance remain separate.


## Exact objc2 macOS publication profiles

The original macOS capture 43376141904 at
`3b7e95b53d4cc9efa7f734f0696ce739696b699b` compiled successfully, then refused
notice coverage for nine crates. Their exact official archives and the following
upstream source identities are now represented separately from that failed run.

| Component | Version | Main commit | Package path |
| --- | --- | --- | --- |
| block2 | 0.6.2 | b4167b582b2f75f9a1be75495c41b765344fd03c | crates/block2 |
| dispatch2 | 0.3.1 | 8852b424193ca41602281b3d7540d7c8ed51e49a | crates/dispatch2 |
| objc2 | 0.6.5 | d7d2fa23ceaa5e6096c923b081040e5d81b3b9df | crates/objc2 |
| objc2-app-kit | 0.3.2 | 7b1abfd750a2cacaea71d6a56ecfb83cb7de560b | framework-crates/objc2-app-kit |
| objc2-core-foundation | 0.3.2 | 7b1abfd750a2cacaea71d6a56ecfb83cb7de560b | framework-crates/objc2-core-foundation |
| objc2-encode | 4.1.0 | 8d214f5477365ffcbcbb7de058c86ed9a518efb7 | crates/objc2-encode |
| objc2-exception-helper | 0.1.1 | 8d214f5477365ffcbcbb7de058c86ed9a518efb7 | crates/objc2-exception-helper |
| objc2-quartz-core | 0.3.2 | 7b1abfd750a2cacaea71d6a56ecfb83cb7de560b | framework-crates/objc2-quartz-core |
| objc2-web-kit | 0.3.2 | 7b1abfd750a2cacaea71d6a56ecfb83cb7de560b | framework-crates/objc2-web-kit |

The main repository is https://github.com/madsmtm/objc2. Each profile fixes the
component, version, registry, license expression, crate checksum, repository,
commit and package path together. The 829 members have a complete classification:
205 direct Git source members, nine original package manifests, nine normalized
manifests, nine VCS metadata files, nine packaged lockfiles, 586 submodule copies
and two copies within the main repository. No line-ending transform is needed.

### Materialized source copies

Cargo dereferences `src/generated` while publishing Dispatch and the four
framework packages. Their Git trees preserve a symlink to `generated/<Framework>`.
The `git_pinned_submodule_copy` profile proves the exact link mode, blob and
target text; the original `.gitmodules`; the `generated` gitlink; the secondary
commit/root tree; and the final regular blob. The secondary repository is
https://github.com/madsmtm/objc2-generated, as named by the authenticated
`.gitmodules` URL ending in `.git`.

Dispatch uses commit `872de032903eec3d718cfd3b35056b010c02e4cb`, root tree
`bb64726fff227b0f21a7df7cb1c4169e322f77d9`, with prefix `Dispatch/`.
The four framework profiles use commit
`4c2d6fb86d17ed4b4b7e68e80994788b60521987`, root tree
`c2239b813eec2ba56884fbc068fd0367c9e1a5a0`, with their exact AppKit,
CoreFoundation, QuartzCore or WebKit prefix.

Dispatch and CoreFoundation also copy `src/retained/forwarding_impls.rs` from
`crates/objc2/src/rc/retained_forwarding_impls.rs` in the same main commit.
The `git_pinned_inrepo_copy` class pins the link and destination independently;
its final blob is `d4c61a21974f4952d77cb63c9600c7d8a18cc0fe`, 10145 bytes,
SHA-256 `70c6c9e0e3089c5b5ffbb0535dfb63f355dab2932a20f87b8442cf4ff5f62518`.

All resolution happens over retained Git objects. No filesystem symlink is
followed. The general Git path reader still rejects symlink and gitlink
traversal. Per-profile member sets and counts are required; an ordinary
`git_exact` label cannot conceal a materialization.

### Original notice and current official reference texts

Every profile retains the original root `LICENSE.md`, blob
`540ae4c81adb5f64a9f61e222b688880da24300e`, 1339 bytes, SHA-256
`7f976f7e9cb2d87df7230606feb932c3f21ac0e664045a775b600046ff850c54`.
This notice identifies MIT-only and triple-license packages and preserves the
upstream qualification about Apple SDK-derived code. That qualification is not
removed or resolved by this capture.

The notice references the primary documents below. Acquisition records and
derived text have a separate provenance class; they are not historical Git
blobs or cryptographic server attestations.

| Reference | Primary URL | Retained license text SHA-256 |
| --- | --- | --- |
| MIT | https://opensource.org/license/MIT | 21785883212d06d2c262b7e9f73f92ae7a03a7f2fe86809d7c7ad9bd6961d265 |
| Zlib | https://zlib.net/zlib_license.html | ef6c0da271fdc74603264907c463e4c979db7a93256ee5e1ba54ad5bc46b7e2a |
| Apache-2.0 | https://www.apache.org/licenses/LICENSE-2.0 | cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30 |

MIT retains the byte-exact 1309-byte license fragment at offset 156111 of the
observed 196580-byte response. The full response remains in the private intake,
with SHA-256 `46ca51ae629ef638fef495e2b060b44ad980b451fa1b1ccfa83c9aeeae90c0e7`.
The public material contains only the fragment, its reviewed acquisition record
and the 1068-byte text deterministically derived from four original paragraphs.
HTML character references are decoded; paragraph separators are fixed. Copyright
placeholders remain unfilled.

Zlib retains the complete original 2895-byte HTML response and the exact
1107-byte `PRE` fragment. Its generic reference includes the zlib version and
authors; those names are not attributed to objc2. Apache retains the complete
11358-byte original plain-text response without transformation.

All profiles require the original upstream notice plus every reference named
by their recorded license expression. No license alternative is selected.
The material closure includes exactly the referenced document, text and receipt.
The largest compact record is 56793 bytes; the existing 64-KiB record, 512-object
and 16-MiB proof limits remain unchanged.

These records establish bounded source/text provenance. They preserve
`redistribution_permission=NOT_INFERRED` and `full_package_inventory=NOT_VERIFIED`.
Apple SDK rights, native-content review, installed-package inspection and formal
R74/G2/G3 acceptance remain separate.

## Cargo index envelope shared by producer and readers

Linux capture 43380010798 at `a6b46c493a7b64ad1f67d91feb577015283ce5e3`
completed the actual Cargo build, collected 381 components without notice
issues, and sealed the physical SQLite-output observation. Staging then
refused its 17305904-byte Cargo index because the packaging reader admitted
only 16 MiB. The failed run remains a failure; no bundle was produced.

`MAX_CARGO_INDEX_BYTES` is now a shared, bounded 32-MiB envelope. Capture
checks it before writing either the index or the completion seal. The binary
binding verifier, Windows/Linux/macOS staging, candidate receipt producer and
installed-package reader use the same limit. Oversized physical files still
fail before parsing or publishing a resource overlay.

This change retains the complete provenance record. It does not increase
frontend-index, generic JSON, source-stream, archive, notice-text, proof-record
or proof-object budgets. The trusted index digest, exact source/tree/target,
artifact roster, physical executable bytes, generated-metadata custody and
SQLite-output checks remain required at their existing boundaries.

Focused controls use inert Git/filesystem fixtures at the observed Linux size
and the 32-MiB boundary. They retain digest/substitution refusals and verify
that a 32-MiB-plus-one-byte index cannot publish an overlay. Reading the
retained original index is source-data verification only; the subsequent
hosted package build must demonstrate the new end-to-end packaging result.
