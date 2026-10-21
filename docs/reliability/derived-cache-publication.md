# Derived cache publication and repair

A cache key binds scope, kind and input fingerprint to one verified output digest
and size. The manifest keeps that binding when its content blob is evicted.
A cache miss signals unavailable content; it does not authorize different bytes
under the retained key.

## Publication contract

The store reads and validates a retained manifest before publishing new CAS
content. It uses the existing staged import to capture, hash and inspect the
source once. A known digest or size conflict is rejected before CAS publication.
The caller's source files and existing manifest remain untouched.

When the bytes match, the store can repair a missing blob and retains the
original manifest record, including its creation time and work-unit count.
Repair uses the same exclusive publication as a new entry. If the manifest
disappeared, its original record can be republished. A different or invalid
winner is preserved and reported as an error.

Manifest promotion uses a hard link from a fully written, synced private staging
file. It never deletes the destination or falls back to replacement rename.
A concurrent equivalent winner returns the complete winner record. A concurrent
different output or unrelated regular destination is an error. The store cleans
only the staging name created by that attempt.

## Regression boundaries

The service tests use real SQLite/CAS stores and caller-owned fixture files:

- Missing blob with a different attempted output preserves all retained files
  and rejects the attempt without publishing its CAS bytes.
- Exact repair preserves metadata and supports reopening the service.
- Known conflicts and malformed manifests reject before CAS publication.
- Different scopes and kinds remain independent.

Three storage tests prepare a contender before another real Store publication.
They then exercise the product's manifest promotion function against a different
winner, an equivalent winner with different metadata, and an unrelated regular
destination. This fixes the interleaving deterministically without timing loops
or a production test switch.

The regression source extracts only the new-entry promotion into a private
function while retaining its replacement-rename behavior. It leaves the old
existing-entry branch unchanged. The same test definitions must run against the
correction. Source inventory is not a test result; admission requires exact SHAs,
original output and named outcomes.

## Scope and limits

This contributes to immutable-source and cache-identity requirements by
preserving the meaning of an admitted key. It does not prove real renderer reuse,
decoded output equivalence, actor authorization or avoided render work.

A collision discovered only at manifest promotion may leave a valid unreferenced
CAS blob: both producers could publish their content before the winner became
known. Removing that blob would require independent proof that no other resource
uses it. No such deletion is attempted. Filesystems without hard-link support
return their error without a replacement fallback.

Syncing the staging file and exclusive publication do not prove power-loss
durability or atomic directory confinement. A concurrent eviction can make a
later lookup miss; it cannot give a retained fingerprint permission to silently
name different bytes. Staging cleanup is best effort after a known publication
result; no successful publication is retried because cleanup failed.
