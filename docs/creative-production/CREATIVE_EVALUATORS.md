# MW05-E13-01 · Versioned creative playbooks and technical evaluators

Status: **partial v0.5 implementation**. This slice adds bounded reusable
creative criteria; it does **not** authorize execution of downloaded skills,
invoke an external model or establish artistic quality automatically.

## Purpose and authority

Production → Evaluators lets a project owner register an original, reusable
data-only playbook. Each bundle has a schema, stable ID, strictly increasing
version, owner-rights declaration, brief editorial guidance and 1–16 typed
criteria. The bundle's exact content SHA-256 is derived from a canonical,
domain-separated JSON representation.

The only executable behavior is the **hardcoded first-party evaluator
interpreter** in the existing Rust domain and Studio TypeScript mirror.
Playbook prose is displayed literally; it cannot grant capabilities, install
code, run arbitrary JavaScript, access a filesystem, contact providers or
override the Semwright Native SDK project authority. Arbitrary extra fields
are rejected.

The existing source CAS supports:

- creative-evaluator.register: append exactly the next immutable version,
  preserving all prior versions and reports.
- creative-evaluator.evaluate: inspect the requested authored scene and
  persist a report linked to the exact bundle ID/version/digest, scene ID,
  project generation and revision through the normal SQLite event journal.

The initial grammar evaluates observable, deterministic properties:

1. minimum_nodes: number of Canvas objects actually authored.
2. text_contains: literal phrase within a native editable text object.
3. project_pixel_safe_area: geometry within a declared 1920×1080
   technical rectangle and explicit bounded margin.
4. human_question: always not_assessed; a person must judge continuity,
   story, originality, accessibility and creative quality.

A false geometric constraint produces a technical fail. Unsupported
coordinate spaces or missing geometry produce not_assessed, not an invented
PASS. Reports distinguish pass_technical_only, fail and needs_human_review.
The creative_approval field is permanently not_assessed for these operations.

## Source preservation and negative acceptance

Evaluator versions are append-only, 1–64 across the project. Evaluations
are bounded to 256. Previously recorded observations remain historic and
do not change when a node or source text changes; the new report states the
new project revision. An ordinary change cannot rewrite the original bundle
content digest, result identities or report conclusion.

Rust and TypeScript independently interpret the same typed grammar and use
the same domain-separated digest. A fixed JSON fixture has a golden SHA-256
test on both platforms, detecting mismatched canonical serialization.

Rust domain tests cover version gaps/duplicates, copy changes, technical
failure, human abstention, unknown executable fields, safe-area refusal and
portable JSON restoration. SQLite tests check durable request replay,
version/scene authority and rejection of stale/missing bundle writes.
Studio unit/browser tests exercise the same refusal conditions and the
visible authoring and review workflow. Native SDK acceptance checks the
two new capability descriptors rather than bypassing the existing app.

## Boundaries of this milestone

These technical checks **cannot** tell whether a graphic looks beautiful,
whether a video is persuasive, or whether the owner has legal rights to
a third-party asset. They do not run an LLM-as-judge or benchmark a
third-party creative renderer. Native MP4 review and real human creative
acceptance remain separate from this source-bound evaluator.

A future implementation may add independently reviewed and signed recipe
packages, expressive multi-scene timing/rhythm constraints, external
evaluator providers and human approval workflows with recorded judgment
evidence. That requires additional security and comparative experiments.
MW05-E13-02, arbitrary dynamic code promotion, remains explicitly
**not implemented**, not silently permitted by these bundles.
