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
