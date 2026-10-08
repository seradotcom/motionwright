# Derived cache integrity

Motionwright's derived cache is an application-owned reuse layer. It is not a second Project Graph implementation and it never promotes a cached artifact to CURRENT by itself. Freshness and project impact remain governed by canonical Semwright evidence; the cache only answers whether exact derived bytes are safely reusable for an already computed dependency fingerprint.

## Cache identity

Every entry is keyed by an owner scope, a bounded artifact kind such as `preview-index`, and a 64-character dependency fingerprint. The owner scope is hashed before it becomes part of a filesystem path. Equal fingerprints in different projects or artifact classes therefore do not alias. Runtime callers are expected to include project generation in the scope.

Cached payloads use the existing content-addressed blob store. The manifest records the verified blob SHA-256, byte count, dependency fingerprint, scope hash, artifact kind, declared regeneration work units, format version and creation time.

## Admission and lookup

Admission ingests the source through the verified blob store before publishing a cache manifest. If the same scope, kind and fingerprint already resolve to valid bytes, a later write must resolve to the same blob. Different bytes for the same exact fingerprint are rejected as nondeterministic output rather than silently replacing the prior result.

Lookup validates scope, kind and fingerprint syntax; derives the manifest path without user-controlled path components; rejects symlink or special manifests and oversized manifests; requires manifest identity to match the requested key; and verifies the referenced content-addressed blob plus byte length. A missing manifest is a miss. A manifest whose blob is absent is also a miss and can be rebuilt. A digest mismatch, malformed manifest or identity mismatch is an error, not a hit.

## Invalidation

The cache does not guess that two revisions are equivalent. Callers use dependency fingerprints that include inputs relevant to the derived artifact. The performance evidence lane currently exercises the delivery master dependency fingerprint and proves that a changed deliverable render input produces a different fingerprint and therefore cannot hit the old entry.

This is intentionally conservative. Finer renderer-specific reuse requires evidence for localized independence, continuity state, fonts, renderer build, color pipeline and other relevant inputs.

## Measured evidence

The Performance Acceptance Evidence workflow runs three unrelated briefs at S/M/L sizes. For every dataset it executes the ten predefined persisted edits, reopens the real SQLite project, derives a preview index from the reopened project and delivery fingerprint, admits that payload to the cache, performs a second exact lookup that re-hashes the blob, records bytes validated plus lookup cost and semantic work units avoided, then changes a delivery render input and asserts that the old cache does not match.

The report counts exact preview-index regeneration units avoided. It does not claim renderer frames, render jobs, GPU time or wall-clock render savings. Those require a renderer-level cold/warm experiment with identical quality and remain a separate product-acceptance concern.

## Recovery

Derived cache content is disposable. Cache eviction must not alter project source, delivery outputs or protected history. Missing cache bytes cause a miss and rebuild; they never convert historical metadata into CURRENT output. Cache presence is never used as an authorization signal.
