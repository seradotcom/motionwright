# Motionwright v0.5 — bounded evaluator experiment before admission

**Requirement:** MW05-E13-02 — **partial, data-only** creative evaluator experiments.
This builds on the existing MW05-E13-01 [versioned evaluator](CREATIVE_EVALUATORS.md).
It is not a new evaluator backend, a downloadable-code sandbox or a trusted
skill/plugin installation mechanism.

## Owner workflow

Production → Evaluators → **Experiment before admission** starts from
one **actually registered, immutable evaluator version**. The user drafts
the next version as typed `motionwright.creative-evaluator/1` JSON.
The existing Rust/TypeScript evaluator schemas remain authoritative;
no code, script, shader, provider or model instruction is installed or run.

A disposable, in-memory comparison operates on one to three distinct
already-authored scenes, at the exact project ID, generation, revision,
selected source evaluator ID/version and SHA-256 digest.

Both baseline and candidate observations use the **existing** pure,
hardcoded first-party criterion interpreter:
`minimum_nodes`, `text_contains`, `project_pixel_safe_area` and
`human_question`. Results show pass/fail/abstain counts per scene,
criterion-ID regressions/recoveries and added/removed criterion IDs.
A missing criterion is not automatically treated as an improvement,
nor are unchanged technical outcomes proof of improved aesthetics.
The candidate must retain a human question that always **abstains**.

The candidate must be a new version of the *latest* source evaluator,
with the same identity and an actual change to typed content or editorial
guidance beyond incrementing its version. The experiment changes no
`Project`, file, asset, provider or Native SDK grant.

After reading results, the owner provides a local-only reason and two
explicit acknowledgments: source rights and the limitations of the
technical/human comparison. In desktop mode, a separate confirmation
calls the **existing** `register_creative_evaluator` operation through
the canonical Studio/Tauri/`StudioService` optimistic CAS path. Before
that call, the experiment is re-run and its baseline/candidate digests,
observations and source revision are checked for a match; stale project
changes fail closed. The browser demo can inspect data but cannot claim
canonical desktop admission.

## Security, permanence and evidence

- The report is deliberately **disposable**: no arbitrary code,
  imported media or new project state is created during comparison.
- The registered candidate bundle is the only persisted result, by
  the existing append-only evaluator-version CAS. The local experiment
  report and rationale are not persisted or signed and are **not**
  represented as independent owner authorization evidence.
- A text `author_attestation` is author-declared, not authenticated.
  There is no external developer identity, signed package permission,
  automatic creative approval, or new effect grant.
- Negative tests cover unknown executable JSON fields, foreign
  source evaluator IDs, version skips, source-revision change,
  changed observations, unsafe content locks, oversized payloads,
  duplicate/unknown scene IDs, missing human abstention and
  cosmetic version-only changes.
- Dedicated GitHub CI runs exact-source Vitest, production TypeScript
  build and real Chromium UI acceptance with a clearly synthetic Tauri
  transport. No third-party media or proprietary prompts are published.

## Not implemented

The broader E13-02 promise of **arbitrary dynamic code promotion**,
a disposable *executable* sandbox, independent package signing,
trustworthy publisher identities, policy-enforced skill capabilities,
resource accounting, provider isolation, native host execution,
automatic quality ranking and independent reviewer acceptance is
**NOT IMPLEMENTED**. The 60 private human creative acceptance
scenarios remain NOT_RUN and `release=false`.

This is one bounded original-data **comparison and subsequent
owner-declared version registration**, not evidence that a skill
may safely run in a real user project.
