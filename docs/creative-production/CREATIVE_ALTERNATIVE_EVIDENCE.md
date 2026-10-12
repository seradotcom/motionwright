# MW05-E05-04 — evidence-first creative alternative studies

This slice extends the existing Review workspace with a real comparison
of two persisted, editable creative branches. It does not generate
invented variants, choose a preferred design or automatically distill
a new reusable component.

## Source comparison

The editor selects a saved alternative with a real branch ID,
branch-workspace current state and source base/head revisions.
The read-only comparison reports:

- added/omitted scenes and editable Canvas objects by stable UUID;
- differences in authored scene objective/title, exact rational timing,
  renderer/status, scene-local beats and camera pose;
- differences in object copy/type, geometry, style/typography, keyframes,
  parent/relations, z order and per-property locks;
- changes in creative brief, narrative, audio intentions, visual
  language, delivery profiles and the saved production plan.

The comparator rejects missing branch snapshots, mismatched base
revision metadata, future revisions, duplicate scene/node identities
and oversized comparison inputs. It never mutates the saved branch.
The comparison operates on actual project state, not generated
screenshots or fake positive/negative ratings.

## Human editorial judgement

Only an explicit editor action creates a positive or negative study.
The operator must provide at least fifteen characters of a rationale.
Clicking Record manual observation creates a normal, branch-scoped
creative review through the existing add_review change and
canonical StudioService compare-and-swap transaction.

The stored review contains the exact project ID, generation and
revision, active/candidate branch IDs, candidate base/head revision,
editable source difference categories/counts and human rationale.
The new review is OPEN, not automatically RESOLVED. Earlier reviews
retain their original branch and revision anchors. Neither branch
merge nor media approval occurs during the review.

## What it cannot establish

Matching source objects do not guarantee identical native pixels
if fonts, assets or external providers differ. A positive study
expresses a human-authored opinion, not measured quality improvement
or a new admitted reusable component. Rights, asset truthfulness,
accessibility and creative acceptance require independent evidence.

The approved merge process remains the existing semantic three-way
branch CAS with conflict validation. Actual native renders continue
through the pinned Semwright Native SDK, without new renderer,
grants or network operations.

## Validation and future scope

Unit tests inspect saved branches, scene/object edits, exact timing,
no-change comparisons and adversarial identity tampering. Chromium
tests inspect alternatives without changing the project revision
and record a negative study as an actual anchored review.

MW05-E05-04 remains PARTIAL. Full component distillation still
requires explicitly approved positive/negative studies from real
editors, source extraction, lineage/versioning, cross-renderer
conformance and independent acceptance of the resulting native
media. None of the original 60 acceptance cases is promoted.
