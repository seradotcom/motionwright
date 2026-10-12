# Render Window Cache: Physical Eviction (v0.5 / MW05-E03-03)

**Status: partial, not native incremental rendering acceptance.** This work follows the read-only render_tail_impact preflight on PR #147. It does not claim full v0.5 completion or modify the acceptance ledger to PASS.

## What is implemented

- Application-owned render-window-proxy artifacts can be admitted to the existing SHA-256 derived cache. Each registered entry has an exact project identity, generation, revision, deliverable, source frame window and caller-authored dependency digest; the content blob is ingested and verified by the existing CAS.
- SQLite stores the registration. Lookups fail closed for a missing registration, stale revision/generation, unknown deliverable, an unselected window or altered blob. A database trigger atomically revokes old registrations on a committed revision change and stages recoverable deletion tombstones; a bounded post-commit sweep physically removes manifests without altering user edits if eviction fails.
- A storage/service method consumes a fresh canonical domain impact request, re-derives exact source-frame windows, and physically removes overlapping derived manifests and registration rows. It is idempotent. It does not touch project revisions, source files, historical receipts or CAS blobs.
- All entries from a different revision are evicted conservatively. Scoped montage profiles or tail spill past film duration invalidate their whole profile rather than using unproven output-frame remapping.
- Explicit bounds: at most 4,096 indexed entries per project, at most 4,096 source frames per derived window, and 1,080,000 source frames for the project. An overfull index stops without partial eviction.
- Partial eviction caused by a filesystem or database error results in a cache miss, not a stale hit: lookup requires both an active SQL registration and its verified manifest. A stray manifest is not authoritative. The cleanup may be explicitly retried with `drain_render_window_evictions(1..=256)` after crashes or transient failures; oversized or symlinked entries fail closed and never suppress the successful project commit.

## Why this is not a native incremental renderer

**There is no native renderer producer attached to this new artifact class yet.** A trusted application producer must still validate the renderer build, source geometry, audio mixing, transition state, color pipeline, initial conditions, masks/fonts and frame provenance before such artifacts can be used to accelerate production.

The cache API accepts an owner-supplied dependency digest; it is not itself cryptographic proof that inputs from different revisions are interchangeable. Current lookups are exact-revision only. The report retains native_frames_regenerated_or_avoided: null, native_renderer_saved_wallclock_micros: null, and native_render_equivalence_verified: false.

The independent HTML/Chromium visual-only dirty transfer oracle from PR #83 has stronger image-transfer evidence, but renders both versions in full and does not prove renderer speedup. This change does not impersonate that proof.

## Security / negative cases

Dedicated Rust integration tests cover selective physical removal, clean-window retention, intact immutable blob bytes, idempotence, stale revision/generation rejection, bad windows, nonselected montage sources, global mix, conservative old-revision eviction, atomic post-commit revocation, aborted edits, crash recovery, corrupt derived metadata and symlink adversaries. The explicitly labelled binary proxy reconstruction test compares rebuilt chunk bytes against cold full concatenation; it is not decoded native PNG/FFV1 equivalence.

The new GitHub Actions workflow runs those tests, cache integrity tests and Rustfmt/Clippy on hosted runners. No heavy renderer/build is required on a personal device.

## Next gates

1. Admit source-pinned native PNG windows after an actual Semwright runtime receipt; reject fabricated output-frame provenance.
2. Bind RenderWindowCacheKey digest to an audited native renderer recipe and exact execution options. Keep audio and global visual dependencies conservative.
3. Independently render cold and incremental masters and compare each decoded frame and audio sample, including camera, masks, transitions, audio tail and mixed frame rates.
4. Only after that may cache hits save native render work and report measured elapsed-time savings. Run human creative acceptance separately.
