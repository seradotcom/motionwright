# Creative expansion ownership and integration

## Source boundaries

The expansion branch is `feat/v05-creative-production`. It began at Motionwright `330e27e22c7d6cba0d555d4b2fed74d9de57e758` and incorporated upstream `d848657ed78ba73a2c925c7c3eceecce8e4f9054` into the feature branch. This did not merge the feature branch into main. Work was isolated from the original working checkout and the other implementation worktrees.

Semwright remains pinned to `8fa191250ae68274182570c65f067f7a60f85625` in `SOURCE_LOCK.json`. No private Core, Broker, scheduler or native driver implementation is copied into Motionwright. New domain commands run through the existing StudioService transaction and Native SDK application handler.

Upstream receipt pagination, static camera pan and frame-bound linear XY motion retain their own history and tests. The creative entrance profile handles synchronized opacity/Y/rotation keys; the existing exact linear profile handles its admitted XY keys. These are separate explicit admissions, not permissive catch-all fallbacks. Project camera or unsupported motion still causes a diagnostic where the provider contract cannot preserve it.

## Owned implementation surfaces

The new modules are `domain/hero.rs`, `domain/production_design.rs`, `domain/creative_revisions.rs`, `native/expressive.rs`, `native/component_text.rs`, and the Production/inspection/undo Studio modules. Integration edits in `domain/lib.rs`, `domain/history.rs`, `native/film.rs`, `native/lib.rs`, Studio `api.ts`, `types.ts` and `App.tsx` connect them to existing contracts.

Project format 2 is necessary because an older format-1 writer ignores unknown creative fields before reserialization. The existing old-format version check rejects format 2 rather than silently dropping its content. New code reads genuine format-1 documents and upgrades them only inside the first successful write. Preserve this boundary when resolving future integration conflicts.

`serde_json/float_roundtrip` is intentional. The component baseline and live geometry are compared exactly after database reopen. Default float decoding changed some values by one representable float and broke baseline fidelity. Do not replace the exact test with tolerance to hide persistence drift.

## Public native additions

The branch adds six operations to the preexisting application catalog:

- `driver.motionwright.product-hero.upsert` and `.product-hero.detach`.
- `driver.motionwright.production-plan.set` and `.native-capsule.upsert`.
- `driver.motionwright.creative.patch.apply` and `.creative.patch.undo`.

The corresponding `production-plan`, `components`, `native-capsules` and `creative-patches` observations are bounded, revision-bound pages. Existing project refs, caller authority, effect grants and idempotency remain authoritative. Undo is explicit domain behavior; the generic Native SDK automatic-undo contract is not falsely advertised as implemented.

No imported capsule is executable. No model credentials are read or transmitted. No third-party account, paid generation batch, plugin installation or publication is performed by these operations.

## Validation ownership

Fast formatting/source checks run without local compilation. Rust/TypeScript builds, browser acceptance, portable bundles and native rendering run in remote CI. `product-hero-e2e.yml` is the selected heavy proof for this component. Its exact source pin, actual provider jobs and copied frame/master digests are essential; an editorial SVG or DOM screenshot is not a substitute.

The original acceptance ledger is not rewritten or promoted by this expansion. The separate 64-ID status file records an implemented slice and open scope. Keep automated preservation tests distinct from ten real human revisions and keep an internal graphic study distinct from observed software evidence.

## Integration follow-through

Resolve shared-file conflicts by preserving both the bounded creative grammar and upstream exact-linear grammar, all new command variants, the document-version guard, branch-state capture and per-generation receipt pagination. Rerun the complete core/Studio suite and the selected native component lane for the combined SHA. An older successful native run does not certify a changed projection or runtime pin.

Do not publish a release or install an experimental renderer solely because this branch compiles. HyperFrames/fframes admission, third-party project editing, sound production, remaining components and user/competitive acceptance need their own concrete implementations and evidence.
