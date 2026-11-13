# Motionwright v0.1 ? current product-source GLib FFI conformance

The existing `v01-glib-backport-conformance.yml` exercises the exact
GLib backport selected by the current product lock. Issue #89 and
RUSTSEC-2024-0429 remain OPEN. This is isolated native FFI component
conformance, not installed-product remediation or release approval.

## Reuse and succession

The historical definition generated a disposable backport and required the
product to retain registry GLib. Product adoption subsequently checked in
`vendor/glib-0.18.5` and selected it with a Cargo path override. Its old
generator correctly refuses an existing directory, and its registry-only
assertion rejects the current local-path lock before reaching the FFI tests.

The successor reuses the source export, isolated runner directories, command
exit records, compiler-artifact observations and failure retention from
`b6581232f4f768c3516c7ea545ec562f6e1b7811`. That source produced four
real FFI passes in run `39162970242`, with a separate unoptimized
fixture lock. Those observations retain their original scope.

The two additional iterator cases and explicit resolver 2 are reused from
A2's frozen `c44c44796ec99c95cf459c1c7a7be09031639a6d` source.
Its original lock is preserved as historical material; the successor seeds
its isolated fixture from the current product lock. The old four-case result
does not provide credit for the six optimized cases or the current graph.

## Exact source and dependency boundary

`tooling/v01_glib_product_admission.py` compares all 121 vendor files
with the retained original archive:

`233daaf6e83ae6a12a52055f568f9d7cf4671dabb78ff9560ab6da230ce00ee5`

Only the two reviewed mutability edits in `VariantStrIter::impl_get`
are admitted. The original archive, MIT license and copyright are retained.
The workflow exports exact regular Git blobs to fresh scratch outside the
checkout and binds both copies before and after the native probe.

The fixture receives a byte-exact copy of product `Cargo.lock`.
Cargo may prune that copy, but the resolution reader refuses selected
names, versions, sources or checksums absent from the original product lock.
Dependency edges must remain within that graph. The sole local dependency
is GLib; Cargo's observed manifest path must identify the admitted vendor.
The resolved graph is closed and reachable from the fixture's one GLib
dependency. The resolved lock and original metadata remain byte-identical
through `cargo test --release --locked`.

## Six physical FFI oracles

The four original Rust tests cover empty and singleton variants, Unicode
forward/reverse iteration, mixed front/back traversal and lengths zero
through 64. The two retained A2 cases exercise `last` after partial
consumption with an empty string, and `nth_back` after forward
consumption. All six named tests must pass exactly once, without ignored or
filtered cases.

The original compiler messages must identify the selected GLib and the
fixture test executable. Both require observed optimization level 3 with
debug assertions disabled. Their output hashes, actual Rust tools, native
GLib version/link flags, source records, lock, metadata, command stdout,
stderr and exit codes are retained. A failed or omitted command cannot
produce a successful conformance verdict. Failure evidence is uploaded too.

All Rust compilation and native GLib headers remain on a disposable
GitHub-hosted Linux runner. Product manifests, product lock, SDK pin and
vendored GLib bytes are not changed by this verification.

## Limits

The fixture uses current product dependency identities and an optimized
profile. It does not reproduce the full desktop feature graph, linker
outcome, installed GUI or native-provider lifecycle. Passing iterator tests
does not prove absence of undefined behavior.

The real Linux package and installed GUI observations keep their own source
and binary identities. Windows/macOS packaging, native production,
independent review, advisory disposition, signing and human acceptance
remain separate obligations. This workflow does not close #89, waive any
warning or authorize a release.
\nA source-bound Linux metadata sample retains 48 resolved package nodes despite\n52 lock entries, due to unused platform alternatives. Each retained lock entry\nstill must match the product pin and checksum, including those not compiled.\nThe 30 source controls (19 pre-existing plus 11 focused) explicitly test this distinction and reject extra\nforeign dependencies. These checks do not prove Linux runtime safety.\n