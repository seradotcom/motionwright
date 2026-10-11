# Direct-versus-mediated native output parity — narrow CI experiment

**Scope:** exactly one source-owned 60-frame / 1920×1080 Motion Canvas Film built from Motionwright's revisioned project. The Film contains native text, a linear X/Y settle, a source-authored 0→1 linear opacity fade and static geometry. This is a concrete subset of v0.5 **MW05-E01-02** and not general cross-editor support.

## Two distinct executions, one Semwright authority

The **mediated path** is the ordinary Motionwright `StudioService → ProductionCoordinator → pinned Semwright Native SDK → Broker/Driver Host → Motion Canvas` pipeline. The **direct comparison** is a separate, explicitly requested CI-only `semwright --json execute` call sequence to the *same exact-SHA Broker and Driver Host*. It uses the same canonical Film exported read-only from the saved project, including exact frame timing and authoring semantics. The direct leg has an **independent acknowledged render job and own native artifact**. This does not circumvent the SDK in the product, open a local shell/renderer to agents, or create a competing render backend.

The direct leg uses `composition.plan`, `composition.apply`, `render.start`, `render.status`, `render.result`, `composition.verify` in order. Provider generation and descriptor provenance must be canonical. Any missing/foreign job reference, unobservable or timed-out status, missing terminal result or incomplete native verification fails closed. **No new render.start is dispatched to recover a previously uncertain job.** The second render is an intentionally different comparison, not a retry of the first.

## What is compared

For each independently produced 60-frame artifact, the CI verifies native manifests, exact frame indexes, path isolation, actual PNG headers, bounded file lengths and individual SHA-256 bytes. Both sets of 60 SHA-256 hashes must match **index by index**. Swapping two frames, forging a manifest or tampering with any PNG must fail. No editorial CSS, synthetic screenshot, FFmpeg render alternative or manually prepared image qualifies.

The machine-readable evidence includes the exact source hash, app SHA, pinned Semwright SHA, project generation/revision, direct native validation check count, independent-job attestation and ordered frame digest. Tests intentionally reject forged results and ensure that read failure cannot create another native job.

**Claims expressly excluded:** native execution parity for other projects/renderer versions, rich masks, nonlinear opacity curves, cross-application source import, audio quality, measured usability, human visual approval and production readiness of v0.1.0 or v0.5. A failure in this CI lane requires investigation; it does not justify replacing the source, disabling checks or asserting approximate visual equality.

Heavy runs are GitHub Actions only at `.github/workflows/native-render-e2e.yml`. Running the lightweight Python conformance tests locally does **not** produce any real native parity claim.
