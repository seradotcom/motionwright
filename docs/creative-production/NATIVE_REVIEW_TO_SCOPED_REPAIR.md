# Native frame review → scoped repair draft

This is a **bounded, user-directed** bridge between two existing Production
tools. It does not generate corrected media, approve a render, or act as a
native authorization endpoint.

## Operator journey

1. Produce a current Semwright Native SDK Motion Canvas frame grant in Deliver.
2. Production → Native inspection → Inspect current native frames. Select one
   sampled source frame and an actual object in the current scene. Record
   the visible constraint, observation, and proposed localized repair.
3. **Draft scoped repair** changes only the UI tab. It carries the project
   generation/revision, deliverable profile, source frame index, PNG SHA-256,
   selected object ID, and a clearly labeled *manual* review rationale.
   The durable rationale also records source generation/revision/profile, exact
   rational frame time, severity and confidence, without disclosing the grant.
4. Production → Scoped changes selects that object and pre-fills the rationale,
   **not** replacement text or geometry. The selected target remains fixed
   for this source-anchored draft; discard the draft to select a different
   object. A mismatched output profile blocks the draft; the operator can
   reselect the original unchanged profile or start a fresh native inspection.
   The operator must modify the value,
   queue the edit, preview the bounded editorial A/B diff, and explicitly
   commit a fresh project revision.
5. All existing CAS, locks, object-scope checks, and independent native
   verification are preserved. Moving to another scene, changing project
   revision, or dismissing the proposal invalidates/clears the draft.

The button for recording a permanent review is separate from drafting a
repair. Recording a review is a document mutation; it invalidates old
source-frame grants. Drafting a repair does not create an approval, native
job, file, project event or edit.

## Source boundaries and limitations

The SHA-256 belongs to the bytes sampled through a current native frame
readback token, not to an external origin attestation. The token itself,
broker capabilities, local paths and raw provider receipt stay outside the
draft and patch rationale. The operator's proposed repair remains a
human-authored interpretation and never becomes an automatic quality PASS.

Only an existing node in the selected current scene may be targeted. Text
nodes default to text edits; other nodes default to transform edits. No
automatic text rewriting or geometry inference is admitted. Stale project
generation/revision/scene changes block the transfer or the subsequent
preview/commit; the user must re-inspect a fresh source.

A native review can identify a problem but cannot guarantee that the
replacement fixes pixels, layout, color, rights, audio or delivery. Real
render-and-review acceptance remains separate. The existing 60 independent
product acceptance scenarios remain NOT_RUN until an external reviewer
actually evaluates them.

## Tests

Pure tests cover exact source identity and object binding, missing or wrong
sources, missing digest, stale revision and generation, empty findings, and
no document mutation. A labeled *synthetic Tauri transport* browser test
covers the actual workspace handoff, source context, human-required value
change, and absence of mutation or preview dispatch. Source-pinned native
E2E suites continue to establish real source-frame behavior separately.
