# Session-scoped native render receipts in Program

Motionwright's Deliver workspace can complete a canonical Motion Canvas render through Semwright's `render_motion_canvas` operation and return a typed `MotionCanvasRenderEvidence` record. This pass lifts only that existing response to the parent editor session, so switching back to Timeline shows which scene belongs to the exact output profile and revision. It does not rebuild or duplicate the native runtime.

## Strict association

- The record must name the exact open project resource, generation and saved delivery profile.
- Its segment must contain the selected scene ID, a non-empty job reference and positive finite frame count.
- If the project revision moved since native completion, the receipt is labeled **STALE** and retains the original revision.
- If the editorial display profile differs from the output profile, the UI identifies the difference instead of implying that another aspect ratio or timebase was rendered.
- Scenes not included in returned segments, including other renderers, continue to show **No render evidence attached**.

The Program stage remains labeled **Design representation**. Its footer displays native **receipt metadata**, not decoded native frames or a video player. A Motion Canvas receipt does not certify a complete MLT/AAC master, creative quality, Graph admission or Effect Conformance PASS. The **Inspect receipt in Deliver** action opens the existing Deliver workspace and retains the same in-session record.

The receipt is ephemeral for this application session. Reloading does not infer it from local paths or browser storage. An imported project starts a fresh generation with no current receipt. Native media playback requires separate readback-safe immutable artifact capability and testing.

## Evidence

- `renderReceipt.test.ts`: exact scene/profile/generation/revision matching, stale/other-profile flags and fail-closed malformed associations.
- `render-receipt.spec.ts`: clearly synthetic Tauri bridge for success, scene switching, profile selection, inspection, post-edit invalidation. The mock artifact is explicitly synthetic and not native render proof.
- Heavy GitHub Actions validates the exact submitted SHA. Independent product acceptance remains separate.
