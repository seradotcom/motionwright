# Motionwright v0.5 — Delivery variant change-impact comparison

**Scope:** read-only, source-bound comparison of two *authored delivery
profiles*, not their rendered media. Built into **Deliver → Variant change
impact** immediately beside the existing Narrative Cut Continuity review.

## What the UI actually does

Two independently expandable source comparisons are available:

1. **Current draft vs saved profile.** Shows exact declared edits that have
   not been committed. Expanding and reading the comparison is read-only:
   it does not save the profile, mutate a revision or request a Broker
   capability. The existing Save profile action remains the only UI path.
2. **Variant vs parent profile.** When a profile has a recorded parent, the
   inspector also compares against that specific saved parent. Derivation
   does not erase or overwrite the parent. The report names the current
   project revision/generation and profile IDs; it warns for a missing,
   mismatched, cyclic or old-revision lineage.

The profile change ledger covers: dimensions, exact rational frame rate,
scene inclusion/order and protected scenes, language and localized text
override identities, narration/voice selection, captions and burn-in,
timing locks, native framing strategy and crop approval, brand, codec,
sample rate, color space and mux container, profile lineage, labels and
adaptation notes. It records the *fact* of modified copy without logging
or serializing the full localized text. Changed scene identities can be
recognized from bounded scene-name excerpts. A report never contains
external owner tokens, raw connection JSON or assets.

Potential downstream review stages are named in the context of the
changed fields:

| Stage | Typical changes that may merit review |
|---|---|
| Picture / native layout | Aspect, scene selection, crop/replan, branded text |
| Narration / mix | Voice selection, source cut and frame clock |
| Caption file / burn-in | Voice/caption selection, cut timeline, burn-in |
| AV mastering | Container, codec, sample rate, frame rate, picture/audio updates |
| Editorial / metadata | Profile name, lineage, protected scenes, cut label, notes |

These are **conservative editorial possibilities**. They are NOT the
physical derived-cache invalidation oracle, an exact renderer dirty-tail
contract, automatic multi-scene recomposition, a hash-verified source
dependency computation or performance benchmarks. Another agent may be
implementing those layers separately. There is no change to the pinned
Semwright Native SDK or any Broker/Driver Host dispatch path.

## Failure and evidence semantics

- Unchanged fields mean no difference among *compared authored profile
  fields*, **not** unchanged media inputs: fonts, source assets, runtime
  generation, renderer revisions and linked third-party projects can all
  vary independently.
- A derived source revision older than the inspected project requires
  review. This does not delete or reconstruct the derivative; it is a
  report of historical provenance, not false automatic staleness proof.
- Missing/altered profile identity, orphaned text references, missing
  parent, cyclic parent, future revision, unbounded source size and
  malformed rational frame rates fail closed to an unresolved/not
  assessed state. Baseline absence is explicit rather than treated as
  a zero-change success.
- Scene selection compares resolved implicit-all with explicit-all as
  equivalent, but reports changed members/order even if the number of
  included scenes is unchanged. The other existing cut continuity
  reviewer retains the authoritative semantic checks for scene order,
  protected claims, localization and transcript cut boundaries.
- Rational frame rates are compared without floating-point rounding:
  60000/2000 and 30/1 are semantically identical. The UI never infers a
  target frame-count or pixel match from equal rates.
- This UI does not infer a successful render or the legitimacy of rights,
  user consent, evidence authenticity, accessibility, viewer impact,
  playable audio, color quality, signing or creative approval. Native
  readback and independent product acceptance remain separate.

## Evidence, validation and constraints

The ledger is deterministic, contains no network operations and does
not create, overwrite, delete, install or upload files. Source budgets
are enforced: 128 profiles, 512 scenes, 2,048 semantic nodes, 512 text
overrides per profile and 256 adaptation notes. Reports stay in local
Studio memory and are not written as public CI artifacts.

The unit suite covers baseline semantics, source identity, bounded
lineage/cycles, content preservation, no project mutations, frame
rate equivalence, stage-specific review classification and adversarial
inputs. Playwright checks the actual Deliver navigation, live unsaved
cut changes and derivative parent comparisons. Heavy CI continues
verifying real Semwright Native SDK rendering and existing GUI/native
contracts at the PR commit. No new independent human PASS is created.

**Tracked requirement:** MW05-E14-02 stays **partial**. The 60 original
independent product acceptance cases remain NOT_RUN; a clean comparison
alone does not make the v0.5 product release-ready.
