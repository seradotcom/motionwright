# Source-bound multimodal review in the existing Studio

## Scope and ownership

This v0.5 increment started from Motionwright `dabbc671f0e910bce713b5140a23a53cc5b7f2ab` in a separate worktree. It advances bounded portions of MW05-E09-01, MW05-E09-02 and MW05-E09-03. It does not close complete requirement acceptance or the original independent acceptance ledger.

The existing Native SDK consumer, Tauri readback, waveform proxy, project graph and `AddReview` transaction remain authoritative. There is no additional Core, renderer, scheduler, filesystem reader, Python runtime, paid model or parallel Studio. `SOURCE_LOCK.json` is unchanged. The earlier offline Python reviewer is not imported as a second backend or declared integrated wholesale.

## Native frame inspection

Open **Production → Native inspection** after producing a current native preview for the selected scene/output profile. The original sampled contact sheet and manual finding/scoped-repair path remain available.

Choose **Consecutive in-scene window** and a starting native frame to inspect up to eight consecutive frames. The window is bounded by the current scene's first/last native indices under the existing read token. It never requests another renderer job, creates a read grant or crosses the scene boundary. Window times use exact rational offsets from the existing inspection plan.

The request identity includes project, generation, revision, scene, output profile, dimensions, rate and every selected frame/token. Batches are immediately hidden when that identity changes. Sequential reads check the current identity after awaited operations; an old response cannot publish thumbnails into the new selection. Runtime read tokens remain private component state and are excluded from recorded review bodies.

The source PNG is bounded to 16 MiB and 16,777,216 source pixels. Its header and declared dimensions are checked before browser decoding. Both thumbnail dimensions are at most 384 pixels. The source PNG SHA-256 is retained, but analysis uses **downsampled, color-managed browser RGBA**, not original full-resolution pixels.

The analysis reports pairwise thumbnail difference counts and bounding boxes. Temporal interpretation requires consecutive indices: missing-frame gaps are reported and excluded. An isolated A-B-A thumbnail change creates an uncertain candidate, not a claim that a designed flash is defective. Unchanged runs of at least three adjacent thumbnails are only candidates when the user explicitly checks **I expect continuous motion in the inspected interval**. That expectation is scoped to the current read batch.

A thumbnail hold can hide subpixel detail or intentional stillness. Zero thumbnail difference does not prove pixel equality, semantic preservation or creative quality. Scene-internal edits are not inferred as authored cuts; a candidate can therefore be intentional and must be reviewed.

## Measured audio page review

Open **Audio** and select an existing measured take. This reuses the existing immutable-source waveform-page request; it does not decode or import another audio file. The returned page must match the selected track's digest, rate, channels, measured duration and requested page, including exact sample extent, bin count, page length and coverage flags.

The view retains only a page whose request key matches the current project/generation/revision, track metadata and pagination. Older responses are ignored when a take or source changes. Source changes do not relabel existing bins as new evidence.

The current Audio workspace requests 192 bins at a time. The review checks near-full-scale proxy bins and, only under the explicit **I expect audible content throughout this waveform page** checkbox, quiet spans of at least 150 ms. The thresholds are inspection heuristics: 0.999 near full scale and 0.001 quiet normalized proxy peak. The proxy is the existing `sample-peak-max-abs-v1` quantized max-absolute data. It does not provide RMS, channel-specific peaks, exact clipping positions, reconstructed true peak, integrated loudness or intelligibility.

Candidate boundaries are exact rational **peak-bin intervals relative to the voice take**. They are not individual clipping/silence sample positions. A run touching the page edge is marked as potentially continuing outside the observed page. At most 16 candidate details are shown; omitted details are counted rather than converted into an all-clear conclusion.

No mapping from voice-take time to project-timeline time is invented. Recorded audio reviews use a project resource with null timeline start/end, while preserving the exact take-relative interval and track/source references inside the body. Existing mix gain, cues, narration, captions and measured LUFS/true-peak fields are unchanged.

## Explicit review transactions, not automatic repairs

**Inspect candidate frames** selects the relevant pair without writing. **Record analytical review candidate** or **Record waveform review candidate** recomputes the current bounded candidate and sends an ordinary `add_review` through the existing project-edit grant and CAS service.

The review body distinguishes automated observations from the original manual finding format. It records method/version, scope, units, thresholds, source revision/generation, exact intervals and source digest references. It retains uncertain interpretation, `creative_approval: not_assessed`, `effects_verdict: not_issued`, no claimed observer admission and `repair_applied: false`.

A successful comment transaction is not validation of its proposed repair. Recording changes the project revision and invalidates the current frame/read scope. Actual content changes still require the existing manually authored scoped-proposal workflow, current revision, human locks and authority. Neither sample inspection nor recording installs or admits an observer as an independent verifier.

The body is bounded by the existing 8,000-byte review limit. Source grants, Blob URLs, full pixel arrays, local paths and credentials are not serialized into the added candidate schemas.

## Validation lanes and limits

The pure modules have unit tests for source mismatches, incomplete/reordered samples, temporal gaps, exact rational times, explicit expectations, corrupt proxy values, missing bins, page boundaries, stale revisions and nonmutating review construction. `multimodal-review.spec.ts` exercises the actual Studio panels with synthetic Tauri transport, including delayed responses, opt-in expectations and existing review transactions.

Browser fixtures do **not** establish native execution, provider identity, independent evidence admission or human approval. Native renderer workflows and installed-product review retain their own acceptance boundaries. Cross-revision full-resolution A/B repair validation, all-provider aggregation, semantic critique and human evaluation remain open.

Lightweight local checks can use an already installed TypeScript compiler without dependency installation:

```text
node tooling/multimodal_review_smoke.cjs <absolute-path-to-typescript-library.js>
```

This runs small synthetic pure-module checks and syntax checks, retaining source hashes. It does not run the full project compiler, Vitest, browser, renderer or desktop build. Full project `npm test`, `npm run build` and browser acceptance remain in the repository's existing GitHub Actions lanes. A written test is not counted as a successful run; inspect results for the exact PR commit before assigning a validation status.
